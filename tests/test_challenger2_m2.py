"""
Adversarial Challenge Test Suite for Milestone 2 (Generic Database Tools & Ledger Remediation).
Author: Milestone 2 Challenger 2 (Empirical Challenger)
Focus:
- Idempotency verification (repeated remediation, no row duplication, no balance corruption)
- Dual-table consistency (transactions.csv and customer_accounts.csv synchronized)
- High-concurrency stress testing and thread safety
- Boundary conditions and edge cases
- Public directory frozen preservation
"""

import csv
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest

import app.database as db


def count_csv_rows(file_path: Path) -> int:
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        return sum(1 for _ in reader)


def read_csv_records(file_path: Path):
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


# =========================================================================
# 1. Idempotency Stress Tests
# =========================================================================

def test_idempotency_sequential_repeats(isolated_database):
    """
    Call remediate_duplicate_charge 10 times consecutively on the same transaction.
    Verify:
    - 1st call returns is_already_refunded=False
    - 2nd through 10th calls return is_already_refunded=True
    - Total rows in transactions.csv and customer_accounts.csv remain unchanged (NO duplicate rows)
    - Refund ID is invariant across all calls
    - Balance remains '$0.00 (Settled)' across all calls
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    init_tx_count = count_csv_rows(tx_file)
    init_acc_count = count_csv_rows(acc_file)

    # First call: Initial remediation of Emily Watson's duplicate charge
    res1 = db.remediate_duplicate_charge(
        order_id="ORD-81094",
        duplicate_txn_id="TXN-7712",
        refund_amount="49.00",
        processed_by="Troy Challenger",
        notes="First remediation test",
    )

    assert res1["ok"] is True
    assert res1["status"] == "Refunded"
    assert res1["is_already_refunded"] is False
    assigned_ref_id = res1["refund_id"]
    assert assigned_ref_id.startswith("REF-")

    # Repeat 9 more times
    for i in range(2, 11):
        res_repeat = db.remediate_duplicate_charge(
            order_id="ORD-81094",
            duplicate_txn_id="TXN-7712",
            refund_amount="49.00",
            processed_by=f"Troy Challenger Run {i}",
            notes=f"Subsequent remediation run {i}",
        )
        assert res_repeat["ok"] is True
        assert res_repeat["status"] == "Refunded"
        assert res_repeat["is_already_refunded"] is True
        assert res_repeat["refund_id"] == assigned_ref_id
        assert res_repeat["refund_amount"] == "49.00"

    # Verify no row explosion or row duplication in CSVs
    final_tx_count = count_csv_rows(tx_file)
    final_acc_count = count_csv_rows(acc_file)
    assert final_tx_count == init_tx_count, f"Row count changed: {init_tx_count} -> {final_tx_count}"
    assert final_acc_count == init_acc_count, f"Row count changed: {init_acc_count} -> {final_acc_count}"

    # Verify directly from raw CSV records
    tx_rows = read_csv_records(tx_file)
    target_txs = [t for t in tx_rows if t["transaction_id"] == "TXN-7712"]
    assert len(target_txs) == 1, "Duplicate transaction rows created!"
    assert target_txs[0]["status"] == "Refunded"
    assert target_txs[0]["refund_id"] == assigned_ref_id

    acc_rows = read_csv_records(acc_file)
    target_accs = [a for a in acc_rows if a["customer_id"] == "USR-07231"]
    assert len(target_accs) == 1, "Duplicate customer account rows created!"
    assert target_accs[0]["account_balance"] == "$0.00 (Settled)"
    assert target_accs[0]["account_status"] == "Active - In Good Standing"
    assert target_accs[0]["refund_id"] == assigned_ref_id


def test_idempotency_preserves_initial_refund_metadata(isolated_database):
    """
    Ensure that subsequent calls do NOT overwrite the original refund_at timestamp
    or original refund_id with blank/new values.
    """
    # 1. First remediation
    res1 = db.remediate_duplicate_charge(
        order_id="ORD-81094",
        duplicate_txn_id="TXN-7712",
        refund_amount="49.00",
        processed_by="Agent Alpha",
    )
    first_refund_id = res1["refund_id"]
    first_refunded_at = res1["refunded_at"]

    # 2. Second remediation with different processor and empty notes
    res2 = db.remediate_duplicate_charge(
        order_id="ORD-81094",
        duplicate_txn_id="TXN-7712",
        refund_amount="49.00",
        processed_by="",
        notes="",
    )

    assert res2["is_already_refunded"] is True
    assert res2["refund_id"] == first_refund_id
    assert res2["refunded_at"] == first_refunded_at

    # Confirm in raw CSV
    tx_records = read_csv_records(isolated_database["transactions_file"])
    tx_row = next(t for t in tx_records if t["transaction_id"] == "TXN-7712")
    assert tx_row["refund_id"] == first_refund_id
    assert tx_row["refunded_at"] == first_refunded_at


# =========================================================================
# 2. Dual-Table Consistency Tests
# =========================================================================

def test_dual_table_consistency_emily_watson(isolated_database):
    """
    Verify complete field-level synchronization between transactions.csv and customer_accounts.csv
    for Emily Watson (ORD-81094 / TXN-7712).
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    # Verify pre-condition
    pre_tx = next(t for t in read_csv_records(tx_file) if t["transaction_id"] == "TXN-7712")
    pre_acc = next(a for a in read_csv_records(acc_file) if a["customer_id"] == "USR-07231")
    assert pre_tx["status"] == "Settled"
    assert "Overcharged" in pre_acc["account_balance"]

    # Remediate
    res = db.remediate_duplicate_charge(
        order_id="ORD-81094",
        duplicate_txn_id="TXN-7712",
        refund_amount="49.00",
        processed_by="Troy Autonomous Agent",
        notes="Resolved accidental double renewal",
    )
    assert res["ok"] is True

    # Check post-condition on disk directly
    post_tx = next(t for t in read_csv_records(tx_file) if t["transaction_id"] == "TXN-7712")
    companion_tx = next(t for t in read_csv_records(tx_file) if t["transaction_id"] == "TXN-7711")
    post_acc = next(a for a in read_csv_records(acc_file) if a["customer_id"] == "USR-07231")

    # Companion transaction TXN-7711 must remain Settled and untouched
    assert companion_tx["status"] == "Settled"
    assert companion_tx["refund_id"] == ""

    # Duplicate transaction TXN-7712 checks
    assert post_tx["status"] == "Refunded"
    assert post_tx["refund_amount"] == "49.00"
    assert post_tx["refund_id"] != ""
    assert post_tx["processed_by"] == "Troy Autonomous Agent"

    # Customer account checks
    assert post_acc["account_balance"] == "$0.00 (Settled)"
    assert post_acc["account_status"] == "Active - In Good Standing"
    assert post_acc["duplicate_verified"] == "Yes - Accidental Double Charge Detected"
    assert "Refunded ($49.00" in post_acc["refund_status"]

    # DUAL-TABLE CONSISTENCY ASSERTION: Refund IDs must strictly match
    assert post_tx["refund_id"] == post_acc["refund_id"], (
        f"Refund ID mismatch between tables! "
        f"transactions: {post_tx['refund_id']} vs customer_accounts: {post_acc['refund_id']}"
    )


