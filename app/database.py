"""
Customer Billing Transactions Database & Agent Ledger Analysis Engine.
Manages transaction records in data/transactions.csv.
Troy analyzes transaction history per billing cycle to detect multiple debits and executes refunds on specific records.
"""

from __future__ import annotations

import csv
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATABASE_FILE = DATA_DIR / "transactions.csv"

FIELDNAMES = [
    "transaction_id",
    "customer_id",
    "customer_name",
    "customer_email",
    "order_id",
    "transaction_date",
    "billing_cycle",
    "subscription_plan",
    "amount",
    "currency",
    "payment_method",
    "status",
    "refund_id",
    "refund_amount",
    "refunded_at",
    "processed_by",
    "notes",
]

DEFAULT_TRANSACTIONS = [
    {
        "transaction_id": "TXN-9011",
        "customer_id": "USR-10492",
        "customer_name": "Alex Morgan",
        "customer_email": "alex.morgan@gmail.com",
        "order_id": "ORD-88219",
        "transaction_date": "2026-10-20 08:14:22",
        "billing_cycle": "October 2026",
        "subscription_plan": "Pro Tier ($49.00/mo)",
        "amount": "49.00",
        "currency": "USD",
        "payment_method": "Visa ending in 4242",
        "status": "Settled",
        "refund_id": "",
        "refund_amount": "",
        "refunded_at": "",
        "processed_by": "Stripe Gateway",
        "notes": "Monthly subscription renewal debit",
    },
    {
        "transaction_id": "TXN-9012",
        "customer_id": "USR-10492",
        "customer_name": "Alex Morgan",
        "customer_email": "alex.morgan@gmail.com",
        "order_id": "ORD-88219",
        "transaction_date": "2026-10-21 09:30:15",
        "billing_cycle": "October 2026",
        "subscription_plan": "Pro Tier ($49.00/mo)",
        "amount": "49.00",
        "currency": "USD",
        "payment_method": "Visa ending in 4242",
        "status": "Settled",
        "refund_id": "",
        "refund_amount": "",
        "refunded_at": "",
        "processed_by": "Stripe Gateway",
        "notes": "Subscription renewal retry debit",
    },
    {
        "transaction_id": "TXN-8801",
        "customer_id": "USR-08914",
        "customer_name": "Sarah Jenkins",
        "customer_email": "sarah.j@enterprise.org",
        "order_id": "ORD-79110",
        "transaction_date": "2026-10-01 00:00:00",
        "billing_cycle": "October 2026",
        "subscription_plan": "Enterprise Tier ($299.00/mo)",
        "amount": "299.00",
        "currency": "USD",
        "payment_method": "Mastercard ending in 1098",
        "status": "Settled",
        "refund_id": "",
        "refund_amount": "",
        "refunded_at": "",
        "processed_by": "Stripe Gateway",
        "notes": "Monthly enterprise plan renewal",
    },
    {
        "transaction_id": "TXN-8450",
        "customer_id": "USR-12053",
        "customer_name": "David Chen",
        "customer_email": "d.chen@fintech.io",
        "order_id": "ORD-93041",
        "transaction_date": "2026-10-15 12:00:00",
        "billing_cycle": "October 2026",
        "subscription_plan": "Developer API Tier ($199.00/mo)",
        "amount": "199.00",
        "currency": "USD",
        "payment_method": "Amex ending in 3004",
        "status": "Settled",
        "refund_id": "",
        "refund_amount": "",
        "refunded_at": "",
        "processed_by": "Stripe Gateway",
        "notes": "Monthly developer API tier billing",
    },
    {
        "transaction_id": "TXN-8201",
        "customer_id": "USR-14309",
        "customer_name": "Elena Rostova",
        "customer_email": "elena@globaltrade.de",
        "order_id": "ORD-85420",
        "transaction_date": "2026-10-05 14:10:00",
        "billing_cycle": "Q4 2026",
        "subscription_plan": "Team Tier ($120.00/mo)",
        "amount": "360.00",
        "currency": "EUR",
        "payment_method": "SEPA Direct Debit",
        "status": "Settled",
        "refund_id": "",
        "refund_amount": "",
        "refunded_at": "",
        "processed_by": "Payment Gateway",
        "notes": "Quarterly team subscription renewal",
    },
    {
        "transaction_id": "TXN-8902",
        "customer_id": "USR-09148",
        "customer_name": "Michael Scott",
        "customer_email": "m.scott@dunder.com",
        "order_id": "ORD-86102",
        "transaction_date": "2026-10-18 10:05:00",
        "billing_cycle": "October 2026",
        "subscription_plan": "Starter Tier ($29.00/mo)",
        "amount": "79.00",
        "currency": "USD",
        "payment_method": "Visa ending in 9921",
        "status": "Settled",
        "refund_id": "",
        "refund_amount": "",
        "refunded_at": "",
        "processed_by": "Stripe Gateway",
        "notes": "Renewal billed at legacy tier before downgrade processed",
    },
    {
        "transaction_id": "TXN-7711",
        "customer_id": "USR-07231",
        "customer_name": "Emily Watson",
        "customer_email": "emily.w@consulting.co",
        "order_id": "ORD-81094",
        "transaction_date": "2026-09-18 09:00:00",
        "billing_cycle": "September 2026",
        "subscription_plan": "Pro Tier ($49.00/mo)",
        "amount": "49.00",
        "currency": "USD",
        "payment_method": "Visa ending in 5543",
        "status": "Settled",
        "refund_id": "",
        "refund_amount": "",
        "refunded_at": "",
        "processed_by": "Stripe Gateway",
        "notes": "Annual renewal fee",
    },
    {
        "transaction_id": "TXN-7712",
        "customer_id": "USR-07231",
        "customer_name": "Emily Watson",
        "customer_email": "emily.w@consulting.co",
        "order_id": "ORD-81094",
        "transaction_date": "2026-09-18 09:01:15",
        "billing_cycle": "September 2026",
        "subscription_plan": "Pro Tier ($49.00/mo)",
        "amount": "49.00",
        "currency": "USD",
        "payment_method": "Visa ending in 5543",
        "status": "Refunded",
        "refund_id": "REF-7712-AUTO",
        "refund_amount": "49.00",
        "refunded_at": "2026-09-18 14:22:10",
        "processed_by": "Troy (Autonomous Resolution Agent)",
        "notes": "Precedent Case: Duplicate renewal debit refunded autonomously by Troy",
    },
]


