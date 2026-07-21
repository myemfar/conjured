---
kind: explanation
audience: [authors, integrators]
slug: pipeline-as-training-contract
explains: ../reference/principles.md#invariants-and-derived-rules
---

{#pipeline-as-training-contract-explanation}
# Pipeline-as-training-contract
The engine is a **typed dataflow language**. A
[pipeline](#pipeline) is a composition graph of typed nodes
(handlers) and typed channels (state reads and writes between them). Every channel
carries a declared type; the composition is valid only when channel types agree at
every junction. The engine is the type-checker and dispatch runtime for that graph.

[I4](#invariants-and-derived-rules) names what this type
system does that is novel: **the training corpus is a derived view of the graph.**
Each channel emerging from a [trainable](#trainable)
composition node's `output_schema` — a
[trainable channel](#trainable-channel) — projects to
training records whose shape is literally the channel type at that position in the
graph. The training contract is not a separate artifact that happens to agree with
the runtime contract; it is one of several queries the engine answers about the
same graph.

This page foregrounds I4 — the axiom itself is owned by
[principles](#invariants-and-derived-rules); here we
develop the queryable-graph view it presupposes and why every other invariant
exists to make this one query mechanically trustworthy. A derived view whose source
graph is "whatever the runtime happens to do" cannot be audited; the rest of the
engine's discipline is what makes the graph a trustworthy source.

---

{#the-graph-and-its-queries}
## The graph and its queries

The composition graph holds:

- **Nodes** — handlers (the closed-enum [handler kinds](#handler-kind)) and
  composition nodes (the [composition TOML](#composition-toml)'s
  kind specializations), with declared interfaces (reads, writes, services) and annotations.
- **Channels** — typed edges connecting one node's writes to another node's reads.
- **Bindings** — `bindings.<name>` declarations (compose-time values, supplied
  inline or by external declaration file path) and service-type bindings (backend
  adapters) that parameterize nodes.

From this single artifact the engine answers several queries:

| Query | What it returns |
|---|---|
| Runtime contract | Per-node input/output validators, dispatched in declared order |
| [Pipeline-hash](#pipeline-hash) | Identity of the full composition graph |
| [Training-bundle-hash](#training-bundle-hash) (per trainable) | Identity of the trainable composition's declaration — the training-record-shape identity for the channels it emits |
| Training-record shape (per trainable channel) | The type at that channel position |
| Composition validity | Whether all channel types agree, every binding resolves, every declared field constraint is honored |

The conventional "problem of two contracts" — a runtime contract that drifts away
from a separately-authored training schema — is dissolved by this view. There are
not two contracts that happen to be the same; there is one graph, and
runtime-contract and training-shape are two queries against it. Editing one node's
interface changes the graph; every query re-derives. The drift cannot occur because
there is nothing to drift *from* — the two views never had independent existence.

---

{#what-the-integrator-gets}
## What the integrator gets

An integrator who has finalized a pipeline can:

- Compute the [pipeline-hash](#pipeline-hash) and
  per-trainable [training-bundle-hashes](#training-bundle-hash)
  with no additional configuration.
- Read the training-record shape directly off the graph at each trainable channel.
- Extract the [pipeline derivables](#pipeline-derivables)
  bundle — schema definitions, training-bundle-hashes, pipeline-fixed binding
  snapshot, service metadata, and composition snapshot — and supply it to an
  external generator to produce a conformant training corpus.
- Train an artifact whose sidecar manifest records the hashes; the engine surfaces
  drift via canonical events at every load, and — when the deployment opts in via
  [integrity enforcement](#integrity-enforcement-opt-in) — halts on
  training-bundle-hash mismatch unless the drift class is explicitly acknowledged.

No external schema work. No separate training-data schema authoring step. The
composition *is* the source of truth; training records are its projection.

One further payout follows from the same collapse: **a captured run's channel
records are simultaneously its training records, its replay records, and its
test fixtures — one artifact, three roles.** Every seam in the graph is a
declared, typed channel (handlers never call each other; nodes meet only at
channels), so the per-dispatch snapshots the canonical event stream captures at
those seams are already the middle-state artifacts a test would otherwise
hand-write: what entered a node and the validated value it produced, at the
declared type the composition guarantees.
[Channel-record correspondence](#channel-record-correspondence) is what makes
the captured set exhaustive for its seams;
[replayability](#replayability) is what makes re-running against the captured
values meaningful. A consumer extracting training pairs, replaying a run, or
asserting a handler against a seam value is reading the same captured artifact
three ways — there is no separately-authored fixture corpus to drift from the
contract, for the same reason there is no separately-authored training-data
schema. (How a test suite harvests, stores, and loads such fixtures is
test-tooling territory, downstream of the engine.)

---

{#closest-field-named-neighbor-openapi-as-graph-with-derived-views}
## Closest field-named neighbor: OpenAPI as graph-with-derived-views

OpenAPI is the field's closest analog. An OpenAPI specification is a graph of
operations and types; the runtime API surface and generated client SDKs are both
*views* of that graph. The runtime contract validates requests against the schema;
the SDK generator projects the same schema into language-specific bindings. One
source artifact, multiple derived views, machine-readable agreement between them.
The pattern works because the graph is declarative and tooling treats it as
authoritative.

Conjured generalizes the pattern from "API graph → runtime + SDK" to "composition
graph → runtime + training-record shape." The engine does not ship a codegen tool;
it ships the *property* — a composition that loads is a composition whose runtime
view and training-shape view are guaranteed to agree, because they are projections
of the same graph.

What Conjured adds beyond OpenAPI:

- **Graph composition.** OpenAPI describes one API surface as a flat schema set.
  Conjured's graph carries node ordering, service bindings, and compose-time
  bindings; the composition itself is the artifact, not just the set of types.
- **Two-hash integrity.** The engine records the hash of the full composition
  (pipeline-hash) and a per-trainable hash covering each trainable composition's
  declaration (training-bundle-hash). A trained artifact's manifest tracks both;
  the engine surfaces drift on every load. See [hash-model](#architecture-hash-model) for the
  full two-hash spec.
- **Kind-typed trainability.** The [trainable](#trainable)
  composition kind is the structural locus of training capture — a trainable
  composition node is body-less and engine-constructed:

  :::{transclude} trainable-channel/emission-locus
  :::

  Trainability is a property of the composition kind, not a flag on the bound
  service-type.

---

{#mechanical-consequences-how-the-other-invariants-protect-the-query}
## Mechanical consequences — how the other invariants protect the query

The queryable-graph property is only as trustworthy as the graph it queries. The
other [invariants](#invariants-and-derived-rules) each
close a way the graph could stop being a faithful source:

- **[I1 (no implicit contracts)](#I1).** A field that does not appear in any node's
  declared write cannot reach any channel; a trainable channel cannot carry a type the
  corpus projection does not reflect. The graph is the corpus's catalog — I1 is what
  makes it *complete*.
- **[I2 (determinism under composition)](#I2).** A pipeline that loads has already passed
  compose-time type-checking, so its graph is internally consistent before any record is
  captured.
  :::{transclude} R-pipeline-002/merge-kernel
  :::
  I2 is what makes the graph *consistent*.
- **[I3 (engine purity)](#I3).** Operations on the graph's *outputs* — persistence,
  retraining cadence, behavioral evaluation — are consumer territory, downstream of the
  engine. I3 is what keeps the graph *the* artifact rather than one input among many.

The rules the engine cannot mechanically enforce — semantic retry inside handler
bodies, silent in-body fallback, hidden writes that bypass declared channels — are
exactly the rules that would corrupt the graph from inside opaque nodes. They carry
`enforcement: review` per [enforcement-modes](#architecture-enforcement-modes); adversarial
review catches them at library publishing. That the un-mechanizable rules are
precisely the graph-corrupting ones is not a coincidence: it is the boundary of
what a type-checker can see, which is why the review mode exists alongside the
mechanical one.

{#channel-record-correspondence-rationale}
### Channel-record correspondence

The "queries against one graph" framing depends on a load-bearing bijection,
[channel-record correspondence](#channel-record-correspondence):

:::{transclude} channel-record-correspondence/bijection
:::

Without it the training projection would not be well-defined:
multiple backend calls collapsing into one captured record (semantic retry), or one
backend call producing a record that doesn't match the channel-write the handler
returned (silent in-body fallback), would each break the bijection at service-kind
handler dispatches. For trainable composition node dispatches the bijection is
preserved by construction (engine-constructed dispatch; no body to break it).

This is where the *why* lives; the **full event-model spec is owned by
[hash-model](#architecture-hash-model)** — payload shapes, per-kind emission rules, and
pair-event semantics. The architectural point for this page is only that the
bijection is what makes the projection well-defined, and that it holds by two
different mechanisms (atomicity + adapter-boundary capture for service-kind;
engine-construction for trainable composition nodes). See
[hash-model § Channel-record correspondence](#channel-record-correspondence-by-kind)
for the mechanism and
[enforcement-modes § Layered defense](#layered-defense) for the
service-kind-scoped silent-fallback second layer.

---

{#what-i4-does-not-guarantee}
## What I4 does NOT guarantee

I4 governs **graph-shape integrity of the training projection**, not behavioral
equivalence of artifacts trained from it.

- A trained artifact's behavior is a property of the data it saw, not a property of
  the graph. A LoRA trained against composition A may behave differently when loaded
  against composition B; I4 promises the shapes match at training time, not
  behavioral equivalence across graph edits. The [hash-model](#architecture-hash-model)
  surfaces drift via canonical events; the deployment's
  [integrity enforcement](#integrity-enforcement-opt-in) opt-in
  determines halt vs emit on mismatch.
- A single training record lost to transient I/O failure is statistical noise; I4
  does not promise per-record durability. Exporting *wrongly-shaped* records is
  contract corruption; I4 promises every captured record matches the projection.
- Behavioral evaluation (does the model produce useful outputs?) is consumer
  territory. I4 promises the projection shape; the consumer's eval methodology
  decides whether the shape captured the right thing.

These are not gaps to be closed later — they are the deliberate edge of the claim.
I4 is a claim about *shape*, and keeping it precisely that keeps it mechanically
checkable; widening it to behavior would trade a guaranteed property for an
aspirational one.

---

{#training-contract-where-this-lives-in-the-engine}
## Where this lives in the engine

The [pipeline](#pipeline) component owns the graph itself:
composition, the type-checker, the
[pipeline-hash](#pipeline-hash) and per-trainable
[training-bundle-hashes](#training-bundle-hash),
trained-artifact sidecar manifests, drift surface via canonical events, halt on
mismatch under integrity enforcement. Type-checking and hash construction operate
over the engine's **Pydantic intermediate representation** of declared schemas, not
over TOML lexical form (see
[overview § Pydantic as the canonical representation](#pydantic-as-the-canonical-representation));
TOML is one dialect over the canonical IR, and future dialects flow through 1×N
converters to the same canonical form.

The [handler](#handler) component owns node-interface
declarations and the literal-equal rule that ties a trainable channel's declared
type to its backend's structured-output constraint.

The [error-channel](#glossary-error-channel) component owns the
type-check failure taxonomy — [ContractViolation](#contractviolation)
and [SchemaValidationError](#schemavalidationerror).
