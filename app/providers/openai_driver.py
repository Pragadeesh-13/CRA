import json
import logging
import re
from typing import List, Dict, Any, Callable, Optional, Union
import httpx
from app.providers.base_driver import BaseLlmDriver
from app.utils.thought_buffer import ThoughtBuffer

logger = logging.getLogger("provider:openai")

CURATED_MODELS: Dict[str, List[str]] = {
    "openrouter": [
        "openai/gpt-6.1-sol-pro",
        "google/gemini-3.8-flash",
        "anthropic/claude-sonnet-5-5",
        "deepseek/deepseek-r1",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "anthropic/claude-3.5-sonnet",
        "google/gemini-2.5-flash",
        "meta-llama/llama-3.3-70b-instruct",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "o1-preview",
        "o1-mini",
        "o3-mini",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
    ],
    "lmstudio": [
        "qwen/qwen3.5-9b",
        "google/gemma-4-e4b",
        "google/gemma-4-12b-qat",
    ],
}


def normalize_base_url(url: str) -> str:
    cleaned = (url or "").rstrip("/")
    if cleaned.lower().endswith("/v1"):
        cleaned = cleaned[:-3].rstrip("/")
    return cleaned


def is_thinking_unsupported_error(error_msg: str) -> bool:
    msg = error_msg.lower()
    return any(
        phrase in msg
        for phrase in [
            "does not support thinking",
            "thinking is not supported",
            "unsupported parameter: 'think'",
            "unrecognized request argument: think",
            "thinking parameter",
        ]
    )


def resolve_think_setting(model: str = "", think: Union[str, bool] = "auto") -> Optional[Union[bool, str]]:
    if think is False or think == "false":
        return False
    if think is True or think == "true":
        return True
    if think in ("low", "medium", "high"):
        return think

    lower = (model or "").lower()
    if "gpt-oss" in lower:
        return "low"
    if any(k in lower for k in ("qwen3", "deepseek", "r1")):
        return True
    return None