def test_dual_table_consistency_alex_morgan(isolated_database):
    """
    Verify complete dual-table synchronization for Alex Morgan (ORD-88219 / TXN-9012).
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    res = db.remediate_duplicate_charge(
        order_id="ORD-88219",
        duplicate_txn_id="TXN-9012",
        refund_amount="49.00",
        processed_by="Troy Agent",
    )
    assert res["ok"] is True

    post_tx = next(t for t in read_csv_records(tx_file) if t["transaction_id"] == "TXN-9012")
    post_acc = next(a for a in read_csv_records(acc_file) if a["customer_id"] == "USR-10492")

    assert post_tx["status"] == "Refunded"
    assert post_acc["account_balance"] == "$0.00 (Settled)"
    assert post_acc["account_status"] == "Active - In Good Standing"
    assert post_tx["refund_id"] == post_acc["refund_id"]


def test_dual_table_custom_amount_consistency(isolated_database):
    """
    Verify dual-table consistency when a non-default custom refund amount is supplied.
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    res = db.remediate_duplicate_charge(
        order_id="ORD-81094",
        duplicate_txn_id="TXN-7712",
        refund_amount="35.50",
    )
    assert res["ok"] is True
    assert res["refund_amount"] == "35.50"

    post_tx = next(t for t in read_csv_records(tx_file) if t["transaction_id"] == "TXN-7712")
    post_acc = next(a for a in read_csv_records(acc_file) if a["customer_id"] == "USR-07231")

    assert post_tx["refund_amount"] == "35.50"
    assert "35.50" in post_acc["refund_status"]
    assert post_tx["refund_id"] == post_acc["refund_id"]


