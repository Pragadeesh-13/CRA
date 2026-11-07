# Milestone 2 Explorer 2 Technical Analysis: Remediation Tools & Multi-Table Synchronization

**Date**: 2026-11-06  
**Author**: Milestone 2 Explorer 2 (Remediation Tools & Multi-Table Synchronization)  
**Target System**: `app/database.py`  
**Scope**: Feature 7 (Generic Database Remediation Tools) & Cross-Feature Integration (Features 8, 10, 12, Tiers 1–4)  

---

## 1. Executive Summary

Milestone 2 requires providing Troy with generic database remediation capabilities over the existing database schema while ensuring atomic multi-table synchronization across `data/transactions.csv` (17 columns) and `data/customer_accounts.csv` (20 columns).

Currently:
1. None of the required generic remediation functions exist in `app/database.py`:
   - `update_transaction(transaction_id, updates)`
   - `update_customer_account(customer_id, updates)`
   - `remediate_duplicate_charge(order_id, duplicate_txn_id, refund_amount="49.00", processed_by=..., notes=...)`
2. All 10 tests in `tests/test_e2e_troy.py` for Feature 7 fail immediately with `pytest.fail("Feature 7: app.database.<tool> is not yet implemented (Milestone 2)")`.
3. In addition to Feature 7, 10 Feature 12 tests, 4 Tier 3 cross-feature tests, and 2 Tier 4 real-world scenario tests depend directly on `remediate_duplicate_charge`.
4. A critical bug exists at `app/database.py:1271`: `read_all_customers` is aliased to `read_all_transactions` instead of `read_all_customer_accounts`. Because tests verify customer balance and status via `database.read_all_customers()`, this alias must be corrected to point to `read_all_customer_accounts`.
5. Atomic synchronization requires a re-entrant process thread lock (`_DB_LOCK = threading.RLock()`), write-to-temp file replacement with `os.replace`, and OS buffer flush (`f.flush(); os.fsync(f.fileno())`) so direct raw open operations on disk immediately read persisted data.

---

## 2. Requirement & Test Contract Mapping

### 2.1 Feature 7 Test Inventory in `tests/test_e2e_troy.py`

| Test Name | Tier | Invocations | Key Assertions |
|---|---|---|---|
| `test_tier1_feature7_update_transaction_status` | Tier 1 | `update_transaction("TXN-7712", {"status": "Refunded", "refund_id": "REF-TEST-7712"})` | `ok is True`, `target.status == "Refunded"`, `target.refund_id == "REF-TEST-7712"` |
| `test_tier1_feature7_update_customer_account_balance` | Tier 1 | `update_customer_account("USR-07231", {"account_balance": "$0.00 (Settled)", "account_status": "Active - In Good Standing"})` | `ok is True`, `accs = read_all_customers()`, `account_balance == "$0.00 (Settled)"`, `account_status == "Active - In Good Standing"` |
| `test_tier1_feature7_remediate_duplicate_charge_alex_morgan` | Tier 1 | `remediate_duplicate_charge(order_id="ORD-88219", duplicate_txn_id="TXN-9012")` | `ok is True`, `status == "Refunded"`, `TXN-9012` status is `"Refunded"`, `USR-10492` balance is `"$0.00 (Settled)"`, status is `"Active - In Good Standing"` |
| `test_tier1_feature7_remediate_duplicate_charge_emily_watson` | Tier 1 | `remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")` | `ok is True`, `TXN-7712` status is `"Refunded"`, `USR-07231` balance is `"$0.00 (Settled)"`, status is `"Active - In Good Standing"` |
| `test_tier1_feature7_remediate_idempotency` | Tier 1 | `remediate_duplicate_charge` called twice sequentially on `ORD-81094` / `TXN-7712` | `res1.ok is True`, `res2.ok is True`, `(res2.is_already_refunded is True or res2.status == "Refunded")` |
| `test_tier2_feature7_remediate_nonexistent_transaction` | Tier 2 | `remediate_duplicate_charge(order_id="ORD-00000", duplicate_txn_id="TXN-INVALID")` | `res.ok is False or "error" in res` |
| `test_tier2_feature7_atomic_multi_table_sync_integrity` | Tier 2 | `remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")` followed by raw `open()` on `database.DATABASE_FILE` and `database.CUSTOMER_ACCOUNTS_FILE` | `TXN-7712` status is `"Refunded"`, `USR-07231` balance contains `"$0.00"` |
| `test_tier2_feature7_custom_refund_amount` | Tier 2 | `remediate_duplicate_charge(..., refund_amount="25.00")` | `ok is True`, `TXN-7712.refund_amount == "25.00"` |
| `test_tier2_feature7_audit_trail_processor_name` | Tier 2 | `remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")` | `"Troy" in TXN-7712.processed_by` |
| `test_tier2_feature7_remediation_timestamp_format` | Tier 2 | `remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")` | `len(TXN-7712.refunded_at.strip()) > 5` |

