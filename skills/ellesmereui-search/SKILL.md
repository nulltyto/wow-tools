---
name: ellesmereui-search
description: Search the EllesmereUI World of Warcraft addon suite's own source code — find where a function is defined, which module owns a settings key and what its default is, where a setting is read, which file builds its options UI, where a locale string is used, which module registers an event, or what a slash command maps to. Use it whenever working in the EllesmereUI/EUI codebase (EllesmereUI.lua, EUI_*_Options.lua, and the EllesmereUI* child addons). Triggers on "where is X defined", "what's the default for setting Y", "which module owns Z", "where does the options UI for W live", "which files handle event E", "what calls this function and what breaks if I change it", or any navigation or survey task in this addon. Reach for it first when a bug report, a feature request, or a performance question arrives, before grepping by hand — including when adding something, since a new variant must be wired everywhere the existing one is. This indexes EllesmereUI's own code — for Blizzard's API, use wow-api-search.
---

# EllesmereUI Search

EllesmereUI is ~148 Lua files and ~447k lines across 21 addon modules, with single
files over 1 MB (`EUI_CooldownManager_Options.lua`, `EUI_UnitFrames_Options.lua`).
Grepping that raw is slow and noisy. This skill maintains a greppable index of
definitions, settings keys, locale strings, events, and slash commands.

## First move, every time

Ask the index the question directly:

```bash
python3 <skill>/scripts/query.py def SpecIndexFor
python3 <skill>/scripts/query.py callers ApplyCastBarTexture
python3 <skill>/scripts/query.py setting hideUnusable
python3 <skill>/scripts/query.py label "Hide Unusable Entries"
python3 <skill>/scripts/query.py event UNIT_HEALTH
python3 <skill>/scripts/query.py module EllesmereUIQuickdraw
```

`<skill>` is this skill's own directory — the one holding this file. Use
`python` instead of `python3` on Windows; the scripts need only the standard
library and Python 3.9+.

`query.py` checks index freshness on every run (~0.4s) and rebuilds if the
source moved, so there is no separate build step to remember and no way to
answer from a stale line number. `--json` prints the raw records, `--limit N`
widens a list, `--root` points at a specific checkout. `query.py grep PATTERN`
searches every record file when the right one is not obvious, and
`query.py status` says what the current index was built from.

**Reach for it before grepping the addon tree.** Two full sessions in this
codebase ran 31 and 55 raw greps against the source with the index sitting
current and unread beside them, because a lookup written by hand cost more
than the grep it replaced. A subcommand costs one line.

The output states the caveat that belongs with each record — that a list is
capped, that a receiver-scoped caller count is a floor rather than an answer,
that a match was a substring rather than the key you named. Read those lines:
they are the difference between a finding and a guess.

`build_index.py` is still there for the index itself: `--ensure` (rebuild if
stale), `--check` (report FRESH/STALE, exit 1 if stale), `--force`. It finds
the addon root via `--root`, `$ELLESMEREUI_ROOT`, the path recorded in the last
build, then common WoW install locations. A cold build takes ~7.5 seconds.

## The index

Six JSONL files under `references/index/`, one complete record per line — a single
Grep returns the whole record, no context flags needed. `meta.json` holds the exact
current counts, the git commit it was built from, and the addon root path.

| File | Grep for | Record fields |
|---|---|---|
| `symbols.jsonl` | `"name":"ApplyCastBarTexture"` | `name` `kind` `owner` `full` `params` `module` `file` `line` `aliases` `callers` `caller_count` *or* `caller_ambiguity` *or* `caller_unresolved` |
| `settings.jsonl` | `"key":"absorbCleanAlpha"` | `key` `path` `store` `default` `module` `table` `file` `line` `refs` `ref_count` `options_refs` `options_ref_count` `refs_other_modules` `used_by` |
| `locale.jsonl` | `"key":"Enable Nameplates"` | `key` `count` `sites` |
| `events.jsonl` | `"event":"UNIT_HEALTH"` | `event` `count` `sites` |
| `slash.jsonl` | `"command":"/enp"` | `command` `token` `module` `file` `line` |
| `modules.jsonl` | `"module":"EllesmereUINameplates"` | `module` `folder` `toc` `title` `saved_variables` `load_order` `lua_files` `lines` `slash_commands` |

