"""`--json`: the same run, said to a program instead of a person.

The install drivers used to decide and print in one breath, so the only way to
ask what a run did was to read its prose. The decisions are data now, and prose
is one renderer over them. This is the other.

Written for a caller that is an agent rather than a shell script: it wants to
know what was chosen, where each thing went, what happened to it, and which
harnesses were passed over and why -- without parsing sentences.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wow_tools import report
from wow_tools.__main__ import main


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def run_json(capsys, *argv) -> dict:
    rc = main(["install", "--json", "--yes", *argv])
    out = capsys.readouterr().out
    return rc, json.loads(out)


def test_the_whole_run_is_one_document(home, capsys):
    rc, doc = run_json(capsys, "--harness", "claude-code", "--skills", "wow-api-search")
    assert rc == 0
    assert doc["ok"] is True
    assert [s["kind"] for s in doc["sections"]] == ["skill"]


def test_nothing_but_json_reaches_stdout(home, capsys):
    """A stray print would make the document unparseable, which is the failure
    mode worth a test: it is silent until something downstream chokes."""
    main(["install", "--json", "--yes", "--harness", "codex", "--skills", "all"])
    out = capsys.readouterr().out
    json.loads(out)  # raises if any prose leaked in
    # The advice string is legitimately inside the document; the prose that
    # must not appear is the human framing around it.
    assert "Installing 7 skill(s)" not in out
    assert "  for: OpenAI Codex" not in out


def test_several_kinds_in_one_run_are_several_sections(home, capsys):
    rc, doc = run_json(capsys, "--harness", "claude-code",
                       "--skills", "wow-api-search", "--rules", "ascii-git-text")
    assert rc == 0
    assert [s["kind"] for s in doc["sections"]] == ["skill", "rule"]


def test_a_target_says_where_and_how(home, capsys):
    _, doc = run_json(capsys, "--harness", "claude-code", "--skills", "wow-api-search")
    target = doc["sections"][0]["targets"][0]
    # Compared as a path, not as a string: the separator is the platform's, and
    # asserting on "/" passes everywhere except the one OS worth testing on.
    assert Path(target["directory"]) == home / ".claude" / "skills"
    assert target["harnesses"] == ["Claude Code"]
    assert target["method"] in ("symlink", "copy")


def test_a_rule_target_carries_the_extension_it_installs_as(home, capsys):
    """The fact that makes rules group differently from skills."""
    _, doc = run_json(capsys, "--harness", "claude-code,copilot-cli",
                      "--rules", "ascii-git-text", "--skills", "none")
    exts = {t["extension"] for t in doc["sections"][0]["targets"]}
    assert exts == {".md", ".instructions.md"}


def test_a_skipped_harness_says_why(home, capsys):
    """Being skipped is the normal outcome for rules, so it is data, not prose."""
    _, doc = run_json(capsys, "--harness", "codex", "--rules", "ascii-git-text",
                      "--skills", "none")
    skipped = doc["sections"][0]["skipped"]
    assert skipped[0]["harness"] == "OpenAI Codex"
    assert "no rules directory" in skipped[0]["reason"]


def test_results_carry_the_outcome_of_each_placement(home, capsys):
    _, doc = run_json(capsys, "--harness", "claude-code", "--skills", "wow-api-search")
    row = doc["sections"][0]["results"][0]
    assert row["item"] == "wow-api-search"
    assert row["outcome"] in ("linked", "copied")
    assert row["ok"] is True


def test_a_second_identical_run_reports_current(home, capsys):
    run_json(capsys, "--harness", "claude-code", "--skills", "wow-api-search")
    _, doc = run_json(capsys, "--harness", "claude-code", "--skills", "wow-api-search")
    assert doc["sections"][0]["results"][0]["outcome"] == "current"


def test_a_blocked_placement_is_not_ok(home, capsys):
    """Exit code and document agree. A caller may read either."""
    target = home / ".claude" / "skills" / "wow-api-search"
    target.mkdir(parents=True)
    (target / "SOMETHING.md").write_text("not ours\n", encoding="utf-8")

    rc, doc = run_json(capsys, "--harness", "claude-code", "--skills", "wow-api-search")
    assert rc == 1
    assert doc["ok"] is False
    assert doc["sections"][0]["ok"] is False
    row = doc["sections"][0]["results"][0]
    assert row["outcome"] == "blocked"
    assert row["ok"] is False
    assert "--force" in row["detail"]


def test_dry_run_reports_a_plan_with_nothing_done(home, capsys):
    rc = main(["install", "--json", "--dry-run",
               "--harness", "claude-code", "--skills", "wow-api-search"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert doc["sections"][0]["results"][0]["outcome"] == "planned"
    assert "advice" not in doc["sections"][0], "nothing moved, so nothing to advise"
    assert not (home / ".claude").exists()


def test_advice_is_present_only_when_something_moved(home, capsys):
    _, first = run_json(capsys, "--harness", "claude-code", "--skills", "wow-api-search")
    assert "Restart your harness" in first["sections"][0]["advice"]

    _, second = run_json(capsys, "--harness", "claude-code", "--skills", "wow-api-search")
    assert "already in place" in second["sections"][0]["advice"]


def test_hooks_report_too(home, tmp_path, capsys):
    """Hooks are placed by their own code and still land in the document."""
    import subprocess

    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    rc, doc = run_json(capsys, "--hooks", "ascii-git-text", "--repo", str(repo))
    assert rc == 0
    section = doc["sections"][0]
    assert section["kind"] == "hook"
    assert section["results"][0]["item"] == "ascii-git-text"
    assert section["results"][0]["outcome"] == "installed"


# --------------------------------------------------------------------------
#  The document shape itself
# --------------------------------------------------------------------------

def test_a_section_omits_what_it_has_nothing_to_say_about():
    """Absent beats null: a consumer can test for a key rather than for None."""
    doc = report.Section(noun="rule", action="install").document()
    assert "advice" not in doc and "skipped" not in doc and "scope" not in doc
    assert doc["kind"] == "rule" and doc["results"] == []


def test_an_empty_run_is_still_a_valid_document():
    assert report.Run().document() == {"ok": True, "sections": []}


def test_paths_survive_as_strings():
    section = report.Section(
        noun="skill", action="install",
        targets=(report.Target(directory=Path("/a/b")),),
        rows=(report.Row(item="x", target=Path("/a/b/x"), outcome="linked"),),
    )
    doc = section.document()
    assert doc["targets"][0]["directory"] == str(Path("/a/b"))
    assert doc["results"][0]["target"] == str(Path("/a/b/x"))
    json.dumps(doc)
