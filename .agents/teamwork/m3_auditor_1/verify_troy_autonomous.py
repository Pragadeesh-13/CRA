import sys, os, shutil, tempfile, hashlib
sys.path.insert(0, os.path.abspath("."))

import asyncio
from app.simulation.troy import TroySimulation
from app.simulation.state import SimulationState
from app.http.sse_manager import SseManager
from app.providers.mock_driver import MockDriver
import app.database as db

def hash_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

def test_intent_evaluation():
    print("--- [TEST 1] evaluate_complaint_intent classification ---")
    billing_cases = [
        ("I was charged twice for order ORD-88219", "database_remediation", "ORD-88219"),
        ("There is a duplicate debit on my card for TXN-7712", "database_remediation", None),
        ("Double charge on monthly renewal, please refund", "database_remediation", None),
        ("Unauthorized duplicate charge of $49", "database_remediation", None),
    ]
    for text, expected_intent, expected_ord in billing_cases:
        res = TroySimulation.evaluate_complaint_intent(text)
        assert res["intent"] == expected_intent, f"Expected {expected_intent} for '{text}', got {res['intent']}"
        assert res["is_billing_dispute"] is True
        assert res["requires_db_mutation"] is True
        if expected_ord:
            assert res["order_id"] == expected_ord

    inquiry_cases = [
        "Cannot log in because 2FA SMS code is not arriving",
        "Where can I find documentation for the webhook API?",
        "Getting an unhandled error when exporting PDF on Android",
        "How do I update my notification email settings?",
    ]
    for text in inquiry_cases:
        res = TroySimulation.evaluate_complaint_intent(text)
        assert res["intent"] == "direct_response", f"Expected direct_response for '{text}', got {res['intent']}"
        assert res["is_billing_dispute"] is False
        assert res["requires_db_mutation"] is False

    print("[PASS] evaluate_complaint_intent correctly classifies billing disputes vs direct inquiries across diverse phrasing.")

async def test_inquiry_bypasses_db_mutation():
    print("\n--- [TEST 2] Inquiries genuinely bypass DB mutation and leave CSVs untouched ---")
    # Take initial hashes of data/transactions.csv and data/customer_accounts.csv
    txn_csv = "data/transactions.csv"
    acc_csv = "data/customer_accounts.csv"
    h_txn_before = hash_file(txn_csv)
    h_acc_before = hash_file(acc_csv)

    sse = SseManager()
    state = SimulationState()
    driver = MockDriver()
    troy = TroySimulation(state=state, sse=sse, driver=driver)

    # Run technical inquiry ticket
    inquiry_complaint = "I cannot receive my 2FA authentication SMS code on my phone."
    inquiry_ticket = {
        "id": "TICKET-INQ-001",
        "complaint": inquiry_complaint,
        "query": inquiry_complaint,
        "customer": {"name": "Test User", "id": "USR-99999", "email": "test@example.com"}
    }

    # Run resolution
    result = await troy.run_resolution(ticket=inquiry_ticket)
    assert result is not None

    h_txn_after = hash_file(txn_csv)
    h_acc_after = hash_file(acc_csv)

    assert h_txn_before == h_txn_after, "data/transactions.csv was mutated during an inquiry!"
    assert h_acc_before == h_acc_after, "data/customer_accounts.csv was mutated during an inquiry!"
    print("[PASS] Inquiries leave both data/transactions.csv and data/customer_accounts.csv 100% byte-identical (zero mutation).")

async def test_duplicate_charge_remediation_isolated():
    print("\n--- [TEST 3] Duplicate charge executes genuine DB inspection and atomic remediation ---")
    # Set up isolated database files in temp directory
    temp_dir = tempfile.mkdtemp()
    orig_tx_path = db.TRANSACTIONS_CSV_PATH
    orig_acc_path = db.CUSTOMER_ACCOUNTS_CSV_PATH

    temp_tx_path = os.path.join(temp_dir, "transactions.csv")
    temp_acc_path = os.path.join(temp_dir, "customer_accounts.csv")
    shutil.copyfile(orig_tx_path, temp_tx_path)
    shutil.copyfile(orig_acc_path, temp_acc_path)

    db.TRANSACTIONS_CSV_PATH = temp_tx_path
    db.CUSTOMER_ACCOUNTS_CSV_PATH = temp_acc_path

    try:
        # Verify pre-remediation state for TXN-9012 (Alex Morgan, ORD-88219)
        pre_txns = db.query_transactions(order_id="ORD-88219")
        dup_pre = next(t for t in pre_txns if t["transaction_id"] == "TXN-9012")
        assert dup_pre["status"] == "Settled", f"Expected initial Settled, got {dup_pre['status']}"
        assert dup_pre.get("refund_id", "") == ""

        sse = SseManager()
        state = SimulationState()
        driver = MockDriver()
        troy = TroySimulation(state=state, sse=sse, driver=driver)

        billing_complaint = "I was charged twice for monthly renewal under order ORD-88219. Please refund duplicate charge TXN-9012."
        billing_ticket = {
            "id": "TICKET-DUP-001",
            "complaint": billing_complaint,
            "query": billing_complaint,
            "customer": {"name": "Alex Morgan", "id": "USR-10492", "email": "alex.morgan@gmail.com"}
        }

        # Run resolution
        result = await troy.run_resolution(ticket=billing_ticket)
        assert result is not None

        # Post-remediation verification
        post_txns = db.query_transactions(order_id="ORD-88219")
        dup_post = next(t for t in post_txns if t["transaction_id"] == "TXN-9012")
        assert dup_post["status"] == "Refunded", f"Expected status Refunded, got {dup_post['status']}"
        assert dup_post["refund_id"] != "", "refund_id must be populated"
        assert dup_post["refund_amount"] == "49.00"
        assert "Troy" in dup_post["processed_by"]

        acc_post = db.query_customer_account(customer_id="USR-10492")
        assert acc_post["account_balance"] == "$0.00 (Settled)"
        assert acc_post["account_status"] == "Active - In Good Standing"
        assert "Refunded" in acc_post["refund_status"]
        print(f"[PASS] Troy autonomously inspected ledger and remediated TXN-9012 (Refund ID: {dup_post['refund_id']}). Account restored to $0.00 settled balance.")

        # Test Idempotency: run again on the already refunded ticket
        print("--- [TEST 4] Idempotency: reconfirming already refunded charge without double-refund ---")
        h_txn_post1 = hash_file(temp_tx_path)
        result2 = await troy.run_resolution(ticket=billing_ticket)
        post_txns2 = db.query_transactions(order_id="ORD-88219")
        dup_post2 = next(t for t in post_txns2 if t["transaction_id"] == "TXN-9012")
        assert dup_post2["status"] == "Refunded"
        assert dup_post2["refund_id"] == dup_post["refund_id"]
        print("[PASS] Re-running resolution on refunded transaction reconfirms state without corrupting records or re-refunding.")

    finally:
        db.TRANSACTIONS_CSV_PATH = orig_tx_path
        db.CUSTOMER_ACCOUNTS_CSV_PATH = orig_acc_path
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    test_intent_evaluation()
    asyncio.run(test_inquiry_bypasses_db_mutation())
    asyncio.run(test_duplicate_charge_remediation_isolated())
    print("\nALL FORENSIC AUDIT CHECKS PASSED EMPIRICALLY!")
