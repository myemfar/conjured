"""Compiled typed-dataflow graph IR — the shape the validator compiles a pipeline
declaration into.

This is the **target shape** of compilation, modeled as data structures. Phase 0 does
**not** build the compiler (graph compilation is Phase 1a, explicitly out of scope); it
fixes the IR the compiler populates and downstream phases (hasher, runner) read.

The field set is **derived/composed from several canon sites** rather than enumerated in
one "compiled-graph schema" doc — each field traces to canon:

- **A node's identity is its dispatch position** (0-indexed in the final compose-time
  dispatch order), not its qualified name — a handler reused at several positions shares
  one name (``hash-model.md`` § Canonical event types: ``handler_position``;
  ``handler-resolution.md`` § Resolution mechanism). ``qualified_name`` is descriptive.
- **``node_kind`` ∈ {transform, service, hook, trainable}** (``hash-model.md`` event
  payloads). A trainable composition node post-flatten dispatches as a ``trainable`` node.
- **Handlers declare channel-agnostic ports; a node binds port↔channel via read-map /
  write-map**, and the normalized wiring IR is **always-explicit** (every port mapped,
  identity-sugar desugared) and computed *through the maps*, never by channel-name
  identity (``pipeline/reference.md`` §§ ``reads_map`` / ``writes_map``, Pipeline load
  lifecycle, R-pipeline-001; memory ``collapsed-handler-channel-boundary``).
- **A channel's type is the agreed type of the ports wired to it** (``pipeline/reference.md``
  R-pipeline-001 read/write shape matching). Channels may be scoped
  (``<composition>.<channel>``) post-flatten.
- **Runner operations (merge / projection / identity-desugar) are NOT nodes.** A merge is
  carried as a graph-level ``channel → strategy`` entry the runner applies inline — no
  synthesized node, no merge event (``pipeline/reference.md`` § ``merge.<channel>``,
  R-pipeline-002; memory ``runner-operation-not-a-node``). Projection and identity-desugar
  are *computation*, not stored entities: the desugar is already applied (the maps below
  are total/normalized), and projection happens at dispatch — neither is a graph member.
"""

from __future__ import annotations

from typing import Iterable, Literal, Mapping, Union

from conjured.events import NodeKind
from conjured.ir.base import IRModel
from conjured.ir.channel_types import ChannelFieldType, FieldDecl
from conjured.ir.common import MergeStrategy, NodeBindingValue, ServiceBindingSupply
from conjured.ir.pipeline import PipelineDeclaration

#: The runtime node kinds the engine dispatches. The four handler-bearing kinds are
#: single-sourced from the canonical-event ``node_kind`` enum
#: (:data:`conjured.events.NodeKind`) — the IR composes over the imported name rather than
#: re-declaring the literal, so that closed member list lives in exactly ONE place. The
#: graph layer adds exactly one graph-only member, ``"pipeline"`` — the nested ``pipeline``
#: composition embed node (engine-invoking-engine). It is deliberately NOT an event
#: ``node_kind`` member: a pipeline-embed dispatch emits no ``handler_enter`` /
#: ``handler_exit`` — its record is the inner run's OWN canonical-event stream, correlated
#: outward by ``parent_run_id`` (``hash-model.md`` § canonical event types, "Nested runs
#: correlate to their parent"; the event payload enum stays closed at four). The dependency
#: runs ``ir → events`` only (``events`` imports nothing from ``ir``, so no cycle).
# guarantees: node-kind-single-source
GraphNodeKind = Union[NodeKind, Literal["pipeline"]]


class Contributor(IRModel):
    """One contributor to a channel — the unit of the contributor model
    (``pipeline/reference.md`` § ``merge.<channel>``; R-pipeline-002): the channel's
    **seed** (iff the channel is a declared ``[inputs]`` channel — the invocation's
    supplied value, the fold's first element) or one **node write** (carrying the
    writing node's dispatch ``position``)."""

    kind: Literal["seed", "write"]
    position: int | None = None  # the writing node's dispatch position; None for the seed


def channel_contributors(
    *, seeded: bool, write_positions: Iterable[int]
) -> tuple[Contributor, ...]:
    """THE contributor derivation, single-homed (R-pipeline-002, the ruled contributor
    model): *a channel's contributors are its seed (iff the channel is a declared
    ``[inputs]`` channel) plus its node writes, in graph order* — the seed first (it
    exists before any node runs), node writes following in dispatch order. Both
    consumers point here: the compose-time merge-requirement count (two or more
    contributors require a declared ``merge.<channel>`` strategy —
    ``validator/compile.py``) and the runtime fold (a reader's projection is the
    strategy's left-fold over the contributors upstream of its position, the seed the
    fold's first element — ``runner/run.py``)."""
    contributors: list[Contributor] = []
    if seeded:
        contributors.append(Contributor(kind="seed"))
    contributors.extend(
        Contributor(kind="write", position=position)
        for position in sorted(write_positions)
    )
    return tuple(contributors)


class Port(IRModel):
    """A channel-agnostic port — a named, typed slot the handler sees, never a channel.
    Direction is given by its container on the node (``input_ports`` vs ``output_ports``).
    """

    name: str
    type: ChannelFieldType


