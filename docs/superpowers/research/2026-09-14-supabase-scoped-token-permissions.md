# Research: Supabase scoped-token permissions for get-project-status and get-connection-info

Date: 2026-09-14

## Summary verdict

**The likely culprit is call #3, `GET /v1/projects/{ref}` (get project / status poll).** Supabase's own Management API reference states this endpoint requires the fine-grained token permission **`project_admin_read`**, whose naming and dashboard grouping put it under a distinct **"Projects" (account-wide)** permission category — separate from **"Organization Projects"**, the category the wizard currently instructs visitors to grant. `create_project` (`POST /v1/projects`) requires `organization_projects_create`, which *is* covered by "Organization Projects: Read-write" — so call #2 succeeding and call #3 (or a later poll of the same endpoint) then 403ing is exactly consistent with a visitor having granted "Organization Projects" but not the separate "Projects" category.

Call #4, `GET /v1/projects/{ref}/config/database/pooler`, requires `database_pooling_config_read`, grouped under "Database" → "Connection Pooling" — this matches the wizard's existing "Connection Pooling (Read)" instruction, so this call is **not** implicated by this naming mismatch (though see the caveat in section 5 about not being able to fully rule it out without a live token).

**Confidence:** High for the "Projects" vs "Organization Projects" distinction (directly stated, verbatim, on Supabase's own reference pages for each endpoint). Not independently reproduced against a live scoped token — see section 5 for what remains unconfirmed.

## 1. Source: dashboard permission category list (`personal-access-tokens` guide)

URL: https://supabase.com/docs/guides/platform/personal-access-tokens

This is the correct primary source for the dashboard's scoped-token permission category names — **not** `/docs/guides/platform/access-control`, which (as fetched) covers organization member roles (Owner/Administrator/Developer/Read-Only) and does not list scoped-token permission categories at all. The handoff doc's apparent reliance on the access-control page's alpha-status framing was directionally right but the page fetched under that URL today does not itself enumerate the categories.

Quoted alpha-status text, confirmed verbatim:

> "Scoped personal access tokens are in **public alpha** and rolling out gradually. If you don't see the option to choose permissions when creating a token, your account doesn't have access yet."

Quoted scope-narrowing note:

> "A scoped personal access token's permissions only ever narrow what your account can already do. They never grant more."

Full permission category list as extracted from the dashboard UI description on this page:

**Project**
- Project Settings, Action Runs, Advisors, Analytics Config, Logs, Usage Analytics, Platform Webhooks

**Database**
- Backups, Database, Database Config, Database JIT, Network Bans, Network Restrictions, Migrations, **Connection Pooling**, Read-only Mode, SSL Enforcement, Database Webhooks

**Application Services**
- API Keys, API Key Secrets, Auth Config, Auth Signing Keys, Data API Config, Data API JWT Secret, Edge Functions, Edge Function Secrets, Realtime Config, Storage, Storage Config, Compute

**Infrastructure and Delivery**
- Development Branches, Production Branches, Custom Domains, Add-ons, Disk Config, Read Replicas, Vanity Subdomain

**Account and Organization**
- Organizations, **Projects (account-wide)**, SQL Snippets (account-wide), Organization Settings, Organization Members, **Organization Projects**, Platform Webhooks (organization)

Each category exposes Read and/or Read-write toggles; the doc doesn't give an endpoint-by-endpoint table on this page itself — it's a dashboard-facing description, not an API-endpoint mapping. That mapping had to come from each endpoint's own reference page (section 2 below).

**Key finding: "Projects (account-wide)" and "Organization Projects" are two distinct, separately-named categories**, both under "Account and Organization". This is the crux of the mismatch — see section 3.

## 2. Source: per-endpoint Management API reference pages (verbatim scope/permission blocks)

Each Management API reference page documents, in a dedicated section, the exact OAuth scope string and the exact fine-grained ("scoped") token permission string required, plus which sidebar category it's filed under. Fetched directly, one endpoint at a time:

| Endpoint | Reference URL | OAuth scope | Fine-grained permission | Sidebar category |
|---|---|---|---|---|
| `GET /v1/organizations` (validate_key / list orgs) | https://supabase.com/docs/reference/api/v1-list-all-organizations | `organizations:read` | `organizations_read` | Organizations |
| `POST /v1/projects` (create_project) | https://supabase.com/docs/reference/api/v1-create-a-project | `projects:write` | `organization_projects_create` | Projects |
| `GET /v1/projects/{ref}` (get_project_status) | https://supabase.com/docs/reference/api/v1-get-project | (not captured — see caveat below) | `project_admin_read` | Projects |
| `GET /v1/projects/{ref}/config/database/pooler` (get_connection_info) | https://supabase.com/docs/reference/api/v1-get-pooler-config | `database:read` | `database_pooling_config_read` | Database |

Note on the "Sidebar category" column: this is the Management API reference's own navigational grouping (used to organize the docs site), which is **not** the same taxonomy as the dashboard's scoped-token permission-category list in section 1 — e.g. both `create_project` and `get_project` are filed under the reference site's "Projects" nav section, but require different fine-grained permission *strings* (`organization_projects_create` vs `project_admin_read`), which is exactly the evidence for two different dashboard toggles.

## 3. The naming mismatch, spelled out

- `organizations_read` → prefix `organizations_` → dashboard category **"Organizations"**. Matches wizard instruction "Organizations (Read)". ✅ Consistent.
- `organization_projects_create` → prefix `organization_projects_` → dashboard category **"Organization Projects"**. Matches wizard instruction "Organization Projects (Read-write)". ✅ Consistent — this is why `create_project` (call #2) succeeded for the visitor who hit the bug.
- `project_admin_read` → prefix `project_admin_` — **does not share the `organization_projects_` prefix**. Given the dashboard's own distinct "Projects (account-wide)" category name (section 1), and that `GET /v1/projects/{ref}` addresses a project directly by ref with no org slug in the path (consistent with "account-wide" framing, since the ref alone doesn't disambiguate which org it belongs to), the most consistent reading is that `project_admin_read` is gated by the **"Projects" (account-wide)** category, not "Organization Projects". This category is **absent** from the wizard's current instructions entirely.
- `database_pooling_config_read` → prefix `database_pooling_config_` → dashboard category **"Connection Pooling"** (listed under "Database" in section 1). Matches wizard instruction "Connection Pooling (Read)". ✅ Consistent — no evidence this call is affected by a naming gap.

This lines up exactly with the reported failure: project creation (needs "Organization Projects") succeeded, but a call reading the project back by ref (needs the separate "Projects" account-wide category) 403'd despite the visitor believing they'd granted every category the wizard told them to.

## 4. Corroborating third-party evidence (not primary, but consistent)

A live, currently-open GitHub issue against `supabase/supabase` describes the same class of problem with a different endpoint pair, reinforcing that scoped-token permission-category coverage has real, reported gaps beyond what any single doc page states:

- https://github.com/supabase/supabase/issues/50244 — "Scoped PAT with Full access gets 403 revealing project API keys; classic PAT succeeds". Reporter used the **Full access preset** (which should grant every category, including "API Keys: Read-write" explicitly shown in the token), yet the fine-grained token still 403'd on the API-keys-reveal call that a Classic PAT succeeds on. Affected both project-scoped and org-scoped PATs. Status: **open, unresolved, labeled bug, no maintainer root-cause comment at time of this research.** This is not proof of *this* wizard's specific gap, but it corroborates that scoped-token permission enforcement has known, currently-unexplained edge cases independent of what the visitor actually granted — i.e., a visitor-side "I definitely checked the right boxes" cannot be fully ruled out as still hitting a Supabase-side bug even after this wizard's instructions are corrected.

This is cited as corroborating, non-primary evidence only, per the task's instruction to prefer primary sources — it is Supabase's own GitHub repo/issue tracker, but it is a user bug report, not documentation.

## 5. Explicit open gaps / what remains unconfirmed

- **The OAuth-scope string for `GET /v1/projects/{ref}` was not captured** in the fetch (the tool call returned scope info for the other three endpoints but the fine-grained-permission fetch for this one only surfaced `project_admin_read` and the sidebar category, not the paired OAuth scope string). This doesn't affect the conclusion (fine-grained/scoped-token permission is the relevant axis here, not OAuth-app scopes, since the wizard uses a visitor-pasted PAT, not an OAuth integration) but is noted as an incomplete extraction rather than a confirmed absence.
- **No Supabase doc page was found that explicitly states "`project_admin_read` maps to the dashboard's 'Projects (account-wide)' toggle"** in so many words — this mapping is inferred from (a) the prefix-matches-category pattern holding for all three other endpoints checked, and (b) the personal-access-tokens page's own listing of "Projects (account-wide)" as a distinct category name. This is a strong inference, not a verbatim-quoted confirmation, and is the one link in the chain most worth verifying against a real scoped token before treating it as fully proven.
- **Not reproduced live.** This research did not create or test an actual Supabase scoped PAT against `GET /v1/projects/{ref}` with only "Organization Projects" (and not "Projects") granted. Per this project's LLM/API testing-hygiene norms and this task's scope (docs research only, no code/live-token changes), that live confirmation is left as a follow-up rather than performed here. If the fix based on this research doesn't resolve the visitor's 403, live testing with a deliberately narrow scoped token is the next step.
- **`access-control` page's actual content** (as fetched today) covers org member roles, not scoped-token permission categories — worth flagging because the task brief anticipated it as the authoritative source (following the 403 error's own link) but it did not contain the category list needed here; `personal-access-tokens` did.
- Whether the GitHub issue (#50244) shares a root cause with this wizard's 403, or is an unrelated Supabase-side bug, is unknown — flagged as corroborating context only, not causally linked.

## 6. Discrepancy vs. this repo's current instructions

- `static/index.html`'s `frame3_instructions` i18n string (English) currently reads: *"...check these permissions when creating it: Organizations (Read), Organization Projects (Read-write), Connection Pooling (Read)."*
- `CLAUDE.md`'s sub-project 3 section lists the identical three permissions and maps them 1:1 to `validate_key` / `create_project`+`get_project_status` (both attributed to "Organization Projects") / `get_connection_info`.
- Based on this research, **`get_project_status` (`GET /v1/projects/{ref}`) is not actually covered by "Organization Projects"** — it needs the separate **"Projects" (account-wide, Read)** category, which is currently **missing entirely** from both the visitor-facing instructions and `CLAUDE.md`'s documented mapping.
- Recommended correction (not applied by this research task): add **"Projects (Read)"** as a fourth permission to `frame3_instructions`, and update `CLAUDE.md`'s sub-project 3 bullet that currently attributes `get_project_status` to "Organization Projects" (NOT CONFIRMED, per the task brief) to instead attribute it to "Projects" (account-wide), with "Organization Projects" left correctly attributed to `create_project` only.

## Citations (all URLs fetched during this research)

- https://supabase.com/docs/guides/platform/access-control
- https://supabase.com/docs/guides/platform/personal-access-tokens
- https://supabase.com/docs/reference/api/introduction
- https://supabase.com/docs/reference/api/v1-list-all-organizations
- https://supabase.com/docs/reference/api/v1-create-a-project
- https://supabase.com/docs/reference/api/v1-get-project
- https://supabase.com/docs/reference/api/v1-get-pooler-config
- https://github.com/supabase/supabase/issues/50244 (corroborating, non-primary)