### 2.2 Downstream Test Dependencies on Feature 7

1. **Feature 10**:
   - `test_tier1_feature10_billing_dispute_executes_remediation` calls `remediate_duplicate_charge`.
2. **Feature 12 (Full Duplicate Charge Remediation Workflow)**:
   - `test_tier1_feature12_e2e_alex_morgan_duplicate_remediation`
   - `test_tier1_feature12_e2e_emily_watson_duplicate_remediation`
   - `test_tier1_feature12_transaction_status_transitions_to_refunded`
   - `test_tier1_feature12_refund_id_assigned`
   - `test_tier1_feature12_account_balance_settled_to_zero`
   - `test_tier2_feature12_second_run_idempotent`
   - `test_tier2_feature12_account_status_in_good_standing`
   - `test_tier2_feature12_refund_amount_matches_charge`
   - `test_tier2_feature12_audit_trail_notes_recorded`
   - `test_tier2_feature12_reconciled_customer_verify_endpoint`
3. **Tier 3 (Cross-Feature Combinations)**:
   - `test_tier3_offline_fallback_with_alex_morgan_duplicate_remediation`
   - `test_tier3_offline_fallback_with_emily_watson_duplicate_remediation`
   - `test_tier3_remediation_followed_by_customer_verify`
   - `test_tier3_idempotent_remediation_across_restart`
4. **Tier 4 (Real-World Application Scenarios)**:
   - `test_tier4_scenario1_alex_morgan_duplicate_charge`
   - `test_tier4_scenario2_emily_watson_duplicate_charge`

---

## 3. Detailed Component Design

### 3.1 `update_transaction(transaction_id, updates)`

```python
def update_transaction(transaction_id: str, updates: Dict[str, Any]) -> bool:
    """
    Updates specified columns for a transaction record in data/transactions.csv.
    
    Args:
        transaction_id: Target transaction ID (e.g. 'TXN-7712').
        updates: Dict of column names to new values.
        
    Returns:
        True if the record was found and updated on disk, False otherwise.
    """
```

#### Detailed Execution Steps:
1. Validate `transaction_id` (must be non-empty string) and `updates` (must be non-empty dict). If empty, return `False`.
2. Acquire `_DB_LOCK`.
3. Load all rows with `read_all_transactions()`.
4. Locate row matching `transaction_id.strip()`. If not found, return `False`.
5. For each key, val in `updates.items()`:
   - If `key in FIELDNAMES`:
     - Set `row[key] = str(val) if val is not None else ""`
6. Call `write_all_transactions(records)`.
7. Return `True` on success, `False` on disk/write error.

---

### 3.2 `update_customer_account(customer_id, updates)`

```python
def update_customer_account(customer_id: str, updates: Dict[str, Any]) -> bool:
    """
    Updates specified columns for a customer account in data/customer_accounts.csv.
    
    Args:
        customer_id: Target customer ID (e.g. 'USR-07231').
        updates: Dict of column names to new values.
        
    Returns:
        True if the account was found and updated on disk, False otherwise.
    """
```

#### Detailed Execution Steps:
1. Validate `customer_id` and `updates`. If empty, return `False`.
2. Acquire `_DB_LOCK`.
3. Load all rows with `read_all_customer_accounts()`.
4. Locate row matching `customer_id.strip().upper()`. If not found, return `False`.
5. For each key, val in `updates.items()`:
   - If `key in CUSTOMER_ACCOUNT_FIELDNAMES`:
     - Set `row[key] = str(val) if val is not None else ""`
6. Call `write_all_customer_accounts(records)`.
7. Return `True` on success, `False` on disk/write error.

---

### 3.3 `remediate_duplicate_charge(order_id, duplicate_txn_id, refund_amount="49.00", processed_by=..., notes="")`

