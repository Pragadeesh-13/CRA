# Dispatch: Milestone 2 Worker

Working directory: D:\CRA\.agents\teamwork\m2_worker_1\
Original request file: D:\CRA\.agents\teamwork\ORIGINAL_REQUEST.md
Scope document: D:\CRA\PROJECT.md

## Mission & Scope
Implement Milestone 2: Generic Database Tools & Ledger Remediation (Features 5, 6, 7, 8).
You have exclusive write ownership over:
- `app/database.py`

DO NOT touch any files under `public/`, `data/`, or `app/simulation/` or `app/main.py`.

## Explorer Inputs
Read the complete handoffs and implementation guides from the 3 Explorers:
- `D:\CRA\.agents\teamwork\m2_explorer_1\handoff.md` (Query tools & USR-10492 fallback fix)
- `D:\CRA\.agents\teamwork\m2_explorer_2\handoff.md` (Remediation tools & multi-table sync)
- `D:\CRA\.agents\teamwork\m2_explorer_3\handoff.md` (Verification, seeding resilience & alias fix)

## Implementation Steps
1. **Fix Fallback & Matching (`app/database.py`)**:
   - In `find_transactions_for_query` (lines 395-398): Remove the hardcoded fallback to Alex Morgan (`USR-10492`). If no match is found, return `[]`.
   - In `find_customer_account_by_query` (line 306): Change `if target_id == acc_id or target_id in acc_id:` to exact equality `if target_id == acc_id:` to prevent partial ID collisions (`USR-1049` != `USR-10492`).

2. **Implement Generic Inspection Tools (`app/database.py`)**:
   - `query_customer_account(customer_id: str = None, email: str = None, order_id: str = None) -> Optional[Dict[str, str]]`
   - `query_transactions(customer_id: str = None, order_id: str = None, status: str = None, billing_cycle: str = None, transaction_id: str = None) -> List[Dict[str, str]]`
   - `inspect_account_and_transactions(customer_id: str = None, query: str = None) -> Dict[str, Any]` (combining customer details, transactions, duplicate detection flags, and billing cycle audit).

3. **Implement Generic Remediation Tools (`app/database.py`)**:
   - `update_transaction(transaction_id: str, updates: Dict[str, Any]) -> bool`
   - `update_customer_account(customer_id: str, updates: Dict[str, Any]) -> bool`
   - `remediate_duplicate_charge(order_id: str, duplicate_txn_id: str, refund_amount: str = "49.00", processed_by: str = "Troy (Autonomous Resolution Agent)", notes: str = "") -> Dict[str, Any]`
   - Ensure atomic synchronization across BOTH `data/transactions.csv` and `data/customer_accounts.csv`.
   - Ensure idempotency: if transaction is already `Refunded`, return confirmation with `is_already_refunded=True` without duplicate mutations.

4. **Fix Customer Account Alias (`app/database.py`)**:
   - At line 1271: Map `read_all_customers = read_all_customer_accounts` (was incorrectly mapped to `read_all_transactions`).

5. **Ensure CSV Seeding Resilience (`app/database.py`)**:
   - Define `DEFAULT_CUSTOMER_ACCOUNTS` (canonical 6 accounts, 20 columns) in `app/database.py`.
   - Update `ensure_database_file()` to seed `CUSTOMER_ACCOUNTS_FILE` if missing.
   - Keep `read_all_customer_accounts()` non-auto-generating (returning `[]` if file doesn't exist) so missing-file test passes gracefully.

6. **Verification**:
   - Run `pytest tests/test_e2e_troy.py -k "feature5 or feature6 or feature7 or feature8" -v` (all 40 tests must pass).
   - Run `pytest tests/test_e2e_troy.py -k "tier3 and (duplicate or inquiry or restart or concurrent)" -v`.
   - Run `pytest --ignore=tests/test_e2e_troy.py -v` (all 91 existing unit tests must pass).

7. **Documentation**:
   - Write `changes.md` and `handoff.md` in your directory.

## MANDATORY INTEGRITY WARNING
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.
