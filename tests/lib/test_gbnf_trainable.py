"""``conjured.lib.gbnf_trainable`` — the llama.cpp / GBNF grammar trainable backend,
tested at the Phase-2 seams: the shipped service-type TOML, adapter resolution, the
certification gate, the four-property contract (one test per property against the
recording llama-server fake — which validates every submitted grammar structurally,
the way the runtime rejects a bad grammar), the wire-form coverage difference from
the strict OpenAI form (the open-keyed ``dict`` IS expressible here; ``tuple`` and
``bytes`` reject on every JSON wire at the shared renderer, and non-ASCII field
names reject at this wire's rule-name boundary), and every wire error path. No
external network (the real-client tests run against a loopback server).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import conjured.lib
from conjured.adapters.gbnf import grammar_from_constraint
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
    TableType,
    ValidatorSpec,
    dict_of,
    list_of,
    literal,
    nested,
    optional,
    primitive,
    tuple_of,
)
from conjured.ir.graph import GraphNode, Port
from conjured.lib import NATIVE_TRAINABLE_ADAPTERS
from conjured.lib.gbnf_trainable import GBNFTrainable
from conjured.runner.dispatch import DispatchContext, construct_trainable
from conjured.validator.model_gen import build_model
from conjured.validator.parse import parse_service_type
from conjured.validator.resolve_adapter import (
    check_trainable_backend,
    construct_trainable_adapter,
    resolve_adapter,
)
from tests.lib.fakes import FakeLlamaServer, check_gbnf
from tests.lib.loopback import loopback_server

QUALIFIED_NAME = "conjured.lib.gbnf_trainable"
TOML_PATH = Path(conjured.lib.__file__).parent / "gbnf_trainable.toml"
SCHEMA_SOURCE = "compositions/npc_dialogue.toml"
CTX = DispatchContext(pipeline_run_id="run_2026-06-10T00:00:00Z_gbnf", handler_position=2)

# A GBNF trainable's output_schema carries NO field `description` — the grammar has no
# description channel and the adapter never prompt-injects (property 4), so a described field
# is a compose-time ContractViolation (see
# test_a_described_output_field_is_rejected_at_construction). The fixture is therefore
# description-free, as a real GBNF-bound trainable must be.
OUT_FIELDS = (
    FieldDecl(name="dialogue", type=primitive("str")),
    FieldDecl(name="mood", type=literal("happy", "sad")),
)
IN_FIELDS = (FieldDecl(name="assembled_prompt", type=primitive("str")),)
EMISSION = {"dialogue": "Arr, welcome aboard.", "mood": "happy"}
TRANSPORT = {"endpoint": "http://localhost:8080", "timeout_ms": 30000}


def shipped_service_type():
    with open(TOML_PATH, "rb") as fh:
        data = tomllib.load(fh)
    return parse_service_type(data, file_path=str(TOML_PATH))


def make_adapter(out_fields=OUT_FIELDS, fake=None):
    adapter = GBNFTrainable(
        model="qwen3.5-4b-gguf", output_schema=out_fields, schema_source=SCHEMA_SOURCE
    )
    if fake is not None:
        adapter._transport = fake  # the B2 lazy-client seam, pre-memoized
    return adapter


def make_dispatch(adapter, in_fields=IN_FIELDS, out_fields=OUT_FIELDS, *,
                  config=None, transport=None):
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
    extras = {f.name: f for f in st.config_schema}["extras"]
    assert isinstance(extras.type, TableType)
    assert extras.has_default and extras.default == {}
    assert st.description


def test_resolves_through_the_native_adapter_table():
    # The native qualified name resolves through the engine's native adapter table
    # (handler-resolution.md § Native adapters): the consult maps it to the shipped class
    # path and routes it through the same audited dotted-path leg, so the full verification
    # surface runs unchanged (discovery, never a shortcut past the checks).
    cls = resolve_adapter(
        QUALIFIED_NAME,
        shipped_service_type(),
        toml_path=str(TOML_PATH),
    )
    assert cls is GBNFTrainable
    assert NATIVE_TRAINABLE_ADAPTERS[QUALIFIED_NAME].endswith(".GBNFTrainable")


def test_construct_trainable_adapter_passes_identity_shape_and_source():
    adapter = construct_trainable_adapter(
        GBNFTrainable, {"model": "qwen3.5-4b-gguf"},
        output_schema=OUT_FIELDS, schema_source=SCHEMA_SOURCE,
        qualified_name=QUALIFIED_NAME, toml_path=str(TOML_PATH),
    )
    assert adapter.model == "qwen3.5-4b-gguf"


def test_passes_the_trainable_backend_certification_gate():
    check_trainable_backend(
        GBNFTrainable, qualified_name=QUALIFIED_NAME, toml_path=str(TOML_PATH)
    )


# ---------------------------------------------------------------------------
# Property 1 — server-side decode-time seal (the grammar IS the declared shape)
# ---------------------------------------------------------------------------


def test_property_1_submits_the_declared_shape_as_the_gbnf_grammar():
    fake = FakeLlamaServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake))
    result = dispatch(reads={"assembled_prompt": "Greet the player."}, ctx=CTX)
    assert result == EMISSION
    [request] = fake.requests
    # The submitted grammar is the deterministic projection of the declared shape's
    # canonical rendering (R-handler-005) — and the strict fake already validated its
    # structural well-formedness the way llama-server does.
    expected = grammar_from_constraint(
        render_output_constraint(OUT_FIELDS, schema_source=SCHEMA_SOURCE)
    )
    assert request["body"]["grammar"] == expected
    assert check_gbnf(request["body"]["grammar"]) == []


def test_property_1_nonconforming_emission_halts_in_engine_validation_no_retry():
    fake = FakeLlamaServer({"dialogue": 42, "mood": "happy"})
    dispatch = make_dispatch(make_adapter(fake=fake))
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert exc.value.audit_code == OUTPUT_VALIDATION_AUDIT_CODE
    assert exc.value.schema_source == SCHEMA_SOURCE
    assert len(fake.requests) == 1  # no parse-and-retry path exists


def test_property_1_smuggled_key_is_contract_violation_no_retry():
    fake = FakeLlamaServer({**EMISSION, "smuggled": 1})
    dispatch = make_dispatch(make_adapter(fake=fake))
    with pytest.raises(ContractViolation) as exc:
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert exc.value.check is Check.UNDECLARED_OUTPUT_KEY
    assert len(fake.requests) == 1


# ---------------------------------------------------------------------------
# Property 2 — consumer-owned serving (no hosted default)
# ---------------------------------------------------------------------------


def test_property_2_no_endpoint_default_and_exact_consumer_url():
    fake = FakeLlamaServer(EMISSION)
    adapter = make_adapter(fake=fake)
    dispatch = make_dispatch(adapter, transport={})
    with pytest.raises(TrainableWireError, match="no default serving runtime"):
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert fake.requests == []
    dispatch = make_dispatch(adapter, transport=TRANSPORT)
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["url"] == "http://localhost:8080/completion"
    assert request["body"]["model"] == "qwen3.5-4b-gguf"


# ---------------------------------------------------------------------------
# Property 3 — the standardized training-artifact contract
# ---------------------------------------------------------------------------


def test_property_3_artifact_contract_is_gguf():
    from conjured.validator.resolve_adapter import TRAINING_ARTIFACT_CONTRACTS

    assert GBNFTrainable.training_artifact_contract == "gguf"
    assert GBNFTrainable.training_artifact_contract in TRAINING_ARTIFACT_CONTRACTS


# ---------------------------------------------------------------------------
# Property 4 — the clean read/write seal
# ---------------------------------------------------------------------------


def test_property_4_single_str_read_is_the_prompt_verbatim():
    fake = FakeLlamaServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake))
    reads = {"assembled_prompt": "Greet the player as Captain Blackwell."}
    result = dispatch(reads=dict(reads), ctx=CTX)
    [request] = fake.requests
    # The prompt IS the assembled read, verbatim — no template, no scaffold (descriptions
    # can't reach this wire at all: a described output field rejects at construction, see
    # test_a_described_output_field_is_rejected_at_construction).
    assert request["body"]["prompt"] == reads["assembled_prompt"]
    assert result == EMISSION
    body_text = json.dumps(request["body"])
    assert "npc_dialogue" not in body_text and "llm" not in body_text


def test_property_4_multi_port_reads_serialize_to_canonical_json():
    in_fields = (
        FieldDecl(name="scene", type=primitive("str")),
        FieldDecl(name="turn", type=primitive("int")),
    )
    fake = FakeLlamaServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake), in_fields=in_fields)
    dispatch(reads={"scene": "tavern", "turn": 3}, ctx=CTX)
    [request] = fake.requests
    assert request["body"]["prompt"] == '{"scene":"tavern","turn":3}'


# ---------------------------------------------------------------------------
# Wire-form coverage — the GBNF grammar expresses what the strict form cannot
# ---------------------------------------------------------------------------


def test_dict_optional_nested_ports_are_expressible_on_this_wire():
    out_fields = (
        FieldDecl(name="aliases", type=dict_of(primitive("str"))),
        FieldDecl(name="note", type=optional(primitive("str"))),
        FieldDecl(name="tags", type=list_of(literal("a", "b"))),
        FieldDecl(
            name="mood",
            type=nested(
                FieldDecl(name="intensity", type=primitive("int")),
                FieldDecl(name="label", type=literal("happy", "sad")),
            ),
        ),
    )
    emission = {
        "aliases": {"cap": "Captain Blackwell"},
        "note": None,
        "tags": ["a"],
        "mood": {"intensity": 3, "label": "happy"},
    }
    fake = FakeLlamaServer(emission)
    dispatch = make_dispatch(make_adapter(out_fields=out_fields, fake=fake),
                             out_fields=out_fields)
    result = dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert result == emission
    [request] = fake.requests
    assert check_gbnf(request["body"]["grammar"]) == []  # structurally sound grammar


def test_compose_caveat_tuple_channel_rejects_json_wire_wide():
    # A JSON wire delivers arrays; strict validation rejects a list against a
    # declared tuple — the seal cannot close, so BOTH native wire forms reject the
    # declaration at construction (the shared renderer).
    field = FieldDecl(name="pair", type=tuple_of(primitive("str"), primitive("int")))
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert exc.value.rule_id == "R-handler-005"


def test_compose_caveat_field_validators_reject_at_construction():
    field = FieldDecl(
        name="count", type=primitive("int"),
        validators=(ValidatorSpec(name="minimum", params={"limit": 1}),),
    )
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert exc.value.rule_id == "R-handler-005"
    assert exc.value.file_path == SCHEMA_SOURCE
    assert exc.value.section_path == "trainable.output_schema"


def test_accepted_matrix_length_keywords_render_into_the_grammar():
    # The GBNF wire's accepted set includes {minLength, maxLength} (D2): a length-bounded
    # string RENDERS into the submitted grammar as a counted string-char repetition — the
    # seal stays literal-equal (the engine output model enforces the same bound). This is
    # the per-family difference from the OpenAI wire, where minLength rejects.
    field = FieldDecl(
        name="code", type=primitive("str"),
        validators=(
            ValidatorSpec(name="minLength", params={"limit": 2}),
            ValidatorSpec(name="maxLength", params={"limit": 4}),
        ),
    )
    adapter = make_adapter(out_fields=(field,))
    assert "string-char{2,4}" in adapter._grammar
    assert check_gbnf(adapter._grammar) == []


def test_accepted_matrix_pattern_rejects_at_construction():
    # The ruled scope (2026-06-13): a `pattern` constraint stays a loud compose-time
    # rejection on the GBNF wire (reject-only — a subtly-wrong regex→GBNF translation
    # corrupts the literal-equal seal worse than rejecting). `pattern` is not in the
    # GBNF wire's accepted set.
    field = FieldDecl(
        name="slug", type=primitive("str"),
        validators=(ValidatorSpec(name="pattern", params={"pattern": "^[a-z]+$"}),),
    )
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert "pattern" in exc.value.actual


def test_compose_caveat_non_ascii_field_name_rejects_at_construction():
    # The GBNF rule-name boundary: grammar rule names are ASCII-only, so a legal
    # non-ASCII field name cannot name its grammar rules — rejected at construction
    # (= compose), never a per-dispatch grammar rejection from the serving runtime.
    field = FieldDecl(name="émotion", type=literal("happy", "sad"))
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert exc.value.rule_id == "R-handler-005"
    assert "émotion" in exc.value.actual
    assert exc.value.file_path == SCHEMA_SOURCE
    assert exc.value.section_path == "trainable.output_schema"


def test_compose_caveat_non_ascii_NON_alphanumeric_field_name_rejects_at_construction():
    """Fix 6 (`gbnf-nonascii-fieldname`): the reject is **unconditional on non-ASCII**,
    not just non-ASCII *alphanumerics*. A non-ASCII NON-alphanumeric field name (`prix€`)
    must reject at compose too (native-library/reference.md § conjured.lib.gbnf_trainable:
    "a declared output-field name carrying a non-ASCII character is … rejected at compose —
    GBNF rule names are [a-zA-Z0-9-]"; R-handler-005). Before Fix 6 the guard tested only
    `c.isalnum() and not c.isascii()`, so `€` (a non-alphanumeric symbol) slipped through and
    `adapters/gbnf.py` `_claim` sanitized it to `-` (all-ASCII) — the field composed and the
    grammar carried a `\\uXXXX`-escaped key the output model would not validate raw, weakening
    the literal-equal seal. RED before the predicate widened to `not key.isascii()` (the `é`
    test above stays green either way, so this non-alphanumeric case is the one that bites)."""
    field = FieldDecl(name="prix€", type=literal("low", "high"))
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert exc.value.rule_id == "R-handler-005"
    assert "prix€" in exc.value.actual
    assert exc.value.file_path == SCHEMA_SOURCE
    assert exc.value.section_path == "trainable.output_schema"


# verifies: gbnf-rejects-described-field
def test_a_described_output_field_is_rejected_at_construction():
    """AC3 — a described `output_schema` field on a GBNF-wire trainable fails LOUD at compose,
    defending against a hashed model-conditioning input the wire would silently drop. The GBNF
    grammar has no field-description channel and the adapter never prompt-injects (property 4),
    so `description` cannot ride this wire: the reject fires at construction (= compose), the
    same honest-failure class as bytes / tuple / a non-ASCII name — never a per-dispatch drop
    (native-library/reference.md § conjured.lib.gbnf_trainable; hash-model.md § What the
    pipeline-hash absorbs). RED if the construction stops rejecting a described field. The
    remediation names the delivering wire and [annotations]."""
    field = FieldDecl(name="dialogue", type=primitive("str"), description="The NPC line.")
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert exc.value.rule_id == "R-handler-005"
    assert "description" in exc.value.actual
    assert exc.value.file_path == SCHEMA_SOURCE
    assert exc.value.section_path == "trainable.output_schema"
    assert "openai_compatible" in exc.value.remediation_hint
    assert "annotations" in exc.value.remediation_hint


def test_a_described_NESTED_output_field_is_rejected_at_construction():
    """The GBNF description reject reaches nested members too (the walker descends
    `properties`): a described field inside a nested object is as unhashable-on-this-wire as a
    top-level one. RED if the walker stops descending into nested objects."""
    nested_field = FieldDecl(
        name="mood",
        type=nested(
            FieldDecl(name="label", type=literal("happy", "sad")),
            FieldDecl(name="intensity", type=primitive("int"), description="0..10 strength."),
        ),
    )
    with pytest.raises(ContractViolation) as exc:
        make_adapter(out_fields=(nested_field,))
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert "mood.intensity" in exc.value.actual


# ---------------------------------------------------------------------------
# Wire error paths — every failure the exact structured class, never a retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "match"),
    [
        ("http_500", "HTTP 500"),
        ("non_json_body", "not JSON"),
        ("non_object_body", "not a JSON object"),
        ("no_content", "no content"),
        ("content_not_text", "content is not text"),
        ("truncated", "truncated"),
        ("non_json_content", "not the constrained JSON"),
    ],
)
def test_wire_failures_raise_trainable_wire_error_exactly_once(mode, match):
    fake = FakeLlamaServer(EMISSION, mode=mode)
    dispatch = make_dispatch(make_adapter(fake=fake))
    with pytest.raises(TrainableWireError, match=match):
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert len(fake.requests) == 1


# ---------------------------------------------------------------------------
# Transport handling — config dials, timeout, auth, memoization
# ---------------------------------------------------------------------------


def test_every_dial_reaches_the_wire_and_max_tokens_maps_to_n_predict():
    """The unpinned-omit wire path is REMOVED (the N2 ruling-1 dial defaults): an
    unstated dial resolves to the DECLARED ship-time default in the shipped TOML (the
    values' only home) through the shared effective-config derivation, and every dial
    always reaches the wire — the serving runtime's own defaults never apply.
    ``max_tokens`` rides the wire as llama.cpp's ``n_predict``."""
    from conjured.validator.compile import effective_config

    st = shipped_service_type()
    declared_defaults = {f.name: f.default for f in st.config_schema}
    # The TOML is the value home (extras defaults to the empty table — D3).
    assert declared_defaults == {"temperature": 0.8, "max_tokens": 4096, "extras": {}}
    effective = effective_config(
        {"max_tokens": 128}, st, composition_ref="c", section_path="trainable.config"
    )
    fake = FakeLlamaServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake), config=effective)
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["body"]["temperature"] == 0.8  # the declared default, on the wire
    assert "max_tokens" not in request["body"]  # the llama.cpp name is n_predict
    assert request["body"]["n_predict"] == 128  # the supplied override wins


