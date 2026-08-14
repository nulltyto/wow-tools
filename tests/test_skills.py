"""The skills in this repo are valid, self-consistent, and runnable.

These are hookup tests rather than unit tests: they check the things that make
a skill actually load and work in a harness, which is where this repo's real
failure modes are. A broken regex inside an extractor is caught by that skill's
own validate_index.py; a SKILL.md that names a script that no longer exists is
caught by nothing else.
"""

from __future__ import annotations

import argparse
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

    A reference may name another skill -- `<ellesmereui-search>/scripts/x.py`,
    the routing the orchestrator skills are made of -- and that is resolved
    against the skill it names. Routing is the whole value of those skills, so
    a script that moves out from under one breaks it completely while leaving
    every other check green.
    """
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    known = {p.name for p in SKILL_DIRS}
    missing = []
    for prefix, script in re.findall(r"<([a-z0-9-]+)>/scripts/([A-Za-z0-9_.-]+\.py)", text):
        if prefix in ("skill", skill_dir.name):
            owner = skill_dir
        elif prefix in known:
            owner = skill_dir.parent / prefix
        else:
            missing.append(f"{prefix} (no such skill)")
            continue
        if not (owner / "scripts" / script).is_file():
            missing.append(f"{prefix}/scripts/{script}")

    unqualified = re.findall(r"(?<![>/\w])scripts/([A-Za-z0-9_.-]+\.py)", text)
    missing += [f"scripts/{s}" for s in sorted(set(unqualified))
                if not (skill_dir / "scripts" / s).is_file()]
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

    index = _bundled_index()
    for section in ("functions", "events", "tables", "predicates"):
        entries = sum(
            len(v) if isinstance(v, list) else 1 for v in index[section].values()
        )
        assert entries == index[f"total_{section}"], (
            f"{section}: header says {index[f'total_{section}']}, found {entries}"
        )


def test_bundled_api_index_carries_no_blizzard_prose():
    """The committed index ships interface facts, never Blizzard's writing.

    Signatures, payloads and enum members are facts about an interface and are
    this repository's to publish. The `Documentation` notes in the export are
    Blizzard's own text, and nothing here licenses republishing them, so the
    committed index omits them and a local `--with-docs` build keeps them.
    The default output path of that build is the committed file's neighbour
    rather than the committed file, but a stray `--force`, a hand edit or a
    future builder change would put the prose back with nothing to notice --
    which is what this test is for.
    """

    index = _bundled_index()
    assert index.get("documentation") == "omitted", (
        "the committed index must declare that it carries no prose notes"
    )

    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "documentation":
                    found.append(path)
                else:
                    walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    for section in ("functions", "events", "tables", "predicates", "systems"):
        walk(index[section], section)

    assert not found, f"Blizzard's prose notes are in the committed index: {found[:5]}"
    assert index["total_documented"] == 0, index["total_documented"]


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


def _settings(source: str, rel="ModA/a.lua", module="ModA"):
    """Run settings extraction over one in-memory source."""
    B = _eui_builder()
    rows = B.extract_settings(B.Source(rel, source), module)
    return {r["path"]: r for r in rows}


def test_a_defaults_branch_filled_in_a_loop_is_still_indexed():
    """Per-entry settings are declared once and shared by every entry.

    EllesmereUI builds its per-bar namespace as one literal inside a `for`,
    assigned through a runtime subscript. Matching only whole `defaults = {`
    literals lost all ~89 of those keys -- including `alwaysShowButtons`, the
    subject of a bug report -- while the index still reported 3,876 settings
    and looked healthy. The runtime key becomes `[]`, as it does for any other
    table keyed at runtime.
    """
    rows = _settings(
        "local defaults = { profile = { scale = 1 } }\n"
        "for _, info in ipairs(BAR_CONFIG) do\n"
        "    defaults.profile.bars[info.key] = {\n"
        "        alwaysShowButtons = true,\n"
        "        clickThrough = false,\n"
        "    }\n"
        "end\n"
    )
    assert "bars.[].alwaysShowButtons" in rows
    assert rows["bars.[].alwaysShowButtons"]["key"] == "alwaysShowButtons"
    assert rows["bars.[].alwaysShowButtons"]["default"] == "true"
    assert rows["bars.[].alwaysShowButtons"]["line"] == 4
    assert "scale" in rows, "the plain form must keep working"


def test_a_key_whose_value_is_a_positional_table_is_the_leaf():
    """Descending finds no named key below it, so the key itself is recorded.

    `gold = { 0.886, 0.675, 0.478 }` is a declared default with a real value.
    Walking into it looking for named leaves finds none and drops the key.
    """
    rows = _settings(
        "local ICON_DEFAULTS = {\n"
        "    gold = { 0.886, 0.675, 0.478 },\n"
        "    paging = {},\n"
        "}\n"
    )
    assert rows["gold"]["default"] == "{ 0.886, 0.675, 0.478 }"
    assert rows["paging"]["default"] == "{}"


def test_an_array_of_named_tables_is_walked_not_collapsed():
    """A table naming nothing at its own level can still name keys below it.

    `bars = { { key = "cooldowns" } }` is the shape CooldownManager uses for
    its default bars. Treating "no named key at this level" as "no leaf below"
    collapsed 153 real records into one.
    """
    rows = _settings(
        "local DEFAULTS = {\n"
        "    bars = {\n"
        '        { key = "cooldowns", iconSize = 42 },\n'
        "    },\n"
        "}\n"
    )
    assert rows["bars.[].key"]["default"] == '"cooldowns"'
    assert rows["bars.[].iconSize"]["default"] == "42"


def test_a_colour_is_one_setting_not_three_channels():
    """`{ r = .., g = .., b = .. }` is the value, not three settings.

    Walking into a colour records leaves keyed `r`, `g` and `b`: the colour's
    own name answers nothing, and each leaf inherits the references of a
    one-letter identifier. That was 787 records -- 19% of the index -- all
    unfindable and all carrying the wrong `refs`, and it is why a bug report
    about cast bar colours had to fall back to grepping the tree.
    """
    rows = _settings(
        "local defaults = {\n"
        "    castbarFillColor = { r = 0.863, g = 0.820, b = 0.639 },\n"
        "    borderColor = { r = 0, g = 0, b = 0, a = 0.5 },\n"
        "    frame = { size = 24 },\n"
        "}\n"
    )
    assert rows["castbarFillColor"]["default"] == "{ r = 0.863, g = 0.820, b = 0.639 }"
    assert rows["borderColor"]["default"] == "{ r = 0, g = 0, b = 0, a = 0.5 }"
    assert not any(r["key"] in ("r", "g", "b", "a") for r in rows.values()), rows
    assert "frame.size" in rows, "an ordinary nested table must still be walked"


def test_a_table_of_short_keys_that_is_not_a_colour_is_still_walked():
    """The collapse is for colours, not for every table with short keys."""
    rows = _settings(
        "local defaults = {\n"
        "    offset = { x = 4, y = -2 },\n"
        "    tint = { r = 1, g = 1 },\n"
        "}\n"
    )
    assert "offset.x" in rows and "offset.y" in rows
    assert "tint.r" in rows, "r and g without b is not a colour"


def test_a_local_bound_onto_a_shared_table_is_called_through_that_name():
    """Every cross-module helper here is a local exported onto a shared table.

    Resolving only the definition's own name credits its file's calls and
    silently drops the rest of the suite -- `BuildColorSwatch` read 11 callers
    against a true 301, and `ComputeCastBarTint` read zero while a session was
    about to change its signature. The row states the second name so the count
    can be checked rather than merely believed.
    """
    rows = _callers({
        "shared.lua": (
            "local EllesmereUI = _G.EllesmereUI\n"
            "local function ComputeTint(a, b)\n"
            "end\n"
            "EllesmereUI.ComputeTint = ComputeTint\n"
        ),
        "ModA/a.lua": (
            "local EllesmereUI = _G.EllesmereUI\n"
            "EllesmereUI.ComputeTint(1, 2)\n"
        ),
        "ModB/b.lua": (
            "local EllesmereUI = _G.EllesmereUI\n"
            "local ComputeTint = EllesmereUI.ComputeTint\n"
            "ComputeTint(3, 4)\n"
        ),
    })
    row = rows[("shared.lua", "ComputeTint")]
    assert row["aliases"] == ["ComputeTint", "EllesmereUI.ComputeTint"], row["aliases"]
    # ModA calls it qualified; ModB binds it back to a local and calls it bare.
    assert row["callers"] == ["ModA/a.lua:2", "ModB/b.lua:3"], row["callers"]


def test_an_export_two_definitions_claim_is_not_credited_to_either():
    """A contested alias gives no edge. A wrong caller is worse than a gap."""
    rows = _callers({
        "ModA/a.lua": (
            "local EllesmereUI = _G.EllesmereUI\n"
            "local function Refresh()\n"
            "end\n"
            "EllesmereUI.Refresh = Refresh\n"
        ),
        "ModB/b.lua": (
            "local EllesmereUI = _G.EllesmereUI\n"
            "local function Refresh()\n"
            "end\n"
            "EllesmereUI.Refresh = Refresh\n"
            "EllesmereUI.Refresh()\n"
        ),
    })
    for rel in ("ModA/a.lua", "ModB/b.lua"):
        row = rows[(rel, "Refresh")]
        assert "aliases" not in row, row
        assert row["callers"] == [], row["callers"]


def test_an_addon_private_table_does_not_export_across_modules():
    """`ns` is per addon, so `ns.Foo` in another module is another function."""
    rows = _callers({
        "ModA/a.lua": (
            "local _, ns = ...\n"
            "local function Refresh()\n"
            "end\n"
            "ns.Refresh = Refresh\n"
            "ns.Refresh()\n"
        ),
        "ModB/b.lua": "local _, ns = ...\nns.Refresh()\n",
    })
    row = rows[("ModA/a.lua", "Refresh")]
    assert row["callers"] == ["ModA/a.lua:5"], row["callers"]


# --- `Name = function()` is three different things ---------------------------
#
# A bare assignment is a table field inside a constructor, the body of a
# forward-declared local outside one, and a global only when it is neither.
# Calling all three `global` was wrong for 7,234 of the 7,265 rows that claimed
# it, and the caller pass reads `kind` to decide whether a bare `Name(`
# anywhere in the tree is a call to this definition.


def test_a_handler_in_a_table_constructor_is_not_a_global():
    """It is reached through the table, whose name the definition site lacks.

    Claiming the bare `Name(` calls elsewhere in the tree would be a false
    edge, so the row says so instead of guessing.
    """
    rows = _callers({
        "ModA/a.lua": (
            "local handlers = {\n"
            "    Refresh = function() end,\n"
            "}\n"
        ),
        "ModB/b.lua": "Refresh()\n",
    })
    row = rows[("ModA/a.lua", "Refresh")]
    assert row["kind"] == "tablefield"
    assert row["caller_unresolved"] == "table field"
    assert "callers" not in row and "caller_count" not in row


def test_a_forward_declared_local_is_not_a_global_at_any_indent():
    """The big options files declare inside a block and fill the body far below.

    `local BuildBossOptions` sits indented in a builder, and the body lands
    thousands of lines later as a bare assignment. Read as a global, that
    definition collects bare calls from the whole tree; read as a local, it
    collects its own file's, which is where they are. 50 of the 120 rows that
    claimed `global` were this, and two of them took cross-file call sites
    belonging to an unrelated function of the same name.
    """
    rows = _callers({
        "ModA/a.lua": (
            "do\n"
            "    local BuildBossOptions\n"
            "    BuildBossOptions = function(y) end\n"
            "    BuildBossOptions(1)\n"
            "end\n"
        ),
        "ModB/b.lua": "BuildBossOptions(2)\n",
    })
    row = rows[("ModA/a.lua", "BuildBossOptions")]
    assert row["kind"] == "local", "an indented declaration still declares a local"
    assert row["callers"] == ["ModA/a.lua:4"], row["callers"]


def test_a_constructor_key_beats_a_local_of_the_same_name():
    """Position decides before the name does, and only that order is safe.

    A file that forward-declares `local Refresh` and also writes `Refresh =`
    as a key inside a table is writing two unrelated things. Letting the
    declaration win hands the local's call sites to a function nothing calls
    by that bare name -- 334 rows in this tree.
    """
    rows = _callers({
        "ModA/a.lua": (
            "local Refresh\n"
            "local handlers = {\n"
            "    Refresh = function() end,\n"
            "}\n"
            "Refresh()\n"
        ),
    })
    row = rows[("ModA/a.lua", "Refresh")]
    assert row["kind"] == "tablefield", "a key in a constructor is a field of that table"


def test_a_real_global_is_still_a_global():
    """The denial rules must not swallow the class they are narrowing.

    22 of these survive in the tree -- the macro-callable entry points, named
    `EllesmereUI_StartPartyMode` and the like -- and they are the reason the
    field is worth filtering on at all.
    """
    rows = _callers({
        "ModA/a.lua": "EllesmereUI_StartPartyMode = function() end\n",
        "ModB/b.lua": "EllesmereUI_StartPartyMode()\n",
    })
    row = rows[("ModA/a.lua", "EllesmereUI_StartPartyMode")]
    assert row["kind"] == "global"
    assert row["callers"] == ["ModB/b.lua:1"], "a global is callable from anywhere"


def test_a_dotted_directory_is_not_indexed(tmp_path):
    """Offline tooling is not shipped code, and it costs real caller lists.

    Three files under `.tools/` contributed 40 symbols and pushed 14 real
    EllesmereUIQuickdraw functions to `caller_ambiguity` with no list at all,
    because an offline helper happened to reuse their names.
    """
    B = _eui_builder()
    for name in (".tools", ".git", ".release", ".github", "Libs", "media"):
        assert B.skip_dir(name), name
    assert not B.skip_dir("EllesmereUIQuickdraw")

    root = tmp_path / "addons"
    (root / ".tools").mkdir(parents=True)
    (root / "ModA").mkdir()
    (root / ".tools" / "helper.lua").write_text("local function Refresh() end\n")
    (root / "ModA" / "a.lua").write_text("local function Refresh() end\n")
    found = {rel for rel, _ in B.iter_lua(root)}
    assert found == {"ModA/a.lua"}, found


def test_prose_that_names_a_setting_key_is_not_a_reference_to_it():
    """A comment is blanked whole, so its offsets look like a blanked string.

    `-- changedAxis: "width", "height"` read as a reference to the `width`
    setting. A real string literal keeps its opening quote, because masking
    starts one byte after it -- which is what tells the two apart.
    """
    B = _eui_builder()
    src = B.Source("ModA/a.lua", (
        '-- changedAxis: "width", "height"\n'
        'local v = p["width"]\n'
    ))
    refs = B.collect_refs([src], {"width", "height"}, ["ModA"])
    assert "height" not in refs, "the comment must not count as a reference"
    assert sorted(s for _, s in refs["width"]) == ["ModA/a.lua:2"]


# --- Options pages are in a different addon from the settings they build -----


def test_an_options_file_is_attributed_to_the_module_it_configures():
    """The file name is the only link between a page and its module."""
    B = _eui_builder()
    mods = ["EllesmereUIQuickdraw", "EllesmereUIRaidFrames", "EllesmereUIQoL",
            "EllesmereUIDataBars", "EllesmereUIOptions"]
    page = lambda rel: B.options_page_module(rel, mods)  # noqa: E731

    assert page("EllesmereUIOptions/EUI_Quickdraw_Options.lua") == "EllesmereUIQuickdraw"
    assert page("EllesmereUIOptions/EUI_RaidFrames_ManagerPages.lua") == "EllesmereUIRaidFrames"
    assert page("EllesmereUIOptions/EUI_QoL_RaidTools_Options.lua") == "EllesmereUIQoL"
    assert page("EllesmereUIOptions/EllesmereUIDataBars_Options.lua") == "EllesmereUIDataBars"
    assert page("EllesmereUIOptions/EUI__General_Options.lua") == "EllesmereUI"

    # The shared widget library configures nothing, and a module's own file is
    # already attributed by its folder.
    assert page("EllesmereUIOptions/EllesmereUI_Widgets.lua") is None
    assert page("EllesmereUIQuickdraw/EllesmereUIQuickdraw.lua") is None
    assert page("EllesmereUIOptions/EUI_Unknown_Options.lua") is None


def _build_tree(tmp_path, files: dict[str, str]):
    """Run the real builder over a fabricated checkout, into a temp index."""
    B = _eui_builder()
    root = tmp_path / "addons"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    out = tmp_path / "index"
    out.mkdir()
    B.INDEX_DIR = out
    fp, n_files, n_bytes = B.fingerprint(root)
    B.build(root, fp, n_files, n_bytes)
    import json as _json
    return [_json.loads(line) for line in (out / "settings.jsonl").read_text().splitlines() if line]


_SUITE_TOC = "## Title: EllesmereUI\n## SavedVariables: EllesmereUIDB\nEllesmereUI.lua\n"


def test_a_settings_key_finds_its_control_in_the_options_addon(tmp_path):
    """`options_refs` is the answer to "where is this setting's control built".

    Every options page lives in the separate EllesmereUIOptions addon, so
    attributing a reference by its folder put the page in a different module
    from the key it reads and dropped it. The field then answered zero for
    every child module in the suite -- 92% of all settings -- while reading
    like a finding.
    """
    rows = _build_tree(tmp_path, {
        "EllesmereUI.toc": _SUITE_TOC,
        "EllesmereUI.lua": "local EllesmereUI = {}\n",
        "EllesmereUIQuickdraw/EllesmereUIQuickdraw.toc": "## Title: Q\nEllesmereUIQuickdraw.lua\n",
        "EllesmereUIQuickdraw/EllesmereUIQuickdraw.lua":
            "local DB_DEFAULTS = { profile = { hideUnusable = true } }\n"
            "local function Use() return p.hideUnusable end\n",
        "EllesmereUIOptions/EllesmereUIOptions.toc": "## Title: O\nEUI_Quickdraw_Options.lua\n",
        "EllesmereUIOptions/EUI_Quickdraw_Options.lua":
            'local row = { text="Hide Unusable", getValue=function() return ACfg("hideUnusable") end }\n',
    })
    row = next(r for r in rows if r["key"] == "hideUnusable")
    assert row["module"] == "EllesmereUIQuickdraw"
    assert row["options_ref_count"] == 1, row
    assert row["options_refs"] == ["EllesmereUIOptions/EUI_Quickdraw_Options.lua:1"]
    # The page is in scope now, so it is not also reported as a foreign module.
    assert row["refs_other_modules"] == 0, row


def test_an_options_page_credits_only_the_module_it_configures(tmp_path):
    """Short key names repeat across modules; the page name keeps them apart.

    `size` is declared independently in most modules. Counting any reference
    from the options addon would credit a Nameplates row to Quickdraw's key
    and back again, which is the false positive the folder scoping existed to
    prevent in the first place.
    """
    rows = _build_tree(tmp_path, {
        "EllesmereUI.toc": _SUITE_TOC,
        "EllesmereUI.lua": "local EllesmereUI = {}\n",
        "EllesmereUIQuickdraw/EllesmereUIQuickdraw.toc": "## Title: Q\nEllesmereUIQuickdraw.lua\n",
        "EllesmereUIQuickdraw/EllesmereUIQuickdraw.lua":
            "local DB_DEFAULTS = { profile = { size = 24 } }\n",
        "EllesmereUINameplates/EllesmereUINameplates.toc": "## Title: N\nEllesmereUINameplates.lua\n",
        "EllesmereUINameplates/EllesmereUINameplates.lua":
            "local DB_DEFAULTS = { profile = { size = 12 } }\n",
        "EllesmereUIOptions/EllesmereUIOptions.toc": "## Title: O\nEUI_Nameplates_Options.lua\n",
        "EllesmereUIOptions/EUI_Nameplates_Options.lua":
            'local row = { getValue=function() return SGet("size") end }\n',
    })
    by_module = {r["module"]: r for r in rows if r["key"] == "size"}
    assert by_module["EllesmereUINameplates"]["options_ref_count"] == 1
    assert by_module["EllesmereUIQuickdraw"]["options_ref_count"] == 0, \
        "a Nameplates page must not build a Quickdraw control"


# --- The index query CLI -----------------------------------------------------
#
# The index was current and unread through two whole sessions because a lookup
# written by hand cost more than the grep of the source it replaced. These
# check the parts of query.py that decide whether an answer can be over-read.


def _query_module():
    import importlib.util

    path = REPO / "skills" / "ellesmereui-search" / "scripts" / "query.py"
    spec = importlib.util.spec_from_file_location("eui_query", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("skill,before,after", [
    ("ellesmereui-search", ["--no-ensure", "--limit", "3", "status"],
     ["--no-ensure", "status", "--limit", "3"]),
    ("wow-api-search", ["--limit", "3", "search", "Specialization"],
     ["search", "Specialization", "--limit", "3"]),
])
def test_a_flag_works_on_either_side_of_the_subcommand(skill, before, after):
    """Both orderings parse, and both mean the same thing.

    Two separate argparse traps live here. Flags declared only on the main
    parser reject `... status --limit 3` outright; flags declared on both
    through `parents=` parse `--limit 3 status` and then quietly overwrite it
    with the subparser's default, so the flag does nothing and says nothing.
    The second is worse: a --limit that is ignored looks like an index that has
    no more records.

    What is asserted is that the two orderings agree, not that either
    succeeds. `ellesmereui-search` builds its index from a local addon
    checkout, so on a machine without one -- CI, or a fresh clone -- every
    subcommand exits 1 with "no index built yet". Requiring exit 0 tested for
    that checkout rather than for the parser. Agreement still catches both
    traps: argparse rejecting `status --limit 3` outright moves one exit code
    and not the other, and a silently discarded --limit changes one stdout and
    not the other. `wow-api-search` ships its index in the repo, so that
    parametrization exercises the same parser against real records.
    """
    script = str(REPO / "skills" / skill / "scripts" / "query.py")
    runs = [subprocess.run([sys.executable, script] + argv,
                           capture_output=True, text=True, timeout=180)
            for argv in (before, after)]
    for proc, argv in zip(runs, (before, after)):
        assert "unrecognized arguments" not in proc.stderr, (argv, proc.stderr)
        assert proc.returncode != 2, (argv, proc.stderr)  # argparse usage error
    assert runs[0].returncode == runs[1].returncode, (
        f"flag position changed the exit code: "
        f"{runs[0].returncode} vs {runs[1].returncode}")
    assert runs[0].stdout == runs[1].stdout, "flag position changed the answer"


def test_query_says_when_a_list_is_a_sample_rather_than_an_answer(capsys):
    """Both truncations are stated: the CLI's --limit and the index's own cap."""
    Q = _query_module()
    Q.show_list(["a:1", "b:2", "c:3"], 40, 2, label="callers")
    out = capsys.readouterr().out
    assert "showing 2 of 3 callers" in out, out
    assert "true count is 40" in out, out

    Q.show_list(["a:1", "b:2"], 2, 20)
    assert "true count" not in capsys.readouterr().out


def test_query_marks_a_receiver_scoped_caller_count_as_a_floor(capsys):
    """A field or method is credited only through its own receiver.

    A count of 1 on one of those is unproven, and the difference between a
    blast radius of one line and one nobody has seen is exactly what a
    signature change turns on. A local has no receiver to rename, so its count
    is the complete answer and carries no caveat.
    """
    Q = _query_module()
    assert "floor" in Q.caller_caveat({"kind": "field", "name": "SpecPositionName", "caller_count": 1})
    assert "floor" in Q.caller_caveat({"kind": "method", "name": "Update", "caller_count": 0})
    assert Q.caller_caveat({"kind": "local", "name": "SpecIndexFor", "caller_count": 1}) == ""
    assert Q.caller_caveat({"kind": "field", "name": "Wide", "caller_count": 40}) == ""


def test_query_labels_a_substring_match_as_one(capsys):
    """`alwaysShow` and `alwaysShowButtons` are different settings."""
    Q = _query_module()
    Q.fuzzy_note(False, "alwaysShow", "key")
    assert "no exact key match" in capsys.readouterr().out
    Q.fuzzy_note(True, "alwaysShowButtons", "key")
    assert capsys.readouterr().out == ""


# --- The API query CLI -------------------------------------------------------


def _api_query_module():
    import importlib.util

    path = REPO / "skills" / "wow-api-search" / "scripts" / "query.py"
    spec = importlib.util.spec_from_file_location("api_query", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_query_renders_the_taint_marking_with_the_signature(capsys):
    """`secret_arguments` is the field a combat-path change turns on.

    A PR in this addon answered its taint checklist line without a lookup,
    while the index carried the marking all along. Printing it beside the
    signature is what makes the cheap answer the one in front of you.
    """
    Q = _api_query_module()
    Q.render_function("SetSpecialization", {
        "qualified_name": "C_SpecializationInfo.SetSpecialization",
        "system": "SpecializationInfo", "file": "SpecializationInfoDocumentation.lua",
        "arguments": [{"name": "specIndex", "type": "luaIndex", "nilable": False}],
        "returns": [{"name": "success", "type": "bool", "nilable": False}],
        "secret_arguments": "AllowedWhenUntainted",
    }, {"documentation": "included"})
    out = capsys.readouterr().out
    assert "C_SpecializationInfo.SetSpecialization(specIndex: luaIndex)  ->  success: bool" in out
    assert "AllowedWhenUntainted" in out
    assert "note: Blizzard records none" in out, "an absent note is a fact worth stating"


def test_api_query_numbers_every_payload_argument(capsys):
    """A handler that binds fewer args than the event sends drops the discriminator.

    SPELL_UPDATE_COOLDOWN's fourth argument is how Blizzard tells a global
    cooldown from a spell cooldown, and a session reconstructed that
    distinction by hand after reading only the first.
    """
    Q = _api_query_module()
    Q.render_event("SPELL_UPDATE_COOLDOWN", {
        "literal_name": "SPELL_UPDATE_COOLDOWN", "name": "SpellUpdateCooldown",
        "system": "SpellBook", "file": "SpellBookDocumentation.lua",
        "payload": [{"name": "spellID", "type": "number", "nilable": True},
                    {"name": "baseSpellID", "type": "number", "nilable": True},
                    {"name": "category", "type": "number", "nilable": True},
                    {"name": "startRecoveryCategory", "type": "number", "nilable": True}],
    }, {"documentation": "included"})
    out = capsys.readouterr().out
    assert "payload (4 args" in out
    assert "4. startRecoveryCategory: number?" in out


def test_api_query_says_when_the_index_is_the_one_without_prose(capsys):
    """Silence from the bundled index means the copy, not the entry."""
    Q = _api_query_module()
    Q.render_doc({"file": "SpellDocumentation.lua"},
                 {"documentation": "omitted", "source_documented": 1307})
    out = capsys.readouterr().out
    assert "the bundled copy omits" in out and "1307" in out


def test_api_query_lists_an_event_once_though_it_is_indexed_twice(capsys):
    """Events are keyed by literal AND camelCase name; a search must not double."""
    Q = _api_query_module()
    index = {"events": {
        "ACTIVE_PLAYER_SPECIALIZATION_CHANGED": {
            "literal_name": "ACTIVE_PLAYER_SPECIALIZATION_CHANGED", "system": "Unit"},
        "ActivePlayerSpecializationChanged": {
            "literal_name": "ACTIVE_PLAYER_SPECIALIZATION_CHANGED", "system": "Unit"},
    }}
    args = argparse.Namespace(pattern="Specialization", limit=25, index=None, json=False)
    Q.load_index = lambda explicit=None: (index, Path("fake.json"))
    Q.cmd_search(args)
    out = capsys.readouterr().out
    assert out.count("ACTIVE_PLAYER_SPECIALIZATION_CHANGED") == 1, out
    assert "== events (1)" in out, out


# --- The skills point at things that exist -----------------------------------
#
# eui-perf does no lookup of its own: it routes to the diagnostics addon, to
# tools/perf, and to the bundled API index. Routing is the whole value, so a
# renamed command or a moved script breaks the skill completely while leaving
# every other check green. These are the hookup tests for that.


def test_euidiag_commands_named_in_skills_really_dispatch():
    """Every `/euidiag <cmd>` a skill tells the user to run is registered.

    The addon registers subcommands with ns.Command("name", {...}); an
    unregistered one prints usage and does nothing, and the user is the only
    one who would ever find out.
    """
    addon = REPO / "addons" / "EllesmereUISecretsDiag"
    registered = set()
    for lua in addon.glob("*.lua"):
        registered.update(
            re.findall(r'ns\.Command\(\s*"([a-z]+)"', lua.read_text(encoding="utf-8"))
        )
    assert registered, "no /euidiag subcommands found in the addon at all"

    named = set()
    for skill_dir in SKILL_DIRS:
        for doc in ("SKILL.md", "README.md"):
            path = skill_dir / doc
            if path.is_file():
                named.update(
                    re.findall(r"/euidiag\s+([a-z]+)", path.read_text(encoding="utf-8"))
                )
    assert named, "no skill names a /euidiag command -- eui-perf should"
    assert not (named - registered), (
        f"skills name /euidiag commands the addon does not register: "
        f"{sorted(named - registered)}"
    )


def test_repo_paths_named_in_skills_exist():
    """A skill that routes to `tools/perf/...` has to be routing somewhere real."""
    missing = []
    for skill_dir in SKILL_DIRS:
        for doc in ("SKILL.md", "README.md"):
            path = skill_dir / doc
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for rel in re.findall(r"(?<![\w./])(?:\./)?((?:tools|addons)/[\w./-]+)", text):
                rel = rel.rstrip(".")
                if not (REPO / rel).exists():
                    missing.append(f"{skill_dir.name}/{doc}: {rel}")
    assert not missing, "skills point at paths that do not exist: " + "; ".join(missing)


def test_bundled_index_carries_the_profiler_surface():
    """wow-api-search documents C_AddOnProfiler, so the index must hold it.

    This is the API that answers "what does this addon cost" without a CVar or
    a reload, and a session that cannot find it falls back to frame rate --
    which cannot attribute cost to a module at all. The worked example in
    SKILL.md names these; this keeps the example from going stale silently.
    """
    index = _bundled_index()
    functions = index["functions"]
    for name in (
        "GetAddOnMetric",
        "GetOverallMetric",
        "GetApplicationMetric",
        "GetTopKAddOnsForMetric",
        "MeasureCall",
    ):
        entries = functions.get(name)
        assert entries, f"C_AddOnProfiler.{name} missing from the bundled index"
        qualified = {e["qualified_name"] for e in (entries if isinstance(entries, list) else [entries])}
        assert f"C_AddOnProfiler.{name}" in qualified, qualified

    metric = _first(index["tables"]["AddOnProfilerMetric"])
    members = {f["name"] for f in metric["fields"]}
    # RecentAverageTime is the steady-state read; the CountTimeOver* buckets are
    # the only per-addon answer to "why are my 1% lows bad".
    assert "RecentAverageTime" in members
    assert {"CountTimeOver1Ms", "CountTimeOver5Ms", "CountTimeOver10Ms"} <= members, members


def _style_checker():
    """The EllesmereUI style checker, loaded from its script path."""
    import importlib.util

    path = REPO / "skills" / "ellesmereui-pr-check" / "scripts" / "check_style.py"
    spec = importlib.util.spec_from_file_location("eui_check_style", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _budget(text: str, scope=None):
    """Run the comment budget over an in-memory Lua source."""
    C = _style_checker()
    src = C.Source(Path("Fake.lua"), "Fake.lua", text)
    return list(C.check_comment_budget(src, scope))


def _comment_lines(n: int, first: str = "local x = 1\n\n") -> str:
    body = "".join(f"-- line {i}\n" for i in range(n))
    return f"{first}{body}local y = 2\n"


def test_comment_budget_caps_a_block():
    """Over the cap reports; at the cap does not."""
    C = _style_checker()
    assert not _budget(_comment_lines(C.COMMENT_BUDGET))
    findings = _budget(_comment_lines(C.COMMENT_BUDGET + 1))
    assert len(findings) == 1
    assert findings[0].rule == "comment-budget"
    assert findings[0].severity == C.ERROR


def test_comment_budget_allows_a_longer_file_header():
    """A header is the comment that earns its length; a block under code is not.

    Both blocks here are the same size. Only their position differs, which is
    the whole of the distinction the rule draws.
    """
    C = _style_checker()
    size = C.COMMENT_BUDGET + 1
    assert not _budget(_comment_lines(size, first=""))
    assert _budget(_comment_lines(size))
    assert _budget(_comment_lines(C.HEADER_BUDGET + 1, first=""))


def test_comment_budget_counts_changed_lines_only():
    """Editing inside a legacy block is silent; adding a new one is not.

    This is the rule's reason for taking `scope` itself rather than being
    filtered by it, and the property that keeps old comments from failing CI.
    """
    C = _style_checker()
    text = _comment_lines(C.COMMENT_BUDGET * 3)
    assert not _budget(text, scope={4})
    assert _budget(text, scope=set(range(1, 100)))


def test_comment_budget_blank_lines_do_not_reset_a_block():
    """Splitting a wall of text with whitespace leaves it a wall of text."""
    C = _style_checker()
    half = C.COMMENT_BUDGET // 2 + 1
    text = "local x = 1\n\n" + ("-- a\n" * half) + "\n" + ("-- b\n" * half) + "local y = 2\n"
    assert _budget(text)


def test_comment_budget_reads_lua_comments_not_strings():
    """A long-bracket comment counts; a long-bracket string does not."""
    C = _style_checker()
    body = "".join(f"text {i}\n" for i in range(C.COMMENT_BUDGET + 2))
    assert _budget(f"local x = 1\n\n--[[\n{body}]]\nlocal y = 2\n")
    assert not _budget(f"local x = [[\n{body}]]\nlocal y = 2\n")


def test_comment_budget_respects_the_suppression_comment():
    """Reference material that must stay long has the documented escape hatch."""
    C = _style_checker()
    text = _comment_lines(C.COMMENT_BUDGET + 1,
                          first="local x = 1\n\n-- eui-style: allow comment-budget\n")
    assert not _budget(text)


def test_comment_budget_ignores_trailing_comments():
    """A comment after code shares its line with code, so no block forms."""
    C = _style_checker()
    text = "".join(f"local v{i} = {i} -- note {i}\n" for i in range(C.COMMENT_BUDGET + 5))
    assert not _budget(text)


# --------------------------------------------------------------------------
#  Lua masking
# --------------------------------------------------------------------------
# Every structural pass in both extractors runs against the masked copy, so a
# masking bug does not fail loudly -- it silently moves or drops records. The
# scan was rewritten to jump between the four characters that can open a masked
# region instead of visiting every character, which is the kind of change that
# keeps the common cases working and breaks an edge nobody has in the tree yet.


def _mask_fns():
    """Both copies of `mask_lua`. They are separate files and drift apart."""
    return (("build_index", _eui_builder().mask_lua),
            ("check_style", _style_checker().mask_lua))


def _blanked(text: str, *fragments: str) -> str:
    """`text` with each fragment replaced by spaces, newlines left in place.

    Spelling the expectation as "this substring disappears" keeps the test
    readable and stops it from asserting a hand-counted run of spaces.
    """
    out = text
    for frag in fragments:
        assert frag in out, frag
        out = out.replace(frag, re.sub(r"[^\n]", " ", frag), 1)
    return out


def test_mask_blanks_comments_and_string_bodies():
    for name, mask_lua in _mask_fns():
        text = 'local s = "hi" -- note\n'
        assert mask_lua(text) == _blanked(text, "hi", "-- note"), name


def test_mask_handles_long_brackets_and_nested_quotes():
    """Braces and quotes inside these must not reach a structural pass."""
    for name, mask_lua in _mask_fns():
        # A long string keeps its brackets and loses its body.
        text = "x = [[a{b}c]]\n"
        assert mask_lua(text) == _blanked(text, "a{b}c"), name
        # The body of a [==[ ]==] string may contain ]] without ending it.
        text = "x = [==[a]]b]==]\n"
        assert mask_lua(text) == _blanked(text, "a]]b"), name
        # A long comment goes entirely, brackets included.
        text = "--[[ {x} ]]\ny = 1\n"
        assert mask_lua(text) == _blanked(text, "--[[ {x} ]]"), name
        # A quote of the other kind inside a string is body, not a delimiter.
        text = """x = "a'b" .. 'c"d'\n"""
        assert mask_lua(text) == _blanked(text, "a'b", 'c"d'), name


def test_mask_preserves_length_and_every_newline():
    """Offsets are read back against the original text, and lines are zipped.

    `check_style.comment_only_lines` compares `mask.splitlines()` against the
    source lines, so a newline lost inside a masked region shifts every later
    line of that file. The per-character version dropped one: a backslash
    escaping a newline inside a short string was overwritten with a space.
    """
    sources = [
        'x = "a\\\nb"\ny = 1\n',          # escaped newline inside a string
        "--[[\nmulti\nline\n]]\nz = 2\n",  # newline inside a long comment
        "s = [[\nraw\n]]\n",               # newline inside a long string
        "t = 'unterminated\nu = 3\n",      # a string that never closes
        "-- trailing comment with no newline",
        "",
    ]
    for name, mask_lua in _mask_fns():
        for text in sources:
            got = mask_lua(text)
            assert len(got) == len(text), (name, repr(text))
            assert [i for i, c in enumerate(got) if c == "\n"] == [
                i for i, c in enumerate(text) if c == "\n"
            ], (name, repr(text))


def test_mask_never_moves_a_newline_on_arbitrary_input():
    """The invariant above, fuzzed over the characters that drive the scanner.

    Length and newline placement are what every offset and line number in both
    indexes depend on. A property test states that once instead of guessing
    which literal will regress next.
    """
    import random

    rand = random.Random(1979)
    alphabet = "-[]=\"'\\\nabc "
    cases = [
        "".join(rand.choice(alphabet) for _ in range(rand.randint(0, 60)))
        for _ in range(3000)
    ]
    for name, mask_lua in _mask_fns():
        for text in cases:
            got = mask_lua(text)
            assert len(got) == len(text), (name, repr(text))
            assert [i for i, c in enumerate(got) if c == "\n"] == [
                i for i, c in enumerate(text) if c == "\n"
            ], (name, repr(text))
            # Masking only ever blanks: a non-space in the output is untouched.
            for i, c in enumerate(got):
                assert c == " " or c == text[i], (name, repr(text), i)


# --------------------------------------------------------------------------
#  Renamed receivers
# --------------------------------------------------------------------------
# Attribution follows the receiver, which is why `SetPoint` does not report
# every Blizzard frame the addon positions. The cost of that rule is every call
# through a receiver spelled differently from the recorded owner, which was
# 639 call sites -- `PP.ToPixels` read zero callers against a true eight.


def test_a_table_reached_through_a_local_alias_is_still_credited():
    """`local PPc = EllesmereUI and EllesmereUI.PP` is how a module borrows one.

    The guard is the house idiom for reaching another module's table, so the
    binding almost never appears as a bare `local PPc = PP`.
    """
    rows = _callers({
        "shared.lua": (
            "local PP = {}\n"
            "EllesmereUI.PP = PP\n"
            "function PP.ToPixels(x)\n"
            "end\n"
        ),
        "ModA/a.lua": (
            "local PPc = EllesmereUI and EllesmereUI.PP\n"
            "PPc.ToPixels(1)\n"
        ),
    })
    row = rows[("shared.lua", "PP.ToPixels")]
    assert "ModA/a.lua:2" in row["callers"], row
    assert "PPc.ToPixels" in (row.get("aliases") or []), row


def test_a_definition_written_on_a_local_alias_is_called_by_the_shared_name():
    """The same binding read backwards.

    A file opens `local EUI = _G.EllesmereUI or {}` and declares
    `function EUI.Foo()`, so the definition is recorded under the short name
    while the rest of the suite calls it `EllesmereUI.Foo(`.
    """
    rows = _callers({
        "shared.lua": (
            "function EllesmereUI.Anchor()\n"
            "end\n"
        ),
        "ModB/b.lua": (
            "local EUI = _G.EllesmereUI or {}\n"
            "function EUI.Refresh()\n"
            "end\n"
        ),
        "ModA/a.lua": "EllesmereUI.Refresh()\n",
    })
    row = rows[("ModB/b.lua", "EUI.Refresh")]
    assert row["callers"] == ["ModA/a.lua:1"], row
    assert "EllesmereUI.Refresh" in (row.get("aliases") or []), row


def test_a_local_alias_does_not_reach_past_the_file_that_binds_it():
    """`PPc` is a local. Another file's `PPc` is another table."""
    rows = _callers({
        "shared.lua": (
            "local PP = {}\n"
            "EllesmereUI.PP = PP\n"
            "function PP.ToPixels(x)\n"
            "end\n"
        ),
        "ModA/a.lua": (
            "local PPc = EllesmereUI and EllesmereUI.PP\n"
            "PPc.ToPixels(1)\n"
        ),
        "ModB/b.lua": (
            "local PPc = SomethingElse\n"
            "PPc.ToPixels(2)\n"
        ),
    })
    row = rows[("shared.lua", "PP.ToPixels")]
    assert row["callers"] == ["ModA/a.lua:2"], row


