"""The single-dispatch kernel — construct-once / invoke-many (Phase 2).

The engine-constructed dispatch wrapper R-handler-001 fixes
(``conjured/docs/components/handler/reference.md``; ``components/pipeline/reference.md``
§ Pipeline load lifecycle stage 4 "Engine-side dispatch construction"), shaped as the
two-phase kernel canon's lifecycle implies:

- :func:`construct` runs **once at compose** — it holds the generated Pydantic models,
  resolves each binding's delivery (deep-freezing ``delivery = "reference"`` values
  exactly once), and returns the dispatch callable.
- The returned ``dispatch_callable(*, reads, ctx)`` runs **per dispatch**: deliver
  bindings per their delivery selector → input-validate ``reads`` → call the bare
  function with **exactly the union the engine built from the TOML** → output-validate
  the return per the R-handler-001/output-validation three-way routing.

**The call is built from the declaration, never from the function's signature** — the
kwarg set is the graph node's input ports ∪ the resolved bindings ∪ ``services`` iff a
service-typed binding is declared. A function whose real parameters do not accept that
union blows up loud right here (the intended "the TOML lied" signal); a deliberately
faked ``__signature__`` cannot widen or narrow the call because nothing here reads the
signature. (Compose already rejected dishonest shapes from the real ``__code__`` —
``validator.resolve_handler`` step 6.)

**Binding delivery — the three decided branches** (trust-model vector 4; ``Delivery``
on ``ir/common.py`` is the selector — no second immutability model exists):

- ``COPY`` (default) — a fresh **deep** copy per dispatch (a shallow copy re-shares
  nested mutables, the exact leak vector 4 seals). Fail-soft by design: mutating your
  own copy is not a violation.
- ``REFERENCE`` — deep-frozen **once at construct**, the single frozen instance shared
  by every dispatch (``dict`` → ``MappingProxyType``, ``list``/``set`` → ``tuple``/
  ``frozenset``, recursive). Fail-**loud**: a write raises — mutating reference data is
  always a bug (handler/reference.md § Reference bindings).
- ``CompileBinding`` — engine-owned compiled artifact, delivered as-is (neither copied
  nor frozen; vector-4-copy-exempt). The artifact is produced once at construct by the
  binding-resolution pass (``runner.assemble`` → ``validator.resolve_compile``); this branch
  only forwards it unchanged.

**Validation boundaries.** Input and output validation are the SAME mechanism — a dict
validated against a model the generator (``validator.model_gen``) built from the
declared schema; the boundaries differ in schema and audit code only. Input failures
raise ``SchemaValidationError`` with audit ``C1.HALT_ON_INPUT_VALIDATION_ERROR.001``
(field paths ``reads.…``). Output routing per ``R-handler-001/output-validation``:
a returned key absent from ``output_schema`` → ``ContractViolation`` (undeclared
write); a declared output port omitted from the return dict → ``ContractViolation``
(missing declared write) — both top-level key-set facts; a value failing its declared
shape *within* a declared port (type/constraint, including a required field absent
inside a nested object) → ``SchemaValidationError`` with audit
``C1.HALT_ON_SCHEMA_VALIDATION_ERROR.001`` (paths ``output_schema.…``). Hooks have no
output model: the wrapper asserts the return is ``None``, else ``ContractViolation``.

**Dispatch context.** ``ctx = (pipeline_run_id, handler_position)`` is caller-supplied
(the test harness in Phase 2, the runner in Phase 3 — the same seam) and is the same
pair the canonical events carry and the adapter dispatch-kwargs require
(``caller_position`` IS ``handler_position`` — one value, threaded). ``position = 0``
is *correct* for a single handler, not a stand-in. The engine-generated
``pipeline_run_id`` form is minted in exactly one place (:func:`new_pipeline_run_id`)
so the deferred format refinement is a one-line change.

**Adapter-boundary capture (Phase 4).** The ``service_invocation`` canonical event is
emitted HERE, inside :class:`_BoundService` — the engine's wrapper around the service-type
adapter's ``invoke`` (``hash-model.md`` § Adapter-boundary capture). It is the structural
silent-fallback defense (R-handler-002): the payload is fixed from the backend's actual
response BEFORE control returns to the handler body, and the body — which receives the
response — has no path to mutate the captured (deep-copied) payload. **Service-kind
dispatches only**; a backend-SDK-emission hook reaches the same ``_BoundService`` but emits
no ``service_invocation`` (a hook writes no channels — § per-kind capture). The trainable
path (``construct_trainable``) does not route through ``_BoundService`` and emits the
``handler_enter`` / ``handler_exit`` pair, not this event.

**Not here (held, by decision):** the read-map *projection* from channel state (Phase 3
— the kernel receives the already-projected reads dict); ``PipelineFailure`` wrapping of
body exceptions (the Phase-3 runner's boundary — an exception out of the body surfaces
raw and loud here); the vector-3 layer-2 module-dict snapshot-restore (Phase-3 runner;
its restore-failure semantics are already decided fail-loud); the ``handler_enter`` /
``handler_exit`` pair + the run-lifecycle events (the runner's boundary — ``runner.run``).
"""

from __future__ import annotations

import copy
import datetime
import functools
import logging
import secrets
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Generator, Literal, Mapping, Protocol, TypeVar, cast

from pydantic import BaseModel, ValidationError

from conjured import events
from conjured.errors import (
    INPUT_VALIDATION_AUDIT_CODE,
    OUTPUT_VALIDATION_AUDIT_CODE,
    Check,
    ContractViolation,
    FieldValidationDetail,
    SchemaValidationError,
)
from conjured.ir.channel_types import (
    ChannelFieldType,
    DictType,
    ListType,
    NestedType,
    OptionalType,
    TupleType,
    canonical_token,
)
from conjured.ir.common import BindingBody, CompileBinding, Delivery, SchemaBinding
from conjured.ir.graph import GraphNode, Port
from conjured.validator.resolve_handler import HandlerEntry
from conjured.validator.resolve_validator import FIELD_VALIDATOR_ERROR_TYPE

#: The dispatch callable shape: keyword-only ``reads`` (the already-projected input-port
#: value dict) + ``ctx``; returns the validated output dict (``None`` for hooks).
DispatchCallable = Callable[..., dict[str, object] | None]


