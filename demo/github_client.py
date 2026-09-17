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
