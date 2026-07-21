"""``conjured.adapters.wire`` — the canonical strict-constraint renderer (the
literal-equal artifact's wire rendering, R-handler-005) and the input-payload
serialization (the property-4 two-case split), every happy and rejection path."""

from __future__ import annotations

import pytest

from conjured.adapters.wire import (
    render_input_payload,
    render_output_constraint,
)
from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import (
    FieldDecl,
    ValidatorSpec,
    dict_of,
    list_of,
    literal,
    nested,
    optional,
    primitive,
    tuple_of,
)

SOURCE = "compositions/fixture.toml"


def render(*fields):
    return render_output_constraint(tuple(fields), schema_source=SOURCE)


# ---------------------------------------------------------------------------
# Happy renders — every IR type token to its canonical node
# ---------------------------------------------------------------------------


def test_primitives_render_to_json_schema_types():
    schema = render(
        FieldDecl(name="s", type=primitive("str")),
        FieldDecl(name="i", type=primitive("int")),
        FieldDecl(name="f", type=primitive("float")),
        FieldDecl(name="b", type=primitive("bool")),
    )
    assert schema == {
        "type": "object",
        "properties": {
            "s": {"type": "string"},
            "i": {"type": "integer"},
            "f": {"type": "number"},
            "b": {"type": "boolean"},
        },
        "required": ["s", "i", "f", "b"],
        "additionalProperties": False,
    }


def test_composites_render_to_their_canonical_nodes():
    schema = render(
        FieldDecl(name="tags", type=list_of(primitive("str"))),
        FieldDecl(name="aliases", type=dict_of(primitive("int"))),
        FieldDecl(name="note", type=optional(primitive("str"))),
        FieldDecl(name="mood", type=literal("happy", "sad")),
    )
    properties = schema["properties"]
    assert properties["tags"] == {"type": "array", "items": {"type": "string"}}
    assert properties["aliases"] == {
        "type": "object",
        "additionalProperties": {"type": "integer"},
    }
    assert properties["note"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    }
    assert properties["mood"] == {"enum": ["happy", "sad"]}


def test_literal_admits_int_and_bool_members():
    schema = render(FieldDecl(name="pick", type=literal("a", 2, True)))
    assert schema["properties"]["pick"] == {"enum": ["a", 2, True]}


def test_nested_objects_recurse_closed_at_every_level():
    schema = render(
        FieldDecl(
            name="mood",
            type=nested(
                FieldDecl(name="intensity", type=primitive("int")),
                FieldDecl(
                    name="source",
                    type=nested(FieldDecl(name="model", type=primitive("str"))),
                ),
            ),
        )
    )
    mood = schema["properties"]["mood"]
    assert mood["additionalProperties"] is False
    assert mood["required"] == ["intensity", "source"]
    source = mood["properties"]["source"]
    assert source["additionalProperties"] is False
    assert source["properties"]["model"] == {"type": "string"}


def test_descriptions_are_carried_and_rendering_is_deterministic():
    fields = (
        FieldDecl(name="dialogue", type=primitive("str"), description="The NPC line."),
    )
    schema = render(*fields)
    assert schema["properties"]["dialogue"]["description"] == "The NPC line."
    assert render(*fields) == schema  # byte-stable across renders


# ---------------------------------------------------------------------------
# The compose-time caveat — rejections carry the exact structured class
# ---------------------------------------------------------------------------


def test_field_validators_reject_with_the_constraint_unsupported_check():
    field = FieldDecl(
        name="count", type=primitive("int"),
        validators=(ValidatorSpec(name="minimum", params={"limit": 1}),),
    )
    with pytest.raises(ContractViolation) as exc:
        render(field)
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert exc.value.rule_id == "R-handler-005"
    assert exc.value.file_path == SOURCE
    assert exc.value.section_path == "trainable.output_schema"
    assert "minimum" in exc.value.actual and "count" in exc.value.actual


def test_nested_field_validators_reject_with_the_member_path():
    field = FieldDecl(
        name="mood",
        type=nested(
            FieldDecl(
                name="intensity", type=primitive("int"),
                validators=(ValidatorSpec(name="maximum", params={"limit": 10}),),
            )
        ),
    )
    with pytest.raises(ContractViolation) as exc:
        render(field)
    assert "mood.intensity" in exc.value.actual


