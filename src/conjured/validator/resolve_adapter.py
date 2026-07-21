"""Service-implementation (adapter) resolution — the sibling mechanism to handler
resolution (``conjured/docs/architecture/handler-resolution.md`` § Resolution mechanism
— Adapters; ``components/service-type/reference.md`` § Service-impl dispatch contract;
R-service-type-002/003).

The same resolution sequence as ``resolve_handler`` against the
``conjured.service_implementations`` entry-points group — with the adapter's **own
selector**: the entry-points group is consulted FIRST, keyed by the **full service-type
qualified name** (dotted or not — an entry-point name may contain dots), with
dotted-path module resolution as the fallback when no entry point carries the name (a
service-type qualified name is a type identity, never coupled to the implementer's
module layout; the handler dot-presence selector would read every dotted name as a
module path and could never reach the group). Two further substitutions:

- **Step 3** applies the R-handler-pure-module **adapter-scope extension** (vector 7:
  no above-instance-scope mutable state — class variables, ``@lru_cache`` on methods,
  module-level state/I/O; instance state IS admissible, bounded by the engine-managed
  composition lifetime). ``validator.ast_audit`` owns the walker.
- **Step 5** is replaced by the class-shape requirement + the ``invoke()`` signature
  validation (an adapter is a class by construction — the function-shape check would
  reject it): ``invoke`` MUST be keyword-only and accept exactly the closed
  dispatch-kwargs (``input_payload`` / ``service_name`` / ``caller_qualified_name`` /
  ``caller_position``), one keyword-only parameter per bound-service-type
  ``[config_schema]`` field, and a ``**transport_extra`` collector — checked from the
  real ``__code__``, the same un-fakeable surface as handler step 6. The contract is
  declared, not Python-introspected (R-service-type-002).

Construction (B2 — ``the-service-type-adapter/construction-lifecycle``): the engine
builds **one instance per composition at compose time**, the constructor receiving only
the compose-fixed identity values; everything dynamic arrives per dispatch through
``invoke()``. An authenticated backend client is the *adapter author's* lazy
first-``invoke()`` concern (the constructor has no transport to build it with) — the
engine's contract here is only the identity-kwargs construction.

**Native adapters — resolved ahead of the legs** (``handler-resolution.md`` § Resolution
mechanism — Native adapters). Before either leg of the selector runs, resolution first
consults the engine's **native adapter table** (:data:`conjured.lib.NATIVE_TRAINABLE_ADAPTERS`,
a native service-type qualified name → the engine's shipped implementation class path). A
qualified name the table holds resolves through the table — the native consult **precedes
the entry-points leg**, so a native qualified name cannot be shadowed by a third-party
``conjured.service_implementations`` registration (the resolution-layer face of the
engine-owned-identity guarantee, R-service-type-004). The mapped class path routes through
the **same** dotted-path leg as any other adapter — the source-AST audit, class-shape
audit, and ``invoke()`` signature check all run unchanged; the table supplies only
*discovery*, never a shortcut past the checks (exactly one verification path). Conversely a
binding whose requested name **equals a native table value** (an adapter class path) is
rejected loud (:data:`Check.ENGINE_OWNED_IDENTITY`): binding a native backend by its class
path would fold a second, non-canonical hash identity for one backend — the dual-identity
hazard — so it is made unrepresentable, the remediation naming the native qualified name.

Every failure is a compose-time ``ContractViolation``; nothing here can fail at runtime.
"""

from __future__ import annotations

import inspect
import os
from typing import Mapping

from conjured.errors import Check, ContractViolation
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.lib import NATIVE_TRAINABLE_ADAPTERS
from conjured.validator.ast_audit import audit_adapter_module_source
from conjured.validator.resolve_handler import (
    code_signature,
    load_entry_point,
    resolve_dotted_attribute,
    select_entry_point,
)

