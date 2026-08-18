"""Addon discovery, .toc validation, and finding the game to install into.

The failures worth testing here are the silent ones. The WoW client does not
report a .toc named wrongly or a file listed but missing -- it loads nothing, or
loads most of the addon, and says the same amount about both.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from wow_tools import addons as addons_mod
from wow_tools import install as engine
from wow_tools import wow
from wow_tools.install import Method, Outcome

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
#  Fixtures
# --------------------------------------------------------------------------

def make_addon(base: Path, name: str, *, files=("Core.lua",), toc_name=None,
               interface="120000", extra="") -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / f).write_text("-- lua\n", encoding="utf-8")
    body = f"## Interface: {interface}\n## Title: {name}\n{extra}"
    body += "\n" + "\n".join(files) + "\n"
    (d / f"{toc_name or name}.toc").write_text(body, encoding="utf-8")
    return d


def make_install(base: Path, flavor: str = "_retail_") -> Path:
    flavor_dir = base / "World of Warcraft" / flavor
    (flavor_dir / "Interface" / "AddOns").mkdir(parents=True)
    return flavor_dir


# --------------------------------------------------------------------------
#  .toc validation
# --------------------------------------------------------------------------

def test_valid_addon_parses(tmp_path):
    d = make_addon(tmp_path, "MyAddon", files=("Core.lua", "Extra.lua"),
                   extra="## SavedVariables: MyAddonDB\n## Dependencies: OtherAddon")
    addon, problems = addons_mod.validate(d)
    assert problems == []
    assert addon.name == "MyAddon"
    assert addon.interface == "120000"
    assert addon.saved_variables == ("MyAddonDB",)
    assert addon.dependencies == ("OtherAddon",)
    assert addon.files == ("Core.lua", "Extra.lua")


def test_toc_must_be_named_after_its_folder(tmp_path):
    """The client's rule, and the one that produces total silence when broken."""
    d = make_addon(tmp_path, "MyAddon", toc_name="Wrong")
    addon, problems = addons_mod.validate(d)
    assert addon is None
    assert any("no MyAddon.toc" in p for p in problems)


def test_file_listed_but_missing_is_reported(tmp_path):
    d = make_addon(tmp_path, "MyAddon", files=("Core.lua",))
    (d / "MyAddon.toc").write_text(
        "## Interface: 120000\n## Title: MyAddon\nCore.lua\nGone.lua\n", encoding="utf-8"
    )
    addon, problems = addons_mod.validate(d)
    assert addon is not None, "the addon still parses; the file list is the problem"
    assert any("Gone.lua" in p for p in problems)


def test_missing_interface_is_reported(tmp_path):
    d = make_addon(tmp_path, "MyAddon")
    (d / "MyAddon.toc").write_text("## Title: MyAddon\nCore.lua\n", encoding="utf-8")
    _, problems = addons_mod.validate(d)
    assert any("## Interface:" in p for p in problems)


def test_backslash_paths_in_toc_resolve(tmp_path):
    """Windows separators are legal in a .toc and common in the wild."""
    d = tmp_path / "MyAddon"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "Core.lua").write_text("-- lua\n", encoding="utf-8")
    (d / "MyAddon.toc").write_text(
        "## Interface: 120000\n## Title: MyAddon\nsub\\Core.lua\n", encoding="utf-8"
    )
    _, problems = addons_mod.validate(d)
    assert problems == []


def test_title_colour_escapes_stripped_for_display(tmp_path):
    d = make_addon(tmp_path, "MyAddon")
    (d / "MyAddon.toc").write_text(
        "## Interface: 120000\n## Title: |cff0cd29fEllesmereUI|r Diagnostics\nCore.lua\n",
        encoding="utf-8",
    )
    addon, _ = addons_mod.validate(d)
    assert addon.summary() == "EllesmereUI Diagnostics"


# --------------------------------------------------------------------------
#  The addon actually shipped here
# --------------------------------------------------------------------------

def test_bundled_addons_are_valid():
    found, problems = addons_mod.discover()
    assert problems == [], f"bundled addons must be clean: {problems}"
    assert {a.name for a in found} == {"EllesmereUISecretsDiag"}


def test_bundled_diag_addon_declares_what_it_needs():
    found, _ = addons_mod.discover()
    diag = next(a for a in found if a.name == "EllesmereUISecretsDiag")
    assert diag.dependencies == ("EllesmereUI",), (
        "the diagnostics addon is useless without the suite it measures, and the "
        "installer warns about the gap using this field"
    )
    assert diag.saved_variables == ("EllesmereUISecretsDiagDB",), (
        "euidiag-perf.py reads recordings out of this SavedVariables file"
    )


