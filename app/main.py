import asyncio
import json
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
from app.providers.jev import configured_jev_model
from app.http.execution_flags import execution_flag
from app.http.request_json import read_json_object
from app.simulation.round_barrier import normalize_round_number
from app.auth import KeyVault, SessionGuard, requires_key
from app.auth.firebase import IdentityError, identity_request, verified_user
from app.auth.session import SESSION_COOKIE, SESSION_SECONDS, is_authenticated_webhook_relay
from app.database import (
    read_all_transactions,
    read_all_customers,
    verify_customer_billing,
    analyze_customer_transactions,
    execute_agent_resolution,
    execute_manual_resolution,
    find_customer_by_id_or_query,
)

START_TIME = time.time()

def _invalid_request(message: str = "Request body must be a JSON object") -> JSONResponse:
    return JSONResponse(status_code=400, content={"ok": False, "error": message})


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
guard = SessionGuard()


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
    if is_authenticated_webhook_relay(request):
        request.state.session_user = None
    elif guard.is_protected_path(path):
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


def _provider_api_key(provider: str, stored: Optional[Dict[str, Any]], explicit: object = None) -> str:
    stored_key = stored.get("apiKey") if stored and stored.get("provider") == provider else None
    host_key = settings.llm_api_key if settings.llm_provider == provider else None
    return _first_non_empty(explicit, stored_key, host_key)


