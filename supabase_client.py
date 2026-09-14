"""Thin async wrapper around Supabase's Management API — validates a
visitor-pasted Personal Access Token and provisions their own Supabase
project, without persisting any credential server-side. See
docs/superpowers/specs/2026-09-04-supabase-pat-frame-design.md."""

from __future__ import annotations

import dataclasses
import hashlib
import logging

import httpx

logger = logging.getLogger(__name__)

SUPABASE_API_BASE = "https://api.supabase.com/v1"
SUPABASE_REGION_CODE = "us-east-1"


def _extract_message(response: httpx.Response) -> str | None:
    """Best-effort parse of a 4xx body's `message` field -- there is no
    guaranteed structured error body (spec section 4), so this degrades to
    None rather than raising on an unexpected shape."""
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):
        return None
    return str(message) if message else None


@dataclasses.dataclass(frozen=True)
class SupabaseOrg:
    slug: str
    name: str


@dataclasses.dataclass(frozen=True)
class SupabaseKeyValid:
    orgs: list[SupabaseOrg]


@dataclasses.dataclass(frozen=True)
class SupabaseKeyInvalid:
    # "invalid_key" | "insufficient_permissions" | "supabase_unreachable"
    reason: str
    message: str | None = None


SupabaseKeyValidation = SupabaseKeyValid | SupabaseKeyInvalid


async def validate_key(pat: str) -> SupabaseKeyValidation:
    """One cheap read call (GET /organizations) to confirm pat is a live
    Supabase Personal Access Token -- doubles as both validation and the
    org list the frame needs next (Supabase has no separate token-identity
    endpoint). Never logs or returns the token itself. Mirrors
    render_client.validate_key()'s shape exactly.

    Accepts both Classic and Scoped Personal Access Tokens -- Supabase's
    scoped/fine-grained tokens are its own recommended choice for
    automation, but are in gradual/alpha rollout (not every account has the
    option to create one yet), so this never gates on token shape; see
    CLAUDE.md's sub-project 3 section."""
    try:
        async with httpx.AsyncClient(base_url=SUPABASE_API_BASE, timeout=15.0) as client:
            response = await client.get(
                "/organizations",
                headers={"Authorization": f"Bearer {pat}"},
            )
    except httpx.HTTPError:
        return SupabaseKeyInvalid(reason="supabase_unreachable")

    if response.status_code == 401:
        return SupabaseKeyInvalid(reason="invalid_key")
    if response.status_code == 403:
        # A scoped token's 403 means "valid token, missing permission" --
        # distinct from a 401 (dead/revoked token). Relay whatever message
        # Supabase provides so the visitor can tell which permission to add.
        return SupabaseKeyInvalid(
            reason="insufficient_permissions", message=_extract_message(response)
        )
    if response.status_code != 200:
        return SupabaseKeyInvalid(reason="supabase_unreachable")

    try:
        body = response.json()
        orgs = [SupabaseOrg(slug=str(o["slug"]), name=str(o["name"])) for o in body]
    except (ValueError, KeyError, TypeError):
        return SupabaseKeyInvalid(reason="supabase_unreachable")
    return SupabaseKeyValid(orgs=orgs)


@dataclasses.dataclass(frozen=True)
class SupabaseApiFailed:
    # "unauthorized" | "insufficient_permissions" | "rate_limited"
    # | "supabase_unreachable" | "pooler_config_unavailable"
    # | "pooler_not_ready" (connection-info only)
    reason: str
    message: str | None = None


@dataclasses.dataclass(frozen=True)
class SupabaseProjectCreated:
    ref: str
    status: str


@dataclasses.dataclass(frozen=True)
class SupabaseProjectRejected:
    message: str


async def create_project(
    access_token: str, organization_slug: str, name: str, db_pass: str
) -> SupabaseProjectCreated | SupabaseProjectRejected | SupabaseApiFailed:
    """POST /v1/projects — provisions a new project inside the visitor's own
    organization, on their own token. db_pass is already the browser's own
    value (generated client-side, spec section 5) — relayed through, never
    minted or logged here. Omits the deprecated `region`/`plan` fields and
    `desired_instance_size` (defaults to the smallest tier) per spec
    section 3 step 7."""
    try:
        async with httpx.AsyncClient(base_url=SUPABASE_API_BASE, timeout=15.0) as client:
            response = await client.post(
                "/projects",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "organization_slug": organization_slug,
                    "name": name,
                    "db_pass": db_pass,
                    "region_selection": {"type": "specific", "code": SUPABASE_REGION_CODE},
                },
            )
    except httpx.HTTPError:
        return SupabaseApiFailed(reason="supabase_unreachable")

    if response.status_code == 401:
        return SupabaseApiFailed(reason="unauthorized")
    if response.status_code == 429:
        return SupabaseApiFailed(reason="rate_limited")
    if response.status_code >= 500:
        return SupabaseApiFailed(reason="supabase_unreachable")
    if response.status_code >= 400:
        # A 4xx here is ambiguous between a business-rule rejection (e.g.
        # the free-tier project cap) and a scoped token missing a required
        # permission — both surface as the same status code with no
        # guaranteed structured body (spec section 4), so this relays
        # Supabase's own message verbatim rather than guessing which one it
        # was; the visitor sees the same text either way.
        message = _extract_message(response)
        if message:
            return SupabaseProjectRejected(message=message)
        if response.status_code == 403:
            # No message to relay, but a 403 specifically is still much more
            # likely a missing permission (e.g. Organization Projects:
            # Read-write) than a transient outage -- degrading this to
            # "Supabase is unreachable, try again" would tell the visitor to
            # do the one thing that can never fix a missing permission.
            return SupabaseApiFailed(reason="insufficient_permissions")
        return SupabaseApiFailed(reason="supabase_unreachable")
    if response.status_code != 201:
        return SupabaseApiFailed(reason="supabase_unreachable")

    try:
        body = response.json()
        ref = str(body["ref"])
        status = str(body["status"])
    except (ValueError, KeyError, TypeError):
        return SupabaseApiFailed(reason="supabase_unreachable")
    return SupabaseProjectCreated(ref=ref, status=status)


