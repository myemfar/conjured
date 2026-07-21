"""Token-level streaming delivery — the ``streamable`` field's runtime realization,
tested at the run boundary and the trainable dispatch seam (the streaming-transport
arc). The contract under test (pipeline/reference.md § Orchestration scope): fragments
delivered through ``run(..., stream_sink=...)`` are provisional transport — the channel
still receives only the complete validated value, the captured record is that same
value, and no new canonical event type exists for deltas. The stub backend sits AT the
adapter seam (its ``invoke_streaming`` is a real generator), so every path exercised
here fails exactly where a live backend's would.
"""

from __future__ import annotations

import importlib
import logging
import textwrap

import pytest

from conjured.errors import (
    OUTPUT_VALIDATION_AUDIT_CODE,
    Check,
    ContractViolation,
    SchemaValidationError,
)
from conjured.runner import assemble, run
from conjured.validator import DeclarationRegistry, loads
from conjured.validator.compile import compile_pipeline


@pytest.fixture
def module_dir(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, name: str, source: str) -> None:
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    importlib.invalidate_caches()


def _capture_events():
    """Attach a consumer handler to the canonical event channel (the engine ships
    none — producer/consumer); returns ``(captured_list, detach)``."""
    from conjured import events as E

    captured: list = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.msg)

    handler = _Capture()
    lg = E.event_logger()
    lg.addHandler(handler)
    prev_level = lg.level
    lg.setLevel(logging.INFO)

    def detach():
        lg.removeHandler(handler)
        lg.setLevel(prev_level)

    return captured, detach


# ---------------------------------------------------------------------------
# Declarations — the proven trainable-composition shape, with `streamable = true`
# ---------------------------------------------------------------------------

SERVICE_TYPE = """
name = "stream_backend_mod.StubStreamingBackend"
[identity_schema]
model = { type = "str" }
[transport_schema]
endpoint = { type = "str" }
[config_schema]
temperature = { type = "float" }
max_tokens = { type = "int" }
"""

PREP_TRANSFORM = """
[transform]
[reads]
raw = { type = "str" }
[output_schema]
npc_state = { type = "str" }
user_message = { type = "str" }
"""

PREPROC_FORMATTER = """
[transform]
[reads]
context = { type = "str" }
utterance = { type = "str" }
[output_schema]
prompt = { type = "str" }
[bindings.config]
template = { type = "str" }
"""

_COMPOSITION_TEMPLATE = """
[meta]
kind = "trainable"
name = "dialogue_training"
[inputs]
npc_state = {{ type = "str" }}
user_message = {{ type = "str" }}
[outputs]
dialogue_response = {{ type = "str" }}
[[preprocessors]]
kind = "handler"
name = "stream_pp_mod.assemble_prompt"
id   = "assemble_prompt"
reads_map = {{ context = "npc_state", utterance = "user_message" }}
writes_map = {{ prompt = "formatted_prompt" }}
[preprocessors.bindings]
config = {{ template = "T" }}
[service_bindings.llm]
type = "{backend}"
model = "test-model"
[trainable]
streamable = {streamable}
[trainable.config]
temperature = 0.7
max_tokens = 64
[trainable.service_bindings]
llm = {{ type = "{backend}" }}
[trainable.reads]
formatted_prompt = {{ type = "str" }}
[trainable.output_schema]
dialogue_response = {{ type = "str" }}
"""

PIPELINE = """
[meta]
name = "acme.stream"
[[nodes]]
kind = "handler"
name = "stream_prep_mod.prep"
[[nodes]]
kind = "composition"
name = "trainables/dialogue.toml"
[inputs]
raw = { type = "str" }
[outputs]
dialogue_response = { type = "str" }
"""

DEPLOYMENT = """
[transport.llm]
endpoint = "https://llm.test/v1"
[training_contract]
integrity_enforcement = false
"""

