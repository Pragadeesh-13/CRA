# Troy E2E Test Infrastructure & Methodology

## 1. Overview & Testing Philosophy

The Troy Complaint Resolution Agent system is verified using an **opaque-box, requirement-driven E2E test methodology**. The test suite treats the system as a black box with observable interfaces (HTTP REST APIs, Server-Sent Events, database CSV state, and environment configuration). Expected outputs are derived strictly from authoritative requirements in `ORIGINAL_REQUEST.md` and `PROJECT.md`.

### Core Testing Invariants
1. **Opaque-Box Requirement-Driven**: Tests assert observable behaviors, interface contracts, and persisted state rather than private internal implementation details.
2. **Deterministic Independence**: Every test runs in complete isolation. The `isolated_database` fixture sandboxes `data/transactions.csv` and `data/customer_accounts.csv` inside ephemeral temporary directories (`tmp_path`), guaranteeing zero state pollution across test runs or source data.
3. **No Facade Tests**: Every test exercises genuine execution paths, schema validations, and database mutations. Assertions check concrete state changes (e.g. status transition from `Settled` to `Refunded`, balance reconciliation to `$0.00 (Settled)`, and exact event payloads).
4. **Adversarial & Fault Injection**: Validates system resilience against missing files, malformed payloads, network timeouts, offline local servers (LM Studio unreachable), and injection attempts.

---

## 2. Test Infrastructure Architecture

```
                    ┌──────────────────────────────────────────┐
                    │            pytest Test Runner            │
                    │         (Python 3.14 / pytest 9)         │
                    └────────────────────┬─────────────────────┘
                                         │
               ┌─────────────────────────┼─────────────────────────┐
               ▼                         ▼                         ▼
   ┌───────────────────────┐ ┌───────────────────────┐ ┌───────────────────────┐
   │   Isolated Database   │ │  FastAPI TestClient   │ │   LLM Driver / Mock   │
   │  Fixture (Sandboxed)  │ │  REST & SSE Streaming │ │   Offline Fallback    │
   ├───────────────────────┤ ├───────────────────────┤ ├───────────────────────┤
   │ • tmp_path copy of    │ │ • POST /api/next      │ │ • LM Studio Port 1234 │
   │   transactions.csv    │ │ • GET /api/stream     │ │ • LMSTUDIO_BASE_URL   │
   │ • tmp_path copy of    │ │ • GET /api/customer/  │ │ • Deterministic mock  │
   │   accounts.csv        │ │   verify              │ │   token streaming     │
   │ • Atomic rollback on  │ │ • POST /api/resolve/  │ │ • 4-stage markdown    │
   │   teardown            │ │   action              │ │   section validation  │
   └───────────────────────┘ └───────────────────────┘ └───────────────────────┘
```

### Component Breakdown
- **Test Runner**: Pytest 9.x with `pytest-asyncio` strict mode and `TestClient` (Starlette / HTTPX).
- **Database Sandbox (`isolated_database`)**: Defined in `tests/conftest.py`. Automatically duplicates `data/transactions.csv` and `data/customer_accounts.csv` to an isolated `tmp_path` directory per test, monkeypatching `app.database.DATABASE_FILE`, `app.database.CUSTOMER_ACCOUNTS_FILE`, and `app.database.DATA_DIR`.
- **SSE Stream Validator**: Helper utility `consume_sse_stream` that ingests `text/event-stream` chunks from `GET /api/stream` and parses event lines (`event: <type>`, `data: <json>`) to verify event ordering and schemas.
- **Frontend Immutability Verifier**: Verifies MD5 checksums / presence of static frontend files under `public/` to ensure zero frontend regression.

---

## 3. Four-Tier Test Hierarchy

The test suite in `tests/test_e2e_troy.py` is partitioned into four requirement-driven tiers:

