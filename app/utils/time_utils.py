"""Shared time helpers for Troy.

Single canonical home for the UTC timestamp helper used across the
codebase (adapters, tool registry, SDK client).
"""

from __future__ import annotations

from datetime import datetime, timezone


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


__all__ = ["_utcnow_iso"]
