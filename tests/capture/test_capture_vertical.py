"""The capture vertical slice — the first behaviorally-complete end-to-end proof.

This module assembles the minimal end-to-end pipeline that exercises the three real
seams (``inputs → transform → service → trainable → outputs``) through the *already-built*
runner, and proves the capture vertical: **the emitted event records, replayed,
reproduce the same hashes** (the pin-and-compare proof,
``architecture/hash-model.md`` § Hash-pinning — "What a capture proof asserts").

This suite adds **no engine code**. Everything here is assembly + two slice-local handlers (a
pure transform and an in-memory service, written as ``_write_module`` fixtures) + the
real ``conjured.lib.gbnf_trainable.GBNFTrainable`` native as the trainable backend with
only its **HTTP transport faked** — plus the capture-proof harness, all test-side.

The slice (positions are the engine's final compose-time dispatch order):

- **position 0 — transform** (``compose_context``): a slice-local *pure* bare
  kwarg-only function (vector 1/2/3 sealed by construction). ``raw → context``.
- **position 1 — service** (``enrich``): a slice-local service bound to a slice-local
  service-type whose adapter is an **in-memory deterministic backend** — the double at
  the service-type adapter seam (legitimate, outside engine code; it exercises the
  ``service_invocation`` adapter-boundary capture, the silent-fallback seam). The body
  reshapes the raw backend response, so ``output_payload != writes_snapshot`` — the
  divergence signal a consumer-side analyzer reads. ``context → assembled_prompt``.
- **position 2 — trainable** (the ``dialogue_training`` composition's terminal node): the
  **real GBNF native**, engine-constructed dispatch (no author body, R-handler-010), with
  the module-global ``urllib_transport`` faked by a contract-satisfying
  ``FakeLlamaServer`` (the only injection that reaches an engine-constructed node — the
  adapter reads the global lazily on first ``invoke()``). ``assembled_prompt →
  dialogue_response``.

The doubles are **only** at external seams and each fails where the runtime would: the
in-memory service backend routes its handler's emission through real output validation,
and ``FakeLlamaServer`` validates the submitted GBNF grammar structurally the way
llama-server does (``tests/lib/fakes.py`` — the standing double rule). No engine internal
is mocked.
"""

from __future__ import annotations

import importlib
import logging
import textwrap
import tomllib
from pathlib import Path

import pytest

import conjured.lib
from conjured import events as E
from conjured.errors import PipelineFailure
from conjured.hasher.hashes import pipeline_hash, training_bundle_hash
from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.common import ServiceBindingDecl, ServiceBindingSupply
from conjured.ir.handler import ServiceDeclaration, TransformDeclaration
from conjured.ir.pipeline import (
    CompositionNode,
    HandlerNode,
    PipelineDeclaration,
    PipelineMeta,
)
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.lib import NATIVE_TRAINABLE_ADAPTERS
from conjured.runner.assemble import assemble
from conjured.runner.run import RunResult, run
from conjured.validator import DeclarationRegistry, loads
from conjured.validator.compile import compile_pipeline
from conjured.validator.parse import parse_service_type
from tests.lib.fakes import FakeLlamaServer

# The GBNF native's shipped service-type declaration + its real qualified name.
GBNF_QUALIFIED_NAME = "conjured.lib.gbnf_trainable"
GBNF_TOML_PATH = Path(conjured.lib.__file__).parent / "gbnf_trainable.toml"
# The GBNF native binds by its NATIVE QUALIFIED NAME (`conjured.lib.gbnf_trainable`) — the
# engine's native adapter table maps it to the shipped `GBNFTrainable` implementation and
# resolves it ahead of the (test-empty) entry-points group (handler-resolution.md § Native
# adapters). The registered service-type IS the shipped declaration under its real name (no
# rename): `NATIVE_TRAINABLE_ADAPTERS` still records the class path the consult routes to,
# and the shipped `[config_schema]` (temperature / max_tokens / extras) is kept verbatim —
# it must match the real `GBNFTrainable.invoke` signature, which is why the shipped TOML is
# loaded, not hand-rolled.
assert NATIVE_TRAINABLE_ADAPTERS[GBNF_QUALIFIED_NAME] == "conjured.lib.gbnf_trainable.GBNFTrainable"