#: Reverse of the native adapter table: an adapter **class path** → the native service-type
#: qualified name it implements. A binding whose requested name is a key here routes to an
#: engine-owned native by its class path — the dual-identity hazard rejected at resolution
#: (the native's canonical identity is its qualified name, never its implementation class).
#: The native table is injective (one class per native), so the inversion is well-defined.
_NATIVE_ADAPTER_CLASS_PATHS = {
    class_path: qualified_name
    for qualified_name, class_path in NATIVE_TRAINABLE_ADAPTERS.items()
}

#: The adapter entry-points group (service-type/reference.md § Entry-point groups).
ADAPTER_ENTRY_POINT_GROUP = "conjured.service_implementations"

#: The closed engine-supplied dispatch-kwargs every ``invoke()`` accepts
#: (R-service-type-003).
CLOSED_DISPATCH_KWARGS = frozenset(
    {"input_payload", "service_name", "caller_qualified_name", "caller_position"}
)

#: The one DECLARED-OPTIONAL engine-supplied dispatch-kwarg (service-type/reference.md
#: § Deadline propagation): a surface that declares it receives the run's remaining
#: whole-run budget; a surface that omits it is dispatched without it. The name is
#: engine-reserved — parse rejects a ``[config_schema]``/``[transport_schema]`` field
#: under it (one kwarg, one source), so declaring it can only ever mean participation.
REMAINING_BUDGET_KWARG = "remaining_budget_ms"

#: The two **reference** training-artifact contracts the engine-shipped native adapters
#: declare (handler/reference.md § Trainable backends, property 3: a GGUF, or merged
#: safetensors plus a PEFT/LoRA adapter). This is a documented reference set, **not** a
#: closed validation roster: ``training_artifact_contract`` is an opaque provenance label the
#: engine records but never interprets, so :func:`check_trainable_backend` accepts any
#: non-empty string (a consumer-supplied backend names its own). Retained because the native
#: adapters' own tests assert their declared value is one of these reference contracts.
TRAINING_ARTIFACT_CONTRACTS = frozenset({"gguf", "safetensors+peft"})


#: The adapter scope's source-audit parameters for the shared resolution legs
#: (``resolve_handler.read_and_audit_source``): the vector-7 walker (adapter modules
#: forbid above-instance-scope mutable state; instance state IS admissible), the
#: adapter pure-module check, and the adapter-scope diagnostic phrasings. The rule is
#: R-service-type-003 (service-type/reference.md § Service-impl dispatch contract:
#: "Resolution and signature failures are compose-time ContractViolation") — an
#: undecodable adapter source is a resolution failure, so it cites the adapter rule,
#: never the default R-pipeline-001 (a pipeline rule).
_ADAPTER_LEG_KWARGS: dict = dict(
    rule_id="R-service-type-003",
    what="adapter",
    auditor=audit_adapter_module_source,
    pure_check=Check.ADAPTER_PURE_MODULE,
    audit_label=("the vector-7 AST audit", "the vector-7 audit"),
    pure_hint="adapters live in plain .py modules",
)


def _resolve_dotted_class_path(name: str, *, toml_path: str, audit_enforcement: bool = False):
    """Dotted-path adapter resolution (steps 2–4) — the shared dotted leg under the
    adapter scope's audit parameters. One leg for both the native-table consult (the
    mapped class path) and the ordinary dotted-path fallback (no entry point carries
    the name) — so the native routes through the same audited path as any adapter,
    never around it. Returns ``(resolved_object, qualified_name)``."""
    resolved = resolve_dotted_attribute(
        name, toml_path=toml_path, attr_hint="class",
        audit_enforcement=audit_enforcement, **_ADAPTER_LEG_KWARGS,
    )
    return resolved, name