### Tier 1: Feature Coverage (>=5 tests per feature, Features 1–13)
Exercises the primary behavioral contracts (happy paths) for every individual feature identified in `PROJECT.md § Feature Inventory`:
- **Feature 1 (ML Removal)**: Absence of `CRA_Models/`, deletion of `app/cra/`, absence of FAISS indexes, XGBoost models, and DistilBERT checkpoints.
- **Feature 2 (Clean Requirements)**: Absence of `torch`, `transformers`, `sentence-transformers`, `xgboost`, `faiss-cpu`; presence of `fastapi`, `uvicorn`, `httpx`, `pydantic`.
- **Feature 3 (Decoupled Startup)**: Sub-second lifespan startup, CPU-only execution, zero ML warmup tasks, immediate `/health` readiness, clean shutdown.
- **Feature 4 (Non-ML Triage)**: Schema validity of `triage_complaint`, canonical category mapping in `predict_category`, SLA risk estimation, routing recommendation across all 11 categories.
- **Feature 5 (No USR-10492 Fallback)**: Elimination of silent fallback; non-matching queries return empty/None; queries for other customers return only their records.
- **Feature 6 (Generic DB Inspection)**: `query_customer_account` (by ID, email, order), `query_transactions` (filtering by status, cycle), `inspect_account_and_transactions`.
- **Feature 7 (Generic DB Remediation)**: `update_transaction`, `update_customer_account`, `remediate_duplicate_charge` with atomic multi-table synchronization and idempotency.
- **Feature 8 (Database Isolation)**: Temporary directory duplication, disk write isolation, monkeypatch pointer redirection, clean teardown.
- **Feature 9 (LM Studio Configuration)**: Default port 1234 (`http://localhost:1234/v1`), `LMSTUDIO_BASE_URL` environment override, driver instantiation, config endpoints.
- **Feature 10 (Autonomous Intent Decision)**: Complaint keyword and context routing between DB remediation (billing/duplicate) vs direct response (technical/general inquiry).
- **Feature 11 (LLM Client & Mock Fallback)**: Transparent fallback to mock streaming when local server is offline, without throwing unhandled exceptions.
- **Feature 12 (Duplicate Charge Remediation)**: Audit of duplicate debit (`TXN-9012` / `TXN-7712`), execution of refund, balance reconciliation to `$0.00`, status transition.
- **Feature 13 (UI Compatibility)**: Unmodified `public/` static files, SSE event contract (`cra_report`, `agent_start`, `agent_delta`, `agent`, `resolution_end`), `/api/customer/verify`, `/api/resolve/action`.

### Tier 2: Boundary & Corner Cases (>=5 tests per feature, Features 1–13)
Stresses edge conditions, adversarial inputs, resource limits, and error handling:
- Empty strings, whitespace-only queries, and oversized payloads (>10,000 chars).
- Special characters, emojis, HTML/SQL injection payloads, and Unicode strings.
- Unknown customer IDs, missing order IDs, and non-existent transactions.
- Already-refunded transactions (idempotent no-op).
- Network disconnects, connection refused (`ECONNREFUSED`), and connection timeouts.
- File system edge cases (missing CSV files, zero-byte CSV files, header-only CSVs).

### Tier 3: Cross-Feature Combinations (Pairwise Coverage)
Evaluates interactions between multiple subsystems:
1. **Offline Local Server + Alex Morgan Duplicate Remediation**: Verifies Troy detects offline LM Studio, switches to mock fallback, audits database, and executes remediation.
2. **Offline Local Server + Emily Watson Duplicate Remediation**: Verifies multi-customer remediation works seamlessly in offline mock mode.
3. **Offline Local Server + General Inquiry**: Verifies technical inquiries generate direct customer guidance while leaving database CSVs completely untouched.
4. **Custom LM Studio Base URL + Non-ML Triage + Ledger Verification**: Verifies end-to-end integration across configuration, heuristic intake, and ledger reporting.
5. **Database Remediation Followed by Ledger Verification**: Tests that an action posted to `/api/resolve/action` immediately updates `/api/customer/verify` output.
6. **SSE Event Stream Integrity During Mock Fallback**: Verifies that streaming events (`cra_report`, `agent_start`, `agent_delta`, `resolution_end`) emit cleanly in offline mode.
7. **Idempotent Remediation Across Application Restart**: Remediates a duplicate debit, cycles the application lifespan, and verifies persisted state remains consistent.
8. **Concurrent Multi-Customer Querying**: Validates that concurrent queries for Alex Morgan and Emily Watson execute without race conditions or cross-talk.

### Tier 4: Real-World Application Scenarios (>=5 Scenarios)
Simulates complete real-world user journeys end-to-end:
1. **Scenario 1 — Alex Morgan Duplicate Renewal (ORD-88219)**:
   - Complaint: *"I was charged $49.00 twice for order ORD-88219 on my Pro Tier renewal. Please refund the second debit."*
   - Flow: Intake triage -> DB transaction audit -> Locate redundant debit `TXN-9012` -> Execute remediation (`Refunded`, `REF-88219-AUTO`, balance settled to `$0.00`) -> Customer reassurance response.
