"""One kind of installable thing, and what can be done with a selection of it.

Skills, rules, addons and hooks are four kinds wearing four coats. Each got its
own module, and each module re-derived the same rules: what `all` and `none`
mean, how a name that matches nothing is reported, what counts as a change
worth mentioning afterwards. `resolve_names` was written four times, near
character-identical, and had already drifted once.

A Catalogue is one kind: the noun, where its members come from, where they go,
and what "nothing named" means for it. What varies between kinds lives in the
four instances; what does not lives here, once.

Terminal input and output stay in __main__. This module decides; it does not
ask and it does not print.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from . import addons as addons_mod
from . import hooks as hooks_mod
from . import install as engine
from . import registry
from . import rules as rules_mod
from . import skills as skills_mod
from .errors import UnknownName
from .install import Outcome


class WhenUnnamed(Enum):
    """What "nothing named, and no terminal to ask" means for a kind.

    The three kinds answer this differently on purpose. A skill costs nothing
    until the model decides it is relevant, so installing all of them is a fair
    reading of silence. A rule loads into every session in every repository,
    which is a larger claim on somebody's setup than silence can authorise. An
    addon goes into a game directory, and "everything" there means writing into
    a folder nobody named an addon for.
    """

    ALL = "all"
    NONE = "none"
    REFUSE = "refuse"


def split_specs(values) -> list[str]:
    """`--skills a,b --skills c` is three names, however it was spelled."""
    return [s for spec in values or () for s in spec.replace(",", " ").split()]


def _itself(group_key):
    return group_key


@dataclass(frozen=True)
class Catalogue:
    """One installable kind."""

    noun: str
    discover: Callable
    when_unnamed: WhenUnnamed
    # How a member is put in place and taken away again. None for a kind that
    # shares only the naming rules: a hook is a generated shell script written
    # into a repository's .git/hooks, which is a different job from linking a
    # directory, and forcing it through this seam would mean a placement
    # interface three kinds ignore half of.
    place: Callable = None
    unplace: Callable = None
    # How harnesses are grouped into install targets. None for a kind whose
    # destination comes from a flag rather than from a harness: nothing reads
    # an addon on a harness's behalf, so there is nothing to group.
    plan: Callable = None
    # Said once at the end of a run, chosen by whether anything actually moved.
    # Empty for a kind that has nothing to say: a rule is read at the start of
    # the next session, so there is nothing to restart and nothing to advise.
    moved_advice: str = ""
    unchanged_advice: str = ""
    # A group key is opaque to the placement loop, but a caller reporting on a
    # group needs the directory out of it. Skills group by directory alone and
    # rules by directory and extension, so only rules override this.
    directory_of: Callable = _itself
    # Only read when when_unnamed is REFUSE.
    refusal: str = ""
    # Addons and hooks match without regard to case; skills and rules do not.
    # For addons that is deliberate -- the names are CamelCase and painful to
    # type exactly, so EllesmereUISecretsdiag resolves rather than being
    # rejected over one letter.
    fold_case: bool = False

    def key_of(self, item) -> str:
        return item.name.lower() if self.fold_case else item.name

    def resolve(self, specs: list[str], available: list) -> list:
        """Map the names the user gave to members. 'all' is every one, 'none' none.

        Written once. The four copies of this differed only in the noun in the
        error message and in whether they folded case, both of which are fields.
        """
        lowered = [s.strip().lower() for s in specs]
        if any(s == "none" for s in lowered):
            return []
        if any(s == "all" for s in lowered):
            return list(available)

        by_name = {self.key_of(item): item for item in available}
        picked: list = []
        unknown: list[str] = []
        for raw in specs:
            spec = raw.strip()
            if not spec:
                continue
            hit = by_name.get(spec.lower() if self.fold_case else spec)
            if hit is None:
                unknown.append(spec)
            elif hit not in picked:
                picked.append(hit)
        if unknown:
            # Listed by display name rather than by lookup key: with fold_case
            # the key is lowercased, and telling somebody the addon is called
            # "ellesmereuisecretsdiag" would be answering with the wrong name.
            raise UnknownName(
                f"unknown {self.noun}(s): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(item.name for item in available))}"
            )
        return picked

    def unnamed_selection(self, available: list) -> list:
        """What to install when nothing was named and nobody can be asked.

        REFUSE is not represented here: it is not a selection, and the caller
        reports it rather than acting on it.
        """
        return list(available) if self.when_unnamed is WhenUnnamed.ALL else []

    def apply(self, chosen: list, groups, methods, *, uninstalling: bool,
              force: bool, dry_run: bool) -> Applied:
        """Place (or remove) every chosen member in every planned group.

        The group key is opaque here. A skill groups by directory and a rule by
        directory and extension, because the same rule becomes a different
        filename per harness -- a distinction the loop has no use for.
        """
        results: list = []
        failed = False
        changed = False
        for key in groups:
            for item in chosen:
                if uninstalling:
                    r = self.unplace(item, key, dry_run=dry_run)
                else:
                    r = self.place(item, key, methods[key], force=force, dry_run=dry_run)
                results.append((key, r))
                failed = failed or not r.outcome.ok
                changed = changed or r.outcome not in (Outcome.CURRENT, Outcome.ABSENT)
        return Applied(results, failed, changed)


@dataclass(frozen=True)
class Applied:
    """What a run did, before anybody has said it out loud."""

    results: list
    failed: bool
    changed: bool

    def advice(self, catalogue: Catalogue) -> str:
        """The closing line, which depends on whether anything actually moved.

        Telling somebody to restart after a run that moved nothing invites a
        pointless restart and makes a no-op look like work.
        """
        return catalogue.moved_advice if self.changed else catalogue.unchanged_advice


# --------------------------------------------------------------------------
#  Where a selection goes
# --------------------------------------------------------------------------

def plan(harnesses, scope: str, project_root: Path | None):
    """Group harnesses by the directory they resolve to.

    Most harnesses read the cross-agent path, so selecting eight of them
    usually means one directory. Reporting eight identical installs would
    misrepresent what happened, so they are collapsed and credited together.
    """
    groups: OrderedDict[Path, list[registry.Harness]] = OrderedDict()
    skipped: list[registry.Harness] = []
    for h in harnesses:
        directory = engine.resolve_directory(h, scope, project_root)
        if directory is None:
            skipped.append(h)
            continue
        groups.setdefault(directory, []).append(h)
    return groups, skipped


def plan_rules(harnesses, scope: str, project_root: Path | None):
    """Group harnesses by the (directory, extension) a rule would install as.

    Keyed on the extension as well as the path because that is what makes a
    rule install different from a skill install: the same file becomes
    `x.md` for Claude Code, `x.mdc` for Cursor, and `x.instructions.md` for
    Copilot, so two harnesses only share an install when both agree.
    """
    groups: OrderedDict = OrderedDict()
    skipped: list[registry.Harness] = []
    for h in harnesses:
        directory = engine.resolve_directory(h, scope, project_root, kind="rules")
        if directory is None:
            skipped.append(h)
            continue
        groups.setdefault((directory, h.rules_ext), []).append(h)
    return groups, skipped


# --------------------------------------------------------------------------
#  The four kinds
# --------------------------------------------------------------------------

SKILLS = Catalogue(
    noun="skill",
    discover=skills_mod.discover,
    plan=lambda hs, scope, root: plan(hs, scope, root),
    when_unnamed=WhenUnnamed.ALL,
    place=lambda item, directory, method, *, force, dry_run: engine.install_item(
        item, directory, method, force=force, dry_run=dry_run),
    unplace=lambda item, directory, *, dry_run: engine.uninstall_item(
        item, directory, dry_run=dry_run),
    moved_advice="Restart your harness (or reload its skills) to pick these up.",
    unchanged_advice="Everything was already in place; nothing to restart.",
)


RULES = Catalogue(
    noun="rule",
    discover=rules_mod.discover,
    plan=lambda hs, scope, root: plan_rules(hs, scope, root),
    when_unnamed=WhenUnnamed.NONE,
    place=lambda item, key, method, *, force, dry_run: engine.install_rule(
        item, key[0], method, filename=item.filename(key[1]),
        force=force, dry_run=dry_run),
    unplace=lambda item, key, *, dry_run: engine.uninstall_rule(
        item, key[0], filename=item.filename(key[1]), dry_run=dry_run),
    directory_of=lambda key: key[0],
)


ADDONS = Catalogue(
    noun="addon",
    discover=addons_mod.discover,
    when_unnamed=WhenUnnamed.REFUSE,
    place=lambda item, directory, method, *, force, dry_run: engine.install_item(
        item, directory, method, force=force, dry_run=dry_run),
    unplace=lambda item, directory, *, dry_run: engine.uninstall_item(
        item, directory, dry_run=dry_run),
    moved_advice="/reload in game, or restart the client, to pick these up.",
    unchanged_advice="Everything was already in place; no /reload needed.",
    refusal="--wow-addons given without --addons.",
    fold_case=True,
)


# Hooks share the naming rules and nothing else. Their membership is a static
# tuple rather than a discovery over the tree, and they are placed by
# hooks.install, which writes a generated shell script into a repository.
HOOKS = Catalogue(
    noun="hook",
    discover=lambda: (list(hooks_mod.HOOKS), []),
    when_unnamed=WhenUnnamed.ALL,
    fold_case=True,
)
