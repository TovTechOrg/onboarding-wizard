"""Tests for config.py's Settings: no public_base_url field exists (the
page derives its own base from location.origin instead — see CLAUDE.md),
and every field (database_url, onboarding_session_encryption_key,
demo_bot_url) reads from the real process environment only (this service
doesn't share the sibling review-engine project's config files)."""

from __future__ import annotations

from config import Settings


def test_no_public_base_url_setting_exists():
    """The page derives its base from location.origin; a hand-set env var was
    a second source of truth for the same fact and the two drifted (see
    ISSUES.md). Reintroducing the field would quietly reintroduce the drift."""
    assert "public_base_url" not in Settings.model_fields


def test_database_url_defaults_to_empty_string(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert Settings().database_url == ""


def test_database_url_strips_whitespace(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "  postgresql://x  \n")
    assert Settings().database_url == "postgresql://x"


def test_session_encryption_key_defaults_to_empty_string(monkeypatch):
    monkeypatch.delenv("ONBOARDING_SESSION_ENCRYPTION_KEY", raising=False)
    assert Settings().onboarding_session_encryption_key == ""


def test_session_encryption_key_reads_from_environment_unvalidated(monkeypatch):
    """Format validity (a real Fernet key or not) is deliberately NOT
    checked here -- see config.py's field docstring for why a pydantic-level
    ValidationError would leak this secret's raw value. That check lives in
    main.py's lifespan instead (test_onboarding_main.py)."""
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", "not-a-fernet-key")
    assert Settings().onboarding_session_encryption_key == "not-a-fernet-key"


def test_session_encryption_key_whitespace_only_value_normalizes_to_the_unset_sentinel(monkeypatch):
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", "   ")
    assert Settings().onboarding_session_encryption_key == ""


def test_demo_bot_url_defaults_to_the_live_demo_host(monkeypatch):
    monkeypatch.delenv("DEMO_BOT_URL", raising=False)
    assert Settings().demo_bot_url == "https://demo-pr-review-bot.onrender.com"


def test_demo_bot_url_reads_an_env_override(monkeypatch):
    monkeypatch.setenv("DEMO_BOT_URL", "https://demo-pr-review-bot-fork.onrender.com")
    assert Settings().demo_bot_url == "https://demo-pr-review-bot-fork.onrender.com"


def test_demo_launcher_ping_path_defaults_and_is_not_healthz(monkeypatch):
    monkeypatch.delenv("DEMO_LAUNCHER_PING_PATH", raising=False)
    assert Settings().demo_launcher_ping_path == "/api/demo/ping-7f3a2"
    assert Settings().demo_launcher_ping_path != "/healthz"


def test_demo_launcher_ping_path_reads_an_env_override(monkeypatch):
    monkeypatch.setenv("DEMO_LAUNCHER_PING_PATH", "/api/demo/ping-rotated")
    assert Settings().demo_launcher_ping_path == "/api/demo/ping-rotated"
