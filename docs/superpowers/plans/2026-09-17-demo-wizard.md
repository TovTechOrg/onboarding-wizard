# Demo Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A publicly reachable demo of the onboarding wizard that walks a reader through four real steps against mocked Render/GitHub/LLM integrations and an in-memory session store, then hands them off to the already-built bot demo.

**Architecture:** All demo-only code lives in a new `demo/` package. `demo/app.py` rebinds `session_store`, `render_client`, `github_client` and `llm_client` module attributes to in-memory mocks, then imports `main` and re-exports its `app` — reusing `main.app` rather than building a new `FastAPI()`, because the `RequestValidationError` handler that scrubs submitted credentials out of 422 responses is registered there. `router.py` imports all four as plain modules and resolves names at call time, so rebinding works without touching real code.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, uv, pytest (`asyncio_mode=auto`, `-n 4 --dist=loadgroup`, auto-applied `db`/`browser` markers), Playwright, Docker.

**Spec:** `~/pr-review-bot/docs/superpowers/specs/2026-09-16-live-mocked-demo-design.md` (absolute path — it lives in the sibling repo, which this repo cannot see)

**Sibling plan already shipped:** `~/pr-review-bot/docs/superpowers/plans/2026-09-16-demo-bot.md` (Plan 1, merged). This is Plan 2. Plan 3 (GitHub Pages launcher, weekly health check, README links) does not exist yet.

## Global Constraints

