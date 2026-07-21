"""The ``table`` token + the trainable members' ``extras`` table (D3) — the parse
surface (admissible only in a service-type ``[config_schema]``, JSON-expressible values
only) and the compose-time JSON-expressibility check on the effective value. The wire
delivery + the reserved-wire-key disjointness live in the adapter/lib suites."""

from __future__ import annotations

import datetime
import tomllib

import pytest

from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import TableType
from conjured.validator.compile import effective_config
from conjured.validator.parse import parse_service_type
from conjured.validator.tokens import parse_type_token


# ---------------------------------------------------------------------------
# The token: admissible only as a [config_schema] field's top-level type
# ---------------------------------------------------------------------------


def test_table_token_parses_under_allow_table():
    assert isinstance(parse_type_token("table", file_path="st.toml", allow_table=True), TableType)


def test_table_token_rejects_without_allow_table():
    # reads / output_schema / identity / transport never thread allow_table.
    with pytest.raises(ContractViolation) as exc:
        parse_type_token("table", file_path="h.toml")
    assert exc.value.check is Check.CHANNEL_TYPE_TOKEN
    assert "config_schema" in exc.value.actual


def test_table_is_not_nestable_in_a_collection_or_optional():
    # `table` is a top-level config field type only (the shipped `extras` use); it does
    # not propagate into the collection / optional recursion.
    for token in ("list[table]", "dict[str, table]", "table | None"):
        with pytest.raises(ContractViolation) as exc:
            parse_type_token(token, file_path="st.toml", allow_table=True)
        assert exc.value.check is Check.CHANNEL_TYPE_TOKEN


SERVICE_TYPE_WITH_EXTRAS = """
name = "acme.wire"
[identity_schema]
model = { type = "str" }
[transport_schema]
endpoint = { type = "str" }
[config_schema]
temperature = { type = "float", default = 0.7 }
extras = { type = "table", default = {} }
"""


def test_config_schema_admits_a_table_field_with_an_empty_default():
    st = parse_service_type(
        tomllib.loads(SERVICE_TYPE_WITH_EXTRAS), file_path="st.toml"
    )
    extras = {f.name: f for f in st.config_schema}["extras"]
    assert isinstance(extras.type, TableType)
    assert extras.has_default and extras.default == {}


def test_table_field_in_identity_schema_rejects():
    # identity_schema does not thread allow_table — `table` there is an unknown token.
    bad = (
        'name = "acme.x"\n[identity_schema]\nm = { type = "table" }\n'
        '[transport_schema]\ne = { type = "str" }\n[config_schema]\n'
    )
    with pytest.raises(ContractViolation) as exc:
        parse_service_type(tomllib.loads(bad), file_path="st.toml")
    assert exc.value.check is Check.CHANNEL_TYPE_TOKEN


def test_table_default_with_a_datetime_rejects_at_declaration_load():
    # tomllib parses a bare TOML datetime into a datetime object — not JSON-expressible,
    # so a table field's declared default carrying one raises at service-type load.
    bad = (
        'name = "acme.x"\n[identity_schema]\nm = { type = "str" }\n'
        '[transport_schema]\ne = { type = "str" }\n'
        '[config_schema]\nextras = { type = "table", default = { created = 2020-01-01T00:00:00 } }\n'
    )
    with pytest.raises(ContractViolation) as exc:
        parse_service_type(tomllib.loads(bad), file_path="st.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert "JSON-expressible" in exc.value.actual


def test_table_default_with_nested_json_data_parses():
    ok = (
        'name = "acme.x"\n[identity_schema]\nm = { type = "str" }\n'
        '[transport_schema]\ne = { type = "str" }\n'
        '[config_schema]\nextras = { type = "table", default = { top_p = 0.9, stops = ["a", "b"], nested = { k = 1 } } }\n'
    )
    st = parse_service_type(tomllib.loads(ok), file_path="st.toml")
    extras = {f.name: f for f in st.config_schema}["extras"]
    assert extras.default == {"top_p": 0.9, "stops": ["a", "b"], "nested": {"k": 1}}


# ---------------------------------------------------------------------------
# The compose-time JSON-expressibility check on the effective (supplied) value
# ---------------------------------------------------------------------------


def _service_type():
    return parse_service_type(
        tomllib.loads(SERVICE_TYPE_WITH_EXTRAS), file_path="st.toml"
    )


def test_supplied_table_with_json_values_passes_effective_config():
    effective = effective_config(
        {"extras": {"top_p": 0.9, "top_k": 40}}, _service_type(),
        composition_ref="acme.p", section_path="trainable.config",
    )
    assert effective["extras"] == {"top_p": 0.9, "top_k": 40}
    assert effective["temperature"] == 0.7  # the dial default folds too


def test_supplied_table_with_a_datetime_rejects_at_compose():
    with pytest.raises(ContractViolation) as exc:
        effective_config(
            {"extras": {"created": datetime.datetime(2020, 1, 1)}}, _service_type(),
            composition_ref="acme.p", section_path="trainable.config",
        )
    cv = exc.value
    assert cv.check is Check.CONFIG_SCHEMA_SUPPLY
    assert cv.rule_id == "R-service-type-002"
    assert "datetime" in cv.actual
    assert cv.section_path == "trainable.config.extras"


def test_omitted_extras_folds_the_empty_default_and_passes():
    # extras default = {} — a composition supplying nothing gets the empty table; coverage
    # never forces it, and the empty table is trivially JSON-expressible.
    effective = effective_config(
        {"temperature": 0.5}, _service_type(),
        composition_ref="acme.p", section_path="trainable.config",
    )
    assert effective["extras"] == {}
