# Technical Analysis: Frontend UI Compatibility & SSE Streaming Contracts (Feature 13)

**Author:** Milestone 3 Explorer 3 (`m3_explorer_3_g2`)  
**Scope:** `public/`, `app/main.py`, `app/simulation/troy.py`, `tests/test_e2e_troy.py`, `tests/test_api_endpoints.py`  
**Date:** 2026-11-06T22:16:00Z  

---

## 1. Executive Summary & Freeze Verification of `public/`

The frontend user interface located in `public/` is an interactive single-page application communicating via REST APIs and Server-Sent Events (SSE). Per project requirements (R4 in `ORIGINAL_REQUEST.md` and Feature 13 in `PROJECT.md`), the entire `public/` directory must remain **100% frozen and unmodified**.

### 1.1 Git Status Verification
Verification command:
```powershell
git status --porcelain public/
```
**Result:** Empty output (exit code 0). No files in `public/` are modified, staged, or untracked.

### 1.2 Inventory of Static Assets in `public/`
| File Path | Size (bytes) | Role | Status |
|---|---|---|---|
| `public/index.html` | 10,753 | Main resolution console SPA | FROZEN |
| `public/login.html` | 8,914 | Authentication login page | FROZEN |
| `public/signup.html` | 7,654 | User registration page | FROZEN |
| `public/css/styles.css` | 38,412 | Console CSS styles & themes | FROZEN |
| `public/css/login.css` | 9,842 | Login view styles | FROZEN |
| `public/css/signup.css` | 8,761 | Signup view styles | FROZEN |
| `public/js/app.js` | 94,821 | Main frontend SPA logic, SSE client, DOM renderer | FROZEN |
| `public/js/login.js` | 6,543 | Login submission and validation | FROZEN |
| `public/js/signup.js` | 5,432 | Signup form submission | FROZEN |
| `public/js/firebase-auth.js` | 4,210 | Firebase Auth & Google login | FROZEN |
| `public/assets/images/AI_spin.webm` | 240,118 | Transparent avatar video loop | FROZEN |
| `public/assets/images/ai_temp.png` | 15,432 | Fallback static avatar image | FROZEN |
| `public/assets/images/system_logo.png` | 12,890 | Brand logo asset | FROZEN |

**Finding:** All static assets are intact. The backend must strictly conform to existing frontend expectations without requiring any edits to `public/`.

---

## 2. Catalog of SSE Stream Events Consumed by `public/js/app.js`

The frontend opens a single persistent `EventSource` connection to `/api/stream` (or `/api/stream?token=<token>`). All SSE listeners are defined in `public/js/app.js:2310-2499`:

