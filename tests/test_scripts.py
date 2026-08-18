"""The registry of standalone scripts, and that it matches the disk.

The table in wow_tools/scripts.py is the only place that knows where a script
lives. That is worth having, and it is worth a test: a table nobody checks is a
seventh copy of the path arithmetic that happens to be wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wow_tools import scripts  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def on_disk() -> set:
    """Every Python script a caller could reasonably want to load.

    Scoped to the two trees that hold standalone scripts. `wow_tools` itself is
    a package and is imported normally; `references/` under a skill is data.
    """
    found = set()
    for base in (REPO / "skills", REPO / "tools"):
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts or "references" in p.parts:
                continue
            found.add(p.relative_to(REPO))
    return found


@pytest.mark.parametrize("key", sorted(scripts.SCRIPTS), ids=lambda k: k)
def test_every_registered_script_exists(key):
    assert scripts.path(key).is_file(), f"{key} points at nothing"


def test_every_script_on_disk_is_registered():
    """The direction that catches a new script, or a renamed one.

    Without this the table stays green while going stale, and the next caller
    writes its own path because the key it wanted was not there.
    """
    registered = {scripts.path(k).relative_to(REPO) for k in scripts.SCRIPTS}
    missing = on_disk() - registered
    assert not missing, f"not in wow_tools/scripts.py: {sorted(map(str, missing))}"


def test_paths_are_built_from_parts():
    """A separator baked into a key would resolve wrong on Windows."""
    for key, parts in scripts.SCRIPTS.items():
        for part in parts:
            assert "/" not in part and "\\" not in part, f"{key} carries a separator"


def test_loading_twice_gives_the_same_module():
    """The cache. build_index.py is 1789 lines and was re-executed per call."""
    assert scripts.load("eui_build_index") is scripts.load("eui_build_index")


def test_a_loaded_script_is_registered_before_it_runs():
    """Not cosmetic, and only reproducible on the oldest Python we support.

    With `from __future__ import annotations` a dataclass resolves its string
    annotations through sys.modules[cls.__module__]. On 3.9 a module absent
    from there fails with `'NoneType' object has no attribute '__dict__'` --
    at import time, so the whole script is unusable. tools/lint/ascii_text.py
    is the file that combines the two.
    """
    assert scripts.load("ascii_text") is sys.modules["ascii_text"]


def test_a_script_that_fails_to_run_is_not_left_registered(monkeypatch, tmp_path):
    """A half-executed module in sys.modules would be found by the next import."""
    broken = tmp_path / "broken.py"
    broken.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    monkeypatch.setattr(scripts, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(scripts.SCRIPTS, "broken", ("broken.py",))
    with pytest.raises(RuntimeError):
        scripts.load("broken")
    assert "broken" not in sys.modules


def test_an_unknown_key_names_the_known_ones():
    with pytest.raises(KeyError) as e:
        scripts.path("build_index")
    assert "eui_build_index" in str(e.value)


def test_a_registered_script_that_is_missing_says_so(monkeypatch):
    """What the six hand-rolled copies got wrong.

    Five of them let spec_from_file_location return None and died on the next
    line with `'NoneType' object has no attribute 'exec_module'`, naming
    neither the script nor the path it looked in.
    """
    monkeypatch.setitem(scripts.SCRIPTS, "ghost", ("tools", "lint", "not_here.py"))
    with pytest.raises(FileNotFoundError) as e:
        scripts.load("ghost")
    assert "ghost" in str(e.value) and "not_here.py" in str(e.value)
