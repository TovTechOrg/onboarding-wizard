import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo/demo")
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def test_demo_script_is_served_and_injected(demo_env):
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")
        asset = await client.get("/static/demo.js")

    assert asset.status_code == 200
    assert '<script src="/static/demo.js"' in page.text


async def test_the_deploy_poll_interval_is_shortened_in_the_served_page(demo_env):
    """2026-09-17: static/index.html's own RENDER_DEPLOY_POLL_INTERVAL_MS
    (10000ms) is a real Render deploy's own sane check cadence, shared
    byte-for-byte with the real wizard -- a reader shouldn't wait on that
    cadence for a mock that resolves instantly, so demo/app.py rewrites
    this one literal in the served page only. The checked-in file itself
    must stay untouched (see test_the_real_pages_fetch_calls_are_untouched's
    own docstring for why that constraint matters)."""
    from pathlib import Path

    from demo.app import app
    from demo.content import DEMO_RENDER_DEPLOY_POLL_INTERVAL_MS

    source = (Path(__file__).parent.parent / "static" / "index.html").read_text()
    assert "const RENDER_DEPLOY_POLL_INTERVAL_MS = 10000;" in source

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")

    assert (
        f"const RENDER_DEPLOY_POLL_INTERVAL_MS = {DEMO_RENDER_DEPLOY_POLL_INTERVAL_MS};"
        in page.text
    )
    assert "const RENDER_DEPLOY_POLL_INTERVAL_MS = 10000;" not in page.text


async def test_the_real_pages_fetch_calls_are_untouched(demo_env):
    """tests/test_onboarding_page.py pins one fetch per credential endpoint.
    The demo trims the flow by mocking the backend, never by editing fetches."""
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")

    for endpoint in ("/api/render/validate-key", "/api/github/validate-app",
                     "/api/render/trigger-deploy"):
        assert page.text.count(f'fetch("{endpoint}"') == 1


def test_demo_js_hides_exactly_the_collapsed_frames():
    from pathlib import Path

    source = (Path(__file__).parent.parent / "demo" / "static" / "demo.js").read_text()
    for frame_id in ("render-service", "dashboard-auth", "supabase", "uptime-pinger"):
        assert frame_id in source
    for frame_id in ("render-key", "github-app", "llm-provider", "render-deploy"):
        assert f'"{frame_id}"' not in source.split("COLLAPSED_FRAMES")[1].split("]")[0]
