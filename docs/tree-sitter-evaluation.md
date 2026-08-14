# Should the indexers use tree-sitter?

**First measured August 2026 against the EllesmereUI tree. Re-measured 13
August 2026 against the current tree (151 Lua files, 448,183 lines, 23.9 MB)
with tree-sitter 0.26.0 and tree-sitter-lua 0.5.0. Conclusion: unchanged. No
for the existing extraction, yes for the part of the call graph the index still
cannot reach.**

The re-measurement was not neutral. It found 12 of the build's 20 seconds in
one avoidable loop, and a receiver-aliasing gap in the caller index worth 616
call edges. Both are fixed; the numbers below are after those fixes.

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

### 4. Cost

| | First run | Now |
|---|---|---|
| Tree | 137 files, 399,657 lines, 20 MB | 151 files, 448,183 lines, 23.9 MB |
| tree-sitter parse of every file | 1.43 s (14 MB/s) | 1.71 s (14.0 MB/s) |
| Full index build (parse + extract + cross-refs + write) | ~14 s | 7.6 s |

The re-measurement first found the extraction had got *slower*, not faster.
Against the same tree the previous extractor (`ab762b2`) took 16.6 s and the
then-current one 19.7 s, for identical symbol output -- the extra work being
caller edges, colour tables, and branch-built defaults tables.

Profiling that 19.7 s found one function holding 12.4 s of it.
`extract_saved_variable_keys` compiled two regexes per SavedVariables name and
ran them over every file: 40 names against 24 MB is 80 reads of the whole tree.
One alternation over all 40 names reads it twice, and the same profile paid for
a `mask_lua` that visited every character one at a time when only four
characters can open a masked region.

| Extractor | Full `--force` build |
|---|---|
| `ab762b2` (first evaluation) | 16.6 s |
| before optimisation | 19.7 s |
| **now** | **7.6 s** |

Byte-identical output on all six index files, verified against a snapshot. The
gap to tree-sitter narrowed from roughly 12x to roughly 4.5x, which changes
nothing about the recommendation -- parsing was never the bottleneck, and
`--ensure` makes the build a no-op when nothing changed. It is recorded because
the first evaluation used "parsing is cheap either way" as a reason not to look,
and 12 of those 20 seconds were one avoidable loop.

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

Call sites break down by shape as 50,106 bare `Foo()`, 24,157 dotted `X.Foo()`,
and 69,623 method `X:Foo()` — 143,886 of the 144,460 `function_call` nodes
resolve to a callee name.

> A first pass at this measurement read `child_by_field_name("field")` for both
> index expressions. `method_index_expression` names its field `method`, so
> every one of those 69,623 calls returned `None` and dropped out silently —
> the harness reported a confident bare/dotted split with the largest category
> missing. Worth stating because the failure looked exactly like a clean
> result.

Restricted to the names whose records actually publish a caller list — that is,
excluding the 7,547 records suppressed as `caller_ambiguity` and the
cap-truncated ones, both of which already route the agent to Grep:

| Call shape | Call sites | Recorded | Before the alias work |
|---|---|---|---|
| `Foo()` | 17,572 | 17,416 (99.1%) | 99.1% |
| `X.Foo()` | 8,388 | 6,596 (**78.6%**) | 73.9% |
| `X:Foo()` | 14,595 | 1,057 (7.2%) | 7.1% |

**The method row is not a defect, and closing it would be a bug.** Method names
collide with Blizzard's widget API: `:SetPoint` accounts for 7,126 of those
"missed" calls across 2,003 distinct receivers, against a single indexed
`fs:SetPoint` helper. Crediting them by name is precisely the false edge the
receiver rule exists to prevent. Name matching is a fair proxy for "should have
been recorded" on bare and dotted calls; on methods it measures instance
dispatch, which no amount of regex or grammar resolves without type inference.
The method calls that *are* real misses look different — `:DualRow` is called
669 times from two receivers, not two thousand.

The dotted gap was one failure: **the receiver at the call site is spelled
differently from the owner the index recorded.** That is now largely resolved.
`build_index.py` follows three syntactic bindings — a table published on a path
(`EllesmereUI.PP = PP`), a local bound from one
(`local PPc = EllesmereUI and EllesmereUI.PP`, including `_G.` prefixes and an
`or {}` fallback), and the same binding read backwards, for a file that
declares `function EUI.Foo()` after `local EUI = _G.EllesmereUI or {}`.

`PP.ToPixels` was the worked example: `caller_count` 0 with eight real callers
reached as `PPi.`, `PPc.`, and `gamePP.`. It now reports all eight and names
the three aliases. Across the tree the change added 616 caller edges and 1,884
to the true counts, and **every one of the 616 was checked against tree-sitter
for a real call of that name at that line — none were false**.

What is deliberately left unresolved:

- An `or` between two *named* tables. `EllesmereUI.Widgets` is assigned
  `AbsorberW`, `realWidgets`, and `WidgetFactory`, because a search feature
  swaps the table at runtime. Three claims, so the alias is dropped.
- A receiver that arrives as a function parameter. `barCtx.X()` for `ctx.X()`
  is 49 sites, and nothing syntactic connects them. This is real dataflow and
  the honest end of what regexes reach — 192 dotted misses remain, down from
  639, and they are nearly all this.

The remaining constructs a grammar would reach:

