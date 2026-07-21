"""Field-validator resolution + compose-time binding — the **third sibling** resolution
path (``conjured/docs/architecture/handler-resolution.md`` § Resolution mechanism —
"Validators — the third sibling"; the contract is owned by R-handler-012,
``conjured/docs/components/handler/reference.md`` § Validators).

A field declaration's ``validators`` entry (a normalized :class:`~conjured.ir.channel_types.ValidatorSpec`)
resolves at compose to a **bound** validator — a bare kwarg-only pure function with the
entry's declared parameters partial-applied by the **engine** (engine-owned partial
application, the same construction the trainable dispatch uses; authors supply no
factory, closure, or callable — parameters are data only). The sequence, mirroring the
handler sibling with the handler steps **unchanged**:

- **Built-in constraints** (``validator/constraints.py`` — the JSON-Schema draft-2020-12
  validation keywords applicable to the declared type, by family: numeric / string / array /
  object / enum) attach as **direct field keys** and resolve mechanically from the engine
  table (:func:`resolve_builtin_constraint`) — no entry-points lookup runs for them
  (engine-reserved names). Their compose checks: keyword **applicability** to the field's
  declared type (the standard's own mapping, fail-loud — never JSON Schema's silent
  ignore), the very same step-6 signature check third-party validators get, and a
  param-*value* check (a malformed engine-read parameter is compose-knowable — never
  deferred to dispatch). Built-ins are **bare** field keys; a third-party validator is a
  **namespaced (dotted)** key — the two key-spaces are disjoint by construction (D8), so a
  registration can never shadow a standard keyword and there is no shadowing case to
  detect, and there is no ``validators`` list.
- **Third-party (dotted) names** resolve through the adapter-style selector (D8): the
  ``conjured.validators`` entry-points group is consulted FIRST keyed by the full qualified
  name (a validator name MUST be namespaced — a bare name fails loud at first resolution),
  with dotted-path module import as the fallback; a two-distribution collision on one
  qualified name fails loud. Steps 2–5 are the handler sequence unchanged: spec-locate with
  namespace-package rejection, the step-3 pre-import source-AST audit
  (R-handler-pure-module, **unchanged**), import, and the step-5 vector-2 function-shape
  check (R-handler-bare-function, **unchanged**). Import-class failures cite
  **R-handler-012** (the rule that owns "it resolves, binds, and signature-checks at
  compose or the pipeline does not load").
- **Step 6 (validator-specific)** — the signature check: kwarg-only with parameters
  exactly ``{value}`` ∪ the entry's declared parameter names; any mismatch — extra,
  missing, positional, ``**kwargs``/``*args`` — raises ``ContractViolation`` at compose
  (``Check.VALIDATOR_SIGNATURE``). Read from the real ``__code__``, the same un-fakeable
  surface as handler step 6.

**The verdict protocol is closed** (§ Validators "The verdict"; R-handler-012): ``None``
= pass; a string = the per-field failure message (the dispatch boundary surfaces it as
``SchemaValidationError`` with ``constraint_violated`` = the validator's qualified name);
**any raise is the validator's own failure, never a validation verdict**. The shim
(:func:`make_validator_shim`) realizes that protocol inside the generated Pydantic model:
a reason string becomes a ``PydanticCustomError`` (type ``"conjured_field_validator"``,
ctx carrying the qualified name) the dispatch translation maps; a raise is re-raised as
:class:`FieldValidatorFailure` — deliberately **not** a ``ValueError``, which Pydantic
would convert into a validation verdict, the exact masking R-handler-012 forbids — with
the underlying exception as ``__cause__``. The Phase-3 runner wraps that into
``PipelineFailure`` with ``cause_class`` = the underlying class; in Phase 2 it surfaces
raw and loud at the dispatch boundary (the same posture as body exceptions —
``runner/dispatch.py`` "not here, by decision").

Every failure in this module is a compose-time ``ContractViolation``; nothing here can
fail at runtime (the shim's two runtime surfaces — the validation verdict and the
validator's own failure — are the dispatch boundary's, by design).
"""

from __future__ import annotations

import functools
import math
import os
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel
from pydantic_core import PydanticCustomError

from conjured.errors import Check, ContractViolation
from conjured.canonical import canon_value
from conjured.ir.channel_types import (
    ChannelFieldType,
    DictType,
    FieldDecl,
    ListType,
    LiteralType,
    NestedType,
    OptionalType,
    Primitive,
    PrimitiveType,
    TupleType,
    ValidatorSpec,
    canonical_token,
)
from conjured.validator.constraints import (
    ARRAY_CARDINALITY_KEYWORDS,
    ARRAY_DISTINCTNESS_KEYWORDS,
    BUILTIN_VALIDATORS,
    NUMERIC_KEYWORDS,
    OBJECT_KEYWORDS,
    STRING_KEYWORDS,
)
from conjured.validator.resolve_handler import (
    check_function_shape,
    code_signature,
    load_entry_point,
    resolve_dotted_attribute,
    select_entry_point,
)

