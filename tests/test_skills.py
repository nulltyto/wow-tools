"""The skills in this repo are valid, self-consistent, and runnable.

These are hookup tests rather than unit tests: they check the things that make
a skill actually load and work in a harness, which is where this repo's real
failure modes are. A broken regex inside an extractor is caught by that skill's
own validate_index.py; a SKILL.md that names a script that no longer exists is
caught by nothing else.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wow_tools import skills as skills_mod  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SKILL_DIRS = sorted(p for p in (REPO / "skills").iterdir() if p.is_dir())


def test_at_least_one_skill():
    found, _ = skills_mod.discover()
    assert found, "no skills discovered under skills/"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_matches_spec(skill_dir: Path):
    """Frontmatter satisfies agentskills.io, not just one harness's parser."""
    skill, problems = skills_mod.validate(skill_dir)
    assert not problems, "\n".join(problems)
    assert skill is not None


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_scripts_named_in_skill_md_exist(skill_dir: Path):
    """Every scripts/<file> the SKILL.md tells the agent to run is really there.

    A skill whose instructions point at a renamed script fails only at the
    moment an agent tries to follow them, which is the worst time to find out.
    """
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    referenced = set(re.findall(r"scripts/([A-Za-z0-9_.-]+\.py)", text))
    missing = [s for s in sorted(referenced) if not (skill_dir / "scripts" / s).is_file()]
    assert not missing, f"SKILL.md references missing scripts: {missing}"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_scripts_import_and_expose_help(skill_dir: Path):
    """Each script is importable and answers --help under the supported floor.

    Catches the syntax error, the bad import, and the argparse mistake without
    needing a WoW install or an addon checkout present.
    """
    scripts = sorted((skill_dir / "scripts").glob("*.py")) if (skill_dir / "scripts").is_dir() else []
    if not scripts:
        pytest.skip("no scripts")
    for script in scripts:
        proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, f"{script.name} --help failed:\n{proc.stderr}"
        assert proc.stdout.strip(), f"{script.name} --help printed nothing"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_body_is_ascii(skill_dir: Path):
    """Non-ASCII in a SKILL.md is allowed; a mojibake replacement char is not."""
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "�" not in text, "SKILL.md contains U+FFFD -- the text is already corrupted"


def test_readme_lists_every_skill():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    found, _ = skills_mod.discover()
    missing = [s.name for s in found if s.name not in readme]
    assert not missing, f"skills absent from the repo README: {missing}"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_has_its_own_readme(skill_dir: Path):
    assert (skill_dir / "README.md").is_file(), "each skill documents itself for humans"


def test_bundled_api_index_is_internally_consistent():
    """The committed index's header totals match the entries it carries.

    wow-api-search ships its index rather than building it, so nothing else
    would notice a truncated or half-regenerated file until a lookup came back
    empty. Unqualified names that collide across namespaces are stored as a
    list of entries under one key, so this counts entries rather than keys.
    """
    import json

    index = json.loads(
        (REPO / "skills" / "wow-api-search" / "references" / "api_index.json").read_text(
            encoding="utf-8"
        )
    )
    for section in ("functions", "events", "tables", "predicates"):
        entries = sum(
            len(v) if isinstance(v, list) else 1 for v in index[section].values()
        )
        assert entries == index[f"total_{section}"], (
            f"{section}: header says {index[f'total_{section}']}, found {entries}"
        )
