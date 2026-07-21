"""The explicit-null value form + the 31#2 transport-coverage extension — the seal set.

Canon: ``handler/reference.md`` § Binding value-supply grammar (the ``explicit-null``
region) and ``pipeline/reference.md`` ``R-pipeline-001/transport-coverage`` (landed at
stamp #42). Each test is the exact adversary a claimed guarantee defends against —
RED if the mechanism is removed, per the guarantees-need-a-failing-case-test discipline:

- the 31#2 adversary: an uncovered transport surface on a COMPOSITION-supplied binding
  rejects at compose (the pre-extension code never walked composition supplies);
- the uniform presence law: a nullable-declared field ABSENT rejects — nullable grants
  no presence exemption; considered-and-null is spelled ``{ null = true }``, present;
- the form's admission, forced spelling, position rejections (identity / config /
  compile-param), the whole-binding single-field law, and the hash fold as null.
"""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation, ContractViolationGroup
from conjured.hasher import pipeline_hash
from conjured.validator import DeclarationRegistry, compile_pipeline, loads
from conjured.validator.normalize import is_explicit_null, normalize_binding_value

from . import fixtures as F

# The dialogue service-type with a nullable transport field alongside the required
# endpoint — the canon worked-example shape (api_key nullable; supplied as a value or
# the explicit null, never omitted).
SERVICE_TYPE_DIALOGUE_NULLABLE = F.SERVICE_TYPE_DIALOGUE.replace(
    'endpoint = { type = "str" }',
    'endpoint = { type = "str" }\napi_key = { type = "str | None", nullable = true }',
)

DEPLOYMENT_HEAD = '[training_contract]\nintegrity_enforcement=true\n'


def _trainable_setup(service_type_toml: str = F.SERVICE_TYPE_DIALOGUE,
                     composition_toml: str = F.TRAINABLE_COMPOSITION):
    """`F.build_trainable` with substitutable service-type / composition TOMLs."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(service_type_toml, "service_type", file_path="st.toml"))
    reg.add_handler("acme.ctx", loads(F.TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg.add_handler("transform.formatter", loads(F.TRANSFORM_FORMATTER, "handler", file_path="fmt.toml"))
    reg.add_composition("trainables/dialogue.toml", loads(composition_toml, "composition", file_path="c.toml"))
    pipeline = loads(F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path="p.toml")
    return reg, pipeline


def _violations(reg, pipeline, deployment_toml: str) -> list[ContractViolation]:
    deployment = loads(deployment_toml, "deployment", file_path="d.toml")
    try:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")
    except ContractViolationGroup as group:
        return list(group.violations)
    except ContractViolation as cv:
        return [cv]
    return []


# ---------------------------------------------------------------------------
# The 31#2 adversary — coverage over composition-supplied bindings
# ---------------------------------------------------------------------------


# verifies: transport-coverage-composition-bindings
def test_composition_backend_missing_transport_block_rejects():
    """The original 31#2 fail-loud gap: the composition's backend binding `llm` has NO
    covering transport block — must reject at compose (RED with the coverage extension
    reverted: the pipeline itself declares no service binding, so the old walk saw
    nothing)."""
    reg, pipeline = _trainable_setup()
    found = _violations(reg, pipeline, DEPLOYMENT_HEAD)  # no transport.llm at all
    assert any(
        v.check is Check.TRANSPORT_COVERAGE and "transport.llm" in (v.section_path or "")
        for v in found
    ), f"expected transport-coverage-gap on the composition backend, got {found}"


# verifies: transport-coverage-composition-bindings
def test_composition_backend_missing_required_field_rejects():
    """A covering block that omits a REQUIRED (non-nullable) transport_schema field of the
    composition backend's service-type rejects at compose."""
    reg, pipeline = _trainable_setup()
    found = _violations(reg, pipeline, "[transport.llm]\n" + DEPLOYMENT_HEAD)  # endpoint missing
    assert any(
        v.check is Check.TRANSPORT_COVERAGE and "endpoint" in v.actual
        for v in found
    ), f"expected a missing-endpoint coverage gap, got {found}"


# ---------------------------------------------------------------------------
# The uniform presence law — no nullable exemption; null is spelled, present
# ---------------------------------------------------------------------------


