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

- the settings key and its default
- every site that reads the key (a guard is often duplicated and only one copy is wrong)
- **every caller of every function you are about to change** — the `callers`
  field on the `symbols.jsonl` record. This is the step that gets replaced by
  `grep -n` on a 13,000-line file, once per function, and it answers "what else
  breaks" that grep does not. A record carrying `caller_ambiguity` instead is
  telling you to grep; a record with `callers` is telling you not to.
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

If the answer is genuinely not in the API — event *ordering* often is not — say
so, and design so that being wrong about it is survivable. That is step 4.

### 4. Design for correctness, and name the failure direction

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

### 5. Write the in-game checklist before you write the fix

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

### 6. Cost it, unprompted

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

### 7. Ship gate

Then, and only then, `ellesmereui-pr-check`. It is the house-style and PR gate,
not a correctness review, and it does not check anything above.

## Combat-time reads

If the fix reads spell cooldowns, charges, auras, action bar state, or unit info
inside combat, load `wow-secret-values` before writing the guard. A value the
client classifies cannot be compared, and the error only appears in instanced
combat — never at a dummy, which is where it will be tested.

The tell is a guard that works in testing and raises in a raid. The fix is not a
`pcall` around the comparison; it is not comparing the value.
