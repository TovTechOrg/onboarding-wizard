"""Tests for router.py — the JSON contract for
POST /api/render/validate-key never echoes the submitted key, and GET /
serves the wizard page. See design doc section 5."""

from __future__ import annotations

import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient

import github_client
import llm_client
import render_client
import router
import session_store
import supabase_client
import uptimerobot_client
from main import app

CONTRACT = json.loads(
    (Path(__file__).resolve().parent.parent / "contracts/provisioning.json").read_text(
        encoding="utf-8"
    )
)

SENTINEL_KEY = "rnd_SENTINEL_DO_NOT_LOG_9f3a"
# PEM-shaped so a leak would be unmistakable in a diff or a response body.
SENTINEL_PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----SENTINEL_DO_NOT_ECHO_4c1b-----END RSA PRIVATE KEY-----"
)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class _FakeSessionStore:
    """An in-memory stand-in for session_store.py's public functions, used
    via `_use_fake_session_store(monkeypatch)` below. Mirrors the real
    module's contract (update_frame merges and fails closed against a
    missing session id; create_session is the only way to mint one) without
    touching Postgres -- session_store.py's own tests (against a real test
    Postgres) are what verify the real implementation actually behaves this
    way."""

    def __init__(self):
        self._sessions: dict[str, dict[str, dict]] = {}
        self._next_id = 0

    def create_session(self) -> str:
        self._next_id += 1
        session_id = f"fake-session-{self._next_id}"
        self._sessions[session_id] = {}
        return session_id

    def get_session(self, session_id: str):
        frames = self._sessions.get(session_id)
        if frames is None:
            return None
        return session_store.SessionData(frames={k: dict(v) for k, v in frames.items()})

    def update_frame(self, session_id: str, frame: str, data: dict, *, replace: bool = False):
        if session_id not in self._sessions:
            return session_store.SessionNotFound()
        existing = {} if replace else self._sessions[session_id].get(frame, {})
        self._sessions[session_id][frame] = {**existing, **data}
        return None

    def read_frame(self, session_id: str, frame: str):
        frames = self._sessions.get(session_id)
        if frames is None:
            return None
        return frames.get(frame)

    def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


def _use_fake_session_store(monkeypatch) -> _FakeSessionStore:
    fake = _FakeSessionStore()
    monkeypatch.setattr(session_store, "create_session", fake.create_session)
    monkeypatch.setattr(session_store, "get_session", fake.get_session)
    monkeypatch.setattr(session_store, "update_frame", fake.update_frame)
    monkeypatch.setattr(session_store, "read_frame", fake.read_frame)
    monkeypatch.setattr(session_store, "delete_session", fake.delete_session)
    return fake


async def test_index_serves_html():
    client = await _client()
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


async def test_index_sets_security_headers():
    """This page's whole purpose is collecting a visitor's Render API key;
    without these headers any site could iframe it for a clickjacking or
    credential-phishing overlay."""
    client = await _client()
    resp = await client.get("/")
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


async def test_valid_key_returns_owner_name(monkeypatch):
    _use_fake_session_store(monkeypatch)

    async def fake_validate_key(api_key: str):
        assert api_key == SENTINEL_KEY
        return render_client.RenderKeyValid(owner_name="Ada Lovelace")

    monkeypatch.setattr(render_client, "validate_key", fake_validate_key)
    client = await _client()
    resp = await client.post("/api/render/validate-key", json={"api_key": SENTINEL_KEY})
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "owner_name": "Ada Lovelace"}


async def test_valid_key_creates_a_session_and_sets_the_cookie(monkeypatch):
    _use_fake_session_store(monkeypatch)

    async def fake_validate_key(api_key: str):
        return render_client.RenderKeyValid(owner_name="Ada Lovelace")

    monkeypatch.setattr(render_client, "validate_key", fake_validate_key)
    client = await _client()
    resp = await client.post("/api/render/validate-key", json={"api_key": SENTINEL_KEY})
    assert "onboarding_session=" in resp.headers.get("set-cookie", "")


async def test_valid_key_reports_failure_if_the_session_write_is_lost(monkeypatch):
    """An unpersisted "success" isn't real (design spec section 3.6) -- if
    update_frame can't find the session it was just told exists (a race
    with e.g. a concurrent reset), the endpoint must not still claim
    success even though the external Render check passed."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    monkeypatch.setattr(
        session_store, "update_frame", lambda *a, **k: session_store.SessionNotFound()
    )

    async def fake_validate_key(api_key: str):
        return render_client.RenderKeyValid(owner_name="Ada Lovelace")

    monkeypatch.setattr(render_client, "validate_key", fake_validate_key)
    client = await _client()
    resp = await client.post(
        "/api/render/validate-key", json={"api_key": SENTINEL_KEY},
        cookies={"onboarding_session": session_id},
    )
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_valid_key_reuses_an_existing_session_cookie(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()

    async def fake_validate_key(api_key: str):
        return render_client.RenderKeyValid(owner_name="Ada Lovelace")

    monkeypatch.setattr(render_client, "validate_key", fake_validate_key)
    client = await _client()
    resp = await client.post(
        "/api/render/validate-key", json={"api_key": SENTINEL_KEY},
        cookies={"onboarding_session": session_id},
    )
    assert "set-cookie" not in resp.headers
    assert fake.read_frame(session_id, "render")["owner_name"] == "Ada Lovelace"


async def test_valid_key_resubmission_discards_the_previous_render_services_data(monkeypatch):
    """A "Change"-triggered resubmission of the Render key must not leave a
    previous service_id/service_url behind -- it may belong to a different
    Render account under the old key."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "render",
        {"api_key": "rnd_old", "owner_name": "Old Owner", "service_id": "srv-old", "service_url": "https://old.onrender.com"},
    )

    async def fake_validate_key(api_key: str):
        return render_client.RenderKeyValid(owner_name="New Owner")

    monkeypatch.setattr(render_client, "validate_key", fake_validate_key)
    client = await _client()
    await client.post(
        "/api/render/validate-key", json={"api_key": "rnd_new"},
        cookies={"onboarding_session": session_id},
    )
    assert fake.read_frame(session_id, "render") == {
        "api_key": "rnd_new", "owner_name": "New Owner",
    }


async def test_invalid_key_reports_the_reason(monkeypatch):
    async def fake_validate_key(api_key: str):
        return render_client.RenderKeyInvalid(reason="invalid_key")

    monkeypatch.setattr(render_client, "validate_key", fake_validate_key)
    client = await _client()
    resp = await client.post("/api/render/validate-key", json={"api_key": SENTINEL_KEY})
    assert resp.status_code == 200
    assert resp.json() == {"valid": False, "reason": "invalid_key"}


async def test_unreachable_reports_the_reason(monkeypatch):
    async def fake_validate_key(api_key: str):
        return render_client.RenderKeyInvalid(reason="render_unreachable")

    monkeypatch.setattr(render_client, "validate_key", fake_validate_key)
    client = await _client()
    resp = await client.post("/api/render/validate-key", json={"api_key": SENTINEL_KEY})
    assert resp.status_code == 200
    assert resp.json() == {"valid": False, "reason": "render_unreachable"}


async def test_response_never_echoes_the_submitted_key(monkeypatch):
    async def fake_validate_key(api_key: str):
        return render_client.RenderKeyInvalid(reason="invalid_key")

    monkeypatch.setattr(render_client, "validate_key", fake_validate_key)
    client = await _client()
    resp = await client.post("/api/render/validate-key", json={"api_key": SENTINEL_KEY})
    assert SENTINEL_KEY not in resp.text


async def test_validation_error_never_echoes_the_submitted_key():
    """Verify that malformed requests (e.g., wrong field name) never echo
    the credential in the 422 validation error response."""
    client = await _client()
    # Send request with wrong field name (typo) to trigger validation error
    resp = await client.post("/api/render/validate-key", json={"key": SENTINEL_KEY})
    assert resp.status_code == 422
    # Credential must not appear in response text
    assert SENTINEL_KEY not in resp.text
    # Response must use generic handler (no "input" field from FastAPI's default)
    assert "input" not in resp.text


async def test_index_derives_its_base_url_in_the_browser():
    """No __ONBOARDING_BASE_URL__ token and no templated value: the page reads
    location.origin, which the browser already knows exactly. A hand-set env
    var was a second source of truth for the same fact and the two drifted --
    one trailing slash broke the Supabase OAuth leg (see ISSUES.md)."""
    client = await _client()
    resp = await client.get("/")
    assert "__ONBOARDING_BASE_URL__" not in resp.text
    assert "window.ONBOARDING_BASE_URL = location.origin;" in resp.text


