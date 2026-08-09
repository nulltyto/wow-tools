#!/usr/bin/env python3
"""Report which parts of a WoW API structure, function, or event survive combat.

Blizzard marks the fields a tainted addon may read in restricted combat with
`NeverSecret`. Everything else may come back as a secret value, and a secret
raises the moment it is compared, concatenated, or indexed -- so this is the
question that decides how the code has to be written, not a footnote.

    secret_fields.py SpellChargeInfo          one structure
    secret_fields.py GetSpellCooldown         one function's returns
    secret_fields.py --all-clean Spell        every clean field in a system
    secret_fields.py --json SpellCooldownInfo machine-readable

Reads the index built by the wow-api-search skill. Resolution order:

    1. $WOW_API_INDEX
    2. --index PATH
    3. a sibling wow-api-search skill next to this one
    4. the usual skill install directories

Standard library only, Python 3.9+.
"""

import argparse
import json
import os
import sys
from pathlib import Path

INDEX_NAME = Path("references") / "api_index.json"

# Where a wow-api-search skill is likely to be, given that this skill may be
# installed on its own. Ordered nearest-first: a sibling in the same install
# is the copy the agent is already using.
SEARCH_BASES = (
    Path(__file__).resolve().parent.parent.parent,       # repo: skills/
    Path.home() / ".agents" / "skills",
    Path.home() / ".claude" / "skills",
    Path.home() / ".config" / "skills",
)


def find_index(explicit=None):
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None

    env = os.environ.get("WOW_API_INDEX")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
        print(f"Warning: $WOW_API_INDEX is set but {p} is not a file", file=sys.stderr)

    for base in SEARCH_BASES:
        candidate = base / "wow-api-search" / INDEX_NAME
        if candidate.is_file():
            return candidate
    return None


def entries(value):
    """Index values are a single entry, or a list when the name collides."""
    return value if isinstance(value, list) else [value]


def report_table(name, entry):
    kind = entry.get("type", "Table")
    print(f"{name}  ({kind}, {entry.get('file', '?')})")
    fields = entry.get("fields", [])
    if not fields:
        print("  no fields")
        return
    width = max(len(f.get("name", "")) for f in fields)
    for f in fields:
        mark = "clean " if f.get("never_secret") else "SECRET"
        print(f"  {mark}  {f.get('name', ''):<{width}}  {f.get('type', '')}")
    clean = sum(1 for f in fields if f.get("never_secret"))
    if clean == 0:
        print("\n  Nothing here is marked NeverSecret. In restricted combat treat every")
        print("  field as opaque: pass it back to Blizzard, never branch on it.")
    elif clean < len(fields):
        print(f"\n  {clean} of {len(fields)} readable while tainted. Build the branch on those.")


def report_function(name, entry):
    qualified = entry.get("qualified_name", name)
    print(f"{qualified}  ({entry.get('file', '?')})")
    if entry.get("secret_arguments"):
        print(f"  arguments: {entry['secret_arguments']}")
    if entry.get("returns_never_secret"):
        print("  returns:   every return value is NeverSecret")
    rets = entry.get("returns", [])
    if not rets:
        print("  returns:   nothing")
        return
    for r in rets:
        mark = "clean " if (r.get("never_secret") or entry.get("returns_never_secret")) else "?     "
        print(f"    {mark}  {r.get('name', '')}  {r.get('type', '')}")
    print("\n  A return typed as a structure carries its own per-field answer --")
    print("  run this again on the structure name.")


def report_event(name, entry):
    print(f"{entry.get('literal_name') or name}  ({entry.get('file', '?')})")
    for f in entry.get("payload", []):
        mark = "clean " if f.get("never_secret") else "?     "
        print(f"    {mark}  {f.get('name', '')}  {f.get('type', '')}")


def all_clean(index, system):
    """Every field marked NeverSecret in one system -- the usable surface."""
    wanted = system.lower()
    hits = []
    for name, value in index.get("tables", {}).items():
        for entry in entries(value):
            if wanted not in entry.get("system", "").lower():
                continue
            clean = [f["name"] for f in entry.get("fields", []) if f.get("never_secret")]
            if clean:
                hits.append((name, clean))
    if not hits:
        print(f"No NeverSecret fields in any structure of a system matching {system!r}.")
        return 1
    for name, clean in sorted(hits):
        print(f"{name}: {', '.join(clean)}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="structure, function, or event name")
    ap.add_argument("--index", help="path to api_index.json")
    ap.add_argument("--all-clean", metavar="SYSTEM",
                    help="list every NeverSecret field in a system (e.g. Spell)")
    ap.add_argument("--json", action="store_true", help="print the raw index entry")
    args = ap.parse_args()

    if not args.name and not args.all_clean:
        ap.error("give a name, or --all-clean SYSTEM")

    path = find_index(args.index)
    if path is None:
        print("No api_index.json found. It ships with the wow-api-search skill;", file=sys.stderr)
        print("point at it with --index PATH or $WOW_API_INDEX.", file=sys.stderr)
        return 2

    index = json.loads(path.read_text(encoding="utf-8"))

    if args.all_clean:
        return all_clean(index, args.all_clean)

    name = args.name
    found = False
    for section, reporter in (("tables", report_table),
                              ("functions", report_function),
                              ("events", report_event)):
        value = index.get(section, {}).get(name)
        if value is None:
            continue
        for entry in entries(value):
            if args.json:
                print(json.dumps(entry, indent=2))
            else:
                if found:
                    print()
                reporter(name, entry)
            found = True

    if not found:
        print(f"{name!r} is not in the index.", file=sys.stderr)
        print("Names are unqualified: GetSpellCooldown, not C_Spell.GetSpellCooldown.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
