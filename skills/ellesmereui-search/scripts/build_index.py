#!/usr/bin/env python3
"""Build a navigation index for the EllesmereUI World of Warcraft addon suite.

The addon is ~148 Lua files / ~447k lines with single files over 1 MB, so plain
grep is a poor first move. This produces greppable JSONL indexes -- one complete
record per line -- covering modules, symbol definitions, settings keys (with
cross-references), locale keys, events, and slash commands.

Usage:
    build_index.py --ensure          rebuild only if the source changed (default)
    build_index.py --check           report FRESH/STALE, exit 1 if stale
    build_index.py --force           rebuild unconditionally
    build_index.py --root PATH       point at a specific addon checkout

Addon root resolution order: --root, $ELLESMEREUI_ROOT, the path recorded in a
previous meta.json, then common WoW install locations.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BUILDER_VERSION = 9

SKILL_DIR = Path(__file__).resolve().parent.parent
INDEX_DIR = SKILL_DIR / "references" / "index"

# Directories never worth indexing: vendored libraries, the packager's output
# copy of the whole tree, and the per-locale translation tables (bulk data --
# the canonical key list lives in Locales/_keys.txt).
#
# Every dotted directory goes too, rather than the six that were listed by
# name. Those hold tooling, not shipped code, and an offline helper that
# happens to share a function name with the addon costs a real caller list:
# three files under `.tools/` claimed 40 symbols and pushed 14 of
# EllesmereUIQuickdraw's functions to `caller_ambiguity` with no list at all.
EXCLUDE_DIRS = {"Libs", "media", "patches"}
EXCLUDE_REL_PREFIXES = ("Locales/",)


def skip_dir(name: str) -> bool:
    return name.startswith(".") or name in EXCLUDE_DIRS


# --------------------------------------------------------------------------
#  Lua source masking
# --------------------------------------------------------------------------
# Every structural pass runs against a "masked" copy of the source in which
# comment bodies and string contents are replaced by spaces while byte offsets
# are preserved. That keeps brace matching and identifier scanning from tripping
# over braces inside comments or strings, and lets any offset be read back
# against the original text when the literal value is what we actually want.

def _long_bracket_len(text: str, i: int) -> int:
    """If text[i] opens a long bracket ([[ or [==[), return its opener length."""
    if text[i] != "[":
        return 0
    j = i + 1
    while j < len(text) and text[j] == "=":
        j += 1
    if j < len(text) and text[j] == "[":
        return j - i + 1
    return 0


# Only four characters can open a masked region. Jumping between them beats
# stepping one character at a time: over this tree the loop below runs ~700k
# times instead of ~24M, and the skipped runs are copied as whole slices.
_MASK_OPENER = re.compile(r"""[-\["']""")
_NOT_NEWLINE = re.compile(r"[^\n]")


def _blank(segment: str) -> str:
    """The segment with every character but a newline turned into a space.

    Newlines survive inside strings too, which the per-character version did
    not do: a backslash-escaped newline was overwritten, leaving the mask one
    line shorter than the source. Nothing in this tree writes one today, but
    any pass that zips `text.splitlines()` against `mask.splitlines()` would
    silently misalign from that point on.
    """
    return _NOT_NEWLINE.sub(" ", segment)


def mask_lua(text: str) -> str:
    """Blank comment bodies and string contents, preserving length and offsets."""
    n = len(text)
    out: list[str] = []
    kept = 0  # start of the run of unmasked text not yet emitted
    i = 0
    while i < n:
        m = _MASK_OPENER.search(text, i)
        if m is None:
            break
        i = m.start()
        c = text[i]

        # Comment: -- line, or --[[ long ]]
        if c == "-" and i + 1 < n and text[i + 1] == "-":
            opener = _long_bracket_len(text, i + 2) if i + 2 < n else 0
            if opener:
                level = opener - 2
                close = "]" + "=" * level + "]"
                end = text.find(close, i + 2 + opener)
                end = n if end == -1 else end + len(close)
            else:
                end = text.find("\n", i)
                end = n if end == -1 else end
            blank_from, blank_to = i, end
        else:
            # Long string [[ ... ]]
            opener = _long_bracket_len(text, i)
            if opener:
                level = opener - 2
                close = "]" + "=" * level + "]"
                end = text.find(close, i + opener)
                end = n if end == -1 else end + len(close)
                blank_from = i + opener
                blank_to = min(end - len(close), n) if end != n else n
            elif c in "\"'":
                # Short string
                quote = c
                j = i + 1
                while j < n:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text[j] == quote or text[j] == "\n":
                        break
                    j += 1
                blank_from, blank_to = i + 1, min(j, n)
                end = j + 1 if j < n and text[j] == quote else j
            else:
                # A bare `-` or a `[` that opens nothing.
                i += 1
                continue

        if blank_to > blank_from:
            out.append(text[kept:blank_from])
            out.append(_blank(text[blank_from:blank_to]))
            kept = blank_to
        i = end

    out.append(text[kept:])
    return "".join(out)


# A chunk-scope declaration: `local a, b, c` or `local function Foo`, both at
# column 0. The second alternative matters for shadowing -- a file-scope
# `local function Foo` hides a global `Foo` for the rest of the chunk.
FILE_LOCAL = re.compile(
    r"^local[ \t]+(?:function[ \t]+([A-Za-z_]\w*)|([A-Za-z_][\w, \t]*))", re.M
)

# The same declaration at any indent. This one is not evidence of chunk scope,
# so it does not shadow anything -- it is only evidence that a bare assignment
# to that name later in the file is not creating a global. The big options
# files declare three builders on one indented `local` line and fill each body
# thousands of lines below; read as globals, those definitions collect bare
# calls from the whole tree instead of from their own file. 50 of the 120 rows
# that once claimed `global` were this shape. See `classify`.
BLOCK_LOCAL = re.compile(
    r"^[ \t]+local[ \t]+(?:function[ \t]+([A-Za-z_]\w*)|([A-Za-z_][\w, \t]*))", re.M
)

# Keywords that open and close a Lua block. See `Source.block_end`.
BLOCK_KW = re.compile(r"\b(function|then|do|repeat|end|until|elseif)\b")


class Source:
    """One Lua file: original text, masked text, and line-number lookup."""

    __slots__ = ("rel", "text", "mask", "_nl", "_brace_at", "_brace_depth",
                 "file_locals", "declared_locals")

    def __init__(self, rel: str, text: str):
        self.rel = rel
        self.text = text
        self.mask = mask_lua(text)
        self._nl = [m.start() for m in re.finditer("\n", text)]

        # Running table-constructor depth, so any offset can be asked whether
        # it sits inside a `{ ... }`. Counted once here rather than per symbol:
        # `mask.count("{", 0, offset)` for each of 17k definitions would read
        # the whole tree 17k times.
        at, depth, d = [], [], 0
        for m in re.finditer(r"[{}]", self.mask):
            d += 1 if m.group() == "{" else -1
            at.append(m.start())
            depth.append(d)
        self._brace_at, self._brace_depth = at, depth

        self.file_locals = self._scan_locals(FILE_LOCAL)
        # Every `local` declaration in the file, at any indent. Used only to
        # deny globalness, never to shadow -- see BLOCK_LOCAL.
        self.declared_locals = self.file_locals | self._scan_locals(BLOCK_LOCAL)

    def _scan_locals(self, rx) -> set:
        """The names declared `local` by every match of `rx`.

        With FILE_LOCAL this is chunk scope, column 0: the whole file, so those
        names shadow a global of the same name everywhere below. Indented
        declarations are kept apart in `declared_locals` because they do not
        prove chunk scope -- they belong to a block this pass cannot delimit,
        and treating one as file-scope for shadowing would drop real call
        edges.
        """
        names = set()
        for m in rx.finditer(self.mask):
            for part in (m.group(1) or m.group(2)).split(","):
                part = part.strip()
                if IDENT.match(part):
                    names.add(part)
        return names

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self._nl, offset) + 1

    def table_depth(self, offset: int) -> int:
        """How many table constructors are open at `offset`."""
        i = bisect.bisect_left(self._brace_at, offset)
        return self._brace_depth[i - 1] if i else 0

    def block_end(self, start: int) -> int:
        """Offset where the block enclosing `start` closes.

        Lua scopes a `local` to the end of its enclosing block. A settings
        table bound to a short name -- `ss`, `bs` -- is re-bound to something
        unrelated a few functions down, so a scan that runs a fixed number of
        lines past the binding credits the second variable's fields to the
        first one's table. In this tree that turned bar-data fields
        (`assignedSpells`, `customSpellIDs`) into per-spell settings keys.

        Counts the keywords that open a block against those that close one.
        The `then` of an `elseif` continues the block its `if` already opened,
        so it must not be counted a second time. `for`/`while` are not counted
        because their own `do` is.
        """
        depth = 0
        after_elseif = False
        for m in BLOCK_KW.finditer(self.mask, start):
            word = m.group(1)
            if word == "elseif":
                after_elseif = True
            elif word == "then":
                if after_elseif:
                    after_elseif = False
                else:
                    depth += 1
            elif word in ("function", "do", "repeat"):
                depth += 1
            else:  # end, until
                if depth == 0:
                    return m.start()
                depth -= 1
        return len(self.mask)

    @property
    def n_lines(self) -> int:
        return len(self._nl) + 1


# --------------------------------------------------------------------------
#  Symbol extraction
# --------------------------------------------------------------------------

# Parameter lists wrap across lines in this codebase, so the body is `[^)]*`
# rather than `[^)\n]*` -- Lua parameter lists cannot themselves contain
# parentheses, so stopping at the first `)` is exact.
FUNC_DECL = re.compile(
    r"^([ \t]*)(local[ \t]+)?function[ \t]+([A-Za-z_][\w.:]*)[ \t]*\(([^)]*)\)",
    re.M,
)
FUNC_ASSIGN = re.compile(
    r"^([ \t]*)(local[ \t]+)?([A-Za-z_][\w.]*)[ \t]*=[ \t]*function[ \t]*\(([^)]*)\)",
    re.M,
)


def classify(
    name: str, is_local: bool, in_table: bool = False, is_file_local: bool = False
) -> tuple[str, str, str]:
    """Return (kind, owner, short_name) for a definition name.

    A bare name is not automatically a global. `Name = function()` is also how
    a table constructor holds a handler and how a forward-declared local gets
    its body, and calling all three "global" was wrong for 7,234 of the 7,265
    rows that claimed it. The three are told apart by where the definition
    sits: inside an open `{` it is a table field, and outside one it is the
    local of that name if the file declares one.

    Position decides before the name does, and only that order is safe. A key
    written inside a constructor is a field of that table whatever else the
    file calls `local`:

        local Refresh                 -- a forward declaration
        local handlers = {
            Refresh = function() end, -- a key in `handlers`, not that local
        }

    Reading the second as the local's body claims the local's call sites for a
    function nothing calls by that bare name -- 334 rows in this tree.
    """
    if ":" in name:
        owner, short = name.rsplit(":", 1)
        return "method", owner, short
    if "." in name:
        owner, short = name.rsplit(".", 1)
        return "field", owner, short
    if is_local:
        return "local", "", name
    if in_table:
        return "tablefield", "", name
    if is_file_local:
        return "local", "", name
    return "global", "", name


def extract_symbols(src: Source, module: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for rx in (FUNC_DECL, FUNC_ASSIGN):
        for m in rx.finditer(src.mask):
            name = m.group(3)
            line = src.line(m.start())
            if (name, line) in seen:
                continue
            seen.add((name, line))
            is_local = bool(m.group(2))
            # Only an assignment can land inside a table constructor; a
            # `function Foo()` statement cannot.
            in_table = rx is FUNC_ASSIGN and src.table_depth(m.start()) > 0
            kind, owner, short = classify(
                name, is_local, in_table, name in src.declared_locals
            )
            params = " ".join(m.group(4).split())
            rows.append(
                {
                    "name": short,
                    "kind": kind,
                    "owner": owner,
                    "full": name,
                    "params": params,
                    "module": module,
                    "file": src.rel,
                    "line": line,
                }
            )
    return rows


# --------------------------------------------------------------------------
#  Settings (defaults tables)
# --------------------------------------------------------------------------

# A defaults table, either whole (`local defaults = {`) or one branch of one
# filled in later (`defaults.profile.bars[info.key] = {`). The second form is
# how a per-entry namespace gets built -- one literal in a loop, one entry per
# bar -- and it holds keys that exist nowhere else.
DEFAULTS_ASSIGN = re.compile(
    r"^[ \t]*(?:local[ \t]+)?([A-Za-z_][\w.]*)((?:\[[^\]\n]+\])*)[ \t]*=[ \t]*\{",
    re.M,
)
DEFAULTS_NAME = re.compile(r"(?i)defaults$")

# A key at the start of a table entry: `foo =` or `["foo"] =`
TABLE_KEY = re.compile(r"([A-Za-z_]\w*)[ \t]*=(?!=)|\[[ \t]*[\"']()")


def _match_brace(mask: str, open_idx: int) -> int:
    """Index just past the `}` matching the `{` at open_idx."""
    depth = 0
    for i in range(open_idx, len(mask)):
        c = mask[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(mask)


def _read_value(mask: str, text: str, start: int, end: int) -> tuple[str, int]:
    """Read a scalar value starting at `start`; return (literal, index after)."""
    depth = 0
    i = start
    while i < end:
        c = mask[i]
        if c in "({[":
            depth += 1
        elif c in ")}]":
            if depth == 0:
                break
            depth -= 1
        elif c == "," and depth == 0:
            break
        i += 1
    raw = " ".join(text[start:i].split())
    return raw[:160], i


COLOUR_KEYS = frozenset("rgba")


def _colour_table(mask: str, open_idx: int) -> bool:
    """Is the table at open_idx a colour literal -- `{ r = .., g = .., b = .. }`?

    A colour is one setting, not three. Descending into it records leaves keyed
    `r`, `g` and `b`, which loses the colour's own name -- nothing answers
    `"key":"castbarFillColor"` -- and gives each leaf the reference list of a
    one-letter identifier, so `r` claims every `r` in the module. Colours are
    ~19% of this addon's defaults, so that is a large hole with wrong data in
    it. The constructor is the value; the key above it is the setting.
    """
    end = _match_brace(mask, open_idx)
    seen: set[str] = set()
    i = open_idx + 1
    while i < end - 1:
        c = mask[i]
        if c.isspace() or c == ",":
            i += 1
            continue
        m = TABLE_KEY.match(mask, i)
        if not m or m.group(1) not in COLOUR_KEYS:
            return False
        literal, i = _read_value(mask, mask, m.end(), end - 1)
        if "{" in literal:
            return False
        seen.add(m.group(1))
    # `a` is optional, the other three are what makes it a colour. Requiring all
    # three keeps an unrelated single-letter key from collapsing its table.
    return {"r", "g", "b"} <= seen


def _names_keys(mask: str, open_idx: int) -> bool:
    """Does the table at open_idx hold a named key anywhere below it?

    Anywhere, not at its own level: `bars = { { key = "cooldowns" } }` is an
    array whose own level names nothing, yet every entry below it does. Only a
    table with no named key at any depth has no leaf to record.
    """
    end = _match_brace(mask, open_idx)
    return TABLE_KEY.search(mask, open_idx + 1, end - 1) is not None


def parse_defaults_table(src: Source, open_idx: int) -> list[tuple[str, str, int]]:
    """Walk a defaults table, yielding (dotted_path, literal_value, line)."""
    mask, text = src.mask, src.text
    end = _match_brace(mask, open_idx)
    out: list[tuple[str, str, int]] = []
    path: list[str] = []
    pending: str | None = None
    i = open_idx

    while i < end:
        c = mask[i]

        if c == "{":
            path.append(pending if pending is not None else "[]")
            pending = None
            i += 1
            continue

        if c == "}":
            if path:
                path.pop()
            i += 1
            continue

        if c.isspace() or c == ",":
            i += 1
            continue

        m = TABLE_KEY.match(mask, i)
        if m and m.group(1):
            key = m.group(1)
            j = m.end()
        elif m:
            # ["literal"] = ... -- string content is masked, read it from text
            close = mask.find("]", i)
            if close == -1:
                i += 1
                continue
            key = text[i + 1 : close].strip().strip("\"'")
            eq = mask.find("=", close)
            if eq == -1:
                i += 1
                continue
            j = eq + 1
        else:
            i += 1
            continue

        # Skip whitespace to the value
        while j < end and mask[j] in " \t\n":
            j += 1

        if (
            j < end
            and mask[j] == "{"
            and _names_keys(mask, j)
            and not _colour_table(mask, j)
        ):
            pending = key
            i = j
            continue

        # A table this walk must not descend into: one with no named key below
        # it (`gold = {0.886, 0.675, 0.478}`, `paging = {}`), where descending
        # finds no leaf at all, or a colour (`{ r = .., g = .., b = .. }`),
        # where descending finds the wrong three. Either way the key itself is
        # the setting and the constructor is its value.

        literal, after = _read_value(mask, text, j, end)
        out.append((".".join(path[1:] + [key]), literal, src.line(i)))
        i = after

    return out


def defaults_prefix(var: str, subscripts: str) -> list[str] | None:
    """Path components between the defaults table and the literal being read.

    `defaults` is the root, so the components after it name the branch this
    literal fills in. A `[expr]` subscript is a key chosen at runtime -- a bar
    id, a profile name -- so it becomes the same `[]` placeholder the nested
    walk already uses. Returns None when the target is not a defaults table.
    """
    parts = var.split(".")
    root = next((i for i, p in enumerate(parts) if DEFAULTS_NAME.search(p)), None)
    if root is None:
        return None
    return parts[root + 1 :] + ["[]"] * subscripts.count("[")


def extract_settings(src: Source, module: str) -> list[dict]:
    rows: list[dict] = []
    for m in DEFAULTS_ASSIGN.finditer(src.mask):
        var = m.group(1)
        prefix = defaults_prefix(var, m.group(2))
        if prefix is None:
            continue
        open_idx = m.end() - 1
        for sub_path, literal, line in parse_defaults_table(src, open_idx):
            path = ".".join(prefix + [sub_path])
            # Profiles are keyed under `profile` at the AceDB layer but read as
            # bare `p.key` at runtime -- index the name the code actually uses.
            key_path = path[len("profile.") :] if path.startswith("profile.") else path
            if not key_path:
                continue
            rows.append(
                {
                    "key": key_path.rsplit(".", 1)[-1],
                    "path": key_path,
                    "store": "defaults",
                    "default": literal,
                    "module": module,
                    "table": var + m.group(2),
                    "file": src.rel,
                    "line": line,
                }
            )
    return rows


def extract_saved_variable_keys(
    sources: list[Source], sv_names: dict[str, str], module_names: list[str]
) -> list[dict]:
    """Index keys accessed directly on a SavedVariables global.

    The suite-wide `EllesmereUIDB` (and a few others) are not fed by a defaults
    table -- code reads and writes `EllesmereUIDB.someKey` directly with inline
    `or`/`~= false` fallbacks. Those keys are invisible to the defaults-table
    pass but are exactly the cross-cutting settings (profiles, unlockAnchors,
    partyMode, ...) worth being able to look up.
    """
    hits: dict[tuple[str, str], set[tuple[str, str]]] = {}
    if not sv_names:
        return []

    # One alternation over every SavedVariables name rather than one pass per
    # name. There are 40 of them and 24 MB of source, so the per-name loop read
    # the whole tree 80 times; this reads it twice. Longest name first, so a
    # name that is a prefix of another cannot win the match.
    alt = "|".join(re.escape(sv) for sv in sorted(sv_names, key=len, reverse=True))
    attr = re.compile(r"\b(" + alt + r")[ \t]*\.[ \t]*([A-Za-z_]\w*)")
    sub = re.compile(
        r"\b(" + alt + r")[ \t]*\[[ \t]*[\"']([A-Za-z_]\w*)[\"']"
    )
    for src in sources:
        mod = module_of(src.rel, module_names)
        for m in attr.finditer(src.mask):
            hits.setdefault((m.group(1), m.group(2)), set()).add(
                (mod, f"{src.rel}:{src.line(m.start())}")
            )
        for m in sub.finditer(src.text):
            if src.mask[m.start(2)] != " " or src.mask[m.start()] == " ":
                continue
            hits.setdefault((m.group(1), m.group(2)), set()).add(
                (mod, f"{src.rel}:{src.line(m.start())}")
            )

    rows: list[dict] = []
    for (sv, key), sites in hits.items():
        ordered = sorted(s for _, s in sites)
        options = [s for s in ordered if "_Options.lua" in s]
        rows.append(
            {
                "key": key,
                "path": f"{sv}.{key}",
                "store": sv,
                "default": "",
                "module": sv_names[sv],
                "table": sv,
                "file": ordered[0].rsplit(":", 1)[0],
                "line": int(ordered[0].rsplit(":", 1)[1]),
                "refs": ordered[:60],
                "ref_count": len(ordered),
                "options_refs": options[:10],
                "options_ref_count": len(options),
                "used_by": sorted({mod for mod, _ in sites}),
            }
        )
    return rows


# --------------------------------------------------------------------------
#  Third settings class: keys stored per entity rather than per profile
# --------------------------------------------------------------------------
# A per-spell setting is written to an entry keyed by a runtime spellID, so it
# never appears in a defaults table and never as `EllesmereUIDB.<key>`. Both
# other passes miss it completely -- and a per-spell option is exactly what a
# "this one spell behaves wrong" report is about. The bug that prompted this
# pass was `buffLostSoundKey`, which the index could not name at all.
#
# The three tables below share ONE key namespace: a read of a per-spell entry
# falls through `__index` to the bar tiers, so a key valid at one tier is valid
# at all of them. That is why they resolve to a single record with a chain
# rather than to three.
#
# These names are facts about this codebase, not about Lua, so a refactor in
# the addon can retire this pass without breaking anything that looks broken.
# `build` fails when the store is still in the source but yields no keys, and
# validate_index.py checks the count, because the failure mode otherwise is a
# pass that silently returns nothing.
SCOPED_STORE_NAME = "spellSettings"
SCOPED_TIERS = ("barSettings", "barSpellSettings")
SCOPED_RESOLVERS = ("ResolveSpellSettings", "_ResolveCdmSS", "GetBarTierSettings")

# A store holds many entries; an entry holds the keys. `store[id]` is the step
# between them, so a local bound to a store is tracked separately from a local
# bound to one entry.
SCOPED_STORE = re.compile(
    r"\.[ \t]*" + SCOPED_STORE_NAME + r"\b(?![ \t]*\[)"
    r"|\bGetSpellSettingsStore(?:ForProf)?[ \t]*\("
)
SCOPED_ENTRY = re.compile(
    r"\.[ \t]*" + SCOPED_STORE_NAME + r"[ \t]*\["
    + r"|\.[ \t]*(?:" + "|".join(SCOPED_TIERS) + r")\b"
    + r"|\b(?:" + "|".join(SCOPED_RESOLVERS) + r")[ \t]*\("
)
# Where the store itself is declared. Its module owns every key in the store.
STORE_ACCESSOR = re.compile(r"\bfunction[ \t]+[\w.:]*GetSpellSettingsStore[ \t]*\(")
LOCAL_BIND = re.compile(r"\blocal[ \t]+([A-Za-z_]\w*)[ \t]*=([^\n]*)")
LOCAL_FUNC = re.compile(r"\blocal[ \t]+function[ \t]+([A-Za-z_]\w*)[ \t]*\([ \t]*([A-Za-z_]\w*)")


def _live_range(src: Source, name: str, start: int) -> tuple[int, int]:
    """Where a local declared at `start` is still the variable it was bound to.

    The block bound is the language rule; the rebinding bound is the one this
    tree needs. The big options file declares `local store = ...` for half a
    dozen unrelated stores inside one enormous block, so block scope alone lets
    the first one claim every later `store[id]`.
    """
    end = src.block_end(start)
    rebind = re.search(r"\blocal[ \t]+" + re.escape(name) + r"\b", src.mask[start:end])
    return start, (start + rebind.start() if rebind else end)


def _scoped_binds(src: Source) -> list[tuple[str, int, int]]:
    """Every local bound to one per-entity settings entry, with its scope.

    Two levels, because the code reaches an entry in two steps: bind the store
    (`local store = ns.GetSpellSettingsStore(bar)`), then subscript it
    (`local ss = store[spellID]`). Matching only the second step against a
    literal container name would miss every options row, which is where the
    keys carry their labels.
    """
    mask = src.mask
    stores = [
        (m.group(1), *_live_range(src, m.group(1), m.end()))
        for m in LOCAL_BIND.finditer(mask)
        if SCOPED_STORE.search(m.group(2))
    ]
    out: list[tuple[str, int, int]] = []
    for m in LOCAL_BIND.finditer(mask):
        rhs = m.group(2)
        hit = bool(SCOPED_ENTRY.search(rhs)) or any(
            start <= m.start() <= end
            and re.search(r"\b" + re.escape(name) + r"[ \t]*\[", rhs)
            for name, start, end in stores
        )
        if hit:
            out.append((m.group(1), *_live_range(src, m.group(1), m.end())))
    return out


def _scoped_key_sites(src: Source) -> list[tuple[str, int]]:
    """Every (key, offset) this file reads or writes on a per-entity entry."""
    mask, text = src.mask, src.text
    found: list[tuple[str, int]] = []
    for name, start, end in _scoped_binds(src):
        body = mask[start:end]
        for m in re.finditer(
            r"\b" + re.escape(name) + r"[ \t]*\.[ \t]*([A-Za-z_]\w*)", body
        ):
            found.append((m.group(1), start + m.start()))

        # A writer helper takes the key as a parameter and writes it onto the
        # entry (`local function SetOwn(key, val) ss[key] = val end`). Its call
        # sites carry key names that appear nowhere as an attribute, so without
        # this every write-only option is invisible.
        for f in LOCAL_FUNC.finditer(body):
            fn, param = f.group(1), f.group(2)
            fs = start + f.end()
            fe = src.block_end(fs)
            if not re.search(
                r"\b" + re.escape(name) + r"[ \t]*\[[ \t]*" + re.escape(param) + r"[ \t]*\]",
                mask[fs:fe],
            ):
                continue
            # Call sites are read from the raw text: the key is a string
            # literal, which the mask blanks.
            for c in re.finditer(
                r"\b" + re.escape(fn) + r"[ \t]*\([ \t]*[\"']([A-Za-z_]\w*)[\"']", text
            ):
                if mask[c.start()] == " ":
                    continue
                found.append((c.group(1), c.start()))
    return found


def extract_scoped_settings(
    sources: list[Source], module_names: list[str]
) -> list[dict]:
    hits: dict[str, set[tuple[str, str]]] = {}

    # Every one of these keys lives in the same store, so they share one owner:
    # the module that declares the store accessor. Attributing them per key by
    # counting sites instead credits whichever file happens to mention a key
    # most -- for two of them that was the suite-level migration file, which
    # only rewrites the store and does not own it.
    owner = next(
        (
            module_of(src.rel, module_names)
            for src in sources
            if STORE_ACCESSOR.search(src.mask)
        ),
        "",
    )

    for src in sources:
        mod = module_of(src.rel, module_names)
        for key, offset in _scoped_key_sites(src):
            hits.setdefault(key, set()).add((mod, f"{src.rel}:{src.line(offset)}"))

    path_root = f"{SCOPED_STORE_NAME}[<id>]"
    rows: list[dict] = []
    for key, sites in hits.items():
        ordered = sorted(s for _, s in sites)
        options = [s for s in ordered if s.rsplit(":", 1)[0].endswith("_Options.lua")]
        rows.append(
            {
                "key": key,
                "path": f"{path_root}.{key}",
                "store": SCOPED_STORE_NAME,
                "scope": "per-spell",
                "inherits": list(SCOPED_TIERS),
                "default": "",
                "module": owner,
                "table": SCOPED_STORE_NAME,
                "file": ordered[0].rsplit(":", 1)[0],
                "line": int(ordered[0].rsplit(":", 1)[1]),
                # Sites come from the scoped scan itself, not from a global
                # attribute sweep: a key like `duration` matches thousands of
                # unrelated `.duration` reads, and counting those would make
                # the number worse than no number.
                "refs": ordered[:60],
                "ref_count": len(ordered),
                "options_refs": options[:10],
                "options_ref_count": len(options),
                "used_by": sorted({m for m, _ in sites}),
                "refs_other_modules": 0,
            }
        )
    return rows


# --------------------------------------------------------------------------
#  Cross-references, locale keys, events, slash commands
# --------------------------------------------------------------------------

ATTR_REF = re.compile(r"\.[ \t]*([A-Za-z_]\w*)")
STR_REF = re.compile(r"[\"']([A-Za-z_]\w*)[\"']")
LOCALE_CALL = re.compile(r"EllesmereUI\.(Lf?)\([ \t]*\"([^\"]*)\"")
EVENT_REG = re.compile(r"Register(?:Unit)?Event\([ \t]*\"([A-Z][A-Z0-9_]*)\"")
SLASH_DECL = re.compile(r"^[ \t]*(?:_G\[[\"'])?SLASH_([A-Z0-9_]+?)(\d+)[\"']?\]?[ \t]*=[ \t]*\"(/[^\"]+)\"", re.M)


def collect_refs(
    sources: list[Source], keys: set[str], module_names: list[str]
) -> dict[str, set[tuple[str, str]]]:
    """Find where settings keys are read: attribute access or string subscript.

    Returns key -> {(module, "file:line")}. Keys are scoped per module at the
    call site because the same short name (`enabled`, `absorbCleanAlpha`) is
    defined independently in several modules.
    """
    refs: dict[str, set[tuple[str, str]]] = {}

    def add(name: str, src: Source, offset: int) -> None:
        if name not in keys:
            return
        mod = module_of(src.rel, module_names)
        refs.setdefault(name, set()).add((mod, f"{src.rel}:{src.line(offset)}"))

    for src in sources:
        for m in ATTR_REF.finditer(src.mask):
            add(m.group(1), src, m.start())
        for m in STR_REF.finditer(src.text):
            # Validate it is a real string literal, not code that merely looks
            # like one: masked text blanks string bodies to spaces.
            #
            # A blanked body alone does not prove it: a comment is blanked
            # whole, so prose like `-- changedAxis: "width", "height"` read as
            # a reference to the `width` setting. A real string keeps its
            # opening quote, because masking starts one byte after it.
            if src.mask[m.start(1)] != " " or src.mask[m.start()] == " ":
                continue
            add(m.group(1), src, m.start())
    return refs


# --------------------------------------------------------------------------
#  Call sites
# --------------------------------------------------------------------------

# A call expression, with the receiver captured when there is one. Run against
# the mask, so calls in comments and strings never count.
CALL = re.compile(r"(?:([A-Za-z_][\w.]*)[ \t]*([.:])[ \t]*)?([A-Za-z_]\w*)[ \t]*\(")

# `local A, B = ...` -- the declaration list stops at the `=`.
LOCAL_DECL = re.compile(r"^[ \t]*local[ \t]+([A-Za-z_][\w, \t]*)", re.M)
IDENT = re.compile(r"^[A-Za-z_]\w*$")

# `EllesmereUI.ComputeCastBarTint = ComputeCastBarTint` -- this addon exports
# across modules by binding a file-local onto a shared table, so the local and
# the field are one function and calls through the field belong to the local.
# Anchored at column 0: at file scope this is the export idiom, while the same
# line indented inside a function body is a conditional rebind whose target
# cannot be resolved from a regex.
EXPORT_ALIAS = re.compile(
    r"^([A-Za-z_][\w.]*)[ \t]*\.[ \t]*([A-Za-z_]\w*)[ \t]*=[ \t]*([A-Za-z_]\w*)[ \t]*$",
    re.M,
)
# `local ComputeCastBarTint = ns.ComputeCastBarTint` -- the same edge inbound.
# Bare calls to the local in that file are calls to the field it was bound from.
IMPORT_ALIAS = re.compile(
    r"^local[ \t]+([A-Za-z_]\w*)[ \t]*=[ \t]*([A-Za-z_][\w.]*)[ \t]*\.[ \t]*([A-Za-z_]\w*)[ \t]*$",
    re.M,
)

# The right-hand side of a table binding, allowing this suite's guard idiom:
# `local PPc = EllesmereUI and EllesmereUI.PP` is how a module reaches another
# module's table without erroring when it loaded alone. Any number of `X and`
# guards may precede the name; what is wanted is the name they guard.
#
# A `_G.` prefix is dropped: `_G.EllesmereUI` and `EllesmereUI` are one table,
# and definitions are recorded under the bare name.
#
# `or {}` is allowed, because an empty constructor is not a second candidate
# receiver -- `local EUI = _G.EllesmereUI or {}` means the global whenever
# there is anything to call. An `or` between two *named* tables
# (`EllesmereUI.PanelPP or EllesmereUI.PP`) still fails to match: picking
# either would invent call edges to a function that may never run.
_ALIAS_RHS = (
    r"(?:[A-Za-z_][\w.]*[ \t]+and[ \t]+)*"
    r"(?:_G[ \t]*\.[ \t]*)?([A-Za-z_][\w.]*)"
    r"(?:[ \t]+or[ \t]*\{[ \t]*\})?"
)

# `local PPc = ...PP` -- a whole table under a second name, for the rest of that
# file. Distinct from IMPORT_ALIAS, which binds one function: here the calls
# that need crediting are `PPc.ToPixels(`, not a bare `PPc(`.
TABLE_ALIAS_LOCAL = re.compile(
    r"^[ \t]*local[ \t]+([A-Za-z_]\w*)[ \t]*=[ \t]*" + _ALIAS_RHS + r"[ \t]*$", re.M
)
# `EllesmereUI.PP = PP` -- the same table published under a second path, which
# then reaches every module. Indentation is allowed: unlike an exported
# function, these sit inside the `do` block that builds the table.
TABLE_ALIAS_PATH = re.compile(
    r"^[ \t]*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)[ \t]*=[ \t]*" + _ALIAS_RHS + r"[ \t]*$",
    re.M,
)

CALLER_CAP = 40


def file_local_names(src: Source) -> set[str]:
    """Names this file declares with `local`, one chunk's worth of scope."""
    names: set[str] = set()
    for m in LOCAL_DECL.finditer(src.mask):
        for part in m.group(1).split(","):
            part = part.strip()
            if IDENT.match(part):
                names.add(part)
    return names


# `local ADDON_NAME, ns = ...` -- the private table WoW hands each addon.
ADDON_TABLE = re.compile(r"^[ \t]*local[ \t]+([A-Za-z_][\w, \t]*)=[ \t]*\.\.\.", re.M)


def addon_table_names(src: Source) -> set[str]:
    """Locals this file binds from its addon vararg.

    The test for "is this receiver private to one module" cannot be "is it a
    local": `local EllesmereUI = _G.EllesmereUI` is a local too, and treating
    that as module-private hides every cross-module call to a suite-wide
    helper. What is genuinely per-addon is the table WoW passes as the second
    vararg, which only `local _, ns = ...` binds.
    """
    names: set[str] = set()
    for m in ADDON_TABLE.finditer(src.mask):
        for part in m.group(1).split(","):
            part = part.strip()
            if IDENT.match(part):
                names.add(part)
    return names


def owner_root(owner: str) -> str:
    return owner.split(".")[0].split(":")[0]


def call_key(row: dict, module_scoped: bool) -> tuple:
    """The expression shape a definition is actually called through.

    Matching on the bare name alone is worthless here: EllesmereUI defines a
    handful of names that Blizzard's frame API also uses, so `SetPoint` reads
    as 6836 callers when almost every one of them is `someFrame:SetPoint`.
    Requiring the receiver to match cuts that to four.

    `module_scoped` marks a receiver that is a file-local rather than a global
    -- overwhelmingly `ns`, from the `local addonName, ns = ...` idiom every
    addon folder uses. Those tables are per-addon, so two modules' `ns.Foo`
    are unrelated functions and must not share one key.
    """
    if row["kind"] == "method":
        base = (row["owner"], ":", row["name"])
    elif row["kind"] == "field":
        base = (row["owner"], ".", row["name"])
    elif row["kind"] == "local":
        # A Lua local is chunk-scoped, and one file is one chunk, so both the
        # definition and every caller are in this file. That makes locals the
        # most reliable class in the index.
        return ("<local>", row["file"], row["name"])
    else:
        return ("<global>", row["name"])
    return base + (row["module"],) if module_scoped else base


def collect_aliases(
    sources: list[Source], symbols: list[dict]
) -> tuple[
    dict[tuple[str, str], tuple[str, str]],
    dict[tuple[str, str], list[tuple[str, str]]],
]:
    """Second names that definitions are called through.

    Returns two maps, each keyed so the caller pass can look an edge up from a
    symbol row it already holds:

    - **exports** -- `(file, local_name) -> [(receiver, field), ...]`. The local
      was bound onto a table at file scope, so `receiver.field(` calls it. More
      than one is normal: a helper is commonly published both on the suite
      table and on `_G` under a prefixed name.
    - **imports** -- `(receiver, field) -> [(file, local_name), ...]`. Each
      listed file binds that field to a local, so a bare call there reaches it.

    An alias is dropped when more than one definition claims it, and an import
    is dropped when the importing file also defines that name itself. A wrong
    edge is worse than a missing one: it credits a call to a function that
    never runs, and unlike a gap it leaves nothing to notice.
    """
    export_claims: dict[tuple[str, str], set[tuple[str, str]]] = {}
    import_claims: dict[tuple[str, str], list[tuple[str, str]]] = {}
    raw_exports: list[tuple[str, str, str, str]] = []

    defined_locals: set[tuple[str, str]] = {
        (r["file"], r["name"]) for r in symbols if r["kind"] == "local"
    }

    for src in sources:
        for m in EXPORT_ALIAS.finditer(src.mask):
            recv, field, local = m.group(1), m.group(2), m.group(3)
            # The right-hand side has to name a local function defined in this
            # file. Anything else is a plain table assignment, not this idiom.
            if (src.rel, local) not in defined_locals:
                continue
            raw_exports.append((src.rel, local, recv, field))
            export_claims.setdefault((recv, field), set()).add((src.rel, local))
        for m in IMPORT_ALIAS.finditer(src.mask):
            local, recv, field = m.group(1), m.group(2), m.group(3)
            # `local Foo = ns.Foo` beside a `local function Foo` in the same
            # file: bare calls there are the file's own, not the import's.
            if (src.rel, local) in defined_locals:
                continue
            import_claims.setdefault((recv, field), []).append((src.rel, local))

    exports: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for rel, local, recv, field in raw_exports:
        if len(export_claims[(recv, field)]) == 1:
            exports.setdefault((rel, local), []).append((recv, field))
    return exports, import_claims


def collect_table_aliases(
    sources: list[Source], symbols: list[dict]
) -> tuple[
    dict[str, list[tuple[str, str]]],
    dict[str, list[str]],
    dict[tuple[str, str], list[str]],
]:
    """Second names that a *table* is called through.

    `collect_aliases` resolves a renamed function. This resolves a renamed
    receiver, which is the larger gap: a file writes `local PPc = PP` and then
    calls `PPc.ToPixels(...)`, and attribution that follows the receiver credits
    nothing, because the definition was recorded as `PP.ToPixels`.

    Returns two maps from the recorded owner to the other names it is reached
    under:

    - **locals** -- `owner -> [(file, alias), ...]`, good only inside that file.
    - **paths** -- `owner -> [dotted.path, ...]`, good everywhere, since a table
      published on a shared table is visible suite-wide.
    - **canonical** -- `(file, alias) -> [owner, ...]`, the same bindings read
      backwards. A file that opens `local EUI = EllesmereUI` and then declares
      `function EUI.Foo()` has its definition recorded under the short name,
      while the rest of the suite calls it `EllesmereUI.Foo(`. Those calls are
      not restricted to the declaring file: the name they use is the shared
      one.

    The right-hand side has to be a name this index already knows owns a
    definition. Without that test `local n = count` would make `n` an alias for
    a number and credit every unrelated `n.foo(` in the file.
    """
    owners = {
        row["owner"]
        for row in symbols
        if row["kind"] in ("field", "method") and row["owner"]
    }

    # First the published paths, because a local almost never binds the table
    # directly -- it binds the path the table was published on
    # (`local PPc = EllesmereUI and EllesmereUI.PP`), so `EllesmereUI.PP = PP`
    # has to be resolved before `PPc` can mean anything.
    path_claims: dict[str, set[str]] = {}
    for src in sources:
        for m in TABLE_ALIAS_PATH.finditer(src.mask):
            path, target = m.group(1), m.group(2)
            if target in owners and path != target:
                path_claims.setdefault(path, set()).add(target)
    path_to_owner = {p: next(iter(t)) for p, t in path_claims.items() if len(t) == 1}

    local_claims: dict[tuple[str, str], set[str]] = {}
    for src in sources:
        for m in TABLE_ALIAS_LOCAL.finditer(src.mask):
            alias, rhs = m.group(1), m.group(2)
            target = rhs if rhs in owners else path_to_owner.get(rhs)
            if target and alias != target:
                local_claims.setdefault((src.rel, alias), set()).add(target)

    # An alias bound to two different tables names neither of them. Dropping it
    # is the same trade the function aliases make: a wrong caller is worse than
    # a missing one, because nothing about it looks wrong.
    locals_by_owner: dict[str, list[tuple[str, str]]] = {}
    for (rel, alias), targets in local_claims.items():
        if len(targets) == 1:
            locals_by_owner.setdefault(next(iter(targets)), []).append((rel, alias))

    paths_by_owner: dict[str, list[str]] = {}
    for path, owner in path_to_owner.items():
        paths_by_owner.setdefault(owner, []).append(path)

    canonical: dict[tuple[str, str], list[str]] = {}
    for (rel, alias), targets in local_claims.items():
        if len(targets) == 1:
            canonical[(rel, alias)] = [next(iter(targets))]

    return locals_by_owner, paths_by_owner, canonical


def collect_callers(
    sources: list[Source], symbols: list[dict], module_names: list[str]
) -> None:
    """Attach `callers`/`caller_count`, or `caller_ambiguity`, to each symbol.

    A caller list is written only when the call key belongs to exactly one
    definition. Roughly half of this codebase's definitions are option-table
    callbacks -- `getValue = function(info)` and friends, 1402 of them sharing
    one name -- and a list spread over 1402 identical keys says nothing while
    reading like an answer. Those rows carry the count of competing
    definitions instead, which is the signal to grep.

    A definition is also reached through the names it is aliased to. Every
    cross-module helper here is a file-local bound onto a shared table
    (`EllesmereUI.ComputeCastBarTint = ComputeCastBarTint`), so resolving only
    the name at the definition site counts the file's own calls and misses the
    rest of the suite -- silently, since the count is non-zero and looks like an
    answer. `collect_aliases` supplies those edges.
    """
    private_by_file = {src.rel: addon_table_names(src) for src in sources}
    module_by_file = {
        src.rel: module_of(src.rel, module_names) for src in sources
    }
    exports, imports = collect_aliases(sources, symbols)
    alias_locals, alias_paths, alias_canonical = collect_table_aliases(
        sources, symbols
    )

    def via_renamed_receiver(row: dict, sep: str) -> tuple[list, list[str]]:
        """Call sites reaching this definition through a renamed receiver."""
        found: list[tuple[str, int]] = []
        seen: list[str] = []
        name = row["name"]
        for rel, alias in alias_locals.get(row["owner"], ()):
            hits = [s for s in qualified.get((alias, sep, name), []) if s[0] == rel]
            if hits:
                found += hits
                seen.append(f"{alias}{sep}{name}")
        for path in alias_paths.get(row["owner"], ()):
            hits = qualified.get((path, sep, name), [])
            if hits:
                found += hits
                seen.append(f"{path}{sep}{name}")
        # The definition itself is written on a local alias -- calls elsewhere
        # in the suite use the shared name it was bound from.
        for real in alias_canonical.get((row["file"], row["owner"]), ()):
            hits = qualified.get((real, sep, name), [])
            if hits:
                found += hits
                seen.append(f"{real}{sep}{name}")
        return found, seen

    def scoped(row: dict) -> bool:
        return row["kind"] in ("field", "method") and owner_root(
            row["owner"]
        ) in private_by_file.get(row["file"], ())

    by_key: dict[tuple, int] = {}
    for row in symbols:
        k = call_key(row, scoped(row))
        by_key[k] = by_key.get(k, 0) + 1

    # Files declaring a local of a name: a global of that name is shadowed
    # there, so calls in those files belong to the local, not the global.
    shadowed: dict[str, set[str]] = {}
    for row in symbols:
        if row["kind"] == "local":
            shadowed.setdefault(row["name"], set()).add(row["file"])
    # Most file-scope locals are not function definitions, so the rows above
    # miss them. EllesmereUINameplates opens `local StartButtonGlow =
    # _G_Glows.StartButtonGlow`; its calls belong to that binding, not to the
    # same-named local in EllesmereUIAuraBuffReminders, which is what the
    # index credited them to.
    for src in sources:
        for name in src.file_locals:
            shadowed.setdefault(name, set()).add(src.rel)

    qualified: dict[tuple, list[tuple[str, int]]] = {}
    plain: dict[str, list[tuple[str, int]]] = {}
    for src in sources:
        # Hoisted out of the loop: this runs about 470k times over the tree, so
        # the attribute lookups cost more than the work between them.
        mask = src.mask
        rel = src.rel
        line_of = src.line
        for m in CALL.finditer(mask):
            recv, sep, short = m.groups()
            start = m.start(3)
            if recv is None:
                if start and mask[start - 1] in ".:":
                    # A receiver that is itself an expression --
                    # `GetFFD(f).method()` -- does not match the identifier
                    # pattern, so the call arrives here looking unqualified. It
                    # is not: this is a table field, and crediting it to a
                    # same-named local is a plain false edge.
                    continue
                bucket, key = plain, short
            else:
                bucket, key = qualified, (recv, sep, short)
            site = (rel, line_of(start))
            got = bucket.get(key)
            if got is None:
                bucket[key] = [site]
            else:
                got.append(site)

    for row in symbols:
        in_module = scoped(row)
        key = call_key(row, in_module)
        if by_key[key] > 1:
            row["caller_ambiguity"] = by_key[key]
            continue
        kind = row["kind"]
        # Second names this definition is reached through. Recorded on the row
        # because `caller_count` is otherwise unexplainable: a reader who greps
        # the definition's own name finds a fraction of the list and concludes
        # the index is wrong.
        names: list[str] = []
        if kind == "method":
            sites = list(qualified.get((row["owner"], ":", row["name"]), []))
            # `self:Foo()` resolves to the owner only inside the file that
            # defines the method; across files the receiver is unknowable.
            sites += [
                s
                for s in qualified.get(("self", ":", row["name"]), [])
                if s[0] == row["file"]
            ]
            renamed, seen = via_renamed_receiver(row, ":")
            sites += renamed
            names += seen
        elif kind == "field":
            sites = list(qualified.get((row["owner"], ".", row["name"]), []))
            for rel, local in imports.get((row["owner"], row["name"]), ()):
                sites += [s for s in plain.get(local, []) if s[0] == rel]
                names.append(local)
            renamed, seen = via_renamed_receiver(row, ".")
            sites += renamed
            names += seen
        elif kind == "local":
            sites = [s for s in plain.get(row["name"], []) if s[0] == row["file"]]
            for alias in exports.get((row["file"], row["name"]), ()):
                names.append(f"{alias[0]}.{alias[1]}")
                alias_sites = list(qualified.get((alias[0], ".", alias[1]), []))
                # A file that binds the export back to a local of its own calls
                # it bare. Widgets does this for a dozen core helpers, so
                # without the second hop those calls belong to nothing.
                for rel, local in imports.get(alias, ()):
                    alias_sites += [s for s in plain.get(local, []) if s[0] == rel]
                    names.append(local)
                if owner_root(alias[0]) in private_by_file.get(row["file"], ()):
                    # It was bound onto this file's own `ns`, so the export
                    # reaches only this module. Another module's `ns.Name` is a
                    # different table and a different function.
                    home = module_by_file.get(row["file"])
                    alias_sites = [
                        s for s in alias_sites if module_by_file.get(s[0]) == home
                    ]
                sites += alias_sites
        elif kind == "tablefield":
            # The table this was written into is reached under whatever name
            # holds it, which the definition site does not give. Bare `name(`
            # is not that call, so claiming those sites would be a false edge.
            #
            # No list, and no count either -- the same exit `caller_ambiguity`
            # takes. `caller_count: 0` is the index's word for "nothing calls
            # this", and writing it here would say that about the 85 rows that
            # reach this branch, whose callers were never looked for. The other
            # 6,866 table fields never arrive: they share a name with another
            # definition and the ambiguity check above claims them first.
            row["caller_unresolved"] = "table field"
            continue
        else:
            skip = shadowed.get(row["name"], ())
            sites = [s for s in plain.get(row["name"], []) if s[0] not in skip]

        if in_module:
            # The receiver is this module's own table; a same-named table in
            # another module is a different object.
            sites = [s for s in sites if module_by_file.get(s[0]) == row["module"]]

        # `local function Foo(` reads as a call to Foo; nothing else does, so
        # only this row's own declaration line is excluded. Dropping every
        # declaration line would lose a real call that shares a line with an
        # unrelated definition.
        own = (row["file"], row["line"])
        hits = sorted({f"{f}:{n}" for f, n in sites if (f, n) != own})
        if names:
            row["aliases"] = sorted(set(names))
        row["callers"] = hits[:CALLER_CAP]
        row["caller_count"] = len(hits)


# --------------------------------------------------------------------------
#  TOC parsing / module discovery
# --------------------------------------------------------------------------

TOC_DIRECTIVE = re.compile(r"^##[ \t]*([^:]+):[ \t]*(.*)$")


def parse_toc(path: Path) -> dict:
    meta: dict = {"directives": {}, "files": []}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        m = TOC_DIRECTIVE.match(line)
        if m:
            meta["directives"][m.group(1).strip()] = m.group(2).strip()
        elif not line.startswith("#"):
            meta["files"].append(line.replace("\\", "/"))
    return meta


def discover(root: Path) -> list[tuple[str, Path]]:
    """Return (module_name, toc_path) for the core addon and every child addon."""
    mods: list[tuple[str, Path]] = []
    core = root / "EllesmereUI.toc"
    if core.exists():
        mods.append(("EllesmereUI", core))
    for child in sorted(root.iterdir()):
        if not child.is_dir() or skip_dir(child.name):
            continue
        toc = child / f"{child.name}.toc"
        if toc.exists():
            mods.append((child.name, toc))
    return mods


def module_of(rel: str, module_names: list[str]) -> str:
    head = rel.split("/", 1)[0]
    return head if head in module_names else "EllesmereUI"


# The options pages do not live with the module they configure. They are one
# separate addon, EllesmereUIOptions, whose files are named after the module
# each one builds pages for. Attributing a reference by folder alone therefore
# credits every options row to EllesmereUIOptions, which puts it in a different
# module from the key it reads -- and "where is this setting's control built"
# then answers zero for every child module in the suite.
OPTIONS_PAGE = re.compile(r"^EUI_(.+?)_(?:Options|ManagerPages)\.lua$")


def options_page_module(rel: str, module_names: list[str]) -> str | None:
    """Which module's settings an options file builds controls for.

    Returns None for a file that is not an options page, and for the shared
    widget library, which belongs to no single module.
    """
    parts = rel.split("/")
    if len(parts) != 2 or parts[0] != "EllesmereUIOptions":
        return None
    fn = parts[1]

    m = OPTIONS_PAGE.match(fn)
    if m:
        stem = m.group(1)
        if stem.startswith("_"):  # EUI__General_Options.lua -- the suite's own page
            return "EllesmereUI"
        # EUI_QoL_RaidTools_Options.lua and EUI_QoL_Options.lua both configure
        # EllesmereUIQoL, so the longest module name that matches the head of
        # the stem wins over a literal join.
        candidates = [n for n in module_names if ("EllesmereUI" + stem.replace("_", "")) == n]
        if candidates:
            return candidates[0]
        head = stem.split("_", 1)[0]
        for name in sorted(module_names, key=len, reverse=True):
            if name == "EllesmereUI" + head:
                return name
        return None

    # EllesmereUIDataBars_Options.lua -- named for the module outright.
    if fn.endswith("_Options.lua"):
        stem = fn[: -len("_Options.lua")]
        return stem if stem in module_names else None
    return None


# --------------------------------------------------------------------------
#  Source collection + fingerprinting
# --------------------------------------------------------------------------

def iter_lua(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not skip_dir(d))
        for fn in sorted(filenames):
            if not fn.endswith(".lua"):
                continue
            rel = str(Path(dirpath, fn).relative_to(root)).replace("\\", "/")
            if rel.startswith(EXCLUDE_REL_PREFIXES):
                continue
            yield rel, Path(dirpath, fn)


def fingerprint(root: Path) -> tuple[str, int, int]:
    """Content hash of every indexed file, plus file and byte counts."""
    h = hashlib.sha256()
    files = 0
    total = 0
    for rel, path in iter_lua(root):
        data = path.read_bytes()
        h.update(rel.encode())
        h.update(hashlib.sha256(data).digest())
        files += 1
        total += len(data)
    for _, toc in discover(root):
        h.update(toc.read_bytes())
    return h.hexdigest(), files, total


def git_info(root: Path) -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=15
            ).stdout.strip()
        except Exception:
            return ""

    return {
        "head": run("rev-parse", "HEAD"),
        "short": run("rev-parse", "--short", "HEAD"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(run("status", "--porcelain", "--untracked-files=no")),
    }


# --------------------------------------------------------------------------
#  Root resolution
# --------------------------------------------------------------------------

def valid_root(p: Path) -> bool:
    return (p / "EllesmereUI.toc").is_file()


def root_candidates(explicit: str | None):
    """Yield candidate roots cheapest first, so the search can stop early.

    Yielded rather than collected because the wildcard sweep below costs 0.35 s
    -- 84% of a no-op `--ensure`, and the skill runs one before every lookup.
    An explicit path, the environment, and the previous build's root each name
    the answer outright for one stat, so the sweep should only run when none of
    them did.
    """
    if explicit:
        yield Path(explicit).expanduser()
    if os.environ.get("ELLESMEREUI_ROOT"):
        yield Path(os.environ["ELLESMEREUI_ROOT"]).expanduser()

    meta_path = INDEX_DIR / "meta.json"
    if meta_path.is_file():
        try:
            yield Path(json.loads(meta_path.read_text())["addon_root"])
        except Exception:
            pass

    # WoW installs sit at unpredictable depths, especially under Proton/Wine
    # prefixes (~/Faugus/<app>/drive_c/Program Files (x86)/World of Warcraft/...).
    # Walk a bounded set of wildcard depths rather than a recursive glob, which
    # would be unusably slow from $HOME.
    home = Path.home()
    tail = "World of Warcraft/_retail_/Interface/AddOns/EllesmereUI*"
    for base in (home, home / "Games", home / ".local/share", Path("/mnt"), Path("/media")):
        if not base.is_dir():
            continue
        for depth in range(6):
            try:
                yield from sorted(base.glob("*/" * depth + tail))
            except OSError:
                continue

    for base in (home / "Repos", home / "repos", home / "src", home / "code", home):
        if base.is_dir():
            yield from sorted(base.glob("EllesmereUI*"))


def resolve_root(explicit: str | None) -> Path:
    for c in root_candidates(explicit):
        try:
            if valid_root(c):
                return c.resolve()
        except OSError:
            continue

    sys.exit(
        "Could not locate the EllesmereUI addon root.\n"
        "Pass --root /path/to/EllesmereUI or set $ELLESMEREUI_ROOT."
    )


# --------------------------------------------------------------------------
#  Build
# --------------------------------------------------------------------------

def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")


def build(root: Path, fp: str, n_files: int, n_bytes: int) -> dict:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    mods = discover(root)
    module_names = [name for name, _ in mods]

    sources: list[Source] = []
    for rel, path in iter_lua(root):
        sources.append(Source(rel, path.read_text(encoding="utf-8", errors="replace")))

    symbols: list[dict] = []
    settings: list[dict] = []
    locale: dict[str, list[str]] = {}
    events: dict[str, list[str]] = {}
    slashes: list[dict] = []
    lines_by_file: dict[str, int] = {}

    for src in sources:
        mod = module_of(src.rel, module_names)
        lines_by_file[src.rel] = src.n_lines
        symbols.extend(extract_symbols(src, mod))
        settings.extend(extract_settings(src, mod))

        for m in LOCALE_CALL.finditer(src.text):
            if src.mask[m.start()] == " ":
                continue  # inside a comment
            locale.setdefault(m.group(2), []).append(f"{src.rel}:{src.line(m.start())}")

        # Event names and slash tokens live *inside* string literals, so these
        # two run against the original text; the mask is only consulted to
        # reject matches sitting in a comment.
        for m in EVENT_REG.finditer(src.text):
            if src.mask[m.start()] == " ":
                continue
            events.setdefault(m.group(1), []).append(f"{src.rel}:{src.line(m.start())}")

        for m in SLASH_DECL.finditer(src.text):
            # Check the token, not m.start() -- the match begins at the line's
            # indentation, which is a space in the mask for code and comment alike.
            if src.mask[m.start(1)] == " ":
                continue
            slashes.append(
                {
                    "command": m.group(3),
                    "token": m.group(1),
                    "module": mod,
                    "file": src.rel,
                    "line": src.line(m.start()),
                }
            )

    collect_callers(sources, symbols, module_names)

    # Cross-reference settings keys against every indexed file.
    keyset = {row["key"] for row in settings}
    refs = collect_refs(sources, keyset, module_names)
    defined_at = {f"{row['file']}:{row['line']}" for row in settings}
    page_owner = {}
    for src in sources:
        owner = options_page_module(src.rel, module_names)
        if owner:
            page_owner[src.rel] = owner

    def in_scope(mod: str, site: str, module: str) -> bool:
        if mod == module:
            return True
        # An options page counts for the module it configures, not the one its
        # file sits in. Scoping stays exact: a "size" row inside
        # EUI_Nameplates_Options.lua credits Nameplates and nothing else.
        return page_owner.get(site.rsplit(":", 1)[0]) == module

    for row in settings:
        hits = refs.get(row["key"], set())
        sites = sorted(
            s for mod, s in hits
            if in_scope(mod, s, row["module"]) and s not in defined_at
        )
        row["refs"] = sites[:60]
        row["ref_count"] = len(sites)
        # Membership of page_owner, not the file suffix: the RaidFrames and
        # PlayerAuraBars managers are options pages named _ManagerPages.lua.
        options = [s for s in sites if s.rsplit(":", 1)[0] in page_owner]
        row["options_refs"] = options[:10]
        # Every capped list carries its true length. Without this the caller
        # cannot tell ten references from ten of ninety -- and a silent
        # truncation reads as a complete answer to "where is this setting's
        # control built".
        row["options_ref_count"] = len(options)
        # Same short name declared and read in a module this key has nothing to
        # do with -- grep globally if the in-scope references do not explain the
        # behaviour you are chasing. An options page for this module is in
        # scope, so it is not counted here.
        row["refs_other_modules"] = len(
            [s for mod, s in hits if not in_scope(mod, s, row["module"])]
        )
        row["used_by"] = [row["module"]]

    # Second settings class: keys living directly on a SavedVariables global.
    sv_names: dict[str, str] = {}
    for name, toc in mods:
        for sv in parse_toc(toc)["directives"].get("SavedVariables", "").split(","):
            if sv.strip():
                sv_names.setdefault(sv.strip(), name)
    sv_rows = extract_saved_variable_keys(sources, sv_names, module_names)
    for row in sv_rows:
        row["refs_other_modules"] = 0
    settings.extend(sv_rows)

    # Third settings class: keys on a per-entity entry. Names already declared
    # at profile level keep both records -- the same short name genuinely
    # exists at two scopes here, and saying so is the answer to "I changed it
    # and nothing happened".
    scoped_rows = extract_scoped_settings(sources, module_names)
    if not scoped_rows and any(SCOPED_STORE_NAME in src.mask for src in sources):
        # The pass is driven by names that live in the addon, so a refactor can
        # retire it silently, and an index that quietly stops covering a whole
        # settings class is worse than one that fails loudly. Only a tree that
        # still has the store but yields no keys is a broken pass -- a tree
        # without it is simply a tree without per-entity settings.
        raise SystemExit(
            f"Found {SCOPED_STORE_NAME!r} in the source but extracted no keys from "
            f"it. A resolver in {SCOPED_RESOLVERS} was probably renamed -- update "
            "SCOPED_* in this file."
        )
    settings.extend(scoped_rows)

    module_rows: list[dict] = []
    for name, toc in mods:
        toc_meta = parse_toc(toc)
        folder = "" if name == "EllesmereUI" else name
        prefix = f"{folder}/" if folder else ""
        own = [
            s
            for s in sources
            if module_of(s.rel, module_names) == name
        ]
        module_rows.append(
            {
                "module": name,
                "folder": folder or ".",
                "toc": str(toc.relative_to(root)).replace("\\", "/"),
                "title": toc_meta["directives"].get("Title", ""),
                "notes": toc_meta["directives"].get("Notes", ""),
                "version": toc_meta["directives"].get("Version", ""),
                "interface": toc_meta["directives"].get("Interface", ""),
                "saved_variables": [
                    v.strip()
                    for v in toc_meta["directives"].get("SavedVariables", "").split(",")
                    if v.strip()
                ],
                "load_order": [prefix + f for f in toc_meta["files"] if f.endswith(".lua")],
                "lua_files": len(own),
                "lines": sum(s.n_lines for s in own),
                "symbols": sum(1 for r in symbols if r["module"] == name),
                "settings": sum(1 for r in settings if r["module"] == name),
                "slash_commands": sorted(
                    {r["command"] for r in slashes if r["module"] == name and r["command"]}
                ),
            }
        )

    write_jsonl(INDEX_DIR / "modules.jsonl", module_rows)
    write_jsonl(INDEX_DIR / "symbols.jsonl", sorted(symbols, key=lambda r: (r["name"], r["file"], r["line"])))
    write_jsonl(INDEX_DIR / "settings.jsonl", sorted(settings, key=lambda r: (r["key"], r["module"])))
    write_jsonl(
        INDEX_DIR / "locale.jsonl",
        [
            {"key": k, "count": len(v), "sites": sorted(v)[:40]}
            for k, v in sorted(locale.items())
        ],
    )
    write_jsonl(
        INDEX_DIR / "events.jsonl",
        [
            {"event": k, "count": len(v), "sites": sorted(v)[:40]}
            for k, v in sorted(events.items())
        ],
    )
    write_jsonl(INDEX_DIR / "slash.jsonl", sorted(slashes, key=lambda r: r["command"]))

    meta = {
        "builder_version": BUILDER_VERSION,
        "addon_root": str(root),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_fingerprint": fp,
        "git": git_info(root),
        "counts": {
            "modules": len(module_rows),
            "lua_files": n_files,
            "lua_lines": sum(lines_by_file.values()),
            "lua_bytes": n_bytes,
            "symbols": len(symbols),
            "call_edges": sum(r.get("caller_count", 0) for r in symbols),
            "symbols_without_callers": sum(
                1 for r in symbols if r.get("caller_count") == 0
            ),
            "symbols_ambiguous_callers": sum(
                1 for r in symbols if "caller_ambiguity" in r
            ),
            "settings": len(settings),
            "locale_keys": len(locale),
            "events": len(events),
            "slash_commands": len(slashes),
        },
    }
    (INDEX_DIR / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def load_meta() -> dict | None:
    p = INDEX_DIR / "meta.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="path to the EllesmereUI addon checkout")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--ensure", action="store_true", help="rebuild only if stale (default)")
    mode.add_argument("--check", action="store_true", help="report freshness, exit 1 if stale")
    mode.add_argument("--force", action="store_true", help="rebuild unconditionally")
    args = ap.parse_args()

    root = resolve_root(args.root)
    meta = load_meta()
    fp, n_files, n_bytes = fingerprint(root)

    fresh = (
        meta is not None
        and meta.get("source_fingerprint") == fp
        and meta.get("builder_version") == BUILDER_VERSION
        and meta.get("addon_root") == str(root)
        and all((INDEX_DIR / f).is_file() for f in
                ("modules.jsonl", "symbols.jsonl", "settings.jsonl",
                 "locale.jsonl", "events.jsonl", "slash.jsonl"))
    )

    if args.check:
        if fresh:
            print(f"FRESH  {root}  ({meta['git'].get('short', '?')}, built {meta['built_at']})")
            return 0
        print(f"STALE  {root}  -- run: python3 {Path(__file__).name} --ensure")
        return 1

    if fresh and not args.force:
        print(f"FRESH  index up to date ({meta['counts']['symbols']} symbols, "
              f"{meta['counts']['settings']} settings) -- nothing to do")
        return 0

    meta = build(root, fp, n_files, n_bytes)
    c = meta["counts"]
    g = meta["git"]
    print(
        f"Built index from {root}\n"
        f"  git      {g.get('short', '?')} on {g.get('branch', '?')}"
        f"{' (dirty)' if g.get('dirty') else ''}\n"
        f"  source   {c['lua_files']} Lua files, {c['lua_lines']:,} lines\n"
        f"  modules  {c['modules']}\n"
        f"  symbols  {c['symbols']:,}\n"
        f"  settings {c['settings']:,}\n"
        f"  locale   {c['locale_keys']:,} keys\n"
        f"  events   {c['events']}\n"
        f"  slash    {c['slash_commands']}\n"
        f"  -> {INDEX_DIR}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
