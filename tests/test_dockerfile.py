"""Regression coverage for Dockerfile conventions that aren't obvious from
reading the file alone -- see CLAUDE.md's "Docker image: no chown -R"
section for the full rationale."""

from __future__ import annotations

from pathlib import Path

DOCKERFILE = (Path(__file__).parent.parent / "Dockerfile").read_text()
DOCKERIGNORE = (Path(__file__).parent.parent / ".dockerignore").read_text()


def test_dockerfile_never_chowns_app_directory():
    """`chown -R appuser:appuser /app` was measured to add +130MB/+25% image
    size for zero functional benefit -- overlayfs stores a changed file as
    a full copy, not a diff, so chowning everything just-copied duplicates
    the whole venv + app code into a new layer. Nothing under /app is
    written to at runtime, so appuser only ever needs the read+execute
    permissions COPY/RUN already leave in place by default. If a future
    change genuinely needs appuser to own or write to something under
    /app, chown only that specific path (or use `COPY --chown=`), not a
    blanket `-R /app`."""
    for line in DOCKERFILE.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  # explaining the tradeoff in prose is fine -- no actual chown command
        assert "chown" not in stripped, f"found a live chown command: {stripped!r}"


def test_dockerfile_still_drops_root_before_cmd():
    """The no-chown fix must not silently regress into running the
    container as root -- appuser still needs to exist and be switched to
    before CMD."""
    assert "useradd -m -u 1000 appuser" in DOCKERFILE
    assert "USER appuser" in DOCKERFILE
    assert DOCKERFILE.index("useradd -m -u 1000 appuser") < DOCKERFILE.index("USER appuser")
    assert DOCKERFILE.index("USER appuser") < DOCKERFILE.index("CMD [")


def test_dockerignore_does_not_exclude_the_contract_router_reads_at_import_time():
    """router.py reads contracts/provisioning.json at MODULE IMPORT time
    (the Vertex location allowlist -- see router.py's _CONTRACT/
    _VERTEX_LOCATIONS), so it must survive into the built image's COPY . .
    -- unlike the rest of contracts/ (the bot-contract-parity test fixture),
    which stays dev-only and excluded.

    Verified live once (not just by this source check): building the image
    with a bare `contracts/` exclusion and running
    `python -c "import main"` inside it raised FileNotFoundError --
    .dockerignore silently wins over COPY . ., so pytest/ruff running
    against the repo tree can never see this class of bug (that gap is
    exactly what the deploy-verify skill's live boot smoke test exists
    for). This test only guards against someone silently reintroducing a
    blanket `contracts/` exclusion with no `!contracts/provisioning.json`
    counter-pattern after it -- it cannot verify Docker's own ignore-engine
    behavior (negation order, `**` semantics), which is what the live
    build already did."""
    assert "contracts/" in DOCKERIGNORE
    assert "!contracts/provisioning.json" in DOCKERIGNORE
    assert DOCKERIGNORE.index("contracts/") < DOCKERIGNORE.index("!contracts/provisioning.json")


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
