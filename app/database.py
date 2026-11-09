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
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DB_LOCK = threading.RLock()

# Simple in-memory read cache for the CSV ledger. Entries are keyed by
# file path and validated against the file mtime + a short TTL, so test
# isolation (monkeypatched paths) and external writes are honoured.
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.RLock()
_CACHE_TTL_SECONDS = 5.0


def _invalidate_cache() -> None:
    """Clear the CSV read cache (called by all write operations)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_lookup(path: Path) -> Optional[List[Dict[str, str]]]:
    """Return a cached copy of ``path``'s records, or None on a miss."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        if entry.get("mtime") != mtime:
            return None
        if time.monotonic() - entry.get("_cache_ts", 0.0) > _CACHE_TTL_SECONDS:
            return None
        return [dict(r) for r in entry["records"]]


def _cache_store(path: Path, records: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Store ``records`` in the cache and return a caller-safe copy."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    with _CACHE_LOCK:
        _CACHE[str(path)] = {
            "mtime": mtime,
            "_cache_ts": time.monotonic(),
            "cache_ts": time.monotonic(),
            "records": [dict(r) for r in records],
        }
    return [dict(r) for r in records]

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

DEFAULT_CUSTOMER_ACCOUNTS: List[Dict[str, str]] = [
    {
        "customer_id": "USR-10492",
        "customer_name": "Alex Morgan",
        "customer_email": "alex.morgan@gmail.com",
        "reference_order_id": "ORD-88219",
        "subscription_plan": "Pro Tier ($49.00/mo)",
        "billing_cycle": "Monthly (20th)",
        "monthly_rate": "49.00",
        "total_billed_this_month": "49.00 (Net after $49.00 refund)",
        "charges_this_month": "2",
        "transaction_1": "TXN-9011 ($49.00 on 2026-10-20) [Settled Renewal]",
        "transaction_2": "TXN-9012 ($49.00 on 2026-10-23) [Refunded: REF-88219-AUTO]",
        "duplicate_verified": "Yes - Accidental Double Charge Detected",
        "refund_status": "Refunded ($49.00 via Stripe API)",
        "refund_id": "REF-88219-AUTO",
        "account_balance": "$0.00 (Settled)",
        "account_status": "Active - In Good Standing",
        "last_verified_by": "Troy (Autonomous Resolution Agent)",
        "updated_by": "Troy (Autonomous Resolution Agent)",
        "last_updated": "2026-10-23 02:20:53",
        "notes": "Autonomous override executed by Troy. Accidental double renewal debit reversed in Stripe API under REF-88219-AUTO.",
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
        "total_billed_this_month": "98.00",
        "charges_this_month": "2",
        "transaction_1": "TXN-7711 ($49.00 on 2026-09-18) [Settled Renewal]",
        "transaction_2": "TXN-7712 ($49.00 on 2026-09-18) [Settled - Accidental Second Debit]",
        "duplicate_verified": "Yes - Accidental Double Charge Detected",
        "refund_status": "Pending Refund",
        "refund_id": "",
        "account_balance": "$-49.00 (Overcharged)",
        "account_status": "Flagged - Accidental Double Charge",
        "last_verified_by": "Troy AI",
        "updated_by": "System Billing Daemon",
        "last_updated": "2026-09-18 10:00:00",
        "notes": "Accidental duplicate renewal charge debited twice within 24 hours. Awaiting agent resolution.",
    },
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
        "status": "Settled",
        "refund_id": "",
        "refund_amount": "",
        "refunded_at": "",
        "processed_by": "Stripe Gateway",
        "notes": "Montly Sub",
    },
]


