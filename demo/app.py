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


from demo.routes import router as demo_router  # noqa: E402
from router import SESSION_COOKIE_NAME  # noqa: E402  (the wizard's own cookie name)

app.include_router(demo_router)

_SHARED_DEMO_SESSION = "demo-stateless-shared-session"


@app.middleware("http")
async def _cookieless_visitors_share_one_session(request, call_next):
    """Pin one shared session when the browser stores no cookie.

    Every bit of state here is synthetic and identical between visitors, so
    sharing is harmless -- the same argument the bot demo's stateless view
    rests on. What is NOT harmless is bouncing: this must never issue a
    redirect, because the equivalent bot-side fix did, and produced an
    infinite loop for precisely the browsers it was written to serve.

    Registration order is load-bearing, not stylistic. FastAPI's
    `@app.middleware("http")` calls `add_middleware(BaseHTTPMiddleware,
    ...)`, and Starlette's `add_middleware` does `user_middleware.insert(0,
    ...)` -- confirmed by reading applications.py directly, not assumed.
    Combined with `build_middleware_stack()`'s `reversed(middleware)` wrap
    order, the LAST-registered `@app.middleware("http")` ends up OUTERMOST,
    i.e. it runs first on the way in. This one is registered after
    `_inject_demo_script` above specifically so it is outermost and mutates
    the request before anything else (including router.py's own cookie
    reads) ever constructs a Request from this scope. This was verified
    empirically (a standalone two-middleware probe script logging entry
    order plus an endpoint reading `request.cookies`), not just reasoned
    about -- see task-7-report.md. `Request.cookies` is a lazily-cached
    property per Request instance, but each middleware layer and the final
    endpoint each construct their OWN Request wrapping the same mutated
    `scope` dict (`Request.__init__` stores `self.scope = scope` by
    reference, not by copy), so a fresh Request built anywhere downstream of
    this mutation sees the injected cookie correctly.

    Only the request scope is touched, so a cookie-capable visitor keeps
    their own session untouched.

    Guarded on `real_session_store.get_session` still being the demo's own
    mock (not the real, Postgres-backed one) -- load-bearing for the TEST
    process, not production. `main.app` is reused unchanged (see this
    module's own docstring), so this middleware, once added by this
    module's first import in a given process, stays attached to that same
    object for the rest of the process's life; a decorator-registered
    Starlette middleware cannot be cleanly un-registered afterward, and
    reusing `main.app` is the whole point (it's what keeps the
    RequestValidationError scrubbing). In a real deployment demo/app.py is
    the ONLY app that ever runs in its process, so `real_session_store` is
    permanently the mock and this guard is always true. Inside the test
    suite, though, tests/conftest.py's
    `_restore_real_clients_after_demo_app_import` fixture reverts
    `real_session_store` back to the genuine module after each test module
    that touches `demo.app` -- without this guard, this middleware would
    otherwise keep injecting a synthetic cookie into every request made
    against the very same `main.app` object by unrelated test files
    (e.g. tests/test_onboarding_router.py's own "no cookie" tests),
    corrupting their real, un-mocked session behaviour. Checked this
    empirically: without this guard, adding this middleware broke over a
    dozen unrelated tests in the full suite that assert on `main.app`'s
    genuine cookieless behaviour.
    """
    if real_session_store.get_session.__module__ != "demo.session_store":
        return await call_next(request)

    if SESSION_COOKIE_NAME not in request.cookies:
        headers = list(request.scope.get("headers", []))
        headers.append(
            (b"cookie", f"{SESSION_COOKIE_NAME}={_SHARED_DEMO_SESSION}".encode())
        )
        request.scope["headers"] = headers
        # The shared id must exist in the store, or every wrapper returns
        # SessionNotFound and the reader sees a dead wizard.
        if demo_session_store.get_session(_SHARED_DEMO_SESSION) is None:
            demo_session_store.adopt_session(_SHARED_DEMO_SESSION)

    return await call_next(request)


__all__ = ["app", "install_mocks"]
