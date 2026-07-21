"""The resolution pass's bundle arm (``validator.resolve._resolve_composition_ref``,
BundleComposition branch) — the registry-twin stamping every later substitution
splices (glossary § Bundle TOML; the external-file mechanism, one across every layer).

Covers the arm's happy + error paths per the baseline standard: the two-directory
anchor adversary (a bundle's ``{ file }`` binding anchors to the BUNDLE's own
declaration directory, never the outer pipeline's), the no-anchor fail-loud contract,
and the resolve-side cycle guard (this pass has no precondition that compile ran
first, so it carries its own rejection).
"""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation
from conjured.hasher import pipeline_hash
from conjured.validator import DeclarationRegistry, loads
from conjured.validator.resolve import resolve_pipeline_bindings

_TRANSFORM_NORMALIZE = (
    '[transform]\n[reads]\nplayer_input = { type = "str" }\n'
    '[output_schema]\nnormalized_input = { type = "str" }\n'
    '[bindings.config]\nmarker_set = { type = "str" }\n'
)

_BUNDLE_WITH_FILE_BINDING = (
    '[meta]\nkind = "bundle"\nname = "prep"\n'
    '[[nodes]]\nkind = "handler"\nname = "acme.norm"\n'
    'bindings = { config = { file = "x.toml" } }\n'
)

_PIPE = (
    '[meta]\nname = "acme.p"\n'
    '[[nodes]]\nkind = "composition"\nname = "bundles/prep.toml"\n'
    '[inputs]\nplayer_input = { type = "str" }\n'
    '[outputs]\nnormalized_input = { type = "str" }\n'
)


def test_bundle_file_binding_anchors_to_the_bundles_own_directory(tmp_path):
    """The two-directory adversary: a same-relative-named x.toml sits in BOTH the outer
    pipeline's directory (A) and the bundle's (B). The stamped registry twin must hold
    B's content — a bundle's { file } paths were written next to the bundle TOML — and
    the downstream hash must splice the STAMPED nodes (never re-read, never A's file)."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "x.toml").write_text('marker_set = "from A"', encoding="utf-8")
    (dir_b / "x.toml").write_text('marker_set = "from B"', encoding="utf-8")
    reg = DeclarationRegistry()
    reg.add_handler("acme.norm", loads(_TRANSFORM_NORMALIZE, "handler", file_path="n.toml"))
    reg.add_composition(
        "bundles/prep.toml",
        loads(_BUNDLE_WITH_FILE_BINDING, "composition", file_path=str(dir_b / "prep.toml")),
        toml_path=str(dir_b / "prep.toml"),
    )
    pipeline = loads(_PIPE, "pipeline", file_path=str(dir_a / "p.toml"))
    resolve_pipeline_bindings(pipeline, reg, base_dir=str(dir_a))
    twin = reg.get_composition("bundles/prep.toml")
    (binding,) = twin.nodes[0].bindings
    assert binding.content_hash is not None  # the twin is stamped
    assert binding.resolved == {"marker_set": "from B"}  # B's file, never A's
    assert pipeline_hash(pipeline, reg)  # the hash splices the stamped nodes


def test_bundle_file_binding_with_no_anchor_fails_loud(tmp_path):
    """A bundle registered with no declaration path (no anchor) carrying an unresolved
    { file } binding MUST fail loud — resolving against the outer directory (or CWD)
    would read and hash the wrong same-named file (the no-anchor contract, the same
    rule the pipeline and trainable arms enforce)."""
    (tmp_path / "x.toml").write_text('marker_set = "outer"', encoding="utf-8")
    reg = DeclarationRegistry()
    reg.add_handler("acme.norm", loads(_TRANSFORM_NORMALIZE, "handler", file_path="n.toml"))
    reg.add_composition(
        "bundles/prep.toml",
        loads(_BUNDLE_WITH_FILE_BINDING, "composition", file_path="prep.toml"),
        # no toml_path → no anchor
    )
    pipeline = loads(_PIPE, "pipeline", file_path=str(tmp_path / "p.toml"))
    with pytest.raises(ContractViolation) as exc:
        resolve_pipeline_bindings(pipeline, reg, base_dir=str(tmp_path))
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED


def test_resolve_carries_its_own_bundle_cycle_guard():
    """The resolution pass may run BEFORE compile, so a cyclic bundle chain must fail
    loud here too (the same COMPOSITION_CYCLE contract compose enforces), never
    recurse forever."""
    reg = DeclarationRegistry()
    reg.add_composition("bundles/a.toml", loads(
        '[meta]\nkind = "bundle"\nname = "a"\n'
        '[[nodes]]\nkind = "composition"\nname = "bundles/b.toml"\n',
        "composition", file_path="a.toml"), toml_path="ba/a.toml")
    reg.add_composition("bundles/b.toml", loads(
        '[meta]\nkind = "bundle"\nname = "b"\n'
        '[[nodes]]\nkind = "composition"\nname = "bundles/a.toml"\n',
        "composition", file_path="b.toml"), toml_path="bb/b.toml")
    pipeline = loads(
        '[meta]\nname = "acme.c"\n'
        '[[nodes]]\nkind = "composition"\nname = "bundles/a.toml"\n'
        '[inputs]\ntext = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        resolve_pipeline_bindings(pipeline, reg, base_dir="")
    assert exc.value.check is Check.COMPOSITION_CYCLE
