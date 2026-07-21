"""Stage-1 channel-field-type token parser + field-declaration parser.

Phase 0 shipped the normalized ``ChannelFieldType`` descriptor and the convenience
*builders*, but deliberately **no token-string parser** — that is the TOML
loader's job (the ``conjured.ir.channel_types`` module docstring). This module is that parser: it
turns the TOML token grammar — owned by ``conjured/docs/components/handler/reference.md``
§ Types allowed in ``reads`` and ``output_schema`` — into the normalized IR descriptor.

The closed token grammar (handler/reference.md § Types allowed):

    str · int · float · bool                          (primitives; NOT bytes — § Channel-type
                                                       discipline: bytes has no TOML token)
    list[<T>] · dict[str, <T>] · tuple[<T>, <U>, …]    (collections)
    <T> | None   (≡ nullable = true)                  (optionality — normalizes to OptionalType)
    Literal['a', 'b', …]                              (closed-enum scalar values)
    [<sec>.<field>.fields] sub-table                  (nested object; recurses to any depth)
    [<sec>.<field>.item.fields] sub-table             (list of nested records — the composite-slot
                                                       convention: the sub-table is named by the
                                                       composite's IR slot; presence marks shape)
    [<sec>.<field>.value.fields] sub-table            (dict[str, <nested record>]; `.item.item.…`
                                                       recursion composes — list-of-list-of-nested)

Every malformed token raises :class:`~conjured.errors.ContractViolation` with
``Check.CHANNEL_TYPE_TOKEN`` (handler/reference.md: "a type outside the engine's Pydantic
IR raises ContractViolation at handler-declaration load") — never a bare ``ValueError`` that
would escape the fuzz harness's compile-or-ContractViolation guarantee.
"""

from __future__ import annotations

from typing import Mapping

from conjured.errors import Check, ContractViolation
from conjured.ir.base import NO_DEFAULT
from conjured.ir.channel_types import (
    ChannelFieldType,
    DictType,
    FieldDecl,
    ListType,
    LiteralType,
    LiteralValue,
    NestedType,
    OptionalType,
    Primitive,
    PrimitiveType,
    SecretRefType,
    TableType,
    TupleType,
    ValidatorSpec,
    first_non_json_expressible,
    nested,
)
from conjured.validator.constraints import BUILTIN_VALIDATOR_NAMES, DIRECT_KEY_PARAM

#: The primitive tokens the TOML dialect admits — the four LCD scalars. ``bytes`` is in
#: the engine's Pydantic IR but has **no TOML token** (channel_types.py; handler/reference.md
#: § Channel-type discipline), so it is excluded here on purpose.
_TOML_PRIMITIVES = {
    "str": Primitive.STR,
    "int": Primitive.INT,
    "float": Primitive.FLOAT,
    "bool": Primitive.BOOL,
}

#: The closed set of per-field metadata keys a schema-section field table admits at EVERY
#: position (channel_types.py ``FIELD_METADATA_KEYS`` + ``type``; ``fields`` marks a nested
#: object). ``nullable`` normalizes into ``OptionalType`` rather than living on ``FieldDecl``.
#: The built-in constraint keywords (``BUILTIN_VALIDATOR_NAMES``) attach as DIRECT field
#: keys beside these (handler/reference.md § Validators), and ``default`` is admitted only
#: where the caller says so (a service-type ``[config_schema]`` field).
#: ``validators`` is GONE (D8 — one validation grammar): a ``validators`` key is now an
#: unknown bare key (CV). Every non-structural field key is a validation keyword — a bare
#: standard constraint or a namespaced (dotted) third-party validator.
#: ``description`` is **position-gated**, NOT in this always-admitted base set: it is model-facing
#: contract content admitted ONLY on a trainable composition node's ``trainable.output_schema``
#: fields (incl. nested members), on a wire family that delivers it (handler/reference.md
#: § TOML field type discipline / description-admission). The caller threads ``allow_description``
#: (true only at that one section); at every other field position ``description`` is an
#: inadmissible key (CV) whose remediation routes prose to ``[annotations]``.
#: ``item`` / ``value`` are the composite-slot shape markers (handler/reference.md § Types
#: allowed): a sub-table named by the composite's IR slot declares a list's / dict's nested
#: element schema — ``item = { fields = … }`` → ``ListType(item=NestedType)``, ``value =
#: { fields = … }`` → ``DictType(value=NestedType)`` — the exact analogue of ``fields``
#: marking a bare nested object (presence marks shape; no ``object`` token, no new token).
_FIELD_KEYS = {"type", "nullable", "fields", "item", "value"}

