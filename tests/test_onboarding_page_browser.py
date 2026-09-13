"""Real browser-behavior tests for static/index.html, using Playwright's
sync API against the actual running FastAPI app (see live_app_url/browser/
page fixtures in tests/conftest.py). Plain `def test_...` functions, not
`async def` -- Playwright's sync API refuses to run inside an active
asyncio event loop, which every async test in this project runs inside
(asyncio_mode = "auto"). Complements, not replaces, the source-substring
convention every other onboarding-page test file uses (see
tests/test_onboarding_page.py's own module docstring) -- only tests that
need to verify real DOM/JS behavior belong here."""
from __future__ import annotations

import json
import time


def test_page_title_loads_over_a_real_browser(page, live_app_url):
    page.goto(live_app_url)
    assert page.title() == "Set up your own reviewer"


def test_uptime_pinger_blocked_state_reflects_render_service_url_presence(page, live_app_url):
    """Replaces tests/test_onboarding_page.py's
    test_frame5_blocked_state_reads_the_forward_contract_key, which only
    checked that the relevant function/constant names existed in the page
    source -- this checks the actual resulting visibility.

    frame-uptime-pinger starts locked/closed (a native <details> renders no
    content for a closed section, so is_visible() on anything inside it is
    always False regardless of the display style toggle this test cares
    about) -- unlockFrame() is the real function that opens it, and already
    calls refreshUptimePingerBlockedState() itself as part of unlocking."""
    page.goto(live_app_url)

    page.evaluate("sessionStorage.removeItem('onboarding.renderServiceUrl')")
    page.evaluate("unlockFrame('uptime-pinger')")
    assert page.is_visible("#uptime-pinger-blocked-section")
    assert not page.is_visible("#uptime-pinger-form-section")

    page.evaluate(
        "sessionStorage.setItem('onboarding.renderServiceUrl', 'https://example.onrender.com')"
    )
    page.evaluate("refreshUptimePingerBlockedState()")
    assert not page.is_visible("#uptime-pinger-blocked-section")
    assert page.is_visible("#uptime-pinger-form-section")


def test_supabase_insufficient_permissions_error_retranslates_on_language_switch(
    page, live_app_url
):
    """supabaseErrorForReason("insufficient_permissions", ...) combines our
    own translated copy with Supabase's relayed message -- unlike
    project_creation_rejected's pure-Supabase-text case, our half of this
    string must re-render on a language switch like every other tracked
    error, not stay frozen in whatever language was active when the error
    first appeared."""
    page.goto(live_app_url)
    # A closed/locked <details> renders no content at all (not just
    # visually hidden) -- unlock it first, same as the other browser tests
    # that need to read text out of a frame that starts locked/collapsed.
    page.evaluate("unlockFrame('supabase')")
    page.evaluate(
        "supabaseErrorForReason("
        "'insufficient_permissions', 'Missing required scope: Organizations')"
    )
    assert page.inner_text("#supabase-error") == (
        "Your token is missing a required permission. Check the permissions "
        "listed above and try again. Missing required scope: Organizations"
    )
    page.evaluate("applyLanguage('he')")
    assert page.inner_text("#supabase-error") == (
        "לטוקן שלכם חסרה הרשאה נדרשת. בדקו את ההרשאות המפורטות למעלה ונסו שוב. "
        "Missing required scope: Organizations"
    )


def test_restore_from_session_resumes_polling_for_a_supabase_project_without_a_connection_string(
    page, live_app_url
):
    """Replaces tests/test_onboarding_page.py's
    test_restore_from_session_resumes_polling_for_a_ref_without_a_connection_string,
    which only checked that three unrelated strings each appeared
    somewhere in the page source, not that they were wired together in
    the same code branch -- this drives the real restore-from-session
    flow and checks the actual resulting UI.

    frame-supabase starts locked/closed. showSupabaseProvisioning() sets
    its <details> `.open = true` directly (unlike unlockFrame(), it never
    touches `dataset.locked`), and guardLockedFrames()'s toggle listener
    immediately closes any frame whose dataset.locked is still "true" --
    exactly what real production code never hits, since this restore path
    only ever fires for a frame a visitor already unlocked earlier in the
    same flow. Pre-unlocking via an init script (registered, and so firing,
    before the page's own DOMContentLoaded listener that calls
    restoreFromSession()) reproduces that real precondition instead of
    fighting the guard."""
    session_body = {
        "frames": {
            "supabase": {
                "complete": False,
                "provisioning": True,
                "display": {"ref": "abcdefghijklmnopqrst", "name": "Test Project"},
            }
        }
    }

    def handle_session(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(session_body),
        )

    def handle_project_status(route):
        # Any non-terminal status -- pollUntilReady() just reschedules
        # itself 5s later on "pending", which this test doesn't wait for.
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"valid": True, "status": "COMING_UP"}),
        )

    page.add_init_script(
        "document.addEventListener('DOMContentLoaded', () => {"
        "  document.getElementById('frame-supabase').dataset.locked = 'false';"
        "});"
    )
    page.route(f"{live_app_url}/api/session", handle_session)
    page.route(f"{live_app_url}/api/supabase/project-status", handle_project_status)

    page.goto(live_app_url)

    page.wait_for_selector("#supabase-provisioning-section", state="visible")
    assert not page.is_visible("#supabase-connect-section")
    assert not page.is_visible("#supabase-org-section")


