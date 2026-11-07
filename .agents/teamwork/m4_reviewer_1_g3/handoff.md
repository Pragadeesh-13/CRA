# Milestone 4 Reviewer 1 Handoff Report: Local LLM Triage, Provider Layer & Mock Fallback (Requirement R1)

**Agent**: `m4_reviewer_1_g3`  
**Roles**: reviewer, critic  
**Working Directory**: `D:\CRA`  
**State Folder**: `D:\CRA\.agents\teamwork\m4_reviewer_1_g3`  
**Date**: 2026-11-07  
**Verdict**: **APPROVE**  

---

## 1. Observation

### 1.1 Local LLM Triage Implementation in `app/triage.py`
- In `app/triage.py:284-296`, the legacy regex-based `CLASSIFICATION_PATTERNS` dictionary has been completely removed (`CLASSIFICATION_PATTERNS present: False`).
- `SYSTEM_TRIAGE_PROMPT` (`app/triage.py:296-351`) instructs the LLM to choose from `ALL_11_CATEGORIES` and return strict schema-compliant JSON.
- `get_triage_driver()` (`app/triage.py:356-380`) initializes a singleton `OpenAiDriver(provider="lmstudio", base_url=target_url)` resolving `os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")`. It includes a fast socket reachability check on localhost with a 30ms timeout that proactively flags `_offline_detected = True` when offline.
- `async_triage_complaint` (`app/triage.py:580-608`) sends structured messages to `d.complete(messages, max_tokens=1000)` with a 10s timeout, parsing outputs via `parse_json()` and normalizing with `_normalize_llm_report()`. If unreachable or on error, it gracefully falls back to `_deterministic_fallback_report(raw)`.
- Synchronous `triage_complaint` (`app/triage.py:610-634`) safely checks `asyncio.get_running_loop()`. When executed within an active, running event loop, it delegates execution to a single-worker `ThreadPoolExecutor`, preventing event loop nesting deadlocks.
- `CategoryResult` (`app/triage.py:383-430`) inherits from `(str, Mapping)`, supporting string comparison (`cr == "Refunds"`, `cr.lower()`), dictionary indexing (`cr["predicted_category"]`, `cr["category_confidence"]`, `cr["top_3_categories"]`), iteration, `cr.get()`, and `.to_dict()`.

### 1.2 Deterministic Mock Fallback in `app/providers/mock_driver.py`
- Triage branch (`app/providers/mock_driver.py:18-150`): Intercepts prompts containing keywords (`"predicted_category"`, `"11 standard cra categories"`, `"triage agent"`, `"intake triage"`). Evaluates `latest_user_prompt` against domain keywords for all 11 CRA categories and returns schema-compliant JSON in ~0.066ms (< 1ms).
- Multi-turn section selector fix (`app/providers/mock_driver.py:14-16, 195-264`):
  Extracts `user_messages = [m.get("content", "") for m in (messages or []) if m.get("role") == "user"]` and sets `latest_user_prompt = user_messages[-1].lower()`.
  Evaluates sections in reverse order: Section 4 -> Section 3 -> Section 2 -> Section 1, with constraint protection `and "do not" not in eval_prompt`, preventing prompt history or negative instructions from causing Sections 2, 3, or 4 to be swallowed by Section 1.

### 1.3 Mandatory Verification Test Suite Executions
- Command 1: `pytest tests/test_e2e_troy.py -k "feature4 or feature9 or feature11" -v`
  **Result**: `30 passed, 113 deselected in 0.70s`.
- Command 2: `pytest tests/test_category_resolutions.py -v`
  **Result**: `40 passed in 0.43s`.
- Command 3: `python -c "from app.triage import triage_complaint, predict_category; r = triage_complaint('Charged twice for ORD-88219'); print(r['predicted_category'], r['recommended_team'])"`
  **Result**: Verbatim output: `Refunds Billing`.
- Secondary verification suites:
  * `pytest tests/test_database_tools.py -v`: `26 passed in 0.32s`.
  * `pytest tests/test_api_endpoints.py -v`: `16 passed in 0.58s`.
  * `pytest tests/test_e2e_troy.py -v`: `143 passed in 3.75s`.
  * `pytest tests/test_triage_stress.py -v`: `18 passed in 0.45s`.
- Frontend and Data integrity:
  * `git status public/`: `nothing to commit, working tree clean` (0 modifications to UI).
  * `git status data/`: `nothing to commit, working tree clean` (0 base data drift).
  * Startup latency: `0.740s` (< 1.0s).

### 1.4 Windows Pytest Environment Variable Boundary Limit
- Running `pytest tests/test_challenger_m3.py` without filters encountered:
  `ValueError: the environment variable is longer than 32767 characters` during pytest setup in `test_evaluate_intent_edge_cases_no_crash[aaaaaaaaaaaaaaaa...]`.
