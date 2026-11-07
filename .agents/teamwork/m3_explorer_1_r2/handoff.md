# Milestone 3 Explorer 1 Handoff Report: Local LLM Provider & Mock Fallback

## 1. Observation
1. **Hardcoded Port 1235 in Provider Layer**:
   - `app/providers/__init__.py:21`:
     ```python
     PROVIDER_BASE_URLS: Dict[str, str] = {
         "openrouter": "https://openrouter.ai/api/v1",
         "openai": "https://api.openai.com/v1",
         "groq": "https://api.groq.com/openai/v1",
         "anthropic": "https://api.anthropic.com",
         "lmstudio": "http://localhost:1235/v1",
     }
     ```
   - `app/providers/openai_driver.py:108-109`:
     ```python
     if self.provider == "lmstudio":
         return "http://localhost:1235/v1"
     ```
   - Requirement R2 and Feature 9 explicitly mandate port 1234 (`http://localhost:1234/v1`).
2. **Missing `LMSTUDIO_BASE_URL` Dynamic Environment Variable Lookup**:
   - `app/providers/__init__.py:25-27`:
     ```python
     def provider_base_url(provider_name: str) -> Optional[str]:
         key = (provider_name or "").strip().lower()
         return PROVIDER_BASE_URLS.get(key)
     ```
     Does not query `os.getenv("LMSTUDIO_BASE_URL")`.
   - `tests/test_e2e_troy.py:1008-1012`:
     ```python
     def test_tier1_feature9_lmstudio_base_url_env_override(monkeypatch):
         monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://192.168.1.150:1234/v1")
         url = provider_base_url("lmstudio")
         assert url == "http://192.168.1.150:1234/v1"
     ```
     Fails against current code because `provider_base_url` ignores the environment variable.
3. **Application Settings Default to Hosted Provider**:
   - `app/config.py:18`:
     ```python
     llm_provider: str = Field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openrouter").lower())
     ```
   - `app/providers/__init__.py:31`:
     ```python
     norm = (provider_name or "openrouter").strip().lower()
     ```
     Defaults to `"openrouter"` instead of `"lmstudio"`.
4. **Unhandled Network/Connection Errors in `OpenAiDriver`**:
   - `app/providers/openai_driver.py:174-175`:
     ```python
     async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
         res = await client.post(endpoint, headers=headers, json=body)
     ```
   - `app/providers/openai_driver.py:224-225`:
     ```python
     async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_sec, read=self.stream_idle_timeout_sec)) as client:
         async with client.stream("POST", endpoint, headers=headers, json=body) as res:
     ```
     When LM Studio is offline, `client.post` and `client.stream` raise `httpx.ConnectError` (WinError 10061: Target machine actively refused it) or `httpx.TimeoutException`.
   - In `complete`: Unhandled exception propagates up to caller.
   - In `stream`: Propagates to `execute_agent_with_reasoning` (`app/reference/reasoning_and_output_reference.py:110-114`), which broadcasts `"Signal lost. Unable to reach model provider."` to SSE clients before Troy catches it.
5. **Existing `MockDriver` Implementation (`app/providers/mock_driver.py:11-33`)**:
   - Implements keyword matching for title/summary JSON, 2-3 word title, and summarize.
   - Currently returns a single generic agent string for any other prompt: `"<thought>Evaluating complaint details and customer impact.</thought>Your refund request has been accepted and is being processed."`.
   - Does not differentiate between the 4 dedicated resolution prompt sections or technical inquiry vs duplicate billing dispute.
