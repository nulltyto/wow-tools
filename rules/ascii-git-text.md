---
name: ascii-git-text
description: Write commit messages, pull request titles and bodies, and review comments in ASCII only.
inclusion: always
alwaysApply: true
applyTo: "**"
---

<!-- wow-tools-rule: ascii-git-text -->

# ASCII only in commit messages and pull request text

Write every commit message, pull request title, pull request body, issue body,
and review comment in ASCII. No em dashes, en dashes, curly quotes, ellipsis
characters, arrows, bullets, or non-breaking spaces.

Use the ASCII form instead:

| Instead of | Write |
|---|---|
| `—` em dash | `--` |
| `–` en dash | `-` |
| `‘` `’` curly single quotes | `'` |
| `“` `”` curly double quotes | `"` |
| `…` ellipsis | `...` |
| `→` arrow | `->` |
| `•` bullet | `*` |
| non-breaking space | a plain space |

## Why this one is worth a rule

Source files get linted, reviewed, and fixed. This text does not. It goes from
a keyboard or a model straight into a place where it is quoted, re-encoded, and
read back for years by terminals, mail gateways, changelog generators, and
release tooling that do not agree on an encoding. A curly quote shows up later
as a mojibake blob in somebody's `git log`.

By then the commit is immutable. Rewriting published history to correct
punctuation is not worth doing, so the character has to not be written in the
first place.

## This rule does not cover source files

Prose in Markdown, comments in code, and user-facing strings are a separate
question with a separate answer per repository. This rule is only about the
text git and GitHub store as metadata.
