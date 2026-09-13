"""Tests for GET / — the onboarding wizard's static page shell: the frame
state machine, locking, and the "Change" re-edit path. Content-substring
checks against the served HTML/JS (this repo's existing convention for
single-file static pages — see tests/test_dashboard_page.py), not a JS
execution harness."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

import github_client
from main import app

FRAME_IDS = [
    "render-key",
    "github-app",
    "supabase",
    "llm-provider",
    "uptime-pinger",
    "render-deploy",
]


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_index_serves_all_six_frames_in_order():
    client = await _client()
    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.text
    positions = [body.index(f'id="frame-{fid}"') for fid in FRAME_IDS]
    assert positions == sorted(positions)


async def test_only_the_render_key_frame_starts_unlocked():
    client = await _client()
    body = (await client.get("/")).text
    assert (
        'id="frame-render-key" class="frame" data-status="ready" data-locked="false" open'
    ) in body
    for fid in FRAME_IDS[1:]:
        assert (f'id="frame-{fid}" class="frame" data-status="locked" data-locked="true"') in body


async def test_locked_frames_cannot_be_toggled_open():
    client = await _client()
    body = (await client.get("/")).text
    assert "function guardLockedFrames" in body
    assert 'el.dataset.locked === "true"' in body


async def test_frame_detail_is_a_separate_element_from_the_badge():
    """A long detail value (an owner name, a deployed URL) must not wrap
    inline with the title/badge/Change-button row and push the Change button
    onto a second line -- .frame-detail's flex-basis: 100% forces it onto its
    own line below instead, so every frame needs both elements and they must
    stay distinct (never re-merged into one concatenated .frame-badge
    string)."""
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('class="frame-detail"') == body.count('class="frame-badge"')
    assert ".frame-detail { flex-basis: 100%" in body


async def test_render_badge_writes_label_and_detail_to_separate_elements():
    client = await _client()
    body = (await client.get("/")).text
    fn_body = body[body.index("function renderBadge") : body.index("function refreshFrameBadges")]
    assert "badgeEl(id).textContent = label;" in fn_body
    assert "detailEl(id).textContent" in fn_body


async def test_render_key_leaves_the_page_exactly_once():
    """The key must only ever transit the one relay call — anything else
    would be a second, unaudited exit path for a visitor's credential."""
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/render/validate-key"') == 1


async def test_validate_github_app_leaves_the_page_exactly_once():
    """The App ID/private key must only ever transit the one relay call —
    anything else would be a second, unaudited exit path for a visitor's
    credential."""
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/github/validate-app"') == 1


async def test_render_key_never_persists_client_side_at_all():
    """The Render API key is validated once (frame 1, the wizard's session
    entry point) and persisted server-side by /api/render/validate-key --
    it never needs to be resent, so it never touches sessionStorage OR
    localStorage on the client at all anymore."""
    client = await _client()
    body = (await client.get("/")).text
    assert 'STORAGE_KEYS["render-key"]' not in body


async def test_validate_render_key_rejects_an_empty_key_client_side():
    """An empty submission must not reach the network -- it shows
    err_empty_key and returns before fetch() is ever called."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function validateRenderKey()")
    fn_body = body[fn_start : fn_start + 600]
    assert 'currentRenderKeyErrorKey = "err_empty_key"' in fn_body
    assert fn_body.index('"err_empty_key"') < fn_body.index("fetch(")


async def test_render_key_input_submits_on_enter():
    """A mobile on-screen keyboard's "Go"/Enter key must submit the key --
    only a click on the button used to work. Handled generically now by
    handleFrameEnterKey()/FRAME_ENTER_CONFIG, wired via a single delegated
    keydown listener rather than one per input."""
    client = await _client()
    body = (await client.get("/")).text
    assert 'document.addEventListener("keydown", handleFrameEnterKey)' in body
    assert 'const FRAME_ENTER_CONFIG = {' in body
    assert '"render-key": {' in body


async def test_frame_enter_config_advances_focus_or_submits_per_frame():
    """Every frame with at least one text/select field must be represented
    in FRAME_ENTER_CONFIG so Enter both advances focus between its fields
    and submits from the last one -- not just render-key's single-field
    case."""
    client = await _client()
    body = (await client.get("/")).text
    config_start = body.index("const FRAME_ENTER_CONFIG = {")
    config_body = body[config_start : body.index("function handleFrameEnterKey")]
    for frame_id in [
        "render-key", "render-service", "dashboard-auth", "github-app",
        "supabase", "llm-provider", "uptime-pinger",
    ]:
        assert f'"{frame_id}": {{' in config_body

    handler_start = body.index("function handleFrameEnterKey")
    handler_body = body[handler_start : body.index('document.addEventListener("DOMContentLoaded"')]
    assert "event.preventDefault();" in handler_body
    assert "document.getElementById(fields[idx + 1]).focus();" in handler_body
    assert "if (btn && !btn.disabled) btn.click();" in handler_body


async def test_completing_a_frame_unlocks_the_next_one():
    client = await _client()
    body = (await client.get("/")).text
    assert "function completeFrame" in body
    assert 'if (next && frameState[next].status === "locked") unlockFrame(next);' in body


async def test_unlocking_a_frame_opens_it():
    """A newly-reachable frame must auto-expand, not just become clickable —
    otherwise the visitor has no visual cue that a new step is ready."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("function unlockFrame")
    fn_body = body[fn_start : fn_start + 300]
    assert "el.open = true;" in fn_body


async def test_done_frames_show_an_explicit_change_control():
    client = await _client()
    body = (await client.get("/")).text
    assert 'class="frame-change"' in body
    assert "function beginChange" in body


async def test_changing_a_frame_relocks_only_its_real_dependents():
    """relockDownstreamOf is dependency-driven, not "everything positioned
    after id" — changing llm-provider must not force redoing uptime-pinger,
    which reads none of llm-provider's data."""
    client = await _client()
    body = (await client.get("/")).text
    assert "function relockDownstreamOf" in body
    assert "relockDownstreamOf(id)" in body
    assert (
        '"render-key": ["render-service", "github-app", "uptime-pinger", "render-deploy"]'
        in body
    )
    assert '"render-service": ["github-app", "uptime-pinger", "render-deploy"]' in body
    assert '"dashboard-auth": ["render-deploy"]' in body
    assert '"github-app": ["render-deploy"]' in body
    assert '"supabase": ["render-deploy"]' in body
    assert '"llm-provider": ["render-deploy"]' in body
    assert '"uptime-pinger": []' in body


async def test_a_redo_of_a_leaf_frame_can_unlock_render_deploy_without_uptime_pinger():
    """The concrete scenario that motivated this mechanism: a visitor who
    only changes the LLM provider + API key must be able to redeploy from
    the last frame without also redoing the uptime monitor."""
    client = await _client()
    body = (await client.get("/")).text
    assert "function maybeUnlockDependentsAfterRedo" in body
    assert "function prereqsFor(frameId)" in body
    assert "const everReached = new Set();" in body
    assert "everReached.add(id);" in body
    # Called from completeFrame for every frame -- no more render-deploy
    # special case, see the next test for why.
    assert "maybeUnlockDependentsAfterRedo(id);" in body


async def test_redoing_render_service_unlocks_github_app_even_when_dashboard_auth_is_already_done():
    """Regression test for a real bug: the previous render-deploy-only
    version of maybeUnlockDependentsAfterRedo (then named
    maybeUnlockRenderDeployAfterRedo) left github-app permanently locked
    after redoing render-service, because completeFrame's positional
    "unlock the next frame" chain only ever checks the single immediate
    next frame (dashboard-auth) -- which was already "done" and not
    locked, so the chain stopped there and never reached github-app, a
    real dependent sitting further down. The only workaround was redoing
    the unrelated, already-correct dashboard-auth frame just to trip the
    chain past it. The general mechanism must check every real dependent
    of the completed frame directly, not rely on what sits positionally
    in between."""
    client = await _client()
    body = (await client.get("/")).text
    # maybeUnlockDependentsAfterRedo must walk FRAME_DEPENDENTS[completedId]
    # directly (github-app is a real dependent of render-service) rather
    # than only ever checking nextFrame(completedId) (dashboard-auth).
    fn_start = body.index("function maybeUnlockDependentsAfterRedo")
    fn_body = body[fn_start : body.index("\n  }", fn_start)]
    assert "FRAME_DEPENDENTS[completedId]" in fn_body
    assert "dataset.locked" in fn_body
    assert "prereqsFor(depId)" in fn_body


