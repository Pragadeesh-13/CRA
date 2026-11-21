"""Local SQLite queue for SDK requests requiring human review."""

import argparse
import json
import math
import os
import socket
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from app.utils.time_utils import _utcnow_iso

REVIEW_DB_ENV = "TROY_REVIEW_DB"


class UnscopedDeliveryHold(Exception):
    """An older delivery hold lacks enough information to match a customer safely."""


class ReviewQueue:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _connection(self, *, create=False):
        # Read-only CLI operations never create the database.
        if create:
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        uri = f"{self.path.resolve().as_uri()}?mode=rw"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            with db:
                if create:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""
                        CREATE TABLE IF NOT EXISTS review_cases (
                            case_id TEXT PRIMARY KEY, replay_key TEXT UNIQUE,
                            status TEXT NOT NULL, reason TEXT NOT NULL,
                            category TEXT NOT NULL, team TEXT NOT NULL, summary TEXT NOT NULL,
                            action TEXT, customer_id TEXT, order_id TEXT, transaction_id TEXT,
                            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, due_at TEXT
                        )
                    """)
                    if not self._has_due_date(db):
                        db.execute("ALTER TABLE review_cases ADD COLUMN due_at TEXT")
                yield db

    @staticmethod
    def _has_due_date(db):
        return any(column["name"] == "due_at" for column in db.execute("PRAGMA table_info(review_cases)"))

    def find(self, replay_key):
        if not replay_key or not self.path.exists():
            return None
        with self._connection() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'review_cases'").fetchone() is None:
                return None
            row = db.execute("SELECT * FROM review_cases WHERE replay_key = ?", (replay_key,)).fetchone()
            return dict(row) if row else None

    def enqueue(self, replay_key, *, reason, category, team, summary, action, customer_id, order_id,
                transaction_id, sla_hours=None):
        case_id = str(uuid.uuid4())
        now = _utcnow_iso()
        due_at = None
        if sla_hours is not None and math.isfinite(sla_hours) and sla_hours >= 0:
            try:
                due_at = (datetime.fromisoformat(now) + timedelta(hours=sla_hours)).isoformat(timespec="microseconds")
            except OverflowError:
                pass
        with self._connection(create=True) as db:
            return self._insert_case(
                db, replay_key, case_id=case_id, reason=reason, category=category, team=team, summary=summary,
                action=action, customer_id=customer_id, order_id=order_id, transaction_id=transaction_id,
                now=now, due_at=due_at,
            )

    @staticmethod
    def _insert_case(db, replay_key, *, case_id, reason, category, team, summary, action, customer_id,
                     order_id, transaction_id, now, due_at=None):
        inserted = db.execute("""
            INSERT INTO review_cases (
                case_id, replay_key, status, reason, category, team, summary, action,
                customer_id, order_id, transaction_id, created_at, updated_at, due_at
            ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(replay_key) DO NOTHING
        """, (case_id, replay_key, reason, category, team, summary, action, customer_id,
              order_id, transaction_id, now, now, due_at)).rowcount
        row = db.execute(
            "SELECT * FROM review_cases WHERE case_id = ? OR replay_key = ?", (case_id, replay_key)
        ).fetchone()
        return dict(row), bool(inserted)

    def claim_delivery(self, replay_key, *, delivery_key=None, legacy_key=None, category=None, team=None, possible_action=None,
                       customer_id=None, order_id=None, transaction_id=None):
        token = str(uuid.uuid4())
        namespace, _, delivery = (delivery_key or "").partition(":")
        provider, _, event_id = delivery.partition(":")
        details = {
            "worker_pid": os.getpid(),
            "worker_host": socket.gethostname(),
            "provider": provider if namespace == "webhook" else None,
            "delivery_id": event_id if namespace == "webhook" else None,
            "category": category,
            "team": team,
            "possible_action": possible_action,
            "customer_id": customer_id,
            "order_id": order_id,
            "transaction_id": transaction_id,
        }
        with self._connection(create=True) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS review_delivery_claims (
                    replay_key TEXT PRIMARY KEY, token TEXT NOT NULL, details TEXT
                )
            """)
            columns = db.execute("PRAGMA table_info(review_delivery_claims)")
            if not any(column["name"] == "details" for column in columns):
                db.execute("ALTER TABLE review_delivery_claims ADD COLUMN details TEXT")
            if legacy_key and db.execute("""
                SELECT 1 FROM review_delivery_claims WHERE replay_key = :key
                UNION ALL
                SELECT 1 FROM review_cases
                WHERE replay_key = :key AND customer_id IS NULL AND reason = 'action_unconfirmed'
            """, {"key": legacy_key}).fetchone():
                raise UnscopedDeliveryHold
            details["claimed_at"] = _utcnow_iso()
            inserted = db.execute("""
                INSERT INTO review_delivery_claims (replay_key, token, details) VALUES (?, ?, ?)
                ON CONFLICT(replay_key) DO NOTHING
            """, (replay_key, token, json.dumps(details))).rowcount
        return token if inserted else None

    def release_delivery(self, replay_key, token):
        with self._connection() as db:
            db.execute("DELETE FROM review_delivery_claims WHERE replay_key = ? AND token = ?", (replay_key, token))

    def list_claims(self):
        if not self.path.exists():
            return []
        with self._connection() as db:
            db.execute("BEGIN")
            tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "review_delivery_claims" not in tables:
                return []
            cases = {}
            if "review_cases" in tables:
                cases = {row["replay_key"]: row for row in db.execute("""
                    SELECT c.replay_key, c.case_id, c.status FROM review_cases c
                    JOIN review_delivery_claims d ON c.replay_key = d.replay_key
                """)}
            claims = []
            for row in db.execute("SELECT * FROM review_delivery_claims"):
                claim = dict(row)
                details = json.loads(claim.get("details") or "{}")
                case = cases.get(claim["replay_key"])
                claims.append({
                    "claim_id": claim["token"],
                    **{field: details.get(field) for field in (
                        "claimed_at", "worker_pid", "worker_host", "provider", "delivery_id",
                        "category", "team", "possible_action", "customer_id", "order_id", "transaction_id",
                    )},
                    "case_id": case["case_id"] if case else None,
                    "case_status": case["status"] if case else None,
                })
        return sorted(claims, key=lambda claim: (claim["claimed_at"] or "", claim["claim_id"]))

    def _recover_claim(self, claim_id):
        """Replace the exact claim with a durable review case in one transaction."""
        if not self.path.exists():
            raise ValueError("Unknown delivery claim ID.")
        with self._connection(create=True) as db:
            if not db.execute("PRAGMA table_info(review_delivery_claims)").fetchone():
                raise ValueError("Unknown delivery claim ID.")
            rows = db.execute("SELECT * FROM review_delivery_claims WHERE token = ?", (claim_id,)).fetchmany(2)
            if len(rows) != 1:
                raise ValueError("Unknown or ambiguous delivery claim ID.")

            claim = dict(rows[0])
            details = json.loads(claim.get("details") or "{}")
            summary = "Delivery processing was interrupted. Verify the action with the provider or ledger."
            if details.get("provider") and details.get("delivery_id"):
                summary += f" Provider: {details['provider']}. Delivery ID: {details['delivery_id']}."
            case, created = self._insert_case(
                db, claim["replay_key"], case_id=str(uuid.uuid4()), reason="action_unconfirmed",
                category=details.get("category") or "Unclassified", team=details.get("team") or "Manual Review",
                summary=summary,
                action=None, customer_id=details.get("customer_id"), order_id=details.get("order_id"),
                transaction_id=details.get("transaction_id"), now=_utcnow_iso(),
            )
            deleted = db.execute("DELETE FROM review_delivery_claims WHERE replay_key = ? AND token = ?",
                                 (claim["replay_key"], claim_id)).rowcount
            if deleted != 1:
                raise ValueError("The delivery claim was not cleared. The recovery was rolled back.")
            return {"case_id": case["case_id"], "status": case["status"], "created": created}

    def list_open(self, *, team=None, overdue_only=False):
        return self._list_cases(status="open", team=team, overdue_only=overdue_only)

    def _list_cases(self, *, status, team=None, overdue_only=False):
        if not self.path.exists():
            return []
        now = datetime.fromisoformat(_utcnow_iso()).isoformat(timespec="microseconds")
        if team is not None:
            team = team.strip()
        with self._connection() as db:
            # Older queues have no captured SLA. Expose their deadline as unknown.
            deadline = "due_at" if self._has_due_date(db) else "NULL"
            rows = db.execute(f"""
                WITH matching_cases AS (
                SELECT case_id, status, reason, category, team, summary, action, customer_id,
                       order_id, transaction_id, created_at, updated_at, {deadline} AS due_at,
                       CASE WHEN status = 'open' THEN {deadline} <= :now END AS overdue
                FROM review_cases WHERE status = :status AND (:team IS NULL OR team = :team COLLATE NOCASE)
                )
                SELECT * FROM matching_cases WHERE NOT :overdue_only OR overdue
                ORDER BY COALESCE(overdue, 0) DESC, created_at, case_id
            """, {"status": status, "team": team, "overdue_only": overdue_only, "now": now})
            return [
                {**dict(row), "overdue": None if row["overdue"] is None else bool(row["overdue"])}
                for row in rows
            ]

    def mark(self, case_id, status):
        if status not in {"reviewed", "closed"}:
            raise ValueError("Expected reviewed or closed.")
        with self._connection() as db:
            row = db.execute("""
                UPDATE review_cases SET status = ?, updated_at = ?
                WHERE case_id = ? AND (status = 'open' OR (status = 'reviewed' AND ? = 'closed'))
            """, (status, _utcnow_iso(), case_id, status))
            if row.rowcount:
                return
            current = db.execute("SELECT status FROM review_cases WHERE case_id = ?", (case_id,)).fetchone()
            if current is None:
                raise ValueError("Unknown case ID.")
            if current["status"] != status:
                raise ValueError(f"Case is already {current['status']}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get(REVIEW_DB_ENV), help=f"Queue database (default: {REVIEW_DB_ENV})")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="List open or reviewed cases as JSON")
    listing.add_argument("--status", choices=("open", "reviewed"), default="open", help="Case status (default: open)")
    listing.add_argument("--team", help="Filter by team name (case-insensitive)")
    listing.add_argument("--overdue", action="store_true", help="Only show cases whose SLA deadline has passed")
    commands.add_parser("claims", help="List persisted delivery claims and worker details as JSON")
    recovery = commands.add_parser("recover-claim", help="Convert an interrupted delivery claim into a review case")
    recovery.add_argument("claim_id")
    recovery.add_argument("--confirm-worker-stopped", action="store_true", required=True,
                          help="Confirm the original worker cannot continue processing the delivery")
    for name in ("reviewed", "closed"):
        commands.add_parser(name, help=f"Mark a case {name}").add_argument("case_id")
    args = parser.parse_args()
    if not args.db:
        parser.error(f"Set {REVIEW_DB_ENV} or pass --db.")
    if args.command == "list" and args.overdue and args.status != "open":
        listing.error("--overdue applies to open cases.")
    queue = ReviewQueue(args.db)
    try:
        if args.command == "list":
            cases = queue._list_cases(status=args.status, team=args.team, overdue_only=args.overdue)
            print(json.dumps(cases, indent=2))
        elif args.command == "claims":
            print(json.dumps(queue.list_claims(), indent=2))
        elif args.command == "recover-claim":
            print(json.dumps(queue._recover_claim(args.claim_id)))
        else:
            queue.mark(args.case_id, args.command)
            print(json.dumps({"case_id": args.case_id, "status": args.command}))
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Review queue: {exc}\n")


if __name__ == "__main__":
    main()
