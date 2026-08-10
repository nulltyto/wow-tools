---
name: eui-perf
description: Make EllesmereUI cheaper per frame, measurement-first — establish what the module actually costs before auditing it, read the number from the client's own profiler rather than from frame rate, and A/B the change in one session. Use this skill whenever the task is performance rather than correctness in the EllesmereUI/EUI addon suite: "find optimization opportunities", "this module is slow", "why do I drop frames in dungeons", "reduce the CPU cost of X", "audit this file for hot paths", "we're mid-pack on the addon performance charts", or any request that arrives with fps numbers attached. Load it before the first audit, not after. It sequences the work: the EllesmereUISecretsDiag addon and tools/perf for the measurement, then ellesmereui-search to find the code, wow-api-search for Blizzard's contract, wow-secret-values before any compare-before-set guard, and ellesmereui-pr-check at the ship gate.
---

# EllesmereUI Performance

A performance request is a measurement problem wearing a code problem's
clothes. The code reading is easy and produces a long list; the measurement
tells you which line of that list is worth an hour, and it is usually none of
them.

The failure this skill exists to prevent: audit first, measure last, ship a
change that removes real work and moves nothing, because the module was 0.4% of
the frame before anyone looked.

## Measure first. Always.

**Do not audit a module until you know its share of the frame.** Not its
plausible share — its measured share, in milliseconds, next to the frame budget.

This is not advice to be thorough. It is arithmetic that decides whether the
task is worth doing at all, and it takes one command.

### The instrument is already installed

`EllesmereUISecretsDiag` (in this repo, `addons/`) reads `C_AddOnProfiler`,
which the client runs continuously for every user — no CVar, no `/reload`, no
overhead you have to ask for.

| Command | Answers |
|---|---|
| `/euidiag cpu` | per-module ms, right now, against the frame budget |
| `/euidiag cpu all [n]` | the same for every addon loaded, not just this suite |
| `/euidiag cpu spikes` | how often each module passed 1/5/10/50/100 ms — **the 1% low question** |
| `/euidiag cpu window` | live rolling averages with a one-minute plot |
| `/euidiag mem` | per-module memory, with the delta since the last reading |
| `/euidiag rec start [interval]` … `rec stop` … `/reload` | samples all of it into SavedVariables |

The client writes SavedVariables only on `/reload` or logout, so the recording
does not exist as a file until one of those happens. Say that in the
instructions you give the user, or the analysis step fails on a missing file.

Then, offline:

```bash
./tools/perf/euidiag-perf.py                 # newest recording, summarised
./tools/perf/euidiag-perf.py --share run.json
```

That summary carries the frame budget, mean vs P95 per module, in-combat vs
out-of-combat split, per-encounter rows, the real spike steps, and the worst
frames with the module that led each one. `tools/perf/README` explains what
each section means and what sample rate can and cannot buy.

If the addon is not installed in the user's game, installing it is the first
step of the task, not a detour: `./install.sh --addons EllesmereUISecretsDiag`.

### The instrument is not free, and it is loud

`EllesmereUISecretsDiag` frequently reads as one of the most expensive addons
loaded, and at a fast sample rate it can produce spikes of its own. It only runs
because you asked for measurements, so `euidiag-perf.py` leaves it out of every
table and out of the plot; `--include-self` puts it back, and it is worth
looking at once per session.

Two consequences for how you read a run:

- **The frame budget is inflated by the observer.** A module's share of "all
  addons" is overstated whenever the recorder is one of the addons. Compare
  against the client frame time, and re-read the ranking with `--include-self`
  before concluding a module leads.
- **Some spikes are the recorder's.** Before attributing a hitch to a module,
  check whether the diagnostics addon stepped at the same moment. Sampling at
  `0.05` costs more than sampling at `1`, and the default of one sample a second
  is the honest setting for attribution work.

For a long run where the cost matters more than the resolution, `rec start 1` and
close the `cpu window`. The window redraws and plots continuously; the recorder
alone does not.

