"""
Tenant-scoped admin configuration for the Admin-Configured CRA MVP.

Scope (PRODUCT_VARIANT.md): integration identity, field mappings, and
resolution policy (refund cap, approval threshold, escalation target), plus
validation / dry-run that never contacts or mutates an external SaaS.

Security notes
--------------
* Secrets are never stored: configs accept only ``secret_ref`` references
  (``vault:``, ``env:`` or ``secret:`` URIs). Any field that looks like an
  actual credential is rejected by validation.
* Persistence is atomic (temp file + ``os.replace``) under a runtime,
  gitignored, env-configurable directory. Never checked-in sample data.
* Tenants are isolated by a strict slug and per-tenant files; a config's
  embedded ``tenant_id`` must match the requested tenant.

Auth gap (minimum, documented in docs/ADMIN_CONFIG.md)
------------------------------------------------------
The existing auth stack has NO admin-role support: ``SessionGuard`` in
optional mode accepts unauthenticated/guest sessions, and any bearer token
is projected to an ``admin@example.com`` user with no role claim. Login /
Google mint users without roles. Admin endpoints therefore fail closed:
they require an authenticated session AND an explicit ``X-Admin-Token``
matching ``CRA_ADMIN_TOKEN`` (or a future ``role == "admin"`` claim).
Without that credential configured, admin writes/reads return 403.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_INTEGRATIONS = {"stripe", "salesforce", "zendesk", "hubspot", "generic"}
REQUIRED_MAPPINGS = ("customer_id", "order_id", "transaction_id")
OPTIONAL_MAPPINGS = ("customer_email", "subscription_id", "invoice_id", "ticket_id")
_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_SECRET_REF_RE = re.compile(r"^(vault|env|secret):[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}$")
_SECRET_VALUE_RES = [
    re.compile(r"^sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"^(ghp|gho|github_pat)_[A-Za-z0-9_]{8,}"),
    re.compile(r"^xox[bpas]-[A-Za-z0-9-]{8,}"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
_FORBIDDEN_KEYS = {
    "apikey", "api_key", "secret", "password", "passwd", "accesstoken",
    "access_token", "refreshtoken", "refresh_token", "privatekey",
    "private_key", "clientsecret", "client_secret", "authtoken", "auth_token",
}

_LOCK = threading.Lock()


def is_valid_tenant_id(tenant_id: Any) -> bool:
    return isinstance(tenant_id, str) and bool(_TENANT_RE.match(tenant_id))


def _norm_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def _scan_for_secrets(node: Any, path: str, errors: List[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            nk = _norm_key(str(k))
            if nk == "secret_ref":
                if not (isinstance(v, str) and _SECRET_REF_RE.match(v.strip())):
                    errors.append(f"{path}.{k}: secret_ref must be a vault:/env:/secret: reference")
                continue
            if nk in _FORBIDDEN_KEYS or nk.endswith("_secret") and nk != "secret_ref":
                errors.append(f"{path}.{k}: secrets must be stored as secret_ref references, not values")
                continue
            if isinstance(v, str) and any(r.match(v.strip()) for r in _SECRET_VALUE_RES):
                errors.append(f"{path}.{k}: value looks like a real credential; use secret_ref instead")
            _scan_for_secrets(v, f"{path}.{k}", errors)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _scan_for_secrets(item, f"{path}[{i}]", errors)


def validate_config(config: Any, expected_tenant: Optional[str] = None) -> List[str]:
    """Return a list of validation errors (empty == valid). Pure, no I/O."""
    errors: List[str] = []
    if not isinstance(config, dict):
        return ["config must be a JSON object"]

    _scan_for_secrets(config, "config", errors)

    tenant = config.get("tenant_id")
    if not is_valid_tenant_id(tenant):
        errors.append("tenant_id: required slug matching ^[a-z0-9][a-z0-9_-]{1,63}$")
    elif expected_tenant and tenant != expected_tenant:
        errors.append("tenant_id: does not match requested tenant")

    integration = config.get("integration")
    if not isinstance(integration, dict):
        errors.append("integration: required object with name, external_tenant_id, secret_ref")
    else:
        name = str(integration.get("name") or "").strip().lower()
        if name not in SUPPORTED_INTEGRATIONS:
            errors.append(f"integration.name: must be one of {sorted(SUPPORTED_INTEGRATIONS)}")
        ext = str(integration.get("external_tenant_id") or "").strip()
        if not ext or len(ext) > 128 or any(c.isspace() for c in ext):
            errors.append("integration.external_tenant_id: required, no whitespace, max 128 chars")
        ref = str(integration.get("secret_ref") or "").strip()
        if not _SECRET_REF_RE.match(ref):
            errors.append("integration.secret_ref: required vault:/env:/secret: reference (no raw secrets)")

    mappings = config.get("field_mappings")
    if not isinstance(mappings, dict):
        errors.append("field_mappings: required object")
    else:
        seen: Dict[str, str] = {}
        for logical in list(REQUIRED_MAPPINGS) + list(OPTIONAL_MAPPINGS):
            if logical not in mappings:
                if logical in REQUIRED_MAPPINGS:
                    errors.append(f"field_mappings.{logical}: required mapping")
                continue
            target = mappings[logical]
            if not isinstance(target, str) or not target.strip() or len(target.strip()) > 128:
                errors.append(f"field_mappings.{logical}: must be a non-empty string (max 128 chars)")
                continue
            target = target.strip()
            if target in seen:
                errors.append(f"field_mappings.{logical}: maps to same external field as {seen[target]}")
            else:
                seen[target] = logical
        for k in mappings:
            if k not in REQUIRED_MAPPINGS + OPTIONAL_MAPPINGS:
                errors.append(f"field_mappings.{k}: unknown logical field")

    policy = config.get("resolution_policy")
    if not isinstance(policy, dict):
        errors.append("resolution_policy: required object")
    else:
        cap = policy.get("refund_cap_cents")
        thresh = policy.get("approval_threshold_cents")
        if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
            errors.append("resolution_policy.refund_cap_cents: required positive integer")
        if not isinstance(thresh, int) or isinstance(thresh, bool) or thresh < 0:
            errors.append("resolution_policy.approval_threshold_cents: required non-negative integer")
        elif isinstance(cap, int) and not isinstance(cap, bool) and thresh > cap:
            errors.append("resolution_policy.approval_threshold_cents: must be <= refund_cap_cents")
        esc = str(policy.get("escalation_target") or "").strip()
        if not esc or len(esc) > 128:
            errors.append("resolution_policy.escalation_target: required non-empty string (max 128 chars)")
        if "allow_auto_refund" in policy and not isinstance(policy["allow_auto_refund"], bool):
            errors.append("resolution_policy.allow_auto_refund: must be boolean if present")
    return errors


def dry_run(config: Any, expected_tenant: Optional[str] = None) -> Dict[str, Any]:
    """Validate mappings/policy without contacting or mutating external SaaS."""
    errors = validate_config(config, expected_tenant=expected_tenant)
    checks = [
        {"name": "tenant_isolation", "ok": is_valid_tenant_id(config.get("tenant_id")) if isinstance(config, dict) else False,
         "detail": "tenant_id slug present and matches requested tenant" if not errors else "see errors"},
        {"name": "integration_identity", "ok": bool(isinstance(config, dict) and isinstance(config.get("integration"), dict))},
        {"name": "secret_reference_only", "ok": not any("secret" in e.lower() or "credential" in e.lower() for e in errors)},
        {"name": "field_mappings", "ok": not any(e.startswith("field_mappings") for e in errors)},
        {"name": "resolution_policy", "ok": not any(e.startswith("resolution_policy") for e in errors)},
    ]
    return {
        "ok": not errors,
        "valid": not errors,
        "errors": errors,
        "checks": checks,
        "external_contact": False,
        "mutated_external": False,
        "persisted": False,
    }


KNOWN_POLICY_ACTIONS = {"refund", "duplicate_refund", "remediate_duplicate_charge"}


def evaluate_policy(
    config: Any,
    *,
    tenant_id: Any,
    action: Any,
    amount_cents: Any,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Pure policy decision (no I/O, no external contact, no mutation).

    Enforced simulated policy only: ``allowed`` means the *simulated* action
    would be permitted; live/Stripe execution is never performed here.
    Fail-closed (deny) on missing/mismatched/invalid config, unknown
    action/amount, and config errors.
    """
    base: Dict[str, Any] = {
        "tenant_id": tenant_id if isinstance(tenant_id, str) else None,
        "action": action if isinstance(action, str) else None,
        "amount_cents": amount_cents if isinstance(amount_cents, int) and not isinstance(amount_cents, bool) else None,
        "dry_run": bool(dry_run),
        "allowed": False,
        "decision": "deny",
        "status": "deny",
        "rationale": "",
        "refund_cap_cents": None,
        "approval_threshold_cents": None,
        "escalation_target": None,
        "external_contact": False,
        "mutated_external": False,
        "mutated_customer_data": False,
    }

    def _deny(rationale: str, escalation: Optional[str] = None) -> Dict[str, Any]:
        base["rationale"] = rationale
        if escalation:
            base["escalation_target"] = escalation
            base["escalated_to"] = escalation
        return base

    if not is_valid_tenant_id(tenant_id):
        return _deny("missing or invalid tenant_id")
    if config is None:
        return _deny("missing tenant config")
    if not isinstance(config, dict):
        return _deny("config error: config must be an object")
    errors = validate_config(config, expected_tenant=tenant_id)
    if errors:
        return _deny(f"config error: {errors[0]}")
    policy = config["resolution_policy"]
    cap = policy["refund_cap_cents"]
    thresh = policy["approval_threshold_cents"]
    esc = str(policy["escalation_target"]).strip()
    base["refund_cap_cents"] = cap
    base["approval_threshold_cents"] = thresh
    base["escalation_target"] = esc
    if action not in KNOWN_POLICY_ACTIONS:
        return _deny(f"unknown action: {action!r}", escalation=esc)
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents < 0:
        return _deny("unknown amount: amount_cents must be a non-negative integer", escalation=esc)
    if dry_run:
        # Dry-run reports what would happen without authorizing execution.
        if amount_cents > cap:
            base["decision"] = base["status"] = "dry_run"
            base["rationale"] = f"dry_run: amount {amount_cents} exceeds refund cap {cap}; would deny and escalate to {esc}"
            base["escalated_to"] = esc
        elif amount_cents > thresh:
            base["decision"] = base["status"] = "dry_run"
            base["rationale"] = f"dry_run: amount {amount_cents} exceeds approval threshold {thresh}; would require approval from {esc}"
            base["escalated_to"] = esc
        else:
            base["decision"] = base["status"] = "dry_run"
            base["rationale"] = f"dry_run: amount {amount_cents} within cap {cap} and approval threshold {thresh}; would allow (simulated only, no execution)"
        return base
    if amount_cents > cap:
        return _deny(f"amount {amount_cents} exceeds refund cap {cap}", escalation=esc)
    if amount_cents > thresh:
        base["decision"] = base["status"] = "require_approval"
        base["rationale"] = f"amount {amount_cents} exceeds approval threshold {thresh}; requires approval from {esc}"
        base["escalated_to"] = esc
        return base
    base["allowed"] = True
    base["decision"] = base["status"] = "allow"
    base["rationale"] = (
        f"amount {amount_cents} within refund cap {cap} and approval threshold {thresh}; "
        "allowed as simulated policy only (no Stripe/live execution, no customer-data mutation)"
    )
    return base


