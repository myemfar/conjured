"""The kernel walk (``runner.run``) — end-to-end over real compiled + assembled
pipelines: declared-order channel threading, the API boundary (presence-only, extras
inert), the fold-as-you-walk merge semantics (every strategy ≥ 1 case), the
dispatch-boundary PipelineFailure wrap, the hook wrapper's two-case sanction, the
vector-3 module-namespace snapshot-restore, the cooperative timeout, and the trainable
composition end-to-end through a certified stub backend at the adapter seam.

Real modules on ``sys.path`` via ``tmp_path``; doubles only at the adapter seam
(stubs whose canned returns still fail where the runtime would — the dispatch wrappers
validate their returns exactly as a live backend's)."""

from __future__ import annotations

import importlib
import logging
import re
import textwrap
from types import MappingProxyType

import pytest

from conjured.errors import (
    INPUT_VALIDATION_AUDIT_CODE,
    OUTPUT_VALIDATION_AUDIT_CODE,
    Check,
    ContractViolation,
    PipelineFailure,
    SchemaValidationError,
)
from conjured.ir.channel_types import (
    FieldDecl,
    ValidatorSpec,
    dict_of,
    list_of,
    primitive,
)
from conjured.ir.common import (
    Binding,
    InlineBindingValue,
    MergeStrategy,
    SchemaBinding,
    ServiceBindingDecl,
    ServiceBindingSupply,
)
from conjured.ir.deployment import (
    DeploymentDeclaration,
    HookTransportBlock,
    TrainingContract,
    TransportBlock,
)
from conjured.ir.handler import (
    HookDeclaration,
    ServiceDeclaration,
    TransformDeclaration,
)
from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.runner.assemble import assemble
from conjured.runner.run import RunResult, restore_after_dispatch, run
from conjured.validator import DeclarationRegistry, loads
from conjured.validator.compile import compile_pipeline

RUN_ID_FORM = re.compile(r"run_\d{8}T\d{6}Z_[0-9a-f]{4}")


@pytest.fixture
def module_dir(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, name: str, source: str) -> None:
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    importlib.invalidate_caches()


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


def _transform(reads, outputs, bindings=()):
    return TransformDeclaration(
        reads=tuple(reads), output_schema=tuple(outputs), bindings=tuple(bindings)
    )


def _runnable(pipeline, registry, *, name, deployment=None):
    graph = compile_pipeline(
        pipeline, registry, pipeline_name=name, deployment=deployment, file_path="p.toml"
    )
    return assemble(graph, registry, deployment)


def _chain(module_dir, mod_name="run_chain_mod"):
    """The standard two-transform chain: text -> mid -> out."""
    _write_module(
        module_dir, mod_name,
        """
        def first(*, text):
            return {"mid": text.upper()}

        def second(*, mid):
            return {"out": mid + "!"}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        f"{mod_name}.first", _transform((_fd("text"),), (_fd("mid"),)),
        toml_path="handlers/first.toml",
    )
    reg.add_handler(
        f"{mod_name}.second", _transform((_fd("mid"),), (_fd("out"),)),
        toml_path="handlers/second.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.chain"),
        nodes=(HandlerNode(name=f"{mod_name}.first"), HandlerNode(name=f"{mod_name}.second")),
        inputs=(_fd("text"),),
        outputs=(_fd("out"),),
    )
    return _runnable(pipeline, reg, name="acme.chain")


# ---------------------------------------------------------------------------
# 13. Happy multi-node chain — order, threading, the RunResult shape (plain-dict state)
# ---------------------------------------------------------------------------


def test_happy_chain_threads_channels_in_declared_order(module_dir):
    runnable = _chain(module_dir)
    result = run(runnable, {"text": "hi"})
    assert isinstance(result, RunResult)
    assert dict(result.state) == {"mid": "HI", "out": "HI!"}
    # state is a PLAIN dict over OUTER WRITTEN channels only — the RunResult.state freeze was
    # dropped (the engine's promise is kept at return; freezing the consumer's mapping is
    # paternalism outside the §8 ownership boundary, and the old top-level MappingProxyType was
    # a partial guarantee anyway — nested values stayed mutable). Defends against the freeze
    # being re-introduced: RED if `state` is re-wrapped (a MappingProxyType is not a `dict`).
    assert isinstance(result.state, dict) and not isinstance(result.state, MappingProxyType)
    result.state["out"] = "overwritten"  # a plain dict: writable, no TypeError
    assert "text" not in result.state  # input channels are not graph-written
    # run_id: engine-minted in the structured sortable form...
    assert RUN_ID_FORM.fullmatch(result.run_id), result.run_id


def test_consumer_supplied_run_id_returned_verbatim(module_dir):
    runnable = _chain(module_dir, mod_name="run_chain_id_mod")
    result = run(runnable, {"text": "hi"}, pipeline_run_id="session-42/turn-3")
    assert result.run_id == "session-42/turn-3"


def test_per_run_channel_state_is_fresh_each_invocation(module_dir):
    runnable = _chain(module_dir, mod_name="run_chain_fresh_mod")
    assert dict(run(runnable, {"text": "a"}).state) == {"mid": "A", "out": "A!"}
    assert dict(run(runnable, {"text": "b"}).state) == {"mid": "B", "out": "B!"}


# ---------------------------------------------------------------------------
# 13b. Canonical event emission — the handler_enter/handler_exit pair (Phase 4)
# ---------------------------------------------------------------------------


def _capture_events():
    """Attach a consumer handler to the canonical event channel ``conjured.events.runner``
    (the engine ships none — producer/consumer). Returns ``(captured_list, detach)``."""
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


def test_emits_handler_enter_exit_pair_per_dispatch(module_dir):
    """The training record IS the handler_enter/handler_exit pair (the
    pipeline-as-training-contract collapse): reads-snapshot in, writes-snapshot out, one
    pair per dispatch, position-ordered, sharing the run id."""
    from conjured import events as E

    runnable = _chain(module_dir, mod_name="run_events_mod")
    captured, detach = _capture_events()
    try:
        result = run(runnable, {"text": "hi"})
    finally:
        detach()

    enters = [e for e in captured if isinstance(e, E.HandlerEnter)]
    exits = [e for e in captured if isinstance(e, E.HandlerExit)]
    # one pair per node, emitted in dispatch (position) order, sharing the run id
    assert [e.handler_position for e in enters] == [0, 1]
    assert [e.handler_position for e in exits] == [0, 1]
    assert all(e.pipeline_run_id == result.run_id for e in enters + exits)
    assert all(e.node_kind == "transform" for e in enters + exits)
    assert enters[0].handler_qualified_name == "run_events_mod.first"

    # the pair carries the captured projections — reads in, validated writes out
    enter, exit_ = {e.handler_position: e for e in enters}, {e.handler_position: e for e in exits}
    assert enter[0].reads_snapshot == {"text": "hi"}
    assert exit_[0].writes_snapshot == {"mid": "HI"}
    assert enter[1].reads_snapshot == {"mid": "HI"}
    assert exit_[1].writes_snapshot == {"out": "HI!"}

    # non-service kinds carry no correlation_id; elapsed is a non-negative int
    assert all(e.correlation_id is None for e in exits)
    assert all(isinstance(e.elapsed_ms, int) and e.elapsed_ms >= 0 for e in exits)


# verifies: event-payload-deepcopy
def test_retained_handler_enter_survives_in_place_mutation_of_shared_reads(module_dir):
    """Ruling 1 end-to-end: the runner SHARES the per-dispatch reads projection with the handler
    body (run.py), and an in-place mutation of a reads value is SANCTIONED fail-soft behavior
    (trust-model § Vector 4). A handler that appends to its reads list must NOT rewrite the
    retained handler_enter.reads_snapshot — the training-pair input side whose silent corruption
    is exactly what the engine exists to prevent (I4). RED if the HandlerEnter dataclass stops
    deep-copying its payload (the event would alias the mutated projection)."""
    from conjured import events as E

    _write_module(
        module_dir, "run_mutating_reads_mod",
        """
        def masher(*, items):
            items.append("MUTATED")   # a legal in-place mutation of the shared reads projection
            return {"out": "|".join(items)}
        """,
    )
    items_fd = FieldDecl(name="items", type=list_of(primitive("str")))
    out_fd = _fd("out")
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_mutating_reads_mod.masher", _transform((items_fd,), (out_fd,)),
        toml_path="handlers/masher.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.masher"),
        nodes=(HandlerNode(name="run_mutating_reads_mod.masher"),),
        inputs=(items_fd,),
        outputs=(out_fd,),
    )
    runnable = _runnable(pipeline, reg, name="acme.masher")
    captured, detach = _capture_events()
    try:
        result = run(runnable, {"items": ["a", "b"]})
    finally:
        detach()
    # the body ran and SAW its mutation (the output folds in the appended value)
    assert dict(result.state) == {"out": "a|b|MUTATED"}
    # ...but the retained handler_enter is the pre-mutation training-pair input, immune to it
    enters = [e for e in captured if isinstance(e, E.HandlerEnter)]
    assert len(enters) == 1
    assert enters[0].reads_snapshot == {"items": ["a", "b"]}


def test_no_consumer_means_no_emission_overhead(module_dir):
    """Producer/consumer: with no handler attached and the channel at its default level,
    ``logger.info`` is a fast no-op — the run produces its result and emits nothing
    observable (the engine ships no handlers; capture is a consumer opt-in)."""
    runnable = _chain(module_dir, mod_name="run_events_silent_mod")
    # No _capture_events(): the channel has the engine's default (no handler, level NOTSET).
    result = run(runnable, {"text": "hi"})
    assert dict(result.state) == {"mid": "HI", "out": "HI!"}


# ---------------------------------------------------------------------------
# 13b-bis. Sequential dispatch in declared order — the § Orchestration scope seal
# ---------------------------------------------------------------------------


# verifies: sequential-dispatch-order
def test_dispatches_data_independent_nodes_in_declared_order(module_dir):
    """The § Orchestration scope seal "Sequential dispatch in declared order": the engine
    dispatches every node sequentially in DECLARED order, never reordered or overlapped. The
    adversary is three DATA-INDEPENDENT nodes (each reads only the shared seed, each writes its
    own channel) declared alpha->beta->gamma: nothing in the data forces an order, so the
    handler_enter event sequence pins the runner's declared-order commitment ALONE. RED if
    `SequentialTaskRunner.run_ordered` stopped consuming the ordered thunks in iteration order
    (reordered, sorted, or overlapped) — the happy-chain tests above cannot catch that because
    their data dependency (first->mid->second) forces the order regardless of the runner."""
    from conjured import events as E

    _write_module(
        module_dir, "run_order_mod",
        """
        def alpha(*, text):
            return {"a": text}

        def beta(*, text):
            return {"b": text}

        def gamma(*, text):
            return {"c": text}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_order_mod.alpha", _transform((_fd("text"),), (_fd("a"),)), toml_path="a.toml",
    )
    reg.add_handler(
        "run_order_mod.beta", _transform((_fd("text"),), (_fd("b"),)), toml_path="b.toml",
    )
    reg.add_handler(
        "run_order_mod.gamma", _transform((_fd("text"),), (_fd("c"),)), toml_path="c.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.order"),
        nodes=(
            HandlerNode(name="run_order_mod.alpha"),
            HandlerNode(name="run_order_mod.beta"),
            HandlerNode(name="run_order_mod.gamma"),
        ),
        inputs=(_fd("text"),),  # the one shared read; a/b/c are independent writes
    )
    runnable = _runnable(pipeline, reg, name="acme.order")
    captured, detach = _capture_events()
    try:
        result = run(runnable, {"text": "hi"})
    finally:
        detach()

    # handler_enter events in EMISSION (temporal) order ARE the dispatch sequence. With no data
    # dependency among the three nodes, only the runner's declared-order walk pins this order.
    enters = [e for e in captured if isinstance(e, E.HandlerEnter)]
    assert [e.handler_qualified_name for e in enters] == [
        "run_order_mod.alpha", "run_order_mod.beta", "run_order_mod.gamma",
    ]
    assert [e.handler_position for e in enters] == [0, 1, 2]
    # all three ran (each wrote its own channel) — the walk is exhaustive, not just ordered
    assert dict(result.state) == {"a": "hi", "b": "hi", "c": "hi"}


# ---------------------------------------------------------------------------
# 13c. The run-lifecycle events — pipeline_start / pipeline_complete / pipeline_error
# ---------------------------------------------------------------------------


def test_emits_pipeline_start_and_complete_around_the_walk(module_dir):
    """pipeline_start fires after seeding (inputs_snapshot = the seeded inputs), before the
    first dispatch; pipeline_complete fires at the happy return (outputs_snapshot = the
    DECLARED [outputs] projection only — `mid` is written but undeclared, so excluded). Both
    name the pipeline by its pipeline-hash; every event of the run shares that hash."""
    from conjured import events as E

    runnable = _chain(module_dir, mod_name="run_lifecycle_mod")
    captured, detach = _capture_events()
    try:
        result = run(runnable, {"text": "hi"})
    finally:
        detach()

    # ordering: start first, complete last, the enter/exit pairs between
    assert isinstance(captured[0], E.PipelineStart)
    assert isinstance(captured[-1], E.PipelineComplete)
    start = captured[0]
    complete = captured[-1]

    assert start.pipeline_run_id == result.run_id
    assert start.inputs_snapshot == {"text": "hi"}
    assert start.pipeline_hash == runnable.pipeline_hash
    assert runnable.pipeline_hash.startswith("sha256:")
    assert start.parent_run_id is None  # a top-level run has no parent

    assert complete.outputs_snapshot == {"out": "HI!"}  # `mid` is written but undeclared
    assert complete.pipeline_run_id == result.run_id
    assert isinstance(complete.elapsed_ms, int) and complete.elapsed_ms >= 0

    # every hash-bearing event of the run carries the one pipeline-hash
    assert {e.pipeline_hash for e in captured if hasattr(e, "pipeline_hash")} == {
        runnable.pipeline_hash
    }