def test_bytes_channel_rejects_no_json_wire_rendering():
    with pytest.raises(ContractViolation) as exc:
        render(FieldDecl(name="blob", type=primitive("bytes")))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert "bytes" in exc.value.actual


def test_tuple_channel_rejects_json_wire_cannot_close_the_seal():
    # A JSON wire delivers arrays; the engine's strict generated model rejects a list
    # against a declared tuple port — the literal-equal seal cannot close end-to-end,
    # so the declaration fails at compose, never per dispatch.
    with pytest.raises(ContractViolation) as exc:
        render(
            FieldDecl(name="pair", type=tuple_of(primitive("str"), primitive("int")))
        )
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert exc.value.rule_id == "R-handler-005"
    assert "tuple" in exc.value.actual and "pair" in exc.value.actual


# ---------------------------------------------------------------------------
# The accepted matrix (D2) — render in-set keywords, reject out-of-set + dotted
# ---------------------------------------------------------------------------


def render_with(*fields, accepted, wire="test-wire"):
    return render_output_constraint(
        tuple(fields), schema_source=SOURCE, accepted_keywords=accepted, wire=wire
    )


def test_accepted_enum_keyword_renders_into_the_property_node():
    # An `enum` constraint in the wire's accepted set renders into the submitted
    # constraint AS the JSON-Schema `enum` keyword (the seal stays literal-equal — the
    # engine model enforces the same membership via the constraint shim).
    schema = render_with(
        FieldDecl(
            name="mood", type=primitive("str"),
            validators=(ValidatorSpec(name="enum", params={"values": ["happy", "sad"]}),),
        ),
        accepted=frozenset({"enum"}),
    )
    assert schema["properties"]["mood"] == {"type": "string", "enum": ["happy", "sad"]}


def test_subset_enum_on_a_literal_field_wire_submits_the_intersection():
    # Under the enum-on-Literal subset guarantee (enforced at compose in resolve_validator), the
    # wire's overwrite of a LiteralType's type-level enum node with the enum-validator's values is
    # BENIGN: values ⊆ members, so the submitted `enum` = the validator values = the type∩enum
    # intersection = the correct predicate. The type node rendered {enum:[happy,sad,angry]}; the
    # validator's subset [happy,sad] overwrites it, and because it IS a subset the overwrite equals
    # the intersection — the literal-equal seal (R-handler-005) holds by construction, not by luck.
    schema = render_with(
        FieldDecl(
            name="mood", type=literal("happy", "sad", "angry"),
            validators=(ValidatorSpec(name="enum", params={"values": ["happy", "sad"]}),),
        ),
        accepted=frozenset({"enum"}),
    )
    assert schema["properties"]["mood"] == {"enum": ["happy", "sad"]}


def test_accepted_length_keywords_render_on_a_string():
    schema = render_with(
        FieldDecl(
            name="code", type=primitive("str"),
            validators=(
                ValidatorSpec(name="minLength", params={"limit": 4}),
                ValidatorSpec(name="maxLength", params={"limit": 4}),
            ),
        ),
        accepted=frozenset({"minLength", "maxLength"}),
    )
    assert schema["properties"]["code"] == {
        "type": "string", "minLength": 4, "maxLength": 4,
    }


def test_enum_on_optional_merges_into_the_non_null_branch():
    schema = render_with(
        FieldDecl(
            name="tag", type=optional(primitive("str")),
            validators=(ValidatorSpec(name="enum", params={"values": ["a", "b"]}),),
        ),
        accepted=frozenset({"enum"}),
    )
    assert schema["properties"]["tag"] == {
        "anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}]
    }


def test_out_of_set_keyword_rejects_naming_the_keyword_and_wire():
    # minLength is renderable in general, but NOT in this wire's accepted set ({enum}) —
    # rejected at compose naming the keyword + the wire (the accepted matrix is per-family).
    field = FieldDecl(
        name="code", type=primitive("str"),
        validators=(ValidatorSpec(name="minLength", params={"limit": 4}),),
    )
    with pytest.raises(ContractViolation) as exc:
        render_with(field, accepted=frozenset({"enum"}), wire="strict-json")
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert "minLength" in exc.value.actual
    assert "strict-json" in exc.value.actual  # names the wire
    assert "code" in exc.value.actual


