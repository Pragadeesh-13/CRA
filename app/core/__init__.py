"""Troy plug-and-play SaaS layer (Phase 1).

Provides SaaS-agnostic configuration, data-store adapters, and a custom
tool registry so Troy can be pointed at any SaaS product.
"""

from app.core.saas_config import (
    SaaSConfig,
    BrandingConfig,
    CategoryConfig,
    PolicyConfig,
    load_saas_config,
    DEFAULT_ENTITY_PATTERNS,
)
from app.core.adapter import (
    DataStoreAdapter,
    CsvDataAdapter,
    InMemoryDataAdapter,
    RestApiDataAdapter,
    create_adapter,
)
from app.core.tool_registry import (
    TroyToolRegistry,
    TroyTool,
    validate_params,
)

__all__ = [
    "SaaSConfig",
    "BrandingConfig",
    "CategoryConfig",
    "PolicyConfig",
    "load_saas_config",
    "DEFAULT_ENTITY_PATTERNS",
    "DataStoreAdapter",
    "CsvDataAdapter",
    "InMemoryDataAdapter",
    "RestApiDataAdapter",
    "create_adapter",
    "TroyToolRegistry",
    "TroyTool",
    "validate_params",
]
