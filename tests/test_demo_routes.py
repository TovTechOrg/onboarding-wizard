import logging

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo/demo")
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    # Explicitly (re)install the mocks rather than relying on demo.app's
    # module-level install_mocks() call: that call only ever fires once per
    # process (Python caches the module after the first `from demo.app
    # import app` anywhere), and tests/conftest.py's
    # `_restore_real_clients_after_demo_app_import` fixture reverts the four
    # mocked modules back to real after every test MODULE that touches
    # demo.app. If this file happens to run in the same pytest-xdist worker
    # AFTER an earlier demo test module, the cached import means install_
    # mocks() never re-fires on its own -- this file's own tests need the
    # mock session_store/render_client to be genuinely active regardless of
    # what ran before it in this worker, so call it directly, every test,
    # the same defensive pattern tests/test_demo_app_boot.py already uses.
    from demo.app import install_mocks

    install_mocks()


async def test_step_endpoint_logs_a_structured_line(demo_env, caplog):
    from demo.app import app

    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="demo.routes"):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/demo/step/provider_done")

    assert response.json() == {"recorded": "provider_done"}
    assert "demo_step step=provider_done" in caplog.text


async def test_unknown_step_names_are_not_echoed(demo_env, caplog):
    from demo.app import app

    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="demo.routes"):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/demo/step/%3Cinjected%3E")

    assert "injected" not in caplog.text
    assert "demo_step step=unknown" in caplog.text


async def test_a_cookieless_visitor_still_gets_a_working_session(demo_env):
    """No cookie jar at all -- the cookie-hostile case (LinkedIn's in-app
    browser, private modes). The reader must still see a usable wizard, and
    crucially must NOT be bounced between responses."""
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies=None, follow_redirects=False) as client:
        first = await client.get("/api/session")
        second = await client.get("/api/session")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json(), "a cookieless visitor must be stable"


async def test_two_cookie_capable_visitors_get_independent_sessions(demo_env):
    """Final-review C2 negative-control test: the ORIGINAL (buggy) version of
    `_cookieless_visitors_share_one_session` pinned every visitor with no
    session cookie yet -- true of every visitor's very first request,
    cookie-capable or not -- so NO visitor ever got their own session;
    `validate-key`'s own `session_id is None or get_session(session_id) is
    None` guard was always false (the shared session already existed), so
    `Set-Cookie` was never sent to anyone.

    Two independent httpx clients, each with a real, working cookie jar,
    each visit "/" first -- `_inject_demo_script`'s own eager-session-mint
    fix sends each its OWN real, distinct session `Set-Cookie` right on
    that very first response, before either ever submits anything -- and
    then submit different render-key values. Neither may see the other's
    session state.

    base_url is `https://test`, not `http://test` -- the real session
    cookie is Secure (`_set_session_cookie` in router.py); httpx's own
    jar refuses to store or resend a Secure cookie over a plain-http
    origin (the same accident
    test_cookieless_visitors_share_a_genuinely_functioning_session's own
    docstring documents), which would make even a genuinely cookie-capable
    client here indistinguishable from a blocked one on the second
    request. `https://test` lets the jar behave like a real browser's.
    """
    from demo.app import app

    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="https://test") as client_a,
        AsyncClient(transport=transport, base_url="https://test") as client_b,
    ):
        index_a = await client_a.get("/")
        index_b = await client_b.get("/")
        assert "set-cookie" in {k.lower() for k in index_a.headers}, (
            "a cookie-capable visitor must get their own Set-Cookie on '/'"
        )
        assert "set-cookie" in {k.lower() for k in index_b.headers}, (
            "a cookie-capable visitor must get their own Set-Cookie on '/'"
        )

        validate_a = await client_a.post(
            "/api/render/validate-key", json={"api_key": "visitor-a-key"}
        )
        validate_b = await client_b.post(
            "/api/render/validate-key", json={"api_key": "visitor-b-key"}
        )
        assert validate_a.json()["valid"] is True
        assert validate_b.json()["valid"] is True

        from router import SESSION_COOKIE_NAME

        session_id_a = client_a.cookies.get(SESSION_COOKIE_NAME)
        session_id_b = client_b.cookies.get(SESSION_COOKIE_NAME)
        assert session_id_a and session_id_b and session_id_a != session_id_b, (
            "two independent cookie-capable visitors must get distinct "
            "session ids, not the one shared demo session"
        )

        state_a_before = await client_a.get("/api/session")
        state_b_before = await client_b.get("/api/session")
        assert state_a_before.json()["frames"]["render-key"]["complete"] is True
        assert state_b_before.json()["frames"]["render-key"]["complete"] is True

        # Stronger than comparing two GET /api/session bodies (identical by
        # construction here -- demo/render_client.py's validate_key() mock
        # always reports the same owner_name regardless of api_key, and
        # every other frame is pre-seeded identically for every session):
        # prove these are genuinely SEPARATE sessions by resetting only A's
        # and confirming B is untouched. Under the original bug (every
        # visitor pinned to the one shared session), this reset would have
        # wiped B's progress too.
        reset_a = await client_a.post("/api/session/reset")
        assert reset_a.status_code in (200, 204)

        state_a_after = await client_a.get("/api/session")
        state_b_after = await client_b.get("/api/session")

    # A's own reset deletes A's session and clears A's cookie; A's very
    # next request is a non-"/" call with no session cookie, which falls
    # through to the shared-session fallback (the same one a genuinely
    # cookie-blocked visitor gets) rather than "/" 's own eager-mint path
    # -- so this checks the one thing that actually matters here (A's
    # render-key progress is gone), not that A sees a wholly empty session.
    assert "render-key" not in state_a_after.json()["frames"], (
        "A's own reset should clear A's render-key progress"
    )
    assert state_b_after.json()["frames"]["render-key"]["complete"] is True, (
        "A's 'Start over' must not affect B's independent session"
    )


