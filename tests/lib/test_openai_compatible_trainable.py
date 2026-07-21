"""``conjured.lib.openai_compatible_trainable`` — the OpenAI-compatible
structured-output trainable backend, tested at the Phase-2 seams: the shipped
service-type TOML, adapter resolution (the vector-7 audit + the invoke-signature
check), the compose-time certification gate, the four-property trainable contract
(one test per property — by-construction demonstrated against the recording fake at
the wire seam), and every wire error path asserting the exact structured class.
No network; the fake fails where the runtime would.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import conjured.lib
from conjured.adapters.secret_refs import SecretResolutionError
from conjured.adapters.wire import (
    TrainableWireError,
    render_output_constraint,
    urllib_transport,
)
from conjured.errors import (
    OUTPUT_VALIDATION_AUDIT_CODE,
    Check,
    ContractViolation,
    SchemaValidationError,
)
from conjured.ir.channel_types import (
    FieldDecl,
    OptionalType,
    dict_of,
    list_of,
    literal,
    optional,
    primitive,
    tuple_of,
)
from conjured.ir.graph import GraphNode, Port
from conjured.lib import NATIVE_TRAINABLE_ADAPTERS
from conjured.lib.openai_compatible_trainable import OpenAICompatibleTrainable
from conjured.runner.dispatch import DispatchContext, construct_trainable
from conjured.validator.model_gen import build_model
from conjured.validator.parse import parse_service_type
from conjured.validator.resolve_adapter import (
    check_trainable_backend,
    construct_trainable_adapter,
    resolve_adapter,
)
from tests.lib.fakes import FakeOpenAICompatibleServer
from tests.lib.loopback import loopback_server

QUALIFIED_NAME = "conjured.lib.openai_compatible_trainable"
TOML_PATH = Path(conjured.lib.__file__).parent / "openai_compatible_trainable.toml"
SCHEMA_SOURCE = "compositions/npc_dialogue.toml"
CTX = DispatchContext(pipeline_run_id="run_2026-06-10T00:00:00Z_oait", handler_position=2)

OUT_FIELDS = (
    FieldDecl(name="dialogue", type=primitive("str"), description="The NPC line."),
    FieldDecl(name="mood", type=literal("happy", "sad")),
)
IN_FIELDS = (FieldDecl(name="assembled_prompt", type=primitive("str")),)
EMISSION = {"dialogue": "Arr, welcome aboard.", "mood": "happy"}
TRANSPORT = {"endpoint": "https://llm.consumer.internal/v1", "timeout_ms": 30000}


def shipped_service_type():
    with open(TOML_PATH, "rb") as fh:
        data = tomllib.load(fh)
    return parse_service_type(data, file_path=str(TOML_PATH))


def make_adapter(out_fields=OUT_FIELDS, fake=None):
    adapter = OpenAICompatibleTrainable(
        model="qwen3.5-4b", output_schema=out_fields, schema_source=SCHEMA_SOURCE
    )
    if fake is not None:
        adapter._transport = fake  # the B2 lazy-client seam, pre-memoized
    return adapter


def make_dispatch(adapter, in_fields=IN_FIELDS, out_fields=OUT_FIELDS, *,
                  config=None, transport=None, streamable=False):
    node = GraphNode(
        position=2, node_kind="trainable", qualified_name="npc_dialogue",
        input_ports=tuple(Port(name=f.name, type=f.type) for f in in_fields),
        output_ports=tuple(Port(name=f.name, type=f.type) for f in out_fields),
        read_map={f.name: f.name for f in in_fields},
        write_map={f.name: f.name for f in out_fields},
    )
    effective = config if config is not None else {"temperature": 0.2, "max_tokens": 256}
    # The shipped service-type declares `extras` (a table, default {}), so the effective
    # config the engine partial-applies always carries it — mirror that here.
    effective = {"extras": {}, **effective}
    return construct_trainable(
        node, adapter=adapter, binding_name="llm",
        config=effective,
        transport_extra=transport if transport is not None else TRANSPORT,
        reads_model=build_model("Reads", tuple(in_fields)),
        output_model=build_model("Output", tuple(out_fields)),
        schema_source=SCHEMA_SOURCE,
        streamable=streamable,
    )


# ---------------------------------------------------------------------------
# Pattern B: the shipped TOML + resolution + the pairing registry
# ---------------------------------------------------------------------------


def test_shipped_toml_parses_as_the_service_type():
    st = shipped_service_type()
    assert st.name == QUALIFIED_NAME
    assert [f.name for f in st.identity_schema] == ["model"]
    assert sorted(f.name for f in st.transport_schema) == [
        "api_key_ref", "endpoint", "timeout_ms",
    ]
    # Nullability per the declaration: api_key_ref / timeout_ms nullable (transport is
    # the only nullable home), endpoint required-valued.
    transport = {f.name: f for f in st.transport_schema}
    assert isinstance(transport["api_key_ref"].type, OptionalType)
    assert isinstance(transport["timeout_ms"].type, OptionalType)
    assert not isinstance(transport["endpoint"].type, OptionalType)
    # The dial core + the open `extras` sampling-tail table (D3).
    assert sorted(f.name for f in st.config_schema) == ["extras", "max_tokens", "temperature"]
    from conjured.ir.channel_types import TableType
    extras = {f.name: f for f in st.config_schema}["extras"]
    assert isinstance(extras.type, TableType)
    assert extras.has_default and extras.default == {}  # default {} — coverage never forces it
    assert st.description  # load-bearing for trainable derivables


def test_resolves_through_the_native_adapter_table():
    # The native qualified name resolves through the engine's native adapter table
    # (handler-resolution.md § Native adapters): the consult maps the name to the shipped
    # class path and routes it through the same audited dotted-path leg, so the vector-7
    # source audit + class shape + the R-service-type-002/003 invoke signature check all run
    # on the shipped module (the table supplies discovery, never a shortcut past the checks).
    cls = resolve_adapter(
        QUALIFIED_NAME,
        shipped_service_type(),
        toml_path=str(TOML_PATH),
    )
    assert cls is OpenAICompatibleTrainable
    # The table maps the native name to exactly this class path (the consult's target).
    assert NATIVE_TRAINABLE_ADAPTERS[QUALIFIED_NAME].endswith(".OpenAICompatibleTrainable")


def test_construct_trainable_adapter_passes_identity_shape_and_source():
    adapter = construct_trainable_adapter(
        OpenAICompatibleTrainable, {"model": "qwen3.5-4b"},
        output_schema=OUT_FIELDS, schema_source=SCHEMA_SOURCE,
        qualified_name=QUALIFIED_NAME, toml_path=str(TOML_PATH),
    )
    assert adapter.model == "qwen3.5-4b"


def test_passes_the_trainable_backend_certification_gate():
    check_trainable_backend(
        OpenAICompatibleTrainable, qualified_name=QUALIFIED_NAME,
        toml_path=str(TOML_PATH),
    )


# ---------------------------------------------------------------------------
# Property 1 — server-side decode-time seal
# ---------------------------------------------------------------------------


def test_property_1_submits_the_declared_shape_as_the_strict_decode_constraint():
    fake = FakeOpenAICompatibleServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake))
    result = dispatch(reads={"assembled_prompt": "Greet the player."}, ctx=CTX)
    assert result == EMISSION
    [request] = fake.requests
    response_format = request["body"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    # The submitted constraint IS the declared shape's canonical rendering — the
    # literal-equal artifact, descriptions included (R-handler-005).
    assert response_format["json_schema"]["schema"] == render_output_constraint(
        OUT_FIELDS, schema_source=SCHEMA_SOURCE
    )
    assert (
        response_format["json_schema"]["schema"]["properties"]["dialogue"]["description"]
        == "The NPC line."
    )


def test_property_1_nonconforming_emission_halts_in_engine_validation_no_retry():
    # A backend that ignores the constraint: the adapter returns the emission
    # verbatim, the engine raises SchemaValidationError (the literal-equal seal's
    # validation half), and the wire saw EXACTLY ONE request — no parse-and-retry.
    fake = FakeOpenAICompatibleServer({"dialogue": 42, "mood": "happy"})
    dispatch = make_dispatch(make_adapter(fake=fake))
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert exc.value.audit_code == OUTPUT_VALIDATION_AUDIT_CODE
    assert exc.value.schema_source == SCHEMA_SOURCE
    assert len(fake.requests) == 1


def test_property_1_smuggled_key_is_contract_violation_no_retry():
    fake = FakeOpenAICompatibleServer({**EMISSION, "smuggled": 1})
    dispatch = make_dispatch(make_adapter(fake=fake))
    with pytest.raises(ContractViolation) as exc:
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert exc.value.check is Check.UNDECLARED_OUTPUT_KEY
    assert len(fake.requests) == 1


# ---------------------------------------------------------------------------
# Property 2 — consumer-owned serving (no hosted default)
# ---------------------------------------------------------------------------


def test_property_2_no_endpoint_default_and_exact_consumer_url():
    fake = FakeOpenAICompatibleServer(EMISSION)
    adapter = make_adapter(fake=fake)
    dispatch = make_dispatch(adapter, transport={})
    with pytest.raises(TrainableWireError, match="no default serving runtime"):
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert fake.requests == []  # nothing was sent anywhere
    dispatch = make_dispatch(adapter, transport=TRANSPORT)
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["url"] == "https://llm.consumer.internal/v1/chat/completions"
    assert request["body"]["model"] == "qwen3.5-4b"  # the composed identity, exactly


# ---------------------------------------------------------------------------
# Property 3 — the standardized training-artifact contract
# ---------------------------------------------------------------------------


def test_property_3_artifact_contract_is_safetensors_peft():
    from conjured.validator.resolve_adapter import TRAINING_ARTIFACT_CONTRACTS

    assert OpenAICompatibleTrainable.training_artifact_contract == "safetensors+peft"
    assert (
        OpenAICompatibleTrainable.training_artifact_contract
        in TRAINING_ARTIFACT_CONTRACTS
    )


# ---------------------------------------------------------------------------
# Property 4 — the clean read/write seal
# ---------------------------------------------------------------------------


def test_property_4_single_str_read_passes_verbatim_no_shaping():
    fake = FakeOpenAICompatibleServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake))
    reads = {"assembled_prompt": "Greet the player as Captain Blackwell."}
    result = dispatch(reads=dict(reads), ctx=CTX)
    [request] = fake.requests
    messages = request["body"]["messages"]
    # Exactly one user message; content is the assembled prompt VERBATIM — no system
    # message, no template, no wrapper (prompt shaping is the preprocessor's job,
    # R-handler-011).
    assert messages == [{"role": "user", "content": reads["assembled_prompt"]}]
    # The return is exactly the parsed emission — no enrichment, no metadata.
    assert result == EMISSION
    # Dispatch metadata never reaches the wire (engine provenance, not model input).
    body_text = json.dumps(request["body"])
    assert "npc_dialogue" not in body_text and "llm" not in body_text


def test_property_4_multi_port_reads_serialize_to_canonical_json():
    in_fields = (
        FieldDecl(name="scene", type=primitive("str")),
        FieldDecl(name="turn", type=primitive("int")),
    )
    fake = FakeOpenAICompatibleServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake), in_fields=in_fields)
    dispatch(reads={"scene": "tavern", "turn": 3}, ctx=CTX)
    [request] = fake.requests
    assert request["body"]["messages"][0]["content"] == '{"scene":"tavern","turn":3}'


# ---------------------------------------------------------------------------
# The compose-time caveat — strict-wire inexpressible schemas reject at construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        FieldDecl(name="meta", type=dict_of(primitive("str"))),
        FieldDecl(name="pair", type=tuple_of(primitive("str"), primitive("int"))),
    ],
    ids=["open-keyed-dict", "fixed-arity-tuple"],
)
def test_compose_caveat_strict_inexpressible_shapes_reject_at_construction(field):
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert exc.value.rule_id == "R-handler-005"
    assert exc.value.file_path == SCHEMA_SOURCE
    assert exc.value.section_path == "trainable.output_schema"


def test_compose_caveat_field_validators_reject_at_construction():
    from conjured.ir.channel_types import ValidatorSpec

    field = FieldDecl(
        name="count", type=primitive("int"),
        validators=(ValidatorSpec(name="minimum", params={"limit": 1}),),
    )
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert exc.value.rule_id == "R-handler-005"


def test_accepted_matrix_enum_renders_into_the_strict_constraint():
    # The OpenAI wire's accepted set is {enum} (D2): an `enum` constraint RENDERS into the
    # submitted strict json_schema as the JSON-Schema `enum` keyword — the seal stays
    # literal-equal (the engine output model enforces the same membership).
    from conjured.ir.channel_types import ValidatorSpec

    field = FieldDecl(
        name="mood", type=primitive("str"),
        validators=(ValidatorSpec(name="enum", params={"values": ["happy", "sad"]}),),
    )
    adapter = make_adapter(out_fields=(field,))
    assert adapter._constraint["properties"]["mood"] == {
        "type": "string", "enum": ["happy", "sad"],
    }


def test_accepted_matrix_length_keyword_rejects_on_the_openai_wire():
    # minLength RENDERS on the GBNF wire but is OUT of the OpenAI wire's accepted set
    # ({enum}) — the matrix is per wire family, not global. Rejected at construction
    # naming the keyword + the wire.
    from conjured.ir.channel_types import ValidatorSpec

    field = FieldDecl(
        name="code", type=primitive("str"),
        validators=(ValidatorSpec(name="minLength", params={"limit": 4}),),
    )
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert "minLength" in exc.value.actual


@pytest.mark.parametrize(
    ("field", "path_fragment"),
    [
        (FieldDecl(name="rows", type=list_of(dict_of(primitive("str")))), "rows[]"),
        (FieldDecl(name="maybe", type=optional(dict_of(primitive("str")))), "maybe"),
    ],
    ids=["open-dict-inside-list", "open-dict-inside-optional"],
)
def test_compose_caveat_nested_open_dicts_reject_through_the_recursion(
    field, path_fragment
):
    # The strict gate recurses through array items and anyOf members — an open-keyed
    # dict is inexpressible on this wire at ANY nesting depth, rejected at
    # construction with the member path named.
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert path_fragment in exc.value.actual


# ---------------------------------------------------------------------------
# Wire error paths — every failure the exact structured class, never a retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "match"),
    [
        ("http_500", "HTTP 500"),
        # Other 2xx included is a wire failure (the exact-status clause canon bolds —
        # native-library § HTTP-speaking member conventions): a 201 with a perfect body
        # still raises; RED if expect_success weakens to >= 400 or a 2xx-range check.
        ("http_201", "HTTP 201"),
        ("non_json_body", "not JSON"),
        ("non_object_body", "not a JSON object"),
        ("no_choices", "no choices"),
        ("choices_not_array", "'choices' is not an array"),
        ("choice_not_object", "choice is not an object"),
        ("message_not_object", "message is not an object"),
        ("no_content", "no message content"),
        ("content_not_text", "content is not text"),
        ("refusal", "refused"),
        ("length", "finish_reason='length'"),
        # An ABSENT finish_reason on the buffered surface rejects exactly as the
        # streaming surface's no-finish stream does: a wire that never said the
        # emission completed cannot seal a training record (the docstring's truncation
        # guarantee admits no None exception).
        ("no_finish_buffered", "finish_reason=None"),
        ("non_json_content", "not the constrained JSON"),
    ],
)
def test_wire_failures_raise_trainable_wire_error_exactly_once(mode, match):
    fake = FakeOpenAICompatibleServer(EMISSION, mode=mode)
    dispatch = make_dispatch(make_adapter(fake=fake))
    with pytest.raises(TrainableWireError, match=match):
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert len(fake.requests) == 1  # fail loud, no retry, no fallback


# ---------------------------------------------------------------------------
# Transport handling — config dials, timeout, auth, memoization
# ---------------------------------------------------------------------------


def test_every_dial_reaches_the_wire_with_a_concrete_value():
    """The unpinned-omit wire path is REMOVED (the N2 ruling-1 dial defaults): an
    unstated dial resolves to the DECLARED ship-time default in the shipped TOML (the
    values' only home) through the shared effective-config derivation, and every dial
    always reaches the wire — the serving runtime's own defaults never apply."""
    from conjured.validator.compile import effective_config

    st = shipped_service_type()
    declared_defaults = {f.name: f.default for f in st.config_schema}
    # The TOML is the value home (extras defaults to the empty table — D3).
    assert declared_defaults == {"temperature": 1.0, "max_tokens": 4096, "extras": {}}
    effective = effective_config(
        {"max_tokens": 128}, st, composition_ref="c", section_path="trainable.config"
    )
    fake = FakeOpenAICompatibleServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake), config=effective)
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["body"]["temperature"] == 1.0  # the declared default, on the wire
    assert request["body"]["max_tokens"] == 128   # the supplied override wins


