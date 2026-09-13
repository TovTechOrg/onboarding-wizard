# Vertex project/location selection + early model-entitlement probe

**Date:** 2026-09-12
**Status:** approved, not yet implemented
**Supersedes in part:** the "no project/location collection UI exists in this
wizard (never did)" statement in `CLAUDE.md`'s sub-project 6 section, and
section 7c item 2 of `pr-review-bot`'s
`docs/superpowers/specs/2026-09-11-vertex-model-entitlement-validation-design.md`
(which placed the wizard's only probe at `_seed_provider_config`).

## 1. The problem

Two defects, one root cause.

**The probe is in the wrong place.** `router.py::bulk_push_render_env_vars`
probes the chosen model's entitlement at the *final* "Finish & Deploy" frame
(`router.py:948-958`). The LLM-provider frame — several frames earlier — gates
only on `/api/llm/{provider}/list-models`, an unscoped catalog listing which,
for Vertex, is essentially the global Model Garden and proves nothing about
entitlement. A visitor therefore picks a model, sails through three more
frames, and discovers `model_not_callable` at the very last step, with no
route back except "Change" on an earlier frame.

**The probe answers a question nobody asked.** `probe_vertex_model` is called
with neither `project` nor `location`, so it verifies the *key's own home
project* at the hardcoded `us-central1`. But `_seed_provider_config` writes
`vertex_gcp_project = NULL`, which makes `pr-review-bot`'s `factory.py` derive
the project from the key's embedded `project_id` — so today those two happen
to coincide. They coincide by accident, not by construction: nothing in the
wizard ties the pair that was *probed* to the pair that will be *run*. The
moment a visitor can choose either value, "verified" and "provisioned" are two
different questions unless the choice flows into both.

Fixing the second properly requires collecting the pair, which is the feature
this spec adds; fixing the first is what makes that collection worth anything
to the visitor.

## 2. Locked decisions

| # | Decision | Chosen |
|---|---|---|
| D1 | Probe point | The frame's **Continue** submit (`POST /api/llm/confirm`), one live call per deliberate choice — not per dropdown change |
| D2 | Finish & Deploy probe | **Kept unchanged** as the correctness gate; the new probe is UX |
| D3 | Location options source | **Extend the bot's contract** (`vertex_locations` block, `contract_version` 2 → 3), vendored here — never a hand-copied list |
| D4 | `projects:search` failure | ~~Surface as a frame error, mirroring the bot — not a silent degrade to the key's home project~~ **Reversed 2026-09-13** (see correction below): the bot does not do this — silently degrade to the key's home project, actually mirroring the bot |
| D5 | Location dropdown order | **The contract's curated order**, preserved verbatim — deliberately not sorted |
| D6 | Project dropdown order | Alphabetical, mirroring the model dropdown |

D5 and D6 differ on purpose. `VERTEX_CATALOG_LOCATIONS` is authored grouped by
geography with `global` last; sorting it alphabetically would scatter `global`
between `europe-*` and `northamerica-*` and bury `us-central1`. A project list
carries no such authored meaning, so it sorts by name like every other
dynamically populated dropdown on the page.

**Correction (2026-09-13):** D4's premise was checked against
`pr-review-bot/dashboard/environment.py::_validate_vertex_credential` and found
wrong. That function calls `catalog.list_accessible_projects(info)` and uses
`projects_result.models or []` without ever checking `projects_result.ok` --
a `projects:search` failure (Cloud Resource Manager disabled, or the account
lacking `resourcemanager.projects.get`, both common for a service account
scoped narrowly to Vertex rather than given broad IAM) never affects the
response's own `ok`/`error`, which come solely from the separate
`list_vertex_models` call. The bot silently degrades to offering just the
key's own project; it does not surface an error for this failure at all. D4 as
written did the opposite of what it claimed to do. Reversed in `router.py`'s
`list_vertex_models` endpoint: a `list_accessible_projects` failure on the
first-validate path now falls back to `[result.project_id]` instead of
failing the whole call, actually matching the bot this time.
`err_llm_vertex_projects_unavailable` (and its `vertex_projects_unavailable`
reason mapping) is now dead in `static/index.html` and was removed --
`llm_client.list_accessible_projects` still returns
`LlmApiFailed(reason="vertex_projects_unavailable")` internally, but nothing
propagates it to the browser anymore.

## 3. Security: a location is a hostname

**This is the load-bearing constraint of the whole design, not a detail.**

Vertex's endpoint is `https://{location}-aiplatform.googleapis.com`. A
`location` value submitted by a visitor and passed to `genai.Client(location=...)`
therefore controls the host this server issues an authenticated outbound
request to. That is precisely the vulnerability class already recorded in
`ISSUES.md` for `list_vertex_models`' `token_uri`/`universe_domain`: inert-
looking routing metadata sitting next to a credential, where the credential's
own validity is not the question.

A shape check (`^[a-z0-9-]+$`) is **not** sufficient — `evil-attacker-host` is
a perfectly well-shaped location. The rule is:

> Every `location` value reaching `genai.Client` must be a member of the
> vendored contract's `vertex_locations.options` list. Anything else is
> rejected before any client is constructed, at every endpoint that accepts
> one.

Pinning to an allowlist, rather than validating a pattern, is what makes this
closed by construction: the set of reachable hosts is exactly the set the bot
declares, and it cannot be widened from the wire.

`project` is a path segment, not a host, so it carries no SSRF weight — it is
still pattern-validated (`^[a-z][a-z0-9-]{4,28}[a-z0-9]$`, GCP's own project-id
rule) so a malformed value fails here with a clear verdict rather than as an
opaque Google API error.

## 4. The bot-side half (lands first)

In `~/pr-review-bot`:

1. `scripts/gen_contract.py` gains a `vertex_locations()` block emitting
   `{"default": catalog.DEFAULT_VERTEX_LOCATION, "options":
   catalog.VERTEX_CATALOG_LOCATIONS}`, wired into `build_contract()`.
2. `CONTRACT_VERSION` 2 → 3.
3. The bot's own committed `contracts/provisioning.json` is regenerated and its
   contract tests re-run.

**Ordering constraint:** `scripts/update_bot_contract.py` here resolves
`origin/main`, never a local HEAD or a feature-branch tip. The bot change must
therefore be merged and pushed to the bot's `main` before the wizard can vendor
it. Until then every wizard task below is blocked — this is the one hard
sequencing dependency in the plan.

No hand-editing of the vendored copy, and no bumping `.ci/pr-review-bot-ref` on
its own: `uv run python -m scripts.update_bot_contract` rewrites both or
neither.

## 5. `llm_client.py`

### 5a. `list_vertex_models` gains overrides

```python
async def list_vertex_models(
    service_account_key_b64: str,
    project: str | None = None,
    location: str | None = None,
) -> VertexModelsListed | LlmApiFailed
```

`project` defaults to the key's own `project_id`, `location` to
`_VERTEX_LOCATION` — the current behaviour is exactly the both-None case, so no
existing caller changes meaning. `VertexModelsListed.project_id` reports the
project actually used, not the key's home project, so the caller can never
mistake one for the other.

This mirrors the bot's `catalog.list_vertex_models(info, project_override,
location_override)` one-for-one, including that the listing is re-run per pair:
Model Garden membership is broadly project-independent, but quota and org
policy are not, and the listing is the cheapest thing that reflects the pair.

### 5b. New `list_accessible_projects`

```python
async def list_accessible_projects(
    service_account_key_b64: str,
) -> VertexProjectsListed | LlmApiFailed
```

`VertexProjectsListed` is a new frozen dataclass carrying `projects: list[str]`.

It resolves credentials through the **existing** `_vertex_credentials_and_project`
helper — never its own copy — so the `token_uri`/`universe_domain` SSRF guard is
written once, exactly as that helper's docstring already requires of its two
current callers.

The call is Cloud Resource Manager's `projects:search`
(`https://cloudresourcemanager.googleapis.com/v3/projects:search`) via
`google.auth.transport.requests.AuthorizedSession`. That transport is
`requests`-based and fully synchronous, so the entire call runs under
`asyncio.to_thread` — the same event-loop rule this module already applies to
`creds.refresh`, for the same reason (a single-process server must not stall
every other visitor for a round trip).

