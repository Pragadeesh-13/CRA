import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.providers import create_driver, provider_base_url, PROVIDER_REGISTRY
from app.simulation import SimulationState, RoundBarrier, Troy, CouncilOrchestrator, SessionRegistry
from app.http.sse_manager import SseManager
from app.auth import (
    KeyVault,
    SessionGuard,
    requires_key,
    resolve_trusted_tenant_id,
    FirebaseTokenVerifier,
    TokenVerificationError,
)
from app.database import (
    read_all_transactions,
    read_all_customers,
    read_all_tickets,
    verify_customer_billing,
    analyze_customer_transactions,
    execute_agent_resolution,
    execute_manual_resolution,
    find_customer_by_id_or_query,
)

START_TIME = time.time()

# Shared boot-time singletons
sse_manager = SseManager()
state = SimulationState()
barrier = RoundBarrier()

# Primary LLM driver
driver = create_driver(
    settings.llm_provider,
    model=settings.llm_model,
    api_key=settings.llm_api_key,
    base_url=provider_base_url(settings.llm_provider) or "",
    public_url=settings.public_url,
    timeout_ms=settings.llm_timeout_ms,
    stream_idle_timeout_ms=settings.llm_stream_idle_timeout_ms,
)

orchestrator = Troy(
    config=settings,
    driver=driver,
    sse_manager=sse_manager,
    state=state,
    barrier=barrier,
)

session_registry = SessionRegistry(config=settings, driver=driver)
key_vault = KeyVault(encryption_secret=settings.key_encryption_secret)
firebase_verifier = FirebaseTokenVerifier(
    project_id=settings.firebase_project_id,
    service_account=settings.firebase_service_account,
)
guard = SessionGuard(mode=settings.auth_mode)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: clean decoupled startup without legacy ML warmup
    yield
    # Shutdown: clean up SSE connections and sessions
    session_registry.close_all()
    sse_manager.close_all()
    key_vault.dispose()


app = FastAPI(title="Agentic complaint resolution system", version="3.0.0", lifespan=lifespan)

# CORS configuration
origins = settings.allowed_origins if settings.allowed_origins else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_session_middleware(request: Request, call_next):
    path = request.url.path
    if guard.is_protected_path(path):
        auth_res = await guard.authenticate(request)
        if not auth_res.get("ok"):
            return JSONResponse(status_code=auth_res.get("status", 401), content={"ok": False, "error": auth_res.get("error", "Unauthorized")})
        request.state.session_user = auth_res.get("user")
    else:
        request.state.session_user = None
    response = await call_next(request)
    return response


def get_active_session(request: Request):
    user = getattr(request.state, "session_user", None)
    return session_registry.resolve(user)


def _story_with_trusted_tenant(story: Dict[str, Any], tenant_id: Optional[str]) -> Dict[str, Any]:
    """Attach ``tenant_id`` to a story using ONLY a trusted principal claim.

    Any tenant field already present is dropped so a value injected via request
    body/query/title/complaint text can never reach the orchestrator. When no
    trusted claim exists the story carries no tenant context, preserving
    fail-closed policy behavior.
    """
    clean = {k: v for k, v in story.items() if k not in ("tenant_id", "tenant")}
    if tenant_id:
        clean["tenant_id"] = tenant_id
    return clean


def _request_trusted_tenant(request: Request) -> Optional[str]:
    """Resolve tenant context from the authenticated server-side principal only."""
    return resolve_trusted_tenant_id(getattr(request.state, "session_user", None))


async def resolve_session_config(request: Request) -> Dict[str, Any]:
    user = getattr(request.state, "session_user", None)
    stored = await key_vault.get(user)
    provider = (stored.get("provider") if stored else None) or settings.llm_provider
    model = (stored.get("model") if stored else None) or settings.llm_model
    api_key = (stored.get("apiKey") if stored else None) or settings.llm_api_key
    base_url = provider_base_url(provider) or ""

    session_driver = create_driver(
        provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        public_url=settings.public_url,
        timeout_ms=settings.llm_timeout_ms,
        stream_idle_timeout_ms=settings.llm_stream_idle_timeout_ms,
    )

    return {
        "provider": provider,
        "model": model,
        "apiKey": api_key,
        "baseUrl": base_url,
        "driver": session_driver,
        "vaultRecord": stored,
    }