#: A field table declares exactly ONE shape marker; ``nullable`` and the validation
#: keywords compose beside whichever marker is present.
_SHAPE_KEYS = ("type", "fields", "item", "value")


def _violate(
    token: str, file_path: str, section_path: str | None, detail: str,
    rule_id: str = "R-handler-006",
) -> ContractViolation:
    """``rule_id`` names the owning rule for the declaration class whose schema section
    this type token lives in (the channel-type-token sibling of PARSE-F3) — R-handler-006
    for handler / trainable-composition sections (the default + most common owner),
    R-pipeline-001 for a pipeline [inputs]/[outputs] field, R-service-type-001 for a
    service-type schema field; threaded from :func:`parse_type_token` so a malformed type
    token cites its OWN rule, not the generic handler-flavored fallback. The token grammar
    itself is owned by handler/reference.md § Types allowed; the diagnostic routes the
    author to the section's owning rule."""
    return ContractViolation(
        check=Check.CHANNEL_TYPE_TOKEN,
        rule_id=rule_id,
        expected="a channel-field type in the engine's closed TOML token grammar",
        actual=detail,
        remediation_hint=(
            "use one of: str/int/float/bool, list[<T>], dict[str, <T>], "
            "tuple[<T>, <U>, …], <T> | None, Literal['a', …], a [.fields] nested object, "
            "or a composite-slot sub-table ([.item.fields] list-of-nested / "
            "[.value.fields] dict-of-nested)"
        ),
        file_path=file_path,
        section_path=section_path,
    )


def _split_top_level(body: str, sep: str) -> list[str]:
    """Split ``body`` on ``sep`` at bracket depth 0 (so commas inside ``list[…]`` /
    ``Literal[…]`` don't split). ``sep`` is a single character."""
    parts: list[str] = []
    depth = 0
    in_str: str | None = None
    current: list[str] = []
    for ch in body:
        if in_str is not None:
            current.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in ("'", '"'):
            in_str = ch
            current.append(ch)
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _find_top_level(token: str, needle: str) -> int:
    """Index of ``needle`` at bracket depth 0 (and outside string literals), or -1."""
    depth = 0
    in_str: str | None = None
    i = 0
    n = len(needle)
    while i < len(token):
        ch = token[i]
        if in_str is not None:
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            i += 1
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif depth == 0 and token[i : i + n] == needle:
            return i
        i += 1
    return -1


def _parse_literal_value(
    raw: str, token: str, file_path: str, section_path: str | None,
    rule_id: str = "R-handler-006",
) -> LiteralValue:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
        # A quoted member is EXACTLY one quoted string: the closed Literal grammar
        # defines no escaping, so the interior may not contain the member's own quote
        # character. Checking only the first/last characters would silently admit a
        # malformed member — `Literal['a' 'b']` parsing as the single string "a' 'b" —
        # instead of the CHANNEL_TYPE_TOKEN rejection the closed grammar requires.
        inner = raw[1:-1]
        if raw[0] in inner:
            raise _violate(
                token, file_path, section_path,
                f"malformed quoted Literal member {raw!r} (an interior quote — the "
                "grammar defines no escaping; each member is one quoted string)",
                rule_id,
            )
        return inner
    if raw == "true":
        return True
    if raw == "false":
        return False
    try:
        return int(raw)
    except ValueError as exc:
        raise _violate(
            token, file_path, section_path, f"unparseable Literal member {raw!r}", rule_id
        ) from exc