def test_project_status_insufficient_permissions_offers_a_retry_not_a_dead_end(
    page, live_app_url
):
    """Bug report: create-project already succeeded (the project genuinely
    exists in Supabase), but the same token lacks the permission
    project-status polling needs -- before this fix, handleProjectStatusResult
    reported the error but pollUntilReady only ever showed the "Check again"
    button on its own pending-timeout path, so an outright failure left the
    visitor stuck reading an error with no available action at all. The
    fix: any failure outcome (not just a pending timeout) reveals "Check
    again" so the visitor can retry once they've added the missing
    permission to the SAME token in Supabase's dashboard -- no need to
    re-paste a credential, which would risk orphaning the already-created
    project's ref/db_pass."""
    session_body = {
        "frames": {
            "supabase": {
                "complete": False,
                "provisioning": True,
                "display": {"ref": "abcdefghijklmnopqrst", "name": "Test Project"},
            }
        }
    }
    status_response = {
        "valid": False,
        "reason": "insufficient_permissions",
        "message": "Missing Organization Projects: Read",
    }

    def handle_session(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(session_body))

    def handle_project_status(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(status_response))

    page.add_init_script(
        "document.addEventListener('DOMContentLoaded', () => {"
        "  document.getElementById('frame-supabase').dataset.locked = 'false';"
        "});"
    )
    page.route(f"{live_app_url}/api/session", handle_session)
    page.route(f"{live_app_url}/api/supabase/project-status", handle_project_status)

    page.goto(live_app_url)

    page.wait_for_selector("#supabase-check-status-submit", state="visible")
    assert not page.is_disabled("#supabase-check-status-submit")
    assert "Missing Organization Projects: Read" in page.inner_text("#supabase-error")

    # The visitor fixed the permission in Supabase's dashboard on the same
    # token -- clicking "Check again" must be able to succeed now.
    status_response["valid"] = True
    status_response["status"] = "ACTIVE_HEALTHY"

    def handle_connection_info(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"valid": True}))

    page.route(f"{live_app_url}/api/supabase/connection-info", handle_connection_info)
    page.click("#supabase-check-status-submit")
    page.wait_for_selector('#frame-supabase[data-status="done"]')


def test_connection_info_insufficient_permissions_offers_a_retry_not_a_dead_end(
    page, live_app_url
):
    """Same bug, one step later: project-status reports ACTIVE_HEALTHY but
    connection-info then fails for a permission reason. Before this fix,
    handleProjectStatusResult unconditionally returned "ready" once status
    was healthy, regardless of whether the connection-info call inside it
    actually succeeded -- so pollUntilReady stopped polling as if the frame
    were done, with no error surfaced and no retry button, the worst
    version of the dead end."""
    session_body = {
        "frames": {
            "supabase": {
                "complete": False,
                "provisioning": True,
                "display": {"ref": "abcdefghijklmnopqrst", "name": "Test Project"},
            }
        }
    }
    connection_info_response = {
        "valid": False,
        "reason": "insufficient_permissions",
        "message": "Missing Connection Pooling: Read",
    }

    def handle_session(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(session_body))

    def handle_project_status(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"valid": True, "status": "ACTIVE_HEALTHY"}),
        )

    def handle_connection_info(route):
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(connection_info_response)
        )

    page.add_init_script(
        "document.addEventListener('DOMContentLoaded', () => {"
        "  document.getElementById('frame-supabase').dataset.locked = 'false';"
        "});"
    )
    page.route(f"{live_app_url}/api/session", handle_session)
    page.route(f"{live_app_url}/api/supabase/project-status", handle_project_status)
    page.route(f"{live_app_url}/api/supabase/connection-info", handle_connection_info)

    page.goto(live_app_url)

    page.wait_for_selector("#supabase-check-status-submit", state="visible")
    assert not page.is_disabled("#supabase-check-status-submit")
    assert "Missing Connection Pooling: Read" in page.inner_text("#supabase-error")
    assert page.get_attribute("#frame-supabase", "data-status") != "done"

    connection_info_response["valid"] = True
    page.click("#supabase-check-status-submit")
    page.wait_for_selector('#frame-supabase[data-status="done"]')


