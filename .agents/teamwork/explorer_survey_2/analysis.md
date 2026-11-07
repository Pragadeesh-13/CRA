# Detailed Backend, Troy LLM Engine, & UI Contract Analysis

## 1. Executive Summary
This analysis details the backend architecture, web framework, API routing, server-sent event (SSE) streaming protocols, frontend UI contracts, local LLM engine integration (OpenAI-compatible LM Studio endpoint), and autonomous decision flow for the Complaint Resolution Agent (CRA) Troy. 

The investigation confirms that:
1. The web backend is built with FastAPI and Uvicorn, serving API routes under `/api/*` and mounting the unmodified static frontend from `public/`.
2. The frontend in `public/` strictly communicates with the backend via specific REST endpoints and an SSE stream (`/api/stream`), expecting exact event types (`topic`, `feed`, `cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`) and structured JSON schemas for customer verification and ledger remediation cards.
3. The current LM Studio provider configuration has a port discrepancy (`http://localhost:1235/v1` vs default `http://localhost:1234/v1`) and lacks environment variable configurability.
4. Legacy ML dependencies in `requirements.txt` (torch, transformers, sentence-transformers) and artifacts in `CRA_Models` and `app/cra` can be cleanly decoupled while maintaining full UI compatibility by having Troy's local LLM engine and deterministic fallback produce the expected `cra_report` schema.
5. Troy's autonomous decision flow can cleanly differentiate between database-related complaints (e.g. duplicate charges requiring inspection and remediation in `data/transactions.csv`) and general technical/account inquiries requiring direct customer responses.

---

## 2. Backend Architecture & Route Inventory

### 2.1 Framework & Lifecycle
- **Framework**: FastAPI (version 3.0.0 declared in `app/main.py:73`), executed via `uvicorn.run("app.main:app")` in `run.py`.
- **Port Management**: `run.py` dynamically probes `settings.port` (default 3000) using socket binding checks and auto-allocates an alternative port if in use.
- **Lifespan Manager**: `app/main.py:58-71` manages startup and shutdown. Currently launches `cra_engine.warmup` on startup and closes `session_registry`, `sse_manager`, and `key_vault` on shutdown.
- **Session & Auth Middleware**: `auth_session_middleware` (`app/main.py:86-97`) inspects protected paths using `SessionGuard(mode=settings.auth_mode)`. Supports `optional`, `none`, and `required` modes.
- **Concurrency & State**: Singletons `SimulationState` (atomic execution locking), `RoundBarrier` (turn synchronization), and `SseManager` (fan-out broadcast queues).

### 2.2 Complete Backend Route Inventory

| Route | HTTP Method | Handler Location | Purpose / Data Flow |
|---|---|---|---|
| `/health`, `/api/health` | GET | `app/main.py:134-144` | Health check returning status, uptime, auth configuration |
| `/api/stream` | GET | `app/main.py:148-193` | Server-Sent Events (SSE) streaming endpoint for real-time resolution logs |
| `/api/config` | GET, POST | `app/main.py:394-449` | Reads and updates active LLM provider, model, API key, and showReasoning setting |
| `/api/models` | GET | `app/main.py:451-464` | Returns available models for a given provider (fetches live or returns curated) |
| `/api/next`, `/api/ticket/next` | POST | `app/main.py:329-390` | Initiates complaint resolution; triggers async background task `_run_flow` |
| `/api/ticket`, `/api/policy`, `/api/cra/ticket` | GET | `app/main.py:197-204` | Retrieves latest ticket/story details |
| `/api/customer/verify` | GET | `app/main.py:206-252` | Verifies customer account and transaction records for UI ledger audit card |
| `/api/resolve/action` | POST | `app/main.py:262-294` | Executes agent or manual remediation; updates CSV ledger and broadcasts update |
| `/api/database/transactions` | GET | `app/main.py:254-260` | Returns all records from `data/transactions.csv` |
| `/api/database/customers` | GET | `app/main.py:255-260` | Alias returning transaction records |
| `/api/trigger` | GET | `app/main.py:296-316` | Triggers a manual resolution run via query parameters |
| `/api/round/begin` | POST | `app/main.py:318-327` | Marks round synchronization barrier |
| `/api/auth/firebase-config` | GET | `app/main.py:467-477` | Returns Firebase Web API credentials |
| `/api/auth/signup` | POST | `app/main.py:479-496` | Validates registration fields; returns 201 |
| `/api/auth/login` | POST | `app/main.py:498-519` | Local session authentication, sets `__session` cookie |
| `/api/auth/google` | POST | `app/main.py:521-545` | Firebase ID token exchange for session cookie |
| `/api/auth/me` | GET | `app/main.py:548-559` | Current user profile details |
| `/api/auth/keys` | DELETE | `app/main.py:561-566` | Deletes stored provider API keys from vault |
| `/api/cra/resolve` | POST | `app/main.py:570-590` | Legacy ML intelligence report endpoint |
| `/api/cra/classify` | POST | `app/main.py:592-610` | Legacy DistilBERT classification endpoint |
| `/`, `/index.html`, `/login.html`, `/signup.html` | GET | `app/main.py:618-653` | Static file serving and redirection based on auth status |

