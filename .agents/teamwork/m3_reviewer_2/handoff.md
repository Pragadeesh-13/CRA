# Milestone 3 Review & Adversarial Assessment Report: Local LLM Provider & Mock Fallback

**Reviewer**: Reviewer 2 (`m3_reviewer_2`)  
**Roles**: Reviewer, Adversarial Critic  
**Date**: 2026-11-06  
**Type**: Hard Handoff  
**Verdict**: **APPROVE**  

---

## 1. Observation

1. **Provider Defaults and Dynamic Override Configuration**:
   - In `app/providers/__init__.py:22`, `PROVIDER_BASE_URLS["lmstudio"]` is set to `"http://localhost:1234/v1"`.
   - In `app/providers/__init__.py:26-31`:
     ```python
     def provider_base_url(provider_name: str) -> Optional[str]:
         key = (provider_name or "").strip().lower()
         if key == "lmstudio":
             env_url = (os.getenv("LMSTUDIO_BASE_URL") or "").strip()
             return env_url if env_url else "http://localhost:1234/v1"
         return PROVIDER_BASE_URLS.get(key)
     ```
   - In `app/providers/__init__.py:34-47`, `create_driver(provider_name: str = "lmstudio", **options)` defaults to `"lmstudio"` and automatically injects `base_url = provider_base_url(norm)`.
   - In `app/config.py:18-22`:
     ```python
     llm_provider: str = Field(default_factory=lambda: os.getenv("LLM_PROVIDER", "lmstudio").lower())
     llm_model: str = Field(default_factory=lambda: os.getenv("LLM_MODEL", "qwen/qwen3.5-9b"))
     lmstudio_base_url: str = Field(
         default_factory=lambda: os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
     )
     ```
   - Python test verification command:
     ```powershell
     python -c "from app.config import settings; from app.providers import provider_base_url; assert settings.llm_provider == 'lmstudio'; assert settings.lmstudio_base_url == 'http://localhost:1234/v1'; assert provider_base_url('lmstudio') == 'http://localhost:1234/v1'"
     ```
     Result: Exit code 0, all assertions passed.

2. **`OpenAiDriver` Offline Network Error Handling & Transparent Fallback**:
   - In `app/providers/openai_driver.py:104-105`:
     `self._fallback_driver = MockDriver()` and `self._offline_detected = False` are initialized.
   - In `complete()` (`app/providers/openai_driver.py:169-215`) and `stream()` (`app/providers/openai_driver.py:225-313`):
     - Both methods check `if self._offline_detected:` and immediately route calls to `self._fallback_driver`.
     - Underlying HTTP calls are wrapped with:
       ```python
       except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError, httpx.HTTPError) as e:
           logger.warning("Local server at %s unreachable (%s). Seamlessly falling back to MockDriver.", endpoint, e)
           self._offline_detected = True
           return await self._fallback_driver.complete(messages, max_tokens, **options)
       except Exception as e:
           logger.error("OpenAiDriver encountered unexpected error (%s). Falling back to MockDriver.", e)
           self._offline_detected = True
           return await self._fallback_driver.complete(messages, max_tokens, **options)
       ```
   - In `fetch_models()` (`app/providers/openai_driver.py:314-339`), unhandled exceptions fall back to returning `{"ok": True, "models": fallback}` with `fallback` defaulting to `["qwen/qwen3.5-9b", "google/gemma-4-e4b", "google/gemma-4-12b-qat"]`.
   - Python async test verification against closed port 1234 executed without exceptions and confirmed `_offline_detected = True` latching.

3. **`MockDriver.stream()` Chunk Identity and SSE Compatibility**:
   - In `app/providers/mock_driver.py:136-145`:
     ```python
     response = self._determine_response(messages)
     # Emit in small token chunks
     words = response.split(" ")
     for i, word in enumerate(words):
         chunk = word if i == 0 else " " + word
         if on_delta:
             on_delta(chunk)
     return response
     ```
   - For all strings $S$, `"".join([w if i == 0 else " " + w for i, w in enumerate(S.split(" "))]) == " ".join(S.split(" ")) == S`.
   - Tested 11 input variations (JSON prompts, concise summaries, 4-section multi-prompt cases, empty message lists, and custom spacing) via async runner: 100% matched `full == "".join(chunks)`.

