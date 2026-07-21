"""``conjured.events`` — the Canonical event log component (C4).

The engine publishes its **closed enum** of canonical events on the Python ``logging``
channel ``conjured.events.runner`` (``conjured/docs/architecture/components.md``
§ Canonical event log). Producer/consumer by construction: the engine **emits**; it ships
**no** ``logging.Handler`` implementations — in-process consumers attach their own handler
to the logger; the server projects the stream onto the wire. **Training capture is one such
consumer use** — the engine's whole capture responsibility is *emitting* a complete,
correctly-shaped, position-ordered stream (the emit-not-write split:
``conjured/docs/architecture/components.md`` § Canonical event log); persistence lives in the
first-party companion package ``conjured-utils`` (the reference sink), never the engine.

Each event is a frozen, typed payload matching its declared shape at
``conjured/docs/architecture/hash-model.md`` § Canonical event types. The set is **closed**:
adding or changing an event is a contract amendment (an engine change), never a runtime
extension — enforced here by ``emit`` rejecting any non-member object. The cross-event key is
``(pipeline_run_id, handler_position)``; ``handler_position`` is a total order over a run's
dispatches, so a consumer reconstructs the expected sequence and detects a hole. The event
stream is the runner's **primary output** (runner-build transfer 5).
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar, Iterator, Literal, Mapping, Union

from conjured.errors import snapshot_copy

# The canonical event channel. The engine emits here; it attaches no handlers.
EVENT_LOGGER_NAME = "conjured.events.runner"

# Snapshots/payloads are projections of channel state restricted to declared interfaces —
# read-only mappings the runner already computes at each boundary.
Snapshot = Mapping[str, object]
#: The closed set of runtime node kinds (the ``node_kind`` event-payload member). THE single
#: source — ``conjured.ir.graph`` composes over this in ``GraphNodeKind`` (widening it with the
#: graph-only ``"pipeline"`` member) rather than re-declaring the literal, so the member list is
#: owned in exactly one place.
NodeKind = Literal["transform", "service", "hook", "trainable"]


class CanonicalEventType(Enum):
    """The closed enum of canonical event types (``hash-model.md`` § Canonical event types).
    Membership is the contract; extending it is an engine change, not a runtime extension."""

    PIPELINE_START = "pipeline_start"
    HANDLER_ENTER = "handler_enter"
    HANDLER_EXIT = "handler_exit"
    SERVICE_INVOCATION = "service_invocation"
    PIPELINE_COMPLETE = "pipeline_complete"
    PIPELINE_ERROR = "pipeline_error"
    TRAINING_BUNDLE_HASH_CHANGED = "training_bundle_hash_changed"
    PIPELINE_HASH_CHANGED = "pipeline_hash_changed"


# ── The eight canonical events ───────────────────────────────────────────────────────────
# Field sets are per-event verbatim from hash-model.md § Canonical event types. `timestamp`
# is ISO-8601 UTC; `pipeline_hash` is `sha256:<hex>`; positions/elapsed are ints; snapshots
# are restricted-interface projections. Nullable fields are `None` when the table says absent.
#
# A retained event object is an IMMUTABLE RECORD by construction. Each event that carries a
# mapping payload materializes its OWN copy in ``__post_init__`` (a frozen dataclass, so via
# ``object.__setattr__``), so neither later channel evolution nor a handler-body mutation of a
# shared projection can rewrite an already-emitted event — the event stream IS the training
# record (I4). The copy is ``snapshot_copy`` (the SAME materializer ``PipelineFailure`` uses on
# its snapshots, ``errors.py`` ``guarantees: pf-snapshot-deepcopy``): it REBUILDS the mutable
# container forms (dict / list / set) — decoupling them from the runner's live channel state —
# but passes non-container leaves BY REFERENCE. That leaf-by-reference behavior is load-bearing,
# not incidental: a validated channel value MAY be a non-deep-copyable immutable ``str`` subclass
# (the closed-type-leaf escape, trust-model § Vector 4), and a value the runner writes by
# reference (``_route_writes``) is copied only later at a downstream reader's engine-locus
# read-side copy — so a blanket ``copy.deepcopy`` here would (a) raise on an immutable leaf that
# never needed copying and (b) pre-empt that engine-locus copy, mis-attributing the failure.
# ``snapshot_copy`` sidesteps both: immutable leaves need no copy (mutation-safety is only about
# the containers), and the runner's own copy loci keep their failure attribution.
# The single exception is ``ServiceInvocation`` (see its note): its payloads are captured at the
# adapter boundary — the canon-mandated moment — which construction time is too late to be, so it
# does NOT re-copy here.


@dataclass(frozen=True, slots=True)
class PipelineStart:
    """Per pipeline run, after pipeline-level inputs load, before the first dispatch."""

    EVENT_TYPE: ClassVar[CanonicalEventType] = CanonicalEventType.PIPELINE_START
    pipeline_run_id: str
    pipeline_hash: str
    timestamp: str
    inputs_snapshot: Snapshot
    # `parent_run_id`: the enclosing run's id on an inner run of a nested `pipeline`
    # embed; `None` for a top-level run (hash-model § Canonical event types).
    parent_run_id: str | None = None

    def __post_init__(self) -> None:
        # guarantees: event-payload-deepcopy
        object.__setattr__(self, "inputs_snapshot", snapshot_copy(self.inputs_snapshot))


@dataclass(frozen=True, slots=True)
class HandlerEnter:
    """Per node dispatch, before body invocation (for trainable composition nodes: before the
    engine-constructed ``adapter.invoke``). ``reads_snapshot`` IS the training-pair input side."""

    EVENT_TYPE: ClassVar[CanonicalEventType] = CanonicalEventType.HANDLER_ENTER
    handler_qualified_name: str
    handler_position: int
    node_kind: NodeKind
    pipeline_run_id: str
    timestamp: str
    reads_snapshot: Snapshot

    def __post_init__(self) -> None:
        # guarantees: event-payload-deepcopy
        # `reads_snapshot` IS the training-pair input side; the runner shares the per-dispatch
        # reads projection with the handler body, so the event owns its own copy — a legal
        # in-place mutation of the reads dict by the body cannot rewrite this retained record.
        object.__setattr__(self, "reads_snapshot", snapshot_copy(self.reads_snapshot))


@dataclass(frozen=True, slots=True)
class HandlerExit:
    """Per node dispatch, after the body completes (for trainable composition nodes: after
    ``adapter.invoke`` returns). ``writes_snapshot`` IS the training-pair output side; ``None``
    for hooks (they write no channels). ``correlation_id`` present only for service dispatches."""

    EVENT_TYPE: ClassVar[CanonicalEventType] = CanonicalEventType.HANDLER_EXIT
    handler_qualified_name: str
    handler_position: int
    node_kind: NodeKind
    elapsed_ms: int
    pipeline_run_id: str
    timestamp: str
    writes_snapshot: Snapshot | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        # The two payload presence rules canon pins for handler_exit (hash-model.md § Canonical
        # event types) become structural constructor seals here — a mismatch is a
        # runner-construction bug that must fail loud, never emit a malformed training record.
        # This mirrors the presence-iff seal PipelineFailure carries on its own payload
        # (errors.py, guarantees: pf-service-binding-iff-service).
        # guarantees: event-exit-writes-iff-hook
        # writes_snapshot is present for transforms / services / trainable composition nodes and
        # absent (None) for hooks (which return None and write no channels by contract).
        if (self.writes_snapshot is None) != (self.node_kind == "hook"):
            raise ValueError(
                "handler_exit writes_snapshot must be None iff node_kind == 'hook' (got "
                f"node_kind={self.node_kind!r}, writes_snapshot={'None' if self.writes_snapshot is None else 'present'}; "
                "hash-model.md § Canonical event types)"
            )
        # guarantees: event-exit-correlation-iff-service
        # correlation_id pairs a service dispatch to its service_invocation; present iff the node
        # is a service (transforms / hooks / trainable nodes emit no service_invocation to pair).
        if (self.correlation_id is not None) != (self.node_kind == "service"):
            raise ValueError(
                "handler_exit correlation_id must be non-None iff node_kind == 'service' (got "
                f"node_kind={self.node_kind!r}, correlation_id={self.correlation_id!r}; "
                "hash-model.md § Canonical event types)"
            )
        # guarantees: event-payload-deepcopy
        # `writes_snapshot` IS the training-pair output side; the routed result dict evolves the
        # channel state downstream, so the event owns its own copy (None stays None for hooks).
        if self.writes_snapshot is not None:
            object.__setattr__(self, "writes_snapshot", snapshot_copy(self.writes_snapshot))


@dataclass(frozen=True, slots=True)
class ServiceInvocation:
    """**Service-kind only.** Per ``services.<name>.invoke(...)``, captured at the adapter
    boundary. ``input_payload``/``output_payload`` is the wire-visible record of what the adapter
    submitted vs what the backend returned (the consumer-side R-handler-002 divergence signal)."""

    # No ``__post_init__`` deep-copy (deliberate, unlike the sibling lifecycle events): the
    # ``input_payload`` / ``output_payload`` are captured — deep-copied — at the adapter boundary
    # (``dispatch.py``, ``guarantees: service-payload-deepcopy``; hash-model.md § Canonical event
    # types: ``output_payload`` is captured BEFORE any handler-body transformation). That boundary
    # IS the correct capture moment, and it precedes this construction, so the payloads reaching
    # here are already this event's own immutable copies — a construction-time re-copy would be
    # both redundant and, as the capture point, too late.
    EVENT_TYPE: ClassVar[CanonicalEventType] = CanonicalEventType.SERVICE_INVOCATION
    handler_qualified_name: str
    handler_position: int
    input_payload: Snapshot
    output_payload: Snapshot
    pipeline_hash: str
    elapsed_ms: int
    pipeline_run_id: str
    timestamp: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class PipelineComplete:
    """Pipeline run reaches happy-path termination. ``outputs_snapshot`` is the projection
    restricted to the pipeline-declared outputs (``{}`` when no ``[outputs]`` block)."""

    EVENT_TYPE: ClassVar[CanonicalEventType] = CanonicalEventType.PIPELINE_COMPLETE
    pipeline_hash: str
    pipeline_run_id: str
    elapsed_ms: int
    timestamp: str
    outputs_snapshot: Snapshot

    def __post_init__(self) -> None:
        # guarantees: event-payload-deepcopy
        # `outputs_snapshot` projects live channel state at termination; the event owns its own
        # copy so post-run mutation of the returned state cannot rewrite this retained record.
        object.__setattr__(self, "outputs_snapshot", snapshot_copy(self.outputs_snapshot))


@dataclass(frozen=True, slots=True)
class PipelineError:
    """A pipeline run halts at runtime (any error class per R-error-channel-001). A load/compose
    ``ContractViolation`` halts before a run is in flight and does NOT fire this."""

    EVENT_TYPE: ClassVar[CanonicalEventType] = CanonicalEventType.PIPELINE_ERROR
    pipeline_hash: str
    pipeline_run_id: str
    elapsed_ms: int
    timestamp: str
    error_class: str  # the closed error-class enum member name
    failed_handler_qualified_name: str
    failed_handler_position: int
    error_message: str
    cause_class: str | None = None  # present when error_class == "PipelineFailure"
    failure_category: str | None = None  # closed locus enum; present when error_class == "PipelineFailure"

    def __post_init__(self) -> None:
        # guarantees: event-error-cause-fields-iff-pf
        # cause_class and failure_category are the PipelineFailure-only payload members
        # (hash-model.md § Canonical event types): present iff error_class == "PipelineFailure".
        # A ContractViolation / SchemaValidationError halt IS its own structural cause and carries
        # neither. A mismatch is a runner-construction bug — fail loud (the structural form of the
        # presence rule, mirroring the PipelineFailure constructor seal in errors.py).
        is_pf = self.error_class == "PipelineFailure"
        if (self.cause_class is not None) != is_pf:
            raise ValueError(
                "pipeline_error cause_class must be non-None iff error_class == 'PipelineFailure' "
                f"(got error_class={self.error_class!r}, cause_class={self.cause_class!r}; "
                "hash-model.md § Canonical event types)"
            )
        if (self.failure_category is not None) != is_pf:
            raise ValueError(
                "pipeline_error failure_category must be non-None iff error_class == 'PipelineFailure' "
                f"(got error_class={self.error_class!r}, failure_category={self.failure_category!r}; "
                "hash-model.md § Canonical event types)"
            )


@dataclass(frozen=True, slots=True)
class TrainingBundleHashChanged:
    """Compose-time: a trainable's training-bundle-hash differs from a loaded manifest's value."""

    EVENT_TYPE: ClassVar[CanonicalEventType] = CanonicalEventType.TRAINING_BUNDLE_HASH_CHANGED
    trainable_qualified_name: str
    new_training_bundle_hash: str
    pipeline_hash: str
    timestamp: str
    old_training_bundle_hash: str | None = None  # absent on first observation


@dataclass(frozen=True, slots=True)
class PipelineHashChanged:
    """Compose-time: pipeline-hash differs from a loaded manifest's recorded value.

    ``old_pipeline_hash`` is **required, non-nullable** — in deliberate contrast to
    ``TrainingBundleHashChanged.old_training_bundle_hash`` (nullable, absent on first
    observation). The asymmetry is canon's (``hash-model.md`` § Canonical event types: the
    ``pipeline_hash_changed`` row carries ``old_pipeline_hash`` with no nullable annotation):
    this event fires only when a loaded manifest's ``pipeline_hash_set`` differs from the
    deployed pipeline's current value, and a missing manifest fires **no** event at all
    (§ Enforcement off), so a recorded prior value is always present when this fires. A single
    trainable can be newly added to a pipeline that already has a manifest baseline (the
    first-observation case the TBH event annotates), but the pipeline-as-a-whole always has a
    recorded baseline whenever a comparison happens."""

    EVENT_TYPE: ClassVar[CanonicalEventType] = CanonicalEventType.PIPELINE_HASH_CHANGED
    new_pipeline_hash: str
    timestamp: str
    old_pipeline_hash: str


CanonicalEvent = Union[
    PipelineStart,
    HandlerEnter,
    HandlerExit,
    ServiceInvocation,
    PipelineComplete,
    PipelineError,
    TrainingBundleHashChanged,
    PipelineHashChanged,
]

#: The exported closed member set — the in-process class roster of the eight canonical
#: events, the one membership surface a downstream consumer tests an event object against
#: (``architecture/components.md`` § Canonical event log owns the attachment/consumer
#: surface; the per-event payload shapes stay hash-model's). The runtime form of "adding
#: an event is a contract amendment".
CANONICAL_EVENT_CLASSES: tuple[type, ...] = (
    PipelineStart,
    HandlerEnter,
    HandlerExit,
    ServiceInvocation,
    PipelineComplete,
    PipelineError,
    TrainingBundleHashChanged,
    PipelineHashChanged,
)


def now_iso() -> str:
    """ISO-8601 UTC wall-clock stamp for a canonical event's ``timestamp`` field — the
    single timestamp format across every emit site (the runner's run-lifecycle + the
    adapter boundary's ``service_invocation``), so the stream's timestamps never drift in
    form."""
    return datetime.now(timezone.utc).isoformat()


def event_logger() -> logging.Logger:
    """The canonical event channel. Consumers attach handlers here; the engine attaches none."""
    return logging.getLogger(EVENT_LOGGER_NAME)


def attach_consumer(handler: logging.Handler) -> None:
    """The **long-lived** channel attachment (a served engine's hub, a training-log sink):
    attach ``handler`` to the canonical event channel and ensure the channel delivers the
    INFO-level canonical events. ``propagate`` is left untouched — a long-lived consumer is
    a leaf; the engine's own warnings on the parent ``conjured.events`` logger are a
    separate surface. Detach with ``event_logger().removeHandler(handler)``.

    The channel-management surface is owned HERE (the channel's home) so every consumer
    subscribes one way — for a block-scoped consumer (a test capture, a scoped exporter)
    use :func:`subscribe`, which also confines the events to the handler and restores the
    channel on exit."""
    logger = event_logger()
    logger.addHandler(handler)
    if logger.level > logging.INFO or logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)


@contextlib.contextmanager
def subscribe(handler: logging.Handler) -> "Iterator[logging.Handler]":
    """The **block-scoped** channel subscription: attach ``handler``, raise the channel to
    INFO, and disable propagation for the block — without the propagation guard, raising
    the level lets every INFO-level canonical event flood ancestor handlers (root, pytest's
    caplog) — then restore all three on exit. The one subscription recipe every
    block-scoped consumer shares (the testing capture, the OTel exporter); a long-lived
    consumer uses :func:`attach_consumer` instead."""
    logger = event_logger()
    logger.addHandler(handler)
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def emit(event: CanonicalEvent) -> None:
    """Publish one canonical event on ``conjured.events.runner`` — the engine's whole capture
    responsibility (it persists nothing).

    Two failure surfaces, partitioned by **whose fault it is** (components.md § Canonical event
    log — the engine fails loud on an *emit* failure only):

    - **Engine-internal emit failure → fail loud.** A non-member object is a closed-enum
      violation and raises ``TypeError`` rather than silently publishing an unshaped record
      (training-corpus integrity is shape, not durability — I4). This is the engine's bug.
    - **Consumer-handler fault during delivery → isolated, never enters the run.** A consumer's
      ``logging.Handler`` that raises is the consumer's concern, *invisible to the engine*: it
      MUST NOT propagate into the walk. The stdlib does not provide this wall across the
      supported Python range (``Handler.handle`` calls ``emit`` with no guard), so the **producer**
      owns it here. Were it to propagate, a consumer bug would (a) be caught by the runner's
      dispatch-boundary ``except`` and laundered into a false ``PipelineFailure`` mis-attributed
      to an innocent author handler, or (b) mask the real cause on the ``pipeline_error`` path —
      corrupting the very provenance the event log exists to provide.
    """
    # guarantees: emit-closed-enum-reject
    # Exact-type membership, not isinstance: a subclass of a canonical event carries a
    # *changed* shape riding under the parent's identity — the unshaped record the closed
    # enum exists to reject (I4). The enum is closed by construction (nothing in the engine
    # subclasses an event), so exact-type is as strict as the docstring and the
    # emit-closed-enum-reject guarantee already claim.
    if type(event) not in CANONICAL_EVENT_CLASSES:
        raise TypeError(
            f"not a canonical event: {type(event).__name__}. The event enum is closed "
            f"(hash-model.md § Canonical event types); adding a member is an engine change."
        )
    # The event object rides as the LogRecord's msg — structured consumers read `record.msg`;
    # a default formatter renders its repr. No string interpolation (the payload is the message).
    # guarantees: emit-consumer-isolated
    try:
        event_logger().info(event)
    except Exception as exc:  # noqa: BLE001 — a consumer handler raised; isolate it from the run
        # Surfaced on the operational `conjured.events` logger (the package parent, NOT the
        # `.runner` event channel — a consumer's channel handler is on the child and never
        # re-receives this), so a consumer can still see their handler failed; the surfacing is
        # itself guarded so even a raising operational handler cannot re-enter the run.
        try:
            logging.getLogger("conjured.events").warning(
                "a consumer handler on %r raised during event delivery and was isolated "
                "(the producer/consumer wall — a consumer fault never halts the run): %r",
                EVENT_LOGGER_NAME, exc,
            )
        except Exception:  # noqa: BLE001 — last-resort: never let isolation itself raise
            pass
