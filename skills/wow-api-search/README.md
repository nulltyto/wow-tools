# wow-api-search

A [Claude Code](https://claude.com/claude-code) skill for searching World of Warcraft's API and Blizzard's exported interface code.

Ask Claude "what arguments does `C_Map.GetPlayerMapPosition` take?", "what are all the values of `Enum.UIMapType`?", or "how does Blizzard's nameplate code handle target changes?" and it looks up the real answer instead of guessing from memory — which matters, because WoW's API churns every patch and LLM training data goes stale fast.

Companion to [ellesmereui-search](../ellesmereui-search/): this one indexes *Blizzard's* API and interface code, that one indexes *EllesmereUI's own* source.

## What's in it

A pre-built JSON index parsed from Blizzard's own `Blizzard_APIDocumentationGenerated` export:

| | Count |
|---|---|
| Functions (with arguments, returns, taint restrictions, preconditions) | 6,144 |
| Events (literal + camelCase names, payloads) | 3,482 |
| Enums and structures (with numeric values and field types) | 1,585 |
| Predicates (precondition failure modes) | 51 |
| API systems | 592 |

The index is bundled and self-contained — signature, event, and enum lookups work with no other setup. When several namespaces define the same unqualified name (262 function names collide, `GetName` and `IsEnabled` among them), the index keeps every entry, not just one.

Function entries include Blizzard's `SecretArguments` taint annotations (`NotAllowed`, `AllowedWhenUntainted`, `AllowedWhenTainted`), which matters when writing code that runs in combat or other protected contexts, and `preconditions` naming the predicates a call requires — each predicate records whether failing it raises an error or silently returns nothing.

Indexed from wow-ui-source `12.0.5`. The index records its own `source_version`, `generated_on` date, and a content fingerprint of the export it was built from, so staleness is always detectable.

## Install

See the [repo README](../../README.md).

## Optional: the interface source

The index alone answers "what does this API do". To also answer "how does Blizzard implement this", clone the interface export:

```bash
git clone --depth 1 https://github.com/Gethe/wow-ui-source
export WOW_UI_SOURCE=/path/to/wow-ui-source   # add to your shell profile
```

Without `WOW_UI_SOURCE` set, the skill looks in the usual places (`~/Repos`, `~/src`, `~/code`, `~/projects`, `~`). Branches: `live` for retail, plus `ptr` and `beta`.

## Updating the index for a new patch

```bash
cd /path/to/wow-ui-source && git pull
python3 scripts/generate_index.py --ensure
```

`--ensure` (the default) fingerprints the export and rebuilds only when it changed; `--check` reports FRESH/STALE and exits 1 when stale; `--force` always rebuilds. The script finds the clone via `$WOW_UI_SOURCE` or the common paths above, and also accepts the docs directory as an argument.

After regenerating, cross-check the parse:

```bash
python3 scripts/validate_index.py
```

It re-extracts every entry with an independent brace-depth parser (the generator uses indentation-anchored regexes) and verifies recall, precision, and header totals. If Blizzard reformats the export, this is what notices.

## Attribution

The API index is generated from Blizzard Entertainment's exported interface code, sourced via [Gethe/wow-ui-source](https://github.com/Gethe/wow-ui-source). That underlying game data belongs to Blizzard; the Apache-2.0 license here covers the skill and tooling in this repo, not Blizzard's data. World of Warcraft is a trademark of Blizzard Entertainment, Inc. This project is unaffiliated with Blizzard.