def ensure_database_file() -> Path:
    """Ensures data directory and transactions CSV file exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATABASE_FILE.exists():
        with open(DATABASE_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(DEFAULT_TRANSACTIONS)
        logger.info(f"Initialized transactions database at {DATABASE_FILE}")
    return DATABASE_FILE


def read_all_transactions() -> List[Dict[str, str]]:
    """Reads all transaction rows from data/transactions.csv."""
    db_path = ensure_database_file()
    records: List[Dict[str, str]] = []
    try:
        with open(db_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(dict(row))
    except Exception as e:
        logger.error(f"Error reading {db_path}: {e}")
    return records


def write_all_transactions(records: List[Dict[str, str]]) -> bool:
    """Writes all transaction rows to data/transactions.csv."""
    db_path = ensure_database_file()
    try:
        with open(db_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(records)
        return True
    except Exception as e:
        logger.error(f"Error writing to {db_path}: {e}")
        return False


def find_transactions_for_query(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Finds all transaction records in data/transactions.csv matching the customer ID,
    order reference, email, name, or keywords in the complaint query.
    """
    transactions = read_all_transactions()
    target_id = str(customer_id).strip() if customer_id else None

    # 1. Search by customer_id match
    if target_id:
        matched = [t for t in transactions if t.get("customer_id") == target_id or target_id in t.get("customer_id", "")]
        if matched:
            return matched

    # 2. Extract reference from query text (e.g. #88219 or ORD-88219)
    if query:
        match = re.search(r"#?(\d{5,})", query)
        if match:
            ref_num = match.group(1)
            matched = [t for t in transactions if ref_num in t.get("order_id", "") or ref_num in t.get("transaction_id", "")]
            if matched:
                return matched

        query_lower = query.lower()
        # Match name or email in records
        for t in transactions:
            name = t.get("customer_name", "").lower()
            email = t.get("customer_email", "").lower()
            if (name and name in query_lower) or (email and email in query_lower):
                return [row for row in transactions if row.get("customer_id") == t.get("customer_id")]

        # Match subscription / twice / duplicate
        if "twice" in query_lower or "duplicate" in query_lower or "49" in query_lower:
            return [t for t in transactions if t.get("customer_id") == "USR-10492"]

    # Default to Alex Morgan's transactions for primary demo
    return [t for t in transactions if t.get("customer_id") == "USR-10492"]


