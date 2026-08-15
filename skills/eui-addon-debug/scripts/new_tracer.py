#!/usr/bin/env python3
"""Install a throwaway event tracer as a real addon in the WoW client.

When the API cannot say what order two events arrive in, the client can. This
writes a loadable addon that prints every dispatch of the events you name, so
the ordering question gets an answer instead of a guess.

    new_tracer.py AuraTrace --events UNIT_AURA --unit player
    new_tracer.py CdTrace --events SPELL_UPDATE_COOLDOWN SPELL_UPDATE_CHARGES
    new_tracer.py AuraTrace --remove

The point of a script is the parts that are silently fatal by hand:

  * a folder with no .toc does not load, and the client says nothing
  * `## Interface` below the running build greys the addon out as out of date,
    so the version is copied from the addons already installed beside it
  * a tracer that reads a value the client classifies raises instead of
    printing, which loses the trace and looks like the tracer is broken

Exit status is 1 if the addon could not be written or fails a syntax check.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

INTERFACE = re.compile(r"^##\s*Interface\s*:\s*(.+)$", re.MULTILINE)

# UNIT_AURA carries a table rather than flat arguments, and its three lists are
# the whole reason to trace it: a replacement (remove + add of a new instance)
# and a refresh (an update of the same instance) are indistinguishable to any
# handler that only counts events.
AURA_BATCH = '''
local function DumpAuraBatch(info)
    if not info then return end
    if info.isFullUpdate then
        Say("|cff888888full update|r")
        return
    end
    if info.addedAuras then
        for _, a in ipairs(info.addedAuras) do
            Say("|cff40ff40ADD|r    inst=" .. S(a.auraInstanceID) .. "  spell=" .. S(a.spellId))
        end
    end
    if info.removedAuraInstanceIDs then
        for _, id in ipairs(info.removedAuraInstanceIDs) do
            Say("|cffff4040REMOVE|r inst=" .. S(id))
        end
    end
    if info.updatedAuraInstanceIDs then
        for _, id in ipairs(info.updatedAuraInstanceIDs) do
            Say("|cffffcc00UPDATE|r inst=" .. S(id))
        end
    end
end
'''

TEMPLATE = '''-- {name}: throwaway event tracer. Delete it when the question is answered.
-- /{slash} toggles printing. Frame numbers are what the ordering question turns
-- on, so every line carries one: two events in one frame is a different fact
-- from two events in consecutive frames.

local UNITFILTER = {unitfilter}
local EVENTS = {{
{eventlist}
}}

local f = CreateFrame("Frame")
local on = false
local t0 = 0

-- There is no frame counter in the API, so keep one. This is the number the
-- ordering question is actually about: "remove then add" in one frame is a
-- replacement, the same pair one frame apart is a real drop and a new cast.
local frame = 0
local ticker = CreateFrame("Frame")
ticker:SetScript("OnUpdate", function() frame = frame + 1 end)

-- A traced value may be one the client classifies in combat. tostring() on one
-- of those raises, which would lose the trace at exactly the moment it matters.
local function S(v)
    if issecretvalue and issecretvalue(v) then return "<secret>" end
    local ok, s = pcall(tostring, v)
    if ok then return s end
    return "<unreadable>"
end

local function Say(msg)
    print(string.format("|cff8888ff[%.2f f%d]|r %s", GetTime() - t0, frame, msg))
end
{aura_batch}
f:SetScript("OnEvent", function(_, event, ...)
    if not on then return end
    -- Only UNIT_* events lead with a unit token. Filtering on argument 1 of
    -- anything else drops lines by matching some unrelated string.
    if UNITFILTER and string.sub(event, 1, 5) == "UNIT_" then
        if (...) ~= UNITFILTER then return end
    end
{dispatch}
    local parts = {{}}
    for i = 1, select("#", ...) do
        parts[#parts + 1] = S((select(i, ...)))
    end
    Say(event .. "  " .. table.concat(parts, ", "))
end)

for _, e in ipairs(EVENTS) do
    local ok = pcall(f.RegisterEvent, f, e)
    if not ok then
        print("|cffff4040{name}: could not register " .. e .. "|r")
    end
end

SLASH_{SLASH}1 = "/{slash}"
SlashCmdList.{SLASH} = function()
    on = not on
    t0 = GetTime()
    print("|cff8888ff{name}: " .. (on and "ON" or "OFF") .. "|r")
end

print("|cff8888ff{name} loaded -- /{slash} to start|r")
'''

TOC = """## Interface: {interface}
## Title: {name}
## Notes: Temporary event tracer -- delete after the investigation
## Version: 1.0

