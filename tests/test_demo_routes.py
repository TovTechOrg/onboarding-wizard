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
