# Convention rationale (full detail)

This file holds the full incident narrative and measurement detail behind
several short rules stated in `CLAUDE.md`. `CLAUDE.md` keeps only the rule
itself plus a one-line pointer here — read the relevant section below when
you're actually touching the area it covers. This split exists purely to
keep `CLAUDE.md`'s per-session token cost down; nothing here is
lower-priority than what stayed inline.

## Docker image: no `chown -R` (2026-09-07)

`Dockerfile` creates `appuser` and switches to it via `USER appuser` before
`CMD`, but **deliberately never runs `chown -R appuser:appuser /app`** (or
any other recursive chown of the whole app directory). This was a real
convention change, not always the case -- measured live: removing a prior
`RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app` line cut
the built image from 528MB to 398MB (~25%). The reason is mechanical, not
cosmetic: an overlay filesystem stores a changed file as a full copy, not a
diff, so `chown -R` over everything just `COPY`'d/`RUN uv sync`'d in
earlier layers (the whole `.venv` plus the app code) duplicates all of it
into a new layer. **Nothing under `/app` is ever written to at runtime** in
this service -- verified before removing the chown, not assumed -- so
`appuser` only ever needs the read+execute permissions `COPY`/`RUN` already
leave in place by default; there was never a functional reason for the
ownership change to begin with.

**If a future change genuinely needs write access under `/app`** (a cache
file, a local SQLite DB, anything written by the running process rather
than read), re-verify that need is real, then chown *only that specific
path* (e.g. `RUN mkdir -p /app/some-dir && chown appuser:appuser
/app/some-dir`) or use `COPY --chown=appuser:appuser` on just the files
that need it -- never reintroduce a blanket `chown -R /app`, which pays the
full duplication cost again for the entire image regardless of how small
the actual write-needing path is.

`tests/test_dockerfile.py` pins both properties: no live `chown` command
anywhere in the file (comments explaining this tradeoff are fine -- the
check skips comment lines), and `useradd`/`USER appuser` still run in the
right order before `CMD`. It cannot verify the *size* claim (that requires
an actual `docker build`, which the `deploy-verify` skill already does as a
boot smoke test, not a size assertion) -- the test only guards against
someone silently reintroducing the anti-pattern, and its regression comment
carries the measured numbers for anyone re-evaluating this later.

## Hebrew strings: measurement behind the nested-list rule (2026-09-08)

Several Hebrew strings used to embed a chain of untranslated English UI
labels separated by an arrow (e.g. `Account Settings ← API Keys`, or a
four-term GitHub navigation chain) directly inline in RTL prose. This
reads ambiguously to visitors, and the ambiguity is not simply "wrong
arrow direction" -- it was measured directly (via `getBoundingClientRect()`
on each term, not by eyeballing a screenshot): **an embedded LTR run's
on-screen position relative to *other* embedded LTR runs is governed by
the surrounding RTL paragraph's directionality, not by the order the runs
appear in the source string.** Isolating each run (Unicode LRI/PDI,
`⁦`/`⁩`) makes this deterministic across renderers instead of
implementation-dependent, but isolating *reverses* the terms' visual
order -- meaning the arrow direction that reads correctly is the
*opposite* of what feels intuitive, and is easy to get backward (this
session got it backward once before catching it with direct
measurement). Rather than depend on correctly matching arrow direction to
isolation behavior in every future string -- a fragile, easy-to-invert
rule with no compiler or test to catch a future mistake -- **every such
chain is now a real nested `<ol>`/`<li>` list, one term per list item**,
read top-to-bottom with no horizontal-adjacency reversal possible at all.
English keeps its original flowing arrow-chain sentence unchanged (e.g.
`Go to Settings → Developer settings → GitHub Apps → New GitHub App`) --
English never had this problem, since a single LTR paragraph's own runs
don't reorder relative to each other.