def parse_type_token(
    token: str, *, file_path: str, section_path: str | None = None, allow_table: bool = False,
    allow_secret_ref: bool = False,
    rule_id: str = "R-handler-006",
) -> ChannelFieldType:
    """Parse one channel-field type *token string* into the normalized IR descriptor.

    Pure + total over strings: every input either returns a ``ChannelFieldType`` or raises
    ``ContractViolation`` (``Check.CHANNEL_TYPE_TOKEN``). Recurses for collections /
    optionals / literals. ``allow_table`` admits the ``table`` token (the open string-keyed
    table of JSON-expressible values) — true ONLY at a service-type ``[config_schema]``
    field's top-level type; it does NOT propagate into the collection / optional recursion,
    so ``list[table]`` / ``table | None`` are rejected (``table`` is a top-level config
    field type, never nested — matching its shipped ``extras`` use). ``allow_secret_ref``
    admits the ``secret_ref`` token (a secret reference — deployment/reference.md § Secret
    references) — true ONLY at a ``[transport_schema]`` field's top-level type; it
    propagates through the ``| None`` optional split alone (``secret_ref | None`` is the
    nullable no-credential union) and NEVER into collection recursion, so a secret
    reference inside a ``dict``/``list``/``tuple`` value is unrepresentable by grammar
    (deployment/reference.md § Secret references: a credential never rides inside a
    collection value).

    ``rule_id`` is the owning rule of the declaration class whose schema section this token
    lives in (the channel-type-token sibling of PARSE-F3) — threaded from :func:`parse_field`
    and passed down every recursive call so a malformed token at any depth cites its OWN rule
    (R-pipeline-001 for a pipeline boundary field, R-service-type-001 for a service-type
    schema field), not the generic handler-flavored R-handler-006 fallback (the default)."""
    if not isinstance(token, str):
        raise _violate(str(token), file_path, section_path, f"type must be a string token, got {type(token).__name__}", rule_id)
    t = token.strip()
    if not t:
        raise _violate(token, file_path, section_path, "empty type token", rule_id)

    # Optionality is the outermost axis: a top-level ``| None`` normalizes to OptionalType
    # with a non-optional inner (channel_types.py: the inner is never itself OptionalType).
    bar = _find_top_level(t, "|")
    if bar != -1:
        left = t[:bar].strip()
        right = t[bar + 1 :].strip()
        if right != "None":
            raise _violate(token, file_path, section_path, f"the only union admitted is '<T> | None'; got '| {right}'", rule_id)
        inner = parse_type_token(
            left, file_path=file_path, section_path=section_path,
            allow_secret_ref=allow_secret_ref, rule_id=rule_id,
        )
        if isinstance(inner, OptionalType):  # pragma: no cover - unreachable: `left` precedes the first top-level `|`, so it carries no union and never parses to OptionalType (the `| None` RHS check above rejects `<T> | None | None` first); kept as the tripwire for the no-doubly-optional invariant, which has no OptionalType-constructor guard
            raise _violate(token, file_path, section_path, "doubly-optional type '<T> | None | None'", rule_id)
        return OptionalType(inner=inner)

    # Literal['a', 'b', …]
    if t.startswith("Literal[") and t.endswith("]"):
        body = t[len("Literal[") : -1].strip()
        if not body:
            raise _violate(token, file_path, section_path, "Literal[…] with no members", rule_id)
        values = tuple(
            _parse_literal_value(part, token, file_path, section_path, rule_id)
            for part in _split_top_level(body, ",")
        )
        return LiteralType(values=values)

    # Collections — prefix[…] (inner types never admit `table` — it is top-level only).
    if t.endswith("]"):
        open_idx = t.find("[")
        if open_idx != -1:
            head = t[:open_idx].strip()
            body = t[open_idx + 1 : -1].strip()
            if head == "list":
                item = parse_type_token(body, file_path=file_path, section_path=section_path, rule_id=rule_id)
                return ListType(item=item)
            if head == "dict":
                kv = _split_top_level(body, ",")
                if len(kv) != 2 or kv[0].strip() != "str":
                    raise _violate(token, file_path, section_path, "dict must be 'dict[str, <T>]' (string keys only)", rule_id)
                value = parse_type_token(kv[1], file_path=file_path, section_path=section_path, rule_id=rule_id)
                return DictType(value=value)
            if head == "tuple":
                items = tuple(
                    parse_type_token(part, file_path=file_path, section_path=section_path, rule_id=rule_id)
                    for part in _split_top_level(body, ",")
                )
                return TupleType(items=items)
            raise _violate(token, file_path, section_path, f"unknown collection constructor {head!r}", rule_id)

    # Primitives (the four LCD scalars; bytes intentionally excluded — no TOML token).
    prim = _TOML_PRIMITIVES.get(t)
    if prim is not None:
        return PrimitiveType(primitive=prim)
    if t == "bytes":
        raise _violate(token, file_path, section_path, "'bytes' has no TOML token (direct-Pydantic dialect only)", rule_id)
    if t == "table":
        # The open string-keyed table — admissible ONLY where the caller threads
        # allow_table (a service-type [config_schema] field's top-level type).
        if not allow_table:
            raise _violate(
                token, file_path, section_path,
                "the 'table' type is admissible only as a service-type [config_schema] "
                "field's top-level type (not in reads/output_schema, not nested, not in a "
                "collection)",
                rule_id,
            )
        return TableType()
    if t == "secret_ref":
        # The secret-reference type — admissible ONLY where the caller threads
        # allow_secret_ref (a [transport_schema] field's top-level type, optionally
        # `secret_ref | None`; deployment/reference.md § Secret references).
        if not allow_secret_ref:
            raise _violate(
                token, file_path, section_path,
                "the 'secret_ref' type is admissible only as a [transport_schema] field's "
                "top-level type (optionally 'secret_ref | None') — a secret reference has "
                "no place in identity/config/reads/output_schema, and never inside a "
                "collection (deployment/reference.md § Secret references)",
                rule_id,
            )
        return SecretRefType()
    raise _violate(token, file_path, section_path, f"unrecognized type token {t!r}", rule_id)