# ── Health Endpoints ──────────────────────────────────────────────────────────

@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {
        "ok": True,
        "status": "healthy",
        "uptimeSeconds": round(time.time() - START_TIME),
        "authConfigured": True,
        "version": "3.0.0",
    }


# ── SSE Stream Endpoint ───────────────────────────────────────────────────────

@app.get("/api/stream")
async def stream_events(request: Request):
    session = get_active_session(request)
    sess_cfg = await resolve_session_config(request)
    q: asyncio.Queue = asyncio.Queue()
    session.sse_manager.add_client(q)

    # Initial connection status frame
    p_prov = sess_cfg["provider"]
    p_mod = sess_cfg["model"]
    init_status = (
        f"event: status\ndata: "
        f'{{"message": "Waiting for resolution signal.", "provider": "{p_prov}", "model": "{p_mod}", "connection": "connected"}}\n\n'
    )
    await q.put(init_status)

    latest = session.orchestrator.get_latest_story()
    if latest:
        await q.put(f"event: topic\ndata: {{\"title\": \"{latest.get('title', '')}\"}}\n\n")
        import json
        await q.put(f"event: feed\ndata: {json.dumps(latest)}\n\n")

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Wait up to 15 seconds for a frame, else yield ping keepalive
                    frame = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield frame
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            session.sse_manager.remove_client(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Ticket & Simulation Endpoints ─────────────────────────────────────────────

@app.get("/api/policy")
@app.get("/api/ticket")
@app.get("/api/cra/ticket")
async def get_ticket(request: Request):
    session = get_active_session(request)
    story = session.orchestrator.get_latest_story()
    return {"story": story or None, "ticket": story or None}


@app.get("/api/customer/verify")
async def verify_customer(query: Optional[str] = None, customer_id: Optional[str] = None, category: Optional[str] = None):
    """Analyzes customer transaction and account records across all 11 categories."""
    from app.database import get_category_complaint_context, analyze_customer_transactions
    from app.triage import predict_category

    cat = (category or "").strip()
    if not cat and query:
        try:
            pred = predict_category(query)
            cat = pred.get("predicted_category", str(pred)) if hasattr(pred, "get") else str(pred)
        except Exception:
            pass

    ctx = get_category_complaint_context(category=cat, query=query or "")
    if not ctx.get("is_refund", False):
        return {
            "ok": True,
            "verified": True,
            "category": ctx.get("category"),
            "customer_id": ctx.get("customer_id"),
            "customer_name": ctx.get("customer_name"),
            "customer_email": ctx.get("customer_email"),
            "subscription_plan": ctx.get("subscription_plan"),
            "reference_id": ctx.get("reference_id"),
            "system_label": ctx.get("system_label"),
            "database_file": ctx.get("database_file"),
            "status_pill": ctx.get("status_pill"),
            "action_btn_text": ctx.get("action_btn_text"),
            "system_details": ctx.get("system_details"),
            "steps": ctx.get("steps"),
            "customer": {
                "customer_id": ctx.get("customer_id"),
                "customer_name": ctx.get("customer_name"),
                "customer_email": ctx.get("customer_email"),
                "subscription_plan": ctx.get("subscription_plan"),
                "account_status": ctx.get("status_pill"),
                "refund_status": "N/A",
                "refund_id": ctx.get("reference_id"),
                "account_balance": "$0.00 (Current)",
            },
            "is_already_refunded": False,
        }

    res = analyze_customer_transactions(customer_id=customer_id, query=query)
    return res


@app.get("/api/database/transactions")
@app.get("/api/database/customers")
@app.get("/api/database/tickets")
async def get_database_transactions():
    transactions = read_all_transactions()
    return {"ok": True, "transactions": transactions, "customers": transactions, "count": len(transactions), "database_file": "data/transactions.csv"}


@app.post("/api/resolve/action")
async def post_resolve_action(request: Request):
    """Admin-Configured fork: fail closed / read-only.

    Live writes are out of scope in this variant, and this legacy route
    previously bypassed tenant policy via execute_agent/manual_resolution
    (CSV mutation + Stripe-claimed refunds). It must not mutate customer
    data or claim success until a trusted tenant context + runtime policy
    enforcement exists (see docs/ADMIN_CONFIG.md). Preserved in the
    standalone automation fork; closed here.
    """
    return JSONResponse(
        status_code=403,
        content={
            "ok": False,
            "action": "agent_resolve",
            "decision": "deny",
            "status": "deny",
            "rationale": "live writes disabled in Admin-Configured variant: /api/resolve/action is read-only/fail-closed and bypasses tenant policy",
            "error": "live writes disabled in Admin-Configured variant",
            "mutated_external": False,
            "mutated_customer_data": False,
            "external_contact": False,
        },
    )


@app.get("/api/trigger")
async def manual_trigger(request: Request, title: str = "Manual resolution trigger"):
    session = get_active_session(request)
    if session.orchestrator.is_running():
        return JSONResponse(status_code=409, content={"ok": False, "running": True, "error": "A resolution is already in progress."})

    sess_cfg = await resolve_session_config(request)
    story = {
        "title": title,
        "source": "Manual",
        "url": "",
        "publishedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "description": "Manual trigger from query parameter.",
    }
    # Tenant context comes only from the trusted principal claim; the `title`
    # query parameter is untrusted and can never select a tenant.
    story = _story_with_trusted_tenant(story, _request_trusted_tenant(request))
    session.orchestrator.set_story(story)
    session.sse_manager.broadcast("topic", {"title": story["title"]})
    session.sse_manager.broadcast("feed", story)

    asyncio.create_task(session.orchestrator.run(story, driver=sess_cfg["driver"]))
    return {"ok": True, "story": story}


@app.post("/api/round/begin")
async def begin_round(request: Request):
    body = await request.json()
    round_num = body.get("round")
    session = get_active_session(request)
    result = session.orchestrator.mark_round_begun(round_num)
    if not result.get("ok"):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Invalid round number"})
    return {"ok": True, "round": result.get("round")}


@app.post("/api/next")
@app.post("/api/ticket/next")
async def next_ticket(request: Request, query: str = ""):
    session = get_active_session(request)
    sess_cfg = await resolve_session_config(request)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    ticket_query = (body.get("query") if isinstance(body, dict) and body.get("query") is not None else query) or ""
    ticket_query = str(ticket_query).strip()

    if not ticket_query:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Complaint ticket details have not been entered."})

    provider = str(body.get("provider") if body.get("provider") is not None else sess_cfg.get("provider", "")).strip().lower()
    api_key = str(body.get("apiKey") if body.get("apiKey") is not None else sess_cfg.get("apiKey", "")).strip()
    model = str(body.get("model") if body.get("model") is not None else sess_cfg.get("model", "")).strip()

    if not provider:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Provider has not been selected."})

    if provider not in PROVIDER_REGISTRY:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Unsupported provider '{provider}'."})

    if not model:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Model has not been selected."})

    if requires_key({"provider": provider}) and not api_key:
        return JSONResponse(status_code=400, content={"ok": False, "error": "API key has not been entered."})

    user = getattr(request.state, "session_user", None)
    await key_vault.put(user, {"provider": provider, "model": model, "apiKey": api_key})

    # Tenant context is resolved once from the trusted principal claim; the
    # `query`, `provider`, `model`, and any other body/query fields are ignored
    # for tenant selection. No trusted claim => story has no tenant (fail closed).
    trusted_tenant = _request_trusted_tenant(request)

    # Build active run driver
    run_driver = create_driver(
        provider,
        model=model,
        api_key=api_key,
        base_url=provider_base_url(provider) or "",
        public_url=settings.public_url,
        timeout_ms=settings.llm_timeout_ms,
        stream_idle_timeout_ms=settings.llm_stream_idle_timeout_ms,
    )

    if session.orchestrator.is_running():
        # Allow starting a new resolution by releasing any lingering state
        session.orchestrator.state.release()
        await asyncio.sleep(0.02)

    async def _run_flow():
        session.sse_manager.broadcast("ticket_summarizing", {"query": ticket_query})
        session.sse_manager.broadcast("policy_summarizing", {"query": ticket_query})
        story = await session.orchestrator.summarise_ticket(ticket_query, driver=run_driver)
        if story:
            story = _story_with_trusted_tenant(story, trusted_tenant)
            await session.orchestrator.run(story, driver=run_driver)

    asyncio.create_task(_run_flow())
    return {"ok": True, "started": True}


# ── Admin-Configured CRA Endpoints (fail-closed; see app/admin_config.py) ────
import hmac as _hmac

def _admin_store():
    from app.admin_config import AdminConfigStore
    from pathlib import Path as _Path
    _dir = getattr(settings, "admin_config_dir", "") or ""
    return AdminConfigStore(_Path(_dir) if _dir else None)

def _require_admin(request: Request):
    """Fail-closed admin gate. Returns error response or None if authorized.

    Existing auth has no admin role (see docs/ADMIN_CONFIG.md), so require
    BOTH an authenticated session user AND CRA_ADMIN_TOKEN via X-Admin-Token,
    or a future explicit admin role claim.
    """
    user = getattr(request.state, "session_user", None)
    if not user:
        return JSONResponse(status_code=403, content={"ok": False, "error": "Admin authentication required (no authenticated session)"})
    if user.get("role") == "admin" or user.get("is_admin") is True:
        return None
    expected = getattr(settings, "admin_token", "") or ""
    provided = request.headers.get("x-admin-token") or ""
    if expected and provided and _hmac.compare_digest(provided, expected):
        return None
    return JSONResponse(status_code=403, content={"ok": False, "error": "Admin credential required (X-Admin-Token missing/invalid or no admin role)"})

@app.put("/api/admin/configs/{tenant_id}")
async def put_admin_config(request: Request, tenant_id: str):
    deny = _require_admin(request)
    if deny is not None:
        return deny
    from app.admin_config import is_valid_tenant_id, validate_config
    if not is_valid_tenant_id(tenant_id):
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid tenant_id"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Request body must be a JSON object"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Request body must be a JSON object"})
    cfg = body.get("config") if isinstance(body.get("config"), dict) else body
    if not cfg.get("tenant_id"):
        cfg = {**cfg, "tenant_id": tenant_id}
    errors = validate_config(cfg, expected_tenant=tenant_id)
    if errors:
        return JSONResponse(status_code=400, content={"ok": False, "errors": errors})
    try:
        saved = _admin_store().save(cfg)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    return {"ok": True, "config": saved}

@app.get("/api/admin/configs/{tenant_id}")
async def get_admin_config(request: Request, tenant_id: str):
    deny = _require_admin(request)
    if deny is not None:
        return deny
    from app.admin_config import is_valid_tenant_id
    if not is_valid_tenant_id(tenant_id):
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid tenant_id"})
    cfg = _admin_store().load(tenant_id)
    if cfg is None:
        return JSONResponse(status_code=404, content={"ok": False, "error": "config not found"})
    return {"ok": True, "config": cfg}

async def _dry_run(request: Request, tenant_id: str):
    deny = _require_admin(request)
    if deny is not None:
        return deny
    from app.admin_config import is_valid_tenant_id, dry_run
    if not is_valid_tenant_id(tenant_id):
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid tenant_id"})
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    cfg = body.get("config") if isinstance(body.get("config"), dict) else (body if body else None)
    if cfg is None:
        cfg = _admin_store().load(tenant_id)
        if cfg is None:
            return {"ok": False, "valid": False, "errors": ["no stored config for tenant"], "checks": [], "external_contact": False, "mutated_external": False, "persisted": False}
    if isinstance(cfg, dict) and not cfg.get("tenant_id"):
        cfg = {**cfg, "tenant_id": tenant_id}
    result = dry_run(cfg, expected_tenant=tenant_id)
    return result

@app.post("/api/admin/configs/{tenant_id}/dry-run")
async def post_admin_dry_run(request: Request, tenant_id: str):
    return await _dry_run(request, tenant_id)

@app.post("/api/admin/configs/{tenant_id}/validate")
async def post_admin_validate(request: Request, tenant_id: str):
    return await _dry_run(request, tenant_id)

@app.post("/api/admin/configs/{tenant_id}/connection-test")
async def post_admin_connection_test(request: Request, tenant_id: str):
    """Read-only Stripe connection check. No writes, no persistence, no secret in logs/responses.

    Integration activation is read-only until runtime policy enforcement is
    implemented; configured policy is not enforced by Troy yet.
    """
    deny = _require_admin(request)
    if deny is not None:
        return deny
    from app.admin_config import is_valid_tenant_id
    from app.integrations import EnvSecretResolver, SecretResolutionError, StripeAdapter
    if not is_valid_tenant_id(tenant_id):
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid tenant_id"})
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    cfg = body.get("config") if isinstance(body.get("config"), dict) else (body if body else None)
    if cfg is None:
        cfg = _admin_store().load(tenant_id)
    if cfg is None:
        return {"ok": False, "read_only": True, "mutated_external": False, "error_type": "no_config", "error": "no stored config for tenant"}
    if isinstance(cfg, dict) and not cfg.get("tenant_id"):
        cfg = {**cfg, "tenant_id": tenant_id}
    integration = cfg.get("integration") if isinstance(cfg, dict) else None
    if not isinstance(integration, dict) or str(integration.get("name") or "").lower() != "stripe":
        return {"ok": False, "read_only": True, "mutated_external": False, "error_type": "unsupported_adapter", "error": "connection test not configured for this integration"}
    secret_ref = str(integration.get("secret_ref") or "")
    try:
        secret = EnvSecretResolver().resolve(secret_ref)
    except SecretResolutionError as e:
        return {"ok": False, "read_only": True, "mutated_external": False, "error_type": e.error_type, "error": e.message}
    result = await StripeAdapter().check_connection(secret, timeout_s=5.0)
    result["integration"] = "stripe"
    return result

# ── Configuration & Models Endpoints ──────────────────────────────────────────

@app.get("/api/config")
async def get_config(request: Request):
    session = get_active_session(request)
    sess_cfg = await resolve_session_config(request)
    vault_rec = sess_cfg.get("vaultRecord") or {}

    return {
        "ok": True,
        "provider": sess_cfg["provider"],
        "model": sess_cfg["model"],
        "baseUrl": sess_cfg["baseUrl"],
        "hasApiKey": bool(sess_cfg["apiKey"]),
        "maskedKey": vault_rec.get("maskedKey", ""),
        "persisted": bool(vault_rec.get("persisted", False)),
        "requiresKey": requires_key({"provider": sess_cfg["provider"]}),
        "running": session.orchestrator.is_running(),
        "showReasoning": getattr(settings, "show_reasoning", False),
    }


@app.post("/api/config")
async def post_config(request: Request):
    session = get_active_session(request)
    if session.orchestrator.is_running():
        return JSONResponse(status_code=409, content={"ok": False, "running": True, "error": "A resolution is already in progress."})

    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Request body must be a JSON object"})

    if "showReasoning" in body:
        settings.show_reasoning = bool(body["showReasoning"])

    sess_cfg = await resolve_session_config(request)
    provider = str(body.get("provider") or sess_cfg["provider"]).strip().lower()
    if provider not in PROVIDER_REGISTRY:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Unsupported provider '{provider}'."})

    model = str(body.get("model") if body.get("model") is not None else sess_cfg["model"]).strip()
    api_key = str(body.get("apiKey") if body.get("apiKey") is not None else sess_cfg["apiKey"]).strip()

    user = getattr(request.state, "session_user", None)
    saved = await key_vault.put(user, {"provider": provider, "model": model, "apiKey": api_key})

    session.sse_manager.broadcast("status", {"model": model, "provider": provider})

    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "baseUrl": provider_base_url(provider) or "",
        "hasApiKey": bool(api_key),
        "persisted": saved.get("persisted", False),
        "showReasoning": getattr(settings, "show_reasoning", False),
    }


