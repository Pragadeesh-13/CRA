# Troy feature audit

This audit follows a complaint from intake through the reported outcome. The public Python API, CLI and SaaS routes use the SDK. The operator console uses a separate orchestrator and a local sample ledger.

| Feature | What works in this checkout | What a deployment still needs |
|---|---|---|
| Python SDK and CLI | Sync and async complaint resolution, structured JSON, custom configuration, preview and tool registration. The in-memory example exercises the same public API that applications import. | A data adapter and resource identifiers for the customers being served. The shared default client is process-local. |
| Triage, routing and SLA | The console classifies eleven domains, extracts entities and returns a recommended team and SLA. It has an offline rules fallback. The SDK uses its own intent and policy logic. | A helpdesk connector to create assignments, track deadlines, record acknowledgments and close tickets in a real queue. |
| Historical precedents | Triage can search an operator-supplied resolved-ticket CSV using a local SQLite FTS5 index and return matching ticket IDs and resolution notes. Without an index it supplies labeled category examples. | An operator-owned corpus and an evaluation of search relevance on representative complaints. |
| Duplicate charges | The SDK checks scoped ledger evidence and policy before dispatch. It distinguishes preview, approval, skipped, failed and unconfirmed outcomes. The console can reconcile its local CSV records. | A payment integration to move money and reconcile the provider's final outcome. A sample CSV refund is a ledger change. |
| Invoice delivery and account unlocking | The SDK recognizes these requests. A registered business tool can use verified account/resource data. An unavailable tool leaves the request open for review. | Billing delivery and identity-provider integrations with customer authorization. Invoice generation, tax correctness and account recovery depend on those services. |
| Subscription credit and webhook retry | The SDK can dispatch configured handlers against verified records. | An approved credit amount and billing integration, or an identified event and delivery service. An asynchronous acknowledgment requires follow-up. |
| Other console playbooks | The demo presents diagnostic and routing scenarios for security, bugs, performance, data sync, onboarding and account administration. | Real telemetry and business operations such as filing a defect, replaying a queue or changing an account. |
| Data and action adapters | Memory, CSV and REST adapters provide customer lookup, ledger reads, actions and audit hooks. Explicit CSV paths isolate reads and writes. REST can report an accepted or unconfirmed action. | A backing service implementing the REST contract and durable audit storage. |
| Policies and replay | Execution controls include dry run, approval thresholds and disabled automatic execution. Tool replay checks the request parameters and prevents simultaneous same-key calls in one registry. | Durable provider-side idempotency for multiple processes, restarts and uncertain network outcomes. A pending approval requires an operator decision. |
| SaaS HTTP and webhooks | JSON endpoints accept complaints and normalize Zendesk, Intercom, Stripe, Freshdesk, Shopify, Slack and generic payloads. Request branding preserves the server's execution policy. | Provider event authentication, trusted customer mapping, delivery retries and write-back to the originating system. Payload normalization alone does not provide those services. |
| Operator console and streaming | SSE carries ticket and progress events. The console can inspect the sample ledger, review an action and display its result. Session ticket history uses submitted tickets. | Persistent ticket and event history for restart recovery or multi-worker operation. |
| Embedded widget | The browser widget submits a complaint, displays the result, and preserves a failed action's report. Its open and close controls survive repeated initialization. | An authorized endpoint and deployment-specific cross-origin settings. |
| Authentication and model keys | The application distinguishes guest use and required authentication. Model keys are sealed in process memory. | A configured identity provider for real accounts and host-managed credentials. Local session and key records expire or disappear on restart. |
| Model providers | Drivers support LM Studio, OpenAI-compatible endpoints and Anthropic. Local transport tests cover request construction, model selection, streaming and fallback paths. | Reachable endpoints, supported models and any required credentials. A generated customer response does not establish that a tool executed. |

## Verification scope

The automated suite exercises local ledgers, synthetic customers, mock identity and business responses, and a loopback model server. A Chrome smoke check exercised widget initialization, opening the drawer, submitting a complaint, and displaying a failed action's report. Live payment, identity, email, model and helpdesk accounts were outside the verification environment.

## Final verification

C2 completed its assigned fixes. All 601 tests passed in a clean snapshot of the final working tree, including the two loopback model-server tests. Pytest reported one dependency warning. The changes remain uncommitted in the shared repository.

The SDK, CLI and SaaS routes report `needs_review` when an actionable request lacks a configured tool or verified records. The suite covers configured business handlers, duplicate charges in different billing cycles, disabled execution, approval, dry run and account isolation. Required-mode identity verification used a mocked provider. The README example previewed a $49 refund with both transactions still settled.

