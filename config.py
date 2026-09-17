"""This service's own Settings."""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # No public_base_url here, deliberately: the page derives its own base
    # from location.origin, which the browser already knows exactly and which
    # is by definition the origin GitHub and Supabase redirect back to. A
    # hand-set env var was a second source of truth for the same fact, and the
    # two drifted — a difference of one trailing slash broke the Supabase
    # OAuth leg outright (see ISSUES.md). Do not reintroduce it without a use
    # the browser genuinely cannot serve itself.

    # No operator-level Supabase secret either: the Supabase frame's
    # credential is a visitor-pasted Personal Access Token (2026-09-04
    # redesign — see
    # docs/superpowers/specs/2026-09-04-supabase-pat-frame-design.md), not an
    # operator-registered OAuth app. This service currently holds no
    # operator-level third-party secret at all.

    # The wizard's own dedicated Postgres (never the sibling review-engine
    # project's own queue DB, never a visitor's provisioned project) backing
    # session_store.py. See
    # docs/superpowers/specs/2026-09-01-onboarding-server-side-session-design.md.
    database_url: str = ""

    # A Fernet key encrypting every credential value session_store.py
    # writes. Only whitespace-normalized here, deliberately NOT format-
    # validated via a pydantic field_validator: pydantic's ValidationError
    # embeds the rejected input_value verbatim in its own __str__ output
    # regardless of the validator's own error message (CLAUDE.md's secret-
    # handling section documents exactly this failure mode) -- fine for a
    # non-secret settings field, but this field is a secret, so raising a
    # pydantic-level ValidationError here would leak it into whatever
    # prints/logs Settings() construction failures. Format validity is
    # checked instead in main.py's lifespan, which raises a plain
    # RuntimeError with a clean, hand-written message.
    onboarding_session_encryption_key: str = ""

    # Where the demo wizard's "Finish & Deploy" step sends the reader: the
    # already-deployed bot demo. Non-secret, sourced from the real process
    # environment only (like every field above -- this class deliberately
    # never reads a dotenv file itself; see .env.config.example for why a
    # non-secret file still exists for this one). Previously a bespoke
    # `os.environ.get("DEMO_BOT_URL", ...)` in demo/content.py rather than a
    # proper Settings field -- moved here so the sibling review-engine
    # project's own config.py isn't the only one of the two with a real
    # settings mechanism for its own copy of this same URL.
    demo_bot_url: str = "https://demo-pr-review-bot.onrender.com"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return value.strip()

    @field_validator("onboarding_session_encryption_key")
    @classmethod
    def _normalize_session_encryption_key(cls, value: str) -> str:
        return value.strip()


settings = Settings()
