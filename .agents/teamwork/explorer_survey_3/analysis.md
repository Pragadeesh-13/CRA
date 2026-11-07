# Database Schema, Tools, & Verification Scenarios — Detailed Analysis

**Explorer**: Explorer 3 (Survey Explorer Database)  
**Date**: 2026-11-06  
**Target Project**: Complaint Resolution Agent (Troy) — `D:\CRA`  

---

## 1. Executive Summary

The database layer for Troy is a dual-table flat-file CSV architecture located in `D:\CRA\data` (`transactions.csv` with 17 fields and `customer_accounts.csv` with 20 fields), managed by Python CSV utilities in `app/database.py`. No SQL database or ORM is utilized. To enable Troy to autonomously inspect records, resolve customer complaints (specifically accidental duplicate subscription charges), and formulate verified customer responses, Troy requires 6 generic database tools (3 inspection tools and 3 remediation/update tools) with strict multi-table synchronization and idempotency protection. The existing test harness contains 89 passing tests across 12 files, but currently relies on legacy ML imports (`app.cra`) and lacks database state isolation fixtures.

---

## 2. Database Schema & Storage Architecture

### 2.1 Storage Mechanism
- **Engine**: File-based CSV storage via Python's standard `csv.DictReader` and `csv.DictWriter` modules (`app/database.py:9`).
- **No SQL/ORM**: No SQLite, SQLAlchemy, PostgreSQL, or other relational engines are present or imported in the project.
- **Directory**: `D:\CRA\data/`
  - `transactions.csv`: Individual financial transaction ledger records.
  - `customer_accounts.csv`: Aggregated customer account summary, current standing, and subscription billing state.

---

### 2.2 `data/transactions.csv` Schema

Initialized in `app/database.py:23-41` with `FIELDNAMES` and seeded by `ensure_database_file()` (`app/database.py:222-231`) if missing.

| Column Index | Field Name | Type | Description | Example Values |
|---|---|---|---|---|
| 1 | `transaction_id` | `str` | Unique transaction identifier | `TXN-9011`, `TXN-9012`, `TXN-7711`, `TXN-7712` |
| 2 | `customer_id` | `str` | Unique customer account identifier | `USR-10492`, `USR-08914`, `USR-07231` |
| 3 | `customer_name` | `str` | Full name of the customer | `Alex Morgan`, `Sarah Jenkins`, `Emily Watson` |
| 4 | `customer_email` | `str` | Primary email of customer | `alex.morgan@gmail.com`, `emily.w@consulting.co` |
| 5 | `order_id` | `str` | Associated order/contract identifier | `ORD-88219`, `ORD-81094`, `ORD-79110` |
| 6 | `transaction_date` | `str` | Timestamp of charge (`YYYY-MM-DD HH:MM:SS`) | `2026-10-20 08:14:22`, `2026-10-21 09:30:15` |
| 7 | `billing_cycle` | `str` | Billing period label | `October 2026`, `September 2026`, `Q4 2026` |
| 8 | `subscription_plan` | `str` | Plan name and monthly rate | `Pro Tier ($49.00/mo)`, `Enterprise Tier ($299.00/mo)` |
| 9 | `amount` | `str` | Numeric charge amount | `49.00`, `299.00`, `79.00` |
| 10 | `currency` | `str` | Currency ISO code | `USD`, `EUR` |
| 11 | `payment_method` | `str` | Payment card/method description | `Visa ending in 4242`, `Mastercard ending in 1098` |
| 12 | `status` | `str` | Transaction status | `Settled`, `Refunded` |
| 13 | `refund_id` | `str` | Refund transaction reference | `REF-88219-AUTO`, `REF-7712-AUTO`, `""` |
| 14 | `refund_amount` | `str` | Numeric refunded amount | `49.00`, `""` |
| 15 | `refunded_at` | `str` | Timestamp of refund processing | `2026-10-23 02:01:13`, `""` |
| 16 | `processed_by` | `str` | Processing entity | `Stripe Gateway`, `Troy (Autonomous Resolution Agent)` |
| 17 | `notes` | `str` | Transaction/audit notes | `Monthly subscription renewal debit`, `Refunded by Troy...` |

---

### 2.3 `data/customer_accounts.csv` Schema