**Implementation:** these Hebrew translations contain literal HTML
(`<ol><li>...</li></ol>`), rendered via `el.innerHTML = t(key)` instead of
`el.textContent = t(key)` -- gated by the `HTML_I18N_KEYS` set in
`applyLanguage()`, checked per-key so every other translation keeps using
`textContent` (the safe default). Only trusted, hand-authored translation
strings are ever assigned this way -- never anything visitor-supplied.
The container elements switched from `<p>`/`<span>` to `<div>` (a
paragraph auto-closes around block content like a nested `<ol>`; a
`<span>` isn't guaranteed to accept block children either). One exception:
`err_uptime_unauthorized` is rendered through the same generic
`errorEl.textContent = t(key)` path every other frame's error message
uses, so giving *this one* key markup would require special-casing a
single key inside a shared, generic rendering function used by every
other error message -- disproportionate for one error string. That
Hebrew string is reworded with ordinary prepositions instead (`מתוך
לשונית API Keys בעמוד My Settings`) rather than an arrow chain, sidestepping
the problem via content rather than markup.

## Workspace isolation: measurement detail and worktree caveats

The redaction wrapper (`check_env_access.py` part 2) and the harness's
`EnterWorktree` isolation guard do not compose. The guard refuses any command
naming `git` unless it is a *plain single command*, and a wrapped command can
never be plain -- redaction needs at minimum a redirect and a second statement.
See `docs/superpowers/specs/2026-09-12-redaction-wrapper-worktree-design.md`
and `ISSUES.md`'s 2026-09-11 entry.

**Measured 2026-09-13: that guard is scoped to the `EnterWorktree` *session
mode*, not to the directory being a worktree.** A worktree created with plain
`git worktree add` and used from an ordinary session runs git freely --
verified with both a bare `git status --short` and a piped, multi-statement
`git log --oneline -1 | cat && echo ...`, wrapper active throughout. The
wrapper therefore costs one *tool*, not the workflow.

**Never `EnterWorktree` while the wrapper lives.** Every git command in such a
session is refused, a bare `git status` included, leaving a session that can
edit and test but not commit or merge. That is unusable for anything
SDD-shaped, where the commit boundary arrives once per task rather than once
per branch. If there is reason to think harness behaviour has changed, the
probe is two commands -- `git worktree add <tmp> -b probe/x`, then `cd <tmp> &&
git status --short`. If that runs, this guidance still holds. This repo's
`check_env_access.py` was ported to the sibling repo's file-fed rewrite on
2026-09-13 (see `ISSUES.md`), so the failure mode here now matches the review
engine's rather than being strictly worse: only commands naming `git` are
refused inside an `EnterWorktree` session, not every Bash command.

### Worktree caveats

- **No `.env`, no `.venv`** -- all gitignored, so a worktree
  never materializes them. Consequences: `uv run` builds a fresh venv there
  (time and disk); anything needing real credentials must run in the main
  checkout. Redaction itself is
  unaffected: `check_env_access.py` resolves an absolute `_ENV_PATH` against
  the main checkout and `CLAUDE_PROJECT_DIR` stays pinned there (measured, see
  `ISSUES.md` 2026-09-11).
- **`ExitWorktree` will not clean up a manual worktree** -- by documented
  design it only touches worktrees it created itself. Use `git worktree
  remove` plus `git branch -d`.
- **Never call `EnterWorktree --path` on a manual worktree.** It converts it
  into a guarded session and reintroduces the entire problem.

## Plan-execution / multi-agent process hygiene: full detail

Lessons from running Superpowers-style plans through subagent-driven
development on this project (see `ISSUES.md` for the incidents these
generalize from). `CLAUDE.md` states each rule in one line (except the
SSRF-focused design-review rule, kept in full there); the elaboration
below is why each one exists.

- **A task brief's "stop and report" instruction is a hard stop, not a
  suggestion.** If an implementer hits an unpredicted failure a brief says to
  stop on, it must actually stop and return control — not self-resolve the
  problem and mention the deviation in its report afterward. A controller
  reviewing a report after the fact cannot approve or reject work that has
  already been done; by the time it reads "I deviated because...", the
  deviation has already happened.
- **When correcting or overriding part of a multi-sentence passage, re-read
  the whole passage afterward for internal consistency** — not just the
  clause that was changed. A targeted fix to one sentence is exactly the kind
  of edit that leaves a contradiction elsewhere in the same passage
  undetected by the person who made it.
- **Task-scoped review checks conformance to the brief, not correctness of
  the brief itself.** Code a plan hands an implementer verbatim — especially
  for external-API/auth integration (credential construction, OAuth scopes,
  client setup) — needs the same scrutiny as any other code. Matching the
  brief exactly does not mean the brief was right; a bug embedded in a plan's
  own provided snippet will sail through every task-scoped review that only
  checks "does this match what was asked." **When a task's diff includes this
  class of code, run the `code-review` skill against that diff immediately,
  as part of finishing the task — not deferred to final/whole-branch
  review.** Final review is still a backstop (don't assume a whole-branch
  review is redundant just because per-task reviews already passed — it is
  often the first review that would even think to distrust the plan's own
  code), but it's a backstop, not the primary catch: the `list_vertex_models`
  SSRF shipped and merged before a dedicated security review caught it,
  which is exactly the delay this per-task trigger exists to close.
- **Documentation describing the outcome of a live-verification step must be
  written after that step actually runs, not drafted in advance assuming
  success.** If a plan's task text describes what a doc should say about a
  pending live call's result, treat that text as a placeholder to revise
  based on the actual outcome, not as literal instructions to transcribe.
- **When a plan is authored in the same session that will execute it via a
  worktree-based flow, write or commit the plan file *inside* the worktree**
  (or commit it to the branch before creating the worktree). Writing a file
  to the main checkout and then branching off via `git worktree add` leaves
  that file invisible to the new worktree, since worktrees only materialize
  committed content.
- **Before merging a feature branch into any target branch, check the
  *target* branch for pre-existing uncommitted changes first** (`git status`
  there, not just on the branch being merged in) — a conflicting local edit
  or untracked file on the target can fail the merge in a way that's
  confusing to debug from the merge failure alone.
- **Don't ask an implementer subagent to reconfirm a full-suite baseline at
  the start of every task.** Trust the SDD ledger's last-recorded green
  state from the prior task's own final run instead. The shared
  `subagent-driven-development` skill's implementer template already asks
  for exactly one full-suite run, right before committing — a controller
  adding its own extra "first, confirm baseline" instruction on top of that
  is a habit this project fell into in earlier stages, not something the
  template requires. For a plan's first task, the worktree-setup step that
  precedes dispatch is normally what already confirms things are green, so
  there's usually no real gap to fill even there. Reason: measured directly
  during the 2026-08-19/20 test-suite-performance work — the doubling was
  never principled, and the case for it is weaker still now that the suite
  itself is faster (full suite 57s serial → 35s at `-n 4`; the `-m "not db"`
  fast-iteration subset 31s → 20s — see
  `docs/superpowers/specs/2026-08-19-test-suite-performance-design.md`
  section 8). Only add an explicit baseline-reconfirm instruction when
  there's a concrete reason to distrust the ledger for *this* task
  specifically — manual edits since the last confirmed-green run, a resumed
  session after a long gap, or a worktree/branch switch — not as a default
  precaution on every task.
- **Every parked/deferred Minor finding from a task-scoped or final
  whole-branch review must be logged in `ISSUES.md`'s Parked Issues section
  before the branch is considered done** — not left only in the SDD
  ledger (deleted once the branch merges) or in a session's own memory,
  either of which loses the finding the moment the workspace is cleaned up
  or the conversation ends. Log it there even when a review explicitly
  judges a finding "no action needed" / harmless-as-is — that judgment call
  belongs in the entry's **Why parked** line, not as a reason to skip
  logging it at all.
