---
name: ellesmereui-search
description: Search the EllesmereUI World of Warcraft addon suite's own source code — find where a function is defined, which module owns a settings key and what its default is, where a setting is read, which file builds its options UI, where a locale string is used, which module registers an event, or what a slash command maps to. Use this skill whenever working in the EllesmereUI/EUI codebase (EllesmereUI.lua, EUI_*_Options.lua, EllesmereUINameplates, EllesmereUIUnitFrames, EllesmereUIRaidFrames, EllesmereUICooldownManager, and the other EllesmereUI* child addons). Triggers on questions like "where is X defined", "what's the default for setting Y", "which module owns Z", "where does the options UI for W live", "what reads this profile key", "which files handle event E", or any navigation task in this addon. Reach for it first when a bug report arrives, before grepping the tree by hand. This indexes EllesmereUI's own code — for Blizzard's API, events, and default UI implementation, use wow-api-search instead.
---

# EllesmereUI Search

EllesmereUI is ~137 Lua files and ~400k lines across 20 addon modules, with single
files over 1 MB (`EUI_CooldownManager_Options.lua`, `EUI_UnitFrames_Options.lua`).
Grepping that raw is slow and noisy. This skill maintains a greppable index of
definitions, settings keys, locale strings, events, and slash commands.

## First move, every time

```bash
python3 <skill>/scripts/build_index.py --ensure
```

`<skill>` is this skill's own directory — the one holding this file. Use
`python` instead of `python3` on Windows; the scripts need only the standard
library and Python 3.9+.

It rebuilds only when the source actually changed (content hash of every indexed
file, so it catches uncommitted edits too) and takes ~3 seconds from cold. Running
it costs nothing when the index is current, and skipping it risks acting on stale
line numbers — this codebase changes fast.

The script finds the addon root via `--root`, `$ELLESMEREUI_ROOT`, the path recorded
in the last build, then common WoW install locations.

## The index

Six JSONL files under `references/index/`, one complete record per line — a single
Grep returns the whole record, no context flags needed. `meta.json` holds the exact
current counts, the git commit it was built from, and the addon root path.

| File | Grep for | Record fields |
|---|---|---|
| `symbols.jsonl` | `"name":"ApplyCastBarTexture"` | `name` `kind` `owner` `full` `params` `module` `file` `line` |
| `settings.jsonl` | `"key":"absorbCleanAlpha"` | `key` `path` `store` `default` `module` `table` `file` `line` `refs` `ref_count` `options_refs` `options_ref_count` `refs_other_modules` `used_by` |
| `locale.jsonl` | `"key":"Enable Nameplates"` | `key` `count` `sites` |
| `events.jsonl` | `"event":"UNIT_HEALTH"` | `event` `count` `sites` |
| `slash.jsonl` | `"command":"/enp"` | `command` `token` `module` `file` `line` |
| `modules.jsonl` | `"module":"EllesmereUINameplates"` | `module` `folder` `toc` `title` `saved_variables` `load_order` `lua_files` `lines` `slash_commands` |

Look up symbols by their **unqualified** name — `ApplyCastBarTexture`, not
`ns.ApplyCastBarTexture`. `kind` is `field` (`ns.Foo`, `EllesmereUI.Foo`), `method`
(`obj:Foo`), `local`, or `global`; `owner` holds the qualifier. The same name is
often defined in several modules — check `module` before opening a file.

## Settings keys

The highest-value part of the index, and settings reach the SavedVariables in two
different ways. The `store` field says which:

**`store: "defaults"`** — a per-module profile key. The child addon declares a
`defaults`/`DEFAULTS` table, and `EUI_*_Options.lua` reads it via `DBVal("key")` or
`p.key`. `default` holds the literal from that table, `file`/`line` point at the
declaration, and `path` gives the dotted position within it
(`player.absorbCleanAlpha`) with the AceDB `profile.` prefix stripped, since runtime
code reads bare `p.key`.

**`store: "EllesmereUIDB"`** (or another SavedVariables global) — a suite-wide key
written directly as `EllesmereUIDB.someKey`, with inline `or` / `~= false` fallbacks
instead of a defaults table. `profiles`, `unlockAnchors`, `partyMode`, and `ppUIScale`
live here. There is no declared default, so `default` is empty and `file`/`line`
point at the first reference rather than a declaration.

Shared by both:

- `refs` / `ref_count` — read sites; `options_refs` is the subset in `_Options.lua`,
  i.e. where the UI control lives
- `used_by` — modules that touch the key. Profile keys are module-scoped;
  `EllesmereUIDB` keys are commonly read from several modules at once
- `refs_other_modules` — for profile keys, how many same-named keys exist elsewhere.
  Short names like `enabled` or `size` are declared independently in many modules;
  when this is non-zero and the in-module refs don't explain the behaviour, grep
  globally.

## Lists are capped — read the count, not the list

Long lists are truncated so a record stays on one greppable line, and every
capped field ships its true length beside it. **Compare the two before
answering "where is this used".** A list at its cap is a sample, not an answer.

| Field | Cap | True length |
|---|---|---|
| `settings.refs` | 60 | `ref_count` |
| `settings.options_refs` | 10 | `options_ref_count` |
| `events.sites` | 40 | `count` |
| `locale.sites` | 40 | `count` |

About a third of settings keys sit at the `options_refs` cap, so this matters
most for "which control builds this setting". When the count exceeds the list,
Grep the module's `_Options.lua` for the key rather than trusting the sample.

`symbols.jsonl` and `slash.jsonl` are never truncated.

## What has no record at all

A key is indexed only if it has a **declaration** — a defaults-table entry, or
a write to a SavedVariables global. A key that is read but never declared has
no record and no default, and the index cannot tell you it exists. There are
about a dozen of these (`absorbAlpha`, `showAllEnemyBuffs`, `targetArrowStyle`,
and similar). An empty result for a key you can see in the source means this,
not a build failure — Grep it and read the fallback the caller supplies
(`p.someKey or 40`), because that inline fallback *is* the effective default.

## Falling back to Grep

The index covers *definitions and identifier references*. Go straight to Grep for
free text (comments, user-facing strings, error messages), for call sites of a
function rather than its definition, and for anything the extractors don't model —
`hooksecurefunc` targets, dynamically built key names, table-driven config.

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
record lands on the line it names, that every capped list carries its true length,
and that no named function declaration is missing. Run it after changing an
extractor, or after a refactor big enough to want proof the index still sees
everything.
