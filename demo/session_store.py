"""In-memory stand-in for session_store.py.

Sync, like the real one -- router.py's wrappers still run these through
asyncio.to_thread, so keeping them sync preserves that contract exactly.
No encryption: there is no real credential in this service's demo, so
Fernet would be ceremony over placeholder strings.

A public link must not grow memory without bound, so sessions are swept.
"""

from __future__ import annotations

import secrets
import time

from session_store import SESSION_TTL, SessionData, SessionNotFound  # noqa: F401

# Frames the trimmed demo collapses. Seeding them complete at session
# creation means GET /api/session reports them done, restoreFromSession()
# completes them through the real machinery, and the chain advances to the
# next visible frame with no client-side hack.
#
# Keys and fields below are confirmed against router.py's GET /api/session
# handler (not guessed) -- backend frame keys are NOT 1:1 with the wizard's
# UI frame ids:
#   - "dashboard_auth": router.py:536 treats any truthy dict as complete,
#     but the real fields (username/password/session_secret) are seeded
#     anyway since bulk-push-env-vars (router.py:1087-1091) reads them.
#   - "supabase": router.py:543 requires "database_url" present to report
#     complete -- a bare "ref" (router.py:545) is only the in-between
#     "provisioning" state, not done. "ref"/"name" are included too since
#     later reads (project-status/connection-info request handlers) key
#     off them.
#
# "uptime_pinger" is deliberately NOT preseeded here even though it's one of
# COLLAPSED_FRAMES too (demo/static/demo.js) -- unlike dashboard_auth/
# supabase, which both sit BEFORE llm-provider in static/index.html's
# FRAME_ORDER, uptime-pinger sits AFTER it. Preseeding it done from session
# creation used to make restoreFromSession() call completeFrame("uptime-
# pinger", ...) at page load, whose own nextFrame() cascade unconditionally
# unlocks "render-deploy" next -- with no check of render-deploy's real
# prereqs (which include llm-provider). Found 2026-09-17: clicking Deploy
# while "3. LLM provider" still read "Not started" pushed no LLM credential,
# seeded nothing into slot_config, and still reported the deploy "Live".
# demo/static/demo.js's autoCreateUptimeMonitor() now drives uptime-pinger's
# own real frame (same "reuse the real machinery" pattern as
# autoCreateRenderService() there) exactly when llm-provider unlocks it,
# so render-deploy only ever unlocks once llm-provider is genuinely done.
# router.py's bulk_push_render_env_vars refuses server-side too now, as a
# backstop for any other way this client-side gate could be bypassed.
_PRESEEDED_FRAMES: dict[str, dict] = {
    "dashboard_auth": {
        "username": "demo",
        "password": "demo-password",
        "session_secret": "demo-session-secret",
    },
    "supabase": {
        "ref": "demo-ref",
        "name": "demo-project",
        "database_url": "postgresql://demo:demo@localhost:5432/demo",
    },
}

_sessions: dict[str, dict[str, dict]] = {}
_created_at: dict[str, float] = {}


def reset() -> None:
    _sessions.clear()
    _created_at.clear()


def init_pool() -> None:
    return None


def close_pool() -> None:
    return None


def create_session() -> str:
    """Sweeps expired sessions before inserting -- mirrors the real
    session_store.create_session()'s own "sweeps expired rows before
    inserting" behavior (see its docstring), so a public, unbounded-uptime
    demo process doesn't grow its in-memory `_sessions` dict forever (see
    this module's own docstring, "swept" note). Found unwired during the
    2026-09-17 final-review pass (I3): `sweep()` existed and was tested in
    isolation but nothing outside its own test ever called it."""
    sweep()
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {k: dict(v) for k, v in _PRESEEDED_FRAMES.items()}
    _created_at[session_id] = time.monotonic()
    return session_id


def adopt_session(session_id: str) -> None:
    """Demo-only: create a session under a caller-chosen id.

    create_session() mints its own id and is the only such path in the real
    store; the cookie-hostile fallback needs a *known* id, so this is a
    separate, clearly-named function rather than a widened create_session().

    Does NOT sweep first, unlike create_session() above -- this is called
    from _cookieless_visitors_share_one_session on (almost) every cookie-
    blocked request to lazily re-adopt the one shared session id if it's
    ever missing (e.g. right after an expiry sweep elsewhere deleted it),
    so sweeping here would just immediately undo whatever eviction made
    this call necessary in the first place for any session younger than
    itself, and would run this check far more often than create_session()'s
    own one-sweep-per-new-session cadence needs.
    """
    _sessions[session_id] = {k: dict(v) for k, v in _PRESEEDED_FRAMES.items()}
    _created_at[session_id] = time.monotonic()


def get_session(session_id: str) -> SessionData | None:
    frames = _sessions.get(session_id)
    if frames is None:
        return None
    return SessionData(frames={k: dict(v) for k, v in frames.items()})


def update_frame(
    session_id: str, frame: str, data: dict, *, replace: bool = False
) -> SessionNotFound | None:
    """Fails closed exactly like the real one: never upserts an unknown id."""
    frames = _sessions.get(session_id)
    if frames is None:
        return SessionNotFound()
    if replace:
        frames[frame] = dict(data)
    else:
        frames.setdefault(frame, {}).update(data)
    return None


def read_frame(session_id: str, frame: str) -> dict | None:
    frames = _sessions.get(session_id)
    if frames is None:
        return None
    stored = frames.get(frame)
    return dict(stored) if stored is not None else None


def delete_session(session_id: str) -> None:
    _sessions.pop(session_id, None)
    _created_at.pop(session_id, None)


def sweep(age_seconds: float | None = None) -> int:
    """Demo-only: drop sessions older than the TTL (or an explicit age)."""
    limit = SESSION_TTL.total_seconds() if age_seconds is None else age_seconds
    now = time.monotonic()
    stale = [sid for sid, born in _created_at.items() if now - born >= limit]
    for sid in stale:
        delete_session(sid)
    return len(stale)