# ---------------------------------------------------------------------------
# Dispatch context — the (pipeline_run_id, handler_position) pair
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """The caller-supplied per-invocation context: the values the canonical events draw
    from (``hash-model.md`` § Canonical event types) plus the run-scoped delivery sink.
    ``pipeline_run_id`` + ``handler_position`` are the handler-bearing-event key (the
    adapter dispatch-kwargs carry the position as ``caller_position``);
    ``handler_position`` is the node's 0-indexed compose-time dispatch position (its
    identity), not a run-loop counter. ``pipeline_hash`` is the running pipeline's
    pipeline-hash — the runner threads ``runnable.pipeline_hash`` so the adapter-boundary
    ``service_invocation`` event names its pipeline without re-importing the hasher into
    the dispatch layer; it defaults empty for the Phase-2 dispatch-unit tests (no
    enclosing pipeline, no event consumer). ``stream_sink`` is the run-scoped token
    delivery callback (``run(..., stream_sink=...)``) — the runner sets it ONLY on the
    streamable terminal trainable's dispatch (``None`` everywhere else, and always when
    the consumer attached none); fragments delivered through it are provisional transport,
    never a channel value (pipeline/reference.md § Orchestration scope)."""

    pipeline_run_id: str
    handler_position: int
    pipeline_hash: str = ""
    stream_sink: "Callable[[str], None] | None" = None
    #: The run's absolute deadline on the monotonic clock (``started + timeout_ms``),
    #: ``None`` for an unbounded run — the deadline-propagation source
    #: (service-type/reference.md § Deadline propagation): the remaining budget is
    #: computed from it at the MOMENT of each adapter call (a body may burn time before
    #: invoking), never once per dispatch.
    deadline_monotonic: float | None = None


def remaining_budget_ms(deadline_monotonic: float | None) -> int | None:
    """The whole-run budget left at THIS moment, in ms, clamped at zero — the value a
    participating adapter surface receives as ``remaining_budget_ms``
    (service-type/reference.md § Deadline propagation). ``None`` deadline ⇒ ``None``
    (an unbounded run propagates no budget, not a large number)."""
    if deadline_monotonic is None:
        return None
    return max(0, int((deadline_monotonic - time.monotonic()) * 1000))


def new_pipeline_run_id() -> str:
    """Mint an engine-generated ``pipeline_run_id`` — the structured, sortable form
    ``run_<ISO-8601 basic UTC>_<short-random>`` (``hash-model.md`` § Canonical event
    types, e.g. ``run_20260506T142311Z_a3f9``). The **basic** ISO-8601 profile is
    colon-free, so the id rides a URI verbatim (no percent-encoding). The single minting
    point — the format is pinned; a change would be a one-line edit here. Consumers may
    supply their own id at invocation instead."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{secrets.token_hex(2)}"


# ---------------------------------------------------------------------------
# Resolved bindings + the three delivery branches
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedBinding:
    """One compose-resolved ``bindings.<name>`` joined to its declaration — the
    value↔declaration join the delivery branch keys on (``GraphNode.bindings`` carries
    values; the ``delivery`` selector lives on the handler's ``SchemaBinding``).
    ``value`` is the compose-resolved binding value (the effective supplied-or-default
    value); for a ``SchemaBinding`` it is **schema-validated at assemble** against a model
    generated over the binding's declared fields (``runner.assemble._validate_binding_value``
    — the same Pydantic validator the reads/output boundaries use, so a constraint on a
    binding field enforces; D4); for a ``CompileBinding`` it is the compiled artifact the
    binding-resolution pass produced (the passthrough branch — engine-owned, delivered as-is,
    not schema-validated)."""

    name: str
    body: BindingBody
    value: object


# guarantees: reference-binding-write-raises
def deep_freeze(value: object) -> object:
    """The reference-binding one-time deep freeze (handler/reference.md § Reference
    bindings — Mechanism): recursive standard-library immutables — ``dict`` →
    ``MappingProxyType`` over recursively-frozen values, ``list``/``set`` → ``tuple``/
    ``frozenset``, scalars already immutable. A write to the frozen value raises
    (fail-loud — correct for opt-in read-only data)."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(v) for v in value)
    return value


_DeliveryMode = Literal["copy", "reference", "compile"]


def _delivery_plan(
    resolved_bindings: tuple[ResolvedBinding, ...],
) -> tuple[tuple[str, _DeliveryMode, object], ...]:
    """Classify each binding once at construct: ``REFERENCE`` values are deep-frozen
    HERE (once — every dispatch shares the frozen instance); ``COPY`` values are held
    for per-dispatch deep copy; ``CompileBinding`` artifacts pass through untouched."""
    plan: list[tuple[str, _DeliveryMode, object]] = []
    for binding in resolved_bindings:
        if isinstance(binding.body, CompileBinding):
            plan.append((binding.name, "compile", binding.value))
        elif isinstance(binding.body, SchemaBinding):
            if binding.body.delivery is Delivery.REFERENCE:
                plan.append((binding.name, "reference", deep_freeze(binding.value)))
            else:
                plan.append((binding.name, "copy", binding.value))
        else:  # pragma: no cover - BindingBody is a closed discriminated union
            raise TypeError(f"unknown binding body for '{binding.name}'")
    return tuple(plan)


class _BindingDeliveryError(Exception):
    """Carrier for a **binding-delivery** failure — a non-deep-copyable COPY-mode binding (or
    transport) value whose per-dispatch ``copy.deepcopy`` raised. Binding delivery is the
    engine's OWN runner machinery (trust-model vector 4), not an author handler body or a
    service backend call, so the runner reads this carrier as the ``engine`` failure_category
    locus (``error-channel/reference.md`` § failure_category: ``"engine"`` covers "binding
    delivery, channel routing, merge"), mirroring how ``_ServiceOriginError`` carries the
    ``service`` locus out of the adapter boundary. Without the carrier the deepcopy failure
    escapes ``dispatch_callable`` raw and the runner's generic dispatch-boundary ``except``
    mis-attributes it by ``node_kind`` to the author's ``handler`` / ``service`` locus.

    Carries the failing binding ``name`` for the diagnostic message only; the structured
    payload's ``service_binding_name`` stays **null** — the engine locus has no failing
    *service* binding (a ``bindings.<name>`` value is not a service binding).

    Runner-package-internal by design: consumed only by the kernel walk (``run.py``), which
    unwraps it into the structured ``PipelineFailure``; never exported from the package."""

    __slots__ = ("binding_name",)

    def __init__(self, binding_name: str) -> None:
        super().__init__(
            f"binding {binding_name!r} could not be delivered "
            "(its COPY-mode value is not deep-copyable)"
        )
        self.binding_name = binding_name


