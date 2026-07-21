---
kind: explanation
audience: [authors, integrators]
slug: handler-reference-explanation
explains: ../components/handler/reference.md
---

{#handler-reference-explanation}
# Why the handler binding and channel-type disciplines are shaped this way

The [handler reference](#handler-reference) states the per-kind TOML
grammar, the binding sections, the channel-type vocabulary, and the R-handler-* rules.
This doc carries the *why* behind three of its disciplines that each trade author
convenience for a structural guarantee: why compose-time bindings resolve **once at
composition** rather than per dispatch, why **runtime ID-lookup belongs in a service
handler, not a content binding**, and why binary content travels by **reference, not
inline `bytes`**. (The handler-*kinds* "why" — why this set of kinds, the
Dagster/Beam/Flink contrast, adding a new kind — lives in the
[handler-kinds explanation](#handler-kinds-explanation); this doc is the
reference-half companion, scoped to the binding/channel disciplines this reference
owns.)

{#why-bindings-resolve-at-compose-time-not-per-dispatch}
## Why bindings resolve at compose time, not per dispatch

A `bindings.<name>` value is resolved **once**, when the pipeline composes, and fixed
to the node for the life of that composition — so every dispatch sees the same value
(the runner hands each dispatch its own fresh copy of it). The instinct
of most config systems is the opposite: resolve configuration lazily, per call, so a
running service can pick up new values without re-composing. Conjured fixes binding
values at compose time on purpose, and the reason is the pipeline-hash.

The [pipeline-hash](#pipeline-hash) is the identity of a
composition — and binding values contribute to it. That is only coherent if a binding
value is a property *of the composition*, fixed across the pipeline's life, rather than
a thing that drifts per dispatch. If `bindings.config.temperature` could change between
two dispatches of the same composed pipeline, the pipeline-hash would no longer identify
"what this pipeline does" — two runs under the same hash could behave differently, and
the training corpus captured under that hash would be a blend of behaviors the hash
cannot distinguish. Compose-time resolution is what makes the hash mean something: same
composition, same binding values, same behavior, same captured-record shape.

This is also why the per-dispatch value-delivery mechanism is a **copy**, not a single
shared object handed to every dispatch. The point of fixing a binding at compose is that
its value is a property *of the composition* — so a handler must not be able to mutate it
mid-run and silently desynchronize its behavior from the hash that identifies it. Handing
each dispatch a fresh copy guarantees exactly that: a handler can scribble on its copy all
it likes (harmless local scratch, discarded when the dispatch returns), and the
compose-fixed value the next dispatch sees is untouched.
:::{region} copy-vs-freeze/derivation
Copy rather than a frozen kwarg,
because freezing only ever protects a *shared* object and an airtight freeze is expensive
— a shallow seal is assignment-only (a handler climbs past it into a nested
`cfg["weights"]['k'] = v` or `tags.append(...)`), and a recursive freeze changes the value's
type (`MappingProxyType` is not `dict`, `tuple` is not `list`), breaking ordinary
`isinstance` / `json.dumps` / json-schema-as-`dict` code. A
copy needs none of that: nothing is shared, so nothing needs to be frozen.
:::
The
compose-time-resolution discipline and the per-dispatch copy are the same guarantee seen
from two angles — *when* the value is fixed (compose) and *that* each dispatch cannot
disturb it (copy).

The one exception is a **reference binding** — large, static, read-only data the author
opts into *sharing* rather than copying (an NPC worldbook read every turn would be
wasteful to deep-copy per dispatch). There the engine deep-freezes the value once and
shares the single frozen instance across every dispatch; the one-time deep freeze is what
makes sharing safe. The [handler reference](#reference-bindings)
owns the full treatment.

The corollary is the no-per-field-`default` rule the handler reference owns (with its
I1 derivation and the `bindings.<name>` remediation home, at
[§ Types allowed in `reads` and `output_schema`](#types-allowed-in-reads-and-outputschema)).
What compose-time visibility adds to that rule's why: a ship-time default at the
bindings level is fixed at compose and visible to the hash, while a channel is a graph
edge whose presence is the contract — not a slot with a fallback.

{#why-runtime-id-lookup-is-a-service-not-a-content-binding}
## Why runtime ID-lookup is a service, not a content binding

The reference draws a hard line: a value chosen *at compose time* is a binding; a value
looked up *at dispatch time based on graph state* is a service call. The line can feel
arbitrary — both "load the NPC" — until you see what each side does to the graph's
identity and its training projection.

A content binding is reference data the composition commits to: "this pipeline, in this
composition, always uses Captain Blackwell." It is part of the composition's identity,
so it folds into the pipeline-hash, and every dispatch sees the same NPC. That is
correct precisely when the choice is fixed at compose — per-game-mode, per-scene,
per-cohort. The composition *is* the choice.

Runtime ID-lookup is the opposite: the NPC is chosen *per dispatch*, from a `reads`
channel value that varies run to run. If you smuggled that into a binding, you'd be
claiming a fixed composition identity for a pipeline whose actual behavior depends on a
runtime value the hash never saw — the hash would lie. Worse, the lookup is an external
read (a database, a file, a REST call), and external reads are exactly what the engine
captures as `service_invocation` events for the training projection. Done as a service
handler, the lookup is a declared external-call edge: one `services.npc_store.invoke(...)`
per dispatch, captured, hash-visible through the binding's service-type identity, and
honest about the fact that this node reaches outside the graph. Done as a "dynamic
binding," it would be an undeclared external read the engine neither sees nor records —
an I4 hole.

So the split is not about convenience or where the code reads nicer; it is about *when
the choice happens* and therefore *what the hash can honestly claim*. Compose-time
choice → binding (folds into identity). Dispatch-time choice from graph state → service
(declared external-call edge, captured). The reference's phrasing — "if the choice
happens per dispatch based on graph state, the handler is a service" — is the operative
form of that distinction.

{#why-binary-content-travels-by-reference-not-inline-bytes}
## Why binary content travels by reference, not inline bytes

The engine's IR admits `bytes` — the
[channel-type discipline](#channel-type-discipline)'s preference for path/hash
references over inline binary is explicitly *documented best practice, not engine-
enforced grammar* (that section owns the convention, its rationale, and the
capability boundary; this page narrates the why). So the question is why the
recommendation leans so hard one way when
the engine permits both, and the answer is what a **training-aware** pipeline does with
channel values: it captures them.

Every channel value flowing through a trainable composition node's projection becomes a
training record. Inline `bytes` for audio, image, or video payloads means the captured
corpus carries the full binary blob *per record* — inflating the corpus by orders of
magnitude versus a short reference string, and forcing full-content hashing on every
record to detect drift. A path/hash reference keeps the structured-pipeline payload
(the part suitable for a training-corpus index) separate from the binary payload (the
part suitable for a content-addressed blob store), and lets a downstream consumer detect
blob-content drift by comparing a hash rather than re-reading the blob.

This is a record-weight / hash-cost / storage-shape argument — author-quality, not
correctness — which is exactly why the owner classifies it as documented best practice
rather than enforced grammar ([§ Channel-type discipline](#channel-type-discipline)
states the convention and the cross-dialect capability boundary: TOML can't express
`bytes`, direct-Pydantic can, and a pipeline that genuinely needs inline binary is
permitted the trade). The reference convention is the engine telling authors what will
keep their
training corpus lean, while leaving the choice where author-quality choices belong: with
the author.

The same instinct routes *non-channel* binary intermediates — a preprocessing buffer no
downstream node needs to see — to a service-type adapter's instance state rather than a
channel (the adapter-scratch rule, also
[§ Channel-type discipline](#channel-type-discipline)'s). Channel state is the IR,
captured and hashed; adapter-scratch is engine-managed,
compose-scoped, and stays out of the projection. Both rules answer one question: does
this binary belong in the captured graph, or beside it?
