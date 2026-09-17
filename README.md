# Onboarding Wizard

[![CI](https://github.com/TovTechOrg/onboarding-wizard/actions/workflows/ci.yml/badge.svg)](https://github.com/TovTechOrg/onboarding-wizard/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)
![uv](https://img.shields.io/badge/package%20manager-uv-de5fe9.svg)

**[Read the guide →](https://tovtechorg.github.io/pr-review-bot)**
— the sibling review-engine project's guide; start here to try the live
demo or walk through setup.

**[See it work → live demo](https://tovtechorg.github.io/pr-review-bot/demo/)**
— walk the wizard end to end against mocked Render, GitHub and LLM
integrations, then watch the bot it "provisions" review a pull request. No
credentials, nothing real is created.

**[Try it →](https://onboarding-wizard-mk6m.onrender.com/)** — this repo's own live deployment.

A self-service setup wizard: a visitor walks through it, in their own
browser, to provision their own instance of a separate PR-review bot —
creating and validating a GitHub App, provisioning a Supabase project,
supplying an LLM provider credential, setting up an UptimeRobot keep-warm
monitor, and triggering the final Render deploy — ending with their own
live bot+dashboard service.

Every credential in that flow is the *visitor's own*: this service relays
each one to the relevant external API to validate/act on it, and never
holds a long-lived operator credential of its own. See `CLAUDE.md` for the
full architecture, per-frame design notes, and the secret-handling rules
that govern this codebase.

## Local development

```bash
uv sync --all-extras --dev
cp .env.example .env
```

The browser-behavior tests (`tests/test_onboarding_page_browser.py`) need
Chromium's binary installed once per machine:

```bash
uv run playwright install chromium
```

Fill in `.env`'s two required settings:

- `DATABASE_URL` — this service's own dedicated Postgres, used only for its
  server-side wizard session (never a visitor's provisioned project).
- `ONBOARDING_SESSION_ENCRYPTION_KEY` — a Fernet key used to encrypt every
  credential value before it's written to that session store. Generate one
  with:

  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

Then run:

```bash
uv run pytest -v
uv run ruff check .
```

Running the full suite without a `DATABASE_URL` set requires Docker: tests
that touch the session store (`-m db`) and the browser-behavior tests
(`-m browser`, which boot the real app and therefore need it too) fall
back to spinning a throwaway Postgres via testcontainers when no
`DATABASE_URL` is present. [Docker Desktop](https://www.docker.com/products/docker-desktop/)
works for this the same as Docker Engine — `testcontainers` just needs a
reachable Docker daemon. With a real `DATABASE_URL` set (e.g. in `.env`),
Docker isn't needed at all.

Re-vendor pr-review-bot's provisioning contract and its pin (rewrites
`contracts/provisioning.json` and `.ci/pr-review-bot-ref` together, or
neither; never stages or commits): `uv run python -m scripts.update_bot_contract`

## Deployment

Deployed on Render as a single Docker web service (`render.yaml`,
`Dockerfile`), backed by its own dedicated Supabase/Postgres session store
(`DATABASE_URL` above) — never the bot's own database. Live at
<https://onboarding-wizard-mk6m.onrender.com/>. See
`docs/superpowers/specs/2026-09-01-onboarding-server-side-session-design.md`
for the full session design.

## Related project

This wizard provisions deployments of a separate PR-review bot+dashboard
project, which lives in its own repository — not part of this codebase.
That repository's own history is also where this repo's specs/plans/docs
that predate the split (referenced by filename in a few places here, but
no longer present in this repo) can still be found.

## More

- `CLAUDE.md` — full architecture, module contracts, and secret-handling
  rules.
- `ISSUES.md` — this service's incident and parked-issue history.
