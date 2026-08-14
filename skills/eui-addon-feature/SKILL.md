---
name: eui-addon-feature
description: Build a requested EllesmereUI feature by extending what is already there - answer the question with sources before designing, find the sibling the request names and match every dispatch site it has, decide what resolves at write time and what resolves per character, then cost it and ship it. Use this skill whenever a feature request, an enhancement, or a "can we make it do X" arrives for the EllesmereUI/EUI addon suite - "is it possible to", "we should add", "add a Y like the existing Z", "give the user the ability to", a request forwarded from Discord, or a not-a-bug report the user decided to build. Load it before designing, not after the first edit. It sequences the other skills - ellesmereui-search to find the precedent and its callers, wow-api-search for Blizzard's contract, wow-secret-values for combat-time reads, ellesmereui-pr-check at the ship gate. For a behaviour that is wrong use eui-addon-debug; for one that is slow use eui-perf.
---

# EllesmereUI Addon Feature

A feature request in this codebase is almost never a new design. It is an
existing one extended by one variant, and the failure mode is not a Lua
mistake -- it is a variant wired into four of the five places its sibling
lives, or a value that resolves correctly on the character that built it and
nowhere else.

This skill is the order to work in. Follow it top to bottom.

## Which loop this is

| The request says | Load |
|---|---|
| this does not work / it shows when it should not | `eui-addon-debug` |
| this is slow / I drop frames / audit for hot paths | `eui-perf` |
| can we add / is it possible / do it like that other thing | this one |

`eui-addon-debug` hands over here explicitly: when a bug report turns out to be
"not a bug" and the user picks an option to build instead, the work is a
feature from that moment. So is a bug fix whose real answer is a new setting.

## Correctness first, cost a close second

The same two gates as the debug loop, and they mean the same thing here.
Correctness is not "the requested case now works" -- it is that the feature
behaves on every character, in both directions of every toggle, including the
states nobody asked about. Cost is reported without being asked, because
`.github/CONTRIBUTING.md` makes "zero cost unless enabled" and "low cost when
enabled" acceptance criteria.

A feature adds one thing that debugging does not: **the old behaviour is a
correctness property now.** Existing saved state was written under the old
rules and must keep working untouched. If it cannot, the change needs a
migration, and a migration is a decision for the user rather than a detail.

## The loop

### 1. Answer the question before designing anything

Most requests arrive as a question -- "is it possible to..." -- and the answer
is usually that the module already does most of it. Say what the code does
today, with file and line, say what Blizzard provides, and say which part of
the request is genuinely missing. Then recommend a shape and let the user pick
it.

This is cheap and it is the highest-leverage step in the loop. A request to
make spec icons follow the character turned out to need no new API at all: the
module already called `C_SpecializationInfo.SetSpecialization(index)`, and the
only real question was what the slot should store. Two sources and a
recommendation settled the design in one round trip.

Do not start editing on "yes, do it". That is the start of step 2.

### 2. Find the precedent, and enumerate every place it lives

The request usually names the sibling: "like dynamicrez", "the same way the
bar types work". Take it literally -- this codebase is built out of variant
families, and a new member of a family is a wiring job with a known shape.

```bash
# The kind strings are table-driven config. The index holds definitions and
# identifier references, not string literals compared against a field, so this
# first step really is a grep -- and it is the only one that is.
grep -rn 'kind == "dynamicrez"' --include=*.lua . | grep -v '/\.release/'

# Everything after it the index answers: what each dispatch site is reached
# from, and what else calls the functions you are about to widen.
python3 <ellesmereui-search>/scripts/query.py callers SlotDisplay
python3 <ellesmereui-search>/scripts/query.py def SpecIndexFor
```

`<ellesmereui-search>` is that skill's own directory, beside this one.

Write the sites down before editing. The new variant appears at **every site
the sibling appears at**, or you state why it does not. Missing one does not
fail a test and does not fail to compile: it produces a feature that draws
correctly and does nothing when pressed, or fires correctly and draws a
question mark.

The same applies to the options side. A new variant usually needs the picker
that offers it, the preset that seeds it, the editor caption that names it, and
the usability filter that hides it -- and those are four different files.

Find these before editing anything:

- every dispatch site of the nearest sibling, by its literal string
- **every caller of every function you are about to widen** -- the `callers`
  field, via `query.py callers <name>`. Widening a signature is the usual
  feature edit and this is the question grep does not answer
- the settings keys the sibling reads, and their defaults
- whether any other addon in the tree consumes the family (a shared kind
  string crosses module boundaries)

### 3. Verify Blizzard's contract before you design against it

Same rule as the debug loop, same reason: **do not assert from memory** what an
API returns, when an event fires, what a payload carries, or whether a value is
readable in combat. Use `wow-api-search`:

```bash
python3 <wow-api-search>/scripts/query.py func SetSpecialization
python3 <wow-api-search>/scripts/query.py event ACTIVE_PLAYER_SPECIALIZATION_CHANGED
```

For a feature specifically, look up:

- **the signature you are about to call**, including what it returns. A feature
  that ignores a `success: bool` has no way to tell the user it failed.
- **the taint marking** -- `secret_arguments` on the entry. The PR template asks
  whether the change is taint-safe, and that line is answerable in one lookup
  rather than by reasoning.
- **the maximum, not the current value.** If the feature enumerates anything
  the client varies by class, spec, or expansion, find the API that gives the
  ceiling. Hardcoding the number you can see today is step 4's failure in
  advance.
