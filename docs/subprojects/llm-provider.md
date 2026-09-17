# LLM provider credential UI (sub-project 4)

This doc holds the full, verbatim rule set sub-project 4 added to `CLAUDE.md`'s
core "Rules" section — moved here so it doesn't reload every session
regardless of whether this subsystem is being touched. Read this before
working on the LLM provider frame (`llm_client.py`, Gemini/Vertex/Groq credential validation and model selection). Nothing here was rewritten or condensed; it was relocated
as-is.

## What sub-project 4 (LLM provider credential UI) adds to these rules

- **The model this deployment runs is always fetched live from the
  provider's own catalog, never hardcoded.** `llm_client.py`
  makes exactly one models-listing call per credential submission, which
  doubles as validation. No provider's default/fallback model string may
  be hardcoded anywhere in this service — that is exactly the drift this
  sub-project exists to avoid (the real-world incident this generalizes
  from: a hardcoded `gemini-flash-latest` default string 404-ing against
  Vertex's publisher-model catalog).
- **Gemini and Vertex share one internal helper**
  (`_list_generative_models`) since both go through the same `google-genai`
  SDK and differ only in how `genai.Client` is constructed. A change to the
  filtering/prefix-stripping logic belongs in that shared helper, not
  duplicated per provider. **The `generateContent` filter only applies
  when `supported_actions` is populated** — Vertex's response converter
  (`google/genai/models.py`'s `_Model_from_vertex`) never sets that field
  at all, unlike the Gemini Developer API's converter, so a Vertex model
  is let through rather than dropped when the field is `None`. Filtering
  Vertex strictly on that field (the original implementation) silently
  emptied its entire model catalog for every credential — verify against
  the installed SDK's actual converter functions before changing this
  again, not just against the `Model` type's field list.
- **Groq's model list is deliberately unfiltered** — its `Model` type
  carries no capability field to distinguish chat-completion models from
  Whisper/TTS/moderation ones, and a name-pattern heuristic was
  deliberately rejected as guessing at API behavior this project's
  testing-hygiene discipline warns against. Do not add one without a new
  brainstorm.
- **The frame's unlock gate requires a live-validated credential, an
  explicit model pick, AND a passing live entitlement probe — for vertex,
  also an explicit project/region pick** (2026-09-12, generalizing an
  earlier "credential + model" gate). There is no fallback to any
  baked-in default if the visitor skips picking a model. A credential that
  validates but returns zero eligible models is a genuine dead end under
  this gate; it gets its own distinct error message
  (`err_llm_no_models_available`) rather than folding into a generic
  validation failure.
- **The entitlement probe runs at `POST /api/llm/confirm`, before the
  session write — not only at the final "Finish & Deploy" backstop.**
  `list_vertex_models`/`list_gemini_models` only prove a credential
  authenticates and a model is *listed*; for Vertex that's essentially the
  global Model Garden, not a per-project entitlement list, so a visitor
  used to sail through three more frames before discovering
  `model_not_callable` at the very last step. `confirm_llm_provider`
  (`router.py`) now probes (`probe_vertex_model`/`probe_gemini_model`,
  groq needs none — see `contracts/provisioning.json`'s
  `model_validation` block) *before* `_update_frame`, so a refused model
  never becomes session state and `GET /api/session` can never report the
  frame done behind an uncallable model. The Finish & Deploy probe stays
  in place as the correctness gate underneath it — a reload never re-runs
  `/api/llm/confirm`, and `session_store.SESSION_TTL` is 4 hours, so a
  credential revoked or a model retired between the two frames would
  otherwise reach `slot_config` unchecked. **The pair actually probed at
  `/api/llm/confirm` is the same pair written to the session, and the pair
  the Finish & Deploy backstop later re-probes and seeds** — `router.py`
  refuses with `vertex_pair_missing` rather than silently falling back to
  `None`/`None` if a stale or pre-upgrade session ever reaches the
  backstop with the frame present but the pair absent or no longer in the
  allowlist.
- **A Vertex `location` is part of the outbound hostname
  (`{location}-aiplatform.googleapis.com`), so every submitted value —
  at `GET /api/llm/vertex/locations`'s sibling POST endpoints and at
  `/api/llm/confirm` — is checked against `router._VERTEX_LOCATIONS`, an
  allowlist read from the vendored contract's `vertex_locations.options`,
  never a regex/shape check.** This is the same vulnerability class as the
  `token_uri`/`universe_domain` SSRF finding below: inert-looking routing
  metadata that controls which host this server issues an authenticated
  outbound request to. `project` is pattern-checked
  (`router._valid_vertex_project`) for a clear-verdict reason, not a
  security boundary — a project id is a path segment, not a host.
- **No operator-level settings were added for this sub-project** — unlike
  Supabase's OAuth app, every credential here is visitor-supplied per
  request. `config.py` and `main.py`'s `lifespan` are
  untouched by it.
- **Gemini/Vertex tests mock at the SDK client boundary
  (`google.genai.Client` itself is monkeypatched), not `respx`** — the
  async listing call itself is `httpx`-based for both providers (verified:
  this environment has no `aiohttp` installed, so `google-genai`'s async
  path falls back to `httpx` regardless of auth type). What `respx` alone
  can't cover is Vertex's separate credential step: a service-account
  refreshes its access token via `google.auth`'s synchronous,
  `requests`-based transport before the `httpx` listing call ever happens,
  and `respx` only intercepts `httpx`. SDK-boundary mocking sidesteps
  needing two different mocking libraries for one code path, and covers
  Gemini and Vertex with the same test shape despite their different
  `Client`-construction inputs (`api_key` vs a service-account credential
  object). Groq's tests use `respx` as normal, since its SDK transport is
  pure `httpx` end-to-end with no separate credential-refresh step.
- **All three credentials share one `fetch(endpoint, ...)` call site**
  in `validateLlmProviderCredential()`, with `endpoint` set to a literal
  per-provider URL string in each branch — a second adaptation of the
  one-exit-path convention (alongside `callSupabaseRelay`'s): audited by
  checking each `endpoint = "/api/llm/<provider>/list-models"` assignment
  appears exactly once, plus the shared call site itself appears exactly
  once. A new provider added to this frame follows the same shape, not a
  new dedicated `fetch()` call.
- **The Vertex credential's storage field name
  (`gcp_service_account_key_b64`) deliberately differs from its wire field
  name (`service_account_key_b64`)** — the frame maps between them in
  `showLlmProviderModels`'s caller. Keep this distinction if either name
  changes: the wire name matches the relay endpoint's pydantic field, the
  storage name matches this service's `VERTEX_GCP_SERVICE_ACCOUNT_KEY`-adjacent
  naming convention for sub-project 6 to read later.
- **`list_vertex_models` validates the submitted service-account JSON's
  `token_uri`/`universe_domain` against Google's real values before ever
  constructing credentials from it — this is a load-bearing SSRF guard, not
  a style nit.** `google.oauth2.service_account.Credentials` reads both
  fields verbatim out of the caller-supplied dict and uses them as the
  destination of the token-refresh request it issues later; since the
  visitor also supplies the matching private key, an unpinned value lets
  them redirect this server's own outbound request to an arbitrary host
  (found by a dedicated security review, 2026-08-28 — see `ISSUES.md`).
  Never relax this to "just check the JSON parses" again. The same fix
  proactively refreshes the credential off the event loop via
  `asyncio.to_thread` before the async listing call, since google-auth's
  refresh is synchronous under the hood — remove this and every other
  visitor's concurrent request stalls for the refresh round-trip.

