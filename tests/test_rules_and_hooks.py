"""Installing rules and git hooks.

Rules and skills look similar and behave differently in the two ways that
matter: a rule is one file rather than a directory, and its name changes per
harness. Both properties are load-bearing, so both are asserted here rather
than assumed to follow from the skill install working.

Hooks get the same treatment for a blunter reason. A hook that installs into
the wrong repository, or that overwrites one somebody wrote, breaks committing
in a checkout -- and `.git/hooks` is not tracked, so what it overwrote is gone.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wow_tools import hooks as hooks_mod  # noqa: E402
from wow_tools import install as engine  # noqa: E402
from wow_tools import registry  # noqa: E402
from wow_tools import rules as rules_mod  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
#  Discovery and validation
# --------------------------------------------------------------------------

def test_every_rule_is_valid():
    found, problems = rules_mod.discover()
    assert found, "no rules discovered"
    assert problems == []


@pytest.mark.parametrize("rule", rules_mod.discover()[0], ids=lambda r: r.name)
def test_rule_carries_the_frontmatter_each_reader_needs(rule):
    """One file serves three harnesses, and each looks at a different key.

    Claude Code loads a rule with no `paths`; Kiro wants `inclusion: always`;
    Cursor wants `alwaysApply`; Copilot wants `applyTo`. They ignore each
    other's keys, so the union is what makes one file enough.
    """
    text = rule.path.read_text(encoding="utf-8")
    head = text.split("---")[1]
    for key in ("name:", "description:", "inclusion:", "alwaysApply:", "applyTo:"):
        assert key in head, f"{rule.name} frontmatter is missing {key}"
    assert "paths:" not in head, "a `paths` field would stop Claude Code loading it every session"


@pytest.mark.parametrize("rule", rules_mod.discover()[0], ids=lambda r: r.name)
def test_rule_carries_its_ownership_marker(rule):
    """Without it, uninstall cannot tell our file from one written by hand."""
    assert rule.marker() in rule.path.read_text(encoding="utf-8")


def test_rule_filename_follows_the_harness_extension():
    rule = rules_mod.discover()[0][0]
    assert rule.filename(".md") == f"{rule.name}.md"
    assert rule.filename(".mdc") == f"{rule.name}.mdc"
    assert rule.filename(".instructions.md") == f"{rule.name}.instructions.md"


# --------------------------------------------------------------------------
#  The registry's rules entries
# --------------------------------------------------------------------------

def test_only_harnesses_with_a_rules_directory_take_rules():
    """A harness whose instructions are one file the user wrote is left alone.

    Codex, Gemini CLI and Claude Code's own CLAUDE.md all fall in that group.
    Appending to a file somebody wrote is not something an installer does
    without being asked, so those must not appear as rule targets.
    """
    takers = {h.key for h in registry.HARNESSES if h.takes_rules}
    assert "claude-code" in takers
    assert takers.isdisjoint({"codex", "gemini-cli", "aider", "devin"})


def test_cursor_takes_rules_in_project_scope_only():
    """Cursor's global rules live in its UI, not on disk, so there is nothing
    to install into for user scope."""
    cursor = registry.HARNESS_BY_KEY["cursor"]
    assert cursor.rules_project and not cursor.rules_user
    assert cursor.rules_ext == ".mdc", "a plain .md in .cursor/rules is ignored"


def test_every_rules_taker_has_a_note_explaining_its_path():
    for h in registry.HARNESSES:
        if h.takes_rules:
            assert h.rules_note, f"{h.key} installs rules with no explanation of where"


# --------------------------------------------------------------------------
#  Placing a rule
# --------------------------------------------------------------------------

def test_install_uses_the_name_the_harness_wants(tmp_path):
    rule = rules_mod.discover()[0][0]
    for ext in (".md", ".mdc", ".instructions.md"):
        directory = tmp_path / ext.replace(".", "_")
        result = engine.install_rule(
            rule, directory, engine.Method.COPY, filename=rule.filename(ext))
        assert result.outcome is engine.Outcome.COPIED
        assert (directory / f"{rule.name}{ext}").is_file()


def test_install_round_trips(tmp_path):
    rule = rules_mod.discover()[0][0]
    engine.install_rule(rule, tmp_path, engine.Method.COPY)
    assert engine.uninstall_rule(rule, tmp_path).outcome is engine.Outcome.REMOVED
    assert not (tmp_path / rule.filename()).exists()
    assert engine.uninstall_rule(rule, tmp_path).outcome is engine.Outcome.ABSENT


def test_a_file_we_did_not_write_is_left_alone(tmp_path):
    rule = rules_mod.discover()[0][0]
    target = tmp_path / rule.filename()
    target.write_text("# my own rule, same name\n", encoding="utf-8")

    assert engine.install_rule(
        rule, tmp_path, engine.Method.COPY).outcome is engine.Outcome.BLOCKED
    assert target.read_text() == "# my own rule, same name\n"

    assert engine.uninstall_rule(rule, tmp_path).outcome is engine.Outcome.BLOCKED
    assert target.is_file()

    assert engine.install_rule(
        rule, tmp_path, engine.Method.COPY, force=True).outcome is engine.Outcome.UPDATED


def test_a_symlink_survives_a_second_install(tmp_path):
    if not engine.symlinks_available(tmp_path):
        pytest.skip("symlinks unavailable on this filesystem")
    rule = rules_mod.discover()[0][0]
    first = engine.install_rule(rule, tmp_path, engine.Method.SYMLINK)
    assert first.outcome is engine.Outcome.LINKED
    again = engine.install_rule(rule, tmp_path, engine.Method.SYMLINK)
    assert again.outcome is engine.Outcome.CURRENT


def test_dry_run_writes_nothing(tmp_path):
    rule = rules_mod.discover()[0][0]
    result = engine.install_rule(rule, tmp_path, engine.Method.COPY, dry_run=True)
    assert result.outcome is engine.Outcome.PLANNED
    assert not (tmp_path / rule.filename()).exists()


# --------------------------------------------------------------------------
#  Hooks
# --------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def test_every_hook_points_at_a_script_that_exists():
    for hook in hooks_mod.HOOKS:
        assert hook.script.is_file(), f"{hook.name} runs a script that is not here"


def test_the_hook_interpreter_is_not_a_virtualenv(tmp_path):
    """A hook baked to a venv breaks every commit once that venv is gone.

    The test suite runs under `uv run`, so sys.executable here IS a virtualenv
    python -- which makes this the exact condition the helper exists to avoid.
    """
    chosen = Path(hooks_mod.interpreter())
    assert chosen.is_file(), f"{chosen} is not there to run"
    assert ".venv" not in chosen.parts, f"{chosen} would die with the virtualenv"


def test_hook_body_runs_the_chosen_interpreter(repo):
    hook = hooks_mod.HOOK_BY_NAME["ascii-git-text"]
    hooks_mod.install(hook, repo)
    body = (repo / ".git" / "hooks" / hook.event).read_text()
    assert hooks_mod.interpreter() in body
    assert ".venv" not in body


def test_hook_round_trips(repo):
    hook = hooks_mod.HOOK_BY_NAME["ascii-git-text"]
    outcome, target, _ = hooks_mod.install(hook, repo)
    assert outcome == "installed"
    assert target.is_file()
    assert hook.marker() in target.read_text()

    assert hooks_mod.install(hook, repo)[0] == "current"
    assert hooks_mod.uninstall(hook, repo)[0] == "removed"
    assert hooks_mod.uninstall(hook, repo)[0] == "absent"


def test_a_hook_that_does_not_fit_the_repo_is_skipped(repo):
    """`--hooks all` in a repo the hook cannot serve would block every commit.

    The EllesmereUI style check exits non-zero when it cannot find the addon,
    so installing it into an unrelated checkout does not degrade -- it stops
    committing entirely, with an error about a .toc nobody was looking for.
    """
    hook = hooks_mod.HOOK_BY_NAME["eui-style"]
    outcome, target, detail = hooks_mod.install(hook, repo)
    assert outcome == "skipped"
    assert "EllesmereUI" in detail
    assert not target.exists()

    (repo / "EllesmereUI.toc").write_text("## Interface: 110000\n")
    assert hooks_mod.install(hook, repo)[0] == "installed"


def test_a_foreign_hook_is_never_overwritten(repo):
    """.git/hooks is not tracked, so an overwrite is unrecoverable."""
    hook = hooks_mod.HOOK_BY_NAME["ascii-git-text"]
    target = repo / ".git" / "hooks" / hook.event
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("#!/bin/sh\necho mine\n")

    outcome, _, detail = hooks_mod.install(hook, repo)
    assert outcome == "blocked"
    assert "add this line to it" in detail.lower()
    assert target.read_text() == "#!/bin/sh\necho mine\n"

    assert hooks_mod.uninstall(hook, repo)[0] == "blocked"
    assert hooks_mod.install(hook, repo, force=True)[0] == "updated"


def test_hooks_refuse_a_directory_that_is_not_a_repository(tmp_path):
    hook = hooks_mod.HOOK_BY_NAME["ascii-git-text"]
    outcome, _, detail = hooks_mod.install(hook, tmp_path)
    assert outcome == "blocked"
    assert "not a git repository" in detail


def test_status_reports_a_foreign_hook_distinctly(repo):
    hook = hooks_mod.HOOK_BY_NAME["ascii-git-text"]
    target = repo / ".git" / "hooks" / hook.event
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("#!/bin/sh\nexit 0\n")
    states = {h.name: s for h, s in hooks_mod.status(repo)}
    assert states["ascii-git-text"] == f"another {hook.event} hook is here"


def test_resolve_names_rejects_an_unknown_hook():
    assert hooks_mod.resolve_names(["all"]) == list(hooks_mod.HOOKS)
    assert hooks_mod.resolve_names(["none"]) == []
    with pytest.raises(KeyError):
        hooks_mod.resolve_names(["no-such-hook"])