#: The closed validation vocabulary the unknown-bare-key diagnostic names (the bare
#: standard keywords); a namespaced (dotted) key is a third-party validator instead.
_VALIDATION_VOCAB = ", ".join(sorted(BUILTIN_VALIDATOR_NAMES))


def _classify_validation_keys(
    value: Mapping, structural: set[str], field_path: str, file_path: str,
    rule_id: str = "R-handler-006",
) -> tuple[tuple[ValidatorSpec, ...], list[str]]:
    """Walk a field table's non-structural keys in **authored order** and classify each
    as a validation keyword (D8 — one grammar): a **bare** key in
    ``BUILTIN_VALIDATOR_NAMES`` normalizes to its built-in constraint spec (the documented
    param name carries the value); a **namespaced (dotted)** key is a third-party validator
    whose value IS its params table (``{}`` when parameterless). A bare key that is neither
    structural nor a standard keyword is an unknown bare key (returned for the caller's
    closed-grammar diagnostic). Order is preserved — enforcement + hash fold in authored
    key order across both classes."""
    specs: list[ValidatorSpec] = []
    unknown_bare: list[str] = []
    for key in value:
        if key in structural:
            continue
        if "." in key:
            params = value[key]
            if not isinstance(params, Mapping):
                raise _malformed_field(
                    field_path,
                    f"the namespaced validator key '{key}' carries its parameter table as "
                    f"its value ({{}} when parameterless), got {type(params).__name__}",
                    file_path,
                    rule_id,
                )
            specs.append(ValidatorSpec(name=key, params=dict(params)))
        elif key in BUILTIN_VALIDATOR_NAMES:
            specs.append(ValidatorSpec(name=key, params={DIRECT_KEY_PARAM[key]: value[key]}))
        else:
            unknown_bare.append(key)
    return tuple(specs), unknown_bare


def _parse_nested_members(
    members: object,
    *,
    field_path: str,
    file_path: str,
    rule_id: str,
    allow_description: bool,
    constraints_forbidden_rule_id: str | None,
) -> NestedType:
    """Parse a ``fields`` sub-table into a :class:`NestedType` — the shared arm for a
    bare nested object AND a composite slot's element schema. ``description`` admission
    propagates into members (handler/reference.md § description-admission: "incl. nested
    members"); ``allow_table`` does NOT propagate (top-level only)."""
    if not isinstance(members, Mapping) or not members:
        raise _malformed_field(
            field_path, "a nested object's 'fields' must be a non-empty table",
            file_path, rule_id,
        )
    return nested(
        *(
            parse_field(
                k, v, file_path=file_path, section_path=field_path + ".fields",
                rule_id=rule_id,
                allow_description=allow_description,
                constraints_forbidden_rule_id=constraints_forbidden_rule_id,
            )
            for k, v in members.items()
        )
    )


