"""Composition-TOML primitive IR — the trainable + nested-pipeline composition kinds.

Owned by ``conjured/docs/components/handler/reference.md`` § composition-TOML +
``kind-schemas/trainable.schema.toml`` + R-handler-006 / R-handler-010. A trainable
composition is a **scoped mini-pipeline** the embedding pipeline references by path: an
``[inputs]`` / ``[outputs]`` boundary, a ``[[preprocessors]]`` sequence (regular handler
nodes inside the trainable's scope, dispatched in declared order), exactly one terminal
``[trainable]`` node (engine-constructed dispatch, no author body), and optional internal
``[merge]``. The composition's own normalized hash IS its training-bundle-hash.

A **nested ``pipeline`` composition** (``pipeline/reference.md`` § The nested ``pipeline``
composition kind) is engine-invoking-engine: its body IS the pipeline grammar (the
mirror-pipeline principle — one grammar, one parser, one hash treatment per feature), so
:class:`PipelineComposition` carries a whole :class:`~conjured.ir.pipeline.PipelineDeclaration`
rather than paralleling it with fresh structure. Its own hash is its **pipeline-hash**
(own-hash-domain; folded by reference into the enclosing unit's hash).

**Composition-kind discriminator.** ``CompositionKind`` is the closed enum
``{trainable, bundle, pipeline}`` (a future ``tool`` kind extends it by engine change).
All three body grammars are canon-authored and modeled here: ``trainable`` (the scoped
mini-pipeline above), ``pipeline`` (:class:`PipelineComposition`), and ``bundle``
(:class:`BundleComposition` — the pure-substitution kind: a bare pipeline-``nodes``
fragment, textually substituted into the enclosing node sequence before scoping and
hashing; ``bundle.schema.toml`` + glossary § Bundle TOML own the grammar).

**``[trainable.config]`` carries config VALUES, not a schema.** Per
``service-type/reference.md`` § Hash placement and ``hash-model.md`` ("the config
*values* a trainable composition node supplies in ``[trainable.config]`` fold into that
trainable's training-bundle-hash"), and R-handler-011 ("compose-time generation
parameters: temperature, top-p, max-tokens"). Modeled as a ``Mapping[str, object]``
(config-field-name → value), validated against the bound service-type's
``[config_schema]`` at compose (Phase 1a). The ``trainable.schema.toml`` template
displays this block in schema shape — a template-display choice; the IR carries values.
"""

from __future__ import annotations

import enum
from typing import Literal, Mapping

from conjured.ir.base import IRModel
from conjured.ir.channel_types import FieldDecl
from conjured.ir.common import (
    MergeStrategy,
    NodeBindingValue,
    ServiceBindingDecl,
    ServiceBindingSupply,
)
from conjured.ir.pipeline import PipelineDeclaration, PipelineNode


class CompositionKind(str, enum.Enum):
    """The closed composition-kind discriminator (``meta.kind``). ``TRAINABLE`` and
    ``PIPELINE`` are the engine-owned-dispatch family (each an own-hash-domain unit);
    ``BUNDLE`` is the pure-substitution family (no own hash domain — its nodes fold
    into the enclosing unit inline). A future ``tool`` kind extends this enum by
    engine change.
    """

    TRAINABLE = "trainable"
    BUNDLE = "bundle"
    PIPELINE = "pipeline"


class CompositionMeta(IRModel):
    """The ``[meta]`` block of a composition declaration. The key set is **closed to
    ``{kind, name}``** (handler/reference.md § A composition mirrors the pipeline; the family
    rule): ``kind`` is the composition-kind discriminator (structural, hashed), ``name`` the
    identity / manifest-key handle (never hashed). No declaration-level ``description`` — author
    prose lives in the composition's ``[annotations]`` block, and a schema field's
    ``description`` is admitted only on the terminal ``trainable.output_schema`` (§ TOML field
    type discipline)."""

    kind: CompositionKind
    name: str  # unique within the embedding pipeline's namespace


