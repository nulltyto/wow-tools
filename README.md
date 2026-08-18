# wow-tools

Skills, addons, and scripts for World of Warcraft addon development.

The skills are [Agent Skills](https://agentskills.io) — the open standard
Anthropic released in December 2025 — so the same skill works in Claude Code,
Codex, Cursor, Gemini CLI, opencode, Copilot, and about twenty other harnesses.
The addon is a real WoW addon, and the installer places it in the game.

## Skills

| Skill | What it does |
|---|---|
| [`wow-api-search`](skills/wow-api-search/) | Searches Blizzard's API: functions, events, enums, structures, and the exported interface code. Bundled index, committed here. |
| [`ellesmereui-search`](skills/ellesmereui-search/) | Searches the EllesmereUI addon suite's own source: symbols, settings keys, locale strings, events, slash commands. Index built locally from your checkout. |
| [`ellesmereui-pr-check`](skills/ellesmereui-pr-check/) | Checks EllesmereUI changes against the code style rules in the addon's `CONTRIBUTING.md`, and flags code that names another addon so its provenance gets checked. Diff-scoped; installs as a pre-commit hook. |
| [`wow-secret-values`](skills/wow-secret-values/) | Writing addon code that survives restricted combat: which API fields stay readable, which operations raise on a secret, and how to confirm either live. |
| [`eui-addon-debug`](skills/eui-addon-debug/) | The order to work in on a reported EllesmereUI bug: correctness first, cost a close second, Blizzard's contract verified before any theory. Sequences the other four. |
| [`eui-addon-feature`](skills/eui-addon-feature/) | The order to work in on an EllesmereUI feature request: answer it with sources first, extend the sibling the request names at every dispatch site it has, and never bake a capability of the building character into shared state. |
| [`eui-perf`](skills/eui-perf/) | The order to work in on an EllesmereUI performance request: measure the module's share of the frame before auditing it, from the client's own profiler rather than from frame rate. |

`wow-api-search` answers "what does this Blizzard API do" and "how does
Blizzard implement this". `ellesmereui-search` answers "where does EllesmereUI
define or read this". `ellesmereui-pr-check` answers "will this change survive
review". `wow-secret-values` answers "will this line raise in a raid".
`eui-addon-debug`, `eui-addon-feature`, and `eui-perf` answer "what do I do
first" for a bug, a feature request, and a slowdown respectively — mostly by
calling the other four in order rather than by looking anything up themselves.
The exception is `eui-addon-debug`, which ships an event tracer for the question
none of the others can answer: not what the client documents, but what it
actually sent, in what order, during one run. Between them they
cover the three ways work arrives, which matters because a request that matches
no orchestrator gets answered by hand: a feature session in this addon ran 31
raw greps past an index that was current and sitting beside it. Each skill's
own README documents its format, scripts, and limitations.

`eui-perf` is also the half of this repo that ties the skills to the addon: the
measurements it works from come from `/euidiag` and `tools/perf/`, below.

## Addons

| Addon | What it does |
|---|---|
| [`EllesmereUISecretsDiag`](addons/EllesmereUISecretsDiag/) | In-game developer diagnostics for the EllesmereUI suite: CPU and memory per module, taint tracking, secret-value probes, an aura filter bench, and a sampling recorder. `/euidiag` |

It depends on `EllesmereUI` and does nothing without it. Installing it is
opt-in — the interactive installer defaults to no addons, since putting a
diagnostics addon into somebody's game is not a side effect of installing a
search skill.

Four offline tools go with it, and stay in this repo rather than in the game:

| Tool | What it does |
|---|---|
| [`tools/diag/harness.lua`](tools/diag/) | Loads all five addon files under a stubbed WoW API and dispatches every `/euidiag` command. Catches load-order faults, registration errors, and dispatch bugs that a syntax check cannot see. Runs in CI. |
| [`tools/perf/euidiag-perf.py`](tools/perf/) | Turns `/euidiag rec` recordings into per-module statistics, CSV, a shareable JSON summary, and an optional plot. |
| [`tools/lint/lua_comments.py`](tools/lint/) | Caps how long a comment block may run in this repo's own Lua, using the rule the `ellesmereui-pr-check` skill applies to the addon. Diff-scoped, so only the comment lines a change adds count. Runs in CI. |
| [`tools/lint/ascii_text.py`](tools/lint/) | Keeps commit messages and pull request text to ASCII. Runs as a `commit-msg` hook, as a Claude Code `PreToolUse` hook over `gh` commands, and in CI. See [Keeping git text to ASCII](#keeping-git-text-to-ascii). |

```bash
cd tools/diag && lua5.1 harness.lua        # smoke test, exit 0 means clean
./tools/perf/euidiag-perf.py               # summarise the newest recording
./tools/lint/lua_comments.py               # comment budget over the diff
./tools/lint/ascii_text.py --range main..HEAD   # ASCII over this branch's messages
```

`euidiag-perf.py` finds the SavedVariables file by locating the game the same
way the installer does. `$WOW_SAVEDVARIABLES` pins one file; `$WOW_INSTALL`
points at a game the search does not reach. Each tool's own README covers the
rest, including what the numbers mean and what sampling rate can and cannot buy.

## Install

```bash
git clone git@github.com:nulltyto/wow-tools.git ~/Repos/wow-tools
cd ~/Repos/wow-tools
./install.sh              # macOS, Linux, WSL, Git Bash
.\install.ps1             # Windows PowerShell
```

The installer asks which harness you use and which skills you want, then puts
them where that harness reads them, and finally offers the addons. It needs
Python 3.9+ and nothing else; if none is on `PATH` it will use `uv` to provide
one.

Non-interactive:

```bash
./install.sh --harness claude-code --skills all --yes
./install.sh --harness codex,cursor,gemini-cli --skills wow-api-search --yes
./install.sh --harness claude-code --scope project --project-root ~/Repos/my-addon --yes
./install.sh --addons all --yes                        # addons only
./install.sh --addons EllesmereUISecretsDiag --wow-addons "/path/to/Interface/AddOns" --yes
./install.sh --harness claude-code --rules all --yes   # rules only
./install.sh --hooks all --repo /path/to/EllesmereUI   # hooks, into another repo
```

Skills, rules, addons, and hooks are independent quarters, and each flag runs
only its own. `--harness` runs the skills half; `--rules` the rules half;
`--addons` the addons half; `--hooks` the hooks half. `--harness` on its own
never installs rules, because asking for skills is not asking for an
instruction in every future session. Hooks are never part of a default run at
all: they write into a repository you have to name.

| | |
|---|---|
| `list` | every skill, addon, harness, and WoW install found |
| `status` | what is installed where right now |
| `doctor` | whether an edit to an addon here reaches the game |
| `install` | `--dry-run`, `--copy`, `--force`, `--json`, `--scope user\|project` |
| `uninstall` | removes only what this installer placed |

`--json` reports the run on stdout as one document instead of as prose: what
was chosen, which directory each thing went to and by what method, what
happened to it, and which harnesses were passed over and why. Intended for a
caller that is a program — an agent driving the installer, most obviously —
rather than for reading. It implies `--yes`, because a confirmation prompt
would corrupt the output. The exit code is unchanged and agrees with the
document's `ok` field, so either may be read.

```bash
python -m wow_tools install --harness claude-code --skills all --json | jq '.sections[].results'
```

Both are **symlinked** by default, so `git pull` updates every harness — and
the running game — at once. Where symlinks are unavailable — Windows without
Developer Mode, an exFAT or NTFS mount, which is a real possibility for a game
install — the installer detects it and copies instead, which needs a re-run
after a pull. `--copy` forces that everywhere.

Nothing is ever silently replaced. A directory or symlink the installer did not
create is reported and left alone until you pass `--force`, which matters most
in an AddOns folder, where a hand-installed copy of the same addon is the
normal case rather than the strange one.

### Git hooks

Hooks are the one thing installed into **another repository** — the addon
checkout you are about to commit to, not this one and not your home directory.
That is why `--repo` is required and why nothing is installed by default:

```bash
python -m wow_tools install --hooks all --repo /path/to/EllesmereUI
python -m wow_tools status  --repo /path/to/EllesmereUI
python -m wow_tools uninstall --hooks all --repo /path/to/EllesmereUI
```

| Hook | Event | What it does |
|---|---|---|
| `ascii-git-text` | `commit-msg` | Rejects a commit message containing a non-ASCII character. |
| `eui-style` | `pre-commit` | Runs the `ellesmereui-pr-check` style check over the staged lines. Errors block the commit; warnings and notes print and let it through. |

A hook that cannot do its job in the target repo is skipped rather than
installed, because a hook that fails does not fail quietly — it fails the
commit. `eui-style` needs an `EllesmereUI.toc`, so pointing `--hooks all` at an
unrelated checkout installs the ASCII hook and skips the other one, instead of
blocking every commit there with an error about a `.toc` nobody was looking for.

`git commit --no-verify` skips both. An existing hook that is not ours is never
overwritten: `.git/hooks` is not tracked and not pushed, so whatever an
overwrite destroyed would be unrecoverable. The installer prints the line to
add by hand instead.

`check_style.py --install-hook` still works and does the same thing for the
style check alone. `--repo` is the route that also gives you `status` and
`uninstall`.

### Keeping git text to ASCII

Source files get linted, reviewed, and fixed. Commit messages and pull request
text do not — they go straight into a place where they are quoted, re-encoded,
and read back for years by tooling that does not agree on an encoding. By the
time a curly quote surfaces as mojibake in someone's `git log`, the commit is
immutable, and rewriting published history to correct punctuation is not worth
doing. So the character has to not be written in the first place.

Three layers enforce it, because each one catches what the others cannot:

| Layer | Covers | Where |
|---|---|---|
| `commit-msg` hook | Commit messages, before they exist | Per clone, via `--hooks` above |
| `PreToolUse` hook | `gh pr create`, `gh pr comment`, `gh issue create`, `gh release create` | [`.claude/settings.json`](.claude/settings.json) |
| CI | Every commit message in the range, plus the PR title and body | [`ci.yml`](.github/workflows/ci.yml) |

The `PreToolUse` layer is the only one that reaches pull request text, since a
PR body never touches git. It reads the command an agent is about to run,
pulls out just the parts that are prose — the values of `-m`, `--title`,
`--body`, `--notes`, and any heredoc — and blocks the call if any of them is
non-ASCII. A non-ASCII character in a path, a branch name, or an unrelated
command is ignored, which is what keeps the gate switched on.

The rule itself is written down in [`rules/ascii-git-text.md`](rules/), and
[installed as a rule](#rules) so an agent knows about it before it is stopped.
Rules explain; hooks enforce. Claude Code's own documentation is explicit that
instruction files "shape Claude's behavior but are not a hard enforcement
layer", and that anything which must run before every commit belongs in a hook.

### Rules

A rule is one always-on instruction file, loaded into every session a harness
runs. Unlike skills, rules have no cross-agent standard to install into:
[AGENTS.md](https://agents.md/) is defined only at a repository root, so it says
nothing about a user-scope location, and every harness that offers one invented
its own path, its own file extension, and its own frontmatter key.

```bash
python -m wow_tools install --harness claude-code --rules all --yes
python -m wow_tools install --harness claude-code --rules all --scope project \
    --project-root ~/Repos/my-addon --yes
```

The same file installs under a different name for each reader:

| Harness | User scope | Project scope | Installed as |
|---|---|---|---|
| Claude Code | `~/.claude/rules/` | `.claude/rules/` | `*.md` |
| GitHub Copilot | `~/.copilot/instructions/` | `.github/instructions/` | `*.instructions.md` |
| Kiro | `~/.kiro/steering/` | `.kiro/steering/` | `*.md` |
| Cursor | — | `.cursor/rules/` | `*.mdc` |

Cursor has no on-disk global rules directory — its user rules live in the
Customize UI — and a plain `.md` in `.cursor/rules` is ignored, which is why
the extension differs. One file serves all four because each reader ignores the
frontmatter keys it does not know, so `inclusion`, `alwaysApply`, and `applyTo`
sit side by side.

**Harnesses whose instructions are a single file are deliberately skipped.**
`~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`, and `~/.claude/CLAUDE.md` are files
you wrote. Appending to one is not a side effect an installer should have, so
the installer prints the paste and stops. Passing `--harness` alone never
installs rules either — asking for skills is not asking for an instruction in
every future session.

### Harness configuration in this repo

This repo ships one piece of harness configuration, and only one:
[`.claude/settings.json`](.claude/settings.json), holding the `PreToolUse` hook
above. It applies to agents working *in this repo*; the installer does not put
it anywhere, and there is still no `CLAUDE.md` here.

Everything else this repo checks about itself runs in CI rather than in a hook:
`ruff`, the Lua comment budget, the ASCII gate, the addon harness, and the
installer end to end.

### Finding the game

Skills go to a path that is known per harness. An addon goes wherever you
installed the game, which on Linux is usually several levels inside a Proton or
Wine prefix. So [`wow_tools/wow.py`](wow_tools/wow.py) answers it in order:

1. `--wow-addons`, then `$WOW_ADDONS_DIR`, then `$WOW_INSTALL`
2. the platform's usual location — `/Applications`, `C:\Program Files (x86)`, …
3. a **bounded** search of likely bases (`~`, `~/Games`, `~/.local/share`,
   `/mnt`, `/media`, Steam's `compatdata`) to six levels deep

Step 3 is a guess and is treated as one: the install it found is printed before
anything is written, and if it finds more than one, an unattended run stops and
asks you to name one rather than picking. Retail is ordered first. A `.toc`
dependency that is not in the same AddOns folder is reported after installing,
because the client's response to a missing dependency is to load nothing and
say nothing.

### Shadow copies

`status` says where things are installed. `doctor` answers the different
question of whether an edit will reach the game:

```bash
python3 -m wow_tools doctor
```

It resolves each installed addon to the path the client actually loads, then
looks for other folders under `AddOns` carrying the same `<name>/<name>.toc`.
Such a folder reads exactly like the source and answers a `grep -rn` from the
AddOns directory, but nothing loads it — so an edit changes nothing in game and
reports no error. This happens whenever an addon checkout lives inside `AddOns`
and keeps a copy of something that is separately symlinked at the top level,
which is the normal state of affairs after moving an addon between repos. It
cost a full debugging session before the check existed.

### Which harnesses

Run `./install.sh list --verbose` for the full table with per-harness notes.
Most of them read the standard's cross-agent path, so selecting eight agents
usually means one directory, and the installer says so rather than reporting
eight identical installs.

Covered: Claude Code, OpenAI Codex, Cursor, Gemini CLI, VS Code (Copilot),
GitHub Copilot CLI, opencode, Kiro, Qwen Code, Roo Code, Kilo Code, Kimi CLI,
Mistral Vibe, Google Antigravity, OpenHands, pi (and pi variants), MiniMax CLI,
Devin, plus a generic `agents-standard` entry that writes the cross-agent path
alone for any spec-compliant harness not listed.

Two entries install nothing, for reasons the installer prints:

- **Devin** indexes `.agents/skills` from connected repositories, not from a
  machine — so it is project scope only, and the directory has to be committed.
- **Aider** does not auto-discover skills at all. Load one per session with
  `/read-only <path>/SKILL.md`.

Every path in [`wow_tools/registry.py`](wow_tools/registry.py) carries the
documentation URL it came from. Harness conventions move; that is what makes a
wrong path fixable.

### claude.ai

For upload to claude.ai, build a `.skill` bundle with the packager from
Anthropic's [skills repo](https://github.com/anthropics/skills):

```bash
cd /path/to/skills/skill-creator
python -m scripts.package_skill /path/to/wow-tools/skills/<skill-name>
```

## Development

[uv](https://docs.astral.sh/uv/) drives the dev environment; the skills
themselves stay standard-library-only and Python 3.9+, so they run from a bare
clone on a machine that has neither uv nor a virtualenv.

```bash
uv sync
uv run pytest          # skill hookup + installer behaviour
uv run ruff check .
```

The test suite checks the things that break silently: that every `SKILL.md`
satisfies the Agent Skills spec rather than one harness's parser, that every
script a `SKILL.md` tells an agent to run still exists and answers `--help`,
that no harness path is absolute or Windows-hostile, and that the installer
never overwrites a directory it did not create.

The addon side is held to the same standard, against the failures the WoW
client reports with silence: a `.toc` not named after its folder (loads
nothing), a file listed in a `.toc` but missing from disk (skipped without a
word), and a load order that puts `Core.lua` anywhere but first. `harness.lua`
runs as part of the suite wherever a Lua interpreter is available, so a
registration or dispatch break fails CI instead of failing in game.

Each skill also ships its own validator, which is the real correctness check
and needs the source it indexes:

```bash
python3 skills/wow-api-search/scripts/validate_index.py
python3 skills/ellesmereui-search/scripts/validate_index.py
```

### Why the Lua parsing is regexes and not tree-sitter

Measured, not assumed: against this tree, tree-sitter finds **zero** function
declarations the current extractors miss, agrees with their comment/string
masking byte for byte on every file it parses cleanly — and mis-parses one file
of valid Lua 5.1 that `luac` accepts. It would also cost the standard-library-only
property the whole install story rests on.
[`docs/tree-sitter-evaluation.md`](docs/tree-sitter-evaluation.md) has the
numbers, and the one case where tree-sitter *would* pay: a call graph, which
the index deliberately does not attempt.

## Layout

```
skills/            One directory per Agent Skill, each self-contained
rules/             One file per always-on rule, installed into a harness rules directory
addons/            One directory per WoW addon, installed into the game
tools/             Offline tools that stay here rather than shipping in-game
wow_tools/         The installer: harness registry, game discovery, link/copy engine, CLI
tests/             Hookup, addon, rule, hook, and installer tests
docs/              Design notes and evaluations
.claude/           This repo's own harness config: the PreToolUse ASCII hook
install.sh         Bootstrap (POSIX)
install.ps1        Bootstrap (Windows)
```

## Attribution

World of Warcraft is a trademark of Blizzard Entertainment, Inc. The
`wow-api-search` index is generated from Blizzard's exported interface code,
sourced via [Gethe/wow-ui-source](https://github.com/Gethe/wow-ui-source);
that underlying game data belongs to Blizzard. The committed index carries the
facts about the interface -- names, signatures, payloads, enum members, secret
markers -- and not the prose notes Blizzard writes on them, which stay in the
export; `generate_index.py --with-docs` builds a local index that includes
them. EllesmereUI is by Ellesmere
Gaming; `ellesmereui-search` contains no addon code, only tooling that builds
an index from a local checkout. `EllesmereUISecretsDiag` is a development-only
diagnostics addon that measures that suite and follows its conventions; it is
not part of a release and is not meant to be shipped to players. The Apache-2.0
license here covers the skills and tooling in this repo, not Blizzard's data.
This project is unaffiliated with Blizzard.
