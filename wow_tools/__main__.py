"""wow-tools installer: put this repo's skills, rules, addons and hooks where they get read.

    python -m wow_tools install                 pick interactively
    python -m wow_tools install --harness claude-code --skills all --yes
    python -m wow_tools install --harness claude-code --rules all --yes
    python -m wow_tools install --addons all --yes
    python -m wow_tools install --hooks all --repo ../EllesmereUI
    python -m wow_tools list                    what is available and what is detected
    python -m wow_tools status                  what is installed right now
    python -m wow_tools uninstall --harness codex

Four kinds of thing get installed, to four different places.

Skills go to an agent harness's skills directory, a known path per harness.
Rules go to a harness's *rules* directory, which is a different path, exists
for far fewer harnesses, and wants a different file extension from each one --
see the `rules_*` fields in `registry.py`. Addons go to the WoW client's AddOns
folder, which has to be found (`wow.py`). Hooks go into another repository's
`.git/hooks`, which is why they are the one thing never installed by default.

Runs on Windows, macOS, and Linux with the standard library only, so it works
from a bare clone before anything is set up.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import OrderedDict
from pathlib import Path

from . import addons as addons_mod
from . import catalogue, registry, wow
from . import hooks as hooks_mod
from . import install as engine
from . import rules as rules_mod
from . import skills as skills_mod
from .catalogue import ADDONS, HOOKS, RULES, SKILLS, plan, plan_rules
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


def choose_addons(available: list[addons_mod.Addon]) -> list[addons_mod.Addon]:
    print("\nWhich addons? These are installed into your WoW AddOns folder.\n")
    for i, a in enumerate(available, 1):
        print(f"   {i:>2}. {a.name}\n       {a.summary()}")
    print("\n  Enter numbers separated by spaces or commas, 'all', or 'none'.")

    while True:
        raw = _ask("\nAddons [none]: ") or "none"
        low = raw.lower()
        if low == "none":
            return []
        if low == "all":
            return list(available)
        try:
            idx = [int(tok) for tok in raw.replace(",", " ").split()]
        except ValueError:
            print("  Enter numbers, 'all', or 'none'.")
            continue
        if any(n < 1 or n > len(available) for n in idx):
            print(f"  Numbers must be between 1 and {len(available)}.")
            continue
        return [available[n - 1] for n in idx]


def choose_rules(available: list[rules_mod.Rule]) -> list[rules_mod.Rule]:
    """Rules default to none, the way addons do.

    A rule is loaded into every session a harness runs, in every repository on
    the machine. That is a larger claim on somebody's setup than a skill, which
    costs nothing until the model decides it is relevant, so it is asked for
    rather than assumed.
    """
    print("\nWhich rules? These load in every session, for every project.\n")
    for i, r in enumerate(available, 1):
        print(f"   {i:>2}. {r.name}\n       {r.summary()}")
    print("\n  Enter numbers separated by spaces or commas, 'all', or 'none'.")

    while True:
        raw = _ask("\nRules [none]: ") or "none"
        low = raw.lower()
        if low == "none":
            return []
        if low == "all":
            return list(available)
        try:
            idx = [int(tok) for tok in raw.replace(",", " ").split()]
        except ValueError:
            print("  Enter numbers, 'all', or 'none'.")
            continue
        if any(n < 1 or n > len(available) for n in idx):
            print(f"  Numbers must be between 1 and {len(available)}.")
            continue
        return [available[n - 1] for n in idx]


def choose_addons_dir(explicit) -> Path | None:
    """Settle on one AddOns folder, or explain why we cannot.

    A guessed game directory is never installed into without being shown. The
    failure that matters is the quiet one: putting the addon in the PTR install
    while the person plays retail, then wondering why /euidiag does nothing.
    """
    directory, candidates = wow.resolve_addons_dir(explicit)
    if directory is not None:
        return directory

    if not candidates:
        print(
            "\nNo World of Warcraft install found.\n"
            "  Pass --wow-addons /path/to/World of Warcraft/_retail_/Interface/AddOns\n"
            "  or set $WOW_ADDONS_DIR.",
            file=sys.stderr,
        )
        return None

    if len(candidates) == 1:
        only = candidates[0]
        print(f"\nWoW install: {only.label}")
        return only.addons

    if not _interactive_available():
        print("\nMore than one World of Warcraft install found:", file=sys.stderr)
        for c in candidates:
            print(f"    {c.addons}", file=sys.stderr)
        print("Pick one with --wow-addons.", file=sys.stderr)
        return None

    print("\nMore than one World of Warcraft install found.\n")
    for i, c in enumerate(candidates, 1):
        print(f"   {i:>2}. {c.label}")
    while True:
        raw = _ask(f"\nWhich install? [1-{len(candidates)}, blank to skip]: ")
        if not raw:
            return None
        try:
            n = int(raw)
        except ValueError:
            print("  Enter a number.")
            continue
        if 1 <= n <= len(candidates):
            return candidates[n - 1].addons
        print(f"  Numbers must be between 1 and {len(candidates)}.")


# --------------------------------------------------------------------------
#  Planning
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
#  Commands
# --------------------------------------------------------------------------







def cmd_list(args) -> int:
    found, problems = skills_mod.discover()
    print("Skills in this repository:\n")
    for s in found:
        print(f"  {s.name}\n      {s.summary()}")

    house_rules, rule_problems = rules_mod.discover()
    problems = problems + rule_problems
    if house_rules:
        print("\nRules in this repository (loaded in every session):\n")
        for r in house_rules:
            print(f"  {r.name}\n      {r.summary()}")

    print("\nGit hooks this installer can place (needs --repo):\n")
    for hook in hooks_mod.HOOKS:
        print(f"  {hook.name:<16} {hook.event:<12} {hook.summary}")

    game_addons, addon_problems = addons_mod.discover()
    problems = problems + addon_problems
    if game_addons:
        print("\nWoW addons in this repository:\n")
        for a in game_addons:
            print(f"  {a.name}\n      {a.summary()}")

    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  ! {p}")

    if game_addons:
        print("\nWorld of Warcraft installs found:\n")
        installs = wow.discover_installs()
        if installs:
            for i in installs:
                print(f"  {i.label}")
        else:
            print("  none — pass --wow-addons or set $WOW_ADDONS_DIR")

    print("\nHarnesses:\n")
    width = max(len(h.name) for h in registry.HARNESSES)
    for h in registry.HARNESSES:
        user = f"~/{h.skills_user[0]}" if h.skills_user else "-"
        proj = h.skills_project[0] if h.skills_project else "-"
        mark = "*" if detected(h) else " "
        print(f" {mark} {h.key:<16} {h.name:<{width}}  user: {user:<26} project: {proj}")
        if args.verbose and h.note:
            print(f"   {' ' * width}  {h.note}")
        if args.verbose and h.takes_rules:
            r_user = f"~/{h.rules_user[0]}" if h.rules_user else "-"
            r_proj = h.rules_project[0] if h.rules_project else "-"
            print(f"   {' ' * width}  rules: user: {r_user}  project: {r_proj}  as *{h.rules_ext}")
            if h.rules_note:
                print(f"   {' ' * width}  {h.rules_note}")
    print("\n  * = detected in your home directory")
    if not args.verbose:
        print("  Pass --verbose for the per-harness notes and caveats.")
    return 0 if not problems else 1


def _present(directory: Path, items) -> list[str]:
    """How each item stands in `directory`: link, copy, or a stranger."""
    out = []
    for it in items:
        t = directory / it.name
        if not t.is_symlink() and not t.exists():
            continue
        if t.is_symlink():
            if not t.exists():
                out.append(f"{it.name} (BROKEN LINK)")
            elif t.resolve() == it.path.resolve():
                out.append(f"{it.name} (link)")
            else:
                out.append(f"{it.name} (link elsewhere -> {t.resolve()})")
        elif (t / ".wow-tools-install").is_file():
            out.append(f"{it.name} (copy)")
        else:
            out.append(f"{it.name} (not ours)")
    return out


def _present_rules(directory: Path, ext: str, items) -> list[str]:
    """How each rule stands in `directory`, under the name that harness wants."""
    out = []
    for r in items:
        t = directory / r.filename(ext)
        if not t.is_symlink() and not t.exists():
            continue
        if t.is_symlink():
            if Path(os.readlink(t)).resolve() == r.path.resolve():
                out.append(f"{t.name} (link)")
            else:
                out.append(f"{t.name} (link elsewhere -> {t.resolve()})")
        elif r.marker() in t.read_text(encoding="utf-8", errors="replace"):
            out.append(f"{t.name} (copy)")
        else:
            out.append(f"{t.name} (not ours)")
    return out


def cmd_status(args) -> int:
    found, _ = skills_mod.discover()
    any_installed = False

    # Several harnesses share one directory, so report the directory once
    # rather than printing the same three links under each harness name.
    seen: dict[Path, list[str]] = OrderedDict()
    for h in registry.HARNESSES:
        for scope in ("user", "project"):
            directory = engine.resolve_directory(h, scope, args.project_root)
            if directory is None or not directory.is_dir():
                continue
            seen.setdefault(directory, []).append(f"{h.name} [{scope}]")

    for directory, users in seen.items():
        present = _present(directory, found)
        if not present:
            continue
        any_installed = True
        print(f"{directory}")
        print(f"  read by: {', '.join(users)}")
        for p in present:
            print(f"    {p}")

    house_rules, _ = rules_mod.discover()
    if house_rules:
        # Keyed by (directory, extension) for the same reason the install is:
        # the file has a different name in each harness's directory.
        rule_dirs: dict = OrderedDict()
        for h in registry.HARNESSES:
            if not h.takes_rules:
                continue
            for scope in ("user", "project"):
                directory = engine.resolve_directory(h, scope, args.project_root, kind="rules")
                if directory is None or not directory.is_dir():
                    continue
                rule_dirs.setdefault((directory, h.rules_ext), []).append(f"{h.name} [{scope}]")

        for (directory, ext), users in rule_dirs.items():
            present = _present_rules(directory, ext, house_rules)
            if not present:
                continue
            any_installed = True
            print(f"\n{directory}")
            print(f"  read by: {', '.join(users)}")
            for p in present:
                print(f"    {p}")

    if getattr(args, "repo", None):
        repo = Path(args.repo).expanduser().resolve()
        print(f"\n{repo}")
        print("  git hooks:")
        for hook, state in hooks_mod.status(repo):
            print(f"    {hook.event:<12} {hook.name:<16} {state}")
            if state == "installed":
                any_installed = True

    game_addons, _ = addons_mod.discover()
    if game_addons:
        directory, candidates = wow.resolve_addons_dir(getattr(args, "wow_addons", None))
        dirs = [directory] if directory is not None else [c.addons for c in candidates]
        for d in dirs:
            if d is None or not d.is_dir():
                continue
            present = _present(d, game_addons)
            if not present:
                continue
            any_installed = True
            print(f"\n{d}")
            print("  read by: World of Warcraft")
            for p in present:
                print(f"    {p}")

    if not any_installed:
        print("Nothing from this repository is installed anywhere yet.")
        print("Run: python -m wow_tools install")
    return 0


def cmd_doctor(args) -> int:
    """Answer "is what I am editing what the game loads".

    `status` says where things are installed. This says whether an edit will
    reach the game, which is a different question once an AddOns folder holds
    a checkout of its own: a folder named after an installed addon, holding
    that addon's `.toc`, reads exactly like the source and swallows edits in
    silence. Nothing here writes anything.
    """
    game_addons, _ = addons_mod.discover()
    if not game_addons:
        print("No addons in this repository.")
        return 0

    directory, candidates = wow.resolve_addons_dir(getattr(args, "wow_addons", None))
    dirs = [directory] if directory is not None else [c.addons for c in candidates]
    dirs = [d for d in dirs if d is not None and d.is_dir()]
    if not dirs:
        print("No World of Warcraft AddOns folder found.")
        print("Pass --wow-addons PATH, or set $WOW_ADDONS_DIR.")
        return 1

    problems = 0
    for d in dirs:
        print(f"{d}")
        for addon in game_addons:
            target = d / addon.name
            if not target.is_symlink() and not target.exists():
                print(f"  {addon.name}: not installed")
                continue

            try:
                live = target.resolve()
            except OSError:
                live = target
            if target.is_symlink() and not target.exists():
                print(f"  {addon.name}: BROKEN LINK -> {os.readlink(target)}")
                problems += 1
                continue

            mine = live == addon.path.resolve()
            print(f"  {addon.name}: loads {live}")
            if not mine:
                print("    ^ that is NOT this repository's copy — edits here will not reach it")
                problems += 1

            for shadow in wow.find_shadow_copies(d, addon.name, live):
                print(f"    shadow copy: {shadow}")
                print("      nothing loads it; editing it changes nothing in game")
                problems += 1

    if problems:
        print(f"\n{problems} problem(s). A shadow copy is safe to delete once you have "
              "checked it holds nothing the live copy lacks:")
        print("  diff -r <shadow> <live>")
        return 1
    print("\nEvery installed addon resolves into this repository, with nothing shadowing it.")
    return 0


def _run(args, uninstalling: bool) -> int:
    """The four halves are independent; any one can be asked for alone."""
    rules_wanted = bool(args.rules)
    hooks_wanted = bool(args.hooks or args.repo)
    # --harness selects a directory for skills *and* rules, so on its own it
    # cannot mean rules: that would install an always-on rule into every
    # session for anyone who asked for skills.
    skills_wanted = bool(args.harness or args.skills)
    addons_wanted = bool(args.addons or args.wow_addons)
    explicit = skills_wanted or addons_wanted or rules_wanted or hooks_wanted
    interactive = _interactive_available()

    if not explicit and not interactive:
        print(
            "error: nothing selected and this is not an interactive terminal.\n"
            "Pass --harness (for skills), --rules, --addons (for WoW addons), "
            "or --hooks; see `python -m wow_tools list`.",
            file=sys.stderr,
        )
        return 2

    rc = 0
    if skills_wanted or not explicit:
        rc |= _run_skills(args, uninstalling)
    if rules_wanted or not explicit:
        rc |= _run_rules(args, uninstalling)
    if addons_wanted or not explicit:
        rc |= _run_addons(args, uninstalling)
    # Hooks stay out of the default run. They write into a repository the user
    # named, and there is no sensible guess for which one.
    if hooks_wanted:
        rc |= _run_hooks(args, uninstalling)
    return rc


def _report(applied, cat, n_groups: int) -> None:
    """Say what happened, one group at a time.

    The directory is only worth repeating when there is more than one, which is
    the uncommon case: most harnesses read the same cross-agent path, so eight
    of them selected usually means one heading.
    """
    shown = None
    for key, result in applied.results:
        if key != shown:
            print()
            if n_groups > 1:
                print(f"{cat.directory_of(key)}")
            shown = key
        print(result)


def _run_rules(args, uninstalling: bool) -> int:
    found, problems = rules_mod.discover()
    if problems:
        print("Rule validation problems:", file=sys.stderr)
        for p in problems:
            print(f"  ! {p}", file=sys.stderr)
    if not found:
        return 0

    if args.rules:
        try:
            chosen = RULES.resolve(catalogue.split_specs(args.rules), found)
        except catalogue.UnknownName as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    elif _interactive_available():
        chosen = choose_rules(found)
    else:
        chosen = RULES.unnamed_selection(found)

    if not chosen:
        return 0

    if args.harness:
        keys = [k for spec in args.harness for k in spec.replace(",", " ").split()]
        try:
            harnesses = [registry.get(k) for k in keys]
        except catalogue.UnknownName as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    else:
        harnesses = [h for h in registry.HARNESSES if h.takes_rules]

    groups, skipped = plan_rules(harnesses, args.scope, args.project_root)

    # Being skipped is the normal outcome here, not an edge case: most
    # harnesses keep their instructions in a single file the user owns, and
    # this installer will not write into one of those.
    for h in skipped:
        if h.takes_rules:
            print(f"\n{h.name}: no rules directory for {args.scope} scope.")
        else:
            print(f"\n{h.name}: no rules directory to install into.")
        if h.rules_note:
            print(f"  {h.rules_note}")
        _print_manual_rule_line(h, chosen)

    if not groups:
        return 0

    print()
    verb = "Uninstalling" if uninstalling else "Installing"
    print(f"{verb} {len(chosen)} rule(s) into {len(groups)} "
          f"director{'y' if len(groups) == 1 else 'ies'} ({args.scope} scope):")

    methods = {}
    for (directory, ext), hs in groups.items():
        method, method_note = _method_for(directory, args, uninstalling)
        methods[directory, ext] = method
        print(f"\n{directory}  (as *{ext}){method_note}")
        print(f"  for: {', '.join(h.name for h in hs)}")

    if not uninstalling and not args.yes and not args.dry_run and _interactive_available():
        if (_ask("\nProceed? [Y/n]: ") or "y").lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    applied = RULES.apply(chosen, groups, methods, uninstalling=uninstalling,
                          force=args.force, dry_run=args.dry_run)
    _report(applied, RULES, len(groups))

    if applied.failed:
        print("\nSome entries were left alone. Re-run with --force to replace them.")
        return 1
    return 0


def _print_manual_rule_line(harness, chosen) -> None:
    """Tell someone how to do by hand what we declined to do for them.

    The harnesses reached here keep their instructions in one file that the
    user wrote. Appending to it is the kind of edit that should be a decision,
    so this prints the paste and stops.
    """
    single = {
        "codex": "~/.codex/AGENTS.md",
        "gemini-cli": "~/.gemini/GEMINI.md",
        "antigravity": "~/.gemini/GEMINI.md",
    }.get(harness.key)
    if not single:
        return
    names = ", ".join(str(r.path) for r in chosen)
    print(f"  To apply it there, append the body of {names} to {single} by hand.")


def _run_hooks(args, uninstalling: bool) -> int:
    if not args.repo:
        print("error: --hooks needs --repo, the repository to install into.\n"
              "  A hook lives in .git/hooks, which is per clone and never pushed.",
              file=sys.stderr)
        return 2

    available, _ = HOOKS.discover()
    try:
        chosen = HOOKS.resolve(catalogue.split_specs(args.hooks or ["all"]), available)
    except catalogue.UnknownName as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not chosen:
        return 0

    repo = Path(args.repo).expanduser().resolve()
    verb = "Uninstalling" if uninstalling else "Installing"
    print(f"\n{verb} {len(chosen)} hook(s) in {repo}:")

    if not uninstalling and not args.yes and not args.dry_run and _interactive_available():
        if (_ask("\nProceed? [Y/n]: ") or "y").lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    failed = False
    for hook in chosen:
        if uninstalling:
            outcome, target, detail = hooks_mod.uninstall(hook, repo, dry_run=args.dry_run)
        else:
            outcome, target, detail = hooks_mod.install(
                hook, repo, force=args.force, dry_run=args.dry_run)
        print(f"  {outcome:<10} {hook.event:<12} {target}")
        if detail:
            print(f"             {detail}")
        failed = failed or outcome == "blocked"

    if failed:
        print("\nSome hooks were left alone. Re-run with --force to replace them.")
        return 1
    return 0


def _method_for(directory: Path, args, uninstalling: bool):
    """Decide link-or-copy per directory: the answer differs between a home
    directory and a game install on another filesystem."""
    if uninstalling:
        return None, ""
    if args.copy:
        return Method.COPY, " (--copy)"
    if engine.symlinks_available(directory):
        return Method.SYMLINK, ""
    return Method.COPY, (
        " (symlinks unavailable here -- copying instead; re-run after a `git pull`)"
    )


def _run_skills(args, uninstalling: bool) -> int:
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
        except catalogue.UnknownName as e:
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
        try:
            chosen = SKILLS.resolve(catalogue.split_specs(args.skills), found)
        except catalogue.UnknownName as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    elif _interactive_available():
        chosen = choose_skills(found)
    else:
        chosen = SKILLS.unnamed_selection(found)

    if not chosen:
        return 0

    groups, skipped = plan(harnesses, args.scope, args.project_root)

    for h in skipped:
        print(f"\n{h.name}: nothing to install for {args.scope} scope.")
        if h.note:
            print(f"  {h.note}")

    if not groups:
        return 0

    # Each directory is announced once, with the harnesses reading it and the
    # method that will be used, and its results land underneath. Printing the
    # plan and then re-printing the same headings over the results said
    # everything twice for the common case of one directory.
    print()
    verb = "Uninstalling" if uninstalling else "Installing"
    print(f"{verb} {len(chosen)} skill(s) into {len(groups)} director{'y' if len(groups) == 1 else 'ies'} "
          f"({args.scope} scope):")

    methods = {}
    for directory, hs in groups.items():
        method, method_note = _method_for(directory, args, uninstalling)
        methods[directory] = method
        print(f"\n{directory}{method_note}")
        print(f"  for: {', '.join(h.name for h in hs)}")

    if not uninstalling and not args.yes and not args.dry_run and _interactive_available():
        if (_ask("\nProceed? [Y/n]: ") or "y").lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    applied = SKILLS.apply(chosen, groups, methods, uninstalling=uninstalling,
                           force=args.force, dry_run=args.dry_run)
    _report(applied, SKILLS, len(groups))

    if applied.failed:
        print("\nSome entries were left alone. Re-run with --force to replace them.")
        return 1

    if not uninstalling and not args.dry_run:
        notes = [h for hs in groups.values() for h in hs if h.note]
        if notes:
            print("\nNotes:")
            for h in notes:
                print(f"  {h.name}: {h.note}")
        # Advice to restart is only advice when something moved. Printing it
        # after a run that changed nothing invites a pointless restart, and
        # makes a no-op look like work.
        print(f"\n{applied.advice(SKILLS)}")
    return 0


def _run_addons(args, uninstalling: bool) -> int:
    found, problems = addons_mod.discover()
    if problems:
        print("Addon validation problems:", file=sys.stderr)
        for p in problems:
            print(f"  ! {p}", file=sys.stderr)
    if not found:
        return 0

    if args.addons:
        try:
            chosen = ADDONS.resolve(catalogue.split_specs(args.addons), found)
        except catalogue.UnknownName as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    elif _interactive_available():
        chosen = choose_addons(found)
    else:
        # Reached only via --wow-addons with no --addons.
        print(f"error: {ADDONS.refusal}", file=sys.stderr)
        return 2

    if not chosen:
        return 0

    directory = choose_addons_dir(args.wow_addons)
    if directory is None:
        return 0 if uninstalling else 1

    if not wow.looks_like_addons_dir(directory) and not args.force:
        print(
            f"\nerror: {directory} does not look like a WoW AddOns folder.\n"
            "  Expected .../World of Warcraft/<flavor>/Interface/AddOns.\n"
            "  Pass --force if it really is one.",
            file=sys.stderr,
        )
        return 1

    method, method_note = _method_for(directory, args, uninstalling)
    verb = "Uninstalling" if uninstalling else "Installing"
    print(f"\n{verb} {len(chosen)} addon(s):")
    print(f"\n{directory}{method_note}")

    if not uninstalling and not args.yes and not args.dry_run and _interactive_available():
        if (_ask("\nProceed? [Y/n]: ") or "y").lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    # One directory, already announced above, so the results follow it directly
    # rather than under a heading of their own.
    applied = ADDONS.apply(chosen, {directory: []}, {directory: method},
                           uninstalling=uninstalling, force=args.force,
                           dry_run=args.dry_run)
    for _key, r in applied.results:
        print(r)

    if applied.failed:
        print("\nSome entries were left alone. Re-run with --force to replace them.")
        return 1

    if not uninstalling and not args.dry_run:
        missing = _missing_dependencies(chosen, directory)
        if missing:
            print("\nMissing dependencies — these addons will not load without them:")
            for addon_name, deps in missing:
                print(f"  {addon_name} needs {', '.join(deps)}")
        # A /reload interrupts whatever the player is doing. Asking for one
        # after a run that moved nothing is worse than merely redundant.
        print(f"\n{applied.advice(ADDONS)}")
    return 0


def _missing_dependencies(chosen, directory: Path) -> list[tuple[str, list[str]]]:
    """Dependencies named in a .toc that are not in the same AddOns folder.

    The client does not report this; it just silently refuses to load the addon,
    which reads as "the install did not work".
    """
    out = []
    for a in chosen:
        gaps = [
            d for d in a.dependencies
            if not (directory / d).is_dir() and not (directory / d).is_symlink()
        ]
        if gaps:
            out.append((a.name, gaps))
    return out


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
                       help="skill name(s), or 'all'/'none'; repeatable")
        p.add_argument("--rules", action="append", metavar="NAME",
                       help="always-on rule name(s), or 'all'/'none'; repeatable")
        p.add_argument("--addons", action="append", metavar="NAME",
                       help="WoW addon name(s), or 'all'/'none'; repeatable")
        p.add_argument("--hooks", action="append", metavar="NAME",
                       help="git hook name(s), or 'all'/'none'; needs --repo")
        p.add_argument("--repo", metavar="PATH", default=None,
                       help="the git repository to install hooks into")
        p.add_argument("--wow-addons", metavar="PATH", default=None,
                       help="the WoW AddOns folder to install addons into "
                            "(default: found automatically; $WOW_ADDONS_DIR)")
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
    p_status.add_argument("--wow-addons", metavar="PATH", default=None)
    p_status.add_argument("--repo", metavar="PATH", default=None,
                          help="also report the git hooks installed in this repository")
    p_status.set_defaults(func=cmd_status)

    p_doctor = sub.add_parser(
        "doctor", help="check that an edit to an addon here reaches the game")
    p_doctor.add_argument("--wow-addons", metavar="PATH", default=None)
    p_doctor.set_defaults(func=cmd_doctor)

    return ap


COMMANDS = ("install", "uninstall", "list", "status", "doctor")


def _with_default_command(argv: list[str]) -> list[str]:
    """Let the install options be passed without naming the subcommand.

    `install.sh` is the documented entry point and installing is what it is
    for, so `install.sh --harness codex --skills all` has to mean what it
    plainly says. Without this, argparse reads the first flag's value as the
    subcommand and reports an invalid choice, naming the wrong thing.
    """
    if not argv:
        return ["install"]
    first = argv[0]
    if first in COMMANDS or first in ("-h", "--help"):
        return argv
    return ["install", *argv]


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(_with_default_command(list(argv if argv is not None else sys.argv[1:])))
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
