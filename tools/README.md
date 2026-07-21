# tools/

Persistent build + codegen tooling that ships with the `conjured` engine package —
part of the package source tree, so it travels wherever the package source goes.
Commands below run from the package root.

## gen_error_index — error-index codegen

Derives two artifacts from the `rules:` blocks in the canonical docs (a
self-contained reader — it carries its own parse and imports no external
doc-tooling): the human-facing error-index reference and the
agent-facing error-classes table. A rule `statement` ships verbatim into the
agent table, so any `:::{transclude} <id>` directive in a statement is resolved
to its owner body (a `:::{region}` span or a referenced rule's statement) before
emission — the shipped artifact carries the resolved text, never the directive.
`--check` verifies the on-disk artifacts are fresh against the current canon (the
test / CI gate).

```
python tools/gen_error_index.py            # regenerate the artifacts
python tools/gen_error_index.py --check     # fail if stale
```

Dependencies: see `requirements.txt`.

## build_agent_surface — the in-package agent surface

Renders the `conjured.agent` package data from the canonical docs: the `llms.txt`
index, the audience-filtered docs bundle (directives expanded; schema `.toml`
companions included), and the steering content (each `kind: steering` doc with its
`renders_from` owner content extracted). Self-contained parse shared with the
docs-site build. `--check` verifies the committed surface is fresh against current
canon.

```
python tools/build_agent_surface.py            # regenerate the surface
python tools/build_agent_surface.py --check     # fail if stale
```

The canonical docs carry **no forward markers or forward-promises** — unshipped
work has no canon presence (its design and landing location are tracked outside
the package), so every doc this tooling reads describes only the current system.