#: The streaming stub AT the adapter seam. `invoke_streaming` is a REAL generator:
#: yields the emission's raw JSON text in three fragments, returns the parsed dict —
#: exactly the surface contract a live streaming backend implements. `invoke` returns a
#: DIFFERENT marker value, so which dispatch surface ran is observable from the result
#: (no module/class mutable state — the adapter purity posture holds for stubs too).
STUB_BACKEND = """
import json

class StubStreamingBackend:
    training_artifact_contract = "gguf"
    reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

    def __init__(self, model, *, output_schema, schema_source):
        self.model = model

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, temperature, max_tokens, **transport_extra):
        return {"dialogue_response": "buffered:" + input_payload["formatted_prompt"]}

    def invoke_streaming(self, *, input_payload, service_name, caller_qualified_name,
                         caller_position, temperature, max_tokens, **transport_extra):
        value = "streamed:" + input_payload["formatted_prompt"]
        emission = {"dialogue_response": value}
        text = json.dumps(emission)
        third = max(1, len(text) // 3)
        for start in range(0, len(text), third):
            yield text[start:start + third]
        return emission
"""


def _write_common_modules(module_dir, backend_source=STUB_BACKEND):
    _write_module(
        module_dir, "stream_prep_mod",
        """
        def prep(*, raw):
            return {"npc_state": "calm", "user_message": raw}
        """,
    )
    _write_module(
        module_dir, "stream_pp_mod",
        """
        def assemble_prompt(*, context, utterance, config):
            return {"prompt": context + "|" + utterance + "|" + config}
        """,
    )
    _write_module(module_dir, "stream_backend_mod", backend_source)


def _registry(streamable: str = "true",
              backend: str = "stream_backend_mod.StubStreamingBackend"):
    reg = DeclarationRegistry()
    reg.add_service_type(
        loads(SERVICE_TYPE.replace("stream_backend_mod.StubStreamingBackend", backend),
              "service_type", file_path="st.toml"),
        toml_path="st.toml",
    )
    reg.add_handler(
        "stream_prep_mod.prep",
        loads(PREP_TRANSFORM, "handler", file_path="prep.toml"),
        toml_path="handlers/prep.toml",
    )
    reg.add_handler(
        "stream_pp_mod.assemble_prompt",
        loads(PREPROC_FORMATTER, "handler", file_path="pp.toml"),
        toml_path="handlers/pp.toml",
    )
    reg.add_composition(
        "trainables/dialogue.toml",
        loads(
            _COMPOSITION_TEMPLATE.format(streamable=streamable, backend=backend),
            "composition", file_path="trainables/dialogue.toml",
        ),
    )
    return reg


def _runnable(module_dir, *, streamable: str = "true",
              backend: str = "stream_backend_mod.StubStreamingBackend",
              backend_source: str = STUB_BACKEND):
    _write_common_modules(module_dir, backend_source)
    reg = _registry(streamable, backend)
    pipeline = loads(PIPELINE, "pipeline", file_path="p.toml")
    deployment = loads(DEPLOYMENT, "deployment", file_path="d.toml")
    graph = compile_pipeline(
        pipeline, reg, pipeline_name="acme.stream", deployment=deployment,
        file_path="p.toml",
    )
    return assemble(graph, reg, deployment)


EXPECTED_VALUE = "streamed:calm|hello|T"


# ---------------------------------------------------------------------------
# 1. The happy path — fragments delivered, the channel gets ONE complete value
# ---------------------------------------------------------------------------


def test_streaming_delivers_fragments_and_one_complete_validated_value(module_dir):
    """The core contract: the sink receives the raw fragments in emission order WHILE
    the terminal dispatch is in flight, and the run's authoritative value — the routed
    channel, the RunResult state, the captured handler_exit record — is exactly the one
    complete validated emission, never a fragment (pipeline/reference.md
    § Orchestration scope, the no-mid-invocation-partial-values seal)."""
    runnable = _runnable(module_dir)
    fragments: list[str] = []
    captured, detach = _capture_events()
    try:
        result = run(runnable, {"raw": "hello"}, stream_sink=fragments.append)
    finally:
        detach()
    # Fragments arrived, in order, and assemble to the emission's raw wire text.
    assert len(fragments) >= 3
    assert "".join(fragments) == (
        '{"dialogue_response": "' + EXPECTED_VALUE + '"}'
    )
    # The streaming dispatch surface ran (not the buffered one).
    assert result.state["dialogue_response"] == EXPECTED_VALUE
    # The captured training record is the complete validated value — the trainable's
    # handler_exit writes_snapshot (per-kind capture), never a fragment.
    from conjured import events as E

    exits = [e for e in captured if isinstance(e, E.HandlerExit)
             and e.node_kind == "trainable"]
    assert len(exits) == 1
    assert exits[0].writes_snapshot == {"dialogue_response": EXPECTED_VALUE}


