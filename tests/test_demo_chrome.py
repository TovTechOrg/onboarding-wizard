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
