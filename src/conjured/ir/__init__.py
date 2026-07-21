"""``conjured.ir`` — the engine's canonical internal representation (the Pydantic IR).

The privileged form every authoring dialect resolves into via 1×N converters; the form
type-checking, hash construction, and dispatch-boundary validation all operate over —
never the TOML lexical form (``conjured/docs/explanation/overview.md`` § Pydantic as the
canonical representation; ``conjured/docs/architecture/hash-model.md``).

**Phase 0 (floor).** These are **data structures only** — no compile / validate / hash /
dispatch behavior (those are later phases that ground in these models). Two families:

- The **channel-type system** (``channel_types``) — the closed type vocabulary, the
  normalized ``ChannelFieldType`` descriptor, ``FieldDecl``, and the codegen-ready
  ``CHANNEL_TYPE_TABLE``.
- The IR of the five **engine-read declaration classes** plus the **compiled graph**:
  handler (``handler``), service-type (``service_type``), pipeline (``pipeline``),
  composition/trainable (``composition``), deployment (``deployment``), and the compiled
  typed-dataflow graph (``graph``). Shared building blocks (bindings, service-binding
  entries, closed registries) live in ``common``.
"""

from __future__ import annotations

from conjured.ir.base import NO_DEFAULT, IRModel
from conjured.ir.channel_types import (
    CHANNEL_TYPE_TABLE,
    FIELD_METADATA_KEYS,
    PRIMITIVE_REALIZATION,
    ChannelFieldType,
    ChannelTypeRow,
    DictType,
    FieldDecl,
    ListType,
    LiteralType,
    LiteralValue,
    NestedType,
    OptionalType,
    Primitive,
    PrimitiveType,
    TableType,
    TupleType,
    ValidatorSpec,
    dict_of,
    list_of,
    literal,
    nested,
    optional,
    primitive,
    tuple_of,
)
from conjured.ir.common import (
    Binding,
    BindingBody,
    CompileBinding,
    CompilePrimitive,
    Delivery,
    FilePathBindingValue,
    InlineBindingValue,
    MergeStrategy,
    NodeBindingValue,
    SchemaBinding,
    ServiceBindingDecl,
    ServiceBindingSupply,
)
from conjured.ir.composition import (
    BundleComposition,
    CompositionKind,
    CompositionMeta,
    PipelineComposition,
    PreprocessorEntry,
    TrainableComposition,
    TrainableNode,
)
from conjured.ir.substitute import substitute_bundle_nodes
from conjured.ir.deployment import (
    DeploymentDeclaration,
    HookTransportBlock,
    PipelineOverride,
    TrainingContract,
    TransportBlock,
)
from conjured.ir.graph import (
    Channel,
    CompiledGraph,
    GraphNode,
    GraphNodeKind,
    MergeOp,
    Port,
)
from conjured.ir.handler import (
    HandlerDeclaration,
    HookDeclaration,
    ServiceDeclaration,
    TransformDeclaration,
)
from conjured.ir.pipeline import (
    CompositionNode,
    HandlerNode,
    PipelineDeclaration,
    PipelineMeta,
    PipelineNode,
)
from conjured.ir.service_type import ServiceTypeDeclaration

# The imports above aggregate the whole IR for the ENGINE'S OWN internal use; the declared
# consumer surface below is only the opaque handle types the public compose-API signatures
# spell (pipeline/reference.md § In-process compose API): the parsed declaration records
# `loads`/`parse` return, and the compiled graph `compile_pipeline` returns. A consumer
# imports these for type annotation and passes them between the compose-API steps — it never
# constructs or introspects them; their fields are engine-internal.
__all__ = [
    "BundleComposition",
    "CompiledGraph",
    "DeploymentDeclaration",
    "HandlerDeclaration",
    "PipelineComposition",
    "PipelineDeclaration",
    "ServiceTypeDeclaration",
    "TrainableComposition",
]
