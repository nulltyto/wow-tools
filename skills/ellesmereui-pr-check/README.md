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
with no right slot, 2 with a placeholder in the left slot, and 293 lines that
name another addon for legitimate interop. A whole-tree run reports all of them
and teaches you to ignore the output. A diff-scoped run reports only what you
are responsible for, so a finding means something.

`--all` is still there for auditing.

## At commit time

```bash
check_style.py --install-hook
```

installs a `pre-commit` hook that runs `--staged`, so a violation never reaches
a commit in the first place. It reads the indexed blob, not the file on disk —
those differ whenever a file was edited after `git add`, and the commit records
the index. Errors block the commit; warnings and notes print and let it
through. `git commit --no-verify` skips it. An existing foreign hook is never
overwritten.

The hook's scope is one commit, so it does not replace the pre-PR run over the
whole branch: that one still catches whatever was committed before the hook
existed, bypassed with `--no-verify`, or carried in by a rebase.

## Rules

| Rule | Severity | Basis |
|---|---|---|
| `lua51` | error | `goto`, `::labels::`, and the 5.2+/5.3+ operators `//`, `&`, `\|`, `~`, `<<`, `>>`. All eight patterns have zero hits tree-wide, so a hit is always real. |
| `ascii` | error | Any byte above 127, with a suggested ASCII replacement. `U+FFFD` is reported separately — that text is already corrupted. |
| `popup` | error | `StaticPopup_Show`; confirmations use `EllesmereUI:ShowConfirmPopup`. |
| `dualrow-nil` | error | Missing or `nil` right slot in `W:DualRow`. |
| `dualrow-left-gap` | error | `{ type = "label", text = "" }` in the left slot — a gap that should be filled left to right. |
| `thirdparty-credit` | error | A third-party addon named within 2 lines of unambiguous derivation language — `adapted from`, `taken from`, `copied from`, `ported from`, `credit to`, `courtesy of`. Softer wording (`based on`, `derived from`, `inspired by`) is the same rule at warning severity. |
| `thirdparty` | warning | One of ~500 CurseForge addons named in code or a comment. |
| `tooltip` | warning | A `GameTooltip` session (`SetOwner` → `Show`) that only ever receives `SetText`/`AddLine`, with no data setter such as `SetHyperlink` or `SetSpellByID`. Heuristic. |
| `dualrow-empty` | note | Every empty right slot, asking you to confirm it is the last row of its section. Never fails the run. |
| `thirdparty-maybe` | note | An addon name that is also an ordinary word: Atlas, Cell, Details, Paste, Clique, Pawn. Never fails the run. |

Errors fail the run (exit 1). Warnings and notes do not, unless `--strict`.

Deliberate violations are suppressed per line:

```lua
local names = { "windrunner spire", "шпиль ветрокрылых" }  -- eui-style: allow ascii
{ addon = "Clique", label = "Clique" },  -- eui-style: allow thirdparty (conflict registry)
```

## Third-party provenance

No linter can prove code was copied — it has no copy to compare against. This
one finds every place another addon is named, on the observation that lifted
code nearly always arrives with the donor's name still attached: a credit
comment, a `based on` note, a copied identifier, a link to the source. The
finding is a question, and answering it is human work; `SKILL.md` carries the
procedure.

Names come from `references/addons.json`, built by `build_addon_list.py` from a
pasted CurseForge listing plus a supplement for the majors that publish
elsewhere (ElvUI, WeakAuras, Tukui, oUF). Each name is sorted into one of three
tiers, because a flat list of 500 names is unusable against real source:

- **distinctive** — an invented name, or any multi-word phrase. Warning.
  Multi-word phrases are safe: measured over the tree, no generic-sounding
  title (`Edit Mode Expanded`, `Method Raid Tools`) ever matched by accident.
- **ambiguous** — a single ordinary English word, decided against the system
  dictionary. Note only, because `Atlas`, `Cell`, `Routes`, and `Paste` collide
  constantly with normal prose and identifiers.
- **library** — `Ace3`, the `Lib*-x.y` family, `SharedMedia`, `Masque`. Never
  matched; every addon is entitled to use them.

Matching is case-sensitive and word-bounded. Case-insensitive matching on this
list is unusable: it fires on every `local cell` and `atlas` in the tree, and
on the French word *masque* throughout the locale files.

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
