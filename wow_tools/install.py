"""Place skill and addon directories where the things that read them will look.

Symlink by default, because a link means `git pull` updates every installed
harness at once and there is no second copy to drift. Windows is the reason
for the fallback: creating a symlink there needs Developer Mode or an elevated
process, and a fresh install refusing to work is worse than a copy that has to
be re-run after a pull.

Skills and addons go to very different places, but the placement problem is the
same one -- link or copy a directory, never clobber somebody else's -- so the
engine works on anything with a `name`, a `path`, and a `signature` file that
identifies an installed copy.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Union

from .addons import Addon
from .registry import Harness
from .rules import Rule
from .skills import Skill

# What the engine can place. Skills and addons are directories carrying `name`,
# `path`, and `signature`; a rule is a single file and takes the pair of
# functions at the bottom of this module instead.
Item = Union[Skill, Addon, Rule]


class Method(Enum):
    SYMLINK = "symlink"
    COPY = "copy"


class Outcome(Enum):
    LINKED = "linked"
    COPIED = "copied"
    UPDATED = "updated"
    CURRENT = "current"
    REMOVED = "removed"
    ABSENT = "absent"
    BLOCKED = "blocked"
    PLANNED = "planned"

    @property
    def ok(self) -> bool:
        return self is not Outcome.BLOCKED


@dataclass
class Result:
    item: Item
    target: Path
    outcome: Outcome
    detail: str = ""

    def __str__(self) -> str:
        line = f"  {self.outcome.value:<8} {self.item.name}  ->  {self.target}"
        return f"{line}\n           {self.detail}" if self.detail else line


def _nearest_existing(path: Path) -> Path:
    """The closest directory at or above `path` that is already there."""
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return candidate
    return path


def symlinks_available(probe_dir: Path) -> bool:
    """Whether this process can actually create a symlink in `probe_dir`.

    Asked by trying, not by inspecting the platform. On Windows the answer
    depends on Developer Mode and on the process's privileges rather than on
    the OS version, and on Linux it depends on the filesystem -- a WoW install
    on an exFAT or NTFS mount cannot hold one.

    Probed against the nearest directory that already exists, rather than by
    creating the target. A target that does not exist yet will sit on its
    parent's filesystem and inherit the answer, so nothing is lost -- and a
    --dry-run stops leaving an empty directory behind, which is what the
    creating version did on every run that had nowhere to install yet.
    """
    probe_dir = _nearest_existing(probe_dir)
    probe = probe_dir / ".wow-tools-symlink-probe"
    try:
        if probe.is_symlink() or probe.exists():
            probe.unlink()
        os.symlink(probe_dir, probe, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        return False
    else:
        return True
    finally:
        try:
            if probe.is_symlink() or probe.exists():
                probe.unlink()
        except OSError:
            pass


def resolve_directory(
    harness: Harness,
    scope: str,
    project_root: Path | None = None,
    kind: str = "skills",
) -> Path | None:
    """The directory this harness would be installed into, or None.

    `kind` picks between the skills directory and the rules directory. They are
    different paths for every harness that has both, and plenty of harnesses
    have one and not the other.
    """
    if kind == "rules":
        candidates = harness.rules_user if scope == "user" else harness.rules_project
    else:
        candidates = harness.skills_user if scope == "user" else harness.skills_project
    if not candidates:
        return None
    base = Path.home() if scope == "user" else (project_root or Path.cwd())
    return base.joinpath(*candidates[0].split("/"))


def _is_our_link(target: Path, source: Path) -> bool:
    """Whether `target` is a link this installer made to `source`.

    Both sides go through realpath rather than being compared as text.
    Windows returns a link's destination in extended-length form
    (`\\\\?\\D:\\...`), which never compares equal to the same path written
    normally -- so a second install read its own link as a stranger's and
    refused to touch it, and uninstall left it behind.
    """
    if not target.is_symlink():
        return False
    try:
        return os.path.realpath(target) == os.path.realpath(source)
    except OSError:
        return False


def install_item(
    item: Item,
    directory: Path,
    method: Method,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Result:
    target = directory / item.name

    if target.is_symlink():
        if _is_our_link(target, item.path) and method is Method.SYMLINK:
            return Result(item, target, Outcome.CURRENT, "already linked here")
        if not force:
            try:
                dest = os.readlink(target)
            except OSError:
                dest = "?"
            return Result(
                item, target, Outcome.BLOCKED,
                f"a symlink to {dest} is already here; --force replaces it",
            )
    elif target.exists():
        # A real directory. It may be an earlier --copy install of this same
        # item, which is safe to refresh, or somebody else's with a colliding
        # name, which is not. An AddOns folder makes this more than theoretical:
        # a hand-installed copy of the same addon from CurseForge sits at
        # exactly this path.
        ours = (target / item.signature).is_file() and (target / ".wow-tools-install").is_file()
        if not ours and not force:
            return Result(
                item, target, Outcome.BLOCKED,
                "a directory is already here and was not installed by wow-tools; --force replaces it",
            )

    if dry_run:
        return Result(item, target, Outcome.PLANNED, f"would {method.value}")

    directory.mkdir(parents=True, exist_ok=True)
    existed = target.is_symlink() or target.exists()
    _remove(target)

    if method is Method.SYMLINK:
        os.symlink(item.path, target, target_is_directory=True)
        return Result(item, target, Outcome.UPDATED if existed else Outcome.LINKED)

    # evals/ holds skill-creator test fixtures, which are development material
    # for this repo rather than anything the skill reads at run time.
    shutil.copytree(
        item.path, target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "evals"),
    )
    # Marks the copy as ours so a later run may refresh it without --force,
    # and so uninstall can tell it apart from a hand-made directory.
    (target / ".wow-tools-install").write_text(
        f"copied from {item.path}\n", encoding="utf-8"
    )
    return Result(item, target, Outcome.UPDATED if existed else Outcome.COPIED)


def uninstall_item(item: Item, directory: Path, *, dry_run: bool = False) -> Result:
    target = directory / item.name
    if not target.is_symlink() and not target.exists():
        return Result(item, target, Outcome.ABSENT)

    if target.is_symlink():
        if not _is_our_link(target, item.path):
            return Result(
                item, target, Outcome.BLOCKED,
                "symlink points somewhere else; left alone",
            )
    elif not (target / ".wow-tools-install").is_file():
        return Result(
            item, target, Outcome.BLOCKED,
            "directory was not installed by wow-tools; left alone",
        )

    if dry_run:
        return Result(item, target, Outcome.PLANNED, "would remove")
    _remove(target)
    return Result(item, target, Outcome.REMOVED)


# The names the CLI and the tests used before addons existed.
install_skill = install_item
uninstall_skill = uninstall_item


# --------------------------------------------------------------------------
#  Rules: the same problem, one file at a time
# --------------------------------------------------------------------------
# A rule is a file, not a directory, so it needs its own pair of functions
# rather than a flag on the ones above. Two things follow from being a file.
#
# The name changes per harness: Cursor ignores a plain .md and VS Code matches
# *.instructions.md, so `filename` is passed in rather than taken from the
# source. A symlink renames happily, which is why this still works as a link.
#
# And there is no directory to drop a `.wow-tools-install` marker into, so a
# copy is identified by a marker inside the file itself.


def install_rule(
    rule,
    directory: Path,
    method: Method,
    *,
    filename: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> Result:
    target = directory / (filename or rule.filename())

    if target.is_symlink():
        if _is_our_link(target, rule.path) and method is Method.SYMLINK:
            return Result(rule, target, Outcome.CURRENT, "already linked here")
        if not force:
            try:
                dest = os.readlink(target)
            except OSError:
                dest = "?"
            return Result(
                rule, target, Outcome.BLOCKED,
                f"a symlink to {dest} is already here; --force replaces it",
            )
    elif target.exists():
        if not _has_marker(target, rule.marker()) and not force:
            return Result(
                rule, target, Outcome.BLOCKED,
                "a file is already here and was not installed by wow-tools; --force replaces it",
            )

    if dry_run:
        return Result(rule, target, Outcome.PLANNED, f"would {method.value}")

    directory.mkdir(parents=True, exist_ok=True)
    existed = target.is_symlink() or target.exists()
    _remove(target)

    if method is Method.SYMLINK:
        os.symlink(rule.path, target)
        return Result(rule, target, Outcome.UPDATED if existed else Outcome.LINKED)

    shutil.copyfile(rule.path, target)
    return Result(rule, target, Outcome.UPDATED if existed else Outcome.COPIED)


def uninstall_rule(
    rule, directory: Path, *, filename: str | None = None, dry_run: bool = False
) -> Result:
    target = directory / (filename or rule.filename())
    if not target.is_symlink() and not target.exists():
        return Result(rule, target, Outcome.ABSENT)

    if target.is_symlink():
        if not _is_our_link(target, rule.path):
            return Result(
                rule, target, Outcome.BLOCKED, "symlink points somewhere else; left alone"
            )
    elif not _has_marker(target, rule.marker()):
        return Result(
            rule, target, Outcome.BLOCKED,
            "file was not installed by wow-tools; left alone",
        )

    if dry_run:
        return Result(rule, target, Outcome.PLANNED, "would remove")
    _remove(target)
    return Result(rule, target, Outcome.REMOVED)


def _has_marker(target: Path, marker: str) -> bool:
    try:
        return marker in target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _remove(target: Path) -> None:
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
