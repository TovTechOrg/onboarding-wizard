# Render service creation + deploy, and Dashboard login (sub-project 6)

This doc holds the full, verbatim rule set sub-project 6 added to `CLAUDE.md`'s
core "Rules" section — moved here so it doesn't reload every session
regardless of whether this subsystem is being touched. Read this before
working on the Render service/deploy frames and the Dashboard login frame (`render_client.py`, the final deploy sequence). Nothing here was rewritten or condensed; it was relocated
as-is.

## What sub-project 6 (Render service creation + deploy, final) adds to these rules

- **What was originally decomposed as one "sub-project 6" frame is
  actually two frames**: "Render service" (position 2, right after the
  Render-key frame) creates the service; the pre-existing placeholder
  `frame-render-deploy` (reserved since sub-project 1's shell, always
  last in `FRAME_ORDER`) is "Finish & Deploy" — triggers the real deploy
  once frames 2-5 have run. Do not conflate them or try to merge them
  back into one frame; the accordion's sequential-lock model is why
  they're split (see
  `docs/superpowers/specs/2026-08-27-onboarding-render-service-frame-design.md`
  section 3).
- **Reversed 2026-09-02: no frame pushes its own credential to Render
  incrementally anymore.** The original design here had frames 2/3/4 each
  push-and-clear the moment they validated, as a deliberate
  browser-residency-shrinking property. That property is moot now that
  those credentials never sit in `sessionStorage` to begin with — they're
  persisted server-side (`session_store.py`) on validation instead. The
  four per-frame push endpoints (`github/push-render-vars`,
  `supabase/push-render-var`, `llm/push-render-vars`,
  `dashboard-auth/push-render-vars`) and their four frontend push
  functions are **deleted, not deprecated** — do not resurrect this
  pattern for a new frame.
- **One bulk push instead: `POST /api/render/bulk-push-env-vars`**, called
  by the final "Finish & Deploy" frame (`triggerRenderDeploy()`) right
  before `trigger-deploy`. It reads every completed frame's data straight
  from the session (`session_store.read_frame`, no request body at all)
  and assembles the full env-var dict in one place — `router.py`'s
  `bulk_push_render_env_vars()`. A frame whose session data is missing
  (never completed) is simply omitted from the push, not an error; the
  wizard's own sequential frame-lock already guarantees every frame is
  done by the time this endpoint is reachable in normal use.
  `render_client.push_env_vars`'s push-failure-handling behavior
  (partial-failure reporting via `_push_result`) is unchanged — only
  *when* it's called moved from per-frame to this one call site.
- **The bulk push also always includes `_GENERIC_OPERATIONAL_ENV_DEFAULTS`**
  — as of 2026-09-08 (see the dedicated slot_config bullet below) this is
  down to just `GITHUB_TARGET_REPO`. It used to also carry the sibling
  review-engine project's dispatcher/timeout tuning knobs plus
  `VERTEX_GCP_LOCATION`; all of those are now DB-only over there (no Render
  env var at all) and are dropped from this dict entirely rather than
  pushed as `""` — the 9 dispatcher knobs get a real value from that
  project's own boot-time backfill (`review_queue/store.py::init_pool`
  fills every NULL `runtime_config` column from its own declared `Settings`
  defaults; the older `_seed_runtime_config_defaults` was removed on
  2026-09-09 and is not what fills them any more — see the cross-repo
  contract section above), and `VERTEX_GCP_LOCATION` (along with
  model and, for vertex, project) is seeded by this wizard directly into
  `slot_config` instead — see below. `GITHUB_TARGET_REPO` is the one
  survivor of the old "unconditional hardcoded operational default"
  pattern this dict used to hold several of; it's pushed as `"*"`
  (2026-09-07) — see the dedicated bullet below for why. This dict is
  still hand-written, but no longer hand-*checked*:
  `tests/test_bot_contract_parity.py` asserts its keys and placement
  against `contracts/provisioning.json`, so a rename or a placement move
  on the bot side fails a test here instead of silently pushing a name
  nothing reads.
