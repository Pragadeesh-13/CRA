# Handoff Report: Survey Explorer 2 (Backend, Troy LLM Engine, & UI Contracts)

## 1. Observation
1. **Web Framework & Entrypoint**:
   - `run.py:23-32`: Starts a Uvicorn ASGI server with `uvicorn.run("app.main:app", host="0.0.0.0", port=target_port, reload=True)` after checking port availability via `is_port_in_use`.
   - `app/main.py:73`: Declares FastAPI application `app = FastAPI(title="Agentic complaint resolution system", version="3.0.0", lifespan=lifespan)`.
   - `app/main.py:653`: Mounts static frontend: `app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")`.

2. **Frontend UI Contracts & Static Files (`public/`)**:
   - `public/js/app.js:2272-2276`: Calls `POST /api/next` with JSON `{ query, provider, model, apiKey }` to begin resolution.
   - `public/js/app.js:2307`: Opens Server-Sent Events stream `new EventSource(streamUrl)` to `/api/stream`.
   - `public/js/app.js:2314-2478`: Listens to SSE event types: `open`, `topic`, `ticket_summarizing`, `policy_summarizing`, `feed`, `resolution_start`, `council_start`, `system`, `cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`, `council_end`, `error`.
   - `public/js/app.js:2070-2143`: `deliverPreliminaryAssessment(report)` specifically expects `cra_report` payload fields: `predicted_category`, `category_confidence`, `recommended_team`, `sla.estimated_hours`, `sla.sla_risk`, `similar_cases`, `extracted_entities`.
   - `public/js/app.js:972-1009`: `SECTION_DEFINITIONS` defines 4 dedicated chatbox stages:
     - `part1` (`executive`): "Executive Assessment & Category Confirmation" (`troy-card` / `troy-speech`)
     - `part2` (`root_cause`): "Root Cause Analysis & Precedent Alignment" (`troy-card-root-cause` / `troy-root-cause-speech`)
     - `part3` (`customer_comms`): "Customer Communication Strategy" (`troy-card-communication` / `troy-communication-speech`)
     - `part4` (`internal_routing`): "Internal Routing & Next Action Steps" (`troy-card-routing` / `troy-routing-speech`)
     Each section must begin with a bold markdown heading `### **<Title>**`.
   - `public/js/app.js:1214-1218`: Fetches `GET /api/customer/verify?query=...&category=...` immediately upon resolution completion to render the customer ledger card (`customer-verify-strip`).
   - `public/js/app.js:1445-1455` and `1601-1616`: Calls `POST /api/resolve/action` with `{ action, customer_id, query, category, action_details, notes }` when clicking "Resolve Manually" or "Let agent resolve it".

3. **Current Provider & LM Studio Configuration**:
   - `app/providers/__init__.py:21`: Current configuration is hardcoded to port 1235:
     ```python
     PROVIDER_BASE_URLS: Dict[str, str] = {
         ...
         "lmstudio": "http://localhost:1235/v1",
     }
     ```
     This does not match LM Studio's standard port 1234 (`http://localhost:1234/v1`) and lacks environment variable support.
   - `app/config.py:18`: `llm_provider: str = Field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openrouter").lower())`.
   - `app/providers/openai_driver.py:108-110`: OpenAiDriver defaults base URL to `http://localhost:1235/v1` when provider is `"lmstudio"`.

4. **Legacy ML Components & Dependencies**:
   - `requirements.txt:7-11`: Contains heavy ML packages: `numpy`, `pandas`, `torch`, `transformers`, `sentence-transformers`.
   - `app/main.py:60-66`: Startup lifespan triggers background warmup of ML models:
     ```python
     from app.cra import cra_engine
     asyncio.create_task(asyncio.to_thread(cra_engine.warmup))
     ```
   - `app/main.py:210, 277, 587, 607`: Imports `cra_engine` for category prediction and report generation.
   - `app/simulation/troy.py:245-249`: Directly invokes `cra_engine.generate_intelligence_report(complaint_text)` during pipeline execution.
   - `CRA_Models/`: Contains DistilBERT tokenizer/config files, confusion matrices, and XGBoost regression JSONs.
   - `app/cra/engine.py:1-386`: 386 lines implementing PyTorch DistilBERT loading, sentence-transformers, FAISS vector dot product, and pandas CSV reading.

5. **Existing Database & Resolution Tools**:
   - `app/database.py:20-21`: Defines `DATABASE_FILE = DATA_DIR / "transactions.csv"` and `CUSTOMER_ACCOUNTS_FILE = DATA_DIR / "customer_accounts.csv"`.
   - `app/database.py:401-555`: `analyze_customer_transactions` audits billing cycles, identifies duplicate settled transactions (`TXN-9012`), detects whether already refunded under `REF-88219-AUTO`, and constructs structured step-by-step progress metadata.
   - `app/database.py:909-1070`: `execute_agent_resolution` updates `data/transactions.csv` and `data/customer_accounts.csv`, changing status to `Refunded`, setting `refund_id`, and reconciling account balance to `$0.00`.
   - `app/database.py:557-907`: `get_category_complaint_context` provides structured diagnostic contexts across all 11 categories without modifying transaction records for non-refund categories.

