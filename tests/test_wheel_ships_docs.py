"""Guard: the built wheel carries the full canonical docs corpus.

WHY this test exists. The engine's RFC-9457 error projection emits ``type = "about:blank"``
and mints no per-error web URI — a rendered error's ``audit_code`` / ``rule_id`` resolves
LOCALLY against the docs shipped in the package (``docs/components/error-channel/reference.md``,
the ``type`` row). That contract is a lie unless the corpus actually ships inside the installed
package. ``docs/`` lives outside the importable package (``src/conjured/``), so it reaches the
wheel only through ``setup.py``'s custom ``build_py`` (which force-includes it). This test goes
RED if that mechanism is removed or broken — the silent-regression defense the next packaging
edit needs.

WHY it emulates rather than builds. The mechanism's copy logic is factored into a pure-stdlib
helper (``setup.stage_docs_into``) precisely so this test can drive it directly: a provisioned
runtime venv does not keep setuptools importable (the editable install builds in an isolated
env), and this suite must not depend on setuptools. So the test loads ``setup.py`` by path (no
setuptools import), runs the real copy against a tmp dir, and asserts the corpus lands — plus an
AST check that the copy is actually wired into ``build_py`` via ``cmdclass`` (the wheel's only
route to the tree)."""

from __future__ import annotations

import ast
import importlib.util
import types
from pathlib import Path

import pytest

# tests/ -> the package root
_PKG_ROOT = Path(__file__).resolve().parents[1]
_SETUP_PY = _PKG_ROOT / "setup.py"
_DOCS_SRC = _PKG_ROOT / "docs"

# Two known anchors the local error->docs resolution must be able to reach: a per-component
# reference (any rule_id cites into one) and a kind-schema TOML (a distinct file type + a nested
# path). Named, not counted — the corpus grows, so nothing here pins an exact size.
_ANCHOR_REFERENCE = Path("docs/components/error-channel/reference.md")
_ANCHOR_KIND_SCHEMA_GLOB = "docs/components/*/kind-schemas/*.toml"


def _load_setup_module() -> types.ModuleType:
    """Import conjured/setup.py as a module WITHOUT triggering its ``setup()`` call or importing
    setuptools (both are guarded behind ``if __name__ == '__main__'`` / a lazy factory)."""
    spec = importlib.util.spec_from_file_location("conjured_setup_shim", _SETUP_PY)
    assert spec and spec.loader, "could not build import spec for conjured/setup.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _expected_corpus_relpaths() -> set[Path]:
    """Independently walk docs/ with the same prune semantics the shim declares, yielding the set
    of relpaths (under the package root) the wheel must carry. Independent of the shim's copy so a
    broken copy diverges from this set."""
    setup = _load_setup_module()
    prune_dirs = set(setup._PRUNE_DIRS)
    prune_suffixes = set(setup._PRUNE_SUFFIXES)
    kept: set[Path] = set()
    for p in _DOCS_SRC.rglob("*"):
        rel = p.relative_to(_DOCS_SRC)
        if prune_dirs.intersection(rel.parts):
            continue
        if p.is_dir() or p.suffix in prune_suffixes:
            continue
        kept.add(Path("docs") / rel)
    return kept


def test_setup_module_imports_without_setuptools():
    """The shim (and its copy helper) must import in a setuptools-free runtime venv — otherwise
    this very suite could not run it. setuptools is reached only lazily, at build time."""
    import sys

    had_setuptools = "setuptools" in sys.modules
    _load_setup_module()
    if not had_setuptools:
        assert "setuptools" not in sys.modules, "importing setup.py must not pull in setuptools"


def test_wheel_would_carry_full_docs_corpus(tmp_path):
    """The force-include copy reproduces the ENTIRE corpus under the package. Emulates
    'unzip the wheel, look under conjured/docs' — RED if the copy is removed or partial."""
    setup = _load_setup_module()
    pkg_build_dir = tmp_path / "conjured"
    copied = setup.stage_docs_into(pkg_build_dir)

    copied_rel = {Path(p).resolve().relative_to(pkg_build_dir) for p in copied}
    expected = _expected_corpus_relpaths()

    assert copied_rel == expected, (
        "wheel docs copy diverges from the source corpus; "
        f"missing={sorted(str(p) for p in expected - copied_rel)}, "
        f"extra={sorted(str(p) for p in copied_rel - expected)}"
    )
    # Non-empty + the named anchors resolve (guards the degenerate 'both walks pointed nowhere').
    assert copied_rel, "no docs were copied — the corpus is empty"
    assert _ANCHOR_REFERENCE in copied_rel, f"{_ANCHOR_REFERENCE} not carried into the wheel"
    assert any(p.match(_ANCHOR_KIND_SCHEMA_GLOB) for p in copied_rel), (
        f"no file matching {_ANCHOR_KIND_SCHEMA_GLOB} carried into the wheel"
    )
    # Every copied file is byte-identical to source (no line-ending rewrite etc.).
    ref_dest = pkg_build_dir / _ANCHOR_REFERENCE
    ref_src = _PKG_ROOT / _ANCHOR_REFERENCE
    assert ref_dest.read_bytes() == ref_src.read_bytes(), "docs copy is not byte-exact"


def test_build_prunes_detritus(tmp_path):
    """Only real corpus files ride the wheel; build/cache detritus is left out — RED if the
    prune logic is dropped. Uses a synthetic source so the assertion is independent of whatever
    the real docs/ tree happens to contain today."""
    setup = _load_setup_module()
    synth = tmp_path / "docs_src"
    (synth / "components").mkdir(parents=True)
    (synth / "components" / "reference.md").write_text("# real\n", encoding="utf-8")
    # Detritus that must NOT ship:
    (synth / "_build").mkdir()
    (synth / "_build" / "index.html").write_text("built\n", encoding="utf-8")
    (synth / "__pycache__").mkdir()
    (synth / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (synth / "components" / "gen.pyc").write_bytes(b"\x00")

    dest = tmp_path / "conjured"
    copied = {Path(p).resolve().relative_to(dest) for p in setup.stage_docs_into(dest, docs_src=synth)}

    assert copied == {Path("docs/components/reference.md")}, f"prune failed; got {sorted(map(str, copied))}"


def test_build_py_is_wired_to_the_copy():
    """AST guard: setup() passes a cmdclass overriding build_py, and the copy helper is invoked
    from a build command. Catches the 'helper kept but build_py unwired' regression the pure copy
    test alone would miss — the wheel's only route to docs/ is this wiring."""
    tree = ast.parse(_SETUP_PY.read_text(encoding="utf-8"), filename=str(_SETUP_PY))

    setup_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setup"
    ]
    assert setup_calls, "setup.py never calls setup()"

    def _cmdclass_has_build_py(call: ast.Call) -> bool:
        for kw in call.keywords:
            if kw.arg == "cmdclass" and isinstance(kw.value, ast.Dict):
                for key in kw.value.keys:
                    if isinstance(key, ast.Constant) and key.value == "build_py":
                        return True
        return False

    assert any(_cmdclass_has_build_py(c) for c in setup_calls), (
        "setup(cmdclass=...) does not override 'build_py' — docs would not reach the wheel"
    )

    invokes_copy = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "stage_docs_into"
        for node in ast.walk(tree)
    )
    assert invokes_copy, "the docs-staging copy (stage_docs_into) is never invoked in the build"