async def test_submitted_key_is_cleared_from_the_input_after_success():
    client = await _client()
    body = (await client.get("/")).text
    assert 'input.value = "";' in body


async def test_page_has_a_mobile_breakpoint():
    client = await _client()
    body = (await client.get("/")).text
    assert "@media (max-width: 480px)" in body


async def test_frame2_has_app_id_and_key_file_inputs():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="github-app-id-input"' in body
    assert 'id="github-app-key-file-input"' in body
    assert 'type="file"' in body[body.index('id="github-app-key-file-input"') - 40 :]
    assert 'id="github-app-validate-submit"' in body


async def test_frame2_no_longer_has_a_name_or_install_section():
    """The old two-section (create/install) shape is fully replaced by one
    instructions+credentials+checklist section."""
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="github-app-name-input"' not in body
    assert 'id="github-app-create-submit"' not in body
    assert 'id="github-app-install-section"' not in body
    assert 'id="github-app-installation-id-input"' not in body
    assert 'id="github-app-install-submit"' not in body


async def test_frame2_strings_present_in_both_languages():
    client = await _client()
    body = (await client.get("/")).text
    for key in (
        "frame2_instructions",
        "frame2_step_create",
        "frame2_step_webhook_url",
        "frame2_step_webhook_secret",
        "frame2_step_permissions",
        "frame2_step_install",
        "frame2_app_id_label",
        "frame2_private_key_label",
        "err_github_app_id_invalid",
        "err_github_no_file",
        "err_github_invalid_key_file",
    ):
        assert f"{key}:" in body
        assert body.count(f"{key}:") == 2, f"{key} missing a translation"


async def test_page_offers_no_route_to_github_app_creation_either():
    """Extends the existing install-page policy to App creation too: after
    another suspension during this frame even with the install-page fix
    already shipped (see ISSUES.md), no github.com URL of any kind is
    rendered in frame 4's own markup or JS — creation is described only as
    breadcrumb text. (Scoped to frame 4 specifically: other frames
    legitimately reference github.com, e.g. the Render-service frame's
    default repo URL suggestion.)"""
    client = await _client()
    body = (await client.get("/")).text
    frame_start = body.index('id="frame-github-app"')
    frame_markup = body[frame_start : body.index("</details>", frame_start)]
    assert "github.com" not in frame_markup
    js_block = body[
        body.index("function resetGithubAppSetupSection") : body.index("function base64UrlEncode")
    ]
    assert "github.com" not in js_block


async def test_page_offers_no_route_to_the_install_page_at_all():
    """Five throwaway GitHub accounts were suspended for a ToS violation at
    this step (see ISSUES.md): three via an automatic location.href redirect,
    one via an <a> with rel="noreferrer"/referrerpolicy="no-referrer", and one
    via the visitor pasting the URL into their own address bar. Reaching
    /installations/new from anywhere but inside GitHub appears to be what
    trips it, so the page must offer no route -- no redirect, no link, and no
    URL text to copy either."""
    client = await _client()
    body = (await client.get("/")).text
    # The App-install URL shape specifically. Not a bare "installations/new"
    # check: the code comment explaining this rule names that path.
    assert "github.com/apps/" not in body
    assert 'href="https://github.com' not in body
    # No navigation call anywhere on the page targets GitHub at all -- a
    # whole-body scan rather than one scoped to a specific function, since
    # this frame no longer has a dedicated "install" function to scope to
    # (App creation and installation are both fully manual now; see
    # test_page_offers_no_route_to_github_app_creation_either).
    for pattern in ("location.href =", "location.assign(", "location.replace("):
        idx = body.find(pattern)
        while idx != -1:
            snippet = body[idx : idx + 200]
            assert "github" not in snippet.lower(), f"{pattern} appears to target GitHub: {snippet}"
            idx = body.find(pattern, idx + 1)


async def test_locked_frame_click_is_prevented_before_toggle():
    """The lock guard must call preventDefault() on the summary's click --
    reacting to the `toggle` event afterward (the previous implementation)
    lets the native <details> element visibly open for a frame before JS
    closes it again, a flicker the visitor can see."""
    client = await _client()
    body = (await client.get("/")).text
    guard_body = body[
        body.index("function guardLockedFrames") : body.index("function attachChangeButtons")
    ]
    assert 'addEventListener("click"' in guard_body
    assert "event.preventDefault()" in guard_body
    assert 'dataset.locked === "true"' in guard_body


async def test_locked_frames_are_visually_dimmed():
    client = await _client()
    body = (await client.get("/")).text
    assert 'details.frame[data-locked="true"] { opacity:' in body


async def test_required_permissions_match_github_client():
    """The page's JS REQUIRED_PERMISSIONS/REQUIRED_EVENTS must mirror
    github_client.py's REQUIRED_PERMISSIONS/REQUIRED_EVENTS, which is the
    single source of truth (a paired comment in each file points at the
    other; there is no shared JS/Python boundary a real shared constant
    could live in).

    This reads the actual Python constants rather than re-listing the same
    literals a second time: a copy of the literals here would keep passing
    after someone edited only github_client.py, which is exactly the drift
    this test exists to catch."""
    client = await _client()
    body = (await client.get("/")).text
    for name, level in github_client.REQUIRED_PERMISSIONS.items():
        assert f'{name}: "{level}"' in body, f"page is missing permission {name}: {level}"
    for event in github_client.REQUIRED_EVENTS:
        assert f'"{event}"' in body, f"page is missing required event {event}"
    js_perms = body[body.index("const REQUIRED_PERMISSIONS = {") :]
    js_perms = js_perms[: js_perms.index("};")]
    assert js_perms.count(":") == len(github_client.REQUIRED_PERMISSIONS)
    js_events = body[body.index("const REQUIRED_EVENTS = [") :]
    js_events = js_events[: js_events.index("];")]
    assert js_events.count('"') == 2 * len(github_client.REQUIRED_EVENTS)


async def test_github_app_credential_never_persists_to_local_storage():
    client = await _client()
    body = (await client.get("/")).text
    assert 'localStorage.setItem(STORAGE_KEYS["github-app"]' not in body
    assert 'localStorage.getItem(STORAGE_KEYS["github-app"]' not in body


async def test_frame2_has_a_reset_path_wired_into_lock_and_change():
    """A locked-then-reopened or Change-clicked frame 4 must not strand a
    stale App ID/checklist on screen from a previous attempt."""
    client = await _client()
    body = (await client.get("/")).text
    assert "function resetGithubAppSetupSection" in body

    lock_body = body[body.index("function lockFrame") : body.index("function unlockFrame")]
    assert "resetGithubAppSetupSection()" in lock_body
    assert 'id === "github-app"' in lock_body

    change_body = body[
        body.index("function beginChange") : body.index("async function validateRenderKey")
    ]
    assert "resetGithubAppSetupSection()" in change_body


async def test_validate_github_app_refuses_without_a_render_service_url():
    """The webhook URL instruction and the request both depend on the
    Render service's URL already existing -- unreachable in normal
    sequential flow (that frame completes two frames earlier), but guards
    the same corrupted/hand-edited sessionStorage case the UptimeRobot and
    Supabase frames' own equivalents guard."""
    client = await _client()
    body = (await client.get("/")).text
    validate_fn = body[
        body.index("async function validateGithubApp") : body.index(
            "function renderGithubAppChecklist"
        )
    ]
    assert "if (!renderService || !renderService.service_url) {" in validate_fn
    assert 'githubAppError("err_github_no_render_service");' in validate_fn


async def test_validate_github_app_only_completes_the_frame_when_all_ok():
    """all_ok gates completion -- a partial pass must not complete the
    frame (the server itself also refuses to persist a not-all_ok result,
    see test_onboarding_router.py::test_validate_app_does_not_persist_when_not_all_ok)."""
    client = await _client()
    body = (await client.get("/")).text
    validate_fn = body[
        body.index("async function validateGithubApp") : body.index(
            "function renderGithubAppChecklist"
        )
    ]
    assert "if (!body.all_ok)" in validate_fn
    assert validate_fn.index("if (!body.all_ok)") < validate_fn.index('completeFrame("github-app"')


