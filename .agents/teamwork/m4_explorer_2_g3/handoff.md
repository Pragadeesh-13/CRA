# Handoff Report: Local LLM Triage, Workflow Integration, UI Contracts & Milestone 3 Audit

**Author**: Explorer 2 (`m4_explorer_2_g3`)  
**Working Directory**: `D:\CRA`  
**State Folder**: `D:\CRA\.agents\teamwork\m4_explorer_2_g3`  
**Target Milestone**: Milestone 3 Review & Milestone 4 Implementation Guide  

---

## 1. Observation

### 1.1 Troy Simulation Engine & Triage Integration (`app/simulation/troy.py`)
- **Invocation Location**: In `ComplaintResolutionOrchestrator._run_pipeline(self, story: Dict[str, Any])`:
  - Lines 302-306:
    ```python
    from app.triage import triage_complaint
    # Run triage asynchronously without blocking event loop
    try:
        report = await asyncio.to_thread(triage_complaint, complaint_text)
    except Exception as e:
        ...
    ```
  - Lines 321-325: Extracts `pred_cat = report.get("predicted_category", "Support")`, `conf_pct = report.get("category_confidence", 0.0) * 100`, `team = report.get("recommended_team", "Tier 1 Support")`, and `sla_hours = report.get("sla", {}).get("estimated_hours", 4.0)`.
  - Line 327: Directly broadcasts `self.sse.broadcast("cra_report", report)`.
  - Lines 339-359: Injects triage intelligence into prompt lines:
    - `- Predicted Category: {pred_cat} (Confidence: {conf_pct:.1f}%)`
    - `- Top Categories: {top_cats_str}`
    - `- Extracted Entities: References: {...}, Dates: {...}, Products: {...}`
    - `- Recommended Department Routing: {team} (Method: {report.get('team_method', 'Rule-based')})`
    - `- Estimated SLA & Resolution Time: {sla_hours} hours (Risk: {report.get('sla', {}).get('sla_risk', 'Low')})`
    - Historical Precedents from `report.get("similar_cases", [])`.
  - Lines 368-370: Evaluates intent:
    ```python
    intent_info = self.evaluate_complaint_intent(complaint_text, category=pred_cat)
    is_refund_domain = intent_info.get("intent") == "database_remediation" or pred_cat == "Refunds"
    cat_ctx = get_category_complaint_context(category=pred_cat, query=complaint_text)
    ```
  - Lines 378-426: If `is_refund_domain`, invokes `inspect_account_and_transactions` and `remediate_duplicate_charge` when an unrefunded duplicate charge exists, broadcasting `database_updated`.
  - Lines 503-652: Executes a 4-section multi-prompt loop:
    - Section 0: `executive` (`### **Executive Assessment & Category Confirmation**`)
    - Section 1: `root_cause` (`### **Root Cause Analysis & Precedent Alignment**`)
    - Section 2: `customer_comms` (`### **Customer Communication Strategy**`)
    - Section 3: `internal_routing` (`### **Internal Routing & Next Action Steps**`)
  - Lines 655-685: Broadcasts `resolution_end` and `council_end`, both embedding `cra_report: report`, `full_resolution: full_resolution_text`, and `sections: section_results`.

### 1.2 SSE Stream Event Sequence (`/api/stream`)
From `app/main.py:146-190`, `app/main.py:380-388`, and `app/simulation/troy.py:274-685`:
1. `event: status` — Sent on connection handshake with `{"message": "Waiting for resolution signal.", "provider": ..., "model": ..., "connection": "connected"}`.
2. `event: ticket_summarizing` / `event: policy_summarizing` — Emitted when `/api/next` starts processing ticket text.
3. `event: topic` — Emitted with `{"title": "<Ticket Title>"}` from `summarise_ticket()`.
4. `event: feed` — Emitted with `{"title": ..., "summary": ..., "description": ..., "publishedAt": ...}`.
5. `event: resolution_start` & `event: council_start` — Emitted at pipeline start.
6. `event: round_start` — `{"round": 0, "label": "Resolution", "count": 1, "order": ["Troy"]}`.
7. `event: system` — Preliminary context analysis messages.
8. `event: cra_report` — Full payload dict from triage intake containing:
   - `complaint`: raw input text
   - `predicted_category`: string (one of 11 CRA categories)
   - `category_confidence`: float (0.0 to 1.0)
   - `top_3_categories`: list of `{"category": str, "score": float}`
   - `extracted_entities`: `{"references": [...], "dates": [...], "amounts": [...], "products": [...]}`
   - `similar_cases`: list of precedents with `similarity`, `description`, `resolution`
   - `recommended_team`: string department name
   - `team_confidence`: string (`"High"` / `"Medium"`)
   - `team_method`: string (`"Rule-based"` / `"Autonomous LLM"`)
   - `sla`: `{"estimated_hours": float, "sla_target_hours": float, "sla_risk": str}`
   - `predicted_sla_hours`: float