def _check_invoke_signature(
    adapter_cls: type,
    service_type: ServiceTypeDeclaration,
    *,
    qualified_name: str,
    toml_path: str,
    method_name: str = "invoke",
) -> None:
    """The R-service-type-002/003 signature validation, from the real ``__code__``:
    ``invoke(self, *, <closed dispatch-kwargs>, <one kwarg per [config_schema] field>,
    **transport_extra)`` — keyword-only, exact set, collector required. ``method_name``
    selects the checked surface: ``invoke`` (every adapter) or ``invoke_streaming``
    (a streamable backend — canon pins the SAME closed dispatch-kwargs;
    service-type/reference.md § The streaming adapter surface)."""
    invoke = inspect.getattr_static(adapter_cls, method_name, None)
    if isinstance(invoke, (staticmethod, classmethod)):
        invoke = invoke.__func__
    if not inspect.isfunction(invoke):
        raise ContractViolation(
            check=Check.ADAPTER_SIGNATURE, rule_id="R-service-type-003",
            expected=f"'{qualified_name}' defines a {method_name}() method (the "
                     "service-impl dispatch contract)",
            actual=f"no plain-function {method_name}() on the adapter class",
            remediation_hint="define invoke(self, *, input_payload, service_name, "
                             "caller_qualified_name, caller_position, <config kwargs>, "
                             "**transport_extra)",
            file_path=toml_path,
        )
    sig = code_signature(invoke.__code__)
    expected = CLOSED_DISPATCH_KWARGS | {f.name for f in service_type.config_schema}
    if sig.has_varargs:
        raise ContractViolation(
            check=Check.ADAPTER_SIGNATURE, rule_id="R-service-type-003",
            expected="invoke() declares no *args collector",
            actual=f"'{qualified_name}.invoke' declares a *args collector",
            remediation_hint="remove the *args collector; the dispatch-kwargs are "
                             "keyword-only",
            file_path=toml_path,
        )
    if not sig.has_varkwargs:
        raise ContractViolation(
            check=Check.ADAPTER_SIGNATURE, rule_id="R-service-type-003",
            expected="invoke() declares a **transport_extra collector (transport rides "
                     "the variadic collector — per-deployment, never hashed, never "
                     "named parameters)",
            actual=f"'{qualified_name}.invoke' has no ** collector",
            remediation_hint="add **transport_extra to invoke()",
            file_path=toml_path,
        )
    if len(sig.positional) != 1:
        raise ContractViolation(
            check=Check.ADAPTER_SIGNATURE, rule_id="R-service-type-003",
            expected="invoke(self, *, ...) — self the only positional parameter, every "
                     "dispatch/config kwarg keyword-only",
            actual=f"'{qualified_name}.invoke' positional parameters: {list(sig.positional)}",
            remediation_hint="make every parameter after self keyword-only",
            file_path=toml_path,
        )
    # The deadline-propagation kwarg is the contract's one declared-optional member
    # (service-type/reference.md § Deadline propagation): a surface may declare it
    # (participation) or omit it — both signature forms validate.
    actual = sig.kwonly - {REMAINING_BUDGET_KWARG}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContractViolation(
            check=Check.ADAPTER_SIGNATURE, rule_id="R-service-type-002",
            expected=f"{method_name}() keyword-only parameters equal to the closed "
                     f"dispatch-kwargs + the declared [config_schema] fields: "
                     f"{sorted(expected)} (plus, optionally, "
                     f"'{REMAINING_BUDGET_KWARG}' — deadline-propagation participation)",
            actual=f"signature parameters {sorted(sig.kwonly)}",
            remediation_hint=f"missing kwargs: {missing}; undeclared extra kwargs: "
                             f"{extra} (a config kwarg must have a [config_schema] "
                             "field — the contract is declared, not introspected)",
            file_path=toml_path,
        )


def declares_remaining_budget(adapter_cls: type, method_name: str = "invoke") -> bool:
    """Does *method_name* on the resolved adapter class declare the deadline-propagation
    kwarg (service-type/reference.md § Deadline propagation)? Read from the same real
    ``__code__`` the signature validation walks — assemble consults this AFTER signature
    validation passed, so a missing/malformed method cannot reach here on a valid
    compose; a defensively-absent method reads as non-participating."""
    method = inspect.getattr_static(adapter_cls, method_name, None)
    if isinstance(method, (staticmethod, classmethod)):
        method = method.__func__
    if not inspect.isfunction(method):
        return False
    return REMAINING_BUDGET_KWARG in code_signature(method.__code__).kwonly


