# Sphinx configuration for the shipping docs site — copied into the preprocessed
# build source by ``build.py`` (Sphinx requires conf.py inside the source dir; the
# persistent home is here, in-package, so the directory-lift carries it).
#
# The build script exports CONJURED_AUTODOC2_SRC (the absolute path of the
# ``src/conjured`` package dir) before invoking Sphinx — conf.py is copied into a
# temporary tree, so a relative path would not survive; the env var is set per-build
# and never hardcodes a machine path.

import os

project = "Conjured"
author = "Conjured"
extensions = ["myst_parser", "sphinxcontrib.mermaid", "autodoc2"]
myst_enable_extensions = ["attrs_block", "attrs_inline", "colon_fence", "deflist"]
# Corpus `[text](#anchor)` cites resolve against headings/targets ONLY — never the
# autodoc2 py-domain objects (a corpus anchor like `handler` must not be capturable
# by `conjured.ir.handler`; the API companion is reached by nav, and its internal
# refs are domain-explicit `{py:obj}` roles this setting does not touch).
myst_ref_domains = ["std"]
root_doc = "index"
html_theme = "furo"
html_title = "Conjured — canonical docs"
html_show_sourcelink = False
# NOTE: xref_missing is deliberately NOT suppressed — the multi-page build exists to
# SURFACE unresolved rule-id / region-id citations (a singlehtml render masks them).
# "ref.python" IS suppressed: it fires only on the autodoc2 pages' short-name
# annotation refs (several public classes share attribute names like `type`), an
# apidocs-internal resolution nicety — the canonical corpus's link integrity is
# carried by the myst.* warning classes, which stay fully on.
suppress_warnings = ["myst.header", "toc.not_included", "ref.python"]
exclude_patterns = ["_build"]

# API reference (engine-maintainer companion; the canonical docs remain the spec).
_autodoc_src = os.environ.get("CONJURED_AUTODOC2_SRC")
if not _autodoc_src:
    raise RuntimeError(
        "CONJURED_AUTODOC2_SRC is not set — run the build via tools/docs_site/build.py, "
        "which exports the package source path for autodoc2"
    )
autodoc2_packages = [{"path": _autodoc_src, "module": "conjured"}]
autodoc2_render_plugin = "myst"
autodoc2_output_dir = "apidocs"
# Public API only — an underscore name is non-public by definition, and private
# values (e.g. a raw grammar-string constant) are not written to survive a MyST
# render of their repr.
autodoc2_hidden_objects = ["private", "dunder"]