def test_pipeline_complete_outputs_snapshot_empty_when_no_outputs_declared(module_dir):
    """A pipeline declaring no [outputs] emits `outputs_snapshot == {}` (absence, not the
    full channel state)."""
    from conjured import events as E

    _write_module(
        module_dir, "run_nooutputs_mod",
        """
        def only(*, text):
            return {"out": text.upper()}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_nooutputs_mod.only", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="h.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.noout"),
        nodes=(HandlerNode(name="run_nooutputs_mod.only"),),
        inputs=(_fd("text"),),  # no [outputs] block
    )
    runnable = _runnable(pipeline, reg, name="acme.noout")
    captured, detach = _capture_events()
    try:
        run(runnable, {"text": "hi"})
    finally:
        detach()
    [complete] = [e for e in captured if isinstance(e, E.PipelineComplete)]
    assert complete.outputs_snapshot == {}


def test_pipeline_error_on_a_wrapped_runtime_failure_names_the_failed_node(module_dir):
    """A halt fires pipeline_error naming the in-flight node; a PipelineFailure carries the
    underlying cause_class. A start fired (the run began); no complete (it halted)."""
    from conjured import events as E

    runnable = _service_runnable(module_dir, "RaisingValueError", "evt_pf")
    captured, detach = _capture_events()
    try:
        with pytest.raises(PipelineFailure):
            run(runnable, {"text": "hi"}, pipeline_run_id="pf-run")
    finally:
        detach()

    [err] = [e for e in captured if isinstance(e, E.PipelineError)]
    assert err.error_class == "PipelineFailure"
    assert err.cause_class == "ValueError"
    assert err.failed_handler_position == 0
    assert err.failed_handler_qualified_name == "run_svc_evt_pf_mod.call"
    assert err.pipeline_hash == runnable.pipeline_hash
    assert err.pipeline_run_id == "pf-run"
    assert err.error_message  # non-empty rendered message
    assert any(isinstance(e, E.PipelineStart) for e in captured)
    assert not any(isinstance(e, E.PipelineComplete) for e in captured)


def test_pipeline_error_on_a_contract_violation_carries_no_cause_class(module_dir):
    """A mid-dispatch ContractViolation (an undeclared output key) sets error_class =
    "ContractViolation" with cause_class None — CV/SVE are themselves the cause. The failed
    node is the runner's knowledge (CV carries no handler field)."""
    from conjured import events as E

    _write_module(
        module_dir, "run_evtcv_mod",
        """
        def bad(*, text):
            return {"out": text, "extra": 1}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_evtcv_mod.bad", _transform((_fd("text"),), (_fd("out"),)), toml_path="h.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.evtcv"),
        nodes=(HandlerNode(name="run_evtcv_mod.bad"),),
        inputs=(_fd("text"),), outputs=(_fd("out"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.evtcv")
    captured, detach = _capture_events()
    try:
        with pytest.raises(ContractViolation):
            run(runnable, {"text": "hi"})
    finally:
        detach()
    [err] = [e for e in captured if isinstance(e, E.PipelineError)]
    assert err.error_class == "ContractViolation"
    assert err.cause_class is None
    assert err.failed_handler_position == 0
    assert err.failed_handler_qualified_name == "run_evtcv_mod.bad"


def test_missing_inputs_fires_no_lifecycle_event_the_run_never_starts(module_dir):
    """The API-boundary missing-inputs ContractViolation raises BEFORE the run starts (no
    run_id minted), so it fires no pipeline_start AND no pipeline_error — mirroring a
    compose-time halt with no run in flight (hash-model § Canonical event types)."""
    runnable = _chain(module_dir, mod_name="run_evt_noinput_mod")
    captured, detach = _capture_events()
    try:
        with pytest.raises(ContractViolation):
            run(runnable, {})  # missing the declared input `text`
    finally:
        detach()
    assert captured == []  # nothing emitted — the run never began


# ---------------------------------------------------------------------------
# 13d. service_invocation — the adapter-boundary capture (service-kind only)
# ---------------------------------------------------------------------------


def _returning_service_runnable(module_dir, suffix, *, body):
    """A service pipeline whose adapter RETURNS a backend response dict (the existing
    `_service_runnable` adapters raise). `body` is the handler module source."""
    _write_module(module_dir, f"run_retsvc_{suffix}_mod", body)
    _write_module(
        module_dir, f"run_retsvc_{suffix}_adapters",
        """
        class EchoBackend:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"r": input_payload["q"].upper(), "raw": "backend-internal"}
        """,
    )
    type_name = f"run_retsvc_{suffix}_adapters.EchoBackend"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),), transport_schema=(),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        f"run_retsvc_{suffix}_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.retsvc"),
        nodes=(HandlerNode(name=f"run_retsvc_{suffix}_mod.call"),),
        service_bindings=(
            ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),
        ),
        inputs=(_fd("text"),), outputs=(_fd("out"),),
    )
    return _runnable(pipeline, reg, name="acme.retsvc")


def test_service_invocation_captures_the_adapter_boundary(module_dir):
    """service_invocation is captured at the adapter boundary: input_payload is what the
    body submitted; output_payload is the RAW backend response (before the body reshaped it
    into output_schema). The pair joins handler_exit via correlation_id, and the body's
    reshape shows as output_payload != writes_snapshot (the divergence signal)."""
    from conjured import events as E

    runnable = _returning_service_runnable(
        module_dir, "ok",
        body="""
        def call(*, text, services):
            return {"out": services.llm.invoke(q=text)["r"]}
        """,
    )
    captured, detach = _capture_events()
    try:
        result = run(runnable, {"text": "hi"})
    finally:
        detach()
    assert dict(result.state) == {"out": "HI"}

    [si] = [e for e in captured if isinstance(e, E.ServiceInvocation)]
    assert si.input_payload == {"q": "hi"}
    assert si.output_payload == {"r": "HI", "raw": "backend-internal"}
    assert si.handler_position == 0
    assert si.handler_qualified_name == "run_retsvc_ok_mod.call"
    assert si.pipeline_hash == runnable.pipeline_hash
    assert isinstance(si.elapsed_ms, int) and si.elapsed_ms >= 0

    # the service pair: correlation_id joins service_invocation to the same dispatch's
    # handler_exit — both the derived (run_id, position) label
    [exit_] = [
        e for e in captured if isinstance(e, E.HandlerExit) and e.node_kind == "service"
    ]
    assert si.correlation_id == exit_.correlation_id == f"{result.run_id}:0"
    # the body reshaped the backend response: output_payload (backend) != writes (handler)
    assert exit_.writes_snapshot == {"out": "HI"}
    assert si.output_payload != exit_.writes_snapshot


# verifies: service-payload-deepcopy
def test_service_invocation_payload_is_immune_to_body_mutation(module_dir):
    """The structural silent-fallback defense: the body mutates the backend response after
    invoke() returns, but the captured event holds the deep-copied actual response — the
    body has no path to rewrite what it reported to the event log."""
    from conjured import events as E

    runnable = _returning_service_runnable(
        module_dir, "mut",
        body="""
        def call(*, text, services):
            resp = services.llm.invoke(q=text)
            resp["r"] = "TAMPERED"        # mutate after the boundary captured it
            resp["raw"] = "tampered"
            return {"out": "clean"}
        """,
    )
    captured, detach = _capture_events()
    try:
        run(runnable, {"text": "hi"})
    finally:
        detach()
    [si] = [e for e in captured if isinstance(e, E.ServiceInvocation)]
    # the captured payload is the backend's ACTUAL response, not the body's tampered one
    assert si.output_payload == {"r": "HI", "raw": "backend-internal"}


# verifies: service-payload-deepcopy
def test_service_invocation_input_payload_is_immune_to_post_call_input_mutation(module_dir):
    """The INPUT-side twin of test_service_invocation_payload_is_immune_to_body_mutation
    (which covers only the output side): the body mutates a NESTED value it passed as an
    input kwarg AFTER invoke() returns, but the captured event holds the deep-copied input.
    A nested mutation is what distinguishes the DEEP copy from a bare reference or a shallow
    dict() copy — defending the dispatch event lying about what the backend was actually
    called with."""
    from conjured import events as E

    _write_module(
        module_dir, "run_retsvc_inmut_mod",
        """
        def call(*, text, services):
            live = {"items": [text]}                 # a nested mutable input the body retains
            resp = services.llm.invoke(payload=live)
            live["items"].append("TAMPERED-INPUT")   # mutate the live input AFTER the boundary captured it
            return {"out": resp["r"]}
        """,
    )
    _write_module(
        module_dir, "run_retsvc_inmut_adapters",
        """
        class TolerantBackend:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"r": "ok"}                   # ignores the input shape; fixed response
        """,
    )
    type_name = "run_retsvc_inmut_adapters.TolerantBackend"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),), transport_schema=(),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        "run_retsvc_inmut_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.inmut"),
        nodes=(HandlerNode(name="run_retsvc_inmut_mod.call"),),
        service_bindings=(
            ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),
        ),
        inputs=(_fd("text"),), outputs=(_fd("out"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.inmut")
    captured, detach = _capture_events()
    try:
        run(runnable, {"text": "hi"})
    finally:
        detach()
    [si] = [e for e in captured if isinstance(e, E.ServiceInvocation)]
    # the captured input_payload is the pre-mutation deep-copied snapshot — the body's later
    # append to the nested list did NOT leak into the event (a bare ref or a shallow dict()
    # copy would have captured ["hi", "TAMPERED-INPUT"]).
    assert si.input_payload == {"payload": {"items": ["hi"]}}


def test_backend_emission_hook_emits_no_service_invocation(module_dir):
    """A backend-SDK-emission hook reaches the same _BoundService, but a hook emits NO
    service_invocation (per-kind capture: a hook writes no channels). It still emits its
    handler_enter/handler_exit pair (writes_snapshot None)."""
    from conjured import events as E

    _write_module(
        module_dir, "run_hooknoSI_mod",
        """
        def producer(*, text):
            return {"out": text.upper()}

        def watch(*, out, services):
            services.emit.invoke(line=out)
        """,
    )
    _write_module(
        module_dir, "run_hooknoSI_adapters",
        """
        class EmitAdapter:
            def __init__(self, sink):
                self.sink = sink

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return None
        """,
    )
    type_name = "run_hooknoSI_adapters.EmitAdapter"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("sink"),), transport_schema=(),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        "run_hooknoSI_mod.producer", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/producer.toml",
    )
    reg.add_handler(
        "run_hooknoSI_mod.watch",
        HookDeclaration(
            reads=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="emit", type=type_name),),
        ),
        toml_path="handlers/watch.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.hooknosi"),
        nodes=(
            HandlerNode(name="run_hooknoSI_mod.producer"),
            HandlerNode(name="run_hooknoSI_mod.watch"),
        ),
        service_bindings=(
            ServiceBindingSupply(name="emit", type=type_name, identity={"sink": "s1"}),
        ),
        inputs=(_fd("text"),),
    )
    deployment = DeploymentDeclaration(
        transport=(TransportBlock(name="emit", values={}),),
        hook_transport=(HookTransportBlock(hook_qualified_name="run_hooknoSI_mod.watch"),),
        training_contract=TrainingContract(integrity_enforcement=False),
    )
    runnable = _runnable(pipeline, reg, name="acme.hooknosi", deployment=deployment)
    captured, detach = _capture_events()
    try:
        run(runnable, {"text": "hi"})
    finally:
        detach()
    # the hook's invoke ran (it returned None) but emitted NO service_invocation
    assert not any(isinstance(e, E.ServiceInvocation) for e in captured)
    # the hook still emits its enter/exit pair, exit carrying writes_snapshot None
    hook_exits = [
        e for e in captured if isinstance(e, E.HandlerExit) and e.node_kind == "hook"
    ]
    assert len(hook_exits) == 1 and hook_exits[0].writes_snapshot is None


# verifies: emit-consumer-isolated
def test_raising_consumer_handler_is_isolated_from_the_run(module_dir):
    """The producer/consumer wall (emit() owns it — the stdlib does not): a consumer
    event-handler that raises on every event MUST NOT enter the engine. A healthy run
    completes with the correct result, never laundered into a false PipelineFailure
    mis-attributed to an author handler, and the original cause is never masked."""
    from conjured import events as E

    runnable = _chain(module_dir, mod_name="run_consumer_raises_mod")

    class _Raises(logging.Handler):
        def emit(self, record):  # a naive consumer handler with no self-guard
            raise RuntimeError("consumer parsing bug on every event")

    handler = _Raises()
    lg = E.event_logger()
    lg.addHandler(handler)
    prev_level = lg.level
    lg.setLevel(logging.INFO)
    try:
        result = run(runnable, {"text": "hi"})  # must NOT raise
    finally:
        lg.removeHandler(handler)
        lg.setLevel(prev_level)
    # the run completed cleanly despite every emit's consumer handler raising
    assert dict(result.state) == {"mid": "HI", "out": "HI!"}


# verifies: emit-consumer-isolated
def test_raising_consumer_on_pipeline_error_does_not_mask_the_real_failure(module_dir):
    """The WORST-CASE arm of the producer/consumer wall: the sibling test above covers the
    normal (happy) path; this covers the `pipeline_error` (halting) arm — the one whose
    masking would hide the REAL failure. A consumer event-handler that raises DURING the
    pipeline_error emit must neither (a) halt the run with the consumer's own fault nor (b)
    mask the underlying failure. The run still raises the genuine PipelineFailure (cause_class
    'ValueError'), never the consumer's RuntimeError — defending the provenance the event log
    exists to provide."""
    from conjured import events as E

    runnable = _service_runnable(module_dir, "RaisingValueError", "evt_mask")

    class _RaisesOnError(logging.Handler):
        def emit(self, record):  # a naive consumer that blows up only on the error event
            if isinstance(record.msg, E.PipelineError):
                raise RuntimeError("consumer bug while logging the pipeline_error")

    handler = _RaisesOnError()
    lg = E.event_logger()
    lg.addHandler(handler)
    prev_level = lg.level
    lg.setLevel(logging.INFO)
    try:
        with pytest.raises(PipelineFailure) as exc:
            run(runnable, {"text": "hi"}, pipeline_run_id="mask-run")
    finally:
        lg.removeHandler(handler)
        lg.setLevel(prev_level)
    # the REAL failure surfaced — the consumer's RuntimeError neither replaced nor masked it
    assert exc.value.cause_class == "ValueError"
    assert not isinstance(exc.value, RuntimeError)


# verifies: emit-consumer-isolated
def test_raising_operational_handler_cannot_reenter_the_run(module_dir):
    """The INNER arm of the producer/consumer wall (emit() owns it). The two tests above
    exercise the OUTER guard: a consumer handler on the ``.runner`` event channel that raises
    is caught and surfaced as a WARNING on the parent ``conjured.events`` operational logger.
    That surfacing is itself wrapped in a SECOND try/except so that even a raising handler
    attached to the operational ``conjured.events`` logger cannot re-enter the run.

    The exact adversary: a raising handler on the ``.runner`` channel (trips the outer guard,
    firing the inner warning) AND a raising handler on the parent ``conjured.events`` logger
    (the inner warning's destination). A healthy run must STILL complete cleanly — the
    operational handler's raise is swallowed by the last-resort inner except, never laundered
    into a false PipelineFailure. RED if the inner try/except in emit() is removed (the
    operational handler's RuntimeError would then propagate out of emit into the walk)."""
    from conjured import events as E

    runnable = _chain(module_dir, mod_name="run_operational_raises_mod")

    class _Raises(logging.Handler):
        def __init__(self, label):
            super().__init__()
            self.label = label

        def emit(self, record):  # a naive handler with no self-guard, on either logger
            raise RuntimeError(f"{self.label} handler bug")

    # The consumer channel handler trips the OUTER guard; the operational-logger handler is
    # the adversary the INNER guard must contain.
    consumer = _Raises("consumer")
    operational = _Raises("operational")
    runner_lg = E.event_logger()  # conjured.events.runner
    operational_lg = logging.getLogger("conjured.events")  # the package parent
    runner_lg.addHandler(consumer)
    operational_lg.addHandler(operational)
    prev_runner_level = runner_lg.level
    prev_operational_level = operational_lg.level
    runner_lg.setLevel(logging.INFO)  # the consumer sees every event
    operational_lg.setLevel(logging.WARNING)  # the inner warning reaches the operational handler
    try:
        result = run(runnable, {"text": "hi"})  # must NOT raise
    finally:
        runner_lg.removeHandler(consumer)
        operational_lg.removeHandler(operational)
        runner_lg.setLevel(prev_runner_level)
        operational_lg.setLevel(prev_operational_level)
    # the run completed cleanly despite BOTH the consumer AND the operational handler raising
    assert dict(result.state) == {"mid": "HI", "out": "HI!"}


def test_pipeline_error_on_a_schema_validation_error(module_dir):
    """A reads-projection SchemaValidationError (a wrong-typed seed) is one of the three
    closed error classes that fire pipeline_error — error_class "SchemaValidationError",
    cause_class None. This halt fires for a node that emitted NO handler_enter (the SVE
    raises at validate-then-copy, before the enter emit), yet the failed node still resolves
    from the runner's own tracking."""
    from conjured import events as E

    _write_module(
        module_dir, "run_evtsve_mod",
        """
        def needs_int(*, n):
            return {"out": n + 1}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_evtsve_mod.needs_int",
        _transform((_fd("n", "int"),), (_fd("out", "int"),)), toml_path="h.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.evtsve"),
        nodes=(HandlerNode(name="run_evtsve_mod.needs_int"),),
        inputs=(_fd("n", "int"),), outputs=(_fd("out", "int"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.evtsve")
    captured, detach = _capture_events()
    try:
        with pytest.raises(SchemaValidationError):
            run(runnable, {"n": "not-an-int"})  # present (API ok) but wrong-typed
    finally:
        detach()
    [err] = [e for e in captured if isinstance(e, E.PipelineError)]
    assert err.error_class == "SchemaValidationError"
    assert err.cause_class is None
    assert err.failed_handler_position == 0
    assert err.failed_handler_qualified_name == "run_evtsve_mod.needs_int"
    # the SVE fired before the enter emit — no enter/exit for this node
    assert not any(isinstance(e, (E.HandlerEnter, E.HandlerExit)) for e in captured)


def test_pipeline_error_on_a_timeout(module_dir):
    """The cooperative timeout halts with a PipelineFailure (cause_class "TimeoutError")
    that flows through the same pipeline_error emit — naming the node at the boundary."""
    from conjured import events as E

    runnable = _chain(module_dir, mod_name="run_evttimeout_mod")
    captured, detach = _capture_events()
    try:
        with pytest.raises(PipelineFailure):
            run(runnable, {"text": "hi"}, timeout_ms=0)  # the first boundary already over budget
    finally:
        detach()
    [err] = [e for e in captured if isinstance(e, E.PipelineError)]
    assert err.error_class == "PipelineFailure"
    assert err.failure_category == "engine"  # a runner-wrapper run-guard, not a service/handler locus
    assert err.cause_class == "TimeoutError"
    assert err.failed_handler_position == 0
    assert not any(isinstance(e, E.PipelineComplete) for e in captured)


def test_service_invocation_correlation_id_distinct_under_multi_dispatch(module_dir):
    """The same service handler dispatched at two positions emits two service_invocation
    events whose correlation_ids are DISTINCT by position (#0 vs #1) — the property a
    consumer's multi-call-violation detector relies on (handler_qualified_name is non-key)."""
    from conjured import events as E

    _write_module(
        module_dir, "run_msvc_mod",
        """
        def call(*, text, services):
            return {"out": services.llm.invoke(q=text)["r"]}
        """,
    )
    _write_module(
        module_dir, "run_msvc_adapters",
        """
        class Echo:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"r": input_payload["q"].upper()}
        """,
    )
    type_name = "run_msvc_adapters.Echo"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),), transport_schema=(),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        "run_msvc_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.msvc"),
        nodes=(
            HandlerNode(name="run_msvc_mod.call", writes_map={"out": "o1"}),
            HandlerNode(name="run_msvc_mod.call", writes_map={"out": "o2"}),
        ),
        service_bindings=(
            ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),
        ),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.msvc")
    captured, detach = _capture_events()
    try:
        result = run(runnable, {"text": "hi"})
    finally:
        detach()
    sis = [e for e in captured if isinstance(e, E.ServiceInvocation)]
    assert [si.handler_position for si in sis] == [0, 1]
    assert [si.correlation_id for si in sis] == [
        f"{result.run_id}:0", f"{result.run_id}:1",
    ]
    assert len({si.correlation_id for si in sis}) == 2  # distinct by position


# ---------------------------------------------------------------------------
# 14. Reads-projection deep copy (the vector-4 read-side seal)
# ---------------------------------------------------------------------------


def test_reads_projection_is_deep_copied_per_dispatch(module_dir):
    _write_module(
        module_dir, "run_mutate_mod",
        """
        def mutate(*, items):
            items.append("smuggled")
            return {"count": len(items)}

        def observe(*, items):
            return {"seen": len(items)}
        """,
    )
    reg = DeclarationRegistry()
    items = FieldDecl(name="items", type=list_of(primitive("str")))
    reg.add_handler(
        "run_mutate_mod.mutate", _transform((items,), (_fd("count", "int"),)),
        toml_path="handlers/mutate.toml",
    )
    reg.add_handler(
        "run_mutate_mod.observe", _transform((items,), (_fd("seen", "int"),)),
        toml_path="handlers/observe.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.copy"),
        nodes=(HandlerNode(name="run_mutate_mod.mutate"), HandlerNode(name="run_mutate_mod.observe")),
        inputs=(items,),
    )
    runnable = _runnable(pipeline, reg, name="acme.copy")
    source = ["a"]
    result = run(runnable, {"items": source})
    # The first reader mutated ITS COPY (count == 2); the channel and the second
    # reader were untouched (seen == 1); the consumer's source list untouched too.
    assert dict(result.state) == {"count": 2, "seen": 1}
    assert source == ["a"]


# ---------------------------------------------------------------------------
# 15. Multi-dispatch: position is identity everywhere
# ---------------------------------------------------------------------------


