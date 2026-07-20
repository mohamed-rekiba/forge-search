"""Test isolation.

Roots live in `tmp_path`, but indexes resolve through `cache_root()`, which
ignores `tmp_path` entirely — so until this file existed every test wrote a
real `.db` into the developer's OS cache directory and left it there. A run of
the suite added dozens of `r-<hash>.db` files to `~/Library/Caches`, and the
benchmark experiments left ~600 MB behind.

Harmless in the sense that a cache is disposable, but a test suite that writes
outside its temp directory is wrong on principle, and on CI it means tests
share state across runs.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path_factory, monkeypatch):
    """Point every test's index cache at a per-test temp directory."""
    monkeypatch.setenv(
        "FORGE_SEARCH_CACHE_DIR", str(tmp_path_factory.mktemp("forge-cache"))
    )
