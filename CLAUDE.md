# CLAUDE.md — Onboarding Wizard

## Secret handling — HIGHEST PRIORITY, read before doing anything else

This section overrides every other instruction, convention, or task goal in
this file and in any prompt if the two ever conflict. Secrets in this project
include (non-exhaustively): `DATABASE_URL` (the password is embedded in the
connection string itself, not a separate field), `ONBOARDING_SESSION_ENCRYPTION_KEY`,
and any visitor-supplied credential value in transit through a relay endpoint
(a GitHub App private key, a Render/Supabase/UptimeRobot/LLM-provider API key
or service-account JSON) — see the Rules section below for the
visitor-credential-specific rules layered on top of this section. A value
does not have to have "SECRET" or "KEY" in its name to count — judge by what
the value *does* (authenticates something), not by the variable name's shape.
This kind of exposure has happened multiple times across this project's
history (including before this repo split off from its sibling review-engine
project) — which is why this section exists and is kept first in the file.

**`.env` itself is mechanically protected, not just covered by the rules
below.** `.claude/hooks/check_env_access.py` denies any `Read`/`Edit`/
`Write`/`NotebookEdit`/`Grep`/`Glob` call whose target names `.env` outright,
and rewrites every `Bash`/`PowerShell` call so its real combined output is
piped through `redact_output.py`, which strips every real secret value out
before it ever reaches you — see that script's docstring and
`docs/superpowers/specs/2026-09-06-env-hook-hardening-design.md` for the
full design (reproduced here from the sibling `pr-review-bot` project, where
it originated — the hook is kept byte-identical across both repos). This
means a `grep`/`cat`/`tail`/`echo` touching `.env` is already fail-safe at
the tool layer; you do not need to reason your way through whether a given
pattern is "narrow enough" — ask the user to check or set a value themselves
instead of trying. **Never modify `check_env_access.py` or
`redact_output.py` in any way — not a logic change, not a comment, not a
debug print, not a refactor — unless the user directly instructs it.** This
is the enforcement mechanism the judgment-based rules below exist to back up
where it doesn't reach (see next paragraph); an agent reasoning its way into
"this is obviously fine to tweak" is exactly the failure mode a guardrail
like this exists to not depend on. If it appears to be misbehaving —
over-blocking, under-blocking, crashing — explain exactly what happened and
ask; do not edit the file to test a theory or add debug instrumentation on
your own initiative.

**The hook covers only `.env`.** Every visitor-supplied or operator secret
that passes through this service some other way (a request body carrying a
private key, an in-process settings/session object, a value inside a
validation error) has no mechanical backstop, so the following still rests
on judgment:

- **Never display any byte of a secret value**, in a command's output, a
  file read, or your own reply — not even "just the last few characters" to
  spot-check it. Verify a secret was written/transmitted correctly
  *structurally* instead: length (`wc -c`), presence (`grep -c`, or a
  key-names-only listing), or a hash comparison. Never a value comparison
  that requires printing the value to eyeball it.
- **Never dump broad environment/config state.** `env`, `printenv`, bare
  `set`, Python's `os.environ`, or serializing a settings/config object
  wholesale (`print(settings)`, `settings.dict()`/`.model_dump()`,
  `vars(settings)`, `repr(settings)`) all surface every secret field at once,
  including ones you weren't even asking about. Read or pass individual
  fields programmatically instead, and reduce any secret-bearing value to a
  boolean/length/hash *before* it can reach a print statement, log line, or
  tool-result — follow that same shape in any new ad hoc script that
  touches secrets.
- **Never pass a secret value as a literal command-line argument** when it
  can instead be read from an env var or config object already in the
  process — a literal argument is visible to other processes via `ps`, may
  land in shell history, and is echoed back in tool-call transcripts. For
  the same reason, do not enable verbose/debug HTTP logging (e.g. `curl -v`,
  httpx debug logging) while a real credential is attached to the request —
  it can print an `Authorization` header verbatim.