def test_multi_dispatch_failure_attributes_to_the_failing_position(module_dir):
    _write_module(
        module_dir, "run_multi_mod",
        """
        def step(*, value):
            if value == "boom":
                raise RuntimeError("exploded at the second position")
            return {"next_value": value + "boom"}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_multi_mod.step", _transform((_fd("value"),), (_fd("next_value"),)),
        toml_path="handlers/step.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.multi"),
        nodes=(
            HandlerNode(name="run_multi_mod.step", writes_map={"next_value": "v1"}),
            HandlerNode(
                name="run_multi_mod.step",
                reads_map={"value": "v1"}, writes_map={"next_value": "v2"},
            ),
        ),
        inputs=(_fd("value"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.multi")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"value": ""})
    pf = exc.value
    # The SAME handler dispatched clean at position 0; the failure carries position
    # 1's full identity (qualified name is no longer unique within the run).
    assert pf.failed_handler_qualified_name == "run_multi_mod.step"
    assert pf.failed_handler_position == 1
    assert pf.composition_ref == "acme.multi[1]"  # the declaration-entry ordinal
    assert pf.reads_snapshot == {"value": "boom"}  # position 1's own projection


# ---------------------------------------------------------------------------
# 16. The merge strategies — fold-as-you-walk (D1) + the B1 micro-semantics
# ---------------------------------------------------------------------------


def _merge_runnable(module_dir, mod_name, channel_type, strategy, payloads, *, reader=False):
    """N `emit` writers of channel ``merged`` (payload via a per-node inline binding),
    optionally a `snap` reader between writers 1 and 2 recording the fold-so-far."""
    _write_module(
        module_dir, mod_name,
        """
        def emit(*, seed, payload):
            return {"item": payload}

        def snap(*, merged):
            return {"snapshot": list(merged)}
        """,
    )
    reg = DeclarationRegistry()
    emit_decl = TransformDeclaration(
        reads=(_fd("seed"),),
        output_schema=(FieldDecl(name="item", type=channel_type),),
        bindings=(
            Binding(
                name="payload",
                body=SchemaBinding(fields=(FieldDecl(name="value", type=channel_type),)),
            ),
        ),
    )
    reg.add_handler(f"{mod_name}.emit", emit_decl, toml_path="handlers/emit.toml")
    nodes = [
        HandlerNode(
            name=f"{mod_name}.emit",
            bindings=(InlineBindingValue(name="payload", value={"value": payload}),),
            writes_map={"item": "merged"},
        )
        for payload in payloads
    ]
    if reader:
        snap_decl = TransformDeclaration(
            reads=(FieldDecl(name="merged", type=channel_type),),
            output_schema=(FieldDecl(name="snapshot", type=channel_type),),
        )
        reg.add_handler(f"{mod_name}.snap", snap_decl, toml_path="handlers/snap.toml")
        nodes.insert(1, HandlerNode(name=f"{mod_name}.snap"))
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.merge"),
        nodes=tuple(nodes),
        merge={"merged": strategy},
        inputs=(_fd("seed"),),
    )
    return _runnable(pipeline, reg, name="acme.merge")


def test_append_list_reader_between_writers_sees_the_fold_so_far(module_dir):
    runnable = _merge_runnable(
        module_dir, "run_merge_append_mod", list_of(primitive("str")),
        MergeStrategy.APPEND_LIST, [["a"], ["b"]], reader=True,
    )
    result = run(runnable, {"seed": "s"})
    # D1: the reader composed between the two writers sees the fold over the writes
    # UPSTREAM of its position; the final value is the fold over ALL writers.
    assert result.state["snapshot"] == ["a"]
    assert result.state["merged"] == ["a", "b"]


def test_last_wins_final_write_in_declared_order(module_dir):
    runnable = _merge_runnable(
        module_dir, "run_merge_last_mod", primitive("str"),
        MergeStrategy.LAST_WINS, ["a", "b"],
    )
    assert run(runnable, {"seed": "s"}).state["merged"] == "b"


def test_first_wins_earliest_write_in_declared_order(module_dir):
    runnable = _merge_runnable(
        module_dir, "run_merge_first_mod", primitive("str"),
        MergeStrategy.FIRST_WINS, ["a", "b"],
    )
    assert run(runnable, {"seed": "s"}).state["merged"] == "a"


def test_concat_str_concatenates_in_declared_order(module_dir):
    runnable = _merge_runnable(
        module_dir, "run_merge_concat_mod", primitive("str"),
        MergeStrategy.CONCAT_STR, ["Hello, ", "world"],
    )
    assert run(runnable, {"seed": "s"}).state["merged"] == "Hello, world"


def test_deep_merge_dict_recurses_and_later_write_wins_conflicts(module_dir):
    runnable = _merge_runnable(
        module_dir, "run_merge_deep_mod", dict_of(dict_of(primitive("str"))),
        MergeStrategy.DEEP_MERGE_DICT,
        [
            {"npc": {"mood": "wary", "stance": "guarded"}},
            {"npc": {"mood": "warm"}, "scene": {"tone": "dusk"}},
        ],
    )
    # Recurse where both sides are dicts; the later write (declared order) wins the
    # inner conflict; non-conflicting keys from both sides survive (B1).
    assert run(runnable, {"seed": "s"}).state["merged"] == {
        "npc": {"mood": "warm", "stance": "guarded"},
        "scene": {"tone": "dusk"},
    }


def test_union_set_dedups_by_equality_preserving_first_occurrence(module_dir):
    runnable = _merge_runnable(
        module_dir, "run_merge_union_mod", list_of(dict_of(primitive("str"))),
        MergeStrategy.UNION_SET,
        [
            [{"id": "a"}, {"id": "b"}, {"id": "a"}],  # dup within one write dedups too
            [{"id": "b"}, {"id": "c"}],
        ],
    )
    # Equality-based (dict elements are unhashable — no hashability constraint),
    # first-occurrence order across writes in declared order (B1).
    assert run(runnable, {"seed": "s"}).state["merged"] == [
        {"id": "a"}, {"id": "b"}, {"id": "c"},
    ]


@pytest.mark.parametrize(
    ("tag", "payloads", "expected"),
    [
        ("override", ["", "v"], "v"),   # latest non-empty wins
        ("default", ["v", ""], "v"),    # an empty later write does not erase
        ("allempty", ["", ""], ""),     # all-empty degenerates to the last write
    ],
)
def test_last_present_wins_emptiness_and_degenerate_cases(module_dir, tag, payloads, expected):
    runnable = _merge_runnable(
        module_dir, f"run_merge_lpw_{tag}_mod", primitive("str"),
        MergeStrategy.LAST_PRESENT_WINS, payloads,
    )
    assert run(runnable, {"seed": "s"}).state["merged"] == expected


# ---------------------------------------------------------------------------
# 17 + 18. The API boundary: presence-only CV; extras inert (D2 / B4 / B5)
# ---------------------------------------------------------------------------


def _sentinel_runnable(module_dir, mod_name="run_sentinel_mod"):
    _write_module(
        module_dir, mod_name,
        """
        def sentinel(*, text):
            raise RuntimeError("dispatched — the API boundary did not hold")
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        f"{mod_name}.sentinel", _transform((_fd("text"),), (_fd("never"),)),
        toml_path="handlers/sentinel.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.boundary"),
        nodes=(HandlerNode(name=f"{mod_name}.sentinel"),),
        inputs=(_fd("text"),),
    )
    return _runnable(pipeline, reg, name="acme.boundary")


def test_missing_declared_input_is_boundary_cv_and_no_node_dispatches(module_dir):
    runnable = _sentinel_runnable(module_dir)
    with pytest.raises(ContractViolation) as exc:
        run(runnable, {"texxt": "hi"})  # typo'd key; declared field absent
    cv = exc.value
    # The exact structured class — a dispatch would have surfaced PipelineFailure
    # (the sentinel raises), so the CV itself proves no node dispatched.
    assert cv.check is Check.API_INPUTS_ENFORCEMENT
    assert cv.rule_id == "R-pipeline-001"
    assert cv.composition_ref == "acme.boundary"
    assert "text" in cv.actual and "texxt" in cv.actual  # names the unrecognized key
    assert cv.pipeline_run_id is None  # no consumer id supplied -> null (B5)


def test_boundary_cv_echoes_the_consumer_supplied_run_id(module_dir):
    runnable = _sentinel_runnable(module_dir, mod_name="run_sentinel_echo_mod")
    with pytest.raises(ContractViolation) as exc:
        run(runnable, {}, pipeline_run_id="consumer-7")
    assert exc.value.pipeline_run_id == "consumer-7"  # echoed verbatim (B5)


def test_undeclared_extras_are_inert_never_seeded_never_an_error(module_dir):
    runnable = _chain(module_dir, mod_name="run_chain_extras_mod")
    # Full declared coverage + an extra: the run SUCCEEDS (B4 — extras are not an
    # error) and the extra never becomes a channel (absent from state, unreadable).
    result = run(runnable, {"text": "hi", "extra": object()})
    assert dict(result.state) == {"mid": "HI", "out": "HI!"}
    assert "extra" not in result.state


def test_wrong_shaped_declared_input_surfaces_at_the_first_reader_sve(module_dir):
    runnable = _chain(module_dir, mod_name="run_chain_shape_mod")
    # D2 option A: the boundary checks presence only; value shape surfaces as the
    # first reading node's reads-projection SchemaValidationError.
    with pytest.raises(SchemaValidationError) as exc:
        run(runnable, {"text": 123})
    sve = exc.value
    assert sve.audit_code == INPUT_VALIDATION_AUDIT_CODE
    assert sve.handler_qualified_name == "run_chain_shape_mod.first"
    assert sve.handler_position == 0
    assert sve.field_validations[0].field_path == "reads.text"


# ---------------------------------------------------------------------------
# 20. The PipelineFailure wrap — exact payload at the dispatch boundary
# ---------------------------------------------------------------------------


# verifies: failure-category-engine-is-binding-delivery
def test_binding_delivery_failure_is_engine_locus_not_the_author_body(module_dir):
    """Fix 2 (`binding-delivery-engine-locus`): a non-deep-copyable COPY-mode binding's
    delivery failure is the ENGINE failure_category locus — binding delivery is the runner's
    OWN machinery (error-channel/reference.md § failure_category: ``"engine"`` covers "binding
    delivery, channel routing, merge"), never the author body. The deepcopy failure escapes
    ``node.dispatch`` via the ``_BindingDeliveryError`` carrier; without that carrier branch
    in ``_wrap`` the runner's generic dispatch-boundary ``except`` mis-attributes it by
    ``node_kind`` to the author's ``handler`` body — failure_category="handler" + a wrong
    blame label that becomes training data. RED if the carrier or its engine branch is
    removed. ``service_binding_name`` is null (the engine locus has no failing SERVICE
    binding — a ``bindings.<name>`` value is not a service binding)."""
    _write_module(
        module_dir, "run_bind_deliver_mod",
        """
        def boom(*, text, cfg):
            return {"out": text}  # never reached — delivery raises before the body runs
        """,
    )

    class _UncopyableStr(str):
        # A value valid as a `str` binding field, but non-deep-copyable: copy.deepcopy
        # calls __deepcopy__, which raises — the engine's COPY-mode delivery deepcopy fails.
        def __deepcopy__(self, memo):
            raise TypeError("this binding value is not deep-copyable")

    reg = DeclarationRegistry()
    decl = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(Binding(name="cfg", body=SchemaBinding(fields=(_fd("marker"),))),),
    )
    reg.add_handler("run_bind_deliver_mod.boom", decl, toml_path="handlers/boom.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.bind_deliver"),
        nodes=(
            HandlerNode(
                name="run_bind_deliver_mod.boom",
                bindings=(InlineBindingValue(name="cfg", value={"marker": _UncopyableStr("x")}),),
            ),
        ),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.bind_deliver")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"})
    pf = exc.value
    assert pf.failure_category == "engine"   # binding delivery is engine machinery, NOT the body
    assert pf.service_binding_name is None    # engine locus -> no failing service binding
    assert pf.cause_class == "TypeError"      # the raw deepcopy failure, unwrapped from the carrier
    assert pf.failed_handler_qualified_name == "run_bind_deliver_mod.boom"
    assert pf.failed_handler_position == 0


# verifies: failure-category-engine-is-channel-routing
def test_readside_channel_routing_deepcopy_of_a_node_output_is_engine_locus(module_dir):
    """The read-side sibling of the binding-delivery engine-locus fix: the vector-4
    reads-projection deep copy is channel routing — the engine's OWN runner machinery
    (error-channel/reference.md § failure_category: ``"engine"`` covers "binding delivery,
    channel routing, merge"). A schema-VALID-but-non-deep-copyable channel value (a ``str``
    subclass whose ``__deepcopy__`` raises — it clears strict validation against a declared
    ``str`` port, the closed-type-leaf invariant's one escape) makes a DOWNSTREAM reader's
    reads-projection deep copy raise BEFORE that reader's body runs. Without the engine wrap
    the failure falls through to the generic dispatch-boundary ``except`` and is
    mis-attributed by ``node_kind`` to the reader's ``handler`` body (whose code never ran) —
    a wrong blame label that becomes training data. RED if the engine wrap is removed.
    ``service_binding_name`` is null (the engine locus has no failing SERVICE binding)."""
    _write_module(
        module_dir, "run_readside_out_mod",
        """
        class _UncopyableStr(str):
            # Valid as a `str` channel value, but non-deep-copyable: __deepcopy__ raises.
            def __deepcopy__(self, memo):
                raise TypeError("this channel value is not deep-copyable")

        def first(*, text):
            return {"mid": _UncopyableStr(text)}  # schema-valid str subclass into channel `mid`

        def second(*, mid):
            return {"out": mid}  # never reached — the read-projection deep copy of `mid` raises first
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler("run_readside_out_mod.first", _transform((_fd("text"),), (_fd("mid"),)),
                    toml_path="handlers/first.toml")
    reg.add_handler("run_readside_out_mod.second", _transform((_fd("mid"),), (_fd("out"),)),
                    toml_path="handlers/second.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.readside_out"),
        nodes=(HandlerNode(name="run_readside_out_mod.first"),
               HandlerNode(name="run_readside_out_mod.second")),
        inputs=(_fd("text"),), outputs=(_fd("out"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.readside_out")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"})
    pf = exc.value
    assert pf.failure_category == "engine"   # channel routing is engine machinery, NOT the body
    assert pf.service_binding_name is None    # engine locus -> no failing service binding
    assert pf.cause_class == "TypeError"      # the raw read-side deep-copy failure
    assert pf.failed_handler_qualified_name == "run_readside_out_mod.second"  # the reader; its body never ran
    assert pf.failed_handler_position == 1


# verifies: failure-category-engine-is-channel-routing
def test_readside_channel_routing_deepcopy_of_a_consumer_seed_is_engine_locus(module_dir):
    """The consumer-trust-boundary face of the same read-side seal: a schema-valid but
    non-deep-copyable CONSUMER SEED (a ``str`` subclass whose ``__deepcopy__`` raises) clears
    the reads-side validate-then-copy validation (D2 routes only a WRONG-TYPED seed to
    SchemaValidationError — this seed is the right type, so it validates), then the vector-4
    reads-projection deep copy raises. That copy is engine channel-routing machinery, so the
    failure is the ``engine`` locus, never the reading handler's body (which never ran). RED
    if the engine wrap is removed."""
    _write_module(
        module_dir, "run_readside_seed_mod",
        """
        def only(*, text):
            return {"out": text}  # never reached — the read-projection deep copy of the seed raises first
        """,
    )

    class _UncopyableStr(str):
        def __deepcopy__(self, memo):
            raise TypeError("this seed value is not deep-copyable")

    reg = DeclarationRegistry()
    reg.add_handler("run_readside_seed_mod.only", _transform((_fd("text"),), (_fd("out"),)),
                    toml_path="handlers/only.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.readside_seed"),
        nodes=(HandlerNode(name="run_readside_seed_mod.only"),),
        inputs=(_fd("text"),), outputs=(_fd("out"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.readside_seed")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": _UncopyableStr("hi")})
    pf = exc.value
    assert pf.failure_category == "engine"
    assert pf.service_binding_name is None
    assert pf.cause_class == "TypeError"
    assert pf.failed_handler_qualified_name == "run_readside_seed_mod.only"
    assert pf.failed_handler_position == 0


# verifies: failure-category-engine-is-channel-routing
def test_writeside_route_writes_merge_fold_failure_is_engine_locus(module_dir):
    """The WRITE-side sibling of the read-side engine-locus seals: the ``_route_writes``
    merge fold is channel routing — the engine's OWN runner machinery
    (error-channel/reference.md § failure_category: ``"engine"`` covers "binding delivery,
    channel routing, merge"). A schema-VALID-but-unfoldable channel value (a ``str`` subclass
    whose ``__add__`` raises — it clears strict validation against a declared ``str`` port)
    written into a merged channel makes the NEXT write's ``concat_str`` fold raise inside
    ``_route_writes``. That fold runs inside the dispatch wrap boundary with ``locus="engine"``,
    so the failure is the ``engine`` locus, never the writing handler's body (which already
    returned cleanly). RED if ``_route_writes``'s engine wrap is removed — the raw fold error
    would fall through to the generic dispatch ``except`` and be mis-attributed by ``node_kind``
    to the author's ``handler`` body. ``service_binding_name`` is null (no failing SERVICE
    binding). This is the write-side analogue the binding-delivery + read-side tests left
    untested."""
    _write_module(
        module_dir, "run_writeside_fold_mod",
        """
        class _UnconcatenableStr(str):
            # Valid as a `str` channel value, but its `__add__` raises — so the concat_str
            # merge fold (channel routing) raises when this value is folded with the next write.
            def __add__(self, other):
                raise TypeError("this channel value cannot be concatenated by the merge fold")

        def first(*, seed):
            return {"part": _UnconcatenableStr("a")}  # schema-valid str into the merged channel

        def second(*, seed):
            return {"part": "b"}  # routing folds `<first> + "b"` here -> __add__ raises
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler("run_writeside_fold_mod.first",
                    _transform((_fd("seed"),), (_fd("part"),)), toml_path="handlers/first.toml")
    reg.add_handler("run_writeside_fold_mod.second",
                    _transform((_fd("seed"),), (_fd("part"),)), toml_path="handlers/second.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.writeside_fold"),
        nodes=(
            HandlerNode(name="run_writeside_fold_mod.first", writes_map={"part": "merged"}),
            HandlerNode(name="run_writeside_fold_mod.second", writes_map={"part": "merged"}),
        ),
        merge={"merged": MergeStrategy.CONCAT_STR},
        inputs=(_fd("seed"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.writeside_fold")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"seed": "s"})
    pf = exc.value
    assert pf.failure_category == "engine"   # the merge fold is channel routing, NOT the body
    assert pf.service_binding_name is None    # engine locus -> no failing service binding
    assert pf.cause_class == "TypeError"      # the raw fold failure
    assert pf.failed_handler_qualified_name == "run_writeside_fold_mod.second"  # the folding write
    assert pf.failed_handler_position == 1


# verifies: failure-category-engine-is-capture
def test_capture_deepcopy_of_a_non_copyable_backend_response_is_engine_locus(module_dir):
    """The adapter-boundary CAPTURE sibling of the binding-delivery + channel-routing
    engine-locus seals: the ``service_invocation`` output-payload deep copy runs AFTER
    ``adapter.invoke`` already returned (the response is in hand), so it is the engine's OWN
    capture machinery (error-channel/reference.md § failure_category: ``"engine"`` is an internal
    runner operation), never the service backend (which succeeded) and never the author body. A
    backend that returns a schema-valid-but-non-deep-copyable response (a ``str`` subclass whose
    ``__deepcopy__`` raises) makes the OUTPUT capture deepcopy raise. Without the ``_CaptureError``
    carrier + its ``_wrap`` engine branch the failure escapes the body raw and the runner's generic
    dispatch-boundary ``except`` mis-attributes it by ``node_kind`` to the service node's ``else``
    arm — failure_category="handler" + a wrong blame label that becomes training data. RED if the
    carrier or its engine branch is removed. ``service_binding_name`` is null (the engine locus has
    no failing SERVICE binding — the backend call returned successfully)."""
    _write_module(
        module_dir, "run_capout_mod",
        """
        def call(*, text, services):
            # the capture deepcopy of the response raises inside invoke(), before this returns
            return {"out": services.llm.invoke(q=text)["r"]}
        """,
    )
    _write_module(
        module_dir, "run_capout_adapters",
        """
        class _UncopyableStr(str):
            # Valid as a `str` backend response value, but non-deep-copyable: the capture
            # deepcopy of the response calls __deepcopy__, which raises.
            def __deepcopy__(self, memo):
                raise TypeError("this backend response value is not deep-copyable")

        class EchoBackend:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"r": _UncopyableStr(input_payload["q"].upper())}
        """,
    )
    type_name = "run_capout_adapters.EchoBackend"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(name=type_name, identity_schema=(_fd("model"),), transport_schema=()),
        toml_path="st.toml",
    )
    reg.add_handler(
        "run_capout_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.capout"),
        nodes=(HandlerNode(name="run_capout_mod.call"),),
        service_bindings=(ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),),
        inputs=(_fd("text"),), outputs=(_fd("out"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.capout")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"})
    pf = exc.value
    assert pf.failure_category == "engine"   # capture is engine machinery, NOT the service handler body
    assert pf.service_binding_name is None    # engine locus -> no failing service binding
    assert pf.cause_class == "TypeError"      # the raw capture deepcopy failure, unwrapped from the carrier
    assert pf.failed_handler_qualified_name == "run_capout_mod.call"
    assert pf.failed_handler_position == 0