def test_extras_sampling_tail_delivers_verbatim_to_the_wire_body():
    # D3: the open `extras` table merges verbatim into the wire body's generation-parameter
    # surface (the cross-server sampling tail the enumerated core does not name).
    fake = FakeOpenAICompatibleServer(EMISSION)
    dispatch = make_dispatch(
        make_adapter(fake=fake),
        config={"temperature": 0.2, "max_tokens": 64, "extras": {"top_p": 0.9, "top_k": 40}},
    )
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["body"]["top_p"] == 0.9
    assert request["body"]["top_k"] == 40
    # The engine's owned wire keys remain present and authoritative.
    assert request["body"]["temperature"] == 0.2
    assert request["body"]["model"] == "qwen3.5-4b"


def test_owned_wire_keys_win_over_extras_defense_in_depth():
    # Compose rejects an extras key naming a reserved wire key; defense in depth, invoke()
    # writes its owned keys AFTER the **extras merge, so an engine wire key can never be
    # overridden by extras even if the compose check had a gap.
    fake = FakeOpenAICompatibleServer(EMISSION)
    dispatch = make_dispatch(
        make_adapter(fake=fake),
        config={"temperature": 0.2, "max_tokens": 64, "extras": {"temperature": 99.0}},
    )
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["body"]["temperature"] == 0.2  # the dial wins, not the extras 99.0