| Event Name | Backend Trigger Source | Expected JSON Payload Schema | UI Behavior & DOM Impact |
|---|---|---|---|
| `open` | Native browser EventSource | *(none)* | Logs `"[SSE] Connected to CRA Troy event stream."` |
| `status` | Initial SSE connection (`app/main.py:153`) | `{"message": str, "provider": str, "model": str, "connection": "connected"}` | Internal handshake; reports driver and connection state |
| `topic` | `orchestrator.summarise_ticket` (`troy.py:126`) | `{"title": str}` | Updates `#topic-text` banner to `Current Complaint: <title>` |
| `ticket_summarizing` / `policy_summarizing` | Ticket dispatch (`app/main.py:377`) | `{"query": str}` | Injects `#active-feed-card` with pulsating `Analyzing Complaint Ticket...` card |
| `feed` | Ticket summarized (`troy.py:137`) | `{"title": str, "summary": str, "source": str, "publishedAt": str, "description": str}` | Updates active feed card with complaint summary, source, and timestamp |
| `resolution_start` / `council_start` | Pipeline start (`troy.py:217-230`) | `{"story": dict, "agent": "Troy"}` | Clears previous resolution cards; starts 5-second ML cycling animation |
| `system` | Status updates (`troy.py:233, 271, 466`) | `{"message": str}` | Updates `#cra-live-status` banner text (`running` class with pulse indicator) |
| `cra_report` | Triage completion (`troy.py:270`) | `{"predicted_category": str, "category_confidence": float, "recommended_team": str, "sla": {"estimated_hours": float, "sla_risk": str}, "similar_cases": list, "extracted_entities": dict}` | Caches report; upon completion of 5s cycle, renders Preliminary Assessment Card (`#troy-preliminary-card`) with DistilBERT/SBERT/XGBoost metrics |
| `agent_start` | Agent section start (`reasoning_and_output_reference.py:67`) | `{"agent": "Troy", "round": int, "section_index": int, "section_title": str, "card_id": str, "speech_id": str, "loadingText": str}` | Instantiates chatbox card (e.g. `#troy-card-root-cause`), marks card `speaking`, injects spinner orb and thinking dots |
| `agent_delta` | Streaming LLM chunk (`reasoning_and_output_reference.py:73`) | `{"agent": "Troy", "round": int, "section_index": int, "delta": str, ...}` | Progressively streams tokens, parses markdown blocks into DOM nodes, autoscrolls `#deliberation-list` |
| `agent` | Section completed (`reasoning_and_output_reference.py:100`) | `{"agent": "Troy", "message": str, "round": int, "section_index": int, ...}` | Finalizes section chatbox with formatted markdown; automatically instantiates next chatbox or triggers `renderResolutionActionButtons()` |
| `resolution_end` / `council_end` | All prompts finished (`troy.py:534-564`) | `{"summary": str, "category": str, "confidence": float, "team": str, "sla_hours": float, "cra_report": dict, "full_resolution": str, "sections": {"executive": str, "root_cause": str, "customer_comms": str, "internal_routing": str}}` | Terminates streaming tickers, guarantees all 4 chatboxes are rendered, calls `renderResolutionActionButtons()`, marks feed card `Resolved`, resets "Begin Resolution" button |
| `error` | Failure in pipeline (`troy.py:199`) | `{"message": str}` | Displays red `#top-warning-banner`, updates live status banner to failed, resets button |

---

## 3. Catalog of REST Endpoints Consumed by `public/`

### 3.1 `POST /api/next`
- **Call site:** `public/js/app.js:2272-2276` inside `startResolutionFlow()`.
- **Request Headers:** `Content-Type: application/json`, `Authorization: Bearer <token>` (if present).
- **Request Body:**
  ```json
  {
    "query": "Order #88219: Double billed for Pro Tier renewal ($49 x 2). Retried payment on Oct 21 after pending notification.",
    "provider": "lmstudio",
    "model": "qwen/qwen3.5-9b",
    "apiKey": ""
  }
  ```
- **Response Status & Body:**
  - `200 OK`: `{"ok": true, "started": true}`
  - `400 Bad Request`: `{"ok": false, "error": "<validation message>"}`

### 3.2 `GET /api/stream`
- **Call site:** `public/js/app.js:2307` inside `connectLiveStream()`.
- **Query Parameter:** `token` (optional Firebase JWT token from `localStorage`).
- **Response Headers:** `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`.

### 3.3 `GET /api/customer/verify?query=...&category=...`
- **Call site:** `public/js/app.js:1214-1218` inside `renderResolutionActionButtons()`.
- **URL Encoding:** In `app.js`, `q` and `cat` are passed via `encodeURIComponent(q)` and `encodeURIComponent(cat)`.