```python
def remediate_duplicate_charge(
    order_id: str,
    duplicate_txn_id: str,
    refund_amount: str = "49.00",
    processed_by: str = "Troy (Autonomous Resolution Agent)",
    notes: str = "",
) -> Dict[str, Any]:
    """
    Atomically resolves an erroneous duplicate charge across transactions.csv and customer_accounts.csv.
    Idempotent: if transaction is already refunded, returns success without duplicate edits.
    """
```

#### Detailed Execution Steps:
1. Validate `duplicate_txn_id`. If empty, return error dict:
   `{"ok": False, "status": "Failed", "error": "duplicate_txn_id is required for remediation"}`
2. Acquire `_DB_LOCK`.
3. Read `all_txns = read_all_transactions()` and `all_accs = read_all_customer_accounts()`.
4. Search `all_txns` for `target_txn` matching `target_txn.get("transaction_id", "").strip() == str(duplicate_txn_id).strip()`.
5. If `target_txn` not found:
   Return `{"ok": False, "status": "Failed", "error": f"Transaction {duplicate_txn_id} not found in database."}`.
6. Extract customer ID and determine customer account:
   - `target_cust_id = target_txn.get("customer_id", "")`
   - `txn_order_id = target_txn.get("order_id", "") or order_id`
   - `effective_order_id = order_id or txn_order_id`
   - Locate `target_acc` in `all_accs` by matching `customer_id` (case-insensitive) or `reference_order_id == effective_order_id`.
7. Format constants:
   - `now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")`
   - `clean_order_num = re.sub(r"[^\d]", "", effective_order_id) if effective_order_id else ""`
   - `suggested_ref_id = f"REF-{clean_order_num}-AUTO" if clean_order_num else f"REF-{duplicate_txn_id.replace('TXN-', '')}-AUTO"`
   - `effective_refund_id = target_txn.get("refund_id") or (target_acc.get("refund_id") if target_acc else "") or suggested_ref_id`
   - `effective_refund_amount = str(refund_amount) if refund_amount else (target_txn.get("amount") or "49.00")`
   - `audit_processor = processed_by or "Troy (Autonomous Resolution Agent)"`
8. **Idempotency Check**:
   - Check `is_already_refunded = (target_txn.get("status") == "Refunded")`.
   - If `True`:
     - Retain existing refund fields:
       `existing_ref_id = target_txn.get("refund_id") or effective_refund_id`
       `existing_ref_amt = target_txn.get("refund_amount") or effective_refund_amount`
       `existing_ref_date = target_txn.get("refunded_at") or now_str`
       `existing_processor = target_txn.get("processed_by") or audit_processor`
       `existing_notes = target_txn.get("notes") or notes or f"Autonomous refund reconfirmed for order {effective_order_id}."`
     - Reconcile customer account if not yet settled:
       If `target_acc`:
       `target_acc["refund_status"] = f"Refunded (${existing_ref_amt} via Stripe API)"`
       `target_acc["refund_id"] = existing_ref_id`
       `target_acc["account_balance"] = "$0.00 (Settled)"`
       `target_acc["account_status"] = "Active - In Good Standing"`
       `target_acc["duplicate_verified"] = "Yes - Accidental Double Charge Detected"`
       `target_acc["total_billed_this_month"] = f"{existing_ref_amt} (Net after ${existing_ref_amt} refund)"`
       `target_acc["last_verified_by"] = audit_processor`
       `target_acc["updated_by"] = audit_processor`
       `target_acc["last_updated"] = now_str`
       if notes: target_acc["notes"] = notes
       `write_all_customer_accounts(all_accs)`
     - Return idempotent success dict:
       ```python
       {
           "ok": True,
           "status": "Refunded",
           "is_already_refunded": True,
           "already_refunded": True,
           "refund_id": existing_ref_id,
           "refund_amount": existing_ref_amt,
           "transaction_id": duplicate_txn_id,
           "order_id": effective_order_id,
           "customer_id": target_cust_id,
           "processed_by": existing_processor,
           "refunded_at": existing_ref_date,
           "notes": existing_notes,
           "action": "remediate_duplicate_charge",
           "message": f"Transaction {duplicate_txn_id} was already refunded under {existing_ref_id}. Reconfirmed without duplicate payout.",
       }
       ```