- **`slot_config` (model, and for vertex, project/location) AND
  `runtime_config` (`provider`, `{provider}_key_index`) are seeded directly
  into the newly-provisioned Supabase database, neither ever pushed as a
  Render env var** (slot_config: 2026-09-08, mirroring that project's own
  slotted-config-and-db-delegation work; provider/key_index generalized to
  the same DB-only treatment on 2026-09-09 — see
  `docs/superpowers/specs/2026-09-08-slotted-config-and-db-delegation-design.md`
  section 5 and `~/pr-review-bot`'s
  `docs/superpowers/specs/2026-09-09-provider-key-index-db-only-design.md`,
  and that project's `providers/active_model.py`/`providers/active_vertex_slot.py`/
  `providers/active.py`, none of which have an env fallback at all: a
  missing/NULL row is a real boot-time refusal, not a degraded default).
  `router.py`'s `_seed_provider_config()` (renamed from `_seed_slot_config`
  when the runtime_config write was added) opens a raw, short-timeout
  `psycopg` connection directly against the visitor's own
  `supabase["database_url"]` (already in-session by this point) — a
  one-shot write, not a pool — and runs both tables' `CREATE TABLE IF NOT
  EXISTS` before the `INSERT ... ON CONFLICT DO UPDATE`s, since a
  freshly-provisioned database has no schema yet at all — the deployed
  bot's own first boot is what normally creates it, and this wizard runs
  before that first boot ever happens. As of 2026-09-10 `runtime_config`'s
  DDL here is deliberately NARROW — only the columns
  `contracts/provisioning.json` lists as `provisioner_required` /
  `provisioner_required_one_of`, i.e. the ones this wizard actually writes.
  It used to have to be that project's FULL column set, because a
  `CREATE TABLE IF NOT EXISTS` from a narrower creator would leave the bot's
  own boot unable to widen the table; that project now widens it itself
  (`ADD COLUMN IF NOT EXISTS` per declared column, then a `COALESCE`
  backfill), so the requirement expired and the copied 22-column DDL and
  15 copied default values went with it. See `router.py`'s own comment above
  `_RUNTIME_CONFIG_SCHEMA` and the cross-repo contract section above.
  `_SLOT_CONFIG_SCHEMA` is still the full six columns — it could shrink too,
  but every column is either required or optional-and-written, so there is
  nothing to gain; that is deliberate, not an oversight.
  Both writes share one connection/transaction, so a failure partway
  through never leaves `slot_config` seeded with no matching
  `runtime_config.provider` or vice versa. Always writes `slot_index = 0`
  and `{provider}_key_index = 0`
  — this wizard has no UI for choosing a numbered credential slot, it only
  ever provisions the base credential, which matches
  `providers/key_index.py::active_key_index`'s own default-to-0 behavior
  when no override is set (the DB write just makes that default explicit
  rather than relying on an unset column reading the same way).
  `{provider}_key_index`'s column name is looked up through a hardcoded
  `_KEY_INDEX_COLUMNS` dict (duplicated from that project's
  `providers/registry.py::KEY_INDEX_COLUMNS`) keyed by `provider`, never
  built from it directly — that dict IS the injection guard for the
  f-string that names the column. **A project/region collection UI now
  exists in this wizard (2026-09-12) — see the sub-project 4 section
  above.** `vertex_gcp_project`/`vertex_gcp_location` are no longer
  `NULL`/hardcoded `"us-central1"` for every vertex credential: they carry
  the visitor's own verified choice, collected in the LLM-provider frame
  and probed against there (`/api/llm/confirm`) before this seed re-probes
  and writes the same pair. For a visitor who keeps the default selection
  the seeded project is the same string that project's `factory.py` would
  have derived from the key's own embedded `project_id` anyway; for a
  visitor who picks another project, the seeded value is the correct one
  and the old derivation would have been wrong. `factory.py` still raises
  if location resolves empty with no fallback of its own — `router.py`
  refuses with `vertex_pair_missing` before ever reaching this seed if the
  session's stored pair is absent or the location is no longer in the
  contract allowlist (a pre-upgrade session, `session_store.SESSION_TTL`
  is 4 hours), rather than silently seeding `NULL`/an unvalidated value.
  **This seed runs before the Render push, and a seed failure refuses the
  whole call (`{"valid": false, "reason": "slot_config_seed_failed"}`)
  without ever calling `render_client.push_env_vars`** — the visitor must
  never reach a state where Render has the LLM credential but the database
  has no matching `slot_config`/`runtime_config` row for it, which the
  no-fallback resolution above would turn into every review request
  failing at runtime, or the deployed service refusing to boot at all. The
  reverse case (seed succeeds, Render push then fails) is unremarkable and
  already covered by the existing partial-failure reporting below — a
  dangling, unused `slot_config`/`runtime_config` row for a service that
  never got its credential is not a correctness problem the way the other
  ordering would be.
- **The final `render-deploy` frame stays open (doesn't collapse) once
  done** (2026-09-02) — `completeFrame()` grew a 5th, optional `keepOpen`
  parameter (every other call site omits it, keeping the default
  collapse-on-complete behavior). The dashboard link (the service's live
  URL, which routes to the dashboard's login-gated `GET /` once the
  deploy is live) is the actual payoff of finishing the wizard; collapsing
  the frame the instant it appears would hide it. Applies to both the live
  completion path (`finishRenderDeploy()`) and the reload-resume path
  (`restoreFromSession()`), which previously didn't even re-show the
  done-section/link at all on a reload after a completed deploy — fixed
  alongside this.
- **`relockDownstreamOf(id)` relocks by real dependency, not by page
  position** (2026-09-02) — `FRAME_DEPENDENTS` is a precomputed-transitive-
  closure map naming which frames' already-submitted data actually goes
  stale when a given frame's data changes (e.g. `render-service`'s
  `service_url` feeds `github-app`'s webhook-URL check and
  `uptime-pinger`'s monitor, so changing it relocks both; `llm-provider`
  feeds nothing but the final bulk push, so changing it relocks only
  `render-deploy`). Replaces the old "relock everything positioned after
  `id` in `FRAME_ORDER`" rule, which forced redoing frames — e.g.
  `uptime-pinger` after an `llm-provider` change — that read none of the
  changed frame's data. Because `render-deploy` is frequently *not* the
  next positional frame after the one just resubmitted anymore,
  `completeFrame()` also gained `maybeUnlockDependentsAfterRedo()` (renamed
  and generalized 2026-09-07 from an earlier `render-deploy`-only version —
  see the Parked/fixed-bug note below for why): after any frame completes,
  it walks that frame's *real* dependents (`FRAME_DEPENDENTS[completedId]`)
  directly and, for each one still locked, checks `prereqsFor(depId)` (the
  reverse of `FRAME_DEPENDENTS` — every frame that must be done before
  `depId` can unlock) and unlocks it if every real prerequisite is done,
  regardless of what sits positionally in between. `render-deploy` keeps
  one extra gate on top of the general rule: `renderDeployReachedOnce` —
  set the first time it's ever unlocked, via the plain first-pass chain or
  `restoreFromSession()` resuming an already-deployed session — must be
  true before it can be unlocked this way, since `uptime-pinger` sits
  before it on the page but isn't one of its real prerequisites, and a
  first-time visitor must still be made to fill in the uptime monitor
  before ever reaching "Deploy". `completeFrame()`'s own `unlockFrame(next)`
  call is also guarded on `next` actually being `"locked"` — otherwise
  a redo's positional "next frame" (which may be an untouched, already-
  `"done"` frame) would get wrongly reopened and reset to "Not started".

  **A real bug in the original, `render-deploy`-only version of this
  mechanism (found and fixed 2026-09-07):** the old gate was
  `renderDeployCompletedOnce`, set only on a *successful* deploy — but a
  visitor who reached `render-deploy` and had the deploy attempt *fail*
  (never completing it), then went back and redid an earlier frame (e.g.
  recreating a manually-deleted Render service via `render-service`'s own
  "Change"), would find every one of that frame's real dependents relocked
  and *stuck* — `completeFrame()`'s positional chain only ever checks the
  single immediate next frame in `FRAME_ORDER`, and if that one happens to
  already be `"done"` (not locked, e.g. `dashboard-auth` sitting right
  after `render-service`), the chain silently stops there without ever
  reaching the real, still-locked dependent further down (`github-app`).
  The only way to make progress was redoing the unrelated, already-correct
  intervening frame just to trip the chain past it. Generalizing the
  mechanism to check every real dependent directly (not just
  `render-deploy`'s) and renaming the gate to track *reaching* the frame
  rather than *completing* it (a failed deploy still reaches the frame)
  fixes this for every frame, not just a hand-picked one.
  `lockFrame("render-deploy")` additionally clears the `deployed`/
  `pending_deploy_id` flags from `render-service`'s own storage blob (the
  only place they live — "render-deploy" has no `STORAGE_KEYS` entry of its
  own), so a reload mid-redo shows the frame's initial pre-deploy state
  instead of resurrecting the previous deploy's live URL.
- **`unlockFrame()` now auto-opens the `<details>`, and `render-deploy`
  shows `"deploying…"` while a triggered deploy is in flight** (2026-09-02)
  — a newly-reachable frame previously became clickable but stayed
  visually collapsed, with no cue a new step was ready.
- **The DB-synced operational keys (cooldown/usage-cap/`REVIEW_DRAFT_PRS`)
  need no wizard-side push at all** (2026-09-02) — unlike the Render-env-var
  knobs above, the review engine's own boot-time backfill handles this on
  its side — no second service needs to open a connection to write into
  that database's schema from the outside (`store.init_pool()`'s
  `COALESCE` upsert only fills a currently-NULL column, so it never
  overwrites an operator's own value; see the cross-repo contract section
  above — the older `ON CONFLICT (id) DO NOTHING` first-boot seed this
  bullet used to describe was removed 2026-09-09). This is what a freshly
  wizard-provisioned Supabase project gets on the sibling review-engine
  project's first boot. See `ISSUES.md`'s 2026-09-02 "push all optional env
  vars" entry for the reasoning and the ordering constraint that ruled out
  doing this from the wizard directly (the table doesn't exist until that
  first boot, which is after the wizard's bulk push already ran).
- **The created service's public URL is always derived from Render's
  returned `service.slug`, never the submitted `name`.** Render may
  normalize the name server-side; a create-service response was verified
  live to have no `service.url` field at all — trusting the requested
  name instead of the response's slug would silently point
  `onboarding.renderServiceUrl` (frame 5's forward contract) at a URL
  that doesn't exist.
- **Neither `VERTEX_GCP_PROJECT` nor `VERTEX_GCP_LOCATION` is pushed as a
  Render env var — both are DB-only now (2026-09-08 slotted-config-and-db-
  delegation, see the dedicated `slot_config` bullet above).** This
  superseded an earlier version of this bullet (through 9de5f04) that still
  said `VERTEX_GCP_LOCATION` **is** pushed via `_GENERIC_OPERATIONAL_ENV_DEFAULTS`
  — true before that commit, false after it: that dict now holds only
  `GITHUB_TARGET_REPO`, and both values are seeded into
  `slot_config.vertex_gcp_project`/`vertex_gcp_location` instead. As of
  2026-09-12 (see the sub-project 4 section above), both are the visitor's
  own verified choice from the LLM-provider frame's project/region
  dropdowns, not a hardcoded `"us-central1"`/derived-at-runtime pair —
  `location` is pinned to the vendored contract's allowlist at every entry
  point that accepts one, since it's part of the outbound Vertex hostname.
  Do not re-add either var to `_GENERIC_OPERATIONAL_ENV_DEFAULTS` without a
  concrete reason the slot_config-seeding path has stopped covering it.
- **`GITHUB_TARGET_REPO` is pushed as `"*"` (2026-09-07), reversing the
  original "never pushed" decision above.** The sibling review-engine
  project's `main.py` lifespan now refuses to boot at all without this set
  explicitly — its own `config.py` names `"*"` the required, operator-set
  sentinel for "no restriction," replacing the old bare-empty-string
  default. This wizard has no frame that collects a repo allowlist from the
  visitor, and every instance it provisions is a track-all install anyway,
  so pushing `"*"` unconditionally avoids handing the visitor a
  boot-looping deploy they'd have no way to diagnose.
- **Deploy status polling keeps its own copy of Render's deploy-status
  buckets in `render_client.py`, matching the sibling review-engine
  project's equivalent sets by hand — no import between the two repos.**
  Keep the two in sync by hand if either changes. This one has no parity
  assertion (the contract carries no deploy-status block) — unlike
  `router.py`'s `_LLM_ENV_VAR_NAMES`, which is a hand-written duplicate too
  but is checked against the vendored contract (see the cross-repo
  contract section above); do not use this bullet as a template for a new
  unasserted duplicate.
- **Frame 5 (UptimeRobot)'s "blocked, no Render URL" state is no longer
  reachable in normal sequential flow** — the "Render service" frame now
  writes `onboarding.renderServiceUrl` two frames before UptimeRobot
  unlocks. The blocked-state markup and its check function are
  unchanged and NOT dead code: they remain a correctness safeguard for a
  corrupted or manually-manipulated `sessionStorage` state, not something
  this sub-project needed or was asked to remove.
- **The GitHub App's webhook URL is a checked requirement, not something
  this wizard ever writes.** The instructions tell the visitor the real
  Render service URL (`<service_url>/webhook`) to type into GitHub's own
  form; `validate_app()` (see the sub-project 2 section above, updated
  2026-09-01) reads it back via `GET /app/hook/config` and reports a
  pass/fail line. There is no `PATCH /app/hook/config` call anywhere in
  this service — the earlier placeholder-then-PATCH flow, and later the
  manifest-flow's baked-in webhook URL, both predated the 2026-09-01 move
  to fully manual App creation; do not reintroduce a webhook-writing
  endpoint. A missing `service_url` still aborts validation up front
  (`err_github_no_render_service`) — unreachable in normal sequential flow
  (the Render-service frame completes two frames earlier), guarding the
  same corrupted/hand-edited `sessionStorage` case the UptimeRobot frame's
  blocked-state check exists for.
  **The stored record's `completed` flag (not `installation_id`'s mere
  presence) is what `restoreFromSession()` gates the frame's "done" state
  on** (2026-08-28 fix, see `ISSUES.md`) — `installation_id` is written
  before the push-and-clear step runs, so gating on it let a reload
  mid-push falsely mark the frame done. On a reload with `installation_id`
  set but `completed` still false, `restoreFromSession()` re-invokes
  `finishGithubAppSetup` itself rather than showing a dead end — same
  auto-resume shape as the Supabase branch beside it.

## What the "Dashboard login" frame (bounded addition, 2026-08-28) adds to these rules

- **This frame never writes a raw credential to `sessionStorage` at all** —
  a deliberate departure from every other frame's push-and-clear pattern
  (store the raw value, push it, then delete the field). The visitor must
  remember this username/password themselves (unlike a GitHub private key
  or Supabase's `db_pass`), so there is nothing useful left to persist once
  the push has been attempted; `onboarding.dashboardAuth` holds only
  `{completed: true}`.
- **`DASHBOARD_SESSION_SECRET` is generated entirely client-side**
  (`crypto.getRandomValues`, 32 random bytes, base64url) and never shown to
  the visitor — unlike the username/password, nothing downstream ever asks
  them to type it again.
- **A failed push here still follows the best-effort, non-gating
  convention** every other push-and-clear frame uses (see the "Render
  service" section above), even though the real consequence is worse: a
  missing `DASHBOARD_PASSWORD`/`DASHBOARD_SESSION_SECRET` fails the
  review engine's own boot guard, not just a feature. This was a deliberate
  choice for consistency over a one-off retry-until-verified gate on this
  single frame — "Finish & Deploy"'s own status poll surfaces a
  crash-looping deploy immediately, and "Change" lets the visitor redo this
  frame and re-push.