The webhook relay can authenticate with `X-Troy-Webhook-Token` when `TROY_WEBHOOK_TOKEN` is configured. The upstream relay must authenticate the original vendor event.

For authenticated webhook relays, Troy derives a provider-scoped replay key when the caller supplies no idempotency key. The relay can set `X-Troy-Webhook-Event-Id` to the verified stable vendor event or delivery ID. Strip incoming copies of this header before setting it. Troy also recognizes Stripe event envelope IDs, Slack Events API event IDs, Intercom notification envelope IDs, and Shopify's `X-Shopify-Event-Id` on requests from the trusted relay. Use the relay header for other providers. Resource IDs such as ticket, order, customer, and conversation IDs do not identify a delivery. Replay is local to the existing in-memory tool registry, applies to tools with idempotency enabled, and lasts only for that registry's lifetime.

### Local review queue

Set `TROY_REVIEW_DB=/private/service/review.sqlite3` before starting the host, or pass `review_queue=ReviewQueue(path)` to `TroyClient` or `Troy`. Use a private, writable directory outside the web root. Keep a separate queue per product or tenant. The default SDK behavior uses no durable queue.

The queue saves manual-review, approval, disabled-execution, failed-action and unconfirmed-action cases. Dry runs and completed resolutions create no records. Each case stores a stable ID, disposition, category, team, summary, action name, available ledger references and timestamps. Raw complaint text, webhook payloads, tool parameters and credentials are excluded. The response provides `review_case_id` and `review_queue_status` after a successful write. An unavailable queue reports the failed save without issuing a receipt.

```bash
python -m app.review_queue --db /private/service/review.sqlite3 list
python -m app.review_queue --db /private/service/review.sqlite3 list --team Billing --overdue
python -m app.review_queue --db /private/service/review.sqlite3 list --status reviewed --team Billing
python -m app.review_queue --db /private/service/review.sqlite3 reviewed CASE_ID
python -m app.review_queue --db /private/service/review.sqlite3 closed CASE_ID
```

Open cases list overdue entries first. Use `--team` for a case-insensitive exact team name and `--overdue` to show only open cases whose deadline has been reached. The filters can be combined. New cases capture a UTC `due_at` by adding the SDK's SLA estimate to case creation time. Replay and later SLA configuration changes preserve that deadline. Old cases and cases without a usable estimate show `null` for both `due_at` and `overdue`.

The CLI uses local file access. Marking a case reviewed or closed records an operator decision. It never approves or executes a business tool. Verified webhook redelivery reuses a saved case across restarts, including reviewed or closed cases. Delivery keys are scoped to provider and customer when the customer is known. Separate providers, customers and delivery IDs have separate cases. Other SDK requests can reuse a case with a stable caller idempotency key and matching request.

With the queue enabled, SQLite admits one worker at a time for each verified delivery scope. An overlapping webhook receives HTTP 409 with a retry indication and creates no case. A successful action that requires no review still uses registry or upstream idempotency for later sequential deliveries. An interrupted process or failed review write after a possible dispatch can leave a claim for local recovery. A replay never reopens a case or resets its SLA. Concurrent reviewed and closed updates leave the case closed.

### Operator stream reconnect

The operator stream restores the current session's latest ticket and terminal resolution when a client reconnects or reloads. It restores the report, drafted sections, and completion or error status without rerunning the ticket. A new submission clears the active result while retaining the submitted-ticket history. In-progress streams resume with new events as they arrive. Partial token output and manual action receipts are transient. State lives in the session's current server process and expires with that session.

Review-queue audit reproduced a cross-customer case collision and duplicate dispatch in two simultaneous workers. The closure race test confirmed that a reviewed update cannot reopen a closed case. The fixed scratch suite passed 619 tests, with two loopback-server tests deselected.

### Widget review receipts

The widget displays the SDK's review case reference and distinguishes saved, reviewed, closed and unavailable queue outcomes. With no queue receipt, it reports manual review or approval as needed and leaves operator-only next steps in the SDK report. A failed action with no reported status gets no invented completion label.

The widget regression failed against the previous renderer because a queued case displayed “Report ready.” The updated renderer passed 71 focused scratch tests alongside SaaS and queue coverage.

### CSV adapter field retention

CSV ledger rewrites preserve host-defined columns such as invoice IDs, webhook event IDs and external payment references. A synthetic explicit-file test reproduced the loss of an invoice ID after updating a standard customer field. The fixed adapter preserved the custom columns and still dispatched the configured invoice tool. The scratch adapter and database run passed 108 tests.

### REST action acknowledgments