Look up symbols by their **unqualified** name — `ApplyCastBarTexture`, not
`ns.ApplyCastBarTexture`. `owner` holds the qualifier. The same name is
often defined in several modules — check `module` before opening a file.

`kind` is one of five, and the last two are worth reading before you trust a
caller list:

| `kind` | What it is | Rows |
|---|---|---|
| `field` | `ns.Foo`, `EllesmereUI.Foo` | 2,282 |
| `method` | `obj:Foo` | 470 |
| `local` | a local, whether `local function Foo` or a forward-declared `Foo = function()` filled in later | 7,250 |
| `tablefield` | `Foo = function()` written inside a `{ ... }` constructor | 6,951 |
| `global` | a real global — nothing declares it `local` and it is not in a table | 22 |

`tablefield` is the option-table and handler idiom, and it is the largest class
in the index. Such a function is invoked through whatever table holds it, which
its definition site does not name, so those rows carry
`caller_unresolved: "table field"` and no list rather than claiming the bare
`Foo(` calls elsewhere in the tree. Grep the table for the key.

`global` means what it says, and the class is small because `Foo = function()`
is usually the body of a forward-declared local (`local Build, Rebuild` up top)
rather than a new global. The declaration counts wherever it sits: the big
options files declare inside a builder block and fill the body thousands of
lines below, and reading that as a global would collect bare `Foo(` calls from
the whole tree instead of from its own file.

Position still decides first. A key written inside a `{ ... }` constructor is a
field of that table even when the file also declares a local of that name --
they are two unrelated things that happen to share a spelling.

One case is left, and it is 4 of the 22: a bare assignment to a **function
parameter** (`setValue = function(...)` inside a builder that takes
`setValue`). Telling that apart needs parameter scope, which this builder does
not track. All four are ambiguous by name and so carry no caller list, but read
`global` as "not declared local at file or block level" rather than as proof.

A second case costs a caller list rather than a label. A **forward declaration
and its body are one variable but two definition sites**:

```lua
local function CloseSnapMenu() end   -- the declaration
CloseSnapMenu = function() ... end   -- the body, 300 lines later
```

Both records are `local`, both key on the same file and name, so the pair reads
as two definitions competing for one name and each says `caller_ambiguity: 2`.
Nothing distinguishes it from the case that genuinely is two functions —
`EUI_Quickdraw_Options.lua` declares two unrelated nested locals called `Add` —
without tracking block scope, which this builder does not do. 483 records are
suppressed this way. Two of them, `CloseSnapMenu` and
`RefreshAllImportVisuals`, would otherwise carry a correct list. When a name you
expect to be answered says `caller_ambiguity: 2`, check whether the two sites
are a declaration and its body before treating the answer as unknowable.

## Who calls this — `callers`

Each symbol carries the sites that call it, so "what breaks if I change this
signature" is one grep, not a tree-wide search. A record states **one of three
things, never more than one**:

- **`callers` + `caller_count`** — the call sites, as `file:line`. Capped at 40;
  compare the count against the list, as everywhere else in this index.
- **`caller_ambiguity: N`** — this definition could not be told apart from N−1
  others called the same way, so no list is given. Grep instead.
- **`caller_unresolved: "table field"`** — a `tablefield` row. It is reached
  through the table that holds it, and the definition site does not name that
  table, so no list is given. Grep the key.

`caller_count: 0` with an empty `callers` means nothing calls it under any name
the index resolves. Before calling it dead, remember what the index cannot see
— a handler reached through `hooksecurefunc`, a name assembled at runtime, or a
function stored in a table and invoked from there.

### What the lists cover, and the one gap left