# verifies: failure-category-engine-is-capture
def test_capture_deepcopy_of_a_non_copyable_input_payload_is_engine_locus(module_dir):
    """The input-payload twin of the capture engine-locus seal: the ``service_invocation``
    input-payload deep copy is engine capture machinery too. A body that submits a
    schema-valid-but-non-deep-copyable domain kwarg (a ``str`` subclass whose ``__deepcopy__``
    raises) makes the INPUT capture deepcopy raise — still the ``engine`` locus, never the author
    body that constructed the value. RED if the carrier or its ``_wrap`` engine branch is removed
    (then the raw failure is mis-attributed by ``node_kind`` to the service node's ``handler``
    arm). Seals the input capture call site alongside the output one."""
    runnable = _returning_service_runnable(
        module_dir, "capin",
        body="""
        class _UncopyableStr(str):
            # Valid as a `str` domain kwarg, but non-deep-copyable: the input-payload capture
            # deepcopy calls __deepcopy__, which raises.
            def __deepcopy__(self, memo):
                raise TypeError("this input payload value is not deep-copyable")

        def call(*, text, services):
            # the INPUT capture deepcopy of {"q": <uncopyable>} raises inside invoke()
            return {"out": services.llm.invoke(q=_UncopyableStr(text))["r"]}
        """,
    )
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"})
    pf = exc.value
    assert pf.failure_category == "engine"
    assert pf.service_binding_name is None
    assert pf.cause_class == "TypeError"
    assert pf.failed_handler_qualified_name == "run_retsvc_capin_mod.call"
    assert pf.failed_handler_position == 0


def test_transform_body_raise_wraps_to_pf_with_the_full_payload(module_dir):
    _write_module(
        module_dir, "run_boom_mod",
        """
        def boom(*, text, cfg):
            raise RuntimeError("kaboom")
        """,
    )
    reg = DeclarationRegistry()
    # A MULTI-field binding (delivered as a mutable dict) so the snapshot deep-copy
    # distinctness below stays observable — a single-field binding would deliver a bare
    # immutable scalar, whose deepcopy is identity (nothing to copy), making the
    # `is not` check vacuous. The snapshot-copy seal is over container values.
    decl = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(Binding(name="cfg", body=SchemaBinding(fields=(_fd("marker"), _fd("style")))),),
    )
    reg.add_handler("run_boom_mod.boom", decl, toml_path="handlers/boom.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.boom"),
        nodes=(
            HandlerNode(
                name="run_boom_mod.boom",
                bindings=(InlineBindingValue(
                    name="cfg", value={"marker": "brackets", "style": "round"}),),
            ),
        ),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.boom")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"})
    pf = exc.value
    assert pf.cause_class == "RuntimeError"
    assert pf.cause_message == "kaboom"
    assert pf.failed_handler_qualified_name == "run_boom_mod.boom"
    assert pf.failed_handler_position == 0
    assert pf.composition_ref == "acme.boom[0]"
    assert pf.pipeline_run_id and RUN_ID_FORM.fullmatch(pf.pipeline_run_id)
    assert isinstance(pf.elapsed_ms_at_failure, int) and pf.elapsed_ms_at_failure >= 0
    assert pf.service_binding_name is None  # no service binding on a transform
    # The snapshots: equal to the live values, never the same objects (deep copies).
    node = runnable.nodes[0]
    assert pf.bindings_snapshot == {"cfg": {"marker": "brackets", "style": "round"}}
    assert pf.bindings_snapshot is not node.bindings_values
    assert pf.bindings_snapshot["cfg"] is not node.bindings_values["cfg"]
    assert pf.reads_snapshot == {"text": "hi"}
    assert isinstance(exc.value.__cause__, RuntimeError)  # the chain is preserved


# ---------------------------------------------------------------------------
# 21. service_binding_name — the well-known cause_class table
# ---------------------------------------------------------------------------


def _service_runnable(module_dir, adapter_class: str, mod_suffix: str):
    _write_module(
        module_dir, f"run_svc_{mod_suffix}_mod",
        """
        def call(*, text, services):
            return {"out": services.llm.invoke(q=text)["r"]}
        """,
    )
    _write_module(
        module_dir, f"run_svc_{mod_suffix}_adapters",
        """
        class ServiceError(Exception):
            pass


        class RaisingServiceError:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                raise ServiceError("backend down")


        class RaisingValueError:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                raise ValueError("not a service-shaped fault")
        """,
    )
    type_name = f"run_svc_{mod_suffix}_adapters.{adapter_class}"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),), transport_schema=(),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        f"run_svc_{mod_suffix}_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),),
            output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.svc"),
        nodes=(HandlerNode(name=f"run_svc_{mod_suffix}_mod.call"),),
        service_bindings=(
            ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),
        ),
        inputs=(_fd("text"),),
    )
    return _runnable(pipeline, reg, name="acme.svc")


def test_service_error_cause_carries_the_binding_name(module_dir):
    runnable = _service_runnable(module_dir, "RaisingServiceError", "err")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"})
    pf = exc.value
    assert pf.failure_category == "service"  # raised inside the adapter -> service locus
    assert pf.cause_class == "ServiceError"
    assert pf.service_binding_name == "llm"  # service locus -> the failing binding is named


# verifies: failure-category-service-is-adapter-origin
def test_adapter_value_error_is_service_origin_with_binding(module_dir):
    # A non-service-SHAPED exception (a plain ValueError) raised from INSIDE the service adapter's
    # invoke() is still a SERVICE-locus failure: the runner attributes it from where it escaped (the
    # adapter boundary, via the _ServiceOriginError carrier), NOT from the exception name. Remove the
    # carrier tag and this becomes failure_category="handler" + null binding -> RED.
    runnable = _service_runnable(module_dir, "RaisingValueError", "val")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"})
    pf = exc.value
    assert pf.failure_category == "service"
    assert pf.service_binding_name == "llm"
    assert pf.cause_class == "ValueError"  # the underlying exception name, verbatim


# verifies: no-engine-retry
def test_no_engine_retry_surface_in_the_runner():
    """R-error-channel-002: the engine carries no retry primitive at the dispatch level — a service
    fault halts, never retries (error-channel/reference.md § halt-semantics; the guarantee is enforced
    by ABSENCE of API). This asserts that absence structurally: (a) ``run()``'s public signature exposes
    no retry parameter, and (b) the runner source (run / executor / dispatch) carries no retry-surface
    token (automating the conformance doc's ``grep`` verification). RED-on-removal: adding a retry
    parameter or a named retry loop turns this red. The token-grep alone is weak (a differently-named
    loop evades it), so it is paired with the signature guard; ``SequentialTaskRunner.run_ordered``
    calling each task exactly once is the structural guarantee this protects."""
    import importlib
    import inspect
    from pathlib import Path

    # importlib.import_module returns the real submodule; `import conjured.runner.run as x` would bind
    # x to the `run` FUNCTION the package re-exports under the same attribute name (submodule shadow).
    run_mod = importlib.import_module("conjured.runner.run")
    executor_mod = importlib.import_module("conjured.runner.executor")
    dispatch_mod = importlib.import_module("conjured.runner.dispatch")

    forbidden_params = {"retry", "retries", "max_retries", "retry_count", "retry_policy", "max_attempts"}
    run_params = set(inspect.signature(run_mod.run).parameters)
    assert not (run_params & forbidden_params), \
        f"run() exposes a retry parameter: {sorted(run_params & forbidden_params)}"

    tokens = ("max_retries", "retry_wrapper", "retry_count", "max_attempts")
    for mod in (run_mod, executor_mod, dispatch_mod):
        src = Path(inspect.getsourcefile(mod)).read_text(encoding="utf-8")
        hits = [t for t in tokens if t in src]
        assert not hits, f"{mod.__name__} carries a retry-surface token {hits} — no engine retry primitive may exist"


def test_service_node_body_fault_is_handler_locus_not_the_backend(module_dir):
    """The dual of ``test_adapter_value_error_is_service_origin_with_binding`` and the
    seal for the correct-by-construction property: a SERVICE node whose own BODY raises
    (its author code, NOT the ``services.<name>.invoke`` backend call) is the HANDLER
    locus, never service. ``failure_category`` is set from WHERE the failure escaped — an
    author body carries no ``_ServiceOriginError`` carrier, so it falls to the else-branch
    → ``"handler"`` with a null ``service_binding_name`` (the backend was never reached, so
    there is no failing SERVICE binding), regardless of ``node_kind == "service"``
    (error-channel/reference.md § failure_category ``"handler"``: "a service handler's own
    body code, including code around its invoke call"). RED if the attribution starts
    keying ``"service"`` off ``node_kind`` instead of the ``_ServiceOriginError`` carrier —
    the locus would flip and the constructor's service-binding-iff-service seal would then
    demand a binding the engine has no basis to name."""
    _write_module(
        module_dir, "run_svc_body_mod",
        """
        def call(*, text, services):
            # The author body faults BEFORE the backend call — never reaches services.llm.invoke.
            raise RuntimeError("handler body fault, not the backend")
        """,
    )
    _write_module(
        module_dir, "run_svc_body_adapters",
        """
        class NeverInvoked:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                raise AssertionError("the backend must not be reached — the body raises first")
        """,
    )
    type_name = "run_svc_body_adapters.NeverInvoked"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),), transport_schema=(),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        "run_svc_body_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),),
            output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.svc_body"),
        nodes=(HandlerNode(name="run_svc_body_mod.call"),),
        service_bindings=(
            ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),
        ),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.svc_body")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"})
    pf = exc.value
    assert pf.failure_category == "handler"  # the author body raised, NOT the backend
    assert pf.service_binding_name is None    # handler locus -> no failing service binding
    assert pf.cause_class == "RuntimeError"
    assert pf.failed_handler_qualified_name == "run_svc_body_mod.call"


# ---------------------------------------------------------------------------
# 22. FieldValidatorFailure → PF cause_class (the N1 obligation)
# ---------------------------------------------------------------------------


def test_raising_validator_wraps_with_the_underlying_cause_class(module_dir):
    _write_module(
        module_dir, "run_fvf_mod",
        """
        def emit(*, text):
            return {"label": "calm"}
        """,
    )
    # A raising third-party validator — the validator's OWN failure, carried as
    # FieldValidatorFailure.__cause__. (A built-in can no longer host the raise: the
    # applicability check rejects a mistyped built-in at compose.)
    _write_module(
        module_dir, "run_fvf_validators",
        """
        def explode(*, value):
            raise TypeError("validator exploded")
        """,
    )
    reg = DeclarationRegistry()
    out = FieldDecl(
        name="label", type=primitive("str"),
        validators=(ValidatorSpec(name="run_fvf_validators.explode"),),
    )
    reg.add_handler(
        "run_fvf_mod.emit", _transform((_fd("text"),), (out,)),
        toml_path="handlers/emit.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.fvf"),
        nodes=(HandlerNode(name="run_fvf_mod.emit"),),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.fvf")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "x"})
    assert exc.value.cause_class == "TypeError"  # the UNDERLYING class, not the carrier


def test_verdict_protocol_break_wraps_as_field_validator_failure(module_dir):
    _write_module(
        module_dir, "run_proto_mod",
        """
        def emit(*, text):
            return {"label": "calm"}
        """,
    )
    _write_module(
        module_dir, "run_proto_validators",
        """
        def broken(*, value):
            return 42  # neither None nor str — breaks the closed verdict protocol
        """,
    )
    reg = DeclarationRegistry()
    out = FieldDecl(
        name="label", type=primitive("str"),
        validators=(ValidatorSpec(name="run_proto_validators.broken"),),
    )
    reg.add_handler(
        "run_proto_mod.emit", _transform((_fd("text"),), (out,)),
        toml_path="handlers/emit.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.proto"),
        nodes=(HandlerNode(name="run_proto_mod.emit"),),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.proto")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "x"})
    # A protocol break has no underlying exception — cause_class is the carrier (N1).
    assert exc.value.cause_class == "FieldValidatorFailure"


# ---------------------------------------------------------------------------
# 23. CV / SVE pass through unwrapped — halt, no RunResult, no fourth class
# ---------------------------------------------------------------------------


def _passthrough_runnable(module_dir, body: str, mod_name: str):
    _write_module(
        module_dir, mod_name,
        f"""
        def producer(*, text):
            {body}

        def sentinel(*, out):
            raise RuntimeError("downstream dispatched past a halt")
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        f"{mod_name}.producer", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/producer.toml",
    )
    reg.add_handler(
        f"{mod_name}.sentinel", _transform((_fd("out"),), (_fd("never"),)),
        toml_path="handlers/sentinel.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.halt"),
        nodes=(HandlerNode(name=f"{mod_name}.producer"), HandlerNode(name=f"{mod_name}.sentinel")),
        inputs=(_fd("text"),),
    )
    return _runnable(pipeline, reg, name="acme.halt")


def test_undeclared_output_key_cv_passes_through_unwrapped(module_dir):
    runnable = _passthrough_runnable(
        module_dir, 'return {"out": text, "smuggled": 1}', "run_cvpass_mod"
    )
    # The exact class proves both halves: not wrapped into PF, and the downstream
    # sentinel never dispatched (it would have surfaced as PF RuntimeError).
    with pytest.raises(ContractViolation) as exc:
        run(runnable, {"text": "hi"})
    assert exc.value.check is Check.UNDECLARED_OUTPUT_KEY
    assert exc.value.pipeline_run_id is not None  # the mid-dispatch form carries the run


def test_output_value_violation_sve_passes_through_unwrapped(module_dir):
    runnable = _passthrough_runnable(
        module_dir, 'return {"out": 123}', "run_svepass_mod"
    )
    with pytest.raises(SchemaValidationError) as exc:
        run(runnable, {"text": "hi"})
    assert exc.value.audit_code == OUTPUT_VALIDATION_AUDIT_CODE
    assert exc.value.handler_qualified_name == "run_svepass_mod.producer"


# ---------------------------------------------------------------------------
# 24. The hook wrapper — the engine-owned two-case sanction
# ---------------------------------------------------------------------------


def _hook_runnable(module_dir, mod_name, hook_decl, hook_fn_source):
    _write_module(
        module_dir, mod_name,
        f"""
        def producer(*, text):
            return {{"out": text.upper()}}

        {hook_fn_source}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        f"{mod_name}.producer", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/producer.toml",
    )
    reg.add_handler(f"{mod_name}.watch", hook_decl, toml_path="handlers/watch.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.hooks"),
        nodes=(HandlerNode(name=f"{mod_name}.producer"), HandlerNode(name=f"{mod_name}.watch")),
        inputs=(_fd("text"),),
    )
    return _runnable(pipeline, reg, name="acme.hooks")


def test_hook_operational_failure_is_absorbed_with_the_b3_warning(module_dir, caplog):
    runnable = _hook_runnable(
        module_dir, "run_hookabs_mod",
        HookDeclaration(reads=(_fd("out"),)),
        """
        def watch(*, out):
            raise RuntimeError("emit target unreachable")
        """,
    )
    with caplog.at_level(logging.WARNING, logger="conjured.runner"):
        result = run(runnable, {"text": "hi"}, pipeline_run_id="hook-run-1")
    # Absorbed: the run COMPLETED with channel integrity intact.
    assert dict(result.state) == {"out": "HI"}
    [record] = [r for r in caplog.records if "absorbed" in r.getMessage()]
    assert record.levelno == logging.WARNING and record.name == "conjured.runner"
    message = record.getMessage()
    # The B3 surface: cause_class, cause_message, hook qualified name + position, run id.
    assert "RuntimeError" in message
    assert "emit target unreachable" in message
    assert "run_hookabs_mod.watch" in message
    assert "position=1" in message
    assert "hook-run-1" in message


