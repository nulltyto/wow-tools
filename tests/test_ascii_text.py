"""The ASCII gate on commit messages and pull request text.

Two things are worth testing harder than the rest. The command parser, because
its whole value is precision -- a gate that fires on a path or a branch name
gets switched off within a day. And the round trip through a real git hook,
because every layer in between (the template, the marker, the exit code) only
matters if a commit actually stops.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def at(ascii_text):
    """The gate under test. `ascii_text` is the fixture in conftest.py."""
    return ascii_text


# --------------------------------------------------------------------------
#  Scanning
# --------------------------------------------------------------------------

def test_plain_ascii_is_clean(at):
    assert at.scan("Add the gate, and say what it does not cover") == []


@pytest.mark.parametrize("ch, swap", [
    ("—", "--"), ("–", "-"), ("’", "'"),
    ("“", '"'), ("…", "..."), (" ", " "),
])
def test_the_usual_offenders_have_an_ascii_form(at, ch, swap):
    """The characters a model actually reaches for, and what to write instead."""
    assert at.REPLACEMENTS[ch] == swap
    assert at.fix(f"a{ch}b") == f"a{swap}b"


def test_offence_reports_where_and_what(at):
    found = at.scan("ok\nno — here")
    assert len(found) == 1
    assert (found[0].line, found[0].col) == (2, 4)
    assert found[0].codepoint == "U+2014"
    assert "EM DASH" in found[0].name


def test_replacement_table_agrees_with_the_lua_one(at, style_checker):
    """The Lua source rule carries the same table; drift between them is a bug.

    They are duplicated on purpose -- this script runs as a hook in another
    repository and must not import across checkouts -- so the coupling is
    asserted here instead of in the code. See ADR-0001.
    """
    shared = set(at.REPLACEMENTS) & set(style_checker.REPLACEMENTS)
    assert shared, "the two tables no longer overlap at all"
    for ch in shared:
        assert at.REPLACEMENTS[ch] == style_checker.REPLACEMENTS[ch], f"{ch!r} disagrees"


# --------------------------------------------------------------------------
#  Commit message bodies
# --------------------------------------------------------------------------

def test_commit_body_drops_comments_and_the_diff(at):
    """git discards these, so flagging them would report the author's own file."""
    text = (
        "Subject line\n"
        "# a comment git will strip — not part of the message\n"
        "\n"
        "Body.\n"
        "# ------------------------ >8 ------------------------\n"
        "diff --git a/x b/x\n"
        "+local s = \"café\"\n"
    )
    body = at.commit_body(text)
    assert at.scan(body) == []
    assert "Body." in body


# --------------------------------------------------------------------------
#  Pulling the message back out of a command line
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    'git commit -m "Subject — bad"',
    "git commit --message='Subject — bad'",
    'gh pr create --title "ok" --body "Body — bad"',
    'gh pr comment 12 --body "Comment — bad"',
    'gh issue create -t "ok" -b "Body — bad"',
    'gh release create v1 --notes "Notes — bad"',
])
def test_message_flags_are_read(at, command):
    parts = at.message_parts(command)
    assert any(at.scan(text) for _, text in parts), command


@pytest.mark.parametrize("command", [
    "ls /home/derek/café",
    "rg 'café' src/",
    'echo "an em dash — in a plain echo"',
])
def test_commands_that_carry_no_git_text_are_ignored(at, command):
    """The gate has to be invisible for everything that is not a message."""
    assert at.message_parts(command) == []


def test_non_ascii_outside_the_message_does_not_fire(at):
    """A path or a branch name is not text anyone reads back out of the log."""
    assert not any(
        at.scan(text)
        for _, text in at.message_parts('git commit -m "Plain subject" -- src/café.lua')
    )


def test_heredoc_bodies_are_read(at):
    """The shape an agent uses whenever a PR body has more than one line."""
    command = (
        "gh pr create --title \"ok\" --body \"$(cat <<'EOF'\n"
        "First line.\n"
        "Second line — with a dash.\n"
        "EOF\n"
        ")\""
    )
    parts = at.message_parts(command)
    assert any(at.scan(text) for _, text in parts)


def test_body_file_is_not_read_as_text(at):
    """--body-file names a file, which gets linted as a file rather than here."""
    parts = at.message_parts("gh pr create --title ok --body-file notes.md")
    assert all(label != "--body-file" for label, _ in parts)


# --------------------------------------------------------------------------
#  Exit codes
# --------------------------------------------------------------------------

