"""Shared IR models and closed registries used across more than one declaration class.

Housed here (rather than duplicated) so the declaration-class modules compose them
without import cycles:

- ``ServiceBindingDecl`` — a ``service_bindings`` entry a handler / hook / trainable
  *declares* (name + service-type qualified ``type``; closed to ``{type}``).
- ``ServiceBindingSupply`` — a pipeline-level ``service_bindings.<name>`` *identity
  supply* (name + ``type`` + the identity field values, which fold into the
  pipeline-hash).
- ``Binding`` — a ``bindings.<name>`` compose-time binding: either a declared schema
  (with a delivery selector) or a ``compile`` directive (a named compiler + its params).
- ``NodeBindingValue`` — a binding *value* a pipeline node (or a preprocessor entry)
  supplies: an inline value or an external-declaration file path.
- ``MergeStrategy`` / ``Delivery`` — closed engine registries; ``CompilePrimitive`` — the
  closed **blessed-bare-name** half of the extensible-first compile-affordance roster.
"""

from __future__ import annotations

import enum
from typing import Annotated, Literal, Mapping, Union

from pydantic import Field

from conjured.ir.base import NO_DEFAULT, IRModel
from conjured.ir.channel_types import FieldDecl


# ---------------------------------------------------------------------------
# Closed registries
# ---------------------------------------------------------------------------


class MergeStrategy(str, enum.Enum):
    """The closed registry of channel-write merge strategies (R-pipeline-002,
    ``conjured/docs/components/pipeline/reference.md`` § ``merge.<channel>``). Each
    carries a type constraint the validator checks against the merged channel's
    declared type at compose time; the constraint is validation (a later phase) — the
    enum here is the closed name set. Expansions go through an engine change.
    """

    LAST_WINS = "last_wins"
    FIRST_WINS = "first_wins"
    APPEND_LIST = "append_list"
    DEEP_MERGE_DICT = "deep_merge_dict"
    UNION_SET = "union_set"
    LAST_PRESENT_WINS = "last_present_wins"
    CONCAT_STR = "concat_str"


class Delivery(str, enum.Enum):
    """A ``bindings.<name>`` delivery selector
    (``conjured/docs/components/handler/reference.md`` § Reference bindings).
    ``COPY`` (the default) hands each dispatch a fresh per-dispatch copy (the vector-4
    seal); ``REFERENCE`` deep-freezes large static read-only data once and shares it.
    """

    COPY = "copy"
    REFERENCE = "reference"


class CompilePrimitive(str, enum.Enum):
    """The closed set of **blessed first-party** compiler bare names the ``compile =
    "..."`` binding directive may name without a namespace
    (``conjured/docs/components/handler/reference.md`` § The ``compile = "..."`` directive
    sub-form). It is the engine-shipped compile vocabulary — the bare-name space the engine
    reserves for itself, exactly as the bare validation keywords are the engine's standard
    set (``BUILTIN_VALIDATOR_NAMES``); a bare ``compile`` value MUST be one of these.

    The compile-affordance roster is **extensible-first**, not closed: this blessed set is
    the bare-name half; a **namespaced (dotted) name** resolves an open third-party compiler
    through the same dotted-path resolution + R-handler-pure-module audit as any foreign
    handler. The two name-spaces are disjoint by construction, so a third-party compiler can
    never shadow a blessed one. New first-party compilers are blessed into this set as they
    ship; the *interface* (a deterministic ``params → artifact`` callable) and the taxonomy
    are closed, the *membership* is not.
    """

    REGEX = "regex"
    JINJA = "jinja"
    JSON_SCHEMA = "json_schema"


# ---------------------------------------------------------------------------
# Service-binding entries
# ---------------------------------------------------------------------------


