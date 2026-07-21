"""``FieldDecl`` → Pydantic model generator — the dispatch-validation substrate.

The compose-time path "generates a Pydantic model per declared ``reads`` and
``output_schema``" (``conjured/docs/components/handler/reference.md`` R-handler-001;
``components/pipeline/reference.md`` § Pipeline load lifecycle stage 4). This module is
that generator: a recursive ``ChannelFieldType`` → Pydantic-annotation realization driven
by the closed allowed-type set ``CHANNEL_TYPE_TABLE`` codifies (``ir/channel_types.py``;
the token grammar is owned by handler/reference.md § Types allowed in ``reads`` and
``output_schema``). **One** generator serves **both** validation boundaries — the
pre-call reads validation and the post-call output validation are the same mechanism
against models built here (one problem, one solution; the boundaries differ only in
which schema they were built from).

Realization per descriptor ``kind`` (mirrors the table's ``pydantic_realization``
column):

- ``primitive`` → the scalar from ``PRIMITIVE_REALIZATION`` (``str``/``int``/``float``/
  ``bool``/``bytes``).
- ``list`` / ``dict`` / ``tuple`` → ``list[realize(T)]`` / ``dict[str, realize(T)]`` /
  ``tuple[realize(T), realize(U), …]`` (fixed arity — no variadic form in the grammar).
- ``optional`` → ``realize(T) | None`` as a **required field with no default** —
  value-nullability is a separate axis from key-presence (invariant I1: a missing key is
  always a violation regardless of nullability).
- ``literal`` → ``Literal[…]`` over the declared scalar members.
- ``nested`` → a recursively generated nested ``BaseModel``.

Generated models are **closed and strict**: ``extra="forbid"`` makes an undeclared key
structurally catchable, and ``strict=True`` disables lax coercion — a declared ``int``
field receiving a ``str`` is a value-level failure (error-channel/reference.md § The
closed enum of error classes names exactly that example as ``SchemaValidationError``),
never a silent parse. A silently coerced value would be a masked type infidelity in the
training projection (fail loud, log deep).

Models are built **once at wrapper construction** (R-handler-001 / pipeline lifecycle
stage 4 — compose-time work), never per dispatch; the dispatch kernel
(``conjured/runner/dispatch.py``) holds and reuses them.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, create_model
from pydantic_core import PydanticCustomError

from conjured.ir.channel_types import (
    PRIMITIVE_REALIZATION,
    ChannelFieldType,
    DictType,
    FieldDecl,
    ListType,
    LiteralType,
    NestedType,
    OptionalType,
    PrimitiveType,
    TupleType,
)
from conjured.validator.constraints import BUILTIN_VALIDATOR_NAMES
from conjured.validator.resolve_validator import (
    check_enum_bound_coherence,
    check_enum_type_coherence,
    make_validator_shim,
    resolve_builtin_constraint,
    resolve_field_validator,
)

#: The generated-model config: closed shape (an undeclared key is structurally
#: catchable) + strict validation (no lax coercion — value-type fidelity).
_GENERATED_MODEL_CONFIG = ConfigDict(extra="forbid", strict=True, frozen=False)

#: The descriptor ``kind`` set this generator realizes. A test asserts this set equals
#: the ``kind`` set of ``CHANNEL_TYPE_TABLE`` (the exhaustiveness confirmation) so a
#: table extension cannot silently outrun the generator.
REALIZED_KINDS: frozenset[str] = frozenset(
    {"primitive", "list", "dict", "tuple", "optional", "literal", "nested"}
)


def _literal_annotation(values: tuple[object, ...]) -> object:
    """A ``Literal`` realization with **exact-type** membership. Bare Pydantic
    ``Literal`` matches by equality, under which ``True == 1`` — so ``Literal[1, 2]``
    would silently admit ``True`` and re-type it to ``1``, a masked type infidelity
    the strict posture forbids (canon names str / int / bool literal members as
    distinct scalars; an out-of-set value raises at validation —
    handler/reference.md § Types allowed). The before-validator rejects any input
    whose type is not exactly a member's type, with the same ``literal_error`` type
    a bare ``Literal`` produces (so the SVE constraint mapping stays ``"enum"``)."""

    def _exact_member(value: object) -> object:
        for member in values:
            if type(value) is type(member) and value == member:
                return value
        raise PydanticCustomError(
            "literal_error",
            "Input should be {expected}",
            {"expected": " or ".join(repr(member) for member in values)},
        )

    return Annotated[Literal[values], BeforeValidator(_exact_member)]


def realize_type(
    field_type: ChannelFieldType, *, model_name: str, schema_source: str | None = None,
    audit_enforcement: bool = False,
) -> object:
    """Realize one ``ChannelFieldType`` descriptor as a Pydantic field annotation.

    ``model_name`` seeds the generated class name of any nested model so diagnostics
    name their declaration locus. ``schema_source`` (the declaring artifact's path)
    threads through nested models so their fields' validators resolve with the right
    diagnostics locus. ``audit_enforcement`` (the deployment opt-in) threads through nested
    models so a nested field's third-party validator module gets the same audit-stamp
    freshness check. Raises ``TypeError`` on a descriptor outside the
    closed union — unreachable through the IR (the union is closed + discriminated);
    kept as the fail-loud guard for a table/generator drift a future kind would create.
    """
    if isinstance(field_type, PrimitiveType):
        return PRIMITIVE_REALIZATION[field_type.primitive]
    if isinstance(field_type, ListType):
        return list[realize_type(field_type.item, model_name=model_name, schema_source=schema_source, audit_enforcement=audit_enforcement)]  # type: ignore[misc]
    if isinstance(field_type, DictType):
        return dict[str, realize_type(field_type.value, model_name=model_name, schema_source=schema_source, audit_enforcement=audit_enforcement)]  # type: ignore[misc]
    if isinstance(field_type, TupleType):
        items = tuple(
            realize_type(item, model_name=model_name, schema_source=schema_source, audit_enforcement=audit_enforcement)
            for item in field_type.items
        )
        return tuple[items]  # type: ignore[valid-type]
    if isinstance(field_type, OptionalType):
        inner = realize_type(field_type.inner, model_name=model_name, schema_source=schema_source, audit_enforcement=audit_enforcement)
        return Union[inner, None]  # noqa: UP007 - built dynamically from a realized object
    if isinstance(field_type, LiteralType):
        return _literal_annotation(field_type.values)
    if isinstance(field_type, NestedType):
        return build_model(
            f"{model_name}__nested", field_type.fields, schema_source=schema_source,
            audit_enforcement=audit_enforcement,
        )
    raise TypeError(
        f"channel-field type descriptor kind not realized by the model generator: "
        f"{type(field_type).__name__} (the closed allowed-type set is owned by "
        f"handler/reference.md § Types allowed; extending it is an engine change)"
    )


def build_model(
    model_name: str,
    fields: tuple[FieldDecl, ...],
    *,
    schema_source: str | None = None,
    audit_enforcement: bool = False,
) -> type[BaseModel]:
    """Build the Pydantic model for one declared schema (``reads`` / ``output_schema`` /
    a binding schema / a ``NestedType``'s members).

    ``audit_enforcement`` (the deployment opt-in, threaded from stage-4 assembly) gates the
    step-3 audit-stamp freshness check on any **third-party validator module** a field
    resolves (a validator module is an in-scope module — handler/reference.md § Audit
    stamps). Built-in constraints resolve no module and are unaffected.

    Every declared field is **required with no default** — including ``optional``
    (``<T> | None``)-typed fields, whose nullability is value-level only (I1: a default
    on a declared channel field would imply optional key-presence). Field order follows
    declaration order, which is what makes ``SchemaValidationError.field_validations``
    declaration-order sorting derivable from the model.

    A field's declared constraint layers resolve, signature-check, and bind **here** —
    model construction is compose-time work (R-handler-012: it resolves, binds, and
    signature-checks at compose or the pipeline does not load) — and wrap into the
    field's annotation as ``AfterValidator`` verdict shims (running after the field's
    type validation; a constraint applies beyond the type token). D8 — one grammar: the
    field's single ``validators`` tuple holds bare standard constraints and namespaced
    (dotted) third-party validators interleaved in **authored key order across both
    classes**; each spec routes per class (a bare keyword through
    :func:`resolve_builtin_constraint`, which runs the keyword applicability check against
    the field's declared type plus the value well-formedness check; a dotted name through
    :func:`resolve_field_validator`) with no class precedence. ``schema_source`` is the
    declaring artifact's path, the compose-time diagnostics' locus; building a
    constraint-carrying schema without one is engine-internal misuse (a
    ``ContractViolation`` requires a location-bearing field) and raises ``ValueError``.
    """
    definitions: dict[str, object] = {}
    for decl in fields:
        annotation = realize_type(
            decl.type, model_name=f"{model_name}__{decl.name}", schema_source=schema_source,
            audit_enforcement=audit_enforcement,
        )
        if decl.validators:
            if schema_source is None:
                # Engine-internal misuse (the caller is the composer / test harness,
                # never author code) — same posture as the dispatch kernel's
                # reads-mapping TypeError.
                raise ValueError(
                    f"build_model: field '{decl.name}' declares validation keywords "
                    "but no schema_source was supplied — the compose-time diagnostics "
                    "need the declaring artifact's path"
                )
            # One grammar, one ordered tuple: each spec resolves per class — a bare
            # standard keyword through resolve_builtin_constraint (applicability + value
            # checks), a namespaced (dotted) third-party validator through
            # resolve_field_validator. Iterative Annotated nesting flattens to declaration
            # order and AfterValidator metadata runs left-to-right, so the field's
            # validation keywords execute in **authored key order across both classes**
            # (D8). Per-class execution order is pinned directly
            # (test_validators_run_in_declaration_order); the cross-class interleave is
            # covered transitively (parse authored-order + same-class execution + the
            # authored-order hash fold).
            for spec in decl.validators:
                if spec.name in BUILTIN_VALIDATOR_NAMES:
                    bound = resolve_builtin_constraint(
                        spec, field_type=decl.type, toml_path=schema_source
                    )
                else:
                    bound = resolve_field_validator(
                        spec, toml_path=schema_source, audit_enforcement=audit_enforcement
                    )
                annotation = Annotated[annotation, AfterValidator(make_validator_shim(bound))]
            # Field-level cross-spec coherence, AFTER the per-spec loop (so each keyword is
            # individually valid): every `enum` member must be admissible under the field's
            # declared TYPE (the type arm) and satisfy a co-declared minLength / maxLength
            # bound (the length arm) — the generalizations of enum-vs-Literal coherence.
            # Both make a seal-breaching declaration unrepresentable at compose, so the GBNF
            # enum-only rendering stays literal-equal (R-handler-005).
            check_enum_type_coherence(decl, toml_path=schema_source)
            check_enum_bound_coherence(decl, toml_path=schema_source)
        definitions[decl.name] = (annotation, ...)  # ... = required, no default
    return create_model(model_name, __config__=_GENERATED_MODEL_CONFIG, **definitions)  # type: ignore[call-overload]
