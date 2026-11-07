## 2026-11-06T22:18:26Z
You are Milestone 2 Remediation Worker (m2_worker_2_g2).
Your working directory is: D:\CRA\.agents\teamwork\m2_worker_2_g2\
Scope document: D:\CRA\PROJECT.md
Original user request: D:\CRA\ORIGINAL_REQUEST.md
Challenger handoff report with required changes: D:\CRA\.agents\teamwork\m2_challenger_1_g2\handoff.md
Challenger analysis: D:\CRA\.agents\teamwork\m2_challenger_1_g2\analysis.md
File to modify: D:\CRA\app\database.py
Adversarial test suite to update: D:\CRA\tests\test_challenger1_m2.py

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

You MUST read D:\CRA\ORIGINAL_REQUEST.md and D:\CRA\PROJECT.md before starting.

Objective:
Resolve the 3 specific defects reported by Challenger 1 in `app/database.py`:
1. Defect 1 (Critical Concurrency on Windows):
   In `app/database.py`, wrap `read_all_transactions()` and `read_all_customer_accounts()` with `with _DB_LOCK:`.
   On Windows, unlocked reads hold open file handles that block writer `os.replace` calls with `[WinError 5] Access is denied`. Wrapping reads in `_DB_LOCK` completely eliminates this contention.
2. Defect 2 (Multi-Table Atomicity & Rollback):
   In `remediate_duplicate_charge()`, ensure:
   - Check `tx_ok` before calling `write_all_customer_accounts()`. If `tx_ok` fails, do NOT attempt `write_all_customer_accounts()`, and return failure immediately.
   - If `tx_ok` succeeds but `write_all_customer_accounts()` fails, take a pre-call snapshot and roll back `transactions.csv` to the pre-call state so disk never remains in a half-refunded desynchronized state.
3. Defect 3 (Idempotency Invariant):
   In `remediate_duplicate_charge()`, when an existing transaction is already `Refunded`, preserve the existing `refund_amount` on disk rather than overwriting it with a new caller argument.
4. Test Harness:
   Remove the `@pytest.mark.xfail` decorators from `tests/test_challenger1_m2.py`.
   Run:
   - `pytest tests/test_challenger1_m2.py -v` (Must pass all 12 tests, 0 xfailed, 0 failed).
   - `pytest tests/test_e2e_troy.py -k "feature5 or feature6 or feature7 or feature8" -v` (Must pass all 40 tests).
   - `pytest tests/test_e2e_troy.py -k "feature12" -v` (Must pass all 10 tests).
   - `pytest --ignore=tests/test_e2e_troy.py -v` (Must pass all 104 tests without regressions).

Output:
Write `changes.md` and `handoff.md` in your working directory.
When complete, notify caller with `send_message`.
