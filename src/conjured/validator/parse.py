"""Stage 1 — declaration parse: TOML → the Phase-0 Pydantic IR.

The first of the two ``pipeline load lifecycle`` stages this unit owns
(``conjured/docs/components/pipeline/reference.md`` § Pipeline load lifecycle stage 1):
read a declaration and construct the parsed IR struct; unknown declarations raise
``ContractViolation``. This module turns each authoring-dialect TOML document into its
Phase-0 IR model (``conjured.ir``), translating two failure surfaces into the engine's
diagnostic:

1. **Section-presence discipline.** The IR models a declaration's resolved *content*; it
   does **not** encode whether a *required-empty-allowed* header textually appeared — a
   defaulted ``reads = ()`` cannot distinguish "present but empty" from "absent"
   (``architecture/exhaustive-declaration.md`` § The section-discipline modes;
   ``03-ir-models.md`` § What the IR models vs what Phase 1a checks). So presence is checked
   here, on the raw parsed TOML mapping, **before** the IR is constructed.

2. **Closed-grammar translation.** The IR's ``extra="forbid"`` + kind-discriminated unions
   make an unknown element *unrepresentable*; this layer translates the structural rejection
   into a ``ContractViolation`` with a kind-aware remediation hint, rather than letting a raw
   pydantic ``ValidationError`` surface (the prompt's deliverable 1).

A bundle composition (``meta.kind = "bundle"``) parses here into
:class:`~conjured.ir.composition.BundleComposition` — a bare pipeline-``nodes`` fragment
in the exact node-entry grammar (glossary § Bundle TOML). Its textual substitution into
the enclosing node sequence happens at every walker's entry chokepoint
(``conjured.ir.substitute``), before scoping and hashing.
"""

from __future__ import annotations

import tomllib
from typing import Mapping

from pydantic import ValidationError

from conjured.errors import Check, ContractViolation
from conjured.ir.common import (
    NO_DEFAULT,
    Binding,
    CompileBinding,
    CompilePrimitive,
    Delivery,
    FilePathBindingValue,
    InlineBindingValue,
    MergeStrategy,
    NodeBindingValue,
    SchemaBinding,
    ServiceBindingDecl,
    ServiceBindingSupply,
)
from conjured.ir.composition import (
    BundleComposition,
    CompositionKind,
    CompositionMeta,
    PipelineComposition,
    PreprocessorEntry,
    TrainableComposition,
    TrainableNode,
)
from conjured.ir.deployment import (
    DeploymentDeclaration,
    HookTransportBlock,
    PipelineOverride,
    TrainingContract,
    TransportBlock,
)
from conjured.ir.handler import (
    HandlerDeclaration,
    HookDeclaration,
    ServiceDeclaration,
    TransformDeclaration,
)
from conjured.ir.pipeline import (
    CompositionNode,
    HandlerNode,
    PipelineDeclaration,
    PipelineMeta,
    PipelineNode,
)
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.validator.normalize import is_explicit_null
from conjured.validator.resolve_adapter import REMAINING_BUDGET_KWARG
from conjured.validator.tokens import (
    field_type_contains_optional,
    parse_schema_section,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_HANDLER_KINDS = ("transform", "service", "hook")

#: The blessed first-party compiler bare names (the closed bare-name space the `compile`
#: directive reserves — CompilePrimitive is its registry). A bare `compile` value must be one
#: of these; a dotted value is an open third-party compiler resolved at compose.
_BLESSED_COMPILER_NAMES = frozenset(p.value for p in CompilePrimitive)


def _require_mapping(data: object, file_path: str, what: str, rule_id: str = "R-handler-006") -> Mapping:
    if not isinstance(data, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION,
            rule_id=rule_id,
            expected=f"{what} is a TOML table",
            actual=f"top-level value is {type(data).__name__}",
            file_path=file_path,
        )
    return data


def _closed_grammar(file_path: str, rule_id: str, allowed: set[str], present: set[str], what: str) -> None:
    unknown = present - allowed
    if unknown:
        raise ContractViolation(
            check=Check.CLOSED_GRAMMAR,
            rule_id=rule_id,
            expected=f"{what} sections in the closed set {sorted(allowed)}",
            actual=f"unknown section(s) {sorted(unknown)}",
            remediation_hint="remove the unknown section; the grammar is closed (an engine change adds structure)",
            file_path=file_path,
        )


def _require_present(file_path: str, data: Mapping, header: str, what: str, rule_id: str = "R-handler-006") -> None:
    """Section-presence discipline: a required-empty-allowed header MUST textually appear.
    ``rule_id`` names the owning rule of the declaration class (PARSE-F3), threaded from the
    non-handler callers (R-service-type-001 for the service-type sections); handler / trainable
    sites keep the R-handler-006 default."""
    if header not in data:
        raise ContractViolation(
            check=Check.SECTION_PRESENCE,
            rule_id=rule_id,
            expected=f"{what} declares the required section header [{header}] (empty body allowed)",
            actual=f"[{header}] absent",
            remediation_hint=f"add the [{header}] header; an empty body is the 'considered, declared nothing' signal",
            file_path=file_path,
            section_path=header,
        )


def _require_body(
    fields: tuple, file_path: str, header: str, what: str, rule_id: str = "R-handler-006"
) -> None:
    """Required-body-required: the section MUST declare ≥ 1 field. ``rule_id`` names the
    owning rule for the declaration class — R-handler-006 for handler / trainable-
    composition declarations, R-service-type-001 for service-type schemas (canon owns
    the service-type body-required arms there), R-pipeline-001 for the pipeline
    declaration's own sections."""
    if not fields:
        raise ContractViolation(
            check=Check.BODY_REQUIRED,
            rule_id=rule_id,
            expected=f"{what} [{header}] declares at least one field",
            actual=f"[{header}] is empty",
            remediation_hint=f"declare at least one field under [{header}] — 'declared nothing' is not a meaningful state here",
            file_path=file_path,
            section_path=header,
        )


def _translate_validation_error(
    exc: ValidationError, file_path: str, what: str, rule_id: str
) -> ContractViolation:
    """Residual translation of a pydantic ``ValidationError`` (the IR's structural rejection)
    into the engine diagnostic — keeps the fuzz guarantee (compile or ContractViolation).
    ``rule_id`` is the owning rule of the declaration class being constructed (PARSE-F3),
    threaded from :func:`_construct` so a residual ValidationError on a pipeline / service-type /
    deployment declaration cites its OWN rule, not the generic handler-flavored fallback."""
    errors = exc.errors()
    loc = ".".join(str(p) for p in errors[0].get("loc", ())) if errors else ""
    msg = errors[0].get("msg", "invalid declaration") if errors else "invalid declaration"
    return ContractViolation(
        check=Check.MALFORMED_DECLARATION,
        rule_id=rule_id,
        expected=f"a well-formed {what} per the closed grammar",
        actual=f"{msg} (at {loc or '<root>'})",
        file_path=file_path,
        section_path=loc or None,
    )


# ---------------------------------------------------------------------------
# Bindings (handler `bindings.<name>`)
# ---------------------------------------------------------------------------


def _as_file_ref(
    name: str, value: object, *, file_path: str, section_path: str, rule_id: str
) -> FilePathBindingValue | None:
    """THE single ``{ file = "<path>" }`` external-file classifier, shared by every external-file
    site (pipeline/preprocessor binding *values* AND a compile directive's *parameters*). Returns a
    ``FilePathBindingValue`` when ``value`` is the external-file form, ``None`` when it is ordinary
    inline content — the caller wraps/keeps the inline value its own way.

    ``file`` is the engine-read external-declaration key: a value of the shape ``{ file = "..." }``
    is resolved by reading the named file, never treated as an inline object with a literal ``file``
    field (handler/reference.md § Binding value-supply grammar). The form is **exactly**
    ``{ file = "<path string>" }`` — the table carries no other keys and the path is a **non-empty**
    string; anything else (extra keys, a non-string path, or an empty path that names no file) is
    malformed and fails loud here at parse (never guessed, never deferred to a late read error)."""
    if not (isinstance(value, Mapping) and "file" in value):
        return None
    if set(value) != {"file"} or not isinstance(value["file"], str) or value["file"] == "":
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id=rule_id,
            expected=f"'{name}' external-file form is exactly {{ file = \"<path>\" }} (a non-empty string path)",
            actual=f"got {value!r}", file_path=file_path, section_path=section_path,
            remediation_hint="`file` is the engine-read external-declaration key; supply only a "
                             "non-empty path string, or drop `file` for an inline object",
        )
    return FilePathBindingValue(name=name, path=value["file"])