def ensure_database_file() -> Path:
    """Ensures data directory, transactions CSV, and customer accounts CSV exist and are seeded."""
    with _DB_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not DATABASE_FILE.exists():
            with open(DATABASE_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                writer.writeheader()
                writer.writerows(DEFAULT_TRANSACTIONS)
            logger.info(f"Initialized transactions database at {DATABASE_FILE}")

        if not CUSTOMER_ACCOUNTS_FILE.exists():
            with open(CUSTOMER_ACCOUNTS_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CUSTOMER_ACCOUNT_FIELDNAMES)
                writer.writeheader()
                writer.writerows(DEFAULT_CUSTOMER_ACCOUNTS)
            logger.info(f"Initialized customer accounts database at {CUSTOMER_ACCOUNTS_FILE}")

        return DATABASE_FILE


def ensure_customer_accounts_file() -> Path:
    """Ensures customer accounts CSV exists and is seeded, returning its Path."""
    ensure_database_file()
    return CUSTOMER_ACCOUNTS_FILE


def read_all_transactions() -> List[Dict[str, str]]:
    """Reads all transaction rows from data/transactions.csv."""
    with _DB_LOCK:
        db_path = ensure_database_file()
        cached = _cache_lookup(db_path)
        if cached is not None:
            return cached
        records: List[Dict[str, str]] = []
        try:
            with open(db_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    records.append(dict(row))
        except Exception as e:
            logger.error(f"Error reading {db_path}: {e}")
        return _cache_store(db_path, records)


def write_all_transactions(records: List[Dict[str, str]]) -> bool:
    """Writes all transaction rows to data/transactions.csv atomically."""
    with _DB_LOCK:
        db_path = ensure_database_file()
        tmp_path = db_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(records)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, db_path)
            _invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"Error writing to {db_path}: {e}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False


def read_all_customer_accounts() -> List[Dict[str, str]]:
    """Reads all rows from data/customer_accounts.csv."""
    with _DB_LOCK:
        if not CUSTOMER_ACCOUNTS_FILE.exists():
            return []
        cached = _cache_lookup(CUSTOMER_ACCOUNTS_FILE)
        if cached is not None:
            return cached
        records = []
        try:
            with open(CUSTOMER_ACCOUNTS_FILE, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    records.append(dict(row))
        except Exception as e:
            logger.error(f"Error reading {CUSTOMER_ACCOUNTS_FILE}: {e}")
        return _cache_store(CUSTOMER_ACCOUNTS_FILE, records)


def write_all_customer_accounts(records: List[Dict[str, str]]) -> bool:
    """Writes all rows to data/customer_accounts.csv atomically."""
    with _DB_LOCK:
        CUSTOMER_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CUSTOMER_ACCOUNTS_FILE.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CUSTOMER_ACCOUNT_FIELDNAMES, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(records)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CUSTOMER_ACCOUNTS_FILE)
            _invalidate_cache()
            return True
        except Exception as e:
            logger.error(f"Error writing to {CUSTOMER_ACCOUNTS_FILE}: {e}")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False


def update_transaction(transaction_id: str, updates: Dict[str, Any]) -> bool:
    """
    Updates specified columns for a transaction record in data/transactions.csv.
    Returns True if record found and updated, False otherwise.
    """
    if not transaction_id or not updates:
        return False

    with _DB_LOCK:
        records = read_all_transactions()
        target_id = str(transaction_id).strip()
        matched = False

        for row in records:
            if row.get("transaction_id", "").strip() == target_id:
                for k, v in updates.items():
                    if k in FIELDNAMES:
                        row[k] = str(v) if v is not None else ""
                matched = True
                break

        if not matched:
            logger.warning(f"update_transaction: transaction_id '{transaction_id}' not found.")
            return False

        return write_all_transactions(records)


def update_customer_account(customer_id: str, updates: Dict[str, Any]) -> bool:
    """
    Updates specified columns for a customer account in data/customer_accounts.csv.
    Returns True if record found and updated, False otherwise.
    """
    if not customer_id or not updates:
        return False

    with _DB_LOCK:
        records = read_all_customer_accounts()
        target_id = str(customer_id).strip().upper()
        matched = False

        for row in records:
            row_id = row.get("customer_id", "").strip().upper()
            if row_id == target_id:
                for k, v in updates.items():
                    if k in CUSTOMER_ACCOUNT_FIELDNAMES:
                        row[k] = str(v) if v is not None else ""
                matched = True
                break

        if not matched:
            logger.warning(f"update_customer_account: customer_id '{customer_id}' not found.")
            return False

        return write_all_customer_accounts(records)


def remediate_duplicate_charge(
    order_id: str,
    duplicate_txn_id: str,
    refund_amount: str = "49.00",
    processed_by: str = "Troy (Autonomous Resolution Agent)",
    notes: str = "",
) -> Dict[str, Any]:
    """
    Atomically resolves an erroneous duplicate charge across transactions.csv and customer_accounts.csv.
    Updates transaction status to 'Refunded', sets refund_id, refunded_at, refund_amount, and processed_by.
    Reconciles customer account to '$0.00 (Settled)' balance and 'Active - In Good Standing' status.
    Idempotent: if transaction is already refunded, returns success without duplicate edits or payouts.
    """
    _invalidate_cache()
    if not duplicate_txn_id:
        return {
            "ok": False,
            "status": "Failed",
            "error": "duplicate_txn_id is required for remediation",
        }

    with _DB_LOCK:
        all_txns = read_all_transactions()
        all_accs = read_all_customer_accounts()

        # Pre-mutation snapshots for atomic rollback
        txns_backup = [dict(t) for t in all_txns]
        accs_backup = [dict(a) for a in all_accs]

        target_txn = next(
            (t for t in all_txns if t.get("transaction_id", "").strip() == str(duplicate_txn_id).strip()),
            None,
        )

        if not target_txn:
            logger.warning(f"remediate_duplicate_charge: transaction {duplicate_txn_id} not found.")
            return {
                "ok": False,
                "status": "Failed",
                "error": f"Transaction {duplicate_txn_id} not found in database.",
            }

        target_cust_id = target_txn.get("customer_id", "")
        txn_order_id = target_txn.get("order_id", "") or order_id
        effective_order_id = order_id or txn_order_id

        # Find customer account matching customer_id or order_id
        target_acc = None
        if target_cust_id:
            target_acc = next(
                (a for a in all_accs if a.get("customer_id", "").strip().upper() == target_cust_id.strip().upper()),
                None,
            )
        if not target_acc and effective_order_id:
            target_acc = next(
                (a for a in all_accs if a.get("reference_order_id", "").strip() == effective_order_id.strip()),
                None,
            )

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_order_num = re.sub(r"[^\d]", "", effective_order_id) if effective_order_id else ""
        suggested_ref_id = f"REF-{clean_order_num}-AUTO" if clean_order_num else f"REF-{duplicate_txn_id.replace('TXN-', '')}-AUTO"

        effective_refund_id = (
            target_txn.get("refund_id")
            or (target_acc.get("refund_id") if target_acc else "")
            or suggested_ref_id
        )
        effective_refund_amount = str(refund_amount) if refund_amount else (target_txn.get("amount") or "49.00")
        audit_processor = processed_by or "Troy (Autonomous Resolution Agent)"

        is_already_refunded = (target_txn.get("status") == "Refunded")

        if is_already_refunded:
            existing_ref_id = target_txn.get("refund_id") or effective_refund_id
            existing_ref_amt = target_txn.get("refund_amount") or effective_refund_amount
            existing_ref_date = target_txn.get("refunded_at") or now_str
            existing_processor = target_txn.get("processed_by") or audit_processor
            existing_notes = target_txn.get("notes") or notes or f"Autonomous refund reconfirmed for order {effective_order_id}."

            target_txn["refund_id"] = existing_ref_id
            target_txn["refund_amount"] = existing_ref_amt
            target_txn["processed_by"] = existing_processor
            if not target_txn.get("refunded_at"):
                target_txn["refunded_at"] = existing_ref_date

            if target_acc:
                target_acc["refund_status"] = f"Refunded (${existing_ref_amt} via Stripe API)"
                target_acc["refund_id"] = existing_ref_id
                target_acc["account_balance"] = "$0.00 (Settled)"
                target_acc["account_status"] = "Active - In Good Standing"
                target_acc["duplicate_verified"] = "Yes - Accidental Double Charge Detected"
                target_acc["total_billed_this_month"] = f"{existing_ref_amt} (Net after ${existing_ref_amt} refund)"
                target_acc["last_verified_by"] = audit_processor
                target_acc["updated_by"] = audit_processor
                target_acc["last_updated"] = now_str
                if notes:
                    target_acc["notes"] = notes

            tx_ok = write_all_transactions(all_txns)
            if not tx_ok:
                logger.error("remediate_duplicate_charge: write transactions failed in idempotent path")
                return {
                    "ok": False,
                    "status": "Failed",
                    "error": "Failed to persist atomic updates to database files",
                }

            acc_ok = write_all_customer_accounts(all_accs)
            if not acc_ok:
                logger.error("remediate_duplicate_charge: write accounts failed in idempotent path; rolling back transactions")
                write_all_transactions(txns_backup)
                return {
                    "ok": False,
                    "status": "Failed",
                    "error": "Failed to persist atomic updates to database files",
                }

            logger.info(f"remediate_duplicate_charge: {duplicate_txn_id} was already refunded under {existing_ref_id}.")
            return {
                "ok": True,
                "status": "Refunded",
                "is_already_refunded": True,
                "already_refunded": True,
                "refund_id": existing_ref_id,
                "refund_amount": existing_ref_amt,
                "transaction_id": duplicate_txn_id,
                "order_id": effective_order_id,
                "customer_id": target_cust_id,
                "processed_by": existing_processor,
                "refunded_at": existing_ref_date,
                "notes": existing_notes,
                "action": "remediate_duplicate_charge",
                "message": f"Transaction {duplicate_txn_id} was already refunded under {existing_ref_id}. Reconfirmed without duplicate payout.",
            }

        # 1. Update transaction row
        target_txn["status"] = "Refunded"
        target_txn["refund_id"] = effective_refund_id
        target_txn["refund_amount"] = effective_refund_amount
        target_txn["refunded_at"] = now_str
        target_txn["processed_by"] = audit_processor
        target_txn["notes"] = notes if notes else f"Refunded by Troy: Duplicate subscription charge for order {effective_order_id}."

        # 2. Update customer account row
        if target_acc:
            target_acc["refund_status"] = f"Refunded (${effective_refund_amount} via Stripe API)"
            target_acc["refund_id"] = effective_refund_id
            target_acc["account_balance"] = "$0.00 (Settled)"
            target_acc["account_status"] = "Active - In Good Standing"
            target_acc["duplicate_verified"] = "Yes - Accidental Double Charge Detected"
            target_acc["total_billed_this_month"] = f"{effective_refund_amount} (Net after ${effective_refund_amount} refund)"
            if (
                duplicate_txn_id in target_acc.get("transaction_2", "")
                or not target_acc.get("transaction_2")
                or target_acc.get("transaction_2") == "None"
            ):
                target_acc["transaction_2"] = (
                    f"{duplicate_txn_id} (${effective_refund_amount} on {now_str[:10]}) [Refunded: {effective_refund_id}]"
                )
            target_acc["last_verified_by"] = audit_processor
            target_acc["updated_by"] = audit_processor
            target_acc["last_updated"] = now_str
            target_acc["notes"] = notes if notes else f"Autonomous override executed by {audit_processor}. Accidental double renewal debit reversed in Stripe API under {effective_refund_id}."

        # 3. Synchronous multi-table persistence with rollback
        tx_ok = write_all_transactions(all_txns)
        if not tx_ok:
            logger.error("remediate_duplicate_charge: write transactions failed; aborting customer account write.")
            return {
                "ok": False,
                "status": "Failed",
                "error": "Failed to persist atomic updates to database files",
            }

        acc_ok = write_all_customer_accounts(all_accs)
        if not acc_ok:
            logger.error("remediate_duplicate_charge: write customer accounts failed; rolling back transactions.")
            write_all_transactions(txns_backup)
            return {
                "ok": False,
                "status": "Failed",
                "error": "Failed to persist atomic updates to database files",
            }

        logger.info(f"remediate_duplicate_charge: {duplicate_txn_id} refunded under {effective_refund_id}. Both tables synchronized.")
        return {
            "ok": True,
            "status": "Refunded",
            "is_already_refunded": False,
            "already_refunded": False,
            "refund_id": effective_refund_id,
            "refund_amount": effective_refund_amount,
            "transaction_id": duplicate_txn_id,
            "order_id": effective_order_id,
            "customer_id": target_cust_id,
            "processed_by": audit_processor,
            "refunded_at": now_str,
            "notes": target_txn["notes"],
            "action": "remediate_duplicate_charge",
            "message": f"Transaction {duplicate_txn_id} successfully refunded under {effective_refund_id} and account settled.",
        }


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

    target_id = str(customer_id).strip().upper() if customer_id and str(customer_id).strip() else None
    if target_id:
        for acc in accounts:
            acc_id = acc.get("customer_id", "").strip().upper()
            if target_id == acc_id:
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
    target_id = str(customer_id).strip().upper() if customer_id and str(customer_id).strip() else None

    # First match against customer_accounts.csv to resolve customer entity
    matched_acc = find_customer_account_by_query(query=query, customer_id=target_id)
    if matched_acc:
        acc_cust_id = matched_acc.get("customer_id")
        acc_order_id = matched_acc.get("reference_order_id")
        matched = [
            t for t in transactions
            if (acc_cust_id and t.get("customer_id", "").strip().upper() == acc_cust_id.strip().upper())
            or (acc_order_id and t.get("order_id", "").strip().upper() == acc_order_id.strip().upper())
        ]
        if matched:
            return matched

    # Fallback to direct customer_id match in transactions
    if target_id:
        matched = [t for t in transactions if t.get("customer_id", "").strip().upper() == target_id]
        if matched:
            return matched

    # Fallback to direct reference / order extraction from query
    if query:
        order_match = re.search(r"(?:ORD-)?(\d{4,6})", query, re.IGNORECASE)
        if order_match:
            ref_num = order_match.group(1)
            matched = [
                t for t in transactions
                if ref_num in t.get("order_id", "")
                or ref_num in t.get("transaction_id", "")
                or f"ORD-{ref_num}" == t.get("order_id", "").strip().upper()
            ]
            if matched:
                return matched

        query_lower = query.lower()
        for t in transactions:
            name = t.get("customer_name", "").lower()
            email = t.get("customer_email", "").lower()
            if (name and name in query_lower) or (email and email in query_lower):
                return [row for row in transactions if row.get("customer_id") == t.get("customer_id")]

    return []


def query_customer_account(
    customer_id: Optional[str] = None,
    email: Optional[str] = None,
    order_id: Optional[str] = None,
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """
    Generic inspection tool (Feature 6): Queries data/customer_accounts.csv by customer_id, email, or order_id.
    Returns customer account dict or None if not found or if all query arguments are empty.
    """
    cid = str(customer_id).strip().upper() if customer_id and str(customer_id).strip() else None
    em = str(email).strip().lower() if email and str(email).strip() else None
    oid = str(order_id).strip().upper() if order_id and str(order_id).strip() else None

    if not any([cid, em, oid]):
        return None

    accounts = read_all_customer_accounts()
    if not accounts:
        return None

    for acc in accounts:
        acc_cid = acc.get("customer_id", "").strip().upper()
        acc_em = acc.get("customer_email", "").strip().lower()
        acc_oid = acc.get("reference_order_id", "").strip().upper()

        if cid and acc_cid != cid:
            continue
        if em and acc_em != em:
            continue
        if oid:
            oid_num = oid.replace("ORD-", "")
            acc_oid_num = acc_oid.replace("ORD-", "")
            if acc_oid != oid and acc_oid_num != oid_num:
                continue

        return acc

    return None


def query_transactions(
    customer_id: Optional[str] = None,
    order_id: Optional[str] = None,
    status: Optional[str] = None,
    billing_cycle: Optional[str] = None,
    transaction_id: Optional[str] = None,
    **kwargs: Any,
) -> List[Dict[str, str]]:
    """
    Generic inspection tool (Feature 6): Queries data/transactions.csv with optional filters.
    Returns list of matching transaction dicts.
    """
    transactions = read_all_transactions()
    if not transactions:
        return []

    cid = str(customer_id).strip().upper() if customer_id is not None else None
    oid = str(order_id).strip().upper() if order_id is not None else None
    stat = str(status).strip().lower() if status is not None else None
    cycle = str(billing_cycle).strip().lower() if billing_cycle is not None else None
    tid = str(transaction_id).strip().upper() if transaction_id is not None else None

    results = []
    for t in transactions:
        t_cid = t.get("customer_id", "").strip().upper()
        t_oid = t.get("order_id", "").strip().upper()
        t_stat = t.get("status", "").strip().lower()
        t_cycle = t.get("billing_cycle", "").strip().lower()
        t_tid = t.get("transaction_id", "").strip().upper()

        if cid is not None and t_cid != cid:
            continue
        if oid is not None:
            oid_num = oid.replace("ORD-", "")
            t_oid_num = t_oid.replace("ORD-", "")
            if t_oid != oid and t_oid_num != oid_num:
                continue
        if stat is not None and t_stat != stat:
            continue
        if cycle is not None and t_cycle != cycle:
            continue
        if tid is not None and t_tid != tid:
            continue

        results.append(t)

    return results


def inspect_account_and_transactions(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Generic inspection tool (Feature 6): Combines customer account details, transaction history,
    duplicate debit detection flags, and billing cycle audit for the specified customer or query.
    """
    cid = str(customer_id).strip().upper() if customer_id and str(customer_id).strip() else None

    # 1. Locate customer account
    account = query_customer_account(customer_id=cid) if cid else None
    if not account and query:
        account = find_customer_account_by_query(query=query, customer_id=cid)

    target_cid = (account.get("customer_id") if account else None) or cid

    # 2. Locate transactions
    txns = query_transactions(customer_id=target_cid) if target_cid else []
    if not txns and (query or target_cid):
        txns = find_transactions_for_query(customer_id=target_cid, query=query)

    if not txns and not account:
        return {
            "ok": False,
            "error": "No account or transaction records found",
            "customer_id": target_cid or "",
            "account": None,
            "customer": None,
            "transactions": [],
            "has_duplicate": False,
            "duplicate_detected": False,
            "is_already_refunded": False,
        }

    # 3. Ledger audit
    first_txn = txns[0] if txns else {}
    c_name = (account.get("customer_name") if account else None) or first_txn.get("customer_name", "")
    c_email = (account.get("customer_email") if account else None) or first_txn.get("customer_email", "")
    plan = (account.get("subscription_plan") if account else None) or first_txn.get("subscription_plan", "")
    cycle = (account.get("billing_cycle") if account else None) or first_txn.get("billing_cycle", "")
    order_ref = (account.get("reference_order_id") if account else None) or first_txn.get("order_id", "")

    cycle_txns = [t for t in txns if t.get("billing_cycle") == cycle] if cycle else txns
    if not cycle_txns:
        cycle_txns = txns
        cycle = cycle_txns[0].get("billing_cycle", "") if cycle_txns else ""

    settled_txns = [t for t in cycle_txns if t.get("status", "").lower() == "settled"]
    refunded_txns = [t for t in cycle_txns if t.get("status", "").lower() == "refunded"]

    has_duplicate_settled = len(settled_txns) > 1
    is_already_refunded = len(refunded_txns) > 0 and len(settled_txns) <= 1
    has_duplicate = has_duplicate_settled or len(refunded_txns) > 0

    sorted_settled = sorted(settled_txns, key=lambda x: x.get("transaction_date", ""))
    retained_valid_txn = sorted_settled[0] if sorted_settled else (cycle_txns[0] if cycle_txns else None)
    candidate_to_refund = sorted_settled[1] if len(sorted_settled) > 1 else (refunded_txns[0] if refunded_txns else None)

    total_billed = sum(float(t.get("amount", "0")) for t in cycle_txns if t.get("amount"))

    cust_card = dict(account) if account else {
        "customer_id": target_cid or "",
        "customer_name": c_name,
        "customer_email": c_email,
        "reference_order_id": order_ref,
        "subscription_plan": plan,
        "billing_cycle": cycle,
        "charges_this_month": str(len(cycle_txns)),
        "total_billed_this_month": f"{total_billed:.2f}",
        "account_status": "Active - In Good Standing" if not has_duplicate_settled else "Flagged - Accidental Double Charge",
        "account_balance": "$0.00 (Settled)" if not has_duplicate_settled else f"$-{float(candidate_to_refund.get('amount', '49.00') if candidate_to_refund else 49.0):.2f} (Overcharged)",
    }

    return {
        "ok": True,
        "customer_id": target_cid,
        "customer_name": c_name,
        "customer_email": c_email,
        "account": account or cust_card,
        "customer": cust_card,
        "transactions": txns,
        "cycle_transactions": cycle_txns,
        "billing_cycle": cycle,
        "order_id": order_ref,
        "charges_this_month": len(cycle_txns),
        "total_billed_this_month": f"{total_billed:.2f}",
        "settled_count": len(settled_txns),
        "refunded_count": len(refunded_txns),
        "has_duplicate": has_duplicate,
        "duplicate_detected": has_duplicate_settled,
        "is_already_refunded": is_already_refunded,
        "retained_transaction": retained_valid_txn,
        "duplicate_transaction": candidate_to_refund,
    }


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
        "is_refund": True,
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
        steps = txn_analysis.get("steps", [])
        if not steps:
            steps = [
                {"text": "Accessing internal customer transaction ledger (data/transactions.csv)...", "delay": 650},
                {"text": "Auditing customer billing cycle records and transaction history...", "delay": 700},
                {"text": "Verifying transaction settlement status and duplicate charge flags...", "delay": 750},
                {"text": "Preparing automated refund authorization via Stripe Gateway API...", "delay": 750},
                {"text": "Updating database records to reflect refund reversal status...", "delay": 700},
                {"text": "Dispatching customer confirmation notification...", "delay": 600},
            ]
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
            "steps": steps,
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
    _invalidate_cache()
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
    _invalidate_cache()
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
read_all_customers = read_all_customer_accounts
read_all_tickets = read_all_transactions
write_all_customers = write_all_customer_accounts
write_all_tickets = write_all_transactions
find_customer_by_id_or_query = find_transactions_for_query
find_ticket_by_id_or_query = find_transactions_for_query
