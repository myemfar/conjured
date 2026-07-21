"""Channel-type system — the closed channel-field-type vocabulary, its normalized
dialect-agnostic descriptor, and the codegen-ready realization table.

**Deliverable 0b.** Two things live here:

1. ``ChannelFieldType`` — the normalized, recursive **type descriptor** that the IR
   uses to represent a declared field's type. It is dialect-agnostic by design: the
   same descriptor is produced whether a type was authored as the TOML token
   ``"list[float]"`` or as a future direct-Pydantic ``list[float]``. This is what
   makes the canonical-IR / 1×N-converter discipline work and what lets lexical
   reformatting be hash-neutral (``conjured/docs/explanation/overview.md`` § Pydantic
   as the canonical representation; ``conjured/docs/architecture/hash-model.md`` § How
   the hashes are constructed). A raw token *string* would be TOML-lexical and would
   break cross-dialect hash equivalence — hence a structured descriptor, not a string.

2. ``CHANNEL_TYPE_TABLE`` — the single codegen-ready table mapping each member of the
   closed allowed-type set to its Pydantic-field realization. A later model generator
   (Phase 1a) codegens declared types into Pydantic models from this table; Phase 0
   ships the table as data, not the generator (no parsing, no model generation here).

**The closed allowed-type set** is owned by
``conjured/docs/components/handler/reference.md`` § Types allowed in ``reads`` and
``output_schema`` (the TOML token grammar) and § Channel-type discipline (the engine
Pydantic-IR posture). The set is closed + exhaustive:

    str · int · float · bool                         (primitives)
    list[<T>] · dict[str, <T>] · tuple[<T>, <U>, …]   (collections)
    <T> | None  (≡ nullable shorthand)               (optionality)
    Literal['a', 'b', …]                             (closed-enum values)
    nested object via a [<sec>.<field>.fields] sub-table   (recurses to any depth)

``bytes`` is admitted by the engine's Pydantic IR (§ Channel-type discipline) but has
**no TOML token** — it is reachable only from a future direct-Pydantic dialect. It is
recorded in the table as a Pydantic-native, **non-LCD** primitive (a documented
cross-dialect capability boundary — hash-model § Cross-dialect portability), and the
descriptor can represent it, but it is not part of the TOML token grammar.
"""

from __future__ import annotations

import enum
from typing import Annotated, Literal, Mapping, Union

from pydantic import Field

from conjured.ir.base import NO_DEFAULT, IRModel

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


class Primitive(str, enum.Enum):
    """The closed primitive scalar set.

    ``STR`` / ``INT`` / ``FLOAT`` / ``BOOL`` are the four LCD primitives expressible
    in every dialect. ``BYTES`` is engine-IR-admissible but **not** a TOML token —
    see the module docstring and ``CHANNEL_TYPE_TABLE``.
    """

    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    BYTES = "bytes"  # non-LCD: direct-Pydantic only, no TOML token


#: Each primitive's concrete Python/Pydantic realization. The scalar half of the
#: realization table (the composite forms compose these — see ``CHANNEL_TYPE_TABLE``).
PRIMITIVE_REALIZATION: dict[Primitive, type] = {
    Primitive.STR: str,
    Primitive.INT: int,
    Primitive.FLOAT: float,
    Primitive.BOOL: bool,
    Primitive.BYTES: bytes,
}

#: Literal-enum member values. Canon shows string enums; Pydantic ``Literal`` also
#: admits int/bool scalars, so the descriptor carries the JSON/TOML-scalar union.
#: ``None`` is never a literal member — nullability is the ``OptionalType`` wrapper.
LiteralValue = Union[str, int, bool]


# ---------------------------------------------------------------------------
# The normalized type descriptor (a closed, recursive, discriminated union)
# ---------------------------------------------------------------------------
#
# Each variant carries a ``kind`` discriminator so the union is closed and
# self-describing. Optionality is represented exactly one way (the ``OptionalType``
# wrapper); the surface ``nullable = true`` shorthand normalizes into it at parse
# time (Phase 1a) so two spellings of the same type produce one descriptor — the
# sugar-neutrality the hash model relies on.


class PrimitiveType(IRModel):
    """A primitive scalar — ``str`` / ``int`` / ``float`` / ``bool`` / ``bytes``."""

    kind: Literal["primitive"] = "primitive"
    primitive: Primitive


class ListType(IRModel):
    """``list[<T>]`` — a homogeneous list of one inner declared type."""

    kind: Literal["list"] = "list"
    item: "ChannelFieldType"


