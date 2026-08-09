---
name: ellesmereui-pr-check
description: Check EllesmereUI changes against the code style in .github/CONTRIBUTING.md before opening a pull request — Lua 5.1 only, ASCII only, house tooltip and confirmation helpers instead of Blizzard defaults, and two-slot W:DualRow options rows. Use this skill whenever finishing a change to the EllesmereUI/EUI addon suite, before committing or opening a PR, when the user says "check this before I PR it", "does this follow the style rules", "am I ready to push", or after writing any options-page widget, tooltip, or confirmation dialog in this codebase. Also use when reviewing someone else's EllesmereUI diff. For finding where code lives in this addon use ellesmereui-search; for Blizzard's own API use wow-api-search.
---

# EllesmereUI PR Check

Run the linter, then hand-check what a linter cannot see. The rules come from
`.github/CONTRIBUTING.md` in the addon; this skill enforces the **Code style**
section mechanically and tells you where mechanical enforcement stops.

## First move

```bash
python3 <skill>/scripts/check_style.py
```

It checks **only the lines your branch changes** against the merge-base with
`main`, including uncommitted edits. That default matters: the tree carries
legacy violations that predate the rules (377 non-ASCII lines, 3 already
corrupted), so a whole-tree run is noise and a diff-scoped run is a gate.

Exit status is 1 on any error-severity finding. Fix every error before opening
the PR.

```bash
check_style.py --base origin/main   # explicit base ref
check_style.py --files a.lua b.lua  # these files, in full
check_style.py --all                # whole tree; expect legacy findings
check_style.py --strict             # warnings fail too
check_style.py --json               # machine-readable
```

Run it from inside the addon checkout, or pass `--root` / set
`$ELLESMEREUI_ROOT`.

## What it enforces, and how far to trust each rule

| Rule | Severity | Trust |
|---|---|---|
| `lua51` | error | Exact. `goto`, `::labels::`, and the 5.2+/5.3+ operators `//`, `&`, `\|`, `~`, `<<`, `>>`. Zero hits tree-wide, so any hit is real. |
| `ascii` | error | Exact. Any byte above 127. `U+FFFD` is reported separately as already-corrupted text. |
| `popup` | error | Exact. `StaticPopup_Show`. |
| `dualrow-nil` | error | Exact. Missing or `nil` right slot. |
| `dualrow-left-gap` | error | Exact. Placeholder label in the left slot. |
| `tooltip` | warning | Heuristic. A `GameTooltip` session (`SetOwner` → `Show`) that only ever gets `SetText`/`AddLine` with no data setter. A rich multi-line tooltip on a Blizzard frame looks identical, so read it before acting. |
| `dualrow-empty` | note | Never fails. See below. |

Suppress a single line when a violation is deliberate:

```lua
local names = { "windrunner spire", "шпиль ветрокрылых" }  -- eui-style: allow ascii
```

Rules take the ids above (`ascii`, `tooltip`, `lua51`, `popup`, `dualrow-nil`,
`dualrow-left-gap`, `dualrow-empty`). The comment goes on the offending line or
the one above it. Suppressing `ascii` is legitimate for locale-matching data;
suppressing it for punctuation is not — that is the corruption the rule exists
to prevent.

## What the linter cannot decide

**"Only the last row of a section may have an empty slot."** Not statically
decidable. Rows sit inside `if`/`else` branches and local helper definitions
appear between them, so every boundary rule either misses real violations or
flags correct code — measured on the tree, a strict boundary gave 8 findings
of which at least 1 was wrong, and a generous one gave 2, both wrong. The
linter emits a `note` on every empty right slot and leaves the judgment to
you: look at the row, confirm nothing follows it in the same section.

**"Match the surrounding code."** Before building any options widget — row,
slider, swatch, cog popup — open the nearest existing example in the same file
and copy its shape. Use `ellesmereui-search` to find one:

```
Grep pattern="\"key\":\"<settingName>\"" path="<index>/settings.jsonl"
```

The `options_refs` field points straight at the `_Options.lua` line that builds
the control for a comparable setting. The codebase is consistent on purpose;
a widget that works but reads differently still bounces in review.

## Before you open the PR

The style check is one gate. Two more the repo actually enforces:

**Locale keys.** CI fails the PR when source strings changed and
`Locales/_keys.txt` is stale. It rewrites the file, so run it deliberately:

```bash
bash .tools/extract-locale-keys.sh && git diff --stat -- Locales/_keys.txt
```

Commit the result if it changed.

**The five acceptance criteria** in `.github/CONTRIBUTING.md` — zero cost
unless enabled, zero behavior change without opt-in, low cost when enabled,
zero taint risk, works on both live and PTR clients. These are review
acceptance criteria, not suggestions, and none of them are mechanically
checkable. Walk the PR template checklist honestly before pushing; the taint
one rejects the most PRs.