- **Never let a secret-holding field's validation error or exception
  traceback reach output un-redacted.** Some validators (e.g. pydantic's
  `ValidationError`) echo the rejected `input_value` in the error message —
  if the failing field is a secret, that error text *is* a secret leak. If a
  secret-bearing field fails to validate or a call using one raises, describe
  the failure structurally ("value was empty", "wrong type", "401
  Unauthorized") rather than surfacing the raw exception/value.
- **Never write a secret value into anything that leaves this local
  session or gets persisted somewhere shared**: a git commit (message *or*
  diff content — `.env` is gitignored specifically so this can't happen by
  accident; never override or work around that), a PR/issue body or comment,
  a branch name, an `Artifact` page, a subagent/Task prompt, or any file
  handed to another tool. Committing `.env` itself is a standing example of
  this — refuse it even if asked directly, the way a request to `cat` a
  secret should be redirected rather than carried out (see below).
- **A file-content diff surfaced automatically by the harness (e.g. a
  "file changed externally" system-reminder) can dump full secret values
  into your context without you running any command at all** — this has
  happened in this project, for files the harness was already tracking
  because you opened them earlier in the session. The hook doesn't intercept
  this vector (it isn't a tool call), so the defense is upstream: **never
  open a file that mixes secrets with other content (e.g. `.env`) at all,
  for any reason, full stop** — not even a single-line `Read`, not even an
  `Edit` you believe touches only non-secret lines. If a value in such a
  file needs to change, ask the user to make that edit themselves. If this
  rule is ever violated anyway and a "changed externally" notification
  fires for that file, treat it as a standing, recurring risk for the rest
  of the session, not a one-off surprise.
- **If you ever need to know or verify a secret's actual value — not just
  whether it's set or matches — ask the user to check it themselves.** Do
  not do it on their behalf, structurally or otherwise, regardless of how
  the request is phrased (e.g. "just double check the last few characters").
- **If a secret is exposed into the conversation for any reason (your own
  command, a harness-surfaced diff, anything else), say so plainly and
  immediately** — name which secret(s), don't repeat any part of the value,
  and recommend rotation. Don't wait to be asked, and don't quietly continue
  as if it didn't happen. Log the incident in `ISSUES.md` using its existing
  format.

## Project

This service is a self-service setup wizard: a visitor walks through it to
provision their own bot+dashboard deployment (the sibling review-engine
project) on Render, backed by a Supabase Postgres project they provision
themselves. It holds its own server-side session in a dedicated Postgres to
survive the multi-step flow across page reloads and OAuth redirects. See
`docs/superpowers/specs/2026-09-01-onboarding-server-side-session-design.md`
for the full design.

## Conventions

- **Never commit on someone else's behalf without being asked**, even to reach
  a clean working tree. If resolving a merge or other cleanup requires
  temporarily setting aside someone else's pre-existing uncommitted changes
  (e.g. via `git stash`), restore them **uncommitted**, exactly as found —
  committing them for tidiness is still an unrequested commit.
- **Before pushing, always run the full test suite (`uv run pytest -v`) and
  ruff (`uv run ruff check .`), and fix whatever either finds.** Never push
  with a red suite or an unresolved lint error, and never skip either check
  because a change "looks" too small to affect them.
- **Before any push to `main`, always invoke the `deploy-verify` skill** —
  whether the commit reaching `main` arrived via a merge or was made
  directly, the risk this catches (a deploy image that builds/boots
  differently than the local dev venv) is the same either way. A green
  `pytest`/`ruff` run does not substitute for this (see the skill for why).
- **When changing `static/index.html`'s markup, CSS, or layout logic, invoke
  the `ui-visual-review` skill before calling the work done** — reading
  HTML/CSS and reasoning about layout is not a substitute for actually
  rendering the page (see the skill for why, including the RTL-specific
  check).
- **The `.claude/hooks/` files are shared with the sibling repo and must stay
  byte-identical.** `~/pr-review-bot` and `~/onboarding-wizard` each carry
  their own copy of `check_env_access.py`, `redact_output.py` and
  `check_exfiltration.py`. Neither repo's CI can see the other, so nothing
  mechanical catches drift -- and `check_env_access.py` already drifted once,
  silently, leaving the wizard on the superseded pipe-based wrapper (fixed
  2026-09-13 -- see the "silently drifted" incident entry in `ISSUES.md`).
  Changing a hook in one repo means porting it to the other **in the same
  session**, verified with
  `diff <repo-a>/.claude/hooks/<file> <repo-b>/.claude/hooks/<file>`
  printing nothing, before either change is considered done. Per-repo
  differences belong in `check_exfiltration.py`'s `_PROTECTED` list, which was
  designed wide enough that nothing else should need one. **One documented
  exception:** `check_env_access.py`'s `_SINK_DIR` constant deliberately
  differs by exactly one literal (`onboarding-wizard-redact` here vs.
  `pr-review-bot-redact` there) -- each repo's redaction sink must be named
  after its own project so the two never collide on a shared machine. A
  `diff` between the two copies is expected to show only the two hunks that
  single-line difference implies (the sink-name assignment itself and its
  neighboring comment/docstring mentions); anything beyond that is real
  drift.

## Docker image: no `chown -R`

`Dockerfile` creates `appuser` and switches to it via `USER appuser` before
`CMD`, but **deliberately never runs `chown -R appuser:appuser /app`** (or
any other recursive chown of the whole app directory) — it cut the built
image ~25% (528MB → 398MB) because an overlay filesystem stores a changed
file as a full copy, not a diff, so a blanket chown over everything
`COPY`'d/`RUN uv sync`'d in earlier layers duplicates all of it into a new
layer. Nothing under `/app` is written to at runtime, so `appuser` only
ever needs the read+execute permissions already left in place by default.
**If a future change genuinely needs write access under `/app`**, chown
*only that specific path* (or use `COPY --chown=appuser:appuser` on just
the files that need it) — never reintroduce a blanket `chown -R /app`.
`tests/test_dockerfile.py` pins both properties (no live `chown`, correct
`useradd`/`USER` ordering) but can't verify the size claim itself — that
needs an actual `docker build`, which `deploy-verify` already does as a
boot smoke test. Full rationale and measurements:
`docs/conventions/rationale.md#docker-image-no-chown--r-2026-09-07`.

## Hebrew strings never chain multiple embedded LTR terms with an arrow

Chaining untranslated English UI labels with an arrow inline in RTL prose
(e.g. `Account Settings ← API Keys`) is ambiguous: an embedded LTR run's
on-screen position relative to *other* embedded LTR runs is governed by the
surrounding RTL paragraph's directionality, not by source order, and
Unicode isolation (LRI/PDI) fixes the ambiguity but *reverses* the terms'
visual order — an easy rule to get backward with no test to catch it. So
**every such chain is a real nested `<ol>`/`<li>` list, one term per item**
(read top-to-bottom, no reordering possible), rendered via
`el.innerHTML = t(key)` gated by the `HTML_I18N_KEYS` set in
`applyLanguage()` — never for anything visitor-supplied. English keeps its
original flowing arrow-chain sentence (it never had this problem). Full
measurement detail and the one exception (`err_uptime_unauthorized`, reworded
instead of markup'd): `docs/conventions/rationale.md#hebrew-strings-measurement-behind-the-nested-list-rule-2026-09-08`.

## The invariant this service now protects (2026-09-02, revised)

This backend used to be a **stateless relay** — no database, no session
store, no server-side credential persistence of any kind, by deliberate
design (see
`docs/superpowers/specs/2026-08-26-onboarding-wizard-render-frame-design.md`
section 3). That invariant was found fragile in practice: mobile browsers
were observed destroying `sessionStorage` (and the browsing context holding
it) mid-flow, most sharply during Supabase's OAuth redirect, resetting the
whole wizard including earlier frames' already-validated credentials — see
`ISSUES.md`. It was deliberately replaced, not patched around.

**The service now holds a server-side session** (`session_store.py`)
in a **new, dedicated Postgres** — never the sibling review-engine project's
own queue DB, never a visitor's own provisioned project — identified by an
`HttpOnly`/`Secure`/`SameSite=Lax` cookie rather than anything tab-scoped.
Every credential value is application-encrypted (Fernet, one opaque blob per
frame) before it's written. See
`docs/superpowers/specs/2026-09-01-onboarding-server-side-session-design.md`
for the full design, including the fork-risk hardening on
`session_store.update_frame()` (it requires an existing session and never
upserts — `create_session()` is the only place a session id is ever minted).
A session's TTL is `session_store.SESSION_TTL` (4 hours as of this writing —
check the constant itself, not this number, if it matters), swept lazily
(no cron): a lookup past its `expires_at` is deleted and treated identically
to a missing session, and `create_session()` sweeps expired rows before
inserting.

What this changes in practice:
- A relay endpoint's *first* submission of a credential still comes from the
  browser (the visitor pastes/uploads it), but the server now persists it
  server-side on success and — for any later step that needs the same
  credential again (e.g. the final deploy frame needing the GitHub key,
  Supabase connection string, and Render key together) — reads it back from
  the session instead of the browser resending it.
- `GET /api/session` (called by the page's `restoreFromSession()` on every
  load) is the source of truth for which frames are already done, not
  `sessionStorage`. It maps session-store frame keys onto the wizard's UI
  frame ids explicitly (not 1:1 — `render` backs three UI frames; `supabase`
  has a genuine "OAuth done, project not yet created" in-between state) and
  never echoes a raw credential back to the browser — only the same class of
  non-secret display fields (an account/org/project name, a URL, an id) this
  file already allowed relay responses to carry.
- A visitor-facing "Start over" control (`POST /api/session/reset`) deletes
  the session and clears the cookie — the explicit, deliberate way to clear
  progress, replacing the implicit "just don't complete a frame" model a
  stateless relay didn't need.

**Two implementation details found missing during a 2026-09-02 correctness
review, fixed the same day (see `ISSUES.md`) — both load-bearing, not
optional:**
- **Every `router.py` endpoint calls `session_store.py` through the
  `_get_session`/`_read_frame`/`_update_frame`/`_create_session`/
  `_delete_session` wrapper functions at the top of `router.py`, never the
  `session_store.*` functions directly.** They exist solely to run the sync,
  real-Postgres-calling `session_store.py` functions via `asyncio.to_thread`
  — calling `session_store.py` directly from an `async def` endpoint blocks
  the single event loop for every other concurrent request for the duration
  of that DB round-trip. A new endpoint that needs the session store uses
  these wrappers, not a fresh direct call.
- **`update_frame(..., replace=True)` fully discards a frame's existing
  content instead of merging — used only by the two endpoints that
  represent "start this frame over" (`validate-key`, `connect`).** A plain
  merge on a resubmitted Render key or a Supabase reconnect would leave the
  *previous* account/project's `service_id`/`ref`/`database_url` sitting in
  the session, which `GET /api/session`'s completeness check (keyed off a
  field's mere presence) could then report as still-done for the wrong
  account. Every endpoint's own docstring/comment says which one it is; a
  new "start over" endpoint for a different frame should use `replace=True`
  too, not assume a plain merge is always safe. Also check every
  `update_frame()`/`_update_frame()` call's return value for
  `SessionNotFound` and report `{"valid": false, "reason": "no_session"}`
  rather than silently reporting success when the write didn't happen — the
  one deliberate exception is `trigger-deploy`'s own write (see its inline
  comment): a real, non-idempotent external side effect already happened by
  that point, so failing the response would only invite a duplicate deploy.

## The cross-repo contract with the review-engine project (2026-09-10)

**`pr-review-bot` owns the schema contract; this wizard owns the row.** That
project declares `runtime_config`/`slot_config`'s shape and every
operational default, backfills any column it can derive from its own
`Settings` defaults, and widens the table itself at boot (`ADD COLUMN IF NOT
EXISTS`, then a `COALESCE` upsert that fills only NULLs and can never
clobber a value this wizard wrote). This wizard writes only what it uniquely
knows -- which provider the visitor chose, which key slot, which model --
and that project refuses to start if *that* is missing. These two arrows
point opposite ways on purpose.

`ISSUES.md`'s 2026-09-09 incident is what it looks like when one side
silently takes over the other's end: this wizard had to create the
`runtime_config` row first (forced by the bot's own provider/slot_config
boot gate), which made it the row's producer while the bot's seeding code
still assumed it was -- 18 of 22 columns NULL forever, and every PR review
on every wizard-provisioned deployment stuck behind a "Dispatcher
configuration issue" comment that never resolved. The mechanism was
row-creation *order*, not the migration of the tuning knobs into the
database.

The mechanical half is `contracts/provisioning.json`, a verbatim copy of a
file `pr-review-bot` generates from its own constants and publishes for
exactly this purpose:

- **Never edit the vendored copy by hand**, and never bump
  `.ci/pr-review-bot-ref` on its own.
  `uv run python -m scripts.update_bot_contract` rewrites **both together or
  neither** -- they are two halves of one fact (which bot contract we are
  built against), and it refuses to write either if
  `tests/test_bot_contract_parity.py` goes red against the extracted copy.
  It resolves `origin/main`, never a local `HEAD` or a feature-branch tip.
- **These are subset/superset checks, never equality checks.** This wizard's
  DDL is deliberately narrower than the bot's declared shape, and the bot's
  boot-time widen-and-backfill is what makes that narrowness harmless. The
  tests assert coverage in both directions that matter -- we write
  everything the bot's boot gate requires, and we push nothing the bot reads
  only from the database -- not identity. An equality assertion across two
  repos cannot be satisfied by either one alone, which is why the earlier
  reciprocal-pin design was discarded.
- **A new hand-maintained duplicate of a bot fact ships with its parity
  assertion in the same commit.** `_LLM_ENV_VAR_NAMES`, `_KEY_INDEX_COLUMNS`
  and `_GENERIC_OPERATIONAL_ENV_DEFAULTS` stay hand-written precisely
  because the vendored contract is what catches a rename in them. A
  duplicate with no assertion is the 2026-09-09 shape all over again.
- **Being briefly behind is normal, not a breakage.** The bot's own CI never
  blocks on this repo; a bot contract change lands there first and a
  scheduled advisory job reports us as lagging until
  `update_bot_contract.py` runs here. Catching up is one commit and is never
  urgent enough to hand-edit either file.

**A docstring that asserts a caller-set invariant ("the only caller always
writes the full pair") is a validation gap waiting for its second caller.**
This has now cost one incident and one live silent-failure path found
before it caused harm, both in `pr-review-bot`'s `store.py`, and
`router.py`'s provisioning writes are the same shape: a single caller today,
prose standing in for a check. Validate in a predicate every writer calls,
not in prose about who calls you.

## Rules

- **Never log a visitor-supplied credential**, in full or truncated — same
  standard as this file's secret-handling section applies to the operator's own secrets, applied
  here to strangers' secrets, which if anything deserves *more* caution
  since these are people who did not choose to trust this codebase with
  their operational hygiene the way the project's own operator has.
- **Every relay endpoint takes a credential in the request body and returns
  a verdict — never the credential itself, never a derived artifact that
  reconstructs it.** A response schema that echoes back anything from the
  request beyond a boolean/enum/short display name (e.g. an account or
  owner name) needs a specific reason, not just convenience.
- **New external-service integrations follow the same relay shape** as the
  Render frame (`render_client.py` / `router.py`'s `/api/render/validate-key`
  pattern): browser holds the token, backend is a stateless pass-through per
  request. Do not special-case a "simple" integration into calling an
  external API directly from browser JS just because it doesn't strictly
  need server-side confidentiality (see design doc section 3 for why).
- **Any new frame involving a cross-origin redirect (OAuth or otherwise)
  must not rely on `sessionStorage` or any other tab-scoped state to survive
  the round trip.** This is the generalized lesson behind the whole
  server-side-session redesign below, not just Supabase-specific history: a
  mobile browser was observed destroying `sessionStorage` (and the
  browsing context holding it) mid-flow, most sharply during a redirect,
  resetting the whole wizard including earlier frames' already-validated
  data. Whatever the redirect's return leg needs must be persisted
  server-side (`session_store.py`) *before* initiating the redirect, and
  `GET /api/session`'s `restoreFromSession()` — never `sessionStorage` — is
  what reconstructs UI state on return or reload. A new frame that adds its
  own redirect leg (there is currently only Supabase's) must follow this
  same pattern from the start, not discover the hard way that it needs to.

## What the implementation adds to these rules

- **The app-wide `RequestValidationError` handler lives in `main.py`,
  not `router.py`.** It's what turns a malformed request into the generic
  `{"detail": "invalid request"}` 422 instead of FastAPI's default response
  (which echoes the rejected input, including a submitted credential). Every
  relay endpoint lives in `router.py` and inherits this protection only
  because `main.py` mounts `router` on the same `app` the handler is
  registered on — a non-obvious cross-file dependency. A new relay endpoint
  added to `router.py` gets this for free; the same router mounted on a
  *different* app (a test harness building its own `FastAPI()`, a future
  split-out service) would lose it silently.
- **A visitor's credential never touches `localStorage`, on the browser
  side too — not just "no database" on the backend.** This page's own
  non-secret theme/language preferences legitimately use `localStorage`
  (they should persist across tabs/sessions); a credential must not, since
  `localStorage` persists past the tab closing. As of the 2026-09-02
  server-side-session redesign, most credentials no longer touch
  `sessionStorage` either — once a frame's endpoint validates a credential,
  it's persisted server-side (`session_store.py`) and never needs to be
  resent, so it never needs local storage at all (the Render API key and
  GitHub App credentials are the clearest examples: `STORAGE_KEYS` has no
  entry for either). Where a frame still keeps a local `sessionStorage`
  mirror (`render-service`, `supabase`, `llm-provider`, `dashboard-auth`,
  `uptime-pinger`), it holds only non-secret continuity/display state for
  the current page view (a url, an id, a provider/model choice, a
  `completed` flag) — never the credential value itself. A new frame that
  needs to remember something locally follows that non-secret-only pattern,
  not the pre-redesign one of mirroring the whole credential.
- **Every credential-carrying `fetch()` on the page has its own
  `..._leaves_the_page_exactly_once` test in
  `tests/test_onboarding_page.py`,** each asserting
  `body.count('fetch("<that endpoint>"') == 1`. This is deliberate: a visitor
  credential should have exactly one, auditable exit path per page load. The
  check was originally a single blanket `body.count("fetch(") == 1`; it was
  narrowed to per-endpoint counts once frame 2 legitimately added a second
  and third relay call, which is the *only* acceptable way to satisfy it —
  loosening a count to `>= 1`, or dropping one, is not. A new frame that
  adds a relay call adds its own such test alongside; a second `fetch()` to
  an endpoint that already has one is the signal to stop and ask why that
  credential now has two exits, not to bump a number.
  For an endpoint called through the shared `callSupabaseRelay(...)` helper
  (sub-project 3's `list-organizations`, `create-project`, `project-status`,
  `connection-info` — see below) rather than a direct `fetch()`, the audit
  target is `body.count('callSupabaseRelay("<that endpoint>"') == 1`
  instead. This is a faithful adaptation of the same one-exit-path
  invariant to the helper's indirection, not a loosening of it: the
  endpoint string must still appear exactly once as the call's own
  argument, wherever in the call chain that argument is spelled.
  `callSupabaseRelay` itself no longer carries `access_token` at all (the
  server reads it from the session cookie) — its callers now pass at most a
  small non-secret payload (e.g. `organization_slug`), never a credential.

## Subsystem-specific rules (routing table)

Each frame/subsystem below has its own accumulated rule set, moved out of
this file so it only loads when actually relevant. **Read the linked doc in
full before making a non-trivial change to that subsystem** — these carry
real incident history (account suspensions, SSRF findings, live-verified API
behavior) at the same priority as anything kept inline here, just not loaded
by default.

- Touching **GitHub App automation** (creation/installation/validation)? Read `docs/subprojects/github-app.md`.
- Touching **Supabase provisioning** (credential/org/project frame)? Read `docs/subprojects/supabase.md`.
- Touching the **LLM provider credential UI** (Gemini/Vertex/Groq)? Read `docs/subprojects/llm-provider.md`.
- Touching the **UptimeRobot keep-warm frame**? Read `docs/subprojects/uptime-pinger.md`.
- Touching **Render service creation/deploy or the Dashboard login frame**? Read `docs/subprojects/render-deploy.md`.

## LLM API testing hygiene (avoid Trust & Safety flags)

This project's `llm_client.py` makes real live calls to Gemini/Groq/Vertex
during credential validation, which carries the same Trust & Safety
abuse-flag risk documented elsewhere for LLM providers' free tiers.

**Rules to avoid triggering it:**

- **Never loop/burst live calls across many models or keys** to "see what
  sticks." One deliberate, single live call per real verification need.
- **Prefer mocked/cassette tests for exploration.** Reserve real network calls
  for the one live-verification step a build step actually requires — not
  for debugging or model-shopping.
- **If a provider starts returning 403/429, stop calling it immediately** and
  investigate via docs/support channels rather than retrying with different
  models/keys in quick succession — retrying does not help and each attempt
  is one more data point that can reinforce an abuse-pattern flag. This
  extends to OAuth/auth-layer failures too (e.g. `invalid_scope`,
  `RefreshError`) — same failure shape, same stop-and-diagnose principle,
  not a "try a different scope/key" situation.
- **The "one deliberate live call" limit is about generation/completion
  requests** — the ones that cost money and carry provider-abuse-flag risk.
  It does **not** apply to lightweight metadata/listing calls (e.g. checking
  whether a model ID exists in a provider's catalog). Checking several
  candidate values via a listing/existence endpoint in one pass is fine, and
  is the right way to narrow down configuration *before* making the one
  deliberate generation call — not a workaround for the rule above.
- This applies to **any** LLM provider's free tier, not just Gemini — Groq and
  future alternatives should get the same restraint.

## Workspace isolation: worktree vs inline

The redaction wrapper (`check_env_access.py` part 2) and the harness's
`EnterWorktree` isolation guard do not compose — every git command in an
`EnterWorktree` session gets refused once the wrapper is active, a bare
`git status` included. **Never use `EnterWorktree` while the wrapper lives.**
A worktree created manually with plain `git worktree add` and used from an
ordinary session runs git freely — the wrapper costs one *tool*, not the
workflow. Full measurement detail and worktree caveats (no `.env`/`.venv`
in a worktree, `ExitWorktree` won't clean up a manual one, never
`EnterWorktree --path` a manual worktree):
`docs/conventions/rationale.md#workspace-isolation-measurement-detail-and-worktree-caveats`.

### Which to use

**Inline -- a plain feature branch in the main checkout -- is the default.**
Use it for single-task changes, and for anything needing real credentials or
the synced venv: running the app locally, `ui-visual-review`, `deploy-verify`.
The existing rule about checking the
*target* branch for pre-existing uncommitted changes before merging binds
harder here, since there is only one working tree to collide in.

**A manual worktree (`git worktree add`, never `EnterWorktree`)** for SDD
plans, genuinely independent parallel tasks, and experiments that may be thrown
away. This path is already sanctioned: the `superpowers:using-git-worktrees`
skill describes itself as working "via native tools *or git worktree
fallback*". The existing rule about writing or committing a plan file *inside*
the worktree still applies — see the next section.

## Plan-execution / multi-agent process hygiene

Lessons from running Superpowers-style plans through subagent-driven
development on this project (see `ISSUES.md` for the incidents these
generalize from, and `docs/conventions/rationale.md#plan-execution--multi-agent-process-hygiene-full-detail`
for full elaboration on each):

- A task brief's "stop and report" instruction is a hard stop, not a suggestion — an implementer must actually stop, not self-resolve and mention the deviation afterward.
- When correcting or overriding part of a multi-sentence passage, re-read the whole passage afterward for internal consistency, not just the changed clause.
- Task-scoped review checks conformance to the brief, not correctness of the brief itself — run the `code-review` skill immediately on any task diff touching external-API/auth integration, don't defer to final review.
- **A credential-accepting endpoint that constructs an auth/HTTP client object from a visitor-supplied structured value (JSON, a config blob) needs an explicit SSRF-focused check as part of its own design/review: does any field in that structure influence which host a server-side request is made to?** This is not covered by "returns a verdict, never the credential" review — the vulnerable field is inert-looking routing metadata sitting next to the credential in the same blob (this is exactly the shape of the `list_vertex_models` SSRF — `token_uri`/`universe_domain` sitting beside the service-account private key, see the sub-project 4 section and `ISSUES.md`); ask this question explicitly whenever a new credential-accepting frame is designed.
- Documentation describing the outcome of a live-verification step must be written after that step actually runs, not drafted in advance assuming success.
- When a plan is authored in the same session that will execute it via a worktree-based flow, write or commit the plan file *inside* the worktree (or commit it before creating the worktree).
- Before merging a feature branch into any target branch, check the *target* branch for pre-existing uncommitted changes first, not just the branch being merged in.
- Don't ask an implementer subagent to reconfirm a full-suite baseline at the start of every task — trust the SDD ledger's last-recorded green state instead, unless there's a concrete reason to distrust it for this task.
- Every parked/deferred Minor finding from a task-scoped or final whole-branch review must be logged in `ISSUES.md`'s Parked Issues section before the branch is considered done — including findings judged "no action needed."