# The held-fixed backend emission — the value the grammar-constrained decode "produces".
# Held fixed across runs (the determinism replay needs), so the trainable's training
# record is reproducible.
EMISSION = {"dialogue_response": "Aye, well met, traveler."}

# The slice's input and the deterministic values that thread through it.
INPUT_RAW = "hello"
EXPECTED_CONTEXT = "[ctx] hello"  # compose_context: "[ctx] " + raw
EXPECTED_ASSEMBLED = "<<[ctx] hello>>"  # the in-memory backend: "<<" + text + ">>"
EXPECTED_DIALOGUE = EMISSION["dialogue_response"]


# ---------------------------------------------------------------------------
# Fixtures + slice construction (the established test_run.py patterns)
# ---------------------------------------------------------------------------


@pytest.fixture
def module_dir(tmp_path, monkeypatch):
    """A fresh importable module root per test (the ``test_run.py`` pattern)."""
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, name: str, source: str) -> None:
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    importlib.invalidate_caches()


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


def _capture_events():
    """Attach a consumer ``logging.Handler`` to the canonical event channel
    ``conjured.events.runner`` (the engine ships none — producer/consumer;
    ``architecture/components.md`` § Canonical event log). Returns
    ``(captured_list, detach)``; ``captured`` holds the ordered event objects (the
    ``record.msg`` payloads)."""
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


# The trainable composition — ZERO preprocessors: its terminal trainable reads the
# composition's `[inputs]` boundary channel `assembled_prompt` directly (the upstream
# service writes it), so the flattened slice is exactly transform(0) → service(1) →
# trainable(2). The backend is the real GBNF native, bound by its dotted class path.
_COMPOSITION_TOML = f"""
[meta]
kind = "trainable"
name = "dialogue_training"
[inputs]
assembled_prompt = {{ type = "str" }}
[outputs]
dialogue_response = {{ type = "str" }}
[service_bindings.llm]
type = "{GBNF_QUALIFIED_NAME}"
model = "qwen3.5-4b-gguf"
[trainable]
[trainable.config]
temperature = 0.7
max_tokens = 128
[trainable.service_bindings]
llm = {{ type = "{GBNF_QUALIFIED_NAME}" }}
[trainable.reads]
assembled_prompt = {{ type = "str" }}
[trainable.output_schema]
dialogue_response = {{ type = "str" }}
"""

# The deployment: `transport.backend` covers the slice-local service binding (its
# service-type declares no transport fields, so the block is empty-but-present — the
# compose-time coverage check still requires it); `transport.llm` carries the GBNF
# backend's required `endpoint` plus its two nullable fields as EXPLICIT nulls — the
# uniform presence law's worked example (a considered-and-null field is present as
# `{ null = true }`, never omitted; R-pipeline-001/transport-coverage), delivered to
# invoke() as Python None.
_DEPLOYMENT_TOML = """
[transport.backend]
[transport.llm]
endpoint    = "http://llama.test/v1"
api_key_ref = { null = true }
timeout_ms  = { null = true }
[training_contract]
integrity_enforcement = false
"""


def _shipped_gbnf_service_type():
    """The shipped GBNF service-type declaration, registered under its real native qualified
    name so the native-table consult resolves ``GBNFTrainable`` (handler-resolution.md
    § Native adapters). It IS the engine-shipped declaration (no name override), so the
    registry's engine-owned-identity guard admits it (R-service-type-004). The three shipped
    schema sections — identity / transport / `[config_schema]` — are kept verbatim
    (un-hand-rolled): the `[config_schema]` fields are exactly the kwargs the real `invoke()`
    declares."""
    with open(GBNF_TOML_PATH, "rb") as fh:
        data = tomllib.load(fh)
    return parse_service_type(data, file_path=str(GBNF_TOML_PATH))