def test_transport_timeout_and_bearer_auth_apply_per_call(monkeypatch):
    # api_key_ref is a "[scheme]payload" secret reference the adapter resolves at dispatch via
    # the blessed resolver (never a raw bearer in the deployment TOML — deployment reference
    # § Secret references).
    monkeypatch.setenv("LLM_PROD", "sk-local-test")
    fake = FakeOpenAICompatibleServer(EMISSION)
    transport = {**TRANSPORT, "api_key_ref": "[env]LLM_PROD"}
    dispatch = make_dispatch(make_adapter(fake=fake), transport=transport)
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["timeout_s"] == 30.0  # timeout_ms applied by the adapter
    assert request["headers"]["Authorization"] == "Bearer sk-local-test"  # resolved from $LLM_PROD


def test_api_key_ref_with_unset_env_var_fails_loud(monkeypatch):
    # A present api_key_ref that resolves to an unset env var is a deployment misconfiguration —
    # fail loud (a missing secret must never degrade to a silent unauthenticated request). The
    # store-side failure is the resolver's SecretResolutionError, riding raw through dispatch.
    monkeypatch.delenv("LLM_PROD", raising=False)
    fake = FakeOpenAICompatibleServer(EMISSION)
    transport = {**TRANSPORT, "api_key_ref": "[env]LLM_PROD"}
    dispatch = make_dispatch(make_adapter(fake=fake), transport=transport)
    with pytest.raises(SecretResolutionError, match="LLM_PROD"):
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)


