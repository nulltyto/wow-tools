"""wow-tools installer: put this repo's skills where your agent harness reads them.

    python -m wow_tools install                 pick harnesses and skills interactively
    python -m wow_tools install --harness claude-code --skills all --yes
    python -m wow_tools list                    what is available and what is detected
    python -m wow_tools status                  what is installed right now
    python -m wow_tools uninstall --harness codex

Runs on Windows, macOS, and Linux with the standard library only, so it works
from a bare clone before anything is set up.
"""

from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from pathlib import Path

from . import install as engine
from . import registry
from . import skills as skills_mod
from .install import Method

# --------------------------------------------------------------------------
#  Selection
# --------------------------------------------------------------------------

def detected(harness: registry.Harness) -> bool:
    return any((Path.home() / p).exists() for p in harness.detect)


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _interactive_available() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def choose_harnesses() -> list[registry.Harness]:
    entries = list(registry.HARNESSES)
    print("\nWhich harness(es) do you use?\n")
    for i, h in enumerate(entries, 1):
        mark = "*" if detected(h) else " "
        tail = "" if h.installable else "  (no directory to install into)"
        print(f" {mark} {i:>2}. {h.name}{tail}")
    print("\n  * = a config directory for this harness exists in your home directory")
    print("  Enter numbers separated by spaces or commas, 'detected', or 'all'.")

    while True:
        raw = _ask("\nHarnesses: ")
        if not raw:
            print("  Nothing selected.")
            continue
        low = raw.lower()
        if low == "all":
            picked = [h for h in entries if h.installable]
        elif low == "detected":
            picked = [h for h in entries if h.installable and detected(h)]
            if not picked:
                print("  None detected. Choose by number instead.")
                continue
        else:
            try:
                idx = [int(tok) for tok in raw.replace(",", " ").split()]
            except ValueError:
                print("  Enter numbers, 'detected', or 'all'.")
                continue
            if any(n < 1 or n > len(entries) for n in idx):
                print(f"  Numbers must be between 1 and {len(entries)}.")
                continue
            picked = [entries[n - 1] for n in idx]
        if picked:
            return picked
        print("  Nothing selected.")


def choose_skills(available: list[skills_mod.Skill]) -> list[skills_mod.Skill]:
    print("\nWhich skills?\n")
    for i, s in enumerate(available, 1):
        print(f"   {i:>2}. {s.name}\n       {s.summary()}")
    print("\n  Enter numbers separated by spaces or commas, or 'all'.")

    while True:
        raw = _ask("\nSkills [all]: ") or "all"
        if raw.lower() == "all":
            return list(available)
        try:
            idx = [int(tok) for tok in raw.replace(",", " ").split()]
        except ValueError:
            print("  Enter numbers or 'all'.")
            continue
        if any(n < 1 or n > len(available) for n in idx):
            print(f"  Numbers must be between 1 and {len(available)}.")
            continue
        picked = [available[n - 1] for n in idx]
        if picked:
            return picked


# --------------------------------------------------------------------------
#  Planning
# --------------------------------------------------------------------------

def plan(harnesses, scope: str, project_root: Path | None):
    """Group harnesses by the directory they resolve to.

    Most harnesses read the cross-agent path, so selecting eight of them
    usually means one directory. Reporting eight identical installs would
    misrepresent what happened, so they are collapsed and credited together.
    """
    groups: OrderedDict[Path, list[registry.Harness]] = OrderedDict()
    skipped: list[registry.Harness] = []
    for h in harnesses:
        directory = engine.resolve_directory(h, scope, project_root)
        if directory is None:
            skipped.append(h)
            continue
        groups.setdefault(directory, []).append(h)
    return groups, skipped


# --------------------------------------------------------------------------
#  Commands
# --------------------------------------------------------------------------

def cmd_list(args) -> int:
    found, problems = skills_mod.discover()
    print("Skills in this repository:\n")
    for s in found:
        print(f"  {s.name}\n      {s.summary()}")
    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  ! {p}")

    print("\nHarnesses:\n")
    width = max(len(h.name) for h in registry.HARNESSES)
    for h in registry.HARNESSES:
        user = f"~/{h.skills_user[0]}" if h.skills_user else "-"
        proj = h.skills_project[0] if h.skills_project else "-"
        mark = "*" if detected(h) else " "
        print(f" {mark} {h.key:<16} {h.name:<{width}}  user: {user:<26} project: {proj}")
        if args.verbose and h.note:
            print(f"   {' ' * width}  {h.note}")
    print("\n  * = detected in your home directory")
    if not args.verbose:
        print("  Pass --verbose for the per-harness notes and caveats.")
    return 0 if not problems else 1


def cmd_status(args) -> int:
    found, _ = skills_mod.discover()
    any_installed = False
    for h in registry.HARNESSES:
        for scope in ("user", "project"):
            directory = engine.resolve_directory(h, scope, args.project_root)
            if directory is None or not directory.is_dir():
                continue
            present = []
            for s in found:
                t = directory / s.name
                if t.is_symlink() or t.exists():
                    kind = "link" if t.is_symlink() else "copy"
                    present.append(f"{s.name} ({kind})")
            if present:
                any_installed = True
                print(f"{h.name} [{scope}] {directory}")
                for p in present:
                    print(f"    {p}")
    if not any_installed:
        print("No skills from this repository are installed anywhere yet.")
        print("Run: python -m wow_tools install")
    return 0