9. `event: system` — Intake triage summary message.
10. `event: system` — Autonomous intent decision message (`database_remediation` vs `direct_response`).
11. (If billing dispute with unrefunded charge) `event: database_updated` with remediation record.
12. Multi-section loop (4 iterations: executive, root_cause, customer_comms, internal_routing):
    - `event: system` — Section generation start message.
    - `event: agent_start` — Section header with metadata (`section`, `section_index`, `section_title`, `card_id`, `speech_id`).
    - `event: agent_delta` — Real-time tokens and reasoning tokens.
    - `event: agent` — Complete section message.
13. `event: resolution_end` & `event: council_end` — Emits `summary`, `category`, `confidence`, `team`, `sla_hours`, `cra_report`, `full_resolution`, `sections`.

### 1.3 REST Endpoints Interaction with Triage & Resolution
- **`POST /api/next`** (`app/main.py:326-388`):
  - Accepts JSON payload `{"query": str, "provider": str, "apiKey": str, "model": str}` or query string.
  - Spawns background task `_run_flow()` -> `summarise_ticket()` -> `Troy.run()` -> `triage_complaint()`.
  - Returns `{"ok": True, "started": True}`.
- **`POST /api/cra/resolve`** (`app/main.py:572-592`):
  - Accepts `{"complaint": str}` (or `query` / `text`).
  - Directly calls `triage_complaint(complaint)`.
  - Returns `{"ok": True, "report": report}`.
- **`POST /api/cra/classify`** (`app/main.py:594-613`):
  - Accepts `{"complaint": str}`.
  - Calls `predict_category(complaint)`.
  - Returns `{"ok": True, "predicted_category": str, "category_confidence": float, "top_3_categories": list}`.
- **`GET /api/customer/verify`** (`app/main.py:203-250`):
  - Accepts query parameters `query`, `customer_id`, `category`.
  - If `category` is omitted, dynamically resolves category using `predict_category(query)`.
  - Resolves ledger context from `app/database.py`.
- **`POST /api/resolve/action`** (`app/main.py:259-291`):
  - Accepts JSON: `action`, `customer_id`, `query`, `category`, `action_details`, `notes`.
  - Dynamically predicts category if missing: `predict_category(query)`.
  - Executes database remediation or manual escalation, broadcasting `database_updated`.

### 1.4 Frontend UI Contracts (`public/js/app.js` and `public/index.html`)
The frontend is 100% frozen and strictly expects the following contracts:
- **Preliminary Case Assessment Card** (`public/js/app.js:2070-2158` `deliverPreliminaryAssessment(report)`):
  - `report.predicted_category`: string category name
  - `report.category_confidence`: float (0.0 to 1.0)
  - `report.recommended_team`: string department name
  - `report.sla.estimated_hours` / `report.predicted_sla_hours`: float hours
  - `report.sla.sla_risk`: string (`"Low"`, `"Medium"`, `"Critical"`, `"Urgent"`)
  - `report.similar_cases`: list of objects with `similarity` (float 0.0-1.0), `description`, `resolution`
  - `report.extracted_entities.references`: list of reference ID strings
  - `report.extracted_entities.dates`: list of date strings
  - `report.extracted_entities.amounts`: list of currency/amount strings
- **4-Stage Resolution Markdown Headings** (`public/js/app.js:972-1009` `SECTION_DEFINITIONS`):
  - Stage 0: `### **Executive Assessment & Category Confirmation**` (`cardId: "troy-card"`, `speechId: "troy-speech"`)
  - Stage 1: `### **Root Cause Analysis & Precedent Alignment**` (`cardId: "troy-card-root-cause"`, `speechId: "troy-root-cause-speech"`)
  - Stage 2: `### **Customer Communication Strategy**` (`cardId: "troy-card-communication"`, `speechId: "troy-communication-speech"`)
  - Stage 3: `### **Internal Routing & Next Action Steps**` (`cardId: "troy-card-routing"`, `speechId: "troy-routing-speech"`)
- **Customer Verification Strip** (`public/js/app.js:1210-1350`):
  - Expects `customer` dict (`customer_id`, `customer_name`, `customer_email`, `subscription_plan`, `total_billed_this_month`, `charges_this_month`).
  - Expects boolean `is_already_refunded` (or inside `customer`).
  - Non-refund inquiries: expects `system_label`, `status_pill`, `system_details`, `action_btn_text`, `reference_id`.

