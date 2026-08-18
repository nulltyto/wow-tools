#!/usr/bin/env python3
"""Comment budget for this repo's own Lua.

    lua_comments.py                 comment lines your branch adds
    lua_comments.py --base <ref>    pick the base ref explicitly
    lua_comments.py --all           every file (expect legacy findings)
    lua_comments.py --files a.lua   just these files, whole file

Diff-scoped by default, so a block that was already long reports nothing until
someone extends it. Exit status is 1 when anything is over budget.

The rule itself lives in the ellesmereui-pr-check skill, which is where the
addon repo's PR gate reads it from. This runs the same code against the Lua
that ships from here -- the diagnostics addon and the test harnesses -- so the
budget does not depend on which repo a file happens to sit in.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from wow_tools import scripts  # noqa: E402


def load_rule():
    """The rule, loaded from the skill that owns it.

    The skills are stdlib-only standalone scripts with no package to import
    from, and copying the rule here would let the two definitions drift. This
    file is not installed as a hook and only ever runs from a full checkout,
    so unlike the scripts it lints, it may import the package. See ADR-0001.
    """
    return scripts.load("eui_check_style")


def resolve_base(cs, explicit: str | None) -> str | None:
    """Base ref for the diff, or None when there is nothing to compare against.

    On a pull request the merge base with main is the right answer. On a push
    straight to main it resolves to HEAD, which would check nothing, so that
    case falls back to the previous commit.
    """
    if explicit:
        return explicit
    # check_style.resolve_base returns (commit, the ref it came from); only the
    # commit is a thing git can diff against.
    resolved = cs.resolve_base(REPO, None)
    base = resolved[0] if resolved else None
    head = cs.git(REPO, "rev-parse", "HEAD")
    if base and cs.git(REPO, "rev-parse", base) != head:
        return base
    return cs.git(REPO, "rev-parse", "--verify", "--quiet", "HEAD~1") or None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="git ref to diff against")
    ap.add_argument("--all", action="store_true", help="check every file, not just the diff")
    ap.add_argument("--files", nargs="+", help="check these files in full")
    args = ap.parse_args()

    cs = load_rule()

    targets: list[tuple[Path, str, set[int] | None]] = []
    if args.files:
        for f in args.files:
            p = Path(f).expanduser().resolve()
            rel = str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)
            targets.append((p, rel, None))
        scope_desc = f"{len(targets)} file(s), in full"
    elif args.all:
        for p in sorted(REPO.rglob("*.lua")):
            rel = str(p.relative_to(REPO))
            if not cs.excluded(rel):
                targets.append((p, rel, None))
        scope_desc = f"whole tree ({len(targets)} files)"
    else:
        base = resolve_base(cs, args.base)
        if base is None:
            print("No base ref to diff against; nothing to check.")
            return 0
        changed = cs.changed_lines(REPO, base)
        for rel, lines in sorted(changed.items()):
            p = REPO / rel
            if p.is_file() and not cs.excluded(rel):
                targets.append((p, rel, lines))
        short = cs.git(REPO, "rev-parse", "--short", base) or base
        scope_desc = (f"changed lines vs {short}: {len(targets)} file(s), "
                      f"{sum(len(v) for v in changed.values())} line(s)")

    findings = []
    for path, rel, scope in targets:
        findings.extend(cs.check_comment_budget(cs.Source(path, rel), scope))

    print(f"Lua comment budget -- {scope_desc}\n")
    if not findings:
        print("Clean.")
        return 0

    for f in sorted(findings, key=lambda x: (x.rel, x.line)):
        print(f"  {f.rel}:{f.line}  {f.message}")
    print(f"\n{len(findings)} block(s) over budget.")
    print(
        "\nTrimming these is summarisation, not design work: hand each file to "
        "a subagent on a small model rather than doing it here. In Claude Code "
        "that is the Agent tool with subagent_type mech-executor and "
        'model "sonnet" (or "haiku" for the plainest cases).'
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.SubprocessError as exc:
        sys.exit(f"git failed: {exc}")