def test_hook_contract_violation_still_halts(module_dir):
    runnable = _hook_runnable(
        module_dir, "run_hookcv_mod",
        HookDeclaration(reads=(_fd("out"),)),
        """
        def watch(*, out):
            return {"oops": out}  # a hook returns None by contract
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        run(runnable, {"text": "hi"})
    assert exc.value.check is Check.HOOK_RETURN_NOT_NONE


def test_stdlib_hook_transport_values_delivered_to_the_body_as_kwargs(module_dir, tmp_path):
    """Delivery follows the emission boundary (handler/reference.md
    § transport_schema): a stdlib-emission hook's declared transport_schema fields are
    supplied by the deployment's hook_transport."<qn>" block and reach the hook BODY
    as kwargs — exactly like bindings, a FRESH per-dispatch copy (a body mutating its
    copy cannot leak into the next dispatch). The hook emits to a REAL tmp_path log
    (its stdlib-emission job), recording what it received."""
    _write_module(
        module_dir, "run_stdlibtx_mod",
        """
        def producer(*, text):
            return {"out": text.upper()}

        def watch(*, out, log_path, tags):
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{out}|{tags!r}\\n")
            tags.append(out)  # mutate the per-dispatch copy — must not persist
            return None
        """,
    )
    log_file = tmp_path / "audit.log"
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_stdlibtx_mod.producer", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/producer.toml",
    )
    reg.add_handler(
        "run_stdlibtx_mod.watch",
        HookDeclaration(
            reads=(_fd("out"),),
            transport_schema=(
                FieldDecl(name="log_path", type=primitive("str")),
                FieldDecl(name="tags", type=list_of(primitive("str"))),
            ),
        ),
        toml_path="handlers/watch.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.stdlibtx"),
        nodes=(
            HandlerNode(name="run_stdlibtx_mod.producer"),
            HandlerNode(name="run_stdlibtx_mod.watch"),
        ),
        inputs=(_fd("text"),),
    )
    deployment = DeploymentDeclaration(
        hook_transport=(
            HookTransportBlock(
                hook_qualified_name="run_stdlibtx_mod.watch",
                values={"log_path": str(log_file), "tags": ["audit"]},
            ),
        ),
        training_contract=TrainingContract(integrity_enforcement=False),
    )
    runnable = _runnable(pipeline, reg, name="acme.stdlibtx", deployment=deployment)
    run(runnable, {"text": "hi"})
    run(runnable, {"text": "yo"})
    # Both dispatches received the PRISTINE deployment-supplied values — the first
    # dispatch's mutation of its copy never reached the second (fresh per-dispatch
    # copy, like a binding).
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        "HI|['audit']",
        "YO|['audit']",
    ]


def test_hook_schema_validation_error_still_halts(module_dir):
    # An SVE out of the hook's own reads-projection (a graph-shape failure at the
    # hook position) halts — the operational tolerance is PF-only.
    hook_decl = HookDeclaration(
        reads=(
            FieldDecl(
                name="out", type=primitive("str"),
                validators=(ValidatorSpec(name="minLength", params={"limit": 50}),),
            ),
        ),
    )
    runnable = _hook_runnable(
        module_dir, "run_hooksve_mod", hook_decl,
        """
        def watch(*, out):
            return None
        """,
    )
    with pytest.raises(SchemaValidationError) as exc:
        run(runnable, {"text": "hi"})
    assert exc.value.audit_code == INPUT_VALIDATION_AUDIT_CODE
    assert exc.value.handler_qualified_name == "run_hooksve_mod.watch"


# ---------------------------------------------------------------------------
# 25. The vector-3 runtime layer (D3): revert-and-continue; restore-failure halts
# ---------------------------------------------------------------------------


def test_module_namespace_mutation_reverted_with_warning_and_run_continues(module_dir, caplog):
    _write_module(
        module_dir, "run_leaky_mod",
        """
        _FLAG = "initial"

        def leaky(*, text):
            global _FLAG, _ADDED
            _FLAG = text          # rebinds an existing module name
            _ADDED = [text]       # adds a new module name
            return {"out": text}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_leaky_mod.leaky", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/leaky.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.leaky"),
        nodes=(HandlerNode(name="run_leaky_mod.leaky"),),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.leaky")
    with caplog.at_level(logging.WARNING, logger="conjured.runner"):
        result = run(runnable, {"text": "hi"})
    # The run CONTINUED past the clean revert (D3) ...
    assert dict(result.state) == {"out": "hi"}
    # ... and the module dict is restored exactly:
    module = importlib.import_module("run_leaky_mod")
    assert module._FLAG == "initial"
    assert not hasattr(module, "_ADDED")
    [record] = [r for r in caplog.records if "reverted module-namespace" in r.getMessage()]
    message = record.getMessage()
    assert "run_leaky_mod.leaky" in message  # names the handler ...
    assert "_ADDED" in message and "_FLAG" in message  # ... and the reverted names


class _UndeletableNamespace(dict):
    """A pathological namespace whose deletions silently fail — the restore-failure
    path is structurally unreachable through a real module ``__dict__`` (rebinding a
    plain dict always verifies), so the halt arm is exercised at the restore helper's
    own contract."""

    def __delitem__(self, key):  # silently refuses — re-verification must catch it
        pass


def _failing_restore_kwargs():
    namespace = _UndeletableNamespace({"kept": 1, "ghost": 2})
    snapshot = {"kept": 1}  # "ghost" was added during dispatch; deletion will no-op
    return namespace, snapshot


# verifies: vector3-restore-reverify-halt
def test_restore_failure_halts_with_handler_pure_module_cv():
    namespace, snapshot = _failing_restore_kwargs()
    with pytest.raises(ContractViolation) as exc:
        restore_after_dispatch(
            namespace, snapshot,
            handler_qualified_name="acme.leaky", handler_position=3,
            run_id="run_2026-06-10T00:00:00Z_v3aa", composition_ref="acme.leaky[3]",
            halting=False,
        )
    cv = exc.value
    assert cv.check is Check.HANDLER_PURE_MODULE
    assert cv.rule_id == "R-handler-pure-module"
    assert cv.pipeline_run_id == "run_2026-06-10T00:00:00Z_v3aa"  # the mid-dispatch form
    assert cv.composition_ref == "acme.leaky[3]"


def test_restore_failure_on_an_already_halting_path_never_masks(caplog):
    namespace, snapshot = _failing_restore_kwargs()
    with caplog.at_level(logging.ERROR, logger="conjured.runner"):
        # No raise: the original halt error owns the exit; the failure logs at ERROR.
        restore_after_dispatch(
            namespace, snapshot,
            handler_qualified_name="acme.leaky", handler_position=3,
            run_id="run_2026-06-10T00:00:00Z_v3bb", composition_ref="acme.leaky[3]",
            halting=True,
        )
    [record] = [r for r in caplog.records if "restore FAILED" in r.getMessage()]
    assert record.levelno == logging.ERROR


# ---------------------------------------------------------------------------
# 26. The cooperative pipeline-level timeout (D4-i)
# ---------------------------------------------------------------------------


def _slow_then_sentinel(module_dir, mod_name="run_slow_mod"):
    _write_module(
        module_dir, mod_name,
        """
        import time

        def slow(*, text):
            time.sleep(0.05)
            return {"mid": text}

        def never(*, mid):
            raise RuntimeError("dispatched past an exhausted budget")
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        f"{mod_name}.slow", _transform((_fd("text"),), (_fd("mid"),)),
        toml_path="handlers/slow.toml",
    )
    reg.add_handler(
        f"{mod_name}.never", _transform((_fd("mid"),), (_fd("after"),)),
        toml_path="handlers/never.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.slow"),
        nodes=(HandlerNode(name=f"{mod_name}.slow"), HandlerNode(name=f"{mod_name}.never")),
        inputs=(_fd("text"),),
    )
    return _runnable(pipeline, reg, name="acme.slow")


def test_budget_exceeded_between_dispatches_halts_with_timeout_pf(module_dir):
    runnable = _slow_then_sentinel(module_dir)
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"}, timeout_ms=10)
    pf = exc.value
    # TimeoutError PF (the decided shape), attributed to the node AT the boundary —
    # the downstream sentinel never dispatched (it would have surfaced as a
    # RuntimeError-caused PF at the same position).
    assert pf.cause_class == "TimeoutError"
    assert pf.failed_handler_qualified_name == "run_slow_mod.never"
    assert pf.failed_handler_position == 1
    assert pf.service_binding_name is None  # pipeline-level: no failing binding
    assert isinstance(pf.elapsed_ms_at_failure, int)
    assert pf.elapsed_ms_at_failure >= 10


def test_timeout_zero_halts_before_any_dispatch(module_dir):
    runnable = _sentinel_runnable(module_dir, mod_name="run_sentinel_t0_mod")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"}, timeout_ms=0)
    pf = exc.value
    assert pf.cause_class == "TimeoutError"
    assert pf.failed_handler_position == 0  # attributed to node 0; nothing dispatched


def test_timeout_none_is_unenforced(module_dir):
    runnable = _chain(module_dir, mod_name="run_chain_notimeout_mod")
    result = run(runnable, {"text": "hi"}, timeout_ms=None)
    assert dict(result.state) == {"mid": "HI", "out": "HI!"}


# ---------------------------------------------------------------------------
# 27. Trainable composition end-to-end through a certified stub backend
# ---------------------------------------------------------------------------

SERVICE_TYPE_TRAIN = """
name = "run_tbackend_mod.StubTrainableBackend"
[identity_schema]
model = { type = "str" }
[transport_schema]
endpoint = { type = "str" }
[config_schema]
temperature = { type = "float" }
max_tokens = { type = "int" }
"""

OUTER_TRANSFORM = """
[transform]
[reads]
raw = { type = "str" }
[output_schema]
npc_state = { type = "str" }
user_message = { type = "str" }
"""

# The preprocessor's REFERENCED handler declaration (name-reference model). The composition's
# [[preprocessors]] entry resolves its ports + the `config` binding from this registered handler;
# the `run_tpp_mod` Python module (written per-test) is its implementation.
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

TRAINABLE_COMPOSITION = """
[meta]
kind = "trainable"
name = "dialogue_training"
[inputs]
npc_state = { type = "str" }
user_message = { type = "str" }
[outputs]
dialogue_response = { type = "str" }
[[preprocessors]]
kind = "handler"
name = "run_tpp_mod.assemble_prompt"
id   = "assemble_prompt"
reads_map = { context = "npc_state", utterance = "user_message" }
writes_map = { prompt = "formatted_prompt" }
[preprocessors.bindings]
config = { template = "T" }
[service_bindings.llm]
type = "run_tbackend_mod.StubTrainableBackend"
model = "test-model"
[trainable]
[trainable.config]
temperature = 0.7
max_tokens = 64
[trainable.service_bindings]
llm = { type = "run_tbackend_mod.StubTrainableBackend" }
[trainable.reads]
formatted_prompt = { type = "str" }
[trainable.output_schema]
dialogue_response = { type = "str" }
"""

TRAIN_PIPELINE = """
[meta]
name = "acme.train"
[[nodes]]
kind = "handler"
name = "run_tctx_mod.prep"
[[nodes]]
kind = "composition"
name = "trainables/dialogue.toml"
[inputs]
raw = { type = "str" }
[outputs]
dialogue_response = { type = "str" }
"""

TRAIN_DEPLOYMENT = """
[transport.llm]
endpoint = "https://llm.test/v1"
[training_contract]
integrity_enforcement = false
"""


def test_trainable_composition_end_to_end(module_dir):
    _write_module(
        module_dir, "run_tctx_mod",
        """
        def prep(*, raw):
            return {"npc_state": "calm", "user_message": raw}
        """,
    )
    _write_module(
        module_dir, "run_tpp_mod",
        """
        def assemble_prompt(*, context, utterance, config):
            return {"prompt": context + "|" + utterance + "|" + config}
        """,
    )
    _write_module(
        module_dir, "run_tbackend_mod",
        """
        class StubTrainableBackend:
            \"\"\"Certified stub AT the adapter seam: accepts the closed dispatch-kwargs
            + the declared [config_schema] kwargs + **transport_extra; its response
            routes through the same output validation a live backend's would, so a
            wrong-shaped emission fails exactly where the runtime would fail.\"\"\"

            training_artifact_contract = "gguf"
            reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

            def __init__(self, model, *, output_schema, schema_source):
                self.model = model
                self.output_schema = output_schema
                self.schema_source = schema_source

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, max_tokens, **transport_extra):
                assert transport_extra.get("endpoint"), "transport never arrived"
                return {
                    "dialogue_response": "reply:" + input_payload["formatted_prompt"]
                }
        """,
    )
    reg = DeclarationRegistry()
    reg.add_service_type(loads(SERVICE_TYPE_TRAIN, "service_type", file_path="st.toml"),
                         toml_path="st.toml")
    reg.add_handler("run_tctx_mod.prep", loads(OUTER_TRANSFORM, "handler", file_path="prep.toml"),
                    toml_path="handlers/prep.toml")
    reg.add_handler("run_tpp_mod.assemble_prompt", loads(PREPROC_FORMATTER, "handler", file_path="pp.toml"),
                    toml_path="handlers/pp.toml")
    reg.add_composition(
        "trainables/dialogue.toml",
        loads(TRAINABLE_COMPOSITION, "composition", file_path="trainables/dialogue.toml"),
    )
    pipeline = loads(TRAIN_PIPELINE, "pipeline", file_path="p.toml")
    deployment = loads(TRAIN_DEPLOYMENT, "deployment", file_path="d.toml")
    runnable = _runnable(pipeline, reg, name="acme.train", deployment=deployment)

    # The assembled shape: preprocessor + terminal trainable flattened after the
    # outer transform; the trainable has no author module (R-handler-010).
    assert [n.node_kind for n in runnable.nodes] == ["transform", "transform", "trainable"]
    # entry_ordinal is the DECLARATION-entry index: both flattened composition
    # members belong to declaration entry 1, while their dispatch positions run on.
    assert [n.entry_ordinal for n in runnable.nodes] == [0, 1, 1]
    assert [n.position for n in runnable.nodes] == [0, 1, 2]
    trainable_node = runnable.nodes[2]
    assert trainable_node.module is None
    assert trainable_node.service_binding_name == "llm"
    assert dict(trainable_node.bindings_values) == {"temperature": 0.7, "max_tokens": 64}

    result = run(runnable, {"raw": "hello"})
    # Composition boundary outputs reach outer state; scoped internals stay
    # encapsulated (the structural scoped marker, not name-parsing).
    assert dict(result.state) == {
        "npc_state": "calm",
        "user_message": "hello",
        "dialogue_response": "reply:calm|hello|T",
    }
    assert "dialogue_training.formatted_prompt" not in result.state
    assert "raw" not in result.state


def test_trainable_composition_emits_pair_and_no_service_invocation(module_dir):
    """The kind-keyed half of the per-kind capture contract (hash-model § per-kind capture):
    a trainable composition node's captured training record IS its handler_enter/handler_exit
    pair — no service_invocation fires (it dispatches engine-constructed, never through
    _BoundService). The trainable's handler_exit carries writes_snapshot (the training-pair
    output side) and correlation_id None (no service pair)."""
    from conjured import events as E

    _write_module(
        module_dir, "run_tctx_mod",
        """
        def prep(*, raw):
            return {"npc_state": "calm", "user_message": raw}
        """,
    )
    _write_module(
        module_dir, "run_tpp_mod",
        """
        def assemble_prompt(*, context, utterance, config):
            return {"prompt": context + "|" + utterance + "|" + config}
        """,
    )
    _write_module(
        module_dir, "run_tbackend_mod",
        """
        class StubTrainableBackend:
            training_artifact_contract = "gguf"
            reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

            def __init__(self, model, *, output_schema, schema_source):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, max_tokens, **transport_extra):
                return {"dialogue_response": "reply:" + input_payload["formatted_prompt"]}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_service_type(loads(SERVICE_TYPE_TRAIN, "service_type", file_path="st.toml"),
                         toml_path="st.toml")
    reg.add_handler("run_tctx_mod.prep", loads(OUTER_TRANSFORM, "handler", file_path="prep.toml"),
                    toml_path="handlers/prep.toml")
    reg.add_handler("run_tpp_mod.assemble_prompt", loads(PREPROC_FORMATTER, "handler", file_path="pp.toml"),
                    toml_path="handlers/pp.toml")
    reg.add_composition(
        "trainables/dialogue.toml",
        loads(TRAINABLE_COMPOSITION, "composition", file_path="trainables/dialogue.toml"),
    )
    pipeline = loads(TRAIN_PIPELINE, "pipeline", file_path="p.toml")
    deployment = loads(TRAIN_DEPLOYMENT, "deployment", file_path="d.toml")
    runnable = _runnable(pipeline, reg, name="acme.train", deployment=deployment)

    captured, detach = _capture_events()
    try:
        run(runnable, {"raw": "hello"})
    finally:
        detach()

    # NO service_invocation for the trainable kind (its capture IS the enter/exit pair)
    assert not any(isinstance(e, E.ServiceInvocation) for e in captured)

    # the terminal trainable node (position 2) emitted its pair
    enters = {e.handler_position: e for e in captured if isinstance(e, E.HandlerEnter)}
    exits = {e.handler_position: e for e in captured if isinstance(e, E.HandlerExit)}
    assert enters[2].node_kind == "trainable"
    assert exits[2].node_kind == "trainable"
    # reads_snapshot in (training-pair input), writes_snapshot out (training-pair output)
    assert enters[2].reads_snapshot == {"formatted_prompt": "calm|hello|T"}
    assert exits[2].writes_snapshot == {"dialogue_response": "reply:calm|hello|T"}
    # correlation_id absent — there is no service_invocation to pair with
    assert exits[2].correlation_id is None


# ---------------------------------------------------------------------------
# 27a. Preprocessor mirrors an outer node at DISPATCH + VALIDATION (the mirror-fix
# halves beyond the hash fold): with the real referenced declaration flowing, a
# preprocessor binding honors `delivery` + value-validation identically to an outer node.
# ---------------------------------------------------------------------------