class PreprocessorEntry(IRModel):
    """One ``[[preprocessors]]`` entry — a regular handler node inside the trainable's
    scope, a **name-reference** to a registered handler exactly like an outer-pipeline node
    (``HandlerNode``) plus the one composition-layer addition, the composition-local ``id``
    (``kind-schemas/trainable.schema.toml`` § ``[[preprocessors]]`` + R-handler-006). Its
    ports, its binding declarations (``delivery`` / ``default`` / validation), and a hook's
    ``transport_schema`` are **owned by the referenced handler declaration** and resolve via
    ``name`` — never inlined on the entry (the mirror-pipeline principle; one grammar / one
    fold path across both layers). The entry carries only the supplied
    ``[preprocessors.bindings]`` **values** and the authored (optional, per-port) wiring maps
    to the composition's scoped channels. Source order is dispatch order and is semantic (it
    contributes to the training-bundle-hash).
    """

    #: The node-realization discriminator, shared with the outer node-entry grammar. A
    #: ``[[preprocessors]]`` entry admits ``"handler"`` ONLY — by design, not a gap: this
    #: is the one id-labeled node sequence (each entry's ``id`` is a load-bearing address
    #: — the hook-transport key, the flattened member name) and a substituted node is
    #: anonymous. Composition embeds live at the unlabeled pipeline-family ``nodes``
    #: layers; the trainable is a deliberate composition boundary, kept explicit
    #: (handler/reference.md § A composition mirrors the pipeline).
    kind: Literal["handler"] = "handler"
    name: str  # qualified handler reference (the outer grammar's `name`)
    id: str  # composition-local node label (unique in this composition; qualifies to <meta.name>.<id>)
    bindings: tuple[NodeBindingValue, ...] = ()  # supplied compose-time binding values
    reads_map: Mapping[str, str] = {}  # input-port -> scoped channel (per-port, optional)
    writes_map: Mapping[str, str] = {}  # output-port -> scoped channel (per-port, optional)


class TrainableNode(IRModel):
    """The terminal ``[trainable]`` node — engine-constructed dispatch, no author body
    (R-handler-010). Dispatches after every preprocessor.
    """

    #: ``streamable`` — the composition's opt-in to run-scoped token delivery
    #: (``run(..., stream_sink=...)`` drives the bound adapter's ``invoke_streaming``;
    #: pipeline/reference.md § Pipeline invocation). A delivery selector, excluded from
    #: both hashes (hash-model § Training-bundle-hash); the terminal-node placement rule
    #: and the streaming-capability gate are mechanically enforced at compose
    #: (R-pipeline-001 streamable-terminal-node; ``check_streamable_backend``).
    streamable: bool = False
    #: ``[trainable.config]`` — compose-time generation-parameter VALUES partial-applied
    #: into the dispatch wrapper; validated against the bound service-type's
    #: ``[config_schema]`` (Phase 1a). NOT prompt-shaping content (R-handler-011).
    config: Mapping[str, object] = {}
    #: ``[trainable.service_bindings]`` — exactly one entry, the trainable backend
    #: (cardinality + trainable-backend property are Phase 1a).
    service_bindings: tuple[ServiceBindingDecl, ...]
    reads: tuple[FieldDecl, ...]  # ``[trainable.reads]`` input ports (required, body-required)
    output_schema: tuple[FieldDecl, ...]  # ``[trainable.output_schema]`` output ports (required, body-required)


