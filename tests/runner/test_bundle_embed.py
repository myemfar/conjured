"""The ``bundle`` composition kind — pure substitution, end-to-end
(glossary § Bundle TOML; handler/reference.md § A composition mirrors the pipeline;
``bundle.schema.toml``).

Acceptance over real parsed + compiled + assembled pipelines dispatched through
``conjured.runner.run`` (no engine internals mocked): the bundle grammar (the minimal
``[meta]`` + ``[[nodes]]`` + optional ``[annotations]`` closed set, each violation a
structured ``ContractViolation``), the substitution path (the bundle's handlers
dispatch INLINE — no bundle node exists at runtime), the **hash-identity invariant**
(an embedded bundle hashes identically to the same nodes written inline — the seal the
pre-positioned own-hash-domain kind-guard defends), recursive substitution (bundle in
bundle), and compose-time cycle rejection (the exact self-embedding adversary).

Real modules on ``sys.path`` via ``tmp_path``; no engine internals mocked.
"""

from __future__ import annotations

import importlib
import textwrap

import pytest

from conjured.errors import Check, ContractViolation, ContractViolationGroup
from conjured.hasher import pipeline_hash
from conjured.ir.composition import BundleComposition
from conjured.validator import DeclarationRegistry, compile_pipeline, loads
from conjured.runner.assemble import assemble
from conjured.runner.run import run


@pytest.fixture
def module_dir(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, name: str, source: str) -> None:
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    importlib.invalidate_caches()


_TRANSFORM_TOML = (
    '[transform]\n[reads]\n{read} = {{ type = "str" }}\n'
    '[output_schema]\n{write} = {{ type = "str" }}\n'
)

_BUNDLE_TOML = (
    '[meta]\nkind = "bundle"\nname = "prep"\n'
    '[[nodes]]\nkind = "handler"\nname = "{mod}.mark"\n'
    '[[nodes]]\nkind = "handler"\nname = "{mod}.wrap"\n'
)

_PIPE_EMBEDDED = (
    '[meta]\nname = "acme.embedded"\n'
    '[[nodes]]\nkind = "handler"\nname = "{mod}.shout"\n'
    '[[nodes]]\nkind = "composition"\nname = "bundles/prep.toml"\n'
    '[[nodes]]\nkind = "handler"\nname = "{mod}.bang"\n'
    '[inputs]\ntext = {{ type = "str" }}\n'
    '[outputs]\nfinal = {{ type = "str" }}\n'
)

_PIPE_INLINE = (
    '[meta]\nname = "acme.embedded"\n'
    '[[nodes]]\nkind = "handler"\nname = "{mod}.shout"\n'
    '[[nodes]]\nkind = "handler"\nname = "{mod}.mark"\n'
    '[[nodes]]\nkind = "handler"\nname = "{mod}.wrap"\n'
    '[[nodes]]\nkind = "handler"\nname = "{mod}.bang"\n'
    '[inputs]\ntext = {{ type = "str" }}\n'
    '[outputs]\nfinal = {{ type = "str" }}\n'
)


def _registry(module_dir, mod_name="bundle_mod", *, embedded=True):
    """A four-transform chain text → shout → mark → wrap → bang → final; the middle two
    live in a bundle (embedded=True) or are written inline (embedded=False)."""
    _write_module(
        module_dir, mod_name,
        """
        def shout(*, text):
            return {"text_upper": text.upper()}

        def mark(*, text_upper):
            return {"marked": "<" + text_upper + ">"}

        def wrap(*, marked):
            return {"wrapped": "[" + marked + "]"}

        def bang(*, wrapped):
            return {"final": wrapped + "!"}
        """,
    )
    reg = DeclarationRegistry()
    for handler, r, w in (
        ("shout", "text", "text_upper"), ("mark", "text_upper", "marked"),
        ("wrap", "marked", "wrapped"), ("bang", "wrapped", "final"),
    ):
        reg.add_handler(
            f"{mod_name}.{handler}",
            loads(_TRANSFORM_TOML.format(read=r, write=w), "handler", file_path=f"{handler}.toml"),
            toml_path=f"{handler}.toml",
        )
    if embedded:
        reg.add_composition(
            "bundles/prep.toml",
            loads(_BUNDLE_TOML.format(mod=mod_name), "composition", file_path="prep.toml"),
        )
        pipeline = loads(_PIPE_EMBEDDED.format(mod=mod_name), "pipeline", file_path="p.toml")
    else:
        pipeline = loads(_PIPE_INLINE.format(mod=mod_name), "pipeline", file_path="p.toml")
    return reg, pipeline