#: The validator entry-points group — the third sibling group beside
#: ``conjured.handlers`` and ``conjured.service_implementations``
#: (handler/reference.md § Validators; R-handler-012).
VALIDATOR_ENTRY_POINT_GROUP = "conjured.validators"

#: The reserved verdict-input kwarg every validator signature carries (R-handler-012:
#: parameters exactly ``{value}`` ∪ the entry's declared parameter names).
RESERVED_VALUE_KWARG = "value"


class FieldValidatorFailure(Exception):
    """A field validator **raised** (or broke the closed verdict protocol) — the
    validator's own failure, never a validation verdict (R-handler-012).

    Deliberately not a ``ValueError``: Pydantic converts a ``ValueError`` raised inside
    a model validator into a ``ValidationError``, which would mask the validator's own
    failure as a verdict — the exact conflation the rule forbids. This class rides raw
    through ``model_validate`` and out of the dispatch boundary (fail loud); the Phase-3
    runner wraps it into ``PipelineFailure`` with ``cause_class`` = the **underlying**
    exception's class (``__cause__`` here, set by the shim), per R-handler-012.
    """


@dataclass(frozen=True, slots=True)
class BoundValidator:
    """One compose-bound field validator: the qualified name (the
    ``constraint_violated`` value a failure carries — the bare built-in constraint name,
    or the third-party validator's resolved dotted name) and the engine-bound callable
    (declared parameters partial-applied; called as ``bound(value=…)``)."""

    qualified_name: str
    bound: Callable[..., object]


def _enum_values(spec: ValidatorSpec) -> list[object] | tuple[object, ...]:
    """The ``values`` list of an already-param-checked ``enum`` spec. Every caller runs
    after the keyword's own param-value check (``_enum_params``: a well-formed non-empty
    list), so a non-list here is an engine sequencing bug, not author input."""
    values = spec.params["values"]
    assert isinstance(values, (list, tuple)), (
        "engine bug: 'enum' coherence ran before the enum param-value check"
    )
    return values


# ---------------------------------------------------------------------------
# The R-handler-012 parameter gate (data-only; the reserved kwarg)
# ---------------------------------------------------------------------------


def _check_params(spec: ValidatorSpec, *, toml_path: str) -> None:
    """Parameters are **data only** — scalar/collection values, never a callable or
    expression (R-handler-012); canonicalizability is the structural data test (the
    same canonical form the pipeline-hash folds them in). The reserved ``value`` kwarg
    is the verdict input — a declared parameter named ``value`` would collide with it
    at the bound call (the union would silently absorb the collision)."""
    if RESERVED_VALUE_KWARG in spec.params:
        raise ContractViolation(
            check=Check.VALIDATOR_PARAMS, rule_id="R-handler-012",
            expected="validator parameters named anything but the reserved 'value' "
                     "kwarg (the verdict input the engine supplies per validation)",
            actual=f"validator '{spec.name}' declares a parameter named 'value'",
            remediation_hint="rename the parameter in the field key's params (and in the "
                             "validator's signature)",
            file_path=toml_path,
        )
    try:
        canon_value(dict(spec.params))
    except TypeError as exc:
        raise ContractViolation(
            check=Check.VALIDATOR_PARAMS, rule_id="R-handler-012",
            expected=f"validator '{spec.name}' parameters are data only — "
                     "scalar/collection values, never a callable or expression "
                     "(they fold into the pipeline-hash as the field's validator "
                     "configuration)",
            actual=f"a non-data parameter value ({exc})",
            file_path=toml_path,
        ) from exc


# ---------------------------------------------------------------------------
# Steps 4-5 for the third-party paths — import + the vector-2 shape seal
# ---------------------------------------------------------------------------


def _validator_shape_hint(qualified_name: str) -> str:
    """The validator path's vector-2 remediation (R-handler-012: a validator is a bare
    kwarg-only pure function) — supplied to the shared :func:`check_function_shape`."""
    return (
        f"'{qualified_name}' is not a bare function; field "
        "validators MUST be bare kwarg-only pure functions per "
        "R-handler-012"
    )


