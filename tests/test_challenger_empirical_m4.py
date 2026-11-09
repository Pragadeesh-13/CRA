"""
Empirical Challenger Stress Test Suite (Milestone 4 - Challenger 2).
Exhaustively tests:
1. Multi-customer querying and zero hardcoded fallback to USR-10492.
2. Atomic duplicate charge remediation across transactions.csv and customer_accounts.csv.
3. Extreme idempotency (sequential and concurrent) without balance corruption.
4. Custom refund amounts, boundary values, and malicious inputs.
5. Absolute database isolation of on-disk production CSV files.
6. End-to-end complaint resolution scenarios:
   - Alex Morgan (ORD-88219)
   - Emily Watson (ORD-81094)
   - General technical inquiry (2FA SMS gateway failure) with ZERO DB mutations.
"""

import asyncio
import concurrent.futures
import hashlib
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.database as db
from app.simulation.troy import Troy
from app.http.sse_manager import SseManager
from app.simulation.state import SimulationState
from mock_driver import MockDriver


ROOT_DIR = Path(__file__).resolve().parent.parent
PROD_DATA_DIR = ROOT_DIR / "data"
PROD_TX_FILE = PROD_DATA_DIR / "transactions.csv"
PROD_ACC_FILE = PROD_DATA_DIR / "customer_accounts.csv"


def get_file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ==============================================================================
# 1. Multi-Customer Querying & Zero Hardcoded Fallbacks
# ==============================================================================

class TestMultiCustomerQueryingEmpirical:
    """Empirical verification of multi-customer querying with zero hardcoded fallbacks."""

    KNOWN_CUSTOMERS = [
        {"customer_id": "USR-10492", "customer_name": "Alex Morgan", "order_id": "ORD-88219", "email": "alex.morgan@gmail.com"},
        {"customer_id": "USR-07231", "customer_name": "Emily Watson", "order_id": "ORD-81094", "email": "emily.w@consulting.co"},
        {"customer_id": "USR-08914", "customer_name": "Sarah Jenkins", "order_id": "ORD-79110", "email": "sarah.j@enterprise.org"},
        {"customer_id": "USR-12053", "customer_name": "David Chen", "order_id": "ORD-93041", "email": "d.chen@fintech.io"},
        {"customer_id": "USR-14309", "customer_name": "Elena Rostova", "order_id": "ORD-85420", "email": "elena@globaltrade.de"},
        {"customer_id": "USR-09148", "customer_name": "Michael Scott", "order_id": "ORD-86102", "email": "m.scott@dunder.com"},
    ]

    def test_all_known_customers_query_by_id(self, isolated_database):
        for c in self.KNOWN_CUSTOMERS:
            acc = db.query_customer_account(customer_id=c["customer_id"])
            assert acc is not None, f"Customer ID {c['customer_id']} not found"
            assert acc.get("customer_name") == c["customer_name"]
            assert acc.get("reference_order_id") == c["order_id"]
            assert acc.get("customer_email") == c["email"]

    def test_all_known_customers_query_by_email(self, isolated_database):
        for c in self.KNOWN_CUSTOMERS:
            acc = db.query_customer_account(email=c["email"])
            assert acc is not None, f"Email {c['email']} not found"
            assert acc.get("customer_id") == c["customer_id"]
            assert acc.get("customer_name") == c["customer_name"]

    def test_all_known_customers_query_by_order(self, isolated_database):
        for c in self.KNOWN_CUSTOMERS:
            # Test with ORD- prefix
            acc = db.query_customer_account(order_id=c["order_id"])
            assert acc is not None, f"Order {c['order_id']} not found"
            assert acc.get("customer_id") == c["customer_id"]

            # Test without ORD- prefix (e.g. '81094')
            raw_num = c["order_id"].replace("ORD-", "")
            acc_num = db.query_customer_account(order_id=raw_num)
            assert acc_num is not None, f"Raw order number {raw_num} not found"
            assert acc_num.get("customer_id") == c["customer_id"]

    @pytest.mark.parametrize(
        "nonexistent_id",
        [
            "USR-99999",
            "USR-00000",
            "USR-UNKNOWN",
            "CUST-10492",
            "ORD-99999",
            "nonexistent@domain.com",
            "'; DROP TABLE transactions;--",
            "<script>alert(1)</script>",
        ],
    )
    def test_zero_hardcoded_fallback_for_nonexistent_queries(self, isolated_database, nonexistent_id):
        # query_customer_account must return None, NOT Alex Morgan
        res_acc = db.query_customer_account(customer_id=nonexistent_id)
        assert res_acc is None, f"Expected None for {nonexistent_id}, got {res_acc}"

        res_email = db.query_customer_account(email=nonexistent_id)
        assert res_email is None, f"Expected None for {nonexistent_id}, got {res_email}"

        res_order = db.query_customer_account(order_id=nonexistent_id)
        assert res_order is None, f"Expected None for {nonexistent_id}, got {res_order}"

        # query_transactions must return empty list, NOT Alex Morgan transactions
        txns = db.query_transactions(customer_id=nonexistent_id)
        assert txns == [], f"Expected empty list for {nonexistent_id}, got {txns}"

        # inspect_account_and_transactions must indicate failure, NOT Alex Morgan
        inspect_res = db.inspect_account_and_transactions(customer_id=nonexistent_id)
        assert inspect_res.get("ok") is False or inspect_res.get("account") is None