async def test_validate_supabase_key_creates_no_session_by_itself(monkeypatch):
    """Unlike render's validate-key (the wizard's session entry point),
    Supabase's validate-key is frame 3 -- it requires an existing session
    and fails closed without one, same as every other non-entry-point
    endpoint."""
    client = await _client()
    resp = await client.post("/api/supabase/validate-key", json={"key": "sbp_a"})
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_validate_supabase_key_stores_the_key_and_returns_orgs(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()

    async def fake_validate(pat):
        assert pat == "sbp_SENTINEL"
        return supabase_client.SupabaseKeyValid(
            orgs=[supabase_client.SupabaseOrg(slug="org-one", name="Org One")]
        )

    monkeypatch.setattr(supabase_client, "validate_key", fake_validate)
    client = await _client()
    resp = await client.post(
        "/api/supabase/validate-key",
        json={"key": "sbp_SENTINEL"},
        cookies={"onboarding_session": session_id},
    )
    assert resp.json() == {"valid": True, "orgs": [{"slug": "org-one", "name": "Org One"}]}
    stored = fake.read_frame(session_id, "supabase")
    assert stored["api_key"] == "sbp_SENTINEL"


async def test_validate_supabase_key_discards_a_previous_projects_data(monkeypatch):
    """A resubmitted key (via "Change") must not leave the OLD project's
    ref/database_url behind -- GET /api/session's completeness check keys
    off database_url's mere presence, so a stale one would report the
    frame as already-done for the wrong project on reload."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "supabase",
        {
            "api_key": "old-key", "name": "old-proj", "ref": "x" * 20,
            "db_pass": "old-pass", "database_url": "postgresql://old",
        },
    )

    async def fake_validate(pat):
        return supabase_client.SupabaseKeyValid(orgs=[])

    monkeypatch.setattr(supabase_client, "validate_key", fake_validate)
    client = await _client()
    await client.post(
        "/api/supabase/validate-key",
        json={"key": "new-key"},
        cookies={"onboarding_session": session_id},
    )
    stored = fake.read_frame(session_id, "supabase")
    assert "ref" not in stored
    assert "database_url" not in stored
    assert stored["api_key"] == "new-key"


async def test_validate_supabase_key_reports_invalid_key(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()

    async def fake_validate(pat):
        return supabase_client.SupabaseKeyInvalid(reason="invalid_key")

    monkeypatch.setattr(supabase_client, "validate_key", fake_validate)
    client = await _client()
    resp = await client.post(
        "/api/supabase/validate-key",
        json={"key": "bad"},
        cookies={"onboarding_session": session_id},
    )
    assert resp.json() == {"valid": False, "reason": "invalid_key"}
    assert fake.read_frame(session_id, "supabase") is None


async def test_supabase_connect_endpoint_is_gone():
    client = await _client()
    resp = await client.post("/api/supabase/connect", json={"name": "x"})
    assert resp.status_code == 404


async def test_supabase_oauth_callback_route_is_gone():
    client = await _client()
    resp = await client.get("/oauth/supabase/callback?code=x&state=y")
    assert resp.status_code == 404


async def test_supabase_list_organizations_endpoint_is_gone():
    client = await _client()
    resp = await client.post("/api/supabase/list-organizations")
    assert resp.status_code == 404


async def test_create_project_generates_db_pass_server_side(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "supabase", {"api_key": "a"})
    captured = {}

    async def fake_create(access_token, organization_slug, name, db_pass):
        captured["args"] = (access_token, organization_slug, name, db_pass)
        return supabase_client.SupabaseProjectCreated(ref="x" * 20, status="INACTIVE")

    monkeypatch.setattr(supabase_client, "create_project", fake_create)
    client = await _client()
    resp = await client.post(
        "/api/supabase/create-project",
        json={"organization_slug": "org-one", "name": "pr-review-bot"},
        cookies={"onboarding_session": session_id},
    )
    body = resp.json()
    assert body == {"valid": True, "ref": "x" * 20, "status": "INACTIVE", "name": "pr-review-bot"}
    assert "db_pass" not in body
    access_token, organization_slug, name, db_pass = captured["args"]
    assert (access_token, organization_slug, name) == ("a", "org-one", "pr-review-bot")
    assert db_pass  # generated, never supplied by the client
    stored = fake.read_frame(session_id, "supabase")
    assert stored["ref"] == "x" * 20
    assert stored["organization_slug"] == "org-one"
    assert stored["db_pass"] == db_pass


async def test_create_project_relays_the_rejection_message(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "supabase", {"api_key": "a"})

    async def fake_create(access_token, organization_slug, name, db_pass):
        return supabase_client.SupabaseProjectRejected(
            message="This organization already has the maximum number of free projects."
        )

    monkeypatch.setattr(supabase_client, "create_project", fake_create)
    client = await _client()
    resp = await client.post(
        "/api/supabase/create-project",
        json={"organization_slug": "org-one", "name": "n"},
        cookies={"onboarding_session": session_id},
    )
    assert resp.json() == {
        "valid": False,
        "reason": "project_creation_rejected",
        "message": "This organization already has the maximum number of free projects.",
    }


async def test_create_project_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post(
        "/api/supabase/create-project", json={"organization_slug": "org-one", "name": "n"}
    )
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_project_status_reads_from_session(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "supabase", {"api_key": "a", "ref": "x" * 20})

    async def fake_status(access_token, ref):
        assert (access_token, ref) == ("a", "x" * 20)
        return supabase_client.SupabaseProjectStatus(status="ACTIVE_HEALTHY")

    monkeypatch.setattr(supabase_client, "get_project_status", fake_status)
    client = await _client()
    resp = await client.post(
        "/api/supabase/project-status", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": True, "status": "ACTIVE_HEALTHY"}


async def test_project_status_reports_failure_reason(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "supabase", {"api_key": "a", "ref": "x" * 20})

    async def fake_status(access_token, ref):
        return supabase_client.SupabaseApiFailed(reason="unauthorized")

    monkeypatch.setattr(supabase_client, "get_project_status", fake_status)
    client = await _client()
    resp = await client.post(
        "/api/supabase/project-status", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": False, "reason": "unauthorized"}


async def test_project_status_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post("/api/supabase/project-status")
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_connection_info_assembles_and_stores_the_database_url(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "supabase", {"api_key": "a", "ref": "x" * 20, "db_pass": "pw123"}
    )

    async def fake_info(access_token, ref, session_id):
        return supabase_client.SupabaseConnectionInfo(
            db_user="postgres.x",
            db_host="aws-0-us-east-1.pooler.supabase.com",
            db_port=5432,
            db_name="postgres",
        )

    monkeypatch.setattr(supabase_client, "get_connection_info", fake_info)
    client = await _client()
    resp = await client.post(
        "/api/supabase/connection-info", cookies={"onboarding_session": session_id}
    )
    body = resp.json()
    assert body == {"valid": True}
    assert "db_user" not in resp.text and "db_host" not in resp.text
    stored = fake.read_frame(session_id, "supabase")
    assert stored["database_url"] == (
        "postgresql://postgres.x:pw123@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    )


async def test_connection_info_reports_failure_reason(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "supabase", {"api_key": "a", "ref": "x" * 20, "db_pass": "pw"}
    )

    async def fake_info(access_token, ref, session_id):
        return supabase_client.SupabaseApiFailed(reason="pooler_config_unavailable")

    monkeypatch.setattr(supabase_client, "get_connection_info", fake_info)
    client = await _client()
    resp = await client.post(
        "/api/supabase/connection-info", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": False, "reason": "pooler_config_unavailable"}


async def test_connection_info_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post("/api/supabase/connection-info")
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_supabase_exchange_oauth_code_endpoint_is_gone():
    client = await _client()
    resp = await client.post("/api/supabase/exchange-oauth-code", json={})
    assert resp.status_code == 404


async def test_supabase_refresh_access_token_endpoint_is_gone():
    client = await _client()
    resp = await client.post("/api/supabase/refresh-access-token", json={})
    assert resp.status_code == 404


async def test_supabase_push_render_var_endpoint_is_gone():
    client = await _client()
    resp = await client.post("/api/supabase/push-render-var", json={})
    assert resp.status_code == 404


async def test_get_session_with_no_cookie_returns_empty_frames():
    client = await _client()
    resp = await client.get("/api/session")
    assert resp.status_code == 200
    assert resp.json() == {"frames": {}}


async def test_get_session_with_unknown_cookie_returns_empty_frames(monkeypatch):
    _use_fake_session_store(monkeypatch)
    client = await _client()
    resp = await client.get("/api/session", cookies={"onboarding_session": "bogus"})
    assert resp.json() == {"frames": {}}


async def test_get_session_reflects_render_key_display_but_never_the_credential(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": SENTINEL_KEY, "owner_name": "alice"})
    client = await _client()
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    body = resp.json()
    assert body == {
        "frames": {"render-key": {"complete": True, "display": {"owner_name": "alice"}}}
    }
    assert SENTINEL_KEY not in resp.text


async def test_get_session_reports_render_key_and_render_service_separately(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "owner_name": "alice"})
    client = await _client()
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    assert "render-service" not in resp.json()["frames"]

    fake.update_frame(session_id, "render", {"service_id": "srv-1", "service_url": "https://x.onrender.com"})
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    body = resp.json()["frames"]
    assert body["render-key"]["complete"] is True
    assert body["render-service"] == {
        "complete": True,
        "display": {"service_id": "srv-1", "service_url": "https://x.onrender.com"},
    }


async def test_get_session_reflects_vertex_project_and_location_but_never_for_other_providers(
    monkeypatch,
):
    """Non-secret configuration values (a project id, a region) -- the same
    class of field CLAUDE.md already permits relay responses to carry.
    Gemini/groq frames never carry these keys at all, so their display
    block must stay exactly the existing two fields."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "llm_provider",
        {
            "provider": "vertex",
            "credential_value": "b64",
            "model": "gemini-2.5-flash",
            "vertex_gcp_project": "chosen-proj",
            "vertex_gcp_location": "europe-west4",
        },
    )
    client = await _client()
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    assert resp.json()["frames"]["llm-provider"]["display"] == {
        "provider": "vertex",
        "model": "gemini-2.5-flash",
        "vertex_gcp_project": "chosen-proj",
        "vertex_gcp_location": "europe-west4",
    }

    fake.update_frame(
        session_id, "llm_provider",
        {"provider": "gemini", "credential_value": "AIza-x", "model": "gemini-flash-latest"},
        replace=True,
    )
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    assert resp.json()["frames"]["llm-provider"]["display"] == {
        "provider": "gemini",
        "model": "gemini-flash-latest",
    }


