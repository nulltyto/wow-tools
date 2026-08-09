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

`wow-api-search` answers "what does this Blizzard API do" and "how does
Blizzard implement this". `ellesmereui-search` answers "where does EllesmereUI
define or read this". `ellesmereui-pr-check` answers "will this change survive
review". `wow-secret-values` answers "will this line raise in a raid".
`eui-addon-debug` answers "what do I do first" — it calls the other four in
order and does no lookup of its own. Each skill's own README documents its
format, scripts, and limitations.

## Addons

| Addon | What it does |
|---|---|
| [`EllesmereUISecretsDiag`](addons/EllesmereUISecretsDiag/) | In-game developer diagnostics for the EllesmereUI suite: CPU and memory per module, taint tracking, secret-value probes, and a sampling recorder. `/euidiag` |

It depends on `EllesmereUI` and does nothing without it. Installing it is
opt-in — the interactive installer defaults to no addons, since putting a
diagnostics addon into somebody's game is not a side effect of installing a
search skill.

Two offline tools go with it, and stay in this repo rather than in the game:

| Tool | What it does |
|---|---|
| [`tools/diag/harness.lua`](tools/diag/) | Loads all five addon files under a stubbed WoW API and dispatches every `/euidiag` command. Catches load-order faults, registration errors, and dispatch bugs that a syntax check cannot see. Runs in CI. |
| [`tools/perf/euidiag-perf.py`](tools/perf/) | Turns `/euidiag rec` recordings into per-module statistics, CSV, a shareable JSON summary, and an optional plot. |

```bash
cd tools/diag && lua5.1 harness.lua        # smoke test, exit 0 means clean
./tools/perf/euidiag-perf.py               # summarise the newest recording
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
```

Skills and addons are independent halves. Passing `--harness` runs only the
skills half, `--addons` only the addons half, and neither runs both.

| | |
|---|---|
| `list` | every skill, addon, harness, and WoW install found |
| `status` | what is installed where right now |
| `doctor` | whether an edit to an addon here reaches the game |
| `install` | `--dry-run`, `--copy`, `--force`, `--scope user\|project` |
| `uninstall` | removes only what this installer placed |

Both are **symlinked** by default, so `git pull` updates every harness — and
the running game — at once. Where symlinks are unavailable — Windows without
Developer Mode, an exFAT or NTFS mount, which is a real possibility for a game
install — the installer detects it and copies instead, which needs a re-run
after a pull. `--copy` forces that everywhere.

Nothing is ever silently replaced. A directory or symlink the installer did not
create is reported and left alone until you pass `--force`, which matters most
in an AddOns folder, where a hand-installed copy of the same addon is the
normal case rather than the strange one.

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
addons/            One directory per WoW addon, installed into the game
tools/             Offline tools that stay here rather than shipping in-game
wow_tools/         The installer: harness registry, game discovery, link/copy engine, CLI
tests/             Hookup, addon, and installer tests
docs/              Design notes and evaluations
install.sh         Bootstrap (POSIX)
install.ps1        Bootstrap (Windows)
```

## Attribution

World of Warcraft is a trademark of Blizzard Entertainment, Inc. The
`wow-api-search` index is generated from Blizzard's exported interface code,
sourced via [Gethe/wow-ui-source](https://github.com/Gethe/wow-ui-source);
that underlying game data belongs to Blizzard. EllesmereUI is by Ellesmere
Gaming; `ellesmereui-search` contains no addon code, only tooling that builds
an index from a local checkout. `EllesmereUISecretsDiag` is a development-only
diagnostics addon that measures that suite and follows its conventions; it is
not part of a release and is not meant to be shipped to players. The Apache-2.0
license here covers the skills and tooling in this repo, not Blizzard's data.
This project is unaffiliated with Blizzard.