def test_no_api_key_means_no_auth_header():
    fake = FakeOpenAICompatibleServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake))
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert "Authorization" not in request["headers"]


def test_wire_client_is_memoized_instance_state():
    fake = FakeOpenAICompatibleServer(EMISSION)
    adapter = make_adapter(fake=fake)
    dispatch = make_dispatch(adapter)
    dispatch(reads={"assembled_prompt": "a"}, ctx=CTX)
    dispatch(reads={"assembled_prompt": "b"}, ctx=CTX)
    assert adapter._transport is fake  # an injected client is never overwritten
    assert len(fake.requests) == 2


def test_invoke_does_not_mutate_the_input_payload():
    fake = FakeOpenAICompatibleServer(EMISSION)
    adapter = make_adapter(fake=fake)
    payload = {"assembled_prompt": "Greet the player."}
    snapshot = dict(payload)
    adapter.invoke(
        input_payload=payload, service_name="llm",
        caller_qualified_name="npc_dialogue", caller_position=2,
        temperature=0.2, max_tokens=64, extras={}, **TRANSPORT,
    )
    assert payload == snapshot  # the clean seal reads; it never writes


# ---------------------------------------------------------------------------
# The REAL wire client — a loopback http.server, no fake at the transport seam
# ---------------------------------------------------------------------------