def test_a_table_that_is_rebound_at_runtime_credits_no_alias():
    """`EllesmereUI.Widgets` really is three tables.

    A search feature swaps it for an absorber and swaps it back, so the path
    names no single table. Picking one would invent calls into a widget
    factory that was not installed at the time.
    """
    rows = _callers({
        "shared.lua": (
            "local WidgetFactory = {}\n"
            "local AbsorberW = {}\n"
            "EllesmereUI.Widgets = WidgetFactory\n"
            "EllesmereUI.Widgets = AbsorberW\n"
            "function WidgetFactory.DualRow(a)\n"
            "end\n"
            "function AbsorberW.Row(a)\n"
            "end\n"
        ),
        "ModA/a.lua": (
            "local W = EllesmereUI and EllesmereUI.Widgets\n"
            "W.DualRow(1)\n"
        ),
    })
    row = rows[("shared.lua", "WidgetFactory.DualRow")]
    assert row["callers"] == [], row


def test_an_or_between_two_named_tables_is_not_an_alias():
    """Two candidate receivers is not a rename, it is a choice made at runtime."""
    rows = _callers({
        "shared.lua": (
            "local PP = {}\n"
            "EllesmereUI.PP = PP\n"
            "function PP.ToPixels(x)\n"
            "end\n"
        ),
        "ModA/a.lua": (
            "local PPc = EllesmereUI.PanelPP or EllesmereUI.PP\n"
            "PPc.ToPixels(1)\n"
        ),
    })
    row = rows[("shared.lua", "PP.ToPixels")]
    assert row["callers"] == [], row


