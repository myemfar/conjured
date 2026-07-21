"""The shared compose-time normalizations — the per-port map desugar and the
single-field binding-value normalization.

Factored out so the
**compiler** (`validator/compile.py`) and the **hasher** (`conjured.hasher`) normalize a
node's authored wiring maps *identically*. That identity is load-bearing: the hash runs
over "the NORMALIZED, always-explicit map IR" (`architecture/hash-model.md` § What the
pipeline-hash absorbs), so the normalized maps the hasher canonicalizes MUST equal the
ones the compiler validated — otherwise the hash would not describe the graph the engine
checked.

A node's authored `reads_map` / `writes_map` are **optional and per-port**: an unmapped
port desugars to a same-named channel (identity). This is the sugar the canon calls
hash-neutral — an empty author map and a written-out identity map produce the SAME
normalized map (`pipeline/reference.md` § `reads_map` / § Pipeline load lifecycle stage 2).
Sugar-neutrality is a property OF this desugar: it runs before canonicalization, so two
spellings collapse to one normalized IR before any hash sees them.

`normalize_binding_value` is the **supply-side counterpart** of that desugar: a
single-field binding has one canonical representation (the bare value), and every supply
spelling reduces to it here, before canonicalization — hash-neutral by the same
canonical-IR construction (`handler/reference.md` § Binding value-supply grammar, the
normalization region).

This module is pure data→data over the IR with ONE carve-out: the reserved
**explicit-null form** ``{ null = true }`` resolves here, at the same join — recognition,
its forced-spelling check, and its nullable-only admission are normalization semantics
(``handler/reference.md`` § Binding value-supply grammar, the ``explicit-null`` region:
"normalizes to the null value at the compose join — the same join the single-field routes
reduce through"), so the classifier (:func:`is_explicit_null`) and the join both live here
— one classifier, one normalization, no position-local variants. Everything else stays
non-validating (the caller checks authored keys against declared ports —
`WIRING_MAP_PORT`) and unscoped (channel scoping for a flattened composition is the
compiler's dispatch concern, applied *after* this step; the TBH deliberately does NOT
scope — see the hasher).
"""

from __future__ import annotations

from typing import Iterable, Mapping

from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import FieldDecl, OptionalType


def is_explicit_null(
    value: object, *, owner: str, file_path: str | None = None,
    section_path: str = "", composition_ref: str | None = None,
    rule_id: str = "R-pipeline-001",
) -> bool:
    """THE single ``{ null = true }`` explicit-null classifier — the ``{ file }`` sibling
    (``handler/reference.md`` § Binding value-supply grammar, the ``explicit-null`` region),
    shared by every engine-read TOML value position that feeds a declared field. Returns
    ``True`` when ``value`` IS the reserved form, ``False`` for ordinary values.

    ``null`` is an **engine-read key** under the same reserved-key rule as ``file``: a table
    carrying it is never an inline object with a literal ``null`` field, so any other
    spelling — ``{ null = false }``, a non-boolean value, an additional key — is malformed
    and fails loud here (``MALFORMED_DECLARATION``, the same split the ``{ file }``
    classifier uses). Whether the form is ADMITTED — the target field is nullable-declared —
    is the caller's compose-time concern (``Check.EXPLICIT_NULL_TARGET``); this classifier
    owns spelling only. Recognition is position-level, never recursive (canon: a collection
    member is not a field position; an opaque table's interior is data)."""
    if not (isinstance(value, Mapping) and "null" in value):
        return False
    # guarantees: explicit-null-forced-spelling
    if set(value) != {"null"} or value["null"] is not True:
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id=rule_id,
            expected=f"'{owner}' explicit-null form is exactly {{ null = true }}",
            actual=f"got {value!r}", file_path=file_path, section_path=section_path,
            composition_ref=composition_ref,
            remediation_hint="`null` is the engine-read explicit-null key; the only spelling "
                             "is { null = true } (a present value already spells not-null; "
                             "there is no { null = false })",
        )
    return True


def _field_is_nullable(field: FieldDecl) -> bool:
    return isinstance(field.type, OptionalType)


def _reject_explicit_null(
    owner: str, *, reason: str, file_path: str | None, section_path: str,
    composition_ref: str | None = None,
) -> ContractViolation:
    return ContractViolation(
        check=Check.EXPLICIT_NULL_TARGET, rule_id="R-pipeline-001",
        expected=f"{{ null = true }} for '{owner}' targets a nullable-declared field "
                 "(the '<T> | None' union / the `nullable` shorthand)",
        actual=reason, file_path=file_path, section_path=section_path,
        composition_ref=composition_ref,
        remediation_hint="supply a concrete value, or declare the target field nullable "
                         "if considered-and-null is a real state for it",
    )


def desugar_map(authored: Mapping[str, str], ports: Iterable[str]) -> dict[str, str]:
    """Return the total, normalized, always-explicit wiring map for ``ports``: each
    declared port maps to its authored channel, or — if unmapped — to its same-named
    channel (identity desugar). Sugar-neutral by construction (an empty ``authored`` and a
    written-out identity map yield the same result).

    The caller is responsible for having validated that ``authored``'s keys are a subset of
    ``ports`` (the compiler's ``WIRING_MAP_PORT`` check); keys outside ``ports`` are ignored
    here, since this is the post-validation normalization, not the validation.
    """
    return {port: authored.get(port, port) for port in ports}