def test_real_wire_client_builds_lazily_and_round_trips_a_loopback_server():
    fake = FakeOpenAICompatibleServer(EMISSION)  # serves as the loopback responder
    adapter = make_adapter()  # NO pre-set transport — the real lazy build
    assert adapter._transport is None
    with loopback_server(fake) as base:
        dispatch = make_dispatch(
            adapter, transport={"endpoint": base, "timeout_ms": 10000}
        )
        result = dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
        assert result == EMISSION
        assert adapter._transport is urllib_transport  # memoized real client
        dispatch(reads={"assembled_prompt": "y"}, ctx=CTX)
        assert adapter._transport is urllib_transport  # never rebuilt
    assert len(fake.requests) == 2


def test_real_wire_client_converts_an_http_error_status_to_the_structured_error():
    # urllib raises HTTPError on a 4xx; the transport converts it to (status, body)
    # and the adapter raises the structured wire error — never a raw HTTPError.
    def responder(url, body, headers, timeout_s):
        return 404, b'{"error": "no such route"}'

    adapter = make_adapter()
    with loopback_server(responder) as base:
        dispatch = make_dispatch(
            adapter, transport={"endpoint": base, "timeout_ms": 10000}
        )
        with pytest.raises(TrainableWireError, match="HTTP 404"):
            dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)