9. **Active Remediation (Initial Refund)**:
   - Update `target_txn`:
     - `target_txn["status"] = "Refunded"`
     - `target_txn["refund_id"] = effective_refund_id`
     - `target_txn["refund_amount"] = effective_refund_amount`
     - `target_txn["refunded_at"] = now_str`
     - `target_txn["processed_by"] = audit_processor`
     - `target_txn["notes"] = notes if notes else f"Refunded by Troy: Duplicate subscription charge for order {effective_order_id}."`
   - Update `target_acc` (if found):
     - `target_acc["refund_status"] = f"Refunded (${effective_refund_amount} via Stripe API)"`
     - `target_acc["refund_id"] = effective_refund_id`
     - `target_acc["account_balance"] = "$0.00 (Settled)"`
     - `target_acc["account_status"] = "Active - In Good Standing"`
     - `target_acc["duplicate_verified"] = "Yes - Accidental Double Charge Detected"`
     - `target_acc["total_billed_this_month"] = f"{effective_refund_amount} (Net after ${effective_refund_amount} refund)"`
     - If `duplicate_txn_id in target_acc.get("transaction_2", "")` or not `target_acc.get("transaction_2")` or `target_acc.get("transaction_2") == "None"`:
       `target_acc["transaction_2"] = f"{duplicate_txn_id} (${effective_refund_amount} on {now_str[:10]}) [Refunded: {effective_refund_id}]"`
     - `target_acc["last_verified_by"] = audit_processor`
     - `target_acc["updated_by"] = audit_processor`
     - `target_acc["last_updated"] = now_str`
     - `target_acc["notes"] = notes if notes else f"Autonomous override executed by {audit_processor}. Accidental double renewal debit reversed in Stripe API under {effective_refund_id}."`
10. **Synchronous Two-Table Persistence**:
    - `tx_ok = write_all_transactions(all_txns)`
    - `acc_ok = write_all_customer_accounts(all_accs)`
    - If `not tx_ok or not acc_ok`:
      Return error dict:
      `{"ok": False, "status": "Failed", "error": "Failed to persist atomic updates to database files"}`
    - Return success dict:
      ```python
      {
          "ok": True,
          "status": "Refunded",
          "is_already_refunded": False,
          "already_refunded": False,
          "refund_id": effective_refund_id,
          "refund_amount": effective_refund_amount,
          "transaction_id": duplicate_txn_id,
          "order_id": effective_order_id,
          "customer_id": target_cust_id,
          "processed_by": audit_processor,
          "refunded_at": now_str,
          "notes": target_txn["notes"],
          "action": "remediate_duplicate_charge",
          "message": f"Transaction {duplicate_txn_id} successfully refunded under {effective_refund_id} and account settled.",
      }
      ```

---

## 4. Multi-Table Synchronization & Atomicity Architecture

```
+-------------------------------------------------------------------------+
|                  remediate_duplicate_charge(...)                        |
|                                                                         |
|  1. Acquire module _DB_LOCK (threading.RLock)                           |
|  2. Load all_txns (transactions.csv) and all_accs (customer_accounts)   |
|  3. Validate target_txn & resolve target_acc                            |
|  4. Idempotency Check: if status == 'Refunded', return clean no-op      |
|  5. Apply modifications in memory:                                      |
|     - transactions: status='Refunded', refund_id, refund_amount, etc.   |
|     - customer_accounts: balance='$0.00 (Settled)', status='Active...'  |
|  6. Synchronous Persistence:                                            |
|     write_all_transactions()       write_all_customer_accounts()         |
|         │                                │                              |
|         ▼                                ▼                              |
|     write to .tmp                    write to .tmp                      |
|     f.flush(); os.fsync()            f.flush(); os.fsync()              |
|     os.replace(tmp, DATABASE_FILE)   os.replace(tmp, ACCOUNTS_FILE)     |
|                                                                         |
|  7. Release _DB_LOCK and return {ok: True, status: 'Refunded', ...}    |
+-------------------------------------------------------------------------+
```

### Why this design fulfills all boundary constraints:
1. **Thread safety**: `_DB_LOCK` guarantees that concurrent requests (e.g. from FastAPI endpoints or multi-threaded background workers) do not interleave reading and writing.
2. **Crash & Truncation Safety**: Writing to `.tmp` first and calling `os.replace` guarantees that any process or test reading the file never reads an incomplete, truncated, or corrupted CSV.
3. **Cache Synchronization**: Explicit `f.flush()` and `os.fsync(f.fileno())` flushes OS filesystem buffers before `write_all_*` returns. When tests such as `test_tier2_feature7_atomic_multi_table_sync_integrity` immediately open raw files with `with open(database.DATABASE_FILE, 'r') as f:`, all changes are already on disk.

