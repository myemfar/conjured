"""The registration API (``errors.CHECK_REGISTRY`` / ``errors.AUDIT_CODE_REGISTRY`` —
the STUB-R4 ruling, 2026-06-10): every :class:`Check` member is registered with its
enforced rule_ids + raising error class, and the constructors reject an unregistered
``audit_code`` / ``(check, rule_id)`` pair — so the generated error index
(``tools/gen_error_index.py``) is complete by construction. One test per guard path
(happy + each rejection), per the baseline standard."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from conjured.errors import (
    AUDIT_CODE_REGISTRY,
    CHECK_REGISTRY,
    INPUT_VALIDATION_AUDIT_CODE,
    OUTPUT_VALIDATION_AUDIT_CODE,
    PIPELINE_FAILURE_WRAP_AUDIT_CODE,
    Check,
    ContractViolation,
    FieldValidationDetail,
    SchemaValidationError,
)

# ---------------------------------------------------------------------------
# Registry completeness — the index's by-construction guarantee rests on these
# ---------------------------------------------------------------------------


def test_every_check_member_is_registered():
    """A Check member outside CHECK_REGISTRY would be a discriminator the generated
    index cannot carry — adding a member without registering it must be impossible
    to miss."""
    missing = set(Check) - set(CHECK_REGISTRY)
    assert not missing, f"unregistered Check members: {sorted(c.value for c in missing)}"


def test_registry_records_are_well_formed():
    for check, record in CHECK_REGISTRY.items():
        assert record.rule_ids, f"{check.value}: empty rule_ids"
        assert all(r.startswith("R-") for r in record.rule_ids), check.value
        assert record.rule_ids == tuple(sorted(record.rule_ids)), \
            f"{check.value}: rule_ids not sorted (the generated index must be deterministic)"
        assert record.error_class in (
            "ContractViolation", "SchemaValidationError", "PipelineFailure"
        ), check.value


def test_audit_code_registry_carries_exactly_the_decided_codes():
    """The two canon-decided SVE boundary codes + the single PipelineFailure wrap
    audit (Phase 3 — the B2 ruling) are registered; the deferred catalog grows this
    set by registration, never by free-form construction."""
    assert AUDIT_CODE_REGISTRY == {
        INPUT_VALIDATION_AUDIT_CODE: Check.HALT_ON_INPUT_VALIDATION_ERROR,
        OUTPUT_VALIDATION_AUDIT_CODE: Check.HALT_ON_SCHEMA_VALIDATION_ERROR,
        PIPELINE_FAILURE_WRAP_AUDIT_CODE: Check.PIPELINE_FAILURE_WRAP,
    }


# ---------------------------------------------------------------------------
# Conformance-catalog parity — the registry <-> conformance-docs gate
# ---------------------------------------------------------------------------
#
# The conformance catalog (conjured/docs/components/*/conformance.md) documents each
# registered Check; that coverage is prose, not a constructor seal, so this pair is the
# mechanical gate keeping the two in parity (the audit-1 blocking-2 full-parity fix, made
# self-enforcing). The 3 deployment-time hash-consistency sections (training-bundle-hash /
# pipeline-hash mismatch at load) carry NO Check discriminator — they are graduated-force,
# integrity_enforcement-gated — so they are neither registry members (direction A skips
# them) nor ``check `<slug>``` mentions (direction B skips them); the exclusion is
# structural, no hardcoded list required.

_CONFORMANCE_DIR = Path(__file__).resolve().parent.parent / "docs" / "components"
_CHECK_MENTION = re.compile(r"check `([a-z][a-z0-9-]+)`")


def _conformance_catalog() -> str:
    docs = sorted(_CONFORMANCE_DIR.glob("*/conformance.md"))
    assert docs, f"no conformance docs found under {_CONFORMANCE_DIR}"
    return "\n".join(p.read_text(encoding="utf-8") for p in docs)


def test_every_registered_check_is_documented_in_the_conformance_catalog():
    """Direction A (completeness): every CHECK_REGISTRY discriminator is named (backticked)
    somewhere in the conformance catalog. RED when a Check is added to the registry without a
    conformance entry — fix by documenting it in the owning component's conformance.md, never
    by trimming the registry."""
    text = _conformance_catalog()
    undocumented = sorted(c.value for c in Check if f"`{c.value}`" not in text)
    assert not undocumented, (
        "registered checks absent from the conformance catalog "
        f"(conjured/docs/components/*/conformance.md): {undocumented}"
    )


def test_conformance_catalog_names_only_registered_checks():
    """Direction B (no-stale): every ``check `<slug>``` mention in the conformance catalog
    resolves to a real CHECK_REGISTRY discriminator. RED when a check is renamed or removed in
    the registry but a conformance entry keeps the old slug — fix the conformance slug to match
    the registry (exactly the drift a rename introduces)."""
    registry = {c.value for c in Check}
    named = set(_CHECK_MENTION.findall(_conformance_catalog()))
    unknown = sorted(named - registry)
    assert not unknown, (
        "conformance catalog names checks absent from CHECK_REGISTRY "
        f"(renamed or removed?): {unknown}"
    )


# ---------------------------------------------------------------------------
# ContractViolation constructor seals
# ---------------------------------------------------------------------------


def _cv(**overrides):
    kwargs = dict(
        check=Check.CLOSED_GRAMMAR,
        rule_id="R-handler-006",
        expected="e",
        actual="a",
        file_path="x.toml",
    )
    kwargs.update(overrides)
    return ContractViolation(**kwargs)


def test_cv_accepts_a_registered_pair():
    exc = _cv()
    assert exc.check is Check.CLOSED_GRAMMAR
    assert exc.rule_id == "R-handler-006"


def test_cv_rejects_a_non_check_discriminator():
    with pytest.raises(ValueError, match="must be a Check member"):
        _cv(check="closed-grammar")


def test_cv_rejects_a_check_registered_to_another_error_class():
    """The two HALT_ON_* members are SVE's symbolic stand-ins; a ContractViolation
    carrying one would put the discriminator on the wrong class's index rows."""
    with pytest.raises(ValueError, match="registered to SchemaValidationError"):
        _cv(check=Check.HALT_ON_INPUT_VALIDATION_ERROR, rule_id="R-error-channel-003")


