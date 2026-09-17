"""The demo's real failure mode is the flow breaking, not a unit regressing.

`demo_app_url` below is a genuinely new fixture, NOT a reuse of
tests/conftest.py's `live_app_url`. `live_app_url` boots `main.app` via
uvicorn and never imports `demo.app` at all -- pointing browser tests at it
directly (an earlier draft of this file did exactly that) would drive the
real, unmocked wizard. Every assertion in this file, including the
cookie-blocked-browser test that is this plan's whole reason for existing,
would then pass regardless of whether Tasks 2-7's demo wiring (mocks,
frame-collapsing, the cookie-hostile fallback) was present at all, because
the plain wizard also never calls a real third party from the browser and
also never redirects a cookieless visitor. See this fixture's own docstring
for the fix.
"""

import socket
import threading
import time

import pytest
from cryptography.fernet import Fernet


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def demo_app_url(monkeypatch):
    """Boots `demo.app:app` (not `main.app`) live via uvicorn in a
    background thread -- same shape as tests/conftest.py's `live_app_url`
    (see its definition there), but importing `demo.app` is the whole
    point: that import is what runs `install_mocks()` and registers
    demo/app.py's own middleware (the demo script injector and the
    cookie-hostile fallback) onto the shared `main.app` object. Without
    that import, this fixture would just be `live_app_url` again.

    DATABASE_URL and ONBOARDING_SESSION_ENCRYPTION_KEY are set both via
    env var and directly on `config.settings` -- matching
    tests/test_demo_app_boot.py's `demo_env` fixture exactly -- because
    `config.settings` is a module-level singleton that may already be
    constructed (by an earlier test module's `import main`/`import
    config`) before this fixture's `setenv` calls run in a full-suite
    run; a bare `monkeypatch.setenv` alone can land too late.
    DATABASE_URL is a placeholder, never a real Postgres: demo/app.py
    rebinds `session_store.init_pool` (what main.py's lifespan calls) to
    demo/session_store.py's in-memory mock before that lifespan ever
    runs, so no real database connection is attempted -- confirmed by
    tests/test_demo_app_boot.py::test_app_boots_with_no_database, which
    boots the same app the same way with the same placeholder URL.

    Deliberately does NOT depend on tests/conftest.py's `live_app_url` /
    `db_url` (a real Postgres testcontainer) -- the demo needs neither,
    and depending on them would also pull every test in this file into
    the shared `db` xdist group for no reason relevant to this file (see
    the local `page` override below for the other half of that).
    """
    database_url = "postgresql://demo/demo"
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", encryption_key)

    import config

    monkeypatch.setattr(config.settings, "database_url", database_url)
    monkeypatch.setattr(config.settings, "onboarding_session_encryption_key", encryption_key)

    from demo.app import install_mocks

    # install_mocks() runs unconditionally exactly once -- at demo.app's
    # own first-ever import in this worker process (Python caches the
    # module after that). tests/conftest.py's
    # `_restore_real_clients_after_demo_app_import` autouse fixture
    # reverts the four mocked modules back to real after every test
    # MODULE that touches demo.app, so a later test file (or an earlier
    # test in the SAME module, if some fixture reordering ever changed
    # that) can find the real modules restored even though `demo.app`
    # "looks" already imported. Calling install_mocks() explicitly here,
    # every time -- the same defensive pattern tests/test_demo_routes.py
    # already uses -- makes this fixture's mocks genuinely active
    # regardless of what ran earlier in this worker.
    install_mocks()

    # demo/session_store.py's in-memory `_sessions` dict is a plain module
    # attribute, not scoped to any one uvicorn server -- it persists across
    # this whole worker process regardless of which (or how many)
    # demo_app_url instances have come and gone. Every fresh browser
    # context in this file has no cookie on its first request, which
    # demo/app.py's own `_cookieless_visitors_share_one_session` middleware
    # (by design -- see its docstring) always pins to the SAME hardcoded
    # shared session id, for every visitor, cookie-capable or not. Without
    # this reset, an earlier test's completed render-key/github-app steps
    # leak into every later test's "fresh" page load via that one shared
    # session, silently changing what each test sees (confirmed directly:
    # a later test's render-key frame showed "Validated" before the test
    # ever touched it). `reset()` is exactly what tests/test_demo_session_
    # store.py already uses for the same reason.
    from demo import render_client as demo_render_client
    from demo import session_store as demo_session_store

    demo_session_store.reset()
    demo_render_client.reset()

    import uvicorn

    from demo.app import app as demo_asgi_app

    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(demo_asgi_app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("demo_app_url: uvicorn server did not start within 10s")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(browser, demo_app_url):
    """Overrides tests/conftest.py's `page` fixture for this module only.
    That fixture hardcodes `page(browser, live_app_url)`, which would boot
    a second, real-main.app live server this file never uses and would
    tag every test here into the shared `db` xdist group (via
    live_app_url -> db_url) for no reason relevant to this file. Same
    per-test browser-context isolation, pointed at our own
    `demo_app_url` instead.
    """
    context = browser.new_context()
    new_page = context.new_page()
    yield new_page
    context.close()


def test_only_four_frames_are_visible(page, demo_app_url):
    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")

    for frame_id in ("render-service", "dashboard-auth", "supabase", "uptime-pinger"):
        assert page.locator(f"#frame-{frame_id}").is_hidden()
    for frame_id in ("render-key", "github-app", "llm-provider", "render-deploy"):
        assert page.locator(f"#frame-{frame_id}").count() == 1


def test_visible_frames_are_numbered_one_to_four(page, demo_app_url):
    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")

    numbers = []
    for frame_id in ("render-key", "github-app", "llm-provider", "render-deploy"):
        title = page.locator(f"#frame-{frame_id} .frame-title").inner_text()
        numbers.append(title.strip().split(".")[0])
    assert numbers == ["1", "2", "3", "4"], f"got {numbers}"


def test_no_request_ever_leaves_for_a_real_third_party(page, demo_app_url):
    """The bug Plan 1 shipped and its final review caught: a mock that was
    never installed, leaving a live call to a real API from the demo."""
    external = []
    page.on("request", lambda r: external.append(r.url)
            if any(host in r.url for host in
                   ("api.render.com", "api.github.com", "googleapis.com",
                    "api.groq.com", "supabase.com", "uptimerobot.com"))
            else None)

    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")
    page.fill("#render-key-input", "demo-key")
    page.click("#render-key-submit")
    page.wait_for_timeout(1500)

    assert external == [], f"demo reached real third parties: {external}"


def test_a_cookie_blocked_browser_is_not_bounced(browser, demo_app_url):
    """The exact regression Plan 1 shipped and needed two fix waves to clear:
    its cookie-hostile fix produced an infinite redirect loop for the very
    browsers it was written for. A unit test did not catch it; this does.

    "no redirect loop" alone is not proof the fallback engaged, though --
    the plain, un-mocked wizard also never redirects a cookieless visitor
    (it just has no working session at all), so a test that stopped at the
    redirect-count assertion would pass even if
    `_cookieless_visitors_share_one_session` were deleted outright. The two
    blocks below drive a real credential through this blocked context and
    then reload it, proving a genuine, persistent, server-side session is
    live for a browser that stores no cookie at all -- not just "didn't
    crash."""
    context = browser.new_context()
    context.add_init_script(
        "Object.defineProperty(document, 'cookie', "
        "{get: () => '', set: () => {}, configurable: true});"
    )
    blocked = context.new_page()

    redirects = []
    blocked.on("response", lambda r: redirects.append(r.url)
               if 300 <= r.status < 400 else None)

    blocked.goto(demo_app_url, wait_until="networkidle")
    blocked.wait_for_selector("#demoBanner", timeout=10_000)

    assert blocked.locator("#frame-render-key").is_visible()
    assert len(redirects) < 3, f"redirect loop for a cookie-blocked visitor: {redirects}"

    # Positive assertion #1: the render-key frame is genuinely interactive
    # for this blocked browser, not just visible -- submitting a
    # credential requires a session for router.py's validate-key endpoint
    # to write into, so the next frame unlocking is proof a real session
    # exists and is usable.
    blocked.fill("#render-key-input", "demo-key")
    blocked.click("#render-key-submit")
    blocked.wait_for_selector("#frame-github-app:not([data-locked='true'])", timeout=10_000)

    # Positive assertion #2: it is the SAME session, not a fresh one
    # minted per request (which would look identical for exactly one page
    # view). A second page load in this same cookie-blocked context must
    # read the unlocked state back via GET /api/session -- that only
    # happens if the shared/pinned session id set by
    # `_cookieless_visitors_share_one_session` actually round-trips
    # across requests, proving statefulness under the fallback rather
    # than a coincidence of a single request.
    blocked.goto(demo_app_url, wait_until="networkidle")
    blocked.wait_for_selector("#demoBanner", timeout=10_000)
    assert blocked.locator("#frame-github-app").get_attribute("data-locked") != "true", (
        "a second page load in the same cookie-blocked context lost the "
        "first load's progress -- the cookie-hostile fallback is not "
        "actually stateful, only silent"
    )

    context.close()


def test_the_github_shortcut_advances_the_frame(page, demo_app_url):
    """Proves the DataTransfer file-input path actually works in a browser --
    assigning .value to a file input is impossible, so this is the one way to
    know the shortcut is real."""
    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")
    page.fill("#render-key-input", "demo-key")
    page.click("#render-key-submit")
    page.wait_for_selector("#frame-github-app:not([data-locked='true'])", timeout=10_000)

    page.click("#demoUseCredentials")
    page.wait_for_selector("#frame-github-app[data-status='done']", timeout=10_000)


def test_banner_survives_a_language_switch(page, demo_app_url):
    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")
    page.click("#langToggleBtn")
    page.click('input[name="lang"][value="he"]')
    page.wait_for_timeout(300)

    assert page.locator("#demoBanner").is_visible()
    title = page.locator("#frame-render-key .frame-title").inner_text()
    assert title.strip().startswith("1."), "renumbering must survive applyLanguage()"
