from abc import ABC, abstractmethod
from typing import List, Dict, Any, Callable, Optional


class BaseLlmDriver(ABC):
    def __init__(self, provider: str = "openrouter", **options):
        self.provider = provider.lower()
        self.options = options

    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        **options: Any,
    ) -> str:
        """Execute a non-streaming chat completion."""
        pass

    @abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        on_delta: Optional[Callable[[str], None]] = None,
        on_reasoning_delta: Optional[Callable[[str], None]] = None,
        **options: Any,
    ) -> str:
        """Stream chat completion token-by-token invoking on_delta for each chunk, and on_reasoning_delta for reasoning/thinking chunks."""
        pass

    @abstractmethod
    async def fetch_models(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch available models catalogue from provider."""
        pass
