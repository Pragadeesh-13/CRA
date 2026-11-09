# Troy Architecture

Troy is a clean, plug-and-play, **open-source complaint-resolution tool**.
Anyone can clone the repository, run one command, and use Troy directly —
as a Python library, as a command-line tool, or as an HTTP service. There
is a single Troy agent with a single product configuration; there are no
registries to set up and no per-customer copies to manage.

This document describes how the pieces fit together. For a per-module
reference (file-by-file APIs, classes, and functions), see
[MODULES.md](./MODULES.md).

---

## 1. At a glance

```
                        ┌─────────────────────────────────────────────┐
                        │                 troy.py (root)              │
                        │  import troy                                │
                        │  troy.resolve('complaint text')             │
                        │  agent = troy.Troy(product_name='MyApp')    │
                        │  @agent.tool  /  CLI: python troy.py ...   │
                        └──────────────────┬──────────────────────────┘
                                           │ re-exports / wraps
                                           ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │              app/sdk/  —  TroyClient (SDK)                        │
        │   resolve_ticket (async) · resolve_ticket_sync · resolve (sync)    │
        │   tool / register_tool / execute_tool · get_info / get_health      │
        └──────┬──────────────────┬──────────────────┬─────────────────────┘
               │                  │                  │
               ▼                  ▼                  ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐
   │ app/core/        │  │ app/core/        │  │ app/providers/       │
   │ saas_config.py   │  │ adapter.py       │  │ base / openai /      │
   │ product config,  │  │ InMemory / CSV / │  │ anthropic            │
   │ entity regexes,  │  │ RestApi adapters │  │ LLM drivers          │
   │ categories, SLAs │  └────────┬─────────┘  └──────────────────────┘
   └──────────────────┘           │
                                  ▼
                     ┌──────────────────────────┐
                     │ app/core/tool_registry.py│
                     │ validation · idempotency │
                     │ dry-run · audit trail    │
                     └──────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────┐
   │  HTTP surfaces (optional)                                        │
   │  • troy.py --serve / run.py  →  app/main.py (FastAPI app)        │
   │  • app/api/v1/saas_routes.py → /api/v1/saas/resolve|info|webhooks│
   │  • app/sdk/middleware.py     → create_troy_router(client)         │
   └──────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────┐
   │  Interactive console (optional)                                  │
   │  • app/simulation/troy.py  ComplaintResolutionOrchestrator       │
   │  • app/simulation/state.py · round_barrier.py · SSE streaming    │
   │  • app/triage.py · app/database.py                               │
   └──────────────────────────────────────────────────────────────────┘
```

## 2. Design principles

1. **Zero setup by default.** `troy.Troy()` / `troy.resolve("...")` works
   out of the box with no API keys: a default product config, an
   empty in-memory adapter, the built-in tool set, and a local
   LM Studio LLM driver (`OpenAiDriver(provider='lmstudio')`).
   Resolution still succeeds (with templated customer communication)
   when the local LLM is unavailable, because LLM failures are
   caught and the templated message is used.
2. **One agent, one product.** Configuration is a single `SaaSConfig`
   (`product_name`, domain, support email, branding, entity patterns,
   categories + SLAs, policies). Point Troy at a different product by
   changing that config — never by forking Troy's code.
3. **Data is pluggable.** Any object implementing the four-method
   `DataStoreAdapter` interface (`get_customer`, `get_transactions`,
   `execute_action`, `audit_log`) can back Troy: in-memory, the bundled
   CSV ledger, a REST API, or a custom adapter you write.
4. **Actions are tools.** Everything Troy can do (refund, account update,
   customer lookup, custom domain actions) is a registered tool with
   schema validation, idempotency, dry-run support, and an audit trail.
   Add your own with `@agent.tool`.
5. **Safe by default.** Dry-run previews, auto-refund limits / approval
   thresholds, idempotent replays, and a full audit log are built in, so
   a repeated or retried ticket never double-refunds.

## 3. Resolution lifecycle

When `resolve_ticket()` is called with a complaint, `TroyClient` runs a
fixed, auditable pipeline:

1. **Extract entities** — `SaaSConfig.extract_entities()` pulls order
   (`ORD-…`), customer (`USR-…`), and transaction (`TXN-…`) IDs out of
   the free-text complaint using configurable regexes.
2. **Evaluate intent** — the complaint is classified as a
   `database_remediation` (billing dispute / duplicate charge) or a
   `direct_response` (inquiry). This decides whether any database work
   is needed at all.
3. **Inspect the ledger** — the adapter looks up the customer and their
   transactions; duplicate-settled and already-refunded states are
   detected from the real records, never assumed.
4. **Decide on remediation** — a duplicate charge with no refund yet is
   a remediation candidate. Amounts above `policies.auto_refund_limit`,
   or any amount when `policies.approval_required` is set, resolve as
   `pending_approval` instead of executing. `dry_run` (per call or per
   policy) resolves as `dry_run` and mutates nothing.
5. **Execute via tools** — remediation runs through the tool registry
   (`issue_refund`), with an idempotency key derived from
   customer + transaction + order, so replays are safe.
6. **Report** — a JSON-serialisable result is returned containing the
   status (`resolved` / `already_refunded` / `dry_run` /
   `pending_approval`), category, team, SLA, extracted entities,
   actions taken, and a customer-ready `customer_communication` message.

The interactive console (`app/simulation/troy.py`) runs the same intent
routing with an LLM, streams progress over SSE, and — for refund-domain
tickets — inspects the ledger and executes the duplicate-charge
remediation autonomously before generating the final report.

## 4. Configuration model

`SaaSConfig` (`app/core/saas_config.py`, loaded via `troy.load_config`)
is a validated pydantic model. All fields are optional:

