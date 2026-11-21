"""Typed complaint classification through LM Studio's System One endpoint."""

import asyncio
import logging
import os

import httpx

from app.providers import provider_base_url
from app.providers.openai_driver import normalize_base_url

logger = logging.getLogger(__name__)

CATEGORY_DESCRIPTIONS = {
    "Account Management": "Account profile, settings, permissions, or organization administration.",
    "Authentication & Access": "Sign-in problems, passwords, MFA, or account lockouts.",
    "Billing": "Invoices, receipts, billing details, or payment issues without a refund request.",
    "Bugs & Errors": "Incorrect product behavior, crashes, errors, or broken features.",
    "Data Sync": "Missing, stale, duplicated, or inconsistent records during synchronization.",
    "Integrations & API": "API calls, SDKs, webhooks, or third-party integrations.",
    "Onboarding & How-To": "Getting started, feature guidance, or instructions for ordinary use.",
    "Performance": "Slow responses, latency, or degraded performance.",
    "Refunds": "Refund requests, duplicate charges, overcharges, or disputes about a charge.",
    "Security": "Suspected account takeover, exposure, security incidents, or threats.",
    "Subscription": "Plan changes, cancellation, renewal terms, or subscription credits.",
}
SDK_CATEGORY_ALIASES = {
    "Subscriptions": "Subscription",
    "Bugs": "Bugs & Errors",
    "System Performance": "Performance",
    "Integrations & APIs": "Integrations & API",
    "Onboarding": "Onboarding & How-To",
}


def configured_jev_model() -> str:
    return os.getenv("TROY_JEV_MODEL", "").strip()


async def classify_with_jev(
    complaint: str,
    model: str,
    criteria: dict[str, str] | None = None,
) -> list[dict] | None:
    """Return up to three ranked choices, or None if no valid decision is available."""
    descriptions = CATEGORY_DESCRIPTIONS if criteria is None else criteria
    if not descriptions:
        return None
    base_url = os.getenv("TROY_JEV_BASE_URL", "").strip() or provider_base_url("lmstudio")
    endpoint = f"{normalize_base_url(base_url)}/v1/systemone"
    token = os.getenv("TROY_JEV_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Stable short choice keys also work with models that restrict the option key format.
    options = {str(index): name for index, name in enumerate(descriptions)}
    question = {
        "type": "choice",
        "instructions": "Select the best support category for the customer's primary issue.",
        "criteria": {key: f"{name}: {descriptions[name]}" for key, name in options.items()},
    }
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False, headers=headers) as client:
            response = await asyncio.wait_for(
                client.post(endpoint, json={
                    "model": model,
                    "state": complaint[:4000],
                    "questions": {"category": question},
                }),
                timeout=10,
            )
            response.raise_for_status()
        answer = response.json()["answers"]["category"]
        scores = answer["probabilities"]
        choice = answer["choice"]
        if not isinstance(scores, dict) or set(scores) != set(options) or choice not in options:
            raise ValueError("Invalid decision options")
        if any(type(score) not in (int, float) or not 0 <= score <= 1 for score in scores.values()):
            raise ValueError("Invalid decision score")
        if scores[choice] <= 0 or scores[choice] != max(scores.values()):
            raise ValueError("Invalid winning choice")
    except (httpx.HTTPError, TimeoutError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Jev classification unavailable (%s)", type(exc).__name__)
        return None

    ranked = sorted(options, key=lambda key: (scores[key], key == choice), reverse=True)
    return [{"category": options[key], "score": scores[key]} for key in ranked[:3]]