def normalize_binding_value(
    fields: tuple[FieldDecl, ...], value: object, *, owner: str,
    file_path: str | None = None, section_path: str = "",
    composition_ref: str | None = None,
) -> object:
    """Reduce a supplied (or default) ``bindings.<name>`` value to its **canonical form**.

    A **single-field binding** (declared schema of exactly one field, of any field type)
    has one canonical representation: the **bare value** of that one field. Its supply
    routes — a bare scalar/array, a one-field inline table keyed by the field name, an
    external ``{ file = "..." }`` declaration (inherently a TOML table), or a one-field
    ship-time default — all reduce **here, at the compose join**, to that bare value, so
    every spelling of one logical value produces **one pipeline-hash and one delivered
    shape** (``handler/reference.md`` § Binding value-supply grammar, the normalization
    region). A **multi-field binding** is already canonical (the plain field-keyed ``dict``)
    and passes through unchanged.

    This is the supply-side counterpart of :func:`desugar_map`: like the identity-map
    desugar it runs **before** canonicalization, so the differing spellings collapse to one
    normalized IR and the normalization is hash-neutral by the same canonical-IR
    construction. It is single-sourced — every consumer (the hasher's supply-site + default
    folds; assemble's binding resolution and validation) calls THIS one helper. Two
    independently-implemented normalizations that could drift is the defect this closes.

    The keyed-table routes (inline one-field table, external file, one-field default)
    present as a ``Mapping`` whose sole key is the field name; an already-bare scalar/array
    is not a ``Mapping`` (the bare-value route is a scalar or array — canon). A single-field
    *nested-object* value therefore always arrives keyed (there is no bare-object route), so
    a ``Mapping`` supply for a single-field binding is always the one-field wrapper and
    unwraps unambiguously. Normalization is **post-declaration** — parse cannot do it (no
    declaration in scope under the name-reference model); this helper is called only where
    the declaration's ``fields`` are in hand.

    **The explicit-null form resolves here** (``handler/reference.md`` § Binding
    value-supply grammar, the ``explicit-null`` region): at the **whole-binding position**
    ``{ null = true }`` is the bare null of a single-field binding's one nullable-declared
    field (a multi-field whole is never a nullable target → ``EXPLICIT_NULL_TARGET``); at a
    **top-level field position** (an inline object's / external file's / multi-field
    default's field value) it resolves to ``None`` iff that field is nullable-declared.
    Recognition is one level — never inside a composite value's interior (a nested object's
    sub-fields, a collection's members, an opaque table's keys are data). Every route — and
    every consumer (assemble's resolution + validation, the hasher's supply-site and
    default folds) — passes through THIS resolution, so the form folds into every hash as
    the null value and delivers as Python ``None``.
    """
    # Whole-binding position: reserved-key recognition BEFORE the one-field unwrap (the
    # reserved form wins over a literal one-field wrapper named `null` — the same
    # reserved-key rule as `{ file }`).
    # guarantees: explicit-null-nullable-only
    # guarantees: explicit-null-normalizes-at-join
    if is_explicit_null(
        value, owner=owner, file_path=file_path, section_path=section_path,
        composition_ref=composition_ref,
    ):
        if len(fields) == 1 and _field_is_nullable(fields[0]):
            return None  # the bare null value, spelled — the bare-value route
        reason = (
            "a whole multi-field binding is never a nullable-declared target"
            if len(fields) != 1
            else f"single field '{fields[0].name}' is not nullable-declared"
        )
        raise _reject_explicit_null(
            owner, reason=reason, file_path=file_path, section_path=section_path,
            composition_ref=composition_ref,
        )
    # Top-level field positions: resolve per-field explicit nulls (one level, never
    # recursive). Recognition fires only where the Mapping is FIELD-KEYED — a multi-field
    # binding's canonical dict, or the single-field one-field wrapper — and only for
    # DECLARED field keys: a bare composite value (e.g. a dict-typed single field supplied
    # unwrapped) is a value interior, not a field position, and an undeclared key's shape
    # fault is the schema validator's to report (BINDING_VALUE_SHAPE names it).
    if isinstance(value, Mapping) and (
        len(fields) != 1 or set(value) == {fields[0].name}
    ):
        by_name = {f.name: f for f in fields}
        resolved: dict[str, object] = {}
        for key, field_value in value.items():
            field = by_name.get(key)
            if field is not None and is_explicit_null(
                field_value, owner=f"{owner}.{key}", file_path=file_path,
                section_path=f"{section_path}.{key}" if section_path else "",
                composition_ref=composition_ref,
            ):
                if not _field_is_nullable(field):
                    raise _reject_explicit_null(
                        f"{owner}.{key}",
                        reason=f"field '{key}' is not nullable-declared",
                        file_path=file_path,
                        section_path=f"{section_path}.{key}" if section_path else "",
                        composition_ref=composition_ref,
                    )
                resolved[key] = None
            else:
                resolved[key] = field_value
        value = resolved
    if len(fields) != 1:
        return value  # multi-field: the plain field-keyed dict is already canonical
    (field,) = fields
    if isinstance(value, Mapping) and set(value) == {field.name}:
        return value[field.name]  # the one-field table / file / default → the bare value
    return value  # already the bare scalar / array