class OpenAiDriver(BaseLlmDriver):
    def __init__(
        self,
        provider: str = "openrouter",
        model: str = "openai/gpt-6.1-sol-pro",
        api_key: str = "",
        base_url: str = "",
        think: Union[str, bool] = "auto",
        public_url: str = "http://localhost:3000",
        timeout_ms: int = 60000,
        stream_idle_timeout_ms: int = 90000,
        **options: Any,
    ):
        super().__init__(provider=provider, **options)
        self.model = model
        self.api_key = api_key
        self.base_url = base_url or self._default_base_url()
        self.think = think
        self.public_url = public_url
        self.timeout_sec = timeout_ms / 1000.0
        self.stream_idle_timeout_sec = stream_idle_timeout_ms / 1000.0

    def _default_base_url(self) -> str:
        if self.provider == "openai":
            return "https://api.openai.com/v1"
        if self.provider == "groq":
            return "https://api.groq.com/openai/v1"
        if self.provider == "lmstudio":
            return "http://localhost:1235/v1"
        return "https://openrouter.ai/api/v1"

    def _build_headers(self, api_key_override: Optional[str] = None) -> Dict[str, str]:
        key = api_key_override if api_key_override is not None else self.api_key
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif self.provider == "lmstudio":
            headers["Authorization"] = "Bearer lm-studio"
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = self.public_url
            headers["X-Title"] = "Complaint Resolution Agent"
        return headers

    def _build_request_body(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        options: Dict[str, Any],
        include_think: bool = True,
        stream: bool = False,
    ) -> Dict[str, Any]:
        target_model = options.get("model") or self.model
        body: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": options.get("temperature", 0.7),
        }
        if stream:
            body["stream"] = True

        effective_max_tokens = max(max_tokens or 4096, 4096)

        if options.get("useMaxCompletionTokens"):
            body["max_completion_tokens"] = effective_max_tokens
        else:
            body["max_tokens"] = effective_max_tokens

        if include_think and self.provider != "lmstudio":
            think_val = resolve_think_setting(
                target_model,
                options.get("think", self.think),
            )
            if think_val is not None:
                body["think"] = think_val

        return body

    async def complete(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        **options: Any,
    ) -> str:
        base = normalize_base_url(options.get("baseUrl") or self.base_url)
        endpoint = f"{base}/v1/chat/completions"
        headers = self._build_headers(options.get("apiKey"))

        async def _make_call(include_think: bool, use_max_completion: bool) -> str:
            opts = dict(options)
            if use_max_completion:
                opts["useMaxCompletionTokens"] = True
            body = self._build_request_body(messages, max_tokens, opts, include_think=include_think, stream=False)

            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                res = await client.post(endpoint, headers=headers, json=body)
                if res.status_code == 400:
                    err_txt = res.text
                    if include_think and is_thinking_unsupported_error(err_txt):
                        logger.info("Retrying without think parameter")
                        return await _make_call(include_think=False, use_max_completion=use_max_completion)
                    if not use_max_completion and "max_completion_tokens" in err_txt:
                        return await _make_call(include_think=include_think, use_max_completion=True)
                res.raise_for_status()
                data = res.json()
                choice_msg = data.get("choices", [{}])[0].get("message", {})
                content = (choice_msg.get("content") or "").strip()
                if not content and choice_msg.get("reasoning_content"):
                    import re
                    reasoning = choice_msg.get("reasoning_content", "").strip()
                    draft_match = re.findall(r"(?:Draft\s*\d*|Final|Response|Output|Summary):\s*([^\n]+(?:\n[^\n]+)?)", reasoning, re.IGNORECASE)
                    if draft_match:
                        content = draft_match[-1].strip()
                    else:
                        lines = [ln.strip() for ln in reasoning.split("\n") if ln.strip() and not ln.strip().startswith(("*", "#", "Thinking", "1.", "2.", "3.", "4.", "5."))]
                        content = lines[-1] if lines else reasoning[:200]
                return content

        return await _make_call(include_think=True, use_max_completion=False)

    async def stream(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        on_delta: Optional[Callable[[str], None]] = None,
        on_reasoning_delta: Optional[Callable[[str], None]] = None,
        **options: Any,
    ) -> str:
        base = normalize_base_url(options.get("baseUrl") or self.base_url)
        endpoint = f"{base}/v1/chat/completions"
        headers = self._build_headers(options.get("apiKey"))

        async def _stream_call(include_think: bool, use_max_completion: bool) -> str:
            opts = dict(options)
            if use_max_completion:
                opts["useMaxCompletionTokens"] = True
            body = self._build_request_body(messages, max_tokens, opts, include_think=include_think, stream=True)

            reasoning_text = ""
            tb = ThoughtBuffer(
                on_delta=lambda full, chunk: on_delta(chunk) if on_delta else None,
                on_reasoning_delta=lambda full, chunk: on_reasoning_delta(chunk) if on_reasoning_delta else None,
            )

            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_sec, read=self.stream_idle_timeout_sec)) as client:
                async with client.stream("POST", endpoint, headers=headers, json=body) as res:
                    if res.status_code == 400:
                        err_bytes = await res.aread()
                        err_txt = err_bytes.decode("utf-8", errors="ignore")
                        if include_think and is_thinking_unsupported_error(err_txt):
                            return await _stream_call(include_think=False, use_max_completion=use_max_completion)
                        if not use_max_completion and "max_completion_tokens" in err_txt:
                            return await _stream_call(include_think=include_think, use_max_completion=True)
                    res.raise_for_status()

                    async for line in res.aiter_lines():
                        if not line:
                            continue
                        line_str = line.strip()
                        if not line_str.startswith("data:"):
                            continue
                        data_part = line_str[5:].strip()
                        if data_part == "[DONE]":
                            break
                        try:
                            parsed = json.loads(data_part)
                            delta = parsed.get("choices", [{}])[0].get("delta", {})

                            # 1. Native reasoning tokens (LM Studio, DeepSeek R1, OpenAI o1/o3)
                            r_chunk = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            if r_chunk:
                                reasoning_text += r_chunk
                                if on_reasoning_delta:
                                    on_reasoning_delta(r_chunk)

                            # 2. Content tokens (handled robustly by ThoughtBuffer)
                            c_chunk = delta.get("content") or ""
                            if c_chunk:
                                tb.feed(c_chunk)
                        except Exception:
                            continue

            tb.flush()
            full_text = tb.public_buffer
            if tb.reasoning_buffer:
                reasoning_text += tb.reasoning_buffer

            if not full_text.strip() and reasoning_text.strip():
                draft_match = re.findall(r"(?:Draft\s*\d*|Final|Response|Output|Summary):\s*([^\n]+(?:\n[^\n]+)?)", reasoning_text, re.IGNORECASE)
                fallback = draft_match[-1].strip() if draft_match else ""
                if fallback:
                    full_text = fallback
                    if on_delta:
                        on_delta(full_text)

            return full_text

        return await _stream_call(include_think=True, use_max_completion=False)

    async def fetch_models(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        base = normalize_base_url(base_url or self.base_url)
        endpoint = f"{base}/v1/models"
        headers = self._build_headers(api_key)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(endpoint, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    models_list = [m.get("id") for m in data.get("data", []) if m.get("id")]
                    if models_list:
                        curated = CURATED_MODELS.get(self.provider, [])
                        pinned = [m for m in curated if m in models_list]
                        remaining = [m for m in models_list if m not in pinned]
                        combined = (pinned or curated) + remaining[:40]
                        return {"ok": True, "models": combined}
        except Exception as e:
            logger.warning(f"Failed to fetch live models for {self.provider}: {e}")

        fallback = CURATED_MODELS.get(self.provider, [])
        return {"ok": True, "models": fallback}
