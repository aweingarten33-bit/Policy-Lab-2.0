"""
Export Router — API endpoints for generating and downloading reports.
Supports both single gap analysis reports and full Compliance Action Packages.
"""

import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.models.schemas import ExportRequest, PackageExportRequest, ExportFormat, DraftPolicyExportRequest, UpdatedPolicyExportRequest
from app.services.export_service import generate_export, generate_action_package_export, generate_draft_policy_export, generate_updated_policy_export

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["export"])


@router.post("/export")
async def export_report(request: ExportRequest):
    """
    Generate and download a single gap analysis report.
    Supports .docx format. The report is generated in memory — no files stored on disk.
    """
    try:
        file_bytes, filename = generate_export(
            result=request.result,
            file_name=request.file_name,
            export_format=request.export_format,
        )
    except Exception as e:
        logger.error(f"Export generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Report generation failed: {str(e)}")

    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export-draft")
async def export_draft_policy(request: DraftPolicyExportRequest):
    """Export a drafted policy as a professional .docx file. Generated in memory — no files stored."""
    try:
        policy_dict = request.policy.model_dump()
        file_bytes, filename = generate_draft_policy_export(policy_dict)
    except Exception as e:
        logger.error(f"Draft policy export failed: {e}")
        raise HTTPException(status_code=500, detail=f"Draft export failed: {str(e)}")

    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export-updated-policy")
async def export_updated_policy(request: UpdatedPolicyExportRequest):
    """
    Generate a clean .docx of just the rewritten/updated policy.
    No analysis, no redline — just the new policy text formatted as a real
    policy document, ready to drop into a handbook or policy library.
    """
    try:
        file_bytes, filename = generate_updated_policy_export(
            rewritten=request.rewritten_policy,
            source_file_name=request.source_file_name,
        )
    except Exception as e:
        logger.error(f"Updated policy export failed: {e}")
        raise HTTPException(status_code=500, detail=f"Updated policy export failed: {str(e)}")

    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export-package")
async def export_action_package(request: PackageExportRequest):
    """
    Generate and download the complete Compliance Action Package as a single .docx.
    Includes all 7 outputs in a professionally formatted document.
    """
    try:
        file_bytes, filename = generate_action_package_export(
            package=request.package,
            file_name=request.file_name,
            export_format=request.export_format,
        )
    except Exception as e:
        logger.error(f"Action package export failed: {e}")
        raise HTTPException(status_code=500, detail=f"Package export failed: {str(e)}")

    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
