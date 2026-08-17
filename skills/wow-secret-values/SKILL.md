---
name: wow-secret-values
description: Write World of Warcraft addon code that survives secret values — the restricted-combat protection that makes many API results opaque to a tainted addon. Use this skill when writing or reviewing addon code that reads spell cooldowns, charges, auras, action bar state, unit info, or any Blizzard frame field during combat, and whenever a Lua error says "a secret number value", "a secret string value", "invalid value (secret)", or "while execution tainted by". Triggers on "why does this only break in combat", "why does this work at the dummy but not in a raid", "can I compare this value", "is this field secret", "how do I test whether something is secret", "attempt to compare a secret", and on any fix that has to branch on a value the client may classify. Covers which fields are readable, the operations that raise, how Blizzard's own cached booleans stay clean, and how to confirm any of it live in game.
---

# WoW Secret Values

Since the 11.x secret-value system, a tainted addon reading certain APIs in
restricted combat gets back a **secret**: a value it may hold and hand back to
Blizzard, but may not look inside. Code that works at a target dummy fails in an
instance, and it fails as a raised Lua error rather than a wrong answer.

Three separate live errors in one debugging session came from this, all of them
the same mistake made three ways. That is what this skill is for.

## First move

```bash
python3 <skill>/scripts/secret_fields.py <StructureOrFunctionName>
```

`<skill>` is this skill's own directory. It needs only the standard library and
Python 3.9+, and reads the index that ships with the `wow-api-search` skill
(`--index PATH` or `$WOW_API_INDEX` if that skill is elsewhere).

```
$ secret_fields.py SpellChargeInfo
  SECRET  currentCharges     number
  clean   maxCharges         number
  ...
  clean   isActive           bool
```

Blizzard publishes the answer per field as `NeverSecret`. Read it before
designing the branch, not after the error. `--all-clean Spell` lists the whole
usable surface of a system at once.

**A name the index does not hold is undecided, not clean.** Blizzard documents
structures unevenly, and the gaps are not the obscure ones: `AuraData` — the
payload behind every aura on every nameplate — has no generated documentation
at all, so neither `dispelName` nor any other field of it can be looked up here.
The script says so and routes you to the live probe rather than printing a bare
"not in the index", because that message reads like a typo and gets treated as
an absence of caveats. It is the opposite.

## Two failure modes, and only one of them is loud

This is the distinction that decides how a fix has to be tested.

| | What happens | How you find out |
|---|---|---|
| **It raises** | the read, compare, or concat throws | a Lua error, immediately, with the addon named |
| **It reads nil** | a redacted field is simply absent | nothing at all -- a filter matches zero rows, a guard silently inverts |

Everything else in this skill is about the first one. The second has no error
text to search for and no `issecretvalue` answer, because there is no value to
classify — the field is gone. A candidate filter keyed on `dispelName`, a
comparison against `auraData.spellId`, anything that reads a field off enemy
unit data: in restricted content that read can come back nil and the code
around it keeps running with a wrong answer.

You cannot test for this outside restricted content, and unrestricted testing
looks completely clean. Say so in the PR when a change reads unit or aura data
and you have not captured it in an instance.

## What raises

A secret is not a poisoned value that spreads. It is opaque. These raise:

| Operation | Error you will see |
|---|---|
| Compare — `<`, `>`, `==` against a non-secret | `attempt to compare local 'id' (a secret number value, while execution tainted by 'YourAddon')` |
| Concatenate — `..`, `table.concat` | `invalid value (secret) at index 4 in table for 'concat'` |
| Index or call a string method — `s:sub()`, `#s` | `attempt to index local 's' (a secret string value, ...)` |

These are safe: assignment, passing it to a Blizzard API that accepts it,
storing it in a table, and testing `v == nil`.

`tostring`, `format`, `print`, and every comparison inside them are **not** safe.
A log line is the most common way to hit this, because logging is added while
debugging and is the one code path nobody tests in a raid.

## The safe shape

Three habits, in order of how much they buy:

1. **Branch only on fields the index calls clean.** A charge spell at zero
   charges cannot be found with `currentCharges == 0`, because that field is
   secret. It can be found from `isActive`, which is not. Redesigning the
   predicate beats guarding the comparison.

2. **Classify before you touch.** Where a value's status depends on context,
   test it rather than assume it:
   ```lua
   local function isSecret(v)
       if not issecretvalue then return false end
       local ok, r = pcall(issecretvalue, v)
       return ok and r or false
   end
   ```
   `pcall` around the test as well, because `issecretvalue` itself is absent on
   older clients. `issecrettable` is the table equivalent.

