# wow-tools

Skills, tools, and scripts for World of Warcraft addon development.

Built as [Agent Skills](https://agentskills.io) — the open standard Anthropic
released in December 2025 — so the same skill works in Claude Code, Codex,
Cursor, Gemini CLI, opencode, Copilot, and about twenty other harnesses.

## Skills

| Skill | What it does |
|---|---|
| [`wow-api-search`](skills/wow-api-search/) | Searches Blizzard's API: functions, events, enums, structures, and the exported interface code. Bundled index, committed here. |
| [`ellesmereui-search`](skills/ellesmereui-search/) | Searches the EllesmereUI addon suite's own source: symbols, settings keys, locale strings, events, slash commands. Index built locally from your checkout. |
| [`ellesmereui-pr-check`](skills/ellesmereui-pr-check/) | Checks EllesmereUI changes against the code style rules in the addon's `CONTRIBUTING.md`, and flags code that names another addon so its provenance gets checked. Diff-scoped; installs as a pre-commit hook. |

`wow-api-search` answers "what does this Blizzard API do" and "how does
Blizzard implement this". `ellesmereui-search` answers "where does EllesmereUI
define or read this". `ellesmereui-pr-check` answers "will this change survive
review". Each skill's own README documents its format, scripts, and limitations.

## Install

```bash
git clone git@github.com:nulltyto/wow-tools.git ~/Repos/wow-tools
cd ~/Repos/wow-tools
./install.sh              # macOS, Linux, WSL, Git Bash
.\install.ps1             # Windows PowerShell
```

The installer asks which harness you use and which skills you want, then puts
them where that harness reads them. It needs Python 3.9+ and nothing else; if
none is on `PATH` it will use `uv` to provide one.

Non-interactive:

```bash
./install.sh --harness claude-code --skills all --yes
./install.sh --harness codex,cursor,gemini-cli --skills wow-api-search --yes
./install.sh --harness claude-code --scope project --project-root ~/Repos/my-addon --yes
```

| | |
|---|---|
| `list` | every skill and harness, with the directory each resolves to |
| `status` | what is installed where right now |
| `install` | `--dry-run`, `--copy`, `--force`, `--scope user\|project` |
| `uninstall` | removes only what this installer placed |

Skills are **symlinked** by default, so `git pull` updates every harness at
once. Where symlinks are unavailable — Windows without Developer Mode, an
exFAT or NTFS mount — the installer detects it and copies instead, which needs
a re-run after a pull. `--copy` forces that everywhere.

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
wow_tools/         The installer: harness registry, link/copy engine, CLI
tests/             Hookup and installer tests
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
an index from a local checkout. The Apache-2.0 license here covers the skills
and tooling in this repo, not Blizzard's data. This project is unaffiliated
with Blizzard.
