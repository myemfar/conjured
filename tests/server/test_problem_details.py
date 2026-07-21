"""``to_problem_details`` — the RFC 9457 HTTP wire projection (R-error-channel-005).

Unit-tests the projection for each of the three closed error classes directly (the
worked-example mappings in error-channel/reference.md § RFC 9457 HTTP wire projection),
including the deferred-catalog edge (an ``audit_code``-less ContractViolation) and the
HTTP null-omission divergence.
"""

from __future__ import annotations

import pytest

from conjured.errors import (
    Check,
    ContractViolation,
    ContractViolationGroup,
    FieldValidationDetail,
    PipelineFailure,
    SchemaValidationError,
)
from conjured.server import to_problem_details


# --- ContractViolation ------------------------------------------------------------


def test_contract_violation_audit_code_present():
    # CLOSED_GRAMMAR is registered to a real audit-less CV in practice, but the projection
    # itself keys `type` off any present audit_code — use a code-bearing SVE-style shape via
    # a CV that DOES carry a file location to exercise instance = file#L.
    cv = ContractViolation(
        check=Check.CLOSED_GRAMMAR,
        rule_id="R-handler-006",
        expected="declared key per the handler's declared grammar",
        actual="unknown key 'mod'",
        file_path="handlers/npc_emotion.toml",
        section_path="bindings.mood",
        line_number=42,
        remediation_hint="rename 'mod' to 'mode'",
    )
    body = to_problem_details(cv, 400)
    assert body["status"] == 400
    assert body["type"] == "about:blank"  # audit_code None (catalog deferred) → about:blank
    assert "audit_code" not in body  # null → omitted (HTTP null-omission)
    assert body["rule_id"] == "R-handler-006"
    assert body["detail"] == (
        "expected: declared key per the handler's declared grammar; actual: unknown key 'mod'"
    )
    assert body["instance"] == "handlers/npc_emotion.toml#L42"
    assert body["section_path"] == "bindings.mood"
    assert body["line_number"] == 42
    assert body["remediation_hint"] == "rename 'mod' to 'mode'"
    assert "composition_ref" not in body  # null → omitted
    assert "pipeline_run_id" not in body  # null (load-time) → omitted


def test_contract_violation_api_boundary_composition_instance():
    """The API-boundary missing-input CV: file_path null, composition_ref present →
    instance = composition_ref; pipeline_run_id echoed."""
    cv = ContractViolation(
        check=Check.API_INPUTS_ENFORCEMENT,
        rule_id="R-pipeline-001",
        expected="every declared [inputs] field present",
        actual="missing declared input field(s): ['text']",
        composition_ref="srv.echo",
        pipeline_run_id="run_x_1",
    )
    body = to_problem_details(cv, 400)
    assert body["type"] == "about:blank"
    assert body["instance"] == "srv.echo"
    assert body["pipeline_run_id"] == "run_x_1"


# --- SchemaValidationError --------------------------------------------------------


def _sve(actual_value="'x'"):
    return SchemaValidationError(
        audit_code="C1.HALT_ON_INPUT_VALIDATION_ERROR.001",
        handler_qualified_name="pkg.npc_emotion",
        handler_position=2,
        pipeline_run_id="run_2026-05-06T14:23:11Z_a3f9",
        schema_source="handlers/npc_emotion.toml",
        field_validations=(
            FieldValidationDetail(
                field_path="reads.n",
                expected_type="int",
                actual_type="str",
                actual_value=actual_value,
                constraint_violated="type",
                message="expected int, got str 'x'",
            ),
        ),
    )


def test_schema_validation_error_projection():
    body = to_problem_details(_sve(), 502)
    assert body["status"] == 502
    # No per-error web URI — the engine's `type` is always RFC 9457's no-type value;
    # dispatch is on the `audit_code` extension member, asserted below.
    assert body["type"] == "about:blank"
    assert body["title"] == "Schema validation failed — pkg.npc_emotion"
    assert body["detail"] == "expected int, got str 'x'"
    # instance carries the run id VERBATIM (no percent-encoding) — the colon-bearing consumer
    # id here proves it: re-adding urllib quote() would render `:`→`%3A` and fail this.
    assert body["instance"] == (
        "pkg.npc_emotion?run=run_2026-05-06T14:23:11Z_a3f9&position=2"
    )
    assert body["audit_code"] == "C1.HALT_ON_INPUT_VALIDATION_ERROR.001"
    assert body["schema_source"] == "handlers/npc_emotion.toml"
    assert len(body["field_validations"]) == 1
    assert body["field_validations"][0]["actual_value"] == "'x'"