def _parse_binding(name: str, table: object, file_path: str) -> Binding:
    section_path = f"bindings.{name}"
    if not isinstance(table, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION,
            rule_id="R-handler-006",
            expected=f"[{section_path}] is a table declaring a schema or a compile directive",
            actual=f"got {type(table).__name__}",
            file_path=file_path,
            section_path=section_path,
        )
    if "compile" in table:
        compiler_token = table["compile"]
        if not isinstance(compiler_token, str):
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR,
                rule_id="R-handler-006",
                expected="a `compile` value naming a compiler (a bare blessed name or a "
                         "dotted third-party qualified name)",
                actual=f"a non-string compile value {compiler_token!r}",
                remediation_hint="set compile = \"<compiler>\" — a bare blessed name "
                                 "(regex / jinja / json_schema) or a dotted third-party name",
                file_path=file_path,
                section_path=section_path,
            )
        # The bare-vs-namespaced split (the same selector field validators use): a dotted
        # name is an open third-party compiler resolved at compose; a bare name MUST be one
        # of the blessed first-party compilers (the engine's reserved bare-name space). A
        # bare name no blessed compiler carries is a closed-grammar failure HERE at parse
        # (the bare-name space is closed); a dotted name is accepted structurally and resolved
        # in the stage-4 binding-resolution pass (handler/reference.md § The `compile = "..."`
        # directive sub-form).
        if "." not in compiler_token and compiler_token not in _BLESSED_COMPILER_NAMES:
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR,
                rule_id="R-handler-006",
                expected=f"a bare `compile` name in the blessed first-party set "
                         f"{sorted(_BLESSED_COMPILER_NAMES)}, or a dotted third-party "
                         "compiler name (containing a '.')",
                actual=f"bare compile name {compiler_token!r} is not a blessed compiler",
                remediation_hint="use a blessed bare name (regex / jinja / json_schema), or "
                                 "namespace a third-party compiler (e.g. 'mypkg.compile_grammar')",
                file_path=file_path,
                section_path=section_path,
            )
        # The engine-read binding keys do NOT combine with the compile directive as
        # PARAMETER KEYS: "the `compile = \"...\"` directive and its parameter keys
        # *are* the complete binding declaration" (handler/reference.md § The compile
        # directive sub-form) — the artifact's delivery is engine-fixed (delivered
        # as-is, "not copied per dispatch, not the reference-binding subtype"), the
        # node supplies nothing for a compile binding (so a ship-time default has no
        # omission to fill), and `file` is the engine's reserved external-file FORM
        # (a param VALUE of `{ file = "<path>" }` stays fully legal — that IS the
        # form; only the top-level param KEY `file` is reserved). Packing any of
        # them into the opaque compiler params would silently strip their engine
        # meaning (one key, one meaning — a compiler parameter may not shadow an
        # engine-reserved key), so they fail loud here at parse.
        reserved_on_compile = {"delivery", "default", "file"} & set(table)
        if reserved_on_compile:
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR,
                rule_id="R-handler-006",
                expected="a compile-directive binding declares the `compile` directive "
                         "and its compiler parameter keys only — the engine-read "
                         "`delivery` / `default` / `file` binding keys do not combine "
                         "with `compile` as parameter keys",
                actual=f"engine-reserved key(s) {sorted(reserved_on_compile)} on a "
                       "compile-directive binding",
                remediation_hint="a compiled artifact's delivery is engine-fixed, a "
                                 "compile binding takes no node supply for a default to "
                                 "fill, and `file` is the reserved external-file form "
                                 "(supplying a parameter FROM a file stays legal: "
                                 "<param> = { file = \"<path>\" }); if the compiler "
                                 "declares a parameter under a reserved name, rename it "
                                 "(e.g. `source_file`) — a compiler parameter may not "
                                 "shadow an engine-reserved binding key",
                file_path=file_path,
                section_path=section_path,
            )
        # A compile PARAMETER's value MAY use the engine's external-file form — `<param> = { file
        # = "<path>" }` — the SAME `{ file }` form a binding value uses (the shared `_as_file_ref`
        # classifier; handler/reference.md § The `compile = "..."` directive sub-form: "A compile
        # parameter is supplied inline OR from a file"). A file-supplied param becomes a
        # `FilePathBindingValue` carried opaquely in `params`; the binding-resolution pass
        # (validator.resolve.resolve_compile_param_files) reads it as TEXT and the compiler parses
        # it. A param has one value, so it is inline or file-supplied by construction — no twin key,
        # no `_file` suffix. There is no XOR check: a value is one thing.
        params: dict[str, object] = {}
        for k, v in table.items():
            if k == "compile":
                continue
            file_ref = _as_file_ref(
                k, v, file_path=file_path, section_path=f"{section_path}.{k}", rule_id="R-handler-006"
            )
            # Compile parameters carry no nullable declaration, so the reserved explicit-null
            # form is recognized-and-rejected at a parameter position — never handed to a
            # compiler as data (handler/reference.md explicit-null region: the same
            # recognized-and-rejected treatment as identity and config).
            if file_ref is None and is_explicit_null(
                v, owner=f"{section_path}.{k}", file_path=file_path,
                section_path=f"{section_path}.{k}", rule_id="R-handler-006",
            ):
                raise ContractViolation(
                    check=Check.EXPLICIT_NULL_TARGET, rule_id="R-pipeline-001",
                    expected="{ null = true } targets a nullable-declared field",
                    actual=f"compile parameter '{k}' — compile parameters carry no nullable "
                           "declaration",
                    file_path=file_path, section_path=f"{section_path}.{k}",
                )
            params[k] = file_ref if file_ref is not None else v
        return Binding(name=name, body=CompileBinding(compiler=compiler_token, params=params))

    delivery_token = table.get("delivery", "copy")
    try:
        delivery = Delivery(delivery_token)
    except ValueError as exc:
        raise ContractViolation(
            check=Check.CLOSED_GRAMMAR,
            rule_id="R-handler-006",
            expected=f"binding delivery selector in {[d.value for d in Delivery]}",
            actual=f"unknown delivery {delivery_token!r}",
            file_path=file_path,
            section_path=section_path,
        ) from exc
    # `default` is an engine-read binding key (alongside `delivery` / `compile`): the ship-time
    # default value the engine supplies when the node omits this binding (handler/reference.md
    # § Ship-time defaults). Absent → NO_DEFAULT (the binding is supply-required). The declared
    # default value is opaque here (validated against the binding's schema at compose). One
    # exception to the opacity: the reserved explicit-null form's spelling at the
    # WHOLE-default position is checked at parse (a default is an engine-read value
    # position — a nullable single field MAY declare `default = { null = true }`). Field-level
    # recognition inside a multi-field default object — spelling and nullable-only admission
    # both — resolves at the compose join (validator.normalize), the same join every supply
    # route reduces through.
    default = table["default"] if "default" in table else NO_DEFAULT
    if default is not NO_DEFAULT:
        is_explicit_null(
            default, owner=f"{section_path}.default", file_path=file_path,
            section_path=f"{section_path}.default", rule_id="R-handler-006",
        )
    field_tables = {k: v for k, v in table.items() if k not in ("delivery", "default")}
    fields = parse_schema_section(field_tables, file_path=file_path, section_path=section_path)
    return Binding(name=name, body=SchemaBinding(fields=fields, delivery=delivery, default=default))


def _parse_bindings(data: Mapping, file_path: str) -> tuple[Binding, ...]:
    raw = data.get("bindings", {})
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION,
            rule_id="R-handler-006",
            expected="[bindings.<name>] sections (individually-named tables)",
            actual=f"'bindings' is {type(raw).__name__}",
            file_path=file_path,
            section_path="bindings",
        )
    return tuple(_parse_binding(name, table, file_path) for name, table in raw.items())


def _parse_service_binding_decls(raw: object, file_path: str, section_path: str) -> tuple[ServiceBindingDecl, ...]:
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION,
            rule_id="R-handler-006",
            expected=f"[{section_path}] is a table of named service-typed bindings",
            actual=f"got {type(raw).__name__}",
            file_path=file_path,
            section_path=section_path,
        )
    decls = []
    for bname, body in raw.items():
        if not isinstance(body, Mapping) or "type" not in body:
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION,
                rule_id="R-handler-006",
                expected=f"service binding '{section_path}.{bname}' declares a 'type' (service-type qualified name)",
                actual="missing 'type'",
                file_path=file_path,
                section_path=f"{section_path}.{bname}",
            )
        # Closed to {type}: a service-binding DECLARATION carries the bound service-type
        # qualified name and nothing else. Notably NO prose `description` — the family rule
        # admits `description` only on a trainable's `output_schema` fields; binding prose
        # lives in the declaration's `[annotations]`. An unknown key (a migrated-from
        # `description`, a stray identity value that belongs at the pipeline supply site)
        # raises CLOSED_GRAMMAR rather than being silently absorbed.
        _closed_grammar(
            file_path, "R-handler-006", {"type"}, set(body),
            f"service binding '{section_path}.{bname}'",
        )
        # Routed through _construct (not direct pydantic construction) so a non-string
        # `type` — the one remaining un-pre-checked shape — surfaces as the structured
        # MALFORMED_DECLARATION, never a raw ValidationError escaping loads() (the
        # module's no-raw-pydantic-leak guarantee; the fuzz compile-or-ContractViolation
        # invariant).
        decls.append(
            _construct(
                ServiceBindingDecl, file_path,
                f"service binding '{section_path}.{bname}'", rule_id="R-handler-006",
                name=bname, type=body["type"],
            )
        )
    return tuple(decls)


# ---------------------------------------------------------------------------
# 1 · Handler declarations (transform / service / hook)
# ---------------------------------------------------------------------------


