---
kind: reference
audience: [authors, integrators, agents]
slug: error-channel-reference
component: error-channel
---

{#error-channel-reference}
# Error-channel reference

The per-component reference for the
[error-channel](#glossary-error-channel) component — the engine's
typed surface for expressing failure. What lives here:

- The **closed enum** of error classes — partitioned by where in the type system
  the failure originates.
- **ContractViolationGroup** — the compose-time container that wraps two or more
  `ContractViolation`s aggregated from one composition-validation group; not a member
  of the closed enum.
- **Halt semantics** — what happens on each class at each handler kind, including
  the hook-wrapper sanction.
- **No engine retry surface** — why the engine has no retry API and why that
  matters for the training projection.
- **Output channel and error channel** as distinct API surfaces — the
  no-partial-output-on-halt invariant.
- **Timeouts** — two kinds, their declared surfaces, and the never-hashed property.
- **Error payload field set** — per-class structured fields the engine populates on
  every raised instance.
- **cause_class semantics** — the runtime-failure dispatch surface: the closed
  `failure_category` (where the failure occurred) plus the open `cause_class` (what threw).
- **RFC 9457 HTTP wire projection** — how each error class maps to an RFC 9457
  Problem Details envelope at the HTTP boundary.
- **Error-index codegen** — the generated cross-reference artifacts that map
  `audit_code` → derived rule.
- The component's **derived rules** — the `R-error-channel-*` set, defined below.

---

{#the-closed-enum-of-error-classes}
## The closed enum of error classes

The engine surfaces failures through the [closed](#closed-enum) enum of
[error classes](#error-class) — [ContractViolation](#contractviolation),
[SchemaValidationError](#schemavalidationerror), and
[PipelineFailure](#pipelinefailure). For halt behavior per class and per
handler kind, see [§ Halt semantics](#halt-semantics). See
[derived rule R-error-channel-001](#error-channel-derived-rules).

:::{region} error-classes/kernel
The engine raises a closed-enum set of error classes, partitioned by where in
the type system the failure originates:

- **ContractViolation** — structural type-check failure. A channel, kwarg, or
  field is declared but absent, or present but undeclared; the interface's
  declaration set is structurally wrong. Examples: a handler returns a key absent
  from `output_schema` (undeclared write); a declaration carries an unknown field
  name; a Python signature carries a kwarg absent from `reads` (signature
  mismatch); a required `output_schema` field is omitted from the return dict
  (missing declared write); `service_bindings` appears on a transform declaration
  (kind-grammar violation); a binding-value declaration referenced by a handler
  entry fails to resolve; a qualified-name reference fails to resolve. Raised at
  handler-declaration load, at pipeline compose time, or at the dispatch boundary
  (a declaration-existence mismatch on the return dict, a hook's non-`None`
  return, a missing declared input at the API boundary).

- **SchemaValidationError** — value-level type-check failure within declared
  fields. The declaration set is structurally intact; a value violates its
  declared type. Examples: a declared `int` field receives `str`; a declared
  regex-constrained string fails its pattern; a declared enum field receives an
  out-of-set value. Raised by Pydantic validation against the declaration-derived
  class — at pre-dispatch kwarg projection (validating `reads` channel values) and
  at post-dispatch return-dict validation (validating the handler's return dict
  against `output_schema`).

- **PipelineFailure** — runtime failure not caught by static type-check. Wraps
  the underlying exception, carrying its verbatim type in an open `cause_class`
  field (e.g., `TimeoutError`, `ConnectionError`) and the structural locus in a
  closed `failure_category` field (`service` / `handler` / `engine`). These two
  fields are the consumer's dispatch surface for runtime failure — no named
  sub-class of `PipelineFailure` exists and none is needed: halt semantics are
  identical across all runtime failures, so the distinctions consumers route on
  ride fields, not an inflated error-class vocabulary.

The enum is closed: consumers may rely on the exhaustive enumeration at dispatch
time, and extending it is an engine change with an architecture decision, not a
runtime extension. The runner enforces the guarantee at the dispatch boundary: any
uncaught exception not already a `ContractViolation` or `SchemaValidationError` is
wrapped into `PipelineFailure` before surfacing through the error channel.
:::

:::{region} R-error-channel-001/key-set-routing
The key-set→ContractViolation routing is scoped by boundary. Key-set faults — a
key, kwarg, or field undeclared, or a required declaration missing — raise
ContractViolation at declaration/compose surfaces and at the **output** boundary
(the return-dict validation, per R-handler-001's output validation). At the
**API** boundary (the pipeline-level `inputs` pre-validation of the incoming
request per R-pipeline-001, before any node dispatches), the routing covers the
**missing-declared-input direction only**: a declared input field absent from
the incoming request raises ContractViolation; an undeclared key in the
incoming request is **not admitted but not an error** — the runner seeds only
the declared input channels, so an extra never becomes a channel
(R-pipeline-001's `api-inputs-enforcement` fragment owns the admission rule).
At the **input** boundary — the per-dispatch reads-projection, where the kwargs
are the engine's own assembly rather than an author's declared return — every
validation failure, key-set faults included, surfaces as SchemaValidationError.
:::

**Derivation from I1.** The closed-enum partition is the failure-surface projection
of [I1](#invariants-and-derived-rules) (no implicit
contracts): no key, kwarg, or field enters or exits the graph without going through
a declared interface. `ContractViolation` fires when the declared interface set is
structurally wrong — a required declaration is missing or an unexpected one is
present. `SchemaValidationError` fires when the declared interface set is correct
but a value violates its declared type. `PipelineFailure` absorbs everything the
static interface declarations cannot surface — runtime conditions outside the type
system's reach. The classes partition the complete failure space without overlap;
an additional class would either recategorize a failure the type system already
names (overlap) or name a failure mode outside the declared-interface surface
(outside scope). Both cases require explicit justification — a change to the
closed error-class set is an engine change.

---

{#contractviolationgroup}
## ContractViolationGroup — the compose-time multi-violation container

[ContractViolation](#contractviolation) is single-violation: one structural fault, one
raised instance. Compose-time validation, however, can detect several independent
structural faults in a single pass, and the
[aggregate-within-a-group, fail-fast-across-groups policy](#composition-validation/error-reporting)
the pipeline reference owns requires the engine to report every independently-detectable
failure within a check group, not only the first. `ContractViolationGroup` is the
container that carries them.

`ContractViolationGroup` is **not a fourth error class**. The
[closed enum](#the-closed-enum-of-error-classes) above is unchanged: the runtime error
channel still surfaces exactly [ContractViolation](#contractviolation),
[SchemaValidationError](#schemavalidationerror), and
[PipelineFailure](#pipelinefailure). `ContractViolationGroup` is a **compose-time
container** that wraps two or more class-1 `ContractViolation`s produced by one
composition-validation group. It never arises at dispatch time (runtime failures are
single-locus, each surfacing as one closed-enum class) and never at stage-1 declaration
parse (which is fail-fast — a parse failure halts at the first malformed declaration). It
is the compose-time analogue, at the **violation grain**, of the multi-error posture
[SchemaValidationError](#schemavalidationerror)'s `field_validations` array takes at the
value grain: where SVE collects multiple failed *fields* inside one error,
`ContractViolationGroup` collects multiple independent *violations* — each a full
`ContractViolation` — detected across one validation pass.

{#contractviolationgroup-when-raised}
### When it is raised

When a single stage-2 composition-validation check group — one of the groups the pipeline
reference's § Composition validation owns — detects:

- **exactly one** violation — the bare [ContractViolation](#contractviolation) is raised.
  This is the common case: no wrapping, so single-fault diagnosis and the consumers that
  catch `ContractViolation` are unchanged.
- **two or more** violations — a `ContractViolationGroup` wrapping them is raised.

The single-violation case raising the bare class is **not** a collapse of the multi-error
posture. The SVE "single-field collapse is forbidden" rule is at the field grain *within*
one error; at the violation grain, one violation IS fully reported by the one
`ContractViolation`, and a one-element container would carry nothing the bare violation
does not. Across groups the order stays fail-fast (a group's failure short-circuits the
groups whose preconditions it invalidates) per the cited policy — so a
`ContractViolationGroup` only ever aggregates violations from **one** group.

{#contractviolationgroup-payload}
### ContractViolationGroup payload

Every `ContractViolationGroup` carries:

**Required:**

- **`violations`** — a tuple of **two or more** [ContractViolation](#contractviolation)
  instances, in the order the group detected them. Each member carries its own complete
  [ContractViolation payload](#contractviolation-payload) (its own `check` / `rule_id`,
  `expected` / `actual`, location-bearing fields, …); the group adds no per-violation
  fields of its own.
- **`message`** — auto-rendered human-readable stringification: a one-line summary naming
  the violation count, followed by each member's rendered message, so a log consumer
  reading only the string sees every aggregated failure.

The members all originate from one composition-validation group on one pipeline, so they
share a compose locus (the pipeline's `file_path` / `composition_ref`); the group declares
no location of its own — each member carries its own per-violation locus.

---

{#halt-semantics}
## Halt semantics

Any of the three [error classes](#error-class) halts
transforms and services. No fallback-mode carve-out exists for either kind: when a
transform or service cannot perform its declared function, the pipeline stops and
the consumer receives the error channel to decide the user-facing response.

The halt rule follows from the channel-write obligation. Transforms and services
occupy [node roles](#node-role) that emit
[channels](#channel) — their return dicts reach
downstream nodes as typed channel values. Halting on
every error class is the mechanical consequence of having a downstream channel to
protect, not a policy election — the channel-writing-implies-halt derivation (a
swallowed failure produces a channel value indistinguishable from a successful one) is
owned by [handler-kinds § Comparison](#comparison).

Hooks have a bounded two-case rule governed by their kind's node role — see
[§ Hook-wrapper sanction](#hook-wrapper-sanction). The two-case rule is part of this
specification and is specific to the hook kind; transforms and services have no
analogous carve-out.

A **trainable composition node** is a channel-writing position too — its engine-
constructed dispatch (no author body, per [R-handler-010](#R-handler-010/no-author-body)) writes
the declared `output_schema` channels — so it halts on any error class exactly as a
service does: a backend failure surfaces as [PipelineFailure](#pipelinefailure)
(carrying `cause_class`), and a backend return that fails the literal-equal
`output_schema` validation surfaces as [SchemaValidationError](#schemavalidationerror).
Preprocessor handlers inside the composition halt per their own kind (transform /
service / hook).

The halt rule is mechanically enforced: the runner halts pipeline execution on any
error class raised from a transform, service, or trainable composition node dispatch. No retry primitive exists
in the engine at the dispatch level; transport-level retry is implementation-internal
to service-type adapter code and does not surface as a re-dispatch to the runner.
See [derived rule R-error-channel-003](#error-channel-derived-rules).

{#hook-wrapper-sanction}
### Hook-wrapper sanction

The runner's hook wrapper — which catches operational
[PipelineFailure](#pipelinefailure) and continues
pipeline execution — is not a fallback and not a per-handler exception. It is a
structural property of the hook [kind](#handler-kind)
derived from the kind's node role.

A hook occupies the **observer** [node role](#node-role): it reads its declared
input ports as kwargs (wired to channels by the node's read-map) and emits
externally.

:::{transclude} the-hook-kind/observer-write-profile
:::

That a hook writes no channels is the load-bearing graph property here.

Because hooks write no channels, operational failure inside a hook (network
unreachable, remote 5xx, timeout) loses a side-effect record — the external emission
did not arrive — but cannot corrupt any downstream channel read. The pipeline can
continue with channel integrity intact. This is the structural reason the
operational tolerance is safe at the hook position and unsafe everywhere else.

Transforms and services cannot have a comparable carve-out. Both kinds occupy
channel-writing positions — their return dicts are routed onto the graph as typed
channel values downstream nodes consume. The halt rule for transforms and services is
channel-value integrity enforcement — the channel-writing-implies-halt derivation is
owned by [handler-kinds § Comparison](#comparison).

The two-case structure is exhaustive:

- **Operational PipelineFailure** (network unreachable, remote 5xx, timeout) —
  caught by the runner's hook wrapper; execution continues. The loss is a missing
  side-effect record, not a corrupted channel.
- **ContractViolation and SchemaValidationError** from a hook — still halt. These
  are graph-shape failures — a declaration mismatch or a type violation — that
  indicate a structural problem independent of in-flight operational conditions. The
  runner cannot safely proceed past a graph-shape failure regardless of hook
  position.

The hook wrapper is engine-owned. Hook authors never write in-body `except`
statements to catch or absorb any error class. In-body `except` in any handler
body — transform, service, or hook — is a named
[silent-fallback](#silent-fallback) violation governed by
R-handler-002 (no silent fallbacks). The hook's operational tolerance is delivered
by the engine-owned wrapper surrounding the dispatch callable; no code in the
handler body participates in it.

---

{#no-engine-retry-surface}
## No engine retry surface

The engine exposes no retry surface. The runner has no `max_retries` field, no
engine-declared retry count, no retry wrapper between the engine-constructed dispatch
callable and the service-type adapter. There is no configuration path through which
a consumer can request engine-level retry; the prohibition is enforced by
absence-of-API rather than by runtime check. See
[derived rule R-error-channel-002](#error-channel-derived-rules).

**Absence-of-API enforcement.** R-error-channel-002 is mechanically enforced by
structural absence rather than by runtime detection. The three non-existent items —
`max_retries` field, engine-declared retry count, retry wrapper — cannot be
configured because no configuration path for them exists in the engine's API,
handler-declaration grammar, or runner code path. An attempt to introduce retry
configuration via a handler-declaration block is rejected at handler-declaration
load by the closed-shape grammar (R-handler-006); there is no recognized block for
retry configuration because none is declared.

**Transport retry vs semantic retry.** The two kinds are structurally distinct, decided
by what **triggers** the next attempt:

| | Transport retry | Semantic retry |
|---|---|---|
| **Trigger** (the deciding axis) | A transport fault before any usable response exists (connection reset, 5xx, timeout) | A verdict on the response (empty, structurally invalid, refusal) |
| Payload across attempts (a correlate, not the criterion) | Identical (same bytes) | Usually modified — but identical on a verdict-triggered resend |
| Engine disposition | Sanctioned | Forbidden |
| Named examples | Connection-reset retry, 5xx backoff, momentary-unreachability recovery | Critique-and-revise, validation-and-retry, prompt-augmentation loops |
| Channel-record correspondence | Preserved | Broken |
| Training-projection effect | One captured record per semantic call | Multiple captured events per channel-write |

:::{region} no-engine-retry/payload-predicate
The deciding axis is the **trigger of the next attempt**, not payload-identity. A retry
triggered by a **verdict on the response** — empty, structurally invalid, or a refusal —
is **semantic** (forbidden), *even when the resubmitted bytes are identical*. A retry
triggered by a **transport fault before any usable response exists** — connection reset,
5xx, a timeout with nothing returned — is **transport** (sanctioned, impl-internal).
Payload-identity is only a correlate: transport retries resend the same bytes and semantic
retries usually modify them, but an identical-bytes resend driven by a response verdict is
still semantic.
:::

Transport retry lives inside the service-type implementation — the implementation
may retry the underlying transport call before returning a result to the adapter.
The adapter captures the `service_invocation` event for the final resolved call;
earlier transport-recovery attempts are impl-internal and not separately captured.
From the engine's perspective, one semantic external interaction occurred, and
[channel-record correspondence](#channel-record-correspondence)
holds.

Semantic retry inside a handler body breaks the correspondence: the handler calls
`services.<name>.invoke(...)`, receives a result, evaluates it, then calls
`services.<name>.invoke(...)` again because of that verdict — typically with modified
arguments, but a resend of identical bytes after judging the reply unusable counts the
same. The engine captures one
`service_invocation` event per adapter call — multiple attempts produce multiple
events for one handler dispatch. The per-dispatch bijection breaks: more events are
captured than map to the single channel-write the handler produces at exit. A
training corpus consumer cannot determine which attempt is the authoritative record
for the channel-write without applying judgment outside the engine's type-check
surface.

**Handler-body opacity.** The runner dispatches through the service-type adapter and
captures the `service_invocation` event at the adapter boundary; it does not inspect
what the handler body does between calls to `services.<name>.invoke(...)`.
[R-handler-002](#R-handler-002) (no silent fallbacks) is the primary defense: semantic retry is a
named instance of the no-silent-fallbacks violation, and the rule is
review-enforced. The adapter-boundary second layer makes review mechanically grounded:
two `service_invocation` events sharing one `(pipeline_run_id, handler_position)` —
two backend calls attributed to a single node dispatch — is the wire-visible signal
for a multi-attempt dispatch. A handler reused at several node positions dispatches
legitimately once per position; position (not the qualified name, which is no longer
unique within a run) is the dispatch identity, so two `service_invocation` events
that share a `handler_qualified_name` but differ in `handler_position` are ordinary
multi-dispatch, not a violation.

**Scope.** Re-invocation of the same external call with different inputs —
critique-and-revise loops, validation-and-retry chains — is consumer multi-pipeline
orchestration: each re-invocation is its own pipeline run with its own
`service_invocation` event, its own channel-record, and its own position in the
training corpus. Consumer-layer re-invocation preserves channel-record
correspondence by construction. The engine has no surface for cross-run
orchestration because cross-run orchestration is consumer territory per
[I3](#invariants-and-derived-rules).

---

{#output-channel-and-error-channel-are-distinct-surfaces}
## Output channel and error channel are distinct surfaces

The engine surfaces every pipeline invocation through two distinct API channels —
the **output channel** and the **error channel** — separated by construction, not by
convention. See [derived rule R-error-channel-004](#error-channel-derived-rules).

**Output channel.** On happy-path completion, the runner returns the run's result —
the declared `outputs` fields are the committed surface the consumer relies on,
returned within the run's full outer-channel state (the pipeline reference's
RunResult owns the result shape). The existence of a returned value IS the
success signal. No `success` / `ok` / `status`
discriminated-union field exists on the return; a Boolean conflating the two
channels would let consumer code bypass the error channel entirely.

**Error channel.** When any error class is raised from a channel-writing dispatch
(transform, service, or trainable composition node), the runner halts and surfaces
the failure through the error channel. The error channel carries (each class's
exact field set is the per-class payload spec below):

- **Error class** — the raised exception type, one of the closed-enum classes.
- **Error message** — the human-readable failure description.
- **Handler identity** (dispatch-time failures; a load- or compose-time
  ContractViolation's locus is its `file_path` / `composition_ref`) — the qualified
  name of the handler at whose graph position the halt occurred.
- **Binding snapshot** (runtime failures — `PipelineFailure`) — the compose-time
  binding values bound at that node.
- **Reads snapshot** (runtime failures — `PipelineFailure`) — the channel values
  projected into the failed handler's dispatch
  kwargs at failure time. This is a **diagnostic payload** for the consumer to log,
  display, and debug — not a pipeline output for downstream composition.
- **Service context** (where applicable) — the bound service-type identity at the
  failing node, when the failure occurred at a service dispatch.

**No partial output on halt.** The output channel is silent on halt: the runner raises
the error class and **no partial channel values are returned as pipeline output**.
When a handler raises, its `output_schema` [output ports](#output-port) produce no values — the
handler did not produce a return dict. Downstream nodes at those channel positions
have no source to read from. Returning partial output would surface an incomplete
channel set as if the pipeline had successfully completed, corrupting the declared
pipeline contract at the API surface.

**Why the separation is structural.** The two-channel discipline is a consequence of
the typed dataflow graph's channel-integrity requirement under halt. Transforms and
services halt on any failure because a swallowed failure would produce a channel
value the runner cannot distinguish from a successful one, propagating downstream as
if real (the channel-writing node roles and the halt constraint that follows from
them are owned by [handler-kinds](#architecture-handler-kinds)). The output
channel and error channel separation is that invariant expressed at the
API surface: on halt, the output surface carries nothing; the error surface carries
the reads snapshot and the diagnostic payload. No cross-channel leakage — error context
does not contaminate the output channel and the output channel does not absorb error
context.

**Event trace — available on both paths.** The runner maintains a trace of every
handler dispatch throughout a pipeline run: channel values at each node's entry and
exit, timing per handler, and (on halt) the failed handler's identity and failure
details. The trace is available on both the success path and the halt path via the
API's canonical event log (`conjured.events.runner`); a consumer reconstructs any
run's channel-state evolution by filtering the
[canonical event](#canonical-event) stream on
`pipeline_run_id`. The
[`pipeline_error` event](#canonical-event-types)
carries the closed `error_class` and, for `PipelineFailure`, its `failure_category`,
`cause_class`, and failed-handler identity — the in-log counterpart to the error channel's
in-process reads snapshot (its full payload field set is owned by the canonical-event-types table). The reads snapshot is the per-handler slice
captured at failure; the event trace is the full run-level event log. See
[Replayability](#replayability) for why the trace is
sufficient to reconstruct any captured run on both paths.

**No discriminated union.** The channel separation rules out any `success` / `ok` /
`status` Boolean on the runner's return value. A returned value means success; a
raised exception means halt; the consumer dispatches on which it receives.

---

{#timeouts}
## Timeouts

The engine recognizes two timeout surfaces, with different engine involvement. Both are
**transport-layer, not composition-layer** (operational concerns), and neither contributes to the
[pipeline-hash](#what-the-pipeline-hash-absorbs) or
the [training-bundle-hash](#training-bundle-hash-construction).
The difference is what the engine *does* with each: the whole-run budget it **enforces directly**; the
per-call service-binding timeout it **does not interpret at all** — that one is an author-named transport
value the service-type adapter reads and applies.

{#service-binding-timeout}
### Service-binding timeout

:::{region} service-binding-timeout/kernel
Declared as a field in a service type's `transport_schema`. The exact field name is
the service-type author's choice and `timeout_ms` is the canonical example. At
deployment time, the per-deployment value is supplied in the deployment
declaration's `transport.<binding>` block, following the identity-vs-transport
placement discipline: identity values belong in the pipeline declaration's
service-binding supply; transport values belong in `transport.<binding>`.
:::

The service-type adapter reads the bound transport block at dispatch and applies the
timeout to the outbound call. No handler body sees the timeout value; it is outside
the handler's declared `reads` and is reached by the adapter only.

When the outbound call exceeds the timeout, it surfaces as a
[PipelineFailure](#pipelinefailure) with [`failure_category`](#pipelinefailure-payload/failure-category)
`= "service"` and the failing `service_binding_name` present (its `cause_class` is the adapter's verbatim
timeout exception).

No contribution to any hash: the deployment `transport.*` block is
[explicitly excluded from pipeline-hash inputs](#what-the-pipeline-hash-absorbs).
Transport values may change per environment without affecting the pipeline contract
or training contract.

{#consumer-pipeline-level-timeout}
### Consumer pipeline-level timeout

:::{region} consumer-pipeline-level-timeout/request-param
An optional `timeout_ms` field in the API call to the engine. Enforced by the
**runner wrapper** — not the handler dispatch path — against the pipeline run's
elapsed time. If the budget is exceeded before the pipeline run completes, the runner
wrapper halts the whole run.
:::

This timeout surfaces to the consumer as a
[PipelineFailure](#pipelinefailure) with `failure_category = "engine"` and
`cause_class = "TimeoutError"` (no `service_binding_name` — a pipeline-level run-guard, not a
service call). See [§ Halt semantics](#halt-semantics) for the full halt behavior under
`PipelineFailure`. The broader `failure_category` / `cause_class` semantics and
consumer-dispatch discipline are addressed in
[§ cause_class semantics](#causeclass-semantics).

No contribution to any hash: the consumer pipeline-level timeout is an API call
parameter, absent from every handler declaration and pipeline declaration the hash
machinery reads. It is outside either hash's input surface by construction.

{#never-hashed-structural-confirmation}
### Never-hashed: structural confirmation

The never-hashed property of both timeout kinds is a structural guarantee, not a
disciplinary one:

- **Service-binding timeout** — lives in `transport_schema` (declared) and
  deployment `transport.<binding>` (valued). The hash model explicitly excludes all
  deployment `transport.*` values from pipeline-hash inputs.
- **Consumer pipeline-level timeout** — an API call parameter absent from every
  declaration the hash machinery reads. Not in scope for either hash by construction.

The transport / composition split is load-bearing for the training contract: values
that shift per deployment without a hash shift are definitionally outside the
composition identity. Timeouts are the canonical example of such values. See
[hash-model § What is NOT hashed](#what-the-pipeline-hash-absorbs).

{#handler-declaration-input-schemas-never-declare-timeout-fields}
### Per-deployment timeouts belong in transport, not hashed surfaces

A per-call service-binding timeout varies by deployment (production budget vs staging vs local) and so
belongs on the **never-hashed transport surface** — an author-named field in the bound service type's
`transport_schema`, valued per deployment in `transport.<binding>`. The engine **reserves no
timeout-field vocabulary** and runs **no timeout-placement check**: it never interprets the per-call
timeout (the service-type adapter reads and applies it), so it cannot — and need not — recognize one
transport field as "a timeout."

The hazard the transport surface guards against is real but **general, not timeout-specific**: a value
that varies per deployment, placed in a **hashed** block (`bindings.<name>` or a pipeline-declaration
identity supply), makes the same composition behave differently across deployments that share one
pipeline-hash — the hash-invisible behavioral divergence the training contract is built to prevent. The
protection is **structural**: transport is never-hashed and is the documented home for per-deployment
values, so declaring a timeout (or any per-environment value) in `transport_schema` gets the correct
never-hashed behavior by construction. Where an author *misplaces* a per-deployment value into a hashed
surface, that is the general transport-vs-identity authoring discipline — caught by review, the same for
any misplaced per-deployment value — not a timeout the engine special-cases.

{#anomaly-detection-is-not-engine-territory}
### Anomaly detection is not engine territory

Runtime anomaly detection — "halt if this handler is slower than N standard
deviations above historical average" — is not an engine concern. This shape of
monitoring belongs to consumer observability (a consumer wrapping pipeline
invocations with its own latency-baseline monitoring) or service-internal monitoring
(a service-type adapter tracking its own latency distributions). The engine declares
no anomaly-detection surface and no threshold-bearing timeout mechanism that
approaches this shape.

---

{#error-payload-field-set}
## Error payload field set

The engine's error classes carry distinct structured payload field sets shaped to
their failure contexts. Declaration violations surface TOML-locus fields; body-output
violations surface handler-dispatch fields; runtime failures surface cause fields and
channel snapshots. No single field set fits all classes; per-class enumeration is the
correct factoring.

Two fields appear across every class: `pipeline_run_id` (the cross-run correlation
key joining this error to the pipeline-run event log — its engine-generated form is
owned at [hash-model § canonical event types](#canonical-event-types), a colon-free
basic ISO-8601 id that rides a URI verbatim, no percent-encoding) and `message` (the
auto-rendered human-readable stringification for log pipelines and `__str__`).
Beyond those, field sets diverge by class.

The in-process error payload (this section) and the `pipeline_error` canonical event
payload (see [hash-model § canonical event types](#canonical-event-types))
are distinct surfaces; the event's payload field set is owned at the hash-model
table. In-process consumers reading the structured diagnostic payload
use the fields enumerated below; consumers joining error records to event-log traces
use `pipeline_run_id` as the correlation key.

{#rendered-message-cites-the-rule}
**The rendered message cites its rule (rule-bearing classes).** For `ContractViolation`
and `SchemaValidationError` — the two classes that carry a `rule_id` — the default-rendered
`message` MUST cite the enforcing `rule_id` and `audit_code` inline. The error message is
**self-steering**: an agent or author reading only the message is routed to the governing
derived rule and its audit-catalog entry without parsing the structured payload. The citation's
*presence* is the contract; the exact rendered form (field order, separators, placement) is the
default template's to settle. `PipelineFailure` carries no `rule_id` — runtime failure has no
declaration-site rule — and renders no such citation.

{#contractviolation-payload}
### ContractViolation payload

Every `ContractViolation` carries these fields:

**Required:**

- **`audit_code`** — the audit-catalog dispatch key (format and routing role: see
  [glossary § audit code](#audit-code); the realized component→`<CX>` allocation is the
  code's registered error set projected into the generated
  [error-index](#error-indexmd-consumer-facing-diagnostic-cross-reference)). Present once the
  audit catalog assigns this violation's code; **`null` for a violation with no assigned code** —
  the symbolic `check` discriminator is then the dispatch key, and the wire projection falls
  back to `about:blank` (see [§ ContractViolation with no assigned audit_code](#contractviolation-audit-code-absent)).
- **`rule_id`** — derived-rule identifier (e.g., `"R-handler-001"`,
  `"R-pipeline-002"`); names the per-component derived-rules section the audit
  enforces. Complements `audit_code` (catalog entry) by naming the canonical rule;
  agents asserting "rejected by rule X" route on `rule_id`.
- **`file_path`** — project-relative path to the offending artifact (handler
  declaration, pipeline declaration, deployment config, or similar). At least one of
  `file_path` or `composition_ref` MUST be non-null — see
  [§ Location-bearing field requirement](#location-bearing-field-requirement).
- **`expected`** — one-line declarative description of what the contract requires
  (e.g., `"handler declaration includes reads block"`). Declarative form, not
  imperative.
- **`actual`** — one-line declarative description of what the engine found (e.g.,
  `"handler declaration reads block absent"`). Declarative form.
- **`message`** — auto-rendered human-readable stringification of the payload per the
  default template. Log pipelines and `__str__` consume this; consumer tools MAY
  render their own form from the structured fields above.

**Optional:**

- **`section_path`** — dotted-section path when the violation lives inside a
  structured artifact (e.g., `"bindings.mood"` or `"service_bindings.npc_db"`). Null
  for violations detected at file level or at composition level without a single
  declaration block.
- **`line_number`** — 1-based line number from the declaration parser when available.
  Null for violations detected at composition level where no single source line is
  responsible.
- **`composition_ref`** — the composition-level locus identifier (format and role:
  see [glossary § composition ref](#composition-ref)). At least one of `composition_ref`
  or `file_path` MUST be non-null.
- **`pipeline_run_id`** — correlation identifier. Null for load-time and compose-time
  violations (no run in flight at those points); present when the runner wraps a
  `ContractViolation` raised mid-dispatch. For the API-boundary missing-declared-input
  `ContractViolation` — raised before any node dispatches, so no run is ever in flight —
  the field echoes the consumer-supplied run identifier when the invocation passed one,
  and is null otherwise: the engine generates a run identifier only for a run that
  starts, and this run never starts.
- **`remediation_hint`** — short actionable consumer guidance (format and role: see
  [glossary § remediation hint](#remediation-hint)).

{#schemavalidationerror-payload}
### SchemaValidationError payload

Every `SchemaValidationError` carries these fields:

**Required:**

- **`audit_code`** — audit catalog identifier. SVE is raised at **two** boundaries, each with
  its own code: one for the post-handler **output**-validation audit and one for the
  pre-dispatch **reads-projection** (input) validation audit. Primary dispatch key for
  SVE-routing consumers; format mirrors `ContractViolation`.
- **`rule_id`** — derived-rule identifier (canonically `"R-error-channel-003"` for the
  halt-semantics rule the SVE-halting audit enforces). Complements `audit_code` per the
  same rule-vs-catalog distinction described on `ContractViolation`.
- **`handler_qualified_name`** — qualified name of the handler whose **reads-projection or
  output** failed validation (e.g., `"conjured_npc.npc_emotion"`). The location-bearing field for
  `SchemaValidationError` — dispatch-time failure always attributes to exactly one
  dispatched handler.
- **`handler_position`** — the 0-indexed
  [dispatch-identity](#canonical-event-types/dispatch-identity) position of the failing
  handler's dispatch (hash-model owns the definition); together with
  `handler_qualified_name` it uniquely identifies the dispatch (the qualified name alone is
  not unique within a run under multi-dispatch).
- **`pipeline_run_id`** — correlation identifier. Always present and non-null:
  `SchemaValidationError` can only be raised mid-dispatch; no run in flight means no
  SVE.
- **`schema_source`** — project-relative path to the handler declaration whose violated
  contract this is — the handler's `reads` declaration on a reads-projection failure, its
  `output_schema` declaration on an output-validation failure (e.g., `"handlers/npc_emotion.toml"`).
  Distinct from `handler_qualified_name` / `handler_position` (the
  [dispatch-identity key](#canonical-event-types/dispatch-identity) hash-model owns);
  `schema_source` is the contract-document
  path the consumer opens to inspect or edit the declared schema.
- **`field_validations`** — non-empty array of `FieldValidationDetail` entries. Every
  field that failed validation gets its own entry; single-field collapse is forbidden
  — Pydantic's natural surface is multi-error, and collapsing multiple field
  violations into one discards declared contract failures without justification.
  **Entries are ordered by the violated schema's declaration order** (`output_schema`, or
  `reads` on a reads-side failure — the order the fields are declared in that schema), so the array — and the RFC-9457 `detail`
  join over `field_validations[].message` — is deterministic.
- **`message`** — auto-rendered human-readable stringification of the payload.

`SchemaValidationError` carries no `remediation_hint` — its `field_validations` entries and
`message` carry the actionable detail directly.

Each `FieldValidationDetail` entry carries:

- **`field_path`** — dot-notation path to the offending field, prefixed by the violated
  schema (`output_schema.…` on an output failure, `reads.…` on a reads-projection failure):
  e.g. `"output_schema.mood.intensity"` (top-level), `"output_schema.mood.label"` (nested),
  `"output_schema.tags[2]"` (array element), `"reads.context"` (a reads-projection failure).
- **`expected_type`** — the declaration-canonical type the field declares in its
  **violated schema** — `output_schema` on an output-validation failure, `reads` on a
  reads-projection failure — as a [channel-field type](#channel-field-type), the same form the
  handler declares its channels in, not the Pydantic class name. Two cases have no
  declared token and render fixed sentinels: `"nested object"` for a nested-object
  field (the handler reference's § TOML field type discipline owns the
  structural-nesting grammar), and `"(undeclared)"` for an undeclared key — inside
  a declared port's value on an output-validation failure, or a top-level
  undeclared key in the assembled reads kwargs on a reads-projection failure (no
  declared type exists for either; its `constraint_violated` is the built-in
  `"keys_subset_of"`).
- **`actual_type`** — Python runtime type of the offending value, captured as
  `type(value).__name__` (e.g., `"str"`, `"int"`, `"NoneType"`); `"absent"` when the
  field is required-and-missing — no offending value exists to type, and `"NoneType"`
  would conflate absence with an explicit `None`.
- **`actual_value`** — `repr()` of the offending value, truncated to 256 characters; when
  truncation occurs a marker reporting the elided count is appended (e.g. `…(+1882 chars)`),
  so a consumer sees that truncation happened and how much was dropped.
  Nullable: `null` when the offending value is `None` (distinguishable from `"None"`
  repr) or when no offending value exists (`actual_type = "absent"`). Omitted from the
  default rendered message; consumer tools MAY surface it in
  verbose modes.
- **`constraint_violated`** — the named constraint that rejected the value. Like
  [`cause_class`](#causeclass-semantics), this is an **open** vocabulary, not a closed
  enum: a consumer dispatches on the known built-in constraints and applies fallback
  behavior for any value it does not recognize. The engine's built-in constraints are
  `"type"` (structural type mismatch), `"required"` (a required field absent within a
  declared port's nested value, or a required kwarg absent at the input boundary —
  a top-level declared port missing from the **return dict** is the output key-set
  case and raises ContractViolation, per R-handler-001's
  output-validation routing), `"nullable"` (null written to a non-nullable field), the **standard
  validation-keyword names** — the JSON Schema validation keywords applicable to the
  field's declared type (`"enum"` for one), whose membership and per-type applicability
  mapping the handler reference's § Validators ([R-handler-012](#R-handler-012)) owns —
  and `"keys_subset_of"` — produced **structurally** by the
  closed declaration shape (mapped from the generated model's extra-key rejection),
  not an attachable constraint. A registered third-party validator contributes its own
  qualified-name constraint here — which is precisely why the field is open: the engine
  cannot enumerate constraints a third party has not written yet.
- **`message`** — one-line human-readable description of this specific field's failure
  (e.g., `"value 11 above maximum 10"` or `"expected one of [happy, sad, angry], got
  'confused'"`).

{#pipelinefailure-payload}
### PipelineFailure payload

Every `PipelineFailure` carries these fields:

**Required:**

- **`cause_class`** — the underlying Python exception class's `__name__` at wrap time (e.g.
  `"ConnectionError"`, `"TimeoutError"`, `"ValueError"`). Free-form string; **open**, not a bounded
  enum — it carries *what* threw, and the engine cannot enumerate consumer-domain exceptions. *Where*
  the failure occurred is the separate closed `failure_category` field. See
  [§ cause_class semantics](#causeclass-semantics).
- **`cause_message`** — `str(exc)` of the underlying exception at wrap time. Empty
  string when the underlying exception carries no message.
- **`failed_handler_qualified_name`** — qualified name of the handler whose dispatch
  raised the underlying exception (e.g., `"conjured_npc.generate_dialogue"`). Always
  present: `PipelineFailure` is runtime-only; a dispatched handler always exists at
  failure time.
- **`failed_handler_position`** — the 0-indexed
  [dispatch-identity](#canonical-event-types/dispatch-identity) position of the failed
  handler's dispatch (hash-model owns the definition); always present alongside
  `failed_handler_qualified_name` (`PipelineFailure` is runtime-only; a dispatched
  handler always exists at failure time).
- **`bindings_snapshot`** — deep-copied projection of the compose-time binding values
  bound at the failed handler's pipeline entry: `bindings.<name>` values as resolved
  at compose time. Deep copy is mandatory; the snapshot must reflect the binding state
  at failure, not subsequent reads.
- **`reads_snapshot`** — deep-copied projection of the channel values projected into the
  failed handler's dispatch kwargs at failure time — the per-handler slice the failing
  handler was seeing (the reads-side counterpart to `bindings_snapshot`). Diagnostic
  payload for consumer logging, display, and debugging; explicitly NOT pipeline output
  for downstream composition — the output channel delivers nothing on halt. Deep copy
  is mandatory; same reason as `bindings_snapshot`. (The full run-level channel-state
  evolution is the event *trace*, not this snapshot.)
- **`pipeline_run_id`** — correlation identifier. Always present and non-null:
  `PipelineFailure` is runtime-only; no run in flight means no PF.
- **`composition_ref`** — pipeline name plus failed handler entry ordinal, format
  `"<pipeline_name>[<entry_ordinal>]"` (e.g., `"dialogue[3]"`). The location-bearing
  field for `PipelineFailure`; always present because runtime failure always has a
  known pipeline and entry ordinal.
- :::{region} pipelinefailure-payload/failure-category
  **`failure_category`** — the **closed** enum naming the engine's structural locus for the failure
  (where it occurred), set by the runner from **which internal scope raised** it, never inferred from
  the exception name. Exactly one of:
  - **`"service"`** — the failure escaped a service backend call: the `adapter.invoke` of a service
    handler's bound `services.<name>.invoke(...)`, or a [trainable](#trainable) composition node's
    engine-constructed `adapter.invoke`. Includes a service-binding timeout (the outbound call
    exceeding its transport timeout). `service_binding_name` is present.
  - **`"handler"`** — the failure escaped **consumer-authored code**: an author handler body (a
    transform, a hook, or a service handler's own body code, including code around its `invoke`
    call). `service_binding_name` is absent.
  - **`"engine"`** — the failure escaped the engine's own runner machinery: a run-guard (the consumer
    pipeline-level timeout) or an internal runner operation (binding delivery, channel routing, merge).
    Not attributable to a service backend or an author body. `service_binding_name` is absent.
  :::

  The category is the consumer's stable routing surface (service-down vs handler-author reporting vs
  run-limit / engine handling); the *specific* underlying failure is the open `cause_class`. The two are
  independent axes — the same `cause_class` (`"TimeoutError"`) occurs under `"service"` (a service-call
  timeout, with a binding) and `"engine"` (a pipeline-level timeout, no binding). For a nested
  `pipeline` embed, the outer failure carries the **inner** run's `failure_category`
  (attribution chain preserved, correlated by `parent_run_id`).
- **`message`** — auto-rendered human-readable stringification of the payload.

**Optional:**

- **`service_binding_name`** — service binding instance name; present **iff
  `failure_category = "service"`** — the runner attributes the failing binding from the service-bound
  node whose `adapter.invoke` raised — identifying it for consumer dispatch and remediation. Null for
  `"handler"` and `"engine"` categories (no failing binding).
- **`elapsed_ms_at_failure`** — milliseconds from pipeline start to failure. Present
  on standard engine dispatch paths; null when a consumer test harness constructs a
  `PipelineFailure` directly outside a timing context.

`PipelineFailure` carries no `audit_code`, `rule_id`, `remediation_hint`, `expected`,
`actual`, or `file_path` — absent by design, not by omission. Every `PipelineFailure`
maps to a single wrap audit entry (the generated [error-index](#error-index-codegen)
owns the code allocation) rather than a per-violation catalog entry; runtime failure has
no declaration-site contrast and no static-file location.

**The materializing copy is an exported surface — `conjured.errors.snapshot_copy`.** The
deep copy the two snapshot fields mandate is realized by one exported function the
engine's other retained-record surfaces (the canonical-event classes) and downstream
first-party consumers share: it **rebuilds the mutable container forms** (`dict` and
`MappingProxyType` → `dict`, `list` → `list`, `tuple` → tuple of copies, `set`/`frozenset`
→ `set`) and passes **non-container leaves by reference**. The leaf-by-reference half is
contract, not shortcut: an immutable leaf needs no copy for mutation-safety, and a blanket
`copy.deepcopy` would raise on a legitimately non-deep-copyable immutable leaf and
pre-empt the engine's own copy loci, mis-attributing the failure.

{#location-bearing-field-requirement}
### Location-bearing field requirement

Every error payload carries at least one location-bearing field identifying the
failure locus. The pattern is per-class:

- **`ContractViolation`** — requires at least one of `file_path` or `composition_ref`
  (or both). `file_path` carries the declaration-site artifact path (with optional
  `#L<line_number>` fragment when `line_number` is non-null); `composition_ref`
  carries the composition-level locus for cross-handler violations. Both absent is a
  runner-construction violation of this requirement — the runner never emits a
  location-less `ContractViolation`.
- **`SchemaValidationError`** — `handler_qualified_name` is the always-present
  location-bearing field (`handler_position` accompanies it as the unique dispatch
  identifier under multi-dispatch). Dispatch-time failure always attributes to exactly
  one handler; no file-path or composition disjunction applies.
- **`PipelineFailure`** — `composition_ref` is required and always present. Runtime
  failure always has a known pipeline and entry ordinal; no static-file location
  exists for the failure site.

---

{#causeclass-semantics}
## cause_class semantics

`cause_class` is a required field on `PipelineFailure`. It carries the underlying
Python exception class's `__name__` at wrap time — the string the runner records when
it wraps an uncaught exception at the dispatch boundary.

{#consumer-dispatch-role}
### Consumer dispatch role

Consumers route on the closed [`failure_category`](#pipelinefailure-payload/failure-category) for
*where* the failure occurred — `"service"` → a service-availability message, `"handler"` → report-to-handler-author, `"engine"` → a run-limit /
engine message — and read `cause_class` for the *specific* underlying exception within that locus (a
`"service"` failure carrying `"TimeoutError"` vs `"ConnectionError"` selects different remediation
prose). Both are matched without unwrapping the Python exception chain.

`cause_class` is an open string — the engine cannot enumerate consumer-domain exception taxonomies, so
it is never a bounded enum and ships no sentinel for unknown causes. Consumer dispatch matches
well-known cause names first with a fallback for unrecognized ones; the *category* axis, being closed,
is switched exhaustively.

{#why-one-class-with-discriminating-fields}
### Why one class with discriminating fields

`PipelineFailure` is a single class — runtime failures halt under identical semantics, so which one
occurred is carried by **fields**. The discrimination rides two fields: the closed
[`failure_category`](#pipelinefailure-payload/failure-category)
(the structural locus, exhaustively switchable) and the open
`cause_class` (the underlying exception type). One class, two discriminating axes; halt semantics are
identical across every value either can take. The error-channel explanation doc develops *why* the
discrimination lives in fields on one class.

{#failure-category-and-well-known-causes}
### Failure category and well-known causes

Each [`failure_category`](#pipelinefailure-payload/failure-category) value's locus is defined above; this
table maps it to `service_binding_name` presence and the representative open `cause_class` values seen
within it. The category is exhaustive and stable (a closed enum); `cause_class` is open.

| `failure_category` | `service_binding_name` | Representative `cause_class` |
|---|---|---|
| `"service"` | Present (the failing binding) | the backend's verbatim exception — `"TimeoutError"`, `"ConnectionError"`, any adapter-raised name |
| `"handler"` | Null | the handler's verbatim exception — `"ValueError"`, any consumer-domain name |
| `"engine"` | Null | `"TimeoutError"` (pipeline-level) |

`cause_class` is **not** a closed enum — consumer-authored handler bodies raise domain-specific
exceptions the engine never anticipates, captured verbatim by `type(exc).__name__`. Consumer dispatch
matches well-known cause names first with a fallback for unrecognized ones; the *category* axis, being
closed, is switched exhaustively. `failure_category` is an engine contract surface — adding or renaming
a member is an engine change consumers pinning dispatch depend on.

---

{#optional-field-serialization}
## Optional field serialization

Optional fields in the structured payload serialize as explicit `null` (not omitted)
on every surface except the HTTP wire projection. Consumers parsing the canonical
JSON, the `pipeline_error` event payload, or the CLI output can rely on every
declared optional field appearing in the serialized form — the field is always
present; its value is `null` when the field doesn't apply to the current violation.

The HTTP wire projection is the single exception: per RFC 9457's guidance on omitting
null extension members, the `application/problem+json` body omits null-valued optional
fields. See [§ RFC 9457 HTTP wire projection](#rfc-9457-http-wire-projection) for the
projection rules; the bullets in [§ Error payload field set](#error-payload-field-set)
name the per-field "null when..." condition consistently across every error class.

The rationale: null-include simplifies consumer parsing (every optional field has a
known position; presence is structurally predictable) at the cost of marginally larger
payloads. The HTTP surface inverts the trade-off because RFC 9457 consumers expect
omit-when-null per the spec's recommended convention.

---

{#rfc-9457-http-wire-projection}
## RFC 9457 HTTP wire projection

The engine's HTTP error-channel response body is shaped per **RFC 9457 (Problem
Details for HTTP APIs)**. When any of the three
[error classes](#error-class) halts a pipeline and the
engine surfaces that halt over HTTP, the response body carries an RFC 9457 envelope
with the error class's structured payload fields as declared extension members. The
`Content-Type` of every HTTP error response is `application/problem+json`. See
[derived rule R-error-channel-005](#error-channel-derived-rules).

{#scope-split}
### Scope-split

RFC 9457 applies **only to the HTTP error-channel surface**. The following surfaces
retain the canonical in-process shapes declared in their respective spec sections:

- Python exception class attribute names (`ContractViolation`,
  `SchemaValidationError`, `PipelineFailure`)
- In-process `__str__` rendering
- The `pipeline_error` [canonical event](#canonical-event)
  payload
- Structured log JSON
- CLI output

RFC 9457 is the wire-format **projection** at the HTTP boundary — not a replacement
for the canonical in-process form. The mechanical projection from the in-process
payload to the RFC 9457 envelope implements via a shared
`to_problem_details(payload, status_code)` helper in the engine's HTTP
error-response handler.

{#contractviolation-rfc-9457}
### ContractViolation → RFC 9457

Every [ContractViolation](#contractviolation) projects to
an RFC 9457 envelope as follows.

**Standard envelope members:**

| RFC 9457 member | Derived from |
|---|---|
| `type` | Always `about:blank` — RFC 9457's value for a problem with no specific type URI. The engine mints **no per-error web URI**: dispatch is on the `audit_code` extension member (verbatim, below), and error→docs resolution is **local** — `audit_code` / `rule_id` resolve against the docs shipped in the package, never against a web address |
| `title` | One-line audit-name summary from the audit catalog entry's name field; a generic `"Contract violation"` when `audit_code` is absent |
| `status` | HTTP status code, caller-supplied via `to_problem_details(payload, status_code)` — concrete selection rules are HTTP-transport territory |
| `detail` | Contrast prose `"expected: <expected>; actual: <actual>"` — `expected` and `actual` also appear as separate extension members so consumers can render diff views from structured fields |
| `instance` | `<file_path>#L<line_number>` when `line_number` is non-null; `<file_path>` when `line_number` is null; `<composition_ref>` for composition-level violations where `file_path` is null — the location-bearing-field-required rule carries through to `instance` |

**Extension members** (all fields not absorbed into standard envelope members):

| Member | Presence |
|---|---|
| `audit_code` | When non-null — present once the audit catalog assigns the code; omitted for a violation with no assigned code (see [§ ContractViolation with no assigned audit_code](#contractviolation-audit-code-absent)) |
| `rule_id` | Always |
| `expected` | Always |
| `actual` | Always |
| `section_path` | When non-null |
| `line_number` | When non-null (also captured in `instance` as `#L` fragment) |
| `composition_ref` | When non-null (also the primary source for `instance` when `file_path` is null) |
| `pipeline_run_id` | When non-null |
| `remediation_hint` | When non-null |

**Null-serialization divergence.** The HTTP wire-format omits null extension members
per RFC 9457 convention. The canonical in-process JSON form (event payload, structured
log JSON, CLI output) serializes absent optional fields as JSON `null` — that
convention is unchanged for non-HTTP surfaces.

**Worked example** (handler declaration `bindings.mood` unknown-key violation;
`status` value illustrative):

```json
{
  "type": "about:blank",
  "title": "Field discipline violation",
  "status": 400,
  "detail": "expected: declared key per the handler's declared grammar; actual: unknown key 'mod'",
  "instance": "handlers/npc_emotion.toml#L42",
  "audit_code": "C2.FIELD_DISCIPLINE.001",
  "rule_id": "R-handler-006",
  "expected": "declared key per the handler's declared grammar",
  "actual": "unknown key 'mod'",
  "section_path": "bindings.mood",
  "line_number": 42,
  "remediation_hint": "rename 'mod' to 'mode' or remove the key"
}
```

Note: `composition_ref` and `pipeline_run_id` are null in this load-time violation and
are omitted from the wire-format per the null-serialization divergence rule.

{#contractviolation-audit-code-absent}
### ContractViolation with no assigned audit_code

The audit-catalog allocation of `<CX>.<TOPIC>.<NNN>` codes is incremental: a violation whose
catalog entry is not assigned raises a [ContractViolation](#contractviolation) carrying
`audit_code = null` and dispatches on the symbolic `check` discriminator instead (the
[error-index](#error-index-codegen) tracks which codes are assigned). The RFC 9457 projection of
such a violation stays RFC 9457-conformant by taking the catalog-derived members from RFC 9457's
own defaults rather than fabricating them:

- `title` is the generic `"Contract violation"` (no catalog entry supplies a name).
- The `audit_code` extension member is **omitted** (the null-serialization divergence — a null
  extension member is dropped on the wire).

`type` is `about:blank` in this case as in every case — the projection's `type` never varies.
Every other member projects unchanged: `status`, `detail` (the `expected` / `actual` contrast),
`instance`, `rule_id`, `expected`, and `actual` all render, so the violation stays fully
diagnosable — the failure is never masked, only its catalog-derived `title` and `audit_code` are
absent. This is the sanctioned projection for an audit_code-absent `ContractViolation`: it is
RFC 9457 conformance, not an engine-specific shape, so it remains correct unchanged for a
violation that carries a real `audit_code` once one is assigned.

**Worked example** (the API-boundary missing-input `ContractViolation`, whose catalog code is not
yet assigned; `status` is caller-supplied — `400` here, the API-boundary case):

```json
{
  "type": "about:blank",
  "title": "Contract violation",
  "status": 400,
  "detail": "expected: declared input 'session_id'; actual: 'session_id' absent",
  "instance": "mypkg.dialogue_npc",
  "rule_id": "R-pipeline-001",
  "expected": "declared input 'session_id'",
  "actual": "'session_id' absent"
}
```

{#contractviolationgroup-rfc-9457}
### ContractViolationGroup → RFC 9457

A [ContractViolationGroup](#contractviolationgroup) projects to a single RFC 9457 envelope
that carries its member violations as an array — the same shape
[SchemaValidationError](#schemavalidationerror) uses for `field_validations`, lifted to the
violation grain.

**Standard envelope members:**

| RFC 9457 member | Derived from |
|---|---|
| `type` | `about:blank`, as for every class. A group has no audit-catalog entry of its own (it is a container; each member violation keeps its own `audit_code`) |
| `title` | The generic `"Multiple contract violations"` |
| `status` | HTTP status code, caller-supplied via `to_problem_details(payload, status_code)` — as for the other classes |
| `detail` | `"<n> contract violations: "` followed by the member violations' `expected … ; actual …` contrasts joined — the count plus each member's contrast prose |
| `instance` | The compose locus shared by the members — taken from a member's `file_path` / `composition_ref` (the group's violations share one composition-validation locus); the per-member loci also ride inside the `violations` array entries |

**Extension members:**

| Member | Presence |
|---|---|
| `violations` | Always — a non-empty array of **two or more** entries, each the member [ContractViolation](#contractviolation)'s own RFC 9457 problem object (the [§ ContractViolation → RFC 9457](#contractviolation-rfc-9457) envelope, verbatim), so every aggregated violation is fully diagnosable from the group envelope |

**Null-serialization divergence.** Same as the other classes — the HTTP projection omits
null extension members; each member problem object inside `violations` is itself projected
under that same rule.

{#schemavalidationerror-rfc-9457}
### SchemaValidationError → RFC 9457

Every [SchemaValidationError](#schemavalidationerror)
projects to an RFC 9457 envelope as follows.

**Standard envelope members:**

| RFC 9457 member | Derived from |
|---|---|
| `type` | Always `about:blank`, as for every class — no per-error web URI; SVE is audit-catalog-keyed and dispatch is on the `audit_code` extension member (always present for SVE), resolved locally against the shipped docs |
| `title` | `"Schema validation failed — <handler_qualified_name>"` |
| `status` | `502 Bad Gateway` — the engine is the gateway between caller and handler, and a SchemaValidationError means a declared schema failed at that engine↔handler boundary: either the handler's returned response (output validation) or the values projected into its dispatch (input-projection validation), the two boundaries [§ SchemaValidationError payload](#schemavalidationerror-payload) distinguishes by `audit_code` |
| `detail` | Joined per-field validation messages derived from `field_validations[].message` entries |
| `instance` | `<handler_qualified_name>?run=<pipeline_run_id>&position=<handler_position>` — handler-identity URI with run correlation; the colon-free `<pipeline_run_id>` ([§ Error payload field set](#error-payload-field-set)) rides the query verbatim; the `position` query parameter makes the instance URI unique under multi-dispatch (the qualified name alone is not) |

**Extension members:**

| Member | Presence |
|---|---|
| `audit_code` | Always |
| `rule_id` | Always |
| `schema_source` | Always |
| `pipeline_run_id` | Always (SVE is runtime-only; a pipeline run always exists) |
| `field_validations` | Always, non-empty — full `FieldValidationDetail` array preserved verbatim |

**Null-serialization divergence.** Same as ContractViolation — HTTP projection omits
null extension members. The only nullable-by-design field within `field_validations`
is `actual_value` per `FieldValidationDetail`; absent `actual_value` entries are
omitted in the wire-format.

**Worked example** (handler `conjured_npc.npc_emotion` produced 2 invalid output
fields; the nested `label` member declares `str` carrying an `enum` validation keyword,
so its failure is the constraint-layer enum case):

```json
{
  "type": "about:blank",
  "title": "Schema validation failed — conjured_npc.npc_emotion",
  "status": 502,
  "detail": "Input should be a valid integer; expected one of [happy, sad, angry], got 'confused'",
  "instance": "conjured_npc.npc_emotion?run=run_20260506T142311Z_a3f9&position=2",
  "audit_code": "C1.HALT_ON_SCHEMA_VALIDATION_ERROR.001",
  "rule_id": "R-error-channel-003",
  "schema_source": "handlers/npc_emotion.toml",
  "pipeline_run_id": "run_20260506T142311Z_a3f9",
  "field_validations": [
    {
      "field_path": "output_schema.mood.intensity",
      "expected_type": "int",
      "actual_type": "str",
      "actual_value": "'high'",
      "constraint_violated": "type",
      "message": "Input should be a valid integer"
    },
    {
      "field_path": "output_schema.mood.label",
      "expected_type": "str",
      "actual_type": "str",
      "actual_value": "'confused'",
      "constraint_violated": "enum",
      "message": "expected one of [happy, sad, angry], got 'confused'"
    }
  ]
}
```

{#pipelinefailure-rfc-9457}
### PipelineFailure → RFC 9457

Every [PipelineFailure](#pipelinefailure) projects to an
RFC 9457 envelope as follows.

**Standard envelope members:**

| RFC 9457 member | Derived from |
|---|---|
| `type` | Always `about:blank`, as for every class — no per-error web URI; `PipelineFailure` is not audit-catalog-keyed per-instance, and dispatch is on the `cause_class` extension member (verbatim, below) |
| `title` | `"Pipeline failure — <cause_class>"` |
| `status` | `504 Gateway Timeout` when `cause_class = "TimeoutError"`; else `502 Bad Gateway` when `failure_category = "service"`; else `500 Internal Server Error` (default). The per-deployment status-code-override mechanism is HTTP-transport territory |
| `detail` | The `cause_message` value verbatim; `cause_message` also appears as an extension member for consumers parsing the structured form independently |
| `instance` | `<composition_ref>` (e.g., `"dialogue[3]"`) — always present; `PipelineFailure`'s location is always composition-shaped |

**Extension members:**

| Member | Presence |
|---|---|
| `failure_category` | Always |
| `cause_class` | Always |
| `cause_message` | Always |
| `failed_handler_qualified_name` | Always |
| `failed_handler_position` | Always |
| `pipeline_run_id` | Always (PF is runtime-only; run always exists) |
| `bindings_snapshot` | Always (verbatim object) |
| `reads_snapshot` | Always (verbatim object) |
| `service_binding_name` | When non-null |
| `elapsed_ms_at_failure` | When non-null (integer) |

**Null-serialization divergence.** Same as ContractViolation — HTTP projection omits
null extension members. Consumer-attached HTTP middleware handles per-deployment
redaction of PII / credentials in `bindings_snapshot` and `reads_snapshot`.

**Worked example** (LLM service-binding timeout from
`conjured_npc.generate_dialogue`):

```json
{
  "type": "about:blank",
  "title": "Pipeline failure — TimeoutError",
  "status": 504,
  "detail": "Service binding 'llm_main' exceeded timeout_ms=30000",
  "instance": "dialogue[3]",
  "failure_category": "service",
  "cause_class": "TimeoutError",
  "cause_message": "Service binding 'llm_main' exceeded timeout_ms=30000",
  "failed_handler_qualified_name": "conjured_npc.generate_dialogue",
  "failed_handler_position": 4,
  "pipeline_run_id": "run_20260506T142311Z_a3f9",
  "bindings_snapshot": {
    "temperature": 0.7,
    "max_tokens": 256,
    "system_prompt_id": "npc_combat_taunt_v3"
  },
  "reads_snapshot": {
    "npc_emotion": {"mood": "angry", "intensity": 0.8},
    "scene_context": {"location": "tavern", "time_of_day": "evening"}
  },
  "service_binding_name": "llm_main",
  "elapsed_ms_at_failure": 30142
}
```

---

{#error-index-codegen}
## Error-index codegen

:::{region} error-index-codegen/kernel
The engine's error classes carry an `audit_code` on every raised instance — a stable,
grep-able identifier encoding which derived rule the error enforces. Two generated
artifacts turn that identifier into a navigable reference: **`error-index.md`**
(rendered cross-reference table, consumer-facing) and **`error-classes.toml`**
(machine-readable companion, agent-facing). Both are generated from the engine's
error registration API by `tools/gen_error_index.py`; each carries a first-line
generated-content marker embedding a hash of its own body, and the generator's
`--check` mode re-derives both artifacts from current engine source in memory
and fails on any divergence — a manual edit and a stale commit are caught the
same way (the check is pinned in the engine's test suite).
:::

{#error-indexmd-consumer-facing-diagnostic-cross-reference}
### `error-index.md` — consumer-facing diagnostic cross-reference

**Path:** `docs/reference/error-index.md`

`error-index.md` is a rendered cross-reference table mapping every `audit_code` the
engine can emit to the derived rule it enforces, that rule's statement, and the
remediation path. An integrator hitting
[`ContractViolation`](#contractviolation) or
[`SchemaValidationError`](#schemavalidationerror) with a
specific `audit_code` looks up that ID here to learn which contract was violated and
how to fix it.

`error-index.md` lives in `docs/reference/` — not inside any single component folder
— because it aggregates `audit_code`s emitted across all engine components. The index
is a cross-component projection over the engine's full `audit_code` corpus; component
locality would misrepresent its scope. It is generated content; the engine-tree
source of truth is the error registration API in `src/conjured/`
(`conjured.errors`), which every raise site constructs through.

{#error-classestoml-machine-readable-agent-surface-companion}
### `error-classes.toml` — machine-readable agent-surface companion

**Path:** `src/conjured/agent/error-classes.toml`

`error-classes.toml` is the structured-data form of the same `audit_code` → rule
mapping — the Tenet 2 structured-data-alongside-prose companion to `error-index.md`.
Where `error-index.md` serves a human or LLM reading a rendered table,
`error-classes.toml` serves agent-side tooling that wants to programmatically resolve
`audit_code` → rule + remediation without parsing prose.

`error-classes.toml` ships inside the wheel's agent surface (`src/conjured/agent/`),
accessible via `importlib.resources.files("conjured.agent")`. An agent embedded in an
integrator environment can resolve `audit_code`s programmatically without a network call
or filesystem path to the rendered docs.

**Record shape.** Each record keys an `audit_code` to the derived `rule_id` it enforces,
that rule's statement, the error class that raises it, and the remediation path — the
structured mirror of an `error-index.md` row.

{#how-the-generator-produces-both-artifacts}
### How the generator produces both artifacts

`tools/gen_error_index.py` derives both artifacts from the engine's **error
registration API** — the registries in `conjured.errors`, whose error
constructors reject an unregistered `audit_code` and an unregistered
check/rule pairing, so the registered set is complete over the engine's
raisable surface by construction. The generator:

1. **Reads the registered set** from the registration API — no AST walk over
   raise sites; the registries are the single source the constructors enforce.
2. **Resolves each registered entry** against the derived-rule corpus (the
   `rule_id`s across the component references), joining each entry to the rule
   it enforces, that rule's statement, and the owning reference — the
   remediation path.
3. **Emits both artifacts**, prepending a generated-content marker on line 1 of
   each file embedding a hash of the file's body.

Freshness is enforced by re-derivation: `gen_error_index.py --check` rebuilds
both artifacts in memory from current engine source and compares them against
the committed files, failing on any divergence — so a hand-edited artifact and
a registry change that was never re-generated surface identically. The check is
pinned in the engine's test suite; a stale artifact cannot land green.

---

{#error-channel-derived-rules}
## Derived rules

:::{transclude} derived-rules-convention/kernel
:::

```yaml
rules:
  - rule_id: R-error-channel-001
    name: closed-enum error classes
    derived_from: [I1]
    enforcement: mechanical
    statement: |
      :::{transclude} error-classes/kernel
      :::

      :::{transclude} R-error-channel-001/key-set-routing
      :::

  - rule_id: R-error-channel-002
    name: no engine retry API
    derived_from: [I3, I4]
    enforcement: mechanical
    statement: |
      The engine exposes no retry surface. The runner has no max_retries
      field, no engine-declared retry count, no retry wrapper between the
      engine-constructed dispatch callable and the service-type adapter.
      There is no configuration path through which a consumer can request
      engine-level retry; the prohibition is enforced by absence-of-API
      rather than by runtime check.

      Transport-level retry — triggered by a transient transport fault before
      a usable response exists, the resend carrying the same payload — is
      impl-internal and sanctioned: the captured service_invocation event
      reflects a single semantic call and channel-record correspondence is
      preserved. Semantic retry inside handler bodies — a re-call triggered by
      a verdict on the response (critique-and-revise, validation-and-retry),
      including a verdict-driven resend of identical bytes — breaks
      channel-record correspondence: multiple distinct service calls generate
      multiple captured events against one channel-write, corrupting the
      training projection. The runner cannot inspect handler bodies, so this
      prohibition is governed by R-handler-002 (no silent fallbacks;
      review-enforced) rather than by this rule. Re-invocation of the same
      external call at the consumer layer — whether with modified inputs or a
      verdict-driven resend — is consumer multi-pipeline orchestration, not
      engine concern.

  - rule_id: R-error-channel-003
    name: halt semantics
    derived_from: [I1, I4]
    enforcement: mechanical
    statement: |
      Any error class halts channel-writing nodes — transform, service,
      and trainable composition dispatches.
      Hooks have a bounded two-case rule: operational PipelineFailure
      (network unreachable, remote 5xx, timeout) is caught by the
      runner's hook wrapper and execution continues; ContractViolation
      and SchemaValidationError from a hook still halt.

      The wrapper sanction applies to hooks specifically because their
      kind admits no outgoing channels — hooks return None and the
      runner has no merge path for a hook return. Operational failure
      in a hook loses a side-effect record but cannot corrupt downstream
      handler inputs, because no downstream node reads channels from a
      hook. Channel-writing nodes halt on every error class because
      they write to channels; a swallowed failure would produce a
      channel value the runner cannot distinguish from a successful one,
      propagating corruption downstream.

      The wrapper sanction is a property of the hook kind (its graph
      position admits no outgoing channels), not a per-handler except;
      in-body except in any handler body is itself a violation.

  - rule_id: R-error-channel-004
    name: channel separation
    derived_from: [I1, I3]
    enforcement: mechanical
    statement: |
      The engine surfaces pipeline invocations through two distinct API
      channels — the output channel and the error channel — separated
      by construction, not by convention.

      On happy-path completion, the runner returns the run's result — the
      declared outputs fields being the committed consumer surface within
      the full outer-channel state (RunResult). On halt, the runner raises
      the error class directly: no partial channel values are returned as
      pipeline output. The output channel is silent on halt.

      The error channel carries each class's declared payload field set
      (error class and message on every class; per-class locus fields; the
      bindings and reads snapshots on runtime failures). The reads snapshot is
      a diagnostic payload for consumer logging and debugging — not
      pipeline output for downstream composition.

      No success/ok/status discriminated-union field exists on the
      runner's return value. A returned value means success; a raised
      exception means halt; the consumer dispatches on which it receives.

  - rule_id: R-error-channel-005
    name: RFC 9457 HTTP wire projection
    derived_from: [T2]
    enforcement: mechanical
    statement: |
      The engine's HTTP error-channel response body is shaped per RFC 9457
      (Problem Details for HTTP APIs). The Content-Type of every HTTP
      error response is application/problem+json.

      RFC 9457 applies only to the HTTP error-channel surface. Python
      exception class attribute names, in-process __str__ rendering, the
      pipeline_error canonical event payload, structured log JSON, and
      CLI output retain the canonical in-process shapes.

      The five standard RFC 9457 envelope members (type, title, status,
      detail, instance) are populated per error class as specified in
      error-channel/reference.md § RFC 9457 HTTP wire projection. All
      in-process payload fields not absorbed into envelope members are
      preserved verbatim as RFC 9457 extension members.
```
