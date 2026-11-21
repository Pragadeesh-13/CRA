import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.review_queue import ReviewQueue
from app.sdk.client import TroyClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required to execute the widget")
def test_widget_exposes_review_receipt_and_truthful_status(tmp_path):
    complaint = "Please send invoice INV-20 for USR-10492"
    queue = ReviewQueue(tmp_path / "review.sqlite3")
    agent = TroyClient(review_queue=queue, driver=object())
    opened = agent.resolve_ticket_sync(complaint, idempotency_key="widget")
    queue.mark(opened["review_case_id"], "closed")
    closed = agent.resolve_ticket_sync(complaint, idempotency_key="widget")
    unavailable = TroyClient(
        review_queue=ReviewQueue(tmp_path / "missing" / "review.sqlite3"), driver=object(),
    ).resolve_ticket_sync(complaint)

    result = subprocess.run(["node", "-e", r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const fixtures = JSON.parse(fs.readFileSync(0, "utf8"));
class Node {
  constructor(tag) {
    this.tag = tag; this.children = []; this.listeners = {}; this.value = "";
    this.classList = new Set();
    this.classList.remove = this.classList.delete.bind(this.classList);
  }
  set textContent(value) { this.text = value; this.children.length = 0; }
  get textContent() { return (this.text || "") + this.children.map(node => node.textContent).join(""); }
  appendChild(node) { this.children.push(node); return node; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute() {}
  focus() {}
}
function descendants(root) { return [root, ...root.children.flatMap(descendants)]; }
const document = {
  readyState: "complete", currentScript: null, body: new Node("body"),
  createElement: tag => new Node(tag), getElementsByTagName: () => [], addEventListener() {},
};
const source = fs.readFileSync("app/static/widget/troy-embed.js", "utf8");
let response;
const context = {document, fetch: async () => ({ok: true, text: async () => JSON.stringify(response)})};
vm.runInNewContext(source, context);
const nodes = descendants(document.body);
const get = key => nodes.find(node => node.className === key);
const input = nodes.find(node => node.tag === "textarea");
const report = get("troy-report");
const button = get("troy-submit");
const state = get("troy-state");
(async () => {
  input.value = "Please send invoice INV-20 for USR-10492";
  for (const [data, label] of [
    [fixtures.opened, "Saved for review."],
    [fixtures.closed, "Review case closed."],
    [fixtures.unavailable, "Could not save the request for review."],
    [{ok: true, status: "needs_review"}, "Manual review needed."],
    [{ok: false, status: "action_failed", action_result: {}}, "Action needs review."],
    [{ok: true, status: "resolved"}, "Report ready."],
  ]) {
    response = data;
    button.listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(state.textContent, label);
    assert.equal(button.disabled, false);
    const metadata = report.children.find(node => node.className === "troy-meta");
    assert.equal(metadata.textContent.includes("Review case:"), Boolean(data.review_case_id));
    if (data.review_case_id) assert.ok(metadata.textContent.includes(data.review_case_id));
    assert.ok(!report.textContent.includes("Action: completed"));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""], cwd=ROOT, input=json.dumps({"opened": opened, "closed": closed, "unavailable": unavailable}),
        text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
