---
name: wow-api-search
description: Search a local export of Blizzard's World of Warcraft interface code for API functions, events, enums, structures, frame templates, mixins, and UI implementation details. Use it whenever the question is about a WoW API function, an event, frame XML, a UI template, a mixin, or how Blizzard implements any part of the default UI — and when writing addon code that needs a signature, an event payload, an enum value, a profiling or measurement API, or Blizzard's own handling of something: "why does Blizzard's frame do X", "which Blizzard function sets this field", "what refreshes this cached value", "is this field secret in combat", "can I compare this while tainted", "what args does X take", "find the event for Z", "how do I measure per-addon CPU". Reach for it the moment the answer is in Blizzard's code rather than the addon's, and always before asserting when an event fires, what its payload carries, what a constant means, or which API measures something — never from memory.
---

# WoW API Search

This skill bundles a pre-built JSON index of every documented WoW API function, event, enum, and structure, and (optionally) searches a local clone of Blizzard's exported retail interface code.

## Paths

`<skill>` below is this skill's own directory — the one holding this file. Run
the scripts with `python3`, or `python` on Windows; they need only the standard
library and Python 3.9+.

**Index** (bundled, works standalone): `references/api_index.json` under this skill's base directory. It answers signature/payload/enum questions without any other setup.

If `references/api_index.local.json` exists, grep that one instead. It is the same index rebuilt locally with Blizzard's prose notes, which the bundled copy leaves out (see **Prose notes** below); everything else in the two files is identical.

