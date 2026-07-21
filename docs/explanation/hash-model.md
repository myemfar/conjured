---
kind: explanation
audience: [authors, integrators]
slug: hash-model-explanation
explains: ../architecture/hash-model.md
---

{#hash-model-explanation}
# Why the hash model

The [hash model](#architecture-hash-model) is the mechanical backbone of
invariant I4 (pipeline-as-training-contract), owned by
[principles](#invariants-and-derived-rules). I4 makes a
composed pipeline simultaneously the runtime contract and the training-data shape;
the two sibling hashes and the canonical event log are how that claim becomes
checkable instead of aspirational. This doc carries the motivating *why* behind the
[reference half](#architecture-hash-model)'s mechanics — why there are two
hashes, why the training-bundle-hash is user-declared, why trainable composition
nodes capture differently from service handlers, where the engine's commitment
ends, and why integrity enforcement is opt-in.

{#why-two-hashes-not-one}
## Why two hashes, not one

A single composition-identity hash would answer one question — "did anything in
the composition change?" — and that question is too coarse for the thing consumers
actually need to know. A consumer holding a fine-tuned artifact (a LoRA trained
against this pipeline) does not care whether *the composition* changed; it cares
whether *the training-record shape at the trainable it serves* changed. Those are
different questions, and most composition edits change the first without touching
the second.

So the engine computes two hashes that bucket a drifted artifact along the two
axes that matter independently:

- The **pipeline-hash** answers "did the composition change?" — full replay
  identity.
- The **training-bundle-hash** answers "did the training-record shape at this
  trainable change?" — the question that decides whether a LoRA needs retraining.

The payoff is that a consumer can acknowledge a pipeline-hash-only drift (a
composition edit that left the trainable TOML intact) while still being forced to
retrain when a training-bundle-hash shifts at the trainable it depends on. One
hash cannot express that distinction; two can. The implication direction is
load-bearing: same pipeline-hash guarantees same training-bundle-hash for every
trainable, but not the converse — which is exactly what lets the set of
composition variants sharing a training corpus be larger than a single pipeline.

{#why-the-training-bundle-hash-is-user-declared-not-graph-derived}
## Why the training-bundle-hash is user-declared, not graph-derived

A trainable's output is **invariant to how its inputs were derived**. If a
trainable reads `emotion` and receives `'happy'`, its response is the same whether
`'happy'` came from an upstream classifier, a coin flip, a turtle crawling on
letter-shaped buttons, or hard-coded user input. The training-data semantics are
identical — provenance does not matter to the trainable's contract.

This is the **provenance-invariance principle**, and it is why the
training-bundle-hash covers the trainable composition's *declared structural
membership* (the covered set is owned at
[hash-model § Training-bundle-hash construction](#training-bundle-hash-construction))
rather than its
upstream graph cone. The engine cannot
determine from DAG topology alone which upstream derivation paths affect the
trainable's contract and which are provenance-only noise — so it does not try. The
user's declaration of *what lives inside the trainable composition's scope* IS the
scope.

The visible consequence is the bucketing asymmetry the reference half lists: a
merge-strategy change upstream of a trainable shifts the pipeline-hash (replay
would differ) but not the trainable's training-bundle-hash (the trainable receives
the same channel values via a different derivation path). Merging is
provenance-invariant value-plumbing; it resolves channel fan-in
mechanically without changing what the trainable's contract sees.

{#why-trainable-composition-nodes-emit-no-serviceinvocation}
## Why trainable composition nodes emit no `service_invocation`

The canonical event log carries per-dispatch provenance for two purposes:
extracting the training corpus, and letting a consumer-side analyzer catch a
subtle failure mode — a service handler whose body lies about what its bound
service produced. The defense against that failure is **adapter-boundary
capture**: the `service_invocation` event is fixed from the backend's actual
response before control returns to the handler body, so the body has no path to
make its report to the runner and its report to the event log agree on a lie.

A trainable composition node has no author body to defend against. The engine
constructs its dispatch wrapper directly (the engine-generated construction
[R-handler-010](#R-handler-010/no-author-body) owns) and calls the adapter from
inside the engine's own dispatch
construction. The engine *is* the trusted author at that boundary; the adapter
call's input and output are already engine-controlled state. There is nothing for
an adapter-boundary interposition to protect against, so no `service_invocation`
fires. Training capture for a trainable composition node is instead the
`handler_enter` + `handler_exit` pair, which already brackets the
engine-controlled adapter call with the declared `reads` and `output_schema`
projections — neither of which passes through an author body. The integrity is
preserved *by construction* rather than by interposition, which is a stronger
guarantee, not a weaker one: there is no body between the two events that could
deviate from the declared shape, so post-hoc divergence detection is not needed
(or possible) for a trainable.

{#provenance-sufficiency-where-the-engines-commitment-ends}
## Provenance sufficiency — where the engine's commitment ends

The engine commits to **provenance sufficiency**: events carry enough payload that
a consumer-side analyzer *could* do meaningful work — pair `service_invocation` +
`handler_exit` to detect suspicious divergence for service dispatches, or extract
the captured training corpus directly from `handler_enter` + `handler_exit` pairs
for trainables. The engine does **not** commit to *performing* either analysis,
and that boundary is deliberate, not an unfinished edge.

Detecting service-kind divergence is interpretive. A service handler body may
legitimately transform a backend emission — parse it into fields, route metadata
onto separate channels — and the divergence between backend response and handler
return is often exactly what a downstream transform exists to express.
Distinguishing legitimate transformation from silent-fallback corruption requires
judgment that lives outside the engine's compose-time type-check surface. Per the
engine / consumer / review partition, the engine's role is to *structurally
enable* the second layer of silent-fallback defense — capture the evidence so the
analysis is possible — not to render the verdict. This is why the
silent-fallback rule remains review-enforced at the service-handler body layer:
the adapter-boundary event log makes that review mechanically grounded in captured
evidence rather than in source-reading alone, but the review is still where the
judgment happens.

{#why-integrity-enforcement-is-opt-in-not-always-on}
## Why integrity enforcement is opt-in, not always-on

The engine separates the integrity *property* (always available — hashes computed
at compose time, canonical events fire on shift) from the integrity *enforcement*
(whether a hash mismatch on a loaded artifact's manifest halts load). Enforcement
is a deployment-level boolean, and making it opt-in rather than always-on is a
direct consequence of Tenet 1.

A consumer using a stock model gets real value from handler composition,
validator-enforced contracts, canonical event emission, and runtime correctness
without ever training anything — and therefore without any manifest to check a
hash against. Forcing integrity enforcement on every consumer would leave only two
options, both bad: fabricate manifests for stock-model use (false declarations
asserting an integrity guarantee that nothing backs), or refuse to serve
stock-model consumers at all (a Tenet 1 regression — the engine would demand the
training contract enter the mental model of someone who never trains). The opt-in
dissolves the dilemma: the property is always *available*; the *enforcement* is
what a deployment explicitly requests by setting the boolean.

The opt-in also matches how the integrity guarantee is actually built up over a
project's life, as plain workflow rather than as named modes:

1. **Iterate with enforcement off.** The composition shifts freely; trainable
   composition nodes capture training data; Studio shows drift in trace; nothing
   halts. This is the right footing while the pipeline and its training corpus are
   still moving.
2. **Train.** Generate a fine-tuned artifact against the accumulated corpus. Its
   manifest captures the current pipeline-hash and the per-trainable
   training-bundle-hashes — the artifact's record of exactly what shape it was
   trained against.
3. **Turn enforcement on.** The freshly trained artifact matches by construction.
   From here, a future edit that shifts a trainable's training-bundle-hash halts
   the deploy until the artifact is retrained or the drift is explicitly
   acknowledged.

A stock-model consumer never sets the boolean to `true` and never encounters
enforcement friction; the training contract stays entirely outside that consumer's
mental model. The arc is a description of how the single boolean is used over
time, not a set of modes the engine defines — there is no state between "off" and
"on" but the ordinary work of training.