Defined in `app/database.py:43-64` with `CUSTOMER_ACCOUNT_FIELDNAMES`.

| Column Index | Field Name | Type | Description | Example Values |
|---|---|---|---|---|
| 1 | `customer_id` | `str` | Primary key identifier | `USR-10492`, `USR-07231` |
| 2 | `customer_name` | `str` | Customer name | `Alex Morgan`, `Emily Watson` |
| 3 | `customer_email` | `str` | Contact email | `alex.morgan@gmail.com` |
| 4 | `reference_order_id` | `str` | Primary order reference | `ORD-88219`, `ORD-81094` |
| 5 | `subscription_plan` | `str` | Subscription tier | `Pro Tier ($49.00/mo)` |
| 6 | `billing_cycle` | `str` | Renewal schedule description | `Monthly (20th)`, `Monthly (18th)` |
| 7 | `monthly_rate` | `str` | Monthly price | `49.00`, `29.00`, `199.00` |
| 8 | `total_billed_this_month` | `str` | Total debited this month | `98.00`, `49.00 (Net after $49.00 refund)` |
| 9 | `charges_this_month` | `str` | Count of debits in current cycle | `2`, `1` |
| 10 | `transaction_1` | `str` | Summary of 1st cycle transaction | `TXN-9011 ($49.00 on 2026-10-20) [Settled Renewal]` |
| 11 | `transaction_2` | `str` | Summary of 2nd cycle transaction | `TXN-9012 ($49.00 on 2026-10-21) [Accidental Duplicate Debit]` |
| 12 | `duplicate_verified` | `str` | Duplicate verification status | `Yes - Accidental Double Charge Detected`, `No` |
| 13 | `refund_status` | `str` | Refund processing state | `Pending Refund`, `Refunded ($49.00 via Stripe API)` |
| 14 | `refund_id` | `str` | Associated refund ID | `REF-88219-AUTO`, `REF-81094-AUTO` |
| 15 | `account_balance` | `str` | Ledger standing balance | `$-49.00 (Overcharged)`, `$0.00 (Settled)` |
| 16 | `account_status` | `str` | Operational account state | `Flagged - Accidental Double Charge`, `Active - In Good Standing` |
| 17 | `last_verified_by` | `str` | Agent/service who audited record | `Troy (Autonomous Resolution Agent)` |
| 18 | `updated_by` | `str` | Agent/service who last updated | `Troy (Autonomous Resolution Agent)` |
| 19 | `last_updated` | `str` | Timestamp of update | `2026-10-23 02:20:53` |
| 20 | `notes` | `str` | Account standing notes | `Accidental duplicate renewal charge debited twice...` |

---

### 2.4 Existing Codebase Functions & Critical Flaws

Inspection of `app/database.py`:
- `ensure_database_file()` (line 222): Only checks and writes `transactions.csv`. Does NOT create or seed `customer_accounts.csv` if missing!
- `find_customer_account_by_query()` (lines 290-346): Extracts order references via regex `(?:ORD-)?(\d{4,6})` and searches customer ID, name, email.
- `find_transactions_for_query()` (lines 349-399):
  - **CRITICAL FLAW (Lines 395-398)**:
    ```python
    if "twice" in query_lower or "duplicate" in query_lower or "49" in query_lower:
        return [t for t in transactions if t.get("customer_id") == "USR-10492"]
    return [t for t in transactions if t.get("customer_id") == "USR-10492"]
    ```
    If no customer ID or order match is found, it silently falls back to returning Alex Morgan (`USR-10492`). This breaks generic queries for arbitrary customers (such as Emily Watson or newly added customers).
- `analyze_customer_transactions()` (lines 401-554): Analyzes transactions for multiple settled debits in a single cycle. It detects if already refunded or pending duplicate.
- `execute_agent_resolution()` (lines 909-1070): Modifies `transactions.csv` (updates `TXN-9012` to `Refunded`) and updates `customer_accounts.csv` (sets `account_balance` to `$0.00 (Settled)` and `account_status` to `Active - In Good Standing`).
- `execute_manual_resolution()` (lines 1147-1266): Escalates to specialized queue, adding audit notes without executing refund.

---

## 3. Generic Database Inspection & Remediation Tools