def _build_slice(module_dir, *, suffix: str, fail_mode: str | None = None):
    """Assemble the slice into a frozen ``Runnable`` and return everything the proof
    needs. ``fail_mode`` selects the run-time failure locus (error-path cases):
    ``None`` → happy; ``"service"`` → the in-memory adapter's ``invoke()`` raises (the
    adapter boundary → ``failure_category="service"``); ``"handler"`` → the service
    handler BODY raises before any backend call (→ ``failure_category="handler"``, null
    binding) — the structural-locus contrast that makes the ``"service"`` assertion bite."""
    handlers_mod = f"cv_{suffix}_handlers"
    adapters_mod = f"cv_{suffix}_adapters"

    # The service handler body — either the normal reshape-and-return, or (handler locus) a
    # raise from the BODY itself (NOT the adapter), which the runner attributes to "handler".
    enrich_body = (
        'raise RuntimeError("handler body failed before the backend call")'
        if fail_mode == "handler"
        else 'return {"assembled_prompt": services.backend.invoke(text=context)["prompt"]}'
    )
    _write_module(
        module_dir, handlers_mod,
        f"""
        def compose_context(*, raw):
            # A real pure transform — no double; the prompt-assembly leg.
            return {{"context": "[ctx] " + raw}}

        def enrich(*, context, services):
            # The service leg: call the bound adapter, reshape its raw response into the
            # declared output_schema (the reshape is what makes output_payload diverge
            # from writes_snapshot — the silent-fallback divergence signal).
            {enrich_body}
        """,
    )

    # The adapter body — either the deterministic in-memory response, or (service locus) a
    # raise from INSIDE invoke() (the adapter boundary the engine wraps as the service locus).
    backend_body = (
        'raise RuntimeError("in-memory backend unreachable")'
        if fail_mode == "service"
        else 'return {"prompt": "<<" + input_payload["text"] + ">>", "trace": "backend-internal"}'
    )
    _write_module(
        module_dir, adapters_mod,
        f"""
        class InMemoryBackend:
            \"\"\"The slice-local service-type adapter (the in-memory deterministic backend
            at the adapter seam). From-spec minimal: instance-state-only (no class- or
            module-level mutable state — vector 7), identity-only __init__, the closed
            dispatch-kwargs invoke().\"\"\"

            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                {backend_body}
        """,
    )

    svc_type_name = f"{adapters_mod}.InMemoryBackend"
    reg = DeclarationRegistry()

    # The slice-local service-type + its in-memory adapter (resolves by dotted class path).
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=svc_type_name, identity_schema=(_fd("model"),),
            transport_schema=(), config_schema=(),
        ),
        toml_path="capture/service_type.toml",
    )
    # The real GBNF native service-type (shipped TOML under its real native qualified name).
    reg.add_service_type(_shipped_gbnf_service_type(), toml_path=str(GBNF_TOML_PATH))

    # The two slice-local handlers.
    reg.add_handler(
        f"{handlers_mod}.compose_context",
        TransformDeclaration(reads=(_fd("raw"),), output_schema=(_fd("context"),)),
        toml_path="capture/handlers.toml",
    )
    reg.add_handler(
        f"{handlers_mod}.enrich",
        ServiceDeclaration(
            reads=(_fd("context"),),
            output_schema=(_fd("assembled_prompt"),),
            service_bindings=(ServiceBindingDecl(name="backend", type=svc_type_name),),
        ),
        toml_path="capture/handlers.toml",
    )

    # The trainable composition (zero preprocessors).
    comp_path = "trainables/dialogue.toml"
    reg.add_composition(
        comp_path, loads(_COMPOSITION_TOML, "composition", file_path=comp_path)
    )

    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="cv.capture_slice"),
        nodes=(
            HandlerNode(name=f"{handlers_mod}.compose_context"),
            HandlerNode(name=f"{handlers_mod}.enrich"),
            CompositionNode(name=comp_path),
        ),
        service_bindings=(
            ServiceBindingSupply(name="backend", type=svc_type_name, identity={"model": "echo-1"}),
        ),
        inputs=(_fd("raw"),),
        outputs=(_fd("dialogue_response"),),
    )
    deployment = loads(_DEPLOYMENT_TOML, "deployment", file_path="capture/deployment.toml")

    graph = compile_pipeline(
        pipeline, reg, pipeline_name="cv.capture_slice",
        deployment=deployment, file_path="capture/pipeline.toml",
    )
    runnable = assemble(graph, reg, deployment)
    return runnable, pipeline, reg, comp_path


