"""What the install CLI does today, pinned before anything moves.

These are characterization tests. They assert current behaviour rather than
desired behaviour, and several of them pin choices that look inconsistent on
purpose: with nothing named and no terminal to ask, skills install everything,
rules install nothing, and addons refuse outright. Each has a reason, none of
them is written down where the code can be compared against it, and all three
are easy to unify by accident while tidying the four install drivers into one.

Written against the code as it stands, so that a later refactor has something
to be wrong about. A test written afterwards only confirms the new code agrees
with itself.

Non-interactive throughout: pytest gives us a stdin that is not a tty, which is
the branch a CI run and a piped install.sh both take.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wow_tools import registry
from wow_tools.__main__ import main


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home directory the installer may write into."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def addons_dir(tmp_path):
    d = tmp_path / "World of Warcraft" / "_retail_" / "Interface" / "AddOns"
    d.mkdir(parents=True)
    return d


# --------------------------------------------------------------------------
#  Nothing named, no terminal to ask: three different answers
# --------------------------------------------------------------------------

def test_skills_default_to_all_when_none_are_named(home, capsys):
    """`--harness codex` alone installs every skill."""
    assert main(["install", "--harness", "codex", "--yes"]) == 0
    capsys.readouterr()
    installed = {p.name for p in (home / ".agents" / "skills").iterdir()}
    assert "wow-api-search" in installed
    assert len(installed) > 1, installed


def test_rules_are_never_installed_unless_named(home, capsys):
    """A rule loads in every session in every repo, so it is opted into.

    The harness selected here does take rules, so nothing but the default is
    keeping them out.
    """
    assert main(["install", "--harness", "claude-code", "--yes"]) == 0
    capsys.readouterr()
    assert (home / ".claude" / "skills").is_dir(), "skills should still install"
    rules = home / ".claude" / "rules"
    assert not rules.exists() or not list(rules.iterdir()), "rules must stay opted out"


def test_an_addons_folder_without_named_addons_is_refused(home, addons_dir, capsys):
    """Installing "everything" into a game directory nobody named an addon for."""
    rc = main(["install", "--wow-addons", str(addons_dir), "--yes"])
    assert rc == 2
    assert "--wow-addons given without --addons" in capsys.readouterr().err


# --------------------------------------------------------------------------
#  What the run says afterwards
# --------------------------------------------------------------------------

def test_a_run_that_moved_something_asks_for_a_restart(home, capsys):
    assert main(["install", "--harness", "codex", "--skills", "all", "--yes"]) == 0
    assert "Restart your harness" in capsys.readouterr().out


def test_a_run_that_moved_nothing_says_not_to_bother(home, capsys):
    """Advice to restart is only advice when something actually changed."""
    main(["install", "--harness", "codex", "--skills", "all", "--yes"])
    capsys.readouterr()
    assert main(["install", "--harness", "codex", "--skills", "all", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Everything was already in place; nothing to restart." in out
    assert "Restart your harness" not in out


def test_an_addon_run_that_moved_something_asks_for_a_reload(home, addons_dir, capsys):
    rc = main(["install", "--addons", "all", "--wow-addons", str(addons_dir), "--yes"])
    assert rc == 0
    assert "/reload in game" in capsys.readouterr().out


def test_an_addon_run_that_moved_nothing_says_not_to_bother(home, addons_dir, capsys):
    main(["install", "--addons", "all", "--wow-addons", str(addons_dir), "--yes"])
    capsys.readouterr()
    rc = main(["install", "--addons", "all", "--wow-addons", str(addons_dir), "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no /reload needed" in out
    assert "/reload in game" not in out


# --------------------------------------------------------------------------
#  Grouping
# --------------------------------------------------------------------------

def test_harnesses_sharing_a_directory_are_announced_once(home, capsys):
    """Five harnesses reading one path is one install, credited to all five."""
    keys = "codex,cursor,gemini-cli,openhands,roo-code"
    assert main(["install", "--harness", keys, "--skills", "all", "--yes"]) == 0
    out = capsys.readouterr().out
    assert out.count("into 1 directory") == 1, out
    assert "roo-code" in out or "Roo Code" in out


def test_a_rule_takes_the_filename_each_harness_wants(home, capsys):
    """The same rule is .md for Claude Code and .instructions.md for Copilot.

    This is what makes rules group by (directory, extension) rather than by
    directory: two harnesses only share an install when both agree on the name.
    """
    rule = next(iter(_rule_names()))
    assert main(["install", "--harness", "claude-code,copilot-cli",
                 "--rules", rule, "--skills", "none", "--yes"]) == 0
    capsys.readouterr()
    assert (home / ".claude" / "rules" / f"{rule}.md").is_file()
    assert (home / ".copilot" / "instructions" / f"{rule}.instructions.md").is_file()


def _rule_names():
    from wow_tools import rules as rules_mod
    found, _ = rules_mod.discover()
    return [r.name for r in found]


# --------------------------------------------------------------------------
#  Refusals
# --------------------------------------------------------------------------

@pytest.mark.parametrize("flag,value,word", [
    ("--skills", "nope", "unknown skill"),
    ("--rules", "nope", "unknown rule"),
    ("--addons", "nope", "unknown addon"),
    ("--hooks", "nope", "unknown hook"),
])
def test_an_unknown_name_exits_two_and_names_what_it_knows(flag, value, word, home,
                                                           tmp_path, capsys):
    """Every kind refuses the same way. They are four separate code paths."""
    argv = ["install", "--harness", "claude-code", flag, value, "--yes"]
    if flag == "--hooks":
        argv += ["--repo", str(tmp_path)]
    if flag == "--addons":
        argv += ["--wow-addons", str(tmp_path)]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert word in err, err


def test_dry_run_changes_nothing_at_all(home, capsys):
    """"Print the plan, change nothing" is taken literally.

    Deciding between symlink and copy is done by trying it, and the probe used
    to mkdir the target first -- so a dry run left an empty directory tree
    behind. It now probes the nearest directory that already exists, which sits
    on the same filesystem and so gives the same answer.
    """
    assert main(["install", "--harness", "codex", "--skills", "all", "--dry-run"]) == 0
    assert "would" in capsys.readouterr().out
    assert not (home / ".agents").exists(), "a dry run must leave no directory behind"


def test_dry_run_leaves_no_probe_where_it_did_look(home, capsys):
    """The probe walks up to a real directory, so it must clean up there too."""
    assert main(["install", "--harness", "codex", "--skills", "all", "--dry-run"]) == 0
    capsys.readouterr()
    assert list(home.iterdir()) == [], sorted(p.name for p in home.iterdir())


def test_a_directory_we_did_not_install_is_left_alone(home, capsys):
    """Exit 1, and the advice that names the flag which would override it."""
    target = home / ".agents" / "skills" / "wow-api-search"
    target.mkdir(parents=True)
    (target / "SOMETHING.md").write_text("not ours\n", encoding="utf-8")

    rc = main(["install", "--harness", "codex", "--skills", "wow-api-search", "--yes"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "Some entries were left alone" in out
    assert "--force" in out
    assert (target / "SOMETHING.md").is_file(), "it must still be there"


def test_a_harness_with_nowhere_to_put_rules_says_so(home, capsys):
    """Being skipped is the normal outcome for rules, not an edge case."""
    rule = next(iter(_rule_names()))
    assert main(["install", "--harness", "codex", "--rules", rule,
                 "--skills", "none", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "no rules directory" in out, out


def test_every_harness_that_takes_rules_is_still_reachable():
    """Pins the set, so a registry edit cannot quietly shrink what installs."""
    assert {h.key for h in registry.HARNESSES if h.takes_rules} == {
        "claude-code", "cursor", "vscode-copilot", "copilot-cli", "kiro",
    }
