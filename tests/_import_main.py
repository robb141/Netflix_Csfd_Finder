"""
Shared helper for importing the project's ``main`` module in tests.

``main.py`` calls ``input()`` at module import time (to ask which csfd user
to compare against) and also spins up its own logging handlers as a side
effect of import. Neither of those should run during `python -m unittest
discover`, so this module imports ``main`` exactly once, with
``builtins.input`` patched, and exposes the already-imported module for
every test module to reuse.
"""
from unittest.mock import patch

with patch('builtins.input', return_value='testuser'):
    import main  # noqa: E402  (import-after-patch is intentional)
