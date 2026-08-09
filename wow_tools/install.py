"""Place skill directories where harnesses will find them.

Symlink by default, because a link means `git pull` updates every installed
harness at once and there is no second copy to drift. Windows is the reason
for the fallback: creating a symlink there needs Developer Mode or an elevated
process, and a fresh install refusing to work is worse than a copy that has to
be re-run after a pull.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .registry import Harness
from .skills import Skill


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
    skill: Skill
    target: Path
    outcome: Outcome
    detail: str = ""

    def __str__(self) -> str:
        line = f"  {self.outcome.value:<8} {self.skill.name}  ->  {self.target}"
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


def install_skill(
    skill: Skill,
    directory: Path,
    method: Method,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Result:
    target = directory / skill.name

    if target.is_symlink():
        if _is_our_link(target, skill.path) and method is Method.SYMLINK:
            return Result(skill, target, Outcome.CURRENT, "already linked here")
        if not force:
            try:
                dest = os.readlink(target)
            except OSError:
                dest = "?"
            return Result(
                skill, target, Outcome.BLOCKED,
                f"a symlink to {dest} is already here; --force replaces it",
            )
    elif target.exists():
        # A real directory. It may be an earlier --copy install of this same
        # skill, which is safe to refresh, or somebody else's skill with a
        # colliding name, which is not.
        ours = (target / "SKILL.md").is_file() and (target / ".wow-tools-install").is_file()
        if not ours and not force:
            return Result(
                skill, target, Outcome.BLOCKED,
                "a directory is already here and was not installed by wow-tools; --force replaces it",
            )

    if dry_run:
        return Result(skill, target, Outcome.PLANNED, f"would {method.value}")

    directory.mkdir(parents=True, exist_ok=True)
    existed = target.is_symlink() or target.exists()
    _remove(target)

    if method is Method.SYMLINK:
        os.symlink(skill.path, target, target_is_directory=True)
        return Result(skill, target, Outcome.UPDATED if existed else Outcome.LINKED)

    # evals/ holds skill-creator test fixtures, which are development material
    # for this repo rather than anything the skill reads at run time.
    shutil.copytree(
        skill.path, target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "evals"),
    )
    # Marks the copy as ours so a later run may refresh it without --force,
    # and so uninstall can tell it apart from a hand-made directory.
    (target / ".wow-tools-install").write_text(
        f"copied from {skill.path}\n", encoding="utf-8"
    )
    return Result(skill, target, Outcome.UPDATED if existed else Outcome.COPIED)


def uninstall_skill(skill: Skill, directory: Path, *, dry_run: bool = False) -> Result:
    target = directory / skill.name
    if not target.is_symlink() and not target.exists():
        return Result(skill, target, Outcome.ABSENT)

    if target.is_symlink():
        if not _is_our_link(target, skill.path):
            return Result(
                skill, target, Outcome.BLOCKED,
                "symlink points somewhere else; left alone",
            )
    elif not (target / ".wow-tools-install").is_file():
        return Result(
            skill, target, Outcome.BLOCKED,
            "directory was not installed by wow-tools; left alone",
        )

    if dry_run:
        return Result(skill, target, Outcome.PLANNED, "would remove")
    _remove(target)
    return Result(skill, target, Outcome.REMOVED)


def _remove(target: Path) -> None:
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
