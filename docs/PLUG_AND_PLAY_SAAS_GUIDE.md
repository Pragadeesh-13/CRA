# Plug-and-Play SaaS Guide — Troy for Any Product

Troy is a universal AI resolution specialist. Instead of hard-coding one
product's customers, orders, categories, and refund rules, Troy is
configured through three small, composable pieces:

| Piece | Class / module | What it does |
|---|---|---|
| **Tenant profile** | `SaaSConfig` (`app.core.saas_config`) | Product name, domain, support email, branding, entity regexes, categories + SLAs, policies. |
| **Data store** | `DataStoreAdapter` (`app.core.adapter`) | How Troy reads customers/transactions and executes actions. |
| **Tools** | `TroyToolRegistry` (`app.core.tool_registry`) | Custom SaaS actions Troy can invoke, with validation, idempotency, dry-run, and auditing. |

Wire them together with the SDK client (`TroyClient`, `app.sdk`), expose
HTTP surfaces with `create_troy_router` / the mounted SaaS routes, and
drop the embed widget on your site. The whole integration takes minutes —
no fork of Troy's core required.

> A complete, runnable example lives in
> [`examples/saas_integration_example.py`](../examples/saas_integration_example.py)
> (`python examples/saas_integration_example.py`), and the behaviour is
> locked in by [`tests/test_saas_plug_and_play.py`](../tests/test_saas_plug_and_play.py).

---

## Quickstart (3 steps)

### Step 1 — Define your tenant profile

```python
from app.core.saas_config import SaaSConfig

config = SaaSConfig(
    product_name="CloudScribe AI",
    domain="cloudscribe.ai",
    support_email="support@cloudscribe.ai",
)
```

### Step 2 — Connect a data store and create the client

```python
from app.core.adapter import InMemoryDataAdapter  # or Csv / RestApi / custom
from app.sdk import TroyClient

adapter = InMemoryDataAdapter(
    customers=[{
        "customer_id": "USR-20481",
        "customer_name": "Maya Patel",
        "customer_email": "maya@acme.example",
        "reference_order_id": "ORD-73001",
    }],
    transactions=[
        {"transaction_id": "TXN-5001", "customer_id": "USR-20481",
         "order_id": "ORD-73001", "amount": "79.00", "status": "Settled"},
        {"transaction_id": "TXN-5002", "customer_id": "USR-20481",
         "order_id": "ORD-73001", "amount": "79.00", "status": "Settled"},
    ],
)

client = TroyClient(config=config, adapter=adapter)
```

### Step 3 — Resolve tickets

```python
import asyncio

result = asyncio.run(client.resolve_ticket(
    "I was charged twice for order ORD-73001, customer USR-20481, transaction TXN-5002",
    auto_execute=True,
))
print(result["status"], result["refund_id"], result["customer_communication"])
# Synchronous alternative: client.resolve_ticket_sync("...")
```

That's it: Troy extracts the order/customer/transaction IDs, detects the
duplicate debit, issues the refund through the pre-registered
`issue_refund` tool, audits the action, and returns a JSON-serialisable
resolution payload with a customer-ready message.

---

## Defining a SaaS Tenant Profile (`SaaSConfig`)

```python
from app.core.saas_config import SaaSConfig, load_saas_config

config = SaaSConfig(
    product_name="CloudScribe AI",
    domain="cloudscribe.ai",
    support_email="support@cloudscribe.ai",
    branding={"name": "CloudScribe AI", "primary_color": "#4f46e5"},
    entity_patterns={                       # what IDs look like in your tickets
        "orders": [r"ORD-\d+"],
        "customers": [r"USR-\d+"],
        "transactions": [r"TXN-\d+"],
        "charges": [r"ch_\d+"],             # e.g. Stripe charge ch_123
        "invoices": [r"INV-\d+"],           # e.g. invoice INV-99
        "tenants": [r"org_\d+"],            # e.g. tenant org_88
    },
    categories=[                            # custom complaint categories + SLAs
        {"name": "Refunds", "sla_hours": 2.0, "sla_risk": "Low", "team": "Billing"},
        {"name": "API Quota", "sla_hours": 4.0, "sla_risk": "Low", "team": "Engineering"},
    ],
    policies={
        "auto_refund_limit": 100.0,         # auto-refund at or below this amount
        "approval_required": False,         # True: always route refunds for approval
        "dry_run": False,                   # True: never mutate (preview mode)
    },
)
```

