# Session ID Authentication Enforcement

## Current State
- **Main API (8000/8001)**: Simple API key (single shared secret)
- **Transcription WS**: Token-based with session tracking

## Proposed Enhancement: Session-Based Auth for Main API

### Architecture

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │ 1. Login with API key
       ▼
┌─────────────────────────────────┐
│   POST /_auth/login             │
│   Body: { "api_key": "..." }   │
└──────┬──────────────────────────┘
       │ 2. Validate + Create Session
       ▼
┌─────────────────────────────────┐
│   SessionManager                │
│   - session_id: uuid            │
│   - user_id: string             │
│   - created_at: timestamp       │
│   - expires_at: timestamp       │
│   - ip_address: optional        │
└──────┬──────────────────────────┘
       │ 3. Return session token
       ▼
┌─────────────────────────────────┐
│   Response:                     │
│   { "session_id": "xyz789",     │
│     "expires_in": 3600 }        │
└──────┬──────────────────────────┘
       │ 4. Use session for requests
       ▼
┌─────────────────────────────────┐
│   GET /memory/tools             │
│   Authorization: Session xyz789 │
└─────────────────────────────────┘
```

### Implementation Plan

#### 1. Session Manager Service
```python
# src/mcpo/services/session_manager.py

class SessionManager:
    def __init__(self, state_path: str = "mcpo_sessions.json"):
        self._sessions: Dict[str, SessionRecord] = {}
        self._lock = threading.RLock()
    
    def create_session(
        self,
        user_id: str,
        ttl_seconds: int = 3600,
        metadata: Optional[Dict] = None
    ) -> str:
        """Create new session and return session_id"""
        session_id = secrets.token_urlsafe(32)
        record = SessionRecord(
            id=session_id,
            user_id=user_id,
            created_at=int(time.time()),
            expires_at=int(time.time()) + ttl_seconds,
            metadata=metadata or {}
        )
        with self._lock:
            self._sessions[session_id] = record
            self._cleanup_expired()
            self.save()
        return session_id
    
    def validate_session(self, session_id: str) -> Optional[SessionRecord]:
        """Validate session and extend if needed"""
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            if record["expires_at"] < int(time.time()):
                del self._sessions[session_id]
                return None
            # Optional: Extend session on activity
            record["expires_at"] = int(time.time()) + 3600
            return record
    
    def revoke_session(self, session_id: str) -> bool:
        """Explicitly revoke a session"""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                self.save()
                return True
            return False
```

#### 2. Auth Middleware Enhancement
```python
# src/mcpo/utils/auth.py

class SessionAuthMiddleware(BaseHTTPMiddleware):
    """
    Supports THREE auth methods:
    1. Authorization: Bearer <api_key>     (Admin - full access)
    2. Authorization: Session <session_id> (User - scoped access)
    3. Authorization: Basic <credentials>  (Legacy compat)
    """
    
    def __init__(self, app, api_key: str, session_manager: SessionManager):
        super().__init__(app)
        self.api_key = api_key
        self.sessions = session_manager
    
    async def dispatch(self, request: Request, call_next):
        authorization = request.headers.get("Authorization")
        
        if not authorization:
            return JSONResponse(status_code=401, content={"detail": "Auth required"})
        
        # Method 1: API Key (Admin)
        if authorization.startswith("Bearer "):
            token = authorization[7:]
            if token == self.api_key:
                request.state.auth_type = "admin"
                request.state.user_id = "admin"
                return await call_next(request)
        
        # Method 2: Session Token (User)
        elif authorization.startswith("Session "):
            session_id = authorization[8:]
            session = self.sessions.validate_session(session_id)
            if session:
                request.state.auth_type = "session"
                request.state.user_id = session["user_id"]
                request.state.session_id = session_id
                return await call_next(request)
        
        # Method 3: Basic Auth (Legacy)
        elif authorization.startswith("Basic "):
            # Existing Basic auth logic...
            pass
        
        return JSONResponse(status_code=403, content={"detail": "Invalid credentials"})
```

#### 3. Auth Endpoints
```python
# src/mcpo/api/routers/auth.py