# verifies: uniform-presence-no-nullable-exemption
def test_nullable_transport_field_absent_rejects():
    """Absence of a declared NULLABLE field is a coverage violation — omission is never a
    null (exhaustive declaration's empty-but-present principle at the field level)."""
    reg, pipeline = _trainable_setup(SERVICE_TYPE_DIALOGUE_NULLABLE)
    found = _violations(
        reg, pipeline, '[transport.llm]\nendpoint="https://x"\n' + DEPLOYMENT_HEAD
    )  # api_key (nullable) omitted
    assert any(
        v.check is Check.TRANSPORT_COVERAGE and "api_key" in v.actual for v in found
    ), f"expected the nullable field's absence to reject, got {found}"


# verifies: explicit-null-nullable-only
def test_explicit_null_on_nullable_transport_composes():
    """`{ null = true }` on a nullable-declared transport field is the considered-and-null
    supply — presence satisfied, composes green."""
    reg, pipeline = _trainable_setup(SERVICE_TYPE_DIALOGUE_NULLABLE)
    deployment = loads(
        '[transport.llm]\nendpoint="https://x"\napi_key={null=true}\n' + DEPLOYMENT_HEAD,
        "deployment", file_path="d.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")


# verifies: explicit-null-nullable-only
def test_explicit_null_on_non_nullable_transport_rejects():
    reg, pipeline = _trainable_setup(SERVICE_TYPE_DIALOGUE_NULLABLE)
    found = _violations(
        reg, pipeline,
        '[transport.llm]\nendpoint={null=true}\napi_key="k"\n' + DEPLOYMENT_HEAD,
    )
    assert any(v.check is Check.EXPLICIT_NULL_TARGET for v in found), found


# verifies: explicit-null-forced-spelling
def test_explicit_null_false_is_malformed():
    """`{ null = false }` is not "not null" — the spelling is forced; there is no
    negative spelling (a present value already is one). The malformed spelling fires
    at deployment PARSE (pipeline/conformance.md § Explicit-null form: "at parse, the
    same split the { file } sibling form uses" — the PARSE-1 sweep covers transport
    blocks), before any compose ever sees the declaration."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[transport.llm]\nendpoint="https://x"\napi_key={null=false}\n' + DEPLOYMENT_HEAD,
            "deployment", file_path="d.toml",
        )
    assert exc.value.check is Check.MALFORMED_DECLARATION


# verifies: explicit-null-forced-spelling
def test_explicit_null_extra_key_is_malformed():
    """The extra-key mis-spelling fires at deployment parse, exactly as the
    `{ null = false }` sibling above (the reserved key admits exactly one spelling)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[transport.llm]\nendpoint="https://x"\napi_key={null=true, ref="x"}\n' + DEPLOYMENT_HEAD,
            "deployment", file_path="d.toml",
        )
    assert exc.value.check is Check.MALFORMED_DECLARATION


