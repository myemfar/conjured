---
kind: reference
audience: [authors, integrators, agents]
slug: handler-reference
component: handler
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#handler-reference}
# Handler reference

The per-component reference for the [handler](#handler)
[node](#node) — the unit a
[pipeline](#pipeline) composes into a typed dataflow
[graph](#graph). The cross-component shared shape — the
[handler kinds](#architecture-handler-kinds) (transform / service / hook),
the trainable [composition kind](#composition-toml)
specialization, the [comparison table](#comparison),
the [node roles](#node-role) they realize, the
engine-constructed dispatch wrapper as the
[sole admission gate](#sole-admission-gate) to the graph,
and the "why this set" argument — lives at
[handler-kinds](#architecture-handler-kinds) and is not restated here.

What lives here:

- Per-kind handler-TOML **grammar** — section-by-section walkthrough of what each
  bare-function kind's TOML carries, mapped onto the
  [section-discipline modes](#the-section-discipline-modes).
  For the trainable composition kind, the grammar is the
  [composition TOML primitive](#composition-toml); this
  doc cross-references the machine-readable `trainable.schema.toml` rather than
  restating.
- Per-kind **per-section validation** — the per-kind rules the engine fires at
  handler-declaration load and at pipeline compose time (transforms forbid
  service-typed bindings; services require exactly one; trainable composition nodes
  tighten the service-binding rule to a trainable backend per
  [R-handler-008](#handler-derived-rules); hook channel structure).
- **TOML field type discipline** — what types are allowed inside the schema
  sections, and inside `bindings.<name>` declarations.
- **§ Compose-time work homes** — the three architectural homes for compose-time work
  (binding, service-type adapter, handler body per dispatch), plus the separate-axis
  "compose vs author" affordance (the native library).
- **§ When NOT to use content bindings** — the runtime-ID-lookup-belongs-in-services
  discipline.
- **§ Channel-type discipline** — the permissive Pydantic IR posture + canonical
  authoring default for binary content.
- The **service-type adapter** — the engine-internal translation seam between a
  handler's declared channels and a backend-specific structured-output API. Outside
  handler-body reach; runner-side; named architectural anchor for R-handler-005's
  literal-equal rule and the canonical event log's adapter-boundary capture
  (service-kind dispatches) plus the engine-constructed dispatch path (trainable
  composition node dispatches).
- The component's **derived rules** (the R-handler-* set, defined below) — citing
  invariants from [principles](#invariants-and-derived-rules)
  via `derived_from`. Rules in `enforcement: mechanical` mode bind the runner; rules
  in `enforcement: review` mode bind adversarial review (the runner can't see handler
  bodies for bare-function kinds; the trainable composition kind has no body to review).
- **Worked examples** — one canonical declaration per kind so an
  agent reading this reference reaches for the
  engine-aligned shape on first try (Tenet 2).

The companion machine-readable per-kind schemas under
`kind-schemas/{transform,service,hook,trainable}.schema.toml` (documented at the
`kind-schemas/README`) are the agent's primary surface for authoring conformant
handler TOMLs; this prose reference is the human-and-agent-readable counterpart. TOML is the current handler-authoring dialect;
the engine's canonical form is Pydantic, and the IR-canonical discipline means future
dialects (JSON Schema sidecars, direct Pydantic declarations) convert to the same IR
via 1×N converters — see
[hash-model § Cross-dialect portability](#cross-dialect-portability).

---

{#handler-toml-grammar}
## Handler-TOML grammar

A handler declaration carries **exactly one**
[handler kind](#handler-kind) — one of the three bare-function
kinds (transform / service / hook). The trainable composition kind uses a different
declaration shape (the composition TOML primitive) and is documented as a
cross-reference below.

- **Bare-function kinds (transform / service / hook)** — a top-level closed-shape key
  naming the kind (`transform`, `service`, or `hook`) at the file's top level. The
  key is read at handler-declaration load (engine startup); at compose time the runner performs
  [handler resolution](#architecture-handler-resolution) (dotted-path or
  entry-points) + the [R-handler-pure-module](#handler-derived-rules) source-AST audit + the
  [R-handler-bare-function](#handler-derived-rules) function-shape check (the
  [trust-model](#trust-model-vector) vector-2 seal — the
  [`inspect.isfunction(x)` admit/reject predicate](#R-handler-bare-function-predicate-admit-reject)
  the rule fragment owns) before constructing the engine-side
  dispatch wrapper per [R-handler-001](#handler-derived-rules) (bare kwarg-only function;
  bindings are supplied as a fresh per-dispatch copy, not partial-applied as a shared
  object).
- **Trainable composition kind** — uses the
  [composition TOML primitive](#composition-toml): a
  pipeline-shaped declaration with `meta.kind = "trainable"` as the kind
  discriminator. The trainable composition declaration's full grammar — `meta` /
  `inputs` / `outputs` / scoped channels / a `[[preprocessors]]` sequence /
  exactly one terminal `trainable` node with `trainable.config` / `trainable.service_bindings`
  / `trainable.reads` / `trainable.output_schema` subsections / optional `merge` /
  optional `annotations` — is owned by the machine-readable `trainable.schema.toml`
  (documented at the `kind-schemas/README`); this doc cross-references rather than
  restating.

```toml
# Bare-function kinds — exactly one of these three top-level headers.
[transform]
# (or)
[service]
# (or)
[hook]

# Trainable kind — composition TOML primitive:
[meta]
kind = "trainable"
name = "<trainable-name>"
# ...full grammar at trainable.schema.toml
```

A bare-function handler declaration carrying zero or more than one of `transform` /
`service` / `hook` keys raises
[ContractViolation](#contractviolation) at
handler-declaration load.

(handler-toml-grammar-composition-kind-roster)=

A composition declaration's `meta.kind` value MUST be one
of the closed-enum composition-kind values (realized today as `"trainable"`,
`"bundle"`, and the nested `"pipeline"`; further kinds plug in via subsequent
engine changes).

{#composition-mirrors-the-pipeline}
#### A composition mirrors the pipeline

A [composition TOML](#composition-toml) is not a bespoke grammar — it is a
pipeline-shaped unit, governed by the same rule the
[hash-model family rule](#what-the-pipeline-hash-absorbs) names:

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

**Composition embedding is a consequence of the mirror.** Because a composition's
internal node sequence mirrors the pipeline's `nodes`, every **unlabeled** node
sequence — the outer pipeline's `nodes`, a nested `pipeline` composition's `nodes`, a
bundle's `nodes` — admits a `kind = "composition"` embed entry exactly as the outer
pipeline does (the pipeline node sequence is the owning grammar, in the pipeline
component reference). A trainable composition's `[[preprocessors]]` sequence is the
one **id-labeled** node sequence and admits handler entries only: each entry's `id`
is a load-bearing address (a hook preprocessor's deployment-transport key; the local
label that qualifies post-flatten to `<meta.name>.<id>`), and a substituted node is
anonymous — the trainable is a deliberate composition boundary, kept explicit.
Per-kind specialization decides how an embed folds:

- A **pure-substitution embed** (a [bundle](#bundle-toml)) is textually substituted
  into the enclosing node sequence **before** scoping
  and hashing — exactly as a bundle substitutes into the outer pipeline's `nodes`.
  It has no own hash domain; its content folds into the enclosing unit's hash.
- An **own-hash-domain embed** (a nested trainable, the nested
  `pipeline` kind) folds its own
  canonicalized hash **by reference** — exactly as the outer pipeline folds an embedded
  trainable composition by reference. The embedded unit's internal scope stays opaque to
  the enclosing unit's hash; only its overall identity hash flows up.

This is one mechanism, applied at both layers. The author surface for a composition
embedding another composition is the `kind = "composition"` node entry plus the
per-kind body templates in the kind-schemas folder —
`bundle.schema.toml` / `pipeline.schema.toml` / `trainable.schema.toml`.
The hash treatment is single-sourced at
[hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs).

{#bundle-composition-kind}
#### The bundle composition kind — pure-substitution grammar

(bundle-composition-kind-grammar)=

A **bundle** (`meta.kind = "bundle"`) is the pure-substitution composition kind — the
[engine-visibility spectrum's](#composition-toml) zero end. It declares a **minimal
grammar**: `[meta]` (`kind = "bundle"`, `name` — the closed `{kind, name}` key set
per [R-handler-006](#handler-derived-rules)), a **non-empty `[[nodes]]` sequence**,
and optionally `[annotations]`. A bundle whose `[[nodes]]` is empty substitutes
nothing and is a compose-time [ContractViolation](#contractviolation).

Its `[[nodes]]`
IS the pipeline node-entry grammar (the mirror-pipeline principle's shared
node-sequence grammar) — the one unlabeled sequence admitting `kind = "handler"` and
`kind = "composition"` entries exactly as the pipeline's `nodes` does, owned at the
[pipeline reference § `nodes`](#nodes-pipeline-node-sequence-kernel); the bundle adds nothing
and restates none of it.

The engine **textually substitutes** the bundle's `nodes` content into the enclosing
`nodes` sequence at the embed point at compose, **before that unit is scoped or
hashed**. Every downstream concern — validation, type-checking, merge resolution, hash
computation, dispatch-graph construction — then operates on the post-substitute inlined
form as if the handlers had been declared directly in the enclosing unit; a bundle's
channel names therefore continue the **enclosing scope** by name. A bundle carries **no
engine-owned dispatch, no [scoped channels](#scoped-channel), no `inputs` / `outputs`
boundary, no own `merge` declaration, and no own hash domain** — its content folds into
the **enclosing unit's hash** like directly-declared nodes ([hash-model § What the
pipeline-hash absorbs](#what-the-pipeline-hash-absorbs) owns the hash treatment). The
`name` is identity, not structure — never hashed (the family rule) — and after
substitution the bundle contributes no node of its own, so the name never appears in
the dispatch graph. A bundle's structural role is authoring-time DRY convenience; it is
invisible at every layer downstream of compose-time substitution.

The remaining sections live as siblings of the top-level kind key (bare-function kinds)
or as sections of the composition declaration (trainable composition kind). Each
bare-function section's applicability and discipline mode follow.

{#composition-service-bindings-identity-supply}
#### `service_bindings.<name>` — the composition's own service-binding identity supply

A trainable composition **supplies its own service-binding identity** — which model /
prompt-template satisfies each service-typed binding its nodes declare — through
composition-level `[service_bindings.<name>]` blocks, **exactly as the
[pipeline](#pipeline) supplies its handlers' service-binding identity** at its top
level (the [mirror-pipeline principle](#composition-mirrors-the-pipeline): one feature,
one grammar, one hash treatment across both layers). This is a **self-contained**
supply: the composition decides its own backend identity, not the embedding pipeline.

```toml
# Composition TOML — supplies the identity for the `llm` backend it declares below:
[service_bindings.llm]
type  = "acme_llm.dialogue"   # equals the declared binding's `type`
model = "qwen3.5-4b-gguf"         # identity value (hashed)

[trainable.service_bindings]
llm = { type = "acme_llm.dialogue" }   # DECLARES the binding (name + type)
```

Each service-typed binding the composition's nodes declare — the terminal
`trainable.service_bindings` backend (required, exactly one) **and** any service-kind
preprocessor's service-typed binding (declared on the handler it references) — requires one
covering `[service_bindings.<name>]` supply, matched by name, with `type` equal to the declared
binding's `type`. The block carries the [identity](#identity-service-binding)-field
**values** (model name, prompt template, version selectors), validated against the bound
service-type's `[identity_schema]` at compose; a transport field placed here, an
identity field absent, an orphan supply (no node declares it), or a type mismatch raises
[ContractViolation](#contractviolation) at compose, exactly as the pipeline-level
identity-supply checks fire. The **supplied identity values fold into the
[training-bundle-hash](#training-bundle-hash)** — they are a composition-level decision
defining what the trainable IS — while per-deployment [transport](#transport)
(endpoint, credentials, timeouts) lives in the deployment declaration's
`transport.<name>` block and is never hashed.

{#reads-applicable-to-all-three-bare-function-kinds-and-to-trainablereads-on-trainable-composition-declarations}
### `reads` — applicable to all three bare-function kinds (and to `trainable.reads` on trainable composition declarations)

Declares the handler's named, typed [input ports](#input-port) — not
channels. Every input-port name becomes a kwarg-only parameter on the handler's
Python signature (bare-function kinds); the runner, using the node's
[read-map](#read-map) (port → channel),
[projects](#projection) each input port's wired channel
value from the graph at this node's position into a kwarg dict keyed by port name,
validates it against the port's declared type via the engine-generated Pydantic model,
and supplies it to the bare author function. A signature mismatch raises
[ContractViolation](#contractviolation) at compose time per
[R-handler-001](#handler-derived-rules).

For the trainable composition kind, `trainable.reads` declares the trainable
composition node's [input ports](#input-port); the engine populates the kwargs from
the node's read-map and passes them to `adapter.invoke` (no author body — see
[R-handler-010](#handler-derived-rules)). The [R-handler-011](#handler-derived-rules)
discipline requires prompt-shaping content reaching a trainable composition node to
arrive via the trainable's input ports (produced by an upstream
[preprocessor](#preprocessor)) — never via
`trainable.config`.

**Section-discipline mode:**
[required, empty-allowed](#the-section-discipline-modes).
The closed-shape key MUST appear; the body MAY be empty when the handler's behavior is
fully determined by `bindings.<name>` declarations (e.g., an NPC-importer transform
reading nothing from upstream because all input is bound by external declaration file
path at compose time).

For the **trainable composition kind**, `trainable.reads` is **required,
body-required** — a justified divergence under the
[mirror-pipeline principle](#composition-mirrors-the-pipeline): the trainable's
input ports ARE the training-pair input side (`handler_enter.reads_snapshot`),
and prompt content reaches the node only through them per
[R-handler-011](#handler-derived-rules), so "declared nothing" is not a
meaningful state — a training record with an empty input side records nothing.

The composition's boundary `inputs` / `outputs` sections are distinct from
`trainable.reads` / `trainable.output_schema` (the boundary contract with the
embedding pipeline vs the terminal node's ports). Boundary `inputs` is
[required, empty-allowed](#the-section-discipline-modes); boundary `outputs` is
[required, body-required](#the-section-discipline-modes) — a justified divergence
from the pipeline's [presence-is-the-signal](#the-section-discipline-modes) `outputs`
arm (owned by the pipeline reference). A trainable composition's output surface IS
the training-record shape its [training-bundle-hash](#training-bundle-hash) covers, a
property of the trainable's emitter nature, so "exports nothing" is not a meaningful
state. The nested `pipeline` composition kind follows the pipeline's
presence-is-the-signal arm, not this trainable-specific body-required arm.

{#outputschema-applicable-to-transforms-services-and-trainable-composition-nodes-absent-on-hooks}
### `output_schema` — applicable to transforms, services, and trainable composition nodes; absent on hooks

Declares the handler's named, typed [output ports](#output-port) — not
channels.
(R-handler-001-output-validation)=

For bare-function kinds (transform / service) the runner validates the return
dict (keyed by output-port name) against the declared port shape via the
engine-generated Pydantic model, then routes the validated output-port values onto
channels using the node's [write-map](#write-map) (output-port → channel). For the
trainable composition kind, `trainable.output_schema` declares the trainable
composition node's output ports; the engine routes the adapter's response onto channels
via the node's write-map (the engine validates the response against the same port shape —
see [literal-equal rule](#handler-derived-rules) R-handler-005). Returning a key absent
from `output_schema` raises
[ContractViolation](#contractviolation), and omitting a declared output port from
the return dict raises the same class — both are top-level key-set facts about
the declared port set; a value that fails its declared shape *within* a declared
port (a type or constraint violation, including a required field absent inside a
nested object) raises
[SchemaValidationError](#schemavalidationerror) — all fire against the output-port
shape, upstream of the write-map, and all halt the pipeline per R-error-channel-003
(halt semantics).

The `output_schema` declares exactly the output ports the handler writes — no more, no
less. There is no side-channel write surface, no implicit port, no
metadata-tucked-into-return-dict route. This is the
[sole admission gate](#sole-admission-gate) — the engine's only path for admitting
values onto graph channels is output-port validation then write-map routing; the
handler cannot name a channel, so it cannot smuggle one onto the graph.

**Section-discipline mode:**
[required, body-required](#the-section-discipline-modes)
for transforms, services, and trainable composition nodes. The closed-shape key MUST
appear AND the body MUST declare at least one field — "declared nothing" is not a
meaningful state for a kind whose
[comparison-table](#comparison) `Declared writes`
cell reads `required`. **Hooks have no `output_schema`** — the kind discipline forbids
the declaration entirely; hooks return `None` per the
[comparison table](#comparison) and the runner has
no merge path for a hook return.

{#trainable-composition-kind-trainableoutputschema-declares-the-llm-emission-channels}
#### Trainable composition kind: `trainable.output_schema` declares the LLM-emission channels

For a [trainable](#trainable) composition node,
`trainable.output_schema` carries a load-bearing affirmative property:

> **The `trainable.output_schema` of a trainable composition node declares exactly
> the [output ports](#output-port) the backend emits — no more, no less; the node's
> [write-map](#write-map) routes each onto a [trainable channel](#trainable-channel).**

Every field corresponds to a
[trainable channel](#trainable-channel) the backend
constrained-decodes under the [literal-equal rule](#handler-derived-rules) (R-handler-005).
Trainable-appended bookkeeping channels — engine-computed latency, engine-computed
cost, structural-validity verdicts, downstream classifications — MUST NOT appear in
`trainable.output_schema`. A trainable composition node that wants metadata alongside
the emission relies on the split-with-downstream-transform pattern: the trainable
emits the channel literally, and a downstream transform reads the trainable channel
and writes the metadata channel.

The discipline is structural, not stylistic: the
[training projection](#training-contract) is taken **per
trainable channel**, with the
[training-bundle-hash](#training-bundle-hash) covering the
trainable composition's full structural scope. A `trainable.output_schema` mixing an
emission channel with a metadata channel would conflate the training projection with
non-emission state. The downstream-transform pattern routes the metadata to its own
channel (under the embedding pipeline, NOT inside the trainable composition's scope)
and keeps the trainable channel clean. See
[hash-model § Training-bundle-hash](#training-bundle-hash-construction)
for the per-trainable-composition hash composition.

{#servicebindings-applicable-to-services-hooks-and-trainable-composition-nodes-as-trainableservicebindings-forbidden-on-transforms}
### `service_bindings` — applicable to services, hooks, and trainable composition nodes (as `trainable.service_bindings`); forbidden on transforms

Declares the handler's **service-typed bindings** — named handles to
[service-types](#service-type) supplied at the
[pipeline](#pipeline) level. Each declared field's `type`
is a qualified service-type name (e.g., `"acme_llm.structured_output"`); the
binding resolves at compose time to a `services.<name>` attribute on the
runner-injected [ServicesProxy](#servicesproxy) kwarg
(bare-function kinds), or partial-applies directly into the engine-constructed dispatch
wrapper (trainable composition kind — no `services` kwarg because no author body). The
handler body invokes the bound backend via `services.<name>.invoke(...)` — the
**only** path from a handler body to external resources (see
[R-handler-007](#handler-derived-rules)). At dispatch the call reaches the bound service-type's
[service-type adapter](#the-service-type-adapter).

A service-typed binding is the handler's **external-call edge** in the graph — the
node-positional declaration that this node makes one external call per dispatch
(services, trainable composition nodes) or routes emission through a backend SDK
(hooks). The binding is the engine's structural backing for the comparison-table
`External call` column.

**Section-discipline mode:**

- **Transform.** `service_bindings` is **forbidden** entirely as a kind-discipline
  property. A transform's declaration MUST NOT carry the section; a transform has no
  external-call edge (per the
  [comparison table](#comparison) and
  [R-handler-004](#handler-derived-rules)), so a service-typed binding has no meaning at the
  transform's graph position. Presence of `service_bindings` on a transform
  declaration raises [ContractViolation](#contractviolation)
  at handler-declaration load — the mechanical half of
  [R-handler-004 (transform purity)](#handler-derived-rules), enforced by the kind-discipline
  closed-grammar mechanism; the diagnostic anchors on transform purity so the
  rejection message names the kind-discipline reason (no external-call edge) rather
  than "unknown section." The mechanical companion — the engine rejects signatures
  carrying a `services` kwarg for transform-kind handlers — fires at compose time per
  [R-handler-001](#handler-derived-rules).
- **Service.**
  [Required, body-required](#the-section-discipline-modes)
  with **exactly one** service-typed entry. The engine rejects construction at compose
  time when zero or more-than-one entries are present per [R-handler-008](#handler-derived-rules).
  A service with no service-typed binding has no external-call edge and is structurally
  a misclassified transform per [R-handler-003](#handler-derived-rules); a service with
  multiple bindings violates the comparison-table "exactly one external call" profile
  and breaks the consumer-side R-handler-002 divergence-detection seam (the
  `service_invocation` ↔ channel-write correspondence the paired-event analysis depends
  on). A service needing multiple external resources splits into separate handlers —
  see [R-handler-008 statement](#handler-derived-rules) for the rationale.
- **Trainable composition node.** `trainable.service_bindings` is required with
  **exactly one** service-typed entry, and the bound implementation MUST be a trainable
  backend (R-handler-008 expansion). "Trainable backend" is the integration property
  of the bound implementation (its adapter supports training capture); it is not a
  property of the service-type declaration.
- **Hook.**
  [Required, empty-allowed](#the-section-discipline-modes);
  per-kind validation governs body contents by emission channel:
  - **[Stdlib-emission hooks](#the-hook-kind).** Empty body;
    the hook emits via `logging`, file writes, stdout/stderr. The dispatch signature
    carries no `services` kwarg.
  - **[Backend-SDK-emission hooks](#the-hook-kind).** Exactly
    one entry MUST be declared; the hook routes emission through
    `services.<name>.invoke(...)`. The dispatch signature carries the `services` kwarg.

{#bindingsname-applicable-to-all-three-bare-function-kinds-and-via-trainableconfig-on-trainable-composition-declarations}
### `bindings.<name>` — applicable to all three bare-function kinds (and via `trainable.config` on trainable composition declarations)

Declares **compose-time bindings** — named values resolved at composition and supplied
to the handler as a fresh per-dispatch copy at each dispatch (the
[trust-model](#trust-model-vector) vector-4 seal).
See [compose-time binding](#compose-time-binding).
`bindings.<name>` is the unified compose-time-binding declaration; author names
bindings by domain meaning; N ≥ 0 bindings per handler.

A `bindings.<name>` value is **fixed at compose time** — the same value across every
dispatch of the composed pipeline. A value that must instead be chosen **per dispatch** on a
runtime key (looking up an NPC by an `npc_id` read off an upstream channel, say) is neither a
binding nor a transform: it is a **service** lookup —
[§ When NOT to use content bindings](#when-not-to-use-content-bindings) owns the boundary and
the two-case split.

Each declared binding entry declares a schema; the pipeline-entry supplies the value
**inline** or by reference to an **external declaration file**:

```toml
# Handler TOML — declares the binding's schema:
[bindings.config]
temperature = "float"
marker_set = "str"

[bindings.npc]
name = "str"
personality = "str"
combat_style = "str"

# Pipeline TOML — supplies values inline OR by external file reference:
[[nodes]]
kind = "handler"
name = "mypkg.dialogue_normalizer"
bindings = { config = { temperature = 0.7, marker_set = "brackets" } }   # inline object

# OR by external declaration file (the explicit `{ file = "..." }` form):
[[nodes]]
kind = "handler"
name = "mypkg.dialogue_normalizer"
bindings = { config = { file = "configs/dialogue_dev.toml" }, npc = { file = "npcs/captain_blackwell.toml" } }
```

{#binding-value-supply-grammar}
#### Binding value-supply grammar

A pipeline-entry binding value is **inline by default**; an external file is the
explicit form:

- A **bare value** — a **bare scalar** (`system_prompt = "You are a gruff tavern
  keeper."`) or a **bare array** (`probe_phrases = ["Care for a room?", "The usual?"]`) —
  is an **inline value**: the value itself, not a path. A bare string is content, not a
  filename; a bare array is inline list content, not a list of filenames. The bare value
  is the direct-supply form of a
  [single-field binding](#binding-value-supply-grammar-normalization).
- An **inline table** (`config = { temperature = 0.7 }`) is an **inline object value**,
  validated field-by-field against the binding's declared schema.
- The **`{ file = "<path>" }`** form is the **external declaration file** reference —
  the engine reads and validates the file's content at compose. `file` is an
  **engine-read binding key** (reserved alongside `compile` and `delivery`); a binding
  value of the shape `{ file = "..." }` is resolved by reading the named declaration
  file, never treated as an inline object with a literal `file` field. A relative
  `<path>` resolves against the directory of the **declaration TOML that supplied the
  binding value**: a pipeline TOML's binding values resolve against the pipeline
  declaration's directory; a composition TOML's (preprocessor) binding values resolve
  against the composition declaration's own directory — never the embedding pipeline's.
  A supplying declaration whose on-disk location the engine does not know MUST fail
  loud at resolution rather than resolve against any other directory (the wrong file
  must never be read and hashed as the binding's content). The same anchor rule covers
  a compile parameter's `{ file }` value — the parameter is written in the handler
  TOML, so it resolves against the handler declaration's directory.
- The **`{ null = true }`** form is the **explicit null** — the dialect's one spelling of
  *considered-and-null* for a nullable-declared field (TOML has no null literal). The
  [explicit-null law](#binding-value-supply-grammar-explicit-null) below owns its
  reservation, admission, positions, and normalization.

This makes the forms decidable at parse with no dependency on the handler's
declared schema: a bare string is always inline content; an external file is always the
`{ file = "..." }` form; an explicit null is always the `{ null = true }` form.
"Inline X" and "an external file containing X" resolve to the
same validated value and produce the **same** [pipeline-hash](#pipeline-hash) — the
external file is hashed by its **canonicalized content**, not by its path, so where the
value lives is hash-neutral (see
[hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs)).

(binding-value-supply-grammar-normalization)=

A **single-field binding** — a binding whose declared schema has exactly one field, of any
field type (scalar, array, or nested object) — MAY be supplied by any of these routes: the
**bare value** (scalar or array), the **one-field inline table** (its single field keyed
explicitly), the external **`{ file = "..." }`** declaration, the
**explicit null `{ null = true }`** where its single field is nullable-declared (the
[explicit-null form](#binding-value-supply-grammar-explicit-null) at the whole-binding
position — the bare null value, spelled), or, where the binding declares
one, its [ship-time default](#binding-ship-time-defaults). Every route **normalizes at the
compose join to the bare value** — the engine reduces every route to that one canonical form,
which is then the single basis for validation, for the pipeline-hash fold, and for delivery.
The differing spellings of one logical value therefore produce **one pipeline-hash and one
delivered shape**. This is what makes the "resolve to the same validated value" promise above
hold for a single-field binding: without normalization an external file — inherently a TOML
table — could never match a bare inline supply, and the two spellings would fold as two
hashes. The normalization is the supply-side counterpart of the wiring-sugar desugar — an
empty and a written-out identity map reduce to one normalized IR before hashing — so it is
hash-neutral by the same canonical-IR construction (see
[hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs)).

(binding-value-supply-grammar-explicit-null)=

**`{ null = true }` is the reserved explicit-null value form** — the dialect's one spelling of
*considered-and-null*. TOML has no null literal, so without a reserved form a nullable-declared
field in an engine-read TOML value position could express null only by omission — collapsing
*considered-and-null* into *forgot*, the distinction
[exhaustive declaration](#architecture-exhaustive-declaration) exists to preserve. The form
carries that principle to the field level: presence-coverage rules admit **no nullable
exemption** — a considered-and-null field is *present as `{ null = true }`*; an absent declared
field is always the coverage violation (the coverage rules own the presence law;
[R-pipeline-001](#R-pipeline-001)'s Transport coverage and Hook transport coverage are the
deployment-side instances).

- **Reservation.** `null` is an **engine-read key**, resolved by the same reserved-key rule as
  `file` above: a value of the shape `{ null = true }` IS the explicit null, never an inline
  table carrying a literal `null` field.
- **Admission.** The form is admitted **only where the target field is nullable-declared** (the
  `"<T> | None"` type union / the `nullable` shorthand — § TOML field type discipline). Supplied
  for a non-nullable target it raises [ContractViolation](#contractviolation) at compose.
- **Spelling.** The spelling is **forced**: exactly the single key `null` with the value `true`.
  `{ null = false }`, a non-boolean value, or any additional key raises
  [ContractViolation](#contractviolation) at compose — there is no "not null" spelling (a
  present value already is one), exactly as a `{ file }` value is forced to be a path.
- **Positions.** Recognition applies at **every engine-read TOML value position that feeds a
  declared field**, across the dialect's value-position classes: **binding values** (a pipeline-
  or composition-entry `bindings.<name>` supply, the field values of an inline object, the
  content fields of an external declaration file, and a declared
  [ship-time default](#binding-ship-time-defaults)'s value — a nullable-declared single field
  MAY declare `default = { null = true }`, and field values inside a multi-field `default`
  object recognize per-field), **identity values** and **config values** (a
  `service_bindings.<name>` block's identity fields; its `config` sub-block and a trainable
  composition's `[trainable.config]`), and **transport values** (a deployment `transport.<name>`
  block; a `hook_transport."<as_written_node_name>"` block). At the **whole-`bindings.<name>` supply
  position** the target field is a single-field binding's one declared field — admitted iff
  that field is nullable-declared (the explicit spelling of the bare-value route, per the
  normalization region above); a multi-field binding's whole-value position is never a
  nullable-declared target, so `{ null = true }` supplied for a whole multi-field binding
  raises [ContractViolation](#contractviolation). Recognition is position-level, never
  recursive: it applies to the value supplied *for* a declared field, and never reaches inside a
  composite value's interior (a config `table` field's inner keys are data — though the value
  supplied for the `table` field itself is a declared-field position — and a collection member
  is not a field position). Identity and config fields admit no nullable declaration (the
  service-type reference's § `[transport_schema]` owns nullable placement), and a
  [compile directive](#the-compile-directive-sub-form)'s parameter values carry none either, so
  at those positions a recognized form always rejects under the admission rule —
  recognized-and-rejected, never silently absorbed as data.
- **Semantics.** The form is a spelling, not a value class: at the compose join it normalizes to
  the **null value** — the same normalization join the single-field routes reduce through
  (above) — folds into whatever hash its position folds into as that null value, and is
  delivered as Python `None`.

Every supply form is resolved and validated once at compose; the engine then supplies the
handler a fresh per-dispatch copy of the value at each dispatch, fixed across every
dispatch of this composed pipeline. The author's function signature carries one kwarg
per declared `bindings.<name>` entry, plus the input-port kwargs (one per `reads` port)
and (where applicable) `services`. **The delivered shape is plain data:** a multi-field
binding arrives as a plain `dict` (field name → value; the dispatch's private copy, safe
to mutate), a single-field binding as the bare value — never an attribute-bearing object (the
copy/freeze delivery machinery and the trust-model's vector-4 seal are defined over
plain data shapes).

{#binding-ship-time-defaults}
#### Ship-time defaults

A `bindings.<name>` entry **MAY** declare a per-binding **ship-time default** — a value
the engine supplies when the pipeline entry omits this binding. Defaults live at the
`bindings.<name>` level (compose-time-resolved binding values), never at the
channel-declaration level — channel fields (`reads` / `output_schema`) forbid defaults
by [invariant I1](#invariants-and-derived-rules) (an optional channel is a lying
default), but a compose-time binding is not a channel.

```toml
[bindings.config]
default = { temperature = 0.7, marker_set = "brackets" }   # ship-time default for the whole binding
temperature = "float"
marker_set = "str"
```

The supply rule is structural:

- A binding that **declares a default** MAY be **omitted** at the node — the engine
  supplies the declared default. The node MAY still supply a value to override it.
- A binding that declares **a value schema** and **no default** MUST be **supplied** at
  the node — the pipeline reference's [binding-supply matching](#R-pipeline-001) enforces
  this at compose.

The hash treatment has two contributions, both load-bearing: the **effective value**
(supplied-or-default) is hashed at the **supply site** as part of the pipeline-hash's
per-node binding contribution (two compositions differing only in which default-bearing
bindings they override differ in pipeline-hash); and the **declared default itself**
folds into the **handler-declaration content hash** (changing a shipped default is a
handler-declaration change that shifts the pipeline-hash of every composition resolving
that handler). See
[hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs).

The mechanism has a second declaration surface: a service-type `[config_schema]` field
MAY declare a per-field ship-time default under the same supply rule. The config-side
realization — its compose-time supply check and its supply-site hash treatment — is
owned by the service-type reference's § The `[config_schema]` contract; the engine's
native trainable backends' sampling dials are the mechanism's first shipped consumers.

`bindings.<name>` is the right home for any compose-time-bound value the handler needs
at runtime: a `marker_set` enum on a charset-filter normalizer, a prompt template for
a service handler (where the template is fixed across the composed pipeline), NPC
character data sourced from an external declaration, structured fixtures, mapping
tables. Binding values contribute to the
[pipeline-hash](#pipeline-hash); a re-composition with
different binding values produces a different pipeline.

{#the-compile-directive-sub-form}
#### The `compile = "..."` directive sub-form

A `bindings.<name>` entry MAY declare `compile = "<compiler>"` to invoke the engine's
compile-affordance machinery: the engine resolves the named **compiler**, runs it once at
binding resolution, and delivers the produced artifact as the binding's engine-owned kwarg
value. The compiler name resolves by the same **bare-vs-namespaced** split the
[field validators](#field-validators) use:

- **A bare name** (no dot — `regex`, `jinja`, `json_schema`) names a **blessed
  first-party compiler**: the engine-shipped compile vocabulary, the bare-name space the
  engine reserves for itself (exactly as the bare validation keywords are the engine's
  standard set). New first-party compilers are blessed into this space as they ship.
- **A namespaced name** (a dotted qualified name — `mypkg.compile_grammar`) names a
  **third-party compiler**, resolved through the same
  [dotted-path resolution](#dotted-path-resolution) and source-AST audit as any foreign
  handler or validator ([handler resolution](#architecture-handler-resolution) owns the
  sequence). The bare and namespaced spaces are disjoint by construction, so a third-party
  compiler can never shadow a blessed one.

**The directive is the binding; the node supplies nothing for it.** For a compile-directive
binding the `compile = "..."` directive and its parameter keys *are* the complete binding
declaration: the engine resolves the named compiler at binding resolution, runs it once, and the
produced artifact is the binding's engine-owned value. A pipeline node or preprocessor entry that
uses a compile-directive binding therefore supplies **nothing** for it — no inline scalar, no
inline table, no `{ file = "..." }`. A value supplied for it is rejected at compose with a
ContractViolation under [binding-supply matching](#R-pipeline-001). Contrast an ordinary
`bindings.<name>` that declares a *schema*: there the node satisfies the declaration by supplying
a value.

**The compiler contract is closed.** A compiler is a **deterministic `params → artifact`**
bare kwarg-only function: the engine introspects its signature against the directive's
declared parameters (the sibling keys), binds those parameters at compose — engine-owned,
so authors write no factory or closure — and runs it to produce the artifact. Its
determinism is the author's contract, held as handler-body purity is: the
[R-handler-pure-module](#handler-derived-rules) source-AST audit runs on a third-party
compiler's module unchanged, and review covers the body. The engine does not interpret the
produced artifact beyond this contract — a compiled `re.Pattern`, a Jinja `Template`, a
third-party grammar object is forwarded as-is, an engine-owned kwarg covered by usage
discipline (not copied per dispatch, not the [reference-binding](#reference-bindings)
subtype).

Compile failures land at two stages. A **bare name no blessed compiler carries** is
rejected at **parse**: the bare-name space is closed, so the closed-grammar check fires a
[ContractViolation](#contractviolation) at declaration load. The other two cases — a
namespaced name that does **not** resolve, or parameters the compiler rejects (a malformed
`regex`, an unparseable `jinja` template, an invalid `json_schema`) — are
resolution-dependent and raise [ContractViolation](#contractviolation) at binding
resolution (compose time), never at dispatch. One boundary sits outside that closed
channel: a **blessed** compiler's missing optional backing library (`jinja2` /
`jsonschema`) surfaces as the library's own raw `ImportError` when the compiler runs —
an environment failure, not a declaration defect, so it is not a ContractViolation
(a **third-party** compiler MODULE whose import fails at resolution stays inside the
closed channel). The `compile` directive and its parameters
are part of the `bindings.<name>` declaration and contribute to the
[pipeline-hash](#pipeline-hash), so changing the named compiler or its parameters is a
composition change.

```toml
[bindings.normalizer]
compile = "regex"
pattern = "\\[[^\\]]+\\]"
flags = "IGNORECASE"
```

`regex` is the worked first-party compiler above — parameters `pattern` and `flags`, its
artifact a compiled `re.Pattern`. The other blessed first-party compilers carry contracts
of the same closed shape:

```toml
[bindings.greeting]
compile = "jinja"
source = "Hello, {{ name }}!"
```

`jinja` — parameter `source`, the template text; its artifact a compiled
`jinja2.Template`.

```toml
[bindings.profile_check]
compile = "json_schema"
schema = { type = "object", required = ["name"] }
```

`json_schema` — parameter `schema`, the JSON Schema; its artifact a compiled `jsonschema`
validator.

**A compile parameter is supplied inline OR from a file.** A parameter's value MAY use the
engine's external-file form — `<param> = { file = "<path>" }` — the same form a binding value
uses (the [`{ file = "..." }` external-file form](#binding-value-supply-grammar), whose
relative-path anchor rule — the supplying declaration's own directory, here the handler
TOML's — applies unchanged). For a compile
parameter the engine reads the named file as **text** at binding resolution and passes that text
to the compiler as `<param>`; the **compiler** parses it (`json_schema` reads the text as JSON;
`jinja` and `regex` use the text directly). A parameter has one value, so it is inline or
file-supplied by construction — there is no twin key and no `_file` suffix. The compiler contract
is unchanged: the compiler declares `<param>` and never sees the path or the `file` key — the
engine reads the file to the text it passes. How the parameter folds into the
[pipeline-hash](#what-the-pipeline-hash-absorbs) — text content, never the path;
inline and file-supplied as distinct declarations — is owned by hash-model's
compile-directive bullet there. A `{ file }` the engine cannot read — or text the
compiler then rejects — raises [ContractViolation](#contractviolation) at compose, the same rule
the directive's other failures take above.

**Three parameter KEYS are reserved.** A compile-directive binding's parameter keys (the
sibling keys of `compile`) MUST NOT include `delivery`, `default`, or `file`: `delivery`
and `default` are engine-owned binding-level directives, and `file` is the reserved
external-file supply key. A compile-directive binding naming any of them as a parameter key
raises a closed-grammar [ContractViolation](#contractviolation)
([R-handler-006](#handler-derived-rules)) at parse — a compiler declaring such a parameter
renames it (`source_file`, …). This reserves only the top-level KEY: supplying a parameter's
VALUE from a file — `<param> = { file = "<path>" }` (above) — stays fully legal; that
external-file value form is the engine's own.

A non-trivial JSON Schema or a large template is unwieldy inline; the external-file form keeps it
in its own file:

```toml
[bindings.profile_check]
compile = "json_schema"
schema = { file = "schemas/profile.json" }
```

```toml
[bindings.greeting]
compile = "jinja"
source = { file = "templates/greeting.jinja" }
```

`schema`'s value comes from `schemas/profile.json` (the `json_schema` compiler parses the text as
JSON); `source`'s comes from `templates/greeting.jinja` (the `jinja` compiler uses the template
text directly). The form is uniform — any parameter of any compiler, first-party or third-party,
may be supplied from a file this way; small parameters like `regex`'s `pattern` / `flags`
ordinarily stay inline.

{#trainable-composition-kind-trainableconfig-carries-compose-time-generation-parameters-not-prompt-content}
#### Trainable composition kind — `trainable.config` carries compose-time generation parameters, NOT prompt content

For the trainable composition kind, the compose-time-binding equivalent lives at
`trainable.config` (per the
[composition TOML primitive](#composition-toml)). Per
[R-handler-011](#handler-derived-rules), **prompt-shaping content MUST NOT appear in
`trainable.config`** — it MUST be produced by an upstream
[preprocessor](#preprocessor) and arrive via
`trainable.reads`. Templates, system prompts, prompt scaffolds, content-injection
strings are not valid `trainable.config` entries; they're preprocessor outputs.

**Section-discipline mode:**
[required, empty-allowed](#the-section-discipline-modes).
A handler MAY declare zero `bindings.<name>` entries; presence of a `bindings` table
heading itself is not required when no bindings are declared (each named binding stands
as its own closed-shape key).

{#transportschema-applicable-to-hooks-only}
### `transport_schema` — applicable to hooks only

Declares the per-deployment transport configuration the hook reads at runtime: log-file
path, formatter selector, output stream choice for a stdlib-emission hook;
conditionally empty for a pure backend-SDK-emission hook (the backend-SDK transport
lives in the bound service-type's `transport_schema`, not the hook's). For
mixed-channel hooks (stdlib AND backend-SDK), the hook's `transport_schema` carries the
stdlib-side config and the bound service-type carries the backend-SDK transport — see
the [Hook section in handler-kinds](#the-hook-kind) for the
channel-discipline detail.

`transport_schema` values live in the deployment declaration and are NOT contributed to
any hash per [transport](#transport) — environment-specific
values that may change per deployment without affecting the pipeline contract. A hook
`transport_schema` field additionally admits the **`secret_ref`** token (top-level only,
optionally `secret_ref | None`) for a credential — a
secret reference the deployment supplies
as `"[scheme]payload"`, shape-checked at pipeline-declaration load and delivered to the
hook body **unresolved** (the body resolves it via the blessed resolver at emission time;
the deployment reference's § Secret references owns the grammar and the never-fetches
split — [R-deployment-003](#R-deployment-003)).

**Validation keywords — none admitted.** A `transport_schema` field declares its type
token only (plus the transport-only nullable axis and the `secret_ref` token above),
never a validation keyword: the engine has no value-enforcement point on transport —
declared fields' supplied values pass through opaque per the coverage check's
key-set-plus-reserved-form posture
([R-pipeline-001 § transport coverage](#R-pipeline-001-transport-coverage)) — so an
attached keyword would be a silently-unenforced constraint, the no-op class the engine
forecloses. The closed grammar rejects it at declaration load
([R-handler-006](#handler-derived-rules)).

**Delivery follows the emission boundary** — transport values deliver to whichever
boundary does the emission. For **stdlib emission** the emitting boundary is the hook
**body**: the deployment's `hook_transport."<as_written_node_name>"` block supplies the
declared `transport_schema` fields, and the engine delivers each field to the body as
a kwarg exactly like a binding — the field names join the R-handler-001
[signature union](#handler-derived-rules), each value a fresh per-dispatch copy,
deployment-supplied and hash-excluded. For **backend-SDK emission** the emitting
boundary is the bound service-type's **adapter**: the deployment's `transport.<name>`
block for the hook's service binding reaches the adapter as `**transport_extra`,
exactly as for a service handler's binding (the service-type reference's § Closed
dispatch-kwargs owns that surface). A `transport_schema` field name MUST NOT collide
with a declared input-port name, a `bindings.<name>` name, or the reserved `services`
kwarg (the engine reads it to inject the [ServicesProxy](#servicesproxy); a transport
field by that name would be clobbered at dispatch) — the collision raises
[ContractViolation](#contractviolation) at handler-declaration load (every colliding
name is declaration-local, so the check fires at the earliest stage, before any
composition references the handler).

**Section-discipline mode:**
[required, empty-allowed](#the-section-discipline-modes).
The closed-shape key MUST appear on every hook declaration.

(transport_schema-stdlib-non-empty)=

**A stdlib-emission hook MUST declare a non-empty `transport_schema`; a pure
backend-SDK-emission hook's `transport_schema` MUST be empty-but-present.** The
derivation is the emission boundary (above): a stdlib-emission hook emits from its own
**body**, so its transport configuration — the format selector, output-stream choice,
and any log routing the body needs — has no other home; unlike a backend-SDK hook, it
binds no service-type whose `transport_schema` could hold it. An empty `transport_schema`
on a stdlib-emission hook is therefore rejected at compose as a
[ContractViolation](#contractviolation) — the body would run with no configured
transport. The pure backend-SDK case is the inverse: its body emits nothing directly (the
bound service-type's adapter does), so the hook's own `transport_schema` declares zero
fields while the closed-shape key still appears. A mixed stdlib-AND-backend hook carries
the stdlib-side config and is non-empty for the same reason as the stdlib case.

**Transforms and services have no `transport_schema`** — the declaration is
hook-only by kind discipline.

{#annotations-applicable-to-all-kinds}
### `annotations` — applicable to all kinds

Free-form prose for author notes, usage examples, caveats. The declaration is **truly
optional** per
[exhaustive-declaration](#the-section-discipline-modes);
presence-or-absence and content are author choice. Excluded from every hash
(`annotations` is metadata-class — excluded from the training-bundle-hash on
trainable composition declarations and from the pipeline-hash via that exclusion);
edits are hash-neutral and never invalidate trained artifacts.

More than hash-excluded, `[annotations]` is **engine-opaque**: it is graph-inert — not a
node, declaring no channels — the engine never reads it, and nothing in it is ever
delivered to a handler or routed through the dataflow. It is purely a **consumer
surface**: author- and tooling-facing metadata (such as the `postprocessors` UI-grouping
field below) that Studio and other consumers read directly; the engine routes nothing
from it.

The `annotations` declaration is the canonical home for author prose about a declaration and
its fields. The one field-level exception is a [trainable](#trainable) composition node's
`trainable.output_schema` field `description` — model-facing contract content that conditions
generation, kept on the field (§ TOML field type discipline); all other field prose lives here.

{#postprocessors-on-trainable-composition-declarations-ui-grouping-declaration}
#### `postprocessors` on trainable composition declarations (UI-grouping declaration)

For the trainable composition kind, `annotations` MAY carry a
`postprocessors = ["<handler_ref>", ...]` list naming downstream handlers Studio groups
visually with the trainable in trace views. This is **engine-opaque** — no engine
consumption, no validation, no audit, no hash contribution; the field is purely a UI
affordance. The preprocessor / postprocessor asymmetry: preprocessors live INSIDE the
trainable composition declaration as `[[preprocessors]]` sequence entries (structural
membership, engine-semantic — they fold into the training-bundle-hash); postprocessors live OUTSIDE
in the embedding pipeline and are merely **named** here for UI grouping.

---

{#toml-field-type-discipline}
## TOML field type discipline

Field types inside the schema sections follow a small closed vocabulary, so the engine
can generate Pydantic models from declared types at handler-declaration load and
validate kwarg projections + return dicts mechanically. For binary content and other
types beyond the LCD-typed subset, see § Channel-type discipline below +
[hash-model § Cross-dialect portability](#cross-dialect-portability)
for the documented capability boundary.

{#types-allowed-in-reads-and-outputschema}
### Types allowed in `reads` and `output_schema`

The [channel-field type](#channel-field-type) tokens the TOML declaration grammar admits are Pydantic-aligned:

- **Primitives.** `"str"`, `"int"`, `"float"`, `"bool"`.
- **Collections.** `"list[<T>]"`, `"dict[str, <T>]"`, `"tuple[<T>, <U>, ...]"` where
  `<T>`/`<U>` are themselves declared types.
- **Optionals.** `"<T> | None"` where the field is nullable; equivalent to Pydantic's
  `Optional[<T>]`. The union nests wherever a declared type is admitted (`list[str | None]`
  is a list of nullable members). In an engine-read TOML value position a nullable field's
  null value is spelled by the reserved
  [explicit-null form](#binding-value-supply-grammar-explicit-null) `{ null = true }` —
  TOML itself has no null literal, and that form's recognition is field-position-level, so a
  null collection MEMBER is expressible at runtime boundaries (channel values, JSON API
  inputs) but never in an engine-read TOML value position.
  Note that nullability on a declared field is a separate axis from
  declaration-existence — a missing top-level output key still raises
  [ContractViolation](#contractviolation) regardless of nullability
  (input-boundary and nested-field absences surface as
  [SchemaValidationError](#schemavalidationerror), per the error-channel's
  routing).
- **Enums.** `"Literal['a', 'b', 'c']"` for closed-enum values. Out-of-set values raise
  [SchemaValidationError](#schemavalidationerror) at dispatch
  (for `reads`) or at return (for `output_schema`). A `Literal` member is a **string, int, or
  bool** — the scalar types Python's `Literal` admits and the engine's IR carries. `float` is
  **not** a valid `Literal` member; `bytes` is excluded as the non-LCD boundary with no TOML
  primitive (see [hash-model § Cross-dialect portability](#cross-dialect-portability)). String
  members are the common case; int and bool are native TOML primitives, so all three are
  reachable from a TOML-authored token.
- **Nested objects.** A field whose value is a structured object declares its members
  in a `[<section>.<field>.fields]` sub-table — the presence of the `.fields` sub-table
  is what marks the field as a nested object (there is no `object` type token). Each
  member is itself a normal field declaration of any type in this vocabulary, including
  a further nested object via its own `.fields` sub-table, so nesting recurses to any
  depth. Nested shapes contribute to the pipeline-hash (and, where the channel is inside
  a trainable composition declaration, to that trainable composition's
  training-bundle-hash).
- **Lists and dicts of nested records — the composite-slot sub-tables.** A composite
  type whose *element* is a nested record declares the element schema in a sub-table
  named by the composite's IR slot, carrying `.fields`: a
  `[<section>.<field>.item.fields]` sub-table declares `list[<nested record>]`
  (`ListType.item`); a `[<section>.<field>.value.fields]` sub-table declares
  `dict[str, <nested record>]` (`DictType.value`). Presence marks shape — the exact
  analogue of `.fields` marking a bare nested object; there is no new type token, and
  the sub-table is a single element schema, never a TOML array-of-tables (`[[…]]`
  enumerates N tables; an element schema is ONE). Recursion composes naturally:
  `.item.item.fields` declares a list of lists of records, and a record member may
  itself carry a composite slot. A field declares **exactly one** shape marker — a
  `type` token, `.fields`, `.item`, or `.value` — and the element sub-table carries
  exactly its one shape key: validation keywords (`minItems`, `uniqueItems`, …) attach
  on the field's own table exactly as for a token-typed field, and the element admits
  no `nullable` (a nullable *member* is declared on that member's own declaration
  inside `.fields`; a nullable nested slot itself is declared with `nullable` on the
  field's own table beside `.fields` — yielding `Optional(<nested record>)`, exactly as
  `nullable` beside `.item`/`.value` wraps the list/dict. What the token grammar's
  existing boundaries leave inexpressible is what carries no shape marker at all: a
  tuple-of-nested slot).

A nested `mood` object on an `output_schema` channel, itself carrying a nested `source`,
beside a `history` channel typed as a list of turn records:

```toml
[output_schema]
status = "str"

[output_schema.mood.fields]
intensity = "int"
label     = "Literal['happy', 'sad', 'angry']"

[output_schema.mood.fields.source.fields]
model      = "str"
confidence = "float"

[output_schema.history.item.fields]
speaker = "str"
line    = "str"
```

A handler authoring a type outside the engine's Pydantic IR raises
[ContractViolation](#contractviolation) at
handler-declaration load (engine startup). The closed vocabulary is what makes the
[literal-equal rule](#handler-derived-rules) work for trainable composition nodes: every
declared channel type maps to a backend-supported structured-output constraint without
an authored derivation step. Channel types beyond this LCD subset (notably `bytes` —
which TOML cannot express but direct-Pydantic authoring can) are governed by § Channel-
type discipline below.

**Field-declaration spelling — bare token or inline table.** A field declaration is
canonically an inline table keyed by `type` — `<name> = { type = "<token>", … }` — and a
**bare type-token string** is exact shorthand for the type-only table: `mood = "str"` and
`mood = { type = "str" }` declare the **identical field**, parsing to the same `FieldDecl`
IR. The two spellings are therefore **hash-neutral** — the canonical IR erases the
difference, exactly as identity-map sugar and inline-vs-file binding supply are hash-neutral
([hash-model § How the hashes are constructed](#how-the-hashes-are-constructed)). Both
spellings are conformant; a field carrying any per-field key below (a `description`, a
validation keyword, `nullable`) uses the table form to carry that key alongside `type`.

Per-field keys beyond the type token (engine-declared, closed set):

- `description` — a one-sentence statement of what a field IS, and **model-facing contract
  content** ([hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs) owns the
  hash treatment and its derivation).

(toml-field-type-discipline-description-admission)=

A `description` is admitted ONLY on a [trainable](#trainable) composition node's
`trainable.output_schema` fields (including nested members), on a wire family that delivers them.
It is **not** general-purpose field documentation admitted everywhere: at any other field
position — a transform / service / hook `reads` or `output_schema` field, a `bindings.<name>`
field, a pipeline-family `[inputs]` / `[outputs]` boundary field, a service-type schema field —
`description` is not in the closed grammar and raises [ContractViolation](#contractviolation) at
load. On a `trainable.output_schema` field bound to a wire family that cannot carry descriptions, a
declared `description` is likewise a compose-time [ContractViolation](#contractviolation)
([hash-model](#what-the-pipeline-hash-absorbs) owns why; the
[native-library trainable-backend wire entries](#native-library-trainable-backends-description-delivery)
own which wires deliver).

  The bound wire form compiles the declared output schema — descriptions included — into the
  backend's structured-output decode constraint (per [R-handler-005](#handler-derived-rules)), so a
  `description` **folds into both hashes** where admitted. Author prose for a non-admitted field
  lives in the declaration's `[annotations]` block where the grammar declares one; the
  pipeline-family `[inputs]` / `[outputs]` grammars declare no `[annotations]`, so their field prose
  lives in TOML comments. A field's **validation keywords** fold into the hashes at every position
  they are admitted — they constrain the accepted value space, which is structural (per
  [§ Validators](#field-validators)).
- the **validation keywords** — one grammar, attached as **direct field keys**: a **bare**
  key is a JSON Schema validation keyword applicable to the field's declared type
  (`pattern = "^\\d{4}"`, `minLength = 4`); a **namespaced (dotted)** key is a registered
  third-party validator whose value is its parameter table — per
  [§ Validators](#field-validators) below.
- `nullable` — boolean; defaults `false`. Equivalent shorthand for the `"<T> | None"`
  type union.

There is no per-field `default` key. Defaults on declared channel fields — `reads` and
`output_schema` — would imply optional channel presence, which [invariant I1 (no implicit
contracts)](#I1) forbids as a category. Ship-time-default surfaces live at the `bindings.<name>`
level (compose-time-resolved binding values), not at the channel-declaration level.

Field-name choice (the keys inside the schema sections) is author territory; only the
type-vocabulary entries and the per-field keys above (the metadata keys and the
built-in constraint keywords) are engine-declared. See
[exhaustive-declaration § What this discipline does NOT cover](#what-this-discipline-does-not-cover).

{#types-allowed-in-bindingsname-schemas}
### Types allowed in `bindings.<name>` schemas

Compose-time bindings are values resolved and validated once at composition and
delivered to the handler as a fresh per-dispatch copy at each dispatch. The vocabulary
is the same Pydantic-aligned token set as `reads` / `output_schema`
([§ Types allowed in `reads` and `output_schema`](#types-allowed-in-reads-and-outputschema)
owns the token grammar).

A binding's value MAY be a single **scalar** rather than a table of fields — a scalar
binding is the home for an inline prompt template, system prompt, or other single-value
content. Under the [§ Binding value-supply grammar](#binding-value-supply-grammar) a
bare-string supply is the inline scalar value (content, not a path); an external file is
the `{ file = "..." }` form. (R-handler-011 routes prompt-shaping content through a
preprocessor's `bindings`, supplied as exactly such an inline scalar or external file.)

Pipeline-entry values are supplied in any form the
[§ Binding value-supply grammar](#binding-value-supply-grammar) admits; every route goes
through the same Pydantic validator and resolves to the
same value, delivered as a fresh per-dispatch copy at each dispatch.
`bindings.<name>` admits the full IR vocabulary — material content (prompt templates,
mapping tables, structured fixtures) lives in a single binding declaration, supplied
inline for short content or by external file for content-heavy declarations:

```toml
# Handler TOML:
[bindings.npc]
name = "str"
personality = "str"
backstory = "str"

# Pipeline TOML — value by external declaration file:
bindings = { npc = { file = "npcs/captain_blackwell.toml" } }
```

The `compile = "..."` directive is a compile-affordance sub-form: a named compiler's
artifact replaces the schema-declaration shape (the engine resolves and runs the compiler
at binding resolution and delivers the artifact as an engine-owned kwarg). See
[§ The `compile = "..."` directive sub-form](#the-compile-directive-sub-form).

{#field-validators}
### Validators — named value constraints

(field-validators-kernel)=

A field declaration attaches **named value constraints** beyond its type token, and they
share **one grammar**: every non-structural field key is a validation keyword, in two classes
the key itself distinguishes — a **bare** key (no dots) is a built-in standard constraint (a
JSON Schema validation keyword applicable to the field's declared type), and a **namespaced
(dotted)** key is a registered third-party validator. There is no separate `validators` list.

The two classes resolve as follows — which the key is decides what resolves it:

- **Bare keys are the closed standard vocabulary.** The bare attachable constraints ARE the
  JSON Schema validation keywords (draft 2020-12) applicable to the field's declared type,
  carrying the standard's semantics and the standard's own applicability mapping — numeric
  keywords (`minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`) apply to numeric
  types; `minLength` / `maxLength` / `pattern` to strings; the array **cardinality** keywords
  `minItems` / `maxItems` to the variable-length `list[<T>]` only and the array **distinctness**
  keyword `uniqueItems` (orthogonal to cardinality) to any array — a `list[<T>]` or a fixed-arity
  `tuple`; the object **cardinality** keywords `minProperties` / `maxProperties` to the open-keyed
  `dict[str, <T>]` only; `enum` to any declared type — not a hand-rolled engine matrix. A
  fixed-arity `tuple` fixes its element count and a fixed-field nested object its property count,
  so a *cardinality* keyword on either can never apply (the fail-loud-inapplicability deviation
  below); distinctness is independent of arity, so `uniqueItems: true` on `tuple[int, int]` — the
  two elements must differ — stays meaningful and applicable. The generated Pydantic models
  enforce them via engine-authored per-keyword check functions attached as validators in
  authored key order (never Pydantic's native `Field(ge=…)`-style constraints, which run at
  type-validation before any attached validator and so could not honor the authored-key-order
  interleave rule above); a violated constraint surfaces
  as [SchemaValidationError](#schemavalidationerror) with `constraint_violated` = the keyword
  name (the error-channel reference owns the `constraint_violated` payload vocabulary, which
  already carries these names; its `type` / `required` / `nullable` members are field axes the
  declaration grammar itself carries — the type token, declaration presence, the nullability
  axis — never attachable constraints). A **bare key the engine does not recognize** — neither a
  structural metadata key (`type`, the `description` where admitted, the `default` where admitted, the `nullable`
  shorthand) nor a standard validation keyword — raises [ContractViolation](#contractviolation)
  at load, naming the closed validation vocabulary. (A bare standard keyword is
  render-eligible to a trainable's submitted wire constraint where the bound wire family's
  accepted set admits it — § Trainable backends owns that accepted matrix.)
- **Namespaced (dotted) keys are registered third-party validators.** A dotted qualified name
  registered under the `conjured.validators` [entry-points group](#entry-points-group) attaches
  as a field key whose **value is its parameter table** — `{}` for a parameterless validator —
  resolved through the sibling mechanism ([handler resolution](#architecture-handler-resolution)
  owns the sequence). A namespaced validator is opaque code; on a `trainable.output_schema` it
  is in the wire-form rejected class, never render-eligible (§ Trainable backends owns it).
  **The `value` kwarg's delivered shape is plain data**: the validator receives the field's
  value as plain data — a nested-object value arrives as a plain `dict`, never an
  attribute-bearing generated-model instance; scalars and collections arrive as themselves —
  the same plain-data delivery posture the binding-supply grammar fixes for handler kwargs.

**Both classes attach as direct field keys** — exactly the standard's shape, the keyword
carrying its value directly; a bare standard keyword and a namespaced validator sit side by
side on one field:

```toml
[output_schema]
release_date = { type = "str", pattern = "^\\d{4}", minLength = 4,
                 "mypkg.is_iso_date" = {},                       # parameterless third-party validator
                 "mypkg.in_range" = { min = 1900, max = 2100 } } # parameterized — the value IS the params table
```

**Enforcement order is authored key order.** The engine enforces a field's validation keywords
in the order they are authored on the field, across both classes — a bare standard keyword and a
namespaced validator interleave by declaration position, with no class precedence. The loader
normalizes every key into the same internal validation-spec representation, and the order folds
into the hashes (below), so a reorder is a composition change.

**One named deviation from the standard — fail-loud inapplicability.** JSON Schema
silently ignores an inapplicable keyword (`minimum` on a string is a no-op there); the
engine instead raises [ContractViolation](#contractviolation) at compose. A declared
constraint that can never apply is a composition defect, and silent inapplicability is
the silent-no-op class the engine forecloses. Keyword-value well-formedness rejects at
compose the same way: a non-numeric or non-finite bound, a non-compiling `pattern`, an
empty `enum` — [ContractViolation](#contractviolation) at compose; engine-read values
never defer their defects to dispatch.

**Enum-on-`Literal` coherence — the enum values MUST be a subset of the Literal's
members.** The `enum` keyword applies to any declared type; where that type is a
`Literal`, the enum's value set MUST be a subset of the Literal's members. The
engine-side model enforces the type and the enum together, so their intersection is the
field's accepted value space — an enum value the Literal forecloses can never pass, and a
fully disjoint enum admits nothing (every dispatch of the field would fail). Such a
contradiction is knowable at compose, so it raises [ContractViolation](#contractviolation)
at compose (R-handler-012, the keyword-coherence arm) rather than deferring to a per-dispatch
[SchemaValidationError](#schemavalidationerror) — the same fail-loud-at-compose posture as
the inapplicability deviation above. The subset requirement also preserves the literal-equal
seal (R-handler-005) where the field renders to a [trainable](#trainable) output wire: the
seal requires the submitted wire constraint and the engine-side model to enforce one
predicate, and a Literal-versus-enum disagreement would split that into two — subset
coherence removes the disagreement (the enforced intersection equals the enum), so the
wire and the model enforce the identical value set by construction.

**Enum-vs-length-bound coherence — every enum member MUST satisfy a co-declared length
bound.** Where a field co-declares the `enum` keyword and a length bound (`minLength` /
`maxLength`), every enum member MUST satisfy the bound. The engine-side model enforces
enum ∩ bound as the field's accepted value space, so a member the bound forecloses can
never pass; and where the field renders to a [trainable](#trainable) output wire whose
accepted matrix carries both keywords, a member-versus-bound disagreement would split the
literal-equal seal (R-handler-005) into two predicates — the submitted wire constraint
and the engine-side model would enforce different value sets. The contradiction is
knowable at compose, so it raises [ContractViolation](#contractviolation) at compose
(R-handler-012) rather than deferring to a per-dispatch
[SchemaValidationError](#schemavalidationerror) — the same fail-loud-at-compose posture
as Enum-on-`Literal` coherence above.

**Enum-vs-field-type coherence — every enum member MUST be admissible under the field's
declared type.** Independent of any co-declared keyword, an `enum` member the field's
declared type can never admit is a composition defect: the engine-side model enforces type
∩ enum as the field's accepted value space, so a foreclosed member can never be the field's
value — a dead member — and a fully type-disjoint enum admits nothing (every dispatch of
the field would fail). Admissibility follows the same value-equality the
membership verdict carries (numeric `1` and `1.0` are one JSON number, a boolean is its own
JSON type, `None` is never an admitted member): a `str` field admits string members; a
numeric field admits across the int/float family (an integral `float` on an `int` field, an
`int` on a `float` field); a `bool` field admits only booleans; and composite types
(`Optional`, `list` / `dict` / `tuple`, nested objects) recurse. Where the field is a
`Literal`, this arm defers to Enum-on-`Literal` coherence above — that arm's exact-type
subset is deliberately stricter (a `Literal` renders its members verbatim to a wire, so
lexical-equality subset keeps the written enum identical to the enforced intersection). The
contradiction is knowable at compose, so it raises
[ContractViolation](#contractviolation) at compose (R-handler-012) rather than deferring to
a per-dispatch [SchemaValidationError](#schemavalidationerror) — the same
fail-loud-at-compose posture as the coherence checks above, and (on a
[trainable](#trainable) output wire that renders the enum alternation) the same
literal-equal-seal (R-handler-005) preservation.

**Nullable fields.** A constraint applies to the present, non-null value: an admitted
`None` on a nullable field (the `"<T> | None"` type union / the `nullable` shorthand)
passes the constraint layer untouched — nullability is the type token's axis, never a
constraint's (the standard's and Pydantic's shared posture). The rule covers
registered third-party validators too: same layer, same rule.

**Validator names must be namespaced — shadowing is structurally impossible.** A
`conjured.validators` registration's name MUST carry a namespace (a dot); a bare registered
name fails loud at first resolution — it would be indistinguishable from a standard keyword.
Because the standard vocabulary is bare and every third-party name is dotted, the two key-spaces
are **disjoint by construction**: a registration can never shadow a standard keyword, and a
standard keyword can never name a third-party validator, so there is no shadowing case left to
detect.

**Parameters are data only.** A namespaced validator's parameter table holds
scalar/collection values, never a callable or expression. A field's validation keywords — bare
standard constraints and namespaced validators alike — fold into the
[pipeline-hash](#pipeline-hash) as the field's validation configuration, in authored order: they
constrain the accepted value space, which is structural, so a keyword change, a parameter
change, or a reorder is a composition change. A namespaced key that does not resolve against the
`conjured.validators` registry raises [ContractViolation](#contractviolation) at compose (per
R-handler-012).

**The validator contract.** A third-party validator is a **bare kwarg-only pure
function** — `def in_range(*, value, min, max): ...` — resolved at compose through
the sibling mechanism (the vector-2 function-shape check and the
R-handler-pure-module source audit apply unchanged). The **engine** binds the
declared parameters at compose — engine-owned partial application, the same
construction the trainable dispatch uses; authors never write factories or closures
(the vector-1 posture holds) — and wraps the bound validator into the field's
generated Pydantic model. The signature MUST be kwarg-only with parameters exactly
`{value}` ∪ the entry's declared parameter names; any mismatch is a compose-time
[ContractViolation](#contractviolation) (the same signature-union discipline as
R-handler-001). R-handler-012 owns this contract.

**The verdict.** A validator returns `None` (the value passes) or a one-line failure
string: the engine raises [SchemaValidationError](#schemavalidationerror) with
`constraint_violated` = the validator's qualified name and the returned string as
the per-field `message`. A validator that **raises** — any exception — is reporting
its own failure, not a verdict: the engine surfaces it as
[PipelineFailure](#pipelinefailure) with the underlying `cause_class`, never as a
validation result. Validator bodies MUST be pure and deterministic per
R-handler-013.

---

{#compose-time-work-homes}
## Compose-time work homes

When an author has compose-time work to do (data assembly, configuration
parameterization, stateful artifact construction), the engine offers **three
architectural homes** for it — and a separate-axis affordance that lets the author skip
writing a handler at all. The handler layer admits no fourth home.

(compose-time-work-homes-homes)=

**Three architectural homes for compose-time work:**

1. **A binding** (`bindings.<name>`). Data artifacts loaded at compose; delivered to the
   handler as a fresh per-dispatch copy at each dispatch — or, for large static
   read-only data, deep-frozen once and shared as a [reference binding](#reference-bindings).
   Configuration data, prompt templates, mapping tables, structured fixtures, and
   compiler-produced artifacts (via the
   [`compile = "..."` directive sub-form](#the-compile-directive-sub-form)) all live in
   the binding home.
2. **A service-type adapter.** Stateful artifacts with lifecycle: DB connections,
   loaded model weights, backend SDK clients. Its construction lifecycle is owned by
   [§ The service-type adapter](#the-service-type-adapter). Instance-state
   cache permitted; class-level + module-level state forbidden under the
   [R-handler-pure-module](#handler-derived-rules) scope extension (the
   [trust-model](#trust-model-vector) vector-7 seal).
3. **The handler body, per dispatch.** Cheap compute or runtime-derived work executed
   per call. Bare-function kinds carry author bodies; the trainable composition kind has no
   body (per [R-handler-010](#handler-derived-rules)), so this third home is unavailable to
   trainable composition nodes — compose-time work for the trainable composition kind
   lives at home 1 (`trainable.config` and `trainable.service_bindings`) or home 2 (the
   bound adapter).

**Separate axis — don't write a handler at all.** The native library lets authors
compose pre-built engine-shipped handlers (the trainable-backend adapters, emission
hooks, the blob-reference emitter; the native-library reference owns the
realized members) instead of writing a custom handler. This is not a "home for compose-time work" — it's
a different question ("compose vs author"). The three-homes taxonomy applies when an
author IS writing a custom handler; the native-library option applies when the work
fits an engine-shipped handler's shape.

**Forbidden:** an "engine-blessed compose-hook" — a fourth home where author code runs
at compose time outside the binding / adapter / body structure. Forbidden by policy per
the [trust-model](#trust-model-vector) vector-6 seal
(engine-blessed compose-hooks are an escape hatch for hidden author state at compose
time). The three homes + native-library axis are exhaustive for compose-time authoring
work.

---

{#when-not-to-use-content-bindings}
## When NOT to use content bindings

Compose-time bindings (`bindings.<name>`) supplied by external declaration file path are
for **fixed configuration** — values that do not vary across dispatches of a composed
pipeline. The canonical use cases are per-game-mode bindings (a "tavern_dialogue" mode
binding a specific NPC + scene + tone set; a "combat" mode binding a different set),
per-scene bindings (a scene's narrator instructions, NPC roster, environment
description), per-deployment-cohort bindings (an A/B variant of prompt scaffolds). These
are values fixed at composition time and reused across every dispatch. The same external
declaration file MAY be referenced by more than one handler's `bindings` — the engine
resolves it once and shares the resolved configuration across them (a `battle.toml` bound
by several combat handlers resolves once).

**Runtime ID-lookup is NOT a content-binding use case.** If the handler needs to look up
a value at dispatch based on a runtime ID (e.g., "load the NPC declaration named in
`npc_id`, where `npc_id` is a `reads` channel value from upstream"), that work belongs
in a **service handler** — not a binding. The service handler's external call is the ID
lookup; the bound service-type's adapter mediates the lookup against a database,
filesystem, or REST endpoint; the response is returned as the service's declared
[output ports](#output-port) and routed onto channels by the node's [write-map](#write-map).

The two use cases split cleanly:

- **Per-game-mode fixed binding**: a transform with `bindings.npc` whose value is
  supplied by external declaration file path at composition time. The same NPC is the
  handler's reference data across every dispatch of the composed pipeline. Different
  game modes are different compositions; each composition's pipeline-hash reflects the
  bound NPC.
- **Runtime NPC lookup by ID**: a service handler with `reads.npc_id` and
  `service_bindings.npc_store`; the body calls `services.npc_store.invoke(npc_id=npc_id)`;
  the response is the NPC data. The service-type adapter mediates the lookup. Every
  dispatch resolves the ID at runtime.

The two use cases differ in *when* the NPC is chosen: at compose time (binding) vs at
dispatch time (service lookup). If the choice happens per dispatch based on graph state,
the handler is a service.

---

{#reference-bindings}
## Reference bindings — large static read-only data

An ordinary [compose-time binding](#compose-time-binding) is
copied per dispatch — the
[trust-model](#vector-4-mutable-kwargs) vector-4 seal —
so a handler may freely mutate its kwargs without leaking state into a later dispatch.
That default is wrong for one shape of binding: **large, static, read-only data.** A
multi-megabyte structure read on every dispatch — an NPC worldbook, an
alias→canonical-character table, an in-process retrieval index — would be deep-copied on
every dispatch under the default, paying an O(size) copy for data that never changes. The
**reference binding** subtype is the opt-in for that case.

**Decision rule.** Default → copy per dispatch (small, and/or possibly mutated by the
handler). Reference subtype → deep-freeze once and share (large AND read-only). The
author opts in; the engine's one-time deep freeze is the structural guarantee that makes
sharing safe — read-only is *enforced*, not merely asked for.

**Field-named precedent.** A reference binding is a **broadcast variable** — the pattern
of handing each node one large read-only value once and sharing it, rather than shipping
a copy with every task.

{#reference-binding-marker}
### Marker — `delivery = "reference"`

A `bindings.<name>` entry opts in with the `delivery` selector:

```toml
[bindings.lookup_table]
delivery = "reference"          # deep-frozen once, shared; not copied per dispatch
table = { type = "dict[str, str]" }   # alias -> canonical character name
```

`delivery` is an optional binding-delivery selector: absent, a binding is delivered by
the per-dispatch-copy default (equivalently `delivery = "copy"`); `reference` is the one
alternative. A reference binding declares its schema and supplies its value inline or by
external declaration file (the `{ file = "..." }` form) exactly like any other binding —
only the delivery changes.

{#reference-binding-mechanism}
### Mechanism — deep-freeze once, share

At compose the engine **recursively (deeply) freezes** the resolved value once and stores
the single frozen instance on the composed node. Every dispatch and every reader receives
that same instance — there is no per-dispatch copy. The cost profile inverts the
default's: **O(size) once at load, O(0) per dispatch.**

The deep freeze uses recursive standard-library immutables — a `dict` becomes a
`MappingProxyType` over recursively-frozen values, a `list` / `set` becomes a `tuple` /
`frozenset`, and scalars are already immutable. This keeps the engine dependency-free and
the frozen forms as close to the builtins as Python allows. (Standard-library immutables
are the deliberate choice over a persistent-structure library such as `pyrsistent` or
`immutables`: those optimize cheap immutable *updates*, which a read-only reference never
performs, and they would add a runtime dependency plus a type further from `dict` / `list`
than `MappingProxyType` is.)

Sharing is safe because the freeze is **deep.** A shallow seal is assignment-only — a
handler climbs straight past it into a nested mutable — but a recursive freeze leaves no
mutable interior, so a single shared instance cannot carry state from one dispatch into
the next. And unlike the copy default, the reference seal is fail-**loud**: a write to an
immutable type raises. That is correct here — mutating reference data is always a bug, and
because the subtype is opt-in for read-only data, no legitimate local-mutation use is
wrongly blocked.

{#reference-binding-worldbook}
### Worked example — an NPC worldbook

A context-assembly transform reads a worldbook on every dialogue turn to inject relevant
lore. The worldbook is megabytes, fixed at compose, and purely read:

```toml
[transform]

[reads]
player_input = { type = "str" }

[output_schema]
assembled_context = { type = "str" }

[bindings.worldbook]
delivery = "reference"
facts = { type = "dict[str, str]" }
characters = { type = "dict[str, str]" }
locations = { type = "dict[str, str]" }

# Pipeline TOML — value by external declaration file (megabytes, fixed at compose):
#   bindings = { worldbook = { file = "lore/eldoria_worldbook.toml" } }
```

Under the per-dispatch-copy default the engine would deep-copy the whole worldbook every
turn; as a reference binding it is deep-frozen once at load and read on every turn at no
copy cost. The same fit covers a large entity/alias→canonical-character table or an
in-process static embedding index for memory retrieval.

{#reference-binding-caveat}
### Caveat — the handler sees immutable types

Because the value is deep-frozen, the handler sees a `MappingProxyType` where it declared
a `dict` and a `tuple` where it declared a `list`. This is transparent for the actual use
— key lookup (`x[k]`), membership (`k in x`), and iteration all work unchanged — but it is
a sharp edge for code that asserts the concrete builtin: `isinstance(x, dict)` is `False`
against a `MappingProxyType`, and `json.dumps(x)` rejects it. The edge is bounded by the
opt-in: a binding is a reference binding only because its author chose an immutable
read-only structure, so the author owns the small adaptation (read via the mapping
protocol, not `isinstance` / `json.dumps` on the reference).

---

{#channel-type-discipline}
## Channel-type discipline

Conjured's compose-time Pydantic IR admits **any Pydantic-expressible channel type,
including `bytes`**. The engine does not propagate any one dialect's expressiveness
boundary as engine policy — TOML's lack of a `bytes` primitive is a TOML-dialect
limitation, not an engine constraint.

(channel-type-discipline-reference-convention)=

For **binary content in training-aware pipelines**, the canonical authoring default is
**path / hash references** (documented best practice, not [mechanically-enforced](#mechanically-enforced-mode) grammar): a
channel typed `str` carrying a reference to the binary blob (a filesystem path, a
content-addressed hash, an S3 key), optionally paired with a `<name>_hash: str` sibling
channel for content-addressing.
Studio renders the reference via the native library's blob-reference emitter hook
(`conjured.lib.blob_reference_emitter.emit`). The rationale is author-quality:

- **Record weight.** A captured training record carrying inline `bytes` for audio /
  image / video payloads inflates the corpus by orders of magnitude vs a reference +
  content-addressed blob store.
- **Hash cost.** Content-addressed references let downstream consumers detect
  blob-content drift cheaply; inline `bytes` requires full-content hashing per record.
- **Storage shape.** Reference-typed channels separate the structured-pipeline payload
  (suitable for training-corpus indexes) from the binary payload (suitable for
  blob-store layouts).

**Scratch state for binary intermediates.** When a handler needs a non-channel binary
intermediate (e.g., a preprocessing pipeline's audio buffer that downstream consumers
don't need to see), the canonical home is a
[service-type adapter](#the-service-type-adapter) carrying the intermediate as instance
state. Channel state is the IR; adapter-scratch is for engine-managed
compose-time-scoped scratch that doesn't flow through the channel graph.

**Cross-dialect portability.** Pipelines using LCD-typed channels (the closed
vocabulary in § Types allowed in `reads` and `output_schema`) are portable across all
dialects with hash equivalence (per
[hash-model § Cross-dialect portability](#cross-dialect-portability)).
Pipelines using dialect-specific types (e.g., `bytes` in direct-Pydantic, which TOML
can't express) are portable to dialects that express them — a documented capability
boundary, NOT a weakened promise.

---

{#the-service-type-adapter}
## The service-type adapter

For service-kind and backend-SDK-emission-hook handlers, the `services.<name>.invoke(...)`
call inside the body does not reach the backend directly. It reaches the bound
service-type's **adapter** — the engine's per-service-type wrapper around the backend
call. For trainable composition node dispatches, the adapter is the same engine-side
wrapper, but the engine calls `adapter.invoke(...)` directly (no author body — see
[R-handler-010](#handler-derived-rules)).

**The body-side call convention.**

(the-service-type-adapter-body-side-call-convention)=

The handler body passes the backend call's **domain
kwargs directly** — `services.<name>.invoke(text=query_text, model=config["model_name"])`
— one keyword per value the call carries, and nothing else. The engine packs those domain
kwargs into the adapter's `input_payload` and supplies the rest of the adapter's closed
dispatch-kwargs (`service_name`, the `caller_*` fields, the config kwargs, and
`**transport_extra`) itself — the service-type reference's § Closed dispatch-kwargs owns
that adapter-side surface. So the body **never** passes `input_payload=` (that would nest
the domain kwargs one level too deep) and never passes the engine-supplied kwargs: the
body-side surface is only the domain kwargs.

This section owns the adapter seam. The service-type's own author surface — the TOML declaring
its `identity_schema` / `transport_schema` / `config_schema`, the service-impl dispatch contract
its implementation satisfies, and the binding-handle → backend wiring model — is documented in
the service-type component reference.

The adapter's construction lifecycle below is the single source for both service bindings and
backend-SDK-emission hooks — they dispatch through this same adapter, differing only in their
transport source:

(the-service-type-adapter-construction-lifecycle)=

The engine constructs **one adapter instance per composition, at compose time** — the
per-composition lifetime that bounds the instance-state caching the trust model permits. The
constructor receives only the **compose-fixed identity** for its binding (the identity values the
pipeline supplied — e.g. `model`, `prompt_template`), so the instance is configured for the one
backend it represents; everything dynamic arrives per dispatch through `invoke()`, never the
constructor. For a **trainable** binding the constructor additionally receives two
engine-supplied compose-fixed inputs: the declared `trainable.output_schema` port shape — the
literal artifact the adapter maps to the backend's decode constraint (R-handler-005) — and the
composition declaration's path (the locus a compose-time constraint rejection cites). The
constraint derivation runs at construction, which is what makes the grammar-expressibility
caveat a compose-time rejection rather than a dispatch-time surprise; nothing dynamic enters
either way. An **authenticated backend client** — which needs the per-deployment transport
(endpoint, credentials), supplied by the deployment's `transport.<name>` block for the binding —
a service handler's and a backend-SDK-emission hook's alike — is therefore built on the
**first** `invoke()`, when that transport first arrives, and memoized as instance state; it is never
constructed at compose time (the constructor has no transport to build it with).

The adapter is the structural seam between author code (for bare-function kinds) or
engine-controlled state (for the trainable composition kind) and the backend SDK. Its
responsibilities and what it owns vs what each kind owns are deliberate:

- **The adapter owns backend-mechanics translation — translation, never a verdict.**
  Serializing the function's invocation arguments to the backend's protocol; submitting
  the declared output-port shape (for the trainable composition kind, the
  `trainable.output_schema`) to the backend's structured-output / constrained-decoding
  API; parsing the wire response into the typed result; returning that parsed result
  back **verbatim**. The adapter never validates, coerces, repairs, or retries an
  emission — the engine's output-port validation downstream of the adapter's return
  (the runner's R-handler-001 output-validation surface; for the trainable composition
  kind, [R-handler-005](#handler-derived-rules)'s verdict) is the only verdict layer.
  The adapter module
  itself is AST-audited at compose for the vector-7 seal (no above-instance-scope
  mutable state per [R-handler-pure-module](#handler-derived-rules) scope extension; see the
  [trust-model](#trust-model-vector) vector inventory).
- **For service kind, the handler body owns intent.** Which [input ports](#input-port) it declares
  (`reads`); which [output ports](#output-port) it writes (`output_schema`); what prompt
  content and declared-binding values to assemble into the backend invocation; what
  computation to perform on the typed response before returning the dict the runner
  routes onto channels via the node's [write-map](#write-map).
- **For the trainable composition kind, the engine constructs the dispatch.** The
  trainable composition's `trainable.config` and `trainable.service_bindings`
  partial-apply into the dispatch wrapper at compose time; `trainable.reads` populates
  at dispatch; the engine routes the adapter's response onto the trainable's declared
  [output ports](#output-port). There is no author body between the adapter boundary and the channel-write.
- **The handler body cannot reach inside the adapter** (bare-function kinds). Backend-SDK
  imports inside handler bodies are forbidden per [R-handler-007](#handler-derived-rules); the
  `services` kwarg is the only handler-side surface for service invocation, and the
  surface lands at the adapter. The adapter's internals — the serialization step, the
  constraint-submission step, the deserialization step — are engine-implementation
  territory the handler body has no access path to.

**Reaching for a parse-and-retry on an empty or malformed reply?** When a backend returns
an empty, truncated, or structurally-invalid emission, the engine-aligned shape is **fail
loud**: the reply flows back verbatim and the output-port validation downstream of the
adapter return ([R-handler-001](#handler-derived-rules)'s output validation;
[R-handler-005](#handler-derived-rules)'s literal-equal verdict for a trainable composition
node) raises against it, surfacing as a [PipelineFailure](#pipelinefailure) the consumer
dispatches on. Re-running the call on a bad reply is **consumer multi-pipeline
orchestration** — its own pipeline run with its own captured record. The adapter does
**not** inspect the response to decide a retry — by the trigger axis the
[no-engine-retry surface](#no-engine-retry-payload-predicate) owns, a re-call driven by a
verdict on the reply is semantic retry, which the adapter has no surface to perform.

The seam is load-bearing for three properties this reference depends on:

1. **The literal-equal rule** ([R-handler-005](#handler-derived-rules)). The adapter is where
   the trainable composition node's declared `trainable.output_schema` becomes the
   backend's structured-output / constrained-decoding constraint. The rule's claim that
   the schema is "literally the same artifact" rests on the adapter submitting the
   declared shape directly — no separately-authored backend-constraint, no derivation
   transform between the runtime schema and the constraint API. Different backends carry
   different structured-output APIs (OpenAI structured outputs, Anthropic tool use,
   Together AI JSON mode, llama-server grammar, etc.) — the per-service-type adapter is
   where backend-mapping divergence is absorbed; the handler-declared shape is the single
   source.

2. **The canonical-event log spec.** The adapter boundary is the capture point:
   for **service-kind** dispatches the `service_invocation` event is constructed
   here, before control returns to the handler body (the body has no path to
   influence or mutate either captured side); for **trainable composition node**
   dispatches the engine-controlled `adapter.invoke` call is what the training
   pair's `handler_enter` / `handler_exit` events bracket — no
   `service_invocation` fires. The per-kind capture model and the payload spec
   are owned at
   [hash-model § canonical event types](#canonical-event-types)
   and
   [hash-model § Adapter-boundary capture](#adapter-boundary-capture-mechanism);
   this reference covers only the seam's role in the dispatch path.

3. **The "exactly one external call" profile**
   ([handler-kinds § comparison](#comparison)). The
   adapter IS the external-call seam from each kind's perspective. For service kind: one
   `services.<name>.invoke(...)` site in the handler body, mediated by exactly one
   adapter. For the trainable composition kind: one engine-constructed
   `adapter.invoke(...)` call per dispatch. The per-dispatch correspondence between
   channel-writes and captured canonical events
   ([channel-record correspondence](#channel-record-correspondence))
   is preserved at the runtime-contract layer by the exactly-one-external-call rule and
   at the provenance-capture layer by adapter-boundary capture (service kind) or by the
   engine-controlled dispatch (trainable composition kind). R-handler-008's expansion
   ("exactly one service-typed binding per service handler / trainable composition node")
   enforces the rule at the binding declaration; the adapter enforces it at the dispatch
   boundary.

{#thin-service-pattern}
### Thin-service pattern — blessed, optional

A service handler MAY return its backend's typed response **faithfully** — passing the adapter's
parsed result straight to its `output_schema` write — and move any reshaping (renaming,
restructuring, deriving fields) into a **downstream transform** node. This is a blessed authoring
pattern, not a requirement.

The payoff is at the [R-handler-002](#handler-derived-rules) review seam. When a service reshapes
its backend response in-body, the captured `service_invocation` and the `handler_exit` write
diverge in *shape* on every honest dispatch — reshaping is the common case — so shape-divergence is
uninformative, and the only review-actionable signal is the narrower **masking signature** (a
schema-valid write standing in for a backend response that indicates failure or absence). A thin
service that returns faithfully collapses the two: any divergence between the captured response and
the write *is* masking, the review seam sharpens to a clean wire signal, and replay/provenance
improve because the captured response and the channel-write coincide. The cost is one extra node per
reshape.

Reshaping in-body remains valid — a service that restructures a successful response violates no
rule. The thin-service pattern is the recommendation for authors who want the strongest provenance
and the cleanest divergence seam; it is not enforced and carries no audit.

{#trainable-backends}
### Trainable backends — the I4 seal and the compose-time gate

A **trainable backend** is the bound implementation a [trainable](#trainable) composition
node's one `trainable.service_bindings` entry resolves to (per
[R-handler-008](#handler-derived-rules)). Not every service-type adapter qualifies: the
trainable composition kind exists to derive a training corpus that fine-tunes the bound
model (I4 — the pipeline IS the training contract), so a trainable backend MUST satisfy four
properties the compose-time gate depends on.

1. **Server-side decode-time seal.** The backend constrains its output at decode time to the
   declared `trainable.output_schema` — a grammar / structured-output / guided-decoding
   constraint the serving runtime enforces token-by-token, so the emission conforms by
   construction (per [R-handler-005](#handler-derived-rules), the literal-equal rule). The
   seal lives at the **server**, never at a client-side parse-and-retry wrapper — a wrapper
   that can emit a non-conforming value and throw breaks the schema-IS-the-constraint identity.
2. **Fine-tunable open weights the consumer owns.** The corpus the pipeline derives must be
   trainable into a model the consumer serves themselves. A frozen hosted endpoint whose
   weights the consumer cannot fine-tune or self-host is a [service-kind](#the-service-kind)
   backend, not a trainable backend.
3. **A standard training-artifact contract.** The fine-tuned model lands in a portable
   artifact family the serving adapter consumes and the consumer can self-host. The adapter
   declares that family as its `training_artifact_contract` — a **provenance label** (any
   non-empty string): the engine records the label and reads the trained artifact **by
   path**, never interpreting the value, so the label set is open, not closed. Each
   reference serving adapter declares its own family label; a consumer-supplied backend
   names its own.
4. **A clean read/write seal — no service-side pre/post-processing.** A trainable node's
   `trainable.reads` → backend → `trainable.output_schema` is a clean seal: the adapter
   submits the reads and routes the constrained emission, and does nothing else. Any shaping
   of the input or the output lives in an upstream [preprocessor](#preprocessor) or a
   downstream transform — never inside the bound service. (This is the same discipline that
   keeps a service handler's `service_invocation` faithful for divergence detection.)

The properties are **behavioral — none names a wire technology.** Each backend
family declares its own realization of property 1: the JSON wire forms realize the
decode-time seal as **constraint submission** (a schema or grammar the serving runtime
enforces token-by-token); an in-process regression-model family realizes it
**structurally, by its output type** (the artifact can only emit values of the declared
shape); a non-structured family (a voice model, say) declares its own. "Submit a
JSON Schema" is one family's realization, never the contract's definition — which is
what keeps non-LLM trainable families additive, with no reframing of the
properties.

**The gate.** The engine ships **native trainable-backend adapters** that satisfy this
contract by construction; a small native set covers the common serving wire forms,
reaching the bulk of real trainable-and-owned serving (e.g. an OpenAI-compatible
structured-output endpoint). A trainable composition node
binding a native trainable-backend adapter passes the gate by construction. A
**consumer-supplied** trainable backend — for the narrow tail the native set does not cover
(an Apple-MLX runtime, a direct constrained-decoding-library binding) — is admitted via the
**trainable-backend audit-stamp**: the engine-shipped adversarial-review prompt an agent or
maintainer runs against the candidate adapter to certify the properties above, exactly
as custom handlers are reviewed — an instance of the general
[audit-stamp mechanism](#audit-stamps), whose result is the adapter module's sibling stamp.
The compose-time gate admits a binding whose resolved adapter is
**native-by-construction** (resolved through the engine's own native adapter table, per
[handler resolution](#architecture-handler-resolution)) or a **consumer-supplied** adapter
whose module carries a **fresh pass-grade audit stamp** — certification is structural on
both arms, never a self-declared marker. The native arm holds by construction at
resolution; the audit-stamp arm is the general [audit-stamp mechanism](#audit-stamps)
applied to the adapter module, so its compose-time consequence follows that mechanism's
**enforcement-gated** freshness semantics (the deployment's `audit_enforcement` opt-in — an
unstamped or stale consumer adapter refuses compose only under enforcement; the
[audit-stamp mechanism](#audit-stamps) owns the gating). Separately and always, the gate
verifies the adapter's two immutable property attributes against the resolved class,
raising [ContractViolation](#contractviolation) if either is absent or malformed.
The adapter **declares** its contract on its class as two immutable class
attributes (admissible under the vector-7 seal — instance state yes, class *mutable*
state no; an immutable marker is neither):
`training_artifact_contract`, a non-empty provenance string naming the trained-artifact
family (property 3) — the engine records it but does not interpret the value, so any
non-empty string is admitted (each reference adapter declares its own);
and `reserved_wire_keys`,
the frozen set of wire keys the adapter family writes (compose's extras-disjointness
check reads it so an author's `extras` cannot override an engine-written key). The gate
verifies both against the resolved class at compose, rejecting if either is absent or
malformed. The audit-stamp prompt ships with the engine's conformance surface
(`conjured.conformance`, reached via `importlib.resources` — the shipped audit prompts
and the native members' findings reports; each native member's stamp is a sibling
`<module>.audit.toml` beside the module itself, per the
[audit-stamp mechanism](#audit-stamps), which owns the stamp shape and the freshness check).

**One compose-time caveat — the accepted matrix.** Property 1 holds only for the value
predicates the backend's grammar can express, and the boundary is **per wire family, not
global**. Each wire family declares its own **accepted-keyword set** — a per-wire-family
property the adapter applies at constraint derivation (per [R-handler-005](#handler-derived-rules),
the trainable-constraint-unsupported check), distinct from the two certification attributes
the gate verifies: a constraint keyword **in** the bound family's set RENDERS into the submitted
wire constraint — the seal stays literal-equal *including* the keyword, because the engine-side
model and the submitted constraint then enforce the same predicate — while a constraint keyword
**out of** the set is REJECTED at compose, naming the keyword and the wire: an honest failure,
not a silent best-effort. Widening a family's accepted set later is an ordinary per-family edit.
The accepted-set VALUES live with the member families — the native-library reference's
per-member entries own which keywords each wire renders — and are not restated here.

(trainable-wire-rejected-class)=

For the JSON wire forms the native adapters speak, the seal-expressibility rejected class
concretely includes: a **constraint keyword outside the bound wire family's accepted set** — a
value predicate the grammar cannot enforce (a PCRE shorthand like `\d` / `\w` / `\s`, a
deep cross-field or numeric-range predicate), moved to a downstream transform reading the
literally-emitted channel; any **namespaced (dotted) validator key** — opaque third-party code,
never render-eligible, same downstream-transform remedy; a `bytes` channel — no JSON wire
rendering, binary rides path/hash references per the handler reference's § Channel-type
discipline; and a fixed-arity `tuple` channel — a JSON wire delivers arrays, which strict output
validation rejects against a declared tuple, so the seal cannot close end-to-end. The strict
structured-output wire form additionally rejects an open-keyed `dict[str, <T>]` level (the GBNF
wire expresses them).

{#test-substitution-twin-handlers-twin-declarations-compose-time-binding-swap}
### Test substitution — twin handlers / twin declarations, compose-time binding swap

Substituting a real backend with a fake for testing happens at **compose time** via the
pipeline declaration, not at runtime via function patching. The pattern differs slightly
by kind:

- **Service kind — twin handlers, one Python function.** Twin handlers preserve one
  source of truth for handler code; the twin's Python module is a one-line re-export
  shim:
  ```python
  # acme_dialog_test/handlers/detect_intent.py
  from acme_dialog.handlers.detect_intent import detect_intent
  ```
  Two registration handles point at the same bare function via separate
  `conjured.handlers` entry-points: `acme_dialog.detect_intent` (production) and
  `acme_dialog_test.detect_intent` (twin). The handler code lives once. What differs
  between production and twin is the **service-type binding** in the pipeline
  declaration, not the handler implementation:
  - Production pipeline: `service_bindings.llm` `type = "acme_dialog.structured_output"`.
  - Test pipeline: `service_bindings.llm` `type = "acme_dialog_fake.structured_output"`.
- **Trainable composition kind — twin composition declarations, no Python function.**
  The trainable composition kind has no author body per [R-handler-010](#handler-derived-rules);
  the trainable composition declaration IS the handler. Production and test ship as two
  trainable composition declarations differing in `trainable.service_bindings`:
  - Production trainable composition declaration: `trainable.service_bindings.llm`
    `type = "acme_dialog.qwen_trainable"`.
  - Test trainable composition declaration: `trainable.service_bindings.llm`
    `type = "acme_dialog_fake.qwen_trainable"`.
  Both bind trainable backends (per R-handler-008 expansion); the fake trainable
  backend's adapter returns canned responses while preserving the engine-constructed
  dispatch and the `handler_enter` / `handler_exit` training-capture path.

The composition validator sees both bindings at compose time. The pipeline-hash differs
between production and test composition by construction; for the trainable composition
kind, the [training-bundle-hash](#training-bundle-hash) on the
test trainable composition declaration also differs from production's (the
`trainable.service_bindings` qualified name is part of the composition declaration's
normalized hash). The fake backend's adapter validates `invoke(...)` arguments against
the service-type's declared input shape (catching handler-body assembly errors
structurally for service kind; catching engine-routed argument errors for the trainable
composition kind) and returns shape-matching output per the canned-response declaration.

**Why this pattern, not runtime patching.**

(test-substitution-runtime-patching-attests)=

`unittest.mock.patch`, monkey-patching,
dependency-injection swaps, or service-locator substitution all modify the running
program at dispatch time without changing the composition. The pipeline-hash sees the
production composition; the dispatch wrapper validates against the production schema; the
engine emits canonical events as if production code ran (per dispatch kind, the owned
event pairs — hash-model's
[§ Paired-event structure (service)](#paired-event-structure-service-kind) and
[§ (trainable composition)](#paired-event-structure-trainable-composition-kind)).
The training-corpus claim
("this composition produces this
training-record stream") becomes false in the test environment — the patched-in fake's
invocations would emit events into the training corpus under the production
pipeline-hash, violating
[I4 (pipeline-as-training-contract)](#invariants-and-derived-rules).
Twin handlers / twin composition declarations preserve I4 by moving substitution to
compose time where the composition validator and the pipeline-hash see it.

(test-substitution-sanctioned-site)=

**The adapter seam is the only sanctioned substitution site.** Compose-time twin
substitution at a declared service-type binding is the engine's one substitution
mechanism, and the adapter seam is its one site: a test composition swaps which
backend a binding resolves to, and changes nothing else.

A transform has no
substitution surface at all — it is called real (its body is pure computation
over its declared ports per R-handler-004, so there is nothing external behind
it to stand in for) — and no handler's internals are ever patched. Agents
trained on mainstream Python testing reach for `unittest.mock.patch` /
monkeypatching here by reflex; that pattern lands wrong in Conjured for the
reason the fragment above derives. Substitute by editing the test
composition's binding `type`; never by
patching.

The test-double library that builds on this mechanism — its verified fakes and twin
packages, the exclusion of fake packages from production deployments, the
propagation of load-bearing field descriptions into twins, and the verification
discipline that observes dispatch through the canonical event stream — is the
testing reference's territory.
Whether training records fire is determined by kind-based separation: the taxonomy
enforces it through the trainable composition kind, not through any property of the
service-type declaration.

---

{#conjured-core-surface}
## The `conjured.core` import surface

`conjured.core` is the engine's **one handler-facing exposure surface** — the namespace
[R-handler-007](#R-handler-007)'s allowlist names for the engine-declared types and pure
utilities a handler body may import. Its contract:

- **The module's export surface IS the definition.** An engine type or utility is available
  to handler bodies iff `conjured.core` exports it; canon enumerates no separate roster, and
  an import of a name the surface does not export fails loud at handler-module import time.
- **Purity bound.** Everything it exports is a declared type or a pure utility — nothing
  I/O-bearing, nothing stateful, nothing that opens a path to external resources beside the
  [`services` kwarg](#services-kwarg).
- **Reserved from day one; populated as engine-exposed types stabilize.** The namespace ships
  with the engine even while empty — it exists so the import rule has a concrete home before
  any handler-facing type is exposed. An empty surface means no engine type is
  handler-importable yet; the surface grows only by deliberate engine decision, never by a
  handler's need reaching into engine internals.

---

{#handler-derived-rules}
## Derived rules

(derived-rules-convention-kernel)=

Every derived rule that governs this component lives here. The rules cite the invariant(s) or
tenet(s) they protect from [principles](#invariants-and-derived-rules) via
`derived_from`; they declare an `enforcement` mode per
[enforcement-modes](#architecture-enforcement-modes).

```yaml
rules:
  - rule_id: R-handler-001
    name: engine-constructed dispatch wrapper
    derived_from: [I1, I2, I3]
    enforcement: mechanical
    statement: |
      Handlers are dispatched through an engine-constructed wrapper,
      not called directly. As bare-function kinds, transform / service / hook
      handlers ship as bare kwarg-only Python functions.
      The engine constructs the dispatch wrapper at compose time, supplying the handler a fresh
      per-dispatch copy of each resolved `bindings.<name>` value at each dispatch rather than
      partial-applying a shared object (the vector-4 copy seal).
      The trainable composition kind has no author body per
      `R-handler-010`; the engine constructs the dispatch directly
      against the bound trainable backend (the construction is fixed in
      `R-handler-010`'s no-author-body fragment). The function-shape check at
      handler resolution rejects all non-bare-function shapes per
      `R-handler-bare-function` (the vector-2 seal in the trust-model
      vector inventory).

      The engine's compose-time path (uniform across the three Pattern
      B kinds; specialized for the trainable composition kind)
      generates a Pydantic model per declared `reads` and `output_schema`
      (or omits the output model for hooks, which return `None` by
      contract), resolves and validates each declared `bindings.<name>`
      value, and returns a dispatch callable that supplies the handler a
      fresh per-dispatch copy of each binding value (alongside the
      input ports projected from the channels their read-map wires them
      to) and performs
      input-validation → handler-call → output-validation on every
      invocation. The engine-constructed dispatch is the **sole
      admission gate to the graph**: no handler enters the dispatch
      path without passing through the engine's compose-time
      construction. Authors do not construct the dispatch callable.

      The author function's signature is introspected once at
      construction and MUST be kwarg-only with parameters equal to the
      union of the handler's declared input-port names,
      `bindings.<name>` declarations, (where a service-typed binding
      is declared in `service_bindings`) the reserved `services` kwarg,
      and (for a hook) the hook's declared `transport_schema` field
      names — deployment-supplied transport delivered to the emitting
      body as kwargs, per § `transport_schema`.
      Any mismatch — extra kwarg, missing kwarg, positional parameter,
      `**kwargs` collector, `*args` collector — raises ContractViolation
      at compose time, before the first pipeline runs. For the
      trainable composition kind, the engine-constructed dispatch has
      no author signature to check; the engine validates that no
      Python handler file is loaded for a trainable composition entry
      (per `R-handler-010`).

  - rule_id: R-handler-002
    name: no silent fallbacks
    derived_from: [I1, I4]
    enforcement: review
    scope: |
      Applies to handler kinds that carry an author body — transform,
      service, hook. The trainable composition kind has no author body
      per `R-handler-010`; silent-fallback as a failure class
      structurally cannot occur for trainable composition node
      dispatches.
    statement: |
      No silent fallbacks.
      A handler MUST NOT mask internal failure with a schema-valid value the engine cannot
      distinguish from a runtime-derived result. Production-resilience patterns — `except
      Exception: pass`, exception-to-default mapping, `value or "default"` coercion on a required
      read where `None` is meaningful signal, a hard-coded default returned when a derivation step
      fails, semantic retry that buries the prior failed attempt — corrupt the training projection because the captured channel-record claims the
      handler produced X for input Y when it actually failed.
      The runner cannot inspect handler bodies; adversarial review
      catches instances during library publishing.

      **Mechanical evidentiary backing (service kind).** The
      service-type adapter captures the `service_invocation` event from
      the backend's actual response, *upstream* of the handler body. A
      dishonest service-handler body cannot lie about both its return
      value and the captured event — the adapter-boundary capture is
      outside the handler-body trust surface. The paired-event
      structure — `service_invocation` at the adapter boundary paired
      with `handler_exit` after the body completes — enables
      consumer-side detection of the masking signature between captured
      invocation and handler return. The engine does not perform the
      analysis but commits to provenance sufficient for it.
      `R-handler-002` is **review-enforced with mechanical evidentiary backing** rather than
      review-only with no captured record: review grounds its judgment in the wire-visible **masking
      signature** — a captured `service_invocation` ↔ `handler_exit` pair where the captured backend
      response indicates failure or absence (an exception, an error payload, an empty result) yet the
      handler returned a schema-valid `writes_snapshot`. Mere reshaping of a successful backend response
      is not the signal: a faithful service that restructures a valid response diverges in shape without
      masking, so shape-divergence alone over-fires.
      See [hash-model § canonical
      event types](#canonical-event-types).

      **Trainable composition kind structural immunity.** Trainable
      composition node dispatches have no author body — the engine
      constructs the dispatch directly against the bound adapter. The
      silent-fallback failure mode requires a body that can choose to
      mask failure; the trainable composition kind has none, so the
      failure class structurally cannot occur. No `service_invocation`
      fires for trainable composition node dispatches; training capture
      is the engine-constructed `handler_enter` + `handler_exit` pair,
      both engine-constructed from declared channel projections without
      passing through an author body.

  - rule_id: R-handler-003
    name: closed-enum handler kinds
    derived_from: [I2, I3]
    enforcement: mechanical
    statement: |
      Handler kinds are a closed enumeration of three: transform,
      service, hook. Each kind has a fixed node role; the
      closed-enum kinds enumerate the role-and-constraints space
      exhaustively at the handler-kind layer per
      [handler-kinds](#architecture-handler-kinds), which fixes the
      per-kind node-role profile. Trainable lives at the composition
      layer as a composition-kind specialization, not at the
      handler-kind layer — see [trainable](#trainable)
      for that specialization. Future expansions go
      through the same evaluation: if the load-bearing property is a
      node role the existing handler kinds don't cover, an engine change
      adds a new handler kind; if the property is engine-owned dispatch
      or other composition-layer machinery, the right home is a new
      composition-kind specialization.

      The closed-enum claim is mechanically enforced through the
      corpus-scope entry-path constraint (see Corpus scope in
      principles): for bare-function kinds the engine accepts only
      registered handlers tagged by top-level kind discriminator
      (`transform`, `service`, or `hook`); for the trainable
      composition kind the discriminator is `meta.kind = "trainable"`
      on the composition TOML primitive. Both discriminator forms
      validate against their closed enums at compose time.

  - rule_id: R-handler-004
    name: transform purity
    derived_from: [I1, I2, I4]
    enforcement: review
    statement: |
      Transform handler bodies MUST be pure: no external runtime
      resource access (no HTTP, DB, filesystem, OS environment, or
      subprocess invocation); no non-deterministic operations (no
      clock reads, random-number generation, or other
      observation-of-external-state operations the runner cannot
      reproduce on replay). Transforms compute their declared
      `output_schema` [output ports](#output-port) purely from their declared `reads`
      [input ports](#input-port) and `bindings.<name>` values.

      Test: given identical reads and binding values, does the
      transform body return the same dict on every invocation?

      The runner cannot inspect transform bodies for these patterns;
      adversarial review catches violations at library publishing. A
      handler whose body needs external reach or non-deterministic
      observation belongs in a service handler (for non-trainable
      external calls); for training-capture backends, the right
      structural shape is a trainable composition node — see
      [trainable](#trainable).

      The mechanical half of transform purity — "transforms forbid service-typed bindings" — is
      structurally enforced by the kind-discipline absence of `service_bindings` on transforms (per
      `R-handler-006`) and by the engine's compose-time signature check (per `R-handler-001`, which
      rejects transform signatures carrying a `services` kwarg).
      `R-handler-pure-module` extends
      structural enforcement to handler module-level state (no
      module-level mutable state, no caching decorators, no
      module-level I/O at import). `R-handler-004` covers the
      review-only handler-body half.

  - rule_id: R-handler-005
    name: literal-equal rule
    derived_from: [I1, I4]
    enforcement: mechanical
    statement: |
      This rule governs an LLM-emission channel of a
      [trainable](#trainable) composition
      node — a [trainable channel](#trainable-channel)
      declared in `trainable.output_schema`.
      The engine submits the declared output-port shape as the backend's structured-output /
      constrained-decoding constraint when invoking the backend, and validates the backend's response
      against the same shape at return. The schema as runtime contract and the schema as backend
      constraint are **literally the same artifact** — no separate authoring step, no derivation
      transform between the two roles. A backend that ignores the constraint and returns a response
      that doesn't validate raises SchemaValidationError and halts.

      The rule is enforced across the
      [service-type adapter](#the-service-type-adapter) seam and the
      engine's output boundary, with exactly one verdict layer: the
      adapter submits the declared output-port shape to the backend on
      the call-out path, and on the return path returns the parsed
      emission **verbatim** — pure translation, never a verdict: the
      adapter does not validate, coerce, repair, or retry. The
      engine's output boundary validates the returned emission against
      the same declared output-port shape — the only judge, and the
      fail-loud backstop for the seams where a constrained decode can
      still diverge: a nominally-compatible server that accepts the
      constraint field but enforces it loosely or not at all;
      token-budget truncation mid-emission (a grammar-constrained
      decode cut off at `max_tokens` yields invalid JSON); a
      strict-mode refusal payload in place of the structured output; a
      misconfigured endpoint that silently ignores the constraint
      field. The boundary detects "the server did not actually honor
      the constraint" and halts before a malformed record enters the
      captured corpus. The engine then routes the
      validated, output-port-keyed response onto channels via the node's
      [write-map](#write-map) — strictly downstream of validation and of
      training capture (the `handler_exit` `writes_snapshot` is taken on
      the validated output-port projection, never between the declared
      output-port shape and the backend constraint). No author body
      interposes (per `R-handler-010`). Because the write-map is applied
      by the runner after the adapter returns, the backend constraint is
      the declared output-port shape and the channel name never enters
      it — the handler/trainable cannot see the channel it writes, so
      what the backend saw is structurally the port shape, captured as
      the corpus record shape.

      Load-bearing for I4: training-corpus records captured from a
      trainable composition node's `handler_exit` event (carrying
      `writes_snapshot` — the trainable composition node's
      [output-port](#output-port) projection, taken before the write-map)
      match the runtime-contract shape
      because the runtime contract IS the structured-output constraint.
      A separate "training-data schema" authored alongside cannot drift
      from the runtime schema because no separate schema exists. The
      per-channel framing is general — a multi-channel-trainable
      composition needs no framing shift, because every trainable
      channel on the same trainable composition declaration carries its
      own declared shape, its own structured-output constraint, and its
      own literal-equal claim; a single-channel-trainable composition is
      the case where the per-channel framing coincides on the wire with
      the per-trainable-composition hash boundary.

      **Note on the captured-record event.** The rule itself is
      unchanged: declared type == backend constraint == validated
      return. The captured record for a trainable composition node
      dispatch appears at `handler_exit` (carrying the validated
      response), NOT at `service_invocation` (which is service-kind
      only — trainable composition node dispatches emit no
      `service_invocation`). The literal-equal claim grounds on the
      engine's output-boundary validation of the adapter-returned
      emission — the single verdict layer above — regardless of which
      canonical event carries the captured record.

      Backend-submission mechanics are engine-implementation territory:
      each service-type adapter maps the declared output-port shape to the
      backend's structured-output API (OpenAI structured outputs,
      Anthropic tool use, Together AI JSON mode, llama-server grammar,
      etc.). What `R-handler-005` enforces is that the submitted
      constraint and the validated return ARE the declared output-port
      shape, not a transformed derivative authored elsewhere.

  - rule_id: R-handler-006
    name: closed handler-declaration shape grammar
    derived_from: [I1, I2, R-handler-003]
    enforcement: mechanical
    statement: |
      A handler declaration's shape is a closed enum per kind. Two
      grammar shapes are admitted:

      - **Bare-function handler declaration (transform / service / hook).**
        The top-level kind discriminator is exactly one of
        `transform`, `service`, `hook` (the kind tag the engine uses
        to route the entry to its kind-specific compose-time path).
        Applicable sub-declarations are the closed set declared in
        [handler-TOML grammar](#handler-toml-grammar) above: `reads`
        (the handler's [input ports](#input-port)),
        `output_schema` (the handler's [output ports](#output-port);
        transform / service), `bindings.<name>`
        (zero-or-more named bindings), `service_bindings`
        (service / hook), `transport_schema` (hook), `annotations`.
        The `bindings.<name>` entry MAY carry the `compile = "..."`
        directive, the `delivery = "reference"` selector (the
        [reference-binding](#reference-bindings) subtype), or a
        `default = ...` ship-time default (per
        [§ Ship-time defaults](#binding-ship-time-defaults)) as
        binding-declaration variants — all part of the closed grammar.
        At the pipeline-supply side the binding value takes any form
        [§ Binding value-supply grammar](#binding-value-supply-grammar)
        admits.
        Port-to-channel wiring (a node's read-map and write-map) is NOT
        part of this closed handler-TOML grammar: it is a node-entry
        addition in the pipeline-declaration grammar, declared per node,
        not on the handler declaration.
      - **Trainable composition declaration.** Uses the composition
        TOML primitive: `meta` (`kind = "trainable"`, `name`),
        `inputs`, `outputs`, a `[[preprocessors]]` sequence — zero or
        more node entries, each entry head carrying the same node-entry
        grammar the outer pipeline's `nodes` uses (`kind = "handler"`
        plus the qualified-ref `name` key; this id-labeled sequence
        admits handler entries only — § A composition mirrors the
        pipeline owns the boundary) plus a composition-local `id` — the one
        justified composition-layer addition to the shared entry
        grammar: a composition's nodes flatten into the embedding
        pipeline's namespace, so each entry carries a local label that
        qualifies post-flatten to `<meta.name>.<id>`, where the outer
        pipeline's nodes are top-level and have no enclosing namespace
        to qualify into. Each entry is a handler node in a position: its
        kind, ports, and binding declarations all resolve from the
        referenced handler declaration via its qualified `name` — the
        same resolution path an outer node uses — so it receives the
        identical binding treatment an outer-pipeline node receives: the
        `delivery` selector, ship-time `default`, and value-validation
        resolve from that referenced declaration, and all of it folds
        into the [training-bundle-hash](#training-bundle-hash) exactly as
        an outer node folds into the pipeline-hash (the
        [mirror-pipeline principle](#composition-mirrors-the-pipeline)). A
        hook preprocessor's `transport_schema` likewise resolves from the
        referenced hook handler, not the entry. It is NOT a distinct kind
        with a reduced binding surface; the engine MUST NOT synthesize its
        `delivery` / `default` / validation (e.g. forcing
        `delivery = copy`) or treat its declaration as inlined — that is
        the position-vs-kind error. The composition then carries its own
        `service_bindings.<name>`
        identity supply (one per service-typed binding the composition's nodes
        declare — mirroring the pipeline's `service_bindings.<name>`), exactly
        one terminal `trainable` node carrying the optional `streamable`
        delivery selector (placement rule owned by R-pipeline-001; hash
        exclusion owned by hash-model) and subsections `trainable.config` /
        `trainable.service_bindings` / `trainable.reads` /
        `trainable.output_schema` (closed key set), optional `merge` for
        internal channel conflicts, optional `annotations` (with
        `postprocessors` UI-grouping field, engine-opaque).

      Declarations outside the kind's applicable set (e.g.,
      `service_bindings` on a transform, `output_schema` on a hook,
      `transport_schema` on a transform or service, any bare-function
      kind discriminator on a trainable composition declaration), the
      wrong number of bare-function kind discriminators (zero, two, three),
      an unknown `meta.kind` value, and unknown sub-declaration names
      all raise
      [ContractViolation](#contractviolation)
      at handler-declaration load (engine startup). The grammar is not
      extensible by handlers; novel structure is an engine change
      accompanied by an architecture decision (per
      [Adding a new kind](#adding-a-new-kind)).

      Load-bearing for I1 (no implicit contracts): a handler whose
      declaration carries an unknown element is asking the engine to
      honor a contract it never declared. Closed-shape grammar makes
      this a structurally rejected case rather than a silently ignored
      one.

  - rule_id: R-handler-007
    name: handler import discipline
    derived_from: [I1, I3, I4]
    enforcement: review
    statement: |
      A handler module's import surface is deliberately narrow. The
      [`services` kwarg](#services-kwarg) on
      the dispatch signature — supplied by the runner as a
      [ServicesProxy](#servicesproxy) carrying
      the declared service-typed binding from `service_bindings` — is
      the **only** path from a handler body to external resources,
      mediated by the [service-type adapter](#the-service-type-adapter)
      for the binding. The import discipline below is what makes that
      claim mechanically true rather than aspirational.

      **Allowed.** `conjured.core.*` (engine-declared types and pure
      utilities — [its owner section](#conjured-core-surface) states the
      surface's contract); the Python standard library; library-internal pure
      technical utilities (algorithmic helpers, canonical
      serialization, pure computation that does not encode material
      content reaching training-relevant channels — prompt fragments,
      template strings, LLM-input composition belong in
      `bindings.<name>` declarations, not in utility modules).

      **Forbidden categorically.**

      - **Backend SDKs and protocol clients** — `requests`, `httpx`,
        `openai`, `anthropic`, `psycopg`, `redis`, DB drivers, LLM SDKs,
        gRPC clients, queue clients. Service-type adapter implementations
        may import these freely (the adapter's job is to make the one
        external call per invocation); handler bodies may not. The
        mechanical bypass — `services.<name>.invoke(...)` routing through
        the adapter — is the only sanctioned path.
      - **Service-locator and global-registry modules** — no
        engine-internal `*.registry` imports, no `*.services` modules,
        no DI-container patterns, no thread-local context-vars
        carrying handler-relevant state. The engine publishes no such
        registry module, so service-locator and context-var patterns
        are forbidden by absence-of-API rather than by runtime check;
        attempted imports encounter missing modules at handler-module
        import time.
      - **Dynamic-import mechanisms** — `exec`, `eval`, `__import__`,
        `importlib.import_module`, `getattr(sys.modules[...], ...)`.
        Dynamic imports defeat the import-graph discipline by
        deferring resolution past static analysis.
      - **Foreign library namespaces** — handler code in library
        `conjured_X` does not import from library `conjured_Y`.
        Cross-library coordination is consumer-side multi-pipeline
        orchestration (consumer territory per I3), not handler-internal
        coupling.
      - **Engine internals beyond declared interfaces** — handlers
        consume the engine via the dispatch contract (declared kwargs in,
        validated dict out) and the typed surfaces in `conjured.core.*`;
        they do not reach into the runner's closure, the validator's
        load path, the hash machinery, or the canonical event log.

      **Mechanical backing for "the services kwarg is the only path."**
      The runner cannot inspect handler-body imports at dispatch; the
      discipline is review-enforced via a CI AST walk over each handler
      module's import closure (transitive, scoped to the library's own
      namespace — the laundering pattern *handler → utility → backend
      SDK* is flagged at the handler's import closure, not just at the
      direct import site). First-party libraries run the check in their
      own CI; third-party libraries are encouraged to run an equivalent
      check as a publishing convention.

      **Why I4 anchors this.** Handler bodies bypassing the `services`
      channel emit invocations the canonical event log cannot capture
      under the I4 capture path — `service_invocation` events fire from
      the service-type adapter's mediation of `services.<name>.invoke(...)`,
      not from arbitrary backend SDK calls inside a handler body. A
      handler that imports and calls an LLM SDK directly produces a
      side effect the engine neither sees nor records; the captured
      training corpus omits the invocation, and the deployed pipeline's
      behavior diverges from the corpus the trained artifact saw. The
      import discipline preserves I4's mechanical promise.

      **Stdlib emission for hooks.** A hook emitting via stdlib
      (`logging`, file writes, stdout/stderr) does not require — and
      MUST NOT declare — a service-typed binding for those emissions.
      The stdlib-emission case is structurally distinct from
      backend-SDK access: stdlib emission does not carry training-
      relevant payloads under capture (no `service_invocation` event
      fires for `print(...)`), and the failure mode of stdlib emission
      (a missing log line) does not corrupt the training contract the
      way a missing `service_invocation` event would. See
      [handler-kinds § Hook](#the-hook-kind)
      for the two-case channel discipline.

  - rule_id: R-handler-008
    name: exactly one service-typed binding (service handler and trainable composition node)
    derived_from: [I2, I4, R-handler-001, R-handler-003]
    enforcement: mechanical
    statement: |
      The engine rejects compose-time construction (raises
      [ContractViolation](#contractviolation))
      when a service-kind handler's or trainable composition node's
      service-binding declaration does not match the required
      cardinality.

      **Service kind (handler-kind).**
      A service-kind handler's `service_bindings` MUST declare **exactly one** service-typed entry.
      Zero entries: a service handler that makes no external call is structurally a misclassified
      transform per `R-handler-003`. Two or more entries: a service handler that would make multiple
      external calls per dispatch violates the comparison-table "exactly one external call" profile and
      breaks the consumer-side R-handler-002 divergence-detection seam — the `service_invocation` ↔
      channel-write correspondence the paired-event analysis depends on for service-kind dispatches.
      Each service-kind dispatch emits exactly one `service_invocation` event paired with one
      `handler_exit`.

      **Trainable composition node (composition-kind).**
      The trainable composition kind's `trainable.service_bindings` MUST declare exactly one
      service-typed entry, and the bound implementation MUST be a [trainable backend](#trainable).
      The engine validates the binding-cardinality at compose
      AND that the resolved adapter carries the trainable-backend
      certification — see
      [§ Trainable backends — the I4 seal and the compose-time gate](#trainable-backends).
      The trainable-backend property is the integration property of the
      bound adapter (it satisfies the trainable-backend property contract, certified
      native-by-construction or by the trainable-backend audit-stamp),
      not a flag on the service-type declaration. Each trainable
      composition node dispatch
      emits a `handler_enter` + `handler_exit` pair (no
      `service_invocation`); the pair IS the captured training record.

      The engine refuses misclassifications at the compose-time
      construction gate rather than at runtime.

      **Multi-resource services split into separate handlers.** A
      composed step that genuinely needs multiple external resources
      (e.g., an embedding lookup followed by a dialogue trainable
      within one logical "dialogue step") MUST be authored as separate
      handlers composed sequentially in the pipeline. Each handler
      carries exactly one service-typed binding and exactly one
      external call per dispatch; each emits its kind's captured
      event (service: `service_invocation`; trainable composition
      node: `handler_exit`). This is structurally cleaner than
      multi-binding-per-service would be: per-dispatch correspondence
      stays a literal bijection; the comparison-table profile reads
      literally; the training projection is unambiguous.

      The check pairs with `R-handler-001`'s signature check: for
      service-kind handlers, the signature check verifies the handler
      accepts a `services` kwarg; this rule verifies the kwarg has
      exactly one binding to deliver. Both fire at the same compose-
      time gate. For the trainable composition kind, the engine
      constructs the dispatch directly (no signature to check); this
      rule fires against the `trainable.service_bindings` cardinality
      and the trainable-backend property.

      The mechanism is structural rather than disciplinary: the engine
      cannot produce a usable dispatch callable without the required
      binding(s), so a misclassified handler cannot produce a usable
      dispatch callable. Compose-time fails; the pipeline does not
      load; no dispatch ever happens.

  - rule_id: R-handler-009
    name: hook binding cardinality
    derived_from: [I1, R-handler-003]
    enforcement: mechanical
    statement: |
      The engine rejects compose-time hook construction (raises
      [ContractViolation](#contractviolation))
      when a hook's `service_bindings` declares two or more entries.
      Zero entries is the stdlib-emission case — the hook emits via
      `logging`, file writes, or stdout/stderr; the dispatch signature
      carries no `services` kwarg. One entry is the backend-SDK-
      emission case — the hook routes emission through
      `services.<name>.invoke(...)` against a single service-type; the
      dispatch signature carries `services`. Two or more entries would
      give the hook multiple external-call edges, contradicting the
      comparison-table "no output channels, no merge" profile. The
      engine cannot produce a usable dispatch callable for a hook with
      multiple bindings, so the misclassification is caught before any
      dispatch.

      A hook's `service_bindings` entry binds for emission only — never
      for training capture (the [trainable](#trainable) composition
      kind's role; see handler-kinds § Hook).

  - rule_id: R-handler-010
    name: trainable composition has no author body
    derived_from: [I4]
    enforcement: mechanical
    statement: |
      Trainable composition nodes MUST have no author body. The
      trainable composition declaration carries only metadata +
      composition structure (no Python handler file); the engine
      validates that no Python handler file is loaded for a trainable
      composition entry. The dispatch wrapper is fully engine-generated
      against the bound trainable backend (the trainable composition
      kind is the engine-owned-dispatch composition-kind
      specialization; the construction is fixed in this rule's
      no-author-body fragment).

      The engine-construction-control property is load-bearing for I4
      integrity: with no author body between the engine-controlled
      adapter call and the channel-write the engine routes, the
      silent-fallback failure class structurally cannot occur for
      trainable composition node dispatches. The engine IS the trusted
      author at the dispatch boundary; no adapter-boundary
      `service_invocation` capture is needed (and none fires —
      trainable composition node dispatches emit `handler_enter` +
      `handler_exit` only).

      Mechanically enforced by the trainable composition kind's
      compose-time construction path, which resolves the trainable
      composition declaration + the bound adapter and constructs the
      dispatch wrapper directly. The path has no admission point for
      an author Python function; attempting to register a Python
      handler under the trainable composition kind raises
      ContractViolation at engine startup.

  - rule_id: R-handler-011
    name: prompt-shaping content via trainable.reads
    derived_from: [I4]
    enforcement: review
    statement: |
      Prompt-shaping content reaching a trainable composition node MUST be produced by an upstream
      [preprocessor](#preprocessor) and arrive via `trainable.reads`; it MUST NOT appear in
      `trainable.config`. Templates, system prompts, prompt scaffolds, content-injection strings — none
      of these are valid `trainable.config` entries, which carry compose-time generation parameters
      (temperature, top-p, max-tokens, sampling-strategy enum, similar backend-side dials) ONLY.

      **Mechanical evidentiary backing.** Prompt content arriving via
      `trainable.reads` appears in `handler_enter.reads_snapshot` —
      the per-dispatch training-input record. Content slipped into
      `trainable.config` is partial-applied into the dispatch wrapper
      at compose time and is absent from `reads_snapshot` (it lives in
      the dispatch wrapper's compose-fixed config kwargs instead). The divergence is
      wire-visible: a captured `reads_snapshot` that lacks the prompt
      content the trainable evidently used is a structural signal that
      prompt content was slipped into `trainable.config`. Review
      grounds its judgment in this captured signal; the discipline
      survives review's interpretive layer because the wire evidence
      is uniform across runs.

      **Structural gate (service-type `[config_schema]`).** The bound
      service-type's `[config_schema]` gate
      ([R-service-type-002](#R-service-type-002)) admits only declared
      config fields as `[trainable.config]` entries; a prompt-shaping key
      is not one, so it fails compose with a
      [ContractViolation](#contractviolation). This mechanically
      forecloses the `trainable.config` route this rule forbids — the rule
      stays `review` for the positive half (prompt content MUST arrive via
      `trainable.reads`) and the interpretive judgment, now backed by both
      the wire evidence above and this compose-time gate.

  - rule_id: R-handler-012
    name: validator registration and binding contract
    derived_from: [I1, I2]
    enforcement: mechanical
    statement: |
      A **namespaced (dotted) validation key** names a registered
      third-party validator — a qualified name registered under the
      `conjured.validators` entry-points group, its value the parameter
      table — resolved at compose through the sibling resolution
      mechanism (the [inverted selector](#adapter-selector-inverted-priority);
      fail-loud collisions when two distributions register one qualified
      name; the R-handler-pure-module source-AST audit and the
      R-handler-bare-function function-shape check apply unchanged). A
      validator name MUST be namespaced; a bare registered name fails
      loud at first resolution. Built-in standard constraints attach as
      **bare** field keys; the two key-spaces — bare standard vocabulary,
      dotted third-party names — are disjoint by construction, so a
      registration can never shadow a standard keyword (no shadowing case
      to detect) and no separate `validators` list exists (§ Validators
      owns the one grammar). A resolved validator is a bare kwarg-only
      pure function whose signature is exactly the reserved `value`
      parameter plus the key's declared parameter names; any mismatch —
      extra, missing, positional, `**kwargs`/`*args` — raises
      ContractViolation at compose. The ENGINE binds the declared
      parameters at compose (engine-owned partial application; authors
      supply no factory, closure, or callable — parameters are data only)
      and wraps the bound validator into the field's generated Pydantic
      model. The verdict protocol is closed: None = pass; a string = the
      per-field failure message (SchemaValidationError, constraint_violated
      = the qualified name); any raise is the validator's own failure
      (PipelineFailure with cause_class), never a validation verdict. A
      field's validation keywords fold into the pipeline-hash as the
      field's validation configuration, in authored order.

      The rule also owns the compose-time **value-space coherence
      checks**: where co-declared validation keywords contradict, or
      where an `enum` member is inadmissible under the field's declared
      type — Enum-on-`Literal` subset coherence, enum-vs-length-bound
      coherence, enum-vs-field-type coherence (§ Validators owns each
      check's statement) — the contradiction is a validation-configuration
      defect knowable at compose and raises ContractViolation under this
      rule, never deferring to a per-dispatch SchemaValidationError.

      Load-bearing for I1/I2: a validator reference is part of the
      declared contract — it resolves, binds, and signature-checks at
      compose or the pipeline does not load; an unrecognized name or
      signature mismatch never defers to dispatch time.

  - rule_id: R-handler-013
    name: validator purity
    derived_from: [I2, I4]
    enforcement: review
    statement: |
      Validator bodies MUST be pure: no external runtime resource
      access, no non-deterministic operations (clock reads,
      random-number generation, observation of external state).

      Test: given the same value and the same bound parameters, does
      the validator return the same verdict on every invocation?

      The runner cannot inspect validator bodies; adversarial review
      catches violations at library publishing — the same body-opacity
      split as R-handler-004 (transform purity), whose mechanical
      halves are carried here by R-handler-012 (resolution seals,
      signature discipline, data-only parameters). A non-deterministic
      validator makes the same dispatch validate differently across
      runs — breaking replayability (I2) and making the captured
      training projection's admission criteria unstable (I4).

  - rule_id: R-handler-bare-function
    name: handler function-shape check (vector-2 seal)
    derived_from: [I3, I4]
    enforcement: mechanical
    statement: |
      At handler resolution, the engine performs a function-shape
      check on the resolved callable.
      The predicate is `inspect.isfunction(x)` — admits `def` / `lambda` /
      `@functools.wraps`-decorated functions; rejects classes, callable instances, bound methods,
      builtins, and `functools.partial` results (a partial's pre-bound args would bypass the
      declaration / `bindings.<name>` / hash surface). Resolution to any rejected shape raises
      ContractViolation at compose time.
      The exhaustive
      per-shape conformance set, with the rationale for each verdict, is
      fixed at the [function-shape predicate conformance
      set](#function-shape-predicate-conformance-set) in handler
      resolution.

      This is the **vector-2 seal** in the
      [trust-model](#trust-model-vector)
      vector inventory — it forbids "instance state on a callable
      class" as a hidden stash for compose-time author state. The check
      applies to transform / service / hook handlers (bare kwarg-only
      functions); the trainable composition kind has no author callable
      to check (per `R-handler-010`).

      Service-type adapter resolution uses a different shape check —
      adapter modules require the class shape (the adapter pattern) but
      constrain mutable state to instance scope (vector-7 seal, audited
      by `R-handler-pure-module`'s scope extension). The two seals
      differ because handlers and adapters realize different
      structural roles in the engine.

  - rule_id: R-handler-pure-module
    name: handler module purity
    derived_from: [I3, I4]
    enforcement: mechanical
    statement: |
      Handler modules MUST NOT contain, at any import-time-executing scope — module level, class
      bodies (nested classes included), or function default-argument expressions — mutable state
      in literal form, persistent caching decorators
      (`@lru_cache`, `@cache`, `@cached_property` at namespace scope), or import-time I/O (filesystem
      reads, network calls, client instantiation). Pure library imports (`import re`,
      `import numpy`) remain admissible.
      This is the **vector-3 and vector-5 seal** in the
      [trust-model](#trust-model-vector)
      vector inventory — namespace-scope mutable state in handler modules
      would leak compose-time author state across dispatches outside
      the declared `bindings.<name>` surface (vector 3), and import-time
      I/O would run uncontrolled side effects at module load (vector 5).

      An AST-walk audit enforces at compose, run on the module source *before* import (per
      [handler-resolution](#architecture-handler-resolution)) — a post-import audit cannot prevent
      import-time I/O. A module-dict snapshot-and-restore around each dispatch enforces at runtime as a
      defense-in-depth check, reverting any mutation the AST walk does not catch. A mutation the restore
      cannot undo raises (fail-loud); the engine never continues past a partial restore.

      **Scope extension — adapter modules (vector-7 seal).** The same
      AST-walk audit applies to service-type adapter modules with
      broader scope.
      Adapter modules MUST NOT contain class-level mutable state (class variables, `@lru_cache` on
      methods) or module-level mutable state. Instance state (initialized in `__init__` or assigned on
      `self` elsewhere) IS admissible — adapter instances are engine-managed compose-time state bounded
      by composition lifetime.
      The distinction from handler modules:
      handler modules forbid the class shape entirely (bare
      kwarg-only functions); adapter modules require the class shape
      (adapter pattern) but constrain mutable state to instance scope.
      Same mechanism (AST walk), broader scope.
```

{#handler-rule-fragments}
## Rule fragments

Single-source definitions of the rule kernels other docs depend on. The convention
(shared by every component reference that carries a fragments section):

(rule-fragments-convention-kernel)=

Each fragment's canonical
text lives here once; the docs that depend on a fragment render it inline by transclusion, so a
dependent doc can never drift from the rule it relies on. Where a derived-rule statement above
carries an owner fragment's exact text, it transcludes that fragment rather than restating it, so
the render mechanism — not hand-maintenance — keeps the statement and the fragment identical. Where
a statement instead states the rule in its own words — a deliberate compression, or a restatement
that carries more than the fragment — the fragment remains the owner wherever the two differ, an
agreement verified by review.

(R-handler-001-signature-union)=

The author function's signature is introspected once at construction and MUST be kwarg-only
with parameters equal to the union of the handler's declared input-port names,
`bindings.<name>` declarations, (where a service-typed binding is declared in
`service_bindings`) the reserved `services` kwarg, and (for a hook) the hook's declared
`transport_schema` field names. Any mismatch — extra kwarg, missing kwarg,
positional parameter, `**kwargs` collector, `*args` collector — raises ContractViolation at
compose time, before the first pipeline runs.

(R-handler-001-bare-function-dispatch)=

The engine constructs the dispatch wrapper at compose time, supplying the handler a fresh
per-dispatch copy of each resolved `bindings.<name>` value at each dispatch rather than
partial-applying a shared object (the vector-4 copy seal).

(R-handler-002-fallback-pattern-catalog)=

A handler MUST NOT mask internal failure with a schema-valid value the engine cannot
distinguish from a runtime-derived result. Production-resilience patterns — `except
Exception: pass`, exception-to-default mapping, `value or "default"` coercion on a required
read where `None` is meaningful signal, a hard-coded default returned when a derivation step
fails, semantic retry that buries the prior failed attempt — corrupt the training projection because the captured channel-record claims the
handler produced X for input Y when it actually failed.

(R-handler-002-evidentiary-backing-classification)=

`R-handler-002` is **review-enforced with mechanical evidentiary backing** rather than
review-only with no captured record: review grounds its judgment in the wire-visible **masking
signature** — a captured `service_invocation` ↔ `handler_exit` pair where the captured backend
response indicates failure or absence (an exception, an error payload, an empty result) yet the
handler returned a schema-valid `writes_snapshot`. Mere reshaping of a successful backend response
is not the signal: a faithful service that restructures a valid response diverges in shape without
masking, so shape-divergence alone over-fires.

(R-handler-003-discriminator)=

Handler kinds are a closed enumeration of three: transform, service, hook. The closed-enum
claim is mechanically enforced through the corpus-scope entry-path constraint: for bare-function
kinds the engine accepts only registered handlers tagged by top-level kind discriminator
(`transform`, `service`, or `hook`); for the trainable composition kind the discriminator is
`meta.kind = "trainable"` on the composition TOML primitive — not a handler-declaration
top-level header.

(R-handler-004-mechanical-half)=

The mechanical half of transform purity — "transforms forbid service-typed bindings" — is
structurally enforced by the kind-discipline absence of `service_bindings` on transforms (per
`R-handler-006`) and by the engine's compose-time signature check (per `R-handler-001`, which
rejects transform signatures carrying a `services` kwarg).

(R-handler-004-forbidden-patterns)=

Transform handler bodies MUST be pure: no external runtime resource access (no HTTP, DB,
filesystem, OS environment, or subprocess invocation); no non-deterministic operations (no clock
reads, random-number generation, or other observation-of-external-state operations the runner
cannot reproduce on replay). Test: given identical reads and binding values, does the transform
body return the same dict on every invocation?

(R-handler-005-literal-equal-kernel)=

The engine submits the declared output-port shape as the backend's structured-output /
constrained-decoding constraint when invoking the backend, and validates the backend's response
against the same shape at return. The schema as runtime contract and the schema as backend
constraint are **literally the same artifact** — no separate authoring step, no derivation
transform between the two roles. A backend that ignores the constraint and returns a response
that doesn't validate raises SchemaValidationError and halts.

(R-handler-006-reject-unknown-blocks)=

Declarations outside the kind's applicable set (e.g., `service_bindings` on a transform,
`output_schema` on a hook, `transport_schema` on a transform or service), the wrong number of
bare-function kind discriminators (zero, two, three), an unknown `meta.kind` value, and unknown
sub-declaration names all raise [ContractViolation](#contractviolation) at handler-declaration
load. The grammar is not extensible by handlers; novel structure is an engine change. A handler
whose declaration carries an unknown element is asking the engine to honor a contract it never
declared.

(R-handler-007-import-namespace-lists)=

**Allowed.** `conjured.core.*` (engine-declared types and pure utilities —
[its owner section](#conjured-core-surface) states the surface's contract); the Python standard
library; library-internal pure technical utilities. **Forbidden categorically.** Backend SDKs
and protocol clients (`requests`, `httpx`, `openai`, `anthropic`, `psycopg`, DB drivers, LLM
SDKs, gRPC clients, queue clients); service-locator and global-registry modules; dynamic-import
mechanisms (`exec`, `eval`, `__import__`, `importlib.import_module`,
`getattr(sys.modules[...], ...)`); foreign library namespaces; engine internals beyond declared
interfaces. The mechanical bypass — `services.<name>.invoke(...)` routing through the adapter —
is the only sanctioned path to external resources.

(R-handler-008-service-binding-cardinality)=

A service-kind handler's `service_bindings` MUST declare **exactly one** service-typed entry.
Zero entries: a service handler that makes no external call is structurally a misclassified
transform per `R-handler-003`. Two or more entries: a service handler that would make multiple
external calls per dispatch violates the comparison-table "exactly one external call" profile and
breaks the consumer-side R-handler-002 divergence-detection seam — the `service_invocation` ↔
channel-write correspondence the paired-event analysis depends on for service-kind dispatches.
Each service-kind dispatch emits exactly one `service_invocation` event paired with one
`handler_exit`.

(R-handler-008-trainable-binding-cardinality)=

The trainable composition kind's `trainable.service_bindings` MUST declare exactly one
service-typed entry, and the bound implementation MUST be a [trainable backend](#trainable).

(R-handler-010-no-author-body)=

Trainable composition nodes MUST have no author body. The trainable composition declaration
carries only metadata + composition structure (no Python handler file); the engine validates that
no Python handler file is loaded for a trainable composition entry. The dispatch wrapper is fully
engine-generated as `functools.partial(adapter.invoke, **config)` against the bound trainable
backend. Attempting to register a Python handler under the trainable composition kind raises
ContractViolation at engine startup.

(R-handler-011-config-vs-reads-split)=

Prompt-shaping content reaching a trainable composition node MUST be produced by an upstream
[preprocessor](#preprocessor) and arrive via `trainable.reads`; it MUST NOT appear in
`trainable.config`. Templates, system prompts, prompt scaffolds, content-injection strings — none
of these are valid `trainable.config` entries, which carry compose-time generation parameters
(temperature, top-p, max-tokens, sampling-strategy enum, similar backend-side dials) ONLY.

(R-handler-bare-function-predicate-admit-reject)=

The predicate is `inspect.isfunction(x)` — admits `def` / `lambda` /
`@functools.wraps`-decorated functions; rejects classes, callable instances, bound methods,
builtins, and `functools.partial` results (a partial's pre-bound args would bypass the
declaration / `bindings.<name>` / hash surface). Resolution to any rejected shape raises
ContractViolation at compose time.

(R-handler-pure-module-forbidden-patterns)=

Handler modules MUST NOT contain, at any import-time-executing scope — module level, class
bodies (nested classes included), or function default-argument expressions — mutable state
in literal form, persistent caching decorators
(`@lru_cache`, `@cache`, `@cached_property` at namespace scope), or import-time I/O (filesystem
reads, network calls, client instantiation). Pure library imports (`import re`,
`import numpy`) remain admissible.

(R-handler-pure-module-adapter-scope)=

Adapter modules MUST NOT contain class-level mutable state (class variables, `@lru_cache` on
methods) or module-level mutable state. Instance state (initialized in `__init__` or assigned on
`self` elsewhere) IS admissible — adapter instances are engine-managed compose-time state bounded
by composition lifetime.

(R-handler-pure-module-enforcement)=

An AST-walk audit enforces at compose, run on the module source *before* import (per
[handler-resolution](#architecture-handler-resolution)) — a post-import audit cannot prevent
import-time I/O. A module-dict snapshot-and-restore around each dispatch enforces at runtime as a
defense-in-depth check, reverting any mutation the AST walk does not catch. A mutation the restore
cannot undo raises (fail-loud); the engine never continues past a partial restore.

{#audit-stamps}
## Audit stamps — dated conformance audits, hash-gated for freshness

(audit-stamps-kernel)=

The review-enforced rule family over resolved modules — the conduct R-handler-pure-module's
mechanical AST walk cannot check (body semantics, judgment-call import discipline, adapter
conduct, the trainable-backend property contract) — is verified by a **dated audit**: an LLM
or human auditor runs the shipped audit prompt over a module and records the result as a
**sibling stamp**, `<module>.audit.toml` beside the audited source file (same stem). The
engine never re-runs an audit; it verifies **freshness** at resolution, hashing the module
source bytes it already reads for the pre-import AST walk and comparing them to the sibling
stamp. A module's stamp state is **fresh** — the hashes match AND the recorded verdict is a
pass-grade — or one of the three not-fresh states: **stale** (the source changed since the
stamp), **absent** (no stamp exists), or **failed** (the hashes match and the recorded
verdict is not a pass-grade). Under enforcement, any not-fresh state refuses compose.

The stamp's closed field set:

| Field | Content |
|---|---|
| `source_hash` | SHA-256 over the audited module's source bytes at audit time. |
| `audit_prompt_hash` | SHA-256 over the audit prompt the auditor ran — recorded so tooling and the re-stamp discipline can detect a stamp minted under a superseded prompt. The compose-time freshness check compares `source_hash` + `verdict` only (the 4-state kernel above); prompt-revision drift is a tooling/re-audit concern, not an engine-staled state. |
| `verdict` | Closed enum: `pass` / `pass-with-notes` / `fail`. Only pass-grades count toward freshness. |
| `date` | The audit date (ISO 8601 date). |
| `findings` | Path to the audit's findings report. |

Scope is structural, not configured: the check runs for exactly the modules resolution
performs the pre-import source read on — handler modules, adapter modules, validator
modules, and third-party **compiler** modules (the R-handler-pure-module scope, its
adapter-scope extension above,
[handler resolution](#architecture-handler-resolution)'s validator arm, and the
compile directive's shared dotted-path leg — the definitional clause and this
enumeration are one set by construction; a blessed compiler is engine code and takes
no stamp).

The stamp check's compose-time consequence is the deployment's opt-in — declaring
[`audit_enforcement`](#training-contract-section-audit-enforcement) makes compose refuse
any not-fresh module in scope, raising the structured
[ContractViolation](#contractviolation); without the opt-in, stamps carry no compose-time
consequence — they are tool-facing conformance artifacts, read when an auditor or consumer
inspects them. The rule is uniform: under enforcement, every resolved
module in scope needs a fresh stamp. The native library passes it because each native
module carries a fresh sibling `<module>.audit.toml` beside it, minted by running the
engine-shipped audit prompts — which ship at `conjured.conformance` alongside the native
members' findings reports, so an author feeds the same shipped prompt to their own agent to
audit and stamp their own modules. The shipped prompts and findings are simultaneously the
native members' conformance record and the worked example of the kit. The
[trainable-backend audit-stamp](#trainable-backends) is the mechanism's first shipped member.

---

{#worked-examples}
## Worked examples

The worked examples below illustrate the canonical declaration shape across kinds (the
three bare-function handler kinds + the trainable composition kind). They are illustrative;
the machine-readable per-kind schemas (`transform.schema.toml` / `service.schema.toml`
/ `hook.schema.toml` / `trainable.schema.toml`, documented at the `kind-schemas/README`)
are the authoritative authoring template.

{#transform-charset-filter-normalizer}
### Transform — charset-filter normalizer

A pure transform that strips emote markers from player text per a binding-supplied
charset selector. Reads upstream `player_input`; writes a normalized string.

```toml
[transform]

[reads]
player_input = { type = "str" }

[output_schema]
normalized_input = { type = "str" }

[bindings.config]
marker_set = { type = "Literal['brackets', 'asterisks', 'parens']" }

[annotations]
notes = """
Pure-function transform — a bare kwarg-only
function. The engine resolves `config` at compose and hands the
handler a fresh per-dispatch copy of it at each dispatch — fixed
across every dispatch of this composed pipeline, and harmless to
mutate because the copy is the handler's own. The TOML carries no
[service_bindings] section — that section is kind-disciplined out
on transforms per R-handler-006.
"""
```

The bare-function Python handler pairs with this TOML:

```python
# acme_dialog/handlers/normalize_charset.py

def normalize_charset(*, player_input, config):
    # `config` is a per-dispatch copy of the compose-resolved binding;
    # `player_input` is projected from the graph at this node's position.
    # ... strip markers per config["marker_set"] ...
    return {"normalized_input": stripped}
```

The pipeline declaration supplies the binding value inline or by external declaration
file path:

```toml
[[nodes]]
kind = "handler"
name = "acme_dialog.normalize_charset"
bindings = { config = { marker_set = "brackets" } }
```

{#service-embedding-lookup}
### Service — embedding lookup

A service-kind handler that calls a non-trainable embedding backend. Reads upstream
`query_text`; writes a dense embedding vector. Calls the backend exactly once per
dispatch per R-handler-008.

```toml
[service]

[reads]
query_text = { type = "str" }

[output_schema]
embedding = { type = "list[float]" }   # validated against the backend's declared output shape on return

[service_bindings]
embedder = { type = "acme_embeddings.dense" }   # R-handler-008: exactly one entry (non-training-capture path; for training capture see the Trainable example below)

[bindings.config]
model_name = { type = "str" }

[annotations]
notes = """
Service-kind handler — non-trainable backend (the service kind
retains its body for genuinely non-trainable backends; training
capture lives at the composition layer via the trainable
composition kind, not at service handlers). Every dispatch emits
service_invocation (at the adapter boundary) + handler_exit (after
the body completes) — the consumer-side R-handler-002
divergence-detection pair. The service-type adapter mediates the
external call; the body assembles the invocation and packages the
response into the declared output_schema output ports.
"""
```

Bare-function Python:

```python
# acme_dialog/handlers/embed_query.py

def embed_query(*, query_text, config, services):
    result = services.embedder.invoke(
        text=query_text,
        model=config["model_name"],
    )    # exactly one external call (R-handler-008)
    return {"embedding": result["embedding"]}
```

{#trainable-composition-llm-dialogue-generation}
### Trainable composition — LLM dialogue generation

A trainable composition node authored as a
[composition TOML](#composition-toml). No Python body (per
R-handler-010, which owns the engine-generated dispatch construction);
the engine constructs the dispatch directly against the bound trainable backend.
Emits `handler_enter` + `handler_exit` pair (no `service_invocation`); the pair IS the
captured training record. The [output ports](#output-port) declared in `trainable.output_schema` are
constrained-decoded by the backend per the literal-equal rule (R-handler-005). The full
trainable composition declaration grammar lives in the machine-readable
`trainable.schema.toml`; a pipeline embeds this composition by referencing the
composition declaration's path, and the engine flattens at compose time — wiring the
trainable composition's `inputs` channels from the embedding pipeline's channels and
exposing its `outputs` to downstream handlers.

{#hook-stdlib-emission-logging}
### Hook — stdlib-emission logging

A hook that writes a structured log line per pipeline run. Emits via stdlib `logging`;
declares no service-typed binding.

```toml
[hook]

[reads]
pipeline_run_id = { type = "str" }
dialogue = { type = "str" }

[service_bindings]
# empty — stdlib-emission hook needs no service-typed binding (R-handler-007 stdlib clause)

[transport_schema]
format = { type = "Literal['plain', 'json']" }   # log line format selector (per-deployment)
# No sink path here: the hook emits to a logger by name and the deployment's standard
# logging configuration routes the sink. The body attaches no handler of its own.

[bindings.config]
include_timestamp = { type = "bool" }

[annotations]
notes = """
Stdlib-emission case: the hook's transport_schema carries the
per-deployment format selector; the hook body emits to a named
logger via Python's standard logging module, and the deployment's
standard logging configuration routes the sink — the body attaches
no handler of its own. Operational PipelineFailure (disk full,
permission denied) is caught by the runner's hook wrapper per
R-error-channel-003.
"""
```

The bare-function Python handler:

```python
# acme_dialog/handlers/log_dialogue.py
import logging
from datetime import datetime, timezone

def log_dialogue(*, pipeline_run_id, dialogue, config, format):
    # no services kwarg — stdlib emission only; format arrives from the deployment's
    # transport block (hash-excluded), delivered like a binding. Emit to a named logger
    # and let the deployment's standard logging config route the sink — the body attaches
    # NO handler (a per-dispatch addHandler on the process-global logger getLogger returns
    # would leak a handler and its file descriptor on every dispatch).
    logger = logging.getLogger("dialogue_audit")
    line = {"run": pipeline_run_id, "dialogue": dialogue} if format == "json" else \
        f"run={pipeline_run_id} dialogue={dialogue!r}"
    if config["include_timestamp"]:
        logger.info("[%s] %s", datetime.now(timezone.utc).isoformat(), line)
    else:
        logger.info("%s", line)
    return None       # hooks return None by contract
```

{#hook-backend-sdk-emission-webhook}
### Hook — backend-SDK-emission webhook

A hook that POSTs each pipeline run's outcome to a webhook. Emits via a service-typed
binding.

```toml
[hook]

[reads]
pipeline_run_id = { type = "str" }
dialogue = { type = "str" }

[service_bindings]
webhook = { type = "acme_webhook.poster" }   # R-handler-009: exactly one entry for backend-SDK case

[transport_schema]
# empty-but-present per the pure backend-SDK-emission case in handler-kinds.md § Hook;
# the backend-SDK transport lives in the bound service-type's [transport_schema]

[bindings.config]
event_type = { type = "str" }

[annotations]
notes = """
Backend-SDK-emission case: the bound webhook service-type's adapter
mediates the external POST. The hook's [transport_schema] is empty-
but-present; the webhook service-type's [transport_schema] (endpoint
URL, auth header reference, timeout) carries the deployment config.

A mixed case (this hook AND stdlib emission) would carry a
non-empty [transport_schema] for the stdlib side alongside the
[service_bindings] entry for the backend-SDK side.
"""
```

The bare-function Python handler:

```python
# acme_dialog/handlers/webhook_emit.py

def webhook_emit(*, pipeline_run_id, dialogue, config, services):
    services.webhook.invoke(
        event_type=config["event_type"],
        payload={"run_id": pipeline_run_id, "dialogue": dialogue},
    )
    return None
```

The deployment must still wire an **empty-but-present** `hook_transport` block for this hook
— required by [hook-transport coverage](#R-pipeline-001-hook-transport-coverage) even though
the hook's backend transport rides the bound service-type's `transport.<binding>` block, not
`hook_transport`:

```toml
# deployment declaration
[hook_transport."acme_dialog.handlers.webhook_emit"]
# empty-but-present — the hook's transport_schema declares zero fields, so this block
# carries none either; it must still appear (omitting it fails coverage).

[transport.webhook]                                # the webhook backend transport — rides the binding
endpoint        = "https://hooks.internal/dialogue"
auth_header_ref = "[env]WEBHOOK_TOKEN"             # a secret_ref field — deployment reference § Secret references
```