async def test_get_session_reports_supabase_provisioning_once_project_created(monkeypatch):
    """ref alone (project created) is NOT complete -- database_url is what
    the final deploy step actually needs, and that's written later by
    connection-info. A session with ref but no database_url must report a
    resumable "provisioning" state, not complete, so a reload mid-wait
    doesn't unlock the next frame with nothing for bulk-push to find."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "supabase", {"api_key": "tok", "name": "myproj", "ref": "x" * 20}
    )
    client = await _client()
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    assert resp.json()["frames"]["supabase"] == {
        "complete": False,
        "provisioning": True,
        "display": {"ref": "x" * 20, "name": "myproj"},
    }


async def test_get_session_reports_supabase_complete_once_database_url_present(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id,
        "supabase",
        {
            "api_key": "tok",
            "name": "myproj",
            "ref": "x" * 20,
            "database_url": "postgresql://u:p@h:5432/d",
        },
    )
    client = await _client()
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    assert resp.json()["frames"]["supabase"] == {"complete": True, "display": {"name": "myproj"}}


async def test_reset_session_deletes_the_row_and_clears_the_cookie(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    client = await _client()
    resp = await client.post("/api/session/reset", cookies={"onboarding_session": session_id})
    assert resp.status_code == 204
    assert fake.get_session(session_id) is None
    set_cookie = resp.headers.get("set-cookie", "")
    assert "onboarding_session=" in set_cookie
    assert 'Max-Age=0' in set_cookie or set_cookie.endswith('onboarding_session=""; Path=/')


async def test_reset_session_with_no_cookie_is_a_noop_204():
    client = await _client()
    resp = await client.post("/api/session/reset")
    assert resp.status_code == 204


async def test_index_csp_no_longer_needs_a_github_form_action():
    """No cross-origin form POST remains in this frame -- App creation is
    fully manual now."""
    client = await _client()
    resp = await client.get("/")
    csp = resp.headers["content-security-policy"]
    assert "form-action 'self';" in csp
    assert "github.com" not in csp


async def test_validate_app_returns_the_full_checklist(monkeypatch):
    async def fake_validate(app_id, private_key_b64, expected_webhook_url):
        assert (app_id, private_key_b64, expected_webhook_url) == (
            42, "cGVt", "https://my-service.onrender.com/webhook",
        )
        return github_client.AppValidated(
            permissions=[
                github_client.PermissionCheck(
                    name="contents", wanted="read", actual="read", ok=True
                ),
            ],
            events=[github_client.EventCheck(name="pull_request", ok=True)],
            installation=github_client.InstallationFound(
                installation_id=100, account_login="octocat", repo_scope="all"
            ),
            webhook=github_client.WebhookCheck(
                ok=True, actual_url="https://my-service.onrender.com/webhook"
            ),
        )

    monkeypatch.setattr(github_client, "validate_app", fake_validate)
    client = await _client()
    resp = await client.post(
        "/api/github/validate-app",
        json={
            "app_id": 42,
            "private_key_b64": "cGVt",
            "expected_webhook_url": "https://my-service.onrender.com/webhook",
            "webhook_secret": "s" * 20,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "valid": True,
        "all_ok": True,
        "permissions": [{"name": "contents", "wanted": "read", "actual": "read", "ok": True}],
        "events": [{"name": "pull_request", "ok": True}],
        "installation": {
            "status": "found", "installation_id": 100,
            "account_login": "octocat", "repo_scope": "all",
        },
        "webhook": {"ok": True, "actual_url": "https://my-service.onrender.com/webhook"},
    }


async def test_validate_app_persists_on_all_ok(monkeypatch):
    async def fake_validate(app_id, private_key_b64, expected_webhook_url):
        return github_client.AppValidated(
            permissions=[],
            events=[],
            installation=github_client.InstallationFound(
                installation_id=100, account_login="octocat", repo_scope="all"
            ),
            webhook=github_client.WebhookCheck(ok=True, actual_url="https://x.example/webhook"),
        )

    monkeypatch.setattr(github_client, "validate_app", fake_validate)
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    client = await _client()
    resp = await client.post(
        "/api/github/validate-app",
        json={
            "app_id": 42, "private_key_b64": "cGVt",
            "expected_webhook_url": "https://x.example/webhook",
            "webhook_secret": "s" * 20,
        },
        cookies={"onboarding_session": session_id},
    )
    assert resp.json()["all_ok"] is True
    assert fake.read_frame(session_id, "github_app") == {
        "app_id": 42, "private_key_b64": "cGVt",
        "webhook_secret": "s" * 20, "installation_id": 100,
    }


async def test_validate_app_does_not_persist_when_not_all_ok(monkeypatch):
    async def fake_validate(app_id, private_key_b64, expected_webhook_url):
        return github_client.AppValidated(
            permissions=[],
            events=[],
            installation=github_client.InstallationNotFound(),
            webhook=github_client.WebhookCheck(ok=False, actual_url=""),
        )

    monkeypatch.setattr(github_client, "validate_app", fake_validate)
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    client = await _client()
    await client.post(
        "/api/github/validate-app",
        json={
            "app_id": 42, "private_key_b64": "cGVt",
            "expected_webhook_url": "https://x.example/webhook",
            "webhook_secret": "s" * 20,
        },
        cookies={"onboarding_session": session_id},
    )
    assert fake.read_frame(session_id, "github_app") is None


async def test_validate_app_all_ok_is_false_when_anything_fails(monkeypatch):
    async def fake_validate(app_id, private_key_b64, expected_webhook_url):
        return github_client.AppValidated(
            permissions=[
                github_client.PermissionCheck(name="issues", wanted="write", actual=None, ok=False),
            ],
            events=[github_client.EventCheck(name="pull_request", ok=True)],
            installation=github_client.InstallationNotFound(),
            webhook=github_client.WebhookCheck(ok=False, actual_url=""),
        )

    monkeypatch.setattr(github_client, "validate_app", fake_validate)
    client = await _client()
    resp = await client.post(
        "/api/github/validate-app",
        json={
            "app_id": 42, "private_key_b64": "cGVt",
            "expected_webhook_url": "https://x.example/webhook",
            "webhook_secret": "s" * 20,
        },
    )
    body = resp.json()
    assert body["valid"] is True
    assert body["all_ok"] is False
    assert body["installation"] == {"status": "none"}


async def test_validate_app_reports_multiple_installations(monkeypatch):
    async def fake_validate(app_id, private_key_b64, expected_webhook_url):
        return github_client.AppValidated(
            permissions=[],
            events=[],
            installation=github_client.MultipleInstallationsFound(
                account_logins=["octocat", "monalisa"]
            ),
            webhook=github_client.WebhookCheck(ok=True, actual_url="https://x.example/webhook"),
        )

    monkeypatch.setattr(github_client, "validate_app", fake_validate)
    client = await _client()
    resp = await client.post(
        "/api/github/validate-app",
        json={
            "app_id": 42, "private_key_b64": "cGVt",
            "expected_webhook_url": "https://x.example/webhook",
            "webhook_secret": "s" * 20,
        },
    )
    body = resp.json()
    assert body["installation"] == {"status": "multiple", "account_logins": ["octocat", "monalisa"]}
    assert body["all_ok"] is False


async def test_validate_app_reports_credentials_failure_reason(monkeypatch):
    async def fake_validate(app_id, private_key_b64, expected_webhook_url):
        return github_client.AppCredentialsInvalid(reason="unauthorized")

    monkeypatch.setattr(github_client, "validate_app", fake_validate)
    client = await _client()
    resp = await client.post(
        "/api/github/validate-app",
        json={
            "app_id": 42, "private_key_b64": "cGVt",
            "expected_webhook_url": "https://x.example/webhook",
            "webhook_secret": "s" * 20,
        },
    )
    assert resp.json() == {"valid": False, "reason": "unauthorized"}


async def test_validate_app_rejects_a_non_positive_app_id():
    client = await _client()
    for bad in (0, -1):
        resp = await client.post(
            "/api/github/validate-app",
            json={
                "app_id": bad, "private_key_b64": "cGVt",
                "expected_webhook_url": "https://x.example/webhook",
                "webhook_secret": "s" * 20,
            },
        )
        assert resp.status_code == 422


async def test_validate_app_rejects_a_malformed_webhook_url():
    client = await _client()
    resp = await client.post(
        "/api/github/validate-app",
        json={
            "app_id": 42,
            "private_key_b64": "cGVt",
            "expected_webhook_url": "not-a-url",
            "webhook_secret": "s" * 20,
        },
    )
    assert resp.status_code == 422


async def test_validate_app_validation_error_never_echoes_the_private_key():
    """Same guard as every other endpoint carrying a private key: FastAPI's
    default 422 body echoes rejected input verbatim; only main.py's app-wide
    RequestValidationError handler stops that."""
    client = await _client()
    resp = await client.post(
        "/api/github/validate-app",
        json={
            "app_id": 42, "private_key": SENTINEL_PRIVATE_KEY,
            "expected_webhook_url": "https://x.example/webhook",
            "webhook_secret": "s" * 20,
        },
    )
    assert resp.status_code == 422
    assert SENTINEL_PRIVATE_KEY not in resp.text
    assert "SENTINEL_DO_NOT_ECHO" not in resp.text
    assert "input" not in resp.text


async def test_validate_app_response_never_echoes_the_private_key(monkeypatch):
    sentinel_key_b64 = "U0VOVElORUxfUFJJVkFURV9LRVk="

    async def fake_validate(app_id, private_key_b64, expected_webhook_url):
        return github_client.AppCredentialsInvalid(reason="invalid_key")

    monkeypatch.setattr(github_client, "validate_app", fake_validate)
    client = await _client()
    resp = await client.post(
        "/api/github/validate-app",
        json={
            "app_id": 42, "private_key_b64": sentinel_key_b64,
            "expected_webhook_url": "https://x.example/webhook",
            "webhook_secret": "s" * 20,
        },
    )
    assert sentinel_key_b64 not in resp.text


async def test_set_webhook_url_endpoint_is_gone():
    """Removed along with the placeholder-then-patch flow: the App is created
    already pointing at its real webhook URL. An endpoint that accepts an
    App private key is not something to leave mounted with no caller."""
    client = await _client()
    resp = await client.post(
        "/api/github/set-webhook-url",
        json={"app_id": 123, "private_key_b64": "cGVt", "url": "https://x.onrender.com/webhook"},
    )
    assert resp.status_code == 404


async def test_index_never_templates_the_supabase_oauth_client_id():
    """No operator-level Supabase secret exists at all anymore -- nothing
    to template."""
    client = await _client()
    resp = await client.get("/")
    assert "SUPABASE_OAUTH_CLIENT_ID" not in resp.text
    assert "__SUPABASE_OAUTH_CLIENT_ID__" not in resp.text


async def test_gemini_list_models_returns_models(monkeypatch):
    async def fake_list(api_key):
        assert api_key == "SENTINEL_KEY"
        return llm_client.LlmModelsListed(models=["gemini-flash-latest", "gemini-2.5-pro"])

    monkeypatch.setattr(llm_client, "list_gemini_models", fake_list)
    client = await _client()
    resp = await client.post("/api/llm/gemini/list-models", json={"api_key": "SENTINEL_KEY"})
    assert resp.json() == {"valid": True, "models": ["gemini-flash-latest", "gemini-2.5-pro"]}


async def test_gemini_list_models_reports_failure_reason(monkeypatch):
    async def fake_list(api_key):
        return llm_client.LlmApiFailed(reason="unauthorized")

    monkeypatch.setattr(llm_client, "list_gemini_models", fake_list)
    client = await _client()
    resp = await client.post("/api/llm/gemini/list-models", json={"api_key": "bad"})
    assert resp.json() == {"valid": False, "reason": "unauthorized"}


async def test_gemini_list_models_validation_error_never_echoes_the_key():
    sentinel_key = "SENTINEL_DO_NOT_ECHO_KEY"
    client = await _client()
    resp = await client.post("/api/llm/gemini/list-models", json={"api_key_typo": sentinel_key})
    assert resp.status_code == 422
    assert sentinel_key not in resp.text
    assert "input" not in resp.text


async def test_groq_list_models_returns_models(monkeypatch):
    async def fake_list(api_key):
        assert api_key == "SENTINEL_KEY"
        return llm_client.LlmModelsListed(models=["llama-3.3-70b-versatile"])

    monkeypatch.setattr(llm_client, "list_groq_models", fake_list)
    client = await _client()
    resp = await client.post("/api/llm/groq/list-models", json={"api_key": "SENTINEL_KEY"})
    assert resp.json() == {"valid": True, "models": ["llama-3.3-70b-versatile"]}


async def test_groq_list_models_reports_failure_reason(monkeypatch):
    async def fake_list(api_key):
        return llm_client.LlmApiFailed(reason="rate_limited")

    monkeypatch.setattr(llm_client, "list_groq_models", fake_list)
    client = await _client()
    resp = await client.post("/api/llm/groq/list-models", json={"api_key": "a"})
    assert resp.json() == {"valid": False, "reason": "rate_limited"}


async def test_groq_list_models_validation_error_never_echoes_the_key():
    sentinel_key = "SENTINEL_DO_NOT_ECHO_KEY"
    client = await _client()
    resp = await client.post("/api/llm/groq/list-models", json={"api_key_typo": sentinel_key})
    assert resp.status_code == 422
    assert sentinel_key not in resp.text
    assert "input" not in resp.text


async def test_vertex_list_models_returns_models_and_project_id(monkeypatch):
    async def fake_list(service_account_key_b64, project=None, location=None):
        assert service_account_key_b64 == "SENTINEL_B64"
        return llm_client.VertexModelsListed(
            project_id="sentinel-project", models=["gemini-2.5-flash"]
        )

    async def fake_list_projects(service_account_key_b64):
        return llm_client.VertexProjectsListed(projects=["sentinel-project"])

    monkeypatch.setattr(llm_client, "list_vertex_models", fake_list)
    monkeypatch.setattr(llm_client, "list_accessible_projects", fake_list_projects)
    client = await _client()
    resp = await client.post(
        "/api/llm/vertex/list-models", json={"service_account_key_b64": "SENTINEL_B64"}
    )
    assert resp.json() == {
        "valid": True,
        "project_id": "sentinel-project",
        "models": ["gemini-2.5-flash"],
        "projects": ["sentinel-project"],
        "default_project": "sentinel-project",
        "default_location": router._VERTEX_DEFAULT_LOCATION,
    }


async def test_vertex_list_models_reports_failure_reason(monkeypatch):
    async def fake_list(service_account_key_b64, project=None, location=None):
        return llm_client.LlmApiFailed(reason="invalid_service_account_json")

    monkeypatch.setattr(llm_client, "list_vertex_models", fake_list)
    client = await _client()
    resp = await client.post(
        "/api/llm/vertex/list-models", json={"service_account_key_b64": "not-json"}
    )
    assert resp.json() == {"valid": False, "reason": "invalid_service_account_json"}


async def test_vertex_list_models_validation_error_never_echoes_the_key():
    sentinel_key = "SENTINEL_DO_NOT_ECHO_SERVICE_ACCOUNT_KEY"
    client = await _client()
    resp = await client.post("/api/llm/vertex/list-models", json={"key_typo": sentinel_key})
    assert resp.status_code == 422
    assert sentinel_key not in resp.text
    assert "input" not in resp.text


# An empty credential must be a 422, not something that reaches the SDK:
# genai.Client(api_key="") falls back to reading the *server's* own
# GOOGLE_API_KEY/GEMINI_API_KEY env vars, so an empty submission could
# otherwise validate against the operator's credential instead of failing.


async def test_gemini_list_models_rejects_empty_key():
    client = await _client()
    resp = await client.post("/api/llm/gemini/list-models", json={"api_key": ""})
    assert resp.status_code == 422


async def test_groq_list_models_rejects_empty_key():
    client = await _client()
    resp = await client.post("/api/llm/groq/list-models", json={"api_key": ""})
    assert resp.status_code == 422


async def test_vertex_list_models_rejects_empty_key():
    client = await _client()
    resp = await client.post("/api/llm/vertex/list-models", json={"service_account_key_b64": ""})
    assert resp.status_code == 422


async def test_uptimerobot_monitor_created_reports_created_true(monkeypatch):
    async def fake_create(api_key, render_service_url):
        assert api_key == SENTINEL_KEY
        assert render_service_url == "https://sentinel-service.onrender.com"
        return uptimerobot_client.UptimeRobotMonitorResult(created=True, monitor_id=42)

    monkeypatch.setattr(uptimerobot_client, "create_or_reuse_monitor", fake_create)
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/create-monitor",
        json={
            "api_key": SENTINEL_KEY,
            "render_service_url": "https://sentinel-service.onrender.com",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "created": True, "monitor_id": 42}


async def test_uptimerobot_create_monitor_persists_to_session(monkeypatch):
    async def fake_create(api_key, render_service_url):
        return uptimerobot_client.UptimeRobotMonitorResult(created=True, monitor_id=99)

    monkeypatch.setattr(uptimerobot_client, "create_or_reuse_monitor", fake_create)
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    client = await _client()
    await client.post(
        "/api/uptimerobot/create-monitor",
        json={"api_key": SENTINEL_KEY, "render_service_url": "https://x.onrender.com"},
        cookies={"onboarding_session": session_id},
    )
    assert fake.read_frame(session_id, "uptime_pinger") == {
        "api_key": SENTINEL_KEY, "monitor_id": 99,
    }


async def test_uptimerobot_monitor_reused_reports_created_false(monkeypatch):
    async def fake_create(api_key, render_service_url):
        return uptimerobot_client.UptimeRobotMonitorResult(created=False, monitor_id=42)

    monkeypatch.setattr(uptimerobot_client, "create_or_reuse_monitor", fake_create)
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/create-monitor",
        json={
            "api_key": SENTINEL_KEY,
            "render_service_url": "https://sentinel-service.onrender.com",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "created": False, "monitor_id": 42}


async def test_uptimerobot_failure_reports_the_reason(monkeypatch):
    async def fake_create(api_key, render_service_url):
        return uptimerobot_client.UptimeRobotApiFailed(reason="unauthorized")

    monkeypatch.setattr(uptimerobot_client, "create_or_reuse_monitor", fake_create)
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/create-monitor",
        json={
            "api_key": SENTINEL_KEY,
            "render_service_url": "https://sentinel-service.onrender.com",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": False, "reason": "unauthorized"}


async def test_uptimerobot_response_never_echoes_the_submitted_key(monkeypatch):
    async def fake_create(api_key, render_service_url):
        return uptimerobot_client.UptimeRobotApiFailed(reason="unauthorized")

    monkeypatch.setattr(uptimerobot_client, "create_or_reuse_monitor", fake_create)
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/create-monitor",
        json={
            "api_key": SENTINEL_KEY,
            "render_service_url": "https://sentinel-service.onrender.com",
        },
    )
    assert SENTINEL_KEY not in resp.text


async def test_uptimerobot_validation_error_never_echoes_the_submitted_key():
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/create-monitor",
        json={"key": SENTINEL_KEY, "render_service_url": "https://sentinel-service.onrender.com"},
    )
    assert resp.status_code == 422
    assert SENTINEL_KEY not in resp.text
    assert "input" not in resp.text


async def test_uptimerobot_empty_api_key_is_rejected():
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/create-monitor",
        json={"api_key": "", "render_service_url": "https://sentinel-service.onrender.com"},
    )
    assert resp.status_code == 422


async def test_uptimerobot_empty_render_url_is_rejected():
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/create-monitor",
        json={"api_key": SENTINEL_KEY, "render_service_url": ""},
    )
    assert resp.status_code == 422


async def test_uptimerobot_whitespace_only_render_url_is_rejected():
    """min_length=1 alone lets "   " through, and the client's own .strip()
    then derives a bare relative "/healthz" as the monitor URL."""
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/create-monitor",
        json={"api_key": SENTINEL_KEY, "render_service_url": "   \n\t"},
    )
    assert resp.status_code == 422
    assert SENTINEL_KEY not in resp.text


async def test_uptimerobot_render_url_is_stripped_before_the_client_sees_it(monkeypatch):
    seen = {}

    async def fake_create(api_key, render_service_url):
        seen["url"] = render_service_url
        return uptimerobot_client.UptimeRobotMonitorResult(created=True, monitor_id=42)

    monkeypatch.setattr(uptimerobot_client, "create_or_reuse_monitor", fake_create)
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/create-monitor",
        json={"api_key": SENTINEL_KEY, "render_service_url": "  https://s.onrender.com \n"},
    )
    assert resp.status_code == 200
    assert seen["url"] == "https://s.onrender.com"


async def test_uptimerobot_delete_monitor_reports_valid_true(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "uptime_pinger", {"api_key": SENTINEL_KEY, "monitor_id": 42})

    async def fake_delete(api_key, monitor_id):
        assert api_key == SENTINEL_KEY
        assert monitor_id == 42
        return uptimerobot_client.UptimeRobotMonitorDeleted()

    monkeypatch.setattr(uptimerobot_client, "delete_monitor", fake_delete)
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/delete-monitor", cookies={"onboarding_session": session_id}
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}


async def test_uptimerobot_delete_monitor_failure_reports_the_reason(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "uptime_pinger", {"api_key": SENTINEL_KEY, "monitor_id": 42})

    async def fake_delete(api_key, monitor_id):
        return uptimerobot_client.UptimeRobotApiFailed(reason="unauthorized")

    monkeypatch.setattr(uptimerobot_client, "delete_monitor", fake_delete)
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/delete-monitor", cookies={"onboarding_session": session_id}
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": False, "reason": "unauthorized"}


async def test_uptimerobot_delete_monitor_never_echoes_the_key(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "uptime_pinger", {"api_key": SENTINEL_KEY, "monitor_id": 42})

    async def fake_delete(api_key, monitor_id):
        return uptimerobot_client.UptimeRobotApiFailed(reason="unauthorized")

    monkeypatch.setattr(uptimerobot_client, "delete_monitor", fake_delete)
    client = await _client()
    resp = await client.post(
        "/api/uptimerobot/delete-monitor", cookies={"onboarding_session": session_id}
    )
    assert SENTINEL_KEY not in resp.text


async def test_uptimerobot_delete_monitor_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post("/api/uptimerobot/delete-monitor")
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_create_service_endpoint_returns_id_and_url(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x"})

    async def fake_create_service(api_key, repo_url, name):
        assert api_key == "rnd_x"
        return render_client.RenderServiceCreated(
            service_id="srv-1", service_url="https://x.onrender.com"
        )

    monkeypatch.setattr(render_client, "create_service", fake_create_service)
    client = await _client()
    resp = await client.post(
        "/api/render/create-service",
        json={"repo_url": "https://github.com/a/b", "name": "n"},
        cookies={"onboarding_session": session_id},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "valid": True,
        "service_id": "srv-1",
        "service_url": "https://x.onrender.com",
    }


async def test_create_service_endpoint_relays_rejection_message(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x"})

    async def fake_create_service(api_key, repo_url, name):
        return render_client.RenderServiceCreationFailed(
            reason="request_rejected", message="name taken"
        )

    monkeypatch.setattr(render_client, "create_service", fake_create_service)
    client = await _client()
    resp = await client.post(
        "/api/render/create-service",
        json={"repo_url": "https://github.com/a/b", "name": "n"},
        cookies={"onboarding_session": session_id},
    )
    assert resp.json() == {"valid": False, "reason": "request_rejected", "message": "name taken"}


async def test_create_service_endpoint_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post(
        "/api/render/create-service", json={"repo_url": "https://github.com/a/b", "name": "n"}
    )
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_github_push_render_vars_endpoint_is_gone():
    client = await _client()
    resp = await client.post("/api/github/push-render-vars", json={})
    assert resp.status_code == 404


async def test_confirm_llm_provider_persists_to_session(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()

    async def fake_probe_gemini_model(api_key, model):
        return llm_client.LlmModelProbed(model=model)

    monkeypatch.setattr(llm_client, "probe_gemini_model", fake_probe_gemini_model)
    client = await _client()
    resp = await client.post(
        "/api/llm/confirm",
        json={
            "provider": "gemini",
            "credential_value": "AIzaSy...",
            "model": "gemini-flash-latest",
        },
        cookies={"onboarding_session": session_id},
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}
    assert fake.read_frame(session_id, "llm_provider") == {
        "provider": "gemini",
        "credential_value": "AIzaSy...",
        "model": "gemini-flash-latest",
    }


async def test_confirm_probes_the_pair_the_visitor_chose(monkeypatch):
    """The pair that is verified must be the pair that gets provisioned --
    probing the key's home project while seeding a different one is the
    defect this whole frame exists to close."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    seen = {}

    async def fake_probe(b64, model, project=None, location=None):
        seen.update(project=project, location=location, model=model)
        return llm_client.LlmModelProbed(model=model)

    monkeypatch.setattr(llm_client, "probe_vertex_model", fake_probe)
    client = await _client()
    resp = await client.post(
        "/api/llm/confirm",
        json={
            "provider": "vertex",
            "credential_value": "b64",
            "model": "gemini-2.5-flash",
            "vertex_gcp_project": "chosen-proj",
            "vertex_gcp_location": "europe-west4",
        },
        cookies={"onboarding_session": session_id},
    )
    assert resp.json() == {"valid": True}
    assert seen == {
        "project": "chosen-proj", "location": "europe-west4", "model": "gemini-2.5-flash"
    }
    assert fake.read_frame(session_id, "llm_provider") == {
        "provider": "vertex",
        "credential_value": "b64",
        "model": "gemini-2.5-flash",
        "vertex_gcp_project": "chosen-proj",
        "vertex_gcp_location": "europe-west4",
    }