class Channel(IRModel):
    """A typed dataflow channel — a wire between nodes. ``name`` may be a scoped
    qualified name (``<composition>.<channel>``) post-flatten; ``type`` is the agreed type
    of every port wired to it. ``scoped`` is the structural scoped-channel marker — a
    composition-internal channel the flatten rescoped, encapsulated from ``RunResult.state``
    (``pipeline/reference.md`` § Pipeline result) — recorded at flatten rather than
    re-derived from the name's dot pattern (the dot is incidental structure).
    """

    name: str
    type: ChannelFieldType
    scoped: bool = False


class GraphNode(IRModel):
    """One dispatched node in the compiled graph. Identity is ``position``; the
    ``read_map`` / ``write_map`` are the **normalized, always-explicit** wiring (every
    port has an entry). ``output_ports`` is empty for a hook (it writes no channels).

    The four trailing fields are the **stage-4 join keys** — what engine-side dispatch
    construction (``pipeline/reference.md`` § Pipeline load lifecycle stage 4) needs to
    route this node back to its declarations without re-walking the pipeline declaration:
    ``entry_ordinal`` is the top-level ``nodes`` declaration-entry index this node came
    from (the ``composition_ref`` ordinal — ``"<pipeline_name>[<entry_ordinal>]"``,
    error-channel/reference.md § PipelineFailure payload — distinct from ``position``,
    the post-flatten dispatch index); ``callable_ref`` is the Python qualified name
    handler resolution resolves (``None`` for a trainable node — no author body,
    R-handler-010); ``composition_path`` / ``member_name`` locate a flattened composition
    member's owning declaration (the registry's composition key + the
    ``[[preprocessors]]`` entry name; both ``None`` for top-level handler nodes,
    ``member_name`` ``None`` for the terminal trainable).
    """

    position: int  # 0-indexed dispatch position — the node's identity
    node_kind: GraphNodeKind
    qualified_name: str  # the AS-WRITTEN handler / composition node name (descriptive, not
    #   identity); it is the as-written label the node carries — the hook_transport coverage
    #   key for a hook (it MAY be a short entry-points name, taken verbatim), matching the
    #   corpus join-on-the-as-written-label pattern (D7) — NOT the resolved dotted name
    input_ports: tuple[Port, ...] = ()
    output_ports: tuple[Port, ...] = ()  # empty for hooks
    read_map: Mapping[str, str] = {}  # NORMALIZED input-port -> channel (total)
    write_map: Mapping[str, str] = {}  # NORMALIZED output-port -> channel (total)
    bindings: tuple[NodeBindingValue, ...] = ()  # resolved binding values fixed to this node
    entry_ordinal: int = 0  # top-level declaration-entry index (the composition_ref ordinal)
    callable_ref: str | None = None  # Python qualified name to resolve (None for trainable)
    composition_path: str | None = None  # owning composition's registry key (members only)
    member_name: str | None = None  # [[preprocessors]] entry name (preprocessors only)


class MergeOp(IRModel):
    """A channel-write merge — a **runner operation, not a node**. The runner applies the
    strategy to the merged channel's writers' contributions inline; there is no
    synthesized node and no merge event.
    """

    channel: str
    strategy: MergeStrategy


class CompiledGraph(IRModel):
    """A compiled pipeline — the typed dataflow graph ready to dispatch. The assembled
    ordered list of nodes is the graph (``pipeline/reference.md`` § Pipeline load
    lifecycle stage 4); merges are runner operations carried alongside, not nodes.
    ``pipeline_name`` is the pipeline's qualified name (identity, never hashed — the
    hasher reads declaration IR, not this graph): the ``composition_ref`` prefix and the
    deployment ``pipelines.<name>`` override key the runner resolves against.
    """

    pipeline_name: str
    #: The pipeline declaration's contract-document path — identity/diagnostics, never
    #: hashed (the hasher reads declaration IR, not this graph). Threaded so stage-4
    #: assembly can build the API-boundary seed validators whose reads-side
    #: ``SchemaValidationError`` names the declaring artifact (D1 first-consumer
    #: validation: a writer-before-reader seed has no reading node to borrow a
    #: ``schema_source`` from — the pipeline declaration is the seed's home).
    source_path: str
    #: The ``PipelineDeclaration`` this graph was compiled FROM — carried so stage-4
    #: assembly computes the pipeline-hash over the exact declaration that produced this
    #: graph (``hasher.pipeline_hash`` reads declaration IR, resolution 3a — the
    #: dispatch-flattened graph is the wrong hash input). This makes hash↔graph
    #: correspondence **structural**, not a by-name registry re-fetch that could drift.
    #: Not itself hashed (the hasher takes the declaration directly). ``None`` only for a
    #: hand-built graph that never went through ``compile_pipeline``; assemble fails loud
    #: on it (a real compiled graph always carries its declaration).
    source_declaration: PipelineDeclaration | None = None
    nodes: tuple[GraphNode, ...]  # ordered by ``position``
    channels: tuple[Channel, ...]  # the typed channel set (may include scoped channels)
    inputs: tuple[FieldDecl, ...] = ()  # API-boundary free variables
    outputs: tuple[FieldDecl, ...] | None = None  # declared output API commitment (None = absent)
    merges: tuple[MergeOp, ...] = ()  # runner operations applied inline — NOT nodes
    service_bindings: tuple[ServiceBindingSupply, ...] = ()  # resolved identity supplies