_STUB_BACKEND_SRC = """
    class StubBackend:
        training_artifact_contract = "gguf"
        reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

        def __init__(self, model, *, output_schema, schema_source):
            self.model = model

        def invoke(self, *, input_payload, service_name, caller_qualified_name,
                   caller_position, temperature, max_tokens, **transport_extra):
            return {"dialogue_response": "reply:" + input_payload["formatted_prompt"]}
"""


def _backend_service_type(class_path):
    return (f'name = "{class_path}"\n[identity_schema]\nmodel = {{ type = "str" }}\n'
            '[transport_schema]\nendpoint = { type = "str" }\n'
            '[config_schema]\ntemperature = { type = "float" }\nmax_tokens = { type = "int" }\n')


def _ref_pipeline(comp_path):
    return (f'[meta]\nname = "acme.ref"\n[[nodes]]\nkind = "handler"\nname = "run_refctx_mod.prep"\n'
            f'[[nodes]]\nkind = "composition"\nname = "{comp_path}"\n'
            '[inputs]\nraw = { type = "str" }\n[outputs]\ndialogue_response = { type = "str" }\n')


# verifies: preprocessor-mirrors-outer-node
def test_preprocessor_reference_delivery_is_honored_end_to_end(module_dir):
    """The DISPATCH half of the mirror: a preprocessor whose REFERENCED handler declares a
    `delivery = "reference"` binding receives the shared deep-frozen value at dispatch — exactly as
    an outer node does — not the per-dispatch COPY the old synthesized declaration forced. The
    handler reports the binding's runtime type; the reference contract delivers a MappingProxyType.
    RED on removal: the old model synthesized `delivery=COPY, fields=()`, so the binding arrived as
    a plain mutable dict (`type(table).__name__ == "dict"`)."""
    _write_module(module_dir, "run_refctx_mod",
                  "def prep(*, raw):\n    return {'npc_state': 'calm', 'user_message': raw}")
    _write_module(module_dir, "run_refpp_mod",
                  "def fmt(*, context, utterance, table):\n    return {'prompt': type(table).__name__}")
    _write_module(module_dir, "run_refbackend_mod", _STUB_BACKEND_SRC)
    reg = DeclarationRegistry()
    reg.add_service_type(loads(_backend_service_type("run_refbackend_mod.StubBackend"),
                               "service_type", file_path="st.toml"), toml_path="st.toml")
    reg.add_handler("run_refctx_mod.prep", loads(OUTER_TRANSFORM, "handler", file_path="prep.toml"),
                    toml_path="handlers/prep.toml")
    reg.add_handler("run_refpp_mod.fmt", loads(
        '[transform]\n[reads]\ncontext={type="str"}\nutterance={type="str"}\n[output_schema]\nprompt={type="str"}\n'
        # A MULTI-field reference binding so the deep-frozen delivery form is a MappingProxyType
        # (the reference-delivery seal the body observes); a single-field binding would deliver a
        # bare scalar, whose deep-freeze is identity and could not report `mappingproxy`.
        '[bindings.table]\ndelivery="reference"\nalias={type="str"}\ntitle={type="str"}\n',
        "handler", file_path="fmt.toml"),
        toml_path="handlers/fmt.toml")
    comp_toml = (
        '[meta]\nkind="trainable"\nname="ref_training"\n'
        '[inputs]\nnpc_state={type="str"}\nuser_message={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="run_refpp_mod.fmt"\nid="fmt"\n'
        'reads_map={context="npc_state",utterance="user_message"}\nwrites_map={prompt="formatted_prompt"}\n'
        '[preprocessors.bindings]\ntable={alias="Blackwell",title="Captain"}\n'
        '[service_bindings.llm]\ntype="run_refbackend_mod.StubBackend"\nmodel="m"\n'
        '[trainable]\n[trainable.config]\ntemperature=0.7\nmax_tokens=64\n'
        '[trainable.service_bindings]\nllm={type="run_refbackend_mod.StubBackend"}\n'
        '[trainable.reads]\nformatted_prompt={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )
    reg.add_composition("trainables/ref.toml", loads(comp_toml, "composition", file_path="trainables/ref.toml"))
    pipeline = loads(_ref_pipeline("trainables/ref.toml"), "pipeline", file_path="p.toml")
    deployment = loads(TRAIN_DEPLOYMENT, "deployment", file_path="d.toml")
    runnable = _runnable(pipeline, reg, name="acme.ref", deployment=deployment)
    result = run(runnable, {"raw": "hi"})
    assert result.state["dialogue_response"] == "reply:mappingproxy"  # reference → deep-frozen, not a plain dict


# verifies: preprocessor-mirrors-outer-node
def test_preprocessor_binding_value_validation_raises(module_dir):
    """The VALIDATION half of the mirror: with the real declaration flowing, a preprocessor's
    supplied binding VALUE is validated against the referenced handler's declared field schema — a
    value violating a closed-enum (`Literal`) field raises a BINDING_VALUE_SHAPE ContractViolation
    at assembly, exactly as an outer node's binding does, and the error is attributed to the
    REFERENCED handler's TOML (the error-attribution shift). RED on removal: the old synthesized
    declaration carried `fields=()`, so a preprocessor binding value was never validated — a
    schema-valid training record built on an unvalidated input (training-data corruption)."""
    _write_module(module_dir, "run_valctx_mod",
                  "def prep(*, raw):\n    return {'npc_state': 'calm', 'user_message': raw}")
    _write_module(module_dir, "run_valpp_mod",
                  "def fmt(*, context, utterance, config):\n    return {'prompt': context}")
    _write_module(module_dir, "run_valbackend_mod", _STUB_BACKEND_SRC)
    reg = DeclarationRegistry()
    reg.add_service_type(loads(_backend_service_type("run_valbackend_mod.StubBackend"),
                               "service_type", file_path="st.toml"), toml_path="st.toml")
    reg.add_handler("run_valctx_mod.prep", loads(OUTER_TRANSFORM, "handler", file_path="prep.toml"),
                    toml_path="handlers/prep.toml")
    reg.add_handler("run_valpp_mod.fmt", loads(
        '[transform]\n[reads]\ncontext={type="str"}\nutterance={type="str"}\n[output_schema]\nprompt={type="str"}\n'
        '[bindings.config]\nmarker_set={type="Literal[\'brackets\', \'curly\']"}\n', "handler", file_path="fmt.toml"),
        toml_path="handlers/fmt.toml")
    comp_toml = (
        '[meta]\nkind="trainable"\nname="val_training"\n'
        '[inputs]\nnpc_state={type="str"}\nuser_message={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="run_valpp_mod.fmt"\nid="fmt"\n'
        'reads_map={context="npc_state",utterance="user_message"}\nwrites_map={prompt="formatted_prompt"}\n'
        '[preprocessors.bindings]\nconfig={marker_set="angles"}\n'  # "angles" violates Literal['brackets','curly']
        '[service_bindings.llm]\ntype="run_valbackend_mod.StubBackend"\nmodel="m"\n'
        '[trainable]\n[trainable.config]\ntemperature=0.7\nmax_tokens=64\n'
        '[trainable.service_bindings]\nllm={type="run_valbackend_mod.StubBackend"}\n'
        '[trainable.reads]\nformatted_prompt={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
    )
    reg.add_composition("trainables/val.toml", loads(comp_toml, "composition", file_path="trainables/val.toml"))
    pipeline = loads(_ref_pipeline("trainables/val.toml").replace("acme.ref", "acme.val").replace("run_refctx_mod", "run_valctx_mod"),
                     "pipeline", file_path="p.toml")
    deployment = loads(TRAIN_DEPLOYMENT, "deployment", file_path="d.toml")
    with pytest.raises(ContractViolation) as exc:
        _runnable(pipeline, reg, name="acme.val", deployment=deployment)
    assert exc.value.check is Check.BINDING_VALUE_SHAPE
    assert "marker_set" in exc.value.actual
    assert exc.value.file_path == "handlers/fmt.toml"  # attributed to the REFERENCED handler's TOML (the shift)


# ---------------------------------------------------------------------------
# 27b. The extras table + reserved-wire-key disjointness, end-to-end (D3)
# ---------------------------------------------------------------------------


def _extras_runnable_factory(module_dir, *, extras_toml, out_schema_toml="response = { type = \"str\" }"):
    """Compose + assemble a trainable composition bound to a stub backend whose
    service-type declares an `extras` table config field (and `reserved_wire_keys`). The
    backend is bound by its dotted-class-path name (the established e2e pattern — the real
    lib adapters resolve via an entry-point map absent in tests). Returns the compiled
    graph + registry + deployment so the caller drives `assemble` (where the
    extras-disjointness check fires)."""
    _write_module(
        module_dir, "run_extras_backend_mod",
        """
        class ExtrasBackend:
            training_artifact_contract = "gguf"
            reserved_wire_keys = frozenset(
                {"model", "prompt", "temperature", "max_tokens", "grammar"}
            )

            def __init__(self, model, *, output_schema, schema_source):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, max_tokens, extras, **transport_extra):
                return {"response": "ok"}
        """,
    )
    service_type = f"""
name = "run_extras_backend_mod.ExtrasBackend"
[identity_schema]
model = {{ type = "str" }}
[transport_schema]
endpoint = {{ type = "str" }}
[config_schema]
temperature = {{ type = "float", default = 0.7 }}
max_tokens = {{ type = "int", default = 64 }}
extras = {{ type = "table", default = {{}} }}
"""
    out_field_names = [
        line.split("=")[0].strip() for line in out_schema_toml.splitlines() if "=" in line
    ]
    outputs_block = "\n".join(f'{n} = {{ type = "str" }}' for n in out_field_names)
    composition = f"""
[meta]
kind = "trainable"
name = "extras_comp"
[inputs]
prompt = {{ type = "str" }}
[outputs]
{outputs_block}
[service_bindings.llm]
type = "run_extras_backend_mod.ExtrasBackend"
model = "m"
[trainable]
[trainable.config]
temperature = 0.7
max_tokens = 64
{extras_toml}
[trainable.service_bindings]
llm = {{ type = "run_extras_backend_mod.ExtrasBackend" }}
[trainable.reads]
prompt = {{ type = "str" }}
[trainable.output_schema]
{out_schema_toml}
"""
    pipeline = f"""
[meta]
name = "acme.extras"
[[nodes]]
kind = "composition"
name = "trainables/extras.toml"
[inputs]
prompt = {{ type = "str" }}
[outputs]
{outputs_block}
"""
    deployment = """
[transport.llm]
endpoint = "https://llm.test/v1"
[training_contract]
integrity_enforcement = false
"""
    from conjured.validator import loads as _loads
    reg = DeclarationRegistry()
    reg.add_service_type(_loads(service_type, "service_type", file_path="st.toml"), toml_path="st.toml")
    reg.add_composition("trainables/extras.toml", _loads(composition, "composition", file_path="trainables/extras.toml"))
    dep = _loads(deployment, "deployment", file_path="d.toml")
    graph = compile_pipeline(
        _loads(pipeline, "pipeline", file_path="p.toml"), reg,
        pipeline_name="acme.extras", deployment=dep, file_path="p.toml",
    )
    return graph, reg, dep


def test_extras_naming_a_reserved_wire_key_rejects_at_assemble(module_dir):
    # The extras-disjointness check fires at assemble (where the adapter resolves): an
    # `extras` key naming a reserved wire key (here `temperature`, a dial) is a wrong-door
    # override attempt — rejected with the key's real home named. Compose passes (no
    # adapter there); assemble raises.
    graph, reg, dep = _extras_runnable_factory(
        module_dir, extras_toml="extras = { temperature = 0.5 }"
    )
    with pytest.raises(ContractViolation) as exc:
        assemble(graph, reg, dep)
    cv = exc.value
    assert cv.check is Check.CONFIG_SCHEMA_SUPPLY
    assert "temperature" in cv.actual
    assert "[config_schema]" in cv.remediation_hint  # the dial's real home


def test_extras_sampling_tail_assembles_clean(module_dir):
    # A disjoint extras table (the sampling tail) assembles without complaint.
    graph, reg, dep = _extras_runnable_factory(
        module_dir, extras_toml="extras = { top_p = 0.9, top_k = 40 }"
    )
    runnable = assemble(graph, reg, dep)  # no raise
    assert runnable.nodes[-1].node_kind == "trainable"


def test_domain_field_named_model_is_data_plane_and_assembles_clean(module_dir):
    # The reserved-wire-key check inspects ONLY extras keys — a DOMAIN output field named
    # `model` (an NPC emitting a car model, say) is data-plane and never a wire key, so it
    # composes + assembles clean even though `model` is a reserved wire key.
    graph, reg, dep = _extras_runnable_factory(
        module_dir,
        extras_toml="extras = {}",
        out_schema_toml='response = { type = "str" }\nmodel = { type = "str" }',
    )
    runnable = assemble(graph, reg, dep)  # no raise — `model` here is an output channel
    assert "model" in {
        port for node in runnable.nodes for port in node.write_map.values()
    } or any("model" in c.name for c in graph.channels)


# ---------------------------------------------------------------------------
# 28. Validate-then-copy — the escape-hole fix (ruled 2026-06-10)
# ---------------------------------------------------------------------------


def test_generator_seeded_input_surfaces_as_the_first_reader_sve(module_dir):
    # A non-deep-copyable wrong-typed seed used to escape run() as a raw TypeError
    # out of the reads-projection deepcopy; the ruled fix validates the RAW seeded
    # value at the first reader's reads boundary BEFORE the vector-4 copy.
    runnable = _chain(module_dir, mod_name="run_chain_gen_mod")
    with pytest.raises(SchemaValidationError) as exc:
        run(runnable, {"text": (c for c in "hi")})
    sve = exc.value
    assert sve.audit_code == INPUT_VALIDATION_AUDIT_CODE
    assert sve.handler_qualified_name == "run_chain_gen_mod.first"
    assert sve.handler_position == 0
    assert sve.field_validations[0].field_path == "reads.text"


def test_file_handle_seeded_input_surfaces_as_the_first_reader_sve(module_dir):
    runnable = _chain(module_dir, mod_name="run_chain_fh_mod")
    target = module_dir / "seeded.txt"
    target.write_text("payload", encoding="utf-8")
    with open(target, encoding="utf-8") as handle:
        with pytest.raises(SchemaValidationError) as exc:
            run(runnable, {"text": handle})
    sve = exc.value
    assert sve.audit_code == INPUT_VALIDATION_AUDIT_CODE
    assert sve.handler_qualified_name == "run_chain_fh_mod.first"
    assert sve.field_validations[0].field_path == "reads.text"


def test_timeout_boundary_with_a_non_copyable_seed_keeps_the_structured_pf(module_dir):
    # The same hole existed via the timeout PF's reads_snapshot (it deep-copied the
    # projection); the snapshot now rides the raw projection and PipelineFailure's
    # own snapshot_copy, which passes non-container leaves by reference.
    runnable = _sentinel_runnable(module_dir, mod_name="run_sentinel_gen_t0_mod")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": (c for c in "hi")}, timeout_ms=0)
    pf = exc.value
    assert pf.cause_class == "TimeoutError"
    assert "text" in pf.reads_snapshot


# ---------------------------------------------------------------------------
# 28b. First-consumer seed validation — the two writer/reader topologies (D1)
# ---------------------------------------------------------------------------
#
# A declared [inputs] channel that is ALSO node-written is a seed-contributing merged
# channel (seed + node write = 2 contributors → requires merge.<channel>). The seed's
# FIRST consumer may be the MERGE FOLD (the writer precedes any reader) rather than a
# reads-projection — and that fold has no reading node to borrow a reads model from. The
# per-channel seed validator (built at assemble over the channel's [inputs] FieldDecl)
# validates the raw seed at the fold, raising the ruled reads-side SVE attributed to the
# pipeline. The flag clears on first validation so a later reader of a seed-preserving
# value (first_wins) never re-validates and never deep-copies an unvalidated seed.


def _seed_fold_runnable(module_dir, mod_name, channel_type, strategy, emit_item):
    """A two-node pipeline whose declared [inputs] channel `acc` is written by `emit`
    (writer at position 0) and read by `reader` (position 1) — so `acc` is a
    seed-contributing merged channel and the WRITER's merge fold is the seed's first
    consumer. `emit` reads a separate seeded `trigger`, never `acc`; `emit_item` is the
    Python expression for its `item` write (matching `channel_type`)."""
    item_type = channel_type
    _write_module(
        module_dir, mod_name,
        f"""
        def emit(*, trigger):
            return {{"item": {emit_item}}}

        def reader(*, acc):
            return {{"seen": len(acc)}}
        """,
    )
    acc = FieldDecl(name="acc", type=channel_type)
    reg = DeclarationRegistry()
    reg.add_handler(
        f"{mod_name}.emit",
        _transform((_fd("trigger"),), (FieldDecl(name="item", type=item_type),)),
        toml_path="handlers/emit.toml",
    )
    reg.add_handler(
        f"{mod_name}.reader", _transform((acc,), (_fd("seen", "int"),)),
        toml_path="handlers/reader.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.seedfold"),
        nodes=(
            HandlerNode(name=f"{mod_name}.emit", writes_map={"item": "acc"}),
            HandlerNode(name=f"{mod_name}.reader"),
        ),
        merge={"acc": strategy},
        inputs=(_fd("trigger"), acc),
    )
    return _runnable(pipeline, reg, name="acme.seedfold")


def test_writer_first_raw_seed_validates_at_the_merge_fold_sve(module_dir):
    # D1 hole 1 (the silent-corruption probe → regression): a str seed on a
    # list[str]/append_list seed-contributing channel used to silently coerce
    # (`list("abc")` → ['a','b','c']) into the fold with NO error — training-data
    # corruption by omission. The merge fold (the seed's first consumer here, the writer
    # precedes the reader) now validates the raw seed against the per-channel seed
    # validator and raises the ruled reads-side SVE before any coercion.
    runnable = _seed_fold_runnable(
        module_dir, "run_seedfold_mod", list_of(primitive("str")),
        MergeStrategy.APPEND_LIST, emit_item="[trigger]",
    )
    with pytest.raises(SchemaValidationError) as exc:
        run(runnable, {"trigger": "go", "acc": "abc"})  # a str seed on a list[str] channel
    sve = exc.value
    assert sve.audit_code == INPUT_VALIDATION_AUDIT_CODE
    assert sve.handler_qualified_name == "acme.seedfold"  # the pipeline owns the seed
    assert sve.handler_position == 0  # caught at the writer's fold, not the later reader
    assert sve.field_validations[0].field_path == "reads.acc"
    assert sve.field_validations[0].expected_type == "list[str]"


