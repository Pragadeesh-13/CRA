"""Lazy, fail-closed Firebase ID-token verification (Admin-Configured fork only).

Identity and tenant/org context are derived ONLY from the verified, decoded
token claims. Request body fields are never trusted. Credentials come only
from a service-account reference in the environment or Application Default
Credentials; nothing is committed and no secret is logged.

Requires the official ``firebase-admin`` SDK (see requirements.txt). The import
is lazy so the app and the test suite run without the SDK installed; when it is
missing, verification fails closed with a 503-style error.

Error hygiene: backend/SDK exception text is never surfaced in
``TokenVerificationError.message`` or HTTP responses (it can leak project and
credential details). Only the sanitized exception class name is logged, and no
token or credential value is ever logged.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from typing import Any, Dict, Optional

from app.admin_config import is_valid_tenant_id

logger = logging.getLogger("auth:firebase")

# The single verified custom claim honored as tenant context. CRA policy storage
# is tenant-keyed, so tenancy is canonicalized on `tenant_id` only; `org_id` is
# intentionally ignored and never authorizes a tenant.
_TENANT_CLAIM = "tenant_id"

# Generic, non-leaking messages.
_UNAVAILABLE_SDK = "firebase-admin SDK unavailable"
_UNAVAILABLE_CREDS = "Firebase credentials/config unavailable"
_UNAVAILABLE_PROJECT = "Firebase project ID is not configured"
_BAD_SERVICE_ACCOUNT = "invalid FIREBASE_SERVICE_ACCOUNT"
_INVALID_TOKEN = "Firebase ID token verification failed"


class TokenVerificationError(Exception):
    """Verification failure. ``status`` distinguishes unavailable (503) from bad token (401)."""

    def __init__(self, error_type: str, message: str, status: int = 401):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.status = status


class FirebaseTokenVerifier:
    def __init__(self, project_id: Optional[str] = None, service_account: Optional[str] = None):
        self.project_id = (project_id if project_id is not None else os.getenv("FIREBASE_PROJECT_ID", "")).strip()
        self.service_account = (
            service_account if service_account is not None else os.getenv("FIREBASE_SERVICE_ACCOUNT", "")
        ).strip()
        self._app = None
        self._auth = None

    # -- seams (overridden/monkeypatched in tests) ---------------------------
    def _load_firebase_admin(self):
        """Import the SDK lazily. Raises if unavailable."""
        import firebase_admin
        from firebase_admin import auth, credentials

        return firebase_admin, auth, credentials

    def _build_credential(self, credentials):
        raw = self.service_account
        if os.path.exists(raw):
            return credentials.Certificate(raw)
        try:
            return credentials.Certificate(json.loads(raw))
        except Exception as e:
            logger.warning("FIREBASE_SERVICE_ACCOUNT parse failed: %s", type(e).__name__)
            raise TokenVerificationError("verifier_unavailable", _BAD_SERVICE_ACCOUNT, 503)

    # -- lifecycle -----------------------------------------------------------
    def _ensure_app(self) -> None:
        if self._app is not None:
            return
        if not self.project_id:
            # No default project is shipped; never fall back to a stale/old one.
            logger.warning("FIREBASE_PROJECT_ID not configured")
            raise TokenVerificationError("verifier_unavailable", _UNAVAILABLE_PROJECT, 503)
        try:
            firebase_admin, auth_mod, credentials = self._load_firebase_admin()
        except Exception as e:
            logger.warning("firebase-admin import failed: %s", type(e).__name__)
            raise TokenVerificationError("verifier_unavailable", _UNAVAILABLE_SDK, 503)
        try:
            if self.service_account:
                cred = self._build_credential(credentials)
            else:
                cred = credentials.ApplicationDefault()
            options = {"projectId": self.project_id} if self.project_id else None
            try:
                self._app = firebase_admin.initialize_app(cred, options)
            except ValueError:
                # Default app already initialized (e.g. by the host process).
                self._app = firebase_admin.get_app()
        except TokenVerificationError:
            raise
        except Exception as e:
            logger.warning("Firebase credential init failed: %s", type(e).__name__)
            raise TokenVerificationError("verifier_unavailable", _UNAVAILABLE_CREDS, 503)
        self._auth = auth_mod

    def _verify_kwargs(self) -> Dict[str, Any]:
        """Pass the app explicitly (pins the audience/project check) and opt into
        revocation checking when the installed SDK supports it."""
        try:
            params = inspect.signature(self._auth.verify_id_token).parameters
        except (TypeError, ValueError):
            params = {}
        kwargs: Dict[str, Any] = {}
        if not params or "app" in params:
            kwargs["app"] = self._app
        if not params or "check_revoked" in params:
            kwargs["check_revoked"] = True
        return kwargs

    # -- verification --------------------------------------------------------
    def verify(self, id_token: str) -> Dict[str, Any]:
        """Verify signature/issuer/audience/expiry; return a trusted principal.

        Raises ``TokenVerificationError`` (503 unavailable, 401 invalid token).
        """
        if not isinstance(id_token, str) or not id_token.strip():
            raise TokenVerificationError("invalid_token", _INVALID_TOKEN, 401)
        self._ensure_app()
        try:
            decoded = self._auth.verify_id_token(id_token, **self._verify_kwargs())
        except Exception as e:
            logger.warning("firebase token verification failed: %s", type(e).__name__)
            raise TokenVerificationError("invalid_token", _INVALID_TOKEN, 401)
        if not isinstance(decoded, dict) or not decoded:
            raise TokenVerificationError("invalid_token", _INVALID_TOKEN, 401)
        return self._principal_from_claims(decoded)

    def _principal_from_claims(self, decoded: Dict[str, Any]) -> Dict[str, Any]:
        """Build the principal from decoded claims ONLY (never from request data)."""
        uid = decoded.get("uid") or decoded.get("user_id") or decoded.get("sub")
        if not uid:
            raise TokenVerificationError("invalid_token", _INVALID_TOKEN, 401)
        email = decoded.get("email")
        name = decoded.get("name")
        user: Dict[str, Any] = {
            "id": str(uid),
            "uid": str(uid),
            "email": email if isinstance(email, str) else "",
            "fullName": name if isinstance(name, str) else "",
        }
        if isinstance(email, str) and "@" in email:
            user["username"] = email.split("@")[0]
        else:
            user["username"] = str(uid)
        # tenant_id only: org_id is ignored, so a token lacking a valid
        # tenant_id claim yields no tenant (fail closed, no default tenant).
        candidate = decoded.get(_TENANT_CLAIM)
        if is_valid_tenant_id(candidate):
            user["tenant_id"] = candidate
        return user