2. **Scenario 2 — Emily Watson Duplicate Renewal (ORD-81094)**:
   - Complaint: *"Emily Watson here. Order ORD-81094 had an accidental second debit of $49.00 on Sep 18. Fix this."*
   - Flow: Intake triage -> DB transaction audit -> Locate redundant debit `TXN-7712` -> Execute remediation -> Reconcile account status from `Flagged - Accidental Double Charge` to `Active - In Good Standing`.
3. **Scenario 3 — General Technical Inquiry (2FA SMS Gateway Failure)**:
   - Complaint: *"My 2FA SMS code is not arriving when I try to log in to my Enterprise account."*
   - Flow: Intake triage -> Heuristic classification (`Authentication & Authorization (2FA/SSO)`) -> Troy direct response -> Zero modifications to `transactions.csv`.
4. **Scenario 4 — Offline Local Server Fallback**:
   - Condition: LM Studio daemon stopped / port 1234 unreachable.
   - Flow: User initiates resolution -> System catches connection refusal -> Transparent mock fallback activates -> Generates 4-stage markdown resolution without unhandled errors.
5. **Scenario 5 — Frontend UI API End-to-End Compatibility**:
   - Flow: Verify ledger card via `/api/customer/verify` -> Submit complaint via `/api/next` -> Monitor SSE events on `/api/stream` -> Execute remediation via `/api/resolve/action` -> Verify static assets under `public/`.

---

## 4. Traceability Matrix

| Feature | Description | Tier 1 Tests | Tier 2 Tests | Tier 3 Tests | Tier 4 Scenarios |
|---------|-------------|--------------|--------------|--------------|-------------------|
| 1 | Delete Legacy ML Artifacts | 5 tests | 5 tests | — | — |
| 2 | Clean `requirements.txt` | 5 tests | 5 tests | — | — |
| 3 | Decoupled Application Startup | 5 tests | 5 tests | — | — |
| 4 | Non-ML Intake Triage Utility | 5 tests | 5 tests | Tier 3 #4 | Scenarios 1–5 |
| 5 | Remove USR-10492 Fallback | 5 tests | 5 tests | Tier 3 #8 | Scenarios 1, 2 |
| 6 | Generic DB Inspection Tools | 5 tests | 5 tests | Tier 3 #4, #5 | Scenarios 1, 2 |
| 7 | Generic DB Remediation Tools | 5 tests | 5 tests | Tier 3 #1, #2, #5 | Scenarios 1, 2 |
| 8 | Database Test Isolation Fixture | 5 tests | 5 tests | All Tier 3 | All Tier 4 |
| 9 | LM Studio Default & Config | 5 tests | 5 tests | Tier 3 #4 | Scenario 4 |
| 10 | Autonomous Intent Decision | 5 tests | 5 tests | Tier 3 #1, #3 | Scenarios 1, 2, 3 |
| 11 | Local LLM Client & Mock Fallback | 5 tests | 5 tests | Tier 3 #1, #2, #3, #6 | Scenario 4 |
| 12 | Duplicate Charge Remediation | 5 tests | 5 tests | Tier 3 #1, #2, #7 | Scenarios 1, 2 |
| 13 | Frontend UI Compatibility | 5 tests | 5 tests | Tier 3 #5, #6 | Scenario 5 |
| **Total** | | **65 tests** | **65 tests** | **8 tests** | **5 scenarios** |

**Grand Total**: **143 Requirement-Driven E2E Tests**.

---

## 5. Test Execution Guide

### Full Suite Run
```powershell
pytest tests/test_e2e_troy.py -v
```

### Run by Tier
```powershell
# Tier 1: Feature Coverage
pytest tests/test_e2e_troy.py -k "tier1" -v

# Tier 2: Boundary & Corner Cases
pytest tests/test_e2e_troy.py -k "tier2" -v

# Tier 3: Pairwise Combinations
pytest tests/test_e2e_troy.py -k "tier3" -v

# Tier 4: Real-World Scenarios
pytest tests/test_e2e_troy.py -k "tier4" -v
```

### Run Specific Feature Tests
```powershell
# Feature 6 Generic DB Inspection
pytest tests/test_e2e_troy.py -k "feature6" -v

# Feature 12 Duplicate Charge Remediation
pytest tests/test_e2e_troy.py -k "feature12" -v
```

### Environment Configuration
- `LMSTUDIO_BASE_URL`: Override local LLM endpoint (default: `http://localhost:1234/v1`).
- `LLM_PROVIDER`: Set active provider (`lmstudio` or `mock`).
- `AUTH_MODE`: Authentication mode for testing (`optional` or `disabled`).
