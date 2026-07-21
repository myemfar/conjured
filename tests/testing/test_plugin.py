"""The pytest11 plugin fixtures — a fresh registry + import-isolated module writing.

The isolation pair (``..._isolation_first`` / ``..._isolation_second``) is RED-on-removal: both write
the SAME module name with different content in separate tests; without the plugin's teardown eviction
of the test's modules, the second test would import the first's cached module and fail.
"""

from __future__ import annotations

import importlib
import sys

import pytest

import conjured.testing.plugin as plugin
from conjured.validator.registry import DeclarationRegistry


def test_conjured_registry_is_fresh_and_empty(conjured_registry):
    assert isinstance(conjured_registry, DeclarationRegistry)
    assert conjured_registry.handlers == {}
    assert conjured_registry.service_types == {}


def test_module_writer_writes_importable_module(module_writer):
    name = module_writer("plug_import_mod", "VALUE = 41\n")
    module = importlib.import_module(name)
    assert module.VALUE == 41


def test_module_writer_isolation_first(module_writer):
    name = module_writer("plug_reuse_mod", "WHICH = 'first'\n")
    assert importlib.import_module(name).WHICH == "first"


def test_module_writer_isolation_second(module_writer):
    # Same name, different content, different test: passes only because the first test's module
    # was evicted on its module_writer teardown (the plugin's import isolation).
    name = module_writer("plug_reuse_mod", "WHICH = 'second'\n")
    assert importlib.import_module(name).WHICH == "second"


def test_module_writer_restores_sys_path(tmp_path):
    # verifies: module-writer-restores-sys-path
    # Drive the fixture generator directly: setup inserts tmp_path on sys.path, teardown MUST remove
    # it. RED-on-removal: drop `sys.path.remove(root_str)` and the entry survives teardown, so the
    # final `sys.path == before` fails (the isolation invariant leaks a path per test).
    root_str = str(tmp_path)
    before = list(sys.path)
    gen = plugin.module_writer.__wrapped__(tmp_path)
    next(gen)  # setup
    assert sys.path[0] == root_str
    with pytest.raises(StopIteration):
        next(gen)  # teardown
    assert root_str not in sys.path
    assert sys.path == before  # fully restored to the pre-fixture state


def test_module_writer_teardown_fails_loud_if_sys_path_escaped(tmp_path):
    # The teardown's fail-loud (item 7): if the fixture's own sys.path entry is gone at teardown, the
    # isolation invariant was broken and teardown RAISES rather than silently swallowing a ValueError.
    # RED-on-removal: restore the `try/except ValueError: pass` and this teardown returns cleanly.
    gen = plugin.module_writer.__wrapped__(tmp_path)
    next(gen)  # setup inserts str(tmp_path)
    sys.path.remove(str(tmp_path))  # simulate a global mutation escaping the fixture
    with pytest.raises(RuntimeError):
        next(gen)  # teardown must fail loud, not swallow the missing entry


def test_module_writer_eviction_is_scoped_to_root(tmp_path):
    # verifies: module-writer-scoped-eviction
    # A module imported from `root` during the window is evicted on teardown; a module first imported
    # from OUTSIDE `root` during the same window is PRESERVED. RED-on-removal: drop the `under_root`
    # guard (evict every new module) and the outside module is wrongly evicted; drop the `del`
    # entirely and the inside module survives — both directions fail an assertion below.
    sibling = tmp_path.parent / f"{tmp_path.name}_sibling"
    sibling.mkdir(exist_ok=True)
    (sibling / "outside_root_mod.py").write_text("Y = 2\n", encoding="utf-8")
    gen = plugin.module_writer.__wrapped__(tmp_path)
    write = next(gen)  # setup snapshots `before` and inserts root
    inside = write("inside_root_mod", "X = 1\n")
    importlib.import_module(inside)
    sys.path.insert(0, str(sibling))
    try:
        importlib.invalidate_caches()
        importlib.import_module("outside_root_mod")
        assert inside in sys.modules and "outside_root_mod" in sys.modules
        with pytest.raises(StopIteration):
            next(gen)  # teardown
        assert inside not in sys.modules          # imported from root -> evicted
        assert "outside_root_mod" in sys.modules  # imported from elsewhere -> preserved (the scoping)
    finally:
        sys.modules.pop("outside_root_mod", None)
        sys.modules.pop(inside, None)
        if str(sibling) in sys.path:
            sys.path.remove(str(sibling))