def _patch_backend(monkeypatch):
    """Fake ONLY the GBNF native's HTTP transport (the one injection that reaches an
    engine-constructed node — the adapter reads the module global lazily on first
    invoke()). ``FakeLlamaServer`` validates the submitted grammar the way llama-server
    does, so a malformed constraint cannot pass silently. Returns the fake (records its
    requests)."""
    fake = FakeLlamaServer(EMISSION)
    monkeypatch.setattr("conjured.lib.gbnf_trainable.urllib_transport", fake)
    return fake


def _tag(event) -> tuple[str, object]:
    """An event's (type-name, handler_position) — position is ``None`` for the
    run-lifecycle events that name no node."""
    return (type(event).__name__, getattr(event, "handler_position", None))


# ---------------------------------------------------------------------------
# The slice runs end-to-end through the built runner
# ---------------------------------------------------------------------------


def test_slice_runs_end_to_end_through_the_built_runner(module_dir, monkeypatch):
    """The vertical slice assembles and runs: a ``RunResult`` whose declared
    [outputs] projection carries the trainable's emission; the three seam outputs reach
    outer channel state; the run id is engine-minted."""
    fake = _patch_backend(monkeypatch)
    runnable, _pipeline, _reg, _comp_path = _build_slice(module_dir, suffix="happy")

    result = run(runnable, {"raw": INPUT_RAW})

    assert isinstance(result, RunResult)
    # The three seam writes reach outer state; the trainable emission is the declared output.
    assert dict(result.state) == {
        "context": EXPECTED_CONTEXT,
        "assembled_prompt": EXPECTED_ASSEMBLED,
        "dialogue_response": EXPECTED_DIALOGUE,
    }
    # The flattened dispatch order IS transform → service → trainable.
    assert [n.node_kind for n in runnable.nodes] == ["transform", "service", "trainable"]
    assert [n.position for n in runnable.nodes] == [0, 1, 2]
    assert runnable.nodes[2].module is None  # the trainable has no author body (R-handler-010)
    # The real native was driven, with only the HTTP call faked.
    assert len(fake.requests) == 1
    assert fake.requests[0]["url"] == "http://llama.test/v1/completion"
    assert fake.requests[0]["body"]["prompt"] == EXPECTED_ASSEMBLED  # the verbatim reads
    # The REAL native submits the rendered output_schema as a GBNF decode constraint
    # (gbnf_trainable.py property 1) — a stub-at-the-seam carries no grammar; ``FakeLlamaServer``
    # 400s a grammarless body, so this is the real-native distinguisher and is RED-on-removal.
    grammar = fake.requests[0]["body"]["grammar"]
    assert isinstance(grammar, str) and grammar.strip()


# ---------------------------------------------------------------------------
# Event-stream completeness + per-kind capture (hash-model § Event-log spec)
# ---------------------------------------------------------------------------