def _deepcopy_for_delivery(name: str, value: object) -> object:
    """A per-dispatch COPY-delivery deep copy attributed to the binding-delivery (engine)
    locus: a non-deep-copyable value raises :class:`_BindingDeliveryError` (the ``engine``
    failure_category carrier) instead of a raw exception the dispatch-boundary ``except``
    would mis-attribute to the author body."""
    try:
        return copy.deepcopy(value)
    except Exception as exc:  # noqa: BLE001 — any deepcopy failure is an engine delivery fault
        # guarantees: failure-category-engine-is-binding-delivery
        raise _BindingDeliveryError(name) from exc


def _deliver(plan: tuple[tuple[str, _DeliveryMode, object], ...]) -> dict[str, object]:
    """The per-dispatch delivery: COPY → a fresh **deep** copy (vector 4 — a shallow
    copy would re-share nested mutables); REFERENCE → the shared frozen instance;
    compile → the engine-owned artifact as-is. A COPY value that is not deep-copyable
    raises :class:`_BindingDeliveryError` — the ``engine`` failure_category locus (binding
    delivery is engine machinery), never a raw exception mis-attributed to the author body."""
    delivered: dict[str, object] = {}
    for name, mode, value in plan:
        delivered[name] = _deepcopy_for_delivery(name, value) if mode == "copy" else value
    return delivered


# ---------------------------------------------------------------------------
# ValidationError → SchemaValidationError translation (one mechanism, two boundaries)
# ---------------------------------------------------------------------------


def _resolve_loc(
    ports: tuple[Port, ...], loc: tuple[object, ...]
) -> tuple[str, ChannelFieldType | None, tuple[tuple[int, str], ...]]:
    """Walk one Pydantic error ``loc`` against the declared port types, producing the
    canonical ``field_path`` suffix, the declared type at the deepest resolvable point
    (for ``expected_type``), and a declaration-order sort key (the ``field_validations``
    array is ordered by the violated schema's declaration order). Union-member marker
    segments Pydantic appends under an ``OptionalType`` are consumed silently (they are
    not fields)."""
    path = ""
    key: list[tuple[int, str]] = []
    current: ChannelFieldType | None = None
    # The current name→(index, type) level: starts at the port level.
    level: dict[str, tuple[int, ChannelFieldType]] | None = {
        p.name: (i, p.type) for i, p in enumerate(ports)
    }
    for seg in loc:
        # Union-member markers ('str', 'none', …) under an optional: descend, no emit.
        if isinstance(seg, str) and isinstance(current, OptionalType):
            current = current.inner
            level = (
                {f.name: (i, f.type) for i, f in enumerate(current.fields)}
                if isinstance(current, NestedType)
                else None
            )
            continue
        if isinstance(seg, int):
            path += f"[{seg}]"
            key.append((seg, ""))
            if isinstance(current, ListType):
                current = current.item
            elif isinstance(current, TupleType) and seg < len(current.items):
                current = current.items[seg]
            else:
                current = None
            level = None
            continue
        seg_name = str(seg)
        path += f".{seg_name}"
        if level is not None and seg_name in level:
            index, current = level[seg_name]
            key.append((index, seg_name))
            level = (
                {f.name: (i, f.type) for i, f in enumerate(current.fields)}
                if isinstance(current, NestedType)
                else None
            )
        elif isinstance(current, DictType):
            key.append((0, seg_name))
            current = current.value
            level = None
        else:
            # An undeclared key (extra) or an unresolvable segment: emit, type unknown.
            key.append((1_000_000_000, seg_name))
            current = None
            level = None
    return path, current, tuple(key)


def _truncated_repr(value: object) -> str | None:
    """``actual_value`` per the decided payload: ``repr()`` truncated to 256 chars with
    an elided-count marker; ``None`` (not ``"None"``) when the value is ``None``."""
    if value is None:
        return None
    rendered = repr(value)
    if len(rendered) > 256:
        rendered = rendered[:256] + f"…(+{len(rendered) - 256} chars)"
    return rendered


def _constraint_for(error: Mapping[str, object]) -> str:
    """Map a Pydantic error to the engine's ``constraint_violated`` names
    (error-channel/reference.md § SchemaValidationError payload — an open vocabulary):
    a field-validator verdict (the shim's ``conjured_field_validator`` custom error)
    → the validator's qualified name carried in the error ctx (a built-in attachable
    constraint name, or a third-party validator's qualified name — which is precisely
    why the vocabulary is open); ``missing`` → ``"required"`` (a required field
    absent within a declared port's value — the top-level output key-set case never
    reaches here, it is pre-routed to ContractViolation); ``extra_forbidden`` →
    ``"keys_subset_of"``; ``literal_error`` → ``"enum"``; ``None`` into a non-nullable
    field → ``"nullable"``; everything else → ``"type"``."""
    error_type = error["type"]
    if error_type == FIELD_VALIDATOR_ERROR_TYPE:
        # The shim always supplies the ctx (engine-constructed) — a missing key here
        # is an engine bug, fail loud via KeyError rather than mask it as "type".
        return str(error["ctx"]["constraint"])  # type: ignore[index]
    if error_type == "missing":
        return "required"
    if error_type == "extra_forbidden":
        return "keys_subset_of"
    if error_type == "literal_error":
        return "enum"
    if error.get("input") is None and "input" in error:
        return "nullable"
    return "type"


