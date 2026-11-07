import sys
from pathlib import Path
root_dir = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(root_dir))

import app.database as db

def run_tests():
    # Test 1: Whitespace & case
    res = db.query_customer_account(customer_id='  usr-07231  ')
    assert res is not None and res['customer_name'] == 'Emily Watson', f"Failed: {res}"

    res = db.query_customer_account(email='  EMILY.W@CONSULTING.CO  ')
    assert res is not None and res['customer_name'] == 'Emily Watson', f"Failed: {res}"

    res = db.query_customer_account(order_id='  ord-81094  ')
    assert res is not None and res['customer_name'] == 'Emily Watson', f"Failed: {res}"

    res = db.query_customer_account(order_id='81094')
    assert res is not None and res['customer_name'] == 'Emily Watson', f"Failed: {res}"
    print("Test 1 Passed: Whitespace and case insensitivity verified.")

    # Test 2: Substring collisions
    assert db.query_customer_account(customer_id='USR-104') is None
    assert db.query_customer_account(order_id='8821') is None
    assert db.query_customer_account(email='alex.mor') is None
    assert db.find_transactions_for_query(customer_id='USR-104') == []
    assert db.find_transactions_for_query(query='USR-104') == []
    print("Test 2 Passed: Substring collisions prevented.")

    # Test 3: SQL Injection / Malformed inputs
    assert db.query_customer_account(customer_id="' OR '1'='1") is None
    assert db.query_transactions(status="'; DROP TABLE transactions; --") == []
    all_tx = db.query_transactions()
    assert len(all_tx) >= 8
    print("Test 3 Passed: Injection safety and empty query parameter handling.")

    # Test 4: Remediation error boundaries
    res_empty = db.remediate_duplicate_charge(order_id='ORD-1', duplicate_txn_id='')
    assert res_empty['ok'] is False
    res_none = db.remediate_duplicate_charge(order_id='ORD-1', duplicate_txn_id=None)
    assert res_none['ok'] is False
    res_fake = db.remediate_duplicate_charge(order_id='ORD-1', duplicate_txn_id='TXN-NONEXISTENT')
    assert res_fake['ok'] is False
    print("Test 4 Passed: Remediation error boundaries verified.")

    # Test 5: Verify no silent fallback to Alex Morgan for unknown customer
    assert db.find_transactions_for_query(customer_id="USR-00000") == []
    assert db.find_transactions_for_query(query="random customer complaint without customer") == []
    inspect_res = db.inspect_account_and_transactions(customer_id="USR-00000")
    assert inspect_res["ok"] is False
    assert inspect_res["customer_id"] == "USR-00000"
    print("Test 5 Passed: No silent fallback to Alex Morgan.")

if __name__ == "__main__":
    run_tests()
    print("ALL AUDITOR ADVERSARIAL STRESS CHECKS PASSED.")