def _parse_composite_slot(
    slot: str,
    element: object,
    *,
    field_path: str,
    file_path: str,
    rule_id: str,
    allow_description: bool,
    constraints_forbidden_rule_id: str | None,
) -> ChannelFieldType:
    """Parse one composite-slot sub-table (handler/reference.md § Types allowed, the
    composite-slot convention): ``item = {…}`` → ``list[<element>]``, ``value = {…}`` →
    ``dict[str, <element>]``. The element table declares **exactly one** shape key —
    ``fields`` (a nested record) or a further ``item`` / ``value`` (recursion:
    ``.item.item.fields`` is list-of-list-of-nested) — and nothing else: validation
    keywords attach on the FIELD's own table, and the element admits no ``nullable``
    (optional-of-nested stays inexpressible, exactly as it is in the token grammar)."""
    slot_path = f"{field_path}.{slot}"
    if not isinstance(element, Mapping):
        raise _malformed_field(
            slot_path,
            f"a composite slot '{slot}' declares its element schema as a sub-table "
            f"(e.g. [{slot_path}.fields])",
            file_path, rule_id,
        )
    element_shapes = [k for k in ("fields", "item", "value") if k in element]
    extra = set(element) - set(element_shapes)
    if extra or len(element_shapes) != 1:
        raise _malformed_field(
            slot_path,
            "a composite slot's element table declares exactly one shape key — 'fields' "
            "(a nested record) or a further 'item'/'value' (a nested composite) — and "
            f"nothing else; got {sorted(element)}",
            file_path, rule_id,
        )
    shape = element_shapes[0]
    if shape == "fields":
        inner: ChannelFieldType = _parse_nested_members(
            element["fields"], field_path=slot_path, file_path=file_path,
            rule_id=rule_id, allow_description=allow_description,
            constraints_forbidden_rule_id=constraints_forbidden_rule_id,
        )
    else:
        inner = _parse_composite_slot(
            shape, element[shape], field_path=slot_path, file_path=file_path,
            rule_id=rule_id, allow_description=allow_description,
            constraints_forbidden_rule_id=constraints_forbidden_rule_id,
        )
    return ListType(item=inner) if slot == "item" else DictType(value=inner)


