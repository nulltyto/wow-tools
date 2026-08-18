"""What a run decided, and what it then did -- as data rather than as text.

The install drivers used to decide and print in the same breath: six decisions
interleaved with twenty-one print calls, so the only way to ask "what would
this do" was to run it and read stdout. Everything interesting about a run --
that eight harnesses sharing a directory collapse to one install, that --force
is needed over a hand-installed copy, that the restart advice is suppressed
when nothing moved -- was testable only as a substring search.

A run produces two documents at two moments, because the confirmation prompt
sits between them: you approve a Plan you have read, and then you are told what
happened. Both are built here and rendered elsewhere.

Nothing in this module prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Target:
    """One place a selection lands, and how it will get there."""

    directory: Path
    # Names of the harnesses that read this directory. Empty for addons: the
    # game reads them, and no harness is acting on anyone's behalf.
    harnesses: tuple = ()
    method: str = ""
    # Appended to the directory line: " (--copy)", or the sentence explaining
    # that symlinks are unavailable here.
    note: str = ""
    # Rules only. The same rule is a different filename per harness, so two
    # harnesses share a target only when they agree on the extension.
    extension: str = ""


@dataclass(frozen=True)
class Skipped:
    """A harness that was asked for and has nowhere to put this kind."""

    harness: str
    reason: str
    note: str = ""


@dataclass(frozen=True)
class Row:
    """One member, in one target, after the attempt."""

    item: str
    target: Path
    outcome: str
    detail: str = ""
    ok: bool = True


@dataclass(frozen=True)
class Section:
    """One kind's whole contribution to a run.

    An install can name skills, rules and addons at once, so a run is a list of
    these rather than a single document.
    """

    noun: str
    action: str
    chosen: tuple = ()
    targets: tuple = ()
    skipped: tuple = ()
    rows: tuple = ()
    advice: str = ""
    failed: bool = False
    scope: str = ""
    # Set when a kind declined to do anything and said why -- an addons folder
    # named with no addons named, a --hooks with no --repo.
    refused: str = ""

    def document(self) -> dict:
        """A JSON-shaped view. Paths become strings; nothing else changes."""
        doc = {
            "kind": self.noun,
            "action": self.action,
            "ok": not self.failed and not self.refused,
            "chosen": list(self.chosen),
            "targets": [
                {
                    "directory": str(t.directory),
                    "harnesses": list(t.harnesses),
                    "method": t.method,
                    **({"extension": t.extension} if t.extension else {}),
                }
                for t in self.targets
            ],
            "results": [
                {
                    "item": r.item,
                    "target": str(r.target),
                    "outcome": r.outcome,
                    "ok": r.ok,
                    **({"detail": r.detail} if r.detail else {}),
                }
                for r in self.rows
            ],
        }
        if self.scope:
            doc["scope"] = self.scope
        if self.skipped:
            doc["skipped"] = [
                {"harness": s.harness, "reason": s.reason,
                 **({"note": s.note} if s.note else {})}
                for s in self.skipped
            ]
        if self.advice:
            doc["advice"] = self.advice
        if self.refused:
            doc["refused"] = self.refused
        return doc


@dataclass
class Run:
    """Every kind touched by one invocation."""

    sections: list = field(default_factory=list)

    def add(self, section: Section) -> Section:
        self.sections.append(section)
        return section

    def document(self) -> dict:
        return {
            "ok": all(not s.failed and not s.refused for s in self.sections),
            "sections": [s.document() for s in self.sections],
        }


def rows_from(applied, catalogue) -> tuple:
    """Turn what the catalogue did into rows that carry no objects."""
    return tuple(
        Row(
            item=result.item.name,
            target=result.target,
            outcome=result.outcome.value,
            detail=result.detail,
            ok=result.outcome.ok,
        )
        for _key, result in applied.results
    )