# ---------------------------------------------------------------------------
# The standing double rule — the fake itself fails where the runtime would
# ---------------------------------------------------------------------------


def test_fake_404s_on_a_wrong_path_like_a_real_server():
    fake = FakeOpenAICompatibleServer(EMISSION)
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    status, payload = fake(
        "https://x/v1/completions", json.dumps(body).encode(), {}, None
    )
    assert status == 404
    assert "unknown path" in json.loads(payload)["error"]


def test_fake_rejects_a_malformed_strict_constraint_like_a_real_server():
    fake = FakeOpenAICompatibleServer(EMISSION)
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "x"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {  # missing strict; open object schema
                "name": "output_schema",
                "schema": {"type": "object"},
            },
        },
    }
    status, payload = fake(
        "https://x/v1/chat/completions", json.dumps(body).encode(), {}, None
    )
    assert status == 400
    problems = json.loads(payload)["error"]
    assert any("strict" in p for p in problems)
    assert any("open-keyed object" in p for p in problems)


# ---------------------------------------------------------------------------
# The streaming dispatch surface — invoke_streaming (the streaming-transport arc)
# ---------------------------------------------------------------------------

from conjured.validator.resolve_adapter import (  # noqa: E402
    check_extras_disjoint,
    check_streamable_backend,
)
from tests.lib.fakes import FakeOpenAICompatibleStreamingServer  # noqa: E402


