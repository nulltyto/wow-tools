# ellesmereui-pr-check

A [Claude Code](https://claude.com/claude-code) skill that checks
[EllesmereUI](https://github.com/EllesmereGaming/EllesmereUI) changes against
the **Code style** rules in the addon's `.github/CONTRIBUTING.md` before a pull
request goes up.

Those rules are review acceptance criteria — a PR that breaks them gets closed
or bounced. Most of them are mechanically checkable, so they should be checked
mechanically rather than remembered.

## Diff-scoped by design

`check_style.py` checks **only the lines your branch changes**, against the
merge-base with `main`, including uncommitted edits.

That is not a shortcut. The tree carries legacy violations that predate the
rules: 377 non-ASCII lines, 3 `StaticPopup_Show` calls, 3 `W:DualRow` calls
with no right slot, 2 with a placeholder in the left slot. A whole-tree run
reports all of them and teaches you to ignore the output. A diff-scoped run
reports only what you are responsible for, so a finding means something.

`--all` is still there for auditing.

## Rules

| Rule | Severity | Basis |
|---|---|---|
| `lua51` | error | `goto`, `::labels::`, and the 5.2+/5.3+ operators `//`, `&`, `\|`, `~`, `<<`, `>>`. All eight patterns have zero hits tree-wide, so a hit is always real. |
| `ascii` | error | Any byte above 127, with a suggested ASCII replacement. `U+FFFD` is reported separately — that text is already corrupted. |
| `popup` | error | `StaticPopup_Show`; confirmations use `EllesmereUI:ShowConfirmPopup`. |
| `dualrow-nil` | error | Missing or `nil` right slot in `W:DualRow`. |
| `dualrow-left-gap` | error | `{ type = "label", text = "" }` in the left slot — a gap that should be filled left to right. |
| `tooltip` | warning | A `GameTooltip` session (`SetOwner` → `Show`) that only ever receives `SetText`/`AddLine`, with no data setter such as `SetHyperlink` or `SetSpellByID`. Heuristic. |
| `dualrow-empty` | note | Every empty right slot, asking you to confirm it is the last row of its section. Never fails the run. |

Errors fail the run (exit 1). Warnings and notes do not, unless `--strict`.

Deliberate violations are suppressed per line:

```lua
local names = { "windrunner spire", "шпиль ветрокрылых" }  -- eui-style: allow ascii
```

## The rule that is not enforced

"Only the last row of a section may have an empty slot" is not statically
decidable in this codebase. Options rows sit inside `if`/`else` branches, and
local helper functions are defined between them, so "the next row" is not the
same thing as "the next row of this section". Measured against the tree, a
strict section boundary (`W:SectionHeader` only) produced 8 findings of which
at least one was a verified false positive; a generous boundary produced 2,
both false positives.

Rather than ship a rule that cries wolf, the linter emits a note on every empty
right slot and says plainly that the judgment is yours. A linter that is wrong
often enough to ignore is worse than no linter.

## Parsing

Every structural pass runs against a masked copy of each Lua file in which
comment bodies and string contents are blanked to spaces with byte offsets
preserved. That is what keeps `goto` inside a string, a brace inside a comment,
or a `//` inside a URL from being a false positive, while still allowing the
original literal to be read back at any offset — which is how `W:DualRow`
arguments are recovered and compared against the placeholder shape.

`W:DualRow` calls are split into top-level arguments by paren/brace depth over
the masked text, so a call spanning 40 lines with nested closures parses
exactly like a one-liner.

## Attribution

EllesmereUI is by Ellesmere Gaming. This is unofficial third-party tooling; it
contains no addon code. World of Warcraft is a trademark of Blizzard
Entertainment, Inc.
