# Troy Module Reference

Per-module documentation for Troy, written for **both** human developers
and AI agents. Every module below is described the same way: what it is
for, its public API, how it is used, and what it depends on.

Conventions used in this document:

- **Public API** = names exported for other code to call. Anything
  prefixed with `_` is private implementation detail.
- All resolution results are plain, JSON-serialisable dicts.
- "Complaint" means the customer text being resolved; "resolution" means
  the result dict returned for it.

---

## `troy.py` — root open-source entrypoint

The single file a user interacts with first. It re-exports the library
API so that `import troy` is all you need, and it doubles as the CLI.

### Library API

```python
import troy

result = troy.resolve('complaint text')          # one-shot, shared default agent
agent = troy.Troy(product_name='MyApp')          # your own agent instance

@agent.tool(name="reset_quota", description="Reset a customer's quota")
def reset_quota(customer_id: str = "", **kwargs):
    return {"ok": True, "customer_id": customer_id, "quota": "reset"}
```

Public exports (`troy.__all__`):

| Name | What it is |
|---|---|
| `Troy` | The agent class (subclass of `TroyClient`). `Troy()`, `Troy(product_name='MyApp')`, `Troy(config={...}, adapter=..., driver=...)`. Methods: `resolve(complaint, ...)` (sync), `resolve_async(...)`, `resolve_ticket(...)` (async, inherited), `resolve_ticket_sync(...)`, `tool` / `register_tool` / `execute_tool`, `list_tools`, `get_info`, `get_health`. |
| `resolve(complaint, customer_id=None, order_id=None, context=None, auto_execute=True, dry_run=False, agent=None, **kwargs)` | Module-level one-shot resolution using a lazily-created shared default agent. |
| `resolve_async(...)` | Async form of `resolve`. |
| `tool(*args, **kwargs)` | Decorator registering a tool on the shared default agent. Usable as `@troy.tool`, `@troy.tool("name")`, or `@troy.tool(name="...", description="...", schema={...})`. |
| `register_tool` / `execute_tool` | Module-level equivalents of the agent methods. |
| `load_config(source=None, **overrides)` | Load a product config from a dict, JSON string, file path, or `SaaSConfig` (alias of `load_saas_config`). |
| `CsvDataAdapter`, `InMemoryDataAdapter`, `RestApiDataAdapter` | The three data-store adapters (see `app/core/adapter.py`). |
| `get_default_agent()` / `default_agent()` | Access the shared default `Troy` instance. |
| `serve(port=3000, host="0.0.0.0")` | Run the FastAPI app with uvicorn (blocking). |
| `main(argv=None) -> int` | CLI entry point; returns a process exit code. |
| `__version__` | Version string (from `app.__version__`). |

### CLI interface

```bash
python troy.py 'complaint text'                  # resolve, human-readable output
python troy.py 'complaint text' --json           # resolve, full JSON output
python troy.py 'complaint text' --dry-run --json # preview; mutates nothing
python troy.py 'complaint text' --auto-execute   # execute the recommended action (default)
python troy.py 'complaint text' --no-auto-execute
python troy.py 'complaint text' --config saas.yaml
python troy.py --serve --port 3000               # serve the FastAPI app
python troy.py --version                         # print version and exit
python troy.py --help                            # usage and examples
```

Exit codes: `0` when the resolution succeeded, `1` when it did not,
`2` (argparse) when no complaint was given without `--serve`/`--help`.

### Dependencies

Imports `app.core.adapter`, `app.core.saas_config`, and
`app.sdk.client` only. It inserts its own directory into `sys.path` so
`python troy.py` and `import troy` work from any working directory.

---

## `app/sdk/` — TroyClient SDK

### `app/sdk/client.py` — `TroyClient`

The SDK entry point. A client bundles four things: a `SaaSConfig`, a
data-store adapter, a `TroyToolRegistry`, and an LLM driver. With no
arguments it is fully zero-setup (default config, empty
`InMemoryDataAdapter`, built-in tools, and
`OpenAiDriver(provider='lmstudio')` for the local LM Studio endpoint).