An HTTP action acknowledgment without a completion status, or a transport error before an outcome is received, now reports `action_unconfirmed` through the SDK. The result directs operators to verify the remote transaction before retrying. Synthetic REST tests reproduced a false completed refund and a timeout labeled as a definitive failure. The fixed SDK and adapter run passed 135 tests.

### Session reasoning preference

Toggling reasoning visibility in one signed-in console session previously changed the process-wide default and affected other users. The preference now applies to that session's stream. Invalid configuration requests leave it unchanged. The scratch reasoning, reconnect and API run passed 26 tests.

### Exact precedent references

The triage API returns ticket IDs above JavaScript's safe integer range as decimal strings. Smaller IDs remain numbers. A synthetic int64 corpus entry previously appeared with a different ID after JSON parsing. The import, triage and console regression now renders the exact ID, and 31 focused scratch tests passed.

### REST resource identifiers

The REST adapter encodes endpoint template values as path segments. A synthetic composite customer ID previously lost part of its value when substituted directly into the URL. The corrected request preserves the full identifier. The focused adapter and SaaS run passed 85 tests.

### Console configuration input

A malformed configuration body previously returned HTTP 500. It now returns the JSON validation error with HTTP 400 and preserves the current settings. The focused API and reasoning run passed 23 tests.

### Console ticket intake validation

Ticket intake rejects malformed and non-object JSON before starting a resolution, even when a legacy query parameter is present. A query-only request still works. The pre-change route accepted the malformed request and started work. The combined scratch suite passed 628 tests, with two loopback-server tests deselected.

The configuration endpoint now reports busy and rejects changes during ticket summarization, before the orchestrator begins streaming. The regression reproduced an idle response during a pending ticket task. The final scratch suite passed 629 tests, with two loopback-server tests deselected.

### Public JSON request validation

The console triage, classify, action, round-begin and auth POST routes now reject malformed or non-object JSON with HTTP 400 before dispatch. Triage and classification require text. Resolution actions require a ticket, customer, complaint query or explicit category. Round IDs must be non-negative whole numbers, and credential fields used by account signup and login must be strings. The regression fixtures preserve complaint aliases, the legacy ticket and login aliases, category-only demo actions, numeric round strings, and bodyless guest login. The baseline reproduced HTTP 500s, a malformed request reaching the action handler, and non-text complaints entering triage. Identity and action checks used synthetic data and mocked handlers.

The final scratch verification covered all 700 tests. The sandboxed run passed 698 tests, and the two mock loopback-server tests passed separately.

### SaaS and SDK tool request envelopes

SaaS resolve, provider webhooks and the SDK HTTP router require JSON objects. They reject bad complaint and tool name types, non-object context or tool argument envelopes, and malformed identifiers before dispatch. Existing aliases, numeric provider resource IDs, nested webhook messages, and arbitrary JSON values inside tool argument objects remain supported. An empty Stripe envelope no longer creates a complaint. When the trusted webhook relay supplies a delivery ID, an invalid ID yields HTTP 400.

The baseline reproduced 13 tool and webhook shape failures, including a malformed argument envelope reaching a registered handler and an empty Stripe payload being resolved. Regression fixtures used synthetic data and local handlers.

Scratch verification passed all 762 tests, including the two mock loopback-server tests run separately. `git diff --check` passed.

### Interrupted delivery claims

Inspect persisted claims and recover an interrupted delivery from the local review CLI.

```bash
python -m app.review_queue --db /private/service/review.sqlite3 claims
python -m app.review_queue --db /private/service/review.sqlite3 recover-claim CLAIM_ID --confirm-worker-stopped
```

The claim list shows the claim ID, acquisition time, worker host and PID, provider delivery reference, possible action, and any matching case when available. Age and worker identifiers are diagnostic. Confirm that the original worker has stopped before recovering its claim. Older records may have no diagnostic metadata.

Recovery atomically creates an `action_unconfirmed` review case and removes the exact claim. An existing case keeps its disposition. Scoped redelivery returns that case even after closure. Legacy unscoped claims remain blocked for reconciliation without returning a case receipt to another customer. Recovery does not call a provider or execute a business tool. Reconcile the action against the provider or ledger before marking the case reviewed or closed.

A synthetic worker exited immediately after recording a simulated side effect. Regression checks cover the blocked retry, CLI conversion, replay after closure, existing-case preservation, stale IDs, and rollback if claim cleanup fails. A second fixture reproduced a lost claim after a review-write failure and verifies that the claim is now retained.

Scratch verification passed all 767 tests, including the two mock loopback-server tests run separately. `git diff --check` passed.

### Reconnect after a lost run

