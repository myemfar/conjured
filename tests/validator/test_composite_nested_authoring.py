"""The composite-slot authoring convention — a TOML-declared list / dict of structured
records (handler/reference.md § Types allowed): ``[<sec>.<field>.item.fields]`` →
``ListType(item=NestedType)``, ``[<sec>.<field>.value.fields]`` →
``DictType(value=NestedType)``, recursing (``.item.item.fields``). Driven through the
real declaration loader (``loads``) — the authoring surface under test IS the TOML
grammar — plus a model-gen round trip proving the parsed IR validates real record
values (the IR/hash/dispatch layers predate this surface; the round trip pins that
claim rather than assuming it).
"""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import DictType, ListType, NestedType, OptionalType
from conjured.validator import loads


def _handler(reads_extra: str = "", output_extra: str = "") -> str:
    return f"""
[transform]
[reads]
raw = {{ type = "str" }}
{reads_extra}
[output_schema]
status = {{ type = "str" }}
{output_extra}
"""


def _load(toml_text: str):
    return loads(toml_text, "handler", file_path="composite.toml")


def _field(decl, section: str, name: str):
    return next(f for f in getattr(decl, section) if f.name == name)


# ---------------------------------------------------------------------------
# Happy paths — the two slots, recursion, and composition with the field grammar
# ---------------------------------------------------------------------------


def test_list_of_nested_records_parses(  ):
    """`[output_schema.history.item.fields]` → ListType(item=NestedType) with the
    member declarations intact — FM's typed conversation_history shape."""
    decl = _load(_handler(output_extra="""
[output_schema.history.item.fields]
speaker = { type = "str" }
line    = { type = "str" }
"""))
    history = _field(decl, "output_schema", "history")
    assert isinstance(history.type, ListType)
    assert isinstance(history.type.item, NestedType)
    assert [f.name for f in history.type.item.fields] == ["speaker", "line"]


def test_dict_of_nested_records_parses():
    """`[reads.sheets.value.fields]` → DictType(value=NestedType)."""
    decl = _load(_handler(reads_extra="""
[reads.sheets.value.fields]
name = { type = "str" }
role = { type = "str" }
"""))
    sheets = _field(decl, "reads", "sheets")
    assert isinstance(sheets.type, DictType)
    assert isinstance(sheets.type.value, NestedType)
    assert [f.name for f in sheets.type.value.fields] == ["name", "role"]


def test_bare_string_field_type_is_exact_sugar_for_the_table_form():
    """``mood = "float"`` is exact shorthand for ``mood = { type = "float" }`` — the two
    spellings parse to the IDENTICAL FieldDecl IR (handler/reference.md § field-type discipline:
    "hash-neutral — the canonical IR erases the difference"). Asserting FieldDecl equality proves
    the canon claim directly and subsumes the hash-neutrality half (the pipeline-hash is a pure
    function of the IR — equal IR ⇒ equal hash). RED-on-removal for the parser's bare-string sugar
    branch (tokens.py): without it the bare form — the PRIMARY spelling in canon's worked examples —
    fails to parse or yields a different FieldDecl, so this equality breaks."""
    bare = _load(_handler(reads_extra='mood = "float"\n'))
    table = _load(_handler(reads_extra='mood = { type = "float" }\n'))
    assert _field(bare, "reads", "mood") == _field(table, "reads", "mood")


def test_composite_slots_recurse():
    """`.item.item.fields` = list-of-list-of-nested — recursion composes naturally."""
    decl = _load(_handler(output_extra="""
[output_schema.batches.item.item.fields]
fact = { type = "str" }
"""))
    batches = _field(decl, "output_schema", "batches")
    assert isinstance(batches.type, ListType)
    assert isinstance(batches.type.item, ListType)
    assert isinstance(batches.type.item.item, NestedType)


def test_nested_member_may_itself_be_a_composite_slot():
    """A record member may declare its own composite slot — a record carrying a list
    of sub-records (the memory_extract quote-evidence shape)."""
    decl = _load(_handler(output_extra="""
[output_schema.finding.fields]
claim = { type = "str" }
[output_schema.finding.fields.evidence.item.fields]
quote   = { type = "str" }
said_by = { type = "str" }
"""))
    finding = _field(decl, "output_schema", "finding")
    assert isinstance(finding.type, NestedType)
    evidence = next(f for f in finding.type.fields if f.name == "evidence")
    assert isinstance(evidence.type, ListType)
    assert isinstance(evidence.type.item, NestedType)