def _resolve_third_party(
    spec: ValidatorSpec, *, toml_path: str, audit_enforcement: bool = False
) -> tuple[str, Callable[..., object]]:
    """Resolve a third-party validator name to ``(qualified_name, function)`` through the
    sibling selector the adapter resolution uses (D8): the ``conjured.validators``
    entry-points group is consulted **FIRST keyed by the full qualified name** (an EP name
    may contain dots), with **dotted-path module resolution** as the fallback when no entry
    point carries the name. A two-distribution EP collision fails loud (``_entry_point_for``).

    **Validator names MUST be namespaced (D8 — the registration-time rule surfaced at first
    resolution).** A bare name is the closed standard-keyword space (the parser routes bare
    keys to built-ins or unknown-key CVs), so a bare name reaching here is a loud failure —
    a third-party validator can never be referenced or registered bare."""
    name = spec.name
    if "." not in name:
        raise ContractViolation(
            check=Check.HANDLER_MODULE_IMPORT, rule_id="R-handler-012",
            expected="a third-party validator name carries a namespace (a dot) — bare "
                     "names are the closed standard-keyword space, never a third-party "
                     "registration",
            actual=f"bare validator name '{name}'",
            remediation_hint="namespace the validator (e.g. 'mypkg.is_iso_date'); a "
                             "validator entry-point MUST be registered under a dotted name",
            file_path=toml_path,
        )
    # The validator selector: the entry-points group FIRST, keyed by the full qualified
    # name (an EP name may contain dots — D8 + the sibling adapter selector); a missing
    # registration falls through to the dotted-path leg; a two-registration collision
    # fails loud (R-handler-012 — no winner is picked). Both legs are the shared
    # resolution legs under the validator's rule/noun (R-handler-pure-module unchanged).
    ep = select_entry_point(
        VALIDATOR_ENTRY_POINT_GROUP, name, toml_path=toml_path,
        rule_id="R-handler-012", on_missing="none",
    )
    if ep is not None:
        resolved = load_entry_point(
            ep, name, toml_path=toml_path, rule_id="R-handler-012", what="validator",
            audit_enforcement=audit_enforcement,
        )
        # The resolved dotted form is the qualified name (the constraint_violated value).
        return f"{ep.module}.{ep.attr}", resolved
    # Dotted-path fallback — no entry point carries the qualified name.
    resolved = resolve_dotted_attribute(
        name, toml_path=toml_path, rule_id="R-handler-012", what="validator",
        audit_enforcement=audit_enforcement,
    )
    return name, resolved


# ---------------------------------------------------------------------------
# Step 6 — the validator signature check ({value} ∪ declared parameter names)
# ---------------------------------------------------------------------------


def _check_signature(
    fn, spec: ValidatorSpec, *, qualified_name: str, toml_path: str,
    is_builtin: bool = False,
) -> None:
    """The R-handler-012 signature contract, read from the real ``__code__`` (the same
    un-fakeable surface as handler step 6): kwarg-only, parameter set exactly the
    reserved ``value`` plus the entry's declared parameter names; collectors and
    positionals reject. The same check polices built-in parameter names — a wrong or
    missing built-in param is a signature-union mismatch like any other — but the
    remediation direction flips for a built-in (``is_builtin``): the signature is
    engine-owned, so the actionable edit is the field KEY, never the function."""
    declared = frozenset({RESERVED_VALUE_KWARG} | set(spec.params))
    sig = code_signature(fn.__code__)
    if sig.has_varargs:
        raise ContractViolation(
            check=Check.VALIDATOR_SIGNATURE, rule_id="R-handler-012",
            expected="a kwarg-only validator signature with no *args collector",
            actual=f"'{qualified_name}' declares a *args collector (real __code__)",
            remediation_hint="remove the *args collector; declare exactly 'value' plus "
                             "the entry's declared parameter names",
            file_path=toml_path,
        )
    if sig.has_varkwargs:
        raise ContractViolation(
            check=Check.VALIDATOR_SIGNATURE, rule_id="R-handler-012",
            expected="a kwarg-only validator signature with no **kwargs collector",
            actual=f"'{qualified_name}' declares a **kwargs collector (real __code__)",
            remediation_hint="remove the **kwargs collector; declare exactly 'value' "
                             "plus the entry's declared parameter names",
            file_path=toml_path,
        )
    if sig.positional:
        raise ContractViolation(
            check=Check.VALIDATOR_SIGNATURE, rule_id="R-handler-012",
            expected="a kwarg-only validator signature (every parameter keyword-only)",
            actual=f"'{qualified_name}' declares positional parameter(s) {list(sig.positional)}",
            remediation_hint="make every parameter keyword-only: def validator(*, value, ...)",
            file_path=toml_path,
        )
    actual = sig.kwonly
    if actual != declared:
        missing = sorted(declared - actual)
        extra = sorted(actual - declared)
        if is_builtin:
            # The built-in's signature is engine-owned — the author cannot edit it.
            # The actionable direction is the FIELD KEY: parameters the built-in takes
            # but the key omits must be ADDED; parameters the key declares but the
            # built-in does not take must be REMOVED.
            entry_directions = []
            if extra:
                added = "; ".join(f"{p} = <value>" for p in extra)
                entry_directions.append(
                    f"add {added} to the '{qualified_name}' field key"
                )
            if missing:
                entry_directions.append(
                    f"remove parameter(s) {missing} from the '{qualified_name}' field key "
                    "(the built-in does not take them)"
                )
            hint = (
                f"'{qualified_name}' is an engine built-in (its signature is fixed); "
                + "; ".join(entry_directions)
            )
        else:
            hint = (
                f"'{qualified_name}' signature does not match the field key's parameters; "
                f"missing kwargs: {missing}; extra kwargs: {extra}"
            )
        raise ContractViolation(
            check=Check.VALIDATOR_SIGNATURE, rule_id="R-handler-012",
            expected=f"keyword-only parameters exactly {{value}} ∪ the entry's declared "
                     f"parameter names: {sorted(declared)}",
            actual=f"signature parameters {sorted(actual)}",
            remediation_hint=hint,
            file_path=toml_path,
        )


