"""The kernel walk — ``conjured.runner.run(...)`` (Phase 3).

``(reduce (fn [state h] (merge state (h state))) initial-state handlers)`` —
``conjured/docs/components/pipeline/reference.md`` § Kernel semantics, realized over a
:class:`~conjured.runner.assemble.Runnable`: per-run channel state scoped to this
invocation's closure (nothing carried across invocations); declared order the only
sequencing mechanism; per node, project the declared input ports from their
read-map-wired channels — **validate-then-copy**: a projection that includes a
consumer-seeded input channel validates the RAW seeded value at the reads boundary
first (the D2-ruled SVE catch point — a non-deep-copyable wrong-typed seed surfaces
as the structured ``SchemaValidationError``, never a raw ``TypeError``), then the
vector-4 read-side **deep copy** runs per dispatch inside the wrap boundary
(trust-model § Vector 4) — dispatch through the Phase-2 wrapper, route the validated
return via the write-map. The public name + signature are pinned:
``run(runnable, inputs, *, pipeline_run_id=None, timeout_ms=None) -> RunResult``.

**The API boundary** (R-pipeline-001 ``api-inputs-enforcement``; R-error-channel-001
key-set routing): with declared ``[inputs]``, a missing declared
field raises ``ContractViolation`` before any node dispatches — the run never starts.
The check is **presence-only** (value shape surfaces at the first reader's
reads-projection SVE — the load-bearing dispatch-time layer), and **undeclared extras
are inert**: never seeded, never an error; the missing-field CV's message names any
unrecognized keys present (diagnostic only).

**The error channel at the dispatch boundary** (R-error-channel-001 wrap guarantee):
``ContractViolation`` / ``SchemaValidationError`` re-raise and halt — including from
hooks (graph-shape failures); any other uncaught exception wraps into
``PipelineFailure`` — no fourth class escapes. ``cause_class`` AND ``cause_message``
come from ``FieldValidatorFailure.__cause__`` when the exception is the
validator-failure carrier (the payload pair names one underlying exception;
``FieldValidatorFailure`` itself for a verdict-protocol break — the N1 obligation);
``service_binding_name`` is set iff the node carries a service binding and the cause is
in the well-known service table (ServiceError / TimeoutError). A **hook's** wrapped PF
is absorbed (§ Hook-wrapper sanction — the partition is by error class, never a
network-vs-bug judgment): WARNING on logger ``conjured.runner`` carrying the PF's key
fields, then execution continues — a hook writes no channels, so nothing downstream
can corrupt.

**Inline merge — fold-as-you-walk over the channel's contributors** (R-pipeline-002
runtime region; the Phase-3 D1 ruling + the ruled contributor model): a channel's
contributors are its seed (iff a declared ``[inputs]`` channel) plus its node writes,
in graph order — the ONE shared derivation (``ir/graph.channel_contributors``) the
compose-time merge-requirement count also consumes. The runner folds each contributor
into the merged channel's current value under the declared strategy as it walks — the
seed is the fold's first element; within one node, writes fold in the node's declared
write-map (output-schema) order, never the handler body's return-dict insertion order.
A reader's projection is the strategy's left-fold over the contributors upstream of
its position (never empty — the input-closure check guarantees ≥ 1 contributor); the
final value is the fold over all contributors. Merges are runner operations — no
synthesized node, no event. Folds build fresh values (the single-assignment property's
runtime face: channel state is replaced, never mutated in place).

**The vector-3 runtime layer** (R-handler-pure-module ``/enforcement``):
a shallow snapshot of the resolved function's defining module ``__dict__`` before the
body call (bare-function kinds only); after — on every exit path — diff
added / deleted / rebound names, rebind from the snapshot, re-verify. A clean full
revert continues with a WARNING naming the handler and the reverted names; a restore
that raises or fails re-verification **halts** with ContractViolation
(``HANDLER_PURE_MODULE``, ``pipeline_run_id`` set — the sanctioned mid-dispatch form).
On an already-halting path a restore failure logs at ERROR and the original halt error
propagates (never masked). In-place mutation behind an unchanged binding is invisible
to a namespace snapshot **by design** — that residue stays review territory.

**The cooperative timeout**: ``timeout_ms`` is enforced at the
dispatch boundaries and the return point — elapsed ≥ budget halts with the decided PF
shape (``cause_class = "TimeoutError"``, null ``service_binding_name``,
``elapsed_ms_at_failure`` set), attributed to the node at the boundary. No preemption:
an in-flight dispatch is bounded by per-call transport timeouts.

**The canonical event stream** (hash-model § Event-log specification — the substrate the
training projection is reconstructed from, load-bearing for I1/I4 and carrying per-dispatch
provenance): the run emits the
closed-enum events at the boundaries the walk already computes. ``pipeline_start`` after
seeding (``inputs_snapshot`` = the seeded-inputs projection), before the first dispatch;
the ``handler_enter`` / ``handler_exit`` pair per node dispatch (``reads_snapshot`` in,
``writes_snapshot`` out — the training record); ``pipeline_complete`` at the happy-path
return (``outputs_snapshot`` = the projection restricted to the declared ``[outputs]``,
``{}`` when none); ``pipeline_error`` when a run halts (any of the three error classes —
the API-boundary missing-inputs ``ContractViolation`` raises BEFORE the run starts, so it
fires no event, mirroring a compose-time halt with no run in flight). The failed-handler
locus the error event names is the **runner's** knowledge of which node was dispatching
(a mid-dispatch ``ContractViolation`` carries no handler field), not a parse of the
exception. ``pipeline_hash`` (carried on the ``Runnable`` from assemble) names the running
pipeline on every run-lifecycle event.

**The nested ``pipeline`` embed** (pipeline/reference.md § The nested ``pipeline``
composition kind — engine-invoking-engine): a ``node_kind == "pipeline"`` node's dispatch
invokes the recursively-assembled inner ``Runnable`` through this same walk. The inner run
gets its own engine-minted ``pipeline_run_id`` and emits its own canonical-event stream;
its ``pipeline_start`` carries ``parent_run_id`` = the enclosing run's id (``null`` for a
top-level run) — the single linkage, so the inner corpus is reconstructed by correlation,
never duplicated into the outer stream. The embed dispatch emits no ``handler_enter`` /
``handler_exit`` (the closed event ``node_kind`` enum has no ``pipeline`` member by
design; the inner stream is the record). An inner halt propagates outward UNCHANGED —
the inner error object carries its own locus (inner ``pipeline_run_id``,
``composition_ref``, failed-handler position, ``failure_category``), the embed node halts
as any channel-writing dispatch (R-error-channel-003), and the outer ``pipeline_error``
names the embed node. No inner failure is swallowed.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass
from typing import Callable, Mapping, MutableMapping

from conjured import events

from conjured.errors import (
    Check,
    ContractViolation,
    PipelineFailure,
    SchemaValidationError,
    format_composition_ref,
)
from conjured.ir.common import MergeStrategy
from conjured.ir.merge import MERGE_STRATEGY_DEFS
from conjured.ir.graph import channel_contributors
from conjured.runner.assemble import Runnable, RunnableNode
from conjured.runner.dispatch import (
    DispatchContext,
    _BindingDeliveryError,
    _CaptureError,
    _ServiceOriginError,
    new_pipeline_run_id,
)
from conjured.runner.executor import SequentialTaskRunner
from conjured.validator.resolve_validator import FieldValidatorFailure

#: The runner's log surface (the B3 ruling): stdlib ``logging``, logger
#: ``conjured.runner`` — the hook-absorption WARNING and the vector-3 revert WARNING
#: both land here.
logger = logging.getLogger("conjured.runner")


@dataclass(frozen=True, slots=True)
class RunResult:
    """One pipeline invocation's typed output — exactly two fields, a frozen
    value object (field reassignment is prevented; pipeline/reference.md § Pipeline
    result). ``state`` is a plain ``Mapping`` over every **outer-pipeline** channel
    the graph wrote (scoped composition-internal channels stay encapsulated;
    consumer-seeded input channels are not graph-written and are excluded) — the
    engine's promise is kept at return; it does not freeze the consumer's mapping.
    ``run_id`` is the consumer's ``pipeline_run_id`` verbatim when one was supplied,
    else the engine-minted structured form. Not a status envelope: a returned
    RunResult IS success — failure raises and the error channel halts the run
    (R-error-channel-004), so there is no status field, no error context, no partial
    state."""

    state: Mapping[str, object]
    run_id: str


# ---------------------------------------------------------------------------
# The merge fold (fold-as-you-walk over contributors)
# ---------------------------------------------------------------------------


def _first_fold(strategy: MergeStrategy, value: object) -> object:
    """The left fold's initial element — the merged channel's FIRST contributor (its
    seed where the channel is a seeded declared input, else its first node write).
    The per-strategy seed lives with the strategy's total definition
    (``conjured.ir.merge`` — e.g. ``union_set`` dedups the element itself; every other
    strategy's fold over one contributor is that contributor)."""
    return MERGE_STRATEGY_DEFS[strategy].seed(value)


def _fold(strategy: MergeStrategy, current: object, new: object) -> object:
    """One step of the graph-order left fold over a merged channel's contributors
    (R-pipeline-002 runtime region; every branch builds a fresh value). The fold
    behavior lives with the strategy's total definition (``conjured.ir.merge`` — one
    table this walk and the compose-time type check both read; totality is sealed at
    that table's import, so no member can reach this line fold-less)."""
    return MERGE_STRATEGY_DEFS[strategy].fold(current, new)


# ---------------------------------------------------------------------------
# The vector-3 restore (shallow module-namespace snapshot)
# ---------------------------------------------------------------------------


def _restore_namespace(
    namespace: MutableMapping[str, object], snapshot: Mapping[str, object]
) -> tuple[list[str], list[str], list[str]]:
    """Diff ``namespace`` against the pre-dispatch ``snapshot`` (added / deleted /
    rebound-by-identity names), rebind from the snapshot, then **re-verify**. Returns
    the three sorted name lists (all empty = no mutation); raises ``RuntimeError``
    when the restore cannot be verified — the caller owns halt semantics."""
    added = sorted(name for name in namespace if name not in snapshot)
    deleted = sorted(name for name in snapshot if name not in namespace)
    rebound = sorted(
        name
        for name in snapshot
        if name in namespace and namespace[name] is not snapshot[name]
    )
    if not (added or deleted or rebound):
        return [], [], []
    for name in added:
        del namespace[name]
    for name in deleted + rebound:
        namespace[name] = snapshot[name]
    if set(namespace) != set(snapshot) or any(
        namespace[name] is not snapshot[name] for name in snapshot
    ):
        raise RuntimeError(
            "module-namespace restore failed re-verification — the namespace did not "
            "return to its pre-dispatch snapshot"
        )
    return added, deleted, rebound


def restore_after_dispatch(
    namespace: MutableMapping[str, object],
    snapshot: Mapping[str, object],
    *,
    handler_qualified_name: str,
    handler_position: int,
    run_id: str,
    composition_ref: str,
    halting: bool,
) -> None:
    """The post-dispatch arm of the vector-3 layer, on every exit path. A clean full
    revert continues with a WARNING naming the handler and the reverted names (D3);
    a restore that raises or fails re-verification **halts** via ContractViolation
    (``HANDLER_PURE_MODULE``, the sanctioned mid-dispatch form, ``pipeline_run_id``
    set) — unless an error is already in flight (``halting``), in which case the
    failure logs at ERROR and the original halt error propagates (never masked; the
    ratified narrowing of D3's restore-failure halt). Public-named because its
    restore-failure arms are structurally unreachable through a real module
    ``__dict__`` — the tests drive this contract directly, so the name claims the
    cross-module surface it serves rather than module privacy."""
    try:
        added, deleted, rebound = _restore_namespace(namespace, snapshot)
    except Exception as exc:
        if halting:
            logger.error(
                "module-namespace restore FAILED after handler '%s'@%d (run %s): %s "
                "— the original halt error propagates",
                handler_qualified_name, handler_position, run_id, exc,
            )
            return
        # guarantees: vector3-restore-reverify-halt
        raise ContractViolation(
            check=Check.HANDLER_PURE_MODULE,
            rule_id="R-handler-pure-module",
            expected=(
                "the handler's defining-module namespace restores to its pre-dispatch "
                "snapshot (the runtime defense-in-depth layer of "
                "R-handler-pure-module/enforcement)"
            ),
            actual=(
                f"restore after '{handler_qualified_name}'@{handler_position} raised "
                f"or failed re-verification ({type(exc).__name__}: {exc})"
            ),
            remediation_hint=(
                "remove the module-namespace mutation from the handler body; the "
                "engine never continues past a partial restore"
            ),
            composition_ref=composition_ref,
            pipeline_run_id=run_id,
        ) from exc
    if added or deleted or rebound:
        logger.warning(
            "reverted module-namespace mutation by handler '%s'@%d (run %s): "
            "added=%s deleted=%s rebound=%s — module state is not channel state "
            "(R-handler-pure-module); execution continues after the clean revert",
            handler_qualified_name, handler_position, run_id, added, deleted, rebound,
        )


# ---------------------------------------------------------------------------
# The public entry — the per-invocation kernel walk
# ---------------------------------------------------------------------------


def run(
    runnable: Runnable,
    inputs: Mapping[str, object],
    *,
    pipeline_run_id: str | None = None,
    timeout_ms: int | None = None,
    stream_sink: "Callable[[str], None] | None" = None,
) -> RunResult:
    """Walk ``runnable`` end-to-end over ``inputs`` (the initial channel values) —
    the engine's per-invocation entry (B6). Raise-on-halt: any error-channel class
    out of this function means the run halted; a returned :class:`RunResult` IS
    success.

    ``stream_sink`` is the run-scoped token-delivery callback (pipeline/reference.md
    § Orchestration scope): when the runnable's terminal trainable declares
    ``streamable = true``, the engine calls ``stream_sink(fragment)`` for each raw
    text fragment the backend emits, WHILE the terminal dispatch is in flight —
    provisional transport for latency/UX, never a channel value (the channel still
    receives only the complete validated value; the captured record is that same
    value). Attaching a sink to a runnable with no streamable terminal (transitively
    through a terminal nested ``pipeline`` embed) raises ``ContractViolation`` — a
    sink that would silently never fire is a contract lie, not a no-op. A sink that
    itself raises during delivery is absorbed, surfaced on the ``conjured.runner``
    operational logger, and detached — the run completes (the observation-plane wall;
    pipeline/reference.md § Pipeline invocation owns the posture). ``None``
    (default) is byte-identical to the pre-streaming engine."""
    return _run(
        runnable, inputs,
        pipeline_run_id=pipeline_run_id, timeout_ms=timeout_ms, parent_run_id=None,
        stream_sink=stream_sink,
    )


def stream_route_position(runnable: Runnable) -> "int | None":
    """The position of the node an attached ``stream_sink`` routes to, or ``None``
    when the runnable cannot stream: the terminal-modulo-hooks node iff it is a
    ``streamable`` trainable (R-pipeline-001 placement — only hooks may follow), or a
    nested ``pipeline`` embed whose OWN runnable can stream (the sink threads into
    the inner run — the inner stream IS the outer stream, matching the nested-run
    correlation model).

    Two callers share this one derivation (don't-solve-it-twice): :func:`run`'s
    sink-boundary check below, and the server's run trigger — which constructs its
    token-hub sink iff the served runnable can stream, so a sink is never attached
    to a runnable that would reject it (server reference § The token stream)."""
    for node in reversed(runnable.nodes):
        if node.node_kind == "hook":
            continue
        if node.streamable:
            return node.position
        if node.node_kind == "pipeline" and node.inner_runnable is not None:
            if stream_route_position(node.inner_runnable) is not None:
                return node.position
            return None
        return None
    return None


def _run(
    runnable: Runnable,
    inputs: Mapping[str, object],
    *,
    pipeline_run_id: str | None,
    timeout_ms: int | None,
    parent_run_id: str | None,
    stream_sink: "Callable[[str], None] | None" = None,
    deadline_monotonic: float | None = None,
) -> RunResult:
    """The kernel walk's shared core. Engine-internal: the ONLY caller besides
    :func:`run` is the walk's own nested-``pipeline``-embed branch, which threads
    ``parent_run_id`` = the enclosing run's ``pipeline_run_id`` into the inner run's
    ``pipeline_start`` (hash-model.md § canonical event types, "Nested runs correlate
    to their parent"). The public entry's pinned signature carries no ``parent_run_id``
    by design — the linkage is engine-set, so a consumer cannot forge nesting a run
    did not have (a top-level run's parent is ``null``, structurally)."""
    if not isinstance(inputs, Mapping):
        # Engine-surface misuse (the signature types the contract) — not an
        # author-facing error-channel case.
        raise TypeError(
            f"inputs must be a mapping of channel name -> value, "
            f"got {type(inputs).__name__}"
        )

    # --- The API boundary: presence-only key-set check; extras inert (D2/B4/B5) ---
    declared = tuple(field.name for field in runnable.input_fields)
    declared_set = set(declared)
    missing = [name for name in declared if name not in inputs]
    if missing:
        unrecognized = sorted(str(key) for key in inputs if key not in declared_set)
        actual = f"missing declared input field(s): {missing}"
        if unrecognized:
            actual += (
                f"; unrecognized key(s) present: {unrecognized} — unrecognized keys "
                "are never seeded (inert) and cannot satisfy a declared field"
            )
        raise ContractViolation(
            check=Check.API_INPUTS_ENFORCEMENT,
            rule_id="R-pipeline-001",
            expected=(
                f"every declared [inputs] field present in the incoming initial "
                f"channel values: {sorted(declared)}"
            ),
            actual=actual,
            remediation_hint=(
                "supply every declared input field; the pre-validation is "
                "presence-only — value shape is checked at the first reading node's "
                "reads-projection"
            ),
            composition_ref=runnable.pipeline_name,
            pipeline_run_id=pipeline_run_id,  # echo the consumer id; null otherwise (B5)
        )

    # --- The stream-sink boundary: an attached sink must have a route ------------
    # (R-pipeline-001): a sink on a runnable with no streamable terminal would
    # silently never fire — the consumer believes they are streaming and is not.
    # Fail loud at the boundary, never a no-op sink.
    sink_route = stream_route_position(runnable) if stream_sink is not None else None
    if stream_sink is not None and sink_route is None:
        raise ContractViolation(
            check=Check.STREAMABLE_SINK_TARGET,
            rule_id="R-pipeline-001",
            expected=(
                "a stream_sink is attached only to a runnable whose terminal node "
                "(modulo trailing hooks, transitively through a terminal nested "
                "pipeline embed) is a trainable declaring streamable = true"
            ),
            actual=(
                f"a stream_sink was attached to '{runnable.pipeline_name}', which "
                "has no streamable terminal — the sink would never fire"
            ),
            remediation_hint=(
                "declare streamable = true on the terminal trainable composition "
                "(and bind a streaming-capable backend), or call run() without a "
                "stream_sink"
            ),
            composition_ref=runnable.pipeline_name,
            pipeline_run_id=pipeline_run_id,  # echo the consumer id; null otherwise
        )

    # --- Run start: mint the id, start the budget clock --------------------------
    run_id = pipeline_run_id if pipeline_run_id is not None else new_pipeline_run_id()
    started = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    # The run's absolute deadline — the deadline-propagation source every dispatch ctx
    # carries (service-type/reference.md § Deadline propagation). A top-level budgeted
    # run derives it from its own timeout_ms; a nested pipeline embed INHERITS the
    # enclosing run's deadline (threaded by the embed branch below — the whole-run
    # budget is the one engine timeout, and it must reach adapters inside the embed
    # too). Both set — not a state the engine itself produces — resolves to the
    # tighter bound.
    own_deadline = (
        started + timeout_ms / 1000.0 if timeout_ms is not None else None
    )
    bounds = [d for d in (own_deadline, deadline_monotonic) if d is not None]
    deadline = min(bounds) if bounds else None

    # Per-run channel state, scoped to THIS invocation's closure — no object, no
    # attribute, nothing carried across invocations (§ Kernel semantics). Only the
    # declared inputs seed it: an extra never becomes a channel.
    seeded_inputs = {name: inputs[name] for name in declared}  # Phase-4 seam: pipeline_start
    seeded_channels = frozenset(seeded_inputs)
    channel_state: dict[str, object] = dict(seeded_inputs)
    written: set[str] = set()
    # D1 — first-consumer seed validation: a raw seed validates at its FIRST consumer
    # (reads-projection OR merge fold, whichever the runner dispatches first), the flag
    # clearing on first successful validation. This replaces the old `written`-set gate
    # for validation — the `written` set still drives the FOLD (which contributor branch
    # a write takes), but it disarmed validation permanently after the first write,
    # leaving two holes: a writer-before-reader fold consumed the raw seed unvalidated
    # (silent type-coercion), and a post-write reader under a seed-preserving strategy
    # (`first_wins`) skipped pre-validation and deep-copied a wrong-typed seed into a
    # raw `TypeError`. The flag survives writes, so the first consumer always validates.
    seed_still_raw: set[str] = set(seeded_channels)
    merges = runnable.merges
    seed_validators = runnable.seed_validators
    # The contributor model (R-pipeline-002): per merged channel, derive the contributor
    # tuple through the ONE shared derivation (the same one the compose-time
    # merge-requirement count consumes — ir/graph.channel_contributors). A merged channel
    # whose first contributor is the SEED folds the seed as the fold's initial element;
    # a reader's projection is the strategy's left-fold over the contributors upstream of
    # its position.
    seed_contributes = frozenset(
        channel
        for channel in merges
        if any(
            contributor.kind == "seed"
            for contributor in channel_contributors(
                seeded=channel in seeded_channels,
                write_positions=(
                    node.position
                    for node in runnable.nodes
                    for ch in node.write_map.values()
                    if ch == channel
                ),
            )
        )
    )

    def _raw_reads(node: RunnableNode) -> dict[str, object]:
        """Project the node's declared input ports from their read-map-wired channels
        — the RAW channel values, pre-copy. The vector-4 deep copy runs at the task
        site, after validate-then-copy; PF snapshot sites pass this raw projection
        directly (``PipelineFailure`` materializes its own deep copy via
        ``snapshot_copy``, which tolerates non-deep-copyable leaves)."""
        return {port: channel_state[channel] for port, channel in node.read_map.items()}

    def _check_timeout(node: RunnableNode) -> None:
        """The cooperative budget check at a dispatch boundary (D4-i), attributed to
        the node at the boundary — its projection and bindings fill the snapshots."""
        if timeout_ms is None:
            return
        elapsed = _elapsed_ms()
        if elapsed >= timeout_ms:
            raise PipelineFailure(
                failure_category="engine",  # a runner-wrapper run-guard, not a service/handler locus
                cause_class="TimeoutError",
                cause_message=(
                    f"pipeline timeout budget {timeout_ms} ms exceeded at a dispatch "
                    f"boundary ({elapsed} ms elapsed)"
                ),
                failed_handler_qualified_name=node.qualified_name,
                failed_handler_position=node.position,
                bindings_snapshot=node.bindings_values,
                reads_snapshot=_raw_reads(node),
                pipeline_run_id=run_id,
                composition_ref=format_composition_ref(
                    runnable.pipeline_name, node.entry_ordinal
                ),
                service_binding_name=None,  # pipeline-level: no failing binding
                elapsed_ms_at_failure=elapsed,
            )

    def _wrap(
        exc: Exception, node: RunnableNode, projected_reads: Mapping[str, object],
        *, locus: str | None = None,
    ) -> PipelineFailure:
        """The dispatch-boundary wrap (R-error-channel-001): any uncaught exception
        that is not already CV/SVE becomes PipelineFailure. ``cause_class`` AND
        ``cause_message`` ride ``FieldValidatorFailure.__cause__`` for the
        validator-failure carrier (N1) — the payload pair names ONE underlying
        exception, never class-from-the-cause / message-from-the-carrier; a
        verdict-protocol break has no underlying exception — its cause pair is the
        carrier itself."""
        # failure_category is the STRUCTURAL locus — read from where the failure escaped, never
        # sniffed from the exception name (the dead `cause_class in (...)` rule this replaces):
        #   - a service backend call -> "service" + the failing binding. Either the
        #     `_ServiceOriginError` carrier (raised at a service node's adapter boundary), or a
        #     trainable node — whose only "body" IS the engine-constructed adapter.invoke, so its
        #     node_kind alone is the structural signal.
        #   - a runner-machinery op (locus="engine", e.g. channel routing) -> "engine".
        #   - otherwise an author handler body -> "handler".
        # cause_class/cause_message name ONE underlying exception, unwrapped from the carrier
        # (`_ServiceOriginError`) or the N1 `FieldValidatorFailure` carrier.
        if locus == "engine":
            failure_category = "engine"
            cause: BaseException = exc
            service_binding_name = None
        elif isinstance(exc, _BindingDeliveryError):
            # Binding delivery is the engine's OWN runner machinery (the per-dispatch
            # COPY-mode deep copy / transport delivery), not an author body or a service
            # backend — so a non-deep-copyable COPY value's deepcopy failure is the `engine`
            # locus (error-channel/reference.md § failure_category: "binding delivery,
            # channel routing, merge"), regardless of node_kind. Without this branch the
            # carrier would fall through to the node_kind attribution below and be
            # mis-blamed on the author's handler/service body. service_binding_name stays
            # null (engine has no failing SERVICE binding); cause is the raw deepcopy error.
            failure_category = "engine"
            cause = exc.__cause__ if exc.__cause__ is not None else exc
            service_binding_name = None
        elif isinstance(exc, _CaptureError):
            # Adapter-boundary capture (the `service_invocation` payload deep copy) is the
            # engine's OWN capture machinery — it runs AFTER `adapter.invoke` already returned,
            # so a non-deep-copyable payload's deepcopy failure is the `engine` locus, NOT the
            # service backend (which succeeded) and NOT the author body the carrier merely escaped
            # through. Without this branch the carrier falls through to the node_kind attribution
            # below and is mis-blamed on the author's handler/service body. service_binding_name
            # stays null (the engine locus has no failing SERVICE binding); cause is the raw
            # deepcopy error, unwrapped from the carrier.
            failure_category = "engine"
            cause = exc.__cause__ if exc.__cause__ is not None else exc
            service_binding_name = None
        elif isinstance(exc, _ServiceOriginError):
            failure_category = "service"
            cause = exc.__cause__ if exc.__cause__ is not None else exc
            service_binding_name = exc.binding_name
        elif node.node_kind == "pipeline":
            # A nested `pipeline` embed node has no author body and no service binding of
            # its own — anything wrapped AT the embed boundary is the runner's own
            # machinery (an inner halt propagates as its own error class and never reaches
            # this wrap; the inner run's failures carry their own inner attribution).
            failure_category = "engine"
            cause = exc
            service_binding_name = None
        # guarantees: failure-category-trainable-is-service
        elif node.node_kind == "trainable":
            failure_category = "service"
            cause = (exc.__cause__ if isinstance(exc, FieldValidatorFailure)
                     and exc.__cause__ is not None else exc)
            service_binding_name = node.service_binding_name
        else:
            failure_category = "handler"
            cause = (exc.__cause__ if isinstance(exc, FieldValidatorFailure)
                     and exc.__cause__ is not None else exc)
            service_binding_name = None
        return PipelineFailure(
            failure_category=failure_category,
            cause_class=type(cause).__name__,
            cause_message=str(cause),
            failed_handler_qualified_name=node.qualified_name,
            failed_handler_position=node.position,
            bindings_snapshot=node.bindings_values,
            reads_snapshot=projected_reads,
            pipeline_run_id=run_id,
            composition_ref=format_composition_ref(
                runnable.pipeline_name, node.entry_ordinal
            ),
            service_binding_name=service_binding_name,
            elapsed_ms_at_failure=_elapsed_ms(),
        )

    def _route_writes(
        node: RunnableNode, validated_output: dict[str, object], ctx: DispatchContext
    ) -> None:
        """Route the validated return onto channels via the write-map — the runner is
        the sole channel writer. Iteration follows the node's **declared write-map
        (output-schema) order**, never the handler body's return-dict insertion order:
        canon's "contributors combine in graph order" must hold within one node wiring
        two ports to a single merged channel, and the declaration is the only hashed
        sequencing input (the return-dict key order is contract-neutral). Merged
        channels fold-as-you-walk (D1) over the channel's **contributors** — where the
        channel is a seeded declared input, the seed is the fold's first element
        (R-pipeline-002; the shared contributor derivation), so the first node write
        folds INTO the seed rather than clobbering it. A non-merged double contribution
        is compose-guaranteed impossible (R-pipeline-002 counts the seed as a
        contributor), so the guard is a plain assert (engine-bug attribution). Runs
        inside the dispatch wrap boundary: a fold over a consumer-supplied seed value
        can raise, and that failure must wrap to PipelineFailure at the writing node —
        never escape as a fourth class."""
        for port, channel in node.write_map.items():
            value = validated_output[port]  # output validation pinned the exact key set
            strategy = merges.get(channel)
            if strategy is None:
                assert channel not in written and channel not in seeded_channels, (
                    f"engine bug: non-merged channel '{channel}' has a second "
                    "contributor — compose-time contributor counting (R-pipeline-002) "
                    "guarantees a merge declaration for every multi-contributor channel"
                )
                channel_state[channel] = value
            elif channel in written:
                channel_state[channel] = _fold(strategy, channel_state[channel], value)
            elif channel in seed_contributes:
                # This first node write folds INTO the seed (graph order: the seed
                # exists before any node runs). The fold is the seed's FIRST consumer
                # when no reader preceded this write — validate the RAW seed here before
                # it is folded (the per-channel seed validator: the fold has no reading
                # node, so the seed's own [inputs] declaration is the model). The ruled
                # reads-side SVE raises inside the dispatch wrap boundary (re-raised by
                # the except (CV, SVE) arm — it halts, never wraps to PF). The flag
                # clears so a later reader of the (possibly seed-preserving) value does
                # not re-validate.
                if channel in seed_still_raw:
                    seed_validators[channel](
                        reads={channel: channel_state[channel]}, ctx=ctx
                    )
                    seed_still_raw.discard(channel)
                channel_state[channel] = _fold(
                    strategy, _first_fold(strategy, channel_state[channel]), value
                )
            else:
                channel_state[channel] = _first_fold(strategy, value)
            written.add(channel)

    # The node currently being dispatched — the failed-handler locus the pipeline_error
    # event names. A mid-dispatch ContractViolation carries no handler field, so the
    # runner's own knowledge of the in-flight node is the authoritative source (and it
    # agrees by construction with the handler_enter the same node emitted).
    dispatching: list[RunnableNode | None] = [None]

    def _make_task(node: RunnableNode):
        def task() -> None:
            dispatching[0] = node
            _check_timeout(node)
            raw_reads = _raw_reads(node)
            ctx = DispatchContext(
                pipeline_run_id=run_id,
                handler_position=node.position,
                pipeline_hash=runnable.pipeline_hash,
                # The run-scoped delivery sink rides ONLY the streamable terminal
                # trainable's dispatch (the sink-route resolution at the run boundary);
                # every other node — and every run without a sink — sees None.
                stream_sink=(
                    stream_sink
                    if sink_route == node.position and node.streamable
                    else None
                ),
                deadline_monotonic=deadline,
            )
            composition_ref = format_composition_ref(
                runnable.pipeline_name, node.entry_ordinal
            )
            snapshot = (
                dict(node.module.__dict__) if node.module is not None else None
            )
            projected_reads: dict[str, object] | None = None
            halting = False
            try:
                # Validate-then-copy (the ruled escape-hole fix): a consumer-seeded
                # input value is unvalidated until its first consumer, and
                # ``copy.deepcopy`` of a wrong-typed seed (open file handle,
                # generator) raises a raw TypeError — so when this reads-projection is
                # a still-raw seed's FIRST consumer, the reads-side validation runs
                # against the RAW value first (the ruled SVE catch point, full field
                # attribution via the reading node's own model), and the copy runs
                # after it, inside the wrap boundary: no fourth class escapes either
                # way. The gate is the per-channel ``seed_still_raw`` flag (D1), not the
                # `written` set — so a reader after a seed-preserving write still
                # validates, and a seed already validated at an upstream merge fold is
                # not re-validated here.
                reads_to_validate = [
                    channel
                    for channel in node.read_map.values()
                    if channel in seed_still_raw
                ]
                if reads_to_validate:
                    node.validate_reads(reads=raw_reads, ctx=ctx)
                    seed_still_raw.difference_update(reads_to_validate)
                # A reader of a seeded MERGED channel before any node write sees the
                # strategy's fold over [seed] — `_first_fold` (the contributor model:
                # the seed is the fold's first element; only ``union_set`` transforms a
                # one-element fold). Applied post-validation (the raw seed was just
                # validated above), pre-copy.
                projection = {
                    port: (
                        _first_fold(merges[channel], raw_reads[port])
                        if channel in seed_contributes and channel not in written
                        else raw_reads[port]
                    )
                    for port, channel in node.read_map.items()
                }
                # The projected-reads dict (one fresh **deep copy** per dispatch;
                # trust-model § Vector 4: "input ports projected-and-copied" — a reader
                # mutating its projection cannot corrupt the channel or a sibling reader).
                # This deep copy is the READ side of channel routing — the engine's OWN
                # runner machinery (error-channel/reference.md § failure_category:
                # ``"engine"`` covers "binding delivery, channel routing, merge"), the
                # sibling of the COPY-mode binding delivery and the merge fold. A
                # schema-VALID-but-non-deep-copyable channel value (the one escape from the
                # closed-type-leaf invariant: a ``str``/``int`` subclass whose
                # ``__deepcopy__`` raises, which clears strict validation against its
                # declared type) makes this copy raise; the engine wrap attributes that to
                # the ``engine`` locus (null ``service_binding_name``). Without it the
                # failure falls through to the generic dispatch-boundary ``except`` below
                # and is mis-attributed by ``node_kind`` to the author's handler / service
                # body — which has NOT run yet (the copy precedes dispatch) — the same
                # mis-blame the binding-delivery carrier closed, at this read-side site;
                # a wrong blame label becomes training data, so it is a correctness defect.
                # The ruled validate-then-copy ordering is preserved: a wrong-typed seed
                # already raised ``SchemaValidationError`` above (the D2 catch point), so
                # this wrap catches only a copy failure on an ALREADY-validated value.
                try:
                    # guarantees: failure-category-engine-is-channel-routing
                    projected_reads = {
                        port: copy.deepcopy(value) for port, value in projection.items()
                    }
                except (ContractViolation, SchemaValidationError):
                    halting = True
                    raise
                except Exception as copy_exc:
                    halting = True
                    raise _wrap(
                        copy_exc, node,
                        projected_reads if projected_reads is not None else raw_reads,
                        locus="engine",
                    ) from copy_exc
                if node.node_kind == "pipeline":
                    # The nested `pipeline` embed — engine-invoking-engine
                    # (pipeline/reference.md § The nested `pipeline` composition kind).
                    # The inner pipeline runs as its OWN invocation through this same
                    # walk: its own engine-minted pipeline_run_id, its own canonical-event
                    # stream (pipeline_start carrying parent_run_id = THIS run's id — the
                    # single linkage), its own per-run channel state. No handler_enter /
                    # handler_exit fires for the embed dispatch: the closed event
                    # node_kind enum has no `pipeline` member by design, and the inner
                    # run's stream IS the record — reconstructed by correlation, never
                    # duplicated into the outer stream (hash-model.md § canonical event
                    # types). The embed's channel-writes correspond to the inner run's
                    # pipeline_complete outputs_snapshot, correlated by parent_run_id.
                    # No inner timeout is threaded: the whole-run budget is the one
                    # engine timeout, checked cooperatively — the outer budget check
                    # fires at the next outer dispatch boundary, exactly as for any
                    # in-flight dispatch. The outer DEADLINE is threaded, though: the
                    # inner run's dispatch contexts inherit it so deadline propagation
                    # (service-type/reference.md § Deadline propagation) reaches
                    # participating adapters inside the embed — a budget check and a
                    # budget hand-off are different mechanisms.
                    inner = node.inner_runnable
                    assert inner is not None, (
                        "engine bug: pipeline-embed node carries no inner_runnable — "
                        "assemble constructs one for every node_kind == 'pipeline'"
                    )
                    try:
                        # guarantees: deadline-propagates-into-embed
                        inner_result = _run(
                            inner, projected_reads,
                            pipeline_run_id=None,  # the inner id is ALWAYS engine-minted
                            timeout_ms=None,
                            deadline_monotonic=deadline,
                            parent_run_id=run_id,
                            # A terminal streamable embed threads the sink into the
                            # inner run (the inner stream IS the outer stream); the
                            # inner boundary re-resolves its own route.
                            stream_sink=(
                                stream_sink if sink_route == node.position else None
                            ),
                        )
                    except (ContractViolation, SchemaValidationError, PipelineFailure):
                        # Inner-halt propagation (R-error-channel-003; § The nested
                        # `pipeline` composition kind, Halt propagation): the inner error
                        # object propagates UNCHANGED through the embed boundary, so the
                        # attribution chain — the inner pipeline_run_id, composition_ref,
                        # failed-handler position, and (for PipelineFailure) the inner
                        # run's failure_category — stays intact, correlated to this run
                        # by parent_run_id. No inner failure is swallowed; the outer
                        # except arms below halt this run with the same object.
                        raise
                    except Exception as inner_exc:
                        # Anything else escaping the nested walk is the engine's own
                        # machinery failing (the walk's contract admits only the three
                        # error classes) — the `engine` locus, never blamed on a handler.
                        halting = True
                        raise _wrap(
                            inner_exc, node, projected_reads, locus="engine"
                        ) from inner_exc
                    # The embed node's validated output: the inner run's declared
                    # [outputs] projection — presence-opts-in, so an inner pipeline with
                    # no [outputs] writes nothing back ({} routes over an empty
                    # write-map). Values are engine-produced by the already-validated
                    # inner run (each inner output is node-written and output-validated
                    # at its writer; boundary types agreed at compose), so no
                    # re-validation boundary exists here.
                    declared_inner_outputs = inner.graph.outputs or ()
                    result: dict[str, object] | None = {
                        f.name: inner_result.state[f.name]
                        for f in declared_inner_outputs
                    }
                else:
                    # handler_enter — `reads_snapshot` IS the training-pair input side. Emitted
                    # BEFORE dispatch, so the snapshot captures the pre-dispatch value (the body
                    # has not yet run). The runner hands the SAME `projected_reads` to both the
                    # event and the handler body, but the HandlerEnter dataclass materializes its
                    # OWN copy of the payload at construction (events/__init__.py, guarantees:
                    # event-payload-deepcopy) — so a later in-place mutation of `projected_reads`
                    # by the handler body cannot rewrite this retained record.
                    events.emit(
                        events.HandlerEnter(
                            handler_qualified_name=node.qualified_name,
                            handler_position=node.position,
                            node_kind=node.node_kind,
                            pipeline_run_id=run_id,
                            timestamp=events.now_iso(),
                            reads_snapshot=projected_reads,
                        )
                    )
                    dispatch_started = time.monotonic()
                    assert node.dispatch is not None, (
                        "engine bug: a non-pipeline node carries no dispatch callable"
                    )
                    result = node.dispatch(reads=projected_reads, ctx=ctx)
                    # handler_exit — after the body completes (the trainable pair's output
                    # side). `writes_snapshot` is the validated output dict, `None` for hooks
                    # (they write no channels); the HandlerExit dataclass materializes its own
                    # copy at construction, so the routed `result` evolving channel state
                    # downstream cannot rewrite this retained record (guarantees: event-payload-deepcopy).
                    # `correlation_id` pairs a service dispatch to its `service_invocation` —
                    # the derived `(pipeline_run_id, handler_position)` label (hash-model
                    # § canonical event types); absent for non-service kinds. A dispatch that
                    # raises skips this emit (the body did not complete) and routes to the
                    # except arm below.
                    events.emit(
                        events.HandlerExit(
                            handler_qualified_name=node.qualified_name,
                            handler_position=node.position,
                            node_kind=node.node_kind,
                            elapsed_ms=int((time.monotonic() - dispatch_started) * 1000),
                            pipeline_run_id=run_id,
                            timestamp=events.now_iso(),
                            writes_snapshot=result,
                            correlation_id=(
                                f"{run_id}:{node.position}"
                                if node.node_kind == "service"
                                else None
                            ),
                        )
                    )
                if result is not None:
                    # Routing runs inside the wrap boundary: the merged-channel fold can
                    # consume a consumer-supplied seed (the fold's first element), and a
                    # fold failure over a wrong-typed seed must wrap to PipelineFailure at
                    # this node, never escape raw. (A hook returns None — no routing.) The fold is
                    # the engine's OWN runner machinery, so a failure here is the `engine` locus —
                    # distinct from the author-body / service-backend loci a dispatch failure carries.
                    try:
                        _route_writes(node, result, ctx)
                    except (ContractViolation, SchemaValidationError):
                        halting = True
                        raise
                    except Exception as route_exc:
                        halting = True
                        raise _wrap(
                            route_exc, node,
                            projected_reads if projected_reads is not None else raw_reads,
                            locus="engine",
                        ) from route_exc
            except (ContractViolation, SchemaValidationError):
                # Graph-shape failures re-raise and halt — including from hooks
                # (§ Hook-wrapper sanction: the partition is by class).
                halting = True
                raise
            except PipelineFailure:
                # Already the closed runtime class (the routing-fold engine wrap above, or
                # an inner run's halt propagating unchanged through a nested `pipeline`
                # embed with its inner attribution intact): propagate as-is — the
                # dispatch-boundary guarantee wraps only what is NOT already
                # CV / SVE / PipelineFailure, never PF-wrapping-PF.
                halting = True
                raise
            except Exception as exc:
                failure = _wrap(
                    exc, node,
                    projected_reads if projected_reads is not None else raw_reads,
                )
                if node.node_kind == "hook":
                    # The engine-owned hook wrapper: an operational PF from a hook is
                    # absorbed — a hook writes no channels, so the loss is a missing
                    # side-effect record, never a corrupted read (B3 log surface).
                    logger.warning(
                        "hook failure absorbed (execution continues): cause_class=%s "
                        "cause_message=%s hook='%s' position=%d pipeline_run_id=%s",
                        failure.cause_class, failure.cause_message,
                        node.qualified_name, node.position, run_id,
                    )
                else:
                    halting = True
                    raise failure from exc
            finally:
                if snapshot is not None:
                    restore_after_dispatch(
                        node.module.__dict__,
                        snapshot,
                        handler_qualified_name=node.qualified_name,
                        handler_position=node.position,
                        run_id=run_id,
                        composition_ref=composition_ref,
                        halting=halting,
                    )

        return task

    # pipeline_start — the run has begun (inputs loaded), before the first dispatch.
    # `inputs_snapshot` is the seeded-inputs projection; the PipelineStart dataclass materializes
    # its own copy at construction (guarantees: event-payload-deepcopy), so the retained event is
    # immune to any later channel evolution. Note the copy passes non-container leaves by reference,
    # so a raw pre-validation seed value (e.g. a non-copyable generator, which the first reader will
    # reject as SchemaValidationError) does NOT raise here — the clean read-side reject locus is
    # preserved. `parent_run_id` is the enclosing run's pipeline_run_id when this is the
    # inner run of a nested `pipeline` embed — the single linkage from an inner run to
    # its parent — and None (`null`) for a top-level run, which has no parent
    # (hash-model.md § canonical event types).
    events.emit(
        events.PipelineStart(
            pipeline_run_id=run_id,
            pipeline_hash=runnable.pipeline_hash,
            timestamp=events.now_iso(),
            inputs_snapshot=dict(seeded_inputs),
            parent_run_id=parent_run_id,
        )
    )

    # The executor seam (transfer 2): the ordered thunks go through the small interface;
    # SequentialTaskRunner is the only shipped implementation. The return-point budget
    # check (D4-i) is attributed to the last node. A halt out of either — one of the three
    # error classes (R-error-channel-001) — emits pipeline_error naming the in-flight node,
    # then re-raises (the run still halts; the event is provenance, never a recovery path).
    try:
        SequentialTaskRunner().run_ordered([_make_task(node) for node in runnable.nodes])
        if runnable.nodes:
            _check_timeout(runnable.nodes[-1])
    except (ContractViolation, SchemaValidationError, PipelineFailure) as exc:
        node = dispatching[0]
        assert node is not None, (
            "engine bug: a run halted inside the walk with no node dispatched — "
            "pipeline_error is runtime-only (a dispatched handler always exists at the halt)"
        )
        events.emit(
            events.PipelineError(
                pipeline_hash=runnable.pipeline_hash,
                pipeline_run_id=run_id,
                elapsed_ms=_elapsed_ms(),
                timestamp=events.now_iso(),
                error_class=type(exc).__name__,
                failed_handler_qualified_name=node.qualified_name,
                failed_handler_position=node.position,
                error_message=str(exc),
                # cause_class rides only the PipelineFailure wrap (the underlying class);
                # CV/SVE are themselves the structural cause — no separate cause.
                cause_class=exc.cause_class if isinstance(exc, PipelineFailure) else None,
                failure_category=(exc.failure_category if isinstance(exc, PipelineFailure) else None),
            )
        )
        raise

    # pipeline_complete — happy-path termination. `outputs_snapshot` is the projection
    # restricted to the declared `[outputs]` (`{}` when the pipeline declares none); each
    # declared output is node-written by compile time (the inputs/outputs-dead check).
    declared_outputs = runnable.graph.outputs
    outputs_snapshot = (
        {}
        if declared_outputs is None
        else {field.name: channel_state[field.name] for field in declared_outputs}
    )
    events.emit(
        events.PipelineComplete(
            pipeline_hash=runnable.pipeline_hash,
            pipeline_run_id=run_id,
            elapsed_ms=_elapsed_ms(),
            timestamp=events.now_iso(),
            outputs_snapshot=outputs_snapshot,
        )
    )

    final_state = {
        channel: channel_state[channel]
        for channel in sorted(runnable.outer_written_channels)
    }
    return RunResult(state=final_state, run_id=run_id)
