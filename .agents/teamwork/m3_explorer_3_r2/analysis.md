# Milestone 3 Explorer 3 Analysis: Frontend UI Compatibility & SSE Streaming Contracts

**Date:** 2026-11-06  
**Focus Area:** Feature 13 (Frontend UI Compatibility Preservation & Streaming Contracts) & R4  
**Investigated Assets:**  
- `public/index.html` (DOM elements, structure, script imports)  
- `public/js/app.js` (EventSource listeners, REST calls, markdown renderer, chatbox constructors)  
- `public/js/firebase-auth.js`, `public/js/login.js`, `public/js/signup.js` (Auth endpoints)  
- `app/main.py` (FastAPI routes, SSE generator, request handlers)  
- `app/simulation/troy.py` (Troy autonomous resolution agent, multi-prompt loop, SSE broadcast)  
- `app/reference/reasoning_and_output_reference.py` (Streaming reasoning and agent event dispatcher)  
- `app/database.py` (Verification cards, ledger audit, action resolvers)  
- `app/triage.py` (Non-ML intake assessment payload)  
- `tests/test_api_endpoints.py` & `tests/test_e2e_troy.py` (API and contract test suites)  

---

## 1. Executive Summary

A comprehensive, read-only analysis was conducted across all static frontend assets under `public/` and backend API / streaming implementations in `app/`. 
Key conclusions:
1. **Frontend Immutability (R4)**: All frontend files under `public/` are 100% frozen, read-only, and require zero modifications.
2. **Backend Contract Compatibility**: The backend endpoints (`/api/next`, `/api/stream`, `/api/customer/verify`, `/api/resolve/action`, `/api/config`, `/api/models`, and auth routes) are completely functional and structurally aligned with `public/js/app.js`.
3. **Streaming Alignment**: All 14 SSE stream event types listened to by `app.js` (`open`, `topic`, `ticket_summarizing`, `policy_summarizing`, `feed`, `resolution_start`, `council_start`, `system`, `cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`, `council_end`) have direct emitting counterparts in `app/simulation/troy.py`, `app/reference/reasoning_and_output_reference.py`, and `app/main.py`.
4. **4-Stage Markdown Format**: The four-stage markdown headings (`### **<Title>**`) match verbatim between `SECTION_DEFINITIONS` in `app.js` and `section_configs` in `troy.py`.
5. **Minor Optimizations Identified**:
   - Adding `"is_refund": True` explicitly to `analyze_customer_transactions` return payload.
   - Adding top-level `"predicted_sla_hours"` to `cra_report` for optimal display in manual escalation card.
   - Configuring LM Studio default URL to `http://localhost:1234/v1` with `LMSTUDIO_BASE_URL` env override in `app/providers/` (Feature 9).
   - Ensuring `execute_agent_with_reasoning` transparently triggers mock streaming fallback rather than broadcasting `"Signal lost"` when LM Studio is offline (Feature 11).

---

## 2. Catalog of Backend HTTP Endpoints Invoked by Frontend UI

