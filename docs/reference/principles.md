---
kind: rules
audience: [authors, integrators, agents]
slug: principles
---

{#principles}
# Principles

This file owns the engine's Invariants (the load-bearing axioms), its Tenets
(the design goals that frame them), and the rubric that partitions engine /
consumer / review concerns. Each invariant carries a `rule_id` of the form
`I<N>` and each tenet a `rule_id` of the form `T<N>`; a derived rule traces
**up** to the invariant(s) or tenet(s) it protects via `derived_from`.

Derived rules live alongside the component reference they govern; components
trace **up** to these invariants and tenets via `derived_from`. This file holds the
invariants and tenets they trace back to, and does not cite leafward to them.

{#audiences}
## Audiences

The canonical docs serve distinct audiences, tagged per-doc via the `audience:`
frontmatter field and kept structurally separate:

- **Author** — the non-coder composing handlers into pipelines (the target user;
  see Tenet 1). Not a software engineer.
- **Coding agent** — an agent reading the canonical docs to help an author
  compose, extend, or debug (see Tenet 2). "Agent" and "coding agent" are used
  interchangeably.
- **Integrator** — a consumer engineer who embeds the engine in a codebase:
  wires services, deploys, hosts the API, operates it.
- **Consumer** — the application or codebase that drives the engine via its API
  at runtime; distinct from the integrator who embeds it.
- **Builder** — an engine maintainer. Builder-internal discipline (the
  conformance gate, multi-session authoring, adversarial review) is not an
  engine invariant and is maintained outside the shipped doc set.

The `audience:` frontmatter enum is a deliberate **3-value coarsening** of this 5-role model: only the
canonical docs' *reader* roles are tagged — **`authors`** = the **Author** role (the non-coder building
pipelines); **`integrators`** = **Integrator**; **`agents`** = **Coding agent**. The other two roles take
no tag: **Consumer** is a runtime role (not a reader), and **Builder** (engine maintainer) is not a
reader of this shipped corpus.

{#corpus-scope}
## Corpus scope

All rules in this file bind **engine-conformant handlers** — handlers
that are declared, entry-point-registered, and admitted to the
graph via the engine's compose-time dispatch construction as
bare kwarg-only functions. The engine-constructed dispatch wrapper is the only path for
admitting a node into the typed dataflow graph; the engine has no
adapter surface for unregistered callables.

The rules below do not bind consumer Python code that drives the engine
via its API from the outside; that code is
the engine's consumer, not its dispatch surface, and is governed by the
engine/consumer boundary (see I3 below) rather than by handler rules.

Builder-internal discipline — the pre-merge conformance gate, multi-session
authoring methodology, adversarial review by an outside surface — is not an
engine invariant and does not appear in this file; it is maintained outside
the shipped doc set.

{#engine-consumer-review-partition}
## Engine / consumer / review partition

The corpus scope above names what the rules in this file bind and don't
bind. A sharper rubric explains *why* features land on the engine side
vs the consumer side vs the review side: the **duplication-collapse
test**. It is a meta-rule, not an invariant — it does not constrain
engine behavior, it constrains *which features the engine takes on*.

A proposed engine feature passes the test if it **collapses a
would-be-duplication that without the collapse would drift between its
copies**:

- I4 (pipeline-as-training-contract) collapses runtime-contract and
  training-contract into one graph that answers both queries — the
  schemas validating channels at runtime and the schemas defining
  training-record shapes are the same types.
- The service-type adapter collapses
  production-and-fake-contracts into one shared interface.
- The closed-enum handler kinds
  collapse what could have been many effect-categories into a
  mechanically-distinguishable enumeration.

A proposed concern lands on the **review** side (rather than the
engine side) if it asks the engine to enforce *honesty* about handler-
body behavior the type system cannot inspect:

- **No silent fallbacks** — review-enforced because a
  handler body deciding to suppress an exception and return a default
  is a body-level dishonesty the runner has no visibility into.
- **Transform purity** — same shape; body-level review
  catches what the dispatch wrapper cannot.
- **Import discipline** — body-level discipline catches
  shape-bypass attempts at the import surface.

(Which specific derived rules are review-enforced is each rule's own
`enforcement:` declaration, at its component reference.)

(Review-enforced rules ARE engine concerns — they carry rule-IDs and
live in the engine's corpus alongside mechanically-enforced rules. The
adversarial-review methodology used to verify them is operational
practice maintained alongside the engine.)

A proposed concern lands on the **consumer** side if it composes at a
scale the engine deliberately does not own:

- Multi-pipeline orchestration (retry-with-modified-inputs, branching,
  fan-out, dynamic/runtime-selected sub-pipeline composition — static
  compose-time-known nesting is the engine's nested `pipeline` kind, not
  this).
- Persistence, evaluation, deployment, behavioral comparison across
  graph edits.

Future architectural questions ("should the engine own X?") get
answered by running the test: does X collapse a would-be-duplication
(engine), ask for honesty about an opaque surface (review), or compose
at a scale outside the engine's contract (consumer)? The partition the
invariants and the corpus scope already enforce is the partition this
test names.

{#exceptions-to-rules}
## Exceptions to rules

Exceptions to rules in this file are enumerated within the rule that
admits them (e.g., the hook-wrapper sanction in `R-error-channel-003`).
Unnamed exceptions are not valid — carve-outs require revising the rule
explicitly (an engine change), not adding the exception in code or in a
subsidiary doc. The discipline prevents the rule corpus
from accumulating quiet carve-outs that hollow out the invariants.

---

{#tenets}
## Tenets

{#tenet-1-composability-by-non-coders}
### Tenet 1 — Composability by non-coders

`rule_id: T1`

The target user is not a software engineer. The user authors AI experiences
by composing handlers into a typed dataflow graph, assisted by templates,
agents, and authoring surfaces. **Studio is the authoring view of the
graph for non-coders** — rendering the typed dataflow graph in user-facing
vocabulary, exposing kinds and channels and bindings in terms the user-
as-author can reason about directly, rather than in the engine-internal
terminology authors and agents read. (Studio is
architecturally a downstream consumer co-shipped
with the engine; Tenet 1 is realized by engine + Studio together, not by
the engine alone. Studio is not a counterexample to I3 — it sits on the
consumer side of the engine/consumer boundary.) Engineering elegance that
does not serve non-coder authorability is wrong.

Because the non-coder author interfaces with the engine primarily **through
agents**, Tenet 1 is realized through Tenet 2: when a surface-shape call
trades human ergonomics against agent pattern-matching and steerability,
**agent legibility wins** — the agent is the author's hands, so the surface
an agent reliably gets right is the surface the author can actually use.

*Reflective check:* can a non-coder compose this correctly without reading
source?

{#tenet-2-legibility-to-agents}
### Tenet 2 — Legibility to agents

`rule_id: T2`

Coding agents are trained on common paradigms — procedural Python, ad-hoc
orchestration, schemas-as-validation rather than schemas-as-types-in-a-graph.
The engine's typed-dataflow framing is novel relative to those defaults;
agents will reach for procedural composition unless steered toward the
graph-and-channel model. Doc structure, vocabulary, and
steering content must converge agents on
engine-aligned solutions rather than defaulting to familiar patterns that
silently break composability.

*Reflective check:* would an agent trained on mainstream Python reach for
the right pattern, or would it need explicit steering? If the latter, the
steering must exist in the docs.

---

{#invariants-and-derived-rules}
## Invariants and derived rules

:::{region} rule-id-format/kernel
Multi-rule block. Every rule carries a `rule_id` and a short
lowercase-noun-phrase `name` (the rule_id is the grep target; the name is
the prose label, e.g., "closed-enum handler kinds"). Invariants (`I<N>`)
and tenets (`T<N>`) are axiomatic and carry no `enforcement` field. Derived rules
(`R-<component>-<id>` — `<id>` a number or a stable descriptive slug; either way the ID is
fixed for the rule's life) cite the invariant(s) or tenet(s) they protect via `derived_from` —
at least one — MAY additionally cite a derived rule they specialize, and
declare an `enforcement` mode of `mechanical` or `review`.
:::

The common case is
an invariant; a rule whose concern no invariant intermediates — a standard
wire-format legibility choice, say — grounds directly in the tenet it serves
(the agent-legibility goal of Tenet 2).

Invariants are ordered axiomatic-prerequisites first (I1, I2, I3) building
to I4; every other invariant exists to make I4
mechanically trustworthy.

```yaml
rules:
  - rule_id: I1
    name: no implicit contracts
    statement: |
      No implicit contracts. Every handler's interface is declared.
      Every channel in the graph carries a type derived from the
      interfaces of the handlers it connects — induced from the typed
      ports wired to it (and from the pipeline-boundary input/output
      declarations), never declared standalone. Every binding is
      declared. The engine admits no implicit channel, no undeclared
      field, no side effect bypassing the graph. Optional reads,
      lying defaults, silent fallbacks, hidden writes — forbidden as
      categories, not just as instances.

      Test: would a stranger's composition of these handlers type-check
      from the declarations alone?

  - rule_id: I2
    name: determinism under composition
    statement: |
      Determinism under composition. Type-checking is compose-time. The
      graph either type-checks at load — every channel's write-type and
      its read-type are the same declared type (every port wired to a
      channel declares the exact-same type; disagreement has no canonical
      type and fails to load), every binding resolves, every declared
      field constraint is honored — or fails to load. Every incompatibility the engine
      can statically detect surfaces before any handler dispatches.

      Test: can the engine verify this composition before it runs?

  - rule_id: I3
    name: engine purity
    statement: |
      Engine purity. The engine is the type-checker and dispatch
      runtime for the typed dataflow graph. Engine modules each hold
      one part of that job at a clean boundary. The engine/consumer
      boundary is one-way: consumers conform to the engine's API for
      authoring, composing, and dispatching graphs; the engine remains
      agnostic of consumer shape. Persistence, deployment orchestration,
      behavioral evaluation, end-user UX — operations on the graph's
      outputs, not on the graph itself — are consumer territory,
      downstream of the engine.

      Test: does the engine/consumer boundary hold if the consumer is
      replaced with something utterly different? Does each engine module
      hold its one job if the others are replaced?

  - rule_id: I4
    name: pipeline-as-training-contract
    statement: |
      The training corpus is a derived view of the graph. A composed
      pipeline is a typed dataflow graph; the training-corpus shape
      is the projection of that graph at
      **trainable channels** —
      the channels emerging from the declared output ports of trainable
      composition nodes. The types
      validating those channels at runtime are the same types defining
      the corpus's shape — not because two contracts are kept in sync,
      but because they are queries against the same graph. Change the
      composition and every query re-derives. No frozen assumptions,
      no pre-bound models at library ship time.

      Scope note: I4 governs the integrity of the training
      projection's shape, not the durability of every individual
      exported record (one transient I/O failure is statistical
      noise; exporting wrongly-shaped records is contract
      corruption), and not post-training behavioral equivalence
      across graph edits (a model trained against composition A may
      perform differently on composition B; I4 guarantees shape
      integrity at training time, not inference-time equivalence
      across drift).

      Test: does this decision preserve the property that an
      integrator can finalize a pipeline and immediately derive a
      matching fine-tuning dataset whose shape is correct, without
      external schema work?

  # Derived rules live alongside the component reference they govern and cite
  # up to these invariants via derived_from; this file does not cite leafward
  # to them.
```

---

{#fail-loud-stance}
## Failure stance — fail loud, never degrade

The engine **fails loud**: at any boundary where it cannot proceed correctly it rejects or
halts, rather than substituting a default, retrying into a masked success, or continuing in a
degraded mode. This is not a separate axiom — it is the failure-boundary expression of **I4**.
A gracefully-degraded run still emits a training record, and that record now lies: it presents a
substituted value as the handler's real output for its input. Because the training corpus is a
derived view of the run (I4), the only honest behavior at a failure is to stop and surface it,
never to paper over it with a plausible value the projection cannot distinguish from a genuine
one. **Graceful degrade is training-data corruption.**

The line is precision, not absolutism — the same line I4's scope note draws: a transient I/O
failure that drops a single exported record is statistical noise the engine tolerates; *silently
substituting a wrong-shaped or wrong-valued result* is the corruption the stance forbids. A
missing record is honest; a fabricated one is not.

The stance is not enforced in one place — it is realized by rules that each hold one boundary
against it: at declaration (a degraded path is not a declarable category), at
compose time (only a graph that type-checks is admitted to run), at the runtime
handler body (no silent fallbacks), and at the runtime channel (the run halts rather than letting
the channel carry a substitute). Each realizing rule owns its boundary and declares its own
derivation (its `derived_from`); this section owns only the
stance they share and its derivation from I4.

---

{#principles-reading-order}
## Reading order

Read the Tenets (the *why*), then the Invariants — the axiomatic
prerequisites building to I4. For an
integrator first encountering the engine, read I4
(pipeline-as-training-contract) first: every other invariant exists in
service of making I4 mechanically trustworthy. The failure stance (fail loud) follows
the invariants — the boundary behavior they jointly imply. From there, each component's
derived rules live with that component's reference.