| Construct | Count | Why regexes struggle |
|---|---|---|
| Method dispatch | 69,623 call sites | The receiver is an instance, not the table the method was defined on. Needs type inference, not a grammar. |
| Receiver arriving as a parameter | 192 missed | `barCtx.X()` for `ctx.X()`. Real dataflow; nothing syntactic links them. |
| Definitions suppressed as ambiguous | 7,547 | Mostly the AceConfig `get = function(info)` idiom, where the missing list costs nothing. |
| `hooksecurefunc` targets | 392 | The hooked name is an argument, often a variable; resolving it means following the binding. |
| Functions in table fields | 7,103 | Named by `FUNC_ASSIGN` already -- see the correction below. What a grammar would add is the table each one belongs to. |

## Correction, 14 August 2026

The "functions in table fields" row above was wrong, and it was the strongest
remaining argument on this page for adopting a grammar. `FUNC_ASSIGN` matches
`^name = function(` wherever it appears, so 6,951 of those definitions were in
`symbols.jsonl` the whole time. What was missing was not the record but the
*kind*: they were labelled `global`, along with every forward-declared local.

| `kind` before | Rows | What they actually were |
|---|---|---|
| `global` | 7,265 | 6,951 table fields, 292 forward-declared locals, **22 globals** |

The builder now separates the three by where the definition sits -- inside an
open `{` it is a `tablefield`, outside one it is that file's `local` when the
file declares the name -- and `global` means global. This is a classification
fix, not a recall fix, and it needed no grammar: the brace depth and the
declarations are both already in the masked text.

Two orderings in that rule are load-bearing, and the first pass got both wrong.
The declaration counts at **any** indent, because the options files
forward-declare inside a builder block and fill the body thousands of lines
below; restricting it to column 0 left 50 of 120 `global` rows that were really
locals, two of which collected cross-file call sites belonging to an unrelated
function of the same name. But **position is checked before the name**: a key
inside a constructor is a field of that table even when the file declares a
local of the same spelling, and letting the declaration win instead moved 334
table fields into `local` and handed them the local's callers.

What is left is 4 rows of the 22: a bare assignment to a function parameter,
which needs parameter scope this builder does not track. All four are ambiguous
by name and carry no caller list, so the cost is the label rather than an edge.

The other residual costs a list. A forward declaration and its body are one
variable with two definition sites (`local function CloseSnapMenu() end` up
top, `CloseSnapMenu = function()` further down), and once both are `local` they
key the same way and the pair reads as two definitions competing for one name.
483 records are suppressed as `caller_ambiguity: 2` by this; two of them,
`CloseSnapMenu` and `RefreshAllImportVisuals`, held a correct list before the
reclassification. That is the honest price of the fix -- it removed 3 false
cross-file edges and cost 7 true same-file ones, which is the trade this index
makes everywhere.

It cannot be fixed by collapsing same-name locals per file. That was tried:
`EUI_Quickdraw_Options.lua` declares two genuinely unrelated nested locals
named `Add`, and merging them handed each the other's call sites -- 431
validator failures. Telling a declaration-plus-body pair from two nested
functions needs block scope, which is the same boundary the parameter case sits
on. **This is the strongest remaining candidate on this page for a grammar**:
unlike method dispatch it is not a type-inference problem, it is a scope
problem, and a grammar hands you scope for free.

Three smaller things went with it, all measured against the same tree:

- **Shadowing.** A file-scope `local X` bound from anything other than a
  function definition was invisible to the caller pass, so 10 cross-file edges
  were credited to a same-named local in another addon. Reading every file's
  chunk-scope declarations removes them.
- **`.tools/`.** Three offline helper scripts in no TOC contributed 40 symbols
  and pushed 14 real EllesmereUIQuickdraw functions to `caller_ambiguity`.
  Excluded now by a dotted-directory rule rather than a list of known names.
- **Comment prose as a reference.** Masking blanks a comment whole, so
  `-- changedAxis: "width", "height"` read as a reference to the `width`
  setting -- 264 of them across 14 records. A real string keeps its opening
  quote and a comment does not, which separates the two exactly.

None of this moves the recommendation. It moves the *reason*: the gap this page
attributed to the regexes was mostly a gap in what the records said about
themselves.

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

1. Say what the caller index is. It is 99.1% complete on bare calls and 78.6%
   complete on `X.Foo()` calls, and it does not attempt method dispatch.
   `SKILL.md` carries that, plus the Grep that resolves a receiver the alias
   passes could not.
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
   out names whose `caller_count` exceeds the stored list length. Report bare,
   dotted, and method shapes separately; they fail at completely different
   rates, and a combined number hides all three.

   Read the callee through the right field or the check lies: a
   `dot_index_expression` names it `field`, a `method_index_expression` names
   it `method`. Using `field` for both silently drops every `X:Foo()` call --
   half the call sites in this tree -- and the run still looks clean.

5. After any change to caller attribution, diff the new `callers` lists against
   the previous ones and confirm each *added* edge has a real call of that name
   at that line. Recall is easy to raise by inventing edges; this is the check
   that says you did not. Expect some listed callers to disappear without a
   regression, since `CALLER_CAP` shows only the first 40 and `caller_count`
   carries the truth.

6. For every bare-name edge, check that the calling file does not declare its
   own chunk-scope `local` of that name above the call line. If it does, the
   call reaches that local and the edge is false. `validate_index.py` cannot
   find these: precision there asks whether the cited line mentions the name,
   and a shadowed call mentions it perfectly. Exclude same-file callers before
   counting, or the forward-declaration idiom
   (`local function Build() end` up top, `Build = function()` below) reads as
   hundreds of false hits when every one of them is correct.

   Note also that ambiguity hides coverage from check 1 of the recall set: a
   row suppressed as `caller_ambiguity` carries no `callers`, so the exact
   per-file count simply skips it. A change that quietly pushes rows into
   ambiguity looks like a clean run with a smaller denominator. Watch the
   denominator.