def _details_from_validation_error(
    exc: ValidationError, *, ports: tuple[Port, ...], prefix: str
) -> tuple[FieldValidationDetail, ...]:
    """One ``FieldValidationDetail`` per failed field (single-field collapse is
    forbidden — Pydantic's multi-error surface maps 1:1), ordered by the violated
    schema's declaration order."""
    keyed: list[tuple[tuple[tuple[int, str], ...], FieldValidationDetail]] = []
    for error in exc.errors():
        loc = tuple(error["loc"])
        suffix, declared_type, order_key = _resolve_loc(ports, loc)
        missing = error["type"] == "missing"
        value = error.get("input")
        keyed.append(
            (
                order_key,
                FieldValidationDetail(
                    field_path=f"{prefix}{suffix}",
                    expected_type=(
                        canonical_token(declared_type)
                        if declared_type is not None
                        else "(undeclared)"
                    ),
                    actual_type="absent" if missing else type(value).__name__,
                    actual_value=None if missing else _truncated_repr(value),
                    constraint_violated=_constraint_for(error),
                    message=str(error["msg"]),
                ),
            )
        )
    keyed.sort(key=lambda pair: pair[0])
    return tuple(detail for _, detail in keyed)


# ---------------------------------------------------------------------------
# ServicesProxy — the per-dispatch handler-side service surface
# ---------------------------------------------------------------------------


class _ServiceAdapter(Protocol):
    """The engine-used surface of a B2 adapter instance, as a typing Protocol only —
    the adapter CONTRACT is canon's (service-type/reference.md; the construction
    lifecycle + closed dispatch-kwargs), and conformance is enforced by the resolution
    seals, not by this type. ``invoke_streaming`` exists only on a streamable backend
    (capability compose-checked upstream — ``check_streamable_backend``); the engine
    accesses it only under that check."""

    def invoke(self, **kwargs: object) -> Mapping[str, object]: ...

    def invoke_streaming(self, **kwargs: object) -> Generator[str, None, object]: ...


@dataclass(frozen=True, slots=True)
class ServiceBindingRuntime:
    """The compose-resolved runtime half of one ``service_bindings`` entry: the B2
    adapter instance (one per composition, identity-only constructor —
    ``the-service-type-adapter/construction-lifecycle``), the composition-fixed
    ``[config_schema]`` kwarg values, and the deployment's ``transport.<name>`` block
    for the binding — the bound service-type's transport reaches the adapter through
    this same record whether the node is a service handler or a backend-SDK-emission
    hook (the B2 unification; the block compose's transport-coverage check validates
    is the block assembly delivers)."""

    name: str
    adapter: object
    config: Mapping[str, object]
    transport_extra: Mapping[str, object]
    #: Deadline-propagation participation for the ``invoke`` surface — does the resolved
    #: adapter's ``invoke`` declare ``remaining_budget_ms``? Compose-derived at assemble
    #: from the same real ``__code__`` the signature seal walked
    #: (``validator.resolve_adapter.declares_remaining_budget``); dispatch passes the
    #: budget iff this is set (service-type/reference.md § Deadline propagation).
    accepts_budget: bool = False


@dataclass(frozen=True, slots=True)
class _ServiceInvocationContext:
    """The fields :class:`_BoundService` needs to emit a ``service_invocation`` event at
    the adapter boundary (``hash-model.md`` § Canonical event types). Built per dispatch
    in the dispatch wrapper (the compose-time identity + the per-dispatch ``ctx``).
    ``None`` on a hook's bound service — only a **service-kind** dispatch emits this event
    (§ per-kind capture). ``handler_qualified_name`` is the as-written node name, so the
    event agrees with the same dispatch's ``handler_enter`` / ``handler_exit`` (the
    descriptive name a consumer joins on for legibility)."""

    handler_qualified_name: str
    handler_position: int
    pipeline_hash: str
    pipeline_run_id: str


class _ServiceOriginError(Exception):
    """Internal carrier — wraps an exception that escaped a service adapter's ``invoke()`` so the
    runner's dispatch-boundary wrap can attribute ``failure_category="service"`` + the failing binding
    **structurally** (from where the failure escaped, NOT by sniffing the exception name). Never
    surfaces: the runner unwraps it to the underlying cause (its ``__cause__``) and constructs the
    ``PipelineFailure``. Mirrors the ``FieldValidatorFailure`` carrier pattern.

    Runner-package-internal by design: consumed only by the kernel walk (``run.py``), which
    unwraps it into the structured ``PipelineFailure``; never exported from the package."""

    __slots__ = ("binding_name",)

    def __init__(self, binding_name: str) -> None:
        super().__init__(f"service adapter {binding_name!r} raised")
        self.binding_name = binding_name


class _CaptureError(Exception):
    """Carrier for an **adapter-boundary capture** failure — a non-deep-copyable
    ``service_invocation`` payload (the ``input_payload`` domain kwargs or the ``output_payload``
    backend response) whose capture ``copy.deepcopy`` raised. The capture is the engine's OWN
    silent-fallback defense (R-handler-002; ``hash-model.md`` § Adapter-boundary capture): it runs
    AFTER ``adapter.invoke`` already returned (the response is in hand), so a deepcopy failure here
    is the engine's capture machinery — NOT the service backend call (which succeeded) and NOT the
    author handler body. The runner reads this carrier as the ``engine`` failure_category locus
    (``error-channel/reference.md`` § failure_category: ``"engine"`` is the engine's own runner
    machinery — an internal runner operation), mirroring how ``_BindingDeliveryError`` carries the
    binding-delivery deepcopy failure and ``_ServiceOriginError`` carries the ``service`` locus.
    Without the carrier the deepcopy failure escapes through the body raw and the runner's generic
    dispatch-boundary ``except`` mis-attributes it by ``node_kind`` to the author's ``handler``
    locus (a service node's else-branch) — a wrong blame label that becomes training data.

    Carries the failing ``side`` (``"input"`` / ``"output"``) for the diagnostic message only; the
    structured payload's ``service_binding_name`` stays **null** — the engine locus has no failing
    *service* binding (the backend call already returned successfully).

    Runner-package-internal by design: consumed only by the kernel walk (``run.py``), which
    unwraps it into the structured ``PipelineFailure``; never exported from the package."""

    __slots__ = ("side",)

    def __init__(self, side: str) -> None:
        super().__init__(
            f"the service_invocation {side} payload could not be captured "
            "(it is not deep-copyable)"
        )
        self.side = side


