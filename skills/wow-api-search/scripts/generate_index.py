#!/usr/bin/env python3
"""Parse Blizzard_APIDocumentationGenerated lua files into a JSON index.

Usage:
    generate_index.py [docs_dir] [output_path] [--ensure|--check|--force]

Modes:
    --ensure   rebuild only if the docs export changed (default)
    --check    report FRESH/STALE, exit 1 if stale
    --force    rebuild unconditionally

docs_dir resolution order:
    1. CLI argument
    2. $WOW_UI_SOURCE/Interface/AddOns/Blizzard_APIDocumentationGenerated
    3. Common clone locations (~/Repos, ~/repos, ~/src, ~/code, ~/Projects,
       ~/projects, ~/dev, ~) + wow-ui-source

output_path defaults to <skill_dir>/references/api_index.json
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

BUILDER_VERSION = 2

DOCS_SUBPATH = Path("Interface/AddOns/Blizzard_APIDocumentationGenerated")
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "references" / "api_index.json"


def find_docs_dir():
    """Locate the Blizzard docs folder without a hardcoded machine-specific path."""
    env = os.environ.get("WOW_UI_SOURCE")
    if env:
        candidate = Path(env).expanduser() / DOCS_SUBPATH
        if candidate.is_dir():
            return candidate
        print(f"Warning: $WOW_UI_SOURCE is set but {candidate} does not exist",
              file=sys.stderr)

    home = Path.home()
    for parent in ("Repos", "repos", "src", "code", "Projects", "projects", "dev", "."):
        candidate = home / parent / "wow-ui-source" / DOCS_SUBPATH
        if candidate.is_dir():
            return candidate

    return None


def detect_source_version(docs_dir):
    """Best-effort version string from the wow-ui-source git checkout."""
    for ancestor in docs_dir.resolve().parents:
        if (ancestor / ".git").exists():
            result = subprocess.run(
                ["git", "-C", str(ancestor), "describe", "--tags", "--always"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            break
    return "unknown"


def fingerprint(docs_dir):
    """Content hash of every doc file, so staleness detection catches
    uncommitted edits and branch switches, not just new files."""
    h = hashlib.sha256()
    for lua_file in sorted(docs_dir.glob("*.lua")):
        h.update(lua_file.name.encode())
        h.update(hashlib.sha256(lua_file.read_bytes()).digest())
    return h.hexdigest()


def read_index_header(output_path):
    """Read the scalar header fields of an existing index without parsing
    the whole multi-megabyte file."""
    if not output_path.is_file():
        return {}
    header = {}
    try:
        with output_path.open(encoding="utf-8") as fh:
            for _ in range(40):
                line = fh.readline()
                m = re.match(r'\s*"([a-z_]+)":\s*(".*?"|-?\d+),?\s*$', line)
                if m:
                    header[m.group(1)] = json.loads(m.group(2))
    except OSError:
        return {}
    return header


def parse_lua_table_entries(text, section_name):
    """Extract named entries from a lua table array like Functions = { ... }."""
    # Find the section. Anchored to one-tab indentation so an outer table
    # whose name merely ends in the section name (local EncounterEvents = {)
    # cannot match.
    pattern = rf'\n\t{section_name}\s*=\s*\{{(.*?)\n\t\}},'
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return []

    section_text = match.group(1)
    entries = []

    # Split on top-level table entries
    entry_pattern = re.compile(r'\{\s*\n(.*?)\n\t\t\}', re.DOTALL)
    for entry_match in entry_pattern.finditer(section_text):
        entry_text = entry_match.group(1)
        entry = parse_entry(entry_text)
        if entry and entry.get("name"):
            entries.append(entry)

    return entries


def parse_entry(text):
    """Parse a single lua table entry into a dict."""
    entry = {}

    # Name
    m = re.search(r'Name\s*=\s*"([^"]+)"', text)
    if m:
        entry["name"] = m.group(1)

    # LiteralName (used by events, e.g., UNIT_HEALTH)
    m = re.search(r'LiteralName\s*=\s*"([^"]+)"', text)
    if m:
        entry["literal_name"] = m.group(1)

    # Type (for tables: Structure, Enumeration, Constants)
    m = re.search(r'Type\s*=\s*"([^"]+)"', text)
    if m:
        entry["type"] = m.group(1)

    # Taint/secret restrictions on function arguments
    m = re.search(r'SecretArguments\s*=\s*"([^"]+)"', text)
    if m:
        entry["secret_arguments"] = m.group(1)

    # Failure behavior (for predicates)
    m = re.search(r'FailureMode\s*=\s*"([^"]+)"', text)
    if m:
        entry["failure_mode"] = m.group(1)

    # Entry-level boolean flags (RequiresValidActionSlot = true, ...) — the
    # ones matching a predicate name become the function's preconditions.
    flags = re.findall(r'\n\t\t\t([A-Za-z]\w*)\s*=\s*true,', text)
    if flags:
        entry["flags"] = flags

    # Arguments
    args = parse_sub_fields(text, "Arguments")
    if args:
        entry["arguments"] = args

    # Returns
    rets = parse_sub_fields(text, "Returns")
    if rets:
        entry["returns"] = rets

    # Payload (for events)
    payload = parse_sub_fields(text, "Payload")
    if payload:
        entry["payload"] = payload

    # Fields (for structures and enums)
    fields = parse_sub_fields(text, "Fields")
    if fields:
        entry["fields"] = fields

    return entry


FIELD_LINE = re.compile(r'\{\s*Name\s*=\s*"([^"]+)"(.*)')
FIELD_TYPE = re.compile(r'Type\s*=\s*"([^"]+)"')
FIELD_INNER_TYPE = re.compile(r'InnerType\s*=\s*"([^"]+)"')
FIELD_NILABLE = re.compile(r'Nilable\s*=\s*(true|false)')
FIELD_ENUM_VALUE = re.compile(r'EnumValue\s*=\s*(-?\d+)')
FIELD_MIXIN = re.compile(r'Mixin\s*=\s*"([^"]+)"')
FIELD_DEFAULT = re.compile(r'Default\s*=\s*([^,}\s]+)')


def parse_sub_fields(text, field_name):
    """Parse a nested array of field entries.

    Handles both regular fields ({Name, Type, Nilable}) and enum fields
    ({Name, Type, EnumValue}), which have no Nilable key.
    """
    pattern = rf'{field_name}\s*=\s*\{{(.*?)\n\t\t\t\}}'
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return []

    fields = []
    for line in match.group(1).splitlines():
        fm = FIELD_LINE.search(line)
        if not fm:
            continue
        rest = fm.group(2)
        field = {"name": fm.group(1)}

        tm = FIELD_TYPE.search(rest)
        im = FIELD_INNER_TYPE.search(rest)
        if tm:
            field["type"] = f"table<{im.group(1)}>" if im else tm.group(1)

        nm = FIELD_NILABLE.search(rest)
        if nm:
            field["nilable"] = nm.group(1) == "true"

        em = FIELD_ENUM_VALUE.search(rest)
        if em:
            field["enum_value"] = int(em.group(1))

        mm = FIELD_MIXIN.search(rest)
        if mm:
            field["mixin"] = mm.group(1)

        dm = FIELD_DEFAULT.search(rest)
        if dm:
            field["default"] = dm.group(1)

        fields.append(field)

    return fields


def parse_doc_file(filepath):
    """Parse a single documentation lua file."""
    text = filepath.read_text(encoding="utf-8", errors="replace")

    system = {}

    # System name
    m = re.search(r'Name\s*=\s*"([^"]+)"', text)
    if m:
        system["name"] = m.group(1)

    # Namespace
    m = re.search(r'Namespace\s*=\s*"([^"]+)"', text)
    if m:
        system["namespace"] = m.group(1)

    system["file"] = filepath.name
    system["functions"] = parse_lua_table_entries(text, "Functions")
    system["events"] = parse_lua_table_entries(text, "Events")
    system["tables"] = parse_lua_table_entries(text, "Tables")
    system["predicates"] = parse_lua_table_entries(text, "Predicates")

    return system


def add_entry(lookup, name, entry):
    """Insert into a lookup, keeping every entry when names collide.

    The same unqualified name (GetName, GetInfo, ...) exists in several
    namespaces. A plain dict assignment would silently keep only the last
    one; collisions become a list instead.
    """
    existing = lookup.get(name)
    if existing is None:
        lookup[name] = entry
    elif isinstance(existing, list):
        existing.append(entry)
    else:
        lookup[name] = [existing, entry]


def build_index(docs_dir):
    """Build the full index from all documentation files."""
    systems = []
    function_lookup = {}
    event_lookup = {}
    table_lookup = {}
    predicate_lookup = {}
    empty_files = []

    for lua_file in sorted(docs_dir.glob("*.lua")):
        system = parse_doc_file(lua_file)
        systems.append(system)

        parsed = (len(system["functions"]) + len(system["events"])
                  + len(system["tables"]) + len(system["predicates"]))
        # An entry-level Name sits at three tabs; the system's own Name field
        # at one tab must not trigger the warning.
        text = lua_file.read_text(encoding="utf-8", errors="replace")
        if parsed == 0 and '\n\t\t\tName = "' in text:
            empty_files.append(lua_file.name)

        ns = system.get("namespace", "")
        sys_name = system.get("name", "")

        # Predicate names defined in this file, so function flags can be
        # recognized as preconditions.
        file_predicates = {p["name"] for p in system["predicates"]}
        for pred in system["predicates"]:
            add_entry(predicate_lookup, pred["name"], {
                "system": sys_name,
                "file": system["file"],
                "failure_mode": pred.get("failure_mode", ""),
            })

        for func in system["functions"]:
            fname = func["name"]
            # Build the qualified name (e.g., C_Map.GetMapInfo)
            qualified = f"{ns}.{fname}" if ns else fname
            entry = {
                "system": sys_name,
                "namespace": ns,
                "qualified_name": qualified,
                "file": system["file"],
                "arguments": func.get("arguments", []),
                "returns": func.get("returns", []),
            }
            if "secret_arguments" in func:
                entry["secret_arguments"] = func["secret_arguments"]
            preconditions = [f for f in func.get("flags", []) if f in file_predicates]
            if preconditions:
                entry["preconditions"] = preconditions
            add_entry(function_lookup, fname, entry)

        for event in system["events"]:
            # Index by LiteralName (UNIT_HEALTH) and camelCase name
            literal = event.get("literal_name", "")
            camel = event["name"]
            entry = {
                "system": sys_name,
                "file": system["file"],
                "literal_name": literal,
                "name": camel,
                "payload": event.get("payload", []),
            }
            if literal:
                add_entry(event_lookup, literal, entry)
            add_entry(event_lookup, camel, entry)

        for tbl in system["tables"]:
            add_entry(table_lookup, tbl["name"], {
                "system": sys_name,
                "file": system["file"],
                "type": tbl.get("type", ""),
                "fields": tbl.get("fields", []),
            })

    if empty_files:
        print(f"Warning: {len(empty_files)} doc file(s) contain entries but "
              f"parsed to nothing — the export format may have changed:",
              file=sys.stderr)
        for name in empty_files[:5]:
            print(f"  {name}", file=sys.stderr)
        if len(empty_files) > 5:
            print(f"  ... and {len(empty_files) - 5} more", file=sys.stderr)

    def total(lookup):
        return sum(len(v) if isinstance(v, list) else 1 for v in lookup.values())

    return {
        "builder_version": BUILDER_VERSION,
        "generated_from": str(docs_dir),
        "source_version": detect_source_version(docs_dir),
        "source_fingerprint": fingerprint(docs_dir),
        "generated_on": date.today().isoformat(),
        "total_systems": len(systems),
        "total_functions": total(function_lookup),
        "total_events": total(event_lookup),
        "total_tables": total(table_lookup),
        "total_predicates": total(predicate_lookup),
        "functions": function_lookup,
        "events": event_lookup,
        "tables": table_lookup,
        "predicates": predicate_lookup,
        "systems": {s["name"]: {
            "namespace": s.get("namespace", ""),
            "file": s["file"],
            "function_count": len(s["functions"]),
            "event_count": len(s["events"]),
            "table_count": len(s["tables"]),
        } for s in systems if s.get("name")},
    }


def serialize_index(index):
    """Write valid JSON with one complete entry per line.

    A single Grep for '"EntryName":' then returns the whole entry — no
    context lines needed — which is how the skill consumes this file.
    """
    lines = ["{"]
    scalars = [k for k, v in index.items() if not isinstance(v, dict)]
    sections = [k for k, v in index.items() if isinstance(v, dict)]

    for key in scalars:
        lines.append(f"  {json.dumps(key)}: {json.dumps(index[key])},")

    for si, key in enumerate(sections):
        lines.append(f"  {json.dumps(key)}: {{")
        entries = index[key]
        for ei, (name, value) in enumerate(entries.items()):
            comma = "," if ei < len(entries) - 1 else ""
            compact = json.dumps(value, separators=(",", ":"))
            lines.append(f"    {json.dumps(name)}: {compact}{comma}")
        comma = "," if si < len(sections) - 1 else ""
        lines.append(f"  }}{comma}")

    lines.append("}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("docs_dir", nargs="?", help="path to Blizzard_APIDocumentationGenerated")
    ap.add_argument("output", nargs="?", help=f"output path (default: {DEFAULT_OUTPUT})")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--ensure", action="store_true", help="rebuild only if stale (default)")
    mode.add_argument("--check", action="store_true", help="report freshness, exit 1 if stale")
    mode.add_argument("--force", action="store_true", help="rebuild unconditionally")
    args = ap.parse_args()

    if args.docs_dir:
        docs_dir = Path(args.docs_dir).expanduser()
    else:
        docs_dir = find_docs_dir()
        if docs_dir is None:
            print(
                "Error: could not locate the wow-ui-source checkout.\n"
                "Clone it (git clone --depth 1 https://github.com/Gethe/wow-ui-source)\n"
                "and either set $WOW_UI_SOURCE to the clone path or pass the docs\n"
                "directory as an argument:\n"
                f"    python {sys.argv[0]} <clone>/{DOCS_SUBPATH}",
                file=sys.stderr,
            )
            sys.exit(1)

    output_path = Path(args.output).expanduser() if args.output else DEFAULT_OUTPUT

    if not docs_dir.is_dir():
        print(f"Error: {docs_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    header = read_index_header(output_path)
    fp = fingerprint(docs_dir)
    fresh = (
        header.get("source_fingerprint") == fp
        and header.get("builder_version") == BUILDER_VERSION
    )

    if args.check:
        if fresh:
            print(f"FRESH  {output_path}  ({header.get('source_version', '?')}, "
                  f"generated {header.get('generated_on', '?')})")
            sys.exit(0)
        print(f"STALE  {output_path}  -- run: python3 {Path(sys.argv[0]).name} --ensure")
        sys.exit(1)

    if fresh and not args.force:
        print(f"FRESH  index up to date ({header.get('total_functions', '?')} functions, "
              f"source {header.get('source_version', '?')}) -- nothing to do")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)

    index = build_index(docs_dir)
    output_path.write_text(serialize_index(index), encoding="utf-8")

    print(f"Index written to {output_path}")
    print(f"  Source:    {index['source_version']}")
    print(f"  Systems:   {index['total_systems']}")
    print(f"  Functions: {index['total_functions']}")
    print(f"  Events:    {index['total_events']}")
    print(f"  Tables:    {index['total_tables']}")


if __name__ == "__main__":
    main()
