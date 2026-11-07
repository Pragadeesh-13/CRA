# Milestone 3 Explorer 1 Analysis: Local LLM Provider & Mock Fallback

## Executive Summary
This analysis details the architectural findings, implementation blueprint, and code modifications for **Feature 9 (LM Studio Provider Default & Config)** and **Feature 11 (Local LLM Client & Mock Fallback)**.
Troy is transitioned from the legacy ML engine to a local LLM resolution engine powered by an OpenAI-compatible endpoint (LM Studio default `http://localhost:1234/v1`, port 1234, configurable via `LMSTUDIO_BASE_URL`).
A 3-level transparent fallback mechanism ensures that when LM Studio is offline, unreachable, or times out, the system automatically falls back to deterministic execution via `MockDriver`, streaming token-by-token reasoning and public deltas via SSE without throwing unhandled exceptions or 500 errors.

---

## 1. Feature 9: LM Studio Provider Default & Config

### 1.1 Current Baseline Deficiencies
1. **Hardcoded Port 1235**:
   - In `app/providers/__init__.py:21`: `PROVIDER_BASE_URLS["lmstudio"] = "http://localhost:1235/v1"`.
   - In `app/providers/openai_driver.py:109`: `_default_base_url()` returns `"http://localhost:1235/v1"`.
   - Standard LM Studio port is `1234`. The requirements (ORIGINAL_REQUEST.md R2, PROJECT.md Feature 9, and `tests/test_e2e_troy.py:1005`) strictly mandate `http://localhost:1234/v1`.
2. **Missing `LMSTUDIO_BASE_URL` Environment Variable Support**:
   - `provider_base_url()` in `app/providers/__init__.py:25-27` directly queries the static dictionary `PROVIDER_BASE_URLS.get(key)`. It completely ignores `os.getenv("LMSTUDIO_BASE_URL")`.
   - `test_tier1_feature9_lmstudio_base_url_env_override` (`monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://192.168.1.150:1234/v1")`) fails against the current implementation.
   - `test_tier2_feature9_empty_env_var_falls_back_to_default` requires empty env var `""` to cleanly fall back to `http://localhost:1234/v1`.
3. **Application Settings Provider Default**:
   - In `app/config.py:18`: `llm_provider` defaults to `os.getenv("LLM_PROVIDER", "openrouter")`.
   - In `app/providers/__init__.py:31`: `create_driver()` defaults to `"openrouter"`.
   - In Milestone 3, the default provider should be `"lmstudio"` so Troy runs local-first without requiring cloud API keys.

### 1.2 URL Normalization & Resolution Contract
- **Function**: `normalize_base_url(url: str) -> str` in `app/providers/openai_driver.py:44-48`:
  - Strips trailing slashes: `http://localhost:1234/v1/` -> `http://localhost:1234`
  - Strips `/v1` suffix: `http://localhost:1234/v1` -> `http://localhost:1234`
  - Endpoint path is constructed as: `f"{base}/v1/chat/completions"`.
  - Base without `/v1` (`http://localhost:1234`) remains `http://localhost:1234`.
  - Case insensitivity: `provider_base_url("LMSTUDIO")` and `provider_base_url("LmStudio")` both resolve identically.

### 1.3 Proposed Changes for Feature 9

#### A. `app/providers/__init__.py`
```python
import os
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
    "lmstudio": "http://localhost:1234/v1",
}


def provider_base_url(provider_name: str) -> Optional[str]:
    key = (provider_name or "").strip().lower()
    if key == "lmstudio":
        env_url = (os.getenv("LMSTUDIO_BASE_URL") or "").strip()
        return env_url if env_url else "http://localhost:1234/v1"
    return PROVIDER_BASE_URLS.get(key)


def create_driver(provider_name: str, **options) -> BaseLlmDriver:
    norm = (provider_name or "lmstudio").strip().lower()
    driver_cls = PROVIDER_REGISTRY.get(norm)
    if not driver_cls:
        raise ValueError(
            f"Unsupported provider '{provider_name}'. Supported: {', '.join(PROVIDER_REGISTRY.keys())}"
        )
    clean_opts = dict(options)
    clean_opts.pop("provider", None)
    return driver_cls(provider=norm, **clean_opts)
```

#### B. `app/providers/openai_driver.py`
```python
    def _default_base_url(self) -> str:
        if self.provider == "openai":
            return "https://api.openai.com/v1"
        if self.provider == "groq":
            return "https://api.groq.com/openai/v1"
        if self.provider == "lmstudio":
            env_url = (os.getenv("LMSTUDIO_BASE_URL") or "").strip()
            return env_url if env_url else "http://localhost:1234/v1"
        return "https://openrouter.ai/api/v1"
```

