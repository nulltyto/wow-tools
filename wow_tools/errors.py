"""Errors that carry a finished sentence.

Small and dependency-free so that anything can raise these without importing
the thing that catches them.
"""

from __future__ import annotations


class UnknownName(Exception):
    """A name the user gave that nothing matches.

    The message is the whole value: `str(e)` is what gets printed. This was a
    KeyError, whose repr wraps its argument in quotes, so every call site
    unwrapped `e.args[0]` behind the same four-line comment explaining why.
    """
