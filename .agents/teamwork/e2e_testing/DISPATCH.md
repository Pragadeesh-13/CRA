# Dispatch: E2E Testing Track

Working directory: D:\CRA\.agents\teamwork\e2e_testing\
Original request file: D:\CRA\.agents\teamwork\ORIGINAL_REQUEST.md
Project Scope Document: D:\CRA\PROJECT.md

## Mission & Scope
You are the E2E Testing Lead / Writer for the Troy Complaint Resolution project.
Your objective is to design the opaque-box test infrastructure and author a comprehensive 4-tier requirement-driven E2E test suite covering all features in `PROJECT.md § Feature Inventory` and all acceptance criteria in `ORIGINAL_REQUEST.md`.

## Methodology (Tiers 1-4)
- **Tier 1 - Feature Coverage (>=5 per feature)**: Representative inputs for Features 1-13.
- **Tier 2 - Boundary & Corner Cases (>=5 per feature)**: Edge cases, empty inputs, malformed queries, unknown customers, network timeouts, offline fallback.
- **Tier 3 - Cross-Feature Combinations (pairwise coverage)**: Combinations of complaint types, customer profiles, DB state, LLM availability.
- **Tier 4 - Real-World Application Scenarios (>=5 scenarios)**: End-to-end user workflows:
  1. Duplicate charge for Alex Morgan (Order ORD-88219) -> DB inspection -> remediation -> customer response.
  2. Duplicate charge for Emily Watson (Order ORD-81094) -> DB inspection -> remediation -> customer response.
  3. General inquiry (technical/informational) -> direct response without modifying transactions.csv.
  4. Offline local server (LM Studio down) -> fallback mock generates clean 4-stage response and intake report.
  5. UI API compatibility (/api/customer/verify, /api/resolve/action, SSE stream event contracts).

## Deliverables
1. `TEST_INFRA.md` at project root `D:\CRA\TEST_INFRA.md` following the template in Project Pattern.
2. Comprehensive test suite in `tests/test_e2e_troy.py` (and any necessary fixtures in `tests/conftest.py`).
3. Signal completion by writing `TEST_READY.md` at project root `D:\CRA\TEST_READY.md`.
4. Report back when test suite is fully designed and written.


## 2026-11-06T21:12:23Z
You are the E2E Test Writer for the Troy project.
Your working directory is D:\CRA\.agents\teamwork\e2e_testing\
Read your task in D:\CRA\.agents\teamwork\e2e_testing\DISPATCH.md, D:\CRA\.agents\teamwork\ORIGINAL_REQUEST.md, and D:\CRA\PROJECT.md.

Design and write:
1. D:\CRA\TEST_INFRA.md following the template in Project Pattern (opaque-box, requirement-driven, 4 tiers).
2. D:\CRA\tests\test_e2e_troy.py implementing the 4-tier requirement-driven E2E test suite covering Features 1-13 from PROJECT.md:
   - Tier 1: Feature coverage (>=5 tests per feature)
   - Tier 2: Boundary & corner cases (>=5 tests per feature)
   - Tier 3: Cross-feature combinations (pairwise coverage)
   - Tier 4: Real-world application scenarios (>=5 scenarios: duplicate charges for Alex Morgan, Emily Watson, direct inquiry response, offline fallback mock, UI API compatibility)
3. When the test infrastructure and test cases are ready, create D:\CRA\TEST_READY.md at project root with runner command, coverage table, and feature checklist.
4. Update progress.md in your directory as you work.
When finished, send a completion message to parent with summary and file paths.
