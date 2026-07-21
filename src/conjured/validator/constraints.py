"""The built-in attachable field constraints — the engine's own validator set.

Bare keys ARE the **JSON Schema draft-2020-12 validation keywords applicable to the
field's declared type** — "not a hand-rolled engine matrix"
(``conjured/docs/components/handler/reference.md`` § Validators) — realized over the
engine's channel-type system by applicability family:

- **numeric** (``int`` / ``float``): ``minimum`` / ``maximum`` / ``exclusiveMinimum`` /
  ``exclusiveMaximum`` / ``multipleOf``.
- **string** (``str``): ``minLength`` / ``maxLength`` / ``pattern``.
- **array cardinality** (the variable-length ``list[T]`` only): ``minItems`` / ``maxItems``.
- **array distinctness** (any array — ``list[T]`` or a fixed-arity ``tuple``): ``uniqueItems``.
- **object** (open-keyed ``dict[str, T]``): ``minProperties`` / ``maxProperties``.
- **any declared type**: ``enum``.

The array/object **cardinality** keywords apply to the **variable-cardinality** channel types
(``ListType`` / ``DictType``), where they constrain an axis the type does not already fix;
on a fixed-arity ``TupleType`` or a fixed-field ``NestedType`` the cardinality is structural,
so a cardinality keyword can never apply and is rejected loud (the same fail-loud-
inapplicability deviation the numeric/string families apply). ``uniqueItems`` is a
**distinctness** keyword, *orthogonal to cardinality* — the fixed arity says nothing about
whether the elements differ — so it applies to **any** array, a ``ListType`` or a fixed-arity
``TupleType``, and is NOT rejected on a tuple (``uniqueItems: true`` on ``tuple[int, int]`` means
"the two elements must differ" — meaningful and satisfiable). A handful of draft-2020-12
assertion keywords are deliberately **not** attachable: ``type`` / ``required`` / ``nullable``
are field axes (the type system carries them); ``const`` duplicates the ``Literal[x]`` type
token; ``maxContains`` / ``minContains`` require the ``contains`` applicator the constraint
grammar has no analogue for; ``dependentRequired`` names cross-property dependencies the
open ``dict[str, T]`` / fixed-field ``NestedType`` shapes give no surface for.

The ``constraint_violated`` name vocabulary is owned by
``conjured/docs/components/error-channel/reference.md`` § SchemaValidationError payload and is
explicitly open. Two field-axis exclusions (not attachable list entries):

- The error-channel's ``type`` / ``required`` / ``nullable`` members are field *axes*
  (carried by the type system + the generated models), not attachable list entries.
- ``keys_subset_of`` is structurally produced by the generated models' closed shape
  (``extra="forbid"``; mapped in ``runner/dispatch.py``) — attaching it by name would
  duplicate a structural guarantee, so it is deliberately not attachable.

**The same contract as a third-party validator** (one problem, one solution): each
built-in is a bare kwarg-only pure function under the R-handler-012 verdict protocol
(``None`` = pass; a one-line failure string = the per-field message), bound by the same
engine-owned partial application and policed by the same ``{value}`` ∪ declared-params
signature check (``validator/resolve_validator.py``) — so a wrong or missing built-in
parameter name rejects at compose exactly as a third-party signature mismatch does.

**Attachment + parameter names.** A built-in attaches as a **direct field key** —
``pattern = "^\\d{4}"``, ``minLength = 4`` — the standard's own shape (handler/reference.md
§ Validators); the loader (``validator/tokens.py``) normalizes the key into the internal
constraint representation via :data:`DIRECT_KEY_PARAM`: the value/length/cardinality limit
constraints (``minimum`` / ``maximum`` / ``exclusiveMinimum`` / ``exclusiveMaximum`` /
``minLength`` / ``maxLength`` / ``minItems`` / ``maxItems`` / ``minProperties`` /
``maxProperties``) take ``limit``; ``multipleOf`` takes ``multiple``; ``pattern`` takes
``pattern`` (an unanchored regex, ``re.search`` semantics — the JSON-Schema ``pattern``
reading); ``uniqueItems`` takes ``unique`` (the boolean flag); ``enum`` takes ``values``
(the admitted members; membership is **JSON-Schema value-equality** — numeric ``1``
and ``1.0`` are one JSON number, while a boolean is its own JSON type, so ``True``
never matches ``1`` — handler/reference.md § Enum-vs-field-type coherence owns the
semantics; deliberately LOOSER than the ``Literal`` realization's exact-type subset).
A built-in keyword is never a ``validators`` list entry (the list is third-party-only).

**Compose-time parameter-value checks.** Each built-in pairs with a param check the
resolver runs at binding (R-handler-012's compose-or-never posture — a malformed
engine-read parameter is compose-knowable and never defers to dispatch): a **finite**
numeric ``limit`` for the four value bounds (a non-finite limit — ``nan`` / ``inf``,
both TOML-expressible — makes every IEEE comparison False, the silent-no-op class the
compose checks foreclose), a finite-and-strictly-positive ``multiple`` for ``multipleOf``
(a non-positive divisor is a div-by-zero / silent no-op), a non-negative-int ``limit`` for
the length and cardinality bounds, a boolean ``unique`` for ``uniqueItems``, a compiling
``pattern``, a non-empty ``values`` list. The check returns a problem string (or ``None``);
the resolver raises the structured ``ContractViolation`` (``Check.VALIDATOR_PARAMS``) — this
module stays pure.
"""