async def test_webhook_secret_is_generated_client_side_not_pasted_back():
    client = await _client()
    body = (await client.get("/")).text
    assert "function ensureGithubAppWebhookSecret" in body
    assert 'id="github-app-webhook-secret"' in body
    assert 'id="github-app-webhook-secret-input"' not in body


async def test_validate_supabase_key_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/supabase/validate-key"') == 1


async def test_create_project_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('callSupabaseRelay("/api/supabase/create-project"') == 1


async def test_frame3_has_a_key_input_and_validate_button():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="supabase-key-input"' in body
    assert 'id="supabase-key-submit"' in body


async def test_frame3_has_an_org_picker_and_a_name_input():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="supabase-org-select"' in body
    assert 'id="supabase-org-submit"' in body
    assert 'id="supabase-project-name-input"' in body


async def test_frame3_strings_present_in_both_languages():
    client = await _client()
    body = (await client.get("/")).text
    for key in (
        "frame3_instructions",
        "frame3_name_placeholder",
        "validate_supabase_key_button",
        "frame3_org_instructions",
        "create_project_button",
        "err_supabase_name_empty",
        "err_supabase_empty_key",
        "err_supabase_invalid_key",
        "err_supabase_callback_invalid",
        "err_supabase_insufficient_permissions",
    ):
        assert f"{key}:" in body
    assert body.count("validate_supabase_key_button:") == 2  # STRINGS.en + STRINGS.he


async def test_frame3_instructions_recommend_a_scoped_token_with_named_permissions():
    """Scoped tokens are Supabase's own recommended choice for automation,
    but are in gradual/alpha rollout -- not every account can create one --
    so the wizard still accepts a Classic token (no server-side gate) and
    only recommends Scoped, naming the exact permissions to grant so a
    later insufficient_permissions failure is diagnosable."""
    client = await _client()
    body = (await client.get("/")).text
    assert "Scoped" in body
    assert "Organizations" in body
    assert "Organization Projects" in body
    assert "Connection Pooling" in body


async def test_reactive_refresh_helper_present():
    client = await _client()
    body = (await client.get("/")).text
    assert "async function callSupabaseRelay" in body
    assert '"unauthorized"' in body


async def test_project_status_leaves_the_page_exactly_once():
    """Like Task 6's list-organizations/create-project tests: this endpoint
    goes through the shared callSupabaseRelay helper, not a direct fetch()
    call, so the audit target is the endpoint string appearing exactly once
    as a callSupabaseRelay(...) argument."""
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('callSupabaseRelay("/api/supabase/project-status"') == 1


async def test_connection_info_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('callSupabaseRelay("/api/supabase/connection-info"') == 1


async def test_frame3_has_a_check_again_button():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="supabase-check-status-submit"' in body


async def test_polling_uses_a_five_second_interval_and_five_minute_timeout():
    client = await _client()
    body = (await client.get("/")).text
    assert "SUPABASE_POLL_INTERVAL_MS = 5000" in body
    assert "SUPABASE_POLL_TIMEOUT_MS = 300000" in body


async def test_target_status_is_active_healthy_and_init_failed_is_terminal():
    client = await _client()
    body = (await client.get("/")).text
    assert '"ACTIVE_HEALTHY"' in body
    assert '"INIT_FAILED"' in body


async def test_supabase_credential_never_persists_to_local_storage():
    client = await _client()
    body = (await client.get("/")).text
    assert 'localStorage.setItem(STORAGE_KEYS["supabase"]' not in body
    assert 'localStorage.getItem(STORAGE_KEYS["supabase"]' not in body


async def test_stored_supabase_credentials_are_parsed_defensively():
    """Same guard as readStoredGithubApp() -- a corrupted sessionStorage
    value must not throw out of DOMContentLoaded and take every later
    listener with it."""
    client = await _client()
    body = (await client.get("/")).text
    assert "function readStoredSupabase" in body
    helper_body = body[body.index("function readStoredSupabase") :]
    helper_body = helper_body[: helper_body.index("\n  }")]
    assert "try {" in helper_body
    assert "catch" in helper_body
    # Every other call site must go through the helper -- the only direct
    # JSON.parse of this key left on the page is inside the helper itself.
    assert body.count('JSON.parse(sessionStorage.getItem(STORAGE_KEYS["supabase"])') == 1


async def test_terminal_supabase_errors_reset_the_connect_section():
    """INIT_FAILED (from handleProjectStatusResult) and an "unauthorized" or
    "no_session" server response (the server-side session's stored token is
    dead, or there's no session record at all) are dead ends --
    resetSupabaseConnectSection() must run before the error is shown so
    "Connect Supabase" is back on screen to restart the flow, not just fold
    into the existing error-clearing convention."""
    client = await _client()
    body = (await client.get("/")).text

    init_failed_start = body.index('body.status === "INIT_FAILED"')
    init_failed_body = body[init_failed_start : body.index('return "pending";')]
    reset_pos = init_failed_body.index("resetSupabaseConnectSection()")
    error_pos = init_failed_body.index('supabaseError("err_supabase_provisioning_failed")')
    assert reset_pos < error_pos

    reason_fn_start = body.index("function supabaseErrorForReason")
    reason_fn_body = body[reason_fn_start : body.index("async function callSupabaseRelay")]

    unauthorized_branch = reason_fn_body[
        reason_fn_body.index('if (reason === "unauthorized" || reason === "no_session")') :
    ]
    assert "resetSupabaseConnectSection();" in unauthorized_branch.split("const key = {")[0]
    assert 'no_session: "err_no_session"' in unauthorized_branch.split("const key = {")[1]


async def test_reset_supabase_connect_section_reenables_the_validate_button():
    """supabase-key-submit is disabled by every validateSupabaseKey() attempt
    and only re-enabled by that same function's own success/failure
    branches. Any other path that lands back on supabase-connect-section
    (zero orgs, unauthorized/no_session, INIT_FAILED, a lock/unlock cycle)
    goes through resetSupabaseConnectSection(), which must also re-enable
    this button -- otherwise the visitor is stuck on the reconnect screen
    with no usable button at all."""
    client = await _client()
    body = (await client.get("/")).text
    reset_fn_start = body.index("function resetSupabaseConnectSection")
    reset_fn_body = body[reset_fn_start : body.index("function supabaseError(key)")]
    assert 'document.getElementById("supabase-key-submit").disabled = false;' in reset_fn_body
    assert 'document.getElementById("supabase-org-submit").disabled = false;' in reset_fn_body


async def test_project_creation_rejected_stays_on_org_section():
    """A rejected create-project call (e.g. a duplicate project name, or a
    plan-level project cap) is recoverable without re-entering the access
    token -- the org/name section must stay on screen so the visitor can
    retry with a different name or org, not be forced back to
    "Connect Supabase" with no way to re-enable its Validate button."""
    client = await _client()
    body = (await client.get("/")).text

    reason_fn_start = body.index("function supabaseErrorForReason")
    reason_fn_body = body[reason_fn_start : body.index("async function callSupabaseRelay")]

    rejected_branch = reason_fn_body[
        : reason_fn_body.index('if (reason === "unauthorized" || reason === "no_session")')
    ]
    assert "resetSupabaseConnectSection()" not in rejected_branch
    assert 'document.getElementById("supabase-error").textContent = message;' in rejected_branch


async def test_org_section_shown_after_key_validation_opens_the_frame_and_updates_its_badge():
    client = await _client()
    body = (await client.get("/")).text
    assert "function showSupabaseOrgSection" in body
    show_body = body[
        body.index("function showSupabaseOrgSection") : body.index(
            "async function confirmSupabaseOrg"
        )
    ]
    assert 'frameEl("supabase").open = true' in show_body
    assert 'setFrameStatus("supabase", "choosing_org")' in show_body
    assert body.count("badge_choosing_org:") == 2  # STRINGS.en + STRINGS.he


