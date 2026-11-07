# Scope: Milestone 3 Gate Evaluation & Milestone 4 Core Implementation and Final Integration

## Architecture
Troy Local LLM Autonomous Complaint Resolution Engine:
- FastAPI backend serving agentic complaint resolution (`app/main.py`, `app/simulation/troy.py`).
- LLM Provider Layer (`app/providers/openai_driver.py`, `app/providers/mock_driver.py`) communicating with LM Studio (port 1234, `LMSTUDIO_BASE_URL`) with deterministic mock fallback when offline.
- Database & Persistence Layer (`app/database.py`) providing generic inspection and atomic duplicate charge remediation tools over CSVs.
- Local LLM Intake & Triage Layer (`app/triage.py`): Replaces Python heuristic models with Local LLM (OpenAI-compatible / LM Studio with mock fallback) complaint categorization, entity extraction, SLA estimation, and `cra_report` payload generation.
- Frontend Layer (`public/`): 100% unmodified static files, preserving all SSE events and REST endpoint schemas.

## Feature Inventory
| # | Feature | Description | Milestone | Source | Status |
|---|---------|-------------|-----------|--------|--------|
| 1 | Delete Legacy ML Artifacts | Remove `CRA_Models/` and `app/cra/` completely | M1 | R1 | DONE |
| 2 | Clean `requirements.txt` | Remove torch, transformers, xgboost, etc. | M1 | R1 | DONE |
| 3 | Decoupled Application Startup | Fast startup < 1s without ML weights | M1 | R1 | DONE |
| 4 | Non-ML Intake Triage Utility | Initial heuristic triage utility | M1 | R1 | DONE |
| 5 | Remove Hardcoded USR-10492 Fallback | Multi-customer query support | M2 | R3 | DONE |
| 6 | Generic Database Inspection Tools | Query accounts and transactions | M2 | R3 | DONE |
| 7 | Generic Database Remediation Tools | Atomic duplicate charge remediation | M2 | R3 | DONE |
| 8 | Database Test Isolation Fixture | `isolated_database` fixture | M2 | Criteria | DONE |
| 9 | LM Studio Provider Default & Config | Port 1234 & `LMSTUDIO_BASE_URL` config | M3 | R2 | DONE |
| 10 | Autonomous Intent Decision | Direct response vs DB remediation | M3 | R2, R3 | DONE |
| 11 | Local LLM Client & Mock Fallback | Transparent mock fallback on offline/timeout | M3 | R2 | DONE |
| 12 | End-to-End Duplicate Charge Remediation | Audit, remediate, restore $0.00, reassure | M3 | R2, R3 | DONE |
| 13 | Frontend UI Compatibility Preservation | Frozen `public/`, SSE & REST contracts | M3 | R4 | DONE |
| 14 | Opaque-Box E2E Test Suite | 143 requirement-driven tests | E2E | Criteria | DONE |
| 15 | Adversarial Coverage Hardening | White-box stress testing | M4 | Methodology | DONE |
| 16 | Local LLM Triage & Categorization | Send complaints to Local LLM (LM Studio / mock fallback) for classification, entities, and `cra_report` | M4 | R1 (New Prompt) | DONE |
| 17 | Final Verification Test Pass | Pass test suites across `test_e2e_troy.py`, `test_database_tools.py`, `test_api_endpoints.py` | M4 | R4 (New Prompt) | DONE |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Legacy ML Removal & Startup Decoupling | Delete ML artifacts, clean deps, fast startup | none | DONE |
| 2 | Generic Database Tools & Ledger Remediation | Multi-customer queries, atomic duplicate remediation | none | DONE |
| 3 | Troy Local LLM Engine & Autonomous Tool Execution | Complete Gate Evaluation (Reviewers, Challengers, Forensic Auditor) | M1, M2 | DONE |
| 4 | Core Implementation & Final Integration | Replace Python heuristic triage with Local LLM triage (LM Studio / mock fallback), integrate with Troy & UI, and execute final verification test suites | M3 | DONE |

## Interface Contracts
### 1. Local LLM Triage Helper (`app/triage.py`) ↔ Backend / Troy / UI
- `triage_complaint(text: str) -> Dict[str, Any]`
  - Connects to local LLM (LM Studio / OpenAI driver) with deterministic fallback.
  - Returns schema-compliant dict:
    - `predicted_category`: str (one of 11 categories)
    - `category_confidence`: float (0.0 to 1.0)
    - `recommended_team`: str
    - `sla`: Dict with `estimated_hours`, `priority`, etc.
    - `predicted_sla_hours`: float
    - `extracted_entities`: Dict (account_number, order_id, transaction_id, customer_id, amount, etc.)
    - `similar_cases`: List[Dict]
    - `summary`: str
- `predict_category(text: str) -> CategoryResult`
  - Returns CategoryResult compatible with UI endpoints (`/api/cra/predict`, `/api/cra/triage`).

### 2. Database Tools (`app/database.py`) ↔ Troy
- Inspection: `query_customer_account`, `query_transactions`, `inspect_account_and_transactions`.
- Remediation: `update_transaction`, `update_customer_account`, `remediate_duplicate_charge`.

### 3. Local LLM Provider (`app/providers/openai_driver.py`) ↔ Troy / Triage
- Base URL: `os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")`.
- Transparent fallback to `MockDriver` when offline/timeout.

### 4. SSE Stream & REST API ↔ Frontend (`public/`)
- Frozen contracts: `/api/next`, `/api/stream`, `/api/customer/verify`, `/api/resolve/action`, `/api/cra/triage`, `/api/cra/predict`.
