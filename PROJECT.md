# Project: Troy Local LLM Autonomous Complaint Resolution Engine

## Architecture
The system is a FastAPI web application serving an agentic customer complaint resolution platform. Troy is the autonomous resolution agent.
- **Backend Entrypoint**: `run.py` launching Uvicorn ASGI server with FastAPI app in `app/main.py`.
- **Agent Simulation Engine**: `app/simulation/troy.py` coordinates resolution workflows, preliminary case assessment, autonomous intent decision (direct response vs database tool execution), SSE streaming events (`topic`, `feed`, `cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`), and customer ledger updates.
- **LLM Provider Layer**: `app/providers/` (`openai_driver.py`, `mock_driver.py`) communicating with OpenAI-compatible local server (LM Studio, default `http://localhost:1234/v1`, configurable via `LMSTUDIO_BASE_URL`), with automated mock/stub fallback when offline.
- **Database & Persistence Layer**: `app/database.py` managing `data/transactions.csv` (17 columns) and `data/customer_accounts.csv` (20 columns). Exposes generic inspection (`query_customer_account`, `query_transactions`, `inspect_account_and_transactions`) and remediation tools (`update_transaction`, `update_customer_account`, `remediate_duplicate_charge`).
- **Triage / Intake Layer**: Non-ML heuristic triage helper (`app/triage.py`) delivering schema-compliant case assessments (`cra_report`) for the 11 complaint categories without heavy machine learning models.
- **Frontend Layer**: `public/` static HTML/CSS/JS files, strictly preserved and completely unmodified.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source | Status |
|---|---------|-------------|-----------|--------|--------|
| 1 | Delete Legacy ML Artifacts | Remove `CRA_Models/` and `app/cra/` completely | M1 | R1 / Survey 1 | DONE |
| 2 | Clean `requirements.txt` | Remove `torch`, `transformers`, `sentence-transformers`, `numpy`, `pandas`; keep backend libs + `pytest-asyncio` | M1 | R1 / Survey 1 | DONE |
| 3 | Decoupled Application Startup | Remove `cra_engine.warmup` from `app/main.py:lifespan`; start clean in <1s without ML weights | M1 | R1 / Survey 1 | DONE |
| 4 | Non-ML Intake Triage Utility | Provide lightweight triage utility to populate `cra_report` payload and serve `/api/cra/*` for UI compatibility | M1 | R1, R4 / Survey 1, 2 | DONE |
| 5 | Remove Hardcoded USR-10492 Fallback | Eliminate silent fallback in `app/database.py:396-398` for unbiased multi-customer querying | M2 | R3 / Survey 3 | DONE |
| 6 | Generic Database Inspection Tools | Provide `query_customer_account`, `query_transactions`, and `inspect_account_and_transactions` | M2 | R3 / Survey 3 | DONE |
| 7 | Generic Database Remediation Tools | Provide `update_transaction`, `update_customer_account`, and `remediate_duplicate_charge` with atomic multi-table sync and idempotency | M2 | R3 / Survey 3 | DONE |
| 8 | Database Test Isolation Fixture | Introduce `isolated_database` fixture in `tests/conftest.py` copying CSVs to temporary directory to prevent test pollution | M2 | Acceptance Criteria / Survey 3 | DONE (by E2E Track) |
| 9 | LM Studio Provider Default & Config | Default base URL to `http://localhost:1234/v1` and support `LMSTUDIO_BASE_URL` env var override | M3 | R2 / Survey 2 | DONE |
| 10 | Autonomous Intent Decision | Troy analyzes complaint context to choose between direct response (inquiries) vs database tool execution (billing disputes) | M3 | R2, R3 / Survey 2, 3 | DONE |
| 11 | Local LLM Client & Mock Fallback | Connect to OpenAI-compatible endpoint with automatic fallback to deterministic streaming mock when server offline | M3 | R2 / Survey 2 | DONE |
| 12 | End-to-End Duplicate Charge Remediation | Troy audits customer transactions, identifies duplicate debit (`TXN-9012` / `TXN-7712`), invokes remediation tool, and crafts customer reassurance | M3 | R2, R3 / Survey 2, 3 | DONE |
| 13 | Frontend UI Compatibility Preservation | Maintain 100% frozen `public/` files; preserve all SSE events, `/api/customer/verify`, `/api/resolve/action`, and 4-stage markdown format | M3 | R4 / Survey 2 | DONE |
| 14 | Opaque-Box E2E Test Suite | Comprehensive 4-tier requirement-driven test suite validating startup, fallback, duplicate charge remediation, and UI contracts | M4 / E2E Track | Acceptance Criteria | DONE (by E2E Track) |
| 15 | Adversarial Coverage Hardening | White-box stress testing and edge-case hardening (Tier 5) | M4 | Methodology | DONE |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| E2E | E2E Testing Suite Track | Design test infrastructure and 4-tier test cases covering Features 1-13; publish `TEST_READY.md` | none | DONE |
| 1 | Legacy ML Removal & Startup Decoupling | Delete `CRA_Models/`, `app/cra/`; clean `requirements.txt`; decouple `app/main.py:lifespan`; create lightweight non-ML triage; update existing tests | none | DONE |
| 2 | Generic Database Tools & Ledger Remediation | Fix query fallback; implement generic inspection and remediation tools; ensure atomic CSV updates; add test isolation fixture | none | DONE |
| 3 | Troy Local LLM Engine & Autonomous Tool Execution | Configure LM Studio port 1234 & env var; implement autonomous decision flow; implement local LLM client with mock fallback; execute DB tools on duplicate charges; preserve UI contracts | M1, M2 | DONE |
| 4 | Final Milestone: E2E Test Pass & Hardening | Phase 1: Pass 100% of E2E tests (Tiers 1-4); Phase 2: Adversarial coverage hardening (Tier 5) | M3, E2E | DONE (PASS) |