# ---------------------------------------------------------------------------
# The resolution entry — resolve, seal, signature-check, bind
# ---------------------------------------------------------------------------


def _applicable_base(field_type: ChannelFieldType) -> ChannelFieldType:
    """The type the constraint applies to: a constraint applies to the present, non-null
    value, so an ``Optional[...]`` wrapper unwraps first (nullability is the type token's
    axis, never a constraint's — handler/reference.md § Validators, Nullable fields)."""
    return field_type.inner if isinstance(field_type, OptionalType) else field_type


def _check_applicability(
    spec: ValidatorSpec, field_type: ChannelFieldType, *, toml_path: str
) -> None:
    """The standard's own applicability mapping, fail-loud (handler/reference.md
    § Validators — the named deviation from JSON Schema's silent ignore): the numeric
    keywords (incl. ``multipleOf``) apply to numeric types; ``minLength`` / ``maxLength`` /
    ``pattern`` to strings; the array **cardinality** keywords (``minItems`` / ``maxItems``)
    to the variable-length ``list[T]`` only; the array **distinctness** keyword
    (``uniqueItems``) to **any** array — a ``list[T]`` or a fixed-arity ``tuple``; the
    object-cardinality keywords (``minProperties`` / ``maxProperties``) to open-keyed
    ``dict[str, T]``; ``enum`` to any declared type. The **cardinality** keywords target the
    **variable-cardinality** channel types — a fixed-arity ``TupleType`` or fixed-field
    ``NestedType`` fixes its cardinality structurally, so a cardinality keyword there can
    never apply and is rejected. **Distinctness is orthogonal to cardinality** — the fixed
    arity says nothing about whether the elements differ — so ``uniqueItems`` is applicable to
    a tuple as well as a list. An inapplicable keyword is a composition defect —
    ContractViolation at compose, never a silent no-op."""
    base = _applicable_base(field_type)
    if spec.name in NUMERIC_KEYWORDS:
        applicable = isinstance(base, PrimitiveType) and base.primitive in (
            Primitive.INT, Primitive.FLOAT,
        )
        family = "numeric types (int / float)"
    elif spec.name in STRING_KEYWORDS:
        applicable = isinstance(base, PrimitiveType) and base.primitive is Primitive.STR
        family = "string fields"
    elif spec.name in ARRAY_CARDINALITY_KEYWORDS:
        applicable = isinstance(base, ListType)
        family = "list / array fields (list[T])"
    elif spec.name in ARRAY_DISTINCTNESS_KEYWORDS:
        applicable = isinstance(base, (ListType, TupleType))
        family = "any array — list[T] or tuple[...]"
    elif spec.name in OBJECT_KEYWORDS:
        applicable = isinstance(base, DictType)
        family = "open-keyed object fields (dict[str, T])"
    elif spec.name == "enum":  # applicable to any declared type
        return
    else:  # engine-internal: a built-in keyword with no applicability family registered
        raise ValueError(
            f"_check_applicability: built-in keyword '{spec.name}' has no applicability "
            "family — every BUILTIN_VALIDATORS keyword must be categorized "
            "(numeric / string / array / object / enum)"
        )
    if not applicable:
        raise ContractViolation(
            check=Check.VALIDATOR_PARAMS, rule_id="R-handler-012",
            expected=f"the built-in '{spec.name}' constraint applies to {family} "
                     "(the JSON-Schema applicability mapping; an inapplicable keyword "
                     "is rejected loud, never silently ignored)",
            actual=f"'{spec.name}' declared on a field of type "
                   f"'{canonical_token(field_type)}'",
            remediation_hint="remove the inapplicable keyword, or re-type the field to "
                             "the family the keyword constrains",
            file_path=toml_path,
        )


