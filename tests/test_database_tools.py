"""
Tests for Generic Database Inspection and Remediation Tools (Milestone 2 & M4 Integration).
Covers query_customer_account, query_transactions, inspect_account_and_transactions,
update_transaction, update_customer_account, and remediate_duplicate_charge,
validating unbiased multi-customer querying, atomic multi-table persistence, idempotency,
and strict database isolation.
"""

import csv
import pytest
import app.database as database


class TestQueryCustomerAccount:
    """Verifies query_customer_account functionality and multi-customer unbiased querying."""

    def test_query_by_customer_id_all_customers(self, isolated_database):
        """Validates exact querying across all distinct customers without hardcoded fallback."""
        test_cases = [
            ("USR-10492", "Alex Morgan", "ORD-88219"),
            ("USR-07231", "Emily Watson", "ORD-81094"),
            ("USR-08914", "Sarah Jenkins", "ORD-79110"),
            ("USR-12053", "David Chen", "ORD-93041"),
            ("USR-14309", "Elena Rostova", "ORD-85420"),
            ("USR-09148", "Michael Scott", "ORD-86102"),
        ]
        for cid, expected_name, expected_order in test_cases:
            acc = database.query_customer_account(customer_id=cid)
            assert acc is not None, f"Customer {cid} not found"
            assert acc.get("customer_name") == expected_name
            assert acc.get("reference_order_id") == expected_order

    def test_query_by_email(self, isolated_database):
        """Validates querying accounts by customer email address."""
        acc1 = database.query_customer_account(email="emily.w@consulting.co")
        assert acc1 is not None
        assert acc1.get("customer_id") == "USR-07231"
        assert acc1.get("customer_name") == "Emily Watson"

        acc2 = database.query_customer_account(email="sarah.j@enterprise.org")
        assert acc2 is not None
        assert acc2.get("customer_id") == "USR-08914"

        acc3 = database.query_customer_account(email="alex.morgan@gmail.com")
        assert acc3 is not None
        assert acc3.get("customer_id") == "USR-10492"

    def test_query_by_order_id(self, isolated_database):
        """Validates querying accounts by reference order ID (with and without ORD- prefix)."""
        acc1 = database.query_customer_account(order_id="ORD-81094")
        assert acc1 is not None
        assert acc1.get("customer_id") == "USR-07231"

        acc2 = database.query_customer_account(order_id="79110")
        assert acc2 is not None
        assert acc2.get("customer_id") == "USR-08914"

    def test_query_case_insensitivity_and_whitespace(self, isolated_database):
        """Validates that casing and whitespace do not cause lookup failures."""
        acc = database.query_customer_account(customer_id="   usr-07231   ")
        assert acc is not None
        assert acc.get("customer_name") == "Emily Watson"

        acc_em = database.query_customer_account(email="  EMILY.W@CONSULTING.CO  ")
        assert acc_em is not None
        assert acc_em.get("customer_id") == "USR-07231"

    def test_query_unknown_customer_returns_none_without_fallback(self, isolated_database):
        """Verifies that querying a non-existent customer returns None and never defaults to Alex Morgan."""
        unknown = database.query_customer_account(customer_id="USR-99999")
        assert unknown is None, "Unknown customer returned non-None record (unbiased querying violation)"

        unknown_email = database.query_customer_account(email="nonexistent@domain.com")
        assert unknown_email is None

        unknown_order = database.query_customer_account(order_id="ORD-99999")
        assert unknown_order is None

    def test_query_empty_arguments_returns_none(self, isolated_database):
        """Verifies that calling query_customer_account with no or empty arguments returns None."""
        assert database.query_customer_account() is None
        assert database.query_customer_account(customer_id="", email="", order_id="") is None
        assert database.query_customer_account(customer_id="   ") is None

    def test_query_sql_injection_safe(self, isolated_database):
        """Verifies that malicious injection strings do not crash or corrupt querying."""
        res = database.query_customer_account(customer_id="'; DROP TABLE customer_accounts;--")
        assert res is None


