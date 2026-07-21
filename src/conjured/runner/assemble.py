"""Stage-4 assembly — lifecycle stage 4 into a frozen :class:`Runnable` (Phase 3).

``conjured.runner.assemble(...)`` completes the pipeline load lifecycle
(``conjured/docs/components/pipeline/reference.md`` § Pipeline load lifecycle stage 4
"Engine-side dispatch construction") over a compiled graph: per node, join the graph
node back to its declarations (the stage-4 join keys the compiler stamped —
``entry_ordinal`` / ``callable_ref`` / ``composition_path`` / ``member_name``), run the
Phase-2 resolution seals (``validator.resolve_handler`` steps 2–7 for bare-function
kinds; ``validator.resolve_adapter`` + the trainable-backend certification for the
trainable kind), generate the Pydantic models from the **declaration's** FieldDecls
(validators ride the FieldDecl — ``validator.model_gen``), resolve the compose-time
binding values and the service-binding runtime, and construct the dispatch callable
through the **existing Phase-2 wrappers** (``runner.dispatch.construct`` /
``construct_trainable`` — never rebuilt).

The public name + shape are pinned:
``assemble(graph, registry, deployment=None) -> Runnable`` — the stage-4 inputs canon
enumerates (compiled graph + declaration registry + deployment; ``deployment`` defaults
to ``registry.deployment``). The result is a **frozen runnable record** the kernel walk
(``runner.run``) consumes; the graph reference stays on it so the Phase-4 boundaries
remain addressable.

**Failure posture.** Author-facing contract gaps raise the structured compose-time
classes (``ContractViolation`` — e.g. the compile-affordance resolution seals
``compile-signature`` / ``compile-artifact`` raised at the binding-resolution pass via
``validator.resolve_compile`` when a ``compile = "..."`` directive resolves here).
Engine-internal misuse — a graph assembled against a registry that no longer carries
what compose resolved (missing declaration, missing declaration path, unresolved
external binding value, missing transport home) — raises ``ValueError``, the
established posture (``dispatch.py`` reads-mapping TypeError; ``model_gen``
schema_source ValueError). The graph-node-ports ≡ declaration cross-check is a plain
``assert`` (engine-bug attribution, not a contract surface — the dissolved item-9
decision).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Callable, Mapping

from pydantic import ValidationError

from conjured.errors import Check, ContractViolation
from conjured.hasher.hashes import pipeline_hash as compute_pipeline_hash
from conjured.manifest import collect_trainables, verify_artifacts
from conjured.ir.channel_types import FieldDecl
from conjured.ir.common import (
    CompileBinding,
    FilePathBindingValue,
    InlineBindingValue,
    MergeStrategy,
    ServiceBindingSupply,
)
from conjured.ir.composition import PipelineComposition, TrainableComposition
from conjured.ir.deployment import DeploymentDeclaration
from conjured.ir.graph import CompiledGraph, GraphNode, GraphNodeKind, Port
from conjured.ir.handler import HookDeclaration
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.runner.dispatch import (
    DispatchCallable,
    ResolvedBinding,
    ServiceBindingRuntime,
    construct,
    construct_trainable,
    make_reads_validator,
)
from conjured.validator.compile import compile_pipeline, effective_config
from conjured.validator.model_gen import build_model
from conjured.validator.normalize import is_explicit_null, normalize_binding_value
from conjured.validator.registry import DeclarationRegistry
from conjured.validator.resolve_adapter import (
    check_extras_disjoint,
    check_streamable_backend,
    check_trainable_backend,
    construct_adapter,
    construct_trainable_adapter,
    declares_remaining_budget,
    resolve_adapter,
)
from conjured.validator.resolve_compile import resolve_and_compile
from conjured.validator.resolve_handler import resolve_handler


def _audit_enforcement_on(deployment: DeploymentDeclaration | None) -> bool:
    """The deployment's audit-stamp enforcement opt-in (deployment/reference.md
    § training_contract): ``[training_contract].audit_enforcement`` (optional, defaults
    false). ``None`` deployment ⇒ off. Threaded into the resolution seals so a not-fresh
    in-scope module (handler / adapter / validator) refuses compose only under the opt-in
    (handler/reference.md § Audit stamps — the stamp read itself is enforcement-gated)."""
    return deployment is not None and deployment.training_contract.audit_enforcement


# ---------------------------------------------------------------------------
# The frozen runnable record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunnableNode:
    """One assembled node — everything the kernel walk needs per dispatch, frozen.

    ``position`` is the node's identity (the 0-indexed final compose-time dispatch
    order); ``entry_ordinal`` is the top-level declaration-entry index — the
    ``composition_ref`` ordinal (``"<pipeline_name>[<entry_ordinal>]"``,
    error-channel/reference.md § PipelineFailure payload). ``module`` is the resolved
    function's **defining module** — the vector-3 snapshot scope (bare-function kinds
    only; ``None`` for a trainable node, which has no author body
    per R-handler-010). ``bindings_values`` is the compose-resolved binding-value
    projection (``trainable.config`` for trainables) — the PF ``bindings_snapshot``
    source. ``service_binding_name`` names the node's declared service binding where
    one exists (the PF ``service_binding_name`` join for the well-known cause table).
    ``validate_reads`` is the node's reads-side validation boundary as a standalone
    callable (``dispatch.make_reads_validator`` — the same generated model the
    dispatch wrapper validates with), for the kernel walk's validate-then-copy
    projection: a consumer-seeded input value validates against it BEFORE the
    vector-4 deep copy.

    A nested ``pipeline`` embed node (``node_kind = "pipeline"`` — engine-invoking-
    engine, pipeline/reference.md § The nested ``pipeline`` composition kind) carries
    its recursively-assembled inner :class:`Runnable` on ``inner_runnable`` and a
    ``None`` ``dispatch``: the RUNNER owns the inner invocation (the kernel walk's
    nested-run branch threads ``parent_run_id`` and correlates the inner event
    stream), so no dispatch callable exists for this kind — structurally, not by
    convention. ``dispatch`` is non-``None`` for every other kind.
    """

    position: int
    entry_ordinal: int
    node_kind: GraphNodeKind
    qualified_name: str
    read_map: Mapping[str, str]
    write_map: Mapping[str, str]
    dispatch: DispatchCallable | None
    module: ModuleType | None
    bindings_values: Mapping[str, object]
    service_binding_name: str | None
    schema_source: str
    validate_reads: Callable[..., None]
    inner_runnable: "Runnable | None" = None
    #: The trainable composition's compose-fixed delivery selector (``streamable = true``
    #: — a delivery property, hash-excluded); the kernel walk routes an attached
    #: ``stream_sink`` to exactly this node. ``False`` for every other kind.
    streamable: bool = False


@dataclass(frozen=True, slots=True)
class Runnable:
    """The frozen runnable record stage-4 assembly completes into.

    ``input_fields`` is the declared ``[inputs]`` boundary (the API-boundary
    presence check's key set). ``merges`` maps channel → declared strategy (scoped
    composition-internal merges included — the fold applies wherever the declaration
    placed it). ``outer_written_channels`` is the static union of write-map targets
    minus scoped channels — exactly what ``RunResult.state`` carries (scoped channels
    stay encapsulated; consumer-seeded input channels are not graph-written).
    ``seed_validators`` maps each declared ``[inputs]`` channel to its **seed validator**
    — a standalone reads-side validation boundary over the channel's single ``[inputs]``
    ``FieldDecl`` (``dispatch.make_reads_validator`` over a one-field model). The kernel
    walk validates a raw seed against it at the seed's **first consumer** when that
    consumer is a merge fold (the writer-before-reader case has no reading node to borrow
    a model from — D1 first-consumer validation; the reads-projection case uses the
    reading node's own ``validate_reads``).
    ``graph`` keeps the compiled graph addressable (the Phase-4 seam: canonical-event
    boundaries key off graph structure). ``pipeline_hash`` is the running pipeline's
    pipeline-hash (``hasher.pipeline_hash`` over the declaration + registry), computed
    once at assemble and carried for the run-lifecycle + ``service_invocation`` canonical
    events that name the running pipeline (``hash-model.md`` § Canonical event types).
    """

    pipeline_name: str
    nodes: tuple[RunnableNode, ...]
    input_fields: tuple[FieldDecl, ...]
    merges: Mapping[str, MergeStrategy]
    outer_written_channels: frozenset[str]
    seed_validators: Mapping[str, Callable[..., None]]
    graph: CompiledGraph
    pipeline_hash: str


# ---------------------------------------------------------------------------
# Deployment transport resolution (override-over-shared, by pipeline name)
# ---------------------------------------------------------------------------


def _pipeline_override(deployment: DeploymentDeclaration, pipeline_name: str):
    return next(
        (o for o in deployment.pipelines if o.pipeline_qualified_name == pipeline_name),
        None,
    )


def _deliver_transport_values(
    values: Mapping[str, object], owner: str, composition_ref: str
) -> dict[str, object]:
    """The delivery-side resolution of the reserved explicit-null form: a compose-admitted
    ``{ null = true }`` transport value delivers as Python ``None`` (handler/reference.md
    explicit-null region — normalize-to-null before the otherwise-opaque passthrough).
    Admission (nullable-declared target) was compose-checked; this is pure spelling→value."""
    # guarantees: explicit-null-delivers-none
    return {
        k: (
            None
            if is_explicit_null(
                v, owner=f"{owner}.{k}", section_path=f"{owner}.{k}",
                composition_ref=composition_ref,
            )
            else v
        )
        for k, v in values.items()
    }


def _transport_for_binding(
    deployment: DeploymentDeclaration | None,
    pipeline_name: str,
    binding_name: str,
    service_type: ServiceTypeDeclaration,
) -> dict[str, object]:
    """The ``transport.<name>`` block's values for one service binding, honoring the
    deployment's ``pipelines.<name>`` override (override-over-shared — the same
    resolution order the compose-time coverage check applies). A binding whose
    service-type declares no transport fields needs no block (→ ``{}``); a missing
    home for declared transport fields is engine-internal misuse at this layer (the
    author-facing coverage check is compose-time, R-pipeline-001) → ``ValueError``."""
    if deployment is not None:
        override = _pipeline_override(deployment, pipeline_name)
        if override is not None:
            for blk in override.transport:
                if blk.name == binding_name:
                    return _deliver_transport_values(
                        blk.values, f"transport.{binding_name}", pipeline_name)
        for blk in deployment.transport:
            if blk.name == binding_name:
                return _deliver_transport_values(
                    blk.values, f"transport.{binding_name}", pipeline_name)
    if not service_type.transport_schema:
        return {}
    raise ValueError(
        f"assemble: no transport.{binding_name} block covers service binding "
        f"'{binding_name}' (service-type '{service_type.name}' declares transport "
        "fields) — assemble a service-bearing pipeline with the deployment that "
        "passed compose-time coverage (R-pipeline-001)"
    )


def _transport_for_hook_body(
    deployment: DeploymentDeclaration | None,
    pipeline_name: str,
    hook_qualified_name: str,
    transport_schema: tuple[FieldDecl, ...],
) -> dict[str, object]:
    """The ``hook_transport."<qn>"`` block's values for a hook's declared
    ``transport_schema`` fields — the stdlib-emission delivery's source (delivery
    follows the emission boundary: these values reach the hook BODY as kwargs, like
    bindings — handler/reference.md § ``transport_schema``). Honors the deployment's
    ``pipelines.<name>`` override (override-over-shared, the same resolution order the
    compose-time hook-coverage check applies). A hook declaring no transport fields
    needs no values (→ ``{}``); declared fields with no covering block is
    engine-internal misuse at this layer (the author-facing coverage check is
    compose-time, R-pipeline-001) → ``ValueError``. Only the DECLARED fields are
    projected from the block (the signature union is built from the schema)."""
    if not transport_schema:
        return {}
    blocks = []
    if deployment is not None:
        override = _pipeline_override(deployment, pipeline_name)
        if override is not None:
            blocks.extend(override.hook_transport)
        blocks.extend(deployment.hook_transport)
    for blk in blocks:
        if blk.hook_qualified_name == hook_qualified_name:
            missing = [f.name for f in transport_schema if f.name not in blk.values]
            if missing:
                raise ValueError(
                    f'assemble: hook_transport."{hook_qualified_name}" omits declared '
                    f"transport_schema field(s) {missing} — compose-time hook-transport "
                    "coverage (R-pipeline-001) guarantees this cannot happen for the "
                    "deployment it validated"
                )
            return _deliver_transport_values(
                {f.name: blk.values[f.name] for f in transport_schema},
                f'hook_transport."{hook_qualified_name}"', pipeline_name,
            )
    raise ValueError(
        f'assemble: no hook_transport."{hook_qualified_name}" block covers the hook\'s '
        f"declared transport_schema fields "
        f"{sorted(f.name for f in transport_schema)} — assemble a hook-bearing "
        "pipeline with the deployment that passed compose-time hook-transport "
        "coverage (R-pipeline-001)"
    )


# ---------------------------------------------------------------------------
# Per-node joins
# ---------------------------------------------------------------------------


def _model_name(node: GraphNode, which: str) -> str:
    safe = re.sub(r"\W", "_", node.qualified_name)
    return f"{safe}__{node.position}__{which}"


def _assert_ports_match_declaration(
    node: GraphNode, reads: tuple[FieldDecl, ...], outputs: tuple[FieldDecl, ...]
) -> None:
    """The item-9 internal assertion (the dissolved construct-time cross-check):
    graph-node port names ≡ the joined declaration's reads/output field names. The
    kwargs are built from the declaration and a mismatch always throws at dispatch,
    so this is engine-bug attribution — a registry that drifted between compile and
    assemble — never a contract surface (no registered check)."""
    declared_reads = {f.name for f in reads}
    graph_reads = {p.name for p in node.input_ports}
    assert graph_reads == declared_reads, (
        f"engine bug: graph node '{node.qualified_name}'@{node.position} input ports "
        f"{sorted(graph_reads)} diverge from the joined declaration's reads "
        f"{sorted(declared_reads)} — the registry drifted between compile and assemble"
    )
    declared_outputs = {f.name for f in outputs}
    graph_outputs = {p.name for p in node.output_ports}
    assert graph_outputs == declared_outputs, (
        f"engine bug: graph node '{node.qualified_name}'@{node.position} output ports "
        f"{sorted(graph_outputs)} diverge from the joined declaration's output_schema "
        f"{sorted(declared_outputs)} — the registry drifted between compile and assemble"
    )


def _validate_binding_value(
    binding, value: object, *, node: GraphNode, schema_source: str,
    audit_enforcement: bool = False,
) -> None:
    """Validate one compose-resolved ``bindings.<name>`` value against a model generated
    over the binding's declared ``SchemaBinding.fields`` (D4 — handler/reference.md
    § Binding value-supply grammar: "both go through the same Pydantic validator", so a
    constraint on a binding field enforces). ``value`` is the **normalized canonical form**
    (:func:`~conjured.validator.normalize.normalize_binding_value` ran at resolution): a
    single-field binding's is the **bare value** of its one field; a multi-field binding's
    is the field-keyed ``dict``. A single-field value validates by wrapping it back under
    its one field name; a multi-field value MUST be an object keyed by the declared fields —
    a non-object is malformed. Compose-fixed values validate once at assemble — a
    ``ContractViolation`` (``BINDING_VALUE_SHAPE``), not the dispatch-only
    ``SchemaValidationError``. A binding with no declared fields is skipped."""
    fields = binding.body.fields
    if not fields:
        return
    model = build_model(
        f"{_model_name(node, 'binding')}__{binding.name}", fields,
        schema_source=schema_source, audit_enforcement=audit_enforcement,
    )
    if len(fields) == 1:
        payload = {fields[0].name: value}  # the bare value IS the single field's value
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise ContractViolation(
            check=Check.BINDING_VALUE_SHAPE, rule_id="R-pipeline-001",
            expected=f"bindings.{binding.name} (a multi-field binding) is supplied an "
                     f"object keyed by its declared fields "
                     f"{sorted(f.name for f in fields)}",
            actual=f"a non-object {type(value).__name__} value for a multi-field binding",
            remediation_hint="supply an inline table (or external file) keyed by the "
                             "binding's field names; a bare scalar is only the "
                             "single-field shorthand",
            file_path=schema_source, section_path=f"bindings.{binding.name}",
        )
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ())) or "<value>"
        raise ContractViolation(
            check=Check.BINDING_VALUE_SHAPE, rule_id="R-pipeline-001",
            expected=f"the bindings.{binding.name} value conforms to its declared schema "
                     f"(the same Pydantic validator the reads/output boundaries use)",
            actual=f"{loc}: {first.get('msg', 'invalid value')}",
            remediation_hint="align the supplied binding value with its declared "
                             "bindings.<name> field types / constraints",
            file_path=schema_source, section_path=f"bindings.{binding.name}.{loc}",
        ) from exc


def _resolve_bindings(
    declaration, node: GraphNode, schema_source: str, *, audit_enforcement: bool = False
) -> tuple[ResolvedBinding, ...]:
    """Join each declared ``bindings.<name>`` to its compose-resolved value: the
    supplied inline value, the stamped external-file ``resolved`` value, or the
    declared ship-time default where the node omits a default-bearing binding
    (handler/reference.md § Ship-time defaults). A ``compile = "<compiler>"`` directive
    binding instead resolves its named compiler, binds the directive's params, and runs it
    once here to produce the artifact it delivers (``validator.resolve_compile``;
    handler/reference.md § The ``compile = "..."`` directive sub-form) — engine-owned, not
    node-supplied and not schema-validated."""
    supplied = {value.name: value for value in node.bindings}
    # The bindings half of the item-9 cross-check (recorded scope: graph-node
    # ports/BINDINGS ≡ the joined declaration): a supplied value matching no declared
    # binding would otherwise drop silently — compose-time supply matching
    # (R-pipeline-001) rejected orphans, so reaching one here is registry drift.
    orphans = sorted(set(supplied) - {b.name for b in declaration.bindings})
    assert not orphans, (
        f"engine bug: node '{node.qualified_name}'@{node.position} supplies binding "
        f"value(s) {orphans} the joined declaration does not declare — the registry "
        "drifted between compile and assemble"
    )
    resolved: list[ResolvedBinding] = []
    for binding in declaration.bindings:
        if isinstance(binding.body, CompileBinding):
            # The engine resolves the named compiler, binds the directive's declared params,
            # and runs it once at binding resolution to produce the artifact — delivered as the
            # engine-owned kwarg value, forwarded as-is (vector-4-copy-exempt; NOT schema-
            # validated — a compile binding declares a compiler, not a value schema). Every
            # resolution failure is a compose-time ContractViolation (handler/reference.md §
            # The ``compile = "..."`` directive sub-form; validator.resolve_compile owns it).
            value: object = resolve_and_compile(
                binding.body.compiler, binding.body.params, toml_path=schema_source,
                binding_name=binding.name,
                # RESOLVE-5: a third-party compiler module receives the pre-import
                # source read, so the stamp opt-in reaches it like any in-scope module.
                audit_enforcement=audit_enforcement,
            )
            resolved.append(ResolvedBinding(name=binding.name, body=binding.body, value=value))
            continue
        value_decl = supplied.get(binding.name)
        if value_decl is None:
            if binding.body.has_default:
                value = binding.body.default
            else:
                # Compose guarantees supplied-or-default (R-pipeline-001 binding-supply
                # matching) — reaching here means the graph and registry diverged.
                raise ValueError(
                    f"assemble: bindings.{binding.name} on '{node.qualified_name}' is "
                    "unsupplied and declares no ship-time default — compose-time "
                    "binding-supply matching guarantees this cannot happen for the "
                    "graph it validated"
                )
        elif isinstance(value_decl, InlineBindingValue):
            value = value_decl.value
        elif isinstance(value_decl, FilePathBindingValue):
            if value_decl.content_hash is None:
                raise ValueError(
                    f"assemble: external binding value bindings.{binding.name} "
                    f"('{value_decl.path}') is unresolved — the stage-1 resolution "
                    "pass (validator.resolve) stamps resolved content before assembly"
                )
            value = value_decl.resolved
        else:  # pragma: no cover - NodeBindingValue is a closed discriminated union
            raise TypeError(f"unknown binding value form for '{binding.name}'")
        # Normalize to the canonical form at the compose join — a single-field binding
        # reduces to its bare value (inline-bare / one-field-table / external-file / default
        # all collapse to one shape) and the reserved explicit-null form resolves to the
        # null value (nullable-only admission), so validation, the hash fold, and delivery
        # share one basis (handler/reference.md § Binding value-supply grammar + its
        # explicit-null region). The SAME single-sourced helper the hasher folds through.
        value = normalize_binding_value(
            binding.body.fields, value,
            owner=f"bindings.{binding.name}", file_path=schema_source,
            section_path=f"bindings.{binding.name}",
        )
        # D4 — the compose-resolved value validates against the binding's declared schema
        # (the missing enforcement point; a constraint on a binding field enforces here).
        _validate_binding_value(
            binding, value, node=node, schema_source=schema_source,
            audit_enforcement=audit_enforcement,
        )
        resolved.append(ResolvedBinding(name=binding.name, body=binding.body, value=value))
    return tuple(resolved)


def _service_runtime(
    declaration,
    node: GraphNode,
    supplies: Mapping[str, ServiceBindingSupply],
    registry: DeclarationRegistry,
    deployment: DeploymentDeclaration | None,
    pipeline_name: str,
    schema_source: str,
    adapter_cache: dict[tuple[str, str], object],
) -> tuple[tuple[ServiceBindingRuntime, ...], str | None]:
    """Resolve the node's declared service binding (exactly one for a service handler,
    at most one for a hook — compose-checked) into its runtime: the B2 adapter
    instance (one per composition scope, identity-only construction), the
    composition-fixed **effective config kwargs** (the supply entry's ``config`` block,
    supplied-or-default per the shared :func:`~conjured.validator.compile.effective_config`
    derivation — the same contract ``[trainable.config]`` rides; delivered at
    ``invoke()`` exactly as the trainable's partial-applied config is), and the
    deployment's ``transport.<name>`` block for the binding — the bound service-type's
    transport reaches the adapter through the binding's block whether the node is a
    service handler or a backend-SDK-emission hook (the block compose's
    transport-coverage check validates is the block delivered here; a hook's own
    ``hook_transport."<qn>"`` block carries the hook-body-side transport schema, not
    the adapter's)."""
    service_bindings = getattr(declaration, "service_bindings", ())
    if not service_bindings:
        return (), None
    sb = service_bindings[0]
    supply = supplies.get(sb.name)
    if supply is None:
        raise ValueError(
            f"assemble: no service_bindings.{sb.name} identity supply for "
            f"'{node.qualified_name}' — compose-time supply matching guarantees this "
            "cannot happen for the graph it validated"
        )
    service_type = registry.get_service_type(sb.type)
    if service_type is None:
        raise ValueError(
            f"assemble: service-type '{sb.type}' is not in the registry — it resolved "
            "at compose; the registry drifted between compile and assemble"
        )
    config = effective_config(
        supply.config, service_type,
        composition_ref=pipeline_name,
        section_path=f"service_bindings.{sb.name}.config",
    )
    cache_key = (node.composition_path or "", sb.name)
    adapter = adapter_cache.get(cache_key)
    if adapter is None:
        service_type_path = registry.get_service_type_path(sb.type)
        if service_type_path is None:
            raise ValueError(
                f"assemble: no declaration path registered for service-type "
                f"'{sb.type}' (DeclarationRegistry.add_service_type toml_path=) — "
                "the resolution seals' diagnostics need the declaring artifact"
            )
        adapter_cls = resolve_adapter(
            sb.type, service_type, toml_path=service_type_path,
            audit_enforcement=_audit_enforcement_on(deployment),
        )
        adapter = construct_adapter(
            adapter_cls, dict(supply.identity),
            qualified_name=f"{adapter_cls.__module__}.{adapter_cls.__name__}",
            toml_path=service_type_path,
        )
        adapter_cache[cache_key] = adapter
    transport = _transport_for_binding(
        deployment, pipeline_name, sb.name, service_type
    )
    # The extras-disjointness check (native-library/reference.md extras rider): when the
    # bound adapter declares reserved_wire_keys and the effective config carries an `extras`
    # table, the keys must be disjoint. type(adapter) carries reserved_wire_keys on both the
    # cache-hit and cache-miss paths; a generic service adapter (no reserved_wire_keys) is
    # skipped inside the check.
    check_extras_disjoint(
        type(adapter), config,
        qualified_name=f"{type(adapter).__module__}.{type(adapter).__name__}",
        toml_path=registry.get_service_type_path(sb.type) or pipeline_name,
    )
    runtime = ServiceBindingRuntime(
        name=sb.name, adapter=adapter, config=config, transport_extra=transport,
        # Deadline-propagation participation, read from the same class the signature
        # seal validated (service-type/reference.md § Deadline propagation).
        accepts_budget=declares_remaining_budget(type(adapter)),
    )
    return (runtime,), sb.name


def _assemble_bare(
    node: GraphNode,
    graph: CompiledGraph,
    registry: DeclarationRegistry,
    deployment: DeploymentDeclaration | None,
    pipeline_supplies: Mapping[str, ServiceBindingSupply],
    adapter_cache: dict[tuple[str, str], object],
) -> RunnableNode:
    """Stage-4 join for the bare-function kinds (transform / service / hook): top-level
    nodes join the registry's handler declaration; flattened composition members join
    the handler declaration their ``[[preprocessors]]`` entry references — the same
    registered handler an outer node joins (the mirror-pipeline principle)."""
    if node.callable_ref is None:  # pragma: no cover - compiler stamps every bare node
        raise ValueError(
            f"assemble: bare-function node '{node.qualified_name}'@{node.position} "
            "carries no callable_ref — the compiled graph is malformed"
        )
    if node.composition_path is None:
        declaration = registry.get_handler(node.callable_ref)
        if declaration is None:
            raise ValueError(
                f"assemble: handler '{node.callable_ref}' is not in the registry — it "
                "resolved at compose; the registry drifted between compile and assemble"
            )
        toml_path = registry.get_handler_path(node.callable_ref)
        if toml_path is None:
            raise ValueError(
                f"assemble: no declaration path registered for handler "
                f"'{node.callable_ref}' (DeclarationRegistry.add_handler toml_path=) — "
                "SVE.schema_source / CV.file_path need the contract-document path"
            )
        supplies = pipeline_supplies
    else:
        composition = registry.get_composition(node.composition_path)
        if composition is None:
            raise ValueError(
                f"assemble: composition '{node.composition_path}' is not in the "
                "registry — it resolved at compose; the registry drifted between "
                "compile and assemble"
            )
        if not isinstance(composition, TrainableComposition):
            raise ValueError(
                f"assemble: composition '{node.composition_path}' joined a "
                f"{type(composition).__name__} for flattened member "
                f"'{node.member_name}' — only a trainable composition carries "
                "[[preprocessors]]; the registry drifted between compile and assemble"
            )
        preproc = next(
            (p for p in composition.preprocessors if p.id == node.member_name), None
        )
        if preproc is None:
            raise ValueError(
                f"assemble: composition '{node.composition_path}' has no "
                f"[[preprocessors]] entry named '{node.member_name}' — the registry "
                "drifted between compile and assemble"
            )
        # A preprocessor is a name-reference: it joins the SAME registered handler declaration
        # an outer node joins (the mirror-pipeline principle), so delivery / default / declared
        # field validation resolve from the real declaration — no synthesized COPY-only shape.
        # The contract document is the REFERENCED handler's own TOML, not the composition's.
        declaration = registry.get_handler(preproc.name)
        if declaration is None:
            raise ValueError(
                f"assemble: preprocessor handler '{preproc.name}' is not in the registry — it "
                "resolved at compose; the registry drifted between compile and assemble"
            )
        toml_path = registry.get_handler_path(preproc.name)
        if toml_path is None:
            raise ValueError(
                f"assemble: no declaration path registered for preprocessor handler "
                f"'{preproc.name}' (DeclarationRegistry.add_handler toml_path=) — "
                "SVE.schema_source / CV.file_path need the contract-document path"
            )
        supplies = {s.name: s for s in composition.service_bindings}

    is_hook = node.node_kind == "hook"
    if is_hook != isinstance(declaration, HookDeclaration):
        raise ValueError(
            f"assemble: node '{node.qualified_name}'@{node.position} has node_kind "
            f"'{node.node_kind}' but joined a {type(declaration).__name__} — the "
            "registry drifted between compile and assemble"
        )
    output_fields = (
        () if isinstance(declaration, HookDeclaration) else declaration.output_schema
    )
    _assert_ports_match_declaration(node, declaration.reads, output_fields)

    # The deployment's audit-stamp enforcement opt-in — threaded into every resolution seal
    # below so a not-fresh in-scope module (handler / adapter / validator) refuses only under
    # the opt-in (handler/reference.md § Audit stamps).
    audit_enforcement = _audit_enforcement_on(deployment)

    # The Phase-2 resolution seals (steps 2-7: pre-import AST audit, import, vector-2
    # shape, signature-union) — every failure a structured compose-time CV.
    entry = resolve_handler(
        node.callable_ref, declaration, toml_path=toml_path,
        audit_enforcement=audit_enforcement,
    )
    schema_source = entry.toml_path.as_posix()

    # The vector-3 layer's scope: the resolved function's defining module (D3).
    module = sys.modules.get(entry.callable.__module__)
    if module is None:
        raise ValueError(
            f"assemble: defining module '{entry.callable.__module__}' of "
            f"'{entry.qualified_name}' is not in sys.modules after resolution — "
            "the vector-3 snapshot layer has no namespace to guard"
        )

    reads_model = build_model(
        _model_name(node, "reads"), declaration.reads, schema_source=schema_source,
        audit_enforcement=audit_enforcement,
    )
    output_model = (
        None
        if is_hook
        else build_model(
            _model_name(node, "output"), output_fields, schema_source=schema_source,
            audit_enforcement=audit_enforcement,
        )
    )
    resolved_bindings = _resolve_bindings(
        declaration, node, schema_source, audit_enforcement=audit_enforcement
    )
    services, service_binding_name = _service_runtime(
        declaration, node, supplies, registry, deployment,
        graph.pipeline_name, schema_source, adapter_cache,
    )
    # Stdlib-emission transport delivery (delivery follows the emission boundary): a
    # hook's declared transport_schema fields are deployment-supplied by the
    # hook_transport."<qn>" block and reach the BODY as kwargs, like bindings (the
    # field names are already in the R-handler-001 signature union the resolution
    # seal checked). Non-hooks have no transport_schema (kind discipline) → {}.
    hook_transport = (
        _transport_for_hook_body(
            deployment, graph.pipeline_name, node.qualified_name,
            declaration.transport_schema,
        )
        if isinstance(declaration, HookDeclaration)
        else {}
    )
    dispatch = construct(
        entry, node, reads_model, output_model, resolved_bindings, services=services,
        hook_transport=hook_transport,
    )
    return RunnableNode(
        position=node.position,
        entry_ordinal=node.entry_ordinal,
        node_kind=node.node_kind,
        qualified_name=node.qualified_name,
        read_map=MappingProxyType(dict(node.read_map)),
        write_map=MappingProxyType(dict(node.write_map)),
        dispatch=dispatch,
        module=module,
        bindings_values=MappingProxyType(
            {rb.name: rb.value for rb in resolved_bindings}
        ),
        service_binding_name=service_binding_name,
        schema_source=schema_source,
        validate_reads=make_reads_validator(
            reads_model=reads_model, input_ports=node.input_ports,
            qualified_name=entry.qualified_name, schema_source=schema_source,
        ),
    )


def _assemble_trainable(
    node: GraphNode,
    graph: CompiledGraph,
    registry: DeclarationRegistry,
    deployment: DeploymentDeclaration | None,
) -> RunnableNode:
    """Stage-4 join for the trainable composition's terminal node: engine-constructed
    dispatch against the certified trainable backend (R-handler-010 — no author body,
    no author module; ``module=None``). ``[trainable.config]`` is the trainable kind's
    config supply site; the **effective** values (supplied-or-default, the shared
    derivation) are what partial-apply into the dispatch and what the PF
    ``bindings_snapshot`` records."""
    if node.composition_path is None:  # pragma: no cover - compiler stamps it
        raise ValueError(
            f"assemble: trainable node '{node.qualified_name}'@{node.position} carries "
            "no composition_path — the compiled graph is malformed"
        )
    composition = registry.get_composition(node.composition_path)
    if composition is None:
        raise ValueError(
            f"assemble: composition '{node.composition_path}' is not in the registry — "
            "it resolved at compose; the registry drifted between compile and assemble"
        )
    if not isinstance(composition, TrainableComposition):
        raise ValueError(
            f"assemble: composition '{node.composition_path}' joined a "
            f"{type(composition).__name__} for trainable node "
            f"'{node.qualified_name}' — only a trainable composition carries "
            "[trainable]; the registry drifted between compile and assemble"
        )
    trainable = composition.trainable
    _assert_ports_match_declaration(node, trainable.reads, trainable.output_schema)

    sb = trainable.service_bindings[0]  # exactly one — compose-checked (R-handler-008)
    supply = next((s for s in composition.service_bindings if s.name == sb.name), None)
    if supply is None:
        raise ValueError(
            f"assemble: no [service_bindings.{sb.name}] supply in composition "
            f"'{node.composition_path}' — compose-time supply matching guarantees "
            "this cannot happen for the graph it validated"
        )
    service_type = registry.get_service_type(sb.type)
    if service_type is None:
        raise ValueError(
            f"assemble: service-type '{sb.type}' is not in the registry — it resolved "
            "at compose; the registry drifted between compile and assemble"
        )
    service_type_path = registry.get_service_type_path(sb.type)
    if service_type_path is None:
        raise ValueError(
            f"assemble: no declaration path registered for service-type '{sb.type}' "
            "(DeclarationRegistry.add_service_type toml_path=)"
        )
    config = effective_config(
        trainable.config, service_type,
        composition_ref=node.composition_path, section_path="trainable.config",
    )
    adapter_cls = resolve_adapter(
        sb.type, service_type, toml_path=service_type_path,
        audit_enforcement=_audit_enforcement_on(deployment),
    )
    check_trainable_backend(
        adapter_cls,
        qualified_name=f"{adapter_cls.__module__}.{adapter_cls.__name__}",
        toml_path=service_type_path,
    )
    if trainable.streamable:
        # The streaming-capability half of the gate: a `streamable = true` declaration
        # promises token delivery the bound backend must be able to honor — an adapter
        # with no `invoke_streaming` generator fails HERE at compose, never a silent
        # buffered fallback at dispatch (in Conjured graceful degrade is training-data
        # corruption; a delivery promise is a contract like any other).
        check_streamable_backend(
            adapter_cls,
            qualified_name=f"{adapter_cls.__module__}.{adapter_cls.__name__}",
            toml_path=service_type_path,
            service_type=service_type,  # the [config_schema] half of the kwargs walk
        )
    # The extras-disjointness check: a `table` extras config key may not name a reserved
    # wire key (native-library/reference.md extras rider) — the adapter is resolved here,
    # so reserved_wire_keys is in hand.
    check_extras_disjoint(
        adapter_cls, config,
        qualified_name=f"{adapter_cls.__module__}.{adapter_cls.__name__}",
        toml_path=service_type_path,
    )
    adapter = construct_trainable_adapter(
        adapter_cls,
        dict(supply.identity),
        output_schema=trainable.output_schema,
        schema_source=node.composition_path,
        qualified_name=f"{adapter_cls.__module__}.{adapter_cls.__name__}",
        toml_path=service_type_path,
    )
    transport = _transport_for_binding(
        deployment, graph.pipeline_name, sb.name, service_type
    )
    audit_enforcement = _audit_enforcement_on(deployment)
    reads_model = build_model(
        _model_name(node, "reads"), trainable.reads,
        schema_source=node.composition_path, audit_enforcement=audit_enforcement,
    )
    output_model = build_model(
        _model_name(node, "output"), trainable.output_schema,
        schema_source=node.composition_path, audit_enforcement=audit_enforcement,
    )
    dispatch = construct_trainable(
        node,
        adapter=adapter,
        binding_name=sb.name,
        config=config,
        transport_extra=transport,
        reads_model=reads_model,
        output_model=output_model,
        schema_source=node.composition_path,
        streamable=trainable.streamable,
        # Deadline-propagation participation, PER SURFACE, from the same class the
        # signature seals validated (service-type/reference.md § Deadline propagation).
        accepts_budget_invoke=declares_remaining_budget(type(adapter)),
        accepts_budget_streaming=declares_remaining_budget(
            type(adapter), "invoke_streaming"
        ),
    )
    return RunnableNode(
        position=node.position,
        entry_ordinal=node.entry_ordinal,
        node_kind="trainable",
        qualified_name=node.qualified_name,
        read_map=MappingProxyType(dict(node.read_map)),
        write_map=MappingProxyType(dict(node.write_map)),
        dispatch=dispatch,
        module=None,  # no author body, no author module (R-handler-010)
        bindings_values=MappingProxyType(dict(config)),
        service_binding_name=sb.name,
        schema_source=node.composition_path,
        validate_reads=make_reads_validator(
            reads_model=reads_model, input_ports=node.input_ports,
            qualified_name=node.qualified_name,
            schema_source=node.composition_path,
        ),
        streamable=trainable.streamable,
    )


def _assemble_pipeline_embed(
    node: GraphNode,
    graph: CompiledGraph,
    registry: DeclarationRegistry,
    deployment: DeploymentDeclaration | None,
) -> RunnableNode:
    """Stage-4 join for a nested ``pipeline`` embed node (engine-invoking-engine —
    pipeline/reference.md § The nested ``pipeline`` composition kind): re-join the
    composition declaration via the registry (the same stage-4 join every other kind
    takes), then recursively complete the load lifecycle over the inner pipeline —
    compile (pure, deterministic; compose already validated the whole nested structure
    at the outer load, so this recompile re-derives the same graph, cycle backstop
    included) and assemble into the inner :class:`Runnable` the kernel walk invokes.

    The inner Runnable carries the inner pipeline's OWN pipeline-hash (own-hash-domain):
    the inner run's canonical events name the inner pipeline, correlated outward by
    ``parent_run_id``. Deployment transport for the inner pipeline's bindings resolves
    under the inner pipeline's own ``meta.name`` (the family rule — a nested-pipeline
    composition and a top-level pipeline share one identity model, so a deployment
    ``pipelines.<name>`` override addresses it by that name).

    ``dispatch`` is ``None`` — the runner owns the inner invocation (it must thread
    ``parent_run_id`` and skip the handler-bearing events; see ``runner.run``). The node
    has no author body and no module (``module=None``), no bindings, and no service
    binding of its own (the inner pipeline's are its own concern)."""
    if node.composition_path is None:  # pragma: no cover - compiler stamps it
        raise ValueError(
            f"assemble: pipeline-embed node '{node.qualified_name}'@{node.position} "
            "carries no composition_path — the compiled graph is malformed"
        )
    composition = registry.get_composition(node.composition_path)
    if composition is None:
        raise ValueError(
            f"assemble: composition '{node.composition_path}' is not in the registry — "
            "it resolved at compose; the registry drifted between compile and assemble"
        )
    if not isinstance(composition, PipelineComposition):
        raise ValueError(
            f"assemble: composition '{node.composition_path}' is not a nested pipeline "
            f"composition (got {type(composition).__name__}) — the registry drifted "
            "between compile and assemble"
        )
    inner_graph = compile_pipeline(
        composition.pipeline, registry,
        pipeline_name=composition.meta.name, deployment=deployment,
        file_path=node.composition_path,
        embed_stack=(node.composition_path,),
    )
    # The impl, not the public entry: the R-pipeline-003 artifact comparison runs once
    # at the TOP-level assemble over the recursively-collected trainable set — an inner
    # assemble re-firing it would double-emit drift events and mis-scope the
    # registration reconciliation.
    inner_runnable = _assemble_impl(inner_graph, registry, deployment)

    reads_model = build_model(
        _model_name(node, "reads"), composition.pipeline.inputs,
        schema_source=node.composition_path,
    )
    return RunnableNode(
        position=node.position,
        entry_ordinal=node.entry_ordinal,
        node_kind="pipeline",
        qualified_name=node.qualified_name,
        read_map=MappingProxyType(dict(node.read_map)),
        write_map=MappingProxyType(dict(node.write_map)),
        dispatch=None,  # the runner's nested-run branch owns the inner invocation
        module=None,  # no author body, no author module
        bindings_values=MappingProxyType({}),
        service_binding_name=None,
        schema_source=node.composition_path,
        validate_reads=make_reads_validator(
            reads_model=reads_model, input_ports=node.input_ports,
            qualified_name=node.qualified_name,
            schema_source=node.composition_path,
        ),
        inner_runnable=inner_runnable,
    )


# ---------------------------------------------------------------------------
# Per-seeded-channel seed validators (D1 first-consumer validation)
# ---------------------------------------------------------------------------


def _build_seed_validators(graph: CompiledGraph) -> dict[str, Callable[..., None]]:
    """One **seed validator** per declared ``[inputs]`` channel — a standalone reads-side
    validation boundary over the channel's single ``FieldDecl`` (``build_model`` of a
    one-field model + ``make_reads_validator``). The kernel walk validates a raw seed
    against it at the seed's **first consumer** when that consumer is a merge fold: the
    writer-before-reader fold has no reading node to borrow a reads model from, so the
    seed's own ``[inputs]`` declaration — the pipeline's API boundary — is the model and
    the diagnostics locus. The resulting ``SchemaValidationError`` is the ruled reads-side
    one (audit ``C1.HALT_ON_INPUT_VALIDATION_ERROR.001``, field path ``reads.<channel>``),
    attributed to the pipeline (``handler_qualified_name`` = the pipeline name;
    ``schema_source`` = the pipeline declaration path). ``[inputs]`` fields carry no
    validation keywords (the boundary forbid), so the model is type-only."""
    validators: dict[str, Callable[..., None]] = {}
    safe_pipeline = re.sub(r"\W", "_", graph.pipeline_name)
    for field in graph.inputs:
        model = build_model(
            f"{safe_pipeline}__inputs__{field.name}",
            (field,),
            schema_source=graph.source_path,
        )
        validators[field.name] = make_reads_validator(
            reads_model=model,
            input_ports=(Port(name=field.name, type=field.type),),
            qualified_name=graph.pipeline_name,
            schema_source=graph.source_path,
        )
    return validators


# ---------------------------------------------------------------------------
# The public entry — lifecycle stage 4
# ---------------------------------------------------------------------------


def assemble(
    graph: CompiledGraph,
    registry: DeclarationRegistry,
    deployment: DeploymentDeclaration | None = None,
) -> Runnable:
    """Complete pipeline load lifecycle stage 4 over ``graph`` into a frozen
    :class:`Runnable` (the public name + shape pinned by the Phase-3 B6 ruling).
    ``deployment`` defaults to ``registry.deployment`` — the same default the
    compose-time validator applies.

    The public entry ALSO runs the R-pipeline-003 trained-artifact integrity
    comparison (``conjured.manifest.verify_artifacts``) over the deployment's
    ``[artifacts]`` registrations, exactly once per top-level assemble — the
    trainable set is collected RECURSIVELY through nested ``pipeline`` embeds, so
    the flat registration table reaches every deployed trainable; the embed
    recursion (:func:`_assemble_pipeline_embed`) calls :func:`_assemble_impl`
    directly, so no inner assemble re-fires the comparison and no public flag
    exists to skip it (the check is structural, not opt-out)."""
    deployment = deployment if deployment is not None else registry.deployment
    runnable = _assemble_impl(graph, registry, deployment)
    # R-pipeline-003 — drift events fire under either enforcement mode; halts are
    # enforcement-gated and graduated inside verify_artifacts (conjured.manifest owns
    # the comparison per hash-model § Integrity-enforcement opt-in).
    if deployment is not None and deployment.artifacts:
        assert graph.source_declaration is not None  # _assemble_impl already refused None
        trainables, duplicates = collect_trainables(graph.source_declaration, registry)
        verify_artifacts(
            deployment=deployment,
            deployment_dir=(
                Path(registry.deployment_path).parent
                if deployment is registry.deployment and registry.deployment_path is not None
                else None
            ),
            pipeline_name=graph.pipeline_name,
            pipeline_hash=runnable.pipeline_hash,
            trainables=trainables,
            duplicate_names=duplicates,
            registry=registry,
        )
    return runnable


def _assemble_impl(
    graph: CompiledGraph,
    registry: DeclarationRegistry,
    deployment: DeploymentDeclaration | None,
) -> Runnable:
    """The stage-4 assembly body (per-node joins + the pipeline-hash), shared by the
    public entry and the nested-``pipeline``-embed recursion. The R-pipeline-003
    artifact comparison lives on the PUBLIC entry only (one comparison per top-level
    assemble, over the recursively-collected trainable set)."""
    pipeline_supplies = {s.name: s for s in graph.service_bindings}
    adapter_cache: dict[tuple[str, str], object] = {}

    nodes: list[RunnableNode] = []
    for node in graph.nodes:
        if node.node_kind == "trainable":
            nodes.append(_assemble_trainable(node, graph, registry, deployment))
        elif node.node_kind == "pipeline":
            nodes.append(_assemble_pipeline_embed(node, graph, registry, deployment))
        else:
            nodes.append(
                _assemble_bare(
                    node, graph, registry, deployment, pipeline_supplies, adapter_cache
                )
            )

    scoped = frozenset(c.name for c in graph.channels if c.scoped)
    outer_written = (
        frozenset(ch for n in graph.nodes for ch in n.write_map.values()) - scoped
    )

    # The running pipeline's pipeline-hash — carried for the canonical events that name the
    # pipeline (the run-lifecycle events on the runner; service_invocation at the adapter
    # boundary, threaded through the dispatch ctx). Computed here, AFTER per-node assembly,
    # so a drifted/broken declaration fails loud at the node-level checks first; the hasher
    # is a pure function of the declaration IR + registry (resolution 3a — NOT the
    # dispatch-flattened graph), and assemble is the first stage that can import it without
    # the compile→hasher→compile cycle. The declaration is read off the GRAPH (compile
    # stamped the exact source onto it), so hash↔graph correspondence is structural — never a
    # by-name registry re-fetch that could resolve a different declaration.
    pipeline_declaration = graph.source_declaration
    if pipeline_declaration is None:
        raise ValueError(
            "assemble: the compiled graph carries no source_declaration — the pipeline-hash "
            "the canonical events carry is computed over the declaration this graph was "
            "compiled from; only compile_pipeline stamps it (a hand-built graph cannot be "
            "assembled into an event-emitting runnable)"
        )
    ph = compute_pipeline_hash(pipeline_declaration, registry)

    return Runnable(
        pipeline_name=graph.pipeline_name,
        nodes=tuple(nodes),
        input_fields=graph.inputs,
        merges=MappingProxyType({m.channel: m.strategy for m in graph.merges}),
        outer_written_channels=outer_written,
        seed_validators=MappingProxyType(_build_seed_validators(graph)),
        graph=graph,
        pipeline_hash=ph,
    )
