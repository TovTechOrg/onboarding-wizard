# UptimeRobot keep-warm frame (sub-project 5)

This doc holds the full, verbatim rule set sub-project 5 added to `CLAUDE.md`'s
core "Rules" section — moved here so it doesn't reload every session
regardless of whether this subsystem is being touched. Read this before
working on the UptimeRobot frame (`uptimerobot_client.py`). Nothing here was rewritten or condensed; it was relocated
as-is.

## What sub-project 5 (UptimeRobot keep-warm frame) adds to these rules

- **UptimeRobot's v3 REST API (`Bearer` auth, JSON,
  `https://api.uptimerobot.com/v3/monitors`) is used for every call this
  frame makes — never the legacy v2 form-API.** The sibling review-engine
  project's own deploy script still uses v2 for its read-only `getMonitors`
  check, and that is intentionally untouched — no reason to migrate a
  working read-only check. But v2's `POST /newMonitor` was verified live to
  reject monitor creation on a free-plan account (`403 "You are not allowed
  to use some settings with your current plan"`), while v3 was verified
  live to accept the identical creation on the same account. Do not
  "simplify" this frame's client onto v2 without re-verifying that live
  behavior first.
- **This frame reads a `sessionStorage` key it does not write:
  `onboarding.renderServiceUrl`.** The "Render service" frame (sub-project 6,
  now built) writes the deployed service's base URL there on its own
  completion — see
  `docs/superpowers/specs/2026-08-27-onboarding-uptimerobot-frame-design.md`
  section 3's forward contract, and (as of 2026-09-02)
  `restoreFromSession()`'s own write of this same key from
  `GET /api/session`'s `render-service` display field on page load, so a
  reload after that frame is done still has it available.
- **Dedupe-before-create is load-bearing, not an optimization.** Every
  credential submission to this frame (including a "Change" resubmit)
  calls `GET /v3/monitors` before ever calling `POST /v3/monitors` — a
  monitor is only created if none already watches the derived
  `<render_service_url>/healthz` target. Removing this check reintroduces
  orphaned duplicate monitors on every resubmit.
- **There is no way to detect a read-only (Monitor-Specific) API key
  server-side — verified live.** `POST /v3/monitors` and `GET
  /v3/monitors` both return the identical `401 {"message": "Invalid
  token.", "code": "003-005"}` for a valid-but-read-only key as for a
  wholly invalid one. Do not add a `reason` value implying this frame can
  tell the two apart; the only mitigation is UI copy (the input's help
  text and the `unauthorized` error both name the Main-API-Key requirement
  explicitly).
- **The monitor's `friendlyName`/`type`/`interval`/`timeout` are fixed, not
  visitor-configurable** — `friendlyName` is always the derived target URL
  itself (matching this project's own production monitor's existing
  naming), `type: "HTTP"`, `interval: 300`, `timeout: 30`. A future change
  that lets the visitor choose these needs its own brainstorm, not a quiet
  addition here.
- **`uptimerobot_client.py` follows the same raw-`httpx`, no-SDK
  shape as `render_client.py`/`github_client.py`/`supabase_client.py`** —
  UptimeRobot has no official SDK. Tests mock via `respx`, same as those
  three modules, not the SDK-boundary mocking `llm_client.py`'s tests
  needed for `google-genai`.
- **Dedupe-before-create pages through every result via v3's cursor-based
  `nextLink`, not just the first `GET /monitors` response (2026-08-28 fix,
  see `ISSUES.md`)** — a single-page scan silently misses a match on any
  account with more monitors than fit in one page, defeating the
  dedupe-is-load-bearing guarantee above. The page cap
  (`_MAX_LIST_PAGES`) is a defensive bound against a malformed/looping
  `nextLink`, not an expected real-account limit.
- **`delete_monitor` (backed by `DELETE /v3/monitors/{id}`) exists so a
  changed render-key or render-service frame can clean up the monitor it
  orphans** — `static/index.html`'s `cleanupOrphanedUptimeMonitor()`
  calls it best-effort from `beginChange`, before `relockDownstreamOf`
  clears the uptime-pinger frame's own `sessionStorage` record (which is
  where the monitor id it needs comes from). Only `render-key` and
  `render-service` changes trigger it — every other frame's "Change"
  leaves the deployed service's URL, and therefore the existing monitor,
  valid.

