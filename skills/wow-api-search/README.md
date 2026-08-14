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

## Blizzard's prose notes are not in the bundled index

The export carries a `Documentation` note on 1,307 entries and fields — one or two sentences of Blizzard's own writing, saying what a nil means or which event a field may be trusted after. The bundled index leaves them out, and its header says so: `"documentation": "omitted"`, with `source_documented` recording how many the export holds.

The reason is redistribution rather than size. Names, signatures, payloads, enum members, flags and the secret markers are facts about an interface, and this repository built its own index of them. The notes are Blizzard's expression, and this repository has no license to republish them. Leaving them in the export they came from costs one command for anyone who wants them:

```bash
python3 scripts/generate_index.py --with-docs
```

That writes `references/api_index.local.json` — the same index with the notes, gitignored, never on top of the committed file. `SKILL.md` tells the agent to prefer the local index when it is present, and to read the note out of the export rather than guess when it is not.



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

`--ensure` (the default) fingerprints the export and rebuilds only when it changed; `--check` reports FRESH/STALE and exits 1 when stale; `--force` always rebuilds. The script finds the clone via `$WOW_UI_SOURCE` or the common paths above, and also accepts the docs directory as an argument. Add `--with-docs` for a local index carrying the prose notes; the notes count as part of what makes an index fresh, so `--check --with-docs` reports the notes-free committed index as stale rather than reusing it.

After regenerating, cross-check the parse:

```bash
python3 scripts/validate_index.py                              # committed index
python3 scripts/validate_index.py --index references/api_index.local.json
```

It re-extracts every entry with an independent brace-depth parser (the generator uses indentation-anchored regexes) and verifies recall, precision, per-entry content, documentation coverage, and header totals. If Blizzard reformats the export, this is what notices. Against an index built without the notes the coverage check reverses: it counts the notes the export holds, then requires that none of them reached the index.

Content and notes are the checks that earn their keep. Name-level recall and precision pass whenever an entry merely exists, so for four builder versions they said nothing while every `Constants` table indexed with no members, 13 `CallbackType` signatures were dropped, and 25 predicate notes went missing — the names were all correct and the counts all matched. The content check compares each entry's arguments, returns, payload and fields against the independent parser, in order; the notes check counts Blizzard's prose per file straight off the source with its own regexes.

## Attribution

The API index is generated from Blizzard Entertainment's exported interface code, sourced via [Gethe/wow-ui-source](https://github.com/Gethe/wow-ui-source). That underlying game data belongs to Blizzard; the Apache-2.0 license here covers the skill and tooling in this repo, not Blizzard's data. The committed index carries the interface facts only, and not Blizzard's prose notes, which stay in the export — see [above](#blizzards-prose-notes-are-not-in-the-bundled-index). World of Warcraft is a trademark of Blizzard Entertainment, Inc. This project is unaffiliated with Blizzard.
