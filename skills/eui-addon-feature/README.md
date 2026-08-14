# eui-addon-feature

The order to work in when a feature request arrives for EllesmereUI: answer the
question with sources before designing, extend the sibling the request names
rather than inventing a shape, and decide what resolves per character before
anything is written to saved state.

## What this is for

`eui-addon-debug` covers a behaviour that is wrong. `eui-perf` covers one that
is slow. Neither fires on "can we add", and that gap is what this skill closes.

The gap was measurable. In a feature session on this codebase the agent ran 31
raw greps and seds across the addon tree and consulted no index at all, while a
bug session in the same checkout on the same day loaded `eui-addon-debug`,
which pulled in `ellesmereui-search` on its second step. Same tree, same
tooling, same day -- the only difference was the entry point. A feature request
matched no skill, so nothing routed the work.

## The two findings it encodes

**A variant is wiring, not design.** This codebase is built out of families --
slot kinds, bar types, options row types -- and a request usually names the
sibling to copy ("add DynamicSpec icons, like dynamicrez"). The new member has
to appear at every dispatch site the sibling appears at, across the module, the
picker, the preset, and the usability filter. A missed site does not fail to
compile; it draws correctly and does nothing when pressed. Verifying that by
hand took a review subagent 50 shell calls and nine minutes.

**A capability of the writing character must never be baked into shared
state.** The feature that prompted this skill was *about* portability: spec
entries stored a specID, which meant nothing on an alt of another class, so the
fix stored the position instead. It still shipped a preset builder that
enumerated the building character's specialization count -- three on a paladin
-- so the druid who loaded that preset could never reach a fourth position, and
could not add it by hand either, because the picker had enumerated the same
three. The user found it in game.

The skill turns that into an explicit step: for every stored value, say whether
it is an identity, a position, or a capability of the writer, and walk the
same-class, different-class, fewer-of-the-thing, and delete-then-restore cases
before writing the code.

## What it sequences

| Step | Skill |
|---|---|
| Find the precedent, its dispatch sites, and every caller | `ellesmereui-search` |
| Blizzard's signature, payload, taint marking, and rendering rules | `wow-api-search` |
| Anything read during combat | `wow-secret-values` |
| House style, at the end | `ellesmereui-pr-check` |

It also carries the file-level budgets that are cheap to check and expensive to
discover: Lua 5.1's 200 main-chunk locals (six files in the tree are within five
of the ceiling), the regenerated locale key list that CI gates on, and the
cross-addon guard convention for a partially updated install.

## Install

See the [repo README](../../README.md).
