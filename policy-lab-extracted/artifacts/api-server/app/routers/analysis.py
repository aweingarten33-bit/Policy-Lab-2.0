"""
Analysis Router — API endpoints for policy gap analysis.
"""

import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional

from app.models.schemas import AnalysisRequest, AnalysisResult, HealthResponse
from app.services.llm_service import analyze_policy
from app.services.text_extraction import extract_text_from_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["analysis"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="ok", version="3.0.0")


@router.post("/analyze", response_model=AnalysisResult)
async def analyze_policy_endpoint(request: AnalysisRequest):
    """
    Analyze a healthcare policy for regulatory gaps.
    
    Accepts policy text directly. Returns structured gap analysis results.
    No policy text is stored — processing is stateless and ephemeral.
    """
    try:
        result = await analyze_policy(
            text=request.text,
            file_name=request.file_name,
            jurisdiction=request.jurisdiction,
        )
        return result
    except ValueError as e:
        logger.error(f"Analysis error: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error during analysis: {e}")
        error_msg = str(e)
        if "429" in error_msg:
            raise HTTPException(status_code=429, detail="Rate limited. Please wait a moment before retrying.")
        if "402" in error_msg:
            raise HTTPException(status_code=402, detail="API key has insufficient credits.")
        if "529" in error_msg:
            raise HTTPException(status_code=503, detail="Model is overloaded. Please retry in a moment.")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {error_msg}")


@router.post("/analyze-file", response_model=AnalysisResult)
async def analyze_file_endpoint(
    file: UploadFile = File(...),
    jurisdiction: Optional[str] = Form(None),
):
    """
    Upload a policy file for gap analysis.
    
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

    # Run analysis
    try:
        result = await analyze_policy(
            text=text,
            file_name=file.filename,
            jurisdiction=jurisdiction,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            raise HTTPException(status_code=429, detail="Rate limited. Please wait a moment before retrying.")
        if "402" in error_msg:
            raise HTTPException(status_code=402, detail="API key has insufficient credits.")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {error_msg}")