def test_explicit_null_malformed_in_identity_supply_fires_at_parse():
    """RED-on-removal for the PARSE-1 sweep's supply arm: a malformed reserved form in
    a pipeline service_bindings identity position is caught at PARSE — a
    parsed-and-registered-but-never-composed declaration cannot carry it undetected."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.h"\n'
            '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel={null=false}\n'
            '[inputs]\nx={type="str"}\n',
            "pipeline", file_path="p.toml",
        )
    assert exc.value.check is Check.MALFORMED_DECLARATION


# ---------------------------------------------------------------------------
# The type-coherent handle join
# ---------------------------------------------------------------------------


# verifies: transport-handle-type-coherence
def test_shared_handle_differing_service_types_rejects():
    """One as-written handle bound to two service-types within one composed scope — the
    shared transport block cannot satisfy two transport_schemas."""
    reg, _ = _trainable_setup()
    reg.add_service_type(loads(F.SERVICE_TYPE_LLM, "service_type", file_path="st2.toml"))
    reg.add_handler("acme.speak", loads(
        '[service]\n[reads]\ndialogue_response={type="str"}\n[output_schema]\nspoken={type="str"}\n'
        '[service_bindings]\nllm={type="conjured_llm.structured_output"}',
        "handler", file_path="speak.toml"))
    pipeline = loads(
        '[meta]\nname="acme.dialogue"\n'
        '[[nodes]]\nkind="handler"\nname="acme.ctx"\n'
        '[[nodes]]\nkind="composition"\nname="trainables/dialogue.toml"\n'
        '[[nodes]]\nkind="handler"\nname="acme.speak"\n'
        '[service_bindings.llm]\ntype="conjured_llm.structured_output"\nmodel="m"\n'
        '[service_bindings.llm.config]\ntemperature=0.7\n'
        '[inputs]\nraw={type="str"}\n[outputs]\nspoken={type="str"}\n',
        "pipeline", file_path="p.toml")
    found = _violations(reg, pipeline, '[transport.llm]\nendpoint="https://x"\n' + DEPLOYMENT_HEAD)
    assert any(v.check is Check.TRANSPORT_HANDLE_COHERENCE for v in found), found


# ---------------------------------------------------------------------------
# Recognized-and-rejected positions — identity / config / compile-param
# ---------------------------------------------------------------------------


# verifies: explicit-null-nullable-only
def test_identity_explicit_null_rejects():
    """An explicit null at an identity position is recognized-and-rejected, never absorbed
    raw as data flowing to the adapter (identity fields admit no nullable declaration)."""
    comp = F.TRAINABLE_COMPOSITION.replace(
        'model = "qwen3.5-4b-gguf"', 'model = { null = true }'
    )
    reg, pipeline = _trainable_setup(composition_toml=comp)
    found = _violations(reg, pipeline, '[transport.llm]\nendpoint="https://x"\n' + DEPLOYMENT_HEAD)
    assert any(v.check is Check.EXPLICIT_NULL_TARGET for v in found), found


# verifies: explicit-null-nullable-only
def test_config_explicit_null_rejects():
    comp = F.TRAINABLE_COMPOSITION.replace(
        "temperature = 0.7", "temperature = { null = true }"
    )
    reg, pipeline = _trainable_setup(composition_toml=comp)
    found = _violations(reg, pipeline, '[transport.llm]\nendpoint="https://x"\n' + DEPLOYMENT_HEAD)
    assert any(v.check is Check.EXPLICIT_NULL_TARGET for v in found), found


# verifies: explicit-null-nullable-only
def test_compile_param_explicit_null_rejects_at_parse():
    """Compile parameters carry no nullable declaration — the form is recognized-and-
    rejected at declaration load, never handed to a compiler as data."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[transform]\n[reads]\nx={type="str"}\n[output_schema]\ny={type="str"}\n'
            '[bindings.norm]\ncompile="regex"\npattern={null=true}\n',
            "handler", file_path="h.toml")
    assert exc.value.check is Check.EXPLICIT_NULL_TARGET


# ---------------------------------------------------------------------------
# The whole-binding position + ship-time defaults + the compose join
# ---------------------------------------------------------------------------


def _binding_fields(binding_toml: str, name: str):
    decl = loads(
        '[transform]\n[reads]\nx={type="str"}\n[output_schema]\ny={type="str"}\n' + binding_toml,
        "handler", file_path="h.toml")
    (binding,) = [b for b in decl.bindings if b.name == name]
    return binding.body.fields, binding.body


NULLABLE_SINGLE = '[bindings.greeting]\ngreeting={type="str | None"}\n'
PLAIN_SINGLE = '[bindings.greeting]\ngreeting={type="str"}\n'
MULTI = '[bindings.config]\nmarker_set={type="str"}\nsuffix={type="str | None"}\n'


# verifies: explicit-null-normalizes-at-join
def test_whole_binding_explicit_null_normalizes_to_none_for_nullable_single_field():
    fields, _ = _binding_fields(NULLABLE_SINGLE, "greeting")
    assert normalize_binding_value(fields, {"null": True}, owner="bindings.test", composition_ref="acme.test") is None


# verifies: explicit-null-nullable-only
def test_whole_binding_explicit_null_rejects_for_non_nullable_single_field():
    fields, _ = _binding_fields(PLAIN_SINGLE, "greeting")
    with pytest.raises(ContractViolation) as exc:
        normalize_binding_value(fields, {"null": True}, owner="bindings.test", composition_ref="acme.test")
    assert exc.value.check is Check.EXPLICIT_NULL_TARGET


# verifies: explicit-null-nullable-only
def test_whole_binding_explicit_null_rejects_for_multi_field_binding():
    fields, _ = _binding_fields(MULTI, "config")
    with pytest.raises(ContractViolation) as exc:
        normalize_binding_value(fields, {"null": True}, owner="bindings.test", composition_ref="acme.test")
    assert exc.value.check is Check.EXPLICIT_NULL_TARGET


# verifies: explicit-null-normalizes-at-join
def test_field_level_explicit_null_resolves_per_field():
    fields, _ = _binding_fields(MULTI, "config")
    out = normalize_binding_value(fields, {"marker_set": "brackets", "suffix": {"null": True}}, owner="bindings.test", composition_ref="acme.test")
    assert out == {"marker_set": "brackets", "suffix": None}