Helpers:

```python
config.extract_entities("Charge ch_123 on invoice INV-99 for tenant org_88")
# {'charges': ['ch_123'], 'invoices': ['INV-99'], 'tenants': ['org_88'], ...}
config.get_category("Refunds").sla_hours   # 2.0
config.requires_approval_for(250.0)        # True (over the auto-refund limit)
```

Load from a file (JSON or YAML) or keep configs in source control:

```python
config = load_saas_config("saas.yaml")     # or "saas.json", a dict, or a JSON string
config.save("saas.yaml")                   # persist the current profile
config.to_dict() / config.to_json() / config.to_yaml()
```

Invalid regex patterns are rejected at construction time.

---

## Connecting Data Stores

Every adapter implements the same four methods — `get_customer(identifier)`,
`get_transactions(customer_id, **filters)`, `execute_action(action_name, params)`,
and `audit_log(entry)` — so Troy's resolution pipeline is identical
regardless of where your data lives.

### In-memory (demos, tests, zero setup)

```python
from app.core.adapter import InMemoryDataAdapter

adapter = InMemoryDataAdapter(customers=[...], transactions=[...])
# or the single-dict form:
adapter = InMemoryDataAdapter({"customers": {...}, "transactions": [...]})
```

### CSV (the built-in Troy ledger)

`CsvDataAdapter` wraps the existing `app.database` CSV store
(`data/transactions.csv` + `data/customer_accounts.csv`) with full
backward compatibility:

```python
from app.core.adapter import CsvDataAdapter

adapter = CsvDataAdapter()  # reads/writes the CSVs via app.database
```

### REST API (your existing SaaS endpoints)

`RestApiDataAdapter` forwards to your HTTP endpoints
(`GET /customers/{id}`, `GET /transactions`, `POST /actions/{name}`, `POST /audit`):

```python
from app.core.adapter import RestApiDataAdapter

adapter = RestApiDataAdapter(
    "https://api.your-saas.example",
    api_key="...",            # sent as `Authorization: Bearer ...`
    timeout=10.0,
    # endpoints={...},        # override the default paths if yours differ
    # http_client=...,        # inject an httpx.Client (e.g. in tests)
)
```

### Custom database adapter

Subclass `DataStoreAdapter` and implement the four methods — nothing else
in Troy needs to change:

```python
from app.core.adapter import DataStoreAdapter

class PostgresAdapter(DataStoreAdapter):
    def get_customer(self, identifier: str): ...
    def get_transactions(self, customer_id: str, **kwargs): ...
    def execute_action(self, action_name: str, params: dict): ...
    def audit_log(self, entry: dict): ...

client = TroyClient(config=config, adapter=PostgresAdapter(...))
```

---

## Registering Custom SaaS Actions / Tools

The default registry ships with `issue_refund`, `get_customer`,
`get_transactions`, `update_customer_account`, and
`inspect_account_and_transactions`. Register your own domain actions with
the `@troy.tool` decorator (on the client or the registry) or
programmatically:

```python
@client.tool(
    name="reset_api_quota",
    description="Reset the API quota for a tenant customer.",
    schema={
        "type": "object",
        "required": ["customer_id"],
        "properties": {"customer_id": {"type": "string"}},
    },
)
def reset_api_quota(customer_id: str = "", **kwargs) -> dict:
    client.adapter.execute_action(
        "update_customer_account",
        {"customer_id": customer_id, "updates": {"account_status": "Active - API Quota Reset"}},
    )
    return {"ok": True, "customer_id": customer_id, "quota": "reset"}


def issue_credit_refund(transaction_id: str = "", amount: float = 0.0, **kwargs) -> dict:
    return client.adapter.execute_action(
        "remediate_duplicate_charge",
        {"duplicate_txn_id": transaction_id, "refund_amount": str(amount)},
    )

client.register_tool(issue_credit_refund, name="issue_credit_refund", schema={...})
result = client.execute_tool("reset_api_quota", {"customer_id": "USR-20481"})
```

Registry guarantees (see `app.core.tool_registry`):

- **Schema validation** — JSON-schema-like `required`/`properties`/`enum`/`type`
  checks run before the handler; invalid calls return `{"ok": False, "error": ...}`.
- **Idempotency** — pass `idempotency_key=` and replays return the cached
  result with `idempotent_replay: True` *without re-running the handler*
  (no double refunds).