### Frame rate is the weakest instrument available

A `/framerates` run conflates addon cost with scene cost, cannot attribute to a
module, and cannot be repeated identically. Two runs in the same dungeon differ
by more than most fixes are worth.

Use it for one thing only: catching engine-side work the Lua profiler does not
bill, such as reparenting traffic. Even then, bracket it in the same session and
say the noise floor out loud first.

**Do not reach for `/console scriptProfile 1`.** It needs a reload, costs
global overhead, double-counts anything a module runs inside a Blizzard frame,
and gives no spike counters. `C_AddOnProfiler` supersedes it and is already on.

### The attribution rule, before you believe any row

The engine bills a script handler's whole call tree to the addon whose
execution context **created the frame carrying the handler** — not to the file
the code lives in. Work handed to a frame the parent created is billed to the
parent. A module reading suspiciously low, or `Core (parent)` reading
implausibly high, is usually this. `EllesmereUI_Ticker.lua` documents the rule
and the two ways to stay on the right side of it.

## State the ceiling before you spend the turn

Once you have the number, do the division and say it, in one sentence, before
proposing any work:

> This module measures 0.049 ms of a 12.4 ms frame. Deleting it entirely buys
> 0.4%. I can audit it as asked — here is what the ceiling is.

The ceiling is not a reason to refuse the task. The user may want the module
cheaper for reasons that are not frame rate, and asking for a specific module is
their call. It is a reason to say the size of the prize **before** the audit
rather than as a calibration note after it.

Sources for a ceiling, cheapest first:

1. `/euidiag cpu` for a live number.
2. **Prior commits.** `git log --grep=ms/frame` and `git log -S"ms/frame"` —
   this codebase's own perf commits record their measurements in the message.
   A commit saying `0.049 vs 0.009 ms/frame` is a ceiling that costs one command
   and no in-game time.
3. The sibling perf commits beside it. If three other modules have unmerged perf
   work and yours does not, that ordering is evidence.

## The loop

### 1. Measure, attribute, and rank modules

Before reading any Lua. `/euidiag cpu all` and `cpu spikes` rank every loaded
addon and every module in the suite. That ranking, not intuition and not the
user's guess, chooses the target.

If the request names a module, still measure the whole suite — you need the
denominator, and the answer to "is this the right module" is free once you have
it.

### 2. Separate steady-state from spikes

They are different bugs with different fixes and different instruments.

| Symptom | Read | Usually |
|---|---|---|
| Low median fps | `RecentAverageTime`, the MEAN column | per-event or per-frame work |
| Bad 1% lows | `cpu spikes`, the P95/MAX columns, spike steps | spawn/despawn, zone change, first-use |

A module with a low mean and a high P95 is spiking, and spikes are what get
felt. Optimising its steady state will not move the number the user is
complaining about.

### 3. Find the code with the index, not with grep

`ellesmereui-search` — `--ensure` first. It answers the three questions an audit
asks, each in one grep:

- **which handlers exist and how often they can fire** — `events.jsonl` gives
  every `RegisterEvent`/`RegisterUnitEvent` site for an event across the suite
- **what breaks if I change this** — the `callers` field. A function with
  `caller_count: 1` has a blast radius of one line, and that is the whole answer
- **what a settings guard costs** — `settings.jsonl` for the key and its reads

Grepping a 1 MB file for `RegisterEvent` is the thing this index exists to
replace, and it costs a dozen round trips to learn less.

### 4. Verify the cost model before believing it

**Do not assert an event's frequency, an API's cost, or a payload from memory.**
Use `wow-api-search`.

