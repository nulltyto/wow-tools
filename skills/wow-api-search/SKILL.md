---
name: wow-api-search
description: Search a local export of Blizzard's World of Warcraft interface code for API functions, events, enums, structures, frame templates, mixins, and UI implementation details. Use this skill whenever the user asks about WoW API functions, events, frame XML, UI templates, mixin implementations, or how Blizzard implements any part of the default UI. Also use when writing WoW addon code and you need to look up how a specific API works, what arguments a function takes, what events are available, what values an enum has, or how Blizzard's own code handles something. Triggers on questions like "what args does X take", "how does Blizzard do Y", "find the event for Z", "what mixin handles W", or any reference to searching Blizzard's exported interface code.
---

# WoW API Search

This skill bundles a pre-built JSON index of every documented WoW API function, event, enum, and structure, and (optionally) searches a local clone of Blizzard's exported retail interface code.

## Paths

`<skill>` below is this skill's own directory — the one holding this file. Run
the scripts with `python3`, or `python` on Windows; they need only the standard
library and Python 3.9+.

**Index** (bundled, works standalone): `references/api_index.json` under this skill's base directory. It answers signature/payload/enum questions without any other setup.

**Source code** (optional, needed only for implementation details): a clone of [Gethe/wow-ui-source](https://github.com/Gethe/wow-ui-source). Resolve the base path in this order:

1. `$WOW_UI_SOURCE` environment variable
2. Common locations: `~/Repos/wow-ui-source`, `~/repos/…`, `~/src/…`, `~/code/…`, `~/Projects/…`, `~/projects/…`, `~/dev/…`, `~/wow-ui-source`
3. The `generated_from` field at the top of the index (the path used at index build time)

The interface code lives at `<clone>/Interface/AddOns/` — 300+ `Blizzard_*` addon folders. If no clone exists and the question needs implementation code, offer to clone it: `git clone --depth 1 https://github.com/Gethe/wow-ui-source` (the `live` branch tracks retail; `ptr`/`beta` branches exist too).

## Search Strategy

Always search before reading. Start narrow, broaden only if needed.

### For signatures, event payloads, enums, structures — use the index first

The index stores one complete entry per line, so a single exact-name Grep returns everything — no context flags needed:

```
Grep pattern="\"GetMapInfo\":" path="<index_path>" output_mode="content"
```

Lookup keys:
- **Functions**: unqualified name (`GetMapInfo`, not `C_Map.GetMapInfo`)
- **Events**: literal name (`UNIT_HEALTH`) or camelCase name (`UnitHealth`) — both indexed
- **Tables**: enum, structure, or constants name (`UiMapDetails`, `AddOnProfilerMetric`)

Entry structure:
- **Functions**: `{ system, namespace, qualified_name, file, arguments: [{name, type, nilable, default?}], returns: [...], secret_arguments?, preconditions? }`
- **Events**: `{ system, file, literal_name, name, payload: [{name, type, nilable}] }`
- **Tables**: `{ system, file, type: Structure|Enumeration|Constants, fields: [{name, type, nilable?, enum_value?}] }`
- **Predicates**: `{ system, file, failure_mode }` — under the `predicates` section

When the same unqualified name exists in several namespaces (`GetName`, `IsEnabled`, ...), the value is an **array** of entries instead of a single object — check `namespace`/`qualified_name` to pick the right one. The one-grep-returns-everything property still holds.

`secret_arguments` marks taint restrictions: `NotAllowed` (rejects secret values), `AllowedWhenUntainted`, or `AllowedWhenTainted`. Worth flagging when the user is writing combat- or protected-context code.

`preconditions` lists the predicate names a function requires (e.g. `RequiresNonReadOnlyCVar`). Grep the predicate name in the `predicates` section for its `failure_mode`: `Error` means the call raises, `ReturnNothing` means it silently returns nothing — useful when explaining why an API "doesn't work".

For partial-name or fuzzy searches, grep the index without the quotes/colon anchor (`pattern="MapInfo"`). If you need more than the index provides (full system docs, related types), the `file` field names the source file in `Blizzard_APIDocumentationGenerated/`.

### For UI implementation details — use targeted Grep on the source clone

When looking for how Blizzard implements something (mixins, frame setup, event handling), search the relevant addon folder:

1. **If you know the addon**: Grep directly in that folder
   ```
   Grep pattern="MixinName" path="<base>/Blizzard_NamePlates/"
   ```

2. **If you don't know where it lives**: Search across all addons with `files_with_matches` first
   ```
   Grep pattern="SearchTerm" path="<base>/" output_mode="files_with_matches"
   ```

3. **For XML templates/frames**: Filter to XML files
   ```
   Grep pattern="TemplateName" path="<base>/" glob="*.xml"
   ```

### Reading files

Once Grep identifies the right file(s), read only what's needed. If Grep shows a match at a specific line, use `offset` and `limit` to read a window around that line rather than the whole file.

For understanding a folder's contents, check its `.toc` file first — it lists all files and their load order.

## Common Lookup Patterns

| Looking for | Approach |
|---|---|
| Function signature / args / returns | Grep the index for `"Name":` |
| Event name and payload | Grep the index for the event name (literal or camelCase) |
| Enum values / structure fields | Grep the index for the type name |
| All functions in a system | Grep the index for the system name |
| How Blizzard calls an API | Grep across `Blizzard_*` lua files for the function name |
| Frame template / XML layout | Grep xml files for the template name |
| Mixin implementation | Grep lua files for `MixinName` — look for `Mixin = {}` definition |
| What events a frame registers | Search lua/xml for `RegisterEvent` in the relevant addon folder |

## Presenting Results

- Show function signatures with argument names, types, and return values
- For events, show the literal name and payload fields
- Include the source file path for reference
- Note `secret_arguments` restrictions when relevant to what the user is building
- When showing implementation code, include enough context to be useful

## Version and Regeneration

The bundled index records its provenance in its first lines: `source_version` (git describe of the wow-ui-source checkout), `generated_on`, `source_fingerprint`, and entry counts. If the user's game version differs or the export has been updated, regenerate:

```bash
python3 <skill>/scripts/generate_index.py --ensure
```

`--ensure` (default) fingerprints the docs export and rebuilds only when it changed; `--check` reports FRESH/STALE and exits 1 when stale; `--force` always rebuilds. The script auto-locates the clone via `$WOW_UI_SOURCE` or common paths, or takes the docs directory as its first argument. This only matters when a local wow-ui-source clone exists — the bundled index works standalone.

`scripts/validate_index.py` re-extracts every entry with an independent parser and cross-checks the index (recall, precision, and header totals). Run it after regenerating, or when a lookup result smells wrong after a game patch.