_Captured = TypeVar("_Captured")


def _deepcopy_for_capture(side: str, value: _Captured) -> _Captured:
    """A per-dispatch adapter-boundary capture deep copy attributed to the capture (engine) locus:
    a non-deep-copyable payload raises :class:`_CaptureError` (the ``engine`` failure_category
    carrier) instead of a raw exception the dispatch-boundary ``except`` would mis-attribute to the
    author body. The twin of :func:`_deepcopy_for_delivery` at the capture boundary."""
    try:
        return copy.deepcopy(value)
    except Exception as exc:  # noqa: BLE001 — any deepcopy failure is an engine capture fault
        # guarantees: failure-category-engine-is-capture
        raise _CaptureError(side) from exc


class _BoundService:
    """One ``services.<name>`` attribute: wraps the adapter's ``invoke`` with the
    closed engine-supplied dispatch-kwargs (service-type/reference.md § Closed
    dispatch-kwargs). The handler body supplies only the domain kwargs, which the proxy
    packages as ``input_payload``; the body cannot reach inside the adapter.

    ``si`` is the ``service_invocation`` emission context — present for a service-kind
    dispatch, ``None`` for a hook's bound service (a hook emits no ``service_invocation``).
    When present, ``invoke`` captures the adapter-boundary event from the backend's actual
    response BEFORE returning it to the body (the structural silent-fallback defense)."""

    __slots__ = (
        "_runtime", "_caller_qualified_name", "_caller_position", "_si",
        "_deadline_monotonic",
    )

    def __init__(
        self,
        runtime: ServiceBindingRuntime,
        caller_qualified_name: str,
        caller_position: int,
        si: "_ServiceInvocationContext | None",
        deadline_monotonic: float | None = None,
    ) -> None:
        self._runtime = runtime
        self._caller_qualified_name = caller_qualified_name
        self._caller_position = caller_position
        self._si = si
        self._deadline_monotonic = deadline_monotonic

    def invoke(self, **domain_kwargs: object) -> object:
        runtime = self._runtime
        # cast: the resolution seals verified the adapter's invoke surface at compose
        # (the field stays object-typed — the engine never constructs adapters).
        adapter = cast(_ServiceAdapter, runtime.adapter)
        # Deadline propagation (service-type/reference.md § Deadline propagation): the
        # remaining budget is computed at THIS moment — the body may have burned run
        # time before calling — and passed iff the adapter's invoke declares the kwarg.
        # guarantees: deadline-budget-at-call-moment
        budget_kwargs: dict[str, object] = (
            {"remaining_budget_ms": remaining_budget_ms(self._deadline_monotonic)}
            if runtime.accepts_budget
            else {}
        )
        started = time.monotonic()
        try:
            response = adapter.invoke(
                input_payload=domain_kwargs,
                service_name=runtime.name,
                caller_qualified_name=self._caller_qualified_name,
                # caller_position IS handler_position — one dispatch position, two layer
                # names (R-service-type-003); threaded, never re-derived.
                caller_position=self._caller_position,
                **budget_kwargs,
                **runtime.config,
                **runtime.transport_extra,
            )
        except (ContractViolation, SchemaValidationError):
            raise  # engine-class exceptions are themselves the structural cause; propagate as-is.
        except Exception as exc:
            # guarantees: failure-category-service-is-adapter-origin
            # Tag the locus structurally at the adapter boundary so the runner reads
            # failure_category="service" from a FACT (which scope raised), never from the exception
            # name. The body does not catch this (fail-loud), so it reaches the dispatch-boundary wrap.
            raise _ServiceOriginError(runtime.name) from exc
        si = self._si
        if si is not None:
            # guarantees: service-payload-deepcopy
            # Adapter-boundary capture (hash-model § Adapter-boundary capture): the event
            # is fixed from the backend's response here, BEFORE `response` returns to the
            # body. Both payloads are deep-copied so the body — which gets `response` —
            # has no path to mutate the captured record (the body cannot lie about what
            # the backend produced; the silent-fallback divergence stays visible).
            # `correlation_id` pairs this event to the same dispatch's `handler_exit`,
            # the derived `(pipeline_run_id, handler_position)` label. The capture deepcopy
            # is the engine's OWN machinery and runs AFTER the adapter already returned, so a
            # non-deep-copyable payload raises the `engine`-locus `_CaptureError` carrier (via
            # `_deepcopy_for_capture`), never a raw exception the runner's generic dispatch
            # boundary would mis-blame on the author body — the capture twin of the
            # binding-delivery carrier.
            captured_input = _deepcopy_for_capture("input", domain_kwargs)
            captured_output = _deepcopy_for_capture("output", response)
            events.emit(
                events.ServiceInvocation(
                    handler_qualified_name=si.handler_qualified_name,
                    handler_position=si.handler_position,
                    input_payload=captured_input,
                    output_payload=captured_output,
                    pipeline_hash=si.pipeline_hash,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    pipeline_run_id=si.pipeline_run_id,
                    timestamp=events.now_iso(),
                    correlation_id=f"{si.pipeline_run_id}:{si.handler_position}",
                )
            )
        return response


class ServicesProxy:
    """The runtime object the runner constructs **at dispatch** and supplies as the
    reserved ``services`` kwarg — one attribute per declared ``service_bindings``
    entry, each exposing ``invoke(...)``; the only handler-side surface for service
    invocation (glossary ``{#servicesproxy}``). ``service_invocation`` is the per-dispatch
    ``service_invocation`` emission context (``None`` for a hook — no event); every bound
    service of one dispatch shares it (they share the dispatch's run-id and position)."""

    def __init__(
        self,
        runtimes: tuple[ServiceBindingRuntime, ...],
        *,
        caller_qualified_name: str,
        caller_position: int,
        service_invocation: "_ServiceInvocationContext | None" = None,
        deadline_monotonic: float | None = None,
    ) -> None:
        for runtime in runtimes:
            setattr(
                self,
                runtime.name,
                _BoundService(
                    runtime, caller_qualified_name, caller_position, service_invocation,
                    deadline_monotonic,
                ),
            )