{name}.lua
"""


def discover_addons_dirs() -> list[Path]:
    """Every `.../World of Warcraft/_retail_/Interface/AddOns` we can find.

    Kept in step with `check_style.discover_roots`: WoW installs sit at
    unpredictable depths under Proton and Wine prefixes, so walk a bounded set
    of wildcard depths instead of a recursive glob from $HOME.
    """
    out: list[Path] = []
    home = Path.home()
    tail = "World of Warcraft/_retail_/Interface/AddOns"
    for base in (home, home / "Games", home / ".local/share", Path("/mnt"), Path("/media")):
        if not base.is_dir():
            continue
        for depth in range(6):
            try:
                out.extend(sorted(p for p in base.glob("*/" * depth + tail) if p.is_dir()))
            except OSError:
                continue
    return out


def resolve_addons(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_dir():
            sys.exit(f"{p} is not a directory")
        return p.resolve()

    env = os.environ.get("ELLESMEREUI_ROOT")
    if env:
        parent = Path(env).expanduser().parent
        if parent.is_dir() and parent.name == "AddOns":
            return parent.resolve()

    found = discover_addons_dirs()
    if not found:
        sys.exit("Could not find an AddOns directory. Pass --addons <path>.")
    return found[0]


def client_interface(addons: Path) -> str:
    """The highest `## Interface` any installed addon declares.

    A .toc may list several builds (`120000, 120001, ... 120100`); the running
    client is the newest of them, and copying the first number rather than the
    largest is what greys the tracer out as out of date. Reading it from the
    addons already loading beside it means it cannot be stale in a way the
    client will not also apply to them.
    """
    best = 0
    for toc in sorted(addons.glob("*/*.toc"))[:400]:
        try:
            text = toc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in INTERFACE.finditer(text):
            for num in re.findall(r"\d{5,6}", match.group(1)):
                best = max(best, int(num))
    if not best:
        sys.exit(f"No `## Interface` found under {addons}. Pass --interface <build>.")
    return str(best)


def syntax_check(path: Path) -> str | None:
    """Compile the generated Lua. Returns an error string, or None if clean."""
    for exe in ("luac5.1", "luac"):
        if shutil.which(exe):
            proc = subprocess.run([exe, "-p", str(path)], capture_output=True, text=True)
            return None if proc.returncode == 0 else (proc.stderr or proc.stdout).strip()
    return None


def render(name: str, events: list[str], unit: str | None) -> str:
    slash = name.lower()
    eventlist = "\n".join(f'    "{e}",' for e in events)
    dispatch = ""
    aura_batch = ""
    if any(e == "UNIT_AURA" for e in events):
        aura_batch = AURA_BATCH
        dispatch = ('    if event == "UNIT_AURA" then\n'
                    '        DumpAuraBatch((select(2, ...)))\n'
                    '        return\n'
                    '    end\n')
    return TEMPLATE.format(
        name=name,
        slash=slash,
        SLASH=name.upper(),
        eventlist=eventlist,
        unitfilter=f'"{unit}"' if unit else "nil",
        aura_batch=aura_batch,
        dispatch=dispatch,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="addon name, letters only (becomes the folder and /slash)")
    ap.add_argument("--events", nargs="+", default=["UNIT_AURA"],
                    help="events to trace (default: UNIT_AURA)")
    ap.add_argument("--unit", help="for UNIT_* events, only trace this unit token")
    ap.add_argument("--addons", help="path to Interface/AddOns")
    ap.add_argument("--interface", help="override the ## Interface build number")
    ap.add_argument("--remove", action="store_true", help="delete the tracer instead")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", args.name):
        sys.exit("name must be letters and digits only -- it becomes a folder and a global")

    addons = resolve_addons(args.addons)
    folder = addons / args.name

    if args.remove:
        if not folder.is_dir():
            print(f"Nothing to remove at {folder}")
            return 0
        stray = sorted(p.name for p in folder.iterdir()
                       if p.name not in (f"{args.name}.lua", f"{args.name}.toc"))
        if stray:
            sys.exit(f"{folder} holds files this script did not write: {stray}\n"
                     "Remove it by hand rather than losing them.")
        shutil.rmtree(folder)
        print(f"Removed {folder}")
        return 0

    if folder.exists() and not folder.is_dir():
        sys.exit(f"{folder} exists and is not a directory")

    interface = args.interface or client_interface(addons)
    folder.mkdir(parents=True, exist_ok=True)
    lua = folder / f"{args.name}.lua"
    (folder / f"{args.name}.toc").write_text(
        TOC.format(interface=interface, name=args.name), encoding="utf-8")
    lua.write_text(render(args.name, args.events, args.unit), encoding="utf-8")

    err = syntax_check(lua)
    if err:
        print(f"Generated Lua does not compile:\n{err}", file=sys.stderr)
        return 1

    print(f"Installed {folder}")
    print(f"  ## Interface: {interface}")
    print(f"  events: {', '.join(args.events)}"
          + (f"  (unit filter: {args.unit})" if args.unit else ""))
    print()
    print("In game:")
    print("  1. Restart the client. A new addon folder needs the list rebuilt,")
    print("     and /reload alone will not pick it up.")
    print(f"  2. Confirm '{args.name} loaded' prints in chat. If it does not, check")
    print("     the AddOns list at the character screen -- and check BugSack, since")
    print("     a load error shows there rather than in chat.")
    print(f"  3. /{args.name.lower()} to start tracing, then run the case.")
    print()
    print(f"Afterwards: {Path(__file__).name} {args.name} --remove")
    return 0


if __name__ == "__main__":
    sys.exit(main())
