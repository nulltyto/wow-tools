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

    index = _bundled_index()
    for section in ("functions", "events", "tables", "predicates"):
        entries = sum(
            len(v) if isinstance(v, list) else 1 for v in index[section].values()
        )
        assert entries == index[f"total_{section}"], (
            f"{section}: header says {index[f'total_{section}']}, found {entries}"
        )


def _bundled_index():
    import json

    return json.loads(
        (REPO / "skills" / "wow-api-search" / "references" / "api_index.json").read_text(
            encoding="utf-8"
        )
    )


def _first(entry):
    """Collided names are stored as a list; take the single or the first."""
    return entry[0] if isinstance(entry, list) else entry


def test_constants_tables_carry_their_values():
    """A Constants table is only useful if its members came through.

    Constants write their members under `Values`, not `Fields`, so a parser
    that reads Fields alone indexes all 55 of them as empty shells and every
    lookup for a named constant comes back with nothing. That failed silently
    for four builder versions -- the entry existed, so nothing looked wrong.
    """
    tables = _bundled_index()["tables"]
    consts = {k: _first(v) for k, v in tables.items() if _first(v).get("type") == "Constants"}
    assert consts, "no Constants tables in the index at all"
    empty = sorted(k for k, v in consts.items() if not v.get("fields"))
    assert not empty, f"Constants tables indexed with no members: {empty}"

    # The one that made the gap visible: Blizzard's own CooldownViewer compares
    # SPELL_UPDATE_COOLDOWN's startRecoveryCategory against it to tell a global
    # cooldown apart from a spell cooldown.
    gcd = consts["SpellCooldownConsts"]
    assert gcd["fields"][0] == {
        "name": "GLOBAL_RECOVERY_CATEGORY", "type": "number", "value": 133,
    }


def test_table_systems_are_not_a_sibling_table_name():
    """A table's `system` names its system, never the table above it.

    207 of the 592 export files declare no system-level Name. An unanchored
    search there fell through to the first table's Name and stamped it on
    every entry in the file, so SpellCooldownConsts reported its system as
    ConfirmationPromptUIType -- a wrong answer that reads like a real one.
    """
    tables = _bundled_index()["tables"]
    by_file = {}
    for name, entry in tables.items():
        by_file.setdefault(_first(entry)["file"], []).append((name, _first(entry)["system"]))

    wrong = [
        (f, name, system)
        for f, entries in by_file.items()
        for name, system in entries
        if system in {n for n, _ in entries}
    ]
    assert not wrong, f"tables whose system names a sibling table: {wrong[:5]}"


def _eui_builder():
    """The EllesmereUI builder, loaded from its script path.

    The index it produces is a gitignored build artifact, so these tests drive
    the extractor over fabricated sources instead of a checkout.
    """
    import importlib.util

    path = REPO / "skills" / "ellesmereui-search" / "scripts" / "build_index.py"
    spec = importlib.util.spec_from_file_location("eui_build_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _callers(files: dict[str, str], modules=("ModA", "ModB")):
    """Run symbol extraction and caller resolution over in-memory sources."""
    B = _eui_builder()
    sources = [B.Source(rel, text) for rel, text in files.items()]
    symbols = []
    for src in sources:
        symbols.extend(B.extract_symbols(src, B.module_of(src.rel, list(modules))))
    B.collect_callers(sources, symbols, list(modules))
    return {(r["file"], r["full"]): r for r in symbols}


def test_callers_follow_the_receiver_not_the_bare_name():
    """A call is credited only when the receiver matches the definition.

    EllesmereUI defines names Blizzard's frame API also uses, so matching the
    bare name reported 6836 callers of `SetPoint`, nearly every one of them a
    Blizzard frame the addon merely positions.
    """
    rows = _callers({
        "ModA/a.lua": (
            "function Skin:SetPoint(x)\n"
            "end\n"
            "Skin:SetPoint(1)\n"
            "someFrame:SetPoint('TOP')\n"
            "local f = CreateFrame('Frame')\n"
            "f:SetPoint('LEFT')\n"
        ),
    })
    row = rows[("ModA/a.lua", "Skin:SetPoint")]
    assert row["callers"] == ["ModA/a.lua:3"], row["callers"]
    assert row["caller_count"] == 1


def test_a_call_through_an_expression_is_not_a_local_call():
    """`GetFFD(frame).refresh()` calls a table field, not a same-named local.

    A receiver that is itself a call does not match the identifier pattern, so
    the call arrives looking unqualified. Crediting it to the local produced a
    confident, wrong edge -- the index validator is what caught it.
    """
    rows = _callers({
        "ModA/a.lua": (
            "local function refresh()\n"
            "end\n"
            "refresh()\n"
            "if GetFFD(frame).refresh then GetFFD(frame).refresh() end\n"
        ),
    })
    row = rows[("ModA/a.lua", "refresh")]
    assert row["callers"] == ["ModA/a.lua:3"], row["callers"]


def test_module_local_tables_do_not_share_one_namespace():
    """Each addon folder declares its own `ns`, so `ns.Foo` is per module."""
    rows = _callers({
        "ModA/a.lua": "local _, ns = ...\nfunction ns.Refresh()\nend\nns.Refresh()\n",
        "ModB/b.lua": "local _, ns = ...\nfunction ns.Refresh()\nend\nns.Refresh()\n",
    })
    a = rows[("ModA/a.lua", "ns.Refresh")]
    b = rows[("ModB/b.lua", "ns.Refresh")]
    assert "caller_ambiguity" not in a and "caller_ambiguity" not in b
    assert a["callers"] == ["ModA/a.lua:4"]
    assert b["callers"] == ["ModB/b.lua:4"]


def test_indistinguishable_definitions_state_that_instead_of_guessing():
    """Ambiguity is reported, never averaged into a list.

    The options files declare 1402 callbacks named `getValue`. A list spread
    over all of them reads like an answer while being noise, so those rows
    carry the count of competing definitions and no sites. Every row states
    exactly one of the two.
    """
    rows = _callers({
        "ModA/a.lua": "getValue = function(info)\nend\n",
        "ModA/b.lua": "getValue = function(info)\nend\ngetValue(1)\n",
    })
    for row in rows.values():
        assert ("callers" in row) != ("caller_ambiguity" in row), row
    assert rows[("ModA/a.lua", "getValue")]["caller_ambiguity"] == 2