#### C. `app/config.py`
```python
    llm_provider: str = Field(default_factory=lambda: os.getenv("LLM_PROVIDER", "lmstudio").lower())
    llm_model: str = Field(default_factory=lambda: os.getenv("LLM_MODEL", "qwen/qwen3.5-9b"))
    lmstudio_base_url: str = Field(
        default_factory=lambda: os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
    )
```

---

## 2. Feature 11: Local LLM Client & Mock Fallback

### 2.1 Current Baseline Deficiencies
1. **Unhandled HTTP Network Errors in `OpenAiDriver`**:
   - `OpenAiDriver.complete` and `OpenAiDriver.stream` make raw HTTP calls via `httpx.AsyncClient`.
   - If LM Studio is offline (port 1234 refused), `httpx.ConnectError` is raised.
   - In `complete`, the exception is unhandled and crashes the caller.
   - In `stream`, `execute_agent_with_reasoning` catches the error, sets `fallback = "Signal lost. Unable to reach model provider."`, and broadcasts `agent` with "Signal lost", disrupting the SSE stream.
2. **Missing Transparent Fallback in Driver**:
   - Project contract requires: "When HTTP connection fails or timeout occurs, transparently switch to `MockDriver` or deterministic streaming fallback without throwing unhandled exceptions."
   - The driver should swallow unreachable server errors and immediately produce deterministic output via `MockDriver`.
3. **`MockDriver` Response Specificity**:
   - `MockDriver._determine_response()` only provided a single generic fallback string (`"Your refund request has been accepted..."`) for agent responses.
   - It did not recognize individual section requests (Section 1: Executive Assessment, Section 2: Root Cause, Section 3: Customer Communication, Section 4: Internal Routing) or specific inquiry categories (e.g. 2FA, Technical Support vs Billing).

### 2.2 Transparent Fallback Architecture

```
[ Troy Simulation / API ]
           │
           ▼
[ OpenAiDriver (provider="lmstudio", base_url="http://localhost:1234/v1") ]
           │
      Is LM Studio
      reachable?
     ├── Yes ──► Send httpx POST /v1/chat/completions (Stream / Complete)
     │
     └── No (ConnectError / Timeout / 5xx) ──► Transparent Switch
                                                     │
                                                     ▼
                                       [ MockDriver (Deterministic Fallback) ]
                                                     │
                                        Emits Token Chunks via on_delta
                                        Parses <thought> Reasoning via ThoughtBuffer
                                        Broadcasts SSE agent_delta & agent
```

### 2.3 Proposed Changes for Feature 11

#### A. Transparent Fallback in `app/providers/openai_driver.py`
Add `self._fallback_driver = MockDriver()` and `self._offline_detected = False`.
On any connection or timeout exception:
1. Set `self._offline_detected = True`.
2. Delegate execution to `self._fallback_driver.complete()` or `self._fallback_driver.stream()`.
3. Ensure subsequent calls in the same resolution session immediately use `_fallback_driver` without repeating multi-second network timeouts.

```python
    def __init__(
        self,
        provider: str = "lmstudio",
        model: str = "qwen/qwen3.5-9b",
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
        self._fallback_driver = MockDriver()
        self._offline_detected = False

    async def complete(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        **options: Any,
    ) -> str:
        if self._offline_detected:
            return await self._fallback_driver.complete(messages, max_tokens, **options)

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

        try:
            return await _make_call(include_think=True, use_max_completion=False)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError, httpx.HTTPError) as e:
            logger.warning("Local server at %s unreachable (%s). Seamlessly falling back to MockDriver.", endpoint, e)
            self._offline_detected = True
            return await self._fallback_driver.complete(messages, max_tokens, **options)
        except Exception as e:
            logger.error("OpenAiDriver encountered unexpected error (%s). Falling back to MockDriver.", e)
            self._offline_detected = True
            return await self._fallback_driver.complete(messages, max_tokens, **options)

    async def stream(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 4096,
        on_delta: Optional[Callable[[str], None]] = None,
        on_reasoning_delta: Optional[Callable[[str], None]] = None,
        **options: Any,
    ) -> str:
        if self._offline_detected:
            return await self._fallback_driver.stream(
                messages, max_tokens, on_delta=on_delta, on_reasoning_delta=on_reasoning_delta, **options
            )

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

                            r_chunk = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            if r_chunk:
                                reasoning_text += r_chunk
                                if on_reasoning_delta:
                                    on_reasoning_delta(r_chunk)

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
                import re
                draft_match = re.findall(r"(?:Draft\s*\d*|Final|Response|Output|Summary):\s*([^\n]+(?:\n[^\n]+)?)", reasoning_text, re.IGNORECASE)
                fallback = draft_match[-1].strip() if draft_match else ""
                if fallback:
                    full_text = fallback
                    if on_delta:
                        on_delta(full_text)

            return full_text

        try:
            return await _stream_call(include_think=True, use_max_completion=False)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError, httpx.HTTPError) as e:
            logger.warning("Local server stream at %s unreachable (%s). Seamlessly falling back to MockDriver.", endpoint, e)
            self._offline_detected = True
            return await self._fallback_driver.stream(
                messages, max_tokens, on_delta=on_delta, on_reasoning_delta=on_reasoning_delta, **options
            )
        except Exception as e:
            logger.error("OpenAiDriver stream encountered unexpected error (%s). Falling back to MockDriver.", e)
            self._offline_detected = True
            return await self._fallback_driver.stream(
                messages, max_tokens, on_delta=on_delta, on_reasoning_delta=on_reasoning_delta, **options
            )
```

