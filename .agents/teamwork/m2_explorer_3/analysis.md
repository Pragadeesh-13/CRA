# Milestone 2 Explorer 3: Verification & CSV Seeding Resilience Analysis

**Working Directory**: `D:\CRA\.agents\teamwork\m2_explorer_3\`  
**Target Files**: `app/database.py`, `tests/conftest.py`, `tests/test_e2e_troy.py`  
**Scope**: Features 5, 6, 7, 8 test suite coverage (40 tests across Tiers 1-2 + Tier 3 cross-feature tests), persistent CSV seeding resilience in `ensure_database_file`, test sandboxing via `isolated_database`, and worker verification procedures.

---

## 1. Executive Summary

Milestone 2 establishes generic, unbiased database query and remediation capabilities over `data/transactions.csv` and `data/customer_accounts.csv`. A baseline run of the 40 dedicated tests for Features 5, 6, 7, and 8 in `tests/test_e2e_troy.py` reveals:
- **Feature 8 (Test Isolation Fixture)**: 10/10 tests **PASS**. The `isolated_database` fixture in `tests/conftest.py` cleanly isolates both CSV files in temporary directories and prevents test pollution.
- **Feature 5 (Remove Hardcoded Fallback)**: 6/10 tests PASS, 4/10 tests **FAIL** due to hardcoded fallback `return [t for t in transactions if t.get("customer_id") == "USR-10492"]` and partial substring matching `target_id in acc_id`.
- **Feature 6 (Generic Inspection Tools)**: 1/10 tests PASS, 9/10 tests **FAIL** due to missing `query_customer_account`, `query_transactions`, and `inspect_account_and_transactions`.
- **Feature 7 (Generic Remediation Tools)**: 0/10 tests PASS, 10/10 tests **FAIL** due to missing `update_transaction`, `update_customer_account`, and `remediate_duplicate_charge`.
- **CSV Seeding Resilience**: `ensure_database_file()` currently only seeds `transactions.csv` and completely ignores `customer_accounts.csv`. `DEFAULT_CUSTOMER_ACCOUNTS` does not exist in `app/database.py`. If `data/customer_accounts.csv` is absent, the system does not recreate it, causing silent `[]` returns.
- **Backwards Compatibility Bug**: `app/database.py:1271` maps `read_all_customers = read_all_transactions`. However, tests in Feature 7 explicitly inspect `account_balance` and `account_status` on records returned by `database.read_all_customers()`. This alias MUST be remapped to `read_all_customer_accounts`.

---

## 2. Test Requirements Matrix (40 Tests in `tests/test_e2e_troy.py`)

### Feature 5: Remove Hardcoded USR-10492 Fallback (10 Tests)

| # | Test Function | Line | Current Status | Cause & Required Worker Fix |
|---|---------------|------|----------------|-----------------------------|
| 1 | `test_tier1_feature5_unknown_customer_id_returns_none_or_empty` | 504 | **FAIL** | `find_transactions_for_query("USR-99999")` falls back to Alex Morgan at line 398. **Fix**: Implement `query_transactions("USR-99999") -> []` and remove Alex Morgan fallback in `find_transactions_for_query` to return `[]`. |
| 2 | `test_tier1_feature5_query_emily_watson_returns_emily_only` | 518 | PASS | Currently resolves via `find_customer_account_by_query`. Must remain supported in `query_customer_account(customer_id="USR-07231")`. |
| 3 | `test_tier1_feature5_query_sarah_jenkins_returns_sarah_only` | 531 | PASS | Must remain supported in `query_customer_account(customer_id="USR-08914")`. |
| 4 | `test_tier1_feature5_unmatched_query_string_no_silent_fallback` | 544 | **FAIL** | Line 398 returns Alex Morgan when query matches nothing. **Fix**: Implement `query_customer_account(None, None, None) -> None` and remove line 398 fallback. |
| 5 | `test_tier1_feature5_exact_customer_id_filtering` | 557 | PASS | Filters strictly to customer transactions. Must be supported in `query_transactions`. |
| 6 | `test_tier2_feature5_empty_query_string_handling` | 570 | PASS | `query_customer_account(customer_id="")` must return `None`. |
| 7 | `test_tier2_feature5_case_insensitive_matching` | 582 | PASS | `usr-07231` resolves to `USR-07231`. |
| 8 | `test_tier2_feature5_whitespace_padded_id` | 595 | PASS | `"   USR-07231   "` resolves to `USR-07231`. |
| 9 | `test_tier2_feature5_nonexistent_order_id_returns_empty` | 608 | **FAIL** | `ORD-00000` falls back to line 398. **Fix**: Implement `query_transactions(order_id="ORD-00000") -> []` and return `[]` when no match found. |
| 10 | `test_tier2_feature5_partial_id_collision_prevention` | 619 | **FAIL** | Line 306 uses `target_id in acc_id`, matching `"USR-1049"` to `"USR-10492"`. **Fix**: Change to exact match `target_id == acc_id` (case-insensitive after strip). |

### Feature 6: Generic Database Inspection Tools (10 Tests)

| # | Test Function | Line | Current Status | Cause & Required Worker Fix |
|---|---------------|------|----------------|-----------------------------|
| 11 | `test_tier1_feature6_query_customer_account_by_id` | 637 | **FAIL** | Missing `query_customer_account`. Returns customer dict for `USR-10492` with `customer_name: "Alex Morgan"`, `reference_order_id: "ORD-88219"`. |
| 12 | `test_tier1_feature6_query_customer_account_by_email` | 648 | **FAIL** | Missing `query_customer_account(email="emily.w@consulting.co")`. Returns `USR-07231`. |
| 13 | `test_tier1_feature6_query_customer_account_by_order_id` | 658 | **FAIL** | Missing `query_customer_account(order_id="ORD-79110")`. Returns `USR-08914`. |
| 14 | `test_tier1_feature6_query_transactions_status_filter` | 668 | **FAIL** | Missing `query_transactions(status="Settled")`. Returns list of transactions where `status == "Settled"`. |
| 15 | `test_tier1_feature6_inspect_account_and_transactions` | 678 | **FAIL** | Missing `inspect_account_and_transactions(customer_id="USR-10492")`. Returns dict containing `"account"` (or `"customer"`) and `"transactions"`. |
| 16 | `test_tier2_feature6_inspect_account_duplicate_detection` | 691 | **FAIL** | Missing `inspect_account_and_transactions`. For Alex Morgan (`USR-10492`), returns `has_duplicate=True` (or `duplicate_detected=True`, `is_already_refunded=True`). |
| 17 | `test_tier2_feature6_inspect_account_single_transaction_no_duplicate` | 700 | **FAIL** | Missing `inspect_account_and_transactions`. For Sarah Jenkins (`USR-08914`, 1 txn), returns `has_duplicate=False`. |
| 18 | `test_tier2_feature6_query_transactions_billing_cycle_filter` | 709 | **FAIL** | Missing `query_transactions(billing_cycle="October 2026")`. Returns matching transactions. |
| 19 | `test_tier2_feature6_query_sql_injection_safety` | 719 | **FAIL** | Missing `query_customer_account(customer_id="'; DROP TABLE transactions;--")`. Must return `None` safely without crash. |
| 20 | `test_tier2_feature6_query_missing_csv_file_graceful` | 728 | PASS | When `CUSTOMER_ACCOUNTS_FILE` points to non-existent file, returns `None`. Must NOT crash and must NOT unconditionally create file. |

### Feature 7: Generic Database Remediation Tools (10 Tests)

| # | Test Function | Line | Current Status | Cause & Required Worker Fix |
|---|---------------|------|----------------|-----------------------------|
| 21 | `test_tier1_feature7_update_transaction_status` | 744 | **FAIL** | Missing `update_transaction("TXN-7712", {"status": "Refunded", "refund_id": "REF-TEST-7712"}) -> True`. Persists updates to `transactions.csv`. |
| 22 | `test_tier1_feature7_update_customer_account_balance` | 759 | **FAIL** | Missing `update_customer_account("USR-07231", {"account_balance": "$0.00 (Settled)", "account_status": "Active - In Good Standing"}) -> True`. Persists to `customer_accounts.csv`. Also requires `read_all_customers = read_all_customer_accounts`. |
| 23 | `test_tier1_feature7_remediate_duplicate_charge_alex_morgan` | 773 | **FAIL** | Missing `remediate_duplicate_charge(order_id="ORD-88219", duplicate_txn_id="TXN-9012")`. Atomically sets `TXN-9012` to `Refunded` and `USR-10492` balance to `$0.00 (Settled)`, status to `Active - In Good Standing`. |
| 24 | `test_tier1_feature7_remediate_duplicate_charge_emily_watson` | 796 | **FAIL** | Missing `remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")`. Atomically updates `TXN-7712` and `USR-07231`. |
| 25 | `test_tier1_feature7_remediate_idempotency` | 816 | **FAIL** | Missing `remediate_duplicate_charge`. Second call must succeed with `ok=True` and `is_already_refunded=True` or `status="Refunded"`. |
| 26 | `test_tier2_feature7_remediate_nonexistent_transaction` | 832 | **FAIL** | `remediate_duplicate_charge(order_id="ORD-00000", duplicate_txn_id="TXN-INVALID")` returns `ok=False` or `"error"` key. |
| 27 | `test_tier2_feature7_atomic_multi_table_sync_integrity` | 841 | **FAIL** | Direct CSV disk read verifies synchronous write to both `DATABASE_FILE` and `CUSTOMER_ACCOUNTS_FILE`. |
| 28 | `test_tier2_feature7_custom_refund_amount` | 861 | **FAIL** | Supports custom `refund_amount="25.00"`. Persists `refund_amount` on transaction row. |
| 29 | `test_tier2_feature7_audit_trail_processor_name` | 873 | **FAIL** | Remediation records processor name containing `"Troy"`. |
| 30 | `test_tier2_feature7_remediation_timestamp_format` | 884 | **FAIL** | Remediation records non-empty timestamp in `refunded_at` (>5 chars). |