class TrainableComposition(IRModel):
    """A trainable composition declaration (``meta.kind = "trainable"``)."""

    meta: CompositionMeta  # kind == TRAINABLE
    inputs: tuple[FieldDecl, ...] = ()  # boundary inputs (required, empty-allowed)
    outputs: tuple[FieldDecl, ...]  # boundary outputs (required, body-required)
    preprocessors: tuple[PreprocessorEntry, ...] = ()  # zero or more; ORDER IS SEMANTIC
    #: ``[service_bindings.<name>]`` — the composition's OWN service-binding identity supply,
    #: self-contained, mirroring the pipeline's ``service_bindings.<name>`` (divergence A,
    #: shape-i; ``handler/reference.md`` § composition ``service_bindings`` supply). Carries the
    #: identity-field VALUES (model selector, prompt-template, …) for the composition's backend
    #: and any service-kind preprocessor binding; they fold into the training-bundle-hash.
    #: Transport stays deployment-supplied (never here).
    service_bindings: tuple[ServiceBindingSupply, ...] = ()
    trainable: TrainableNode  # exactly one terminal node
    #: Optional internal ``[merge]`` — channel -> strategy, scoped to this composition.
    merge: Mapping[str, MergeStrategy] = {}
    #: Optional ``[annotations]`` — engine-opaque; may carry the ``postprocessors``
    #: UI-grouping list (names of OUTER-pipeline handlers; not modeled as structure here).
    annotations: Mapping[str, object] | None = None


class PipelineComposition(IRModel):
    """A nested ``pipeline`` composition declaration (``meta.kind = "pipeline"``) —
    engine-invoking-engine (``pipeline/reference.md`` § The nested ``pipeline``
    composition kind).

    The body IS the pipeline grammar, carried whole as a
    :class:`~conjured.ir.pipeline.PipelineDeclaration` (the mirror-pipeline principle:
    one grammar, one compiler, one hash treatment — the compiler, hasher, and runner
    consume ``pipeline`` through the same paths a top-level pipeline takes; the embed
    layer adds only the ``meta.kind`` discrimination). ``pipeline.meta`` mirrors this
    ``meta`` minus ``kind`` (both never hashed — the family rule). It follows the
    pipeline's presence-opts-in ``[outputs]`` arm, not the trainable's body-required
    arm. Its own identity hash is its **pipeline-hash** (own-hash-domain; folded by
    reference into the enclosing unit's hash — ``hash-model.md`` § What the
    pipeline-hash absorbs).
    """

    meta: CompositionMeta  # kind == PIPELINE
    pipeline: PipelineDeclaration  # the pipeline-shaped body, the exact pipeline grammar


class BundleComposition(IRModel):
    """A ``bundle`` composition declaration (``meta.kind = "bundle"``) — the
    pure-substitution composition kind (glossary § Bundle TOML; handler/reference.md
    § A composition mirrors the pipeline; ``bundle.schema.toml``).

    The body is a bare pipeline-``nodes`` fragment — the exact pipeline node-entry
    grammar, carried as the same node union the pipeline declares (one grammar, one
    parser across both layers). The engine textually substitutes these nodes into the
    enclosing node sequence at the embed point at compose, BEFORE scoping and hashing
    (:func:`conjured.ir.substitute.substitute_bundle_nodes` — every walker's entry
    chokepoint): a bundle declares no ``inputs``/``outputs`` boundary, no ``merge``, no
    ``service_bindings`` supply, and has **no own hash domain** — every downstream
    concern operates on the post-substitute inlined form as if the nodes had been
    declared directly in the enclosing unit. ``annotations`` is the engine-opaque
    author-prose block (metadata-class, never delivered, never hashed — and a bundle
    contributes no hash domain for it to be excluded from; it simply never rides the
    substitution)."""

    meta: CompositionMeta  # kind == BUNDLE
    #: The substituted content — pipeline node entries (handler nodes and/or nested
    #: composition embeds), in substituted dispatch order. Non-empty: an empty bundle
    #: substitutes nothing and is rejected at parse (a body-required section).
    nodes: tuple[PipelineNode, ...]
    #: Engine-opaque author prose (glossary § Bundle TOML: "optionally annotations").
    annotations: Mapping[str, object] = {}
