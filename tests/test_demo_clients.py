import pytest

from demo import github_client as demo_gh
from demo import llm_client as demo_llm
from demo import render_client as demo_render
from demo.content import DEMO_BOT_URL, DEMO_SERVICE_ID


async def test_render_key_always_validates():
    result = await demo_render.validate_key("anything-the-reader-typed")
    assert result.__class__.__name__ == "RenderKeyValid"
    assert result.owner_name


async def test_created_service_points_at_the_bot_demo():
    result = await demo_render.create_service("k", "https://example/repo", "svc")
    assert result.service_id == DEMO_SERVICE_ID
    assert result.service_url == DEMO_BOT_URL


async def test_deploy_reports_in_progress_before_live():
    demo_render.reset()
    statuses = [
        (await demo_render.poll_deploy_status("k", DEMO_SERVICE_ID, "dep-1")).status
        for _ in range(6)
    ]
    assert statuses[0] == "in_progress", "the real polling UI is the animation"
    assert statuses[-1] == "live"


async def test_app_validation_reports_every_requirement_met():
    result = await demo_gh.validate_app(1, "ZmFrZQ==", "https://example/webhook")
    assert result.__class__.__name__ == "AppValidated"
    assert all(check.ok for check in result.permissions)
    assert all(check.ok for check in result.events)
    assert result.webhook.ok
    assert result.installation.__class__.__name__ == "InstallationFound"


@pytest.mark.parametrize("lister,args", [
    (lambda m: m.list_gemini_models, ("key",)),
    (lambda m: m.list_groq_models, ("key",)),
])
async def test_model_listings_are_non_empty(lister, args):
    result = await lister(demo_llm)(*args)
    assert result.models


async def test_vertex_listing_includes_a_project():
    result = await demo_llm.list_vertex_models("a2V5")
    assert result.project_id
    assert result.models