def _check_enum_literal_subset(
    spec: ValidatorSpec, field_type: ChannelFieldType, *, toml_path: str
) -> None:
    """Enum-on-``Literal`` coherence (handler/reference.md § Validators — "Enum-on-``Literal``
    coherence"): where an ``enum`` keyword sits on a ``Literal``-typed field, its value set MUST
    be a **subset** of the Literal's members. The engine-side model enforces the type and the enum
    together, so their intersection is the accepted value space — a value the Literal forecloses can
    never pass, and a fully disjoint enum admits nothing (every dispatch of the field would fail).
    That contradiction is knowable at compose, so it raises a ``ContractViolation`` here rather than
    deferring to a per-dispatch ``SchemaValidationError`` — the same fail-loud-at-compose posture as
    the applicability deviation. Subset membership is **exact-type** (the same anti-coercion rule as
    ``model_gen``'s Literal realization — ``True`` is not ``1``; NOTE this is deliberately stricter
    than the post-D6 ``_enum`` verdict's JSON-Schema numeric equality: a Literal's value space is its
    exact members, and the wire renders enum members as written, so lexical-equality subset keeps the
    written enum identical to the enforced intersection), which
    also preserves the literal-equal seal (R-handler-005) where the field renders to a
    [trainable](#trainable) output wire: the enforced intersection equals the enum, so the submitted
    wire constraint and the engine-side model enforce one identical predicate by construction.

    Runs after the ``enum`` param-value check (a well-formed non-empty ``values`` list), so this only
    inspects an already-valid enum. Non-``enum`` keywords and non-``Literal`` fields are a no-op."""
    base = _applicable_base(field_type)
    if spec.name != "enum" or not isinstance(base, LiteralType):
        return
    members = base.values
    foreclosed = [
        v for v in _enum_values(spec)
        if not any(type(v) is type(m) and v == m for m in members)
    ]
    if foreclosed:
        rendered_members = ", ".join(repr(m) for m in members)
        rendered_bad = ", ".join(repr(v) for v in foreclosed)
        raise ContractViolation(
            check=Check.VALIDATOR_PARAMS, rule_id="R-handler-012",
            expected=f"the 'enum' values on a Literal-typed field to be a subset of the "
                     f"Literal's members [{rendered_members}] (handler/reference.md § Validators "
                     "— Enum-on-Literal coherence)",
            actual=f"enum value(s) [{rendered_bad}] are not Literal members — the Literal "
                   f"forecloses them, so the field's accepted value space (the type∩enum "
                   f"intersection) would reject every such value at dispatch",
            remediation_hint="drop the foreclosed value(s) from the enum, or widen the Literal "
                             "type to include them",
            file_path=toml_path,
        )


# guarantees: enum-bound-coherence
def check_enum_bound_coherence(
    field: FieldDecl, *, toml_path: str | os.PathLike[str]
) -> None:
    """Enum-vs-length-bound coherence — the generalization of Enum-on-``Literal`` coherence
    (:func:`_check_enum_literal_subset`) to a co-declared **value constraint**. Where a field
    carries BOTH an ``enum`` keyword and a length bound (``minLength`` / ``maxLength``), every
    enum member MUST satisfy the bound. The engine-side generated model enforces enum ∩ bound as
    the field's accepted value space, so a member the bound forecloses can never pass — and on a
    wire whose accepted set renders BOTH keywords (the GBNF grammar's accepted matrix is
    ``{enum, minLength, maxLength}`` — ``lib/gbnf_trainable.py``), the submitted constraint
    renders the enum alternation but the enum decode path drops the length repetition
    (``adapters/gbnf.py`` ``ref``: the ``enum`` branch returns before the length branch), so the
    grammar would admit a member the engine-side model rejects. That splits the literal-equal
    seal (R-handler-005) into two predicates (submitted-wire vs engine-model). The contradiction
    is compose-knowable, so it raises here rather than deferring to a per-dispatch
    ``SchemaValidationError`` — the same fail-loud-at-compose posture as Enum-on-``Literal``
    coherence and the applicability deviation (handler/reference.md § Validators).

    Reuses the built-in bound CHECK FUNCTIONS (``BUILTIN_VALIDATORS`` — ``validator/constraints.py``)
    the engine-side model binds, so the coherence verdict and the model's enforcement can never
    diverge (one problem, one solution). A length bound applies only to strings — the applicability
    check already restricts ``minLength`` / ``maxLength`` to a ``str`` field, so a valid enum member
    is a string; a non-string member is foreclosed by the type layer (a separate coherence concern)
    and is skipped here, never fed to ``len()``. Runs after each keyword's own param/applicability
    check (from :func:`~conjured.validator.model_gen.build_model`'s resolution loop), so ``values``
    and each ``limit`` are already well-formed. A field lacking either an ``enum`` or a length
    bound is a no-op — this only fires on the co-declaration."""
    enum_spec = next((s for s in field.validators if s.name == "enum"), None)
    if enum_spec is None:
        return
    bound_specs = [s for s in field.validators if s.name in ("minLength", "maxLength")]
    if not bound_specs:
        return
    toml_str = str(toml_path)
    for value in _enum_values(enum_spec):
        if not isinstance(value, str):
            # A non-string member is foreclosed by the string type layer — the sibling
            # :func:`check_enum_type_coherence`'s territory, not the length bound's —
            # and len() would raise on it. Skip.
            continue
        for spec in bound_specs:
            check_fn, _ = BUILTIN_VALIDATORS[spec.name]
            problem = check_fn(value=value, **dict(spec.params))
            if problem is not None:
                raise ContractViolation(
                    check=Check.VALIDATOR_PARAMS,
                    rule_id="R-handler-012",
                    expected=(
                        "every 'enum' member on a length-bounded field satisfies the "
                        "co-declared length bound(s) — the engine-side model enforces "
                        "enum ∩ bound, so a member the bound forecloses can never pass (and "
                        "would split the literal-equal seal on a wire that renders the enum "
                        "alternation but not the length)"
                    ),
                    actual=(
                        f"enum member {value!r} violates the co-declared '{spec.name}' "
                        f"bound ({problem})"
                    ),
                    remediation_hint=(
                        "drop the foreclosed member from the enum, or relax the "
                        f"'{spec.name}' bound to admit it"
                    ),
                    file_path=toml_str,
                )


