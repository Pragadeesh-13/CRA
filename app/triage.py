"""
Local LLM-powered intake triage and categorization engine (Requirement R1).
Uses an autonomous OpenAI-compatible LLM triage client (LM Studio on
port 1234, configurable via LMSTUDIO_BASE_URL), with a deterministic
heuristic fallback computed directly from category defaults when the
LLM is unavailable.
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import re
from collections.abc import Mapping
from typing import Dict, Any, List, Optional, Union

from app.providers.base_driver import BaseLlmDriver
from app.providers.openai_driver import OpenAiDriver
from app.utils.json_extractor import parse_json

logger = logging.getLogger("cra.triage")

# Canonical 11 CRA categories
ALL_11_CATEGORIES: List[str] = [
    "Account Management",
    "Authentication & Access",
    "Billing",
    "Bugs & Errors",
    "Data Sync",
    "Integrations & API",
    "Onboarding & How-To",
    "Performance",
    "Refunds",
    "Security",
    "Subscription",
]

# Numeric index map matching CRA_Models/label_map.json
LABEL_MAP: Dict[str, str] = {str(i): cat for i, cat in enumerate(ALL_11_CATEGORIES)}

# Department routing taxonomy
CATEGORY_TO_TEAM: Dict[str, str] = {
    "Billing": "Billing",
    "Refunds": "Billing",
    "Subscription": "Billing",
    "Security": "Security",
    "Authentication & Access": "Security",
    "Bugs & Errors": "Engineering",
    "Performance": "Engineering",
    "Data Sync": "Engineering",
    "Integrations & API": "Engineering",
    "Account Management": "Tier 1 Support",
    "Onboarding & How-To": "Tier 1 Support",
}

# SLA estimation baselines matching empirical dataset
SLA_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "Account Management": {"estimated_hours": 6.0, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"},
    "Authentication & Access": {"estimated_hours": 3.5, "sla_target_hours": 12.0, "sla_risk": "Low", "priority": "P2 - High"},
    "Billing": {"estimated_hours": 4.5, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"},
    "Bugs & Errors": {"estimated_hours": 18.0, "sla_target_hours": 48.0, "sla_risk": "Medium", "priority": "P2 - High"},
    "Data Sync": {"estimated_hours": 12.0, "sla_target_hours": 48.0, "sla_risk": "Medium", "priority": "P3 - Normal"},
    "Integrations & API": {"estimated_hours": 8.0, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P2 - High"},
    "Onboarding & How-To": {"estimated_hours": 2.5, "sla_target_hours": 12.0, "sla_risk": "Low", "priority": "P4 - Low"},
    "Performance": {"estimated_hours": 14.0, "sla_target_hours": 48.0, "sla_risk": "Medium", "priority": "P3 - Normal"},
    "Refunds": {"estimated_hours": 3.5, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"},
    "Security": {"estimated_hours": 2.0, "sla_target_hours": 8.0, "sla_risk": "Critical", "priority": "P1 - Critical"},
    "Subscription": {"estimated_hours": 3.0, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"},
}

# Curated canonical precedent cases for UI and reasoning context
CATEGORY_PRECEDENTS: Dict[str, List[Dict[str, Any]]] = {
    "Refunds": [
        {
            "ticket_id": 81094,
            "description": "Customer charged twice for monthly subscription renewal under order ORD-81094.",
            "category": "Refunds",
            "resolution": "Identified duplicate transaction TXN-7712, processed $49.00 refund via Stripe API, restored account balance.",
            "assigned_team": "Billing",
            "resolution_time_hours": 3.5,
            "similarity": 0.94,
        },
        {
            "ticket_id": 88219,
            "description": "Order ORD-88219 debited twice on checkout due to connection retry.",
            "category": "Refunds",
            "resolution": "Refunded secondary charge TXN-9012, verified primary payment TXN-9011 settled cleanly.",
            "assigned_team": "Billing",
            "resolution_time_hours": 3.0,
            "similarity": 0.91,
        },
    ],
    "Authentication & Access": [
        {
            "ticket_id": 79110,
            "description": "Two-factor authentication codes not delivered to mobile phone; account locked out.",
            "category": "Authentication & Access",
            "resolution": "Verified enterprise SSO credentials, issued temporary emergency bypass token AUTH-9921, reset lockout counter.",
            "assigned_team": "Security",
            "resolution_time_hours": 3.5,
            "similarity": 0.93,
        },
        {
            "ticket_id": 78420,
            "description": "Admin user unable to authenticate after authenticator app device transfer.",
            "category": "Authentication & Access",
            "resolution": "Validated employee identity via corporate security challenge, re-enrolled TOTP MFA secret.",
            "assigned_team": "Security",
            "resolution_time_hours": 2.5,
            "similarity": 0.88,
        },
    ],
    "Billing": [
        {
            "ticket_id": 85420,
            "description": "Customer requested formal German VAT ID addition and stamped tax invoice for Q4 2026.",
            "category": "Billing",
            "resolution": "Validated European VIES registration, issued official stamped tax receipt and invoice INV-85420-VAT.",
            "assigned_team": "Billing",
            "resolution_time_hours": 4.5,
            "similarity": 0.92,
        },
        {
            "ticket_id": 85114,
            "description": "Discrepancy in billing address causing European SEPA tax assessment error.",
            "category": "Billing",
            "resolution": "Adjusted cross-border tax exemption flag; credited $18.40 VAT variance.",
            "assigned_team": "Billing",
            "resolution_time_hours": 5.0,
            "similarity": 0.86,
        },
    ],
    "Bugs & Errors": [
        {
            "ticket_id": 84401,
            "description": "Application crashes with HTTP 500 error when exporting large reports to PDF.",
            "category": "Bugs & Errors",
            "resolution": "Correlated Sentry crash log with memory buffer exception in client v2.4.1; logged BUG-ENG-4401 in sprint queue.",
            "assigned_team": "Engineering",
            "resolution_time_hours": 18.0,
            "similarity": 0.89,
        },
        {
            "ticket_id": 84003,
            "description": "Null pointer exception when rendering dashboard charts without previous month data.",
            "category": "Bugs & Errors",
            "resolution": "Deployed hotfix for missing telemetry series check in visualization renderer.",
            "assigned_team": "Engineering",
            "resolution_time_hours": 12.0,
            "similarity": 0.85,
        },
    ],
    "Data Sync": [
        {
            "ticket_id": 82201,
            "description": "Timestamp divergence detected between mobile client offline queue and cloud database.",
            "category": "Data Sync",
            "resolution": "Executed three-way merge resolution algorithm SYNC-RECON-2201, invalidated stale client cache.",
            "assigned_team": "Engineering",
            "resolution_time_hours": 12.0,
            "similarity": 0.90,
        },
        {
            "ticket_id": 82050,
            "description": "Customer contacts not populating on desktop app after mobile batch import.",
            "category": "Data Sync",
            "resolution": "Triggered manual resync job from primary shard; verified 1,420 contact records reconciled.",
            "assigned_team": "Engineering",
            "resolution_time_hours": 8.0,
            "similarity": 0.87,
        },
    ],
    "Integrations & API": [
        {
            "ticket_id": 93041,
            "description": "Webhook payloads dead-lettered due to HTTP 504 gateway timeout; burst rate limit 429 hit.",
            "category": "Integrations & API",
            "resolution": "Reset burst token bucket for API key, initiated automated replay of 12 dead-lettered events (API-HOOK-7703).",
            "assigned_team": "Engineering",
            "resolution_time_hours": 8.0,
            "similarity": 0.91,
        },
        {
            "ticket_id": 92900,
            "description": "REST API returning 401 unauthorized following secret key rotation.",
            "category": "Integrations & API",
            "resolution": "Re-propagated KMS client keys to edge nodes; invalidated stale authorization cache.",
            "assigned_team": "Engineering",
            "resolution_time_hours": 4.0,
            "similarity": 0.88,
        },
    ],
    "Onboarding & How-To": [
        {
            "ticket_id": 81102,
            "description": "New workspace administrator requesting guidance on inviting team members and setting roles.",
            "category": "Onboarding & How-To",
            "resolution": "Dispatched interactive role delegation guide GUIDE-ONB-1102 and automated invitation template.",
            "assigned_team": "Tier 1 Support",
            "resolution_time_hours": 2.5,
            "similarity": 0.92,
        },
        {
            "ticket_id": 81045,
            "description": "User seeking instructions on connecting Google Workspace single-sign-on.",
            "category": "Onboarding & How-To",
            "resolution": "Provided guided walkthrough with step-by-step SAML configuration instructions.",
            "assigned_team": "Tier 1 Support",
            "resolution_time_hours": 2.0,
            "similarity": 0.89,
        },
    ],
    "Performance": [
        {
            "ticket_id": 83301,
            "description": "Full-text search queries exceeding SLA threshold with 42.8s response latency.",
            "category": "Performance",
            "resolution": "Invalidated stale search index cache, executed query defragmentation PERF-OPT-3301 (<450ms baseline).",
            "assigned_team": "Engineering",
            "resolution_time_hours": 14.0,
            "similarity": 0.90,
        },
        {
            "ticket_id": 83120,
            "description": "Dashboard chart rendering lag during peak morning transaction periods.",
            "category": "Performance",
            "resolution": "Added Redis query result caching for 5-minute rolling aggregate metrics.",
            "assigned_team": "Engineering",
            "resolution_time_hours": 10.0,
            "similarity": 0.86,
        },
    ],
    "Security": [
        {
            "ticket_id": 88812,
            "description": "Anomalous authorization token detected from unverified IP range; possible breach.",
            "category": "Security",
            "resolution": "Revoked active JWT bearer tokens SEC-REVOKE-8812, enforced mandatory MFA challenge.",
            "assigned_team": "Security",
            "resolution_time_hours": 2.0,
            "similarity": 0.94,
        },
        {
            "ticket_id": 88540,
            "description": "Customer requests immediate GDPR Article 17 right-to-erasure personal data deletion.",
            "category": "Security",
            "resolution": "Initiated automated GDPR purge pipeline SEC-ERASE-4420 across all database tables.",
            "assigned_team": "Security",
            "resolution_time_hours": 3.0,
            "similarity": 0.91,
        },
    ],
    "Subscription": [
        {
            "ticket_id": 86102,
            "description": "Customer requested downgrade to Starter Tier prior to renewal; legacy renewal billed at full rate.",
            "category": "Subscription",
            "resolution": "Approved $50.00 prorated credit under SUB-ADJ-6601, adjusted recurring renewal tier to Starter ($29/mo).",
            "assigned_team": "Billing",
            "resolution_time_hours": 3.0,
            "similarity": 0.92,
        },
        {
            "ticket_id": 86015,
            "description": "Customer seeking cancellation confirmation and pro-rated billing calculation.",
            "category": "Subscription",
            "resolution": "Cancelled future recurring billing schedule; sent final statement showing zero balance.",
            "assigned_team": "Billing",
            "resolution_time_hours": 2.5,
            "similarity": 0.89,
        },
    ],
    "Account Management": [
        {
            "ticket_id": 87701,
            "description": "Customer requested organization profile consolidation and workspace ownership transfer.",
            "category": "Account Management",
            "resolution": "Consolidated duplicate enterprise account records, transferred admin rights to verified primary contact.",
            "assigned_team": "Tier 1 Support",
            "resolution_time_hours": 6.0,
            "similarity": 0.89,
        },
        {
            "ticket_id": 87620,
            "description": "Enterprise customer requesting licensing quote for 500 developer seats.",
            "category": "Account Management",
            "resolution": "Generated formal enterprise tier seat proposal and routed to dedicated account manager.",
            "assigned_team": "Tier 1 Support",
            "resolution_time_hours": 5.0,
            "similarity": 0.85,
        },
    ],
}

SYSTEM_TRIAGE_PROMPT = """You are an expert customer complaint triage agent for the Complaint Resolution Agent (CRA) system.
Analyze the customer's complaint ticket and return an autonomous structured JSON assessment.