**Source code** (optional, needed only for implementation details): a clone of [Gethe/wow-ui-source](https://github.com/Gethe/wow-ui-source). Resolve the base path in this order:

1. `$WOW_UI_SOURCE` environment variable
2. Common locations: `~/Repos/wow-ui-source`, `~/repos/…`, `~/src/…`, `~/code/…`, `~/Projects/…`, `~/projects/…`, `~/dev/…`, `~/wow-ui-source`

The index records which export it was built from — `source_version` and
`source_fingerprint` at the top of the file — but not where that clone sat, so
the path has to be resolved rather than read.

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
- **Functions**: `{ system, namespace, qualified_name, file, arguments: [{name, type, nilable, default?}], returns: [...], documentation?, secret_arguments?, returns_never_secret?, preconditions? }`
- **Events**: `{ system, file, literal_name, name, payload: [{name, type, nilable}], documentation? }`
- **Tables**: `{ system, file, type: Structure|Enumeration|Constants, fields: [{name, type, nilable?, enum_value?, value?, never_secret?, documentation?}] }` — `enum_value` on an Enumeration member, `value` on a Constants member
- **Predicates**: `{ system, file, failure_mode }` — under the `predicates` section

`documentation` is Blizzard's own prose note, carried on the entry and on
individual fields. Read it when it is there — it holds semantics no signature
can, and the caveats are the expensive part to rediscover.
`SpellCooldownInfo.isOnGCD` says "do not trust this field unless responding to
a SPELL_UPDATE_COOLDOWN event"; `GetSpellCharges` says it "may return nil if
spell is not found or is not charge-based". Quote the note when it changes how
the API has to be called.

**Prose notes and which index carries them.** The header field `documentation`
says `included` or `omitted`. The bundled index says `omitted`: it carries the
facts about the interface, and leaves Blizzard's writing in the export it came
from. `source_documented` is how many notes that export holds.

When the note is absent, do not infer the semantics from the name. Either read
the entry's own `file` in the docs export, under
`<clone>/Interface/AddOns/Blizzard_APIDocumentationGenerated/`, where the note
sits one grep from the entry name — or say that the index carries no note for
this entry, and that `scripts/generate_index.py --with-docs` builds one that
does. Reporting a signature without its caveat, as though there were no
caveat, is the failure this paragraph exists to prevent.

When the same unqualified name exists in several namespaces (`GetName`, `IsEnabled`, ...), the value is an **array** of entries instead of a single object — check `namespace`/`qualified_name` to pick the right one. The one-grep-returns-everything property still holds.

`secret_arguments` marks taint restrictions: `NotAllowed` (rejects secret values), `AllowedWhenUntainted`, or `AllowedWhenTainted`. Worth flagging when the user is writing combat- or protected-context code.

### Read the whole payload, not the argument you expected

An event handler that binds fewer arguments than the event sends throws away
the discriminator that would have made the code simple. Before writing or
editing a handler, grep the event and read every payload field — the later
ones carry the classification, and a handler shared between several events
must bind each one at its own position.

`SPELL_UPDATE_COOLDOWN` is the worked example. It sends four arguments —
`spellID, baseSpellID, category, startRecoveryCategory` — and the fourth is
how Blizzard tells a global cooldown apart from a spell cooldown:

```lua
-- Blizzard_CooldownViewer/CooldownViewer.lua
if startRecoveryCategory == Constants.SpellCooldownConsts.GLOBAL_RECOVERY_CATEGORY then
```

An addon that reads only `spellID` cannot make that distinction and ends up
inferring it from cast bars and timers instead.

Watch the position, too. `UNIT_*` events lead with a unit token; most others
do not. One `OnEvent` handling both must not read them at shared offsets.

### Named constants — `Constants.<Table>.<NAME>`

Blizzard compares against named numbers rather than literals, and the index
carries their values. When a payload field or a struct field is "a category",
"a type", or "an index", the constant it is compared against is in a
Constants table, and finding it beats inferring the meaning from behaviour:

```
Grep pattern="\"SpellCooldownConsts\":" path="<index_path>" output_mode="content"
```

returns `GLOBAL_RECOVERY_CATEGORY` with `"value": 133`. Grep the constant's
own name when you have the name but not its table.

### Which fields survive combat — `never_secret`

In restricted combat an addon gets **secret values** back from many APIs. A
secret can be passed around and handed back to Blizzard, but comparing it,
concatenating it, or indexing it **raises a Lua error** — so "which of these
fields can I actually branch on" decides the shape of the code, not just its
correctness. Blizzard answers it per field, and the index carries the answer:

- a struct field with `"never_secret": true` is readable and comparable by a
  tainted addon, always
- a field **without** the marker may read secret — treat it as opaque, or
  classify it before use

```
Grep pattern="\"SpellChargeInfo\":" path="<index_path>" output_mode="content"
```

returns `maxCharges` and `isActive` marked `never_secret`, and `currentCharges`
unmarked — which is exactly why a charge count has to be inferred from
`isActive` rather than compared against zero. `returns_never_secret` on a
function says the same thing about its whole return set.

Answer this from the index. Grepping `NeverSecret` in the docs export works but
re-derives what the index already holds, and only for the one struct grepped.

`preconditions` lists the predicate names a function requires (e.g. `RequiresNonReadOnlyCVar`). Grep the predicate name in the `predicates` section for its `failure_mode`: `Error` means the call raises, `ReturnNothing` means it silently returns nothing — useful when explaining why an API "doesn't work".

For partial-name or fuzzy searches, grep the index without the quotes/colon anchor (`pattern="MapInfo"`). If you need more than the index provides (full system docs, related types), the `file` field names the source file in `Blizzard_APIDocumentationGenerated/`.

### Measuring cost — `C_AddOnProfiler`

"How do I measure this" is an index question, and answering it from memory
reaches for the wrong tool. The client runs a per-addon profiler continuously
for every user — no CVar, no `/reload`:

```
Grep pattern="\"GetAddOnMetric\":" path="<index_path>" output_mode="content"
Grep pattern="\"AddOnProfilerMetric\":" path="<index_path>" output_mode="content"
```

| Call | Gives |
|---|---|
| `C_AddOnProfiler.GetAddOnMetric(name, metric)` | one addon's cost, in ms |
| `C_AddOnProfiler.GetTopKAddOnsForMetric(metric, k)` | the ranking, in one call |
| `C_AddOnProfiler.GetOverallMetric` / `GetApplicationMetric` | all addons, and the whole client |
| `C_AddOnProfiler.MeasureCall(func, args)` | one call's `elapsedMilliseconds` **and** `allocatedBytes` |

`Enum.AddOnProfilerMetric` is where the useful part lives: `RecentAverageTime`
is the last 60 ticks, `PeakTime` is per-frame and does not average, and
`CountTimeOver1Ms` through `CountTimeOver1000Ms` are cumulative hitch counters
— which is the "why are my 1% lows bad" question asked directly, per addon.

The older `GetAddOnCPUUsage` path needs `scriptProfile` set plus a reload,
costs global overhead, and double-counts work a module runs inside a Blizzard
frame. Prefer `C_AddOnProfiler`, and say so if the user proposes the other.

For EllesmereUI specifically this is already wrapped — see `eui-perf`, which
routes to `/euidiag cpu` and `tools/perf/` rather than to raw API calls.

### What a secure snippet may do in combat — `Blizzard_RestrictedAddOnEnvironment/`

An addon that drives action bars, unit frames, or anything protected runs part
of itself as a **secure snippet**, inside a restricted environment with its own
much smaller API. "Can I do this in combat" has two different answers there,
and guessing either one produces a fix that works out of combat and does
nothing in a raid:

- **the frame handle surface** — `RestrictedFrames.lua` defines every method a
  snippet can call on a frame, as `function HANDLE:Name(...)`. If a method has
  no `HANDLE:` definition, a snippet cannot call it. Grep the file for
  `^function HANDLE:` for the whole list, or for one method name to settle one
  question.
- **the callable whitelist** — `RestrictedEnvironment.lua` lists the global
  functions a snippet may call (`HasAction`, `GetActionInfo`, …). A function
  absent from it is unreachable, not merely discouraged.

The surface is deliberately narrow and the omissions matter more than the
inclusions. `HANDLE:SetAlpha` and `HANDLE:EnableMouse` exist; there is no
`SetMouseMotionEnabled`, so a snippet can turn a button's mouse fully on or
fully off and cannot make it motion-only. Confirm the method exists **before**
designing around it, and say so in the comment — this is not something the
generated API documentation covers, so the file is the only specification.

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
| Value of a named constant | Grep the index for the constant name, or for its `Consts`/`Constants` table |
| What a `category` / `type` argument means | Find the Constants table Blizzard compares it against, then grep `Blizzard_*` for that constant |
| When an event fires relative to another | The index does not say. Grep `Blizzard_*` for both `RegisterEvent` names and read the handler order Blizzard relies on |
| All functions in a system | Grep the index for the system name |
| How Blizzard calls an API | Grep across `Blizzard_*` lua files for the function name |
| Frame template / XML layout | Grep xml files for the template name |
| Mixin implementation | Grep lua files for `MixinName` — look for `Mixin = {}` definition |
| Can I read this field in combat | Grep the index for the struct name, check `never_secret` per field |
| Can a secure snippet call this method in combat | Grep `Blizzard_RestrictedAddOnEnvironment/RestrictedFrames.lua` for `HANDLE:<Name>` — no definition means no |
| Can a secure snippet call this global | Grep `Blizzard_RestrictedAddOnEnvironment/RestrictedEnvironment.lua` for the name |
| Which Blizzard code writes a field | Grep the owning `Blizzard_*` folder for the field name — the writer is usually one function, and finding it beats inferring the rule |
| What events a frame registers | Search lua/xml for `RegisterEvent` in the relevant addon folder |

## Presenting Results

- Show function signatures with argument names, types, and return values
- For events, show the literal name and payload fields
- Include the source file path for reference
- Note `secret_arguments` restrictions when relevant to what the user is building
- When showing implementation code, include enough context to be useful

## Version and Regeneration

The bundled index is committed and works standalone, so — unlike `ellesmereui-search` — this skill does **not** rebuild on every use. It has no way to know the game patched; nothing checks freshness unless you ask.

```bash
python3 <skill>/scripts/generate_index.py --check    # FRESH/STALE, exit 1 if stale
python3 <skill>/scripts/generate_index.py --ensure   # rebuild only if the export changed
```

Run `--check` when any of these is true, and not otherwise:

- a lookup returns nothing for a name the user is sure exists
- the user mentions a patch, a PTR or beta client, or a new expansion feature
- an indexed signature disagrees with what the user is seeing in game
- you are about to rely on the index for something expensive to get wrong

Staleness is decided by a content hash of the export plus the builder version, so a rebuild is also forced when the extractor itself changes — not only when Blizzard ships new files. `--force` always rebuilds. The script auto-locates the clone via `$WOW_UI_SOURCE` or common paths, or takes the docs directory as its first argument. All of this needs a local wow-ui-source clone; with none, the bundled index is what you have, and its `source_version` and `generated_on` in the first lines tell the user what it is.

`scripts/validate_index.py` re-extracts every entry with an independent parser and cross-checks recall, precision, per-entry content, note coverage, and header totals. Run it after regenerating, or when a lookup result smells wrong after a game patch.
