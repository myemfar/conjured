"""``conjured.errors`` — the engine's typed failure surface.

The engine surfaces failure through a **closed enum of three error classes**
(``conjured/docs/components/error-channel/reference.md`` § The closed enum of error
classes; R-error-channel-001): ``ContractViolation`` (structural type-check failure),
``SchemaValidationError`` (value-level type-check failure), and ``PipelineFailure``
(runtime failure). This module is the home of that surface.

**Built so far:** ``ContractViolation`` (Phase 1a — the declaration validator's output,
every compose-time check raises it before any handler dispatches; Phase 2 adds its
dispatch-time uses with an optional ``pipeline_run_id``), ``SchemaValidationError``
(Phase 2 — the dispatch-time value-level class with its full decided required payload,
raised at the two validation boundaries), and ``PipelineFailure`` (Phase 3 — the runtime
wrap class with the full decided payload, constructed only by the runner's
dispatch-boundary wrap + the cooperative pipeline-level timeout). The closed enum is now
complete.

The ``_render`` implementations are the **canonical default message templates**
(error-channel/reference.md § The rendered message cites its rule owns the citation
contract). The rule-bearing classes render their discriminator through one slot: the
catalog ``audit_code`` once assigned, the symbolic :class:`Check` value until then. The
RFC-9457 HTTP wire projection (R-error-channel-005) is realized in the server component
(``conjured.server.problem_details``, the projection its HTTP boundary applies) — never
here (this module stays the pure typed-error surface).

**On ``audit_code`` and the registration API.** error-channel/reference.md makes
``audit_code`` (format ``<CX>.<TOPIC>.<NNN>``) the primary dispatch key, but the audit
*catalog* and the ``<CX>`` allocation rule are deferred. We do not invent catalog codes:
``audit_code`` is carried as an optional field (``None`` until the catalog lands), and the
canonical ``rule_id`` plus a stable symbolic :class:`Check` discriminator (1:1 with the
conformance-check names) are the assertion / dispatch key in the meantime. The
**registered error set** — :data:`CHECK_REGISTRY` (every :class:`Check` member's enforced
rule_ids + raising error class) and :data:`AUDIT_CODE_REGISTRY` (the decided catalog
codes) — is the registration API the error-index codegen reads
(``tools/gen_error_index.py``; the STUB-R4 ruling, 2026-06-10: the registration API, not
an AST walk). The constructors below reject an unregistered ``audit_code`` and an
unregistered ``(check, rule_id)`` pair, so the generated index is **complete by
construction**: a raise site cannot emit a discriminator the index does not carry.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Mapping, overload


class Check(str, enum.Enum):
    """The stable symbolic discriminator naming *which* check fired.

    One member per mechanically-enforced check, named after the corresponding
    ``conformance.md`` check (the canonical names). This is the pre-catalog stand-in for
    ``audit_code`` (deferred) and the field tests and consumers dispatch on. Closed-enum:
    a new check is an engine change, exactly as the audit catalog will be. Phase 2 adds
    the resolution-seal members (``handler-resolution.md`` steps 3–7; the adapter sibling
    mechanism) and the dispatch-boundary members (R-handler-001 output-validation routing;
    the two SVE halt audits, whose ``audit_code`` strings are already decided canon —
    error-channel/reference.md § SchemaValidationError payload).
    """

    # -- Stage 1: declaration parse / load --------------------------------------------
    #: Handler declaration carries zero or more-than-one top-level kind header
    #: (R-handler-003 / R-handler-006; handler/conformance.md § Top-level kind header).
    HANDLER_KIND_HEADER = "handler-kind-header"
    #: A block / field / section outside the closed per-kind grammar — the diagnostic
    #: translation of the IR's ``extra="forbid"`` (R-handler-006 / R-service-type-001 /
    #: R-deployment-001; handler/conformance.md § Closed handler-declaration grammar).
    CLOSED_GRAMMAR = "closed-grammar"
    #: A required-empty-allowed section header is absent from the declaration text — the
    #: presence discipline the IR deliberately does not encode (exhaustive-declaration.md
    #: § The section-discipline modes; a defaulted empty tuple cannot distinguish
    #: "present-but-empty" from "absent").
    SECTION_PRESENCE = "section-presence"
    #: A required-body-required section is present but empty / declares < 1 field
    #: (exhaustive-declaration.md; e.g. transform/service ``output_schema``, service-type
    #: ``identity_schema`` / ``transport_schema``, trainable ``trainable.output_schema``).
    BODY_REQUIRED = "body-required"
    #: A declared channel-field type token is outside the engine's Pydantic IR token
    #: grammar (handler/reference.md § Types allowed; ``bytes`` has no TOML token).
    CHANNEL_TYPE_TOKEN = "channel-type-token"
    #: ``nullable`` / ``"<T> | None"`` declared on a non-transport field (service-type/
    #: reference.md § ``[transport_schema]`` — nullable is transport-only).
    NULLABLE_PLACEMENT = "nullable-placement"
    #: A composition declaration's ``meta.kind`` is outside the closed composition-kind
    #: enum (R-handler-006 closed composition-kind grammar — the composition-declaration
    #: counterpart of the bare-function ``handler-kind-header`` check; composition.py
    #: ``CompositionKind``). Matches CHECK_REGISTRY + the parse.py raise site + the error-index.
    UNKNOWN_COMPOSITION_KIND = "unknown-composition-kind"
    #: A declaration is structurally malformed in a way the per-kind translation above
    #: does not name specifically — the residual translation of a pydantic
    #: ``ValidationError`` into the engine's diagnostic (keeps the fuzz harness's
    #: "compile or ContractViolation, never another exception" guarantee).
    MALFORMED_DECLARATION = "malformed-declaration"

    # -- Stage 2: compose-time validation + graph compilation -------------------------
    #: A ``nodes`` entry's qualified name resolves to no declaration in the registry
    #: (the 1a slice of pipeline/conformance.md § Handler-name resolution failure — the
    #: module import / AST / shape / signature seals are Phase 2).
    HANDLER_NAME_RESOLUTION = "handler-name-resolution"
    #: A ``service_bindings.<name>.type`` resolves to no registered service-type
    #: declaration (1a slice of § Service-type resolution failure; adapter resolution is
    #: Phase 2).
    SERVICE_TYPE_RESOLUTION = "service-type-resolution"
    #: The hasher's own-hash-domain **structural backstop** (a sibling of the cycle /
    #: unresolved-file guards): the by-reference training-bundle-hash fold is an
    #: own-hash-domain allowlist, so a pure-substitution bundle (or any future
    #: non-own-hash-domain kind) reaching it fails loud rather than being silently
    #: folded by reference. A bundle has no own hash domain — it is textually
    #: substituted out at every walker's entry (``conjured.ir.substitute``), before
    #: scoping and hashing (``hash-model.md`` § What the pipeline-hash absorbs) — so
    #: this firing means a walk forgot to substitute (engine drift), never author error.
    BUNDLE_REACHES_BYREF_FOLD = "bundle-reaches-byref-fold"
    #: A nested ``pipeline`` composition transitively embeds itself — the only
    #: non-terminating case under static nesting, rejected when the engine resolves the
    #: embed graph at compose, before any node dispatches (pipeline/reference.md § The
    #: nested ``pipeline`` composition kind, Termination — structural, not a runtime
    #: guard: a cyclic composition never loads, so it can never run; a finite acyclic
    #: nesting has no depth ceiling).
    COMPOSITION_CYCLE = "composition-embed-cycle"
    #: Two ports wired to one channel declare different types
    #: (pipeline/conformance.md § Read/write shape mismatch; R-pipeline-001).
    READ_WRITE_SHAPE = "read-write-shape-mismatch"
    #: A ``reads_map`` / ``writes_map`` key names an undeclared port or maps a port twice
    #: (§ Undeclared or doubly-mapped port in a node wiring map).
    WIRING_MAP_PORT = "wiring-map-port"
    #: An unmapped input-port's same-named channel is neither upstream-written nor in
    #: ``[inputs]`` (§ Dangling identity port).
    DANGLING_IDENTITY_PORT = "dangling-identity-port"
    #: An author-wired read-port channel has no upstream writer and no ``[inputs]`` entry
    #: (§ Read-port channel not closed by an upstream write or ``[inputs]``).
    READ_PORT_UNCLOSED = "read-port-unclosed"
    #: A node wires a read-port and an output-port to the same channel
    #: (R-pipeline-001 single-assignment / read-write disjointness).
    SINGLE_ASSIGNMENT = "single-assignment"
    #: A channel has two or more **contributors** — its seed (iff a declared ``[inputs]``
    #: channel) plus its node writes, in graph order — with no ``merge.<channel>``
    #: declaration (R-pipeline-002; pipeline/conformance.md § Channel-write overlap
    #: without ``merge`` declaration — the contributor model's undeclared-fan-in check).
    CHANNEL_WRITE_OVERLAP = "channel-write-overlap"
    #: A merge strategy's type constraint does not match the merged channel's induced
    #: type (R-pipeline-002 compose-time validation).
    MERGE_STRATEGY_TYPE = "merge-strategy-type"
    #: A declared binding is unsupplied, or a supplied binding/identity matches no
    #: declaration (orphan), or an identity field is missing (§ Binding supply incomplete).
    BINDING_SUPPLY = "binding-supply-incomplete"
    #: A service handler / trainable composition node does not declare exactly one
    #: service-typed binding (R-handler-008 cardinality; the trainable-backend property is
    #: Phase 2). Hooks: ≤ 1 (R-handler-009).
    SERVICE_BINDING_CARDINALITY = "service-binding-cardinality"
    #: A field in a pipeline ``service_bindings.<name>`` block is not in the service-type's
    #: ``identity_schema`` (or a deployment ``transport.<name>`` field is not in
    #: ``transport_schema``) (§ Identity/transport field misplacement).
    IDENTITY_TRANSPORT_PLACEMENT = "identity-transport-placement"
    #: A service-typed binding has no covering deployment ``transport.<name>`` block, or
    #: the block omits a declared ``transport_schema`` field (§ Service-binding transport
    #: coverage gap).
    TRANSPORT_COVERAGE = "transport-coverage-gap"
    #: A hook node has no covering ``hook_transport."<qn>"`` block, or the block omits a
    #: declared field (§ Hook transport coverage gap).
    HOOK_TRANSPORT_COVERAGE = "hook-transport-coverage-gap"
    #: An ``[inputs]`` field no node reads, or an ``[outputs]`` field no node writes
    #: (§ ``inputs`` / ``outputs`` dead declaration).
    INPUTS_OUTPUTS_DEAD = "inputs-outputs-dead-declaration"
    #: A config value supply violates the ``[config_schema]`` supply rule — identical at
    #: both supply sites (a trainable composition's ``[trainable.config]``; any other
    #: service-typed binding's pipeline/composition ``service_bindings.<name>`` ``config``
    #: block), in either direction: a supplied key is not a declared ``[config_schema]``
    #: field of the bound service-type, or a declared field is neither supplied nor
    #: covered by a declared ship-time default (R-service-type-002 compose-side;
    #: service-type/reference.md § The ``[config_schema]`` contract).
    CONFIG_SCHEMA_SUPPLY = "config-schema-supply"
    #: A ``streamable = true`` trainable composition node is followed by a non-hook node
    #: (R-pipeline-001 streamable terminal-node placement).
    STREAMABLE_TERMINAL = "streamable-terminal-node"
    #: A ``streamable = true`` trainable composition is bound to a backend whose adapter
    #: exposes no ``invoke_streaming`` generator — the declaration promises token-level
    #: delivery the binding cannot honor (the streaming-capability half of the
    #: trainable-backend gate, R-handler-008 expansion; a silent buffered fallback is
    #: the graceful degrade the engine forbids).
    STREAMABLE_BACKEND_SUPPORT = "streamable-backend-support"
    #: A ``stream_sink`` was attached at ``run(...)`` against a runnable whose terminal
    #: node (modulo trailing hooks, transitively through a terminal nested ``pipeline``
    #: embed) is not a ``streamable`` trainable — the sink would silently never fire
    #: (R-pipeline-001; the run-boundary half of the streamable delivery contract).
    STREAMABLE_SINK_TARGET = "streamable-sink-target"
    #: A deployment ``pipelines.<name>`` override names a binding/hook the pipeline does
    #: not declare (R-deployment-002).
    DEPLOYMENT_OVERRIDE_TARGET = "deployment-override-target"
    #: A name the engine requires unique within a namespace is duplicated: two composition
    #: nodes resolve to the same ``meta.name`` within one pipeline (collides in the
    #: trained-artifact-manifest key + in ``<meta.name>.<channel>`` channel scoping —
    #: ``hash-model.md`` § Manifest-key shape: "unique within the embedding pipeline's
    #: namespace"), or two ``[[preprocessors]]`` share a name within one composition (their
    #: ``<meta.name>.<name>`` qualified names would collide; ``composition.py`` IR — preprocessor
    #: name "unique in this composition").
    NAME_UNIQUENESS = "name-uniqueness"

    # -- Phase 2: handler / adapter resolution seals (handler-resolution.md steps 3-7) --
    #: A handler/adapter name's module is not importable, or the module does not export
    #: the named attribute (§ Error semantics: module-not-found / function-not-in-module;
    #: for a dot-less short name, no ``conjured.handlers`` /
    #: ``conjured.service_implementations`` entry point is registered under it).
    HANDLER_MODULE_IMPORT = "handler-module-import"
    #: ``find_spec`` reports a namespace package (PEP 420, ``origin is None``) — rejected
    #: at step 2, before the step-3 source read it would make impossible
    #: (§ Namespace packages).
    HANDLER_NAMESPACE_PACKAGE = "handler-namespace-package"
    #: The cached ``sys.modules`` entry about to execute was loaded from a DIFFERENT
    #: file than the one step 3 just read, audited, and hashed — two files claiming one
    #: module name in one process (a shadowed package, a stale install beside local
    #: source). Executing the cached module would silently break audited-source-IS-
    #: executed-source coherence, so the compose rejects
    #: (handler-resolution.md § Hot-reload semantics).
    MODULE_ORIGIN_DIVERGENCE = "module-origin-divergence"
    #: The step-3 source-AST audit (R-handler-pure-module + the import-time-I/O scan, run
    #: BEFORE import) found module-level mutable state, a persistent caching decorator at
    #: module scope, or module-level I/O.
    HANDLER_PURE_MODULE = "handler-pure-module"
    #: The resolved object fails the vector-2 ``inspect.isfunction`` seal
    #: (R-handler-bare-function predicate admit/reject conformance set).
    HANDLER_FUNCTION_SHAPE = "handler-function-shape"
    #: The step-6 signature introspection found a mismatch against the declared union
    #: (reads ports ∪ ``bindings.<name>`` ∪ ``services`` iff service-typed binding) —
    #: extra/missing kwarg, positional parameter, ``*args`` / ``**kwargs`` collector
    #: (R-handler-001 signature-union; read from the real ``__code__``).
    HANDLER_SIGNATURE = "handler-signature-mismatch"
    #: Two or more installed distributions register the same entry-point short name —
    #: the engine fails loud, never picks a winner (§ Entry-points collision).
    ENTRY_POINT_COLLISION = "entry-point-collision"
    #: The adapter-module source-AST audit (vector 7: above-instance-scope mutable state;
    #: R-handler-pure-module adapter-scope extension) found a violation, or the resolved
    #: object is not a class.
    ADAPTER_PURE_MODULE = "adapter-pure-module"
    #: The adapter ``invoke()`` signature fails the R-service-type-002/003 contract
    #: (closed dispatch-kwargs + exactly the ``[config_schema]`` kwargs +
    #: ``**transport_extra``; keyword-only).
    ADAPTER_SIGNATURE = "adapter-signature-mismatch"
    #: Under the deployment's ``audit_enforcement`` opt-in, a resolved in-scope module
    #: (handler / adapter / validator) carries a **not-fresh** sibling audit stamp — one of
    #: the three not-fresh states: **stale** (the source changed since the stamp), **absent**
    #: (no ``<module>.audit.toml`` exists), or **failed** (the hashes match but the recorded
    #: verdict is not a pass-grade). The dated-audit complement of R-handler-pure-module — the
    #: review-enforced family the mechanical AST walk cannot check (handler/reference.md
    #: § Audit stamps; ``validator.audit_stamp``). Without the opt-in the stamp is never read
    #: and carries no compose-time consequence.
    AUDIT_STAMP_NOT_FRESH = "audit-stamp-not-fresh"
    #: Under ``audit_enforcement``, a resolved module's sibling ``<module>.audit.toml`` exists
    #: but is **malformed** — unreadable, not valid TOML, missing a closed field, or carrying
    #: a mistyped / out-of-enum field. Fail loud on a corrupt engine-read artifact; distinct
    #: from **absent** (the normal not-yet-audited state, ``AUDIT_STAMP_NOT_FRESH``), never
    #: coerced to it (handler/reference.md § Audit stamps; ``validator.audit_stamp``).
    AUDIT_STAMP_MALFORMED = "audit-stamp-malformed"
    #: Adapter construction failed at compose — the B2 one-instance-per-composition
    #: construction (``__init__`` receiving only the compose-fixed identity kwargs, plus
    #: the two engine-supplied trainable kwargs) raised: a ``TypeError`` rejecting the
    #: compose-supplied identity kwargs, or any consumer-adapter constructor raise. The
    #: closed compose-time error channel covers construction (resolve_adapter's seal:
    #: nothing there can fail at runtime), so the raw exception wraps here — a
    #: constructor-raised ``ContractViolation`` (e.g. the trainable constraint-derivation
    #: rejection) passes through unwrapped.
    ADAPTER_CONSTRUCTION = "adapter-construction"
    #: An engine-owned native identity (a ``conjured.lib.*`` service-type qualified name)
    #: was represented outside its canonical form — either a binding whose ``type`` is a
    #: native adapter **class path** (resolution, ``validator.resolve_adapter``: the
    #: dual-identity hazard, one backend under two hash identities) or a
    #: ``DeclarationRegistry.add_service_type`` registration under a ``conjured.lib.*`` name
    #: that is **not** the engine-shipped declaration for that native (redefining an
    #: engine-owned identity). Hand-loading the genuine shipped declaration stays legal; both
    #: illegitimate forms fail loud (native-library/reference.md § the engine-owned-identity
    #: clause; R-service-type-004 — one implementation per service-type qualified name).
    ENGINE_OWNED_IDENTITY = "engine-owned-identity"
    #: A trainable composition node's resolved adapter fails the trainable-backend
    #: **property contract** the compose-time gate verifies on the resolved class: the
    #: ``training_artifact_contract`` provenance label is absent, empty, or not a string
    #: (an opaque label the engine records but never interprets — any non-empty string is
    #: accepted), or ``reserved_wire_keys`` is absent or not a ``frozenset[str]``
    #: (handler/reference.md § Trainable backends — the compose-time gate; R-handler-008
    #: expansion). Certification is **structural** — native-by-construction via the
    #: native adapter table, or a fresh pass-grade audit stamp under
    #: ``audit_enforcement`` (the sibling ``.audit.toml``; ``validator.audit_stamp``), never
    #: a self-declared attribute.
    TRAINABLE_BACKEND_CERTIFICATION = "trainable-backend-certification"
    #: A trainable's declared ``trainable.output_schema`` reaches for constraints the
    #: bound backend's grammar / structured-output wire form cannot enforce token-by-token
    #: (any field ``validators`` entry, anywhere in the schema; a ``bytes`` or a
    #: fixed-arity ``tuple`` channel on EVERY JSON wire — the shared renderer; an
    #: open-keyed ``dict`` under the OpenAI strict wire form only; a field name outside
    #: the GBNF wire form's ASCII rule-name charset) — it cannot form a literal-equal
    #: seal and is rejected at compose: an honest failure, not a silent best-effort
    #: (handler/reference.md § Trainable backends — the compose-time caveat;
    #: R-handler-005).
    TRAINABLE_CONSTRAINT_UNSUPPORTED = "trainable-constraint-unsupported"
    #: A resolved field validator's signature fails the R-handler-012 contract: kwarg-only
    #: with parameters exactly the reserved ``value`` plus the entry's declared parameter
    #: names — extra, missing, positional, ``*args`` / ``**kwargs`` collectors all reject
    #: (handler/reference.md § Validators; the same signature-union discipline as
    #: R-handler-001, read from the real ``__code__``).
    VALIDATOR_SIGNATURE = "validator-signature-mismatch"
    #: A constraint-layer declaration violates the R-handler-012 binding contract at
    #: compose: a parameter named ``value`` (the reserved kwarg), a non-data parameter
    #: value (parameters are data only — scalar/collection, never a callable/expression),
    #: a built-in constraint key's malformed value (a non-numeric or non-finite bound, a
    #: non-compiling ``pattern``, an empty ``enum`` list), or a built-in keyword declared
    #: on a type outside its JSON-Schema applicability family (numeric keywords → numeric
    #: types; ``minLength``/``maxLength``/``pattern`` → strings; ``enum`` → any) — the
    #: fail-loud deviation from the standard's silent ignore (handler/conformance.md
    #: § Validator resolution and parameter binding).
    VALIDATOR_PARAMS = "validator-parameter-binding"

    # -- Phase 2: dispatch boundary (R-handler-001 output-validation routing; G14a) -----
    #: The return dict carries a key absent from ``output_schema`` — a top-level key-set
    #: fact → ContractViolation (R-handler-001/output-validation).
    UNDECLARED_OUTPUT_KEY = "undeclared-output-key"
    #: A declared output port is omitted from the return dict — the same top-level
    #: key-set class (R-handler-001/output-validation; error-channel "missing declared
    #: write").
    MISSING_DECLARED_WRITE = "missing-declared-write"
    #: A non-hook handler returned something other than a dict keyed by output-port name
    #: — the key-set cannot even be read; structurally wrong return shape
    #: (R-handler-001: the return dict is the sole admission gate).
    RETURN_SHAPE = "return-shape"
    #: A hook returned non-``None`` (hooks return ``None`` by contract; the runner has no
    #: merge path for a hook return — handler/reference.md ``output_schema``
    #: §-discipline).
    HOOK_RETURN_NOT_NONE = "hook-return-not-none"
    #: The pre-call reads validation failed — the SVE input boundary. The decided
    #: ``audit_code`` is :data:`INPUT_VALIDATION_AUDIT_CODE`; this member is the symbolic
    #: stand-in consistent with the rest of the enum.
    HALT_ON_INPUT_VALIDATION_ERROR = "halt-on-input-validation-error"
    #: The post-call output validation failed within declared ports — the SVE output
    #: boundary (:data:`OUTPUT_VALIDATION_AUDIT_CODE`).
    HALT_ON_SCHEMA_VALIDATION_ERROR = "halt-on-schema-validation-error"

    # -- Phase 1b: hash machinery -----------------------------------------------------
    #: An **external-file** declaration (the ``{ file = "<path>" }`` form) could not be made
    #: hashable. Two surfaces share this class: a pipeline-entry / preprocessor **binding value**
    #: (read + canonicalized by ``validator.resolve``) and a ``compile`` directive's file-supplied
    #: **parameter** (read as raw text by ``validator.resolve.resolve_compile_param_files``), whose
    #: content the pipeline-hash / training-bundle-hash folds (``hash-model.md`` § What the
    #: pipeline-hash absorbs). The resolution passes do the I/O at compose; this fires when a
    #: referenced file is **unreadable / un-decodable** at that pass, or when an external-file
    #: declaration reaches the hasher / compiler **unresolved** (the resolution pass was not run) —
    #: a structural backstop so the engine **never silently hashes a path or feeds a path to a
    #: compiler** (mirrors ``BUNDLE_REACHES_BYREF_FOLD``; fail loud). Not a Phase-1a *validator* check —
    #: exercised by the hasher / resolution suites, carved out of the validator coverage guarantee.
    EXTERNAL_BINDING_UNSUPPORTED = "external-binding-content-unsupported"

    # -- Phase 3: the runner (API boundary, wrap boundary, assembly deferrals) ---------
    #: A declared ``[inputs]`` field is absent from the incoming request's initial channel
    #: values — ContractViolation at the API boundary; no node dispatches; the run never
    #: starts (R-pipeline-001 ``api-inputs-enforcement``; pipeline/conformance.md
    #: § API-invocation declared-inputs enforcement). The message names any unrecognized
    #: keys present (an extra alone is **not** an error — never seeded, inert).
    API_INPUTS_ENFORCEMENT = "api-invocation-declared-inputs-enforcement"
    #: The runner's dispatch-boundary wrap guarantee (R-error-channel-001): any uncaught
    #: exception that is not already ContractViolation / SchemaValidationError wraps into
    #: PipelineFailure before surfacing — no fourth class escapes. Registered so the
    #: generated error index carries the wrap audit; the ``PipelineFailure`` constructor
    #: carries NO audit/check/rule argument (the class maps to this single catalog entry
    #: rather than per-violation codes — error-channel/reference.md § PipelineFailure
    #: payload).
    PIPELINE_FAILURE_WRAP = "pipeline-failure-wrap"
    #: A ``compile = "<compiler>"`` directive's declared parameters do not bind the resolved
    #: compiler's signature (a declared parameter the compiler does not accept, a required
    #: compiler parameter not declared, or a non-kwarg-only compiler) — introspected at the
    #: stage-4 binding-resolution pass from the real ``__code__`` (``validator.resolve_compile``;
    #: handler/reference.md § The ``compile = "..."`` directive sub-form). Compose-time, never
    #: at dispatch.
    COMPILE_SIGNATURE = "compile-signature"
    #: A resolved ``compile`` compiler **raised** when run against its bound parameters — its own
    #: failure producing the artifact (a malformed ``regex``, an unparseable ``jinja`` template,
    #: an invalid ``json_schema``, an unknown parameter value). Raised at binding resolution
    #: (compose time), never at dispatch (handler/reference.md § The ``compile`` directive sub-form).
    COMPILE_ARTIFACT = "compile-artifact"
    #: A compose-resolved ``bindings.<name>`` value violates its declared binding schema —
    #: a value-level type/constraint failure (or a scalar supplied for a multi-field
    #: binding). Stage-4 assembly validates each resolved binding value against a model
    #: generated over the binding's ``SchemaBinding.fields`` (handler/reference.md
    #: § Binding value-supply grammar: "both go through the same Pydantic validator"), so a
    #: constraint on a binding field enforces. Compose-fixed values validate once at
    #: assemble — a ContractViolation, not the dispatch-only SchemaValidationError.
    BINDING_VALUE_SHAPE = "binding-value-shape"
    #: The reserved explicit-null value form ``{ null = true }`` supplied where the target
    #: field is not nullable-declared — identity / config / compile-param positions always
    #: reject (no nullable axis exists there), a whole multi-field binding is never a
    #: nullable target, and a non-nullable binding / transport / hook-transport field
    #: rejects the form. Compose-time (handler/reference.md § Binding value-supply grammar,
    #: the ``explicit-null`` region: admitted ONLY where the target field is
    #: nullable-declared). A *malformed* spelling (``{ null = false }``, a non-boolean, an
    #: extra key) is ``MALFORMED_DECLARATION`` at parse — the same split the ``{ file }``
    #: sibling uses.
    EXPLICIT_NULL_TARGET = "explicit-null-target"
    #: Two service-typed bindings sharing one as-written handle within a composing
    #: pipeline's scope (the pipeline's own ``service_bindings.<name>`` and/or an embedded
    #: trainable composition's ``[service_bindings.<name>]``) resolve DIFFERENT
    #: service-types — the shared ``transport.<name>`` block cannot satisfy two
    #: ``transport_schema``s (pipeline/reference.md ``R-pipeline-001/transport-coverage``:
    #: the join is type-coherent). Compose-time.
    TRANSPORT_HANDLE_COHERENCE = "transport-handle-coherence"
    # -- R-pipeline-003: trained-artifact integrity enforcement (deployment load) -------
    #: A registered artifact's sidecar trained-artifact manifest is absent under
    #: ``integrity_enforcement = true`` — no comparison is possible and the deployment
    #: opted into the guarantee (pipeline/conformance.md § Missing manifest sidecar under
    #: integrity enforcement; hash-model § Enforcement on). With enforcement off, absence
    #: is the no-baseline case: no comparison, no event, no error.
    TRAINED_ARTIFACT_MANIFEST_MISSING = "trained-artifact-manifest-missing"
    #: A registered artifact's sidecar exists but is unreadable, not valid UTF-8 TOML,
    #: missing a required manifest field, or carrying a mistyped / out-of-enum field —
    #: malformed, raised under EITHER enforcement mode (the read is not enforcement-gated;
    #: the always-available drift events need the recorded values), never coerced to
    #: absent (pipeline/conformance.md § Malformed trained-artifact manifest sidecar; the
    #: audit-stamp artifact's posture).
    TRAINED_ARTIFACT_MANIFEST_MALFORMED = "trained-artifact-manifest-malformed"
    #: A trainable composition node's current training-bundle-hash differs from the
    #: loaded manifest's recorded value under ``integrity_enforcement = true``, with no
    #: ``acknowledged_drift`` entry covering the artifact + trainable — the HIGH-force
    #: halt (pipeline/conformance.md § Training-bundle-hash mismatch at deployment load;
    #: hash-model § Enforcement on owns the graduated force). Under enforcement off the
    #: mismatch fires ``training_bundle_hash_changed`` and load proceeds — no raise.
    TRAINING_BUNDLE_HASH_MISMATCH = "training-bundle-hash-mismatch"
    #: A deployment ``[artifacts]`` key names a trainable composition that matches no
    #: trainable node in the deployed pipeline — a registration that can never be
    #: compared (a renamed composition, a typo, a stale entry), refused rather than
    #: silently skipped (pipeline/conformance.md § Artifact registration names an
    #: unknown trainable). Either enforcement mode.
    ARTIFACT_TRAINABLE_UNKNOWN = "artifact-trainable-unknown"

    #: A ``secret_ref``-declared transport field's supplied value is not a well-formed
    #: ``[scheme]payload`` secret reference (deployment/reference.md § Secret references —
    #: the reference grammar). A raw credential pasted where a reference belongs lands here
    #: loud at pipeline-declaration load, never forwarded. Compose-time.
    SECRET_REF_MALFORMED = "secret-ref-malformed"
    #: A well-formed secret reference names a scheme that is neither a built-in
    #: (``env`` / ``file``) nor a namespaced (dotted) consumer resolver's qualified name
    #: (§ Secret references — the scheme set). Compose-time.
    SECRET_REF_SCHEME_UNKNOWN = "secret-ref-scheme-unknown"
    #: A secret reference's namespaced (dotted) scheme names a consumer resolver that does
    #: not import to a callable at pipeline-declaration load (§ Secret references — the
    #: consumer-resolver arm; the fetch itself is dispatch-time, this is the shape check).
    SECRET_RESOLVER_INVALID = "secret-resolver-invalid"


# ---------------------------------------------------------------------------
# The registration API — the registered error set (the STUB-R4 ruling, 2026-06-10)
# ---------------------------------------------------------------------------

#: The two decided SVE audit codes — SVE raises at exactly two boundaries, each with its
#: own catalog code (error-channel/reference.md § SchemaValidationError payload). Unlike
#: ``ContractViolation`` (whose catalog is deferred), these strings are canon.
INPUT_VALIDATION_AUDIT_CODE = "C1.HALT_ON_INPUT_VALIDATION_ERROR.001"
OUTPUT_VALIDATION_AUDIT_CODE = "C1.HALT_ON_SCHEMA_VALIDATION_ERROR.001"

#: The single decided PipelineFailure catalog entry — every PF maps to the same wrap
#: audit rather than a per-violation code (error-channel/reference.md § PipelineFailure
#: payload: "carries no audit_code … Every PipelineFailure maps to the same wrap audit").
#: Registered for the generated index; never carried on the class.
PIPELINE_FAILURE_WRAP_AUDIT_CODE = "C1.PIPELINE_FAILURE_WRAP.001"


@dataclasses.dataclass(frozen=True, slots=True)
class CheckRecord:
    """One :class:`Check` member's registered metadata — the unit of the registered
    error set the error-index codegen reads (``tools/gen_error_index.py``).

    - ``rule_ids`` — the canonical derived rule(s) this check enforces; the closed set
      of ``rule_id`` values a raise site may pass with this check. Registering a new
      pair is an engine change, exactly as adding a ``Check`` member is.
    - ``error_class`` — the error-channel class that raises this check (by name; the
      classes are defined below the registry).
    - ``audit_code`` — the decided catalog code, where canon has assigned one (three codes
      today: the two SVE boundary codes + the PipelineFailure-wrap audit); ``None`` until
      the deferred catalog lands.
    """

    rule_ids: tuple[str, ...]
    error_class: str
    audit_code: str | None = None


#: The registered error set: every :class:`Check` member maps to its enforced rule_ids
#: + raising error class. The constructors below enforce membership — an unregistered
#: ``(check, rule_id)`` pair fails loud at construction, and the per-check negative-test
#: coverage guard (every member has a firing test) keeps every registered row real. The
#: error-index codegen reads this mapping; the generated index is therefore complete by
#: construction (structural-over-disciplinary).
CHECK_REGISTRY: dict[Check, CheckRecord] = {
    # -- Stage 1: declaration parse / load ------------------------------------------
    # R-handler-006 joins R-handler-003 per the conformance row's dual anchor (the kind
    # header is both the bare-function kind rule and a closed-shape-grammar clause).
    Check.HANDLER_KIND_HEADER: CheckRecord(
        ("R-handler-003", "R-handler-006"), "ContractViolation"
    ),
    Check.CLOSED_GRAMMAR: CheckRecord(
        (
            "R-deployment-001",
            # R-deployment-002: the closed [pipelines.<name>] override-block grammar —
            # only transport / hook_transport accept per-pipeline override; a stray or
            # canon-forbidden override key raises rather than silently no-opping.
            "R-deployment-002",
            "R-handler-004",
            "R-handler-006",
            "R-handler-010",
            "R-pipeline-001",
            "R-pipeline-002",
            "R-service-type-001",
        ),
        "ContractViolation",
    ),
    Check.SECTION_PRESENCE: CheckRecord(
        ("R-deployment-001", "R-handler-006", "R-service-type-001"), "ContractViolation"
    ),
    Check.BODY_REQUIRED: CheckRecord(
        ("R-deployment-001", "R-handler-006", "R-pipeline-001", "R-service-type-001"),
        "ContractViolation",
    ),
    Check.CHANNEL_TYPE_TOKEN: CheckRecord(
        # The same owning-rule fidelity PARSE-F3 threaded through the sibling stage-1
        # diagnostics: a malformed channel-field type token cites the owning rule of the
        # declaration class whose schema section it sits in — R-handler-006 for handler /
        # trainable-composition sections (the default), R-pipeline-001 for a pipeline
        # [inputs]/[outputs] field, R-service-type-001 for a service-type
        # [identity_schema]/[transport_schema]/[config_schema] field. The token grammar
        # is owned by handler/reference.md § Types allowed, but the DIAGNOSTIC routes the
        # author to the section's own rule.
        ("R-handler-006", "R-pipeline-001", "R-service-type-001"), "ContractViolation"
    ),
    Check.NULLABLE_PLACEMENT: CheckRecord(("R-service-type-001",), "ContractViolation"),
    Check.UNKNOWN_COMPOSITION_KIND: CheckRecord(("R-handler-006",), "ContractViolation"),
    Check.MALFORMED_DECLARATION: CheckRecord(
        (
            "R-deployment-001",
            "R-deployment-002",
            "R-handler-006",
            "R-handler-010",
            "R-handler-011",
            "R-pipeline-001",
            "R-pipeline-002",
            "R-service-type-001",
        ),
        "ContractViolation",
    ),
    # -- Stage 2: compose-time validation + graph compilation -----------------------
    Check.HANDLER_NAME_RESOLUTION: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.SERVICE_TYPE_RESOLUTION: CheckRecord(
        ("R-pipeline-001", "R-service-type-004"), "ContractViolation"
    ),
    # BUNDLE_REACHES_BYREF_FOLD: R-pipeline-001 only — the hasher backstop is its sole
    # raise site since the bundle embed-form landed (the stage-1 parse rejection retired).
    Check.BUNDLE_REACHES_BYREF_FOLD: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.COMPOSITION_CYCLE: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.READ_WRITE_SHAPE: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.WIRING_MAP_PORT: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.DANGLING_IDENTITY_PORT: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.READ_PORT_UNCLOSED: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.SINGLE_ASSIGNMENT: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.CHANNEL_WRITE_OVERLAP: CheckRecord(("R-pipeline-002",), "ContractViolation"),
    Check.MERGE_STRATEGY_TYPE: CheckRecord(("R-pipeline-002",), "ContractViolation"),
    Check.BINDING_SUPPLY: CheckRecord(
        ("R-handler-006", "R-pipeline-001"), "ContractViolation"
    ),
    Check.SERVICE_BINDING_CARDINALITY: CheckRecord(
        ("R-handler-008", "R-handler-009"), "ContractViolation"
    ),
    Check.IDENTITY_TRANSPORT_PLACEMENT: CheckRecord(
        ("R-pipeline-001",), "ContractViolation"
    ),
    Check.TRANSPORT_COVERAGE: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.HOOK_TRANSPORT_COVERAGE: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.INPUTS_OUTPUTS_DEAD: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.CONFIG_SCHEMA_SUPPLY: CheckRecord(
        ("R-service-type-002",), "ContractViolation"
    ),
    Check.STREAMABLE_TERMINAL: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.STREAMABLE_BACKEND_SUPPORT: CheckRecord(("R-handler-008",), "ContractViolation"),
    Check.STREAMABLE_SINK_TARGET: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.DEPLOYMENT_OVERRIDE_TARGET: CheckRecord(
        ("R-deployment-002",), "ContractViolation"
    ),
    Check.NAME_UNIQUENESS: CheckRecord(
        ("R-handler-006", "R-pipeline-001", "R-service-type-001"), "ContractViolation"
    ),
    # -- Phase 2: handler / adapter / validator resolution seals ----------------------
    Check.HANDLER_MODULE_IMPORT: CheckRecord(
        ("R-handler-012", "R-pipeline-001", "R-service-type-003"), "ContractViolation"
    ),
    Check.HANDLER_NAMESPACE_PACKAGE: CheckRecord(
        # R-service-type-003: the adapter-resolution path shares HANDLER_NAMESPACE_PACKAGE
        # for a PEP-420 namespace-package module, citing the service-type rule (mirrors the
        # sibling HANDLER_MODULE_IMPORT) — without this pair a namespace-package adapter
        # would escape as a raw ValueError, a fourth class out of the closed channel.
        ("R-handler-012", "R-pipeline-001", "R-service-type-003"), "ContractViolation"
    ),
    Check.MODULE_ORIGIN_DIVERGENCE: CheckRecord(
        # Shared by the same resolution paths as HANDLER_MODULE_IMPORT (handler / adapter /
        # validator / compiler all import through the one audited-import seam), citing the
        # caller's rule — the same trio.
        ("R-handler-012", "R-pipeline-001", "R-service-type-003"), "ContractViolation"
    ),
    Check.HANDLER_PURE_MODULE: CheckRecord(
        ("R-handler-pure-module",), "ContractViolation"
    ),
    Check.HANDLER_FUNCTION_SHAPE: CheckRecord(
        ("R-handler-bare-function",), "ContractViolation"
    ),
    Check.HANDLER_SIGNATURE: CheckRecord(("R-handler-001",), "ContractViolation"),
    Check.ENTRY_POINT_COLLISION: CheckRecord(
        ("R-handler-012", "R-pipeline-001", "R-service-type-004"), "ContractViolation"
    ),
    Check.ADAPTER_PURE_MODULE: CheckRecord(
        ("R-handler-pure-module", "R-service-type-003"), "ContractViolation"
    ),
    Check.ADAPTER_SIGNATURE: CheckRecord(
        ("R-service-type-002", "R-service-type-003"), "ContractViolation"
    ),
    Check.AUDIT_STAMP_NOT_FRESH: CheckRecord(
        ("R-handler-pure-module",), "ContractViolation"
    ),
    Check.AUDIT_STAMP_MALFORMED: CheckRecord(
        ("R-handler-pure-module",), "ContractViolation"
    ),
    Check.ADAPTER_CONSTRUCTION: CheckRecord(
        ("R-service-type-003",), "ContractViolation"
    ),
    Check.ENGINE_OWNED_IDENTITY: CheckRecord(
        ("R-service-type-004",), "ContractViolation"
    ),
    Check.TRAINABLE_BACKEND_CERTIFICATION: CheckRecord(
        ("R-handler-008",), "ContractViolation"
    ),
    Check.TRAINABLE_CONSTRAINT_UNSUPPORTED: CheckRecord(
        ("R-handler-005",), "ContractViolation"
    ),
    Check.VALIDATOR_SIGNATURE: CheckRecord(("R-handler-012",), "ContractViolation"),
    Check.VALIDATOR_PARAMS: CheckRecord(("R-handler-012",), "ContractViolation"),
    # -- Phase 2: dispatch boundary ---------------------------------------------------
    Check.UNDECLARED_OUTPUT_KEY: CheckRecord(("R-handler-001",), "ContractViolation"),
    Check.MISSING_DECLARED_WRITE: CheckRecord(("R-handler-001",), "ContractViolation"),
    Check.RETURN_SHAPE: CheckRecord(("R-handler-001",), "ContractViolation"),
    # R-error-channel-003 joins R-handler-001 per the error-channel conformance row's
    # anchor (the hook-return contract is both the output-admission rule and a
    # halt-semantics clause — the runner has no merge path for a hook return).
    Check.HOOK_RETURN_NOT_NONE: CheckRecord(
        ("R-error-channel-003", "R-handler-001"), "ContractViolation"
    ),
    Check.HALT_ON_INPUT_VALIDATION_ERROR: CheckRecord(
        ("R-error-channel-003",), "SchemaValidationError", INPUT_VALIDATION_AUDIT_CODE
    ),
    Check.HALT_ON_SCHEMA_VALIDATION_ERROR: CheckRecord(
        ("R-error-channel-003",), "SchemaValidationError", OUTPUT_VALIDATION_AUDIT_CODE
    ),
    # -- Phase 1b: hash machinery -----------------------------------------------------
    Check.EXTERNAL_BINDING_UNSUPPORTED: CheckRecord(
        ("R-pipeline-001",), "ContractViolation"
    ),
    # -- Phase 3: the runner ------------------------------------------------------------
    Check.API_INPUTS_ENFORCEMENT: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.PIPELINE_FAILURE_WRAP: CheckRecord(
        ("R-error-channel-001",), "PipelineFailure", PIPELINE_FAILURE_WRAP_AUDIT_CODE
    ),
    Check.COMPILE_SIGNATURE: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.COMPILE_ARTIFACT: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.BINDING_VALUE_SHAPE: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.EXPLICIT_NULL_TARGET: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    Check.TRANSPORT_HANDLE_COHERENCE: CheckRecord(("R-pipeline-001",), "ContractViolation"),
    # -- R-pipeline-003: trained-artifact integrity enforcement -------------------------
    Check.TRAINED_ARTIFACT_MANIFEST_MISSING: CheckRecord(("R-pipeline-003",), "ContractViolation"),
    Check.TRAINED_ARTIFACT_MANIFEST_MALFORMED: CheckRecord(("R-pipeline-003",), "ContractViolation"),
    Check.TRAINING_BUNDLE_HASH_MISMATCH: CheckRecord(("R-pipeline-003",), "ContractViolation"),
    Check.ARTIFACT_TRAINABLE_UNKNOWN: CheckRecord(("R-pipeline-003",), "ContractViolation"),
    # -- Secret references (deployment/reference.md § Secret references) ----------------
    Check.SECRET_REF_MALFORMED: CheckRecord(("R-deployment-003",), "ContractViolation"),
    Check.SECRET_REF_SCHEME_UNKNOWN: CheckRecord(("R-deployment-003",), "ContractViolation"),
    Check.SECRET_RESOLVER_INVALID: CheckRecord(("R-deployment-003",), "ContractViolation"),
}

#: The decided catalog codes, keyed back to their :class:`Check` member — the
#: audit-code half of the registered error set. Grows as the deferred catalog assigns
#: codes; the constructors reject any code not registered here.
AUDIT_CODE_REGISTRY: dict[str, Check] = {
    rec.audit_code: check for check, rec in CHECK_REGISTRY.items() if rec.audit_code
}


class ConjuredError(Exception):
    """Base for the engine's typed error-channel classes (R-error-channel-001).

    The closed enum's three members — :class:`ContractViolation` (structural),
    :class:`SchemaValidationError` (value-level), and :class:`PipelineFailure` (runtime) —
    all land under this one parent, so the closed failure surface has a single root the
    runtime + consumers can catch.
    """


class ContractViolation(ConjuredError):
    """Structural type-check failure — a declared interface is structurally wrong
    (error-channel/reference.md § The closed enum of error classes).

    Raised at declaration load and at pipeline compose time, before any handler
    dispatches. Carries the **minimal compose-time payload**:

    - ``check`` — the :class:`Check` discriminator naming which check fired (the
      pre-catalog stand-in for ``audit_code``; the test / consumer dispatch key).
    - ``rule_id`` — the canonical derived-rule identifier (``"R-pipeline-001"`` …).
    - ``expected`` / ``actual`` — one-line declarative contrast.
    - ``remediation_hint`` — short actionable guidance (Studio surfaces it for non-coders).
    - ``file_path`` / ``composition_ref`` — the location-bearing fields; **at least one
      MUST be non-null** (error-channel/reference.md § Location-bearing field requirement;
      both absent is itself a violation, ``C1.CONTRACT_VIOLATION_SHAPE.003``).
    - ``section_path`` / ``line_number`` — optional intra-artifact locators.
    - ``audit_code`` — optional; ``None`` until the deferred catalog assigns codes.
    - ``pipeline_run_id`` — optional correlation identifier; ``None`` for load-time and
      compose-time violations (no run in flight), present when raised mid-dispatch
      (error-channel/reference.md § ContractViolation payload).

    The RFC-9457 wire projection of this payload is the server boundary's
    (``conjured.server.problem_details``), never this class's.
    """

    def __init__(
        self,
        *,
        check: Check,
        rule_id: str,
        expected: str,
        actual: str,
        remediation_hint: str | None = None,
        file_path: str | None = None,
        composition_ref: str | None = None,
        section_path: str | None = None,
        line_number: int | None = None,
        audit_code: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> None:
        # guarantees: cv-requires-location
        if file_path is None and composition_ref is None:
            # The location-bearing-field requirement is itself a structural contract on the
            # payload — fail loud rather than emit a locationless diagnostic.
            raise ValueError(
                "ContractViolation requires at least one of file_path / composition_ref "
                "(error-channel/reference.md § Location-bearing field requirement)"
            )
        # The registration-API seals (the STUB-R4 ruling): a raise site cannot emit a
        # discriminator the generated error index does not carry. An unregistered pair is
        # an engine-construction bug — register it in CHECK_REGISTRY (an engine change,
        # exactly as adding a Check member is) before raising it.
        if not isinstance(check, Check):
            raise ValueError(
                f"check must be a Check member, got {check!r} — the registered error set "
                "is the closed Check enum (errors.CHECK_REGISTRY)"
            )
        record = CHECK_REGISTRY[check]
        if record.error_class != "ContractViolation":
            raise ValueError(
                f"check {check.value!r} is registered to {record.error_class}, not "
                "ContractViolation (errors.CHECK_REGISTRY)"
            )
        if rule_id not in record.rule_ids:
            raise ValueError(
                f"rule_id {rule_id!r} is not registered for check {check.value!r} "
                f"(registered: {list(record.rule_ids)}) — register the pair in "
                "errors.CHECK_REGISTRY before raising it"
            )
        # The check-consistency seal (mirrors SchemaValidationError's wrong-class seal,
        # tighter here): a non-None audit_code MUST be the catalog code registered for this
        # violation's own check (error-channel/reference.md § ContractViolation payload —
        # "this violation's code"). A code that is unregistered, belongs to another class, or
        # belongs to a different check would land the discriminator on the wrong error-index
        # rows — an engine-construction bug, fail loud.
        if audit_code is not None and audit_code != record.audit_code:
            raise ValueError(
                f"audit_code {audit_code!r} is not the catalog code registered for check "
                f"{check.value!r} (registered: {record.audit_code!r}) — a ContractViolation's "
                "audit_code must be its own check's code (errors.CHECK_REGISTRY), so the "
                "generated error index stays complete by construction"
            )
        self.check = check
        self.rule_id = rule_id
        self.expected = expected
        self.actual = actual
        self.remediation_hint = remediation_hint
        self.file_path = file_path
        self.composition_ref = composition_ref
        self.section_path = section_path
        self.line_number = line_number
        self.audit_code = audit_code
        self.pipeline_run_id = pipeline_run_id
        super().__init__(self._render())

    # guarantees: cv-rendered-message-cites-rule
    def _render(self) -> str:
        """The canonical default template (error-channel/reference.md § The rendered
        message cites its rule owns the citation contract; RFC-9457 ``detail`` is
        ``expected … ; actual …``)."""
        where = self.file_path or self.composition_ref
        if self.section_path:
            where = f"{where} [{self.section_path}]"
        if self.line_number is not None:
            where = f"{where}#L{self.line_number}"
        # The discriminator slot: the catalog audit_code once assigned, the symbolic
        # check value until then (the documented stand-in while audit_code is null) —
        # the cites-rule contract holds in both catalog eras with no template change.
        slot = self.audit_code if self.audit_code is not None else self.check.value
        msg = f"{self.rule_id} ({slot}) at {where}: expected {self.expected}; actual {self.actual}"
        if self.remediation_hint:
            msg = f"{msg} — {self.remediation_hint}"
        return msg

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ContractViolation(check={self.check.value!r}, rule_id={self.rule_id!r})"


class ContractViolationGroup(ConjuredError):
    """A compose-time container wrapping two or more :class:`ContractViolation`s
    aggregated from one stage-2 composition-validation group
    (``conjured/docs/components/error-channel/reference.md``
    § ContractViolationGroup — the compose-time multi-violation container).

    **NOT a fourth error class.** The closed enum stays
    ``ContractViolation`` / ``SchemaValidationError`` / ``PipelineFailure``; this is a
    *container* around class-1 ``ContractViolation``s, raised only at compose time and
    only when a single composition-validation group detects **≥ 2** independent
    violations. The within-a-group multi-error report is the compose-time analogue, at
    the **violation grain**, of ``SchemaValidationError.field_validations`` at the value
    grain (error-channel/reference.md § ContractViolationGroup payload).

    The single-violation case raises the **bare** ``ContractViolation`` (no wrapping) —
    a one-element container would carry nothing the bare violation does not, and the
    existing single-violation consumers stay unchanged; the across-group order stays
    fail-fast (pipeline/reference.md § Composition validation — the
    aggregate-within-a-group, fail-fast-across-groups policy). The constructor therefore
    **rejects** a group of fewer than two: a single violation is a construction bug here,
    fail loud.

    Carries the ``violations`` tuple (≥ 2, in detection order); each member is a full
    ``ContractViolation`` carrying its own complete payload. The group declares no locus
    of its own — the members share one composition-validation locus and each carries it.
    """

    def __init__(self, violations: "tuple[ContractViolation, ...]") -> None:
        violations = tuple(violations)
        if len(violations) < 2:
            # The ≥2 requirement is structural (error-channel: a group wraps "two or
            # more" violations; one violation raises the bare ContractViolation) — a
            # one-or-zero group is a construction bug, fail loud.
            raise ValueError(
                "ContractViolationGroup wraps two or more ContractViolations "
                f"(got {len(violations)}); a single violation raises the bare "
                "ContractViolation (error-channel/reference.md § ContractViolationGroup)"
            )
        if not all(isinstance(v, ContractViolation) for v in violations):
            # The container holds class-1 ContractViolations only — it is not a fourth
            # error class that could wrap an SVE / PipelineFailure.
            raise ValueError(
                "ContractViolationGroup wraps ContractViolation instances only — it is a "
                "container around class-1 violations, not a fourth error class "
                "(error-channel/reference.md § ContractViolationGroup)"
            )
        self.violations = violations
        super().__init__(self._render())

    def _render(self) -> str:
        """The auto-rendered ``message``: a count summary followed by each member's
        rendered message, so a log consumer reading only the string sees every
        aggregated failure (error-channel/reference.md § ContractViolationGroup payload)."""
        body = "; ".join(str(v) for v in self.violations)
        return f"{len(self.violations)} contract violations: {body}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            "ContractViolationGroup("
            f"{[v.check.value for v in self.violations]!r})"
        )


# ---------------------------------------------------------------------------
# SchemaValidationError — the dispatch-time value-level failure class (Phase 2)
# ---------------------------------------------------------------------------
# (The two decided SVE audit codes live with the registration API above, alongside
# the CHECK_REGISTRY records that key them.)


@dataclasses.dataclass(frozen=True, slots=True)
class FieldValidationDetail:
    """One field's validation failure (error-channel/reference.md § SchemaValidationError
    payload — the ``field_validations`` entry shape).

    - ``field_path`` — dot-notation path prefixed by the violated schema
      (``output_schema.…`` / ``reads.…``); array elements as ``[i]``.
    - ``expected_type`` — the declaration-canonical channel-field type (the form the
      handler declares — e.g. ``"list[float]"``), not the Pydantic class name.
    - ``actual_type`` — ``type(value).__name__`` of the offending value.
    - ``actual_value`` — ``repr()`` truncated to 256 chars with an elided-count marker;
      ``None`` (not ``"None"``) when the offending value is ``None``.
    - ``constraint_violated`` — open vocabulary; engine built-ins include ``"type"``,
      ``"required"`` (a required field absent WITHIN a declared port's nested value —
      the top-level key-set case is ``ContractViolation`` per R-handler-001's
      output-validation routing), ``"nullable"``, ``"enum"``, ``"keys_subset_of"``.
    - ``message`` — one-line human-readable description of this field's failure.
    """

    field_path: str
    expected_type: str
    actual_type: str
    actual_value: str | None
    constraint_violated: str
    message: str


class SchemaValidationError(ConjuredError):
    """Value-level type-check failure within declared fields — the declaration set is
    structurally intact; a value violates its declared type
    (error-channel/reference.md § The closed enum of error classes; § SchemaValidationError
    payload). Raised only mid-dispatch, at two boundaries: pre-call reads validation
    (audit :data:`INPUT_VALIDATION_AUDIT_CODE`) and post-call output validation
    (:data:`OUTPUT_VALIDATION_AUDIT_CODE`); both halt per R-error-channel-003.

    Carries the full decided required payload. ``field_validations`` is non-empty, one
    entry per failed field (single-field collapse forbidden), ordered by the violated
    schema's declaration order. No ``remediation_hint`` — the per-field entries and
    ``message`` carry the actionable detail directly.

    ``_render`` is the canonical default template, mirroring ``ContractViolation._render``'s
    shape (error-channel/reference.md § The rendered message cites its rule owns the
    citation contract — SVE cites both ``rule_id`` and ``audit_code``; its two boundary
    codes are canon-decided).
    """

    def __init__(
        self,
        *,
        audit_code: str,
        handler_qualified_name: str,
        handler_position: int,
        pipeline_run_id: str,
        schema_source: str,
        field_validations: tuple[FieldValidationDetail, ...],
        rule_id: str = "R-error-channel-003",
    ) -> None:
        if not field_validations:
            # The non-empty requirement is structural (error-channel: "non-empty array of
            # FieldValidationDetail entries") — an SVE with no failed field is a
            # construction bug, fail loud.
            raise ValueError(
                "SchemaValidationError requires a non-empty field_validations tuple "
                "(error-channel/reference.md § SchemaValidationError payload)"
            )
        # The registration-API seal (the STUB-R4 ruling): SVE's audit codes are canon-
        # decided and registered; an unregistered code (or a rule_id outside the code's
        # registered set) is a construction bug the index could not carry — fail loud.
        registered_check = AUDIT_CODE_REGISTRY.get(audit_code)
        if registered_check is None:
            raise ValueError(
                f"audit_code {audit_code!r} is not a registered catalog code "
                "(errors.AUDIT_CODE_REGISTRY) — the constructor rejects unregistered "
                "codes so the generated error index is complete by construction"
            )
        if CHECK_REGISTRY[registered_check].error_class != "SchemaValidationError":
            # The analogous seal ContractViolation carries (tighter there — its audit_code
            # must be its own check's registered code): a code registered to another class
            # (e.g. the PipelineFailure wrap audit) on an SVE would put the discriminator on
            # the wrong class's index rows.
            raise ValueError(
                f"audit_code {audit_code!r} is registered to "
                f"{CHECK_REGISTRY[registered_check].error_class}, not "
                "SchemaValidationError (errors.CHECK_REGISTRY)"
            )
        if rule_id not in CHECK_REGISTRY[registered_check].rule_ids:
            raise ValueError(
                f"rule_id {rule_id!r} is not registered for audit_code {audit_code!r} "
                f"(registered: {list(CHECK_REGISTRY[registered_check].rule_ids)})"
            )
        self.audit_code = audit_code
        self.rule_id = rule_id
        self.handler_qualified_name = handler_qualified_name
        self.handler_position = handler_position
        self.pipeline_run_id = pipeline_run_id
        self.schema_source = schema_source
        self.field_validations = field_validations
        super().__init__(self._render())

    # guarantees: sve-rendered-message-cites-rule-and-audit-code
    def _render(self) -> str:
        """The canonical default template (mirrors ``ContractViolation._render``'s
        shape; cites ``rule_id`` + ``audit_code`` inline per the citation contract)."""
        details = "; ".join(
            f"{d.field_path}: {d.message}" for d in self.field_validations
        )
        return (
            f"{self.rule_id} ({self.audit_code}) at "
            f"{self.handler_qualified_name}[{self.handler_position}] "
            f"(run {self.pipeline_run_id}, schema {self.schema_source}): "
            f"{len(self.field_validations)} field validation failure(s): {details}"
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"SchemaValidationError(audit_code={self.audit_code!r}, "
            f"handler={self.handler_qualified_name!r}@{self.handler_position}, "
            f"fields={[d.field_path for d in self.field_validations]!r})"
        )


# ---------------------------------------------------------------------------
# PipelineFailure — the runtime wrap class (Phase 3)
# ---------------------------------------------------------------------------


def format_composition_ref(pipeline_name: str, entry_ordinal: int) -> str:
    """The canon-pinned ``composition_ref`` form — pipeline name plus declaration-entry
    ordinal, ``"<pipeline_name>[<entry_ordinal>]"`` (error-channel/reference.md
    § PipelineFailure payload; the ordinal is the declaration-entry index, distinct
    from dispatch position). The single derivation point — compose-time
    ContractViolations and the runner's PipelineFailure/CV sites all format through
    here, so a format change is a one-line edit."""
    return f"{pipeline_name}[{entry_ordinal}]"


@overload
def snapshot_copy(value: Mapping[str, object]) -> dict[str, object]: ...
@overload
def snapshot_copy(value: object) -> object: ...
def snapshot_copy(value: object) -> object:
    """Materializing deep copy for the PF snapshots (the per-class payload's "deep copy
    is mandatory" — error-channel/reference.md § PipelineFailure payload), converting the
    engine's frozen delivery forms back to plain data: ``MappingProxyType`` (the
    reference-binding freeze) → ``dict`` — ``copy.deepcopy`` of a mappingproxy raises, so
    a plain structural walk is the deep copy here — ``frozenset`` → ``set``, ``tuple``
    kept a tuple of copies, ``dict``/``list``/``set`` rebuilt recursively.

    Leaves outside the container forms pass by reference: post-validation channel and
    binding data is closed-type plain data (the model generator's closed kind set), whose
    only mutable shapes are exactly the containers walked here; the one non-data leaf the
    delivery surface admits — an engine-owned compile artifact — is copy-exempt by the
    same vector-4 exemption that delivers it uncopied (trust-model § Vector 4)."""
    if isinstance(value, Mapping):  # dict and MappingProxyType alike
        return {k: snapshot_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [snapshot_copy(v) for v in value]
    if isinstance(value, tuple):
        return tuple(snapshot_copy(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return {snapshot_copy(v) for v in value}
    return value


#: The closed structural-locus enum a ``PipelineFailure.failure_category`` may take — *where* the
#: failure arose (error-channel/reference.md § PipelineFailure payload). Engine-produced and
#: engine-enforced (unlike the open, author-named ``cause_class`` passthrough), so it is a closed set.
_FAILURE_CATEGORIES = ("service", "handler", "engine")


class PipelineFailure(ConjuredError):
    """Runtime failure not caught by static type-check — the closed enum's third class
    (error-channel/reference.md § The closed enum of error classes; R-error-channel-001).
    Constructed at exactly two runner sites: the dispatch-boundary wrap (any uncaught
    exception that is not already ContractViolation / SchemaValidationError) and the
    cooperative pipeline-level timeout.

    Carries the full decided payload (§ PipelineFailure payload). The consumer's dispatch
    surface is two fields: ``failure_category`` — the **closed** structural-locus enum
    (``service`` / ``handler`` / ``engine``; :data:`_FAILURE_CATEGORIES`) naming *where* the
    failure arose, set by the runner from which internal scope raised it and **never**
    inferred from the exception name (the constructor rejects any other value and seals
    ``service_binding_name`` present iff ``"service"``) — and the open ``cause_class``
    (*what* threw). Deliberately absent —
    by design, not omission: ``audit_code``, ``rule_id``, ``remediation_hint``,
    ``expected``, ``actual``, ``file_path``. Every PF maps to the single registered wrap
    audit (:data:`PIPELINE_FAILURE_WRAP_AUDIT_CODE` via ``Check.PIPELINE_FAILURE_WRAP``)
    rather than a per-violation catalog entry, so the constructor takes **no**
    audit/check/rule argument.

    Both snapshots are **deep-copied at construction** (:func:`snapshot_copy`) — the
    structural form of the payload's deep-copy mandate: a PF cannot be built holding live
    references whose later mutation would rewrite the failure record.
    """

    def __init__(
        self,
        *,
        failure_category: str,
        cause_class: str,
        cause_message: str,
        failed_handler_qualified_name: str,
        failed_handler_position: int,
        bindings_snapshot: "Mapping[str, object]",
        reads_snapshot: "Mapping[str, object]",
        pipeline_run_id: str,
        composition_ref: str,
        service_binding_name: str | None = None,
        elapsed_ms_at_failure: int | None = None,
    ) -> None:
        # PF is runtime-only: a run and a composition locus always exist at failure time
        # (§ Location-bearing field requirement: composition_ref required and always
        # present; pipeline_run_id always present and non-null). Empty either way is a
        # runner-construction bug — fail loud.
        if not pipeline_run_id:
            raise ValueError(
                "PipelineFailure requires a non-empty pipeline_run_id — PF is "
                "runtime-only; no run in flight means no PF "
                "(error-channel/reference.md § PipelineFailure payload)"
            )
        if not composition_ref:
            raise ValueError(
                "PipelineFailure requires a non-empty composition_ref — runtime failure "
                "always has a known pipeline and entry ordinal "
                "(error-channel/reference.md § Location-bearing field requirement)"
            )
        # guarantees: pf-failure-category-closed-enum
        if failure_category not in _FAILURE_CATEGORIES:
            raise ValueError(
                f"failure_category must be one of {list(_FAILURE_CATEGORIES)}, got "
                f"{failure_category!r} (error-channel/reference.md § PipelineFailure payload)"
            )
        # guarantees: pf-service-binding-iff-service
        # The structural form of the payload's presence rule: service_binding_name is present iff the
        # locus is a service backend call. A binding on a handler/engine locus, or its absence on a
        # service locus, is a runner-construction bug.
        if (failure_category == "service") != (service_binding_name is not None):
            raise ValueError(
                "service_binding_name must be present iff failure_category == 'service' (got "
                f"failure_category={failure_category!r}, service_binding_name={service_binding_name!r}; "
                "error-channel/reference.md § PipelineFailure payload)"
            )
        self.failure_category = failure_category
        self.cause_class = cause_class
        self.cause_message = cause_message
        self.failed_handler_qualified_name = failed_handler_qualified_name
        self.failed_handler_position = failed_handler_position
        # guarantees: pf-snapshot-deepcopy
        self.bindings_snapshot = snapshot_copy(dict(bindings_snapshot))
        self.reads_snapshot = snapshot_copy(dict(reads_snapshot))
        self.pipeline_run_id = pipeline_run_id
        self.composition_ref = composition_ref
        self.service_binding_name = service_binding_name
        self.elapsed_ms_at_failure = elapsed_ms_at_failure
        super().__init__(self._render())

    def _render(self) -> str:
        """The canonical default template (mirrors the sibling classes' shape; renders
        no rule citation — PF carries no ``rule_id`` by design)."""
        msg = (
            f"PipelineFailure ({self.failure_category}/{self.cause_class}) at {self.composition_ref} — "
            f"'{self.failed_handler_qualified_name}'"
            f"[{self.failed_handler_position}] (run {self.pipeline_run_id}): "
            f"{self.cause_message}"
        )
        if self.service_binding_name is not None:
            msg = f"{msg} [service binding '{self.service_binding_name}']"
        if self.elapsed_ms_at_failure is not None:
            msg = f"{msg} [elapsed {self.elapsed_ms_at_failure} ms]"
        return msg

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"PipelineFailure(failure_category={self.failure_category!r}, cause_class={self.cause_class!r}, "
            f"handler={self.failed_handler_qualified_name!r}"
            f"@{self.failed_handler_position}, ref={self.composition_ref!r})"
        )
