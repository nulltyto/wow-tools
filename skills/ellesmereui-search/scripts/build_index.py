#!/usr/bin/env python3
"""Build a navigation index for the EllesmereUI World of Warcraft addon suite.

The addon is ~137 Lua files / ~400k lines with single files over 1 MB, so plain
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

BUILDER_VERSION = 1

SKILL_DIR = Path(__file__).resolve().parent.parent
INDEX_DIR = SKILL_DIR / "references" / "index"

# Directories never worth indexing: vendored libraries, the packager's output
# copy of the whole tree, and the per-locale translation tables (bulk data --
# the canonical key list lives in Locales/_keys.txt).
EXCLUDE_DIRS = {".git", ".release", ".github", ".kiro", ".vscode", ".claude", "Libs", "media", "patches"}
EXCLUDE_REL_PREFIXES = ("Locales/",)


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


def mask_lua(text: str) -> str:
    """Blank comment bodies and string contents, preserving length and offsets."""
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
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
            for k in range(i, end):
                if out[k] != "\n":
                    out[k] = " "
            i = end
            continue

        # Long string [[ ... ]]
        opener = _long_bracket_len(text, i)
        if opener:
            level = opener - 2
            close = "]" + "=" * level + "]"
            end = text.find(close, i + opener)
            end = n if end == -1 else end + len(close)
            for k in range(i + opener, min(end - len(close), n) if end != n else n):
                if out[k] != "\n":
                    out[k] = " "
            i = end
            continue

        # Short string
        if c in "\"'":
            quote = c
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote or text[j] == "\n":
                    break
                j += 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1 if j < n and text[j] == quote else j
            continue

        i += 1

    return "".join(out)


class Source:
    """One Lua file: original text, masked text, and line-number lookup."""

    __slots__ = ("rel", "text", "mask", "_nl")

    def __init__(self, rel: str, text: str):
        self.rel = rel
        self.text = text
        self.mask = mask_lua(text)
        self._nl = [m.start() for m in re.finditer("\n", text)]

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self._nl, offset) + 1

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


def classify(name: str, is_local: bool) -> tuple[str, str, str]:
    """Return (kind, owner, short_name) for a definition name."""
    if ":" in name:
        owner, short = name.rsplit(":", 1)
        return "method", owner, short
    if "." in name:
        owner, short = name.rsplit(".", 1)
        return "field", owner, short
    return ("local" if is_local else "global"), "", name


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
            kind, owner, short = classify(name, is_local)
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

DEFAULTS_ASSIGN = re.compile(
    r"^[ \t]*(?:local[ \t]+)?([A-Za-z_][\w.]*)[ \t]*=[ \t]*\{",
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

        if j < end and mask[j] == "{":
            pending = key
            i = j
            continue

        literal, after = _read_value(mask, text, j, end)
        out.append((".".join(path[1:] + [key]), literal, src.line(i)))
        i = after

    return out


def extract_settings(src: Source, module: str) -> list[dict]:
    rows: list[dict] = []
    for m in DEFAULTS_ASSIGN.finditer(src.mask):
        var = m.group(1)
        if not DEFAULTS_NAME.search(var.split(".")[-1]):
            continue
        open_idx = src.mask.index("{", m.start())
        for path, literal, line in parse_defaults_table(src, open_idx):
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
                    "table": var,
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
    for sv in sv_names:
        attr = re.compile(r"\b" + re.escape(sv) + r"[ \t]*\.[ \t]*([A-Za-z_]\w*)")
        sub = re.compile(r"\b" + re.escape(sv) + r"[ \t]*\[[ \t]*[\"']([A-Za-z_]\w*)[\"']")
        for src in sources:
            mod = module_of(src.rel, module_names)
            for m in attr.finditer(src.mask):
                hits.setdefault((sv, m.group(1)), set()).add(
                    (mod, f"{src.rel}:{src.line(m.start())}")
                )
            for m in sub.finditer(src.text):
                if src.mask[m.start(1)] != " ":
                    continue
                hits.setdefault((sv, m.group(1)), set()).add(
                    (mod, f"{src.rel}:{src.line(m.start())}")
                )

    rows: list[dict] = []
    for (sv, key), sites in hits.items():
        ordered = sorted(s for _, s in sites)
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
                "options_refs": [s for s in ordered if "_Options.lua" in s][:10],
                "used_by": sorted({mod for mod, _ in sites}),
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
            if src.mask[m.start(1)] != " ":
                continue
            add(m.group(1), src, m.start())
    return refs


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
        if not child.is_dir() or child.name in EXCLUDE_DIRS:
            continue
        toc = child / f"{child.name}.toc"
        if toc.exists():
            mods.append((child.name, toc))
    return mods


def module_of(rel: str, module_names: list[str]) -> str:
    head = rel.split("/", 1)[0]
    return head if head in module_names else "EllesmereUI"


# --------------------------------------------------------------------------
#  Source collection + fingerprinting
# --------------------------------------------------------------------------

def iter_lua(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
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


def resolve_root(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("ELLESMEREUI_ROOT"):
        candidates.append(Path(os.environ["ELLESMEREUI_ROOT"]).expanduser())

    meta_path = INDEX_DIR / "meta.json"
    if meta_path.is_file():
        try:
            candidates.append(Path(json.loads(meta_path.read_text())["addon_root"]))
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
                candidates.extend(sorted(base.glob("*/" * depth + tail)))
            except OSError:
                continue

    for base in (home / "Repos", home / "repos", home / "src", home / "code", home):
        if base.is_dir():
            candidates.extend(sorted(base.glob("EllesmereUI*")))

    for c in candidates:
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

    # Cross-reference settings keys against every indexed file.
    keyset = {row["key"] for row in settings}
    refs = collect_refs(sources, keyset, module_names)
    defined_at = {f"{row['file']}:{row['line']}" for row in settings}
    for row in settings:
        hits = refs.get(row["key"], set())
        sites = sorted(
            s for mod, s in hits if mod == row["module"] and s not in defined_at
        )
        row["refs"] = sites[:60]
        row["ref_count"] = len(sites)
        row["options_refs"] = [s for s in sites if "_Options.lua" in s][:10]
        # Same short name defined/read in other modules -- grep globally if the
        # in-module references do not explain the behaviour you are chasing.
        row["refs_other_modules"] = len(hits) - len(
            [s for mod, s in hits if mod == row["module"]]
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