async def resolve_session_config(request: Request) -> Dict[str, Any]:
    user = getattr(request.state, "session_user", None)
    stored = await key_vault.get(user)
    # Stored values may be empty/whitespace/generic (e.g. persisted by an
    # older client); fall back to server settings in all of those cases.
    provider = _first_non_empty(stored.get("provider") if stored else None, settings.llm_provider).lower()
    model = _resolve_model(stored.get("model") if stored else None, settings.llm_model)
    api_key = _provider_api_key(provider, stored)
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
        "authConfigured": bool(settings.firebase_project_id and settings.firebase_web_api_key),
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
    initial = {"message": "Waiting for resolution signal.", "provider": p_prov,
               "model": p_mod, "connection": "connected"}
    q.put_nowait(f"event: status\ndata: {json.dumps(initial)}\n\n")

    snapshot = session.state.resolution_snapshot()
    latest = snapshot["story"]
    if latest:
        topic = {"title": latest.get("title", "")}
        q.put_nowait(f"event: topic\ndata: {json.dumps(topic)}\n\n")
        q.put_nowait(f"event: feed\ndata: {json.dumps(latest)}\n\n")
    snapshot["running"] = session.is_busy()
    q.put_nowait(f"event: session_snapshot\ndata: {json.dumps(snapshot)}\n\n")

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Wait up to 15 seconds for a frame, else yield ping keepalive
                    frame = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield frame
                    if frame.startswith("event: server_shutdown\n"):
                        break
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            session.sse_manager.remove_client(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
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
    """Return local ledger findings or a labeled example for the requested category."""
    from app.database import get_category_complaint_context, analyze_customer_transactions
    from app.triage import async_predict_category

    cat = (category or "").strip()
    if not cat and query:
        try:
            pred = await async_predict_category(query)
            cat = pred.get("predicted_category", str(pred)) if hasattr(pred, "get") else str(pred)
        except Exception:
            pass

    # Troy's decision: does this ticket require a database mutation /
    # human routing, or is it a direct response? The UI renders
    # action/routing buttons only when action_required is True.
    intent_category = None if query and configured_jev_model() else cat or None
    intent_info = Troy.evaluate_complaint_intent(query or "", category=intent_category)
    action_required = bool(intent_info.get("requires_db_mutation"))

    ctx = get_category_complaint_context(category=cat, query=query or "")
    if not ctx.get("is_refund", False):
        return {
            "ok": True,
            "verified": False,
            "execution_mode": "simulation",
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
                "refund_id": None,
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
async def get_database_tickets(request: Request):
    tickets = get_active_session(request).state.ticket_history()
    return {"ok": True, "tickets": tickets, "count": len(tickets), "source": "session"}


@app.post("/api/resolve/action")
async def post_resolve_action(request: Request):
    body = await read_json_object(request)
    if body is None:
        return _invalid_request()

    action = body.get("action", "agent_resolve")
    if action not in ("agent_resolve", "manual_resolve"):
        message = "Unknown resolution action"
        return JSONResponse(status_code=400, content={"ok": False, "error": message, "detail": message})

    fields = ("customer_id", "ticket_id", "query", "category", "action_details", "notes")
    for field in fields:
        value = body.get(field)
        if field == "ticket_id" and type(value) is int and value > 0:
            body[field] = str(value)
        elif value is not None and not isinstance(value, str):
            return _invalid_request(f"{field} must be text.")

    customer_id = _first_non_empty(body.get("customer_id"), body.get("ticket_id")) or None
    query = (body.get("query") or "").strip() or None
    category = (body.get("category") or "").strip() or None
    if not (customer_id or query or category):
        return _invalid_request("A customer ID, ticket ID, query or category is required.")
    custom_action = body.get("action_details") or ""
    notes = body.get("notes")

    if not category and query:
        from app.triage import async_predict_category
        try:
            pred = await async_predict_category(query)
            category = str(pred)
            if (category == "Refunds" and configured_jev_model()
                    and not Troy.evaluate_complaint_intent(query)["is_billing_dispute"]):
                return _invalid_request("The complaint does not identify a billing dispute for a refund action.")
        except Exception:
            pass

    if action == "manual_resolve":
        res = execute_manual_resolution(customer_id=customer_id, query=query, notes=notes, category=category)
    else:
        res = execute_agent_resolution(customer_id=customer_id, query=query, action=custom_action, notes=notes, category=category)

    # Broadcast event so UI or stream clients know database was updated
    session = get_active_session(request)
    if res.get("ok"):
        session.sse_manager.broadcast("database_updated", res)

    return res


@app.get("/api/trigger")
async def manual_trigger(request: Request, title: str = "Manual resolution trigger"):
    session = get_active_session(request)
    sess_cfg = await resolve_session_config(request)
    if session.is_busy():
        return JSONResponse(status_code=409, content={"ok": False, "running": True, "error": "A resolution is already in progress."})

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

    session.task = asyncio.create_task(session.orchestrator.run(story, driver=sess_cfg["driver"]))
    return {"ok": True, "story": story}


@app.post("/api/round/begin")
async def begin_round(request: Request):
    body = await read_json_object(request, exact_numbers=True)
    if body is None:
        return _invalid_request()
    round_num = normalize_round_number(body.get("round"))
    if round_num is None:
        return _invalid_request("Invalid round number")
    session = get_active_session(request)
    result = session.orchestrator.mark_round_begun(round_num)
    if not result.get("ok"):
        return _invalid_request("Invalid round number")
    return {"ok": True, "round": result.get("round")}


@app.post("/api/next")
@app.post("/api/ticket/next")
async def next_ticket(request: Request, query: str = ""):
    body = {}
    if await request.body():
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(status_code=400, content={"ok": False, "error": "Request body must be a JSON object"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "error": "Request body must be a JSON object"})

    ticket_query = body.get("query")
    if ticket_query is None:
        ticket_query = query
    if not isinstance(ticket_query, str):
        return _invalid_request("Complaint ticket details must be text.")
    ticket_query = ticket_query.strip()

    if not ticket_query:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Complaint ticket details have not been entered."})

    session = get_active_session(request)
    sess_cfg = await resolve_session_config(request)

    # Blank browser values preserve the selected provider's key and configured model.
    provider = _first_non_empty(body.get("provider"), sess_cfg.get("provider"), settings.llm_provider).lower()
    api_key = _provider_api_key(provider, sess_cfg.get("vaultRecord"), body.get("apiKey"))
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

    if session.is_busy():
        return JSONResponse(status_code=409, content={"ok": False, "running": True, "error": "A resolution is already in progress."})

    async def _run_flow():
        session.sse_manager.broadcast("ticket_summarizing", {"query": ticket_query})
        session.sse_manager.broadcast("policy_summarizing", {"query": ticket_query})
        story = await session.orchestrator.summarise_ticket(ticket_query, driver=run_driver)
        if story:
            await session.orchestrator.run(story, driver=run_driver)

    session.state.set_story(None)
    session.task = asyncio.create_task(_run_flow())
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
        "running": session.is_busy(),
        "showReasoning": session.orchestrator.show_reasoning,
    }


@app.post("/api/config")
async def post_config(request: Request):
    session = get_active_session(request)
    if session.is_busy():
        return JSONResponse(status_code=409, content={"ok": False, "running": True, "error": "A resolution is already in progress."})

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Request body must be a JSON object"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Request body must be a JSON object"})

    show_reasoning = execution_flag(body, "showReasoning", "show_reasoning", None)

    sess_cfg = await resolve_session_config(request)
    provider = _first_non_empty(body.get("provider"), sess_cfg["provider"], settings.llm_provider).lower()
    if provider not in PROVIDER_REGISTRY:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Unsupported provider '{provider}'."})

    model = _resolve_model(body.get("model"), _first_non_empty(sess_cfg["model"], settings.llm_model))
    api_key = _provider_api_key(provider, sess_cfg.get("vaultRecord"), body.get("apiKey"))

    user = getattr(request.state, "session_user", None)
    saved = await key_vault.put(user, {"provider": provider, "model": model, "apiKey": api_key})
    if show_reasoning is not None:
        session.orchestrator.show_reasoning_override = show_reasoning

    session.sse_manager.broadcast("status", {"model": model, "provider": provider})

    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "baseUrl": provider_base_url(provider) or "",
        "hasApiKey": bool(api_key),
        "persisted": saved.get("persisted", False),
        "showReasoning": session.orchestrator.show_reasoning,
    }