def parse_field(
    name: str,
    value: object,
    *,
    file_path: str,
    section_path: str,
    rule_id: str = "R-handler-006",
    allow_default: bool = False,
    allow_table: bool = False,
    allow_secret_ref: bool = False,
    allow_description: bool = False,
    constraints_forbidden_rule_id: str | None = None,
) -> FieldDecl:
    """Parse one declared field — a ``reads`` / ``output_schema`` / ``*_schema`` entry — into
    a :class:`FieldDecl`. A field value is either a bare type-token string (shorthand) or a
    field-metadata table; a nested object is marked by a ``fields`` sub-table, and a
    **list / dict of nested records** by the composite-slot sub-tables ``item`` / ``value``
    (named by the composite's IR slot; ``item = { fields = … }`` → ``list[<nested>]``,
    ``value = { fields = … }`` → ``dict[str, <nested>]``, recursing —
    handler/reference.md § Types allowed). Exactly one shape marker per field.

    Validation keywords attach as **direct field keys** in one grammar (D8 —
    handler/reference.md § Validators): a **bare** key is a built-in standard constraint
    (normalized into a :class:`ValidatorSpec` via ``constraints.DIRECT_KEY_PARAM``); a
    **namespaced (dotted)** key is a registered third-party validator whose value IS its
    params table. Both classes interleave in authored key order in the single
    ``validators`` tuple; a bare key that is neither structural nor a standard keyword is a
    loud closed-grammar ContractViolation naming the vocabulary (the retired ``validators``
    list key is now exactly such an unknown bare key). ``allow_default`` admits the
    ``default`` key — a service-type ``[config_schema]`` field's ship-time default (the ONLY
    ``default``-bearing field surface; never propagated into nested members).
    ``allow_description`` admits the ``description`` key — model-facing contract content admitted
    ONLY on a trainable composition node's ``trainable.output_schema`` fields (incl. nested
    members, so it DOES propagate into ``fields`` recursion), on a wire family that delivers it
    (handler/reference.md § description-admission). At every other position a ``description`` key
    is an inadmissible closed-grammar violation whose remediation routes the prose to
    ``[annotations]`` (the field-position half of the family rule; the WIRE-family rejection —
    a described field on a wire that cannot carry descriptions — fires later, at compose).
    ``constraints_forbidden_rule_id`` (non-None for pipeline / composition boundary
    ``[inputs]`` / ``[outputs]``) makes any validation keyword a loud ContractViolation
    citing that rule — boundary validation is presence-only, so a value constraint there
    would have no enforcement point of its own (the silent-no-op class the engine
    forecloses; pipeline/reference.md § ``inputs`` / ``outputs``).
    ``rule_id`` (PARSE-F3) is the owning rule of the declaration class this section belongs
    to; it is the ``rule_id`` every MALFORMED_DECLARATION diagnostic this function raises
    cites (the default R-handler-006 is the handler / trainable-composition owner).
    """
    field_path = f"{section_path}.{name}"

    # Shorthand: `name = "str"` — a bare type token (`allow_table` admits `name = "table"`;
    # `allow_secret_ref` admits `name = "secret_ref"`).
    if isinstance(value, str):
        return FieldDecl(
            name=name,
            type=parse_type_token(
                value, file_path=file_path, section_path=field_path, allow_table=allow_table,
                allow_secret_ref=allow_secret_ref,
                rule_id=rule_id,
            ),
        )

    if isinstance(value, Mapping):
        structural = set(_FIELD_KEYS)
        if allow_default:
            structural = structural | {"default"}
        if allow_description:
            structural = structural | {"description"}
        # Position-gated `description`: model-facing contract content admitted ONLY on a
        # trainable's `trainable.output_schema` fields (the caller threads `allow_description`
        # there). Anywhere else it is inadmissible — raise a precise CV routing the prose to
        # `[annotations]` (the field-position half of the family rule) BEFORE the generic
        # unknown-bare-key path, so the author sees the migration home, not a bare "unknown key".
        if not allow_description and "description" in value:
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR,
                rule_id=rule_id,
                expected="a schema field's `description` is admitted ONLY on a trainable "
                         "composition node's `trainable.output_schema` fields (incl. nested "
                         "members), on a wire family that delivers descriptions "
                         "(handler/reference.md § description-admission)",
                actual=f"a `description` key on the non-admitted field '{field_path}'",
                remediation_hint="route the field prose to the declaration's `[annotations]` "
                                 "block (or a TOML comment where the grammar declares no "
                                 "annotations, e.g. a pipeline `[inputs]`/`[outputs]` field); "
                                 "`description` is contract content only for a trainable's "
                                 "`output_schema`",
                file_path=file_path,
                section_path=field_path,
            )
        # One grammar: every non-structural key is a validation keyword (bare standard
        # constraint, or namespaced dotted third-party validator), classified in authored
        # key order. Enforcement + hash fold in that order across both classes.
        validators, unknown_bare = _classify_validation_keys(
            value, structural, field_path, file_path, rule_id
        )
        if constraints_forbidden_rule_id is not None and validators:
            offending = sorted(spec.name for spec in validators)
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR,
                rule_id=constraints_forbidden_rule_id,
                expected="boundary fields admit no validation keywords — neither bare "
                         "standard constraints nor namespaced third-party validators; "
                         "boundary validation is presence-only, so a value constraint here "
                         "would have no enforcement point of its own",
                actual=f"validation keyword(s) {offending} on boundary field '{field_path}'",
                remediation_hint="declare the value constraint on the port that enforces "
                                 "it — the reading node's reads for an input channel; the "
                                 "writing node's output_schema for an output channel",
                file_path=file_path,
                section_path=field_path,
            )
        if unknown_bare:
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR,
                rule_id=rule_id,
                expected="field keys in the closed structural set {type, nullable, fields, "
                         "item, value"
                         + (", default" if allow_default else "")
                         + (", description" if allow_description else "")
                         + "} plus validation keywords — a bare standard keyword "
                         f"({_VALIDATION_VOCAB}) or a namespaced (dotted) third-party "
                         "validator key",
                actual=f"unknown bare field key(s) {sorted(unknown_bare)} on '{field_path}' "
                       "(a bare key must be a structural key or a standard validation "
                       "keyword; a third-party validator key must be namespaced/dotted)",
                remediation_hint="remove the unknown key, route field prose to `[annotations]` "
                                 "(or a TOML comment at a boundary field), or namespace a "
                                 "third-party validator key (e.g. 'mypkg.name')",
                file_path=file_path,
                section_path=field_path,
            )
        description = value.get("description")
        if description is not None and not isinstance(description, str):
            raise _malformed_field(field_path, "description must be a string", file_path, rule_id)

        # --- The shape marker: a field table declares exactly ONE of the closed shape
        # --- set — `type` (a token), `fields` (a nested object), or a composite slot
        # --- (`item` → list-of, `value` → dict[str, …]-of; handler/reference.md § Types
        # --- allowed, the composite-slot sub-table convention).
        present_shapes = [k for k in _SHAPE_KEYS if k in value]
        if len(present_shapes) > 1:
            raise _malformed_field(
                field_path,
                f"a field declares exactly one shape marker (one of {list(_SHAPE_KEYS)}); "
                f"got {present_shapes}",
                file_path, rule_id,
            )
        if not present_shapes:
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION,
                rule_id=rule_id,
                expected=f"field '{field_path}' declares a 'type' (or a nested 'fields' "
                         "table, or a composite-slot 'item'/'value' sub-table)",
                actual="no shape marker present",
                remediation_hint="add type = \"<token>\", a [.fields] sub-table for a nested "
                                 "object, or [.item.fields] / [.value.fields] for a "
                                 "list / dict of nested records",
                file_path=file_path,
                section_path=field_path,
            )
        shape = present_shapes[0]
        if shape == "fields":
            field_type: ChannelFieldType = _parse_nested_members(
                value["fields"], field_path=field_path, file_path=file_path,
                rule_id=rule_id, allow_description=allow_description,
                constraints_forbidden_rule_id=constraints_forbidden_rule_id,
            )
        elif shape in ("item", "value"):
            field_type = _parse_composite_slot(
                shape, value[shape], field_path=field_path, file_path=file_path,
                rule_id=rule_id, allow_description=allow_description,
                constraints_forbidden_rule_id=constraints_forbidden_rule_id,
            )
        else:
            field_type = parse_type_token(
                value["type"], file_path=file_path, section_path=field_path,
                allow_table=allow_table, allow_secret_ref=allow_secret_ref, rule_id=rule_id,
            )

        nullable = value.get("nullable", False)
        # `nullable` is a closed-enum field-metadata key: a boolean (defaults false), the
        # shorthand for `"<T> | None"` (handler/reference.md § per-field metadata keys). A
        # non-boolean value is a malformed declaration — assert it loud like the adjacent
        # `description`/`validators` checks, never silently coerce to not-nullable (a silent
        # fallback is forbidden as a category by invariant I1, reference/principles.md).
        if not isinstance(nullable, bool):
            raise _malformed_field(field_path, "nullable must be a boolean", file_path, rule_id)
        if nullable is True and not isinstance(field_type, OptionalType):
            field_type = OptionalType(inner=field_type)
        default = value["default"] if allow_default and "default" in value else NO_DEFAULT
        # A [config_schema] field's declared default is an engine-read TOML value position
        # feeding a declared field, so the reserved explicit-null form is recognized here —
        # and config fields admit no nullable declaration, so recognition always REJECTS
        # (recognized-and-rejected, never silently absorbed as data — handler/reference.md
        # explicit-null region; the classifier also fails a malformed spelling loud). The
        # import is deferred to keep this near-leaf module free of sibling validator
        # imports at module scope.
        if default is not NO_DEFAULT:
            from conjured.validator.normalize import is_explicit_null

            # guarantees: explicit-null-nullable-only
            if is_explicit_null(
                default, owner=f"{field_path}.default", file_path=file_path,
                section_path=f"{field_path}.default", rule_id=rule_id,
            ):
                raise ContractViolation(
                    check=Check.EXPLICIT_NULL_TARGET, rule_id="R-pipeline-001",
                    expected="{ null = true } targets a nullable-declared field",
                    actual=f"the declared default of config field '{name}' — config fields "
                           "admit no nullable declaration",
                    file_path=file_path, section_path=f"{field_path}.default",
                )
        # A `table` field's value is JSON-expressible only (the canon: strings/ints/floats/
        # bools and arrays/tables of these). A declared default carrying a non-JSON TOML
        # value (a datetime/date/time) raises at declaration load — it could not fold into
        # the hash as canonical data. (Supplied values are re-checked at compose through
        # effective_config; this is the service-type-load arm for the declared default.)
        if isinstance(field_type, TableType) and default is not NO_DEFAULT:
            bad = first_non_json_expressible(default)
            if bad is not None:
                raise _malformed_field(
                    field_path,
                    "a 'table' field's value must be JSON-expressible (strings, integers, "
                    "floats, booleans, and arrays/tables of these); the declared default "
                    f"carries a non-JSON {bad} value",
                    file_path,
                    rule_id,
                )
        return FieldDecl(
            name=name, type=field_type, description=description,
            validators=validators, default=default,
        )

    raise _malformed_field(field_path, f"field value must be a type token or a table, got {type(value).__name__}", file_path, rule_id)