# ==============================================================================
# 2. Atomic Duplicate Charge Remediation & Cross-Table Consistency
# ==============================================================================

class TestAtomicRemediationEmpirical:
    """Empirical verification of atomic multi-table remediation and rollback."""

    def test_emily_watson_remediation_dual_table_sync(self, isolated_database):
        # Initial state verification
        txns_pre = db.query_transactions(customer_id="USR-07231")
        dup_pre = next(t for t in txns_pre if t.get("transaction_id") == "TXN-7712")
        assert dup_pre.get("status") == "Settled"

        acc_pre = db.query_customer_account(customer_id="USR-07231")
        assert "$0.00" not in acc_pre.get("account_balance")

        # Execute remediation
        rem = db.remediate_duplicate_charge(
            order_id="ORD-81094",
            duplicate_txn_id="TXN-7712",
            refund_amount="49.00",
            processed_by="Troy Empirical Challenger",
            notes="Empirical verification test",
        )
        assert rem.get("ok") is True
        assert rem.get("status") == "Refunded"
        assert rem.get("refund_amount") == "49.00"
        ref_id = rem.get("refund_id")
        assert "REF-81094-AUTO" in ref_id

        # Verify transactions.csv updated
        txns_post = db.query_transactions(customer_id="USR-07231")
        dup_post = next(t for t in txns_post if t.get("transaction_id") == "TXN-7712")
        assert dup_post.get("status") == "Refunded"
        assert dup_post.get("refund_id") == ref_id
        assert dup_post.get("refund_amount") == "49.00"
        assert dup_post.get("processed_by") == "Troy Empirical Challenger"

        # Verify customer_accounts.csv updated
        acc_post = db.query_customer_account(customer_id="USR-07231")
        assert acc_post.get("account_balance") == "$0.00 (Settled)"
        assert acc_post.get("account_status") == "Active - In Good Standing"
        assert acc_post.get("refund_id") == ref_id
        assert acc_post.get("duplicate_verified") == "Yes - Accidental Double Charge Detected"
        assert acc_post.get("last_verified_by") == "Troy Empirical Challenger"

    def test_atomic_rollback_on_customer_account_write_failure(self, isolated_database, monkeypatch):
        # Force write_all_customer_accounts to fail
        monkeypatch.setattr(db, "write_all_customer_accounts", lambda recs: False)

        rem = db.remediate_duplicate_charge(
            order_id="ORD-81094",
            duplicate_txn_id="TXN-7712",
        )
        assert rem.get("ok") is False
        assert "Failed" in rem.get("error", "")

        # Verify transactions.csv was rolled back and is STILL 'Settled'
        txns = db.query_transactions(customer_id="USR-07231")
        dup = next(t for t in txns if t.get("transaction_id") == "TXN-7712")
        assert dup.get("status") == "Settled", "Transaction was NOT rolled back after customer accounts write failure!"