Returns the project ids sorted, unioned with the key's own `project_id`. The
union is not cosmetic: a freshly created service account whose IAM binding has
not yet propagated is omitted from its own `projects:search` results, and
without the union the wizard would refuse to offer the one project the
credential certainly works against. (This mirrors the bot's identical union at
`dashboard/environment.py`.)

Failure is reported as a dedicated `vertex_projects_unavailable` reason rather
than a generic `forbidden`, because the cause is specific and actionable — the
Cloud Resource Manager API is not enabled on the project, or the service
account lacks `resourcemanager.projects.get` — and the UI copy can say so.

**`MODEL_PROBE_ERROR_CODES` is not touched.** It is asserted equal to the
contract's `model_validation.error_codes` by
`tests/test_model_validation_conformance.py`; `vertex_projects_unavailable` is a
listing reason, not a probe verdict, and adding it there would fail that test
correctly.

## 6. `router.py`

### 6a. `GET /api/llm/vertex/locations`

Returns `{"locations": [...], "default": "us-central1"}` read from the vendored
contract at import time. No credential, no request body, no session — a static
reference served once per page load, mirroring the bot's
`GET /api/environment/vertex-locations` and its rationale for why this cannot
be a live call.

The list is served in the contract's authored order (D5). The wizard never
templates it into the page: `CLAUDE.md` permits exactly one templated value
(`supabase_oauth_client_id`), and a fetch keeps that rule intact.

