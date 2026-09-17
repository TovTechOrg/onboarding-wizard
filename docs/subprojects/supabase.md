# Supabase provisioning (sub-project 3)

This doc holds the full, verbatim rule set sub-project 3 added to `CLAUDE.md`'s
core "Rules" section — moved here so it doesn't reload every session
regardless of whether this subsystem is being touched. Read this before
working on the Supabase credential/org/project frame (`supabase_client.py`, `callSupabaseRelay`). Nothing here was rewritten or condensed; it was relocated
as-is.

## What sub-project 3 (Supabase provisioning) adds to these rules

- **The credential is a visitor-pasted Personal Access Token, not an
  OAuth app (2026-09-04 redesign)** — see
  `docs/superpowers/specs/2026-09-04-supabase-pat-frame-design.md`. The
  original design used an operator-registered OAuth app, which made this
  service's one shared credential across every visitor; a follow-up
  brainstorm found the stated reason for that choice ("PAT can't do full
  automation") didn't hold up against Supabase's own docs, so this
  frame now matches every other frame's model (Render, GitHub App, LLM
  provider, UptimeRobot): the visitor supplies their own credential, no
  operator-level Supabase secret exists.
- **`POST /api/supabase/validate-key` does both credential validation and
  org listing in one call** (`supabase_client.validate_key`, one
  `GET /v1/organizations` request) — Supabase has no separate
  token-identity endpoint, so this doubles as both. On success the PAT is
  persisted server-side (`session_store.py`, under the `supabase` frame's
  `api_key` field) via `_update_frame(..., replace=True)` — a resubmitted
  key (via "Change") must discard any previous project's `ref`/`db_pass`/
  `database_url`, same reasoning the Render/GitHub validate-key endpoints
  already document.
- **The project name is captured alongside the org picker, after key
  validation** — not before, since there's no pre-redirect step anymore
  to have captured it earlier (the original OAuth design had the visitor
  type it before authorizing). `create-project`'s request body carries
  both `organization_slug` and `name` now; the session never pre-stores
  `name` on its own.
- **`db_pass` is still generated server-side** (`create-project`,
  `router.py`) — this was already true before this redesign (2026-09-02)
  and is unaffected by the credential swap. It never needs to leave the
  server: `create-project` mints it, passes it directly to
  `supabase_client.create_project()`, and stores it in the session for
  `connection-info` to assemble the final `DATABASE_URL` with later.
- **`connection-info` never returns Supabase's own `connection_string`/
  `connectionString` fields, nor `db_user`/`db_host`/`db_port`/`db_name`
  individually** — unchanged from before this redesign. Since `db_pass`
  already lives server-side, the endpoint assembles the full
  `postgresql://` URL itself and stores it in the session
  (`supabase.database_url`); its response to the browser is just
  `{"valid": true}`.
- **`create-project`, `project-status`, and `connection-info` all read
  `api_key` from the session** (via `session_store.read_frame`), never
  from the request body — set by `validate-key` above. There is no
  client-facing refresh path (there never was one exposed to the
  browser even under OAuth) and nothing to refresh: a PAT doesn't expire
  the way an OAuth access token does.
- **Both Classic and Scoped Personal Access Tokens are accepted
  (2026-09-13) — no server-side gate on token shape.** Supabase's own docs
  recommend scoped tokens "especially [for] AI agents, automation
  scripts, and CI environments" (exactly this wizard's use case), so the
  visitor-facing instructions *prefer* one and name the permissions to
  grant. A hard `sbp_fc`-prefix-only gate was built and then deliberately
  reverted the same day, once verified against Supabase's own docs:
  **scoped tokens are "in public alpha and rolling out gradually"** — an
  account without early access doesn't even get the option to create one,
  which would have made the gate an unactionable dead end for those
  visitors ("create a Scoped token instead" with no way to do so). Classic
  tokens are functionally identical for every call this wizard makes (same
  bearer-auth header, same endpoints, same response shapes) except that
  they can never produce a permission-shaped 403, so accepting both is
  free — `supabase_client.validate_key` makes no distinction at all.
- **A scoped token's 403 means "valid token, missing permission" — a
  reason distinct from 401's "invalid/revoked token", not folded
  together.** `supabase_client.py`'s `validate_key`, `get_project_status`,
  and `get_connection_info` all map 403 to `"insufficient_permissions"`
  (renamed from the old undifferentiated `"forbidden"`) and relay
  Supabase's own `message` field when the body provides one (best-effort;
  no guaranteed structured error body), so the frontend can show the
  visitor which permission to add rather than a generic "access denied".
  `create_project`'s 403-with-a-message still goes through the existing
  `SupabaseProjectRejected` message-relay path unchanged, since a 403
  there is ambiguous between a missing permission and a business-rule
  rejection (e.g. the free-tier project cap) and both already get the
  same "show Supabase's own text" treatment; a 403 with *no* relayable
  message, though, still reports `"insufficient_permissions"` rather than
  degrading to `"supabase_unreachable"` (a bug caught in review — the old
  fallback told the visitor to do the one thing, wait and retry, that
  can never fix a missing permission).