### Feature 8: Database Test Isolation Fixture (10 Tests)

| # | Test Function | Line | Current Status | Cause & Status |
|---|---------------|------|----------------|----------------|
| 31 | `test_tier1_feature8_copies_transactions_to_tmp` | 901 | **PASS** | Copies `transactions.csv` to `tmp_path`. |
| 32 | `test_tier1_feature8_copies_customer_accounts_to_tmp` | 908 | **PASS** | Copies `customer_accounts.csv` to `tmp_path`. |
| 33 | `test_tier1_feature8_modifications_do_not_affect_source_csv` | 915 | **PASS** | Source CSV in `data/` untouched by mutations in sandbox. |
| 34 | `test_tier1_feature8_monkeypatch_updates_module_constants` | 934 | **PASS** | Monkeypatches `DATABASE_FILE`, `CUSTOMER_ACCOUNTS_FILE`, `DATA_DIR`. |
| 35 | `test_tier1_feature8_clean_teardown_leaves_clean_state` | 941 | **PASS** | Verifies pristine rows (>=5 txns, >=5 custs). |
| 36 | `test_tier2_feature8_sequential_test_isolation_part_a` | 952 | **PASS** | Modifies isolated file. |
| 37 | `test_tier2_feature8_sequential_test_isolation_part_b` | 961 | **PASS** | Subsequent test starts with unpolluted state. |
| 38 | `test_tier2_feature8_custom_mock_data_injection` | 968 | **PASS** | Overwriting isolated CSV works cleanly. |
| 39 | `test_tier2_feature8_both_tables_isolated_simultaneously` | 983 | **PASS** | Both files isolated and share temporary parent directory. |
| 40 | `test_tier2_feature8_data_dir_monkeypatched` | 990 | **PASS** | `app.database.DATA_DIR == isolated_database["dir"]`. |