6. **E2E Test Contracts for Features 9 & 11**:
   - `tests/test_e2e_troy.py:1002-1077` (Feature 9) validates:
     - `provider_base_url("lmstudio") == "http://localhost:1234/v1"`
     - `LMSTUDIO_BASE_URL` env override and empty env fallback
     - `create_driver("lmstudio")` base_url contains "1234"
     - `GET /api/config` and `POST /api/config` with provider "lmstudio"
     - URL normalization for trailing slashes and missing `/v1`
     - Case-insensitive provider name ("LMSTUDIO", "LmStudio")
   - `tests/test_e2e_troy.py:1176-1280` (Feature 11) validates:
     - `complete()` transparently succeeds when server offline
     - `stream()` emits token chunks via `on_delta` and matches `full == "".join(chunks)`
     - `fetch_models()` returns curated models list when server offline
     - Connection refused does not crash
     - Entity preservation (Android PDF crash keyword)
     - Empty messages list handled safely
     - Low timeout (10ms) does not hang
     - Sequential fallback stability (10 iterations)
     - Non-blocking async event loop streaming (elapsed < 0.5s)

---

## 2. Logic Chain
1. **Observation 1 & 2 -> Port 1234 & `LMSTUDIO_BASE_URL` Fix**:
   - Because `PROVIDER_BASE_URLS["lmstudio"]` is `1235` and `provider_base_url` ignores `LMSTUDIO_BASE_URL`, updating `PROVIDER_BASE_URLS["lmstudio"] = "http://localhost:1234/v1"` and modifying `provider_base_url()` to check `(os.getenv("LMSTUDIO_BASE_URL") or "").strip()` satisfies both default port 1234 and dynamic environment override requirements.
2. **Observation 1 -> `OpenAiDriver._default_base_url` Fix**:
   - `OpenAiDriver._default_base_url()` must also return `(os.getenv("LMSTUDIO_BASE_URL") or "").strip() or "http://localhost:1234/v1"` when `self.provider == "lmstudio"`.
3. **Observation 3 -> Application Settings & Provider Default**:
   - Updating `Settings.llm_provider` in `app/config.py` to default to `os.getenv("LLM_PROVIDER", "lmstudio").lower()` and `create_driver()` in `app/providers/__init__.py` to default to `"lmstudio"` ensures Troy runs as a local LLM agent by default across startup, session resolution, and ticket dispatch.
4. **Observation 4 -> Transparent Driver-Level Mock Fallback**:
   - Because offline connection errors happen inside `OpenAiDriver.complete` and `OpenAiDriver.stream`, adding a `try...except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError, httpx.HTTPError, Exception)` block in `OpenAiDriver` that catches network failures and seamlessly delegates to an internal `self._fallback_driver = MockDriver()` prevents any unhandled exception from propagating to `troy.py` or `execute_agent_with_reasoning`.
   - Setting a cached flag `self._offline_detected = True` on first failure ensures that subsequent calls during the same resolution flow immediately use `MockDriver` without repeating multi-second network connection timeouts.
5. **Observation 5 -> Deterministic Rich Section Output**:
   - Enriching `MockDriver._determine_response()` to recognize Section 1 ("Executive Assessment"), Section 2 ("Root Cause"), Section 3 ("Customer Communication"), and Section 4 ("Internal Routing"), as well as technical inquiry vs duplicate billing contexts, ensures that offline fallback produces structured, authoritative markdown with thinking tags and matching headings (`### **<Title>**`), satisfying all UI rendering and streaming requirements.

---

## 3. Caveats
1. **Legacy Test Port Mismatch (`tests/test_api_endpoints.py:128, 137`)**:
   - `tests/test_api_endpoints.py` contains legacy assertions expecting `"http://localhost:1235/v1"`. Per Milestone 3 requirements and `tests/test_e2e_troy.py:1005`, the correct default port is `1234`. Tests are currently skipped per user instruction, but during Milestone 4 test execution, lines 128 and 137 in `tests/test_api_endpoints.py` will reflect `1234`.
2. **Offline Detection Scope**:
   - `self._offline_detected` is maintained per `OpenAiDriver` instance. Since `app/main.py` creates a new `run_driver` instance per ticket resolution session, each new ticket will attempt connection to LM Studio first, falling back to mock driver if still offline. This avoids permanently latching to mock mode if LM Studio is started after application launch.