def test_a_local_bound_to_a_non_table_creates_no_alias():
    """`local n = count` must not make every `n.foo(` in the file a call.

    The right-hand side has to name something this index already knows owns a
    definition, or an alias pass turns arithmetic into call edges.
    """
    rows = _callers({
        "shared.lua": (
            "local PP = {}\n"
            "EllesmereUI.PP = PP\n"
            "function PP.ToPixels(x)\n"
            "end\n"
        ),
        "ModA/a.lua": (
            "local count = 3\n"
            "local n = count\n"
            "n.ToPixels(1)\n"
        ),
    })
    row = rows[("shared.lua", "PP.ToPixels")]
    assert row["callers"] == [], row


# --------------------------------------------------------------------------
#  SavedVariables keys
# --------------------------------------------------------------------------


def _sv_rows(files: dict[str, str], sv_names: dict[str, str], modules=("ModA", "ModB")):
    B = _eui_builder()
    sources = [B.Source(rel, text) for rel, text in files.items()]
    rows = B.extract_saved_variable_keys(sources, sv_names, list(modules))
    return {(r["store"], r["key"]): r for r in rows}


def test_saved_variable_keys_are_found_in_both_access_forms():
    """`DB.key` and `DB["key"]` are the same setting.

    The bracket form is matched against the raw text rather than the mask --
    the key is inside a string literal, which the mask has blanked -- so the
    two forms run through different code and only one of them would notice a
    capture group renumbered by mistake.
    """
    rows = _sv_rows(
        {"ModA/a.lua": 'EllesmereUIDB.alpha = 1\nEllesmereUIDB["beta"] = 2\n'},
        {"EllesmereUIDB": "ModA"},
    )
    assert set(rows) == {("EllesmereUIDB", "alpha"), ("EllesmereUIDB", "beta")}, rows
    assert rows[("EllesmereUIDB", "beta")]["refs"] == ["ModA/a.lua:2"], rows


