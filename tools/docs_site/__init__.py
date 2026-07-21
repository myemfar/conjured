"""The shipping docs-site build — renders ``conjured/docs/`` (the canonical corpus)
into the multi-page integrator site (Sphinx + MyST + Furo), with the corpus's
``:::{region}`` / ``:::{transclude}`` directives expanded and every ``rules:`` block
rendered as anchored, deep-linkable rule sections.

Lives inside the package directory so the directory-lift carries it (the extraction
boundary): self-contained over ``markdown-it-py`` + ``PyYAML`` — the same posture as
``tools/gen_error_index.py`` — every import resolves inside the package or those two
libraries.

Entry point: ``python -m tools.docs_site.build`` from the package directory (or
``python tools/docs_site/build.py``). Sphinx-stack dependencies ship as the
``conjured[docs]`` extra.
"""
