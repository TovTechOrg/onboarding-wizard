"""Demo-only routes layered over the real wizard."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_STEPS = {
    "wizard_start", "render_key_done", "github_app_done",
    "provider_done", "deploy_done", "handoff_clicked",
}


# See config.py's demo_launcher_ping_path field comment for why this exists
# as a second endpoint rather than reusing "/healthz" (main.py) -- same
# trivial body, but the sibling repo's launcher's own cross-origin poll is
# the only caller.
@router.get(settings.demo_launcher_ping_path)
@router.head(settings.demo_launcher_ping_path)
async def launcher_ping() -> dict:
    return {"status": "ok"}


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
