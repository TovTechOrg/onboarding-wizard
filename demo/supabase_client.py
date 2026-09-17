"""In-memory stand-in for supabase_client.py. Reaches no network.

The demo's supabase frame is pre-seeded complete (see
demo/session_store.py's _PRESEEDED_FRAMES) and none of the four visible
demo frames drives one of these endpoints from the browser -- but every
one of them stays reachable directly (the demo mounts main.app's real
router unchanged, see demo/app.py's own docstring), so this is mocked
anyway for defense in depth, the same reasoning that added
demo/uptimerobot_client.py alongside it (CLAUDE.md's C3 finding,
generalized).
"""

from __future__ import annotations

from demo.content import (
    DEMO_SUPABASE_DB_HOST,
    DEMO_SUPABASE_DB_NAME,
    DEMO_SUPABASE_DB_PORT,
    DEMO_SUPABASE_DB_USER,
    DEMO_SUPABASE_ORG_NAME,
    DEMO_SUPABASE_ORG_SLUG,
    DEMO_SUPABASE_PROJECT_REF,
    DEMO_SUPABASE_STATUS,
)
from supabase_client import (  # noqa: F401  (re-exported for isinstance checks)
    SupabaseConnectionInfo,
    SupabaseKeyValid,
    SupabaseOrg,
    SupabaseOrgProject,
    SupabaseProjectCreated,
    SupabaseProjectStatus,
)


async def validate_key(pat: str) -> SupabaseKeyValid:
    return SupabaseKeyValid(
        orgs=[SupabaseOrg(slug=DEMO_SUPABASE_ORG_SLUG, name=DEMO_SUPABASE_ORG_NAME)]
    )


async def find_org_project_by_name(
    access_token: str, organization_slug: str, name: str
) -> SupabaseOrgProject | None:
    # None -- "no existing project with this name" -- the normal
    # first-creation case; there is no persistent Supabase account behind
    # this demo for a name collision to ever be real.
    return None


async def create_project(
    access_token: str, organization_slug: str, name: str, db_pass: str
) -> SupabaseProjectCreated:
    return SupabaseProjectCreated(ref=DEMO_SUPABASE_PROJECT_REF, status=DEMO_SUPABASE_STATUS)


async def get_project_status(access_token: str, ref: str) -> SupabaseProjectStatus:
    return SupabaseProjectStatus(status=DEMO_SUPABASE_STATUS)


async def get_connection_info(
    access_token: str, ref: str, session_id: str
) -> SupabaseConnectionInfo:
    return SupabaseConnectionInfo(
        db_user=DEMO_SUPABASE_DB_USER,
        db_host=DEMO_SUPABASE_DB_HOST,
        db_port=DEMO_SUPABASE_DB_PORT,
        db_name=DEMO_SUPABASE_DB_NAME,
    )