def parse_schema_section(
    section: object,
    *,
    file_path: str,
    section_path: str,
    rule_id: str = "R-handler-006",
    allow_default: bool = False,
    allow_table: bool = False,
    allow_secret_ref: bool = False,
    allow_description: bool = False,
    constraints_forbidden_rule_id: str | None = None,
) -> tuple[FieldDecl, ...]:
    """Parse a whole schema section (a TOML table of field declarations) into an ordered
    tuple of :class:`FieldDecl`. Declaration order is preserved (it is semantic + hashed).
    ``rule_id`` (the owning rule of the declaration class this section belongs to; PARSE-F3)
    and ``allow_default`` / ``allow_table`` / ``allow_secret_ref`` / ``allow_description`` /
    ``constraints_forbidden_rule_id`` thread to :func:`parse_field` (the malformed-field
    diagnostic's owning rule; the ``[config_schema]`` ship-time-default + ``table`` surfaces;
    the ``[transport_schema]`` secret-reference surface; the ``description`` admission —
    true ONLY at a trainable ``trainable.output_schema``; the boundary no-constraints
    rule)."""
    if not isinstance(section, Mapping):
        raise _malformed_field(section_path, f"section must be a table, got {type(section).__name__}", file_path, rule_id)
    return tuple(
        parse_field(
            name, value, file_path=file_path, section_path=section_path,
            rule_id=rule_id,
            allow_default=allow_default,
            allow_table=allow_table,
            allow_secret_ref=allow_secret_ref,
            allow_description=allow_description,
            constraints_forbidden_rule_id=constraints_forbidden_rule_id,
        )
        for name, value in section.items()
    )


