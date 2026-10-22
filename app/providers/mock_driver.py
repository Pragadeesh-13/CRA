import asyncio
import json
from typing import List, Dict, Any, Callable, Optional
from app.providers.base_driver import BaseLlmDriver


class MockDriver(BaseLlmDriver):
    def __init__(self, provider: str = "mock", **options: Any):
        super().__init__(provider=provider, **options)

    def _determine_response(self, messages: List[Dict[str, str]]) -> str:
        prompt_text = " ".join(m.get("content", "") for m in messages).lower()

        if "title" in prompt_text and "summary" in prompt_text and ("json" in prompt_text or "keys" in prompt_text):
            if "pdf" in prompt_text or "crash" in prompt_text:
                return json.dumps({
                    "title": "Android PDF Export Crash",
                    "summary": "Customer reports that the mobile application crashes with an unhandled 500 error when clicking export PDF on Android."
                })
            return json.dumps({
                "title": "Customer Complaint Resolution Request",
                "summary": "Customer submitted an inquiry regarding account resolution and billing entitlements."
            })

        if "output only a 2-3 word title" in prompt_text or "title:" in prompt_text:
            return "Refund Request"

        if "summarize" in prompt_text:
            return "Complaint regarding delayed delivery. Requesting full refund and expedited handling."

        # Default agent response demonstrating thinking tags and resolution output
        return "<thought>Evaluating complaint details and customer impact.</thought>Your refund request has been accepted and is being processed."

    async def complete(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        **options: Any,
    ) -> str:
        return self._determine_response(messages)

    async def stream(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        on_delta: Optional[Callable[[str], None]] = None,
        on_reasoning_delta: Optional[Callable[[str], None]] = None,
        **options: Any,
    ) -> str:
        response = self._determine_response(messages)
        # Emit in small token chunks
        words = response.split(" ")
        for i, word in enumerate(words):
            chunk = word if i == 0 else " " + word
            if on_delta:
                on_delta(chunk)
        return response

    async def fetch_models(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {"ok": True, "models": ["mock-gpt-4o", "mock-claude-3.5-sonnet"]}