class ServiceBindingDecl(IRModel):
    """A ``service_bindings`` entry a handler / hook / trainable composition node
    *declares* — the node's external-call edge. ``type`` is a service-type qualified
    name resolved at compose. Cardinality per kind (service: exactly one; hook: 0 or 1;
    trainable: exactly one trainable backend) is validation (a later phase); this model
    carries one declared entry.

    The entry's key set is **closed to ``{type}``** — a service-binding declaration carries
    no prose ``description`` (the family rule: ``description`` is model-facing contract content
    admitted only on a trainable's ``trainable.output_schema`` fields; author prose about a
    binding lives in the declaration's ``[annotations]``). ``name`` is the section key, not an
    entry key.
    """

    name: str
    type: str  # service-type qualified name, e.g. "conjured_llm.structured_output"


class ServiceBindingSupply(IRModel):
    """A pipeline-level ``service_bindings.<name>`` identity supply — which service
    implementation satisfies which handler binding. ``identity`` carries the
    identity-field *values* (model selector, prompt-template, …), which fold into the
    pipeline-hash (``conjured/docs/components/service-type/reference.md`` § Hash
    placement). ``config`` carries the entry's **`config` block** — the bound
    service-type's ``[config_schema]`` generation-parameter values, the service-binding
    counterpart of the trainable kind's ``[trainable.config]`` under the same supply
    contract (pipeline/reference.md § ``service_bindings.<name>``; the supply rule is
    owned by service-type/reference.md § The ``[config_schema]`` contract — both
    directions checked at compose; the **effective** values fold in with the identity
    surface). Transport values live in the deployment declaration, never here.
    """

    name: str
    type: str  # service-type qualified name
    identity: Mapping[str, object] = {}  # identity field values (hashed)
    config: Mapping[str, object] = {}  # [config_schema] value supply (effective values hashed)


# ---------------------------------------------------------------------------
# Compose-time bindings (bindings.<name>) — declared schema or compile directive
# ---------------------------------------------------------------------------


# The no-default sentinel lives at the IR root (``ir/base.py``) — two declaration surfaces
# carry ship-time defaults (``SchemaBinding`` here; a ``[config_schema]`` field's
# ``FieldDecl`` in ``ir/channel_types.py``) and one sentinel serves both. Re-exported here
# because the binding surface is this module's.


class SchemaBinding(IRModel):
    """A ``bindings.<name>`` declaring a schema — the common case. Carries the binding's
    declared fields, its delivery selector, and an optional ship-time ``default``. The
    *value* is supplied at the pipeline node (inline or by external declaration file) as a
    ``NodeBindingValue``; when the node omits a default-bearing binding the engine supplies
    the declared ``default`` (handler/reference.md § Ship-time defaults).

    The ``default`` lives at the ``bindings.<name>`` level (a compose-time binding value),
    NEVER on a channel ``FieldDecl`` — channel fields forbid defaults by invariant I1 (an
    optional channel is a lying default), but a compose-time binding is not a channel.
    ``default`` is the ``NO_DEFAULT`` sentinel when none is declared (so a declared
    ``default = <none>`` is distinguishable from "no default")."""

    form: Literal["schema"] = "schema"
    fields: tuple[FieldDecl, ...]
    delivery: Delivery = Delivery.COPY
    #: The declared ship-time default value, or ``NO_DEFAULT`` when none is declared.
    default: object = NO_DEFAULT

    @property
    def has_default(self) -> bool:
        return self.default is not NO_DEFAULT