# ==============================================================================
# 3. Idempotency Stress Testing (Sequential and Concurrent)
# ==============================================================================

class TestIdempotencyEmpirical:
    """Stress tests idempotency under sequential repetition and high concurrency."""

    def test_20x_sequential_remediation_no_balance_corruption(self, isolated_database):
        # First call: executes initial refund
        first = db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")
        assert first.get("ok") is True
        initial_ref_id = first.get("refund_id")

        # Subsequent 19 calls
        for i in range(19):
            subsequent = db.remediate_duplicate_charge(
                order_id="ORD-81094",
                duplicate_txn_id="TXN-7712",
                refund_amount="999.00",  # Adversarial attempt to alter refund amount
            )
            assert subsequent.get("ok") is True
            assert subsequent.get("is_already_refunded") is True or subsequent.get("already_refunded") is True
            # Assert refund_id is strictly preserved
            assert subsequent.get("refund_id") == initial_ref_id

            # Verify balance is still $0.00
            acc = db.query_customer_account(customer_id="USR-07231")
            assert acc.get("account_balance") == "$0.00 (Settled)"
            assert acc.get("account_status") == "Active - In Good Standing"

    def test_30_thread_concurrent_remediation_race_stress(self, isolated_database):
        results = []

        def worker(thread_idx):
            return db.remediate_duplicate_charge(
                order_id="ORD-81094",
                duplicate_txn_id="TXN-7712",
                processed_by=f"Worker-{thread_idx}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(30)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        assert len(results) == 30
        assert all(r.get("ok") is True for r in results)

        # Check ledger consistency after 30 concurrent calls
        txns = db.query_transactions(customer_id="USR-07231")
        dup = next(t for t in txns if t.get("transaction_id") == "TXN-7712")
        assert dup.get("status") == "Refunded"

        acc = db.query_customer_account(customer_id="USR-07231")
        assert acc.get("account_balance") == "$0.00 (Settled)"
        assert acc.get("account_status") == "Active - In Good Standing"


# ==============================================================================
# 4. Custom Refund Amounts & Edge Inputs
# ==============================================================================

class TestCustomRefundAndEdgeCasesEmpirical:
    """Empirical verification of custom refund amounts and invalid inputs."""

    @pytest.mark.parametrize("amt", ["10.00", "49.00", "100.50", "0.00"])
    def test_custom_refund_amounts(self, isolated_database, amt):
        rem = db.remediate_duplicate_charge(
            order_id="ORD-81094",
            duplicate_txn_id="TXN-7712",
            refund_amount=amt,
        )
        assert rem.get("ok") is True
        assert rem.get("refund_amount") == amt

        txns = db.query_transactions(customer_id="USR-07231")
        dup = next(t for t in txns if t.get("transaction_id") == "TXN-7712")
        assert dup.get("refund_amount") == amt

    def test_invalid_and_empty_txn_id_fails_safely(self, isolated_database):
        res_empty = db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="")
        assert res_empty.get("ok") is False

        res_none = db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id=None)
        assert res_none.get("ok") is False

        res_nonexistent = db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-NONEXISTENT")
        assert res_nonexistent.get("ok") is False


# ==============================================================================
# 5. Database Isolation Protection of Production CSVs
# ==============================================================================

class TestDatabaseIsolationEmpirical:
    """Verifies that tests using isolated_database fixture NEVER modify production CSVs."""

    def test_production_csv_files_unmodified_after_mutations(self, isolated_database):
        # Capture production file hashes before
        hash_tx_before = get_file_sha256(PROD_TX_FILE)
        hash_acc_before = get_file_sha256(PROD_ACC_FILE)

        # Mutate isolated sandbox
        db.remediate_duplicate_charge(order_id="ORD-81094", duplicate_txn_id="TXN-7712")
        db.update_transaction("TXN-7711", {"notes": "Modified in sandbox"})
        db.update_customer_account("USR-10492", {"notes": "Modified in sandbox"})

        # Production files MUST have identical hash
        hash_tx_after = get_file_sha256(PROD_TX_FILE)
        hash_acc_after = get_file_sha256(PROD_ACC_FILE)

        assert hash_tx_before == hash_tx_after, "CRITICAL: Production transactions.csv was mutated!"
        assert hash_acc_before == hash_acc_after, "CRITICAL: Production customer_accounts.csv was mutated!"


