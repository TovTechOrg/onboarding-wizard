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

    assert render_client.validate_key.__module__ == "demo.render_client"
    assert github_client.validate_app.__module__ == "demo.github_client"
    assert llm_client.list_groq_models.__module__ == "demo.llm_client"
    assert session_store.create_session.__module__ == "demo.session_store"


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
