"""Where each agent harness looks for Agent Skills.

Anthropic released the Agent Skills format as an open standard in December 2025
(agentskills.io). The practical consequence for this installer is that almost
every harness now reads the *same* skill directory layout -- a folder with a
SKILL.md -- and most of them additionally honour the cross-agent path
`~/.agents/skills`. So installing a skill for twelve harnesses is usually one
symlink, not twelve, and the interesting part of this file is the exceptions.

Every path below was read off the harness's own documentation; `docs` records
where. Paths are given home-relative (user scope) or project-relative (project
scope) with forward slashes, and are resolved with pathlib, so the same string
works on Windows.

Preference order within `skills_user` / `skills_project` matters: the first
entry is what the installer writes to. Where a harness documents both a
brand-specific directory and the cross-agent one, the cross-agent path is
preferred whenever the harness honours it -- one directory serving ten
harnesses is the whole point of the standard.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The cross-agent path from the open standard. Honoured by the large majority
# of harnesses; a handful predate it or namespace everything under their own
# directory, and those are the entries that carry a brand path first.
AGENTS_USER = ".agents/skills"
AGENTS_PROJECT = ".agents/skills"


@dataclass(frozen=True)
class Harness:
    """One agent harness and the directories it reads skills from."""

    key: str
    name: str
    # Candidate install directories, most preferred first. Empty means this
    # harness has no directory of that scope.
    skills_user: tuple[str, ...] = ()
    skills_project: tuple[str, ...] = ()
    # Home-relative paths whose existence suggests the harness is installed.
    # Only ever used to order and annotate the menu, never to block an install:
    # plenty of people install skills before the harness, or use a harness
    # whose config directory is created lazily on first run.
    detect: tuple[str, ...] = ()
    docs: str = ""
    note: str = ""

    @property
    def installable(self) -> bool:
        return bool(self.skills_user or self.skills_project)


# --------------------------------------------------------------------------
#  The registry
# --------------------------------------------------------------------------
# Ordered roughly by how likely a WoW addon developer is to be using them,
# since this list is also the interactive menu.

HARNESSES: tuple[Harness, ...] = (
    Harness(
        key="claude-code",
        name="Claude Code",
        skills_user=(".claude/skills",),
        skills_project=(".claude/skills",),
        detect=(".claude",),
        docs="https://code.claude.com/docs/en/skills",
        note="Native Agent Skills. Does not read ~/.agents/skills, so it gets its own link.",
    ),
    Harness(
        key="codex",
        name="OpenAI Codex",
        skills_user=(AGENTS_USER,),
        skills_project=(AGENTS_PROJECT,),
        detect=(".codex",),
        docs="https://learn.chatgpt.com/docs/build-skills",
        note="Reads ~/.agents/skills directly; also scans .agents/skills from cwd up to the repo root.",
    ),
    Harness(
        key="cursor",
        name="Cursor",
        skills_user=(AGENTS_USER, ".cursor/skills"),
        skills_project=(AGENTS_PROJECT, ".cursor/skills"),
        detect=(".cursor",),
        docs="https://cursor.com/docs/context/skills",
        note="Reads ~/.agents/skills, and for compatibility ~/.claude/skills and ~/.codex/skills too.",
    ),
    Harness(
        key="gemini-cli",
        name="Gemini CLI",
        skills_user=(AGENTS_USER, ".gemini/skills"),
        skills_project=(AGENTS_PROJECT, ".gemini/skills"),
        detect=(".gemini",),
        docs="https://geminicli.com/docs/cli/skills/",
        note="Treats .agents/skills as an alias of .gemini/skills, and gives it precedence.",
    ),
    Harness(
        key="vscode-copilot",
        name="VS Code (GitHub Copilot)",
        skills_user=(AGENTS_USER, ".copilot/skills"),
        skills_project=(".github/skills", AGENTS_PROJECT),
        detect=(".copilot", ".vscode"),
        docs="https://code.visualstudio.com/docs/copilot/customization/agent-skills",
        note="Project scope prefers .github/skills, which is the path VS Code documents first.",
    ),
    Harness(
        key="copilot-cli",
        name="GitHub Copilot CLI",
        skills_user=(AGENTS_USER, ".copilot/skills"),
        skills_project=(".github/skills", AGENTS_PROJECT),
        detect=(".copilot",),
        docs="https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills",
        note="Run /skills reload in an open session to pick up a new install.",
    ),
    Harness(
        key="opencode",
        name="opencode",
        skills_user=(AGENTS_USER, ".config/opencode/skills"),
        skills_project=(AGENTS_PROJECT, ".opencode/skills"),
        detect=(".config/opencode", ".opencode"),
        docs="https://opencode.ai/docs/skills/",
        note="Reads ~/.agents/skills, ~/.claude/skills and ~/.config/opencode/skills.",
    ),
    Harness(
        key="kiro",
        name="Kiro",
        skills_user=(".kiro/skills",),
        skills_project=(".kiro/skills",),
        detect=(".kiro",),
        docs="https://kiro.dev/docs/skills/",
        note="Documents no .agents/skills alias, so this one needs its own link.",
    ),
    Harness(
        key="qwen-code",
        name="Qwen Code",
        skills_user=(".qwen/skills",),
        skills_project=(".qwen/skills",),
        detect=(".qwen",),
        docs="https://qwenlm.github.io/qwen-code-docs/en/users/features/skills/",
        note="Brand path only. Restart Qwen Code after installing; it loads skills at startup.",
    ),
    Harness(
        key="roo-code",
        name="Roo Code",
        skills_user=(AGENTS_USER, ".roo/skills"),
        skills_project=(AGENTS_PROJECT, ".roo/skills"),
        detect=(".roo",),
        docs="https://roocodeinc.github.io/Roo-Code/features/skills",
        note="Roo-specific paths outrank .agents/skills, which only matters if a name collides.",
    ),
    Harness(
        key="kilo-code",
        name="Kilo Code",
        skills_user=(".kilo/skills",),
        skills_project=(AGENTS_PROJECT, ".kilo/skills"),
        detect=(".kilo", ".kilocode"),
        docs="https://kilo.ai/docs/customize/skills",
        note="Loads .agents/skills per project, but documents only ~/.kilo/skills globally.",
    ),
    Harness(
        key="kimi-cli",
        name="Kimi CLI",
        skills_user=(".config/agents/skills", AGENTS_USER, ".kimi/skills"),
        skills_project=(AGENTS_PROJECT, ".kimi/skills"),
        detect=(".kimi", ".config/agents"),
        docs="https://moonshotai.github.io/kimi-cli/en/customization/skills.html",
        note="Recommends ~/.config/agents/skills for the generic scope; ~/.agents/skills also works.",
    ),
    Harness(
        key="mistral-vibe",
        name="Mistral Vibe",
        skills_user=(".vibe/skills",),
        skills_project=(AGENTS_PROJECT, ".vibe/skills"),
        detect=(".vibe",),
        docs="https://docs.mistral.ai/vibe/code/cli/skills",
        note="Project .agents/skills is read only when the working directory is trusted.",
    ),
    Harness(
        key="antigravity",
        name="Google Antigravity",
        skills_user=(".gemini/config/skills",),
        skills_project=(AGENTS_PROJECT,),
        detect=(".antigravity", ".gemini"),
        docs="https://antigravity.google/docs/skills",
        note="~/.gemini/config/skills is the one global path all three flavours (IDE, CLI, AGY) read.",
    ),
    Harness(
        key="openhands",
        name="OpenHands",
        skills_user=(AGENTS_USER,),
        skills_project=(AGENTS_PROJECT, ".openhands/skills"),
        detect=(".openhands",),
        docs="https://docs.openhands.dev/overview/skills",
        note=".agents/skills is the recommended location; .openhands/skills is legacy.",
    ),
    Harness(
        key="pi",
        name="pi (and pi variants)",
        skills_user=(AGENTS_USER, ".pi/agent/skills"),
        skills_project=(AGENTS_PROJECT, ".pi/skills"),
        detect=(".pi",),
        docs="https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/skills.md",
        note="Covers oh-my-pi and other pi distributions, which inherit pi's discovery paths.",
    ),
    Harness(
        key="minimax-cli",
        name="MiniMax CLI",
        skills_user=(".claude/skills", AGENTS_USER),
        skills_project=(AGENTS_PROJECT,),
        detect=(".minimax",),
        docs="https://github.com/MiniMax-AI/skills",
        note="Has no directory of its own: it reads other agents' skill directories, ~/.claude/skills first.",
    ),
    Harness(
        key="devin",
        name="Devin",
        skills_project=(AGENTS_PROJECT,),
        docs="https://docs.devin.ai/product-guides/skills",
        note=(
            "Project scope only, and it must be committed. Devin indexes .agents/skills from "
            "connected repositories rather than from a machine, so there is nothing to install "
            "into a home directory."
        ),
    ),
    Harness(
        key="agents-standard",
        name="Any spec-compliant agent",
        skills_user=(AGENTS_USER,),
        skills_project=(AGENTS_PROJECT,),
        docs="https://agentskills.io/specification",
        note=(
            "Not a product. Pick this to write the cross-agent path on its own, for a "
            "spec-compliant harness not listed here (Amp, Goose, Factory, Junie, Letta, ...)."
        ),
    ),
    Harness(
        key="aider",
        name="Aider",
        docs="https://aider.chat/",
        note=(
            "No skill directory to install into. Aider does not auto-discover skills; load one "
            "per session with `/read-only <path>/SKILL.md`. The installer prints the paths."
        ),
    ),
)

HARNESS_BY_KEY = {h.key: h for h in HARNESSES}


@dataclass
class Resolution:
    """A harness paired with the concrete directory an install would write to."""

    harness: Harness
    directory: object | None = None  # pathlib.Path, or None when not installable
    shared_with: list[str] = field(default_factory=list)


def get(key: str) -> Harness:
    try:
        return HARNESS_BY_KEY[key]
    except KeyError:
        raise KeyError(
            f"unknown harness {key!r}. Known: {', '.join(sorted(HARNESS_BY_KEY))}"
        ) from None
