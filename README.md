# wow-tools

Skills, tools, and scripts for World of Warcraft addon development with
[Claude Code](https://claude.com/claude-code).

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

Clone the repo, then symlink each skill into your Claude Code skills
directory:

```bash
git clone git@github.com:nulltyto/wow-tools.git ~/Repos/wow-tools
for s in wow-api-search ellesmereui-search ellesmereui-pr-check; do
  ln -s ~/Repos/wow-tools/skills/$s ~/.claude/skills/$s
done
```

Restart Claude Code. The symlinks mean `git pull` updates the live skills.

For claude.ai, build an uploadable `.skill` bundle with the packager from
Anthropic's [skills repo](https://github.com/anthropics/skills):

```bash
cd /path/to/skills/skill-creator
python -m scripts.package_skill /path/to/wow-tools/skills/<skill-name>
```

## Layout

```
skills/            One directory per Claude Code skill, each self-contained
```

Future tools and scripts get their own top-level directories.

## Attribution

World of Warcraft is a trademark of Blizzard Entertainment, Inc. The
`wow-api-search` index is generated from Blizzard's exported interface code,
sourced via [Gethe/wow-ui-source](https://github.com/Gethe/wow-ui-source);
that underlying game data belongs to Blizzard. EllesmereUI is by Ellesmere
Gaming; `ellesmereui-search` contains no addon code, only tooling that builds
an index from a local checkout. The Apache-2.0 license here covers the skills
and tooling in this repo, not Blizzard's data. This project is unaffiliated
with Blizzard.
