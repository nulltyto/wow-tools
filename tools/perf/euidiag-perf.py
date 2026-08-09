#!/usr/bin/env python3
"""Turn /euidiag rec recordings into CSV and per-module statistics.

The in-game recorder writes samples into the addon's SavedVariables, which the
client flushes to disk on /reload or logout:

    <WoW>/_retail_/WTF/Account/<account>/SavedVariables/EllesmereUISecretsDiag.lua

This reads that file. Each recording carries its own `columns` list and stores
samples as flat arrays in that order, so nothing here needs to know which
modules were loaded or which metrics were captured.

    ./euidiag-perf.py                     # summarise the newest recording
    ./euidiag-perf.py --list              # what is in the file
    ./euidiag-perf.py -r 2 --csv out.csv  # export recording 2
    ./euidiag-perf.py --out report.txt    # save the summary as well as print it
    ./euidiag-perf.py --plot trace.png    # needs matplotlib, otherwise skipped

The recorder itself (EllesmereUISecretsDiag) is left out of every table and
plot: it only runs because you asked for measurements, so its cost is not part
of what the suite does to a normal session. Pass --include-self to see it.

Usage note that saves an hour: a module reads low here if its work runs on a
frame some OTHER addon created. The engine bills a script handler's whole call
tree to the frame's birth context, so `Core (parent)` absorbing everything is a
known attribution artefact, not necessarily a parent-side regression.
"""

import argparse
import csv
import glob
import json
import os
import statistics
import sys
import time
import zlib

# The recorder addon. Measuring costs something, and that cost is not a cost
# the suite imposes on anybody who is not recording.
SELF_MODULE = "EllesmereUISecretsDiag"

# Columns that are totals rather than per-module readings.
TOTAL_COLUMNS = ("app_ms", "all_addons_ms")

# Eight hues that stay apart for a colorblind reader as well as a trichromat.
# Matplotlib's own cycle is ten and then repeats, which put two identical blues
# on one chart the moment the suite passed ten modules.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
           "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

# Past eight lines nothing keeps them apart by hue alone, so the ninth onward
# reuses a hue with a different dash. Only reached by asking for it.
DASHES = ["-", "--", ":", "-."]

# How many modules the plot draws before the rest become one summed line. The
# tables can afford fifteen rows; a chart cannot afford fifteen lines.
PLOT_SERIES = 8


# --- Lua SavedVariables parser ----------------------------------------------
# Only the subset WoW's serialiser emits: nested tables, string/number keys,
# strings, numbers, booleans. No functions, no cycles, no comments.

class LuaSyntaxError(Exception):
    pass


class LuaParser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.n = len(text)

    def error(self, msg):
        line = self.s.count("\n", 0, self.i) + 1
        raise LuaSyntaxError("%s at line %d" % (msg, line))

    def skip(self):
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif self.s.startswith("--", self.i):
                nl = self.s.find("\n", self.i)
                self.i = self.n if nl < 0 else nl + 1
            else:
                return

    def expect(self, ch):
        self.skip()
        if self.i >= self.n or self.s[self.i] != ch:
            self.error("expected %r" % ch)
        self.i += 1

    def parse_string(self):
        # Assumes self.s[self.i] is the opening quote.
        quote = self.s[self.i]
        self.i += 1
        out = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                self.i += 1
                if self.i >= self.n:
                    self.error("unterminated escape")
                e = self.s[self.i]
                out.append({"n": "\n", "t": "\t", "r": "\r",
                            "\\": "\\", '"': '"', "'": "'"}.get(e, e))
                self.i += 1
            elif c == quote:
                self.i += 1
                return "".join(out)
            else:
                out.append(c)
                self.i += 1
        self.error("unterminated string")

    def parse_number(self):
        start = self.i
        if self.s[self.i] in "+-":
            self.i += 1
        while self.i < self.n and (self.s[self.i].isdigit()
                                   or self.s[self.i] in ".eExXaAbBcCdDfF+-"):
            # Stop a trailing sign from swallowing the next entry: only accept
            # +/- when it is part of an exponent.
            if self.s[self.i] in "+-" and self.s[self.i - 1] not in "eE":
                break
            self.i += 1
        raw = self.s[start:self.i]
        try:
            return int(raw, 0) if raw.lower().startswith(("0x", "-0x")) else \
                (int(raw) if raw.lstrip("+-").isdigit() else float(raw))
        except ValueError:
            self.error("bad number %r" % raw)

    def parse_value(self):
        self.skip()
        if self.i >= self.n:
            self.error("unexpected end of input")
        c = self.s[self.i]
        if c == "{":
            return self.parse_table()
        if c in "\"'":
            return self.parse_string()
        if self.s.startswith("true", self.i):
            self.i += 4
            return True
        if self.s.startswith("false", self.i):
            self.i += 5
            return False
        if self.s.startswith("nil", self.i):
            self.i += 3
            return None
        if c.isdigit() or c in "+-.":
            return self.parse_number()
        self.error("unexpected character %r" % c)

    def parse_table(self):
        self.expect("{")
        result = {}
        array = []
        while True:
            self.skip()
            if self.i >= self.n:
                self.error("unterminated table")
            if self.s[self.i] == "}":
                self.i += 1
                break
            if self.s[self.i] == "[":
                self.i += 1
                self.skip()
                key = self.parse_string() if self.s[self.i] in "\"'" \
                    else self.parse_number()
                self.expect("]")
                self.expect("=")
                result[key] = self.parse_value()
            else:
                # Bare `name = value`, or a positional entry.
                mark = self.i
                ident = ""
                while self.i < self.n and (self.s[self.i].isalnum()
                                           or self.s[self.i] == "_"):
                    ident += self.s[self.i]
                    self.i += 1
                self.skip()
                if ident and self.i < self.n and self.s[self.i] == "=" \
                        and self.s[self.i + 1] != "=":
                    self.i += 1
                    result[ident] = self.parse_value()
                else:
                    self.i = mark
                    array.append(self.parse_value())
            self.skip()
            if self.i < self.n and self.s[self.i] in ",;":
                self.i += 1
        if array and not result:
            return array
        for index, value in enumerate(array, start=1):
            result[index] = value
        return result