async def test_confirm_refuses_an_uncallable_model_and_writes_nothing(monkeypatch):
    """A refused model must never become session state -- otherwise
    GET /api/session reports the frame done behind an uncallable model."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()

    async def fake_probe(api_key, model):
        return llm_client.LlmApiFailed(reason="model_not_callable")

    monkeypatch.setattr(llm_client, "probe_gemini_model", fake_probe)
    client = await _client()
    resp = await client.post(
        "/api/llm/confirm",
        json={"provider": "gemini", "credential_value": "k", "model": "nope"},
        cookies={"onboarding_session": session_id},
    )
    assert resp.json() == {"valid": False, "reason": "model_not_callable"}
    assert fake.read_frame(session_id, "llm_provider") in (None, {})


async def test_confirm_never_probes_groq(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()

    async def boom(*a, **k):
        raise AssertionError("groq needs no probe -- see the contract")

    monkeypatch.setattr(llm_client, "probe_gemini_model", boom)
    monkeypatch.setattr(llm_client, "probe_vertex_model", boom)
    client = await _client()
    resp = await client.post(
        "/api/llm/confirm",
        json={"provider": "groq", "credential_value": "k", "model": "llama-3.3-70b"},
        cookies={"onboarding_session": session_id},
    )
    assert resp.json() == {"valid": True}


async def test_confirm_refuses_a_vertex_submission_missing_its_pair(monkeypatch):
    _use_fake_session_store(monkeypatch)
    client = await _client()
    resp = await client.post(
        "/api/llm/confirm",
        json={"provider": "vertex", "credential_value": "b64", "model": "m"},
    )
    # main.py's app-wide handler -- the rejected body is never echoed back.
    assert resp.status_code == 422
    assert resp.json() == {"detail": "invalid request"}


async def test_confirm_refuses_a_gemini_submission_carrying_a_stray_vertex_pair(monkeypatch):
    """The other half of _pair_belongs_to_vertex_only: a gemini/groq
    submission carrying a region is a malformed request, not a field to
    quietly ignore."""
    _use_fake_session_store(monkeypatch)
    client = await _client()
    resp = await client.post(
        "/api/llm/confirm",
        json={
            "provider": "gemini",
            "credential_value": "AIza-x",
            "model": "gemini-flash-latest",
            "vertex_gcp_location": "us-central1",
        },
    )
    assert resp.status_code == 422
    assert resp.json() == {"detail": "invalid request"}


async def test_confirm_rejects_an_unlisted_location_without_constructing_a_client(monkeypatch):
    """The SSRF regression test for /api/llm/confirm specifically -- Task 6
    has this for /api/llm/vertex/list-models, but the endpoint that
    actually WRITES session state (and is the one that gets probed) had no
    equivalent of its own. A location is part of the Vertex hostname; an
    unlisted value must never reach genai.Client via probe_vertex_model."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()

    async def boom(*a, **k):
        raise AssertionError("no Vertex client may be constructed for a rejected location")

    monkeypatch.setattr(llm_client, "probe_vertex_model", boom)
    client = await _client()
    resp = await client.post(
        "/api/llm/confirm",
        json={
            "provider": "vertex",
            "credential_value": "b64",
            "model": "m",
            "vertex_gcp_project": "chosen-proj",
            "vertex_gcp_location": "evil-attacker-host",
        },
        cookies={"onboarding_session": session_id},
    )
    assert resp.json() == {"valid": False, "reason": "invalid_vertex_location"}
    assert fake.read_frame(session_id, "llm_provider") in (None, {})