| Field | Purpose |
|---|---|
| `product_name`, `domain`, `support_email`, `branding` | How Troy presents itself in messages and reports. |
| `entity_patterns` | Regexes for the IDs that appear in complaints (orders, customers, transactions, invoices, subscriptions, tickets). Invalid patterns are rejected at construction. |
| `categories` | Complaint categories with `sla_hours`, `sla_risk`, and routing `team`. |
| `policies` | `auto_refund_limit`, `approval_required`, `dry_run`. |

Configs can be supplied as a dict, a JSON/YAML string, a JSON/YAML file
path, or an existing `SaaSConfig`, and round-trip via
`to_dict()` / `to_json()` / `to_yaml()` / `save()`.

## 5. Data adapters

| Adapter | Use it when |
|---|---|
| `InMemoryDataAdapter` | Demos, tests, and zero-setup usage; seed customers/transactions in code. |
| `CsvDataAdapter` | You want the bundled CSV ledger (`data/transactions.csv`, `data/customer_accounts.csv`) via `app/database.py`. |
| `RestApiDataAdapter` | Your data already lives behind HTTP endpoints (`GET /customers/{id}`, `GET /transactions`, `POST /actions/{name}`, `POST /audit`); an `httpx` client can be injected for tests. |
| Custom `DataStoreAdapter` | Implement the same four methods against your own database/API. Nothing else in Troy changes. |

## 6. Tool execution engine

`TroyToolRegistry` (`app/core/tool_registry.py`) guarantees, for every
tool call:

- **Schema validation** — JSON-schema-like `required` / `properties` /
  `type` / `enum` checks; invalid calls return `{"ok": False, ...}`.
- **Idempotency** — an `idempotency_key` replays the cached result
  (`idempotent_replay: True`) without re-running the handler.
- **Dry-run** — validates and audits without invoking the handler.
- **Audit trail** — every attempt is recorded and inspectable via
  `registry.get_audit_trail()`.

The default registry ships `issue_refund` (aliases: `refund`,
`remediate_duplicate_charge`), `get_customer`, `get_transactions`,
`update_customer_account` (alias: `update_account`), and
`inspect_account_and_transactions`.

## 7. LLM providers

LLM access goes through the `BaseLlmDriver` interface
(`complete` / `stream` / `fetch_models`):

- `OpenAiDriver` — OpenAI-compatible chat completions; also serves
  OpenRouter, Groq, and local **LM Studio** (default preset
  `qwen/qwen3.5-9b` on `http://localhost:1234/v1`). This is the
  default driver (`OpenAiDriver(provider='lmstudio')`). When an LLM
  call fails, the real error propagates to the caller — for customer
  communication generation, Troy falls back to its templated message.
- `AnthropicDriver` — Claude models via the Anthropic Messages API.

`TroyClient` uses `OpenAiDriver(provider='lmstudio')` by default. In the
interactive console, the provider/model/key come from the session
configuration (`app/config.py` settings + the key vault), never from
the complaint. Tests that need a deterministic driver use the
test-only mock in `tests/mock_driver.py`.

## 8. HTTP surfaces (optional)

Troy can be used purely as a library/CLI, or served over HTTP:

| Surface | Routes |
|---|---|
| `app/api/v1/saas_routes.py` (mounted by `app/main.py`) | `POST /api/v1/saas/resolve`, `POST /api/v1/saas/webhooks/{provider}` (zendesk, intercom, stripe, freshdesk, shopify, slack, generic), `GET /api/v1/saas/info` |
| `app/sdk/middleware.py` (`create_troy_router(client)`) | `POST /resolve`, `POST /tools/execute`, `GET /health`, `GET /info`, `GET /tools` |
| `app/main.py` (full app) | The SaaS routes plus the operator console API (`/api/next`, `/api/stream` SSE, `/api/customer/verify`, `/api/resolve/action`, config/model/auth endpoints) and the embed widget at `/widget/troy-embed.js` |

Webhook payloads are normalised by `normalize_webhook_payload()` into
Troy's canonical shape (`complaint`, `customer_id`, `order_id`,
`transaction_id`, `customer_email`, `customer_name`, `subject`,
`provider`, `event_type`, `context`) before resolution.

## 9. Repository layout

```
troy.py                  Root open-source entrypoint (library + CLI)
run.py                   Launch the full FastAPI app (uvicorn)
app/
  main.py                FastAPI app, operator console endpoints, widget
  config.py              Environment settings (port, LLM provider/model)
  database.py            CSV ledger: reads, inspection, duplicate-charge refund
  triage.py              Intake triage: 11 categories, team routing, SLAs
  sdk/                   TroyClient + create_troy_router
  core/                  saas_config, adapter, tool_registry
  simulation/            ComplaintResolutionOrchestrator, state, round barrier
  api/v1/saas_routes.py  REST + webhook routes
  providers/             base / openai / anthropic LLM drivers
  http/                  SSE manager for live streaming
  auth/ · prompts/ · utils/ · reference/
data/                    Bundled CSV ledger (transactions, customer accounts)
examples/                Runnable integration examples
public/ · app/static/    Operator console frontend + embed widget
tests/                   test_troy_opensource_tool.py, test_saas_plug_and_play.py
docs/                    ARCHITECTURE.md (this file), MODULES.md
```

## 10. Testing

```bash
python -m pytest tests/test_troy_opensource_tool.py tests/test_saas_plug_and_play.py -v
```

The open-source suite covers the library API and CLI; the plug-and-play
suite covers config, adapters, the tool registry, the SDK, the REST /
webhook surfaces, and the widget. Database tests use an isolated
temporary copy of the CSV ledger (`isolated_database` fixture).