### 6b. `POST /api/llm/vertex/list-models` gains the pair

`LlmVertexListModelsRequest` gains optional `project` and `location`.

- **Both absent** — "first validate of a freshly uploaded key". Lists models at
  the key's own project and the default location, **and** runs
  `list_accessible_projects`, returning `projects`, `default_project`,
  `default_location` to populate the dropdowns. `default_location` is the
  same value 6a serves as `default`, read from the same contract field — the
  dropdown's *options* and its *preselection* must never come from two
  independently maintained places.
- **Either present** — "the visitor changed a dropdown". Re-lists models for
  that exact pair only. The projects listing is deliberately **not** repeated:
  doing so would mean a live Cloud Resource Manager call per dropdown change,
  which is the burst pattern `CLAUDE.md`'s LLM-testing-hygiene section forbids.

`location` is validated against the contract allowlist (section 3) before
anything else; `project` against GCP's project-id pattern. A rejected value
returns `{"valid": false, "reason": "invalid_vertex_location"}` /
`"invalid_vertex_project"` and constructs no client.

A `projects:search` failure on the first-validate path fails the whole call
with `vertex_projects_unavailable` (D4) rather than returning a partial
response — the frame cannot offer a project dropdown it could not populate.

### 6c. `POST /api/llm/confirm` probes before it writes

`LlmConfirmRequest` gains `vertex_gcp_project` and `vertex_gcp_location`,
**required when `provider == "vertex"`** and rejected when it is not (a gemini
or groq submission carrying a location is a malformed request, not a field to
ignore). Conditional presence is expressed as a pydantic model validator, since
`Field` alone cannot state "required only for one value of a sibling field"; a
violation therefore surfaces through `main.py`'s app-wide
`RequestValidationError` handler as the generic `{"detail": "invalid request"}`
422, never echoing the submitted body. Both values are validated exactly as in
6b.

The endpoint then, *before* `_update_frame`:

```
vertex → probe_vertex_model(credential_value, model,
                            project=vertex_gcp_project,
                            location=vertex_gcp_location)
gemini → probe_gemini_model(credential_value, model)
groq   → no probe   (contract: required_before_write = false)
failure → {"valid": false, "reason": probe.reason}   # no write happens
```

Probing before the write is the same ordering rule the bulk-push block already
follows, applied one frame earlier: a refused model must never become state. A
rejected model therefore never enters the session, so `GET /api/session` can
never report the frame done with an uncallable model behind it.

On success the session's `llm_provider` frame carries `vertex_gcp_project` and
`vertex_gcp_location` alongside the existing three fields. `GET /api/session`'s
`llm-provider` display block echoes both — they are non-secret configuration
values of exactly the class (`an account/org/project name, a URL, an id`) that
`CLAUDE.md` already permits relay responses to carry.

