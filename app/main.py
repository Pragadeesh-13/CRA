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
from app.auth import KeyVault, SessionGuard, requires_key
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

# ── Request value resolution helpers ────────────────────────────────────────
# The browser UI legitimately sends empty strings for values it does not
# manage (e.g. ``apiKey: ""`` for local providers such as lmstudio). Empty,
# falsy, or whitespace-only values must therefore *fall back* to the
# session / server configuration instead of overriding it — an explicit
# ``is not None`` check would keep the empty string and silently discard
# the configured key (causing llama-server 401s).

_GENERIC_MODEL_NAMES = {"generic", "default", "auto", "none", "null", "undefined", "select model", "select-model"}


def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _first_non_empty(*values: object) -> str:
    """Return the first value that is non-empty after stripping."""
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _resolve_model(value: object, fallback: object = None) -> str:
    """Resolve a model name, treating empty/generic placeholders as unset."""
    candidate = _clean(value)
    if not candidate or candidate.lower() in _GENERIC_MODEL_NAMES:
        candidate = _clean(fallback)
    if not candidate or candidate.lower() in _GENERIC_MODEL_NAMES:
        candidate = _clean(settings.llm_model)
    return candidate


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

# ── Plug-and-play SaaS integration surfaces (Phase 2) ───────────────────────
# Mounted additively; existing /api/next, /api/stream, /api/customer/verify,
# and /api/resolve/action endpoints below are unchanged.
from app.api.v1.saas_routes import router as saas_router

app.include_router(saas_router)

WIDGET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "widget", "troy-embed.js")


@app.get("/widget/troy-embed.js")
async def serve_widget():
    if os.path.exists(WIDGET_FILE):
        return FileResponse(WIDGET_FILE, media_type="application/javascript")
    return JSONResponse(status_code=404, content={"error": "Widget not found"})



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


async def resolve_session_config(request: Request) -> Dict[str, Any]:
    user = getattr(request.state, "session_user", None)
    stored = await key_vault.get(user)
    # Stored values may be empty/whitespace/generic (e.g. persisted by an
    # older client); fall back to server settings in all of those cases.
    provider = _first_non_empty(stored.get("provider") if stored else None, settings.llm_provider).lower()
    model = _resolve_model(stored.get("model") if stored else None, settings.llm_model)
    api_key = _first_non_empty(stored.get("apiKey") if stored else None, settings.llm_api_key)
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

    # Troy's decision: does this ticket require a database mutation /
    # human routing, or is it a direct response? The UI renders
    # action/routing buttons only when action_required is True.
    intent_info = Troy.evaluate_complaint_intent(query or "", category=cat or None)
    action_required = bool(intent_info.get("requires_db_mutation"))

    ctx = get_category_complaint_context(category=cat, query=query or "")
    if not ctx.get("is_refund", False):
        return {
            "ok": True,
            "verified": True,
            "action_required": action_required,
            "requires_action": action_required,
            "intent": intent_info,
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
    if isinstance(res, dict):
        res["action_required"] = action_required
        res["requires_action"] = action_required
        res["intent"] = intent_info
    return res


@app.get("/api/database/transactions")
async def get_database_transactions():
    transactions = read_all_transactions()
    return {"ok": True, "transactions": transactions, "customers": transactions, "count": len(transactions), "database_file": "data/transactions.csv"}


@app.get("/api/database/customers")
async def get_database_customers():
    customers = read_all_customers()
    return {"ok": True, "customers": customers, "count": len(customers), "database_file": "data/customer_accounts.csv"}


@app.get("/api/database/tickets")
async def get_database_tickets():
    tickets = read_all_tickets()
    return {"ok": True, "tickets": tickets, "count": len(tickets), "database_file": "data/transactions.csv"}


@app.post("/api/resolve/action")
async def post_resolve_action(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    action = body.get("action", "agent_resolve")
    customer_id = body.get("customer_id") or body.get("ticket_id")
    query = body.get("query")
    category = body.get("category")
    custom_action = body.get("action_details") or "Refund Initiated ($49.00 via Stripe API)"
    notes = body.get("notes")

    if not category and query:
        from app.triage import predict_category
        try:
            pred = predict_category(query)
            category = pred.get("predicted_category", str(pred)) if hasattr(pred, "get") else str(pred)
        except Exception:
            pass

    if action == "manual_resolve":
        res = execute_manual_resolution(customer_id=customer_id, query=query, notes=notes, category=category)
    else:
        res = execute_agent_resolution(customer_id=customer_id, query=query, action=custom_action, notes=notes, category=category)

    # Broadcast event so UI or stream clients know database was updated
    session = get_active_session(request)
    session.sse_manager.broadcast("database_updated", res)

    return res


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

    # Empty/whitespace values from the browser (e.g. apiKey: "" for
    # lmstudio) must fall back to the session config, which itself falls
    # back to settings.llm_api_key / settings.llm_model from the server
    # environment — never let "" clobber the configured key or model.
    provider = _first_non_empty(body.get("provider"), sess_cfg.get("provider"), settings.llm_provider).lower()
    api_key = _first_non_empty(body.get("apiKey"), sess_cfg.get("apiKey"), settings.llm_api_key)
    model = _resolve_model(body.get("model"), _first_non_empty(sess_cfg.get("model"), settings.llm_model))
    if provider == "lmstudio" and not model:
        model = "qwen/qwen3.5-9b"

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
            await session.orchestrator.run(story, driver=run_driver)

    asyncio.create_task(_run_flow())
    return {"ok": True, "started": True}


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
    provider = _first_non_empty(body.get("provider"), sess_cfg["provider"], settings.llm_provider).lower()
    if provider not in PROVIDER_REGISTRY:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Unsupported provider '{provider}'."})

    model = _resolve_model(body.get("model"), _first_non_empty(sess_cfg["model"], settings.llm_model))
    api_key = _first_non_empty(body.get("apiKey"), sess_cfg["apiKey"], settings.llm_api_key)

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
    target_provider = _first_non_empty(provider, sess_cfg["provider"], settings.llm_provider).lower()

    if target_provider not in PROVIDER_REGISTRY:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Unsupported provider '{target_provider}'."})

    api_key = _first_non_empty(request.headers.get("x-api-key"), sess_cfg["apiKey"], settings.llm_api_key)
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
    proj_id = settings.firebase_project_id
    return {
        "apiKey": settings.firebase_web_api_key,
        "authDomain": f"{proj_id}.firebaseapp.com",
        "projectId": proj_id,
        "storageBucket": f"{proj_id}.appspot.com",
        "messagingSenderId": "109599031175",
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
    body = await request.json()
    id_token = body.get("idToken")
    if not id_token:
        return JSONResponse(status_code=400, content={"ok": False, "error": "idToken is required"})

    email = str(body.get("email") or "user@gmail.com").strip()
    full_name = str(body.get("displayName") or "Google User").strip()
    uid = str(body.get("uid") or f"google_{int(time.time())}").strip()
    username = email.split("@")[0] if "@" in email else uid

    photo_url = str(body.get("photoURL") or body.get("photoUrl") or "").strip()

    user = {
        "id": uid,
        "username": username,
        "fullName": full_name,
        "email": email,
        "photoURL": photo_url,
    }
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