def test_schema_validation_error_omits_null_actual_value():
    body = to_problem_details(_sve(actual_value=None), 502)
    fv = body["field_validations"][0]
    assert "actual_value" not in fv  # null → omitted within field_validations


# --- PipelineFailure --------------------------------------------------------------


def test_pipeline_failure_service_locus_504():
    pf = PipelineFailure(
        failure_category="service",
        cause_class="TimeoutError",
        cause_message="Service binding 'llm_main' exceeded timeout_ms=30000",
        failed_handler_qualified_name="pkg.generate_dialogue",
        failed_handler_position=4,
        bindings_snapshot={"temperature": 0.7, "max_tokens": 256},
        reads_snapshot={"npc_emotion": {"mood": "angry"}},
        pipeline_run_id="run_x",
        composition_ref="dialogue[3]",
        service_binding_name="llm_main",
        elapsed_ms_at_failure=30142,
    )
    body = to_problem_details(pf, 504)
    assert body["status"] == 504
    # No per-error web URI — dispatch is on the `cause_class` extension member.
    assert body["type"] == "about:blank"
    assert body["title"] == "Pipeline failure — TimeoutError"
    assert body["detail"] == "Service binding 'llm_main' exceeded timeout_ms=30000"
    assert body["instance"] == "dialogue[3]"
    assert body["failure_category"] == "service"
    assert body["service_binding_name"] == "llm_main"
    assert body["elapsed_ms_at_failure"] == 30142
    assert body["bindings_snapshot"] == {"temperature": 0.7, "max_tokens": 256}
    assert body["reads_snapshot"] == {"npc_emotion": {"mood": "angry"}}


def test_pipeline_failure_handler_locus_omits_binding():
    pf = PipelineFailure(
        failure_category="handler",
        cause_class="ValueError",
        cause_message="kaboom",
        failed_handler_qualified_name="pkg.boom",
        failed_handler_position=0,
        bindings_snapshot={},
        reads_snapshot={"text": "hi"},
        pipeline_run_id="run_x",
        composition_ref="boom[0]",
    )
    body = to_problem_details(pf, 500)
    assert body["status"] == 500
    assert body["failure_category"] == "handler"
    assert "service_binding_name" not in body  # null → omitted
    assert "elapsed_ms_at_failure" not in body  # null → omitted


# --- ContractViolationGroup -------------------------------------------------------


def test_contract_violation_group_projection():
    """§ ContractViolationGroup → RFC 9457: one envelope (about:blank, no audit URI of its
    own), the member violations carried verbatim as a `violations` array of CV problem
    objects, and `detail` = count + each member's contrast."""
    a = ContractViolation(
        check=Check.READ_WRITE_SHAPE, rule_id="R-pipeline-001",
        expected="one type for channel 'ch1'", actual="str vs int",
        composition_ref="acme.p", section_path="channel.ch1",
    )
    b = ContractViolation(
        check=Check.READ_WRITE_SHAPE, rule_id="R-pipeline-001",
        expected="one type for channel 'ch2'", actual="str vs int",
        composition_ref="acme.p", section_path="channel.ch2",
    )
    body = to_problem_details(ContractViolationGroup((a, b)), 400)
    assert body["type"] == "about:blank"  # a container has no audit-catalog entry of its own
    assert body["title"] == "Multiple contract violations"
    assert body["status"] == 400
    assert body["detail"].startswith("2 contract violations: ")
    assert "one type for channel 'ch1'" in body["detail"]
    assert "one type for channel 'ch2'" in body["detail"]
    assert body["instance"] == "acme.p"  # the shared compose locus (composition_ref)
    # The members ride verbatim as their own § ContractViolation → RFC 9457 problem objects.
    assert len(body["violations"]) == 2
    assert [v["section_path"] for v in body["violations"]] == ["channel.ch1", "channel.ch2"]
    assert all(v["rule_id"] == "R-pipeline-001" for v in body["violations"])


def test_non_error_payload_fails_loud():
    with pytest.raises(TypeError):
        to_problem_details({"not": "an error"}, 500)
