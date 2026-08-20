"""
Policy Gap Analyzer — FastAPI Backend v3.0
Source-Grounded Compliance Intelligence System.

Features:
  - Gap analysis + "Fix All Gaps" rewrite from a single policy upload
  - Policy drafting from a plain-English description
  - Curated internal retrieval (RAG) from an eCFR-sourced compliance knowledge base
  - Controlled live research from curated regulatory sources
  - Post-generation verification against source material
  - Source attribution on every output (verified, retrieved, live research, model inference)
  - Privacy-first: No PHI or policy text is stored. All processing is ephemeral.
"""

import asyncio
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from app.config import settings
from app.error_utils import http_error
from app.services.orchestrator import GroundingUnavailableError
from app.routers import analysis, export, action_package, knowledge_base
from app.models.schemas import (
    DraftPolicyRequest,
    DraftedPolicy,
    DraftedPolicySection,
    ChatRequest,
    ChatResponse,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown events."""
    # Startup
    cascade = settings.llm_cascade_models
    logger.info(
        f"The Policy Lab API v3.0 starting — AI cascade: {len(cascade)} model(s)"
    )
    for i, model in enumerate(cascade):
        logger.info(f"  #{i + 1}: {model}")
    logger.info(f"CORS origins: {settings.cors_origin_list}")
    logger.info(f"Environment: {settings.environment}")
    logger.info(
        f"Knowledge base: {'enabled' if settings.kb_enabled else 'disabled'} at {settings.kb_persist_dir}"
    )

    # Auto-seed the knowledge base if enabled
    if settings.kb_auto_seed and settings.kb_enabled:
        try:
            from app.services.retrieval.seed_data import seed_knowledge_base
            from app.services.retrieval.store import get_store

            # Initialize the store first
            store = get_store()
            stats = store.get_all_stats()
            total_chunks = sum(stats.values())

            if store.has_unreadable_collections():
                logger.critical(
                    "One or more knowledge-base collections are UNREADABLE (not merely "
                    "empty). The store may be corrupted or the persist directory "
                    "inaccessible. Grounding cannot be trusted until this is resolved."
                )

            if total_chunks == 0:
                logger.info(
                    "Knowledge base is empty — seeding with foundational regulatory content..."
                )
                results = seed_knowledge_base()
                total = sum(results.values())
                if total == 0:
                    # Zero chunks after a seed attempt is a grounding outage, not
                    # a routine status line. Log it at a level that pages someone.
                    logger.critical(
                        "GROUNDING FAILURE: seeding completed but produced 0 chunks across "
                        f"{len(results)} sources. Source-verified output is NOT available; "
                        "analyses will fall back to model-only reasoning. Check eCFR "
                        "reachability and the parser, then POST /api/kb/seed to retry."
                    )
                else:
                    logger.info(
                        f"Knowledge base seeded: {total} chunks across {len(results)} sources"
                    )
            else:
                logger.info(
                    f"Knowledge base already contains {total_chunks} chunks — skipping seed"
                )
        except Exception as e:
            # Still non-fatal by choice -- refusing to boot would take the whole
            # service down over a third-party outage. But this is a grounding
            # outage, so it is logged as one rather than as a warning, and
            # /api/health reports kb_grounded=false for monitoring.
            logger.critical(
                f"GROUNDING FAILURE: knowledge base could not be seeded ({e}). "
                "The service will start, but source-verified output is NOT available "
                "until the knowledge base is populated."
            )

    # ── FIX: Warm up the embedding model at startup ──────────────────────────
    # sentence-transformers loads the model on first use, adding 10-30s latency
    # to the very first request. Warming up here means every request is fast.
    if settings.kb_enabled:
        try:
            logger.info("Warming up embedding model (sentence-transformers)...")
            loop = asyncio.get_running_loop()

            def _warmup():
                from app.services.retrieval.store import _get_embedding_function
                ef = _get_embedding_function()
                # Run a dummy embed to force model download + load
                ef(["warmup"])

            await loop.run_in_executor(None, _warmup)
            logger.info("Embedding model warmed up — first request will be fast")
        except Exception as e:
            logger.warning(f"Embedding warmup failed (non-fatal): {e}")
    # ─────────────────────────────────────────────────────────────────────────

    # ── Start the nightly regulatory refresh ──
    # start_scheduler() existed but was never called, so the "if startup seeding
    # fails, the nightly job will fix it" fallback described elsewhere in the
    # code was never actually running. An empty knowledge base therefore stayed
    # empty until the next deploy.
    scheduler_started = False
    if settings.kb_enabled:
        try:
            from app.services.retrieval.scheduler import start_scheduler
            start_scheduler()
            scheduler_started = True
        except Exception as e:
            logger.warning(f"Could not start regulatory refresh scheduler: {e}")

    yield

    # Shutdown
    if scheduler_started:
        try:
            from app.services.retrieval.scheduler import stop_scheduler
            stop_scheduler()
        except Exception as e:
            logger.warning(f"Scheduler shutdown failed: {e}")
    logger.info("Policy Gap Analyzer API shutting down")


# ── Create the app ──
app = FastAPI(
    title="Policy Gap Analyzer API",
    description="Source-Grounded Compliance Intelligence System for Hospitals, Home Health, and other industries. Upload a policy for gap analysis, generate a corrected rewrite from the findings (Fix All Gaps), or draft a new policy from scratch — all grounded in curated regulatory source material.",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)


# ── API Key Middleware ──
# Endpoints reachable without a key: health, docs, and the SPA shell itself
# (the frontend has to load before it can prompt for a password).
_PUBLIC_PATHS = frozenset(["/api/health", "/docs", "/redoc", "/", "/openapi.json"])

# Destructive / knowledge-base-mutating routes. These change what every future
# generation is grounded in, so they require a separate admin credential rather
# than the same shared password used to read the app.
_ADMIN_PATH_PREFIXES = ("/api/kb/ingest", "/api/kb/seed", "/api/kb/collections")


def _is_admin_request(request: Request) -> bool:
    path = request.url.path
    if request.method == "DELETE" and path.startswith("/api/kb/"):
        return True
    return request.method == "POST" and path.startswith(_ADMIN_PATH_PREFIXES)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Require the app password for API access, and a separate admin key for
    destructive knowledge-base operations.

    Fails CLOSED in production: if API_KEY is unset there, every request is
    refused rather than served openly. A missing env var on a redeploy used to
    silently turn the whole app public with no signal that it had happened --
    the safe direction for that failure is 'nobody gets in', not 'everybody
    does'.
    """
    if request.url.path in _PUBLIC_PATHS or request.method == "OPTIONS":
        return await call_next(request)

    if not settings.api_key:
        if settings.is_production:
            logger.critical(
                "API_KEY is not set while ENVIRONMENT=production — refusing all API "
                "requests. Set the API_KEY environment variable to restore service."
            )
            return JSONResponse(
                status_code=503,
                content={"detail": "Server is not configured for access. Contact the administrator."},
            )
        # Development only: no key configured, allow through.
        return await call_next(request)

    supplied = request.headers.get("x-api-key", "")
    # compare_digest avoids leaking the key's prefix through response timing.
    if not secrets.compare_digest(supplied, settings.api_key):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key. Set x-api-key header."},
        )

    if _is_admin_request(request):
        admin_key = settings.admin_api_key
        if not admin_key:
            logger.warning("Admin action blocked: ADMIN_API_KEY is not configured (%s)", request.url.path)
            return JSONResponse(
                status_code=403,
                content={"detail": "Administrative actions are not enabled on this deployment."},
            )
        supplied_admin = request.headers.get("x-admin-key", "")
        if not secrets.compare_digest(supplied_admin, admin_key):
            return JSONResponse(
                status_code=403,
                content={"detail": "This action requires an administrator key."},
            )

    return await call_next(request)


