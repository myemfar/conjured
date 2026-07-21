---
kind: reference
audience: [authors, integrators, agents]
slug: architecture-hash-model
---

{#architecture-hash-model}
# Hash model
A composed pipeline is a typed dataflow [graph](#graph) —
handlers as nodes, declared reads and writes as typed channels between them. The
engine computes two sibling hashes covering this graph:

- The **[pipeline-hash](#pipeline-hash)** — identity of
  the full composition. Every input to the outer pipeline declaration
  contributes, and embedded engine-owned-dispatch
  [composition declarations](#composition-toml) (e.g.,
  [trainable TOMLs](#trainable-toml)) fold in by-reference
  via their own hashes; pure-substitution composition kinds (e.g.,
  [bundle TOMLs](#bundle-toml)) inline their content into
  the outer pipeline before hashing.
- The **[training-bundle-hash](#training-bundle-hash)** —
  the per-composition hash domain for engine-owned-dispatch composition kinds. One
  training-bundle-hash per such composition node in the pipeline; the composition
  declaration's own canonicalized hash IS its training-bundle-hash. The
  [trainable](#trainable) composition kind is the realized
  member.

The two-hash scheme is the mechanical backbone of
[invariant I4](#invariants-and-derived-rules). The engine
separates the integrity *property* (always available — hashes computed at compose
time, canonical events fire on shift) from the integrity *enforcement*
(deployment-level opt-in that determines whether mismatch halts load). See
[integrity-enforcement opt-in](#integrity-enforcement-opt-in).

---

{#what-the-pipeline-hash-absorbs}
## What the pipeline-hash absorbs

:::{region} what-the-pipeline-hash-absorbs/family-rule
**The rule: the pipeline-hash absorbs the entire canonical pipeline declaration; the
only exclusions are the closed engine-defined set in *What is explicitly NOT* below.**
The absorbed inputs are listed illustratively, not as a closed enumeration — anything in
the canonical declaration not on the exclusion list is hashed (state the rule rather than
maintain a stale-prone include-list). The pipeline-hash composes from the outer
pipeline declaration's normalized hash plus by-reference inclusion of embedded
engine-owned-dispatch composition declarations' own hashes — no cross-composition
join (the hash boundary tracks the composition boundary).
:::

- **Outer pipeline declaration inputs.** Handler order; per-handler entry binding
  values — the **effective value** (supplied-or-default) for each handler's declared
  `bindings.<name>`, supplied inline (a bare scalar is inline content, an inline table
  is an inline object), by external declaration file (the `{ file = "..." }` form),
  as the explicit null (the `{ null = true }` form, normalizing to the null value
  before canonicalization — a hash-neutral spelling like every other route),
  or — when the node omits a default-bearing binding — the binding's declared ship-time
  default (the declared default itself also folds in on the handler-declaration side,
  per the Handler-content bullet below). A **single-field binding**'s effective value folds
  in its **normalized (bare) form regardless of supply spelling** — the normalization
  itself is owned at the
  [handler reference § binding value-supply grammar](#binding-value-supply-grammar/normalization) —
  so the differing spellings of one logical value fold to a single pipeline-hash. The per-node
  `reads_map` / `writes_map` wiring (port → channel for each node — the edges of
  the typed dataflow graph, the same class as `merge.<channel>` and handler order);
  service-binding identity values from pipeline-level `service_bindings.<name>`;
  pipeline-level `merge.<channel>` declarations (the channel-write disjointness
  rule's structural contribution); qualified-name references to handlers and
  service-types. The wiring contribution is the NORMALIZED, always-explicit map IR,
  not the raw author surface: an empty author map (identity sugar) and a written-out
  identity map desugar to the same normalized IR and so produce the SAME
  pipeline-hash — sugar is hash-neutral by construction because the desugar runs
  before canonicalization (the same canonical-IR property that makes lexical
  re-formatting hash-neutral, below).
- **Pipeline-level `[inputs]` / `[outputs]` API boundary.** Part of the
  canonical pipeline declaration, so absorbed — an `[outputs]` API commitment is composition
  structure (its presence/absence changes the pipeline's external contract and replay
  identity). (Pipeline `meta.name` is **not** absorbed — it is identity, not structure; see the
  exclusions below.)
- **Handler content (resolved via qualified-name).** Each referenced handler's
  declared `output_schema`, `bindings.<name>` schemas (including each binding's
  declared **ship-time default**, where one is declared), `service_bindings`
  declarations, and validator configurations fold into the pipeline-hash through
  the qualified-name resolution path. The declared default folds in on this
  handler-declaration side **in addition to** the effective value folding in at the
  supply site above — changing a shipped default is a handler-declaration change that
  shifts the pipeline-hash of every composition resolving that handler, independent of
  whether any node overrides it.
- **Compile-directive binding content.** A `bindings.<name>` entry carrying the
  `compile = "<compiler>"` directive folds its **declared content** — the directive
  (the named compiler) AND its parameters — into the pipeline-hash as part of the
  binding's value contribution. Changing the named compiler or any of its
  parameters is a composition change. A parameter declared **inline** folds its inline
  content; a parameter supplied **from a file** (the `<param> = { file = "..." }`
  external-file form) folds the referenced file's content **as text**, never its path: the
  engine reads a compile-param file as raw text and hands it to the compiler unparsed (the
  compiler parses it). A parameter's inline value and its file-supplied text are
  therefore **distinct declarations** — they fold differently and produce different
  pipeline-hashes — while a content edit to the file stays visible in the hash. (The compiled
  artifact is engine-derived from this declared content at binding resolution; the hash
  covers the declaration, not the derived artifact.)

- :::{region} what-the-pipeline-hash-absorbs/external-binding-content
  **External binding-value declaration content.** Each external declaration
  referenced by a pipeline-entry binding (`<binding> = { file = "path/to/file.toml" }`)
  folds its own **canonicalized content** — the file is read at load (a
  resolution pass, I/O at parse so the hasher stays pure), its content normalized to the
  same canonical IR an inline value normalizes to, and that canonicalized content folds
  into the referencing binding's value contribution exactly as an inline value's content
  does — the content itself is what folds, never a separate per-file content hash. The
  path is NOT hashed. The consequence is **lexical / cross-dialect neutrality**: "inline X" and "an
  external file containing X" canonicalize identically, so they produce the same
  pipeline-hash — where a binding value lives (inline vs file) is hash-neutral, exactly
  as lexical re-formatting and identity-sugar are.
  :::
- **Embedded engine-owned-dispatch compositions.** Each embedded
  engine-owned-dispatch composition declaration (e.g., a
  [trainable TOML](#trainable-toml)) contributes its own
  normalized hash by reference — the embedded composition's internal scope is
  opaque to the outer hash; only its overall identity hash flows up.
- **Inlined pure-substitution compositions.** Each embedded pure-substitution
  composition declaration (e.g., a
  [bundle TOML](#bundle-toml)) is textually substituted
  into the outer pipeline before hashing; its content folds into the outer
  pipeline's hash like any directly-declared content.

These two contribution shapes apply **recursively** under
[the mirror-pipeline principle](#the-mirror-pipeline-principle): a composition's
internal node sequence mirrors the pipeline's `nodes`, so a composition that embeds
another composition folds that embed by the very same rule — each embed contributing
per its own shape, by-reference or pure-substitution, exactly as the two bullets above
define. One mechanism, applied at every layer.

What is **explicitly NOT** in the pipeline-hash:

- **The entire deployment declaration** — every value a deployment declares is
  per-environment, never per-composition, and folds into neither hash. The rule is
  the *category* (deployment-declared), not a fixed member list: it covers
  `transport.*` / `hook_transport.*` (environment endpoints and credentials),
  `training_export` (the capture toggle), `training_contract` (the
  integrity-enforcement opt-in), `acknowledged_drift` (drift acknowledgments), and
  the per-pipeline `pipelines.<name>` override section alike. Moving a composition
  between environments shifts neither hash.
- Package versions — pip semver is not in the hash; library authors are
  responsible for the immutability of their published handler names.
- A **bound service-type's `[*_schema]` field shapes** (`[identity_schema]`,
  `[config_schema]`, `[transport_schema]`) — the schema declarations the service-type
  publishes are not folded into either hash. What IS hashed is the **supplied values**
  validated against them: the `service_bindings.<name>` identity values supplied at the
  pipeline level (and the config supplies' **effective** values — supplied-or-default,
  where a `[config_schema]` field declares a ship-time default: a trainable composition's
  `trainable.config`, and a service binding's `service_bindings.<name>` `config` block —
  each validated against the bound service-type's `[config_schema]`) fold in as composition
  structure;
  transport values supplied against `[transport_schema]` are excluded as
  per-environment (the `transport.*` exclusion above). The engine hashes the values a
  composition supplies, never the bound service-type's declared field shapes. The
  service-type's **top-level `description`** is likewise outside both hashes: it is
  generation-time conditioning — per-pair generator instruction context riding the
  pipeline derivables bundle — not runtime contract, and its integrity pin is the
  provenance layer (the trained-artifact manifest's `generator_info.derivables_bundle_hash`
  records the exact serialized bundle the generator consumed).
- Hook declarations — hooks write to no channels and do not participate in the
  training projection; they contribute to neither hash, at either layer (a hook
  `[[preprocessors]]` entry inside a composition declaration is likewise
  stripped from that composition's own hash). The supply table folds
  **affirmatively over the non-hook graph**: the pipeline-hash folds each
  `service_bindings.<name>` supply entry (its identity values and its effective
  config) **referenced by a non-hook node**, and the hasher never reads a hook's
  declaration — the domain is defined by what the non-hook graph references, not
  by subtracting hook entries, so a supply entry referenced only by hooks is
  invisible to the hash by construction (this is why a pure hook carries nothing
  hashed). A binding **shared** between a hook and a non-hook consumer folds as
  ordinary supply data — the fold is the non-hook consumer's reference, not the
  hook's, so editing the shared entry shifts the hash exactly as any other
  non-hook supply edit does.
- `annotations` blocks inside engine-owned-dispatch composition declarations —
  metadata-class; excluded from the embedded composition's own hash, and therefore
  from the outer pipeline-hash by extension.
- **A composable unit's `meta.name`** — identity, not structure. A `meta.name` is the unit's
  correspondence handle — a trainable composition's is its
  [trained-artifact-manifest key](#manifest-key-shape); a pipeline's is its `pipelines.<name>`
  deployment reference. It is not hashed: **renaming a composable unit is hash-neutral.**
  (`meta.kind`, where a composition kind declares one, IS structural — it selects the dispatch
  apparatus — and is absorbed.)

**A declared schema field's `description` is model-facing contract content, hashed where it is
admitted.** A `description` is admitted in the declaration grammar ONLY on a
[trainable](#trainable) composition node's `trainable.output_schema` fields, on a wire family that
delivers them — the [handler reference's field-description admission rule](#toml-field-type-discipline/description-admission)
owns the positions. It **folds into both hashes wherever it exists** — the same derivation as a
trainable's `output_schema` field ORDER (emission order conditions an autoregressive backend's
generation, § Training-bundle-hash): a `description` conditions the backend's constrained
generation, so an input that changes what the backend generates is contract, not prose. Editing
such a description is a composition change and honestly shifts the training-bundle-hash (and the
pipeline-hash by reference). A described field on a wire family that cannot deliver descriptions is
a compose-time [ContractViolation](#contractviolation) — **the engine never hashes a
model-conditioning input a wire silently drops.** The rest of a declared field's hashed body is its
normalized type plus its **validation-keyword configuration** (bare standard constraints and
namespaced third-party validators alike, in authored order) — validation keywords constrain the
accepted value space, which is structural, so they DO fold in (per the Handler-content bullet above;
the handler reference's § TOML field type discipline states the field-grammar side).

> **The family rule (one identity model for every composable declaration).** Every
> composable unit — the top-level pipeline and each composition kind — **self-names
> via `[meta].name`**.
> That name is its **identity / correspondence key** (manifest key for a trainable;
> `pipelines.<name>` reference for a pipeline), and is **never part of any hash**. The
> hashes cover **structural membership** only; `name` and `annotations` are
> identity-or-metadata and excluded. This is why renaming is hash-neutral and why a
> nested-pipeline composition and a top-level pipeline share one identity model.

{#the-mirror-pipeline-principle}
### The mirror-pipeline principle

The family rule above is one face of a broader principle: a composition mirrors
the pipeline to the extent its [visibility-spectrum](#composition-toml) position
dictates — an engine-owned-dispatch composition is a full pipeline-shaped unit
sharing one feature set with one hash treatment per feature, while a
pure-substitution composition mirrors only the shared node-sequence grammar.

:::{region} the-mirror-pipeline-principle/kernel
> **The mirror-pipeline principle.** A composition kind sits on an
> [engine-visibility spectrum](#composition-toml), and how much of the pipeline it mirrors follows its
> position on it. An **engine-owned-dispatch composition** (a [trainable](#trainable), the nested
> `pipeline` kind) is a **pipeline-shaped unit**: it supports every feature the top-level pipeline
> declaration supports — node ordering, per-node bindings and their value-supply grammar, service-binding
> identity supply, channel-write merges, and the input/output boundary — and supports each one *the same
> way*, through the same shared mechanism, so that one feature has one grammar and one hash treatment
> across both layers. A **pure-substitution composition** (a [bundle](#bundle-toml)) mirrors only the
> shared **node-sequence grammar** — its `nodes` ARE the pipeline's `nodes` — and is textually substituted
> into the enclosing node sequence *before* that unit is scoped or hashed, so it carries no input/output
> boundary, no scoped channels, no own merge, and no own hash domain (those are the engine-owned-dispatch
> end of the spectrum, not features a bundle omits by defect). An engine-owned-dispatch composition
> differs from the pipeline only where a written justification names the reason (e.g. a trainable
> composition's `[outputs]` is body-required, not optional, because its output surface IS the
> training-record shape its training-bundle-hash covers — a property of the trainable's emitter nature, not
> of compositions in general). Any such difference without a justification is a defect to mirror-fix, not a
> sanctioned divergence; any new shared-grammar feature is added to the one shared mechanism, never
> per-layer.
:::

The principle is what makes the by-reference / pure-substitution split below
hash-coherent: an embedded composition's internal node sequence mirrors the
pipeline's `nodes`, so the same canonical-IR construction applies to both. The
composition-declaration side renders this same rule by transclusion alongside
the composition grammar in the handler component reference.

---

{#training-bundle-hash-construction}
## Training-bundle-hash

For each [trainable](#trainable) composition node in the pipeline, the engine
computes one [training-bundle-hash](#training-bundle-hash) covering the **trainable
composition's own [composition TOML](#composition-toml)**:

```
training-bundle-hash = sha256(canonical_repr(<trainable_toml_normalized>))
```

Canonical representation (engine-internal); `sha256:<hex>` prefix. `annotations`
blocks on the trainable composition are excluded from the hash as metadata-class —
their Studio-grouping postprocessor lists and free-form author prose shift no
semantics for the trainable composition's training-record shape. Hook
`[[preprocessors]]` entries are likewise excluded — stripped in canonicalization
exactly as `annotations` are: hooks write no channels and do not participate in
the training projection, so they contribute to neither hash at either layer (the
same exclusion the pipeline-hash applies to hooks in the outer `nodes`).

The trainable composition declaration's structural membership is what the hash
covers: the `trainable.config` / `trainable.service_bindings` / `trainable.reads`
/ `trainable.output_schema` subsections of the terminal trainable node, the
non-hook `[[preprocessors]]` sequence entries inside the trainable composition's
scope (hook entries are canonicalization-stripped, above), the
composition's own `service_bindings.<name>` **identity-supply values** (the model /
prompt-template selectors the composition supplies for its declared bindings —
mirroring how pipeline-level `service_bindings.<name>` identity values fold into the
pipeline-hash; a composition backend's supplied identity defines what the trainable IS,
so it folds in here), and any optional internal `merge` declaration. The supplied
identity *values* fold in, never the bound service-type's `[identity_schema]` field
shapes (those stay out of both hashes, per § What is explicitly NOT). The trainable
composition's own hash IS its training-bundle-hash; there is no separate composition
formula across preprocessor entries.

The `[trainable]` node's `streamable` field is **excluded** from the
training-bundle-hash. `streamable` is a delivery selector — it governs how the
backend's emission is transported to the consumer, not the training-record shape —
so it is the same class as the unhashed deployment `transport.*` values, not a
structural input. Toggling `streamable` does not shift the trainable composition's
training-bundle-hash (nor, by the by-reference fold, the pipeline-hash). The
streamable terminal-node placement rule (R-pipeline-001) is a compose-time
graph-shape check, separate from the hash.

The `[[preprocessors]]` **sequence order is semantic** — it is the trainable's
internal dispatch order, so it is preserved in the canonical representation and
contributes to the training-bundle-hash, taken over the hook-stripped sequence
(adding, removing, or re-placing a hook entry shifts neither hash). Only
field-key order *within* an entry is normalized away (per the canonical-IR
construction below); reordering the non-hook `[[preprocessors]]` entries shifts
the hash.

**Field order on the trainable's own `trainable.output_schema` is likewise
semantic — contract, not authoring convention.** The bound wire form compiles
the declared schema, in declared order, into the backend's decode constraint:
the declared field order IS the enforced emission order, and emission order
conditions an autoregressive backend's generation (a mood-then-dialogue schema
and a dialogue-then-mood schema are different generation tasks). The hash fold
therefore **preserves entry order for a trainable's `trainable.output_schema`**;
reordering its fields is honestly a new training-bundle-hash. This is the same
rule the canonical IR applies everywhere order reaches the contract (validator
lists, the `[[preprocessors]]` sequence, tuple and `Literal[...]` members):
**order is preserved exactly where order reaches the contract.** Non-trainable
schemas and the read side stay name-keyed — nothing consumes their order (a
trainable's reads serialize key-sorted on the wire, and a bare-function handler
receives kwargs, not a sequence).

{#bucketing-semantics-pipeline-hash-vs-training-bundle-hash}
### Bucketing semantics — pipeline-hash vs training-bundle-hash

The two hashes answer distinct questions about a drifted artifact at load:

- **Did the composition change?** (pipeline-hash differs)
- **Did the training-record shape at this trainable change?** (the trainable's
  training-bundle-hash differs)

A consumer holding a LoRA trained against a previous composition can acknowledge a
pipeline-hash-only drift (a composition edit that left the trainable TOML the LoRA
was trained against intact) while still requiring retraining when a
training-bundle-hash shifts at the trainable the LoRA serves.

Corpus consumers wanting "training data of compatible content semantics" bucket by
training-bundle-hash; full-pipeline replay identity uses pipeline-hash. **Same
pipeline-hash → same training-bundle-hash for every trainable.** The converse does
not hold:

- Postprocessor changes in the outer pipeline shift pipeline-hash but NOT any
  training-bundle-hash (postprocessors live outside the trainable composition's
  scope per the preprocessor / postprocessor asymmetry).
- Engine-shipped infrastructure changes around the trainable (transforms, merge
  declarations in the outer pipeline) shift pipeline-hash but NOT
  training-bundle-hash; hook changes shift neither (hooks are excluded from
  both hashes).
- A merge-strategy change upstream of a trainable shifts pipeline-hash (replay
  would differ) but NOT the trainable's training-bundle-hash — the trainable
  receives the same channel values via a different derivation path (the
  provenance-invariance property; the *why* is in the explanation half).
- A `reads_map` / `writes_map` re-wiring on a node upstream of a trainable —
  routing the same port shapes through different channels — shifts pipeline-hash
  (the graph edges changed) but NOT the trainable's training-bundle-hash. Which
  channel feeds which port is composition structure, not training-record shape;
  this is the same class as a merge-strategy change (provenance-invariance
  generalized).
- Editing the trainable composition's `trainable.output_schema` or
  `trainable.reads` declared PORT shapes (names + types), `trainable.config`,
  `trainable.service_bindings`, or any inside-scope preprocessor handler
  declaration — shifts both the trainable's training-bundle-hash AND the outer
  pipeline-hash, because the port shapes ARE the training-record shape. (A
  composition node entry itself declares no wiring maps — its key set is closed to
  `{kind, name}` at the pipeline-declaration grammar — so there is no own-map
  re-wiring surface at the embed position; see the load-bearing placement condition
  below.)

**Load-bearing placement condition.** The per-node `reads_map` / `writes_map`
wiring contributes to the pipeline-hash ONLY — and only handler-node entries carry
it (a composition entry's key set is closed to `{kind, name}`, so no outer wiring
map exists at an embed position to fold anywhere). Outer-graph wiring MUST NOT fold
into any training-bundle-hash: the training-bundle-hash covers the trainable's
declared PORT shapes (`trainable.reads` / `trainable.output_schema` names + types)
and the composition's own internal preprocessor wiring, never the outer graph's
routing. If outer wiring folded into a TBH, an outer re-route feeding the same port
shapes would shift the trainable's TBH (breaking the converse classification above)
and the empty-vs-explicit-identity-map desugar equivalence could shift TBH
(breaking sugar-neutrality). The maps are a node / pipeline-grammar addition, not a
trainable-internal-shape addition.

---

{#how-the-hashes-are-constructed}
## How the hashes are constructed

Both hashes are SHA-256 over a canonicalized serialization of their respective
input subgraphs. Canonicalization operates over the engine's **Pydantic
intermediate representation** of declared schemas, not over the source lexical
form — the canonical IR fixes key ordering, type representation, and metadata
expansion so two authoring conventions producing the same declared graph produce
the same hash, and lexical re-formatting of the source is hash-neutral by
construction. Key-order normalization applies exactly where order is authoring
convention; where order reaches the contract the canonical representation
preserves it instead — sequence-ordered members (the `[[preprocessors]]`
sequence, a field's authored-order validation keywords, tuple and `Literal[...]` members) and a trainable's
`trainable.output_schema` entry order (semantic, per § Training-bundle-hash
above). The training-bundle-hash uses the same canonical-IR construction
over the trainable composition's structural scope (excluding `annotations`); one
hash per engine-owned-dispatch composition node in the pipeline.

The exact canonicalization format is engine-internal; consumers do not
hand-construct hashes. The engine's hash machinery is the canonical
implementation, and the wire-visible artifact is the trained-artifact manifest
that records the hashes a given trained artifact was produced against.

---

{#trained-artifact-manifest-as-view}
## Trained-artifact manifest

A fine-tuned artifact ships with its
[trained-artifact manifest](#trained-artifact-manifest) — a sidecar TOML adjacent to the
artifact file, the wire form of the artifact-as-[materialized derived view](#materialized-derived-view):

```toml
[manifest]
artifact = "loras/my_lora.safetensors"
pipeline_hash_set = ["<pipeline-hash A>", "<pipeline-hash B>"]

[training_bundle_hashes]
"my_pkg.dialogue_trainable" = "<training-bundle-hash for that trainable at training time>"
```

`pipeline_hash_set` is a list, not a single value. A trained corpus may union
pairs from multiple pipeline compositions that share the same training-bundle-hashes
but differ in non-bundle-affecting composition details (outer-pipeline binding
values, postprocessor declarations, transforms/hooks unrelated to any trainable
composition's scope). Pipeline-hash match at load time is a **set-membership
check** against this list. Single-pipeline corpora are the common case; the
set-shape admits variant-spanning corpora without forcing trainable composition
edits to absorb composition variation.

This page names only the hash-bearing fields of the
[trained-artifact manifest](#trained-artifact-manifest) —
the fields load-bearing for the integrity property. The full manifest field set
is owned by the pipeline component reference.

{#manifest-key-shape}
### Manifest-key shape

The `training_bundle_hashes` table keys are the trainable composition's
**declared name** — the `name` from its `meta` block (`<trainable_name>`), which
the engine requires unique within the embedding pipeline's namespace. One manifest
entry per trainable composition node in the composed pipeline.
A pipeline composing multiple trainables (e.g., one dialogue trainable and one
summarizer trainable) contributes one manifest entry per trainable.
Multi-channel-trainable backends contribute one manifest entry covering all
channels the trainable composition declares.

{#hash-pinned-captured-artifacts}
### Hash-pinning — the manifest pattern generalizes to captured artifacts

The manifest realizes a general pattern the two-hash scheme makes available: **an
artifact derived from a captured run pins the hash identity it was captured
under, and staleness against the current composition is then a mechanical
comparison, not a judgment.** The trained artifact is the engine-checked
instance — its manifest records `pipeline_hash_set` + per-trainable
`training_bundle_hashes`, and the engine compares them at load per
[§ Integrity-enforcement opt-in](#integrity-enforcement-opt-in). Consumer-side
captured artifacts — an extracted training corpus, a replay bundle, a per-seam
test fixture — pin the same way: every run's events resolve to that run's
`pipeline_hash` (carried on `pipeline_start`, joined by `pipeline_run_id`), so
an artifact built from them records the pipeline-hash it was harvested under,
and the tool that loads it flags a recorded hash that differs from the current
composition's ("captured under a previous composition — re-harvest") instead of
trusting a stale snapshot. The split is deliberate: the engine MUST perform this
comparison for the trained-artifact manifest (the integrity-enforcement surface
above); for every other captured artifact the engine reads nothing and checks
nothing — the pinning is the documented pattern, and the check belongs to the
consuming tool.

**What a capture proof asserts (replay-and-recompute equality).** A captured run's
reproducibility is asserted by *recomputing from the declaration and comparing to the
pinned/recorded values* — never by reconstructing a hash from event payloads. Two legs.
**Pipeline-hash leg:** recompute the pipeline-hash from the unchanged declaration IR and
assert it equals the `pipeline_hash` pinned on the captured `pipeline_start` (and, where a
manifest exists, that it is in the manifest's `pipeline_hash_set`). **Training leg:** with
service responses held fixed (a captured or faked backend — the determinism replay needs),
assert the captured `handler_enter`/`handler_exit` pair reproduces the training record, and
separately that the hasher recomputes the trainable's training-bundle-hash deterministically
from its declaration TOML. The training-bundle-hash is **not** reconstructed from event
payloads — no per-run event carries it (it is a compose-time function of the trainable's
declared structural membership), so there is nothing in the stream to reconstruct it from.

---

{#event-log-specification}
## Event-log specification

The hashes detect *composition-or-projection drift* at LoRA load; the **canonical
event log** is the second mechanical layer for
[I4](#invariants-and-derived-rules), carrying
per-dispatch provenance sufficient both to extract the training corpus and for a
consumer-side analyzer to detect a subtler failure mode — a service handler whose
body lies about what its bound service produced.

:::{region} event-log-specification/per-kind-capture
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
:::

This page owns the canonical event-model spec — the per-event payload shapes,
per-kind emission rules, and pair-event semantics below are authoritative; other
canonical docs cross-reference here rather than re-enumerate.

The [closed enum](#closed-enum) of canonical events lives
on `conjured.events.runner`. Each event has a declared payload shape; adding or
changing an event is a contract amendment — an engine change governed by the
closed-enum discipline, not a runtime extension point.

{#canonical-event-types}
### Canonical event types

| Event | Fires | Payload fields |
|---|---|---|
| `pipeline_start` | Per pipeline run, after pipeline-level inputs load but before the first handler dispatches | `pipeline_run_id` (string), `parent_run_id` (string, nullable — the enclosing run's `pipeline_run_id` when this run is an inner run of a nested `pipeline` embed; `null` for a top-level run), `pipeline_hash` (`sha256:<hex>`), `timestamp` (ISO 8601), `inputs_snapshot` (object — projection of channel state restricted to the pipeline-declared inputs at invocation) |
| `handler_enter` | Per node dispatch, before body invocation (or, for [trainable](#trainable) composition node dispatches, before engine-constructed `adapter.invoke`). For trainable composition node dispatches `reads_snapshot` IS the training-pair input side. | `handler_qualified_name`, `handler_position` (integer, 0-indexed in the engine's final compose-time dispatch order), `node_kind` (enum: `"transform"` / `"service"` / `"hook"` / `"trainable"`), `pipeline_run_id`, `timestamp`, `reads_snapshot` (object — projection of channel state restricted to the node's declared `reads` field set) |
| `handler_exit` | Per node dispatch, after body completes successfully (or, for trainable composition node dispatches, after engine-constructed `adapter.invoke` returns). For trainable composition node dispatches `writes_snapshot` IS the training-pair output side. | `handler_qualified_name`, `handler_position`, `node_kind`, `elapsed_ms` (integer), `pipeline_run_id`, `timestamp`, `writes_snapshot` (object — projection of channel state restricted to the node's declared `output_schema` field set; present for transforms, services, and trainable composition nodes; `null` for hooks (key present, value null — hooks return `None` by contract)), `correlation_id` (string; present with a value for service dispatches — pairs this event to the same dispatch's `service_invocation`; `null` for transforms, hooks, and trainable composition nodes (key present, value null — none of those emit a `service_invocation` to pair with)) |
| `service_invocation` | **Service-kind only.** Per `services.<name>.invoke(...)` call on a service handler; captured at the **adapter boundary** (see below). The `input_payload` / `output_payload` pair is the wire-visible record of what the adapter submitted vs what the backend returned — useful primarily for service-kind divergence detection (consumer-side check against R-handler-002 no silent fallbacks — see § Paired-event structure). Trainable composition node dispatches do NOT emit this event; their training capture is the `handler_enter` + `handler_exit` pair — see § Adapter-boundary capture for the kind-keyed capture rule. | `handler_qualified_name`, `handler_position` (integer, 0-indexed in the engine's final compose-time dispatch order — present so the service pair joins on `(pipeline_run_id, handler_position)`, symmetric with `handler_enter` / `handler_exit`), `input_payload` (object — the payload the adapter submitted to the backend, post handler-body assembly), `output_payload` (object — the backend's response **exactly as returned**, deep-copied and captured BEFORE any handler-body transformation; it MAY diverge from the handler's `output_schema` shape, and detecting that divergence is the service-kind silent-fallback check — see [§ Paired-event structure (service-kind)](#paired-event-structure-service-kind)), `pipeline_hash`, `elapsed_ms`, `pipeline_run_id`, `timestamp`, `correlation_id` |
| `pipeline_complete` | Pipeline run reaches happy-path termination | `pipeline_hash`, `pipeline_run_id`, `elapsed_ms`, `timestamp`, `outputs_snapshot` (object — projection of channel state restricted to the pipeline-declared outputs; `{}` when the pipeline declares no `[outputs]` block) |
| `pipeline_error` | A pipeline **run** halts at runtime; any error class per R-error-channel-001 (a load- or compose-time `ContractViolation` halts before a run is in flight and does NOT fire this event) | `pipeline_hash`, `pipeline_run_id`, `elapsed_ms`, `timestamp`, `error_class` (the closed [error class](#error-class) enum), `failure_category` (the closed structural-locus enum; present when `error_class = "PipelineFailure"`; members + semantics owned at [failure_category](#pipelinefailure-payload/failure-category)), `cause_class` (string — the underlying Python exception class name; present when `error_class = "PipelineFailure"`), `failed_handler_qualified_name` (always present — `pipeline_error` is runtime-only, so a dispatched handler always exists at the halt), `failed_handler_position` (integer — the failed handler's 0-indexed position in the engine's final compose-time dispatch order; present alongside `failed_handler_qualified_name`, since the qualified name is not unique within a run under multi-dispatch), `error_message` (string) |
| `training_bundle_hash_changed` | Compose-time; a trainable's training-bundle-hash differs from a loaded manifest's recorded value. | `trainable_qualified_name`, `old_training_bundle_hash` (nullable — `null` on first observation), `new_training_bundle_hash`, `pipeline_hash` (current pipeline-hash at compose time), `timestamp` |
| `pipeline_hash_changed` | Compose-time; pipeline-hash differs from a loaded manifest's recorded value | `old_pipeline_hash`, `new_pipeline_hash`, `timestamp` |

**Optional payload fields serialize as explicit `null` — the canonical
in-process event serialization.** Each canonical event's key-set is fixed by its
declared payload shape above; a field that shape carries but that has no
applicable value for a particular dispatch (`parent_run_id` on a top-level run,
`correlation_id` on a non-service dispatch, `writes_snapshot` on a hook, the
`PipelineFailure`-conditional fields on a non-`PipelineFailure` `pipeline_error`)
is serialized with its **key present and value `null`**, never dropped. A consumer
therefore reads a stable per-event shape and distinguishes a carried-but-null
field (key present, value `null`) from a field outside that event's declared shape
(key absent). This in-process include-nulls contract is deliberately the
**inverse** of the error channel's HTTP problem-details wire projection, which
omits null extension members (RFC 9457 economy) — the two serialization surfaces
differ by design. This page owns the event-side policy; the server
event-emission and error-channel surfaces cite it.

**`pipeline_run_id` is the cross-event correlation field.** A consumer joining
events for a single pipeline invocation filters on `pipeline_run_id`. The engine
accepts consumer-supplied `pipeline_run_id` values at invocation to support
cross-invocation observability reconstruction via log aggregators; an
engine-generated identifier is a structured, sortable string of the form
`run_<ISO-8601 basic UTC>_<short-random>` (e.g. `run_20260506T142311Z_a3f9`),
which sorts lexicographically by run time. The **basic** ISO-8601 profile is
colon-free, so the id rides a URI (a path segment or a query value) verbatim,
with no percent-encoding.

:::{region} canonical-event-types/dispatch-identity
The engine
admits multi-dispatch — the same handler can dispatch more than once per run,
because node identity is dispatch POSITION, not the qualified name — so
`(pipeline_run_id, handler_position)` is the primary key for handler-bearing
events. `handler_position` is a total order over a run's dispatches (the final
compose-time dispatch order), so it is unique
per run where `handler_qualified_name` is not. `handler_qualified_name` remains a
NON-KEY descriptive payload field — it answers "which handler ran at this
position" for Studio legibility and bucketing, but does not disambiguate identity
under multi-dispatch.
:::

**Nested runs correlate to their parent by `parent_run_id`.** A nested
`pipeline` embed (an engine-invoking-engine composition) runs an inner run,
which emits its **own** canonical-event stream under its **own** engine-generated
`pipeline_run_id`. The inner run's `pipeline_start` event carries `parent_run_id` —
the `pipeline_run_id` of the enclosing run (nullable: `null` for a top-level run,
which has no parent) — the single linkage from the inner run to its parent. The
inner training corpus is therefore **reconstructed by
correlation, not duplication**: inner records join the inner run's own corpus (keyed
by the inner `pipeline_run_id`), and a consumer recovers the full nested context by
following `parent_run_id` outward — no inner record is copied into the outer run's
stream. This is the same reconstruct-over-duplicate posture the four boundary
snapshots take for intra-run trace.

**`correlation_id` is the wire field behind the
[correlation ID](#correlation-id) concept** — a parallel
pairing mechanism on the service-dispatch pair (`service_invocation` +
`handler_exit`). It is the canonical service-pair join: a single dispatch-specific
field tying together the two events of ONE specific dispatch.
`(pipeline_run_id, handler_position)` is the equivalent composite — both pair the
same two events of the same dispatch. The field is retained as the direct wire
pair-id so the join is a single-field lookup even under multi-dispatch, where the
qualified name alone would no longer be unique.

:::{region} correlation-id-derivation/composite-rendering
Its value is the dispatch's
`(pipeline_run_id, handler_position)` rendered as the string
`<pipeline_run_id>:<handler_position>` — the composite joined with a `:`
separator (the colon-free engine-minted run-id keeps the join legible). It is a
derived convenience label paired by string equality — not a separately-generated
identifier, and not parsed back apart: the structured
`(pipeline_run_id, handler_position)` fields are the authoritative pair. The two
events of one dispatch share it, and two `service_invocation` events sharing one
value are the wire signal for a multi-call violation.
:::

**The four boundary snapshots** (`inputs_snapshot`, `reads_snapshot`,
`writes_snapshot`, `outputs_snapshot`) are projections of channel state restricted
to declared interfaces at each boundary. They are symmetric by design: the four
snapshots together let a consumer reconstruct a full run's channel-state evolution
from the event log alone (see
[Replayability](#replayability)).

{#adapter-boundary-capture-mechanism}
### Adapter-boundary capture

[Adapter-boundary capture](#adapter-boundary-capture) puts the `service_invocation`
canonical event at the **service-type adapter boundary** — the engine's wrapper around
the service-type's outbound call. **Service-kind dispatches only.** The event payload is
constructed from the backend's actual response BEFORE control returns to the service
handler body; the body has **no access** to the event payload and **no ability** to
mutate it.

This is the structural defense for the
[silent-fallback](#silent-fallback) failure mode (invoke
the backend inside a service handler body, ignore the result, return a default).
If event capture were post-dispatch — if the engine read the captured payload from
the handler's eventual return — the same dishonest handler body could lie about
both what it returned to the runner and what it reported to the event log; the two
reports would line up by construction, and the divergence would not surface.
Adapter-boundary capture closes that collapse: the event payload is fixed before
the handler body executes, and the body has no path to reach it.

The seam is structural rather than disciplinary. Under
bare-function dispatch the engine constructs each service
handler's dispatch wrapper around the bare author
function; the function reaches the
[service-type adapter](#service-type-adapter) via the
`services` kwarg, and the adapter wraps the backend call. The event-capture point
lives in the adapter, structurally outside the function's reach: the adapter
captures both **what was submitted to the backend** (`input_payload` — the
post-handler-body-assembly payload the adapter actually sent) and **what the
backend returned** (`output_payload` — the response, captured before the function
receives the typed result). The handler body sees the typed response but cannot
influence either captured payload.

{#paired-event-structure-service-kind}
### Paired-event structure (service-kind)

For each [service](#service)-kind dispatch, the engine
emits **two paired events**: `service_invocation` (at the adapter boundary, before
the handler body sees the backend's response) and `handler_exit` (after the
handler body completes, carrying the `writes_snapshot` the runner is about to
merge onto the graph). The pairing — via `correlation_id` (the canonical
single-field service-pair join) and equivalently via the composite
`(pipeline_run_id, handler_position)` — is the consumer-side analysis seam.

A consumer holding the pair can compare what the backend produced (`output_payload`
on the `service_invocation`) against what the handler returned (`writes_snapshot`
on the `handler_exit`); the comparison is shape-projection-equality across the
service's declared `output_schema` fields. Divergence between the two events is
consumer-side evidence, not an engine verdict: legitimate in-body postprocessing
produces divergence too, and distinguishing transformation from a
silent-fallback instance is interpretive — consumer/review territory (the
explanation half owns the why).

Symmetrically, `input_payload` on the `service_invocation` plus `reads_snapshot`
on the same dispatch's `handler_enter` give the consumer the full input side:
`reads_snapshot` is what the handler saw from the graph; `input_payload` is what
the handler assembled and sent to the backend. The two together let a consumer
reconstruct the handler body's in-process formatting (prompt assembly, parameter
substitution, content interpolation) — the layer between channel state and backend
call that upstream channel state alone cannot reconstruct.

This provenance holds **only when the in-process formatting lives inside the Python
adapter scope.** The capture point records `input_payload` at the adapter boundary, so
assembly the adapter does *before* the call is captured — but assembly that happens
*after* it, inside an external process the adapter merely shells out to, is not. A
**non-Python adapter** (a Python shim wrapping a Rust / Clojure / etc. backend behind
`invoke`) MUST therefore do any content assembly or formatting that shapes what the
backend receives **inside the Python adapter, before the call** — never in the external
process. Otherwise `input_payload` records the pre-shim payload while the backend sees a
different post-shim one, and the pair no longer reconstructs what the backend actually
saw. The engine cannot reach inside an external process, so this is a library-publishing
discipline (the review-enforced class of R-handler-002), not a mechanically-enforced check.

{#paired-event-structure-trainable-composition-kind}
### Paired-event structure (trainable composition kind)

For each [trainable](#trainable) composition node
dispatch, the engine emits **two paired events**: `handler_enter` (before the
engine-constructed `adapter.invoke` call, carrying `reads_snapshot` — the
training-pair input side) and `handler_exit` (after `adapter.invoke` returns,
carrying `writes_snapshot` — the trainable composition node's `output_schema`
projection, the training-pair output side). The pairing is via the composite
`(pipeline_run_id, handler_position)`; `correlation_id` is `null` (key present, value `null` — no
`service_invocation` to pair with, per the null-serialization policy above).

There is no in-process formatting layer for a consumer to reconstruct on a
trainable composition node dispatch — the engine partial-applies the trainable
composition's `bindings` into the dispatch wrapper at compose time, so what
reaches the adapter is fully determined by the declared `reads` projection plus
the compose-time-fixed `trainable.config`. The training pair (`reads_snapshot`,
`writes_snapshot`) is itself the captured training record; no divergence-detection
comparison is needed (or possible) because there is no body between the two events
that could deviate from the declared shape.

{#channel-record-correspondence-by-kind}
### Channel-record correspondence

The [bijection](#channel-record-correspondence) between
channel-writes and captured canonical events — **every channel-write maps to one
captured event, every captured event maps to one channel-write** — is the property
the event-log spec stands on. The captured event differs by node kind:

- **Service-kind handler** writes onto declared output channels correspond to
  `service_invocation` events. Service-atomicity (exactly one external call per
  dispatch) preserves the bijection at the runtime-contract layer; adapter-boundary
  capture preserves it at the provenance-capture layer.
- **Trainable composition node** writes onto trainable channels correspond to
  `handler_exit` events (paired with the same dispatch's `handler_enter`). The
  engine-constructed dispatch is the locus; no `service_invocation` fires, and the
  pair `(handler_enter, handler_exit)` IS the captured training record.

With both bijections, a captured training corpus is exhaustive for its trainable
channels (no events lost, no channel-writes uncaptured) and — for service-kind
writes — a drifted handler body is detectable post-hoc (a channel-write that does
not match its paired invocation event is recorded on the wire). For trainable
composition node writes the structural integrity is preserved by construction (no
body between the engine-controlled adapter call and the channel-write the engine
routes), so post-hoc divergence-detection does not apply.

---

{#integrity-enforcement-opt-in}
## Integrity-enforcement opt-in

:::{region} integrity-enforcement-opt-in/property-vs-enforcement
The engine separates the integrity *property* (always available — hashes computed
at compose time, `training_bundle_hash_changed` and `pipeline_hash_changed`
canonical events fire on shift, Studio surfaces drift in trace regardless) from
the integrity *enforcement* (deployment-level opt-in toggling whether hash
mismatch on a loaded artifact's manifest **halts** load or only emits events).
:::

The opt-in lives in the deployment declaration:

```toml
[training_contract]
integrity_enforcement = false   # or true
```

The `training_contract` declaration is **required, body-required** per
[exhaustive declaration](#glossary-exhaustive-declaration) — the grammar rule is owned at
the deployment reference:

:::{transclude} training-contract-section/required-body-required
:::

The choice itself is load-bearing — Conjured's
[pipeline-as-training-contract](#glossary-pipeline-as-training-contract)
property holds at compose time, and whether a deployment enforces it is the
deployment's affirmative or negative answer per
[Tenet 1](#tenets).

{#enforcement-off-integrityenforcement-false}
### Enforcement off (`integrity_enforcement = false`)

- Hashes computed in background; canonical events fire **on every hash shift** (a
  manifest's value differing from the deployed pipeline's current value); Studio
  shows drift in trace **when there is a baseline to differ from**.
- **Missing manifest on any service: no comparison happens, no halt, and no drift
  event** — there is no baseline to differ from. The "integrity property is always
  available" framing presupposes a manifest exists; consumers without manifests
  who later decide they want integrity guarantees should produce a manifest (via
  training) and flip the opt-in. Consumers using stock models and never training
  never enter this path.
- Hash mismatch on a loaded manifest: canonical events fire, load proceeds.
- `acknowledged_drift` entries are ignored (no enforcement to acknowledge
  against).

{#enforcement-on-integrityenforcement-true}
### Enforcement on (`integrity_enforcement = true`)

- Hashes computed; canonical events fire (same as off).
- Missing manifest where the deployment declared an artifact load (an `[artifacts]`
  entry — the deployment reference's § `artifacts` owns the registration surface):
  halt with ContractViolation. No manifest = no integrity guarantee, and the consumer
  opted in to the guarantee.
- Hash mismatch on a loaded manifest, in graduated force:
  - **training-bundle-hash mismatch at a trainable composition node** — halt with
    ContractViolation; the trained shape at that trainable does not match the
    runtime shape. Load proceeds only if `acknowledged_drift` covers the artifact
    and the specific drift class at the affected trainable.
  - **pipeline-hash not in `pipeline_hash_set`** with all training-bundle-hashes
    matching — medium-force warning emitted via the `pipeline_hash_changed` canonical event; composition has
    changed but no trainable's shape is affected; load proceeds with an audit-log
    entry, no halt.
  - **both match** (all training-bundle-hashes equal AND pipeline-hash is in the
    set) — load proceeds without warning.

Acknowledged-drift discipline: `acknowledged_drift` entries name the artifact and
the specific drift classes accepted, per class. Under per-trainable granularity, a
drift class names a specific trainable — acknowledging training-bundle-hash drift
at one trainable does not silently accept drift at another. No `"any"` sentinel
exists; unbounded acceptance defeats the integrity property the deployment just
opted into. Studio can generate acknowledgment blocks from a LoRA's manifest diff
to ease the typing-friction without removing the explicit per-trainable consent.

---

{#what-the-hash-model-does-not-promise}
## What the hash model does NOT promise

- **Behavioral equivalence.** Matching hashes do not guarantee identical inference
  behavior — they guarantee the shape-matching property at training time, per
  trainable channel. Behavioral evaluation is consumer territory.
- **Cross-version stability.** A pipeline-hash is defined relative to the engine's
  hash construction algorithm and the canonical IR representation. Engine version
  changes that alter the algorithm or the IR shift hashes for every existing
  pipeline. This is why the engine ships hash construction as part of the engine
  contract, not as a consumer option.
- **Training-data per-record durability.** Per
  [I4 scope notes](#invariants-and-derived-rules): a
  single training record lost to transient I/O failure is statistical noise. The
  hash model promises shape integrity at every trainable channel, not per-record
  durability.
- **Backend stability.** The hashes cover the graph the engine sees — declared
  channel types, bindings, compositions. They do not cover the *backend's behavior*
  under those declarations. A vendor API silently changing its effective output
  distribution while the declared constraint stays stable is not detectable from
  hash comparison; pinning backends and treating backend upgrades as graph edits
  is deployment discipline. For pinned local models this concern is near-zero; for
  vendor-API service-types it is real, and the burden lives at the consumer /
  deployment layer rather than inside the engine's compose-time type-check surface.

---

{#cross-dialect-portability}
## Cross-dialect portability

The hash-equivalence promise — same composition produces the same pipeline-hash —
is **conditional on type-system overlap** across the dialects expressing the
composition.

Conjured's compose-time Pydantic intermediate representation admits any
Pydantic-expressible channel type. Different dialects (TOML, direct-Pydantic,
future protobuf / JSON-Schema authoring surfaces) have different type
expressiveness:

- **TOML** lacks a `bytes` primitive. A composition that uses `bytes` channel
  types cannot be expressed in TOML; the composition's pipeline-hash is
  well-defined under the dialects that can express it, but is not reachable from a
  TOML-only authoring path.
- **direct-Pydantic authoring** admits the full Pydantic type space, including
  `bytes`. A pipeline authored in direct-Pydantic can use channel types not
  expressible in TOML.
- **Future dialects** (protobuf, JSON Schema, others) carry their own
  expressiveness boundaries.

The cross-dialect equivalence promise is: **for the type-system intersection
across two dialects, the same composition produces the same pipeline-hash from
both authoring surfaces**. Compositions outside the intersection are
dialect-scoped; the hash exists, but only in dialects that can express the
composition.

This is a **documented capability boundary, not a weakened promise**. The
LCD-typed (lowest-common-denominator) subset has identical hash guarantees across
all dialects. Pipelines that exceed the LCD subset trade off cross-dialect
portability for type-expressiveness. The choice is the author's; the engine does
not propagate any one dialect's expressiveness boundary as engine policy.

TOML's lack of `bytes` is the canonical concrete example: a training-aware
pipeline routing binary content (audio waveforms, image bytes, model weights)
authored in direct-Pydantic uses `bytes` channels naturally; the same pipeline
rendered to TOML requires the path / hash-reference convention the handler
component reference owns (the channel-type discipline). Authors
using TOML stay within the LCD subset by convention; authors using direct-Pydantic
may either match the convention (preserving cross-dialect portability) or use
`bytes` directly (trading portability for expressiveness).

---

{#hash-model-where-this-lives-in-the-engine}
## Where this lives in the engine

The [pipeline](#pipeline) component owns the hash
construction algorithm, the manifest comparison logic, and the event-log emission
of the hash-relevant and training-capture canonical events (`service_invocation`,
`handler_enter`, `handler_exit`, `training_bundle_hash_changed`,
`pipeline_hash_changed`). The [handler](#handler) component
owns the schema declarations the projection reads from at each trainable channel
position. The [error-channel](#glossary-error-channel) component
owns the [ContractViolation](#contractviolation) raised
when an unacknowledged training-bundle-hash mismatch refuses load under integrity
enforcement.