def test_cv_rejects_an_unregistered_rule_id_for_the_check():
    with pytest.raises(ValueError, match="not registered for check"):
        _cv(rule_id="R-handler-008")  # real rule, but not in CLOSED_GRAMMAR's registered set


def test_cv_rejects_an_unregistered_audit_code():
    # The seal message changed (surprise-fixes 3-code): membership-only → check-consistency.
    with pytest.raises(ValueError, match="not the catalog code registered for check"):
        _cv(audit_code="C2.FIELD_DISCIPLINE.001")  # canon example; catalog not yet landed


def test_cv_rejects_an_audit_code_registered_to_another_check():
    """The check-consistency seal (surprise-fixes 3-code): a non-None audit_code MUST be the
    catalog code registered for the CV's OWN check — not merely *a* registered code. A code
    belonging to another check (here an SVE boundary code) would land the discriminator on the
    wrong check's error-index rows. Membership-only acceptance was the latent gap the seal
    closes; RED if it reverts to ``audit_code not in AUDIT_CODE_REGISTRY`` (which admits any
    registered code regardless of check). No ContractViolation-class check carries an audit_code
    today, so a non-None audit_code on any CV is rejected — mirroring SVE's wrong-class seal, one
    step tighter."""
    with pytest.raises(ValueError, match="not the catalog code registered for check"):
        _cv(audit_code=OUTPUT_VALIDATION_AUDIT_CODE)  # a real code, but registered to an SVE check


# verifies: cv-requires-location
def test_cv_requires_a_location_bearing_field():
    """F3 regression: a ContractViolation must carry at least one location field; both
    file_path AND composition_ref absent is itself a violation
    (C1.CONTRACT_VIOLATION_SHAPE.003) — the constructor fails loud rather than emit a
    locationless diagnostic. The _cv helper always supplies file_path, so this branch
    was unexercised; RED if the location guard is removed."""
    with pytest.raises(ValueError, match="at least one of file_path / composition_ref"):
        _cv(file_path=None)  # composition_ref defaults to None → both absent


# verifies: cv-rendered-message-cites-rule
def test_cv_rendered_message_cites_its_rule_id_inline():
    """error-channel/reference.md § #rendered-message-cites-the-rule (the rule-bearing
    classes): a ContractViolation's default-rendered ``message`` MUST cite the enforcing
    ``rule_id`` inline — the message is self-steering (an agent reading only ``str(cv)``
    is routed to the governing derived rule without parsing the structured payload). The
    deferral is scoped to FORM only — "the citation's *presence* is the contract" — so the
    presence guarantee is in force now.

    Every other citation test asserts the STRUCTURED field (``cv.rule_id == ...``), so
    dropping the ``{rule_id}`` token from ``ContractViolation._render`` leaves the suite
    green; this asserts the RENDERED string. Presence-only (a substring), not form-pinning:
    the exact rendered form is the canonical default template's (ratified as shipped,
    2026-07-09). RED if the ``rule_id`` token is removed from
    ``_render``.

    (The discriminator-slot sibling test below covers the audit-code half: the slot
    renders the catalog code once assigned, ``check.value`` until then.)"""
    cv = _cv()  # rule_id "R-handler-006"
    assert cv.rule_id in str(cv)