# ==============================================================================
# 6. End-to-End Complaint Resolution Scenarios (R2, R3, R4)
# ==============================================================================

class TestEndToEndComplaintScenariosEmpirical:
    """Empirical verification of E2E complaint resolution scenarios."""

    @pytest.fixture
    def test_client(self):
        with TestClient(app) as c:
            yield c

    @pytest.mark.asyncio
    async def test_scenario_alex_morgan_duplicate_charge(self, isolated_database):
        """End-to-end resolution of Alex Morgan ORD-88219 duplicate charge."""
        complaint = "I was charged twice $49.00 for my Pro tier renewal under order ORD-88219. Please fix this."
        story = {
            "title": "Double Charge on ORD-88219",
            "description": complaint,
            "publishedAt": "2026-11-07T15:00:00Z",
            "source": "Ticket",
        }

        sse = SseManager()
        troy = Troy(
            config={},
            driver=MockDriver(),
            sse_manager=sse,
            state=SimulationState(),
        )

        ok = await troy.run(story)
        assert ok is True

        # Verify audit & remediation
        analysis = db.inspect_account_and_transactions(customer_id="USR-10492")
        assert analysis.get("ok") is True
        assert analysis.get("is_already_refunded") is True

        # Verify Alex Morgan ledger is settled
        acc = db.query_customer_account(customer_id="USR-10492")
        assert acc.get("account_balance") == "$0.00 (Settled)"
        assert acc.get("account_status") == "Active - In Good Standing"

    @pytest.mark.asyncio
    async def test_scenario_emily_watson_duplicate_charge(self, isolated_database):
        """End-to-end resolution of Emily Watson ORD-81094 duplicate charge."""
        complaint = "Hi, order ORD-81094 was debited twice ($49.00 each) on my statement. Redundant transaction is TXN-7712."
        story = {
            "title": "Duplicate charge on ORD-81094",
            "description": complaint,
            "publishedAt": "2026-11-07T15:00:00Z",
            "source": "Ticket",
        }

        sse = SseManager()
        troy = Troy(
            config={},
            driver=MockDriver(),
            sse_manager=sse,
            state=SimulationState(),
        )

        ok = await troy.run(story)
        assert ok is True

        # Verify TXN-7712 is Refunded
        txns = db.query_transactions(customer_id="USR-07231")
        dup = next(t for t in txns if t.get("transaction_id") == "TXN-7712")
        assert dup.get("status") == "Refunded"

        # Verify Emily Watson account settled
        acc = db.query_customer_account(customer_id="USR-07231")
        assert acc.get("account_balance") == "$0.00 (Settled)"
        assert acc.get("account_status") == "Active - In Good Standing"

    @pytest.mark.asyncio
    async def test_scenario_general_technical_inquiry_zero_db_mutation(self, isolated_database):
        """End-to-end resolution of Sarah Jenkins 2FA SMS technical inquiry with ZERO DB mutations."""
        complaint = "Two-factor authentication (2FA) SMS codes are not arriving for my Enterprise account USR-08914. I cannot login."
        story = {
            "title": "2FA SMS Failure",
            "description": complaint,
            "publishedAt": "2026-11-07T15:00:00Z",
            "source": "Ticket",
        }

        # Snapshot transactions and accounts before
        txns_before = db.read_all_transactions()
        accs_before = db.read_all_customer_accounts()

        sse = SseManager()
        troy = Troy(
            config={},
            driver=MockDriver(),
            sse_manager=sse,
            state=SimulationState(),
        )

        ok = await troy.run(story)
        assert ok is True

        # Snapshot transactions and accounts after
        txns_after = db.read_all_transactions()
        accs_after = db.read_all_customer_accounts()

        # ZERO database mutations assertion
        assert txns_before == txns_after, "CRITICAL: General technical inquiry modified transactions.csv!"
        assert accs_before == accs_after, "CRITICAL: General technical inquiry modified customer_accounts.csv!"
