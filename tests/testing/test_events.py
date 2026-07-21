"""Compositional verification — capture the canonical event stream and read node state from it.

The verification path is the real dispatch path (R-testing-001); the event stream is how a test reads
it. inspect_state reads a node's reads/writes snapshot off the captured stream, never engine internals.
"""

from __future__ import annotations

import pytest

from conjured.errors import PipelineFailure
from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.handler import TransformDeclaration
from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
from conjured.runner.run import run
from conjured.testing import capture_events, inspect_state, load_test_pipeline, run_and_capture


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


def test_run_and_capture_returns_result_and_stream(chain):
    result, events = run_and_capture(chain.runnable, {"text": "hi"})
    # state carries every outer-written channel (mid + out), not just the declared [outputs].
    assert dict(result.state) == {"mid": "HI", "out": "HI!"}
    kinds = {type(e).__name__ for e in events}
    assert {"PipelineStart", "HandlerEnter", "HandlerExit", "PipelineComplete"} <= kinds


def test_inspect_state_reads_and_writes_per_position(chain):
    _result, events = run_and_capture(chain.runnable, {"text": "hi"})
    first = inspect_state(events, 0)
    assert first.node_kind == "transform"
    assert first.reads == {"text": "hi"}
    assert first.writes == {"mid": "HI"}
    assert first.service_input is None and first.service_output is None
    second = inspect_state(events, 1)
    assert second.reads == {"mid": "HI"}
    assert second.writes == {"out": "HI!"}


def test_inspect_state_absent_position_raises(chain):
    _result, events = run_and_capture(chain.runnable, {"text": "hi"})
    with pytest.raises(LookupError):
        inspect_state(events, 99)


def test_capture_events_is_synchronous_and_detaches(chain):
    with capture_events() as events:
        run(chain.runnable, {"text": "x"})
    # Synchronous delivery: the full position-ordered stream is present on return.
    assert [e.handler_position for e in events if type(e).__name__ == "HandlerEnter"] == [0, 1]
    # After the block the handler is detached: a second run does not append to the old list.
    before = len(events)
    run(chain.runnable, {"text": "y"})
    assert len(events) == before


def test_capture_events_confines_events_no_propagation_to_ancestors(chain):
    """TESTING-F1 — `capture_events` sets `logger.propagate = False` so the INFO-level canonical
    events (it raises the `conjured.events.runner` level to INFO to capture them) stay CONFINED to
    its own consumer handler and do NOT propagate to ANCESTOR handlers (the parent `conjured.events`
    logger, and through root pytest's caplog — the reference names caplog the *companion* surface
    for the engine's own warnings, NOT the canonical event stream). Attach a sentinel handler to
    the parent `conjured.events` logger, run a real pipeline inside the block, and assert the
    sentinel received NOTHING while `capture_events` captured the full stream. RED if
    `logger.propagate = False` is removed: every captured event would ALSO propagate up to the
    ancestor sentinel (this seal had no own adversary)."""
    import logging

    seen: list[object] = []

    class _Sentinel(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.msg)

    ancestor = logging.getLogger("conjured.events")  # the parent of conjured.events.runner
    sentinel = _Sentinel()
    ancestor.addHandler(sentinel)
    try:
        with capture_events() as events:
            run(chain.runnable, {"text": "x"})
        assert events  # the confined consumer handler captured the stream
        assert seen == []  # nothing propagated to the ancestor logger — the propagate=False seal
    finally:
        ancestor.removeHandler(sentinel)


def test_capture_events_observes_the_error_path(conjured_registry, module_writer):
    # The documented error-path use: capture around a halting run so the partial stream — including
    # the terminal pipeline_error event — is still observable (R-testing-005 error path).
    module = module_writer("ev_err_mod", "def boom(*, text):\n    raise RuntimeError('nope')\n")
    conjured_registry.add_handler(
        f"{module}.boom",
        TransformDeclaration(reads=(_fd("text"),), output_schema=(_fd("out"),)),
        toml_path="h.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="ev.err"),
        nodes=(HandlerNode(name=f"{module}.boom"),),
        inputs=(_fd("text"),), outputs=(_fd("out"),),
    )
    runnable = load_test_pipeline(pipeline, conjured_registry)
    with capture_events() as events:
        with pytest.raises(PipelineFailure):
            run(runnable, {"text": "hi"})
    kinds = {type(e).__name__ for e in events}
    assert "PipelineStart" in kinds and "PipelineError" in kinds  # partial stream survived the halt
