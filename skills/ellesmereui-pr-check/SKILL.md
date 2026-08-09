---
name: ellesmereui-pr-check
description: Check EllesmereUI changes against the code style in .github/CONTRIBUTING.md before committing or opening a pull request — Lua 5.1 only, ASCII only, house tooltip and confirmation helpers instead of Blizzard defaults, two-slot W:DualRow options rows, and no code lifted from other addons (ElvUI, Plater, WeakAuras, Bartender4, and ~500 more from the CurseForge top list). Use this skill whenever finishing a change to the EllesmereUI/EUI addon suite, before committing or opening a PR, when the user says "check this before I PR it", "does this follow the style rules", "am I ready to push", when a change names or borrows from another addon, or after writing any options-page widget, tooltip, or confirmation dialog in this codebase. Also use when reviewing someone else's EllesmereUI diff. For finding where code lives in this addon use ellesmereui-search; for Blizzard's own API use wow-api-search.
---

# EllesmereUI PR Check

Run the linter, then hand-check what a linter cannot see. The rules come from
`.github/CONTRIBUTING.md` in the addon; this skill enforces the **Code style**
section mechanically and tells you where mechanical enforcement stops.

## First move

```bash
python3 <skill>/scripts/check_style.py
```

`<skill>` is this skill's own directory — the one holding this file. Use
`python` instead of `python3` on Windows; the scripts need only the standard
library and Python 3.9+.

It checks **only the lines your branch changes** against the merge-base with
`main`, including uncommitted edits. That default matters: the tree carries
legacy violations that predate the rules (377 non-ASCII lines, 3 already
corrupted), so a whole-tree run is noise and a diff-scoped run is a gate.

Exit status is 1 on any error-severity finding. Fix every error before opening
the PR.

```bash
check_style.py --staged             # staged lines only -- what the commit records
check_style.py --base origin/main   # explicit base ref
check_style.py --files a.lua b.lua  # these files, in full
check_style.py --all                # whole tree; expect legacy findings
check_style.py --strict             # warnings fail too
check_style.py --json               # machine-readable
```

Run it from inside the addon checkout, or pass `--root` / set
`$ELLESMEREUI_ROOT`. Given none of those it searches the usual WoW install
locations, the same ones `ellesmereui-search` uses, and prints the checkout it
settled on. An explicit `--root` that misses is an error rather than a reason
to go looking — the wrong tree linted silently is worse than a failed run.

## Catch it at commit time, not at PR time

Nothing here needs to wait for a PR, and a violation is far cheaper to fix
before it is committed than after. Offer to install the pre-commit hook the
first time this skill runs in a checkout that has none:

```bash
python3 <skill>/scripts/check_style.py --install-hook
```

The hook runs `--staged`, so it reads the **indexed blob** rather than the file
on disk — the commit records the index, and the two differ whenever a file was
edited after `git add`. Errors block the commit; warnings and notes print and
let it through. `git commit --no-verify` skips it.

The hook does not replace the pre-PR run. Its scope is one commit; the default
run's scope is the whole branch, so it still catches anything committed before
the hook existed, anything committed with `--no-verify`, and anything a
rebase carried in. Run both.

If a pre-commit hook already exists and is not this one, the installer refuses
to touch it and prints the line to add by hand.

## What it enforces, and how far to trust each rule

| Rule | Severity | Trust |
|---|---|---|
| `lua51` | error | Exact. `goto`, `::labels::`, and the 5.2+/5.3+ operators `//`, `&`, `\|`, `~`, `<<`, `>>`. Zero hits tree-wide, so any hit is real. |
| `ascii` | error | Exact. Any byte above 127. `U+FFFD` is reported separately as already-corrupted text. |
| `popup` | error | Exact. `StaticPopup_Show`. |
| `dualrow-nil` | error | Exact. Missing or `nil` right slot. |
| `dualrow-left-gap` | error | Exact. Placeholder label in the left slot. |
| `thirdparty-credit` | error | Exact on the words. A third-party addon named within 2 lines of unambiguous derivation language (`adapted from`, `taken from`, `credit to`, `ported from`). Zero such pairs exist in the tree, so a hit is new. Softer phrasing (`based on`, `derived from`, `inspired by`) is the same rule at warning severity — the tree has three, all about values rather than provenance. |
| `thirdparty` | warning | Exact on the name. One of ~500 CurseForge addons named in code or a comment. A name is not an accusation: see below. |
| `tooltip` | warning | Heuristic. A `GameTooltip` session (`SetOwner` → `Show`) that only ever gets `SetText`/`AddLine` with no data setter. A rich multi-line tooltip on a Blizzard frame looks identical, so read it before acting. |
| `dualrow-empty` | note | Never fails. See below. |
| `thirdparty-maybe` | note | Never fails. An addon name that is also an ordinary word — Atlas, Cell, Details, Paste, Clique, Pawn. |

