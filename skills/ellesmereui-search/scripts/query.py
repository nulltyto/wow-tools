#!/usr/bin/env python3
"""Answer one question against the EllesmereUI index, in one command.

The index files are JSONL and can be grepped directly, but a lookup written by
hand costs more than the grep of the source it replaces -- so the grep of the
source is what gets used, and the index sits there current and unread. This
script makes the index the cheaper option:

    query.py def SpecIndexFor
    query.py callers SpecIndexFor
    query.py setting hideUnusable
    query.py label "Hide Unusable Entries"

Freshness is checked on every run (about 0.4s), so there is no separate
build step to remember and no way to answer from a stale line number.

Output carries the caveats that belong with each record -- a capped list says
what it is capped at, a method's caller count says it is a floor rather than an
answer -- because those are the parts a reader skips when they live in prose.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import build_index  # noqa: E402

INDEX_DIR = build_index.INDEX_DIR


# --------------------------------------------------------------------------
#  Index access
# --------------------------------------------------------------------------

def ensure_index(root_arg, quiet=False):
    """Rebuild if the source moved. Returns the resolved addon root."""
    root = build_index.resolve_root(root_arg)
    meta = build_index.load_meta()
    fp, n_files, n_bytes = build_index.fingerprint(root)
    fresh = (
        meta is not None
        and meta.get("source_fingerprint") == fp
        and meta.get("builder_version") == build_index.BUILDER_VERSION
        and meta.get("addon_root") == str(root)
        and all((INDEX_DIR / f).is_file() for f in
                ("modules.jsonl", "symbols.jsonl", "settings.jsonl",
                 "locale.jsonl", "events.jsonl", "slash.jsonl"))
    )
    if not fresh:
        if not quiet:
            print("index stale -- rebuilding (~12s)", file=sys.stderr)
        build_index.build(root, fp, n_files, n_bytes)
    return root


def records(name):
    path = INDEX_DIR / (name + ".jsonl")
    if not path.is_file():
        sys.exit("no %s in the index -- run build_index.py --force" % path.name)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def find(name, field, needle, exact_first=True):
    """Exact matches on `field`, or substring matches when there are none.

    Returns (rows, matched_exactly). A fuzzy result is labelled rather than
    silently substituted -- `alwaysShow` and `alwaysShowButtons` are different
    settings, and a partial name matches the wrong one confidently.
    """
    rows = list(records(name))
    if exact_first:
        hits = [r for r in rows if r.get(field) == needle]
        if hits:
            return hits, True
    low = needle.lower()
    return [r for r in rows if low in str(r.get(field, "")).lower()], False


# --------------------------------------------------------------------------
#  Shared output helpers
# --------------------------------------------------------------------------

def emit_json(rows):
    for r in rows:
        print(json.dumps(r))


def show_list(items, total, limit, indent="  ", label="more"):
    """Print a list, and be explicit about anything not shown.

    Two different truncations can hide sites: this script's --limit, and the
    index's own per-field cap. Both are stated, because a list read as complete
    when it is a sample is the failure this index exists to prevent.
    """
    for item in items[:limit]:
        print(indent + item)
    if len(items) > limit:
        print("%s... showing %d of %d %s (--limit %d to widen)"
              % (indent, limit, len(items), label, len(items)))
    if total is not None and total > len(items):
        print("%sindex caps this list at %d; the true count is %d -- grep to see the rest"
              % (indent, len(items), total))


def fuzzy_note(matched, needle, field):
    if not matched:
        print("(no exact %s match for %r -- showing substring matches)" % (field, needle))


# --------------------------------------------------------------------------
#  Commands
# --------------------------------------------------------------------------

def cmd_def(args):
    rows, exact = find("symbols", "name", args.name)
    if not rows:
        print("no definition named %r" % args.name)
        print("The index records definitions. A handler reached only through "
              "hooksecurefunc, or a name assembled at runtime, has no record -- grep for those.")
        return 1
    fuzzy_note(exact, args.name, "name")
    if args.json:
        emit_json(rows)
        return 0
    for r in rows:
        line = "%s  %s  %s:%d" % (r["name"], r["kind"], r["file"], r["line"])
        if r.get("owner"):
            line += "  owner=%s" % r["owner"]
        if r.get("params"):
            line += "  (%s)" % r["params"]
        print(line)
        if r.get("aliases"):
            print("  also called as: %s" % ", ".join(r["aliases"]))
        if "caller_ambiguity" in r:
            print("  callers not listed: %d definitions here are indistinguishable -- grep"
                  % r["caller_ambiguity"])
        elif "caller_unresolved" in r:
            print("  callers not listed: reached through the %s that holds it, which the "
                  "definition site does not name -- grep '%s'"
                  % (r["caller_unresolved"], r["name"]))
        else:
            n = r.get("caller_count", 0)
            print("  %d caller%s%s" % (n, "" if n == 1 else "s", caller_caveat(r)))
    return 0


def caller_caveat(r):
    """The one sentence that keeps a caller count from being over-read.

    A field or method is credited only where the receiver matches, so a low
    count on one of those is unproven rather than complete. On a local or a
    global there is no receiver to rename and the count is the whole answer.
    """
    if r["kind"] in ("field", "method") and r.get("caller_count", 0) < 3:
        return "  (floor, not an answer -- %s is credited only through its own receiver; " \
               "settle it with: grep -rn '[.:]%s(' --include=*.lua)" % (r["kind"], r["name"])
    return ""


def cmd_callers(args):
    rows, exact = find("symbols", "name", args.name)
    if not rows:
        print("no definition named %r" % args.name)
        return 1
    fuzzy_note(exact, args.name, "name")
    if args.json:
        emit_json(rows)
        return 0
    for r in rows:
        print("%s  %s  %s:%d" % (r["name"], r["kind"], r["file"], r["line"]))
        if "caller_ambiguity" in r:
            print("  no list: %d definitions called the same way cannot be told apart -- grep"
                  % r["caller_ambiguity"])
            continue
        if "caller_unresolved" in r:
            print("  no list: this is a %s, invoked through whatever table holds it. The "
                  "definition site does not name that table, so bare '%s(' calls are not "
                  "it -- grep the key." % (r["caller_unresolved"], r["name"]))
            continue
        callers = r.get("callers", [])
        count = r.get("caller_count", 0)
        if not callers:
            print("  0 callers under any name the index resolves.%s" % caller_caveat(r))
            print("  Not the same as dead: check hooksecurefunc targets, runtime-built "
                  "names, and functions invoked out of a table.")
            continue
        print("  %d caller%s%s" % (count, "" if count == 1 else "s", caller_caveat(r)))
        show_list(callers, count, args.limit, label="callers")
    return 0


def cmd_setting(args):
    rows, exact = find("settings", "key", args.key)
    if not rows:
        print("no declared setting named %r" % args.key)
        print("A key that is read but never declared has no record. Grep it and read the "
              "caller's inline fallback (p.someKey or 40) -- that fallback IS the effective default.")
        print("If you started from a UI label rather than a key, try: query.py label \"<label>\"")
        return 1
    fuzzy_note(exact, args.key, "key")
    if args.json:
        emit_json(rows)
        return 0
    for r in rows:
        print("%s  store=%s  module=%s" % (r["key"], r["store"], r["module"]))
        print("  path      %s%s" % (r["path"],
                                    "   ([] = one declaration, one value per runtime entry)"
                                    if "[]" in r["path"] else ""))
        scope = r.get("scope")
        if scope:
            # The fact that decides whether a fix is even in the right place:
            # this is not a profile setting, and changing the profile-level key
            # of the same name will not move it.
            print("  scope     %s -- one value per entry, not one profile-wide value"
                  % scope)
            if r.get("inherits"):
                print("  inherits  an unset key falls through to %s, in that order"
                      % ", then ".join(r["inherits"]))
        default = r.get("default", "")
        if default != "":
            print("  default   %s" % default)
        elif scope:
            print("  default   (none declared -- an entry stores only the keys explicitly "
                  "set on it. An unset key inherits; what a fresh entry behaves like is "
                  "the inline fallback at the read site)")
        else:
            print("  default   (none declared -- the store is a SavedVariables global; "
                  "the effective default is the inline fallback at the read site)")
        print("  declared  %s:%d" % (r["file"], r["line"]))
        print("  read at   %d sites" % r.get("ref_count", 0))
        show_list(r.get("refs", []), r.get("ref_count"), args.limit, label="refs")
        print("  options   %d sites (where the UI control lives)" % r.get("options_ref_count", 0))
        show_list(r.get("options_refs", []), r.get("options_ref_count"), args.limit, label="sites")
        print("  used by   %s" % ", ".join(r.get("used_by", [])))
        if r.get("refs_other_modules"):
            print("  WARNING   %d same-named keys are declared in other modules. If the "
                  "in-module refs do not explain the behaviour, you have the wrong record."
                  % r["refs_other_modules"])
    return 0


def cmd_event(args):
    rows, exact = find("events", "event", args.event.upper())
    if not rows:
        print("no registration recorded for %r" % args.event)
        print("The index records RegisterEvent/RegisterUnitEvent sites. An event handled "
              "only through a Blizzard frame's own registration will not appear.")
        return 1
    fuzzy_note(exact, args.event, "event")
    if args.json:
        emit_json(rows)
        return 0
    for r in rows:
        print("%s  registered at %d sites" % (r["event"], r["count"]))
        show_list(r.get("sites", []), r["count"], args.limit, label="sites")
    return 0


def cmd_locale(args):
    rows, exact = find("locale", "key", args.text)
    if not rows:
        print("no locale string matching %r" % args.text)
        print("locale.jsonl records EllesmereUI.L() call sites only. An options-row label "
              "is a plain text= field and is not in here -- use: query.py label \"<label>\"")
        return 1
    fuzzy_note(exact, args.text, "key")
    if args.json:
        emit_json(rows)
        return 0
    for r in rows[:args.limit]:
        print("%r  used at %d sites" % (r["key"], r["count"]))
        show_list(r.get("sites", []), r["count"], args.limit, label="sites")
    if len(rows) > args.limit:
        print("... %d more matching strings" % (len(rows) - args.limit))
    return 0


def cmd_slash(args):
    needle = args.command if args.command.startswith("/") else "/" + args.command
    rows, exact = find("slash", "command", needle)
    if not rows:
        print("no slash command matching %r" % needle)
        return 1
    fuzzy_note(exact, needle, "command")
    if args.json:
        emit_json(rows)
        return 0
    for r in rows:
        print("%s  token=%s  module=%s  %s:%d"
              % (r["command"], r["token"], r["module"], r["file"], r["line"]))
    return 0


def cmd_module(args):
    rows, exact = find("modules", "module", args.name)
    if not rows:
        print("no module named %r" % args.name)
        print("Known modules: %s" % ", ".join(sorted(r["module"] for r in records("modules"))))
        return 1
    fuzzy_note(exact, args.name, "module")
    if args.json:
        emit_json(rows)
        return 0
    for r in rows:
        print("%s  %s" % (r["module"], r.get("title", "")))
        print("  folder    %s   toc %s" % (r["folder"], r["toc"]))
        print("  size      %d Lua files, %s lines, %d symbols, %d settings"
              % (r["lua_files"], format(r["lines"], ","), r.get("symbols", 0), r.get("settings", 0)))
        if r.get("saved_variables"):
            print("  saves     %s" % ", ".join(r["saved_variables"]))
        if r.get("slash_commands"):
            print("  slash     %s" % ", ".join(r["slash_commands"]))
        print("  load order (first entries): %s" % ", ".join(r.get("load_order", [])[:5]))
    return 0


# A row in this addon's table-driven options carries its label and its key a
# couple of lines apart, so the label is the bridge from a bug report's wording
# to something the index can answer.
_KEY_CALL = re.compile(r"""(?:SGet|SSet|DBVal|Get|Set)\s*\(\s*["']([A-Za-z_][\w.]*)["']""")
_KEY_FIELD = re.compile(r"""\b(?:key|setting|dbKey|profileKey)\s*=\s*["']([A-Za-z_][\w.]*)["']""")
# A name used as a table field (`ss.someKey`) or passed as a string literal.
_IDENT_USE = re.compile(r"""\.\s*([A-Za-z_]\w*)|["']([A-Za-z_]\w*)["']""")


_SCOPED_KEYS: set | None = None


def _scoped_keys():
    """Every entry-scoped settings key in the index, loaded once."""
    global _SCOPED_KEYS
    if _SCOPED_KEYS is None:
        _SCOPED_KEYS = {r["key"] for r in records("settings") if r.get("scope")}
    return _SCOPED_KEYS


def cmd_label(args):
    root = ensure_index(args.root, quiet=True)
    label = args.text
    needle = label.lower()
    hits = []
    for path in sorted(root.rglob("*_Options.lua")):
        if ".release" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if needle not in line.lower():
                continue
            keys = []
            for probe in lines[i:i + 10]:
                keys += _KEY_CALL.findall(probe) + _KEY_FIELD.findall(probe)
                # An entry-scoped row has no SGet/SSet call to match: it reads
                # `ss.someKey` and writes through a helper. Those are only
                # recognisable against the set of keys the index already knows
                # are entry-scoped, so match on that rather than on a shape.
                keys += [k for pair in _IDENT_USE.findall(probe) for k in pair
                         if k and k in _scoped_keys()]
            rel = str(path.relative_to(root))
            hits.append((rel, i + 1, line.strip()[:100], keys))
    if not hits:
        print("no options row carries the label %r" % label)
        print("Labels are matched case-insensitively against *_Options.lua. Try a distinctive "
              "fragment of the label rather than the whole string.")
        return 1
    for rel, line, text, keys in hits[:args.limit]:
        print("%s:%d  %s" % (rel, line, text))
        if keys:
            seen = []
            for k in keys:
                if k not in seen:
                    seen.append(k)
            print("  keys nearby: %s" % ", ".join(seen))
            print("  -> query.py setting %s" % seen[0])
        else:
            print("  no setting key within 10 lines -- read the row")
    if len(hits) > args.limit:
        print("... %d more rows" % (len(hits) - args.limit))
    return 0


def cmd_grep(args):
    """Substring across every record file, when the right file is not obvious."""
    total = 0
    for name in ("symbols", "settings", "locale", "events", "slash", "modules"):
        hits = [r for r in records(name) if args.pattern.lower() in json.dumps(r).lower()]
        if not hits:
            continue
        print("== %s: %d records" % (name, len(hits)))
        for r in hits[:args.limit]:
            ident = r.get("name") or r.get("key") or r.get("event") or r.get("command") or r.get("module")
            where = "%s:%s" % (r["file"], r["line"]) if "file" in r and "line" in r else ""
            print("  %s  %s" % (ident, where))
        if len(hits) > args.limit:
            print("  ... %d more" % (len(hits) - args.limit))
        total += len(hits)
    if not total:
        print("nothing in the index matches %r -- this is where you fall back to grepping "
              "the source: free text, comments, and table-driven config are not indexed."
              % args.pattern)
        return 1
    return 0


def cmd_status(args):
    meta = build_index.load_meta()
    if not meta:
        print("no index built yet")
        return 1
    g = meta.get("git", {})
    c = meta.get("counts", {})
    print("root     %s" % meta["addon_root"])
    print("git      %s on %s%s" % (g.get("short", "?"), g.get("branch", "?"),
                                   " (dirty)" if g.get("dirty") else ""))
    print("built    %s" % meta["built_at"])
    print("counts   %s symbols, %s settings, %s locale keys, %s events, %s modules"
          % (c.get("symbols"), c.get("settings"), c.get("locale_keys"),
             c.get("events"), c.get("modules")))
    return 0


# --------------------------------------------------------------------------

DEFAULTS = {"root": None, "json": False, "limit": 20, "no_ensure": False}

COMMANDS = {
    "def": (cmd_def, "name", "where a symbol is defined"),
    "callers": (cmd_callers, "name", "every recorded call site of a symbol"),
    "setting": (cmd_setting, "key", "a settings key: default, declaration, read sites, options row"),
    "event": (cmd_event, "event", "every RegisterEvent site for an event, suite-wide"),
    "locale": (cmd_locale, "text", "where a localised string is used"),
    "slash": (cmd_slash, "command", "which module owns a slash command"),
    "module": (cmd_module, "name", "a module's size, saved variables, and load order"),
    "label": (cmd_label, "text", "bridge a UI label from a bug report to its setting key"),
    "grep": (cmd_grep, "pattern", "substring across every record file"),
    "status": (cmd_status, None, "what the current index was built from"),
}


def main():
    # The flags hang off a parent parser so they are accepted on either side of
    # the subcommand. `query.py setting foo --limit 4` is what anyone types
    # first, and an argparse error there costs more than the lookup saves.
    #
    # Their default is SUPPRESS because argparse applies a subparser's defaults
    # after the main parser has run: with an ordinary default, a flag given
    # before the subcommand is parsed correctly and then overwritten by the
    # subparser's own default, and the flag silently does nothing.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS,
                        help="path to the EllesmereUI addon checkout")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print raw index records")
    common.add_argument("--limit", type=int, default=argparse.SUPPRESS,
                        help="max list entries per record (default 20)")
    common.add_argument("--no-ensure", action="store_true", default=argparse.SUPPRESS,
                        help="skip the freshness check")

    ap = argparse.ArgumentParser(
        prog="query.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Every command checks index freshness first and rebuilds if the source moved.",
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
    if not args.no_ensure and args.command not in ("status", "label"):
        ensure_index(args.root, quiet=True)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