**This is the pair that gets verified, and it is the same pair that gets
provisioned** — which is the whole point of section 1's second defect.

### 6d. The Finish & Deploy backstop reads the stored pair

`bulk_push_render_env_vars` is otherwise unchanged (D2). Two substitutions:

- its `probe_vertex_model` call passes the session's stored project/location
  instead of nothing;
- `_seed_provider_config` receives `llm_provider["vertex_gcp_project"]` instead
  of the literal `None`, and `llm_provider["vertex_gcp_location"]` instead of
  `llm_client._VERTEX_LOCATION`.

Keeping this probe matters precisely because the earlier one cannot cover
everything: `session_store.SESSION_TTL` is 4 hours, and a visitor who reloads
mid-flow never re-runs `/api/llm/confirm` (`restoreFromSession` marks the frame
done), so a credential revoked or a model retired between the two frames would
otherwise reach `slot_config` unchecked. The contract's own semantic is
`required_before_write`, and the write is the `slot_config` row.

`VERTEX_GCP_PROJECT` and `VERTEX_GCP_LOCATION` remain **DB-only** — neither is
added to `_GENERIC_OPERATIONAL_ENV_DEFAULTS` or pushed as a Render env var.
Nothing in this spec revisits that; it only changes the *values* seeded, from
`(NULL, "us-central1")` to the visitor's verified pair.

A consequence worth stating so it is not later mistaken for a regression:
`vertex_gcp_project` stops being NULL for new provisionings, so
`pr-review-bot`'s `factory.py` will read the seeded value instead of deriving
it from the key's embedded `project_id`. For a visitor who keeps the default
selection those are the same string; for a visitor who picks another project,
the seeded value is the correct one and the derivation would have been wrong.

## 7. `static/index.html`

The Vertex branch of the LLM-provider frame gains two dropdowns between
credential validation and model selection:

```
[ upload service-account JSON ]  → Validate
        ↓  (one projects:search + one model listing)
Project:  [ my-project        ▾ ]   ← alphabetical
Region:   [ us-central1       ▾ ]   ← contract order
Model:    [ gemini-2.5-flash  ▾ ]   ← alphabetical (unchanged)
                                [ Continue ]  ← one probe
```

- Locations are fetched once per page load from `GET /api/llm/vertex/locations`
  and rendered **in the order received**, with a source comment stating that
  this is deliberate and citing the contract, so a future reader does not
  "fix" it into a sort.
- Projects are sorted client-side mirroring `showLlmProviderModels`'
  `sortedModels`, so the page test for it reads identically to the existing
  `test_model_select_options_are_sorted_alphabetically`.
- Changing either dropdown re-lists models for the new pair and clears the
  current model selection — a model verified against one pair says nothing
  about another. This mirrors the bot's `rowVertexBaseline` re-arming rule.
- Both dropdowns and the model section are revealed inside `growFrameToFit`,
  the existing convention for content that grows a frame's height.

**One exit path preserved.** The re-list on a dropdown change and the initial
validate both go through a single shared helper owning the only
`fetch(endpoint, ...)` call site, exactly as `callSupabaseRelay` does for the
four Supabase endpoints. The audit target
`body.count('endpoint = "/api/llm/vertex/list-models"') == 1` continues to hold
literally, and for the right reason — one credential, one exit.

New error strings (English **and** Hebrew — `tests/test_onboarding_i18n.py:62`
enforces key-set parity mechanically):

| key | shown when |
|---|---|
| `err_llm_model_not_callable` | the Continue probe returns `model_not_callable` |
| `err_llm_model_probe_unavailable` | the Continue probe returns `model_probe_unavailable` |
| `err_llm_vertex_projects_unavailable` | `projects:search` failed — names enabling Cloud Resource Manager / granting `resourcemanager.projects.get` |
| `err_llm_invalid_vertex_location` | a location outside the allowlist reached the server |
| `err_llm_invalid_vertex_project` | a malformed project id reached the server |

The first two are frame-local and deliberately **not** reuses of
`err_render_deploy_model_not_callable`, whose text says "Go back to the LLM
provider step" — wrong advice when the visitor is standing on it. The
`err_render_deploy_*` pair stays in place for the backstop's own failures.