---

## 3. UI Contracts & Frontend Compatibility (`public/`)

The frontend UI in `public/` is strictly read-only and must not be altered. The backend must adhere to its exact expected contracts.

### 3.1 Static Assets Map
- `public/index.html`: Main CRA dashboard containing:
  - Header with `topic-pill` ("Current Complaint: ...") and user profile dropdown.
  - Left panel: Provider selector (`provider-select`: openrouter, openai, anthropic, groq, lmstudio), model selector (`model-select` dropdown or `model-input` text box for LM Studio), API key field, "Begin Resolution" button (`fetch-next`), dataset category preset pills (`category-preset-pills`), and complaint input textarea (`topic-input`).
  - Right panel: Deliberation stage (`deliberation-list`) starting with Troy's initial card (`troy-card`).
- `public/js/app.js`: 2647 lines implementing:
  - Configuration synchronization with `/api/config`.
  - SSE connection handling with auto-reconnect.
  - Multi-prompt section splitting and chatbox rendering.
  - Dynamic customer verification card rendering (`renderResolutionActionButtons`).
  - Interactive manual vs agent action execution (`POST /api/resolve/action`).
- `public/js/firebase-auth.js`, `login.js`, `signup.js`: Authentication state management.

### 3.2 SSE Event Stream Contracts (`/api/stream`)
When a resolution is initiated via `POST /api/next`, the frontend connects to `/api/stream` and listens for the following event sequence:

1. **`status`**: Emits initial connection and provider status:
   ```json
   {"message": "Waiting for resolution signal.", "provider": "lmstudio", "model": "qwen/qwen3.5-9b", "connection": "connected"}
   ```
2. **`ticket_summarizing` / `policy_summarizing`**: Emits complaint query currently being summarized:
   ```json
   {"query": "User complaint text..."}
   ```
3. **`topic`**: Updates the topbar complaint title:
   ```json
   {"title": "Duplicate Subscription Charge Dispute"}
   ```
4. **`feed`**: Renders the active case card in the left feed list:
   ```json
   {
     "title": "Duplicate Subscription Charge Dispute",
     "source": "Customer Ticket",
     "url": "",
     "publishedAt": "2026-11-06T21:00:00Z",
     "description": "Full complaint text...",
     "summary": "Objective 1-2 sentence issue summary..."
   }
   ```
5. **`resolution_start` / `council_start`**: Signals that Troy has begun deliberation:
   ```json
   {"story": {"title": "..."}, "agent": "Troy"}
   ```
6. **`round_start`**: Stage counter initialization:
   ```json
   {"round": 0, "label": "Resolution", "count": 1, "order": ["Troy"]}
   ```
7. **`system`**: Informational log messages displayed in the status ticker:
   ```json
   {"message": "Processing Complaint Ticket: ..."}
   ```
8. **`cra_report`**: **Critical Contract**. Emits structured case intake intelligence used by `deliverPreliminaryAssessment()` (`app.js:2070-2143`) to render the Preliminary Case Assessment card:
   ```json
   {
     "complaint": "Complaint text...",
     "predicted_category": "Refunds",
     "category_confidence": 0.98,
     "top_3_categories": [
       {"category": "Refunds", "score": 0.98},
       {"category": "Billing", "score": 0.02}
     ],
     "extracted_entities": {
       "references": ["ORD-88219"],
       "dates": ["Oct 20, 2026"],
       "amounts": ["$49.00"],
       "products": ["Pro Tier"]
     },
     "similar_cases": [
       {
         "ticket_id": 7712,
         "description": "Precedent Case: Duplicate renewal debit refunded autonomously by Troy",
         "category": "Refunds",
         "resolution": "Duplicate renewal debit refunded autonomously by Troy",
         "assigned_team": "Billing",
         "resolution_time_hours": 3.5,
         "similarity": 0.99
       }
     ],
     "recommended_team": "Billing",
     "team_confidence": "High",
     "team_method": "Rule-based",
     "sla": {
       "estimated_hours": 3.5,
       "sla_target_hours": 24.0,
       "sla_risk": "Low"
     }
   }
   ```