def resolve_adapter(
    name: str,
    service_type: ServiceTypeDeclaration,
    *,
    toml_path: str | os.PathLike[str],
    audit_enforcement: bool = False,
) -> type:
    """Resolve a service-implementation name to its adapter **class** (not an
    instance — construction is :func:`construct_adapter`'s job, one instance per
    composition). ``name`` is the service-type qualified name — a **type identity,
    never coupled to the implementer's module layout** — so adapter resolution runs its
    **own selector** (``architecture/handler-resolution.md`` § Resolution mechanism —
    Adapters): the engine's **native adapter table is consulted first** (a native
    qualified name resolves through it, unshadowable by any entry point); then the
    ``conjured.service_implementations`` entry-points group, keyed by the full qualified
    name (dotted or not — an entry-point name may contain dots), with dotted-path module
    resolution as the fallback when no entry point carries the name. (The dot-presence
    selector that routes handler names would read every dotted service-type name as a
    module path and could never reach the group.) A name equal to a native table VALUE
    (an adapter class path) is rejected — see the module docstring's Native adapters note.
    ``service_type`` is the bound declaration supplying the ``[config_schema]``
    half of the signature contract; ``toml_path`` is its declaration file (the
    diagnostics' locus)."""
    toml_str = str(toml_path)

    # A binding whose requested name is a native adapter CLASS PATH routes to an
    # engine-owned native by a non-canonical identity — rejected loud so one backend cannot
    # carry two hash identities (native-library/reference.md § the engine-owned-identity
    # clause; R-service-type-004). The canonical binding names the native qualified name.
    native_of_class_path = _NATIVE_ADAPTER_CLASS_PATHS.get(name)
    if native_of_class_path is not None:
        raise ContractViolation(
            check=Check.ENGINE_OWNED_IDENTITY, rule_id="R-service-type-004",
            expected=f"an engine-owned native trainable backend is bound by its native "
                     f"qualified name '{native_of_class_path}', never by its "
                     "implementation class path",
            actual=f"'{name}' is the adapter class path implementing the native "
                   f"'{native_of_class_path}'",
            remediation_hint=f"bind the native qualified name '{native_of_class_path}' "
                             "(set type = that name); the engine resolves its shipped "
                             "implementation through the native adapter table, so one "
                             "backend keeps exactly one hash identity",
            file_path=toml_str,
        )

    # The native adapter table consult — ahead of BOTH legs (unshadowable). A native
    # qualified name resolves through the table's mapped class path, routed through the
    # same audited dotted-path leg every adapter passes (never a shortcut past the checks).
    native_class_path = NATIVE_TRAINABLE_ADAPTERS.get(name)
    if native_class_path is not None:
        resolved, qualified_name = _resolve_dotted_class_path(
            native_class_path, toml_path=toml_str, audit_enforcement=audit_enforcement,
        )
    else:
        # The entry-points half of the adapter selector, keyed by the FULL service-type
        # qualified name — dotted or not (an entry-point name may contain dots); a
        # missing registration returns None and falls through to the dotted-path leg.
        # A two-registration collision fails loud (R-service-type-004 — the engine
        # never silently disambiguates; § One implementation per service-type).
        ep = select_entry_point(
            ADAPTER_ENTRY_POINT_GROUP, name, toml_path=toml_str,
            rule_id="R-service-type-004", on_missing="none",
        )
        if ep is not None:
            resolved = load_entry_point(
                ep, name, toml_path=toml_str,
                audit_enforcement=audit_enforcement, **_ADAPTER_LEG_KWARGS,
            )
            qualified_name = f"{ep.module}.{ep.attr}"
        elif "." in name:
            # Dotted-path fallback — no entry point carries the qualified name.
            resolved, qualified_name = _resolve_dotted_class_path(
                name, toml_path=toml_str, audit_enforcement=audit_enforcement,
            )
        else:
            # No entry point and no module path to fall back to: an unsatisfiable binding.
            raise ContractViolation(
                check=Check.SERVICE_TYPE_RESOLUTION, rule_id="R-service-type-004",
                expected=f"a service implementation is registered under "
                         f"'{ADAPTER_ENTRY_POINT_GROUP}' for '{name}'",
                actual="no installed distribution registers one (an unsatisfiable binding)",
                remediation_hint="install the implementing package, or reference the "
                                 "adapter class by explicit dotted path",
                file_path=toml_str,
            )

    # The class-shape requirement — an adapter is a class by construction (the
    # vector-2/vector-7 distinction is exact: handler modules forbid the class shape;
    # adapter modules require it).
    if not inspect.isclass(resolved):
        raise ContractViolation(
            check=Check.ADAPTER_PURE_MODULE, rule_id="R-service-type-003",
            expected="a service implementation is a class (the adapter pattern; "
                     "instance state bounded by composition lifetime)",
            actual=f"'{qualified_name}' resolved to a {type(resolved).__name__}",
            remediation_hint="implement the adapter as a class with an identity-only "
                             "__init__ and the closed-kwargs invoke()",
            file_path=toml_str,
        )
    _check_invoke_signature(
        resolved, service_type, qualified_name=qualified_name, toml_path=toml_str
    )
    return resolved


