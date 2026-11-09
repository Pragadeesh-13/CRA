"""
Product configuration for Troy (open-source complaint-resolution tool).

Defines the product settings a developer can supply — as Python objects,
dicts, JSON, or YAML — so Troy can be pointed at *any* product without
code changes: product name, domain, support email, branding, entity
regexes, categories + SLAs, and policies.

Example:
    from app.core.saas_config import SaaSConfig, load_saas_config

    config = SaaSConfig(product_name="MyApp", domain="example.com")
    config = load_saas_config("config.yaml")          # YAML / JSON file
    config = load_saas_config({"product_name": "DevMetrics"})  # dict
    data = config.to_dict()
    config.save("config.json")

Used by ``troy.load_config`` and by ``TroyClient`` (via ``troy.Troy``).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_ENTITY_PATTERNS: Dict[str, List[str]] = {
    "orders": [r"ORD-\d+", r"order[\s:_#-]*([A-Z0-9-]{4,})"],
    "customers": [r"USR-\d+", r"customer[\s:_#-]*([A-Z0-9-]{4,})"],
    "transactions": [r"TXN-\d+"],
    "invoices": [r"INV-\d+", r"invoice[\s:_#-]*([A-Z0-9-]{4,})"],
    "subscriptions": [r"SUB-\d+", r"subscription[\s:_#-]*([A-Z0-9-]{4,})"],
    "tickets": [r"TCK-\d+", r"ticket[\s:_#-]*([A-Z0-9-]{4,})"],
}


def _default_categories() -> List["CategoryConfig"]:
    return [
        CategoryConfig(name="Refunds", sla_hours=4.0, sla_risk="Low", team="Billing"),
        CategoryConfig(name="Billing", sla_hours=8.0, sla_risk="Low", team="Billing"),
        CategoryConfig(name="Subscriptions", sla_hours=8.0, sla_risk="Low", team="Billing"),
        CategoryConfig(name="Authentication & Access", sla_hours=4.0, sla_risk="Medium", team="Tier 1 Support"),
        CategoryConfig(name="Bugs", sla_hours=24.0, sla_risk="Medium", team="Engineering"),
        CategoryConfig(name="System Performance", sla_hours=24.0, sla_risk="Medium", team="Engineering"),
        CategoryConfig(name="Data Sync", sla_hours=24.0, sla_risk="Medium", team="Engineering"),
        CategoryConfig(name="Integrations & APIs", sla_hours=24.0, sla_risk="Medium", team="Engineering"),
        CategoryConfig(name="Account Management", sla_hours=12.0, sla_risk="Low", team="Tier 1 Support"),
        CategoryConfig(name="Security", sla_hours=4.0, sla_risk="High", team="Security"),
        CategoryConfig(name="Onboarding", sla_hours=16.0, sla_risk="Low", team="Tier 1 Support"),
    ]


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class BrandingConfig(BaseModel):
    """Product branding / identity settings."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str = ""
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    support_name: Optional[str] = None
    tone: Optional[str] = None
    website: Optional[str] = None


# Backwards / forwards compatible alias
Branding = BrandingConfig


class CategoryConfig(BaseModel):
    """
    A custom complaint category with an SLA.

    Accepts ``sla_hours`` as a number, or ``sla`` as a number / dict /
    string (e.g. ``"4h"`` / ``"4 hours"``) for convenience.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    sla_hours: float = 8.0
    sla_risk: str = "Low"
    team: Optional[str] = None
    department: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"name": data}
        if isinstance(data, Mapping):
            data = dict(data)
            # Normalise aliases.
            if "category" in data and "name" not in data:
                data["name"] = data.pop("category")
            if "sla" in data and "sla_hours" not in data:
                sla = data.pop("sla")
                if isinstance(sla, Mapping):
                    data["sla_hours"] = float(sla.get("hours", sla.get("sla_hours", 8.0)))
                    if "risk" in sla:
                        data.setdefault("sla_risk", sla.get("risk"))
                elif isinstance(sla, (int, float)):
                    data["sla_hours"] = float(sla)
                elif isinstance(sla, str):
                    m = re.search(r"([\d.]+)", sla)
                    data["sla_hours"] = float(m.group(1)) if m else 8.0
            if "department" in data and "team" not in data:
                data["team"] = data.get("department")
            return data
        return data

    @property
    def sla(self) -> Dict[str, Any]:
        """SLA summary dict (convenience for callers expecting ``.sla``)."""
        return {"estimated_hours": self.sla_hours, "sla_risk": self.sla_risk}

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


ComplaintCategory = CategoryConfig
Category = CategoryConfig


class PolicyConfig(BaseModel):
    """Auto-remediation policies / guardrails."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    auto_refund_limit: float = 50.0
    # Aliases that mean the same thing in caller-supplied dicts.
    approval_required: bool = False
    require_approval: Optional[bool] = None
    dry_run: bool = False
    approval_threshold: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            data = dict(data)
            if "auto_refund_limit_amount" in data and "auto_refund_limit" not in data:
                data["auto_refund_limit"] = data.pop("auto_refund_limit_amount")
            if "max_auto_refund" in data and "auto_refund_limit" not in data:
                data["auto_refund_limit"] = data.pop("max_auto_refund")
            if "require_approval" in data and "approval_required" not in data:
                data["approval_required"] = data.get("require_approval")
            if "requires_approval" in data and "approval_required" not in data:
                data["approval_required"] = data.pop("requires_approval")
            return data
        return data

    def requires_approval_for(self, amount: float) -> bool:
        """Return True when ``amount`` exceeds the auto-refund limit."""
        try:
            return float(amount) > float(self.auto_refund_limit)
        except (TypeError, ValueError):
            return bool(self.approval_required)