```python
from app.sdk import TroyClient
from app.core.adapter import InMemoryDataAdapter

client = TroyClient(
    config={"product_name": "MyApp", "domain": "example.com"},
    adapter=InMemoryDataAdapter(customers=[...], transactions=[...]),
)
result = await client.resolve_ticket("I was charged twice for order ORD-88219")
```

Public API:

| Member | Behaviour |
|---|---|
| `resolve_ticket(complaint, customer_id=None, order_id=None, context=None, auto_execute=True, dry_run=False, **kwargs)` (async) | The core pipeline: extract entities → evaluate intent → inspect ledger → decide remediation (respecting `dry_run`, `auto_execute`, approval limits) → execute via tools → return the resolution dict (status, category, team, SLA, entities, `action_result`, `refund_id`, `customer_communication`, audit-logged). |
| `resolve_ticket_sync(...)` | Synchronous wrapper (runs the coroutine via `asyncio.run`, or in a helper thread if a loop is already running). |
| `resolve` / `resolve_sync` | Aliases of the above. |
| `tool(...)` / `register_tool(...)` | Decorator / programmatic form for registering custom tools on this client. |
| `execute_tool(name, params, **kwargs)` / `list_tools()` | Run a registered tool / list registered tools. |
| `get_info()` / `get_health()` | Product, adapter, driver, tools, categories, and policies metadata (used by the HTTP surfaces). |
| Properties | `config`, `adapter` (aliases `store`, `data_adapter`), `tool_registry` (alias `registry`), `driver`, `product_name`. |

Internal helpers: `_jsonable` (recursive JSON-safe conversion),
`_default_driver` / `_coerce_driver` (lmstudio default-driver selection), and the
default tool implementations (`issue_refund`, `get_customer`,
`get_transactions`, `update_customer_account`,
`inspect_account_and_transactions`) built per-client so they always act
on that client's adapter.

### `app/sdk/middleware.py` — `create_troy_router`

`create_troy_router(client=None, *, prefix="", tags=None)` returns a
FastAPI `APIRouter` bound to one client:

- `POST /resolve` — resolve a complaint (flexible body aliases;
  missing complaint → `400 {"ok": False, ...}`).
- `POST /tools/execute` — execute a tool by name with an optional
  idempotency key / dry-run flag.
- `GET /health`, `GET /info`, `GET /tools` — discovery/metadata.

Use it to mount Troy inside your own FastAPI app in two lines:
`app.include_router(create_troy_router(TroyClient()))`.

### `app/sdk/__init__.py`

Exports `TroyClient` and `create_troy_router`.

---

## `app/core/` — configuration, data, and tools

### `app/core/saas_config.py` — `SaaSConfig` / `load_saas_config`

Validated product configuration (pydantic). A config describes one
product: `product_name` (default `"Troy Demo SaaS"`), `support_email`,
`domain`, `branding`, `entity_patterns`, `categories`, and `policies`.

Key API:

- `SaaSConfig(...)` — construct directly; caller dicts are normalised
  (aliases like `name`/`product` → `product_name`, `email` →
  `support_email`, plain-string branding/categories, `sla` → `sla_hours`).
- `load_saas_config(source=None, **overrides)` — load from a dict,
  JSON/YAML string, JSON/YAML file path, existing `SaaSConfig`, or
  nothing (defaults). This is `troy.load_config`.
- `config.extract_entities(text)` — find order/customer/transaction/
  invoice/subscription/ticket IDs in free text using the configured
  regexes. Invalid regexes are rejected at construction time.
- `config.get_category(name)`, `config.category_names()`,
  `config.get_sla(name)`, `config.requires_approval_for(amount)` —
  category/SLA/policy lookups used by the resolution pipeline.
- `config.to_dict()` / `to_json()` / `to_yaml()` / `save(path)` /
  `from_dict` / `from_json` / `from_yaml` / `from_file` — round-trip
  serialisation.