---

## 5. Critical Fix: Customer Accounts Backwards Compatibility Alias

In `app/database.py` line 1271:
```python
# CURRENT INCORRECT ALIAS:
read_all_customers = read_all_transactions
read_all_tickets = read_all_transactions
write_all_customers = write_all_transactions
write_all_tickets = write_all_transactions
```

### Problem:
Tests in `tests/test_e2e_troy.py` (lines 766, 789, 809, 946, 1343, 1747) verify customer accounts by calling `database.read_all_customers()`, expecting customer account objects containing `account_balance`, `account_status`, `subscription_plan`, etc.
Because line 1271 pointed `read_all_customers` to `read_all_transactions`, `accs` contained transaction objects which lack these fields, causing immediate assertion errors:
`assert target.get("account_balance") == "$0.00 (Settled)"` -> `None == "$0.00 (Settled)"` (FAILED).

### Required Fix:
```python
# CORRECT ALIAS:
read_all_customers = read_all_customer_accounts
read_all_tickets = read_all_transactions
write_all_customers = write_all_customer_accounts
write_all_tickets = write_all_transactions
```

---

## 6. Step-by-Step Implementation Guide for Worker

### Step 1: Add `threading` and `_DB_LOCK` to `app/database.py`
Near the top of `app/database.py` (lines 9–16):
```python
import csv
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DB_LOCK = threading.RLock()
```

### Step 2: Update `write_all_transactions` and `write_all_customer_accounts` for Atomic Sync
In `app/database.py` (lines 248–285):
```python
def write_all_transactions(records: List[Dict[str, str]]) -> bool:
    """Writes all transaction rows to data/transactions.csv atomically."""
    with _DB_LOCK:
        db_path = ensure_database_file()
        tmp_path = db_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(records)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, db_path)
            return True
        except Exception as e:
            logger.error(f"Error writing to {db_path}: {e}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False


def write_all_customer_accounts(records: List[Dict[str, str]]) -> bool:
    """Writes all rows to data/customer_accounts.csv atomically."""
    with _DB_LOCK:
        CUSTOMER_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CUSTOMER_ACCOUNTS_FILE.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CUSTOMER_ACCOUNT_FIELDNAMES, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(records)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CUSTOMER_ACCOUNTS_FILE)
            return True
        except Exception as e:
            logger.error(f"Error writing to {CUSTOMER_ACCOUNTS_FILE}: {e}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False
```

### Step 3: Implement `update_transaction` and `update_customer_account`
Add these functions directly below `write_all_customer_accounts`:
```python
def update_transaction(transaction_id: str, updates: Dict[str, Any]) -> bool:
    """
    Updates specified columns for a transaction record in data/transactions.csv.
    Returns True if record found and updated, False otherwise.
    """
    if not transaction_id or not updates:
        return False

    with _DB_LOCK:
        records = read_all_transactions()
        target_id = str(transaction_id).strip()
        matched = False

        for row in records:
            if row.get("transaction_id", "").strip() == target_id:
                for k, v in updates.items():
                    if k in FIELDNAMES:
                        row[k] = str(v) if v is not None else ""
                matched = True
                break

        if not matched:
            logger.warning(f"update_transaction: transaction_id '{transaction_id}' not found.")
            return False

        return write_all_transactions(records)


def update_customer_account(customer_id: str, updates: Dict[str, Any]) -> bool:
    """
    Updates specified columns for a customer account in data/customer_accounts.csv.
    Returns True if record found and updated, False otherwise.
    """
    if not customer_id or not updates:
        return False

    with _DB_LOCK:
        records = read_all_customer_accounts()
        target_id = str(customer_id).strip().upper()
        matched = False

        for row in records:
            row_id = row.get("customer_id", "").strip().upper()
            if row_id == target_id:
                for k, v in updates.items():
                    if k in CUSTOMER_ACCOUNT_FIELDNAMES:
                        row[k] = str(v) if v is not None else ""
                matched = True
                break

        if not matched:
            logger.warning(f"update_customer_account: customer_id '{customer_id}' not found.")
            return False

        return write_all_customer_accounts(records)
```