@app.get("/api/models")
async def get_models(request: Request, provider: Optional[str] = None):
    sess_cfg = await resolve_session_config(request)
    target_provider = _first_non_empty(provider, sess_cfg["provider"], settings.llm_provider).lower()

    if target_provider not in PROVIDER_REGISTRY:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Unsupported provider '{target_provider}'."})

    api_key = _provider_api_key(target_provider, sess_cfg.get("vaultRecord"), request.headers.get("x-api-key"))
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
        "authMode": "account" if guard.required else "demo",
        "authDomain": f"{proj_id}.firebaseapp.com" if proj_id else "",
        "projectId": proj_id,
        "storageBucket": f"{proj_id}.appspot.com",
        "messagingSenderId": "109599031175",
    }


def _identity_error(exc: IdentityError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"ok": False, "error": str(exc)})


def _signed_in(user: dict) -> JSONResponse:
    response = JSONResponse({"ok": True, "user": user, "mode": "account"})
    response.set_cookie(
        SESSION_COOKIE, guard.create_session(user), max_age=SESSION_SECONDS, path="/",
        httponly=True, secure=guard.required or settings.public_url.startswith("https://"), samesite="lax",
    )
    return response


@app.post("/api/auth/signup")
async def signup(request: Request):
    body = await read_json_object(request)
    if body is None:
        return _invalid_request()
    full_name, email, password, confirmation = (
        body.get(field) for field in ("fullName", "email", "password", "confirmPassword")
    )
    if (
        not all(isinstance(value, str) for value in (full_name, email, password, confirmation))
        or not full_name.strip() or not email.strip() or len(password) < 6 or password != confirmation
    ):
        return _invalid_request("Provide a name, email and matching passwords of at least six characters.")
    full_name, email = full_name.strip(), email.strip()
    if not guard.required:
        return JSONResponse(status_code=403, content={
            "ok": False, "error": "Demo mode does not create accounts. Continue as a guest.",
        })
    try:
        account = await identity_request("signUp", {
            "email": email, "password": password, "returnSecureToken": True,
        })
        token = account.get("idToken")
        if not token:
            raise IdentityError(503, "The identity provider did not return a signup token.")
        await identity_request("update", {"idToken": token, "displayName": full_name})
        await identity_request("sendOobCode", {"idToken": token, "requestType": "VERIFY_EMAIL"})
    except IdentityError as exc:
        return _identity_error(exc)
    return JSONResponse(status_code=201, content={
        "ok": True, "message": "Account created. Verify your email before signing in.",
    })