Measured against a real parser: **99.1%** of bare `Foo()` calls are recorded,
and **78.6%** of dotted `owner.Foo()` calls.

The builder resolves a renamed receiver where the rename is syntactic — a table
published on a path (`EllesmereUI.PP = PP`) and a local bound from one
(`local PPc = EllesmereUI and EllesmereUI.PP`), in both directions. So
`PP.ToPixels` lists all eight of its callers and names `PPi.`, `PPc.`, and
`gamePP.` in `aliases`, even though none of them is the recorded `owner`.

What it cannot resolve is a receiver that arrives as **a function parameter**:
one builder takes `ctx` and calls it `barCtx`, and nothing in the text connects
the two. That is most of the remaining 21%.

**Method calls are a separate matter, and the coverage there is thin by
design: about 7% of `X:Foo()` sites.** A method is credited only where the
receiver is literally the `owner` (or `self` inside the defining file), and
most calls instead go through an instance — `btn:`, `frame:`, `bar:`. Widening
that to match on the method name alone would hand `SetPoint` 7,126 callers that
belong to Blizzard frames. So for anything normally called as `obj:Method()`,
read a low `caller_count` as "not measured" rather than "not called", and grep.

So for a **`field` or `method` record**, treat a zero or suspiciously small
count as unproven rather than as an answer. One grep settles it, and it is the
bare name you want — the receiver is the part that varies:

```
grep -rn '[.:]ToPixels(' --include=*.lua . | grep -v '/\.release/'
```

Drop `.release/`: it is the packager's copy of the whole tree, the index skips
it, and it doubles the hits with stale line numbers. Any receiver in the result
other than the record's `owner` is a caller the count is missing.

**`local` and `global` records do not have this problem** — there is no
receiver to rename, and a small count there is the complete answer, as below.

About 44% of definitions are ambiguous, and that is mostly one idiom: the options
files declare thousands of `get = function(info)` / `getValue = function(info)`
callbacks, 1402 sharing a single name. AceConfig invokes those, never addon code,
so the missing list costs nothing.

Attribution follows the receiver, not the bare name. A `method` matches only
`owner:Name(` (plus `self:Name(` inside its own file), a `field` only
`owner.Name(`, and a `local` only its own file, since one file is one Lua chunk.
Without that, `SetPoint` would report 6836 callers, nearly all of them Blizzard
frames. A `field` on a module-local table — `ns.Foo`, the usual case — is scoped
to its own module, because each addon folder has its own `ns`. `EllesmereUI` is
not that: it is one suite-wide table, so `EllesmereUI.Foo` reaches every module.

When the question is "what breaks if I change this", a small `caller_count` on
a `local` or `global` record is the complete answer, not a starting point.
`caller_count: 1` means the blast radius is that one line — read it and you are
done. Reaching for `grep -n` on a 13,000-line file to re-derive that is the
round trip this field exists to remove. On a `field` or `method` record, spend
the one grep above first; a renamed receiver is the difference between a blast
radius of one line and one you have not seen yet.

## Surveying a module, not looking one thing up

An audit — "find the hot paths", "what does this module do on every event" — is
the same index read breadth-first, and it is cheaper than the greps it replaces:

- `events.jsonl` is the registration census. One grep gives every
  `RegisterEvent`/`RegisterUnitEvent` site for an event **across the suite**,
  with a count, so "is this handler registered once or in six places" needs no
  file reading at all.
- `symbols.jsonl` filtered by `"module":"<name>"` enumerates what the module
  defines, with `caller_count` beside each — a cheap first pass at what is
  reachable and what is dead.
- `modules.jsonl` gives load order and line counts, which is how you tell a
  1 MB options file apart from the runtime file worth reading.

Subagents dispatched to audit a file cannot call this skill. Put the absolute
path of `query.py` in their prompt with two example subcommands, or they will
each grep the megabyte again — a review subagent on this codebase spent 50
shell calls and nine minutes re-deriving what `callers` answers in one.

## Second names — `aliases`