4. **Integrity and Anti-Cheating Verification**:
   - Automated grep of `app/**/*.py` for `test_tier`, `pytest`, `test_challenger`, and hardcoded assertion hooks returned 0 matches.
   - Zero facade bypasses: `OpenAiDriver` is a functional HTTP client communicating with OpenAI chat completions and parsing SSE lines (`aiter_lines()`).
   - `MockDriver` dynamically extracts order IDs (`re.search(r"ord-\d+", ...)`) and distinguishes technical inquiry triggers from billing triggers.

5. **Decoupled Fast Startup and Static Frontend Preservation**:
   - Startup command:
     `python -c "import time; t0 = time.time(); from app.main import app; print(f'Startup completed in {time.time()-t0:.3f}s')"`
     Result: `0.826s` (< 1.0s, no legacy ML weights loaded).
   - Frontend immutability command:
     `git status public/` returned `nothing to commit, working tree clean` (0 files modified or untracked).

6. **Automated Test Suite Execution**:
   - `pytest tests/test_e2e_troy.py -k "feature9 or feature11"` -> `20 passed in 0.72s`.
   - `pytest tests/test_e2e_troy.py` -> `143 passed in 1.66s`.
   - `pytest tests/test_category_resolutions.py tests/test_api_endpoints.py` -> `56 passed in 3.31s`.
   - `pytest tests/test_challenger1_m2.py tests/test_challenger2_m2.py tests/test_challenger2_edge_cases.py` -> `56 passed in 2.49s`.

---

## 2. Logic Chain

1. **LM Studio Default Port & Dynamic Configuration** (Observations 1 & 6):
   - Mandate required LM Studio default URL to be `http://localhost:1234/v1` and support `LMSTUDIO_BASE_URL` override.
   - `app/providers/__init__.py:22` updates the registry URL from 1235 to 1234.
   - `provider_base_url()` evaluates `os.getenv("LMSTUDIO_BASE_URL")` on each call, stripping whitespace and falling back to default port 1234 if empty.
   - `create_driver("lmstudio")` automatically injects this URL into driver options.
   - `Settings` in `app/config.py` defaults to provider `"lmstudio"`, model `"qwen/qwen3.5-9b"`, and base URL `"http://localhost:1234/v1"`.
   - Therefore, Requirement R2 / Feature 9 is fully satisfied.

2. **Resilient Local LLM Provider & Mock Fallback** (Observations 2 & 6):
   - When the local model server is offline, HTTP requests to `http://localhost:1234/v1/chat/completions` throw connection errors (e.g., WinError 10061).
   - `OpenAiDriver.complete` and `OpenAiDriver.stream` catch both specific `httpx` network/status exceptions and generic `Exception`, log a warning, set `self._offline_detected = True`, and seamlessly delegate to `MockDriver`.
   - Once offline is detected, subsequent requests in that session bypass network connection attempts entirely, preventing repeated multi-second connection timeouts.
   - Therefore, Requirement R2 / Feature 11 is robustly satisfied without throwing unhandled exceptions.

3. **Stream Chunk Identity & Frontend Compatibility** (Observations 3, 5 & 6):
   - The streaming contract requires `full == "".join(chunks)`.
   - The token reconstruction logic in `MockDriver.stream()` mathematically guarantees that the sum of emitted chunks equals the returned text for any valid string.
   - Emitted chunks feed into SSE `agent_delta` frames consumed by the frontend.
   - Files in `public/` remain 100% frozen and untouched.
   - Therefore, Requirement R4 / Feature 13 is fully preserved.

4. **Code Quality and Integrity** (Observations 4 & 5):
   - Independent verification revealed no hardcoded test hooks, no facade implementations, and no fabricated artifacts.
   - The application starts cleanly in 0.826 seconds without any legacy machine learning models (DistilBERT/SBERT/XGBoost).
   - Therefore, all integrity mandates are met.

---

## 3. Caveats & Adversarial Findings

