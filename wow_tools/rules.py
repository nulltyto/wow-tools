"""Discover and validate the always-on rules in this repository.

The third kind of thing this repo installs, and the one with the least
standardisation behind it. Skills have a specification and a shared directory:
`~/.agents/skills` serves most of the registry with one symlink. Rules have
neither. AGENTS.md is defined only at a repository root, so it says nothing
about a user-scope location, and every harness that has one invented its own
path, its own file extension, and its own frontmatter key for "always load
this".

The consequence for this module is that a rule is a single *file* rather than a
directory, and that the same file is installed under different names depending
on who is reading it. `registry.Harness.rules_ext` carries the extension.

Frontmatter here is the union of what the readers want, because the readers
ignore keys they do not know: `inclusion` is Kiro's, `alwaysApply` is Cursor's,
and Claude Code loads any rule that has no `paths` field. One file satisfies
all three rather than three files drifting apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = REPO_ROOT / "rules"

# The comment that marks a file as ours. Placed in the rule rather than in a
# sidecar because a rule installs as one file with nothing next to it, and
# because Claude Code strips block-level HTML comments before loading a rule --
# so the marker costs nothing in the context window it lands in.
MARKER = "<!-- wow-tools-rule: {name} -->"

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)
_SCALAR = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)$")
_NAME_RE = re.compile(r"\A(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)\Z")


@dataclass(frozen=True)
class Rule:
    path: Path
    name: str
    description: str

    def marker(self) -> str:
        return MARKER.format(name=self.name)

    def filename(self, ext: str = ".md") -> str:
        return f"{self.name}{ext}"

    def summary(self, width: int = 68) -> str:
        first = self.description.split(". ")[0].rstrip(".")
        return first if len(first) <= width else first[: width - 1].rstrip() + "..."


def _fields(text: str) -> dict:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line[:1] in " \t":
            continue
        sm = _SCALAR.match(line)
        if sm:
            value = sm.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out[sm.group(1)] = value
    return out


def validate(path: Path) -> tuple:
    """Return the parsed rule, plus every problem found."""
    problems = []
    text = path.read_text(encoding="utf-8")
    fields = _fields(text)

    name = fields.get("name", "")
    desc = fields.get("description", "")
    stem = path.stem

    if not name:
        problems.append(f"{path.name}: frontmatter has no `name`")
    else:
        if not _NAME_RE.match(name):
            problems.append(
                f"{path.name}: name {name!r} is not a usable filename stem "
                "(lowercase a-z 0-9 and single hyphens)"
            )
        if name != stem:
            problems.append(f"{path.name}: name {name!r} does not match the file name")
    if not desc:
        problems.append(f"{path.name}: frontmatter has no `description`")

    # A rule that installs without its marker cannot be told from a file the
    # user wrote by hand, which would make uninstall unsafe.
    if name and MARKER.format(name=name) not in text:
        problems.append(f"{path.name}: missing its ownership marker, {MARKER.format(name=name)}")

    if not name or not desc:
        return None, problems
    return Rule(path=path, name=name, description=desc), problems


def discover(root: Path = None) -> tuple:
    """Every valid rule under rules/, plus problems found along the way."""
    base = root if root is not None else RULES_DIR
    found = []
    problems = []
    if not base.is_dir():
        return found, []
    for child in sorted(base.glob("*.md")):
        if child.name.startswith("."):
            continue
        rule, errs = validate(child)
        problems.extend(errs)
        if rule is not None:
            found.append(rule)
    return found, problems