def parse_handler(data: Mapping, *, file_path: str) -> HandlerDeclaration:
    """TOML → ``HandlerDeclaration`` (transform / service / hook) with kind-header,
    closed-grammar, and section-presence discipline (handler/conformance.md §§ Top-level
    kind header / Closed handler-declaration grammar; exhaustive-declaration.md)."""
    data = _require_mapping(data, file_path, "handler declaration")
    present_kinds = [k for k in _HANDLER_KINDS if k in data]
    if len(present_kinds) != 1:
        raise ContractViolation(
            check=Check.HANDLER_KIND_HEADER,
            rule_id="R-handler-003",
            expected="exactly one top-level handler-kind header (transform / service / hook)",
            actual=f"{len(present_kinds)} kind header(s): {present_kinds or '[]'}",
            remediation_hint="declare exactly one of [transform], [service], [hook] at file top level",
            file_path=file_path,
        )
    kind = present_kinds[0]
    # The kind header is a BARE discriminator — `[transform]` / `[service]` / `[hook]`
    # routes the declaration to its kind-specific path and declares nothing itself
    # (R-handler-006: the sub-declarations are the closed set of TOP-LEVEL sections).
    # Its body is therefore closed to {}: a key authored inside it (a typo'd
    # `[transform.reads]`, a stray `name = ...`) is an undeclared element and fails
    # loud, never silently absorbed. A non-table kind header (`transform = "yes"`) is
    # malformed the same way.
    kind_body = data[kind]
    if not isinstance(kind_body, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-handler-006",
            expected=f"[{kind}] is the bare kind-header table (the kind discriminator)",
            actual=f"'{kind}' is {type(kind_body).__name__}, not a table header",
            remediation_hint=f"declare the kind as the bare [{kind}] table header",
            file_path=file_path, section_path=kind,
        )
    if kind_body:
        raise ContractViolation(
            check=Check.CLOSED_GRAMMAR, rule_id="R-handler-006",
            expected=f"the [{kind}] kind header declares nothing (a bare discriminator; "
                     "sub-declarations are top-level sections)",
            actual=f"undeclared element(s) {sorted(kind_body)} inside the [{kind}] header",
            remediation_hint="move the content to its top-level section ([reads], "
                             "[output_schema], [bindings.<name>], …) — keys inside the "
                             "kind header are not part of the closed grammar",
            file_path=file_path, section_path=kind,
        )
    annotations = _parse_annotations(data, file_path)

    if kind == "transform":
        allowed = {"transform", "reads", "output_schema", "bindings", "annotations"}
        # Forbidden-section check BEFORE the generic closed-grammar check: a transform's
        # service_bindings is a kind-discipline violation owning the more-specific R-handler-004
        # diagnostic; running _closed_grammar first would shadow it with the generic
        # "unknown section" R-handler-006 (the forbidden sections are a subset of "not in the
        # allowed set", so the kind check must claim them first). _closed_grammar still catches
        # genuinely-unknown keys after.
        _check_forbidden_handler_sections(data, file_path, kind)
        _closed_grammar(file_path, "R-handler-006", allowed, set(data), "transform handler")
        _require_present(file_path, data, "reads", "transform handler")
        _require_present(file_path, data, "output_schema", "transform handler")
        reads = parse_schema_section(data.get("reads", {}), file_path=file_path, section_path="reads")
        output_schema = parse_schema_section(data.get("output_schema", {}), file_path=file_path, section_path="output_schema")
        _require_body(output_schema, file_path, "output_schema", "transform handler")
        return _construct(
            TransformDeclaration, file_path, "transform handler", rule_id="R-handler-006",
            reads=reads, output_schema=output_schema, bindings=_parse_bindings(data, file_path), annotations=annotations,
        )

    if kind == "service":
        allowed = {"service", "reads", "output_schema", "service_bindings", "bindings", "annotations"}
        # Forbidden-section check BEFORE _closed_grammar — the same ordering the transform arm
        # documents above: the tailored kind-discipline diagnostic (transport_schema on a
        # service) must claim its section before the generic closed-grammar check shadows it.
        _check_forbidden_handler_sections(data, file_path, kind)
        _closed_grammar(file_path, "R-handler-006", allowed, set(data), "service handler")
        _require_present(file_path, data, "reads", "service handler")
        _require_present(file_path, data, "output_schema", "service handler")
        _require_present(file_path, data, "service_bindings", "service handler")
        reads = parse_schema_section(data.get("reads", {}), file_path=file_path, section_path="reads")
        output_schema = parse_schema_section(data.get("output_schema", {}), file_path=file_path, section_path="output_schema")
        _require_body(output_schema, file_path, "output_schema", "service handler")
        return _construct(
            ServiceDeclaration, file_path, "service handler", rule_id="R-handler-006",
            reads=reads, output_schema=output_schema,
            service_bindings=_parse_service_binding_decls(data.get("service_bindings", {}), file_path, "service_bindings"),
            bindings=_parse_bindings(data, file_path), annotations=annotations,
        )

    # hook
    allowed = {"hook", "reads", "service_bindings", "transport_schema", "bindings", "annotations"}
    # Forbidden-section check BEFORE _closed_grammar — the same ordering the transform arm
    # documents above: the tailored kind-discipline diagnostic (output_schema on a hook) must
    # claim its section before the generic closed-grammar check shadows it.
    _check_forbidden_handler_sections(data, file_path, kind)
    _closed_grammar(file_path, "R-handler-006", allowed, set(data), "hook handler")
    _require_present(file_path, data, "reads", "hook handler")
    _require_present(file_path, data, "service_bindings", "hook handler")
    _require_present(file_path, data, "transport_schema", "hook handler")
    reads = parse_schema_section(data.get("reads", {}), file_path=file_path, section_path="reads")
    transport_schema = parse_schema_section(
        data.get("transport_schema", {}), file_path=file_path, section_path="transport_schema",
        allow_secret_ref=True,  # the hook transport arm of § Secret references (deployment/reference.md)
        constraints_forbidden_rule_id="R-handler-006",  # D5 — no value-enforcement point on transport
    )
    bindings = _parse_bindings(data, file_path)
    _check_transport_field_collision(
        transport_schema,
        reads_names={f.name for f in reads},
        binding_names={b.name for b in bindings},
        file_path=file_path,
    )
    return _construct(
        HookDeclaration, file_path, "hook handler", rule_id="R-handler-006",
        reads=reads,
        service_bindings=_parse_service_binding_decls(data.get("service_bindings", {}), file_path, "service_bindings"),
        transport_schema=transport_schema, bindings=bindings, annotations=annotations,
    )


def _check_transport_field_collision(
    transport_schema, *, reads_names: set, binding_names: set, file_path: str
) -> None:
    """A hook's ``transport_schema`` field names join the R-handler-001 signature union
    (delivered to the body as kwargs, like bindings), so a name colliding with a
    declared input-port or ``bindings.<name>`` name would make one kwarg two-sourced —
    rejected loud at declaration load (handler/reference.md § ``transport_schema``:
    "A transport_schema field name MUST NOT collide…"; the one-namespace-one-name
    discipline). The reserved ``services`` kwarg joins the disallowed set: the engine reads
    it to inject the ServicesProxy, so a transport field named ``services`` would be
    clobbered at dispatch (handler/reference.md § ``transport_schema`` — the ``services``
    reserved-kwarg clause)."""
    for field in transport_schema:
        if field.name in reads_names or field.name in binding_names or field.name == "services":
            if field.name == "services":
                source = "reserved 'services' kwarg (the ServicesProxy injection)"
            else:
                source = "input-port" if field.name in reads_names else "bindings.<name>"
            raise ContractViolation(
                check=Check.NAME_UNIQUENESS, rule_id="R-handler-006",
                expected="transport_schema field names disjoint from the declared "
                         "input-port and bindings.<name> names and the reserved "
                         "'services' kwarg (they join the R-handler-001 signature union "
                         "as kwargs)",
                actual=f"transport_schema field '{field.name}' collides with a "
                       f"declared {source}",
                remediation_hint="rename the transport field (or the colliding "
                                 "port/binding) — one kwarg cannot carry two sources",
                file_path=file_path, section_path=f"transport_schema.{field.name}",
            )


def _check_forbidden_handler_sections(data: Mapping, file_path: str, kind: str) -> None:
    """Per-kind forbidden sections (R-handler-006 / exhaustive-declaration § forbidden):
    ``output_schema`` on a hook; ``service_bindings`` / ``transport_schema`` on a transform;
    ``transport_schema`` on a service. The diagnostic anchors on the kind-discipline reason."""
    forbidden = {
        "transform": {"service_bindings": "R-handler-004", "transport_schema": "R-handler-006"},
        "service": {"transport_schema": "R-handler-006"},
        "hook": {"output_schema": "R-handler-006"},
    }[kind]
    for section, rule_id in forbidden.items():
        if section in data:
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR,
                rule_id=rule_id,
                expected=f"a {kind} handler does not declare [{section}] (kind discipline)",
                actual=f"[{section}] present on a {kind}",
                remediation_hint=_forbidden_hint(kind, section),
                file_path=file_path,
                section_path=section,
            )


def _forbidden_hint(kind: str, section: str) -> str:
    if kind == "transform" and section == "service_bindings":
        return "a transform has no external-call edge; if it needs one, it is a service"
    if section == "output_schema":
        return "a hook returns None and writes no channels; if it writes state, it is a transform or service"
    return f"[{section}] is not part of the {kind} grammar"


def _parse_annotations(data: Mapping, file_path: str) -> Mapping | None:
    raw = data.get("annotations")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION,
            rule_id="R-handler-006",
            expected="[annotations] is a table (engine-opaque)",
            actual=f"got {type(raw).__name__}",
            file_path=file_path,
            section_path="annotations",
        )
    return dict(raw)


# ---------------------------------------------------------------------------
# 2 · Service-type declarations
# ---------------------------------------------------------------------------