- In `tests/test_challenger_m3.py:81`, `"a" * 100000` is parameterized directly. Windows environment variables have an OS limit of 32,767 characters, causing pytest's `os.environ["PYTEST_CURRENT_TEST"]` update to raise `ValueError`. Running with `-k "not aaaaa"` resolves the issue.

---

## 2. Logic Chain

1. **Superseding Heuristic Models (Observation 1.1)**:
   The previous `CLASSIFICATION_PATTERNS` regex map was removed. Incoming requests to `triage_complaint` and `predict_category` are routed through `OpenAiDriver` with prompt `SYSTEM_TRIAGE_PROMPT`. This fulfills Requirement R1.
2. **Transparent Mock Fallback (Observations 1.1 & 1.2)**:
   `OpenAiDriver` detects offline status via fast socket check and connection error trapping, switching to `MockDriver`. `MockDriver` evaluates domain keywords in `latest_user_prompt` and emits complete, schema-compliant JSON in 0.066ms, well under the 1ms requirement.
3. **Event Loop Safety & Concurrency (Observation 1.1 & Stress Tests)**:
   Synchronous `triage_complaint` detects active loops via `asyncio.get_running_loop()`. When called from within an async worker or coroutine, it executes in a background thread via `ThreadPoolExecutor`, avoiding `RuntimeError: This event loop is already running`. High concurrency tests (50 burst calls) executed without deadlocks.
4. **Interface Contract Compatibility (Observations 1.1, 1.2, 1.3)**:
   `CategoryResult` cleanly acts as both string and dict. The frontend endpoints `/api/cra/triage`, `/api/cra/predict`, `/api/cra/resolve`, and `/api/cra/classify` respond with expected payloads. Static assets in `public/` are untouched.
5. **Multi-Turn Section Ordering (Observation 1.2)**:
   Testing consecutive turns 1, 2, 3, and 4 verified that Sections 2, 3, and 4 are no longer swallowed by Section 1.
6. **Integrity Audit (Observations 1.1 - 1.4)**:
   Code inspection verified no hardcoded test cheats or dummy facades. The LLM triage pipeline genuinely formats prompts, parses JSON, and falls back to deterministic mock logic as specified.

---

## 3. Caveats

- **LM Studio Server Requirement**: For live local inference, an OpenAI-compatible server (LM Studio) must be running on `http://localhost:1234/v1` (or `LMSTUDIO_BASE_URL`). When offline, the system transparently utilizes the deterministic mock fallback.
- **Windows Pytest Parameter Limit in Challenger Suite**: Parameterizing inputs longer than 32,767 characters in pytest causes `ValueError` on Windows due to OS environment variable length restrictions. Such tests must use explicit test IDs (e.g. `pytest.param("a"*100000, id="100k_a")`) or be filtered with `-k "not aaaaa"`.

---

## 4. Conclusion

Requirement R1 (Local LLM Triage & Categorization, Provider Layer, and Mock Fallback) is **FULLY IMPLEMENTED, CORRECT, AND ROBUST**.
- Legacy regex heuristics have been eliminated.
- Local LLM prompt intake and schema normalization are verified.
- Deterministic mock fallback executes schema-compliant payloads in < 1ms.
- Multi-turn section swallowing bug is resolved.
- Event loop safety is verified across sync and async contexts.
- Zero frontend modifications; 0 base data drift.
- All 185 primary tests and 18 triage stress tests pass cleanly.

**Verdict**: **APPROVE**.

---

## 5. Verification Method

To independently verify this evaluation:

### Step 1: Run Required Target Tests
```powershell
pytest tests/test_e2e_troy.py -k "feature4 or feature9 or feature11" -v
pytest tests/test_category_resolutions.py -v
python -c "from app.triage import triage_complaint, predict_category; r = triage_complaint('Charged twice for ORD-88219'); print(r['predicted_category'], r['recommended_team'])"
```
**Expected Outcome**: 30 passed in ~0.70s, 40 passed in ~0.43s, and output `Refunds Billing`.

### Step 2: Run Full Suite of Primary Tests
```powershell
pytest tests/test_e2e_troy.py tests/test_database_tools.py tests/test_api_endpoints.py tests/test_triage_stress.py -v
```
**Expected Outcome**: 203 passed in ~5.0s.

### Step 3: Verify Frontend Immutability and Decoupled Startup
```powershell
git status public/
# Expected: nothing to commit, working tree clean

python -c "import time; t0 = time.perf_counter(); import app.main; print(f'Startup: {time.perf_counter()-t0:.3f}s')"
# Expected: Startup < 1.0s
```
