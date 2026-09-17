from pathlib import Path

DEMO_JS = (Path(__file__).parent.parent / "demo" / "static" / "demo.js").read_text()


def test_github_app_credentials_are_auto_filled_on_page_load():
    """2026-09-17: readers should never need to type an App id or pick a
    .pem file from their own device -- both are auto-filled/mocked, and the
    frame's own machinery (the real /api/github/validate-app relay) still
    advances it once Validate is clicked."""
    assert "autoFillGithubApp" in DEMO_JS
    assert "DEMO_APP_ID" in DEMO_JS


def test_the_auto_fill_never_calls_the_validate_endpoint_directly():
    """The real /api/github/validate-app call still runs from the page's own
    Validate button -- demo.js only ever fills fields, never bypasses the
    frame machinery by calling the relay itself."""
    assert "/api/github/validate-app" not in DEMO_JS


def test_the_private_key_file_input_is_mocked_and_locked():
    """A file input can't be pre-filled by setting .value (every browser
    forbids it) or made read-only (no such attribute exists for file
    inputs) -- this must synthesize a real File via DataTransfer and then
    disable the input, hiding its own visible "Choose File" control so it
    doesn't look clickable once it's no longer functional."""
    assert "mockAndLockFileInput" in DEMO_JS
    assert "DataTransfer" in DEMO_JS
    assert ".disabled = true" in DEMO_JS


def test_render_key_and_llm_credential_are_also_locked_read_only():
    assert "autoFillRenderKey" in DEMO_JS
    assert "DEMO_RENDER_KEY" in DEMO_JS
    assert "autoFillLlmCredentialForCurrentChoice" in DEMO_JS
    assert "DEMO_LLM_CREDENTIAL" in DEMO_JS


def test_the_llm_provider_picker_itself_stays_mutable():
    """Unlike every other credential field, the provider radio choice (and,
    for Vertex, its model/project/location) must stay interactive -- it's
    what personalizes the review the reader lands on. demo.js never sets
    the radios themselves (or the model/project/location selects) to
    read-only/disabled -- only the credential control matching whichever
    provider is chosen."""
    assert 'input[name="llm-provider-choice"]' in DEMO_JS
    assert "wireLlmCredentialAutoFill" in DEMO_JS
    assert "llm-provider-model-select" not in DEMO_JS
    assert "llm-provider-project-select" not in DEMO_JS
    assert "llm-provider-location-select" not in DEMO_JS


def test_service_link_carries_the_chosen_provider():
    assert "render-deploy-service-link" in DEMO_JS
    assert "provider=" in DEMO_JS