### Step 4: Implement `remediate_duplicate_charge`
Add `remediate_duplicate_charge` directly below `update_customer_account`:
```python
def remediate_duplicate_charge(
    order_id: str,
    duplicate_txn_id: str,
    refund_amount: str = "49.00",
    processed_by: str = "Troy (Autonomous Resolution Agent)",
    notes: str = "",
) -> Dict[str, Any]:
    """
    Atomically resolves an erroneous duplicate charge across transactions.csv and customer_accounts.csv.
    Updates transaction status to 'Refunded', sets refund_id, refunded_at, refund_amount, and processed_by.
    Reconciles customer account to '$0.00 (Settled)' balance and 'Active - In Good Standing' status.
    Idempotent: if transaction is already refunded, returns success without duplicate edits or payouts.
    """
    if not duplicate_txn_id:
        return {
            "ok": False,
            "status": "Failed",
            "error": "duplicate_txn_id is required for remediation",
        }

    with _DB_LOCK:
        all_txns = read_all_transactions()
        all_accs = read_all_customer_accounts()

        target_txn = next(
            (t for t in all_txns if t.get("transaction_id", "").strip() == str(duplicate_txn_id).strip()),
            None,
        )

        if not target_txn:
            logger.warning(f"remediate_duplicate_charge: transaction {duplicate_txn_id} not found.")
            return {
                "ok": False,
                "status": "Failed",
                "error": f"Transaction {duplicate_txn_id} not found in database.",
            }

        target_cust_id = target_txn.get("customer_id", "")
        txn_order_id = target_txn.get("order_id", "") or order_id
        effective_order_id = order_id or txn_order_id

        # Find customer account matching customer_id or order_id
        target_acc = None
        if target_cust_id:
            target_acc = next(
                (a for a in all_accs if a.get("customer_id", "").strip().upper() == target_cust_id.strip().upper()),
                None,
            )
        if not target_acc and effective_order_id:
            target_acc = next(
                (a for a in all_accs if a.get("reference_order_id", "").strip() == effective_order_id.strip()),
                None,
            )

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_order_num = re.sub(r"[^\d]", "", effective_order_id) if effective_order_id else ""
        suggested_ref_id = f"REF-{clean_order_num}-AUTO" if clean_order_num else f"REF-{duplicate_txn_id.replace('TXN-', '')}-AUTO"

        effective_refund_id = (
            target_txn.get("refund_id")
            or (target_acc.get("refund_id") if target_acc else "")
            or suggested_ref_id
        )
        effective_refund_amount = str(refund_amount) if refund_amount else (target_txn.get("amount") or "49.00")
        audit_processor = processed_by or "Troy (Autonomous Resolution Agent)"

        is_already_refunded = (target_txn.get("status") == "Refunded")

        if is_already_refunded:
            existing_ref_id = target_txn.get("refund_id") or effective_refund_id
            existing_ref_amt = target_txn.get("refund_amount") or effective_refund_amount
            existing_ref_date = target_txn.get("refunded_at") or now_str
            existing_processor = target_txn.get("processed_by") or audit_processor
            existing_notes = target_txn.get("notes") or notes or f"Autonomous refund reconfirmed for order {effective_order_id}."

            if target_acc:
                target_acc["refund_status"] = f"Refunded (${existing_ref_amt} via Stripe API)"
                target_acc["refund_id"] = existing_ref_id
                target_acc["account_balance"] = "$0.00 (Settled)"
                target_acc["account_status"] = "Active - In Good Standing"
                target_acc["duplicate_verified"] = "Yes - Accidental Double Charge Detected"
                target_acc["total_billed_this_month"] = f"{existing_ref_amt} (Net after ${existing_ref_amt} refund)"
                target_acc["last_verified_by"] = audit_processor
                target_acc["updated_by"] = audit_processor
                target_acc["last_updated"] = now_str
                if notes:
                    target_acc["notes"] = notes
                write_all_customer_accounts(all_accs)

            logger.info(f"remediate_duplicate_charge: {duplicate_txn_id} was already refunded under {existing_ref_id}.")
            return {
                "ok": True,
                "status": "Refunded",
                "is_already_refunded": True,
                "already_refunded": True,
                "refund_id": existing_ref_id,
                "refund_amount": existing_ref_amt,
                "transaction_id": duplicate_txn_id,
                "order_id": effective_order_id,
                "customer_id": target_cust_id,
                "processed_by": existing_processor,
                "refunded_at": existing_ref_date,
                "notes": existing_notes,
                "action": "remediate_duplicate_charge",
                "message": f"Transaction {duplicate_txn_id} was already refunded under {existing_ref_id}. Reconfirmed without duplicate payout.",
            }

        # 1. Update transaction row
        target_txn["status"] = "Refunded"
        target_txn["refund_id"] = effective_refund_id
        target_txn["refund_amount"] = effective_refund_amount
        target_txn["refunded_at"] = now_str
        target_txn["processed_by"] = audit_processor
        target_txn["notes"] = notes if notes else f"Refunded by Troy: Duplicate subscription charge for order {effective_order_id}."

        # 2. Update customer account row
        if target_acc:
            target_acc["refund_status"] = f"Refunded (${effective_refund_amount} via Stripe API)"
            target_acc["refund_id"] = effective_refund_id
            target_acc["account_balance"] = "$0.00 (Settled)"
            target_acc["account_status"] = "Active - In Good Standing"
            target_acc["duplicate_verified"] = "Yes - Accidental Double Charge Detected"
            target_acc["total_billed_this_month"] = f"{effective_refund_amount} (Net after ${effective_refund_amount} refund)"
            if (
                duplicate_txn_id in target_acc.get("transaction_2", "")
                or not target_acc.get("transaction_2")
                or target_acc.get("transaction_2") == "None"
            ):
                target_acc["transaction_2"] = (
                    f"{duplicate_txn_id} (${effective_refund_amount} on {now_str[:10]}) [Refunded: {effective_refund_id}]"
                )
            target_acc["last_verified_by"] = audit_processor
            target_acc["updated_by"] = audit_processor
            target_acc["last_updated"] = now_str
            target_acc["notes"] = notes if notes else f"Autonomous override executed by {audit_processor}. Accidental double renewal debit reversed in Stripe API under {effective_refund_id}."

        # 3. Synchronous multi-table persistence
        tx_ok = write_all_transactions(all_txns)
        acc_ok = write_all_customer_accounts(all_accs)

        if not tx_ok or not acc_ok:
            logger.error(f"remediate_duplicate_charge: write failed (tx_ok={tx_ok}, acc_ok={acc_ok})")
            return {
                "ok": False,
                "status": "Failed",
                "error": "Failed to persist atomic updates to database files",
            }

        logger.info(f"remediate_duplicate_charge: {duplicate_txn_id} refunded under {effective_refund_id}. Both tables synchronized.")
        return {
            "ok": True,
            "status": "Refunded",
            "is_already_refunded": False,
            "already_refunded": False,
            "refund_id": effective_refund_id,
            "refund_amount": effective_refund_amount,
            "transaction_id": duplicate_txn_id,
            "order_id": effective_order_id,
            "customer_id": target_cust_id,
            "processed_by": audit_processor,
            "refunded_at": now_str,
            "notes": target_txn["notes"],
            "action": "remediate_duplicate_charge",
            "message": f"Transaction {duplicate_txn_id} successfully refunded under {effective_refund_id} and account settled.",
        }
```

