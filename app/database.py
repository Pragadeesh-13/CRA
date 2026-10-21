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
        # Already refunded in earlier resolution
        retained_valid_txn = settled_txns[0] if settled_txns else cycle_txns[0]
        candidate_to_refund = refunded_txns[0]
    else:
        retained_valid_txn = settled_txns[0] if settled_txns else (cycle_txns[0] if cycle_txns else None)
        candidate_to_refund = None

    overcharged_amount = float(candidate_to_refund.get("amount", "49.00")) if candidate_to_refund else 0.0

    ref_id = refunded_txns[0].get("refund_id") or "REF-88219-AUTO" if refunded_txns else ""
    ref_date = refunded_txns[0].get("refunded_at") or "" if refunded_txns else ""

    if is_already_refunded:
        summary_text = (
            f"Analyzed data/transactions.csv: Found {len(cycle_txns)} transaction records in {cycle} for {cust_name} ({cust_id}). "
            f"Records: {', '.join([f'{t.get('transaction_id')} (${t.get('amount')}, {t.get('status')})' for t in cycle_txns])}. "
            f"Accidental duplicate debit {candidate_to_refund.get('transaction_id')} was ALREADY REFUNDED on {ref_date} (Refund ID: {ref_id}). "
            f"Current ledger has 1 valid active renewal ({retained_valid_txn.get('transaction_id')}: ${retained_valid_txn.get('amount')}, Settled). "
            f"Account balance is $0.00. No further refund required."
        )
    elif has_duplicate_settled:
        summary_text = (
            f"Analyzed data/transactions.csv: Found {len(cycle_txns)} transaction records in {cycle} for {cust_name} ({cust_id}). "
            f"Records: {', '.join([f'{t.get('transaction_id')} (${t.get('amount')}, {t.get('status')})' for t in cycle_txns])}. "
            f"Multiple settled debits detected in a single monthly cycle. Candidate transaction for refund: {candidate_to_refund.get('transaction_id') if candidate_to_refund else 'None'}."
        )
    else:
        summary_text = (
            f"Analyzed data/transactions.csv: Found {len(cycle_txns)} transaction records in {cycle} for {cust_name} ({cust_id}). "
            f"Records: {', '.join([f'{t.get('transaction_id')} (${t.get('amount')}, {t.get('status')})' for t in cycle_txns])}. "
            f"Ledger reconciled. No duplicate charges found."
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
            "is_already_refunded": is_already_refunded,
            "refund_status": "Already Refunded" if is_already_refunded else ("Refunded" if refunded_txns else ("Pending Refund" if has_duplicate_settled else "N/A")),
            "refund_id": ref_id,
            "refunded_at": ref_date,
            "account_balance": "$0.00 (Settled)" if not has_duplicate_settled else f"$-{overcharged_amount:.2f} (Overcharged)",
            "account_status": "Active - In Good Standing" if not has_duplicate_settled else "Flagged - Multiple Charges in Cycle",
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
            "steps": [
                {"text": "Accessing internal customer transaction ledger (data/transactions.csv)...", "delay": 650},
                {"text": "Locating account records for Alex Morgan (USR-10492)...", "delay": 700},
                {"text": f"Ledger Audit: Duplicate TXN-9012 is {'ALREADY marked as Refunded under ' + ref_id if is_already_refunded else 'verified as redundant second debit in October 2026'}...", "delay": 750},
                {"text": f"{'Verifying account balance: Account balance is $0.00 with 1 active renewal TXN-9011' if is_already_refunded else 'Authorizing $49.00 refund reversal via Stripe Gateway API (REF-88219-AUTO)'}...", "delay": 750},
                {"text": f"{'Duplicate payout prevention active: Secondary refund blocked' if is_already_refunded else 'Updating data/transactions.csv: TXN-9012 status set to Refunded'}...", "delay": 700},
                {"text": f"Dispatching refund confirmation receipt ({ref_id}) to alex.morgan@gmail.com...", "delay": 600},
            ],
            "txn_analysis": txn_analysis,
        }

    # Category 2: Authentication & Access
    elif cat == "Authentication & Access" or "2fa" in q or "two-factor" in q or "login" in q or "locked" in q:
        return {
            "category": "Authentication & Access",
            "is_refund": False,
            "system_label": "Identity & Access Management (Okta / IAM Gateway)",
            "database_file": "Identity Directory (IAM / Okta)",
            "status_pill": "🔒 Account Locked: 2FA SMS Gateway Failure",
            "action_btn_text": "Let agent restore access & reset 2FA",
            "customer_id": "USR-08914",
            "customer_name": "Sarah Jenkins",
            "customer_email": "sarah.j@enterprise.org",
            "subscription_plan": "Enterprise Tier ($299.00/mo)",
            "reference_id": "AUTH-9921",
            "system_details": "Account locked after failed carrier SMS OTP delivery on payment portal. Identity verified via enterprise SSO certificate.",
            "steps": [
                {"text": "Accessing Identity & Access Management directory (IAM / Okta)...", "delay": 650},
                {"text": "Verifying corporate identity credentials for Sarah Jenkins (USR-08914)...", "delay": 700},
                {"text": "Diagnosing authentication log: Carrier SMS delivery timeout on Payment Gateway...", "delay": 750},
                {"text": "Generating temporary secure 2FA emergency bypass token (AUTH-9921)...", "delay": 750},
                {"text": "Unlocking account status in IAM directory & resetting failed attempt counter...", "delay": 700},
                {"text": "Dispatching secure biometric re-enrollment link to sarah.j@enterprise.org...", "delay": 600},
            ],
        }

    # Category 3: Billing
    elif cat == "Billing" or "vat" in q or "invoice" in q or "tax id" in q:
        return {
            "category": "Billing",
            "is_refund": False,
            "system_label": "Stripe Invoicing & Global Tax Ledger",
            "database_file": "Invoicing & Regional Tax Ledger",
            "status_pill": "📋 Pending VAT & Invoicing Documentation",
            "action_btn_text": "Let agent generate & dispatch VAT invoice",
            "customer_id": "USR-14309",
            "customer_name": "Elena Rostova",
            "customer_email": "elena@globaltrade.de",
            "subscription_plan": "Team Tier ($120.00/mo)",
            "reference_id": "INV-85420-VAT",
            "system_details": "Customer requested formal German VAT ID addition and stamped tax invoice for Q4 2026 accounting records.",
            "steps": [
                {"text": "Accessing billing ledger & tax invoice engine...", "delay": 650},
                {"text": "Locating account records for Elena Rostova (USR-14309)...", "delay": 700},
                {"text": "Validating VAT registration ID via European VIES tax service...", "delay": 750},
                {"text": "Generating official stamped VAT invoice (INV-85420-VAT) with tax breakdown...", "delay": 750},
                {"text": "Updating billing profile in customer organization profile...", "delay": 700},
                {"text": "Dispatching signed tax receipt and PDF invoice to elena@globaltrade.de...", "delay": 600},
            ],
        }

    # Category 4: Bugs & Errors
    elif cat == "Bugs & Errors" or "crash" in q or "500 error" in q or "bug" in q:
        return {
            "category": "Bugs & Errors",
            "is_refund": False,
            "system_label": "Sentry Error Telemetry & Jira Engineering Board",
            "database_file": "Engineering Defect Telemetry (Sentry & Jira)",
            "status_pill": "🐛 HTTP 500 Unhandled Exception Logged",
            "action_btn_text": "Let agent dispatch bug report to Engineering",
            "customer_id": "USR-12053",
            "customer_name": "David Chen",
            "customer_email": "d.chen@fintech.io",
            "subscription_plan": "Developer API Tier ($199.00/mo)",
            "reference_id": "BUG-ENG-4401",
            "system_details": "Correlated recurring HTTP 500 crash with memory stream buffer exception in client release v2.4.1.",
            "steps": [
                {"text": "Accessing Sentry error telemetry & crash reporting logs...", "delay": 650},
                {"text": "Correlating stack trace with client release v2.4.1 export handler...", "delay": 700},
                {"text": "Formulating Engineering defect ticket: BUG-ENG-4401 (Priority: High)...", "delay": 750},
                {"text": "Synthesizing temporary client-side workaround (direct CSV export stream)...", "delay": 750},
                {"text": "Logging ticket in Engineering sprint queue with full diagnostic trace...", "delay": 700},
                {"text": "Dispatching workaround instructions & ticket tracking to d.chen@fintech.io...", "delay": 600},
            ],
        }

    # Category 5: Data Sync
    elif cat == "Data Sync" or "sync" in q or "synchronization" in q:
        return {
            "category": "Data Sync",
            "is_refund": False,
            "system_label": "Cloud Real-Time Sync & State Reconciler",
            "database_file": "Cloud Real-Time Sync & State Reconciler",
            "status_pill": "🔄 Desynchronized: Offline Queue Conflict",
            "action_btn_text": "Let agent trigger cloud resync & reconcile state",
            "customer_id": "USR-10492",
            "customer_name": "Alex Morgan",
            "customer_email": "alex.morgan@gmail.com",
            "subscription_plan": "Pro Tier ($49.00/mo)",
            "reference_id": "SYNC-RECON-2201",
            "system_details": "Timestamp divergence detected between mobile client offline queue and cloud PostgreSQL cluster.",
            "steps": [
                {"text": "Connecting to cloud real-time sync coordinator...", "delay": 650},
                {"text": "Auditing device-to-cloud replication queue for USR-10492...", "delay": 700},
                {"text": "Detecting timestamp conflict on pending mobile client records...", "delay": 750},
                {"text": "Executing three-way merge resolution algorithm (SYNC-RECON-2201)...", "delay": 750},
                {"text": "Forcing cloud ledger cache invalidation and clean state push...", "delay": 700},
                {"text": "Notifying user client of successful resynchronization...", "delay": 600},
            ],
        }

    # Category 6: Integrations & API
    elif cat == "Integrations & API" or "api" in q or "webhook" in q or "429" in q:
        return {
            "category": "Integrations & API",
            "is_refund": False,
            "system_label": "API Gateway Telemetry & Webhook Dispatcher",
            "database_file": "API Gateway Telemetry & Webhook Dispatcher",
            "status_pill": "⚡ Webhook Timeout (HTTP 504) / Rate Limit 429",
            "action_btn_text": "Let agent dispatch API guide & flush webhook queue",
            "customer_id": "USR-12053",
            "customer_name": "David Chen",
            "customer_email": "d.chen@fintech.io",
            "subscription_plan": "Developer API Tier ($199.00/mo)",
            "reference_id": "API-HOOK-7703",
            "system_details": "12 webhook payloads dead-lettered due to endpoint latency; burst rate limit threshold exceeded.",
            "steps": [
                {"text": "Querying API Gateway error telemetry & access logs...", "delay": 650},
                {"text": "Locating developer account for David Chen (USR-12053)...", "delay": 700},
                {"text": "Analyzing dead-letter queue: 12 events delayed by 504 endpoint timeout...", "delay": 750},
                {"text": "Resetting burst rate-limit token bucket for customer API key...", "delay": 750},
                {"text": "Initiating automated webhook replay sequence (API-HOOK-7703)...", "delay": 700},
                {"text": "Dispatching developer documentation & OpenAPI specification link...", "delay": 600},
            ],
        }

    # Category 7: Onboarding & How-To
    elif cat == "Onboarding & How-To" or "how to" in q or "get started" in q or "invite" in q:
        return {
            "category": "Onboarding & How-To",
            "is_refund": False,
            "system_label": "Customer Success Knowledge Base & Permissions Engine",
            "database_file": "Customer Success Knowledge Base & CRM",
            "status_pill": "📚 Workspace Setup & Role Delegation Assistance",
            "action_btn_text": "Let agent send setup guide & configure workspace",
            "customer_id": "USR-09148",
            "customer_name": "Michael Scott",
            "customer_email": "m.scott@dunder.com",
            "subscription_plan": "Starter Tier ($29.00/mo)",
            "reference_id": "GUIDE-ONB-1102",
            "system_details": "New workspace administrator requesting guidance on inviting team members and setting role-based permissions.",
            "steps": [
                {"text": "Accessing Customer Success Knowledge Base & training registry...", "delay": 650},
                {"text": "Locating workspace records for USR-09148 (Michael Scott)...", "delay": 700},
                {"text": "Synthesizing step-by-step role delegation & member onboarding workflow...", "delay": 750},
                {"text": "Generating automated member invitation link and role permission template...", "delay": 750},
                {"text": "Recording onboarding milestone completion in customer CRM...", "delay": 700},
                {"text": "Dispatching interactive guide (GUIDE-ONB-1102) to m.scott@dunder.com...", "delay": 600},
            ],
        }

    # Category 8: Performance
    elif cat == "Performance" or "slow" in q or "latency" in q or "load" in q:
        return {
            "category": "Performance",
            "is_refund": False,
            "system_label": "Datadog APM & Database Query Telemetry",
            "database_file": "Datadog APM & Database Query Telemetry",
            "status_pill": "⚡ High Latency Bottleneck: 42.8s Search Duration",
            "action_btn_text": "Let agent flush query cache & run optimization",
            "customer_id": "USR-08914",
            "customer_name": "Sarah Jenkins",
            "customer_email": "sarah.j@enterprise.org",
            "subscription_plan": "Enterprise Tier ($299.00/mo)",
            "reference_id": "PERF-OPT-3301",
            "system_details": "Full-text search queries exceeding SLA threshold due to fragmented elastic index cache.",
            "steps": [
                {"text": "Connecting to Datadog APM & search cluster telemetry...", "delay": 650},
                {"text": "Profiling query latency: P99 response time degraded to 42.8s...", "delay": 700},
                {"text": "Identifying bottleneck: Stale search index cache and worker thread saturation...", "delay": 750},
                {"text": "Executing automated cache invalidation and query index defragmentation (PERF-OPT-3301)...", "delay": 750},
                {"text": "Verifying query latency normalized to < 450ms baseline...", "delay": 700},
                {"text": "Dispatching performance resolution report to sarah.j@enterprise.org...", "delay": 600},
            ],
        }

    # Category 9: Security
    elif cat == "Security" or "unauthorized" in q or "compromised" in q or "ip allowlist" in q:
        return {
            "category": "Security",
            "is_refund": False,
            "system_label": "Cloudflare WAF & Security Operations Center (SOC)",
            "database_file": "Cloudflare WAF & Security Operations Center (SOC)",
            "status_pill": "🛡️ Anomaly: Unauthorized Access / Untrusted IP",
            "action_btn_text": "Let agent terminate sessions & secure account",
            "customer_id": "USR-08914",
            "customer_name": "Sarah Jenkins",
            "customer_email": "sarah.j@enterprise.org",
            "subscription_plan": "Enterprise Tier ($299.00/mo)",
            "reference_id": "SEC-REVOKE-8812",
            "system_details": "Anomalous authorization token detected from unverified IP range. Threat level assessed as elevated.",
            "steps": [
                {"text": "Connecting to Security Operations Center (SOC) telemetry...", "delay": 650},
                {"text": "Auditing active user sessions for Sarah Jenkins (USR-08914)...", "delay": 700},
                {"text": "Confirming geolocation anomaly: Untrusted login session from unfamiliar IP...", "delay": 750},
                {"text": "Immediately revoking all active JWT bearer tokens (SEC-REVOKE-8812)...", "delay": 750},
                {"text": "Enforcing mandatory multi-factor biometric challenge on next sign-in...", "delay": 700},
                {"text": "Dispatching urgent security incident notice & confirmation to user...", "delay": 600},
            ],
        }

    # Category 10: Subscription
    elif cat == "Subscription" or "downgrade" in q or "renewal" in q or "cancel" in q:
        return {
            "category": "Subscription",
            "is_refund": False,
            "system_label": "Stripe Subscription Billing & Entitlements Engine",
            "database_file": "Stripe Subscription & Entitlements Engine",
            "status_pill": "📋 Pending Downgrade Adjustment ($50.00 Credit Due)",
            "action_btn_text": "Let agent apply downgrade & credit prorated balance",
            "customer_id": "USR-09148",
            "customer_name": "Michael Scott",
            "customer_email": "m.scott@dunder.com",
            "subscription_plan": "Starter Tier ($29.00/mo)",
            "reference_id": "SUB-ADJ-6601",
            "system_details": "Customer requested downgrade to Starter Tier prior to renewal; legacy renewal was billed at $79.00. $50.00 credit due.",
            "steps": [
                {"text": "Connecting to Stripe Subscription & Entitlements Engine...", "delay": 650},
                {"text": "Locating subscription profile for Michael Scott (USR-09148)...", "delay": 700},
                {"text": "Auditing billing schedule: Legacy $79 renewal confirmed; downgrade to Starter ($29) requested...", "delay": 750},
                {"text": "Calculating prorated delta: $50.00 credit approved under SUB-ADJ-6601...", "delay": 750},
                {"text": "Applying $50.00 credit to customer ledger & setting renewal tier to Starter ($29.00)...", "delay": 700},
                {"text": "Dispatching updated subscription schedule and receipt to m.scott@dunder.com...", "delay": 600},
            ],
        }

    # Category 11: Account Management (default fallback)
    else:
        return {
            "category": "Account Management",
            "is_refund": False,
            "system_label": "Enterprise Workspace Directory & IAM",
            "database_file": "Enterprise Workspace Directory & IAM",
            "status_pill": "👤 Entity Update & Account Consolidation Request",
            "action_btn_text": "Let agent update account records & confirm",
            "customer_id": "USR-14309",
            "customer_name": "Elena Rostova",
            "customer_email": "elena@globaltrade.de",
            "subscription_plan": "Team Tier ($120.00/mo)",
            "reference_id": "ACC-MGT-5501",
            "system_details": "Request to merge subsidiary account into primary organization and update legal contact information.",
            "steps": [
                {"text": "Accessing Enterprise Workspace Directory (IAM / SSO)...", "delay": 650},
                {"text": "Locating organization profile for Elena Rostova (USR-14309)...", "delay": 700},
                {"text": "Verifying organizational signing authority and corporate domain records...", "delay": 750},
                {"text": "Processing account consolidation and updating primary contact credentials (ACC-MGT-5501)...", "delay": 750},
                {"text": "Updating customer master account record in Enterprise Directory...", "delay": 700},
                {"text": "Dispatching consolidation confirmation notice to elena@globaltrade.de...", "delay": 600},
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
            res_action = f"Transaction {target_txn_id} Refunded ($49.00 via Stripe Gateway API)"

        plan_rate = analysis.get("retained_transaction", {}).get("amount", "49.00")
        active_ref_id = existing_ref_id if is_already_ref else (updated_txn.get("refund_id") if updated_txn else "REF-88219-AUTO")

        return {
            "ok": True,
            "action": "agent_resolve",
            "category": "Refunds",
            "already_refunded": is_already_ref,
            "is_already_refunded": is_already_ref,
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
                "refund_id": active_ref_id,
                "refunded_at": existing_ref_date if is_already_ref else now_str,
                "account_balance": "$0.00 (Settled)",
                "customer_notified": f"Yes - Confirmation Dispatched ({active_ref_id})",
            },
            "ticket": {
                "ticket_id": "88219",
                "order_id": "ORD-88219",
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
