---
kind: explanation
audience: [authors, integrators]
slug: trust-model-explanation
explains: ../architecture/trust-model.md
---

{#trust-model-explanation}
# Why the trust model

A composed pipeline is a typed dataflow [graph](#graph);
its training corpus is a derived view of that graph, projected at trainable
channels. Invariant I4 (pipeline-as-training-contract), owned by
[principles](#invariants-and-derived-rules), makes that
derivation load-bearing — the
[channel records](#channel-record-correspondence) the
engine captures at runtime ARE the training data.

For the projection to be trustworthy, the values flowing through channels must be
the complete determinant of each handler's behavior. A handler that carries hidden
mutable state — state the channel records do not capture — produces outputs that
depend on something the corpus cannot see, and the corpus is corrupted: the engine
trains a model on input → output pairs whose true function also consulted the
hidden state. The [vector inventory](#the-vector-inventory)
is the operational enumeration of every specific way that corruption can happen
through author-supplied code, and the structural seal committed against each.

It is not a new constraint layer — every seal there traces to an existing axiom or
architecture decision. Its value is being the *single* canonical inventory:
without it, new features accrete without an audit surface, and the escape hatches
it seals resurface under refactor pressure. The inventory exists so that pressure
meets a recorded commitment instead of an open question.

{#the-threat-model}
## The threat model

The threat the seals address is **accidental I4 breakage by trusted authors** —
handler and adapter authors, including the coding agents that assist them, on the
natural-idiom path. A handler or adapter author following ordinary Python idioms
must not be able to break the channel-record correspondence I4 depends on; an
author who does break it must have unmistakably stepped outside intended use. The
seals are calibrated to that line: they make the corrupting shapes structurally
unavailable on the idiom path, not merely discouraged.

This is explicitly **not** an adversarial threat model. It is not multi-tenant
isolation, and it is not supply-chain defense. Python's process is structurally
open — any code in the engine's process can reach any other code's state through
standard introspection — and the seals make no claim against deliberate
circumvention. Naming this boundary is what keeps the seals honest: each is sized
to prevent an accident, and none pretends to a guarantee it cannot make.

{#structural-enforcement-vs-documented-best-practice}
## Structural enforcement vs documented best practice

> Structural enforcement is for invariants the engine needs to preserve;
> documented best practice is for author-quality concerns where the engine
> functions correctly but outcome quality varies. Reaching for structural
> enforcement on a quality concern is paternalism disguised as rigor. Reaching for
> documented best practice on an invariant concern is whack-a-mole disguised as
> flexibility.

This distinction is what places every entry in the inventory. Each vector is an
I4-integrity concern, not an author-quality concern — so each earns a *structural*
seal rather than a documented recommendation. The classification is the reason the
seals look the way they do, and it is what the audit procedure re-checks when a new
feature might shift a concern from one side of the line to the other.

Two seals carry a qualification that this distinction explains:

- **[Vector 5](#vector-5-external-io-at-handler-module-import)** pairs a structural
  scan with a *documented* boundary. Why the boundary is documented rather than
  checked: "is this import doing I/O" is not always statically decidable, so the
  line between admissible library imports and inadmissible import-time work is a
  judgment documentation carries where a check cannot.
- **[Vector 6](#vector-6-engine-blessed-compose-time-author-state)** is sealed by a
  policy commitment, not a check — there is nothing to scan, because the seal is
  the *absence* of an affordance. The meta-distinction places the concern firmly on
  the invariant side, which is why the commitment is firm.

{#why-these-vectors}
## Why these vectors

The vectors are not an arbitrary list of bad patterns; they are the exhaustive set
of ways author code under the engine load path can carry mutable state above the
scope the channel records capture. The enumeration has three parts. Author code
reaches the engine in two **categories** — handler modules and adapter modules —
and mutable state can hide at a small set of **scopes**: a closure, an instance,
the module namespace, or a delivered kwarg. Crossing those scopes with the two
categories yields the scope-based vectors — closure (1) and instance (2) in handler
modules, the module namespace as vector 3 in handler modules and vector 7 in adapter
modules, and the delivered kwarg (4) in handler modules. To that grid the inventory adds
two vectors that are not scopes: external I/O executed at handler-module *import*
(5) — a mechanism, not a place — and a future engine-blessed compose-time surface
(6), sealed by policy because no such mechanism exists to scan. Enumerate the grid
and add those two and the list is complete; that is why the
[inventory](#the-vector-inventory) can claim completeness rather than
coverage-so-far, and it fixes the vector-number-to-scope correspondence.

{#the-scope-generalization-principle}
### The scope-generalization principle

> No above-instance-scope mutable state in author-code modules under the engine
> load path.

The inventory states a seal per mechanism; this principle is the generalization
they are all instances of. It is what makes "why these vectors" answerable: the
mechanisms differ, but the line they all defend is the same one. Per-scope
enforcement details differ because the lifecycles differ — handler modules get
snapshot-and-restore because their lifecycle is per-dispatch; adapter modules get
the compose-time AST walk alone because an adapter is initialized once per
composition. The principle is the constant; the per-vector seals are how it lands
in each scope.

{#forward-isolation}
## Forward isolation

The seals are in-process. For adversarial, multi-tenant, or sandbox threat models
they are not sufficient — any Python code in the engine's process can reach any
other code's state through standard introspection facilities, and no static seal
closes that. This is not a gap the inventory is trying to close; it is the line the
threat model deliberately draws: adversarial / multi-tenant / sandbox isolation is
out of scope.