- **Blizzard's own version of the thing you are rendering.** If the default UI
  already draws this -- a countdown, a duration string, a cooldown swipe -- its
  formatting is the specification. Match its rounding before writing your own:
  a custom formatter that rounds up beside Blizzard's that floors reads as an
  off-by-one to the player, and only in game.

### 4. Decide what resolves at write time and what resolves per character

**This is the step this loop exists for, and it is the one that escapes.**

Anything saved -- a palette, a preset, a profile key, a per-character list --
is written by one character and read by another. For every value the feature
stores, say which of these it is:

- **an identity**, meaningful everywhere (a spellID, a specID, an item link)
- **a position**, meaningful only against whoever resolves it (slot 3, spec 2)
- **a capability of the writer**, which is the trap (how many specs *this*
  character has, which spells *this* character knows)

The third one is never safe to bake in. A preset builder that enumerates the
building character's specialization count seeds three entries on a paladin, and
the druid who loads that same preset can never reach a fourth -- not from the
preset, and not by hand from the picker either, because the picker enumerated
the same three. The feature was *about* portability and still shipped
non-portable, because the count came from the character in the chair.

Walk these explicitly before writing the code:

1. another character of the **same** class
2. another character of a **different** class, with more of the thing
3. another character of a different class with **fewer** of the thing
4. a preset or profile built on one and loaded on the other, in both directions
5. the entry the current character cannot use -- is it hidden, greyed, or gone?
   Say which, and check the setting that decides (`hideUnusable` and its kin
   default to hiding, so "it disappeared" is the expected report)
6. deleting the entry and adding it back -- can the character that cannot use
   it still restore it for the one that can?

Case 6 is the one that has no workaround once shipped.

### 5. Check the file's budgets before you add to it

Cheap to check, expensive to discover:

- **Lua's 200 main-chunk locals.** Six files in this tree sit within five
  locals of the ceiling, and the next top-level `local` added to one of them
  does not fail a test -- it fails to compile, and the whole module goes dead
  at load. Probe before adding one:

  ```bash
  cp Module/File.lua /tmp/probe.lua && echo 'local __probe = 1' >> /tmp/probe.lua
  luac5.1 -p /tmp/probe.lua && echo "headroom" || echo "AT CEILING"
  ```

  At the ceiling, put the function on `ns` with no local alias, or scope it
  inside a `do ... end` block. The files that are already there say so in a
  comment -- read it rather than spending the last slot.

- **A new user-facing string is a new locale key.** Run
  `bash .tools/extract-locale-keys.sh` and commit the regenerated
  `EllesmereUILocales/_keys.txt`. CI (`.github/workflows/locale-check.yml`)
  fails the PR when it is stale.

- **Cross-addon calls.** Reaching into another addon's `ns` from the options
  page is normal here, but a partially updated install is a real case: guard it
  the way the neighbouring code does (`ns.Foo and ns.Foo(...)`,
  `ns.MAX_PALETTES or 16`) so an older module beside a newer options page
  offers nothing rather than erroring.

### 6. Write the in-game checklist before you write the feature

You cannot run the game; the user is the test harness, so the checklist is part
of the deliverable. Writing it first is what exposes the case in step 4 while
the design is still cheap to change.

Cover, numbered so the user can answer by number:

1. the requested case, on the character the requester used
2. **the character the feature is for** -- the alt, the other class, the one
   with more or fewer of the thing
3. the existing behaviour that must not change, confirmed unchanged (old saved
   state still works, no migration ran)
4. the new thing switched off, or absent -- the untouched path
5. the entry this character cannot use, with the usability filter both on and
   off
6. the case the feature knowingly cannot do, named as such

Say what the correct result looks like for each, not just what to press.

### 7. Cost it, unprompted

Before being asked, state: per-frame work (say if there is none), allocation on
a chatty event, new event registrations and how often they fire, unbounded
growth, and anything that got cheaper. A feature whose cost cannot be stated is
not finished. If it is a genuine cost/correctness trade, present both and let
the user choose rather than resolving it quietly.

### 8. Ship gate

`/code-review` first if the change is more than a few lines, then
`ellesmereui-pr-check`. The pr-check is the house-style gate, not a correctness
review, and it checks nothing above.

Two things about the base ref, on a fork:

- `check_style.py` resolves its base as `origin/main` first. On a fork, that is
  **your** copy on GitHub, which is usually far behind the real upstream --
  hundreds of commits, in this checkout -- and it makes the diff-scoped check
  report thousands of lines of legacy noise. Pass `--base` explicitly and say
  which ref you used.
- Rebase onto `upstream/main` before opening the PR, then re-run every gate.
  Nothing about the first run survives a 40-commit replay on its own, including
  the regenerated `_keys.txt`.

## Combat-time reads

If the feature reads spell cooldowns, charges, auras, action bar state, or unit
info during combat, load `wow-secret-values` before writing the guard. A value
the client classifies cannot be compared, and the error appears only in
instanced combat -- never at a dummy, which is where it will be tested.

Rendering is not reading: handing a Blizzard aura button a FontString and a
formatter and letting the engine write the number is safe, because the addon
never touches the value. Comparing that same remaining time against a threshold
is not. Know which side of that line the feature is on before promising it.

## Discarding work

A change abandoned mid-way is still the user's. Say what will be lost and get
agreement before touching the tree, and prefer `git stash push -m "<what it
was>"` over `git restore` -- a stash costs nothing and keeps the work
reachable, while uncommitted edits discarded by `git restore` are not in the
reflog either.