@router.post("/_auth/login")
async def login(
    credentials: LoginRequest,
    request: Request,
    api_key: str = Depends(get_api_key)
) -> LoginResponse:
    """
    Exchange API key for session token
    """
    if credentials.api_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    session_manager: SessionManager = request.app.state.session_manager
    session_id = session_manager.create_session(
        user_id=credentials.user_id or "default",
        ttl_seconds=credentials.ttl or 3600,
        metadata={"ip": request.client.host}
    )
    
    return LoginResponse(
        session_id=session_id,
        expires_in=credentials.ttl or 3600
    )

@router.post("/_auth/logout")
async def logout(request: Request) -> dict:
    """
    Revoke current session
    """
    session_id = getattr(request.state, "session_id", None)
    if not session_id:
        raise HTTPException(status_code=400, detail="No active session")
    
    session_manager: SessionManager = request.app.state.session_manager
    session_manager.revoke_session(session_id)
    return {"ok": True}

@router.get("/_auth/validate")
async def validate(request: Request) -> dict:
    """
    Check if current session is valid
    """
    return {
        "valid": True,
        "auth_type": request.state.auth_type,
        "user_id": request.state.user_id
    }
```

### Security Considerations

#### ✅ Advantages of Session ID Auth:
- **Token Rotation**: Sessions can expire independently
- **Granular Revocation**: Revoke specific sessions without changing API key
- **Audit Trail**: Track which session performed which action
- **Scoped Permissions**: Different sessions can have different permissions
- **Multi-User**: Support multiple concurrent users with separate sessions

#### ⚠️ Risks to Mitigate:
1. **Session Hijacking**
   - Solution: Bind sessions to IP address
   - Solution: Rotate session IDs on sensitive operations
   
2. **Session Storage**
   - Current: JSON file (not thread-safe at scale)
   - Better: Redis/SQLite with proper locking
   
3. **Replay Attacks**
   - Solution: Add nonce or request signing
   
4. **Timing Attacks**
   - Solution: Use `secrets.compare_digest()` for token comparison

### Configuration

```bash
# .env additions
SESSION_TTL=3600                  # Default session lifetime (seconds)
SESSION_CLEANUP_INTERVAL=300      # Cleanup expired sessions every 5 min
SESSION_BIND_IP=true              # Bind sessions to client IP
SESSION_MAX_PER_USER=5            # Limit concurrent sessions per user
```

### Migration Path

**Phase 1: Add session support (non-breaking)**
- Implement SessionManager
- Add `/_auth/login` endpoint
- Support both API key and session auth

**Phase 2: Encourage session adoption**
- Document session auth in README
- Add UI for session management
- Log warnings for direct API key usage

**Phase 3: Deprecate direct API key** (optional)
- Make sessions mandatory
- API key only for login endpoint
- Force existing integrations to migrate

### Testing

```python
def test_session_auth_flow():
    # Login
    resp = client.post("/_auth/login", json={"api_key": "test-key"})
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    
    # Use session
    resp = client.get(
        "/memory/tools",
        headers={"Authorization": f"Session {session_id}"}
    )
    assert resp.status_code == 200
    
    # Logout
    resp = client.post(
        "/_auth/logout",
        headers={"Authorization": f"Session {session_id}"}
    )
    assert resp.status_code == 200
    
    # Verify revoked
    resp = client.get(
        "/memory/tools",
        headers={"Authorization": f"Session {session_id}"}
    )
    assert resp.status_code == 401
```

---

## Alternative: JWT-Based Sessions

Instead of random session IDs, use signed JWT tokens:

### Advantages:
- Stateless (no storage needed)
- Can include claims (permissions, expiry)
- Industry standard

### Implementation:
```python
import jwt
from datetime import datetime, timedelta

def create_session_jwt(user_id: str, secret: str) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, secret, algorithm="HS256")

def validate_session_jwt(token: str, secret: str) -> Optional[dict]:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
```

---

## Recommendation

**For OpenHubUI (current state):**

Use the **random session ID approach** for now because:
1. ✅ Simple to implement and understand
2. ✅ Easy revocation (delete from dict)
3. ✅ Consistent with existing TokenStore pattern
4. ✅ No crypto dependencies beyond `secrets`
5. ✅ Audit trail via state file

**Move to JWT later if:**
- Need stateless horizontal scaling
- Want to embed permissions in token
- Need cross-service auth (microservices)

