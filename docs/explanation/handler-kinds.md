---
kind: explanation
audience: [authors, integrators]
slug: handler-kinds-explanation
explains: ../architecture/handler-kinds.md
---

{#handler-kinds-explanation}
# Why the handler kinds are what they are

The [handler-kinds reference](#architecture-handler-kinds) states the closed
enum — transform, service, hook — and the [node role](#node-role)
each plays. This doc carries the *why*: why exactly this set and no other, why the
enum is closed rather than extensible, why one axis (who constructs the dispatch)
lives at the boundary between handler kinds and composition kinds rather than
inside the handler-kind layer, and how a candidate new kind is evaluated.

{#why-this-set}
## Why this set

The taxonomy enumerates the mechanically-distinct node roles a handler can play.
One load-bearing axis carves the space at the handler-kind layer: **what outgoing
channels the node writes to.** External-call profile and failure tolerance are not
independent axes; they fall out of this one under two type-system constraints.

Lay the three roles along the write axis — each kind's [node role](#node-role) is
the role it plays in the graph — and the set is forced:

- **Writes to channels, no external call** — the transform.
- **Writes to channels, exactly one external call** — the service.
- **Writes to no channels, zero or more external calls** — the hook.

Two constraints fix why those are the *only* admissible combinations:

- **Channel-record correspondence.** A node whose channel-writes feed a training
  projection must map each write to one captured canonical event, or the
  projection is not auditable. For a service the captured event is
  `service_invocation`, and the [atomicity rule](#service)
  (exactly one external call per dispatch) preserves the one-write-to-one-event
  bijection. A channel-writing node with *more* than one external call per
  dispatch would collapse several events into one write, breaking the bijection; a
  channel-writing node with *zero* external calls has no external event to capture
  — that is precisely the transform. Exactly-one is therefore the only
  external-call profile compatible with the write-to-channels topology under
  training capture.
- **Channel-corruption halt.** A node writing to channels must halt on any
  failure: a swallowed failure produces a channel value the runner cannot
  distinguish from a real one, and it propagates downstream as if it were real.
  Transforms and services carry this constraint. A node writing to no channels
  carries no such constraint — an operational failure loses an observation but
  corrupts nothing downstream, which is exactly what licenses the hook wrapper's
  "log-and-continue on operational failure." That sanction is structurally
  available to hooks because their node role admits it, and would be unsafe on a
  transform or a service.

So the halt-on-failure behavior in the comparison table is not an independent
design choice; it is a consequence of the write axis under these constraints. The
closed enum is the exhaustive enumeration of the role-and-constraint space — a
hypothetical fourth handler kind would have to occupy a role the constraints rule
out: writes-to-channels-with-multiple-external-calls (breaks correspondence),
writes-to-channels-with-relaxed-halt (corrupts downstream channels), or
writes-to-no-channels-with-halt-on-everything (no advantage over hooks, and it
forfeits operational tolerance for nothing).

{#why-these-constraints-are-conjureds-not-the-genres}
### Why these constraints are Conjured's, not the genre's

It matters that this set is *not* what typed dataflow as a genre produces. Dagster,
Apache Beam, Flink, and LangGraph are all typed-dataflow systems, and none lands on
this enum — because each makes different architectural commitments. Beam's
PTransforms are an open, composable vocabulary; Flink's operators are built around
streaming-state and windowing; LangGraph's nodes are open Python callables wired by
edges; Dagster's ops/assets center on a materialization-and-lineage model. They
differ because their commitments differ.

The closed three-kind enum follows from three commitments specific to Conjured:

- **[I4](#invariants-and-derived-rules)
  (pipeline-as-training-contract derivation)** — forces channel-record
  correspondence, because the channel records *are* the training data.
- **[Closed-enum](#closed-enum) discipline** — a fixed
  taxonomy the type-checker can reason over exhaustively, rather than open
  extensible node types.
- **No typed-uncertainty for failures** — failure is a runner-level
  halt-or-continue, not an `Either` / `Option`-typed channel value, which forces
  the channel-corruption-halt constraint.

The pre-dispatch payoff is concrete: because the kinds are closed and each carries
a known node role, the type-checker can answer "what does this composition do" by
inspecting node positions, channel connections, and per-node kind — before
anything runs. An open or extensible node vocabulary would make that question
underdetermined.

{#the-layer-boundary-why-who-constructs-the-dispatch-is-not-a-handler-kind}
## The layer boundary — why "who constructs the dispatch" is not a handler kind

There is a second axis that looks, at first, like it should produce a fourth kind:
**who constructs the dispatch.** All three handler kinds are uniformly bare kwarg-only functions —
the author writes a body, and the engine wraps it in a dispatch wrapper that hands
each dispatch a fresh per-dispatch copy of the bindings (alongside the projected
reads). But the engine can also construct a
dispatch with *no author body at all* (the engine-generated construction
[R-handler-010](#R-handler-010/no-author-body) owns) — and that body-less,
engine-constructed dispatch is the distinguishing property of the **trainable
composition kind**.

The crucial observation is that this axis does not vary *within* the handler-kind
enum — all handler kinds sit on the same side of it (author-bodied, bare-function). It
only distinguishes handler kinds (as a group) from composition kinds (as a group).
So it is a *layer boundary*, not a fourth member of the handler-kind taxonomy. The
dispatch-construction axis is load-bearing for training capture's structural
integrity — a body-less engine-constructed dispatch is what makes the trainable's
captured training pair tamper-proof by construction — but that payoff lives at the
composition layer, and surfaces in the handler-kind doc only as the boundary line.

This is exactly why the reference half states the handler-kind/composition-kind
boundary once and declines to re-fence it in every section: the boundary is real
and load-bearing, but it is a single fact about *which layer you are in*, not a
property that needs restating per kind.

{#how-a-new-kind-is-evaluated}
## How a new kind is evaluated

The layer boundary supplies the test for any future candidate. When a use case
seems to want a new kind, ask what its load-bearing property actually is:

- If it is a **node role** the existing handler kinds do not cover — a new
  combination on the write / external-call / halt axes — then it is a candidate
  handler-kind addition, and it goes through an architecture decision that shows
  why transform, service, and hook each fail the case.
- If it is **engine-owned dispatch or other composition-layer machinery** (scoped
  channels, engine-constructed dispatch, a training-capture locus), the right
  landing place is a composition-kind specialization, not a handler kind.

This is why the candidates that recur in practice resolve *without* a new kind. A
validation handler is a transform (it writes a verdict channel, no external
call — distinct from a [validator](#validator), the field-level value
constraint). A cache
is a service (it writes channels with exactly one external call to the cache
backend). Retry, branching, and fan-out are not engine concerns at either layer —
they are consumer multi-pipeline orchestration, composing at a scale the engine
deliberately does not own, per the
[engine / consumer / review partition](#engine-consumer-review-partition).
In each case the apparent "new kind" is really an existing node role, or it is out
of the engine's scope entirely. The closed enum holds because the space it
enumerates is genuinely closed under the engine's commitments — not because
extension was forbidden by fiat.
