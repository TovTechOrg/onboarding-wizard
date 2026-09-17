"""router.py — the wizard's only HTTP surface: GET / (the static
page) and one relay endpoint per external service. Every relay endpoint
returns a verdict, never the credential it was given.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets as _secrets
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator, model_validator

import github_client
import llm_client
import render_client
import session_store
import supabase_client
import uptimerobot_client

router = APIRouter()
logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_HTML = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

# The vendored bot contract -- same read shape as
# tests/test_model_validation_conformance.py and
# tests/test_bot_contract_parity.py use. Read once at import time: the
# contract is a committed file, never changes at runtime, and every
# consumer below needs the same parsed dict.
_CONTRACT = json.loads(
    (Path(__file__).parent / "contracts/provisioning.json").read_text(encoding="utf-8")
)

SESSION_COOKIE_NAME = "onboarding_session"


def _get_session_id(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(session_store.SESSION_TTL.total_seconds()),
    )


# session_store.py's functions are sync (real Postgres calls) -- every
# endpoint below awaits them through these thin asyncio.to_thread wrappers
# rather than calling them directly, so a request's DB round-trips never
# block the event loop for every other concurrent request. Looked up via
# `session_store.<name>` at call time (not captured at import time), so
# these stay correct under tests that monkeypatch the module's functions.
async def _create_session() -> str:
    return await asyncio.to_thread(session_store.create_session)


async def _get_session(session_id: str) -> session_store.SessionData | None:
    return await asyncio.to_thread(session_store.get_session, session_id)


async def _read_frame(session_id: str, frame: str) -> dict | None:
    return await asyncio.to_thread(session_store.read_frame, session_id, frame)


async def _update_frame(
    session_id: str, frame: str, data: dict, *, replace: bool = False
) -> session_store.SessionNotFound | None:
    return await asyncio.to_thread(
        session_store.update_frame, session_id, frame, data, replace=replace
    )


async def _delete_session(session_id: str) -> None:
    await asyncio.to_thread(session_store.delete_session, session_id)


class RenderKeyRequest(BaseModel):
    api_key: str = Field(max_length=512)


class GithubValidateAppRequest(BaseModel):
    # App ID + private key are pasted in by the visitor after hand-creating
    # the App in GitHub's UI (see CLAUDE.md) -- validate_app()
    # reads the App's actual live configuration back from GitHub rather
    # than trusting anything about how it was created.
    app_id: int = Field(gt=0)
    private_key_b64: str = Field(max_length=16384)
    # Computed client-side from the already-known Render service URL, sent
    # up rather than recomputed server-side -- this service holds no state
    # to recompute it from.
    expected_webhook_url: str = Field(
        min_length=1, max_length=2048, pattern=r"^https?://[^\s\"'<>\\]+$"
    )
    # Generated client-side (never sent to GitHub, only used later as
    # GITHUB_WEBHOOK_SECRET), so it rides along here purely for session
    # storage -- validate_app()'s own logic never reads it.
    webhook_secret: str = Field(min_length=1, max_length=512)


class SupabaseKeyRequest(BaseModel):
    key: str = Field(min_length=1, max_length=512)
    # True only for the error-recovery resubmission (a new token after a
    # permission gap) -- merges api_key into the frame instead of the
    # default full replace, so an already-created project's ref/db_pass
    # survive the token swap. See docs/subprojects/supabase.md.
    preserve_project: bool = False


class SupabaseCreateProjectRequest(BaseModel):
    organization_slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)


class LlmGeminiListModelsRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)


class LlmGroqListModelsRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)


class LlmVertexListModelsRequest(BaseModel):
    service_account_key_b64: str = Field(min_length=1, max_length=16384)
    project: str | None = None
    location: str | None = None


class UptimeRobotCreateMonitorRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)
    render_service_url: str = Field(min_length=1, max_length=2048)

    @field_validator("render_service_url")
    @classmethod
    def _normalize_render_service_url(cls, value: str) -> str:
        """Strip first, then require non-empty -- min_length=1 alone does not
        survive stripping.

        A whitespace-only value passes min_length=1, and
        uptimerobot_client._target_url's own .strip() then derives the bare
        relative path "/healthz", which would be POSTed to UptimeRobot as a
        monitor URL. Rejecting it here turns a nonsense monitor (or an
        opaque provider-side 400 surfaced as `request_rejected`) into an
        honest 422. No shape/regex check beyond strip-then-require-non-empty:
        this endpoint takes no session cookie at all (the visitor's
        UptimeRobot key isn't persisted server-side until after a
        successful create), so render_service_url IS caller-supplied
        request-body input here, not something this endpoint reads back
        from the session -- in the normal wizard UI it's pre-filled from
        the wizard's own sessionStorage mirror of frame 6's URL (sub-project
        6's forward contract), but a direct API caller can send anything.
        Real impact of that is low (UptimeRobot, not this server, is what
        ends up pinging whatever URL is submitted), but this validator's
        purpose is honest input hygiene, not authorization.
        """
        value = value.strip()
        if not value:
            raise ValueError("render_service_url must not be empty or whitespace-only")
        return value


class RenderServiceCreateRequest(BaseModel):
    repo_url: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=64)


class LlmConfirmRequest(BaseModel):
    provider: str = Field(pattern=r"^(gemini|groq|vertex)$")
    credential_value: str = Field(min_length=1, max_length=16384)
    model: str = Field(min_length=1, max_length=256)
    vertex_gcp_project: str | None = None
    vertex_gcp_location: str | None = None

    @model_validator(mode="after")
    def _pair_belongs_to_vertex_only(self) -> "LlmConfirmRequest":
        """Field() alone cannot say "required only when a sibling field has
        one particular value". A gemini/groq submission carrying a region is
        a malformed request, not a field to quietly ignore."""
        has_pair = self.vertex_gcp_project is not None and self.vertex_gcp_location is not None
        if self.provider == "vertex" and not has_pair:
            raise ValueError("vertex requires a project and a location")
        if self.provider != "vertex" and (
            self.vertex_gcp_project is not None or self.vertex_gcp_location is not None
        ):
            raise ValueError("only vertex carries a project/location")
        return self


class DashboardAuthConfirmRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=256)
    session_secret: str = Field(min_length=32, max_length=256)


# A Vertex location is part of the outbound hostname
# ({location}-aiplatform.googleapis.com), so every submitted value is pinned
# to the set the bot's contract declares -- an allowlist, never a pattern.
# "evil-attacker-host" satisfies any reasonable regex; membership is what
# makes the reachable-host set closed by construction. Same vulnerability
# class as the token_uri/universe_domain finding in ISSUES.md.
_VERTEX_LOCATIONS: tuple[str, ...] = tuple(_CONTRACT["vertex_locations"]["options"])
_VERTEX_DEFAULT_LOCATION: str = _CONTRACT["vertex_locations"]["default"]

# GCP's own project-id rule. A project is a path segment, not a host, so this
# is a clarity check rather than a security boundary -- it turns a malformed
# value into a clear verdict instead of an opaque Google API error.
_VERTEX_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


def _valid_vertex_location(value: str) -> bool:
    return value in _VERTEX_LOCATIONS


def _valid_vertex_project(value: str) -> bool:
    return bool(_VERTEX_PROJECT_RE.fullmatch(value))


# Paired comment with the sibling review-engine project's (~/pr-review-bot)
# providers/registry.py::PROVIDERS -- kept in sync by hand, nothing
# automated ties the two together, so this 3-entry mapping is a deliberate
# copy, not a shared import. Keep in sync if a provider's env var names
# ever change there.
_LLM_ENV_VAR_NAMES = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL"),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL"),
    "vertex": ("VERTEX_GCP_SERVICE_ACCOUNT_KEY", "VERTEX_MODEL"),
}

# Duplicated (not imported) from the sibling review-engine project's
# (~/pr-review-bot) providers/registry.py::KEY_INDEX_COLUMNS -- same
# duplication-not-import convention as _LLM_ENV_VAR_NAMES above. A hardcoded
# whitelist, not a naming convention derived at call time -- _seed_provider_config
# below looks the column name up through this dict rather than building it
# from `provider`, so this dict IS the injection guard, mirroring the
# reasoning given for KEY_INDEX_COLUMNS itself over there.
_KEY_INDEX_COLUMNS = {
    "gemini": "gemini_key_index",
    "groq": "groq_key_index",
    "vertex": "vertex_key_index",
}

# The sibling review-engine project's (~/pr-review-bot) config.py's
# OPERATIONAL_KEYS entries that its deploy.py's --sync-env pushes as Render
# env vars on every sync (its _ALWAYS_SYNCED -- NOT _GENERIC_OPERATIONAL_ENV_ATTRS,
# which is empty there as of its 2026-09-10 cross-repo-contract-direction
# work), with their config.py Settings field defaults hardcoded here -- same
# duplication-not-import pattern as _LLM_ENV_VAR_NAMES above. Kept in sync by
# hand, but no longer entirely unchecked: tests/test_bot_contract_parity.py's
# test_generic_operational_env_defaults_are_all_render_pushable asserts every
# key here against that project's vendored contracts/provisioning.json, which
# catches a rename or a placement move on the next `pytest` run rather than
# leaving this wizard pushing a stale name forever with nothing red anywhere.
#
# As of that project's 2026-09-08 slotted-config-and-db-delegation work,
# the 9 dispatcher/timeout tuning knobs (plus VERTEX_GCP_PROJECT/_LOCATION
# and every provider's model var) are DB-only there -- no Render env var at
# all. This wizard follows suit: those 11 keys are removed from here
# entirely (nothing to push -- the 9 tuning knobs get their real value from
# that project's own boot-time backfill, review_queue/store.py::
# _backfill_runtime_config, the moment the newly-deployed service boots for
# the first time; model/project/location are seeded directly by this wizard
# into slot_config below, since THAT has no automatic first-boot seed of its
# own).
#
# GITHUB_TARGET_REPO used to be excluded for the same reason blank-default
# keys were (Render's API rejects an empty value outright, ISSUES.md
# 2026-08-17) but that project's main.py lifespan now refuses to boot at
# all without it explicitly set -- config.py's target_repos() names "*" the
# required, operator-set sentinel for "no restriction" (see that project's
# 2026-09-07 config.py change). This wizard has no frame that collects a
# repo allowlist from the visitor, and every instance it provisions is a
# track-all install, so "*" is pushed unconditionally here rather than
# left for the operator to discover the hard way via a boot-looping deploy.
_GENERIC_OPERATIONAL_ENV_DEFAULTS = {
    "GITHUB_TARGET_REPO": "*",
}

# Duplicated (not imported) from the sibling review-engine project's
# (~/pr-review-bot) review_queue/store.py::_SCHEMA -- same
# duplication-not-import convention as _LLM_ENV_VAR_NAMES/
# _GENERIC_OPERATIONAL_ENV_DEFAULTS above. A freshly-provisioned Supabase
# database has no schema at all yet (that project's own store.init_pool()
# is what normally creates it, the first time the deployed service itself
# boots) -- this wizard runs BEFORE that first boot (the visitor must not
# be able to reach the deploy trigger without slot_config already seeded),
# so it has to create just this one table itself. CREATE TABLE IF NOT
# EXISTS makes this safe to run again once the real service boots and
# creates its own full schema: this is a no-op against a table that
# already exists, with an identical shape either way.
_SLOT_CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS slot_config (
    provider            TEXT    NOT NULL,
    slot_index          INTEGER NOT NULL,
    model               TEXT,
    vertex_gcp_project  TEXT,
    vertex_gcp_location TEXT,
    updated_at          TEXT    NOT NULL,
    PRIMARY KEY (provider, slot_index)
);
ALTER TABLE slot_config ENABLE ROW LEVEL SECURITY;
"""

# Duplicated (not imported) from the sibling review-engine project's
# (~/pr-review-bot) review_queue/store.py -- same duplication-not-import
# convention as _SLOT_CONFIG_SCHEMA above, and for the same reason: a
# freshly-provisioned Supabase database has no schema at all yet (that
# project's own store.init_pool() is what normally creates it, on the
# deployed service's first boot), and this wizard runs BEFORE that first
# boot.
#
# DELIBERATELY NARROWER than that project's full 22-column
# RUNTIME_CONFIG_COLUMNS: exactly contracts/provisioning.json's
# runtime_config.provisioner_required (id, provider, updated_at) plus all
# three provisioner_required_one_of columns. All three key-index columns are
# declared even though only the chosen provider's is ever written -- the
# column has to exist before the INSERT below can name it.
#
# This used to have to be the FULL column set, because CREATE TABLE IF NOT
# EXISTS is a no-op against an existing table and that project's own
# init_pool() would then never widen it. **That is no longer true**: as of
# its 2026-09-10 bot-owned-defaults work, init_pool() widens both tables
# itself with ALTER TABLE ... ADD COLUMN IF NOT EXISTS
# (review_queue/store.py::_widen_statements) and then backfills every NULL
# column from its own declared Settings defaults (_backfill_runtime_config,
# which uses COALESCE per column and so cannot clobber a value this wizard or
# an operator wrote). A narrower table here is now self-healing, and this
# wizard carrying a copy of that project's 15 operational defaults was
# exactly the hand-duplication the contract exists to end. Do NOT re-add the
# tuning columns.
# tests/test_bot_contract_parity.py asserts this covers provisioner_required
# and declares nothing the bot backfills.
_RUNTIME_CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_config (
    id                  INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    provider            TEXT,
    updated_at          TEXT NOT NULL,
    gemini_key_index    INTEGER,
    groq_key_index      INTEGER,
    vertex_key_index    INTEGER
);
ALTER TABLE runtime_config ENABLE ROW LEVEL SECURITY;
"""

_DB_CONNECT_TIMEOUT = 10


def _seed_provider_config(
    database_url: str,
    provider: str,
    model: str,
    vertex_gcp_project: str | None,
    vertex_gcp_location: str | None,
) -> bool:
    """Write slot 0's model (and for vertex, project/location) into
    slot_config, and provider/key_index (always 0) into runtime_config,
    directly into the newly-provisioned instance's own database -- see
    docs/superpowers/specs/2026-09-08-slotted-config-and-db-delegation-
    design.md section 5 and pr-review-bot's docs/superpowers/specs/2026-09-09-
    provider-key-index-db-only-design.md (which generalized that design's
    DB-only treatment from model to provider selection itself): a
    wizard-provisioned service must never boot into "no env fallback, no DB
    row either" for its own just-configured provider. Always slot 0: this
    wizard has no UI for choosing a numbered credential slot, it only ever
    provisions the base credential.

    Writes ONLY what this wizard uniquely knows: provider, the chosen
    provider's key-slot index, and updated_at. As of pr-review-bot's
    2026-09-10 cross-repo-contract-direction work, every other
    runtime_config column is the deployed service's own to fill at boot --
    its store.init_pool() widens a narrower table with
    ALTER TABLE ... ADD COLUMN IF NOT EXISTS and backfills every NULL from
    its own declared Settings defaults, via COALESCE so it can never clobber
    a value an operator has since set through the dashboard. This wizard no
    longer carries a copy of those 15 operational defaults (see
    contracts/provisioning.json's runtime_config.bot_backfilled, vendored
    from that project) -- see ISSUES.md's 2026-09-09 entry for the incident
    that a stale copy of this exact hand-duplication caused before the
    contract existed.

    Both writes share one connection/transaction -- a failure partway
    through must never leave slot_config seeded with no matching
    runtime_config.provider, or vice versa. Raw, short-timeout connection
    (not a pool) -- a one-shot provisioning step,
    same reason pr-review-bot's own scripts/deploy.py avoids
    store.init_pool()'s 30s pool timeout for this kind of single write.
    Returns True on success, False on any failure (never raises) -- the
    caller reports this the same way it reports a Render push failure, so
    the visitor cannot reach a state where Render has the credential but
    the DB has no matching model/provider row, or vice versa.
    """
    key_index_column = _KEY_INDEX_COLUMNS[provider]
    try:
        with psycopg.connect(
            database_url,
            connect_timeout=_DB_CONNECT_TIMEOUT,
            # connect_timeout only bounds the TCP/auth handshake -- these
            # bound the DDL/INSERT themselves, so a stalled statement (e.g.
            # a lock wait behind the deployed bot's own concurrent
            # store.init_pool() creating the same table) can't hang the
            # shared asyncio.to_thread pool every other session-store call
            # in this file also uses.
            options="-c statement_timeout=15000 -c lock_timeout=5000",
        ) as conn:
            conn.execute(_SLOT_CONFIG_SCHEMA)
            conn.execute(_RUNTIME_CONFIG_SCHEMA)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO slot_config "
                "(provider, slot_index, model, vertex_gcp_project, vertex_gcp_location, "
                "updated_at) "
                "VALUES (%s, 0, %s, %s, %s, %s) "
                "ON CONFLICT (provider, slot_index) DO UPDATE SET "
                "model = EXCLUDED.model, "
                "vertex_gcp_project = EXCLUDED.vertex_gcp_project, "
                "vertex_gcp_location = EXCLUDED.vertex_gcp_location, "
                "updated_at = EXCLUDED.updated_at",
                (provider, model, vertex_gcp_project, vertex_gcp_location, now),
            )
            # key_index_column is looked up through _KEY_INDEX_COLUMNS above,
            # never built from `provider`, so that dict IS the injection
            # guard for the f-strings here. All three columns are
            # unconditionally overwritten on every call -- a redo of the
            # LLM-provider frame really is a reconfiguration of exactly
            # these three, and they are the only runtime_config values this
            # wizard has any claim to. Every other column is the deployed
            # service's own to fill at boot (see _RUNTIME_CONFIG_SCHEMA's
            # comment): this wizard no longer writes, and no longer needs a
            # COALESCE to avoid clobbering, any operational default.
            conn.execute(
                "INSERT INTO runtime_config "
                f"(id, provider, {key_index_column}, updated_at) "
                "VALUES (1, %s, 0, %s) "
                "ON CONFLICT (id) DO UPDATE SET "
                "provider = EXCLUDED.provider, "
                f"{key_index_column} = EXCLUDED.{key_index_column}, "
                "updated_at = EXCLUDED.updated_at",
                (provider, now),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        # A type name only -- never the exception's own message/args, which
        # for a psycopg connection error can embed the DSN (and therefore
        # the visitor's db_pass) verbatim.
        logger.info("provider config seed failed: %s", type(exc).__name__)
        return False


def _render_index() -> HTMLResponse:
    # No operator-level Supabase secret to template -- the frame's
    # credential is a visitor-pasted Personal Access Token now.
    return HTMLResponse(
        _INDEX_HTML,
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "form-action 'self'; frame-ancestors 'none'"
            ),
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return _render_index()


@router.get("/api/session")
async def get_session_state(request: Request) -> dict:
    """Maps session_store's backend frame_data keys onto the wizard's UI
    frame ids -- not a 1:1 pass-through, since "render" backs three
    distinct UI frames (render-key, render-service, render-deploy's
    pending_deploy_id) and "supabase" has a genuine in-between state
    (OAuth done, project not yet created) that isn't just "locked" or
    "complete". Never echoes a credential value -- see CLAUDE.md's
    secret-handling section and this file's own module docstring."""
    session_id = _get_session_id(request)
    if session_id is None:
        return {"frames": {}}
    session = (await _get_session(session_id))
    if session is None:
        return {"frames": {}}
    data = session.frames
    frames: dict[str, dict] = {}

    render = data.get("render")
    if render and "api_key" in render:
        frames["render-key"] = {
            "complete": True, "display": {"owner_name": render.get("owner_name")}
        }
    if render and "service_id" in render:
        frames["render-service"] = {
            "complete": True,
            # service_id isn't a credential (an identifier for the
            # visitor's own Render service, like installation_id is for
            # GitHub) -- the frontend's local render-service mirror needs
            # it to gate later actions (trigger-deploy, bulk-push) the same
            # way it does right after an in-page completion.
            "display": {
                "service_id": render.get("service_id"),
                "service_url": render.get("service_url"),
            },
        }
    if render and render.get("deployed"):
        frames["render-deploy"] = {
            "complete": True, "display": {"service_url": render.get("service_url")}
        }
    elif render and render.get("pending_deploy_id"):
        # A deploy was triggered but hasn't (yet, or ever will) reach
        # "live" -- resume polling rather than showing the Deploy button
        # again, same in-between-state shape as supabase's "ref" case
        # below. pending_deploy_id isn't a credential (Render's own deploy
        # identifier, like service_id above) -- the frontend's local mirror
        # needs it to gate the "Check again" button the same way it does
        # right after an in-page trigger.
        frames["render-deploy"] = {
            "complete": False, "provisioning": True,
            "display": {"pending_deploy_id": render.get("pending_deploy_id")},
        }

    if data.get("dashboard_auth"):
        frames["dashboard-auth"] = {"complete": True, "display": {}}

    if data.get("github_app"):
        frames["github-app"] = {"complete": True, "display": {}}

    supabase = data.get("supabase")
    if supabase and "database_url" in supabase:
        frames["supabase"] = {"complete": True, "display": {"name": supabase.get("name")}}
    elif supabase and "ref" in supabase:
        # Project created, but connection-info hasn't run yet (e.g. a
        # reload during the ~2 minute provisioning wait) -- resume
        # polling, don't report complete without a DATABASE_URL for the
        # final deploy step to find.
        frames["supabase"] = {
            "complete": False,
            "provisioning": True,
            "display": {"ref": supabase.get("ref"), "name": supabase.get("name")},
        }
    # A key validated but no project created yet reports as not present in
    # frames at all -- the same gap the Render frame already leaves between
    # key validation and service creation. There's no redirect round-trip
    # to resume from anymore, so no separate "authorized" in-between state
    # is needed here.

    llm_provider = data.get("llm_provider")
    if llm_provider:
        display = {
            "provider": llm_provider.get("provider"),
            "model": llm_provider.get("model"),
        }
        if llm_provider.get("provider") == "vertex":
            # Non-secret configuration values (a project id, a region) --
            # the same class of field CLAUDE.md already permits relay
            # responses to carry. restoreFromSession() doesn't read either
            # today (only display.provider), but the frame's own confirmed
            # choice should be inspectable via this endpoint the same way
            # every other frame's non-secret state already is.
            display["vertex_gcp_project"] = llm_provider.get("vertex_gcp_project")
            display["vertex_gcp_location"] = llm_provider.get("vertex_gcp_location")
        frames["llm-provider"] = {"complete": True, "display": display}

    if data.get("uptime_pinger"):
        frames["uptime-pinger"] = {"complete": True, "display": {}}

    return {"frames": frames}


@router.post("/api/session/reset")
async def reset_session(request: Request, response: Response) -> Response:
    session_id = _get_session_id(request)
    if session_id is not None:
        (await _delete_session(session_id))
    response.delete_cookie(SESSION_COOKIE_NAME)
    response.status_code = 204
    return response


@router.post("/api/render/validate-key")
async def validate_render_key(
    payload: RenderKeyRequest, request: Request, response: Response
) -> dict:
    result = await render_client.validate_key(payload.api_key)
    if isinstance(result, render_client.RenderKeyValid):
        # The wizard's session entry point -- the ONLY endpoint allowed to
        # call create_session(). Every other endpoint below requires an
        # existing session and fails closed instead (session_store.py's
        # update_frame() enforces this at the storage layer too).
        session_id = _get_session_id(request)
        if session_id is None or (await _get_session(session_id)) is None:
            session_id = (await _create_session())
            _set_session_cookie(response, session_id)
        # replace=True: a resubmitted Render key (via the "Change" flow)
        # must discard any previous service_id/service_url/
        # pending_deploy_id -- those belong to whatever Render account the
        # OLD key authenticated as, and may not even exist under the new
        # one. A plain merge would leave them behind for GET /api/session
        # to report as still complete.
        write_result = await _update_frame(
            session_id,
            "render",
            {"api_key": payload.api_key, "owner_name": result.owner_name},
            replace=True,
        )
        if isinstance(write_result, session_store.SessionNotFound):
            # The session was created microseconds ago -- only reachable if
            # something else (a concurrent reset) deleted it in that gap.
            # An unpersisted "success" isn't real (design spec section 3.6).
            return {"valid": False, "reason": "no_session"}
        return {"valid": True, "owner_name": result.owner_name}
    return {"valid": False, "reason": result.reason}


@router.post("/api/github/validate-app")
async def validate_github_app(payload: GithubValidateAppRequest, request: Request) -> dict:
    result = await github_client.validate_app(
        payload.app_id, payload.private_key_b64, payload.expected_webhook_url
    )
    if isinstance(result, github_client.AppCredentialsInvalid):
        return {"valid": False, "reason": result.reason}

    if isinstance(result.installation, github_client.InstallationFound):
        installation = {
            "status": "found",
            "installation_id": result.installation.installation_id,
            "account_login": result.installation.account_login,
            "repo_scope": result.installation.repo_scope,
        }
        installation_ok = True
        installation_id = result.installation.installation_id
    elif isinstance(result.installation, github_client.MultipleInstallationsFound):
        installation = {"status": "multiple", "account_logins": result.installation.account_logins}
        installation_ok = False
        installation_id = None
    else:
        installation = {"status": "none"}
        installation_ok = False
        installation_id = None

    all_ok = (
        all(p.ok for p in result.permissions)
        and all(e.ok for e in result.events)
        and installation_ok
        and result.webhook.ok
    )
    if all_ok:
        # Best-effort: a directly-called endpoint with no session yet (e.g.
        # a visitor re-validating before frame 1 is done) still gets a live
        # checklist result -- the frontend's own frame-lock sequencing is
        # what normally prevents reaching this frame without an earlier
        # session existing.
        session_id = _get_session_id(request)
        if session_id is not None and (await _get_session(session_id)) is not None:
            await _update_frame(
                session_id,
                "github_app",
                {
                    "app_id": payload.app_id,
                    "private_key_b64": payload.private_key_b64,
                    "webhook_secret": payload.webhook_secret,
                    "installation_id": installation_id,
                },
            )
    return {
        "valid": True,
        "all_ok": all_ok,
        "permissions": [
            {"name": p.name, "wanted": p.wanted, "actual": p.actual, "ok": p.ok}
            for p in result.permissions
        ],
        "events": [{"name": e.name, "ok": e.ok} for e in result.events],
        "installation": installation,
        "webhook": {"ok": result.webhook.ok, "actual_url": result.webhook.actual_url},
    }


@router.post("/api/supabase/validate-key")
async def validate_supabase_key(payload: SupabaseKeyRequest, request: Request) -> dict:
    session_id = _get_session_id(request)
    if session_id is None or (await _get_session(session_id)) is None:
        return {"valid": False, "reason": "no_session"}
    result = await supabase_client.validate_key(payload.key)
    if isinstance(result, supabase_client.SupabaseKeyValid):
        # replace=True (the default): a resubmitted key via "Change" must
        # discard any previous ref/db_pass/database_url outright -- see
        # test_validate_supabase_key_discards_a_previous_projects_data.
        # preserve_project=True (the error-recovery resubmission) instead
        # merges api_key only, keeping an already-created project's
        # ref/db_pass intact -- see
        # test_validate_supabase_key_preserves_project_when_requested.
        write_result = await _update_frame(
            session_id,
            "supabase",
            {"api_key": payload.key},
            replace=not payload.preserve_project,
        )
        if isinstance(write_result, session_store.SessionNotFound):
            return {"valid": False, "reason": "no_session"}
        return {
            "valid": True,
            "orgs": [{"slug": o.slug, "name": o.name} for o in result.orgs],
        }
    if result.message:
        return {"valid": False, "reason": result.reason, "message": result.message}
    return {"valid": False, "reason": result.reason}


@router.post("/api/supabase/create-project")
async def create_supabase_project(payload: SupabaseCreateProjectRequest, request: Request) -> dict:
    session_id = _get_session_id(request)
    supabase_frame = session_id and (await _read_frame(session_id, "supabase"))
    if not supabase_frame or "api_key" not in supabase_frame:
        return {"valid": False, "reason": "no_session"}

    # Checked before ever attempting to create: the error-recovery flow can
    # resubmit the same org/name after swapping tokens (preserve_project),
    # and must never re-provision a second project for what's really just a
    # permission retry. See docs/subprojects/supabase.md.
    existing = await supabase_client.find_org_project_by_name(
        supabase_frame["api_key"], payload.organization_slug, payload.name
    )
    if isinstance(existing, supabase_client.SupabaseApiFailed):
        if existing.message:
            return {"valid": False, "reason": existing.reason, "message": existing.message}
        return {"valid": False, "reason": existing.reason}
    if existing is not None:
        # Only safe to adopt if THIS session already knows this exact
        # project's db_pass (i.e. it created it earlier in this same
        # session) -- Supabase's API never returns a project's password
        # after creation, so a name collision this session doesn't own is
        # a dead end for building a working DATABASE_URL, not something to
        # silently adopt.
        owns_it = supabase_frame.get("ref") == existing.ref and "db_pass" in supabase_frame
        if owns_it:
            return {
                "valid": True,
                "ref": existing.ref,
                "status": existing.status,
                "name": payload.name,
            }
        return {"valid": False, "reason": "project_name_taken"}

    db_pass = _secrets.token_urlsafe(24)
    result = await supabase_client.create_project(
        supabase_frame["api_key"], payload.organization_slug, payload.name, db_pass
    )
    if isinstance(result, supabase_client.SupabaseProjectCreated):
        write_result = await _update_frame(
            session_id,
            "supabase",
            {
                "ref": result.ref,
                "status": result.status,
                "db_pass": db_pass,
                "organization_slug": payload.organization_slug,
                "name": payload.name,
            },
        )
        if isinstance(write_result, session_store.SessionNotFound):
            # The project WAS created in Supabase -- this only leaves the
            # session unable to find it again (ref/db_pass lost), not the
            # visitor's Supabase account in a bad state. Reporting failure
            # here is about this wizard's own bookkeeping, not Supabase's.
            return {"valid": False, "reason": "no_session"}
        return {"valid": True, "ref": result.ref, "status": result.status, "name": payload.name}
    if isinstance(result, supabase_client.SupabaseProjectRejected):
        return {"valid": False, "reason": "project_creation_rejected", "message": result.message}
    if result.message:
        return {"valid": False, "reason": result.reason, "message": result.message}
    return {"valid": False, "reason": result.reason}


@router.post("/api/supabase/project-status")
async def get_supabase_project_status(request: Request) -> dict:
    session_id = _get_session_id(request)
    supabase_frame = session_id and (await _read_frame(session_id, "supabase"))
    if not supabase_frame or "api_key" not in supabase_frame or "ref" not in supabase_frame:
        return {"valid": False, "reason": "no_session"}
    result = await supabase_client.get_project_status(
        supabase_frame["api_key"], supabase_frame["ref"]
    )
    if isinstance(result, supabase_client.SupabaseProjectStatus):
        return {"valid": True, "status": result.status}
    if result.message:
        return {"valid": False, "reason": result.reason, "message": result.message}
    return {"valid": False, "reason": result.reason}


@router.post("/api/supabase/connection-info")
async def get_supabase_connection_info(request: Request) -> dict:
    session_id = _get_session_id(request)
    supabase_frame = session_id and (await _read_frame(session_id, "supabase"))
    required = ("api_key", "ref", "db_pass")
    if not supabase_frame or not all(k in supabase_frame for k in required):
        return {"valid": False, "reason": "no_session"}
    result = await supabase_client.get_connection_info(
        supabase_frame["api_key"], supabase_frame["ref"], session_id=session_id
    )
    if isinstance(result, supabase_client.SupabaseConnectionInfo):
        database_url = (
            f"postgresql://{result.db_user}:{supabase_frame['db_pass']}"
            f"@{result.db_host}:{result.db_port}/{result.db_name}"
        )
        write_result = await _update_frame(session_id, "supabase", {"database_url": database_url})
        if isinstance(write_result, session_store.SessionNotFound):
            return {"valid": False, "reason": "no_session"}
        return {"valid": True}
    if result.message:
        return {"valid": False, "reason": result.reason, "message": result.message}
    return {"valid": False, "reason": result.reason}


@router.post("/api/llm/gemini/list-models")
async def list_gemini_models(payload: LlmGeminiListModelsRequest) -> dict:
    result = await llm_client.list_gemini_models(payload.api_key)
    if isinstance(result, llm_client.LlmModelsListed):
        return {"valid": True, "models": result.models}
    return {"valid": False, "reason": result.reason}


@router.post("/api/llm/groq/list-models")
async def list_groq_models(payload: LlmGroqListModelsRequest) -> dict:
    result = await llm_client.list_groq_models(payload.api_key)
    if isinstance(result, llm_client.LlmModelsListed):
        return {"valid": True, "models": result.models}
    return {"valid": False, "reason": result.reason}


@router.get("/api/llm/vertex/locations")
async def get_vertex_locations() -> dict:
    """A static reference list, not a live call -- there is no API that
    enumerates generative-model-enabled regions per project (see the bot's
    catalog.VERTEX_CATALOG_LOCATIONS docstring). Served rather than
    templated into the page: index.html templates exactly one value
    (supabase_oauth_client_id) and that stays true."""
    return {"locations": list(_VERTEX_LOCATIONS), "default": _VERTEX_DEFAULT_LOCATION}


@router.post("/api/llm/vertex/list-models")
async def list_vertex_models(payload: LlmVertexListModelsRequest) -> dict:
    """Both fields absent means "first validate of a freshly uploaded key":
    list models at the key's own project/default region AND run the one
    projects:search that populates the dropdown. Either present means "the
    visitor changed a dropdown": re-list models for that exact pair only --
    repeating the projects listing per change would be a live call per
    keystroke."""
    if payload.location is not None and not _valid_vertex_location(payload.location):
        return {"valid": False, "reason": "invalid_vertex_location"}
    if payload.project is not None and not _valid_vertex_project(payload.project):
        return {"valid": False, "reason": "invalid_vertex_project"}

    both_absent = payload.project is None and payload.location is None
    # On the both-absent path, list at the contract's OWN default location
    # explicitly rather than leaving list_vertex_models fall back to its
    # own module constant -- the two happen to hold the same string today,
    # but they are two independently maintained values, and the dropdown's
    # preselection (default_location below) must never drift from the
    # region the listing was actually scoped to.
    location = _VERTEX_DEFAULT_LOCATION if both_absent else payload.location
    result = await llm_client.list_vertex_models(
        payload.service_account_key_b64, payload.project, location
    )
    if not isinstance(result, llm_client.VertexModelsListed):
        return {"valid": False, "reason": result.reason}
    response = {"valid": True, "project_id": result.project_id, "models": result.models}
    if both_absent:
        # Mirrors pr-review-bot's dashboard/environment.py::
        # _validate_vertex_credential: a projects:search failure (Cloud
        # Resource Manager disabled, or the account lacking
        # resourcemanager.projects.get -- both common for a service account
        # scoped narrowly to Vertex) never blocks validation. The key's own
        # project is already confirmed usable by the list_vertex_models call
        # above, so it's always a real dropdown option even when the
        # broader project listing fails outright.
        projects = await llm_client.list_accessible_projects(payload.service_account_key_b64)
        response["projects"] = (
            projects.projects
            if isinstance(projects, llm_client.VertexProjectsListed)
            else [result.project_id]
        )
        response["default_project"] = result.project_id
        response["default_location"] = _VERTEX_DEFAULT_LOCATION
    return response


@router.post("/api/llm/confirm")
async def confirm_llm_provider(payload: LlmConfirmRequest, request: Request) -> dict:
    session_id = _get_session_id(request)
    if session_id is None or (await _get_session(session_id)) is None:
        return {"valid": False, "reason": "no_session"}

    if payload.provider == "vertex":
        if not _valid_vertex_location(payload.vertex_gcp_location):
            return {"valid": False, "reason": "invalid_vertex_location"}
        if not _valid_vertex_project(payload.vertex_gcp_project):
            return {"valid": False, "reason": "invalid_vertex_project"}

    # Prove the model is callable BEFORE it becomes session state -- same
    # ordering rule bulk_push_render_env_vars already follows, one frame
    # earlier, so a refused model never reaches GET /api/session as "done".
    # For vertex this probes the exact project/region pair the visitor
    # chose, which is the same pair _seed_provider_config will write.
    # Groq needs no probe (contracts/provisioning.json's model_validation).
    probe: llm_client.LlmModelProbed | llm_client.LlmApiFailed | None = None
    if payload.provider == "vertex":
        probe = await llm_client.probe_vertex_model(
            payload.credential_value,
            payload.model,
            project=payload.vertex_gcp_project,
            location=payload.vertex_gcp_location,
        )
    elif payload.provider == "gemini":
        probe = await llm_client.probe_gemini_model(payload.credential_value, payload.model)
    if isinstance(probe, llm_client.LlmApiFailed):
        return {"valid": False, "reason": probe.reason}

    frame_data = {
        "provider": payload.provider,
        "credential_value": payload.credential_value,
        "model": payload.model,
    }
    if payload.provider == "vertex":
        frame_data["vertex_gcp_project"] = payload.vertex_gcp_project
        frame_data["vertex_gcp_location"] = payload.vertex_gcp_location
    write_result = await _update_frame(session_id, "llm_provider", frame_data)
    if isinstance(write_result, session_store.SessionNotFound):
        return {"valid": False, "reason": "no_session"}
    return {"valid": True}


@router.post("/api/uptimerobot/create-monitor")
async def create_uptimerobot_monitor(
    payload: UptimeRobotCreateMonitorRequest, request: Request
) -> dict:
    result = await uptimerobot_client.create_or_reuse_monitor(
        payload.api_key, payload.render_service_url
    )
    if isinstance(result, uptimerobot_client.UptimeRobotMonitorResult):
        session_id = _get_session_id(request)
        if session_id is not None and (await _get_session(session_id)) is not None:
            await _update_frame(
                session_id,
                "uptime_pinger",
                {"api_key": payload.api_key, "monitor_id": result.monitor_id},
            )
        return {"valid": True, "created": result.created, "monitor_id": result.monitor_id}
    return {"valid": False, "reason": result.reason}


@router.post("/api/uptimerobot/delete-monitor")
async def delete_uptimerobot_monitor(request: Request) -> dict:
    """Best-effort cleanup, called when an earlier frame change (render-key
    or render-service) invalidates a monitor a visitor already created for
    the old service URL -- see static/index.html's
    cleanupOrphanedUptimeMonitor(). Session-backed like every other
    post-redesign endpoint -- the UptimeRobot API key is never resent from
    the browser."""
    session_id = _get_session_id(request)
    uptime_frame = session_id and (await _read_frame(session_id, "uptime_pinger"))
    if not uptime_frame or "api_key" not in uptime_frame or "monitor_id" not in uptime_frame:
        return {"valid": False, "reason": "no_session"}
    result = await uptimerobot_client.delete_monitor(
        uptime_frame["api_key"], uptime_frame["monitor_id"]
    )
    if isinstance(result, uptimerobot_client.UptimeRobotMonitorDeleted):
        return {"valid": True}
    return {"valid": False, "reason": result.reason}


@router.post("/api/render/create-service")
async def create_render_service(payload: RenderServiceCreateRequest, request: Request) -> dict:
    session_id = _get_session_id(request)
    render_frame = session_id and (await _read_frame(session_id, "render"))
    if not render_frame or "api_key" not in render_frame:
        return {"valid": False, "reason": "no_session"}
    result = await render_client.create_service(
        render_frame["api_key"], payload.repo_url, payload.name
    )
    if isinstance(result, render_client.RenderServiceCreated):
        write_result = await _update_frame(
            session_id,
            "render",
            {"service_id": result.service_id, "service_url": result.service_url},
        )
        if isinstance(write_result, session_store.SessionNotFound):
            # The Render service WAS created -- this only leaves the
            # session unable to find it again, not a dangling external
            # resource the visitor doesn't know about (its URL/id are in
            # this response either way).
            return {"valid": False, "reason": "no_session"}
        return {"valid": True, "service_id": result.service_id, "service_url": result.service_url}
    if result.message:
        return {"valid": False, "reason": result.reason, "message": result.message}
    return {"valid": False, "reason": result.reason}


def _push_result(result) -> dict:
    if isinstance(result, render_client.RenderEnvVarsPushed):
        return {"valid": True, "pushed": result.pushed}
    return {"valid": False, "reason": result.reason, "pushed": result.pushed}


@router.post("/api/dashboard-auth/confirm")
async def confirm_dashboard_auth(payload: DashboardAuthConfirmRequest, request: Request) -> dict:
    # No external API to validate against -- this value is wizard/visitor-
    # chosen, never checked against anything else. This endpoint's only job
    # is persisting it to the session for the final bulk push.
    session_id = _get_session_id(request)
    if session_id is None or (await _get_session(session_id)) is None:
        return {"valid": False, "reason": "no_session"}
    write_result = await _update_frame(
        session_id, "dashboard_auth",
        {
            "username": payload.username,
            "password": payload.password,
            "session_secret": payload.session_secret,
        },
    )
    if isinstance(write_result, session_store.SessionNotFound):
        return {"valid": False, "reason": "no_session"}
    return {"valid": True}


@router.post("/api/render/bulk-push-env-vars")
async def bulk_push_render_env_vars(request: Request) -> dict:
    """Replaces the four now-removed per-frame push-render-vars endpoints:
    assembles every completed frame's env vars from the session and pushes
    them to Render in one call, from the final ("render-deploy") frame --
    per the decision made alongside this redesign, no earlier frame pushes
    to Render incrementally anymore."""
    session_id = _get_session_id(request)
    session = session_id and (await _get_session(session_id))
    if not session:
        return {"valid": False, "reason": "no_session"}
    render_frame = session.frames.get("render")
    if not render_frame or "api_key" not in render_frame or "service_id" not in render_frame:
        return {"valid": False, "reason": "no_session"}
    # Unlike github_app/supabase/dashboard_auth below (each best-effort --
    # omitted silently if the visitor never reached them), llm_provider is a
    # hard requirement: skipping it used to let a deploy through with no
    # LLM_PROVIDER/model ever seeded into slot_config, silently producing a
    # "live" bot that can't review anything and no error anywhere in the
    # flow (found 2026-09-17 by clicking Deploy before the LLM step
    # validated -- the wizard's own client-side gating unlocks "Finish &
    # Deploy" too early in the demo, see demo/session_store.py's
    # _PRESEEDED_FRAMES comment; this is the fail-closed backstop for that
    # and for any other way the client-side gate could be bypassed).
    if not session.frames.get("llm_provider"):
        return {"valid": False, "reason": "llm_provider_not_ready"}

    env_vars: dict[str, str] = {}

    # The deployed bot service needs its own RENDER_API_KEY to power the
    # dashboard's Environment tab (docs/superpowers/specs/
    # 2026-09-02-dashboard-environment-tab-design.md) -- previously this
    # credential never left the visitor's browser/onboarding session.
    # render_frame["api_key"] is guaranteed present by this function's own
    # guard clause above.
    env_vars["RENDER_API_KEY"] = render_frame["api_key"]

    github_app = session.frames.get("github_app")
    if github_app:
        env_vars["GITHUB_APP_ID"] = str(github_app["app_id"])
        env_vars["GITHUB_APP_PRIVATE_KEY"] = github_app["private_key_b64"]
        env_vars["GITHUB_WEBHOOK_SECRET"] = github_app["webhook_secret"]
        env_vars["GITHUB_APP_INSTALLATION_ID"] = str(github_app["installation_id"])

    supabase = session.frames.get("supabase")
    if supabase and "database_url" in supabase:
        env_vars["DATABASE_URL"] = supabase["database_url"]

    llm_provider = session.frames.get("llm_provider")
    if llm_provider:
        # Credential only -- no LLM_PROVIDER, no model_var push. Both provider
        # selection and model are DB-only over there now (runtime_config/
        # slot_config -- see pr-review-bot's docs/superpowers/specs/2026-09-09-
        # provider-key-index-db-only-design.md, generalizing the 2026-09-08
        # slotted-config-and-db-delegation work from model to provider too),
        # so neither has a Render env var at all -- _seed_provider_config
        # below seeds both directly into the visitor's own database instead.
        credential_var, _model_var = _LLM_ENV_VAR_NAMES[llm_provider["provider"]]
        env_vars[credential_var] = llm_provider["credential_value"]

    dashboard_auth = session.frames.get("dashboard_auth")
    if dashboard_auth:
        env_vars["DASHBOARD_USERNAME"] = dashboard_auth["username"]
        env_vars["DASHBOARD_PASSWORD"] = dashboard_auth["password"]
        env_vars["DASHBOARD_SESSION_SECRET"] = dashboard_auth["session_secret"]

    # Always included, not gated on any frame: these are tuning defaults,
    # not visitor-submitted credentials -- see _GENERIC_OPERATIONAL_ENV_DEFAULTS.
    env_vars.update(_GENERIC_OPERATIONAL_ENV_DEFAULTS)

    # Seed slot_config/runtime_config BEFORE the Render push (and refuse if
    # it fails) -- the visitor must never be able to reach a deployable
    # state where Render has the credential but the newly-provisioned
    # database has no matching model/provider row (see
    # docs/superpowers/specs/2026-09-08-slotted-config-and-db-delegation-
    # design.md section 5, generalized to provider selection itself by
    # pr-review-bot's docs/superpowers/specs/2026-09-09-provider-key-index-
    # db-only-design.md). This wizard is the only thing that can seed
    # provider/key-index/model for a freshly-provisioned instance, since it
    # runs before the deployed service ever boots for the first time --
    # every other runtime_config column (the tuning knobs included) is
    # backfilled by that project's own boot-time
    # review_queue/store.py::_backfill_runtime_config instead, per its
    # 2026-09-10 cross-repo-contract-direction work (see
    # _RUNTIME_CONFIG_SCHEMA's own comment above).
    if llm_provider:
        if not supabase or "database_url" not in supabase:
            # Unreachable in normal sequential flow (the Supabase frame
            # completes before llm-provider unlocks) -- guards the same
            # corrupted/hand-edited session-state case the other frames'
            # "no_session"-shaped refusals guard, rather than silently
            # pushing a credential with no matching slot_config row.
            return {"valid": False, "reason": "supabase_not_ready", "pushed": []}
        # Prove the model is actually callable before seeding it or pushing
        # its credential to Render -- list-models (at /api/llm/*/list-models
        # time) only proves the credential authenticates and the model is
        # LISTED, which for Vertex is the global Model Garden, not a
        # per-project entitlement list (pr-review-bot's docs/superpowers/
        # specs/2026-09-11-vertex-model-entitlement-validation-design.md
        # section 1 -- the incident this whole mechanism exists to prevent).
        # Runs before _seed_provider_config AND before the Render push
        # below, same ordering reason as pr-review-bot's own
        # _apply_llm_credential: a refused model must never leave a pushed
        # credential (or a seeded row) with nothing to match it. Groq needs
        # no probe (contracts/provisioning.json's model_validation block --
        # no free token-counting endpoint, and its own listing is
        # key-scoped, unlike Vertex's).
        provider = llm_provider["provider"]
        probe: llm_client.LlmModelProbed | llm_client.LlmApiFailed | None = None
        # Only ever populated in the vertex branch below and only ever
        # passed to _seed_provider_config for vertex -- a gemini/groq
        # submission's frame dict can still carry a STALE vertex pair left
        # over from an earlier vertex confirm (the merge write /api/llm/
        # confirm uses, not replace=True, per the parked ISSUES.md entry),
        # and unconditionally reading it here would write those stale
        # values onto the gemini/groq provider's own slot_config row.
        vertex_project: str | None = None
        vertex_location: str | None = None
        if provider == "vertex":
            # /api/llm/confirm has required and validated this pair for
            # every vertex submission since this feature shipped, but a
            # session confirmed against an older deploy (session_store.
            # SESSION_TTL is 4 hours, so this window is real, not
            # theoretical) can still reach here with the frame present but
            # missing/stale vertex_gcp_project/vertex_gcp_location. Falling
            # back to None/None here would silently probe (and then seed)
            # the key's home project at the SDK default region instead of
            # the pair the visitor actually confirmed -- exactly the defect
            # this whole feature exists to close. Refuse instead of
            # guessing; re-checking the allowlist membership here (not just
            # presence) also closes the one path identified as
            # session-sourced rather than freshly visitor-validated.
            vertex_project = llm_provider.get("vertex_gcp_project")
            vertex_location = llm_provider.get("vertex_gcp_location")
            if not vertex_project or not _valid_vertex_location(vertex_location):
                return {"valid": False, "reason": "vertex_pair_missing", "pushed": []}
            probe = await llm_client.probe_vertex_model(
                llm_provider["credential_value"],
                llm_provider["model"],
                project=vertex_project,
                location=vertex_location,
            )
        elif provider == "gemini":
            probe = await llm_client.probe_gemini_model(
                llm_provider["credential_value"], llm_provider["model"]
            )
        if isinstance(probe, llm_client.LlmApiFailed):
            return {"valid": False, "reason": probe.reason, "pushed": []}
        # vertex_gcp_project/vertex_gcp_location now carry the visitor's own
        # verified choice, written by /api/llm/confirm -- the LLM-provider
        # frame collects and probes this exact pair before it ever reaches
        # here. This probe is retained as the correctness gate anyway
        # (not merely for its side effect of re-confirming what confirm
        # already proved): a reload never re-runs /api/llm/confirm, and
        # session_store.SESSION_TTL is 4 hours, so a credential revoked or a
        # model retired between the two frames would otherwise reach
        # slot_config unchecked.
        seeded = await asyncio.to_thread(
            _seed_provider_config,
            supabase["database_url"],
            llm_provider["provider"],
            llm_provider["model"],
            vertex_project,
            vertex_location,
        )
        if not seeded:
            return {"valid": False, "reason": "slot_config_seed_failed", "pushed": []}

    result = await render_client.push_env_vars(
        render_frame["api_key"], render_frame["service_id"], env_vars
    )
    return _push_result(result)


@router.post("/api/render/trigger-deploy")
async def trigger_render_deploy(request: Request) -> dict:
    session_id = _get_session_id(request)
    render_frame = session_id and (await _read_frame(session_id, "render"))
    if not render_frame or "api_key" not in render_frame or "service_id" not in render_frame:
        return {"valid": False, "reason": "no_session"}
    result = await render_client.trigger_deploy(render_frame["api_key"], render_frame["service_id"])
    if isinstance(result, render_client.RenderDeployTriggered):
        # Deliberately does NOT report failure on a lost write here, unlike
        # every other endpoint in this file: the deploy has already been
        # triggered as a real, non-idempotent external side effect (unlike
        # every check `bulk_push_render_env_vars`/earlier steps make, which
        # are cheap to retry) -- reporting failure would invite the visitor
        # to retry and trigger a second deploy. `deploy_id` is still
        # returned either way; a lost write here only costs the ability to
        # resume polling after a reload, not correctness.
        await _update_frame(session_id, "render", {"pending_deploy_id": result.deploy_id})
        return {"valid": True, "deploy_id": result.deploy_id}
    return {"valid": False, "reason": result.reason}


@router.post("/api/render/deploy-status")
async def get_render_deploy_status(request: Request) -> dict:
    session_id = _get_session_id(request)
    render_frame = session_id and (await _read_frame(session_id, "render"))
    required = ("api_key", "service_id", "pending_deploy_id")
    # .get(k), not "k in render_frame": clear-deploy-state merges
    # pending_deploy_id=None rather than deleting the key, so a presence-
    # only check would let a None deploy id reach poll_deploy_status and
    # surface as a misleading "service not found".
    if not render_frame or not all(render_frame.get(k) for k in required):
        return {"valid": False, "reason": "no_session"}
    result = await render_client.poll_deploy_status(
        render_frame["api_key"], render_frame["service_id"], render_frame["pending_deploy_id"]
    )
    if isinstance(result, render_client.RenderDeployStatus):
        if result.status == "live":
            # Best-effort, like trigger-deploy's own write above -- this is
            # what lets a later GET /api/session (a revisit with no local
            # sessionStorage mirror left) still report the service as
            # already deployed instead of showing the Deploy button again.
            await _update_frame(session_id, "render", {"deployed": True})
        return {"valid": True, "status": result.status}
    return {"valid": False, "reason": result.reason}


@router.post("/api/render/clear-deploy-state")
async def clear_render_deploy_state(request: Request) -> dict:
    """Called when an earlier frame render-deploy depends on gets changed
    (static/index.html's lockFrame("render-deploy"), reached via
    relockDownstreamOf()) -- clears the persisted "deployed"/
    "pending_deploy_id" flags so a reload mid-redo doesn't resurrect the
    OLD deploy's done state from GET /api/session. service_id/service_url
    are left untouched -- they're still valid; only this frame's own
    resumable state is being invalidated here."""
    session_id = _get_session_id(request)
    render_frame = session_id and (await _read_frame(session_id, "render"))
    if not render_frame or "service_id" not in render_frame:
        return {"valid": False, "reason": "no_session"}
    write_result = await _update_frame(
        session_id, "render", {"deployed": False, "pending_deploy_id": None}
    )
    if isinstance(write_result, session_store.SessionNotFound):
        return {"valid": False, "reason": "no_session"}
    return {"valid": True}