def load_policy_for_tenant(store: "AdminConfigStore", tenant_id: Any) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Load + validate tenant config; returns (config|None, errors)."""
    if not is_valid_tenant_id(tenant_id):
        return None, ["missing or invalid tenant_id"]
    cfg = store.load(tenant_id)
    if cfg is None:
        return None, ["missing tenant config"]
    errors = validate_config(cfg, expected_tenant=tenant_id)
    return (None, errors) if errors else (cfg, [])


def default_config_dir() -> Path:
    raw = os.getenv("CRA_ADMIN_CONFIG_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent / "runtime" / "admin_configs"


class AdminConfigStore:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir else default_config_dir()

    def path_for(self, tenant_id: str) -> Path:
        if not is_valid_tenant_id(tenant_id):
            raise ValueError("invalid tenant_id")
        return self.base_dir / f"{tenant_id}.json"

    def load(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        path = self.path_for(tenant_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("tenant_id") != tenant_id:
            return None
        return data

    def save(self, config: Dict[str, Any]) -> Dict[str, Any]:
        errors = validate_config(config)
        if errors:
            raise ValueError("; ".join(errors))
        tenant = config["tenant_id"]
        path = self.path_for(tenant)
        with _LOCK:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.base_dir / f".{tenant}.tmp"
            payload = json.dumps(config, indent=2, sort_keys=True) + "\n"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        return config