Suppress a single line when a violation is deliberate:

```lua
local names = { "windrunner spire", "шпиль ветрокрылых" }  -- eui-style: allow ascii
{ addon = "Clique", label = "Clique" },  -- eui-style: allow thirdparty (conflict registry)
```

Rules take the ids above (`ascii`, `tooltip`, `lua51`, `popup`, `dualrow-nil`,
`dualrow-left-gap`, `dualrow-empty`, `thirdparty`, `thirdparty-credit`,
`thirdparty-maybe`). The comment goes on the offending line or the one above
it. Suppressing `ascii` is legitimate for locale-matching data; suppressing it
for punctuation is not — that is the corruption the rule exists to prevent.

## When another addon is named

EllesmereUI must ship no code taken from another addon. A linter cannot see
that a block was copied — it has no copy to compare against. What it can do is
find every place another addon is named and make you account for it, because
lifted code nearly always arrives with the donor's name still attached: a
credit comment, a "based on" note, a copied identifier, a link to the source.

The tree names plenty of addons legitimately — a conflict registry, compat
shims for FarmHud and Myslot, unit-frame globals it must not fight with — so
293 of these exist already and the diff-scoped default is what keeps the rule
usable. **Resolve every `thirdparty*` finding in your diff before the PR.**

For each one, read the surrounding block and put it in one of three boxes:

**Interop.** `C_AddOns.IsAddOnLoaded("Plater")`, a conflict-registry entry, a
`_G.ElvUF_Player` probe, a frame-name prefix to skip, a user-facing message
naming the conflicting addon. The feature does not work without the name.
Suppress with the reason: `-- eui-style: allow thirdparty (conflict registry)`.

**Coincidence.** Almost always a `thirdparty-maybe` — "Cell reference table",
"Atlas-based styles", "Paste your profile string". Suppress with the reason.

**Provenance.** The name is there because it says where the code came from.
Verify it before anything else happens to this branch:

1. Get the addon's source. `references/addons.json` carries the author, which
   is what makes a GitHub search land — search the author and addon name, or
   open `curseforge.com/wow/addons/<slug>`. Ask the user to fetch it if the
   source is not reachable; do not guess.
2. Compare against the block in the diff. Copied code shows itself in things
   nobody reinvents identically: the same local names in the same order, the
   same magic constants, the same table layout, the same comment wording,
   the same sequence of early returns.
3. If it matches, it does not go in the PR. Rewrite the behaviour from the
   Blizzard API — use `wow-api-search` for the real functions and events — or
   drop the change.
4. If it does not match, say what you compared and suppress with that as the
   reason.

**Never resolve one of these by deleting the comment.** Removing a credit line
does not change where the code came from; it only removes the evidence and
makes the next reviewer's job impossible. If the code is fine, keep the credit
and suppress the rule.

Vendored libraries are a different matter and are already out of scope: `Libs/`
is excluded, and pulling oUF or Ace3 in under their own license is normal
practice. Copying a function *out* of one into EllesmereUI source is not the
same thing, and that is what the rule catches.

The addon list is regenerated from a CurseForge listing pasted to a file:

```bash
python3 <skill>/scripts/build_addon_list.py ~/Documents/curseforge_addons.txt
```

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
Grep pattern="\"key\":\"<settingName>\"" path="<ellesmereui-search>/references/index/settings.jsonl"
```

`<ellesmereui-search>` is that skill's directory; run its `build_index.py
--ensure` first, since the index is a build artifact and is not committed. If
that skill is not installed, grep the `_Options.lua` file directly instead.

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