### Tier 3 Cross-Feature Tests Impacted by Milestone 2
- `test_tier3_offline_fallback_with_emily_watson_duplicate_remediation` (Line 1551): Will PASS once `remediate_duplicate_charge` is implemented.
- `test_tier3_idempotent_remediation_across_restart` (Line 1636): Will PASS once `remediate_duplicate_charge` is implemented.
- `test_tier3_multi_customer_concurrent_query_isolation` (Line 1651): Already PASSES!

---

## 3. Persistent CSV Seeding Resilience (`ensure_database_file`)

### 3.1 Problem Identified
In `app/database.py`:
1. `DEFAULT_TRANSACTIONS` (8 records) is present at lines 66-219.
2. `DEFAULT_CUSTOMER_ACCOUNTS` is **absent**.
3. `ensure_database_file()` only checks `DATABASE_FILE.exists()`. If `CUSTOMER_ACCOUNTS_FILE` is missing, it is never initialized or seeded.
4. If a fresh deployment or clean CI clone starts without `data/customer_accounts.csv`, any customer query returns `[]`.
5. In `tests/test_e2e_troy.py:728` (`test_tier2_feature6_query_missing_csv_file_graceful`), monkeypatching `CUSTOMER_ACCOUNTS_FILE` to a non-existent path tests that query functions return `None` gracefully without crashing. Therefore, `read_all_customer_accounts()` must NOT unconditionally re-seed on every read, but `ensure_database_file()` must provision both files when called during initialization or test setup.