def test_post_write_reader_first_wins_raw_seed_validates_as_sve(module_dir):
    # D1 hole 2: under first_wins the post-fold value IS still the raw seed; a reader
    # after the first write used to skip pre-validation and deep-copy a non-deep-copyable
    # wrong-typed seed into a raw TypeError → PF, never the ruled SVE. The merge fold (the
    # seed's first consumer) now validates the raw seed, so a generator seed on a str
    # channel surfaces as the reads-side SVE before any reader deep-copies it.
    runnable = _seed_fold_runnable(
        module_dir, "run_seedfw_mod", primitive("str"), MergeStrategy.FIRST_WINS,
        emit_item='"written"',
    )
    with pytest.raises(SchemaValidationError) as exc:
        run(runnable, {"trigger": "go", "acc": (c for c in "hi")})  # generator on a str channel
    sve = exc.value
    assert sve.audit_code == INPUT_VALIDATION_AUDIT_CODE
    assert sve.handler_qualified_name == "acme.seedfold"
    assert sve.field_validations[0].field_path == "reads.acc"


def test_writer_first_valid_seed_folds_into_the_seed_after_validation(module_dir):
    # D1 happy path (baseline coverage): a writer-first VALID seed validates clean at the
    # merge fold, and the first node write folds INTO the seed (the seed is the fold's
    # first element) — append_list yields [seed-elem, written-elem].
    runnable = _seed_fold_runnable(
        module_dir, "run_seedok_mod", list_of(primitive("str")),
        MergeStrategy.APPEND_LIST, emit_item="[trigger]",
    )
    result = run(runnable, {"trigger": "go", "acc": ["seed"]})
    assert result.state["acc"] == ["seed", "go"]  # the write folded INTO the seed
    assert result.state["seen"] == 2


# ---------------------------------------------------------------------------
# 29. The return-point timeout check (D4-i's merge/return-point half)
# ---------------------------------------------------------------------------


def test_return_point_timeout_attributed_to_the_last_node(module_dir):
    _write_module(
        module_dir, "run_slow_solo_mod",
        """
        import time

        def slow(*, text):
            time.sleep(0.05)
            return {"out": text}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_slow_solo_mod.slow", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/slow.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.solo"),
        nodes=(HandlerNode(name="run_slow_solo_mod.slow"),),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.solo")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "hi"}, timeout_ms=10)
    pf = exc.value
    # The ONLY boundary a single-node walk can exhaust the budget at is the return
    # point: the pre-dispatch check ran at ~0 ms, the dispatch slept past the
    # budget, and the halt is attributed to the last (only) node.
    assert pf.cause_class == "TimeoutError"
    assert pf.failed_handler_qualified_name == "run_slow_solo_mod.slow"
    assert pf.failed_handler_position == 0
    assert pf.composition_ref == "acme.solo[0]"
    assert pf.elapsed_ms_at_failure >= 10
    # The D4 snapshot fill: the attributed node's projection and bindings.
    assert pf.reads_snapshot == {"text": "hi"}
    assert pf.bindings_snapshot == {}


# ---------------------------------------------------------------------------
# 30. Same-node multi-port writes fold in DECLARATION order, not body order
# ---------------------------------------------------------------------------


def test_same_node_multi_port_writes_fold_in_declaration_order(module_dir):
    _write_module(
        module_dir, "run_two_port_mod",
        """
        def both(*, seed):
            return {"b": "B", "a": "A"}  # body insertion order is b-then-a
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_two_port_mod.both", _transform((_fd("seed"),), (_fd("a"), _fd("b"))),
        toml_path="handlers/both.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.twoport"),
        nodes=(
            HandlerNode(
                name="run_two_port_mod.both", writes_map={"a": "log", "b": "log"}
            ),
        ),
        merge={"log": MergeStrategy.CONCAT_STR},
        inputs=(_fd("seed"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.twoport")
    # The declaration (output_schema a-then-b) sequences the fold; reordering the
    # body's return-dict literal is contract-neutral and must not change the merge.
    assert run(runnable, {"seed": "s"}).state["log"] == "AB"


# ---------------------------------------------------------------------------
# 31. FVF wrap: cause_message carries the UNDERLYING exception's message
# ---------------------------------------------------------------------------


def test_raising_validator_pf_carries_the_underlying_message(module_dir):
    _write_module(
        module_dir, "run_fvfmsg_mod",
        """
        def emit(*, text):
            return {"label": "calm"}
        """,
    )
    _write_module(
        module_dir, "run_fvfmsg_validators",
        """
        def explode(*, value):
            raise RuntimeError("validator exploded inside")
        """,
    )
    reg = DeclarationRegistry()
    out = FieldDecl(
        name="label", type=primitive("str"),
        validators=(ValidatorSpec(name="run_fvfmsg_validators.explode"),),
    )
    reg.add_handler(
        "run_fvfmsg_mod.emit", _transform((_fd("text"),), (out,)),
        toml_path="handlers/emit.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.fvfmsg"),
        nodes=(HandlerNode(name="run_fvfmsg_mod.emit"),),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.fvfmsg")
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"text": "x"})
    pf = exc.value
    # The cause pair names ONE underlying exception: class AND message both from
    # FieldValidatorFailure.__cause__ (canon: cause_message is str(exc) of the
    # underlying exception at wrap time) — never the carrier's shim prose.
    assert pf.cause_class == "RuntimeError"
    assert pf.cause_message == "validator exploded inside"


# ---------------------------------------------------------------------------
# 32. run()'s non-Mapping inputs guard (engine-surface misuse, raw TypeError)
# ---------------------------------------------------------------------------


def test_non_mapping_inputs_rejected_with_a_typeerror(module_dir):
    runnable = _chain(module_dir, mod_name="run_chain_nonmap_mod")
    with pytest.raises(
        TypeError, match="inputs must be a mapping of channel name -> value, got list"
    ):
        run(runnable, ["text"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 33. last_present_wins: numerics/bools always present (B1); merge fold edges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "channel_token", "payloads", "expected"),
    [
        ("intzero", "int", [7, 0], 0),                # 0 is a value, not an absence
        ("boolfalse", "bool", [True, False], False),  # False is a value
    ],
)
def test_last_present_wins_numerics_and_bools_are_always_present(
    module_dir, tag, channel_token, payloads, expected
):
    # B1's accepted clause: "numerics and bools are always present (0/False are
    # values, not absences)" — the _is_present TypeError arm, unreachable from str
    # payloads, must treat the LAST write as present even when falsy.
    runnable = _merge_runnable(
        module_dir, f"run_merge_lpwnum_{tag}_mod", primitive(channel_token),
        MergeStrategy.LAST_PRESENT_WINS, payloads,
    )
    assert run(runnable, {"seed": "s"}).state["merged"] == expected


def test_three_writer_merged_channel_chains_the_fold(module_dir):
    runnable = _merge_runnable(
        module_dir, "run_merge_three_mod", list_of(primitive("str")),
        MergeStrategy.APPEND_LIST, [["a"], ["b"], ["c"]],
    )
    # Fold of a fold: _first_fold seeds, then TWO chained fold steps.
    assert run(runnable, {"seed": "s"}).state["merged"] == ["a", "b", "c"]


def test_single_writer_merged_channel_is_the_first_fold_degenerate(module_dir):
    runnable = _merge_runnable(
        module_dir, "run_merge_single_mod", list_of(primitive("str")),
        MergeStrategy.UNION_SET, [["a", "a", "b"]],
    )
    # Exactly one writer: the channel value is _first_fold alone — union_set dedups
    # the seed write itself (the fold over one write is already a union).
    assert run(runnable, {"seed": "s"}).state["merged"] == ["a", "b"]


# ---------------------------------------------------------------------------
# 33b. The contributor model (R-pipeline-002, the ruled replacement): a seeded
#      declared-input channel's seed is the fold's FIRST ELEMENT; readers see the
#      strategy's left-fold over the contributors upstream of their position.
# ---------------------------------------------------------------------------


def _seeded_merge_runnable(module_dir, mod_name, channel_type, strategy, payloads):
    """The channel ``merged`` is BOTH a declared [inputs] channel (seeded by the
    invocation) AND written by N writer nodes — a multi-contributor channel under the
    declared strategy. A ``snap`` reader sits BEFORE the first writer (recording the
    fold over [seed] alone) and the final state carries the full fold."""
    _write_module(
        module_dir, mod_name,
        """
        def emit(*, payload):
            return {"item": payload}

        def snap(*, merged):
            return {"snapshot": merged}
        """,
    )
    reg = DeclarationRegistry()
    emit_decl = TransformDeclaration(
        reads=(),
        output_schema=(FieldDecl(name="item", type=channel_type),),
        bindings=(
            Binding(
                name="payload",
                body=SchemaBinding(fields=(FieldDecl(name="value", type=channel_type),)),
            ),
        ),
    )
    snap_decl = TransformDeclaration(
        reads=(FieldDecl(name="merged", type=channel_type),),
        output_schema=(FieldDecl(name="snapshot", type=channel_type),),
    )
    reg.add_handler(f"{mod_name}.emit", emit_decl, toml_path="handlers/emit.toml")
    reg.add_handler(f"{mod_name}.snap", snap_decl, toml_path="handlers/snap.toml")
    nodes = [HandlerNode(name=f"{mod_name}.snap")] + [
        HandlerNode(
            name=f"{mod_name}.emit",
            bindings=(InlineBindingValue(name="payload", value={"value": payload}),),
            writes_map={"item": "merged"},
        )
        for payload in payloads
    ]
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.seedmerge"),
        nodes=tuple(nodes),
        merge={"merged": strategy},
        inputs=(FieldDecl(name="merged", type=channel_type),),
    )
    return _runnable(pipeline, reg, name="acme.seedmerge")


def test_seeded_merge_last_present_wins_fold_of_seed_and_write_is_the_write(module_dir):
    """The ruled no-behavioral-change assertion: under last_present_wins,
    fold(seed, present write) = the write — a seeded-then-written channel ends at the
    write's value exactly as before the contributor model (the seed participates but a
    present later contributor wins); the reader before the writer sees the seed."""
    runnable = _seeded_merge_runnable(
        module_dir, "run_seedmerge_lpw_mod", primitive("str"),
        MergeStrategy.LAST_PRESENT_WINS, ["W"],
    )
    result = run(runnable, {"merged": "SEED"})
    assert result.state["snapshot"] == "SEED"  # the fold over [seed] alone
    assert result.state["merged"] == "W"       # fold(seed, write) = write


def test_seeded_merge_concat_str_folds_the_seed_first(module_dir):
    """A combining strategy now lets the seed participate (previously structurally
    clobbered): the seed is the fold's FIRST element, the write folds into it."""
    runnable = _seeded_merge_runnable(
        module_dir, "run_seedmerge_concat_mod", primitive("str"),
        MergeStrategy.CONCAT_STR, ["B", "C"],
    )
    result = run(runnable, {"merged": "A"})
    assert result.state["snapshot"] == "A"     # reader before any write: the seed
    assert result.state["merged"] == "ABC"     # left-fold: seed, then writes in order


def test_seeded_merge_union_set_dedups_the_seed_in_a_readers_projection(module_dir):
    """A reader of a seeded merged channel BEFORE any node write sees the strategy's
    fold over [seed] — for union_set, the deduped seed (not the raw value); the final
    value is the fold over all contributors."""
    runnable = _seeded_merge_runnable(
        module_dir, "run_seedmerge_union_mod", list_of(primitive("str")),
        MergeStrategy.UNION_SET, [["y", "z"]],
    )
    result = run(runnable, {"merged": ["x", "x", "y"]})
    assert result.state["snapshot"] == ["x", "y"]       # fold over [seed]: deduped
    assert result.state["merged"] == ["x", "y", "z"]    # ∪ the write, first-occurrence order


# ---------------------------------------------------------------------------
# 34. The vector-3 matrix tail: deleted-name arm; halting-path wiring; the
#     ratified already-halting restore-failure arm
# ---------------------------------------------------------------------------


def test_deleted_module_name_reinserted_by_the_revert(module_dir, caplog):
    _write_module(
        module_dir, "run_deleter_mod",
        """
        _DOOMED = "survives"

        def deleter(*, text):
            global _DOOMED
            del _DOOMED
            return {"out": text}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_deleter_mod.deleter", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/deleter.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.deleter"),
        nodes=(HandlerNode(name="run_deleter_mod.deleter"),),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.deleter")
    with caplog.at_level(logging.WARNING, logger="conjured.runner"):
        result = run(runnable, {"text": "hi"})
    assert dict(result.state) == {"out": "hi"}
    module = importlib.import_module("run_deleter_mod")
    assert module._DOOMED == "survives"  # the deleted name was re-inserted
    [record] = [
        r for r in caplog.records if "reverted module-namespace" in r.getMessage()
    ]
    assert "deleted=['_DOOMED']" in record.getMessage()


def test_clean_revert_on_a_halting_path_keeps_the_original_error(module_dir, caplog):
    _write_module(
        module_dir, "run_leakboom_mod",
        """
        _FLAG = "initial"

        def leakboom(*, text):
            global _FLAG
            _FLAG = text
            raise RuntimeError("body halt after a namespace mutation")
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_leakboom_mod.leakboom", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/leakboom.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.leakboom"),
        nodes=(HandlerNode(name="run_leakboom_mod.leakboom"),),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.leakboom")
    with caplog.at_level(logging.WARNING, logger="conjured.runner"):
        with pytest.raises(PipelineFailure) as exc:
            run(runnable, {"text": "hi"})
    # The ORIGINAL halt error propagates (the finally's clean revert never masks)...
    assert exc.value.cause_class == "RuntimeError"
    assert exc.value.cause_message == "body halt after a namespace mutation"
    # ... and the halting=True wiring through _make_task still reverted the leak:
    module = importlib.import_module("run_leakboom_mod")
    assert module._FLAG == "initial"
    [record] = [
        r for r in caplog.records if "reverted module-namespace" in r.getMessage()
    ]
    assert "run_leakboom_mod.leakboom" in record.getMessage()


def test_already_halting_restore_failure_logs_error_and_the_original_class_propagates(caplog):
    """The RATIFIED arm (2026-06-10): when the dispatch is ALREADY halting and the
    restore also fails, the runner logs at ERROR and the ORIGINAL error class
    propagates — never the restore ContractViolation (raising it from the finally
    would mask the real failure's class; both paths halt)."""
    namespace, snapshot = _failing_restore_kwargs()
    original = RuntimeError("the body's own halt")
    with caplog.at_level(logging.ERROR, logger="conjured.runner"):
        with pytest.raises(RuntimeError) as exc:
            try:
                raise original  # the in-flight halt the finally must not mask
            finally:
                restore_after_dispatch(
                    namespace, snapshot,
                    handler_qualified_name="acme.leaky", handler_position=3,
                    run_id="run_2026-06-10T00:00:00Z_v3cc",
                    composition_ref="acme.leaky[3]",
                    halting=True,
                )
    # The ORIGINAL error object — not a ContractViolation out of the restore arm.
    assert exc.value is original
    [record] = [r for r in caplog.records if "restore FAILED" in r.getMessage()]
    assert record.levelno == logging.ERROR
    assert "the original halt error propagates" in record.getMessage()


# ---------------------------------------------------------------------------
# 35. Backend-SDK hook transport reaches the adapter (run level)
# ---------------------------------------------------------------------------


def test_backend_sdk_hook_transport_reaches_the_adapter_at_run_level(module_dir, caplog):
    _write_module(
        module_dir, "run_hooktx_mod",
        """
        def producer(*, text):
            return {"out": text.upper()}

        def watch(*, out, services):
            services.emit.invoke(line=out)
        """,
    )
    _write_module(
        module_dir, "run_hooktx_adapters",
        """
        class EmitAdapter:
            def __init__(self, sink):
                self.sink = sink

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                raise RuntimeError("endpoint=" + repr(transport_extra.get("endpoint")))
        """,
    )
    type_name = "run_hooktx_adapters.EmitAdapter"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("sink"),),
            transport_schema=(_fd("endpoint"),),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        "run_hooktx_mod.producer", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/producer.toml",
    )
    reg.add_handler(
        "run_hooktx_mod.watch",
        HookDeclaration(
            reads=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="emit", type=type_name),),
        ),
        toml_path="handlers/watch.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.hooktx"),
        nodes=(
            HandlerNode(name="run_hooktx_mod.producer"),
            HandlerNode(name="run_hooktx_mod.watch"),
        ),
        service_bindings=(
            ServiceBindingSupply(name="emit", type=type_name, identity={"sink": "s1"}),
        ),
        inputs=(_fd("text"),),
    )
    deployment = DeploymentDeclaration(
        transport=(
            TransportBlock(name="emit", values={"endpoint": "https://emit.test/v1"}),
        ),
        hook_transport=(
            HookTransportBlock(hook_qualified_name="run_hooktx_mod.watch"),
        ),
        training_contract=TrainingContract(integrity_enforcement=False),
    )
    runnable = _runnable(pipeline, reg, name="acme.hooktx", deployment=deployment)
    with caplog.at_level(logging.WARNING, logger="conjured.runner"):
        result = run(runnable, {"text": "hi"}, pipeline_run_id="hooktx-1")
    assert dict(result.state) == {"out": "HI"}
    [record] = [r for r in caplog.records if "absorbed" in r.getMessage()]
    # The compose-validated transport.emit endpoint REACHED the adapter — the
    # recording raise carries it into the absorbed-PF warning's cause_message.
    assert "endpoint='https://emit.test/v1'" in record.getMessage()


# ---------------------------------------------------------------------------
# 36. Adapter-cache reuse: one adapter per (composition scope, binding)
# ---------------------------------------------------------------------------


def test_adapter_instance_shared_across_nodes_of_one_binding(module_dir):
    _write_module(
        module_dir, "run_cache_mod",
        """
        def call(*, text, services):
            return {"out": services.llm.invoke(q=text)["r"]}
        """,
    )
    _write_module(
        module_dir, "run_cache_adapters",
        """
        class IdentityRevealing:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"r": str(id(self))}
        """,
    )
    type_name = "run_cache_adapters.IdentityRevealing"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),), transport_schema=(),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        "run_cache_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.cache"),
        nodes=(
            HandlerNode(name="run_cache_mod.call", writes_map={"out": "o1"}),
            HandlerNode(name="run_cache_mod.call", writes_map={"out": "o2"}),
        ),
        service_bindings=(
            ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),
        ),
        inputs=(_fd("text"),),
    )
    runnable = _runnable(pipeline, reg, name="acme.cache")
    state = run(runnable, {"text": "hi"}).state
    # One adapter per (composition scope, binding name): both nodes' invocations
    # hit the SAME instance — identity-only construction ran once (the B2 cache).
    assert state["o1"] == state["o2"]