def parse_service_type(data: Mapping, *, file_path: str) -> ServiceTypeDeclaration:
    """TOML → ``ServiceTypeDeclaration`` (R-service-type-001 closed grammar; the three
    schema sections; nullable-only-on-transport)."""
    data = _require_mapping(data, file_path, "service-type declaration", rule_id="R-service-type-001")
    allowed = {"name", "description", "identity_schema", "transport_schema", "config_schema", "annotations"}
    _closed_grammar(file_path, "R-service-type-001", allowed, set(data), "service-type")
    if "name" not in data:
        raise ContractViolation(
            check=Check.SECTION_PRESENCE, rule_id="R-service-type-001",
            expected="service-type declares a top-level 'name' (qualified identifier)",
            actual="'name' absent", file_path=file_path,
        )
    _require_present(file_path, data, "identity_schema", "service-type", rule_id="R-service-type-001")
    _require_present(file_path, data, "transport_schema", "service-type", rule_id="R-service-type-001")
    _require_present(file_path, data, "config_schema", "service-type", rule_id="R-service-type-001")

    # D5 — service-type schema fields admit NO validation keywords (neither bare standard
    # constraints nor namespaced third-party validators): identity reaches the adapter raw,
    # config is key/coverage-checked only (the ruled posture), transport projects raw — so a
    # value constraint here has no enforcement point and is the silent-no-op the engine
    # forecloses (service-type/reference.md § Schema-field vocabulary). The
    # constraints-forbidden flag makes any such keyword a loud CV citing R-service-type-001.
    identity = parse_schema_section(
        data.get("identity_schema", {}), file_path=file_path, section_path="identity_schema",
        rule_id="R-service-type-001", constraints_forbidden_rule_id="R-service-type-001",
    )
    # [transport_schema] is the ONLY service-type surface admitting the `secret_ref` token
    # (allow_secret_ref) — a credential field declares a secret reference the deployment
    # supplies as "[scheme]payload" and the adapter resolves at dispatch
    # (deployment/reference.md § Secret references).
    transport = parse_schema_section(
        data.get("transport_schema", {}), file_path=file_path, section_path="transport_schema",
        rule_id="R-service-type-001", allow_secret_ref=True,
        constraints_forbidden_rule_id="R-service-type-001",
    )
    # A [config_schema] field MAY declare a per-field ship-time `default` — the config-side
    # ship-time-default surface (service-type/reference.md § [config_schema]; the supply
    # rule lives at § The [config_schema] contract) — but no validation keywords (D5). It
    # is also the ONLY surface admitting the `table` token (allow_table) — the open
    # string-keyed table of JSON-expressible values (the trainable members' `extras`).
    config = parse_schema_section(
        data.get("config_schema", {}), file_path=file_path, section_path="config_schema",
        rule_id="R-service-type-001",
        allow_default=True, allow_table=True, constraints_forbidden_rule_id="R-service-type-001",
    )
    _require_body(identity, file_path, "identity_schema", "service-type", rule_id="R-service-type-001")
    _require_body(transport, file_path, "transport_schema", "service-type", rule_id="R-service-type-001")

    # The deadline-propagation kwarg name is engine-reserved (service-type/reference.md
    # § Deadline propagation): config and transport fields reach the adapter's dispatch
    # surface as kwargs, so a field under the reserved name would make one kwarg
    # two-sourced — rejected loud here, the same one-namespace-one-name discipline as
    # the hook transport-collision check.
    for section_name, fields in (("transport_schema", transport), ("config_schema", config)):
        for field in fields:
            if field.name == REMAINING_BUDGET_KWARG:
                raise ContractViolation(
                    check=Check.NAME_UNIQUENESS, rule_id="R-service-type-001",
                    expected=f"no [{section_name}] field named '{REMAINING_BUDGET_KWARG}' "
                             "— the name is the engine-reserved deadline-propagation "
                             "dispatch-kwarg (the runner is its only supplier)",
                    actual=f"[{section_name}] declares a field '{field.name}'",
                    remediation_hint="rename the field — one kwarg cannot carry two "
                                     "sources (service-type reference, the deadline-"
                                     "propagation section, owns the reserved name)",
                    file_path=file_path, section_path=f"{section_name}.{field.name}",
                )

    # nullable is admitted ONLY on transport fields (service-type/reference.md § transport_schema).
    for section_name, fields in (("identity_schema", identity), ("config_schema", config)):
        for field in fields:
            if field_type_contains_optional(field.type):
                raise ContractViolation(
                    check=Check.NULLABLE_PLACEMENT, rule_id="R-service-type-001",
                    expected="nullable / '<T> | None' is admitted only on [transport_schema] fields "
                             "(the ban covers a nullable nested inside an object or collection too)",
                    actual=f"nullable reachable in field '{field.name}' in [{section_name}]",
                    remediation_hint="a missing identity/config value is not a meaningful composition state; drop the nullable",
                    file_path=file_path, section_path=f"{section_name}.{field.name}",
                )

    return _construct(
        ServiceTypeDeclaration, file_path, "service-type", rule_id="R-service-type-001",
        name=data["name"], description=data.get("description"),
        identity_schema=identity, transport_schema=transport, config_schema=config,
        annotations=_parse_annotations(data, file_path),
    )


# ---------------------------------------------------------------------------
# 3 · Pipeline declarations
# ---------------------------------------------------------------------------


def parse_pipeline(data: Mapping, *, file_path: str) -> PipelineDeclaration:
    """TOML → ``PipelineDeclaration`` (pipeline/reference.md § Pipeline TOML grammar)."""
    data = _require_mapping(data, file_path, "pipeline declaration", rule_id="R-pipeline-001")
    allowed = {"meta", "nodes", "service_bindings", "merge", "inputs", "outputs"}
    _closed_grammar(file_path, "R-pipeline-001", allowed, set(data), "pipeline")

    meta = _parse_pipeline_meta(data.get("meta"), file_path)
    return _construct(
        PipelineDeclaration, file_path, "pipeline", rule_id="R-pipeline-001",
        meta=meta, **_parse_pipeline_body(data, file_path, "pipeline"),
    )


def _parse_pipeline_body(data: Mapping, file_path: str, what: str) -> dict:
    """THE pipeline body parser — ``nodes`` / ``service_bindings`` / ``merge`` /
    ``inputs`` / ``outputs``, shared by the top-level pipeline and the nested ``pipeline``
    composition kind (the mirror-pipeline principle: one grammar, one parser across both
    layers; ``pipeline/reference.md`` § The nested ``pipeline`` composition kind — the
    body IS the pipeline grammar, including the presence-opts-in ``[outputs]`` arm).
    Returns the constructor kwargs minus ``meta`` (each caller supplies its own layer's
    meta)."""
    raw_nodes = data.get("nodes", ())
    if not isinstance(raw_nodes, (list, tuple)):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
            expected="[[nodes]] is an array of node entries", actual=f"'nodes' is {type(raw_nodes).__name__}",
            file_path=file_path, section_path="nodes",
        )
    nodes = tuple(_parse_node(entry, idx, file_path) for idx, entry in enumerate(raw_nodes))

    service_bindings = _parse_supply_blocks(data.get("service_bindings", {}), file_path)
    merge = _parse_merge(data.get("merge", {}), file_path)
    # Boundary fields admit no constraint keywords and no validators list — the boundary's
    # own validation is presence-only, so a value constraint here would have no enforcement
    # point (pipeline/reference.md § inputs / outputs; the fail-loud-inapplicability posture).
    inputs = (
        parse_schema_section(
            data.get("inputs", {}), file_path=file_path, section_path="inputs",
            rule_id="R-pipeline-001", constraints_forbidden_rule_id="R-pipeline-001",
        )
        if "inputs" in data else ()
    )
    if "outputs" in data:
        # `outputs` is truly-optional (absence opts out) BUT present-but-empty is categorically
        # distinct: an empty closed-shape body is an exhaustive-declaration violation —
        # required-body-required when present (pipeline/reference.md § inputs/outputs API boundary;
        # architecture/exhaustive-declaration.md § The section-discipline modes). Mirror the
        # trainable-composition `_require_body(outputs, …)` path.
        outputs = parse_schema_section(
            data["outputs"], file_path=file_path, section_path="outputs",
            rule_id="R-pipeline-001", constraints_forbidden_rule_id="R-pipeline-001",
        )
        _require_body(outputs, file_path, "outputs", what, rule_id="R-pipeline-001")
    else:
        outputs = None  # truly-optional: absence is categorically distinct from empty-present
    return {
        "nodes": nodes, "service_bindings": service_bindings, "merge": merge,
        "inputs": inputs, "outputs": outputs,
    }


def _parse_pipeline_meta(raw: object, file_path: str) -> PipelineMeta:
    """Build the pipeline ``[meta]`` self-name (the family rule — every composable unit
    self-names via ``[meta].name``; ``hash-model.md`` § The family rule). Required block,
    ``name`` required (the block's key set is closed — ``{name}``); mirrors composition ``[meta]`` minus
    ``kind`` (a top-level pipeline has no composition-kind variant)."""
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
            expected="a pipeline declaration has a [meta] block with a 'name'",
            actual="missing [meta]" if raw is None else f"[meta] is {type(raw).__name__}",
            remediation_hint="add [meta] with a 'name' — the pipeline self-names (its pipelines.<name> reference); the name is identity, never hashed",
            file_path=file_path, section_path="meta",
        )
    if "name" not in raw:
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
            expected="[meta] declares a 'name' (the pipeline's identity / pipelines.<name> reference)",
            actual="missing meta.name", file_path=file_path, section_path="meta.name",
        )
    # Inner closed grammar: a pipeline [meta] block declares only {name} — the family rule's
    # self-name (pipeline/reference.md § `meta`: "The block's key set is closed — {name}").
    # A declaration-level `description` is NOT admitted (author prose lives in a TOML comment;
    # a schema field's `description` rides only a trainable's `output_schema`); an unknown inner
    # key raises CLOSED_GRAMMAR. Mirrors composition [meta] minus `kind`.
    _closed_grammar(file_path, "R-pipeline-001", {"name"}, set(raw), "pipeline [meta]")
    return _construct(
        PipelineMeta, file_path, "pipeline meta", rule_id="R-pipeline-001",
        name=raw["name"],
    )