### 3.2 Canonical Seed Data Specification (`DEFAULT_CUSTOMER_ACCOUNTS`)
The Worker must add `DEFAULT_CUSTOMER_ACCOUNTS` to `app/database.py` with 6 customer accounts exactly conforming to `CUSTOMER_ACCOUNT_FIELDNAMES` (20 fields):

```python
DEFAULT_CUSTOMER_ACCOUNTS: List[Dict[str, str]] = [
    {
        "customer_id": "USR-10492",
        "customer_name": "Alex Morgan",
        "customer_email": "alex.morgan@gmail.com",
        "reference_order_id": "ORD-88219",
        "subscription_plan": "Pro Tier ($49.00/mo)",
        "billing_cycle": "Monthly (20th)",
        "monthly_rate": "49.00",
        "total_billed_this_month": "49.00 (Net after $49.00 refund)",
        "charges_this_month": "2",
        "transaction_1": "TXN-9011 ($49.00 on 2026-10-20) [Settled Renewal]",
        "transaction_2": "TXN-9012 ($49.00 on 2026-10-23) [Refunded: REF-88219-AUTO]",
        "duplicate_verified": "Yes - Accidental Double Charge Detected",
        "refund_status": "Refunded ($49.00 via Stripe API)",
        "refund_id": "REF-88219-AUTO",
        "account_balance": "$0.00 (Settled)",
        "account_status": "Active - In Good Standing",
        "last_verified_by": "Troy (Autonomous Resolution Agent)",
        "updated_by": "Troy (Autonomous Resolution Agent)",
        "last_updated": "2026-10-23 02:20:53",
        "notes": "Autonomous override executed by Troy. Accidental double renewal debit reversed in Stripe API under REF-88219-AUTO.",
    },
    {
        "customer_id": "USR-08914",
        "customer_name": "Sarah Jenkins",
        "customer_email": "sarah.j@enterprise.org",
        "reference_order_id": "ORD-79110",
        "subscription_plan": "Enterprise Tier ($299.00/mo)",
        "billing_cycle": "Monthly (1st)",
        "monthly_rate": "299.00",
        "total_billed_this_month": "299.00",
        "charges_this_month": "1",
        "transaction_1": "TXN-8801 ($299.00 on 2026-10-01) [Settled]",
        "transaction_2": "None",
        "duplicate_verified": "No",
        "refund_status": "N/A",
        "refund_id": "",
        "account_balance": "$0.00 (Current)",
        "account_status": "Locked - 2FA SMS Gateway Failure",
        "last_verified_by": "Tier 1 Support",
        "updated_by": "Auth Service",
        "last_updated": "2026-10-21 09:30:00",
        "notes": "Account locked after 3 failed 2FA SMS attempts. Awaiting gateway reset.",
    },
    {
        "customer_id": "USR-12053",
        "customer_name": "David Chen",
        "customer_email": "d.chen@fintech.io",
        "reference_order_id": "ORD-93041",
        "subscription_plan": "Developer API Tier ($199.00/mo)",
        "billing_cycle": "Monthly (15th)",
        "monthly_rate": "199.00",
        "total_billed_this_month": "199.00",
        "charges_this_month": "1",
        "transaction_1": "TXN-8450 ($199.00 on 2026-10-15) [Settled]",
        "transaction_2": "None",
        "duplicate_verified": "No",
        "refund_status": "N/A",
        "refund_id": "",
        "account_balance": "$0.00 (Current)",
        "account_status": "Degraded - Webhook Gateway 504 Timeout",
        "last_verified_by": "Engineering Bot",
        "updated_by": "API Monitor",
        "last_updated": "2026-10-21 10:15:00",
        "notes": "Webhook retry queue flooded. Engineering investigation underway.",
    },
    {
        "customer_id": "USR-14309",
        "customer_name": "Elena Rostova",
        "customer_email": "elena@globaltrade.de",
        "reference_order_id": "ORD-85420",
        "subscription_plan": "Team Tier ($120.00/mo)",
        "billing_cycle": "Quarterly ($360.00)",
        "monthly_rate": "120.00",
        "total_billed_this_month": "360.00",
        "charges_this_month": "1",
        "transaction_1": "TXN-8201 ($360.00 on 2026-10-05) [Settled]",
        "transaction_2": "None",
        "duplicate_verified": "No",
        "refund_status": "N/A",
        "refund_id": "",
        "account_balance": "$0.00 (Current)",
        "account_status": "Active - Pending VAT Invoice Receipt",
        "last_verified_by": "Billing Team",
        "updated_by": "Invoicing Engine",
        "last_updated": "2026-10-20 16:40:00",
        "notes": "Formal German VAT tax invoice and receipt requested for accounting records.",
    },
    {
        "customer_id": "USR-09148",
        "customer_name": "Michael Scott",
        "customer_email": "m.scott@dunder.com",
        "reference_order_id": "ORD-86102",
        "subscription_plan": "Starter Tier ($29.00/mo)",
        "billing_cycle": "Monthly (18th)",
        "monthly_rate": "29.00",
        "total_billed_this_month": "79.00",
        "charges_this_month": "1",
        "transaction_1": "TXN-8902 ($79.00 on 2026-10-18) [Settled - Old Plan]",
        "transaction_2": "None",
        "duplicate_verified": "No",
        "refund_status": "Partial Refund Pending ($50.00)",
        "refund_id": "",
        "account_balance": "$-50.00 (Credit Due)",
        "account_status": "Pending Downgrade Adjustment",
        "last_verified_by": "Billing Bot",
        "updated_by": "Subscription Manager",
        "last_updated": "2026-10-20 18:00:00",
        "notes": "User requested downgrade prior to renewal. $50 prorated refund due.",
    },
    {
        "customer_id": "USR-07231",
        "customer_name": "Emily Watson",
        "customer_email": "emily.w@consulting.co",
        "reference_order_id": "ORD-81094",
        "subscription_plan": "Pro Tier ($49.00/mo)",
        "billing_cycle": "Monthly (18th)",
        "monthly_rate": "49.00",
        "total_billed_this_month": "98.00",
        "charges_this_month": "2",
        "transaction_1": "TXN-7711 ($49.00 on 2026-09-18) [Settled Renewal]",
        "transaction_2": "TXN-7712 ($49.00 on 2026-09-18) [Settled - Accidental Second Debit]",
        "duplicate_verified": "Yes - Accidental Double Charge Detected",
        "refund_status": "Pending Refund",
        "refund_id": "",
        "account_balance": "$-49.00 (Overcharged)",
        "account_status": "Flagged - Accidental Double Charge",
        "last_verified_by": "Troy AI",
        "updated_by": "System Billing Daemon",
        "last_updated": "2026-09-18 10:00:00",
        "notes": "Accidental duplicate renewal charge debited twice within 24 hours. Awaiting agent resolution.",
    },
]
```