def test_bundled_diag_toc_load_order_puts_core_first():
    """Every other file registers against Core's command registry."""
    found, _ = addons_mod.discover()
    diag = next(a for a in found if a.name == "EllesmereUISecretsDiag")
    assert diag.files[0] == "Core.lua"
    assert diag.files.index("Secrets.lua") < diag.files.index("Investigations.lua"), (
        "Investigations uses the probe registry and unit helpers Secrets owns"
    )


# --------------------------------------------------------------------------
#  Name resolution
# --------------------------------------------------------------------------

def test_resolve_names(tmp_path):
    """Resolution moved to the catalogue; the rules for addons did not change.

    The shared cases live in test_catalogue.py. What is worth keeping here is
    that real Addon objects resolve, and that case folding -- which only addons
    and hooks do -- still applies to this kind.
    """
    from wow_tools.__main__ import ADDONS
    from wow_tools.catalogue import UnknownName

    a = addons_mod.validate(make_addon(tmp_path, "Alpha"))[0]
    b = addons_mod.validate(make_addon(tmp_path, "Beta"))[0]
    available = [a, b]

    assert ADDONS.resolve(["all"], available) == [a, b]
    assert ADDONS.resolve(["none"], available) == []
    assert ADDONS.resolve(["Alpha"], available) == [a]
    # CamelCase addon names are painful to type exactly.
    assert ADDONS.resolve(["alpha"], available) == [a]
    assert ADDONS.resolve(["Alpha", "Alpha"], available) == [a]
    with pytest.raises(UnknownName):
        ADDONS.resolve(["Nope"], available)


# --------------------------------------------------------------------------
#  Finding the game
# --------------------------------------------------------------------------

def test_install_recognised_by_its_interface_folder(tmp_path):
    flavor = make_install(tmp_path)
    assert wow.is_install(flavor)
    assert not wow.is_install(tmp_path / "World of Warcraft" / "_ptr_")


def test_install_with_no_addons_folder_still_counts(tmp_path):
    """A fresh install has no AddOns folder; refusing it would be backwards."""
    flavor = tmp_path / "World of Warcraft" / "_retail_"
    (flavor / "Interface").mkdir(parents=True)
    assert wow.is_install(flavor)


def test_install_paths(tmp_path):
    flavor = make_install(tmp_path)
    install = wow.WowInstall(flavor)
    assert install.flavor == "retail"
    assert install.root == flavor.parent
    assert install.addons == flavor / "Interface" / "AddOns"
    assert install.wtf == flavor / "WTF"


def test_savedvariables_newest_first(tmp_path):
    flavor = make_install(tmp_path)
    made = []
    for i, account in enumerate(("111#1", "222#2")):
        sv = flavor / "WTF" / "Account" / account / "SavedVariables"
        sv.mkdir(parents=True)
        f = sv / "EllesmereUISecretsDiag.lua"
        f.write_text("x = {}\n", encoding="utf-8")
        os.utime(f, (1000 + i * 100, 1000 + i * 100))
        made.append(f)

    hits = wow.WowInstall(flavor).savedvariables("EllesmereUISecretsDiag")
    assert hits == [made[1], made[0]], "the account played most recently comes first"


def test_env_var_points_at_an_install(tmp_path, monkeypatch):
    flavor = make_install(tmp_path)
    monkeypatch.setenv("WOW_INSTALL", str(flavor.parent))
    monkeypatch.delenv("WOW_ADDONS_DIR", raising=False)
    found = wow.discover_installs()
    assert flavor.resolve() in [i.flavor_dir.resolve() for i in found]


def test_env_var_tolerates_the_flavor_directory(tmp_path, monkeypatch):
    """Copying a path from a file manager usually lands on _retail_, not above it."""
    flavor = make_install(tmp_path)
    monkeypatch.setenv("WOW_INSTALL", str(flavor))
    found = wow.discover_installs()
    assert flavor.resolve() in [i.flavor_dir.resolve() for i in found]


def test_explicit_addons_dir_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("WOW_ADDONS_DIR", str(tmp_path / "from-env"))
    directory, candidates = wow.resolve_addons_dir(tmp_path / "explicit")
    assert directory == tmp_path / "explicit"
    assert candidates == []

    directory, _ = wow.resolve_addons_dir(None)
    assert directory == tmp_path / "from-env"