| # | Endpoint | Method | Source File & Line | Invocation Trigger & Purpose | Request Payload | Response Schema Expectations |
|---|---|---|---|---|---|---|
| 1 | `/` / `/index.html` | `GET` | Browser / `app.js:2644` | Initial page load. If `auth_mode == "required"` and unauthenticated, redirects to `/login.html`. | None | HTML document (`text/html`) |
| 2 | `/api/config` | `POST` | `public/js/app.js:290` | Triggered when user selects provider, model, or enters API key in UI header. | JSON: `{"provider": str, "model": str, "apiKey": str}` | JSON: `{"ok": bool, "provider": str, "model": str, "baseUrl": str, ...}` |
| 3 | `/api/config` | `GET` | `public/js/app.js:420` | Triggered on page bootstrap to populate provider dropdown and input fields. | Headers: `Authorization: Bearer <token>` | JSON: `{"ok": bool, "provider": str, "model": str, "baseUrl": str, "hasApiKey": bool, "maskedKey": str, "requiresKey": bool, "running": bool}` |
| 4 | `/api/models` | `GET` | `public/js/app.js:313` | Triggered when user changes provider dropdown (except for LM Studio). | Query param: `?provider=<key>`, Header: `X-API-Key` | JSON: `{"ok": bool, "models": List[str]}` |
| 5 | `/api/auth/me` | `GET` | `public/js/app.js:490` | Triggered on page bootstrap to populate user profile menu avatar and name. | Headers: `Authorization: Bearer <token>` | JSON: `{"ok": bool, "user": {"id": str, "username": str, "fullName": str, "email": str}}` |
| 6 | `/api/customer/verify` | `GET` | `public/js/app.js:1214` | Triggered after Section 4 finishes to fetch customer verification & ledger card. | Query params: `?query=<encoded_query>&category=<encoded_cat>` | JSON: Customer verification payload (see Section 4 for complete schema). |
| 7 | `/api/resolve/action` | `POST` | `public/js/app.js:1445, 1601` | Triggered when operator clicks "Resolve Manually" or "Let agent resolve it". | JSON: `{"action": "agent_resolve"|"manual_resolve", "customer_id": str, "query": str, "category": str, "action_details": str, "notes": str}` | JSON: `{"ok": bool, "action": str, "customer": {...}, "ticket": {...}, "database_file": str, "timestamp": str}` |
| 8 | `/api/next` | `POST` | `public/js/app.js:2272` | Triggered when user clicks "Begin Resolution" button. | JSON: `{"query": str, "provider": str, "model": str, "apiKey": str}` | JSON: `{"ok": True, "started": True}` |
| 9 | `/api/stream` | `GET` | `public/js/app.js:2306` | Triggered during `connectLiveStream()` on DOM load. SSE EventSource connection. | Query param: `?token=<idToken>` (optional) | `text/event-stream` SSE event frames |
| 10 | `/api/auth/firebase-config` | `GET` | `public/js/firebase-auth.js:61` | Fetches Firebase authentication configuration. | None | JSON: `{"apiKey": str, "authDomain": str, "projectId": str, ...}` |
| 11 | `/api/auth/google` | `POST` | `public/js/firebase-auth.js:323` | Authenticates Google OAuth token. | JSON: `{"idToken": str, "email": str, "displayName": str, ...}` | JSON: `{"ok": True, "user": {...}}` |
| 12 | `/api/auth/login` | `POST` | `public/js/login.js:44` | User username/password login. | JSON: `{"username": str, "password": str}` | JSON: `{"ok": True, "user": {...}}` |
| 13 | `/api/auth/signup` | `POST` | `public/js/signup.js:58` | User account creation. | JSON: `{"fullName": str, "email": str, "username": str, "password": str, "confirmPassword": str}` | JSON: `{"ok": True, "message": str}` |
| 14 | `/logout` | `GET` | Browser / `app/main.py:640` | Clears cookie and redirects to `/login.html`. | None | HTTP 302 Redirect |

---

## 3. Catalog of SSE Streaming Events & Payload Contracts

The frontend establishes an EventSource connection at `const stream = new EventSource("/api/stream")` (`public/js/app.js:2307`).
The following 14 event listeners are active:

