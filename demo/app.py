"""Demo entrypoint: `uvicorn demo.app:app`.

Rebinds every external client to an in-memory mock BEFORE importing main,
then re-exports main's own app object unchanged.

Reusing main.app is load-bearing, not convenience: main.py registers the
RequestValidationError handler that returns a flat {"detail": "invalid
request"}, which is what stops FastAPI echoing a submitted credential back
in a 422. A demo that built its own FastAPI() would silently lose it.

Rebinding works because router.py imports these as plain modules and
resolves `<module>.<name>` at call time -- see its own comment at :61-65.
"""

from __future__ import annotations

import github_client as real_github_client
import llm_client as real_llm_client
import render_client as real_render_client
import session_store as real_session_store

from demo import github_client as demo_github_client
from demo import llm_client as demo_llm_client
from demo import render_client as demo_render_client
from demo import session_store as demo_session_store

_PAIRS = (
    (real_session_store, demo_session_store),
    (real_render_client, demo_render_client),
    (real_github_client, demo_github_client),
    (real_llm_client, demo_llm_client),
)


def install_mocks() -> None:
    for real_module, demo_module in _PAIRS:
        for name in vars(demo_module):
            if name.startswith("_"):
                continue
            if callable(getattr(demo_module, name)) and hasattr(real_module, name):
                setattr(real_module, name, getattr(demo_module, name))


install_mocks()

from main import app  # noqa: E402  (must follow install_mocks)

from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse, HTMLResponse  # noqa: E402

_DEMO_STATIC = Path(__file__).parent / "static"
_SCRIPT_TAG = '<script src="/static/demo.js" defer></script>'


@app.get("/static/demo.js", include_in_schema=False)
async def _demo_script() -> FileResponse:
    return FileResponse(_DEMO_STATIC / "demo.js", media_type="application/javascript")


@app.middleware("http")
async def _inject_demo_script(request, call_next):
    """Inject the demo asset into the served page without editing index.html.

    index.html is left byte-identical on purpose: tests/test_onboarding_page.py
    pins one fetch(...) per credential-carrying endpoint, and the demo trims
    the flow by mocking the backend rather than by editing the page.
    """
    response = await call_next(request)
    if request.url.path != "/" or response.status_code != 200:
        return response

    body = b"".join([chunk async for chunk in response.body_iterator])
    html = body.decode("utf-8").replace("</body>", f"{_SCRIPT_TAG}</body>", 1)
    return HTMLResponse(content=html, status_code=200, headers={
        k: v for k, v in response.headers.items() if k.lower() != "content-length"
    })


__all__ = ["app", "install_mocks"]