9. **`agent_start`**: Emitted prior to generating each of the 4 dedicated resolution sections:
   ```json
   {
     "agent": "Troy",
     "round": 0,
     "state": "Assigned: Billing",
     "section": "executive",
     "section_index": 0,
     "section_title": "Executive Assessment & Category Confirmation",
     "card_id": "troy-card",
     "speech_id": "troy-speech",
     "show_reasoning": false
   }
   ```
10. **`agent_delta`**: Streams token chunks for the active section:
    ```json
    {
      "agent": "Troy",
      "round": 0,
      "delta": "Text token chunk...",
      "section": "executive",
      "section_index": 0,
      "card_id": "troy-card",
      "speech_id": "troy-speech"
    }
    ```
11. **`agent`**: Emitted upon completing each section:
    ```json
    {
      "agent": "Troy",
      "round": 0,
      "state": "Assigned: Billing",
      "message": "### **Executive Assessment & Category Confirmation**\n\nFull section text...",
      "reasoning": "",
      "section": "executive",
      "section_index": 0,
      "section_title": "Executive Assessment & Category Confirmation",
      "card_id": "troy-card",
      "speech_id": "troy-speech"
    }
    ```
    *Formatting Requirement*: The first line of each section MUST be a bold heading `### **<Section Title>**`.
12. **`resolution_end` / `council_end`**: Emitted when all 4 sections finish:
    ```json
    {
      "summary": "Complaint ticket categorized as ... and routed to ...",
      "category": "Refunds",
      "confidence": 98.0,
      "team": "Billing",
      "sla_hours": 3.5,
      "cra_report": { ... },
      "full_resolution": "Concatenated resolution text...",
      "sections": {
        "executive": "...",
        "root_cause": "...",
        "customer_comms": "...",
        "internal_routing": "..."
      },
      "impacts": {
        "positive": ["Category: Refunds", "Team: Billing", "SLA: 3.5h"],
        "negative": []
      }
    }
    ```
13. **`database_updated`**: Broadcast whenever the database CSV is updated by an action execution.

### 3.3 Customer Verification & Ledger Audit Cards (`GET /api/customer/verify`)
Immediately upon resolution completion, `app.js:1214` queries `GET /api/customer/verify?query=<query>&category=<cat>`.

The returned payload structure determines what ledger card is rendered:
- **Refund Domain (`is_refund: true` or `category: "Refunds"`)**:
  - Requires: `customer_name`, `customer_id`, `customer_email`, `subscription_plan`, `billing_cycle`, `charges_this_month`, `total_billed_this_month`, `duplicate_transaction` (`transaction_id`, `amount`, `status`), `retained_transaction` (`transaction_id`, `amount`, `status`), `is_already_refunded`, `refund_id`, `refunded_at`, `status_pill`, `system_label`, `database_file` (`"data/transactions.csv"`), `action_btn_text` (`"Let agent resolve it"` or `"Verify & Reconfirm Refund"`), `steps` list, and `customer` object.
- **Non-Refund Domains (`is_refund: false`)**:
  - Requires: `category`, `customer_id`, `customer_name`, `customer_email`, `subscription_plan`, `reference_id`, `system_label`, `database_file`, `status_pill`, `action_btn_text`, `system_details`, `steps`, and `customer` object with `account_balance: "$0.00 (Current)"`.

### 3.4 Action Execution Contract (`POST /api/resolve/action`)
When the user clicks the action button ("Let agent resolve it" or "Resolve Manually"):
- Request body:
  ```json
  {
    "action": "agent_resolve", // or "manual_resolve"
    "customer_id": "USR-10492",
    "query": "Complaint text",
    "category": "Refunds",
    "action_details": "Refund Initiated ($49.00 via Stripe API)",
    "notes": "Resolution notes"
  }
  ```