- **The frontend's `insufficient_permissions` display combines our own
  translated copy with Supabase's relayed message** — unlike
  `project_creation_rejected` (100% Supabase's own untranslatable text,
  tracked via a null `currentSupabaseErrorKey`), this case's fixed-copy
  half must still re-render on a language switch. `currentSupabaseErrorKey`
  stays set to the real key and a sibling `currentSupabaseErrorMessage`
  carries the untranslated remainder; `applyLanguage()` recombines both.
  A bug where this case nulled the key (breaking re-translation on
  EN↔HE toggle) was caught by a real-browser Playwright test, not the
  source-substring convention every other page test uses — that
  convention cannot exercise a language-switch re-render at all.
- **The visitor-facing instructions name the exact permissions to grant**
  if creating a scoped token: Organizations (Read), Organization
  Projects (Read-write), Projects (Read), Connection Pooling (Read) —
  matching `validate_key`'s org listing, `create_project`'s project-create
  call, `get_project_status`'s project-by-ref read, and
  `get_connection_info`'s pooler-config read, respectively. These are
  Supabase's current dashboard permission-category labels (as of
  2026-09-14) and could drift if Supabase reorganizes that UI — there is
  no API-level way to verify a token's granted permissions ahead of a call
  that needs them, so this is instructional copy, not a server-side check.
  **Corrected 2026-09-14: `get_project_status` (`GET /v1/projects/{ref}`)
  is gated by the separate account-wide "Projects" category, not
  "Organization Projects"** — a live visitor hit a 403 on this call despite
  having granted every permission this bullet used to list, and both a
  live probe (a real scoped-like token walked through the failing calls)
  and Supabase's own per-endpoint Management API reference pages (each
  documenting a fine-grained permission string; `create_project` requires
  `organization_projects_create` while `get_project` requires
  `project_admin_read` — different prefixes, different dashboard toggles)
  confirmed it. `create_project`/`Organization Projects` and
  `get_connection_info`/`Connection Pooling` were both re-confirmed correct
  in the same pass — only the `get_project_status` attribution was wrong.
  See `docs/superpowers/research/2026-09-14-supabase-scoped-token-permissions.md`
  for the full source trail.
- **A failure during status polling or connection-info must always leave a
  retry path visible, never a silent dead end (2026-09-14 fix).** Reported
  live: a token that passed `validate_key` (has `Organizations: Read`) and
  `create_project` (has `Organization Projects: Read-write`) but lacks the
  permission `get_project_status` or `get_connection_info` needs left the
  visitor stuck on the provisioning section reading an error with no
  button at all — the project had genuinely already been created in
  Supabase by this point. Two distinct bugs combined to cause this:
  `pollUntilReady`/`checkSupabaseStatusOnce` only ever revealed the
  "Check again" button on the pending-timeout path, never on an outright
  failure (`handleProjectStatusResult` returning `"error"`); and
  `handleProjectStatusResult` unconditionally returned `"ready"` once
  status was `ACTIVE_HEALTHY`, regardless of whether the
  `fetchSupabaseConnectionInfo()` call it awaits inside that branch
  actually succeeded — so a connection-info failure stopped polling
  entirely with no error path taken at all. Both are fixed:
  `fetchSupabaseConnectionInfo()` now returns a boolean the caller must
  check, and any `"error"` outcome (not just `"pending"`-timeout) reveals
  "Check again" and stops automatic polling in favor of a manual retry —
  deliberately *not* a reset back to the key-input section, since the
  project already exists and the fix is adding a permission to the *same*
  token in Supabase's dashboard, not pasting a new one (which would also
  risk orphaning this project's `ref`/`db_pass` via `validate-key`'s
  `replace=True`). Caught with real-browser Playwright tests that mock
  the two failure points and drive an actual retry click through to
  `completeFrame` — the source-substring convention every other page test
  uses cannot exercise this kind of multi-step async state transition.
