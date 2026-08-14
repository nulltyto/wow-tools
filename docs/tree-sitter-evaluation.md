# Should the indexers use tree-sitter?

**First measured August 2026 against the EllesmereUI tree. Re-measured 13
August 2026 against the current tree (151 Lua files, 448,183 lines, 23.9 MB)
with tree-sitter 0.26.0 and tree-sitter-lua 0.5.0. Conclusion: unchanged. No
for the existing extraction, yes for the part of the call graph the index still
cannot reach.**

The three skills here parse Lua with regexes over a *masked* copy of the source
— a pass that blanks comment bodies and string contents to spaces while keeping
byte offsets intact. tree-sitter is the obvious alternative, so this is what
happened when the two were compared directly rather than argued about.

Every number below is from the re-measurement. Where the answer moved, the
first measurement is shown next to it.

## What was measured

### 1. Recall: does a real parser find declarations the regexes miss?

| | Named function declarations |
|---|---|
| tree-sitter `(function_declaration name: (_))` | 8,889 |
| **Found by tree-sitter but absent from `symbols.jsonl`** | **0** |

Still zero, on a tree that has grown by 14 files and 48,000 lines since the
first run. The extractors have no recall gap against a real grammar on the
construct they both model. This is the number that decides the question: the
usual reason to adopt a parser is that regexes are quietly missing things, and
here they are not missing anything.

(The index holds 17,015 symbols in total because it also covers assignment
forms — `ns.Foo = function()`, `obj.Bar = function()` — which are not
`function_declaration` nodes at all.)

### 2. Masking: does `mask_lua` agree with a grammar about where strings and comments are?

| | |
|---|---|
| Files compared | 151 (all of them) |
| Comment/string bytes examined | 9,029,206 |
| Bytes `mask_lua` blanked that tree-sitter calls code | 1,827 |
| **…of those, in files tree-sitter parsed cleanly** | **0** |

The first run compared only the files whose byte and character counts line up.
That method silently skips every file containing a non-ASCII byte — 36 of the
151 files here. This run maps character offsets to byte offsets and compares
all of them, which is why the examined count roughly doubled. The verdict did
not move.

Every disagreement is inside one file, and that file is the one tree-sitter
fails to parse. On the 150 files tree-sitter handles, the hand-written masking
pass and the grammar agree exactly — including long brackets (`[[`, `[==[`),
nested quotes, and comments containing braces.

### 3. Robustness: tree-sitter is still the one that breaks

`EllesmereUI_Profiles.lua` produces **13 ERROR nodes** around line 4299, on a
long profile string full of escaped punctuation
(`"2 50 0 0 0 3 3 UIParent 440.0 190.5 -1 ##$%%/&('%)$+$,$ ..."`).

`luac5.1 -p` accepts the file. The file is valid Lua 5.1; the grammar is wrong
about it.

This is the check the first evaluation asked to repeat on any grammar upgrade,
and the upgrade has now happened: tree-sitter-lua 0.5.0 fails on the same
construct, in the same file, with the same node count. The bug is not aging
out.

That inverts the usual argument. Adopting tree-sitter here would mean replacing
something that parses this tree correctly with something that does not, and the
failure is silent — an ERROR node yields no captures, so the records simply
stop appearing.

### 4. Cost, and the parsing efficiency question

| | First run | Now |
|---|---|---|
| Tree | 137 files, 399,657 lines, 20 MB | 151 files, 448,183 lines, 23.9 MB |
| tree-sitter parse of every file | 1.43 s (14 MB/s) | 1.71 s (14.0 MB/s) |
| Full index build (parse + extract + cross-refs + write) | ~14 s | 19.7 s |

**The extraction has not become more efficient. It has become slower, because
it now does more.** Running the previous extractor (`ab762b2`) and the current
one against the same tree, three runs each, isolates the code change from the
tree growth:

| Extractor | Full `--force` build | Symbols produced |
|---|---|---|
| `ab762b2` (first evaluation) | 16.6 s | 17,015 |
| current | 19.7 s | 17,015 |

Same output on the symbol count, about 19% more wall clock. The extra time buys
the passes added since: caller edges, colour tables, and defaults tables built
one branch at a time. tree-sitter's own throughput is unchanged at 14 MB/s, so
the gap between the two has widened slightly in tree-sitter's favour — from
roughly 10x to roughly 12x — and it still does not matter.

Parsing is not the bottleneck in either version, and `--ensure` makes the build
a no-op when nothing changed. There is no performance case either way.

## The dependency, which is the real cost

Every script in this repo is standard-library-only, and that is load-bearing
rather than incidental. A skill installed into `~/.agents/skills` is run by
whatever harness picked it up, with whatever Python happens to be on that
machine — no virtualenv, no `uv sync`, quite possibly no ability to install
anything. `import tree_sitter` turns a working skill into a stack trace on any
machine that has not been prepared, and the preparation is per-machine, not
per-clone.

Reproducing this evaluation made the point concretely. tree-sitter 0.26 needs
Python 3.10 or newer; this repo targets 3.9, so the measurement had to run in a
throwaway 3.13 virtualenv. A dependency that cannot install against the repo's
own floor is not a dependency the skills can carry.

That is a real price. It would be worth paying for a real gain.

## Where tree-sitter would pay

This is the section the first evaluation got overtaken on. It said the index
"finds where a function is *defined*, never where it is called." That is no
longer true: `symbols.jsonl` now carries a `callers` field, so a partial call
graph exists without a grammar.