Sub-models: `BrandingConfig`, `CategoryConfig`, `PolicyConfig`
(`auto_refund_limit`, `approval_required`, `dry_run`); defaults are in
`DEFAULT_ENTITY_PATTERNS` and `_default_categories()` (11 categories:
Refunds, Billing, Subscriptions, Authentication & Access, Bugs, System
Performance, Data Sync, Integrations & APIs, Account Management,
Security, Onboarding).

### `app/core/adapter.py` — data-store adapters

`DataStoreAdapter` is the interface every adapter implements
(synchronous, plain dicts/lists):

- `get_customer(identifier) -> dict | None` — look up by ID, email,
  name, or order ID.
- `get_transactions(customer_id, **filters) -> list[dict]` — ledger
  records for a customer, optionally filtered.
- `execute_action(action_name, params) -> dict` — perform a remediation
  (e.g. `remediate_duplicate_charge`, `update_customer_account`).
- `audit_log(entry)` — record an audit entry; `audit_trail` exposes
  the retained entries.

Implementations:

| Class | Behaviour |
|---|---|
| `InMemoryDataAdapter(customers, transactions)` | Zero-setup in-memory store for demos/tests. Accepts lists of dicts, a `{customer_id: record}` mapping, or the single-dict `{"customers": ..., "transactions": ...}` form. Refunds are idempotent (re-refunding a refunded transaction returns `is_already_refunded`). |
| `CsvDataAdapter(...)` | Wraps `app/database.py`'s CSV ledger (`data/transactions.csv`, `data/customer_accounts.csv`), including honouring test monkey-patches of the database module. Optional explicit file paths for standalone use. |
| `RestApiDataAdapter(base_url, api_key=None, ...)` | Forwards to HTTP endpoints: `GET /customers/{identifier}`, `GET /transactions?customer_id=…`, `POST /actions/{action_name}`, `POST /audit`. Accepts an injected `http_client` (e.g. `httpx.Client`) for tests; otherwise creates one lazily. |

`create_adapter("csv" | "memory" | "rest", **kwargs)` is a small
factory. To write your own adapter, subclass `DataStoreAdapter` and
implement the four methods — nothing else in Troy changes.

### `app/core/tool_registry.py` — `TroyToolRegistry`

The tool execution engine. Tools are plain callables registered by name
with an optional description and JSON-schema-like `schema`.

```python
@registry.tool(name="reset_api_quota", description="Reset a quota",
               schema={"type": "object", "required": ["customer_id"],
                       "properties": {"customer_id": {"type": "string"}}})
def reset_api_quota(customer_id: str = "", **kwargs):
    return {"ok": True, "customer_id": customer_id, "quota": "reset"}

result = registry.execute("reset_api_quota", {"customer_id": "USR-1"})
```

Public API: `register` / `register_tool` (both decorator and
programmatic forms, both argument orders), `tool` (decorator, usable
bare, with a positional name, or with kwargs), `unregister`,
`get_tool`, `has_tool`, `list_tools`, `tool_names`, `execute`,
`get_audit_trail`. `execute` always returns a result dict and guarantees
schema validation, idempotency-key replay, dry-run (handler never
invoked), and audit logging. `validate_params()` is exported for reuse.
Aliases: `ToolRegistry = TroyToolRegistry`, `Tool = TroyTool`.

---

## `app/simulation/` — interactive resolution orchestrator

This package powers the operator console: an LLM-driven, streamed
resolution run with shared session state. It is distinct from the
library pipeline in `app/sdk/client.py` — the SDK resolves one complaint
deterministically; the orchestrator deliberates with an LLM and streams
its reasoning.

### `app/simulation/troy.py` — `ComplaintResolutionOrchestrator` (alias `Troy`)

Coordinates the local LLM resolution engine, autonomous intent routing,
and SSE streaming:

- `run(story, driver=None)` — the full streamed run: summarise the
  ticket, evaluate intent, inspect the ledger, execute duplicate-charge
  remediation when the intent is `database_remediation`, and generate
  the final resolution report.
