"""Build shim — the ONE thing pyproject.toml cannot express: bundling the canonical
docs corpus into the wheel.

All package metadata lives in ``pyproject.toml`` (declarative, ``setuptools.build_meta``).
This file exists only to add a custom build step, which setuptools can only take through a
``cmdclass`` passed to ``setup()`` — there is no declarative equivalent. A minimal
``setup.py`` carrying only ``cmdclass`` alongside a pyproject-configured project is the
setuptools-maintainer-recommended pattern for a custom build step (pypa/setuptools
discussion #3762; current, non-deprecated in setuptools 83.x) and works under
``python -m build``.

WHY the docs must ride the wheel. The engine's RFC-9457 error projection emits
``type = "about:blank"`` and mints **no** per-error web URI: an ``audit_code`` / ``rule_id``
on a rendered error resolves **locally, against the docs shipped in the package**, never
against a web address (``docs/components/error-channel/reference.md`` — the ``type`` row).
The corpus (``docs/``) is the authoring home of every rule an error can cite, so the
installed package must carry it. ``importlib.resources.files("conjured") / "docs"`` reaches
it once it lands under the package.

WHY a build step and not ``package-data``. ``docs/`` lives OUTSIDE the importable package
(``src/conjured/``), and setuptools includes only files *inside* the package directory in the
wheel — ``include_package_data`` / ``package-data`` cannot reach an out-of-package tree
(setuptools docs, "Controlling files in the distribution"). Relocating ``docs/`` under
``src/conjured/`` would move the authoring home the whole doc system keys off — its corpus
attestation, its validation checks, and the in-package docs-site build
(``tools/docs_site/``) — and is deliberately out of scope; so the tree is
*force-included* at build time instead: ``build_py`` copies it into the package's build
output, whence ``bdist_wheel`` zips it into the wheel at ``conjured/docs/…``.

The sdist already carries ``docs/`` via ``MANIFEST.in`` (``graft docs``); this shim closes
the same gap for the wheel.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DOCS_SRC = _HERE / "docs"

# Build/cache detritus to leave out — mirrors the ``MANIFEST.in`` prunes so the wheel and
# sdist carry the identical docs tree. Directory names excluded anywhere in the path, plus
# compiled-artifact suffixes.
_PRUNE_DIRS = frozenset({"_build", "__pycache__", ".pytest_cache", ".deepeval"})
_PRUNE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd", ".so"})


def stage_docs_into(package_build_dir: str | Path, *, docs_src: str | Path = _DOCS_SRC) -> list[Path]:
    """Copy the canonical docs corpus into ``<package_build_dir>/docs``, byte-for-byte,
    mirroring the MANIFEST prunes.

    ``package_build_dir`` is the *package's* directory in the build output tree
    (``<build_lib>/conjured``), so the corpus lands at ``conjured/docs/…`` in the wheel and
    resolves via ``importlib.resources.files("conjured") / "docs"`` once installed.

    Pure stdlib and side-effect-scoped to ``package_build_dir`` on purpose: the wheel-ships-docs
    guard test calls it directly against a ``tmp_path`` (the provisioned venv does not keep
    setuptools importable, so the test cannot drive the real command). Returns the destination
    paths copied, sorted, for the caller to assert over.
    """
    src_root = Path(docs_src)
    dest_root = Path(package_build_dir) / "docs"
    copied: list[Path] = []
    for src in sorted(src_root.rglob("*")):
        rel = src.relative_to(src_root)
        if _PRUNE_DIRS.intersection(rel.parts):
            continue
        if src.is_dir():
            continue
        if src.suffix in _PRUNE_SUFFIXES:
            continue
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)  # copy2 = byte-exact + metadata; no line-ending rewrite
        copied.append(dest)
    return copied


def _docs_bundling_build_py() -> type:
    """Return a ``build_py`` subclass that force-includes ``docs/`` after the stock run.

    setuptools is imported lazily *inside* this factory (not at module top level) so the
    module — and its ``stage_docs_into`` helper — imports cleanly in a runtime venv that has
    no setuptools (the guard test's environment). setuptools is present only during the build,
    which is the only time this factory is called.
    """
    from setuptools.command.build_py import build_py as _build_py

    class _DocsBundlingBuildPy(_build_py):
        def run(self) -> None:
            super().run()
            # self.build_lib is <build>/lib; the package's dir under it is <build>/lib/conjured.
            stage_docs_into(Path(self.build_lib) / "conjured")

    return _DocsBundlingBuildPy


if __name__ == "__main__":
    from setuptools import setup

    setup(cmdclass={"build_py": _docs_bundling_build_py()})