def test_extras_sampling_tail_delivers_verbatim_to_the_wire_body():
    # D3: the open `extras` table merges verbatim into the wire body's generation-parameter
    # surface (the cross-server sampling tail the enumerated dial core does not name). The
    # GBNF reserved set differs from the OpenAI sibling's, but the verbatim-merge seal is the
    # same — a non-colliding extras key reaches the llama.cpp body untouched.
    fake = FakeLlamaServer(EMISSION)
    dispatch = make_dispatch(
        make_adapter(fake=fake),
        config={"temperature": 0.2, "max_tokens": 64,
                "extras": {"top_p": 0.9, "top_k": 40, "mirostat": 2}},
    )
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["body"]["top_p"] == 0.9
    assert request["body"]["top_k"] == 40
    assert request["body"]["mirostat"] == 2
    # The engine's owned wire keys remain present and authoritative alongside the extras tail.
    assert request["body"]["temperature"] == 0.2
    assert request["body"]["model"] == "qwen3.5-4b-gguf"


def test_owned_wire_keys_win_over_extras_defense_in_depth():
    # Compose rejects an extras key naming a reserved wire key; defense in depth, invoke()
    # writes its owned keys AFTER the **extras merge, so an engine wire key can never be
    # overridden by extras even if the compose disjointness check had a gap. The GBNF reserved
    # set is {model, prompt, temperature, n_predict, grammar} — distinct from the OpenAI
    # sibling's. A colliding `n_predict` (llama.cpp's token bound) loses to the engine-written
    # `max_tokens`. RED if invoke() inverts the order (merging `extras` AFTER the owned keys).
    fake = FakeLlamaServer(EMISSION)
    dispatch = make_dispatch(
        make_adapter(fake=fake),
        config={"temperature": 0.2, "max_tokens": 64, "extras": {"n_predict": 1}},
    )
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["body"]["n_predict"] == 64  # the engine dial wins, not the extras 1


