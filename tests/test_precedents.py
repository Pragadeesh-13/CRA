import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import triage
from app.main import app
from app.precedents import CORPUS_SOURCE

ROOT = Path(__file__).resolve().parent.parent
NOTES = "  Rotated the synthetic delivery cursor, then retried.\n<em>Recorded as plain text.</em>"


def write_corpus(path, duplicate=False, ticket_id=710001):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticket_id", "issue_description", "category", "status", "resolution_notes", "assigned_team"])
        writer.writerows([
            [ticket_id, "Webhook cursor stalled during redelivery", "Integrations & API", "Resolved", NOTES, "Engineering"],
            [710002, "Webhook cursor stalled again", "Integrations & API", "Open", "Draft answer", "Engineering"],
            [710003, "Webhook cursor stalled after redeploy", "Integrations & API", "Cancelled", "Cancelled case", "Engineering"],
            [710004, "Webhook cursor stalled on Tuesday", "Integrations & API", "Closed", "", "Engineering"],
            [710005, "Invoice archive lacked receipt", "Billing", "Closed", "Restored receipt copy.", "Billing"],
        ])
        if duplicate:
            writer.writerow([710001, "Duplicate identifier", "Billing", "Closed", "Duplicate", "Billing"])


def run_import(csv_path, db_path, *options):
    return subprocess.run(
        [sys.executable, "-m", "app.precedents", str(csv_path), str(db_path), *options],
        cwd=ROOT, text=True, capture_output=True,
    )


@pytest.fixture
def imported_corpus(tmp_path):
    source, index = tmp_path / "synthetic.csv", tmp_path / "tickets #%.sqlite3"
    write_corpus(source)
    completed = run_import(source, index)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "Indexed 2 resolved tickets."
    return source, index


@pytest.fixture
def triage_client(monkeypatch):
    class OfflineDriver:
        async def complete(self, messages, max_tokens):
            return json.dumps({
                "predicted_category": "Integrations & API",
                "similar_cases": [{"ticket_id": "made-up", "similarity": 0.99}],
            })
    monkeypatch.setattr(triage, "get_triage_driver", lambda driver: OfflineDriver())
    monkeypatch.setattr(triage, "_TRIAGE_RETRY_AT", 0)
    return TestClient(app)


def test_import_and_triage_http(imported_corpus, triage_client, monkeypatch):
    _, index = imported_corpus
    monkeypatch.setenv("TROY_PRECEDENT_INDEX", str(index))
    data = triage_client.post("/api/cra/triage", json={"complaint": 'Webhook cursor redelivery " OR *'}).json()["report"]
    assert data["case_source"] == CORPUS_SOURCE
    assert data["case_search_status"] == "matched"
    assert [case["ticket_id"] for case in data["similar_cases"]] == [710001]
    case = data["similar_cases"][0]
    assert case["source"] == CORPUS_SOURCE
    assert case["resolution_notes"] == case["resolution"] == NOTES
    assert "similarity" not in case
    assert [case["ticket_id"] for case in triage.triage_complaint("Invoice archive")["similar_cases"]] == [710005]

    result = triage_client.post("/api/cra/triage", json={"complaint": "zznomatchzz"}).json()["report"]
    assert result["case_search_status"] == "no_matches"
    assert not result["similar_cases"]

    missing = index.with_name("missing.sqlite3")
    monkeypatch.setenv("TROY_PRECEDENT_INDEX", str(missing))
    result = triage_client.post("/api/cra/triage", json={"complaint": "Webhook cursor"}).json()["report"]
    assert result["case_search_status"] == "unavailable"
    assert not result["similar_cases"]
    assert not missing.exists()

    monkeypatch.delenv("TROY_PRECEDENT_INDEX")
    result = triage_client.post("/api/cra/triage", json={"complaint": "Webhook cursor"}).json()["report"]
    assert result["case_source"] == "static_category_examples"
    assert result["similar_cases"]
    assert all("similarity" not in case for case in result["similar_cases"])


