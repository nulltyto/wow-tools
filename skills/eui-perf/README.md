# eui-perf

A [Claude Code](https://claude.com/claude-code) skill for making the
[EllesmereUI](https://github.com/EllesmereGaming/EllesmereUI) World of Warcraft
addon suite cheaper per frame.

It is the performance counterpart to [`eui-addon-debug`](../eui-addon-debug/),
and like that skill it does no lookup of its own — it is the order to work in,
and it calls the other skills and this repo's own measurement tooling in
sequence.

## The problem it solves

A performance request reads like a code problem and is a measurement problem.
Reading the code is easy and produces a long ranked list; nothing in that list
is worth an hour until you know what the module costs.

The failure mode this exists to prevent, taken from a real session: four
thorough audit agents, ~150 turns, two implemented fixes and a live test, all
spent on a module whose own prior commit message recorded it at **0.049 ms of a
12.4 ms frame**. That number was on screen in the first tool result. The fixes
measured flat, which the arithmetic had predicted before the first audit
started, and the modules that actually held ~3 ms were named in the same `git
log` output.

So the first rule is the whole skill: **establish the ceiling before you spend
the turn**, and say it out loud.

## What it routes to

| For | Use |
|---|---|
| The measurement | `/euidiag cpu`, `cpu spikes`, `cpu all`, `rec` — [`EllesmereUISecretsDiag`](../../addons/EllesmereUISecretsDiag/) |
| The analysis | [`tools/perf/euidiag-perf.py`](../../tools/perf/) |
| Finding the code | [`ellesmereui-search`](../ellesmereui-search/) — `events.jsonl` for handlers, `callers` for blast radius |
| Blizzard's contract | [`wow-api-search`](../wow-api-search/) — event payloads, whether the expensive call is expensive |
| Before a compare-before-set guard | [`wow-secret-values`](../wow-secret-values/) |
| The ship gate | [`ellesmereui-pr-check`](../ellesmereui-pr-check/) |

All of the measurement pieces already ship in this repo. The skill's main job is
knowing they exist at the moment a performance question arrives, which is the
moment they are easiest to forget.

## Why not frame rate

`/framerates` conflates addon cost with scene cost, cannot attribute to a
module, and cannot be repeated identically — two runs in the same dungeon differ
by more than most fixes are worth. `C_AddOnProfiler`, which the client keeps
running for everyone with no CVar and no reload, gives per-module milliseconds
and cumulative over-1/5/10/50/100 ms counters. Those counters are the 1%-low
question asked directly.

The older `/console scriptProfile 1` path needs a reload, costs global overhead,
and double-counts anything a module runs inside a Blizzard frame. It is not the
tool.

Frame rate keeps exactly one job: catching engine-side work the Lua profiler
never bills, such as reparenting traffic. Bracket it in one session and state
the noise floor before predicting a win.

## Two caveats the skill leads with

**Attribution.** The engine bills a script handler's entire call tree to the
addon whose execution context created the frame carrying the handler, not to the
file the code lives in. Work handed to a frame the parent created is billed to
the parent, so a module can read low while its parent reads high.

**The observer.** `EllesmereUISecretsDiag` is often among the most expensive
addons loaded and can generate spikes of its own at a fast sample rate. It is
excluded from the analysis tables for that reason (`--include-self` restores
it), and the skill says to check whether a hitch was the recorder's before
attributing it to a module.

## Limitations

It cannot run the game. Every measurement in the loop is something the user
performs and pastes back, so the skill is written around producing numbered,
answerable instructions rather than around running anything itself. It also
does not model the addon's code — that is `ellesmereui-search`'s job, and the
skill hands off rather than grepping.

## Attribution

EllesmereUI is by Ellesmere Gaming. This is unofficial third-party tooling; it
contains no addon code. World of Warcraft is a trademark of Blizzard
Entertainment, Inc.
