#!/usr/bin/env python3
"""ASCII-only gate for the text git and GitHub carry.

    ascii_text.py --commit-msg .git/COMMIT_EDITMSG   one message, from a file
    ascii_text.py --range main..HEAD                 every message in a range
    ascii_text.py --stdin --label "PR body"          text on standard input
    ascii_text.py --command 'gh pr create -b "..."'  a shell command line
    ascii_text.py --hook-json                        a PreToolUse event, on stdin
    ascii_text.py --install-hook [repo]              install the commit-msg hook

Source files have their own ASCII rule, in the ellesmereui-pr-check skill. This
one covers the text that never becomes a file: commit messages, pull request
titles and bodies, and review comments. That text is worth a separate gate
because nothing downstream ever lints it -- it goes straight from a model or a
keyboard into a place where it is quoted, re-encoded, and read back for years.

A curly quote in a commit message survives until something reads the log with a
different encoding assumption and prints a mojibake blob, and by then the commit
is immutable. Rewriting published history to fix punctuation is not worth it, so
the character never gets written in the first place.

Exit status is 1 when anything is non-ASCII, 2 when the arguments are wrong.
The PreToolUse mode exits 2 to block, which is that hook API's convention
rather than this script's.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# ASCII stand-ins for the characters a model reaches for most. check_style.py
# carries the same table for Lua source; tests/test_ascii_text.py asserts the
# two agree wherever they overlap, so the duplication cannot drift silently.
#
# They are duplicated rather than imported on purpose: this script runs as a
# git hook inside a different repository, and a hook that fails because another
# checkout moved would block every commit in that repository.
REPLACEMENTS = {
    "—": "--", "–": "-", "─": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "→": "->", "←": "<-", "•": "*",
    "…": "...", " ": " ", "×": "x", "°": " degrees",
    "≥": ">=", "≤": "<=", "·": "*", "✓": "y", "✗": "x",
}


@dataclass(frozen=True)
class Offence:
    line: int
    col: int
    char: str

    @property
    def codepoint(self) -> str:
        return f"U+{ord(self.char):04X}"

    @property
    def name(self) -> str:
        try:
            return unicodedata.name(self.char)
        except ValueError:
            return "unnamed"

    def describe(self) -> str:
        swap = REPLACEMENTS.get(self.char)
        fix = f"write {swap!r} instead" if swap else "remove it or spell it in ASCII"
        return (f"line {self.line}, col {self.col}: {self.char!r} "
                f"({self.codepoint} {self.name}) -- {fix}")


def scan(text: str) -> list[Offence]:
    """Every non-ASCII character in `text`, in reading order."""
    out = []
    for row, line in enumerate(text.splitlines(), start=1):
        for col, ch in enumerate(line, start=1):
            if ord(ch) > 127:
                out.append(Offence(row, col, ch))
    return out


def fix(text: str) -> str:
    """`text` with every character the table knows swapped for its ASCII form.

    Anything not in the table is left in place, so the result still has to be
    checked. This exists to make the common failure a one-keystroke fix, not to
    guarantee a clean string.
    """
    return "".join(REPLACEMENTS.get(ch, ch) for ch in text)


# --------------------------------------------------------------------------
#  Commit messages
# --------------------------------------------------------------------------

def commit_body(text: str) -> str:
    """A commit message with the parts git itself discards removed.

    Comment lines never reach the stored message, and neither does anything
    below the scissors line that `commit --verbose` adds. Flagging a character
    in the diff git pasted in below the scissors would be a false positive on
    the author's own source file.
    """
    lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            if "------------------------ >8" in line:
                break
            continue
        lines.append(line)
    return "\n".join(lines)


# git stores messages as UTF-8 regardless of the machine's locale, so every
# read here says so. Left to `text=True` alone, Python decodes with the
# preferred encoding -- cp1252 on a default Windows install -- and an em dash
# arrives as the two characters its UTF-8 bytes happen to mean there. The
# check still fires, but it names the wrong character and offers a repair that
# would not help.
ENCODING = "utf-8"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding=ENCODING, errors="replace",
    ).stdout.strip()


def messages_in_range(root: Path, rev_range: str) -> list[tuple[str, str]]:
    """(label, message) for every commit in `rev_range`.

    Uses a record separator that cannot appear in a message, because the one
    thing a commit message reliably contains is blank lines.
    """
    sep = "\x1e"
    raw = subprocess.run(
        ["git", "-C", str(root), "log", "--no-merges", f"--format=%H%x1f%B{sep}", rev_range],
        capture_output=True, text=True, encoding=ENCODING, errors="replace",
    )
    if raw.returncode != 0:
        sys.exit(f"git log failed for {rev_range!r}: {raw.stderr.strip()}")
    out = []
    for record in raw.stdout.split(sep):
        if "\x1f" not in record:
            continue
        sha, body = record.split("\x1f", 1)
        out.append((f"commit {sha.strip()[:12]}", body.strip("\n")))
    return out


# --------------------------------------------------------------------------
#  Shell commands that carry a message
# --------------------------------------------------------------------------
# The PreToolUse hook sees a command line, not a message. These patterns pull
# the message back out, so that a non-ASCII character in a path or a branch
# name does not read as a non-ASCII character in a commit subject.

CARRIES_TEXT = re.compile(
    r"(?<!\w)git\s+(?:-\S+\s+|--\S+\s+)*(?:commit|tag)(?!\w)"
    r"|(?<!\w)gh\s+(?:pr|issue|release|repo)(?!\w)"
)

# Flags whose value is prose a human will read. `-F`/`--body-file` name a file
# rather than carrying text, so they are deliberately absent: the file is on
# disk and gets linted as a file.
TEXT_FLAGS = {
    "-m", "--message", "-t", "--title", "-b", "--body",
    "-n", "--notes", "--subject", "-d", "--description",
}

HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def heredoc_bodies(command: str) -> list[str]:
    """The body of every heredoc in `command`.

    `gh pr create --body "$(cat <<'EOF' ... EOF)"` is the shape an agent reaches
    for whenever the body has more than one line, so a checker that only reads
    flag values would miss most real pull request bodies.
    """
    out = []
    lines = command.splitlines()
    i = 0
    while i < len(lines):
        m = HEREDOC.search(lines[i])
        if not m:
            i += 1
            continue
        delimiter = m.group(2)
        body = []
        i += 1
        while i < len(lines) and lines[i].strip() != delimiter:
            body.append(lines[i])
            i += 1
        out.append("\n".join(body))
        i += 1
    return out


def message_parts(command: str) -> list[tuple[str, str]]:
    """(label, text) for each piece of prose a git or gh command carries.

    Returns nothing for a command that carries none, which is how the hook
    stays out of the way of every other command in a session.
    """
    if not CARRIES_TEXT.search(command):
        return []

    parts = [(f"heredoc {n}", body) for n, body in enumerate(heredoc_bodies(command), 1)]

    # The heredoc bodies are already accounted for, and their contents would
    # confuse the tokeniser, so tokenise only the command's first line.
    head = command.splitlines()[0] if command else ""
    try:
        tokens = shlex.split(head, comments=False, posix=True)
    except ValueError:
        # Unbalanced quotes, usually because a heredoc opened on this line.
        # Falling back to the raw line over-reports rather than under-reports,
        # which is the right direction for a gate.
        return parts + [("command", head)]

    for n, token in enumerate(tokens):
        if token in TEXT_FLAGS and n + 1 < len(tokens):
            parts.append((token, tokens[n + 1]))
        elif token.startswith("--") and "=" in token and token.split("=", 1)[0] in TEXT_FLAGS:
            flag, value = token.split("=", 1)
            parts.append((flag, value))
    return parts


# --------------------------------------------------------------------------
#  Reporting
# --------------------------------------------------------------------------

def report(parts: list[tuple[str, str]], *, stream=sys.stderr) -> int:
    """Print every offence in `parts`. Returns the number found."""
    total = 0
    for label, text in parts:
        offences = scan(text)
        if not offences:
            continue
        if not total:
            print("Non-ASCII characters in text that git or GitHub will store:",
                  file=stream)
        total += len(offences)
        print(f"\n  {label}:", file=stream)
        for o in offences:
            print(f"    {o.describe()}", file=stream)
        repaired = fix(text)
        if repaired != text and not scan(repaired):
            print("\n    Rewritten in ASCII:", file=stream)
            for line in repaired.splitlines() or [""]:
                print(f"      {line}", file=stream)
    if total:
        print(
            "\nCommit messages and pull request text are read back for years, "
            "through tooling that does not agree on encoding, and cannot be "
            "corrected once published. Keep them to ASCII.",
            file=stream,
        )
    return total


# --------------------------------------------------------------------------
#  The git hook
# --------------------------------------------------------------------------

HOOK_MARKER = "wow-tools-ascii-text"

# Paths are quoted and written with forward slashes. A hook is a /bin/sh
# script even on Windows, where git runs it through its bundled shell, and a
# native path arrives with backslashes that sh reads as escapes -- turning
# D:\a\x\.venv\Scripts\python.exe into D:axvenvScriptspython.exe. Python
# accepts forward slashes on every platform.
HOOK_TEMPLATE = """#!/bin/sh
# {marker}: reject a commit message containing non-ASCII characters.
# Skip once with `git commit --no-verify`.
exec "{python}" "{script}" --commit-msg "$1"
"""


def _sh_path(path) -> str:
    return str(path).replace("\\", "/")


def install_hook(root: Path) -> int:
    hooks = Path(git(root, "rev-parse", "--git-path", "hooks") or ".git/hooks")
    if not hooks.is_absolute():
        hooks = root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "commit-msg"

    python = _sh_path(sys.executable)
    script = _sh_path(Path(__file__).resolve())
    line = f'"{python}" "{script}" --commit-msg "$1"'
    if hook.exists() and HOOK_MARKER not in hook.read_text(encoding="utf-8", errors="replace"):
        print(f"{hook} already exists and is not ours -- leaving it alone.\n"
              f"Add this line to it instead:\n\n  {line}\n")
        return 1

    hook.write_text(HOOK_TEMPLATE.format(
        marker=HOOK_MARKER, python=python, script=script,
    ), encoding="utf-8")
    hook.chmod(0o755)
    print(f"Installed {hook}\n"
          "It rejects a commit whose message has a non-ASCII character.\n"
          "Bypass once with: git commit --no-verify")
    return 0


# --------------------------------------------------------------------------
#  Driver
# --------------------------------------------------------------------------

def _use_utf8() -> None:
    """Read and write UTF-8 whatever the machine's locale says.

    Both directions matter. Input, because the text being judged is UTF-8 --
    a PR body piped in by CI, a PreToolUse event. Output, because the report
    quotes the offending character back, and a console encoding that cannot
    represent an em dash would turn this check into a crash on exactly the
    input it exists to catch.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=ENCODING, errors="replace")
        except (AttributeError, ValueError):
            pass  # already detached, or not a text stream