def test_event_stream_is_complete_and_per_kind_correct(module_dir, monkeypatch):
    """The happy run emits the complete, position-ordered canonical stream, every event a
    member of the closed 8-type enum, with the per-kind capture rule
    (``hash-model.md`` § Adapter-boundary capture, L466-480): the service kind's record is
    the ``service_invocation`` at the adapter boundary; the trainable kind's record IS its
    ``handler_enter``/``handler_exit`` pair, with NO ``service_invocation``. The two
    compose-time ``*_hash_changed`` events are absent (no manifest declared)."""
    _patch_backend(monkeypatch)
    runnable, _pipeline, _reg, _comp_path = _build_slice(module_dir, suffix="events")

    captured, detach = _capture_events()
    try:
        result = run(runnable, {"raw": INPUT_RAW})
    finally:
        detach()

    # (i) Every emitted event is a member of the closed 8-type canonical enum.
    assert all(isinstance(e, E.CANONICAL_EVENT_CLASSES) for e in captured)

    # (ii) The no-manifest happy run emits exactly this ordered runtime stream:
    # pipeline_start → (transform enter/exit) → (service enter / service_invocation / exit)
    # → (trainable enter/exit) → pipeline_complete.
    assert [_tag(e) for e in captured] == [
        ("PipelineStart", None),
        ("HandlerEnter", 0), ("HandlerExit", 0),
        ("HandlerEnter", 1), ("ServiceInvocation", 1), ("HandlerExit", 1),
        ("HandlerEnter", 2), ("HandlerExit", 2),
        ("PipelineComplete", None),
    ]
    # The two compose-time integrity-enforcement events fire only against a loaded manifest
    # baseline — the slice declares none, so they are absent (the Boundary note). And no error.
    assert not any(isinstance(e, E.TrainingBundleHashChanged) for e in captured)
    assert not any(isinstance(e, E.PipelineHashChanged) for e in captured)
    assert not any(isinstance(e, E.PipelineError) for e in captured)

    by_pos_enter = {e.handler_position: e for e in captured if isinstance(e, E.HandlerEnter)}
    by_pos_exit = {e.handler_position: e for e in captured if isinstance(e, E.HandlerExit)}
    [start] = [e for e in captured if isinstance(e, E.PipelineStart)]
    [complete] = [e for e in captured if isinstance(e, E.PipelineComplete)]
    service_invocations = [e for e in captured if isinstance(e, E.ServiceInvocation)]

    # pipeline_start: carries the pinned pipeline_hash + the seeded inputs; top-level → no parent.
    assert start.pipeline_hash == runnable.pipeline_hash
    assert start.pipeline_hash.startswith("sha256:")
    assert start.inputs_snapshot == {"raw": INPUT_RAW}
    assert start.parent_run_id is None

    # transform (position 0): the reads/writes training pair, no correlation_id (not a service).
    assert by_pos_enter[0].node_kind == "transform"
    assert by_pos_enter[0].reads_snapshot == {"raw": INPUT_RAW}
    assert by_pos_exit[0].writes_snapshot == {"context": EXPECTED_CONTEXT}
    assert by_pos_exit[0].correlation_id is None

    # service (position 1): the service_invocation IS the captured record at the adapter
    # boundary; input_payload is what the body submitted, output_payload the RAW backend
    # response (pre-reshape). The pair joins handler_exit by correlation_id.
    assert len(service_invocations) == 1  # I4 per-kind: one service_invocation, for the service
    [si] = service_invocations
    assert si.handler_position == 1
    assert si.input_payload == {"text": EXPECTED_CONTEXT}
    assert si.output_payload == {"prompt": EXPECTED_ASSEMBLED, "trace": "backend-internal"}
    assert si.pipeline_hash == runnable.pipeline_hash
    assert si.correlation_id == f"{result.run_id}:1"
    assert by_pos_exit[1].node_kind == "service"
    assert by_pos_exit[1].correlation_id == si.correlation_id
    assert by_pos_exit[1].writes_snapshot == {"assembled_prompt": EXPECTED_ASSEMBLED}
    # The divergence signal: the body reshaped the backend response.
    assert si.output_payload != by_pos_exit[1].writes_snapshot

    # trainable (position 2): the kind-keyed capture — the enter/exit pair IS the training
    # record (reads in, writes out); NO service_invocation fires for it; correlation_id None.
    assert by_pos_enter[2].node_kind == "trainable"
    assert by_pos_exit[2].node_kind == "trainable"
    assert by_pos_enter[2].reads_snapshot == {"assembled_prompt": EXPECTED_ASSEMBLED}
    assert by_pos_exit[2].writes_snapshot == {"dialogue_response": EXPECTED_DIALOGUE}
    assert by_pos_exit[2].correlation_id is None
    assert not any(e.handler_position == 2 for e in service_invocations)

    # pipeline_complete: the DECLARED [outputs] projection only.
    assert complete.outputs_snapshot == {"dialogue_response": EXPECTED_DIALOGUE}


