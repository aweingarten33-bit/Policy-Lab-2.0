"""
Action Package Router — API endpoints for the Complete Compliance Action Package.
Generates all 7 outputs from a single policy upload.
"""

import asyncio
import json
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from typing import Optional, List

from app.models.schemas import (
    ActionPackageRequest, ComplianceActionPackage, PackageStatus,
)
from app.services.orchestrator import get_orchestrator
from app.services.text_extraction import extract_text_from_file
from app.services.job_store import get_job_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["action-package"])

# Strong references to background tasks prevent premature GC cancellation.
_background_tasks: set[asyncio.Task] = set()


def _humanize_error(msg: str) -> str:
    if "429" in msg:
        return "Rate limited. Please wait a moment before retrying."
    if "402" in msg:
        return "API key has insufficient credits."
    if "529" in msg:
        return "Model is overloaded. Please retry in a moment."
    return msg


async def _run_action_package_job(job_id: str, request: ActionPackageRequest) -> None:
    """Background runner: drives the orchestrator and writes snapshots into the job store."""
    store = get_job_store()
    orchestrator = get_orchestrator()
    last_package: Optional[ComplianceActionPackage] = None
    try:
        async for package in orchestrator.generate_full_package_stream(
            text=request.text,
            file_name=request.file_name,
            industry=request.industry,
            jurisdiction=request.jurisdiction,
            requested_outputs=request.outputs,
            enable_live_research=request.enable_live_research,
        ):
            last_package = package
            await store.update_package(job_id, package)
        if last_package is not None and last_package.status == PackageStatus.failed:
            await store.mark_error(job_id, _humanize_error(last_package.error_message or "Generation failed"))
        else:
            await store.mark_complete(job_id)
    except Exception as e:
        logger.exception(f"Background action-package job {job_id} failed")
        await store.mark_error(job_id, _humanize_error(str(e)))


@router.post("/action-package", response_model=ComplianceActionPackage)
async def generate_action_package(request: ActionPackageRequest):
    """
    Generate the Complete Compliance Action Package from a policy document.
    
    Produces up to 7 outputs:
    1. Gap Analysis
    2. Rewritten Policy
    3. Redline Document (tracked changes)
    4. Adjacent Policy Recommendations
    5. 90-Day Remediation Plan
    6. Board-Ready Summary
    7. Implementation Checklist
    
    No policy text is stored — processing is stateless and ephemeral.
    """
    orchestrator = get_orchestrator()

    try:
        package = await orchestrator.generate_full_package(
            text=request.text,
            file_name=request.file_name,
            industry=request.industry,
            jurisdiction=request.jurisdiction,
            requested_outputs=request.outputs,
            enable_live_research=request.enable_live_research,
        )
        return package
    except ValueError as e:
        logger.error(f"Action package error: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error generating action package: {e}")
        error_msg = str(e)
        if "429" in error_msg:
            raise HTTPException(status_code=429, detail="Rate limited. Please wait a moment before retrying.")
        if "402" in error_msg:
            raise HTTPException(status_code=402, detail="API key has insufficient credits.")
        if "529" in error_msg:
            raise HTTPException(status_code=503, detail="Model is overloaded. Please retry in a moment.")
        raise HTTPException(status_code=500, detail=f"Action package generation failed: {error_msg}")


@router.post("/action-package-stream")
async def generate_action_package_stream(request: ActionPackageRequest):
    """
    SSE streaming version — yields ComplianceActionPackage JSON after each phase:
      Phase 0: gap analysis (~30s) — displayed immediately
      Phase 1: rewrite + adjacent + action plan in parallel
      Phase 2: redline + board summary + checklist in parallel
    """
    orchestrator = get_orchestrator()

    async def event_stream():
        try:
            async for package in orchestrator.generate_full_package_stream(
                text=request.text,
                file_name=request.file_name,
                industry=request.industry,
                jurisdiction=request.jurisdiction,
                requested_outputs=request.outputs,
                enable_live_research=request.enable_live_research,
            ):
                yield f"data: {package.model_dump_json()}\n\n"
        except Exception as e:
            import json
            logger.error(f"Streaming error: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/action-package-file", response_model=ComplianceActionPackage)
