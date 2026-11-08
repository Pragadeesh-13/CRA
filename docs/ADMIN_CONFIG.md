# Admin-Configured CRA — Backend Vertical Slice

Endpoints (all admin-gated, fail-closed):
- `PUT /api/admin/configs/{tenant_id}` — validate + atomically persist tenant config
- `GET /api/admin/configs/{tenant_id}` — read one tenant's config
- `POST /api/admin/configs/{tenant_id}/dry-run` (alias `/validate`) — check mappings/policy, no external contact/mutation, no persistence
- `POST /api/admin/configs/{tenant_id}/connection-test` — read-only Stripe check (see below)
- `GET /admin` — minimal setup UI served from `admin_ui/` (not `public/`); token is in-memory only and sent as `X-Admin-Token`

**Enforced simulated policy (this fork):** Troy's duplicate-refund path now
calls `evaluate_policy()` (`app/admin_config.py`) before any action, using
`refund_cap_cents`, `approval_threshold_cents`, `escalation_target`, and
`dry_run`. Decisions: `allow` (simulated only), `deny`, `require_approval`,
`dry_run`, fail-closed `deny` on missing/mismatched/invalid config or unknown
action/amount. The decision + rationale are returned in `resolution_end`/
`council_end` as `policy_decision` and via system SSE.

**Not live execution:** even `allow` performs **no** Stripe call and **no**
customer-data/CSV mutation from Troy in this fork (`remediate_duplicate_charge`
is not called on the Troy path). The legacy bypass route
`POST /api/resolve/action` (previously `execute_agent/manual_resolution`
CSV mutation + Stripe-claimed refunds) is now **fail-closed/read-only (403,
never `database_updated`)** in this Admin-Configured fork; it is preserved
only in the standalone automation fork. Other mutating routes audited:
`/api/next` + `/api/trigger` go through policy-gated Troy (no live write),
`/api/config` is LLM-provider vault config (not a refund/customer write),
auth routes only create sessions.

**Tenant context (trusted principal only):** Troy accepts tenant context only
from the authenticated server-side principal. `resolve_trusted_tenant_id()`
(`app/auth/session.py`) reads a verified `tenant_id` custom claim from
`request.state.session_user` and validates it as a tenant slug; `main.py` then
attaches it to the story for `/api/next` and `/api/trigger` via
`_story_with_trusted_tenant()`, which strips any tenant field first. Query
params, JSON body fields, URL titles, and complaint text are **never** used to
select a tenant, and no default tenant is applied.

The auth stack now derives identity and tenant context **only** from a
server-verified Firebase ID token:

- `POST /api/auth/google` requires an `idToken`. `FirebaseTokenVerifier`
  (`app/auth/firebase_verifier.py`) calls the official Firebase Admin SDK
  `verify_id_token(id_token, app=<app>, check_revoked=True)`, which checks the
  signature, issuer, audience (the configured project), expiry, and revocation.
- Only the decoded claims are registered: `uid` (or `sub`), `email`, `name`, and
  a verified `tenant_id` custom claim validated as a CRA tenant slug. The
  `org_id` claim is **ignored** and never authorizes a tenant. Body profile and
  tenant fields are **ignored**; forged fields are never reflected.
- Fail-closed: missing token → 400; invalid/expired/wrong-audience/revoked token
  → 401; SDK missing, credentials unavailable, or `FIREBASE_PROJECT_ID` unset →
  503. Error bodies are generic and never echo SDK, project, or credential
  detail.
- `AUTH_MODE=required` rejects an unrecognized bearer with 401 instead of
  downgrading it to the guest projection. Optional/dev login still allows guests
  but grants no `tenant_id`, so `resolve_trusted_tenant_id()` returns
  `None` and tenant-scoped work fails closed.
- No default tenant and **no default Firebase project/key** are shipped.

Remaining limits: admin role and per-tenant membership are still not verified
claims (admin routes still require `X-Admin-Token`), and Firebase is the only
supported identity provider. Until a real verified `role`/membership claim
exists, keep deriving the tenant only from the verified principal and do not
add a default tenant.

