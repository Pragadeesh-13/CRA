"""
Database access and operations for Complaint Resolution Agent (CRA).
Manages complaints and tickets in data/complaints_database.csv.
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
DATABASE_FILE = DATA_DIR / "complaints_database.csv"

FIELDNAMES = [
    "ticket_id",
    "customer_id",
    "customer_name",
    "customer_email",
    "order_id",
    "category",
    "predicted_team",
    "priority",
    "amount",
    "issue_description",
    "status",
    "resolution_action",
    "resolution_type",
    "resolved_by",
    "resolved_at",
    "sla_hours",
    "customer_notified",
    "notes",
]

DEFAULT_RECORDS = [
    {
        "ticket_id": "88219",
        "customer_id": "CUST-10492",
        "customer_name": "Alex Morgan",
        "customer_email": "alex.morgan@gmail.com",
        "order_id": "ORD-88219",
        "category": "Subscription",
        "predicted_team": "Billing Team",
        "priority": "High",
        "amount": "49.00",
        "issue_description": "I was charged twice for my subscription renewal this month. Order #88219 was debited for $49.00 yesterday and again this morning. Please investigate and refund the duplicate charge immediately.",
        "status": "Open",
        "resolution_action": "Pending Action",
        "resolution_type": "Pending",
        "resolved_by": "Unassigned",
        "resolved_at": "",
        "sla_hours": "18.13",
        "customer_notified": "No",
        "notes": "Awaiting resolution decision: manual review or autonomous override",
    },
    {
        "ticket_id": "88220",
        "customer_id": "CUST-08914",
        "customer_name": "Sarah Jenkins",
        "customer_email": "sarah.j@enterprise.org",
        "order_id": "ORD-79110",
        "category": "Authentication & Access",
        "predicted_team": "Tier 1 Support",
        "priority": "High",
        "amount": "0.00",
        "issue_description": "Two-factor authentication codes are not arriving on my phone. Account is locked after three attempts.",
        "status": "Open",
        "resolution_action": "Pending Action",
        "resolution_type": "Pending",
        "resolved_by": "Unassigned",
        "resolved_at": "",
        "sla_hours": "4.00",
        "customer_notified": "No",
        "notes": "Auth timeout detected on SMS gateway",
    },
    {
        "ticket_id": "88221",
        "customer_id": "CUST-12053",
        "customer_name": "David Chen",
        "customer_email": "d.chen@fintech.io",
        "order_id": "ORD-93041",
        "category": "Integrations & API",
        "predicted_team": "Engineering",
        "priority": "Medium",
        "amount": "0.00",
        "issue_description": "Webhook delivery failing with 504 gateway timeout on /v1/payments endpoint during batch synchronization.",
        "status": "Open",
        "resolution_action": "Pending Action",
        "resolution_type": "Pending",
        "resolved_by": "Unassigned",
        "resolved_at": "",
        "sla_hours": "8.50",
        "customer_notified": "No",
        "notes": "Engineering investigation queued",
    },
    {
        "ticket_id": "88222",
        "customer_id": "CUST-14309",
        "customer_name": "Elena Rostova",
        "customer_email": "elena@globaltrade.de",
        "order_id": "ORD-85420",
        "category": "Billing & Invoicing",
        "predicted_team": "Billing Team",
        "priority": "Low",
        "amount": "120.00",
        "issue_description": "Need formal tax invoice and VAT receipt for Q3 enterprise subscription renewal for accounting records.",
        "status": "Open",
        "resolution_action": "Pending Action",
        "resolution_type": "Pending",
        "resolved_by": "Unassigned",
        "resolved_at": "",
        "sla_hours": "24.00",
        "customer_notified": "No",
        "notes": "Invoice generation pending",
    },
    {
        "ticket_id": "88223",
        "customer_id": "CUST-09148",
        "customer_name": "Michael Scott",
        "customer_email": "m.scott@dunder.com",
        "order_id": "ORD-86102",
        "category": "Subscription",
        "predicted_team": "Billing Team",
        "priority": "Medium",
        "amount": "79.00",
        "issue_description": "Subscription auto-renewed but user wanted to downgrade to Starter tier before billing date.",
        "status": "Open",
        "resolution_action": "Pending Action",
        "resolution_type": "Pending",
        "resolved_by": "Unassigned",
        "resolved_at": "",
        "sla_hours": "18.00",
        "customer_notified": "No",
        "notes": "Downgrade request under review",
    },
    {
        "ticket_id": "88215",
        "customer_id": "CUST-07231",
        "customer_name": "Emily Watson",
        "customer_email": "emily.w@consulting.co",
        "order_id": "ORD-81094",
        "category": "Subscription",
        "predicted_team": "Billing Team",
        "priority": "High",
        "amount": "49.00",
        "issue_description": "Double charged on annual renewal fee due to payment retry.",
        "status": "Resolved",
        "resolution_action": "Refund Initiated ($49.00 via Payment Gateway API)",
        "resolution_type": "Autonomous Agent Resolution",
        "resolved_by": "Troy (Lead Autonomous Resolution Agent)",
        "resolved_at": "2026-10-18 14:22:10",
        "sla_hours": "18.00",
        "customer_notified": "Yes - Email Dispatched",
        "notes": "Precedent Case: Autonomous refund authorized",
    },
    {
        "ticket_id": "88216",
        "customer_id": "CUST-11029",
        "customer_name": "Marcus Vance",
        "customer_email": "m.vance@techcorp.io",
        "order_id": "ORD-82415",
        "category": "Integrations & API",
        "predicted_team": "Engineering",
        "priority": "Medium",
        "amount": "0.00",
        "issue_description": "API rate limit exceeded during database migration.",
        "status": "Resolved",
        "resolution_action": "Rate Limit Temporarily Doubled to 2000 req/min",
        "resolution_type": "Autonomous Agent Resolution",
        "resolved_by": "Troy (Lead Autonomous Resolution Agent)",
        "resolved_at": "2026-10-19 09:15:45",
        "sla_hours": "8.00",
        "customer_notified": "Yes - Email Dispatched",
        "notes": "Precedent Case: Quota override applied",
    },
]


def ensure_database_file() -> Path:
    """Ensures data directory and sample database CSV file exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATABASE_FILE.exists():
        with open(DATABASE_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(DEFAULT_RECORDS)
        logger.info(f"Initialized sample database with {len(DEFAULT_RECORDS)} records at {DATABASE_FILE}")
    return DATABASE_FILE


def read_all_tickets() -> List[Dict[str, str]]:
    """Reads all ticket rows from the database CSV."""
    db_path = ensure_database_file()
    tickets: List[Dict[str, str]] = []
    try:
        with open(db_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tickets.append(dict(row))
    except Exception as e:
        logger.error(f"Error reading {db_path}: {e}")
    return tickets


def write_all_tickets(tickets: List[Dict[str, str]]) -> bool:
    """Writes ticket rows to the database CSV."""
    db_path = ensure_database_file()
    try:
        with open(db_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(tickets)
        return True
    except Exception as e:
        logger.error(f"Error writing to {db_path}: {e}")
        return False


def find_ticket_by_id_or_query(ticket_id: Optional[str] = None, query: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Finds a ticket by ID or matching reference in complaint text."""
    tickets = read_all_tickets()
    target_id = str(ticket_id).strip() if ticket_id else None

    # 1. Search by exact ticket_id
    if target_id:
        for t in tickets:
            if t.get("ticket_id") == target_id:
                return t

    # 2. Extract reference from query text (e.g. #88219 or Order #88219)
    if query:
        match = re.search(r"#?(\d{5,})", query)
        if match:
            extracted_id = match.group(1)
            for t in tickets:
                if t.get("ticket_id") == extracted_id or extracted_id in t.get("order_id", ""):
                    return t

        # Match substring in issue description
        query_lower = query.lower()
        for t in tickets:
            desc = t.get("issue_description", "").lower()
            if query_lower in desc or desc in query_lower:
                return t

    # Default to ticket 88219 if available
    for t in tickets:
        if t.get("ticket_id") == "88219":
            return t

    return tickets[0] if tickets else None


def execute_agent_resolution(
    ticket_id: Optional[str] = None,
    query: Optional[str] = None,
    action: str = "Refund Initiated ($49.00 via Payment Gateway API)",
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes autonomous resolution on the database record:
    Updates status to 'Resolved', stamps resolved_at, assigns Troy, records resolution action.
    """
    tickets = read_all_tickets()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    target = find_ticket_by_id_or_query(ticket_id, query)
    target_id = target.get("ticket_id") if target else (ticket_id or "88219")

    updated_record: Optional[Dict[str, str]] = None

    for t in tickets:
        if t.get("ticket_id") == target_id:
            t["status"] = "Resolved"
            t["resolution_action"] = action
            t["resolution_type"] = "Autonomous Agent Resolution"
            t["resolved_by"] = "Troy (Lead Autonomous Resolution Agent)"
            t["resolved_at"] = now_str
            t["customer_notified"] = "Yes - Email Dispatched"
            t["notes"] = notes or f"Autonomous refund override executed by Troy based on precedent match. API refund confirmed."
            updated_record = t
            break

    # If not found, create new record
    if not updated_record:
        updated_record = {
            "ticket_id": str(target_id),
            "customer_id": "CUST-10492",
            "customer_name": "Alex Morgan",
            "customer_email": "alex.morgan@gmail.com",
            "order_id": f"ORD-{target_id}",
            "category": "Subscription",
            "predicted_team": "Billing Team",
            "priority": "High",
            "amount": "49.00",
            "issue_description": query or "Subscription duplicate charge complaint.",
            "status": "Resolved",
            "resolution_action": action,
            "resolution_type": "Autonomous Agent Resolution",
            "resolved_by": "Troy (Lead Autonomous Resolution Agent)",
            "resolved_at": now_str,
            "sla_hours": "18.13",
            "customer_notified": "Yes - Email Dispatched",
            "notes": notes or "Autonomous resolution executed by Troy.",
        }
        tickets.append(updated_record)

    write_all_tickets(tickets)
    logger.info(f"Database updated: Ticket #{target_id} marked as Resolved by Troy.")
    return {
        "ok": True,
        "action": "agent_resolve",
        "ticket": updated_record,
        "database_file": "data/complaints_database.csv",
        "timestamp": now_str,
    }


def execute_manual_resolution(
    ticket_id: Optional[str] = None,
    query: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Escalates the ticket in the database for human manual review:
    Updates status to 'Escalated - Manual Review', assigns department queue.
    """
    tickets = read_all_tickets()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    target = find_ticket_by_id_or_query(ticket_id, query)
    target_id = target.get("ticket_id") if target else (ticket_id or "88219")

    updated_record: Optional[Dict[str, str]] = None

    for t in tickets:
        if t.get("ticket_id") == target_id:
            t["status"] = "Escalated - Manual Review"
            t["resolution_action"] = "Escalated to Billing Team for manual review"
            t["resolution_type"] = "Manual Review Escalation"
            t["resolved_by"] = "Escalated by Troy -> Human Agent"
            t["resolved_at"] = now_str
            t["customer_notified"] = "Yes - Escalation Notice Sent"
            t["notes"] = notes or "Flagged for manual review by human specialist. High priority queue."
            updated_record = t
            break

    if not updated_record:
        updated_record = {
            "ticket_id": str(target_id),
            "customer_id": "CUST-10492",
            "customer_name": "Alex Morgan",
            "customer_email": "alex.morgan@gmail.com",
            "order_id": f"ORD-{target_id}",
            "category": "Subscription",
            "predicted_team": "Billing Team",
            "priority": "High",
            "amount": "49.00",
            "issue_description": query or "Subscription duplicate charge complaint.",
            "status": "Escalated - Manual Review",
            "resolution_action": "Escalated to Billing Team for manual review",
            "resolution_type": "Manual Review Escalation",
            "resolved_by": "Escalated by Troy -> Human Agent",
            "resolved_at": now_str,
            "sla_hours": "18.13",
            "customer_notified": "Yes - Escalation Notice Sent",
            "notes": notes or "Flagged for manual review by human specialist.",
        }
        tickets.append(updated_record)

    write_all_tickets(tickets)
    logger.info(f"Database updated: Ticket #{target_id} marked as Escalated for Manual Review.")
    return {
        "ok": True,
        "action": "manual_resolve",
        "ticket": updated_record,
        "database_file": "data/complaints_database.csv",
        "timestamp": now_str,
    }
