"""Negative acceptance — **one test per binned compose-time conformance check**, each driving
that check's violation example (from the ``conformance.md`` entries) and asserting the correct
``ContractViolation``. This is the unit's core acceptance: the validator is honest iff every
check fires on its example.

The ``CASES`` table is parametrized by :class:`Check`; ``test_every_check_has_a_negative``
asserts the table covers **every** ``Check`` member — so a future check cannot land without a
firing negative test.
"""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation, ContractViolationGroup
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

from . import fixtures as F


# ---------------------------------------------------------------------------
# Stage 1 — declaration parse / load
# ---------------------------------------------------------------------------


def case_handler_kind_header():
    loads('[transform]\n[service]\n[reads]\n[output_schema]\nx={type="str"}\n[service_bindings]', "handler", file_path="x.toml")


def case_closed_grammar():
    loads('[transform]\n[reads]\n[output_schema]\nx={type="str"}\n[retry_policy]\nmax=3', "handler", file_path="x.toml")


def case_section_presence():
    loads('[transform]\n[output_schema]\nx={type="str"}', "handler", file_path="x.toml")  # [reads] absent


def case_body_required():
    loads('[transform]\n[reads]\n[output_schema]', "handler", file_path="x.toml")  # output_schema empty


def case_channel_type_token():
    loads('[transform]\n[reads]\n[output_schema]\nx={type="frobnicate"}', "handler", file_path="x.toml")


def case_nullable_placement():
    loads('name="s"\n[identity_schema]\nm={type="str | None", nullable=true}\n[transport_schema]\ne={type="str"}\n[config_schema]',
          "service_type", file_path="x.toml")


def case_unknown_composition_kind():
    loads('[meta]\nkind="frobnicate"\nname="c"\n[inputs]\n[outputs]\nx={type="str"}', "composition", file_path="x.toml")


def case_malformed_declaration():
    loads('[transform', "handler", file_path="x.toml")  # TOML syntax error


# ---------------------------------------------------------------------------
# Stage 2 — compose-time validation + graph compilation
# ---------------------------------------------------------------------------