6. **Current Test Suite**:
   - Execution of `pytest` completed in 15.81s with `89 passed, 3 warnings`.
   - `tests/test_api_endpoints.py` tests health, config, lmstudio driver configuration, customer verify, database transactions, and resolve actions.
   - `tests/test_category_resolutions.py` imports `cra_engine` and verifies all 11 categories.
   - `tests/test_mock_simulation.py` verifies mock driver execution and SSE event ordering.

---

## 2. Logic Chain
1. *From Observation 2*: The frontend in `public/` cannot be modified (Requirement R4). It relies strictly on specific API routes (`/api/next`, `/api/stream`, `/api/customer/verify`, `/api/resolve/action`), exact SSE event names (`cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`), and markdown bold headers `### **<Title>**` on each of the 4 resolution sections.
2. *From Observation 4*: Legacy ML components in `app/cra` and `CRA_Models` must be removed along with dependencies in `requirements.txt` (`torch`, `transformers`, `sentence-transformers`, `numpy`, `pandas`) per Requirement R1.
3. *From Observations 1, 2, and 4*: Removing `app/cra` without replacing its output structure would break the SSE `cra_report` event and `/api/customer/verify`, causing the UI's Preliminary Case Assessment card and ledger verification card to crash or fail to render. Therefore, Troy must synthesize the case assessment payload (category, confidence, entities, team, SLA) autonomously or via lightweight deterministic domain rules matching the 11 CRA categories.
4. *From Observation 3*: LM Studio is configured with port 1235 instead of port 1234. Updating `PROVIDER_BASE_URLS["lmstudio"]` and `OpenAiDriver._default_base_url()` to default to `http://localhost:1234/v1` and reading `os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")` satisfies Requirement R2.
5. *From Observations 2 and 6*: Requirement R2 requires an automated mock/stub fallback mode so test suites can run deterministically when the local LLM server is offline. When LM Studio is unreachable (connection refused/timeout), Troy must seamlessly catch the network exception and fall back to deterministic title/summary generation and the 4-section resolution fallback templates (`_build_category_fallback`).
6. *From Observations 2 and 5*: For database-related complaints (e.g. Scenario 1 duplicate charge for Alex Morgan Order #88219), Troy must invoke `analyze_customer_transactions` to audit `data/transactions.csv`, locate `TXN-9012`, execute the database remediation (updating status to `Refunded` and account balance to settled `$0.00`), and formulate a customer reassurance response. For general technical inquiries (e.g. Scenario 3 2FA SMS timeout, Scenario 4 webhook timeout), Troy must provide direct guidance and root-cause explanation without touching `data/transactions.csv`.

---

## 3. Caveats
1. No source code files in `app/`, `public/`, or `tests/` were modified during this investigation, strictly preserving read-only explorer status.
2. Live connection to an active LM Studio desktop application on port 1234 was not tested because LM Studio was not running in the test environment; however, the `OpenAiDriver` protocol and HTTP client implementation were verified against OpenAI-compatible REST specifications.
3. The mock/stub fallback mode must produce token chunks via SSE streaming when streaming is requested so that UI typing animations execute naturally during offline testing.

---

## 4. Conclusion
The backend architecture is cleanly structured around FastAPI and SSE streaming, and the UI contracts are well-defined and stable. The migration from legacy machine learning models to the local LLM Troy resolution engine can be accomplished with complete backward compatibility:
- **ML Removal**: Delete `CRA_Models/`, delete `app/cra/`, and remove heavy ML dependencies (`torch`, `transformers`, `sentence-transformers`, `numpy`, `pandas`) from `requirements.txt`.
- **Troy Local LLM Engine**: Configure default endpoint `http://localhost:1234/v1` with env var configurability (`LMSTUDIO_BASE_URL`), and implement an automated mock/stub fallback in `Troy.run()` and `Troy.summarise_ticket()` that catches connection errors and yields deterministic resolution sections and intake reports.
- **Autonomous Decision Flow**: Troy inspects complaint keywords and entities to route between direct customer responses (technical/informational) and database inspection/remediation tools (invoking `analyze_customer_transactions` and updating `data/transactions.csv` for duplicate charges).
- **UI Preservation**: Keep `public/` files 100% untouched while fulfilling all expected API and SSE payloads.

---

## 5. Verification Method
To independently verify these findings:
1. **Inspect UI Contracts**:
   - Read `public/js/app.js:1214-1265` to confirm the `/api/customer/verify` request query and expected ledger fields (`customer_name`, `duplicate_transaction`, `is_already_refunded`, `status_pill`).
   - Read `public/js/app.js:2360-2369` and `2070-2143` to confirm the `cra_report` SSE event structure for the preliminary assessment card.
   - Read `public/js/app.js:972-1009` to confirm the 4 multi-prompt stage IDs, card IDs, and speech IDs.
2. **Inspect Current LM Studio Port**:
   - View `app/providers/__init__.py:21` and `app/providers/openai_driver.py:108-110` to confirm current port 1235 setting.
3. **Inspect ML Couplings**:
   - View `app/main.py:60-66, 210, 277, 587, 607` and `app/simulation/troy.py:245-249` to confirm all imports from `app.cra`.
4. **Run Test Suite**:
   - Execute `pytest` to confirm all 89 current tests execute cleanly.