- `evaluate_complaint_intent(complaint, category=None)` /
  `evaluate_complaint_intent_with_llm(...)` — decide whether a ticket
  needs database work (`database_remediation`) or is a direct response,
  extracting `order_id` / `customer_id` / `transaction_id` from the text.
- `summarise_ticket(query, driver=...)` — build the ticket title/summary
  via the ticket prompts.
- `is_running()`, `set_story()` / `get_latest_story()`,
  `mark_round_begun(round_num)` — session lifecycle used by the API.

Refund-domain runs rely **only** on dynamically extracted IDs and the
ledger analysis (`intent_info` + `txn_analysis` from
`inspect_account_and_transactions`): no order or transaction ID is ever
assumed. Remediation goes through
`app.database.remediate_duplicate_charge`, and the resulting refund ID
comes from the database result.

`RESOLUTION_TEMPLATES` (module level, outside the class) holds the 11
per-category resolution templates formerly inlined in
`_build_category_fallback`: each key is a category name and each value
is a function taking `(ref_id, team, sla_hours, conf_pct, report)`
(the Refunds template also accepts an optional `cat_ctx`) and returning
the structured string. `_build_category_fallback` is now a ~20-line
lookup: `RESOLUTION_TEMPLATES.get(pred_cat, RESOLUTION_TEMPLATES['Account Management'])(...)`,
with the Refunds branch passed `cat_ctx` for ledger details.

### `app/simulation/state.py` — `SimulationState`

Thread-safe session state: a running flag (with `acquire_lock` /
`release_lock`, aliases `acquire` / `release`), the latest ticket/story,
and the story history. Aliases `set_ticket`/`get_ticket` and
`set_latest_story`/`get_latest_story` are kept for older callers.

### `app/simulation/round_barrier.py` — `RoundBarrier`

Async coordination for resolution "rounds": `wait_for_round_begin(...)`
blocks until `mark_round_begun(round_id)` is called (or returns
immediately if the round was already marked pending), and `reset()`
releases all waiters. Round numbers are normalised with
`normalize_round_number()` (non-numeric/negative values are rejected).

Also in this package: `session_registry.py` (`SessionRegistry`, one
orchestrator session per user) and `__init__.py` re-exporting `Troy`,
`SimulationState`, `RoundBarrier`, `CouncilOrchestrator`, and
`SessionRegistry`.

---

## `app/api/` — HTTP surfaces

### `app/api/v1/saas_routes.py` — REST & webhook routes

Mounted by `app/main.py` at `/api/v1/saas`. The module exposes a
lazily-created shared default client (`get_saas_client`, aliases
`get_default_client` / `get_client`) and `create_saas_router(client=None)`
to mount the same routes against a custom client.

Endpoints (the only three):

| Method & path | Behaviour |
|---|---|
| `POST /api/v1/saas/resolve` | JSON resolution: accepts `complaint` (or `query`/`text`/`message`/`ticket`/`description`/`body`), `customer_id`, `order_id`, `context`, `auto_execute`, `dry_run`, and an optional per-request `config` override. Missing complaint → `400`. |
| `POST /api/v1/saas/webhooks/{provider}` | Normalises the provider payload, resolves it, and returns the resolution merged with `provider`, `normalized`, `complaint`, `customer_email`, `customer_name`, `subject`, and `event_type`. |
| `GET /api/v1/saas/info` | Product name, capabilities (providers, tools, adapter type, categories, policies), and the endpoint list. |

`SUPPORTED_PROVIDERS = ["zendesk", "intercom", "stripe", "freshdesk",
"shopify", "slack", "generic"]`. `normalize_webhook_payload(provider,
payload)` (aliases `normalize_payload`, `normalize_webhook`) maps every
provider into Troy's canonical shape:

- **zendesk / freshdesk** — `ticket.subject` + `ticket.description`,
  requester id/email/name.
- **intercom** — `data.item.title/body`, `data.user` id/email/name.
- **stripe** — event type, charge amount, metadata `order_id` /
  `customer_id`; refund events emphasise the duplicate charge.