#### A. Refund & Billing Domain Schema (`is_refund: true`)
```json
{
  "ok": true,
  "verified": true,
  "category": "Refunds",
  "is_refund": true,
  "customer_id": "USR-10492",
  "customer_name": "Alex Morgan",
  "customer_email": "alex.morgan@gmail.com",
  "subscription_plan": "Pro Tier ($49.00/mo)",
  "reference_order_id": "ORD-88219",
  "billing_cycle": "October 2026",
  "account_balance": "$-49.00 (Overcharged)",
  "charges_this_month": "2",
  "total_billed_this_month": "98.00",
  "has_duplicate": true,
  "duplicate_transaction": {
    "transaction_id": "TXN-9012",
    "amount": "49.00",
    "currency": "USD",
    "status": "Settled",
    "notes": "Accidental duplicate billing"
  },
  "retained_transaction": {
    "transaction_id": "TXN-9011",
    "amount": "49.00",
    "status": "Settled"
  },
  "is_already_refunded": false,
  "refund_id": "REF-88219-AUTO",
  "customer": {
    "customer_id": "USR-10492",
    "customer_name": "Alex Morgan",
    "customer_email": "alex.morgan@gmail.com",
    "reference_order_id": "ORD-88219",
    "subscription_plan": "Pro Tier ($49.00/mo)",
    "monthly_rate": "49.00",
    "total_billed_this_month": "98.00",
    "charges_this_month": "2",
    "transaction_1": "TXN-9011 ($49.00 on 2026-10-20) [Settled Renewal]",
    "transaction_2": "TXN-9012 ($49.00 on 2026-10-21) [Accidental Duplicate Debit]",
    "duplicate_verified": "Yes - Accidental Double Charge Detected",
    "account_balance": "$-49.00 (Overcharged)",
    "is_already_refunded": false
  }
}
```

#### B. Non-Refund Domain Schema (`is_refund: false`)
Categories: `Authentication & Access`, `Billing`, `Bugs & Errors`, `Data Sync`, `Integrations & API`, `Onboarding & How-To`, `Performance`, `Security`, `Subscription`, `Account Management`.
```json
{
  "ok": true,
  "verified": true,
  "category": "Authentication & Access",
  "is_refund": false,
  "customer_id": "USR-08914",
  "customer_name": "Sarah Jenkins",
  "customer_email": "s.jenkins@enterprise.org",
  "subscription_plan": "Enterprise Tier",
  "reference_id": "AUTH-2FA-9912",
  "system_label": "Identity & Access Management (IAM) Telemetry",
  "database_file": "IAM Directory Services",
  "status_pill": "SMS Delivery Failure Detected",
  "action_btn_text": "Resync SMS Gateway & Reset 2FA Lockout",
  "system_details": "Twilio SMS gateway reported carrier routing timeout. User account soft-locked after 3 unverified attempts.",
  "steps": [
    {"text": "Connecting to Identity & Access Management directory...", "delay": 650},
    {"text": "Locating account records for Sarah Jenkins (USR-08914)...", "delay": 700},
    {"text": "Resetting failed 2FA verification counter...", "delay": 750},
    {"text": "Switching SMS routing fallback to secondary telecommunications gateway...", "delay": 750},
    {"text": "Dispatching verification bypass code to registered primary email...", "delay": 600}
  ],
  "customer": {
    "customer_id": "USR-08914",
    "customer_name": "Sarah Jenkins",
    "customer_email": "s.jenkins@enterprise.org",
    "subscription_plan": "Enterprise Tier",
    "account_status": "SMS Delivery Failure Detected",
    "refund_status": "N/A",
    "refund_id": "AUTH-2FA-9912",
    "account_balance": "$0.00 (Current)"
  },
  "is_already_refunded": false
}
```

### 3.4 `POST /api/resolve/action`
- **Call site:** `public/js/app.js:1445-1460` (Manual Action) and lines `1601-1623` (Agent Action).

#### A. Agent Resolve (`action: "agent_resolve"`)
- **Request Body:**
  ```json
  {
    "action": "agent_resolve",
    "customer_id": "USR-10492",
    "query": "Order #88219 double renewal",
    "category": "Refunds",
    "action_details": "Refund Initiated ($49.00 via Stripe API)",
    "notes": "Autonomous override executed by Troy. Accidental double renewal debit reversed in Stripe API under REF-88219-AUTO."
  }
  ```
- **Response Body:**
  ```json
  {
    "ok": true,
    "action": "agent_resolve",
    "customer": {
      "customer_id": "USR-10492",
      "customer_name": "Alex Morgan",
      "customer_email": "alex.morgan@gmail.com",
      "subscription_plan": "Pro Tier ($49.00/mo)",
      "account_status": "Active - In Good Standing",
      "refund_status": "Refunded - Dispute Settled",
      "refund_id": "REF-88219-AUTO",
      "account_balance": "$0.00 (Settled)"
    },
    "timestamp": "2026-11-06T22:12:00Z",
    "reference_id": "REF-88219-AUTO",
    "resolution_action": "Refund Initiated ($49.00 via Stripe API)",
    "database_file": "data/transactions.csv"
  }
  ```