def make_streaming_adapter(out_fields=OUT_FIELDS, fake=None):
    adapter = make_adapter(out_fields)
    if fake is not None:
        adapter._streaming_transport = fake  # the streaming lazy-client seam
    return adapter


def drive(generator):
    """Drive an invoke_streaming generator to completion — returns
    ``(fragments, returned_emission)`` (the engine's dispatch layer does exactly
    this loop; StopIteration.value is the assembled emission)."""
    fragments = []
    while True:
        try:
            fragments.append(next(generator))
        except StopIteration as stop:
            return fragments, stop.value


def invoke_streaming_kwargs(**overrides):
    kwargs = dict(
        input_payload={"assembled_prompt": "Say hi."},
        service_name="llm",
        caller_qualified_name="npc_dialogue",
        caller_position=2,
        temperature=0.2,
        max_tokens=256,
        extras={},
        **TRANSPORT,
    )
    kwargs.update(overrides)
    return kwargs


def test_streaming_yields_fragments_and_returns_the_assembled_emission():
    fake = FakeOpenAICompatibleStreamingServer(EMISSION)
    adapter = make_streaming_adapter(fake=fake)
    fragments, returned = drive(
        adapter.invoke_streaming(**invoke_streaming_kwargs())
    )
    assert len(fragments) >= 3
    assert json.loads("".join(fragments)) == EMISSION
    assert returned == EMISSION  # returned verbatim; validation is downstream


def test_streaming_submits_stream_true_and_the_identical_constraint():
    """The streaming request is the buffered request plus exactly the owned
    `stream: true` key — same rendered reads, same strict json_schema constraint,
    same dials (the clean seal holds on both surfaces)."""
    buffered = FakeOpenAICompatibleServer(EMISSION)
    streaming = FakeOpenAICompatibleStreamingServer(EMISSION)
    adapter = make_adapter(fake=buffered)
    adapter._streaming_transport = streaming
    adapter.invoke(**invoke_streaming_kwargs())
    drive(adapter.invoke_streaming(**invoke_streaming_kwargs()))
    buffered_body = buffered.requests[0]["body"]
    streaming_body = streaming.requests[0]["body"]
    assert streaming_body.pop("stream") is True
    assert "stream" not in buffered_body  # absence IS the wire's non-streaming default
    assert streaming_body == buffered_body


@pytest.mark.parametrize(
    "mode, match",
    [
        ("http_500", "HTTP 500"),
        ("refusal", "refused"),
        ("length", "finish_reason='length'"),
        ("no_finish", "without a finish_reason"),
        ("non_json_chunk", "not JSON"),
        ("empty", "no content deltas"),
        ("content_not_text", "not text"),
        ("non_json_assembled", "not the constrained JSON"),
    ],
)
def test_streaming_wire_failures_raise_trainable_wire_error(mode, match):
    """Every streaming protocol failure raises the structured wire error raw —
    no retry, no substitute value, mirroring the buffered surface's honesty."""
    fake = FakeOpenAICompatibleStreamingServer(EMISSION, mode=mode)
    adapter = make_streaming_adapter(fake=fake)
    with pytest.raises(TrainableWireError, match=match):
        drive(adapter.invoke_streaming(**invoke_streaming_kwargs()))


def test_streaming_client_is_memoized_instance_state():
    fake = FakeOpenAICompatibleStreamingServer(EMISSION)
    adapter = make_streaming_adapter(fake=fake)
    drive(adapter.invoke_streaming(**invoke_streaming_kwargs()))
    assert adapter._streaming_transport is fake  # an injected client is never overwritten


