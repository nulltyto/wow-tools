#!/usr/bin/env python3
"""Look up one Blizzard API entry, rendered rather than raw.

The index answers a signature, a payload, an enum value or a taint restriction
in one grep -- but only if the lookup gets written. Sessions in this repo's
addon reach for `grep -rn` against a wow-ui-source clone instead, which finds
the declaration and skips the caveats attached to it. This makes the indexed
answer the shorter thing to type:

    query.py func SetSpecialization
    query.py event SPELL_UPDATE_COOLDOWN
    query.py table AddOnProfilerMetric
    query.py search Specialization

Every rendering carries what the entry says about taint, nilability, and
Blizzard's own prose note -- including saying so when the note is absent,
because a signature reported without its caveat reads as though there were no
caveat.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
BUNDLED = SKILL_DIR / "references" / "api_index.json"
LOCAL = SKILL_DIR / "references" / "api_index.local.json"


def load_index(explicit=None):
    """The local index wins: it is the same data plus Blizzard's prose notes."""
    for path in ([Path(explicit)] if explicit else [LOCAL, BUNDLED]):
        if path.is_file():
            with path.open(encoding="utf-8") as fh:
                return json.load(fh), path
    sys.exit("no API index found -- run generate_index.py, or check %s" % BUNDLED)


def as_entries(value):
    """A name shared by several namespaces is stored as a list of entries."""
    return value if isinstance(value, list) else [value]


def type_of(field):
    t = field.get("type", "?")
    if field.get("nilable"):
        t += "?"
    if "default" in field:
        t += " = %s" % field["default"]
    return t


def render_doc(entry, index, indent="  "):
    doc = entry.get("documentation")
    if doc:
        if isinstance(doc, list):
            doc = " ".join(doc)
        print("%snote: %s" % (indent, doc))
    elif index.get("documentation") == "omitted":
        print("%snote: none in this index -- the bundled copy omits Blizzard's prose "
              "(%d notes exist in the export). Build one that carries them with "
              "generate_index.py --with-docs, or read %s in the docs export."
              % (indent, index.get("source_documented", 0), entry.get("file", "?")))
    else:
        # This index carries the notes, so silence here is a fact about the
        # entry rather than about the index -- worth saying, so the absence is
        # not read as a lookup that half worked.
        print("%snote: Blizzard records none for this entry." % indent)


def render_function(name, entry, index):
    args = ", ".join("%s: %s" % (a["name"], type_of(a)) for a in entry.get("arguments", []))
    rets = ", ".join("%s: %s" % (r["name"], type_of(r)) for r in entry.get("returns", []))
    print("%s(%s)%s" % (entry.get("qualified_name", name), args, "  ->  " + rets if rets else ""))
    print("  system %s   file %s" % (entry.get("system", "?"), entry.get("file", "?")))

    # The taint line is the one a combat-path change turns on, and it is the
    # one a from-memory answer never has.
    secret = entry.get("secret_arguments")
    if secret:
        print("  taint: secret_arguments = %s%s" % (secret, {
            "NotAllowed": "  (rejects a secret value -- guard the call site)",
            "AllowedWhenUntainted": "  (works until your execution path is tainted)",
            "AllowedWhenTainted": "  (safe to call from tainted code)",
        }.get(secret, "")))
    if entry.get("returns_never_secret"):
        print("  taint: returns are never secret -- safe to compare")
    if entry.get("preconditions"):
        print("  preconditions: %s" % ", ".join(entry["preconditions"]))
    render_doc(entry, index)


def render_event(name, entry, index):
    print("%s  (%s)" % (entry.get("literal_name", name), entry.get("name", "")))
    print("  system %s   file %s" % (entry.get("system", "?"), entry.get("file", "?")))
    payload = entry.get("payload", [])
    if not payload:
        print("  payload: none")
    else:
        print("  payload (%d args -- bind every one; the later ones carry the "
              "classification):" % len(payload))
        for i, f in enumerate(payload, 1):
            print("    %d. %s: %s" % (i, f["name"], type_of(f)))
    render_doc(entry, index)


def render_table(name, entry, index, limit):
    print("%s  %s" % (name, entry.get("type", "?")))
    print("  system %s   file %s" % (entry.get("system", "?"), entry.get("file", "?")))
    fields = entry.get("fields", [])
    for f in fields[:limit]:
        bits = [f["name"]]
        if "enum_value" in f:
            bits.append("= %s" % f["enum_value"])
        elif "value" in f:
            bits.append("= %s" % f["value"])
        else:
            bits.append(": %s" % type_of(f))
        if f.get("never_secret"):
            bits.append("[never secret -- readable in combat]")
        print("    %s" % " ".join(str(b) for b in bits))
        if f.get("documentation"):
            print("      %s" % f["documentation"])
    if len(fields) > limit:
        print("    ... %d more fields (--limit %d to widen)" % (len(fields) - limit, len(fields)))
    render_doc(entry, index)


SECTIONS = {"func": "functions", "event": "events", "table": "tables"}
RENDER = {"functions": render_function, "events": render_event, "tables": render_table}