What that partial graph covers:

| | |
|---|---|
| Symbols carrying at least one caller | 7,914 of 17,015 |
| Caller edges recorded | 31,275 |
| Distinct call sites recorded | 29,875 |
| Symbols truncated by `CALLER_CAP = 40` | 125 |

Measured against tree-sitter's `function_call` nodes, restricted to the names
whose records actually publish a caller list — that is, excluding the 7,547
records suppressed as `caller_ambiguity` and the 125 cap-truncated ones, both
of which already route the agent to Grep:

| Call shape | Call sites | Recorded by the index | Missed |
|---|---|---|---|
| `Foo()` | 17,573 | 17,417 (99.1%) | 156 |
| `X.Foo()` | 8,592 | 6,353 (73.9%) | 2,239 |
| all | 26,165 | 23,770 (90.8%) | 2,395 |

Bare calls are essentially complete. The gap is almost entirely one failure,
and it is not scope analysis in general: **the receiver at the call site is
spelled differently from the owner the index recorded.** 639 of the dotted
misses are a receiver that is a plain alias for the indexed owner, and the rest
are the same idea reached through a longer path:

| Called as | Indexed as | Sites |
|---|---|---|
| `PPa.X()` | `PP.X()` | 95 |
| `EllesmereUI.Lite.X()` | `EUILite.X()` | 63 |
| `barCtx.X()` | `ctx.X()` | 49 |
| `EllesmereUI.X()` | `EUI.X()` | 45 |
| `ns.Engine.X()` | `Engine.X()` | 33 |

`PP.ToPixels` at `EllesmereUI.lua:2137` shows the shape plainly. Its
`caller_count` is 0, yet it is called at `EUI_UnlockMode.lua:9567` as
`PPc.ToPixels(liveCX)` — `PPc` being a local bound to the same table.
Attribution follows the receiver by design, because the alternative reports
6,836 callers for `SetPoint`; the cost of that correct choice is every call
through a renamed receiver.

So the honest statement is narrower than the first evaluation's, and more
useful: the caller index is close to complete on bare calls, reliable on dotted
calls whose receiver matches the definition, and blind to receiver aliasing. It
should not be read as a complete call graph. The remaining constructs a grammar
would reach:

| Construct | Count | Why regexes struggle |
|---|---|---|
| Function call sites | 144,460 | Only 74,263 have a callee name resolvable without scope analysis at all. |
| Calls through a renamed receiver | 2,239 missed | `PPc.ToPixels()` where the index recorded `PP.ToPixels`. Needs the binding followed. |
| Definitions suppressed as ambiguous | 7,547 | Mostly the AceConfig `get = function(info)` idiom, where the missing list costs nothing. |
| `hooksecurefunc` targets | 392 | The hooked name is an argument, often a variable; resolving it means following the binding. |
| Functions in table fields | 7,103 | Table-driven config and anonymous handlers, which no definition regex names. |

## Recommendation

**Do not migrate the existing extraction.** `build_index.py`, `check_style.py`,
and `generate_index.py` should keep the masking approach. It matches a real
grammar exactly where that grammar is correct, beats it where it is not, costs
no dependency, and its two validators already prove precision and recall on
every build. The re-measurement strengthens this rather than weakening it: the
grammar upgrade that might have fixed the one file it mis-parses did not.

`generate_index.py` is the clearest case of all: it parses Blizzard's
*generated* documentation export, whose formatting is machine-uniform. A
grammar there is pure cost.

**Two things worth doing, neither of them a rewrite:**

1. Say what the caller index is. It is 99.1% complete on bare calls and 73.9%
   complete on `X.Foo()` calls, and the shortfall is receiver aliasing rather
   than anything diffuse. `SKILL.md` already warns about `hooksecurefunc`,
   runtime-assembled names, and table-stored functions; it should add the
   alias case, which is the larger one, and name the Grep that resolves it.
2. Keep the call-graph completion on the shelf. If "what does this
   hooksecurefunc actually hook" or "which handler does this config table
   install" becomes worth answering properly, build it as a **separate,
   optional index**: keep the existing extractors as the always-works baseline,
   have the builder degrade to "not available" when `tree_sitter` is not
   importable rather than failing the skill, and pin `tree-sitter-lua`.

## Re-running this

The measurement scripts are not committed — they are ~150 lines against the
internals above and are quicker to rewrite than to maintain. Run them from a
throwaway virtualenv on Python 3.10+, since tree-sitter 0.26 will not install
against this repo's 3.9 floor:

```
uv venv --python 3.13 /tmp/tsvenv
uv pip install --python /tmp/tsvenv/bin/python tree-sitter==0.26.0 tree-sitter-lua
```

The four checks worth repeating after any grammar or extractor change:

1. Count `(function_declaration name: (_))` captures per file; diff against
   `symbols.jsonl` line numbers. Expect zero found only by tree-sitter.
2. Mark every byte inside a `(comment)` or `(string)` node; diff against
   `mask_lua` output. Map character offsets to byte offsets rather than
   skipping files where the two counts differ, or a quarter of the tree goes
   uncompared. Expect disagreements only in files where
   `tree.root_node.has_error`.
3. Feed any file with ERROR nodes to `luac5.1 -p`. If luac accepts it, the
   grammar is the thing that is wrong.
4. Diff tree-sitter `function_call` sites against the `callers` field, holding
   out names whose `caller_count` exceeds the stored list length. Report bare
   and dotted call shapes separately; they fail at very different rates.
