# Should the indexers use tree-sitter?

**Measured August 2026 against the EllesmereUI tree (137 Lua files, 399,657
lines, 20 MB). Conclusion: no for the existing extraction, yes for a specific
set of things the index does not model today.**

The three skills here parse Lua with regexes over a *masked* copy of the source
— a pass that blanks comment bodies and string contents to spaces while keeping
byte offsets intact. tree-sitter is the obvious alternative, so this is what
happened when the two were compared directly rather than argued about.

Reproduce with `tree-sitter` 0.26 and `tree-sitter-lua`.

## What was measured

### 1. Recall: does a real parser find declarations the regexes miss?

| | Named function declarations |
|---|---|
| tree-sitter `(function_declaration name: (_))` | 8,373 |
| `symbols.jsonl` | 8,374 |
| **Found by tree-sitter but absent from the index** | **0** |

Zero. The extractors have no recall gap against a real grammar on the construct
they both model. This is the number that decides the question: the usual reason
to adopt a parser is that regexes are quietly missing things, and here they are
not missing anything.

(The index holds 16,358 symbols in total because it also covers assignment
forms — `ns.Foo = function()`, `obj.Bar = function()` — which are not
`function_declaration` nodes at all.)

### 2. Masking: does `mask_lua` agree with a grammar about where strings and comments are?

Compared byte for byte over exact node spans, across every file whose bytes and
characters line up:

| | |
|---|---|
| Comment/string bytes examined | 3,588,212 |
| Bytes `mask_lua` blanked that tree-sitter calls code | 1,827 |
| **…of those, in files tree-sitter parsed cleanly** | **0** |

Every disagreement is inside one file, and that file is the one tree-sitter
fails to parse. On the 136 files tree-sitter handles, the hand-written masking
pass and the grammar agree exactly — including long brackets (`[[`, `[==[`),
nested quotes, and comments containing braces.

### 3. Robustness: tree-sitter is the one that breaks

`EllesmereUI_Profiles.lua` produces **13 ERROR nodes** around line 4327, on a
long profile string full of escaped punctuation
(`"2 50 0 0 0 3 3 UIParent 440.0 190.5 -1 ##$%%/&('%)$+$,$ ..."`).

`luac5.1 -p` accepts the file. The file is valid Lua 5.1; the grammar is wrong
about it.

That inverts the usual argument. Adopting tree-sitter here would mean replacing
something that parses this tree correctly with something that does not, and the
failure is silent — an ERROR node yields no captures, so the records simply
stop appearing.

### 4. Cost

| | |
|---|---|
| tree-sitter parse of all 137 files | 1.43 s (14 MB/s) |
| Current full index build (parse + extract + 75k cross-refs + write) | ~14 s |

Parsing is not the bottleneck, and `--ensure` makes the build a no-op when
nothing changed. There is no performance case either way.

## The dependency, which is the real cost

Every script in this repo is standard-library-only, and that is load-bearing
rather than incidental. A skill installed into `~/.agents/skills` is run by
whatever harness picked it up, with whatever Python happens to be on that
machine — no virtualenv, no `uv sync`, quite possibly no ability to install
anything. `import tree_sitter` turns a working skill into a stack trace on any
machine that has not been prepared, and the preparation is per-machine, not
per-clone.

That is a real price. It would be worth paying for a real gain. There is no
measured gain.

## Where tree-sitter would pay

The index documents what it does not model, and those limitations are where a
grammar genuinely helps. Counted in this tree:

| Construct | Count | Why regexes struggle |
|---|---|---|
| Function **call sites** | 140,640 | The index finds where a function is *defined*, never where it is called. A call graph needs scope resolution. |
| `hooksecurefunc` targets | 390 | The hooked name is an argument, often a variable; resolving it means following the binding. |
| Functions in table fields | 7,032 | Table-driven config and anonymous handlers, which no definition regex names. |

None of these are things the current extractors do badly. They are things the
current extractors do not attempt, and the SKILL.md files say so and route the
agent to Grep instead.

## Recommendation

**Do not migrate the existing extraction.** `build_index.py`, `check_style.py`,
and `generate_index.py` should keep the masking approach. It matches a real
grammar exactly where that grammar is correct, beats it where it is not, costs
no dependency, and its two validators already prove precision and recall on
every build.

`generate_index.py` is the clearest case of all: it parses Blizzard's
*generated* documentation export, whose formatting is machine-uniform. A
grammar there is pure cost.

**Reconsider only for a call graph.** If "where is this called from", "what
does this hooksecurefunc actually hook", or "which handler does this config
table install" becomes something worth answering, that is a new capability that
regexes cannot reach, and tree-sitter is the right tool for it. Build it as a
**separate, optional index** rather than a rewrite:

- keep the existing extractors as the always-works baseline,
- have the call-graph builder degrade to "not available" when `tree_sitter` is
  not importable, rather than failing the skill,
- pin `tree-sitter-lua` and re-run the ERROR-node check above on upgrade, since
  a grammar that mis-parses valid Lua 5.1 today may or may not tomorrow.

## Re-running this

The measurement scripts are not committed — they are ~150 lines against the
internals above and are quicker to rewrite than to maintain. The three checks
worth repeating after any grammar or extractor change:

1. Count `(function_declaration name: (_))` captures per file; diff against
   `symbols.jsonl` line numbers. Expect zero found only by tree-sitter.
2. Mark every byte inside a `(comment)` or `(string)` node; diff against
   `mask_lua` output. Expect disagreements only in files where
   `tree.root_node.has_error`.
3. Feed any file with ERROR nodes to `luac5.1 -p`. If luac accepts it, the
   grammar is the thing that is wrong.
