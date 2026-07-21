"""
Policy Gap Analyzer — FastAPI Backend v3.0
Source-Grounded Compliance Intelligence System.

Features:
  - Complete Compliance Action Package (7 outputs from a single policy upload)
  - Curated internal retrieval (RAG) from healthcare compliance knowledge base
  - Controlled live research from curated regulatory sources
  - Post-generation verification against source material
  - Source attribution on every output (verified, retrieved, live research, model inference)
  - Privacy-first: No PHI or policy text is stored. All processing is ephemeral.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from app.config import settings
from app.routers import analysis, export, action_package, knowledge_base
from app.models.schemas import (
    DraftPolicyRequest,
    DraftedPolicy,
    DraftedPolicySection,
    ChatRequest,
    ChatResponse,
    CertificateExportRequest,
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

            if total_chunks == 0:
                logger.info(
                    "Knowledge base is empty — seeding with foundational regulatory content..."
                )
                results = seed_knowledge_base()
                total = sum(results.values())
                logger.info(
                    f"Knowledge base seeded: {total} chunks across {len(results)} sources"
                )
            else:
                logger.info(
                    f"Knowledge base already contains {total_chunks} chunks — skipping seed"
                )
        except Exception as e:
            logger.warning(
                f"Knowledge base auto-seed failed: {e}. Continuing without KB."
            )
            logger.warning(
                "The system will operate in model-only mode until the knowledge base is populated."
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

    yield

    # Shutdown
    logger.info("Policy Gap Analyzer API shutting down")


# ── Create the app ──
app = FastAPI(
    title="Policy Gap Analyzer API",
    description="Source-Grounded Healthcare Compliance Intelligence System. Upload a policy, get gap analysis, rewritten policy, redline, adjacent policies, 90-day remediation plan, board summary, and implementation checklist — all grounded in curated regulatory source material.",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)


# ── API Key Middleware (FIX #3) ──
@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Require API key for all endpoints except health check and docs."""
    # Skip auth for public endpoints and CORS preflight requests
    if request.url.path in ["/api/health", "/docs", "/redoc", "/", "/openapi.json"] or request.method == "OPTIONS":
        return await call_next(request)

    # If no API key is configured, allow all (development mode)
    if not settings.api_key:
        return await call_next(request)

    # Check API key header
    api_key = request.headers.get("x-api-key", "")
    if api_key != settings.api_key:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key. Set x-api-key header."},
        )

    return await call_next(request)


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
        )
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            raise HTTPException(
                status_code=429,
                detail="Rate limited. Please wait a moment before retrying.",
            )
        raise HTTPException(
            status_code=500, detail=f"Policy drafting failed: {error_msg}"
        )


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
    from app.services.draft_policy_service import draft_policy_stream, parse_draft_response

    async def event_stream():
        raw_text = ""
        try:
            async for chunk in draft_policy_stream(
                policy_description=request.policy_description,
                industry=request.industry,
                jurisdiction=request.jurisdiction,
            ):
                raw_text += chunk
                yield f"data: {_json.dumps({'delta': chunk})}\n\n"

            data = parse_draft_response(raw_text)
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
    from app.services.draft_policy_service import draft_policy_stream, parse_draft_response
    from app.services.draft_job_store import get_draft_job_store

    store = get_draft_job_store()
    raw_text = ""
    try:
        async for chunk in draft_policy_stream(
            policy_description=request.policy_description,
            industry=request.industry,
            jurisdiction=request.jurisdiction,
        ):
            raw_text += chunk
            await store.append_text(job_id, chunk)

        data = parse_draft_response(raw_text)
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
        response_text, follow_ups = await chat(
            message=request.message,
            mode=request.mode,
            industry=request.industry,
            jurisdiction=request.jurisdiction,
            context_summary=request.context_summary,
            conversation_history=request.conversation_history,
        )
        return ChatResponse(response=response_text, suggested_follow_ups=follow_ups)
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            raise HTTPException(
                status_code=429,
                detail="Rate limited. Please wait a moment before retrying.",
            )
        raise HTTPException(status_code=500, detail=f"Chat failed: {error_msg}")


@app.post("/api/export-certificate")
async def export_certificate(request: CertificateExportRequest):
    """Export a professional compliance assessment certificate as a .docx file."""
    from app.services.export_service import generate_certificate_export

    try:
        pkg_dict = request.package.model_dump(mode="json")
        file_name = request.file_name
        file_bytes, filename = generate_certificate_export(pkg_dict)
        if file_name:
            safe = file_name.replace(" ", "_").replace("/", "-")[:50]
            filename = f"Compliance_Certificate_{safe}.docx"
        return StreamingResponse(
            iter([file_bytes]),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Certificate export failed: {str(e)}"
        )


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
