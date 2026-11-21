
# Complaint Resolution Agent (CRA) — Troy

**Troy** classifies customer complaints, checks records supplied by a data adapter, and can execute a supported remediation through a configured tool. The operator console uses example scenarios and a local CSV ledger.
---

## Troy as an Open-Source Tool

Troy is a clean, plug-and-play, **open-source tool you can use directly** —
zero-config: there is nothing to create, register, or configure first.
You install the repo, import `troy`, and call it. Works out of the box
with in-memory data and a local LLM (LM Studio) with no API keys
required; bring your own config, data adapter, or LLM when you want.

### Open-source Python library usage

```python
import troy

# One-shot resolution — no agent instance required:
result = troy.resolve('complaint text')
print(result['ok'], result['customer_communication'])

# Or create an agent instance for your app:
agent = troy.Troy(product_name='MyApp')

# Register your own custom tool with the @agent.tool decorator:
@agent.tool(name="reset_quota", description="Reset a customer's quota")
def reset_quota(customer_id: str = "", **kwargs):
    return {"ok": True, "customer_id": customer_id, "quota": "reset"}

result = agent.resolve('complaint text')
print(result['ok'], result['status'], result['customer_communication'])
```

Canonical exports: `Troy`, `resolve`, `tool`, `load_config`,
`CsvDataAdapter`, `InMemoryDataAdapter`, `RestApiDataAdapter`.

```python
import troy

config = troy.load_config({'product_name': 'MyApp', 'domain': 'example.com'})
adapter = troy.InMemoryDataAdapter(
    customers=[{
        'customer_id': 'USR-10492',
        'customer_name': 'Alex Morgan',
        'reference_order_id': 'ORD-88219',
    }],
    transactions=[
        {
            'transaction_id': 'TXN-9011',
            'customer_id': 'USR-10492',
            'order_id': 'ORD-88219',
            'transaction_date': '2026-10-20',
            'amount': '49.00',
            'status': 'Settled',
        },
        {
            'transaction_id': 'TXN-9012',
            'customer_id': 'USR-10492',
            'order_id': 'ORD-88219',
            'transaction_date': '2026-10-21',
            'amount': '49.00',
            'status': 'Settled',
        },
    ],
)
agent = troy.Troy(config=config, adapter=adapter)
result = agent.resolve(
    'I was charged twice for order ORD-88219, customer USR-10492.',
    dry_run=True,
)
print(result['status'], result['summary'])
print(result['customer_communication'])
```

With LM Studio stopped, Troy uses its built-in message template.

### Jev classification with LM Studio Bionic

Load a decision model in Bionic or LM Studio and start its API server. Use the model's server identifier as `TROY_JEV_MODEL`.

```bash
export TROY_JEV_MODEL='your-decision-model-id'
python troy.py 'Webhook deliveries are timing out with HTTP 504.' --dry-run --json
```

The classifier uses `POST /v1/systemone` and scores all 11 complaint categories by default. The Python SDK, CLI, and SaaS API use the categories from the product configuration. The operator console and triage API use the standard CRA categories.

The endpoint defaults to `LMSTUDIO_BASE_URL` or `http://localhost:1234/v1`. Set `TROY_JEV_BASE_URL` if the decision model runs on a different server and `TROY_JEV_API_KEY` if that server requires a token. Set these variables in `.env` for the web server or export them for CLI and Python usage. Keep `LLM_MODEL` set to your chat model for customer replies and summaries.

When `TROY_JEV_MODEL` is unset, Troy uses its existing classifiers. If the decision server is unavailable or returns an invalid answer, the SDK uses its existing intent-based routing and the console and triage API use their rules fallback. Classification does not authorize actions. The SDK still checks the complaint, the records, and the configured tools and policies.

### CLI usage

```bash
# Resolve a complaint from the command line:
python troy.py 'complaint text'

# Same, as machine-readable JSON:
python troy.py 'complaint text' --json

# Preview without mutating data / control auto-execution:
python troy.py 'complaint text' --dry-run --json
python troy.py 'complaint text' --auto-execute --json

# Serve Troy over HTTP (FastAPI app):
python troy.py --serve --port 3000

# Other CLI commands:
python troy.py --help
python troy.py --version
```

### Tests

```bash
python -m pytest tests/test_troy_opensource_tool.py tests/test_saas_plug_and_play.py -v
```

---

## Supported SDK actions

