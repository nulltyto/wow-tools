# eui-addon-debug

The order to work in when a bug report arrives for EllesmereUI: **correctness
first, cost a close second**, with Blizzard's own contract verified before any
theory is formed.

## What this is for

A UI bug in this codebase is rarely a Lua mistake. It is a wrong belief about
what the client does and when it does it. The Lua fix is cheap. The belief is
what costs the day — and an unverified belief ships inside a code comment, where
it reads like a fact to whoever edits the handler next.

The skill exists because of a session that reached a working fix without ever
consulting an API reference. Twenty-eight shell calls and five edits, no lookup.
The diagnosis rested on two assertions about event timing made from memory, both
of which ended up in a code comment and a PR body. In-game testing is what
confirmed the fix; the reasoning behind it was never checked.

The same session left a signal unused. `SPELL_UPDATE_COOLDOWN` carries four
arguments, and the fourth — `startRecoveryCategory` — is how Blizzard's own
`CooldownViewer` tells a global cooldown apart from a spell cooldown. The
handler bound the payload at unit-event offsets and read only the first
argument, then reconstructed the distinction from cast bars and a
learn-by-watching table. One index lookup would have surfaced it.

It also produced a design that trades correctness for latency — the first cast
of each spell per session takes a slower path, and a procced instant stays
misclassified — and surfaced that trade as a closing footnote rather than as a
decision the user got to make. Hence the second half of the rule: cost is
reported unprompted, and a correctness trade is never resolved silently.

## Contents

| Path | What it is |
|---|---|
| `SKILL.md` | The seven-step loop, and what each step must produce |

No scripts. The work this skill governs is done by the other four:

| Step | Skill |
|---|---|
| Locate the setting, the reads, the handler | `ellesmereui-search` |
| Verify payloads, constants, Blizzard's own handler | `wow-api-search` |
| Combat-time reads that may return a secret | `wow-secret-values` |
| House style and PR gate, at the end only | `ellesmereui-pr-check` |

A report that the addon is **slow** rather than wrong goes to `eui-perf`
instead. That loop inverts the first step — measure the module's share of the
frame before reading any of its code — because the measurement decides whether
the audit is worth running.

## The part that matters

Step 3. Do not assert from memory when an event fires, what it carries, what
order two events arrive in, or what a field means. Everything else in the loop
is bookkeeping around that one rule.

Step 5 is the runner-up: write the in-game checklist **before** the fix. You
cannot run the game, so the user is the test harness — and drafting the
checklist first exposes the cases the design does not handle while the design is
still cheap to change.

## Limits

Event *ordering* is often not documented anywhere, and this skill cannot conjure
it. What it can do is stop you from asserting it: when the answer is not in the
API and not in Blizzard's source, say so and pick a failure direction that
survives being wrong about it.

The skill assumes the EllesmereUI checkout and the other four skills are
present. With none of them it degrades to a checklist, which is still better
than the default order of work.