- **Dry-run** — `dry_run=True` (per call, per tool, or registry-wide)
  validates and audits without invoking the handler.
- **Audit trail** — every execution attempt is recorded
  (`registry.get_audit_trail()`), alongside the adapter's audit log.

---

## Webhook Integrations

Troy ships mounted REST/webhook surfaces (already included in
`app/main.py`; the same routes can be mounted anywhere with
`create_saas_router(client)` from `app.api.v1.saas_routes`, or the SDK
router `create_troy_router(client)` from `app.sdk`):

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/saas/resolve` | Standard JSON resolution: `{"complaint": "...", "customer_id": ..., "order_id": ..., "auto_execute": true, "dry_run": false}` |
| `POST /api/v1/saas/webhooks/{provider}` | Provider webhooks: `zendesk`, `intercom`, `stripe`, `freshdesk`, or `generic` |
| `GET /api/v1/saas/info` | Tenant + capability metadata (product, tools, adapter type, categories, policies) |

Point each provider's webhook at
`https://your-troy-host/api/v1/saas/webhooks/<provider>` — payloads are
normalised automatically into `{complaint, customer_id, order_id,
transaction_id, customer_email, subject, provider, event_type, context}`
and then resolved. Examples:

- **Zendesk** — send the ticket object: `{"ticket": {"subject": "...", "description": "...", "requester": {"id": ..., "email": ..., "name": ...}}}`
- **Intercom** — `{"type": "ticket.created", "data": {"item": {"title": ..., "body": ...}, "user": {...}}}`
- **Stripe** — forward the event as-is: `{"type": "charge.refunded", "data": {"object": {...}}}` (metadata `order_id`/`customer_id` are honoured)
- **Freshdesk** — `{"ticket": {"subject": ..., "description": ..., "requester_id": ...}}`
- **Generic** — any JSON with `complaint`/`query`/`text`/`message`/`description`

Responses merge the resolution payload with `provider` and `normalized`
(so you can log exactly what was extracted). External webhooks are treated
as public paths (they're signed by the provider, not by Troy sessions).

---

## Embedding the Drop-in Web Widget

A single zero-dependency script gives any frontend a floating
"Resolve with Troy" drawer with ticket input, live status feedback, and the
resolution report:

```html
<script
  src="https://your-troy-host/widget/troy-embed.js"
  data-endpoint="https://your-troy-host/api/v1/saas/resolve"
  data-product-name="CloudScribe AI"></script>
```

The script is served by Troy at `/widget/troy-embed.js` (source:
`app/static/widget/troy-embed.js`). It auto-detects its origin to build
the resolve endpoint, and can be configured in code too:

```js
window.TROY_EMBED_CONFIG = { productName: "CloudScribe AI" };
TroyEmbed.init({ title: "Help · CloudScribe AI" });
```

The widget POSTs `{complaint, customer_id, order_id}` to your mounted
resolve endpoint and renders `customer_communication`, status, category,
and refund reference — no bundler, no framework, no external requests.

---

## Production Readiness & Safety Controls

- **Idempotency everywhere** — refunds and tool executions accept
  idempotency keys; re-submitted tickets/webhooks never double-refund
  (already-refunded charges resolve as `already_refunded`).
- **Dry-run mode** — preview a resolution end-to-end with
  `dry_run=True` (per ticket, per config `policies.dry_run`, or
  registry-wide) to validate before going live.
- **Approval thresholds** — set `policies.auto_refund_limit` and/or
  `policies.approval_required`; amounts above the limit resolve as
  `pending_approval` instead of auto-executing.
- **Audit trails** — every resolution is logged via
  `adapter.audit_log()` and every tool execution via the registry;
  inspect with `adapter.audit_trail` / `registry.get_audit_trail()`.
- **Offline-safe by default** — `TroyClient()` uses `MockDriver`, so
  demos/tests need no API keys and no network. Set `OPENAI_API_KEY`
  (and optionally `OPENAI_MODEL`) to use the OpenAI driver, or pass any
  `BaseLlmDriver` explicitly: `TroyClient(driver=my_driver)`.
- **Tenant isolation** — one client per tenant (`config` + `adapter` +
  `registry` + `driver`); share nothing between tenants except code.
- **Test it** — run the plug-and-play suite:
  `python -m pytest tests/test_saas_plug_and_play.py -q`
  (52 tests covering config, adapters, registry, SDK, APIs, webhooks,
  and the widget), then the full suite with `python -m pytest -q`.
