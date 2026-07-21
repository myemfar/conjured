---
kind: explanation
audience: [authors, integrators]
slug: pipeline-reference-explanation
explains: ../components/pipeline/reference.md
---

{#pipeline-reference-explanation}
# Why the pipeline component is shaped this way

The [pipeline reference](#pipeline-reference) states the kernel
semantics, the pipeline-TOML grammar, the merge registry, the load lifecycle, the
derivables bundle, and the manifest field set. This doc carries the *why* behind
three of the component's design choices: that the engine dispatches one pipeline
per invocation and orchestrates nothing across invocations; that the engine computes
a second hash alongside the pipeline-hash; and that a pipeline run is replayable. The
*why* behind the training-contract derivation itself — the engine's one-graph /
two-derived-views claim — lives in
[pipeline-as-training-contract](#pipeline-as-training-contract-explanation); this doc is the
reference-half companion.

---

{#why-the-engine-doesnt-orchestrate}
## Why the engine doesn't orchestrate

The engine API accepts exactly one `(pipeline, inputs)` pair per invocation: one
typed dataflow graph, one dispatch, one output or one error. The
[reference half's orchestration-scope section](#orchestration-scope)
states the fact and lists the orchestration shapes a consumer assembles on top.
This is *why* the engine stops at one pipeline rather than absorbing those shapes
itself.

The kernel is a linear reduce over a fixed `nodes` list. Engine-level retry,
fan-out, or **runtime-conditional** branching would each require the engine to hold
**invocation-scoped decision state** — retry counts, branch-completion tracking,
which-pipeline-to-fire-next — across what is currently a single straight-line pass.
That state is a runtime quantity: it makes the executed graph depend on values seen
at run time, so the engine could no longer verify the composition before it runs
([I2 — determinism under composition](#invariants-and-derived-rules)). Adding it is
not an opportunistic feature; it is a load-bearing change to the kernel's shape, and
it would reintroduce exactly the runtime branch state the
[per-run kernel](#kernel-semantics) is built to exclude. The property that makes a
pipeline run replayable is the same property that makes the engine refuse *runtime*
orchestration: a runtime decision has no place to live.

So *runtime* coordination lives where runtime decision state already lives — the
consumer layer. (Static, compose-time-known nesting is a different thing: the
[nested `pipeline` kind](#nested-pipeline-kind) embeds sub-pipelines whose
structure and depth are fixed at compose, so the whole graph still type-checks at
load — no runtime decision, no excluded state. The line is **conditionality, not nesting**.)
This is the [engine / consumer / review partition's](#engine-consumer-review-partition)
duplication-collapse test applied to orchestration: a capability the consumer must
own anyway (it holds the cross-invocation state) is not also built into the engine,
because building it twice is the duplication the partition forbids. The single-pair
API is therefore a *positive* constraint — the per-invocation purity is precisely
what gives the consumer a clean substrate to build any orchestration shape against,
not a ceiling on what the consumer can achieve.

---

{#why-a-second-hash}
## Why a second hash

The [pipeline-hash](#pipeline-hash) answers one question:
*did the composition change?* Any composition edit shifts it. But composition changes
vary in their consequences for a trained artifact, and the pipeline-hash cannot tell
those consequences apart. A binding-value edit on a bare-function handler shifts the
pipeline-hash without altering the training-record shape any trainable composition
node emits. A service-binding identity edit (a different model name in a pipeline-level
`service_bindings`) shifts the pipeline-hash without changing the trainable composition
declaration it bound against. A `merge`-strategy change shifts the pipeline-hash but
leaves every trainable composition's declaration untouched — merge is
provenance-invariant value-plumbing, so the same channel values still reach the
trainable node regardless of the derivation path.

Why a single composition-identity hash is too coarse to tell those consequences apart —
and why two hashes bucketing a drifted artifact along the two independent axes is the
resolution — is owned by [hash-model § Why two hashes, not
one](#why-two-hashes-not-one). The pipeline-component payoff is what the
[training-bundle-hash](#training-bundle-hash) buys at load: computed per trainable
composition node over that composition's own declaration, it shifts only when the
training-record shape at *that* node changes — so a consumer adjudicates a drifted
artifact at load with the two distinct per-axis checks, and the graduated force each
outcome carries, that
[hash-model § Bucketing semantics](#bucketing-semantics-pipeline-hash-vs-training-bundle-hash)
and the [integrity-enforcement opt-in](#integrity-enforcement-opt-in) own, rather
than one blunt composition-changed signal.

---

{#the-replayability-rationale}
## The replayability rationale

:::{transclude} replayability/kernel-property
:::

The guarantee is *conditional on service responses* — it is not a claim of
unconditional determinism.

{#the-mechanical-basis}
### The mechanical basis

Two structural properties and one review-held discipline collectively ensure no
hidden cross-run dependency can
affect a pipeline's output.

**Per-run kernel.** Each pipeline invocation starts from a fresh state map; the engine
threads no state between invocations. Cross-invocation persistence lives in services
or in consumer orchestration — structurally outside the kernel's reach. (This is the
same property that makes the engine
[unable to orchestrate](#why-the-engine-doesnt-orchestrate): there is no cross-run
state to carry.)

**Declared-only channels.** Every value the kernel routes between handlers is declared
in a handler's `output_schema` and received downstream via `reads`; the
[sole admission gate](#sole-admission-gate) rejects any
undeclared key at handler exit. No undeclared side channel exists, so a replay
presenting the same declared inputs encounters the same typed channel values at every
node position.

**Service halt-on-failure.** Services halt on every error class; the engine provides no
fallback-to-default for service handlers (per
[R-error-channel-003](#error-channel-derived-rules) halt
semantics), and the no-silent-fallbacks rule
([R-handler-002](#handler-derived-rules)) holds the handler-body half — a
**review-enforced** discipline, not a structural seal: a body that catches the
adapter's error and returns a default is invisible to the runner, which is why the
rule's enforcement is review (with the wire-visible masking signature as its
evidentiary backing). Together they defend against the failure mode where a
service silently substitutes a default and two invocations' states diverge with no
visible signal — the halt path removes the engine's side of it structurally; review
holds the body's side.

{#the-scope-of-same-service-responses}
### The scope of "same service responses"

The conditional is load-bearing. Replayability is *not* a claim that a pipeline
produces the same output across two real invocations; it is a claim that *given the
same service responses*, the pipeline deterministically produces the same final state.

LLM services with nonzero temperature do not reproduce their outputs across
invocations — a well-known LLM property, not a Conjured carve-out. Two runs of the same
pipeline against a live LLM will typically produce different states because the LLM's
responses differ, and that is expected: natural-variance runs are training signal, not
errors. The conditional has three practical consequences:

- **Consumer testing with [fake services](#fake-service)** is
  meaningful — swapping a fake (deterministic response) for a production service at the
  qualified-name boundary preserves pipeline behavior given the same response payloads.
- **Replayability tests against real LLM services** are valid only under
  `temperature=0` with matched sampling parameters.
- **Training-data generation from captured runs** is deterministic — replaying a
  captured run (same input channel values, same recorded response payloads) produces
  the same training records the original run produced.

{#what-replayability-enables}
### What replayability enables

Replayability is the foundation several downstream capabilities rest on. The
property itself stays in engine canon; the applications below are the capabilities it
enables.

| Downstream capability | Basis |
|---|---|
| Studio state-trace — replay a captured run deterministically | Same declared inputs + same captured service responses → same channel values at every node position |
| Deterministic training-data generation from captured runs | Replay of captured run events produces the identical training corpus |
| Consumer testing with fake services | Fake-service swap at the qualified-name boundary preserves state behavior given the same response payloads |
| Pipeline-hash stability as a composition identifier | "Same pipeline declaration composition" is a special case of "same inputs" — same declared composition → same [pipeline-hash](#pipeline-hash) across machines (engine-version changes that alter the hash construction are explicitly outside the promise — the hash-model owns that boundary) |