def _parse_node(entry: object, idx: int, file_path: str) -> PipelineNode:
    section_path = f"nodes[{idx}]"
    if not isinstance(entry, Mapping) or "kind" not in entry:
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
            expected=f"{section_path} is a table with a 'kind' ('handler' | 'composition')",
            actual="missing 'kind'", file_path=file_path, section_path=section_path,
        )
    kind = entry["kind"]
    if kind == "handler":
        allowed = {"kind", "name", "bindings", "reads_map", "writes_map"}
        unknown = set(entry) - allowed
        if unknown:
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR, rule_id="R-pipeline-001",
                expected=f"a handler node entry's keys are {sorted(allowed)}",
                actual=f"unknown key(s) {sorted(unknown)}", file_path=file_path, section_path=section_path,
            )
        if "name" not in entry:
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
                expected=f"{section_path} declares a 'name' (qualified handler name)", actual="missing 'name'",
                file_path=file_path, section_path=section_path,
            )
        return _construct(
            HandlerNode, file_path, "handler node", rule_id="R-pipeline-001",
            name=entry["name"],
            bindings=_parse_node_binding_values(entry.get("bindings", {}), file_path, section_path),
            reads_map=_parse_str_map(entry.get("reads_map", {}), file_path, f"{section_path}.reads_map"),
            writes_map=_parse_str_map(entry.get("writes_map", {}), file_path, f"{section_path}.writes_map"),
        )
    if kind == "composition":
        # A composition node carries no bindings / reads_map / writes_map (pipeline/reference.md
        # § nodes: declaring any raises ContractViolation — the absence is structural in the IR).
        extra = set(entry) - {"kind", "name"}
        if extra:
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR, rule_id="R-pipeline-001",
                expected="a composition node entry declares only 'kind' and 'name'",
                actual=f"forbidden key(s) {sorted(extra)} on a composition node",
                remediation_hint=(
                    "a composition embed wires by flatten, not per-node maps: the engine flattens the "
                    "embedded composition's [inputs]/[outputs] into the outer graph BY NAME — rename the "
                    "composition's boundary ports (or the outer channels) so the names match, then drop "
                    "reads_map/writes_map (a composition supplies its internal bindings inside its own declaration)"
                ),
                file_path=file_path, section_path=section_path,
            )
        if "name" not in entry:
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
                expected=f"{section_path} declares a 'name' (composition declaration path)", actual="missing 'name'",
                file_path=file_path, section_path=section_path,
            )
        return _construct(CompositionNode, file_path, "composition node", rule_id="R-pipeline-001", name=entry["name"])

    raise ContractViolation(
        check=Check.CLOSED_GRAMMAR, rule_id="R-pipeline-001",
        expected="a node 'kind' of 'handler' or 'composition'", actual=f"unknown node kind {kind!r}",
        file_path=file_path, section_path=section_path,
    )


def _parse_node_binding_values(raw: object, file_path: str, section_path: str) -> tuple[NodeBindingValue, ...]:
    """Classify each supplied binding value as INLINE or EXTERNAL-FILE (the single classifier
    site, feeding both pipeline handler nodes and composition preprocessors).

    Per the decided inline-scalar inversion (handler/reference.md § Binding value-supply grammar):
    a bare scalar (or any non-`{file}` inline value) is **inline content**; the explicit
    ``{ file = "<path>" }`` form is the **external declaration file** reference. ``file`` is an
    engine-read binding key — a value of the shape ``{ file = "..." }`` is resolved by reading the
    named file, never treated as an inline object with a literal ``file`` field. The form is
    decidable at parse with no dependency on the handler's declared schema."""
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
            expected=f"{section_path}.bindings is a table of supplied binding values",
            actual=f"got {type(raw).__name__}", file_path=file_path, section_path=f"{section_path}.bindings",
        )
    values: list[NodeBindingValue] = []
    for name, value in raw.items():
        # The SAME `{ file }` classifier the compile-directive params use (`_as_file_ref`) — one
        # external-file recognition, never two. A `{ file = "<path>" }` value is the external
        # declaration-file reference; anything else is inline content (a bare scalar is the value
        # itself, not a path; an inline table is an inline object validated against the binding's
        # declared schema).
        file_ref = _as_file_ref(
            name, value, file_path=file_path,
            section_path=f"{section_path}.bindings.{name}", rule_id="R-pipeline-001",
        )
        if file_ref is None:
            # Spelling-check the reserved explicit-null form at parse (fail loud early, the
            # `{ file }` split): a well-spelled `{ null = true }` rides InlineBindingValue to
            # the compose join (validator.normalize), where its nullable-only admission and
            # its normalization to the bare null resolve — parse has no schema in scope.
            is_explicit_null(
                value, owner=f"bindings.{name}", file_path=file_path,
                section_path=f"{section_path}.bindings.{name}",
            )
        values.append(file_ref if file_ref is not None else InlineBindingValue(name=name, value=value))
    return tuple(values)


def _parse_str_map(raw: object, file_path: str, section_path: str) -> Mapping[str, str]:
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
            expected=f"{section_path} is a table of port → channel name strings",
            actual=f"got {type(raw).__name__}", file_path=file_path, section_path=section_path,
        )
    for port, channel in raw.items():
        if not isinstance(channel, str):
            # The map value is a plain channel-name STRING — no callable / expression / file path
            # (pipeline/reference.md § reads_map; the vector-6 data-only bound).
            raise ContractViolation(
                check=Check.WIRING_MAP_PORT, rule_id="R-pipeline-001",
                expected=f"wiring-map value for port '{port}' is a plain channel-name string",
                actual=f"got {type(channel).__name__}",
                remediation_hint="a wiring map routes a port to a channel by name; no callable/expression/path",
                file_path=file_path, section_path=f"{section_path}.{port}",
            )
    return dict(raw)


def _parse_supply_blocks(raw: object, file_path: str) -> tuple[ServiceBindingSupply, ...]:
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
            expected="[service_bindings.<name>] identity-supply blocks", actual=f"got {type(raw).__name__}",
            file_path=file_path, section_path="service_bindings",
        )
    supplies = []
    for name, block in raw.items():
        sp = f"service_bindings.{name}"
        if not isinstance(block, Mapping) or "type" not in block:
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
                expected=f"[{sp}] declares a 'type' (service-type qualified name)", actual="missing 'type'",
                file_path=file_path, section_path=sp,
            )
        # `config` is an engine-read key on a supply block — the entry's [config_schema]
        # value supply (pipeline/reference.md § service_bindings.<name>); every other
        # non-`type` key is an identity value. Coverage in both directions is the
        # compose-time check (service-type/reference.md § The [config_schema] contract).
        config = block.get("config", {})
        if not isinstance(config, Mapping):
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
                expected=f"[{sp}.config] is a table of [config_schema] values",
                actual=f"'config' is {type(config).__name__}",
                file_path=file_path, section_path=f"{sp}.config",
            )
        identity = {k: v for k, v in block.items() if k not in ("type", "config")}
        # PARSE-1 — the explicit-null SPELLING sweep runs over every engine-read value
        # position at parse (pipeline/conformance.md § Explicit-null form: a malformed
        # spelling is malformed-declaration AT PARSE, the { file } sibling's split), so a
        # parsed-and-registered-but-never-composed declaration cannot carry an undetected
        # malformed reserved form. Admission (nullable-declared target) stays compose's.
        for field_name, supplied in (*identity.items(), *config.items()):
            is_explicit_null(
                supplied, owner=f"{sp}.{field_name}", file_path=file_path,
                section_path=f"{sp}.{field_name}",
            )
        # Routed through _construct — the supply-side sibling of the ServiceBindingDecl
        # fix above (one fix-shape, two sites): a non-string `type` in a
        # [service_bindings.<name>] supply block raises the structured
        # MALFORMED_DECLARATION, never a raw pydantic ValidationError.
        supplies.append(
            _construct(
                ServiceBindingSupply, file_path,
                f"service-binding supply '{sp}'", rule_id="R-pipeline-001",
                name=name, type=block["type"], identity=identity, config=dict(config),
            )
        )
    return tuple(supplies)


def _parse_merge(raw: object, file_path: str) -> Mapping[str, MergeStrategy]:
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-002",
            expected="[merge] is a table of channel → strategy", actual=f"got {type(raw).__name__}",
            file_path=file_path, section_path="merge",
        )
    out: dict[str, MergeStrategy] = {}
    for channel, strategy in raw.items():
        try:
            out[channel] = MergeStrategy(strategy)
        except ValueError as exc:
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR, rule_id="R-pipeline-002",
                expected=f"merge strategy in the closed registry {[s.value for s in MergeStrategy]}",
                actual=f"unknown strategy {strategy!r} for channel '{channel}'",
                remediation_hint="the merge registry is closed; the 'aggregator' transform is the escape hatch",
                file_path=file_path, section_path=f"merge.{channel}",
            ) from exc
    return out


# ---------------------------------------------------------------------------
# 4 · Trainable composition declarations
# ---------------------------------------------------------------------------