def _run(*args, stdin: str = ""):
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "lint" / "ascii_text.py"), *args],
        input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_clean_text_exits_zero():
    assert _run("--text", "All ASCII here").returncode == 0


def test_dirty_text_exits_one():
    assert _run("--text", "Not — ASCII").returncode == 1


def test_hook_json_exits_two_to_block():
    """PreToolUse reads 2 as "block this call", which is why it is not 1."""
    event = '{"tool_name":"Bash","tool_input":{"command":"git commit -m \\"a \\u2014 b\\""}}'
    proc = _run("--hook-json", stdin=event)
    assert proc.returncode == 2
    assert "EM DASH" in proc.stderr


def test_hook_json_ignores_other_tools():
    proc = _run("--hook-json", stdin='{"tool_name":"Read","tool_input":{"file_path":"/café"}}')
    assert proc.returncode == 0


@pytest.mark.skipif(not Path("/bin/bash").is_file(), reason="needs bash for PIPESTATUS")
def test_a_truncated_report_still_fails():
    """`... | head` must not turn the check into one that always passes.

    The report goes to stderr, so a consumer that closes the pipe early raises
    BrokenPipeError midway through printing. Exiting 0 from that handler is the
    quiet failure this asserts against.
    """
    script = str(REPO / "tools" / "lint" / "ascii_text.py").replace("\\", "/")
    codes = subprocess.run(
        f'"{sys.executable}" "{script}" --text "bad — dash" 2>&1 '
        '| head -2 >/dev/null; echo ${PIPESTATUS[0]}',
        shell=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", executable="/bin/bash",
    )
    assert codes.stdout.strip() == "1", "the checker must still report failure"


def test_hook_json_survives_input_that_is_not_json():
    """A hook that crashes on a surprise blocks every Bash call in the session."""
    assert _run("--hook-json", stdin="not json at all").returncode == 0


# --------------------------------------------------------------------------
#  The git hook, end to end
# --------------------------------------------------------------------------

@pytest.fixture()
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    (tmp_path / "a.txt").write_text("hi\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    return tmp_path


def _commit(repo: Path, message: str, *extra: str):
    """Commit `message`, passed through a UTF-8 file rather than argv.

    `git commit -m` sends the message through the process command line, which
    Windows re-encodes in the active code page -- so an em dash written here
    is not necessarily the em dash git stores, and a test asserting that the
    gate rejects one would fail for a reason unrelated to the gate.
    """
    path = repo / ".git" / "TEST_MSG"
    path.write_text(message, encoding="utf-8")
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-F", str(path), *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_installed_hook_blocks_and_allows(repo):
    installed = _run("--install-hook", str(repo))
    assert installed.returncode == 0
    assert (repo / ".git" / "hooks" / "commit-msg").is_file()

    rejected = _commit(repo, "Add a thing — properly")
    assert rejected.returncode != 0
    assert "EM DASH" in rejected.stderr
    assert not _log(repo)

    assert _commit(repo, "Add a thing, properly").returncode == 0
    assert _log(repo) == ["Add a thing, properly"]


def test_installed_hook_can_be_bypassed(repo):
    """--no-verify has to keep working: this is a lint, not a security control."""
    _run("--install-hook", str(repo))
    assert _commit(repo, "Bypassed — on purpose", "--no-verify").returncode == 0
    assert _log(repo) == ["Bypassed — on purpose"]


def test_install_leaves_a_foreign_hook_alone(repo):
    """.git/hooks is not tracked, so overwriting it destroys unrecoverable work."""
    hook = repo / ".git" / "hooks"
    hook.mkdir(parents=True, exist_ok=True)
    (hook / "commit-msg").write_text("#!/bin/sh\nexit 0\n")
    proc = _run("--install-hook", str(repo))
    assert proc.returncode == 1
    assert "leaving it alone" in proc.stdout
    assert (hook / "commit-msg").read_text() == "#!/bin/sh\nexit 0\n"


def test_range_mode_reads_every_message_in_the_range(repo):
    _commit(repo, "First subject")
    (repo / "b.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    _commit(repo, "Second — subject", "--no-verify")

    proc = subprocess.run(
        ["python3", str(REPO / "tools" / "lint" / "ascii_text.py"),
         "--root", str(repo), "--range", "HEAD~1..HEAD"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "EM DASH" in proc.stderr


def _log(repo: Path) -> list:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    return out.splitlines() if out else []