def _member_satisfiable(member: object, field_type: ChannelFieldType) -> bool:
    """Whether SOME value the field's strict type realization admits can equal ``member``
    under the ``enum`` keyword's JSON-Schema value-equality (``constraints._json_equal``):
    the numeric family is one JSON type (an integral float member is satisfiable on an
    ``int`` field through its integer twin, and an int member on a ``float`` field through
    its float twin); a boolean is its own JSON type (never satisfiable on a numeric field,
    and a numeric member never on a ``bool`` field); arrays / objects recurse. ``None`` is
    never satisfiable — the shared shim's null-skip means the constraint layer never sees
    an admitted ``None``, so a ``None`` member can match nothing. A ``Literal`` descriptor
    returns True here — Enum-on-``Literal`` subset coherence owns that case (exact-type);
    any descriptor kind this walk does not model likewise returns True (the check is
    additive — an unmodeled kind keeps the status quo, and the per-dispatch boundary still
    validates)."""
    if member is None:
        return False
    if isinstance(field_type, OptionalType):
        return _member_satisfiable(member, field_type.inner)
    if isinstance(field_type, PrimitiveType):
        prim = field_type.primitive
        if prim is Primitive.BOOL:
            return isinstance(member, bool)
        if prim is Primitive.STR:
            return isinstance(member, str)
        if prim is Primitive.INT:
            if isinstance(member, bool):
                return False
            if isinstance(member, int):
                return True
            return isinstance(member, float) and math.isfinite(member) and member.is_integer()
        if prim is Primitive.FLOAT:
            return not isinstance(member, bool) and isinstance(member, (int, float))
        if prim is Primitive.BYTES:
            return isinstance(member, bytes)
        return True  # pragma: no cover - Primitive is a closed enum
    if isinstance(field_type, ListType):
        return isinstance(member, (list, tuple)) and all(
            _member_satisfiable(item, field_type.item) for item in member
        )
    if isinstance(field_type, DictType):
        return isinstance(member, dict) and all(
            isinstance(key, str) and _member_satisfiable(value, field_type.value)
            for key, value in member.items()
        )
    if isinstance(field_type, TupleType):
        return (
            isinstance(member, (list, tuple))
            and len(member) == len(field_type.items)
            and all(
                _member_satisfiable(item, item_type)
                for item, item_type in zip(member, field_type.items)
            )
        )
    if isinstance(field_type, NestedType):
        if not isinstance(member, dict):
            return False
        declared = {f.name: f.type for f in field_type.fields}
        if set(member) != set(declared):
            return False  # the generated model is closed (extra="forbid") + every field required
        return all(_member_satisfiable(value, declared[key]) for key, value in member.items())
    return True  # LiteralType (the subset check's territory) + any unmodeled kind