def test_validation_keywords_attach_on_the_fields_own_table():
    """minItems/uniqueItems ride the FIELD's table beside the composite slot — the
    existing keyword grammar composes unchanged."""
    decl = _load(_handler(output_extra="""
[output_schema.history]
minItems = 1
[output_schema.history.item.fields]
speaker = { type = "str" }
"""))
    history = _field(decl, "output_schema", "history")
    assert isinstance(history.type, ListType)
    assert [v.name for v in history.validators] == ["minItems"]


def test_nullable_wraps_the_composite_exactly_as_it_wraps_fields():
    """`nullable = true` beside `item` yields Optional(list[nested]) — the same
    composition `nullable` already performs beside every other shape marker."""
    decl = _load(_handler(output_extra="""
[output_schema.history]
nullable = true
[output_schema.history.item.fields]
speaker = { type = "str" }
"""))
    history = _field(decl, "output_schema", "history")
    assert isinstance(history.type, OptionalType)
    assert isinstance(history.type.inner, ListType)


def test_nullable_wraps_a_bare_nested_object_yielding_optional_of_nested():
    """`nullable = true` beside `.fields` yields Optional(<nested record>) — the same
    composition `nullable` performs beside `.item`/`.value`, via the field-level metadata
    key (canon's general nullable rule), NOT via a nested ELEMENT (which admits no
    `nullable`). Pins handler/reference.md's scoped optional-of-nested clause: it is the
    token / element grammar that cannot spell it, not the field level."""
    decl = _load(_handler(output_extra="""
[output_schema.mood]
nullable = true
[output_schema.mood.fields]
label = { type = "str" }
"""))
    mood = _field(decl, "output_schema", "mood")
    assert isinstance(mood.type, OptionalType)
    assert isinstance(mood.type.inner, NestedType)
    assert [f.name for f in mood.type.inner.fields] == ["label"]


def test_model_gen_validates_record_values_against_the_parsed_shape():
    """The IR round trip: a conforming list-of-records value validates; a record
    missing a declared member is rejected — proving the pre-existing model-gen path
    handles the authored shape (the catalog's 'IR needs nothing' claim, pinned)."""
    from conjured.validator.model_gen import build_model

    decl = _load(_handler(output_extra="""
[output_schema.history.item.fields]
speaker = { type = "str" }
line    = { type = "str" }
"""))
    model = build_model("out", decl.output_schema)
    ok = model.model_validate({
        "status": "ok",
        "history": [{"speaker": "npc", "line": "hello"}, {"speaker": "player", "line": "hi"}],
    })
    assert ok.history[0].speaker == "npc"
    with pytest.raises(Exception):
        model.model_validate({"status": "ok", "history": [{"speaker": "npc"}]})


# ---------------------------------------------------------------------------
# Error paths — every malformation is a structured ContractViolation
# ---------------------------------------------------------------------------


def _cv(toml_text: str) -> ContractViolation:
    with pytest.raises(ContractViolation) as exc:
        _load(toml_text)
    return exc.value


def test_two_shape_markers_on_one_field_raise():
    cv = _cv(_handler(output_extra="""
[output_schema.history]
type = "str"
[output_schema.history.item.fields]
speaker = { type = "str" }
"""))
    assert cv.check is Check.MALFORMED_DECLARATION
    assert "exactly one shape marker" in cv.actual


def test_element_table_with_no_shape_key_raises():
    cv = _cv(_handler(output_extra="""
[output_schema.history.item]
"""))
    assert cv.check is Check.MALFORMED_DECLARATION
    assert "exactly one shape key" in cv.actual


def test_element_table_with_extra_keys_raises():
    """Keywords / nullable inside the ELEMENT table are rejected — validation attaches
    on the field's own table, and optional-of-nested stays inexpressible."""
    cv = _cv(_handler(output_extra="""
[output_schema.history.item]
nullable = true
[output_schema.history.item.fields]
speaker = { type = "str" }
"""))
    assert cv.check is Check.MALFORMED_DECLARATION
    assert "nothing else" in cv.actual


def test_non_table_element_raises():
    cv = _cv(_handler(output_extra="""
[output_schema.history]
item = "str"
"""))
    assert cv.check is Check.MALFORMED_DECLARATION
    assert "sub-table" in cv.actual


def test_empty_fields_inside_an_element_raises():
    cv = _cv(_handler(output_extra="""
[output_schema.history.item]
fields = {}
"""))
    assert cv.check is Check.MALFORMED_DECLARATION
    assert "non-empty" in cv.actual