def check_trainable_backend(
    adapter_cls: type,
    *,
    qualified_name: str,
    toml_path: str | os.PathLike[str],
) -> None:
    """The trainable-backend **property-contract** half of the compose-time gate
    (handler/reference.md § Trainable backends; R-handler-008 expansion): a trainable
    composition node's binding declares the two immutable property attributes the gate
    verifies against the resolved class — a non-empty ``training_artifact_contract``
    provenance label (property 3) and a ``reserved_wire_keys`` ``frozenset[str]`` (the
    extras-disjointness rider); an absent / empty / mistyped value raises
    ``ContractViolation``. The trainable-backend property is the integration property of the
    bound adapter, never a flag on the service-type declaration.

    **Certification is structural, not a self-declared marker.** A binding is
    admitted when its resolved adapter is **native-by-construction** (resolved through the
    engine's native adapter table — :func:`resolve_adapter` routes it there) OR, under the
    deployment's ``audit_enforcement`` opt-in, its adapter module carries a **fresh
    pass-grade audit stamp** (the sibling ``.audit.toml``, verified at resolution by the
    general in-scope-module check — ``validator.audit_stamp``; a consumer adapter's module
    is an in-scope adapter module like any other). This function is the property-contract
    check only; the native-vs-stamp admission is realized upstream at resolution. Runs after
    :func:`resolve_adapter` for a ``trainable.service_bindings`` entry (the cardinality half
    fires in the composition validator)."""
    toml_str = str(toml_path)
    # `training_artifact_contract` is a PROVENANCE LABEL the adapter declares — the engine
    # records it but never interprets the value: it reads the trained artifact by path, and
    # the label feeds no hash / dispatch / derived-training-shape path. So the gate requires
    # the attribute be PRESENT and a NON-EMPTY STRING (absent / empty / non-string is a real
    # declaration defect — fail loud) but does NOT close the value set. The two reference
    # contracts (`"gguf"` / `"safetensors+peft"`) are what the native adapters declare; a
    # consumer-supplied backend names its own (handler/reference.md § Trainable backends,
    # property 3). Reserving a closed set here would police a value the engine forwards but
    # does not read.
    contract = inspect.getattr_static(adapter_cls, "training_artifact_contract", None)
    if not isinstance(contract, str) or not contract:
        raise ContractViolation(
            check=Check.TRAINABLE_BACKEND_CERTIFICATION, rule_id="R-handler-008",
            expected="a certified trainable backend declares training_artifact_contract as a "
                     "non-empty provenance string — the artifact family its trained model "
                     "lands in (the engine records it opaquely, reading the artifact by path)",
            actual=(
                f"'{qualified_name}' carries no training_artifact_contract attribute"
                if contract is None
                else f"'{qualified_name}' training_artifact_contract is an empty string"
                if contract == ""
                else f"'{qualified_name}' training_artifact_contract is "
                     f"{type(contract).__name__}, not a string"
            ),
            remediation_hint="declare training_artifact_contract = \"<provenance>\" on the "
                             "adapter class (the reference serving adapters use \"gguf\" / "
                             "\"safetensors+peft\"; a consumer-supplied backend names its own)",
            file_path=toml_str,
        )
    # The reserved-wire-key certification (native-library/reference.md extras rider): a
    # certified trainable backend declares `reserved_wire_keys` as a frozenset of strings
    # — its owned wire keys (the dial core + the structural keys it constructs). compose
    # reads it for the extras-disjointness check (an `extras` table key cannot override an
    # engine-written wire key). Validated alongside the other certification attributes.
    reserved = inspect.getattr_static(adapter_cls, "reserved_wire_keys", None)
    if not isinstance(reserved, frozenset) or not all(
        isinstance(key, str) for key in reserved
    ):
        raise ContractViolation(
            check=Check.TRAINABLE_BACKEND_CERTIFICATION, rule_id="R-handler-008",
            expected="a certified trainable backend declares `reserved_wire_keys` as a "
                     "frozenset[str] — the wire keys invoke() constructs (the dial core "
                     "plus the structural keys), read for the extras-disjointness check",
            actual=(
                f"'{qualified_name}' carries no reserved_wire_keys attribute"
                if reserved is None
                else f"'{qualified_name}' reserved_wire_keys is "
                     f"{type(reserved).__name__}, not a frozenset of strings"
            ),
            remediation_hint="declare reserved_wire_keys = frozenset({...}) on the adapter "
                             "class (the keys its invoke() writes to the wire body)",
            file_path=toml_str,
        )