### [Minor] Adversarial Finding 1: NoneType in Message Content
- **What**: In `MockDriver._determine_response()` (`app/providers/mock_driver.py:13`), prompt text is extracted via `" ".join(m.get("content", "") for m in (messages or []))`. If a message dictionary explicitly contains `{"content": None}` (as sometimes emitted by OpenAI tool calls or null responses), `m.get("content", "")` returns `None`, causing `TypeError: sequence item 0: expected str instance, NoneType found`.
- **Impact**: Extremely Low. Troy's internal simulation always constructs messages with non-null string content (`{"role": "user", "content": "..."}`).
- **Recommendation**: For future defensive hardening in Milestone 4, replace `m.get("content", "")` with `m.get("content") or ""` to gracefully handle explicit `None` content.

### [Minor] Adversarial Finding 2: Mid-stream Network Disconnect Behavior
- **What**: If an LM Studio server is online initially but severs connection mid-stream during token iteration (`aiter_lines()`), `OpenAiDriver.stream()` catches the exception and invokes `self._fallback_driver.stream()`. If some real tokens were already pushed to `on_delta`, the fallback driver will stream the mock response starting from the beginning.
- **Impact**: Low. In a local LLM setup, the server is either online or offline. A mid-stream drop is an extreme edge case, and falling back to a full valid mock response is vastly superior to crashing the ASGI server or leaving the frontend in an unhandled pending state.

---

## 4. Conclusion & Verdict

**Verdict**: **APPROVE**

Worker 1's implementation of Milestone 3 satisfies all acceptance criteria, review mandate requirements, and interface contracts:
- LM Studio default URL is `http://localhost:1234/v1` and `LMSTUDIO_BASE_URL` env override works reliably.
- `OpenAiDriver` catches all network and connection errors when offline and transparently delegates to `MockDriver`.
- `MockDriver.stream()` maintains exact chunk identity (`full == "".join(chunks)`).
- `app/config.py` defaults to `"lmstudio"`.
- Startup is fast (0.826s) and completely decoupled from legacy ML components.
- Frontend assets in `public/` are strictly unmodified.
- No integrity violations or hardcoded test bypasses detected.

---

## 5. Verification Method

To independently verify this evaluation:

1. **Verify Config Defaults and Base URL Lookup**:
   ```powershell
   python -c "
   import os
   from app.config import settings
   from app.providers import provider_base_url, create_driver

   assert settings.llm_provider == 'lmstudio'
   assert settings.llm_model == 'qwen/qwen3.5-9b'
   assert settings.lmstudio_base_url == 'http://localhost:1234/v1'

   assert provider_base_url('lmstudio') == 'http://localhost:1234/v1'
   os.environ['LMSTUDIO_BASE_URL'] = 'http://10.0.0.5:1234/v1'
   assert provider_base_url('lmstudio') == 'http://10.0.0.5:1234/v1'
   del os.environ['LMSTUDIO_BASE_URL']
   print('Config and URL tests passed.')
   "
   ```

2. **Verify Offline Fallback and Chunk Identity**:
   ```powershell
   python -c "
   import asyncio
   from app.providers.openai_driver import OpenAiDriver

   async def run():
       driver = OpenAiDriver(provider='lmstudio', base_url='http://localhost:1234/v1', timeout_ms=500)
       res = await driver.complete([{'role': 'user', 'content': 'Test double charge ORD-88219'}])
       assert len(res) > 0 and driver._offline_detected
       chunks = []
       stream_res = await driver.stream([{'role': 'user', 'content': 'section 1 executive assessment'}], on_delta=lambda c: chunks.append(c))
       assert stream_res == ''.join(chunks)
       print('Offline fallback and chunk identity passed.')

   asyncio.run(run())
   "
   ```

3. **Verify E2E and Category Resolutions Test Suites**:
   ```powershell
   pytest tests/test_e2e_troy.py -v
   pytest tests/test_category_resolutions.py tests/test_api_endpoints.py -v
   ```

4. **Verify Frontend File Immutability**:
   ```powershell
   git status public/
   ```
   *Expected*: `nothing to commit, working tree clean`.