class TestQueryTransactions:
    """Verifies query_transactions filtering across customer_id, order_id, status, and billing_cycle."""

    def test_query_by_customer_id(self, isolated_database):
        """Verifies filtering transactions by customer ID."""
        txns_emily = database.query_transactions(customer_id="USR-07231")
        assert len(txns_emily) == 2
        assert all(t.get("customer_id") == "USR-07231" for t in txns_emily)
        txn_ids = {t.get("transaction_id") for t in txns_emily}
        assert txn_ids == {"TXN-7711", "TXN-7712"}

        txns_sarah = database.query_transactions(customer_id="USR-08914")
        assert len(txns_sarah) == 1
        assert txns_sarah[0].get("transaction_id") == "TXN-8801"

    def test_query_by_order_id(self, isolated_database):
        """Verifies filtering transactions by order ID."""
        txns = database.query_transactions(order_id="ORD-88219")
        assert len(txns) == 2
        assert all(t.get("order_id") == "ORD-88219" for t in txns)

    def test_query_by_status(self, isolated_database):
        """Verifies filtering transactions by status column."""
        settled = database.query_transactions(status="Settled")
        assert len(settled) > 0
        assert all(t.get("status") == "Settled" for t in settled)

    def test_query_by_billing_cycle(self, isolated_database):
        """Verifies filtering transactions by billing cycle."""
        oct_txns = database.query_transactions(billing_cycle="October 2026")
        assert len(oct_txns) > 0
        assert all(t.get("billing_cycle") == "October 2026" for t in oct_txns)

    def test_query_combined_filters(self, isolated_database):
        """Verifies multi-attribute filtering (customer_id AND status)."""
        txns = database.query_transactions(customer_id="USR-10492", status="Settled")
        assert len(txns) == 1
        assert txns[0].get("transaction_id") == "TXN-9011"

    def test_query_unknown_customer_returns_empty_list(self, isolated_database):
        """Verifies that an unknown customer returns an empty list without error or fallback."""
        txns = database.query_transactions(customer_id="USR-99999")
        assert txns == []


class TestInspectAccountAndTransactions:
    """Verifies combined account inspection and duplicate debit detection."""

    def test_inspect_alex_morgan_duplicate_detected(self, isolated_database):
        """Verifies combined inspection flags duplicate charge for Alex Morgan (ORD-88219)."""
        res = database.inspect_account_and_transactions(customer_id="USR-10492")
        assert isinstance(res, dict)
        account = res.get("account") or res.get("customer")
        assert account is not None
        assert account.get("customer_name") == "Alex Morgan"
        assert len(res.get("transactions", [])) == 2
        assert res.get("has_duplicate", False) is True or res.get("duplicate_detected", False) is True or res.get("is_already_refunded", False) is True

    def test_inspect_emily_watson_duplicate_detected(self, isolated_database):
        """Verifies combined inspection detects duplicate charges for Emily Watson (ORD-81094)."""
        res = database.inspect_account_and_transactions(customer_id="USR-07231")
        assert isinstance(res, dict)
        account = res.get("account") or res.get("customer")
        assert account is not None
        assert account.get("customer_name") == "Emily Watson"
        assert len(res.get("transactions", [])) == 2
        assert res.get("has_duplicate", False) is True or res.get("duplicate_detected", False) is True

    def test_inspect_sarah_jenkins_single_transaction_no_duplicate(self, isolated_database):
        """Verifies customer with normal single transaction is NOT flagged as duplicate."""
        res = database.inspect_account_and_transactions(customer_id="USR-08914")
        assert isinstance(res, dict)
        assert res.get("has_duplicate", False) is False
        assert len(res.get("transactions", [])) == 1

    def test_inspect_by_query_string(self, isolated_database):
        """Verifies inspection resolves customer account by query text containing order ID."""
        res = database.inspect_account_and_transactions(query="Customer inquiries about order ORD-81094")
        assert isinstance(res, dict)
        account = res.get("account") or res.get("customer")
        assert account is not None
        assert account.get("customer_id") == "USR-07231"


