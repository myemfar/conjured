"""ContractViolationGroup — the compose-time multi-violation container (error-channel
/reference.md § ContractViolationGroup). Class-level construction + payload guarantees,
distinct from the compile-level aggregation behavior (tests/validator/test_aggregation.py).
"""

from __future__ import annotations

import pytest

from conjured.errors import (
    Check,
    ConjuredError,
    ContractViolation,
    ContractViolationGroup,
)


def _cv(actual: str) -> ContractViolation:
    return ContractViolation(
        check=Check.READ_WRITE_SHAPE, rule_id="R-pipeline-001",
        expected="one type per channel", actual=actual, composition_ref="acme.p",
    )


def test_wraps_two_or_more_violations():
    a, b = _cv("a"), _cv("b")
    group = ContractViolationGroup((a, b))
    assert group.violations == (a, b)
    # Each member keeps its own complete ContractViolation payload.
    assert [v.actual for v in group.violations] == ["a", "b"]


def test_message_names_count_and_every_member():
    group = ContractViolationGroup((_cv("first"), _cv("second")))
    msg = str(group)
    assert msg.startswith("2 contract violations:")
    assert "first" in msg and "second" in msg  # every aggregated failure is visible in the string


def test_is_a_conjured_error_but_not_a_contract_violation():
    """It is catchable in the engine error hierarchy (ConjuredError) but is NOT a
    ContractViolation — it is a container, not a fourth error class, so it never
    masquerades as a member of the closed enum."""
    group = ContractViolationGroup((_cv("a"), _cv("b")))
    assert isinstance(group, ConjuredError)
    assert not isinstance(group, ContractViolation)


def test_rejects_fewer_than_two():
    """A single violation raises the bare ContractViolation; a one-or-zero group is a
    construction bug — fail loud (the ≥2 requirement is structural)."""
    with pytest.raises(ValueError, match="two or more"):
        ContractViolationGroup((_cv("solo"),))
    with pytest.raises(ValueError, match="two or more"):
        ContractViolationGroup(())


def test_rejects_non_contract_violation_members():
    """The container holds class-1 ContractViolations only — never another error class or
    arbitrary object (it is not a fourth error class that could wrap another class)."""
    with pytest.raises(ValueError, match="ContractViolation instances only"):
        ContractViolationGroup((_cv("a"), object()))  # type: ignore[arg-type]