# ── Rate Limiting ──
# In-memory per-IP fixed-window limiter for the expensive LLM-backed POST
# endpoints. No auth is required to use this app (see api_key_middleware
# above — api_key is unset by default), so without this, anyone who finds
# the URL could script repeated calls against paid Anthropic/OpenAI/Gemini
# keys. Same in-memory-per-instance tradeoff as job_store.py: fine for a
# single Render instance, would need a shared store (e.g. Redis) if this
# ever scales to multiple instances.
_RATE_LIMIT_WINDOW = 60.0  # seconds
_RATE_LIMIT_MAX = 20       # requests per window per IP
_RATE_LIMITED_PREFIXES = (
    "/api/action-package",
    "/api/draft-policy",
    "/api/chat",
    # KB writes are cheap to call but mutate what every future generation is
    # grounded in, so they're limited for data integrity rather than cost.
    "/api/kb",
)
_rate_limit_buckets: dict[str, deque] = defaultdict(deque)
_rate_limit_lock = asyncio.Lock()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.method == "POST" and any(request.url.path.startswith(p) for p in _RATE_LIMITED_PREFIXES):
        ip = _client_ip(request)
        now = time.monotonic()
        async with _rate_limit_lock:
            bucket = _rate_limit_buckets[ip]
            while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW:
                bucket.popleft()
            if len(bucket) >= _RATE_LIMIT_MAX:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please wait a moment and try again."},
                    headers={"Retry-After": str(int(_RATE_LIMIT_WINDOW))},
                )
            bucket.append(now)

    return await call_next(request)