- **shopify** — complaint from `note` / `cancel_reason` / `reason`,
  order from `name` / `order_number`, customer from the `customer` object.
- **slack** — complaint from `text`, sender from `user_id` / `user_name`
  (including nested `event`/`message` payloads).
- **generic** (and fallback for all) — `complaint`/`query`/`text`/
  `message`/`description`, `customer_id`/`user_id`, `order_id`, etc.

Troy has a single client; no registries are involved.

### `app/main.py` — FastAPI application

The full app (`app = FastAPI(...)`, launched by `run.py` or
`troy.py --serve`):

- Mounts the SaaS router above, plus the operator console API:
  `GET /api/stream` (SSE), `GET /api/policy` / `GET /api/ticket`,
  `GET /api/customer/verify`, `GET /api/database/*`,
  `POST /api/resolve/action`, `POST /api/next`, `POST /api/round/begin`,
  `GET /api/trigger`, `/api/config`, `/api/models`, and the auth
  endpoints (`/api/auth/*`).
- `GET /health` / `GET /api/health`.
- Serves the embed widget at `/widget/troy-embed.js` and the static
  frontend from `public/`.
- Session-aware LLM configuration: `resolve_session_config()` builds a
  per-session driver from the key vault + `app/config.py` settings,
  treating empty/generic values as unset.

### `app/api/__init__.py` / `app/api/v1/__init__.py`

Re-export the SaaS `router` and `create_saas_router`.

---

## `app/providers/` — LLM drivers

All drivers implement `BaseLlmDriver` (`app/providers/base_driver.py`):
`complete(messages, max_tokens)`, `stream(messages, max_tokens,
on_delta, on_reasoning_delta)`, and `fetch_models(api_key, base_url)`.
`app/providers/__init__.py` provides `create_driver(provider, ...)` and
`provider_base_url(provider)`.

| File | Driver | Behaviour |
|---|---|---|
| `base_driver.py` | `BaseLlmDriver` | Abstract interface; stores `provider` + options. |
| `openai_driver.py` | `OpenAiDriver` | OpenAI-compatible chat completions over `httpx`, with streaming, thinking-mode handling, and curated model lists per backend (`openai`, `openrouter`, `groq`, `lmstudio`). A failed call raises the real error instead of silently switching drivers. `OpenAiDriver(..., max_retries=2)` retries `httpx.ConnectError`/`TimeoutException` up to `max_retries` times with 1-second backoff, and retries HTTP 5xx once; 400/401/403/404 are never retried and propagate immediately. |
| `anthropic_driver.py` | `AnthropicDriver` | Claude via the Anthropic Messages API; `format_anthropic_payload()` maps chat messages to the Anthropic shape (system prompt split out). |

Production code has no mock driver. Tests that need a deterministic
offline driver use the test-only mock in `tests/mock_driver.py`
(excluded from the `app/` package).

Local LLM usage: set `LLM_PROVIDER=lmstudio` (the default) with
`LLM_MODEL` and `LMSTUDIO_BASE_URL` in `.env` (see `app/config.py`), or
`LLM_PROVIDER=openai` with `LLM_API_KEY`. `TroyClient` defaults to
`OpenAiDriver(provider='lmstudio')`.

---

## `app/database.py` & `app/triage.py`

### `app/database.py` — CSV ledger & remediation

The system of record for the bundled demo data
(`data/transactions.csv`, `data/customer_accounts.csv`), guarded by a
re-entrant lock and safe to monkey-patch in tests
(`DATABASE_FILE`, `CUSTOMER_ACCOUNTS_FILE`, `DATA_DIR`).

Reads are cached in a module-level `_CACHE` dict (guarded by
`_CACHE_LOCK`): `read_all_transactions()` and `read_all_customers()`
(via `read_all_customer_accounts()`) store results with a `_cache_ts`
timestamp and the file mtime, and serve cached copies while the mtime
is unchanged and the entry is ≤ 5 seconds old. Copy-on-read keeps
callers from mutating the cache. `_invalidate_cache()` clears the
cache and is called by `write_all_transactions()`,
`write_all_customer_accounts()`, `remediate_duplicate_charge()`,
`execute_agent_resolution()`, and `execute_manual_resolution()`.

