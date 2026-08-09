#!/usr/bin/env python3
"""Build references/addons.json from a pasted CurseForge addon listing.

CurseForge has no public listing API, so the input is the text of the addon
browse pages, copied from the browser. Each entry there looks like:

    Bartender4 logo
    Bartender4
    By
    Nevcairiel
    Download
    Install

    Bartender4 is a full ActionBar replacement mod...

Only the `<name> logo` line and the author two lines below it are read, so the
parser survives changes to the rest of the card.

    build_addon_list.py ~/Documents/curseforge_addons.txt

Every name is reduced to match tokens and sorted into one of three tiers,
because a flat name list is unusable against real source:

  distinctive  An invented name or a multi-word phrase. Matched anywhere in
               changed lines. Multi-word phrases are safe -- measured against
               the EllesmereUI tree, no generic-sounding title ("Edit Mode
               Expanded", "Method Raid Tools") ever matched by accident.

  ambiguous    A single ordinary English word: Atlas, Paste, Cell, Details,
               Routes, Clique, Pawn, Scrap. These collide constantly with
               normal prose and identifiers, so they are reported at note
               severity and never fail a run.

  library      Shared libraries every addon is entitled to use: Ace3, the
               Lib*-x.y family, SharedMedia, Masque, DataStore. Never matched.

The tier is baked into the output, so the linter needs no word list at run
time. The single-word test uses the system dictionary when one is present and
falls back to the embedded list below.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "references" / "addons.json"

DICT_PATHS = [
    Path("/usr/share/dict/words"),
    Path("/usr/share/dict/cracklib-small"),
    Path("/usr/share/dict/american-english"),
]

# Used when no system dictionary is installed. These are the single-word addon
# names that actually collide with ordinary source text -- measured by running
# every candidate token against the EllesmereUI tree.
FALLBACK_WORDS = """
armory arcana atlas cell clique details elephant farmer gathering grail
immersion masque musician outfitter passer paste pawn poisoner routes routine
scrap socialite spy storyline wholly worthit gnosis molinari paragon postal
""".split()

# Names that are the property of everyone: shared libraries and skinning
# frameworks that addons are meant to integrate with.
LIBRARY_NAMES = {
    "Ace3", "AceGUI-3.0", "LibStub", "SharedMedia", "SharedMediaAdditionalFonts",
    "Masque", "BugGrabber", "BugSack", "DataStore",
}

# Measured false positives that the dictionary test misses. Demoting them to
# `ambiguous` costs nothing -- they are still reported, just as notes.
FORCE_AMBIGUOUS = {"Rematch", "Plumber", "Gnosis", "Angleur", "Preydator"}

# Major addons absent from the CurseForge listing (WeakAuras and ElvUI publish
# on Wago and tukui.org; the listing only carries their plugins).
SUPPLEMENT = [
    ("ElvUI", "Elv"),
    ("WeakAuras", "The WeakAuras Team"),
    ("Tukui", "Tukz"),
    ("Plater", "Terciob"),
    ("Hekili", "Hekili"),
    ("OmniCD", "causese"),
    ("ShadowedUnitFrames", "Nevcairiel"),
    ("Shadowed Unit Frames", "Nevcairiel"),
    ("Grid2", "Michael"),
    ("NDui", "siweia"),
    ("KkthnxUI", "Kkthnx"),
    ("RealUI", "Nibelheim"),
    ("Dominos", "Tuller"),
    ("ConsolePort", "Munk"),
    ("Cell", "enderneko"),
    ("Quartz", "Nevcairiel"),
    ("OmniBar", "Jordon"),
    ("BattleGroundEnemies", "Fadelight"),
    ("SexyMap", "Funkeh"),
    ("Stuf Unit Frames", "totalpackage"),
    ("PitBull4", "ckknight"),
    ("oUF", "haste"),
    ("Skada", "Zarnivoop"),
]

# The addon this skill is for.
OWN_NAME = "EllesmereUI"

TAGLINE_SEPARATORS = [" - ", " (", ": ", ", ", " – ", " ["]


def load_words() -> set[str]:
    for path in DICT_PATHS:
        if path.is_file():
            return {w.strip().lower() for w in path.read_text(
                encoding="utf-8", errors="ignore").splitlines() if w.strip()}
    return set(FALLBACK_WORDS)


def parse_listing(text: str) -> list[dict]:
    """Pull (name, author) out of a pasted CurseForge browse page."""
    lines = text.splitlines()
    out, seen = [], set()
    for i, line in enumerate(lines):
        if not line.endswith(" logo"):
            continue
        name = line[:-5].strip()
        if not name or name in seen:
            continue
        seen.add(name)
        author = None
        if i + 3 < len(lines) and lines[i + 2].strip() == "By":
            author = lines[i + 3].strip() or None
        out.append({"name": name, "author": author})
    return out


def title_of(name: str) -> str:
    """Strip the marketing tagline CurseForge appends to the addon name."""
    for sep in TAGLINE_SEPARATORS:
        cut = name.find(sep)
        if cut > 0:
            name = name[:cut]
    return name.strip(" !?|​")


def tokens_for(title: str) -> list[str]:
    """Match tokens for one title: the title, and its de-spaced folder form.

    "Chonky Character Sheet" also appears in source as ChonkyCharacterSheet --
    that is the form the addon's own folder and TOC use, and the form a
    conflict-registry entry carries.
    """
    toks = [title]
    squashed = re.sub(r"[^A-Za-z0-9_]", "", title)
    if len(squashed) >= 4 and squashed != title:
        toks.append(squashed)
    return toks


def tier_for(token: str, words: set[str]) -> str:
    if token in LIBRARY_NAMES or re.match(r"^Lib[A-Z]", token):
        return "library"
    if token in FORCE_AMBIGUOUS:
        return "ambiguous"
    # A multi-word phrase is distinctive even when every word is ordinary:
    # "Edit Mode Expanded" does not turn up by accident in Lua.
    if " " in token:
        return "distinctive"
    return "ambiguous" if token.lower() in words else "distinctive"


def build(records: list[dict], words: set[str]) -> dict:
    addons, tokens = [], {}
    for rec in records:
        title = title_of(rec["name"])
        if len(title) < 3 or title == OWN_NAME:
            continue
        entry = {
            "name": rec["name"],
            "title": title,
            "author": rec["author"],
            "tokens": tokens_for(title),
        }
        addons.append(entry)
        for tok in entry["tokens"]:
            # First writer wins: the shortest, most canonical title that
            # produced a token keeps it.
            tokens.setdefault(tok, {
                "tier": tier_for(tok, words),
                "addon": title,
                "author": rec["author"],
            })

    for tok in list(tokens):
        if tok == OWN_NAME or tok.startswith(OWN_NAME):
            del tokens[tok]

    return {
        "source": "CurseForge addon listing for WoW Retail, plus a supplement "
                  "of major addons that do not publish there",
        "own_addon": OWN_NAME,
        "counts": {
            "addons": len(addons),
            "tokens": len(tokens),
            "distinctive": sum(1 for t in tokens.values() if t["tier"] == "distinctive"),
            "ambiguous": sum(1 for t in tokens.values() if t["tier"] == "ambiguous"),
            "library": sum(1 for t in tokens.values() if t["tier"] == "library"),
        },
        "addons": sorted(addons, key=lambda a: a["title"].lower()),
        "tokens": dict(sorted(tokens.items())),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("listing", nargs="?",
                    help="pasted CurseForge listing (omit to rebuild from the "
                         "supplement alone)")
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    records = []
    if args.listing:
        path = Path(args.listing).expanduser()
        if not path.is_file():
            sys.exit(f"{path} does not exist")
        records = parse_listing(path.read_text(encoding="utf-8", errors="replace"))
        if not records:
            sys.exit(f"No '<name> logo' lines in {path}. Is this a CurseForge paste?")

    have = {r["name"] for r in records}
    records += [{"name": n, "author": a} for n, a in SUPPLEMENT if n not in have]

    words = load_words()
    data = build(records, words)

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    c = data["counts"]
    print(f"{out}\n  {c['addons']} addons -> {c['tokens']} tokens "
          f"({c['distinctive']} distinctive, {c['ambiguous']} ambiguous, "
          f"{c['library']} library)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
