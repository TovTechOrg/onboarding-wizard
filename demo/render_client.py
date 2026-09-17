"""In-memory stand-in for render_client.py. Reaches no network.

Re-exports the real result types so isinstance checks in router.py keep
working -- the mock changes where results come from, never their shape.
"""

from __future__ import annotations

from demo.content import (
    DEMO_BOT_URL,
    DEMO_DEPLOY_ID,
    DEMO_OWNER_NAME,
    DEMO_SERVICE_ID,
    DEPLOY_POLLS_BEFORE_LIVE,
)
from render_client import (  # noqa: F401  (re-exported for isinstance checks)
    RenderDeployStatus,
    RenderDeployTriggered,
    RenderEnvVarsPushed,
    RenderKeyValid,
    RenderServiceCreated,
)

_poll_counts: dict[str, int] = {}


def reset() -> None:
    _poll_counts.clear()


async def validate_key(api_key: str) -> RenderKeyValid:
    return RenderKeyValid(owner_name=DEMO_OWNER_NAME)


async def create_service(api_key: str, repo_url: str, name: str) -> RenderServiceCreated:
    # service_url is what the wizard shows as the finished deployment's link,
    # so it is the handoff point to the bot demo.
    return RenderServiceCreated(service_id=DEMO_SERVICE_ID, service_url=DEMO_BOT_URL)


async def push_env_vars(
    api_key: str, service_id: str, values: dict[str, str]
) -> RenderEnvVarsPushed:
    # Names only, never values -- same contract as the real client.
    return RenderEnvVarsPushed(pushed=sorted(values))


async def trigger_deploy(api_key: str, service_id: str) -> RenderDeployTriggered:
    _poll_counts[DEMO_DEPLOY_ID] = 0
    return RenderDeployTriggered(deploy_id=DEMO_DEPLOY_ID)


async def poll_deploy_status(
    api_key: str, service_id: str, deploy_id: str
) -> RenderDeployStatus:
    seen = _poll_counts.get(deploy_id, 0) + 1
    _poll_counts[deploy_id] = seen
    return RenderDeployStatus(
        status="live" if seen > DEPLOY_POLLS_BEFORE_LIVE else "in_progress"
    )
