import json
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.saas_routes import create_saas_router
from app.core.adapter import InMemoryDataAdapter
from app.review_queue import ReviewQueue
from app.sdk.client import TroyClient

ROOT = Path(__file__).resolve().parent.parent
REQUEST = "Please send invoice INV-20 for USR-10492"


def operator(db, *args):
    result = subprocess.run(
        [sys.executable, "-m", "app.review_queue", "--db", str(db), *args],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_opt_in_queue_and_operator_cli(tmp_path, monkeypatch):
    monkeypatch.delenv("TROY_REVIEW_DB", raising=False)
    default = TroyClient(driver=object()).resolve_ticket_sync(REQUEST)
    assert default["status"] == "needs_review"
    assert "review_case_id" not in default

    db = tmp_path / "review.sqlite3"
    monkeypatch.setenv("TROY_REVIEW_DB", str(db))
    agent = TroyClient(driver=object())
    assert agent.resolve_ticket_sync(REQUEST, dry_run=True)["dry_run"]
    assert not db.exists()
    assert agent.resolve_ticket_sync("How do I export my data?")["status"] == "resolved"
    assert not db.exists()

    result = agent.resolve_ticket_sync(REQUEST, idempotency_key="invoice-request")
    case_id = result["review_case_id"]
    assert result["review_queue_status"] == "open"
    assert case_id in result["customer_communication"]
    assert case_id in result["summary"]
    assert case_id in result["report"]["next_steps"][0]
    assert agent.adapter.audit_trail[-1]["review_case_id"] == case_id
    cases = operator(db, "list")
    assert len(cases) == 1 and cases[0]["case_id"] == case_id
    assert cases[0]["reason"] == "needs_review"
    assert {"complaint", "context", "replay_key"}.isdisjoint(cases[0])
    assert operator(db, "reviewed", case_id)["status"] == "reviewed"
    assert operator(db, "list") == []
    reviewed = operator(db, "list", "--status", "reviewed")
    assert [(case["case_id"], case["status"]) for case in reviewed] == [(case_id, "reviewed")]
    assert reviewed[0]["overdue"] is None
    assert operator(db, "list", "--status", "reviewed", "--team", reviewed[0]["team"].lower()) == reviewed
    assert operator(db, "list", "--status", "reviewed", "--team", "Unknown team") == []

    replay = TroyClient(driver=object()).resolve_ticket_sync(REQUEST, idempotency_key="invoice-request")
    assert replay["review_case_id"] == case_id and replay["review_case_replay"]
    assert replay["review_queue_status"] == "reviewed"
    assert "marked reviewed" in replay["customer_communication"]
    assert operator(db, "closed", case_id)["status"] == "closed"
    assert operator(db, "list", "--status", "reviewed") == []


def test_verified_webhook_case_survives_registry_restart(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "webhook_token", "relay-fixture")
    db = tmp_path / "review.sqlite3"
    calls = []

    def make_http():
        adapter = InMemoryDataAdapter(customers=[{"customer_id": "USR-10492", "invoice_id": "INV-20"}])
        agent = TroyClient(adapter=adapter, review_queue=ReviewQueue(db), driver=object())

        @agent.tool(name="send_invoice")
        def send_invoice(**params):
            calls.append(params)
            return {"ok": False, "status": "Failed"}

        app = FastAPI()
        app.include_router(create_saas_router(agent))
        return TestClient(app)

    path = "/api/v1/saas/webhooks/zendesk"
    body = {"ticket": {"id": 42, "description": REQUEST}, "config": {"product_name": "Request brand"}}
    headers = {"X-Troy-Webhook-Token": "relay-fixture", "X-Troy-Webhook-Event-Id": "delivery-one"}
    first = make_http().post(path, json=body, headers=headers).json()
    assert first["status"] == "action_failed"
    case_id = first["review_case_id"]

    replay_http = make_http()
    replay = replay_http.post(path, json=body, headers={**headers, "Idempotency-Key": "caller-key"}).json()
    assert replay["review_case_id"] == case_id and replay["review_case_replay"]
    assert replay["action_result"] is None
    assert len(calls) == 1
    operator(db, "closed", case_id)
    closed = replay_http.post(path, json=body, headers=headers).json()
    assert closed["review_queue_status"] == "closed"
    assert closed["report"]["next_steps"] == []

    distinct = replay_http.post(path, json=body, headers={**headers, "X-Troy-Webhook-Event-Id": "delivery-two"}).json()
    assert distinct["review_case_id"] != case_id
    assert len(calls) == 2
    assert [case["case_id"] for case in operator(db, "list")] == [distinct["review_case_id"]]


def test_write_failure_reports_no_queue_receipt(tmp_path):
    queue = ReviewQueue(tmp_path / "missing-parent" / "review.sqlite3")
    agent = TroyClient(review_queue=queue, driver=object())
    result = agent.resolve_ticket_sync(REQUEST)
    assert result["status"] == "needs_review"
    assert result["review_queue_status"] == "unavailable"
    assert "review_case_id" not in result
    assert "could not be recorded" in result["customer_communication"]


def test_concurrent_case_writes_keep_one_open_case(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    db = tmp_path / "review.sqlite3"

    def resolve(_):
        agent = TroyClient(review_queue=ReviewQueue(db), driver=object())
        return agent.resolve_ticket_sync(REQUEST, idempotency_key="same-request")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(resolve, range(8)))
    assert len({result["review_case_id"] for result in results}) == 1
    assert sum(not result["review_case_replay"] for result in results) == 1
    assert len(operator(db, "list")) == 1


def test_cli_lists_open_cases_by_team_and_sla(tmp_path, monkeypatch):
    import app.review_queue as queue_module

    db = tmp_path / "review.sqlite3"
    queue = ReviewQueue(db)
    agent = TroyClient(
        config={"categories": [{"name": "Billing", "team": "Billing", "sla_hours": 2.5},
                               {"name": "Integrations & APIs", "team": "Engineering", "sla_hours": 24}]},
        review_queue=queue, driver=object(),
    )
    monkeypatch.setattr(queue_module, "_utcnow_iso", lambda: "2020-01-01T10:00:00+00:00")
    billing_id = agent.resolve_ticket_sync(REQUEST, idempotency_key="billing")["review_case_id"]
    monkeypatch.setattr(queue_module, "_utcnow_iso", lambda: "2099-01-01T10:00:00+00:00")
    engineering_id = agent.resolve_ticket_sync("Please retry webhook EVT-40")["review_case_id"]

    billing, engineering = operator(db, "list")
    assert (billing["case_id"], billing["overdue"], billing["due_at"]) == (
        billing_id, True, "2020-01-01T12:30:00.000000+00:00",
    )
    assert (engineering["case_id"], engineering["overdue"]) == (engineering_id, False)
    assert operator(db, "list", "--team", "billing", "--overdue") == [billing]
    assert operator(db, "list", "--team", "Engineering", "--overdue") == []
    assert operator(db, "list", "--team", "engineering") == [engineering]

    monkeypatch.setattr(queue_module, "_utcnow_iso", lambda: "2020-01-01T12:29:59.999999+00:00")
    assert queue.list_open(overdue_only=True) == []
    monkeypatch.setattr(queue_module, "_utcnow_iso", lambda: "2020-01-01T12:30:00+00:00")
    assert queue.list_open(overdue_only=True) == [billing]
    assert agent.resolve_ticket_sync(REQUEST, idempotency_key="billing")["review_case_id"] == billing_id
    assert queue.list_open(overdue_only=True) == [billing]

    operator(db, "reviewed", billing_id)
    assert operator(db, "list", "--overdue") == []


def test_existing_queue_lists_and_migrates_without_inventing_deadlines(tmp_path):
    import sqlite3
    from contextlib import closing

    db = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute("""
            CREATE TABLE review_cases (
                case_id TEXT PRIMARY KEY, replay_key TEXT UNIQUE,
                status TEXT NOT NULL, reason TEXT NOT NULL,
                category TEXT NOT NULL, team TEXT NOT NULL, summary TEXT NOT NULL,
                action TEXT, customer_id TEXT, order_id TEXT, transaction_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            INSERT INTO review_cases (case_id, status, reason, category, team, summary, created_at, updated_at)
            VALUES ('old', 'open', 'needs_review', 'Billing', 'Billing', 'Existing case',
                    '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')
        """)
    original, = operator(db, "list", "--team", "Billing")
    assert original["due_at"] is None and original["overdue"] is None
    assert operator(db, "list", "--overdue") == []

    agent = TroyClient(review_queue=ReviewQueue(db), driver=object())
    new_id = agent.resolve_ticket_sync(REQUEST)["review_case_id"]
    cases = {case["case_id"]: case for case in operator(db, "list")}
    assert cases["old"] == original
    assert cases[new_id]["due_at"] is not None


def test_verified_delivery_replay_isolated_by_customer_and_provider(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "webhook_token", "relay-fixture")
    queue = ReviewQueue(tmp_path / "review.sqlite3")
    app = FastAPI()
    app.include_router(create_saas_router(TroyClient(review_queue=queue, driver=object())))
    with TestClient(app) as client:
        def post(provider, customer):
            response = client.post(f"/api/v1/saas/webhooks/{provider}", json={
                "complaint": f"Please send invoice INV-20 for {customer}",
            }, headers={"X-Troy-Webhook-Token": "relay-fixture", "X-Troy-Webhook-Event-Id": "shared"})
            return response.json()

        first = post("zendesk", "USR-10492")
        second = post("zendesk", "USR-10493")
        third = post("freshdesk", "USR-10492")
        assert len({row["review_case_id"] for row in [first, second, third]}) == 3
        assert post("zendesk", "USR-10492")["review_case_id"] == first["review_case_id"]


def test_simultaneous_verified_delivery_does_not_dispatch_or_queue_twice(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.config import settings

    monkeypatch.setattr(settings, "webhook_token", "relay-fixture")
    path = tmp_path / "review.sqlite3"
    started, finish = Event(), Event()
    calls = []

    def post():
        adapter = InMemoryDataAdapter(customers=[{"customer_id": "USR-10492", "invoice_id": "INV-20"}])
        agent = TroyClient(adapter=adapter, review_queue=ReviewQueue(path), driver=object())

        @agent.tool(name="send_invoice")
        def send_invoice(**params):
            calls.append(params)
            if len(calls) == 1:
                started.set()
                assert finish.wait(5)
            return {"status": "Sent"}

        app = FastAPI()
        app.include_router(create_saas_router(agent))
        with TestClient(app) as client:
            return client.post("/api/v1/saas/webhooks/zendesk", json={"complaint": REQUEST},
                               headers={"X-Troy-Webhook-Token": "relay-fixture", "X-Troy-Webhook-Event-Id": "shared"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        original = pool.submit(post)
        try:
            assert started.wait(5)
            duplicate = pool.submit(post).result(timeout=4)
            assert duplicate.status_code == 409
            assert duplicate.json()["status"] == "delivery_in_progress"
        finally:
            finish.set()
        assert original.result(timeout=5).json()["status"] == "resolved"
    assert len(calls) == 1
    assert ReviewQueue(path).list_open() == []


def test_concurrent_review_and_close_never_reopens_case(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "review.sqlite3"
    queue = ReviewQueue(path)
    agent = TroyClient(review_queue=queue, driver=object())
    case_id = agent.resolve_ticket_sync(REQUEST, idempotency_key="close-case")["review_case_id"]

    def mark_reviewed():
        try:
            ReviewQueue(path).mark(case_id, "reviewed")
        except ValueError as exc:
            assert str(exc) == "Case is already closed."

    with ThreadPoolExecutor(max_workers=2) as pool:
        review = pool.submit(mark_reviewed)
        close = pool.submit(ReviewQueue(path).mark, case_id, "closed")
        review.result()
        close.result()
    replay = agent.resolve_ticket_sync(REQUEST, idempotency_key="close-case")
    assert replay["review_case_id"] == case_id
    assert replay["review_queue_status"] == "closed"
    assert queue.list_open() == []