def case_handler_name_resolution():
    reg = DeclarationRegistry()
    pipeline = loads('[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.missing"\n', "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_service_type_resolution():
    reg = DeclarationRegistry()
    reg.add_handler("acme.s", loads(
        '[service]\n[reads]\nx={type="str"}\n[output_schema]\ny={type="str"}\n[service_bindings]\nllm={type="x.unregistered"}',
        "handler", file_path="s.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.s"\n[service_bindings.llm]\ntype="x.unregistered"\n[inputs]\nx={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_read_write_shape_mismatch():
    reg = DeclarationRegistry()
    reg.add_handler("acme.w", loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\nc={type="str"}', "handler", file_path="w.toml"))
    reg.add_handler("acme.r", loads('[transform]\n[reads]\nc={type="int"}\n[output_schema]\no={type="int"}', "handler", file_path="r.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.w"\n[[nodes]]\nkind="handler"\nname="acme.r"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_wiring_map_port():
    reg = DeclarationRegistry()
    reg.add_handler("acme.add", loads('[transform]\n[reads]\nleft={type="int"}\nright={type="int"}\n[output_schema]\no={type="int"}', "handler", file_path="add.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.add"\nreads_map={left="a", rihgt="b"}\n[inputs]\na={type="int"}\nb={type="int"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_dangling_identity_port():
    reg = DeclarationRegistry()
    reg.add_handler("acme.add", loads('[transform]\n[reads]\nleft={type="int"}\nright={type="int"}\n[output_schema]\no={type="int"}', "handler", file_path="add.toml"))
    # `right` unmapped → identity channel `right`, written by no one and not in [inputs].
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.add"\nreads_map={left="base"}\n[inputs]\nbase={type="int"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_read_port_unclosed():
    reg = DeclarationRegistry()
    reg.add_handler("acme.g", loads('[transform]\n[reads]\nenemy_health={type="int"}\n[output_schema]\no={type="int"}', "handler", file_path="g.toml"))
    # explicit map to a typo'd channel that nobody writes and is not in [inputs].
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.g"\nreads_map={enemy_health="nemy_health"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_single_assignment():
    reg = DeclarationRegistry()
    reg.add_handler("acme.x", loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}', "handler", file_path="x.toml"))
    # read-map and write-map both target channel `c`.
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.x"\nreads_map={i="c"}\nwrites_map={o="c"}\n[inputs]\nc={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_channel_write_overlap():
    reg = DeclarationRegistry()
    for name in ("acme.a", "acme.b"):
        reg.add_handler(name, loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\nstate={type="str"}', "handler", file_path=f"{name}.toml"))
    # both write channel `state`, no [merge].
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.a"\nwrites_map={state="npc_state"}\n'
        '[[nodes]]\nkind="handler"\nname="acme.b"\nwrites_map={state="npc_state"}\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_merge_strategy_type():
    reg = DeclarationRegistry()
    for name in ("acme.a", "acme.b"):
        reg.add_handler(name, loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\nstate={type="str"}', "handler", file_path=f"{name}.toml"))
    # `npc_state` is str; append_list requires a list-typed channel.
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.a"\nwrites_map={state="npc_state"}\n'
        '[[nodes]]\nkind="handler"\nname="acme.b"\nwrites_map={state="npc_state"}\n'
        '[merge]\nnpc_state="append_list"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_binding_supply_incomplete():
    reg = DeclarationRegistry()
    reg.add_handler("acme.n", loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n[bindings.config]\nsystem_prompt={type="str"}', "handler", file_path="n.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.n"\n[inputs]\ni={type="str"}\n',  # bindings.config not supplied
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_service_binding_cardinality():
    reg = DeclarationRegistry()
    # A service handler declaring zero service bindings (empty [service_bindings]).
    reg.add_handler("acme.s", loads('[service]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n[service_bindings]', "handler", file_path="s.toml"))
    pipeline = loads('[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.s"\n[inputs]\ni={type="str"}\n', "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_identity_transport_placement():
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_LLM, "service_type", file_path="st.toml"))
    reg.add_handler("acme.s", loads(SERVICE_BINDS_LLM, "handler", file_path="s.toml"))
    # `endpoint` is a transport field placed in the identity supply block.
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.s"\n'
        '[service_bindings.llm]\ntype="conjured_llm.structured_output"\nmodel="qwen"\nendpoint="https://x"\n'
        '[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_transport_coverage_gap():
    reg, pipeline, _ = F.build_base()
    deployment = loads('[hook_transport."acme.log"]\npath="/x"\n[training_contract]\nintegrity_enforcement=true', "deployment", file_path="d.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")  # transport.llm absent


def case_explicit_null_target():
    # The reserved explicit-null form on a NON-nullable transport field — recognized and
    # rejected, never passed through opaque (handler/reference.md explicit-null region).
    reg, pipeline, _ = F.build_base()
    deployment = loads(
        '[transport.llm]\nendpoint={null=true}\n[hook_transport."acme.log"]\npath="/x"\n'
        '[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")


def _secret_ref_case(api_key_value: str):
    # A secret_ref-declared transport field supplied with the parameterized value — the
    # shared setup for the three R-deployment-003 shape checks (deployment/reference.md
    # § Secret references; the full seal set lives in test_secret_ref_compile.py).
    st = F.SERVICE_TYPE_DIALOGUE.replace(
        'endpoint = { type = "str" }',
        'endpoint = { type = "str" }\napi_key_ref = { type = "secret_ref | None", nullable = true }',
    )
    reg = DeclarationRegistry()
    reg.add_service_type(loads(st, "service_type", file_path="st.toml"))
    reg.add_handler("acme.ctx", loads(F.TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg.add_handler("transform.formatter", loads(F.TRANSFORM_FORMATTER, "handler", file_path="fmt.toml"))
    reg.add_composition("trainables/dialogue.toml", loads(F.TRAINABLE_COMPOSITION, "composition", file_path="c.toml"))
    pipeline = loads(F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path="p.toml")
    deployment = loads(
        f'[transport.llm]\nendpoint="https://x"\napi_key_ref={api_key_value}\n'
        '[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")


def case_secret_ref_malformed():
    # A raw credential pasted where a reference belongs — the exact mistake the whole-value
    # grammar exists to catch at load, never forwarded to a dispatch.
    _secret_ref_case('"sk-raw-bearer-token"')


def case_secret_ref_scheme_unknown():
    # Well-formed reference, bare scheme outside the closed built-in set {env, file} —
    # no fallback store, no guess.
    _secret_ref_case('"[vault]prod/llm"')


def case_secret_resolver_invalid():
    # A dotted (consumer) scheme must import to a callable at load.
    _secret_ref_case('"[no_such_pkg.resolver]prod/llm"')


def case_transport_handle_coherence():
    # One as-written handle (`llm`) bound to TWO service-types within one composed scope —
    # the pipeline's own service binding and the embedded trainable composition's backend
    # (R-pipeline-001/transport-coverage: the join is type-coherent).
    reg, pipeline = F.build_trainable()
    reg.add_service_type(loads(F.SERVICE_TYPE_LLM, "service_type", file_path="st2.toml"))
    reg.add_handler("acme.speak", loads(
        '[service]\n[reads]\ndialogue_response={type="str"}\n[output_schema]\nspoken={type="str"}\n'
        '[service_bindings]\nllm={type="conjured_llm.structured_output"}',
        "handler", file_path="speak.toml"))
    pipeline2 = loads(
        '[meta]\nname="acme.dialogue"\n'
        '[[nodes]]\nkind="handler"\nname="acme.ctx"\n'
        '[[nodes]]\nkind="composition"\nname="trainables/dialogue.toml"\n'
        '[[nodes]]\nkind="handler"\nname="acme.speak"\n'
        '[service_bindings.llm]\ntype="conjured_llm.structured_output"\nmodel="m"\n'
        '[service_bindings.llm.config]\ntemperature=0.7\n'
        '[inputs]\nraw={type="str"}\n[outputs]\nspoken={type="str"}\n',
        "pipeline", file_path="p.toml")
    deployment = loads(
        '[transport.llm]\nendpoint="https://x"\n[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    compile_pipeline(pipeline2, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")


def case_hook_transport_coverage_gap():
    reg, pipeline, _ = F.build_base()
    deployment = loads('[transport.llm]\nendpoint="https://x"\n[training_contract]\nintegrity_enforcement=true', "deployment", file_path="d.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")  # hook_transport absent


def case_inputs_outputs_dead():
    reg = DeclarationRegistry()
    reg.add_handler("acme.n", loads('[transform]\n[reads]\nplayer_input={type="str"}\n[output_schema]\no={type="str"}', "handler", file_path="n.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.n"\n[inputs]\nplayer_input={type="str"}\nsession_id={type="str"}\n',  # session_id read by no node
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_config_schema_supply():
    reg, pipeline = F.build_trainable()
    # Re-register the composition with an undeclared config key (template — a prompt-shaping key).
    bad = F.TRAINABLE_COMPOSITION.replace("temperature = 0.7", 'template = "you are an npc"')
    reg.add_composition("trainables/dialogue.toml", loads(bad, "composition", file_path="c.toml"))
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_streamable_terminal():
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("acme.after", loads('[transform]\n[reads]\ndialogue_response={type="str"}\n[output_schema]\no={type="str"}', "handler", file_path="a.toml"))
    streamable = F.TRAINABLE_COMPOSITION.replace("[trainable]\n", "[trainable]\nstreamable = true\n")
    reg.add_composition("trainables/dialogue.toml", loads(streamable, "composition", file_path="c.toml"))
    # A non-hook node follows the streamable trainable.
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="composition"\nname="trainables/dialogue.toml"\n'
        '[[nodes]]\nkind="handler"\nname="acme.after"\n'
        '[inputs]\nnpc_state={type="str"}\nuser_message={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_streamable_terminal_transitive():
    """The streamable terminal sits inside a nested ``pipeline`` embed; a non-hook node follows
    the EMBED. Placement is evaluated transitively through a terminal embed (pipeline/reference.md
    § streamable terminal-node — "at any nesting layer"), so this must fire STREAMABLE_TERMINAL."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("acme.after", loads('[transform]\n[reads]\ndialogue_response={type="str"}\n[output_schema]\no={type="str"}', "handler", file_path="a.toml"))
    streamable = F.TRAINABLE_COMPOSITION.replace("[trainable]\n", "[trainable]\nstreamable = true\n")
    reg.add_composition("trainables/dialogue.toml", loads(streamable, "composition", file_path="c.toml"))
    # An inner pipeline whose OWN terminal is the streamable trainable.
    reg.add_composition("pipelines/inner.toml", loads(
        '[meta]\nkind="pipeline"\nname="acme.inner"\n'
        '[[nodes]]\nkind="composition"\nname="trainables/dialogue.toml"\n'
        '[inputs]\nnpc_state={type="str"}\nuser_message={type="str"}\n'
        '[outputs]\ndialogue_response={type="str"}\n',
        "composition", file_path="pipelines/inner.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="composition"\nname="pipelines/inner.toml"\n'
        '[[nodes]]\nkind="handler"\nname="acme.after"\n'
        '[inputs]\nnpc_state={type="str"}\nuser_message={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def test_streamable_terminal_fires_transitively_through_a_nested_embed():
    """RED-on-removal for the transitive clause: an embed whose transitive terminal streams is
    itself a streamable terminal, so a non-hook node following it violates placement. Pre-fix the
    embed branch ``continue``d before the streamable-terminal check ever ran, so this case
    compiled clean. (The direct same-pipeline case is covered by ``case_streamable_terminal``.)"""
    violations = _violations_from(case_streamable_terminal_transitive)
    assert any(v.check is Check.STREAMABLE_TERMINAL for v in violations), \
        f"expected STREAMABLE_TERMINAL transitively; got {[v.check.value for v in violations]}"


def case_streamable_terminal_through_a_terminal_bundle():
    """The streamable trainable rides a BUNDLE that is the inner pipeline's terminal node —
    canon's substitution rule makes this equivalent to declaring the trainable directly, so
    a non-hook node following the embed must still fire STREAMABLE_TERMINAL (pre-fix the
    embed walk treated a bundle terminal as non-streaming and this compiled clean)."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("acme.after", loads('[transform]\n[reads]\ndialogue_response={type="str"}\n[output_schema]\no={type="str"}', "handler", file_path="a.toml"))
    streamable = F.TRAINABLE_COMPOSITION.replace("[trainable]\n", "[trainable]\nstreamable = true\n")
    reg.add_composition("trainables/dialogue.toml", loads(streamable, "composition", file_path="c.toml"))
    reg.add_composition("bundles/tail.toml", loads(
        '[meta]\nkind="bundle"\nname="acme.tail"\n'
        '[[nodes]]\nkind="composition"\nname="trainables/dialogue.toml"\n',
        "composition", file_path="bundles/tail.toml"))
    reg.add_composition("pipelines/inner.toml", loads(
        '[meta]\nkind="pipeline"\nname="acme.inner"\n'
        '[[nodes]]\nkind="composition"\nname="bundles/tail.toml"\n'
        '[inputs]\nnpc_state={type="str"}\nuser_message={type="str"}\n'
        '[outputs]\ndialogue_response={type="str"}\n',
        "composition", file_path="pipelines/inner.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="composition"\nname="pipelines/inner.toml"\n'
        '[[nodes]]\nkind="handler"\nname="acme.after"\n'
        '[inputs]\nnpc_state={type="str"}\nuser_message={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def test_streamable_terminal_fires_through_a_terminal_bundle_inside_an_embed():
    violations = _violations_from(case_streamable_terminal_through_a_terminal_bundle)
    assert any(v.check is Check.STREAMABLE_TERMINAL for v in violations), \
        f"expected STREAMABLE_TERMINAL through the bundle; got {[v.check.value for v in violations]}"


def case_name_uniqueness():
    # Two composition nodes resolve to compositions sharing one meta.name → collides in the
    # manifest key + in <meta.name>.<channel> scoping (hash-model.md § Manifest-key shape).
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    # Same composition body registered under two distinct paths → same meta.name twice.
    reg.add_composition("trainables/a.toml", loads(F.TRAINABLE_COMPOSITION, "composition", file_path="a.toml"))
    reg.add_composition("trainables/b.toml", loads(F.TRAINABLE_COMPOSITION, "composition", file_path="b.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n'
        '[[nodes]]\nkind="composition"\nname="trainables/a.toml"\n'
        '[[nodes]]\nkind="composition"\nname="trainables/b.toml"\n'
        '[inputs]\nnpc_state={type="str"}\nuser_message={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_composition_cycle():
    # A nested `pipeline` composition that transitively embeds itself (A -> B -> A) — the
    # only non-terminating case under static nesting, rejected when the embed graph is
    # resolved at compose (pipeline/reference.md § The nested `pipeline` composition kind,
    # Termination). Transitive on purpose: the direct self-embed is the degenerate case.
    reg = DeclarationRegistry()
    reg.add_composition("pipelines/a.toml", loads(
        '[meta]\nkind="pipeline"\nname="acme.a"\n'
        '[[nodes]]\nkind="composition"\nname="pipelines/b.toml"\n[inputs]\ni={type="str"}\n',
        "composition", file_path="a.toml"))
    reg.add_composition("pipelines/b.toml", loads(
        '[meta]\nkind="pipeline"\nname="acme.b"\n'
        '[[nodes]]\nkind="composition"\nname="pipelines/a.toml"\n[inputs]\ni={type="str"}\n',
        "composition", file_path="b.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="composition"\nname="pipelines/a.toml"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def case_deployment_override_target():
    reg, pipeline, _ = F.build_base()
    # Override names binding `nonexistent` the pipeline does not declare.
    deployment = loads(
        '[transport.llm]\nendpoint="https://x"\n[hook_transport."acme.log"]\npath="/x"\n'
        '[training_contract]\nintegrity_enforcement=true\n'
        f'[pipelines."{F.NAME}".transport.nonexistent]\nendpoint="https://y"\n',
        "deployment", file_path="d.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")


# A service handler declaring exactly one binding (for the identity-placement case).
SERVICE_BINDS_LLM = '[service]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n[service_bindings]\nllm={type="conjured_llm.structured_output"}'


# ---------------------------------------------------------------------------
# The check → trigger table (one per binned 1a check)
# ---------------------------------------------------------------------------

CASES: dict[Check, callable] = {
    Check.HANDLER_KIND_HEADER: case_handler_kind_header,
    Check.CLOSED_GRAMMAR: case_closed_grammar,
    Check.SECTION_PRESENCE: case_section_presence,
    Check.BODY_REQUIRED: case_body_required,
    Check.CHANNEL_TYPE_TOKEN: case_channel_type_token,
    Check.NULLABLE_PLACEMENT: case_nullable_placement,
    Check.UNKNOWN_COMPOSITION_KIND: case_unknown_composition_kind,
    Check.MALFORMED_DECLARATION: case_malformed_declaration,
    Check.HANDLER_NAME_RESOLUTION: case_handler_name_resolution,
    Check.SERVICE_TYPE_RESOLUTION: case_service_type_resolution,
    Check.READ_WRITE_SHAPE: case_read_write_shape_mismatch,
    Check.WIRING_MAP_PORT: case_wiring_map_port,
    Check.DANGLING_IDENTITY_PORT: case_dangling_identity_port,
    Check.READ_PORT_UNCLOSED: case_read_port_unclosed,
    Check.SINGLE_ASSIGNMENT: case_single_assignment,
    Check.CHANNEL_WRITE_OVERLAP: case_channel_write_overlap,
    Check.MERGE_STRATEGY_TYPE: case_merge_strategy_type,
    Check.BINDING_SUPPLY: case_binding_supply_incomplete,
    Check.SERVICE_BINDING_CARDINALITY: case_service_binding_cardinality,
    Check.IDENTITY_TRANSPORT_PLACEMENT: case_identity_transport_placement,
    Check.TRANSPORT_COVERAGE: case_transport_coverage_gap,
    Check.EXPLICIT_NULL_TARGET: case_explicit_null_target,
    Check.TRANSPORT_HANDLE_COHERENCE: case_transport_handle_coherence,
    Check.SECRET_REF_MALFORMED: case_secret_ref_malformed,
    Check.SECRET_REF_SCHEME_UNKNOWN: case_secret_ref_scheme_unknown,
    Check.SECRET_RESOLVER_INVALID: case_secret_resolver_invalid,
    Check.HOOK_TRANSPORT_COVERAGE: case_hook_transport_coverage_gap,
    Check.INPUTS_OUTPUTS_DEAD: case_inputs_outputs_dead,
    Check.CONFIG_SCHEMA_SUPPLY: case_config_schema_supply,
    Check.STREAMABLE_TERMINAL: case_streamable_terminal,
    Check.DEPLOYMENT_OVERRIDE_TARGET: case_deployment_override_target,
    Check.NAME_UNIQUENESS: case_name_uniqueness,
    Check.COMPOSITION_CYCLE: case_composition_cycle,
}


# Checks owned by ANOTHER suite, not this Phase-1a declaration-fixture harness — each is
# covered by a firing negative test in its own home, so carved out of this coverage
# guarantee (named by string so the set is correct whether or not a member has landed):
# - EXTERNAL_BINDING_UNSUPPORTED — the Phase-1b hasher guard (tests/hasher/).
# - The Phase-2 resolution seals (HANDLER_MODULE_IMPORT / HANDLER_NAMESPACE_PACKAGE /
#   HANDLER_PURE_MODULE / HANDLER_FUNCTION_SHAPE / HANDLER_SIGNATURE /
#   ENTRY_POINT_COLLISION) — tests/validator/test_resolve_handler.py; the adapter
#   siblings (ADAPTER_PURE_MODULE / ADAPTER_SIGNATURE / ADAPTER_CONSTRUCTION — the
#   stage-4 construction wrap) — test_resolve_adapter.py; the
#   validator third-sibling seals (VALIDATOR_SIGNATURE / VALIDATOR_PARAMS) —
#   test_resolve_validator.py.
# - The Phase-2 dispatch-boundary checks (UNDECLARED_OUTPUT_KEY / MISSING_DECLARED_WRITE /
#   RETURN_SHAPE / HOOK_RETURN_NOT_NONE) — tests/runner/test_dispatch.py. The two
#   HALT_ON_*_VALIDATION members are the SVE boundaries' symbolic stand-ins; the
#   boundaries raise SchemaValidationError (not ContractViolation) with the decided
#   audit codes, asserted in tests/runner/test_dispatch.py.
# - The trainable-backend compose gate (TRAINABLE_BACKEND_CERTIFICATION — now the surviving
#   property-contract check: training_artifact_contract + reserved_wire_keys) —
#   test_resolve_adapter.py; the compose-time constraint caveat
#   (TRAINABLE_CONSTRAINT_UNSUPPORTED) — tests/adapters/test_wire.py + the two
#   native-adapter suites under tests/lib/.
# - The audit-stamp freshness seals (AUDIT_STAMP_NOT_FRESH / AUDIT_STAMP_MALFORMED) — the
#   resolution-time sibling-stamp check under audit_enforcement (validator.audit_stamp);
#   tests/validator/test_audit_stamp.py (the mechanism + all three resolution loci).
# - The engine-owned-identity guard (ENGINE_OWNED_IDENTITY) — test_resolve_adapter.py: the
#   native class-path-binding reject (resolve_adapter) + the conjured.lib.* redefinition
#   reject (DeclarationRegistry.add_service_type); both raise ContractViolation outside this
#   Phase-1a declaration-fixture harness.
# - The streaming-delivery contract halves (STREAMABLE_BACKEND_SUPPORT — the stage-4
#   capability gate beside check_trainable_backend; STREAMABLE_SINK_TARGET — the run-boundary
#   sink-route check) — tests/runner/test_streaming.py (both arms each).
# - The audited-vs-executed origin guard (MODULE_ORIGIN_DIVERGENCE — the fresh-resolution
#   eviction's different-origin reject) — tests/validator/test_resolve_handler.py
#   (test_different_origin_module_is_rejected_loud).
# - The Phase-3 runner checks: the API-boundary presence check
#   (API_INPUTS_ENFORCEMENT) — tests/runner/test_run.py; the stage-4 binding-value
#   validation (BINDING_VALUE_SHAPE) — tests/runner/test_assemble.py;
#   PIPELINE_FAILURE_WRAP is the PipelineFailure class's single registered wrap audit
#   (raises PipelineFailure, not ContractViolation) —
#   tests/runner/test_pipeline_failure.py + test_run.py.
# - The compile-affordance binding-resolution seals (COMPILE_SIGNATURE / COMPILE_ARTIFACT) —
#   tests/validator/test_resolve_compile.py (the dotted-resolution import/purity/shape seals
#   reuse the HANDLER_* members above).
_NON_VALIDATOR_CHECKS = {
    c
    for c in Check
    if c.name
    in {
        # The hasher's own-hash-domain backstop — its sole raise site since the bundle
        # embed-form landed; fired by tests/hasher/test_hashes.py (tbh-fold-own-hash-domain-only).
        "BUNDLE_REACHES_BYREF_FOLD",
        # The R-pipeline-003 trained-artifact integrity surface (conjured.manifest) —
        # each fired by tests/test_manifest.py (the graduated-force + malformed +
        # dead-registration cases).
        "TRAINED_ARTIFACT_MANIFEST_MISSING",
        "TRAINED_ARTIFACT_MANIFEST_MALFORMED",
        "TRAINING_BUNDLE_HASH_MISMATCH",
        "ARTIFACT_TRAINABLE_UNKNOWN",
        "API_INPUTS_ENFORCEMENT",
        "PIPELINE_FAILURE_WRAP",
        "COMPILE_SIGNATURE",
        "COMPILE_ARTIFACT",
        "BINDING_VALUE_SHAPE",
        "EXTERNAL_BINDING_UNSUPPORTED",
        "HANDLER_MODULE_IMPORT",
        "HANDLER_NAMESPACE_PACKAGE",
        "HANDLER_PURE_MODULE",
        "HANDLER_FUNCTION_SHAPE",
        "HANDLER_SIGNATURE",
        "ENTRY_POINT_COLLISION",
        "ADAPTER_PURE_MODULE",
        "ADAPTER_SIGNATURE",
        "ADAPTER_CONSTRUCTION",
        "AUDIT_STAMP_NOT_FRESH",
        "AUDIT_STAMP_MALFORMED",
        "ENGINE_OWNED_IDENTITY",
        "TRAINABLE_BACKEND_CERTIFICATION",
        "TRAINABLE_CONSTRAINT_UNSUPPORTED",
        "STREAMABLE_BACKEND_SUPPORT",
        "STREAMABLE_SINK_TARGET",
        "MODULE_ORIGIN_DIVERGENCE",
        "VALIDATOR_SIGNATURE",
        "VALIDATOR_PARAMS",
        "UNDECLARED_OUTPUT_KEY",
        "MISSING_DECLARED_WRITE",
        "RETURN_SHAPE",
        "HOOK_RETURN_NOT_NONE",
        "HALT_ON_INPUT_VALIDATION_ERROR",
        "HALT_ON_SCHEMA_VALIDATION_ERROR",
    }
}


def _violations_from(case) -> list[ContractViolation]:
    """Run a negative case and return the ContractViolation(s) it raised. Under the
    within-group aggregation, a minimal fixture may trip its own target check **plus**
    other independently-detectable same-group faults — those surface as a
    ContractViolationGroup, not a bare ContractViolation (error-channel/reference.md
    § ContractViolationGroup). Either shape is unwrapped to the flat list here so the
    coverage assertion is "the target check fired on its example," not "it fired alone"
    (which would pin the incidental fail-fast ordering)."""
    try:
        case()
    except ContractViolationGroup as group:
        return list(group.violations)
    except ContractViolation as cv:
        return [cv]
    raise AssertionError("case compiled without raising — expected a ContractViolation")


@pytest.mark.parametrize("check", list(CASES), ids=lambda c: c.value)
def test_check_fires_on_its_violation_example(check):
    violations = _violations_from(CASES[check])
    matching = [v for v in violations if v.check is check]
    assert matching, (
        f"expected {check.value} to fire; got {[v.check.value for v in violations]}"
    )
    # The structured error must also cite a rule (every engine rule_id is `R-…`). A raise site
    # that drops or malforms the rule_id is a regression the check-enum assertion alone misses
    # (the message form itself is the canonical default template — this assertion stays
    # presence-level, never form-pinning).
    for v in matching:
        assert v.rule_id and v.rule_id.startswith("R-"), \
            f"{check.value} fired with a missing/malformed rule_id: {v.rule_id!r}"


def test_every_check_has_a_negative():
    """Coverage guarantee: every Check member is exercised by a firing negative test, so a
    new 1a check cannot land without one."""
    missing = set(Check) - set(CASES) - _NON_VALIDATOR_CHECKS
    assert not missing, f"checks with no negative test: {sorted(c.value for c in missing)}"


def test_pipeline_requires_meta_name():
    """The Phase-1b floor amendment makes pipelines self-name (the family rule): a pipeline
    declaration missing its [meta] block, or [meta] without a 'name', raises
    ContractViolation at parse (hash-model.md § The family rule; pipeline/reference.md § meta)."""
    with pytest.raises(ContractViolation) as no_block:
        loads('[[nodes]]\nkind="handler"\nname="acme.x"\n', "pipeline", file_path="p.toml")
    assert no_block.value.check is Check.MALFORMED_DECLARATION
    assert no_block.value.section_path == "meta"

    with pytest.raises(ContractViolation) as no_name:
        # An empty [meta] — missing `name` (the block is closed to {name}, so there is no
        # other admitted filler key; the name-required check owns this raise).
        loads('[meta]\n[[nodes]]\nkind="handler"\nname="acme.x"\n', "pipeline", file_path="p.toml")
    assert no_name.value.check is Check.MALFORMED_DECLARATION
    assert no_name.value.section_path == "meta.name"


# ---------------------------------------------------------------------------
# Per-declaration-class body-required arms + the hook cardinality arm — each error
# path asserts the exact owning rule_id (gap closed by the 2026-06-10
# review-on-return verification pass; canon: exhaustive-declaration § the
# section-discipline modes / R-service-type-001; pipeline/reference § inputs/outputs;
# R-handler-009).
# ---------------------------------------------------------------------------


def test_service_type_schema_body_required_cites_r_service_type_001():
    """An empty [identity_schema] / [transport_schema] on a service-type is the
    R-service-type-001 body-required arm — NOT the handler grammar's R-handler-006
    (the mislabel this test pins against)."""
    with pytest.raises(ContractViolation) as identity:
        loads('name="s"\n[identity_schema]\n[transport_schema]\ne={type="str"}\n[config_schema]',
              "service_type", file_path="x.toml")
    assert identity.value.check is Check.BODY_REQUIRED
    assert identity.value.rule_id == "R-service-type-001"
    assert identity.value.section_path == "identity_schema"

    with pytest.raises(ContractViolation) as transport:
        loads('name="s"\n[identity_schema]\nm={type="str"}\n[transport_schema]\n[config_schema]',
              "service_type", file_path="x.toml")
    assert transport.value.check is Check.BODY_REQUIRED
    assert transport.value.rule_id == "R-service-type-001"
    assert transport.value.section_path == "transport_schema"


def test_pipeline_outputs_present_but_empty_cites_r_pipeline_001():
    """[outputs] is truly optional on a pipeline, but present-but-empty is the
    body-required violation — owned by the pipeline declaration's R-pipeline-001,
    not the handler grammar."""
    with pytest.raises(ContractViolation) as exc:
        loads('[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.x"\n[outputs]\n',
              "pipeline", file_path="p.toml")
    assert exc.value.check is Check.BODY_REQUIRED
    assert exc.value.rule_id == "R-pipeline-001"
    assert exc.value.section_path == "outputs"


def test_hook_binding_cardinality_cites_r_handler_009():
    """A hook declaring two service-typed bindings fires the hook arm of the
    cardinality gate with R-handler-009 (the service arm's R-handler-008 case is
    in the CASES table; the hook arm was untested)."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.h", loads(
        '[hook]\n[reads]\ni={type="str"}\n[service_bindings]\na={type="x.poster"}\nb={type="y.poster"}\n[transport_schema]',
        "handler", file_path="h.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.h"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.SERVICE_BINDING_CARDINALITY
    assert exc.value.rule_id == "R-handler-009"


# ---------------------------------------------------------------------------
# The contributor model (R-pipeline-002, the ruled replacement): the seed counts
# as a contributor — a node writing a seeded [inputs] channel is a fan-in.
# ---------------------------------------------------------------------------


def _seeded_write_pipeline(merge_line: str = ""):
    reg = DeclarationRegistry()
    reg.add_handler("acme.r", loads(
        '[transform]\n[reads]\nstate={type="str"}\n[output_schema]\nseen={type="str"}',
        "handler", file_path="r.toml"))
    reg.add_handler("acme.w", loads(
        '[transform]\n[reads]\nseen={type="str"}\n[output_schema]\nout={type="str"}',
        "handler", file_path="w.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n'
        '[[nodes]]\nkind="handler"\nname="acme.r"\n'  # reads the seeded channel
        '[[nodes]]\nkind="handler"\nname="acme.w"\nwrites_map={out="state"}\n'  # writes it
        f'{merge_line}'
        '[inputs]\nstate={type="str"}\n',
        "pipeline", file_path="p.toml")
    return reg, pipeline


def test_seeded_channel_plus_node_write_without_a_strategy_fails_compose():
    """A channel's contributors are its seed (a declared [inputs] channel) plus its
    node writes — ONE node write to a seeded channel is already two contributors, so
    a missing merge.<channel> raises the SAME undeclared-fan-in CV every collision
    gets (the contributor model replaced the proposed reject-check)."""
    reg, pipeline = _seeded_write_pipeline()
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    cv = exc.value
    assert cv.check is Check.CHANNEL_WRITE_OVERLAP
    assert cv.rule_id == "R-pipeline-002"
    assert "2 contributors" in cv.expected
    assert "seed" in cv.actual  # the diagnostic names the seed contributor


def test_seeded_channel_plus_node_write_with_a_strategy_composes():
    """With a declared merge.<channel> strategy the seeded fan-in is a legal
    multi-contributor channel — compose passes."""
    reg, pipeline = _seeded_write_pipeline('[merge]\nstate="last_present_wins"\n')
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


# ---------------------------------------------------------------------------
# Boundary fields admit no constraint keywords and no validators (the P13 rule:
# boundary validation is presence-only — a value constraint there would have no
# enforcement point; fail loud, never a silent no-op)
# ---------------------------------------------------------------------------


def test_pipeline_input_constraint_keyword_rejects_at_load():
    with pytest.raises(ContractViolation) as exc:
        loads('[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.x"\n'
              '[inputs]\ni={type="str", minLength=2}\n', "pipeline", file_path="p.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-pipeline-001"
    assert "minLength" in exc.value.actual


def test_pipeline_output_namespaced_validator_rejects_at_load():
    """A namespaced (dotted) third-party validator key on a boundary field hits the
    boundary forbid (D8 — boundary fields admit no validation keywords of either class)."""
    with pytest.raises(ContractViolation) as exc:
        loads('[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.x"\n'
              '[outputs]\no={type="str", "mypkg.v"={}}\n', "pipeline", file_path="p.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-pipeline-001"
    assert "mypkg.v" in exc.value.actual


def test_composition_boundary_constraint_keyword_rejects_at_load():
    with pytest.raises(ContractViolation) as exc:
        loads('[meta]\nkind="trainable"\nname="c"\n'
              '[inputs]\nx={type="int", minimum=1}\n[outputs]\ny={type="str"}\n'
              '[trainable]\n[trainable.config]\n[trainable.service_bindings]\nllm={type="t.x"}\n'
              '[trainable.reads]\nx={type="int"}\n[trainable.output_schema]\ny={type="str"}\n',
              "composition", file_path="c.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-006"


# ---------------------------------------------------------------------------
# Composition boundary mirrors the pipeline boundary (COMPILE-1/2/3) — a trainable
# composition's [inputs]/[outputs] participate in type-matching after flatten
# (R-pipeline-001 read/write shape-matching) AND get the dead-declaration check
# (R-pipeline-001 inputs/outputs resolution), through the SAME shared mechanism the
# top-level pipeline uses (the mirror-pipeline principle, hash-model.md). Each case is
# RED-on-removal of the shared boundary registration/check: with it reverted the
# perturbed fixture compiles clean (the boundary name-set is inert in the unmirrored
# path), so the test would no longer raise. The pre-fix suite stayed GREEN with the
# defect present (test_positive uses a boundary-consistent fixture).
# ---------------------------------------------------------------------------


def _trainable_pipeline_with(comp_toml: str):
    """``build_trainable``'s (reg, pipeline) with the composition re-registered from a
    perturbed body — the one knob each boundary-mirror case turns."""
    reg, pipeline = F.build_trainable()
    reg.add_composition("trainables/dialogue.toml", loads(comp_toml, "composition", file_path="c.toml"))
    return reg, pipeline


def test_composition_output_type_lie_fires_read_write_shape():
    """A composition [outputs] type mismatching the terminal trainable's output-port type is
    caught: the boundary type participates in channel type-agreement after flatten (COMPILE-1)."""
    # [outputs] dialogue_response declares int while the terminal trainable writes it as str.
    bad = F.TRAINABLE_COMPOSITION.replace(
        '[outputs]\ndialogue_response = { type = "str" }',
        '[outputs]\ndialogue_response = { type = "int" }', 1)
    reg, pipeline = _trainable_pipeline_with(bad)
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.READ_WRITE_SHAPE
    assert exc.value.rule_id == "R-pipeline-001"
    assert "composition 'dialogue_training'" in exc.value.actual  # the lying boundary is named


def test_composition_input_type_lie_fires_read_write_shape():
    """A composition [inputs] type mismatching its outer producer / internal reader is caught
    (COMPILE-1): the boundary input type participates in type-agreement after flatten."""
    # [inputs] npc_state declares int while acme.ctx writes it (str) and the preprocessor reads it (str).
    bad = F.TRAINABLE_COMPOSITION.replace(
        'npc_state = { type = "str" }', 'npc_state = { type = "int" }', 1)
    reg, pipeline = _trainable_pipeline_with(bad)
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.READ_WRITE_SHAPE
    assert exc.value.rule_id == "R-pipeline-001"
    assert "composition 'dialogue_training'" in exc.value.actual


def test_composition_dead_boundary_input_fires_inputs_outputs_dead():
    """A composition [inputs] field no internal node reads is a dead declaration (COMPILE-2),
    mirroring the pipeline's check — scoped to the composition's own nodes (an outer reader of the
    unscoped boundary channel must not mask it)."""
    bad = F.TRAINABLE_COMPOSITION.replace(
        '[inputs]\nnpc_state = { type = "str" }',
        '[inputs]\nunread_in = { type = "str" }\nnpc_state = { type = "str" }', 1)
    reg, pipeline = _trainable_pipeline_with(bad)
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.INPUTS_OUTPUTS_DEAD
    assert exc.value.rule_id == "R-pipeline-001"
    assert exc.value.section_path == "inputs.unread_in"


def test_composition_dead_boundary_output_fires_inputs_outputs_dead():
    """A composition [outputs] field no internal node writes is a dead declaration (COMPILE-2),
    mirroring the pipeline's check, scoped to the composition's own nodes."""
    bad = F.TRAINABLE_COMPOSITION.replace(
        '[outputs]\ndialogue_response = { type = "str" }',
        '[outputs]\ndialogue_response = { type = "str" }\nunwritten_out = { type = "str" }', 1)
    reg, pipeline = _trainable_pipeline_with(bad)
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.INPUTS_OUTPUTS_DEAD
    assert exc.value.rule_id == "R-pipeline-001"
    assert exc.value.section_path == "outputs.unwritten_out"


# ---------------------------------------------------------------------------
# The [[preprocessors]] entry re-key (kind + name + id — trainable.schema.toml)
# ---------------------------------------------------------------------------


def _comp_with_preproc_head(head: str) -> str:
    # Name-reference entry: only the head (kind/name/id + any token under test). No inline
    # [preprocessors.reads]/[output_schema] — those are owned by the referenced handler, so
    # injecting them here would add incidental unknown keys that muddy the closed-grammar `actual`.
    return (
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        f'[[preprocessors]]\n{head}'
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="qwen"\n'
        '[trainable]\n[trainable.config]\n[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nout={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )


def test_preprocessor_entry_missing_id_rejects():
    with pytest.raises(ContractViolation) as exc:
        loads(_comp_with_preproc_head('kind="handler"\nname="transform.a"\n'),
              "composition", file_path="c.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert "'id'" in exc.value.expected


def test_preprocessor_entry_old_type_key_rejects_closed_grammar():
    """The pre-re-key spelling (`name` as the local label + `type` as the callable)
    no longer parses: `type` is an unknown key under the re-keyed entry grammar."""
    with pytest.raises(ContractViolation) as exc:
        loads(_comp_with_preproc_head('kind="handler"\nname="p"\nid="p"\ntype="transform.a"\n'),
              "composition", file_path="c.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "type" in exc.value.actual


def test_preprocessor_composition_embed_is_ruled_out_by_design():
    """`kind = "composition"` on a preprocessor entry is rejected BY DESIGN (user-ruled
    2026-07-09): the trainable's [[preprocessors]] is the one id-labeled node sequence
    (each id is a load-bearing address — hook-transport key, member name) and a
    substituted node is anonymous; the trainable is a deliberate composition boundary.
    Rejected loud, the hint routing the author to the pipeline-family nodes layers."""
    with pytest.raises(ContractViolation) as exc:
        loads(_comp_with_preproc_head('kind="composition"\nname="x.toml"\nid="x"\n'),
              "composition", file_path="c.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "by design" in exc.value.remediation_hint
    assert "pipeline-family" in exc.value.remediation_hint


def test_preprocessor_transport_schema_is_not_an_entry_key():
    """A [[preprocessors]] entry is a name-reference: its ports + a hook's transport_schema are
    owned by the REFERENCED handler declaration, never inlined on the entry. So `transport_schema`
    (like `reads` / `output_schema` / `service_bindings`) is an unknown entry key, rejected by the
    closed-grammar guard — the structural guard that makes a divergent inline declaration
    unrepresentable."""
    with pytest.raises(ContractViolation) as exc:
        loads(_comp_with_preproc_head(
            'kind="handler"\nname="p"\nid="p"\ntransport_schema={f={type="str"}}\n'
        ), "composition", file_path="c.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-006"
    assert "transport_schema" in exc.value.actual


# ---------------------------------------------------------------------------
# D5 — service-type / hook schemas admit no validation keywords (no enforcement point)
# ---------------------------------------------------------------------------

_ST = 'name="s"\n[identity_schema]\n{id}\n[transport_schema]\n{tr}\n[config_schema]\n{cf}'


def test_service_type_identity_schema_constraint_rejects():
    with pytest.raises(ContractViolation) as exc:
        loads(_ST.format(id='m={type="int", minimum=1}', tr='e={type="str"}', cf=""),
              "service_type", file_path="x.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-service-type-001"


def test_service_type_config_schema_constraint_rejects():
    with pytest.raises(ContractViolation) as exc:
        loads(_ST.format(id='m={type="str"}', tr='e={type="str"}', cf='t={type="float", minimum=0.0}'),
              "service_type", file_path="x.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-service-type-001"


def test_service_type_transport_schema_namespaced_validator_rejects():
    with pytest.raises(ContractViolation) as exc:
        loads(_ST.format(id='m={type="str"}', tr='e={type="str", "mypkg.v"={}}', cf=""),
              "service_type", file_path="x.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-service-type-001"


def test_hook_transport_schema_constraint_rejects():
    with pytest.raises(ContractViolation) as exc:
        loads('[hook]\n[reads]\nx={type="str"}\n[service_bindings]\n[transport_schema]\np={type="str", minLength=1}',
              "handler", file_path="x.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-006"


def test_hook_transport_field_named_services_collides():
    """A hook transport field named `services` collides with the reserved ServicesProxy
    kwarg (the mechanical-set fix) — NAME_UNIQUENESS at declaration load."""
    with pytest.raises(ContractViolation) as exc:
        loads('[hook]\n[reads]\nx={type="str"}\n[service_bindings]\n[transport_schema]\nservices={type="str"}',
              "handler", file_path="x.toml")
    assert exc.value.check is Check.NAME_UNIQUENESS
    assert exc.value.rule_id == "R-handler-006"
    assert "services" in exc.value.actual


def test_hook_transport_block_with_an_unknown_field_rejects():
    """The no-unknown-fields direction at deployment coverage (the mechanical-set fix;
    R-pipeline-001 hook-transport coverage — "no unknown fields are accepted", mirroring the
    binding-transport arm): a hook_transport block carrying a field the hook's
    transport_schema does not declare is a loud HOOK_TRANSPORT_COVERAGE CV."""
    reg, pipeline, _ = F.build_base()
    deployment = loads(
        '[transport.llm]\nendpoint="https://x"\n'
        '[hook_transport."acme.log"]\npath="/x"\nunknown="y"\n'  # `unknown` not declared
        '[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")
    assert exc.value.check is Check.HOOK_TRANSPORT_COVERAGE
    assert exc.value.rule_id == "R-pipeline-001"
    assert "unknown" in exc.value.actual


def test_hook_transport_value_type_mismatch_rejects():
    """The value-vs-type arm of hook-transport coverage (surprise-fixes 3-code; R-pipeline-001/
    hook-transport-coverage — "declared types must match"). A deployment supplying a value that
    violates the hook's declared transport_schema TYPE — the ``format = "jsonn"`` typo against a
    ``Literal['plain','json']`` field — is a compose-time HOOK_TRANSPORT_COVERAGE CV, not a value
    that composes green and silently changes behaviour at every dispatch (blob_reference_emitter's
    ``format`` is exactly such a Literal field). RED if the type-match loop is removed (then only
    presence + no-unknown-fields are checked and "jsonn" composes clean)."""
    reg = DeclarationRegistry()
    reg.add_handler(
        "acme.producer",
        loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\nstate={type="str"}',
              "handler", file_path="prod.toml"))
    reg.add_handler(
        "auditlog",
        loads('[hook]\n[reads]\nstate={type="str"}\n[service_bindings]\n'
              '[transport_schema]\nformat={type="Literal[\'plain\', \'json\']"}',
              "handler", file_path="hook.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n'
        '[[nodes]]\nkind="handler"\nname="acme.producer"\nwrites_map={state="state"}\n'
        '[[nodes]]\nkind="handler"\nname="auditlog"\n'
        '[inputs]\ni={type="str"}\n',
        "pipeline", file_path="pl.toml")
    bad = loads(
        '[hook_transport."auditlog"]\nformat="jsonn"\n'  # not a Literal member — a deployment typo
        '[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name="acme.p", deployment=bad, file_path="pl.toml")
    assert exc.value.check is Check.HOOK_TRANSPORT_COVERAGE
    assert exc.value.rule_id == "R-pipeline-001"
    assert "jsonn" in exc.value.actual
    assert exc.value.section_path == 'hook_transport."auditlog".format'

    # Positive control: a valid Literal member composes clean (the arm rejects only mismatches).
    ok = loads(
        '[hook_transport."auditlog"]\nformat="json"\n'
        '[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    compile_pipeline(pipeline, reg, pipeline_name="acme.p", deployment=ok, file_path="pl.toml")  # no raise


def test_short_named_hook_coverage_keys_on_the_as_written_name():
    """D7: a hook named by a dot-less entry-points short name is hook-transport-coverage-keyed
    by its AS-WRITTEN node name (not a resolved dotted name) — matching the corpus-wide
    join-on-the-as-written-label pattern. A hook_transport block keyed by the short name
    covers it (compose passes); were the engine to resolve the short name first, the block
    would not match."""
    reg = DeclarationRegistry()
    reg.add_handler(
        "acme.producer",
        loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\nstate={type="str"}',
              "handler", file_path="prod.toml"))
    # The hook is registered under a DOT-LESS short name.
    reg.add_handler(
        "auditlog",
        loads('[hook]\n[reads]\nstate={type="str"}\n[service_bindings]\n[transport_schema]\npath={type="str"}',
              "handler", file_path="hook.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n'
        '[[nodes]]\nkind="handler"\nname="acme.producer"\nwrites_map={state="state"}\n'
        '[[nodes]]\nkind="handler"\nname="auditlog"\n'
        '[inputs]\ni={type="str"}\n',
        "pipeline", file_path="pl.toml")
    deployment = loads(
        '[hook_transport."auditlog"]\npath="/x"\n[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    graph = compile_pipeline(
        pipeline, reg, pipeline_name="acme.p", deployment=deployment, file_path="pl.toml",
    )  # no raise — the as-written short name keys the coverage block
    hook_node = next(n for n in graph.nodes if n.node_kind == "hook")
    assert hook_node.qualified_name == "auditlog"  # the as-written label, not a resolved form


# ---------------------------------------------------------------------------
# Per-entry merge validation (mechanical set) — checks fire regardless of the
# contributor count; an unwired merge is inert; a single-contributor merge folds
# degenerate (NOT inert) and is only rejected on a type mismatch.
# ---------------------------------------------------------------------------


def test_merge_on_unwired_channel_rejects_as_inert():
    """A merge naming a channel no port wires is inert — CHANNEL_WRITE_OVERLAP (the
    named-channel-exists arm; previously escaped because the channel had < 2 contributors)."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.a", loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\nstate={type="str"}', "handler", file_path="a.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.a"\nwrites_map={state="state"}\n'
        '[merge]\nghost="last_wins"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.CHANNEL_WRITE_OVERLAP
    assert exc.value.rule_id == "R-pipeline-002"
    assert "ghost" in exc.value.actual


def test_single_contributor_merge_type_mismatch_rejects():
    """The strategy type-constraint now fires on a single-contributor merge entry too
    (previously gated behind the >= 2 count) — append_list on a str channel → MERGE_STRATEGY_TYPE."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.a", loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\nstate={type="str"}', "handler", file_path="a.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.a"\nwrites_map={state="npc_state"}\n'
        '[merge]\nnpc_state="append_list"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert exc.value.check is Check.MERGE_STRATEGY_TYPE
    assert exc.value.rule_id == "R-pipeline-002"


def test_single_contributor_merge_matching_type_compiles():
    """A single-contributor wired channel with a TYPE-MATCHING strategy folds degenerate and
    is NOT rejected as inert (the over-rejection lock) — append_list on a list channel compiles."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.a", loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\nitems={type="list[str]"}', "handler", file_path="a.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.a"\nwrites_map={items="merged"}\n'
        '[merge]\nmerged="append_list"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")  # no raise


# ---------------------------------------------------------------------------
# Self-steering violation messages — the GATE channel of the agent-steering work
# (Steer agents empirically: where a break is catchable at the gate, the violation
# MESSAGE is the steering lever). Both checks already FIRE on their example (covered by
# the meta-harness above); these assert the rendered message NAMES the intended mechanism,
# so a fresh agent that lands on the right rule but reaches for a trained default
# self-corrects from the diagnostic. Each is RED-on-removal of the steering text: the
# asserted phrases are absent from the pre-fix generic wording, so reverting the message
# turns the test red while the check still fires.
# ---------------------------------------------------------------------------


def test_composition_node_maps_message_names_flatten_by_name_and_rename():
    """G1: ``reads_map``/``writes_map`` on a ``kind="composition"`` embed (the empirical
    "wire I/O explicitly" default applied to a composition embed, which the engine wires by
    flatten-by-name, not per-node maps — pipeline/reference.md § nodes). The CLOSED_GRAMMAR
    message must name the flatten-BY-NAME mechanism and the rename remedy, not just say the
    keys are forbidden."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[meta]\nname="acme.p"\n'
            '[[nodes]]\nkind="composition"\nname="trainables/dialogue.toml"\n'
            'reads_map={ctx="npc_state"}\n'  # handler-node wiring on a composition embed
            '[inputs]\nraw={type="str"}\n',
            "pipeline", file_path="p.toml")
    cv = exc.value
    assert cv.check is Check.CLOSED_GRAMMAR
    assert cv.rule_id == "R-pipeline-001"
    assert "reads_map" in cv.actual  # the forbidden key is named
    msg = str(cv).lower()  # the rendered structured payload, not a bare trace
    # RED-on-removal: the steering names the flatten-BY-NAME mechanism + the rename remedy.
    assert "flatten" in msg
    assert "by name" in msg
    assert "rename" in msg


def test_backend_sdk_hook_missing_block_message_names_the_backend_sdk_case():
    """G2: a backend-SDK-emission hook (a service-bound hook whose own ``transport_schema``
    declares zero fields, so its emission rides the binding's ``transport.<name>`` to the
    adapter) STILL needs an empty-but-present ``hook_transport."<qn>"`` block. The empirical
    break: an author omits it ("its transport rides the binding — nothing to declare") →
    HOOK_TRANSPORT_COVERAGE. The message must name the backend-SDK special case so the
    omission self-corrects (deployment/reference.md § hook_transport; pipeline/reference.md
    R-pipeline-001/hook-transport-coverage)."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_LLM, "service_type", file_path="st.toml"))
    reg.add_handler(
        "acme.producer",
        loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\ndialogue={type="str"}',
              "handler", file_path="prod.toml"))
    # A backend-SDK-emission hook: one service binding + a ZERO-field transport_schema (the
    # hook body needs no per-deployment transport; the emission rides the binding's adapter).
    reg.add_handler(
        "acme.emit",
        loads('[hook]\n[reads]\ndialogue={type="str"}\n'
              '[service_bindings]\nsink={type="conjured_llm.structured_output"}\n'
              '[transport_schema]\n',
              "handler", file_path="emit.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n'
        '[[nodes]]\nkind="handler"\nname="acme.producer"\nwrites_map={dialogue="dialogue"}\n'
        '[[nodes]]\nkind="handler"\nname="acme.emit"\n'
        '[service_bindings.sink]\ntype="conjured_llm.structured_output"\nmodel="qwen"\n'
        '[service_bindings.sink.config]\ntemperature=0.7\n'
        '[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    # Deployment supplies the binding's transport.sink (the backend transport) but OMITS the
    # hook_transport."acme.emit" block — the exact backend-SDK omission.
    deployment = loads(
        '[transport.sink]\nendpoint="https://x"\n'
        '[training_contract]\nintegrity_enforcement=true',
        "deployment", file_path="d.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name="acme.p", deployment=deployment, file_path="p.toml")
    cv = exc.value
    assert cv.check is Check.HOOK_TRANSPORT_COVERAGE
    assert cv.rule_id == "R-pipeline-001"
    msg = str(cv).lower()  # the rendered structured payload, not a bare trace
    # RED-on-removal: the steering names the backend-SDK special case + coverage-not-content.
    assert "backend-sdk" in msg
    assert "empty-but-present" in msg
    assert "declares coverage" in msg


# ---------------------------------------------------------------------------
# The declaration-kind roster front door (enforcement-coverage E11) — the one
# error path of the exported loads()/parse() dispatch: an unknown kind must
# raise the designed loud ValueError naming the closed roster, never a bare
# KeyError from a dispatch-table reshuffle.
# ---------------------------------------------------------------------------


def test_loads_rejects_an_unknown_kind_naming_the_roster():
    with pytest.raises(ValueError) as exc:
        loads("[transform]\n", "pipelines", file_path="x.toml")
    msg = str(exc.value)
    for kind in ("handler", "service_type", "pipeline", "composition", "deployment"):
        assert kind in msg, f"roster kind {kind!r} missing from the rejection: {msg}"


def test_parse_rejects_an_unknown_kind_naming_the_roster():
    from conjured.validator import parse

    with pytest.raises(ValueError) as exc:
        parse({}, "declaration", file_path="x.toml")
    msg = str(exc.value)
    for kind in ("handler", "service_type", "pipeline", "composition", "deployment"):
        assert kind in msg, f"roster kind {kind!r} missing from the rejection: {msg}"