# ---------------------------------------------------------------------------
# Output validation — the R-handler-001/output-validation three-way routing
# ---------------------------------------------------------------------------


def _validate_output(
    result: object,
    *,
    output_model: type[BaseModel],
    output_ports: tuple[Port, ...],
    qualified_name: str,
    schema_source: str,
    ctx: DispatchContext,
    section_path: str = "output_schema",
) -> dict[str, object]:
    """Validate a return dict (or adapter response — the trainable path validates the
    same way per R-handler-005's literal-equal kernel) against the declared output-port
    set + shapes, upstream of the write-map. Key-set facts first (→ ContractViolation),
    then value validation (→ SchemaValidationError). ``section_path`` names the schema
    section in the artifact ``schema_source`` points at (``output_schema`` in a handler
    TOML; ``trainable.output_schema`` in a composition TOML)."""
    if not isinstance(result, dict):
        raise ContractViolation(
            check=Check.RETURN_SHAPE, rule_id="R-handler-001",
            expected="a return dict keyed by output-port name (the sole admission gate)",
            actual=f"a {type(result).__name__}",
            remediation_hint="return {'<output_port>': value, ...} — exactly the "
                             "declared output_schema ports",
            file_path=schema_source, section_path=section_path,
            pipeline_run_id=ctx.pipeline_run_id,
        )
    declared = {p.name for p in output_ports}
    # Sort by repr: an author return dict may carry undeclared keys of mixed,
    # mutually-unorderable types — the diagnostic must still be the structured CV,
    # never a bare TypeError out of sorted().
    undeclared = sorted(result.keys() - declared, key=repr)
    if undeclared:
        raise ContractViolation(
            check=Check.UNDECLARED_OUTPUT_KEY, rule_id="R-handler-001",
            expected=f"return-dict keys within the declared output ports {sorted(declared)}",
            actual=f"undeclared key(s) {undeclared} in the return dict of "
                   f"'{qualified_name}'",
            remediation_hint="declare the port in output_schema, or stop returning the "
                             "key — there is no side-channel write surface",
            file_path=schema_source, section_path=section_path,
            pipeline_run_id=ctx.pipeline_run_id,
        )
    missing = sorted(declared - result.keys())
    if missing:
        raise ContractViolation(
            check=Check.MISSING_DECLARED_WRITE, rule_id="R-handler-001",
            expected=f"every declared output port written: {sorted(declared)}",
            actual=f"declared port(s) {missing} omitted from the return dict of "
                   f"'{qualified_name}'",
            remediation_hint="write every declared output port, or remove the port "
                             "from output_schema — a declared write is a contract",
            file_path=schema_source, section_path=section_path,
            pipeline_run_id=ctx.pipeline_run_id,
        )
    try:
        output_model.model_validate(result)
    except ValidationError as exc:
        raise SchemaValidationError(
            audit_code=OUTPUT_VALIDATION_AUDIT_CODE,
            handler_qualified_name=qualified_name,
            handler_position=ctx.handler_position,
            pipeline_run_id=ctx.pipeline_run_id,
            schema_source=schema_source,
            field_validations=_details_from_validation_error(
                exc, ports=output_ports, prefix="output_schema"
            ),
        ) from exc
    return result


def _validate_reads(
    reads: Mapping[str, object],
    *,
    reads_model: type[BaseModel],
    input_ports: tuple[Port, ...],
    qualified_name: str,
    schema_source: str,
    ctx: DispatchContext,
) -> None:
    """The input boundary (G11): validate the caller-projected reads dict against the
    reads model. Every failure — including a key-set mismatch — is the reads-side
    ``SchemaValidationError`` (audit ``C1.HALT_ON_INPUT_VALIDATION_ERROR.001``); the
    ContractViolation key-set routing is canon for the OUTPUT boundary only
    (R-handler-001/output-validation)."""
    if not isinstance(reads, Mapping):
        # Engine-internal misuse (the caller is the runner / test harness, never author
        # code) — not an author-facing contract surface.
        raise TypeError(
            f"reads must be a mapping of input-port name -> value, got {type(reads).__name__}"
        )
    try:
        reads_model.model_validate(dict(reads))
    except ValidationError as exc:
        raise SchemaValidationError(
            audit_code=INPUT_VALIDATION_AUDIT_CODE,
            handler_qualified_name=qualified_name,
            handler_position=ctx.handler_position,
            pipeline_run_id=ctx.pipeline_run_id,
            schema_source=schema_source,
            field_validations=_details_from_validation_error(
                exc, ports=input_ports, prefix="reads"
            ),
        ) from exc


def make_reads_validator(
    *,
    reads_model: type[BaseModel],
    input_ports: tuple[Port, ...],
    qualified_name: str,
    schema_source: str,
) -> Callable[..., None]:
    """Bind one node's reads-side validation boundary into a standalone callable —
    ``validate_reads(*, reads, ctx)`` raises the reads-side ``SchemaValidationError``
    (full field attribution) on a violating value, else returns ``None``.

    The single construction point for the boundary's two consumers: the dispatch
    wrapper's step 2 (every dispatch), and the runner's validate-then-copy reads
    projection (``runner.run`` — a consumer-seeded input value validates against this
    boundary BEFORE the vector-4 deep copy, so a non-deep-copyable wrong-typed seed
    surfaces as the ruled SVE, never a raw ``TypeError``)."""

    def validate_reads(*, reads: Mapping[str, object], ctx: DispatchContext) -> None:
        _validate_reads(
            reads, reads_model=reads_model, input_ports=input_ports,
            qualified_name=qualified_name, schema_source=schema_source, ctx=ctx,
        )

    return validate_reads


# ---------------------------------------------------------------------------
# construct() — the bare-function kinds (transform / service / hook)
# ---------------------------------------------------------------------------


