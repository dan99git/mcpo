from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import base64
import hmac
import os

from mcpo.services.model_api_keys import (
    ModelAPIKeyStore,
    ModelAPIKeyStoreError,
    get_model_api_key_store,
)


ALGORITHM = "HS256"

bearer_security = HTTPBearer(auto_error=False)
basic_security = HTTPBasic(auto_error=False)


def _model_api_scope(request: Request) -> str | None:
    method = request.method.upper()
    path = str(request.scope.get("path") or request.url.path)
    root_path = str(request.scope.get("root_path") or "").rstrip("/")
    if root_path and (path == root_path or path.startswith(root_path + "/")):
        path = path[len(root_path) :] or "/"
    path = path.rstrip("/") or "/"
    if method == "POST" and path in {
        "/v1/chat/completions",
        "/v1/completions",
    }:
        return "responses:write"
    if method == "GET" and path in {
        "/v1/models",
        "/v1/chat/completions/models",
        "/v1/completions/models",
    }:
        return "models:read"
    return None


def _model_api_key_store(request: Request) -> ModelAPIKeyStore:
    configured = getattr(request.app.state, "model_api_key_store", None)
    return (
        configured
        if isinstance(configured, ModelAPIKeyStore)
        else get_model_api_key_store()
    )


def _set_admin_principal(request: Request) -> None:
    request.state.auth_principal = {"kind": "admin_api_key"}


def _authenticate_model_token(
    request: Request,
    token: str,
    *,
    required_scope: str,
) -> bool:
    principal = _model_api_key_store(request).authenticate(
        token,
        required_scope=required_scope,
    )
    if principal is None:
        return False
    request.state.auth_principal = principal
    return True


def get_verify_api_key(api_key: str):
    async def verify_api_key(
        request: Request,
        authorization: HTTPAuthorizationCredentials | None = Depends(bearer_security),
        basic_authorization: HTTPBasicCredentials | None = Depends(basic_security),
    ):
        if authorization and authorization.credentials:
            token = authorization.credentials
        elif basic_authorization:
            token = basic_authorization.password
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid Authorization header",
                headers={"WWW-Authenticate": "Bearer, Basic"},
            )
        if hmac.compare_digest(token, api_key):
            _set_admin_principal(request)
            return
        required_scope = _model_api_scope(request)
        if authorization and required_scope:
            try:
                if _authenticate_model_token(
                    request,
                    token,
                    required_scope=required_scope,
                ):
                    return
            except ModelAPIKeyStoreError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                ) from exc
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return verify_api_key


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces Basic or Bearer token authentication for all requests.
    """

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        # Skip authentication for OPTIONS requests
        if request.method == "OPTIONS":
            return await call_next(request)

        # Get authorization header
        authorization = request.headers.get("Authorization")

        # Verify API key
        try:
            # Use the same function that the dependency uses
            if not authorization:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid Authorization header"},
                    headers={"WWW-Authenticate": "Bearer, Basic"},
                )

            required_scope = _model_api_scope(request)

            # Handle Bearer token auth
            if authorization.startswith("Bearer "):
                token = authorization[7:]  # Remove "Bearer " prefix
                if hmac.compare_digest(token, self.api_key):
                    _set_admin_principal(request)
                elif required_scope:
                    try:
                        if not _authenticate_model_token(
                            request,
                            token,
                            required_scope=required_scope,
                        ):
                            return JSONResponse(
                                status_code=403,
                                content={"detail": "Invalid API key"},
                            )
                    except ModelAPIKeyStoreError as exc:
                        return JSONResponse(
                            status_code=503,
                            content={"detail": str(exc)},
                        )
                else:
                    return JSONResponse(
                        status_code=403, content={"detail": "Invalid API key"}
                    )
            # Handle Basic auth
            elif authorization.startswith("Basic "):
                # Decode the base64 credentials
                credentials = authorization[6:]  # Remove "Basic " prefix
                try:
                    decoded = base64.b64decode(credentials).decode("utf-8")
                    # Basic auth format is username:password
                    username, password = decoded.split(":", 1)
                    # Any username is allowed, but password must match api_key
                    if not hmac.compare_digest(password, self.api_key):
                        return JSONResponse(
                            status_code=403, content={"detail": "Invalid credentials"}
                        )
                    _set_admin_principal(request)
                except Exception:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid Basic Authentication format"},
                        headers={"WWW-Authenticate": "Bearer, Basic"},
                    )
            else:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unsupported authorization method"},
                    headers={"WWW-Authenticate": "Bearer, Basic"},
                )

            return await call_next(request)
        except Exception as e:
            return JSONResponse(status_code=500, content={"detail": str(e)})


class ModelAPIKeyMiddleware(BaseHTTPMiddleware):
    """Protect model routes with the admin key and generated client keys."""

    def __init__(self, app, api_key: str | None = None):
        super().__init__(app)
        self.api_key = api_key or os.getenv("MCPO_API_KEY")

    async def dispatch(self, request: Request, call_next):
        required_scope = _model_api_scope(request)
        if request.method == "OPTIONS" or required_scope is None:
            return await call_next(request)

        try:
            store = _model_api_key_store(request)
            if not self.api_key and not store.enforcement_enabled():
                return await call_next(request)
        except ModelAPIKeyStoreError as exc:
            return JSONResponse(status_code=503, content={"detail": str(exc)})

        authorization = request.headers.get("Authorization")
        if not authorization:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer, Basic"},
            )

        if authorization.startswith("Bearer "):
            token = authorization[7:]
            if self.api_key and hmac.compare_digest(token, self.api_key):
                _set_admin_principal(request)
                return await call_next(request)
            try:
                if _authenticate_model_token(
                    request,
                    token,
                    required_scope=required_scope,
                ):
                    return await call_next(request)
            except ModelAPIKeyStoreError as exc:
                return JSONResponse(status_code=503, content={"detail": str(exc)})
            return JSONResponse(status_code=403, content={"detail": "Invalid API key"})

        if authorization.startswith("Basic ") and self.api_key:
            try:
                decoded = base64.b64decode(authorization[6:]).decode("utf-8")
                _, password = decoded.split(":", 1)
            except Exception:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid Basic Authentication format"},
                    headers={"WWW-Authenticate": "Bearer, Basic"},
                )
            if hmac.compare_digest(password, self.api_key):
                _set_admin_principal(request)
                return await call_next(request)
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid credentials"},
            )

        return JSONResponse(
            status_code=401,
            content={"detail": "Unsupported authorization method"},
            headers={"WWW-Authenticate": "Bearer, Basic"},
        )