- The event's **full payload** — a delta payload you did not know about
  (`UNIT_AURA`'s `updateInfo`) turns a rescan into an incremental update, and
  that is a bigger win than any micro-optimisation of the rescan.
- Whether **Blizzard's own code** does the same thing. If the default UI solves
  this problem, its solution is the specification and it is already tuned.
- Whether the expensive call is expensive. "`UnitIsUnit` is an expensive WoW
  API" is a claim, and an unverified claim that ranks a finding HIGH is worse
  than no ranking.

Op-count estimates are not measurements. `~75,000 ops/sec` over five-element
integer arrays is a number that looks like evidence and is not. Rank by measured
ms or say the ranking is a guess.

### 5. Check the guard is legal before you write it

The default optimisation in this codebase is "compare before set" — skip the
`SetValue`/`SetVertexColor` when nothing changed. In restricted combat that
comparison **raises**, and the value being compared is often exactly the one you
want to guard: cast bar progress, aura durations, health on a restricted unit.

Load `wow-secret-values` before adding any change-detection guard on a value
that comes from the client. An unguarded setter in a per-frame driver is
sometimes deliberate, and the comment above it usually says so — read it before
calling it a finding.

### 6. Name what the change costs in correctness

A performance change is a correctness change with a different motive. Deferring
work by one frame, coalescing events, caching a value that can go stale, and
skipping a restore all move behaviour.

Say which, in the comment and in the report:

| Shape | Reads as | Watch for |
|---|---|---|
| Coalesce into a per-frame queue | lands one frame late, never wrong | stale entries after recycle or unit change |
| Cache a derived value | correct until an input changes silently | what invalidates it, and whether you registered for that |
| Skip work on a reused object | correct if reuse is always same-kind | the first use after `/reload`, and the cross-kind reuse |

Then write the in-game checklist **before** the code. You cannot run the game;
the user is the harness, and writing the checklist first exposes the case the
design does not handle while changing the design is still cheap. Cover the
reported case, the inverse, the option toggled off, the first use after
`/reload`, and the case the change is knowingly weakest at. Number them.

### 7. A/B in one session, or do not claim a win

Cross-session comparisons cannot resolve anything smaller than the run-to-run
spread, which in a dungeon is a couple of milliseconds.

The clean A/B, in one sitting:

```
/euidiag rec start 0.1   ... one pull ...   /euidiag rec stop   /reload
git stash                                    # remove the change
/euidiag rec start 0.1   ... comparable pull ...  rec stop   /reload
git stash pop
./tools/perf/euidiag-perf.py --list          # both recordings are in the file
```

`euidiag-perf.py -r <n>` reads either one, and modules keep the same colour
between plots so two traces read side by side.

For a single function, `C_AddOnProfiler.MeasureCall(func, args)` returns
`elapsedMilliseconds` and `allocatedBytes` for one call — the direct before/after,
no pull required.

**Report the honest result.** A change that removes real work and measures flat
is hygiene, and the commit message must not claim a frame-rate improvement it
cannot show. Say "no measurable change, less work done" and let the user decide
whether to keep it.

### 8. Ship gate

`ellesmereui-pr-check`. It is house style, not a correctness or performance
review, and it checks nothing above.

## If you fan out to subagents

An audit dimension per agent is reasonable on a file this size. Three things
have to go in every prompt or the results come back confidently wrong:

- **the measured ceiling.** Without it every agent returns findings ranked HIGH,
  because HIGH is relative to nothing.
- **what is already fixed, including unmerged branches.** Otherwise agents
  rediscover work that exists and you filter it by hand afterwards.
- **the ranking rule** — measured ms, or say it is a guess. And the instruction
  to check whether the code path is even live before costing it: a whole audit
  dimension in this codebase turned out to be inert because an engine-side path
  had taken it over.

Subagents cannot call skills. Give them the index paths, or accept that they
will grep the megabyte again, once each.

Verify every load-bearing claim against the source before acting on it. On the
last full audit, three of four headline findings did not survive that check.

## What is not a performance finding

- A loop over five elements. `O(n*m)` on tiny arrays is a shape, not a cost.
- An unguarded setter with a comment explaining why it is unguarded.
- A path that does not run. Check the feature is enabled and the branch is live
  before costing it.
- Anything in a module you have not measured.