def test_owned_grammar_key_wins_over_extras_defense_in_depth():
    # The seal's most load-bearing arm for THIS wire: a colliding `grammar` extras key must
    # never displace the engine-rendered decode constraint (the server-side seal IS the
    # grammar). The owned `grammar` is written after the merge, so the submitted body carries
    # the real rendered grammar, not the extras imposter — and the strict fake validates it.
    fake = FakeLlamaServer(EMISSION)
    expected = grammar_from_constraint(
        render_output_constraint(OUT_FIELDS, schema_source=SCHEMA_SOURCE)
    )
    dispatch = make_dispatch(
        make_adapter(fake=fake),
        config={"temperature": 0.2, "max_tokens": 64,
                "extras": {"grammar": 'root ::= "imposter"'}},
    )
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["body"]["grammar"] == expected  # the rendered grammar wins
    assert check_gbnf(request["body"]["grammar"]) == []


def test_transport_timeout_and_bearer_auth_apply_per_call(monkeypatch):
    # api_key_ref is a "[scheme]payload" secret reference the adapter resolves at dispatch via
    # the blessed resolver (never a raw bearer in the deployment TOML — deployment reference
    # § Secret references).
    monkeypatch.setenv("LLM_PROD", "sk-local-test")
    fake = FakeLlamaServer(EMISSION)
    transport = {**TRANSPORT, "api_key_ref": "[env]LLM_PROD"}
    dispatch = make_dispatch(make_adapter(fake=fake), transport=transport)
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert request["timeout_s"] == 30.0
    assert request["headers"]["Authorization"] == "Bearer sk-local-test"  # resolved from $LLM_PROD