def test_dotted_validator_key_is_never_render_eligible():
    # A namespaced (dotted) validator key is opaque third-party code — rejected even when
    # the family's accepted set is wide; it is never render-eligible on any wire.
    field = FieldDecl(
        name="label", type=primitive("str"),
        validators=(ValidatorSpec(name="mypkg.is_slug", params={}),),
    )
    with pytest.raises(ContractViolation) as exc:
        render_with(
            field, accepted=frozenset({"enum", "minLength", "maxLength"}), wire="gbnf",
        )
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert "mypkg.is_slug" in exc.value.actual
    assert "gbnf" in exc.value.actual


def test_default_accepted_set_rejects_every_constraint():
    # The empty default set is the honest-failure floor: a caller that certifies no
    # accepted keywords rejects every constraint (the reject-only posture).
    field = FieldDecl(
        name="mood", type=primitive("str"),
        validators=(ValidatorSpec(name="enum", params={"values": ["a"]}),),
    )
    with pytest.raises(ContractViolation) as exc:
        render(field)  # no accepted_keywords → frozenset()
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert "enum" in exc.value.actual


# ---------------------------------------------------------------------------
# Input-payload rendering — the explicit two-case split
# ---------------------------------------------------------------------------


def test_single_str_port_passes_verbatim():
    prompt = "Greet the player as Captain Blackwell.\nStay in character."
    assert render_input_payload({"assembled_prompt": prompt}) == prompt


def test_multi_port_payload_renders_canonical_json():
    rendered = render_input_payload({"turn": 3, "scene": "tavern"})
    assert rendered == '{"scene":"tavern","turn":3}'  # key-sorted, compact
    # Deterministic regardless of dict insertion order:
    assert render_input_payload({"scene": "tavern", "turn": 3}) == rendered


def test_single_non_str_port_renders_canonical_json():
    assert render_input_payload({"turn": 3}) == '{"turn":3}'


def test_non_json_reads_value_raises_the_underlying_type_error():
    # The documented loud path: bytes has no JSON rendering and is not the
    # single-str verbatim case — the underlying TypeError rides raw (binary content
    # rides path/hash references, never inline bytes into a prompt).
    with pytest.raises(TypeError):
        render_input_payload({"blob": b"\x00\x01"})


# ---------------------------------------------------------------------------
# The streaming wire floor — iter_sse_data + urllib_streaming_transport
# ---------------------------------------------------------------------------

from conjured.adapters.wire import (  # noqa: E402
    iter_sse_data,
    urllib_streaming_transport,
)
from tests.lib.loopback import loopback_server  # noqa: E402


def test_iter_sse_data_extracts_payloads_and_stops_at_done():
    lines = [
        b": keep-alive comment\n",
        b"\n",
        b'data: {"a": 1}\n',
        b"\n",
        b'data: {"b": 2}\n',
        b"\n",
        b"data: [DONE]\n",
        b'data: {"never": "reached"}\n',
    ]
    assert list(iter_sse_data(iter(lines))) == ['{"a": 1}', '{"b": 2}']


def test_iter_sse_data_non_utf8_line_raises_raw():
    with __import__("pytest").raises(UnicodeDecodeError):
        list(iter_sse_data(iter([b"data: \xff\xfe\n"])))


def test_urllib_streaming_transport_iterates_real_response_lines():
    """The REAL streaming client over a loopback socket: the SSE body arrives as
    iterable lines the extractor consumes — no fake at the transport seam."""
    sse_body = b'data: {"n": 1}\n\ndata: {"n": 2}\n\ndata: [DONE]\n\n'

    def responder(url, body, headers, timeout_s):
        return 200, sse_body

    with loopback_server(responder) as base:
        status, lines = urllib_streaming_transport(
            f"{base}/chat/completions", b"{}", {"Content-Type": "application/json"}, 5.0
        )
        payloads = list(iter_sse_data(lines))
    assert status == 200
    assert payloads == ['{"n": 1}', '{"n": 2}']


def test_urllib_streaming_transport_error_status_returns_body_as_data():
    """An HTTP error status returns (code, body-iterator) — the adapter raises the
    structured wire error, exactly the buffered client's contract."""
    def responder(url, body, headers, timeout_s):
        return 502, b'{"error": "bad gateway"}'

    with loopback_server(responder) as base:
        status, lines = urllib_streaming_transport(
            f"{base}/chat/completions", b"{}", {"Content-Type": "application/json"}, 5.0
        )
        payload = b"".join(lines)
    assert status == 502
    assert payload == b'{"error": "bad gateway"}'