# ── Security Headers ──
# TLS is terminated by the platform, but the app still has to tell browsers to
# enforce it and to refuse being framed. The CSP matches how the app actually
# loads: same-origin bundles, Google Fonts, and API/LLM calls to our own origin.
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    if settings.is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ──
app.include_router(analysis.router)
app.include_router(export.router)
app.include_router(action_package.router)
app.include_router(knowledge_base.router)


@app.get("/api/industries")
async def list_industries():
    """Return available industry verticals for the frontend selector."""
    from app.services.industry_config import get_industry_choices

    return {"industries": get_industry_choices()}


@app.post("/api/draft-policy")
async def draft_policy_endpoint(request: DraftPolicyRequest):
    """Draft a complete policy document from a plain-English description."""
    from app.services.draft_policy_service import draft_policy

    try:
        data = await draft_policy(
            policy_description=request.policy_description,
            industry=request.industry,
            jurisdiction=request.jurisdiction,
        )
        sections = [
            DraftedPolicySection(title=s.get("title", ""), content=s.get("content", ""))
            for s in data.get("sections", [])
        ]
        return DraftedPolicy(
            policy_title=data.get("policy_title", "Drafted Policy"),
            effective_date=data.get("effective_date"),
            version=data.get("version", "1.0"),
            scope=data.get("scope"),
            regulations_applied=data.get("regulations_applied", []),
            sections=sections,
            full_text=data.get("full_text", ""),
            drafting_notes=data.get("drafting_notes"),
            kb_sources_used=data.get("kb_sources_used"),
            kb_source_urls=data.get("kb_source_urls"),
            source_snippets=data.get("source_snippets"),
            live_research_used=data.get("live_research_used", False),
            verification_overall=data.get("verification_overall"),
            unverified_claim_count=data.get("unverified_claim_count"),
        )
    except GroundingUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except Exception as e:
        raise http_error(e, context="Policy drafting",
                         user_message="Policy drafting failed. Please try again.")