### 3.3 Implementation of `ensure_database_file`
Update `ensure_database_file()` in `app/database.py`:
```python
def ensure_database_file() -> Path:
    """Ensures data directory, transactions CSV, and customer accounts CSV exist and are seeded."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATABASE_FILE.exists():
        with open(DATABASE_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(DEFAULT_TRANSACTIONS)
        logger.info(f"Initialized transactions database at {DATABASE_FILE}")

    if not CUSTOMER_ACCOUNTS_FILE.exists():
        with open(CUSTOMER_ACCOUNTS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CUSTOMER_ACCOUNT_FIELDNAMES)
            writer.writeheader()
            writer.writerows(DEFAULT_CUSTOMER_ACCOUNTS)
        logger.info(f"Initialized customer accounts database at {CUSTOMER_ACCOUNTS_FILE}")

    return DATABASE_FILE


def ensure_customer_accounts_file() -> Path:
    """Ensures customer accounts CSV exists and is seeded, returning its Path."""
    ensure_database_file()
    return CUSTOMER_ACCOUNTS_FILE
```

### 3.4 Read and Write Safety
- `read_all_customer_accounts()` must retain the existence check:
  ```python
  def read_all_customer_accounts() -> List[Dict[str, str]]:
      """Reads all rows from data/customer_accounts.csv."""
      if not CUSTOMER_ACCOUNTS_FILE.exists():
          return []
      records = []
      try:
          with open(CUSTOMER_ACCOUNTS_FILE, "r", newline="", encoding="utf-8") as f:
              reader = csv.DictReader(f)
              for row in reader:
                  records.append(dict(row))
      except Exception as e:
          logger.error(f"Error reading {CUSTOMER_ACCOUNTS_FILE}: {e}")
      return records
  ```
  This is required so `test_tier2_feature6_query_missing_csv_file_graceful` passes when monkeypatched to a non-existent file.