---

## Interface Contracts

### 1. Triage Helper (`app/triage.py`) ↔ Backend / Troy
- **Function**: `triage_complaint(text: str) -> Dict[str, Any]`
  - Output schema contains: `predicted_category`, `category_confidence`, `recommended_team`, `sla`, `similar_cases`, `extracted_entities`.
- **Function**: `predict_category(text: str) -> CategoryResult`
  - Returns `CategoryResult` (str and Mapping dual-type).

### 2. Database Tools (`app/database.py`) ↔ Troy Resolution Engine
- **`query_customer_account(customer_id: str = None, email: str = None, order_id: str = None) -> Optional[Dict[str, str]]`**
  - Returns customer account dict or None if not found.
- **`query_transactions(customer_id: str = None, order_id: str = None, status: str = None, billing_cycle: str = None) -> List[Dict[str, str]]`**
  - Returns list of matching transaction dicts.
- **`inspect_account_and_transactions(customer_id: str = None, query: str = None) -> Dict[str, Any]`**
  - Combines account details, transactions, duplicate detection flags, and billing cycle audit.
- **`update_transaction(transaction_id: str, updates: Dict[str, Any]) -> bool`**
  - Updates specified columns in `data/transactions.csv`.
- **`update_customer_account(customer_id: str, updates: Dict[str, Any]) -> bool`**
  - Updates specified columns in `data/customer_accounts.csv`.
- **`remediate_duplicate_charge(order_id: str, duplicate_txn_id: str, refund_amount: str = "49.00", processed_by: str = "Troy (Autonomous Resolution Agent)", notes: str = "") -> Dict[str, Any]`**
  - Atomically marks `duplicate_txn_id` as `Refunded`, sets `refund_id`, restores customer account balance to `$0.00 (Settled)`, and sets account status to `Active - In Good Standing`. Idempotent if already refunded.

### 3. Local LLM Provider (`app/providers/openai_driver.py`) ↔ Troy
- **Default Base URL**: `os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")`
- **Fallback**: When HTTP connection fails or timeout occurs, transparently switch to `MockDriver` or deterministic streaming fallback without throwing unhandled exceptions.

### 4. Backend SSE & REST ↔ Frontend UI (`public/`)
- Frozen contracts. Must support:
  - `POST /api/next` -> initiates resolution session.
  - `GET /api/stream` -> SSE stream sending `topic`, `feed`, `cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`.
  - `GET /api/customer/verify?query=...&category=...` -> customer ledger verification card payload.
  - `POST /api/resolve/action` -> action execution result.
  - 4-stage markdown format: `### **<Title>**` for executive, root cause, customer comms, and internal routing.

---

## Code Layout
- `app/`
  - `main.py` — FastAPI application routes and startup lifespan
  - `database.py` — Database tools, CSV operations, query and remediation logic
  - `triage.py` — Non-ML category triage and case assessment payload generator
  - `providers/`
    - `__init__.py` — Provider URLs and registry
    - `openai_driver.py` — OpenAI-compatible LLM driver (LM Studio on port 1234)
    - `mock_driver.py` — Deterministic fallback mock driver
  - `simulation/`
    - `troy.py` — Troy autonomous resolution agent, decision routing, tool execution, SSE broadcasting
- `data/`
  - `transactions.csv` — Transaction records (17 columns)
  - `customer_accounts.csv` — Customer accounts (20 columns)
- `tests/`
  - `conftest.py` — Pytest fixtures including `isolated_database`
  - `test_e2e_troy.py` — E2E tests (Tiers 1-4)
  - `test_api_endpoints.py` — API endpoint tests
  - `test_database_tools.py` — Database tool inspection and remediation tests
- `public/` — Unmodified frontend static assets (read-only)
- `requirements.txt` — Python dependencies (lightweight, zero ML)
