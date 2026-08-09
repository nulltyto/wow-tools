# ellesmereui-search

A [Claude Code](https://claude.com/claude-code) skill for navigating the
[EllesmereUI](https://github.com/EllesmereGaming/EllesmereUI) World of Warcraft
addon suite.

EllesmereUI is ~137 Lua files and ~400k lines across 20 addon modules, with several
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
| `symbols.jsonl` | Every function definition — `ns.Foo`, `EllesmereUI.Foo`, `obj:Method`, locals, globals — with params, module, file, line |
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
actually changed, so it is a no-op when current and ~3 seconds when not. It detects
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

`scripts/validate_index.py` checks the built index against the source on three axes that
fail differently: **precision** (every record lands on a line that actually contains
what it claims — a wrong line number is worse than no index), **caps** (every truncated
list carries its true length, so a sample can never be mistaken for a complete answer),
and **recall** (every named function declaration has a record, which is how you notice
an extractor regex silently losing coverage after the codebase adopts a new idiom). A
clean run asserts 90,000+ record-to-source checks with zero named declarations missed.

## Limitations

The index covers definitions and identifier references. It does not model
`hooksecurefunc` targets, dynamically constructed key names, or table-driven config,
and it is not a call graph — it finds where a function is *defined*, not everywhere
it is called. Free-text search (comments, user-facing strings) is still a grep job.
The skill says as much, and falls back accordingly.

## Attribution

EllesmereUI is by Ellesmere Gaming. This is unofficial third-party tooling; it
contains no addon code, only an index built from a local checkout. World of Warcraft
is a trademark of Blizzard Entertainment, Inc.