A console that lost its accepted run during a stream disconnect previously stayed busy after reconnecting to an idle session. It now reports the interruption and enables **Begin Resolution** so the operator can submit again. A pending submission, a fresh idle boot, and a reconnect to a still-running session retain their normal behavior. The Node EventSource fixture reproduced the disabled button on the previous renderer and verified that the fixed console can submit again.

Scratch verification passed all 768 tests, including two local mock-server tests run separately. The JavaScript syntax check and `git diff --check` passed.

### Console complaint text validation

The console intake accepted a JSON array in `query`, converted it to complaint text, and returned HTTP 200 with a resolution started. Both `/api/next` and `/api/ticket/next` now return HTTP 400 for arrays, objects, numbers, and booleans before starting work, including when a legacy URL query is also supplied. JSON text is trimmed and takes precedence over the URL query. A missing or null JSON query still falls back to the URL query.

The regression reproduced HTTP 200 before the fix and verifies rejection on both aliases, no resolution task for invalid input, and delivery of valid text to the summarizer. All 769 tests passed in a scratch copy, including two local mock-server tests run separately.

### Model key scope on provider changes

Switching from OpenRouter to Anthropic caused model discovery to pass the saved OpenRouter key to the Anthropic driver. A synthetic driver recorded the mismatched provider and key locally. Model discovery, configuration writes, and ticket starts now fall back only to a key associated with the selected provider. The host key applies to the host's configured provider. The browser clears the key field before loading models for a newly selected provider. The vault still stores one provider configuration per session.

Regression coverage checks provider switches, same-provider fallback, explicit keys, and rejection of a ticket start when the newly selected hosted provider lacks a key. All 771 tests passed in a scratch copy, including two local mock-server tests run separately. The JavaScript syntax check and `git diff --check` passed.

### Starting a resolution with a saved model key

After a console reload, the API key field can be blank while the server has a key for the selected hosted provider. The browser previously stopped at **Begin Resolution** and asked for a key before making a request. It now submits the complaint and lets the server check for a saved or host-configured key. If the selected provider still needs a key, the console displays the server's error and re-enables the button.

The Node regression reproduced the missing request and verifies both the saved-key submission and the missing-key error. All 772 tests passed in a scratch copy, including two local mock-server tests run separately. JavaScript syntax and `git diff --check` passed.

### Saved model selection on console reload

Reloading the console replaced a saved hosted-provider model with the default when the saved model was absent from the example list. The console then saved the replacement locally and sent it to the server. The selector now retains the saved model as an option and keeps it selected when discovery fails or returns a list that omits it. Discovered models remain available in the selector.

The Node regression reproduced the default substitution and checks the displayed selection, local storage, and configuration POST in both discovery scenarios. All 773 tests passed in a scratch copy, including two local mock-server tests run separately. JavaScript syntax and `git diff --check` passed.

### Model discovery after entering a key

The console queried model discovery on provider selection but did not refresh it when the operator entered a key. Models available only after authentication could stay absent until a reload. The console now refreshes discovery after 400 ms without a key edit, cancels queued refreshes on provider change, and ignores responses from lookups superseded by a key edit.

The Node regression reproduced the missing authenticated lookup and verifies that rapid edits trigger one refresh with the final key and that a late earlier response cannot replace the model list. All 774 tests passed in a scratch copy, including two local mock-server tests run separately. JavaScript syntax and `git diff --check` passed.

### Truncated CSV during precedent index replacement

A synthetic CSV with an unclosed quote in the resolution notes caused the import CLI to absorb the following ticket into those notes. The CLI reported one indexed ticket and replaced the existing index. The importer now uses strict CSV parsing. The CLI exits with an error and preserves the existing index byte for byte. Well-formed quoted multiline notes still import and appear in triage.

The regression exercises the CLI with `--replace`. All 775 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### Finding reviewed cases before closure

Marking a case reviewed removed it from the only CLI listing, although closing it still required its ID. Use `list --status reviewed` to recover that ID and optionally filter by team. The default view still lists open cases. The overdue filter applies only to open cases.

The CLI regression verifies that a reviewed case can be found by team and leaves the reviewed view after closure. All 775 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### CSV field counts during precedent index replacement

An unquoted comma in a resolution note caused the import CLI to discard the rest of the note and report a successful index replacement. The importer now rejects rows with too many or too few fields, identifies the CSV line, and preserves the existing index. Properly quoted commas and multiline notes still appear intact in triage.

The CLI regression covers both field count mismatches with `--replace`. All 777 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### Duplicate headers in precedent CSV imports