| Event Name | Backend Source | Frontend Handler (`public/js/app.js`) | Payload Format & Expected Fields |
|---|---|---|---|
| `open` | HTTP SSE connection open | Logs `[SSE] Connected to CRA Troy event stream.` | Browser native event |
| `topic` | `troy.py:126`, `main.py:161, 306` | `setStatusTopic(data.title)` | JSON: `{"title": str}` |
| `ticket_summarizing` | `main.py:377` | `showSummarizingFeedCard(data.query)` | JSON: `{"query": str}` |
| `policy_summarizing` | `main.py:378` | `showSummarizingFeedCard(data.query)` (alias) | JSON: `{"query": str}` |
| `feed` | `troy.py:137`, `main.py:163, 307` | `setActiveFeedCard(data)` | JSON: `{"title": str, "source": str, "url": str, "publishedAt": str, "description": str, "summary": str}` |
| `resolution_start` | `troy.py:218` | Resets console, starts ML cycling spinner | JSON: `{"story": {...}, "agent": "Troy"}` |
| `council_start` | `troy.py:225` | Resets console, starts ML cycling spinner (alias) | JSON: `{"story": {...}, "agents": [{"name": "Troy", "state": str}]}` |
| `system` | `troy.py:233, 239, 272, 468` | `updateLiveStatus(data.message, true)` | JSON: `{"message": str}` |
| `cra_report` | `troy.py:270` | Caches report, triggers `deliverPreliminaryAssessment(report)` | JSON: Triage report schema (see detailed breakdown below) |
| `agent_start` | `reasoning.py:67`, `troy.py:500` | Creates/activates chatbox for `SECTION_DEFINITIONS[secIdx]` | JSON: `{"agent": "Troy", "round": int, "section_index": int, "section": str, "section_title": str, "card_id": str, "speech_id": str, "show_reasoning": bool}` |
| `agent_delta` | `reasoning.py:72` | Appends delta chunk to active chatbox and renders markdown | JSON: `{"agent": "Troy", "delta": str, "section_index": int, ...}` |
| `agent` | `reasoning.py:100`, `troy.py:504` | Finalizes chatbox; if `secIdx == 3`, triggers `renderResolutionActionButtons()` | JSON: `{"agent": "Troy", "round": int, "section_index": int, "message": str, "reasoning": str, ...}` |
| `resolution_end` | `troy.py:535` | Calls `finishTransformedStream()` | JSON: `{"summary": str, "category": str, "confidence": float, "team": str, "sla_hours": float, "cra_report": {...}, "full_resolution": str, "sections": {"executive": str, "root_cause": str, "customer_comms": str, "internal_routing": str}}` |
| `council_end` | `troy.py:548` | Calls `finishTransformedStream()` (alias) | JSON: Same as `resolution_end` with optional `impacts` dict |
| `error` | `troy.py:200` | Shows warning banner, resets button state | JSON: `{"message": str}` |

### Detailed `cra_report` Payload Schema
Consumed by `deliverPreliminaryAssessment` (`app.js:2070-2158`):
```json
{
  "predicted_category": "Refunds",
  "category_confidence": 0.95,
  "top_3_categories": [
    {"category": "Refunds", "score": 0.95},
    {"category": "Billing", "score": 0.035},
    {"category": "Subscription", "score": 0.015}
  ],
  "extracted_entities": {
    "references": ["ORD-88219", "TXN-9012"],
    "dates": ["Oct 21"],
    "amounts": ["$49.00"],
    "products": ["payment gateway"]
  },
  "similar_cases": [
    {
      "ticket_id": 88219,
      "description": "Order ORD-88219 debited twice on checkout due to connection retry.",
      "category": "Refunds",
      "resolution": "Refunded secondary charge TXN-9012, verified primary payment TXN-9011 settled cleanly.",
      "assigned_team": "Billing",
      "similarity": 0.91
    }
  ],
  "recommended_team": "Billing",
  "team_confidence": "High",
  "sla": {
    "estimated_hours": 3.5,
    "sla_target_hours": 24.0,
    "sla_risk": "Low"
  }
}
```

---

## 4. UI Markdown Formatting Expectations (4-Stage Architecture)

In `public/js/app.js:972-1062`, Troy's resolution output is structured into 4 distinct chatbox containers based on `SECTION_DEFINITIONS`.

### Section Specifications Table

| Stage Index | Key | ID | Section Title | Heading Format | Card DOM ID | Speech DOM ID | Loading Text |
|---|---|---|---|---|---|---|---|
| 0 | `part1` | `executive` | Executive Assessment & Category Confirmation | `### **Executive Assessment & Category Confirmation**` | `troy-card` | `troy-speech` | Troy is performing Executive Assessment |
| 1 | `part2` | `root_cause` | Root Cause Analysis & Precedent Alignment | `### **Root Cause Analysis & Precedent Alignment**` | `troy-card-root-cause` | `troy-root-cause-speech` | Troy is performing Root Cause Analysis |
| 2 | `part3` | `customer_comms` | Customer Communication Strategy | `### **Customer Communication Strategy**` | `troy-card-communication` | `troy-communication-speech` | Troy is formulating Customer Communication Strategy |
| 3 | `part4` | `internal_routing` | Internal Routing & Next Action Steps | `### **Internal Routing & Next Action Steps**` | `troy-card-routing` | `troy-routing-speech` | Troy is determining Internal Routing & Next Steps |

