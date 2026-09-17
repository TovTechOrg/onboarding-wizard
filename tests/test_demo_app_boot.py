import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def demo_env(monkeypatch):
    database_url = "postgresql://demo/demo"
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", encryption_key)
    # config.settings is a module-level singleton built once, on whichever
    # import of config.py happens first in this worker process -- in the
    # full suite that's usually during collection (several other test
    # modules `import main`/`import config` at module scope, well before
    # this fixture's setenv calls ever run), so a plain setenv alone can
    # land after the singleton already froze both fields empty. Patching
    # the already-constructed instance directly (same shape as
    # tests/conftest.py's own `db` fixture) makes this fixture's effect
    # independent of import order.
    import config

    monkeypatch.setattr(config.settings, "database_url", database_url)
    monkeypatch.setattr(config.settings, "onboarding_session_encryption_key", encryption_key)


def test_install_mocks_rebinds_every_client(demo_env):
    from demo import app as demo_app

    demo_app.install_mocks()

    import github_client
    import llm_client
    import render_client
    import session_store
    import supabase_client
    import uptimerobot_client

    assert render_client.validate_key.__module__ == "demo.render_client"
    assert github_client.validate_app.__module__ == "demo.github_client"
    assert llm_client.list_groq_models.__module__ == "demo.llm_client"
    assert session_store.create_session.__module__ == "demo.session_store"
    # Added 2026-09-17 (final-review C3 fix) -- the two clients the demo's
    # trimmed UI never drives from the browser but router.py can still
    # reach directly (e.g. "Change render-key"'s uptime-monitor cleanup
    # call).
    assert supabase_client.validate_key.__module__ == "demo.supabase_client"
    assert uptimerobot_client.create_or_reuse_monitor.__module__ == (
        "demo.uptimerobot_client"
    )


def test_install_mocks_rebinds_seed_provider_config(demo_env):
    """Final-review C1 fix: router.py's own `_seed_provider_config` opens a
    REAL psycopg connection to the visitor's session-stored
    `supabase["database_url"]`, which demo/session_store.py pre-seeds as a
    fake, unreachable Postgres URL. Left unmocked, every "Finish & Deploy"
    attempt in the demo failed with `slot_config_seed_failed` before ever
    triggering the (also mocked) Render deploy -- a reader who completed
    all 4 demo frames hit a permanent dead end on the very last step."""
    from demo import app as demo_app

    demo_app.install_mocks()

    import router

    assert router._seed_provider_config.__module__ == "demo.app"
    assert router._seed_provider_config(
        "postgresql://demo:demo@localhost:5432/demo", "groq", "some-model", None, None
    ) is True


def _client_modules_router_calls() -> set[str]:
    """Every `<module>.<name>(` call site in router.py naming a top-level
    client-shaped module -- used by the parity test below to catch the
    exact root cause of the C3 finding: a client module router.py actually
    calls at runtime with nothing in demo/app.py's `_PAIRS` mocking it."""
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "router.py"
    text = source.read_text(encoding="utf-8")
    candidates = {
        "session_store", "render_client", "github_client", "llm_client",
        "supabase_client", "uptimerobot_client",
    }
    used = set()
    for name in candidates:
        if re.search(rf"\b{name}\.\w+\(", text):
            used.add(name)
    return used


def test_every_client_module_router_calls_is_in_pairs():
    """The C3 root cause, generalized (I1): a module router.py actually
    calls (by name, at runtime) must have a demo-side mock rebinding it via
    `_PAIRS` -- this is what would have caught uptimerobot_client/
    supabase_client being unmocked before a reviewer had to reproduce it
    live in a browser."""
    from demo import app as demo_app

    paired_real_module_names = {real.__name__ for real, _demo in demo_app._PAIRS}
    called = _client_modules_router_calls()
    missing = called - paired_real_module_names
    assert not missing, f"router.py calls these modules with no demo mock: {missing}"


def test_demo_reuses_mains_app_so_422_scrubbing_survives(demo_env):
    """A demo with its own FastAPI() loses main.py's RequestValidationError
    handler, which is what stops a submitted credential being echoed in a 422."""
    from fastapi.exceptions import RequestValidationError

    import main
    from demo.app import app

    assert app is main.app
    assert RequestValidationError in app.exception_handlers


async def test_app_boots_with_no_database(demo_env):
    from httpx import ASGITransport, AsyncClient

    from demo.app import app

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
    assert response.status_code == 200