def construct(
    handler_entry: HandlerEntry,
    graph_node: GraphNode,
    reads_model: type[BaseModel],
    output_model: type[BaseModel] | None,
    resolved_bindings: tuple[ResolvedBinding, ...],
    *,
    services: tuple[ServiceBindingRuntime, ...] = (),
    hook_transport: Mapping[str, object] = MappingProxyType({}),
) -> DispatchCallable:
    """Construct the dispatch wrapper for a resolved bare-function handler — run once
    at compose (G16). Holds the generated models and the delivery-classified bindings
    (reference values frozen here, once); returns the per-dispatch callable.

    A **service-kind** dispatch emits a ``service_invocation`` event at the adapter
    boundary; its ``pipeline_hash`` arrives per dispatch on the ``ctx`` (the runner threads
    ``runnable.pipeline_hash``), so ``construct`` carries only the compose-time identity
    (the kind gate + the as-written node name).

    ``services`` carries the compose-resolved runtime of each declared service-typed
    binding (exactly one for a service handler, at most one for a hook — cardinality is
    compose-checked upstream, R-handler-008/009); its presence is what adds the
    reserved ``services`` kwarg to the engine-built call, mirroring the declaration the
    signature was checked against. ``output_model`` is ``None`` exactly for hooks
    (hooks have no ``output_schema``; their return contract is ``None``).

    ``hook_transport`` carries a HOOK's deployment-supplied ``transport_schema`` values
    (the ``hook_transport."<qn>"`` block) — delivered to the emitting body as kwargs
    exactly like bindings, each value a fresh per-dispatch deep copy,
    deployment-supplied and hash-excluded (handler/reference.md § ``transport_schema``:
    delivery follows the emission boundary; the field names are already in the
    R-handler-001 signature union). Empty for non-hooks (kind discipline) and for the
    pure backend-SDK-emission hook (whose transport rides the binding's
    ``transport.<name>`` block to the adapter as ``transport_extra``).
    """
    kind = handler_entry.kind
    if kind == "hook":
        if output_model is not None:
            raise ValueError("a hook has no output_schema — pass output_model=None")
    elif output_model is None:
        raise ValueError(f"a {kind} handler requires an output model")
    if kind == "transform" and services:
        # Structurally unreachable from a valid compose (a transform declaration has no
        # service_bindings attribute) — guard the seam anyway, fail loud.
        raise ValueError("a transform has no external-call edge; services must be empty")
    if kind != "hook" and hook_transport:
        # Structurally unreachable from a valid compose (transport_schema is hook-only
        # by kind discipline) — guard the seam anyway, fail loud.
        raise ValueError(
            f"a {kind} handler has no transport_schema; hook_transport must be empty"
        )

    plan = _delivery_plan(resolved_bindings)
    transport_values = dict(hook_transport)
    input_port_names = tuple(p.name for p in graph_node.input_ports)
    output_ports = graph_node.output_ports
    qualified_name = handler_entry.qualified_name
    # service_invocation fires for service-kind dispatches only (§ per-kind capture); a
    # backend-SDK-emission hook reaches _BoundService too but emits nothing. The event's
    # handler name is the AS-WRITTEN node name, matching this dispatch's handler_enter /
    # handler_exit (the runner emits those off `graph_node.qualified_name`).
    emit_service_invocation = kind == "service"
    event_qualified_name = graph_node.qualified_name
    # The contract-document path a consumer opens (SVE.schema_source / CV.file_path) —
    # POSIX-rendered, matching canon's project-relative form ("handlers/npc_emotion.toml")
    # on every platform.
    schema_source = handler_entry.toml_path.as_posix()
    fn = handler_entry.callable
    validate_reads = make_reads_validator(
        reads_model=reads_model, input_ports=graph_node.input_ports,
        qualified_name=qualified_name, schema_source=schema_source,
    )

    def dispatch_callable(*, reads: Mapping[str, object], ctx: DispatchContext):
        # 1. Deliver bindings per their delivery branch (deep copy / shared frozen /
        #    compile passthrough) — and, for a stdlib-emission hook, the deployment's
        #    hook_transport."<qn>" values as kwargs, exactly like bindings: a fresh
        #    per-dispatch deep copy of each declared transport_schema field. Delivery is
        #    engine machinery: a non-deep-copyable COPY value raises _BindingDeliveryError,
        #    the `engine` failure_category locus (never an author-body mis-attribution).
        kwargs = _deliver(plan)
        for name, value in transport_values.items():
            kwargs[name] = _deepcopy_for_delivery(name, value)
        # 2. Input-validate the caller-projected reads (the reads-side SVE boundary).
        validate_reads(reads=reads, ctx=ctx)
        for port_name in input_port_names:
            kwargs[port_name] = reads[port_name]
        # 3. The services kwarg — a fresh proxy per dispatch (glossary: constructed at
        #    dispatch), iff the declaration carries a service-typed binding. The
        #    service_invocation emission context is built here (the per-dispatch ctx + the
        #    compose-time identity), None for non-service kinds (a hook emits no such event).
        if services:
            si_ctx = (
                _ServiceInvocationContext(
                    handler_qualified_name=event_qualified_name,
                    handler_position=ctx.handler_position,
                    pipeline_hash=ctx.pipeline_hash,
                    pipeline_run_id=ctx.pipeline_run_id,
                )
                if emit_service_invocation
                else None
            )
            # caller_qualified_name is the DISPATCHING NODE's qualified name (the
            # as-written label — service-type/reference.md § Closed dispatch-kwargs), so
            # an adapter's telemetry joins the same dispatch's canonical events by string
            # equality; the resolved handler name stays the SVE/CV attribution surface.
            kwargs["services"] = ServicesProxy(
                services,
                caller_qualified_name=event_qualified_name,
                caller_position=ctx.handler_position,
                service_invocation=si_ctx,
                deadline_monotonic=ctx.deadline_monotonic,
            )
        # 4. Call with exactly the TOML-built union. An honest signature mismatch
        #    raises TypeError right here — loud, uncaught (the "TOML lied" signal).
        result = fn(**kwargs)
        # 5. Output-validate per the three-way routing; hooks assert None.
        if kind == "hook":
            if result is not None:
                raise ContractViolation(
                    check=Check.HOOK_RETURN_NOT_NONE, rule_id="R-handler-001",
                    expected="a hook returns None (hooks write no channels; the runner "
                             "has no merge path for a hook return)",
                    actual=f"'{qualified_name}' returned a {type(result).__name__}",
                    remediation_hint="emit externally inside the body and return None",
                    file_path=schema_source,
                    pipeline_run_id=ctx.pipeline_run_id,
                )
            return None
        assert output_model is not None, (
            "engine bug: a channel-writing kind assembled without an output model"
        )
        return _validate_output(
            result, output_model=output_model, output_ports=output_ports,
            qualified_name=qualified_name, schema_source=schema_source, ctx=ctx,
        )

    return dispatch_callable


