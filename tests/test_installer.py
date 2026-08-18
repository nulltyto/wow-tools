"""Installer behaviour, exercised against a fake HOME.

Everything here runs in tmp_path, so a test run never touches the real
~/.claude/skills or any other live harness directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wow_tools import install as engine  # noqa: E402
from wow_tools import registry  # noqa: E402
from wow_tools import skills as skills_mod
from wow_tools.__main__ import main, plan  # noqa: E402
from wow_tools.install import Method, Outcome  # noqa: E402


@pytest.fixture
def skill():
    found, _ = skills_mod.discover()
    return found[0]


# --------------------------------------------------------------------------
#  Registry
# --------------------------------------------------------------------------

def test_harness_keys_are_unique():
    keys = [h.key for h in registry.HARNESSES]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("harness", registry.HARNESSES, ids=lambda h: h.key)
def test_every_harness_is_documented(harness):
    """A path nobody can trace back to a source is a path nobody can fix."""
    assert harness.docs, f"{harness.key} has no docs URL"
    assert harness.name


@pytest.mark.parametrize("harness", registry.HARNESSES, ids=lambda h: h.key)
def test_paths_are_relative_and_posix(harness):
    """Paths must be home- or project-relative so pathlib can localise them.

    An absolute POSIX path or a backslash here would silently produce a
    nonsense target on Windows.
    """
    for p in harness.skills_user + harness.skills_project:
        assert not p.startswith(("/", "~")), f"{harness.key}: {p!r} is not relative"
        assert "\\" not in p, f"{harness.key}: {p!r} uses a backslash"
        assert p.endswith("skills"), f"{harness.key}: {p!r} is not a skills directory"


@pytest.mark.parametrize("harness", registry.HARNESSES, ids=lambda h: h.key)
def test_uninstallable_harnesses_explain_themselves(harness):
    if not harness.installable:
        assert harness.note, f"{harness.key} installs nowhere but gives no reason"


def test_registry_covers_the_standard_path():
    """The cross-agent path must be reachable on its own.

    Someone using a spec-compliant harness this registry has never heard of
    needs a way to install without waiting for an entry to be added.
    """
    generic = registry.get("agents-standard")
    assert generic.skills_user == (registry.AGENTS_USER,)


# --------------------------------------------------------------------------
#  Planning and deduplication
# --------------------------------------------------------------------------

def test_harnesses_sharing_a_directory_are_collapsed():
    """Selecting eight agents that all read ~/.agents/skills is one install."""
    picked = [registry.get(k) for k in ("codex", "cursor", "gemini-cli", "openhands", "roo-code")]
    groups, skipped = plan(picked, "user", None)
    assert not skipped
    assert len(groups) == 1, "these five all read the cross-agent path"
    assert len(next(iter(groups.values()))) == 5


def test_claude_code_gets_its_own_directory():
    picked = [registry.get(k) for k in ("codex", "claude-code")]
    groups, _ = plan(picked, "user", None)
    assert len(groups) == 2, "Claude Code does not read ~/.agents/skills"


def test_harness_with_no_user_directory_is_skipped_not_crashed():
    groups, skipped = plan([registry.get("aider"), registry.get("devin")], "user", None)
    assert not groups
    assert {h.key for h in skipped} == {"aider", "devin"}


def test_devin_installs_at_project_scope(tmp_path):
    groups, skipped = plan([registry.get("devin")], "project", tmp_path)
    assert not skipped
    assert list(groups) == [tmp_path / ".agents" / "skills"]


# --------------------------------------------------------------------------
#  Install engine
# --------------------------------------------------------------------------

def test_symlink_install_and_idempotence(tmp_path, skill):
    if not engine.symlinks_available(tmp_path):
        pytest.skip("symlinks unavailable on this filesystem")
    d = tmp_path / "skills"
    first = engine.install_skill(skill, d, Method.SYMLINK)
    assert first.outcome is Outcome.LINKED
    assert (d / skill.name / "SKILL.md").is_file()

    again = engine.install_skill(skill, d, Method.SYMLINK)
    assert again.outcome is Outcome.CURRENT, "re-running must not churn"


def test_copy_install_is_self_contained(tmp_path, skill):
    d = tmp_path / "skills"
    r = engine.install_skill(skill, d, Method.COPY)
    assert r.outcome is Outcome.COPIED
    assert not (d / skill.name).is_symlink()
    assert (d / skill.name / "SKILL.md").is_file()
    assert not (d / skill.name / "__pycache__").exists()


def test_copy_refreshes_without_force(tmp_path, skill):
    d = tmp_path / "skills"
    engine.install_skill(skill, d, Method.COPY)
    r = engine.install_skill(skill, d, Method.COPY)
    assert r.outcome is Outcome.UPDATED


def test_foreign_directory_is_never_clobbered(tmp_path, skill):
    d = tmp_path / "skills"
    victim = d / skill.name
    victim.mkdir(parents=True)
    (victim / "SKILL.md").write_text("someone else's skill", encoding="utf-8")

    r = engine.install_skill(skill, d, Method.COPY)
    assert r.outcome is Outcome.BLOCKED
    assert (victim / "SKILL.md").read_text(encoding="utf-8") == "someone else's skill"

    forced = engine.install_skill(skill, d, Method.COPY, force=True)
    assert forced.outcome is Outcome.UPDATED


def test_foreign_symlink_is_never_clobbered(tmp_path, skill):
    if not engine.symlinks_available(tmp_path):
        pytest.skip("symlinks unavailable on this filesystem")
    d = tmp_path / "skills"
    d.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (d / skill.name).symlink_to(elsewhere, target_is_directory=True)

    r = engine.install_skill(skill, d, Method.SYMLINK)
    assert r.outcome is Outcome.BLOCKED


def test_dry_run_changes_nothing(tmp_path, skill):
    d = tmp_path / "skills"
    r = engine.install_skill(skill, d, Method.COPY, dry_run=True)
    assert r.outcome is Outcome.PLANNED
    assert not (d / skill.name).exists()


def test_uninstall_removes_only_our_own(tmp_path, skill):
    d = tmp_path / "skills"
    engine.install_skill(skill, d, Method.COPY)
    assert engine.uninstall_skill(skill, d).outcome is Outcome.REMOVED
    assert not (d / skill.name).exists()
    assert engine.uninstall_skill(skill, d).outcome is Outcome.ABSENT


def test_uninstall_leaves_foreign_directories_alone(tmp_path, skill):
    d = tmp_path / "skills"
    (d / skill.name).mkdir(parents=True)
    (d / skill.name / "SKILL.md").write_text("not ours", encoding="utf-8")
    assert engine.uninstall_skill(skill, d).outcome is Outcome.BLOCKED
    assert (d / skill.name / "SKILL.md").is_file()


# --------------------------------------------------------------------------
#  CLI
# --------------------------------------------------------------------------

def test_cli_install_into_fake_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    rc = main(["install", "--harness", "codex,kiro", "--skills", "all", "--yes"])
    assert rc == 0
    for d in (tmp_path / ".agents" / "skills", tmp_path / ".kiro" / "skills"):
        assert (d / "wow-api-search" / "SKILL.md").is_file()

    rc = main(["uninstall", "--harness", "codex,kiro", "--skills", "all", "--yes"])
    assert rc == 0
    assert not (tmp_path / ".agents" / "skills" / "wow-api-search").exists()
    capsys.readouterr()


def test_cli_rejects_unknown_harness(capsys):
    assert main(["install", "--harness", "not-a-real-harness", "--skills", "all", "--yes"]) == 2
    assert "unknown harness" in capsys.readouterr().err


def test_cli_rejects_unknown_skill(capsys):
    assert main(["install", "--harness", "codex", "--skills", "nope", "--yes"]) == 2
    assert "unknown skill" in capsys.readouterr().err


def test_cli_list_runs_clean(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "wow-api-search" in out
    for h in registry.HARNESSES:
        assert h.key in out


# --------------------------------------------------------------------------
#  CLI entry ergonomics
# --------------------------------------------------------------------------

def test_install_options_work_without_naming_the_subcommand():
    """`install.sh --harness x --skills y` is what the README documents.

    Without a default, argparse reads the first flag's *value* as the
    subcommand and reports an invalid choice, naming the wrong thing entirely.
    """
    from wow_tools.__main__ import _with_default_command

    assert _with_default_command(["--harness", "codex"]) == ["install", "--harness", "codex"]
    assert _with_default_command([]) == ["install"]


def test_an_explicit_subcommand_is_left_alone():
    from wow_tools.__main__ import _with_default_command

    for cmd in ("install", "uninstall", "list", "status", "doctor"):
        assert _with_default_command([cmd, "--yes"]) == [cmd, "--yes"]
    # Top-level help must stay top-level, not become `install --help`.
    assert _with_default_command(["--help"]) == ["--help"]


def test_a_near_miss_harness_key_is_suggested():
    """The keys are hyphenated and the names are not, so short forms are the
    common typo -- and a wall of twenty keys does not answer which was meant."""
    from wow_tools import registry

    with pytest.raises(KeyError) as excinfo:
        registry.get("claude")
    assert "claude-code" in excinfo.value.args[0]

    assert registry.suggest("gemini") == ["gemini-cli"]
    assert registry.suggest("vscode") == ["vscode-copilot"]
    # A short key must not match on being a substring of an unrelated word.
    assert "pi" not in registry.suggest("copilot")


def test_harness_keys_are_case_insensitive():
    from wow_tools import registry

    assert registry.get("Cursor").key == "cursor"
    assert registry.get(" CLAUDE-CODE ").key == "claude-code"


def test_an_unrecognisable_key_still_lists_them_all():
    from wow_tools import registry

    with pytest.raises(KeyError) as excinfo:
        registry.get("zzzz")
    assert "Known:" in excinfo.value.args[0]


def test_restart_advice_only_appears_when_something_moved(tmp_path, capsys):
    """Advice to restart is advice only when there is something to pick up.

    Printing it after a run that changed nothing invites a pointless restart
    and makes a no-op read as work.
    """
    argv = ["install", "--harness", "kiro", "--skills", "all",
            "--scope", "project", "--project-root", str(tmp_path), "--yes"]

    assert main(argv) == 0
    first = capsys.readouterr().out
    assert "Restart your harness" in first

    assert main(argv) == 0
    second = capsys.readouterr().out
    assert "Restart your harness" not in second
    assert "already in place" in second


def test_a_single_directory_is_named_once(tmp_path, capsys):
    """The plan and the results used to print the same heading twice, which for
    one directory -- the common case -- said everything two times."""
    main(["install", "--harness", "kiro", "--skills", "all",
          "--scope", "project", "--project-root", str(tmp_path), "--yes"])
    out = capsys.readouterr().out
    directory = str(tmp_path / ".kiro" / "skills")
    assert out.count(f"\n{directory}\n") == 1
