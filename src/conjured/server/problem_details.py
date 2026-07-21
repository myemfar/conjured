"""The RFC 9457 HTTP wire projection — ``to_problem_details(payload, status_code)``.

The single, shared projection from an in-process error-channel payload to an
``application/problem+json`` envelope, owned in full by the error-channel reference
(``conjured/docs/components/error-channel/reference.md`` § RFC 9457 HTTP wire projection;
R-error-channel-005). That section locates the helper **"in the engine's HTTP
error-response handler"** — the server — so it lives here, not in ``conjured.errors``
(which stays the pure typed-error surface). The server is the engine's sole HTTP
boundary, so this is the projection's single home (single-ownership), not a
re-derivation: nothing else projects the engine's errors onto HTTP.

What this module owns is the **mechanical projection** (which in-process field maps to
which RFC 9457 standard envelope member vs. extension member, and the null-omission
divergence). What it does **not** own — and never re-derives — is **status selection**:
``status_code`` is supplied by the caller per R-error-channel-005's per-class status
table (the server's :mod:`conjured.server.app` computes it; § ContractViolation →
RFC 9457 makes status caller-supplied, and the SVE / PipelineFailure status pins are
applied there). This keeps the envelope shape here and the status policy at the wire.

The three projections (§ ContractViolation → RFC 9457, § SchemaValidationError →
RFC 9457, § PipelineFailure → RFC 9457) are implemented verbatim from those tables.
**Null-serialization divergence** (§ Optional field serialization): the HTTP wire form
**omits** null-valued optional extension members (unlike the canonical in-process JSON,
which serializes them as explicit ``null``).
"""

from __future__ import annotations

from typing import Any

from conjured.errors import (
    ContractViolation,
    ContractViolationGroup,
    PipelineFailure,
    SchemaValidationError,
)

#: The one ``type`` value the engine emits: RFC 9457's own "no specific type URI" value.
#: The engine mints NO per-error web URI — error→docs resolution is local, against the
#: docs shipped in the package, keyed by the ``audit_code`` / ``cause_class`` / ``rule_id``
#: extension members (error-channel/reference.md § ContractViolation → RFC 9457).
_TYPE_NO_URI = "about:blank"


def to_problem_details(payload: object, status_code: int) -> dict[str, Any]:
    """Project one engine error-channel ``payload`` onto an RFC 9457 Problem Details
    object, with the HTTP ``status`` member set to the caller-supplied ``status_code``
    (R-error-channel-005). ``payload`` is one of the three closed error-class instances;
    any other type is engine misuse and fails loud.

    The returned dict is the ``application/problem+json`` body; the caller sets the
    ``Content-Type`` response header (every HTTP error response is
    ``application/problem+json``, R-error-channel-005). Null-valued optional extension
    members are omitted (the HTTP null-serialization divergence)."""
    if isinstance(payload, SchemaValidationError):
        return _schema_validation_error(payload, status_code)
    if isinstance(payload, ContractViolationGroup):
        return _contract_violation_group(payload, status_code)
    if isinstance(payload, ContractViolation):
        return _contract_violation(payload, status_code)
    if isinstance(payload, PipelineFailure):
        return _pipeline_failure(payload, status_code)
    raise TypeError(
        "to_problem_details projects an engine error-channel payload "
        "(ContractViolation / ContractViolationGroup / SchemaValidationError / "
        f"PipelineFailure); got {type(payload).__name__} (error-channel/reference.md "
        "§ RFC 9457 HTTP wire projection)"
    )


def _put(envelope: dict[str, Any], member: str, value: object) -> None:
    """Add an extension member, omitting it when null — the HTTP null-serialization
    divergence (§ Optional field serialization: the wire form omits null extension
    members per RFC 9457 convention)."""
    if value is not None:
        envelope[member] = value


def _contract_violation(cv: ContractViolation, status_code: int) -> dict[str, Any]:
    """§ ContractViolation → RFC 9457. ``status`` is caller-supplied.

    ``type`` is always ``about:blank`` (the engine mints no per-error web URI; dispatch
    is on the ``audit_code`` extension member, resolution is against the shipped docs).
    ``title`` derives from the audit catalog, which is **deferred**: ``audit_code`` is
    ``None`` for every ContractViolation whose catalog code is not yet assigned (the only
    CV that reaches the server's wire surface — the API-boundary missing-input CV — is one
    such), and an absent ``audit_code`` extension member is omitted (null). ``status`` /
    ``detail`` / ``rule_id`` / ``expected`` / ``actual`` / ``instance`` all still
    render."""
    envelope: dict[str, Any] = {
        "type": _TYPE_NO_URI,
        "title": "Contract violation",
        "status": status_code,
        "detail": f"expected: {cv.expected}; actual: {cv.actual}",
        "instance": _cv_instance(cv),
    }
    _put(envelope, "audit_code", cv.audit_code)
    envelope["rule_id"] = cv.rule_id
    envelope["expected"] = cv.expected
    envelope["actual"] = cv.actual
    _put(envelope, "section_path", cv.section_path)
    _put(envelope, "line_number", cv.line_number)
    _put(envelope, "composition_ref", cv.composition_ref)
    _put(envelope, "pipeline_run_id", cv.pipeline_run_id)
    _put(envelope, "remediation_hint", cv.remediation_hint)
    return envelope