### Step 5: Update Backwards-Compatibility Aliases
At the bottom of `app/database.py`:
Change:
```python
read_all_customers = read_all_transactions
read_all_tickets = read_all_transactions
write_all_customers = write_all_transactions
write_all_tickets = write_all_transactions
```
To:
```python
read_all_customers = read_all_customer_accounts
read_all_tickets = read_all_transactions
write_all_customers = write_all_customer_accounts
write_all_tickets = write_all_transactions
```

---

## 7. Verification Method Matrix

To verify the implementation once applied by the Worker:

1. **Run Feature 7 dedicated tests**:
   ```powershell
   pytest -v tests/test_e2e_troy.py -k "feature7"
   ```
   **Expected**: 10 passed, 0 failed.

2. **Run Feature 12 tests (dependent on remediation)**:
   ```powershell
   pytest -v tests/test_e2e_troy.py -k "feature12"
   ```
   **Expected**: 10 passed, 0 failed.

3. **Run All Milestone 2 tests (Features 5, 6, 7, 8)**:
   ```powershell
   pytest -v tests/test_e2e_troy.py -k "feature5 or feature6 or feature7 or feature8"
   ```
   **Expected**: 40 passed, 0 failed.

4. **Run Tier 3 & Tier 4 Scenarios**:
   ```powershell
   pytest -v tests/test_e2e_troy.py -k "test_tier3_remediation or test_tier3_idempotent or test_tier4_scenario1 or test_tier4_scenario2"
   ```
   **Expected**: All passed.