@dataclasses.dataclass(frozen=True)
class SupabaseProjectStatus:
    status: str


async def get_project_status(
    access_token: str, ref: str
) -> SupabaseProjectStatus | SupabaseApiFailed:
    """GET /v1/projects/{ref} — polled by the browser during provisioning.
    Target status is ACTIVE_HEALTHY; the caller treats INIT_FAILED as a
    terminal failure and every other status as still-provisioning."""
    try:
        async with httpx.AsyncClient(base_url=SUPABASE_API_BASE, timeout=15.0) as client:
            response = await client.get(
                f"/projects/{ref}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError:
        return SupabaseApiFailed(reason="supabase_unreachable")

    if response.status_code == 401:
        return SupabaseApiFailed(reason="unauthorized")
    if response.status_code == 403:
        return SupabaseApiFailed(
            reason="insufficient_permissions", message=_extract_message(response)
        )
    if response.status_code == 429:
        return SupabaseApiFailed(reason="rate_limited")
    if response.status_code != 200:
        return SupabaseApiFailed(reason="supabase_unreachable")

    try:
        status = str(response.json()["status"])
    except (ValueError, KeyError, TypeError):
        return SupabaseApiFailed(reason="supabase_unreachable")
    return SupabaseProjectStatus(status=status)


@dataclasses.dataclass(frozen=True)
class SupabaseProjectsListed:
    pass


async def list_projects(pat: str) -> SupabaseProjectsListed | SupabaseApiFailed:
    """GET /v1/projects -- a read-only, account-wide probe used solely to
    confirm the visitor's token carries the "Projects (Read)" scoped-token
    permission, the same one that later gates get_project_status's per-ref
    read (see
    docs/superpowers/research/2026-09-14-supabase-scoped-token-permissions.md).
    Checked once, at validate-key time -- before any project exists -- so a
    missing permission is caught immediately rather than surfacing later as
    a 403 mid-provisioning. No project data from the response is ever kept;
    only whether the call succeeded matters."""
    try:
        async with httpx.AsyncClient(base_url=SUPABASE_API_BASE, timeout=15.0) as client:
            response = await client.get(
                "/projects",
                headers={"Authorization": f"Bearer {pat}"},
            )
    except httpx.HTTPError:
        return SupabaseApiFailed(reason="supabase_unreachable")

    if response.status_code == 401:
        return SupabaseApiFailed(reason="unauthorized")
    if response.status_code == 403:
        return SupabaseApiFailed(
            reason="insufficient_permissions", message=_extract_message(response)
        )
    if response.status_code == 429:
        return SupabaseApiFailed(reason="rate_limited")
    if response.status_code != 200:
        return SupabaseApiFailed(reason="supabase_unreachable")
    return SupabaseProjectsListed()


@dataclasses.dataclass(frozen=True)
class SupabaseOrgProject:
    ref: str
    name: str
    status: str


async def find_org_project_by_name(
    access_token: str, organization_slug: str, name: str
) -> SupabaseOrgProject | None | SupabaseApiFailed:
    """GET /v1/organizations/{slug}/projects -- read-only, used by
    create-project's caller to detect an already-existing project with the
    same name before attempting to create a duplicate (which would either
    409 on the name conflict, or -- worse -- provision a second project the
    visitor didn't intend). Returns None when the list succeeded but no
    project matches `name` (the normal case: proceed with creation); a
    SupabaseOrgProject when one does (the caller decides whether this
    session actually owns it -- i.e. already knows its db_pass -- before
    treating it as safe to adopt); SupabaseApiFailed if the list call
    itself failed."""
    try:
        async with httpx.AsyncClient(base_url=SUPABASE_API_BASE, timeout=15.0) as client:
            response = await client.get(
                f"/organizations/{organization_slug}/projects",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError:
        return SupabaseApiFailed(reason="supabase_unreachable")

    if response.status_code == 401:
        return SupabaseApiFailed(reason="unauthorized")
    if response.status_code == 403:
        return SupabaseApiFailed(
            reason="insufficient_permissions", message=_extract_message(response)
        )
    if response.status_code == 429:
        return SupabaseApiFailed(reason="rate_limited")
    if response.status_code != 200:
        return SupabaseApiFailed(reason="supabase_unreachable")

    try:
        projects = response.json()["projects"]
        match = next((p for p in projects if str(p.get("name")) == name), None)
    except (ValueError, KeyError, TypeError):
        return SupabaseApiFailed(reason="supabase_unreachable")
    if match is None:
        return None
    try:
        return SupabaseOrgProject(
            ref=str(match["ref"]), name=str(match["name"]), status=str(match["status"])
        )
    except (KeyError, TypeError):
        return SupabaseApiFailed(reason="supabase_unreachable")


def _log_session_tag(session_id: str) -> str:
    """A one-way, non-reversible correlation tag for log lines -- lets an
    operator match up log entries for the same request without the log
    ever carrying the session cookie itself (which authenticates every
    relay endpoint in router.py, so it's a credential, not an id)."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:8]


@dataclasses.dataclass(frozen=True)
class SupabaseConnectionInfo:
    db_user: str
    db_host: str
    db_port: int
    db_name: str


async def get_connection_info(
    access_token: str, ref: str, session_id: str
) -> SupabaseConnectionInfo | SupabaseApiFailed:
    """GET /v1/projects/{ref}/config/database/pooler — selects the
    session-mode (port 5432) PRIMARY entry, matching the manual guide's
    existing "Session-mode pooler, not transaction mode" requirement.
    Deliberately never reads Supabase's own connection_string/
    connectionString fields (see module docstring) — the caller (the
    router, which already holds db_pass server-side) assembles the final
    connection string itself from this non-secret shape.

    Root-caused 2026-09-02 (was a standing TEMPORARY diagnostic before
    this): for projects created after Supabase's pooler-config API change,
    this endpoint lists only a "transaction" PRIMARY entry, no "session"
    one. Session and transaction mode share the same pooler host/user —
    Supavisor selects between them purely by port (5432 vs 6543; see
    guide/setup/hosted/05-supabase.md) — so when no session entry is
    listed, this falls back to the transaction entry's host/user/name with
    the port forced to 5432 to still get session-mode semantics.

    `session_id` is the onboarding session cookie value -- the sole
    authenticator for every relay endpoint in router.py, so it IS a
    credential and must never appear in a log line. The diagnostic logs
    below tag themselves with a one-way hash of it (`_log_session_tag`)
    for log correlation instead of the raw value."""
    try:
        async with httpx.AsyncClient(base_url=SUPABASE_API_BASE, timeout=15.0) as client:
            response = await client.get(
                f"/projects/{ref}/config/database/pooler",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError:
        return SupabaseApiFailed(reason="supabase_unreachable")

    if response.status_code == 401:
        return SupabaseApiFailed(reason="unauthorized")
    if response.status_code == 403:
        return SupabaseApiFailed(
            reason="insufficient_permissions", message=_extract_message(response)
        )
    if response.status_code == 429:
        return SupabaseApiFailed(reason="rate_limited")
    if response.status_code != 200:
        return SupabaseApiFailed(reason="supabase_unreachable")

    try:
        entries = response.json()
    except ValueError:
        logger.info(
            "connection-info [%s]: response body did not parse as JSON",
            _log_session_tag(session_id),
        )
        return SupabaseApiFailed(reason="pooler_config_unavailable")

    try:
        session_entry = next(
            (
                e
                for e in entries
                if e.get("pool_mode") == "session" and e.get("database_type") == "PRIMARY"
            ),
            None,
        )
        matched = session_entry
        if matched is None:
            matched = next(
                e
                for e in entries
                if e.get("pool_mode") == "transaction" and e.get("database_type") == "PRIMARY"
            )
        db_user = str(matched["db_user"])
        db_host = str(matched["db_host"])
        db_name = str(matched["db_name"])
        db_port = int(matched["db_port"]) if session_entry is not None else 5432
    except (ValueError, KeyError, TypeError, StopIteration, AttributeError):
        # Diagnostic for the case neither a session nor a transaction
        # PRIMARY entry is present at all — logs only the two label fields
        # being matched on, never db_user/db_host/connection_string.
        try:
            shapes = [(e.get("pool_mode"), e.get("database_type")) for e in entries]
        except (TypeError, AttributeError):
            # Never repr `entries` itself here -- if the response shape is
            # a single object rather than a list (exactly the kind of
            # mismatch this diagnostic exists to catch), it may carry
            # db_host/connection_string/other credential-adjacent fields.
            # A type name and, for a dict, its key names only (values
            # discarded) is the safe presence-check shape this project's
            # secret-handling rules require.
            if isinstance(entries, dict):
                shapes = f"entries was a single dict with keys: {sorted(entries)}"
            else:
                shapes = f"entries was not a list of objects: {type(entries).__name__}"
        logger.info(
            "connection-info [%s]: no session/PRIMARY match; entries seen: %s",
            _log_session_tag(session_id),
            shapes,
        )
        return SupabaseApiFailed(reason="pooler_config_unavailable")
    return SupabaseConnectionInfo(
        db_user=db_user, db_host=db_host, db_port=db_port, db_name=db_name
    )