Under Requirements R2 & R3, Troy must autonomously decide between direct response vs database tool invocation. When database tool invocation is needed, Troy should interact with structured, generic tools.

### 3.1 Tool Set Specification

#### Tool 1: `query_customer_account`
- **Purpose**: Inspect customer profile, subscription plan, account status, and current balance.
- **Parameters**:
  - `query` (`Optional[str]`): Search terms (customer name, email, order reference like `88219`, `ORD-81094`).
  - `customer_id` (`Optional[str]`): Explicit customer ID (e.g. `USR-10492`).
- **Return Type**: `Dict[str, Any]` (matches `customer_accounts.csv` record or `None` if not found).
- **Behavior**: Pure read-only inspection. No side effects.

#### Tool 2: `query_transactions`
- **Purpose**: Inspect customer transaction ledger with generic multi-field filtering.
- **Parameters**:
  - `customer_id` (`Optional[str]`): Filter by customer ID.
  - `order_id` (`Optional[str]`): Filter by order ID.
  - `billing_cycle` (`Optional[str]`): Filter by billing cycle (e.g. `October 2026`).
  - `status` (`Optional[str]`): Filter by status (`Settled`, `Refunded`).
  - `transaction_id` (`Optional[str]`): Filter by single transaction ID (`TXN-9012`).
  - `query` (`Optional[str]`): Free-text search across records.
- **Return Type**: `List[Dict[str, Any]]` (list of matching transaction rows).
- **Behavior**: Pure read-only inspection.

#### Tool 3: `inspect_account_and_transactions` (Composite Inspection Tool)
- **Purpose**: Convenient one-stop diagnostic tool for Troy to analyze account standing, billing cycles, and detect duplicate charges or balance discrepancies.
- **Parameters**:
  - `query` (`Optional[str]`): Complaint text or search identifier.
  - `customer_id` (`Optional[str]`): Customer ID.
- **Return Schema**:
  ```json
  {
    "ok": true,
    "customer": {
      "customer_id": "USR-10492",
      "customer_name": "Alex Morgan",
      "account_balance": "$-49.00 (Overcharged)",
      "account_status": "Flagged - Accidental Double Charge"
    },
    "billing_cycle": "October 2026",
    "transactions": [...],
    "has_duplicate": true,
    "is_already_refunded": false,
    "duplicate_transaction": {
      "transaction_id": "TXN-9012",
      "amount": "49.00",
      "status": "Settled"
    },
    "retained_transaction": {
      "transaction_id": "TXN-9011",
      "amount": "49.00",
      "status": "Settled"
    }
  }
  ```

#### Tool 4: `update_transaction`
- **Purpose**: Generic row-level update on `transactions.csv`.
- **Parameters**:
  - `transaction_id` (`str`, required): ID of target transaction.
  - `status` (`Optional[str]`): e.g. `Refunded`.
  - `refund_id` (`Optional[str]`): e.g. `REF-88219-AUTO`.
  - `refund_amount` (`Optional[str]`): e.g. `49.00`.
  - `notes` (`Optional[str]`): Audit reason.
  - `processed_by` (`Optional[str]`): Default `"Troy (Autonomous Resolution Agent)"`.
- **Return Type**: `Dict[str, Any]` (`{"ok": bool, "updated_transaction": ...}`).

#### Tool 5: `update_customer_account`
- **Purpose**: Generic row-level update on `customer_accounts.csv`.
- **Parameters**:
  - `customer_id` (`str`, required): ID of target customer.
  - `account_balance` (`Optional[str]`): e.g. `"$0.00 (Settled)"`.
  - `account_status` (`Optional[str]`): e.g. `"Active - In Good Standing"`.
  - `refund_status` (`Optional[str]`): e.g. `"Refunded ($49.00 via Stripe API)"`.
  - `refund_id` (`Optional[str]`): e.g. `"REF-88219-AUTO"`.
  - `notes` (`Optional[str]`): Audit summary.
- **Return Type**: `Dict[str, Any]` (`{"ok": bool, "updated_account": ...}`).