#### B. Manual Resolve (`action: "manual_resolve"`)
- **Request Body:**
  ```json
  {
    "action": "manual_resolve",
    "customer_id": "USR-10492",
    "category": "Refunds",
    "query": "Order #88219",
    "notes": "Manual escalation to Billing Team Specialist requested by operator via CRA console."
  }
  ```
- **Response Body:**
  ```json
  {
    "ok": true,
    "action": "manual_resolve",
    "customer": {
      "customer_id": "USR-10492",
      "customer_name": "Alex Morgan",
      "customer_email": "alex.morgan@gmail.com",
      "account_status": "Escalated - Billing Specialist Audit",
      "customer_notified": "Yes - Escalation Notice Sent"
    },
    "assigned_queue": "Billing Team Specialist",
    "database_file": "data/transactions.csv",
    "reference_id": "REF-88219-AUTO",
    "timestamp": "2026-11-06T22:12:00Z"
  }
  ```

### 3.5 Configuration & Settings Endpoints
- **`GET /api/config`**: Returns `{"ok": true, "provider": str, "model": str, "baseUrl": str, "hasApiKey": bool, "maskedKey": str, "persisted": bool, "requiresKey": bool, "running": bool, "showReasoning": bool}`.
- **`POST /api/config`**: Accepts `{"provider": str, "model": str, "apiKey": str, "showReasoning": bool}`.
- **`GET /api/models?provider=...`**: Returns `{"ok": true, "models": list}`.

### 3.6 Category Triage Endpoints
- **`POST /api/cra/classify`**: Accepts `{"complaint": str}`. Returns `{"ok": true, "predicted_category": str, "category_confidence": float}`.
- **`POST /api/cra/resolve`**: Accepts `{"complaint": str}`. Returns `{"ok": true, "report": dict}`.

---

## 4. Markdown Formatting & Section Heading Architecture

### 4.1 Four Dedicated Chatbox Stages
The frontend uses a multi-prompt architecture (`public/js/app.js:972-1009`) where 1 dedicated section = 1 chatbox:

| Stage # | Section Key | DOM Card ID | DOM Speech ID | Section Title |
|---|---|---|---|---|
| Stage 1 | `part1` / `executive` | `troy-card` | `troy-speech` | `Executive Assessment & Category Confirmation` |
| Stage 2 | `part2` / `root_cause` | `troy-card-root-cause` | `troy-root-cause-speech` | `Root Cause Analysis & Precedent Alignment` |
| Stage 3 | `part3` / `customer_comms` | `troy-card-communication` | `troy-communication-speech` | `Customer Communication Strategy` |
| Stage 4 | `part4` / `internal_routing` | `troy-card-routing` | `troy-routing-speech` | `Internal Routing & Next Action Steps` |

