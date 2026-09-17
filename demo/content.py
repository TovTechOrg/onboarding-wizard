"""Fixed values the demo wizard reports. Nothing here authenticates anything."""

from __future__ import annotations

# Where "Finish & Deploy" sends the reader: the already-deployed bot demo.
# The hostname is the deployed service's real slug (demo-pr-review-BOT, not
# -engine -- corrected 2026-09-17 after the service was created and the old
# value was found to point at a hostname that never existed). Baked in rather
# than configured per-deployment, per the design's "no env-var configuration
# to get wrong" argument; DEMO_BOT_URL remains available as an override.
import os

DEMO_BOT_URL = os.environ.get(
    "DEMO_BOT_URL", "https://demo-pr-review-bot.onrender.com"
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
