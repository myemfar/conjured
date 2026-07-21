"""Pipeline declaration IR — the parsed pipeline TOML.

Owned by ``conjured/docs/components/pipeline/reference.md`` (§ Pipeline TOML grammar;
R-pipeline-001 / R-pipeline-002). A pipeline is a named, ordered composition of nodes
the engine validates as a typed dataflow graph at load and dispatches in declared
order. This module models the **declaration** (the authored pipeline TOML); the
**compiled graph** the validator produces from it is ``conjured.ir.graph``.

The authored node ``reads_map`` / ``writes_map`` are **optional and per-port** here
(an unmapped port desugars to a same-named channel). The *normalized, always-explicit*
maps live on the compiled-graph node, not here — keeping the declaration faithful to
what the author wrote (sugar in) and the graph faithful to the normalized IR (sugar
desugared). Map values are plain channel-name strings — data only, no callable /
expression / file path (the vector-6 bound, ``conjured/docs/architecture/trust-model.md``).

A ``CompositionNode`` structurally carries no ``bindings`` / ``reads_map`` /
``writes_map`` (declaring any on a ``kind = "composition"`` entry raises
ContractViolation — pipeline reference § ``nodes``); the absence is by construction.
"""

from __future__ import annotations

from typing import Annotated, Literal, Mapping, Union

from pydantic import Field

from conjured.ir.base import IRModel
from conjured.ir.channel_types import FieldDecl
from conjured.ir.common import MergeStrategy, NodeBindingValue, ServiceBindingSupply


class PipelineMeta(IRModel):
    """The ``[meta]`` block of a top-level pipeline declaration — the pipeline's
    self-name under the **family rule** (every composable unit self-names via
    ``[meta].name``; ``conjured/docs/architecture/hash-model.md`` § What is explicitly NOT
    in the pipeline-hash → "The family rule"). ``name`` is the pipeline's identity /
    correspondence handle — its ``pipelines.<name>`` deployment reference — and is **never
    hashed** (identity, not structure: renaming is hash-neutral).

    The block's key set is **closed to ``{name}``** (pipeline/reference.md § ``meta`` —
    pipeline self-name): a top-level pipeline carries no declaration-level ``description``.
    Author prose about a pipeline lives in a TOML comment (the pipeline grammar declares no
    ``[annotations]`` block); a schema field's ``description`` is admitted only on a trainable
    composition node's ``trainable.output_schema`` (handler/reference.md § TOML field type
    discipline).

    Mirrors ``CompositionMeta`` minus ``kind``: a top-level pipeline has no composition-kind
    variant (``kind`` discriminates a ``kind = "composition"`` *embed*, not the outer
    pipeline), so a ``PipelineMeta`` carries no ``kind`` field.
    """

    name: str  # the pipeline's identity / `pipelines.<name>` deployment reference (never hashed)


class HandlerNode(IRModel):
    """A ``kind = "handler"`` pipeline node — a bare-function handler reference plus its
    supplied binding values and its authored (optional, per-port) wiring maps.
    """

    kind: Literal["handler"] = "handler"
    name: str  # qualified handler name resolved at compose
    bindings: tuple[NodeBindingValue, ...] = ()  # supplied binding values (inline or file path)
    reads_map: Mapping[str, str] = {}  # authored read-map: input-port -> channel (per-port, optional)
    writes_map: Mapping[str, str] = {}  # authored write-map: output-port -> channel (per-port, optional)


class CompositionNode(IRModel):
    """A ``kind = "composition"`` pipeline node — an embed of a composition declaration
    by path. Carries no bindings or wiring maps (the outer composition node's boundary
    wiring is the flatten mechanism); their absence is structural.
    """

    kind: Literal["composition"] = "composition"
    name: str  # composition declaration path, e.g. "trainables/dialogue_generation.toml"


#: A pipeline node — a handler reference or a composition embed.
PipelineNode = Annotated[Union[HandlerNode, CompositionNode], Field(discriminator="kind")]


class PipelineDeclaration(IRModel):
    """A pipeline declaration: its self-name, ordered nodes, service-binding identity
    supplies, channel-write merges, and the optional ``inputs`` / ``outputs`` API boundary.
    """

    meta: PipelineMeta  # required self-name (the family rule); name is identity, never hashed
    nodes: tuple[PipelineNode, ...]  # source order is dispatch order
    service_bindings: tuple[ServiceBindingSupply, ...] = ()  # identity supplies
    #: ``merge.<channel>`` declarations — channel name -> strategy (R-pipeline-002).
    merge: Mapping[str, MergeStrategy] = {}
    inputs: tuple[FieldDecl, ...] = ()  # free-variable read-port channels; required where any exist (Phase 1a)
    #: ``outputs`` is *truly optional* — absence opts out of the output API commitment,
    #: categorically distinct from an empty-but-present declaration. ``None`` = absent.
    outputs: tuple[FieldDecl, ...] | None = None
