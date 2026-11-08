from typing import Dict, Any, Optional
from fastapi import Request

SESSION_COOKIE = "__session"

# The single server-side principal field that may carry a *verified* tenant
# claim. Populated only by the auth middleware from a verified IdP token; never
# from request body/query/complaint text. CRA policy storage is tenant-keyed, so
# tenancy is canonicalized on `tenant_id` only; `org_id` is intentionally not
# honored as a tenant source.
TRUSTED_TENANT_CLAIM: str = "tenant_id"


def resolve_trusted_tenant_id(session_user: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return a tenant id from the trusted server-side principal, else ``None``.

    SECURITY CONTRACT
    -----------------
    * ``session_user`` MUST originate from ``request.state.session_user``, which
      the auth middleware populates exclusively from a verified IdP token.
    * Callers MUST NEVER pass query params, JSON body fields, URL titles, or
      complaint text here — those are untrusted and must not select a tenant.
    * Only ``tenant_id`` is honored (``org_id`` is ignored). No default tenant is
      ever returned. A missing, non-string, or invalid slug claim yields ``None``
      so downstream policy stays fail-closed.
    """
    if not isinstance(session_user, dict):
        return None
    # Local import keeps this module dependency-light and avoids import cycles.
    from app.admin_config import is_valid_tenant_id

    candidate = session_user.get(TRUSTED_TENANT_CLAIM)
    return candidate if is_valid_tenant_id(candidate) else None

PUBLIC_API_PATHS = {
    "/api/auth/firebase-config",
    "/api/auth/signup",
    "/api/auth/login",
    "/api/auth/google",
    "/health",
    "/api/health",
    "/api/config",
    "/api/models",
    "/api/cra/resolve",
    "/api/cra/classify",
    "/api/customer/verify",
    "/api/database/transactions",
    "/api/database/customers",
    "/api/database/tickets",
    "/api/resolve/action",
}


def parse_bearer_token(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    parts = header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def extract_id_token(req: Request) -> Optional[str]:
    auth_header = req.headers.get("authorization")
    token = parse_bearer_token(auth_header)
    if token:
        return token
    cookie_token = req.cookies.get(SESSION_COOKIE)
    if cookie_token:
        return cookie_token
    return req.query_params.get("token")


class SessionGuard:
    def __init__(self, mode: Optional[str] = None):
        self._mode = mode.lower() if mode else None
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def register_session(self, token: str, user: Dict[str, Any]):
        if token and user:
            self._sessions[token] = user

    def revoke_session(self, token: str):
        if token in self._sessions:
            del self._sessions[token]

    @property
    def mode(self) -> str:
        if self._mode is not None:
            return self._mode
        from app.config import settings
        return getattr(settings, "auth_mode", "optional").lower()

    def is_protected_path(self, path: str) -> bool:
        if path in PUBLIC_API_PATHS:
            return False
        return path.startswith("/api/")

    async def authenticate(self, req: Request) -> Dict[str, Any]:
        token = extract_id_token(req)

        if not token:
            if self.mode in ("open", "optional"):
                return {"ok": True, "user": None, "status": 200}
            return {"ok": False, "user": None, "status": 401, "error": "Authentication required"}

        if token in self._sessions:
            return {"ok": True, "status": 200, "user": self._sessions[token]}

        # In required mode an unrecognized bearer must NOT be downgraded to the
        # fake guest/admin projection. Firebase ID tokens are verified at
        # /api/auth/google and then registered above; anything else is rejected.
        if self.mode not in ("open", "optional"):
            return {"ok": False, "user": None, "status": 401, "error": "Authentication required"}

        # Basic token projection for guest/offline sessions (no tenant claim).
        return {
            "ok": True,
            "status": 200,
            "user": {
                "id": "user_" + token[-8:] if len(token) >= 8 else token,
                "email": "admin@example.com",
                "fullName": "Admin",
                "username": "admin",
            },
        }
