"""The rules that were written once per kind, now written once.

`resolve` is the interesting one: four near-identical copies of it existed, and
they had already drifted -- hooks folded case where skills did not, and addons
folded case for a reason nobody had written next to the other three. These
tests state the shared rules once and the two deliberate differences explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from wow_tools import catalogue
from wow_tools.catalogue import Catalogue, UnknownName, WhenUnnamed


@dataclass(frozen=True)
class Thing:
    name: str


def make(noun="skill", when_unnamed=WhenUnnamed.ALL, fold_case=False) -> Catalogue:
    return Catalogue(
        noun=noun,
        discover=lambda: ([], []),
        plan=lambda *a: ({}, []),
        when_unnamed=when_unnamed,
        place=lambda *a, **k: None,
        unplace=lambda *a, **k: None,
        moved_advice="moved",
        unchanged_advice="unchanged",
        fold_case=fold_case,
    )


AVAILABLE = [Thing("Alpha"), Thing("Beta")]


# --------------------------------------------------------------------------
#  Names
# --------------------------------------------------------------------------

def test_all_and_none_are_reserved():
    c = make()
    assert c.resolve(["all"], AVAILABLE) == AVAILABLE
    assert c.resolve(["none"], AVAILABLE) == []


def test_none_wins_over_anything_else_named():
    """`--skills none --skills Alpha` installs nothing. Refusal is the safe read."""
    assert make().resolve(["none", "Alpha"], AVAILABLE) == []


def test_a_name_given_twice_is_selected_once():
    assert make().resolve(["Alpha", "Alpha"], AVAILABLE) == [Thing("Alpha")]


def test_selection_follows_the_order_asked_for():
    assert make().resolve(["Beta", "Alpha"], AVAILABLE) == [Thing("Beta"), Thing("Alpha")]


def test_blank_specs_are_ignored():
    """`--skills "Alpha, "` splits to a trailing empty string."""
    assert make().resolve(["Alpha", "", "  "], AVAILABLE) == [Thing("Alpha")]


def test_an_unknown_name_is_reported_with_the_kind_s_noun():
    with pytest.raises(UnknownName) as e:
        make(noun="rule").resolve(["Nope"], AVAILABLE)
    assert "unknown rule(s): Nope" in str(e.value)


def test_the_message_is_a_finished_sentence():
    """It used to be a KeyError, whose repr quotes its argument.

    Every call site unwrapped e.args[0] behind the same four-line comment. The
    point of a real exception type is that str(e) is the message.
    """
    with pytest.raises(UnknownName) as e:
        make().resolve(["Nope"], AVAILABLE)
    assert str(e.value).startswith("unknown skill(s)")
    assert "Available: Alpha, Beta" in str(e.value)


def test_every_unknown_name_is_reported_not_just_the_first():
    with pytest.raises(UnknownName) as e:
        make().resolve(["Nope", "Alpha", "AlsoNope"], AVAILABLE)
    assert "Nope, AlsoNope" in str(e.value)


# --------------------------------------------------------------------------
#  The two deliberate differences
# --------------------------------------------------------------------------

def test_case_is_significant_unless_the_kind_says_otherwise():
    with pytest.raises(UnknownName):
        make().resolve(["alpha"], AVAILABLE)


def test_a_folding_kind_matches_regardless_of_case():
    """Addon names are CamelCase and painful to type exactly."""
    assert make(fold_case=True).resolve(["alpha"], AVAILABLE) == [Thing("Alpha")]


def test_a_folding_kind_still_reports_the_real_name():
    """The lookup key is lowercased; answering with it would name it wrongly."""
    with pytest.raises(UnknownName) as e:
        make(noun="addon", fold_case=True).resolve(["Nope"], AVAILABLE)
    assert "Available: Alpha, Beta" in str(e.value), str(e.value)


# --------------------------------------------------------------------------
#  Nothing named, nobody to ask
# --------------------------------------------------------------------------

@pytest.mark.parametrize("policy,expected", [
    (WhenUnnamed.ALL, AVAILABLE),
    (WhenUnnamed.NONE, []),
    (WhenUnnamed.REFUSE, []),
])
def test_the_unnamed_default_is_per_kind(policy, expected):
    """REFUSE selects nothing here; refusing is the caller's job to report."""
    assert make(when_unnamed=policy).unnamed_selection(AVAILABLE) == expected


def test_split_specs_accepts_commas_spaces_and_repeats():
    assert catalogue.split_specs(["a,b", "c d"]) == ["a", "b", "c", "d"]
    assert catalogue.split_specs(None) == []
    assert catalogue.split_specs([]) == []


# --------------------------------------------------------------------------
#  What a run did
# --------------------------------------------------------------------------

def test_advice_depends_on_whether_anything_moved():
    c = make()
    assert catalogue.Applied([], failed=False, changed=True).advice(c) == "moved"
    assert catalogue.Applied([], failed=False, changed=False).advice(c) == "unchanged"


def test_a_kind_with_nothing_to_advise_says_nothing():
    """Rules have no closing advice: one is read at the start of the next
    session, so there is nothing to restart and nothing to tell anyone.

    The first thing converting rules taught us about the shape -- the advice
    fields were required, and rules have neither.
    """
    quiet = Catalogue(
        noun="rule", discover=lambda: ([], []), plan=lambda *a: ({}, []),
        when_unnamed=WhenUnnamed.NONE, place=lambda *a, **k: None,
        unplace=lambda *a, **k: None,
    )
    assert catalogue.Applied([], failed=False, changed=True).advice(quiet) == ""
    assert catalogue.Applied([], failed=False, changed=False).advice(quiet) == ""


def test_a_group_key_yields_its_directory():
    """Skills group by directory; rules by directory and extension.

    The placement loop never looks inside a group key, but whoever reports on a
    group needs the directory out of it.
    """
    from pathlib import Path as P

    assert make().directory_of(P("/a/b")) == P("/a/b")
    from wow_tools.__main__ import RULES
    assert RULES.directory_of((P("/a/b"), ".mdc")) == P("/a/b")


def test_the_rules_catalogue_is_wired_to_the_real_thing():
    from wow_tools.__main__ import RULES

    assert RULES.noun == "rule"
    assert RULES.when_unnamed is WhenUnnamed.NONE
    found, _ = RULES.discover()
    assert found, "discover should find this repo's own rules"
    assert RULES.unnamed_selection(found) == [], "rules are opted into"


def test_the_skills_catalogue_is_wired_to_the_real_thing():
    """Guards the instance, not just the type."""
    from wow_tools.__main__ import SKILLS

    assert SKILLS.noun == "skill"
    assert SKILLS.when_unnamed is WhenUnnamed.ALL
    assert not SKILLS.fold_case
    found, _ = SKILLS.discover()
    assert found, "discover should find this repo's own skills"
    assert SKILLS.resolve(["all"], found) == found
