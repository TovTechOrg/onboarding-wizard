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