# verifies: explicit-null-nullable-only
def test_field_level_explicit_null_rejects_non_nullable_field():
    fields, _ = _binding_fields(MULTI, "config")
    with pytest.raises(ContractViolation) as exc:
        normalize_binding_value(fields, {"marker_set": {"null": True}, "suffix": "s"}, owner="bindings.test", composition_ref="acme.test")
    assert exc.value.check is Check.EXPLICIT_NULL_TARGET


# verifies: explicit-null-nullable-only
def test_nullable_single_field_default_may_be_explicit_null():
    """A nullable single-field binding MAY declare `default = { null = true }` — without
    the form, a null default is inexpressible (the considered-vs-forgot collapse at the
    default surface). The default resolves through the same compose join."""
    fields, body = _binding_fields(
        '[bindings.greeting]\ndefault={null=true}\ngreeting={type="str | None"}\n', "greeting")
    assert body.has_default
    assert normalize_binding_value(fields, body.default, owner="bindings.test", composition_ref="acme.test") is None


# verifies: explicit-null-nullable-only
def test_non_nullable_default_explicit_null_rejects_at_join():
    fields, body = _binding_fields(
        '[bindings.greeting]\ndefault={null=true}\ngreeting={type="str"}\n', "greeting")
    with pytest.raises(ContractViolation) as exc:
        normalize_binding_value(fields, body.default, owner="bindings.test", composition_ref="acme.test")
    assert exc.value.check is Check.EXPLICIT_NULL_TARGET


# verifies: explicit-null-forced-spelling
def test_default_misspelled_null_rejects_at_parse():
    with pytest.raises(ContractViolation) as exc:
        _binding_fields(
            '[bindings.greeting]\ndefault={null=false}\ngreeting={type="str | None"}\n', "greeting")
    assert exc.value.check is Check.MALFORMED_DECLARATION


def test_recognition_is_position_level_not_recursive():
    """A composite value's interior is data: a `{ null = true }`-SHAPED table nested inside
    a bare dict-typed single-field value is never recognized (a collection/table member is
    not a field position)."""
    fields, _ = _binding_fields('[bindings.table]\ntable={type="dict[str, bool]"}\n', "table")
    # Bare dict-typed supply (not the one-field wrapper): interior untouched.
    out = normalize_binding_value(fields, {"a": True}, owner="bindings.test", composition_ref="acme.test")
    assert out == {"a": True}
    inner = normalize_binding_value(fields, {"table": {"a": True}}, owner="bindings.test", composition_ref="acme.test")
    assert inner == {"a": True}  # unwrapped; interior is data