def check_streamable_backend(
    adapter_cls: type,
    *,
    qualified_name: str,
    toml_path: str | os.PathLike[str],
    service_type: "ServiceTypeDeclaration | None" = None,
) -> None:
    """The streaming-capability half of the trainable-backend gate (R-handler-008
    expansion, the delivery-selector contract): a trainable composition declaring
    ``streamable = true`` promises token-level delivery its bound backend must honor —
    the resolved adapter class MUST expose ``invoke_streaming`` as a **generator
    function** with the SAME closed dispatch-kwargs as ``invoke`` (yields raw text
    fragments, returns the assembled emission). Both halves fail HERE at compose — a
    missing/non-generator surface AND a wrong-kwargs generator alike: a signature
    defect the compose can see, deferred to the first streamed dispatch, would be the
    fail-at-runtime the compose-time posture forbids. ``service_type`` supplies the
    ``[config_schema]`` half of the expected kwarg set; the signature walk is skipped
    when the caller has none in hand (the capability check still runs). Runs beside
    :func:`check_trainable_backend` for a ``streamable = true`` composition only (a
    non-streamable composition makes no delivery promise)."""
    method = inspect.getattr_static(adapter_cls, "invoke_streaming", None)
    if isinstance(method, (staticmethod, classmethod)):
        method = method.__func__
    if method is None or not inspect.isgeneratorfunction(method):
        raise ContractViolation(
            check=Check.STREAMABLE_BACKEND_SUPPORT, rule_id="R-handler-008",
            expected="a streamable trainable's bound backend exposes invoke_streaming "
                     "as a generator function (yields each raw text fragment as the "
                     "backend emits it; returns the assembled emission the output "
                     "boundary validates)",
            actual=(
                f"'{qualified_name}' exposes no invoke_streaming attribute"
                if method is None
                else f"'{qualified_name}' invoke_streaming is not a generator function "
                     f"({type(method).__name__})"
            ),
            remediation_hint="bind a streaming-capable backend (the native "
                             "openai_compatible_trainable streams), implement "
                             "invoke_streaming as a generator on the adapter class, or "
                             "declare streamable = false",
            file_path=str(toml_path),
        )
    if service_type is not None:
        # guarantees: streamable-signature-compose-checked
        # The kwargs half of the same promise: canon pins invoke_streaming to the SAME
        # closed dispatch-kwargs as invoke, so the identical real-__code__ walk runs
        # here — a compose-knowable signature defect never waits for the first
        # streamed dispatch to TypeError.
        _check_invoke_signature(
            adapter_cls, service_type,
            qualified_name=qualified_name, toml_path=str(toml_path),
            method_name="invoke_streaming",
        )


