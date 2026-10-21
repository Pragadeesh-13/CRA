import json
import logging
from typing import List, Dict, Any, Callable, Optional
import httpx
from app.providers.base_driver import BaseLlmDriver

logger = logging.getLogger("provider:anthropic")

ANTHROPIC_MODELS = [
    "claude-3-5-sonnet-20241022",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
]


def format_anthropic_payload(
    messages: List[Dict[str, str]],
    max_tokens: int = 4096,
    model: str = "claude-3-5-sonnet-20241022",
) -> Dict[str, Any]:
    system_prompt = ""
    anthropic_messages = []

    for m in messages or []:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            system_prompt += ("\n\n" if system_prompt else "") + content
        else:
            anthropic_messages.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": content,
            })

    if not anthropic_messages:
        anthropic_messages.append({"role": "user", "content": "Continue"})

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max(max_tokens or 4096, 4096),
        "messages": anthropic_messages,
    }
    if system_prompt:
        payload["system"] = system_prompt

    return payload


class AnthropicDriver(BaseLlmDriver):
    def __init__(
        self,
        provider: str = "anthropic",
        model: str = "claude-3-5-sonnet-20241022",
        api_key: str = "",
        base_url: str = "https://api.anthropic.com",
        timeout_ms: int = 60000,
        stream_idle_timeout_ms: int = 90000,
        **options: Any,
    ):
        super().__init__(provider=provider, **options)
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.timeout_sec = timeout_ms / 1000.0
        self.stream_idle_timeout_sec = stream_idle_timeout_ms / 1000.0

    def _build_headers(self, api_key_override: Optional[str] = None) -> Dict[str, str]:
        key = api_key_override if api_key_override is not None else self.api_key
        return {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }

    async def complete(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        **options: Any,
    ) -> str:
        endpoint = f"{self.base_url}/v1/messages"
        headers = self._build_headers(options.get("apiKey"))
        payload = format_anthropic_payload(
            messages,
            max_tokens=max_tokens,
            model=options.get("model") or self.model,
        )

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            res = await client.post(endpoint, headers=headers, json=payload)
            res.raise_for_status()
            data = res.json()
            blocks = data.get("content", [])
            if blocks and isinstance(blocks, list):
                return blocks[0].get("text", "").strip()
            return ""

    async def stream(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        on_delta: Optional[Callable[[str], None]] = None,
        on_reasoning_delta: Optional[Callable[[str], None]] = None,
        **options: Any,
    ) -> str:
        endpoint = f"{self.base_url}/v1/messages"
        headers = self._build_headers(options.get("apiKey"))
        payload = format_anthropic_payload(
            messages,
            max_tokens=max_tokens,
            model=options.get("model") or self.model,
        )
        payload["stream"] = True

        full_text = ""
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_sec, read=self.stream_idle_timeout_sec)) as client:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as res:
                res.raise_for_status()
                async for line in res.aiter_lines():
                    if not line:
                        continue
                    line_str = line.strip()
                    if not line_str.startswith("data:"):
                        continue
                    data_part = line_str[5:].strip()
                    try:
                        parsed = json.loads(data_part)
                        event_type = parsed.get("type")
                        if event_type == "content_block_delta":
                            delta = parsed.get("delta", {})
                            if delta.get("type") == "text_delta":
                                chunk = delta.get("text", "")
                                full_text += chunk
                                if on_delta:
                                    on_delta(chunk)
                    except Exception:
                        continue
        return full_text

    async def fetch_models(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {"ok": True, "models": list(ANTHROPIC_MODELS)}
