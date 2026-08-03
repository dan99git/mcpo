from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import asyncio
import base64
import hmac
import os


def _credentials_match(supplied: str, expected: str) -> bool:
    """Constant-time compare that never raises on non-ASCII input.

    hmac.compare_digest(str, str) raises TypeError on non-ASCII characters; a
    latin-1 bearer token otherwise crashed the middleware with a 500 that leaked
    which middleware was in play. Encoding to bytes first is both raise-safe and
    still constant-time.
    """
    try:
        return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    except (AttributeError, TypeError):
        return False

from mcpo.services.model_api_keys import (
    ModelAPIKeyStore,
    ModelAPIKeyStoreError,
    get_model_api_key_store,
)


ALGORITHM = "HS256"

bearer_security = HTTPBearer(auto_error=False)
basic_security = HTTPBasic(auto_error=False)


# Sentinel: path requires *any* active key (or the admin key), no specific scope.
ANY_SCOPE = "*"


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
    if method == "GET" and path == "/v1/whoami":
        return ANY_SCOPE
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
    scope_arg = None if required_scope == ANY_SCOPE else required_scope
    principal = _model_api_key_store(request).authenticate(
        token,
        required_scope=scope_arg,
    )
    if principal is None:
        return False
    request.state.auth_principal = principal
    return True


def _enforce_rate_limit(request: Request):
    """Apply the per-key sliding-window limit to an authenticated model-key
    request. Returns a 429 JSONResponse when over budget, else None. Admin-key
    principals are never rate limited."""
    from mcpo.services.rate_limit import get_rate_limiter

    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, dict) or principal.get("kind") != "model_api_key":
        return None
    limiter = get_rate_limiter()
    if not limiter.enabled:
        return None
    identity = str(principal.get("keyId") or "")
    allowed, remaining, retry_after = limiter.check(identity)
    if allowed:
        request.state.rate_limit_remaining = remaining
        return None
    retry_seconds = max(1, int(retry_after + 0.999))
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded for this key"},
        headers={
            "Retry-After": str(retry_seconds),
            "X-RateLimit-Limit": str(limiter.limit),
            "X-RateLimit-Remaining": "0",
        },
    )


def _record_model_key_usage(request: Request, status_code: int) -> None:
    """Count this request against the authenticated model key. In-memory only —
    never touches the key-store file (see services/usage.py for why)."""
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, dict) or principal.get("kind") != "model_api_key":
        return
    from mcpo.services.usage import get_usage_recorder

    path = str(request.scope.get("path") or request.url.path)
    get_usage_recorder().record(
        str(principal.get("keyId") or ""),
        path=path,
        status_code=status_code,
    )


def _annotate_rate_limit_headers(request: Request, response) -> None:
    """Expose the per-key budget on successful responses so clients can back off
    before they hit 429. No-op when the limiter is disabled or the principal is
    not a model key. remaining reflects the hit already recorded by the check."""
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, dict) or principal.get("kind") != "model_api_key":
        return
    from mcpo.services.rate_limit import get_rate_limiter

    limiter = get_rate_limiter()
    if not limiter.enabled:
        return
    remaining = getattr(request.state, "rate_limit_remaining", None)
    response.headers["X-RateLimit-Limit"] = str(limiter.limit)
    if isinstance(remaining, int) and remaining >= 0:
        response.headers["X-RateLimit-Remaining"] = str(remaining)


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
            # Off-load the blocking file-locked store read so a held lock in one
            # worker never freezes this worker's event loop (audit HIGH-1).
            if not self.api_key and not await asyncio.to_thread(store.enforcement_enabled):
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
            if self.api_key and _credentials_match(token, self.api_key):
                _set_admin_principal(request)
                return await call_next(request)
            try:
                authed = await asyncio.to_thread(
                    _authenticate_model_token,
                    request,
                    token,
                    required_scope=required_scope,
                )
                if authed:
                    limited = _enforce_rate_limit(request)
                    if limited is not None:
                        _record_model_key_usage(request, limited.status_code)
                        return limited
                    response = await call_next(request)
                    _annotate_rate_limit_headers(request, response)
                    _record_model_key_usage(request, response.status_code)
                    return response
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
            if _credentials_match(password, self.api_key):
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
