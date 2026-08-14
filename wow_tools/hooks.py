"""Install the git hooks that gate what a repository records.

Rules are context, not enforcement. Claude Code's own documentation is explicit
about it: instruction files "shape Claude's behavior but are not a hard
enforcement layer", and anything that must run at a fixed point -- before every
commit -- belongs in a hook. So the rule under `rules/` explains, and this
installs the thing that actually says no.

Hooks are the one thing here that installs into *another* repository. Skills go
to a harness directory in your home, addons go to a game folder, and both are
about this machine. A hook is about one checkout: the addon repo you are about
to commit to. That is why `--repo` is required and why nothing is installed by
default.

A hook is a file in `.git/hooks`, which is not tracked and not pushed, so this
has to be run once per clone. Git offers `core.hooksPath` for a tracked hooks
directory, and it is deliberately not used here: pointing that at a checked-in
directory silently changes what every future `git commit` in the repository
executes, which is a bigger claim on somebody's machine than an installer
should make.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every hook is a marker, the git hook it occupies, and the command it runs.
# The marker goes in a comment inside the generated hook so that a later run,
# and uninstall, can tell our file from one somebody wrote.
MARKER = "wow-tools:{name}"

TEMPLATE = """#!/bin/sh
# {marker} -- installed by wow-tools. Remove with:
#   python -m wow_tools uninstall --hooks {name} --repo .
# Skip once with `git commit --no-verify`.
exec {python} {script} {args}
"""


@dataclass(frozen=True)
class Hook:
    name: str
    event: str
    script: Path
    args: str
    summary: str
    # Paths that must exist in the target repository for this hook to make
    # sense there. A hook that cannot do its job does not fail quietly -- it
    # fails the commit -- so `--hooks all` pointed at the wrong checkout would
    # otherwise block every commit in it until someone found the cause.
    requires: tuple = ()
    requires_desc: str = ""

    def marker(self) -> str:
        return MARKER.format(name=self.name)

    def applies_to(self, repo: Path) -> bool:
        return not self.requires or any((repo / p).exists() for p in self.requires)

    def body(self) -> str:
        return TEMPLATE.format(
            marker=self.marker(),
            name=self.name,
            python=sys.executable,
            script=self.script,
            args=self.args,
        )


HOOKS: tuple[Hook, ...] = (
    Hook(
        name="ascii-git-text",
        event="commit-msg",
        script=REPO_ROOT / "tools" / "lint" / "ascii_text.py",
        args='--commit-msg "$1"',
        summary="reject a commit message containing non-ASCII characters",
    ),
    Hook(
        name="eui-style",
        event="pre-commit",
        script=REPO_ROOT / "skills" / "ellesmereui-pr-check" / "scripts" / "check_style.py",
        args='--root "$(git rev-parse --show-toplevel)" --staged',
        summary="run the EllesmereUI style check over the staged lines",
        requires=("EllesmereUI.toc",),
        requires_desc="an EllesmereUI checkout (no EllesmereUI.toc here)",
    ),
)

HOOK_BY_NAME = {h.name: h for h in HOOKS}


def resolve_names(requested: list) -> list:
    """Map `--hooks` values to hooks. 'all' selects everything, 'none' nothing."""
    lowered = [r.strip().lower() for r in requested]
    if any(r == "none" for r in lowered):
        return []
    if any(r == "all" for r in lowered):
        return list(HOOKS)
    picked = []
    unknown = []
    for raw in requested:
        r = raw.strip().lower()
        if not r:
            continue
        hit = HOOK_BY_NAME.get(r)
        if hit is not None:
            if hit not in picked:
                picked.append(hit)
        else:
            unknown.append(raw)
    if unknown:
        raise KeyError(
            f"unknown hook(s): {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(HOOK_BY_NAME))}"
        )
    return picked


def hooks_dir(repo: Path) -> Path | None:
    """Where this repository keeps its hooks, or None if it is not one.

    Asked of git rather than assumed to be `.git/hooks`, because a worktree and
    a submodule both keep theirs somewhere else entirely.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    path = Path(proc.stdout.strip())
    return path if path.is_absolute() else repo / path


def install(hook: Hook, repo: Path, *, force: bool = False, dry_run: bool = False) -> tuple:
    """Place `hook` in `repo`. Returns (outcome, target, detail)."""
    directory = hooks_dir(repo)
    if directory is None:
        return "blocked", repo, "not a git repository"
    target = directory / hook.event

    if not hook.script.is_file():
        return "blocked", target, f"the script it would run is missing: {hook.script}"

    if not hook.applies_to(repo) and not force:
        return ("skipped", target,
                f"needs {hook.requires_desc}; --force installs it anyway")

    if target.exists():
        existing = target.read_text(encoding="utf-8", errors="replace")
        if hook.marker() in existing:
            if existing == hook.body():
                return "current", target, "already installed"
        elif not force:
            # Someone else's hook, or a second wow-tools hook on the same
            # event. Overwriting either loses work that is not in version
            # control, since .git/hooks is not tracked.
            return ("blocked", target,
                    f"a {hook.event} hook is already here and is not this one; "
                    "--force replaces it. To keep both, add this line to it:\n"
                    f"             {sys.executable} {hook.script} {hook.args}")

    if dry_run:
        return "planned", target, "would install"

    directory.mkdir(parents=True, exist_ok=True)
    updated = target.exists()
    target.write_text(hook.body(), encoding="utf-8")
    target.chmod(0o755)
    return ("updated" if updated else "installed"), target, hook.summary


def uninstall(hook: Hook, repo: Path, *, dry_run: bool = False) -> tuple:
    directory = hooks_dir(repo)
    if directory is None:
        return "blocked", repo, "not a git repository"
    target = directory / hook.event
    if not target.exists():
        return "absent", target, ""
    if hook.marker() not in target.read_text(encoding="utf-8", errors="replace"):
        return "blocked", target, "hook was not installed by wow-tools; left alone"
    if dry_run:
        return "planned", target, "would remove"
    target.unlink()
    return "removed", target, ""


def status(repo: Path) -> list:
    """(hook, state) for every known hook, for `wow_tools status`."""
    directory = hooks_dir(repo)
    out = []
    for hook in HOOKS:
        if directory is None:
            out.append((hook, "no git repository"))
            continue
        target = directory / hook.event
        if not target.exists():
            out.append((hook, "not installed"))
        elif hook.marker() in target.read_text(encoding="utf-8", errors="replace"):
            out.append((hook, "installed"))
        else:
            out.append((hook, f"another {hook.event} hook is here"))
    return out
