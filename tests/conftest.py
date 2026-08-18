"""What every test module needs before it can import anything of ours.

`wow_tools` is at the repository root rather than on the path, and conftest
runs before the test modules do, so the insert happens once here instead of at
the top of each of them.

The fixtures below are for the scripts that are used as fixtures. Loading is
already cached in wow_tools.scripts, so a fixture buys naming, not speed --
which is why there is not one per registered script.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from wow_tools import scripts  # noqa: E402


@pytest.fixture(scope="session")
def ascii_text():
    """tools/lint/ascii_text.py -- the ASCII gate on commit messages."""
    return scripts.load("ascii_text")


@pytest.fixture(scope="session")
def style_checker():
    """The EllesmereUI style checker, which the PR gate and the hook both run."""
    return scripts.load("eui_check_style")