def check_enum_type_coherence(
    field: FieldDecl, *, toml_path: str | os.PathLike[str]
) -> None:
    """Enum member-vs-field-type coherence — the type arm beside
    :func:`check_enum_bound_coherence`'s length arm. Every ``enum`` member MUST be
    admissible under the field's declared type per the post-D6 value-space semantics
    (JSON-Schema numeric equality across the int/float family; bool its own JSON type;
    str unchanged): the engine-side model enforces type ∩ enum as the field's accepted
    value space, so a foreclosed member can never be the field's value — a dead member —
    and a fully type-disjoint enum admits nothing (every dispatch of the field would fail);
    on a trainable output wire that renders the enum alternation the submitted grammar
    would admit a member the engine-side model rejects, splitting the literal-equal seal
    (R-handler-005) into two predicates. The contradiction is
    compose-knowable, so it raises here (R-handler-012's keyword-coherence arm, the same
    fail-loud-at-compose posture as the length arm and Enum-on-``Literal`` coherence)
    rather than deferring to a per-dispatch ``SchemaValidationError`` storm.

    A ``Literal``-typed field is skipped — Enum-on-``Literal`` subset coherence
    (:func:`_check_enum_literal_subset`, exact-type) owns that case. Runs after each
    keyword's own param/applicability checks (from ``build_model``'s resolution loop), so
    ``values`` is already a well-formed non-empty list."""
    # guarantees: enum-type-coherence
    enum_spec = next((s for s in field.validators if s.name == "enum"), None)
    if enum_spec is None:
        return
    if isinstance(_applicable_base(field.type), LiteralType):
        return  # Enum-on-Literal subset coherence owns the Literal case
    toml_str = str(toml_path)
    for member in _enum_values(enum_spec):
        if not _member_satisfiable(member, field.type):
            raise ContractViolation(
                check=Check.VALIDATOR_PARAMS,
                rule_id="R-handler-012",
                expected=(
                    "every 'enum' member is admissible under the field's declared type "
                    "— the engine-side model enforces type ∩ enum as the accepted value "
                    "space (JSON-Schema numeric equality across int/float; bool its own "
                    "JSON type), so a member the type forecloses can never pass (and "
                    "would split the literal-equal seal on a wire that renders the enum "
                    "alternation)"
                ),
                actual=(
                    f"enum member {member!r} on field '{field.name}' is foreclosed by "
                    f"the field's declared type '{canonical_token(field.type)}'"
                ),
                remediation_hint=(
                    "drop the foreclosed member from the enum, or change the field's "
                    "declared type to admit it"
                ),
                file_path=toml_str,
            )


def resolve_builtin_constraint(
    spec: ValidatorSpec,
    *,
    field_type: ChannelFieldType,
    toml_path: str | os.PathLike[str],
) -> BoundValidator:
    """Resolve one **bare built-in constraint** (a standard-keyword entry in the field's single
    ``FieldDecl.validators`` tuple — D8)
    to its :class:`BoundValidator` — compose-time only. The built-in layer's compose
    checks (handler/conformance.md § Validator resolution and parameter binding):
    keyword **applicability** to the field's declared type (the standard's own mapping,
    fail-loud), the ``{value}`` ∪ params **signature** check (the same step-6 discipline,
    engine-owned direction), keyword-value **well-formedness** (a non-numeric or
    non-finite bound, a non-compiling ``pattern``, an empty ``enum``), and **enum-on-``Literal``
    coherence** (an ``enum`` on a ``Literal``-typed field must be a subset of the Literal's
    members — the type∩enum contradiction is compose-knowable, so it fails loud here)."""
    toml_str = str(toml_path)
    _check_params(spec, toml_path=toml_str)
    builtin = BUILTIN_VALIDATORS.get(spec.name)
    if builtin is None:
        # Structurally unreachable through the loader (only BUILTIN_VALIDATOR_NAMES keys
        # normalize into constraints) — fail loud on engine-internal misuse.
        raise ValueError(
            f"resolve_builtin_constraint: {spec.name!r} is not a built-in constraint "
            "keyword (the loader normalizes only BUILTIN_VALIDATOR_NAMES direct keys)"
        )
    fn, param_check = builtin
    _check_applicability(spec, field_type, toml_path=toml_str)
    _check_signature(
        fn, spec, qualified_name=spec.name, toml_path=toml_str, is_builtin=True,
    )
    problem = param_check(spec.params)
    if problem is not None:
        raise ContractViolation(
            check=Check.VALIDATOR_PARAMS, rule_id="R-handler-012",
            expected=f"a well-formed value for the built-in '{spec.name}' constraint key",
            actual=problem,
            remediation_hint="fix the constraint key's value on the field declaration",
            file_path=toml_str,
        )
    # Enum-on-Literal coherence: an enum on a Literal-typed field must be a subset of the
    # Literal's members (the type∩enum contradiction is compose-knowable — fail loud here, never
    # a per-dispatch SchemaValidationError). Runs after param_check so `values` is well-formed.
    _check_enum_literal_subset(spec, field_type, toml_path=toml_str)
    # Engine-owned partial application — built-ins carry the bare constraint name (the
    # constraint_violated value a failure reports).
    return BoundValidator(
        qualified_name=spec.name,
        bound=functools.partial(fn, **dict(spec.params)),
    )