def test_no_api_key_means_no_auth_header():
    fake = FakeLlamaServer(EMISSION)
    dispatch = make_dispatch(make_adapter(fake=fake))
    dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    [request] = fake.requests
    assert "Authorization" not in request["headers"]


def test_wire_client_is_memoized_instance_state():
    fake = FakeLlamaServer(EMISSION)
    adapter = make_adapter(fake=fake)
    dispatch = make_dispatch(adapter)
    dispatch(reads={"assembled_prompt": "a"}, ctx=CTX)
    dispatch(reads={"assembled_prompt": "b"}, ctx=CTX)
    assert adapter._transport is fake  # an injected client is never overwritten
    assert len(fake.requests) == 2


def test_invoke_does_not_mutate_the_input_payload():
    fake = FakeLlamaServer(EMISSION)
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
    fake = FakeLlamaServer(EMISSION)  # serves as the loopback responder
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


def test_real_wire_client_lets_a_url_error_ride_raw(monkeypatch):
    """The transport-level failure half (the HTTP-status→structured half is above). The
    `except urllib.error.HTTPError` arm catches ONLY a status error (→ `(status, body)` as
    data); a transport-level failure — connection refused, DNS — surfaces as a bare
    `urllib.error.URLError` (HTTPError's non-status superclass), which is NOT caught and rides
    RAW out of `urllib_transport` (there is no status/body to return). The Phase-3 runner
    boundary wraps it; the transport must not swallow it. RED if the `except` widens past
    `HTTPError` to `URLError` — a bare URLError would then be caught and `.code` / `.read()`
    accessed, neither of which a non-HTTPError URLError has.

    A monkeypatched `urlopen` is a legitimate double: it is stdlib I/O OUTSIDE engine code,
    and it fails exactly where the runtime would — a real refused connection raises URLError."""
    import urllib.error
    import urllib.request

    def refuse(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(urllib.error.URLError) as exc:
        urllib_transport("http://localhost:1/v1/completions", b"{}", {}, 1.0)
    assert not isinstance(exc.value, urllib.error.HTTPError)  # the raw transport error, not a status


# ---------------------------------------------------------------------------
# The standing double rule — the fake itself fails where the runtime would
# ---------------------------------------------------------------------------


def test_fake_404s_on_a_wrong_path_like_a_real_server():
    fake = FakeLlamaServer(EMISSION)
    body = {"prompt": "x", "grammar": 'root ::= "a"'}
    status, payload = fake(
        "http://localhost:8080/v1/completions", json.dumps(body).encode(), {}, None
    )
    assert status == 404
    assert "unknown path" in json.loads(payload)["error"]


def test_fake_rejects_a_broken_grammar_like_llama_server():
    fake = FakeLlamaServer(EMISSION)
    body = {"prompt": "x", "grammar": "root ::= undefined-rule"}
    status, payload = fake(
        "http://localhost:8080/completion", json.dumps(body).encode(), {}, None
    )
    assert status == 400
    problems = json.loads(payload)["error"]
    assert any("undefined" in p for p in problems)


def test_fake_rejects_a_missing_grammar_like_llama_server():
    fake = FakeLlamaServer(EMISSION)
    body = {"prompt": "x"}  # an unconstrained call — the seal would not exist
    status, payload = fake(
        "http://localhost:8080/completion", json.dumps(body).encode(), {}, None
    )
    assert status == 400
    assert any("grammar" in p for p in json.loads(payload)["error"])


def test_extras_cannot_smuggle_the_max_tokens_dial():
    """RED-on-removal for the reserved-keys widening (LIB-1): the token-bound dial is
    reserved under BOTH its names (`max_tokens` the [config_schema] field, `n_predict`
    its wire rendering), so an `extras = { max_tokens = N }` second supply route is
    rejected at compose naming the dial's real home - never hash-covered dead data or
    a live server-interpreted alias (native-library reference, reserved-keys-disjoint)."""
    from conjured.errors import ContractViolation
    from conjured.lib.gbnf_trainable import GBNFTrainable
    from conjured.validator.resolve_adapter import check_extras_disjoint

    with pytest.raises(ContractViolation, match="max_tokens"):
        check_extras_disjoint(
            GBNFTrainable,
            {"temperature": 0.8, "max_tokens": 4096, "extras": {"max_tokens": 99}},
            qualified_name="conjured.lib.gbnf_trainable.GBNFTrainable",
            toml_path="gbnf_trainable.toml",
        )
