#!/usr/bin/env python3
"""Check EllesmereUI source against the code style in .github/CONTRIBUTING.md.

By default this checks only the lines your branch changes, because the tree
carries legacy violations that predate the rules -- a whole-tree run is noise,
a diff-scoped run is a gate.

    check_style.py                     changed lines vs the merge-base with main
    check_style.py --base origin/main  pick the base ref explicitly
    check_style.py --all               every file (expect legacy findings)
    check_style.py --files a.lua b.lua just these files, whole file
    check_style.py --json              machine-readable output

Exit status is 1 when an error-severity finding is reported, 0 otherwise.
Warnings and notes never fail the run unless --strict is passed.

Suppress a single line with a trailing or preceding comment:

    local s = "naive"  -- eui-style: allow ascii

Rules, and how far each can be trusted:

  lua51            error   goto, ::labels::, and 5.2+/5.3+ operators. Exact.
  ascii            error   any non-ASCII byte. Exact. U+FFFD is called out
                           separately as already-corrupted text.
  popup            error   StaticPopup_Show. Exact.
  dualrow-nil      error   missing or nil right slot. Exact.
  dualrow-left-gap error   placeholder label in the left slot. Exact.
  tooltip          warning plain-text GameTooltip session with no data setter.
                           A heuristic -- a rich multi-line tooltip on a
                           Blizzard frame can look the same.
  dualrow-empty    note    a placeholder right slot. Whether it is the last
                           row of its section is not statically decidable
                           (if/else branches and local helpers break any
                           boundary rule), so this only asks you to look.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Vendored libraries, the packager's output copy, and the translation tables
# (which are non-ASCII by definition) are never ours to lint.
EXCLUDE_DIRS = {"Libs", ".release", "Locales", ".git", ".github", "media", "patches"}

ERROR, WARNING, NOTE = "error", "warning", "note"


# --------------------------------------------------------------------------
#  Lua source masking
# --------------------------------------------------------------------------
# Structural passes run against a copy of the source in which comment bodies
# and string contents are replaced by spaces, byte offsets preserved. Without
# it, a brace in a comment breaks argument matching and a `goto` in a string
# is a false positive.

def _long_bracket_len(text: str, i: int) -> int:
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

        if c == "-" and i + 1 < n and text[i + 1] == "-":
            opener = _long_bracket_len(text, i + 2) if i + 2 < n else 0
            if opener:
                close = "]" + "=" * (opener - 2) + "]"
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

        opener = _long_bracket_len(text, i)
        if opener:
            close = "]" + "=" * (opener - 2) + "]"
            end = text.find(close, i + opener)
            end = n if end == -1 else end + len(close)
            stop = (end - len(close)) if end != n else n
            for k in range(i + opener, stop):
                if out[k] != "\n":
                    out[k] = " "
            i = end
            continue

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
    """One Lua file: original text, masked text, line lookup, suppressions."""

    def __init__(self, path: Path, rel: str):
        self.path = path
        self.rel = rel
        self.text = path.read_text(encoding="utf-8", errors="replace")
        self.mask = mask_lua(self.text)
        self.lines = self.text.splitlines()
        self._nl = [m.start() for m in re.finditer("\n", self.text)]

    def line_of(self, offset: int) -> int:
        lo, hi = 0, len(self._nl)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._nl[mid] < offset:
                lo = mid + 1
            else:
                hi = mid
        return lo + 1

    def source_line(self, line: int) -> str:
        return self.lines[line - 1] if 0 < line <= len(self.lines) else ""


SUPPRESS = re.compile(r"--\s*eui-style:\s*allow\s+([\w, -]+)")


def suppressed(src: Source, line: int, rule: str) -> bool:
    """A rule is suppressed by a comment on its line or the line above."""
    for probe in (line, line - 1):
        m = SUPPRESS.search(src.source_line(probe))
        if m and rule in {r.strip() for r in m.group(1).replace(",", " ").split()}:
            return True
    return False


# --------------------------------------------------------------------------
#  Findings
# --------------------------------------------------------------------------

class Finding:
    def __init__(self, rel, line, severity, rule, message, hint=""):
        self.rel, self.line = rel, line
        self.severity, self.rule = severity, rule
        self.message, self.hint = message, hint

    def as_dict(self):
        return {
            "file": self.rel, "line": self.line, "severity": self.severity,
            "rule": self.rule, "message": self.message, "hint": self.hint,
        }


# --------------------------------------------------------------------------
#  Rule: Lua 5.1 only
# --------------------------------------------------------------------------

LUA52 = [
    (re.compile(r"\bgoto\b"), "goto", "`goto` is Lua 5.2+; WoW runs Lua 5.1"),
    (re.compile(r"::[A-Za-z_]\w*::"), "goto", "`::label::` is Lua 5.2+; WoW runs Lua 5.1"),
    (re.compile(r"//"), "intdiv", "`//` (floor division) is Lua 5.3+; use math.floor(a / b)"),
    (re.compile(r"(?<![&])&(?!&)"), "bitop", "bitwise `&` is Lua 5.3+; use bit.band"),
    (re.compile(r"(?<![|])\|(?!\|)"), "bitop", "bitwise `|` is Lua 5.3+; use bit.bor"),
    (re.compile(r"~(?!=)"), "bitop", "bitwise `~` is Lua 5.3+; use bit.bxor / bit.bnot"),
    (re.compile(r"<<"), "bitop", "bitwise `<<` is Lua 5.3+; use bit.lshift"),
    (re.compile(r">>"), "bitop", "bitwise `>>` is Lua 5.3+; use bit.rshift"),
]


def check_lua51(src: Source):
    for rx, _kind, message in LUA52:
        for m in rx.finditer(src.mask):
            line = src.line_of(m.start())
            if not suppressed(src, line, "lua51"):
                yield Finding(src.rel, line, ERROR, "lua51", message)


# --------------------------------------------------------------------------
#  Rule: ASCII only
# --------------------------------------------------------------------------

REPLACEMENTS = {
    "—": "--", "–": "-", "─": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "→": "->", "←": "<-", "•": "*",
    "…": "...", " ": " ", "×": "x", "°": " degrees",
    "≥": ">=", "≤": "<=", "·": "*", "✓": "y", "✗": "x",
}


def check_ascii(src: Source):
    for idx, text in enumerate(src.lines, start=1):
        bad = [(col, ch) for col, ch in enumerate(text, start=1) if ord(ch) > 127]
        if not bad or suppressed(src, idx, "ascii"):
            continue
        if any(ch == "�" for _, ch in bad):
            yield Finding(
                src.rel, idx, ERROR, "ascii",
                "U+FFFD replacement character -- this text is already corrupted",
                "Restore the intended ASCII punctuation; a multi-byte character "
                "was mangled here, which is exactly what the ASCII rule prevents.",
            )
            continue
        col, ch = bad[0]
        swap = REPLACEMENTS.get(ch)
        extra = f" ({len(bad)} on this line)" if len(bad) > 1 else ""
        yield Finding(
            src.rel, idx, ERROR, "ascii",
            f"non-ASCII {ch!r} (U+{ord(ch):04X}) at column {col}{extra}",
            f"Replace with {swap!r}." if swap else
            "Multi-byte characters corrupt in the packaging pipeline. If this is "
            "intentional (locale matching data), add: -- eui-style: allow ascii",
        )


# --------------------------------------------------------------------------
#  Rule: house UI systems
# --------------------------------------------------------------------------

STATIC_POPUP = re.compile(r"\bStaticPopup_Show\s*\(")

# GameTooltip setters that load real game data. Anything else that fills a
# tooltip is plain text and belongs to the house widget tooltip.
TOOLTIP_DATA_SETTER = re.compile(
    r"GameTooltip:Set(?!Owner\b|Text\b|Padding\b|Anchor\b|Scale\b|MinimumWidth\b"
    r"|BackdropBorderColor\b|Point\b|Parent\b|Frame\b|Clamped\b|Ignore\b)\w+\s*\("
)
TOOLTIP_PLAIN = re.compile(r"GameTooltip:(?:SetText|AddLine|AddDoubleLine)\s*\(")
TOOLTIP_OWNER = re.compile(r"GameTooltip:SetOwner\s*\(")


def check_popup(src: Source):
    for m in STATIC_POPUP.finditer(src.mask):
        line = src.line_of(m.start())
        if not suppressed(src, line, "popup"):
            yield Finding(
                src.rel, line, ERROR, "popup",
                "StaticPopup_Show is a Blizzard default",
                "Confirmations use EllesmereUI:ShowConfirmPopup.",
            )


def check_tooltip(src: Source):
    """Flag a tooltip session that only ever receives plain text.

    A session runs from SetOwner to the next Show(). If nothing in it loads
    game data (SetHyperlink, SetSpellByID, ...), the tooltip is plain text
    and should go through the house helper.
    """
    for m in TOOLTIP_OWNER.finditer(src.mask):
        end = src.mask.find("GameTooltip:Show", m.end())
        end = end if end != -1 else min(m.end() + 4000, len(src.mask))
        body = src.mask[m.start():end]
        if not TOOLTIP_PLAIN.search(body) or TOOLTIP_DATA_SETTER.search(body):
            continue
        line = src.line_of(m.start())
        if suppressed(src, line, "tooltip"):
            continue
        finding = Finding(
            src.rel, line, WARNING, "tooltip",
            "plain-text tooltip built on GameTooltip",
            "Use EllesmereUI.ShowWidgetTooltip / HideWidgetTooltip. Item and "
            "spell tooltips (GameTooltip:SetHyperlink and friends) are fine; "
            "if this is one, add: -- eui-style: allow tooltip",
        )
        yield finding, (line, src.line_of(end))


# --------------------------------------------------------------------------
#  Rule: DualRow slots
# --------------------------------------------------------------------------

DUALROW_CALL = re.compile(r"\b[\w.]+:DualRow\s*\(")
DUALROW_DEF = re.compile(r"\bfunction\s+[\w.]+:DualRow\b")
EMPTY_LABEL = re.compile(r'^\{\s*type\s*=\s*"label"\s*,\s*text\s*=\s*""\s*,?\s*\}$')


def split_call_args(mask: str, open_idx: int):
    """Split the top-level arguments of the call whose '(' sits at open_idx."""
    depth = 0
    args = []
    start = open_idx + 1
    i = open_idx
    while i < len(mask):
        c = mask[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append((start, i))
                return args, i
        elif c == "," and depth == 1:
            args.append((start, i))
            start = i + 1
        i += 1
    return args, len(mask)


def is_empty_label(raw: str) -> bool:
    return bool(EMPTY_LABEL.match(" ".join(raw.split())))


def check_dualrow(src: Source):
    for m in DUALROW_CALL.finditer(src.mask):
        if DUALROW_DEF.search(src.mask, max(0, m.start() - 60), m.end()):
            continue
        args, end_idx = split_call_args(src.mask, m.end() - 1)
        line = src.line_of(m.start())
        span = (line, src.line_of(end_idx))

        left = src.text[args[2][0]:args[2][1]].strip() if len(args) >= 3 else ""
        right = src.text[args[3][0]:args[3][1]].strip() if len(args) >= 4 else ""

        if len(args) < 4 or right == "nil":
            what = "no right slot argument" if len(args) < 4 else "nil right slot"
            if not suppressed(src, line, "dualrow-nil"):
                yield Finding(
                    src.rel, line, ERROR, "dualrow-nil",
                    f"W:DualRow called with {what}",
                    'Pass { type = "label", text = "" } instead of leaving the '
                    "slot empty.",
                ), span
            continue

        if is_empty_label(left):
            if not suppressed(src, line, "dualrow-left-gap"):
                yield Finding(
                    src.rel, line, ERROR, "dualrow-left-gap",
                    "placeholder label in the left slot of W:DualRow",
                    "Fill slots left to right with no gaps -- move the real "
                    "widget into the left slot.",
                ), span
            continue

        if is_empty_label(right) and not suppressed(src, line, "dualrow-empty"):
            yield Finding(
                src.rel, line, NOTE, "dualrow-empty",
                "empty right slot -- confirm this is the last row of its section",
                "Only the last row of a section may have an empty slot. Static "
                "analysis cannot tell where a section ends (if/else branches and "
                "local helpers sit between rows), so this one is on you.",
            ), span


# --------------------------------------------------------------------------
#  Scope: which lines count
# --------------------------------------------------------------------------

def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True,
    ).stdout.strip()


def resolve_base(root: Path, explicit: str | None) -> str | None:
    for ref in ([explicit] if explicit else ["origin/main", "main", "origin/master", "master"]):
        if ref and git(root, "rev-parse", "--verify", "--quiet", ref):
            base = git(root, "merge-base", ref, "HEAD")
            return base or ref
    return None


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(root: Path, base: str) -> dict[str, set[int]]:
    """Map relative path -> set of line numbers added or modified vs base.

    Compares base to the working tree, so uncommitted edits are included --
    this runs before you push, not after.
    """
    # Force the a/ b/ prefixes: diff.mnemonicPrefix in the user's git config
    # emits c/ and w/ instead, which would silently match no files and turn
    # this gate into a no-op.
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--unified=0", "--no-color",
         "--src-prefix=a/", "--dst-prefix=b/", base, "--", "*.lua"],
        capture_output=True, text=True,
    ).stdout
    result: dict[str, set[int]] = {}
    current: set[int] | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = result.setdefault(line[6:], set())
        elif line.startswith("+++ /dev/null"):
            current = None
        elif current is not None:
            m = HUNK.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2) or 1)
                current.update(range(start, start + count))
    return {k: v for k, v in result.items() if v}


def excluded(rel: str) -> bool:
    return any(part in EXCLUDE_DIRS for part in Path(rel).parts[:-1])


# --------------------------------------------------------------------------
#  Driver
# --------------------------------------------------------------------------

SIMPLE_RULES = (check_lua51, check_ascii, check_popup)
SPAN_RULES = (check_tooltip, check_dualrow)


def check_file(src: Source, scope: set[int] | None) -> list[Finding]:
    """Run every rule. `scope` limits findings to changed lines (None = all)."""
    out: list[Finding] = []

    for rule in SIMPLE_RULES:
        for finding in rule(src):
            if scope is None or finding.line in scope:
                out.append(finding)

    # A DualRow call or a tooltip session spans many lines; it is in scope if
    # the change touched any line of the construct, not just its first.
    for rule in SPAN_RULES:
        for finding, span in rule(src):
            if scope is None or any(ln in scope for ln in range(span[0], span[1] + 1)):
                out.append(finding)

    return sorted(out, key=lambda f: (f.line, f.rule))


def resolve_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).expanduser()
    else:
        env = os.environ.get("ELLESMEREUI_ROOT")
        root = Path(env).expanduser() if env else Path.cwd()
    if not (root / "EllesmereUI.toc").is_file():
        top = git(root, "rev-parse", "--show-toplevel")
        if top and (Path(top) / "EllesmereUI.toc").is_file():
            return Path(top)
        sys.exit(
            f"{root} is not an EllesmereUI checkout (no EllesmereUI.toc).\n"
            "Run from inside the addon, pass --root, or set $ELLESMEREUI_ROOT."
        )
    return root.resolve()


SEVERITY_ORDER = {ERROR: 0, WARNING: 1, NOTE: 2}
LABEL = {ERROR: "error", WARNING: "warn ", NOTE: "note "}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="path to the EllesmereUI checkout")
    ap.add_argument("--base", help="git ref to diff against (default: origin/main, then main)")
    ap.add_argument("--all", action="store_true", help="check every file, not just the diff")
    ap.add_argument("--files", nargs="+", help="check these files in full")
    ap.add_argument("--strict", action="store_true", help="warnings fail too")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    root = resolve_root(args.root)

    targets: list[tuple[Path, str, set[int] | None]] = []
    if args.files:
        for f in args.files:
            p = Path(f).expanduser().resolve()
            rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
            targets.append((p, rel, None))
        scope_desc = f"{len(targets)} file(s), in full"
    elif args.all:
        for p in sorted(root.rglob("*.lua")):
            rel = str(p.relative_to(root))
            if not excluded(rel):
                targets.append((p, rel, None))
        scope_desc = f"whole tree ({len(targets)} files)"
    else:
        base = resolve_base(root, args.base)
        if base is None:
            sys.exit("Could not resolve a base ref. Pass --base, or use --all.")
        changed = changed_lines(root, base)
        for rel, lines in sorted(changed.items()):
            p = root / rel
            if p.is_file() and not excluded(rel):
                targets.append((p, rel, lines))
        short = git(root, "rev-parse", "--short", base) or base
        scope_desc = (f"changed lines vs {args.base or 'main'} ({short}): "
                      f"{len(targets)} file(s), "
                      f"{sum(len(v) for v in changed.values())} line(s)")

    findings: list[Finding] = []
    for path, rel, scope in targets:
        findings.extend(check_file(Source(path, rel), scope))

    if args.json:
        print(json.dumps({
            "root": str(root),
            "scope": scope_desc,
            "findings": [f.as_dict() for f in findings],
        }, indent=2))
    else:
        print(f"EllesmereUI style check -- {scope_desc}\n")
        if not findings:
            print("Clean.")
        else:
            by_file: dict[str, list[Finding]] = {}
            for f in findings:
                by_file.setdefault(f.rel, []).append(f)
            for rel in sorted(by_file):
                print(rel)
                for f in sorted(by_file[rel],
                                key=lambda x: (x.line, SEVERITY_ORDER[x.severity])):
                    print(f"  {LABEL[f.severity]} {f.line:>6}  [{f.rule}] {f.message}")
                    if f.hint:
                        print(f"                  {f.hint}")
                print()

    counts = {s: sum(1 for f in findings if f.severity == s)
              for s in (ERROR, WARNING, NOTE)}
    if findings and not args.json:
        print(f"{counts[ERROR]} error(s), {counts[WARNING]} warning(s), "
              f"{counts[NOTE]} note(s)")

    if counts[ERROR]:
        return 1
    if args.strict and counts[WARNING]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