# ---------------------------------------------------------------------------
# The bundle grammar — parse happy path + each closed-grammar violation
# ---------------------------------------------------------------------------


def test_bundle_toml_parses_to_the_bundle_ir():
    comp = loads(
        '[meta]\nkind = "bundle"\nname = "b"\n'
        '[[nodes]]\nkind = "handler"\nname = "acme.x"\n'
        '[annotations]\npurpose = "shared prep"\n',
        "composition", file_path="b.toml",
    )
    assert isinstance(comp, BundleComposition)
    assert comp.meta.name == "b"
    assert [n.name for n in comp.nodes] == ["acme.x"]
    assert comp.annotations == {"purpose": "shared prep"}


def test_bundle_rejects_a_boundary_section():
    """A bundle's channels continue the ENCLOSING scope by name — it declares no
    [inputs]/[outputs] boundary; the closed grammar rejects one loud."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nkind = "bundle"\nname = "b"\n'
            '[[nodes]]\nkind = "handler"\nname = "acme.x"\n'
            '[inputs]\ntext = { type = "str" }\n',
            "composition", file_path="b.toml",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "inputs" in exc.value.actual


def test_bundle_rejects_a_service_bindings_supply():
    """Post-substitution the ENCLOSING unit supplies service-binding identity for the
    bundle's nodes, exactly as for directly-declared nodes — a bundle-level supply
    block is outside the closed grammar."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nkind = "bundle"\nname = "b"\n'
            '[[nodes]]\nkind = "handler"\nname = "acme.x"\n'
            '[service_bindings.llm]\ntype = "acme.llm"\n',
            "composition", file_path="b.toml",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "service_bindings" in exc.value.actual


def test_bundle_requires_a_non_empty_nodes_sequence():
    """An empty bundle substitutes nothing — body-required, fail loud."""
    with pytest.raises(ContractViolation) as exc:
        loads('[meta]\nkind = "bundle"\nname = "b"\n', "composition", file_path="b.toml")
    assert exc.value.check is Check.BODY_REQUIRED


def test_bundle_meta_is_closed_to_kind_and_name():
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nkind = "bundle"\nname = "b"\ndescription = "x"\n'
            '[[nodes]]\nkind = "handler"\nname = "acme.x"\n',
            "composition", file_path="b.toml",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "description" in exc.value.actual


# ---------------------------------------------------------------------------
# The substitution path — composes + runs, dispatching the bundle's handlers inline
# ---------------------------------------------------------------------------


def test_bundle_embed_composes_and_dispatches_inline(module_dir):
    """Acceptance 1 (the substitution path): a pipeline embedding a bundle composes and
    RUNS, dispatching the bundle's handlers as if declared inline — there is no bundle
    node at runtime (a runner operation is not a node; the mirror principle)."""
    reg, pipeline = _registry(module_dir)
    graph = compile_pipeline(pipeline, reg, pipeline_name="acme.embedded")
    # The compiled graph holds the four flattened handlers — no composition node survives
    # substitution, and every node is a plain handler dispatch.
    assert len(graph.nodes) == 4
    runnable = assemble(graph, reg, None)
    result = run(runnable, {"text": "hi"})
    assert result.state["final"] == "[<HI>]!"


# ---------------------------------------------------------------------------
# The hash-identity invariant — the seal the kind-guard was pre-positioned for
# ---------------------------------------------------------------------------


# verifies: bundle-substitutes-before-scope-and-hash
def test_bundle_embedded_hashes_identically_to_inline(module_dir):
    """Acceptance 2 + 3 (the hash-identity invariant, RED-on-removal): a pipeline
    embedding a bundle hashes IDENTICALLY to the same pipeline with the bundle's nodes
    written inline — the bundle has no own hash domain; its content folds into the
    enclosing hash like directly-declared nodes (hash-model.md § What the pipeline-hash
    absorbs, the inlined pure-substitution contribution).

    RED if substitution is removed from ``pipeline_hash``'s entry: the composition node
    would reach the by-reference fold and the own-hash-domain kind-guard raises (never a
    silent by-reference fold — the guard's own test covers that arm); RED equally if a
    substitution bug reorders/drops nodes (the two hashes diverge)."""
    reg_e, pipe_e = _registry(module_dir, "hash_mod_e", embedded=True)
    reg_i, pipe_i = _registry(module_dir, "hash_mod_e", embedded=False)
    assert pipeline_hash(pipe_e, reg_e) == pipeline_hash(pipe_i, reg_i)