- **Superseded 2026-09-14, same day: the "Check again" retry button above
  is gone. Every recoverable Supabase error now reverts the frame to the
  connect-section (key input), never a bare retry button on the
  provisioning screen.** Root cause of the *design*, not just the bug the
  retry button fixed: a Supabase Personal Access Token's permissions
  cannot be edited after creation -- the visitor must generate a brand-new
  token regardless of which permission was missing, so "just retry the
  same call" can never actually be the fix. Concretely:
  - **`validate-key` gains a permission checklist** (mirroring the GitHub
    App frame's checklist) for the two permissions safely probable before
    any project exists: `Organizations: Read` (implied by getting past
    `GET /v1/organizations` at all) and `Projects: Read` (a new read-only
    `supabase_client.list_projects()` probe, `GET /v1/projects`). A failed
    check blocks the org picker and shows exactly what's missing.
    `Organization Projects: Read-write` (a write permission) and
    `Connection Pooling: Read` (needs a project `ref` that doesn't exist
    yet) can't be preflighted this way -- they still only surface
    reactively, from `create-project` and `connection-info` respectively.
  - **A resubmitted token after one of those reactive failures must not
    orphan the already-created project.** `validate-key`'s
    `preserve_project` request field (default `false`, unchanged default
    behavior for the existing "Change" full-reset flow) makes the
    frontend's error-recovery resubmission merge `api_key` only
    (`replace=False`) instead of wiping
    `ref`/`db_pass`/`organization_slug`/`name` -- the frontend sets it
    whenever `readStoredSupabase().ref` is already present, i.e. a project
    already exists in this session. `showSupabaseOrgSection()` also
    prefills the org dropdown/name input from that same local record so
    the visitor can just click Continue.
  - **`create-project` now checks whether a same-named project already
    exists in the selected org first**
    (`supabase_client.find_org_project_by_name`,
    `GET /v1/organizations/{slug}/projects`) -- this is what makes the
    `preserve_project` resubmission above actually work without
    re-provisioning: if the match is this session's own already-known
    `ref`+`db_pass`, the response reuses it (`{"valid": true, "ref",
    "status", "name"}`, same shape as a fresh creation, transparent to the
    client) instead of calling `create_project` again. A name collision
    this session does *not* own the password for is refused outright
    (`{"valid": false, "reason": "project_name_taken"}`) rather than
    silently adopted -- Supabase's API never returns a project's password
    after creation, so a project whose `db_pass` this session never
    generated is a dead end for building a working `DATABASE_URL`, not
    something to guess at.
  - **Error copy is now fully our own translated strings naming the exact
    permission**, not Supabase's generic relayed 403 message, for all four
    known permission gaps
    (`err_supabase_missing_{organizations,projects,organization_projects,connection_pooling}_permission`)
    -- each frontend call site (`validateSupabaseKey`,
    `kickOffProjectCreation`, `handleProjectStatusResult`,
    `fetchSupabaseConnectionInfo`) passes its own fixed context string to
    `supabaseErrorForReason`'s new third parameter, since only the caller
    knows which Supabase API call actually 403'd. `project_creation_rejected`
    (Supabase's own business-rule rejection text, e.g. a plan-level project
    cap) and the new `project_name_taken` are both token-independent and
    still just stay on the org/name section, unchanged in spirit from the
    superseded design.
  - Reached via back-and-forth brainstorming in-session, not a written
    spec -- a small enough, single-frame change that the bounded path's
    in-chat design was enough.
- **Corrected 2026-09-14, later the same day: the preflight "Projects
  (Read)" checklist item above was itself wrong and has been removed
  entirely** (`supabase_client.list_projects()`, the `permission_checks`
  response field, and the frontend's checklist widget are all deleted --
  `validate-key` now returns only `{valid, orgs}`, same shape as before the
  checklist existed). A visitor hit the checklist-blind "Projects (Read)
  missing" 403 *after* project creation despite the checklist having
  passed at validate-key time, which shouldn't have been reachable if the
  checklist tested the right thing. Live-tested against a real Supabase
  account with two different scoped tokens (never guessed): `GET
  /v1/projects` (the checklist's probe, needing the "Projects
  account-wide: Read" toggle) and `GET /v1/projects/{ref}` (what
  `get_project_status` actually calls) are gated by **two independent,
  separately-toggled permissions with no reliable relationship** -- a
  token with only "Project Settings: Read" granted 403'd on the former but
  succeeded on the latter, and a token adding "Projects account-wide:
  Read" on top changed only the former. This falsifies the same-day
  "Corrected" bullet above in a second way: it's not just that
  `get_project_status` was mis-attributed to "Organization Projects"
  instead of "Projects account-wide" -- "Projects account-wide" was never
  the right category for it at all. The real gate is **"Project Settings:
  Read"**, a project-scoped category with no account-wide list endpoint to
  preflight against before a project exists (the same structural dead end
  "Connection Pooling" already hit) -- confirmed by testing with only that
  one permission and no others, and successfully reading back a live
  project's status. The visitor-facing permission list is now
  **Organizations (Read), Organization Projects (Read-write), Project
  Settings (Read), Connection Pooling (Read)** -- "Projects account-wide"
  is dropped outright, not swapped 1:1, since nothing in this service's
  actual call graph uses `GET /v1/projects` (the account-wide list
  endpoint) once its only caller (the now-deleted checklist probe) is
  gone. The reactive revert-to-connect-section path for this failure
  (`handleProjectStatusResult` -> `supabaseErrorForReason(..., "projects")`)
  needed no changes -- it already existed and already worked correctly;
  only the unsound preflight layer sitting in front of it is gone. Lesson
  for future permission-mapping work in this file: a same-account,
  same-session live test that confirms two calls fail *together* proves
  only that one particular token lacked both permissions being tested --
  it does not prove the two calls share a permission, since a token
  lacking every relevant permission fails everything by construction. Only
  a token with *one* of the two permissions and not the other (tested
  here) can actually distinguish "same gate" from "two gates."

