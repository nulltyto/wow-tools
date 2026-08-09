"""Find the World of Warcraft install, its AddOns folder, and its SavedVariables.

This is the part of the installer that cannot be answered from a table. A skill
goes to `~/.agents/skills` on every machine; an addon goes wherever this person
happens to have installed the game, which on Linux is usually several levels
inside a Proton or Wine prefix whose name nobody can predict.

So the answer comes from three places, in descending order of authority:

    1. what the caller said            --wow-addons, $WOW_ADDONS_DIR, $WOW_INSTALL
    2. where the platform puts it      /Applications, C:\\Program Files (x86), ...
    3. a bounded search of likely bases

Only step 3 is a guess, and a guess is never installed into silently -- the CLI
shows what it found and asks. Nothing here writes anything.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Blizzard's own directory names. Retail first: it is what almost every install
# has, and ordering decides which one an unattended `--yes` run would pick.
FLAVORS = (
    "_retail_",
    "_ptr_",
    "_xptr_",
    "_beta_",
    "_classic_",
    "_classic_era_",
    "_classic_ptr_",
    "_classic_era_ptr_",
    "_classic_beta_",
)

# The folder Blizzard's installer creates. Kept as a constant because it is
# also the tail of every search pattern below.
GAME_DIR = "World of Warcraft"

# How deep to look for a game folder under each search base. Six is enough for
# ~/Games/<launcher>/<app>/drive_c/Program Files (x86)/World of Warcraft and
# for the Steam/Proton equivalents; a recursive walk of $HOME is not, because
# it is unusably slow on a real home directory.
_MAX_DEPTH = 6


@dataclass(frozen=True)
class WowInstall:
    """One game flavor -- `.../World of Warcraft/_retail_` and what hangs off it."""

    flavor_dir: Path

    @property
    def flavor(self) -> str:
        return self.flavor_dir.name.strip("_") or self.flavor_dir.name

    @property
    def root(self) -> Path:
        """The `World of Warcraft` folder holding this flavor."""
        return self.flavor_dir.parent

    @property
    def addons(self) -> Path:
        return self.flavor_dir / "Interface" / "AddOns"

    @property
    def wtf(self) -> Path:
        return self.flavor_dir / "WTF"

    def savedvariables(self, addon: str) -> list[Path]:
        """Account-level SavedVariables files for `addon`, newest first.

        There is one per account on the machine, and the useful one is almost
        always the most recently written -- the client rewrites it on /reload
        and at logout, so mtime is a real signal about which account was played
        last rather than an accident of the filesystem.
        """
        found = sorted(
            self.wtf.glob(f"Account/*/SavedVariables/{addon}.lua"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return found

    @property
    def label(self) -> str:
        return f"{self.flavor} — {self.flavor_dir}"

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.label


def is_install(flavor_dir: Path) -> bool:
    """Whether `flavor_dir` really is a game flavor and not a lookalike.

    `Interface` is the test rather than `Interface/AddOns`, because a fresh
    install that has never loaded an addon has no AddOns folder yet, and
    refusing to install into it would be exactly backwards.
    """
    try:
        return flavor_dir.is_dir() and (flavor_dir / "Interface").is_dir()
    except OSError:
        return False


def _flavors_under(game_dir: Path) -> list[WowInstall]:
    out = []
    for name in FLAVORS:
        candidate = game_dir / name
        if is_install(candidate):
            out.append(WowInstall(candidate))
    return out


def _platform_bases() -> list[Path]:
    """Where the game lives when nobody has moved it."""
    home = Path.home()
    if sys.platform == "darwin":
        return [Path("/Applications"), home / "Applications"]
    if os.name == "nt":
        bases = []
        for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
            value = os.environ.get(var)
            if value:
                bases.append(Path(value))
        # A second drive is the normal place for a 140 GB game, and it is not
        # in any environment variable.
        bases.extend(Path(f"{letter}:/") for letter in "CDEFG")
        bases.extend(Path(f"{letter}:/Games") for letter in "CDEFG")
        return bases
    # Linux and the BSDs: the game is always inside some compatibility prefix,
    # so the useful bases are the roots those prefixes are created under.
    return [
        home,
        home / "Games",
        home / ".local/share",
        home / ".steam/steam/steamapps/compatdata",
        home / ".wine/drive_c",
        Path("/mnt"),
        Path("/media"),
        Path("/run/media") / os.environ.get("USER", ""),
    ]


def discover_installs(extra_bases: list[Path] | None = None) -> list[WowInstall]:
    """Every game flavor findable on this machine, best guess first.

    Deliberately bounded. `Path.rglob` over $HOME would find installs this does
    not, and would take minutes to do it on a machine with a large home
    directory -- which turns `install` into something people interrupt.
    """
    seen: set[Path] = set()
    out: list[WowInstall] = []

    def add(game_dir: Path) -> None:
        for install in _flavors_under(game_dir):
            resolved = install.flavor_dir.resolve()
            if resolved not in seen:
                seen.add(resolved)
                out.append(install)

    # An explicit install root beats anything found by searching.
    for var in ("WOW_INSTALL", "WOW_PATH"):
        value = os.environ.get(var)
        if value:
            root = Path(value).expanduser()
            add(root)
            # Tolerate being handed the flavor directory instead of the folder
            # above it, which is the more natural thing to copy from a path bar.
            if is_install(root):
                add(root.parent)

    bases = list(_platform_bases()) + list(extra_bases or [])
    for base in bases:
        try:
            if not base.is_dir():
                continue
        except OSError:
            continue
        for depth in range(_MAX_DEPTH):
            pattern = "*/" * depth + GAME_DIR
            try:
                for game_dir in sorted(base.glob(pattern)):
                    add(game_dir)
            except OSError:
                # A permission-denied or a dead mount partway through a search
                # is not a reason to fail the whole install.
                continue
    return out


def resolve_addons_dir(explicit: str | Path | None = None) -> tuple[Path | None, list[WowInstall]]:
    """Return `(addons_dir, candidates)`.

    `addons_dir` is set only when something authoritative said where to look --
    an explicit path or `$WOW_ADDONS_DIR`. Otherwise it is None and the caller
    chooses from `candidates`, because installing into a guessed game directory
    without showing it first is how somebody's PTR install quietly gets the
    addon and their retail one does not.
    """
    if explicit is not None:
        return Path(explicit).expanduser(), []

    env = os.environ.get("WOW_ADDONS_DIR")
    if env:
        return Path(env).expanduser(), []

    installs = discover_installs()
    return None, installs


# How deep under AddOns to look for a duplicate of an addon that is already
# installed. Two is enough for the case that matters -- a whole addon checkout
# living inside AddOns, carrying its own copy of a folder that is separately
# symlinked at the top level -- without walking a hundred unrelated addons.
_SHADOW_DEPTH = 2


def find_shadow_copies(addons_dir: Path, name: str, live: Path | None) -> list[Path]:
    """Other directories under `addons_dir` that also look like the addon `name`.

    The game loads exactly one path. Anything else carrying the same
    `<name>/<name>.toc` is a decoy: it reads like the source, it answers a
    `grep -rn` from the AddOns directory, and an edit to it changes nothing in
    game and reports no error. That failure mode cost a whole debugging session
    -- three rounds of "the fix did not take" against a copy nothing loads --
    which is why it is worth a check rather than a note in a README.

    `live` is the resolved path the game actually reads, and is excluded.
    """
    out: list[Path] = []
    resolved_live = None
    if live is not None:
        try:
            resolved_live = live.resolve()
        except OSError:
            resolved_live = live

    for depth in range(1, _SHADOW_DEPTH + 1):
        pattern = "*/" * depth + name
        try:
            candidates = sorted(addons_dir.glob(pattern))
        except OSError:
            continue
        for candidate in candidates:
            try:
                if not (candidate / f"{name}.toc").is_file():
                    continue
                real = candidate.resolve()
            except OSError:
                continue
            if real == resolved_live or real in [p.resolve() for p in out]:
                continue
            out.append(candidate)
    return out


def looks_like_addons_dir(path: Path) -> bool:
    """Whether `path` is plausibly an AddOns folder.

    An empty or missing AddOns folder is fine -- it is created on demand. What
    is not fine is a path that is nowhere near a game install, so the check is
    on the ancestry rather than on the contents.
    """
    if path.name != "AddOns":
        return False
    return is_install(path.parent.parent)
