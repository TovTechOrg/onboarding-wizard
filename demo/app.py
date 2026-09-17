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
import supabase_client as real_supabase_client
import uptimerobot_client as real_uptimerobot_client

from demo import github_client as demo_github_client
from demo import llm_client as demo_llm_client
from demo import render_client as demo_render_client
from demo import session_store as demo_session_store
from demo import supabase_client as demo_supabase_client
from demo import uptimerobot_client as demo_uptimerobot_client

_PAIRS = (
    (real_session_store, demo_session_store),
    (real_render_client, demo_render_client),
    (real_github_client, demo_github_client),
    (real_llm_client, demo_llm_client),
    # Added 2026-09-17 (final-review C3 fix): the demo's trimmed 4-frame UI
    # never drives either of these two clients from the browser, but every
    # endpoint that calls them is still mounted and reachable (main.app's
    # real router, unchanged -- see this module's own docstring), and
    # router.py's "Change render-key" cleanup path
    # (cleanupOrphanedUptimeMonitor()) reaches uptimerobot_client
    # unconditionally because demo/session_store.py pre-seeds the
    # uptime_pinger frame's api_key/monitor_id fields. Leaving either
    # unmocked is a real outbound call to a third party from a public demo.
    (real_supabase_client, demo_supabase_client),
    (real_uptimerobot_client, demo_uptimerobot_client),
)


def install_mocks() -> None:
    for real_module, demo_module in _PAIRS:
        for name in vars(demo_module):
            if name.startswith("_"):
                continue
            if callable(getattr(demo_module, name)) and hasattr(real_module, name):
                setattr(real_module, name, getattr(demo_module, name))
    _patch_seed_provider_config()


def _seed_provider_config_mock(
    database_url: str,
    provider: str,
    model: str,
    vertex_gcp_project: str | None,
    vertex_gcp_location: str | None,
) -> bool:
    """Stand-in for router.py's own `_seed_provider_config` (2026-09-17,
    final-review C1 fix).

    That function is not a separate importable module -- it lives directly
    in router.py, so it has no `demo/`-side pair to slot into `_PAIRS`
    above -- but it opens a REAL, live `psycopg` connection to
    `supabase["database_url"]` from the session, which demo/session_store.py
    pre-seeds as a fake, unreachable `postgresql://demo:demo@localhost:5432/
    demo`. Left unmocked, every "Finish & Deploy" attempt in the demo called
    the real function, always failed, and `bulk-push-env-vars` returned
    `{"valid": false, "reason": "slot_config_seed_failed"}` before ever
    calling `trigger-deploy` -- a reader who completed all 4 demo frames hit
    a permanent, unrecoverable error on the very last step, and the handoff
    link to the bot demo never fired.

    Always reports success (`True`, matching the real function's own
    success return -- see router.py's `_seed_provider_config` docstring),
    same as every other mocked client in this file.
    """
    return True


def _patch_seed_provider_config() -> None:
    """Rebind `router._seed_provider_config` to the stub above.

    Called from `install_mocks()` (not run as a plain module-level
    statement here) because at THIS module's own first-ever `install_mocks()`
    call (below, before `from main import app`), `router` has not been
    imported yet -- `main.py` is what imports it (`from router import
    router`). Looking the module up via `sys.modules` rather than a bare
    `import router` at call time means this is a no-op the first time
    (harmlessly -- see the second call below, right after `from main import
    app`, which is what actually takes effect) and a real, idempotent patch
    on every later call (e.g. tests/test_demo_routes.py's `demo_env`
    fixture re-invoking `install_mocks()` explicitly).

    `_seed_provider_config` is called inside router.py as a bare name
    (`_seed_provider_config(...)`, not `router._seed_provider_config(...)`)
    -- Python resolves that name against router.py's own module globals at
    call time, which `setattr`/dict-mutation on the `router` module object
    changes just as effectively as it changes any of the four `_PAIRS`
    modules' own bare-name calls to session_store/render_client/etc.
    """
    import sys

    router_module = sys.modules.get("router")
    if router_module is not None:
        router_module._seed_provider_config = _seed_provider_config_mock


