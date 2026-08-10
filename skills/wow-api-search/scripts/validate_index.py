#!/usr/bin/env python3
"""Validate the API index against the Blizzard docs export it was built from.

generate_index.py extracts entries with regexes anchored on the export's
exact tab indentation. If Blizzard ever reformats the export, those regexes
lose coverage silently. This script re-scans every doc file with a different
mechanism — a brace-depth walker over comment/string-masked text — and
cross-checks the two results:

  recall    -- every entry name found by the depth walker exists in the index,
               attributed to the right file.
  precision -- every index entry's name is found by the depth walker in the
               file the entry claims.
  content   -- every entry's members (arguments, returns, payload, fields)
               match the walker's, in order.

The content check is the one that earns its keep. Name-level checks pass
whenever an entry merely exists, so they said nothing while Constants tables
indexed with no members at all and payload fields went missing -- the names
were right, the insides were empty.

Run after regenerating the index, or after a wow-ui-source update large
enough that you want proof the parser still sees everything.

    validate_index.py            exit 0 if clean, 1 if any check fails
    validate_index.py --verbose  list every failure rather than a sample
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_index import DEFAULT_OUTPUT, DOCS_SUBPATH, find_docs_dir  # noqa: E402

SECTIONS = ("Functions", "Events", "Tables", "Predicates")

# The member blocks inside an entry, and the index key each lands under.
# Constants write their members as Values; everything else as Fields.
SUBSECTIONS = {
    "Arguments": "arguments",
    "Returns": "returns",
    "Payload": "payload",
    "Fields": "fields",
    "Values": "fields",
}


def mask_lua(text):
    """Blank string contents and line comments, preserving offsets.

    The generated docs use only short strings and `--` comments, so this
    does not handle long brackets. A brace inside an unmasked construct
    shows up as a validation failure, which is the desired behavior.
    """
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == "-" and i + 1 < n and text[i + 1] == "-":
            end = text.find("\n", i)
            end = n if end == -1 else end
            for k in range(i, end):
                out[k] = " "
            i = end
        elif c == '"':
            j = i + 1
            while j < n and text[j] not in '"\n':
                j += 2 if text[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def scan_file(text):
    """Independently extract entries per section via brace-depth walking.

    Each entry is {"Name": ..., "LiteralName"?: ..., "members": {index_key:
    [member name, ...]}}, where members are read from the Arguments, Returns,
    Payload, Fields and Values blocks at their own brace depth.
    """
    mask = mask_lua(text)

    section_open = {
        m.end() - 1: m.group(1)
        for m in re.finditer(r"\n\t(Functions|Events|Tables|Predicates)\s*=\s*\{", mask)
    }
    # A member block opens one level inside an entry. Anchored to the export's
    # three-tab indentation so a Documentation brace cannot be mistaken for one.
    sub_open = {
        m.end() - 1: m.group(1)
        for m in re.finditer(
            r"\n\t\t\t(" + "|".join(SUBSECTIONS) + r")\s*=\s*\n?\s*\{", mask)
    }
    tokens = [(m.start(), "brace", m.group()) for m in re.finditer(r"[{}]", mask)]
    for m in re.finditer(r"\b(Name|LiteralName)\s*=\s*\"", mask):
        close = text.find('"', m.end())
        value = text[m.end():close] if close != -1 else ""
        tokens.append((m.start(), "name", (m.group(1), value)))
    tokens.sort(key=lambda t: t[0])

    result = {s: [] for s in SECTIONS}
    depth = 0
    section = None      # (name, depth at which its brace opened)
    entry = None
    sub = None          # (index key, depth at which its brace opened)

    for offset, kind, payload in tokens:
        if kind == "brace" and payload == "{":
            depth += 1
            if offset in section_open:
                section = (section_open[offset], depth)
            elif section and depth == section[1] + 1:
                entry = {"members": {}}
            elif entry is not None and offset in sub_open:
                sub = (SUBSECTIONS[sub_open[offset]], depth)
                entry["members"].setdefault(sub[0], [])
        elif kind == "brace":
            if sub and depth == sub[1]:
                sub = None
            if section and entry is not None and depth == section[1] + 1:
                if "Name" in entry:
                    result[section[0]].append(entry)
                entry = None
            if section and depth == section[1]:
                section = None
            depth -= 1
        elif entry is not None and section:
            field, value = payload
            if depth == section[1] + 1:
                entry.setdefault(field, value)
            elif sub and depth == sub[1] + 1 and field == "Name":
                entry["members"][sub[0]].append(value)

    return result


def flatten(lookup):
    """Yield every entry in a lookup, unwrapping collision lists."""
    for key, value in lookup.items():
        for entry in value if isinstance(value, list) else [value]:
            yield key, entry


class Reporter:
    def __init__(self, verbose):
        self.verbose = verbose
        self.failures = 0

    def report(self, label, total, bad):
        status = "ok " if not bad else "FAIL"
        print(f"  [{status}] {label:34s} {total - len(bad):6d}/{total:6d}")
        for item in bad if self.verbose else bad[:5]:
            print(f"           {item}")
        if bad and not self.verbose and len(bad) > 5:
            print(f"           ... and {len(bad) - 5} more (--verbose for all)")
        self.failures += len(bad)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--docs-dir", type=Path)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not args.index.is_file():
        sys.exit(f"No index at {args.index}. Run generate_index.py first.")
    index = json.loads(args.index.read_text(encoding="utf-8"))

    docs_dir = args.docs_dir or Path(index.get("generated_from", ""))
    if not docs_dir.is_dir():
        docs_dir = find_docs_dir()
    if docs_dir is None or not docs_dir.is_dir():
        sys.exit("Could not locate the docs export. Pass --docs-dir "
                 f"<clone>/{DOCS_SUBPATH}.")

    print(f"Validating {args.index.name} against {docs_dir}")
    print(f"  generated {index.get('generated_on', '?')} "
          f"from {index.get('source_version', '?')}\n")

    # Independent scan of every doc file.
    raw_functions = set()   # (name, file)
    raw_events = set()      # (key, file) — both camel and literal keys
    raw_tables = set()
    raw_predicates = set()
    # (section, lookup key, file) -> {index key: [member name, ...]}, for the
    # content check. An entry name that repeats within one file is dropped
    # rather than guessed at, so the check never compares against the wrong one.
    raw_members = {}
    ambiguous = set()

    def remember(section, key, filename, members):
        slot = (section, key, filename)
        if slot in raw_members and raw_members[slot] != members:
            ambiguous.add(slot)
        raw_members[slot] = members

    for lua_file in sorted(docs_dir.glob("*.lua")):
        scanned = scan_file(lua_file.read_text(encoding="utf-8", errors="replace"))
        for e in scanned["Functions"]:
            raw_functions.add((e["Name"], lua_file.name))
            remember("functions", e["Name"], lua_file.name, e["members"])
        for e in scanned["Events"]:
            raw_events.add((e["Name"], lua_file.name))
            remember("events", e["Name"], lua_file.name, e["members"])
            if "LiteralName" in e:
                raw_events.add((e["LiteralName"], lua_file.name))
                remember("events", e["LiteralName"], lua_file.name, e["members"])
        for e in scanned["Tables"]:
            raw_tables.add((e["Name"], lua_file.name))
            remember("tables", e["Name"], lua_file.name, e["members"])
        for e in scanned["Predicates"]:
            raw_predicates.add((e["Name"], lua_file.name))
            remember("predicates", e["Name"], lua_file.name, e["members"])

    for slot in ambiguous:
        raw_members.pop(slot, None)

    idx_functions = {(name, e["file"]) for name, e in flatten(index["functions"])}
    idx_events = {(key, e["file"]) for key, e in flatten(index["events"])}
    idx_tables = {(name, e["file"]) for name, e in flatten(index["tables"])}
    idx_predicates = {(name, e["file"]) for name, e in flatten(index.get("predicates", {}))}

    pairs = (("functions", raw_functions, idx_functions),
             ("events", raw_events, idx_events),
             ("tables", raw_tables, idx_tables),
             ("predicates", raw_predicates, idx_predicates))

    r = Reporter(args.verbose)
    print("RECALL -- every scanned entry is in the index")
    for label, raw, idx in pairs:
        bad = sorted(f"{f}: {n}" for n, f in raw - idx)
        r.report(label, len(raw), bad)

    print("\nPRECISION -- every index entry is found by the scan")
    for label, raw, idx in pairs:
        bad = sorted(f"{f}: {n}" for n, f in idx - raw)
        r.report(label, len(idx), bad)

    print("\nCONTENT -- every entry's members match the scan, in order")
    member_keys = ("arguments", "returns", "payload", "fields")
    for label, lookup in (("functions", index["functions"]),
                          ("events", index["events"]),
                          ("tables", index["tables"]),
                          ("predicates", index.get("predicates", {}))):
        bad, total = [], 0
        for key, entry in flatten(lookup):
            members = raw_members.get((label, key, entry["file"]))
            if members is None:
                continue  # absent or ambiguous; recall/precision owns that
            for mkey in member_keys:
                total += 1
                scanned = members.get(mkey, [])
                indexed = [f.get("name") for f in entry.get(mkey, [])]
                if scanned != indexed:
                    bad.append(f"{entry['file']}: {key}.{mkey}: "
                               f"scan {scanned} != index {indexed}")
        r.report(label, total, bad)
    if ambiguous:
        print(f"           ({len(ambiguous)} name(s) repeat within a file; "
              f"not content-checked)")

    print("\nNOTES -- Blizzard's prose survives extraction, per file")
    # Counted straight off the source with its own regexes rather than through
    # the walker: notes are the part of an entry most easily lost without
    # changing any name or count, so the check is deliberately independent of
    # both other mechanisms. An entry's note sits at three tabs; a field's is
    # inline on the field line.
    src_entry, src_field = {}, {}
    for lua_file in sorted(docs_dir.glob("*.lua")):
        text = lua_file.read_text(encoding="utf-8", errors="replace")
        src_entry[lua_file.name] = len(
            re.findall(r"^\t\t\tDocumentation\s*=\s*\{", text, re.M))
        src_field[lua_file.name] = sum(
            1 for line in text.splitlines()
            if line.strip().startswith("{ Name") and "Documentation = {" in line)

    idx_entry = dict.fromkeys(src_entry, 0)
    idx_field = dict.fromkeys(src_entry, 0)
    counted = set()
    for section in ("functions", "events", "tables", "predicates"):
        for key, entry in flatten(index[section]):
            # An event is stored under both its literal and camelCase name;
            # counting it twice would hide a real loss behind a surplus.
            ident = (entry["file"], section,
                     entry.get("literal_name") or entry.get("qualified_name") or key)
            if ident in counted:
                continue
            counted.add(ident)
            if entry["file"] not in idx_entry:
                continue
            if entry.get("documentation"):
                idx_entry[entry["file"]] += 1
            for mkey in member_keys:
                idx_field[entry["file"]] += sum(
                    1 for f in entry.get(mkey, []) if f.get("documentation"))

    for label, src, idx in (("entry notes", src_entry, idx_entry),
                            ("field notes", src_field, idx_field)):
        bad = sorted(f"{f}: source {src[f]}, index {idx[f]}"
                     for f in src if src[f] != idx[f])
        r.report(label, sum(src.values()), bad)

    print("\nHEADER -- recorded totals match the entries present")
    for label, key, idx in (("functions", "total_functions", idx_functions),
                            ("events", "total_events", idx_events),
                            ("tables", "total_tables", idx_tables),
                            ("predicates", "total_predicates", idx_predicates)):
        recorded = index.get(key, -1)
        bad = [] if recorded == len(idx) else [f"{key}={recorded}, actual {len(idx)}"]
        r.report(label, 1, bad)

    print()
    if r.failures:
        print(f"FAILED -- {r.failures} problem(s)")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
