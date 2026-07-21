"""Binding/declaration-model completion — tests for the floor-completion pass.

Covers the decided binding/composition-model designs implemented additive to the floor
(grounded in the binding/composition-model canon). One section per sequenced step:

1. Quick correctness — `streamable` out of the TBH; the `_parse_preprocessors` closed-grammar
   guard; the inner `[meta]` closed-grammar checks (pipeline `{name}`; composition
   `{kind, name}` — the family rule; a declaration-level `description` is not admitted).
2. Divergence C — a hook preprocessor's `transport_schema` parses (kind inferred structurally).
3. Divergence A — a composition supplies its own service-binding identity; it folds into the
   TBH; coverage / placement / orphan / type checks fire.
4. Inline-scalar grammar — a bare string is inline content; `{ file = "..." }` is the external
   form.
5. Ship-time defaults — a default-bearing binding MAY be omitted; the declared default + the
   effective value fold into the hashes. External-file content — resolved + canonicalized +
   hashed (inline X and a file containing X hash identically).
"""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation
from conjured.hasher import pipeline_hash, training_bundle_hash
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

from . import fixtures as F


# ===========================================================================
# Step 1 — quick correctness fixes
# ===========================================================================


def test_streamable_excluded_from_training_bundle_hash():
    """`streamable` is a delivery selector, not training-record shape — toggling it on the
    [trainable] node leaves the training-bundle-hash unchanged (hash-model.md
    § Training-bundle-hash; trainable.schema.toml)."""
    reg, _ = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    base = training_bundle_hash(comp, reg)
    streamed = comp.model_copy(
        update={"trainable": comp.trainable.model_copy(update={"streamable": True})}
    )
    assert training_bundle_hash(streamed, reg) == base


