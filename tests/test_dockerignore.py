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