#### B. Enhanced `app/providers/mock_driver.py`
Enrich `MockDriver` to recognize Troy resolution prompts and categories while retaining 100% compatibility with all Feature 11 tests:

```python
import asyncio
import json
import re
from typing import List, Dict, Any, Callable, Optional
from app.providers.base_driver import BaseLlmDriver


class MockDriver(BaseLlmDriver):
    def __init__(self, provider: str = "mock", **options: Any):
        super().__init__(provider=provider, **options)

    def _determine_response(self, messages: List[Dict[str, str]]) -> str:
        prompt_text = " ".join(m.get("content", "") for m in messages).lower()

        # 1. Title and summary extraction JSON (summarise_ticket)
        if "title" in prompt_text and "summary" in prompt_text and ("json" in prompt_text or "keys" in prompt_text):
            if "pdf" in prompt_text or "crash" in prompt_text:
                return json.dumps({
                    "title": "Android PDF Export Crash",
                    "summary": "Customer reports that the mobile application crashes with an unhandled 500 error when clicking export PDF on Android."
                })
            if "ord-" in prompt_text or "charge" in prompt_text or "duplicate" in prompt_text or "refund" in prompt_text:
                order_m = re.search(r"ord-\d+", prompt_text, re.IGNORECASE)
                order_id = order_m.group(0).upper() if order_m else "ORD-88219"
                return json.dumps({
                    "title": f"Billing Dispute - Duplicate Charge ({order_id})",
                    "summary": f"Customer reports accidental duplicate billing for order {order_id} and requests immediate refund remediation."
                })
            return json.dumps({
                "title": "Customer Complaint Resolution Request",
                "summary": "Customer submitted an inquiry regarding account resolution and billing entitlements."
            })

        # 2. Concise title generation
        if "output only a 2-3 word title" in prompt_text or "title:" in prompt_text:
            return "Refund Request"

        # 3. Concise summary generation
        if "summarize" in prompt_text:
            return "Complaint regarding delayed delivery. Requesting full refund and expedited handling."

        # 4. Multi-prompt 4-stage resolution sections (Troy agent deliberation)
        is_inquiry = "2fa" in prompt_text or "sms" in prompt_text or "password" in prompt_text or "documentation" in prompt_text

        if "section 1" in prompt_text or "executive assessment" in prompt_text:
            if is_inquiry:
                return (
                    "<thought>Evaluating inquiry details against authentication system telemetry.</thought>"
                    "### **Executive Assessment & Category Confirmation**\n\n"
                    "The customer inquiry regarding account authentication access has been reviewed. "
                    "System telemetry confirms standard technical support procedures apply with no billing ledger adjustments required."
                )
            return (
                "<thought>Auditing customer transaction ledger in data/transactions.csv.</thought>"
                "### **Executive Assessment & Category Confirmation**\n\n"
                "The customer complaint regarding duplicate billing has been verified against transaction records in data/transactions.csv. "
                "Autonomous remediation is authorized under SLA guidelines to restore account standing."
            )

        if "section 2" in prompt_text or "root cause" in prompt_text:
            if is_inquiry:
                return (
                    "<thought>Investigating SMS gateway logs and delivery retry queues.</thought>"
                    "### **Root Cause Analysis & Precedent Alignment**\n\n"
                    "Root cause identified as downstream telecommunication SMS carrier latency. "
                    "Matches knowledge base precedent KB-4402; resending verification code clears the condition."
                )
            return (
                "<thought>Analyzing payment gateway retry events and transaction logs.</thought>"
                "### **Root Cause Analysis & Precedent Alignment**\n\n"
                "Root cause identified as an unhandled payment gateway retry event resulting in a secondary authorization record. "
                "Matches historical precedent KB-8821 with identical remediation protocols."
            )

        if "section 3" in prompt_text or "customer communication" in prompt_text:
            if is_inquiry:
                return (
                    "<thought>Drafting personalized technical guidance for the customer.</thought>"
                    "### **Customer Communication Strategy**\n\n"
                    "**Subject:** Update regarding your authentication verification code\n\n"
                    "Dear Valued Customer,\n\n"
                    "Thank you for contacting support. We have verified gateway traffic and refreshed your verification routing. "
                    "Please retry requesting your verification code now.\n\n"
                    "Sincerely,\nTroy, Lead Complaint Resolution Agent"
                )
            return (
                "<thought>Drafting personalized customer reassurance and confirmation of credit.</thought>"
                "### **Customer Communication Strategy**\n\n"
                "**Subject:** Resolution Update: Subscription Billing Dispute\n\n"
                "Dear Valued Customer,\n\n"
                "We have completed an automated audit of your account and identified the erroneous debit. "
                "A full refund has been credited back to your original payment method. Your account is settled and in good standing.\n\n"
                "Sincerely,\nTroy, Lead Complaint Resolution Agent"
            )

        if "section 4" in prompt_text or "internal routing" in prompt_text:
            if is_inquiry:
                return (
                    "<thought>Logging operational audit trail and closing support ticket.</thought>"
                    "### **Internal Routing & Next Action Steps**\n\n"
                    "1. Dispatched telemetry alert to Identity & Security Operations.\n"
                    "2. Confirmed zero database mutation required for inquiries.\n"
                    "3. Ticket marked Resolved under technical support SLA."
                )
            return (
                "<thought>Recording database audit trail and transitioning ticket status.</thought>"
                "### **Internal Routing & Next Action Steps**\n\n"
                "1. Database record in data/transactions.csv updated to Refunded.\n"
                "2. Customer account balance settled at $0.00.\n"
                "3. Resolution audit log dispatched to Billing & Finance team."
            )

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
```