- `write_all_customer_accounts()` must ensure parent directories exist before writing:
  ```python
  def write_all_customer_accounts(records: List[Dict[str, str]]) -> bool:
      """Writes all rows to data/customer_accounts.csv."""
      DATA_DIR.mkdir(parents=True, exist_ok=True)
      try:
          with open(CUSTOMER_ACCOUNTS_FILE, "w", newline="", encoding="utf-8") as f:
              writer = csv.DictWriter(f, fieldnames=CUSTOMER_ACCOUNT_FIELDNAMES)
              writer.writeheader()
              writer.writerows(records)
          return True
      except Exception as e:
          logger.error(f"Error writing to {CUSTOMER_ACCOUNTS_FILE}: {e}")
          return False
  ```

### 3.5 Backwards Compatibility Alias Remapping
In `app/database.py:1270-1275`:
```python
# CURRENT (BUGGY FOR CUSTOMER ACCOUNTS):
# read_all_customers = read_all_transactions
# write_all_customers = write_all_transactions

# REQUIRED FIX:
read_all_customers = read_all_customer_accounts
write_all_customers = write_all_customer_accounts
```
**Rationale**:
In `tests/test_e2e_troy.py` (lines 766, 789, 809, 946), tests call `accs = database.read_all_customers()` and assert `a.get("account_balance") == "$0.00 (Settled)"` and `a.get("account_status") == "Active - In Good Standing"`. Neither field exists in `transactions.csv`. If `read_all_customers` points to `read_all_transactions`, all these assertions evaluate against transaction objects and fail. Meanwhile, FastAPI's `/api/database/customers` route in `app/main.py:253` directly calls `read_all_transactions()`, so remapping `read_all_customers` does not break the API route or `test_api_endpoints.py`.

---

## 4. Test Isolation Fixture Verification (`tests/conftest.py`)

### 4.1 Fixture Mechanics
`tests/conftest.py:14-43` defines:
```python
@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    import app.database

    data_dir = Path(root_dir) / "data"
    src_tx = data_dir / "transactions.csv"
    src_acc = data_dir / "customer_accounts.csv"

    tmp_tx = tmp_path / "transactions.csv"
    tmp_acc = tmp_path / "customer_accounts.csv"

    if src_tx.exists():
        shutil.copyfile(src_tx, tmp_tx)
    if src_acc.exists():
        shutil.copyfile(src_acc, tmp_acc)

    monkeypatch.setattr(app.database, "DATABASE_FILE", tmp_tx)
    monkeypatch.setattr(app.database, "CUSTOMER_ACCOUNTS_FILE", tmp_acc)
    monkeypatch.setattr(app.database, "DATA_DIR", tmp_path)

    yield {
        "transactions_file": tmp_tx,
        "accounts_file": tmp_acc,
        "dir": tmp_path,
    }
```

### 4.2 Robustness Enhancement for Missing Source Files
If tests run in an environment where `data/customer_accounts.csv` or `data/transactions.csv` is not pre-populated, `shutil.copyfile` will be bypassed, causing `tmp_acc.exists()` to be False and failing `test_tier1_feature8_copies_customer_accounts_to_tmp`.
To ensure 100% resilience across all environments:
Before copying, ensure source database files exist by calling `app.database.ensure_database_file()`:
```python
    if not src_tx.exists() or not src_acc.exists():
        app.database.ensure_database_file()
```
This guarantees that source CSVs exist before `shutil.copyfile()` executes.

---

## 5. Worker Verification Command Matrix

The Worker must execute the following verification commands in order:

### Command 1: Milestone 2 Targeted Test Verification (40 Tests)
```powershell
pytest tests/test_e2e_troy.py -k "feature5 or feature6 or feature7 or feature8" -v
```
**Acceptance Criterion**: 40 passed, 0 failed.

### Command 2: Tier 3 Cross-Feature Integration Verification
```powershell
pytest tests/test_e2e_troy.py -k "tier3 and (duplicate or inquiry or restart or concurrent)" -v
```
**Acceptance Criterion**: All targeted cross-feature integration tests pass cleanly without regressions.

