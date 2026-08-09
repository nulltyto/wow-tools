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
from .skills import Skill

# What the engine can place. Both carry `name`, `path`, and `signature`.
Item = Union[Skill, Addon]


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


def symlinks_available(probe_dir: Path) -> bool:
    """Whether this process can actually create a symlink in `probe_dir`.

    Asked by trying, not by inspecting the platform. On Windows the answer
    depends on Developer Mode and on the process's privileges rather than on
    the OS version, and on Linux it depends on the filesystem -- a WoW install
    on an exFAT or NTFS mount cannot hold one.
    """
    probe_dir.mkdir(parents=True, exist_ok=True)
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


def resolve_directory(harness: Harness, scope: str, project_root: Path | None = None) -> Path | None:
    """The directory this harness would be installed into, or None."""
    candidates = harness.skills_user if scope == "user" else harness.skills_project
    if not candidates:
        return None
    base = Path.home() if scope == "user" else (project_root or Path.cwd())
    return base.joinpath(*candidates[0].split("/"))


def _is_our_link(target: Path, source: Path) -> bool:
    if not target.is_symlink():
        return False
    try:
        return Path(os.readlink(target)).resolve() == source.resolve()
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


def _remove(target: Path) -> None:
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