def test_stream_is_a_reserved_wire_key():
    """`stream` is engine-owned on this wire: declared in reserved_wire_keys (so an
    `extras` table naming it is rejected at compose — the disjointness check) — the
    structural guard against an extras key flipping the delivery mode."""
    assert "stream" in OpenAICompatibleTrainable.reserved_wire_keys
    with pytest.raises(ContractViolation) as exc:
        check_extras_disjoint(
            OpenAICompatibleTrainable,
            {"temperature": 0.2, "max_tokens": 256, "extras": {"stream": True}},
            qualified_name=QUALIFIED_NAME, toml_path=str(TOML_PATH),
        )
    assert "stream" in str(exc.value)


def test_shipped_adapter_passes_the_streamable_capability_gate():
    """The native adapter IS streaming-capable — the compose-time capability check
    admits it (the gate a streamable=true composition fires)."""
    check_streamable_backend(
        OpenAICompatibleTrainable,
        qualified_name=QUALIFIED_NAME, toml_path=str(TOML_PATH),
    )


def test_dispatch_streaming_delivers_fragments_then_validates():
    """The trainable dispatch layer drives the generator, delivers each fragment to
    the ctx sink, and validates the RETURNED emission through the same output
    boundary as the buffered path."""
    fake = FakeOpenAICompatibleStreamingServer(EMISSION)
    adapter = make_streaming_adapter(fake=fake)
    dispatch = make_dispatch(adapter, streamable=True)
    fragments: list[str] = []
    ctx = DispatchContext(
        pipeline_run_id="run_2026-07-09T00:00:00Z_strm", handler_position=2,
        stream_sink=fragments.append,
    )
    result = dispatch(reads={"assembled_prompt": "Say hi."}, ctx=ctx)
    assert result == EMISSION
    assert json.loads("".join(fragments)) == EMISSION


def test_dispatch_sink_error_is_walled_and_the_dispatch_completes(caplog):
    # verifies: stream-sink-consumer-isolated
    """The dispatch-seam half of the observation-plane wall (pipeline/reference.md
    § Pipeline invocation), against the REAL native adapter's generator: a raising
    consumer sink is absorbed, surfaced on the `conjured.runner` operational logger,
    and detached — the dispatch still drives the backend to completion and returns
    the validated emission; the backend is never blamed and nothing escapes."""
    import logging

    fake = FakeOpenAICompatibleStreamingServer(EMISSION)
    adapter = make_streaming_adapter(fake=fake)
    dispatch = make_dispatch(adapter, streamable=True)
    calls: list[str] = []

    def exploding_sink(fragment: str) -> None:
        calls.append(fragment)
        raise RuntimeError("sink burst")

    ctx = DispatchContext(
        pipeline_run_id="run_2026-07-09T00:00:00Z_strm", handler_position=2,
        stream_sink=exploding_sink,
    )
    with caplog.at_level(logging.WARNING, logger="conjured.runner"):
        result = dispatch(reads={"assembled_prompt": "Say hi."}, ctx=ctx)
    assert result == EMISSION
    assert len(calls) == 1  # detached after the raising delivery
    [record] = [r for r in caplog.records if "stream_sink raised" in r.getMessage()]
    assert record.name == "conjured.runner"


def test_dispatch_streamable_without_sink_uses_the_buffered_surface():
    """streamable=true with no ctx sink runs the buffered invoke — streaming is
    per-invocation opt-in; the streaming fake would record a request if touched."""
    buffered = FakeOpenAICompatibleServer(EMISSION)
    streaming = FakeOpenAICompatibleStreamingServer(EMISSION)
    adapter = make_adapter(fake=buffered)
    adapter._streaming_transport = streaming
    dispatch = make_dispatch(adapter, streamable=True)
    result = dispatch(reads={"assembled_prompt": "Say hi."}, ctx=CTX)
    assert result == EMISSION
    assert len(buffered.requests) == 1
    assert streaming.requests == []