def parse_composition(
    data: Mapping, *, file_path: str
) -> TrainableComposition | PipelineComposition | BundleComposition:
    """TOML → ``TrainableComposition`` / ``PipelineComposition`` / ``BundleComposition``,
    discriminated by ``meta.kind`` (handler/reference.md § composition-TOML + the
    ``kind-schemas/`` templates, R-handler-006 closed grammars; pipeline/reference.md
    § The nested ``pipeline`` composition kind — the pipeline-kind body is the pipeline
    grammar; glossary § Bundle TOML — the bundle body is a bare pipeline-``nodes``
    fragment)."""
    data = _require_mapping(data, file_path, "composition declaration")
    meta_raw = data.get("meta")
    if not isinstance(meta_raw, Mapping) or "kind" not in meta_raw:
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-handler-006",
            expected="a composition declaration has a [meta] block with a 'kind'", actual="missing [meta].kind",
            file_path=file_path, section_path="meta",
        )
    try:
        kind = CompositionKind(meta_raw["kind"])
    except ValueError as exc:
        raise ContractViolation(
            check=Check.UNKNOWN_COMPOSITION_KIND, rule_id="R-handler-006",
            expected=f"meta.kind in the closed composition-kind enum {[k.value for k in CompositionKind]}",
            actual=f"unknown meta.kind {meta_raw['kind']!r}", file_path=file_path, section_path="meta.kind",
        ) from exc
    if kind is CompositionKind.BUNDLE:
        return _parse_bundle_composition(data, meta_raw, file_path)
    if kind is CompositionKind.PIPELINE:
        return _parse_pipeline_composition(data, meta_raw, file_path)

    allowed = {"meta", "inputs", "outputs", "preprocessors", "service_bindings", "trainable", "merge", "annotations"}
    _closed_grammar(file_path, "R-handler-006", allowed, set(data), "trainable composition")
    if "name" not in meta_raw:
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-handler-006",
            expected="[meta] declares a 'name'", actual="missing meta.name",
            file_path=file_path, section_path="meta.name",
        )
    # Inner closed grammar: a composition [meta] block declares only {kind, name} (the family
    # rule — handler/reference.md § A composition mirrors the pipeline; mirrors the pipeline
    # [meta] plus the composition-kind discriminator). No declaration-level `description`
    # (author prose → [annotations]; a schema field's `description` → a trainable's
    # `output_schema`); an unknown inner key raises CLOSED_GRAMMAR.
    _closed_grammar(file_path, "R-handler-006", {"kind", "name"}, set(meta_raw), "composition [meta]")
    # Route through error-translating construction so any residual pydantic ValidationError
    # surfaces as a clean ContractViolation (consistent with every other IR construction here).
    meta = _construct(
        CompositionMeta, file_path, "composition meta", rule_id="R-handler-006",
        kind=kind, name=meta_raw["name"],
    )

    _require_present(file_path, data, "inputs", "trainable composition")
    _require_present(file_path, data, "outputs", "trainable composition")
    # Boundary fields admit no constraint keywords and no validators list (the same
    # presence-only-boundary rule the pipeline's [inputs]/[outputs] carry).
    inputs = parse_schema_section(
        data.get("inputs", {}), file_path=file_path, section_path="inputs",
        constraints_forbidden_rule_id="R-handler-006",
    )
    outputs = parse_schema_section(
        data.get("outputs", {}), file_path=file_path, section_path="outputs",
        constraints_forbidden_rule_id="R-handler-006",
    )
    _require_body(outputs, file_path, "outputs", "trainable composition")

    preprocessors = _parse_preprocessors(data.get("preprocessors", ()), file_path)
    # The composition's OWN service-binding identity supply (divergence A, shape-i): reuse the
    # pipeline's `[service_bindings.<name>]` supply parser — one feature, one grammar, one parser
    # across both layers (the mirror-pipeline principle).
    service_bindings = _parse_supply_blocks(data.get("service_bindings", {}), file_path)
    trainable_node = _parse_trainable_node(data.get("trainable"), file_path)
    merge = _parse_merge(data.get("merge", {}), file_path)

    return _construct(
        TrainableComposition, file_path, "trainable composition", rule_id="R-handler-006",
        meta=meta, inputs=inputs, outputs=outputs, preprocessors=preprocessors,
        service_bindings=service_bindings, trainable=trainable_node, merge=merge,
        annotations=_parse_annotations(data, file_path),
    )


def _parse_bundle_composition(
    data: Mapping, meta_raw: Mapping, file_path: str
) -> BundleComposition:
    """The ``meta.kind = "bundle"`` arm — the pure-substitution composition kind
    (glossary § Bundle TOML + ``bundle.schema.toml``): the minimal grammar is ``[meta]``
    ``{kind, name}``, a non-empty ``[[nodes]]`` sequence in the EXACT pipeline
    node-entry grammar (parsed through THE shared node parser — the mirror-pipeline
    principle, never a parallel mechanism), optionally ``[annotations]``. No
    ``[inputs]``/``[outputs]`` boundary, no ``[merge]``, no ``[service_bindings.<name>]``
    — a bundle's channels continue the enclosing scope by name after substitution, and
    the enclosing unit's own compose-time validation diagnoses the post-substitute
    graph."""
    allowed = {"meta", "nodes", "annotations"}
    _closed_grammar(file_path, "R-handler-006", allowed, set(data), "bundle composition")
    if "name" not in meta_raw:
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-handler-006",
            expected="[meta] declares a 'name'", actual="missing meta.name",
            file_path=file_path, section_path="meta.name",
        )
    # Inner closed grammar: a composition [meta] block declares only {kind, name} (the family
    # rule; no declaration-level `description` — author prose lives in [annotations]).
    _closed_grammar(file_path, "R-handler-006", {"kind", "name"}, set(meta_raw), "composition [meta]")
    meta = _construct(
        CompositionMeta, file_path, "composition meta", rule_id="R-handler-006",
        kind=CompositionKind.BUNDLE, name=meta_raw["name"],
    )
    nodes_raw = data.get("nodes")
    if not isinstance(nodes_raw, (list, tuple)) or not nodes_raw:
        raise ContractViolation(
            check=Check.BODY_REQUIRED, rule_id="R-handler-006",
            expected="a bundle declares a non-empty [[nodes]] sequence (the substituted "
                     "content — an empty bundle substitutes nothing)",
            actual="missing or empty nodes",
            file_path=file_path, section_path="nodes",
        )
    nodes = tuple(_parse_node(entry, i, file_path) for i, entry in enumerate(nodes_raw))
    return _construct(
        BundleComposition, file_path, "bundle composition", rule_id="R-handler-006",
        meta=meta, nodes=nodes,
        annotations=_parse_annotations(data, file_path) or {},
    )


def _parse_pipeline_composition(
    data: Mapping, meta_raw: Mapping, file_path: str
) -> PipelineComposition:
    """The ``meta.kind = "pipeline"`` arm — a nested ``pipeline`` composition declaration
    (pipeline/reference.md § The nested ``pipeline`` composition kind). The body IS the
    pipeline grammar, parsed through THE shared pipeline body parser (the mirror-pipeline
    principle: one grammar, one parser — never a parallel mechanism), including the
    pipeline's presence-opts-in ``[outputs]`` arm. Only the ``[meta]`` layer differs: it
    carries the composition-kind discriminator alongside the family-rule self-name."""
    allowed = {"meta", "nodes", "service_bindings", "merge", "inputs", "outputs"}
    _closed_grammar(file_path, "R-pipeline-001", allowed, set(data), "nested pipeline composition")
    if "name" not in meta_raw:
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-handler-006",
            expected="[meta] declares a 'name'", actual="missing meta.name",
            file_path=file_path, section_path="meta.name",
        )
    # Inner closed grammar: a composition [meta] block declares only {kind, name} (the family
    # rule; mirrors the pipeline [meta] plus the composition-kind discriminator — no
    # declaration-level `description`).
    _closed_grammar(file_path, "R-handler-006", {"kind", "name"}, set(meta_raw), "composition [meta]")
    meta = _construct(
        CompositionMeta, file_path, "composition meta", rule_id="R-handler-006",
        kind=CompositionKind.PIPELINE, name=meta_raw["name"],
    )
    # The body constructs a whole PipelineDeclaration — the exact pipeline grammar the
    # compiler / hasher / runner already consume. Its meta mirrors this composition's
    # (minus `kind`); both are identity, never hashed (the family rule).
    inner = _construct(
        PipelineDeclaration, file_path, "nested pipeline composition", rule_id="R-pipeline-001",
        meta=PipelineMeta(name=meta.name),
        **_parse_pipeline_body(data, file_path, "nested pipeline composition"),
    )
    return _construct(
        PipelineComposition, file_path, "nested pipeline composition", rule_id="R-handler-006",
        meta=meta, pipeline=inner,
    )


def _parse_preprocessors(raw: object, file_path: str) -> tuple[PreprocessorEntry, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-handler-006",
            expected="[[preprocessors]] is an array of handler entries", actual=f"got {type(raw).__name__}",
            file_path=file_path, section_path="preprocessors",
        )
    # Closed preprocessor-entry grammar: a preprocessor is a regular handler node inside the
    # trainable's scope — a NAME-REFERENCE to a registered handler, exactly like an outer
    # pipeline node (parse._parse_node handler arm). The entry head carries the outer
    # pipeline's node-entry grammar — `kind = "handler"` + `name` (the qualified handler) +
    # the supplied `bindings` VALUES + the wiring maps — plus the one composition-layer
    # addition, the composition-local `id` (trainable.schema.toml § [[preprocessors]]). The
    # ports, the binding declarations (delivery/default/validation), and a hook's
    # `transport_schema` are owned by the REFERENCED handler declaration (resolved at compile)
    # and are never inlined here. An unknown key raises CLOSED_GRAMMAR.
    allowed = {"kind", "name", "id", "bindings", "reads_map", "writes_map"}
    out = []
    for idx, entry in enumerate(raw):
        sp = f"preprocessors[{idx}]"
        if not isinstance(entry, Mapping) or not {"kind", "name", "id"} <= set(entry):
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-handler-006",
                expected=f"{sp} declares 'kind', 'name' (the qualified handler), and 'id' (the composition-local label)",
                actual="missing 'kind', 'name', or 'id'",
                file_path=file_path, section_path=sp,
            )
        if entry["kind"] != "handler":
            hint = (
                "a [[preprocessors]] entry is an id-labeled handler name-reference, and a "
                "trainable's preprocessor sequence admits handler entries ONLY (by design: "
                "the id is a load-bearing address and a substituted node is anonymous) — "
                "composition embeds live at the unlabeled pipeline-family nodes layers"
                if entry["kind"] == "composition"
                else "a [[preprocessors]] entry is a regular handler node"
            )
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR, rule_id="R-handler-006",
                expected=f"{sp} kind is 'handler' (the one admitted preprocessor-entry kind)",
                actual=f"kind {entry['kind']!r}", remediation_hint=hint,
                file_path=file_path, section_path=sp,
            )
        _closed_grammar(file_path, "R-handler-006", allowed, set(entry), f"{sp} preprocessor entry")
        # Name-reference: the ports + binding declarations + a hook's transport_schema are
        # owned by the referenced handler declaration (resolved at compile), so the entry
        # carries only the supplied binding VALUES + the wiring maps. The transport-field
        # collision is enforced at the referenced hook handler's own declaration parse.
        out.append(
            _construct(
                PreprocessorEntry, file_path, "preprocessor entry", rule_id="R-handler-006",
                kind=entry["kind"], name=entry["name"], id=entry["id"],
                bindings=_parse_node_binding_values(entry.get("bindings", {}), file_path, sp),
                reads_map=_parse_str_map(entry.get("reads_map", {}), file_path, f"{sp}.reads_map"),
                writes_map=_parse_str_map(entry.get("writes_map", {}), file_path, f"{sp}.writes_map"),
            )
        )
    return tuple(out)