# ---------------------------------------------------------------------------
# The capture-vertical proof — pin-and-compare (hash-model § Hash-pinning, L441-452)
# ---------------------------------------------------------------------------


def test_capture_proof_pipeline_hash_leg(module_dir, monkeypatch):
    """Pipeline-hash leg: recompute the pipeline-hash from the UNCHANGED declaration IR and
    assert it equals the ``pipeline_hash`` pinned on the captured ``pipeline_start`` (which
    equals ``runnable.pipeline_hash``), and is deterministic. It is recomputed from the
    declaration, NOT reconstructed from any event payload (no per-run event carries the
    declaration IR)."""
    _patch_backend(monkeypatch)
    runnable, pipeline, reg, _comp_path = _build_slice(module_dir, suffix="phash")

    captured, detach = _capture_events()
    try:
        run(runnable, {"raw": INPUT_RAW})
    finally:
        detach()
    [start] = [e for e in captured if isinstance(e, E.PipelineStart)]

    # Recompute from the unchanged declaration (NOT from event payloads).
    recomputed = pipeline_hash(pipeline, reg)
    assert recomputed == start.pipeline_hash      # equals the value the events pinned
    assert recomputed == runnable.pipeline_hash   # equals what assemble computed
    # Deterministic: recompute twice → equal.
    assert pipeline_hash(pipeline, reg) == recomputed
    # The captured run carries exactly one pipeline-hash, and it is the pinned value.
    assert {e.pipeline_hash for e in captured if hasattr(e, "pipeline_hash")} == {recomputed}

    # Sensitivity — the recompute must TRACK the declaration, else the equality above is
    # vacuous (a constant-returning hasher would pass it). A hash-NEUTRAL edit recomputes
    # EQUAL; a hash-ABSORBED edit recomputes DIFFERENT — so a broken/constant hasher goes RED.
    # Renaming the pipeline is hash-neutral (the family rule — meta.name is not hashed,
    # hash-model.md § What is explicitly NOT in the pipeline-hash):
    renamed = pipeline.model_copy(update={"meta": PipelineMeta(name="cv.capture_slice_renamed")})
    assert pipeline_hash(renamed, reg) == recomputed
    # A pipeline-level service-binding identity value IS absorbed (hash-model.md § What the
    # pipeline-hash absorbs — service_bindings identity values), so changing it shifts the hash:
    svc_type = pipeline.service_bindings[0].type
    perturbed = pipeline.model_copy(update={
        "service_bindings": (
            ServiceBindingSupply(name="backend", type=svc_type, identity={"model": "echo-CHANGED"}),
        ),
    })
    assert pipeline_hash(perturbed, reg) != recomputed