def test_no_sink_uses_the_buffered_surface_byte_identical(module_dir):
    """`streamable = true` with NO sink attached runs the buffered `invoke` path —
    streaming is opt-in per invocation; the declaration alone changes nothing at
    dispatch (the field is a delivery capability, not a behavior toggle)."""
    runnable = _runnable(module_dir)
    result = run(runnable, {"raw": "hello"})
    assert result.state["dialogue_response"] == "buffered:calm|hello|T"


def test_streamed_run_emits_no_new_canonical_event_types(module_dir):
    """Token deltas NEVER ride the canonical event channel — a streamed run emits
    exactly the same closed-enum event types as a buffered run (the closed enum is
    the training-log substrate; a delta is provisional transport)."""
    from conjured import events as E

    runnable = _runnable(module_dir)
    captured, detach = _capture_events()
    try:
        run(runnable, {"raw": "hello"}, stream_sink=lambda _f: None)
    finally:
        detach()
    assert {type(e) for e in captured} <= {
        E.PipelineStart, E.HandlerEnter, E.HandlerExit, E.PipelineComplete,
    }


# ---------------------------------------------------------------------------
# 2. The run-boundary check — a sink with no route fails loud (never a no-op)
# ---------------------------------------------------------------------------


def test_sink_on_a_non_streamable_pipeline_raises(module_dir):
    """A stream_sink attached to a runnable with no streamable terminal raises the
    structured ContractViolation at the run boundary — the sink would silently never
    fire, and a silent no-op sink is a contract lie (the STREAMABLE_SINK_TARGET
    negative)."""
    runnable = _runnable(module_dir, streamable="false")
    with pytest.raises(ContractViolation) as exc:
        run(runnable, {"raw": "hello"}, stream_sink=lambda _f: None)
    assert exc.value.check is Check.STREAMABLE_SINK_TARGET
    assert exc.value.rule_id == "R-pipeline-001"


# ---------------------------------------------------------------------------
# 3. The compose-time capability gate — a promise the binding cannot honor
# ---------------------------------------------------------------------------

STUB_NO_STREAMING = """
class StubStreamingBackend:
    training_artifact_contract = "gguf"
    reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

    def __init__(self, model, *, output_schema, schema_source):
        self.model = model

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, temperature, max_tokens, **transport_extra):
        return {"dialogue_response": "buffered"}
"""

STUB_NON_GENERATOR_STREAMING = STUB_NO_STREAMING + """
    def invoke_streaming(self, *, input_payload, service_name,
                         caller_qualified_name, caller_position, temperature,
                         max_tokens, **transport_extra):
        return {"dialogue_response": "not a generator"}
"""


@pytest.mark.parametrize(
    "backend_source, arm",
    [(STUB_NO_STREAMING, "absent"), (STUB_NON_GENERATOR_STREAMING, "non-generator")],
)
def test_streamable_true_requires_a_streaming_capable_backend(
    module_dir, backend_source, arm
):
    """`streamable = true` bound to a backend with no `invoke_streaming` GENERATOR
    fails at compose (assemble) with the structured ContractViolation — never a silent
    buffered fallback at dispatch (the STREAMABLE_BACKEND_SUPPORT negative; both the
    absent-method and the plain-method arms)."""
    with pytest.raises(ContractViolation) as exc:
        _runnable(module_dir, backend_source=backend_source)
    assert exc.value.check is Check.STREAMABLE_BACKEND_SUPPORT
    assert exc.value.rule_id == "R-handler-008"