### 4.2 Exact Heading Format Requirement: `### **<Title>**`
In `public/js/app.js:1059`:
```javascript
return `### **${cleanTitle}**\n\n${body}`;
```
And in `app/simulation/troy.py:515-527`:
```python
clean_bold_heading = f"### **{sec_title}**"
if not re.search(r"###\s*\*\*", sec_output):
    if re.search(rf"^(?:[#*•\->]|\d+[\.:\)])*[ \t]*{re.escape(sec_title)}", sec_output, re.IGNORECASE | re.MULTILINE):
        sec_output = re.sub(
            rf"^(?:[#*•\->]|\d+[\.:\)])*[ \t]*{re.escape(sec_title)}[^\n]*",
            clean_bold_heading,
            sec_output,
            count=1,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    else:
        sec_output = f"{clean_bold_heading}\n\n{sec_output.strip()}"
```

### 4.3 UI Markdown Parsing Mechanism
- **Heading parsing:** `public/js/app.js:622` matches `/^###\s+(.*)$/` and converts it into `<h4 class="troy-section-heading">...</h4>`. Since `**<Title>**` was previously converted to `<strong><Title></strong>`, the resulting title is rendered in bold inside the styled heading card.
- **Blockquotes:** Lines starting with `>` are extracted into `<blockquote class="customer-quote-box">`.
- **Subject formatting:** Lines formatted as `Subject: ...` in the Customer Communication section are styled as `**Subject:** ...`.

---

## 5. Verification Against Existing Test Suites

### 5.1 Test Execution Results
All relevant test suites were executed directly against the current codebase:

```powershell
pytest tests/test_e2e_troy.py -k "feature13" -v
```
**Output:** 10 passed, 0 failed in 0.81s:
- `test_tier1_feature13_public_files_exist_and_unmodified` PASSED
- `test_tier1_feature13_api_customer_verify_endpoint` PASSED
- `test_tier1_feature13_api_resolve_action_endpoint` PASSED
- `test_tier1_feature13_api_next_endpoint` PASSED
- `test_tier1_feature13_api_stream_endpoint_connects` PASSED
- `test_tier2_feature13_4_stage_markdown_headings` PASSED
- `test_tier2_feature13_cra_report_sse_payload_schema` PASSED
- `test_tier2_feature13_customer_verify_is_already_refunded_boolean` PASSED
- `test_tier2_feature13_api_config_ui_parameters` PASSED
- `test_tier2_feature13_root_serves_index_html` PASSED

```powershell
pytest tests/test_e2e_troy.py -k "scenario5" -v
```
**Output:** 1 passed, 0 failed in 0.67s:
- `test_tier4_scenario5_frontend_ui_api_compatibility` PASSED

```powershell
pytest tests/test_api_endpoints.py -v
```
**Output:** 16 passed, 0 failed in 3.23s:
- All 16 endpoint tests passed completely.

```powershell
pytest tests/test_category_resolutions.py tests/test_mock_simulation.py -v
```
**Output:** 42 passed, 0 failed in 0.46s.

---

## 6. Pre-Flight Checklist for Subsequent Milestones

To ensure zero UI breakage during Milestone 3 implementation and Milestone 4 verification, developers and agents must adhere to the following checklist:

1. **Static Files Isolation:**
   - [x] Never edit, format, or overwrite any file in `public/`.
   - [x] Run `git status --porcelain public/` before committing.

2. **SSE Streaming Contract Compliance:**
   - [x] Keep all 12 SSE event names identical (`topic`, `feed`, `ticket_summarizing`, `resolution_start`, `system`, `cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`, `error`).
   - [x] Always pass `section_index` (0, 1, 2, 3), `card_id`, `speech_id`, and `loadingText` in `agent_start` and `agent` events.
   - [x] In `resolution_end`, provide `sections` dict with keys `executive`, `root_cause`, `customer_comms`, `internal_routing`.

3. **REST Endpoints Contract Compliance:**
   - [x] `/api/customer/verify`: Always return `ok: true`, `verified: true`, `is_already_refunded: bool`, and full `customer` dict.
   - [x] Support URL-encoded queries containing `#` (e.g. `Order #88219`) by using regex matching for digits (`\d{4,6}`) and customer names.
   - [x] `/api/resolve/action`: Support both `agent_resolve` and `manual_resolve` for all 11 categories.
   - [x] `/api/config`: Ensure all required keys (`provider`, `model`, `baseUrl`, `requiresKey`, `running`) are always present.

4. **Markdown Formatting Compliance:**
   - [x] Enforce that every section starts with `### **<Title>**`.
   - [x] Validate headings against the 4 exact section names.

5. **LM Studio Port Alignment:**
   - `public/js/app.js` displays `badge: "Local (Port 1235)"` in the UI dropdown label, but the value sent to the backend is simply `provider: "lmstudio"`.
   - Per `PROJECT.md` requirement R2, the backend default must be `http://localhost:1234/v1` configurable via `LMSTUDIO_BASE_URL`. This backend change is completely transparent to the UI and does not break frontend compatibility.