def _build_policy_dict(data: dict) -> dict:
    sections = [
        {"title": s.get("title", ""), "content": s.get("content", "")}
        for s in data.get("sections", [])
    ]
    return {
        "policy_title": data.get("policy_title", "Drafted Policy"),
        "effective_date": data.get("effective_date"),
        "version": data.get("version", "1.0"),
        "scope": data.get("scope"),
        "regulations_applied": data.get("regulations_applied", []),
        "sections": sections,
        "full_text": data.get("full_text", ""),
        "drafting_notes": data.get("drafting_notes"),
        "kb_sources_used": data.get("kb_sources_used"),
        "kb_source_urls": data.get("kb_source_urls"),
        "source_snippets": data.get("source_snippets"),
        "live_research_used": data.get("live_research_used", False),
        "verification_overall": data.get("verification_overall"),
        "unverified_claim_count": data.get("unverified_claim_count"),
    }


@app.post("/api/draft-policy-stream")
async def draft_policy_stream_endpoint(request: DraftPolicyRequest):
    """SSE version of /api/draft-policy — streams text as it's generated so the
    UI can show live progress instead of a blank spinner for the full ~1-2 min.

    NOTE: tied directly to this HTTP request/response — if the client
    disconnects (tab closed, navigation, mobile backgrounding the request),
    generation stops with it. Use /api/draft-policy/start for a version that
    survives the client going away."""
    import json as _json
    from app.services.draft_policy_service import draft_policy_stream, parse_draft_response, attach_attribution

    async def event_stream():
        raw_text = ""
        context_holder: dict = {}
        try:
            async for chunk in draft_policy_stream(
                policy_description=request.policy_description,
                industry=request.industry,
                jurisdiction=request.jurisdiction,
                context_holder=context_holder,
            ):
                raw_text += chunk
                yield f"data: {_json.dumps({'delta': chunk})}\n\n"

            data = parse_draft_response(raw_text)
            if context_holder.get("ctx") is not None:
                data = attach_attribution(data, context_holder["ctx"])
            yield f"data: {_json.dumps({'done': True, 'policy': _build_policy_dict(data)})}\n\n"
        except Exception as e:
            logger.error(f"Draft stream error: {e}")
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Draft background-job endpoints
# ---------------------------------------------------------------------------
# Same pattern as the action-package job endpoints below: the draft runs as a
# server-side background task keyed by job_id, so it survives the client
# tabbing away, backgrounding the app, or losing the connection. The client
# reconnects with the job_id to pick up the live partial text or the finished
# result, whichever is ready.

_draft_running_tasks: dict[str, asyncio.Task] = {}


async def _run_draft_job(job_id: str, request: DraftPolicyRequest) -> None:
    from app.services.draft_policy_service import draft_policy_stream, parse_draft_response, attach_attribution
    from app.services.draft_job_store import get_draft_job_store

    store = get_draft_job_store()
    raw_text = ""
    context_holder: dict = {}
    try:
        async for chunk in draft_policy_stream(
            policy_description=request.policy_description,
            industry=request.industry,
            jurisdiction=request.jurisdiction,
            context_holder=context_holder,
        ):
            raw_text += chunk
            await store.append_text(job_id, chunk)

        data = parse_draft_response(raw_text)
        if context_holder.get("ctx") is not None:
            data = attach_attribution(data, context_holder["ctx"])
        await store.mark_complete(job_id, _build_policy_dict(data))
    except Exception as e:
        logger.exception(f"Background draft job {job_id} failed")
        await store.mark_error(job_id, str(e))


@app.post("/api/draft-policy/start")
async def start_draft_job(request: DraftPolicyRequest):
    """Kick off a draft as a background task. Returns a job_id immediately.

    Use GET /api/draft-policy/stream/{job_id} to subscribe to live updates,
    or GET /api/draft-policy/status/{job_id} for a one-shot snapshot."""
    from app.services.draft_job_store import get_draft_job_store

    store = get_draft_job_store()
    job_id = await store.create()
    task = asyncio.create_task(_run_draft_job(job_id, request))
    _draft_running_tasks[job_id] = task
    task.add_done_callback(lambda t, jid=job_id: _draft_running_tasks.pop(jid, None))
    return {"job_id": job_id}