With two `resolution_notes` headers, the importer silently took the second value and reported a successful index replacement. It now reports duplicate column names and preserves the existing index. The CLI regression exercises `--replace` and verifies that the index remains byte-for-byte intact.

All 778 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### YAML configuration root validation

The public `troy.load_config` loader silently loaded default settings for YAML documents containing `false`, `0`, an empty list, or an empty string. These values now raise the existing top-level mapping error. Blank documents and YAML null still load defaults.

File-based regressions reproduce all four invalid inputs and cover the blank-document fallback. All 783 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### CLI diagnostics for malformed JSON config

Malformed inline JSON and malformed JSON files passed to `--config` previously ended with a Python traceback. The CLI now reports the JSON syntax error with a line and column, exits with status 2, and produces no resolution output. The error omits the configuration contents.

The subprocess regression covers both input forms. All 785 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### REST customer lookup response shape

A successful upstream response containing text, null, or a list previously became a synthetic object and was reported as a found customer. The REST adapter now preserves the response shape so customer lookup can return no record and audit `found: false`. Transaction lookup still accepts bare lists of records and ignores non-record entries.

Mock-transport regressions reproduced the false lookups and verify bare-list transaction compatibility. All 789 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### REST lookup failure envelopes

An HTTP 200 response with `ok: false` could still supply a nested customer or transaction list to the SDK. The adapter now honors the top-level failure flag before extracting records. Failed customer lookups audit `found: false`, and successful envelopes still return their records.

The mock-transport regression reproduced both false lookups and checks the successful forms. All 790 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### Explicitly empty REST transaction results

An explicitly empty transaction list caused the REST adapter to fall through to a lower-priority alias and return records from that field. It now uses the first non-null field in the order `transactions`, `data`, `items`. An empty list yields no records. Lower-priority aliases still work when the preferred field is absent or null.

Mock-transport regressions reproduced all three alias combinations. All 793 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### Custom transaction filters on the default CSV adapter

The default CSV adapter silently dropped custom column filters. A synthetic ledger query for settled USD transactions also returned a settled EUR transaction for the same customer. The adapter now applies exact custom column matches alongside the existing built-in filters. A custom filter with no match returns an empty list.

The regression covers a combined status and currency query and a currency-only query with no matches. All 794 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### Transaction IDs in in-memory seed mappings

The in-memory adapter discarded transaction mapping keys, so a record whose ID appeared only as its key could not be found by ID. The adapter now uses the mapping key when the record omits `transaction_id`. Explicit IDs take precedence, and the caller's seed records are left intact.

The regression reproduced the empty exact-ID lookup and verifies both seed forms. All 795 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### Transaction filters on explicitly configured CSV ledgers

A transaction query using a differently capitalized transaction ID or status found a matching row with the default CSV adapter but missed the same row when the ledger path was explicit. Both paths now use the database's built-in filter normalization, with exact custom-column matching and the existing scoped file reads.

The regression runs both configurations against a synthetic ledger and verifies that customer and currency mismatches still return no rows. All 796 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### Order and transaction ID matching in memory

The in-memory adapter missed lowercase ledger IDs when the SDK looked up an uppercase order or transaction reference. Valid duplicate-charge previews fell into manual review. The adapter now normalizes order and transaction IDs before exact comparison, using the same normalization it already applies to customer IDs. Other filters retain their existing comparisons.

The public SDK regressions cover order-only and transaction-only previews and verify the ledger remains untouched. All 798 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### Missing transaction IDs in in-memory refunds

A direct call to the built-in in-memory refund action with a missing or whitespace-only transaction ID could match an unidentified ledger row and report a refund. The action now returns a failed result requiring a transaction ID and leaves the ledger untouched.

The regression reproduced both invalid calls. The scratch suite passed 800 tests, including two local mock-server tests run separately. After a final adjustment to the lookup guard, the focused refund checks passed again. `git diff --check` passed.

### Refund replay with normalized in-memory status

The built-in in-memory refund action treated a stored ` refunded ` status as a new refund and overwrote the original reference, amount, and timestamp. It now recognizes refunded status regardless of casing and surrounding whitespace, returns the original refund details, and preserves the ledger row.

The regression reproduced the overwrite path using a synthetic record. All 801 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.

### Ambiguous transaction IDs in in-memory refunds

The built-in in-memory refund action selected the first ledger row when multiple rows shared the same normalized transaction ID. A synthetic ledger with matching IDs on two customers reproduced a successful refund against the first row. The action now reports an ambiguous ID and leaves both rows unchanged.

The focused regression verifies the failure and both unchanged records. All 802 tests passed in a scratch copy, including two local mock-server tests run separately. `git diff --check` passed.
