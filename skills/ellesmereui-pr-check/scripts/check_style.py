#!/usr/bin/env python3
"""Check EllesmereUI source against the code style in .github/CONTRIBUTING.md.

By default this checks only the lines your branch changes, because the tree
carries legacy violations that predate the rules -- a whole-tree run is noise,
a diff-scoped run is a gate.

    check_style.py                     changed lines vs the merge-base with main
    check_style.py --staged            staged lines only (what a commit records)
    check_style.py --base origin/main  pick the base ref explicitly
    check_style.py --all               every file (expect legacy findings)
    check_style.py --files a.lua b.lua just these files, whole file
    check_style.py --json              machine-readable output
    check_style.py --install-hook      install --staged as a git pre-commit hook

Exit status is 1 when an error-severity finding is reported, 0 otherwise.
Warnings and notes never fail the run unless --strict is passed.

Suppress a single line with a trailing or preceding comment:

    local s = "naive"  -- eui-style: allow ascii

Rules, and how far each can be trusted:

  lua51            error   goto, ::labels::, and 5.2+/5.3+ operators. Exact.
  ascii            error   any non-ASCII byte. Exact. U+FFFD is called out
                           separately as already-corrupted text.
  popup            error   StaticPopup_Show. Exact.
  comment-budget   error   more than 8 lines of comment in one block (30 for
                           the file header). Counts changed lines only, so a
                           legacy block reports nothing until you extend it.
                           Exact on the count; whether the prose earns its
                           length is for you to judge.
  dualrow-nil      error   missing or nil right slot. Exact.
  dualrow-left-gap error   placeholder label in the left slot. Exact.
  tooltip          warning plain-text GameTooltip session with no data setter.
                           A heuristic -- a rich multi-line tooltip on a
                           Blizzard frame can look the same.
  dualrow-empty    note    a placeholder right slot. Whether it is the last
                           row of its section is not statically decidable
                           (if/else branches and local helpers break any
                           boundary rule), so this only asks you to look.
  thirdparty-credit
                   error   a named third-party addon next to derivation
                           language ("adapted from", "credit to"). Exact on
                           the words; what it means is for you to establish.
  thirdparty       warning a named third-party addon in code or comments.
                           EllesmereUI names plenty of them legitimately, so
                           this asks a question rather than making a claim:
                           confirm nothing was copied from that addon.
  thirdparty-maybe note    the same, for names that are also ordinary words
                           (Atlas, Cell, Details, Paste). Never fails.
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


# Only four characters can open a masked region, so the scan jumps between
# them rather than visiting every character.
_MASK_OPENER = re.compile(r"""[-\["']""")
_NOT_NEWLINE = re.compile(r"[^\n]")


def _blank(segment: str) -> str:
    """The segment with every character but a newline turned into a space.

    Newlines survive inside strings too. `comment_only_lines` zips
    `mask.splitlines()` against the source lines, so a backslash-escaped
    newline blanked away would shift every later line of that file.
    """
    return _NOT_NEWLINE.sub(" ", segment)


def mask_lua(text: str, comments: list[tuple[int, int]] | None = None) -> str:
    """Blank comment bodies and string contents, preserving length and offsets.

    Pass `comments` to also collect the (start, end) offsets of each comment.
    Masking alone cannot tell one blanked region from another, and the comment
    budget has to distinguish a comment from a multi-line string.
    """
    n = len(text)
    out: list[str] = []
    kept = 0
    i = 0
    while i < n:
        m = _MASK_OPENER.search(text, i)
        if m is None:
            break
        i = m.start()
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
            if comments is not None:
                comments.append((i, end))
            blank_from, blank_to = i, end
        else:
            opener = _long_bracket_len(text, i)
            if opener:
                close = "]" + "=" * (opener - 2) + "]"
                end = text.find(close, i + opener)
                end = n if end == -1 else end + len(close)
                blank_from = i + opener
                blank_to = (end - len(close)) if end != n else n
            elif c in "\"'":
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
                i += 1
                continue

        if blank_to > blank_from:
            out.append(text[kept:blank_from])
            out.append(_blank(text[blank_from:blank_to]))
            kept = blank_to
        i = end

    out.append(text[kept:])
    return "".join(out)


class Source:
    """One Lua file: original text, masked text, line lookup, suppressions."""

    def __init__(self, path: Path, rel: str, text: str | None = None):
        self.path = path
        self.rel = rel
        # `text` is passed when checking staged content, which is not what is
        # on disk if the file was edited after `git add`.
        self.text = (text if text is not None
                     else path.read_text(encoding="utf-8", errors="replace"))
        spans: list[tuple[int, int]] = []
        self.mask = mask_lua(self.text, spans)
        self.lines = self.text.splitlines()
        self.mask_lines = self.mask.splitlines()
        self._nl = [m.start() for m in re.finditer("\n", self.text)]
        self._comment_spans = spans

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

    def comment_only_lines(self) -> set[int]:
        """Line numbers that carry a comment and nothing else.

        A trailing comment on a line of code is excluded: its line still has
        content after masking. Both `--` runs and `--[[ ]]` blocks count.
        """
        out: set[int] = set()
        for start, end in self._comment_spans:
            for ln in range(self.line_of(start), self.line_of(max(start, end - 1)) + 1):
                if 0 < ln <= len(self.mask_lines) and not self.mask_lines[ln - 1].strip():
                    out.add(ln)
        return out


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
#  Rule: comment budget
# --------------------------------------------------------------------------
# A comment block is capped at COMMENT_BUDGET lines. The cap is on length
# alone, because length is the part a linter can measure: whether a comment
# earns its place is a judgment, whether it runs to half a screen is a fact.
#
# The file header is the one comment that reliably earns its length -- it says
# what the file is and what its commands are -- so the first block of a file
# gets HEADER_BUDGET instead.
#
# Only changed lines count toward the budget. Editing one word inside a long
# legacy block reports nothing; adding a new block over the cap reports it.

COMMENT_BUDGET = 8
HEADER_BUDGET = 30

# A header may sit under a shebang or a blank line, but not below real code.
HEADER_START = 3


def _comment_blocks(src: Source):
    """Yield (comment_line_numbers, is_header) for each run of comment-only lines.

    A blank line continues a block without counting toward it: two paragraphs
    split by whitespace are still one wall of text. A line of code ends it.

    A block is the header only if nothing but blank lines comes before it. The
    first block of a file is not the header when it sits under code.
    """
    comment = src.comment_only_lines()
    block: list[int] = []
    code_seen = False
    for i, raw in enumerate(src.lines, 1):
        if i in comment:
            block.append(i)
        elif raw.strip():
            if block:
                yield block, not code_seen
            block = []
            code_seen = True
    if block:
        yield block, not code_seen


def check_comment_budget(src: Source, scope: set[int] | None):
    """Flag a comment block longer than its budget.

    Takes `scope` directly rather than yielding a span, because the budget is
    counted over changed lines only -- a span rule would report the whole
    legacy block the moment the diff touched one line of it.
    """
    for lines, is_header in _comment_blocks(src):
        header = is_header and lines[0] <= HEADER_START
        budget = HEADER_BUDGET if header else COMMENT_BUDGET

        counted = lines if scope is None else [ln for ln in lines if ln in scope]
        if len(counted) <= budget:
            continue
        if any(suppressed(src, ln, "comment-budget") for ln in [lines[0] - 1] + lines):
            continue

        where = "file header" if header else "comment block"
        measured = "line(s)" if scope is None else "new line(s)"
        yield Finding(
            src.rel, counted[0], ERROR, "comment-budget",
            f"{len(counted)} {measured} of comment in one {where} (budget {budget})",
            "Cut it to what the next reader cannot infer from the code. "
            "Reference material belongs in docs/ or the file header; if this "
            "block is that reference, add: -- eui-style: allow comment-budget",
        )


# --------------------------------------------------------------------------
#  Rule: third-party addon provenance
# --------------------------------------------------------------------------
# EllesmereUI must not carry code lifted from another addon. A linter cannot
# see that a block was copied -- it has no copy to compare against. What it can
# do is find every place a third-party addon is named and make someone account
# for it, because a lifted block almost always arrives with the donor's name
# still attached: in a credit comment, a "based on" note, a copied identifier,
# or a link to the source.
#
# The tree already names many addons legitimately -- a conflict registry, compat
# shims for FarmHud and Myslot, unit-frame globals it must not fight with. So a
# mention is a question, not a verdict, and only derivation language turns it
# into an error.

ADDON_DATA = Path(__file__).resolve().parent.parent / "references" / "addons.json"

# Wording that claims the code came from somewhere else. Blizzard's own source
# is fair game, so these only fire when a third-party name is nearby.
#
# Split by how much the phrase can mean. STRONG phrases take a source as their
# object and nothing else -- "adapted from", "credit to". Measured against the
# tree they never appear near a third-party name, so a hit is always real, and
# they are errors.
#
# WEAK phrases are ordinary technical English that happens to describe
# derivation: the tree has "initialAnchor is ALWAYS derived from the anchor
# position" and "ownership based on whether UnitFrames is rendering", neither
# about code provenance. Flagging those as errors would have made this rule
# wrong half the time at its highest severity, so they are warnings.
DERIVATION_STRONG = re.compile(
    r"\b(?:"
    r"adapted from|adapted by|taken from|copied from|copy-?paste[d]? from"
    r"|borrowed from|lifted from|ported from|stolen from|cribbed from"
    r"|straight (?:out )?of|straight from|ripped from|snippet from"
    r"|credits?\s*(?::|to\b)|thanks to|courtesy of|kudos to|hat tip"
    r"|originally (?:from|by)|source\s*:|reimplement(?:ed|ation) of"
    r")", re.I)

DERIVATION_WEAK = re.compile(
    r"\b(?:"
    r"based (?:on|upon)|derived from|inspired by|copy of|clone of"
    r"|same (?:approach|trick|technique|idea|logic|way) as|the way \w+ does"
    r"|modelled on|modeled on|after \w+'s"
    r")\b", re.I)

# How far a credit comment may sit from the addon name it credits. A block
# comment usually names the source a line or two above the code.
DERIVATION_RADIUS = 2


def _load_addon_tokens():
    if not ADDON_DATA.is_file():
        return None, None
    data = json.loads(ADDON_DATA.read_text(encoding="utf-8"))
    tokens = {k: v for k, v in data["tokens"].items() if v["tier"] != "library"}
    if not tokens:
        return None, None
    # Longest first, so "Plater Nameplates" wins over "Plater".
    alternation = "|".join(re.escape(t) for t in
                           sorted(tokens, key=len, reverse=True))
    # Case-sensitive on purpose. Case-insensitive matching on this list is
    # unusable: it turns every `local cell`, `atlas`, and `routes` in the tree
    # into a finding, and matches the French word "masque" in the locale files.
    rx = re.compile(r"(?<![A-Za-z0-9_])(" + alternation + r")(?![A-Za-z0-9_])")
    return rx, tokens


ADDON_RX, ADDON_TOKENS = _load_addon_tokens()


def _describe(entries) -> str:
    addons = sorted({e["addon"] for e in entries})
    return ", ".join(addons[:3]) + (" ..." if len(addons) > 3 else "")


def check_thirdparty(src: Source):
    """Yield (finding, span) -- a credit block is in scope if the diff touched
    any line of it, not only the line carrying the addon name."""
    if ADDON_RX is None:
        return

    # Scan the raw text, not the mask: a credit comment and a copied string
    # literal are both exactly what this rule is looking for.
    names_at: dict[int, set[str]] = {}
    for m in ADDON_RX.finditer(src.text):
        names_at.setdefault(src.line_of(m.start()), set()).add(m.group(1))
    if not names_at:
        return

    credits_at: dict[int, tuple[str, bool]] = {}
    for rx, is_strong in ((DERIVATION_WEAK, False), (DERIVATION_STRONG, True)):
        for m in rx.finditer(src.text):
            line = src.line_of(m.start())
            # A strong phrase on the same line as a weak one wins.
            if is_strong or line not in credits_at:
                credits_at[line] = (m.group(0).strip(), is_strong)

    # A credit line claims every addon named within its radius, and reports
    # once, at the claim. Reporting per name instead produced two errors for
    # one comment and pointed at the code rather than the sentence about it.
    claimed: set[int] = set()
    for cline, (phrase, is_strong) in sorted(credits_at.items()):
        lo, hi = cline - DERIVATION_RADIUS, cline + DERIVATION_RADIUS
        near = sorted(n for n in names_at if lo <= n <= hi)
        if not near:
            continue
        claimed.update(near)
        if any(suppressed(src, n, "thirdparty-credit") or
               suppressed(src, n, "thirdparty") for n in [cline] + near):
            continue
        entries = [ADDON_TOKENS[t] for n in near for t in sorted(names_at[n])]
        authors = sorted({e["author"] for e in entries if e["author"]})
        yield Finding(
            src.rel, cline, ERROR if is_strong else WARNING, "thirdparty-credit",
            f"{_describe(entries)} named next to {phrase!r}",
            "This reads as code taken from another addon. Establish where it "
            "came from before this goes near a PR: open that addon's source, "
            "compare, and either write the logic from scratch or drop it. If "
            "the phrase is not about provenance, add: "
            "-- eui-style: allow thirdparty-credit (reason)"
            + (f" Addon author: {', '.join(authors)}." if authors else ""),
        ), (lo, hi)

    for line, tokens in sorted(names_at.items()):
        if line in claimed:
            continue
        entries = [ADDON_TOKENS[t] for t in sorted(tokens)]
        ambiguous = all(e["tier"] == "ambiguous" for e in entries)
        rule = "thirdparty-maybe" if ambiguous else "thirdparty"
        if suppressed(src, line, rule) or suppressed(src, line, "thirdparty"):
            continue
        yield Finding(
            src.rel, line, NOTE if ambiguous else WARNING, rule,
            f"third-party addon named: {_describe(entries)}"
            + (" (also an ordinary word -- may be a coincidence)"
               if ambiguous else ""),
            "Naming an addon is fine for interop -- conflict entries, "
            "IsAddOnLoaded checks, compat shims. Copying its code is not. "
            "Confirm this line carries no logic from that addon, then add: "
            f"-- eui-style: allow {rule} (reason)",
        ), (line, line)


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


def changed_lines(root: Path, base: str | None, staged: bool = False) -> dict[str, set[int]]:
    """Map relative path -> set of line numbers added or modified.

    Against `base` this compares to the working tree, so uncommitted edits are
    included -- it runs before you push, not after. With `staged` it compares
    the index to HEAD, which is exactly what the next commit will record.
    """
    # Force the a/ b/ prefixes: diff.mnemonicPrefix in the user's git config
    # emits c/ and w/ instead, which would silently match no files and turn
    # this gate into a no-op.
    cmd = ["git", "-C", str(root), "diff", "--unified=0", "--no-color",
           "--src-prefix=a/", "--dst-prefix=b/"]
    cmd += ["--cached"] if staged else [base]
    diff = subprocess.run(cmd + ["--", "*.lua"], capture_output=True, text=True).stdout
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


def staged_text(root: Path, rel: str) -> str | None:
    """The content git would commit for `rel`, or None if it is being deleted."""
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f":{rel}"],
        capture_output=True, text=True, errors="replace")
    return proc.stdout if proc.returncode == 0 else None