# ---------------------------------------------------------------------------
# 37. The trainable e2e tail: ordinal-vs-position discrimination on failure;
#     hook- and service-kind preprocessor synthesis
# ---------------------------------------------------------------------------


def test_trainable_terminal_failure_discriminates_ordinal_from_position(module_dir):
    _write_module(
        module_dir, "run_tfctx_mod",
        """
        def prep(*, raw):
            return {"npc_state": "calm", "user_message": raw}
        """,
    )
    _write_module(
        module_dir, "run_tfpp_mod",
        """
        def assemble_prompt(*, context, utterance, config):
            return {"prompt": context + "|" + utterance + "|" + config}
        """,
    )
    _write_module(
        module_dir, "run_tfailbackend_mod",
        """
        class RaisingTrainableBackend:
            training_artifact_contract = "gguf"
            reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

            def __init__(self, model, *, output_schema, schema_source):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, max_tokens, **transport_extra):
                raise RuntimeError("backend exploded")
        """,
    )
    service_type_toml = SERVICE_TYPE_TRAIN.replace(
        "run_tbackend_mod.StubTrainableBackend",
        "run_tfailbackend_mod.RaisingTrainableBackend",
    )
    composition_toml = (
        TRAINABLE_COMPOSITION
        .replace(
            "run_tbackend_mod.StubTrainableBackend",
            "run_tfailbackend_mod.RaisingTrainableBackend",
        )
        .replace("run_tpp_mod.assemble_prompt", "run_tfpp_mod.assemble_prompt")
    )
    pipeline_toml = (
        TRAIN_PIPELINE
        .replace("acme.train", "acme.trainfail")
        .replace("run_tctx_mod.prep", "run_tfctx_mod.prep")
        .replace("trainables/dialogue.toml", "trainables/dialogue_fail.toml")
    )
    reg = DeclarationRegistry()
    reg.add_service_type(
        loads(service_type_toml, "service_type", file_path="st.toml"),
        toml_path="st.toml",
    )
    reg.add_handler(
        "run_tfctx_mod.prep", loads(OUTER_TRANSFORM, "handler", file_path="prep.toml"),
        toml_path="handlers/prep.toml",
    )
    reg.add_handler(
        "run_tfpp_mod.assemble_prompt", loads(PREPROC_FORMATTER, "handler", file_path="pp.toml"),
        toml_path="handlers/pp.toml",
    )
    reg.add_composition(
        "trainables/dialogue_fail.toml",
        loads(
            composition_toml, "composition", file_path="trainables/dialogue_fail.toml"
        ),
    )
    pipeline = loads(pipeline_toml, "pipeline", file_path="p.toml")
    deployment = loads(TRAIN_DEPLOYMENT, "deployment", file_path="d.toml")
    runnable = _runnable(pipeline, reg, name="acme.trainfail", deployment=deployment)
    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"raw": "hello"})
    pf = exc.value
    # The discrimination under test: failed_handler_position is the DISPATCH index (2);
    # the composition_ref ordinal is the DECLARATION-entry index (1) — substituting
    # node.position would emit "acme.trainfail[2]".
    assert pf.cause_class == "RuntimeError"
    assert pf.failed_handler_position == 2
    assert pf.composition_ref == "acme.trainfail[1]"


# verifies: failure-category-trainable-is-service
def test_trainable_backend_failure_is_service_locus_with_binding(module_dir):
    """The trainable analogue of test_adapter_value_error_is_service_origin_with_binding.
    error-channel/reference.md § PipelineFailure payload / failure_category: ``"service"``
    is defined to INCLUDE "a trainable composition node's engine-constructed
    ``adapter.invoke``", with ``service_binding_name`` present. A trainable node's only
    "body" IS that engine-constructed invoke, so its ``node_kind`` alone is the structural
    locus signal — the attribution is read from where the failure escaped, never sniffed
    from the exception name (the backend raises a plain ValueError, not a service-shaped
    fault, exactly as the adapter analogue does).

    This is a SEPARATE ``_wrap`` branch from the service-kind path: the trainable dispatch
    (``construct_trainable`` → ``partial(adapter.invoke, ...)``) carries NO
    ``_ServiceOriginError`` wrap, so the ``elif node.node_kind == "trainable"`` arm is what
    sets ``failure_category="service"`` + the binding. The existing trainable-failure test
    asserts only cause_class / position / composition_ref — none of which depend on that
    arm — so deleting it leaves that test green. RED here: removing the trainable arm routes
    the failure to the ``else`` (``failure_category="handler"`` + null binding), mis-blaming
    a backend/service-availability failure on the handler author (the consumer-routing
    corruption the closed locus exists to prevent)."""
    _write_module(
        module_dir, "run_tsvcctx_mod",
        """
        def prep(*, raw):
            return {"npc_state": "calm", "user_message": raw}
        """,
    )
    _write_module(
        module_dir, "run_tsvcpp_mod",
        """
        def assemble_prompt(*, context, utterance, config):
            return {"prompt": context + "|" + utterance + "|" + config}
        """,
    )
    _write_module(
        module_dir, "run_tsvcbackend_mod",
        """
        class RaisingValueBackend:
            training_artifact_contract = "gguf"
            reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

            def __init__(self, model, *, output_schema, schema_source):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, max_tokens, **transport_extra):
                raise ValueError("not a service-shaped fault")
        """,
    )
    service_type_toml = SERVICE_TYPE_TRAIN.replace(
        "run_tbackend_mod.StubTrainableBackend",
        "run_tsvcbackend_mod.RaisingValueBackend",
    )
    composition_toml = (
        TRAINABLE_COMPOSITION
        .replace(
            "run_tbackend_mod.StubTrainableBackend",
            "run_tsvcbackend_mod.RaisingValueBackend",
        )
        .replace("run_tpp_mod.assemble_prompt", "run_tsvcpp_mod.assemble_prompt")
    )
    pipeline_toml = (
        TRAIN_PIPELINE
        .replace("acme.train", "acme.trainsvc")
        .replace("run_tctx_mod.prep", "run_tsvcctx_mod.prep")
        .replace("trainables/dialogue.toml", "trainables/dialogue_svc.toml")
    )
    reg = DeclarationRegistry()
    reg.add_service_type(
        loads(service_type_toml, "service_type", file_path="st.toml"),
        toml_path="st.toml",
    )
    reg.add_handler(
        "run_tsvcctx_mod.prep", loads(OUTER_TRANSFORM, "handler", file_path="prep.toml"),
        toml_path="handlers/prep.toml",
    )
    reg.add_handler(
        "run_tsvcpp_mod.assemble_prompt", loads(PREPROC_FORMATTER, "handler", file_path="pp.toml"),
        toml_path="handlers/pp.toml",
    )
    reg.add_composition(
        "trainables/dialogue_svc.toml",
        loads(
            composition_toml, "composition", file_path="trainables/dialogue_svc.toml"
        ),
    )
    pipeline = loads(pipeline_toml, "pipeline", file_path="p.toml")
    deployment = loads(TRAIN_DEPLOYMENT, "deployment", file_path="d.toml")
    runnable = _runnable(pipeline, reg, name="acme.trainsvc", deployment=deployment)
    # Sanity: the failing node IS the engine-constructed trainable bound to "llm".
    trainable_node = runnable.nodes[-1]
    assert trainable_node.node_kind == "trainable"
    assert trainable_node.service_binding_name == "llm"

    with pytest.raises(PipelineFailure) as exc:
        run(runnable, {"raw": "hello"})
    pf = exc.value
    assert pf.failure_category == "service"   # the trainable invoke IS a service-backend call
    assert pf.service_binding_name == "llm"   # service locus -> the failing binding is named
    assert pf.cause_class == "ValueError"     # the underlying exception name, verbatim


SYNTH_SERVICE_TYPE_BACKEND = """
name = "run_synbackend_mod.CertifiedBackend"
[identity_schema]
model = { type = "str" }
[transport_schema]
endpoint = { type = "str" }
[config_schema]
temperature = { type = "float" }
max_tokens = { type = "int" }
"""

SYNTH_COMPOSITION = """
[meta]
kind = "trainable"
name = "synth_training"
[inputs]
user_message = { type = "str" }
[outputs]
dialogue_response = { type = "str" }
[[preprocessors]]
kind = "handler"
name = "run_synpp_mod.enrich"
id   = "enrich"
reads_map = { um = "user_message" }
writes_map = { enriched = "enriched" }
[[preprocessors]]
kind = "handler"
name = "run_synpp_mod.audit_log"
id   = "audit_log"
reads_map = { enriched = "enriched" }
[service_bindings.aux]
type = "run_synaux_adapters.AuxAdapter"
label = "aux-1"
[service_bindings.llm]
type = "run_synbackend_mod.CertifiedBackend"
model = "test-model"
[trainable]
[trainable.config]
temperature = 0.7
max_tokens = 64
[trainable.service_bindings]
llm = { type = "run_synbackend_mod.CertifiedBackend" }
[trainable.reads]
enriched = { type = "str" }
[trainable.output_schema]
dialogue_response = { type = "str" }
"""

SYNTH_PIPELINE = """
[meta]
name = "acme.synth"
[[nodes]]
kind = "handler"
name = "run_synctx_mod.prep"
[[nodes]]
kind = "composition"
name = "trainables/synth.toml"
[inputs]
raw = { type = "str" }
[outputs]
dialogue_response = { type = "str" }
"""

SYNTH_DEPLOYMENT = """
[transport.llm]
endpoint = "https://llm.test/v1"
[transport.aux]
[hook_transport."synth_training.audit_log"]
[training_contract]
integrity_enforcement = false
"""


def test_hook_and_service_preprocessor_kinds_synthesize_and_run(module_dir):
    _write_module(
        module_dir, "run_synctx_mod",
        """
        def prep(*, raw):
            return {"user_message": raw}
        """,
    )
    _write_module(
        module_dir, "run_synpp_mod",
        """
        def enrich(*, um, services):
            return {"enriched": services.aux.invoke(q=um)["r"]}

        def audit_log(*, enriched):
            return None
        """,
    )
    _write_module(
        module_dir, "run_synaux_adapters",
        """
        class AuxAdapter:
            def __init__(self, label):
                self.label = label

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"r": input_payload["q"] + "+" + self.label}
        """,
    )
    _write_module(
        module_dir, "run_synbackend_mod",
        """
        class CertifiedBackend:
            training_artifact_contract = "gguf"
            reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

            def __init__(self, model, *, output_schema, schema_source):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, max_tokens, **transport_extra):
                return {"dialogue_response": "reply:" + input_payload["enriched"]}
        """,
    )
    reg = DeclarationRegistry()
    # Direct IR for the aux type: a zero-transport-field service-type (the parse
    # layer's presence discipline is stage-1 territory, not this test's concern).
    reg.add_service_type(
        ServiceTypeDeclaration(
            name="run_synaux_adapters.AuxAdapter",
            identity_schema=(_fd("label"),),
            transport_schema=(),
        ),
        toml_path="aux.toml",
    )
    reg.add_service_type(
        loads(SYNTH_SERVICE_TYPE_BACKEND, "service_type", file_path="backend.toml"),
        toml_path="backend.toml",
    )
    reg.add_handler(
        "run_synctx_mod.prep",
        _transform((_fd("raw"),), (_fd("user_message"),)),
        toml_path="handlers/prep.toml",
    )
    # The preprocessors are name-references — register the service + hook handlers they resolve.
    reg.add_handler(
        "run_synpp_mod.enrich",
        loads('[service]\n[reads]\num={type="str"}\n[output_schema]\nenriched={type="str"}\n'
              '[service_bindings]\naux={type="run_synaux_adapters.AuxAdapter"}\n',
              "handler", file_path="enrich.toml"),
        toml_path="handlers/enrich.toml",
    )
    reg.add_handler(
        "run_synpp_mod.audit_log",
        loads('[hook]\n[reads]\nenriched={type="str"}\n[service_bindings]\n[transport_schema]\n',
              "handler", file_path="audit.toml"),
        toml_path="handlers/audit.toml",
    )
    reg.add_composition(
        "trainables/synth.toml",
        loads(SYNTH_COMPOSITION, "composition", file_path="trainables/synth.toml"),
    )
    pipeline = loads(SYNTH_PIPELINE, "pipeline", file_path="p.toml")
    deployment = loads(SYNTH_DEPLOYMENT, "deployment", file_path="d.toml")
    runnable = _runnable(pipeline, reg, name="acme.synth", deployment=deployment)
    # The referenced member declarations joined per their RESOLVED kinds (name-reference): the
    # service preprocessor (its referenced handler declares a service binding + output_schema) and
    # the hook preprocessor (its referenced handler declares no output_schema) — both live branches.
    assert [n.node_kind for n in runnable.nodes] == [
        "transform", "service", "hook", "trainable",
    ]
    # The service member consumed the COMPOSITION's own [service_bindings.aux]
    # supply (the composition-member supply join); the hook member binds nothing.
    assert runnable.nodes[1].service_binding_name == "aux"
    assert runnable.nodes[2].service_binding_name is None
    result = run(runnable, {"raw": "hello"})
    assert dict(result.state)["dialogue_response"] == "reply:hello+aux-1"


# verifies: explicit-null-delivers-none
def test_explicit_null_transport_value_delivers_python_none(module_dir):
    """End-to-end through the real dispatch path: a nullable transport field supplied as
    `{ null = true }` composes, and the adapter's `**transport_extra` receives the field
    PRESENT with value `None` — never the reserved table, never absent (the explicit-null
    delivery half of handler/reference.md's explicit-null region; the same
    `_deliver_transport_values` resolution serves the hook-body kwarg arm)."""
    _write_module(
        module_dir, "run_nulldeliv_mod",
        """
        def talk(*, um, services):
            return {"r": services.nd.invoke(q=um)["r"]}
        """,
    )
    _write_module(
        module_dir, "run_nulldeliv_adapters",
        """
        class NullDeliv:
            def __init__(self, label):
                self.label = label

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                got = transport_extra.get("api_key", "ABSENT")
                return {"r": "delivered-none" if got is None else f"unexpected:{got!r}"}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_service_type(
        loads(
            'name="run_nulldeliv_adapters.NullDeliv"\n'
            '[identity_schema]\nlabel={type="str"}\n'
            '[transport_schema]\nendpoint={type="str"}\napi_key={type="str | None", nullable=true}\n'
            "[config_schema]\n",
            "service_type", file_path="nd.toml"),
        toml_path="nd.toml",
    )
    reg.add_handler(
        "run_nulldeliv_mod.talk",
        loads('[service]\n[reads]\num={type="str"}\n[output_schema]\nr={type="str"}\n'
              '[service_bindings]\nnd={type="run_nulldeliv_adapters.NullDeliv"}\n',
              "handler", file_path="talk.toml"),
        toml_path="handlers/talk.toml",
    )
    pipeline = loads(
        '[meta]\nname="acme.nulldeliv"\n[[nodes]]\nkind="handler"\nname="run_nulldeliv_mod.talk"\n'
        '[service_bindings.nd]\ntype="run_nulldeliv_adapters.NullDeliv"\nlabel="nd-1"\n'
        '[inputs]\num={type="str"}\n[outputs]\nr={type="str"}\n',
        "pipeline", file_path="p.toml")
    deployment = loads(
        '[transport.nd]\nendpoint="https://nd.test"\napi_key={null=true}\n'
        "[training_contract]\nintegrity_enforcement=false\n",
        "deployment", file_path="d.toml")
    runnable = _runnable(pipeline, reg, name="acme.nulldeliv", deployment=deployment)
    result = run(runnable, {"um": "hi"})
    assert dict(result.state)["r"] == "delivered-none"


# verifies: explicit-null-delivers-none
def test_explicit_null_hook_transport_value_delivers_python_none(module_dir, tmp_path):
    """The hook-body kwarg arm of the same delivery resolution: hook transport fields are
    engine-read and delivered as kwargs into the hook BODY, so a nullable field supplied as
    `{ null = true }` must arrive as Python None — never the raw reserved table (RED if
    `_transport_for_hook_body` stops resolving through `_deliver_transport_values`)."""
    out_file = tmp_path / "marker.txt"
    _write_module(
        module_dir, "run_nullhook_mod",
        """
        def shout(*, um):
            return {"r": um.upper()}

        def note(*, r, sink, marker):
            with open(sink, "a", encoding="utf-8") as f:
                f.write(repr(marker))
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "run_nullhook_mod.shout",
        loads('[transform]\n[reads]\num={type="str"}\n[output_schema]\nr={type="str"}\n',
              "handler", file_path="shout.toml"),
        toml_path="handlers/shout.toml",
    )
    reg.add_handler(
        "run_nullhook_mod.note",
        loads('[hook]\n[reads]\nr={type="str"}\n[service_bindings]\n'
              '[transport_schema]\nsink={type="str"}\nmarker={type="str | None"}\n',
              "handler", file_path="note.toml"),
        toml_path="handlers/note.toml",
    )
    pipeline = loads(
        '[meta]\nname="acme.nullhook"\n'
        '[[nodes]]\nkind="handler"\nname="run_nullhook_mod.shout"\n'
        '[[nodes]]\nkind="handler"\nname="run_nullhook_mod.note"\n'
        '[inputs]\num={type="str"}\n[outputs]\nr={type="str"}\n',
        "pipeline", file_path="p.toml")
    deployment = loads(
        f'[hook_transport."run_nullhook_mod.note"]\nsink="{out_file.as_posix()}"\n'
        "marker={null=true}\n"
        "[training_contract]\nintegrity_enforcement=false\n",
        "deployment", file_path="d.toml")
    runnable = _runnable(pipeline, reg, name="acme.nullhook", deployment=deployment)
    result = run(runnable, {"um": "hi"})
    assert dict(result.state)["r"] == "HI"
    assert out_file.read_text(encoding="utf-8") == "None"