def test_csv_fieldnames_integrity(isolated_database):
    """
    Ensure all original columns are retained and ordered properly in both CSV files after remediation.
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    db.remediate_duplicate_charge(
        order_id="ORD-81094",
        duplicate_txn_id="TXN-7712",
    )

    with open(tx_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        tx_headers = next(reader)
    assert tx_headers == db.FIELDNAMES
    assert len(tx_headers) == 17

    with open(acc_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        acc_headers = next(reader)
    assert acc_headers == db.CUSTOMER_ACCOUNT_FIELDNAMES
    assert len(acc_headers) == 20

    # Ensure no leftover temporary files (.tmp) remain
    tmp_files = list(isolated_database["dir"].glob("*.tmp"))
    assert len(tmp_files) == 0, f"Dangling .tmp files found: {tmp_files}"


# =========================================================================
# 3. High Concurrency and Race Condition Stress Tests
# =========================================================================

def test_concurrent_remediation_race_stress(isolated_database):
    """
    Execute 20 simultaneous threads attempting to remediate the same duplicate transaction.
    Verify:
    - No unhandled exceptions or deadlocks occur
    - Thread-safety guarantees atomicity via _DB_LOCK
    - Transactions CSV and Customer Accounts CSV remain uncorrupted
    - All threads return ok=True with the exact same refund_id
    - Exactly one call was the initial remediation (or all reflect Refunded)
    - CSV row count does not increase
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    init_tx_count = count_csv_rows(tx_file)
    init_acc_count = count_csv_rows(acc_file)

    def run_remediation(thread_id: int):
        return db.remediate_duplicate_charge(
            order_id="ORD-81094",
            duplicate_txn_id="TXN-7712",
            refund_amount="49.00",
            processed_by=f"Worker-{thread_id}",
            notes=f"Concurrent stress test thread {thread_id}",
        )

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(run_remediation, i) for i in range(20)]
        results = [f.result() for f in futures]

    # Verify every single call succeeded
    for r in results:
        assert r["ok"] is True
        assert r["status"] == "Refunded"

    # All returned refund IDs must be identical
    refund_ids = {r["refund_id"] for r in results}
    assert len(refund_ids) == 1, f"Multiple refund IDs issued under concurrency! {refund_ids}"

    # Row counts must not have increased
    assert count_csv_rows(tx_file) == init_tx_count
    assert count_csv_rows(acc_file) == init_acc_count

    # On disk, exactly one record exists for TXN-7712 with status Refunded
    txs = [t for t in read_csv_records(tx_file) if t["transaction_id"] == "TXN-7712"]
    assert len(txs) == 1
    assert txs[0]["status"] == "Refunded"

    accs = [a for a in read_csv_records(acc_file) if a["customer_id"] == "USR-07231"]
    assert len(accs) == 1
    assert accs[0]["account_balance"] == "$0.00 (Settled)"
    assert accs[0]["account_status"] == "Active - In Good Standing"


# =========================================================================
# 4. Boundary and Edge Case Stress Tests
# =========================================================================