HOOK_MARKER = "ellesmereui-pr-check"

HOOK_TEMPLATE = """#!/bin/sh
# {marker}: block a commit that records a style violation.
# Skip once with `git commit --no-verify`.
exec {python} {script} --root "$(git rev-parse --show-toplevel)" --staged
"""


def install_hook(root: Path) -> int:
    hooks = Path(git(root, "rev-parse", "--git-path", "hooks") or ".git/hooks")
    if not hooks.is_absolute():
        hooks = root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"

    if hook.exists() and HOOK_MARKER not in hook.read_text(
            encoding="utf-8", errors="replace"):
        print(f"{hook} already exists and is not ours -- leaving it alone.\n"
              "Add this line to it instead:\n\n"
              f'  {sys.executable} {Path(__file__).resolve()} '
              '--root "$(git rev-parse --show-toplevel)" --staged\n')
        return 1

    hook.write_text(HOOK_TEMPLATE.format(
        marker=HOOK_MARKER,
        python=sys.executable,
        script=Path(__file__).resolve(),
    ), encoding="utf-8")
    hook.chmod(0o755)
    print(f"Installed {hook}\n"
          "It checks staged lines on every commit. Errors block the commit; "
          "warnings and notes print and let it through.\n"
          "Bypass once with: git commit --no-verify")
    return 0


