import inspect

import github_client as real_gh
import llm_client as real_llm
import pytest
import render_client as real_render
import supabase_client as real_supabase
import uptimerobot_client as real_uptimerobot

from demo import github_client as demo_gh
from demo import llm_client as demo_llm
from demo import render_client as demo_render
from demo import supabase_client as demo_supabase
from demo import uptimerobot_client as demo_uptimerobot
from demo.content import DEMO_BOT_URL, DEMO_SERVICE_ID


def _public_defined(module) -> set[str]:
    """Public functions actually DEFINED in `module` (not merely imported
    into it) -- same shape as tests/test_demo_session_store.py's own
    `_public()`. Used for the REAL side of the parity check below: what a
    real client module's own public API surface actually is."""
    return {
        name for name, obj in vars(module).items()
        if not name.startswith("_") and inspect.isfunction(obj)
        and obj.__module__ == module.__name__
    }


def _public_available(module) -> set[str]:
    """Public callables available on `module`, defined there OR re-exported
    (e.g. demo/github_client.py re-exports the real, pure
    `diff_required_permissions` unchanged rather than reimplementing it --
    see its own `# noqa: F401` import comment). Used for the DEMO side: a
    demo mock satisfies parity either by providing its own implementation
    or by re-exporting the real one directly, both of which genuinely cover
    the surface router.py calls."""
    return {
        name for name, obj in vars(module).items()
        if not name.startswith("_") and callable(obj)
    }


@pytest.mark.parametrize("real_module,demo_module", [
    (real_render, demo_render),
    (real_gh, demo_gh),
    (real_llm, demo_llm),
    (real_supabase, demo_supabase),
    (real_uptimerobot, demo_uptimerobot),
])
def test_mock_covers_the_real_clients_public_surface(real_module, demo_module):
    """Generalizes tests/test_demo_session_store.py's own signature-parity
    test (2026-09-17, final-review I1) to every boundary module's mock, not
    just session_store's -- a function the real module gained that no demo
    mock provides is exactly the shape of gap the C3 finding exploited
    (uptimerobot_client/supabase_client had no demo mock at all)."""
    missing = _public_defined(real_module) - _public_available(demo_module)
    assert not missing, f"{demo_module.__name__} is missing: {sorted(missing)}"


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