3. **Stream Delta Contract**:
   - `MockDriver.stream` splits text on whitespace and feeds tokens to `on_delta`. `test_tier1_feature11_stream_mock_fallback_when_server_offline` asserts `full == "".join(chunks)`. `MockDriver.stream` strictly preserves this identity.

---

## 4. Conclusion
Features 9 and 11 are fully analyzed and ready for implementation by the Worker:
1. Update `app/providers/__init__.py`: change port to 1234, implement `LMSTUDIO_BASE_URL` dynamic lookup in `provider_base_url()`, and set default provider to `"lmstudio"`.
2. Update `app/providers/openai_driver.py`: update default base URL for LM Studio, embed `self._fallback_driver = MockDriver()`, and implement transparent try/except fallback on `complete()` and `stream()`.
3. Update `app/providers/mock_driver.py`: enhance `_determine_response()` to produce section-specific structured markdown and inquiry-aware responses while preserving 100% backward compatibility with all Feature 11 test assertions.
4. Update `app/config.py`: default `llm_provider` to `"lmstudio"`, `llm_model` to `"qwen/qwen3.5-9b"`, and add `lmstudio_base_url`.
5. Ensure `app/main.py` passes `base_url=provider_base_url(provider) or ""` when constructing drivers.

---

## 5. Verification Method

### Concrete Verification Steps:
1. **Feature 9 Verification**:
   - In Python shell / script:
     ```python
     import os
     from app.providers import provider_base_url, create_driver

     # Default port 1234
     assert provider_base_url("lmstudio") == "http://localhost:1234/v1"

     # Env var override
     os.environ["LMSTUDIO_BASE_URL"] = "http://192.168.1.150:1234/v1"
     assert provider_base_url("lmstudio") == "http://192.168.1.150:1234/v1"

     # Empty env var fallback
     os.environ["LMSTUDIO_BASE_URL"] = ""
     assert provider_base_url("lmstudio") == "http://localhost:1234/v1"

     # Driver port 1234
     driver = create_driver("lmstudio")
     assert "1234" in driver.base_url
     ```
2. **Feature 11 Verification (Offline Local Server)**:
   - In Python shell / script:
     ```python
     import asyncio
     from app.providers.openai_driver import OpenAiDriver
     from app.providers.mock_driver import MockDriver

     # Point to offline port
     driver = OpenAiDriver(provider="lmstudio", base_url="http://localhost:1234/v1")

     # complete() must not raise exception; must return valid mock text
     loop = asyncio.new_event_loop()
     res = loop.run_until_complete(driver.complete([{"role": "user", "content": "Test prompt"}]))
     assert isinstance(res, str) and len(res) > 0

     # stream() must stream tokens without raising exception
     chunks = []
     stream_res = loop.run_until_complete(
         driver.stream([{"role": "user", "content": "Customer double charge ORD-88219"}], on_delta=lambda c: chunks.append(c))
     )
     assert len(chunks) > 0
     assert stream_res == "".join(chunks)
     loop.close()
     ```
3. **API Endpoint Verification**:
   - Run `GET /api/config` -> returns `{"ok": True, "provider": "lmstudio", "baseUrl": "http://localhost:1234/v1", ...}`.
   - Run `POST /api/next` with `{"query": "ORD-88219 duplicate charge"}` when LM Studio is offline -> returns `{"ok": True, "started": True}` and streams resolution without 500 error.

### Invalidation Conditions:
- `provider_base_url("lmstudio")` returns anything other than `http://localhost:1234/v1` when `LMSTUDIO_BASE_URL` is unset or empty.
- `LMSTUDIO_BASE_URL` override fails to change `provider_base_url("lmstudio")`.
- `OpenAiDriver.complete()` or `OpenAiDriver.stream()` raises `httpx.ConnectError` when the local port is closed.
- `stream()` chunks do not reconstruct the exact return value (`full != "".join(chunks)`).