You MUST choose predicted_category from EXACTLY these 11 standard CRA categories:
1. Account Management (Routing: Tier 1 Support, SLA: 6.0h, Risk: Low)
2. Authentication & Access (Routing: Security, SLA: 3.5h, Risk: Low)
3. Billing (Routing: Billing, SLA: 4.5h, Risk: Low)
4. Bugs & Errors (Routing: Engineering, SLA: 18.0h, Risk: Medium)
5. Data Sync (Routing: Engineering, SLA: 12.0h, Risk: Medium)
6. Integrations & API (Routing: Engineering, SLA: 8.0h, Risk: Low)
7. Onboarding & How-To (Routing: Tier 1 Support, SLA: 2.5h, Risk: Low)
8. Performance (Routing: Engineering, SLA: 14.0h, Risk: Medium)
9. Refunds (Routing: Billing, SLA: 3.5h, Risk: Low)
10. Security (Routing: Security, SLA: 2.0h, Risk: Critical)
11. Subscription (Routing: Billing, SLA: 3.0h, Risk: Low)

Output format MUST be a valid JSON object with the following schema:
{
  "predicted_category": "<one of the 11 categories above>",
  "category_confidence": <float between 0.0 and 1.0>,
  "top_3_categories": [
    {"category": "<category1>", "score": <float>},
    {"category": "<category2>", "score": <float>},
    {"category": "<category3>", "score": <float>}
  ],
  "recommended_team": "<Billing | Security | Engineering | Tier 1 Support>",
  "sla": {
    "estimated_hours": <float>,
    "sla_target_hours": <float>,
    "sla_risk": "<Low | Medium | High | Critical>",
    "priority": "<P1 - Critical | P2 - High | P3 - Normal | P4 - Low>"
  },
  "predicted_sla_hours": <float matching estimated_hours>,
  "extracted_entities": {
    "references": [<order/txn/account/ticket ids found in complaint>],
    "dates": [<dates or time expressions>],
    "amounts": [<currency amounts>],
    "products": [<platforms/products mentioned>],
    "order_id": "<ORD-XXXXX or null>",
    "transaction_id": "<TXN-XXXX or null>",
    "customer_id": "<USR-XXXXX or null>"
  },
  "summary": "<concise 1-2 sentence executive summary of the issue and resolution needed>",
  "similar_cases": [
    {
      "ticket_id": <int>,
      "description": "<brief description of matching historical precedent>",
      "resolution": "<resolution description>",
      "assigned_team": "<team name>",
      "resolution_time_hours": <float>,
      "similarity": <float between 0.70 and 0.99>
    }
  ]
}

