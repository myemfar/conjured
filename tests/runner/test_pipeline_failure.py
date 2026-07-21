"""``PipelineFailure`` — the closed enum's third class (Phase 3): the full decided
payload (error-channel/reference.md § PipelineFailure payload), the deliberate
absences (no ``audit_code`` — every PF maps to the single registered wrap audit, the
B2 ruling), the mandatory snapshot deep copies, and the runtime-only location
requirements. The wrap *behavior* (the dispatch boundary) is tests/runner/test_run.py
territory; this suite owns the class + its registration."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from conjured.errors import (
    AUDIT_CODE_REGISTRY,
    CHECK_REGISTRY,
    PIPELINE_FAILURE_WRAP_AUDIT_CODE,
    Check,
    CheckRecord,
    ConjuredError,
    PipelineFailure,
    snapshot_copy,
)


def _pf(**overrides):
    kwargs = dict(
        failure_category="handler",
        cause_class="RuntimeError",
        cause_message="kaboom",
        failed_handler_qualified_name="acme.generate",
        failed_handler_position=2,
        bindings_snapshot={"cfg": {"marker": "brackets"}},
        reads_snapshot={"text": "hi"},
        pipeline_run_id="run_2026-06-10T00:00:00Z_t3st",
        composition_ref="acme.dialogue[1]",
    )
    kwargs.update(overrides)
    return PipelineFailure(**kwargs)


# ---------------------------------------------------------------------------
# 1. The full required payload; optionals default null
# ---------------------------------------------------------------------------


def test_pf_carries_the_full_required_payload_and_null_optionals():
    pf = _pf()
    assert isinstance(pf, ConjuredError)  # the closed enum's shared root
    assert pf.failure_category == "handler"
    assert pf.cause_class == "RuntimeError"
    assert pf.cause_message == "kaboom"
    assert pf.failed_handler_qualified_name == "acme.generate"
    assert pf.failed_handler_position == 2
    assert pf.bindings_snapshot == {"cfg": {"marker": "brackets"}}
    assert pf.reads_snapshot == {"text": "hi"}
    assert pf.pipeline_run_id == "run_2026-06-10T00:00:00Z_t3st"
    assert pf.composition_ref == "acme.dialogue[1]"
    # Optionals default null (a pipeline-level cause has no failing binding; a
    # harness-constructed PF outside a timing context has no elapsed value).
    assert pf.service_binding_name is None
    assert pf.elapsed_ms_at_failure is None
    # message: the auto-rendered stringification carries the key fields.
    assert "RuntimeError" in str(pf) and "acme.dialogue[1]" in str(pf)


def test_pf_optionals_carried_when_supplied():
    pf = _pf(failure_category="service", cause_class="ConnectionError",
             service_binding_name="llm", elapsed_ms_at_failure=128)
    assert pf.service_binding_name == "llm"
    assert pf.elapsed_ms_at_failure == 128


# ---------------------------------------------------------------------------
# 2. No audit_code attribute; the ctor takes no audit/check/rule argument
# ---------------------------------------------------------------------------


def test_pf_has_no_audit_code_and_rejects_audit_arguments():
    pf = _pf()
    # Absent by design, not by omission (§ PipelineFailure payload).
    for absent in ("audit_code", "rule_id", "remediation_hint", "expected", "actual",
                   "file_path"):
        assert not hasattr(pf, absent), absent
    # The constructor carries NO audit/check/rule argument (B2): the kwarg-only
    # signature rejects them outright.
    with pytest.raises(TypeError):
        _pf(audit_code=PIPELINE_FAILURE_WRAP_AUDIT_CODE)
    with pytest.raises(TypeError):
        _pf(check=Check.PIPELINE_FAILURE_WRAP)
    with pytest.raises(TypeError):
        _pf(rule_id="R-error-channel-001")


# ---------------------------------------------------------------------------
# 3. The snapshots are deep copies; frozen delivery forms materialize to plain data
# ---------------------------------------------------------------------------


# verifies: pf-snapshot-deepcopy
def test_pf_snapshots_are_deep_copies_immune_to_source_mutation():
    bindings = {"cfg": {"tags": ["a"]}}
    ctx = {"items": [1, 2]}
    pf = _pf(bindings_snapshot=bindings, reads_snapshot=ctx)
    bindings["cfg"]["tags"].append("mutated")
    ctx["items"].append(3)
    # The failure record reflects the state AT failure, not subsequent mutation.
    assert pf.bindings_snapshot == {"cfg": {"tags": ["a"]}}
    assert pf.reads_snapshot == {"items": [1, 2]}


def test_pf_snapshots_materialize_frozen_delivery_forms():
    # A reference-delivered binding arrives as the engine's frozen forms
    # (MappingProxyType / tuple / frozenset); copy.deepcopy of a mappingproxy raises,
    # so the materializing walk IS the mandated deep copy.
    frozen = MappingProxyType(
        {"table": MappingProxyType({"alias": "Blackwell"}), "tags": frozenset({"x"}),
         "pair": (1, [2])}
    )
    pf = _pf(bindings_snapshot={"lookup": frozen}, reads_snapshot={})
    lookup = pf.bindings_snapshot["lookup"]
    assert isinstance(lookup, dict) and not isinstance(lookup, MappingProxyType)
    assert isinstance(lookup["table"], dict)
    assert lookup["table"] == {"alias": "Blackwell"}
    assert isinstance(lookup["tags"], set)
    assert isinstance(lookup["pair"], tuple)  # a tuple stays a tuple of copies
    assert isinstance(lookup["pair"][1], list)


def test_snapshot_copy_walks_containers_and_passes_leaves_by_reference():
    leaf = object()  # an engine-owned compile artifact stands outside the containers
    source = {"k": [leaf], "t": (leaf,)}
    copied = snapshot_copy(source)
    assert copied == {"k": [leaf], "t": (leaf,)}
    assert copied is not source and copied["k"] is not source["k"]
    assert copied["k"][0] is leaf  # copy-exempt leaf passes by reference (vector 4)


# ---------------------------------------------------------------------------
# 4. The registration row (B2) — exact record + the catalog code
# ---------------------------------------------------------------------------


def test_pipeline_failure_wrap_registered_exactly_per_the_b2_ruling():
    assert CHECK_REGISTRY[Check.PIPELINE_FAILURE_WRAP] == CheckRecord(
        ("R-error-channel-001",), "PipelineFailure", "C1.PIPELINE_FAILURE_WRAP.001"
    )
    assert PIPELINE_FAILURE_WRAP_AUDIT_CODE == "C1.PIPELINE_FAILURE_WRAP.001"
    assert AUDIT_CODE_REGISTRY[PIPELINE_FAILURE_WRAP_AUDIT_CODE] is Check.PIPELINE_FAILURE_WRAP


# ---------------------------------------------------------------------------
# 5. Runtime-only: the location-bearing requirements fail loud
# ---------------------------------------------------------------------------


def test_pf_requires_non_empty_pipeline_run_id():
    with pytest.raises(ValueError, match="pipeline_run_id"):
        _pf(pipeline_run_id="")


def test_pf_requires_non_empty_composition_ref():
    with pytest.raises(ValueError, match="composition_ref"):
        _pf(composition_ref="")


# ---------------------------------------------------------------------------
# 6. failure_category — the closed locus enum + the service<->binding invariant
# ---------------------------------------------------------------------------


# verifies: pf-failure-category-closed-enum
def test_pf_rejects_a_failure_category_outside_the_closed_enum():
    # The locus is engine-produced + engine-enforced (a closed set), unlike the open author-named
    # cause_class. A member outside service/handler/engine is a runner-construction bug -> fail loud.
    with pytest.raises(ValueError, match="failure_category"):
        _pf(failure_category="backend")


# verifies: pf-service-binding-iff-service
def test_pf_service_binding_present_iff_category_is_service():
    # The structural form of the payload's presence rule — both directions fail loud.
    with pytest.raises(ValueError, match="service_binding_name"):
        _pf(failure_category="service", service_binding_name=None)   # service must name its binding
    with pytest.raises(ValueError, match="service_binding_name"):
        _pf(failure_category="handler", service_binding_name="llm")  # handler must not carry one
    with pytest.raises(ValueError, match="service_binding_name"):
        _pf(failure_category="engine", service_binding_name="llm")   # engine must not carry one
    pf = _pf(failure_category="service", service_binding_name="llm")  # the valid service shape
    assert pf.failure_category == "service" and pf.service_binding_name == "llm"