def _run(args, uninstalling: bool) -> int:
    found, problems = skills_mod.discover()
    if problems:
        print("Skill validation problems:", file=sys.stderr)
        for p in problems:
            print(f"  ! {p}", file=sys.stderr)
    if not found:
        print("No valid skills found to install.", file=sys.stderr)
        return 1

    # Harnesses
    if args.harness:
        keys = [k for spec in args.harness for k in spec.replace(",", " ").split()]
        try:
            harnesses = [registry.get(k) for k in keys]
        except KeyError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    elif _interactive_available():
        harnesses = choose_harnesses()
    else:
        print(
            "error: no --harness given and this is not an interactive terminal.\n"
            "Pass --harness (see `python -m wow_tools list`).",
            file=sys.stderr,
        )
        return 2

    # Skills
    if args.skills:
        specs = [s for spec in args.skills for s in spec.replace(",", " ").split()]
        try:
            chosen = skills_mod.resolve_names(specs, found)
        except KeyError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    elif _interactive_available():
        chosen = choose_skills(found)
    else:
        chosen = list(found)

    groups, skipped = plan(harnesses, args.scope, args.project_root)

    for h in skipped:
        print(f"\n{h.name}: nothing to install for {args.scope} scope.")
        if h.note:
            print(f"  {h.note}")

    if not groups:
        return 0

    print()
    verb = "Uninstalling" if uninstalling else "Installing"
    print(f"{verb} {len(chosen)} skill(s) into {len(groups)} director{'y' if len(groups) == 1 else 'ies'} "
          f"({args.scope} scope):")
    for directory, hs in groups.items():
        print(f"\n{directory}")
        print(f"  for: {', '.join(h.name for h in hs)}")

    if not uninstalling and not args.yes and not args.dry_run and _interactive_available():
        if (_ask("\nProceed? [Y/n]: ") or "y").lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    # Method, decided once per directory: whether symlinks work can differ
    # between a home directory and a project on another filesystem.
    failed = False
    print()
    for directory in groups:
        if uninstalling:
            method_note = ""
        elif args.copy:
            method, method_note = Method.COPY, " (--copy)"
        elif engine.symlinks_available(directory):
            method, method_note = Method.SYMLINK, ""
        else:
            method, method_note = Method.COPY, (
                " (symlinks unavailable here -- copying instead; re-run after a `git pull`)"
            )

        print(f"{directory}{method_note}")
        for s in chosen:
            if uninstalling:
                r = engine.uninstall_skill(s, directory, dry_run=args.dry_run)
            else:
                r = engine.install_skill(
                    s, directory, method, force=args.force, dry_run=args.dry_run
                )
            print(r)
            failed = failed or not r.outcome.ok
        print()

    if failed:
        print("Some entries were left alone. Re-run with --force to replace them.")
        return 1

    if not uninstalling and not args.dry_run:
        notes = [h for hs in groups.values() for h in hs if h.note]
        if notes:
            print("Notes:")
            for h in notes:
                print(f"  {h.name}: {h.note}")
        print("\nRestart your harness (or reload its skills) to pick these up.")
    return 0


def cmd_install(args) -> int:
    return _run(args, uninstalling=False)


def cmd_uninstall(args) -> int:
    return _run(args, uninstalling=True)


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="wow-tools",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="command")

    def add_common(p):
        p.add_argument("--harness", action="append", metavar="KEY",
                       help="harness key(s), comma- or space-separated; repeatable")
        p.add_argument("--skills", action="append", metavar="NAME",
                       help="skill name(s), or 'all'; repeatable")
        p.add_argument("--scope", choices=("user", "project"), default="user",
                       help="install for this user (default) or into a project directory")
        p.add_argument("--project-root", type=Path, default=None,
                       help="project directory for --scope project (default: cwd)")
        p.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
        p.add_argument("--yes", "-y", action="store_true", help="do not prompt for confirmation")
        p.add_argument("--force", action="store_true",
                       help="replace files that are already at the target path")

    p_install = sub.add_parser("install", help="install skills for one or more harnesses")
    add_common(p_install)
    p_install.add_argument("--copy", action="store_true",
                           help="copy instead of symlinking, even where symlinks work")
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="remove skills this installer placed")
    add_common(p_uninstall)
    p_uninstall.set_defaults(copy=False, func=cmd_uninstall)

    p_list = sub.add_parser("list", help="list skills and harnesses")
    p_list.add_argument("--verbose", "-v", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="show what is installed where")
    p_status.add_argument("--project-root", type=Path, default=None)
    p_status.set_defaults(func=cmd_status)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "command", None):
        # Bare invocation is the common case from a bootstrap script, and the
        # thing the user wants then is the interactive installer.
        args = ap.parse_args(["install", *(argv or [])])
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