async def test_start_over_resets_the_session(demo_env):
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/session")
        reset = await client.post("/api/session/reset")

    assert reset.status_code in (200, 204)


async def test_a_cookieless_visitor_gets_no_redirects_across_many_requests(demo_env):
    """Direct regression guard for the sibling bot project's infinite-redirect
    incident: repeated requests from a browser that stores no cookie at all
    must never see a 3xx anywhere, on any of the endpoints this fallback
    touches, across enough repetitions that a redirect loop would show up."""
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies=None, follow_redirects=False) as client:
        for _ in range(5):
            r1 = await client.get("/api/session")
            r2 = await client.get("/")
            assert r1.status_code < 300
            assert r2.status_code < 300


async def test_finish_and_deploy_succeeds_end_to_end(demo_env):
    """Final-review C1 regression test: router.py's `bulk_push_render_env_vars`
    calls `_seed_provider_config`, which opens a REAL psycopg connection to
    the session's `supabase["database_url"]` -- demo/session_store.py
    pre-seeds that as a fake, unreachable Postgres URL
    (postgresql://demo:demo@localhost:5432/demo). Before demo/app.py
    rebound `router._seed_provider_config` to a no-op success stub, this
    call always failed and `bulk-push-env-vars` returned
    `{"valid": false, "reason": "slot_config_seed_failed"}` -- the reader's
    "Finish & Deploy" step was permanently broken, and `trigger-deploy` was
    never reached."""
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        validate = await client.post(
            "/api/render/validate-key", json={"api_key": "demo-key"}
        )
        assert validate.json()["valid"] is True

        created = await client.post(
            "/api/render/create-service",
            json={"repo_url": "https://github.com/example/repo", "name": "demo-svc"},
        )
        assert created.json()["valid"] is True

        confirmed = await client.post(
            "/api/llm/confirm",
            json={
                "provider": "groq",
                "credential_value": "demo-groq-key",
                "model": "llama-3.3-70b-versatile",
            },
        )
        assert confirmed.json()["valid"] is True

        pushed = await client.post("/api/render/bulk-push-env-vars")
        body = pushed.json()
        assert body.get("reason") != "slot_config_seed_failed", body
        assert body["valid"] is True, body

        # Proves the fix actually unblocks the rest of the flow, not just
        # this one endpoint: trigger-deploy is the next real call
        # static/index.html's triggerRenderDeploy() makes right after a
        # successful bulk-push-env-vars.
        trigger = await client.post("/api/render/trigger-deploy")
    assert trigger.json().get("valid") is True, trigger.json()