# ---------------------------------------------------------------------------
# construct_trainable() — the engine-constructed trainable dispatch (no author body)
# ---------------------------------------------------------------------------


def construct_trainable(
    graph_node: GraphNode,
    *,
    adapter: object,
    binding_name: str,
    config: Mapping[str, object],
    transport_extra: Mapping[str, object],
    reads_model: type[BaseModel],
    output_model: type[BaseModel],
    schema_source: str,
    streamable: bool = False,
    accepts_budget_invoke: bool = False,
    accepts_budget_streaming: bool = False,
) -> DispatchCallable:
    """Construct the trainable composition node's dispatch — fully engine-generated as
    ``functools.partial(adapter.invoke, **config)`` against the bound trainable backend
    (R-handler-010: no author body; attempting to load one is rejected upstream). The
    ``partial`` binds only the compose-fixed config; the runner supplies the closed
    dispatch-kwargs at each dispatch, ``input_payload`` being the ``trainable.reads``
    projection (pipeline/reference.md lifecycle stage 4). The response validates
    against ``trainable.output_schema`` — the same artifact submitted as the backend's
    decode constraint (R-handler-005, the literal-equal kernel) — through the same
    output-validation path as every channel-writing kind.

    ``streamable`` is the composition's compose-fixed delivery selector: when true AND
    the per-dispatch ``ctx.stream_sink`` is attached, dispatch drives the adapter's
    ``invoke_streaming`` **generator** (capability compose-checked upstream —
    ``check_streamable_backend``), delivering each yielded raw text fragment to the
    consumer's sink from THIS layer — consumer code never executes inside adapter
    frames. A sink that itself raises takes the observation-plane wall
    (pipeline/reference.md § Pipeline invocation): the raise is absorbed, surfaced on
    the runner's operational logger, and the sink is detached for the rest of the
    dispatch — the generator is still driven to completion and the dispatch completes.
    The generator's RETURN value is the assembled emission, validated through the
    identical output boundary as the buffered path (validate-on-assembly): fragments
    are provisional transport, never a channel value. With no sink attached — or
    ``streamable = false`` — the buffered ``invoke`` path runs, byte-identical to the
    pre-streaming engine.

    ``schema_source`` is the trainable composition declaration's path (the contract
    document a consumer opens on a validation failure).
    """
    # cast: the resolution seals verified the adapter's invoke surface at compose (and
    # check_streamable_backend the streaming capability when `streamable` is set).
    backend = cast(_ServiceAdapter, adapter)
    partial = functools.partial(backend.invoke, **dict(config))
    streaming_partial = (
        functools.partial(backend.invoke_streaming, **dict(config))
        if streamable
        else None
    )
    output_ports = graph_node.output_ports
    qualified_name = graph_node.qualified_name
    transport = dict(transport_extra)
    validate_reads = make_reads_validator(
        reads_model=reads_model, input_ports=graph_node.input_ports,
        qualified_name=qualified_name, schema_source=schema_source,
    )

    def dispatch_callable(*, reads: Mapping[str, object], ctx: DispatchContext):
        validate_reads(reads=reads, ctx=ctx)
        sink = ctx.stream_sink
        # Deadline propagation (service-type/reference.md § Deadline propagation),
        # per surface: each call passes the remaining budget iff ITS surface declares
        # the kwarg; computed at the call moment from the run's deadline.
        def _budget(accepts: bool) -> dict[str, object]:
            return (
                {"remaining_budget_ms": remaining_budget_ms(ctx.deadline_monotonic)}
                if accepts
                else {}
            )

        if streaming_partial is not None and sink is not None:
            generator = streaming_partial(
                input_payload=dict(reads),
                service_name=binding_name,
                caller_qualified_name=qualified_name,
                caller_position=ctx.handler_position,
                **_budget(accepts_budget_streaming),
                **transport,
            )
            try:
                while True:
                    try:
                        fragment = next(generator)
                    except StopIteration as stop:
                        response = stop.value
                        break
                    if sink is None:
                        # Detached mid-dispatch: keep driving the backend to completion —
                        # the assembled emission is still the dispatch's value.
                        continue
                    try:
                        sink(fragment)
                    except Exception as exc:  # noqa: BLE001 — consumer sink raised; wall, never halt
                        # guarantees: stream-sink-consumer-isolated
                        # Fragments are provisional transport on the observation plane —
                        # they gate no value path — so a raising sink takes the
                        # absorb-log-detach wall (pipeline/reference.md § Pipeline
                        # invocation), never the dispatch halt: detach the sink, drive
                        # the generator to completion, validate and route the assembled
                        # emission as if the sink had kept up. The raise stays visible on
                        # the runner's operational logger; the surfacing itself is
                        # guarded so a raising log handler cannot re-enter the run (the
                        # emit-wall shape, conjured.events.emit).
                        sink = None
                        try:
                            logging.getLogger("conjured.runner").warning(
                                "stream_sink raised during fragment delivery and was "
                                "detached (the observation-plane wall — the run "
                                "completes; the fragment stream ends with no terminal "
                                "signal): handler=%r position=%d pipeline_run_id=%s "
                                "error=%r",
                                qualified_name, ctx.handler_position,
                                ctx.pipeline_run_id, exc,
                            )
                        except Exception:  # noqa: BLE001 — never let the surfacing itself re-enter the run
                            pass
            finally:
                generator.close()
        else:
            response = partial(
                input_payload=dict(reads),
                service_name=binding_name,
                caller_qualified_name=qualified_name,
                caller_position=ctx.handler_position,
                **_budget(accepts_budget_invoke),
                **transport,
            )
        return _validate_output(
            response, output_model=output_model, output_ports=output_ports,
            qualified_name=qualified_name, schema_source=schema_source, ctx=ctx,
            section_path="trainable.output_schema",
        )

    return dispatch_callable