def _parse_trainable_node(raw: object, file_path: str) -> TrainableNode:
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-handler-010",
            expected="a [trainable] terminal node table", actual=f"got {type(raw).__name__}",
            file_path=file_path, section_path="trainable",
        )
    # The [trainable] node has NO author body (R-handler-010): TrainableNode carries no
    # name/callable field, so naming a callable is structurally rejected. Detect a stray
    # 'name'/'type' (an attempt to register an author handler) with a precise diagnostic.
    allowed = {"streamable", "config", "service_bindings", "reads", "output_schema"}
    stray = set(raw) - allowed
    if stray & {"name", "type"}:
        raise ContractViolation(
            check=Check.CLOSED_GRAMMAR, rule_id="R-handler-010",
            expected="a [trainable] node has no author body (no 'name'/'type' callable)",
            actual=f"author-callable key(s) {sorted(stray & {'name', 'type'})} on [trainable]",
            remediation_hint="the trainable composition kind admits no author callable; the dispatch is engine-constructed",
            file_path=file_path, section_path="trainable",
        )
    if stray:
        raise ContractViolation(
            check=Check.CLOSED_GRAMMAR, rule_id="R-handler-006",
            expected=f"[trainable] subsections in {sorted(allowed)}", actual=f"unknown key(s) {sorted(stray)}",
            file_path=file_path, section_path="trainable",
        )
    _require_present(file_path, raw, "service_bindings", "trainable node")
    _require_present(file_path, raw, "reads", "trainable node")
    _require_present(file_path, raw, "output_schema", "trainable node")
    # [trainable.config] is a required value-supply block (trainable.schema.toml § [trainable.config]
    # — "required; empty-allowed"; architecture/exhaustive-declaration.md § the value-supply carve-out
    # — a required value-supply block requires its header PRESENT). Its absence must raise, not
    # default to {} (the silent-default would accept a forgotten header).
    _require_present(file_path, raw, "config", "trainable node")
    reads = parse_schema_section(raw.get("reads", {}), file_path=file_path, section_path="trainable.reads")
    # `trainable.output_schema` is the ONE field position where `description` is admitted —
    # model-facing contract content the bound wire compiles into the decode constraint
    # (handler/reference.md § description-admission; hash-model.md § What the pipeline-hash
    # absorbs). `trainable.reads` (above) and every other schema section reject it.
    output_schema = parse_schema_section(
        raw.get("output_schema", {}), file_path=file_path,
        section_path="trainable.output_schema", allow_description=True,
    )
    _require_body(reads, file_path, "trainable.reads", "trainable node")
    _require_body(output_schema, file_path, "trainable.output_schema", "trainable node")
    config = raw.get("config", {})
    if not isinstance(config, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-handler-011",
            expected="[trainable.config] is a table of generation-parameter VALUES", actual=f"got {type(config).__name__}",
            file_path=file_path, section_path="trainable.config",
        )
    # `streamable` is an optional [trainable] field (default false; trainable.schema.toml
    # § [trainable]). When DECLARED it MUST carry an explicit TOML boolean — the same
    # explicit-boolean discipline the deployment integrity opt-in carries (PARSE-F2). A
    # `bool(...)` wrapper would truthiness-coerce ("false" -> True, 0 -> False), masking the
    # correct-or-loud parse pydantic gives — so require a real boolean and let it reach the IR
    # uncoerced. A forward-stub today (engine streaming implementation-gated), but the coercion
    # class is the one the engine forbids, so it fails loud now (`bool` is an `int` subclass, so
    # `isinstance(., bool)` is the exact guard — admitting only a real TOML boolean).
    if "streamable" in raw and not isinstance(raw["streamable"], bool):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-handler-010",
            expected="[trainable].streamable is an explicit boolean (true / false)",
            actual=f"got {raw['streamable']!r} ({type(raw['streamable']).__name__})",
            remediation_hint="declare streamable = true or false — a non-boolean is not coerced",
            file_path=file_path, section_path="trainable.streamable",
        )
    return _construct(
        TrainableNode, file_path, "trainable node", rule_id="R-handler-010",
        streamable=raw.get("streamable", False), config=dict(config),
        service_bindings=_parse_service_binding_decls(raw.get("service_bindings", {}), file_path, "trainable.service_bindings"),
        reads=reads, output_schema=output_schema,
    )


# ---------------------------------------------------------------------------
# 5 · Deployment declarations
# ---------------------------------------------------------------------------


def parse_deployment(data: Mapping, *, file_path: str) -> DeploymentDeclaration:
    """TOML → ``DeploymentDeclaration`` (R-deployment-001 closed grammar; training_contract
    required-body-required)."""
    data = _require_mapping(data, file_path, "deployment declaration", rule_id="R-deployment-001")
    allowed = {"transport", "hook_transport", "training_contract", "training_export", "artifacts", "acknowledged_drift", "annotations", "pipelines"}
    _closed_grammar(file_path, "R-deployment-001", allowed, set(data), "deployment")

    if "training_contract" not in data:
        raise ContractViolation(
            check=Check.SECTION_PRESENCE, rule_id="R-deployment-001",
            expected="deployment declares [training_contract] (required, body-required)",
            actual="[training_contract] absent",
            remediation_hint="add [training_contract] with an explicit integrity_enforcement boolean",
            file_path=file_path, section_path="training_contract",
        )
    tc_raw = data["training_contract"]
    if not isinstance(tc_raw, Mapping) or "integrity_enforcement" not in tc_raw:
        raise ContractViolation(
            check=Check.BODY_REQUIRED, rule_id="R-deployment-001",
            expected="[training_contract].integrity_enforcement is an explicit boolean",
            actual="integrity_enforcement absent", file_path=file_path, section_path="training_contract",
        )
    # Closed [training_contract] body: the block declares exactly {integrity_enforcement}
    # (required) plus the OPTIONAL {audit_enforcement} (the TrainingContract IR's two
    # fields). An unknown key — a typo'd or misplaced enforcement-adjacent setting — must
    # raise like every other closed inner block here, never vanish silently (the whole
    # table is read, not just the known keys).
    _closed_grammar(
        file_path, "R-deployment-001",
        {"integrity_enforcement", "audit_enforcement"}, set(tc_raw),
        "[training_contract]",
    )
    # integrity_enforcement MUST carry an EXPLICIT TOML boolean (R-deployment-001 §
    # training_contract; architecture/exhaustive-declaration § Required, body-required — the
    # I4 integrity-enforcement opt-in is a load-bearing affirmative-or-negative choice). The
    # lenient pydantic bool validator would silently coerce "yes"/1/"0" into a boolean; a
    # misread opt-in is training-contract corruption, so a non-boolean fails loud here, never
    # coerced (PARSE-F1). `bool` is an `int` subclass, so `isinstance(., bool)` is the exact
    # guard — it admits only a real TOML boolean, rejecting the int/str the validator would coerce.
    integrity_value = tc_raw["integrity_enforcement"]
    if not isinstance(integrity_value, bool):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
            expected="[training_contract].integrity_enforcement is an explicit boolean (true / false)",
            actual=f"got {integrity_value!r} ({type(integrity_value).__name__})",
            remediation_hint="declare integrity_enforcement = true or false — a non-boolean is "
                             "not coerced; the integrity opt-in must be an explicit boolean",
            file_path=file_path, section_path="training_contract.integrity_enforcement",
        )
    # audit_enforcement is the OPTIONAL audit-stamp opt-in (deployment/reference.md §
    # training_contract, region audit-enforcement) — a boolean defaulting to false when
    # omitted. When PRESENT it gets the same explicit-boolean guard integrity_enforcement
    # gets: the lenient pydantic bool validator would coerce "yes"/1/"0", but a misread
    # enforcement opt-in is training-contract corruption, so a non-bool fails loud, never
    # coerced (the same isinstance(., bool) exact guard).
    if "audit_enforcement" in tc_raw:
        audit_value = tc_raw["audit_enforcement"]
        if not isinstance(audit_value, bool):
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
                expected="[training_contract].audit_enforcement is an explicit boolean (true / false)",
                actual=f"got {audit_value!r} ({type(audit_value).__name__})",
                remediation_hint="declare audit_enforcement = true or false — a non-boolean is "
                                 "not coerced; the audit-stamp opt-in must be an explicit boolean",
                file_path=file_path, section_path="training_contract.audit_enforcement",
            )
    else:
        audit_value = False  # optional — defaults to false when omitted
    training_contract = _construct(
        TrainingContract, file_path, "training_contract", rule_id="R-deployment-001",
        integrity_enforcement=integrity_value, audit_enforcement=audit_value,
    )

    transport = _parse_transport_blocks(data.get("transport", {}), file_path, "transport")
    hook_transport = _parse_hook_transport_blocks(data.get("hook_transport", {}), file_path, "hook_transport")
    pipelines = _parse_pipeline_overrides(data.get("pipelines", {}), file_path)

    training_export = data.get("training_export")  # presence-toggling: None vs mapping
    if training_export is not None and not isinstance(training_export, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
            expected="[training_export] is a (possibly empty) table", actual=f"got {type(training_export).__name__}",
            file_path=file_path, section_path="training_export",
        )
    # artifacts: trainable composition name -> artifact file path (deployment/reference.md
    # § artifacts — the trained-artifact registration surface R-pipeline-003 compares from).
    # Truly optional; when present, a table of string -> string only — a non-string key or
    # value is malformed and fails loud (a mis-typed registration would silently skip the
    # very comparison the integrity opt-in promises).
    artifacts_raw = data.get("artifacts", {})
    if not isinstance(artifacts_raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
            expected="[artifacts] is a (possibly empty) table mapping trainable name -> artifact path",
            actual=f"got {type(artifacts_raw).__name__}",
            file_path=file_path, section_path="artifacts",
        )
    artifacts: dict[str, str] = {}
    for trainable_name, artifact_path in artifacts_raw.items():
        if not isinstance(artifact_path, str) or not artifact_path:
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
                expected=f"artifacts['{trainable_name}'] is a non-empty artifact file path string",
                actual=f"got {artifact_path!r}",
                remediation_hint='supply the artifact path, e.g. "loras/alice_dialogue.safetensors"',
                file_path=file_path, section_path=f"artifacts.{trainable_name}",
            )
        artifacts[trainable_name] = artifact_path

    # acknowledged_drift: artifact path -> list of trainable qualified names whose drift it
    # accepts (deployment/reference.md § acknowledged_drift). Each entry value MUST be a list of
    # strings; a bare string / non-list / non-string member is malformed and fails loud here —
    # never silently tuple()-shredded (a string "foo" would otherwise become ('f','o','o')).
    ack_raw = data.get("acknowledged_drift", {})
    if not isinstance(ack_raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
            expected="[acknowledged_drift] is a (possibly empty) table mapping artifact -> list of trainable names",
            actual=f"got {type(ack_raw).__name__}",
            file_path=file_path, section_path="acknowledged_drift",
        )
    acknowledged = {}
    for artifact, names in ack_raw.items():
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
                expected=f"acknowledged_drift['{artifact}'] is a list of trainable qualified-name strings",
                actual=f"got {names!r}",
                remediation_hint='supply a TOML array of strings, e.g. ["mypkg.dialogue_trainable"]',
                file_path=file_path, section_path=f"acknowledged_drift.{artifact}",
            )
        acknowledged[artifact] = tuple(names)
    return _construct(
        DeploymentDeclaration, file_path, "deployment", rule_id="R-deployment-001",
        transport=transport, hook_transport=hook_transport, training_contract=training_contract,
        training_export=dict(training_export) if isinstance(training_export, Mapping) else None,
        artifacts=artifacts,
        acknowledged_drift=acknowledged, annotations=_parse_annotations(data, file_path), pipelines=pipelines,
    )


