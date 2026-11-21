import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from app import main
from app.simulation.session_registry import SessionRegistry
from mock_driver import MockDriver


def decode(frame):
    event, payload = frame.strip().split("\n", 1)
    return event.removeprefix("event: "), json.loads(payload.removeprefix("data: "))


class SessionRequest:
    def __init__(self, identity):
        self.state = SimpleNamespace(session_user={"id": identity})

    async def is_disconnected(self):
        return False


async def read_initial_frames(response, count):
    return [decode(await asyncio.wait_for(anext(response.body_iterator), timeout=0.2)) for _ in range(count)]


@pytest_asyncio.fixture
async def completed_run(monkeypatch):
    from app import triage
    from app.simulation import troy

    driver = MockDriver()
    registry = SessionRegistry(SimpleNamespace(llm_stream=False, show_reasoning=False), driver)
    monkeypatch.setattr(main, "session_registry", registry)
    monkeypatch.setattr(main, "create_driver", lambda *args, **kwargs: driver)
    monkeypatch.setattr(main.guard, "_mode", "required")

    async def config(_):
        return {"provider": "lmstudio", "model": "offline"}

    async def speak(**_):
        return {"message": "Synthetic operator draft."}

    monkeypatch.setattr(main, "resolve_session_config", config)
    monkeypatch.setattr(troy, "execute_agent_with_reasoning", speak)
    monkeypatch.setattr(triage, "triage_complaint", lambda _: {
        "predicted_category": "Onboarding & How-To", "recommended_team": "Tier 1 Support",
        "category_confidence": 0.9, "sla": {"estimated_hours": 4}, "similar_cases": [],
    })

    request = SessionRequest("alice")
    session = main.get_active_session(request)
    initial = await main.stream_events(request)
    assert (await read_initial_frames(initial, 1))[0][0] == "status"
    await initial.body_iterator.aclose()
    assert session.sse_manager.client_count == 0

    token = main.guard.create_session({"id": "alice"})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            started = await client.post(
                "/api/next", json={"query": "How do I invite teammates?"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert started.json()["started"] is True
            await asyncio.wait_for(session.task, timeout=5)
            ticket = await client.get("/api/ticket", headers={"Authorization": f"Bearer {token}"})
            story = ticket.json()["ticket"]
            assert story["description"] == "How do I invite teammates?"
    finally:
        main.guard.revoke_session(token)
    return request, session, story


@pytest.mark.asyncio
async def test_reconnect_restores_latest_completed_report_and_isolates_sessions(completed_run):
    request, session, story = completed_run
    response = await main.stream_events(request)
    try:
        frames = dict(await read_initial_frames(response, 4))
    finally:
        await response.body_iterator.aclose()
    snapshot = frames["session_snapshot"]
    assert snapshot["story"] == story
    assert snapshot["running"] is False
    assert snapshot["resolution"]["execution_status"] == "demo_only"
    assert len(snapshot["resolution"]["sections"]) == 4
    assert "Synthetic operator draft" in snapshot["resolution"]["full_resolution"]
    assert session.state.ticket_history() == [story]

    other = await main.stream_events(SessionRequest("bob"))
    try:
        assert dict(await read_initial_frames(other, 2))["session_snapshot"] == {
            "story": None, "resolution": None, "running": False,
        }
    finally:
        await other.body_iterator.aclose()

    session.state.set_story(None)
    session.state.set_story({"title": "New ticket"})
    assert session.state.resolution_snapshot()["resolution"] is None
    assert len(session.state.ticket_history()) == 2


@pytest.mark.asyncio
async def test_retired_stream_closes_after_shutdown_notice(completed_run):
    request, session, _ = completed_run
    response = await main.stream_events(request)
    try:
        await read_initial_frames(response, 4)
        session.sse_manager.close_all("Session expired")
        assert (await read_initial_frames(response, 1))[0][0] == "server_shutdown"
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(response.body_iterator), timeout=0.2)
    finally:
        await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_reconnect_restores_terminal_error(completed_run, monkeypatch):
    request, session, _ = completed_run

    async def fail(_):
        raise RuntimeError("internal details")

    monkeypatch.setattr(session.orchestrator, "_run_pipeline", fail)
    assert not await session.orchestrator.run({"title": "Failed ticket"})
    response = await main.stream_events(request)
    try:
        result = dict(await read_initial_frames(response, 4))["session_snapshot"]["resolution"]
        assert result["error"] == "Resolution encountered an error. Check settings and retry."
    finally:
        await response.body_iterator.aclose()


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required to exercise the console handlers")
@pytest.mark.asyncio
async def test_console_live_completion_and_reconnect_use_same_render_path(completed_run):
    request, _, _ = completed_run
    response = await main.stream_events(request)
    try:
        frames = dict(await read_initial_frames(response, 4))
    finally:
        await response.body_iterator.aclose()

    result = subprocess.run(["node", "-e", r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const source = fs.readFileSync("public/js/app.js", "utf8");
const finish = source.slice(source.indexOf("const finishTransformedStream"), source.indexOf("const startStage"));
const connect = source.slice(source.indexOf("const connectLiveStream"), source.indexOf("// ── Input & Event Listeners"));
function harness() {
  let stream;
  const rendered = { sections: [] };
  const scope = {
    activeEventSource: null, activeStoryKey: null, activeTicketQuery: "",
    cachedCraReport: null, cachedResolutionEnd: null, cachedSplitParts: {},
    cachedResolutionText: null, streamedSpeechText: "", hasDeliveredIntakeSummary: false,
    isResolving: true, isIntakeCycling: false, isTypingTransformed: false, consoleRevision: 0,
    fetchNextBtn: {disabled: true, classList: {remove() {}}}, rendered,
    console,
    EventSource: class {
      constructor() { this.handlers = {}; stream = this; }
      addEventListener(name, fn) { this.handlers[name] = fn; }
    },
    resetTroyConsole() {
      scope.cachedResolutionEnd = null; scope.cachedCraReport = null;
      scope.cachedSplitParts = {}; scope.hasDeliveredIntakeSummary = false;
    },
    stopTransformedStreamTicker() {}, stopIntakeCycle() {},
    deliverIntakeSummary(report) { rendered.report = report; scope.hasDeliveredIntakeSummary = true; },
    renderSectionChatbox(index, text) { rendered.sections.push([index, text]); },
    renderResolutionActionButtons() { rendered.actionQuery = scope.activeTicketQuery; },
    updateLiveStatus(text) { rendered.status = text; },
    markFeedCardComplete(label) { rendered.badge = label; },
    setActiveFeedCard(story) { rendered.story = story.title; },
  };
  vm.runInNewContext(finish + connect + "\nconnectLiveStream();", scope);
  return { scope, emit(name, data) { stream.handlers[name]({data: JSON.stringify(data)}); } };
}
const live = harness();
live.emit("feed", input.feed);
live.emit("resolution_end", input.session_snapshot.resolution);
live.emit("council_end", input.session_snapshot.resolution);
const restored = harness();
restored.emit("feed", input.feed);
restored.emit("session_snapshot", input.session_snapshot);
restored.emit("feed", input.feed);
restored.emit("session_snapshot", input.session_snapshot);
assert.deepEqual(live.scope.rendered.sections, restored.scope.rendered.sections);
assert.equal(restored.scope.rendered.sections.length, 4);
assert.equal(restored.scope.rendered.report.predicted_category, "Onboarding & How-To");
assert.equal(restored.scope.rendered.badge, "Demo draft");
assert.equal(restored.scope.rendered.status, "Demo draft");
assert.equal(restored.scope.rendered.actionQuery, input.feed.description);
assert.equal(restored.scope.fetchNextBtn.disabled, false);
"""], input=json.dumps(frames), text=True, capture_output=True,
        cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required to exercise the console handlers")
def test_console_unlocks_when_reconnect_has_lost_the_active_run():
    result = subprocess.run(["node", "-e", r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("public/js/app.js", "utf8");
const flow = source.slice(source.indexOf("const startResolutionFlow"), source.indexOf("// ── SSE Stream Connection"));
const reconnect = source.slice(source.indexOf("const connectLiveStream"), source.indexOf("// ── Input & Event Listeners"));
const idle = { story: null, resolution: null, running: false };
const pending = { ...idle, running: true };
const completion = { ok: true, json: async () => ({ ok: true, started: true }) };

let stream;
let resolvePost;
let submissions = 0;
const warnings = [];
const button = { disabled: false, textContent: "Begin Resolution", classList: { add() {}, remove() {} } };
const elements = {
  "provider-select": { value: "lmstudio" },
  "model-input": { value: "offline-fixture" },
  "api-key-input": { value: "" },
};
const scope = {
  activeEventSource: null, activeStoryKey: null, activeTicketQuery: "",
  cachedResolutionEnd: null, isResolving: false, isSubmittingResolution: false, consoleRevision: 0,
  fetchNextBtn: button,
  topicInput: { value: "Synthetic complaint" }, PROVIDERS: { lmstudio: { requiresKey: false } },
  document: { getElementById: id => elements[id] },
  console,
  EventSource: class {
    constructor() { this.handlers = {}; stream = this; }
    addEventListener(name, handler) { this.handlers[name] = handler; }
  },
  fetch(path) {
    assert.equal(path, "/api/next");
    submissions++;
    return submissions === 1 ? new Promise(resolve => { resolvePost = resolve; }) : Promise.resolve(completion);
  },
  triggerInputError(_, message) { assert.fail(message); },
  showSummarizingFeedCard() {}, resetTroyConsole() {}, startIntakeCycle() {},
  setStatusTopic() {}, stopIntakeCycle() {}, stopTransformedStreamTicker() {},
  showTopWarning(message) { warnings.push(message); },
  markFeedCardComplete() {}, updateLiveStatus() {},
};
const context = vm.createContext(scope);
vm.runInContext(flow + reconnect + "\nconnectLiveStream();", context);
const snapshot = data => stream.handlers.session_snapshot({ data: JSON.stringify(data) });

(async () => {
  snapshot(idle);
  assert.equal(button.disabled, false);
  assert.deepEqual(warnings, []);

  const submission = vm.runInContext("startResolutionFlow()", context);
  assert.equal(button.disabled, true);
  snapshot(idle); // Initial snapshot raced the still-pending POST.
  assert.equal(button.disabled, true);
  assert.deepEqual(warnings, []);
  stream.handlers.error({}); // This interruption preceded the POST acknowledgment.
  resolvePost(completion);
  await submission;
  snapshot(idle); // The early interruption did not identify an accepted run.
  assert.equal(button.disabled, true);

  stream.handlers.error({}); // Browser transport error with no server payload.
  snapshot(pending);
  assert.equal(button.disabled, true);
  assert.deepEqual(warnings, []);
  stream.handlers.error({});
  snapshot(idle); // Reconnected to a session that no longer has the run.
  assert.equal(button.disabled, false);
  assert.equal(scope.isResolving, false);
  assert.match(warnings[0], /interrupted/i);

  await vm.runInContext("startResolutionFlow()", context);
  assert.equal(submissions, 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""], text=True, capture_output=True, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required to exercise the console handlers")
def test_console_provider_change_clears_key_before_loading_models():
    result = subprocess.run(["node", "-e", r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("public/js/app.js", "utf8");
const start = source.indexOf('providerSelect.addEventListener("change"');
const end = source.indexOf('modelSelect?.addEventListener("change"', start);
const apiKeyInput = { value: "previous-provider-fixture" };
let change;
let loaded;
vm.runInNewContext(source.slice(start, end), {
  apiKeyInput,
  providerSelect: { value: "anthropic", addEventListener(name, handler) { change = handler; } },
  clearInputError() {},
  dismissTopWarning() {},
  applyProvider(provider) { loaded = [provider, apiKeyInput.value]; },
});
change();
assert.deepEqual(loaded, ["anthropic", ""]);
"""], text=True, capture_output=True, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required to exercise the console handlers")
def test_console_submits_blank_key_for_server_side_resolution():
    result = subprocess.run(["node", "-e", r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("public/js/app.js", "utf8");
const flow = source.slice(source.indexOf("const startResolutionFlow"), source.indexOf("// ── SSE Stream Connection"));
const button = { disabled: false, textContent: "Begin Resolution", classList: { add() {}, remove() {} } };
const elements = {
  "provider-select": { value: "openrouter" },
  "model-select": { value: "synthetic-model" },
  "api-key-input": { value: "", placeholder: "••••1234" },
};
const submissions = [];
const warnings = [];
const scope = {
  activeStoryKey: null, activeTicketQuery: "", isResolving: false, isSubmittingResolution: false,
  fetchNextBtn: button, topicInput: { value: "Synthetic complaint" },
  PROVIDERS: { openrouter: {}, anthropic: {} },
  document: { getElementById: id => elements[id] },
  async fetch(path, request) {
    assert.equal(path, "/api/next");
    const submitted = JSON.parse(request.body);
    submissions.push(submitted);
    const ok = submitted.provider === "openrouter";
    return { ok, json: async () => ok ? { ok, started: true } : { ok, error: "API key has not been entered." } };
  },
  triggerInputError(_, message) { warnings.push(message); },
  showTopWarning(message) { warnings.push(message); },
  showSummarizingFeedCard() {}, resetTroyConsole() {}, startIntakeCycle() {},
  setStatusTopic() {}, stopIntakeCycle() {}, updateLiveStatus() {},
};
const context = vm.createContext(scope);
vm.runInContext(flow, context);
(async () => {
  await vm.runInContext("startResolutionFlow()", context);
  assert.deepEqual(submissions, [{
    query: "Synthetic complaint", provider: "openrouter", model: "synthetic-model", apiKey: "",
  }]);
  assert.deepEqual(warnings, []);

  scope.isResolving = false;
  elements["provider-select"].value = "anthropic";
  await vm.runInContext("startResolutionFlow()", context);
  assert.equal(submissions.length, 2);
  assert.deepEqual(warnings, ["API key has not been entered."]);
  assert.equal(button.disabled, false);
  assert.equal(scope.isResolving, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""], text=True, capture_output=True, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required to exercise the console handlers")
def test_console_keeps_saved_model_outside_discovery_results():
    result = subprocess.run(["node", "-e", r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("public/js/app.js", "utf8");
const config = source.slice(source.indexOf("const initProviderConfig"), source.indexOf("// ── User Profile Menu"));
const savedModel = "provider/custom-model";
const defaultModel = "provider/default-model";

async function check(discovered) {
  const provider = { value: "", addEventListener() {} };
  const model = {
    options: [],
    set innerHTML(_) { this.options = []; },
    appendChild(option) { this.options.push(option); },
    get value() { return this.options.find(option => option.selected)?.value || ""; },
    addEventListener() {},
  };
  const requests = [];
  let sync;
  let local;
  vm.runInNewContext(config + "\ninitProviderConfig();", {
    PROVIDERS: { openrouter: { defaultModel, exampleModels: [defaultModel] } },
    document: {
      getElementById: id => ({ "provider-select": provider, "model-select": model })[id],
      createElement: () => ({}),
    },
    clearInputError() {}, dismissTopWarning() {},
    localStorage: { setItem(_, value) { local = JSON.parse(value); } },
    URLSearchParams,
    clearTimeout() {},
    setTimeout(callback) { sync = callback; },
    async fetch(path, request) {
      if (request?.method === "POST") {
        requests.push(JSON.parse(request.body));
        return;
      }
      if (path === "/api/config") {
        return { ok: true, json: async () => ({ provider: "openrouter", model: savedModel }) };
      }
      assert.equal(path, "/api/models?provider=openrouter");
      return { json: async () => ({ ok: !!discovered, models: discovered }) };
    },
  });
  await new Promise(resolve => setImmediate(resolve));
  await sync();
  assert.equal(model.value, savedModel);
  assert.equal(local.model, savedModel);
  assert.equal(requests[0].model, savedModel);
  if (discovered) assert(model.options.some(option => option.value === discovered[0]));
}
(async () => {
  await check(null);
  await check(["provider/discovered-model"]);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""], text=True, capture_output=True, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required to exercise the console handlers")
def test_console_refreshes_model_discovery_after_key_entry():
    result = subprocess.run(["node", "-e", r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("public/js/app.js", "utf8");
const config = source.slice(source.indexOf("const initProviderConfig"), source.indexOf("// ── User Profile Menu"));
const provider = { value: "", addEventListener() {} };
const key = { value: "", addEventListener(_, callback) { this.input = callback; } };
const model = {
  options: [],
  set innerHTML(_) { this.options = []; },
  appendChild(option) { this.options.push(option); },
  get value() { return this.options.find(option => option.selected)?.value || ""; },
  addEventListener() {},
};
const requests = [];
const timers = new Map();
let timerId = 0;
let finishInitialDiscovery;
const initialDiscovery = new Promise(resolve => { finishInitialDiscovery = resolve; });
const elements = { "provider-select": provider, "model-select": model, "api-key-input": key };
vm.runInNewContext(config + "\ninitProviderConfig();", {
  PROVIDERS: { openrouter: { defaultModel: "example", exampleModels: ["example"] } },
  document: { getElementById: id => elements[id], createElement: () => ({}) },
  clearInputError() {}, dismissTopWarning() {},
  localStorage: { setItem() {} },
  URLSearchParams,
  clearTimeout(id) { timers.delete(id); },
  setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
  async fetch(path, request) {
    if (request?.method === "POST") return;
    if (path === "/api/config") {
      return { ok: true, json: async () => ({ provider: "openrouter", model: "example" }) };
    }
    assert.equal(path, "/api/models?provider=openrouter");
    const sent = request.headers["X-API-Key"] || "";
    requests.push(sent);
    if (!sent) return initialDiscovery;
    return { json: async () => ({ ok: true, models: ["account-model"] }) };
  },
});
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  key.value = "partial";
  key.input();
  key.value = "synthetic-key";
  key.input();
  for (const callback of timers.values()) await callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(requests, ["", "synthetic-key"]);
  finishInitialDiscovery({ json: async () => ({ ok: true, models: ["stale-model"] }) });
  await new Promise(resolve => setImmediate(resolve));
  assert(model.options.some(option => option.value === "account-model"));
  assert(!model.options.some(option => option.value === "stale-model"));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""], text=True, capture_output=True, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0, result.stderr
