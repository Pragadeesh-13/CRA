import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.saas_routes import create_saas_router
from app.config import settings
from app.core.adapter import InMemoryDataAdapter
from app.review_queue import ReviewQueue
from app.sdk.client import TroyClient

ROOT = Path(__file__).resolve().parent.parent
REQUEST = "Please send invoice INV-20 for USR-10492"


def cli(path, *args, check=True):
    return subprocess.run(
        [sys.executable, "-m", "app.review_queue", "--db", str(path), *args],
        cwd=ROOT, capture_output=True, text=True, check=check,
    )


def test_cli_recovers_interrupted_worker_without_repeat_action(tmp_path, monkeypatch):
    db, effect = tmp_path / "review.sqlite3", tmp_path / "synthetic-effect.txt"
    worker = subprocess.run([sys.executable, "-c", '''
import os, sys
from pathlib import Path
from app.core.adapter import InMemoryDataAdapter
from app.review_queue import ReviewQueue
from app.sdk.client import TroyClient

queue, effect, complaint = sys.argv[1:]
adapter = InMemoryDataAdapter(customers=[{"customer_id": "USR-10492", "invoice_id": "INV-20"}])
agent = TroyClient(adapter=adapter, review_queue=ReviewQueue(queue), driver=object())

@agent.tool(name="send_invoice")
def send_invoice(**params):
    Path(effect).write_text("Synthetic side effect")
    os._exit(73)

agent.resolve_ticket_sync(complaint, _verified_delivery_key="webhook:zendesk:interrupted")
''', str(db), str(effect), REQUEST], cwd=ROOT, capture_output=True, text=True)
    assert worker.returncode == 73, worker.stderr
    assert effect.read_text() == "Synthetic side effect"

    queue = ReviewQueue(db)
    adapter = InMemoryDataAdapter(customers=[{"customer_id": "USR-10492", "invoice_id": "INV-20"}])
    agent = TroyClient(adapter=adapter, review_queue=queue, driver=object())
    calls = []

    @agent.tool(name="send_invoice")
    def send_invoice(**params):
        calls.append(params)
        return {"status": "Sent"}

    monkeypatch.setattr(settings, "webhook_token", "synthetic-relay")
    app = FastAPI()
    app.include_router(create_saas_router(agent))
    http = TestClient(app)
    path = "/api/v1/saas/webhooks/zendesk"
    headers = {"X-Troy-Webhook-Token": "synthetic-relay", "X-Troy-Webhook-Event-Id": "interrupted"}

    def redeliver():
        return http.post(path, json={"complaint": REQUEST}, headers=headers)

    before = redeliver()
    assert before.status_code == 409
    assert before.json()["status"] == "delivery_in_progress"
    claim, = json.loads(cli(db, "claims").stdout)
    assert (claim["provider"], claim["delivery_id"], claim["customer_id"]) == ("zendesk", "interrupted", "USR-10492")
    assert claim["claimed_at"] and claim["worker_pid"] > 0
    assert claim["possible_action"] == "send_invoice"
    assert "complaint" not in claim and "replay_key" not in claim
    assert cli(db, "recover-claim", claim["claim_id"], check=False).returncode != 0
    assert queue.list_open() == []

    recovered = json.loads(cli(db, "recover-claim", claim["claim_id"], "--confirm-worker-stopped").stdout)
    assert json.loads(cli(db, "claims").stdout) == []
    case, = queue.list_open()
    assert case["case_id"] == recovered["case_id"]
    assert case["reason"] == "action_unconfirmed"
    assert case["customer_id"] == "USR-10492"
    assert "Delivery ID: interrupted" in case["summary"]

    replay = redeliver().json()
    assert replay["review_case_id"] == case["case_id"]
    assert replay["status"] == "action_unconfirmed"
    assert replay["action_result"] is None
    cli(db, "closed", case["case_id"])
    assert redeliver().json()["review_queue_status"] == "closed"
    assert not calls