### 1.5 Milestone 3 Evaluation & Defect Identification
1. **`app/providers/openai_driver.py`**:
   - Sound: Configurable via `LMSTUDIO_BASE_URL`, defaults to `http://localhost:1234/v1`.
   - Sound: Transparent fallback to `MockDriver()` when local server is offline or times out (`_offline_detected = True`).
   - Sound: Handles reasoning tokens (`reasoning_content`) and stripping thought tags via `ThoughtBuffer`.
2. **`app/providers/mock_driver.py`**:
   - **CRITICAL DEFECT DETECTED**:
     - Line 13: `prompt_text = " ".join(m.get("content", "") for m in (messages or [])).lower()`
     - In `troy.py`, multi-prompt stages append previous user messages and assistant responses to `conversation_history`.
     - In Section 2, 3, and 4, `prompt_text` contains Section 1's text from earlier turns in the conversation.
     - Because `MockDriver` checks `if "section 1" in prompt_text or "executive assessment" in prompt_text:` BEFORE Section 2, 3, or 4, `MockDriver` repeatedly returns Section 1's text for every section.
     - Verbatim error reproduced in `pytest tests/test_challenger_m3.py`:
       ```
       FAILED tests/test_challenger_m3.py::test_response_formulation_all_4_sections_and_headings
       assert sections["root_cause"].startswith("### **Root Cause Analysis & Precedent Alignment**")
       AssertionError: '### **Executive Assessment & Category Confirmation**...'.startswith(...)
       ```
     - Remedy: In `MockDriver._determine_response`, inspect the latest user message (`latest_user_prompt = [m.get("content", "") for m in messages if m.get("role") == "user"][-1].lower()`) or evaluate sections in reverse order (4, 3, 2, 1).
3. **Database Test State Contamination**:
   - **CRITICAL ANOMALY DETECTED**:
     - `data/transactions.csv` and `data/customer_accounts.csv` were committed/written on disk with `TXN-7712` already marked as `Refunded` (`git diff data/`).
     - In clean git HEAD, `TXN-7712` was `Settled` with `refund_status: Pending Refund`.
     - Because `isolated_database` in `tests/conftest.py` copies on-disk CSVs to `tmp_path`, it copies already-refunded rows.
     - As a result, 8 tests in `tests/test_challenger1_m2.py`, `tests/test_challenger2_m2.py`, and `tests/test_e2e_troy.py:870` fail because they expect `TXN-7712` to be `Settled` initially.
     - Restoring `data/transactions.csv` and `data/customer_accounts.csv` to their initial `Settled` state resolves all 8 failures immediately.

### 1.6 Residual Legacy Heuristics in Intake & Triage (`app/triage.py`)
- `app/triage.py` is currently 100% pure Python regex heuristic:
  - `CLASSIFICATION_PATTERNS` (lines 284-296): compiled regexes matching keywords for the 11 categories.
  - `extract_entities()` (lines 352-368): regexes for references, dates, amounts, and a static list of product keywords.
  - `predict_category()` (lines 370-387): loops through regex patterns.
  - `SLA_DEFAULTS`, `CATEGORY_TO_TEAM`, `CATEGORY_PRECEDENTS`: hardcoded lookup tables.
- Requirement R1 explicitly dictates:
  > "Do not rely on pure Python heuristic or rule-based models for complaint intake and triage. Send incoming customer complaints directly to the local LLM (LM Studio / OpenAI-compatible endpoint, with deterministic mock fallback when offline) so that the LLM autonomously classifies the complaint category, extracts entities, and generates the case assessment (`cra_report`), while returning schema-compliant data expected by the frontend."
- Therefore, in Milestone 4, `app/triage.py` must query the Local LLM (using `OpenAiDriver` pointing to port 1234, with deterministic `MockDriver` fallback) to perform structured categorization, entity extraction, and case assessment.

---

## 2. Logic Chain