3. **Put the whole expression inside `pcall`, not just the call.** The read is
   usually fine and the comparison is what raises, so a `pcall` around the API
   call alone protects nothing:
   ```lua
   -- wrong: the compare is outside
   local ok, id = pcall(item.GetSpellID, item)
   if ok and id == watched then ...

   -- right
   local ok, hit = pcall(function() return item:GetSpellID() == watched end)
   if ok and hit then ...
   ```

Short-circuit order matters too. `not self.isOnGCD and self.cooldownIsActive`
never evaluates the secret operand when the clean one decides the result — which
is how Blizzard's own code stays legal, and why the same expression written in
the other order raises.

## Blizzard's cached booleans are clean

The most useful rule, and the least obvious: **Blizzard's untainted code may
compare secrets and cache the answer as a plain boolean. An addon reading that
cached field gets a clean value.** Only the addon's own comparison would be
secret.

So when a field you need is secret, look for a Blizzard frame field that already
holds the conclusion. In the Cooldown Manager, `currentCharges` is secret but
`frame.wasSetFromCharges` reads a plain `true`/`false` throughout, because
Blizzard set it from its own comparison. That field turned an impossible test
into a one-line one.

Find these by grepping the owning `Blizzard_*` folder for where the field is
written — `wow-api-search` covers this.

## Before trusting a cached field, ask what refreshes it

A cached boolean is exact only after the refresh that maintains it has run. An
addon handler on the same event races Blizzard's handler for it, and if the
addon runs first the field still describes the previous state.

- A hook that fires **from** a Blizzard write reads the field current by
  construction. Prefer these.
- A handler on a shared event does not. Either take the safe direction when the
  two sources disagree, or re-run the check on the next frame from `OnUpdate` so
  a stale read corrects itself regardless of handler order.

## Confirming it live

Nothing above beats a capture from the real client, and the answers change with
context — solo in a city almost everything reads plain, and the same probe in an
instanced pull is the interesting run.

The `EllesmereUISecretsDiag` addon in this repository exists for that:

```
/euidiag secrets          run every probe that fits the current context
/euidiag eval <lua>       classify whatever an expression returns
/euidiag aurarows [set]   several aura filters side by side against one unit
/euidiag chargewatch <id> log a spell cooldown and charge recharge side by side
```

`/euidiag eval` is the fastest way to settle "is this secret here" — it
classifies the result without performing an operation that could raise.

**Reach for these before writing a throwaway addon.** They are installed,
harnessed, and answer in one line. A session spent about twenty-five edits and
six client restarts hand-building a probe for a question `/euidiag eval` was
already built to answer, and then ported the useful half of that probe back
into this addon as `aurarows`. Check what `/euidiag` already does first.

`aurarows` is the one for filter questions: it declares one real AuraContainer
per row, each with its own filter string and `candidateFilters`, binds them all
to the same unit, and shows them stacked. Read the icons, not a count — group
counts are obfuscated and a tainted addon cannot enumerate what it just drew.
Rows must be built outside restricted content, and the command reports the
denial count rather than letting an empty panel read as "nothing matched".

When writing a new probe, run the offline harness before handing a command to
somebody who has to go into combat to use it:

```bash
cd tools/diag && lua5.1 harness.lua
```

It loads the addon under a stubbed API and dispatches every command. A round
trip through a live raid to discover a nil call is expensive; this is not.

## Watching who writes a contested value

Once a texture or frame field has been written from a secret, its getter stops
answering — `IsDesaturated()` returns nil forever after such a write. The state
cannot be read back, so record the **writes** instead, with `hooksecurefunc` on
the setter.

One trap, worth stating because it produced two wrong conclusions before it was
understood: **a post-hook reports nested writes in reverse.** `hooksecurefunc`
chains outward, so a hook registered last runs outermost. When an inner hook
answers a write with its own write, the inner write runs the whole chain and is
recorded while the outer call is still unwinding — the reply is logged before
the write it replies to. To get true call order, wrap the method (record, call
the original, record) rather than post-hook it.

`debugstack()` returns a secret string in combat. Classify it before any string
method touches it, or the write census raises on the very run it was added for.

## Related

- `wow-api-search` — the `NeverSecret` markers, and Blizzard's own code, which
  is the specification for any fix that has to agree with its behaviour.
- `ellesmereui-search` — where the addon's own code reads the field.