Key functions:

- Reads/queries: `read_all_transactions`, `read_all_customer_accounts`,
  `read_all_tickets`, `query_customer_account`, `query_transactions`,
  `find_customer_account_by_query`, `find_transactions_for_query`.
- Inspection: `inspect_account_and_transactions(customer_id, query)` —
  combines the customer account, transaction history, and duplicate /
  already-refunded detection into one analysis dict (including
  `duplicate_transaction`, the candidate to refund). This is what both
  the SDK and the orchestrator use; it never assumes IDs.
- Remediation: `remediate_duplicate_charge(order_id,
  duplicate_txn_id, refund_amount, ...)` — atomically refunds one
  transaction and reconciles the customer account to a settled balance.
  Idempotent: an already-refunded transaction returns success without a
  second payout. Also `execute_agent_resolution`,
  `execute_manual_resolution`, `update_transaction`,
  `update_customer_account`, `verify_customer_billing`,
  `analyze_customer_transactions`, and
  `get_category_complaint_context` for the console's verification card.

`CsvDataAdapter` (`app/core/adapter.py`) is the adapter-shaped wrapper
around these functions.

### `app/triage.py` — intake triage & categorization

Turns a complaint into a category, team, and SLA:

- `ALL_11_CATEGORIES`, `LABEL_MAP`, `CATEGORY_TO_TEAM`, `SLA_DEFAULTS`
  — the canonical taxonomy (Refunds, Billing, Subscription, Security,
  Authentication & Access, Bugs & Errors, Performance, Data Sync,
  Integrations & API, Account Management, Onboarding & How-To).
- `predict_category(complaint)` / `triage_complaint(...)` (sync) and
  `async_predict_category` / `async_triage_complaint` (async) — ask the
  LLM driver for a structured triage report (predicted category,
  confidence, entities, similar precedents, team, SLA), with a
  deterministic heuristic fallback computed directly from category
  defaults when the LLM is unavailable. Used by
  `POST /api/cra/resolve` and `POST /api/cra/classify` in `app/main.py`.

---

## Supporting modules (brief)

- `app/config.py` — `Settings` (pydantic) loaded from environment /
  `.env`: `port`, `auth_mode`, `llm_provider`, `llm_model`, API keys,
  timeouts, Firebase project settings. Entry point: `settings`.
- `app/http/sse_manager.py` — `SseManager`: fan-out of server-sent
  events to connected console clients.
- `app/auth/` — `SessionGuard` (optional/required auth modes),
  `KeyVault` (encrypted per-session LLM keys), `crypto` helpers.
- `app/prompts/` — ticket title/summary and policy prompt builders used
  by the orchestrator and triage.
- `app/utils/` — `sanitizer` (model output cleanup), `json_extractor`
  (lenient JSON parsing from LLM text), `thought_buffer`, and
  `time_utils.py` — the single canonical `_utcnow_iso()` helper,
  imported by `app/core/adapter.py`, `app/core/tool_registry.py`, and
  `app/sdk/client.py` (re-exported from `app/utils/__init__.py`).
- `app/reference/` — reasoning/output reference used by the orchestrator.
- `run.py` — launch the full app with uvicorn on an available port
  (`settings.port`, default 3000).

## Where to start

- **Use Troy as a library:** read `troy.py`, then `app/sdk/client.py`.
- **Add a data source:** read `app/core/adapter.py`.
- **Add an action Troy can take:** read `app/core/tool_registry.py`.
- **Change how Troy talks about a product:** read
  `app/core/saas_config.py`.
- **Serve Troy over HTTP:** read `app/api/v1/saas_routes.py` and
  `app/main.py`.
- **Understand the console run:** read `app/simulation/troy.py`,
  `app/triage.py`, and `app/database.py` in that order.
