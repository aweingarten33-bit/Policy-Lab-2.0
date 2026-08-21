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
  - Privacy-first: uploaded policy text is not persisted to disk or a database;
    temporary background-job output lives only in process memory and expires.
"""

import asyncio
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from app.config import settings
from app.error_utils import http_error
from app.request_identity import client_id
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
    # Keep strong references to fire-and-forget startup tasks. asyncio keeps
    # only weak references to tasks; an unreferenced task can otherwise vanish
    # before it finishes. The set also gives shutdown one place to cancel them.
    background_tasks: set[asyncio.Task] = set()

    def _track_background_task(task: asyncio.Task) -> asyncio.Task:
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return task

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

    # ── Knowledge base bootstrap (background) ──
    # Seeding used to run inline here, before `yield`, so the app accepted no
    # traffic until every CFR part had been downloaded and embedded. On a cold
    # container that can take minutes -- long enough for the platform health
    # check to fail and restart the container, which starts the whole thing
    # over. The knowledge base could therefore never finish seeding, which is
    # exactly the "always 0 chunks" symptom seen in production.
    #
    # It now runs as a background task: the app is ready immediately and the
    # knowledge base fills in behind it. Progress is recorded in seed_state and
    # reported by /api/kb/diagnose, since a background job is otherwise silent.
    async def _bootstrap_knowledge_base():
        from app.services.retrieval.seed_data import seed_knowledge_base_async
        from app.services.retrieval.store import get_store
        from app.services.retrieval import seed_state

        try:
            # If the corpus was baked into the image but KB_PERSIST_DIR points
            # at a mounted disk, copy it across before deciding to download.
            try:
                from app.services.retrieval.seed_data import restore_baked_knowledge_base
                restore_baked_knowledge_base()
            except Exception as e:
                logger.warning(f"Prebuilt knowledge base restore skipped: {e}")

            store = get_store()
            stats = store.get_all_stats()

            if store.has_unreadable_collections():
                logger.critical(
                    "One or more knowledge-base collections are UNREADABLE (not merely "
                    "empty). The store may be corrupted or the persist directory "
                    "inaccessible. Grounding cannot be trusted until this is resolved."
                )

            existing = sum(v for v in stats.values() if v > 0)
            if existing > 0:
                logger.info(f"Knowledge base already contains {existing} chunks — skipping seed")
                return

            if not settings.kb_seed_at_runtime:
                seed_state.mark_failed(
                    "Knowledge base is empty and runtime seeding is disabled. The corpus is "
                    "built into the image; this container shipped without one. Rebuild the "
                    "image, or set KB_SEED_AT_RUNTIME=true on an instance with enough memory."
                )
                logger.critical(
                    "GROUNDING FAILURE: knowledge base is empty and runtime seeding is "
                    "disabled to protect service availability. The app is UP but not "
                    "source-grounded. See /api/kb/diagnose."
                )
                return

            logger.info("Knowledge base is empty — seeding from eCFR in the background...")
            seed_state.mark_started()
            results = await seed_knowledge_base_async()
            seed_state.mark_finished(results)

            total = sum(results.values())
            if total == 0:
                logger.critical(
                    "GROUNDING FAILURE: seeding completed but produced 0 chunks across "
                    f"{len(results)} sources. Source-verified output is NOT available. "
                    "See /api/kb/diagnose."
                )
            else:
                logger.info(f"Knowledge base seeded: {total} chunks across {len(results)} sources")
        except Exception as e:
            from app.services.retrieval import seed_state as _st
            _st.mark_failed(f"{type(e).__name__}: {e}")
            logger.critical(
                f"GROUNDING FAILURE: knowledge base could not be seeded ({e}). "
                "The service is running, but source-verified output is NOT available."
            )

    if settings.kb_auto_seed and settings.kb_enabled:
        _track_background_task(asyncio.create_task(_bootstrap_knowledge_base()))

    # ── Warm up the embedding model at startup ───────────────────────────────
    if settings.kb_enabled:
        logger.info("Warming up embedding model (sentence-transformers)...")
        loop = asyncio.get_running_loop()

        def _warmup():
            from app.services.retrieval.store import _get_embedding_function
            ef = _get_embedding_function()
            ef(["warmup"])

        async def _warm():
            try:
                await loop.run_in_executor(None, _warmup)
                logger.info("Embedding model warmed up — first request will be fast")
            except Exception as e:
                logger.warning(f"Embedding warmup failed (non-fatal): {e}")

        _track_background_task(asyncio.create_task(_warm()))

    # ── Start the nightly regulatory refresh ──
    scheduler_started = False
    if settings.kb_enabled:
        try:
            from app.services.retrieval.scheduler import start_scheduler
            start_scheduler()
            scheduler_started = True
        except Exception as e:
            logger.warning(f"Could not start regulatory refresh scheduler: {e}")

    yield

    # Shutdown tracked startup work cleanly rather than leaving tasks dangling.
    pending_tasks = tuple(background_tasks)
    for task in pending_tasks:
        task.cancel()
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)

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
    openapi_url="/openapi.json" if not settings.is_production else None,
)


# ── API Key Middleware ──
# Health and the frontend shell remain reachable without a key. Diagnostics are
# API functionality and can trigger outbound/provider checks, so they require
# the same app credential as the rest of /api rather than being anonymously
# callable from the internet.
_PUBLIC_PATHS = frozenset([
    "/api/health", "/docs", "/redoc", "/",
])

# Destructive / knowledge-base-mutating routes. These change what every future
# generation is grounded in, so they require a separate admin credential rather
# than the same shared password used to read the app.
_ADMIN_PATH_PREFIXES = ("/api/kb/ingest", "/api/kb/seed", "/api/kb/collections")


def _is_admin_request(request: Request) -> bool:
    path = request.url.path
    if request.method == "DELETE" and path.startswith("/api/kb/"):
        return True
    return request.method == "POST" and path.startswith(_ADMIN_PATH_PREFIXES)


def _is_api_request(request: Request) -> bool:
    """Whether this request targets the API rather than the frontend bundle.

    The password guards the API. It cannot guard static files: a browser
    loading the page issues its own requests for <script src>, stylesheets and
    the favicon, and there is no way to attach a header to those. Allowlisting
    only "/" meant the shell returned 200 while every asset it referenced
    returned 401 -- so the served page was blank and the site looked completely
    down. Anything outside /api/ is frontend content and must be reachable;
    the PasswordGate in that bundle is what then gates access to the API.
    """
    return request.url.path.startswith("/api/")


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Require the app password for API access, and a separate admin key for
    destructive knowledge-base operations.

    Fails CLOSED in production: if API_KEY is unset there, every API request is
    refused rather than served openly.
    """
    if (
        not _is_api_request(request)
        or request.url.path in _PUBLIC_PATHS
        or request.method == "OPTIONS"
    ):
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
        return await call_next(request)

    supplied = request.headers.get("x-api-key", "")
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
# In-memory per-IP fixed-window limiter for expensive or mutating endpoints.
# The API is authenticated in production, but the limiter still caps accidental
# or scripted spend by an authenticated client. This is per-process and should
# move to a shared store such as Redis before horizontal scaling.
_RATE_LIMIT_WINDOW = 60.0  # seconds
_RATE_LIMIT_MAX = 20       # requests per window per IP
_RATE_LIMITED_PREFIXES = (
    "/api/action-package",
    "/api/draft-policy",
    "/api/chat",
    "/api/kb",
)
_rate_limit_buckets: dict[str, deque] = defaultdict(deque)
_rate_limit_lock = asyncio.Lock()