async def test_confirm_supabase_org_badges_provisioning_before_the_request_not_after():
    """create-project's own request IS the provisioning call -- Supabase
    provisions the project synchronously inside it, before the response
    ever comes back. Badging "provisioning" only once that request
    resolves (i.e. inside kickOffProjectCreation/showSupabaseProvisioning)
    left the header stuck on "Choose an organization" for the entire round
    trip, which can take a few seconds."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function confirmSupabaseOrg")
    fn_body = body[fn_start : body.index("async function kickOffProjectCreation")]
    assert 'setFrameStatus("supabase", "provisioning");' in fn_body
    assert fn_body.index('setFrameStatus("supabase", "provisioning");') < fn_body.index(
        "await kickOffProjectCreation(select.value, name);"
    )


async def test_connection_info_missing_local_state_shows_an_error_not_a_silent_stall():
    """Polling already reported "ready" and stopped by this point -- a bare
    return here used to leave the frame stuck at "Provisioning..." forever
    with no error and no button."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function fetchSupabaseConnectionInfo")
    fn_body = body[fn_start : body.index("function restoreFromSession")]
    guard_body = fn_body[: fn_body.index("const body = await callSupabaseRelay(")]
    assert "return;" in guard_body
    assert 'supabaseError("err_supabase_callback_invalid");' in guard_body


async def test_frame4_has_a_three_way_provider_selector():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="llm-provider-choice-gemini"' in body
    assert 'id="llm-provider-choice-groq"' in body
    assert 'id="llm-provider-choice-vertex"' in body


async def test_frame4_has_credential_inputs_and_model_picker():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="llm-provider-api-key-input"' in body
    assert 'id="llm-provider-file-input"' in body
    assert 'id="llm-provider-model-select"' in body
    assert 'id="llm-provider-continue-submit"' in body


async def test_gemini_llm_endpoint_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('endpoint = "/api/llm/gemini/list-models"') == 1


async def test_groq_llm_endpoint_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('endpoint = "/api/llm/groq/list-models"') == 1


async def test_llm_provider_credential_has_exactly_one_fetch_call_site():
    """All three providers (Gemini/Groq here, Vertex in Task 5) share one
    fetch() call site in validateLlmProviderCredential() rather than one
    fetch() per provider — the per-provider endpoint tests above establish
    each credential still has exactly one path to that shared call site,
    the same one-exit-path invariant CLAUDE.md documents for
    every other credential-carrying fetch on this page, adapted for this
    frame's shared-call-site shape.

    Scoped to validateLlmProviderCredential()'s own body rather than the
    whole page: callSupabaseRelay() (frame 3, pre-existing) also names its
    parameter `endpoint` and its fetch call has the identical literal
    shape "await fetch(endpoint, {" purely coincidentally -- an unscoped
    count would false-positive against that unrelated function."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function validateLlmProviderCredential")
    fn_body = body[fn_start : body.index("function confirmLlmProviderModel")]
    assert fn_body.count("await fetch(endpoint, {") == 1


async def test_llm_provider_never_persists_to_local_storage():
    client = await _client()
    body = (await client.get("/")).text
    assert 'sessionStorage.setItem(STORAGE_KEYS["llm-provider"]' in body
    assert 'localStorage.setItem(STORAGE_KEYS["llm-provider"]' not in body


async def test_model_confirm_requires_both_credential_and_model():
    """Frame unlock gate: both a live-validated credential AND an explicit
    model pick are required (spec section 2) — no fallback if either is
    missing."""
    client = await _client()
    body = (await client.get("/")).text
    assert "if (!model || !pendingLlmProviderCredential)" in body


async def test_frame4_locked_by_default():
    client = await _client()
    body = (await client.get("/")).text
    assert ('id="frame-llm-provider" class="frame" data-status="locked" data-locked="true"') in body


async def test_empty_model_list_shows_dedicated_message():
    """A credential that validates but returns zero eligible models is a
    dead end under the "both required" gate (spec section 2) — it gets its
    own message rather than silently showing an empty dropdown."""
    client = await _client()
    body = (await client.get("/")).text
    assert "if (!models.length) {" in body
    assert 'llmProviderError("err_llm_no_models_available");' in body


async def test_llm_provider_error_sets_frame_status_to_error():
    """Structural sibling of githubAppError()/supabaseError() -- both call
    setFrameStatus(id, "error") before writing the error text. Without the
    equivalent call here, the frame-llm-provider badge never flips to the
    error state on a rejected credential or missing pick."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("function llmProviderError(key)")
    fn_body = body[fn_start : body.index("function llmProviderErrorForReason")]
    assert 'setFrameStatus("llm-provider", "error");' in fn_body


async def test_model_select_has_a_disabled_placeholder_forcing_an_explicit_pick():
    """A <select> with only real <option>s defaults .value to the first one
    -- satisfying confirmLlmProviderModel()'s "model" half of the unlock
    gate the instant any model exists, even if the visitor never touches the
    dropdown. A disabled, pre-selected placeholder option with value=""
    keeps .value falsy until an explicit pick is made."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("function showLlmProviderModels")
    fn_body = body[fn_start : body.index("async function validateLlmProviderCredential")]
    assert 'placeholder.value = "";' in fn_body
    assert "placeholder.disabled = true;" in fn_body
    assert "placeholder.selected = true;" in fn_body
    assert 'placeholder.textContent = t("frame4_model_placeholder");' in fn_body
    # Placeholder must be appended before the real models are, not after.
    placeholder_append_pos = fn_body.index("select.appendChild(placeholder);")
    models_forEach_pos = fn_body.index("sortedModels.forEach((m) => {")
    assert placeholder_append_pos < models_forEach_pos
    assert body.count("frame4_model_placeholder:") == 2  # STRINGS.en + STRINGS.he


async def test_model_select_options_are_sorted_alphabetically():
    """Same sort as pr-review-bot's own dashboard model dropdown
    (dashboard/static/dashboard.html's modelOptionsHtml) -- a provider's
    catalog order is not meaningful to a visitor picking a model by name."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("function showLlmProviderModels")
    fn_body = body[fn_start : body.index("async function validateLlmProviderCredential")]
    assert "const sortedModels = [...models].sort((a, b) => a.localeCompare(b));" in fn_body
    sort_pos = fn_body.index("const sortedModels =")
    forEach_pos = fn_body.index("sortedModels.forEach((m) => {")
    assert sort_pos < forEach_pos


async def test_vertex_llm_endpoint_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('endpoint = "/api/llm/vertex/list-models"') == 1


async def test_vertex_file_is_read_via_filereader_and_base64_encoded():
    client = await _client()
    body = (await client.get("/")).text
    assert "function readFileAsBase64" in body
    assert "new FileReader()" in body
    assert "readAsDataURL(file)" in body


async def test_vertex_credential_gets_a_client_side_json_sanity_check():
    """Catches "wrong file entirely" before any network call — spec
    section 3 step 2."""
    client = await _client()
    body = (await client.get("/")).text
    assert "function base64ToJsonSanityCheck" in body
    assert "JSON.parse(decoded)" in body


async def test_vertex_credential_stored_under_the_spec_field_name():
    """Storage field is gcp_service_account_key_b64 (spec section 5),
    distinct from the wire field service_account_key_b64 (spec section 4)
    the relay endpoint expects — the frame maps between the two."""
    client = await _client()
    body = (await client.get("/")).text
    assert "credentialFragment = {gcp_service_account_key_b64: b64}" in body


async def test_language_switch_retranslates_llm_provider_error():
    """Every other frame re-renders its held error key in applyLanguage();
    without this, currentLlmProviderErrorKey is written in four places and
    read nowhere, and frame 4's error text keeps the old language."""
    client = await _client()
    body = (await client.get("/")).text
    assert (
        'document.getElementById("llm-provider-error").textContent = t(currentLlmProviderErrorKey);'
        in body
    )


async def test_switching_llm_provider_clears_stale_credential_input():
    """The api-key field is one shared DOM element across Gemini and Groq,
    so without this a key typed for one provider is still sitting there
    (hidden) to be submitted to the other."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("function handleLlmProviderChoice")
    fn_body = body[fn_start : body.index("\n  }\n", fn_start)]
    assert 'apiKeyInput.value = "";' in fn_body
    assert 'fileInput.value = "";' in fn_body


async def test_vertex_frame_offers_project_and_region_dropdowns():
    client = await _client()
    body = (await client.get("/")).text
    assert '<select id="llm-provider-project-select">' in body
    assert '<select id="llm-provider-location-select">' in body


async def test_project_options_are_sorted_alphabetically():
    """Same rule as the model dropdown -- a project list carries no authored
    order, so it sorts by name."""
    client = await _client()
    body = (await client.get("/")).text
    assert "const sortedProjects = [...projects].sort((a, b) => a.localeCompare(b));" in body


async def test_location_options_are_rendered_in_contract_order_not_sorted():
    """The contract's order is a curated geographic grouping with `global`
    last. Sorting it would scatter `global` into the g's and bury
    us-central1 -- so this asserts the absence of a sort, deliberately."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("function showVertexLocations")
    fn_body = body[fn_start : body.index("}", body.index("locations.forEach"))]
    assert "sort(" not in fn_body


async def test_vertex_list_models_endpoint_still_leaves_the_page_exactly_once():
    """Both the first validate and every dropdown-change re-list go through
    one shared helper -- one credential, one exit path."""
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('endpoint = "/api/llm/vertex/list-models"') == 1


async def test_changing_a_dropdown_clears_the_selected_model():
    """A model verified against one project/region says nothing about
    another pair."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("function onVertexPairChanged")
    fn_body = body[fn_start : fn_start + 800]
    assert 'document.getElementById("llm-provider-model-select").innerHTML = "";' in fn_body


async def test_relay_vertex_list_models_distinguishes_network_failure_from_bad_response():
    """A thrown fetch() (offline, DNS) is "can't reach the wizard's own
    server" (err_network); a received-but-bad response is "the provider
    itself is unreachable" (err_llm_unreachable via provider_unreachable)
    -- validateLlmProviderCredential's gemini/groq branch already makes
    this distinction, and relayVertexListModels must not collapse it."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function relayVertexListModels")
    fn_body = body[fn_start : body.index("function showLlmProviderModels")]
    assert 'reason: "network_unreachable"' in fn_body
    assert 'reason: "provider_unreachable"' in fn_body
    assert 'network_unreachable: "err_network"' in body