def test_streamable_false_never_requires_the_capability(module_dir):
    """A non-streamable composition makes no delivery promise — the same
    streaming-incapable backend composes and runs clean (the gate is scoped to the
    promise, not imposed on every backend)."""
    runnable = _runnable(
        module_dir, streamable="false", backend_source=STUB_NO_STREAMING
    )
    result = run(runnable, {"raw": "hello"})
    assert result.state["dialogue_response"] == "buffered"


# ---------------------------------------------------------------------------
# 4. Sink failure — the observation-plane wall (absorb + log + detach)
# ---------------------------------------------------------------------------


def test_raising_sink_is_absorbed_logged_detached_and_the_run_completes(
    module_dir, caplog
):
    # verifies: stream-sink-consumer-isolated
    """The exact adversary the wall defends against: a sink that RAISES on its first
    fragment (pipeline/reference.md § Pipeline invocation — the observation-plane
    posture). The engine absorbs the raise, surfaces it on the `conjured.runner`
    operational logger, and detaches the sink for the rest of the dispatch: no later
    fragment reaches the consumer's callback, the backend generator still runs to
    completion, and the run's authoritative value is untouched — the channel, the
    RunResult, and the captured handler_exit record all carry the complete validated
    emission. Remove the wall and the raise escapes as a halt; remove the detach and
    the sink sees more than one fragment; remove the surfacing and the log is empty —
    each arm goes RED."""
    runnable = _runnable(module_dir)
    calls: list[str] = []

    def exploding_sink(fragment: str) -> None:
        calls.append(fragment)
        raise ValueError("consumer sink exploded")

    captured, detach = _capture_events()
    try:
        with caplog.at_level(logging.WARNING, logger="conjured.runner"):
            result = run(runnable, {"raw": "hello"}, stream_sink=exploding_sink)
    finally:
        detach()
    # The run COMPLETED on the streaming surface — the raise never became a halt.
    assert result.state["dialogue_response"] == EXPECTED_VALUE
    # Detach: the sink was invoked exactly once (the fragment that raised); the
    # remaining fragments were never delivered to the detached callback.
    assert len(calls) == 1
    # The failure stayed VISIBLE — the operational-log record names the wall.
    [record] = [r for r in caplog.records if "stream_sink raised" in r.getMessage()]
    assert record.levelno == logging.WARNING and record.name == "conjured.runner"
    assert "detached" in record.getMessage()
    # The captured training record is intact — the complete validated value, exactly
    # as a run whose sink kept up (observation never corrupts the record).
    from conjured import events as E

    exits = [e for e in captured if isinstance(e, E.HandlerExit)
             and e.node_kind == "trainable"]
    assert len(exits) == 1
    assert exits[0].writes_snapshot == {"dialogue_response": EXPECTED_VALUE}


# ---------------------------------------------------------------------------
# 5. Validate-on-assembly — fragments are provisional; the boundary still seals
# ---------------------------------------------------------------------------

STUB_INVALID_ASSEMBLED = """
class StubStreamingBackend:
    training_artifact_contract = "gguf"
    reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

    def __init__(self, model, *, output_schema, schema_source):
        self.model = model

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, temperature, max_tokens, **transport_extra):
        return {"dialogue_response": "buffered"}

    def invoke_streaming(self, *, input_payload, service_name,
                         caller_qualified_name, caller_position, temperature,
                         max_tokens, **transport_extra):
        yield '{"dialogue_response": '
        yield "42}"
        return {"dialogue_response": 42}
"""


def test_streamed_fragments_deliver_then_invalid_assembled_value_halts(module_dir):
    """Validate-on-assembly (the decided streaming design point): fragments the
    consumer already received are PROVISIONAL — when the assembled value fails the
    output boundary, the run halts with the ruled SchemaValidationError and the
    channel is never written. Acting on provisional fragments (TTS already speaking)
    is consumer territory; the engine's authoritative value is the validated write."""
    runnable = _runnable(module_dir, backend_source=STUB_INVALID_ASSEMBLED)
    fragments: list[str] = []
    with pytest.raises(SchemaValidationError) as exc:
        run(runnable, {"raw": "hello"}, stream_sink=fragments.append)
    assert exc.value.audit_code == OUTPUT_VALIDATION_AUDIT_CODE
    # The fragments HAD been delivered before the halt — provisional by contract.
    assert "".join(fragments) == '{"dialogue_response": 42}'