def _contract_violation_group(
    group: ContractViolationGroup, status_code: int
) -> dict[str, Any]:
    """§ ContractViolationGroup → RFC 9457. A single envelope carrying the member
    violations as a ``violations`` array — the same shape SVE uses for
    ``field_validations``, lifted to the violation grain. ``type`` is ``about:blank``
    (a container has no audit-catalog entry of its own; each member keeps its own
    ``audit_code``); ``detail`` is the count plus each member's ``expected … ; actual …``
    contrast; ``instance`` is the compose locus the members share; each member projects
    through the verbatim § ContractViolation → RFC 9457 envelope."""
    members = group.violations
    envelope: dict[str, Any] = {
        "type": _TYPE_NO_URI,
        "title": "Multiple contract violations",
        "status": status_code,
        "detail": (
            f"{len(members)} contract violations: "
            + "; ".join(f"expected: {cv.expected}; actual: {cv.actual}" for cv in members)
        ),
        # The members share one composition-validation locus — the group's `instance` is
        # that SHARED locus only (file path or composition ref, per § CVGroup → RFC 9457);
        # a member's `#L<line>` fragment is a PER-MEMBER locus and rides inside its own
        # `violations` entry, never on the group envelope.
        "instance": members[0].file_path or members[0].composition_ref,
        "violations": [_contract_violation(cv, status_code) for cv in members],
    }
    return envelope


def _cv_instance(cv: ContractViolation) -> str:
    """The ``instance`` member (§ ContractViolation → RFC 9457): ``<file_path>#L<n>``
    when ``line_number`` is non-null; ``<file_path>`` when it is null;
    ``<composition_ref>`` for composition-level violations where ``file_path`` is null —
    the location-bearing-field requirement (at least one of file_path / composition_ref)
    carries through to ``instance``."""
    if cv.file_path is not None:
        if cv.line_number is not None:
            return f"{cv.file_path}#L{cv.line_number}"
        return cv.file_path
    # file_path is null → composition_ref is non-null (the location requirement).
    return cv.composition_ref  # type: ignore[return-value]


def _schema_validation_error(
    sve: SchemaValidationError, status_code: int
) -> dict[str, Any]:
    """§ SchemaValidationError → RFC 9457. SVE's audit codes are decided canon (always
    present, on the ``audit_code`` extension member — the dispatch key); the projection
    carries the full ``field_validations`` array verbatim."""
    envelope: dict[str, Any] = {
        "type": _TYPE_NO_URI,
        "title": f"Schema validation failed — {sve.handler_qualified_name}",
        "status": status_code,
        # detail = joined per-field validation messages (§ SVE → RFC 9457 detail row).
        "detail": "; ".join(fv.message for fv in sve.field_validations),
        # instance = handler-identity URI with run correlation; the colon-free basic ISO-8601
        # pipeline_run_id (§ Error payload field set) rides the query verbatim; position makes
        # it unique under multi-dispatch.
        "instance": (
            f"{sve.handler_qualified_name}"
            f"?run={sve.pipeline_run_id}&position={sve.handler_position}"
        ),
        "audit_code": sve.audit_code,
        "rule_id": sve.rule_id,
        "schema_source": sve.schema_source,
        "pipeline_run_id": sve.pipeline_run_id,
        "field_validations": [
            _field_validation(fv) for fv in sve.field_validations
        ],
    }
    return envelope


def _field_validation(fv: object) -> dict[str, Any]:
    """One ``FieldValidationDetail`` as a wire object; ``actual_value`` omitted when null
    (the only nullable-by-design field within ``field_validations`` — § SVE → RFC 9457
    null-serialization divergence)."""
    entry: dict[str, Any] = {
        "field_path": fv.field_path,  # type: ignore[attr-defined]
        "expected_type": fv.expected_type,  # type: ignore[attr-defined]
        "actual_type": fv.actual_type,  # type: ignore[attr-defined]
        "constraint_violated": fv.constraint_violated,  # type: ignore[attr-defined]
        "message": fv.message,  # type: ignore[attr-defined]
    }
    _put(entry, "actual_value", fv.actual_value)  # type: ignore[attr-defined]
    return entry


def _pipeline_failure(pf: PipelineFailure, status_code: int) -> dict[str, Any]:
    """§ PipelineFailure → RFC 9457. ``status`` is caller-supplied (the server computes it
    from the per-class pin: 504 for ``TimeoutError``, else 502 for a ``service`` locus,
    else 500). Dispatch is on the ``cause_class`` extension member (PF is not
    audit-catalog-keyed per-instance)."""
    envelope: dict[str, Any] = {
        "type": _TYPE_NO_URI,
        "title": f"Pipeline failure — {pf.cause_class}",
        "status": status_code,
        "detail": pf.cause_message,
        "instance": pf.composition_ref,
        "failure_category": pf.failure_category,
        "cause_class": pf.cause_class,
        "cause_message": pf.cause_message,
        "failed_handler_qualified_name": pf.failed_handler_qualified_name,
        "failed_handler_position": pf.failed_handler_position,
        "pipeline_run_id": pf.pipeline_run_id,
        # The snapshots ride verbatim as objects (§ PipelineFailure → RFC 9457 extension
        # members: "verbatim object"). They are plain post-validation data (the PF
        # constructor deep-copies them at construction).
        "bindings_snapshot": _plain(pf.bindings_snapshot),
        "reads_snapshot": _plain(pf.reads_snapshot),
    }
    _put(envelope, "service_binding_name", pf.service_binding_name)
    _put(envelope, "elapsed_ms_at_failure", pf.elapsed_ms_at_failure)
    return envelope


def _plain(value: object) -> object:
    """Render a snapshot as JSON-native plain data — the snapshots are already plain
    post-validation data (the PF constructor's ``snapshot_copy`` converted the engine's
    frozen delivery forms back to dict / list / set), but ``set`` / ``frozenset`` /
    ``tuple`` are not JSON-native, so normalize them (sets → sorted-stable lists is not
    safe across unorderable members → list in iteration order; tuples → lists). Mappings
    and lists recurse; scalars pass through."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [_plain(v) for v in value]
    return value
