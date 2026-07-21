"""The ``FieldDecl`` → Pydantic model generator (``validator.model_gen``) — every
member of the closed allowed-type set realizes and validates; the generated models are
closed (``extra="forbid"``) and strict (no lax coercion); ``optional`` is required-
with-no-default (I1). The exhaustiveness check confirms the generator's realized-kind
set equals ``CHANNEL_TYPE_TABLE``'s, so a table extension cannot silently outrun it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from conjured.ir.channel_types import (
    CHANNEL_TYPE_TABLE,
    FieldDecl,
    canonical_token,
    dict_of,
    list_of,
    literal,
    nested,
    optional,
    primitive,
    tuple_of,
)
from conjured.validator.model_gen import REALIZED_KINDS, build_model


def _model(*fields: FieldDecl):
    return build_model("M", tuple(fields))


# --- exhaustiveness (the allowed-type set is closed) ----------------------------------


def test_generator_covers_every_table_kind():
    table_kinds = {row.kind for row in CHANNEL_TYPE_TABLE}
    assert REALIZED_KINDS == table_kinds


# --- every kind realizes + validates --------------------------------------------------


def test_primitives_realize_and_validate():
    M = _model(
        FieldDecl(name="s", type=primitive("str")),
        FieldDecl(name="i", type=primitive("int")),
        FieldDecl(name="f", type=primitive("float")),
        FieldDecl(name="b", type=primitive("bool")),
    )
    got = M.model_validate({"s": "x", "i": 1, "f": 1.5, "b": True})
    assert (got.s, got.i, got.f, got.b) == ("x", 1, 1.5, True)


def test_bytes_realizes_non_lcd_primitive():
    M = _model(FieldDecl(name="raw", type=primitive("bytes")))
    assert M.model_validate({"raw": b"\x00"}).raw == b"\x00"


def test_collections_realize_and_validate():
    M = _model(
        FieldDecl(name="xs", type=list_of(primitive("float"))),
        FieldDecl(name="kv", type=dict_of(primitive("str"))),
        FieldDecl(name="pair", type=tuple_of(primitive("int"), primitive("str"))),
    )
    got = M.model_validate({"xs": [1.0], "kv": {"a": "b"}, "pair": (1, "z")})
    assert got.pair == (1, "z")


def test_tuple_arity_is_fixed():
    M = _model(FieldDecl(name="pair", type=tuple_of(primitive("int"), primitive("str"))))
    with pytest.raises(ValidationError):
        M.model_validate({"pair": (1, "z", "extra")})


def test_literal_closed_enum():
    M = _model(FieldDecl(name="mood", type=literal("happy", "sad")))
    assert M.model_validate({"mood": "happy"}).mood == "happy"
    with pytest.raises(ValidationError) as exc:
        M.model_validate({"mood": "confused"})
    assert exc.value.errors()[0]["type"] == "literal_error"


def test_literal_membership_is_exact_type():
    # Bare Pydantic Literal matches by equality (True == 1) — the generator must not
    # admit a bool into an int literal set or vice versa (a masked re-typing).
    M_int = _model(FieldDecl(name="n", type=literal(1, 2)))
    assert M_int.model_validate({"n": 1}).n == 1
    for out_of_set in (True, 1.0, "1"):
        with pytest.raises(ValidationError) as exc:
            M_int.model_validate({"n": out_of_set})
        assert exc.value.errors()[0]["type"] == "literal_error"
    M_bool = _model(FieldDecl(name="flag", type=literal(True)))
    assert M_bool.model_validate({"flag": True}).flag is True
    with pytest.raises(ValidationError):
        M_bool.model_validate({"flag": 1})


def test_nested_recurses_to_depth():
    M = _model(
        FieldDecl(
            name="mood",
            type=nested(
                FieldDecl(name="intensity", type=primitive("int")),
                FieldDecl(
                    name="source",
                    type=nested(FieldDecl(name="confidence", type=primitive("float"))),
                ),
            ),
        )
    )
    got = M.model_validate({"mood": {"intensity": 3, "source": {"confidence": 0.9}}})
    assert got.mood.source.confidence == 0.9
    with pytest.raises(ValidationError) as exc:
        M.model_validate({"mood": {"intensity": 3, "source": {}}})
    assert exc.value.errors()[0]["loc"] == ("mood", "source", "confidence")


# --- optional: value-nullability is a separate axis from key-presence (I1) ------------


def test_optional_is_required_with_no_default():
    M = _model(FieldDecl(name="hint", type=optional(primitive("str"))))
    assert M.model_validate({"hint": None}).hint is None
    assert M.model_validate({"hint": "x"}).hint == "x"
    with pytest.raises(ValidationError) as exc:
        M.model_validate({})  # the key itself is never optional
    assert exc.value.errors()[0]["type"] == "missing"


# --- closed + strict -------------------------------------------------------------------


def test_undeclared_key_is_structurally_rejected():
    M = _model(FieldDecl(name="a", type=primitive("int")))
    with pytest.raises(ValidationError) as exc:
        M.model_validate({"a": 1, "zzz": 2})
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_strict_no_lax_coercion():
    # error-channel: "a declared int field receives str" is an SVE example — the
    # generated model must reject it, never silently parse "5" -> 5.
    M = _model(FieldDecl(name="a", type=primitive("int")))
    with pytest.raises(ValidationError):
        M.model_validate({"a": "5"})
    with pytest.raises(ValidationError):
        M.model_validate({"a": True})  # bool is not int under strict


# --- the canonical token renderer (the SVE expected_type form) -------------------------


def test_canonical_token_round_trip():
    assert canonical_token(list_of(primitive("float"))) == "list[float]"
    assert canonical_token(dict_of(primitive("str"))) == "dict[str, str]"
    assert canonical_token(optional(primitive("int"))) == "int | None"
    assert canonical_token(tuple_of(primitive("int"), primitive("str"))) == "tuple[int, str]"
    assert canonical_token(literal("a", "b")) == "Literal['a', 'b']"
    assert canonical_token(nested(FieldDecl(name="x", type=primitive("int")))) == "nested object"