def test_a_saved_variable_name_that_prefixes_another_keeps_its_own_keys():
    """One alternation over all forty names replaced one pass per name.

    `EllesmereUIDB` is a prefix of `EllesmereUIDBExtra`, so an alternation that
    let the shorter branch win would file every `EllesmereUIDBExtra` key under
    the wrong store, and the count would still look right.
    """
    rows = _sv_rows(
        {"ModA/a.lua": "EllesmereUIDBExtra.alpha = 1\nEllesmereUIDB.beta = 2\n"},
        {"EllesmereUIDB": "ModA", "EllesmereUIDBExtra": "ModB"},
    )
    assert set(rows) == {
        ("EllesmereUIDBExtra", "alpha"),
        ("EllesmereUIDB", "beta"),
    }, rows


def test_every_saved_variable_name_is_still_scanned():
    """The single pass must not quietly cover only the first few names."""
    names = {f"Store{i}DB": "ModA" for i in range(12)}
    text = "".join(f"Store{i}DB.key{i} = {i}\n" for i in range(12))
    rows = _sv_rows({"ModA/a.lua": text}, names)
    assert set(rows) == {(f"Store{i}DB", f"key{i}") for i in range(12)}, sorted(rows)


def test_a_key_named_only_inside_a_comment_is_not_a_setting():
    """The attribute form runs against the mask, so commented code stays out."""
    rows = _sv_rows(
        {"ModA/a.lua": "-- EllesmereUIDB.ghost = 1\nEllesmereUIDB.real = 2\n"},
        {"EllesmereUIDB": "ModA"},
    )
    assert set(rows) == {("EllesmereUIDB", "real")}, rows
