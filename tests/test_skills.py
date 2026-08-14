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