# verifies: bundle-substitutes-before-scope-and-hash
def test_every_bundle_spelling_of_one_pipeline_is_the_same_pipeline(module_dir):
    """The full equivalence class (the spec statement, user-ruled 2026-07-09): a bundle
    is PURELY ORGANIZATIONAL — functionally part of the parent pipeline. With bundle
    A = [mark, wrap] and bundle B = [crunch, bang], ALL FOUR spellings are one pipeline:

        [shout, bundle A, bundle B] == [shout, mark, wrap, bundle B]
        == [shout, bundle A, crunch, bang] == [shout, mark, wrap, crunch, bang]

    — one identical pipeline-hash across all four (never a different hash), identical
    compiled graphs, identical run output. RED if ANY spelling — including the MIXED
    ones — diverges on any of the three."""
    mod = "fourway_mod"
    _write_module(
        module_dir, mod,
        """
        def shout(*, text):
            return {"text_upper": text.upper()}

        def mark(*, text_upper):
            return {"marked": "<" + text_upper + ">"}

        def wrap(*, marked):
            return {"wrapped": "[" + marked + "]"}

        def crunch(*, wrapped):
            return {"crunched": "{" + wrapped + "}"}

        def bang(*, crunched):
            return {"final": crunched + "!"}
        """,
    )
    reg = DeclarationRegistry()
    for handler, r, w in (
        ("shout", "text", "text_upper"), ("mark", "text_upper", "marked"),
        ("wrap", "marked", "wrapped"), ("crunch", "wrapped", "crunched"),
        ("bang", "crunched", "final"),
    ):
        reg.add_handler(
            f"{mod}.{handler}",
            loads(_TRANSFORM_TOML.format(read=r, write=w), "handler", file_path=f"{handler}.toml"),
            toml_path=f"{handler}.toml",
        )
    for path, name, members in (
        ("bundles/a.toml", "bundle_a", ("mark", "wrap")),
        ("bundles/b.toml", "bundle_b", ("crunch", "bang")),
    ):
        body = "".join(
            f'[[nodes]]\nkind = "handler"\nname = "{mod}.{m}"\n' for m in members
        )
        reg.add_composition(
            path,
            loads(f'[meta]\nkind = "bundle"\nname = "{name}"\n{body}',
                  "composition", file_path=path),
        )

    def spelling(*entries):
        nodes = "".join(
            f'[[nodes]]\nkind = "composition"\nname = "{e}"\n' if e.endswith(".toml")
            else f'[[nodes]]\nkind = "handler"\nname = "{mod}.{e}"\n'
            for e in entries
        )
        return loads(
            f'[meta]\nname = "acme.fourway"\n{nodes}'
            '[inputs]\ntext = { type = "str" }\n'
            '[outputs]\nfinal = { type = "str" }\n',
            "pipeline", file_path="p.toml",
        )

    spellings = [
        spelling("shout", "bundles/a.toml", "bundles/b.toml"),
        spelling("shout", "mark", "wrap", "bundles/b.toml"),
        spelling("shout", "bundles/a.toml", "crunch", "bang"),
        spelling("shout", "mark", "wrap", "crunch", "bang"),
    ]
    hashes = {pipeline_hash(p, reg) for p in spellings}
    assert len(hashes) == 1  # one pipeline, one hash — never a different hash

    results = set()
    node_shapes = set()
    for p in spellings:
        graph = compile_pipeline(p, reg, pipeline_name="acme.fourway")
        node_shapes.add(tuple((n.qualified_name, n.position) for n in graph.nodes))
        runnable = assemble(graph, reg, None)
        results.add(run(runnable, {"text": "hi"}).state["final"])
    assert node_shapes == {(
        (f"{mod}.shout", 0), (f"{mod}.mark", 1), (f"{mod}.wrap", 2),
        (f"{mod}.crunch", 3), (f"{mod}.bang", 4),
    )}
    assert results == {"{[<HI>]}!"}


