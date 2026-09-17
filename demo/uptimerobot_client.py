"""In-memory stand-in for uptimerobot_client.py. Reaches no network.

The demo's uptime-pinger frame is pre-seeded complete (see
demo/session_store.py's _PRESEEDED_FRAMES) and is never driven through the
browser via its own frame UI -- but router.py's delete-monitor endpoint is
still reachable from "Change"-ing render-key (static/index.html's
beginChange() calls cleanupOrphanedUptimeMonitor(), which POSTs there
whenever the session's uptime_pinger frame carries both api_key and
monitor_id, which the demo's preseed always does). Before this module
existed, that path ran the real, unmocked client and fired a genuine
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
