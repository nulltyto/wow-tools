"""Discover and validate the World of Warcraft addons in this repository.

The equivalent of `skills.py`, for the other kind of thing this repo installs.
The checks are the ones the game client answers with silence: a `.toc` whose
name does not match its folder is never loaded at all, and a file listed in a
`.toc` that is not on disk is skipped without a word, which shows up much later
as one function mysteriously not existing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDONS_DIR = REPO_ROOT / "addons"

_DIRECTIVE = re.compile(r"^##\s*([^:]+?)\s*:\s*(.*)$")

# Blizzard ships one .toc per game flavor, either as suffixed files
# (Foo_Mainline.toc, Foo_Vanilla.toc) or as a single unsuffixed one.
_FLAVOR_SUFFIXES = (
    "", "_Mainline", "_Standard", "_Vanilla", "_Classic", "_TBC", "_Wrath",
    "_Cata", "_Mists", "_Legion",
)


@dataclass(frozen=True)
class Addon:
    path: Path
    name: str
    title: str
    interface: str
    saved_variables: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    files: tuple[str, ...] = field(default=(), repr=False)

    @property
    def signature(self) -> str:
        """The file whose presence marks an installed copy as this addon."""
        return f"{self.name}.toc"

    def summary(self, width: int = 68) -> str:
        # Titles carry WoW's inline color escapes (|cff0cd29f...|r), which are
        # noise in a terminal menu.
        plain = re.sub(r"\|c[0-9a-fA-F]{8}|\|r", "", self.title).strip()
        return plain if len(plain) <= width else plain[: width - 1].rstrip() + "…"


def _toc_paths(addon_dir: Path) -> list[Path]:
    return [
        p for p in (addon_dir / f"{addon_dir.name}{s}.toc" for s in _FLAVOR_SUFFIXES)
        if p.is_file()
    ]


def _parse_toc(text: str) -> tuple[dict[str, str], list[str]]:
    directives: dict[str, str] = {}
    files: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("##"):
            m = _DIRECTIVE.match(line)
            if m:
                directives[m.group(1).strip().lower()] = m.group(2).strip()
        elif not line.startswith("#"):
            files.append(line)
    return directives, files


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in re.split(r"[,\s]+", value) if p.strip())


def validate(addon_dir: Path) -> tuple[Addon | None, list[str]]:
    """Return the parsed addon, plus everything that would make the client skip it."""
    problems: list[str] = []
    tocs = _toc_paths(addon_dir)
    if not tocs:
        # Worth naming the rule rather than the symptom: a folder called Foo
        # needs Foo.toc, and renaming the folder alone is the usual cause.
        return None, [
            f"{addon_dir.name}: no {addon_dir.name}.toc "
            "(the client requires the .toc to be named after its folder)"
        ]

    directives, files = _parse_toc(tocs[0].read_text(encoding="utf-8", errors="replace"))

    interface = directives.get("interface", "")
    title = directives.get("title", "") or addon_dir.name
    if not interface:
        problems.append(f"{addon_dir.name}: {tocs[0].name} has no ## Interface:")

    for rel in files:
        # Windows-style separators are legal in a .toc and common in the wild.
        target = addon_dir.joinpath(*rel.replace("\\", "/").split("/"))
        if not target.is_file():
            problems.append(
                f"{addon_dir.name}: {tocs[0].name} lists {rel}, which is not in the folder "
                "(the client skips missing files silently)"
            )

    addon = Addon(
        path=addon_dir,
        name=addon_dir.name,
        title=title,
        interface=interface,
        saved_variables=_split_list(directives.get("savedvariables", "")),
        dependencies=_split_list(
            directives.get("dependencies", "") or directives.get("requireddeps", "")
        ),
        files=tuple(files),
    )
    return addon, problems


def discover(root: Path | None = None) -> tuple[list[Addon], list[str]]:
    """Every valid addon under addons/, plus problems found along the way."""
    base = root if root is not None else ADDONS_DIR
    found: list[Addon] = []
    problems: list[str] = []
    if not base.is_dir():
        return found, []
    for child in sorted(p for p in base.iterdir() if p.is_dir()):
        if child.name.startswith("."):
            continue
        addon, errs = validate(child)
        problems.extend(errs)
        if addon is not None:
            found.append(addon)
    return found, problems