async def test_vertex_validate_awaits_the_locations_fetch_before_rendering():
    """loadVertexLocations() is kindled fire-and-forget at DOMContentLoaded,
    but a visitor who validates faster than that GET resolves must not see
    an empty, submittable region dropdown -- validateVertexCredential must
    await the same promise (not just read the possibly-still-default
    vertexLocationOptions) before calling showVertexLocations."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function validateVertexCredential")
    fn_body = body[fn_start : body.index("async function onVertexPairChanged")]
    assert "loadVertexLocations()" in fn_body
    await_pos = fn_body.index("loadVertexLocations()")
    show_pos = fn_body.index("showVertexLocations(")
    assert await_pos < show_pos


async def test_vertex_pending_credential_is_set_even_when_the_default_pair_has_no_models():
    """showLlmProviderModels returns early (never reaching its own
    pendingLlmProviderCredential assignment) when the listing comes back
    empty -- so validateVertexCredential must set it itself, before that
    call, or a zero-model default pair leaves onVertexPairChanged's own
    "if (!pendingLlmProviderCredential) return;" guard permanently dead:
    the visitor would see the project/region dropdowns rendered but unable
    to do anything when changed."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function validateVertexCredential")
    fn_body = body[fn_start : body.index("async function onVertexPairChanged")]
    assign_pos = fn_body.index('pendingLlmProviderCredential = {provider: "vertex"')
    show_models_pos = fn_body.index("showLlmProviderModels(body.models,")
    assert assign_pos < show_models_pos


async def test_frame5_has_blocked_and_form_sections():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="uptime-pinger-blocked-section"' in body
    assert 'id="uptime-pinger-form-section"' in body


async def test_frame5_has_credential_input_and_submit():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="uptime-pinger-api-key-input"' in body
    assert 'id="uptime-pinger-submit"' in body


async def test_frame5_locked_by_default():
    client = await _client()
    body = (await client.get("/")).text
    assert (
        'id="frame-uptime-pinger" class="frame" data-status="locked" data-locked="true"'
    ) in body


async def test_uptimerobot_endpoint_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/uptimerobot/create-monitor"') == 1


async def test_uptimerobot_delete_monitor_endpoint_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/uptimerobot/delete-monitor"') == 1


async def test_frame5_never_persists_to_local_storage():
    client = await _client()
    body = (await client.get("/")).text
    assert 'sessionStorage.setItem(STORAGE_KEYS["uptime-pinger"]' in body
    assert 'localStorage.setItem(STORAGE_KEYS["uptime-pinger"]' not in body