def test_remediate_empty_or_none_txn_id(isolated_database):
    """
    Test empty, None, or whitespace-only transaction IDs fail gracefully.
    """
    res1 = db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="")
    assert res1["ok"] is False
    assert res1["status"] == "Failed"

    res2 = db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id=None)
    assert res2["ok"] is False
    assert res2["status"] == "Failed"


def test_remediate_nonexistent_transaction(isolated_database):
    """
    Test non-existent transaction IDs fail gracefully with descriptive error.
    """
    res = db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-999999")
    assert res["ok"] is False
    assert res["status"] == "Failed"
    assert "not found" in res["error"].lower()


def test_remediate_whitespace_padded_txn_id(isolated_database):
    """
    Verify transaction ID with leading/trailing whitespace is handled properly.
    """
    res = db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="  TXN-7712  ")
    assert res["ok"] is True
    assert res["status"] == "Refunded"

    # Verify on disk
    tx = next(t for t in read_csv_records(isolated_database["transactions_file"]) if t["transaction_id"] == "TXN-7712")
    assert tx["status"] == "Refunded"


def test_remediate_without_order_id_resolves_via_transaction(isolated_database):
    """
    If order_id is empty or None, remediation should look up the order_id from the transaction record.
    """
    res = db.remediate_duplicate_charge(order_id="", duplicate_txn_id="TXN-7712")
    assert res["ok"] is True
    assert res["order_id"] == "ORD-81094"
    assert "REF-81094-AUTO" in res["refund_id"]

    # Verify customer account was also matched and updated
    acc = next(a for a in read_csv_records(isolated_database["accounts_file"]) if a["customer_id"] == "USR-07231")
    assert acc["account_balance"] == "$0.00 (Settled)"
    assert acc["refund_id"] == res["refund_id"]


def test_inspect_tool_reflects_remediation_lifecycle(isolated_database):
    """
    Validate that inspect_account_and_transactions accurately reflects
    duplicate detection before remediation and settled status after remediation.
    """
    # 1. Before remediation: Duplicate detected
    audit_pre = db.inspect_account_and_transactions(customer_id="USR-07231")
    assert audit_pre["ok"] is True
    assert audit_pre["has_duplicate"] is True
    assert audit_pre["duplicate_detected"] is True
    assert audit_pre["is_already_refunded"] is False
    assert audit_pre["settled_count"] == 2
    assert audit_pre["refunded_count"] == 0

    # 2. Perform remediation
    rem_res = db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")
    assert rem_res["ok"] is True

    # 3. After remediation: Accidental duplicate resolved, is_already_refunded is True
    audit_post = db.inspect_account_and_transactions(customer_id="USR-07231")
    assert audit_post["ok"] is True
    assert audit_post["settled_count"] == 1
    assert audit_post["refunded_count"] == 1
    assert audit_post["duplicate_detected"] is False
    assert audit_post["is_already_refunded"] is True
    assert audit_post["customer"]["account_balance"] == "$0.00 (Settled)"
    assert audit_post["customer"]["account_status"] == "Active - In Good Standing"


# =========================================================================
# 5. Public Static Directory Integrity
# =========================================================================

def test_public_directory_completely_frozen():
    """
    Verify public/ static files remain 100% frozen with zero modifications.
    """
    repo_root = Path(__file__).resolve().parent.parent
    public_dir = repo_root / "public"

    assert public_dir.exists(), "public/ directory does not exist!"
    assert (public_dir / "index.html").exists(), "public/index.html is missing!"

    # Use git status to check if any files in public/ are modified or untracked
    cmd = ["git", "status", "--porcelain", "public/"]
    result = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "", f"public/ directory has modifications!\n{result.stdout}"

    # Use git diff to check if any changes against HEAD exist
    diff_cmd = ["git", "diff", "HEAD", "--", "public/"]
    diff_result = subprocess.run(diff_cmd, cwd=str(repo_root), capture_output=True, text=True)
    assert diff_result.returncode == 0
    assert diff_result.stdout.strip() == "", f"public/ directory differs from git HEAD!\n{diff_result.stdout}"