def test_legacy_claim_recovery_preserves_existing_case_and_rejects_stale_handles(tmp_path):
    db = tmp_path / "review.sqlite3"
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute("CREATE TABLE review_delivery_claims (replay_key TEXT PRIMARY KEY, token TEXT NOT NULL)")
        connection.execute("INSERT INTO review_delivery_claims VALUES (?, ?)", ("durable-key", "legacy-claim"))

    old, = json.loads(cli(db, "claims").stdout)
    assert old["claim_id"] == "legacy-claim"
    assert old["claimed_at"] is None and old["worker_pid"] is None

    queue = ReviewQueue(db)
    case, _ = queue.enqueue("durable-key", reason="needs_review", category="Billing", team="Billing",
                            summary="Existing operator disposition", action=None,
                            customer_id=None, order_id=None, transaction_id=None)
    queue.mark(case["case_id"], "closed")
    recovered = json.loads(cli(db, "recover-claim", "legacy-claim", "--confirm-worker-stopped").stdout)
    assert recovered == {"case_id": case["case_id"], "status": "closed", "created": False}

    new_token = queue.claim_delivery("durable-key")
    assert new_token != old["claim_id"]
    assert cli(db, "recover-claim", old["claim_id"], "--confirm-worker-stopped", check=False).returncode != 0
    remaining, = json.loads(cli(db, "claims").stdout)
    assert remaining["claim_id"] == new_token
    assert queue.find("durable-key")["summary"] == "Existing operator disposition"


def test_verified_dispatch_keeps_claim_when_review_write_fails(tmp_path, monkeypatch):
    queue = ReviewQueue(tmp_path / "review.sqlite3")
    adapter = InMemoryDataAdapter(customers=[{"customer_id": "USR-10492", "invoice_id": "INV-20"}])
    agent = TroyClient(review_queue=queue, adapter=adapter, driver=object())
    calls = []

    @agent.tool(name="send_invoice")
    def send_invoice(**params):
        calls.append(params)
        return {"status": "Unconfirmed"}

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("Synthetic review write failure")

    monkeypatch.setattr(queue, "enqueue", fail)
    kwargs = {"_verified_delivery_key": "webhook:zendesk:lost-review"}
    result = agent.resolve_ticket_sync(REQUEST, **kwargs)
    assert result["review_queue_status"] == "unavailable"
    assert agent.resolve_ticket_sync(REQUEST, **kwargs)["status"] == "delivery_in_progress"
    assert len(calls) == 1
    assert len(json.loads(cli(queue.path, "claims").stdout)) == 1


def test_unscoped_legacy_claim_remains_held_without_exposing_case_receipt(tmp_path):
    import hashlib

    db = tmp_path / "review.sqlite3"
    delivery = "webhook:zendesk:legacy-event"
    key = hashlib.sha256(json.dumps(["delivery", delivery]).encode()).hexdigest()
    queue = ReviewQueue(db)
    claim = queue.claim_delivery(key)

    agent = TroyClient(review_queue=queue, driver=object())
    held = agent.resolve_ticket_sync(REQUEST, _verified_delivery_key=delivery)
    assert held["status"] == "delivery_requires_reconciliation"
    assert "review_case_id" not in held

    case = json.loads(cli(db, "recover-claim", claim, "--confirm-worker-stopped").stdout)
    assert case["created"] is True
    assert queue.find(key)["customer_id"] is None
    for customer in ("USR-10492", "USR-10493"):
        held = agent.resolve_ticket_sync(REQUEST, customer_id=customer, _verified_delivery_key=delivery)
        assert held["status"] == "delivery_requires_reconciliation"
        assert "review_case_id" not in held


def test_recovery_cannot_clear_claim_without_saving_case(tmp_path):
    db = tmp_path / "review.sqlite3"
    queue = ReviewQueue(db)
    claim = queue.claim_delivery("durable-key")
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute("""
            CREATE TRIGGER prevent_claim_deletion BEFORE DELETE ON review_delivery_claims
            BEGIN SELECT RAISE(IGNORE); END
        """)

    failed = cli(db, "recover-claim", claim, "--confirm-worker-stopped", check=False)
    assert failed.returncode == 1
    assert "rolled back" in failed.stderr
    assert queue.find("durable-key") is None
    remaining, = json.loads(cli(db, "claims").stdout)
    assert remaining["claim_id"] == claim