---

## 3. Integration with Troy Orchestrator (`app/simulation/troy.py`)

### 3.1 Trace of Execution Flow
1. **Intake (`POST /api/next`)**:
   - `main.py` extracts ticket query, provider (defaults to `lmstudio`), model, and apiKey.
   - Creates `run_driver = create_driver(provider, model=model, api_key=api_key, base_url=provider_base_url(provider) or "")`.
   - Starts async task `_run_flow()`.
2. **Ticket Summarization**:
   - Calls `session.orchestrator.summarise_ticket(ticket_query, driver=run_driver)`.
   - Calls `_summarize_ticket_with_llm(text, driver)`.
   - `driver.complete()` executes against `http://localhost:1234/v1` or falls back seamlessly to `MockDriver`.
   - Returns valid JSON `{title, summary}` and broadcasts SSE `topic` and `feed`.
3. **Resolution Deliberation Pipeline (`_run_pipeline`)**:
   - Triage intake (`triage_complaint`) classifies category (e.g. `Refunds` or technical category).
   - Broadcasts `cra_report`.
   - Iterates through the 4 section prompts.
   - Each section executes `_agent_speak`:
     `execute_agent_with_reasoning(driver, sse_manager, ...)` calls `driver.stream(messages, max_tokens, on_delta, on_reasoning_delta)`.
   - If LM Studio is online: real tokens and thinking tags stream to UI.
   - If LM Studio is offline: `OpenAiDriver` catches the error on the first token request and switches to `MockDriver`. `MockDriver` emits word chunks via `on_delta`. `ThoughtBuffer` parses `<thought>` tags and emits `agent_reasoning_delta` (if enabled) and `agent_delta`.
   - Final `agent` SSE event emitted per section with bold markdown heading `### **<Title>**`.
   - Broadcasts `resolution_end` and `council_end`.

---

## 4. Verification Checkpoints & Invalidation Conditions

1. `provider_base_url("lmstudio") == "http://localhost:1234/v1"`
2. `LMSTUDIO_BASE_URL="http://192.168.1.150:1234/v1"` overrides `provider_base_url("lmstudio")`.
3. `LMSTUDIO_BASE_URL=""` falls back to `http://localhost:1234/v1`.
4. `create_driver("lmstudio").base_url` contains `"1234"`.
5. `GET /api/config` reports `baseUrl` pointing to port 1234.
6. `POST /api/config` accepts `provider: "lmstudio"`.
7. `OpenAiDriver(provider="lmstudio", base_url="http://localhost:1234/v1")` with offline server does not raise unhandled exception on `complete()` or `stream()`; returns deterministic text.
8. `MockDriver` passes all Tier 1 and Tier 2 tests in `tests/test_e2e_troy.py`.
