import secrets
import time
from typing import Dict, Any, Optional
from fastapi import Request

SESSION_COOKIE = "__session"
SESSION_SECONDS = 12 * 60 * 60

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
    # Plug-and-play SaaS surfaces: embeddable widget + external webhooks
    # (Zendesk/Intercom/Stripe/Freshdesk) cannot carry session cookies.
    "/api/v1/saas/resolve",
    "/api/v1/saas/info",
    "/widget/troy-embed.js",
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
    return None


def is_authenticated_webhook_relay(req: Request) -> bool:

    prefix = "/api/v1/saas/webhooks/"
    path = req.url.path
    if req.method != "POST" or not path.startswith(prefix) or "/" in path[len(prefix):]:
        return False
    return has_webhook_relay_token(req)


def has_webhook_relay_token(req: Request) -> bool:
    from app.config import settings

    supplied = req.headers.get("X-Troy-Webhook-Token", "")
    expected = settings.webhook_token
    return bool(expected and supplied and secrets.compare_digest(supplied.encode(), expected.encode()))


class SessionGuard:
    def __init__(self, mode: Optional[str] = None):
        self._mode = mode.lower() if mode else None
        self._sessions: Dict[str, tuple[float, Dict[str, Any]]] = {}

    def create_session(self, user: Dict[str, Any]) -> str:
        now = time.monotonic()
        self._sessions = {key: item for key, item in self._sessions.items() if item[0] > now}
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (now + SESSION_SECONDS, user.copy())
        return token

    def revoke_session(self, token: str):
        self._sessions.pop(token, None)

    @property
    def mode(self) -> str:
        if self._mode is not None:
            return self._mode
        from app.config import settings
        return getattr(settings, "auth_mode", "optional").lower()

    @property
    def required(self) -> bool:
        return self.mode not in {"open", "optional", "demo", "guest"}

    def is_protected_path(self, path: str) -> bool:
        if self.required:
            return path.startswith("/api/") and path not in {
                "/api/auth/firebase-config", "/api/auth/signup",
                "/api/auth/login", "/api/auth/google", "/api/health",
            }
        if path in PUBLIC_API_PATHS or path.startswith("/api/v1/saas/webhooks/"):
            return False
        return path.startswith("/api/")

    async def authenticate(self, req: Request) -> Dict[str, Any]:
        token = extract_id_token(req)
        session = self._sessions.get(token) if token else None
        if session and session[0] > time.monotonic():
            return {"ok": True, "status": 200, "user": session[1].copy()}
        if token:
            self.revoke_session(token)
        if self.required:
            return {"ok": False, "user": None, "status": 401, "error": "Authentication required"}
        return {"ok": True, "user": None, "status": 200}