- **No real credentials in the demo.** `Settings` declares exactly two fields (`database_url`, `onboarding_session_encryption_key`) and there is **no `env_file`**, so both ship as `ENV` in `Dockerfile.demo`.
- **`ONBOARDING_SESSION_ENCRYPTION_KEY` must be a structurally valid Fernet key** — `main.py:38-51` constructs `Fernet(key.encode("ascii"))` and refuses to boot if it fails. A placeholder string will not do. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. It encrypts nothing real; the mock store drops encryption entirely.
- **Never edit `static/index.html`'s `fetch(...)` calls.** `tests/test_onboarding_page.py` asserts `body.count('fetch("<endpoint>"') == 1` per credential-carrying endpoint. Trim the flow by mocking the backend and hiding frames client-side, never by removing a fetch.
- **Step numbers are hardcoded in the i18n strings and the key names do not match their positions**: `frame1_title`="1. Render API key", `frame2_title`="4. GitHub App", `frame3_title`="5. Supabase database", `frame4_title`="6. LLM provider", `frame5_title`="7. Keep-warm pinger", `frame6_title`="8. Finish & Deploy", plus `frame_render_service_title` and `frame_dashboard_auth_title`. Never infer order from a key name.
- **All session access goes through `router.py`'s `_get_session`/`_read_frame`/`_update_frame`/`_create_session`/`_delete_session` wrappers**, never `session_store.*` directly — the wrappers run sync Postgres calls via `asyncio.to_thread`, and a direct call blocks the event loop for every concurrent request.
- **Reuse `main.app`.** A demo that builds its own `FastAPI()` silently loses `main.py`'s `RequestValidationError` handler, which is what stops FastAPI echoing a submitted credential back in a 422.
- **Hebrew strings never chain embedded LTR terms with an arrow** (CLAUDE.md:197). Such sequences must be real nested `<ol>/<li>`, and only then may the key join `HTML_I18N_KEYS`.
- **Never modify** `.claude/hooks/check_env_access.py` or `redact_output.py`.
- **Before any push:** `uv run pytest -v` and `uv run ruff check .` both green; `deploy-verify` before any push to `main`; `ui-visual-review` for any `static/index.html` markup/CSS change.
- Never hand-apply the `db` or `browser` marker — `tests/conftest.py:220` applies both automatically from the fixture closure.

---

### Task 1: Make the production image ship only what it should

`Dockerfile:20` is `COPY . .`, so `.dockerignore` is the *only* thing deciding what ships — unlike the sibling repo, which uses an explicit COPY list. Two consequences, one live and one blocking:

- **Live today:** `.env.example`, `.claude/settings.local.json` and all of `.impeccable/` currently ship into the production image. The `.env` pattern does not match `.env.example`.
- **Blocking this plan:** a new `demo/` directory would ship into the *production* image automatically. Excluding it in `.dockerignore` cannot work either, because `.dockerignore` applies to every build from that context, including `Dockerfile.demo` — which needs `demo/`.

Converting to an explicit COPY list fixes all three at once and matches the sibling repo's existing pattern.

> **This changes the production image.** It is the reason this task is first and standalone: it must be reviewed and `deploy-verify`'d on its own merits, not folded into demo work.

**Files:**
- Modify: `Dockerfile:20`, `.dockerignore`
- Test: `tests/test_dockerfile.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dockerfile.py`:

```python
def test_no_blanket_copy_of_the_build_context():
    """`COPY . .` makes .dockerignore the only gate on what ships, and would
    silently ship demo/ into production."""
    live = [
        line.strip() for line in DOCKERFILE.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "COPY . ." not in live


def test_demo_package_never_ships_in_the_production_image():
    assert "COPY demo" not in DOCKERFILE


def test_application_modules_are_copied_explicitly():
    for module in ("main.py", "router.py", "config.py", "session_store.py",
                   "render_client.py", "github_client.py", "llm_client.py",
                   "supabase_client.py", "uptimerobot_client.py"):
        assert module in DOCKERFILE, f"{module} must be COPY'd explicitly"


def test_contracts_json_still_ships():
    """router.py reads contracts/provisioning.json at import time."""
    assert "contracts/" in DOCKERFILE or "contracts" in DOCKERFILE
```

Append to `tests/test_dockerignore.py` if it exists, otherwise create it:

```python
from pathlib import Path

DOCKERIGNORE = (Path(__file__).parent.parent / ".dockerignore").read_text()


def _patterns() -> list[str]:
    return [
        line.strip() for line in DOCKERIGNORE.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_env_example_does_not_ship():
    """The `.env` pattern does not match `.env.example`."""
    assert ".env.example" in _patterns()


def test_local_agent_config_does_not_ship():
    patterns = _patterns()
    assert ".claude/" in patterns
    assert ".impeccable/" in patterns


def test_every_private_key_json_is_excluded():
    """No such file exists in this repo today, but `COPY . .` made the
    consequence severe, and the sibling repo had exactly this gap."""
    assert "*private-key*.json" in _patterns()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dockerfile.py tests/test_dockerignore.py -v`
Expected: FAIL — `COPY . .` is present and the new ignore patterns are absent.

- [ ] **Step 3: Replace the blanket COPY**

In `Dockerfile`, replace line 20 (`COPY . .`) with:

```dockerfile
# Explicit, not `COPY . .`: the blanket form made .dockerignore the only gate
# on what ships (it was shipping .env.example and local agent config), and
# would ship demo/ into production -- which .dockerignore cannot prevent,
# since it applies to Dockerfile.demo's build too.
COPY main.py router.py config.py session_store.py ./
COPY render_client.py github_client.py llm_client.py ./
COPY supabase_client.py uptimerobot_client.py ./
COPY static/ ./static/
COPY contracts/ ./contracts/
```

- [ ] **Step 4: Harden `.dockerignore`**

Add to `.dockerignore`:

```
# `.env` does not match `.env.example`.
.env.example

# Local agent/tooling config, never part of the service.
.claude/
.impeccable/

# Any service-account / private-key JSON, whatever it is named. None exists
# in this repo today; the narrow `gcp-service-account-key*.json` pattern
# alone missed a real file in the sibling repo.
*private-key*.json
*service-account*.json
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dockerfile.py tests/test_dockerignore.py -v`
Expected: PASS.

- [ ] **Step 6: Build and boot the real image**

```bash
docker build -t onboarding-wizard-check .
docker run --rm onboarding-wizard-check python -c "import main, router; print('imports ok')"
```

Expected: `imports ok`. This catches a module the explicit list forgot — the exact failure a green pytest run cannot see.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore tests/test_dockerfile.py tests/test_dockerignore.py
git commit -m "Ship the production image from an explicit file list"
```

---

### Task 2: Demo content and the three mock clients

Every real client function is async, returns a result union, and never raises — so each mock is a one-line return of the success variant.

**Files:**
- Create: `demo/__init__.py`, `demo/content.py`, `demo/render_client.py`, `demo/github_client.py`, `demo/llm_client.py`
- Test: `tests/test_demo_clients.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `demo.content` constants; mock modules mirroring each real client's public surface.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_demo_clients.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'demo'`.

- [ ] **Step 3: Create `demo/__init__.py`**

```python
"""Demo-only code. Never present in the production image."""
```

- [ ] **Step 4: Create `demo/content.py`**

```python
"""Fixed values the demo wizard reports. Nothing here authenticates anything."""

from __future__ import annotations

# Where "Finish & Deploy" sends the reader: the already-deployed bot demo.
# Overridden per-deployment by the DEMO_BOT_URL env var so the two demo
# services can be pointed at each other without a rebuild.
import os

DEMO_BOT_URL = os.environ.get(
    "DEMO_BOT_URL", "https://demo-pr-review-engine.onrender.com"
)

DEMO_SERVICE_ID = "srv-demo000000000000"
DEMO_OWNER_NAME = "Demo Workspace"
DEMO_DEPLOY_ID = "dep-demo000000000000"
DEMO_INSTALLATION_ID = 99000001
DEMO_ACCOUNT_LOGIN = "bot-demo"
DEMO_APP_ID = 900001
DEMO_VERTEX_PROJECT = "bot-demo-project"

# How many polls the deploy reports in_progress before going live. This is
# the provisioning animation: the real polling UI, driven by a mock that
# takes a few seconds, rather than a fake animation written from scratch.
DEPLOY_POLLS_BEFORE_LIVE = 3

GEMINI_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-pro"]
GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
VERTEX_MODELS = ["gemini-flash-latest", "gemini-2.5-flash"]
```

- [ ] **Step 5: Create `demo/render_client.py`**

```python
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
```

- [ ] **Step 6: Create `demo/github_client.py`**

```python
"""In-memory stand-in for github_client.py.

The App the reader "creates" does not exist -- there is no real App and no
testbed repo behind this demo. diff_required_permissions() is pure, so the
success result is built from the real requirement tables rather than
hand-faked, and stays correct if those tables change.
"""

from __future__ import annotations

from demo.content import DEMO_ACCOUNT_LOGIN, DEMO_INSTALLATION_ID
from github_client import (  # noqa: F401  (re-exported for isinstance checks)
    REQUIRED_EVENTS,
    REQUIRED_PERMISSIONS,
    AppValidated,
    InstallationFound,
    WebhookCheck,
    diff_required_permissions,
)


async def validate_app(
    app_id: int, private_key_b64: str, expected_webhook_url: str
) -> AppValidated:
    permissions, events = diff_required_permissions(
        dict(REQUIRED_PERMISSIONS), list(REQUIRED_EVENTS)
    )
    return AppValidated(
        permissions=permissions,
        events=events,
        installation=InstallationFound(
            installation_id=DEMO_INSTALLATION_ID,
            account_login=DEMO_ACCOUNT_LOGIN,
            repo_scope="selected",
        ),
        webhook=WebhookCheck(ok=True, actual_url=expected_webhook_url),
    )
```

- [ ] **Step 7: Create `demo/llm_client.py`**

```python
"""In-memory stand-in for llm_client.py. Makes no provider calls.

Listing and probing both always succeed: the demo's provider step exists to
let the reader make one real choice, not to exercise failure paths.
"""

from __future__ import annotations

from demo.content import DEMO_VERTEX_PROJECT, GEMINI_MODELS, GROQ_MODELS, VERTEX_MODELS
from llm_client import (  # noqa: F401  (re-exported for isinstance checks)
    LlmModelProbed,
    LlmModelsListed,
    VertexModelsListed,
    VertexProjectsListed,
)


async def list_gemini_models(api_key: str) -> LlmModelsListed:
    return LlmModelsListed(models=list(GEMINI_MODELS))


async def list_groq_models(api_key: str) -> LlmModelsListed:
    return LlmModelsListed(models=list(GROQ_MODELS))


async def list_vertex_models(
    service_account_key_b64: str, project: str | None = None, location: str | None = None
) -> VertexModelsListed:
    return VertexModelsListed(
        project_id=project or DEMO_VERTEX_PROJECT, models=list(VERTEX_MODELS)
    )


async def list_accessible_projects(*args, **kwargs) -> VertexProjectsListed:
    return VertexProjectsListed(projects=[DEMO_VERTEX_PROJECT])


async def probe_gemini_model(api_key: str, model: str) -> LlmModelProbed:
    return LlmModelProbed(model=model)


async def probe_vertex_model(
    service_account_key_b64: str, model: str,
    project: str | None = None, location: str | None = None,
) -> LlmModelProbed:
    return LlmModelProbed(model=model)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_demo_clients.py -v`
Expected: PASS, all cases.

- [ ] **Step 9: Commit**

```bash
git add demo/ tests/test_demo_clients.py
git commit -m "Add the demo wizard's content and its three mock clients"
```

---

### Task 3: In-memory session store

**Files:**
- Create: `demo/session_store.py`
- Test: `tests/test_demo_session_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a module exposing every public name `session_store` does, plus `reset()` and `sweep()`.

- [ ] **Step 1: Write the failing test**

```python
import inspect

import session_store as real_store
from demo import session_store as demo_store


def _public(module) -> set[str]:
    return {
        name for name, obj in vars(module).items()
        if not name.startswith("_") and inspect.isfunction(obj)
        and obj.__module__ == module.__name__
    }


def test_mock_covers_the_real_session_store_surface():
    missing = _public(real_store) - _public(demo_store)
    assert not missing, f"demo/session_store.py is missing: {sorted(missing)}"


def test_create_then_read_round_trips_a_frame():
    demo_store.reset()
    sid = demo_store.create_session()
    assert demo_store.update_frame(sid, "render", {"api_key": "x"}) is None
    assert demo_store.read_frame(sid, "render") == {"api_key": "x"}


def test_update_frame_shallow_merges_unless_replacing():
    demo_store.reset()
    sid = demo_store.create_session()
    demo_store.update_frame(sid, "render", {"api_key": "x"})
    demo_store.update_frame(sid, "render", {"service_id": "s"})
    assert demo_store.read_frame(sid, "render") == {"api_key": "x", "service_id": "s"}

    demo_store.update_frame(sid, "render", {"api_key": "y"}, replace=True)
    assert demo_store.read_frame(sid, "render") == {"api_key": "y"}


def test_unknown_session_fails_closed_and_never_upserts():
    demo_store.reset()
    result = demo_store.update_frame("no-such-session", "render", {"a": 1})
    assert result.__class__.__name__ == "SessionNotFound"
    assert demo_store.get_session("no-such-session") is None


def test_collapsed_frames_are_seeded_complete():
    """The trimmed flow keeps 4 frames; the other 4 must already be done, so
    the wizard's own frame machinery advances past them with no client hack."""
    demo_store.reset()
    sid = demo_store.create_session()
    session = demo_store.get_session(sid)
    for backend_key in ("dashboard_auth", "supabase", "uptimerobot"):
        assert backend_key in session.frames, f"{backend_key} should be pre-completed"


def test_sweep_evicts_only_expired_sessions():
    demo_store.reset()
    sid = demo_store.create_session()
    assert demo_store.sweep(age_seconds=0) == 1
    assert demo_store.get_session(sid) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_demo_session_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'session_store' from 'demo'`.

- [ ] **Step 3: Create `demo/session_store.py`**

```python
"""In-memory stand-in for session_store.py.

Sync, like the real one -- router.py's wrappers still run these through
asyncio.to_thread, so keeping them sync preserves that contract exactly.
No encryption: there is no real credential in this service's demo, so
Fernet would be ceremony over placeholder strings.

A public link must not grow memory without bound, so sessions are swept.
"""

from __future__ import annotations

import secrets
import time

from session_store import SESSION_TTL, SessionData, SessionNotFound  # noqa: F401

# Frames the trimmed demo collapses. Seeding them complete at session
# creation means GET /api/session reports them done, restoreFromSession()
# completes them through the real machinery, and the chain advances to the
# next visible frame with no client-side hack.
_PRESEEDED_FRAMES: dict[str, dict] = {
    "dashboard_auth": {"confirmed": True},
    "supabase": {"ref": "demo-ref", "connected": True},
    "uptimerobot": {"monitor_id": "demo-monitor"},
}

_sessions: dict[str, dict[str, dict]] = {}
_created_at: dict[str, float] = {}


def reset() -> None:
    _sessions.clear()
    _created_at.clear()


def init_pool() -> None:
    return None


def close_pool() -> None:
    return None


def create_session() -> str:
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {k: dict(v) for k, v in _PRESEEDED_FRAMES.items()}
    _created_at[session_id] = time.monotonic()
    return session_id


def get_session(session_id: str) -> SessionData | None:
    frames = _sessions.get(session_id)
    if frames is None:
        return None
    return SessionData(frames={k: dict(v) for k, v in frames.items()})


def update_frame(
    session_id: str, frame: str, data: dict, *, replace: bool = False
) -> SessionNotFound | None:
    """Fails closed exactly like the real one: never upserts an unknown id."""
    frames = _sessions.get(session_id)
    if frames is None:
        return SessionNotFound()
    if replace:
        frames[frame] = dict(data)
    else:
        frames.setdefault(frame, {}).update(data)
    return None


def read_frame(session_id: str, frame: str) -> dict | None:
    frames = _sessions.get(session_id)
    if frames is None:
        return None
    stored = frames.get(frame)
    return dict(stored) if stored is not None else None


def delete_session(session_id: str) -> None:
    _sessions.pop(session_id, None)
    _created_at.pop(session_id, None)


def sweep(age_seconds: float | None = None) -> int:
    """Demo-only: drop sessions older than the TTL (or an explicit age)."""
    limit = SESSION_TTL.total_seconds() if age_seconds is None else age_seconds
    now = time.monotonic()
    stale = [sid for sid, born in _created_at.items() if now - born >= limit]
    for sid in stale:
        delete_session(sid)
    return len(stale)
```

- [ ] **Step 4: Run the tests iteratively until green**

Run: `uv run pytest tests/test_demo_session_store.py -v`
Expected: the parity test names any missing function; add it and re-run until PASS.

> If `test_collapsed_frames_are_seeded_complete` fails on a key name, read
> `router.py`'s `GET /api/session` handler for the **backend** frame keys —
> they are deliberately not 1:1 with the UI frame ids, and `render` alone
> backs three UI frames.

- [ ] **Step 5: Commit**

```bash
git add demo/session_store.py tests/test_demo_session_store.py
git commit -m "Add the demo wizard's in-memory session store"
```

---

### Task 4: Demo app assembly

**Files:**
- Create: `demo/app.py`
- Test: `tests/test_demo_app_boot.py`

**Interfaces:**
- Consumes: every module from Tasks 2-3.
- Produces: `demo.app.app` (the FastAPI instance from `main`), `demo.app.install_mocks() -> None`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo/demo")
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_install_mocks_rebinds_every_client(demo_env):
    from demo import app as demo_app

    demo_app.install_mocks()

    import github_client
    import llm_client
    import render_client
    import session_store

    assert render_client.validate_key.__module__ == "demo.render_client"
    assert github_client.validate_app.__module__ == "demo.github_client"
    assert llm_client.list_groq_models.__module__ == "demo.llm_client"
    assert session_store.create_session.__module__ == "demo.session_store"


def test_demo_reuses_mains_app_so_422_scrubbing_survives(demo_env):
    """A demo with its own FastAPI() loses main.py's RequestValidationError
    handler, which is what stops a submitted credential being echoed in a 422."""
    from fastapi.exceptions import RequestValidationError

    import main
    from demo.app import app

    assert app is main.app
    assert RequestValidationError in app.exception_handlers


async def test_app_boots_with_no_database(demo_env):
    from httpx import ASGITransport, AsyncClient

    from demo.app import app

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
    assert response.status_code == 200
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_demo_app_boot.py -v`
Expected: FAIL with `ImportError: cannot import name 'app' from 'demo'`.

- [ ] **Step 3: Create `demo/app.py`**

```python
"""Demo entrypoint: `uvicorn demo.app:app`.

Rebinds every external client to an in-memory mock BEFORE importing main,
then re-exports main's own app object unchanged.

Reusing main.app is load-bearing, not convenience: main.py registers the
RequestValidationError handler that returns a flat {"detail": "invalid
request"}, which is what stops FastAPI echoing a submitted credential back
in a 422. A demo that built its own FastAPI() would silently lose it.

Rebinding works because router.py imports these as plain modules and
resolves `<module>.<name>` at call time -- see its own comment at :61-65.
"""

from __future__ import annotations

import github_client as real_github_client
import llm_client as real_llm_client
import render_client as real_render_client
import session_store as real_session_store

from demo import github_client as demo_github_client
from demo import llm_client as demo_llm_client
from demo import render_client as demo_render_client
from demo import session_store as demo_session_store

_PAIRS = (
    (real_session_store, demo_session_store),
    (real_render_client, demo_render_client),
    (real_github_client, demo_github_client),
    (real_llm_client, demo_llm_client),
)


def install_mocks() -> None:
    for real_module, demo_module in _PAIRS:
        for name in vars(demo_module):
            if name.startswith("_"):
                continue
            if callable(getattr(demo_module, name)) and hasattr(real_module, name):
                setattr(real_module, name, getattr(demo_module, name))


install_mocks()

from main import app  # noqa: E402  (must follow install_mocks)

__all__ = ["app", "install_mocks"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_demo_app_boot.py -v`
Expected: PASS, all three.

- [ ] **Step 5: Run the full suite to confirm nothing regressed**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all green. The demo package must not perturb the existing 631 tests.

- [ ] **Step 6: Commit**

```bash
git add demo/app.py tests/test_demo_app_boot.py
git commit -m "Assemble the demo wizard app, reusing main's app object"
```

---

### Task 5: Demo chrome — banner, trimmed frames, renumbering

**Files:**
- Create: `demo/static/demo.js`
- Modify: `demo/app.py` (serve the demo asset and inject its tag)
- Test: `tests/test_demo_chrome.py`

**Interfaces:**
- Consumes: `demo.app.app`.
- Produces: `GET /static/demo.js`, and a `<script src="/static/demo.js">` tag injected into the served page.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo/demo")
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def test_demo_script_is_served_and_injected(demo_env):
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")
        asset = await client.get("/static/demo.js")

    assert asset.status_code == 200
    assert '<script src="/static/demo.js"' in page.text


async def test_the_real_pages_fetch_calls_are_untouched(demo_env):
    """tests/test_onboarding_page.py pins one fetch per credential endpoint.
    The demo trims the flow by mocking the backend, never by editing fetches."""
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")

    for endpoint in ("/api/render/validate-key", "/api/github/validate-app",
                     "/api/render/trigger-deploy"):
        assert page.text.count(f'fetch("{endpoint}"') == 1


def test_demo_js_hides_exactly_the_collapsed_frames():
    from pathlib import Path

    source = (Path(__file__).parent.parent / "demo" / "static" / "demo.js").read_text()
    for frame_id in ("render-service", "dashboard-auth", "supabase", "uptime-pinger"):
        assert frame_id in source
    for frame_id in ("render-key", "github-app", "llm-provider", "render-deploy"):
        assert f'"{frame_id}"' not in source.split("COLLAPSED_FRAMES")[1].split("]")[0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_demo_chrome.py -v`
Expected: FAIL — `/static/demo.js` 404s.

- [ ] **Step 3: Create `demo/static/demo.js`**

```javascript
// Demo-only chrome. Hides the frames the trimmed flow collapses, renumbers
// the four that remain, and adds the banner.
//
// Step numbers live in the i18n strings, and the key names do NOT match
// their positions (frame2_title is "4. GitHub App"), so the visible titles
// are renumbered by DOM position here rather than by editing those strings.
(function () {
  var COLLAPSED_FRAMES = [
    "render-service", "dashboard-auth", "supabase", "uptime-pinger"
  ];
  var KEPT_FRAMES = ["render-key", "github-app", "llm-provider", "render-deploy"];

  var BANNER = {
    en: "Demo — mock data. No real Render, GitHub or LLM calls.",
    he: "דמו — נתונים מדומיים."
  };

  function lang() {
    return document.documentElement.lang === "he" ? "he" : "en";
  }

  function addBanner() {
    var el = document.createElement("div");
    el.id = "demoBanner";
    el.setAttribute("data-demo-banner", "");
    el.textContent = BANNER[lang()];
    el.style.cssText =
      "padding:.5rem 1rem;text-align:center;background:#f5c518;color:#1a1a2e;" +
      "font-weight:600;position:sticky;top:0;z-index:50";
    document.body.prepend(el);
  }

  function hideCollapsedFrames() {
    COLLAPSED_FRAMES.forEach(function (id) {
      var el = document.getElementById("frame-" + id);
      if (el) el.hidden = true;
    });
  }

  function renumberKeptFrames() {
    KEPT_FRAMES.forEach(function (id, index) {
      var el = document.getElementById("frame-" + id);
      if (!el) return;
      var title = el.querySelector(".frame-title");
      if (!title) return;
      // Replace a leading "<digits>. " with this frame's visible position.
      title.textContent = title.textContent.replace(
        /^\s*\d+\.\s*/, String(index + 1) + ". "
      );
    });
  }

  function apply() {
    hideCollapsedFrames();
    renumberKeptFrames();
  }

  document.addEventListener("DOMContentLoaded", function () {
    addBanner();
    apply();
    // applyLanguage() rewrites every [data-i18n] node, restoring the
    // hardcoded numbers, so renumber again after a language switch.
    document.addEventListener("click", function (event) {
      if (event.target.closest("[data-lang-option], #langToggleBtn")) {
        setTimeout(apply, 0);
      }
    });
  });
})();
```

- [ ] **Step 4: Serve and inject it from `demo/app.py`**

Append to `demo/app.py`, after `from main import app`:

```python
from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse, HTMLResponse  # noqa: E402

_DEMO_STATIC = Path(__file__).parent / "static"
_SCRIPT_TAG = '<script src="/static/demo.js" defer></script>'


@app.get("/static/demo.js", include_in_schema=False)
async def _demo_script() -> FileResponse:
    return FileResponse(_DEMO_STATIC / "demo.js", media_type="application/javascript")


@app.middleware("http")
async def _inject_demo_script(request, call_next):
    """Inject the demo asset into the served page without editing index.html.

    index.html is left byte-identical on purpose: tests/test_onboarding_page.py
    pins one fetch(...) per credential-carrying endpoint, and the demo trims
    the flow by mocking the backend rather than by editing the page.
    """
    response = await call_next(request)
    if request.url.path != "/" or response.status_code != 200:
        return response

    body = b"".join([chunk async for chunk in response.body_iterator])
    html = body.decode("utf-8").replace("</body>", f"{_SCRIPT_TAG}</body>", 1)
    return HTMLResponse(content=html, status_code=200, headers={
        k: v for k, v in response.headers.items() if k.lower() != "content-length"
    })
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_demo_chrome.py -v`
Expected: PASS, all three.

- [ ] **Step 6: Commit**

```bash
git add demo/static/demo.js demo/app.py tests/test_demo_chrome.py
git commit -m "Add the demo wizard's banner, trimmed frames and renumbering"
```

---

### Task 6: The GitHub App shortcut and the handoff to the bot demo

The App step cannot be faked in place — App creation genuinely happens on github.com and there is no real App or testbed repo behind this demo. The real instructional UI stays (it is the honest answer to "how much work is this?"), with an added control that fills demo values and validates instantly.

**Files:**
- Modify: `demo/static/demo.js`
- Test: `tests/test_demo_shortcut.py`

**Interfaces:**
- Consumes: nothing from Python — this task is entirely client-side. The demo App id and the synthetic `.pem` body are declared in `demo.js` itself, since only the browser needs them.
- Produces: a `#demoUseCredentials` button in the `github-app` frame; a provider-carrying href on `#render-deploy-service-link`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_demo_shortcut.py -v`
Expected: FAIL — none of these identifiers exist in `demo.js` yet.

- [ ] **Step 3: Add the shortcut and the handoff to `demo/static/demo.js`**

Add inside the IIFE, and call `addShortcut()` and `wireServiceLink()` from the `DOMContentLoaded` handler:

```javascript
  var DEMO_APP_ID = "900001";
  // Body of the synthetic .pem the file input receives. Not a key of any
  // kind -- demo/github_client.py never reads it.
  var DEMO_PEM_BODY =
    "-----BEGIN RSA PRIVATE KEY-----\ndemo-not-a-real-key\n-----END RSA PRIVATE KEY-----\n";

  function addShortcut() {
    var frame = document.getElementById("frame-github-app");
    if (!frame) return;
    var body = frame.querySelector(".frame-body");
    if (!body) return;

    var button = document.createElement("button");
    button.id = "demoUseCredentials";
    button.type = "button";
    button.textContent =
      lang() === "he"
        ? "השתמש בפרטי דמו"
        : "Use demo credentials";
    button.style.cssText = "margin-bottom:.75rem;font-weight:600";

    // Fill the real fields and let the page's own submit path run. The
    // /api/github/validate-app call still happens; demo/github_client.py is
    // what makes it succeed, so the frame machinery advances normally.
    //
    // The private key field is a FILE input (accept=".pem"), not a text box:
    // assigning .value to it is forbidden by every browser. A DataTransfer
    // is the supported way to hand it a synthetic file, and the change event
    // must be dispatched explicitly because assigning .files fires none.
    button.addEventListener("click", function () {
      var appId = document.getElementById("github-app-id-input");
      var keyFile = document.getElementById("github-app-key-file-input");
      if (appId) appId.value = DEMO_APP_ID;
      if (keyFile) {
        var transfer = new DataTransfer();
        transfer.items.add(
          new File([DEMO_PEM_BODY], "demo-app.pem", {
            type: "application/x-pem-file"
          })
        );
        keyFile.files = transfer.files;
        keyFile.dispatchEvent(new Event("change", { bubbles: true }));
      }
      // Every button on this page is type="button"; there is no submit.
      var submit = document.getElementById("github-app-validate-submit");
      if (submit) submit.click();
    });

    body.prepend(button);
  }

  function chosenProvider() {
    var checked = document.querySelector(
      'input[name="llm-provider-choice"]:checked'
    );
    return checked ? checked.value : null;
  }

  function wireServiceLink() {
    // finishRenderDeploy() sets this href to the created service's URL, which
    // demo/render_client.py returns as the bot demo. Append the reader's
    // provider choice so the review they land on reports it.
    var observer = new MutationObserver(function () {
      var link = document.getElementById("render-deploy-service-link");
      if (!link || !link.href || link.dataset.demoWired) return;
      var provider = chosenProvider();
      if (provider) {
        link.href =
          link.href + (link.href.indexOf("?") === -1 ? "?" : "&") +
          "provider=" + encodeURIComponent(provider);
      }
      link.dataset.demoWired = "1";
    });
    observer.observe(document.body, { attributes: true, childList: true, subtree: true });
  }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_demo_shortcut.py -v`
Expected: PASS, all three.

> **Selectors above were verified against `static/index.html` on 2026-09-17**
> — `#github-app-id-input`, `#github-app-key-file-input`,
> `#github-app-validate-submit`, and `input[name="llm-provider-choice"]`. Two
> traps they encode: the private-key field is a **file input**, so `.value`
> assignment is impossible and a `DataTransfer` plus an explicit `change`
> event is required; and **no button on this page is `type="submit"`** —
> every one is `type="button"` with an id, so a `button[type="submit"]`
> selector silently matches nothing. Task 9's browser test proves the click
> path end to end.

- [ ] **Step 5: Commit**

```bash
git add demo/static/demo.js tests/test_demo_shortcut.py
git commit -m "Add the demo App-credentials shortcut and the bot-demo handoff"
```

---

### Task 7: Start-over control, cookie-hostile fallback, and step logging

Three spec requirements that have no home in Tasks 2-6.

> ⚠️ **The cookie-hostile fallback is the single most dangerous change in this
> plan.** Plan 1 shipped the equivalent for the bot, its own fix introduced an
> **infinite redirect loop** for exactly the cookie-blocked browsers it was
> meant to serve, and it took two fix waves plus a real-Chromium
> investigation to resolve — the root cause was deeper than it looked. Do not
> mark this task done on a passing unit test. Task 9's browser test must
> cover the cookie-blocked path explicitly.

**Files:**
- Create: `demo/routes.py`
- Modify: `demo/app.py`, `demo/static/demo.js`
- Test: `tests/test_demo_routes.py`

**Interfaces:**
- Consumes: `demo.session_store`.
- Produces: `demo.routes.router`; `POST /api/demo/step/{name}`; a `#demoStartOver` control.

- [ ] **Step 1: Write the failing test**

```python
import logging

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo/demo")
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def test_step_endpoint_logs_a_structured_line(demo_env, caplog):
    from demo.app import app

    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="demo.routes"):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/demo/step/provider_done")

    assert response.json() == {"recorded": "provider_done"}
    assert "demo_step step=provider_done" in caplog.text


async def test_unknown_step_names_are_not_echoed(demo_env, caplog):
    from demo.app import app

    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="demo.routes"):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/demo/step/%3Cinjected%3E")

    assert "injected" not in caplog.text
    assert "demo_step step=unknown" in caplog.text


async def test_a_cookieless_visitor_still_gets_a_working_session(demo_env):
    """No cookie jar at all -- the cookie-hostile case (LinkedIn's in-app
    browser, private modes). The reader must still see a usable wizard, and
    crucially must NOT be bounced between responses."""
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           cookies=None, follow_redirects=False) as client:
        first = await client.get("/api/session")
        second = await client.get("/api/session")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json(), "a cookieless visitor must be stable"


async def test_start_over_resets_the_session(demo_env):
    from demo.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/session")
        reset = await client.post("/api/session/reset")

    assert reset.status_code in (200, 204)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_demo_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'demo.routes'`.

- [ ] **Step 3: Create `demo/routes.py`**

```python
"""Demo-only routes layered over the real wizard."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_STEPS = {
    "wizard_start", "render_key_done", "github_app_done",
    "provider_done", "deploy_done", "handoff_clicked",
}


@router.post("/api/demo/step/{name}")
async def record_step(name: str) -> JSONResponse:
    """Analytics: one structured line per step reached, read from the host's
    logs during the launch window. No database, no third-party script, no
    stored identifier. Unknown names are collapsed rather than echoed, so a
    crafted path cannot write arbitrary text into the log.
    """
    step = name if name in _ALLOWED_STEPS else "unknown"
    logger.info("demo_step step=%s", step)
    return JSONResponse({"recorded": step})
```

- [ ] **Step 4: Add the cookie-hostile fallback to `demo/app.py`**

```python
from demo.routes import router as demo_router  # noqa: E402

app.include_router(demo_router)

_SHARED_DEMO_SESSION = "demo-stateless-shared-session"


@app.middleware("http")
async def _cookieless_visitors_share_one_session(request, call_next):
    """Pin one shared session when the browser stores no cookie.

    Every bit of state here is synthetic and identical between visitors, so
    sharing is harmless -- the same argument the bot demo's stateless view
    rests on. What is NOT harmless is bouncing: this must never issue a
    redirect, because the equivalent bot-side fix did, and produced an
    infinite loop for precisely the browsers it was written to serve.

    Only the request scope is touched, so a cookie-capable visitor keeps
    their own session untouched.
    """
    from router import SESSION_COOKIE_NAME  # the wizard's own cookie name

    if SESSION_COOKIE_NAME not in request.cookies:
        headers = list(request.scope.get("headers", []))
        headers.append(
            (b"cookie", f"{SESSION_COOKIE_NAME}={_SHARED_DEMO_SESSION}".encode())
        )
        request.scope["headers"] = headers
        # The shared id must exist in the store, or every wrapper returns
        # SessionNotFound and the reader sees a dead wizard.
        from demo import session_store as demo_session_store

        if demo_session_store.get_session(_SHARED_DEMO_SESSION) is None:
            demo_session_store.adopt_session(_SHARED_DEMO_SESSION)

    return await call_next(request)
```

Add to `demo/session_store.py`:

```python
def adopt_session(session_id: str) -> None:
    """Demo-only: create a session under a caller-chosen id.

    create_session() mints its own id and is the only such path in the real
    store; the cookie-hostile fallback needs a *known* id, so this is a
    separate, clearly-named function rather than a widened create_session().
    """
    _sessions[session_id] = {k: dict(v) for k, v in _PRESEEDED_FRAMES.items()}
    _created_at[session_id] = time.monotonic()
```

> The import is correct as written: `router.py:43` defines
> `SESSION_COOKIE_NAME = "onboarding_session"` (verified 2026-09-17). Import
> it rather than hardcoding the string — if the name ever changes and this
> hardcodes it, the fallback silently does nothing and only a cookie-blocked
> visitor ever finds out.

- [ ] **Step 5: Add the start-over control to `demo/static/demo.js`**

Add inside the IIFE and call `addStartOver()` from `DOMContentLoaded`:

```javascript
  function addStartOver() {
    var link = document.createElement("button");
    link.id = "demoStartOver";
    link.type = "button";
    link.textContent = lang() === "he"
      ? "התחל מחדש"
      : "Start over";
    link.style.cssText =
      "position:fixed;bottom:1rem;inset-inline-end:1rem;z-index:60;font-size:.85rem";
    link.addEventListener("click", function () {
      fetch("/api/session/reset", { method: "POST" }).then(function () {
        location.href = location.pathname;
      });
    });
    document.body.appendChild(link);
  }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_demo_routes.py -v`
Expected: PASS, all four.

- [ ] **Step 7: Commit**

```bash
git add demo/routes.py demo/app.py demo/session_store.py demo/static/demo.js tests/test_demo_routes.py
git commit -m "Add demo step logging, start-over, and a cookieless fallback"
```

---

### Task 8: `Dockerfile.demo` and its guard test

**Files:**
- Create: `Dockerfile.demo`
- Test: `tests/test_dockerfile_demo.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEMO = (ROOT / "Dockerfile.demo").read_text()


def _live_lines(text: str) -> list[str]:
    return [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_demo_image_runs_the_demo_entrypoint():
    assert "demo.app:app" in DEMO
    assert "main:app" not in DEMO


def test_demo_image_copies_the_demo_package():
    assert "COPY demo" in DEMO


def test_demo_image_bakes_a_structurally_valid_fernet_key():
    """main.py constructs Fernet(key) at boot; a placeholder string fails."""
    import re

    from cryptography.fernet import Fernet

    match = re.search(r'ONBOARDING_SESSION_ENCRYPTION_KEY="([^"]+)"', DEMO)
    assert match, "the demo image must bake the key, not take it from Render"
    Fernet(match.group(1).encode("ascii"))  # raises if malformed


def test_demo_image_declares_a_database_url_it_never_uses():
    assert "DATABASE_URL" in DEMO


def test_no_recursive_chown():
    assert not any("chown -R" in line for line in _live_lines(DEMO))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_dockerfile_demo.py -v`
Expected: FAIL with `FileNotFoundError: Dockerfile.demo`.

- [ ] **Step 3: Create `Dockerfile.demo`**

Generate a real Fernet key first and paste it in:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```dockerfile
# Demo image. Mirrors Dockerfile, plus demo/, booting demo.app:app and
# baking its own fake settings so a demo deployment has no env-var
# configuration to get wrong. None of these values authenticate anything.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# No chown -R: an overlay filesystem copies every chowned file into a new
# layer. Nothing under /app is written to at runtime.
RUN useradd -m -u 1000 appuser

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py router.py config.py session_store.py ./
COPY render_client.py github_client.py llm_client.py ./
COPY supabase_client.py uptimerobot_client.py ./
COPY static/ ./static/
COPY contracts/ ./contracts/
COPY demo/ ./demo/

# Fake-but-present. DATABASE_URL is never dialled -- demo/session_store.py
# replaces the pool -- but main.py refuses to boot on an empty one. The
# encryption key must be a STRUCTURALLY VALID Fernet key: main.py constructs
# Fernet(key) at boot. It encrypts nothing; the mock store holds no secrets.
ENV DATABASE_URL="postgresql://demo:demo@127.0.0.1:5432/demo" \
    ONBOARDING_SESSION_ENCRYPTION_KEY="REPLACE_WITH_GENERATED_FERNET_KEY" \
    DEMO_BOT_URL="https://demo-pr-review-engine.onrender.com"

USER appuser
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "--no-dev", "uvicorn", "demo.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dockerfile_demo.py -v`
Expected: PASS — including the Fernet check, which fails while the placeholder is still in place. Replace it with the generated key.

- [ ] **Step 5: Build and boot the image for real**

```bash
docker build -f Dockerfile.demo -t demo-onboarding-wizard .
docker run --rm -d -p 8001:8000 --name demo-wizard demo-onboarding-wizard
curl -fsS http://localhost:8001/healthz && curl -fsS http://localhost:8001/ | head -5
docker rm -f demo-wizard
```

Expected: boots with no database reachable, `/healthz` 200, `/` serves the page.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile.demo tests/test_dockerfile_demo.py
git commit -m "Add the demo wizard image, booting demo.app with baked settings"
```

---

### Task 9: End-to-end browser test

This repo already has the harness: `tests/conftest.py`'s `live_app_url` fixture runs the real app in a thread on a free port, and `browser`/`page` fixtures drive Playwright. The `browser` marker is auto-applied from the fixture closure.

This is the task that catches what per-task diffs cannot — Plan 1's whole-branch review found three integration bugs (a mock never installed, leaving a real network call in the deployed demo; state never wired; a bypass that fired unconditionally) that every individual task review had passed.

**Files:**
- Create: `tests/test_demo_end_to_end.py`

- [ ] **Step 1: Write the end-to-end test**

```python
"""The demo's real failure mode is the flow breaking, not a unit regressing."""

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def demo_app_url(monkeypatch, live_app_url):
    """The same live-server harness the existing browser tests use, but
    serving demo.app instead of main.app."""
    monkeypatch.setenv("ONBOARDING_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return live_app_url


def test_only_four_frames_are_visible(page, demo_app_url):
    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")

    for frame_id in ("render-service", "dashboard-auth", "supabase", "uptime-pinger"):
        assert page.locator(f"#frame-{frame_id}").is_hidden()
    for frame_id in ("render-key", "github-app", "llm-provider", "render-deploy"):
        assert page.locator(f"#frame-{frame_id}").count() == 1


def test_visible_frames_are_numbered_one_to_four(page, demo_app_url):
    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")

    numbers = []
    for frame_id in ("render-key", "github-app", "llm-provider", "render-deploy"):
        title = page.locator(f"#frame-{frame_id} .frame-title").inner_text()
        numbers.append(title.strip().split(".")[0])
    assert numbers == ["1", "2", "3", "4"], f"got {numbers}"


def test_no_request_ever_leaves_for_a_real_third_party(page, demo_app_url):
    """The bug Plan 1 shipped and its final review caught: a mock that was
    never installed, leaving a live call to a real API from the demo."""
    external = []
    page.on("request", lambda r: external.append(r.url)
            if any(host in r.url for host in
                   ("api.render.com", "api.github.com", "googleapis.com",
                    "api.groq.com", "supabase.com", "uptimerobot.com"))
            else None)

    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")
    page.fill("#render-key-input", "demo-key")
    page.click("#render-key-submit")
    page.wait_for_timeout(1500)

    assert external == [], f"demo reached real third parties: {external}"


def test_a_cookie_blocked_browser_is_not_bounced(browser, demo_app_url):
    """The exact regression Plan 1 shipped and needed two fix waves to clear:
    its cookie-hostile fix produced an infinite redirect loop for the very
    browsers it was written for. A unit test did not catch it; this does."""
    context = browser.new_context()
    context.add_init_script(
        "Object.defineProperty(document, 'cookie', "
        "{get: () => '', set: () => {}, configurable: true});"
    )
    blocked = context.new_page()

    redirects = []
    blocked.on("response", lambda r: redirects.append(r.url)
               if 300 <= r.status < 400 else None)

    blocked.goto(demo_app_url, wait_until="networkidle")
    blocked.wait_for_selector("#demoBanner", timeout=10_000)

    assert blocked.locator("#frame-render-key").is_visible()
    assert len(redirects) < 3, f"redirect loop for a cookie-blocked visitor: {redirects}"
    context.close()


def test_the_github_shortcut_advances_the_frame(page, demo_app_url):
    """Proves the DataTransfer file-input path actually works in a browser --
    assigning .value to a file input is impossible, so this is the one way to
    know the shortcut is real."""
    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")
    page.fill("#render-key-input", "demo-key")
    page.click("#render-key-submit")
    page.wait_for_selector("#frame-github-app:not([data-locked='true'])", timeout=10_000)

    page.click("#demoUseCredentials")
    page.wait_for_selector("#frame-github-app[data-status='done']", timeout=10_000)


def test_banner_survives_a_language_switch(page, demo_app_url):
    page.goto(demo_app_url)
    page.wait_for_selector("#demoBanner")
    page.click("#langToggleBtn")
    page.click('[data-lang-option="he"], input[name="lang"][value="he"]')
    page.wait_for_timeout(300)

    assert page.locator("#demoBanner").is_visible()
    title = page.locator("#frame-render-key .frame-title").inner_text()
    assert title.strip().startswith("1."), "renumbering must survive applyLanguage()"
```

- [ ] **Step 2: Run it and fix what it finds**

Run: `uv run pytest tests/test_demo_end_to_end.py -v`
Expected: initially FAIL if any wiring from Tasks 2-6 is incomplete, and the selectors flagged in Task 6 may need correcting against the real markup. Fix until PASS.

> If `live_app_url` cannot be pointed at `demo.app`, read its definition in
> `tests/conftest.py:123` and add a sibling fixture that starts
> `demo.app:app` the same way rather than reshaping the existing one — the
> existing browser tests depend on it unchanged.

- [ ] **Step 3: Commit**

```bash
git add tests/test_demo_end_to_end.py
git commit -m "Drive the demo wizard end to end in a real browser"
```

---

### Task 10: Mobile pass

**Files:**
- Modify: `demo/static/demo.js` (CSS only, via the injected banner's styles)
- Possibly modify: `static/index.html` (CSS only)

- [ ] **Step 1: Run the demo locally**

```bash
DATABASE_URL="postgresql://demo:demo@127.0.0.1:5432/demo" \
ONBOARDING_SESSION_ENCRYPTION_KEY="$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
uv run uvicorn demo.app:app --port 8001
```

- [ ] **Step 2: Invoke the `ui-visual-review` skill**

Capture the four visible frames at light-desktop, dark-desktop, and mobile viewports. This repo's CLAUDE.md requires it for any `static/index.html` markup/CSS/layout change, and the banner is a layout change regardless of which file it lives in.

- [ ] **Step 3: Fix what the screenshots show**

Expected problems: the sticky banner overlapping the language/theme toggles at narrow width, and the renumbered frame titles wrapping. Fix with CSS only. If a fix belongs in `static/index.html`, keep it to CSS — never touch a `fetch(...)` call.

- [ ] **Step 4: Re-run `ui-visual-review`; confirm no horizontal page scroll at mobile width**

- [ ] **Step 5: Commit**

```bash
git add demo/static/demo.js static/index.html
git commit -m "Make the demo wizard legible at mobile width"
```

---

## Out of scope for this plan

Plan 3 covers the GitHub Pages launcher, the weekly Playwright health check, the README/docs links, and the closing call to action. Creating the `demo-onboarding-wizard` and `demo-pr-review-engine` Render services is a deployment step, not a code change, and is gated on the unresolved Render free-instance-hour verification recorded in the spec.