This addon exports across modules by binding a file-local onto a shared table:

```lua
local function ComputeCastBarTint(readyTint, baseTint) end
EllesmereUI.ComputeCastBarTint = ComputeCastBarTint
```

Both names are the same function, so `callers` covers both, and `aliases` lists
the other ones. **Read it before you disbelieve a count.** A record for
`ComputeCastBarTint` at `EllesmereUI_Kick.lua:61` citing callers in Nameplates
is not a bad edge — grepping only the definition's own name finds a fraction of
the list, because most of the suite calls it as `EllesmereUI.ComputeCastBarTint`.

A definition with no `aliases` is reached under one name only. Where two
definitions are bound onto the same field, neither gets the edge — a wrong
caller is worse than a missing one — so the alias is dropped, and that field is
worth a grep.

## Settings keys

The highest-value part of the index, and settings reach the SavedVariables in two
different ways. The `store` field says which:

**`store: "defaults"`** — a per-module profile key. The child addon declares a
`defaults`/`DEFAULTS` table, and `EUI_*_Options.lua` reads it via `DBVal("key")` or
`p.key`. `default` holds the literal from that table, `file`/`line` point at the
declaration, and `path` gives the dotted position within it
(`player.absorbCleanAlpha`) with the AceDB `profile.` prefix stripped, since runtime
code reads bare `p.key`.

A colour is **one** setting. `castbarFillColor = { r = 0.863, g = 0.820, b =
0.639 }` is a single record whose `default` is the whole constructor — there
are no `r`/`g`/`b` records to look up, and `"key":"castbarFillColor"` is the
lookup that answers. Colours are about 7% of this addon's settings and most of
what a colour bug report names, so reach for the key you can see in the options
UI rather than the channel.

A `[]` in the path is a key chosen at runtime, so the declaration is shared by
every entry under it. `bars.[].alwaysShowButtons` is declared once and holds
per bar; the `line` points at that single declaration, and the setting exists
independently for bar 1 through bar 10. Most of what a bug report is about
lives under a `[]` — `clickThrough`, `barVisibility`, `mouseoverEnabled` — so
do not read the placeholder as "unresolved".

**`store: "EllesmereUIDB"`** (or another SavedVariables global) — a suite-wide key
written directly as `EllesmereUIDB.someKey`, with inline `or` / `~= false` fallbacks
instead of a defaults table. `profiles`, `unlockAnchors`, `partyMode`, and `ppUIScale`
live here. There is no declared default, so `default` is empty and `file`/`line`
point at the first reference rather than a declaration.

Shared by both:

- `refs` / `ref_count` — read sites; `options_refs` is the subset that builds
  the UI control
- `used_by` — modules that touch the key. Profile keys are module-scoped;
  `EllesmereUIDB` keys are commonly read from several modules at once
- `refs_other_modules` — for profile keys, how many same-named keys exist elsewhere.
  Short names like `enabled` or `size` are declared independently in many modules;
  when this is non-zero and the in-module refs don't explain the behaviour, grep
  globally.

**The options pages are a separate addon.** `EllesmereUIOptions` holds every
`EUI_*_Options.lua` and `EUI_*_ManagerPages.lua`, and each file is named for the
module it configures rather than living beside it. References from a page are
credited to that module, so `options_refs` on a Quickdraw key names lines in
`EUI_Quickdraw_Options.lua`. Attribution stays exact in both directions: a
`size` row inside `EUI_Nameplates_Options.lua` credits Nameplates and nothing
else, which matters because most short key names are declared independently in
half the suite.

## Lists are capped — read the count, not the list

Long lists are truncated so a record stays on one greppable line, and every
capped field ships its true length beside it. **Compare the two before
answering "where is this used".** A list at its cap is a sample, not an answer.

| Field | Cap | True length |
|---|---|---|
| `settings.refs` | 60 | `ref_count` |
| `settings.options_refs` | 10 | `options_ref_count` |
| `symbols.callers` | 40 | `caller_count` |
| `events.sites` | 40 | `count` |
| `locale.sites` | 40 | `count` |

