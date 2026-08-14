#!/usr/bin/env python3
"""Parse Blizzard_APIDocumentationGenerated lua files into a JSON index.

Usage:
    generate_index.py [docs_dir] [output_path] [--ensure|--check|--force] [--with-docs]

Modes:
    --ensure   rebuild only if the docs export changed (default)
    --check    report FRESH/STALE, exit 1 if stale
    --force    rebuild unconditionally

Blizzard's prose notes:
    --with-docs keeps them. They are left out by default because the index in
    this repository is redistributed, and the notes are the one part of the
    export that is Blizzard's own writing rather than a fact about the
    interface. Names, signatures, payloads, enum members, flags and the
    secret markers are facts and are always kept.

    --with-docs writes references/api_index.local.json, which is gitignored,
    so a local build with the notes never replaces the file this repository
    ships. The skill reads the local one when it is there.

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

# 6 added the `documentation` header field, so an index built by 5 rebuilds
# rather than being read as one that deliberately carries no notes.
BUILDER_VERSION = 6

DOCS_SUBPATH = Path("Interface/AddOns/Blizzard_APIDocumentationGenerated")
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "references" / "api_index.json"
# --with-docs writes here instead, so a local build carrying Blizzard's prose
# never lands on top of the file this repository ships. Gitignored.
LOCAL_OUTPUT = DEFAULT_OUTPUT.with_name("api_index.local.json")


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


# Blizzard's own prose note on a function, event, structure or single field.
# It carries semantics no type signature can: which event a field is only
# trustworthy in, what a nil means, which category decides a duration.
# Nearly every note is written on one line, but a lua string may carry a
# `\` line continuation, so these span lines rather than stopping at the
# first newline and losing the note.
DOC_CLAUSE = re.compile(r'Documentation\s*=\s*\{(.*)\}', re.S)
DOC_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"', re.S)
# An entry's own note sits at three tabs; a field's note is inline on the
# field line at four, so anchoring keeps the two from being read as one.
# The close is pinned to the end of a line so a `}` inside the prose does
# not end the clause early.
ENTRY_DOC = re.compile(r'^\t\t\tDocumentation\s*=\s*\{.*?\}\s*,?[ \t]*$', re.M | re.S)
# A lua string continued with a trailing backslash holds a real newline.
DOC_CONTINUATION = re.compile(r'\\\r?\n')


def parse_documentation(text):
    """Pull the note strings out of a `Documentation = { "..." }` clause."""
    m = DOC_CLAUSE.search(text)
    if not m:
        return []
    return [DOC_CONTINUATION.sub("\n", sm.group(1))
            .replace('\\"', '"').replace("\\\\", "\\")
            for sm in DOC_STRING.finditer(m.group(1))]


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

    # A few functions carry their own Namespace, different from the system's
    # (C_StringUtil inside Localization). It wins over the file-level one.
    m = re.search(r'Namespace\s*=\s*"([^"]+)"', text)
    if m:
        entry["namespace"] = m.group(1)

    # Taint/secret restrictions on function arguments
    m = re.search(r'SecretArguments\s*=\s*"([^"]+)"', text)
    if m:
        entry["secret_arguments"] = m.group(1)

    # The whole return set is readable by a tainted addon. Promoted out of
    # `flags` because "can I read this in combat" is asked of a function far
    # more often than any other flag it carries.
    if re.search(r'\bReturnsNeverSecret\s*=\s*true', text):
        entry["returns_never_secret"] = True

    # Blizzard's prose note on the entry itself.
    m = ENTRY_DOC.search(text)
    if m:
        notes = parse_documentation(m.group(0))
        if notes:
            entry["documentation"] = notes

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

    # Fields (for structures and enums). A Constants table writes the same
    # shape under a different key -- `Values = { { Name, Type, Value } }` --
    # so reading Fields alone indexed all 55 of them empty, and a lookup for
    # a constant such as GLOBAL_RECOVERY_CATEGORY found nothing at all.
    fields = parse_sub_fields(text, "Fields") or parse_sub_fields(text, "Values")
    if fields:
        entry["fields"] = fields

    return entry


FIELD_LINE = re.compile(r'\{\s*Name\s*=\s*"([^"]+)"(.*)')
FIELD_TYPE = re.compile(r'Type\s*=\s*"([^"]+)"')
FIELD_INNER_TYPE = re.compile(r'InnerType\s*=\s*"([^"]+)"')
FIELD_NILABLE = re.compile(r'Nilable\s*=\s*(true|false)')
FIELD_ENUM_VALUE = re.compile(r'EnumValue\s*=\s*(-?\d+)')
# A Constants member holds its value here. The lookbehind keeps EnumValue and
# MaxValue out. Most are plain numbers, but ~35 are strings, booleans, hex, or
# a reference to another constant, so the raw token is kept when it is not a
# decimal number rather than dropping the member.
FIELD_VALUE = re.compile(r'(?<![A-Za-z])Value\s*=\s*([^,}]+)')
FIELD_MIXIN = re.compile(r'Mixin\s*=\s*"([^"]+)"')
FIELD_DEFAULT = re.compile(r'Default\s*=\s*([^,}\s]+)')
# Blizzard marks the fields a tainted addon may read in restricted combat. It is
# the field-level half of a function's SecretArguments, and the question an
# addon asks most often about a structure: which of these can I compare?
FIELD_NEVER_SECRET = re.compile(r'NeverSecret\s*=\s*(true|false)')


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
        # The note is prose and may itself contain `Type = "..."` or a comma,
        # so it is split off before the scalar keys are read out of the line.
        rest, _, doc_clause = fm.group(2).partition("Documentation")
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
        else:
            vm = FIELD_VALUE.search(rest)
            if vm:
                raw = vm.group(1).strip()
                try:
                    field["value"] = int(raw, 0)
                except ValueError:
                    try:
                        field["value"] = float(raw)
                    except ValueError:
                        field["value"] = raw.strip('"')

        mm = FIELD_MIXIN.search(rest)
        if mm:
            field["mixin"] = mm.group(1)

        dm = FIELD_DEFAULT.search(rest)
        if dm:
            field["default"] = dm.group(1)

        sm = FIELD_NEVER_SECRET.search(rest)
        if sm:
            field["never_secret"] = sm.group(1) == "true"

        if doc_clause:
            notes = parse_documentation("Documentation" + doc_clause)
            if notes:
                field["documentation"] = notes

        fields.append(field)

    return fields


def parse_doc_file(filepath):
    """Parse a single documentation lua file."""
    text = filepath.read_text(encoding="utf-8", errors="replace")

    system = {}

    # System name and namespace. Both sit at one tab; an entry's own Name sits
    # at three. 207 of the 592 export files (every *Constants and *Shared one)
    # declare no system Name, so an unanchored search there fell through to the
    # first table's name and stamped it on every entry in the file -- which is
    # how SpellCooldownConsts came to report itself as ConfirmationPromptUIType.
    # The variable handed to AddDocumentationTable is the reliable fallback.
    m = re.search(r'^\tName\s*=\s*"([^"]+)"', text, re.M)
    if m:
        system["name"] = m.group(1)
    else:
        m = re.search(r'AddDocumentationTable\((\w+)\)', text)
        if m:
            system["name"] = m.group(1)

    m = re.search(r'^\tNamespace\s*=\s*"([^"]+)"', text, re.M)
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


def strip_documentation(lookup):
    """Drop every prose note from a built lookup, in place. Returns the count.

    Applied to the sections rather than to the whole index, so the header
    field of the same name survives.
    """
    removed = 0

    def walk(node):
        nonlocal removed
        if isinstance(node, dict):
            if "documentation" in node:
                del node["documentation"]
                removed += 1
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(lookup)
    return removed


def build_index(docs_dir, include_docs=False):
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
            pred_entry = {
                "system": sys_name,
                "file": system["file"],
                "failure_mode": pred.get("failure_mode", ""),
            }
            # A predicate's note says what the restriction actually is --
            # which unit tokens may be compared, what "declassified" means.
            # The failure mode alone says only that a call went wrong.
            if pred.get("documentation"):
                pred_entry["documentation"] = pred["documentation"]
            add_entry(predicate_lookup, pred["name"], pred_entry)

        for func in system["functions"]:
            fname = func["name"]
            # Build the qualified name (e.g., C_Map.GetMapInfo). A function
            # that declares its own namespace overrides the file's.
            fns = func.get("namespace", ns)
            qualified = f"{fns}.{fname}" if fns else fname
            entry = {
                "system": sys_name,
                "namespace": fns,
                "qualified_name": qualified,
                "file": system["file"],
                "arguments": func.get("arguments", []),
                "returns": func.get("returns", []),
            }
            if func.get("documentation"):
                entry["documentation"] = func["documentation"]
            if "secret_arguments" in func:
                entry["secret_arguments"] = func["secret_arguments"]
            if func.get("returns_never_secret"):
                entry["returns_never_secret"] = True
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
            if event.get("documentation"):
                entry["documentation"] = event["documentation"]
            if literal:
                add_entry(event_lookup, literal, entry)
            add_entry(event_lookup, camel, entry)

        for tbl in system["tables"]:
            entry = {
                "system": sys_name,
                "file": system["file"],
                "type": tbl.get("type", ""),
                "fields": tbl.get("fields", []),
            }
            # A CallbackType declares its signature under Arguments rather than
            # Fields. Dropping it left 13 callback types indexed as empty, and
            # the signature is the whole reason to look one up.
            if tbl.get("arguments"):
                entry["arguments"] = tbl["arguments"]
            if tbl.get("documentation"):
                entry["documentation"] = tbl["documentation"]
            add_entry(table_lookup, tbl["name"], entry)

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

    # Counted so a reformat of the export shows up as a coverage drop rather
    # than as notes that quietly stop being extracted.
    documented = 0
    for system in systems:
        for section in ("functions", "events", "tables", "predicates"):
            for item in system[section]:
                documented += 1 if item.get("documentation") else 0
                for field_list in ("arguments", "returns", "payload", "fields"):
                    documented += sum(1 for f in item.get(field_list, [])
                                      if f.get("documentation"))

    stored = documented
    if not include_docs:
        stored = 0
        for lookup in (function_lookup, event_lookup, table_lookup, predicate_lookup):
            strip_documentation(lookup)

    return {
        "builder_version": BUILDER_VERSION,
        # The subpath, not the absolute one: the checkout a build ran against
        # is identified by source_version and source_fingerprint, so the rest
        # of the path says nothing except whose machine it was.
        "generated_from": DOCS_SUBPATH.as_posix(),
        "source_version": detect_source_version(docs_dir),
        "source_fingerprint": fingerprint(docs_dir),
        "generated_on": date.today().isoformat(),
        "documentation": "included" if include_docs else "omitted",
        "total_systems": len(systems),
        "total_functions": total(function_lookup),
        "total_events": total(event_lookup),
        "total_tables": total(table_lookup),
        "total_predicates": total(predicate_lookup),
        # What this file carries, then what the export offered. The two differ
        # by exactly the notes left behind, so a reader can see the cost of
        # the default and a validator can check the omission was complete.
        "total_documented": stored,
        "source_documented": documented,
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
    ap.add_argument("output", nargs="?",
                    help=f"output path (default: {DEFAULT_OUTPUT}, "
                         f"or {LOCAL_OUTPUT.name} with --with-docs)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--ensure", action="store_true", help="rebuild only if stale (default)")
    mode.add_argument("--check", action="store_true", help="report freshness, exit 1 if stale")
    mode.add_argument("--force", action="store_true", help="rebuild unconditionally")
    ap.add_argument("--with-docs", action="store_true",
                    help="keep Blizzard's prose notes (left out of the committed index)")
    args = ap.parse_args()
    wanted_docs = "included" if args.with_docs else "omitted"

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

    if args.output:
        output_path = Path(args.output).expanduser()
    else:
        output_path = LOCAL_OUTPUT if args.with_docs else DEFAULT_OUTPUT

    if not docs_dir.is_dir():
        print(f"Error: {docs_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    header = read_index_header(output_path)
    fp = fingerprint(docs_dir)
    # The notes are part of what makes an index the one that was asked for, so
    # --with-docs against a index built without them is stale, not fresh.
    fresh = (
        header.get("source_fingerprint") == fp
        and header.get("builder_version") == BUILDER_VERSION
        and header.get("documentation") == wanted_docs
    )
    rebuild = f"python3 {Path(sys.argv[0]).name} --ensure"
    if args.with_docs:
        rebuild += " --with-docs"

    if args.check:
        if fresh:
            print(f"FRESH  {output_path}  ({header.get('source_version', '?')}, "
                  f"generated {header.get('generated_on', '?')}, "
                  f"notes {header.get('documentation', '?')})")
            sys.exit(0)
        print(f"STALE  {output_path}  -- run: {rebuild}")
        sys.exit(1)

    if fresh and not args.force:
        print(f"FRESH  index up to date ({header.get('total_functions', '?')} functions, "
              f"source {header.get('source_version', '?')}) -- nothing to do")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)

    index = build_index(docs_dir, include_docs=args.with_docs)
    output_path.write_text(serialize_index(index), encoding="utf-8")

    print(f"Index written to {output_path}")
    print(f"  Source:    {index['source_version']}")
    print(f"  Systems:   {index['total_systems']}")
    print(f"  Functions: {index['total_functions']}")
    print(f"  Events:    {index['total_events']}")
    print(f"  Tables:    {index['total_tables']}")
    if args.with_docs:
        print(f"  Doc notes: {index['total_documented']}  (local build; not committed)")
    else:
        print(f"  Doc notes: omitted  ({index['source_documented']} in the export; "
              f"add --with-docs to keep them locally)")


if __name__ == "__main__":
    main()
