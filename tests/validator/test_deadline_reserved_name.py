"""The engine-reserved deadline-propagation kwarg name (service-type/reference.md
§ Deadline propagation): a ``[config_schema]`` / ``[transport_schema]`` field named
``remaining_budget_ms`` is rejected at declaration load — the runner is the kwarg's only
supplier, so a declared field under the name would make one kwarg two-sourced."""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation
from conjured.validator import loads

_BASE = """
name = "st.x"
[identity_schema]
model = {{ type = "str" }}
[transport_schema]
{transport}
[config_schema]
{config}
"""


def test_config_field_under_the_reserved_name_is_rejected():
    with pytest.raises(ContractViolation) as exc:
        loads(
            _BASE.format(transport='endpoint = { type = "str" }',
                         config='remaining_budget_ms = { type = "int" }'),
            "service_type", file_path="st.toml",
        )
    assert exc.value.check is Check.NAME_UNIQUENESS
    assert exc.value.rule_id == "R-service-type-001"
    assert "remaining_budget_ms" in str(exc.value)


def test_transport_field_under_the_reserved_name_is_rejected():
    with pytest.raises(ContractViolation) as exc:
        loads(
            _BASE.format(transport='remaining_budget_ms = { type = "int" }',
                         config=""),
            "service_type", file_path="st.toml",
        )
    assert exc.value.check is Check.NAME_UNIQUENESS
    assert exc.value.rule_id == "R-service-type-001"


def test_other_field_names_stay_legal():
    declaration = loads(
        _BASE.format(transport='endpoint = { type = "str" }',
                     config='budget_hint_ms = { type = "int" }'),
        "service_type", file_path="st.toml",
    )
    assert {f.name for f in declaration.config_schema} == {"budget_hint_ms"}