async def generate_action_package_from_file(
    file: UploadFile = File(...),
    jurisdiction: Optional[str] = Form(None),
    enable_live_research: Optional[str] = Form("false"),
):
    """
    Upload a policy file and generate the Complete Compliance Action Package.
    
    Supports: .txt, .md, .docx, .pdf, .rtf
    Max file size: 10MB
    No files are stored — text is extracted in memory and processed statelessly.
    """
    # Validate file size
    max_size = 10 * 1024 * 1024  # 10MB
    contents = await file.read()
    if len(contents) > max_size:
        raise HTTPException(status_code=413, detail=f"File too large ({len(contents) / 1024 / 1024:.1f}MB). Maximum is 10MB.")

    # Validate file type
    allowed_extensions = {".txt", ".md", ".docx", ".doc", ".pdf", ".rtf"}
    file_ext = "." + (file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "")
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_ext}. Allowed: {', '.join(allowed_extensions)}")

    # Extract text
    try:
        text = await extract_text_from_file(contents, file.filename, file_ext)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not extract text from file: {str(e)}")

    if not text or len(text.strip()) < 50:
        raise HTTPException(status_code=422, detail="Could not extract readable text (minimum 50 characters). Try pasting the policy text directly.")

    # Generate the action package
    live_research = enable_live_research.lower() in ("true", "1", "yes")
    orchestrator = get_orchestrator()
    try:
        package = await orchestrator.generate_full_package(
            text=text,
            file_name=file.filename,
            jurisdiction=jurisdiction,
            enable_live_research=live_research,
        )
        return package
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            raise HTTPException(status_code=429, detail="Rate limited. Please wait a moment before retrying.")
        if "402" in error_msg:
            raise HTTPException(status_code=402, detail="API key has insufficient credits.")
        raise HTTPException(status_code=500, detail=f"Action package generation failed: {error_msg}")


# ---------------------------------------------------------------------------
# Background-job endpoints
# ---------------------------------------------------------------------------
# These let the client kick off an analysis and reconnect to it later, so the
# work survives tab-switches, navigation, and brief network drops. Job state
# lives only in memory and self-expires after 30 minutes.

@router.post("/action-package/start")
async def start_action_package_job(request: ActionPackageRequest):
    """Kick off an analysis as a background task. Returns a job_id immediately.

    Use GET /api/action-package/stream/{job_id} to subscribe to live updates,
    or GET /api/action-package/status/{job_id} for a one-shot snapshot.
    """
    store = get_job_store()
    job_id = await store.create()
    task = asyncio.create_task(_run_action_package_job(job_id, request))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"job_id": job_id}


@router.get("/action-package/status/{job_id}")
async def get_action_package_job_status(job_id: str):
    """Return the current snapshot of a job. 404 if not found or expired."""
    store = get_job_store()
    job = await store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "package": job.package.model_dump(mode="json") if job.package else None,
        "error": job.error,
        "version": job.version,
    }


@router.get("/action-package/stream/{job_id}")
async def stream_action_package_job(job_id: str):
    """SSE stream of job updates. Sends a frame whenever the job version changes,
    then closes once the job is complete or errored."""
    store = get_job_store()
    initial = await store.get(job_id)
    if initial is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    async def event_stream():
        last_version = -1
        # Watchdog: stop streaming after 15 minutes of inactivity to avoid orphaned
        # generators if a client connects and never disconnects.
        max_iterations = 15 * 60 * 2  # 0.5s per iteration
        iterations = 0
        while iterations < max_iterations:
            iterations += 1
            current = await store.get(job_id)
            if current is None:
                yield f"data: {json.dumps({'status': 'error', 'error': 'Job expired'})}\n\n"
                return
            if current.version != last_version:
                last_version = current.version
                payload = {
                    "status": current.status,
                    "package": current.package.model_dump(mode="json") if current.package else None,
                    "error": current.error,
                    "version": current.version,
                }
                yield f"data: {json.dumps(payload)}\n\n"
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