def test_restore_from_session_shows_deployed_service_link_when_server_reports_deployed(
    page, live_app_url
):
    """A revisit with an empty sessionStorage (new device, or the mobile
    tab-discard scenario the whole server-side-session redesign exists for)
    must still show the live service link -- not the Deploy button -- once
    the server itself has recorded the deploy as done. frame-render-deploy
    starts locked/closed; completeFrame's own keepOpen=true path (already
    exercised by the live in-page completion) is what opens it here too, so
    no pre-unlock init script is needed the way the supabase test above
    needs one."""
    session_body = {
        "frames": {
            "render-deploy": {
                "complete": True,
                "display": {"service_url": "https://example.onrender.com"},
            }
        }
    }

    def handle_session(route):
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(session_body)
        )

    page.route(f"{live_app_url}/api/session", handle_session)
    page.goto(live_app_url)

    page.wait_for_selector("#render-deploy-done-section", state="visible")
    assert not page.is_visible("#render-deploy-trigger-section")
    assert not page.is_visible("#render-deploy-polling-section")
    link = page.locator("#render-deploy-service-link")
    assert link.get_attribute("href") == "https://example.onrender.com"


def test_restore_from_session_resumes_deploy_polling_when_server_reports_pending(
    page, live_app_url
):
    session_body = {
        "frames": {
            "render-deploy": {
                "complete": False, "provisioning": True,
                "display": {"pending_deploy_id": "dep-1"},
            }
        }
    }

    def handle_session(route):
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(session_body)
        )

    def handle_deploy_status(route):
        # Any non-terminal status -- pollRenderDeployStatus() just
        # reschedules itself later on "in_progress", which this test
        # doesn't wait for.
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"valid": True, "status": "in_progress"}),
        )

    page.route(f"{live_app_url}/api/session", handle_session)
    page.route(f"{live_app_url}/api/render/deploy-status", handle_deploy_status)
    page.goto(live_app_url)

    page.wait_for_selector("#render-deploy-polling-section", state="visible")
    assert not page.is_visible("#render-deploy-trigger-section")
    assert not page.is_visible("#render-deploy-done-section")


def test_changing_an_earlier_frame_clears_the_persisted_deploy_state(page, live_app_url):
    """lockFrame("render-deploy") -- reached via relockDownstreamOf() when
    any of render-deploy's real prerequisites is redone -- must clear the
    server-side "deployed"/"pending_deploy_id" flags too, not just the
    local sessionStorage mirror; otherwise a reload mid-redo would resurrect
    the OLD deploy's done state from GET /api/session (see the "deployed"
    restore test above)."""
    calls = []

    def handle_clear(route):
        calls.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body='{"valid": true}')

    page.route(f"{live_app_url}/api/render/clear-deploy-state", handle_clear)
    page.goto(live_app_url)

    page.evaluate("beginChange('llm-provider')")
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert calls


def test_completing_a_frame_on_a_fresh_first_pass_does_not_unlock_unreached_dependents(
    page, live_app_url
):
    """Regression test for a real bug: on a brand-new session, completing
    render-service (right after render-key) must only unlock the next
    positional frame (dashboard-auth) -- github-app and uptime-pinger,
    both real FRAME_DEPENDENTS of render-service, must stay locked until
    the wizard actually reaches them, not jump open early just because
    their formal prereqsFor() (render-key + render-service) already happen
    to be done this early in a fresh linear run.

    maybeUnlockDependentsAfterRedo used to gate only render-deploy on
    "has this frame ever been reached before" (the render-deploy-only
    renderDeployReachedOnce flag) -- every other dependent had no such
    gate, so on a fresh session it unlocked github-app and uptime-pinger
    the moment render-service completed, since dataset.locked === "true"
    is also true for a frame that simply hasn't been reached yet. Gating
    every dependent on the generalized everReached set fixes this."""
    page.goto(live_app_url)

    page.evaluate("completeFrame('render-key', 'owner_prefix', 'test-owner')")
    page.evaluate(
        "completeFrame('render-service', 'url_prefix', 'https://example.onrender.com')"
    )

    assert page.eval_on_selector("#frame-dashboard-auth", "el => el.dataset.locked") == "false"
    assert page.eval_on_selector("#frame-github-app", "el => el.dataset.locked") == "true"
    assert page.eval_on_selector("#frame-uptime-pinger", "el => el.dataset.locked") == "true"


def test_language_switch_sets_dir_for_rtl(page, live_app_url):
    """Replaces tests/test_onboarding_i18n.py's test of the same name,
    which asserted an exact literal source line rather than the actual
    resulting behavior -- more brittle to a harmless refactor of that
    line, and only a proxy for whether dir actually changes."""
    page.goto(live_app_url)
    assert page.get_attribute("html", "dir") == "ltr"

    page.click("#langToggleBtn")
    page.check('input[name="lang"][value="he"]')

    assert page.get_attribute("html", "dir") == "rtl"