def main(argv: list[str] | None = None) -> int:
    _use_utf8()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--commit-msg", metavar="FILE",
                      help="a commit message file, as git passes to commit-msg")
    mode.add_argument("--range", metavar="REV_RANGE",
                      help="check every commit message in this range")
    mode.add_argument("--stdin", action="store_true", help="check text on standard input")
    mode.add_argument("--text", metavar="TEXT", help="check this string")
    mode.add_argument("--command", metavar="CMD", nargs="?", const="-",
                      help="check the message a git or gh command carries ('-' reads stdin)")
    mode.add_argument("--hook-json", action="store_true",
                      help="check a Claude Code PreToolUse event on standard input")
    mode.add_argument("--install-hook", metavar="REPO", nargs="?", const=".",
                      help="install the commit-msg hook into REPO (default: here)")
    ap.add_argument("--label", default="text", help="what to call the text from --stdin")
    ap.add_argument("--root", default=".", help="repository root for --range")
    args = ap.parse_args(argv)

    if args.install_hook is not None:
        return install_hook(Path(args.install_hook).expanduser().resolve())

    if args.commit_msg:
        path = Path(args.commit_msg)
        if not path.is_file():
            print(f"error: no such file: {path}", file=sys.stderr)
            return 2
        parts = [("commit message",
                  commit_body(path.read_text(encoding="utf-8", errors="replace")))]
    elif args.range:
        parts = messages_in_range(Path(args.root).resolve(), args.range)
    elif args.stdin:
        parts = [(args.label, sys.stdin.read())]
    elif args.text is not None:
        parts = [(args.label, args.text)]
    elif args.command is not None:
        command = sys.stdin.read() if args.command == "-" else args.command
        parts = message_parts(command)
    else:
        parts = _hook_parts()

    found = report(parts)
    if not found:
        return 0
    # PreToolUse reads 2 as "block this call and show the model why". Every
    # other mode is an ordinary lint, where 1 is failure and 2 means the
    # arguments were wrong.
    return 2 if args.hook_json else 1


def _hook_parts() -> list[tuple[str, str]]:
    """Message text from a PreToolUse event, or nothing if this is not one."""
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if event.get("tool_name") != "Bash":
        return []
    command = (event.get("tool_input") or {}).get("command") or ""
    return message_parts(command)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except subprocess.SubprocessError as exc:
        sys.exit(f"git failed: {exc}")
    except BrokenPipeError:
        # Reached only while printing findings -- a clean run prints nothing to
        # a pipe -- so the answer was "not clean" even though the report was
        # cut short. Exiting 0 here would turn `ascii_text.py ... | head` into
        # a check that always passes.
        os._exit(1)