def resolve_field_validator(
    spec: ValidatorSpec, *, toml_path: str | os.PathLike[str],
    audit_enforcement: bool = False,
) -> BoundValidator:
    """Resolve one **namespaced (dotted) third-party validator** key to its
    :class:`BoundValidator` — compose-time only; every failure is a ``ContractViolation``
    (R-handler-012: it resolves, binds, and signature-checks at compose or the pipeline
    does not load; an unrecognized name or signature mismatch never defers to dispatch
    time).

    D8 — one grammar: a **bare** standard keyword is never routed here (the parser routes
    bare keys to built-ins via :func:`resolve_builtin_constraint`, or to an unknown-key CV);
    a third-party validator name MUST be namespaced (the namespace rule fails loud in
    :func:`_resolve_third_party`). The two key-spaces (bare standard, dotted third-party)
    are disjoint by construction, so there is no shadowing case to detect — the prior
    fail-loud shadowing check is retired.

    ``toml_path`` is the declaring artifact (the handler/composition TOML whose field
    carries the key) — the diagnostics' declaration-site locus. ``audit_enforcement`` (the
    deployment opt-in, threaded from stage-4 assembly through ``build_model``) gates the
    validator module's step-3 audit-stamp freshness check — a validator module is an
    in-scope module (handler/reference.md § Audit stamps), checked like a handler / adapter.
    """
    toml_str = str(toml_path)
    _check_params(spec, toml_path=toml_str)
    qualified_name, fn = _resolve_third_party(
        spec, toml_path=toml_str, audit_enforcement=audit_enforcement
    )
    check_function_shape(  # step 5 — the shared vector-2 seal, validator hint
        fn, toml_path=toml_str, hint=_validator_shape_hint(qualified_name),
    )
    _check_signature(  # step 6
        fn, spec, qualified_name=qualified_name, toml_path=toml_str,
    )
    # Engine-owned partial application — the same construction the trainable dispatch
    # uses; authors never write factories or closures (parameters are data only).
    return BoundValidator(
        qualified_name=qualified_name,
        bound=functools.partial(fn, **dict(spec.params)),
    )


# ---------------------------------------------------------------------------
# The verdict shim — None | str | raise, realized inside the generated model
# ---------------------------------------------------------------------------

#: The PydanticCustomError type the shim raises on a failure verdict; the dispatch
#: translation (``runner/dispatch.py`` ``_constraint_for``) maps it to the ctx-carried
#: qualified name (``constraint_violated``) and the reason string (``message``).
FIELD_VALIDATOR_ERROR_TYPE = "conjured_field_validator"


def _to_plain_data(value: object) -> object:
    """Convert a validated value to plain data for validator delivery (canon's delivery
    posture, handler/reference.md: "The delivered shape is plain data … never an
    attribute-bearing object"). A nested-object field validates into a generated
    ``BaseModel`` instance — delivered to the validator as the plain ``dict``
    (``model_dump(mode="python")``); containers recurse so a model anywhere inside a
    list/dict/tuple value is dumped too. The conversion is delivery-only: the shim
    returns the ORIGINAL value (verdict-only protocol — the field keeps its validated
    type)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, dict):
        return {k: _to_plain_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_to_plain_data(v) for v in value)
    return value


def make_validator_shim(bound: BoundValidator) -> Callable[[object], object]:
    """Wrap one :class:`BoundValidator` as the ``AfterValidator`` function the model
    generator attaches to the field's annotation (R-handler-012: the engine "wraps the
    bound validator into the field's generated Pydantic model"). Runs **after** the
    field's type validation — a validator constrains values beyond the type token.

    The closed verdict protocol: ``None`` → the value passes (returned unchanged);
    a ``str`` → the per-field failure (a ``PydanticCustomError`` the dispatch boundary
    surfaces as ``SchemaValidationError`` with ``constraint_violated`` = the qualified
    name and ``message`` = the returned string); anything else — a raise, or a
    non-``None``/non-``str`` return breaking the closed protocol — is the validator's
    **own** failure, re-raised as :class:`FieldValidatorFailure` (never a verdict).

    The validator's ``value`` kwarg receives **plain data** (:func:`_to_plain_data` —
    a nested-object field's generated-model instance arrives as the plain ``dict``);
    the shim still returns the ORIGINAL validated value — delivery is converted, the
    field's type is not.

    **Null-skip** (handler/reference.md § Validators, Nullable fields): a constraint
    applies to the present, non-null value — an admitted ``None`` on a nullable field
    passes the constraint layer untouched (nullability is the type token's axis, never
    a constraint's). The rule covers built-ins and third-party validators alike: the
    skip lives here, in the one shared shim.
    """

    def _shim(value: object) -> object:
        if value is None:
            # The type layer already admitted None (only an Optional[...] field can);
            # the constraint layer never sees it.
            return value
        try:
            verdict = bound.bound(value=_to_plain_data(value))
        except Exception as exc:
            raise FieldValidatorFailure(
                f"field validator '{bound.qualified_name}' raised "
                f"{type(exc).__name__} — a raise is the validator's own failure, "
                "never a validation verdict (R-handler-012)"
            ) from exc
        if verdict is None:
            return value
        if isinstance(verdict, str):
            raise PydanticCustomError(
                FIELD_VALIDATOR_ERROR_TYPE,
                "{reason}",
                {"reason": verdict, "constraint": bound.qualified_name},
            )
        raise FieldValidatorFailure(
            f"field validator '{bound.qualified_name}' returned a "
            f"{type(verdict).__name__} — the verdict protocol is closed: None (pass) "
            "or a one-line failure string (R-handler-012)"
        )

    return _shim