class TestUpdateOperations:
    """Verifies generic update_transaction and update_customer_account persistence tools."""

    def test_update_transaction_persists_to_file(self, isolated_database):
        """Verifies update_transaction modifies columns and persists atomically."""
        ok = database.update_transaction("TXN-7712", {"status": "Refunded", "refund_id": "REF-UNITTEST-01"})
        assert ok is True

        txns = database.read_all_transactions()
        target = next((t for t in txns if t.get("transaction_id") == "TXN-7712"), None)
        assert target is not None
        assert target.get("status") == "Refunded"
        assert target.get("refund_id") == "REF-UNITTEST-01"

    def test_update_transaction_invalid_id_returns_false(self, isolated_database):
        """Verifies updating a non-existent transaction returns False."""
        ok = database.update_transaction("TXN-NONEXISTENT", {"status": "Refunded"})
        assert ok is False

    def test_update_customer_account_persists_to_file(self, isolated_database):
        """Verifies update_customer_account modifies columns and persists atomically."""
        ok = database.update_customer_account(
            "USR-07231",
            {"account_balance": "$0.00 (Settled)", "account_status": "Active - In Good Standing"},
        )
        assert ok is True

        accs = database.read_all_customers()
        target = next((a for a in accs if a.get("customer_id") == "USR-07231"), None)
        assert target is not None
        assert target.get("account_balance") == "$0.00 (Settled)"
        assert target.get("account_status") == "Active - In Good Standing"

    def test_update_customer_account_invalid_id_returns_false(self, isolated_database):
        """Verifies updating a non-existent customer account returns False."""
        ok = database.update_customer_account("USR-NONEXISTENT", {"account_status": "Active"})
        assert ok is False


class TestRemediateDuplicateCharge:
    """Verifies atomic duplicate charge remediation, cross-table synchronization, and idempotency."""

    def test_remediate_emily_watson_atomic_sync(self, isolated_database):
        """Verifies atomic remediation for Emily Watson: TXN-7712 refunded and balance restored to $0.00."""
        res = database.remediate_duplicate_charge(
            order_id="ORD-81094",
            duplicate_txn_id="TXN-7712",
            refund_amount="49.00",
            processed_by="Troy (Autonomous Resolution Agent)",
        )
        assert res.get("ok") is True
        assert res.get("status") == "Refunded"
        assert res.get("refund_amount") == "49.00"

        # Check transactions.csv
        txns = database.read_all_transactions()
        txn = next((t for t in txns if t.get("transaction_id") == "TXN-7712"), None)
        assert txn is not None
        assert txn.get("status") == "Refunded"
        assert txn.get("refund_amount") == "49.00"
        assert "REF-81094-AUTO" in txn.get("refund_id", "")

        # Check customer_accounts.csv
        accs = database.read_all_customers()
        acc = next((a for a in accs if a.get("customer_id") == "USR-07231"), None)
        assert acc is not None
        assert acc.get("account_balance") == "$0.00 (Settled)"
        assert acc.get("account_status") == "Active - In Good Standing"

    def test_remediate_idempotency(self, isolated_database):
        """Verifies calling remediate_duplicate_charge multiple times is safe and idempotent."""
        res1 = database.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")
        assert res1.get("ok") is True

        res2 = database.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")
        assert res2.get("ok") is True
        assert res2.get("is_already_refunded", False) is True or res2.get("already_refunded", False) is True

    def test_remediate_custom_refund_amount(self, isolated_database):
        """Verifies custom refund amounts are recorded correctly in transactions."""
        res = database.remediate_duplicate_charge(
            order_id="ORD-81094",
            duplicate_txn_id="TXN-7712",
            refund_amount="25.00",
        )
        assert res.get("ok") is True
        assert res.get("refund_amount") == "25.00"

        txns = database.read_all_transactions()
        txn = next((t for t in txns if t.get("transaction_id") == "TXN-7712"), None)
        assert txn.get("refund_amount") == "25.00"

    def test_remediate_nonexistent_transaction_returns_error(self, isolated_database):
        """Verifies remediating a non-existent transaction fails safely."""
        res = database.remediate_duplicate_charge(order_id="ORD-00000", duplicate_txn_id="TXN-INVALID")
        assert res.get("ok") is False
        assert "error" in res

    def test_remediate_empty_id_returns_error(self, isolated_database):
        """Verifies remediating with empty transaction ID fails safely."""
        res = database.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="")
        assert res.get("ok") is False