@app.get("/api/models")
async def get_models(request: Request, provider: Optional[str] = None):
    sess_cfg = await resolve_session_config(request)
    target_provider = (provider or sess_cfg["provider"]).strip().lower()

    if target_provider not in PROVIDER_REGISTRY:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Unsupported provider '{target_provider}'."})

    api_key = request.headers.get("x-api-key") or sess_cfg["apiKey"] or ""
    temp_driver = create_driver(
        target_provider,
        api_key=api_key,
        base_url=provider_base_url(target_provider) or "",
    )
    res = await temp_driver.fetch_models(api_key=api_key)
    return res


# ── Auth Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/auth/firebase-config")
async def get_firebase_config():
    """Expose the *operator-configured* Firebase web config.

    Admin-Configured fork: no project/key defaults are shipped. When the project
    ID or web API key is unset, fail closed (503) instead of advertising a stale
    or placeholder project. The browser client treats a non-OK response as
    "Firebase login unavailable".
    """
    proj_id = (settings.firebase_project_id or "").strip()
    api_key = (settings.firebase_web_api_key or "").strip()
    if not proj_id or not api_key:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "configured": False,
                "error": "Firebase project is not configured",
            },
        )
    return {
        "ok": True,
        "configured": True,
        "apiKey": api_key,
        "authDomain": (settings.firebase_auth_domain or "").strip(),
        "projectId": proj_id,
        "storageBucket": (settings.firebase_storage_bucket or "").strip(),
        "messagingSenderId": (settings.firebase_messaging_sender_id or "").strip(),
        "appId": (settings.firebase_app_id or "").strip(),
        "measurementId": (settings.firebase_measurement_id or "").strip(),
    }