#### Tool 6: `remediate_duplicate_charge` (Transactional Remediation Tool)
- **Purpose**: Atomic remediation executing both transaction reversal and customer account balance restoration.
- **Parameters**:
  - `customer_id` (`str`, required): ID of customer.
  - `duplicate_transaction_id` (`str`, required): ID of transaction to refund.
  - `refund_id` (`Optional[str]`): Auto-generated if omitted (e.g. `REF-<order_id>-AUTO`).
  - `refund_amount` (`Optional[str]`): Defaults to duplicate transaction amount.
  - `notes` (`Optional[str]`): Reason for remediation.
- **Behavior & Invariants**:
  1. Validates that the transaction belongs to customer.
  2. **Idempotency check**: If already marked `Refunded`, aborts secondary refund and returns current status without modifying balance.
  3. Updates `transactions.csv`: status -> `Refunded`, `refund_id`, `refund_amount`, `refunded_at` -> current UTC timestamp, `processed_by` -> `"Troy (Autonomous Resolution Agent)"`.
  4. Updates `customer_accounts.csv`: `account_balance` -> `"$0.00 (Settled)"`, `account_status` -> `"Active - In Good Standing"`, `refund_status` -> `"Refunded ($<amt> via Stripe API)"`, `refund_id`, `notes`.
  5. Returns atomic confirmation payload with audit timestamps.

---

## 4. Duplicate Charge Scenario & Resolution Flow

### 4.1 Benchmark Scenarios in Codebase

#### Scenario 1: Primary Demo — Alex Morgan
- **Customer**: Alex Morgan (`USR-10492`, `alex.morgan@gmail.com`)
- **Order**: `ORD-88219` | Plan: `Pro Tier ($49.00/mo)`
- **Cycle**: `October 2026`
- **Transactions**:
  - `TXN-9011`: 2026-10-20 08:14:22, `$49.00`, Status: `Settled` (Valid renewal)
  - `TXN-9012`: 2026-10-21 09:30:15, `$49.00`, Status: `Settled` (Duplicate retry debit)
- **Prompt**:
  > *"I was charged twice for my subscription renewal this month. Order #88219 was debited for $49.00 yesterday and again this morning. Please investigate and refund the duplicate charge immediately."*

#### Scenario 6: Benchmark Precedent — Emily Watson
- **Customer**: Emily Watson (`USR-07231`, `emily.w@consulting.co`)
- **Order**: `ORD-81094` | Plan: `Pro Tier ($49.00/mo)`
- **Cycle**: `September 2026`
- **Transactions**:
  - `TXN-7711`: 2026-09-18 09:00:00, `$49.00`, Status: `Settled` (Valid renewal)
  - `TXN-7712`: 2026-09-18 09:01:15, `$49.00`, Status: `Settled` (Duplicate retry debit, 75s later)
- **Prompt**:
  > *"Double charged on annual renewal fee due to payment gateway retry for Order #81094. Debited $49.00 twice on renewal date."*

---

### 4.2 End-to-End Resolution Flow

```
[Customer Complaint]
        │
        ▼
[Troy Autonomous Intent Triage]
        │
        ├── [General Inquiry / Policy] ────► Formulate Direct Customer Response
        │                                     (No DB Tool Invocations)
        │
        └── [Billing / Duplicate Charge Issue]
                    │
                    ▼
        [Tool: inspect_account_and_transactions]
                    │
                    ▼
        [Audit Analysis: Detect 2 Settled Charges in 1 Cycle]
                    │
                    ├── [Already Refunded?] ────► Confirm Prior Refund (No Second Payout)
                    │                             Formulate Reassurance Response
                    │
                    └── [Pending Duplicate Found]
                                │
                                ▼
                    [Tool: remediate_duplicate_charge]
                                │
                                ├── Update transactions.csv: TXN-9012 -> 'Refunded'
                                └── Update customer_accounts.csv: Balance -> '$0.00 (Settled)'
                                │
                                ▼
                    [Formulate Formal Customer Response]
                    (Citing Refund ID, Retained Renewal, & Banking SLA)
```

#### Step 1: Complaint Ingestion & Autonomous Decision
Troy analyzes incoming text. If keywords/entities indicate duplicate billing (e.g. "charged twice", "double debited", "Order #88219"), Troy decides that database inspection is mandatory.

#### Step 2: Database Inspection
Troy invokes `inspect_account_and_transactions(query="Order #88219 was debited twice")`.
- Finds customer record `USR-10492`.
- Groups transactions for `October 2026`.
- Identifies:
  - Valid renewal: `TXN-9011` ($49.00, Settled).
  - Redundant duplicate: `TXN-9012` ($49.00, Settled).

