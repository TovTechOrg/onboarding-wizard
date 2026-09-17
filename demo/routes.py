"""Demo-only routes layered over the real wizard."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_STEPS = {
    "wizard_start", "render_key_done", "github_app_done",
    "provider_done", "deploy_done", "handoff_clicked",
}


@router.post("/api/demo/step/{name}")
async def record_step(name: str) -> JSONResponse:
    """Analytics: one structured line per step reached, read from the host's
    logs during the launch window. No database, no third-party script, no
    stored identifier. Unknown names are collapsed rather than echoed, so a
    crafted path cannot write arbitrary text into the log.
    """
    step = name if name in _ALLOWED_STEPS else "unknown"
    logger.info("demo_step step=%s", step)
    return JSONResponse({"recorded": step})
