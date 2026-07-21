"""``conjured.derivables`` — the pipeline-derivables extraction surface (library entry point).

The **library** half of the derivables-extraction surface
(``conjured/docs/components/pipeline/reference.md`` § Extraction surface): given an
already-loaded :class:`~conjured.ir.pipeline.PipelineDeclaration` and a populated
:class:`~conjured.validator.registry.DeclarationRegistry`, produce the **pipeline-derivables
bundle** (§ Pipeline derivables) as the single deterministic JSON artifact its § Bundle
serialized form specifies. The CLI (``conjured.cli``) is a thin wrapper: it does the
path→registry assembly (the disk I/O the engine itself has no loader for — ``server/app.py``
module docstring) and delegates the bundle construction here.

**Compose-time, pure-read.** :func:`extract` runs the REAL verification path
(:func:`~conjured.validator.compile.compile_pipeline`) so the bundle can only derive from a
compile-validated pipeline — a side parse or a re-derived hash is exactly the
verification-path bypass the engine's incident log exists to prevent (trust-model; the
historical incidents). It then folds the SAME hashers the runtime uses
(:func:`~conjured.hasher.pipeline_hash` / :func:`~conjured.hasher.training_bundle_hash`) and
reuses the canonical schema renderers — never a parallel derivation. **No service invocations
occur; no handlers dispatch** (§ Extraction surface): nothing here reaches the runner, so the
pure-read seal holds by construction (RED-on-removal — the zero-events test in
``tests/derivables/test_extract.py``).

**No I/O here.** Reading the ``{ file = "..." }`` external binding declarations is the
resolution pass's job (``validator.resolve``), run by the CLI *before* :func:`extract`; the
hashers this calls fail loud on any unresolved external-file binding that slips through, so a
path is never silently hashed. :func:`extract` takes compile-level inputs and touches no disk.

**No deployment input** — the bundle is declared-structure-only (the 2026-07-07 design ruling).
Transport is per-environment and never part of the derivables; extraction runs compile's
registry-resolution + graph-topology groups, never the deployment-coverage group.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from conjured.hasher import pipeline_hash, training_bundle_hash
from conjured.hasher.hashes import (
    canon_supplied_bindings,
    non_hook_referenced_supplies,
)
from conjured.canonical import canon_schema, canon_schema_ordered, canon_value
from conjured.ir.common import FilePathBindingValue, InlineBindingValue, NodeBindingValue
from conjured.ir.composition import PipelineComposition, TrainableComposition
from conjured.ir.substitute import substitute_bundle_nodes
from conjured.ir.handler import HookDeclaration
from conjured.ir.pipeline import CompositionNode, HandlerNode, PipelineDeclaration
from conjured.validator import DeclarationRegistry, compile_pipeline

#: The envelope's own version integer (pipeline/reference.md § Bundle serialized form —
#: "this section specifies format ``1``"). A consumer MUST reject an unrecognized value; the
#: integer lets external tooling detect an evolution of the envelope shape. Bumped only by an
#: engine change to the envelope structure.
BUNDLE_FORMAT = 1


def extract(
    pipeline: PipelineDeclaration,
    registry: DeclarationRegistry,
    *,
    conjured_version: str,
) -> dict[str, Any]:
    """Extract the pipeline-derivables bundle for ``pipeline`` as the envelope dict
    (pipeline/reference.md § Pipeline derivables + § Bundle serialized form).

    ``pipeline`` is a loaded declaration, ``registry`` the populated declaration set its
    handler / composition / service-type references resolve against (both already
    binding-resolved by the caller — this function does no disk I/O). ``conjured_version`` is
    the engine version stamped into the bundle as provenance (the ``conjured_version`` member),
    passed in rather than read here so the pure function has no import-time coupling to the
    package metadata.

    Runs :func:`compile_pipeline` first (the one verification path — raises
    :class:`~conjured.errors.ContractViolation` before any extraction if the composition does
    not type-check), then folds the real hashers over the declaration IR. Returns the envelope
    mapping; :func:`serialize` renders it to the deterministic JSON artifact.
    """
    # The REAL verification path. deployment is left to fall back to registry.deployment
    # (None for a derivables extraction — the CLI loads no deployment), so compile runs its
    # registry-resolution + graph-topology groups and skips deployment coverage. An invalid
    # composition raises ContractViolation HERE, before any bundle is built — the bundle can
    # only ever describe a compile-validated pipeline (the verification-path seal).
    compile_pipeline(pipeline, registry, pipeline_name=pipeline.meta.name)

    # Pure-substitution embeds resolve FIRST (glossary § Bundle TOML): the walks below —
    # the trainables enumeration, the binding + composition snapshots — see the
    # post-substitute inlined form, exactly the composition the pipeline-hash covers (a
    # bundle-embedded trainable IS a trainable of this pipeline).
    substituted = substitute_bundle_nodes(
        pipeline.nodes, registry.get_composition, where=pipeline.meta.name,
    )
    if substituted is not pipeline.nodes:
        pipeline = pipeline.model_copy(update={"nodes": substituted})

    # pipeline_hash folds every referenced handler declaration + embedded own-hash-domain
    # composition (by reference) — computed BEFORE the snapshots so an unresolved external-file
    # binding (a path the resolution pass never stamped) fails loud here rather than surfacing
    # as a null in the snapshot (the hasher's external-file backstop).
    ph = pipeline_hash(pipeline, registry)

    bundle: dict[str, Any] = {
        "bundle_format": BUNDLE_FORMAT,
        "pipeline_hash": ph,
        "conjured_version": conjured_version,
        "trainables": _trainables(pipeline, registry),
        "binding_snapshot": _binding_snapshot(pipeline, registry),
        "composition_snapshot": _composition_snapshot(pipeline, registry),
    }
    return bundle


def serialize(bundle: dict[str, Any]) -> str:
    """Render an envelope dict to the deterministic JSON artifact (pipeline/reference.md
    § Bundle serialized form): one JSON object, UTF-8, **object keys sorted lexicographically**,
    so the same declaration set extracted by the same engine version produces a byte-identical
    artifact. Every value is JSON-native by construction (the hashers' canonical renderers), so
    ``json.dumps`` never coerces something lossy — and a non-finite float (TOML admits
    ``nan``/``inf``) raises via ``allow_nan=False`` rather than emitting ``NaN``/``Infinity``,
    which would make the artifact invalid under a strict RFC 8259 parser (the one-JSON-object
    guarantee, fail loud). Indented for the generator/agent audience —
    sorted keys keep it deterministic regardless of indentation."""
    return json.dumps(
        bundle, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False
    ) + "\n"


# guarantees: derivables-bundle-hash-provenance-pin
def bundle_hash(serialized: str) -> str:
    """The ``derivables_bundle_hash`` provenance value over a :func:`serialize`d bundle
    artifact — ``sha256:<hex>`` over the artifact's exact UTF-8 bytes
    (pipeline/reference.md § ``generator_info``). The serialized form is deterministic
    (sorted keys, byte-identical), so byte-exact hashing makes "the same derivables
    bundle" a checkable equality — the provenance pin for the generation-time
    conditioning inputs the structural hashes exclude (the bound service-type's
    top-level ``description`` among them; hash-model § What the pipeline-hash absorbs
    owns the exclusion)."""
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The `trainables` member — one entry per trainable composition node
# ---------------------------------------------------------------------------


def _trainables(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry
) -> dict[str, Any]:
    """One entry per trainable composition node in the pipeline, keyed by the trainable
    composition's declared ``meta`` name — the same key the trained-artifact manifest's
    ``training_bundle_hashes`` field uses, so a bundle entry and its manifest hash correlate
    without translation (pipeline/reference.md § Bundle serialized form). A composition
    ``meta.name`` is unique within the embedding pipeline (compile enforces it), so keying by
    name is one-entry-per-node. Nested ``pipeline`` composition embeds are a distinct
    own-hash-domain kind (their inner trainables fold by reference into the inner pipeline-hash
    and are the inner pipeline's derivables concern) — only the outer pipeline's own trainable
    composition nodes are enumerated here."""
    entries: dict[str, Any] = {}
    for node in pipeline.nodes:
        if not isinstance(node, CompositionNode):
            continue
        comp = registry.get_composition(node.name)
        if not isinstance(comp, TrainableComposition):
            # A nested `pipeline` composition (or any non-trainable) is not a trainable node.
            # An unresolved composition never reaches here — compile_pipeline (run first)
            # already raised on it.
            continue
        entries[comp.meta.name] = _trainable_entry(comp, registry)
    return entries


def _trainable_entry(
    comp: TrainableComposition, registry: DeclarationRegistry
) -> dict[str, Any]:
    """One trainable composition node's bundle entry (pipeline/reference.md § Pipeline
    derivables — the two per-trainable components): its ``training_bundle_hash`` (the binding
    identifier tying generated pairs to the declaration), the ``reads`` shape (the input-payload
    shape the generator must produce) and ``output_schema`` shape (the output-payload shape the
    generator LLM must emit), and the ``service_metadata`` identifying the bound backend + its
    generator-instruction ``description``.

    The shape renderers are the hashers' own canonical schema helpers (never re-derived): a
    name-keyed map for ``reads`` (field order non-semantic), an ordered list for ``output_schema``
    (a trainable's declared field order IS the enforced emission order — canon_schema_ordered)."""
    return {
        "training_bundle_hash": training_bundle_hash(comp, registry),
        "reads": canon_schema(comp.trainable.reads),
        "output_schema": canon_schema_ordered(comp.trainable.output_schema),
        "service_metadata": _service_metadata(comp, registry),
    }


def _service_metadata(
    comp: TrainableComposition, registry: DeclarationRegistry
) -> dict[str, Any]:
    """The bound trainable backend's service metadata (pipeline/reference.md § Service
    metadata): the backend service-type qualified name (identifying the backend) and its
    ``description`` string — the generator-instruction context (what the backend is for, what a
    useful input-output pair looks like from its perspective). The backend's adapter-contract
    input/output types ARE this trainable's ``reads`` / ``output_schema`` shapes (a trainable
    dispatch is a service-type adapter dispatch with ``input_payload`` = the ``trainable.reads``
    projection — pipeline/reference.md § Pipeline load lifecycle stage 4), already carried once
    at the entry level, so they are not restated here.

    The description folds into neither structural hash; its integrity pin is the provenance
    layer — the manifest's ``generator_info.derivables_bundle_hash`` (:func:`bundle_hash`)
    records the exact serialized bundle the generator consumed (pipeline/reference.md
    § generator_info).

    Compile (run first in :func:`extract`) guarantees exactly one backend binding that resolves
    to a registered service-type, so both lookups below are total — a missing resolution would
    already have raised."""
    backend_type = comp.trainable.service_bindings[0].type
    service_type = registry.get_service_type(backend_type)
    # Compile guaranteed the resolution (the docstring's totality claim) — an unresolved
    # backend here is engine drift between compile and extract, never a legitimate null:
    # folding it as description=None would present a fabricated "no declared description"
    # the projection cannot distinguish from a genuine one (the fail-loud stance). Same
    # engine-bug attribution as _composition_snapshot's assertion.
    assert service_type is not None, (
        f"engine bug: backend service-type '{backend_type}' is not in the registry — "
        "it resolved at compile; the registry drifted between compile and extract"
    )
    return {
        "service_type": backend_type,
        # description is Optional on the declaration (str | None); a backend with no declared
        # description folds JSON null — the member is always present (declared-structure-only).
        "description": service_type.description,
    }


# ---------------------------------------------------------------------------
# Non-hook node walk — the domain both snapshots share (= the pipeline-hash's domain)
# ---------------------------------------------------------------------------


def _non_hook_nodes(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry
) -> list[tuple[int, HandlerNode | CompositionNode]]:
    """The pipeline's nodes EXCLUDING hooks, each paired with a contiguous non-hook ``order``.

    Hooks (a ``HandlerNode`` resolving to a ``HookDeclaration``) contribute to neither hash and
    are not part of the training-contract composition — so both bundle snapshots scope to the
    non-hook domain, exactly what the pipeline-hash covers (hash-model.md § What the
    pipeline-hash absorbs — "hooks contribute to neither hash"; hasher/hashes.py). This is the
    single source of that filter + ordering so ``binding_snapshot`` and ``composition_snapshot``
    stay consistent (Don't solve it twice). Compile (run first in :func:`extract`) has resolved
    every handler name, so ``get_handler`` is total here. A composition node is never a hook."""
    result: list[tuple[int, HandlerNode | CompositionNode]] = []
    order = 0
    for node in pipeline.nodes:
        if isinstance(node, HandlerNode) and isinstance(
            registry.get_handler(node.name), HookDeclaration
        ):
            continue  # a hook is excluded from the composition the pipeline-hash covers
        result.append((order, node))
        order += 1
    return result


# ---------------------------------------------------------------------------
# The `binding_snapshot` member — the pipeline-fixed binding context
# ---------------------------------------------------------------------------


def _binding_value(b: NodeBindingValue) -> Any:
    """The resolved, canonicalized value of one supplied node binding — an inline value
    canonicalized as data, or an external-file binding's stamped canonical content (read +
    canonicalized by the resolution pass; a file and an equal inline value fold identically).
    An unresolved external-file binding never reaches here: this is called only for non-hook
    nodes (:func:`_non_hook_nodes`), and the ``pipeline_hash`` computed earlier in
    :func:`extract` folds every non-hook node's bindings and fails loud on an unresolved one
    first — so ``b.resolved`` is never ``None`` here."""
    if isinstance(b, FilePathBindingValue):
        return b.resolved
    if isinstance(b, InlineBindingValue):
        return canon_value(b.value)
    raise TypeError(  # pragma: no cover - NodeBindingValue is a closed union
        f"unhandled node binding value {type(b).__name__!r}"
    )


def _binding_snapshot(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry
) -> dict[str, Any]:
    """The pipeline-fixed binding snapshot (pipeline/reference.md § Pipeline derivables —
    Pipeline-fixed binding snapshot): the resolved ``bindings.<name>`` values each **non-hook**
    handler node supplies (with pipeline-level overrides applied — the values as written on the
    node, external files resolved) and the service-binding identity values from the pipeline's
    ``service_bindings`` entries. This is the generator's scoping input — *what to generate
    about* (which characters, scenes, prompt conventions are in scope).

    Hooks are excluded (:func:`_non_hook_nodes`): they contribute to neither hash and are not
    training-generation content, so folding a hook's binding here would be off-domain — and it
    is the only path by which an unresolved external-file binding could slip in as a silent null
    (the ``pipeline_hash`` fail-loud backstop skips hooks). Node bindings carry the contiguous
    non-hook ``position`` (matching the composition snapshot's ``order``) because a handler
    qualified name may repeat across positions; only nodes that actually supply a binding appear.
    Composition-internal bindings (a preprocessor's, a composition's own service identity) fold
    into that trainable's training-bundle-hash and its bundle entry, not into this
    outer-pipeline snapshot."""
    # The EFFECTIVE (supplied-or-default) values, through the hasher's own fold — the
    # single derivation that defines the binding contribution the pipeline-hash absorbs
    # (hash-model.md: the per-node contribution is the effective value), so the snapshot
    # and the hash cannot diverge: a node omitting a default-bearing binding contributes
    # the declared default here exactly as it folds into pipeline_hash, and two
    # hash-identical compositions (explicit X vs defaulted X) produce one bundle.
    node_bindings = []
    for order, node in _non_hook_nodes(pipeline, registry):
        if not isinstance(node, HandlerNode):
            continue
        decl = registry.get_handler(node.name)
        assert decl is not None  # compile resolved every name (the walk's totality claim)
        effective = canon_supplied_bindings(
            node.bindings, where=pipeline.meta.name, declared=decl.bindings
        )
        if effective:
            node_bindings.append(
                {"position": order, "node": node.name, "bindings": effective}
            )
    # The supply half scopes to the same non-hook domain the pipeline-hash folds
    # (hash-model.md: a supply entry referenced only by hooks is invisible to the hash
    # by construction) — reusing the hasher's own domain scan, never a re-derivation.
    referenced = non_hook_referenced_supplies(pipeline, registry)
    service_bindings = {
        s.name: {"type": s.type, "identity": canon_value(dict(s.identity))}
        for s in pipeline.service_bindings
        if s.name in referenced
    }
    return {"node_bindings": node_bindings, "service_bindings": service_bindings}


# ---------------------------------------------------------------------------
# The `composition_snapshot` member — the node list + order + wiring
# ---------------------------------------------------------------------------


def _composition_snapshot(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry
) -> dict[str, Any]:
    """The pipeline composition snapshot (pipeline/reference.md § Pipeline derivables —
    Pipeline composition snapshot): the ``nodes`` list, node order, and inter-node relationships
    — the same composition the ``pipeline_hash`` covers, carried for reproducibility so a
    consumer extracting before and after a composition edit receives different snapshots.

    Each node records its contiguous non-hook ``order``, kind, and as-written name; a handler
    node adds its authored ``reads_map`` / ``writes_map`` (the wiring that relates nodes through
    channels); a composition node adds its resolved ``composition_kind`` + ``meta`` name (it
    wires by flatten-by-name, carrying no per-node maps). **Hooks are excluded**
    (:func:`_non_hook_nodes`) — the pipeline-hash excludes them ("hooks contribute to neither
    hash"), so a faithful "same composition the pipeline-hash covers" snapshot must too:
    including them (and with gap-inclusive indices) would let a hook-only edit change the
    snapshot while the pipeline-hash stays byte-identical, breaking the stated 1:1
    snapshot↔pipeline-hash correspondence."""
    nodes: list[dict[str, Any]] = []
    for order, node in _non_hook_nodes(pipeline, registry):
        if isinstance(node, HandlerNode):
            nodes.append({
                "order": order,
                "kind": "handler",
                "name": node.name,
                "reads_map": dict(node.reads_map),
                "writes_map": dict(node.writes_map),
            })
        elif isinstance(node, CompositionNode):
            comp = registry.get_composition(node.name)
            entry: dict[str, Any] = {"order": order, "kind": "composition", "name": node.name}
            if isinstance(comp, (TrainableComposition, PipelineComposition)):
                entry["composition_kind"] = comp.meta.kind.value
                entry["meta_name"] = comp.meta.name
            else:  # pragma: no cover - substitution precedes the snapshot walk
                # The fail-loud sibling of the hasher's own-hash-domain backstop: a
                # composition that is neither own-hash-domain kind here means a walk
                # forgot to substitute (a bundle) or the registry drifted — never a
                # silently under-described snapshot entry.
                raise AssertionError(
                    f"composition '{node.name}' is not an own-hash-domain kind — "
                    "substitution precedes the snapshot walk (engine drift)"
                )
            nodes.append(entry)
    return {"pipeline_name": pipeline.meta.name, "nodes": nodes}
