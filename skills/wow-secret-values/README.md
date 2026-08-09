# wow-secret-values

How to write World of Warcraft addon code that survives **secret values**, and
how to confirm any claim about them from a live client.

## What this is for

Since the 11.x secret-value system, a tainted addon that reads certain APIs in
restricted combat gets back a value it may hold and hand back to Blizzard, but
may not look inside. The failure mode is specific and unpleasant: the code works
solo and at a target dummy, then raises a Lua error in an instance.

The skill exists because one debugging session produced three separate live
errors of this kind — a comparison, a `table.concat`, and a string index — each
costing a round trip through combat to discover. All three were avoidable from
information Blizzard publishes.

## Contents

| Path | What it is |
|---|---|
| `SKILL.md` | The rules: what raises, the safe shapes, and how to check live |
| `scripts/secret_fields.py` | Which fields of a structure or function survive combat |

## The script

```bash
python3 scripts/secret_fields.py SpellChargeInfo
python3 scripts/secret_fields.py GetSpellCooldown
python3 scripts/secret_fields.py --all-clean Spell
```

Blizzard marks readable fields `NeverSecret` in its documentation export. The
script reads those markers out of the index that ships with the
`wow-api-search` skill, so it answers in one call what otherwise takes a grep
of the raw export per structure.

It finds the index at `$WOW_API_INDEX`, at `--index PATH`, beside this skill,
or in the usual skill install directories. Standard library only, Python 3.9+.

## Limits

The markers are Blizzard's own, and they describe the API surface. They say
nothing about a **frame field** an addon reads directly — `frame.someField` is
not in any documentation export, and whether it reads clean has to be
established from Blizzard's source or from a live capture. `SKILL.md` covers
both routes, and `EllesmereUISecretsDiag` in this repository is the live one.

A field marked clean is clean. A field not marked is *unknown*, not proven
secret — many are plain in every context anyone has tested. Treat the absence
of a marker as a reason to classify rather than as a verdict.