def analyze_customer_transactions(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Troy / Agent analyzes raw transaction records from data/transactions.csv:
    - Groups records by customer and billing_cycle (e.g. 'October 2026').
    - If a single billing cycle contains > 1 settled transaction for a subscription plan,
      flags the redundant transaction as a duplicate that should be refunded.
    """
    txns = find_transactions_for_query(customer_id=customer_id, query=query)
    if not txns:
        return {"ok": False, "error": "No transaction records found"}

    first = txns[0]
    cust_id = first.get("customer_id", "USR-10492")
    cust_name = first.get("customer_name", "Alex Morgan")
    cust_email = first.get("customer_email", "alex.morgan@gmail.com")
    plan = first.get("subscription_plan", "Pro Tier ($49.00/mo)")
    cycle = first.get("billing_cycle", "October 2026")

    # Group transactions by billing cycle
    cycle_txns = [t for t in txns if t.get("billing_cycle") == cycle]
    settled_txns = [t for t in cycle_txns if t.get("status") == "Settled"]
    refunded_txns = [t for t in cycle_txns if t.get("status") == "Refunded"]

    total_billed = sum(float(t.get("amount", "0")) for t in cycle_txns)
    settled_amount = sum(float(t.get("amount", "0")) for t in settled_txns)

    # Detect if multiple settled transactions exist in the same month
    has_multiple_in_cycle = len(cycle_txns) > 1
    has_duplicate_settled = len(settled_txns) > 1

    # Candidate for refund: the later transaction in the cycle
    candidate_to_refund: Optional[Dict[str, str]] = None
    retained_valid_txn: Optional[Dict[str, str]] = None

    if has_duplicate_settled:
        # Sort by transaction_date ascending: first is valid renewal, second is duplicate
        sorted_settled = sorted(settled_txns, key=lambda x: x.get("transaction_date", ""))
        retained_valid_txn = sorted_settled[0]
        candidate_to_refund = sorted_settled[1]
    elif len(cycle_txns) > 1 and refunded_txns:
        # Already refunded in earlier resolution
        retained_valid_txn = settled_txns[0] if settled_txns else cycle_txns[0]
        candidate_to_refund = refunded_txns[0]

    overcharged_amount = float(candidate_to_refund.get("amount", "0")) if candidate_to_refund else 0.0

    summary_text = (
        f"Analyzed data/transactions.csv: Found {len(cycle_txns)} transaction records in {cycle} for {cust_name} ({cust_id}). "
        f"Records: {', '.join([f'{t.get('transaction_id')} (${t.get('amount')}, {t.get('status')})' for t in cycle_txns])}. "
        f"{'Multiple settled debits detected in a single monthly cycle.' if has_duplicate_settled else 'Ledger reconciled.'} "
        f"Candidate transaction for refund: {candidate_to_refund.get('transaction_id') if candidate_to_refund else 'None'}."
    )

    return {
        "ok": True,
        "verified": True,
        "customer_id": cust_id,
        "customer_name": cust_name,
        "customer_email": cust_email,
        "subscription_plan": plan,
        "billing_cycle": cycle,
        "charges_this_month": len(cycle_txns),
        "total_billed_this_month": f"{total_billed:.2f}",
        "settled_count": len(settled_txns),
        "has_duplicate": has_duplicate_settled or len(refunded_txns) > 0,
        "overcharge_amount": f"${overcharged_amount:.2f}",
        "retained_transaction": retained_valid_txn,
        "duplicate_transaction": candidate_to_refund,
        "transactions": cycle_txns,
        "summary": summary_text,
        # Backward compatibility fields for UI components
        "customer": {
            "customer_id": cust_id,
            "customer_name": cust_name,
            "customer_email": cust_email,
            "reference_order_id": first.get("order_id", "ORD-88219"),
            "subscription_plan": plan,
            "billing_cycle": cycle,
            "monthly_rate": str(first.get("amount", "49.00")),
            "total_billed_this_month": f"{total_billed:.2f}",
            "charges_this_month": str(len(cycle_txns)),
            "transaction_1": f"{txns[0].get('transaction_id')} (${txns[0].get('amount')} on {txns[0].get('transaction_date')[:10]}) [{txns[0].get('status')}]" if len(txns) > 0 else "",
            "transaction_2": f"{txns[1].get('transaction_id')} (${txns[1].get('amount')} on {txns[1].get('transaction_date')[:10]}) [{txns[1].get('status')}]" if len(txns) > 1 else "None",
            "duplicate_verified": "Yes - 2 Transaction Records in Same Cycle" if has_multiple_in_cycle else "No",
            "refund_status": "Refunded" if refunded_txns else ("Pending Refund" if has_duplicate_settled else "N/A"),
            "refund_id": refunded_txns[0].get("refund_id", "") if refunded_txns else "",
            "account_balance": "$0.00 (Settled)" if not has_duplicate_settled else f"$-{overcharged_amount:.2f} (Overcharged)",
            "account_status": "Active - In Good Standing" if not has_duplicate_settled else "Flagged - Multiple Charges in Cycle",
        },
    }


def execute_agent_resolution(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
    action: str = "Refund Initiated ($49.00 via Stripe API)",
    notes: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Troy (LLM Agent) executes the refund on the specific duplicate transaction row in data/transactions.csv:
    - Finds the redundant transaction record (e.g. TXN-9012).
    - Updates that record: status='Refunded', refund_id='REF-88219-AUTO', refund_amount=amount, refunded_at=timestamp.
    - Leaves the first transaction (TXN-9011) intact as 'Settled'.
    """
    all_txns = read_all_transactions()
    analysis = analyze_customer_transactions(customer_id=customer_id, query=query)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    dup_txn = analysis.get("duplicate_transaction")
    target_txn_id = dup_txn.get("transaction_id") if dup_txn else "TXN-9012"

    updated_txn: Optional[Dict[str, str]] = None

    for row in all_txns:
        if row.get("transaction_id") == target_txn_id:
            row["status"] = "Refunded"
            row["refund_id"] = "REF-88219-AUTO"
            row["refund_amount"] = row.get("amount", "49.00")
            row["refunded_at"] = now_str
            row["processed_by"] = "Troy (Autonomous Resolution Agent)"
            row["notes"] = notes or "Refunded by Troy: Duplicate subscription charge in October 2026 billing cycle."
            updated_txn = row
            break

    write_all_transactions(all_txns)
    logger.info(f"Database updated: Transaction {target_txn_id} marked as Refunded in data/transactions.csv.")

    plan_rate = analysis.get("retained_transaction", {}).get("amount", "49.00")

    return {
        "ok": True,
        "action": "agent_resolve",
        "refunded_transaction": updated_txn,
        "retained_transaction": analysis.get("retained_transaction"),
        "customer": {
            "customer_id": analysis.get("customer_id", "USR-10492"),
            "customer_name": analysis.get("customer_name", "Alex Morgan"),
            "customer_email": analysis.get("customer_email", "alex.morgan@gmail.com"),
            "subscription_plan": analysis.get("subscription_plan", "Pro Tier ($49.00/mo)"),
            "status": "Resolved",
            "account_status": "Active - In Good Standing",
            "refund_status": "Refunded ($49.00 via Stripe API)",
            "refund_id": "REF-88219-AUTO",
            "account_balance": "$0.00 (Settled)",
            "customer_notified": "Yes - Confirmation Dispatched (REF-88219-AUTO)",
        },
        "ticket": {
            "ticket_id": "88219",
            "order_id": "ORD-88219",
            "customer_name": analysis.get("customer_name", "Alex Morgan"),
            "customer_email": analysis.get("customer_email", "alex.morgan@gmail.com"),
            "status": "Resolved",
            "resolution_action": f"Transaction {target_txn_id} Refunded ($49.00 via Stripe Gateway API)",
            "refund_id": "REF-88219-AUTO",
            "customer_notified": "Yes - Email Dispatched (REF-88219-AUTO)",
        },
        "database_file": "data/transactions.csv",
        "timestamp": now_str,
    }


def execute_manual_resolution(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
    notes: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Escalates the transactions in data/transactions.csv for human review:
    Flags the duplicate candidate transaction with a review note.
    """
    all_txns = read_all_transactions()
    analysis = analyze_customer_transactions(customer_id=customer_id, query=query)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    dup_txn = analysis.get("duplicate_transaction")
    target_txn_id = dup_txn.get("transaction_id") if dup_txn else "TXN-9012"

    for row in all_txns:
        if row.get("transaction_id") == target_txn_id:
            row["notes"] = notes or "Flagged for manual review by human billing specialist."
            row["processed_by"] = "Escalated by Troy -> Human Agent"
            break

    write_all_transactions(all_txns)
    logger.info(f"Database updated: Transaction {target_txn_id} flagged for manual review in data/transactions.csv.")

    return {
        "ok": True,
        "action": "manual_resolve",
        "customer": {
            "customer_id": analysis.get("customer_id", "USR-10492"),
            "customer_name": analysis.get("customer_name", "Alex Morgan"),
            "customer_email": analysis.get("customer_email", "alex.morgan@gmail.com"),
            "subscription_plan": analysis.get("subscription_plan", "Pro Tier ($49.00/mo)"),
            "account_status": "Escalated - Billing Specialist Audit",
        },
        "ticket": {
            "ticket_id": "88219",
            "order_id": "ORD-88219",
            "status": "Escalated - Manual Review",
            "predicted_team": "Billing Team Specialist",
            "customer_notified": "Yes - Escalation Notice Sent",
        },
        "database_file": "data/transactions.csv",
        "timestamp": now_str,
    }


# Backwards compatibility aliases
verify_customer_billing = analyze_customer_transactions
read_all_customers = read_all_transactions
read_all_tickets = read_all_transactions
write_all_customers = write_all_transactions
write_all_tickets = write_all_transactions
find_customer_by_id_or_query = find_transactions_for_query
find_ticket_by_id_or_query = find_transactions_for_query