- Response body:
  ```json
  {
    "ok": true,
    "action": "agent_resolve",
    "category": "Refunds",
    "already_refunded": false,
    "is_already_refunded": false,
    "refund_id": "REF-88219-AUTO",
    "refunded_transaction": { "transaction_id": "TXN-9012", "status": "Refunded", ... },
    "retained_transaction": { "transaction_id": "TXN-9011", "status": "Settled", ... },
    "duplicate_transaction": { "transaction_id": "TXN-9012", ... },
    "customer": {
      "customer_id": "USR-10492",
      "customer_name": "Alex Morgan",
      "customer_email": "alex.morgan@gmail.com",
      "subscription_plan": "Pro Tier ($49.00/mo)",
      "status": "Resolved",
      "account_status": "Active - In Good Standing",
      "refund_status": "Refunded ($49.00 via Stripe API)",
      "refund_id": "REF-88219-AUTO",
      "refunded_at": "2026-11-06 21:00:00",
      "account_balance": "$0.00 (Settled)",
      "customer_notified": "Yes - Confirmation Dispatched (REF-88219-AUTO)"
    },
    "ticket": {
      "ticket_id": "88219",
      "order_id": "ORD-88219",
      "customer_name": "Alex Morgan",
      "status": "Resolved",
      "resolution_action": "Transaction TXN-9012 Refunded ($49.00 via Stripe Gateway API)",
      "refund_id": "REF-88219-AUTO",
      "customer_notified": "Yes - Email Dispatched (REF-88219-AUTO)"
    },
    "database_file": "data/transactions.csv",
    "timestamp": "2026-11-06 21:00:00"
  }
  ```

---

## 4. Architecture of Troy Resolution Engine

### 4.1 Local LLM Connection (OpenAI-Compatible Endpoint)
- **Local Endpoint Specification**:
  - OpenAI-compatible chat completion URL: `POST /v1/chat/completions`.
  - Default LM Studio endpoint: `http://localhost:1234/v1`.
  - Current codebase issue: `app/providers/__init__.py:21` currently defines `"lmstudio": "http://localhost:1235/v1"`, which points to port 1235 instead of port 1234.
  - Configuration: Must support environment variables such as `LMSTUDIO_BASE_URL` or `LOCAL_LLM_URL` with fallback to `http://localhost:1234/v1`.
- **Driver Integration (`OpenAiDriver`)**:
  - Base URL normalization: `normalize_base_url` handles stripping `/v1` before constructing `{base}/v1/chat/completions`.
  - Request format:
    ```json
    {
      "model": "qwen/qwen3.5-9b",
      "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
      "temperature": 0.7,
      "max_tokens": 1024,
      "stream": true
    }
    ```
  - Reasoning support: Handles both native `delta.reasoning_content` (DeepSeek-R1 / Qwen on LM Studio) and inline `<think>...</think>` tags using `ThoughtBuffer`.

### 4.2 Automated Mock/Stub Fallback Mode
Requirement R2 explicitly mandates:
> "Provide an automated mock/stub fallback mode so that test suites can execute and verify agent flows when the local server is offline."

To achieve this:
1. **Connection Resilience**: When Troy attempts to communicate with LM Studio (`http://localhost:1234/v1`) and the endpoint is offline (e.g. `httpx.ConnectError`, `httpx.ConnectTimeout`, `httpx.TimeoutException`, connection refused):
   - Rather than throwing an unhandled exception or emitting "Signal lost", the engine immediately engages deterministic fallback mode.
2. **Deterministic Processing Components in Fallback**:
   - **Ticket Summarization**: `_title_fallback(text)` and `_summary_fallback(text)` extract domain-intelligent titles and summaries without requiring LLM inference.
   - **Intake Intelligence (`cra_report`)**: Deterministic extraction of complaint category (across the 11 classes), confidence, entities, team routing, and SLA estimation based on keyword and domain heuristics.
   - **4-Section Deliberation**: Deterministic section generation using pre-formulated templates for all 11 categories (`_build_category_fallback` split into `executive`, `root_cause`, `customer_comms`, and `internal_routing`).
   - **Streaming Token Simulation**: In fallback mode, tokens are chunked and streamed via `agent_delta` with slight timing pacing so the frontend UI animations appear realistic and responsive.
3. **Automated Testing Support**: Allows `pytest` test suites to run completely offline in continuous integration or development environments without requiring a live LM Studio process.

---

## 5. Troy Autonomous Decision Flow

Requirement R2 and R3 define Troy's autonomous intelligence:
> "Troy must analyze incoming customer complaints and autonomously determine the appropriate course of action (answering directly or invoking database tools)."