def _client_ip(request: Request) -> str:
    """Best available client address for rate limiting.

    X-Forwarded-For is honoured only when the deployment says it sits behind a
    trusted proxy that rewrites that header.
    """
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    _limited = (
        request.method == "POST"
        and any(request.url.path.startswith(p) for p in _RATE_LIMITED_PREFIXES)
    ) or request.url.path == "/api/kb/diagnose"
    if _limited:
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
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "worker-src 'self' blob:; "
        "child-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' blob:; "
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


def _build_policy_dict(data: dict, industry: Optional[str] = None) -> dict:
    sections = [
        {"title": s.get("title", ""), "content": s.get("content", "")}
        for s in data.get("sections", [])
    ]
    return {
        "policy_title": data.get("policy_title", "Drafted Policy"),
        "industry": industry,
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
    """SSE version of /api/draft-policy tied to the current HTTP connection."""
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
            yield f"data: {_json.dumps({'done': True, 'policy': _build_policy_dict(data, request.industry)})}\n\n"
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
        await store.mark_complete(job_id, _build_policy_dict(data, request.industry))
    except Exception as e:
        logger.exception(f"Background draft job {job_id} failed")
        await store.mark_error(job_id, str(e))


@app.post("/api/draft-policy/start")
async def start_draft_job(request: DraftPolicyRequest, http_request: Request):
    """Kick off a draft as a background task. Returns a job_id immediately."""
    from app.services.draft_job_store import get_draft_job_store

    store = get_draft_job_store()
    job_id = await store.create(owner=client_id(http_request))
    task = asyncio.create_task(_run_draft_job(job_id, request))
    _draft_running_tasks[job_id] = task
    task.add_done_callback(lambda t, jid=job_id: _draft_running_tasks.pop(jid, None))
    return {"job_id": job_id}


@app.post("/api/draft-policy/cancel/{job_id}")
async def cancel_draft_job(job_id: str, http_request: Request):
    """Cancel a draft only after proving the caller owns that job."""
    from app.services.draft_job_store import get_draft_job_store

    store = get_draft_job_store()
    owner = client_id(http_request)
    job = await store.get(job_id, owner=owner)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    task = _draft_running_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    if job.status == "running":
        await store.mark_error(job_id, "Cancelled by user")
    return {"cancelled": True}


@app.get("/api/draft-policy/status/{job_id}")
async def get_draft_job_status(job_id: str, http_request: Request):
    """Return the current snapshot of a draft job. 404 if not found or expired."""
    from app.services.draft_job_store import get_draft_job_store

    store = get_draft_job_store()
    job = await store.get(job_id, owner=client_id(http_request))
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
async def stream_draft_job(job_id: str, http_request: Request):
    """SSE stream of draft job updates for the owning client."""
    import json as _json
    from app.services.draft_job_store import get_draft_job_store

    store = get_draft_job_store()
    owner = client_id(http_request)
    initial = await store.get(job_id, owner=owner)
    if initial is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    async def event_stream():
        last_version = -1
        max_iterations = 15 * 60 * 2  # 0.5s per iteration, 15 min watchdog
        iterations = 0
        while iterations < max_iterations:
            iterations += 1
            current = await store.get(job_id, owner=owner)
            if current is None:
                yield f"data: {_json.dumps({'status': 'error', 'error': 'Job expired or access revoked'})}\n\n"
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
    """Chat with the compliance AI assistant. Stateless on the server."""
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