# ---------------------------------------------------------------------------
# 6. Terminal-modulo-hooks + the nested-embed route
# ---------------------------------------------------------------------------

HOOK_AFTER = """
[hook]
[reads]
dialogue_response = { type = "str" }
[service_bindings]
[transport_schema]
"""


def test_sink_routes_past_a_trailing_hook(module_dir):
    """R-pipeline-001 admits hooks after a streamable trainable (they write no
    channels) — the sink route resolution skips them and still reaches the streamable
    terminal."""
    _write_common_modules(module_dir)
    _write_module(
        module_dir, "stream_hook_mod",
        """
        def observe(*, dialogue_response):
            return None
        """,
    )
    reg = _registry()
    reg.add_handler(
        "stream_hook_mod.observe",
        loads(HOOK_AFTER, "handler", file_path="hook.toml"),
        toml_path="handlers/hook.toml",
    )
    pipeline = loads(
        PIPELINE.replace(
            "[inputs]",
            '[[nodes]]\nkind = "handler"\nname = "stream_hook_mod.observe"\n[inputs]',
        ),
        "pipeline", file_path="p.toml",
    )
    deployment = loads(
        DEPLOYMENT + '[hook_transport."stream_hook_mod.observe"]\n',
        "deployment", file_path="d.toml",
    )
    graph = compile_pipeline(
        pipeline, reg, pipeline_name="acme.stream", deployment=deployment,
        file_path="p.toml",
    )
    runnable = assemble(graph, reg, deployment)
    fragments: list[str] = []
    result = run(runnable, {"raw": "hello"}, stream_sink=fragments.append)
    assert "".join(fragments) == '{"dialogue_response": "' + EXPECTED_VALUE + '"}'
    assert result.state["dialogue_response"] == EXPECTED_VALUE


def test_sink_threads_into_a_terminal_nested_pipeline_embed(module_dir):
    """A terminal nested `pipeline` embed whose OWN terminal is a streamable trainable
    receives the sink through the recursion — the inner stream IS the outer stream
    (the nested-run correlation model applied to delivery)."""
    _write_common_modules(module_dir)
    reg = _registry()
    # The inner pipeline: just the streamable trainable composition.
    reg.add_composition(
        "pipelines/inner.toml",
        loads(
            '[meta]\nkind = "pipeline"\nname = "acme.inner"\n'
            '[[nodes]]\nkind = "composition"\nname = "trainables/dialogue.toml"\n'
            '[inputs]\nnpc_state = { type = "str" }\n'
            'user_message = { type = "str" }\n'
            '[outputs]\ndialogue_response = { type = "str" }\n',
            "composition", file_path="pipelines/inner.toml",
        ),
    )
    outer = loads(
        '[meta]\nname = "acme.outer"\n'
        '[[nodes]]\nkind = "handler"\nname = "stream_prep_mod.prep"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/inner.toml"\n'
        '[inputs]\nraw = { type = "str" }\n'
        '[outputs]\ndialogue_response = { type = "str" }\n',
        "pipeline", file_path="outer.toml",
    )
    deployment = loads(DEPLOYMENT, "deployment", file_path="d.toml")
    graph = compile_pipeline(
        outer, reg, pipeline_name="acme.outer", deployment=deployment,
        file_path="outer.toml",
    )
    runnable = assemble(graph, reg, deployment)
    fragments: list[str] = []
    result = run(runnable, {"raw": "hello"}, stream_sink=fragments.append)
    assert "".join(fragments) == '{"dialogue_response": "' + EXPECTED_VALUE + '"}'
    assert result.state["dialogue_response"] == EXPECTED_VALUE