class DictType(IRModel):
    """``dict[str, <T>]`` — string keys (fixed by the grammar), one inner value type."""

    kind: Literal["dict"] = "dict"
    value: "ChannelFieldType"


class TupleType(IRModel):
    """``tuple[<T>, <U>, …]`` — a fixed-arity, heterogeneous tuple of declared types.

    Arity is fixed by how many element types are listed; the Python-``Ellipsis``
    variadic form has no token in the grammar.
    """

    kind: Literal["tuple"] = "tuple"
    #: One or more element types, order significant. ``min_length=1`` makes the empty
    #: ``tuple[]`` unrepresentable by construction — a zero-arity tuple is unrealizable
    #: (handler/reference.md § Types allowed: a tuple is N declared element types).
    items: tuple["ChannelFieldType", ...] = Field(min_length=1)


class OptionalType(IRModel):
    """``<T> | None`` — the value may be ``None`` (a separate axis from key-presence:
    a missing key is always a violation regardless). ``inner`` is never itself an
    ``OptionalType`` in normalized form.
    """

    kind: Literal["optional"] = "optional"
    inner: "ChannelFieldType"


class LiteralType(IRModel):
    """``Literal['a', 'b', …]`` — a closed-enum of scalar values."""

    kind: Literal["literal"] = "literal"
    #: One or more enum members, order significant. ``min_length=1`` makes the empty
    #: ``Literal[]`` unrepresentable by construction — a closed enum with no members is
    #: unrealizable (handler/reference.md § Types allowed: Literal = closed-enum values).
    values: tuple[LiteralValue, ...] = Field(min_length=1)


class NestedType(IRModel):
    """A nested object — a ``[<sec>.<field>.fields]`` sub-table. Recurses to any depth
    (a nested field may itself be a ``NestedType``). There is no ``object`` token;
    the presence of sub-fields is what marks a nested object.
    """

    kind: Literal["nested"] = "nested"
    fields: tuple["FieldDecl", ...]  # one or more member field declarations, order significant


class TableType(IRModel):
    """An **open, string-keyed table of JSON-expressible values** — the ``table`` token
    (service-type/reference.md § Schema-field vocabulary). Engine-opaque data: the engine
    validates shape only (a mapping with string keys), admits no constraint or extension
    keywords on it, and folds the supplied value into the hash as canonical data. Carries
    **no inner type** — unlike ``DictType`` (one concrete value type), a ``table`` admits
    heterogeneous JSON-expressible values (strings, ints, floats, bools, and
    arrays/tables of these, recursively). Its JSON Schema image is the unconstrained open
    object. **Admissible only in a service-type ``[config_schema]``** (the parser threads
    ``allow_table`` there alone); the shipped use is the trainable members' ``extras``
    table. Deliberately absent from ``CHANNEL_TYPE_TABLE`` — it is a config-only type, not
    a ``reads`` / ``output_schema`` channel-field type, so it never reaches the model
    generator or the type-hasher (config values fold as DATA, not as types)."""

    kind: Literal["table"] = "table"


class SecretRefType(IRModel):
    """A **secret reference** — the ``secret_ref`` token (deployment/reference.md
    § Secret references). The field's deployment value is a ``[scheme]payload`` reference
    to where the consuming implementation fetches a credential at dispatch, never the
    credential itself; the engine validates the reference's shape at pipeline-declaration
    load (``adapters/secret_refs.py`` owns the grammar) and forwards it opaque — **the
    engine never fetches**. **Admissible only as a ``[transport_schema]`` field's top-level
    type** (the parser threads ``allow_secret_ref`` there alone — top-level only, so a
    secret reference inside a collection value is unrepresentable, not detected). Composes
    with ``| None`` (the nullable transport union): ``{ null = true }`` is the
    unauthenticated no-credential state. Like ``table``, deliberately absent from
    ``CHANNEL_TYPE_TABLE`` — never a ``reads`` / ``output_schema`` channel-field type, so
    it never reaches the model generator or the type-hasher."""

    kind: Literal["secret_ref"] = "secret_ref"


