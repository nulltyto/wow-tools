---
name: eui-addon-debug
description: Debug a reported EllesmereUI bug correctness-first — reproduce it, locate the edge, verify Blizzard's own contract before theorizing, fix it, then account for the cost. Use this skill whenever a bug report, a user complaint, or an in-game symptom arrives for the EllesmereUI/EUI addon suite: "this option does not work", "it shows when it should not", "why does this only happen in combat", "the bar flickers", "let's find and fix this issue" — and before changing any event handler, guard, or visibility rule in this codebase. It sequences the other skills: ellesmereui-search to locate, wow-api-search to verify Blizzard's behaviour, wow-secret-values for combat-time reads, ellesmereui-pr-check at the ship gate. Load it before you form a theory, not after you have one.
---

# EllesmereUI Addon Debug

A UI bug in this codebase is almost never a Lua mistake. It is a wrong belief
about what the client does and when it does it. The fix is cheap; the belief is
what costs a day, and a belief that is never checked ships inside a code comment
and reads like a fact to the next person.

This skill is the order to work in. Follow it top to bottom.

## If the report is not "wrong", this is the wrong loop

This one is for a behaviour that is incorrect.

A request to make something cheaper — "find optimization opportunities", "why do
I drop frames", "audit this module for hot paths", anything arriving with fps
numbers attached — inverts the first step: there, the measurement comes before
the code, because the module's share of the frame decides whether the audit is
worth running at all. Load `eui-perf`, and come back here only if the profiling
turns up a behaviour that is wrong rather than slow.

A request for something that does not exist yet — "can we add", "is it possible
to", "do it like that other thing" — is `eui-addon-feature`. Its middle steps
differ: find the sibling the request names and match every dispatch site it
has, and decide what a stored value resolves against before writing any of it.

## Correctness first, cost a close second

Both are gates. They are not the same gate and they are not checked at the same
moment.

**Correctness is not "the reporter's case now behaves".** It is: the option does
what its own tooltip promises, in every state, in both directions, including the
states nobody reported. A fix that satisfies the report and quietly breaks a
neighbouring case has not passed this gate.

**Cost is checked after the behaviour is right, and it is reported without being
asked.** `.github/CONTRIBUTING.md` makes "zero cost unless enabled" and "low cost
when enabled" acceptance criteria, so a fix that cannot state its cost is not
finished. But a cheaper wrong answer is still a wrong answer: never trade a
correctness property for latency or allocations without saying so out loud and
letting the user decide.

That last clause is the one that gets skipped. See **Name the failure
direction** below.

## The loop

### 1. Pin the report to something you can run

A report names a symptom, not a case. Turn it into a case you can trigger on
demand, and say which class and spec you are testing on — half of these bugs are
timing-dependent and the reporter's latency is not yours.

Write down the state the option is in, the exact action, and what should have
happened. If the reporter's class is unknown, pick one that covers both sides of
the distinction being tested (a healer with hard casts and instants, a class with
channels).

### 2. Locate the edge, do not read the file

The tree is ~137 Lua files and single files over 1 MB. Use `ellesmereui-search`
to find the module that owns the setting, the line that reads it, and the handler
that acts on it. Raw `grep` across the tree followed by `sed -n` on line ranges
works, and it costs a dozen round trips to learn what one index query answers.

A report names a **label**, not a key — "Always Show Buttons", not
`alwaysShowButtons`. The label lives in the options row that also names the
key, so start there: Grep the module's `_Options.lua` for the quoted label and
read the `getValue`/`SGet` beside it. That one grep converts the report into
something the index can answer.

Find all of these before editing anything:

- the settings key and its default — and its `scope`, if it has one. A
  CooldownManager key can be held **per spell**, in which case there is no one
  default and no one value: an unset key inherits from the bar tiers. A fix
  aimed at the profile-level key of the same name moves nothing.
- every site that reads the key (a guard is often duplicated and only one copy is wrong)
- **every caller of every function you are about to change** — the `callers`
  field on the `symbols.jsonl` record. This is the step that gets replaced by
  `grep -n` on a 13,000-line file, once per function, and it answers "what else
  breaks" that grep does not. A record carrying `caller_ambiguity` instead is
  telling you to grep; a record with `callers` is telling you not to. Read
  `aliases` with it: a shared helper here is a file-local bound onto
  `EllesmereUI`, so most of its callers name it something other than what the
  definition line says, and the count only makes sense once you know that.
