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

    print("\nRECALL -- every named declaration has a record")
    indexed = {(r["file"], r["line"]) for r in load("symbols.jsonl")}
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