# --------------------------------------------------------------------------
#  Driver
# --------------------------------------------------------------------------

SIMPLE_RULES = (check_lua51, check_ascii, check_popup)
SPAN_RULES = (check_tooltip, check_dualrow, check_thirdparty)


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

    # Counts changed lines itself rather than being filtered by them.
    out.extend(check_comment_budget(src, scope))

    return sorted(out, key=lambda f: (f.line, f.rule))


def valid_root(p: Path) -> bool:
    return (p / "EllesmereUI.toc").is_file()


def discover_roots() -> list[Path]:
    """Addon checkouts found without being told where to look.

    Deliberately duplicated from ellesmereui-search's build_index.py: the two
    skills install independently, so neither may import the other. They must
    still agree about where the addon lives, or the same cwd resolves for one
    and not the other.

    WoW installs sit at unpredictable depths, especially under Proton/Wine
    prefixes (~/Faugus/<app>/drive_c/Program Files (x86)/World of Warcraft/...).
    Walk a bounded set of wildcard depths rather than a recursive glob, which
    would be unusably slow from $HOME.
    """
    out: list[Path] = []
    home = Path.home()
    tail = "World of Warcraft/_retail_/Interface/AddOns/EllesmereUI*"
    for base in (home, home / "Games", home / ".local/share", Path("/mnt"), Path("/media")):
        if not base.is_dir():
            continue
        for depth in range(6):
            try:
                out.extend(sorted(base.glob("*/" * depth + tail)))
            except OSError:
                continue
    for base in (home / "Repos", home / "repos", home / "src", home / "code", home):
        if base.is_dir():
            out.extend(sorted(base.glob("EllesmereUI*")))
    return out


