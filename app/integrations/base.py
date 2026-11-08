"""Integration adapter + secret-resolver protocols (read-only MVP).

Read-only until runtime policy enforcement is implemented: adapters perform
GET-only connection checks, never write to the external SaaS, and configured
resolution policy is NOT yet enforced by Troy.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Protocol, runtime_checkable

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretResolutionError(Exception):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


@runtime_checkable
class SecretResolver(Protocol):
    def resolve(self, secret_ref: str) -> str:
        """Resolve a secret_ref to a secret value. Must never log the value."""
        ...


class EnvSecretResolver:
    """Resolves only ``env:NAME`` references from the process environment."""

    def resolve(self, secret_ref: str) -> str:
        ref = (secret_ref or "").strip()
        if ref.startswith("vault:") or ref.startswith("secret:"):
            raise SecretResolutionError(
                "resolver_not_configured",
                "resolver not configured for vault:/secret: secret references",
            )
        if not ref.startswith("env:"):
            raise SecretResolutionError("invalid_secret_ref", "secret_ref must use env:, vault:, or secret: scheme")
        name = ref[4:].strip()
        if not _ENV_NAME_RE.match(name):
            raise SecretResolutionError("invalid_secret_ref", "env: reference name is invalid")
        value = os.getenv(name, "")
        if not value:
            raise SecretResolutionError("secret_not_found", f"environment variable {name} is not set")
        return value


@runtime_checkable
class IntegrationAdapter(Protocol):
    name: str

    async def check_connection(self, secret: str, timeout_s: float = 5.0) -> Dict[str, Any]:
        """Read-only GET connection check. No writes; never log/return secret."""
        ...