def load_savedvariables(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    marker = "EllesmereUISecretsDiagDB"
    at = text.find(marker)
    if at < 0:
        raise SystemExit("no EllesmereUISecretsDiagDB in %s" % path)
    at = text.find("{", at)
    if at < 0:
        raise SystemExit("EllesmereUISecretsDiagDB is empty")
    parser = LuaParser(text)
    parser.i = at
    return parser.parse_table()


def _hits_under(retail):
    pattern = os.path.join(retail, "WTF", "Account", "*", "SavedVariables",
                           "%s.lua" % SELF_MODULE)
    return sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)


def find_savedvariables():
    """The newest EllesmereUISecretsDiag.lua on this machine, or None.

    This tool used to live inside the game tree and could just walk up five
    directories to _retail_. It now ships in wow-tools, which is nowhere near
    the install, so the game has to be located the same way the installer
    locates it -- and that logic lives in one place rather than two.

    The walk-up is kept as a fallback for the case where a copy of this file
    has been dropped back into a WoW folder to run against an install directly.
    """
    # An explicit answer wins, and is the only thing that works for an install
    # the search does not reach.
    explicit = os.environ.get("WOW_SAVEDVARIABLES")
    if explicit:
        return explicit if os.path.isfile(explicit) else None

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from wow_tools import wow
    except ImportError:
        pass
    else:
        for install in wow.discover_installs():
            hits = install.savedvariables(SELF_MODULE)
            if hits:
                return str(hits[0])

    here = os.path.abspath(__file__)
    for up in (5, 3):
        retail = os.path.normpath(os.path.join(os.path.dirname(here), *([os.pardir] * up)))
        hits = _hits_under(retail)
        if hits:
            return hits[0]
    return None


# --- Report ------------------------------------------------------------------

class Report:
    """Print a line and keep it, so --out can save exactly what was shown."""

    def __init__(self):
        self.lines = []

    def __call__(self, text="", *args):
        if args:
            text = text % args
        print(text)
        self.lines.append(text)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(self.lines) + "\n")
        print("wrote %s (%d lines)" % (path, len(self.lines)))


# --- Recordings --------------------------------------------------------------