def resolve_root(explicit: str | None) -> tuple[Path, bool]:
    """Return (root, discovered). `discovered` is True when nothing pointed here."""
    if explicit:
        root = Path(explicit).expanduser()
    else:
        env = os.environ.get("ELLESMEREUI_ROOT")
        root = Path(env).expanduser() if env else Path.cwd()

    if valid_root(root):
        return root.resolve(), False

    top = git(root, "rev-parse", "--show-toplevel")
    if top and valid_root(Path(top)):
        return Path(top).resolve(), False

    # Only guess when the caller gave no hint at all. An explicit --root or
    # $ELLESMEREUI_ROOT that misses is a mistake worth reporting, not a reason
    # to lint some other checkout.
    if not explicit and not os.environ.get("ELLESMEREUI_ROOT"):
        for c in discover_roots():
            try:
                if valid_root(c):
                    return c.resolve(), True
            except OSError:
                continue

    sys.exit(
        f"{root} is not an EllesmereUI checkout (no EllesmereUI.toc).\n"
        "Run from inside the addon, pass --root, or set $ELLESMEREUI_ROOT."
    )


SEVERITY_ORDER = {ERROR: 0, WARNING: 1, NOTE: 2}
LABEL = {ERROR: "error", WARNING: "warn ", NOTE: "note "}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="path to the EllesmereUI checkout")
    ap.add_argument("--base", help="git ref to diff against (default: origin/main, then main)")
    ap.add_argument("--staged", action="store_true",
                    help="check staged lines only -- what the next commit records")
    ap.add_argument("--all", action="store_true", help="check every file, not just the diff")
    ap.add_argument("--files", nargs="+", help="check these files in full")
    ap.add_argument("--strict", action="store_true", help="warnings fail too")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--install-hook", action="store_true",
                    help="install --staged as this repo's git pre-commit hook")
    args = ap.parse_args()

    root, discovered = resolve_root(args.root)
    if discovered and not args.json:
        # Nothing pointed us here, so name the checkout being linted rather
        # than letting the findings imply the wrong tree.
        print(f"Using discovered checkout: {root}")

    if args.install_hook:
        return install_hook(root)

    targets: list[tuple[Path, str, set[int] | None, str | None]] = []
    if args.files:
        for f in args.files:
            p = Path(f).expanduser().resolve()
            rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
            targets.append((p, rel, None, None))
        scope_desc = f"{len(targets)} file(s), in full"
    elif args.all:
        for p in sorted(root.rglob("*.lua")):
            rel = str(p.relative_to(root))
            if not excluded(rel):
                targets.append((p, rel, None, None))
        scope_desc = f"whole tree ({len(targets)} files)"
    elif args.staged:
        changed = changed_lines(root, None, staged=True)
        for rel, lines in sorted(changed.items()):
            if excluded(rel):
                continue
            # Read the indexed blob, not the file on disk: they differ whenever
            # the file was edited after `git add`, and the commit records the
            # blob.
            text = staged_text(root, rel)
            if text is not None:
                targets.append((root / rel, rel, lines, text))
        scope_desc = (f"staged lines: {len(targets)} file(s), "
                      f"{sum(len(v) for v in changed.values())} line(s)")
    else:
        base = resolve_base(root, args.base)
        if base is None:
            sys.exit("Could not resolve a base ref. Pass --base, or use --all.")
        changed = changed_lines(root, base)
        for rel, lines in sorted(changed.items()):
            p = root / rel
            if p.is_file() and not excluded(rel):
                targets.append((p, rel, lines, None))
        short = git(root, "rev-parse", "--short", base) or base
        scope_desc = (f"changed lines vs {args.base or 'main'} ({short}): "
                      f"{len(targets)} file(s), "
                      f"{sum(len(v) for v in changed.values())} line(s)")

    findings: list[Finding] = []
    for path, rel, scope, text in targets:
        findings.extend(check_file(Source(path, rel, text), scope))

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