```
Incoming Customer Complaint Ticket
             │
             ▼
┌──────────────────────────────┐
│  Troy Complaint Classifier   │
│   & Intent Analysis Engine   │
└──────────────┬───────────────┘
               │
       Autonomous Decision:
       Does complaint require
       database audit / remediation?
       (Refunds, duplicate debits,
       renewal errors, ledger balance)
               │
        ───────┴───────
       │               │
      YES              NO
       │               │
       ▼               ▼
┌──────────────────┐  ┌──────────────────────────────┐
│ Database Action  │  │ Direct Customer Response     │
│ Pipeline         │  │ Pipeline                     │
├──────────────────┤  ├──────────────────────────────┤
│ 1. Invoke DB     │  │ 1. Diagnose technical/       │
│    Inspection:   │  │    operational root cause    │
│    Inspect       │  │ 2. Formulate empathetic      │
│    transactions  │  │    direct response with      │
│    and accounts  │  │    step-by-step guidance,    │
│ 2. Detect error: │  │    workarounds, or incident  │
│    Locate        │  │    tracking numbers          │
│    redundant     │  │ 3. DO NOT alter or access    │
│    TXN debit     │  │    data/transactions.csv     │
│ 3. Execute DB    │  │ 4. Route ticket to relevant  │
│    Remediation:  │  │    technical queue           │
│    Update status │  │    (Engineering/Security/    │
│    to 'Refunded' │  │    Support)                  │
│    Reconcile $0  │  │                              │
│ 4. Formulate     │  │                              │
│    reassurance   │  │                              │
│    response      │  │                              │
└──────────────────┘  └──────────────────────────────┘
```

### 5.1 Database-Related Complaints (e.g. Duplicate Debits / Refunds)
- **Detection Triggers**: Keywords ("charged twice", "double charge", "duplicate debit", "billed twice", "refund", order numbers like "88219", "81094").
- **Inspection**:
  - Calls `analyze_customer_transactions(customer_id, query)`.
  - Queries `data/transactions.csv` and `data/customer_accounts.csv`.
  - Groups records by customer and billing cycle.
  - Identifies settled duplicate charge (`TXN-9012` for Alex Morgan `USR-10492`).
- **Remediation**:
  - Determines if refund was already executed vs pending.
  - Executes remediation: updates `status` to `Refunded`, sets `refund_id: REF-88219-AUTO`, sets `refund_amount: 49.00`, sets `refunded_at`, records `processed_by: Troy`.
  - Reconciles account balance to `$0.00 (Settled)`.
- **Response**: Formulates definitive response citing transaction IDs, refund ID, settled zero balance, and bank posting expectations (3-5 business days).

### 5.2 Direct Customer Inquiries (e.g. Technical / Informational)
- **Examples**:
  - Android application crash during PDF export (HTTP 500 memory buffer overflow).
  - 2FA authentication SMS carrier gateway delivery timeout.
  - Workspace role permissions and team invitation guidance.
  - Search query database latency (index fragmentation).
  - Webhook delivery 429 rate limit / 504 timeout.
- **Action**:
  - Bypasses transaction database modifications completely.
  - Formulates technical root-cause explanation.
  - Provides direct customer remediation (bypass links, workaround export procedures, interactive guide links).
  - Assigns ticket to appropriate engineering or support queue without touching `data/transactions.csv`.

---

## 6. Migration Plan & Legacy ML Decoupling Recommendations

1. **Remove Heavy ML Dependencies from `requirements.txt`**:
   - Remove: `torch`, `transformers`, `sentence-transformers`, `numpy`, `pandas`.
   - Retain: `fastapi`, `uvicorn[standard]`, `pydantic`, `python-dotenv`, `httpx`, `cryptography`, `pytest`.
2. **Remove ML Model Artifacts & Code**:
   - Delete directory `CRA_Models/`.
   - Delete directory `app/cra/`.
   - Remove `from app.cra import cra_engine` from `app/main.py` and `app/simulation/troy.py`.
3. **Configure Local LLM LM Studio Provider**:
   - Update `app/providers/__init__.py`: set default LM Studio base URL to `http://localhost:1234/v1` and read `os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")`.
   - In `app/config.py`: support `LOCAL_LLM_URL` or `LMSTUDIO_BASE_URL`.
4. **Implement Resilient Fallback in Troy**:
   - Ensure `Troy.run()` and `Troy.summarise_ticket()` catch connection errors to LM Studio and activate deterministic mock/stub fallback transparently.
5. **Preserve Frontend Compatibility**:
   - Generate `cra_report` event payload cleanly using lightweight deterministic domain rules matching the 11 CRA categories.
   - Maintain all SSE events, `GET /api/customer/verify`, and `POST /api/resolve/action` response structures.