#### Step 3: Atomic Remediation Execution
Troy invokes `remediate_duplicate_charge(customer_id="USR-10492", duplicate_transaction_id="TXN-9012")`.
- `transactions.csv`: `TXN-9012` status set to `Refunded`, `refund_id` set to `REF-88219-AUTO`, `refund_amount` set to `49.00`, `refunded_at` set to current timestamp, `processed_by` set to `"Troy (Autonomous Resolution Agent)"`.
- `customer_accounts.csv`: `account_balance` set to `"$0.00 (Settled)"`, `account_status` set to `"Active - In Good Standing"`, `refund_status` set to `"Refunded ($49.00 via Stripe API)"`.

#### Step 4: Customer Response Generation
Troy composes the customer response:
- Formal greeting addressing customer by name.
- Explicit confirmation that internal transaction ledger was audited.
- Explicit confirmation that redundant transaction `TXN-9012` has been refunded under reference `REF-88219-AUTO`.
- Confirmation that subscription remains active and settled via `TXN-9011`.
- Banking credit turnaround statement (3 to 5 business days).

#### Step 5: Duplicate Payout Prevention (Idempotency)
If customer or operator queries again for the same ticket:
- `inspect_account_and_transactions` reports `is_already_refunded=True`.
- `remediate_duplicate_charge` skips transaction mutation, returns existing refund details, and blocks duplicate bank payouts.

---

## 5. Existing Test Harness & Pytest Requirements

### 5.1 Inventory of Existing Tests
Running `pytest` currently executes **89 tests across 12 files**:

1. `tests/test_api_endpoints.py` (15 tests): Health, config, models, auth signup/login/google, static serving, lmstudio config, customer verify, database customers, database transactions, resolve action.
2. `tests/test_auth_vault.py` (4 tests): AES-GCM encryption/decryption of LLM API keys.
3. `tests/test_category_resolutions.py` (39 tests): Parameterized tests across 11 CRA categories, category context, Troy fallback generation, 2FA complaint flow, customer account lookup.
4. `tests/test_json_extractor.py` (5 tests): Markdown code fence stripping, thought tag handling, JSON extraction.
5. `tests/test_mock_simulation.py` (2 tests): End-to-end resolution pipeline lifecycle with `MockDriver`, reasoning/streaming.
6. `tests/test_reasoning_streaming.py` (5 tests): ThoughtBuffer and SSE reasoning streaming.
7. `tests/test_round_barrier.py` (3 tests): Barrier synchronization.
8. `tests/test_sanitizer.py` (7 tests): Output sanitization.
9. `tests/test_sse.py` (2 tests): SseManager client queuing and broadcasting.
10. `tests/test_thought_buffer.py` (4 tests): Chunk buffering.
11. `tests/test_ticket_prompts.py` (3 tests): Ticket prompt builders.

All 89 tests pass currently (`37.91s` execution time, mostly consumed by loading DistilBERT model weights in `test_category_resolutions.py` and `test_api_endpoints.py`).

---

### 5.2 Legacy ML Couplings to Remove in Tests
- `tests/test_api_endpoints.py:147-171` (`test_cra_model_endpoints`): Directly calls `/api/cra/classify` and `/api/cra/resolve`, which load DistilBERT and SBERT.
- `tests/test_category_resolutions.py`: Lines 2, 26, 83, 180 import and call `cra_engine`.
Once `app/cra` and `CRA_Models` are removed per R1, these tests must be removed or refactored into tests verifying the local LLM engine and prompt categorization without legacy models.

---

### 5.3 Test Suite Deficiencies & Gaps
1. **Lack of Database File Isolation**:
   - `test_resolve_action_agent_and_manual` (`tests/test_api_endpoints.py:217-257`) temporarily backs up `DATABASE_FILE` (`transactions.csv`), but does NOT back up or restore `CUSTOMER_ACCOUNTS_FILE` (`customer_accounts.csv`).
   - Consequently, running the tests permanently mutates `customer_accounts.csv`, breaking test determinism.
2. **Missing End-to-End Tool Invocation Test**:
   - Currently, no automated test verifies Troy autonomously calling database query and remediation tools during a complaint resolution cycle.