@app.post("/api/auth/login")
async def login(request: Request):
    body = await read_json_object(request, allow_empty=not guard.required)
    if body is None:
        return _invalid_request()
    if not guard.required:
        from app.auth.session import extract_id_token
        token = extract_id_token(request)
        if token:
            guard.revoke_session(token)
        response = JSONResponse({"ok": True, "mode": "guest", "message": "Continuing as a guest."})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response
    email, username = body.get("email"), body.get("username")
    password = body.get("password")
    if any(value is not None and not isinstance(value, str) for value in (email, username)):
        return _invalid_request("Email and password are required.")
    email = (email or username or "").strip()
    if "@" not in email or not isinstance(password, str) or not password:
        return _invalid_request("Email and password are required.")
    try:
        identity = await identity_request("signInWithPassword", {
            "email": email, "password": password, "returnSecureToken": True,
        })
        token = identity.get("idToken")
        if not token:
            raise IdentityError(401, "The identity provider did not verify the credentials.")
        return _signed_in(await verified_user(token))
    except IdentityError as exc:
        return _identity_error(exc)


@app.post("/api/auth/google")
async def google_auth(request: Request):
    body = await read_json_object(request)
    if body is None:
        return _invalid_request()
    token = body.get("idToken")
    if not isinstance(token, str) or not token.strip():
        return _invalid_request("idToken is required.")
    try:
        return _signed_in(await verified_user(token, google=True))
    except IdentityError as exc:
        return _identity_error(exc)


@app.get("/api/auth/me")
async def get_me(request: Request):
    user = getattr(request.state, "session_user", None)
    if not user:
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

def _complaint_text(body: dict) -> Optional[str]:
    for field in ("complaint", "query", "text"):
        value = body.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return None
        if value.strip():
            return value.strip()
    return None


@app.post("/api/cra/resolve")
@app.post("/api/cra/triage")
async def api_cra_resolve(request: Request):
    """
    Executes the full CRA machine learning intelligence pipeline for an incoming complaint.
    Return category scores, extracted entities, static category examples,
    a recommended team and an SLA estimate.
    """
    body = await read_json_object(request)
    if body is None:
        return _invalid_request()
    complaint = _complaint_text(body)
    if complaint is None:
        return _invalid_request("Complaint text is required.")

    from app.triage import async_triage_complaint
    report = await async_triage_complaint(complaint)
    return {"ok": True, "report": report}


@app.post("/api/cra/classify")
@app.post("/api/cra/predict")
async def api_cra_classify(request: Request):
    """
    Return a category and scores from the configured classifier.
    """
    body = await read_json_object(request)
    if body is None:
        return _invalid_request()
    complaint = _complaint_text(body)
    if complaint is None:
        return _invalid_request("Complaint text is required.")

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
        if guard.required:
            auth_res = await guard.authenticate(request)
            if not auth_res.get("ok") or not auth_res.get("user"):
                return RedirectResponse(url="/login.html", status_code=302)
        return FileResponse(os.path.join(PUBLIC_DIR, "index.html"))

    @app.get("/login.html")
    async def serve_login(request: Request):
        if guard.required:
            auth_res = await guard.authenticate(request)
            if auth_res.get("ok") and auth_res.get("user"):
                return RedirectResponse(url="/", status_code=302)
        return FileResponse(os.path.join(PUBLIC_DIR, "login.html"))

    @app.get("/signup.html")
    async def serve_signup(request: Request):
        if guard.required:
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
        response.delete_cookie(key=SESSION_COOKIE, path="/")
        return response

    app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="public")