def check_extras_disjoint(
    adapter_cls: type,
    config: Mapping[str, object],
    *,
    qualified_name: str,
    toml_path: str | os.PathLike[str],
) -> None:
    """The extras-disjointness check (native-library/reference.md extras rider): when the
    effective ``config`` carries an ``extras`` table, its keys MUST be disjoint from the
    adapter's ``reserved_wire_keys`` — an overlap is a wrong-door attempt to override an
    engine-written wire key (the checkpoint, the seal, or a dial), rejected at compose
    naming the key's real home. Past compose the two key-sets are disjoint by construction
    (and ``invoke()`` writes its owned keys after the ``**extras`` merge — defense in
    depth). An adapter with no ``reserved_wire_keys`` (a non-trainable service adapter) has
    no wire keys to collide with — skipped. Runs after :func:`resolve_adapter` at the
    config supply sites (stage-4 assembly)."""
    extras = config.get("extras")
    if not isinstance(extras, Mapping):
        return
    reserved = inspect.getattr_static(adapter_cls, "reserved_wire_keys", None)
    if reserved is None:
        return  # a non-trainable service adapter with no reserved wire keys — nothing to collide with
    if not isinstance(reserved, frozenset) or not all(isinstance(key, str) for key in reserved):
        # Present-but-malformed is not absence: a wrong-typed reserved_wire_keys would silently
        # equal "no reserved keys" and let a colliding extras key through unchecked (the
        # silent-degrade class). Mirror the trainable gate's frozenset[str] validation and fail loud.
        raise ContractViolation(
            check=Check.CONFIG_SCHEMA_SUPPLY, rule_id="R-service-type-002",
            expected="if an adapter declares `reserved_wire_keys` it MUST be a frozenset[str] "
                     "(the wire keys invoke() constructs) — the extras-disjointness check reads it",
            actual=f"'{qualified_name}' reserved_wire_keys is "
                   f"{type(reserved).__name__}, not a frozenset of strings",
            remediation_hint="declare reserved_wire_keys = frozenset({...}) on the adapter class, "
                             "or remove it if the adapter constructs no wire keys",
            file_path=str(toml_path), section_path="config.extras",
        )
    overlap = sorted(set(extras) & reserved)
    if overlap:
        raise ContractViolation(
            check=Check.CONFIG_SCHEMA_SUPPLY, rule_id="R-service-type-002",
            expected="every `extras` table key is disjoint from the adapter's reserved "
                     f"wire keys {sorted(reserved)} — extras carries the engine-opaque "
                     "sampling tail, never a wire key the engine constructs",
            actual=f"extras key(s) {overlap} name a reserved wire key of "
                   f"'{qualified_name}'",
            remediation_hint="each reserved key has its own home: the checkpoint identity "
                             "in [identity_schema], the dials as declared [config_schema] "
                             "fields, the prompt and seal derived from reads/output_schema "
                             "— remove the key from extras",
            file_path=str(toml_path), section_path="config.extras",
        )


