#!/usr/bin/env python3
"""Validate the EllesmereUI index against the source it was built from.

Two independent properties, because they fail in different ways:

  precision -- every record points at a line that actually contains what the
               record claims. A stale or off-by-one line number is worse than
               no index at all.
  recall    -- every named function declaration in real code has a record.
               Extractor regexes silently lose coverage when the codebase
               adopts a new idiom (a wrapped parameter list, a new defaults
               table naming convention), and nothing else would notice.

Run after changing the extractor, or after a refactor large enough that you
want proof the index still sees everything.

    validate_index.py            exit 0 if clean, 1 if any check fails
    validate_index.py --verbose  list every failure rather than a sample
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_index import INDEX_DIR, iter_lua, mask_lua  # noqa: E402

FUNCTION_KW = re.compile(r"\bfunction\b")
ANON_FUNCTION = re.compile(r"\bfunction\s*\(")

# Rebuilt from a symbol record, not shared with the builder, so a fault in the
# builder's own pattern cannot validate itself.
def caller_pattern(row: dict) -> re.Pattern:
    name = re.escape(row["name"])
    if row["kind"] == "method":
        owner = re.escape(row["owner"])
        forms = [rf"(?:{owner}|self)[ \t]*:[ \t]*{name}[ \t]*\("]
    elif row["kind"] == "field":
        forms = [rf"{re.escape(row['owner'])}[ \t]*\.[ \t]*{name}[ \t]*\("]
    else:
        forms = [rf"(?<![\w.:]){name}[ \t]*\("]
    # A definition bound onto a shared table is called through that name too,
    # and the row has to say so -- otherwise the caller list cites lines that
    # do not mention the function, which is indistinguishable from a bad edge.
    for alias in row.get("aliases", ()):
        if "." in alias:
            recv, field = alias.rsplit(".", 1)
            forms.append(rf"{re.escape(recv)}[ \t]*\.[ \t]*{re.escape(field)}[ \t]*\(")
        else:
            forms.append(rf"(?<![\w.:]){re.escape(alias)}[ \t]*\(")
    return re.compile("|".join(forms))


# `EllesmereUI.Foo = Foo` at file scope, read a line at a time -- the builder
# scans offsets across one masked string, so agreement is evidence.
EXPORT_LINE = re.compile(
    r"^([A-Za-z_][\w.]*)[ \t]*\.[ \t]*([A-Za-z_]\w*)[ \t]*=[ \t]*([A-Za-z_]\w*)[ \t]*$"
)
# `local ADDON_NAME, ns = ...` -- the one table that really is per-addon.
VARARG_LOCAL = re.compile(r"^[ \t]*local[ \t]+([\w, \t]+)=[ \t]*\.\.\.")

# A colour written whole on one line: `castbarFillColor = { r = .., g = .., b = .. }`.
# Matched a line at a time against the mask, where the builder decides the same
# question by walking the constructor's entries from its opening brace.
COLOUR_LINE = re.compile(
    r"^[ \t]*([A-Za-z_]\w*)[ \t]*=[ \t]*\{[ \t]*"
    r"r[ \t]*=[^,{}]+,[ \t]*g[ \t]*=[^,{}]+,[ \t]*b[ \t]*=[^,{}]+"
    r"(?:,[ \t]*a[ \t]*=[^,{}]+)?[ \t]*,?[ \t]*\}"
)


# A bare `name(` call, with any receiver captured -- an independent
# line-oriented scan, where the builder works from offsets into one masked
# string for the whole file.
CALL_ON_LINE = re.compile(r"([A-Za-z_][\w.]*[ \t]*[.:][ \t]*)?\b([A-Za-z_]\w*)[ \t]*\(")

# The opening line of a defaults table, whole or one branch of one. Matched a
# line at a time, where the builder scans offsets across the whole file.
DEFAULTS_LINE = re.compile(
    r"^[ \t]*(?:local[ \t]+)?((?:[A-Za-z_]\w*)(?:\.[A-Za-z_]\w*)*(?:\[[^\]]+\])*)[ \t]*=[ \t]*\{[ \t]*$"
)
DEFAULTS_COMPONENT = re.compile(r"(?i)(?:^|\.)\w*defaults(?:\[|$|\.)")
# A named key inside a table. A table named `defaults` holding only positional
# entries -- a CVar list, a colour triple -- declares no key to look up, so it
# is not a miss when it produces no records.
NAMED_KEY = re.compile(r"^[ \t]*(?:[A-Za-z_]\w*|\[[ \t]*[\"'][^\"']+[\"'][ \t]*\])[ \t]*=(?!=)")


class Checker:
    def __init__(self, root: Path, verbose: bool):
        self.root = root
        self.verbose = verbose
        self.failures = 0
        self._lines: dict[str, list[str]] = {}

    def lines(self, rel: str) -> list[str]:
        if rel not in self._lines:
            self._lines[rel] = (self.root / rel).read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        return self._lines[rel]

    def at(self, site: str) -> str:
        rel, _, line = site.rpartition(":")
        try:
            return self.lines(rel)[int(line) - 1]
        except (IndexError, ValueError, OSError):
            return ""

    def report(self, label: str, total: int, bad: list[str]) -> None:
        status = "ok " if not bad else "FAIL"
        print(f"  [{status}] {label:34s} {total - len(bad):6d}/{total:6d}")
        for item in bad if self.verbose else bad[:5]:
            print(f"           {item}")
        if bad and not self.verbose and len(bad) > 5:
            print(f"           ... and {len(bad) - 5} more (--verbose for all)")
        self.failures += len(bad)


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (INDEX_DIR / name).open()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    meta_path = INDEX_DIR / "meta.json"
    if not meta_path.is_file():
        sys.exit("No index found. Run build_index.py --ensure first.")
    meta = json.loads(meta_path.read_text())
    root = Path(meta["addon_root"])
    c = Checker(root, args.verbose)

    print(f"Validating index of {root}")
    print(f"  built {meta['built_at']} from {meta['git'].get('short', '?')}\n")

    print("PRECISION -- records land on the line they name")
    for fname, field in (("symbols.jsonl", "name"), ("slash.jsonl", "command")):
        rows = load(fname)
        bad = [
            f"{r['file']}:{r['line']} claims {field}={r[field]!r}"
            for r in rows
            if r[field] not in c.lines(r["file"])[r["line"] - 1]
        ]
        c.report(fname, len(rows), bad)

    # Settings declared in a defaults table point at the declaration; keys on a
    # SavedVariables global point at their first reference instead.
    settings = load("settings.jsonl")
    bad = [
        f"{r['file']}:{r['line']} claims key={r['key']!r}"
        for r in settings
        if r["key"] not in c.lines(r["file"])[r["line"] - 1]
    ]
    c.report("settings.jsonl", len(settings), bad)

    for fname, field in (("events.jsonl", "event"), ("locale.jsonl", "key")):
        rows = load(fname)
        bad = [
            f"{s} does not contain {r[field]!r}"
            for r in rows
            for s in r["sites"]
            if r[field] not in c.at(s)
        ]
        c.report(f"{fname} sites", sum(len(r["sites"]) for r in rows), bad)

    pairs = [(r, s) for r in settings for s in r.get("refs", [])]
    bad = [f"{s} does not contain {r['key']!r}" for r, s in pairs if r["key"] not in c.at(s)]
    c.report("settings refs", len(pairs), bad)

    # A caller record claims more than a line number: it claims the line calls
    # this definition through this receiver. Rebuild the expected expression
    # from the record's own fields and require the cited line to contain it,
    # which is what separates a real edge from a same-named function elsewhere.
    symbols = load("symbols.jsonl")
    pairs = [(r, s) for r in symbols for s in r.get("callers", [])]
    bad = []
    for r, s in pairs:
        if not caller_pattern(r).search(c.at(s)):
            bad.append(f"{s} does not call {r['full']!r}")
    c.report("symbols callers", len(pairs), bad)

    modules = load("modules.jsonl")
    load_order = [(m["module"], f) for m in modules for f in m["load_order"]]
    bad = [f"{mod}: {f} listed in TOC but not on disk" for mod, f in load_order
           if not (root / f).is_file()]
    c.report("modules load_order", len(load_order), bad)

    print("\nCAPS -- every truncated list says how long it really is")
    # The index caps long lists to keep a record greppable on one line. A cap
    # is fine; a cap the caller cannot see is not, because a truncated list
    # reads exactly like a complete one. Each capped field must ship a count,
    # and the count must never be smaller than the list it describes.
    for fname, field, count_field, cap in (
        ("settings.jsonl", "refs", "ref_count", 60),
        ("settings.jsonl", "options_refs", "options_ref_count", 10),
        ("events.jsonl", "sites", "count", 40),
        ("locale.jsonl", "sites", "count", 40),
    ):
        rows = load(fname)
        bad = []
        for r in rows:
            if count_field not in r:
                bad.append(f"{fname}: {field} has no {count_field}")
                break
            if len(r[field]) > cap:
                bad.append(f"{fname}: {field} of {len(r[field])} exceeds its cap of {cap}")
            elif r[count_field] < len(r[field]):
                bad.append(f"{fname}: {count_field}={r[count_field]} "
                           f"< {len(r[field])} listed")
        c.report(f"{fname} {field}", len(rows), bad)

    # Callers are capped the same way, and carry one extra invariant: a symbol
    # states exactly one of three things -- the call sites, the number of
    # definitions it could not be told apart from, or that its callers cannot
    # be resolved at all. Never two, and never none. A row with no caller field
    # would read as "nothing calls this", which is a different claim from
    # either "this name is ambiguous" or "this is reached through a table".
    #
    # Two of these being present at once is the failure worth naming: a
    # `tablefield` that also carried `caller_count: 0` satisfied a check for
    # "has a list" while telling every reader that 6,951 functions were dead.
    bad = []
    for r in symbols:
        states = [k for k in ("callers", "caller_ambiguity", "caller_unresolved") if k in r]
        if len(states) != 1:
            bad.append(f"{r['file']}:{r['line']} {r['full']}: states {states or 'nothing'}")
            continue
        state = states[0]
        if state == "callers":
            if "caller_count" not in r:
                bad.append(f"{r['file']}:{r['line']} {r['full']}: no caller_count")
            elif len(r["callers"]) > 40:
                bad.append(f"{r['file']}:{r['line']} {r['full']}: "
                           f"{len(r['callers'])} callers exceeds its cap of 40")
            elif r["caller_count"] < len(r["callers"]):
                bad.append(f"{r['file']}:{r['line']} {r['full']}: caller_count="
                           f"{r['caller_count']} < {len(r['callers'])} listed")
        elif state == "caller_ambiguity":
            if r["caller_ambiguity"] < 2:
                bad.append(f"{r['file']}:{r['line']} {r['full']}: "
                           f"caller_ambiguity={r['caller_ambiguity']} is not ambiguous")
        elif "caller_count" in r:
            bad.append(f"{r['file']}:{r['line']} {r['full']}: "
                       f"caller_unresolved with a caller_count of {r['caller_count']}")
    c.report("symbols.jsonl callers", len(symbols), bad)

    print("\nRECALL -- every named declaration has a record")

    # Caller counts are checked on the one class whose scope is exactly known:
    # a Lua local lives in one chunk, and one file is one chunk, so every call
    # to it is in its own file and an independent per-file count must agree
    # exactly. The other kinds depend on resolution rules a second
    # implementation could only restate, so a disagreement there would prove
    # nothing -- this class is where a scan regression actually shows up.
    # An exported local is excluded: it is reached under a second name, so its
    # own file no longer holds every call and exact agreement is not the
    # invariant. The axis below covers those instead.
    by_file: dict[str, list[dict]] = {}
    for r in symbols:
        if r["kind"] == "local" and "callers" in r and "aliases" not in r:
            by_file.setdefault(r["file"], []).append(r)
    total = 0
    bad = []
    for rel, rows in by_file.items():
        mask = mask_lua((root / rel).read_text(encoding="utf-8", errors="replace"))
        # A record counts distinct call *lines*, so the scan must too -- two
        # calls on one line are one citation.
        seen: dict[str, set[int]] = {}
        for lineno, line in enumerate(mask.splitlines(), 1):
            for m in CALL_ON_LINE.finditer(line):
                if m.group(1) or line[m.start(2) - 1 : m.start(2)] in (".", ":"):
                    continue
                seen.setdefault(m.group(2), set()).add(lineno)
        for r in rows:
            total += 1
            # `local function Foo(` looks like a call to Foo on its own line.
            expect = len(seen.get(r["name"], set()) - {r["line"]})
            if expect != r["caller_count"]:
                bad.append(f"{rel}:{r['line']} {r['name']}: index says "
                           f"{r['caller_count']}, scan finds {expect}")
    c.report("local caller counts", total, bad)

    # The other half of that invariant, and the one that silently rotted: every
    # cross-module helper here is a file-local bound onto a shared table, so
    # resolving only the definition's own name credits its own file and drops
    # the rest of the suite. The count stays non-zero, which is why nothing
    # noticed -- `BuildColorSwatch` read 11 callers against a true 301.
    #
    # Re-derive the export lines and the calls through them with a
    # line-oriented scan, and require every one to be cited.
    lua = sorted(iter_lua(root))
    module_names = {m["module"] for m in load("modules.jsonl")}
    private: set[str] = set()
    exports: dict[str, list[tuple[str, str]]] = {}
    masks: dict[str, list[str]] = {}
    for rel, path in lua:
        masks[rel] = mask_lua(path.read_text(encoding="utf-8", errors="replace")).splitlines()
        for line in masks[rel]:
            m = VARARG_LOCAL.match(line)
            if m:
                private.update(p.strip() for p in m.group(1).split(",") if p.strip())
            m = EXPORT_LINE.match(line)
            if m:
                exports.setdefault(f"{m.group(1)}.{m.group(2)}", []).append(
                    (rel, m.group(3))
                )

    # Where a call through each alias actually is.
    wanted = {a for a, claims in exports.items() if len(claims) == 1}
    sites: dict[str, set[str]] = {}
    for rel, _ in lua:
        for lineno, line in enumerate(masks[rel], 1):
            for m in CALL_ON_LINE.finditer(line):
                if not m.group(1):
                    continue
                expr = m.group(1).rstrip(" \t")[:-1].rstrip(" \t") + "." + m.group(2)
                if m.group(1).rstrip(" \t").endswith(".") and expr in wanted:
                    sites.setdefault(expr, set()).add(f"{rel}:{lineno}")

    def module_of(rel: str) -> str:
        head = rel.split("/", 1)[0]
        return head if head in module_names else "EllesmereUI"

    by_def = {(r["file"], r["name"]): r for r in symbols if r["kind"] == "local"}
    total = 0
    bad = []
    for alias in sorted(wanted):
        rel, local = exports[alias][0]
        row = by_def.get((rel, local))
        if row is None or "callers" not in row:
            continue
        total += 1
        found = sites.get(alias, set())
        if alias.split(".", 1)[0] in private:
            # Bound onto the file's own addon table, so the export reaches only
            # this module; another module's same-named table is another object.
            found = {s for s in found if module_of(s.rsplit(":", 1)[0]) == row["module"]}
        if len(row["callers"]) >= 40:
            if row["caller_count"] < len(found):
                bad.append(f"{rel}:{row['line']} {local}: caller_count="
                           f"{row['caller_count']} < {len(found)} calls to {alias}")
        else:
            missing = found - set(row["callers"]) - {f"{rel}:{row['line']}"}
            if missing:
                bad.append(f"{rel}:{row['line']} {local}: {len(missing)} call(s) "
                           f"to {alias} not cited, e.g. {sorted(missing)[0]}")
    c.report("exported local callers", total, bad)

    # Every defaults table in the source must have produced records. A settings
    # key can only be looked up if its declaration was seen, and a declaration
    # written in a shape the extractor does not match fails silently: the
    # lookup returns nothing, which reads exactly like "this key does not
    # exist". That is how the whole per-bar namespace -- `alwaysShowButtons`,
    # `clickThrough`, `barVisibility` -- stayed invisible while the index
    # reported 3,876 settings and looked healthy.
    declared: dict[tuple[str, str], int] = {}
    defaults_span: dict[str, list[tuple[int, int]]] = {}
    for rel, path in iter_lua(root):
        lines = mask_lua(path.read_text(encoding="utf-8", errors="replace")).splitlines()
        for lineno, line in enumerate(lines, 1):
            m = DEFAULTS_LINE.match(line)
            if not m or not DEFAULTS_COMPONENT.search(m.group(1)):
                continue
            # Walk to the closing brace by depth, counting a line at a time --
            # the builder matches braces by offset, so this stays a second
            # implementation rather than a restatement.
            depth, named, end = 0, False, lineno
            for offset, body in enumerate(lines[lineno - 1 :]):
                named = named or bool(NAMED_KEY.match(body))
                depth += body.count("{") - body.count("}")
                if depth <= 0:
                    end = lineno + offset
                    break
            if named:
                declared.setdefault((rel, m.group(1)), lineno)
                defaults_span.setdefault(rel, []).append((lineno, end))
    covered = {(r["file"], r["table"]) for r in settings if r["store"] == "defaults"}
    bad = [f"{rel}:{declared[(rel, tbl)]} defaults table {tbl!r} has no records"
           for rel, tbl in sorted(declared) if (rel, tbl) not in covered]
    c.report("defaults tables indexed", len(declared), bad)

    # A colour is one setting and has to be indexed under its own name. Walking
    # into `{ r = .., g = .., b = .. }` produces leaves keyed `r`, `g` and `b`
    # instead: the colour's name answers nothing, and each leaf inherits the
    # references of a one-letter identifier -- 787 records, 19% of the index,
    # every one of them unfindable and carrying the wrong `refs`. Colours are
    # the most common setting in a UI addon, so this is checked by name.
    keys_by_file: dict[str, set[str]] = {}
    for r in settings:
        keys_by_file.setdefault(r["file"], set()).add(r["key"])
    total = len(settings)
    bad = [f"{r['file']}:{r['line']} record keyed {r['key']!r} -- a colour channel, "
           "not a setting" for r in settings if r["key"] in ("r", "g", "b", "a")]
    for rel, spans in defaults_span.items():
        lines = mask_lua(
            (root / rel).read_text(encoding="utf-8", errors="replace")
        ).splitlines()
        for lineno, line in enumerate(lines, 1):
            # Only inside a defaults table. A colour elsewhere -- the class
            # colour table, a theme palette -- is a constant, not a setting.
            if not any(a <= lineno <= b for a, b in spans):
                continue
            m = COLOUR_LINE.match(line)
            if not m:
                continue
            total += 1
            if m.group(1) not in keys_by_file.get(rel, ()):
                bad.append(f"{rel}:{lineno} colour {m.group(1)!r} has no record")
    c.report("colours indexed by name", total, bad)

    indexed = {(r["file"], r["line"]) for r in symbols}
    total = 0
    missed: list[str] = []
    for rel, path in iter_lua(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        mask = mask_lua(text)
        newlines = [m.start() for m in re.finditer("\n", text)]
        source = text.splitlines()
        for m in FUNCTION_KW.finditer(mask):
            # Anonymous callbacks have no name to index and are not misses.
            if ANON_FUNCTION.match(mask, m.start()):
                continue
            total += 1
            line = bisect.bisect_right(newlines, m.start()) + 1
            if (rel, line) not in indexed:
                missed.append(f"{rel}:{line}  {source[line - 1].strip()[:100]}")
    c.report("named function declarations", total, missed)

    print()
    if c.failures:
        print(f"FAILED -- {c.failures} problem(s)")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