Plus labels for the two new dropdowns and their placeholders, in both locales.

Recovery needs no restructuring: `confirmLlmProviderModel`'s `!body.valid`
branch already re-enables Continue, nothing hides the dropdowns, and
`pendingLlmProviderCredential` still holds the credential — so the visitor
picks another model (or another project/region) and submits again, without
re-uploading anything.

## 8. What does not change

- No new `fetch()` beyond the shared helper and the one static locations GET.
- `FRAME_ORDER`, `FRAME_DEPENDENTS`, `prereqsFor`, the relock machinery: the
  probe needs only credential + model + project + location, nothing from
  Supabase or any other frame, so no dependency edge moves.
- Gemini and Groq paths: unchanged apart from gemini's Continue-time probe.
- `_GENERIC_OPERATIONAL_ENV_DEFAULTS`, the Render push, `trigger-deploy`.
- `/api/llm/confirm` keeps its merge write (no `replace=True`). A failed
  re-submit via "Change" therefore leaves the previously confirmed pair in the
  session while the UI shows the frame errored. This is pre-existing behaviour
  shared with every other frame's failed resubmit; whether `confirm` counts as
  a "start this frame over" endpoint under `CLAUDE.md`'s `replace=True` rule is
  a real question, and a separate one. **Logged in `ISSUES.md`'s Parked Issues
  as part of this work**, per the rule that a deferred finding must not live
  only in a session's memory.

## 9. Testing

**`tests/test_onboarding_llm_client.py`** — `list_vertex_models` honours each
override independently and defaults to the key's project/`_VERTEX_LOCATION`
when omitted; `list_accessible_projects` returns sorted ids, unions the key's
own project, and maps a 403 and a transport error to
`vertex_projects_unavailable`. Mocked at the SDK/transport boundary
(monkeypatching `genai.Client` and `AuthorizedSession`), not `respx` — the
established rule for this module, since google-auth's refresh is a synchronous
`requests` call `respx` cannot intercept.

**`tests/test_onboarding_router.py`** —
- a location outside the contract allowlist is rejected at every endpoint that
  accepts one, **and constructs no client** (asserted with a `boom` stub that
  raises if reached) — the SSRF regression test;
- a malformed project id is rejected likewise;
- first-validate returns `projects`/`default_project`/`default_location`; a
  validate carrying either override does **not** repeat the projects listing
  (`boom` stub on `list_accessible_projects`);
- `confirm` probes with the submitted pair, refuses on probe failure, and
  **does not write the frame** on refusal (asserted against the fake session
  store) — per provider: vertex, gemini, and groq-never-probes;
- the existing `test_confirm_llm_provider_persists_to_session` gains a passing
  probe stub, mirroring the bulk-push tests' existing pattern;
- `bulk_push_render_env_vars` probes and seeds with the session's stored pair,
  not `None`/`_VERTEX_LOCATION`.

**`tests/test_onboarding_page.py`** — both dropdowns present; projects sorted
(mirroring the model-sort test); locations explicitly **not** sorted and
rendered in received order; the list-models endpoint string still appears
exactly once; changing a dropdown clears the model selection.

**`tests/test_model_validation_conformance.py`** — `contract_version == 3`.

**`tests/test_onboarding_i18n.py`** — already mechanical; new keys must exist in
both dictionaries or it fails.

**Skills:** `ui-visual-review` is required before calling the work done (real
markup/layout change, and the RTL check matters for two new labelled
dropdowns). `deploy-verify` is required before any push to `main`.

## 10. Explicitly out of scope

- **Gemini/Groq project or region selection** — neither provider has the
  concept.
- **A numbered credential-slot UI.** `slot_index = 0` and
  `{provider}_key_index = 0` stay hardcoded, as `CLAUDE.md` documents.
- **Changing `pr-review-bot`'s dashboard UX.** Its section 6a row-level
  Validate gate already exists and serves the operator, not a visitor.
- **Retroactive re-validation of already-provisioned deployments.** Inherited
  unchanged from the 2026-09-11 design's section 8.
- **The `replace=True` question on `/api/llm/confirm`** — section 8, parked.