- every event the handler is registered for
- the git history of the guard — `git log -S"<key>"`. A guard that used to be
  correct tells you which change broke it, and the commit message usually says
  what it was buying.

### 3. Verify the contract before you form a theory

**Do not assert from memory when an event fires, what it carries, what order two
events arrive in, or what a field means.** Use `wow-api-search`. This is the step
that gets skipped, and skipping it is what produces a fix that works for the
wrong reason.

Look up, every time:

- **the full payload of every event the handler is registered for.** Not the
  argument you expected — all of them. The discriminator you need is usually a
  later argument. `SPELL_UPDATE_COOLDOWN` sends four, and the fourth,
  `startRecoveryCategory`, is how Blizzard's own `CooldownViewer` tells a global
  cooldown apart from a spell cooldown. An addon that binds only the first
  argument has to re-derive that from cast bars and timers instead.
- **argument positions per event.** `UNIT_*` events lead with a unit token; most
  others do not. One `OnEvent` serving both must bind each event at its own
  offset, or a variable named `spellID` silently holds something else.
- **the constant behind any "category", "type", or "index" value.** Blizzard
  compares against named constants, and the index carries their values.
- **Blizzard's own handler for the same problem.** Grep `Blizzard_*` for the
  event. If the default UI solves this, its solution is the specification, and
  it is already correct about the timing you were about to guess.
- **whether a secure snippet can do what your fix needs**, if the fix runs in
  one. `Blizzard_RestrictedAddOnEnvironment/RestrictedFrames.lua` defines the
  whole frame-handle surface as `function HANDLE:Name`; the environment file
  beside it lists the callable globals. `wow-api-search` documents both. A
  method missing from that file is missing, not merely undocumented, and
  designing around one that is not there costs the whole fix.

Then state your theory with the source attached. "`UNIT_SPELLCAST_START` lands
after the server acknowledges, so a same-frame read sees nothing" is a claim. It
belongs in a comment only once something confirms it.

If the answer is genuinely not in the API — event *ordering* often is not — do
not skip to designing around it. Measure it. That is step 4.

**If the verdict is "not a bug", stop and say so.** Report the model the code
actually implements, the evidence, and what the reporter believed instead. Then
offer the options — do nothing, change the default, add an opt-in — and let the
user pick. Do not start building the one you prefer.

When the user picks one, the work is now a feature rather than a fix: load
`eui-addon-feature` and follow it from its step 2. "Yes, do it" reads like
permission to edit, which is where the design steps get skipped -- and a
feature has failure modes this loop does not cover, chiefly a value that
resolves correctly on the character that wrote it and on no other.

### 4. When the source cannot answer it, trace it in the client

Blizzard's source says what the events are and what they carry. It does not say
what the client actually sent, in what order, for the spell in the report. That
gap is where a plausible theory survives to become a comment nobody can check.

You cannot play the game, but you can ship a tracer and have the user run one
case. Ten seconds of real trace outranks any amount of reasoning about ordering:

    python3 scripts/new_tracer.py AuraTrace --events UNIT_AURA --unit player

That writes a loadable addon into the client's `AddOns/`, prints every dispatch
with a frame number, and prints the in-game steps. Use it, rather than writing
the addon by hand, because the ways a hand-written one fails are all silent —
the client does not report them, so the trace comes back empty and reads like a
disproved theory:

- **a folder with no `.toc` does not load at all.** Describing the `.toc` in a
  comment is not writing one.
- **an `## Interface` below the running build** greys the addon out as
  out of date. A neighbouring `.toc` lists every build it supports; the client
  is on the newest of them, not the first.
- **sending the file to the user is not installing it.** It has to be written
  into `Interface/AddOns/<Name>/`.
- **`tostring` on a value the client classifies raises**, which loses the trace
  in exactly the combat case being investigated.

