"""Stage 2 — compose-time validation (R-pipeline-001) + the pipeline → graph compile.

The second ``pipeline load lifecycle`` stage this unit owns
(``conjured/docs/components/pipeline/reference.md`` § Pipeline load lifecycle stage 2): the
full compose-time type-check and the ``PipelineDeclaration`` → ``CompiledGraph`` transform —
``desugar → resolve names/ports → type-match channels → assemble ordered nodes`` — every
failure raising :class:`~conjured.errors.ContractViolation` **before any handler dispatches**.

Pure function over an in-memory declaration set: no entry-points discovery, no callable
import, no dispatch, no hashing. Name resolution is registry
membership; the module import / source-AST seals / function-shape / signature checks are
Phase 2 (``handler-resolution.md`` § Resolution sequence steps 3–7).

**Error-reporting posture.** Canon specifies "aggregate within a group, fail-fast across
groups" (pipeline/reference.md § Composition validation — the cited error-reporting policy).
The three groups below — A (registry resolution + flatten), B (graph topology), C (deployment
coverage) — each **collect** every independently-detectable ``ContractViolation`` they find,
then :func:`_finalize` raises at the group boundary: a group with exactly one violation raises
the bare ``ContractViolation`` (the common case), a group with **≥ 2** raises a
:class:`~conjured.errors.ContractViolationGroup` wrapping them. Across groups the order stays
fail-fast — a non-empty earlier group raises before a later group whose preconditions it
invalidated runs (graph topology cannot be trusted against unresolved nodes), so a group only
ever aggregates violations from itself (error-channel/reference.md § ContractViolationGroup).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

from pydantic import ValidationError

from conjured.errors import (
    Check,
    ContractViolation,
    ContractViolationGroup,
    format_composition_ref,
)
from conjured.ir.channel_types import (
    ChannelFieldType,
    FieldDecl,
    OptionalType,
    SecretRefType,
    TableType,
    canonical_token,
    first_non_json_expressible,
)
from conjured.ir.common import (
    CompileBinding,
    MergeStrategy,
    SchemaBinding,
    ServiceBindingSupply,
)
from conjured.ir.composition import BundleComposition, PipelineComposition, TrainableComposition
from conjured.ir.merge import MERGE_STRATEGY_DEFS
from conjured.ir.deployment import DeploymentDeclaration
from conjured.ir.graph import (
    Channel,
    CompiledGraph,
    GraphNode,
    GraphNodeKind,
    MergeOp,
    Port,
    channel_contributors,
)
from conjured.ir.handler import HookDeclaration, ServiceDeclaration
from conjured.ir.pipeline import CompositionNode, HandlerNode, PipelineDeclaration
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.ir.substitute import substitute_bundle_nodes
from conjured.validator.model_gen import build_model
from conjured.validator.normalize import desugar_map, is_explicit_null
from conjured.validator.registry import DeclarationRegistry

Direction = Literal["read", "write"]
PortSource = Literal["mapped", "identity"]


# ---------------------------------------------------------------------------
# Intermediate flatten representation
# ---------------------------------------------------------------------------


@dataclass
class _WiringPort:
    """One port wired to a channel — the unit of type-match / closure / overlap."""

    port_name: str
    node_qualified_name: str
    node_position: int
    direction: Direction
    type: ChannelFieldType
    source: PortSource


@dataclass
class _FlatNode:
    """A dispatched node after flatten/desugar — the pre-``GraphNode`` working form."""

    position: int
    node_kind: GraphNodeKind
    qualified_name: str
    input_ports: tuple[Port, ...]
    output_ports: tuple[Port, ...]
    read_map: dict[str, str]
    write_map: dict[str, str]
    bindings: tuple
    #: per read-port: whether the channel came from an author map or identity-desugar.
    read_sources: dict[str, PortSource] = field(default_factory=dict)
    #: stage-4 join keys (see ``ir/graph.py`` GraphNode): the top-level declaration-entry
    #: index, the Python qualified name to resolve, and the owning-composition locator.
    entry_ordinal: int = 0
    callable_ref: str | None = None
    composition_path: str | None = None
    member_name: str | None = None


@dataclass
class _Channels:
    """Accumulated per-channel evidence the topology checks run over."""

    wiring: dict[str, list[_WiringPort]] = field(default_factory=dict)
    #: type-only assertions (composition boundary in/out, pipeline in/out) — they
    #: participate in type agreement but are NOT readers/writers (no closure/overlap role).
    expectations: dict[str, list[tuple[ChannelFieldType, str]]] = field(default_factory=dict)
    free_vars: set[str] = field(default_factory=set)

    def wire(self, channel: str, port: _WiringPort) -> None:
        self.wiring.setdefault(channel, []).append(port)

    def expect(self, channel: str, type_: ChannelFieldType, label: str) -> None:
        self.expectations.setdefault(channel, []).append((type_, label))


@dataclass
class _Boundary:
    """A unit's declared ``[inputs]`` / ``[outputs]`` boundary — the top-level pipeline's, or an
    embedded composition's after flatten. The two boundary checks (type-participation +
    dead-declaration) run identically over either: the one shared mechanism the mirror-pipeline
    principle requires (``hash-model.md`` § the-mirror-pipeline-principle; R-pipeline-001
    read/write shape-matching "the embedded declaration's inputs / outputs participate after
    flatten" + inputs/outputs resolution). The boundary channel name IS the field name in both
    layers: the pipeline's ``[inputs]`` / ``[outputs]`` and a composition's *unscoped* boundary
    channels (the contact with the outer graph) alike."""

    inputs: tuple[FieldDecl, ...]
    outputs: tuple[FieldDecl, ...]
    ref: str  # composition_ref for diagnostics (the pipeline name, or the embed's comp_ref)
    in_label: str
    out_label: str
    #: the flattened node positions whose reads/writes count as "in scope" for the
    #: dead-declaration check — ``None`` means the whole graph (the top-level pipeline). A
    #: composition passes its OWN positions so an outer reader/writer of an unscoped boundary
    #: channel does not mask a dead composition boundary declaration.
    node_positions: set[int] | None


# ---------------------------------------------------------------------------
# Merge strategy → channel-type constraint (pipeline/reference.md § merge.<channel>)
# ---------------------------------------------------------------------------


def _strategy_accepts(strategy: MergeStrategy, channel_type: ChannelFieldType) -> bool:
    if isinstance(channel_type, OptionalType):
        # Merge requires a non-optional base type — the engine does NOT see through the
        # `<T> | None` wrapper; nullable-channel fan-in is the aggregator's territory
        # (pipeline/reference.md § merge.<channel>; the constrain ruling, 2026-06-07).
        return False
    # The per-strategy base-type constraint lives with the strategy's total definition
    # (conjured.ir.merge — one table both this compose check and the runner's fold read,
    # so a new member cannot land type-checked but fold-less).
    return MERGE_STRATEGY_DEFS[strategy].accepts(channel_type)


# ---------------------------------------------------------------------------
# Within-group aggregation → fail-fast across groups (the cited error-reporting policy)
# ---------------------------------------------------------------------------


def _finalize(violations: list[ContractViolation]) -> None:
    """Raise a check group's collected violations at its boundary — the
    aggregate-within-a-group, fail-fast-across-groups policy (pipeline/reference.md
    § Composition validation; error-channel/reference.md § ContractViolationGroup).

    No violations → return (the group passed). Exactly one → raise the **bare**
    ``ContractViolation`` (the common case; single-fault diagnosis and the existing
    single-violation consumers stay unchanged). **≥ 2** → raise a
    :class:`~conjured.errors.ContractViolationGroup` wrapping them in detection order.
    Called after each group so a non-empty group short-circuits the later groups whose
    preconditions it invalidated (fail-fast across groups)."""
    if not violations:
        return
    if len(violations) == 1:
        raise violations[0]
    raise ContractViolationGroup(tuple(violations))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compile_pipeline(
    pipeline: PipelineDeclaration,
    registry: DeclarationRegistry,
    *,
    pipeline_name: str,
    deployment: DeploymentDeclaration | None = None,
    file_path: str = "<pipeline>",
    embed_stack: tuple[str, ...] = (),
) -> CompiledGraph:
    """Validate ``pipeline`` against ``registry`` (+ optional ``deployment``) and compile it
    into a :class:`~conjured.ir.graph.CompiledGraph`. Raises ``ContractViolation`` before any
    dispatch. ``pipeline_name`` is the pipeline's qualified name (the caller / registry key —
    ``PipelineDeclaration`` carries no name field); used for ``composition_ref`` and to resolve
    deployment ``pipelines.<name>`` overrides.

    ``embed_stack`` is the engine's recursive nested-``pipeline``-embed context: the chain of
    composition declaration paths currently being compiled, threaded so a pipeline that
    transitively embeds itself — the only non-terminating case under static nesting — is
    rejected as a ``ContractViolation`` when the embed graph is resolved at compose
    (pipeline/reference.md § The nested ``pipeline`` composition kind, Termination). Callers
    other than the engine's own recursion leave it defaulted.
    """
    deployment = deployment if deployment is not None else registry.deployment

    # Pure-substitution embeds resolve FIRST (glossary § Bundle TOML): every bundle node is
    # textually substituted into `nodes` before anything scopes, validates, or hashes — all
    # the walks below (flatten, streamable placement, hook + transport coverage) see the
    # post-substitute inlined form, as if the nodes had been declared directly here. A
    # transitive bundle self-embed fails loud here (COMPOSITION_CYCLE), before any group runs.
    substituted = substitute_bundle_nodes(
        pipeline.nodes, registry.get_composition, where=pipeline_name,
    )
    if substituted is not pipeline.nodes:
        pipeline = pipeline.model_copy(update={"nodes": substituted})

    # --- Group A: registry resolution + flatten (desugar, scope, per-kind cardinality) ---
    # Each group COLLECTS its independently-detectable violations, then _finalize raises at
    # the group boundary (bare CV for one; ContractViolationGroup for >=2) — and a non-empty
    # group short-circuits the later groups whose preconditions it invalidated (fail-fast
    # across groups; the cited error-reporting policy).
    violations_a: list[ContractViolation] = []
    # A composition's config + service-binding SUPPLY faults are detected during flatten (the
    # resolved backend is in scope only there) but REPORT in Group B — they are supply/topology
    # concerns whose preconditions are the graph, not Group-A resolution, so a composition supply
    # fault must co-report with pipeline-level supply + topology in one load (37#5). Collected
    # here, extended into violations_b below.
    comp_supply_violations: list[ContractViolation] = []
    flat_nodes, merges, supplies, scoped_channels, comp_boundaries = _flatten(
        pipeline, registry, pipeline_name, violations_a,
        supply_violations=comp_supply_violations,
        deployment=deployment, embed_stack=embed_stack,
    )
    _finalize(violations_a)

    # --- Group B: graph topology over the flattened, normalized wiring IR ---
    violations_b: list[ContractViolation] = []
    channels = _collect_channels(flat_nodes, pipeline)
    # The input/output boundary participates in channel type-agreement AND the dead-declaration
    # check through ONE shared mechanism, run over the top-level pipeline boundary and every
    # embedded composition boundary alike (R-pipeline-001 read/write shape-matching "the embedded
    # declaration's inputs / outputs participate after flatten" + inputs/outputs resolution; the
    # mirror-pipeline principle, hash-model.md). The pipeline's own boundary spans the whole graph
    # (node_positions=None); each composition boundary spans only its own flattened nodes.
    boundaries = [
        _Boundary(
            inputs=pipeline.inputs, outputs=pipeline.outputs or (),
            ref=pipeline_name, in_label="pipeline [inputs]", out_label="pipeline [outputs]",
            node_positions=None,
        ),
        *comp_boundaries,
    ]
    for boundary in boundaries:
        _register_boundary_expectations(channels, boundary)

    channel_types, type_mismatch_channels = _type_match(
        channels, pipeline_name, violations_b
    )
    _check_single_assignment(flat_nodes, pipeline_name, violations_b)
    _check_write_overlap(
        channels, channel_types, merges, pipeline_name,
        violations_b, type_mismatch_channels,
    )
    _check_input_closure(channels, pipeline_name, violations_b)
    for boundary in boundaries:
        _check_boundary_dead(channels, boundary, violations_b)
    _check_binding_supply(pipeline, registry, flat_nodes, pipeline_name, violations_b)
    # Composition-level supply faults report HERE, alongside pipeline-level supply + topology —
    # so one supply-fault class reports in one load whether the fault is composition- or
    # pipeline-level (37#5; detected during flatten where the backend is resolved).
    violations_b.extend(comp_supply_violations)
    _finalize(violations_b)

    # --- Group C: deployment coverage (only when a deployment is paired) ---
    violations_c: list[ContractViolation] = []
    if deployment is not None:
        _check_deployment_coverage(
            pipeline, registry, flat_nodes, deployment, pipeline_name, violations_c
        )
    _finalize(violations_c)

    # --- Assemble the CompiledGraph ---
    nodes = tuple(
        GraphNode(
            position=fn.position, node_kind=fn.node_kind, qualified_name=fn.qualified_name,
            input_ports=fn.input_ports, output_ports=fn.output_ports,
            read_map=dict(fn.read_map), write_map=dict(fn.write_map), bindings=fn.bindings,
            entry_ordinal=fn.entry_ordinal, callable_ref=fn.callable_ref,
            composition_path=fn.composition_path, member_name=fn.member_name,
        )
        for fn in flat_nodes
    )
    graph_channels = tuple(
        Channel(name=name, type=channel_types[name], scoped=name in scoped_channels)
        for name in sorted(channel_types)
    )
    merge_ops = tuple(MergeOp(channel=ch, strategy=st) for ch, st in merges.items())
    return CompiledGraph(
        pipeline_name=pipeline_name, source_path=file_path,
        nodes=nodes, channels=graph_channels, inputs=pipeline.inputs, outputs=pipeline.outputs,
        merges=merge_ops, service_bindings=supplies,
        # Carry the declaration this graph was compiled from, so stage-4 assembly hashes the
        # exact source (structural hash↔graph correspondence — never a by-name re-fetch).
        source_declaration=pipeline,
    )


# ---------------------------------------------------------------------------
# Flatten: resolve names, desugar maps, scope composition channels, per-kind cardinality
# ---------------------------------------------------------------------------


def _flatten(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry, pipeline_name: str,
    violations: list[ContractViolation],
    *, supply_violations: list[ContractViolation],
    deployment: DeploymentDeclaration | None = None,
    embed_stack: tuple[str, ...] = (),
) -> tuple[
    list[_FlatNode], dict[str, MergeStrategy], tuple[ServiceBindingSupply, ...], set[str],
    list[_Boundary],
]:
    flat: list[_FlatNode] = []
    merges: dict[str, MergeStrategy] = dict(pipeline.merge)
    #: one _Boundary per embedded composition (its [inputs]/[outputs] + the positions of its
    #: flattened nodes) — the shared boundary checks run over these alongside the pipeline's own.
    boundaries: list[_Boundary] = []
    #: every channel a composition flatten rescoped (``<meta.name>.<channel>``) — recorded
    #: here, where the scoping happens, so scoped-ness is structural on the Channel rather
    #: than re-derived from the name pattern.
    scoped_channels: set[str] = set()
    position = 0
    #: composition meta.name → the composition-node it was first seen on. The engine requires
    #: a trainable's meta.name unique within the embedding pipeline's namespace — it keys the
    #: trained-artifact manifest and scopes the composition's internal channels
    #: (``<meta.name>.<channel>``); a collision would silently overwrite a manifest entry and
    #: cross-wire two compositions' scoped channels (hash-model.md § Manifest-key shape).
    seen_comp_names: dict[str, str] = {}

    for idx, node in enumerate(pipeline.nodes):
        comp_ref = format_composition_ref(pipeline_name, idx)
        if isinstance(node, HandlerNode):
            decl = registry.get_handler(node.name)
            if decl is None:
                violations.append(ContractViolation(
                    check=Check.HANDLER_NAME_RESOLUTION, rule_id="R-pipeline-001",
                    expected=f"node name '{node.name}' resolves to a handler declaration in the registry",
                    actual="no such handler declaration", composition_ref=comp_ref,
                    remediation_hint="register the handler declaration, or fix the qualified name",
                ))
                continue  # unresolved node — nothing downstream to build from
            _check_handler_cardinality(decl, node.name, comp_ref, violations)
            input_ports = tuple(Port(name=f.name, type=f.type) for f in decl.reads)
            output_ports = (
                tuple(Port(name=f.name, type=f.type) for f in decl.output_schema)
                if not isinstance(decl, HookDeclaration) else ()
            )
            read_map, read_sources = _desugar(node.reads_map, input_ports, "reads_map", comp_ref, node.name, violations)
            write_map, _ = _desugar(node.writes_map, output_ports, "writes_map", comp_ref, node.name, violations)
            flat.append(_FlatNode(
                position=position, node_kind=decl.kind, qualified_name=node.name,
                input_ports=input_ports, output_ports=output_ports,
                read_map=read_map, write_map=write_map, bindings=node.bindings, read_sources=read_sources,
                entry_ordinal=idx, callable_ref=node.name,
            ))
            position += 1

        elif isinstance(node, CompositionNode):
            comp = registry.get_composition(node.name)
            if comp is None:
                violations.append(ContractViolation(
                    check=Check.HANDLER_NAME_RESOLUTION, rule_id="R-pipeline-001",
                    expected=f"composition path '{node.name}' resolves to a composition declaration",
                    actual="no such composition declaration", composition_ref=comp_ref,
                    remediation_hint="register the trainable composition declaration, or fix the path",
                ))
                continue  # unresolved composition — nothing downstream to build from
            if comp.meta.name in seen_comp_names:
                violations.append(ContractViolation(
                    check=Check.NAME_UNIQUENESS, rule_id="R-pipeline-001",
                    expected=f"each composition node's meta.name is unique within pipeline '{pipeline_name}'",
                    actual=f"meta.name '{comp.meta.name}' is also used by node '{seen_comp_names[comp.meta.name]}'",
                    composition_ref=comp_ref,
                    remediation_hint="rename one composition's [meta].name — it keys the trained-artifact manifest and scopes its channels",
                ))
                continue  # a colliding meta.name would cross-wire scoped channels — skip flatten
            seen_comp_names[comp.meta.name] = comp_ref
            if isinstance(comp, BundleComposition):  # pragma: no cover - substitution precedes flatten
                raise AssertionError(
                    "a bundle reached _flatten — substitution precedes flatten (engine drift)"
                )
            if isinstance(comp, PipelineComposition):
                # Streamable-terminal is transitive: an embed whose own terminal streams may
                # itself be followed only by hooks (evaluated before flatten, over the outer nodes).
                _check_streamable_terminal(comp, pipeline, idx, registry, comp_ref, violations)
                position = _flatten_pipeline_embed(
                    comp, registry, position, comp_ref, flat,
                    entry_ordinal=idx, composition_path=node.name,
                    deployment=deployment, embed_stack=embed_stack,
                    violations=violations,
                )
                continue
            _check_streamable_terminal(comp, pipeline, idx, registry, comp_ref, violations)
            position = _flatten_trainable(
                comp, registry, position, comp_ref, flat, merges,
                entry_ordinal=idx, composition_path=node.name,
                scoped_channels=scoped_channels, boundaries=boundaries,
                violations=violations, supply_violations=supply_violations,
            )
        else:  # pragma: no cover - PipelineNode is a closed union
            raise AssertionError("unreachable node kind")

    # The outer direction of the merge scope rule (pipeline/reference.md § merge Scope:
    # "An outer pipeline's `merge` declaration cannot reach into an embedded trainable
    # composition's internal scoped channels" — cross-scope merges are structurally
    # impossible): an authored outer merge key literally spelled `<meta.name>.<channel>`
    # would otherwise land in the merge table and govern the composition's internal
    # scoped channel. Checked here, after every composition has flattened (the scoped
    # set is complete only then), and evicted so it can never govern.
    for ch in pipeline.merge:
        if ch in scoped_channels:
            violations.append(ContractViolation(
                check=Check.CHANNEL_WRITE_OVERLAP, rule_id="R-pipeline-002",
                expected="outer-pipeline merge entries name the outer pipeline's own "
                         "channels — an embedded composition's internal scoped "
                         "channels are outside the outer merge's scope",
                actual=f"merge.{ch} resolves to an embedded composition's internal "
                       "scoped channel",
                composition_ref=pipeline_name, section_path=f"merge.{ch}",
                remediation_hint="declare the merge inside the composition that owns "
                                 "the channel (its own [merge] block); the outer "
                                 "pipeline's merge covers only its own channels",
            ))
            merges.pop(ch, None)

    return flat, merges, pipeline.service_bindings, scoped_channels, boundaries


def _check_handler_cardinality(decl, name: str, comp_ref: str, violations: list[ContractViolation]) -> None:
    """Service: exactly one service-typed binding; hook: ≤ 1 (R-handler-008 / R-handler-009
    cardinality — the trainable-backend property is Phase 2). Transform forbids the section
    structurally (no field on the IR)."""
    if isinstance(decl, ServiceDeclaration) and len(decl.service_bindings) != 1:
        violations.append(ContractViolation(
            check=Check.SERVICE_BINDING_CARDINALITY, rule_id="R-handler-008",
            expected="a service handler declares exactly one service-typed binding",
            actual=f"'{name}' declares {len(decl.service_bindings)}", composition_ref=comp_ref,
            remediation_hint="zero → it is a transform; multiple → split into separate service handlers",
        ))
    if isinstance(decl, HookDeclaration) and len(decl.service_bindings) > 1:
        violations.append(ContractViolation(
            check=Check.SERVICE_BINDING_CARDINALITY, rule_id="R-handler-009",
            expected="a hook declares at most one service-typed binding",
            actual=f"'{name}' declares {len(decl.service_bindings)}", composition_ref=comp_ref,
            remediation_hint="split a multi-backend hook into one hook per emission target",
        ))


def _desugar(
    authored: Mapping[str, str], ports: tuple[Port, ...], which: str, comp_ref: str, name: str,
    violations: list[ContractViolation],
) -> tuple[dict[str, str], dict[str, PortSource]]:
    """The single compose-time normalization: validate the author's map keys against the
    declared ports, then desugar every unmapped port to a same-named channel (identity) via
    the shared :func:`~conjured.validator.normalize.desugar_map` step (3a — the compiler and
    hasher normalize identically). Returns the total normalized map + per-port source
    (mapped / identity).

    Each undeclared map key is collected (the WIRING_MAP_PORT check). On any such key the
    node still builds from its **valid** keys (best-effort) so the rest of Group A's
    independently-detectable checks run; the group fails before any stage-2 check reads this
    map (fail-fast across groups), so the dropped keys never reach topology."""
    port_names = {p.name for p in ports}
    valid_authored = {k: v for k, v in authored.items() if k in port_names}
    for key in authored:
        if key not in port_names:
            violations.append(ContractViolation(
                check=Check.WIRING_MAP_PORT, rule_id="R-pipeline-001",
                expected=f"every {which} key is a declared port of '{name}' ({sorted(port_names)})",
                actual=f"key '{key}' is not a declared port", composition_ref=comp_ref,
                remediation_hint=f"a {which} key must name one of the handler's declared ports",
            ))
    normalized = desugar_map(valid_authored, [p.name for p in ports])  # ordered: preserve port order
    sources: dict[str, PortSource] = {
        p.name: ("mapped" if p.name in valid_authored else "identity") for p in ports
    }
    return normalized, sources


def _flatten_trainable(
    comp: TrainableComposition, registry: DeclarationRegistry, position: int, comp_ref: str,
    flat: list[_FlatNode], merges: dict[str, MergeStrategy],
    *, entry_ordinal: int, composition_path: str, scoped_channels: set[str],
    boundaries: list[_Boundary], violations: list[ContractViolation],
    supply_violations: list[ContractViolation],
) -> int:
    """Flatten a trainable composition into the outer graph: preprocessors (in order) + the
    terminal trainable, with internal channels scoped ``<comp_name>.<channel>`` and the
    declared ``inputs`` / ``outputs`` left unscoped (the boundary contact with the outer
    pipeline) — pipeline/reference.md § nodes + § Pipeline load lifecycle stage 2. Records a
    :class:`_Boundary` for the composition so its boundary types participate in type-matching and
    its dead-declaration check fires, mirroring the top-level pipeline (the mirror-pipeline
    principle)."""
    cname = comp.meta.name
    start_position = position
    boundary = {f.name for f in comp.inputs} | {f.name for f in comp.outputs}

    def scope(channel: str) -> str:
        if channel in boundary:
            return channel
        scoped = f"{cname}.{channel}"
        scoped_channels.add(scoped)
        return scoped

    # Trainable-backend cardinality + config-schema gate (the trainable-backend *property*
    # needs the resolved adapter — Phase 2). The backend cardinality and its service-type
    # resolution stay in Group A: they are PRECONDITIONS for the config / supply checks
    # (deferred to Group B, below) — the resolved backend must exist for those to run, and
    # Group A must pass for Group B to run at all. On failure collect and skip this
    # composition's remaining checks (the within-group "independently-detectable" boundary).
    if len(comp.trainable.service_bindings) != 1:
        violations.append(ContractViolation(
            check=Check.SERVICE_BINDING_CARDINALITY, rule_id="R-handler-008",
            expected="a trainable composition declares exactly one service-typed binding (the backend)",
            actual=f"'{cname}' declares {len(comp.trainable.service_bindings)}", composition_ref=comp_ref,
        ))
        return position  # cannot resolve the backend — skip this composition's flatten
    backend_type = comp.trainable.service_bindings[0].type
    service_type = registry.get_service_type(backend_type)
    if service_type is None:
        violations.append(ContractViolation(
            check=Check.SERVICE_TYPE_RESOLUTION, rule_id="R-pipeline-001",
            expected=f"the trainable backend service-type '{backend_type}' resolves in the registry",
            actual="no such service-type declaration", composition_ref=comp_ref,
        ))
        return position  # backend unresolved — its config / supply checks can't run
    # config + service-binding SUPPLY are Group-B (graph-topology) concerns per canon
    # (pipeline/reference.md § Composition validation — the three-groups passage): a
    # composition supply fault invalidates no topology precondition, so it must report WITH
    # pipeline-level supply + topology in one load, not short-circuit Group B from Group A.
    # Collected into supply_violations (extended into violations_b by compile_pipeline).
    # Within-group aggregation: EVERY supply fault in the block reports (COMPILE-4 —
    # the collecting scan replaces catch-one-raise).
    supply_violations.extend(_check_trainable_config(comp, service_type, comp_ref))
    _check_composition_binding_supply(comp, registry, comp_ref, supply_violations)

    # Internal merges are scoped to this composition — and scope-VALIDATED: a
    # composition's `merge` governs its internal channel conflicts only (R-handler-006;
    # pipeline/reference.md § merge Scope — "cross-scope merges are structurally
    # impossible"). Two silent paths are closed here: a boundary-channel entry would
    # silently promote into the outer pipeline's merge table (scope() leaves boundary
    # names unscoped), and a key collision with an already-present outer entry would
    # silently overwrite it (plain dict assignment, composition-last-wins).
    for ch, st in comp.merge.items():
        if ch in boundary:
            violations.append(ContractViolation(
                check=Check.CHANNEL_WRITE_OVERLAP, rule_id="R-pipeline-002",
                expected=f"composition '{cname}' merge entries name its INTERNAL "
                         "channels only (a composition merge governs internal channel "
                         "conflicts; its boundary channels are the outer pipeline's "
                         "merge scope)",
                actual=f"merge.{ch} names the composition's own boundary channel",
                composition_ref=comp_ref, section_path=f"merge.{ch}",
                remediation_hint="a fan-in on a boundary channel is the OUTER "
                                 "pipeline's merge declaration; move the entry there "
                                 "(or rename the internal channel if the fan-in is "
                                 "internal)",
            ))
            continue  # cross-scope — never promoted into the outer merge table
        scoped_key = scope(ch)
        if scoped_key in merges:
            violations.append(ContractViolation(
                check=Check.CHANNEL_WRITE_OVERLAP, rule_id="R-pipeline-002",
                expected=f"one merge strategy per channel — '{scoped_key}' is declared "
                         "once across the outer pipeline and its compositions",
                actual=f"merge key collision on '{scoped_key}' (an outer-pipeline "
                       f"entry already governs it; composition '{cname}' declares it "
                       "again)",
                composition_ref=comp_ref, section_path=f"merge.{ch}",
                remediation_hint="cross-scope merges are structurally impossible — "
                                 "remove the outer entry spelled as the composition's "
                                 "scoped channel; each scope declares its own merges",
            ))
            continue  # fail loud, never silently overwrite
        merges[scoped_key] = st

    # Preprocessor ids must be unique within the composition: each qualifies to
    # ``<meta.name>.<id>`` (the flattened node's qualified_name); a duplicate would collide
    # there (composition.py IR — preprocessor id "unique in this composition"). The
    # uniqueness check runs in the flatten loop (collect every dup, skip flattening it so
    # positions stay contiguous).
    seen_preproc_ids: set[str] = set()
    for pidx, preproc in enumerate(comp.preprocessors):
        if preproc.id in seen_preproc_ids:
            violations.append(ContractViolation(
                check=Check.NAME_UNIQUENESS, rule_id="R-handler-006",
                expected=f"each [[preprocessors]] id is unique within composition '{cname}'",
                actual=f"preprocessor id '{preproc.id}' is declared more than once",
                composition_ref=comp_ref,
                remediation_hint=f"re-label one preprocessor — its qualified name '{cname}.{preproc.id}' must be unique",
            ))
            continue  # skip the dup — flattening it would collide its scoped qualified name
        seen_preproc_ids.add(preproc.id)
        pref = f"{comp_ref}.{cname}.{preproc.id}"
        decl = registry.get_handler(preproc.name)
        if decl is None:
            violations.append(ContractViolation(
                check=Check.HANDLER_NAME_RESOLUTION, rule_id="R-pipeline-001",
                expected=f"preprocessor name '{preproc.name}' resolves to a handler declaration in the registry",
                actual="no such handler declaration", composition_ref=comp_ref,
                remediation_hint="register the handler declaration, or fix the qualified name",
            ))
            continue  # unresolved preprocessor — nothing downstream to build from
        # A preprocessor is a name-reference resolved exactly like an outer node: ports +
        # cardinality come from the referenced declaration (the mirror-pipeline principle).
        _check_handler_cardinality(decl, preproc.id, pref, violations)
        input_ports = tuple(Port(name=f.name, type=f.type) for f in decl.reads)
        output_ports = (
            tuple(Port(name=f.name, type=f.type) for f in decl.output_schema)
            if not isinstance(decl, HookDeclaration) else ()
        )
        read_map, read_sources = _desugar(preproc.reads_map, input_ports, "reads_map", pref, preproc.id, violations)
        write_map, _ = _desugar(preproc.writes_map, output_ports, "writes_map", pref, preproc.id, violations)
        flat.append(_FlatNode(
            position=position, node_kind=decl.kind,
            qualified_name=f"{cname}.{preproc.id}",
            input_ports=input_ports, output_ports=output_ports,
            read_map={k: scope(v) for k, v in read_map.items()},
            write_map={k: scope(v) for k, v in write_map.items()},
            bindings=preproc.bindings, read_sources=read_sources,
            entry_ordinal=entry_ordinal, callable_ref=preproc.name,
            composition_path=composition_path, member_name=preproc.id,
        ))
        position += 1

    # The terminal trainable node — engine-constructed dispatch (R-handler-010), identity
    # wiring (TrainableNode carries no authored maps); node_kind = "trainable".
    t = comp.trainable
    t_inputs = tuple(Port(name=f.name, type=f.type) for f in t.reads)
    t_outputs = tuple(Port(name=f.name, type=f.type) for f in t.output_schema)
    flat.append(_FlatNode(
        position=position, node_kind="trainable", qualified_name=cname,
        input_ports=t_inputs, output_ports=t_outputs,
        read_map={p.name: scope(p.name) for p in t_inputs},
        write_map={p.name: scope(p.name) for p in t_outputs},
        bindings=(), read_sources={p.name: "identity" for p in t_inputs},
        entry_ordinal=entry_ordinal, composition_path=composition_path,
    ))
    # The composition's boundary: its [inputs]/[outputs] + the positions of its flattened nodes
    # (preprocessors + the terminal, [start_position, position]). The shared boundary checks run
    # over it exactly as over the pipeline's, scoped to these positions so an outer reader/writer
    # of an unscoped boundary channel cannot mask a dead composition declaration.
    boundaries.append(_Boundary(
        inputs=comp.inputs, outputs=comp.outputs, ref=comp_ref,
        in_label=f"composition '{cname}' [inputs]",
        out_label=f"composition '{cname}' [outputs]",
        node_positions=set(range(start_position, position + 1)),
    ))
    return position + 1


def _flatten_pipeline_embed(
    comp: PipelineComposition, registry: DeclarationRegistry, position: int, comp_ref: str,
    flat: list[_FlatNode],
    *, entry_ordinal: int, composition_path: str,
    deployment: DeploymentDeclaration | None,
    embed_stack: tuple[str, ...], violations: list[ContractViolation],
) -> int:
    """Resolve a nested ``pipeline`` composition embed into ONE dispatched node
    (pipeline/reference.md § The nested ``pipeline`` composition kind — engine-invoking-engine:
    the inner pipeline runs as its own invocation inside the outer walk; its nodes do NOT
    flatten into the outer dispatch order and its internal channels never join the outer graph
    — the inner scope is opaque, mirroring the own-hash-domain hash treatment).

    Two compose-time obligations discharge here, before any node dispatches:

    - **Cycle rejection (Termination — compose-time, cycle-only).** The embed graph is
      resolved by recursion; a composition path already on ``embed_stack`` means this pipeline
      transitively embeds itself — the only non-terminating case under static nesting —
      rejected as a ``ContractViolation``, structural, never a runtime guard. A finite acyclic
      nesting has no depth ceiling: no ``max_depth``, no depth cap.
    - **The whole nested structure type-checks at load (I2).** The inner pipeline is
      recursively compiled (all its groups, with the same deployment) so a statically-declared
      nesting is verified whole before it runs; inner violations surface with their own inner
      ``composition_ref`` attribution.

    The embed node's boundary is the existing composition-embed mirror: the inner ``[inputs]``
    become the node's input ports and the inner ``[outputs]`` its output ports (presence-opts-in
    — an inner pipeline with no ``[outputs]`` writes no outer channels), each wired to the
    same-named outer channel (the flatten-by-name boundary contact; a composition node carries
    no authored maps). Type agreement / closure / merge counting then run over it exactly as
    over any node."""
    # guarantees: nested-embed-cycle-rejected
    if composition_path in embed_stack:
        violations.append(ContractViolation(
            check=Check.COMPOSITION_CYCLE, rule_id="R-pipeline-001",
            expected="the nested-pipeline embed graph is acyclic — a pipeline never "
                     "transitively embeds itself (the only non-terminating case under "
                     "static nesting)",
            actual="embed cycle: " + " -> ".join((*embed_stack, composition_path)),
            composition_ref=comp_ref,
            remediation_hint="break the cycle — restructure the shared content into a "
                             "composition both pipelines embed, or lift the loop to consumer "
                             "orchestration (runtime iteration is the consumer's)",
        ))
        return position  # a cyclic embed never loads — nothing downstream to build from
    try:
        # The recursive compile IS the "whole nested structure type-checks at load" guarantee:
        # every inner group runs (same deployment; transport/hook coverage resolve under the
        # INNER pipeline's own meta.name — the family rule's one identity model).
        compile_pipeline(
            comp.pipeline, registry,
            pipeline_name=comp.meta.name, deployment=deployment,
            file_path=composition_path,
            embed_stack=(*embed_stack, composition_path),
        )
    except ContractViolationGroup as group:
        violations.extend(group.violations)
        return position  # an invalid inner pipeline — the embed has nothing to wire
    except ContractViolation as cv:
        violations.append(cv)
        return position
    inputs = tuple(Port(name=f.name, type=f.type) for f in comp.pipeline.inputs)
    outputs = tuple(Port(name=f.name, type=f.type) for f in (comp.pipeline.outputs or ()))
    flat.append(_FlatNode(
        position=position, node_kind="pipeline", qualified_name=comp.meta.name,
        input_ports=inputs, output_ports=outputs,
        read_map={p.name: p.name for p in inputs},
        write_map={p.name: p.name for p in outputs},
        bindings=(), read_sources={p.name: "identity" for p in inputs},
        entry_ordinal=entry_ordinal, composition_path=composition_path,
    ))
    return position + 1


def _embed_terminal_streams(comp, registry, _seen: tuple[str, ...] = ()) -> bool:
    """True iff a nested ``pipeline`` embed's transitive terminal-modulo-hooks node streams —
    a ``streamable`` trainable, or a deeper pipeline embed whose own terminal streams. The
    declaration-level mirror of the runtime ``stream_route_position`` recursion (runner/run.py),
    so the streamable-terminal placement rule is evaluated **transitively** through a terminal
    embed exactly as canon states (pipeline/reference.md § streamable terminal-node). Bundle
    nodes are **substituted first** — the same mechanism every other walker applies — so a
    terminal bundle whose substituted content ends with a streamable trainable is seen exactly
    as if directly declared (canon's substitution rule makes the two forms equivalent; treating
    a bundle terminal as non-streaming was the R-pipeline-001 under-enforcement this closes).
    ``_seen`` breaks a cyclic embed — the same case
    ``_flatten_pipeline_embed`` rejects with COMPOSITION_CYCLE; here it just yields False so the
    predicate terminates and the cycle violation surfaces from the flatten pass."""
    if comp.meta.name in _seen:
        return False  # cyclic embed — the cycle is the flatten pass's COMPOSITION_CYCLE violation
    _seen = (*_seen, comp.meta.name)
    nodes = substitute_bundle_nodes(
        comp.pipeline.nodes, registry.get_composition, where=comp.meta.name
    )
    for node in reversed(nodes):
        if isinstance(node, CompositionNode):
            inner = registry.get_composition(node.name)
            if inner is None:
                return False  # unresolved — its own resolution violation fires in flatten
            if isinstance(inner, PipelineComposition):
                return _embed_terminal_streams(inner, registry, _seen)
            return bool(getattr(inner, "trainable", None)) and inner.trainable.streamable
        decl = registry.get_handler(node.name)  # a HandlerNode
        if decl is not None and isinstance(decl, HookDeclaration):
            continue  # hooks may follow a streamable terminal — keep scanning leftward
        return False  # a non-hook, non-streamable terminal-modulo-hooks node
    return False


def _reject_non_hook_followers(
    pipeline, idx: int, registry, comp_ref: str, violations: list[ContractViolation]
) -> None:
    """Append one STREAMABLE_TERMINAL violation if any non-hook node follows position ``idx``
    (the first non-hook follower establishes the fault — additional followers are the same
    design error), collected into the Group-A report."""
    for later in pipeline.nodes[idx + 1 :]:
        if isinstance(later, CompositionNode):
            violations.append(ContractViolation(
                check=Check.STREAMABLE_TERMINAL, rule_id="R-pipeline-001",
                expected="a streamable trainable is the pipeline's terminal node (only hooks may follow)",
                actual="a composition node follows it", composition_ref=comp_ref,
            ))
            return
        decl = registry.get_handler(later.name)
        if decl is not None and not isinstance(decl, HookDeclaration):
            violations.append(ContractViolation(
                check=Check.STREAMABLE_TERMINAL, rule_id="R-pipeline-001",
                expected="a streamable trainable is the pipeline's terminal node (only hooks may follow)",
                actual=f"non-hook node '{later.name}' follows it", composition_ref=comp_ref,
            ))
            return


def _check_streamable_terminal(comp, pipeline, idx: int, registry, comp_ref: str, violations: list[ContractViolation]) -> None:
    """A streamable trainable MUST be terminal — only hooks may follow (R-pipeline-001
    streamable terminal-node placement), evaluated **transitively** through a terminal nested
    ``pipeline`` embed: an embed whose own transitive terminal streams is itself a streamable
    terminal for the enclosing pipeline, so a non-hook node following it is the same fault."""
    if isinstance(comp, PipelineComposition):
        streams = _embed_terminal_streams(comp, registry)
    else:
        streams = comp.trainable.streamable
    if not streams:
        return
    _reject_non_hook_followers(pipeline, idx, registry, comp_ref, violations)


def config_supply_violations(
    supplied: Mapping[str, object],
    service_type: ServiceTypeDeclaration,
    *,
    composition_ref: str,
    section_path: str,
) -> list[ContractViolation]:
    """EVERY independently-detectable ``[config_schema]`` supply fault in one block —
    the within-group aggregation source (pipeline/reference.md region
    ``composition-validation/error-reporting``: within a check group the engine reports
    every independently-detectable failure, not only the first). The compose call sites
    collect this full list into their ``ContractViolationGroup``; :func:`effective_config`
    stays the raising derivation (first fault) for assembly and the hasher, delegating
    its fault scan here — one scan, two consumption modes."""
    declared = {f.name: f for f in service_type.config_schema}
    faults: list[ContractViolation] = []
    for key in supplied:
        if key not in declared:
            faults.append(ContractViolation(
                check=Check.CONFIG_SCHEMA_SUPPLY, rule_id="R-service-type-002",
                expected=f"every supplied config key at {section_path} is a declared "
                         f"[config_schema] field of '{service_type.name}' ({sorted(declared)})",
                actual=f"undeclared config key '{key}'", composition_ref=composition_ref,
                section_path=section_path,
                remediation_hint="prompt-shaping content arrives via reads, never config "
                                 "(R-handler-011); a generation parameter needs a declared "
                                 "[config_schema] field",
            ))
    for key, supplied_value in supplied.items():
        if key in declared and is_explicit_null(
            supplied_value, owner=f"{section_path}.{key}",
            section_path=f"{section_path}.{key}", composition_ref=composition_ref,
        ):
            faults.append(ContractViolation(
                check=Check.EXPLICIT_NULL_TARGET, rule_id="R-pipeline-001",
                expected="{ null = true } targets a nullable-declared field",
                actual=f"config field '{key}' — config fields admit no nullable declaration",
                composition_ref=composition_ref, section_path=f"{section_path}.{key}",
            ))
    for name, fld in declared.items():
        if name not in supplied and not fld.has_default:
            faults.append(ContractViolation(
                check=Check.CONFIG_SCHEMA_SUPPLY, rule_id="R-service-type-002",
                expected=f"every declared [config_schema] field of '{service_type.name}' "
                         f"is covered at {section_path} — supplied, or carrying a declared "
                         "ship-time default",
                actual=f"config field '{name}' is neither supplied nor default-bearing",
                composition_ref=composition_ref, section_path=section_path,
                remediation_hint=f"supply {name} = <value>, or declare a ship-time "
                                 "default on the service-type's [config_schema] field",
            ))
            continue
        if isinstance(fld.type, TableType):
            effective_value = supplied[name] if name in supplied else fld.default
            bad = first_non_json_expressible(effective_value)
            if bad is not None:
                faults.append(ContractViolation(
                    check=Check.CONFIG_SCHEMA_SUPPLY, rule_id="R-service-type-002",
                    expected=f"the 'table' config field '{name}' of '{service_type.name}' "
                             "holds a JSON-expressible value (strings, integers, floats, "
                             "booleans, and arrays/tables of these)",
                    actual=f"a non-JSON {bad} value for table field '{name}'",
                    composition_ref=composition_ref, section_path=f"{section_path}.{name}",
                    remediation_hint="a table holds JSON-expressible data only; a TOML "
                                     "datetime/date/time cannot fold into the hash as "
                                     "canonical data",
                ))
    return faults


def effective_config(
    supplied: Mapping[str, object],
    service_type: ServiceTypeDeclaration,
    *,
    composition_ref: str,
    section_path: str,
) -> dict[str, object]:
    """The ONE ``[config_schema]`` supply derivation — identical at every config supply
    site (service-type/reference.md § The ``[config_schema]`` contract: a trainable
    composition's ``[trainable.config]``; any other service-typed binding's
    ``service_bindings.<name>`` ``config`` block). Validates coverage in **both
    directions**, each its own ContractViolation, and returns the **effective** values —
    supplied-or-default — per declared field (every config kwarg reaches ``invoke()``
    with a concrete, composition-visible value; the effective values are what fold into
    the hashes). Consumed by the compose checks (which collect the FULL fault list via
    :func:`config_supply_violations` for within-group aggregation), by stage-4 assembly
    (delivery), and by the hasher (the effective-value fold) — one scan, three consumers.
    This raising form surfaces the first fault (assembly/hasher run over
    compose-validated declarations, so any fault here is fail-loud drift)."""
    declared = {f.name: f for f in service_type.config_schema}
    faults = config_supply_violations(
        supplied, service_type,
        composition_ref=composition_ref, section_path=section_path,
    )
    if faults:
        raise faults[0]
    # Fault-free by the scan above: every supplied key declared, every declared field
    # covered (supplied or default-bearing), table values JSON-expressible, no explicit
    # null at a config position — the effective computation is now total.
    return {
        name: (supplied[name] if name in supplied else fld.default)
        for name, fld in declared.items()
    }


def _check_trainable_config(
    comp, service_type: ServiceTypeDeclaration, comp_ref: str
) -> list[ContractViolation]:
    """``[trainable.config]`` is the trainable kind's config supply site — both supply
    directions checked through the shared derivation (R-service-type-002 compose-side;
    service-type/reference.md § The ``[config_schema]`` contract). Returns the FULL
    fault list (within-group aggregation — every independently-detectable supply fault
    in the block reports, not only the first)."""
    return config_supply_violations(
        comp.trainable.config, service_type,
        composition_ref=comp_ref, section_path="trainable.config",
    )


# guarantees: preprocessor-mirrors-outer-node
def _check_schema_binding_supply(
    declared_bindings, supplied_values, node_ref: str, comp_ref: str,
    violations: list[ContractViolation],
) -> None:
    """Compose-time schema/compile binding-supply matching for ONE node, shared by the
    outer-pipeline handler-node loop and the composition's preprocessor loop — one author for
    the supply rule (the mirror-pipeline principle; R-pipeline-001 binding-supply matching).

    A schema binding the handler declares needs a supply UNLESS it declares a ship-time
    default (handler/reference.md § Ship-time defaults); a compile-directive binding is
    engine-owned (the engine produces its value by running the named compiler at binding
    resolution) so a node supply for it is rejected at compose, never silently absorbed into a
    hash (graceful-degrade = training-data corruption); an undeclared supply is rejected.
    ``node_ref`` names the node in the message (an outer node's qualified ``name`` vs a
    preprocessor's ``<comp>.<id>``); ``comp_ref`` is the composition_ref the violation carries.
    """
    supplied_names = {b.name for b in supplied_values}
    for b in declared_bindings:
        if (
            isinstance(b.body, SchemaBinding)
            and b.name not in supplied_names
            and not b.body.has_default
        ):
            violations.append(ContractViolation(
                check=Check.BINDING_SUPPLY, rule_id="R-pipeline-001",
                expected=f"node '{node_ref}' supplies a value for bindings.{b.name}",
                actual=f"bindings.{b.name} unsupplied (and no ship-time default declared)", composition_ref=comp_ref,
                section_path=f"bindings.{b.name}",
                remediation_hint="supply the value inline or by external declaration file, or declare a ship-time default",
            ))
    decl_binding_bodies = {b.name: b.body for b in declared_bindings}
    for supplied in supplied_values:
        body = decl_binding_bodies.get(supplied.name)
        if body is None:
            violations.append(ContractViolation(
                check=Check.BINDING_SUPPLY, rule_id="R-pipeline-001",
                expected=f"every supplied binding matches a declared bindings.<name> on '{node_ref}'",
                actual=f"supplied binding '{supplied.name}' is not declared by the handler",
                composition_ref=comp_ref, section_path=f"bindings.{supplied.name}",
            ))
        elif isinstance(body, CompileBinding):
            violations.append(ContractViolation(
                check=Check.BINDING_SUPPLY, rule_id="R-pipeline-001",
                expected=f"node '{node_ref}' supplies a value only for an author-owned bindings.<name>; the compile-directive binding '{supplied.name}' is engine-owned",
                actual=f"node '{node_ref}' supplies a value for compile-directive binding '{supplied.name}'",
                composition_ref=comp_ref, section_path=f"bindings.{supplied.name}",
                remediation_hint="remove the supply — the engine produces a compile-directive binding's value by running its declared compiler at binding resolution",
            ))


def _check_composition_binding_supply(
    comp: TrainableComposition, registry: DeclarationRegistry, comp_ref: str,
    violations: list[ContractViolation],
) -> None:
    """Composition-level service-binding identity supply matching (divergence A, shape-i — the
    mirror of the pipeline's ``_check_binding_supply`` service-binding arm). Every service-typed
    binding the composition's own nodes declare — the terminal trainable backend AND any
    service-kind preprocessor's declared ``service_bindings`` — MUST have a matching
    ``[service_bindings.<name>]`` identity supply in the composition; the supplied identity
    covers and is correctly placed against the bound service-type's ``identity_schema``; types
    agree; and no supply is an orphan. Transport stays deployment-supplied (not checked here).
    """
    supplies = {s.name: s for s in comp.service_bindings}
    declared_binding_names: set[str] = set()
    backend_binding_names = {sb.name for sb in comp.trainable.service_bindings}

    # Every service-typed binding the composition declares: the trainable backend + each
    # preprocessor's service-typed bindings, resolved from the REFERENCED handler declaration
    # (the name-reference model — the binding declarations live on the handler, not inlined on
    # the entry). A transform handler declares none (getattr default ()); an unresolved name is
    # reported by the flatten arm (HANDLER_NAME_RESOLUTION), so skip it silently here.
    declared_bindings = list(comp.trainable.service_bindings)
    for preproc in comp.preprocessors:
        pdecl = registry.get_handler(preproc.name)
        if pdecl is not None:
            declared_bindings.extend(getattr(pdecl, "service_bindings", ()))

    for sb in declared_bindings:
        declared_binding_names.add(sb.name)
        supply = supplies.get(sb.name)
        if supply is None:
            violations.append(ContractViolation(
                check=Check.BINDING_SUPPLY, rule_id="R-handler-006",
                expected=f"composition '{comp.meta.name}' supplies [service_bindings.{sb.name}] for its '{sb.name}' binding",
                actual=f"no service_bindings.{sb.name} block", composition_ref=comp_ref,
                section_path=f"service_bindings.{sb.name}",
                remediation_hint="a composition supplies its own service-binding identity (self-contained, mirroring the pipeline)",
            ))
            continue  # no supply → its type / identity / config checks have no subject
        if supply.type != sb.type:
            violations.append(ContractViolation(
                check=Check.BINDING_SUPPLY, rule_id="R-handler-006",
                expected=f"service_bindings.{sb.name}.type equals the declared type '{sb.type}'",
                actual=f"supplied type '{supply.type}'", composition_ref=comp_ref,
                section_path=f"service_bindings.{sb.name}",
            ))
            continue  # type disagreement → resolving against the declared type is moot
        st = registry.get_service_type(sb.type)
        if st is None:
            violations.append(ContractViolation(
                check=Check.SERVICE_TYPE_RESOLUTION, rule_id="R-pipeline-001",
                expected=f"service-type '{sb.type}' resolves in the registry",
                actual="no such service-type declaration", composition_ref=comp_ref,
                section_path=f"service_bindings.{sb.name}",
            ))
            continue  # unresolved service-type → no identity_schema / config_schema to check
        _check_identity_coverage_and_placement(supply, st, comp_ref, violations)
        if sb.name in backend_binding_names:
            # The trainable kind's config supply site is [trainable.config] (checked in
            # _check_trainable_config); a `config` block on the backend's supply entry
            # would be a second, undefined supply surface — rejected loud, never merged
            # or silently ignored (a config block belongs to bindings OUTSIDE the
            # trainable kind; service-type/reference.md § The [config_schema] contract).
            if supply.config:
                violations.append(ContractViolation(
                    check=Check.CONFIG_SCHEMA_SUPPLY, rule_id="R-service-type-002",
                    expected=f"the trainable backend binding '{sb.name}' supplies its "
                             "[config_schema] values in [trainable.config] — its supply "
                             "entry carries no config block",
                    actual=f"a config block on service_bindings.{sb.name} "
                           f"({sorted(supply.config)})",
                    composition_ref=comp_ref,
                    section_path=f"service_bindings.{sb.name}.config",
                    remediation_hint="move the values into [trainable.config]",
                ))
        else:
            # A preprocessor-declared binding's config supply site IS its supply entry's
            # config block (the same supply rule as the pipeline site — the
            # mirror-pipeline principle; both directions checked). Within-group
            # aggregation: the collecting scan reports EVERY fault in the block
            # (COMPILE-4); effective_config stays the raising twin for assembly/hasher.
            violations.extend(config_supply_violations(
                supply.config, st, composition_ref=comp_ref,
                section_path=f"service_bindings.{sb.name}.config",
            ))

    # Orphan supplies — a composition service_bindings.<name> no node declares.
    for name in supplies:
        if name not in declared_binding_names:
            violations.append(ContractViolation(
                check=Check.BINDING_SUPPLY, rule_id="R-handler-006",
                expected="every composition service_bindings.<name> supply matches a binding a node declares",
                actual=f"orphan supply 'service_bindings.{name}' — no node declares it",
                composition_ref=comp_ref, section_path=f"service_bindings.{name}",
            ))

    # Each preprocessor's schema/compile binding supply — the mirror of the pipeline's
    # handler-node binding-supply arm, through the SAME shared author (one supply rule across
    # both layers). Resolve the referenced handler; an unresolved name is reported by the
    # flatten arm (HANDLER_NAME_RESOLUTION), so skip it silently here.
    for preproc in comp.preprocessors:
        pdecl = registry.get_handler(preproc.name)
        if pdecl is None:
            continue
        _check_schema_binding_supply(
            getattr(pdecl, "bindings", ()), preproc.bindings,
            f"{comp.meta.name}.{preproc.id}", comp_ref, violations,
        )


# ---------------------------------------------------------------------------
# Channel collection + the topology checks
# ---------------------------------------------------------------------------


def _collect_channels(flat_nodes: list[_FlatNode], pipeline: PipelineDeclaration) -> _Channels:
    channels = _Channels()
    for fn in flat_nodes:
        for port in fn.input_ports:
            channel = fn.read_map[port.name]
            channels.wire(channel, _WiringPort(
                port_name=port.name, node_qualified_name=fn.qualified_name, node_position=fn.position,
                direction="read", type=port.type, source=fn.read_sources.get(port.name, "identity"),
            ))
        for port in fn.output_ports:
            channel = fn.write_map[port.name]
            channels.wire(channel, _WiringPort(
                port_name=port.name, node_qualified_name=fn.qualified_name, node_position=fn.position,
                direction="write", type=port.type, source="mapped",
            ))
    # Pipeline [inputs] are free variables — they close downstream readers and seed the
    # contributor model. The boundary TYPE expectations (the pipeline's and every composition's)
    # are registered through the shared _register_boundary_expectations, so type-participation has
    # one mechanism per layer; a composition input is NOT a free var (it is a requirement on the
    # outer context, enforced by _check_input_closure), so only the pipeline seeds here.
    for f in pipeline.inputs:
        channels.free_vars.add(f.name)
    return channels


def _type_match(
    channels: _Channels, pipeline_name: str, violations: list[ContractViolation]
) -> tuple[dict[str, ChannelFieldType], set[str]]:
    """Read/write shape matching (R-pipeline-001): every port (read or write) + every type
    expectation wired to one channel MUST declare the exact-same type. Returns the agreed
    type per channel (the first contribution wins as the channel's resolved type, so the map
    is fully populated even where a channel disagrees) AND the set of channels that disagreed.

    Collects **one** violation per disagreeing channel (the canonical "three channel-type
    mismatches → all three reported" aggregation), then continues — the channel's resolved
    type stays the first contribution so downstream Group-B checks still run. The
    disagreeing-channel set lets the merge-strategy-type check skip a channel whose type is
    itself in dispute (that mismatch is not independently detectable)."""
    resolved: dict[str, ChannelFieldType] = {}
    mismatched: set[str] = set()
    all_channels = set(channels.wiring) | set(channels.expectations)
    for channel in all_channels:
        contributions: list[tuple[ChannelFieldType, str]] = [
            (wp.type, f"{wp.direction} port '{wp.port_name}' of {wp.node_qualified_name}")
            for wp in channels.wiring.get(channel, [])
        ]
        contributions += channels.expectations.get(channel, [])
        first_type, first_label = contributions[0]
        for type_, label in contributions[1:]:
            if type_ != first_type:
                violations.append(ContractViolation(
                    check=Check.READ_WRITE_SHAPE, rule_id="R-pipeline-001",
                    expected=f"all ports wired to channel '{channel}' declare one type (exact equality)",
                    actual=f"{first_label} declares {canonical_token(first_type)} but {label} declares {canonical_token(type_)}",
                    composition_ref=pipeline_name, section_path=f"channel.{channel}",
                    remediation_hint="align the wiring or the declared types; the engine does no subtype widening",
                ))
                mismatched.add(channel)
                break  # one disagreement report per channel
        resolved[channel] = first_type
    return resolved, mismatched


def _check_single_assignment(flat_nodes: list[_FlatNode], pipeline_name: str, violations: list[ContractViolation]) -> None:
    """No node wires a read-port and an output-port to the same channel (R-pipeline-001
    single-assignment / read-write disjointness). One violation per offending node,
    collected (every independently-detectable single-assignment fault)."""
    for fn in flat_nodes:
        overlap = set(fn.read_map.values()) & set(fn.write_map.values())
        if overlap:
            violations.append(ContractViolation(
                check=Check.SINGLE_ASSIGNMENT, rule_id="R-pipeline-001",
                expected=f"node '{fn.qualified_name}' wires reads and writes to disjoint channels",
                actual=f"channel(s) {sorted(overlap)} are both read and written by one node",
                composition_ref=pipeline_name,
                remediation_hint="to transform a value, write a NEW channel — a channel is single-assignment",
            ))


def _check_write_overlap(
    channels: _Channels, channel_types: dict[str, ChannelFieldType],
    merges: dict[str, MergeStrategy], pipeline_name: str,
    violations: list[ContractViolation], type_mismatch_channels: set[str],
) -> None:
    """Channel-write disjointness with merge opt-in, in **contributor** terms
    (R-pipeline-002): a channel's contributors are its seed (iff a declared ``[inputs]``
    channel) plus its node writes, in graph order — the single
    :func:`~conjured.ir.graph.channel_contributors` derivation, shared with the runtime
    fold. Two or more contributors require a ``merge.<channel>`` strategy whose type
    constraint matches the channel; absence collects the same undeclared-fan-in
    ContractViolation whether the second contributor is a node write or the seed. All
    independently-detectable overlap / merge-entry faults are collected."""
    contributor_counts: dict[str, int] = {}
    for channel, ports in channels.wiring.items():
        contributors = channel_contributors(
            seeded=channel in channels.free_vars,
            write_positions=(wp.node_position for wp in ports if wp.direction == "write"),
        )
        contributor_counts[channel] = len(contributors)
        if len(contributors) >= 2 and channel not in merges:
            kinds = [c.kind for c in contributors]
            detail = (
                f"{kinds.count('write')} node write(s)"
                + (" plus the channel's seed (a declared [inputs] channel)" if "seed" in kinds else "")
            )
            violations.append(ContractViolation(
                check=Check.CHANNEL_WRITE_OVERLAP, rule_id="R-pipeline-002",
                expected=f"channel '{channel}' with {len(contributors)} contributors declares a merge.<channel> strategy",
                actual=f"no merge declaration for a multi-contributor channel ({detail})",
                composition_ref=pipeline_name,
                section_path=f"channel.{channel}",
                remediation_hint="add [merge] <channel> = \"<strategy>\", or route one contributor to a distinct channel",
            ))

    # Per-entry merge validation — every declared merge.<channel> entry is checked
    # **unconditionally** (canon states the checks per entry, not gated behind the
    # >=2-contributor count): the named channel MUST exist in the graph (a merge on an
    # unwired/non-existent channel is inert — nothing to fold), and the strategy's
    # type-constraint + non-optional-base rule MUST hold. A single-contributor wired
    # channel is NOT inert — it folds degenerate (the reader-side _first_fold), valid input.
    # Registry membership is already checked at parse (_parse_merge).
    for channel, strategy in merges.items():
        if channel not in contributor_counts:
            violations.append(ContractViolation(
                check=Check.CHANNEL_WRITE_OVERLAP, rule_id="R-pipeline-002",
                expected=f"merge.{channel} names a channel the graph wires",
                actual=f"no channel '{channel}' in the graph — a merge on an unwired channel is inert",
                composition_ref=pipeline_name, section_path=f"merge.{channel}",
                remediation_hint="remove the inert merge entry, or wire the channel it names",
            ))
            continue  # no resolved type for an unwired channel — skip the strategy-type check
        if channel in type_mismatch_channels:
            continue  # the channel's type is itself in dispute — its strategy fit isn't independently detectable
        if not _strategy_accepts(strategy, channel_types[channel]):
            violations.append(ContractViolation(
                check=Check.MERGE_STRATEGY_TYPE, rule_id="R-pipeline-002",
                expected=f"merge strategy '{strategy.value}' matches the type of channel '{channel}'",
                actual=f"strategy '{strategy.value}' rejects {canonical_token(channel_types[channel])}",
                composition_ref=pipeline_name, section_path=f"merge.{channel}",
            ))


def _check_input_closure(channels: _Channels, pipeline_name: str, violations: list[ContractViolation]) -> None:
    """Every read-port's channel must be closed: written by a strictly-upstream node, or a
    pipeline ``[inputs]`` free variable (pipeline/conformance.md §§ Dangling identity port /
    Read-port channel not closed). Every independently-detectable unclosed read-port is
    collected (e.g. two dangling identity ports → both reported)."""
    for channel, ports in channels.wiring.items():
        writers = [wp for wp in ports if wp.direction == "write"]
        for reader in (wp for wp in ports if wp.direction == "read"):
            if channel in channels.free_vars:
                continue
            if any(w.node_position < reader.node_position for w in writers):
                continue
            if reader.source == "identity":
                violations.append(ContractViolation(
                    check=Check.DANGLING_IDENTITY_PORT, rule_id="R-pipeline-001",
                    expected=f"unmapped port '{reader.port_name}' of {reader.node_qualified_name} desugars to a channel in scope",
                    actual=f"channel '{channel}' is neither written upstream nor a pipeline [inputs] field",
                    composition_ref=pipeline_name, section_path=f"channel.{channel}",
                    remediation_hint="map the port explicitly, produce the channel upstream, or declare it in [inputs]",
                ))
                continue
            violations.append(ContractViolation(
                check=Check.READ_PORT_UNCLOSED, rule_id="R-pipeline-001",
                expected=f"read-port channel '{channel}' is produced by an upstream write or declared in [inputs]",
                actual=f"no upstream writer and not a pipeline [inputs] field (read by {reader.node_qualified_name})",
                composition_ref=pipeline_name, section_path=f"channel.{channel}",
                remediation_hint="omitting [inputs] never closes a read-port; declare it there or write it upstream",
            ))


def _register_boundary_expectations(channels: _Channels, boundary: _Boundary) -> None:
    """Register a unit's ``[inputs]`` / ``[outputs]`` declared types as type-match expectations on
    their boundary channels, so the boundary participates in channel type-agreement exactly as the
    ports wired to it do (R-pipeline-001 read/write shape-matching, "the embedded declaration's
    inputs / outputs participate after flatten"). One mechanism for the pipeline boundary and every
    composition boundary — the mirror-pipeline principle. An expectation is type-only: it is not a
    reader, writer, or seed, so it touches type agreement and nothing else (closure / contributor
    counting are unaffected)."""
    for f in boundary.inputs:
        channels.expect(f.name, f.type, boundary.in_label)
    for f in boundary.outputs:
        channels.expect(f.name, f.type, boundary.out_label)


def _check_boundary_dead(channels: _Channels, boundary: _Boundary, violations: list[ContractViolation]) -> None:
    """A declared boundary input no in-scope node reads, or boundary output no in-scope node
    writes, is a dead declaration (R-pipeline-001 inputs/outputs resolution;
    pipeline/conformance.md § inputs/outputs dead declaration). One mechanism for the top-level
    pipeline (scope = the whole graph) and every embedded composition (scope = the composition's
    own flattened nodes) — the mirror-pipeline principle. Scoping to the unit's own nodes is
    load-bearing for a composition: its boundary channels are unscoped (shared with the outer
    graph), so an outer reader/writer must NOT mask a dead composition boundary field. Every
    dead boundary field is collected."""
    read_targets, write_targets = _scope_targets(channels, boundary.node_positions)
    for f in boundary.inputs:
        if f.name not in read_targets:
            violations.append(ContractViolation(
                check=Check.INPUTS_OUTPUTS_DEAD, rule_id="R-pipeline-001",
                expected=f"every {boundary.in_label} field is read by at least one node ('{f.name}')",
                actual=f"no node reads channel '{f.name}'", composition_ref=boundary.ref,
                section_path=f"inputs.{f.name}", remediation_hint="remove the dead input, or add a node that reads it",
            ))
    for f in boundary.outputs:
        if f.name not in write_targets:
            violations.append(ContractViolation(
                check=Check.INPUTS_OUTPUTS_DEAD, rule_id="R-pipeline-001",
                expected=f"every {boundary.out_label} field is written by at least one node ('{f.name}')",
                actual=f"no node writes channel '{f.name}'", composition_ref=boundary.ref,
                section_path=f"outputs.{f.name}", remediation_hint="remove the dead output, or add a node that writes it",
            ))


def _scope_targets(
    channels: _Channels, positions: set[int] | None
) -> tuple[set[str], set[str]]:
    """The channels read / written by the nodes in scope: every node when ``positions`` is None
    (the top-level pipeline), else only the flattened nodes at those positions (one composition's
    own preprocessors + terminal trainable)."""
    read_targets: set[str] = set()
    write_targets: set[str] = set()
    for channel, ports in channels.wiring.items():
        for p in ports:
            if positions is not None and p.node_position not in positions:
                continue
            if p.direction == "read":
                read_targets.add(channel)
            else:
                write_targets.add(channel)
    return read_targets, write_targets


# ---------------------------------------------------------------------------
# Binding supply + identity placement
# ---------------------------------------------------------------------------


def _check_binding_supply(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry, flat_nodes: list[_FlatNode],
    pipeline_name: str, violations: list[ContractViolation],
) -> None:
    """Binding supply matching (R-pipeline-001): service-typed bindings supplied + identity
    covers the service-type's identity_schema; compose-time schema bindings supplied; no
    orphan supplies/keys; identity-field placement against identity_schema. Every
    independently-detectable supply fault is collected; a missing / wrong-typed / unresolved
    supply skips its own dependent identity/config checks (no subject to check)."""
    supplies = {s.name: s for s in pipeline.service_bindings}
    declared_binding_names: set[str] = set()

    for node in pipeline.nodes:
        if not isinstance(node, HandlerNode):
            continue
        comp_ref = pipeline_name
        decl = registry.get_handler(node.name)  # resolved in _flatten; re-fetch for its bindings
        if decl is None:
            continue

        # Service-typed bindings declared by the handler.
        for sb in getattr(decl, "service_bindings", ()):  # transform has none
            declared_binding_names.add(sb.name)
            supply = supplies.get(sb.name)
            if supply is None:
                violations.append(ContractViolation(
                    check=Check.BINDING_SUPPLY, rule_id="R-pipeline-001",
                    expected=f"pipeline supplies [service_bindings.{sb.name}] for handler '{node.name}'",
                    actual=f"no service_bindings.{sb.name} block", composition_ref=comp_ref,
                    section_path=f"service_bindings.{sb.name}",
                ))
                continue  # no supply → its type / identity / config checks have no subject
            if supply.type != sb.type:
                violations.append(ContractViolation(
                    check=Check.BINDING_SUPPLY, rule_id="R-pipeline-001",
                    expected=f"service_bindings.{sb.name}.type equals the handler's declared type '{sb.type}'",
                    actual=f"supplied type '{supply.type}'", composition_ref=comp_ref,
                    section_path=f"service_bindings.{sb.name}",
                ))
                continue  # type disagreement → resolving against the declared type is moot
            st = registry.get_service_type(sb.type)
            if st is None:
                violations.append(ContractViolation(
                    check=Check.SERVICE_TYPE_RESOLUTION, rule_id="R-pipeline-001",
                    expected=f"service-type '{sb.type}' resolves in the registry",
                    actual="no such service-type declaration", composition_ref=comp_ref,
                    section_path=f"service_bindings.{sb.name}",
                ))
                continue  # unresolved service-type → no identity_schema / config_schema to check
            _check_identity_coverage_and_placement(supply, st, comp_ref, violations)
            # The entry's `config` block supplies the bound service-type's [config_schema]
            # values — both directions checked at compose (the supply rule is identical at
            # every config supply site; service-type/reference.md § The [config_schema]
            # contract). Within-group aggregation: the collecting scan reports EVERY
            # fault in the block (COMPILE-4); effective_config stays the raising twin
            # for assembly/hasher (which recompute the effective values at delivery).
            violations.extend(config_supply_violations(
                supply.config, st, composition_ref=comp_ref,
                section_path=f"service_bindings.{sb.name}.config",
            ))

        # Compose-time schema/compile binding-supply matching — the SAME author for the outer
        # handler-node loop and the composition's preprocessor loop (the mirror-pipeline
        # principle — one author, reused, never a parallel copy).
        _check_schema_binding_supply(
            getattr(decl, "bindings", ()), node.bindings, node.name, comp_ref, violations,
        )

    # Orphan supplies — a service_bindings.<name> no node declares.
    for name in supplies:
        if name not in declared_binding_names:
            violations.append(ContractViolation(
                check=Check.BINDING_SUPPLY, rule_id="R-pipeline-001",
                expected="every service_bindings.<name> supply matches a binding a node declares",
                actual=f"orphan supply 'service_bindings.{name}' — no node declares it",
                composition_ref=pipeline_name, section_path=f"service_bindings.{name}",
            ))


def _check_identity_coverage_and_placement(
    supply: ServiceBindingSupply, st: ServiceTypeDeclaration, comp_ref: str,
    violations: list[ContractViolation],
) -> None:
    declared_identity = {f.name for f in st.identity_schema}
    for field_name, field_value in supply.identity.items():
        # Identity fields admit no nullable declaration, so a spelled explicit null at an
        # identity position is recognized-and-rejected — never absorbed raw as data flowing
        # to the adapter (handler/reference.md explicit-null region; pipeline/reference.md
        # § service_bindings.<name>).
        try:
            if is_explicit_null(
                field_value, owner=f"service_bindings.{supply.name}.{field_name}",
                section_path=f"service_bindings.{supply.name}.{field_name}",
                composition_ref=comp_ref,
            ):
                violations.append(ContractViolation(
                    check=Check.EXPLICIT_NULL_TARGET, rule_id="R-pipeline-001",
                    expected="{ null = true } targets a nullable-declared field",
                    actual=f"identity field '{field_name}' — identity fields admit no "
                           "nullable declaration",
                    composition_ref=comp_ref,
                    section_path=f"service_bindings.{supply.name}.{field_name}",
                ))
        except ContractViolation as cv:
            violations.append(cv)
    for field_name in supply.identity:
        if field_name not in declared_identity:
            violations.append(ContractViolation(
                check=Check.IDENTITY_TRANSPORT_PLACEMENT, rule_id="R-pipeline-001",
                expected=f"every field in service_bindings.{supply.name} is a declared identity_schema field of '{st.name}' ({sorted(declared_identity)})",
                actual=f"field '{field_name}' is not in identity_schema",
                composition_ref=comp_ref, section_path=f"service_bindings.{supply.name}.{field_name}",
                remediation_hint="a transport value (endpoint/credential/timeout) belongs in the deployment's transport.<name> block",
            ))
    for field_name in declared_identity:
        if field_name not in supply.identity:
            violations.append(ContractViolation(
                check=Check.BINDING_SUPPLY, rule_id="R-pipeline-001",
                expected=f"service_bindings.{supply.name} supplies every identity_schema field of '{st.name}'",
                actual=f"identity field '{field_name}' not supplied",
                composition_ref=comp_ref, section_path=f"service_bindings.{supply.name}",
            ))


# ---------------------------------------------------------------------------
# Deployment coverage
# ---------------------------------------------------------------------------


def _check_deployment_coverage(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry, flat_nodes: list[_FlatNode],
    deployment: DeploymentDeclaration, pipeline_name: str,
    violations: list[ContractViolation],
) -> None:
    """Transport + hook-transport coverage (R-pipeline-001) and override-target validity
    (R-deployment-002), with deterministic override-over-shared resolution by pipeline name.
    Every independently-detectable coverage / override / placement fault is collected; a
    missing transport block skips its own field-level checks (no block to inspect)."""
    override = next((o for o in deployment.pipelines if o.pipeline_qualified_name == pipeline_name), None)

    def resolve_transport(name: str):
        if override is not None:
            for blk in override.transport:
                if blk.name == name:
                    return blk
        for blk in deployment.transport:
            if blk.name == name:
                return blk
        return None

    def resolve_hook_transport(qn: str):
        if override is not None:
            for blk in override.hook_transport:
                if blk.hook_qualified_name == qn:
                    return blk
        for blk in deployment.hook_transport:
            if blk.hook_qualified_name == qn:
                return blk
        return None

    # The full hook set covered by hook_transport: outer-pipeline hook nodes (by qualified
    # name) AND composition-internal hook preprocessors (addressed `<composition>.<hook>`,
    # divergence C). A hook preprocessor is a name-reference to a hook handler; its hook kind
    # and its transport_schema are resolved from that referenced declaration (the
    # mirror-pipeline principle), exactly as an outer hook node's are.
    hooks: list[tuple[str, tuple]] = []  # (hook_qualified_name, declared transport_schema fields)
    for n in pipeline.nodes:
        if isinstance(n, HandlerNode):
            decl = registry.get_handler(n.name)
            if isinstance(decl, HookDeclaration):
                hooks.append((n.name, decl.transport_schema))
        elif isinstance(n, CompositionNode):
            comp = registry.get_composition(n.name)
            # A nested `pipeline` composition's hooks are covered by ITS OWN recursive
            # compile's deployment-coverage group, resolved under the inner pipeline's
            # meta.name (the family rule's one identity model) — never re-walked here
            # (the inner scope is opaque to the outer coverage pass).
            if isinstance(comp, TrainableComposition):
                for pp in comp.preprocessors:
                    pdecl = registry.get_handler(pp.name)
                    if isinstance(pdecl, HookDeclaration):  # hook preprocessor (resolved decl)
                        hooks.append((f"{comp.meta.name}.{pp.id}", pdecl.transport_schema))
    pipeline_hook_qns = {qn for qn, _ in hooks}

    # Every service-typed binding the engine composes, grouped by its as-written handle:
    # the pipeline's own service_bindings AND each embedded trainable composition's
    # [service_bindings.<name>] supplies (terminal backend + preprocessor bindings) — the
    # 31#2 coverage extension (R-pipeline-001/transport-coverage). A nested `pipeline`
    # composition's bindings are covered by ITS OWN recursive compile's coverage group
    # (the same opacity rule the hook walk below applies).
    # guarantees: transport-coverage-composition-bindings
    composed_supplies: list[tuple[str, ServiceBindingSupply]] = [
        (pipeline_name, s) for s in pipeline.service_bindings
    ]
    for n in pipeline.nodes:
        if isinstance(n, CompositionNode):
            comp = registry.get_composition(n.name)
            if isinstance(comp, TrainableComposition):
                composed_supplies.extend((comp.meta.name, s) for s in comp.service_bindings)
    by_handle: dict[str, list[tuple[str, ServiceBindingSupply]]] = {}
    for scope, s in composed_supplies:
        by_handle.setdefault(s.name, []).append((scope, s))

    # Override-target validity (R-deployment-002): an override naming a binding/hook
    # outside the named pipeline's composed scope (composition-supplied handles included).
    pipeline_binding_names = set(by_handle)
    if override is not None:
        for blk in override.transport:
            if blk.name not in pipeline_binding_names:
                violations.append(ContractViolation(
                    check=Check.DEPLOYMENT_OVERRIDE_TARGET, rule_id="R-deployment-002",
                    expected=f"override pipelines.{pipeline_name}.transport.{blk.name} names a binding handle within the pipeline's composed scope",
                    actual=f"binding '{blk.name}' not declared within pipeline '{pipeline_name}' (pipeline- or composition-supplied)",
                    composition_ref=pipeline_name, section_path=f"pipelines.{pipeline_name}.transport.{blk.name}",
                ))
        for hook_blk in override.hook_transport:
            if hook_blk.hook_qualified_name not in pipeline_hook_qns:
                violations.append(ContractViolation(
                    check=Check.DEPLOYMENT_OVERRIDE_TARGET, rule_id="R-deployment-002",
                    expected=f"override hook_transport.\"{hook_blk.hook_qualified_name}\" names a hook the pipeline declares",
                    actual=f"hook '{hook_blk.hook_qualified_name}' not in pipeline '{pipeline_name}'",
                    composition_ref=pipeline_name,
                ))

    # Service-binding transport coverage + transport-field placement + explicit-null +
    # secret-reference admission, per as-written handle over every engine-composed binding.
    # The join is type-coherent first: one handle, one service-type — one covering block
    # cannot satisfy two transport_schemas (R-pipeline-001/transport-coverage).
    for handle in sorted(by_handle):
        entries = by_handle[handle]
        types = sorted({s.type for _, s in entries})
        # guarantees: transport-handle-type-coherence
        if len(types) > 1:
            violations.append(ContractViolation(
                check=Check.TRANSPORT_HANDLE_COHERENCE, rule_id="R-pipeline-001",
                expected=f"every binding sharing handle '{handle}' within pipeline "
                         f"'{pipeline_name}' resolves one service-type",
                actual=f"handle '{handle}' binds service-types {types} across scopes "
                       f"{sorted({scope for scope, _ in entries})}",
                remediation_hint="rename one handle, or align the bindings on one "
                                 "service-type — the shared transport.<name> block is "
                                 "key-checked against exactly one transport_schema",
                composition_ref=pipeline_name, section_path=f"transport.{handle}",
            ))
            continue  # incoherent handle → no single schema to check coverage against
        supply = entries[0][1]
        st = registry.get_service_type(supply.type)
        blk = resolve_transport(handle)
        if blk is None:
            violations.append(ContractViolation(
                check=Check.TRANSPORT_COVERAGE, rule_id="R-pipeline-001",
                expected=f"deployment supplies transport.{handle} for service binding '{handle}'",
                actual="no covering transport block", composition_ref=pipeline_name,
                section_path=f"transport.{handle}",
            ))
            continue  # no transport block → its field placement / coverage have nothing to inspect
        if st is not None:
            declared = {f.name: f for f in st.transport_schema}
            for field_name in blk.values:
                if field_name not in declared:
                    violations.append(ContractViolation(
                        check=Check.IDENTITY_TRANSPORT_PLACEMENT, rule_id="R-pipeline-001",
                        expected=f"every transport.{handle} field is a declared transport_schema field of '{st.name}'",
                        actual=f"field '{field_name}' is not in transport_schema",
                        composition_ref=pipeline_name, section_path=f"transport.{handle}.{field_name}",
                    ))
            # guarantees: uniform-presence-no-nullable-exemption
            for field_name, decl_field in declared.items():
                if field_name not in blk.values:
                    violations.append(ContractViolation(
                        check=Check.TRANSPORT_COVERAGE, rule_id="R-pipeline-001",
                        expected=f"transport.{handle} supplies every transport_schema field of '{st.name}' "
                                 "(a nullable field as a value or the explicit { null = true }; "
                                 "omission is never a null)",
                        actual=f"transport field '{field_name}' missing", composition_ref=pipeline_name,
                        section_path=f"transport.{handle}",
                    ))
                    continue
                # Explicit-null admission (the one value shape the engine reads in an
                # otherwise-opaque transport block): spelled null on a nullable-declared
                # field is the considered-and-null supply; on a non-nullable field it
                # rejects (handler/reference.md explicit-null region).
                try:
                    spelled = is_explicit_null(
                        blk.values[field_name], owner=f"transport.{handle}.{field_name}",
                        section_path=f"transport.{handle}.{field_name}",
                        composition_ref=pipeline_name,
                    )
                except ContractViolation as cv:
                    violations.append(cv)
                    continue
                if spelled and not isinstance(decl_field.type, OptionalType):
                    violations.append(ContractViolation(
                        check=Check.EXPLICIT_NULL_TARGET, rule_id="R-pipeline-001",
                        expected=f"{{ null = true }} for transport.{handle}.{field_name} targets a "
                                 "nullable-declared transport_schema field",
                        actual=f"field '{field_name}' of '{st.name}' is not nullable-declared",
                        composition_ref=pipeline_name,
                        section_path=f"transport.{handle}.{field_name}",
                    ))
                # Secret-reference shape — the SECOND engine-read value shape in the
                # otherwise-opaque service-transport block (deployment/reference.md
                # § Secret references, R-deployment-003): a secret_ref-declared field's
                # supplied value must be a well-formed [scheme]payload reference with a
                # known scheme, a dotted (consumer) scheme importing to a callable.
                # Shape-early: a malformed reference (a pasted raw credential included)
                # never reaches a dispatch. The spelled null admitted above is the
                # no-credential state and carries no reference to check.
                if not spelled and _is_secret_ref_field(decl_field.type):
                    violations.extend(_secret_ref_shape_violations(
                        blk.values[field_name],
                        owner=f"transport.{handle}.{field_name}",
                        composition_ref=pipeline_name,
                    ))

    # Hook transport coverage — outer hook nodes AND flattened composition hook preprocessors
    # (addressed `<composition>.<hook>`, divergence C).
    for hook_qn, transport_schema in hooks:
        blk = resolve_hook_transport(hook_qn)
        if blk is None:
            violations.append(ContractViolation(
                check=Check.HOOK_TRANSPORT_COVERAGE, rule_id="R-pipeline-001",
                expected=f'deployment supplies hook_transport."{hook_qn}" for the hook',
                actual="no covering hook_transport block (absent — even empty-but-present satisfies a zero-field schema)",
                remediation_hint=(
                    f'every hook node needs a hook_transport."{hook_qn}" block, even an empty-but-present one; '
                    "a backend-SDK-emission hook STILL needs it though its backend transport rides "
                    "transport.<binding> to the adapter — the block declares coverage, not transport content"
                ),
                composition_ref=pipeline_name, section_path=f'hook_transport."{hook_qn}"',
            ))
            continue  # no hook_transport block → its field placement / coverage have nothing to inspect
        declared_names = {f.name for f in transport_schema}
        # No-unknown-fields direction (mirrors the sibling binding-transport arm; the
        # canon-mandated strict validation — R-pipeline-001/hook-transport-coverage: "no
        # unknown fields are accepted"). A field the schema does not declare is a loud CV.
        for field_name in blk.values:
            if field_name not in declared_names:
                violations.append(ContractViolation(
                    check=Check.HOOK_TRANSPORT_COVERAGE, rule_id="R-pipeline-001",
                    expected=f'every hook_transport."{hook_qn}" field is a declared transport_schema field',
                    actual=f"unknown hook transport field '{field_name}' (no such transport_schema field)",
                    composition_ref=pipeline_name, section_path=f'hook_transport."{hook_qn}".{field_name}',
                ))
        for field_name in declared_names:
            if field_name not in blk.values:
                violations.append(ContractViolation(
                    check=Check.HOOK_TRANSPORT_COVERAGE, rule_id="R-pipeline-001",
                    expected=f'hook_transport."{hook_qn}" supplies every transport_schema field',
                    actual=f"hook transport field '{field_name}' missing", composition_ref=pipeline_name,
                    section_path=f'hook_transport."{hook_qn}"',
                ))
        # Type-match direction — the third arm of the strict validation R-pipeline-001/
        # hook-transport-coverage mandates ("declared types must match"). Each supplied value for
        # a declared, present field is validated against that field's declared channel-field type
        # via the same model generator the dispatch boundary uses, so a deployment typo
        # (`format = "jsonn"` against a `Literal['plain','json']` field) is a compose-time CV here
        # rather than a value that composes green and silently changes behavior at every dispatch.
        # Missing / unknown fields are already flagged above; a hook transport_schema field carries
        # no validators (parse.py D5), so build_model here does pure type realization.
        for decl in transport_schema:
            if decl.name not in blk.values:
                continue  # missing — the coverage arm above already flags it
            # Explicit-null admission first (hook transport fields are engine-read, so the
            # reserved form must resolve BEFORE the type-match — a spelled null on a
            # nullable field delivers as Python None, which its optional type admits by
            # construction; on a non-nullable field it rejects here, never type-matches
            # as a dict).
            try:
                spelled = is_explicit_null(
                    blk.values[decl.name], owner=f'hook_transport."{hook_qn}".{decl.name}',
                    section_path=f'hook_transport."{hook_qn}".{decl.name}',
                    composition_ref=pipeline_name,
                )
            except ContractViolation as cv:
                violations.append(cv)
                continue
            if spelled:
                if not isinstance(decl.type, OptionalType):
                    violations.append(ContractViolation(
                        check=Check.EXPLICIT_NULL_TARGET, rule_id="R-pipeline-001",
                        expected=f'{{ null = true }} for hook_transport."{hook_qn}".{decl.name} '
                                 "targets a nullable-declared transport_schema field",
                        actual=f"field '{decl.name}' is not nullable-declared",
                        composition_ref=pipeline_name,
                        section_path=f'hook_transport."{hook_qn}".{decl.name}',
                    ))
                continue  # admitted null needs no type-match (None ∈ the optional type)
            if _is_secret_ref_field(decl.type):
                # The reference grammar IS the type-match for a secret_ref field — the
                # same shape check the service arm runs (deployment/reference.md § Secret
                # references); a SecretRefType never reaches the model generator.
                violations.extend(_secret_ref_shape_violations(
                    blk.values[decl.name],
                    owner=f'hook_transport."{hook_qn}".{decl.name}',
                    composition_ref=pipeline_name,
                ))
                continue
            field_model = build_model(f"hook_transport__{decl.name}", (decl,))
            try:
                field_model.model_validate({decl.name: blk.values[decl.name]})
            except ValidationError as exc:
                detail = exc.errors()[0]["msg"] if exc.errors() else str(exc)
                violations.append(ContractViolation(
                    check=Check.HOOK_TRANSPORT_COVERAGE, rule_id="R-pipeline-001",
                    expected=(
                        f'hook_transport."{hook_qn}".{decl.name} value matches its declared '
                        f"transport_schema type {canonical_token(decl.type)}"
                    ),
                    actual=f"value {blk.values[decl.name]!r} does not: {detail}",
                    composition_ref=pipeline_name,
                    section_path=f'hook_transport."{hook_qn}".{decl.name}',
                ))


def _is_secret_ref_field(field_type: ChannelFieldType) -> bool:
    """Whether a transport field is secret_ref-declared — bare or the nullable
    ``secret_ref | None`` union (the only positions the parser admits the token in)."""
    if isinstance(field_type, OptionalType):
        field_type = field_type.inner
    return isinstance(field_type, SecretRefType)


def _secret_ref_shape_violations(
    value: object, *, owner: str, composition_ref: str
) -> list[ContractViolation]:
    """The compose-time shape check for one secret_ref-declared transport value —
    grammar → scheme classification → (dotted) resolver import, each failing as its own
    check with one fix-shape (R-deployment-003; the grammar/classify/import halves are
    single-homed in ``adapters/secret_refs.py``, this wraps them as ContractViolations).
    The FETCH never happens here — store availability is dispatch-time
    (``SecretResolutionError``)."""
    # Deferred import: the adapters package is the I/O-boundary layer; pulling it at
    # module scope from the validator would couple the pure-validation import graph to it.
    from conjured.adapters.secret_refs import (
        BUILTIN_SCHEMES,
        classify_scheme,
        load_consumer_resolver,
        parse_secret_ref,
    )

    try:
        scheme, _payload = parse_secret_ref(value)
    except ValueError as exc:
        # guarantees: secret-ref-malformed
        return [ContractViolation(
            check=Check.SECRET_REF_MALFORMED, rule_id="R-deployment-003",
            expected=f"{owner} supplies a '[scheme]payload' secret reference "
                     "(or {{ null = true }} on a nullable-declared field)",
            actual=str(exc),
            remediation_hint="a raw credential never belongs in a declaration file — "
                             "reference the store instead, e.g. \"[env]LLM_PROD_KEY\" or "
                             "\"[file]/run/secrets/llm\"",
            composition_ref=composition_ref, section_path=owner,
        )]
    kind = classify_scheme(scheme)
    if kind is None:
        # guarantees: secret-ref-scheme-unknown
        return [ContractViolation(
            check=Check.SECRET_REF_SCHEME_UNKNOWN, rule_id="R-deployment-003",
            expected=f"{owner} names a built-in scheme ({', '.join(BUILTIN_SCHEMES)}) or a "
                     "namespaced (dotted) consumer-resolver qualified name",
            actual=f"unknown scheme '{scheme}'",
            composition_ref=composition_ref, section_path=owner,
        )]
    if kind == "consumer":
        try:
            load_consumer_resolver(scheme)
        except (ImportError, AttributeError, TypeError) as exc:
            # guarantees: secret-resolver-invalid
            return [ContractViolation(
                check=Check.SECRET_RESOLVER_INVALID, rule_id="R-deployment-003",
                expected=f"{owner}'s dotted scheme '{scheme}' imports to a callable "
                         "(payload: str) -> str at pipeline-declaration load",
                actual=f"{exc.__class__.__name__}: {exc}",
                composition_ref=composition_ref, section_path=owner,
            )]
    return []
