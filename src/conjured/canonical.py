"""Canonical serialization primitives for the two hashes.

Both hashes are ``SHA-256`` over a **canonicalized serialization of the engine's Pydantic
IR** — not the TOML lexical form (``architecture/hash-model.md`` § How the hashes are
constructed). The canonical form fixes three things so two authoring conventions producing
the same declared graph produce the same hash, and lexical re-formatting is hash-neutral:

1. **Key ordering** — every mapping is emitted with sorted keys (``json.dumps(sort_keys=True)``).
   TOML table key-order is non-semantic, so a schema / wiring-map / identity block hashes the
   same regardless of the order its keys were authored in.
2. **Type representation** — a declared type is emitted as its normalized ``ChannelFieldType``
   descriptor (the ``kind``-tagged form), already dialect-agnostic in the IR (e.g. the
   ``nullable = true`` sugar is already an ``OptionalType``). A raw token string would be
   TOML-lexical and break cross-dialect equivalence.
3. **Metadata expansion** — schemas are emitted as name-keyed maps of their full field bodies
   (type + ``validators``); the structured form, not the source text.

**What these primitives do and don't decide.** They render *included* IR fragments to a
deterministic JSON-able structure; *which* fragments are included vs excluded (the closed
exclusion set — ``annotations``, a composable unit's ``meta.name``, hook nodes, the
trainable node's own wiring maps) is the structure-builder's job in
:mod:`conjured.hasher.hashes`. **Ordering policy:** a tuple of named declarations (a *schema*
— originating from a TOML *table* of named fields, key-order non-semantic) is emitted as a
**name-keyed map** (so field reordering is hash-neutral); a genuine **array**
(``TupleType.items``, ``LiteralType.values``, and — at the structure layer — node /
preprocessor sequences) preserves order, because position is semantic there. The one
schema **exception** is a trainable's ``trainable.output_schema`` (:func:`canon_schema_ordered`),
where the declared field order IS the enforced emission order — and, recursively, its nested-object
members fold ordered too (a nested object is emitted in declared member order on the wire); the
``ordered`` flag threads that context through :func:`canon_type`.

Field ``validators`` ARE hashed (they constrain the accepted value space — structural).
Field ``description`` is **hashed where present** — it is model-facing contract content that
conditions a trainable backend's constrained generation, the same derivation as a trainable's
``output_schema`` field ORDER (``architecture/hash-model.md`` § What the pipeline-hash absorbs +
the family rule). Admission is position-gated at the grammar (only a trainable
``trainable.output_schema`` field carries one — ``validator/tokens.py``), so folding it
here-when-present is safe at every position: a non-trainable-output field can no longer carry the
key. ``meta.name`` / ``annotations`` / hook nodes / the trainable node's own wiring maps are the
exclusion set (applied by the structure-builders in :mod:`conjured.hasher.hashes`); a composable
unit's ``[meta]`` carries no ``description`` (the family rule closes it to ``name`` / ``kind, name``).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Any, Iterable, Mapping

from conjured.ir.channel_types import (
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
from conjured.ir.common import ServiceBindingDecl, ServiceBindingSupply

#: Prefix on every emitted hash — the wire shape canon shows (``sha256:<hex>``).
_HASH_PREFIX = "sha256"


def canonical_json(structure: Any) -> str:
    """THE canonical JSON text of ``structure`` — the engine's single canonical-JSON
    recipe, single-homed here (the hashes consume it below; the trainable wire's
    input-payload rendering imports it — one recipe, never re-derived per consumer).

    ``sort_keys`` normalizes every mapping's key order (the canonical-IR key-ordering
    property); compact separators + ``ensure_ascii=False`` fix a single stable byte
    form. ``structure`` must be built from JSON-native values only — a non-serializable
    value raises ``TypeError``, and a non-finite float (TOML 1.0 admits ``nan``/``inf``)
    raises ``ValueError`` via ``allow_nan=False`` — either way fail loud rather than
    serialize something lossy or emit ``NaN``/``Infinity``, which no strict RFC 8259
    parser accepts (the artifact must stay one valid JSON object).
    """
    return json.dumps(
        structure, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def sha256_of(structure: Any) -> str:
    """SHA-256 of :func:`canonical_json`'s serialization of ``structure`` →
    ``"sha256:<hex>"``. UTF-8 is the hash input; the canon helpers below guarantee a
    JSON-native structure (a non-serializable value raises ``TypeError`` in the
    serializer — fail loud rather than hash something lossy)."""
    digest = hashlib.sha256(canonical_json(structure).encode("utf-8")).hexdigest()
    return f"{_HASH_PREFIX}:{digest}"


# ---------------------------------------------------------------------------
# Channel-field types — the normalized descriptor, rendered structurally
# ---------------------------------------------------------------------------


# guarantees: nested-output-schema-order-is-semantic
def canon_type(t: ChannelFieldType, *, ordered: bool = False) -> dict[str, Any]:
    """Render a normalized ``ChannelFieldType`` descriptor. The ``kind`` discriminator is
    preserved; composite forms recurse. ``TupleType.items`` and ``LiteralType.values`` keep
    order (positional / enumerated — semantic).

    ``ordered`` threads the **order-semantic context** through composite forms so a ``NestedType``
    reached from a trainable ``output_schema`` root folds its member fields **order-sensitively**.
    A trainable's ``output_schema`` is folded ordered (:func:`canon_schema_ordered`, ``ordered=True``)
    because the bound wire form compiles the declared schema, in declared order, into the backend's
    decode constraint — and that holds **recursively**: a nested object's members are emitted in
    declared order too (``adapters/wire.py`` ``_object_schema`` preserves member order;
    ``adapters/gbnf.py`` renders the object rule's keys in that order — a sequential grammar), so
    nested member order IS enforced emission order exactly as the top-level field order is
    (``architecture/hash-model.md`` § Training-bundle-hash — "order is preserved exactly where
    order reaches the contract"). The SAME descriptor reached from a name-keyed read / non-trainable
    schema (``ordered=False``, the default) stays name-keyed — nothing consumes those members' order
    (a bare-function handler receives kwargs, a trainable's reads serialize key-sorted). ``ordered``
    propagates through every composite arm (list / dict / optional / tuple), so a nested object at
    ANY depth under an order-semantic root folds order-sensitively."""
    if isinstance(t, PrimitiveType):
        return {"kind": "primitive", "primitive": t.primitive.value}
    if isinstance(t, ListType):
        return {"kind": "list", "item": canon_type(t.item, ordered=ordered)}
    if isinstance(t, DictType):
        return {"kind": "dict", "value": canon_type(t.value, ordered=ordered)}
    if isinstance(t, TupleType):
        return {"kind": "tuple", "items": [canon_type(x, ordered=ordered) for x in t.items]}  # order significant
    if isinstance(t, OptionalType):
        return {"kind": "optional", "inner": canon_type(t.inner, ordered=ordered)}
    if isinstance(t, LiteralType):
        return {"kind": "literal", "values": [_canon_scalar(v) for v in t.values]}  # order preserved
    if isinstance(t, NestedType):
        # The order-semantic switch: inside a trainable output_schema (ordered=True) a nested
        # object's members reach the emission contract, so they fold as an ordered LIST; a
        # name-keyed context (the default) folds them as a name-keyed map (order-neutral).
        fields = canon_schema_ordered(t.fields) if ordered else canon_schema(t.fields)
        return {"kind": "nested", "fields": fields}
    raise TypeError(  # fail loud: a new ChannelFieldType variant must be handled explicitly
        f"canon_type: unhandled channel-field-type {type(t).__name__!r}"
    )


# guarantees: description-shifts-both-hashes
def canon_field(fd: FieldDecl, *, ordered: bool = False) -> dict[str, Any]:
    """A declared field's body (its name is the key in the enclosing schema map): the
    normalized type + the constraint layers (+ a declared ship-time ``default`` where one
    exists — a ``[config_schema]`` field) (+ a ``description`` where one exists).

    The field **`description` folds in where present** — model-facing contract content that
    conditions a trainable backend's constrained generation, the same derivation as a
    trainable's ``output_schema`` field ORDER (``architecture/hash-model.md`` § What the
    pipeline-hash absorbs + the family rule: "an input that changes what the backend generates
    is contract, not prose"). The grammar admits ``description`` ONLY on a trainable
    ``trainable.output_schema`` field (position-gated in ``validator/tokens.py``), so folding it
    here-when-present is safe at every position — a non-trainable-output field can no longer
    carry the key. The omit-when-absent shape (mirroring ``has_default``) keeps
    description-less declarations hashing exactly as before the reframe: a fold shift happens
    only where a described field exists. **The seal:** a description edit on a trainable output
    field shifts the training-bundle-hash AND the pipeline-hash (RED-on-removal —
    ``tests/hasher/test_hashes.py`` ``verifies: description-shifts-both-hashes``).

    A validation-keyword entry is its normalized ``ValidatorSpec`` — name + canonicalized
    parameters (handler/reference.md § Validators: validation keywords "fold into the
    pipeline-hash as the field's validation configuration, in authored order"). D8 — one
    grammar: the field's bare standard constraints and namespaced (dotted) third-party
    validators are one ordered tuple, folded into the ONE ``validators`` array in **authored
    key order across both classes**. Entry order is preserved (a declared sequence —
    execution order is semantic). An absent ``default`` omits the key (``NO_DEFAULT`` is not
    a value), so default-less fields hash exactly as before the surface existed.

    ``ordered`` threads to :func:`canon_type` so a nested-object field inside an order-semantic
    schema (a trainable's ``output_schema``, folded by :func:`canon_schema_ordered`) folds its
    members order-sensitively (see :func:`canon_type`); the ``ordered=False`` default preserves
    name-keyed folding for every other position."""
    out: dict[str, Any] = {
        "type": canon_type(fd.type, ordered=ordered),
        "validators": [
            {"name": v.name, "params": canon_value(v.params)}
            for v in fd.validators
        ],
    }
    if fd.description is not None:
        out["description"] = fd.description
    if fd.has_default:
        out["default"] = canon_value(fd.default)
    return out


def canon_schema(fields: Iterable[FieldDecl]) -> dict[str, Any]:
    """A schema — a tuple of named ``FieldDecl`` — as a **name-keyed map**. Field order is
    non-semantic (TOML-table origin), so keying by name + ``sort_keys`` at serialization makes
    field reordering hash-neutral. Field names are unique by grammar (a TOML table cannot
    repeat a key)."""
    return {fd.name: canon_field(fd) for fd in fields}


def canon_schema_ordered(fields: Iterable[FieldDecl]) -> list[dict[str, Any]]:
    """A schema whose **entry order reaches the contract** — an ordered LIST of
    name-carrying field bodies (a JSON array preserves order under ``sort_keys``; the
    validators' ordered precedent). The one consumer today is a trainable's
    ``trainable.output_schema`` (the P9 order-semantic ruling, 2026-06-10:
    ``architecture/hash-model.md`` § Training-bundle-hash — the bound wire form compiles
    the declared field order into the enforced emission order, so reordering is honestly
    a new training-bundle-hash). Non-trainable schemas and the read side stay
    name-keyed (:func:`canon_schema`) — nothing consumes their order.

    Order-semantics extend **recursively**: each field body folds with ``ordered=True``, so a
    nested-object member inside a trainable ``output_schema`` field ALSO folds its members as an
    ordered list (:func:`canon_type`'s ``NestedType`` arm) — a nested object's members are emitted
    in declared order on the wire too, so reordering them is likewise a new training-bundle-hash.
    :func:`canon_schema` stays ``ordered=False`` (its nested members remain name-keyed)."""
    return [{"name": fd.name, **canon_field(fd, ordered=True)} for fd in fields]


# ---------------------------------------------------------------------------
# Service-binding entries
# ---------------------------------------------------------------------------


def canon_service_binding_decl(sb: ServiceBindingDecl) -> dict[str, Any]:
    """A handler/trainable-declared ``service_bindings`` entry: its local name + the bound
    service-type qualified ``type`` (the qualified-name reference canon absorbs). The entry's
    grammar is **closed to ``{type}``** — a service-binding declaration carries no prose
    ``description`` (``description`` is model-facing contract content admitted only on a
    trainable's ``trainable.output_schema`` fields, per the family rule; a service binding folds
    name/type/identity/config — ``components/service-type/reference.md`` § Hash placement). There
    is therefore no per-binding prose to exclude; name + type are the whole fold."""
    return {"name": sb.name, "type": sb.type}


def canon_service_supply(
    s: ServiceBindingSupply, *, config: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """A pipeline-level ``service_bindings.<name>`` identity supply: the binding name, the
    service-type qualified ``type``, the **identity field values**, and — where the entry
    is a config supply site — the ``config`` block's **effective** values
    (supplied-or-default, computed by the caller where the registry is available), which
    fold in with the identity surface (``service-type/reference.md`` § Hash placement).
    ``config=None`` omits the key (a supply entry that is not a config supply site — the
    trainable backend's own entry, whose surface is ``[trainable.config]``). Values are
    arbitrary scalars/objects, canonicalized as data."""
    out: dict[str, Any] = {"name": s.name, "type": s.type, "identity": canon_value(s.identity)}
    if config is not None:
        out["config"] = canon_value(dict(config))
    return out


# ---------------------------------------------------------------------------
# Arbitrary supplied values (inline binding values, identity values)
# ---------------------------------------------------------------------------


def canon_value(v: Any) -> Any:
    """Canonicalize an arbitrary supplied value (an inline binding value, an identity-field
    value) as data. Mappings → key-sorted-at-serialization maps; lists/tuples preserve order
    (list data is ordered); scalars pass through; datetimes become a tagged ISO string (so a
    datetime never collides with a string of the same spelling). Anything else raises
    ``TypeError`` — fail loud rather than hash a value we cannot serialize deterministically."""
    return _canon_scalar(v)


def _canon_scalar(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Mapping):
        out: dict[str, Any] = {}
        for k, val in v.items():
            canon_key = str(k)
            if canon_key in out:  # two distinct source keys collapse to one canonical str key
                raise TypeError(  # fail loud — a silent merge would lose a declaration (the key
                    # side of the same "no silent coercion" promise the type arm below holds)
                    f"canon_value: mapping keys collide under str() — canonical key "
                    f"{canon_key!r} produced by more than one source key (a silent merge "
                    "would lose a declaration)"
                )
            out[canon_key] = _canon_scalar(val)
        return out
    if isinstance(v, (list, tuple)):
        return [_canon_scalar(x) for x in v]  # order significant (list/array data)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return {"__dt__": v.isoformat()}
    raise TypeError(  # fail loud — no silent coercion of an un-serializable supplied value
        f"canon_value: non-canonicalizable value of type {type(v).__name__!r}"
    )