@app.post("/api/draft-policy/cancel/{job_id}")
async def cancel_draft_job(job_id: str):
    """Cancel an in-flight draft job. Safe to call even if it already finished (no-op)."""
    from app.services.draft_job_store import get_draft_job_store

    task = _draft_running_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    store = get_draft_job_store()
    job = await store.get(job_id)
    if job is not None and job.status == "running":
        await store.mark_error(job_id, "Cancelled by user")
    return {"cancelled": True}


@app.get("/api/draft-policy/status/{job_id}")
async def get_draft_job_status(job_id: str):
    """Return the current snapshot of a draft job. 404 if not found or expired."""
    from app.services.draft_job_store import get_draft_job_store

    store = get_draft_job_store()
    job = await store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "partial_text": job.partial_text,
        "policy": job.policy,
        "error": job.error,
        "version": job.version,
    }


@app.get("/api/draft-policy/stream/{job_id}")
async def stream_draft_job(job_id: str):
    """SSE stream of draft job updates. Sends a frame whenever the job version
    changes, then closes once the job is complete or errored."""
    import json as _json
    from app.services.draft_job_store import get_draft_job_store

    store = get_draft_job_store()
    initial = await store.get(job_id)
    if initial is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    async def event_stream():
        last_version = -1
        max_iterations = 15 * 60 * 2  # 0.5s per iteration, 15 min watchdog
        iterations = 0
        while iterations < max_iterations:
            iterations += 1
            current = await store.get(job_id)
            if current is None:
                yield f"data: {_json.dumps({'status': 'error', 'error': 'Job expired'})}\n\n"
                return
            if current.version != last_version:
                last_version = current.version
                payload = {
                    "status": current.status,
                    "partial_text": current.partial_text,
                    "policy": current.policy,
                    "error": current.error,
                    "version": current.version,
                }
                yield f"data: {_json.dumps(payload)}\n\n"
            if current.status in ("complete", "error"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/chat", response_model=ChatResponse)
async def compliance_chat(request: ChatRequest):
    """
    Chat with the compliance AI assistant.
    Works in both analysis context (post-gap-analysis) and draft context (post-policy-draft).
    Free via Gemini API — stateless, no conversation stored server-side.
    """
    from app.services.chat_service import chat

    try:
        response_text = await chat(
            message=request.message,
            mode=request.mode,
            industry=request.industry,
            jurisdiction=request.jurisdiction,
            context_summary=request.context_summary,
            conversation_history=request.conversation_history,
        )
        return ChatResponse(response=response_text)
    except Exception as e:
        raise http_error(e, context="Chat",
                         user_message="The assistant couldn't answer that right now. Please try again.")


# ── Serve React frontend static files in production ──
# In development the Vite dev server serves the frontend; mounting these
# routes would shadow the live source and cause stale-bundle bugs.
_STATIC_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))

if settings.is_production and os.path.isdir(_STATIC_DIR):
    _assets_dir = os.path.join(_STATIC_DIR, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str = ""):
        index = os.path.join(_STATIC_DIR, "index.html")
        return FileResponse(index)
else:
    @app.get("/")
    async def root():
        return {
            "name": "Policy Gap Analyzer API",
            "version": "3.0.0",
            "status": "running",
            "docs": "/docs",
            "features": {
                "retrieval": settings.kb_enabled,
                "live_research": settings.live_research_enabled,
                "verification": True,
                "source_attribution": True,
            },
            "endpoints": {
                "analyze": "/api/analyze",
                "analyze_file": "/api/analyze-file",
                "action_package": "/api/action-package",
                "action_package_file": "/api/action-package-file",
                "export": "/api/export",
                "export_package": "/api/export-package",
                "health": "/api/health",
                "kb_stats": "/api/kb/stats",
                "kb_ingest": "/api/kb/ingest",
                "kb_seed": "/api/kb/seed",
                "kb_collections": "/api/kb/collections",
            },
        }