*IdP claim / middleware contract still open:*
1. Add a verified `role`/`admin` claim (or server-side role store) so admin
   routes stop depending on the shared token.
2. Store per-user tenant membership plus roles and emit audit records for admin
   config writes.
3. Keep putting only the **issuer-asserted** `tenant_id` custom claim into
   `request.state.session_user["tenant_id"]`; it must always
   be a CRA tenant slug (`^[a-z0-9][a-z0-9_-]{1,63}$`) or a trusted
   admin-configured mapping. It must never come from client-supplied JSON/query.
   Tenancy is canonicalized on `tenant_id` only; do not use `org_id`.

Connection test (`app/integrations/`): `EnvSecretResolver` resolves only
`env:NAME`; `vault:`/`secret:` return `resolver_not_configured`. `StripeAdapter`
calls `GET https://api.stripe.com/v1/account` with a 5s timeout, returns only
`account_id`/`livemode`/status, and never logs or returns the secret.

Config shape: `tenant_id`, `integration{name, external_tenant_id, secret_ref}`,
`field_mappings{customer_id, order_id, transaction_id, ...}`,
`resolution_policy{refund_cap_cents, approval_threshold_cents, escalation_target, allow_auto_refund}`.
Secrets are references only (`vault:`/`env:`/`secret:`); raw credentials are rejected.

Runtime path: `$CRA_ADMIN_CONFIG_DIR` or `runtime/admin_configs/` (gitignored).
Per-tenant files `<tenant_id>.json`, written atomically (tmp + `os.replace`).

## Auth gap (minimum)
Firebase ID-token verification is now implemented for `POST /api/auth/google`
(see "Tenant context" above): signature, issuer, audience, expiry, and
revocation are checked server-side and only decoded claims are trusted.

Still missing: an admin role. Firebase login does not yet assert `role ==
"admin"`, so admin routes require an authenticated session **and**
`X-Admin-Token` matching `CRA_ADMIN_TOKEN` (or a future verified `role ==
"admin"` claim). Without it: 403.

Remaining work: verify a real IdP role/group claim at login, store per-user
roles + per-tenant membership, and add audit records for config writes. Until
then the shared admin token must be treated as a high-value secret and rotated
via env only.

## Firebase setup (required before client login works)
The fork ships **no** project or key values. Configure your own Firebase project
before login will work. All of these are **public web/client** values (safe to
return to the browser) except `FIREBASE_SERVICE_ACCOUNT`, which is a server
credential and must never be shipped to the client.

Server-side (verifier):
1. `FIREBASE_PROJECT_ID` — your Firebase project ID. The verifier fails closed
   with a generic 503 while this is unset.
2. `FIREBASE_SERVICE_ACCOUNT` — server credential: either a path to a
   service-account JSON file or inline JSON. Leave unset to use Application
   Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS`). Never commit this
   value; keep it only in an ignored local `.env` (this repo loads `.env`, and
   `.env` is gitignored). Do not reuse or reconstruct the web values for it.

Client config, returned verbatim by `GET /api/auth/firebase-config`:
3. `FIREBASE_WEB_API_KEY` — the Firebase web API key.
4. `FIREBASE_AUTH_DOMAIN` — Firebase `authDomain`.
5. `FIREBASE_STORAGE_BUCKET` — Firebase `storageBucket`.
6. `FIREBASE_MESSAGING_SENDER_ID` — Firebase `messagingSenderId`.
7. `FIREBASE_APP_ID` — Firebase `appId`.
8. `FIREBASE_MEASUREMENT_ID` — Firebase `measurementId`.

While `FIREBASE_PROJECT_ID` or `FIREBASE_WEB_API_KEY` is unset, the
`firebase-config` endpoint returns 503 with `configured: false` and advertises
no project; the browser treats this as "Firebase login unavailable". When set,
the endpoint returns `configured: true` and all fields above. No default/derived
project values are emitted.

The `firebase-admin` SDK (`requirements.txt`) is imported lazily; if it is not
installed, verification fails closed (503) rather than trusting an unverified
token. Only Firebase is supported as an identity provider.
