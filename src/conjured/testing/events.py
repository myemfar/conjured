"""Compositional verification — observe a run through the canonical event stream.

The contract: ``conjured/docs/components/testing/reference.md`` § Compositional verification.
A test never inspects engine internals to confirm a handler ran correctly under dispatch; it
observes the **canonical event stream** (``conjured.events.runner``). The engine ships no
``logging.Handler`` (producer/consumer) — a test attaches its own and reads the typed event
objects off ``record.msg``. Delivery is synchronous, so on ``run`` return the captured list
already holds the full position-ordered stream.

This module generalises the ``_capture_events`` helper that was duplicated across the engine's
own suite into one public capture context manager, plus ``inspect_state`` — the read over the
event stream that yields a node's ``reads_snapshot`` / ``writes_snapshot`` (the training pair),
keyed by ``handler_position`` (a qualified name is not unique under multi-dispatch).
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, TypeVar

from conjured.events import (
    CanonicalEvent,
    HandlerEnter,
    HandlerExit,
    NodeKind,
    ServiceInvocation,
    subscribe,
)
from conjured.runner.run import RunResult, run
from conjured.testing.errors import AmbiguousServiceCapture

#: The handler-position-keyed event kinds ``_one`` selects by (both carry ``handler_position``).
_PositionKeyedEvent = TypeVar("_PositionKeyedEvent", HandlerEnter, HandlerExit)


@contextlib.contextmanager
def capture_events() -> Iterator[list[CanonicalEvent]]:
    """Capture the canonical event stream for the duration of the ``with`` block.

    Attaches a consumer ``logging.Handler`` to ``conjured.events.runner`` (the engine attaches
    none), yields the list the events accumulate into, and detaches on exit. The event object
    rides as the ``LogRecord.msg`` (never string-formatted), so each captured item is a
    :data:`~conjured.events.CanonicalEvent` instance. Because ``logging`` delivers synchronously,
    the list holds the complete position-ordered stream the moment the dispatched ``run`` returns.
    """
    captured: list[CanonicalEvent] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.msg)  # type: ignore[arg-type]

    # The channel owner's block-scoped subscription (attach + raise-to-INFO + propagation
    # confined to this handler so the INFO-level events don't flood ancestor handlers —
    # root, and pytest's caplog, the testing reference's *companion* surface for the
    # engine's own warnings — all restored on exit).
    with subscribe(_Capture()):
        yield captured


def run_and_capture(
    runnable, inputs: Mapping[str, object], **run_kwargs
) -> tuple[RunResult, list[CanonicalEvent]]:
    """Happy-path convenience: dispatch ``runnable`` through the real engine runner and return
    ``(result, events)``. The dispatch goes through the real path — never a bare call — so the
    returned events are the same stream the engine would emit in production. For an error path
    (asserting the run halts), use :func:`capture_events` around ``run`` under ``pytest.raises``
    so the partial stream (including ``pipeline_error``) is still observable.
    """
    with capture_events() as events:
        result = run(runnable, inputs, **run_kwargs)
    return result, events


@dataclass(frozen=True, slots=True)
class NodeState:
    """One dispatched node's observed state, read from the event stream — the training pair the
    engine logged plus, for a service dispatch, the wire-visible service payloads.

    ``reads`` is the ``handler_enter`` ``reads_snapshot`` (the projection over the node's declared
    ``reads`` input ports). ``writes`` is the ``handler_exit`` ``writes_snapshot`` (the projection
    over its declared ``output_schema`` output ports, taken before the write-map) — **None for a
    hook**, which writes no channels. ``service_input`` / ``service_output`` are the
    ``service_invocation`` payloads (the consumer-side R-handler-002 divergence signal) — present
    only for a service dispatch, else None.
    """

    position: int
    qualified_name: str
    node_kind: NodeKind
    reads: Mapping[str, object]
    writes: Mapping[str, object] | None
    service_input: Mapping[str, object] | None = None
    service_output: Mapping[str, object] | None = None


def inspect_state(events: Iterable[CanonicalEvent], position: int) -> NodeState:
    """Read the dispatched node at ``position`` out of a captured event stream.

    The node is selected by ``handler_position`` — the total order over a run's dispatches — not
    by qualified name, which is not unique when the same handler is dispatched at more than one
    position. Raises ``LookupError`` when no dispatch at ``position`` is present in ``events``
    (a missing pair is a hole the consumer should see, never a silent empty result).
    """
    events = list(events)
    enter = _one(events, HandlerEnter, position)
    exit_ = _one(events, HandlerExit, position)
    if enter is None or exit_ is None:
        raise LookupError(
            f"no complete handler_enter/handler_exit pair at position {position} in the captured "
            f"stream (positions present: {sorted(_positions(events))}). The dispatch may not have "
            f"run, or the run halted before it."
        )
    service = _service_at(events, position)
    return NodeState(
        position=position,
        qualified_name=enter.handler_qualified_name,
        node_kind=enter.node_kind,
        reads=enter.reads_snapshot,
        writes=exit_.writes_snapshot,
        service_input=None if service is None else service.input_payload,
        service_output=None if service is None else service.output_payload,
    )


def _one(
    events: list[CanonicalEvent], cls: type[_PositionKeyedEvent], position: int
) -> _PositionKeyedEvent | None:
    for event in events:
        if isinstance(event, cls) and event.handler_position == position:
            return event
    return None


def _service_at(events: list[CanonicalEvent], position: int) -> ServiceInvocation | None:
    """The single ``service_invocation`` at ``position``, or ``None`` when the node made no service
    call. Unlike :func:`_one` (which returns the first match), this **fails loud** on more than one:
    a service node makes exactly one external call per dispatch (``handler-kinds.md`` § Service), so
    a second capture at one position is never steady state — silently keeping one arbitrary event
    would launder a buried multi-call (or a capture bug) into a clean single-invocation record."""
    matches = [
        e for e in events if isinstance(e, ServiceInvocation) and e.handler_position == position
    ]
    if len(matches) > 1:
        raise AmbiguousServiceCapture(
            f"{len(matches)} service_invocation events at handler_position {position} — a service "
            f"makes exactly one external call per dispatch (handler-kinds.md § Service); more than "
            f"one signals a buried multi-call or a capture bug, never steady state."
        )
    return matches[0] if matches else None


def _positions(events: list[CanonicalEvent]) -> set[int]:
    return {e.handler_position for e in events if isinstance(e, HandlerEnter)}