class CompileBinding(IRModel):
    """A ``bindings.<name>`` using the ``compile = "<compiler>"`` directive sub-form — the
    engine resolves the named compiler, runs it once at binding resolution, and delivers the
    produced artifact as the binding's engine-owned kwarg value (vector-4-copy-exempt).

    ``compiler`` is the name **as written**: a **bare** blessed first-party name (a
    :class:`CompilePrimitive` value — ``regex`` / ``jinja`` / ``json_schema``) or a
    **namespaced** dotted third-party compiler (``mypkg.compile_grammar``). The
    ``"." in compiler`` split is the bare-vs-namespaced selector, mirroring field-validator
    resolution; the disjoint name-spaces forbid a third-party compiler shadowing a blessed one.

    ``params`` are the directive's declared parameters (the binding's sibling keys — e.g.
    ``pattern`` / ``flags`` for ``regex``), carried opaquely here; the engine binds them at
    compose (no author factory or closure) and produces the artifact in the stage-4 resolution
    pass (``runner.assemble``; ``validator.resolve_compile`` owns the resolution).

    A parameter's value MAY be the engine's external-file form ``<param> = { file = "<path>" }`` —
    the SAME ``{ file }`` shape a binding value uses, carried here as a :class:`FilePathBindingValue`
    (reused, not paralleled). The binding-resolution pass
    (``validator.resolve.resolve_compile_param_files``) reads the file as **raw text** and stamps it;
    ``resolve_compile`` passes that text to the compiler as ``<param>``, and the hasher folds the
    text (handler/reference.md § The ``compile = "..."`` directive sub-form). A param has one value,
    so it is inline or file-supplied by construction — no twin key, no ``_file`` suffix.
    """

    form: Literal["compile"] = "compile"
    compiler: str
    params: Mapping[str, object] = {}


#: A binding body — a declared schema or a compile directive.
BindingBody = Annotated[Union[SchemaBinding, CompileBinding], Field(discriminator="form")]


class Binding(IRModel):
    """A single ``bindings.<name>`` compose-time binding: a name plus its body."""

    name: str
    body: BindingBody


# ---------------------------------------------------------------------------
# Pipeline-node binding values
# ---------------------------------------------------------------------------


class InlineBindingValue(IRModel):
    """A binding value supplied inline at a pipeline node (a scalar or object)."""

    source: Literal["inline"] = "inline"
    name: str
    value: object  # the supplied inline scalar / object


class FilePathBindingValue(IRModel):
    """A binding value supplied by external declaration file (the ``{ file = "..." }`` form)
    at a pipeline node or composition preprocessor. The referenced file's **canonicalized
    content** folds into the binding's value contribution to the pipeline-hash /
    training-bundle-hash (``conjured/docs/architecture/hash-model.md`` § External binding-value
    declaration content) — so "inline X" and "an external file containing X" hash identically;
    the path is NOT hashed (the stamped ``content_hash`` is the manifest/event-layer convenience,
    never the fold).

    The file is read + parsed + canonicalized + hashed by the **stage-1 resolution pass**
    (``validator.resolve``: I/O at compose, never at dispatch), which **stamps** ``content_hash``
    (the ``sha256:<hex>`` of the canonicalized content) and ``resolved`` (the canonicalized
    value) onto a fresh IR instance. An unresolved instance carries ``content_hash = None``;
    the hasher requires a resolved instance (fail loud, never hash a path).

    **Dual resolution semantics — the one branch.** This same IR shape is reused for a
    file-supplied **compile parameter** (``CompileBinding.params``). There the
    ``resolve_compile_param_files`` pass reads the file as **raw text** and stamps ``resolved`` =
    that text (``content_hash`` = the ``sha256`` of the text); it does NOT parse / canonicalize.
    That is the *only* difference between the two external-file branches — a binding value keeps
    canonicalized content (hash-neutral: inline ≡ file), a compile param keeps raw text (hash-
    distinct: inline and file are different declarations, since the engine never parses compiler
    content — handler/reference.md § The ``compile = "..."`` directive sub-form)."""

    source: Literal["file"] = "file"
    name: str
    path: str  # external declaration file path, e.g. "npcs/captain_blackwell.toml"
    #: Stamped by the stage-1 resolution pass: the canonicalized content hash (``sha256:<hex>``)
    #: and the canonicalized resolved value. ``None`` until resolved.
    content_hash: str | None = None
    resolved: object = None


#: A binding value a pipeline node (or a preprocessor entry) supplies.
NodeBindingValue = Annotated[
    Union[InlineBindingValue, FilePathBindingValue], Field(discriminator="source")
]


for _model in (SchemaBinding, CompileBinding, Binding):
    _model.model_rebuild()
del _model