Ask for a full restart rather than `/reload` — a new addon folder needs the list
rebuilt — and say what each possible shape of the output would prove *before*
seeing it, so the reading is not fitted to the theory afterwards. The
distinction worth designing the trace around is almost always **same frame
versus adjacent frames**: a remove and an add of a new instance in one frame is
a replacement, and the same pair one frame apart is a real drop.

Remove it when the question is answered: `scripts/new_tracer.py <Name> --remove`.

If you extend the generated Lua, run it against the harness before sending it —
`lua5.1 scripts/tracer_harness.lua <the generated .lua>` fires stubbed events at
it and checks it prints. A tracer that loads and stays silent is indistinguishable
from a theory that was wrong.

### 5. Design for correctness, and name the failure direction

Every guard fails in a direction. Choose it deliberately and write it in the
comment:

| Shape | Reads as | Use when |
|---|---|---|
| Late, never wrong | the feature lags on an unclassified case, and is never incorrect | almost always |
| Wrong, never late | it responds instantly and is sometimes incorrect | only when the incorrect frame is invisible |
| Silent approximation | correct in the cases tested, unknown elsewhere | never ship this unnamed |

Then, before writing any code, list the cases where the fix is **knowingly**
approximate and tell the user. An approximation the user has not been told about
is a bug you chose on their behalf.

Concrete: a guard that learns a spell's behaviour by watching it means the first
use after every `/reload` takes the fallback path, and a spell whose behaviour
changes with a proc stays misclassified. Both are defensible. Neither is
defensible as a footnote after the commit is written.

Warning signs that step 3 was skipped and this design is guesswork:

- a comment asserting event order or timing with nothing behind it
- a session-learned lookup table classifying something the API can answer directly
- `C_Timer.After(0)` used to "let the client catch up" — this waits one frame,
  which is shorter than any real round trip, so it reads the same nothing
- reading argument 1 of an event whose payload you have not opened
- a `pcall` wrapping a read to make an error go away rather than to handle a
  secret value

### 6. Write the in-game checklist before you write the fix

You cannot run the game. The user is the test harness, so the checklist is the
deliverable — and writing it first exposes cases the design does not handle,
while changing the design is still cheap.

A checklist covers:

1. the reported case
2. the inverse case (the behaviour that must **not** change)
3. **the option toggled off** — the untouched path, confirmed untouched
4. the first use after `/reload`, if any state is learned at runtime
5. the proc, the cancel, the interrupt, the queue-spam — whatever makes the
   normal case abnormal
6. **the case your fix is knowingly weakest at**, named as such
7. a class or spec you are not testing on, if the distinction depends on it

Number them so the user can answer by number. Say what the correct result looks
like for each, not just what to press.

### 7. Cost it, unprompted

State the cost when you report the fix, before being asked. Cover:

- **per-frame work** — any new `OnUpdate` or tick is the expensive kind; say if
  there is none
- **allocation on a chatty event** — a closure or table per event in combat is
  the usual regression. `SPELL_UPDATE_COOLDOWN` fires on every cooldown start,
  charge refill, and press
- **new registrations** — unit-filter them with `RegisterUnitEvent` and say how
  often each fires
- **unbounded growth** — a table keyed by spellID is fine; one keyed by GUID is not
- **what got cheaper**, if anything. Removing a per-event closure often pays for
  the whole change

If the fix is a genuine cost/correctness trade, present both options and let the
user choose. Do not resolve it silently in favour of either one.

### 8. Ship gate

Then, and only then, `ellesmereui-pr-check`. It is the house-style and PR gate,
not a correctness review, and it does not check anything above.

## Discarding work

A change abandoned mid-way is still the user's. Say what will be lost and get
agreement before touching the tree — but prefer `git stash push -m "<what it
was>"` over `git restore`, and say which you used. A stash costs nothing, keeps
the work reachable, and does not need the user to decide now whether they will
want it back. `git restore` is unrecoverable: the edits were never committed, so
they are not in the reflog either.

## Combat-time reads

If the fix reads spell cooldowns, charges, auras, action bar state, or unit info
inside combat, load `wow-secret-values` before writing the guard. A value the
client classifies cannot be compared, and the error only appears in instanced
combat — never at a dummy, which is where it will be tested.

The tell is a guard that works in testing and raises in a raid. The fix is not a
`pcall` around the comparison; it is not comparing the value.