install_mocks()

from main import app  # noqa: E402  (must follow install_mocks)

# router.py is already loaded by main's own `from router import router` above
# -- re-invoking the patch here (a no-op the first time, inside
# install_mocks() before main was imported) is what actually takes effect.
_patch_seed_provider_config()

import router  # noqa: E402  (router.py is already loaded by main's own import above)
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

    Also relaxes the served page's own Content-Security-Policy header to
    add `'self'` to `script-src` -- router.py's `_render_index()` (shared
    with the real wizard) sets `script-src 'unsafe-inline'` with no
    `'self'`, which is correct for the real page (every real script is
    inline) but silently blocks a real browser from ever loading this
    externally-referenced `<script src="/static/demo.js">` at all. Found
    by tests/test_demo_end_to_end.py -- every other demo test drives the
    app via httpx's ASGITransport, which never enforces CSP the way a
    real browser does, so this was invisible until one used real
    Chromium. Adjusting the header only on this response (never touching
    router.py's own header-building code) keeps the real wizard's
    stricter policy completely unchanged.

    Also adds `font-src 'self'` for the same reason (2026-09-17, final-
    review I9): router.py's CSP carries no `font-src` directive at all, so
    `static/fonts/*.woff2` never loads in a real browser and silently
    falls back to a system font -- verified via real Chromium console
    errors. This is a real, pre-existing bug in the production wizard too
    (out of scope to fix there in this branch -- logged in ISSUES.md), but
    since this demo-only header rewrite already exists for `script-src`,
    extending it costs nothing and fixes the demo's own rendering.

    Also eagerly mints a fresh, per-visitor session right on this `/`
    response when the request carries no session cookie yet (2026-09-17,
    final-review C2 fix), rather than waiting for the visitor to submit
    something -- see `_cookieless_visitors_share_one_session` below for the
    full reasoning (why this needs to happen HERE, on `/`, and why a
    cheaper "just check for a cookie" fix doesn't work for this demo
    specifically).
    """
    response = await call_next(request)
    if request.url.path != "/" or response.status_code != 200:
        return response

    body = b"".join([chunk async for chunk in response.body_iterator])
    html = body.decode("utf-8").replace("</body>", f"{_SCRIPT_TAG}</body>", 1)
    headers = {
        k: v for k, v in response.headers.items() if k.lower() != "content-length"
    }
    csp = headers.get("content-security-policy")
    if csp:
        csp = csp.replace(
            "script-src 'unsafe-inline'", "script-src 'unsafe-inline' 'self'"
        )
        if "font-src" not in csp:
            csp = f"{csp}; font-src 'self'"
        headers["content-security-policy"] = csp
    new_response = HTMLResponse(content=html, status_code=200, headers=headers)
    existing_session_id = request.cookies.get(router.SESSION_COOKIE_NAME)
    if existing_session_id is None or demo_session_store.get_session(existing_session_id) is None:
        session_id = demo_session_store.create_session()
        router._set_session_cookie(new_response, session_id)
    return new_response


from demo.routes import router as demo_router  # noqa: E402
from router import SESSION_COOKIE_NAME  # noqa: E402  (the wizard's own cookie name)

app.include_router(demo_router)

_SHARED_DEMO_SESSION = "demo-stateless-shared-session"


@app.middleware("http")
async def _cookieless_visitors_share_one_session(request, call_next):
    """Pin one shared session for a genuinely cookie-blocked browser only.

    Every bit of state here is synthetic and identical between visitors, so
    sharing is harmless -- the same argument the bot demo's stateless view
    rests on. What is NOT harmless is bouncing: this must never issue a
    redirect, because the equivalent bot-side fix did, and produced an
    infinite loop for precisely the browsers it was written to serve.

    Corrected 2026-09-17 (final-review C2 fix): the original version of
    this middleware pinned EVERY visitor with no session cookie -- which
    is true of every visitor's first-ever request, cookie-capable or not,
    since `/api/render/validate-key` (the only endpoint that ever mints a
    fresh session and sends `Set-Cookie`) is itself gated on
    `session_id is None or get_session(session_id) is None`. Pinning
    unconditionally made both of those false on every request, so a
    session was NEVER minted for ANYONE -- every visitor, cookie-capable
    or not, was permanently pinned to the one shared session, seeing every
    other visitor's progress and able to wipe it via "Start over". This
    contradicted this docstring's own "a cookie-capable visitor keeps their
    own session untouched" claim, which was aspirational, not true, before
    this fix.

    The real problem: a browser's first-ever request looks IDENTICAL
    whether or not it can store cookies at all -- neither carries a
    session cookie yet, and one request is not enough to tell "hasn't been
    issued one yet" apart from "would drop it if issued one". A first
    attempt at fixing this used a second, non-HttpOnly "probe" cookie set
    on `/` and checked on a later request, letting a visitor through
    unpinned as soon as the probe round-tripped once. That distinguished
    the two cases correctly, but broke something else this demo depends
    on that a plain per-visitor-isolation fix doesn't have to consider:
    the collapsed frames (dashboard-auth/supabase/uptime-pinger) are only
    reported "done" by reading THIS VISITOR'S OWN session's pre-seeded
    frame data (demo/session_store.py's `_PRESEEDED_FRAMES`), and the
    page's one-shot `restoreFromSession()` call (`GET /api/session`) fires
    immediately on page load -- before the visitor has done anything, so
    before any probe could ever have round-tripped. Under the probe
    design, that very first `GET /api/session` found no session at all
    (correctly not pinned, since it can't yet prove cookie-blocked
    status), so the collapsed frames' "done" state was simply missing for
    the entire rest of the page life (`restoreFromSession()` never runs
    again) -- verified live: `render-service`'s own MutationObserver-driven
    auto-create still fired and succeeded once render-key completed, but
    `github-app` never unlocked, because `dashboard-auth` (its positional
    predecessor in `completeFrame()`'s "unlock the next frame" chain) was
    still reporting "ready", not "done".

    Fixed properly instead: `_inject_demo_script` above now EAGERLY mints
    a real, per-visitor session (not the shared one) and sends its own
    real `Set-Cookie` on the very first `/` response that lacks one --
    before the visitor does anything at all, exactly matching what a
    session-per-visitor model needs for `restoreFromSession()`'s first
    call to already see this visitor's own pre-seeded collapsed frames.
    A cookie-capable browser retains that cookie and sends it back on
    every later request, so it never touches this middleware's pinning
    logic below at all -- it already has its own working session. A
    cookie-blocked browser drops it like it drops everything else, so
    every later non-`/` request still arrives with no session cookie and
    genuinely needs the shared-session fallback below, unchanged from the
    original design. No second round trip or probe cookie needed: whether
    a browser is cookie-capable is fully decided by whether the session
    cookie eagerly set on `/` comes back on the very next request.

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
    mock (not the real, Postgres-backed one), checked by object identity
    against `demo_session_store.get_session` rather than a string
    comparison against `__module__` -- a rename or re-export of
    `demo/session_store.py` can't silently make this check permanently
    inert the way a string literal could -- load-bearing for the TEST
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
    if real_session_store.get_session is not demo_session_store.get_session:
        return await call_next(request)

    # "/" is excluded here (not just "handled the same as everything
    # else") because `_inject_demo_script` above needs to see this
    # request's REAL, un-pinned cookie state to decide whether to eagerly
    # mint a fresh per-visitor session on the way out -- pinning it here
    # first would make that request look like it already has a session
    # (the shared one), and `_inject_demo_script` would wrongly skip
    # minting a real one for a genuinely new, cookie-capable visitor.
    if SESSION_COOKIE_NAME not in request.cookies and request.url.path != "/":
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
