"""Validator floor-fix regression unit — one test per cleared fix + one per invariant proof.

Grounded in canon (each docstring cites the anchor the fix/invariant derives from). The
per-check negative-coverage table (``test_negative.py``) already covers the
``NAME_UNIQUENESS`` discriminator via ``case_name_uniqueness`` (the composition-meta.name path);
this file adds the preprocessor-name sub-check, the remaining fixes that are not new
``Check`` members, and the invariant proofs.
"""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation
from conjured.hasher import pipeline_hash, training_bundle_hash
from conjured.canonical import canon_value
from conjured.ir.channel_types import LiteralType, TupleType, optional, list_of, primitive
from conjured.validator import DeclarationRegistry, compile_pipeline, loads
from conjured.validator.compile import _strategy_accepts
from conjured.validator.normalize import desugar_map
from conjured.ir.common import MergeStrategy

from . import fixtures as F


# ===========================================================================
# Part A — clear fixes
# ===========================================================================


# --- C1: pipeline present-but-empty [outputs] → BODY_REQUIRED ------------------------------
# Canon: pipeline/reference.md § inputs/outputs — "An empty closed-shape key (body omitted) is
# an exhaustive-declaration violation; `outputs` is truly optional — omit it to opt out".
# architecture/exhaustive-declaration.md § section-discipline modes (required, body-required).


def test_c1_pipeline_empty_outputs_raises_body_required():
    reg = DeclarationRegistry()
    reg.add_handler("acme.n", loads(
        '[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}', "handler", file_path="n.toml"))
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.n"\n[inputs]\ni={type="str"}\n[outputs]\n',
            "pipeline", file_path="p.toml")
    assert exc.value.check is Check.BODY_REQUIRED
    assert exc.value.section_path == "outputs"


