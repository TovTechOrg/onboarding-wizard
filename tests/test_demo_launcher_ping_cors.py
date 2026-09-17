"""The launcher lives on GitHub Pages and must be able to READ the launcher
ping endpoint (config.py's demo_launcher_ping_path, NOT "/healthz" -- see
that field's own comment for why) cross-origin to know when this service has
finished waking."""

from __future__ import annotations

import httpx
import pytest
from cryptography.fernet import Fernet

from config import settings
from demo import content

LAUNCHER_ORIGIN = "https://tovtechorg.github.io"
PING_PATH = settings.demo_launcher_ping_path


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo/demo")
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from demo.app import install_mocks

    install_mocks()


async def test_launcher_ping_is_readable_from_the_launcher_origin(demo_env):
    from demo.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(PING_PATH)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == LAUNCHER_ORIGIN


async def test_healthz_itself_does_not_get_the_cors_header(demo_env):
    """Regression guard for the 2026-09-17 rename: the CORS allowance moved
    to demo_launcher_ping_path, it did not additionally grow to cover
    "/healthz" too -- least surface exposed, matching the middleware's own
    comment."""
    from demo.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_no_other_route_becomes_readable_cross_origin(demo_env):
    from demo.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
    assert "access-control-allow-origin" not in response.headers


def test_the_handoff_points_at_the_service_that_actually_exists():
    """Deployed 2026-09-17 as demo-pr-review-bot, not demo-pr-review-engine.
    A reader who finishes all four steps lands here."""
    assert content.DEMO_BOT_URL == "https://demo-pr-review-bot.onrender.com"
