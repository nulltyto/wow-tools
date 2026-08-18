"""Discover and validate the skills in this repository.

Validation here is against the Agent Skills specification (agentskills.io), not
against any one harness, because the whole point of installing to a shared
directory is that a dozen harnesses read the same file. A skill that only
satisfies Claude Code's parser is a skill that silently fails somewhere else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)
# Deliberately not a YAML parser: the spec's required fields are two scalars,
# and depending on PyYAML would give this repo a runtime dependency it does not
# otherwise need. Anything more exotic than `key: value` is reported rather
# than guessed at.
_SCALAR = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)$")

# From the spec: 1-64 chars, lowercase alphanumeric and hyphens, no leading or
# trailing hyphen, no consecutive hyphens.
_NAME_RE = re.compile(r"\A(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)\Z")
_DESC_MAX = 1024


@dataclass(frozen=True)
class Skill:
    path: Path
    name: str
    description: str

    @property
    def slug(self) -> str:
        return self.path.name

    @property
    def signature(self) -> str:
        """The file whose presence marks an installed copy as a skill."""
        return "SKILL.md"

    def summary(self, width: int = 68) -> str:
        """First sentence of the description, for the selection menu."""
        first = self.description.split(". ")[0].rstrip(".")
        return first if len(first) <= width else first[: width - 1].rstrip() + "…"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str | None]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, "no YAML frontmatter"
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in " \t":
            continue  # a nested value under the previous key; not a field we read
        sm = _SCALAR.match(line)
        if sm:
            value = sm.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            fields[sm.group(1)] = value
    return fields, None


def validate(skill_dir: Path) -> tuple[Skill | None, list[str]]:
    """Return the parsed skill, plus every spec violation found."""
    problems: list[str] = []
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return None, [f"{skill_dir.name}: no SKILL.md"]

    fields, err = _parse_frontmatter(md.read_text(encoding="utf-8"))
    if err:
        return None, [f"{skill_dir.name}: {err}"]

    name = fields.get("name", "")
    desc = fields.get("description", "")

    if not name:
        problems.append(f"{skill_dir.name}: frontmatter has no `name`")
    else:
        if not _NAME_RE.match(name):
            problems.append(
                f"{skill_dir.name}: name {name!r} breaks the spec "
                "(1-64 chars, lowercase a-z 0-9 and single hyphens, no leading/trailing hyphen)"
            )
        if name != skill_dir.name:
            problems.append(
                f"{skill_dir.name}: name {name!r} does not match its directory, "
                "which the spec requires"
            )
    if not desc:
        problems.append(f"{skill_dir.name}: frontmatter has no `description`")
    elif len(desc) > _DESC_MAX:
        problems.append(
            f"{skill_dir.name}: description is {len(desc)} chars, over the spec's {_DESC_MAX}"
        )

    if not name or not desc:
        return None, problems
    return Skill(path=skill_dir, name=name, description=desc), problems


def discover(root: Path | None = None) -> tuple[list[Skill], list[str]]:
    """Every valid skill under skills/, plus problems found along the way."""
    base = root if root is not None else SKILLS_DIR
    found: list[Skill] = []
    problems: list[str] = []
    if not base.is_dir():
        return found, [f"no skills directory at {base}"]
    for child in sorted(p for p in base.iterdir() if p.is_dir()):
        if child.name.startswith("."):
            continue
        skill, errs = validate(child)
        problems.extend(errs)
        if skill is not None:
            found.append(skill)
    return found, problems
