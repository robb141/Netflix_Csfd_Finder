"""
Shared helper for importing the project's ``main`` module in tests.

``main.py`` resolves the csfd username (CLI arg, falling back to an
interactive prompt) only inside its ``__main__`` guard, and sets the
module-level `user` to `None` otherwise - so importing it never blocks on
stdin. It does still spin up its own logging handlers as a side effect of
import. This module imports ``main`` exactly once and exposes the
already-imported module for every test module to reuse.
"""
import main  # noqa: F401  (imported for its side effect, re-exported below)
