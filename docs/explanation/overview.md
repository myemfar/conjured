---
kind: explanation
audience: [authors, integrators]
slug: architecture-overview
explains: ../reference/principles.md#invariants-and-derived-rules
---

{#architecture-overview}
# Architecture overview

Conjured is a Python engine for handler composition. A composed pipeline is a
**typed dataflow [graph](#graph)** — handlers as nodes,
state reads and writes as typed channels between them; the engine type-checks the
graph at compose time and dispatches handlers in declared order at runtime. The
engine does **pipeline-as-training-contract derivation**
([I4](#invariants-and-derived-rules)): the training
corpus is a derived view of the graph. The schemas validating channels at runtime
are the same types defining training-record shapes — not because two contracts are
kept in sync, but because they are queries against one graph. This
collapse-by-construction is the engine's load-bearing structural choice;
most other choices map to field-named patterns.

Conjured is for developers shipping fine-tuned local models inside real
products — where production must run exactly the pipeline the model was
trained on. A captured run is simultaneously training record, replay record,
and test fixture; the pipeline is the contract the model is trained *to*, and
the engine's guarantees exist to keep that contract trustworthy.

This page is the landing-page structural map for the architecture corpus — the
orientation a new reader reads first. The
[glossary](#glossary) defines vocabulary; the
[principles](#principles) carry the engine's axioms; the per-concept
architecture references carry the mechanics this page points to.

---

{#the-three-contract-components}
## The three contract components

The engine's **type-system contract** factors into three components, each with its
own canonical reference:
[handler](#handler) (node-level concerns — declared
interface, the closed-enum [handler kinds](#handler-kind)),
[error-channel](#glossary-error-channel) (failure-mode concerns —
the [error classes](#error-class), halt semantics, the
hook-error wrapper), and [pipeline](#pipeline) (graph-level
concerns — composition, compose-time type-checking, the two-hash scheme, dispatch
order, merge resolution, training-contract derivation). These three partition the
*type-system* contract exhaustively — node-level, failure-mode, graph-level — but
they are not the engine's whole component set: its other component references own
the surfaces outside the type-system contract (service-type transport, deployment,
the server wire surface, the testing API, and the native-library catalog among
them).

This three-way *contract* decomposition is distinct from the C4 *implementation*
factoring, which is finer-grained and does not map 1:1 onto the three. The
[components reference](#architecture-components) owns that distinction and the full
implementation map; this page names the contract trichotomy only as orientation.

{#how-the-pieces-fit-together}
## How the pieces fit together

A run flows through three boundaries:

1. **Engine startup.** The runner enumerates entry points and loads every
   [handler](#handler) declaration and every
   [service-type](#service-type) declaration, making the
   handler registry available for composition. No graph exists yet; no pipeline has
   been type-checked; no dispatch callable is constructed.
2. **Pipeline-declaration load (compose time).** The engine compiles the pipeline
   declaration into a typed dataflow graph and type-checks it. For each node entry
   the engine performs [handler resolution](#architecture-handler-resolution)
   (dotted-path or entry-points) along with the source-AST audit and function-shape
   check that ground the structural seals in
   [trust-model](#architecture-trust-model), then generates Pydantic models
   from the declared `reads` and `output_schema`, fixes the declared
   [compose-time bindings](#compose-time-binding) to the node,
   and constructs the dispatch callable that — on every invocation — supplies the handler
   a fresh per-dispatch copy of each binding alongside the projected reads and performs
   input-validation → handler-call → output-validation. A
   signature mismatch raises
   [ContractViolation](#contractviolation) here, before any
   node dispatches; graph-level type-checks (channel-type agreement,
   service-binding resolution, channel-write disjointness with `merge` opt-in, hook
   transport coverage) fire in the same pass. A graph that loads is a graph that
   type-checks. See [enforcement-modes](#architecture-enforcement-modes) for
   the mechanical-vs-review split.
3. **Dispatch.** The runner walks the graph in declared order, invoking each
   engine-constructed dispatch callable with its
   [dispatch-time bindings](#dispatch-time-binding) —
   declared `reads` projected into kwargs (and `services` for handlers declaring
   service-typed bindings). Bare-function handlers return their declared writes as a
   dict (or `None` for hooks); the
   [trainable](#trainable) composition kind has no author
   body — the engine routes the bound adapter's response onto the trainable
   composition node's declared [output ports](#output-port). The runner threads the writes
   through the graph's channels and emits canonical events on
   `conjured.events.runner` for training-projection capture and operational
   telemetry.

{#topology-sequence-within-dag-across}
## Topology — sequence within, DAG across

Within a pipeline the graph is **sequential**: nodes dispatch in declared order;
data dependencies form a degenerate DAG implicit in the sequence (any node may read
from any earlier write, but the engine makes no fan-out, branching, or
parallel-dispatch commitment at the within-pipeline scope).

:::{transclude} R-pipeline-002/merge-kernel
:::

This preserves the sequence-only commitment.
*Runtime cross-pipeline* composition — chaining one pipeline's output as another's
input on a runtime value, branching between pipeline variants, retrying a pipeline
with modified inputs — IS
DAG-shaped, but that composition lives in consumer code under
[I3](#invariants-and-derived-rules), not in the engine. (*Static*, compose-time
nesting is different: the engine's own nested `pipeline`
[composition kind](#nested-pipeline-kind) embeds a whole pipeline as a node, fixed
at compose time.)

The within-pipeline sequence-only commitment is a deliberate choice that protects
two properties, and the *why* is what makes it architectural rather than incidental:

- **Tenet 1 (composability by non-coders).** A pipeline declaration reads
  top-to-bottom as a sequence of named node entries; the author reasons about it
  the way they read code, not the way they read a graph diagram. Studio renders the
  derived DAG for visual surfacing, but the authoring surface stays linear — a
  branching authoring model would push graph-shaped reasoning onto the non-coder
  author Tenet 1 exists to protect.
- **The I4 unit-of-projection.** One pipeline = one composition identity = one set
  of [trainable](#trainable) projections. Within-pipeline
  DAG topology would either fragment the projection unit (each branch its own
  projection) or aggregate across branches (the projection loses the
  channel-record correspondence the training contract depends on). Sequence-only
  resolves that cleanly: the projection unit is unambiguous because the composition
  is linear.

Cross-pipeline DAG composition has no such constraint — the engine has no contract
to honor at that scope, so consumer code can compose pipelines into any DAG shape
its orchestration logic requires.

{#pydantic-as-the-canonical-representation}
## Pydantic as the canonical representation

Declared interfaces and channel types live in TOML at the authoring surface, but
the engine's *canonical internal representation* is **Pydantic**. Type-checking,
hash construction, and dispatch-boundary validation all operate over the Pydantic
IR, not over TOML lexical form — which is why lexical reformatting of source is
hash-neutral, and why future authoring dialects (JSON Schema sidecar, direct
Pydantic, …) can convert into the *same* IR via 1×N converters rather than N×N
pairwise. The [hash-model](#architecture-hash-model) owns the canonical-IR
construction and the cross-dialect-portability boundary; the architectural point
for this page is only that one privileged IR exists, and every authoring dialect
resolves to it — that single-IR discipline is what prevents multi-dialect drift.

{#the-authoring-surface-the-native-library-is-primary}
## The authoring surface — the native library is primary

The engine's architectural identity is that **the native library is the primary
authoring surface** — the curated, first-party blessed (`conjured*`) catalog
covering most pipeline-composition needs via declarative TOML, resolved exactly as
third-party members are (the
[native-library reference](#native-library-reference) owns the convention and
the normative members).
Authors reach for the native library first; custom handlers are the exception, not the rule. This is
a load-bearing *positive* commitment — the engine commits to maintaining the
library's coverage and shape so declarative composition stays the natural authoring
surface, and it does not ask the library's authors to expose handler-author
mechanics to first-tier authors.

The authoring surface has three tiers, and the key architectural claim is that they
are orthogonal to the closed-enum kind taxonomy — *how* an author reaches a handler
is a separate axis from *what kind* the handler is:

1. **Native library** — first-party blessed (`conjured*`) handlers composed
   declaratively in pipeline declarations. The primary path. Most pipelines are
   *composed* from native-library handlers, not *authored* from custom bare
   kwarg-only functions.
2. **Third-party native handlers** — bare kwarg-only functions packaged
   in third-party libraries and discovered via the `conjured.handlers`
   [entry-points group](#entry-points-group). The
   structural seals in [trust-model](#architecture-trust-model) (function-shape
   check, source-AST audit, no above-instance-scope mutable state) apply —
   third-party handlers compose with native-library handlers under the same engine
   contract.
3. **Escape-hatch mediation** — the long-tail path for non-Python execution: a
   service handler that mediates calls to an external runtime (other languages,
   subprocess invocation, cross-runtime adapters), exactly one external call per
   dispatch with the adapter as the structural seam. The mediation is **one service
   handler, not a separate architectural tier** — the closed handler-kind taxonomy
   is unchanged by the authoring model.

A handler from any tier is one of the closed-enum
[kinds](#handler-kind), and the compose-time engine path
treats all tiers uniformly. The three-tier model is about *how authors reach
handlers*, not *what kinds of handlers exist* — the two axes are independent by
design.

{#what-is-out-of-scope-for-the-engine}
## What is out of scope for the engine

Per the [corpus scope](#corpus-scope), the engine's
invariants apply to engine-conformant handlers — declared, entry-point-registered,
and admitted to the graph by the compose-time dispatch construction. Consumer code
that drives the engine via its
[API](#api-contract) may freely persist pipeline outputs,
orchestrate multi-pipeline flows (retry-with-modified-inputs, branching, fan-out,
dynamic/runtime sub-pipeline composition — static compose-time-known nesting is the
engine's nested `pipeline` kind), deploy the engine (process supervision, container
orchestration, secrets), and evaluate runtime behavior (LoRA quality, classifier
accuracy, end-user satisfaction). The engine ships no abstraction for any of these:
it is a typed dataflow language for handler composition — type-checker plus
dispatch runtime — and gets out of the way. The
[engine / consumer / review partition](#engine-consumer-review-partition)
is the meta-rule that decides what lands inside the engine versus outside it.

{#sensitive-regulated-data-is-a-consumer-design-concern}
### Sensitive / regulated data is a consumer-design concern

Sensitive / regulated data (PII, PHI, financial records) is consumer territory. The
engine emits [canonical events](#canonical-event) whose
payloads carry the values flowing through declared channels; where those channels
carry sensitive fields is a consumer-design concern, not an engine feature. The
architecture admits three patterns, in decreasing safety order:

1. **Tokenize before the graph runs** — consumer-side anonymization, recommended
   for regulated-data use cases; PII never enters any channel.
2. **Author sanitizing service handlers** whose `output_schema` declares only the
   fields that should be public — the dispatch wrapper enforces the declared shape,
   so any PII the handler body computes that is not in the declared writes never
   reaches a channel.
3. **Filter at downstream consumer-attached handlers** — weakest, because PII has
   already flowed through the graph's channels; every attached sink must filter.

The engine ships no PII tooling because PII semantics are domain-specific (HIPAA vs
PCI vs GDPR differ); the `output_schema` declaration surface is already where
source-side sanitization happens cleanly. This is *why the engine's existing shape*
(declared-writes-only dispatch) is sufficient for the safe patterns — not a
separate engine capability.

{#overview-reading-order}
## Reading order

A new reader should read in this order:

1. This overview (you are here).
2. [pipeline-as-training-contract](#pipeline-as-training-contract-explanation)
   — the training corpus as a derived view of the graph.
3. [handler-kinds](#architecture-handler-kinds) — the closed-enum kind
   taxonomy, motivated by mechanically distinct graph positions.
4. [exhaustive-declaration](#architecture-exhaustive-declaration) — the
   discipline distinguishing considered-and-declared-nothing from forgot.
5. [enforcement-modes](#architecture-enforcement-modes) — mechanically-enforced
   versus review-enforced, and how the runner and adversarial review compose.
6. [trust-model](#architecture-trust-model) — the vector inventory of
   structural seals against I4 breakage.
7. [handler-resolution](#architecture-handler-resolution) — the compose-time
   mechanism resolving handler names to bare-function callables.
8. [hash-model](#architecture-hash-model) — the two-hash scheme backing the
   training-contract integrity guarantee.
9. [principles](#principles) — the Invariants and Tenets the
   architecture pages cite.

C4-flavored diagrams of system context and engine-internal components live at
[context](#architecture-context) and
[components](#architecture-components). Conjured has no Container-level
decomposition because the engine ships as one Python process per host.
