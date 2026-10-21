from typing import Dict, Type, Optional
from app.providers.base_driver import BaseLlmDriver
from app.providers.openai_driver import OpenAiDriver
from app.providers.anthropic_driver import AnthropicDriver
from app.providers.mock_driver import MockDriver

PROVIDER_REGISTRY: Dict[str, Type[BaseLlmDriver]] = {
    "openrouter": OpenAiDriver,
    "openai": OpenAiDriver,
    "groq": OpenAiDriver,
    "anthropic": AnthropicDriver,
    "lmstudio": OpenAiDriver,
    "mock": MockDriver,
}

PROVIDER_BASE_URLS: Dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "anthropic": "https://api.anthropic.com",
    "lmstudio": "http://localhost:1235/v1",
}


def provider_base_url(provider_name: str) -> Optional[str]:
    key = (provider_name or "").strip().lower()
    return PROVIDER_BASE_URLS.get(key)


def create_driver(provider_name: str, **options) -> BaseLlmDriver:
    norm = (provider_name or "openrouter").strip().lower()
    driver_cls = PROVIDER_REGISTRY.get(norm)
    if not driver_cls:
        raise ValueError(
            f"Unsupported provider '{provider_name}'. Supported: {', '.join(PROVIDER_REGISTRY.keys())}"
        )
    clean_opts = dict(options)
    clean_opts.pop("provider", None)
    return driver_cls(provider=norm, **clean_opts)


__all__ = [
    "BaseLlmDriver",
    "OpenAiDriver",
    "AnthropicDriver",
    "MockDriver",
    "PROVIDER_REGISTRY",
    "PROVIDER_BASE_URLS",
    "provider_base_url",
    "create_driver",
]