@app.post("/api/auth/signup")
async def signup(request: Request):
    body = await request.json()
    full_name = str(body.get("fullName", "")).strip()
    email = str(body.get("email", "")).strip()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    confirm_password = str(body.get("confirmPassword", ""))

    if not all([full_name, email, username, password, confirm_password]):
        return JSONResponse(status_code=400, content={"ok": False, "error": "All fields are required"})
    if len(password) < 6:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Password must be at least 6 characters"})
    if password != confirm_password:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Passwords do not match"})

    return JSONResponse(status_code=201, content={"ok": True, "message": "Account created successfully"})


@app.post("/api/auth/login")
async def login(request: Request):
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))

    if not username or not password:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Username and password are required"})

    # In guest/offline/optional mode, create a local session token
    user = {
        "id": f"user_{username}",
        "username": username,
        "fullName": username.capitalize(),
        "email": f"{username}@cra.local",
    }
    session_token = f"token_{username}"
    guard.register_session(session_token, user)
    response = JSONResponse(status_code=200, content={"ok": True, "user": user})
    response.set_cookie(key="__session", value=session_token, path="/", max_age=43200, samesite="lax")
    return response


@app.post("/api/auth/google")
async def google_auth(request: Request):
    """Verify a Firebase ID token and register ONLY server-decoded claims.

    Admin-Configured fork: body profile/tenant fields are ignored. Signature,
    issuer, audience, and expiry are checked by the Firebase Admin SDK. Fails
    closed: 400 missing token, 401 invalid token, 503 verifier/config unavailable.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    id_token = body.get("idToken")
    if not id_token or not isinstance(id_token, str):
        return JSONResponse(status_code=400, content={"ok": False, "error": "idToken is required"})

    try:
        user = await asyncio.to_thread(firebase_verifier.verify, id_token)
    except TokenVerificationError as e:
        return JSONResponse(
            status_code=e.status,
            content={"ok": False, "error": e.message, "error_type": e.error_type},
        )
    if not user or not user.get("id"):
        return JSONResponse(status_code=401, content={"ok": False, "error": "Invalid Firebase ID token"})

    guard.register_session(id_token, user)
    response = JSONResponse(status_code=200, content={"ok": True, "user": user, "idToken": id_token})
    response.set_cookie(key="__session", value=id_token, path="/", max_age=43200, samesite="lax")
    return response


@app.get("/api/auth/me")
async def get_me(request: Request):
    user = getattr(request.state, "session_user", None)
    if not user:
        from app.auth.session import extract_id_token
        auth_res = await guard.authenticate(request)
        if auth_res.get("ok") and auth_res.get("user"):
            user = auth_res.get("user")
    if not user:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Not authenticated"})
    return {"ok": True, "user": user}


@app.delete("/api/auth/keys")
async def delete_keys(request: Request):
    user = getattr(request.state, "session_user", None)
    removed = await key_vault.delete(user)
    return {"ok": True, "removed": removed}


# ── CRA Model Inference Endpoints ─────────────────────────────────────────────

@app.post("/api/cra/resolve")
@app.post("/api/cra/triage")
async def api_cra_resolve(request: Request):
    """
    Executes the full CRA machine learning intelligence pipeline for an incoming complaint.
    Returns predicted category, top-3 breakdown, extracted entities, top-3 similar precedents,
    recommended team routing, and estimated SLA hours.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    complaint = str(body.get("complaint") or body.get("query") or body.get("text") or "").strip()
    if not complaint:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Complaint text is required."})

    from app.triage import async_triage_complaint
    report = await async_triage_complaint(complaint)
    return {"ok": True, "report": report}


