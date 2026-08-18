# ADR-0001: Skill scripts do not share code

Status: accepted
Date: 2026-08-17

## Context

Every script under `skills/` is stdlib-only, and `pyproject.toml` records why:

> Every script under `skills/` is stdlib-only so it can be run directly by an
> agent harness that knows nothing about this project -- `python3 <skill>/
> scripts/x.py` has to work from a bare clone, on a machine with no uv and no
> virtualenv.

There is a second reason the comment does not state. The installer places one
skill directory at a time (`wow_tools/install.py`, `install_item`), by symlink
or by copy. A skill that imported a module from outside its own directory would
be a dangling reference the moment it was installed anywhere.

Two scripts carry the same constraint from a third direction: `hooks.py`
installs `tools/lint/ascii_text.py` and `ellesmereui-pr-check/scripts/check_style.py`
as git hooks *into other repositories*, where nothing of this project is on the
path at all.

The visible cost is duplication. `mask_lua`, `_long_bracket_len` and `_blank`
exist twice, byte-identical except for their comments, in
`ellesmereui-search/scripts/build_index.py` and
`ellesmereui-pr-check/scripts/check_style.py`. The `REPLACEMENTS` table exists
twice, in `check_style.py` and `tools/lint/ascii_text.py`. Both pairs look like
an obvious extraction.

## Decision

Skill scripts do not import each other, and do not import `wow_tools`.
Duplication across them is deliberate, and is held together by tests that
assert the copies agree rather than by an import.

Two consequences that are easy to undo by accident:

1. **`wow_tools/scripts.py` does no `sys.path` work.** The scripts insert their
   own directory before importing a sibling (`query.py`, both
   `validate_index.py`). Python already puts a script's directory on
   `sys.path[0]` for direct execution, so those inserts look redundant -- and
   they are, right up until Python runs with `-P` or `PYTHONSAFEPATH=1`, which
   is precisely the unknown-harness case this ADR exists for. The loader could
   absorb them and everything would still pass; the guarantee would be gone.

2. **The duplication is not a defect to be cleaned up.** A reviewer who
   extracts a shared `lua_mask` module has not removed complexity, only moved
   it into the installer, which would then have to know that two skill
   directories travel together.

## Consequences

Callers that need a script load it by path through `wow_tools/scripts.py`,
which is allowed to import the package because it is not itself a skill script.
`tools/lint/lua_comments.py` may use it: it is not installed as a hook and only
ever runs from a full checkout.

Where code is duplicated across skills, an agreement test asserts the copies
produce identical output over a real corpus, rather than sampling a handful of
cases by hand.

## Alternatives rejected

**Vendor a shared module into each skill at build time.** Adds a build step to
a repo whose entire premise is that the scripts run from a bare clone.

**Make the skills a package and have the installer place the package too.**
Changes the unit of installation from a directory to a directory plus its
dependencies, for two functions and one table.
