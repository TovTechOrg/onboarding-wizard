"""In-memory stand-in for uptimerobot_client.py. Reaches no network.

The demo's uptime-pinger frame is hidden (COLLAPSED_FRAMES in
demo/static/demo.js) but, unlike dashboard_auth/supabase, is no longer
pre-seeded complete at session creation (see demo/session_store.py's
_PRESEEDED_FRAMES comment, 2026-09-17) -- demo.js's
autoCreateUptimeMonitor() drives it through this exact create-monitor
endpoint instead, right when llm-provider unlocks it, the same "reuse the
real machinery" pattern autoCreateRenderService() there uses for
render-service. router.py's delete-monitor endpoint is also reachable from
"Change"-ing render-key (static/index.html's beginChange() calls
cleanupOrphanedUptimeMonitor(), which POSTs there whenever the session's
uptime_pinger frame carries both api_key and monitor_id, which this mock's
own create-monitor response above always does once it's run). Before this
module existed, that path ran the real, unmocked client and fired a genuine
outbound HTTPS request to api.uptimerobot.com on every "Change" click --
see CLAUDE.md's C3 finding. Mocking here closes it the same way every
other client is mocked, rather than relying on the endpoint never being
reachable.
"""

from __future__ import annotations

from demo.content import DEMO_UPTIME_MONITOR_ID
from uptimerobot_client import (  # noqa: F401  (re-exported for isinstance checks)
    UptimeRobotMonitorDeleted,
    UptimeRobotMonitorResult,
)


async def create_or_reuse_monitor(
    api_key: str, render_service_url: str
) -> UptimeRobotMonitorResult:
    return UptimeRobotMonitorResult(created=True, monitor_id=DEMO_UPTIME_MONITOR_ID)


async def delete_monitor(api_key: str, monitor_id: int) -> UptimeRobotMonitorDeleted:
    return UptimeRobotMonitorDeleted()