def _wrap_construction_failure(
    exc: Exception, *, qualified_name: str, toml_path: str, trainable: bool
) -> ContractViolation:
    """The closed-channel wrap for an adapter-construction failure: an ``__init__``
    that rejects the compose-supplied identity kwargs (a ``TypeError`` from the call
    binding) or raises from its own body surfaces as the compose-time
    ``ContractViolation`` the module seal promises — never a raw untyped exception out
    of stage-4 assembly. A ``ContractViolation`` the constructor itself raises (e.g.
    the trainable constraint-derivation rejection) passes through the callers unwrapped
    — it is already the closed channel."""
    extra = (
        " plus the engine-supplied output_schema / schema_source kwargs"
        if trainable else ""
    )
    return ContractViolation(
        check=Check.ADAPTER_CONSTRUCTION, rule_id="R-service-type-003",
        expected=f"'{qualified_name}' constructs from the compose-fixed identity "
                 f"kwargs{extra} (one instance per composition, at compose time)",
        actual=f"adapter construction raised ({type(exc).__name__}: {exc})",
        remediation_hint="the adapter __init__ takes exactly the bound service-type's "
                         "[identity_schema] fields as kwargs and must not raise at "
                         "construction — anything dynamic (transport, config, payload) "
                         "arrives per dispatch through invoke()",
        file_path=toml_path,
    )


def construct_adapter(
    adapter_cls: type,
    identity: "dict[str, object] | None" = None,
    *,
    qualified_name: str,
    toml_path: str | os.PathLike[str],
) -> object:
    """The B2 construction: one instance per composition, at compose time, the
    constructor receiving **only the compose-fixed identity** values the pipeline
    supplied for the binding (e.g. ``model``, ``prompt_template``). Everything dynamic
    — transport, config, the call payload — arrives per dispatch through ``invoke()``;
    the authenticated client is the adapter's own lazy first-``invoke()`` memoization
    (it cannot be built here: the constructor has no transport). A construction failure
    is a compose-time ``ContractViolation`` (the closed channel covers construction —
    ``qualified_name`` / ``toml_path`` are its diagnostic locus)."""
    # guarantees: adapter-construction-fails-structured
    try:
        return adapter_cls(**(identity or {}))
    except ContractViolation:
        raise  # already the closed channel (a constructor may raise it deliberately)
    except Exception as exc:
        raise _wrap_construction_failure(
            exc, qualified_name=qualified_name, toml_path=str(toml_path),
            trainable=False,
        ) from exc


def construct_trainable_adapter(
    adapter_cls: type,
    identity: "dict[str, object] | None" = None,
    *,
    output_schema,
    schema_source: str,
    qualified_name: str,
    toml_path: str | os.PathLike[str],
) -> object:
    """The B2 construction for a **trainable backend**: identity plus the two
    engine-supplied compose-fixed kwargs the literal-equal seal requires —
    ``output_schema`` (the trainable's declared output ports,
    ``tuple[FieldDecl, ...]`` — the literal artifact the adapter submits as the decode
    constraint, R-handler-005) and ``schema_source`` (the trainable composition
    declaration's path, the locus a compose-time constraint rejection points at).
    Nothing dynamic enters: the shape is composition-fixed exactly as identity is
    (it IS the training-bundle-hash's port-shape scope), and transport still arrives
    only through ``invoke()``. The constraint derivation runs inside the constructor,
    so a grammar-inexpressible schema is rejected **here, at compose** — the
    § Trainable backends compose-time caveat's honest failure (that rejection is
    already a ``ContractViolation`` and passes through unwrapped); any OTHER
    construction failure wraps into the compose-time ``ContractViolation`` the closed
    channel requires."""
    # guarantees: adapter-construction-fails-structured
    try:
        return adapter_cls(
            **(identity or {}), output_schema=output_schema, schema_source=schema_source
        )
    except ContractViolation:
        raise  # the constraint-derivation rejection (and kin) — already the closed channel
    except Exception as exc:
        raise _wrap_construction_failure(
            exc, qualified_name=qualified_name, toml_path=str(toml_path),
            trainable=True,
        ) from exc
