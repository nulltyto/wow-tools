# wow-tools

Skills, rules, addons and git hooks for World of Warcraft addon development,
plus the installer that places each of them where the thing that reads it will
look.

## Language

### What gets installed

**Harness**:
An agent tool that reads skills and rules from a directory on disk. Claude
Code, Codex and Cursor are three of them.
_Avoid_: agent, client, editor, IDE

**Skill**:
A bundle of instructions for one kind of task, which a harness loads only when
it judges the task relevant.
_Avoid_: prompt, plugin, command

**Rule**:
An instruction loaded into every session a harness runs, in every repository.
Always on, where a skill costs nothing until it is needed.
_Avoid_: instruction, memory, always-on prompt

**Addon**:
Code installed into the game client rather than into a harness.
_Avoid_: mod, plugin

**Hook**:
A check this repo installs into a git repository, which runs on commit and can
refuse one.

**Scope**:
Whether an install is for the whole user or for one project directory.

**Shadow copy**:
A second copy of an addon at a path the game also reads, which silently wins
over the one being edited.

### The game

**Flavor**:
One build of the game client, each with its own install tree and addon folder.
Retail and Classic are flavors.
_Avoid_: version, branch

**Secret value**:
A value the client makes opaque to an addon during combat, so that reading or
comparing it raises rather than returning a wrong answer.
_Avoid_: protected value, tainted value

### Answering questions about code

**Index**:
The generated tables a search skill answers from, built from a source tree it
does not own.

**Index store**:
Where one built index lives, and whether it is still good enough to answer
from.

**Masker**:
A copy of a Lua source with comments and string bodies blanked out, so a
structural search cannot match inside them.
_Avoid_: stripper, sanitiser

**Freshness**:
Whether an index still describes the source it was built from. A stale index
answers confidently and wrongly, so it is checked rather than assumed.

### Conventions of this repo

**Standalone script**:
A script that runs from a bare clone with nothing installed, importing only
the standard library. Every script inside a skill is one, because a skill
directory is installed on its own.
_Avoid_: helper, utility

**Script registry**:
The single table naming where each standalone script lives, so a caller refers
to one by name rather than by rebuilding its path.

**Agreement test**:
A test asserting that two deliberate copies of the same logic still behave
identically. Used where the copies cannot import each other.
_Avoid_: drift test, duplication test