About 17% of settings keys sit at the `options_refs` cap, so this matters most
for "which control builds this setting". When the count exceeds the list, grep
the module's options page for the key rather than trusting the sample.
`query.py` prints both numbers whenever it shows a list.

`symbols.jsonl` and `slash.jsonl` are never truncated.

## Starting from a UI label, not a key

A bug report names what the user sees — "Always Show Buttons" — and the index
is keyed by `alwaysShowButtons`. `locale.jsonl` does **not** bridge the two: it
records `EllesmereUI.L()` call sites, and option labels are plain `text=`
fields in a table-driven options row, so a label lookup there returns nothing.

`query.py label` bridges the two. It scans the options pages for the label and
reports the setting keys within ten lines of it, which is where the row that
carries a label also carries its key:

```
$ python3 <skill>/scripts/query.py label "Hide Unusable Entries"
EllesmereUIOptions/EUI_Quickdraw_Options.lua:3626  { type="toggle", text="Hide Unusable Entries", noCapture=true,
  keys nearby: hideUnusable
  -> query.py setting hideUnusable
```

The report is now a key the index can answer. Do this first. A row with no key
within ten lines is reported as such rather than guessed at — read that row.

## What has no record at all

A key is indexed only if it has a **declaration** — a defaults-table entry, or
a write to a SavedVariables global. A key that is read but never declared has
no record and no default, and the index cannot tell you it exists. There are
about a dozen of these (`absorbAlpha`, `showAllEnemyBuffs`, `targetArrowStyle`,
and similar). An empty result for a key you can see in the source means this,
not a build failure — Grep it and read the fallback the caller supplies
(`p.someKey or 40`), because that inline fallback *is* the effective default.

Before concluding that, check you have the right key: `alwaysShow` and
`alwaysShowButtons` are different settings in different modules, and a partial
name matches the wrong one confidently. Grep the exact `"key":"<name>"`.

## Reading a record

`query.py` is the way to read one: it prints the fields in a fixed order with
the caveats attached, and `--json` gives the raw record when you want every
field. Both avoid the trap below.

Grepping the JSONL by hand still works, and returns whole lines — read them as
they come. Do **not** pipe Grep into a JSON parser: a shell wrapper that
annotates grep output turns every line into invalid JSON, and the decode error
looks like a corrupt index.

## Falling back to Grep

The index covers *definitions and identifier references*. Go straight to Grep for
free text (comments, user-facing strings, error messages), for the call sites of
any symbol carrying `caller_ambiguity`, and for anything the extractors don't
model — `hooksecurefunc` targets, dynamically built key names, table-driven
config, and any function invoked through a table rather than by name.

Treat the index as the fast way to find the right file and line, then read the
source. It is a navigation aid, not a substitute for reading the code.

## Handing off to wow-api-search

Most investigations here stop being about EllesmereUI partway through. The
moment the question becomes what a Blizzard API returns, which field of a
Blizzard structure is readable in combat, what refreshes a Blizzard cache
field, or how Blizzard's own consumers of an API behave, **use
`wow-api-search`** rather than grepping the interface export by hand — it
carries the answers already indexed, including the per-field `never_secret`
markers, and one grep of its index replaces several of the source clone.

The tell is a fix that has to agree with Blizzard's behaviour rather than
merely compile: at that point Blizzard's code is the specification, and
reading it is the cheaper half of the work.

## Rebuilding

`--ensure` (default) rebuilds if stale; `--check` reports FRESH/STALE and exits 1
when stale; `--force` always rebuilds. The index is a build artifact and is
gitignored — it is derived entirely from the addon checkout.

`scripts/validate_index.py` checks the built index against the source: that every
record lands on the line it names, that every cited caller line really calls that
definition through that receiver, that every capped list carries its true length,
and that no named function declaration is missing. Run it after changing an
extractor, or after a refactor big enough to want proof the index still sees
everything.