def as_list(value):
    """SavedVariables arrays come back as a dict keyed 1..n, or as a list."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        keys = [k for k in value if isinstance(k, int)]
        return [value[k] for k in sorted(keys)]
    return []


def get_recordings(db):
    return as_list(db.get("recordings", {}))


def say_source(say, path, recordings):
    """Where these numbers came from.

    The file is found automatically, inside the game install, under a WTF path
    nobody types -- so a summary that does not name it is a page of figures with
    no provenance, and there is one of these files per account.
    """
    try:
        size = os.path.getsize(path)
        written = time.strftime("%Y-%m-%d %H:%M:%S",
                                time.localtime(os.path.getmtime(path)))
    except OSError:
        size, written = 0, "?"
    say("source     %s", os.path.abspath(path))
    say("           %.1f MB, written %s, %d recording(s)",
        size / (1024.0 * 1024.0), written, len(recordings))


def describe(rec, index):
    samples = as_list(rec.get("samples", {}))
    return "#%d  %s  %d samples @ %ss  %s" % (
        index,
        rec.get("label", "?"),
        len(samples),
        rec.get("interval", "?"),
        rec.get("character", "?"),
    )


def remind_to_drop(say, path, recordings):
    """Say what is still being carried, and how to stop carrying it.

    The recordings live in SavedVariables, which the client re-reads at EVERY
    login -- not only the session that wants them. A pile nobody drops is a
    permanent load-time cost, and the only moment the user reliably thinks
    about it is right after reading the numbers they came here for.
    """
    total = sum(len(as_list(r.get("samples", {}))) for r in recordings)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    say()
    say("-" * 68)
    say("%d recording(s), %d samples, %.1f MB in SavedVariables."
        % (len(recordings), total, size / (1024.0 * 1024.0)))
    say("That file is re-read at every login, so drop what you are done with:")
    say()
    say("    /euidiag rec drop all            (or: /euidiag rec drop <n>)")
    say()
    say("The file on disk is rewritten on the next /reload or logout.")
    say("-" * 68)


def rows_of(rec):
    columns = as_list(rec.get("columns", {}))
    for sample in as_list(rec.get("samples", {})):
        yield columns, as_list(sample)


# --- Shaping -----------------------------------------------------------------

def module_of(column):
    """`EllesmereUIBags_peak_ms` -> ('EllesmereUIBags', 'peak_ms')."""
    for suffix in ("_peak_ms", "_ms", "_kb"):
        if column.endswith(suffix):
            return column[:-len(suffix)], suffix[1:]
    return None, None


def wanted(order, include_self):
    return [name for name in order if include_self or name != SELF_MODULE]


def memory_series(rec, order, times, include_self):
    """Spread the memory table back over the sample timeline.

    The rows are written every few seconds, so each one stands until the next
    replaces it -- which is exactly what the removed per-sample column held.
    """
    rows = [as_list(row) for row in as_list(rec.get("memory", {}))]
    rows = [row for row in rows if len(row) > len(order)]
    if not rows or not times:
        return {}
    out = {name: [] for name in wanted(order, include_self)}
    at = 0
    for when in times:
        while at + 1 < len(rows) and rows[at + 1][0] <= when:
            at += 1
        for position, name in enumerate(order):
            if name in out:
                out[name].append(rows[at][position + 1])
    return out


def peak_series(rec, order, times, include_self):
    """Replay the {t, module, ms} rises into a per-sample high-water series."""
    rows = [as_list(row) for row in as_list(rec.get("peaks", {}))]
    rows = [row for row in rows if len(row) >= 3]
    out = {name: [] for name in wanted(order, include_self)}
    if not out or not times:
        return {}
    held = dict.fromkeys(out, 0.0)
    at = 0
    for when in times:
        while at < len(rows) and rows[at][0] <= when:
            index = int(rows[at][1])
            if 1 <= index <= len(order) and order[index - 1] in held:
                held[order[index - 1]] = rows[at][2]
            at += 1
        for name in out:
            out[name].append(held[name])
    return out


def collect(rec, include_self):
    """Sort the flat samples into named series, per metric kind."""
    columns = as_list(rec.get("columns", {}))
    samples = [as_list(s) for s in as_list(rec.get("samples", {}))]
    shaped = {"t": [], "fps": [], "totals": {}, "ms": {}, "peak_ms": {}, "kb": {}}
    for position, column in enumerate(columns):
        values = [s[position] if position < len(s)
                  and isinstance(s[position], (int, float)) else 0.0
                  for s in samples]
        if column in ("t", "fps"):
            shaped[column] = values
        elif column in TOTAL_COLUMNS:
            shaped["totals"][column] = values
        else:
            module, kind = module_of(column)
            if not module or kind not in shaped:
                continue
            if module == SELF_MODULE and not include_self:
                continue
            shaped[kind][module] = values
    shaped["n"] = len(samples)

    # Recordings made before memory and peak moved out of the samples carry
    # them as columns, and are read by the loop above. Newer ones carry them as
    # their own tables, which cost a fraction of the space and say the same
    # thing once spread back over the timeline.
    order = [str(name) for name in as_list(rec.get("modules", {}))]
    if order:
        shaped["kb"].update(
            memory_series(rec, order, shaped["t"], include_self))
        shaped["peak_ms"].update(
            peak_series(rec, order, shaped["t"], include_self))
    return shaped


def stats(values):
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return statistics.fmean(values), statistics.median(values), p95, max(values)


def combat_windows(rec):
    """[(start, end)] from the combat marks, closed at the end of the run."""
    windows, open_at = [], None
    for mark in as_list(rec.get("marks", {})):
        pair = as_list(mark)
        if len(pair) < 2:
            continue
        when, label = pair[0], str(pair[1])
        if label == "combat-start" and open_at is None:
            open_at = when
        elif label == "combat-end" and open_at is not None:
            windows.append((open_at, when))
            open_at = None
    if open_at is not None:
        windows.append((open_at, rec.get("duration", 0) or open_at))
    return windows


def encounter_windows(rec):
    """[(start, end, name)] for boss fights, from the ENCOUNTER marks."""
    windows, open_at = [], {}
    for mark in as_list(rec.get("marks", {})):
        pair = as_list(mark)
        if len(pair) < 2:
            continue
        when, label = pair[0], str(pair[1])
        if label.startswith("encounter-start"):
            open_at[label.partition(": ")[2] or "?"] = when
        elif label.startswith("encounter-end"):
            name = label.partition(": ")[2] or "?"
            if name in open_at:
                windows.append((open_at.pop(name), when, name))
    return windows


def inside(when, windows):
    return any(start <= when <= end for start, end in windows)


def nearest_mark(rec, when):
    """The last mark at or before `when`, as 'label +Ns'."""
    best = None
    for mark in as_list(rec.get("marks", {})):
        pair = as_list(mark)
        if len(pair) >= 2 and pair[0] <= when:
            best = pair
    if not best:
        return ""
    delta = when - best[0]
    return "%s +%.0fs" % (best[1], delta) if delta >= 1 else str(best[1])


# --- Summary sections --------------------------------------------------------

def section_header(say, rec, shaped):
    say("label      %s", rec.get("label", "?"))
    say("character  %s", rec.get("character", "?"))
    say("build      %s", rec.get("build", "?"))
    say("samples    %d over %.1fs, one every %ss",
        shaped["n"], rec.get("duration", 0) or 0, rec.get("interval", "?"))


def section_budget(say, shaped):
    """Where the frame goes: the client, all addons, this suite.

    Per-module milliseconds mean nothing without this. 0.08 ms sounds large
    until you see the frame is 12 ms and every addon together is 0.7 ms.
    """
    fps, totals, ms = shaped["fps"], shaped["totals"], shaped["ms"]
    if not fps and not totals:
        return
    say()
    say("Frame budget (ms per frame, recent rolling average)")
    say("  %-24s %9s %9s %9s %9s", "", "MEAN", "MEDIAN", "P95", "MAX")
    if fps:
        low = sorted(fps)[max(0, int(len(fps) * 0.05) - 1)]
        say("  %-24s %9.1f %9.1f %9.1f %9.1f  (p5 %.1f, min %.1f)",
            "fps", statistics.fmean(fps), statistics.median(fps),
            sorted(fps)[min(len(fps) - 1, int(len(fps) * 0.95))], max(fps),
            low, min(fps))

    app = totals.get("app_ms")
    addons = totals.get("all_addons_ms")
    suite = [sum(values[i] for values in ms.values()) for i in range(shaped["n"])] \
        if ms else []

    def share(values, of):
        if not of or not values:
            return ""
        base = statistics.fmean(of)
        return "" if base <= 0 else "  (%.1f%% of the frame)" % (
            100.0 * statistics.fmean(values) / base)

    for name, values, against in (("whole client frame", app, None),
                                  ("all addons", addons, app),
                                  ("EllesmereUI suite", suite, app)):
        if not values:
            continue
        mean, median, p95, peak = stats(values)
        say("  %-24s %9.4f %9.4f %9.4f %9.4f%s",
            name, mean, median, p95, peak, share(values, against))
    if suite and addons and statistics.fmean(addons) > 0:
        say("  The suite is %.0f%% of all addon time in this run.",
            100.0 * statistics.fmean(suite) / statistics.fmean(addons))


def section_cpu(say, shaped, top):
    ms = shaped["ms"]
    if not ms:
        return
    ranked = sorted(ms.items(), key=lambda kv: statistics.fmean(kv[1]), reverse=True)
    say()
    say("CPU per module (ms per frame, recent rolling average)")
    say("  %-30s %9s %9s %9s %9s", "MODULE", "MEAN", "MEDIAN", "P95", "MAX")
    for name, values in ranked[:top]:
        mean, median, p95, peak = stats(values)
        say("  %-30s %9.4f %9.4f %9.4f %9.4f", name, mean, median, p95, peak)
    if len(ranked) > top:
        say("  ... %d more (use --top)", len(ranked) - top)


def section_combat(say, rec, shaped, top):
    """Split every module by whether the game was in combat.

    A module that costs the same idle as it does mid-pull is doing work it does
    not need to do; a module that only wakes up in combat is behaving.
    """
    windows = combat_windows(rec)
    ms, times = shaped["ms"], shaped["t"]
    if not windows or not ms or not times:
        return
    flags = [inside(when, windows) for when in times]
    fought, idle = flags.count(True), flags.count(False)
    if not fought or not idle:
        return
    say()
    say("In combat vs out (%d pull(s), %.0fs in combat, %.0fs out)",
        len(windows), sum(e - s for s, e in windows),
        (rec.get("duration", 0) or 0) - sum(e - s for s, e in windows))
    fps = shaped["fps"]
    if fps:
        in_fps = [v for v, f in zip(fps, flags) if f]
        out_fps = [v for v, f in zip(fps, flags) if not f]
        say("  fps                            %9.1f %9.1f",
            statistics.fmean(in_fps), statistics.fmean(out_fps))
    say("  %-30s %9s %9s %7s", "MODULE", "COMBAT", "IDLE", "RATIO")
    rows = []
    for name, values in ms.items():
        in_combat = statistics.fmean([v for v, f in zip(values, flags) if f])
        out_combat = statistics.fmean([v for v, f in zip(values, flags) if not f])
        rows.append((name, in_combat, out_combat))
    rows.sort(key=lambda row: row[1], reverse=True)
    for name, in_combat, out_combat in rows[:top]:
        ratio = ("%6.1fx" % (in_combat / out_combat)) if out_combat > 0 else "     -"
        say("  %-30s %9.4f %9.4f %7s", name, in_combat, out_combat, ratio)


def section_encounters(say, rec, shaped):
    """One row per boss: a dungeon run is the usual reason to record."""
    windows = encounter_windows(rec)
    times, fps, ms = shaped["t"], shaped["fps"], shaped["ms"]
    if not windows or not times:
        return
    say()
    say("Encounters")
    say("  %-34s %6s %7s %7s %9s  %s",
        "BOSS", "SECS", "FPSAVG", "FPSMIN", "SUITEMS", "TOP MODULE")
    for start, end, name in windows:
        picked = [i for i, when in enumerate(times) if start <= when <= end]
        if not picked:
            continue
        window_fps = [fps[i] for i in picked] if fps else [0]
        totals = {module: statistics.fmean([values[i] for i in picked])
                  for module, values in ms.items()}
        worst = max(totals.items(), key=lambda kv: kv[1]) if totals else ("", 0)
        say("  %-34s %6.0f %7.1f %7.1f %9.4f  %s (%.4f)",
            name[:34], end - start, statistics.fmean(window_fps),
            min(window_fps), sum(totals.values()), worst[0], worst[1])


def section_peaks(say, rec, shaped, top):
    """PeakTime is a high-water mark, so only the steps up are events.

    The column never falls, which is why a raw reading is useless: it reports
    whatever the addon did while loading for the rest of the session. A step up
    during the run is a real spike, and its sample tells you when it happened.
    """
    peaks, times = shaped["peak_ms"], shaped["t"]
    if not peaks or not times:
        return
    events, carried = [], []
    for name, values in peaks.items():
        if values and values[0] > 0:
            carried.append((name, values[0]))
        for i in range(1, len(values)):
            if values[i] > values[i - 1] + 0.0001:
                events.append((values[i], times[i], name, values[i - 1]))
    if carried:
        carried.sort(key=lambda item: item[1], reverse=True)
        say()
        say("Worst single frame carried in from load (before the recording)")
        for name, value in carried[:5]:
            say("  %-30s %9.2f ms", name, value)
    say()
    if not events:
        # Worth saying out loud. Silence here reads as "the section is broken",
        # when it actually means no module got slower than it already was.
        say("Spikes during the recording: none — no module beat the worst frame")
        say("  it had already set before the recording started.")
        return
    events.sort(reverse=True)
    say("Spikes during the recording (a new worst frame for that module)")
    say("  %8s  %-30s %9s %9s  %s", "T", "MODULE", "MS", "WAS", "NEAREST MARK")
    for value, when, name, previous in events[:top]:
        say("  %7.1fs  %-30s %9.2f %9.2f  %s",
            when, name, value, previous, nearest_mark(rec, when))
    if len(events) > top:
        say("  ... %d more (use --top)", len(events) - top)


def section_worst_frames(say, rec, shaped, top):
    """The samples the player would actually have felt."""
    fps, times, ms = shaped["fps"], shaped["t"], shaped["ms"]
    if not fps or not times:
        return
    order = sorted(range(len(fps)), key=lambda i: fps[i])
    picked = []
    for i in order:
        # One dip lasting several samples is one event, not five.
        if any(abs(times[i] - times[j]) < 10 for j in picked):
            continue
        picked.append(i)
        if len(picked) >= min(top, 8):
            break
    app = shaped["totals"].get("app_ms", [])
    addons = shaped["totals"].get("all_addons_ms", [])
    say()
    say("Worst moments (lowest fps, 10s apart)")
    say("  %8s %7s %8s %8s  %-28s %8s  %s",
        "T", "FPS", "FRAMEMS", "ADDONMS", "TOP MODULE", "ITS MS", "NEAREST MARK")
    for i in sorted(picked, key=lambda j: times[j]):
        worst = max(((name, values[i]) for name, values in ms.items()),
                    key=lambda kv: kv[1], default=("", 0))
        say("  %7.1fs %7.1f %8.2f %8.3f  %-28s %8.4f  %s",
            times[i], fps[i], app[i] if i < len(app) else 0,
            addons[i] if i < len(addons) else 0, worst[0], worst[1],
            nearest_mark(rec, times[i]))


def section_memory(say, rec, shaped, top):
    memory = shaped["kb"]
    if not memory:
        return
    minutes = (rec.get("duration", 0) or 0) / 60.0 or 1.0
    # Growth over the run is the signal; a single high reading is just a
    # module that legitimately holds a lot, and says nothing about leaking.
    ranked = sorted(memory.items(), key=lambda kv: kv[1][-1] - kv[1][0], reverse=True)
    say()
    say("Memory per module (KB)")
    say("  %-30s %10s %10s %10s %10s %8s",
        "MODULE", "START", "END", "GROWTH", "KB/MIN", "DROPS")
    for name, values in ranked[:top]:
        drops = sum(1 for a, b in zip(values, values[1:]) if b < a - 1)
        say("  %-30s %10.0f %10.0f %+10.0f %10.1f %8d",
            name, values[0], values[-1], values[-1] - values[0],
            (values[-1] - values[0]) / minutes, drops)
    say("  Steady growth with no DROPS is a leak. Growth alongside many drops")
    say("  is garbage the collector has not reached yet, and is not.")


def section_marks(say, rec):
    marks = as_list(rec.get("marks", {}))
    if not marks:
        return
    say()
    say("Marks (%d)", len(marks))
    for mark in marks[:40]:
        pair = as_list(mark)
        if len(pair) >= 2:
            say("  %8.1fs  %s", pair[0], pair[1])
    if len(marks) > 40:
        say("  ... %d more", len(marks) - 40)


def summarise(say, rec, top, include_self, show_marks=True):
    shaped = collect(rec, include_self)
    if not shaped["n"]:
        say("recording has no samples")
        return
    section_header(say, rec, shaped)
    section_budget(say, shaped)
    section_cpu(say, shaped, top)
    section_combat(say, rec, shaped, top)
    section_encounters(say, rec, shaped)
    section_peaks(say, rec, shaped, top)
    section_worst_frames(say, rec, shaped, top)
    section_memory(say, rec, shaped, top)
    if show_marks:
        section_marks(say, rec)
    if not include_self:
        say()
        say("(%s is left out — it only runs while you are recording."
            " --include-self shows it.)" % SELF_MODULE)


def write_csv(rec, path):
    """A flat table with every metric in it, whichever layout the recording used.

    Memory and peak live in side tables now, but they are put back as columns
    here: a CSV that changed shape between two recordings would break whatever
    sheet or script is pointed at it, and the compact layout was about what the
    client carries between sessions, not about what leaves this tool.
    """
    columns = list(as_list(rec.get("columns", {})))
    samples = [as_list(s) for s in as_list(rec.get("samples", {}))]
    shaped = collect(rec, include_self=True)
    order = [str(name) for name in as_list(rec.get("modules", {}))]

    extra = []
    if not any(c.endswith("_peak_ms") for c in columns):
        extra += [(name + "_peak_ms", shaped["peak_ms"][name])
                  for name in order if name in shaped["peak_ms"]]
    if not any(c.endswith("_kb") for c in columns):
        extra += [(name + "_kb", shaped["kb"][name])
                  for name in order if name in shaped["kb"]]

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns + [name for name, _ in extra])
        for index, sample in enumerate(samples):
            writer.writerow(sample + [values[index] for _, values in extra])
    print("wrote %s (%d rows, %d columns)"
          % (path, len(samples), len(columns) + len(extra)))

    marks = as_list(rec.get("marks", {}))
    if marks:
        marks_path = os.path.splitext(path)[0] + "-marks.csv"
        with open(marks_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["t", "label"])
            for mark in marks:
                writer.writerow(as_list(mark))
        print("wrote %s (%d marks)" % (marks_path, len(marks)))


def styles_for(names):
    """Give each module a hue it keeps between recordings.

    Assigning by rank instead would repaint every survivor as soon as one
    module moved, and the whole point of two traces is to lay them side by
    side. crc32 rather than hash(): hash() is salted per process, so the same
    module would come out a different color on every run.
    """
    taken, out = set(), {}
    for name in names:
        start = zlib.crc32(name.encode("utf-8")) % len(PALETTE)
        for cycle in range(len(DASHES)):
            for step in range(len(PALETTE)):
                slot = (start + step) % len(PALETTE)
                if (cycle, slot) not in taken:
                    taken.add((cycle, slot))
                    out[name] = (PALETTE[slot], DASHES[cycle])
                    break
            if name in out:
                break
        out.setdefault(name, (PALETTE[start], DASHES[-1]))
    return out


def bucket(values, buckets, how="mean"):
    """Reduce a series to `buckets` points, keeping the shape of the run."""
    total = len(values)
    if total <= buckets:
        return values
    out = []
    for index in range(buckets):
        low = index * total // buckets
        high = max(low + 1, (index + 1) * total // buckets)
        chunk = values[low:high]
        out.append(max(chunk) if how == "max"
                   else min(chunk) if how == "min"
                   else statistics.fmean(chunk))
    return out


def trim(value, places=4):
    """Round for size. A tenth of a microsecond is not a finding."""
    if isinstance(value, float):
        rounded = round(value, places)
        return int(rounded) if rounded == int(rounded) else rounded
    return value


def write_share(rec, path, include_self, points, source=None):
    """Write a small JSON file that answers the same questions as the summary.

    A raw recording is megabytes of full-resolution samples, most of them
    repeats, and it carries the account folder in its path. This carries the
    statistics, one downsampled trace, and nothing that identifies where it
    came from beyond the character and the build.
    """
    shaped = collect(rec, include_self)
    if not shaped["n"]:
        print("nothing to share")
        return

    times, fps, ms = shaped["t"], shaped["fps"], shaped["ms"]
    windows = combat_windows(rec)
    flags = [inside(when, windows) for when in times]
    fought = any(flags)
    idle = not all(flags)

    def split(values):
        entry = {}
        if fought:
            entry["combat"] = trim(statistics.fmean(
                [v for v, f in zip(values, flags) if f]))
        if idle:
            entry["idle"] = trim(statistics.fmean(
                [v for v, f in zip(values, flags) if not f]))
        return entry

    modules = {}
    for name, values in ms.items():
        mean, median, p95, peak = stats(values)
        entry = {"mean": trim(mean), "median": trim(median),
                 "p95": trim(p95), "max": trim(peak)}
        entry.update(split(values))
        kb = shaped["kb"].get(name)
        if kb:
            entry["kb_start"], entry["kb_end"] = round(kb[0]), round(kb[-1])
        modules[name] = entry

    suite = [sum(values[i] for values in ms.values()) for i in range(shaped["n"])] \
        if ms else []
    budget = {}
    for key, values in (("app_ms", shaped["totals"].get("app_ms")),
                        ("all_addons_ms", shaped["totals"].get("all_addons_ms")),
                        ("suite_ms", suite)):
        if values:
            mean, median, p95, peak = stats(values)
            budget[key] = {"mean": trim(mean), "median": trim(median),
                           "p95": trim(p95), "max": trim(peak)}

    spikes = []
    for name, values in shaped["peak_ms"].items():
        for index in range(1, len(values)):
            if values[index] > values[index - 1] + 0.0001:
                spikes.append({"t": times[index], "module": name,
                               "ms": trim(values[index], 2),
                               "was": trim(values[index - 1], 2),
                               "mark": nearest_mark(rec, times[index])})
    spikes.sort(key=lambda item: item["ms"], reverse=True)

    share = {
        "tool": "euidiag-perf",
        "format": 1,
        "recording": {
            "label": rec.get("label"),
            "character": rec.get("character"),
            "build": rec.get("build"),
            "metric": rec.get("metric"),
            "interval": rec.get("interval"),
            "duration": rec.get("duration"),
            "samples": shaped["n"],
            "modules": len(ms),
            # The name only. The full path carries the account folder, and this
            # file is meant to be handed to somebody else.
            "source": os.path.basename(source) if source else None,
            "excluded": None if include_self else SELF_MODULE,
        },
        "fps": {"mean": trim(statistics.fmean(fps), 1), "min": trim(min(fps), 1),
                "p5": trim(sorted(fps)[max(0, int(len(fps) * 0.05) - 1)], 1)} if fps else {},
        "combat": {"pulls": len(windows),
                   "seconds": trim(sum(e - s for s, e in windows), 1)},
        "budget": budget,
        "modules": modules,
        "encounters": [{"name": name, "start": start, "end": end}
                       for start, end, name in encounter_windows(rec)],
        "marks": [as_list(m)[:2] for m in as_list(rec.get("marks", {}))
                  if len(as_list(m)) >= 2],
        "spikes": spikes[:50],
        # One trace at a readable resolution rather than every sample. Mean for
        # shape, max alongside it so a spike survives the reduction.
        "trace": {
            "points": min(points, shaped["n"]),
            "t": [trim(v, 1) for v in bucket(times, points)],
            "fps": [trim(v, 1) for v in bucket(fps, points)] if fps else [],
            "fps_min": [trim(v, 1) for v in bucket(fps, points, "min")] if fps else [],
            "suite_ms": [trim(v) for v in bucket(suite, points)] if suite else [],
            "suite_ms_max": [trim(v) for v in bucket(suite, points, "max")] if suite else [],
            "modules": {name: [trim(v) for v in bucket(values, points)]
                        for name, values in ms.items()},
        },
    }

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(share, handle, separators=(",", ":"), sort_keys=False)
    print("wrote %s (%.0f KB, from %d samples down to %d points)"
          % (path, os.path.getsize(path) / 1024.0, shaped["n"],
             share["trace"]["points"]))


def plot(rec, path, top, include_self, source=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed — skipping the plot "
              "(the CSV export works without it)")
        return

    shaped = collect(rec, include_self)
    if not shaped["n"]:
        print("nothing to plot")
        return

    times = shaped["t"]
    cpu = sorted(((name, values, statistics.fmean(values))
                  for name, values in shaped["ms"].items()),
                 key=lambda item: item[2], reverse=True)
    drawn, folded = cpu[:top], cpu[top:]

    figure, (axes, lower) = plt.subplots(
        2, 1, figsize=(14, 9), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]})
    styles = styles_for([name for name, _, _ in drawn])
    for name, values, _ in drawn:
        color, dash = styles[name]
        axes.plot(times, values, linewidth=1.2, label=name,
                  color=color, linestyle=dash)

    # The scale comes from the module lines and nothing else, so the chart is
    # normalised to what it is actually showing. Zero at the bottom because a
    # millisecond count has no negative half, and matplotlib's default margin
    # otherwise spends 5% of the panel below the axis on nothing.
    ceiling = max((max(values) for _, values, _ in drawn), default=0)

    # The rest as one summed line rather than dropped: the tail is usually flat
    # and near zero, and seeing that is the point. A line that vanishes into the
    # axis is the answer to "is anything hiding down there".
    #
    # It is kept OUT of the scale, though. It is an aggregate rather than a
    # measurement -- nobody reads a value off it -- and on this recording its
    # rare moments of every tail module spiking at once reached twice the
    # tallest real module, which squashed all eight of them into the bottom
    # half of the panel to make room for a line that means "not much".
    if folded:
        rest = [sum(values[i] for _, values, _ in folded) for i in range(shaped["n"])]
        label = "other (%d modules, summed)" % len(folded)
        if ceiling > 0 and max(rest) > ceiling:
            label += " — clipped"
        axes.plot(times, rest, linewidth=1.0, color="0.45", linestyle=(0, (1, 2)),
                  label=label)
    if ceiling > 0:
        axes.set_ylim(0, ceiling * 1.05)

    # Shade the pulls: a spike inside combat and the same spike while standing
    # still are different findings, and the eye should not have to work it out.
    for start, end in combat_windows(rec):
        for panel in (axes, lower):
            panel.axvspan(start, end, color="0.85", alpha=0.5, zorder=0)

    for mark in as_list(rec.get("marks", {})):
        pair = as_list(mark)
        if len(pair) >= 2 and not str(pair[1]).startswith("combat-"):
            axes.axvline(pair[0], color="0.6", linestyle="--", linewidth=0.8)
            axes.annotate(str(pair[1]), (pair[0], axes.get_ylim()[1]),
                          rotation=90, fontsize=7, va="top", color="0.4")

    axes.set_ylabel("ms per frame")
    axes.set_title("EllesmereUI CPU — %s (%s)   shaded = in combat"
                   % (rec.get("label", "?"), rec.get("character", "?")))
    axes.grid(alpha=0.3)

    if shaped["fps"]:
        lower.plot(times, shaped["fps"], linewidth=1.0, color="tab:red")
        lower.set_ylabel("fps")
        lower.grid(alpha=0.3)
    lower.set_xlabel("seconds into the recording")

    # Under the figure rather than over the trace: with a dozen modules an
    # in-axes legend hides the first two minutes, which is where loading is.
    figure.legend(*axes.get_legend_handles_labels(),
                  fontsize=8, ncol=5, loc="lower center",
                  bbox_to_anchor=(0.5, 0.02), frameon=False)
    if source:
        # A PNG travels further than the terminal it was made in, so it carries
        # the file it was made from.
        figure.text(0.5, 0.004, os.path.abspath(source),
                    fontsize=6, color="0.5", ha="center")
    figure.tight_layout(rect=(0, 0.10, 1, 1))
    figure.savefig(path, dpi=130)
    print("wrote %s" % path)


def main():
    parser = argparse.ArgumentParser(
        description="Summarise /euidiag rec recordings from SavedVariables.")
    parser.add_argument("savedvariables", nargs="?",
                        help="path to EllesmereUISecretsDiag.lua "
                             "(found automatically under the WoW install)")
    parser.add_argument("-r", "--recording", type=int, default=None,
                        help="which recording (1-based); default is the newest")
    parser.add_argument("--list", action="store_true", help="list recordings and exit")
    parser.add_argument("--csv", metavar="PATH", help="write the samples as CSV")
    parser.add_argument("--out", metavar="PATH",
                        help="write the printed summary to a text file as well")
    parser.add_argument("--plot", metavar="PATH", help="write a PNG of the trace")
    parser.add_argument("--share", metavar="PATH",
                        help="write a small JSON file to hand to somebody else: "
                             "statistics plus one downsampled trace, no account path")
    parser.add_argument("--share-points", type=int, default=240, metavar="N",
                        help="trace resolution in the shared file (default 240)")
    parser.add_argument("--top", type=int, default=15,
                        help="how many modules each table shows (default 15)")
    parser.add_argument("--plot-top", type=int, default=None, metavar="N",
                        help="how many lines the plot draws, the rest summed "
                             "into one (default %d, or --top if that is lower)"
                             % PLOT_SERIES)
    parser.add_argument("--include-self", action="store_true",
                        help="keep %s in the tables and plot" % SELF_MODULE)
    parser.add_argument("--no-marks", action="store_true",
                        help="leave the mark list out of the summary")
    args = parser.parse_args()

    path = args.savedvariables or find_savedvariables()
    if not path:
        raise SystemExit(
            "could not find EllesmereUISecretsDiag.lua — pass it as an argument.\n"
            "It appears under WTF/Account/<account>/SavedVariables/ after a\n"
            "/reload or logout, not while the recording is still running.")
    if not os.path.exists(path):
        raise SystemExit("no such file: %s" % path)

    db = load_savedvariables(path)
    recordings = get_recordings(db)
    if not recordings:
        raise SystemExit("no recordings in %s — run /euidiag rec start in game" % path)

    say = Report()
    say_source(say, path, recordings)
    if args.list:
        say()
        for position, rec in enumerate(recordings, start=1):
            say(describe(rec, position))
        remind_to_drop(say, path, recordings)
        if args.out:
            say.save(args.out)
        return

    which = args.recording if args.recording is not None else len(recordings)
    if not 1 <= which <= len(recordings):
        raise SystemExit("no recording #%d (there are %d)" % (which, len(recordings)))
    rec = recordings[which - 1]

    say("recording  %s", describe(rec, which).lstrip())
    say()
    summarise(say, rec, args.top, args.include_self, not args.no_marks)
    if args.csv:
        print()
        write_csv(rec, args.csv)
    if args.share:
        print()
        write_share(rec, args.share, args.include_self, args.share_points, path)
    if args.plot:
        print()
        lines = args.plot_top if args.plot_top is not None \
            else min(PLOT_SERIES, args.top)
        plot(rec, args.plot, lines, args.include_self, path)
    remind_to_drop(say, path, recordings)
    if args.out:
        print()
        say.save(args.out)


if __name__ == "__main__":
    sys.exit(main())