from __future__ import annotations

import math
import re
from typing import Callable, Mapping, TypeGuard

#: A built-in's compose-time param-value check: returns ``None`` (well-formed) or a
#: one-line problem description the resolver raises as ``Check.VALIDATOR_PARAMS``.
ParamCheck = Callable[[Mapping[str, object]], "str | None"]


def _is_number(value: object) -> TypeGuard[int | float]:
    """A numeric limit — int or float; ``bool`` is excluded (it is an ``int`` subtype,
    but a ``limit = true`` is an authoring error, not a bound)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# The value bounds — minimum / maximum / exclusiveMinimum / exclusiveMaximum
# ---------------------------------------------------------------------------


def _minimum(*, value, limit):
    if value < limit:
        return f"value {value} below minimum {limit}"
    return None


def _maximum(*, value, limit):
    # Message form fixed by canon's example: "value 11 above maximum 10"
    # (error-channel/reference.md § SchemaValidationError payload).
    if value > limit:
        return f"value {value} above maximum {limit}"
    return None


def _exclusive_minimum(*, value, limit):
    if value <= limit:
        return f"value {value} not above exclusiveMinimum {limit}"
    return None


def _exclusive_maximum(*, value, limit):
    if value >= limit:
        return f"value {value} not below exclusiveMaximum {limit}"
    return None


def _numeric_limit(params: Mapping[str, object]) -> str | None:
    limit = params.get("limit")
    if not _is_number(limit):
        return f"'limit' must be a number, got {type(limit).__name__}"
    if not math.isfinite(limit):
        # nan/inf are TOML-expressible; every IEEE comparison with nan is False, so a
        # nan bound would pass everything forever — a silent no-op, never composed.
        return f"'limit' must be finite, got {limit}"
    return None


# ---------------------------------------------------------------------------
# The length bounds — minLength / maxLength
# ---------------------------------------------------------------------------


def _min_length(*, value, limit):
    if len(value) < limit:
        return f"length {len(value)} below minLength {limit}"
    return None


def _max_length(*, value, limit):
    if len(value) > limit:
        return f"length {len(value)} above maxLength {limit}"
    return None


def _length_limit(params: Mapping[str, object]) -> str | None:
    limit = params.get("limit")
    if not isinstance(limit, int) or isinstance(limit, bool):
        return f"'limit' must be an integer, got {type(limit).__name__}"
    if limit < 0:
        return f"'limit' must be non-negative, got {limit}"
    return None


# ---------------------------------------------------------------------------
# pattern
# ---------------------------------------------------------------------------


def _pattern(*, value, pattern):
    # Unanchored search — the JSON-Schema `pattern` semantics (a constraint is
    # satisfied if the regex matches anywhere; authors anchor explicitly).
    if re.search(pattern, value) is None:
        return f"value {value!r} does not match pattern {pattern!r}"
    return None


def _pattern_params(params: Mapping[str, object]) -> str | None:
    pattern = params.get("pattern")
    if not isinstance(pattern, str):
        return f"'pattern' must be a regex string, got {type(pattern).__name__}"
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"'pattern' does not compile as a regex ({exc})"
    return None


# ---------------------------------------------------------------------------
# enum
# ---------------------------------------------------------------------------


def _enum(*, value, values):
    # JSON-Schema membership (draft 2020-12 — "bare keywords carry the standard's
    # semantics"): a value is admitted iff it equals a member under the standard's
    # value-equality — numeric 1 == 1.0 (one JSON number type), while a boolean is its
    # own JSON type (True never matches member 1). `_json_equal` below is the single
    # equality this keyword and `uniqueItems` share (one semantics, one function);
    # it recurses through arrays and objects.
    for member in values:
        if _json_equal(value, member):
            return None
    rendered = ", ".join(str(m) for m in values)
    # Message form fixed by canon's example:
    # "expected one of [happy, sad, angry], got 'confused'"
    return f"expected one of [{rendered}], got {value!r}"


def _enum_params(params: Mapping[str, object]) -> str | None:
    values = params.get("values")
    if not isinstance(values, (list, tuple)):
        return f"'values' must be a list of admitted members, got {type(values).__name__}"
    if not values:
        return "'values' must be non-empty (a closed enum with no members admits nothing)"
    return None


# ---------------------------------------------------------------------------
# multipleOf — numeric (draft-2020-12 §6.2.1)
# ---------------------------------------------------------------------------


def _multiple_of(*, value, multiple):
    # draft-2020-12 §6.2.1: valid iff value / multiple is an integer. Float representation
    # drift (0.3 / 0.1 == 2.9999999999999996) is tolerated RELATIVE to the quotient's
    # magnitude — never an absolute window, which admits a value orders of magnitude
    # smaller than `multiple` (1e-13 is not a multiple of 1) as a false multiple.
    quotient = value / multiple
    if not math.isclose(quotient, round(quotient), rel_tol=1e-9, abs_tol=0.0):
        return f"value {value} is not a multiple of {multiple}"
    return None


def _multiple_of_param(params: Mapping[str, object]) -> str | None:
    multiple = params.get("multiple")
    if not _is_number(multiple):
        return f"'multiple' must be a number, got {type(multiple).__name__}"
    if not math.isfinite(multiple):
        return f"'multiple' must be finite, got {multiple}"
    if multiple <= 0:
        # JSON Schema: multipleOf MUST be strictly greater than 0 — a non-positive divisor
        # is a div-by-zero / silent-no-op, never composed.
        return f"'multiple' must be greater than 0, got {multiple}"
    return None


# ---------------------------------------------------------------------------
# Array keywords — cardinality (minItems / maxItems) + distinctness (uniqueItems)
# (draft-2020-12 §6.4); cardinality applies to list[T] only, distinctness to any array
# ---------------------------------------------------------------------------


def _min_items(*, value, limit):
    if len(value) < limit:
        return f"item count {len(value)} below minItems {limit}"
    return None


def _max_items(*, value, limit):
    if len(value) > limit:
        return f"item count {len(value)} above maxItems {limit}"
    return None


def _json_equal(a: object, b: object) -> bool:
    """JSON-Schema value-equality (draft 2020-12), applied recursively through arrays and
    objects. Identical to Python ``==`` except a **boolean is a distinct JSON type from a
    number** — ``True`` never equals ``1`` — where bare ``==`` conflates them (``True == 1``,
    and ``{"a": 1} == {"a": True}`` by extension). Numeric ``1 == 1.0`` stays equal (both are
    JSON numbers — the standard's numeric equality)."""
    if isinstance(a, bool) or isinstance(b, bool):
        # bool is its own JSON type: equal only to a bool of the same value (never to a number,
        # even though Python's `bool` subclasses `int`). Handled first so the numeric fallthrough
        # below never sees a bool.
        return type(a) is type(b) and a == b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    return a == b


def _unique_items(*, value, unique):
    # `uniqueItems = false` imposes no constraint (the standard's posture — a meaningful
    # explicit opt-out, not a defect). `uniqueItems = true` requires all items distinct by
    # JSON-Schema value-equality (draft 2020-12): a boolean is a distinct JSON type from a
    # number, so `1` and `True` are distinct — `_json_equal` enforces that (recursively through
    # nested arrays/objects), where bare Python `==` conflates them (`True == 1`). `1 == 1.0`
    # remains a duplicate (both are JSON numbers), and `1 == "1"` is distinct (different types).
    # The items need NOT share one declared element type — a `tuple[...]` is heterogeneous. O(n²)
    # over a declared output array (small); the linear `seen` scan (not a `set()`) also lets
    # unhashable members (nested dicts/lists) compare correctly where a `set()` would raise.
    if not unique:
        return None
    seen: list = []
    for item in value:
        if any(_json_equal(item, s) for s in seen):
            return f"items are not unique (duplicate {item!r})"
        seen.append(item)
    return None


def _unique_items_param(params: Mapping[str, object]) -> str | None:
    unique = params.get("unique")
    if not isinstance(unique, bool):
        return f"'unique' must be a boolean (uniqueItems = true | false), got {type(unique).__name__}"
    return None


# ---------------------------------------------------------------------------
# Object cardinality — minProperties / maxProperties (draft-2020-12 §6.5)
# ---------------------------------------------------------------------------


def _min_properties(*, value, limit):
    if len(value) < limit:
        return f"property count {len(value)} below minProperties {limit}"
    return None


def _max_properties(*, value, limit):
    if len(value) > limit:
        return f"property count {len(value)} above maxProperties {limit}"
    return None


# ---------------------------------------------------------------------------
# The registry — name → (validator function, compose-time param check)
# ---------------------------------------------------------------------------

#: The closed built-in attachable constraint set. Built-in names are engine-reserved
#: keywords that attach as **direct field keys** (handler/reference.md § Validators —
#: "Built-in constraints are direct field keys"; the loader normalizes a direct key into
#: the same internal constraint representation a resolved validator binds to). A built-in
#: keyword is never a ``validators`` list entry — the list resolves against the
#: third-party registry only. Extending the set is an engine change.
BUILTIN_VALIDATORS: dict[str, tuple[Callable[..., object], ParamCheck]] = {
    "minimum": (_minimum, _numeric_limit),
    "maximum": (_maximum, _numeric_limit),
    "exclusiveMinimum": (_exclusive_minimum, _numeric_limit),
    "exclusiveMaximum": (_exclusive_maximum, _numeric_limit),
    "multipleOf": (_multiple_of, _multiple_of_param),
    "minLength": (_min_length, _length_limit),
    "maxLength": (_max_length, _length_limit),
    "pattern": (_pattern, _pattern_params),
    "minItems": (_min_items, _length_limit),
    "maxItems": (_max_items, _length_limit),
    "uniqueItems": (_unique_items, _unique_items_param),
    "minProperties": (_min_properties, _length_limit),
    "maxProperties": (_max_properties, _length_limit),
    "enum": (_enum, _enum_params),
}

#: The built-in attachable constraint names (the closed name set, for callers that need
#: membership without the implementations).
BUILTIN_VALIDATOR_NAMES: frozenset[str] = frozenset(BUILTIN_VALIDATORS)

#: Each keyword's documented parameter name — the loader's direct-key normalization
#: target: ``minimum = 1`` ≡ the internal constraint ``{name: "minimum", params:
#: {limit: 1}}`` (the standard's shape, the keyword carrying its value directly;
#: handler/reference.md § Validators).
DIRECT_KEY_PARAM: dict[str, str] = {
    "minimum": "limit",
    "maximum": "limit",
    "exclusiveMinimum": "limit",
    "exclusiveMaximum": "limit",
    "multipleOf": "multiple",
    "minLength": "limit",
    "maxLength": "limit",
    "pattern": "pattern",
    "minItems": "limit",
    "maxItems": "limit",
    "uniqueItems": "unique",
    "minProperties": "limit",
    "maxProperties": "limit",
    "enum": "values",
}

#: The standard's own applicability mapping (JSON Schema draft 2020-12, the anchor the
#: built-in layer carries — handler/reference.md § Validators): the numeric keywords apply
#: to numeric types; the string keywords to strings; the array **cardinality** keywords to
#: the variable-length ``list[T]`` only and the array **distinctness** keyword ``uniqueItems``
#: to any array (``list[T]`` or a fixed-arity ``tuple``); the object-cardinality keywords to
#: open-keyed ``dict[str, T]``; ``enum`` to any declared type. The compose check unwraps an
#: ``Optional[...]`` first (a constraint applies to the present, non-null value) and rejects
#: an inapplicable keyword loud — the named fail-loud deviation from the standard's silent
#: ignore.
NUMERIC_KEYWORDS: frozenset[str] = frozenset(
    {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
)
STRING_KEYWORDS: frozenset[str] = frozenset({"minLength", "maxLength", "pattern"})
#: Array **cardinality** keywords — applicable to ``ListType`` only (the variable-length
#: array; a fixed-arity ``TupleType``'s length is structural, so a cardinality keyword there
#: is the rejected-loud inapplicable class).
ARRAY_CARDINALITY_KEYWORDS: frozenset[str] = frozenset({"minItems", "maxItems"})
#: Array **distinctness** keyword(s) — applicable to ANY array (``ListType`` OR a fixed-arity
#: ``TupleType``). Distinctness is orthogonal to cardinality: ``uniqueItems: true`` on
#: ``tuple[int, int]`` ("the two elements must differ") is meaningful and satisfiable, so the
#: fixed arity does not make it inapplicable (handler/reference.md § Validators).
ARRAY_DISTINCTNESS_KEYWORDS: frozenset[str] = frozenset({"uniqueItems"})
#: Object-cardinality keywords — applicable to ``DictType`` (the open-keyed object; a
#: fixed-field ``NestedType``'s property count is structural, so a cardinality keyword there
#: is the rejected-loud inapplicable class).
OBJECT_KEYWORDS: frozenset[str] = frozenset({"minProperties", "maxProperties"})
