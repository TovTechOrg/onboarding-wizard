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

# demo/supabase_client.py's fixed success values -- the supabase frame is
# pre-seeded complete (demo/session_store.py's _PRESEEDED_FRAMES) and never
# driven through the browser, but these back the mock's return values for
# any endpoint reached directly (defense in depth, see CLAUDE.md's C3
# finding and its "mock every client router.py can call" generalization).
DEMO_SUPABASE_ORG_SLUG = "demo-org"
DEMO_SUPABASE_ORG_NAME = "Demo Organization"
DEMO_SUPABASE_PROJECT_REF = "demo-ref"
DEMO_SUPABASE_STATUS = "ACTIVE_HEALTHY"
DEMO_SUPABASE_DB_HOST = "db.demo-ref.supabase.co"
DEMO_SUPABASE_DB_USER = "postgres"
DEMO_SUPABASE_DB_NAME = "postgres"
DEMO_SUPABASE_DB_PORT = 5432

# demo/uptimerobot_client.py's fixed success value.
DEMO_UPTIME_MONITOR_ID = 900001
