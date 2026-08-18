"""Where this repo's standalone scripts live, and how to load one.

Every script under `skills/` is stdlib-only and standalone, because an
installed skill directory travels on its own: the installer copies one
directory at a time, so a skill that imported a shared module would be a
dangling reference the moment it landed. `tools/lint/ascii_text.py` and
`check_style.py` carry the same constraint for a different reason -- they are
installed as git hooks into other repositories.

Nothing can therefore `import` them, and everything that needs one loads it by
path. That was six hand-rolled copies of the same five lines of importlib,
each with its own path arithmetic starting from a different directory, plus two
more paths hard-coded in hooks.py. This is the one place that knows.

Note for anyone tidying up later: `load` deliberately does no `sys.path` work.
The scripts insert their own directory before importing a sibling, and that
insert is what holds when Python is run with `-P` or PYTHONSAFEPATH=1, which is
exactly the unknown-harness case the standalone rule exists for. See
docs/adr/0001-skill-scripts-do-not-share-code.md.
"""

from __future__ import annotations

import importlib.util
import sys
from functools import cache
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

# Key -> path parts, relative to the repository root. Built from parts rather
# than a "a/b/c" string so the separator is the platform's.
#
# Two files are named query.py and two more validate_index.py, so a bare
# filename cannot be the key. The prefixes say which skill owns each.
SCRIPTS: dict[str, tuple[str, ...]] = {
    "eui_build_addon_list": ("skills", "ellesmereui-pr-check", "scripts", "build_addon_list.py"),
    "eui_check_style": ("skills", "ellesmereui-pr-check", "scripts", "check_style.py"),
    "eui_build_index": ("skills", "ellesmereui-search", "scripts", "build_index.py"),
    "eui_query": ("skills", "ellesmereui-search", "scripts", "query.py"),
    "eui_validate_index": ("skills", "ellesmereui-search", "scripts", "validate_index.py"),
    "eui_new_tracer": ("skills", "eui-addon-debug", "scripts", "new_tracer.py"),
    "api_generate_index": ("skills", "wow-api-search", "scripts", "generate_index.py"),
    "api_query": ("skills", "wow-api-search", "scripts", "query.py"),
    "api_validate_index": ("skills", "wow-api-search", "scripts", "validate_index.py"),
    "secret_fields": ("skills", "wow-secret-values", "scripts", "secret_fields.py"),
    "ascii_text": ("tools", "lint", "ascii_text.py"),
    "lua_comments": ("tools", "lint", "lua_comments.py"),
    "euidiag_perf": ("tools", "perf", "euidiag-perf.py"),
}


def path(key: str) -> Path:
    """Where the script with this key lives.

    Answered from the table alone, so a caller that only needs the path -- the
    git hooks, which run the script rather than importing it -- pays nothing.
    """
    try:
        parts = SCRIPTS[key]
    except KeyError:
        raise KeyError(
            f"unknown script {key!r}. Known: {', '.join(sorted(SCRIPTS))}"
        ) from None
    return REPO_ROOT.joinpath(*parts)


@cache
def load(key: str) -> ModuleType:
    """The script with this key, executed and returned as a module.

    Cached: build_index.py is 1789 lines and was being re-executed a dozen
    times per test run. Callers therefore share one module object, so a test
    that needs to rebind an attribute on it must use monkeypatch, which puts
    it back afterwards.

    Registered under the key rather than the script's bare name. The scripts
    use bare names among themselves -- query.py does `import build_index` --
    and claiming those would collide with the fakes the tests install there.
    """
    target = path(key)
    if not target.is_file():
        raise FileNotFoundError(f"{key} is registered at {target}, which does not exist")
    spec = importlib.util.spec_from_file_location(key, target)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {key} from {target}")
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs, which is not optional. With `from __future__
    # import annotations` every annotation is a string, and on Python 3.9
    # dataclasses resolves those through sys.modules[cls.__module__]; a module
    # missing from there dies with an AttributeError on NoneType.
    sys.modules[key] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[key]
        raise
    return module