def field_type_contains_optional(field_type: ChannelFieldType) -> bool:
    """Whether ``None`` is admissible ANYWHERE in a field's type tree — the whole tree, not
    just the top level. Used by the nullable-placement check (nullable is transport-only;
    service-type/reference.md § ``[transport_schema]`` bans the ``<T> | None`` union on
    identity/config fields — a nullable nested inside an object or collection is the same
    ban, so the walk recurses through every composite)."""
    if isinstance(field_type, OptionalType):
        return True
    if isinstance(field_type, ListType):
        return field_type_contains_optional(field_type.item)
    if isinstance(field_type, DictType):
        return field_type_contains_optional(field_type.value)
    if isinstance(field_type, TupleType):
        return any(field_type_contains_optional(e) for e in field_type.items)
    if isinstance(field_type, NestedType):
        return any(field_type_contains_optional(f.type) for f in field_type.fields)
    return False


def _malformed_field(
    field_path: str, detail: str, file_path: str, rule_id: str = "R-handler-006"
) -> ContractViolation:
    """``rule_id`` names the owning rule for the declaration class whose schema section this
    field lives in (PARSE-F3) — R-handler-006 for handler / trainable-composition schemas (the
    default + most common owner), R-service-type-001 for service-type schemas, R-pipeline-001 for
    the pipeline boundary; threaded from :func:`parse_schema_section` so a malformed field cites
    its OWN rule, not the generic handler-flavored fallback."""
    return ContractViolation(
        check=Check.MALFORMED_DECLARATION,
        rule_id=rule_id,
        expected=f"a well-formed field declaration at '{field_path}'",
        actual=detail,
        file_path=file_path,
        section_path=field_path,
    )