def test_preprocessor_unknown_key_raises_closed_grammar():
    """`_parse_preprocessors` previously let any unknown preprocessor key pass silently (a real
    bug, REPORT § 1.C). An unknown key now raises CLOSED_GRAMMAR."""
    comp_toml = (
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="transform.a"\nid="p"\nretry_policy="aggressive"\n'  # unknown key
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="qwen"\n'
        '[trainable]\n[trainable.config]\n[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nformatted_prompt={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )
    with pytest.raises(ContractViolation) as exc:
        loads(comp_toml, "composition", file_path="c.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR


def test_pipeline_meta_unknown_inner_key_raises_closed_grammar():
    """A pipeline [meta] block declares only {name}; an unknown inner key raises
    CLOSED_GRAMMAR (1c — inner-grammar gap, previously absorbed silently)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nname="p"\nversion="3"\n[[nodes]]\nkind="handler"\nname="acme.x"\n',
            "pipeline", file_path="p.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "meta" in exc.value.expected and "version" in exc.value.actual


def test_composition_meta_unknown_inner_key_raises_closed_grammar():
    """A composition [meta] block declares only {kind, name}; an unknown inner key
    raises CLOSED_GRAMMAR (1c)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nkind="trainable"\nname="c"\nflavor="x"\n[inputs]\n[outputs]\nx={type="str"}',
            "composition", file_path="c.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "meta" in exc.value.expected and "flavor" in exc.value.actual


def test_pipeline_meta_name_only_parses():
    """The valid path: a pipeline [meta] carries just {name} (the family rule closes it —
    pipeline/reference.md § `meta`). The name parses; the block has no `description`."""
    pipe = loads(
        '[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.x"\n',
        "pipeline", file_path="p.toml")
    assert pipe.meta.name == "p"
    assert not hasattr(pipe.meta, "description")  # the field is gone, not just unset


def test_pipeline_meta_description_is_rejected():
    """A declaration-level `description` on a pipeline [meta] is no longer admitted (the family
    rule — prose lives in a TOML comment); it raises CLOSED_GRAMMAR, not silently absorbed."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nname="p"\ndescription="d"\n[[nodes]]\nkind="handler"\nname="acme.x"\n',
            "pipeline", file_path="p.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "description" in exc.value.actual


# ===========================================================================
# Field-level `description` admission (AC2) — model-facing contract content admitted
# ONLY on a trainable's `trainable.output_schema` fields (incl. nested), on a wire that
# delivers it; rejected at every other field position with a remediation naming
# `[annotations]` (handler/reference.md § description-admission; the family rule).
# ===========================================================================


def _trainable_with_output_field(output_field_toml: str) -> str:
    return (
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="qwen"\n'
        '[trainable]\n[trainable.config]\n[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nformatted_prompt={type="str"}\n'
        f'[trainable.output_schema]\n{output_field_toml}\n'
    )


def test_description_admitted_on_trainable_output_field():
    """The ONE admitted position: a `description` on a trainable `trainable.output_schema` field
    parses and reaches the FieldDecl (it folds into both hashes; the bound wire delivers it)."""
    comp = loads(
        _trainable_with_output_field('dialogue_response = { type = "str", description = "The NPC line." }'),
        "composition", file_path="c.toml")
    out = {f.name: f for f in comp.trainable.output_schema}
    assert out["dialogue_response"].description == "The NPC line."


def test_description_admitted_on_nested_trainable_output_member():
    """Admission propagates into nested members (handler/reference.md § description-admission:
    "incl. nested members") — a described field inside a nested object on a trainable output
    schema parses too."""
    comp = loads(
        _trainable_with_output_field(
            'dialogue_response = { type = "str" }\n'
            '[trainable.output_schema.mood.fields]\n'
            'intensity = { type = "int", description = "0..10 strength." }'),
        "composition", file_path="c.toml")
    out = {f.name: f for f in comp.trainable.output_schema}
    members = {m.name: m for m in out["mood"].type.fields}
    assert members["intensity"].description == "0..10 strength."


@pytest.mark.parametrize(
    ("kind_toml", "described_section"),
    [
        # a transform `reads` field
        ('[transform]\n[reads]\na = { type = "str", description = "x" }\n[output_schema]\no = { type = "str" }\n', "reads.a"),
        # a transform `output_schema` field (NOT a trainable's — not admitted)
        ('[transform]\n[reads]\na = { type = "str" }\n[output_schema]\no = { type = "str", description = "x" }\n', "output_schema.o"),
        # a `bindings.<name>` field
        ('[transform]\n[reads]\na = { type = "str" }\n[output_schema]\no = { type = "str" }\n[bindings.cfg]\nk = { type = "str", description = "x" }\n', "bindings.cfg.k"),
    ],
)
def test_description_rejected_at_non_admitted_handler_positions(kind_toml, described_section):
    """AC2 — `description` at a non-admitted field position raises CLOSED_GRAMMAR at load, with a
    remediation naming `[annotations]` (defends against the key surviving where no model sees it).
    Covers a transform `reads` field, a non-trainable `output_schema` field, and a `bindings`
    field — the reject is position-gated, not keyword-specific."""
    with pytest.raises(ContractViolation) as exc:
        loads(kind_toml, "handler", file_path="h.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "description" in exc.value.actual
    assert exc.value.section_path == described_section
    assert "annotations" in exc.value.remediation_hint


def test_description_rejected_on_pipeline_boundary_field():
    """A pipeline `[inputs]`/`[outputs]` boundary field admits no `description` (the pipeline
    grammar declares no `[annotations]`, so its field prose lives in TOML comments — the
    remediation says so)."""
    with pytest.raises(ContractViolation) as exc:
        loads('[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.x"\n'
              '[inputs]\ni = { type = "str", description = "x" }\n',
              "pipeline", file_path="p.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "description" in exc.value.actual


def test_description_rejected_on_service_type_schema_field():
    """A service-type schema field (identity/transport/config) admits no `description` — only the
    service-type declaration's own TOP-LEVEL `description` stays (a distinct, still-admitted key:
    generation-time generator-instruction context, provenance-pinned via the manifest's
    `generator_info.derivables_bundle_hash`, outside both structural hashes)."""
    with pytest.raises(ContractViolation) as exc:
        loads('name="s"\n[identity_schema]\nm = { type = "str", description = "x" }\n'
              '[transport_schema]\ne = { type = "str" }\n[config_schema]\n',
              "service_type", file_path="s.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "description" in exc.value.actual


# ===========================================================================
# Step 2 — divergence C (hook preprocessor transport_schema)
# ===========================================================================


def test_hook_preprocessor_is_a_name_reference():
    """A hook preprocessor is a NAME-REFERENCE to a registered hook handler (the mirror-pipeline
    principle): the [[preprocessors]] entry carries only {kind, name, id, maps, bindings}; the
    ports AND the transport_schema (divergence C) are owned by the referenced hook handler
    declaration, resolved via `name` — never inlined on the entry."""
    comp_toml = (
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="hook.audit"\nid="audit"\n'
        'reads_map={observed="npc_state"}\n'
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="qwen"\n'
        '[trainable]\n[trainable.config]\n[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nnpc_state={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )
    comp = loads(comp_toml, "composition", file_path="c.toml")
    audit = comp.preprocessors[0]
    assert audit.name == "hook.audit" and audit.id == "audit"
    assert not hasattr(audit, "transport_schema")  # owned by the referenced handler, not the entry
    # The transport_schema (divergence C) lives on the referenced hook handler declaration.
    hook = loads(F.HOOK_AUDIT, "handler", file_path="h.toml")
    assert [f.name for f in hook.transport_schema] == ["log_path"]


def test_service_preprocessor_multiple_bindings_raises_cardinality():
    """A service-kind preprocessor is a name-reference to a service handler; the referenced
    declaration declares EXACTLY ONE service-typed binding (R-handler-008 — the SAME cardinality
    the top-level service handler enforces, now through the shared `_check_handler_cardinality`
    over the resolved declaration, the mirror-pipeline principle). A referenced service handler
    with two bindings → ContractViolation at flatten."""
    service_two = (
        '[service]\n[reads]\nutterance={type="str"}\n[output_schema]\nenriched={type="str"}\n'
        '[service_bindings]\nsvc_a={type="conjured_llm.dialogue"}\nsvc_b={type="conjured_llm.dialogue"}\n'  # TWO → R-handler-008
    )
    comp_toml = (
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nutterance={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="service.enrich"\nid="enrich"\n'
        'writes_map={enriched="enriched"}\n'
        '[service_bindings.svc_a]\ntype="conjured_llm.dialogue"\nmodel="m"\n'
        '[service_bindings.svc_a.config]\ntemperature=0.7\nmax_tokens=64\n'
        '[service_bindings.svc_b]\ntype="conjured_llm.dialogue"\nmodel="m"\n'
        '[service_bindings.svc_b.config]\ntemperature=0.7\nmax_tokens=64\n'
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="m"\n'
        '[trainable]\n[trainable.config]\ntemperature=0.7\nmax_tokens=64\n'
        '[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nenriched={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("service.enrich", loads(service_two, "handler", file_path="se.toml"))
    reg.add_composition("c.toml", loads(comp_toml, "composition", file_path="c.toml"))
    pipeline = loads(
        '[meta]\nname="p"\n[[nodes]]\nkind="composition"\nname="c.toml"\n'
        '[inputs]\nutterance={type="str"}\n[outputs]\ndialogue_response={type="str"}\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name="p", file_path="p.toml")
    assert exc.value.check is Check.SERVICE_BINDING_CARDINALITY
    assert exc.value.rule_id == "R-handler-008"
    assert "enrich" in exc.value.actual


def _trainable_with_hook_preproc() -> str:
    """A trainable composition with a hook preprocessor — a name-reference to the registered
    `hook.audit` hook handler (which owns the `observed` read + the `log_path` transport_schema,
    divergence C). Callers register `F.HOOK_AUDIT` as `hook.audit`."""
    return (
        '[meta]\nkind = "trainable"\nname = "dialogue_training"\n'
        '[inputs]\nnpc_state = { type = "str" }\nuser_message = { type = "str" }\n'
        '[outputs]\ndialogue_response = { type = "str" }\n'
        '[[preprocessors]]\nkind = "handler"\nname = "hook.audit"\nid = "audit"\n'
        'reads_map = { observed = "npc_state" }\n'
        '[service_bindings.llm]\ntype = "conjured_llm.dialogue"\nmodel = "qwen"\n'
        '[trainable]\n[trainable.config]\ntemperature = 0.7\nmax_tokens = 512\n'
        '[trainable.service_bindings]\nllm = { type = "conjured_llm.dialogue" }\n'
        '[trainable.reads]\nnpc_state = { type = "str" }\nuser_message = { type = "str" }\n'
        '[trainable.output_schema]\ndialogue_response = { type = "str" }\n'
    )


def test_composition_hook_transport_coverage_gap_fires():
    """A flattened composition hook preprocessor needs a covering hook_transport.<comp>.<hook>
    block — a missing one raises HOOK_TRANSPORT_COVERAGE (the deployment-coverage walk now
    reaches composition-internal hooks, divergence C)."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("acme.ctx", loads(F.TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg.add_handler("hook.audit", loads(F.HOOK_AUDIT, "handler", file_path="ha.toml"))
    reg.add_composition("trainables/dialogue.toml",
                        loads(_trainable_with_hook_preproc(), "composition", file_path="c.toml"))
    pipeline = loads(F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path="p.toml")
    # Deployment covers transport.llm but omits the composition hook's hook_transport block.
    deployment = loads(
        '[transport.llm]\nendpoint="https://x"\n[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")
    assert exc.value.check is Check.HOOK_TRANSPORT_COVERAGE
    assert "dialogue_training.audit" in exc.value.expected


def test_composition_hook_transport_coverage_satisfied():
    """With the composition hook's hook_transport block present and complete, coverage passes."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("acme.ctx", loads(F.TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg.add_handler("hook.audit", loads(F.HOOK_AUDIT, "handler", file_path="ha.toml"))
    reg.add_composition("trainables/dialogue.toml",
                        loads(_trainable_with_hook_preproc(), "composition", file_path="c.toml"))
    pipeline = loads(F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path="p.toml")
    deployment = loads(
        '[transport.llm]\nendpoint="https://x"\n'
        '[hook_transport."dialogue_training.audit"]\nlog_path="/var/log/audit.jsonl"\n'
        '[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")


def test_hook_preprocessor_excluded_from_tbh():
    """A hook preprocessor contributes to neither hash — adding one (a name-reference to the
    registered `hook.audit` hook handler) leaves the training-bundle-hash unchanged (its hook
    kind is resolved from the referenced declaration, then dropped from the TBH)."""
    reg, _ = F.build_trainable()
    reg.add_handler("hook.audit", loads(F.HOOK_AUDIT, "handler", file_path="ha.toml"))
    comp = reg.get_composition("trainables/dialogue.toml")
    base = training_bundle_hash(comp, reg)
    from conjured.ir.composition import PreprocessorEntry
    hook_pp = PreprocessorEntry(name="hook.audit", id="audit", reads_map={"observed": "npc_state"})
    with_hook = comp.model_copy(update={"preprocessors": comp.preprocessors + (hook_pp,)})
    assert training_bundle_hash(with_hook, reg) == base


# ===========================================================================
# Step 3 — divergence A (composition service-binding identity supply)
# ===========================================================================


def test_composition_supply_folds_into_tbh():
    """A composition backend's supplied identity folds into the training-bundle-hash — changing
    the supplied model value moves the TBH (divergence A; the mirror of pipeline-level identity
    folding into the pipeline-hash)."""
    reg, _ = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    base = training_bundle_hash(comp, reg)
    s0 = comp.service_bindings[0]
    changed = comp.model_copy(update={
        "service_bindings": (s0.model_copy(update={"identity": {"model": "different-model"}}),)
    })
    assert training_bundle_hash(changed, reg) != base


def test_composition_missing_identity_supply_raises():
    """A trainable backend binding with no covering composition [service_bindings.<name>] supply
    raises BINDING_SUPPLY at compose."""
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    no_supply = comp.model_copy(update={"service_bindings": ()})
    reg.add_composition("trainables/dialogue.toml", no_supply)
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.BINDING_SUPPLY


def test_composition_orphan_supply_raises():
    """A composition service_bindings.<name> no node declares is an orphan → BINDING_SUPPLY."""
    bad = F.TRAINABLE_COMPOSITION.replace(
        '[service_bindings.llm]\ntype = "conjured_llm.dialogue"\nmodel = "qwen3.5-4b-gguf"\n',
        '[service_bindings.llm]\ntype = "conjured_llm.dialogue"\nmodel = "qwen3.5-4b-gguf"\n'
        '[service_bindings.ghost]\ntype = "conjured_llm.dialogue"\nmodel = "x"\n')
    reg, pipeline = F.build_trainable()
    reg.add_composition("trainables/dialogue.toml", loads(bad, "composition", file_path="c.toml"))
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.BINDING_SUPPLY


def test_composition_supply_transport_field_misplaced_raises():
    """A transport field placed in the composition's identity supply block is misplacement
    (IDENTITY_TRANSPORT_PLACEMENT) — reuses the pipeline identity-placement check. `endpoint`
    is a transport_schema field, not identity_schema."""
    bad = F.TRAINABLE_COMPOSITION.replace(
        '[service_bindings.llm]\ntype = "conjured_llm.dialogue"\nmodel = "qwen3.5-4b-gguf"\n',
        '[service_bindings.llm]\ntype = "conjured_llm.dialogue"\nmodel = "qwen3.5-4b-gguf"\nendpoint = "https://x"\n')
    reg, pipeline = F.build_trainable()
    reg.add_composition("trainables/dialogue.toml", loads(bad, "composition", file_path="c.toml"))
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.IDENTITY_TRANSPORT_PLACEMENT


def test_composition_supply_type_mismatch_raises():
    """The supplied type must equal the declared binding type → BINDING_SUPPLY on mismatch."""
    bad = F.TRAINABLE_COMPOSITION.replace(
        '[service_bindings.llm]\ntype = "conjured_llm.dialogue"\nmodel = "qwen3.5-4b-gguf"\n',
        '[service_bindings.llm]\ntype = "conjured_llm.other"\nmodel = "qwen3.5-4b-gguf"\n')
    reg, pipeline = F.build_trainable()
    reg.add_composition("trainables/dialogue.toml", loads(bad, "composition", file_path="c.toml"))
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.BINDING_SUPPLY


# ===========================================================================
# Step 4 — inline-scalar binding grammar (the inversion)
# ===========================================================================


def _pipeline_with_binding(supply: str):
    """A one-handler pipeline whose node supplies `bindings = { config = <supply> }`."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.n", loads(
        '[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n'
        '[bindings.config]\nsystem_prompt={type="str"}', "handler", file_path="n.toml"))
    pipe = loads(
        f'[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.n"\n'
        f'bindings = {{ config = {supply} }}\n[inputs]\ni={{type="str"}}\n',
        "pipeline", file_path="p.toml")
    return reg, pipe


def test_bare_string_binding_is_inline():
    """A bare string binding value is INLINE content (the inversion): it parses to an
    InlineBindingValue carrying the string itself, never a file path."""
    from conjured.ir.common import InlineBindingValue
    _, pipe = _pipeline_with_binding('"You are a gruff tavern keeper."')
    b = pipe.nodes[0].bindings[0]
    assert isinstance(b, InlineBindingValue)
    assert b.value == "You are a gruff tavern keeper."


def test_file_form_binding_is_external():
    """The explicit `{ file = "<path>" }` form is the external declaration-file reference."""
    from conjured.ir.common import FilePathBindingValue
    _, pipe = _pipeline_with_binding('{ file = "configs/markers.toml" }')
    b = pipe.nodes[0].bindings[0]
    assert isinstance(b, FilePathBindingValue)
    assert b.path == "configs/markers.toml"


def test_inline_object_binding_is_inline():
    """An inline table (without a `file` key) is an inline object value."""
    from conjured.ir.common import InlineBindingValue
    _, pipe = _pipeline_with_binding('{ system_prompt = "x" }')
    b = pipe.nodes[0].bindings[0]
    assert isinstance(b, InlineBindingValue)
    assert b.value == {"system_prompt": "x"}


def test_file_form_with_extra_keys_raises():
    """`file` is the engine-read external-declaration key — `{ file = "...", x = ... }` is an
    ambiguous mix and raises (fail loud, never guess)."""
    with pytest.raises(ContractViolation) as exc:
        _pipeline_with_binding('{ file = "p.toml", system_prompt = "x" }')
    assert exc.value.check is Check.MALFORMED_DECLARATION


def test_inline_scalar_and_file_classifier_covers_preprocessors():
    """The classifier is the single site feeding BOTH layers — a composition preprocessor
    binding classifies identically (bare string = inline)."""
    from conjured.ir.common import InlineBindingValue
    comp_toml = (
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="transform.a"\nid="p"\n'
        'writes_map={out="formatted_prompt"}\n'
        '[preprocessors.bindings]\ntemplate="{context}"\n'  # bare string = inline content
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="qwen"\n'
        '[trainable]\n[trainable.config]\n[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nformatted_prompt={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )
    comp = loads(comp_toml, "composition", file_path="c.toml")
    b = comp.preprocessors[0].bindings[0]
    assert isinstance(b, InlineBindingValue) and b.value == "{context}"


# --- A compile-directive binding is engine-owned — the node supplies nothing for it ---------
# (R-pipeline-001 binding-supply matching; handler/reference.md § The compile = "..." directive
#  sub-form). The directive IS the binding (the engine produces its value); a node supply for it is
#  rejected at compose, never silently absorbed into the pipeline-hash.


def test_pipeline_supply_for_compile_directive_binding_raises():
    """A pipeline node that supplies a value for a binding the handler declares as `compile = "..."`
    raises BINDING_SUPPLY at compose: the engine produces a compile-directive binding's value
    (engine-owned), so a node supply for it is meaningless and is rejected — never silently absorbed
    into the pipeline-hash (R-pipeline-001; handler/reference.md § The compile = "..." directive
    sub-form). RED-on-removal of the compile-wrong-supply arm in validator/compile.py
    `_check_binding_supply` (a failing-case test for the engine-owned guarantee)."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.n", loads(
        '[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n'
        '[bindings.normalizer]\ncompile="regex"\npattern="x"',  # an engine-owned compile-directive binding
        "handler", file_path="n.toml"))
    pipe = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.n"\n'
        'bindings = { normalizer = "oops, a node supply" }\n[inputs]\ni={type="str"}\n',  # wrong: supplies a value
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipe, reg, pipeline_name="acme.p", file_path="p.toml")
    assert exc.value.check is Check.BINDING_SUPPLY
    assert exc.value.rule_id == "R-pipeline-001"
    assert "normalizer" in exc.value.actual


def test_preprocessor_compile_table_classifies_inline_never_compile_binding():
    """FENCE: a [preprocessors.bindings] entry is SUPPLY-ONLY — `template = { compile = "jinja" }`
    there is an inline TABLE value (an InlineBindingValue carrying {"compile": "jinja"}), NEVER a
    CompileBinding. The compile directive is a handler-DECLARATION construct; the single supply-side
    classifier (`_parse_node_binding_values`) has no compile branch, so the wrong-supply hole has no
    preprocessor analogue. Goes RED if a CompileBinding ever reaches a preprocessor binding site."""
    from conjured.ir.common import CompileBinding, InlineBindingValue
    comp_toml = (
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="transform.a"\nid="p"\n'
        'writes_map={out="formatted_prompt"}\n'
        '[preprocessors.bindings]\ntemplate={ compile = "jinja" }\n'  # an inline TABLE, not a compile directive
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="qwen"\n'
        '[trainable]\n[trainable.config]\n[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nformatted_prompt={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )
    comp = loads(comp_toml, "composition", file_path="c.toml")
    b = comp.preprocessors[0].bindings[0]
    assert isinstance(b, InlineBindingValue)
    assert b.value == {"compile": "jinja"}
    assert not isinstance(b, CompileBinding)


# ===========================================================================
# Step 5 — ship-time defaults (the file docstring's step 5 — feature wired
# parser→IR→validator→hasher but previously had no case; rule-#1 happy/error gap)
# ===========================================================================


def _default_binding_reg(default_value='{ system_prompt = "hello" }'):
    """One-handler registry whose `config` binding declares a ship-time `default`
    (handler/reference.md § Ship-time defaults — a default-bearing binding MAY be omitted)."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.n", loads(
        '[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n'
        f'[bindings.config]\ndefault={default_value}\nsystem_prompt={{type="str"}}',
        "handler", file_path="n.toml"))
    return reg


def _pipe_omitting_config():
    return loads('[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.n"\n'
                 '[inputs]\ni={type="str"}\n', "pipeline", file_path="p.toml")


def _pipe_supplying_config(supply):
    return loads(f'[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.n"\n'
                 f'bindings = {{ config = {supply} }}\n[inputs]\ni={{type="str"}}\n',
                 "pipeline", file_path="p.toml")


def test_ship_time_default_omission_compiles():
    """HAPPY PATH: a default-bearing binding MAY be omitted at the node — the validator does NOT
    raise BINDING_SUPPLY; the engine supplies the declared default (validator/compile.py
    `not has_default`; handler/reference.md § Ship-time defaults)."""
    compile_pipeline(_pipe_omitting_config(), _default_binding_reg(),
                     pipeline_name="acme.p", file_path="p.toml")  # no raise == pass


def test_default_less_binding_omission_still_raises():
    """ERROR PATH / contrast that proves the default is what enables omission: a default-LESS
    binding omitted at the node raises BINDING_SUPPLY."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.n", loads(
        '[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n'
        '[bindings.config]\nsystem_prompt={type="str"}',  # no default
        "handler", file_path="n.toml"))
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(_pipe_omitting_config(), reg, pipeline_name="acme.p", file_path="p.toml")
    assert exc.value.check is Check.BINDING_SUPPLY


# verifies: preprocessor-mirrors-outer-node
def test_preprocessor_unsupplied_defaultless_binding_raises():
    """The PREPROCESSOR twin of test_default_less_binding_omission_still_raises: a preprocessor
    whose REFERENCED handler declares a default-less binding with no supply raises
    BINDING_SUPPLY/R-pipeline-001 at compose — through the SAME shared `_check_schema_binding_supply`
    the outer node uses (the mirror-pipeline principle). RED if the binding-supply arm never runs
    for preprocessors (the old inline model declared no binding schemas, so it could not fire)."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("transform.needs", loads(
        '[transform]\n[reads]\ncontext={type="str"}\n[output_schema]\nprompt={type="str"}\n'
        '[bindings.cfg]\nmarker={type="str"}\n',  # default-LESS → must be supplied
        "handler", file_path="needs.toml"))
    comp_toml = (
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="transform.needs"\nid="needs"\n'
        'reads_map={context="npc_state"}\nwrites_map={prompt="formatted_prompt"}\n'  # cfg NOT supplied
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="m"\n'
        '[trainable]\n[trainable.config]\ntemperature=0.7\nmax_tokens=64\n'
        '[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nformatted_prompt={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )
    reg.add_composition("c.toml", loads(comp_toml, "composition", file_path="c.toml"))
    pipeline = loads(
        '[meta]\nname="p"\n[[nodes]]\nkind="composition"\nname="c.toml"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name="p", file_path="p.toml")
    assert exc.value.check is Check.BINDING_SUPPLY
    assert exc.value.rule_id == "R-pipeline-001"
    assert "cfg" in exc.value.actual
    assert "dt.needs" in exc.value.expected  # the node_ref names the preprocessor


def test_ship_time_default_effective_value_folds_into_hash():
    """The EFFECTIVE value (supplied-or-default) folds into the pipeline-hash at the supply site:
    omitting (uses the default) vs supplying a different value → different pipeline-hashes
    (hash-model.md § What the pipeline-hash absorbs — the effective-value fold)."""
    reg = _default_binding_reg()
    h_default = pipeline_hash(_pipe_omitting_config(), reg)
    h_override = pipeline_hash(_pipe_supplying_config('{ system_prompt = "other" }'), reg)
    assert h_default != h_override


def test_declared_default_folds_into_handler_hash_even_when_overridden():
    """The DECLARED default folds into the handler-declaration content hash INDEPENDENT of any node
    override: two handlers differing ONLY in their declared default, both overridden with the SAME
    node value, still produce different pipeline-hashes (hash-model.md: 'changing a shipped default
    is a handler-declaration change that shifts the pipeline-hash ... independent of whether any
    node overrides it')."""
    supply = '{ system_prompt = "override" }'
    h_a = pipeline_hash(_pipe_supplying_config(supply), _default_binding_reg('{ system_prompt = "hello" }'))
    h_b = pipeline_hash(_pipe_supplying_config(supply), _default_binding_reg('{ system_prompt = "changed" }'))
    assert h_a != h_b


# ===========================================================================
# Step 6 — the service_bindings.<name> config block (the config-supply surface)
# + the stdlib-hook transport-field collision + the [config_schema] `default`
# ===========================================================================


def test_supply_config_block_parses_onto_the_supply_ir():
    """`config` is an engine-read key on a supply block — it parses into
    ServiceBindingSupply.config (the [config_schema] value supply); every other
    non-`type` key stays an identity value."""
    pipe = loads(
        '[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.s"\n'
        '[service_bindings.llm]\ntype="st.x"\nmodel="m"\n'
        '[service_bindings.llm.config]\ntemperature=0.7\nmax_tokens=64\n'
        '[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    supply = pipe.service_bindings[0]
    assert dict(supply.identity) == {"model": "m"}
    assert dict(supply.config) == {"temperature": 0.7, "max_tokens": 64}


def test_supply_config_non_table_rejects():
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.s"\n'
            '[service_bindings.llm]\ntype="st.x"\nconfig="hot"\n[inputs]\ni={type="str"}\n',
            "pipeline", file_path="p.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.section_path == "service_bindings.llm.config"


def test_trainable_backend_supply_entry_rejects_a_config_block():
    """The trainable kind's config supply site is [trainable.config] — a config block
    on the BACKEND's composition supply entry is a second, undefined surface and is
    rejected loud (never merged, never silently ignored)."""
    bad = F.TRAINABLE_COMPOSITION.replace(
        '[service_bindings.llm]\ntype = "conjured_llm.dialogue"\nmodel = "qwen3.5-4b-gguf"\n',
        '[service_bindings.llm]\ntype = "conjured_llm.dialogue"\nmodel = "qwen3.5-4b-gguf"\n'
        '[service_bindings.llm.config]\ntemperature = 0.9\n')
    reg, pipeline = F.build_trainable()
    reg.add_composition("trainables/dialogue.toml", loads(bad, "composition", file_path="c.toml"))
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    cv = exc.value
    assert cv.check is Check.CONFIG_SCHEMA_SUPPLY
    assert "trainable.config" in cv.expected  # points at the one supply surface


def test_config_schema_field_default_parses_and_stays_forbidden_elsewhere():
    """A [config_schema] field MAY declare a per-field ship-time `default` (the
    config-side ship-time-default surface); identity/transport fields admit none
    (channel fields are pinned by test_gap9 — I1)."""
    st = loads(
        'name="st.x"\n[identity_schema]\nm={type="str"}\n[transport_schema]\ne={type="str"}\n'
        '[config_schema]\ntemperature={type="float", default=0.8}\nmax_tokens={type="int"}\n',
        "service_type", file_path="st.toml")
    by_name = {f.name: f for f in st.config_schema}
    assert by_name["temperature"].has_default and by_name["temperature"].default == 0.8
    assert not by_name["max_tokens"].has_default
    with pytest.raises(ContractViolation) as exc:
        loads(
            'name="st.x"\n[identity_schema]\nm={type="str", default="d"}\n'
            '[transport_schema]\ne={type="str"}\n[config_schema]\n',
            "service_type", file_path="st.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR  # `default` unknown outside config_schema


def test_hook_transport_field_collision_rejects_at_load():
    """A transport_schema field name colliding with a declared input-port or
    bindings.<name> name makes one signature-union kwarg two-sourced — rejected loud
    at declaration load (handler/reference.md § transport_schema)."""
    with pytest.raises(ContractViolation) as reads_collision:
        loads('[hook]\n[reads]\nlog_path={type="str"}\n[service_bindings]\n'
              '[transport_schema]\nlog_path={type="str"}\n',
              "handler", file_path="h.toml")
    assert reads_collision.value.check is Check.NAME_UNIQUENESS
    assert "input-port" in reads_collision.value.actual

    with pytest.raises(ContractViolation) as binding_collision:
        loads('[hook]\n[reads]\nout={type="str"}\n[service_bindings]\n'
              '[transport_schema]\nfmt={type="str"}\n[bindings.fmt]\nstyle={type="str"}\n',
              "handler", file_path="h.toml")
    assert binding_collision.value.check is Check.NAME_UNIQUENESS
    assert "bindings.<name>" in binding_collision.value.actual


def test_trainable_config_uncovered_field_fails_compose():
    """The covered direction at the TRAINABLE supply site (identical at both sites —
    service-type/reference.md § The [config_schema] contract): a declared
    [config_schema] field neither supplied in [trainable.config] nor default-bearing
    fails compose with the exact CV."""
    bad = F.TRAINABLE_COMPOSITION.replace("max_tokens = 512\n", "")  # uncovered now
    reg, pipeline = F.build_trainable()
    reg.add_composition("trainables/dialogue.toml", loads(bad, "composition", file_path="c.toml"))
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    cv = exc.value
    assert cv.check is Check.CONFIG_SCHEMA_SUPPLY
    assert cv.rule_id == "R-service-type-002"
    assert "max_tokens" in cv.actual
    assert cv.section_path == "trainable.config"