async def test_confirm_llm_provider_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post(
        "/api/llm/confirm",
        json={"provider": "gemini", "credential_value": "x", "model": "m"},
    )
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_confirm_llm_provider_rejects_unknown_provider(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    client = await _client()
    resp = await client.post(
        "/api/llm/confirm",
        json={"provider": "openai", "credential_value": "x", "model": "y"},
        cookies={"onboarding_session": session_id},
    )
    assert resp.status_code == 422


async def test_llm_push_render_vars_endpoint_is_gone():
    client = await _client()
    resp = await client.post("/api/llm/push-render-vars", json={})
    assert resp.status_code == 404


async def test_confirm_dashboard_auth_persists_to_session(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    client = await _client()
    resp = await client.post(
        "/api/dashboard-auth/confirm",
        json={
            "username": "operator",
            "password": "correct-horse-battery",
            "session_secret": "s" * 43,
        },
        cookies={"onboarding_session": session_id},
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}
    assert fake.read_frame(session_id, "dashboard_auth") == {
        "username": "operator",
        "password": "correct-horse-battery",
        "session_secret": "s" * 43,
    }


async def test_confirm_dashboard_auth_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post(
        "/api/dashboard-auth/confirm",
        json={
            "username": "operator",
            "password": "correct-horse-battery",
            "session_secret": "s" * 43,
        },
    )
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_confirm_dashboard_auth_rejects_short_password(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    client = await _client()
    resp = await client.post(
        "/api/dashboard-auth/confirm",
        json={"username": "operator", "password": "short1", "session_secret": "s" * 43},
        cookies={"onboarding_session": session_id},
    )
    assert resp.status_code == 422


async def test_confirm_dashboard_auth_rejects_short_session_secret(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    client = await _client()
    resp = await client.post(
        "/api/dashboard-auth/confirm",
        json={
            "username": "operator",
            "password": "correct-horse-battery",
            "session_secret": "tooshort",
        },
        cookies={"onboarding_session": session_id},
    )
    assert resp.status_code == 422


async def test_dashboard_auth_push_render_vars_endpoint_is_gone():
    client = await _client()
    resp = await client.post("/api/dashboard-auth/push-render-vars", json={})
    assert resp.status_code == 404


# test_push_render_vars_partial_failure_reports_pushed_keys used to exercise
# _push_result()'s partial-failure shape via the now-removed per-frame
# push-render-vars endpoints. Re-added against /api/render/bulk-push-env-vars
# once that endpoint exists (Task 11) -- _push_result() itself is unchanged.


async def test_trigger_deploy_endpoint(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})

    async def fake_trigger_deploy(api_key, service_id):
        assert (api_key, service_id) == ("rnd_x", "srv-1")
        return render_client.RenderDeployTriggered(deploy_id="dep-1")

    monkeypatch.setattr(render_client, "trigger_deploy", fake_trigger_deploy)
    client = await _client()
    resp = await client.post(
        "/api/render/trigger-deploy", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": True, "deploy_id": "dep-1"}
    assert fake.read_frame(session_id, "render")["pending_deploy_id"] == "dep-1"


async def test_trigger_deploy_endpoint_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post("/api/render/trigger-deploy")
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_deploy_status_endpoint(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "render",
        {"api_key": "rnd_x", "service_id": "srv-1", "pending_deploy_id": "dep-1"},
    )

    async def fake_poll_deploy_status(api_key, service_id, deploy_id):
        assert (api_key, service_id, deploy_id) == ("rnd_x", "srv-1", "dep-1")
        return render_client.RenderDeployStatus(status="live")

    monkeypatch.setattr(render_client, "poll_deploy_status", fake_poll_deploy_status)
    client = await _client()
    resp = await client.post(
        "/api/render/deploy-status", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": True, "status": "live"}


async def test_deploy_status_endpoint_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post("/api/render/deploy-status")
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_deploy_status_endpoint_persists_deployed_once_live(monkeypatch):
    """A revisit after the browser's own sessionStorage mirror is lost (new
    device, mobile tab discard) must still be able to tell the service is
    already deployed -- GET /api/session can only report that if this
    write actually happens when Render first reports "live"."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "render",
        {"api_key": "rnd_x", "service_id": "srv-1", "pending_deploy_id": "dep-1"},
    )

    async def fake_poll_deploy_status(api_key, service_id, deploy_id):
        return render_client.RenderDeployStatus(status="live")

    monkeypatch.setattr(render_client, "poll_deploy_status", fake_poll_deploy_status)
    client = await _client()
    await client.post("/api/render/deploy-status", cookies={"onboarding_session": session_id})
    assert fake.read_frame(session_id, "render")["deployed"] is True


async def test_deploy_status_endpoint_does_not_persist_deployed_while_in_progress(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "render",
        {"api_key": "rnd_x", "service_id": "srv-1", "pending_deploy_id": "dep-1"},
    )

    async def fake_poll_deploy_status(api_key, service_id, deploy_id):
        return render_client.RenderDeployStatus(status="in_progress")

    monkeypatch.setattr(render_client, "poll_deploy_status", fake_poll_deploy_status)
    client = await _client()
    await client.post("/api/render/deploy-status", cookies={"onboarding_session": session_id})
    assert "deployed" not in fake.read_frame(session_id, "render")


async def test_clear_deploy_state_endpoint_resets_deployed_and_pending_deploy_id(monkeypatch):
    """Called when an earlier frame this final step depends on gets
    changed (static/index.html's lockFrame("render-deploy")) -- a stale
    persisted "deployed" flag must not resurrect the OLD deploy's done
    state on a reload mid-redo."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "render",
        {
            "api_key": "rnd_x", "service_id": "srv-1",
            "pending_deploy_id": "dep-1", "deployed": True,
        },
    )
    client = await _client()
    resp = await client.post(
        "/api/render/clear-deploy-state", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": True}
    render_frame = fake.read_frame(session_id, "render")
    assert not render_frame.get("deployed")
    assert not render_frame.get("pending_deploy_id")
    # service_id/service_url are untouched -- still valid, only render-deploy's
    # own resumable state is being invalidated here.
    assert render_frame["service_id"] == "srv-1"


async def test_clear_deploy_state_endpoint_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post("/api/render/clear-deploy-state")
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_get_session_reports_render_deploy_complete_once_deployed(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "render",
        {
            "api_key": "rnd_x", "service_id": "srv-1",
            "service_url": "https://x.onrender.com", "deployed": True,
        },
    )
    client = await _client()
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    assert resp.json()["frames"]["render-deploy"] == {
        "complete": True, "display": {"service_url": "https://x.onrender.com"}
    }


async def test_get_session_reports_render_deploy_provisioning_while_pending(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "render",
        {"api_key": "rnd_x", "service_id": "srv-1", "pending_deploy_id": "dep-1"},
    )
    client = await _client()
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    assert resp.json()["frames"]["render-deploy"] == {
        "complete": False, "provisioning": True, "display": {"pending_deploy_id": "dep-1"}
    }


async def test_get_session_omits_render_deploy_before_any_deploy_is_triggered(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    client = await _client()
    resp = await client.get("/api/session", cookies={"onboarding_session": session_id})
    assert "render-deploy" not in resp.json()["frames"]


async def test_bulk_push_assembles_every_frame_into_one_push_call(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(
        session_id, "github_app",
        {"app_id": 1, "private_key_b64": "pk", "webhook_secret": "wh", "installation_id": 42},
    )
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {"provider": "gemini", "credential_value": "AIza-x", "model": "gemini-flash-latest"},
    )
    fake.update_frame(
        session_id, "dashboard_auth",
        {"username": "admin", "password": "pw123456", "session_secret": "s" * 32},
    )
    captured = {}
    seeded = {}

    async def fake_push_env_vars(api_key, service_id, values):
        captured["values"] = values
        return render_client.RenderEnvVarsPushed(pushed=list(values.keys()))

    def fake_seed_provider_config(database_url, provider, model, project, location):
        seeded["args"] = (database_url, provider, model, project, location)
        return True

    async def fake_probe_gemini_model(api_key, model):
        return llm_client.LlmModelProbed(model=model)

    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    monkeypatch.setattr(router, "_seed_provider_config", fake_seed_provider_config)
    monkeypatch.setattr(llm_client, "probe_gemini_model", fake_probe_gemini_model)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json()["valid"] is True
    assert captured["values"] == {
        "RENDER_API_KEY": "rnd_x",
        "GITHUB_APP_ID": "1",
        "GITHUB_APP_PRIVATE_KEY": "pk",
        "GITHUB_WEBHOOK_SECRET": "wh",
        "GITHUB_APP_INSTALLATION_ID": "42",
        "DATABASE_URL": "postgresql://x",
        "GEMINI_API_KEY": "AIza-x",
        "DASHBOARD_USERNAME": "admin",
        "DASHBOARD_PASSWORD": "pw123456",
        "DASHBOARD_SESSION_SECRET": "s" * 32,
        **router._GENERIC_OPERATIONAL_ENV_DEFAULTS,
    }
    assert "GEMINI_MODEL" not in captured["values"]  # model is DB-only now
    assert seeded["args"] == ("postgresql://x", "gemini", "gemini-flash-latest", None, None)


async def test_bulk_push_omits_a_frame_that_was_never_completed(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    captured = {}

    async def fake_push_env_vars(api_key, service_id, values):
        captured["values"] = values
        return render_client.RenderEnvVarsPushed(pushed=list(values.keys()))

    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    client = await _client()
    await client.post("/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id})
    # The generic operational-tuning defaults are always included, unlike
    # every other key (which is gated on its frame being complete).
    # RENDER_API_KEY is read from the render frame itself, which this
    # endpoint's own guard clause already requires to be present -- so it
    # is never actually omitted the way other frames' keys are.
    assert captured["values"] == {
        "RENDER_API_KEY": "rnd_x",
        **router._GENERIC_OPERATIONAL_ENV_DEFAULTS,
    }


async def test_bulk_push_includes_github_target_repo_wildcard(monkeypatch):
    """The sibling review-engine project's main.py lifespan now refuses to
    boot without GITHUB_TARGET_REPO explicitly set (2026-09-07) -- "*" is
    that project's own required sentinel for track-all mode, which is the
    only mode this wizard ever provisions (it has no frame collecting a
    repo allowlist). Pin the literal value so a future edit to
    _GENERIC_OPERATIONAL_ENV_DEFAULTS can't silently drop or change it."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    captured = {}

    async def fake_push_env_vars(api_key, service_id, values):
        captured["values"] = values
        return render_client.RenderEnvVarsPushed(pushed=list(values.keys()))

    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    client = await _client()
    await client.post("/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id})
    assert captured["values"]["GITHUB_TARGET_REPO"] == "*"


def test_seed_provider_config_writes_a_real_row(db_url, db_query):
    ok = router._seed_provider_config(db_url, "groq", "llama-3.3-70b-versatile", None, None)
    assert ok is True
    row = db_query(
        "SELECT model, vertex_gcp_project, vertex_gcp_location "
        "FROM slot_config WHERE provider = 'groq' AND slot_index = 0"
    )
    assert row == [("llama-3.3-70b-versatile", None, None)]
    row = db_query("SELECT provider, groq_key_index FROM runtime_config WHERE id = 1")
    assert row == [("groq", 0)]


def test_seed_provider_config_upserts_on_a_second_call(db_url, db_query):
    router._seed_provider_config(db_url, "groq", "stale-model", None, None)
    ok = router._seed_provider_config(db_url, "groq", "new-model", None, None)
    assert ok is True
    row = db_query(
        "SELECT model FROM slot_config WHERE provider = 'groq' AND slot_index = 0"
    )
    assert row == [("new-model",)]
    row = db_query("SELECT provider, groq_key_index FROM runtime_config WHERE id = 1")
    assert row == [("groq", 0)]


def test_seed_provider_config_writes_the_right_key_index_column_per_provider(
    db_url, db_query, db_exec
):
    """runtime_config has one *_key_index column per provider -- a wrong
    lookup would silently write to the wrong column instead of the one
    actually being configured. runtime_config is a singleton row this
    wizard's own conftest doesn't truncate between tests (it belongs to the
    simulated visitor's own external database, not the wizard's session
    store) -- cleared here explicitly so an earlier test's leftover
    gemini_key_index/groq_key_index value can't be mistaken for something
    this call wrote."""
    db_exec("DELETE FROM runtime_config WHERE id = 1")
    ok = router._seed_provider_config(db_url, "vertex", "gemini-2.5-flash", None, "us-central1")
    assert ok is True
    row = db_query(
        "SELECT provider, gemini_key_index, groq_key_index, vertex_key_index "
        "FROM runtime_config WHERE id = 1"
    )
    assert row == [("vertex", None, None, 0)]


def test_seed_provider_config_writes_only_what_this_wizard_uniquely_knows(
    db_url, db_exec, db_query
):
    """The 2026-09-10 contract-direction change, from this side. This wizard
    no longer copies pr-review-bot's 15 operational defaults: it writes
    provider, the chosen provider's key-slot index, and updated_at, and the
    bot fills the rest at boot from its OWN declared defaults (its
    review_queue/store.py::_widen_statements + _backfill_runtime_config,
    landed 2026-09-10). Those columns coming back NULL here is now CORRECT --
    the bot's backfill is what makes them non-NULL, and tests/
    test_cross_repo_config_ordering.py::test_wizard_seed_leaves_bot_boot_ready
    is what proves the whole chronology end to end against a live Postgres.

    Selecting from the columns the narrow table actually declares, not from
    the bot's full 22: after tier-2 has run, this shared Postgres may hold a
    table the bot already widened, so a SELECT of a tuning column would
    succeed or raise UndefinedColumn depending on test order.
    """
    db_exec("DROP TABLE IF EXISTS runtime_config CASCADE")
    ok = router._seed_provider_config(db_url, "groq", "llama-3.3-70b-versatile", None, None)
    assert ok is True
    row = db_query(
        "SELECT provider, groq_key_index, gemini_key_index, vertex_key_index "
        "FROM runtime_config WHERE id = 1"
    )
    assert row == [("groq", 0, None, None)]
    assert "cooldown_base_seconds" not in router._RUNTIME_CONFIG_SCHEMA
    assert not hasattr(router, "_RUNTIME_CONFIG_DEFAULTS")


def test_seed_provider_config_returns_false_on_an_unreachable_database():
    ok = router._seed_provider_config(
        "postgresql://u:p@localhost:1/nonexistent", "groq", "x", None, None
    )
    assert ok is False


async def test_bulk_push_seeds_vertex_location_default(monkeypatch):
    """The LLM-provider frame now always collects and probes the visitor's
    own project/region pair for vertex (/api/llm/confirm requires it), so
    the llm_provider frame's stored vertex_gcp_project/vertex_gcp_location
    are what gets seeded here -- not a None/"us-central1" fallback, which
    was this wizard's pre-project/region-collection behaviour."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {
            "provider": "vertex",
            "credential_value": "b64-key",
            "model": "gemini-2.5-flash",
            "vertex_gcp_project": "my-project",
            "vertex_gcp_location": "us-central1",
        },
    )
    seeded = {}

    def fake_seed_provider_config(database_url, provider, model, project, location):
        seeded["args"] = (database_url, provider, model, project, location)
        return True

    async def fake_push_env_vars(api_key, service_id, values):
        return render_client.RenderEnvVarsPushed(pushed=list(values.keys()))

    async def fake_probe_vertex_model(service_account_key_b64, model, project=None, location=None):
        return llm_client.LlmModelProbed(model=model)

    monkeypatch.setattr(router, "_seed_provider_config", fake_seed_provider_config)
    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    monkeypatch.setattr(llm_client, "probe_vertex_model", fake_probe_vertex_model)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json()["valid"] is True
    assert seeded["args"] == (
        "postgresql://x", "vertex", "gemini-2.5-flash", "my-project", "us-central1",
    )


async def test_bulk_push_refuses_before_seeding_when_the_model_probe_fails(monkeypatch):
    """list-models only proves the credential authenticates and the model is
    LISTED -- for Vertex that's the global Model Garden, not per-project
    entitlement. A failed probe must refuse before _seed_provider_config and
    before the Render push, exactly like a DB-seed failure does: the visitor
    must never reach a state where a credential is pushed (or a row seeded)
    for a model that 404s every real call."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {
            "provider": "vertex",
            "credential_value": "b64-key",
            "model": "gemini-3.1-flash-lite",
            "vertex_gcp_project": "my-project",
            "vertex_gcp_location": "us-central1",
        },
    )

    def boom_seed(*a, **k):
        raise AssertionError("_seed_provider_config must not run when the probe fails")

    def boom_push(*a, **k):
        raise AssertionError("push_env_vars must not run when the probe fails")

    async def fake_probe_vertex_model(service_account_key_b64, model, project=None, location=None):
        return llm_client.LlmApiFailed(reason="model_not_callable")

    monkeypatch.setattr(router, "_seed_provider_config", boom_seed)
    monkeypatch.setattr(render_client, "push_env_vars", boom_push)
    monkeypatch.setattr(llm_client, "probe_vertex_model", fake_probe_vertex_model)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": False, "reason": "model_not_callable", "pushed": []}


async def test_bulk_push_never_probes_groq(monkeypatch):
    """Groq needs no live entitlement probe (contracts/provisioning.json's
    model_validation block: no free token-counting endpoint, and its own
    listing is key-scoped, unlike Vertex's)."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {"provider": "groq", "credential_value": "gsk-x", "model": "llama-3.3-70b-versatile"},
    )

    def fake_seed_provider_config(database_url, provider, model, project, location):
        return True

    async def fake_push_env_vars(api_key, service_id, values):
        return render_client.RenderEnvVarsPushed(pushed=list(values.keys()))

    def boom_probe(*a, **k):
        raise AssertionError("groq must never be probed")

    monkeypatch.setattr(router, "_seed_provider_config", fake_seed_provider_config)
    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    monkeypatch.setattr(llm_client, "probe_gemini_model", boom_probe)
    monkeypatch.setattr(llm_client, "probe_vertex_model", boom_probe)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json()["valid"] is True


async def test_bulk_push_refuses_when_slot_config_seed_fails(monkeypatch):
    """The visitor must never reach a deployable state where Render has the
    credential but the database has no matching model row -- a DB-seed
    failure must refuse before the Render push even runs."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {"provider": "gemini", "credential_value": "AIza-x", "model": "gemini-flash-latest"},
    )

    def fake_seed_provider_config(database_url, provider, model, project, location):
        return False

    def boom(*a, **k):
        raise AssertionError("push_env_vars must not be called when the DB seed failed")

    async def fake_probe_gemini_model(api_key, model):
        return llm_client.LlmModelProbed(model=model)

    monkeypatch.setattr(router, "_seed_provider_config", fake_seed_provider_config)
    monkeypatch.setattr(render_client, "push_env_vars", boom)
    monkeypatch.setattr(llm_client, "probe_gemini_model", fake_probe_gemini_model)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    body = resp.json()
    assert body["valid"] is False
    assert body["reason"] == "slot_config_seed_failed"


async def test_bulk_push_with_no_session_fails_closed():
    client = await _client()
    resp = await client.post("/api/render/bulk-push-env-vars")
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_bulk_push_partial_failure_reports_pushed_keys(monkeypatch):
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(
        session_id, "github_app",
        {"app_id": 1, "private_key_b64": "pk", "webhook_secret": "wh", "installation_id": 42},
    )

    async def fake_push_env_vars(api_key, service_id, values):
        return render_client.RenderEnvVarsPushFailed(reason="invalid_key", pushed=["GITHUB_APP_ID"])

    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": False, "reason": "invalid_key", "pushed": ["GITHUB_APP_ID"]}


async def test_bulk_push_refuses_when_llm_provider_present_without_database_url(monkeypatch):
    """An llm_provider frame with no matching supabase.database_url must not
    silently push the credential with no slot_config row to seed it into --
    that's the exact state the seed-before-push ordering elsewhere in this
    endpoint exists to prevent. Unreachable in normal sequential flow (the
    Supabase frame always completes first), but a corrupted/hand-edited
    session shouldn't get a silent partial push instead of a clear refusal."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(
        session_id, "llm_provider",
        {"provider": "gemini", "credential_value": "AIza-x", "model": "gemini-flash-latest"},
    )

    def boom(*a, **k):
        raise AssertionError("push_env_vars must not be called without a database_url")

    monkeypatch.setattr(render_client, "push_env_vars", boom)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": False, "reason": "supabase_not_ready", "pushed": []}


async def test_bulk_push_seeds_vertex_location_from_the_stored_pair_not_the_module_constant(
    monkeypatch,
):
    """The seeded vertex_gcp_location must track the session's own stored
    vertex_gcp_location -- written by /api/llm/confirm from the visitor's
    dropdown choice -- not llm_client._VERTEX_LOCATION. Changing the module
    constant must have no effect on what gets seeded."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {
            "provider": "vertex",
            "credential_value": "b64-key",
            "model": "gemini-2.5-flash",
            "vertex_gcp_project": "my-project",
            "vertex_gcp_location": "europe-west4",
        },
    )
    seeded = {}

    def fake_seed_provider_config(database_url, provider, model, project, location):
        seeded["location"] = location
        return True

    async def fake_push_env_vars(api_key, service_id, values):
        return render_client.RenderEnvVarsPushed(pushed=list(values.keys()))

    async def fake_probe_vertex_model(service_account_key_b64, model, project=None, location=None):
        return llm_client.LlmModelProbed(model=model)

    monkeypatch.setattr(router, "_seed_provider_config", fake_seed_provider_config)
    monkeypatch.setattr(llm_client, "_VERTEX_LOCATION", "some-other-region")
    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    monkeypatch.setattr(llm_client, "probe_vertex_model", fake_probe_vertex_model)
    client = await _client()
    await client.post("/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id})
    assert seeded["location"] == "europe-west4"


async def test_bulk_push_seeds_and_probes_the_visitors_chosen_pair(monkeypatch):
    """The backstop must re-verify, and seed, the same pair the frame
    confirmed -- not the key's home project at a hardcoded region."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {
            "provider": "vertex",
            "credential_value": "b64",
            "model": "gemini-2.5-flash",
            "vertex_gcp_project": "chosen-proj",
            "vertex_gcp_location": "europe-west4",
        },
    )
    seeded, probed = {}, {}

    def fake_seed(database_url, provider, model, project, location):
        seeded.update(project=project, location=location)
        return True

    async def fake_probe(b64, model, project=None, location=None):
        probed.update(project=project, location=location)
        return llm_client.LlmModelProbed(model=model)

    async def fake_push_env_vars(api_key, service_id, values):
        return render_client.RenderEnvVarsPushed(pushed=list(values.keys()))

    monkeypatch.setattr(router, "_seed_provider_config", fake_seed)
    monkeypatch.setattr(llm_client, "probe_vertex_model", fake_probe)
    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json()["valid"] is True
    assert probed == {"project": "chosen-proj", "location": "europe-west4"}
    assert seeded == {"project": "chosen-proj", "location": "europe-west4"}


async def test_bulk_push_refuses_a_vertex_frame_with_no_stored_pair(monkeypatch):
    """A session confirmed against an older deploy (before this pair was
    required/stored) can still reach here with the llm_provider frame
    present but missing vertex_gcp_project/vertex_gcp_location.
    session_store.SESSION_TTL is 4 hours, so this window is real. Falling
    back to None/None would silently probe (and seed) the key's home
    project at the SDK default region instead of refusing -- exactly the
    defect this whole feature exists to close."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {"provider": "vertex", "credential_value": "b64", "model": "gemini-2.5-flash"},
    )

    def boom_seed(*a, **k):
        raise AssertionError("_seed_provider_config must not run with no stored pair")

    async def boom_probe(*a, **k):
        raise AssertionError("probe_vertex_model must not run with no stored pair")

    def boom_push(*a, **k):
        raise AssertionError("push_env_vars must not run with no stored pair")

    monkeypatch.setattr(router, "_seed_provider_config", boom_seed)
    monkeypatch.setattr(llm_client, "probe_vertex_model", boom_probe)
    monkeypatch.setattr(render_client, "push_env_vars", boom_push)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": False, "reason": "vertex_pair_missing", "pushed": []}


async def test_bulk_push_refuses_a_vertex_frame_with_an_unlisted_stored_location(monkeypatch):
    """Same as the missing-pair case, but for a stored location that isn't
    (or is no longer) in the contract allowlist -- a location is part of
    the Vertex hostname, so a stale/corrupted session value gets the same
    re-check a freshly-submitted one gets, not a free pass."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {
            "provider": "vertex",
            "credential_value": "b64",
            "model": "gemini-2.5-flash",
            "vertex_gcp_project": "chosen-proj",
            "vertex_gcp_location": "evil-attacker-host",
        },
    )

    async def boom_probe(*a, **k):
        raise AssertionError("no Vertex client may be constructed for a rejected location")

    monkeypatch.setattr(llm_client, "probe_vertex_model", boom_probe)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": False, "reason": "vertex_pair_missing", "pushed": []}


async def test_bulk_push_never_seeds_a_stale_vertex_pair_onto_a_redone_gemini_frame(monkeypatch):
    """/api/llm/confirm's write is a merge, not replace=True (a parked,
    pre-existing issue -- see ISSUES.md) -- so a visitor who first confirms
    vertex and then redoes the frame as gemini can leave the OLD
    vertex_gcp_project/vertex_gcp_location sitting in the same frame dict
    alongside the new gemini fields. Those stale values must never reach
    _seed_provider_config for a gemini row."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    fake.update_frame(session_id, "supabase", {"database_url": "postgresql://x"})
    fake.update_frame(
        session_id, "llm_provider",
        {
            "provider": "gemini",
            "credential_value": "AIza-x",
            "model": "gemini-flash-latest",
            # Left over from an earlier vertex confirm -- confirm's merge
            # write never clears these when a redo switches provider.
            "vertex_gcp_project": "stale-proj",
            "vertex_gcp_location": "europe-west4",
        },
    )
    seeded = {}

    def fake_seed(database_url, provider, model, project, location):
        seeded.update(provider=provider, project=project, location=location)
        return True

    async def fake_probe_gemini_model(api_key, model):
        return llm_client.LlmModelProbed(model=model)

    async def fake_push_env_vars(api_key, service_id, values):
        return render_client.RenderEnvVarsPushed(pushed=list(values.keys()))

    monkeypatch.setattr(router, "_seed_provider_config", fake_seed)
    monkeypatch.setattr(llm_client, "probe_gemini_model", fake_probe_gemini_model)
    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    client = await _client()
    resp = await client.post(
        "/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id}
    )
    assert resp.json()["valid"] is True
    assert seeded == {"provider": "gemini", "project": None, "location": None}


async def test_bulk_push_reads_the_session_exactly_once(monkeypatch):
    """Reads every frame off one _get_session call rather than one
    _read_frame (and therefore one full session fetch+decrypt) per frame --
    5 round-trips collapsed into 1."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(session_id, "render", {"api_key": "rnd_x", "service_id": "srv-1"})
    calls = {"n": 0}
    real_get_session = fake.get_session

    def counting_get_session(sid):
        calls["n"] += 1
        return real_get_session(sid)

    monkeypatch.setattr(session_store, "get_session", counting_get_session)

    async def fake_push_env_vars(api_key, service_id, values):
        return render_client.RenderEnvVarsPushed(pushed=list(values.keys()))

    monkeypatch.setattr(render_client, "push_env_vars", fake_push_env_vars)
    client = await _client()
    await client.post("/api/render/bulk-push-env-vars", cookies={"onboarding_session": session_id})
    assert calls["n"] == 1


async def test_deploy_status_endpoint_with_cleared_pending_deploy_id_fails_closed(monkeypatch):
    """clear-deploy-state merges pending_deploy_id=None rather than deleting
    the key -- a presence-only guard here would let that None reach
    poll_deploy_status and surface as a misleading 'service not found'."""
    fake = _use_fake_session_store(monkeypatch)
    session_id = fake.create_session()
    fake.update_frame(
        session_id, "render",
        {"api_key": "rnd_x", "service_id": "srv-1", "pending_deploy_id": None},
    )

    def boom(*a, **k):
        raise AssertionError("poll_deploy_status must not be called with no pending_deploy_id")

    monkeypatch.setattr(render_client, "poll_deploy_status", boom)
    client = await _client()
    resp = await client.post(
        "/api/render/deploy-status", cookies={"onboarding_session": session_id}
    )
    assert resp.json() == {"valid": False, "reason": "no_session"}


async def test_vertex_locations_endpoint_serves_the_contract_list_in_order():
    """Rendered as received -- the contract's order is a curated geographic
    grouping, not alphabetical, and the dropdown preserves it."""
    client = await _client()
    resp = await client.get("/api/llm/vertex/locations")
    assert resp.status_code == 200
    assert resp.json() == {
        "locations": CONTRACT["vertex_locations"]["options"],
        "default": CONTRACT["vertex_locations"]["default"],
    }


def test_only_contract_declared_locations_are_accepted():
    """A location is part of the Vertex hostname -- an allowlist, never a
    pattern. See the spec's section 3."""
    assert router._valid_vertex_location("us-central1")
    assert not router._valid_vertex_location("evil-attacker-host")
    assert not router._valid_vertex_location("")
    assert not router._valid_vertex_location("us-central1.evil.com")


def test_project_ids_are_pattern_checked():
    assert router._valid_vertex_project("my-project-123")
    assert not router._valid_vertex_project("Bad_Project")
    assert not router._valid_vertex_project("x")
    assert not router._valid_vertex_project("")


async def test_first_validate_returns_the_project_options(monkeypatch):
    async def fake_list_models(b64, project=None, location=None):
        return llm_client.VertexModelsListed(project_id="test-project", models=["m1"])

    async def fake_list_projects(b64):
        return llm_client.VertexProjectsListed(projects=["a-proj", "test-project"])

    monkeypatch.setattr(llm_client, "list_vertex_models", fake_list_models)
    monkeypatch.setattr(llm_client, "list_accessible_projects", fake_list_projects)
    client = await _client()
    resp = await client.post("/api/llm/vertex/list-models", json={"service_account_key_b64": "k"})
    assert resp.json() == {
        "valid": True,
        "project_id": "test-project",
        "models": ["m1"],
        "projects": ["a-proj", "test-project"],
        "default_project": "test-project",
        "default_location": router._VERTEX_DEFAULT_LOCATION,
    }


async def test_first_validate_lists_at_the_contracts_own_default_location(monkeypatch):
    """The listing call and the dropdown's preselection (default_location)
    must be scoped to the SAME region -- both read from
    router._VERTEX_DEFAULT_LOCATION, never from llm_client's own separate
    module constant, which is a second, independently maintained value
    that happens to hold the same string today but could drift."""
    seen = {}

    async def fake_list_models(b64, project=None, location=None):
        seen["location"] = location
        return llm_client.VertexModelsListed(project_id="test-project", models=["m1"])

    async def fake_list_projects(b64):
        return llm_client.VertexProjectsListed(projects=["test-project"])

    monkeypatch.setattr(llm_client, "list_vertex_models", fake_list_models)
    monkeypatch.setattr(llm_client, "list_accessible_projects", fake_list_projects)
    monkeypatch.setattr(llm_client, "_VERTEX_LOCATION", "some-other-region-entirely")
    client = await _client()
    resp = await client.post("/api/llm/vertex/list-models", json={"service_account_key_b64": "k"})
    assert seen["location"] == router._VERTEX_DEFAULT_LOCATION
    assert resp.json()["default_location"] == router._VERTEX_DEFAULT_LOCATION


async def test_a_dropdown_change_does_not_repeat_the_projects_listing(monkeypatch):
    """One live Cloud Resource Manager call per credential, not one per
    dropdown change -- the burst pattern CLAUDE.md's LLM hygiene forbids."""
    async def fake_list_models(b64, project=None, location=None):
        return llm_client.VertexModelsListed(project_id=project, models=["m1"])

    async def boom(b64):
        raise AssertionError("re-validate must not repeat the projects listing")

    monkeypatch.setattr(llm_client, "list_vertex_models", fake_list_models)
    monkeypatch.setattr(llm_client, "list_accessible_projects", boom)
    client = await _client()
    resp = await client.post(
        "/api/llm/vertex/list-models",
        json={"service_account_key_b64": "k", "project": "other-proj", "location": "europe-west4"},
    )
    assert resp.json() == {"valid": True, "project_id": "other-proj", "models": ["m1"]}


async def test_an_unlisted_location_is_refused_without_constructing_a_client(monkeypatch):
    """The SSRF regression test: a well-shaped but undeclared location must
    never reach genai.Client -- a location is part of the hostname."""
    async def boom(*a, **k):
        raise AssertionError("no Vertex client may be constructed for a rejected location")

    monkeypatch.setattr(llm_client, "list_vertex_models", boom)
    monkeypatch.setattr(llm_client, "list_accessible_projects", boom)
    client = await _client()
    resp = await client.post(
        "/api/llm/vertex/list-models",
        json={"service_account_key_b64": "k", "location": "evil-attacker-host"},
    )
    assert resp.json() == {"valid": False, "reason": "invalid_vertex_location"}


async def test_a_malformed_project_is_refused(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("no listing may run for a rejected project")

    monkeypatch.setattr(llm_client, "list_vertex_models", boom)
    monkeypatch.setattr(llm_client, "list_accessible_projects", boom)
    client = await _client()
    resp = await client.post(
        "/api/llm/vertex/list-models",
        json={"service_account_key_b64": "k", "project": "Bad_Project"},
    )
    assert resp.json() == {"valid": False, "reason": "invalid_vertex_project"}


async def test_a_failed_projects_listing_falls_back_to_the_keys_own_project(monkeypatch):
    """Mirrors pr-review-bot's dashboard/environment.py::_validate_vertex_credential:
    a projects:search failure (e.g. Cloud Resource Manager not enabled, or the
    account lacking resourcemanager.projects.get) must not block validation --
    the key's own project, already confirmed usable by the list_vertex_models
    call above, is still offered as the one dropdown option."""
    async def fake_list_models(b64, project=None, location=None):
        return llm_client.VertexModelsListed(project_id="test-project", models=["m1"])

    async def fake_list_projects(b64):
        return llm_client.LlmApiFailed(reason="vertex_projects_unavailable")

    monkeypatch.setattr(llm_client, "list_vertex_models", fake_list_models)
    monkeypatch.setattr(llm_client, "list_accessible_projects", fake_list_projects)
    client = await _client()
    resp = await client.post("/api/llm/vertex/list-models", json={"service_account_key_b64": "k"})
    assert resp.json() == {
        "valid": True,
        "project_id": "test-project",
        "models": ["m1"],
        "projects": ["test-project"],
        "default_project": "test-project",
        "default_location": router._VERTEX_DEFAULT_LOCATION,
    }
