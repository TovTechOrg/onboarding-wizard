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