@app.post("/api/cra/classify")
@app.post("/api/cra/predict")
async def api_cra_classify(request: Request):
    """
    Fast category classification using the non-ML triage classifier.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    complaint = str(body.get("complaint") or body.get("query") or body.get("text") or "").strip()
    if not complaint:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Complaint text is required."})

    from app.triage import async_predict_category
    result = await async_predict_category(complaint)
    res_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    return {"ok": True, **res_dict}



# ── Admin Setup UI (served from admin_ui/, not public/) ─────────────────────
ADMIN_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "admin_ui")
if os.path.isdir(ADMIN_UI_DIR):
    @app.get("/admin")
    @app.get("/admin/")
    async def serve_admin_ui():
        return FileResponse(os.path.join(ADMIN_UI_DIR, "index.html"))

# ── Static Frontend Serving ───────────────────────────────────────────────────

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public")

if os.path.isdir(PUBLIC_DIR):
    @app.get("/")
    @app.get("/index.html")
    async def serve_index(request: Request):
        if settings.auth_mode == "required":
            auth_res = await guard.authenticate(request)
            if not auth_res.get("ok") or not auth_res.get("user"):
                return RedirectResponse(url="/login.html", status_code=302)
        return FileResponse(os.path.join(PUBLIC_DIR, "index.html"))

    @app.get("/login.html")
    async def serve_login(request: Request):
        if settings.auth_mode == "required":
            auth_res = await guard.authenticate(request)
            if auth_res.get("ok") and auth_res.get("user"):
                return RedirectResponse(url="/", status_code=302)
        return FileResponse(os.path.join(PUBLIC_DIR, "login.html"))

    @app.get("/signup.html")
    async def serve_signup(request: Request):
        if settings.auth_mode == "required":
            auth_res = await guard.authenticate(request)
            if auth_res.get("ok") and auth_res.get("user"):
                return RedirectResponse(url="/", status_code=302)
        return FileResponse(os.path.join(PUBLIC_DIR, "signup.html"))

    @app.get("/logout")
    async def logout(request: Request):
        from app.auth.session import extract_id_token
        token = extract_id_token(request)
        if token:
            guard.revoke_session(token)
        response = RedirectResponse(url="/login.html", status_code=302)
        response.delete_cookie(key="__session", path="/")
        return response

    app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")