### Heading Regex & Formatting Rules
1. `formatSectionWithBoldHeading(rawText, defaultTitle)` (`app.js:1021-1062`):
   - Scans the first non-empty line of text.
   - If candidate matches `headingPattern` (`/(?:Executive\s+Assessment|Root\s+Cause|Customer\s+Comm|Internal\s+Routing)/i`), strips leading symbols (`#`, `*`, `•`, `-`, numbers) and trims.
   - For Customer Communication, replaces `Subject: ...` with `**Subject:** ...`.
   - Wraps title in `### **${cleanTitle}**\n\n${body}`.
2. In `app/simulation/troy.py:515-526`:
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
   This guarantees that both streaming deltas and final section messages strictly conform to `### **<Title>**`.

---

## 5. Customer Verification Card Data (`/api/customer/verify`)

When Section 4 completes, `renderResolutionActionButtons()` (`app.js:1214`) calls:
`GET /api/customer/verify?query=${encodeURIComponent(q)}&category=${encodeURIComponent(cat)}`.

### Refund Category Response Contract
For `Refunds` (e.g. `query="ORD-88219"`), backend returns:
```json
{
  "ok": true,
  "verified": true,
  "category": "Refunds",
  "customer_id": "USR-10492",
  "customer_name": "Alex Morgan",
  "customer_email": "alex.morgan@gmail.com",
  "subscription_plan": "Pro Tier ($49.00/mo)",
  "billing_cycle": "October 2026",
  "order_id": "ORD-88219",
  "charges_this_month": 2,
  "total_billed_this_month": "98.00",
  "settled_count": 2,
  "has_duplicate": true,
  "has_pending_duplicate": true,
  "is_already_refunded": false,
  "already_refunded": false,
  "refund_id": "REF-88219-AUTO",
  "refunded_at": "",
  "overcharge_amount": "$49.00",
  "retained_transaction": {
    "transaction_id": "TXN-9011",
    "amount": "49.00",
    "status": "Settled",
    "transaction_date": "2026-10-20 08:14:22"
  },
  "duplicate_transaction": {
    "transaction_id": "TXN-9012",
    "amount": "49.00",
    "status": "Settled",
    "transaction_date": "2026-10-21 09:30:15"
  },
  "refunded_transaction": null,
  "transactions": [ ... ],
  "summary": "Analyzed data/transactions.csv: Found 2 transaction records in October 2026...",
  "steps": [ ... ],
  "customer": {
    "customer_id": "USR-10492",
    "customer_name": "Alex Morgan",
    "customer_email": "alex.morgan@gmail.com",
    "reference_order_id": "ORD-88219",
    "subscription_plan": "Pro Tier ($49.00/mo)",
    "billing_cycle": "October 2026",
    "monthly_rate": "49.00",
    "total_billed_this_month": "98.00",
    "charges_this_month": "2",
    "transaction_1": "TXN-9011 ($49.00 on 2026-10-20) [Settled]",
    "transaction_2": "TXN-9012 ($49.00 on 2026-10-21) [Settled]",
    "duplicate_verified": "Yes - Accidental Double Charge Detected",
    "is_already_refunded": false,
    "refund_status": "Pending Refund",
    "refund_id": "",
    "refunded_at": "",
    "account_balance": "$-49.00 (Overcharged)",
    "account_status": "Flagged - Accidental Double Charge"
  }
}
```