def test_failed_rebuild_preserves_index(imported_corpus):
    source, index = imported_corpus
    before = index.read_bytes()
    assert run_import(source, index).returncode == 1
    write_corpus(source, duplicate=True)
    failed = run_import(source, index, "--replace")
    assert failed.returncode == 1
    assert "Duplicate ticket ID" in failed.stderr
    assert index.read_bytes() == before


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required to exercise the console renderer")
@pytest.mark.parametrize("ticket_id", [710001, 9007199254740993])
def test_console_renders_actual_http_ticket_as_text(imported_corpus, triage_client, monkeypatch, ticket_id):
    source, index = imported_corpus
    if ticket_id != 710001:
        write_corpus(source, ticket_id=ticket_id)
        assert run_import(source, index, "--replace").returncode == 0
    monkeypatch.setenv("TROY_PRECEDENT_INDEX", str(index))
    report = triage_client.post("/api/cra/triage", json={"complaint": "Webhook cursor"}).json()["report"]
    result = subprocess.run(["node", "-e", r"""
const fs = require("node:fs");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const source = fs.readFileSync("public/js/app.js", "utf8");
const start = source.indexOf("const renderCaseReferences");
const end = source.indexOf("const deliverIntakeSummary", start);
assert.ok(source.includes('.append(renderCaseReferences(report))'));
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; }
  append(...children) { this.children.push(...children); }
  set innerHTML(_) { throw Error("Corpus fields must be rendered as text"); }
}
const { report, expectedId } = JSON.parse(fs.readFileSync(0, "utf8"));
const document = { createElement: (tag) => new Element(tag) };
const render = vm.runInNewContext(source.slice(start, end) + "\nrenderCaseReferences", { document });
const section = render(report);
assert.equal(section.children[0].textContent, "Local resolved-ticket corpus");
const [ticket, description, notes] = section.children[1].children[0].children;
assert.equal(ticket.textContent, "Ticket #" + expectedId);
assert.equal(description.textContent, "Webhook cursor stalled during redelivery");
assert.equal(notes.textContent, "Resolution notes: " + report.similar_cases[0].resolution_notes);
assert.equal(render({}).children[0].textContent, "Static category examples");
assert.equal(render({...report, similar_cases: [], case_search_status: "no_matches"}).children[1].textContent,
  "No matching resolved tickets.");
"""], cwd=ROOT, input=json.dumps({"report": report, "expectedId": str(ticket_id)}), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_truncated_csv_cannot_replace_precedent_index(imported_corpus):
    source, index = imported_corpus
    before = index.read_bytes()
    source.write_text(
        'ticket_id,issue_description,category,status,resolution_notes\n'
        '810001,Webhook cursor stalled,Integrations & API,Resolved,"Truncated note\n'
        '810002,Invoice archive lacked receipt,Billing,Closed,Restored receipt.\n',
        encoding="utf-8",
    )

    failed = run_import(source, index, "--replace")

    assert failed.returncode == 1
    assert "Import failed: unexpected end of data" in failed.stderr
    assert index.read_bytes() == before


@pytest.mark.parametrize("row", [
    "810001,Webhook cursor stalled,Integrations & API,Resolved,Rotated the cursor, then retried.\n",
    "810001,Webhook cursor stalled,Integrations & API,Resolved\n",
])
def test_csv_with_wrong_field_count_cannot_replace_precedent_index(imported_corpus, row):
    source, index = imported_corpus
    before = index.read_bytes()
    source.write_text(
        "ticket_id,issue_description,category,status,resolution_notes\n" + row,
        encoding="utf-8",
    )

    failed = run_import(source, index, "--replace")

    assert failed.returncode == 1
    assert "CSV line 2 has a different number of fields than the header." in failed.stderr
    assert index.read_bytes() == before


def test_csv_with_duplicate_header_cannot_replace_precedent_index(imported_corpus):
    source, index = imported_corpus
    before = index.read_bytes()
    source.write_text(
        "ticket_id,issue_description,category,status,resolution_notes,resolution_notes\n"
        "810001,Webhook cursor stalled,Integrations & API,Resolved,Rotated cursor,Unrelated note\n",
        encoding="utf-8",
    )

    failed = run_import(source, index, "--replace")

    assert failed.returncode == 1
    assert "Duplicate CSV columns: resolution_notes" in failed.stderr
    assert index.read_bytes() == before
