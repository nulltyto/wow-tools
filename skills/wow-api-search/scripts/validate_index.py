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
    """Independently extract entry names per section via brace-depth walking.

    Returns {"Functions": [name, ...], "Events": [{"name":..., "literal":...}],
    "Tables": [name, ...]}.
    """
    mask = mask_lua(text)

    section_open = {
        m.end() - 1: m.group(1)
        for m in re.finditer(r"\n\t(Functions|Events|Tables|Predicates)\s*=\s*\{", mask)
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

    for offset, kind, payload in tokens:
        if kind == "brace" and payload == "{":
            depth += 1
            if offset in section_open:
                section = (section_open[offset], depth)
            elif section and depth == section[1] + 1:
                entry = {}
        elif kind == "brace":
            if section and entry is not None and depth == section[1] + 1:
                if "Name" in entry:
                    result[section[0]].append(entry)
                entry = None
            if section and depth == section[1]:
                section = None
            depth -= 1
        elif entry is not None and section and depth == section[1] + 1:
            field, value = payload
            entry.setdefault(field, value)

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
    for lua_file in sorted(docs_dir.glob("*.lua")):
        scanned = scan_file(lua_file.read_text(encoding="utf-8", errors="replace"))
        for e in scanned["Functions"]:
            raw_functions.add((e["Name"], lua_file.name))
        for e in scanned["Events"]:
            raw_events.add((e["Name"], lua_file.name))
            if "LiteralName" in e:
                raw_events.add((e["LiteralName"], lua_file.name))
        for e in scanned["Tables"]:
            raw_tables.add((e["Name"], lua_file.name))
        for e in scanned["Predicates"]:
            raw_predicates.add((e["Name"], lua_file.name))

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