async def test_uptimerobot_error_sets_frame_status_to_error():
    """Structural sibling of llmProviderError()/githubAppError() -- all
    three call setFrameStatus(id, "error") before writing the error text."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function submitUptimeRobotKey")
    fn_body = body[fn_start : body.index("function uptimePingerErrorKeyForReason")]
    assert fn_body.count('setFrameStatus("uptime-pinger", "error")') >= 1


async def test_unauthorized_error_mentions_the_main_api_key_requirement():
    """No server-side signal distinguishes a read-only key from an invalid
    one (design doc section 2) -- mitigated at the UI-copy level instead,
    which makes this copy the *entire* mitigation.

    Scoped to the err_uptime_unauthorized value on purpose: a bare
    `"Main API Key" in body` passes on frame5_instructions alone, so the
    error message could quietly drop the mention and this test would still
    go green -- the one regression it exists to catch.
    """
    client = await _client()
    body = (await client.get("/")).text
    values = [
        line.split("err_uptime_unauthorized:", 1)[1]
        for line in body.splitlines()
        if "err_uptime_unauthorized:" in line
    ]
    assert len(values) == 2, "expected an en and a he err_uptime_unauthorized"
    for value in values:
        assert "Main API Key" in value, f"Main-API-Key mention missing from: {value}"


async def test_frame5_strings_present_in_both_languages():
    client = await _client()
    body = (await client.get("/")).text
    for key in (
        "frame5_instructions",
        "frame5_blocked_no_render_url",
        "err_uptime_empty_key",
        "err_uptime_unauthorized",
        "err_uptime_rate_limited",
        "err_uptime_unreachable",
        "err_uptime_request_rejected",
    ):
        # == 2 (STRINGS.en + STRINGS.he), not merely `in body`: a presence
        # check passes on an en-only definition, which is exactly the
        # regression this frame's Hebrew half needs guarding against.
        assert body.count(f"{key}:") == 2, f"{key} is not defined in both languages"


async def test_language_switch_retranslates_uptime_pinger_error():
    client = await _client()
    body = (await client.get("/")).text
    assert (
        'document.getElementById("uptime-pinger-error").textContent = '
        "t(currentUptimePingerErrorKey);"
    ) in body


async def test_frame5_has_a_reset_path_wired_into_lock_and_change():
    client = await _client()
    body = (await client.get("/")).text
    assert "function resetUptimePingerSection" in body
    assert 'if (id === "uptime-pinger") resetUptimePingerSection();' in body
    assert 'if (id === "uptime-pinger") {' in body  # beginChange's storage-clear branch


async def test_restore_from_session_completes_uptime_pinger_frame():
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function restoreFromSession")
    fn_body = body[fn_start : body.index("function guardLockedFrames")]
    assert 'frames["uptime-pinger"]' in fn_body
    assert 'completeFrame("uptime-pinger"' in fn_body


async def test_frame5_reevaluates_blocked_state_when_reopened():
    """Spec section 3: the frame re-checks its precondition each time it's
    reopened, not only once at unlock -- sub-project 6 may not have run yet
    the first time frame 5 unlocks, but could have by a later reopen."""
    client = await _client()
    body = (await client.get("/")).text
    assert 'document.getElementById("frame-uptime-pinger").addEventListener("toggle"' in body


async def test_frame_order_includes_render_service_after_render_key():
    client = await _client()
    body = (await client.get("/")).text
    assert '"render-key", "render-service", "dashboard-auth", "github-app"' in body


async def test_frame_order_ends_with_render_deploy():
    client = await _client()
    body = (await client.get("/")).text
    assert '"uptime-pinger", "render-deploy"' in body


async def test_render_service_frame_markup_present():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="frame-render-service"' in body
    assert 'id="render-service-repo-input"' in body
    assert 'id="render-service-name-input"' in body
    assert 'id="render-service-submit"' in body


async def test_render_service_storage_key_present():
    client = await _client()
    body = (await client.get("/")).text
    assert '"render-service": "onboarding.renderService"' in body


async def test_create_service_fetch_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/render/create-service"') == 1


async def test_render_service_url_written_on_success():
    client = await _client()
    body = (await client.get("/")).text
    assert "sessionStorage.setItem(RENDER_SERVICE_URL_KEY, body.service_url)" in body


async def test_render_deploy_frame_markup_present():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="render-deploy-trigger-section"' in body
    assert 'id="render-deploy-polling-section"' in body
    assert 'id="render-deploy-done-section"' in body
    assert 'id="render-deploy-trigger-submit"' in body
    assert 'id="render-deploy-check-again-submit"' in body


async def test_trigger_deploy_fetch_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/render/trigger-deploy"') == 1


async def test_deploy_status_fetch_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/render/deploy-status"') == 1


async def test_clear_deploy_state_fetch_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/render/clear-deploy-state"') == 1


async def test_render_service_frame_i18n_strings_present_in_both_languages():
    client = await _client()
    body = (await client.get("/")).text
    keys = [
        "frame_render_service_title",
        "frame_render_service_instructions",
        "frame_render_service_repo_label",
        "frame_render_service_name_label",
        "create_service_button",
        "url_prefix",
        "err_render_service_no_key",
        "err_render_service_empty",
        "err_render_service_invalid_key",
        "err_render_service_unreachable",
        "err_render_service_rejected",
    ]
    for key in keys:
        assert body.count(f"{key}:") == 2, f"{key} should appear once in en and once in he"


async def test_render_deploy_frame_i18n_strings_present_in_both_languages():
    client = await _client()
    body = (await client.get("/")).text
    keys = [
        "frame6_instructions",
        "frame6_polling",
        "frame6_done",
        "deploy_button",
        "err_render_deploy_no_service",
        "err_render_deploy_invalid_key",
        "err_render_deploy_service_not_found",
        "err_render_deploy_unreachable",
        "err_render_deploy_failed",
        "err_render_deploy_timeout",
    ]
    for key in keys:
        assert body.count(f"{key}:") == 2, f"{key} should appear once in en and once in he"


async def test_frame_titles_renumbered_after_render_service_insertion():
    client = await _client()
    body = (await client.get("/")).text
    assert 'frame_render_service_title: "2. Render service"' in body
    assert 'frame_dashboard_auth_title: "3. Dashboard login"' in body
    assert 'frame2_title: "4. GitHub App"' in body
    assert 'frame3_title: "5. Supabase database"' in body
    assert 'frame4_title: "6. LLM provider"' in body
    assert 'frame5_title: "7. Keep-warm pinger"' in body
    assert 'frame6_title: "8. Finish & Deploy"' in body


async def test_dashboard_auth_frame_markup_present():
    client = await _client()
    body = (await client.get("/")).text
    assert 'id="frame-dashboard-auth"' in body
    assert 'id="dashboard-auth-username-input"' in body
    assert 'id="dashboard-auth-password-input"' in body
    assert 'id="dashboard-auth-password-toggle"' in body
    assert 'id="dashboard-auth-generate-submit"' in body
    assert 'id="dashboard-auth-ack-checkbox"' in body
    assert 'id="dashboard-auth-submit"' in body


async def test_password_reveal_is_an_icon_inside_the_input():
    """The reveal control sits inside the field as an eye/crossed-eye icon,
    the way password inputs conventionally work -- not a separate "Show"
    text button sitting beside it."""
    client = await _client()
    body = (await client.get("/")).text
    field = body[body.index('<div class="password-field">') :]
    field = field[: field.index("</div>")]
    assert 'id="dashboard-auth-password-input"' in field
    assert 'id="dashboard-auth-password-toggle"' in field
    assert 'id="dashboard-auth-eye-open"' in field
    assert 'id="dashboard-auth-eye-closed"' in field
    # Positioned with logical properties so it lands on the correct side
    # under the Hebrew RTL direction, not pinned to the physical right.
    assert "inset-inline-end" in body
    assert "padding-inline-end" in body


async def test_password_toggle_label_is_translated_via_aria_not_text():
    """An icon button still needs an accessible name, and it has to follow a
    language switch like every other string on the page."""
    client = await _client()
    body = (await client.get("/")).text
    fn_body = body[
        body.index("function setDashboardAuthPasswordVisibility") : body.index(
            "function toggleDashboardAuthPasswordVisibility"
        )
    ]
    assert 'toggle.setAttribute("aria-label", label)' in fn_body
    assert '"hide_password_button" : "show_password_button"' in fn_body
    # applyLanguage must re-resolve it rather than leaving a stale label.
    apply_body = body[body.index("function applyLanguage") : body.index("function frameEl")]
    assert "setDashboardAuthPasswordVisibility(dashboardAuthPasswordVisible);" in apply_body


async def test_github_app_checklist_and_permissions_table_follow_language_switch():
    """Both are built dynamically (not [data-i18n]) from t()-resolved
    strings baked into DOM text at render time -- applyLanguage() must
    re-invoke both renderers, same as every other dynamic string on the
    page, or a visitor who switches language after validating the App is
    left with a checklist/table stuck in the old language."""
    client = await _client()
    body = (await client.get("/")).text
    apply_body = body[body.index("function applyLanguage") : body.index("function frameEl")]
    assert "renderGithubAppInstructions();" in apply_body
    assert "if (lastGithubAppChecklistBody) {" in apply_body
    assert "renderGithubAppChecklist(lastGithubAppChecklistBody);" in apply_body

    # lastGithubAppChecklistBody must be set on render and cleared on reset,
    # not just referenced by applyLanguage -- otherwise a stale success body
    # from a previous account would resurface after a language switch that
    # follows a "Change".
    checklist_fn = body[
        body.index("function renderGithubAppChecklist") : body.index("function base64UrlEncode")
    ]
    assert "lastGithubAppChecklistBody = body;" in checklist_fn
    reset_fn = body[
        body.index("function resetGithubAppSetupSection") : body.index("function githubAppError")
    ]
    assert "lastGithubAppChecklistBody = null;" in reset_fn


async def test_dashboard_auth_ack_checkbox_sits_below_both_buttons():
    """Its own row under the buttons -- wedged between them it broke the
    button row onto two lines."""
    client = await _client()
    body = (await client.get("/")).text
    frame = body[body.index('id="frame-dashboard-auth"') :]
    frame = frame[: frame.index("</details>")]
    assert frame.index('id="dashboard-auth-generate-submit"') < frame.index(
        'id="dashboard-auth-ack-checkbox"'
    )
    assert frame.index('id="dashboard-auth-submit"') < frame.index(
        'id="dashboard-auth-ack-checkbox"'
    )
    assert 'class="checkbox-row"' in frame


async def test_dashboard_auth_frame_positioned_right_after_render_service():
    client = await _client()
    body = (await client.get("/")).text
    assert body.index('id="frame-render-service"') < body.index('id="frame-dashboard-auth"')
    assert body.index('id="frame-dashboard-auth"') < body.index('id="frame-github-app"')
    assert '"render-key", "render-service", "dashboard-auth", "github-app", "supabase",' in body


async def test_dashboard_auth_storage_key_present():
    client = await _client()
    body = (await client.get("/")).text
    assert '"dashboard-auth": "onboarding.dashboardAuth"' in body


async def test_dashboard_auth_confirm_fetch_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/dashboard-auth/confirm"') == 1
    assert "push-render-vars" not in body


async def test_dashboard_auth_never_persists_raw_credentials():
    client = await _client()
    body = (await client.get("/")).text
    assert (
        'sessionStorage.setItem(STORAGE_KEYS["dashboard-auth"], JSON.stringify({completed: true}))'
        in body
    )


async def test_dashboard_auth_begin_change_clears_its_own_stale_state():
    client = await _client()
    body = (await client.get("/")).text
    assert 'if (id === "dashboard-auth") {' in body
    assert 'sessionStorage.removeItem(STORAGE_KEYS["dashboard-auth"]);' in body


async def test_dashboard_auth_frame_i18n_strings_present_in_both_languages():
    client = await _client()
    body = (await client.get("/")).text
    keys = [
        "frame_dashboard_auth_title",
        "frame_dashboard_auth_instructions",
        "frame_dashboard_auth_username_label",
        "frame_dashboard_auth_password_label",
        "generate_password_button",
        "show_password_button",
        "hide_password_button",
        "dashboard_auth_ack_label",
        "save_continue_button",
        "err_dashboard_auth_empty",
        "err_dashboard_auth_password_short",
        "err_dashboard_auth_ack_required",
        "err_dashboard_auth_storage_failed",
    ]
    for key in keys:
        assert body.count(f"{key}:") == 2, f"{key} should appear once in en and once in he"


async def test_begin_change_render_service_clears_its_own_stale_state():
    client = await _client()
    body = (await client.get("/")).text
    assert 'if (id === "render-service")' in body
    assert 'sessionStorage.removeItem(STORAGE_KEYS["render-service"])' in body
    assert "sessionStorage.removeItem(RENDER_SERVICE_URL_KEY)" in body


async def test_lock_frame_resets_render_deploy_section():
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("function lockFrame")
    fn_body = body[fn_start : body.index("\n  function unlockFrame", fn_start)]
    assert "resetRenderDeploySection();" in fn_body


async def test_lock_frame_clears_stale_deploy_progress_flags():
    """deployed/pending_deploy_id live inside render-service's own storage
    blob, which has no STORAGE_KEYS entry of its own for "render-deploy" --
    without this, a reload after locking render-deploy (e.g. right after
    changing llm-provider) would resurrect the OLD deploy's live-URL/polling
    state instead of the fresh "ready to redeploy" state."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("function lockFrame")
    fn_body = body[fn_start : body.index("\n  function unlockFrame", fn_start)]
    assert "delete renderServiceState.deployed;" in fn_body
    assert "delete renderServiceState.pending_deploy_id;" in fn_body