Policies = PolicyConfig
SaaSPolicy = PolicyConfig


# ---------------------------------------------------------------------------
# SaaSConfig
# ---------------------------------------------------------------------------


class SaaSConfig(BaseModel):
    """
    Product configuration for Troy.

    Fields (all optional except where validated):
      - product_name: e.g. ``'MyApp'`` (defaults to ``'Troy Demo SaaS'``)
      - support_email, domain, branding
      - entity_patterns: regex patterns for orders / customers / invoices /
        subscriptions / transactions / tickets
      - categories: custom complaint categories + SLAs
      - policies: auto-refund limit, approval requirements, dry-run mode
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    product_name: str = "Troy Demo SaaS"
    support_email: str = "support@example.com"
    domain: str = "example.com"
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    entity_patterns: Dict[str, List[str]] = Field(default_factory=lambda: dict(DEFAULT_ENTITY_PATTERNS))
    categories: List[CategoryConfig] = Field(default_factory=_default_categories)
    policies: PolicyConfig = Field(default_factory=PolicyConfig)

    # -- Normalisation of caller-supplied shapes ---------------------------

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if data is None:
            return {}
        if isinstance(data, SaaSConfig):
            return data.model_dump()
        if not isinstance(data, Mapping):
            return data
        data = dict(data)

        # Alias: name / product -> product_name
        if "product_name" not in data:
            for alias in ("name", "product", "tenant_name", "tenant"):
                if alias in data and data[alias]:
                    data["product_name"] = data.pop(alias)
                    break
        # Alias: email -> support_email
        if "support_email" not in data and data.get("email"):
            data["support_email"] = data.pop("email")
        # Alias: website -> domain
        if "domain" not in data and data.get("website"):
            data["domain"] = data.pop("website")

        # branding: accept a plain string (the brand name).
        branding = data.get("branding")
        if isinstance(branding, str):
            data["branding"] = {"name": branding}
        elif branding is None and data.get("product_name"):
            data["branding"] = {"name": data.get("product_name")}

        # entity_patterns: accept dict[str, str|list] or a flat list of patterns.
        ep = data.get("entity_patterns")
        if ep is not None:
            if isinstance(ep, Mapping):
                normalised: Dict[str, List[str]] = {}
                for key, value in ep.items():
                    if value is None:
                        normalised[str(key)] = []
                    elif isinstance(value, (list, tuple)):
                        normalised[str(key)] = [str(v) for v in value]
                    else:
                        normalised[str(key)] = [str(value)]
                data["entity_patterns"] = normalised
            elif isinstance(ep, (list, tuple)):
                data["entity_patterns"] = {"orders": [str(v) for v in ep]}

        # categories: accept list[str] / list[dict] / list[CategoryConfig].
        cats = data.get("categories")
        if cats is not None:
            normalised_cats: List[Any] = []
            for item in cats:  # type: ignore[union-attr]
                if isinstance(item, CategoryConfig):
                    normalised_cats.append(item)
                elif isinstance(item, Mapping):
                    normalised_cats.append(dict(item))
                else:
                    normalised_cats.append({"name": str(item)})
            data["categories"] = normalised_cats

        # policies: accept plain dict (PolicyConfig coerces aliases).
        if isinstance(data.get("policies"), Mapping):
            data["policies"] = dict(data["policies"])

        return data

    @field_validator("entity_patterns")
    @classmethod
    def _validate_patterns(cls, value: Dict[str, List[str]]) -> Dict[str, List[str]]:
        for entity, patterns in (value or {}).items():
            for pattern in patterns or []:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"Invalid regex pattern for '{entity}': {pattern!r} ({exc})") from exc
        return value

    @field_validator("product_name")
    @classmethod
    def _non_empty_product(cls, value: str) -> str:
        return (value or "").strip() or "Troy Demo SaaS"

    # -- Construction helpers ----------------------------------------------

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SaaSConfig":
        return cls.model_validate(dict(data))

    @classmethod
    def from_json(cls, text: Union[str, bytes]) -> "SaaSConfig":
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        return cls.model_validate(json.loads(text))

    @classmethod
    def from_yaml(cls, text: str) -> "SaaSConfig":
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - exercised only without PyYAML
            raise RuntimeError(
                "PyYAML is required to load YAML SaaS configs; install with `pip install pyyaml`"
            ) from exc
        data = yaml.safe_load(text) or {}
        if not isinstance(data, Mapping):
            raise ValueError("YAML SaaS config must be a mapping at the top level")
        return cls.model_validate(dict(data))

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "SaaSConfig":
        return load_saas_config(path)

    @classmethod
    def load(cls, source: Any = None, **overrides: Any) -> "SaaSConfig":
        config = load_saas_config(source)
        if overrides:
            merged = {**config.to_dict(), **overrides}
            config = cls.model_validate(merged)
        return config

    # -- Serialisation -------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain (JSON-serialisable) dict representation."""
        return self.model_dump(mode="json")

    def to_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("indent", 2)
        return self.model_dump_json(**kwargs)

    def to_yaml(self) -> str:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PyYAML is required to dump YAML SaaS configs; install with `pip install pyyaml`"
            ) from exc
        return yaml.safe_dump(self.to_dict(), sort_keys=False)

    def save(self, path: Union[str, Path], **kwargs: Any) -> Path:
        """Persist this config to ``path`` (JSON or YAML by extension)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        suffix = target.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            target.write_text(self.to_yaml(), encoding="utf-8")
        else:
            target.write_text(self.to_json(**kwargs), encoding="utf-8")
        return target

    # Aliases commonly expected by callers.
    dump = save
    to_file = save
    dump_to_file = save

    # -- Behaviour helpers ---------------------------------------------------

    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """Extract domain IDs (orders, customers, ...) from free text."""
        if not text:
            return {entity: [] for entity in self.entity_patterns}
        found: Dict[str, List[str]] = {}
        for entity, patterns in self.entity_patterns.items():
            matches: List[str] = []
            for pattern in patterns or []:
                try:
                    compiled = re.compile(pattern, re.IGNORECASE)
                except re.error:
                    continue
                for raw in compiled.findall(text):
                    value = raw[0] if isinstance(raw, tuple) and raw else raw
                    value = str(value).strip()
                    if value and value not in matches:
                        matches.append(value)
            found[entity] = matches
        return found

    def get_category(self, name: Optional[str]) -> Optional[CategoryConfig]:
        if not name:
            return None
        target = str(name).strip().lower()
        for cat in self.categories:
            if cat.name.strip().lower() == target:
                return cat
        return None

    def category_names(self) -> List[str]:
        return [c.name for c in self.categories]

    def get_sla(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        cat = self.get_category(name)
        return cat.sla if cat else None

    def requires_approval_for(self, amount: float) -> bool:
        return self.policies.requires_approval_for(amount)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_saas_config(
    source: Optional[Union[str, Path, Mapping[str, Any], SaaSConfig]] = None,
    **overrides: Any,
) -> SaaSConfig:
    """
    Load a :class:`SaaSConfig` from a dict, JSON/YAML file path, JSON/YAML
    string, existing config, or nothing (defaults).

    Paths are detected by suffix (``.json`` / ``.yaml`` / ``.yml``); strings
    that are not existing files are parsed as JSON (falling back to YAML).
    """
    config: SaaSConfig

    if source is None:
        config = SaaSConfig()
    elif isinstance(source, SaaSConfig):
        config = source
    elif isinstance(source, Mapping):
        config = SaaSConfig.model_validate(dict(source))
    elif isinstance(source, (str, Path)):
        path = Path(source)
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8")
            suffix = path.suffix.lower()
            if suffix == ".json":
                config = SaaSConfig.from_json(text)
            elif suffix in {".yaml", ".yml"}:
                config = SaaSConfig.from_yaml(text)
            else:
                # Sniff content: JSON first, then YAML.
                stripped = text.strip()
                if stripped.startswith("{"):
                    config = SaaSConfig.from_json(text)
                else:
                    config = SaaSConfig.from_yaml(text)
        else:
            # Treat as a JSON / YAML payload string.
            stripped = str(source).strip()
            if stripped.startswith("{"):
                config = SaaSConfig.from_json(stripped)
            else:
                logger.debug("load_saas_config: treating string source as YAML payload")
                config = SaaSConfig.from_yaml(stripped)
    else:
        raise TypeError(
            f"load_saas_config() source must be a dict, path, JSON/YAML string, "
            f"SaaSConfig, or None — got {type(source).__name__}"
        )

    if overrides:
        merged = {**config.to_dict(), **overrides}
        config = SaaSConfig.model_validate(merged)
    return config


__all__ = [
    "SaaSConfig",
    "BrandingConfig",
    "Branding",
    "CategoryConfig",
    "ComplaintCategory",
    "Category",
    "PolicyConfig",
    "Policies",
    "SaaSPolicy",
    "DEFAULT_ENTITY_PATTERNS",
    "load_saas_config",
]