def test_capture_proof_training_leg(module_dir, monkeypatch):
    """Training leg. (a) The trainable's training-bundle-hash recomputes **deterministically
    from its declaration TOML** (recompute twice → equal); it is NOT reconstructed from any
    event payload — no per-run event carries it (it is a compose-time function of the
    trainable's declared structural membership). (b) With the faked backend held fixed,
    REPLAY the run and assert the captured ``handler_enter``/``handler_exit`` pair reproduces
    the training record (identical reads/writes snapshots across runs)."""
    _patch_backend(monkeypatch)
    runnable, _pipeline, reg, comp_path = _build_slice(module_dir, suffix="tbh")
    composition = reg.get_composition(comp_path)

    # (a) TBH recomputes deterministically from the composition declaration.
    tbh = training_bundle_hash(composition, reg)
    assert training_bundle_hash(composition, reg) == tbh  # deterministic
    assert tbh.startswith("sha256:")

    # Sensitivity — the TBH must TRACK the trainable's declared structural membership, else
    # the determinism check is vacuous (a constant hasher passes it). Renaming the composition
    # is hash-neutral (the family rule — meta.name excluded); a trainable-config value IS
    # absorbed (hash-model.md § Training-bundle-hash — the effective config values fold in),
    # so changing it shifts the TBH. A broken/constant hasher fails the ``!=``.
    renamed_comp = loads(
        _COMPOSITION_TOML.replace('name = "dialogue_training"', 'name = "dialogue_training_v2"'),
        "composition", file_path=comp_path,
    )
    assert training_bundle_hash(renamed_comp, reg) == tbh
    perturbed_comp = loads(
        _COMPOSITION_TOML.replace("temperature = 0.7", "temperature = 0.9"),
        "composition", file_path=comp_path,
    )
    assert training_bundle_hash(perturbed_comp, reg) != tbh

    # (b) With the backend held fixed, the trainable's training pair (reads_snapshot in /
    # writes_snapshot out) reproduces identically — across a replay of the SAME runnable AND
    # across a FRESHLY, independently assembled slice (the stronger "same declaration + same
    # fixed backend reproduces the record" property, not merely same-object determinism).
    def _run_and_capture_trainable_pair(r):
        captured, detach = _capture_events()
        try:
            run(r, {"raw": INPUT_RAW})
        finally:
            detach()
        [enter] = [
            e for e in captured
            if isinstance(e, E.HandlerEnter) and e.node_kind == "trainable"
        ]
        [exit_] = [
            e for e in captured
            if isinstance(e, E.HandlerExit) and e.node_kind == "trainable"
        ]
        # The TBH is NOT on the per-run stream — nothing in the captured events carries it.
        assert all(not hasattr(e, "training_bundle_hash") for e in captured)
        return enter.reads_snapshot, exit_.writes_snapshot

    first_reads, first_writes = _run_and_capture_trainable_pair(runnable)
    second_reads, second_writes = _run_and_capture_trainable_pair(runnable)

    # A fresh, independent assembly (new registry, new adapter instance), backend held fixed.
    _patch_backend(monkeypatch)
    fresh_runnable, _fp, _fr, _fc = _build_slice(module_dir, suffix="tbh_fresh")
    fresh_reads, fresh_writes = _run_and_capture_trainable_pair(fresh_runnable)

    assert first_reads == second_reads == fresh_reads == {"assembled_prompt": EXPECTED_ASSEMBLED}
    assert first_writes == second_writes == fresh_writes == {"dialogue_response": EXPECTED_DIALOGUE}


# ---------------------------------------------------------------------------
# The error path — a service-adapter failure fires pipeline_error and raises
# (error-channel/reference.md § failure_category; hash-model § pipeline_error)
# ---------------------------------------------------------------------------