def test_looks_like_addons_dir(tmp_path):
    flavor = make_install(tmp_path)
    assert wow.looks_like_addons_dir(flavor / "Interface" / "AddOns")
    assert not wow.looks_like_addons_dir(tmp_path / "somewhere" / "AddOns")
    assert not wow.looks_like_addons_dir(flavor / "Interface")


def test_discover_installs_finds_a_proton_style_prefix(tmp_path, monkeypatch):
    """The layout that motivated the bounded search rather than a fixed path."""
    deep = tmp_path / "Games" / "battlenet" / "drive_c" / "Program Files (x86)"
    flavor = make_install(deep)
    monkeypatch.delenv("WOW_INSTALL", raising=False)
    monkeypatch.delenv("WOW_PATH", raising=False)
    monkeypatch.setattr(wow, "_platform_bases", lambda: [tmp_path])
    found = wow.discover_installs()
    assert [i.flavor_dir.resolve() for i in found] == [flavor.resolve()]


def test_discover_installs_orders_retail_first(tmp_path, monkeypatch):
    """An unattended run picks the first candidate, so the order is a decision."""
    game = tmp_path / "World of Warcraft"
    for flavor in ("_ptr_", "_retail_", "_classic_"):
        (game / flavor / "Interface").mkdir(parents=True)
    monkeypatch.delenv("WOW_INSTALL", raising=False)
    monkeypatch.delenv("WOW_PATH", raising=False)
    monkeypatch.setattr(wow, "_platform_bases", lambda: [tmp_path])
    found = wow.discover_installs()
    assert [i.flavor for i in found] == ["retail", "ptr", "classic"]


def test_discover_installs_survives_an_unreadable_base(tmp_path, monkeypatch):
    """A dead mount under /media must not fail the whole install."""
    monkeypatch.delenv("WOW_INSTALL", raising=False)
    monkeypatch.delenv("WOW_PATH", raising=False)
    monkeypatch.setattr(wow, "_platform_bases", lambda: [tmp_path / "gone", tmp_path])
    make_install(tmp_path)
    assert len(wow.discover_installs()) == 1


# --------------------------------------------------------------------------
#  Installing an addon
# --------------------------------------------------------------------------

def test_addon_links_into_an_addons_folder(tmp_path):
    source = make_addon(tmp_path / "repo", "MyAddon")
    addon, _ = addons_mod.validate(source)
    addons_dir = make_install(tmp_path) / "Interface" / "AddOns"

    if not engine.symlinks_available(addons_dir):
        pytest.skip("no symlink support here")

    r = engine.install_item(addon, addons_dir, Method.SYMLINK)
    assert r.outcome is Outcome.LINKED
    assert (addons_dir / "MyAddon" / "MyAddon.toc").is_file()

    again = engine.install_item(addon, addons_dir, Method.SYMLINK)
    assert again.outcome is Outcome.CURRENT


def test_addon_will_not_clobber_a_hand_installed_copy(tmp_path):
    """The realistic collision: the same addon installed from CurseForge."""
    source = make_addon(tmp_path / "repo", "MyAddon")
    addon, _ = addons_mod.validate(source)
    addons_dir = make_install(tmp_path) / "Interface" / "AddOns"
    make_addon(addons_dir, "MyAddon")  # somebody else's copy, no marker file

    r = engine.install_item(addon, addons_dir, Method.COPY)
    assert r.outcome is Outcome.BLOCKED
    assert "not installed by wow-tools" in r.detail

    forced = engine.install_item(addon, addons_dir, Method.COPY, force=True)
    assert forced.outcome is Outcome.UPDATED


def test_uninstall_leaves_a_foreign_addon_alone(tmp_path):
    source = make_addon(tmp_path / "repo", "MyAddon")
    addon, _ = addons_mod.validate(source)
    addons_dir = make_install(tmp_path) / "Interface" / "AddOns"
    make_addon(addons_dir, "MyAddon")

    assert engine.uninstall_item(addon, addons_dir).outcome is Outcome.BLOCKED
    assert (addons_dir / "MyAddon" / "MyAddon.toc").is_file()


# --------------------------------------------------------------------------
#  The offline tools under tools/
# --------------------------------------------------------------------------

