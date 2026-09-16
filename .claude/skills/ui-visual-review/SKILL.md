---
name: ui-visual-review
description: Screenshot a web UI change at light-desktop, dark-desktop, and mobile viewports using Playwright, to catch CSS/layout regressions invisible from reading source alone. Use before calling any change to static/index.html done.
---

# UI visual review

Reading HTML/CSS and reasoning about layout is **not a substitute** for
actually rendering the page. The sibling `pr-review-bot` project's own
dashboard hit exactly this failure mode (2026-09-03): a CSS-grid column
forced to ~1700px by one unwrapped child, and a mobile-only bug that
squeezed a value input to invisible near-zero width — both invisible from
source and only caught by rendering and measuring the actual page. This
project's own `static/index.html` carries the same category of risk, plus
RTL layout (see `tests/test_onboarding_i18n.py`) that a passing automated
test can confirm the `dir` attribute flips for without confirming the
*layout itself* actually mirrors correctly.

## When to use

Before marking done any change that touches `static/index.html`'s markup,
CSS, or client-side JS layout logic — including a frame's accordion
open/close state, RTL language switching, or a new form field's styling.
Skip only for changes with no layout/visual surface at all (a pure backend
endpoint change).

## How

1. **Start the local stack** if it isn't already running. This project
   refuses to boot without a reachable `DATABASE_URL` (`main.py`'s own boot
   gate), so use the disposable-Postgres helper shipped alongside this
   skill rather than pointing at the real Supabase database or hand-rolling
   a `docker run` each time:
   ```
   .claude/skills/ui-visual-review/dev_stack.sh start
   ```
   Idempotent — safe to re-run. Tear down afterward with
   `.claude/skills/ui-visual-review/dev_stack.sh stop`.
2. **Run the helper script** shipped alongside this skill:
   ```
   uv run --no-project python .claude/skills/ui-visual-review/screenshot_ui.py <url> <out_dir>
   ```
   This captures three PNGs into `<out_dir>`: `light-desktop.png`,
   `dark-desktop.png`, `mobile.png` (390×844, ~iPhone-width). This page
   needs no auth, so `--cookie` normally isn't needed here.
3. **Read each PNG** (the `Read` tool renders images inline) and check for:
   - A grid/flex column or input forced far wider or narrower than its
     siblings (the grid-blowout shape).
   - An input, button, or accordion frame squeezed to near-zero width or
     overlapping at the mobile viewport.
   - In dark mode: unreadable contrast, or an element that stayed on a
     light-mode color it shouldn't have.
   - **If the change touches i18n/RTL**: switch the language selector
     (or navigate with the language query/localStorage value already set)
     and re-run this script — confirm the layout actually mirrors
     (text alignment, icon/button order, accordion chevrons), not just
     that `dir="rtl"` got set on an element.
4. **Fix and re-run** until all renders look correct. Don't declare the UI
   change done on the strength of the light-desktop screenshot alone.

## Script

`screenshot_ui.py` (this directory) needs nothing beyond this project's
existing `playwright` dependency — no MCP server, no extra install. Kept
byte-identical to the sibling `pr-review-bot` project's copy of the same
script. See its own docstring for the full CLI.
