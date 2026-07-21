"""RED-on-removal seal tests for engine fixes without a natural host suite —
each the exact adversary its originating audit finding named: the hasher's
structured-wrap parity, the IR-wide closed-shape seals, and the
SVE non-empty-field_validations constructor seal."""

from __future__ import annotations

import pytest

from conjured.errors import (
    Check,
    ContractViolation,
    FieldValidationDetail,
    INPUT_VALIDATION_AUDIT_CODE,
    SchemaValidationError,
)
from conjured.hasher.hashes import _comp_non_hook_referenced, pipeline_hash
from conjured.ir.common import InlineBindingValue, ServiceBindingSupply
from conjured.ir.deployment import TransportBlock
from conjured.validator import DeclarationRegistry, loads

from tests.derivables._fixtures import (
    SERVICE_TYPE_DIALOGUE,
    TRAINABLE_COMPOSITION,
    build_bindings,
)


# ── HASHER-2: non-canonicalizable SUPPLIED values stay in the closed channel ──────────


def _with_set_binding(pipeline):
    """The derivables fixture pipeline with node-0's inline binding value swapped for a
    SET — reachable only via the canon-sanctioned direct-Pydantic dialect (TOML has no
    set literal); the exact construction the omitted-default adversary tests use."""
    node0 = pipeline.nodes[0]
    bad_binding = InlineBindingValue(name="config", value={1, 2})
    return pipeline.model_copy(
        update={"nodes": (node0.model_copy(update={"bindings": (bad_binding,)}),)
                + tuple(pipeline.nodes[1:])}
    )


def test_non_canonicalizable_supplied_binding_is_structured():
    """A set-valued INLINE supplied binding folds through the same structured
    MALFORMED_DECLARATION wrap the omitted-default fold carries (fail-loud parity) —
    never a bare TypeError/ValueError escaping pipeline_hash as a fourth class."""
    reg, pipeline = build_bindings()
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(_with_set_binding(pipeline), reg)
    assert exc.value.check is Check.MALFORMED_DECLARATION


def test_non_canonicalizable_identity_value_is_structured():
    reg, pipeline = build_bindings()
    supply = pipeline.service_bindings[0]
    bad = supply.model_copy(update={"identity": {"model": {1, 2}}})
    pipeline = pipeline.model_copy(update={"service_bindings": (bad,)})
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipeline, reg)
    assert exc.value.check is Check.MALFORMED_DECLARATION


def test_non_canonicalizable_config_value_is_structured():
    reg, pipeline = build_bindings()
    supply = pipeline.service_bindings[0]
    bad = supply.model_copy(update={"config": {"temperature": {1, 2}}})
    pipeline = pipeline.model_copy(update={"service_bindings": (bad,)})
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipeline, reg)
    assert exc.value.check is Check.MALFORMED_DECLARATION


# ── HASHER-3: the TBH supply-domain scan's mirror fail-loud ───────────────────────────


def test_comp_supply_scan_fails_loud_on_an_unresolvable_preprocessor():
    """The direct-call twin of the pipeline-layer scan's own failing case: an
    unresolvable preprocessor name raises the structured HANDLER_NAME_RESOLUTION —
    never a silent binding-less () that would narrow the TBH's folded supply domain
    (the arm is shadowed end-to-end today by the preprocessors fold's own raise;
    this pins the mirror so a fold reorder cannot expose a silent path)."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    comp = loads(TRAINABLE_COMPOSITION, "composition", file_path="c.toml")
    # transform.formatter deliberately NOT registered.
    with pytest.raises(ContractViolation) as exc:
        _comp_non_hook_referenced(comp, {"llm"}, reg)
    assert exc.value.check is Check.HANDLER_NAME_RESOLUTION


# ── IR-2: the IR-wide frozen + extra="forbid" seals ───────────────────────────────────


def test_ir_instances_are_assignment_frozen():
    """RED if frozen=True is dropped from IRModel.model_config (the whole suite stayed
    green with both seals dropped — the empirically-shown gap this closes)."""
    block = TransportBlock(name="llm", values={"endpoint": "https://x"})
    with pytest.raises(Exception):
        block.name = "other"  # pydantic frozen-instance error


def test_ir_unknown_fields_are_unrepresentable():
    """RED if extra="forbid" is dropped: an undeclared constructor field must refuse —
    the structural floor under the closed-shape grammar rules (R-handler-006 family)."""
    with pytest.raises(Exception):
        TransportBlock(name="llm", values={}, surprise=1)


def test_supply_ir_carries_both_seals():
    """One representative from a second module (the parameterized-per-module spirit):
    the supply IR refuses assignment and unknown fields alike."""
    supply = ServiceBindingSupply(name="llm", type="conjured_llm.x", identity={}, config={})
    with pytest.raises(Exception):
        supply.type = "other"
    with pytest.raises(Exception):
        ServiceBindingSupply(name="llm", type="conjured_llm.x", identity={}, config={}, x=1)


# ── ERRORS-CD-7: the SVE non-empty field_validations seal ─────────────────────────────


def test_sve_refuses_an_empty_field_validations_tuple():
    """The constructor seal beside the other registration seals: an SVE with no failed
    field is a construction bug (canon: non-empty array, single-field collapse
    forbidden) — RED if the guard is removed."""
    with pytest.raises(ValueError, match="non-empty field_validations"):
        SchemaValidationError(
            audit_code=INPUT_VALIDATION_AUDIT_CODE,
            handler_qualified_name="pkg.h",
            handler_position=0,
            pipeline_run_id="run_x",
            schema_source="h.toml",
            field_validations=(),
        )


def test_sve_non_empty_path_still_constructs():
    detail = FieldValidationDetail(
        field_path="reads.x", expected_type="int", actual_type="str",
        actual_value="'v'", constraint_violated="type", message="not an int",
    )
    sve = SchemaValidationError(
        audit_code=INPUT_VALIDATION_AUDIT_CODE,
        handler_qualified_name="pkg.h", handler_position=0,
        pipeline_run_id="run_x", schema_source="h.toml",
        field_validations=(detail,),
    )
    assert sve.field_validations == (detail,)


# ── RESOLVE-3: the invoke_streaming dispatch-kwargs walk at compose ──────────────────


# verifies: streamable-signature-compose-checked
def test_streamable_backend_with_wrong_streaming_kwargs_refuses_at_compose():
    """A streamable binding whose adapter exposes a GENERATOR invoke_streaming with the
    WRONG closed dispatch-kwargs composes red (ADAPTER_SIGNATURE) — a compose-knowable
    signature defect never waits for the first streamed dispatch to TypeError."""
    from conjured.validator.resolve_adapter import check_streamable_backend

    st = loads(SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml")

    class WrongKwargsStreaming:
        def invoke(self, *, input_payload, service_name, caller_qualified_name,
                   caller_position, temperature, max_tokens, **transport_extra):
            return {}

        def invoke_streaming(self, *, wrong_name):  # a generator, wrong kwargs
            yield "x"

    with pytest.raises(ContractViolation) as exc:
        check_streamable_backend(
            WrongKwargsStreaming,
            qualified_name="tests.WrongKwargsStreaming", toml_path="st.toml",
            service_type=st,
        )
    assert exc.value.check is Check.ADAPTER_SIGNATURE