def test_perf_tool_answers_help():
    """Same contract the SKILL.md scripts are held to: it runs on a bare clone."""
    r = subprocess.run(
        [sys.executable, str(REPO / "tools" / "perf" / "euidiag-perf.py"), "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr


def test_harness_can_still_find_the_addon_after_the_move():
    """harness.lua resolves the addon by relative path, and the move changed it.

    Checked without running Lua, so it fails on a machine that has no
    interpreter -- which is where a broken path would otherwise go unnoticed.
    """
    diag = REPO / "tools" / "diag"
    harness = (diag / "harness.lua").read_text(encoding="utf-8")
    candidates = re.findall(r'here\s*\.\.\s*"([^"]+)"', harness)
    assert candidates, "harness.lua no longer lists candidate addon paths"
    resolved = [(diag / c).resolve() for c in candidates]
    assert any((p / "Core.lua").is_file() for p in resolved), (
        f"none of harness.lua's candidate paths reach the addon: {resolved}"
    )


def test_harness_runs_clean(tmp_path):
    """The check a syntax pass cannot make: load order, registration, dispatch."""
    lua = shutil.which("lua5.1") or shutil.which("lua")
    if lua is None:
        pytest.skip("no Lua interpreter available")

    r = subprocess.run(
        [lua, "harness.lua"], cwd=REPO / "tools" / "diag",
        capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0, f"harness failed:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"
    assert "ALL COMMANDS RAN CLEAN" in r.stdout


# --------------------------------------------------------------------------
#  doctor: is what I am editing what the game loads
# --------------------------------------------------------------------------

def _addons_dir_with(tmp_path, live_target: Path) -> Path:
    flavor = make_install(tmp_path)
    addons = flavor / "Interface" / "AddOns"
    (addons / "EllesmereUISecretsDiag").symlink_to(live_target)
    return addons


def test_shadow_copy_under_a_checkout_is_found(tmp_path):
    """The failure this exists for: an addon checkout living inside AddOns.

    A folder named after an installed addon, holding that addon's .toc, reads
    like the source and answers a grep from the AddOns directory -- but nothing
    loads it, so an edit to it changes nothing and reports no error.
    """
    live = make_addon(tmp_path / "repo", "Widget")
    flavor = make_install(tmp_path)
    addons = flavor / "Interface" / "AddOns"
    (addons / "Widget").symlink_to(live)
    shadow = make_addon(addons / "SomeFork", "Widget")

    found = wow.find_shadow_copies(addons, "Widget", live)
    assert found == [shadow]


def test_the_live_copy_is_never_reported_as_its_own_shadow(tmp_path):
    """A checkout installed by path rather than by symlink is not a decoy."""
    flavor = make_install(tmp_path)
    addons = flavor / "Interface" / "AddOns"
    live = make_addon(addons, "Widget")
    assert wow.find_shadow_copies(addons, "Widget", live) == []


def test_a_folder_without_the_toc_is_not_a_shadow(tmp_path):
    """Name collisions are common; only a loadable duplicate can take an edit."""
    live = make_addon(tmp_path / "repo", "Widget")
    flavor = make_install(tmp_path)
    addons = flavor / "Interface" / "AddOns"
    (addons / "Widget").symlink_to(live)
    (addons / "Other" / "Widget").mkdir(parents=True)
    assert wow.find_shadow_copies(addons, "Widget", live) == []


def test_doctor_passes_on_a_clean_install(tmp_path, monkeypatch, capsys):
    from wow_tools import __main__ as cli

    addons = _addons_dir_with(tmp_path, REPO / "addons" / "EllesmereUISecretsDiag")
    monkeypatch.setenv("WOW_ADDONS_DIR", str(addons))
    rc = cli.main(["doctor"])
    assert rc == 0
    assert "shadow copy" not in capsys.readouterr().out


def test_doctor_fails_on_a_shadow_copy(tmp_path, monkeypatch, capsys):
    from wow_tools import __main__ as cli

    real = REPO / "addons" / "EllesmereUISecretsDiag"
    addons = _addons_dir_with(tmp_path, real)
    monkeypatch.setenv("WOW_ADDONS_DIR", str(addons))
    decoy = addons / "SomeFork" / "EllesmereUISecretsDiag"
    decoy.mkdir(parents=True)
    (decoy / "EllesmereUISecretsDiag.toc").write_text("## Interface: 120000\n", encoding="utf-8")

    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "shadow copy" in out
    assert str(decoy) in out


def test_doctor_reports_an_addon_that_resolves_outside_this_repo(tmp_path, monkeypatch, capsys):
    """The other half of the same question: the link is fine, the target is not ours."""
    from wow_tools import __main__ as cli

    elsewhere = make_addon(tmp_path / "elsewhere", "EllesmereUISecretsDiag")
    addons = _addons_dir_with(tmp_path, elsewhere)
    monkeypatch.setenv("WOW_ADDONS_DIR", str(addons))
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "NOT this repository's copy" in out