| Request | Category and default team | Tool | Verified inputs |
|---|---|---|---|
| Duplicate charge | Refunds · Billing | `issue_refund` (default) | Matching customer, order and transactions. Two distinct settled charges with equal positive amounts, currency and billing cycle when recorded. |
| Invoice delivery | Billing · Billing | `send_invoice` | `customer_id`, `invoice_id` |
| Account lockout | Authentication & Access · Tier 1 Support | `unlock_account` | `customer_id` |
| Subscription credit | Subscriptions · Billing | `apply_subscription_credit` | `customer_id`, `subscription_id`, `amount` |
| Webhook retry | Integrations & APIs · Engineering | `retry_webhook` | `customer_id`, `event_id` |

Register the exact tool name with `agent.register_tool(..., name=...)` or pass a configured tool registry when constructing the SDK or SaaS router. The default agent and standalone CLI have no invoice, unlock, credit or webhook integration. Specify the resource ID in the complaint or in the request context. Tools receive identifiers checked against the adapter's customer record. Store `invoice_id`, `subscription_id` or `event_id` on that record. The adapter can also provide lists in `invoice_ids`, `subscription_ids` or `event_ids`. Credits use the primary subscription's stored `subscription_credit_amount`. A caller-supplied amount cannot replace a ledger or credit record. Let the tool enforce service-specific authorization, account recovery, recipient and delivery rules.

`approval_required`, the refund and credit limits, `dry_run`, and `auto_execute` control dispatch. The SDK, CLI JSON and SaaS routes return the same outcome and report. `needs_review` means a tool, verified evidence or consistent instructions are missing. `pending_approval`, `dry_run` and `not_executed` leave the action open. `action_failed` means execution failed and `action_unconfirmed` means the tool did not confirm completion. A draft customer message is included in the result.

## Operator console demo

The walkthrough illustrates the demo workflow. https://github.com/user-attachments/assets/a54769be-e2e2-4f77-818a-992695f4c85f

The console classifies complaints and shows recommended teams, SLA estimates, case references and drafted responses. With the default configuration it displays static category examples.

Refund and flag-for-review actions in the console can update `data/transactions.csv` and its demo account records. The console has no payment gateway or notification service. Non-refund action and routing cards illustrate the workflow with demo records. For real business actions, configure tools on an SDK or SaaS client and authorize the caller before dispatch.

### Optional local precedent search

Import a de-identified CSV in the [documented ticket format](dataset_details.md) with a Python build that includes SQLite FTS5. Keep the source and index outside the web root.

```bash
python -m app.precedents /path/to/complaints-deduplicated.csv /path/to/precedents.sqlite3
TROY_PRECEDENT_INDEX=/path/to/precedents.sqlite3 python run.py
```

The importer indexes `Resolved` and `Closed` rows with resolution notes. Use `--replace` to rebuild an existing index. Triage searches complaint text against the indexed issue descriptions and returns up to three keyword matches with the original ticket IDs and notes. The console labels these as local corpus results. The search returns no similarity percentages, and corpus text is kept out of model prompts.

A configured index with no matches or an unreadable index produces an empty result with an explicit search status. An unset `TROY_PRECEDENT_INDEX` retains static category examples. The triage API and console return the notes verbatim. Use only data approved for those audiences and enable `AUTH_MODE=required` for restricted deployments.

## Quickstart

### Prerequisites

- Python 3.11+
- (Optional) [LM Studio](https://lmstudio.ai/) for running local LLMs, or an API key for cloud providers (OpenAI, Anthropic, OpenRouter, Groq).

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/Pragadeesh-13/CRA.git
cd CRA

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate      # On Windows
# source .venv/bin/activate  # On Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

Create your `.env` file (or copy from `.env.example`):
```bash
cp .env.example .env
```

Configure your preferred LLM provider in `.env` (defaults to local LM Studio on port 1234, or set `LLM_PROVIDER=openai` / `anthropic` with your API key).

For a shared local demo, leave `AUTH_MODE=optional`. Guest sessions share the demo workspace. For account access, set `AUTH_MODE=required` or `production`, configure `FIREBASE_PROJECT_ID` and `FIREBASE_WEB_API_KEY` for your Firebase project, enable the desired Firebase sign-in methods, and serve the console over HTTPS. Password signup creates a Firebase account and sends an email verification link. The server checks credentials and email verification before starting an account session. Google sign-in exchanges a verified Firebase ID token for the same kind of session. Application sessions are held in process and expire after 12 hours.

### 3. Launch the Application

```bash
python run.py
```

Open your browser at **http://localhost:3000** to enter the Complaint Resolution Agent console.

---

## Running Tests

Run the test suite to verify classification, demo case reporting, database actions, and resolution workflows:

```bash
pytest
```

---

## License

This project is licensed under the MIT License.