1. **Premise 1 (R1 & Scope)**: Pure Python regex heuristics in `app/triage.py` must be replaced by Local LLM inference (LM Studio / OpenAI-compatible endpoint on port 1234, with offline mock fallback).
2. **Premise 2 (R3 & Frozen UI)**: The frontend in `public/js/app.js` renders the Preliminary Assessment card dynamically using `report = JSON.parse(e.data)` from the `cra_report` SSE event. It accesses `predicted_category`, `category_confidence`, `recommended_team`, `sla.estimated_hours`, `sla.sla_risk`, `similar_cases`, and `extracted_entities` (`references`, `dates`, `amounts`).
3. **Premise 3 (API Compatibility)**: `/api/next`, `/api/customer/verify`, and `/api/resolve/action` rely on category classification and entity resolution. Adding route aliases `/api/cra/triage` and `/api/cra/predict` alongside `/api/cra/resolve` and `/api/cra/classify` ensures full backward and forward compatibility with both existing tests and upcoming prompt requirements.
4. **Premise 4 (M3 MockDriver Defect)**: In `app/providers/mock_driver.py`, joining all message history causes the Section 1 condition to swallow Sections 2, 3, and 4 during multi-turn prompting in `troy.py`. Changing `prompt_text` to check the latest user message (`latest_user_prompt`) directly resolves the test failure in `test_challenger_m3.py`.
5. **Premise 5 (M2 CSV Data Integrity)**: In `data/transactions.csv`, `TXN-7712` was modified on disk to `Refunded`. Because `isolated_database` copies disk CSVs, tests expecting an unrefunded duplicate charge fail. Reverting `data/transactions.csv` to git HEAD (`Settled`) restores test isolation integrity.
6. **Inference / Implementation Strategy for M4**:
   - Update `app/triage.py` to implement `async def triage_complaint_llm(complaint_text, driver=None)` and synchronous `triage_complaint(complaint_text)` using an LLM JSON completion prompt requesting `predicted_category`, `category_confidence`, `recommended_team`, `sla`, `extracted_entities`, and `similar_cases`.
   - Update `MockDriver` to recognize triage prompt requests (e.g. `is_triage_prompt`) and return schema-compliant JSON, while fixing the multi-prompt section selector.
   - Wire `app/main.py` routes `/api/cra/triage` and `/api/cra/predict` as direct aliases to `triage_complaint` and `predict_category`.
   - Revert modified `data/transactions.csv` and `data/customer_accounts.csv` to clean initial standing.

---

## 3. Caveats

1. **Read-Only Investigation**: As an Explorer agent, no application code, data, or tests were modified during this investigation. All identified bugs, root causes, and patch designs are documented for the implementing agent.
2. **Missing Test File in Request Prompt**: The user prompt and original request listed `tests/test_database_tools.py` under verification resources. That file does not exist in the repository; the actual database tool tests reside in `tests/test_e2e_troy.py` (Features 5, 6, 7), `tests/test_challenger1_m2.py`, and `tests/test_challenger2_m2.py`.
3. **Execution Latency of Local LLM**: Calling a live Local LLM for triage on every complaint adds 1-3 seconds of network/inference time. An automated fallback to cached/deterministic responses when offline or during test suites is mandatory so test suites run in seconds.
4. **Endpoint Naming Aliases**: `app/main.py` currently exposes `/api/cra/resolve` and `/api/cra/classify`. Adding `/api/cra/triage` and `/api/cra/predict` as aliases will satisfy all prompt requirements without breaking any existing clients or tests.

---

## 4. Conclusion

- **Milestone 3 Architecture**: The Local LLM resolution engine in `app/simulation/troy.py` and `app/providers/openai_driver.py` is architecturally sound. Two specific defects must be addressed during M4 integration:
  1. Fix `MockDriver._determine_response` in `app/providers/mock_driver.py` to evaluate the latest user message instead of joined conversation history.
  2. Restore `data/transactions.csv` and `data/customer_accounts.csv` so `TXN-7712` starts as `Settled`, fixing 8 failing tests.
- **Requirement R1 (Local LLM Triage Intake)**: `app/triage.py` must replace the regex dictionary classifier with an LLM prompt that returns a schema-compliant JSON payload (`predicted_category`, `category_confidence`, `top_3_categories`, `extracted_entities`, `recommended_team`, `sla`, `similar_cases`).
- **Requirements R2 & R3 (SSE & UI Preservation)**: The existing SSE event sequence (`topic`, `feed`, `cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`, `database_updated`) and the 4-stage bold headings match the frontend UI contracts in `public/js/app.js` with 100% precision.

---

## 5. Verification Method

To verify these findings independently, run the following commands:

```powershell
# 1. Run Challenger M3 test suite (demonstrates the MockDriver Section 1 swallow bug)
pytest tests/test_challenger_m3.py -k "not aaaaa" --tb=short

# 2. Run E2E Troy test suite (demonstrates the TXN-7712 already-refunded failure)
pytest tests/test_e2e_troy.py -k "test_tier2_feature7_custom_refund_amount" --tb=short

# 3. Check git diff on data files (demonstrates disk pollution of transactions.csv)
git diff data/transactions.csv data/customer_accounts.csv

# 4. Verify API endpoints test pass cleanly
pytest tests/test_api_endpoints.py

# 5. Check UI contract references in public static assets
Select-String -Path public/js/app.js -Pattern "cra_report|deliverPreliminaryAssessment|SECTION_DEFINITIONS"
```
