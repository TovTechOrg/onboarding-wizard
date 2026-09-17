"""Shared Postgres test harness. Uses DATABASE_URL if the environment already
provides one (CI's `services: postgres`); otherwise spins a throwaway Postgres
via testcontainers (local dev — Docker required). Never touches Supabase."""
from __future__ import annotations

import os
import socket
import threading
import time
from urllib.parse import urlsplit

import pytest
from cryptography.fernet import Fernet

# Hosts treated as "local/CI Postgres, safe for tests to TRUNCATE". Anything
# else (e.g. a Supabase pooler hostname) is refused unless the operator
# explicitly opts in via ALLOW_REMOTE_TEST_DB=1 -- this guard exists solely so
# an accidentally-exported DATABASE_URL pointing at a real Supabase database
# can never get truncated by a test run.
_LOCAL_TEST_DB_HOSTS = {"localhost", "127.0.0.1"}


def _looks_like_local_test_db(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host in _LOCAL_TEST_DB_HOSTS or host.endswith(".internal")


def _close_onboarding_pool() -> None:
    """Best-effort close of session_store's pool -- a no-op if `db` was
    never requested this session/worker (the module may not even be
    importable in a worker that never touched it)."""
    try:
        import session_store as onboarding_store
    except ImportError:
        return
    onboarding_store.close_pool()


@pytest.fixture(scope="session")
def db_url() -> str:
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        if not _looks_like_local_test_db(env_url) and not os.environ.get(
            "ALLOW_REMOTE_TEST_DB"
        ):
            raise AssertionError(
                "DATABASE_URL does not look like a local/CI Postgres (host must be "
                "'localhost', '127.0.0.1', or end in '.internal'). Refusing to run "
                "destructive tests (TRUNCATE) against it -- this guard protects a real "
                "database (e.g. Supabase) from being wiped by a test run. If this really "
                "is an intentional, disposable local/CI Postgres on an unusual "
                "hostname, set ALLOW_REMOTE_TEST_DB=1 to bypass."
            )
        yield env_url
        # Matches the testcontainers branch below: whichever pool the `db`
        # fixture built against this session's db_url gets closed exactly
        # once, here, rather than per-test -- see `db`'s docstring.
        # _close_onboarding_pool() is a no-op if `db` was never requested
        # this session.
        _close_onboarding_pool()
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        # driver=None gives a bare "postgresql://" scheme for raw psycopg3.
        # driver="psycopg" (the previous value) builds a SQLAlchemy-style
        # "postgresql+psycopg://" dialect+driver URL, which psycopg3's own
        # parser cannot read at all ('missing "=" after "postgresql+psycopg:...'"').
        # This was masked until now: CI's services:postgres sets DATABASE_URL
        # directly and never calls this method, and every local run before
        # Docker/WSL integration was enabled failed earlier on
        # docker.errors.DockerException, before this code path ever ran.
        yield pg.get_connection_url(driver=None)
        _close_onboarding_pool()


@pytest.fixture
def db(db_url, monkeypatch):
    """Points session_store at the test Postgres and truncates its one
    table (wizard_sessions) before each test that requests this fixture."""
    import session_store as onboarding_store
    from config import settings as onboarding_settings

    monkeypatch.setattr(onboarding_settings, "database_url", db_url)
    onboarding_store.init_pool()
    with onboarding_store._require_pool().connection() as conn:
        conn.execute("TRUNCATE wizard_sessions")
    yield


@pytest.fixture
def db_exec(db_url):
    """Run a raw statement against the test DB (replaces test-side sqlite3.connect)."""
    import psycopg

    def _exec(sql: str, params: tuple = ()):
        with psycopg.connect(db_url) as conn:
            conn.execute(sql, params)
            conn.commit()

    return _exec


@pytest.fixture
def db_query(db_url):
    """Run a raw query and return the rows (list of tuples)."""
    import psycopg

    def _query(sql: str, params: tuple = ()):
        with psycopg.connect(db_url) as conn:
            return conn.execute(sql, params).fetchall()

    return _query


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_app_url(db_url):
    """Boots the real FastAPI app (main:app) via uvicorn in a background
    thread, bound to a free local port -- for Playwright's browser fixture
    to navigate against. Depends on db_url purely so main.py's lifespan can
    open a real Postgres connection and boot at all (it raises RuntimeError
    otherwise); individual browser tests never touch this database
    directly for their own assertions (they intercept the specific
    /api/* responses they need via page.route()), so this never depends on
    the per-test `db` fixture's truncation. Settings are mutated directly
    (not via monkeypatch) since this is a one-time, session-scoped setup
    with nothing else to restore."""
    import uvicorn

    from config import settings as onboarding_settings

    onboarding_settings.database_url = db_url
    onboarding_settings.onboarding_session_encryption_key = Fernet.generate_key().decode()

    import main as onboarding_main

    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(onboarding_main.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("live_app_url: uvicorn server did not start within 10s")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def browser():
    """One headless Chromium instance for this single test only --
    function-scoped, NOT session-scoped. Playwright's sync API keeps an
    asyncio event loop marked as "running" on the main thread for as long
    as its sync_playwright() context stays open (confirmed directly: even
    with no browser launched yet, merely entering that context makes
    asyncio.events._get_running_loop() return a live loop on the calling
    thread) -- a session-scoped fixture holding that context open for the
    whole suite breaks every *other*, unrelated pytest-asyncio async test
    that runs afterward in the same worker process with "RuntimeError:
    Runner.run() cannot be called from a running event loop". Opening and
    fully closing the context within each single test's synchronous call
    keeps that leak transient and invisible to every other test -- see
    docs/superpowers/specs/2026-09-06-onboarding-browser-tests-design.md
    section 3's correction for the full story. Sync Playwright API is used
    at all (rather than the async one) because it cannot run inside an
    active asyncio event loop, which every *async def* test in this
    project runs inside under asyncio_mode = "auto"."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(headless=True)
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser, live_app_url):
    """A fresh incognito-style browser context and page per test -- so
    sessionStorage/cookies never leak between tests, matching the
    isolation every other fixture in this file already gives."""
    context = browser.new_context()
    new_page = context.new_page()
    yield new_page
    context.close()


def _uses_real_browser(item: pytest.Item) -> bool:
    """True if item's fixture closure includes `page` -- the fixture every
    real browser test requests, directly or (via live_app_url -> db_url)
    transitively also picking up the existing `db` marker/xdist_group
    below, which is intentional: it keeps browser tests grouped onto the
    same xdist worker as other Postgres-touching tests rather than having
    multiple workers each spin up their own live server + Chromium
    instance."""
    return "page" in item.fixturenames


def _touches_shared_postgres(item: pytest.Item) -> bool:
    """True if item's fixture closure includes db_url -- the root fixture
    that db, db_exec, and db_query all depend on, and that some tests
    request directly. Checking the root rather than the three derived
    names means a test can't slip through by requesting db_url on its
    own."""
    return "db_url" in item.fixturenames


@pytest.fixture(scope="module", autouse=True)
def _restore_real_clients_after_demo_app_import():
    """`demo/app.py` rebinds attributes directly onto the real session_store /
    render_client / github_client / llm_client modules as an import-time side
    effect of its own `install_mocks()` call, which runs unconditionally at
    module scope (so `uvicorn demo.app:app` works with no extra call -- see
    demo/app.py's own docstring) and therefore only ONCE per process, the
    first time anything imports `demo.app` -- Python caches the module after
    that, so a second `from demo.app import app` elsewhere does not call
    install_mocks() again. That one-shot rebind is correct for running the
    demo as its own process, but inside THIS test process it would otherwise
    permanently mock those four modules for every other test that happens to
    run afterward in the same pytest-xdist worker (measured while building
    tests/test_demo_app_boot.py: 86+ unrelated failures in
    test_onboarding_render_client.py / test_onboarding_llm_client.py before a
    restore fixture existed).

    This used to be a fixture private to tests/test_demo_app_boot.py -- that
    protected only that one file. Tasks 5+ add more test files that also
    import `demo.app` (test_demo_chrome.py, test_demo_routes.py,
    test_demo_end_to_end.py, ...), each facing the identical hazard, so the
    fix belongs here where every test file gets it for free with no per-file
    opt-in required -- a module-scoped autouse fixture in conftest.py gets
    instantiated separately for EVERY test module pytest collects, so this
    same protection now applies per-file automatically, matching the
    per-file blast radius of the hazard it guards against.

    Deliberately module-scoped, not function-scoped: since install_mocks()
    only ever fires once per process (module-level caching, see above), a
    function-scoped restore would restore the real modules after the first
    test that happens to trigger the import, leaving every later test in
    that file with no mocks at all even though `demo.app` "looks" imported
    -- this was caught directly: switching this fixture to function scope
    during development broke test_app_boots_with_no_database (the third test
    in test_demo_app_boot.py) because the mocks it depends on had already
    been un-installed by test 1's teardown. Module scope matches the actual
    lifetime of the thing being guarded (once per module import, not once
    per test). Snapshotting four modules' __dict__ per module is a handful
    of cheap dict copies, negligible next to this suite's Postgres/browser
    fixtures, whether or not that module ever touches `demo.app` at all."""
    import github_client
    import llm_client
    import render_client
    import session_store

    modules = (session_store, render_client, github_client, llm_client)
    snapshots = [dict(vars(m)) for m in modules]
    yield
    for module, snapshot in zip(modules, snapshots):
        vars(module).clear()
        vars(module).update(snapshot)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-tag every Postgres-touching test with `db` (for `pytest -m "not
    db"` fast iteration) and `xdist_group(name="db")` (so pytest-xdist
    schedules them all onto the same worker, avoiding cross-worker TRUNCATE
    races against the one shared Postgres instance). See the 2026-08-19
    test-suite-performance design doc, section 3c, for why this is keyed off
    db_url specifically.

    `tryfirst=True` is load-bearing, not decoration. `--dist=loadgroup` never
    reads the `xdist_group` marker: pytest-xdist's worker-side
    `WorkerInteractor.pytest_collection_modifyitems` stamps an `@<group>`
    suffix onto `item._nodeid`, and that nodeid *string* is the only thing the
    scheduler groups on. That stamping hookimpl is undecorated, so pluggy
    orders it by registration LIFO -- and this file is an *initial* conftest
    (loaded as an initial conftest because `tests/` is the sole `testpaths`
    entry) registered before `WorkerInteractor`, so
    without `tryfirst` xdist stamps first, while no item carries the marker
    yet, and every db test ends up its own singleton group spread across every
    worker (each spinning its own testcontainers Postgres). The failure is
    silent -- all tests still pass and `-m db` still selects correctly, since
    marker selection is evaluated after both hooks have run.
    `tests/test_xdist_group_ordering.py` is the regression guard."""
    for item in items:
        if _touches_shared_postgres(item):
            item.add_marker(pytest.mark.db)
            item.add_marker(pytest.mark.xdist_group(name="db"))
        if _uses_real_browser(item):
            item.add_marker(pytest.mark.browser)
