---
kind: glossary
audience: [authors, integrators, agents]
slug: glossary
owns_headings: true
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#glossary}
# Glossary

The vocabulary of the Conjured engine. Most entries are a one-paragraph
definition plus, where useful, cross-references to the page where the concept is
treated in depth; some run longer, and a few also serve as an **owning home** —
for a transcluded kernel (e.g. the composition-kind roster, the `failure_category`
enum) or for facts deliberately single-homed here (the Studio and conjured-utils
PyPI packaging facts). Terms are listed alphabetically.

This glossary is the single source for engine domain language. Every domain
term that appears elsewhere in the canonical doc set is defined here; pages
elsewhere link back rather than redefining.

---

{#acknowledged-drift}
## Acknowledged drift

A deployment-declaration mechanism for explicitly accepting a
specific [training-contract](#training-contract) drift class when
[integrity enforcement](#integrity-enforcement) is enabled. Declared
as `acknowledged_drift` entries naming the artifact and the specific
drift class — under per-trainable granularity, a drift class names
a specific trainable, so acknowledging
[training-bundle-hash](#training-bundle-hash) drift at one trainable
does not silently accept drift at another. Acknowledged-drift entries
are ignored when integrity enforcement is off (no enforcement to
acknowledge against).

{#adapter-boundary-capture}
## Adapter-boundary capture

The engine's structural defense against silent-fallback for
**service-kind** handlers — a service body that masks a failed or
absent backend response with a schema-valid return (see
enforcement-modes § layered defense).
The `service_invocation` [canonical event](#canonical-event) is
captured at the [service-type adapter](#service-type-adapter)
boundary, BEFORE control returns to the handler body; paired with
the same dispatch's `handler_exit` event (joined by `pipeline_run_id`
+ `handler_position` and equivalently by the
[correlation ID](#correlation-id)), the two
events expose the masking signature on the wire. The seam is structural
rather than disciplinary: the handler body cannot reach inside the
adapter, so it cannot launder its return through the event log.

Provenance capture is **keyed by node kind**:

- For a **[service](#service)**-kind handler dispatch the captured record is the
  `service_invocation` event payload — the adapter boundary fixes what was
  submitted and what the backend returned before the handler body sees the
  response. It is provenance / divergence evidence, **not** an
  engine-guaranteed training record; training capture is the trainable
  composition kind's role.
- For a **[trainable](#trainable)** composition node
  dispatch the captured training record IS the `handler_enter` + `handler_exit`
  pair (the engine constructs the dispatch directly against the bound trainable
  backend; there is no author body for an adapter boundary to defend against, and
  no `service_invocation` fires).

{#annotations}
## annotations

An **engine-opaque** block on a declaration: the engine never reads it. It is graph-inert
(not a node, declaring no channels), excluded from every hash, and never delivered to a
handler. Its role is a **consumer surface** — human- and tool-facing metadata the engine
ignores and consumers read directly: author docstrings and comments, and structured tool-use
fields (such as the `postprocessors` UI-grouping a frontend reads to group trace views). The
per-kind grammar is owned by the handler component reference.

{#glossary-api}
## API

The wire-protocol surface the engine's [server](#server) exposes to consumers,
used in its standard sense. The contract-scope name is the
[API contract](#api-contract) (which carries the full treatment); the deployable
process is the [server](#server). Avoid "network API" — it conflates the API and
the server.

{#api-contract}
## API contract

The protocol the engine's [server](#server) exposes for operating and
observing runs — the cross-language side of the consumer boundary, with
the engine's Python-ness an implementation detail behind it. One
contract; many transport implementations may satisfy it. Composing — and
embedded/notebook runs — cross the boundary's in-process side, the
compose API the pipeline component's reference owns. Distinct from the [pipeline contract](#pipeline-contract),
[training contract](#training-contract), and [declared contract](#declared-contract);
context determines scope when "ContractViolation" is referenced.

{#audit-code}
## audit code

The `audit_code` field carried on error payloads. A string identifier in
format `<CX>.<TOPIC>.<NNN>` (e.g., `"C2.FIELD_DISCIPLINE.001"`) uniquely
identifying a component audit catalog entry. The primary dispatch key for agent
tooling and consumer error-routing logic — consumers match on `audit_code` to select
per-violation documentation, remediation prose, and escalation paths; tooling
cross-references the catalog entry for the rule, expected form, and seam details. The `<CX>`
prefix is the **component allocation** — the component that owns the enforced rule (not
the machinery that raises the error); each canon component owns one `<CX>` prefix, and
`<TOPIC>` and `<NNN>`
are assigned within the owning component's conformance catalog.

{#bundle-toml}
## Bundle TOML

The `kind = "bundle"` specialization of the
[composition TOML](#composition-toml) — the pure-substitution member
of the composition-kind enum. Its `nodes` are textually substituted
into the enclosing node sequence at compose, before that unit is scoped
or hashed, so a bundle has no engine-owned dispatch, no
[scoped channels](#scoped-channel), no `inputs` / `outputs` boundary,
and no own hash domain; its structural role is authoring-time DRY
convenience. The minimal grammar and substitution semantics are owned at
[handler reference § The bundle composition kind](#bundle-composition-kind-grammar);
the hash treatment at
[hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs-family-rule).

{#canonical-event}
## Canonical event

One of a [closed enum](#closed-enum) of events the engine emits on
`conjured.events.runner` and the [server](#server) projects onto the
wire — events like `pipeline_start`, `handler_exit`, `service_invocation`.

`service_invocation` is **service-kind only** — captured at the
[service-type adapter](#service-type-adapter) boundary (see
[adapter-boundary capture](#adapter-boundary-capture)); [trainable](#trainable)
composition nodes emit none, capturing the `handler_enter` / `handler_exit`
pair instead.

{#channel}
## Channel

The typed value-conduit in the engine's [typed dataflow graph](#graph) —
an edge connecting a [node's](#node) [output port](#output-port) (routed
on by its [write-map](#write-map)) to another node's [input port](#input-port)
(routed in by its [read-map](#read-map)). A channel has no standalone type
declaration; its type is **induced** by exact agreement of every port wired
to it (and of any pipeline-boundary `inputs` / `outputs` declaration that
participates): the graph [type-checks](#type-check) at compose time by
collecting every read-port and write-port resolved to a channel and verifying
they declare the same type (exact equality, no widening). If all agree, that
agreed type is canonical; if any two disagree, the engine refuses to load —
no port owns the channel, agreement does. A channel is **single-assignment** in
the read/write-disjointness sense: no [node](#node) both reads and rewrites one
channel — a [handler](#handler) is channel-agnostic and cannot name the channel
it writes, so read-then-rewrite is structurally impossible (to transform a value,
write a new channel). A channel with two or more contributors is a fan-in whose
value is the declared [merge](#merge-strategy) strategy's fold in graph order, so
a reader composed between two contributors sees the fold-so-far
([R-pipeline-002](#R-pipeline-002-merge-kernel) owns the rule). The channel is the unit on which
[input-port](#input-port) and [output-port](#output-port) declarations
type-check, and on which trainability scope applies — a
[trainable channel](#trainable-channel) is the projection unit for
the [training contract](#training-contract).

{#channel-record-correspondence}
## Channel-record correspondence

(channel-record-correspondence-bijection)=

The load-bearing bijection between channel-writes at external-edge positions —
[service](#service)-kind nodes and [trainable](#trainable) composition nodes —
and their captured [canonical events](#canonical-event): **every such
channel-write maps to one captured event, every captured event maps to one
channel-write.** At [trainable channels](#trainable-channel) the bijection is
what makes the training projection well-defined; at service-kind writes it
holds the provenance record.

Without the bijection the training projection would not be
well-defined — multiple backend calls collapsing into one captured
record (semantic retry), or one call producing a record that doesn't
match the channel-write the handler returned (silent in-body
fallback), would each break the correspondence. The atomicity rule on
[services](#service) preserves the correspondence at the
runtime-contract layer; [adapter-boundary capture](#adapter-boundary-capture)
preserves it at the provenance-capture layer for service-kind writes.
The per-kind event mapping is owned at
hash-model § canonical event types.

{#channel-field-type}
## Channel-field type

The type a [channel](#channel) carries — canonically, a type in the engine's
**Pydantic intermediate representation** (the form the engine type-checks across node
boundaries and folds into the hashes). Authors declare it with a curated, Pydantic-aligned token set — curated so
declared types map to backend structured-output constraints (the
[literal-equal rule](#literal-equal-rule) for trainable channels; schemas outside
a backend's grammar-convertible subset are rejected at compose — the handler
reference owns that boundary). The IR is the
canonical form; the token set is the author-facing surface for declaring it. The
handler reference owns the full token grammar.

{#closed-enum}
## Closed enum

A fixed set of options the engine won't let you add to at runtime —
changing the set requires an engine change, not consumer configuration.
Engine surfaces with this property include the
[handler kinds](#handler-kind), the composition kinds, the
[error classes](#error-class), and the [canonical events](#canonical-event).

{#compose-time-binding}
## Compose-time binding

One of the two binding axes of a [handler](#handler). Compose-time
bindings are declared via `bindings.<name>` entries in the handler
declaration; the pipeline-entry supplies each binding's value in any
form the handler reference's binding value-supply grammar admits —
inline, by external declaration file (the `{ file = "..." }` form), as
the explicit null (`{ null = true }`, on a nullable-declared target), or
a ship-time default — that grammar owns the complete set; the engine
resolves and validates each value once at compose. At each dispatch the runner
supplies the handler a fresh per-dispatch copy of the binding value as
a kwarg whose value is fixed across every dispatch of this composed
pipeline — copying per dispatch keeps a handler's in-place mutation
from leaking into a later dispatch ([trust-model vector](#trust-model-vector)
4). Large static read-only data opts out of copying via the
[reference binding](#reference-binding) subtype. Author names bindings
by domain meaning; N ≥ 0 bindings per handler. All bindings contribute
to the [pipeline-hash](#pipeline-hash). Service-typed bindings declared
in `service_bindings` resolve at compose time (the bound service is
captured for the dispatch) but are reached at dispatch via the
`services` kwarg. Contrast [dispatch-time binding](#dispatch-time-binding).

{#compile-directive}
## compile directive

The `compile = "..."` sub-form of a
[compose-time binding](#compose-time-binding) declaration. The engine
resolves a named **compiler** at binding resolution and delivers the
produced artifact as an engine-owned kwarg to the handler — the author
never sees a cache API. A compile-resolved artifact is engine-owned and
is delivered as-is — not copied per dispatch and not the
[reference binding](#reference-binding) subtype. The directive's
resolution model (bare blessed first-party compilers plus namespaced
third-party compilers), the closed deterministic `params → artifact`
compiler contract, and its failure and hash semantics are owned by the
handler reference's `compile = "..."` directive sub-form.

{#reference-binding}
## reference binding

An opt-in [compose-time binding](#compose-time-binding) subtype for
large, static, read-only data — marked `delivery = "reference"` on a
`bindings.<name>` entry. Where an ordinary binding is copied per
dispatch, a reference binding is **deep-frozen once at compose and
shared**: the same immutable instance is handed to every dispatch and
every reader, so a multi-megabyte structure (an NPC worldbook, an
alias→character lookup table, an in-process retrieval index) is read on
every dispatch at no per-dispatch copy cost. The field-named precedent
is the **broadcast variable** — a large read-only value shared per node
rather than shipped with each task. The one-time deep freeze is what
makes sharing safe: with no mutable interior left to leak, a single
shared instance cannot carry state across dispatches. Mutation is
fail-loud (the immutable type rejects the write), which is correct here
because mutating reference data is always a bug. Contrast the
per-dispatch-copy default of an ordinary
[compose-time binding](#compose-time-binding); the seal is the one
opt-in exemption to [trust-model vector](#trust-model-vector) 4.

{#composition}
## Composition

The act of arranging [nodes](#node) into a [pipeline](#pipeline).
Composition is declarative — a pipeline declaration names nodes in
order (handler entries and composition entries per the `nodes` array's
`kind` discriminator), supplies their [service](#service) bindings,
and supplies each entry's [compose-time bindings](#compose-time-binding)
(in any form the binding value-supply grammar admits).

{#composition-change}
## Composition change

Any edit that shifts the [pipeline-hash](#pipeline-hash). Sources
include pipeline-declaration edits (handler order, binding values,
service bindings), handler-declaration edits (declared schemas,
declared bindings, service bindings), external-declaration edits
(binding values supplied by file path), and qualified-name reference
changes.

{#composition-ref}
## Composition ref

The `composition_ref` field carried on error payloads. A string identifying the
pipeline and handler entry ordinal at the locus of the failure, format
`"<pipeline_name>[<entry_ordinal>]"` (e.g., `"dialogue[3]"`) — the location-bearing
identifier for composition-level violations where no single source declaration is the
responsible locus.

{#composition-toml}
## Composition TOML

A kind-discriminated, embeddable composition primitive with
per-kind specialization rules spanning an engine-visibility spectrum
— from pure-substitution kinds (engine sees through entirely; no
scope, no boundary, no own hash domain) to engine-owned-dispatch
kinds (pipeline-shaped, with explicit `inputs` / `outputs` boundary,
[scoped channels](#scoped-channel), flatten-on-embed semantics, and
own hash domain — the composition's own hash IS its kind-determined
identity hash, and the hash boundary tracks the composition
boundary). The `meta.kind` discriminator at the top of the
composition declaration names which specialization applies; the
specialization rules determine which structural apparatus the engine
invokes. The `kind` enum is closed and extensible only by an engine change; its realized
membership is owned by the handler reference's grammar:

A composition declaration's `meta.kind` value MUST be one
of the closed-enum composition-kind values (realized today as `"trainable"`,
`"bundle"`, and the nested `"pipeline"`; further kinds plug in via subsequent
engine changes).

A composition declaration,
when embedded in a pipeline, fills a [node](#node) in the outer
pipeline's nodes sequence (with `kind = "composition"`); the
embedded composition's own `meta.kind` discriminates which
specialization applies.

{#conformance-check}
## Conformance check

A mechanical, agent-runnable check verifying that handler or pipeline code
conforms to a derived rule. Conformance checks are
[mechanically-enforced](#mechanically-enforced) where the runner can see the violation;
[review-enforced](#review-enforced) where it cannot.

{#consumer}
## Consumer

The application or codebase that drives the engine via its
[API](#api-contract). The consumer composes pipelines, supplies service
implementations, deploys the engine, and owns the runtime. The engine
ships rules that bind its own engine-conformant handlers;
consumer code is not bound by those rules.

{#contractviolation}
## ContractViolation

The [error class](#error-class) raised when the engine detects a
**declaration-existence mismatch** — a key or field that is not declared,
or a required declaration that is missing. The per-class examples and
the per-boundary routing are owned at the error-channel reference
([the key-set routing](#R-error-channel-001-key-set-routing) and the
[error-classes kernel](#error-classes-kernel)).
Halts the pipeline. Contrast with [SchemaValidationError](#schemavalidationerror)
(shape mismatch within declared fields).

{#reads-snapshot}
## reads snapshot

The channel values projected into a failed handler's dispatch kwargs at halt — a
diagnostic field of the runtime-failure payload
([PipelineFailure](#pipelinefailure)), surfaced on the
[error channel](#glossary-error-channel) alongside the bindings snapshot and composition
reference. A per-handler failure
slice: precisely what the failing handler was seeing at dispatch time, reproduced
for consumer logging, display, and debugging. Explicitly not pipeline output for
downstream composition — the [output channel](#output-channel) delivers nothing on
halt. Distinct from [event trace](#event-trace), which is the full run-level event log.

{#event-trace}
## event trace

The runtime trace of every handler dispatch in a pipeline run — channel values at
each node's entry and exit, timing per handler, and (on halt) the failed handler's
identity and failure details. Available on both the success path and the halt path
via `conjured.events.runner`, filtered on `pipeline_run_id`. The `pipeline_error`
[canonical event](#canonical-event) is the halt-path log entry. Distinguished from
[reads snapshot](#reads-snapshot), which is the per-handler failure slice surfaced on
the [error channel](#glossary-error-channel). See [Replayability](#replayability) for why
the trace suffices to reconstruct any captured run.

{#declared-contract}
## Declared contract

A handler's schema-level commitment to its inputs, outputs, and bindings —
written declaratively, validated at engine startup, and visible to every
other handler that composes against it. The phrase "declared contract"
foregrounds the absence of any implicit channel: if it is not declared, it
does not exist for the engine.

{#derivation-chain}
## Derivation chain

The relationship between an [invariant](#invariant) (or a tenet) and a
[derived rule](#derived-rule). A derived rule names the invariant(s) or tenet(s) it
protects in its `derived_from`
metadata — at least one is mandatory — and MAY additionally cite the
derived rule(s) it specializes; the build hook validates that every citation
resolves. The chain makes engine behavior auditable — every rule answers "which
axiom or design goal does this serve?"

{#derived-rule}
## Derived rule

A rule that protects one or more [invariants](#invariant) or tenets. Derived rules
carry a `rule_id` ([its format convention](#rule-id-format-kernel) is owned at
principles' § Invariants and derived rules), an `enforcement` mode
([mechanical](#mechanically-enforced) or [review](#review-enforced)), and one or more
`derived_from` entries citing the invariant(s) or tenet(s) protected (at least one; a rule
MAY additionally cite a derived rule it specializes). Cross-component derived rules live
in principles.md; per-component rules live alongside their
component reference.

{#derived-view}
## Derived view

A value materialized from declared [channels](#channel) via
type-preserving transforms — one of the engine's foundational
structural framings. The
[training contract](#training-contract) is a derived view of the
[pipeline](#pipeline) contract: the engine's typed dataflow graph
carries the channels, and projecting the graph at
[trainable channels](#trainable-channel) yields the training-record
shape. The runtime contract and training-shape contract are not
separate artifacts kept in sync; they are two derived views over one
graph. Contrast [materialized derived view](#materialized-derived-view) —
the trained artifact (LoRA, adapter) is the materialization of running
the contract through training, frozen into weights.

{#determinism-under-composition}
## Determinism (under composition)

The property that two handlers composed in a pipeline produce predictable
behavior before execution. Schemas match or fail at load; compose-time
[type-checking](#type-check) surfaces every incompatibility the runner can
statically detect before any handler dispatches.

{#correlation-id}
## correlation ID

The `correlation_id` field on the `service_invocation` and
`handler_exit` [canonical events](#canonical-event) for the same
service-handler dispatch. It pairs the two events of one dispatch so a
consumer can detect divergence between what the backend produced and
what the handler returned; a consumer may equivalently pair on
`pipeline_run_id` + `handler_position` — the two joins are equivalent, and
`correlation_id` is retained as a
single-field wire convenience whose value IS that composite (the
[dispatch-identity key](#canonical-event-types-dispatch-identity)
hash-model owns). Its presence conditions (which dispatch kinds carry it on
`handler_exit`), value derivation, and pair semantics are owned by
hash-model § canonical event types.

{#dispatch-time-binding}
## Dispatch-time binding

One of the two binding axes of a [handler](#handler). Dispatch-time
bindings are supplied as kwargs to the handler at each invocation
during a pipeline run. Two declarable sources: `reads` (the handler's
[input ports](#input-port) — every declared port becomes a kwarg-only
parameter, populated by [projection](#projection) of the channel each port's
[read-map](#read-map) wires it to, from the graph at this node's position) and
the reserved `services` kwarg (carrying a [ServicesProxy](#servicesproxy) for
handlers declaring an entry in `service_bindings`). The dispatch signature is exhaustive —
undeclared keys are not in scope as parameters, and undeclared writes
are rejected by the [sole admission gate](#sole-admission-gate) at
the [type-check](#type-check) seam on return. Contrast
[compose-time binding](#compose-time-binding).

{#dotted-path-resolution}
## dotted-path resolution

The primary [handler resolution](#glossary-handler-resolution) mechanism: a
pipeline declaration names a handler as a dotted Python module path
(e.g., `mymodule.normalize_charset`), resolved through the compose-time
resolution sequence the handler-resolution architecture doc owns. Its
priority over the [entry-points group](#entry-points-group) short-name
path is the owned selector rule:

Explicit dotted-path resolution wins over entry-points short-name
resolution. The rule is mechanical: if the handler name contains a dot,
the engine treats it as a dotted path and does no entry-points lookup;
a name with no dot is an entry-points short name. The presence or
absence of a dot fully determines which path runs — there is no
ambiguous case.

{#engine}
## Engine

The Conjured runtime — [type-checker](#type-check) for the typed dataflow
[graph](#graph), runner that dispatches handlers in declared order,
validator that loads declarations into the canonical Pydantic intermediate
representation, hash machinery, canonical event log — packaged inside
a [server](#server) process whose [API](#glossary-api) is the engine's public
surface. The engine is narrow by design and does not own consumer
concerns (persistence, deployment orchestration, behavioral evaluation).
Distinct from the server (the deployable process) and from the API
(the wire contract).

{#engine-source-tree}
## engine source tree

The set of files inside the conjured package — engine code, canonical docs,
tests, build infrastructure. Distinguished from consumer-side files. Nothing in
the engine source tree references content outside it, so the engine ships with a
self-contained doc set.

{#enforcement-mode}
## Enforcement mode

The mechanism by which the engine ensures a rule holds. Two modes:
[mechanically-enforced](#mechanically-enforced) (runner mechanically rejects) and
[review-enforced](#review-enforced) (adversarial review
catches handler-body instances the runner cannot see). A rule may carry
both modes when it has structural and impl-body components.

{#entry-points-group}
## entry-points group

A Python entry-points group name for additive third-party-package
discovery via short names. The [group roster](#entry-point-groups-roster) —
handler, service-implementation, and validator discovery — is owned by the
service-type reference's § Entry-point groups. Entry-point collisions fail-loud
at engine startup.

{#glossary-error-channel}
## Error-channel

One of the engine's components
(alongside [handler](#handler) and [pipeline](#pipeline)). Owns the
[error class](#error-class) closed enum — full membership and
per-class semantics live at
R-error-channel-001 (closed-enum error classes)
— along with halt semantics and the runner's hook-error wrapper
(the hook two-case rule, owned at
[R-error-channel-003](#R-error-channel-003)). The error-channel is the engine's single point of
failure-class commitment: a closed enum that consumers can dispatch
on, with no escape-hatch class to absorb unexpected runtime
conditions.

{#error-class}
## Error class

One of the [closed enum](#closed-enum) of classes the engine raises. Halts the
pipeline for channel-writing nodes; the [hook](#hook) carve-out is
[R-error-channel-003](#R-error-channel-003)'s two-case rule.

{#glossary-exhaustive-declaration}
## Exhaustive declaration

The discipline that every closed-shape key applicable to a handler's
[kind](#handler-kind) MUST appear in the handler declaration, even when
the body is empty. Empty-but-present is the canonical "considered this
axis, declared nothing" signal; omission is a load-time
[ContractViolation](#contractviolation). The pattern's analog is the
legal-compliance form, where every field is addressed and "N/A" is
explicit.

{#fake-service}
## fake service

A service implementation used in [conformance checks](#conformance-check) and
local testing — synthetic, deterministic, no external dependency. fake services satisfy the same service-type contract as production
implementations; they are swapped at the qualified-name boundary. The
distinction between fake and production is a deployment concern; the engine
treats both alike.

The term carries its trained [test-double](#test-double) sense: a **fake** is a
contract-satisfying deterministic stand-in. A *mock* — a double whose job is
verifying how it was called — is not the engine's substitution vocabulary
(compose-time twin substitution swaps in fakes), and mock-vs-fake never carries
the training-capture distinction: whether capture fires is determined by
composition kind — a fake trainable backend preserves training capture; a fake
service-type backend has none (the handler reference's § Test substitution owns
that split).

{#graph}
## Graph

The composition artifact the engine [type-checks](#type-check) and
dispatches over — handlers as [nodes](#node), declared reads and
writes as typed [channels](#channel) between them. **Sequential within
a pipeline** (nodes dispatch in declared order), **DAG-shaped across
pipelines** (*runtime* cross-pipeline composition lives in consumer
code; *static*, compose-time nesting is the engine's own nested
`pipeline` [composition kind](#composition-toml)).

{#native-library}
## Native library

The first-party blessed (`conjured*`) handler catalog — the primary authoring
surface: most pipelines compose declaratively from it rather than authoring
custom handlers. Native handlers are referenced by their qualified `conjured*`
dotted path and resolve through ordinary [dotted-path resolution](#dotted-path-resolution),
exactly as third-party handlers do. The native-library component reference owns
the convention and the member contracts.

{#node-role}
## node role

The kind-as-role framing for the [handler kinds](#handler-kind): each
kind corresponds to a mechanically-distinct role a [node](#node) can
play in the graph — what channels it writes (or none, for an observer),
whether it reaches an external resource, and how a failure at its
position halts. The per-kind role assignments live with the kind
taxonomy's owner (the handler-kinds architecture page; membership at
R-handler-003, closed-enum handler kinds). The "same artifact, two views" pair
to handler kind: kind is the type the handler declaration carries;
node role is the role the node plays in the graph.

{#handler}
## Handler

The engine's **runtime dispatch surface** — the [node](#node) in the
typed dataflow [graph](#graph) realized by a handler entry, a
bare kwarg-only function the runner dispatches. A handler is a
**channel-agnostic pure function** over its declared, named, typed
[input ports](#input-port) → [output ports](#output-port); it never sees a
channel name. The [node](#node) that places the handler carries the
port→channel wiring — a [read-map](#read-map) and a [write-map](#write-map) —
so the runner constructs the handler's kwargs from the read-map before the
call and routes the return dict onto channels via the write-map after, and the
same handler may be wired at more than one node. Handler is
the *subset* of graph [nodes](#node) backed by a handler entry; the
broader compose-time graph position — which also covers composition
entries — is the [node](#node) (the `nodes` array's `kind` discriminator
is `kind = "handler"` vs `kind = "composition"`). Every handler is
exactly one of the [kinds](#handler-kind), each corresponding to a
distinct [node role](#node-role); handlers are declared and
entry-point-registered, and the runner dispatches each to the code path
for its kind.

{#handler-kind}
## Handler kind

One of the [closed-enum](#closed-enum) handler kinds — full membership
lives at R-handler-003 (closed-enum handler kinds) — distinguished by the
[node role](#node-role) its [node](#node)
plays in the typed dataflow graph: which channels it writes, whether
it makes an external call, and how it halts on failure. All ship as
bare kwarg-only functions; the
engine constructs the dispatch wrapper. For the trainable
composition-kind specialization, see [trainable](#trainable).

{#glossary-handler-resolution}
## handler resolution

The engine's compose-time mechanism for resolving a
pipeline-declaration handler name to a bare-function callable — two
discovery paths (primary:
[dotted-path resolution](#dotted-path-resolution); additive:
[entry-points group](#entry-points-group) short-name lookup) feeding one
resolution sequence, owned in full — steps, seals, per-step checks — by
the handler-resolution architecture doc. All resolution failures are
compose-time [ContractViolation](#contractviolation) with remediation
hints. [Service-type adapter](#service-type-adapter) resolution runs the
same sequence under the `conjured.service_implementations`
entry-points group with its own
[inverted selector](#adapter-selector-inverted-priority); third-party
validator resolution uses the same
[inverted selector](#adapter-selector-inverted-priority) (under the
`conjured.validators` group).

{#glossary-hash-model}
## Hash model

The engine's two-hash scheme: a [pipeline-hash](#pipeline-hash)
covering every composition input (the identity of the full
[graph](#graph)), and a per-[trainable](#trainable)
[training-bundle-hash](#training-bundle-hash) covering the trainable
composition's declaration (the training-record-shape identity for
the [trainable channels](#trainable-channel) the node emits). Together
the hashes implement the
[pipeline-as-training-contract](#glossary-pipeline-as-training-contract)
integrity property.

{#hook}
## Hook

The [handler kind](#handler-kind) occupying the **observer node**
[node role](#node-role): reads declared
channels as kwargs, emits externally, and writes no channels. Hooks
return `None` and the runner has no merge path for a hook return.
A hook's failure disposition is the two-case rule
[R-error-channel-003](#R-error-channel-003) owns. Examples: post-dialogue
webhook, telemetry emitter, audit-log writer.

{#identity-service-binding}
## Identity service binding

The fields a service-typed binding contributes to a hash — model name,
prompt template, version selectors. A composable unit supplies its own
bindings' identity at its own level: a top-level handler's identity is
supplied in the pipeline declaration's `service_bindings.<name>` and
folds into the [pipeline-hash](#pipeline-hash); a trainable composition
supplies its own backend identity in the composition's
`service_bindings.<name>` and folds into the
[training-bundle-hash](#training-bundle-hash) — the self-contained
supply of the mirror-pipeline principle. Either way the identity is
baked into the trained-artifact's reproducibility manifest.
Distinguished from [transport](#transport), which carries
deployment-specific values that are not hashed.

{#input-port}
## Input port

A [handler](#handler)-local named, typed input declaration — distinct from a
[channel](#channel). A handler's `reads` entries ARE its input ports: each
becomes a kwarg-only parameter on the handler's Python signature. The handler
is channel-agnostic; it names ports, never channels. The [node](#node) that
places the handler carries a [read-map](#read-map) wiring each input port to
the channel it reads (identity — port name = channel name — is the sugar
default). At dispatch the runner [projects](#projection) each input port's
wired channel value into a kwarg keyed by port name. Paired with
[output port](#output-port).

{#integrity-enforcement}
## Integrity enforcement

The deployment's opt-in to halt-on-mismatch behavior for the
[training contract](#training-contract). Declared in the deployment
declaration's `training_contract` block via the
`integrity_enforcement = true | false` field. The block's grammar rule
is owned at the deployment reference:

The closed-shape key MUST appear AND `integrity_enforcement` MUST carry an explicit boolean; a
missing `[training_contract]` block, an empty
body, or a missing field is [ContractViolation](#contractviolation) at deployment load.

The kernel of the semantics — owned, with the full per-state and
graduated-force behavior, by hash-model's integrity-enforcement opt-in:

The engine separates the integrity *property* (always available — hashes computed
at compose time, `training_bundle_hash_changed` and `pipeline_hash_changed`
canonical events fire on shift, Studio surfaces drift in trace regardless) from
the integrity *enforcement* (deployment-level opt-in toggling whether hash
mismatch on a loaded artifact's manifest **halts** load or only emits events).

{#invariant}
## Invariant

A load-bearing axiom of the engine. Every [derived rule](#derived-rule) traces
its `derived_from` chain to one or more invariants or tenets. Invariants are the contract
by which the engine binds itself; they carry rule-IDs of the form `I<N>` (the
design-goal tenets they serve carry `T<N>`).

{#literal-equal-rule}
## Literal-equal rule

The discipline that a [trainable channel's](#trainable-channel)
declared type — the channel a [trainable](#trainable) composition
node emits via its `output_schema` — is canonically equal to the
backend's response shape under structured-output / constrained-decoding.
The schema the runner validates against at the adapter boundary IS
the schema the backend's structured-output mode is constrained
against — one source, two consumers, no drift surface. The rule is
what makes capturing the trainable dispatch also capture a
training-data record whose shape is guaranteed to match what the
backend can produce, and it is the per-channel rule that generalizes
to multi-channel-trainable compositions without framing shift.

{#llmstxt}
## llms.txt

The agent-surface index file of the engine's in-package agent
surface (`importlib.resources.files("conjured.agent")`). Coding
agents read it to discover the engine's machine-readable doc surface;
the file lists agent-audience canonical pages by section, generated
from canonical-doc frontmatter (`audience: [..., agents]`) by the
in-package build tooling, which also gates the shipped index's
freshness against current canon.
See [single-sourcing](#single-sourcing).

{#materialized-derived-view}
## Materialized derived view

A trained artifact (LoRA, adapter, fine-tuned model variant) viewed
as a [derived view](#derived-view) of the typed dataflow graph that
has been **materialized** — sampled over runtime traffic at a
[trainable channel](#trainable-channel) position and frozen into
weights. The artifact's sidecar
[trained-artifact manifest](#trained-artifact-manifest) records the
[pipeline-hash](#pipeline-hash) set and per-[trainable](#trainable)
[training-bundle-hashes](#training-bundle-hash) at training time; the
engine surfaces drift on every load (and halts under
[integrity enforcement](#integrity-enforcement) when the deployment
opts in). The framing names the pipeline as the contract and the
artifact as a projection of running that contract through training.

{#mechanically-enforced}
## Mechanically-enforced

An [enforcement mode](#enforcement-mode) in which the runner mechanically
rejects a violation at a boundary it can see — handler-declaration load,
compose time, or dispatch. Mechanically-enforced rules carry `enforcement: mechanical`
in their metadata.

{#merge-strategy}
## merge strategy

The operation that combines a [channel](#channel)'s two-or-more
contributors, declared in a pipeline declaration's `merge.<channel>` block.

A channel's contributors are its seed (if the channel is a declared input) plus its node writes,
in graph order. A channel MAY have two or more contributors **iff** the pipeline declaration's
`merge` block declares a merge strategy for that channel from the engine's closed registry of
merge operations. Without an explicit `merge.<channel>` declaration, a channel with two or more
contributors is rejected at compose time with [ContractViolation](#contractviolation). The runner
folds the contributors into the channel's value inline, in graph order under the declared
strategy — the runner's own work.

The strategy registry is closed-enum (new strategies land by an engine
change); the pipeline reference's `merge.<channel>` grammar owns the full set.
Fan-in the closed registry can't express is served by the
**aggregator pattern** — an author-written transform node, not a merge strategy —
documented in the pipeline reference's aggregator-pattern section.

{#node}
## Node

A position in the typed dataflow [graph](#graph) — the **compose-time
graph unit** and the superset of [handler](#handler): a node is realized
by either a handler entry or a composition entry (the `nodes` array's
`kind` discriminator, `kind = "handler"` vs `kind = "composition"`), so
every handler is a node but not every node is a handler. A handler node
carries `kind` / `name` / `bindings` plus an optional `reads_map`
([read-map](#read-map)) and `writes_map` ([write-map](#write-map)) wiring
the handler's [input ports](#input-port) and [output ports](#output-port) to
graph [channels](#channel). A node's identity is its **dispatch position**
in the pipeline's `nodes` sequence, not the handler it names — so the same
handler may appear at more than one node, each dispatch a distinct position.
For a handler node it is the "same artifact, two views" pair to
[handler kind](#handler-kind): kind is the type the declaration carries,
node is the [role](#node-role) the handler plays in the graph. Each
entry in a pipeline declaration's `nodes` array realizes one node.

{#output-channel}
## Output channel

The success-path pipeline API surface. The declared `outputs` fields are the
pipeline's committed happy-path surface, returned within the run's full
outer-channel state (the pipeline reference's RunResult owns the result shape).
The channel-separation guarantees — a returned value IS the success signal, no
envelope discriminator field, silent on halt — are owned at
[R-error-channel-004](#R-error-channel-004) (channel separation).
Distinct from the [error channel](#glossary-error-channel), which is the halt-path
surface carrying [reads snapshot](#reads-snapshot) and diagnostic payload.

{#output-port}
## Output port

A [handler](#handler)-local named, typed output declaration — distinct from a
[channel](#channel). A handler's `output_schema` entries ARE its output ports,
and the keys of the dict it returns ARE its output-port names. The handler is
channel-agnostic; it names ports, never channels. The [node](#node) that places
the handler carries a [write-map](#write-map) wiring each output port to the
channel it writes (identity — port name = channel name — is the sugar default).
The runner validates the return dict against the declared output-port shapes,
THEN routes the validated values onto channels via the write-map — so a handler
cannot name, and so cannot read-then-rewrite, the channel it writes. Paired
with [input port](#input-port).

{#override-instruction}
## Override-instruction

A short, imperative steering note — often phrased as a contrastive
redirect — adjacent to a discipline whose framing diverges from a
mainstream paradigm; the
[exhaustive-declaration](#glossary-exhaustive-declaration) page is the canonical
example. The agent surface re-renders the override-instruction so that
agents priming on the codebase see the discipline before reaching for a
familiar pattern.

{#pipeline-derivables}
## Pipeline derivables

The compound artifact the engine extracts from a composed pipeline at
compose time — the primary input to the training-data generation flow.
Extraction is pure-read: no pipeline dispatch occurs, no service
invocations fire. The component breakdown is owned by the pipeline
component reference.

{#pipeline}
## Pipeline

A named, ordered composition of [nodes](#node) — handler entries and
composition entries (per the `nodes` array's `kind` discriminator) —
forming the typed dataflow [graph](#graph) the engine
[type-checks](#type-check) at load and dispatches at run. A pipeline
declaration declares the node sequence, supplies their [service](#service)
bindings, and supplies each handler's
[compose-time bindings](#compose-time-binding) (inline, via external
declaration file path, or as an explicit null). The pipeline is the unit of
[composition](#composition) and the unit the engine validates,
hashes, and projects — one pipeline = one composition identity = one
set of [trainable-channel](#trainable-channel) projections.

{#glossary-pipeline-as-training-contract}
## Pipeline-as-training-contract

In the engine, a composed pipeline is
simultaneously the runtime contract AND the training-data shape.
Editing the composition re-derives both — they are not two contracts
kept in sync but two [derived views](#derived-view) over one
[graph](#graph). The trained artifact is the
[materialized derived view](#materialized-derived-view) of running
the contract through training.

{#pipeline-contract}
## Pipeline contract

The shape a pipeline declares to its consumer — declared inputs, declared
outputs, ordered nodes (handlers and compositions), service bindings. The
pipeline contract is what the engine validates at compose time and what the
consumer integrates against. Distinct from the
[training contract](#training-contract), which is derived from the pipeline
contract.

{#pipeline-hash}
## Pipeline-hash

A stable identifier covering every input that affects pipeline
composition — the identity of the full [graph](#graph), shifted by
any [composition change](#composition-change). Sibling to
[training-bundle-hash](#training-bundle-hash) (the per-trainable
training-record-shape identity); together the two hashes implement
the [pipeline-as-training-contract](#glossary-pipeline-as-training-contract)
integrity property — pipeline-hash is whole-composition replay
identity, training-bundle-hash is per-trainable bucketing identity.
The pipeline-hash is recorded on the
[trained-artifact manifest's](#trained-artifact-manifest)
`pipeline_hash_set` so a deployed artifact can detect the composition
it was trained against.

{#pipelinefailure}
## PipelineFailure

The [error class](#error-class) wrapping every runtime failure that is not
a [ContractViolation](#contractviolation) or
[SchemaValidationError](#schemavalidationerror). It carries two discriminating
fields — the structural locus and the underlying cause:

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

`cause_class` carries the underlying Python exception type verbatim (e.g.,
`TimeoutError`, `ConnectionError`). Halts pipelines for
channel-writing nodes; the [hook](#hook) carve-out is
[R-error-channel-003](#R-error-channel-003)'s two-case rule.

{#preprocessor}
## Preprocessor

An engine-semantic `[[preprocessors]]` sequence entry inside a
[trainable TOML](#trainable-toml) — a regular handler, dispatched in
declared order, whose membership in the trainable composition's scope is
structural (not advisory metadata). Its
hash contributes to the trainable composition's own hash and therefore
to the [training-bundle-hash](#training-bundle-hash). Distinct from a
postprocessor, which lives outside the trainable composition in the
embedding pipeline and contributes only to the
[pipeline-hash](#pipeline-hash).

{#projection}
## Projection

The runner's act of populating a handler's declared [input-port](#input-port)
kwargs at dispatch — for each input port, reading the [channel](#channel) its
node's [read-map](#read-map) wires it to from accumulated channel values and
supplying the value under the port name — and, more generally, the act of
deriving a view of the [graph](#graph) by selecting some of its channels. The
dispatch surface is exhaustively the handler's kwarg signature plus its return
dict; projection is how channel values reach kwargs at the [node's](#node)
position without exposing the rest of state to the handler body. The same
vocabulary applies at the training layer: the
[training contract](#training-contract) is the projection of the
graph at every [trainable channel](#trainable-channel).

{#query}
## Query

A consumer-side projection / filter / aggregation against captured
channel records — the [canonical event](#canonical-event) streams
(`service_invocation` + `handler_exit` for service-kind dispatches;
`handler_enter` + `handler_exit` for [trainable](#trainable)
composition node dispatches). Consumer-territory under the
engine / consumer / review partition:
the engine emits provenance with sufficient payload (paired for the
same dispatch via [correlation ID](#correlation-id)
on service-kind events, or via `pipeline_run_id` +
`handler_position` on either kind's pair); analysis of the
captured records — including divergence detection between paired
events — is the consumer's concern. Distinct from the engine-internal
"queries against one graph" framing in
pipeline-as-training-contract,
which names how the runtime contract and training-shape contract are
both [derived views](#derived-view) over one compose-time graph; the
glossary entry here names the consumer-facing operation on captured
records.

{#read-map}
## Read-map

The node-level inline table wiring a [handler's](#handler) [input ports](#input-port)
to graph [channels](#channel) — the `reads_map` field on a `kind = "handler"`
[node](#node) entry, an optional table of `<input_port_name> = "<channel_name>"`
pairs (DATA only: the value is a plain channel-name string, never a callable,
expression, or external-declaration file path). Identity (port name = channel
name) is **surface sugar**: an omitted or partial map desugars each unmapped
input port to a same-named channel at the single compose-time normalization
step (an empty map = all-identity = reads exactly like an unwired node). After
normalization every input port carries an explicit channel; an unmapped port
whose required channel is neither written upstream nor declared in `inputs` is
a loud compose-time [ContractViolation](#contractviolation). At dispatch the
runner [projects](#projection) each input port's wired channel into a kwarg.
Paired with [write-map](#write-map).

{#remediation-hint}
## Remediation hint

The `remediation_hint` field. A short actionable string guiding the consumer toward
the corrective action (e.g., `"add [reads] section header; empty body acceptable"`),
populated by the ContractViolation Remediation Dictionary. Its audience is the consumer
surface: consumer tooling (notably Studio) surfaces it alongside the error so a non-coder
author can act on the failure without reading a stack trace.

{#single-sourcing}
## single-sourcing

The structural mechanism by which one canonical doc renders into multiple
audience surfaces — the integrator HTML site (Sphinx) and the in-package
agent surface (filtered build hook). single-sourcing replaces the disciplinary
"keep these in sync" obligation with a generated artifact whose freshness
is enforced at build time.

{#replayability}
## Replayability

(replayability-kernel-property)=

The kernel property that **same inputs and same service responses produce
the same final state**. A pipeline run is pure with respect to the external
world: reads are observations; writes only affect state within the run.

It holds because transforms are pure, [services](#service) are atomic and
contract-bounded, and the runner routes channels deterministically — so
*given the same service responses*, the run reproduces.

{#review-enforced}
## Review-enforced

An [enforcement mode](#enforcement-mode) in which a rule holds at the
handler-body layer — where the runner cannot see — and is caught by
adversarial review. Review-enforced rules carry
`enforcement: review` in their metadata.

{#schemavalidationerror}
## SchemaValidationError

The [error class](#error-class) raised when a value within a declared
field fails its **shape** — wrong type, validator failure, regex
mismatch, out-of-set enum value. Applies at both ends of dispatch: a
kwarg projected into the handler from accumulated state can fail its
`reads` schema, and a value returned from the handler can fail its
`output_schema` schema. Halts the pipeline. The error sits between
[ContractViolation](#contractviolation) (declaration-existence
mismatch) and [PipelineFailure](#pipelinefailure) (everything else):
ContractViolation concerns the *set* of declared keys; SchemaValidationError
concerns the *value within* a declared key. Which class a key-set fault
takes at each boundary is
[the routing the error-channel reference owns](#R-error-channel-001-key-set-routing).

{#scoped-channel}
## Scoped channel

A [channel](#channel) declared inside a
[composition TOML](#composition-toml) — local to that composition;
post-flatten the channel qualifies to
`<composition_name>.<channel_name>` and cannot be written or read by
handlers outside the composition's scope. The composition's explicit
`inputs` / `outputs` boundary is the only contact point with the
embedding pipeline. Eliminates cross-scope merge cases structurally
— an outer pipeline's `merge` cannot reach into an embedded
composition's internal channels and vice versa.

{#server}
## Server

The Python process that serves the engine's [API](#glossary-api). The server is
the engine's public surface — the engine ships a server process, and
consumers in any language drive pipelines over the wire. The first-party
`conjured` Python client wraps a bundled localhost subprocess so Python
consumers get an import-and-use experience without a separate Python API
contract. When transport matters, qualify as `http-server`,
`websocket-server`, etc.; bare "server" is fine when transport is
incidental. See [API contract](#api-contract).

{#service}
## Service

The [handler kind](#handler-kind) occupying the **external-edge node**
[node role](#node-role): makes exactly one
external call per dispatch and writes the result onto its declared
`output_schema` channels. The "exactly one" property — the
**atomicity rule** — preserves
[channel-record correspondence](#channel-record-correspondence) at
the runtime-contract layer: each dispatch maps to one captured
`service_invocation` [canonical event](#canonical-event). Semantic
retry (call → critique → call again) is forbidden because it would
collapse multiple distinct external interactions under one captured
invocation, corrupting the training projection. Services bind to a
[service type](#service-type) supplied at pipeline level; the call
reaches the backend through the
[service-type adapter](#service-type-adapter), which is the
structural locus of [adapter-boundary capture](#adapter-boundary-capture).
The binding's [identity](#identity-service-binding) values contribute
to the pipeline-hash; [transport](#transport) values do not. Examples:
LLM call, vector search, DB read.

{#service-type}
## Service type

A declared contract for an external dependency — what the engine
expects to call (identity schema, transport schema, config schema). Service types
are declared, entry-point-registered, and resolved by qualified
name at pipeline-declaration load. Multiple implementations may
satisfy one service type; swapping implementations is a
pipeline-declaration edit. Each service-type carries a
[service-type adapter](#service-type-adapter) — the engine's wrapper
around the backend call — where canonical events are captured at the
structural boundary the handler body cannot reach. Trainability is a
property of the [trainable](#trainable) composition kind.

{#service-type-adapter}
## Service-type adapter

The engine's wrapper around the backend call for a given
[service type](#service-type) — the structural seam between author
code and the backend SDK. For a [service](#service)-kind handler the
seam sits between the handler body and the SDK: the adapter
serializes the body's `services.<name>.invoke(...)` call arguments
to the backend's protocol, issues the call, deserializes the
response into the typed result — translation, never a verdict — and
returns it back to the handler body. For a [trainable](#trainable)
composition node dispatch there is no handler body — the engine
partial-applies the trainable's bindings into the dispatch wrapper
and calls `adapter.invoke` directly; the adapter boundary IS the
entire seam. The seam is load-bearing for canonical event
provenance: the engine captures `service_invocation` events at the
adapter boundary for service-kind handlers (structurally outside the
body's reach — see [adapter-boundary capture](#adapter-boundary-capture));
the trainable composition node's training capture is the
`handler_enter` / `handler_exit` pair that brackets the same boundary.
The adapter is also the structural locus of the "no SDK imports
inside handler bodies" discipline — service-kind bodies reach the
backend only via `services.<name>.invoke(...)`, which lands at the
adapter.

{#servicesproxy}
## ServicesProxy

The runtime object the runner constructs at dispatch and supplies to a
[service](#service) handler (and to [hooks](#hook) routing through
service-typed bindings) as the [services kwarg](#services-kwarg). The
proxy carries one attribute per entry declared in the handler's
`service_bindings`; each attribute exposes an `invoke(...)` method
implementing the bound [service type's](#service-type) call shape. The
proxy is the only handler-side surface for service invocation — direct
imports of backend SDKs are forbidden inside handler bodies.

{#services-kwarg}
## services kwarg

The reserved kwarg name on the Python signature of a handler that
declares a service-typed binding. The runner constructs a
[ServicesProxy](#servicesproxy) at dispatch and supplies it as
`services=<proxy>`. Only [service](#service) handlers and
[hooks](#hook) routing through service-typed bindings declare the
kwarg; [transforms](#transform) MUST NOT.

{#silent-fallback}
## Silent fallback

A handler outputting a schema-valid value that does not reflect the
handler's runtime derivation — a value emitted to mask internal failure
rather than as the outcome of the handler's actual work.

A derived value that happens to match a sentinel or default is **not** a
silent fallback: a validation handler returning `passes: true` because
its checks ran and all passed is deriving from runtime; the same handler
returning `passes: true` because its internals raised and `true` was the
default IS a silent fallback.

Forbidden categorically (not instance-by-instance), because a silent
fallback corrupts the captured [training contract](#training-contract)
record — claiming the handler produced a value for an input when the
handler actually failed.

{#sole-admission-gate}
## Sole admission gate

The `output_schema` discipline: a handler's `output_schema`
declares its [output ports](#output-port) — no more and no less; the
node's [write-map](#write-map) binds each port to the channel it
writes. An
undeclared key in a handler's return dict is a
[ContractViolation](#contractviolation) at handler exit; a declared
key omitted from the return dict is the same. The gate is the
engine's only path for admitting values onto graph channels: there is
no side-channel write surface, no implicit channel, no
metadata-tucked-into-return-dict route. The discipline plus the
engine-constructed dispatch wrapper
makes invariant I1's "no implicit contracts" claim mechanically
enforced at the dispatch boundary rather than disciplinary.

{#state}
## state

The per-run map of channel values the runner maintains as a
[pipeline](#pipeline) runs. State lives in the runner's closure; each
handler, at dispatch, receives its declared [input ports](#input-port)
populated by [projection](#projection) — each port's value read from the
channel its node's [read-map](#read-map) wires it to — into kwargs (plus
the `services` kwarg where service-typed bindings are declared), and the
runner routes its validated return, via the node's [write-map](#write-map),
onto the graph's [channels](#channel) for downstream nodes to read.

A handler's dispatch surface is exhaustively its kwarg signature plus
its return dict — the handler does not see state as a threaded
mutable object. Compose-time bindings (`bindings.<name>`) are
fixed at compose and delivered as a fresh per-dispatch copy, not
threaded as runtime state.

State is per-run: each pipeline invocation starts fresh; nothing
persists across runs by way of state. Cross-run persistence lives in
[services](#service) or in consumer orchestration.

{#steering}
## Steering

The agent-facing doc category. Conjured's canonical content separates
the Diátaxis modes onto two planes — a **reference** plane (facts /
rules / grammar) and a parallel **explanation** plane (the "why"); the
pipeline-as-training-contract page, for one, is an **explanation-plane**
doc, not a reference doc. (The teaching Diátaxis modes — how-to, tutorial — are out of
canon scope.) Steering content is agent-facing — short, imperative notes that
prevent an agent from reaching for a familiar paradigm where the
engine's discipline diverges. Steering files are for the agent surface
only — never the integrator HTML site; each renders into the
in-package surface with its owning canonical content extracted
verbatim (the doc's `renders_from` anchor), so the steering layer
cites canon rather than restating it.

{#studio}
## Studio

The first-party authoring + observation tool that ships alongside the
Conjured engine. Architecturally a downstream [consumer](#consumer):
Studio drives the engine via its [wire API](#api-contract) and receives
the [canonical event](#canonical-event) feed by attaching its own
`logging.Handler` to the engine's `conjured.events.runner` logger (or
by subscribing to the server's wire-projected events out-of-process).
The engine does not ship Studio-specific code; Studio implements its
own event-consumption logic against the engine's canonical event log.
Functionally load-bearing: Studio is the canonical realization of
Tenet 1 (composability by non-coders) — the
**authoring view** of the typed dataflow graph for non-coders,
rendering kinds and channels and bindings in terms the user-as-author
can reason about directly. The engine ships the runtime substrate
(handlers, pipelines, TOML schemas, dispatch); Studio ships the human
surface (authoring UI, pipeline composition, run replay backed by
[replayability](#replayability)). Tenet 1 is realized by the engine +
Studio together; the engine alone is necessary but not sufficient.

**Packaging:** Studio ships as a separate PyPI distribution
(`conjured-studio`), sibling to `conjured`, rather than as a subpackage
inside the engine. The separation preserves the engine's "drive from
any language" wire contract and keeps the engine boundary narrow per
I3 (engine purity); the co-shipping preserves Tenet 1.

{#conjured-utils}
## conjured-utils

The first-party companion utilities package — a downstream [consumer](#consumer),
sibling to Studio — holding the blessed reference implementations of the I/O-bearing
mechanisms the pure engine deliberately does not ship: the training-log sink,
non-trainable service adapters, and reference deployment configs. It depends on
`conjured`, never the reverse (the engine never imports its companion). **Packaging:**
a separate PyPI distribution (`conjured-utils`), sibling to `conjured`; an optional but
strongly-recommended companion — bare `conjured` runs standalone (an integrator may
bring its own sink), keeping the engine boundary narrow per I3 (engine purity).

{#tenet}
## Tenet

A design goal that frames why the [invariants](#invariant) exist. Unlike
an [invariant](#invariant) — which the engine mechanically enforces — a
tenet is not enforced: it is honored or drifted from, and informs
judgment calls when the invariants underdetermine an answer.

{#test-double}
## test double

The Fowler/Meszaros umbrella term for any stand-in substituted for a real
collaborator under test. The taxonomy, by what the double does:

- **Dummy** — passed only to satisfy a signature; never actually used.
- **Stub** — returns canned answers to the calls made during the test
  (indirect input); no verification.
- **Spy** — a stub that also **records** how it was called, for the test to
  assert against afterward.
- **Mock** — pre-programmed with expectations and **verifies** the calls it
  received (interaction verification) — its job is checking *how* it was called.
- **Fake** — a working implementation with real but shortcut behaviour
  (contract-satisfying, deterministic, unfit for production); see
  [fake service](#fake-service).

Conjured's sanctioned substitution swaps in **fakes** at the
[service-type adapter](#service-type-adapter) seam via compose-time **twin**
substitution (the handler reference's § Test substitution) — never
interaction-verifying mocks, never runtime monkeypatching. The mock/fake word never
carries the training-capture distinction: whether capture fires is determined by
composition kind (that same § Test substitution owns the split), not by which kind of
double is bound.

{#trainable}
## Trainable

A [composition kind](#composition-toml) specialization in the
engine-owned-dispatch family;
a trainable in a pipeline is a **trainable composition node**,
declared by embedding a [trainable TOML](#trainable-toml) (with
`meta.kind = "trainable"`) at a `kind = "composition"` entry in the
outer pipeline's `nodes` array. Structurally distinct from
[handler kinds](#handler-kind) on the load-bearing axes: the engine
constructs the dispatch directly against the bound trainable backend
(no author body; the construction is owned at
[R-handler-010](#R-handler-010-no-author-body));
[scoped channels](#scoped-channel) at the composition boundary with
explicit `inputs` / `outputs`; own hash domain at the composition
boundary (the trainable composition's hash IS the
[training-bundle-hash](#training-bundle-hash)); the
[literal-equal rule](#literal-equal-rule) on the trainable channel's
declared type.
The trainable composition kind's `trainable.service_bindings` MUST declare exactly one
service-typed entry, and the bound implementation MUST be a [trainable backend](#trainable).
Emits `handler_enter` +
`handler_exit` only — no `service_invocation`; the
engine-controlled boundary needs no adapter-boundary safety net.
A trainable composition whose `[trainable]` node declares `streamable = true` MUST be the
pipeline's terminal node — only hooks (which write no channels) may follow it. Any non-hook node
downstream of a streamable trainable raises ContractViolation. Terminal position is evaluated
**transitively through a terminal nested `pipeline` embed**: a streamable trainable that is
terminal inside a nested `pipeline` which is itself the enclosing pipeline's terminal node
satisfies the rule, and any non-hook node downstream of it — at any nesting layer — raises
ContractViolation.
See
[trainable TOML](#trainable-toml) for the declaration grammar.

{#trainable-channel}
## Trainable channel

(trainable-channel-emission-locus)=

The canonical unit of trainability — a [channel](#channel) emerging
from a [trainable](#trainable) composition node's `output_schema`. A
[service](#service)-kind node calling the same backend does not emit
trainable channels; training capture requires the trainable
composition kind.

The
granularity unit on which the
[literal-equal rule](#literal-equal-rule) applies (the channel's
declared type equals the backend's structured-output constraint),
and on which the [training contract](#training-contract) projects
(the corpus's training-record shape at this position IS the
channel's declared type). Sibling granularity: the
[training-bundle-hash](#training-bundle-hash) is per-trainable, not
per-channel — one hash per trainable composition, covering all
channels the trainable emits. The
per-channel framing for the literal-equal rule and training-record
shape generalizes to multi-channel-trainable compositions without
manifest format shift.

{#trainable-toml}
## Trainable TOML

The `kind = "trainable"` specialization of the
[composition TOML](#composition-toml) primitive — pipeline-shaped,
with `meta.kind = "trainable"`, an explicit `inputs` / `outputs`
boundary, [scoped channels](#scoped-channel), a declared
`[[preprocessors]]` sequence of [preprocessor](#preprocessor) entries
(each a regular handler, dispatched in declared order), and exactly
one terminal `trainable` node. The closed grammar — the section set,
the terminal node's subsections, and the optional `streamable`
delivery selector — is owned by R-handler-006 (closed
handler-declaration shape grammar) and the machine-readable
`trainable.schema.toml`. The trainable TOML's own canonicalized hash
IS the [training-bundle-hash](#training-bundle-hash).

{#training-bundle-hash}
## training-bundle-hash

The per-composition content-semantics identity for **engine-owned-dispatch**
composition kinds — the [trainable](#trainable) composition kind, and any other
engine-owned-dispatch kind following the same per-composition
pattern. One training-bundle-hash per such composition node, covering that
composition's own declaration (the training-record-shape identity). Corpus
consumers wanting "training data of compatible
content semantics" use the bundle-hash; full-pipeline replay identity
uses [pipeline-hash](#pipeline-hash). Provenance-invariance: a
merge-strategy change upstream of a trainable shifts pipeline-hash
but NOT training-bundle-hash (the trainable's contract is unchanged
given the same channel values). The canonical event firing on shift
is `training_bundle_hash_changed`.

{#training-contract}
## Training contract

The shape — schemas, field types, training-pair structure — that the
[trained artifact's](#trained-artifact-manifest) fine-tuning data must
conform to. The training contract is a [derived view](#derived-view)
of the [pipeline](#pipeline) contract, projecting at every
[trainable channel](#trainable-channel).

{#trained-artifact-manifest}
## Trained-artifact manifest

A sidecar TOML adjacent to a fine-tuned artifact (e.g., a LoRA) — the
wire form of the artifact-as-[materialized derived view](#materialized-derived-view).
The manifest records a `pipeline_hash_set` (the set of
[pipeline-hashes](#pipeline-hash) the artifact's training corpus
spans) and per-[trainable](#trainable)
[training-bundle-hashes](#training-bundle-hash). At load time the
engine compares the deployed pipeline's pipeline-hash against the
manifest's set as a membership check, and the deployed
training-bundle-hashes against the manifest's. Mismatches always
fire `training_bundle_hash_changed` / `pipeline_hash_changed`
canonical events; whether the mismatch additionally halts load
depends on the deployment's [integrity enforcement](#integrity-enforcement)
opt-in. The set shape on `pipeline_hash_set` is what allows a single
artifact to serve multiple pipeline compositions sharing
training-bundle-hashes but differing in non-bundle-affecting
composition details.

{#transform}
## Transform

The [handler kind](#handler-kind) occupying the **pure internal node**
[node role](#node-role): deterministic, no
external runtime resource, returns its computed output as a dict for
the runner to route onto declared channels. Transforms receive
declared `reads` as dispatch-time kwargs and may carry
`bindings.<name>` declarations as
[compose-time bindings](#compose-time-binding); they never invoke
services. Examples: charset-filter normalizer, NPC import, response
packaging, structured-data shaping.

{#transport}
## Transport

The deployment-specific fields of a [service](#service) or [hook](#hook)
binding — endpoint URL, [secret references](#secret-reference), timeouts,
headers. Transport values
live in the deployment declaration, are NOT contributed to any hash, and
may change per environment without affecting the pipeline contract or
training contract. Distinguished from
[identity](#identity-service-binding), which is hashed.

{#secret-reference}
## Secret reference

The whole value of a `secret_ref`-declared [transport](#transport) field:
a `[scheme]payload` instruction for *where* the consuming implementation
fetches a credential at dispatch, never the credential itself — a raw
secret value never appears in any declaration file. The engine validates
the reference's shape at pipeline-declaration load and forwards it
opaque; it never fetches. Owned by the deployment reference's § Secret
references — the grammar, the `env`/`file` built-ins, and the dotted
consumer-resolver arm are [R-deployment-003](#R-deployment-003)'s.

{#trust-model-vector}
## trust-model vector

One of a closed enumeration of ways author code could carry hidden
state — or otherwise mutate engine-controlled state across dispatches —
and break the engine's pure-dispatch model. Each vector pairs a named
threat with a **structural seal**, a scope, and an audit — most making the
violation impossible by construction; [which seals carry a documented
qualification](#the-vector-inventory-qualified-seals) is stated with their
vectors in the trust-model inventory. The threat model is
**accidental** breakage by a trusted author, not adversarial /
multi-tenant / sandbox isolation (a forward-design scope, not current).
Examples: the closure-factory pattern (vector 1) and above-instance-scope
mutable state in adapter modules (vector 7).

{#type-check}
## Type-check

Compose-time validation of [channel](#channel)-type compatibility
across declared `reads` and `output_schema` declarations. The
engine's I2 (determinism under composition)
property — schemas match or fail at load. The type-checker verifies
that every channel's write-type and its downstream read-type are the same
declared type (exact match — no subtype widening, mirroring service-type
resolution's strict qualified-name equality),
every binding resolves, every declared field constraint is honored, every kind
discipline holds (e.g., a [trainable](#trainable) composition node's
one service binding MUST be a trainable backend per [R-handler-008](#R-handler-008);
a channel with two or more contributors requires a `merge` declaration per
R-pipeline-002).
A pipeline that loads is a pipeline whose [graph](#graph)
type-checks; a graph that fails to type-check raises
[ContractViolation](#contractviolation) before any handler
dispatches.

{#validator}
## Validator

A field declaration attaches **named value constraints** beyond its type token, and they
share **one grammar**: every non-structural field key is a validation keyword, in two classes
the key itself distinguishes — a **bare** key (no dots) is a built-in standard constraint (a
JSON Schema validation keyword applicable to the field's declared type), and a **namespaced
(dotted)** key is a registered third-party validator. There is no separate `validators` list.
A namespaced validator registers under the `conjured.validators`
[entry-points group](#entry-points-group). The handler reference's
§ Validators owns the full grammar and the validator contract
([R-handler-012](#R-handler-012), [R-handler-013](#R-handler-013)).

{#write-map}
## Write-map

The node-level inline table wiring a [handler's](#handler) [output ports](#output-port)
to graph [channels](#channel) — the `writes_map` field on a `kind = "handler"`
[node](#node) entry, an optional table of `<output_port_name> = "<channel_name>"`
pairs (DATA only: the value is a plain channel-name string, never a callable,
expression, or external-declaration file path). Identity (port name = channel
name) is **surface sugar**: an omitted or partial map desugars each unmapped
output port to a same-named channel at the single compose-time normalization
step (an empty map = all-identity = writes exactly like an unwired node). The
runner applies the write-map AFTER it validates the return dict against the
declared output-port shapes — routing the validated, port-keyed values onto
channels — so the handler cannot name the channel it writes, and
read-then-rewrite is structurally impossible. Paired with [read-map](#read-map).