3. **Missing Offline Local LLM Fallback Test**:
   - No automated test verifies that when LM Studio (`http://localhost:1234/v1`) is offline/unreachable, Troy seamlessly falls back to the deterministic mock/stub engine without raising network exceptions.

---

### 5.4 Proposed Pytest Architecture for Acceptance Criteria

To satisfy the Acceptance Criteria in `ORIGINAL_REQUEST.md`, the following automated tests and fixtures must be implemented:

#### 1. Pytest Fixture: `isolated_database` (`tests/conftest.py`)
```python
@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """
    Copies data/transactions.csv and data/customer_accounts.csv to tmp_path
    and monkeypatches database file paths so tests never contaminate repository data.
    """
    import shutil
    from app import database
    
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy("data/transactions.csv", data_dir / "transactions.csv")
    shutil.copy("data/customer_accounts.csv", data_dir / "customer_accounts.csv")
    
    monkeypatch.setattr(database, "DATABASE_FILE", data_dir / "transactions.csv")
    monkeypatch.setattr(database, "CUSTOMER_ACCOUNTS_FILE", data_dir / "customer_accounts.csv")
    monkeypatch.setattr(database, "DATA_DIR", data_dir)
```

#### 2. Test Targets:
1. `test_clean_startup_without_ml()`:
   - Verifies the application boots cleanly.
   - Verifies neither `torch`, `transformers`, `sentence_transformers`, nor `app.cra` are imported into `sys.modules`.
   - Verifies `/health` returns status `healthy`.
2. `test_local_llm_connection_and_fallback()`:
   - Configures LM Studio endpoint (`http://localhost:1234/v1`).
   - Verifies driver creation with configured base URL.
   - Simulates offline/unreachable server; verifies automatic fallback to mock/stub mode without unhandled exceptions.
3. `test_duplicate_charge_resolution_e2e()`:
   - Sends duplicate charge complaint (`Order #88219 charged twice`).
   - Verifies Troy inspects database, detects duplicate `TXN-9012`.
   - Verifies Troy invokes remediation: `TXN-9012` marked `Refunded`, account balance set to `$0.00 (Settled)`, account status set to `Active - In Good Standing`.
   - Verifies Troy's generated response contains refund reference `REF-88219-AUTO`.
   - Verifies second execution is idempotent (prevents duplicate refund).
4. `test_general_inquiry_no_database_update()`:
   - Sends non-database general inquiry (e.g. how-to, general question).
   - Verifies Troy responds directly without invoking DB remediation tools or mutating CSV files.
5. `test_frontend_api_compatibility()`:
   - Verifies `/api/customer/verify`, `/api/resolve/action`, `/api/database/transactions`, `/api/database/customers`, and `/api/stream` maintain 100% schema compatibility with `public/js/app.js`.

---

## 6. Recommendations for Architecture & Implementation

1. **Fix `app/database.py` Hardcoded Query Fallback**:
   - Delete lines 395-398 that hardcoded `USR-10492` Alex Morgan as default. Return empty list when no matches are found, allowing generic tool logic to accurately report "No matching records found".
2. **Add `ensure_customer_accounts_file()`**:
   - Ensure both CSV files are auto-initialized if missing, preventing FileNotFoundError in fresh containerized/cloud deployments.
3. **Implement Dedicated Tools Module (`app/tools/database_tools.py`)**:
   - Provide clean typed schemas for the 6 tools (`query_customer_account`, `query_transactions`, `inspect_account_and_transactions`, `update_transaction`, `update_customer_account`, `remediate_duplicate_charge`).
   - Provide tool declarations suitable for LLM tool invocation / JSON function calling.
4. **Refactor Troy Execution Engine (`app/simulation/troy.py`)**:
   - Decouple from legacy `app.cra`.
   - Integrate autonomous intent triage: classify complaint as direct response vs database tool invocation.
   - For database complaints, execute the tool calling loop, update the ledger, and emit SSE events expected by frontend.
5. **Update `requirements.txt`**:
   - Retain: `fastapi`, `uvicorn[standard]`, `pydantic`, `python-dotenv`, `httpx`, `cryptography`, `pytest`.
   - Remove: `numpy`, `pandas`, `torch`, `transformers`, `sentence-transformers`.
