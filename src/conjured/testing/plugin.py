"""The ``conjured.testing`` pytest plugin — registry + import isolation as fixtures.

Registered via the ``pytest11`` entry-point group (``conjured``'s ``pyproject.toml``), so installing
the engine makes these fixtures available to a consumer's suite. The contract:
``conjured/docs/components/testing/reference.md`` names "the registry-isolation plugin" among the
seam helpers; its shape is authored here with the code.

What actually needs isolating: the ``DeclarationRegistry`` is instance-scoped (a fresh one per test
leaks nothing), so the real cross-test contamination surface is the **process-global import system** —
a handler module a test writes and the engine then imports persists in ``sys.modules``. ``module_writer``
owns that leak: it writes modules under a per-test ``tmp_path`` on ``sys.path`` and, on teardown,
evicts exactly the modules loaded from that directory (and restores ``sys.path``), so a module name is
safe to reuse in the next test — turning the engine suite's "unique name by discipline" convention
into a structural guarantee. The isolation rides the leak-creating fixture rather than being autouse,
so a suite that writes no modules pays nothing and nothing global changes for it.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path
from typing import Callable, Iterator

import pytest

from conjured.validator.registry import DeclarationRegistry


@pytest.fixture
def conjured_registry() -> DeclarationRegistry:
    """A fresh, empty :class:`~conjured.validator.registry.DeclarationRegistry` per test. The
    registry is instance-scoped, so this is the whole of registry isolation — no global state to
    save or restore."""
    return DeclarationRegistry()


@pytest.fixture
def module_writer(tmp_path: Path) -> Iterator[Callable[[str, str], str]]:
    """Write importable handler/adapter modules for a test, with import isolation.

    Yields ``write(name, source) -> name``: it writes ``<name>.py`` under a per-test directory that
    is on ``sys.path`` and invalidates the import caches so the engine's resolution can import it by
    dotted name. On teardown it evicts every module that was imported from that directory and removes
    the directory from ``sys.path``, so the same module name can be reused by another test without
    picking up a stale ``sys.modules`` entry. Engine modules (imported from elsewhere) are left
    cached — only the test's own written modules are evicted.
    """
    root = tmp_path
    root_str = str(root)
    sys.path.insert(0, root_str)
    before = set(sys.modules)

    def write(name: str, source: str) -> str:
        (root / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
        importlib.invalidate_caches()
        return name

    try:
        yield write
    finally:
        # Scoped eviction: drop exactly the modules imported from THIS test's `root` (its own written
        # modules), leaving cached anything imported from elsewhere — the `- before` snapshot excludes
        # pre-existing modules, and `under_root` additionally spares a module first imported from
        # outside `root` during the window (a lazily-imported engine module), so the next test's engine
        # imports aren't needlessly re-paid while a reused name is still stale-free.
        # guarantees: module-writer-scoped-eviction
        for module_name in set(sys.modules) - before:
            module = sys.modules.get(module_name)
            file = getattr(module, "__file__", None)
            if file is None:
                continue
            try:
                under_root = Path(file).resolve().is_relative_to(root.resolve())
            except (OSError, ValueError):
                under_root = False
            if under_root:
                del sys.modules[module_name]
        # Restoration: this fixture inserted `root_str` and OWNS its removal. An absent entry at
        # teardown means the isolation invariant was broken (a global sys.path mutation escaped the
        # fixture) — fail loud rather than silently swallow a ValueError, which would leave the escape
        # invisible and the next test's sys.path polluted.
        # guarantees: module-writer-restores-sys-path
        if root_str not in sys.path:
            raise RuntimeError(
                f"module_writer teardown: '{root_str}' is no longer on sys.path — this fixture "
                f"inserted it and owns its removal, so an absent entry means the import-isolation "
                f"invariant was broken (a global sys.path mutation escaped the fixture)."
            )
        sys.path.remove(root_str)
        importlib.invalidate_caches()
