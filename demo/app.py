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

__all__ = ["app", "install_mocks"]
