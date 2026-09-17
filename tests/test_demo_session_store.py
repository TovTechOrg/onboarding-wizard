import inspect

import session_store as real_store
from demo import session_store as demo_store


def _public(module) -> set[str]:
    return {
        name for name, obj in vars(module).items()
        if not name.startswith("_") and inspect.isfunction(obj)
        and obj.__module__ == module.__name__
    }


def test_mock_covers_the_real_session_store_surface():
    missing = _public(real_store) - _public(demo_store)
    assert not missing, f"demo/session_store.py is missing: {sorted(missing)}"


def test_create_then_read_round_trips_a_frame():
    demo_store.reset()
    sid = demo_store.create_session()
    assert demo_store.update_frame(sid, "render", {"api_key": "x"}) is None
    assert demo_store.read_frame(sid, "render") == {"api_key": "x"}


def test_update_frame_shallow_merges_unless_replacing():
    demo_store.reset()
    sid = demo_store.create_session()
    demo_store.update_frame(sid, "render", {"api_key": "x"})
    demo_store.update_frame(sid, "render", {"service_id": "s"})
    assert demo_store.read_frame(sid, "render") == {"api_key": "x", "service_id": "s"}

    demo_store.update_frame(sid, "render", {"api_key": "y"}, replace=True)
    assert demo_store.read_frame(sid, "render") == {"api_key": "y"}


def test_unknown_session_fails_closed_and_never_upserts():
    demo_store.reset()
    result = demo_store.update_frame("no-such-session", "render", {"a": 1})
    assert result.__class__.__name__ == "SessionNotFound"
    assert demo_store.get_session("no-such-session") is None


def test_collapsed_frames_are_seeded_complete():
    """The trimmed flow keeps 4 frames; the other 4 must already be done, so
    the wizard's own frame machinery advances past them with no client hack.

    Backend frame keys, confirmed against router.py's GET /api/session
    handler -- NOT 1:1 with the wizard's UI frame ids. Notably
    "uptime_pinger" is the real key (the brief's original draft guessed
    "uptimerobot", which router.py never reads)."""
    demo_store.reset()
    sid = demo_store.create_session()
    session = demo_store.get_session(sid)
    for backend_key in ("dashboard_auth", "supabase", "uptime_pinger"):
        assert backend_key in session.frames, f"{backend_key} should be pre-completed"


def test_seeded_frames_satisfy_router_completeness_checks():
    """Not just present -- present with the exact fields router.py's
    GET /api/session checks for "complete", per-frame:
    - dashboard_auth: any truthy dict (router.py:536 `if data.get(...)`)
    - supabase: must contain "database_url" (router.py:543), not just "ref"
      (a bare "ref" is the in-between "provisioning" state, not complete)
    - uptime_pinger: any truthy dict (router.py:578 `if data.get(...)`)
    """
    demo_store.reset()
    sid = demo_store.create_session()
    session = demo_store.get_session(sid)
    assert session.frames["dashboard_auth"]
    assert "database_url" in session.frames["supabase"]
    assert session.frames["uptime_pinger"]


def test_sweep_evicts_only_expired_sessions():
    demo_store.reset()
    sid = demo_store.create_session()
    assert demo_store.sweep(age_seconds=0) == 1
    assert demo_store.get_session(sid) is None
