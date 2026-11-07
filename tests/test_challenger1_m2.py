"""
Empirical Adversarial Test Suite for Milestone 2 (Generic Database Tools & Ledger Remediation).
Author: Milestone 2 Challenger 1 (m2_challenger_1_g2)
Roles: critic, specialist

Focus:
1. Concurrency & Thread-Safety:
   - High-concurrency mixed reader & writer workloads.
   - Empirical validation of Windows file sharing and [WinError 5] Access is denied during atomic file rename.
2. Atomic Multi-Table Synchronization:
   - Verification that both transactions.csv and customer_accounts.csv update atomically on disk.
   - Partial failure / rollback behavior when one table write fails.
3. Idempotency Under High Stress:
   - 50 concurrent remediation requests on the same duplicate charge.
   - Invariance of refund_id, refund_amount, and settled balances across redundant calls.
   - Protection against accidental amount mutation on already-refunded records.
4. Edge Cases & Boundary Conditions:
   - Whitespace, None/empty inputs, case-insensitivity, non-existent records.
"""

import csv
import threading
import tempfile
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest

import app.database as db


def count_rows(file_path: Path) -> int:
    with open(file_path, "r", encoding="utf-8") as f:
        return sum(1 for _ in csv.reader(f))


def get_records(file_path: Path):
    with open(file_path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# =========================================================================
# 1. Concurrency and Thread Safety Stress Tests
# =========================================================================

def test_concurrent_50_remediations_same_transaction(isolated_database):
    """
    Stress-test: 50 threads concurrently call remediate_duplicate_charge on TXN-7712.
    Verifies:
    - Exactly 1 initial remediation (is_already_refunded=False)
    - Exactly 49 subsequent remediations (is_already_refunded=True)
    - All 50 threads receive the identical refund_id
    - Exactly 1 row for TXN-7712 exists in transactions.csv
    - Exactly 1 row for USR-07231 exists in customer_accounts.csv
    - No CSV row explosion or corruption occurs
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    initial_tx_count = count_rows(tx_file)
    initial_acc_count = count_rows(acc_file)

    def worker(idx: int):
        return db.remediate_duplicate_charge(
            order_id="ORD-81094",
            duplicate_txn_id="TXN-7712",
            refund_amount="49.00",
            processed_by=f"StressTester-{idx}",
            notes=f"Concurrent remediation stress index {idx}",
        )

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(worker, i) for i in range(50)]
        results = [f.result() for f in futures]

    assert all(r["ok"] is True for r in results), "Some concurrent remediation calls failed"

    first_runs = [r for r in results if not r.get("is_already_refunded")]
    subsequent_runs = [r for r in results if r.get("is_already_refunded")]

    assert len(first_runs) == 1, f"Expected exactly 1 first run, got {len(first_runs)}"
    assert len(subsequent_runs) == 49, f"Expected 49 subsequent runs, got {len(subsequent_runs)}"

    refund_ids = {r["refund_id"] for r in results}
    assert len(refund_ids) == 1, f"Multiple refund IDs generated under concurrency: {refund_ids}"

    # Row count invariance check
    assert count_rows(tx_file) == initial_tx_count
    assert count_rows(acc_file) == initial_acc_count


def test_concurrent_updates_different_records(isolated_database):
    """
    Stress-test: Multiple threads concurrently updating distinct transactions and customer accounts.
    Verifies thread safety across both tables without dropped writes.
    """
    updates = [
        ("tx", "TXN-9011", {"notes": "Batch update TXN-9011"}),
        ("tx", "TXN-8801", {"notes": "Batch update TXN-8801"}),
        ("tx", "TXN-8450", {"notes": "Batch update TXN-8450"}),
        ("acc", "USR-10492", {"notes": "Batch update USR-10492"}),
        ("acc", "USR-08914", {"notes": "Batch update USR-08914"}),
        ("acc", "USR-12053", {"notes": "Batch update USR-12053"}),
    ]

    def run_update(task):
        table, record_id, payload = task
        if table == "tx":
            return db.update_transaction(record_id, payload)
        else:
            return db.update_customer_account(record_id, payload)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(run_update, u) for u in updates]
        results = [f.result() for f in futures]

    assert all(results), "Not all concurrent updates succeeded"

    tx_records = get_records(isolated_database["transactions_file"])
    for tid, note in [("TXN-9011", "Batch update TXN-9011"), ("TXN-8801", "Batch update TXN-8801"), ("TXN-8450", "Batch update TXN-8450")]:
        row = next(t for t in tx_records if t["transaction_id"] == tid)
        assert row["notes"] == note

    acc_records = get_records(isolated_database["accounts_file"])
    for cid, note in [("USR-10492", "Batch update USR-10492"), ("USR-08914", "Batch update USR-08914"), ("USR-12053", "Batch update USR-12053")]:
        row = next(a for a in acc_records if a["customer_id"] == cid)
        assert row["notes"] == note


# =========================================================================
# 2. Dual-Table Atomicity and Consistency Tests
# =========================================================================

def test_atomic_dual_table_disk_synchronization(isolated_database):
    """
    Test that remediation synchronously writes and flushes both tables to disk.
    Reading directly from raw file paths without db API confirms disk persistence.
    """
    tx_file = isolated_database["transactions_file"]
    acc_file = isolated_database["accounts_file"]

    res = db.remediate_duplicate_charge(
        order_id="ORD-81094",
        duplicate_txn_id="TXN-7712",
        refund_amount="49.00",
    )
    assert res["ok"] is True

    # Read raw disk content directly
    with open(tx_file, "r", encoding="utf-8") as f:
        tx_rows = list(csv.DictReader(f))
    with open(acc_file, "r", encoding="utf-8") as f:
        acc_rows = list(csv.DictReader(f))

    tx_match = next((t for t in tx_rows if t["transaction_id"] == "TXN-7712"), None)
    acc_match = next((a for a in acc_rows if a["customer_id"] == "USR-07231"), None)

    assert tx_match is not None
    assert tx_match["status"] == "Refunded"
    assert tx_match["refund_amount"] == "49.00"

    assert acc_match is not None
    assert acc_match["account_balance"] == "$0.00 (Settled)"
    assert acc_match["account_status"] == "Active - In Good Standing"
    assert tx_match["refund_id"] == acc_match["refund_id"]


def test_idempotent_remediation_preserves_immutable_terms(isolated_database):
    """
    Verify that subsequent calls to remediate_duplicate_charge do not overwrite
    the original refund_id, timestamp, or refund_amount.
    """
    res1 = db.remediate_duplicate_charge(
        order_id="ORD-81094",
        duplicate_txn_id="TXN-7712",
        refund_amount="49.00",
        processed_by="Agent Original",
        notes="Original note",
    )
    orig_ref_id = res1["refund_id"]
    orig_timestamp = res1["refunded_at"]

    # Re-call with different processor and different refund amount (must not overwrite settled terms)
    res2 = db.remediate_duplicate_charge(
        order_id="ORD-81094",
        duplicate_txn_id="TXN-7712",
        refund_amount="100.00",
        processed_by="Agent Repeat",
    )

    assert res2["is_already_refunded"] is True
    assert res2["refund_id"] == orig_ref_id
    assert res2["refunded_at"] == orig_timestamp
    assert res2["refund_amount"] == "49.00"

    # Verify disk record preserves original settlement amount
    tx_records = get_records(isolated_database["transactions_file"])
    tx_7712 = next(t for t in tx_records if t["transaction_id"] == "TXN-7712")
    assert tx_7712["refund_amount"] == "49.00"


# =========================================================================
# 4. Adversarial Failure Mode Reproductions (Remediated)
# =========================================================================

def test_concurrent_reader_writer_contention_windows(isolated_database):
    """
    Adversarial reproduction: 5 reader threads continuously query transactions and accounts
    while 5 writer threads attempt to update records.
    Without read locks, os.replace raises [WinError 5] and writes are lost.
    """
    stop_readers = False
    errors = []

    def reader_worker():
        while not stop_readers:
            try:
                db.query_transactions()
                db.query_customer_account(customer_id="USR-10492")
            except Exception as e:
                errors.append(("reader", e))

    def writer_worker(idx):
        for i in range(25):
            ok = db.update_transaction("TXN-9011", {"notes": f"update-{idx}-{i}"})
            if not ok:
                errors.append(("writer", f"update failed at {idx}-{i}"))

    readers = [threading.Thread(target=reader_worker) for _ in range(5)]
    writers = [threading.Thread(target=writer_worker, args=(i,)) for i in range(5)]

    for t in readers:
        t.start()
    for t in writers:
        t.start()

    for t in writers:
        t.join()
    stop_readers = True
    for t in readers:
        t.join()

    # If the bug is present, errors will be non-empty
    assert len(errors) == 0, f"Encountered {len(errors)} concurrency errors: {errors[:5]}"


def test_atomicity_partial_write_rollback(isolated_database, monkeypatch):
    """
    Adversarial reproduction: Simulate write failure on customer_accounts.csv.
    Verify that transactions.csv is rolled back and not left in a desynchronized state.
    """
    orig_write_acc = db.write_all_customer_accounts
    monkeypatch.setattr(db, "write_all_customer_accounts", lambda records: False)

    res = db.remediate_duplicate_charge("ORD-81094", "TXN-7712")
    assert res["ok"] is False

    # Check transactions.csv on disk - it should NOT be modified to 'Refunded'
    with open(isolated_database["transactions_file"], "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    tx_7712 = next(t for t in rows if t["transaction_id"] == "TXN-7712")
    assert tx_7712["status"] == "Settled", f"Atomicity breach: TXN-7712 was set to {tx_7712['status']} despite remediation failure!"


def test_atomicity_first_table_failure_aborts_second_table(isolated_database, monkeypatch):
    """
    Adversarial reproduction: Simulate write failure on transactions.csv.
    Verify that customer_accounts.csv is NOT updated if the transaction write fails.
    """
    monkeypatch.setattr(db, "write_all_transactions", lambda records: False)

    res = db.remediate_duplicate_charge("ORD-81094", "TXN-7712")
    assert res["ok"] is False

    # Check customer_accounts.csv on disk - Emily's balance should NOT be settled
    with open(isolated_database["accounts_file"], "r", encoding="utf-8") as f:
        acc_rows = list(csv.DictReader(f))
    emily = next(a for a in acc_rows if a["customer_id"] == "USR-07231")
    assert "$0.00" not in emily["account_balance"], "Atomicity breach: Customer account settled despite transaction write failure!"



def test_query_customer_account_with_whitespace_and_mixed_case(isolated_database):
    """
    Verify case-insensitivity and whitespace stripping in query_customer_account.
    """
    res1 = db.query_customer_account(customer_id="  usr-10492  ")
    assert res1 is not None
    assert res1["customer_name"] == "Alex Morgan"

    res2 = db.query_customer_account(email="  ALEX.MORGAN@GMAIL.COM  ")
    assert res2 is not None
    assert res2["customer_id"] == "USR-10492"

    res3 = db.query_customer_account(order_id="  ord-88219  ")
    assert res3 is not None
    assert res3["customer_id"] == "USR-10492"


def test_query_transactions_filtering_matrix(isolated_database):
    """
    Verify query_transactions filtering capabilities by customer_id, order_id, status, and billing cycle.
    """
    # 1. Filter by status 'Settled'
    settled = db.query_transactions(status="settled")
    assert len(settled) > 0
    assert all(t["status"].lower() == "settled" for t in settled)

    # 2. Filter by customer_id and billing_cycle
    emily_txs = db.query_transactions(customer_id="USR-07231", billing_cycle="September 2026")
    assert len(emily_txs) == 2
    assert {t["transaction_id"] for t in emily_txs} == {"TXN-7711", "TXN-7712"}

    # 3. Filter by non-existent combination returns empty list
    none_txs = db.query_transactions(customer_id="USR-07231", billing_cycle="NonExistentCycle 2099")
    assert none_txs == []


def test_update_transaction_nonexistent_returns_false(isolated_database):
    """
    Attempting to update a non-existent transaction must return False without crashing.
    """
    ok = db.update_transaction("TXN-NONEXISTENT", {"notes": "test"})
    assert ok is False


def test_update_customer_account_nonexistent_returns_false(isolated_database):
    """
    Attempting to update a non-existent customer account must return False without crashing.
    """
    ok = db.update_customer_account("USR-NONEXISTENT", {"notes": "test"})
    assert ok is False


def test_inspect_tool_empty_query_no_crash(isolated_database):
    """
    inspect_account_and_transactions with empty or unknown arguments returns ok=False cleanly.
    """
    res = db.inspect_account_and_transactions(customer_id="USR-99999", query="nonexistent")
    assert res["ok"] is False
    assert res["transactions"] == []
    assert res["account"] is None
