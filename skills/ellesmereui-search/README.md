# ellesmereui-search

A [Claude Code](https://claude.com/claude-code) skill for navigating the
[EllesmereUI](https://github.com/EllesmereGaming/EllesmereUI) World of Warcraft
addon suite.

EllesmereUI is ~148 Lua files and ~447k lines across 21 addon modules, with several
single files over 1 MB. Asking Claude "where is `ApplyCastBarTexture` defined",
"what's the default for `absorbCleanAlpha` and which module owns it", or "where does
the options UI for that setting live" means grepping a megabyte of Lua and reading
back a wall of noise. This skill keeps a small, greppable index instead.

Companion to [wow-api-search](../wow-api-search/): that one indexes *Blizzard's*
API and interface code, this one indexes *EllesmereUI's own* source.

## What's indexed

Six JSONL files, one complete record per line, so a single grep returns a whole
record:

| File | Contents |
|---|---|
| `symbols.jsonl` | Every function definition — `ns.Foo`, `EllesmereUI.Foo`, `obj:Method`, locals, globals — with params, module, file, line, and the sites that call it |
| `settings.jsonl` | Every key in every `defaults`/`DEFAULTS` table, with its literal default, dotted path, in-module read sites, and which `_Options.lua` line builds its UI control — plus the suite-wide keys written straight onto `EllesmereUIDB`, which have no defaults table at all |
| `locale.jsonl` | Every `EllesmereUI.L()` / `.Lf()` key with all call sites |
| `events.jsonl` | Every `RegisterEvent` / `RegisterUnitEvent` name with registration sites |
| `slash.jsonl` | Every `SLASH_*` declaration and the command it maps to |
| `modules.jsonl` | Per module: TOC metadata, SavedVariables, `.lua` load order, line counts, slash commands |

Settings come in two flavours because the addon stores them two ways. Per-module
profile keys come from a `defaults` table and carry a literal default. Suite-wide keys
(`profiles`, `unlockAnchors`, `partyMode`, `ppUIScale`, …) are written directly as
`EllesmereUIDB.someKey` with inline `or` / `~= false` fallbacks and have no declared
default anywhere — the `store` field distinguishes them, and `used_by` lists every
module that touches the key.

## Asking it something

One subcommand per question, and the freshness check runs inside each one:

```bash
python3 scripts/query.py def SpecIndexFor            # where a symbol is defined
python3 scripts/query.py callers ApplyCastBarTexture # what breaks if I change it
python3 scripts/query.py setting hideUnusable        # default, declaration, UI control
python3 scripts/query.py label "Hide Unusable"       # bug-report wording -> setting key
python3 scripts/query.py event UNIT_HEALTH           # every registration, suite-wide
python3 scripts/query.py grep Quickdraw              # when the right file is unclear
```

The output states what each answer cannot carry: a list that is capped, a
caller count scoped to one receiver, a match that was a substring rather than
the name asked for. Grepping the JSONL directly still works and returns whole
records.

## Install

See the [repo README](../../README.md). If your addon checkout isn't in a standard
WoW install path, point at it once:

```bash
export ELLESMEREUI_ROOT="/path/to/Interface/AddOns/EllesmereUI"   # shell profile
```

## Keeping it current

The index is a build artifact — gitignored, and rebuilt from your local checkout:

```bash
python3 scripts/build_index.py --ensure
```

`--ensure` hashes the content of every indexed file and rebuilds only when something
actually changed, so it is a no-op when current and ~7.5 seconds when not. It detects
uncommitted working-tree edits, not just commits — which matters, since you are
usually asking about code you just wrote.

The skill runs this itself before every lookup, so in normal use you never invoke it
manually. `--check` reports FRESH/STALE (exit 1 when stale) if you want it in a hook
or CI; `--force` rebuilds unconditionally.

## How the extraction works

Every structural pass runs against a masked copy of each Lua file in which comment
bodies and string contents are blanked to spaces while byte offsets are preserved.
That keeps brace matching and identifier scanning from tripping over braces in
comments or strings, while still allowing the original literal to be read back at any
offset — which is how defaults tables are walked and `["quoted"] =` keys recovered.

Settings references are scoped to the declaring module, because short key names
(`enabled`, `size`, `absorbCleanAlpha`) are declared independently in many modules.
Each record carries a `refs_other_modules` count so a genuinely cross-module read is
still visible.

A defaults table is recognised whole (`local defaults = {`) or one branch at a time
(`defaults.profile.bars[info.key] = {` inside a loop), because a per-entry namespace
is declared once and shared by every entry. A runtime subscript becomes `[]` in the
path, so `bars.[].alwaysShowButtons` reads as "declared once, holds per bar". The
second form matters more than it looks: it holds most of what a bug report is about
(`clickThrough`, `barVisibility`, `alwaysShowButtons`), and matching only the first
form lost all of it while the index still reported 3,876 settings and looked healthy.
A key whose value is a positional table — `gold = { 0.886, 0.675, 0.478 }`, `paging =
{}` — is recorded as its own leaf, since there is no named key below it to record
instead. A colour is the same call for a different reason: `{ r = .., g = .., b = .. }`
does name keys, but they are channels rather than settings. Walking in produced three
records keyed `r`, `g` and `b`, so nothing answered `castbarFillColor`, and each leaf
inherited the references of a one-letter identifier — 787 records, 19% of the index,
every one unfindable and carrying refs belonging to some other `b`.

A bare `Name = function()` is not a global. It is also how a table constructor
holds a handler and how a forward-declared local gets its body, and calling all
three `global` was wrong for 7,243 of the 7,265 records that claimed it. They are
told apart by where the definition sits: inside an open `{` it is a `tablefield`,
and outside one it is the `local` of that name when the file declares one at any
indent — the options files forward-declare inside a builder block and fill the
body thousands of lines below. Position decides before the name does, since a key
inside a constructor is a field of that table whether or not the file also
declares that local. That leaves `global` meaning global — 22 records rather than
7,265, four of which are assignments to a function parameter — which
is what makes the field worth filtering on. A `tablefield` is invoked through
whatever table holds it, a name its definition site does not carry, so those
records say `caller_unresolved` instead of claiming the bare `Name(` calls
elsewhere in the tree.

That reclassification has a price, paid in the one place a declaration and its
body are written apart: `local function CloseSnapMenu() end` up top and
`CloseSnapMenu = function()` further down are one variable, but they are two
records keyed the same way, so the pair reads as two definitions competing for
one name and both say `caller_ambiguity: 2`. 483 records are suppressed this
way, two of which held a correct list before. Collapsing same-name locals per
file is not the fix — `EUI_Quickdraw_Options.lua` declares two genuinely
unrelated nested locals called `Add`, and merging those handed each the other's
callers. Separating the two cases needs block scope, which this builder does
not track.

Call sites are matched on the whole call expression, not the bare name. A method
counts only `owner:Name(`, a field only `owner.Name(`, and a local only calls in
its own file, since one Lua file is one chunk. Bare-name matching would report
6836 callers of `SetPoint` — almost every one of them a Blizzard frame, none of
them the addon's own function. Fields on a module-local table (`ns.Foo`, the
common case) are scoped per module, because every addon folder declares its own
`ns` and two modules' `ns.Foo` are unrelated. What makes a table per-module is
being bound from the addon vararg, not being a local: `local EllesmereUI =
_G.EllesmereUI` is a local too, and reading that as module-private hid every
cross-module call to a suite-wide helper.

A definition is also reached under the names it is aliased to, which in this
codebase is how anything shared is reached at all: a file-local bound onto a
table (`EllesmereUI.ComputeCastBarTint = ComputeCastBarTint`), and often bound
back to a local at the far end (`local MakeBorder = EllesmereUI.MakeBorder`).
Resolving only the definition's own name counted its own file and dropped the
rest of the suite — 333 helpers missing 4,354 call sites, `BuildColorSwatch`
reading 11 against a true 301. That failure is quiet in a way a missing record
is not, because the count stays plausible. The row now carries an `aliases`
field naming the other call expressions, so the number can be checked instead
of believed. An alias claimed by two definitions is dropped rather than guessed.

A name is only global where nothing shadows it. A file-scope `local X` hides a
global `X` for the rest of that chunk, and most such bindings are not function
definitions — `local StartButtonGlow = _G_Glows.StartButtonGlow` opens
EllesmereUINameplates, so the calls below it belong to that and not to the
same-named local in EllesmereUIAuraBuffReminders, which is where they were being
credited. Reading every file's chunk-scope declarations, rather than only the
ones that define a function, removes that class of false edge.

Only shipped code is indexed. Any dotted directory is skipped, not a list of the
ones seen so far: three files under `.tools/` were offline helpers in no TOC, and
their 40 symbols collided by name with EllesmereUIQuickdraw's, pushing 14 real
functions to `caller_ambiguity` with no list at all.

Where a definition cannot be told apart from others called the same way, the
record says so — `caller_ambiguity: N` — and gives no list. That covers 44% of
definitions, nearly all of them AceConfig option callbacks: 1402 functions named
`getValue`, invoked by the config library rather than by name. A caller list
averaged over 1402 candidates would read like an answer while being noise.

`scripts/validate_index.py` checks the built index against the source on three axes that
fail differently: **precision** (every record lands on a line that actually contains
what it claims — a wrong line number is worse than no index), **caps** (every truncated
list carries its true length, so a sample can never be mistaken for a complete answer),
and **recall** (every named function declaration has a record, and every defaults table
in the source produced records — which is how you notice an extractor regex silently
losing coverage after the codebase adopts a new idiom). A clean run asserts 137,000+
record-to-source checks with zero declarations missed.

Recall is the axis that earns its keep, because losing coverage is invisible from the
index alone: a lookup that returns nothing reads exactly like "this key does not
exist". The defaults-table check finds a whole namespace going missing by re-deriving
the tables from source with a line-oriented scan and requiring each one that names a
key to have records. It found the per-bar namespace absent — ~89 keys, including the
subject of an open bug report — from an index that otherwise passed every check. A
sibling check requires every colour inside a defaults table to have a record under its
own name, and no record anywhere to be keyed by a channel.

Caller records are checked on both axes, and the checks are worth the trouble: they
caught `GetFFD(frame).refreshVerticalScroll()` being credited to a same-named local,
because a receiver that is itself a call does not look like a receiver. Precision
rebuilds the expected call expression from each record's own fields — including its
declared aliases, so a caller list can never cite a line that does not mention the
function under some name it claims. Recall re-counts call sites for every file-local,
the one class whose scope is exactly a file, so a second implementation must agree
exactly rather than merely restate the resolution rules; exported locals are the
harder half, and each one has to cite every call made through the name it was
exported under.

## Limitations

The index covers definitions and identifier references. It does not model
`hooksecurefunc` targets, dynamically constructed key names, or table-driven config —
which includes the options rows, so a user-visible label like "Always Show Buttons"
has no record and cannot be looked up. Grep the module's `_Options.lua` for the label;
the row that carries it also carries the key, and the key is what the index answers.
Callers are recorded per definition, but this is not a call graph: a record names
the lines that call a function, not the function that encloses each of those lines,
so it answers "what breaks if I change this" and not "trace this path". Free-text
search (comments, user-facing strings) is still a grep job. The skill says as much,
and falls back accordingly.

## Attribution

EllesmereUI is by Ellesmere Gaming. This is unofficial third-party tooling; it
contains no addon code, only an index built from a local checkout. World of Warcraft
is a trademark of Blizzard Entertainment, Inc.