def test_error_path_service_adapter_failure(module_dir, monkeypatch):
    """A raise INSIDE the service adapter's ``invoke()`` (the adapter boundary the engine
    wraps as the ``service`` locus) halts the run with ``PipelineFailure``, across the two
    surfaces that carry different fields:

    - the captured ``pipeline_error`` EVENT — ``failure_category == "service"``,
      ``error_class == "PipelineFailure"``, ``cause_class`` the verbatim raised-exception
      name, and the failed in-flight service node named; the event carries NO
      ``service_binding_name`` (not a ``PipelineError`` field).
    - the raised ``PipelineFailure`` EXCEPTION — ``service_binding_name`` the failing
      binding, ``failure_category == "service"`` (the structural-locus invariant lives on
      the exception). A ``pipeline_start`` fired (the run began); no ``pipeline_complete``."""
    _patch_backend(monkeypatch)
    runnable, _pipeline, _reg, _comp_path = _build_slice(
        module_dir, suffix="err", fail_mode="service"
    )

    captured, detach = _capture_events()
    try:
        with pytest.raises(PipelineFailure) as exc:
            run(runnable, {"raw": INPUT_RAW}, pipeline_run_id="cv-err-run")
    finally:
        detach()

    # (a) The captured pipeline_error EVENT.
    [err] = [e for e in captured if isinstance(e, E.PipelineError)]
    assert err.failure_category == "service"  # the locus = WHERE it escaped (the adapter)
    assert err.error_class == "PipelineFailure"
    assert err.cause_class == "RuntimeError"  # the fixture's verbatim type name (G5: no well-known names)
    assert err.failed_handler_position == 1  # the in-flight service node
    assert err.failed_handler_qualified_name == "cv_err_handlers.enrich"
    assert err.pipeline_hash == runnable.pipeline_hash
    assert err.pipeline_run_id == "cv-err-run"
    assert err.error_message  # non-empty rendered message
    assert isinstance(err.elapsed_ms, int) and err.elapsed_ms >= 0
    assert err.timestamp
    # service_binding_name is NOT a PipelineError payload field — it lives on the exception.
    assert not hasattr(err, "service_binding_name")
    # The halt was IN-FLIGHT at the named service node: its handler_enter fired, its
    # handler_exit did NOT (handler_exit fires only on body completion).
    assert any(isinstance(e, E.HandlerEnter) and e.handler_position == 1 for e in captured)
    assert not any(isinstance(e, E.HandlerExit) and e.handler_position == 1 for e in captured)
    # A start fired (the run began); no complete (it halted).
    assert any(isinstance(e, E.PipelineStart) for e in captured)
    assert not any(isinstance(e, E.PipelineComplete) for e in captured)

    # (b) The raised PipelineFailure EXCEPTION carries the structural-locus invariant.
    pf = exc.value
    assert pf.failure_category == "service"
    assert pf.service_binding_name == "backend"
    assert pf.cause_class == "RuntimeError"


def test_error_path_handler_body_failure_is_handler_locus(module_dir, monkeypatch):
    """The structural-locus CONTRAST that makes the ``"service"`` assertion above bite: an
    identical slice whose service handler BODY raises (not the adapter) is attributed to the
    ``"handler"`` locus with a NULL binding — the locus is read from WHERE the exception
    escaped, never sniffed from the exception name (error-channel/reference.md
    § failure_category; run.py reads ``_ServiceOriginError`` for the service locus, else
    ``handler``). Were the engine to mis-attribute by exception name, the two cases would be
    indistinguishable; this case fails RED if the ``"service"`` locus were hardcoded."""
    _patch_backend(monkeypatch)
    runnable, _pipeline, _reg, _comp_path = _build_slice(
        module_dir, suffix="herr", fail_mode="handler"
    )

    captured, detach = _capture_events()
    try:
        with pytest.raises(PipelineFailure) as exc:
            run(runnable, {"raw": INPUT_RAW})
    finally:
        detach()

    [err] = [e for e in captured if isinstance(e, E.PipelineError)]
    assert err.failure_category == "handler"  # escaped the author body, not the adapter
    assert err.cause_class == "RuntimeError"
    assert err.failed_handler_position == 1
    assert err.failed_handler_qualified_name == "cv_herr_handlers.enrich"
    # A body raise never reached the backend, so NO service_invocation fired for the node.
    assert not any(isinstance(e, E.ServiceInvocation) for e in captured)

    pf = exc.value
    assert pf.failure_category == "handler"
    assert pf.service_binding_name is None  # the handler locus names no failing binding