# verifies: cv-rendered-message-cites-rule
def test_cv_discriminator_slot_renders_the_audit_code_once_assigned():
    """The discriminator slot rule (ratified 2026-07-09): the rendered message's
    parenthesized discriminator is the catalog ``audit_code`` once assigned, the
    symbolic ``check`` value until then — the message satisfies the cites-rule contract
    in both catalog eras with no template change (error-channel/reference.md § The
    rendered message cites its rule; SVE, whose codes ARE assigned, already renders its
    code there).

    No ContractViolation check carries a registered catalog code today (the constructor
    rejects any non-None ``audit_code``), so the assigned-era branch is exercised on the
    template method directly over a constructed instance — real code, no engine double.
    RED if the slot rule is removed from ``_render``."""
    cv = _cv()
    assert f"({cv.check.value})" in str(cv)  # unassigned era: the symbolic check renders
    cv.audit_code = "C1.CLOSED_GRAMMAR.001"  # the assigned era, on the instance
    rendered = cv._render()
    assert "(C1.CLOSED_GRAMMAR.001)" in rendered
    assert f"({cv.check.value})" not in rendered  # the slot swaps; it never appends


# ---------------------------------------------------------------------------
# SchemaValidationError constructor seals
# ---------------------------------------------------------------------------

_DETAIL = FieldValidationDetail(
    field_path="output_schema.x",
    expected_type="str",
    actual_type="int",
    actual_value="1",
    constraint_violated="type",
    message="expected str, got int 1",
)


def _sve(**overrides):
    kwargs = dict(
        audit_code=OUTPUT_VALIDATION_AUDIT_CODE,
        handler_qualified_name="acme.h",
        handler_position=0,
        pipeline_run_id="run_2026-06-10T00:00:00Z_t3st",
        schema_source="handlers/h.toml",
        field_validations=(_DETAIL,),
    )
    kwargs.update(overrides)
    return SchemaValidationError(**kwargs)


def test_sve_accepts_both_registered_codes():
    assert _sve().audit_code == OUTPUT_VALIDATION_AUDIT_CODE
    assert _sve(audit_code=INPUT_VALIDATION_AUDIT_CODE).audit_code == INPUT_VALIDATION_AUDIT_CODE


def test_sve_rejects_an_unregistered_audit_code():
    with pytest.raises(ValueError, match="not a registered catalog code"):
        _sve(audit_code="C1.HALT_ON_SCHEMA_VALIDATION_ERROR.999")


def test_sve_rejects_an_audit_code_registered_to_another_error_class():
    """The mirror of CV's wrong-class seal: the PipelineFailure wrap audit on an SVE
    would put the discriminator on the wrong class's index rows."""
    with pytest.raises(ValueError, match="registered to PipelineFailure"):
        _sve(audit_code=PIPELINE_FAILURE_WRAP_AUDIT_CODE)


def test_sve_rejects_a_rule_id_outside_the_codes_registered_set():
    with pytest.raises(ValueError, match="not registered for audit_code"):
        _sve(rule_id="R-handler-001")


# verifies: sve-rendered-message-cites-rule-and-audit-code
def test_sve_rendered_message_cites_rule_id_and_audit_code_inline():
    """error-channel/reference.md § #rendered-message-cites-the-rule: a
    SchemaValidationError's default-rendered ``message`` MUST cite BOTH the enforcing
    ``rule_id`` AND its ``audit_code`` inline (SVE's two boundary audit codes are
    canon-decided, unlike ContractViolation's deferred catalog). The message is
    self-steering — an agent reading only ``str(sve)`` is routed to the rule and the
    audit-catalog entry. Presence is the contract; the rendered form is the canonical
    default template's (ratified as shipped, 2026-07-09).

    The existing SVE citation tests assert the structured fields (``sve.audit_code == ...``),
    so dropping either token from ``SchemaValidationError._render`` stays green; this
    asserts the rendered string. Presence-only (substring), not form-pinning. RED if
    EITHER the ``rule_id`` or the ``audit_code`` token is removed from ``_render``."""
    sve = _sve()  # rule_id "R-error-channel-003", audit_code OUTPUT_VALIDATION_AUDIT_CODE
    rendered = str(sve)
    assert sve.rule_id in rendered
    assert sve.audit_code in rendered