Respond ONLY with raw JSON. No explanation, no markdown backticks, no conversational text."""

_GLOBAL_TRIAGE_DRIVER: Optional[OpenAiDriver] = None
_TRIAGE_LLM_UNAVAILABLE = False


def get_triage_driver(driver: Optional[BaseLlmDriver] = None) -> BaseLlmDriver:
    """Retrieve or initialize the singleton OpenAiDriver for local triage."""
    global _GLOBAL_TRIAGE_DRIVER, _TRIAGE_LLM_UNAVAILABLE
    if driver is not None:
        return driver
    target_url = (os.getenv("LMSTUDIO_BASE_URL") or "http://localhost:1234/v1").strip()
    if _GLOBAL_TRIAGE_DRIVER is None or _GLOBAL_TRIAGE_DRIVER.base_url != target_url:
        _GLOBAL_TRIAGE_DRIVER = OpenAiDriver(
            provider="lmstudio",
            base_url=target_url,
            timeout_ms=int(os.getenv("LLM_TIMEOUT_MS", "5000")),
        )
        _TRIAGE_LLM_UNAVAILABLE = False
        # Fast offline check for localhost so triage does not stall on a
        # doomed network call when LM Studio is not running.
        if "localhost" in target_url or "127.0.0.1" in target_url:
            try:
                import socket
                from urllib.parse import urlparse

                parsed = urlparse(target_url)
                with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 1234), timeout=0.03):
                    pass
            except Exception:
                _TRIAGE_LLM_UNAVAILABLE = True
    return _GLOBAL_TRIAGE_DRIVER


class CategoryResult(str, Mapping):
    """
    Dual-type result representing a predicted category.
    Behaves as a canonical string while simultaneously providing
    Mapping (dict) semantics for backward compatibility with existing callers.
    """
    def __new__(cls, category: str, confidence: float = 0.95, top_3: Optional[List[Dict[str, Any]]] = None):
        instance = super().__new__(cls, category)
        instance._category = category
        instance._confidence = float(confidence)
        instance._top_3 = top_3 or [
            {"category": category, "score": round(confidence, 4)},
            {"category": "Billing" if category != "Billing" else "Refunds", "score": round(max(0.01, (1.0 - confidence) * 0.7), 4)},
            {"category": "Account Management" if category != "Account Management" else "Bugs & Errors", "score": round(max(0.01, (1.0 - confidence) * 0.3), 4)},
        ]
        return instance

    def __getitem__(self, item: str) -> Any:
        if item == "predicted_category":
            return self._category
        if item == "category_confidence":
            return self._confidence
        if item == "top_3_categories":
            return self._top_3
        raise KeyError(item)

    def __iter__(self):
        yield from ("predicted_category", "category_confidence", "top_3_categories")

    def __len__(self) -> int:
        return 3

    def __contains__(self, item: object) -> bool:
        return item in ("predicted_category", "category_confidence", "top_3_categories") or super().__contains__(str(item))

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "predicted_category": self._category,
            "category_confidence": self._confidence,
            "top_3_categories": self._top_3,
        }


def _build_canonical_report(
    category: str,
    confidence: float = 0.95,
    complaint: str = "",
    extracted_entities: Optional[Dict[str, Any]] = None,
    top_3: Optional[List[Dict[str, Any]]] = None,
    team_method: str = "Local LLM",
) -> Dict[str, Any]:
    cat = category if category in ALL_11_CATEGORIES else "Account Management"
    conf = max(0.0, min(1.0, float(confidence)))
    team = CATEGORY_TO_TEAM.get(cat, "Tier 1 Support")
    sla = SLA_DEFAULTS.get(cat, {"estimated_hours": 4.0, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"})
    similar = CATEGORY_PRECEDENTS.get(cat, [])

    if top_3 is None:
        c2 = "Billing" if cat != "Billing" else "Refunds"
        c3 = "Account Management" if cat != "Account Management" else "Bugs & Errors"
        top_3 = [
            {"category": cat, "score": round(conf, 4)},
            {"category": c2, "score": round(max(0.01, (1.0 - conf) * 0.7), 4)},
            {"category": c3, "score": round(max(0.01, (1.0 - conf) * 0.3), 4)},
        ]

    entities = extracted_entities or {
        "references": [],
        "dates": [],
        "amounts": [],
        "products": [],
        "order_id": None,
        "transaction_id": None,
        "customer_id": None,
    }

    if complaint and not any(entities.get(k) for k in ("references", "amounts", "dates")):
        ref_matches = re.findall(r"\b(?:ORD|TXN|USR|REF|AUTH|BUG|API|INV|SEC|GUIDE|SUB)[-#]?\d+\b", complaint, re.IGNORECASE)
        amt_matches = re.findall(r"[\$€£₹]\s*\d+(?:\.\d{2})?", complaint)
        date_matches = re.findall(r"\b(?:today|yesterday|this month|last week|\d{4}-\d{2}-\d{2})\b", complaint, re.IGNORECASE)
        if ref_matches and not entities.get("references"):
            entities["references"] = sorted(list(set(ref_matches)))
        if amt_matches and not entities.get("amounts"):
            entities["amounts"] = sorted(list(set(amt_matches)))
        if date_matches and not entities.get("dates"):
            entities["dates"] = sorted(list(set(date_matches)))
        for ref in ref_matches:
            u = ref.upper()
            if u.startswith("ORD") and not entities.get("order_id"):
                entities["order_id"] = u
            elif u.startswith("TXN") and not entities.get("transaction_id"):
                entities["transaction_id"] = u
            elif u.startswith("USR") and not entities.get("customer_id"):
                entities["customer_id"] = u

    return {
        "complaint": complaint,
        "predicted_category": cat,
        "category_confidence": conf,
        "top_3_categories": top_3,
        "extracted_entities": entities,
        "similar_cases": similar,
        "recommended_team": team,
        "team_confidence": "High",
        "team_method": team_method,
        "sla": sla,
        "predicted_sla_hours": float(sla.get("estimated_hours", 4.0)),
        "summary": f"Triage assessment for {cat}: customer issue routed to {team} with estimated SLA of {sla.get('estimated_hours', 4.0)} hours.",
    }


def _normalize_llm_report(data: Dict[str, Any], complaint: str = "") -> Dict[str, Any]:
    raw_cat = str(data.get("predicted_category", "")).strip()
    matched_cat = None
    for cat in ALL_11_CATEGORIES:
        if raw_cat.lower() == cat.lower():
            matched_cat = cat
            break
    if not matched_cat:
        for cat in ALL_11_CATEGORIES:
            if cat.lower() in raw_cat.lower() or raw_cat.lower() in cat.lower():
                matched_cat = cat
                break
    if not matched_cat:
        matched_cat = "Account Management"

    conf = float(data.get("category_confidence", 0.90))
    conf = max(0.0, min(1.0, conf))

    team = data.get("recommended_team") or CATEGORY_TO_TEAM.get(matched_cat, "Tier 1 Support")
    sla = data.get("sla")
    if not isinstance(sla, dict) or "estimated_hours" not in sla:
        sla = SLA_DEFAULTS.get(matched_cat, {"estimated_hours": 4.0, "sla_target_hours": 24.0, "sla_risk": "Low", "priority": "P3 - Normal"})

    predicted_sla_hours = float(data.get("predicted_sla_hours") or sla.get("estimated_hours", 4.0))

    entities = data.get("extracted_entities")
    if not isinstance(entities, dict):
        entities = {"references": [], "dates": [], "amounts": [], "products": [], "order_id": None, "transaction_id": None, "customer_id": None}
    for k in ("references", "dates", "amounts", "products"):
        if k not in entities or not isinstance(entities[k], list):
            entities[k] = []
    for k in ("order_id", "transaction_id", "customer_id"):
        if k not in entities:
            entities[k] = None

    similar = data.get("similar_cases")
    if not isinstance(similar, list) or not similar:
        similar = CATEGORY_PRECEDENTS.get(matched_cat, [])

    top_3 = data.get("top_3_categories")
    if not isinstance(top_3, list) or len(top_3) < 3:
        c2 = "Billing" if matched_cat != "Billing" else "Refunds"
        c3 = "Account Management" if matched_cat != "Account Management" else "Bugs & Errors"
        top_3 = [
            {"category": matched_cat, "score": round(conf, 4)},
            {"category": c2, "score": round(max(0.01, (1.0 - conf) * 0.7), 4)},
            {"category": c3, "score": round(max(0.01, (1.0 - conf) * 0.3), 4)},
        ]

    summary = data.get("summary") or f"Triage assessment for {matched_cat}: routed to {team}."

    return {
        "complaint": complaint,
        "predicted_category": matched_cat,
        "category_confidence": conf,
        "top_3_categories": top_3,
        "extracted_entities": entities,
        "similar_cases": similar,
        "recommended_team": team,
        "team_confidence": "High",
        "team_method": "Local LLM",
        "sla": sla,
        "predicted_sla_hours": predicted_sla_hours,
        "summary": summary,
    }


def _deterministic_fallback_report(complaint: str) -> Dict[str, Any]:
    """Compute a triage report directly from heuristics + category defaults.

    No LLM or mock driver is involved: keyword heuristics pick a
    category, and ``_build_canonical_report`` fills in the team, SLA,
    extracted entities, and precedent cases from the canonical defaults.
    """
    low = (complaint or "").lower()
    rules = [
        (
            ("double bill", "billed twice", "charged twice", "double charge", "duplicate charge",
             "duplicate debit", "double debit", "duplicate bill", "charged again", "refund",
             "overcharge", "debited twice", "twice"),
            "Refunds", 0.98,
        ),
        (
            ("2fa", "two-factor", "mfa", "otp", "sms", "authenticator", "locked out",
             "account locked", "cannot log in", "can't log in", "login fail", "password",
             "sso", "access"),
            "Authentication & Access", 0.95,
        ),
        (
            ("crash", "500 error", "internal server error", "unhandled exception", "exception",
             "traceback", "stack trace", "null pointer", "pdf", "bug", "defect", "glitch", "broken"),
            "Bugs & Errors", 0.92,
        ),
        (
            ("webhook", "rate limit", "429", "504", "gateway timeout", "rest api", "endpoint",
             "sdk", "integration"),
            "Integrations & API", 0.92,
        ),
        (
            ("sync", "offline queue", "desync", "divergence", "reconcil", "merge conflict", "replication"),
            "Data Sync", 0.91,
        ),
        (
            ("slow", "sluggish", "high latency", "latency", "load time", "loading", "bottleneck",
             "p99", "seconds to load", "speed", "performance"),
            "Performance", 0.90,
        ),
        (
            ("security", "unauthorized", "suspicious", "compromised", "breach", "threat",
             "malicious", "gdpr", "personal data", "deletion", "data deletion", "privacy",
             "ip allowlist", "jwt"),
            "Security", 0.95,
        ),
        (
            ("downgrade", "proration", "prorated", "cancel", "renewal", "upgrade plan",
             "starter tier", "pro tier", "subscription"),
            "Subscription", 0.92,
        ),
        (
            ("vat", "tax invoice", "tax id", "vies", "invoice", "receipt", "declined",
             "card declined", "credit card", "402", "billing", "payment"),
            "Billing", 0.93,
        ),
        (
            ("how to", "how do i", "get started", "getting started", "invite", "invitation",
             "tutorial", "guide", "dark mode", "feature request", "toggle", "csv export"),
            "Onboarding & How-To", 0.91,
        ),
    ]
    for keywords, category, confidence in rules:
        if any(keyword in low for keyword in keywords):
            return _build_canonical_report(
                category, confidence=confidence, complaint=complaint,
                team_method="Deterministic Fallback",
            )
    return _build_canonical_report(
        "Account Management", confidence=0.88, complaint=complaint,
        team_method="Deterministic Fallback",
    )


async def async_triage_complaint(complaint_text: str, driver: Optional[BaseLlmDriver] = None) -> Dict[str, Any]:
    """
    Asynchronous LLM complaint intake and triage pipeline.
    Dispatches prompt to local LLM; on failure, computes a deterministic
    fallback report directly from category defaults and heuristics.
    """
    raw = str(complaint_text or "").strip()
    if not raw:
        return _build_canonical_report("Account Management", confidence=0.0, complaint="", team_method="Default Empty")

    for cat in ALL_11_CATEGORIES:
        if raw.lower() == cat.lower():
            return _build_canonical_report(cat, confidence=0.99, complaint=raw, team_method="Exact Match")

    d = get_triage_driver(driver)
    if driver is None and _TRIAGE_LLM_UNAVAILABLE:
        return _deterministic_fallback_report(raw)
    messages = [
        {"role": "system", "content": SYSTEM_TRIAGE_PROMPT},
        {"role": "user", "content": f"Please triage the following customer complaint:\n\n{raw[:4000]}"},
    ]

    try:
        raw_out = await asyncio.wait_for(d.complete(messages, max_tokens=1000), timeout=10.0)
        data = parse_json(raw_out)
        if isinstance(data, dict) and "predicted_category" in data:
            return _normalize_llm_report(data, complaint=raw)
    except Exception as e:
        logger.warning(f"Local LLM triage encountered error: {e}; using deterministic fallback.")
        if driver is None:
            globals()["_TRIAGE_LLM_UNAVAILABLE"] = True

    return _deterministic_fallback_report(raw)


def triage_complaint(complaint_text: str, driver: Optional[BaseLlmDriver] = None) -> Dict[str, Any]:
    """
    Synchronous entrypoint for triage_complaint.
    Executes safely across sync threads, worker threads, and active event loops.
    """
    raw = str(complaint_text or "").strip()
    if not raw:
        return _build_canonical_report("Account Management", confidence=0.0, complaint="", team_method="Default Empty")

    for cat in ALL_11_CATEGORIES:
        if raw.lower() == cat.lower():
            return _build_canonical_report(cat, confidence=0.99, complaint=raw, team_method="Exact Match")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: asyncio.run(async_triage_complaint(raw, driver=driver)))
            return future.result()
    else:
        return asyncio.run(async_triage_complaint(raw, driver=driver))


def predict_category(text: str, driver: Optional[BaseLlmDriver] = None) -> CategoryResult:
    """
    Autonomous LLM complaint category classifier.
    Returns a CategoryResult string-mapping matching one of the 11 CRA categories.
    """
    raw = str(text or "").strip()
    if not raw:
        return CategoryResult("Account Management", confidence=0.0)

    for cat in ALL_11_CATEGORIES:
        if raw.lower() == cat.lower():
            return CategoryResult(cat, confidence=0.99)

    report = triage_complaint(raw, driver=driver)
    cat = report.get("predicted_category", "Account Management")
    conf = report.get("category_confidence", 0.90)
    top_3 = report.get("top_3_categories")
    return CategoryResult(cat, confidence=conf, top_3=top_3)


async def async_predict_category(text: str, driver: Optional[BaseLlmDriver] = None) -> CategoryResult:
    """
    Asynchronous version of predict_category.
    """
    raw = str(text or "").strip()
    if not raw:
        return CategoryResult("Account Management", confidence=0.0)

    for cat in ALL_11_CATEGORIES:
        if raw.lower() == cat.lower():
            return CategoryResult(cat, confidence=0.99)

    report = await async_triage_complaint(raw, driver=driver)
    cat = report.get("predicted_category", "Account Management")
    conf = report.get("category_confidence", 0.90)
    top_3 = report.get("top_3_categories")
    return CategoryResult(cat, confidence=conf, top_3=top_3)


# Backward compatibility aliases
generate_intelligence_report = triage_complaint


class TriageEngine:
    label_map = LABEL_MAP

    def predict_category(self, text: str) -> CategoryResult:
        return predict_category(text)

    def generate_intelligence_report(self, text: str) -> Dict[str, Any]:
        return triage_complaint(text)

    def warmup(self) -> None:
        pass


cra_engine = TriageEngine()