def _parse_transport_blocks(raw: object, file_path: str, section: str) -> tuple[TransportBlock, ...]:
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
            expected=f"[{section}.<name>] blocks", actual=f"got {type(raw).__name__}",
            file_path=file_path, section_path=section,
        )
    out = []
    for name, values in raw.items():
        if not isinstance(values, Mapping):  # a non-mapping block body is malformed, NOT empty
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
                expected=f"[{section}.{name}] block body is a table of transport field values",
                actual=f"got {type(values).__name__}",
                file_path=file_path, section_path=f"{section}.{name}",
            )
        # PARSE-1 — the explicit-null spelling sweep at parse (a malformed reserved form
        # is malformed-declaration here, the { file } sibling's split; admission stays
        # compose's nullable-target check).
        for field_name, supplied in values.items():
            is_explicit_null(
                supplied, owner=f"{section}.{name}.{field_name}", file_path=file_path,
                section_path=f"{section}.{name}.{field_name}", rule_id="R-deployment-001",
            )
        out.append(TransportBlock(name=name, values=dict(values)))
    return tuple(out)


def _parse_hook_transport_blocks(raw: object, file_path: str, section: str) -> tuple[HookTransportBlock, ...]:
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
            expected=f'[{section}."<qn>"] blocks', actual=f"got {type(raw).__name__}",
            file_path=file_path, section_path=section,
        )
    out = []
    for qn, values in raw.items():
        if not isinstance(values, Mapping):  # a non-mapping block body is malformed, NOT empty
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-001",
                expected=f'[{section}."{qn}"] block body is a table of transport field values',
                actual=f"got {type(values).__name__}",
                file_path=file_path, section_path=f"{section}.{qn}",
            )
        # PARSE-1 — the same spelling sweep as the transport blocks above.
        for field_name, supplied in values.items():
            is_explicit_null(
                supplied, owner=f'{section}."{qn}".{field_name}', file_path=file_path,
                section_path=f"{section}.{qn}.{field_name}", rule_id="R-deployment-001",
            )
        out.append(HookTransportBlock(hook_qualified_name=qn, values=dict(values)))
    return tuple(out)


def _parse_pipeline_overrides(raw: object, file_path: str) -> tuple[PipelineOverride, ...]:
    if not isinstance(raw, Mapping):
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-002",
            expected="[pipelines.<name>...] override blocks", actual=f"got {type(raw).__name__}",
            file_path=file_path, section_path="pipelines",
        )
    out = []
    for qn, block in raw.items():
        if not isinstance(block, Mapping):  # a non-mapping override block body is malformed, NOT empty
            raise ContractViolation(
                check=Check.MALFORMED_DECLARATION, rule_id="R-deployment-002",
                expected=f"[pipelines.{qn}...] override block body is a table",
                actual=f"got {type(block).__name__}",
                file_path=file_path, section_path=f"pipelines.{qn}",
            )
        # Closed override-block grammar (R-deployment-002: "Only transport /
        # hook_transport accept per-pipeline override"). A key outside that set — a
        # typo'd `transprot` that would silently fall back to the shared block, or a
        # canon-forbidden per-pipeline environment-posture override (e.g. a
        # [pipelines."<qn>".training_contract]) — must raise, never silently no-op:
        # the author would believe an override applied while the deployment-wide value
        # silently governs (the I4 masking class).
        unknown = set(block) - {"transport", "hook_transport"}
        if unknown:
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR, rule_id="R-deployment-002",
                expected=f"[pipelines.{qn}] override sections in the closed set "
                         "['hook_transport', 'transport'] — only transport / "
                         "hook_transport accept per-pipeline override",
                actual=f"unknown override section(s) {sorted(unknown)}",
                remediation_hint="an override re-wires transport / hook_transport for one "
                                 "pipeline; the environment-posture sections "
                                 "(training_contract, training_export, acknowledged_drift) "
                                 "are deployment-wide — declare them at top level",
                file_path=file_path, section_path=f"pipelines.{qn}",
            )
        out.append(
            PipelineOverride(
                pipeline_qualified_name=qn,
                transport=_parse_transport_blocks(block.get("transport", {}), file_path, f"pipelines.{qn}.transport"),
                hook_transport=_parse_hook_transport_blocks(block.get("hook_transport", {}), file_path, f"pipelines.{qn}.hook_transport"),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# IR construction wrapper + TOML entry points
# ---------------------------------------------------------------------------


def _construct(model, file_path: str, what: str, *, rule_id: str, **kwargs):
    """Construct an IR model, translating any residual pydantic ``ValidationError`` (the
    structural ``extra="forbid"`` rejection the per-kind checks above didn't pre-empt) into
    the engine's ``ContractViolation`` — so the loader never leaks a raw pydantic error.
    ``rule_id`` is the owning rule of the declaration class (PARSE-F3); each caller names its
    own (R-handler-006 for handler / composition, R-service-type-001 / R-pipeline-001 /
    R-handler-010 / R-deployment-001 for the others) so the residual diagnostic cites it."""
    try:
        return model(**kwargs)
    except ValidationError as exc:
        raise _translate_validation_error(exc, file_path, what, rule_id) from exc


#: Owning rule per declaration kind — the rule a kind-level diagnostic (e.g. a TOML syntax
#: error, before any section parses) cites (PARSE-F3). handler + composition share the
#: handler/composition grammar owner; the others own their own rule. Mirrors parse()'s kinds.
_KIND_OWNING_RULE = {
    "handler": "R-handler-006",
    "composition": "R-handler-006",
    "pipeline": "R-pipeline-001",
    "service_type": "R-service-type-001",
    "deployment": "R-deployment-001",
}


def loads(toml_text: str, kind: str, *, file_path: str = "<string>"):
    """Parse a TOML *string* of the given declaration ``kind`` into its IR model.

    ``kind`` ∈ {handler, service_type, pipeline, composition, deployment}. A TOML syntax
    error is translated to ``ContractViolation`` (so callers and the fuzz harness see one
    error class). Stage-1 entry point used by fixtures and the registry loaders.
    """
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        # The kind is in hand at the decode site — cite the declaration class's OWNING rule
        # (PARSE-F3), not the generic handler-flavored fallback. `.get(...)` guards a
        # malformed-TOML + unknown-kind double error (the unknown-kind ValueError fires later
        # in parse(), which the decode path never reaches).
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION,
            rule_id=_KIND_OWNING_RULE.get(kind, "R-handler-006"),
            expected="a syntactically valid TOML declaration", actual=f"TOML parse error: {exc}",
            file_path=file_path,
        ) from exc
    return parse(data, kind, file_path=file_path)


def parse(data: Mapping, kind: str, *, file_path: str):
    """Dispatch a parsed TOML mapping to the right declaration loader."""
    loaders = {
        "handler": parse_handler,
        "service_type": parse_service_type,
        "pipeline": parse_pipeline,
        "composition": parse_composition,
        "deployment": parse_deployment,
    }
    loader = loaders.get(kind)
    if loader is None:
        raise ValueError(f"unknown declaration kind {kind!r}; expected one of {sorted(loaders)}")
    return loader(data, file_path=file_path)
