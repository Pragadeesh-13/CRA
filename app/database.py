"""
Customer Accounts & Billing Ledger Database for Complaint Resolution Agent (CRA).
Manages customer subscription data, billing transactions, and autonomous updates in data/customer_accounts.csv.
"""

from __future__ import annotations

import csv
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATABASE_FILE = DATA_DIR / "customer_accounts.csv"

FIELDNAMES = [
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

DEFAULT_CUSTOMERS = [
    {
        "customer_id": "USR-10492",
        "customer_name": "Alex Morgan",
        "customer_email": "alex.morgan@gmail.com",
        "reference_order_id": "ORD-88219",
        "subscription_plan": "Pro Tier ($49.00/mo)",
        "billing_cycle": "Monthly (20th)",
        "monthly_rate": "49.00",
        "total_billed_this_month": "98.00",
        "charges_this_month": "2",
        "transaction_1": "TXN-9011 ($49.00 on 2026-10-20) [Settled Renewal]",
        "transaction_2": "TXN-9012 ($49.00 on 2026-10-21) [Accidental Duplicate Debit]",
        "duplicate_verified": "Yes - Accidental Double Charge Detected",
        "refund_status": "Pending Refund",
        "refund_id": "",
        "account_balance": "$-49.00 (Overcharged)",
        "account_status": "Flagged - Accidental Double Charge",
        "last_verified_by": "Troy AI",
        "updated_by": "System Billing Daemon",
        "last_updated": "2026-10-21 12:00:00",
        "notes": "Accidental duplicate renewal charge debited twice within 24 hours. Awaiting agent resolution.",
    },
    {
        "customer_id": "USR-08914",
        "customer_name": "Sarah Jenkins",
        "customer_email": "sarah.j@enterprise.org",
        "reference_order_id": "ORD-79110",
        "subscription_plan": "Enterprise Tier ($299.00/mo)",
        "billing_cycle": "Monthly (1st)",
        "monthly_rate": "299.00",
        "total_billed_this_month": "299.00",
        "charges_this_month": "1",
        "transaction_1": "TXN-8801 ($299.00 on 2026-10-01) [Settled]",
        "transaction_2": "None",
        "duplicate_verified": "No",
        "refund_status": "N/A",
        "refund_id": "",
        "account_balance": "$0.00 (Current)",
        "account_status": "Locked - 2FA SMS Gateway Failure",
        "last_verified_by": "Tier 1 Support",
        "updated_by": "Auth Service",
        "last_updated": "2026-10-21 09:30:00",
        "notes": "Account locked after 3 failed 2FA SMS attempts. Awaiting gateway reset.",
    },
    {
        "customer_id": "USR-12053",
        "customer_name": "David Chen",
        "customer_email": "d.chen@fintech.io",
        "reference_order_id": "ORD-93041",
        "subscription_plan": "Developer API Tier ($199.00/mo)",
        "billing_cycle": "Monthly (15th)",
        "monthly_rate": "199.00",
        "total_billed_this_month": "199.00",
        "charges_this_month": "1",
        "transaction_1": "TXN-8450 ($199.00 on 2026-10-15) [Settled]",
        "transaction_2": "None",
        "duplicate_verified": "No",
        "refund_status": "N/A",
        "refund_id": "",
        "account_balance": "$0.00 (Current)",
        "account_status": "Degraded - Webhook Gateway 504 Timeout",
        "last_verified_by": "Engineering Bot",
        "updated_by": "API Monitor",
        "last_updated": "2026-10-21 10:15:00",
        "notes": "Webhook retry queue flooded. Engineering investigation underway.",
    },
    {
        "customer_id": "USR-14309",
        "customer_name": "Elena Rostova",
        "customer_email": "elena@globaltrade.de",
        "reference_order_id": "ORD-85420",
        "subscription_plan": "Team Tier ($120.00/mo)",
        "billing_cycle": "Quarterly ($360.00)",
        "monthly_rate": "120.00",
        "total_billed_this_month": "360.00",
        "charges_this_month": "1",
        "transaction_1": "TXN-8201 ($360.00 on 2026-10-05) [Settled]",
        "transaction_2": "None",
        "duplicate_verified": "No",
        "refund_status": "N/A",
        "refund_id": "",
        "account_balance": "$0.00 (Current)",
        "account_status": "Active - Pending VAT Invoice Receipt",
        "last_verified_by": "Billing Team",
        "updated_by": "Invoicing Engine",
        "last_updated": "2026-10-20 16:40:00",
        "notes": "Formal German VAT tax invoice and receipt requested for accounting records.",
    },
    {
        "customer_id": "USR-09148",
        "customer_name": "Michael Scott",
        "customer_email": "m.scott@dunder.com",
        "reference_order_id": "ORD-86102",
        "subscription_plan": "Starter Tier ($29.00/mo)",
        "billing_cycle": "Monthly (18th)",
        "monthly_rate": "29.00",
        "total_billed_this_month": "79.00",
        "charges_this_month": "1",
        "transaction_1": "TXN-8902 ($79.00 on 2026-10-18) [Settled - Old Plan]",
        "transaction_2": "None",
        "duplicate_verified": "No",
        "refund_status": "Partial Refund Pending ($50.00)",
        "refund_id": "",
        "account_balance": "$-50.00 (Credit Due)",
        "account_status": "Pending Downgrade Adjustment",
        "last_verified_by": "Billing Bot",
        "updated_by": "Subscription Manager",
        "last_updated": "2026-10-20 18:00:00",
        "notes": "User requested downgrade prior to renewal. $50 prorated refund due.",
    },
    {
        "customer_id": "USR-07231",
        "customer_name": "Emily Watson",
        "customer_email": "emily.w@consulting.co",
        "reference_order_id": "ORD-81094",
        "subscription_plan": "Pro Tier ($49.00/mo)",
        "billing_cycle": "Monthly (18th)",
        "monthly_rate": "49.00",
        "total_billed_this_month": "49.00",
        "charges_this_month": "2",
        "transaction_1": "TXN-7711 ($49.00 on 2026-09-18) [Settled Renewal]",
        "transaction_2": "TXN-7712 ($49.00 on 2026-09-18) [Refunded: REF-7712-AUTO]",
        "duplicate_verified": "Yes - Double Debit on Sep 18",
        "refund_status": "Refunded ($49.00 via Stripe API)",
        "refund_id": "REF-7712-AUTO",
        "account_balance": "$0.00 (Settled)",
        "account_status": "Active - In Good Standing",
        "last_verified_by": "Troy (Autonomous Resolution Agent)",
        "updated_by": "Troy (Autonomous Resolution Agent)",
        "last_updated": "2026-09-18 14:22:10",
        "notes": "Precedent Case: Accidental double renewal refunded autonomously by Troy.",
    },
]


def ensure_database_file() -> Path:
    """Ensures data directory and customer accounts CSV file exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATABASE_FILE.exists():
        with open(DATABASE_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(DEFAULT_CUSTOMERS)
        logger.info(f"Initialized customer accounts database at {DATABASE_FILE}")
    return DATABASE_FILE


def read_all_customers() -> List[Dict[str, str]]:
    """Reads all customer rows from customer_accounts.csv."""
    db_path = ensure_database_file()
    customers: List[Dict[str, str]] = []
    try:
        with open(db_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                customers.append(dict(row))
    except Exception as e:
        logger.error(f"Error reading {db_path}: {e}")
    return customers


def write_all_customers(customers: List[Dict[str, str]]) -> bool:
    """Writes customer rows to customer_accounts.csv."""
    db_path = ensure_database_file()
    try:
        with open(db_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(customers)
        return True
    except Exception as e:
        logger.error(f"Error writing to {db_path}: {e}")
        return False


def find_customer_by_id_or_query(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Finds a customer record by ID, order reference, or matching tokens in query text."""
    customers = read_all_customers()
    target_id = str(customer_id).strip() if customer_id else None

    # 1. Direct customer_id match (e.g. USR-10492 or 10492)
    if target_id:
        for c in customers:
            if c.get("customer_id") == target_id or target_id in c.get("customer_id", ""):
                return c

    # 2. Extract reference from query text (e.g. #88219 or ORD-88219)
    if query:
        # Check order ID regex
        match = re.search(r"#?(\d{5,})", query)
        if match:
            ref_num = match.group(1)
            for c in customers:
                if ref_num in c.get("reference_order_id", "") or ref_num in c.get("customer_id", ""):
                    return c

        # Match name or email
        query_lower = query.lower()
        for c in customers:
            name = c.get("customer_name", "").lower()
            email = c.get("customer_email", "").lower()
            if (name and name in query_lower) or (email and email in query_lower):
                return c

        # Check keywords for duplicate charge / subscription
        if "twice" in query_lower or "duplicate" in query_lower or "49" in query_lower:
            for c in customers:
                if c.get("customer_id") == "USR-10492":
                    return c

    # Default to Alex Morgan for demo complaints
    for c in customers:
        if c.get("customer_id") == "USR-10492":
            return c

    return customers[0] if customers else None


def verify_customer_billing(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Agent verifies customer account data in data/customer_accounts.csv:
    Checks if customer was debited twice in a month and confirms overcharge.
    """
    customer = find_customer_by_id_or_query(customer_id, query)
    if not customer:
        return {"ok": False, "error": "Customer not found in database"}

    total_billed = float(customer.get("total_billed_this_month", "0").split()[0].replace("$", ""))
    monthly_rate = float(customer.get("monthly_rate", "0").split()[0].replace("$", ""))
    charges_count = int(customer.get("charges_this_month", "1"))
    has_duplicate = charges_count > 1 or "Yes" in customer.get("duplicate_verified", "")

    overcharged = max(0.0, total_billed - monthly_rate) if has_duplicate else 0.0

    return {
        "ok": True,
        "verified": True,
        "customer": customer,
        "customer_id": customer.get("customer_id"),
        "customer_name": customer.get("customer_name"),
        "customer_email": customer.get("customer_email"),
        "subscription_plan": customer.get("subscription_plan"),
        "monthly_rate": f"${monthly_rate:.2f}",
        "total_billed_this_month": f"${total_billed:.2f}",
        "charges_this_month": charges_count,
        "has_duplicate": has_duplicate,
        "overcharge_amount": f"${overcharged:.2f}",
        "transaction_1": customer.get("transaction_1"),
        "transaction_2": customer.get("transaction_2"),
        "refund_status": customer.get("refund_status"),
        "account_status": customer.get("account_status"),
        "summary": (
            f"Verified {customer.get('customer_name')} ({customer.get('customer_email')}) in data/customer_accounts.csv: "
            f"Debited {charges_count} times in current billing cycle (${total_billed:.2f} total vs. ${monthly_rate:.2f} plan fee). "
            f"Accidental duplicate charge of ${overcharged:.2f} CONFIRMED."
            if has_duplicate
            else f"Customer {customer.get('customer_name')} account verified in data/customer_accounts.csv."
        ),
    }


def execute_agent_resolution(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
    action: str = "Refund Initiated ($49.00 via Stripe API)",
    notes: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Agent executes autonomous refund and updates data/customer_accounts.csv:
    - Marks transaction_2 as Refunded
    - Updates total_billed_this_month to net rate ($49.00)
    - Sets refund_status to Refunded ($49.00)
    - Stamps refund_id REF-88219-AUTO
    - Sets account_balance to $0.00 (Settled)
    - Sets account_status to Active - In Good Standing
    - Stamps Troy as the updater with current timestamp
    """
    customers = read_all_customers()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    target = find_customer_by_id_or_query(customer_id, query)
    target_id = target.get("customer_id") if target else "USR-10492"

    updated_record: Optional[Dict[str, str]] = None

    for c in customers:
        if c.get("customer_id") == target_id:
            c["transaction_2"] = "TXN-9012 ($49.00 on 2026-10-21) [Refunded: REF-88219-AUTO]"
            c["total_billed_this_month"] = "49.00 (Net after $49.00 refund)"
            c["refund_status"] = "Refunded ($49.00 via Stripe API)"
            c["refund_id"] = "REF-88219-AUTO"
            c["account_balance"] = "$0.00 (Settled)"
            c["account_status"] = "Active - In Good Standing"
            c["last_verified_by"] = "Troy (Autonomous Resolution Agent)"
            c["updated_by"] = "Troy (Autonomous Resolution Agent)"
            c["last_updated"] = now_str
            c["notes"] = notes or "Autonomous refund executed by Troy. Accidental double renewal debit reversed in Stripe API."
            updated_record = c
            break

    if not updated_record and customers:
        updated_record = customers[0]

    write_all_customers(customers)
    logger.info(f"Customer account CSV updated: {target_id} refund executed and account settled by Troy.")

    return {
        "ok": True,
        "action": "agent_resolve",
        "customer": updated_record,
        "ticket": {
            # Backward-compat shim for frontend components expecting ticket structure
            "ticket_id": target.get("reference_order_id", "ORD-88219").replace("ORD-", "") if target else "88219",
            "order_id": target.get("reference_order_id", "ORD-88219") if target else "ORD-88219",
            "customer_id": updated_record.get("customer_id") if updated_record else "USR-10492",
            "customer_name": updated_record.get("customer_name") if updated_record else "Alex Morgan",
            "customer_email": updated_record.get("customer_email") if updated_record else "alex.morgan@gmail.com",
            "subscription_plan": updated_record.get("subscription_plan") if updated_record else "Pro Tier ($49.00/mo)",
            "status": "Resolved",
            "resolution_action": action,
            "refund_id": updated_record.get("refund_id", "REF-88219-AUTO") if updated_record else "REF-88219-AUTO",
            "account_status": updated_record.get("account_status", "Active - In Good Standing") if updated_record else "Active - In Good Standing",
            "account_balance": updated_record.get("account_balance", "$0.00 (Settled)") if updated_record else "$0.00 (Settled)",
            "customer_notified": "Yes - Email Dispatched (REF-88219-AUTO)",
        },
        "database_file": "data/customer_accounts.csv",
        "timestamp": now_str,
    }


def execute_manual_resolution(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
    notes: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Escalates the customer account in data/customer_accounts.csv for human manual review:
    Updates account_status to 'Escalated - Billing Specialist Audit'.
    """
    customers = read_all_customers()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    target = find_customer_by_id_or_query(customer_id, query)
    target_id = target.get("customer_id") if target else "USR-10492"

    updated_record: Optional[Dict[str, str]] = None

    for c in customers:
        if c.get("customer_id") == target_id:
            c["account_status"] = "Escalated - Billing Specialist Audit"
            c["last_verified_by"] = "Troy AI (Pre-verification complete)"
            c["updated_by"] = "Escalated by Troy -> Human Agent"
            c["last_updated"] = now_str
            c["notes"] = notes or "Flagged for manual audit by human billing specialist. High priority queue."
            updated_record = c
            break

    if not updated_record and customers:
        updated_record = customers[0]

    write_all_customers(customers)
    logger.info(f"Customer account CSV updated: {target_id} escalated for manual review.")

    return {
        "ok": True,
        "action": "manual_resolve",
        "customer": updated_record,
        "ticket": {
            "ticket_id": target.get("reference_order_id", "ORD-88219").replace("ORD-", "") if target else "88219",
            "order_id": target.get("reference_order_id", "ORD-88219") if target else "ORD-88219",
            "customer_id": updated_record.get("customer_id") if updated_record else "USR-10492",
            "customer_name": updated_record.get("customer_name") if updated_record else "Alex Morgan",
            "customer_email": updated_record.get("customer_email") if updated_record else "alex.morgan@gmail.com",
            "subscription_plan": updated_record.get("subscription_plan") if updated_record else "Pro Tier ($49.00/mo)",
            "predicted_team": "Billing Team Specialist",
            "status": "Escalated - Manual Review",
            "account_status": "Escalated - Billing Specialist Audit",
            "sla_hours": "18.13",
            "customer_notified": "Yes - Escalation Notice Sent",
        },
        "database_file": "data/customer_accounts.csv",
        "timestamp": now_str,
    }


# Backwards compatibility aliases
read_all_tickets = read_all_customers
write_all_tickets = write_all_customers
find_ticket_by_id_or_query = find_customer_by_id_or_query