async def test_triggering_deploy_sets_a_deploying_status():
    """The badge must read "Deploying…", not stay on "Not started", while a
    triggered deploy is in flight — both the live-trigger path and the
    reload-resume-while-pending path."""
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('setFrameStatus("render-deploy", "deploying");') == 2
    for lang_block_marker in ("badge_deploying: \"Deploying…\"", "badge_deploying: \"פורס…\""):
        assert lang_block_marker in body


async def test_render_deploy_frame_stays_open_when_done():
    """Unlike every other frame, render-deploy (the last one) must not
    collapse on completion -- it's the only place the dashboard link lives,
    and there's no next frame for the collapse to draw attention to."""
    client = await _client()
    body = (await client.get("/")).text
    assert 'completeFrame("render-deploy", null, null, "deploy_done", true)' in body
    # completeFrame's default (every other frame) must remain collapse-on-complete.
    assert "function completeFrame(id, detailKey, detailValue, status, keepOpen)" in body
    fn_start = body.index("function completeFrame(id, detailKey, detailValue, status, keepOpen)")
    fn_body = body[fn_start : body.index("function ", fn_start + 10)]
    assert "el.dataset.stayOpen = \"true\";" in fn_body
    assert "el.open = true;" in fn_body
    assert "closeFrameAnimated(id);" in fn_body


async def test_render_deploy_frame_reopens_on_a_fresh_reload_after_completion():
    """restoreFromSession() runs in a totally new script instance where
    every frame starts closed+locked per its raw HTML -- keepOpen alone
    (skipping the close) isn't enough there, since the frame was never
    open to begin with. Both branches (already deployed, and mid-deploy)
    must genuinely open the frame, not just skip collapsing it. A native
    <details>'s "toggle" event fires asynchronously, so
    guardLockedFrames()'s own toggle listener needs a separate signal
    (dataset.stayOpen) to know this locked frame is allowed to stay open --
    reordering statements alone can't fix this, since dataset.locked is
    already "true" by the time that listener actually runs regardless of
    where it's set in completeFrame's own source."""
    client = await _client()
    body = (await client.get("/")).text

    guard_start = body.index("function guardLockedFrames")
    guard_body = body[guard_start : body.index("function ", guard_start + 10)]
    assert 'el.dataset.stayOpen !== "true"' in guard_body

    restore_start = body.index("async function restoreFromSession")
    restore_body = body[restore_start : body.index("function guardLockedFrames")]
    deployed_branch = restore_body[
        restore_body.index("if (deployedServiceUrl)") :
        restore_body.index("else if (pendingDeployId)")
    ]
    assert 'completeFrame("render-deploy", null, null, "deploy_done", true);' in deployed_branch
    # render-deploy-trigger-section has no `style="display: none"` in its
    # raw HTML (unlike the polling/done sections) -- the live completion
    # path never needs to hide it here because triggerRenderDeploy() already
    # did earlier in the same script instance. A fresh reload has none of
    # that history, so this branch must hide it explicitly or the "Deploy"
    # button shows alongside the completed link.
    assert (
        'document.getElementById("render-deploy-trigger-section").style.display = "none";'
        in deployed_branch
    )

    pending_branch = restore_body[restore_body.index("else if (pendingDeployId)") :]
    assert 'unlockFrame("render-deploy");' in pending_branch

    lock_start = body.index("function lockFrame(id)")
    lock_body = body[lock_start : body.index("function unlockFrame")]
    assert "delete el.dataset.stayOpen;" in lock_body


async def test_check_again_button_disables_itself_while_in_flight():
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function checkRenderDeployStatusOnce")
    fn_body = body[fn_start : body.index("\n  async function triggerRenderDeploy", fn_start)]
    assert "checkAgainBtn.disabled = true;" in fn_body
    assert "checkAgainBtn.disabled = false;" in fn_body


async def test_supabase_check_again_button_disables_itself_while_in_flight():
    """Matches the render-deploy check-again button's own guard above -- a
    double-click must not issue two concurrent status checks."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function checkSupabaseStatusOnce")
    fn_body = body[fn_start : body.index("\n\n  let currentRenderDeployErrorKey", fn_start)]
    assert "checkAgainBtn.disabled = true;" in fn_body
    assert "checkAgainBtn.disabled = false;" in fn_body


async def test_restoring_a_completed_deploy_shows_the_dashboard_link():
    """A reload after a completed deploy must re-show the done-section link,
    not just mark the frame done with no visible way back to the dashboard.
    See tests/test_onboarding_page_browser.py's
    test_restore_from_session_shows_deployed_service_link_when_server_reports_deployed
    for the corresponding real-browser behavior test (server-reported
    deployed state, no local sessionStorage mirror at all)."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("if (deployedServiceUrl)")
    fn_snippet = body[fn_start:fn_start + 1900]
    assert 'getElementById("render-deploy-done-section").style.display = "block"' in fn_snippet
    assert 'getElementById("render-deploy-service-link")' in fn_snippet
    assert "deployedServiceUrl" in fn_snippet


async def test_github_confirm_fetch_leaves_the_page_exactly_once():
    """github-app no longer pushes to Render incrementally -- validate-app
    persists server-side on success, and the final render-deploy frame's
    bulk-push endpoint sends everything to Render in one call."""
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/github/validate-app"') == 1
    assert "github/push-render-vars" not in body


async def test_llm_confirm_fetch_leaves_the_page_exactly_once():
    client = await _client()
    body = (await client.get("/")).text
    assert body.count('fetch("/api/llm/confirm"') == 1
    assert "llm/push-render-vars" not in body


async def test_no_per_frame_push_to_render_remains():
    """The four incremental push-render-vars endpoints (github, supabase,
    llm, dashboard-auth) were replaced by one bulk push from the final
    render-deploy frame -- none of the four call sites, or their helper
    functions, should exist anywhere on the page anymore."""
    client = await _client()
    body = (await client.get("/")).text
    for endpoint in (
        "/api/github/push-render-vars",
        "/api/supabase/push-render-var",
        "/api/llm/push-render-vars",
        "/api/dashboard-auth/push-render-vars",
    ):
        assert endpoint not in body
    for fn in (
        "pushGithubAppToRenderService",
        "pushSupabaseToRenderService",
        "pushLlmProviderToRenderService",
        "pushDashboardAuthToRenderService",
    ):
        assert fn not in body


async def test_render_deploy_bulk_pushes_before_triggering():
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function triggerRenderDeploy")
    fn_body = body[fn_start : body.index("async function fetchSupabaseConnectionInfo")]
    assert 'fetch("/api/render/bulk-push-env-vars"' in fn_body
    assert 'fetch("/api/render/trigger-deploy"' in fn_body
    assert fn_body.index('fetch("/api/render/bulk-push-env-vars"') < fn_body.index(
        'fetch("/api/render/trigger-deploy"'
    )