async def test_a_real_network_call_never_fires_on_a_render_key_change(demo_env, monkeypatch):
    """Final-review C3 regression test: router.py's delete-monitor endpoint
    (reached from static/index.html's beginChange('render-key') ->
    cleanupOrphanedUptimeMonitor()) used to call the REAL, unmocked
    uptimerobot_client -- demo/session_store.py's uptime_pinger preseed
    always satisfies delete-monitor's precondition (api_key + monitor_id
    both present), so this fired a genuine outbound HTTPS call to
    api.uptimerobot.com on every "Change" click. Patches
    httpx.AsyncHTTPTransport.handle_async_request to raise if ANY outbound
    HTTP call is attempted, then drives the exact request the browser makes
    and confirms zero outbound attempts and a clean response."""
    import httpx

    def _fail_on_any_outbound_call(self, request):
        raise AssertionError(f"unexpected outbound HTTP call: {request.url}")

    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _fail_on_any_outbound_call
    )

    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        validate = await client.post(
            "/api/render/validate-key", json={"api_key": "demo-key"}
        )
        assert validate.json()["valid"] is True

        response = await client.post("/api/uptimerobot/delete-monitor")

    assert response.status_code == 200


async def test_cookieless_visitors_share_a_genuinely_functioning_session(demo_env):
    """The critical end-to-end property: a real state-changing write made by
    router.py's own endpoint code (which reads request.cookies.get(
    SESSION_COOKIE_NAME) deep inside validate_render_key) through a
    cookieless client must actually persist and be visible to the SAME
    cookieless client's next request. This is stronger than checking a 200
    status -- it proves the injected cookie header is genuinely observed by
    downstream router.py code, not just by our own middleware's request
    object (the functools.cached_property staleness trap named in the task
    brief).

    On what actually makes this client "cookieless": httpx's AsyncClient
    keeps a real cookie jar even with cookies=None (that only sets the
    jar's starting contents to empty, it doesn't disable storing/sending
    cookies). This test passes because router.py's session cookie is set
    Secure=True (`_set_session_cookie` in router.py) while this test's
    base_url is plain http://test -- the jar's own secure-cookie policy
    refuses to store or resend a Secure cookie over a non-https origin, so
    the second request genuinely arrives with none. That is an accident of
    this test's chosen base_url, not something this test previously
    verified was still true, so a future change to base_url (e.g. to
    "https://test") could silently turn this into a same-session round trip
    while the test kept passing. The assertions below pin the actual
    mechanism directly: no Set-Cookie header was ever sent to this client,
    and the client's own jar stays empty throughout.
    """
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies=None, follow_redirects=False) as client:
        validate = await client.post(
            "/api/render/validate-key", json={"api_key": "demo-key-value"}
        )
        assert validate.status_code == 200
        assert validate.json()["valid"] is True
        assert "set-cookie" not in {k.lower() for k in validate.headers}
        assert len(client.cookies) == 0

        # A second, independent cookieless request -- confirmed above that
        # the client never received nor stored a session cookie, so this
        # genuinely simulates a second cookie-blocked page load, not a
        # resumed browser session.
        state = await client.get("/api/session")

    assert state.json()["frames"].get("render-key", {}).get("complete") is True