### Non-Refund Category Response Contract
For non-refund categories (e.g. `category="Authentication & Access"`), backend returns:
```json
{
  "ok": true,
  "verified": true,
  "category": "Authentication & Access",
  "customer_id": "USR-08914",
  "customer_name": "Sarah Jenkins",
  "customer_email": "sarah.j@enterprise.org",
  "subscription_plan": "Enterprise Tier ($299.00/mo)",
  "reference_id": "AUTH-9921",
  "system_label": "Identity & Access Management (Okta / IAM Gateway)",
  "database_file": "Identity Directory (IAM / Okta)",
  "status_pill": "🔒 Account Locked: 2FA SMS Gateway Failure",
  "action_btn_text": "Let agent restore access & reset 2FA",
  "system_details": "Account locked after failed carrier SMS OTP delivery on payment portal. Identity verified via enterprise SSO certificate.",
  "steps": [ ... ],
  "customer": {
    "customer_id": "USR-08914",
    "customer_name": "Sarah Jenkins",
    "customer_email": "sarah.j@enterprise.org",
    "subscription_plan": "Enterprise Tier ($299.00/mo)",
    "account_status": "🔒 Account Locked: 2FA SMS Gateway Failure",
    "refund_status": "N/A",
    "refund_id": "AUTH-9921",
    "account_balance": "$0.00 (Current)"
  },
  "is_already_refunded": false
}
```

---

## 6. Action Execution Endpoint (`POST /api/resolve/action`)

When the user clicks "Let agent resolve it" or "Resolve Manually":
1. **Agent Resolve** (`btnAgent`):
   - Requests: `{"action": "agent_resolve", "customer_id": ..., "query": ..., "category": ..., "action_details": ..., "notes": ...}`
   - Backend calls `execute_agent_resolution()` (`app/database.py:1518`).
   - For `Refunds`: updates `data/transactions.csv` (marks `TXN-9012` as `Refunded`, records `refund_id`, `refunded_at`) and synchronizes `data/customer_accounts.csv` (sets balance to `$0.00 (Settled)`, status to `Active - In Good Standing`).
   - SseManager broadcasts `database_updated` event.
   - Returns: `{"ok": True, "action": "agent_resolve", "category": ..., "customer": {...}, "ticket": {...}, "database_file": "data/transactions.csv", "timestamp": ...}`.
   - Frontend renders the green confirmation card with synced ledger status (`app.js:1712-1735`).
2. **Manual Resolve** (`btnManual`):
   - Requests: `{"action": "manual_resolve", "customer_id": ..., "category": ..., "query": ..., "notes": ...}`
   - Backend calls `execute_manual_resolution()` (`app/database.py:1756`).
   - Flags record in database or specialist queue (`assigned_queue`, `customer`, `ticket`).
   - Returns: `{"ok": True, "action": "manual_resolve", "assigned_queue": ..., "customer": {...}, "ticket": {...}}`.
   - Frontend renders the manual escalation confirmation card (`app.js:1496-1540`).

---

## 7. Observations & Minor Recommendations

1. **Explicit `"is_refund": True` flag**:
   `app/database.py:analyze_customer_transactions` currently returns `"category": "Refunds"`, so `effectiveCat === "Refunds"` in `app.js:1261` evaluates to True. Adding `"is_refund": True` explicitly to the return dictionary ensures `Boolean(verifyData?.is_refund)` is also True directly.
2. **Top-level `"predicted_sla_hours"` in `cra_report`**:
   `app.js:1510` checks `cachedCraReport?.predicted_sla_hours ? cachedCraReport.predicted_sla_hours + " hrs" : "Priority Routing"`. In `app/triage.py:triage_complaint`, SLA is nested under `sla: {"estimated_hours": 4.0, ...}`. Adding `"predicted_sla_hours": sla["estimated_hours"]` at the root of `cra_report` allows the UI to display the exact SLA value in manual escalation cards.
3. **LM Studio Default Port & Configuration (Feature 9)**:
   In `app/providers/__init__.py:21`, `"lmstudio"` is currently set to `"http://localhost:1235/v1"`, while PROJECT.md Feature 9 and `test_e2e_troy.py:1003-1029` require:
   `provider_base_url("lmstudio") == "http://localhost:1234/v1"` (port 1234) and support for `LMSTUDIO_BASE_URL` env override. Aligning this in `app/providers/` satisfies Feature 9 without breaking the UI (as the browser never talks directly to LM Studio).
4. **Mock Fallback Handling (Feature 11)**:
   In `app/reference/reasoning_and_output_reference.py:110-114`, an unhandled connection error broadcasts `"Signal lost. Unable to reach model provider."`. In Milestone 3, Feature 11 implementation must ensure that `create_driver("lmstudio")` seamlessly switches to `MockDriver` when offline so no exception is thrown and the deterministic mock stream is delivered to the UI.