#: The normalized channel-field type — a closed discriminated union over ``kind``.
ChannelFieldType = Annotated[
    Union[
        PrimitiveType,
        ListType,
        DictType,
        TupleType,
        OptionalType,
        LiteralType,
        NestedType,
        TableType,
        SecretRefType,
    ],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# A declared field
# ---------------------------------------------------------------------------


class ValidatorSpec(IRModel):
    """One validation keyword attached to a declared field beyond its type token
    (``conjured/docs/components/handler/reference.md`` § Validators). ``name`` is either a
    **bare** built-in standard constraint name (``minimum``, ``pattern``, ``enum``, and
    kin — ``validator/constraints.py``; ``name in BUILTIN_VALIDATOR_NAMES``) or a
    **namespaced (dotted)** third-party validator's qualified name (resolved at compose
    through the sibling resolution path, ``validator/resolve_validator.py``; R-handler-012).
    ``params`` carries the keyword's declared parameters — **data only** (scalar/collection
    values, never a callable or expression); they fold into the pipeline-hash as the
    field's validation configuration.

    The surface grammar normalizes here: a bare built-in key (``pattern = "^x"``) carries
    its value into the documented param name (``constraints.DIRECT_KEY_PARAM``); a dotted
    third-party key's value IS the params table (``{}`` for a parameterless validator) —
    so two spellings of the same keyword hash identically.
    """

    name: str
    params: Mapping[str, object] = {}


class FieldDecl(IRModel):
    """One declared field: a name, its normalized type, and the closed-enum field
    metadata.

    Used everywhere the engine declares typed fields — handler ``reads`` /
    ``output_schema``, service-type ``identity_schema`` / ``transport_schema`` /
    ``config_schema``, trainable ``reads`` / ``output_schema``, pipeline / composition
    ``inputs`` / ``outputs``, and the members of a ``NestedType``.

    The per-field metadata keys are a closed enum (``FIELD_METADATA_KEYS``):
    ``description``, ``nullable``. Every other non-structural field key is a **validation
    keyword** in one grammar (handler/reference.md § Validators): a **bare** key is a
    built-in standard constraint; a **namespaced (dotted)** key is a registered third-party
    validator (the value its params table). ``nullable`` is **not** a field on this model —
    it normalizes into an ``OptionalType`` on ``type`` (see the module docstring). There is
    **no** ``default``
    key on a declared *channel* field: a channel default would imply optional channel
    presence, forbidden by invariant I1 (handler/reference.md § Types allowed) — the
    ``default`` member below is admitted by the loader ONLY on a service-type's
    ``[config_schema]`` fields (the config-side ship-time-default surface,
    service-type/reference.md § ``[config_schema]``).
    """

    name: str
    type: ChannelFieldType
    #: Model-facing contract content, loader-admitted ONLY on a trainable composition node's
    #: ``trainable.output_schema`` fields on a wire family that delivers them — every other
    #: field position rejects it at load (handler/reference.md § TOML field-type discipline,
    #: rule ``description-admission``; the hash consequence at architecture/hash-model.md).
    description: str | None = None
    #: The field's validation keywords — **one grammar, one ordered tuple** in authored
    #: key order (handler/reference.md § Validators). Each :class:`ValidatorSpec` is either
    #: a **bare** built-in standard constraint (``spec.name in BUILTIN_VALIDATOR_NAMES`` —
    #: ``pattern = "^\\d{4}"``, ``minimum = 1``, normalized by the loader into the internal
    #: representation) or a **namespaced (dotted)** registered third-party validator (its
    #: value the params table). There is no separate ``validators`` list and no
    #: ``constraints`` split: the two classes interleave in declaration order, resolved,
    #: signature-checked, and wrapped into the generated model at compose in that order
    #: (``validator/resolve_validator.py`` + ``validator/model_gen.py``; R-handler-012).
    validators: tuple[ValidatorSpec, ...] = ()
    #: The declared ship-time default value, or ``NO_DEFAULT`` when none is declared —
    #: parse-admitted ONLY on a service-type's ``[config_schema]`` fields (the supply rule
    #: lives at service-type/reference.md § The ``[config_schema]`` contract; the value is
    #: the field's effective value where a composition omits the dial).
    default: object = NO_DEFAULT

    @property
    def has_default(self) -> bool:
        return self.default is not NO_DEFAULT


#: The closed enum of per-field metadata keys the declaration grammar admits (beyond the
#: built-in constraint keywords, which attach as direct field keys — the loader owns that
#: set, ``validator/constraints.py``). Membership here is grammatical admissibility, NOT
#: per-position legality: ``description`` is loader-admitted ONLY on a trainable composition
#: node's ``trainable.output_schema`` fields (see :class:`FieldDecl.description`); ``nullable``
#: is a surface key that normalizes into the ``OptionalType`` wrapper rather than being carried
#: on ``FieldDecl``, and its placement is itself constrained — forbidden on a service-type's
#: ``[identity_schema]`` / ``[config_schema]`` fields (the nullable-placement check,
#: ``parse.py``). (The sibling direct field key ``default`` — NOT a member of this tuple — is
#: likewise position-restricted: admitted ONLY on a service-type's ``[config_schema]`` fields;
#: channel fields forbid it — I1.)
FIELD_METADATA_KEYS: tuple[str, ...] = ("description", "nullable")


# ---------------------------------------------------------------------------
# The codegen-ready realization table
# ---------------------------------------------------------------------------


class ChannelTypeRow(IRModel):
    """One row of the channel-type → Pydantic-field realization table."""

    #: The TOML token-grammar form (``<T>`` / ``<U>`` are inner declared types).
    token: str
    #: The ``ChannelFieldType`` descriptor ``kind`` this token maps to.
    kind: str
    #: How the type realizes as a Pydantic-field annotation. ``realize(X)`` denotes the
    #: recursive realization of an inner descriptor ``X``.
    pydantic_realization: str
    #: In the lowest-common-denominator subset portable across all dialects with hash
    #: equivalence? ``False`` only for ``bytes`` (direct-Pydantic only).
    lcd: bool
    notes: str


#: The single codegen-ready table mapping each member of the closed allowed-type set
#: to its Pydantic-field realization. A model generator (Phase 1a) reads ``kind`` +
#: ``pydantic_realization`` to emit fields; Phase 0 ships only this data.
CHANNEL_TYPE_TABLE: tuple[ChannelTypeRow, ...] = (
    ChannelTypeRow(
        token='"str"',
        kind="primitive",
        pydantic_realization="str",
        lcd=True,
        notes="Primitive scalar.",
    ),
    ChannelTypeRow(
        token='"int"',
        kind="primitive",
        pydantic_realization="int",
        lcd=True,
        notes="Primitive scalar.",
    ),
    ChannelTypeRow(
        token='"float"',
        kind="primitive",
        pydantic_realization="float",
        lcd=True,
        notes="Primitive scalar.",
    ),
    ChannelTypeRow(
        token='"bool"',
        kind="primitive",
        pydantic_realization="bool",
        lcd=True,
        notes="Primitive scalar.",
    ),
    ChannelTypeRow(
        token="(no TOML token)",
        kind="primitive",
        pydantic_realization="bytes",
        lcd=False,
        notes=(
            "Engine-IR-admissible but has no TOML token; reachable only from a future "
            "direct-Pydantic dialect. Documented cross-dialect capability boundary "
            "(hash-model § Cross-dialect portability), not part of the TOML token grammar."
        ),
    ),
    ChannelTypeRow(
        token='"list[<T>]"',
        kind="list",
        pydantic_realization="list[realize(T)]",
        lcd=True,
        notes="Homogeneous list of one inner declared type.",
    ),
    ChannelTypeRow(
        token='"dict[str, <T>]"',
        kind="dict",
        pydantic_realization="dict[str, realize(T)]",
        lcd=True,
        notes="String keys fixed by the grammar; one inner value type.",
    ),
    ChannelTypeRow(
        token='"tuple[<T>, <U>, …]"',
        kind="tuple",
        pydantic_realization="tuple[realize(T), realize(U), …]",
        lcd=True,
        notes="Fixed-arity heterogeneous tuple; arity fixed by the listed element types.",
    ),
    ChannelTypeRow(
        token='"<T> | None"   (≡ nullable = true)',
        kind="optional",
        pydantic_realization="Optional[realize(T)]   (required field, no default)",
        lcd=True,
        notes=(
            "Value-may-be-None, a separate axis from key-presence (a missing key is "
            "always a ContractViolation). The `nullable = true` shorthand normalizes "
            "to this same wrapper — one descriptor for both spellings."
        ),
    ),
    ChannelTypeRow(
        token="\"Literal['a', 'b', …]\"",
        kind="literal",
        pydantic_realization="Literal['a', 'b', …]",
        lcd=True,
        notes="Closed-enum scalar values; out-of-set values fail validation (later phase).",
    ),
    ChannelTypeRow(
        token="[<sec>.<field>.fields] sub-table",
        kind="nested",
        pydantic_realization="a generated nested BaseModel",
        lcd=True,
        notes=(
            "No `object` token — the presence of a `.fields` sub-table marks a nested "
            "object; recurses to any depth. The nested BaseModel is generated by the "
            "Phase-1a model generator, not here."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Declaration-canonical token rendering
# ---------------------------------------------------------------------------


def canonical_token(field_type: "ChannelFieldType") -> str:
    """Render a descriptor in the declaration-canonical token form — the same form the
    handler declares its channels in (``CHANNEL_TYPE_TABLE``'s ``token`` column). Used
    by diagnostics that must show declared types as the author wrote them
    (``SchemaValidationError.field_validations[].expected_type`` — error-channel
    /reference.md § SchemaValidationError payload: "not the Pydantic class name").

    A nested object has **no type token** in the grammar (the ``.fields`` sub-table is
    its marker), so it renders as the descriptive ``"nested object"``.
    """
    if isinstance(field_type, PrimitiveType):
        return field_type.primitive.value
    if isinstance(field_type, ListType):
        return f"list[{canonical_token(field_type.item)}]"
    if isinstance(field_type, DictType):
        return f"dict[str, {canonical_token(field_type.value)}]"
    if isinstance(field_type, TupleType):
        return f"tuple[{', '.join(canonical_token(item) for item in field_type.items)}]"
    if isinstance(field_type, OptionalType):
        return f"{canonical_token(field_type.inner)} | None"
    if isinstance(field_type, LiteralType):
        return f"Literal[{', '.join(repr(v) for v in field_type.values)}]"
    if isinstance(field_type, NestedType):
        return "nested object"
    if isinstance(field_type, TableType):
        return "table"
    if isinstance(field_type, SecretRefType):
        return "secret_ref"
    raise TypeError(  # unreachable through the closed union; drift guard
        f"no canonical token for {type(field_type).__name__}"
    )


def first_non_json_expressible(value: object) -> str | None:
    """The ``table`` token's JSON-expressibility check (service-type/reference.md
    § Schema-field vocabulary): a ``table`` field's value admits **strings, integers,
    floats, booleans, and arrays/tables of these, recursively**. Returns the type name of
    the first non-JSON-expressible leaf (a TOML ``datetime`` / ``date`` / ``time``, or a
    non-string mapping key, or any other non-JSON type) so the caller can raise a
    ``ContractViolation`` naming it; returns ``None`` when every leaf is JSON-expressible.
    ``bool`` is JSON-expressible (checked before ``int`` — ``bool`` is an ``int``
    subclass)."""
    if isinstance(value, bool) or isinstance(value, (str, int, float)):
        return None
    if isinstance(value, Mapping):
        for key, inner in value.items():
            if not isinstance(key, str):
                return type(key).__name__
            bad = first_non_json_expressible(inner)
            if bad is not None:
                return bad
        return None
    if isinstance(value, (list, tuple)):
        for inner in value:
            bad = first_non_json_expressible(inner)
            if bad is not None:
                return bad
        return None
    return type(value).__name__


# ---------------------------------------------------------------------------
# Convenience constructors (data builders — not parsers)
# ---------------------------------------------------------------------------
#
# Ergonomic builders for hand-constructing descriptors (used by the Phase-0
# instantiation smoke-check and by any later code building IR directly). These are
# pure constructors over the descriptor models — there is no token-string parser here
# (that is the Phase-1a TOML loader's job, out of Phase 0 scope).


def primitive(p: Primitive | str) -> PrimitiveType:
    """Build a primitive type from a ``Primitive`` or its token string."""
    return PrimitiveType(primitive=Primitive(p))


def list_of(item: "ChannelFieldType") -> ListType:
    """Build ``list[<item>]``."""
    return ListType(item=item)


def dict_of(value: "ChannelFieldType") -> DictType:
    """Build ``dict[str, <value>]``."""
    return DictType(value=value)


def tuple_of(*items: "ChannelFieldType") -> TupleType:
    """Build ``tuple[<items...>]`` (fixed-arity, heterogeneous)."""
    return TupleType(items=tuple(items))


def optional(inner: "ChannelFieldType") -> OptionalType:
    """Build ``<inner> | None``."""
    return OptionalType(inner=inner)


def literal(*values: LiteralValue) -> LiteralType:
    """Build ``Literal[<values...>]``."""
    return LiteralType(values=tuple(values))


def nested(*fields: "FieldDecl") -> NestedType:
    """Build a nested object from its member ``FieldDecl``s."""
    return NestedType(fields=tuple(fields))


# Resolve the recursive forward references now that every model + the union alias and
# ``FieldDecl`` are in the module namespace.
for _model in (ListType, DictType, TupleType, OptionalType, NestedType, FieldDecl):
    _model.model_rebuild()
del _model