### Command 3: Full Existing Test Suite Regression Pass (91 Tests)
```powershell
pytest --ignore=tests/test_e2e_troy.py -v
```
**Acceptance Criterion**: 91 passed, 0 failed. Verifies that API endpoints, auth vault, triage logic, and category resolution mechanisms remain 100% compatible.

### Command 4: CSV Seeding Resilience Smoke Test
```powershell
python -c "import app.database as db; from pathlib import Path; import tempfile, shutil; tmp = Path(tempfile.mkdtemp()); db.DATABASE_FILE = tmp / 'tx.csv'; db.CUSTOMER_ACCOUNTS_FILE = tmp / 'acc.csv'; db.DATA_DIR = tmp; db.ensure_database_file(); assert (tmp/'tx.csv').exists() and len(open(tmp/'tx.csv').readlines()) == 9; assert (tmp/'acc.csv').exists() and len(open(tmp/'acc.csv').readlines()) == 7; shutil.rmtree(tmp); print('Seeding resilience verified successfully!')"
```
**Acceptance Criterion**: Script exits code 0 and prints `Seeding resilience verified successfully!`.

---

## 6. Step-by-Step Implementation Guide for the Worker

### Step 1: Add `DEFAULT_CUSTOMER_ACCOUNTS` in `app/database.py`
Place `DEFAULT_CUSTOMER_ACCOUNTS` immediately following `CUSTOMER_ACCOUNT_FIELDNAMES`. Populate all 6 accounts (Alex Morgan, Sarah Jenkins, David Chen, Elena Rostova, Michael Scott, Emily Watson) with all 20 columns as specified in Section 3.2.

### Step 2: Update `ensure_database_file` in `app/database.py`
Update `ensure_database_file` to ensure `DATA_DIR` exists, check `DATABASE_FILE.exists()` (seeding `DEFAULT_TRANSACTIONS`), and check `CUSTOMER_ACCOUNTS_FILE.exists()` (seeding `DEFAULT_CUSTOMER_ACCOUNTS`). Add `ensure_customer_accounts_file()` helper.

### Step 3: Update `read_all_customer_accounts` & `write_all_customer_accounts`
Ensure `write_all_customer_accounts` calls `DATA_DIR.mkdir(parents=True, exist_ok=True)`. Ensure `read_all_customer_accounts` returns `[]` when `not CUSTOMER_ACCOUNTS_FILE.exists()`.

### Step 4: Fix Fallback Bug & Partial ID Matching in `app/database.py` (Feature 5)
1. In `find_customer_account_by_query`: line 306 change `if target_id == acc_id or target_id in acc_id:` to `if target_id == acc_id:` (exact match).
2. In `find_transactions_for_query`: replace line 398 Alex Morgan fallback with `return []`.

### Step 5: Implement Generic Inspection Tools (Feature 6)
Implement:
- `query_customer_account(customer_id: str = None, email: str = None, order_id: str = None) -> Optional[Dict[str, str]]`
- `query_transactions(customer_id: str = None, order_id: str = None, status: str = None, billing_cycle: str = None) -> List[Dict[str, str]]`
- `inspect_account_and_transactions(customer_id: str = None, query: str = None) -> Dict[str, Any]`

### Step 6: Implement Generic Remediation Tools (Feature 7)
Implement:
- `update_transaction(transaction_id: str, updates: Dict[str, Any]) -> bool`
- `update_customer_account(customer_id: str, updates: Dict[str, Any]) -> bool`
- `remediate_duplicate_charge(order_id: str, duplicate_txn_id: str, refund_amount: str = "49.00", processed_by: str = "Troy (Autonomous Resolution Agent)", notes: str = "") -> Dict[str, Any]` with idempotency check, timestamp formatting, and atomic dual-file sync.

### Step 7: Fix Backwards Compatibility Aliases
Change line 1271:
`read_all_customers = read_all_customer_accounts`
`write_all_customers = write_all_customer_accounts`

### Step 8: Verify Robustness in `tests/conftest.py`
Ensure `app.database.ensure_database_file()` is called if source files in `data/` do not exist before copying.

### Step 9: Run Verification Command Matrix
Execute Commands 1 through 4 from Section 5. Confirm 40/40 tests pass for Features 5-8, Tier 3 tests pass, and all 91 existing tests pass.