def test_nested_bundle_substitutes_recursively(module_dir):
    """A bundle embedding a bundle inlines fully — one mechanism, applied at whichever
    layer the embed sits (the mirror principle's recursive contribution)."""
    mod = "nested_bundle_mod"
    reg, _ = _registry(module_dir, mod, embedded=True)
    # An outer bundle that embeds the inner bundle between two of its own references.
    reg.add_composition(
        "bundles/outer.toml",
        loads(
            '[meta]\nkind = "bundle"\nname = "outer_prep"\n'
            '[[nodes]]\nkind = "composition"\nname = "bundles/prep.toml"\n',
            "composition", file_path="outer.toml",
        ),
    )
    pipeline = loads(
        '[meta]\nname = "acme.embedded"\n'
        f'[[nodes]]\nkind = "handler"\nname = "{mod}.shout"\n'
        '[[nodes]]\nkind = "composition"\nname = "bundles/outer.toml"\n'
        f'[[nodes]]\nkind = "handler"\nname = "{mod}.bang"\n'
        '[inputs]\ntext = { type = "str" }\n'
        '[outputs]\nfinal = { type = "str" }\n',
        "pipeline", file_path="p2.toml",
    )
    graph = compile_pipeline(pipeline, reg, pipeline_name="acme.embedded")
    assert len(graph.nodes) == 4  # both bundle layers dissolved
    # And the two-level embed hashes identically to the flat inline chain.
    reg_i, pipe_i = _registry(module_dir, mod, embedded=False)
    assert pipeline_hash(pipeline, reg) == pipeline_hash(pipe_i, reg_i)


def test_bundle_self_embed_cycle_is_rejected_at_compose(module_dir):
    """Acceptance 4 (fail loud): a bundle transitively embedding itself is the only
    non-terminating case — rejected as a structured ContractViolation when the embed
    graph is resolved at compose, before any node dispatches."""
    reg, _ = _registry(module_dir, "cycle_mod", embedded=True)
    reg.add_composition(
        "bundles/a.toml",
        loads(
            '[meta]\nkind = "bundle"\nname = "a"\n'
            '[[nodes]]\nkind = "composition"\nname = "bundles/b.toml"\n',
            "composition", file_path="a.toml",
        ),
    )
    reg.add_composition(
        "bundles/b.toml",
        loads(
            '[meta]\nkind = "bundle"\nname = "b"\n'
            '[[nodes]]\nkind = "composition"\nname = "bundles/a.toml"\n',
            "composition", file_path="b.toml",
        ),
    )
    pipeline = loads(
        '[meta]\nname = "acme.cyclic"\n'
        '[[nodes]]\nkind = "composition"\nname = "bundles/a.toml"\n'
        '[inputs]\ntext = { type = "str" }\n',
        "pipeline", file_path="pc.toml",
    )
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name="acme.cyclic")
    assert exc.value.check is Check.COMPOSITION_CYCLE
    assert "bundles/a.toml" in str(exc.value)


def test_post_substitute_graph_faults_are_diagnosed_by_the_enclosing_unit(module_dir):
    """A bundle's channels continue the enclosing scope: a bundle node reading a channel
    nothing upstream writes is diagnosed on the POST-substitute graph by the enclosing
    pipeline's own compose-time validation — exactly as for directly-declared nodes."""
    mod = "fault_mod"
    reg, _ = _registry(module_dir, mod, embedded=True)
    # A pipeline that embeds the bundle WITHOUT the upstream `shout` writer: the bundle's
    # first node reads `text_upper`, which nothing writes and no [inputs] declares.
    pipeline = loads(
        '[meta]\nname = "acme.broken"\n'
        '[[nodes]]\nkind = "composition"\nname = "bundles/prep.toml"\n'
        f'[[nodes]]\nkind = "handler"\nname = "{mod}.bang"\n'
        '[inputs]\ntext = { type = "str" }\n'
        '[outputs]\nfinal = { type = "str" }\n',
        "pipeline", file_path="pb.toml",
    )
    with pytest.raises((ContractViolation, ContractViolationGroup)) as exc:
        compile_pipeline(pipeline, reg, pipeline_name="acme.broken")
    # The precise check is the enclosing graph's own closure diagnostics (aggregated
    # within the group when several fire) — the point is that a structured compose-time
    # violation names the dangling read, exactly as for directly-declared nodes.
    violations = getattr(exc.value, "violations", (exc.value,))
    assert any(
        v.check in (Check.DANGLING_IDENTITY_PORT, Check.READ_PORT_UNCLOSED)
        for v in violations
    )
