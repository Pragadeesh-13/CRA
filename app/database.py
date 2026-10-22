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
CUSTOMER_ACCOUNTS_FILE = DATA_DIR / "customer_accounts.csv"

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

CUSTOMER_ACCOUNT_FIELDNAMES = [
    "customer_id",
    "customer_name",
    "customer_email",
    "reference_order_id",
    "subscription_plan",
    "billing_cycle",
    "monthly_rate",
    "total_billed_this_month",
    "charges_this_month",
    "transaction_1",
    "transaction_2",
    "duplicate_verified",
    "refund_status",
    "refund_id",
    "account_balance",
    "account_status",
    "last_verified_by",
    "updated_by",
    "last_updated",
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


def write_all_customer_accounts(records: List[Dict[str, str]]) -> bool:
    """Writes all rows to data/customer_accounts.csv."""
    try:
        with open(CUSTOMER_ACCOUNTS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CUSTOMER_ACCOUNT_FIELDNAMES)
            writer.writeheader()
            writer.writerows(records)
        return True
    except Exception as e:
        logger.error(f"Error writing to {CUSTOMER_ACCOUNTS_FILE}: {e}")
        return False


def find_customer_account_by_query(
    query: Optional[str] = None,
    customer_id: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """
    Finds a customer account from data/customer_accounts.csv by customer_id,
    order reference (e.g. 81094, ORD-81094), customer name, or email.
    """
    accounts = read_all_customer_accounts()
    if not accounts:
        return None

    target_id = str(customer_id).strip().upper() if customer_id else None
    if target_id:
        for acc in accounts:
            acc_id = acc.get("customer_id", "").upper()
            if target_id == acc_id or target_id in acc_id:
                return acc

    if query:
        q_clean = query.strip()
        q_lower = q_clean.lower()

        # 1. Check for Order ID / reference numbers like 81094, 88219, ORD-81094
        order_match = re.search(r"(?:ORD-)?(\d{4,6})", q_clean, re.IGNORECASE)
        if order_match:
            ref_num = order_match.group(1)
            for acc in accounts:
                ref_order = acc.get("reference_order_id", "")
                if ref_num in ref_order or ref_num in acc.get("transaction_1", "") or ref_num in acc.get("transaction_2", "") or ref_num in acc.get("notes", ""):
                    return acc

        # 2. Check for customer_id mention in query
        for acc in accounts:
            acc_id = acc.get("customer_id", "").lower()
            if acc_id and acc_id in q_lower:
                return acc

        # 3. Check for customer name tokens
        for acc in accounts:
            name = acc.get("customer_name", "").lower()
            if not name:
                continue
            if name in q_lower:
                return acc
            name_parts = [p for p in name.split() if len(p) >= 3]
            for part in name_parts:
                if re.search(rf"\b{re.escape(part)}\b", q_lower):
                    return acc

        # 4. Check for customer email in query
        for acc in accounts:
            email = acc.get("customer_email", "").lower()
            if email and email in q_lower:
                return acc

    return None


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

    # First match against customer_accounts.csv to resolve customer entity
    matched_acc = find_customer_account_by_query(query=query, customer_id=target_id)
    if matched_acc:
        acc_cust_id = matched_acc.get("customer_id")
        acc_order_id = matched_acc.get("reference_order_id")
        matched = [
            t for t in transactions
            if (acc_cust_id and t.get("customer_id") == acc_cust_id)
            or (acc_order_id and t.get("order_id") == acc_order_id)
        ]
        if matched:
            return matched

    # Fallback to direct customer_id match in transactions
    if target_id:
        matched = [t for t in transactions if t.get("customer_id") == target_id or target_id in t.get("customer_id", "")]
        if matched:
            return matched

    # Fallback to direct reference / order extraction from query
    if query:
        order_match = re.search(r"(?:ORD-)?(\d{4,6})", query, re.IGNORECASE)
        if order_match:
            ref_num = order_match.group(1)
            matched = [t for t in transactions if ref_num in t.get("order_id", "") or ref_num in t.get("transaction_id", "")]
            if matched:
                return matched

        query_lower = query.lower()
        for t in transactions:
            name = t.get("customer_name", "").lower()
            email = t.get("customer_email", "").lower()
            if (name and name in query_lower) or (email and email in query_lower):
                return [row for row in transactions if row.get("customer_id") == t.get("customer_id")]

        if "twice" in query_lower or "duplicate" in query_lower or "49" in query_lower:
            return [t for t in transactions if t.get("customer_id") == "USR-10492"]

    return [t for t in transactions if t.get("customer_id") == "USR-10492"]


def analyze_customer_transactions(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Troy / Agent analyzes raw transaction records from data/transactions.csv:
    - Groups records by customer and billing_cycle (e.g. 'October 2026', 'September 2026').
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
    order_ref = first.get("order_id", "ORD-88219")

    # Reconcile customer details with customer_accounts.csv if available
    acc = find_customer_account_by_query(query=query, customer_id=cust_id)
    if acc:
        cust_id = acc.get("customer_id") or cust_id
        cust_name = acc.get("customer_name") or cust_name
        cust_email = acc.get("customer_email") or cust_email
        plan = acc.get("subscription_plan") or plan
        order_ref = acc.get("reference_order_id") or order_ref

    # Group transactions by billing cycle
    cycle_txns = [t for t in txns if t.get("billing_cycle") == cycle]
    if not cycle_txns:
        cycle_txns = txns
        cycle = cycle_txns[0].get("billing_cycle", cycle)

    settled_txns = [t for t in cycle_txns if t.get("status") == "Settled"]
    refunded_txns = [t for t in cycle_txns if t.get("status") == "Refunded"]

    total_billed = sum(float(t.get("amount", "0")) for t in cycle_txns)
    settled_amount = sum(float(t.get("amount", "0")) for t in settled_txns)

    # Detect if multiple settled transactions exist in the same month
    has_multiple_in_cycle = len(cycle_txns) > 1
    has_duplicate_settled = len(settled_txns) > 1
    is_already_refunded = len(refunded_txns) > 0 and len(settled_txns) <= 1

    # Candidate for refund: the later transaction in the cycle
    candidate_to_refund: Optional[Dict[str, str]] = None
    retained_valid_txn: Optional[Dict[str, str]] = None

    if has_duplicate_settled:
        # Sort by transaction_date ascending: first is valid renewal, second is duplicate
        sorted_settled = sorted(settled_txns, key=lambda x: x.get("transaction_date", ""))
        retained_valid_txn = sorted_settled[0]
        candidate_to_refund = sorted_settled[1]
    elif is_already_refunded:
        retained_valid_txn = settled_txns[0] if settled_txns else cycle_txns[0]
        candidate_to_refund = refunded_txns[0]
    else:
        retained_valid_txn = settled_txns[0] if settled_txns else (cycle_txns[0] if cycle_txns else None)
        candidate_to_refund = None

    overcharged_amount = float(candidate_to_refund.get("amount", "49.00")) if candidate_to_refund else 0.0

    clean_order_num = order_ref.replace("ORD-", "") if order_ref else "88219"
    suggested_ref_id = f"REF-{clean_order_num}-AUTO"
    ref_id = (refunded_txns[0].get("refund_id") if refunded_txns and refunded_txns[0].get("refund_id") else suggested_ref_id)
    ref_date = refunded_txns[0].get("refunded_at") or "" if refunded_txns else ""

    dup_id = candidate_to_refund.get("transaction_id", "TXN-9012") if candidate_to_refund else "TXN-9012"
    ret_id = retained_valid_txn.get("transaction_id", "TXN-9011") if retained_valid_txn else "TXN-9011"
    plan_rate = str(first.get("amount", "49.00"))

    if is_already_refunded:
        summary_text = (
            f"Analyzed data/transactions.csv: Found {len(cycle_txns)} transaction records in {cycle} for {cust_name} ({cust_id}). "
            f"Records: {', '.join([f'{t.get('transaction_id')} (${t.get('amount')}, {t.get('status')})' for t in cycle_txns])}. "
            f"Accidental duplicate debit {dup_id} was ALREADY REFUNDED on {ref_date} (Refund ID: {ref_id}). "
            f"Current ledger has 1 valid active renewal ({ret_id}: ${retained_valid_txn.get('amount', plan_rate)}, Settled). "
            f"Account balance is $0.00. No further refund required."
        )
    elif has_duplicate_settled:
        summary_text = (
            f"Analyzed data/transactions.csv: Found {len(cycle_txns)} transaction records in {cycle} for {cust_name} ({cust_id}). "
            f"Records: {', '.join([f'{t.get('transaction_id')} (${t.get('amount')}, {t.get('status')})' for t in cycle_txns])}. "
            f"Multiple settled debits detected in a single monthly cycle. Candidate transaction for refund: {dup_id}."
        )
    else:
        summary_text = (
            f"Analyzed data/transactions.csv: Found {len(cycle_txns)} transaction records in {cycle} for {cust_name} ({cust_id}). "
            f"Records: {', '.join([f'{t.get('transaction_id')} (${t.get('amount')}, {t.get('status')})' for t in cycle_txns])}. "
            f"Ledger reconciled. No duplicate charges found."
        )

    # Dynamic progressive steps
    steps = [
        {"text": "Accessing internal customer transaction ledger (data/transactions.csv)...", "delay": 650},
        {"text": f"Locating account records for {cust_name} ({cust_id})...", "delay": 700},
        {"text": f"Ledger Audit: Duplicate {dup_id} is {'ALREADY marked as Refunded under ' + ref_id if is_already_refunded else f'verified as redundant second debit in {cycle}'}...", "delay": 750},
        {"text": f"{'Verifying account balance: Account balance is $0.00 with 1 active renewal ' + ret_id if is_already_refunded else f'Authorizing ${plan_rate} refund reversal via Stripe Gateway API ({ref_id})'}...", "delay": 750},
        {"text": f"{'Duplicate payout prevention active: Secondary refund blocked' if is_already_refunded else f'Updating data/transactions.csv: {dup_id} status set to Refunded'}...", "delay": 700},
        {"text": f"Dispatching refund confirmation receipt ({ref_id}) to {cust_email}...", "delay": 600},
    ]

    return {
        "ok": True,
        "verified": True,
        "category": "Refunds",
        "customer_id": cust_id,
        "customer_name": cust_name,
        "customer_email": cust_email,
        "subscription_plan": plan,
        "billing_cycle": cycle,
        "order_id": order_ref,
        "charges_this_month": len(cycle_txns),
        "total_billed_this_month": f"{total_billed:.2f}",
        "settled_count": len(settled_txns),
        "has_duplicate": has_duplicate_settled or len(refunded_txns) > 0,
        "has_pending_duplicate": has_duplicate_settled,
        "is_already_refunded": is_already_refunded,
        "already_refunded": is_already_refunded,
        "refund_id": ref_id,
        "refunded_at": ref_date,
        "overcharge_amount": f"${overcharged_amount:.2f}",
        "retained_transaction": retained_valid_txn,
        "duplicate_transaction": candidate_to_refund,
        "refunded_transaction": refunded_txns[0] if refunded_txns else None,
        "transactions": cycle_txns,
        "summary": summary_text,
        "steps": steps,
        # Backward compatibility fields for UI components
        "customer": {
            "customer_id": cust_id,
            "customer_name": cust_name,
            "customer_email": cust_email,
            "reference_order_id": order_ref,
            "subscription_plan": plan,
            "billing_cycle": cycle,
            "monthly_rate": plan_rate,
            "total_billed_this_month": f"{total_billed:.2f}",
            "charges_this_month": str(len(cycle_txns)),
            "transaction_1": f"{retained_valid_txn.get('transaction_id')} (${retained_valid_txn.get('amount')} on {retained_valid_txn.get('transaction_date', '')[:10]}) [{retained_valid_txn.get('status')}]" if retained_valid_txn else "",
            "transaction_2": f"{candidate_to_refund.get('transaction_id')} (${candidate_to_refund.get('amount')} on {candidate_to_refund.get('transaction_date', '')[:10]}) [{candidate_to_refund.get('status')}]" if candidate_to_refund else "None",
            "duplicate_verified": "Yes - Accidental Double Charge Detected" if has_duplicate_settled else ("Yes - Previously Refunded" if is_already_refunded else "No"),
            "is_already_refunded": is_already_refunded,
            "refund_status": "Already Refunded" if is_already_refunded else ("Pending Refund" if has_duplicate_settled else "N/A"),
            "refund_id": ref_id if is_already_refunded else "",
            "refunded_at": ref_date,
            "account_balance": "$0.00 (Settled)" if not has_duplicate_settled else f"$-{overcharged_amount:.2f} (Overcharged)",
            "account_status": "Active - In Good Standing" if not has_duplicate_settled else "Flagged - Accidental Double Charge",
        },
    }


def get_category_complaint_context(category: str, query: str = "") -> Dict[str, Any]:
    """
    Returns structured domain data and resolution metadata for all 11 CRA complaint categories.
    """
    cat = (category or "").strip()
    q = (query or "").strip().lower()

    # Category 1: Refunds
    # Triggered if explicitly in Refunds category or explicitly requesting duplicate charge/refund
    is_explicit_refund = (
        "refund" in q
        or "88219" in q
        or "81094" in q
        or "double billed" in q
        or "billed twice" in q
        or "charged twice" in q
        or "double charge" in q
        or "duplicate charge" in q
        or "duplicate debit" in q
        or "double debit" in q
        or "duplicate bill" in q
        or "charged again" in q
        or ("charged" in q and ("twice" in q or "second" in q or "again" in q or "49" in q or "double" in q))
        or ("duplicate" in q and ("charge" in q or "debit" in q or "payment" in q or "transaction" in q or "billing" in q))
    )
    is_refund_query = cat == "Refunds" or is_explicit_refund

    if is_refund_query:
        txn_analysis = analyze_customer_transactions(query=query)
        is_already_refunded = txn_analysis.get("is_already_refunded", False)
        ref_id = txn_analysis.get("refund_id") or "REF-88219-AUTO"
        return {
            "category": "Refunds",
            "is_refund": True,
            "system_label": "Customer Transaction Ledger (data/transactions.csv)",
            "database_file": "data/transactions.csv",
            "status_pill": f"✓ Refund Already Processed ({ref_id})" if is_already_refunded else "2 Charges in Single Cycle Found",
            "action_btn_text": "Verify & Reconfirm Refund" if is_already_refunded else "Let agent resolve it",
            "customer_id": txn_analysis.get("customer_id", "USR-10492"),
            "customer_name": txn_analysis.get("customer_name", "Alex Morgan"),
            "customer_email": txn_analysis.get("customer_email", "alex.morgan@gmail.com"),
            "subscription_plan": txn_analysis.get("subscription_plan", "Pro Tier ($49.00/mo)"),
            "reference_id": ref_id,
            "is_already_refunded": is_already_refunded,
            "refund_id": ref_id,
            "refunded_at": txn_analysis.get("refunded_at", "2026-10-21 14:49:37"),
            "system_details": txn_analysis.get("summary", ""),
            "steps": txn_analysis.get("steps", []),
            "txn_analysis": txn_analysis,
        }

    # Category 2: Authentication & Access
    elif cat == "Authentication & Access" or "2fa" in q or "two-factor" in q or "login" in q or "locked" in q or "79110" in q:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-08914"
        c_name = (acc.get("customer_name") if acc else None) or "Sarah Jenkins"
        c_email = (acc.get("customer_email") if acc else None) or "sarah.j@enterprise.org"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Enterprise Tier ($299.00/mo)"
        return {
            "category": "Authentication & Access",
            "is_refund": False,
            "system_label": "Identity & Access Management (Okta / IAM Gateway)",
            "database_file": "Identity Directory (IAM / Okta)",
            "status_pill": "🔒 Account Locked: 2FA SMS Gateway Failure",
            "action_btn_text": "Let agent restore access & reset 2FA",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "AUTH-9921",
            "system_details": "Account locked after failed carrier SMS OTP delivery on payment portal. Identity verified via enterprise SSO certificate.",
            "steps": [
                {"text": "Accessing Identity & Access Management directory (IAM / Okta)...", "delay": 650},
                {"text": f"Verifying corporate identity credentials for {c_name} ({c_id})...", "delay": 700},
                {"text": "Diagnosing authentication log: Carrier SMS delivery timeout on Payment Gateway...", "delay": 750},
                {"text": "Generating temporary secure 2FA emergency bypass token (AUTH-9921)...", "delay": 750},
                {"text": "Unlocking account status in IAM directory & resetting failed attempt counter...", "delay": 700},
                {"text": f"Dispatching secure biometric re-enrollment link to {c_email}...", "delay": 600},
            ],
        }

    # Category 3: Billing
    elif cat == "Billing" or "vat" in q or "invoice" in q or "tax id" in q or "85420" in q:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-14309"
        c_name = (acc.get("customer_name") if acc else None) or "Elena Rostova"
        c_email = (acc.get("customer_email") if acc else None) or "elena@globaltrade.de"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Team Tier ($120.00/mo)"
        return {
            "category": "Billing",
            "is_refund": False,
            "system_label": "Stripe Invoicing & Global Tax Ledger",
            "database_file": "Invoicing & Regional Tax Ledger",
            "status_pill": "📋 Pending VAT & Invoicing Documentation",
            "action_btn_text": "Let agent generate & dispatch VAT invoice",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "INV-85420-VAT",
            "system_details": "Customer requested formal German VAT ID addition and stamped tax invoice for Q4 2026 accounting records.",
            "steps": [
                {"text": "Accessing billing ledger & tax invoice engine...", "delay": 650},
                {"text": f"Locating account records for {c_name} ({c_id})...", "delay": 700},
                {"text": "Validating VAT registration ID via European VIES tax service...", "delay": 750},
                {"text": "Generating official stamped VAT invoice (INV-85420-VAT) with tax breakdown...", "delay": 750},
                {"text": "Updating billing profile in customer organization profile...", "delay": 700},
                {"text": f"Dispatching signed tax receipt and PDF invoice to {c_email}...", "delay": 600},
            ],
        }

    # Category 4: Bugs & Errors
    elif cat == "Bugs & Errors" or "crash" in q or "500 error" in q or "bug" in q:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-12053"
        c_name = (acc.get("customer_name") if acc else None) or "David Chen"
        c_email = (acc.get("customer_email") if acc else None) or "d.chen@fintech.io"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Developer API Tier ($199.00/mo)"
        return {
            "category": "Bugs & Errors",
            "is_refund": False,
            "system_label": "Sentry Error Telemetry & Jira Engineering Board",
            "database_file": "Engineering Defect Telemetry (Sentry & Jira)",
            "status_pill": "🐛 HTTP 500 Unhandled Exception Logged",
            "action_btn_text": "Let agent dispatch bug report to Engineering",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "BUG-ENG-4401",
            "system_details": "Correlated recurring HTTP 500 crash with memory stream buffer exception in client release v2.4.1.",
            "steps": [
                {"text": "Accessing Sentry error telemetry & crash reporting logs...", "delay": 650},
                {"text": "Correlating stack trace with client release v2.4.1 export handler...", "delay": 700},
                {"text": "Formulating Engineering defect ticket: BUG-ENG-4401 (Priority: High)...", "delay": 750},
                {"text": "Synthesizing temporary client-side workaround (direct CSV export stream)...", "delay": 750},
                {"text": "Logging ticket in Engineering sprint queue with full diagnostic trace...", "delay": 700},
                {"text": f"Dispatching workaround instructions & ticket tracking to {c_email}...", "delay": 600},
            ],
        }

    # Category 5: Data Sync
    elif cat == "Data Sync" or "sync" in q or "synchronization" in q:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-10492"
        c_name = (acc.get("customer_name") if acc else None) or "Alex Morgan"
        c_email = (acc.get("customer_email") if acc else None) or "alex.morgan@gmail.com"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Pro Tier ($49.00/mo)"
        return {
            "category": "Data Sync",
            "is_refund": False,
            "system_label": "Cloud Real-Time Sync & State Reconciler",
            "database_file": "Cloud Real-Time Sync & State Reconciler",
            "status_pill": "🔄 Desynchronized: Offline Queue Conflict",
            "action_btn_text": "Let agent trigger cloud resync & reconcile state",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "SYNC-RECON-2201",
            "system_details": "Timestamp divergence detected between mobile client offline queue and cloud PostgreSQL cluster.",
            "steps": [
                {"text": "Connecting to cloud real-time sync coordinator...", "delay": 650},
                {"text": f"Auditing device-to-cloud replication queue for {c_id}...", "delay": 700},
                {"text": "Detecting timestamp conflict on pending mobile client records...", "delay": 750},
                {"text": "Executing three-way merge resolution algorithm (SYNC-RECON-2201)...", "delay": 750},
                {"text": "Forcing cloud ledger cache invalidation and clean state push...", "delay": 700},
                {"text": "Notifying user client of successful resynchronization...", "delay": 600},
            ],
        }

    # Category 6: Integrations & API
    elif cat == "Integrations & API" or "api" in q or "webhook" in q or "429" in q or "93041" in q:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-12053"
        c_name = (acc.get("customer_name") if acc else None) or "David Chen"
        c_email = (acc.get("customer_email") if acc else None) or "d.chen@fintech.io"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Developer API Tier ($199.00/mo)"
        return {
            "category": "Integrations & API",
            "is_refund": False,
            "system_label": "API Gateway Telemetry & Webhook Dispatcher",
            "database_file": "API Gateway Telemetry & Webhook Dispatcher",
            "status_pill": "⚡ Webhook Timeout (HTTP 504) / Rate Limit 429",
            "action_btn_text": "Let agent dispatch API guide & flush webhook queue",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "API-HOOK-7703",
            "system_details": "12 webhook payloads dead-lettered due to endpoint latency; burst rate limit threshold exceeded.",
            "steps": [
                {"text": "Querying API Gateway error telemetry & access logs...", "delay": 650},
                {"text": f"Locating developer account for {c_name} ({c_id})...", "delay": 700},
                {"text": "Analyzing dead-letter queue: 12 events delayed by 504 endpoint timeout...", "delay": 750},
                {"text": "Resetting burst rate-limit token bucket for customer API key...", "delay": 750},
                {"text": "Initiating automated webhook replay sequence (API-HOOK-7703)...", "delay": 700},
                {"text": f"Dispatching developer documentation & OpenAPI specification link to {c_email}...", "delay": 600},
            ],
        }

    # Category 7: Onboarding & How-To
    elif cat == "Onboarding & How-To" or "how to" in q or "get started" in q or "invite" in q:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-09148"
        c_name = (acc.get("customer_name") if acc else None) or "Michael Scott"
        c_email = (acc.get("customer_email") if acc else None) or "m.scott@dunder.com"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Starter Tier ($29.00/mo)"
        return {
            "category": "Onboarding & How-To",
            "is_refund": False,
            "system_label": "Customer Success Knowledge Base & Permissions Engine",
            "database_file": "Customer Success Knowledge Base & CRM",
            "status_pill": "📚 Workspace Setup & Role Delegation Assistance",
            "action_btn_text": "Let agent send setup guide & configure workspace",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "GUIDE-ONB-1102",
            "system_details": "New workspace administrator requesting guidance on inviting team members and setting role-based permissions.",
            "steps": [
                {"text": "Accessing Customer Success Knowledge Base & training registry...", "delay": 650},
                {"text": f"Locating workspace records for {c_id} ({c_name})...", "delay": 700},
                {"text": "Synthesizing step-by-step role delegation & member onboarding workflow...", "delay": 750},
                {"text": "Generating automated member invitation link and role permission template...", "delay": 750},
                {"text": "Recording onboarding milestone completion in customer CRM...", "delay": 700},
                {"text": f"Dispatching interactive guide (GUIDE-ONB-1102) to {c_email}...", "delay": 600},
            ],
        }

    # Category 8: Performance
    elif cat == "Performance" or "slow" in q or "latency" in q or "load" in q:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-08914"
        c_name = (acc.get("customer_name") if acc else None) or "Sarah Jenkins"
        c_email = (acc.get("customer_email") if acc else None) or "sarah.j@enterprise.org"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Enterprise Tier ($299.00/mo)"
        return {
            "category": "Performance",
            "is_refund": False,
            "system_label": "Datadog APM & Database Query Telemetry",
            "database_file": "Datadog APM & Database Query Telemetry",
            "status_pill": "⚡ High Latency Bottleneck: 42.8s Search Duration",
            "action_btn_text": "Let agent flush query cache & run optimization",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "PERF-OPT-3301",
            "system_details": "Full-text search queries exceeding SLA threshold due to fragmented elastic index cache.",
            "steps": [
                {"text": "Connecting to Datadog APM & search cluster telemetry...", "delay": 650},
                {"text": "Profiling query latency: P99 response time degraded to 42.8s...", "delay": 700},
                {"text": "Identifying bottleneck: Stale search index cache and worker thread saturation...", "delay": 750},
                {"text": "Executing automated cache invalidation and query index defragmentation (PERF-OPT-3301)...", "delay": 750},
                {"text": "Verifying query latency normalized to < 450ms baseline...", "delay": 700},
                {"text": f"Dispatching performance resolution report to {c_email}...", "delay": 600},
            ],
        }

    # Category 9: Security
    elif cat == "Security" or "unauthorized" in q or "compromised" in q or "ip allowlist" in q:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-08914"
        c_name = (acc.get("customer_name") if acc else None) or "Sarah Jenkins"
        c_email = (acc.get("customer_email") if acc else None) or "sarah.j@enterprise.org"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Enterprise Tier ($299.00/mo)"
        return {
            "category": "Security",
            "is_refund": False,
            "system_label": "Cloudflare WAF & Security Operations Center (SOC)",
            "database_file": "Cloudflare WAF & Security Operations Center (SOC)",
            "status_pill": "🛡️ Anomaly: Unauthorized Access / Untrusted IP",
            "action_btn_text": "Let agent terminate sessions & secure account",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "SEC-REVOKE-8812",
            "system_details": "Anomalous authorization token detected from unverified IP range. Threat level assessed as elevated.",
            "steps": [
                {"text": "Connecting to Security Operations Center (SOC) telemetry...", "delay": 650},
                {"text": f"Auditing active user sessions for {c_name} ({c_id})...", "delay": 700},
                {"text": "Confirming geolocation anomaly: Untrusted login session from unfamiliar IP...", "delay": 750},
                {"text": "Immediately revoking all active JWT bearer tokens (SEC-REVOKE-8812)...", "delay": 750},
                {"text": "Enforcing mandatory multi-factor biometric challenge on next sign-in...", "delay": 700},
                {"text": f"Dispatching urgent security incident notice & confirmation to {c_email}...", "delay": 600},
            ],
        }

    # Category 10: Subscription
    elif cat == "Subscription" or "downgrade" in q or "renewal" in q or "cancel" in q or "86102" in q:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-09148"
        c_name = (acc.get("customer_name") if acc else None) or "Michael Scott"
        c_email = (acc.get("customer_email") if acc else None) or "m.scott@dunder.com"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Starter Tier ($29.00/mo)"
        return {
            "category": "Subscription",
            "is_refund": False,
            "system_label": "Stripe Subscription Billing & Entitlements Engine",
            "database_file": "Stripe Subscription & Entitlements Engine",
            "status_pill": "📋 Pending Downgrade Adjustment ($50.00 Credit Due)",
            "action_btn_text": "Let agent apply downgrade & credit prorated balance",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "SUB-ADJ-6601",
            "system_details": "Customer requested downgrade to Starter Tier prior to renewal; legacy renewal was billed at $79.00. $50.00 credit due.",
            "steps": [
                {"text": "Connecting to Stripe Subscription & Entitlements Engine...", "delay": 650},
                {"text": f"Locating subscription profile for {c_name} ({c_id})...", "delay": 700},
                {"text": "Auditing billing schedule: Legacy $79 renewal confirmed; downgrade to Starter ($29) requested...", "delay": 750},
                {"text": "Calculating prorated delta: $50.00 credit approved under SUB-ADJ-6601...", "delay": 750},
                {"text": "Applying $50.00 credit to customer ledger & setting renewal tier to Starter ($29.00)...", "delay": 700},
                {"text": f"Dispatching updated subscription schedule and receipt to {c_email}...", "delay": 600},
            ],
        }

    # Category 11: Account Management (default fallback)
    else:
        acc = find_customer_account_by_query(query=query)
        c_id = (acc.get("customer_id") if acc else None) or "USR-14309"
        c_name = (acc.get("customer_name") if acc else None) or "Elena Rostova"
        c_email = (acc.get("customer_email") if acc else None) or "elena@globaltrade.de"
        c_plan = (acc.get("subscription_plan") if acc else None) or "Team Tier ($120.00/mo)"
        return {
            "category": "Account Management",
            "is_refund": False,
            "system_label": "Enterprise Workspace Directory & IAM",
            "database_file": "Enterprise Workspace Directory & IAM",
            "status_pill": "👤 Entity Update & Account Consolidation Request",
            "action_btn_text": "Let agent update account records & confirm",
            "customer_id": c_id,
            "customer_name": c_name,
            "customer_email": c_email,
            "subscription_plan": c_plan,
            "reference_id": "ACC-MGT-5501",
            "system_details": "Request to merge subsidiary account into primary organization and update legal contact information.",
            "steps": [
                {"text": "Accessing Enterprise Workspace Directory (IAM / SSO)...", "delay": 650},
                {"text": f"Locating organization profile for {c_name} ({c_id})...", "delay": 700},
                {"text": "Verifying organizational signing authority and corporate domain records...", "delay": 750},
                {"text": "Processing account consolidation and updating primary contact credentials (ACC-MGT-5501)...", "delay": 750},
                {"text": "Updating customer master account record in Enterprise Directory...", "delay": 700},
                {"text": f"Dispatching consolidation confirmation notice to {c_email}...", "delay": 600},
            ],
        }


def execute_agent_resolution(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
    action: str = "Refund Initiated ($49.00 via Stripe API)",
    notes: Optional[str] = None,
    category: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Troy (LLM Agent) executes the resolution corresponding to the complaint's category:
    - For Refunds: Audits and updates duplicate transactions in data/transactions.csv.
    - For Authentication & Access: Resets 2FA lockout and issues bypass token in customer records.
    - For Security: Terminates active sessions and updates firewall allowlists.
    - For Bugs & Errors: Logs defect in engineering queue with reproduction trace.
    - For other categories: Executes the domain action and records audit logs.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    context = get_category_complaint_context(category=category or "", query=query or "")

    # If Refunds or duplicate charge issue
    if context.get("is_refund", False):
        all_txns = read_all_transactions()
        analysis = analyze_customer_transactions(customer_id=customer_id, query=query)
        dup_txn = analysis.get("duplicate_transaction")
        target_txn_id = dup_txn.get("transaction_id") if dup_txn else "TXN-9012"
        is_already_ref = analysis.get("is_already_refunded", False)
        existing_ref_id = dup_txn.get("refund_id") or "REF-88219-AUTO" if dup_txn else "REF-88219-AUTO"
        existing_ref_date = dup_txn.get("refunded_at") or now_str if dup_txn else now_str

        updated_txn: Optional[Dict[str, str]] = None

        if is_already_ref:
            for row in all_txns:
                if row.get("transaction_id") == target_txn_id:
                    updated_txn = row
                    break
            logger.info(f"Database verify: Transaction {target_txn_id} was ALREADY refunded under {existing_ref_id}.")
            res_action = f"Transaction {target_txn_id} was already refunded under {existing_ref_id} on {existing_ref_date}. Reconfirmed without duplicate payout."
        else:
            ref_code = analysis.get("refund_id") or "REF-88219-AUTO"
            for row in all_txns:
                if row.get("transaction_id") == target_txn_id:
                    row["status"] = "Refunded"
                    row["refund_id"] = ref_code
                    row["refund_amount"] = row.get("amount", "49.00")
                    row["refunded_at"] = now_str
                    row["processed_by"] = "Troy (Autonomous Resolution Agent)"
                    row["notes"] = notes or f"Refunded by Troy: Duplicate subscription charge in {analysis.get('billing_cycle', 'cycle')}."
                    updated_txn = row
                    break
            write_all_transactions(all_txns)
            logger.info(f"Database updated: Transaction {target_txn_id} marked as Refunded in data/transactions.csv.")
            res_action = f"Transaction {target_txn_id} Refunded (${updated_txn.get('amount', '49.00') if updated_txn else '49.00'} via Stripe Gateway API)"

        plan_rate = analysis.get("retained_transaction", {}).get("amount", "49.00")
        active_ref_id = existing_ref_id if is_already_ref else (updated_txn.get("refund_id") if updated_txn else ref_code)

        # Synchronize and update data/customer_accounts.csv
        accounts = read_all_customer_accounts()
        target_cust_id = analysis.get("customer_id")
        for acc in accounts:
            if acc.get("customer_id") == target_cust_id:
                acc["refund_status"] = f"Refunded (${plan_rate} via Stripe API)"
                acc["refund_id"] = active_ref_id
                acc["account_balance"] = "$0.00 (Settled)"
                acc["account_status"] = "Active - In Good Standing"
                acc["duplicate_verified"] = "Yes - Accidental Double Charge Detected"
                acc["total_billed_this_month"] = f"{plan_rate} (Net after ${plan_rate} refund)"
                if target_txn_id in acc.get("transaction_2", "") or not acc.get("transaction_2") or acc.get("transaction_2") == "None":
                    acc["transaction_2"] = f"{target_txn_id} (${plan_rate} on {now_str[:10]}) [Refunded: {active_ref_id}]"
                acc["last_verified_by"] = "Troy (Autonomous Resolution Agent)"
                acc["updated_by"] = "Troy (Autonomous Resolution Agent)"
                acc["last_updated"] = now_str
                acc["notes"] = f"Autonomous override executed by Troy. Accidental double renewal debit reversed in Stripe API under {active_ref_id}."
                break
        write_all_customer_accounts(accounts)

        order_num = analysis.get("order_id") or analysis.get("customer", {}).get("reference_order_id", "ORD-88219")
        ticket_num = order_num.replace("ORD-", "")

        return {
            "ok": True,
            "action": "agent_resolve",
            "category": "Refunds",
            "already_refunded": is_already_ref,
            "is_already_refunded": is_already_ref,
            "refund_id": active_ref_id,
            "refunded_transaction": updated_txn,
            "retained_transaction": analysis.get("retained_transaction"),
            "duplicate_transaction": updated_txn or dup_txn,
            "customer": {
                "customer_id": analysis.get("customer_id", "USR-10492"),
                "customer_name": analysis.get("customer_name", "Alex Morgan"),
                "customer_email": analysis.get("customer_email", "alex.morgan@gmail.com"),
                "subscription_plan": analysis.get("subscription_plan", "Pro Tier ($49.00/mo)"),
                "status": "Resolved",
                "account_status": "Active - In Good Standing",
                "refund_status": f"Refunded (${plan_rate} via Stripe API)",
                "refund_id": active_ref_id,
                "refunded_at": existing_ref_date if is_already_ref else now_str,
                "account_balance": "$0.00 (Settled)",
                "customer_notified": f"Yes - Confirmation Dispatched ({active_ref_id})",
            },
            "ticket": {
                "ticket_id": ticket_num,
                "order_id": order_num,
                "customer_name": analysis.get("customer_name", "Alex Morgan"),
                "customer_email": analysis.get("customer_email", "alex.morgan@gmail.com"),
                "status": "Resolved",
                "resolution_action": res_action,
                "refund_id": active_ref_id,
                "customer_notified": f"Yes - Email Dispatched ({active_ref_id})",
            },
            "database_file": "data/transactions.csv",
            "timestamp": now_str,
        }

    # For all other categories:
    cat_name = context.get("category", "General Support")
    ref_id = context.get("reference_id", "RES-9901-AUTO")
    cust_id = context.get("customer_id", "USR-08914")
    cust_name = context.get("customer_name", "Valued Customer")
    cust_email = context.get("customer_email", "customer@enterprise.org")
    plan = context.get("subscription_plan", "Enterprise Tier")
    sys_file = context.get("database_file") or context.get("system_label", "System Directory")
    res_action = action if (action and "Refund" not in action) else (context.get("action_btn_text") or f"{cat_name} autonomously resolved by Troy under reference {ref_id}.")

    logger.info(f"Autonomous resolution executed for category [{cat_name}] on account {cust_id} ({ref_id}).")

    return {
        "ok": True,
        "action": "agent_resolve",
        "category": cat_name,
        "reference_id": ref_id,
        "already_refunded": False,
        "is_already_refunded": False,
        "resolution_action": res_action,
        "customer": {
            "customer_id": cust_id,
            "customer_name": cust_name,
            "customer_email": cust_email,
            "subscription_plan": plan,
            "status": "Resolved",
            "account_status": f"Active - {cat_name} Reconciled",
            "reference_id": ref_id,
            "refund_id": ref_id,
            "account_balance": "$0.00 (Settled)",
            "customer_notified": f"Yes - Confirmation Dispatched ({ref_id})",
        },
        "ticket": {
            "ticket_id": ref_id,
            "category": cat_name,
            "customer_name": cust_name,
            "customer_email": cust_email,
            "status": "Resolved",
            "resolution_action": res_action,
            "reference_id": ref_id,
            "customer_notified": f"Yes - Notice Dispatched ({ref_id})",
        },
        "database_file": sys_file,
        "timestamp": now_str,
    }


CATEGORY_TO_TEAM = {
    "Billing": "Billing",
    "Refunds": "Billing",
    "Subscription": "Billing",
    "Security": "Security",
    "Authentication & Access": "Security",
    "Bugs & Errors": "Engineering",
    "Performance": "Engineering",
    "Data Sync": "Engineering",
    "Integrations & API": "Engineering",
    "Account Management": "Tier 1 Support",
    "Onboarding & How-To": "Tier 1 Support",
}


CATEGORY_MANUAL_ROUTING = {
    "Refunds": {
        "queue": "Billing Team Specialist",
        "action": "Flagged for manual ledger audit and refund authorization by Human Billing Specialist.",
        "status": "Escalated - Billing Specialist Audit",
    },
    "Billing": {
        "queue": "Billing & Corporate Tax Specialist",
        "action": "Routed to Corporate Accounting for VAT verification and custom invoice issuance.",
        "status": "Escalated - Tax & Billing Audit",
    },
    "Subscription": {
        "queue": "Subscription & Account Retention Specialist",
        "action": "Assigned to Retention Team for manual plan tier adjustment and credit review.",
        "status": "Escalated - Subscription Adjustment Review",
    },
    "Authentication & Access": {
        "queue": "Security & IAM Access Specialist",
        "action": "Routed to Identity & Access Management (IAM) for manual 2FA reset and credential recovery.",
        "status": "Escalated - IAM Security Review",
    },
    "Security": {
        "queue": "Security Operations Center (SOC) Specialist",
        "action": "Escalated to SOC Incident Response for threat assessment, IP forensics, and session review.",
        "status": "Escalated - Security Incident Review",
    },
    "Bugs & Errors": {
        "queue": "Engineering Bug Triage & QA Specialist",
        "action": "Logged in Engineering Priority Queue with client telemetry and stack trace for developer fix.",
        "status": "Escalated - Engineering Bug Triage",
    },
    "Integrations & API": {
        "queue": "API Platform & Developer Support Specialist",
        "action": "Assigned to Developer Platform Engineers for webhook dead-letter analysis and rate-limit audit.",
        "status": "Escalated - Developer Support Review",
    },
    "Performance": {
        "queue": "Site Reliability Engineering (SRE) Specialist",
        "action": "Assigned to Infrastructure & Database Team for query profiling and index optimization.",
        "status": "Escalated - Infrastructure Review",
    },
    "Data Sync": {
        "queue": "Distributed Systems & Cloud Sync Specialist",
        "action": "Routed to Backend Engineering for manual database conflict reconciliation and queue replay.",
        "status": "Escalated - Data Reconciliation Review",
    },
    "Onboarding & How-To": {
        "queue": "Customer Success & Onboarding Specialist",
        "action": "Assigned to Dedicated Customer Success Manager for guided workspace walkthrough.",
        "status": "Escalated - Customer Success Outreach",
    },
    "Account Management": {
        "queue": "Tier 1 Support & Account Administration Specialist",
        "action": "Routed to Account Administration for organization consolidation and contract contact update.",
        "status": "Escalated - Account Admin Review",
    },
}


def execute_manual_resolution(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
    notes: Optional[str] = None,
    category: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Escalates complaint for human intervention according to its specific category.
    - For Refunds: Audits and flags duplicate transaction in data/transactions.csv.
    - For all other categories: Routes ticket to the dedicated specialist queue without touching transactions.csv.
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    context = get_category_complaint_context(category=category or "", query=query or "")
    cat_name = context.get("category", "General Support")
    is_refund = context.get("is_refund", False)

    routing_info = CATEGORY_MANUAL_ROUTING.get(
        cat_name,
        {
            "queue": f"{CATEGORY_TO_TEAM.get(cat_name, 'Support')} Specialist",
            "action": f"Escalated to {cat_name} specialist queue for manual operator review.",
            "status": f"Escalated - {cat_name} Specialist Review",
        },
    )
    assigned_queue = routing_info["queue"]
    escalation_action = routing_info["action"]
    account_status = routing_info["status"]

    if is_refund:
        all_txns = read_all_transactions()
        analysis = analyze_customer_transactions(customer_id=customer_id, query=query)
        dup_txn = analysis.get("duplicate_transaction")
        target_txn_id = dup_txn.get("transaction_id") if dup_txn else "TXN-9012"

        for row in all_txns:
            if row.get("transaction_id") == target_txn_id:
                row["notes"] = notes or f"Flagged for manual review by {assigned_queue}."
                row["processed_by"] = f"Escalated by Troy -> {assigned_queue}"
                break

        write_all_transactions(all_txns)
        logger.info(f"Database updated: Transaction {target_txn_id} flagged for manual review in data/transactions.csv.")

        order_num = analysis.get("order_id") or analysis.get("customer", {}).get("reference_order_id", "ORD-88219")
        ticket_num = order_num.replace("ORD-", "")

        return {
            "ok": True,
            "action": "manual_resolve",
            "category": "Refunds",
            "assigned_queue": assigned_queue,
            "resolution_action": escalation_action,
            "customer": {
                "customer_id": analysis.get("customer_id", "USR-10492"),
                "customer_name": analysis.get("customer_name", "Alex Morgan"),
                "customer_email": analysis.get("customer_email", "alex.morgan@gmail.com"),
                "subscription_plan": analysis.get("subscription_plan", "Pro Tier ($49.00/mo)"),
                "account_status": account_status,
                "status": "Escalated",
                "customer_notified": "Yes - Escalation Notice Sent",
            },
            "ticket": {
                "ticket_id": ticket_num,
                "order_id": order_num,
                "status": "Escalated - Manual Review",
                "predicted_team": assigned_queue,
                "customer_notified": "Yes - Escalation Notice Sent",
                "notes": notes or escalation_action,
                "resolution_action": escalation_action,
            },
            "database_file": "data/transactions.csv",
            "reference_id": target_txn_id,
            "timestamp": now_str,
        }

    # Non-refund categories: strictly NO CSV access
    ref_id = context.get("reference_id", "ESC-9901-REV")
    cust_id = context.get("customer_id", customer_id or "USR-08914")
    cust_name = context.get("customer_name", "Valued Customer")
    cust_email = context.get("customer_email", "customer@enterprise.org")
    plan = context.get("subscription_plan", "Enterprise Tier")
    sys_file = context.get("database_file") or context.get("system_label", "System Directory")
    system_label = context.get("system_label", sys_file)

    logger.info(f"Manual intervention: Ticket {ref_id} for category [{cat_name}] routed to [{assigned_queue}] in {sys_file}.")

    return {
        "ok": True,
        "action": "manual_resolve",
        "category": cat_name,
        "reference_id": ref_id,
        "assigned_queue": assigned_queue,
        "resolution_action": escalation_action,
        "customer": {
            "customer_id": cust_id,
            "customer_name": cust_name,
            "customer_email": cust_email,
            "subscription_plan": plan,
            "status": "Escalated",
            "account_status": account_status,
            "reference_id": ref_id,
            "customer_notified": f"Yes - Escalation Notice Sent ({ref_id})",
        },
        "ticket": {
            "ticket_id": ref_id,
            "category": cat_name,
            "order_id": ref_id,
            "customer_name": cust_name,
            "customer_email": cust_email,
            "status": "Escalated - Manual Review",
            "predicted_team": assigned_queue,
            "customer_notified": f"Yes - Escalation Notice Sent ({ref_id})",
            "notes": notes or escalation_action,
            "resolution_action": escalation_action,
        },
        "database_file": sys_file,
        "system_label": system_label,
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