async def test_supabase_restore_uses_completed_flag_not_connection_string():
    client = await _client()
    body = (await client.get("/")).text
    assert "supabaseState.completed" not in body  # old field, gone
    assert "supabaseFrame.complete" in body
    assert "connection_string" not in body


async def test_render_service_frame_precedes_the_github_app_frame():
    """Load-bearing ordering, not cosmetic: the App's webhook URL is fixed at
    creation time from the Render service's URL, so that URL must already
    exist by the time this frame runs. Reordering these two would make every
    App get created with no webhook URL available."""
    client = await _client()
    body = (await client.get("/")).text
    order = body[body.index("const FRAME_ORDER = [") :]
    order = order[: order.index("];")]
    assert order.index('"render-service"') < order.index('"github-app"')


async def test_every_getelementbyid_target_exists_in_the_markup():
    """A stale getElementById("...") reference throws at runtime (unlike
    every other check in this file, which is a content-substring check, not
    JS execution) -- one such reference (github-app-name-input, left behind
    by the manual-GitHub-App-flow redesign) was in applyLanguage(), meaning
    EVERY page load threw before restoreFromSession() or the theme/reset
    listeners ever ran, silently. This is a blanket regression guard: every
    literal getElementById("<id>") call anywhere in the script must have a
    matching id="<id>" (or id='<id>') somewhere in the document."""
    import re

    client = await _client()
    body = (await client.get("/")).text
    requested_ids = set(re.findall(r'getElementById\("([^"]+)"\)', body))
    present_ids = set(re.findall(r'id="([^"]+)"', body)) | set(re.findall(r"id='([^']+)'", body))
    missing = requested_ids - present_ids
    assert not missing, f"getElementById() targets with no matching id= in the markup: {missing}"


async def test_page_self_hosts_its_display_font():
    """Same contract as the sibling review-engine project's dashboard/login
    pages: the certificate-style display face must be self-hosted (never a
    Google Fonts CDN link an outage or blocked third-party request could
    silently revert), served via main.py's own /static/fonts mount."""
    client = await _client()
    body = (await client.get("/")).text
    assert "/static/fonts/fraunces-v38-latin-regular.woff2" in body
    assert "/static/fonts/fraunces-v38-latin-600.woff2" in body
    assert "/static/fonts/fraunces-v38-latin-700.woff2" in body
    assert "fonts.googleapis.com" not in body
    assert "fonts.gstatic.com" not in body


async def test_font_files_are_served_from_the_static_mount():
    client = await _client()
    for filename in (
        "fraunces-v38-latin-regular.woff2",
        "fraunces-v38-latin-600.woff2",
        "fraunces-v38-latin-700.woff2",
    ):
        resp = await client.get(f"/static/fonts/{filename}")
        assert resp.status_code == 200
        assert resp.content[:4] == b"wOF2"


async def test_webhook_patch_flow_is_fully_removed():
    """Endpoint, client call, retry UI and its strings all go together --
    a leftover half of this flow is worse than either whole."""
    client = await _client()
    body = (await client.get("/")).text
    for gone in (
        "/api/github/set-webhook-url",
        "setGithubWebhookUrl",
        "github-app-webhook-retry-section",
        "github-app-webhook-retry-submit",
        "retryGithubWebhookSetup",
        "frame2_webhook_retry_instructions",
        "retry_button",
        "err_github_webhook_invalid_credentials",
        "err_github_webhook_unreachable",
    ):
        assert gone not in body, f"{gone} survived the webhook-patch removal"


async def test_reset_wizard_clears_session_storage_before_reloading():
    """resetWizard() ("Start over") deletes the server-side session but a
    plain location.reload() alone leaves sessionStorage untouched -- a
    prior run's leftover render-service blob (in particular
    `pending_deploy_id`, written once a deploy is triggered) would survive
    into the fresh session. restoreFromSession() reads that blob directly
    (deploy progress isn't part of GET /api/session) and would start
    polling a deploy with no matching server-side record, poisoning the
    still-locked render-deploy frame's status to "error" before the
    visitor has redone a single frame. Every STORAGE_KEYS entry and
    RENDER_SERVICE_URL_KEY must be cleared before the reload."""
    client = await _client()
    body = (await client.get("/")).text
    fn_start = body.index("async function resetWizard")
    fn_body = body[fn_start : body.index("function lockFrame")]
    assert (
        "Object.values(STORAGE_KEYS).forEach((key) => sessionStorage.removeItem(key));"
        in fn_body
    )
    assert "sessionStorage.removeItem(RENDER_SERVICE_URL_KEY);" in fn_body
    # Order matters: both clears must happen before the reload, not after.
    assert fn_body.index("sessionStorage.removeItem(RENDER_SERVICE_URL_KEY)") < fn_body.index(
        "location.reload();"
    )


async def test_links_use_the_palette_accent_color_not_browser_defaults():
    """No `a { }` rule existed at all before this -- the wizard's one real
    link (the final frame's deployed-dashboard URL) fell back to the
    browser's bare default blue/visited-purple, which reads poorly (and
    belongs to no theme) against the dark-mode navy/brown surfaces.
    --accent is this system's own designated interactive-control color
    (see the :root comment) -- links must actually use it."""
    client = await _client()
    body = (await client.get("/")).text
    assert "a { color: var(--accent); }" in body
    assert "a:visited { color: var(--accent); }" in body
    assert "a:hover, a:focus-visible { color: var(--gold); }" in body


async def test_hebrew_breadcrumb_strings_use_a_real_nested_list_not_arrows():
    """A mixed-direction chain like "Account Settings <arrow> API Keys"
    embedded inline in Hebrew prose renders ambiguously: an embedded LTR
    run's position among *other* embedded LTR runs is governed by the
    surrounding RTL paragraph's directionality, not by the order the runs
    appear in the source string -- confirmed by direct on-screen
    measurement (getBoundingClientRect) showing the runs' visual order
    flips depending on whether they're isolated, independent of which
    arrow character is used. A real nested <ol> sidesteps the whole
    class of bug: each term is its own block-level list item, read
    top-to-bottom, with no horizontal-adjacency reversal possible.
    English keeps its original flowing arrow-chain sentence unchanged --
    it never had this problem, since English text reads left-to-right in
    its own native direction."""
    client = await _client()
    body = (await client.get("/")).text
    assert "←" not in body
    assert "→" in body  # English still uses it -- never had the bug

    html_keys_start = body.index("const HTML_I18N_KEYS = new Set([")
    html_keys_body = body[html_keys_start : body.index("]);", html_keys_start)]
    for key in (
        "frame1_instructions",
        "frame2_step_create",
        "frame2_step_webhook_url",
        "frame2_step_webhook_secret",
        "frame2_step_install",
        "frame3_instructions",
        "frame5_instructions",
    ):
        assert f'"{key}"' in html_keys_body

    apply_start = body.index("function applyLanguage")
    placeholder_marker = 'document.getElementById("render-key-input").placeholder'
    apply_body = body[apply_start : body.index(placeholder_marker)]
    assert "if (HTML_I18N_KEYS.has(key)) {" in apply_body
    assert "el.innerHTML = t(key);" in apply_body
    assert "el.textContent = t(key);" in apply_body

    # Pin the actual list order for one representative multi-term chain.
    create_he = body[
        body.index('frame2_step_create: "עברו') : body.index('frame2_step_webhook_url: "פתחו')
    ]
    assert (
        "<ol><li>Settings</li><li>Developer settings</li><li>GitHub Apps</li>"
        "<li>New GitHub App</li></ol>" in create_he
    )


async def test_github_app_value_rows_each_get_their_own_copy_button():
    """Homepage URL previously had no copy button at all (only Webhook URL
    and Webhook secret did) -- inconsistent, and copying a whole base URL
    by hand is exactly the kind of thing a copy button exists for. Every
    value now also sits on its own line below its label (was crammed onto
    the same line), matching the label's own new block-level layout."""
    client = await _client()
    body = (await client.get("/")).text
    targets = ("github-app-homepage-url", "github-app-webhook-url", "github-app-webhook-secret")
    for target in targets:
        assert f'<div class="value-row"><code id="{target}"></code>' in body
        assert f'data-copy-target="{target}"' in body
        assert f'id="{target}-copy" class="copy-btn"' in body