def lookup(args, section):
    index, path = load_index(args.index)
    store = index.get(section, {})
    name = args.name
    hits = {}
    if name in store:
        hits[name] = store[name]
    elif section == "events" and name.upper() in store:
        hits[name.upper()] = store[name.upper()]
    if not hits:
        low = name.lower()
        near = [k for k in store if low in k.lower()]
        print("no %s entry named %r" % (section[:-1], name))
        if near:
            print("did you mean: %s%s" % (", ".join(sorted(near)[:12]),
                                          " ..." if len(near) > 12 else ""))
        else:
            print("Nothing in the index matches. Undocumented APIs exist -- fall back to "
                  "grepping the wow-ui-source clone, and say the index had no entry.")
        return 1
    if args.json:
        print(json.dumps(hits, indent=1))
        return 0
    for key, value in hits.items():
        for entry in as_entries(value):
            if section == "tables":
                RENDER[section](key, entry, index, args.limit)
            else:
                RENDER[section](key, entry, index)
            print()
    return 0


def cmd_func(args):
    return lookup(args, "functions")


def cmd_event(args):
    return lookup(args, "events")


def cmd_table(args):
    return lookup(args, "tables")


def cmd_search(args):
    """Substring across every section, for when the exact name is unknown."""
    index, path = load_index(args.index)
    low = args.pattern.lower()
    found = 0
    for section in ("functions", "events", "tables", "predicates", "systems"):
        names = sorted(k for k in index.get(section, {}) if low in k.lower())
        if not names:
            continue
        # Events are indexed under both their literal and camelCase names, so
        # matching on the key alone lists the same event twice.
        rows, seen = [], set()
        for n in names:
            entry = as_entries(index[section][n])[0]
            display = entry.get("qualified_name") or entry.get("literal_name") or n
            if display in seen:
                continue
            seen.add(display)
            rows.append((display, entry.get("system", "")))
        print("== %s (%d)" % (section, len(rows)))
        for display, system in rows[:args.limit]:
            print("  %-46s %s" % (display, system))
        if len(rows) > args.limit:
            print("  ... %d more (--limit %d to widen)" % (len(rows) - args.limit, len(rows)))
        found += len(rows)
    if not found:
        print("nothing matches %r in %s" % (args.pattern, path.name))
        return 1
    return 0


def cmd_system(args):
    """Everything one documentation system holds -- the survey view."""
    index, _ = load_index(args.index)
    systems = index.get("systems", {})
    name = args.name
    if name not in systems:
        near = sorted(k for k in systems if args.name.lower() in k.lower())
        print("no system named %r" % name)
        if near:
            print("did you mean: %s" % ", ".join(near[:12]))
        return 1
    meta = systems[name]
    print("%s  namespace=%s  file=%s" % (name, meta.get("namespace") or "(none)", meta.get("file")))
    for section in ("functions", "events", "tables"):
        names = sorted(k for k, v in index.get(section, {}).items()
                       if any(e.get("system") == name for e in as_entries(v)))
        if not names:
            continue
        print("  %s (%d): %s%s" % (section, len(names), ", ".join(names[:args.limit]),
                                   " ..." if len(names) > args.limit else ""))
    return 0


def cmd_status(args):
    index, path = load_index(args.index)
    print("index    %s" % path)
    print("built    %s from %s" % (index.get("generated_on"), index.get("source_version")))
    print("prose    %s (%s notes in the export)"
          % (index.get("documentation"), index.get("source_documented")))
    print("counts   %s functions, %s events, %s tables, %s systems"
          % (index.get("total_functions"), index.get("total_events"),
             index.get("total_tables"), index.get("total_systems")))
    return 0


COMMANDS = {
    "func": (cmd_func, "name", "a function's signature, taint marking, and note"),
    "event": (cmd_event, "name", "an event's full payload, in order"),
    "table": (cmd_table, "name", "an enum, structure, or constants table and its fields"),
    "search": (cmd_search, "pattern", "substring across every section"),
    "system": (cmd_system, "name", "everything one documentation system holds"),
    "status": (cmd_status, None, "which index is loaded and what it was built from"),
}


DEFAULTS = {"index": None, "json": False, "limit": 25}


def main():
    # Shared flags carry SUPPRESS as their default so that a subparser does not
    # overwrite a value the main parser already read. Without it, argparse
    # applies the subparser's defaults last and `query.py --json func X`
    # silently loses the flag -- the same value, parsed twice, second one wins.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--index", default=argparse.SUPPRESS,
                        help="path to an api_index.json to use instead")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print the raw entries")
    common.add_argument("--limit", type=int, default=argparse.SUPPRESS,
                        help="max list entries (default 25)")

    ap = argparse.ArgumentParser(
        prog="query.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="The local index (api_index.local.json) is preferred when present: "
               "same data, plus Blizzard's prose notes.",
        parents=[common],
    )
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")
    for name, (fn, arg, help_text) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text, parents=[common])
        if arg:
            p.add_argument(arg)
        p.set_defaults(fn=fn)

    args = ap.parse_args()
    for key, value in DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    if not args.command:
        ap.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