# verifies: explicit-null-nullable-only
def test_config_field_default_explicit_null_rejects_at_load():
    """A [config_schema] field's declared ship-time default is an engine-read value
    position; config fields admit no nullable declaration, so `default = { null = true }`
    is recognized-and-rejected at service-type load — never absorbed as data into
    delivery and the hashes."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            'name="s"\n[identity_schema]\nm={type="str"}\n[transport_schema]\ne={type="str"}\n'
            '[config_schema]\ntemperature={type="float", default={null=true}}\n',
            "service_type", file_path="st.toml")
    assert exc.value.check is Check.EXPLICIT_NULL_TARGET


# verifies: explicit-null-forced-spelling
def test_config_field_default_misspelled_null_rejects_at_load():
    with pytest.raises(ContractViolation) as exc:
        loads(
            'name="s"\n[identity_schema]\nm={type="str"}\n[transport_schema]\ne={type="str"}\n'
            '[config_schema]\ntemperature={type="float", default={null=false}}\n',
            "service_type", file_path="st.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION


# verifies: explicit-null-forced-spelling
def test_classifier_spelling_is_forced():
    assert is_explicit_null({"null": True}, owner="x", file_path="x.toml") is True
    assert is_explicit_null("hello", owner="x", file_path="x.toml") is False
    assert is_explicit_null({"file": "p.toml"}, owner="x", file_path="x.toml") is False
    for bad in ({"null": False}, {"null": 1}, {"null": True, "k": 1}, {"null": "true"}):
        with pytest.raises(ContractViolation) as exc:
            is_explicit_null(bad, owner="x", file_path="x.toml")
        assert exc.value.check is Check.MALFORMED_DECLARATION
    # The location-bearing seal holds through the classifier: a locationless raise is an
    # engine bug the ContractViolation constructor makes unrepresentable (cv-requires-location).
    with pytest.raises(ValueError):
        is_explicit_null({"null": False}, owner="x")


# ---------------------------------------------------------------------------
# The hash fold — the form is a spelling of the null value
# ---------------------------------------------------------------------------


# verifies: explicit-null-normalizes-at-join
def test_binding_explicit_null_folds_as_null_into_the_pipeline_hash():
    """Hash sensitivity: an explicit-null binding supply and a concrete value are two
    compositions (different pipeline-hash); the explicit null itself is deterministic."""
    def build(supply: str):
        reg = DeclarationRegistry()
        reg.add_handler("acme.greet", loads(
            '[transform]\n[reads]\nx={type="str"}\n[output_schema]\ny={type="str"}\n'
            '[bindings.greeting]\ngreeting={type="str | None"}\n',
            "handler", file_path="g.toml"))
        pipeline = loads(
            '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.greet"\n'
            f'bindings = {{ greeting = {supply} }}\n'
            '[inputs]\nx={type="str"}\n[outputs]\ny={type="str"}\n',
            "pipeline", file_path="p.toml")
        return pipeline_hash(pipeline, reg)

    null_hash = build("{ null = true }")
    value_hash = build('"hello"')
    assert null_hash != value_hash
    assert null_hash == build("{ null = true }")
    # Spelling-neutrality: the whole-binding explicit null and the one-field-wrapper
    # spelling are one logical value → ONE pipeline-hash (RED if the hasher folds the raw
    # reserved table instead of the normalized null value).
    assert null_hash == build("{ greeting = { null = true } }")


# ---------------------------------------------------------------------------
# Hook transport — nullable hook field satisfied by the explicit null
# ---------------------------------------------------------------------------


# verifies: explicit-null-nullable-only
def test_nullable_hook_transport_field_explicit_null_composes():
    """A nullable hook transport field supplied as `{ null = true }` composes (presence
    satisfied; no type-match against a dict — the null resolves first)."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_LLM, "service_type", file_path="st.toml"))
    reg.add_handler("acme.normalize", loads(F.TRANSFORM_NORMALIZE, "handler", file_path="h.norm.toml"))
    reg.add_handler("acme.respond", loads(F.SERVICE_RESPOND, "handler", file_path="h.respond.toml"))
    nullable_hook = F.HOOK_LOG.replace(
        'path = { type = "str" }', 'path = { type = "str | None" }')
    reg.add_handler("acme.log", loads(nullable_hook, "handler", file_path="h.log.toml"))
    pipeline = loads(F.PIPELINE, "pipeline", file_path="p.toml")
    deployment = loads(
        '[transport.llm]\nendpoint="https://x"\n'
        '[hook_transport."acme.log"]\npath={null=true}\n' + DEPLOYMENT_HEAD,
        "deployment", file_path="d.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")


# verifies: uniform-presence-no-nullable-exemption
def test_nullable_hook_transport_field_absent_rejects():
    """The hook half of the uniform presence law: a nullable-declared hook transport field
    ABSENT from the covering block rejects — nullable grants no presence exemption on the
    hook arm either."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_LLM, "service_type", file_path="st.toml"))
    reg.add_handler("acme.normalize", loads(F.TRANSFORM_NORMALIZE, "handler", file_path="h.norm.toml"))
    reg.add_handler("acme.respond", loads(F.SERVICE_RESPOND, "handler", file_path="h.respond.toml"))
    nullable_hook = F.HOOK_LOG.replace(
        'path = { type = "str" }', 'path = { type = "str | None" }')
    reg.add_handler("acme.log", loads(nullable_hook, "handler", file_path="h.log.toml"))
    pipeline = loads(F.PIPELINE, "pipeline", file_path="p.toml")
    found = _violations(
        reg, pipeline,
        '[transport.llm]\nendpoint="https://x"\n'
        '[hook_transport."acme.log"]\n' + DEPLOYMENT_HEAD,  # path (nullable) omitted
    )
    assert any(
        v.check is Check.HOOK_TRANSPORT_COVERAGE and "path" in v.actual for v in found
    ), found


# verifies: explicit-null-nullable-only
def test_non_nullable_hook_transport_field_explicit_null_rejects():
    reg, pipeline, _ = F.build_base()
    found = _violations(
        reg, pipeline,
        '[transport.llm]\nendpoint="https://x"\n'
        '[hook_transport."acme.log"]\npath={null=true}\n' + DEPLOYMENT_HEAD,
    )
    assert any(v.check is Check.EXPLICIT_NULL_TARGET for v in found), found