def test_c1_absent_outputs_still_opts_out():
    """The other side of the categorical distinction: ABSENT [outputs] is truly-optional (opts
    out), not a violation — outputs stays None."""
    pipe = loads(
        '[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.n"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    assert pipe.outputs is None


def test_c1_present_nonempty_outputs_parses():
    pipe = loads(
        '[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.n"\n[inputs]\ni={type="str"}\n[outputs]\no={type="str"}\n',
        "pipeline", file_path="p.toml")
    assert [f.name for f in pipe.outputs] == ["o"]


# --- D3: non-boolean `nullable` field-metadata value raises ------------------------------
# Canon: handler/reference.md § per-field metadata keys — "nullable — boolean; defaults false".
# reference/principles.md I1 — silent fallbacks forbidden as a category.


@pytest.mark.parametrize("bad", ['"yes"', "1", "0", '"true"'])
def test_d3_non_boolean_nullable_raises(bad):
    with pytest.raises(ContractViolation) as exc:
        loads(
            f'[transform]\n[reads]\nx={{type="str", nullable={bad}}}\n[output_schema]\no={{type="str"}}',
            "handler", file_path="x.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.section_path == "reads.x"


def test_d3_boolean_nullable_still_normalizes():
    """nullable = true still normalizes to OptionalType (the fix does not break the valid path)."""
    h = loads(
        '[transform]\n[reads]\nx={type="str", nullable=true}\n[output_schema]\no={type="str"}',
        "handler", file_path="x.toml")
    from conjured.ir.channel_types import OptionalType
    assert isinstance(h.reads[0].type, OptionalType)


# --- U1: empty tuple[] / Literal[] are unrepresentable (min_length=1) ----------------------
# Canon: handler/reference.md § Types allowed — tuple = N declared element types; Literal =
# closed-enum values. The descriptors' own docstrings promise "one or more".


def test_u1_empty_tuple_descriptor_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TupleType(items=())


def test_u1_empty_literal_descriptor_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        LiteralType(values=())


def test_u1_nonempty_tuple_and_literal_still_build():
    assert TupleType(items=(primitive("str"),)).items
    assert LiteralType(values=("a",)).values


def test_u1_nested_fields_left_unconstrained():
    """U1 deliberately does NOT constrain NestedType.fields (the deferred output_schema ≥ 1
    twin — left to Phase-1a declaration-cardinality). An empty nested object still constructs."""
    from conjured.ir.channel_types import NestedType
    assert NestedType(fields=()).fields == ()


# --- gap-1b: preprocessor-name uniqueness within a composition -----------------------------
# Canon: hash-model.md § Manifest-key shape (unique within the embedding pipeline's namespace);
# composition.py IR — preprocessor name "unique in this composition" (<meta.name>.<name>).
# (The composition-meta.name path is covered by test_negative.case_name_uniqueness.)


def test_gap1_duplicate_preprocessor_name_raises():
    # Two preprocessors share the id "p" → their <meta.name>.<id> qualified names collide.
    comp_toml = (
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n'
        '[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="transform.a"\nid="p"\n'
        'writes_map={out="mid"}\n'
        '[[preprocessors]]\nkind="handler"\nname="transform.b"\nid="p"\n'
        'reads_map={inp="mid"}\nwrites_map={out2="formatted_prompt"}\n'
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="qwen"\n'
        '[trainable]\n[trainable.config]\ntemperature=0.7\nmax_tokens=64\n'
        '[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nformatted_prompt={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    # The preprocessors are name-references — register the handlers they resolve (the first
    # entry flattens cleanly; the second collides on its id before resolution → NAME_UNIQUENESS).
    reg.add_handler("transform.a", loads('[transform]\n[reads]\nnpc_state={type="str"}\n[output_schema]\nout={type="str"}\n', "handler", file_path="a.toml"))
    reg.add_handler("transform.b", loads('[transform]\n[reads]\ninp={type="str"}\n[output_schema]\nout2={type="str"}\n', "handler", file_path="b.toml"))
    reg.add_composition("trainables/dt.toml", loads(comp_toml, "composition", file_path="c.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="composition"\nname="trainables/dt.toml"\n'
        '[inputs]\nnpc_state={type="str"}\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.NAME_UNIQUENESS


# --- gap-2 (superseded by the bundle embed-form): the bundle GRAMMAR is the chokepoint -----
# Canon: glossary § Bundle TOML (the minimal grammar — [meta] {kind,name} + [[nodes]] +
# optional [annotations]); the old parse-time BUNDLE_REACHES_BYREF_FOLD rejection retired when
# the embed-form landed. The closed grammar still rejects a boundary-bearing "bundle"
# (the exact TOML the old gap-2 fence pinned) — now as the grammar violation it is.


def test_gap2_bundle_with_a_boundary_is_rejected_by_the_closed_grammar():
    with pytest.raises(ContractViolation) as exc:
        loads('[meta]\nkind="bundle"\nname="b"\n[inputs]\n[outputs]\nx={type="str"}', "composition", file_path="b.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "inputs" in exc.value.actual or "outputs" in exc.value.actual


# --- gap-3: un-canonicalizable inline CompileBinding.params → ContractViolation -------------
# Canon: components/error-channel/reference.md (ContractViolation is the structural fail-loud
# surface); mirrors the FilePathBindingValue → EXTERNAL_BINDING_UNSUPPORTED posture. Compile-
# directive params ARE a hash input (the inline value folds; a file-supplied param folds the
# file's text — handler/reference.md § The `compile = "..."` directive sub-form); this case is the
# inline branch's fail-loud guard for a non-canonicalizable value.


def test_gap3_uncanonicalizable_compile_param_raises_contract_violation():
    # A compile-directive binding whose params carry a non-JSON-native value (a set) — the hasher
    # must raise ContractViolation, not let canon_value's bare TypeError escape.
    reg = DeclarationRegistry()
    reg.add_handler("acme.h", loads(
        '[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n'
        '[bindings.cfg]\ncompile="regex"\npattern="x+"',
        "handler", file_path="h.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.h"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    # Inject a non-canonicalizable params value on the resolved declaration (bypasses TOML, which
    # cannot express a set — this is the programmatic path the guard defends).
    decl = reg.get_handler("acme.h")
    b0 = decl.bindings[0]
    poisoned_body = b0.body.model_copy(update={"params": {"bad": {1, 2, 3}}})
    poisoned = b0.model_copy(update={"body": poisoned_body})
    reg.add_handler("acme.h", decl.model_copy(update={"bindings": (poisoned,)}))
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipeline, reg)
    assert exc.value.check is Check.MALFORMED_DECLARATION


def test_gap3_canon_value_still_raises_typeerror_at_its_own_boundary():
    """The low-level canon_value contract is unchanged (it raises TypeError on a non-serializable
    value); gap-3 only adds the ContractViolation translation at the binding boundary above it."""
    with pytest.raises(TypeError):
        canon_value({1, 2, 3})


# ===========================================================================
# Part B — verifications (each asserts the invariant; all HELD)
# ===========================================================================


def _two_node_pipeline(order):
    reg = DeclarationRegistry()
    reg.add_handler("acme.a", loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\nx={type="str"}', "handler", file_path="a.toml"))
    reg.add_handler("acme.b", loads('[transform]\n[reads]\nx={type="str"}\n[output_schema]\ny={type="str"}', "handler", file_path="b.toml"))
    names = ("acme.a", "acme.b") if order == "ab" else ("acme.b", "acme.a")
    toml = (
        '[meta]\nname="p"\n'
        f'[[nodes]]\nkind="handler"\nname="{names[0]}"\n'
        f'[[nodes]]\nkind="handler"\nname="{names[1]}"\n'
        '[inputs]\ni={type="str"}\nx={type="str"}\n'
    )
    return reg, loads(toml, "pipeline", file_path="p.toml")


def test_gap6_position_as_identity_hashes_distinctly():
    """Canon: handler_position is identity (hash-model.md). Two pipelines differing only by node
    position hash distinctly — node order is absorbed by the pipeline-hash."""
    reg_ab, p_ab = _two_node_pipeline("ab")
    reg_ba, p_ba = _two_node_pipeline("ba")
    assert pipeline_hash(p_ab, reg_ab) != pipeline_hash(p_ba, reg_ba)


def test_gap7_canonical_key_order_determinism_identity_values():
    """Canon: conjured/canonical.py — every Mapping the structure-builders emit normalizes via
    sort_keys. service_bindings identity values folded into the pipeline-hash are key-order-neutral."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(
        'name="st.x"\n[identity_schema]\na={type="str"}\nb={type="str"}\n[transport_schema]\ne={type="str"}\n[config_schema]\nt={type="float"}',
        "service_type", file_path="st.toml"))
    reg.add_handler("acme.s", loads(
        '[service]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n[service_bindings]\nllm={type="st.x"}',
        "handler", file_path="s.toml"))
    p1 = loads('[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.s"\n[service_bindings.llm]\ntype="st.x"\na="1"\nb="2"\n[service_bindings.llm.config]\nt=0.5\n[inputs]\ni={type="str"}\n', "pipeline", file_path="p.toml")
    p2 = loads('[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.s"\n[service_bindings.llm]\ntype="st.x"\nb="2"\na="1"\n[service_bindings.llm.config]\nt=0.5\n[inputs]\ni={type="str"}\n', "pipeline", file_path="p.toml")
    assert pipeline_hash(p1, reg) == pipeline_hash(p2, reg)


def test_gap7_canon_value_nested_mapping_key_order_neutral():
    """canon_value renders nested mappings as structures; sort_keys at serialization makes any
    nested-key reordering hash-neutral (the algorithm-level guarantee)."""
    from conjured.canonical import sha256_of
    a = canon_value({"x": {"p": 1, "q": 2}, "y": 3})
    b = canon_value({"y": 3, "x": {"q": 2, "p": 1}})
    assert sha256_of(a) == sha256_of(b)


def test_gap8_desugar_byte_identity_compiler_and_hasher():
    """Canon: validator/normalize.py — the compiler and hasher call the SAME desugar_map for
    every node kind. Confirm the hasher's normalized preprocessor maps equal the compiler's."""
    from conjured.hasher.hashes import _canon_preprocessor
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    pp = comp.preprocessors[0]
    decl = reg.get_handler(pp.name)
    cname = comp.meta.name

    def _unscope(ch):
        return ch[len(cname) + 1:] if ch.startswith(cname + ".") else ch

    # The REAL hasher path (`_canon_preprocessor`) emits normalized maps via the shared
    # `desugar_map` over the REFERENCED handler's declared ports.
    canon = _canon_preprocessor(pp, reg)
    # The REAL compiler path (`_flatten_trainable`) scopes those same desugared maps onto the
    # composition's channels; unscope to recover the shared normalization both layers compute.
    graph = compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    pre = next(n for n in graph.nodes if getattr(n, "member_name", None) == pp.id)
    assert canon["reads_map"] == {k: _unscope(v) for k, v in pre.read_map.items()}
    assert canon["writes_map"] == {k: _unscope(v) for k, v in pre.write_map.items()}
    # ...and both equal the desugar over the referenced handler's declared ports (RED if either
    # layer desugared over a different/wrong port source — the byte-identity property under test).
    assert canon["reads_map"] == desugar_map(pp.reads_map, [f.name for f in decl.reads])
    assert canon["writes_map"] == desugar_map(pp.writes_map, [f.name for f in decl.output_schema])


def test_gap9_default_key_surfaces_clean_closed_grammar():
    """Canon: I1 forbids a per-field `default` (handler/reference.md § Types allowed — "There is
    no per-field default key"). The `default` key surfaces a clean CLOSED_GRAMMAR diagnostic,
    not a generic MALFORMED_DECLARATION."""
    with pytest.raises(ContractViolation) as exc:
        loads('[transform]\n[reads]\nx={type="str", default="hi"}\n[output_schema]\no={type="str"}', "handler", file_path="x.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.section_path == "reads.x"


def test_gap12_pipeline_hash_excludes_meta_entirely():
    """Canon: the family rule applied to the top-level unit — pipeline_hash excludes pipeline.meta
    entirely; renaming meta.name is hash-neutral (hash-model.md § What is explicitly NOT). The
    `[meta]` block is now closed to `{name}` (no declaration-level `description` to toggle —
    prose lives in a TOML comment), so `name` is the only meta axis, and it is excluded."""
    reg, pipeline, _ = F.build_base()
    before = pipeline_hash(pipeline, reg)
    renamed = pipeline.model_copy(update={"meta": pipeline.meta.model_copy(update={"name": "x.y"})})
    assert pipeline_hash(renamed, reg) == before


def test_gap11_duplicate_pipeline_override_block_is_unrepresentable_in_toml():
    """gap-11 outcome — HELD by construction. A duplicate `pipelines.<name>` override is
    structurally impossible in authored TOML: a true duplicate table is a TOML parse error
    (→ ContractViolation), and two override blocks for one pipeline name merge into a single
    table (one PipelineOverride). So R-deployment-002's "resolution is deterministic, not by load
    order" is satisfied structurally — TOML table-key uniqueness — and canon is silent on the
    duplicate-block case because it cannot arise. The first-wins `next(...)` is benign: the IR
    can never carry two overrides with the same qualified name from any authored declaration."""
    # (a) a true duplicate table → ContractViolation at parse (TOMLDecodeError translated).
    with pytest.raises(ContractViolation):
        loads(
            '[training_contract]\nintegrity_enforcement=true\n'
            '[pipelines."acme.dialogue".transport.llm]\nendpoint="a"\n'
            '[pipelines."acme.dialogue".transport.llm]\nendpoint="b"\n',
            "deployment", file_path="d.toml")
    # (b) two override sub-tables for one pipeline name merge → exactly one PipelineOverride.
    dep = loads(
        '[training_contract]\nintegrity_enforcement=true\n'
        '[pipelines."acme.dialogue".transport.llm]\nendpoint="a"\n'
        '[pipelines."acme.dialogue".hook_transport."acme.log"]\npath="/x"\n',
        "deployment", file_path="d.toml")
    matching = [o for o in dep.pipelines if o.pipeline_qualified_name == "acme.dialogue"]
    assert len(matching) == 1


def test_merge_rejects_optional_base_type():
    """gap-10 RESOLVED (constrain, 2026-06-07): merge requires a non-optional base type —
    `_strategy_accepts` rejects an `Optional[...]` channel for EVERY strategy; nullable-channel
    fan-in is the aggregator's territory (pipeline/reference.md § merge.<channel>)."""
    opt_list = optional(list_of(primitive("str")))
    assert _strategy_accepts(MergeStrategy.APPEND_LIST, opt_list) is False
    # The constraint is blanket — even an identity strategy rejects an Optional channel:
    assert _strategy_accepts(MergeStrategy.LAST_WINS, opt_list) is False
    # The non-optional baseline (unchanged):
    assert _strategy_accepts(MergeStrategy.APPEND_LIST, list_of(primitive("str"))) is True
    assert _strategy_accepts(MergeStrategy.APPEND_LIST, primitive("str")) is False
