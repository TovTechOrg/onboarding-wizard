from pathlib import Path

DEMO_JS = (Path(__file__).parent.parent / "demo" / "static" / "demo.js").read_text()


def test_shortcut_button_is_added_to_the_github_frame():
    assert "demoUseCredentials" in DEMO_JS
    assert "frame-github-app" in DEMO_JS


def test_shortcut_fills_fields_rather_than_bypassing_the_endpoint():
    """The real /api/github/validate-app call still runs -- the mock client
    is what makes it succeed. Bypassing it would skip the frame machinery."""
    assert "/api/github/validate-app" not in DEMO_JS.split("demoUseCredentials")[1][:800]


def test_service_link_carries_the_chosen_provider():
    assert "render-deploy-service-link" in DEMO_JS
    assert "provider=" in DEMO_JS
