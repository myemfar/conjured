---
kind: explanation
audience: [authors, integrators]
slug: error-channel-explanation
explains: ../components/error-channel/reference.md
---

{#error-channel-explanation}
# Why the error channel is shaped this way

The [error-channel reference](#error-channel-reference) states the
closed enum of error classes, the halt semantics, the payload field sets, and the
RFC 9457 projection. This doc carries the *why* behind three design choices that
each could plausibly have gone another way: why runtime failure is **one** class
with discriminating fields rather than several named classes, why the error
surface is a **flat** payload rather than a nested exception hierarchy, and why
**retry is the caller's job** rather than an engine affordance.

{#why-runtime-failure-is-one-class-with-discriminating-fields}
## Why runtime failure is one class with discriminating fields

Runtime failure surfaces as a single class, `PipelineFailure`, with the kind of failure
carried in **fields**. Every runtime failure halts the pipeline under identical
semantics: no retry primitive, no fallback path, the output channel goes silent, and the
consumer receives the error channel to decide the user-facing response. Because the
engine's halt behavior is uniform, the distinctions a consumer acts on are *values on the
one class*, read from fields — and the public error vocabulary stays at the
closed top-level classes.

`PipelineFailure` carries two such fields, for the two things a consumer varies its
handling by: *where* the failure arose and *what* threw. The closed `failure_category`
names the structural locus, which the engine knows from the scope that raised the
failure (never inferred from the exception name), so a consumer switches it
exhaustively — its members and their loci are owned by the error-channel reference's
[`failure_category` region](#pipelinefailure-payload/failure-category). The open
`cause_class` carries the underlying exception's type verbatim, because the engine cannot
enumerate the domain-specific exceptions consumer handler bodies raise.

The two axes are genuinely independent — a single field could not carry both. The same
`cause_class` `"TimeoutError"` arises under `failure_category = "service"` (a service-call
timeout, with a failing binding) and under `"engine"` (a pipeline-level timeout, no
binding). The locus is not recoverable from the exception name; only the engine's
structural position knows it, which is why it is the engine's to report as a closed field.

This is the closed-vocabulary discipline of the error enum applied one level down. Each
top-level class names a mechanically distinct origin in the type system — structural
type-check, value-level type-check, runtime. Within runtime, the loci share identical
halt semantics, so they live as a closed field on one class.

{#why-a-flat-shape-not-a-nested-exception-hierarchy}
## Why a flat shape, not a nested exception hierarchy

The natural Python instinct is an inheritance tree —
`PipelineError` → `ServiceError` → `LLMServiceError`, dispatched by `except`
hierarchy. The error channel deliberately does not do this, for two reasons rooted in
what the error has to cross.

First, the error surface is a **wire** surface, not just an in-process one. It
projects to a `pipeline_error` canonical event, to structured log JSON, to CLI
output, and to an RFC 9457 HTTP envelope for consumers in any language. An exception
inheritance hierarchy is a Python-runtime construct that does not survive any of those
projections — a consumer reading the RFC 9457 body over HTTP has no `isinstance`
tree, only fields. A flat payload with an explicit class field and a `cause_class`
field projects cleanly to every surface; a hierarchy would have to be flattened at
each boundary anyway, so the flat shape is the honest canonical form.

Second, a flat shape with explicit fields is what makes the payload **dispatchable by
agents and tooling without executing Python**. The `audit_code` and `rule_id` fields
are stable string keys an agent routes on to select remediation prose; the per-field
`field_validations` array is structured data a tool renders without parsing a
traceback. A nested hierarchy hides the dispatch keys inside type identity, which is
exactly the form an agent or a non-Python consumer cannot read. The flat shape is the
Tenet-2 (legibility to agents) choice at the error surface: the discriminating
information lives in named fields, where every reader — human, agent, or
cross-language consumer — can reach it the same way.

The per-class field-set divergence (ContractViolation carries TOML-locus fields,
SchemaValidationError carries `field_validations`, PipelineFailure carries cause +
snapshots) is not a hierarchy in disguise — it is three flat shapes, each sized to
its failure context, sharing the fields every error needs. No class inherits
another's fields; each is a complete declared payload.

{#why-retry-is-the-callers-job-not-an-engine-affordance}
## Why retry is the caller's job, not an engine affordance

The engine ships no retry surface — no `max_retries`, no backoff config, no
retry wrapper. This looks like a missing feature until you trace what an engine-level
retry would do to the training projection, which is the property the whole engine
exists to keep trustworthy.

The distinction that matters is **transport retry vs semantic retry**. Transport
retry — resend the *same* payload after a connection reset or a 5xx — is fine, and the
engine permits it *inside* the service-type implementation: the adapter captures the
`service_invocation` event for the final resolved call, one semantic interaction is
recorded, and
[channel-record correspondence](#channel-record-correspondence)
holds. Semantic retry — call, evaluate the result, then call *again* because of a verdict on
that result (critique-and-revise, validation-and-retry — and a resend of identical bytes
after judging the reply empty or malformed counts too) — is the dangerous one: it
produces multiple distinct external interactions under one handler dispatch, so
multiple `service_invocation` events map to the single channel-write the handler
emits at exit. The per-dispatch bijection breaks, and the training corpus can no
longer tell which attempt is the authoritative record for that channel value.

So an engine-level retry affordance would be an engine-blessed way to break
channel-record correspondence — the engine handing authors a footgun aimed at the
exact invariant it is built to protect.
:::{transclude} no-engine-retry/payload-predicate
:::
That predicate cleanly separates the safe case (transport, pushed down into the adapter
where it's invisible to the projection) from the unsafe case (semantic, which must not
happen inside one dispatch).

That leaves the legitimate need — "I want to retry with a better prompt" — which is
real, and the answer is that it is **consumer multi-pipeline orchestration, not an
engine concern**, per [I3](#invariants-and-derived-rules).
Each re-invocation — with modified inputs, or a verdict-driven resend of the same
inputs — is its own pipeline run, with its own
`service_invocation` event, its own channel-record, and its own position in the
training corpus. Done at the consumer layer, retry-with-modification preserves
channel-record correspondence by construction — each attempt is a clean, separately
captured run. The engine doesn't *withhold* retry; it locates retry at the layer where
retry doesn't corrupt the projection. The absence of a retry API is the structural
expression of that boundary: there is no engine surface for cross-run orchestration
because cross-run orchestration is, correctly, the caller's job.
