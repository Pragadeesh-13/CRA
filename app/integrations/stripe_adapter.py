"""Stripe read-only connection check (GET /v1/account only, no writes)."""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

STRIPE_API_BASE_URL = "https://api.stripe.com/v1"


class StripeAdapter:
    name = "stripe"
    base_url = STRIPE_API_BASE_URL

    async def check_connection(
        self, secret: str, timeout_s: float = 5.0, transport: Optional[Any] = None
    ) -> Dict[str, Any]:
        # GET only; secret is used solely as a Bearer credential and is
        # never included in the result or logged.
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s), transport=transport
            ) as client:
                resp = await client.get(
                    f"{self.base_url}/account",
                    headers={"Authorization": f"Bearer {secret}"},
                )
        except httpx.TimeoutException:
            return {"ok": False, "read_only": True, "mutated_external": False, "error_type": "timeout", "error": "stripe connection check timed out"}
        except httpx.RequestError:
            return {"ok": False, "read_only": True, "mutated_external": False, "error_type": "network_error", "error": "stripe connection check failed (network)"}
        if resp.status_code != 200:
            return {
                "ok": False, "read_only": True, "mutated_external": False,
                "status_code": resp.status_code, "error_type": "http_error",
                "error": f"stripe returned HTTP {resp.status_code}",
            }
        try:
            data = resp.json()
        except Exception:
            data = {}
        return {
            "ok": True, "read_only": True, "mutated_external": False,
            "status_code": 200,
            "account_id": data.get("id") if isinstance(data, dict) else None,
            "livemode": data.get("livemode") if isinstance(data, dict) else None,
        }
