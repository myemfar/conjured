---
kind: reference
audience: [authors, integrators, agents]
slug: pipeline-reference
component: pipeline
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#pipeline-reference}
# Pipeline
A **pipeline** is a named, ordered composition of nodes that the engine
validates as a typed dataflow graph at load time and dispatches in declared
order at runtime. The pipeline is the engine's composition unit: one
pipeline = one composition identity = one set of
[trainable](#trainable) projections. It is the
locus of compose-time type-checking, hash computation, and
training-contract derivation.

Every pipeline is declared in a TOML file. The pipeline declaration names
nodes in order, supplies their service-type identity bindings, supplies
each handler's [compose-time bindings](#compose-time-binding)
(`bindings.<name>` values, inline or by external declaration file path),
embeds composition nodes by reference to their
[composition TOMLs](#composition-toml),
declares any cross-handler channel-write merges (`merge.<channel>` per
R-pipeline-002), and declares its API boundary (`inputs` — the
externally-seedable channels, required whenever the graph reads a channel before
any write; `outputs` — optional, presence opting
into the output commitment). The engine loads the declaration at compose time, type-checks
the resulting graph, and produces a dispatch-ready typed dataflow graph.
A pipeline that loads is a pipeline whose graph is internally consistent —
every channel type-checks, every binding resolves, every declared field
constraint is honored.

The pipeline component owns graph-level concerns: composition of nodes
into a typed dataflow graph, compose-time type-checking, the two-hash
scheme (whole-composition [pipeline-hash](#pipeline-hash)
+ per-trainable-composition [training-bundle-hash](#training-bundle-hash)),
dispatch order, channel-write merge resolution, and training-contract
derivation. Handler-level concerns (node interfaces, binding discipline,
engine-constructed dispatch wrapper) belong to the handler component;
failure-class concerns (error taxonomy, halt semantics) belong to the
error-channel component; the canonical event model + hash composition
specs are owned by [hash-model](#architecture-hash-model).

---

{#kernel-semantics}
## Kernel semantics

The engine dispatches handlers by reducing an ordered list over the graph's
channel state. The Clojure-flavored pseudocode that captures the kernel
shape:

```
(reduce (fn [state h]
          (merge state (route-writes h (h (project-reads h state)))))
        initial-state handlers)
```

The `merge` here is Clojure's **map-merge** — the runner assoc'ing each node's
routed writes into the channel-state map — *not* the `merge.<channel>` fan-in
strategy of [R-pipeline-002](#pipeline-derived-rules); the two senses are
unrelated. (Single-assignment makes this map-merge a pure add of fresh channel
keys; channel-write fan-in is the runner's separate inline fold, below.)

Each handler `h` receives its declared input PORTS — `project-reads`: each
port projected from the channel its read-map wires it to in the accumulated
channel state, delivered as kwargs keyed by port name (the handler never sees
the state map itself) — and returns its declared output PORTS (a dict keyed by
output-port name for transforms and services; the engine-routed adapter
response for trainable composition nodes — no author body returns anything
per R-handler-010; `None` for hooks) which the runner, after validation,
routes via the write-map onto the graph's channels (`route-writes`) before
invoking the next
node.

Three properties of the kernel are non-negotiable:

- **Per-run channel state.** Each pipeline invocation starts with a fresh
  channel state map scoped to the runner's invocation closure — no
  class-level attribute, no module-level variable, no state carried across
  invocations. Cross-run state lives in services or in consumer
  orchestration. This is the mechanical basis for
  [replayability](#replayability): same inputs
  and same service responses produce the same final state.
- **Declared order is the only sequencing mechanism.** Nodes dispatch in
  the order they appear in `nodes`. No hidden dependencies, no
  out-of-band signaling between nodes, no runtime reordering.
- **Channels are single-assignment.** A channel is produced by its writer(s)
  and consumed by its readers; no node wires a read-port and an output-port
  to the same channel. To transform a value, write a new channel; to combine
  independent contributors, declare a fan-in merge (the
  [merge kernel](#R-pipeline-002-merge-kernel) owns the rule). A cross-run
  "update" is carried by a next-channel the consumer feeds back as next run's
  input — the [`inputs` / `outputs`
  boundary](#inputs-outputs-optional-api-boundary-declarations) owns that feedback
  mechanism; the kernel itself carries nothing across invocations (below).
- **No cross-invocation state at the kernel level.** Long-lived
  service-internal state (loaded model weights, warm connection pools, GPU
  contexts) is permitted inside service implementations — but it is
  invisible to the kernel and never crosses into channel state.

The typed dataflow graph the kernel dispatches is the same structure the
[pipeline-as-training-contract](#glossary-pipeline-as-training-contract)
is derived from.

---

{#pipeline-toml-grammar}
## Pipeline TOML grammar

The pipeline author writes two declarations: a **pipeline declaration**
naming the composition, and a **deployment declaration** supplying
per-environment transport and opt-in enforcement. Both are covered below
because together they complete the pipeline's authoring surface.

{#pipeline-toml-sections}
### Pipeline TOML sections

{#meta-pipeline-self-name}
#### `meta` — pipeline self-name

Every pipeline declaration carries a required `[meta]` block naming the
pipeline, under the [family rule](#what-the-pipeline-hash-absorbs) — every
composable unit (the top-level pipeline and each composition kind) self-names
via `[meta].name`. The `name` is the pipeline's identity: its
`pipelines.<name>` deployment reference (the key a deployment's per-pipeline
override and the trained-artifact manifest resolve against). The block's key set
is closed — `{name}`.

```toml
[meta]
name = "conjured_npc.dialogue"   # the pipeline's identity / pipelines.<name> reference (required)
```

`name` is **identity, not structure** — it is **never hashed**, so renaming a
pipeline is hash-neutral.
See [hash-model § What is explicitly NOT in the pipeline-hash](#what-the-pipeline-hash-absorbs).
The block mirrors a [composition's `[meta]`](#composition-toml) minus `kind`: a
top-level pipeline has no composition-kind variant (`kind` discriminates a
`kind = "composition"` embed, not the outer pipeline). An absent `[meta]` or a
`[meta]` missing `name` raises [ContractViolation](#contractviolation) at
declaration load.

{#nodes-pipeline-node-sequence}
#### `nodes` — pipeline node sequence

(nodes-pipeline-node-sequence-kernel)=

The `nodes` array is the ordered list of pipeline node entries, one
per composition step. Source order is dispatch order; each entry's
`kind` field declares what realizes the node:

- `kind = "handler"` — a bare-function handler reference (transform / service / hook).
- `kind = "composition"` — an embed of a [composition TOML](#composition-toml). The embedded composition's own `meta.kind` discriminates its specialization — the composition-kind enum, whose [membership and realization status](#handler-toml-grammar-composition-kind-roster) the handler reference's grammar owns. Each kind's embed semantics (flatten, scoping, hash domain) are specified where this reference owns them: the `name` bullet below (the trainable and bundle embed treatments), the per-kind hash-treatment paragraph below, and [§ The nested `pipeline` composition kind](#nested-pipeline-kind). All kinds plug in via the same two-level discrimination without re-engineering the pipeline-declaration grammar.

Each entry's key set is **closed per kind**: a `kind = "handler"` entry admits exactly {`kind`, `name`, `bindings`, `reads_map`, `writes_map`}; a `kind = "composition"` entry admits exactly {`kind`, `name`}. An unknown key — or a handler-only key on a composition entry — raises ContractViolation at compose. The `kind` enum is likewise closed at the pipeline-declaration grammar layer; novel `kind` values raise ContractViolation at compose. Two-level kind discrimination is the structural shape: the outer `kind` here says *what realizes the node*; the embedded composition's `meta.kind` says *what specialization of the composition primitive* (the per-kind specialization rules determine which structural apparatus — pipeline-shape, scoping, own hash domain — the engine applies to the embedded composition).

```toml
[[nodes]]
kind = "handler"
name = "conjured_npc.generate_dialogue"   # qualified handler name (required)
bindings = { config = { prompt_template = "v3" } }   # inline object
                                             # inline scalar: system_prompt = "You are a knight."
                                             # OR by external declaration file:
                                             # bindings = { npc = { file = "npcs/knight.toml" } }
# reads_map  = { player_input = "player_input" }  # optional; omitted = all-identity
# writes_map = { dialogue = "dialogue" }          # (port name == channel name)

[[nodes]]
kind = "composition"
name = "trainables/dialogue_generation.toml"   # composition TOML path (required)
# The engine flattens the embedded composition TOML's [inputs] / [outputs]
# into the outer pipeline's channel graph at compose time.
```

- **`name`** —
  - For `kind = "handler"`, the qualified Python name resolved at pipeline-declaration load via [handler resolution](#architecture-handler-resolution) (dotted-path or the `conjured.handlers` entry-points group). An unresolvable name raises ContractViolation before any handler dispatches ([R-pipeline-001](#pipeline-derived-rules) compose-time validation). Handler resolution also performs the R-handler-pure-module source-AST audit before import and the R-handler-bare-function function-shape check (vector-2 seal per [trust-model](#architecture-trust-model)).
  - For `kind = "composition"`, the path to the embedded composition declaration. The trainable composition kind (`meta.kind = "trainable"`) has no Python author body (R-handler-010); the trainable composition declaration IS the dispatch unit. The engine flattens the embedded composition's `inputs` / `outputs` declarations into the outer pipeline's channel graph at compose time; the embedded composition's internal channels are [scoped channels](#scoped-channel) — they qualified-name to `<composition_name>.<channel_name>` post-flatten and cannot be written or read by nodes outside the composition's scope. The bundle composition kind (`meta.kind = "bundle"`) carries no own dispatch unit; the engine textually substitutes the bundle's `nodes` into the enclosing `nodes` sequence at compose, the inlined nodes then validated, type-checked, and hashed as if directly declared — the bundle grammar and substitution semantics are owned at [handler reference § The bundle composition kind](#bundle-composition-kind-grammar).
- **`bindings`** — applicable only when `kind = "handler"`. Inline table supplying compose-time `bindings.<name>` values. A binding value takes any form the handler reference's § Binding value-supply grammar admits (a bare string is always inline content, never a path); every form is resolved and validated once at compose and delivered to the handler as a fresh per-dispatch copy at each dispatch. Bindings that declare a value schema and no ship-time default in the handler's schema MUST be supplied here; a binding that declares a default MAY be omitted (the engine supplies the default) or overridden. Binding values contribute to the [pipeline-hash](#pipeline-hash); an external-file-supplied value folds per hash-model's owned [external-binding-content rule](#what-the-pipeline-hash-absorbs-external-binding-content) (canonicalized content, never the path — inline and file hash identically). Composition-kind entries supply their internal bindings inside their own composition declaration; declaring `bindings` on a `kind = "composition"` entry raises ContractViolation.
- **`reads_map`** — optional inline table, applicable only when `kind = "handler"`. The node's [read-map](#read-map): it wires each of the handler's declared [input ports](#input-port) (the keys of the handler's `reads`) to a channel in the graph. Each key is EXACTLY one of the handler's declared input-port names; each value is a plain channel-name STRING — data only, never an author-supplied callable, expression, or lambda, and never an external-declaration file path. A key naming an undeclared input port, or an input port mapped twice, raises ContractViolation at compose. The field is OPTIONAL and PER-PORT: any input port the author does not map desugars to a same-named channel at the single compose-time normalization step (see [§ Pipeline load lifecycle](#pipeline-load-lifecycle)). An omitted map = all-identity = reads exactly like a node that names no channels. An unmapped input port whose same-named channel is in scope NOWHERE — neither written by an upstream node's resolved write-map nor declared in [`inputs`](#inputs-outputs-optional-api-boundary-declarations) — raises ContractViolation at normalization (a dangling input port; the API-input set is closed and enumerable, so a typo'd port fails loud).
- **`writes_map`** — optional inline table, applicable only when `kind = "handler"`. The node's [write-map](#write-map): it routes each of the handler's declared [output ports](#output-port) (the keys of the handler's `output_schema`) onto a channel in the graph. Each key is EXACTLY one of the handler's declared output-port names; each value is a plain channel-name STRING under the same data-only constraint as `reads_map` (no callable, no expression, no external-declaration file path). A key naming an undeclared output port, or an output port mapped twice, raises ContractViolation at compose. The field is OPTIONAL and PER-PORT with the same same-named-channel identity desugar. The runner applies the write-map AFTER output-port validation, routing validated output-port values onto channels; the handler cannot name a channel, so it cannot smuggle one. A node's `reads_map` and `writes_map` MUST target disjoint channel sets — no node wires a read-port and an output-port to the same channel; overlap raises ContractViolation at normalization. This is the structural form of the kernel single-assignment property: it keeps a channel's value free of in-place mutation across the run, so each merge strategy is a pure reducer over independently-produced contributors. `reads_map`/`writes_map` are part of the pipeline-declaration grammar (their keys range over the handler's already-declared ports; they add no key to the handler-TOML grammar) and contribute to the [pipeline-hash](#pipeline-hash) as graph wiring. Declaring `reads_map` or `writes_map` on a `kind = "composition"` entry raises ContractViolation; the outer composition node's boundary wiring to its flattened `inputs` / `outputs` channels is the existing flatten mechanism.

Node order in `nodes` is the dispatch order. Reordering entries is a composition change that takes effect at next invocation (see [§ Hot-reload boundary](#hot-reload-boundary)); it shifts the pipeline-hash except when only hook entries move — hooks are `nodes` entries excluded from the pipeline-hash (per § What is excluded).

The hash treatment differs per composition-kind specialization. For the
**trainable composition kind** (engine-owned-dispatch), the embedded
composition's own canonicalized
hash IS its training-bundle-hash; the
outer pipeline-hash composes from the outer pipeline declaration's hash
plus by-reference inclusion of these embedded compositions' own hashes —
no cross-composition join. For the **bundle composition kind**
(pure-substitution), the bundle's content
is textually substituted into the outer pipeline declaration before
hashing; bundle has no own hash domain and is invisible to the hash
machinery. The nested **`pipeline` kind** likewise carries its own hash
domain, folded by reference — its treatment is owned at
[§ The nested `pipeline` composition kind](#nested-pipeline-kind). See
[hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs).

{#servicebindingsname-service-type-identity-supply}
#### `service_bindings.<name>` — service-type identity supply

Each service-typed binding a handler declares in its `service_bindings`
requires an identity supply in the pipeline declaration. The identity
supply lives here — it is the pipeline-level decision about *which*
service implementation satisfies *which* handler's binding.

```toml
[service_bindings.llm_main]
type             = "acme_llm.structured_output"  # service-type qualified name
model            = "qwen3.5-4b-gguf"                # identity value (hashed)
prompt_template  = "dialogue_v3"                    # identity value (hashed)
```

[Identity fields](#identity-service-binding)
(model name, prompt template, version selectors) contribute to the
pipeline-hash — they are composition-level decisions defining what the
pipeline is. [Transport fields](#transport)
(endpoint URL, credentials, timeouts) live in the deployment declaration's
`transport.<binding>` block and are not hashed — moving from staging to
production does not change the graph. Identity and config fields admit no
nullable declaration, so the reserved
[explicit-null form](#binding-value-supply-grammar-explicit-null) rejects
at compose in an identity or `config` position.

A `service_bindings.<name>` entry also supplies the bound service-type's
generation-parameter values in its **`config` block** — the service-binding
counterpart of the trainable composition kind's `[trainable.config]`, under
the supply contract the service-type reference's § The `[config_schema]`
contract owns (identical at both supply sites). The
values reach the implementation's `invoke()` as its config kwargs.

```toml
[service_bindings.llm_main.config]
temperature = 0.7        # config value — composition-fixed, supplied-or-default
max_tokens  = 1024
```

Hash treatment is the service-type reference's § Hash placement: a `config`
block's effective values ride the binding node's hash exactly as its identity
values do.

{#mergechannel-channel-write-disjointness-opt-in}
#### `merge.<channel>` — channel-write disjointness opt-in

Per [R-pipeline-002 (channel-write disjointness with `merge` opt-in)](#pipeline-derived-rules),
a channel's **contributors** are its seed (if the channel is a declared
[input](#inputs-outputs-optional-api-boundary-declarations)) plus its node
writes, in graph order. A channel MAY have two or more contributors **iff**
the pipeline declaration carries a `merge.<channel>` entry naming a
[merge strategy](#merge-strategy) from the engine's closed registry. Two or
more contributors without an explicit `merge.<channel>` declaration are
rejected at compose time with [ContractViolation](#contractviolation).

A merge is **fan-in**: the contributors are independent — each node write is
produced from the writer's own input ports, and a writer is a
channel-agnostic pure function that cannot reference the merged channel, so
read-then-rewrite is structurally impossible; the strategy is a pure reducer
over independently-produced contributors. Contributors combine in **graph
order** (the seed is the fold's first element; node writes follow in the
order the writing nodes appear in `nodes`, and where a single node writes two
or more output ports to one merged channel, those same-node writes fold in
declared write-map order): the runner folds each
contributor into the channel's value as it walks, and a reader's projection
is the strategy's left-fold over the contributors upstream of its position.

```toml
[merge]
npc_state         = "deep_merge_dict"
events_log        = "append_list"
narrator_response = "last_wins"
```

**Closed registry of merge strategies** (each carries a type constraint
the engine validates against the merged channel's declared type at
compose time):

| Strategy | Type constraint | Behavior |
|---|---|---|
| `last_wins` | any | Final contributor in graph order wins |
| `first_wins` | any | Earliest contributor in graph order wins |
| `append_list` | list-typed channel | Concatenate all contributors in graph order |
| `deep_merge_dict` | dict-typed channel | Recursive dict merge in graph order |
| `union_set` | list-typed channel (unique-element semantics) | Set union across contributors |
| `last_present_wins` | any | Latest non-None / non-empty contributor wins (default + conditional override pattern) |
| `concat_str` | string | Concatenate strings in graph order |

Three strategies carry a micro-semantic worth pinning: **`union_set`** preserves
first-occurrence order and dedups by **equality** (not hashing), so unhashable
elements (e.g. `dict`s) are admitted; **`last_present_wins`** reads "present" as a
non-empty value — length 0 is empty, numerics and booleans are always present — and
if every contributor is empty the last in graph order wins; **`deep_merge_dict`**
recurses only where BOTH sides hold a dict at a key — on any other collision the
later contributor in graph order wins whole (lists and scalars are replaced, never
concatenated), so nested maps interleave while every non-map leaf takes
last-writer-wins.

The registry is **closed-enum**; expansions go through an engine change. Fan-in
the closed registry cannot express is served by
[the aggregator pattern](#the-aggregator-pattern) (below) — an author-written
transform, not a shipped handler or an engine affordance.

**A merged channel's declared type MUST be non-optional.** Each strategy's type
constraint matches against a **non-optional base type**: a strategy reduces over
two-or-more independent contributors, and an `Optional[<T>]` merged channel would make
"is a contributor present?" a per-contributor reducer decision the closed strategy
cannot express. The engine does NOT see through the `Optional[...]` wrapper — an
`Optional[list[str]]` channel is rejected for `append_list`, not silently accepted as
a `list[str]`. A merged channel declared `<T> | None` raises ContractViolation at
compose; declare the channel as the non-optional base type the strategy requires.
(Presence-or-absence semantics that genuinely need a nullable channel are
[the aggregator pattern](#the-aggregator-pattern)'s territory, not a closed merge
strategy's — including the default-plus-override shape `last_present_wins` reads
"non-None / non-empty contributor wins" *across non-optional contributors*, not
over a nullable channel type.)

**Compose-time engine validation:**

- Each channel with two or more contributors in the pipeline MUST have a
  `merge.<channel>` entry; absence raises ContractViolation.
- Each declared `merge.<channel>` entry MUST name a channel the pipeline actually
  wires (one some port reads or writes); a `merge.<channel>` naming a channel no port
  wires is inert and is rejected at compose.
- The named strategy MUST be in the closed registry.
- For each declared `merge.<channel>` entry, the strategy's type constraint MUST match
  that channel's declared type, which MUST be non-optional — an `Optional[<T>]` merged
  channel is rejected (no see-through to the base type).

**Runtime semantics — the merge is the runner's own inline work.** A merged
channel's value is built **incrementally**: as the runner walks graph order
(the runner is the sole channel writer), it folds each contributor — a
validated node write, or the seed as the fold's first element — into the
channel's current value under the declared strategy — a left-fold in graph
order. **A reader's projection is the strategy's left-fold over the
contributors upstream of its own position** — so a reader composed
between two contributors of a merged channel is legal and sees the fold-so-far:
deterministic, recorded (the reader's own `handler_enter` snapshot captures
what it saw), and replayable. The input-closure check already guarantees at
least one contributor upstream of any reader of a merged channel, so the fold a
reader sees is never empty. The **final** value — what `outputs` cross-checks
and `RunResult.state` carry — is the fold over all contributors. There is **no
synthesized merge node and no merge event**: each writing node's own `handler_exit`
already records its contribution (its `writes_snapshot`), and a seed is the
invocation's supplied input value, so the channel's
state at *any* position is reconstructable as the declared strategy's fold
over the contributors upstream of it (the strategy is in the hashed pipeline
declaration) — the merge needs no node and no event of its own. (With
`last_present_wins`, an intermediate reader of a default-plus-override channel
sees the default until the overriding contributor has run — that is the pattern's
semantics, not an anomaly. Contrast [the aggregator pattern](#the-aggregator-pattern),
whose author-written transform *is* a real handler node — it has a function body,
so it is captured like any node.)

**Scope.** `merge.<channel>` declarations are
**scoped to their composition declaration**. An outer pipeline's `merge`
declaration cannot reach into an embedded trainable composition's internal
scoped channels and vice versa — cross-scope merges are structurally
impossible under [scoped channels](#scoped-channel).
A trainable composition that internally has a fan-in channel (two or
more contributors among its scope) declares its own `merge` declaration within
the trainable composition; the outer pipeline's `merge` covers only the
outer pipeline's channels. Bundle composition declarations have no own
scoped channels; merges across bundle-substituted content fall under the
outer pipeline's `merge` declaration.

{#the-aggregator-pattern}
##### The aggregator pattern — author-written fan-in

The closed registry above is the engine's whole merge vocabulary. Fan-in it
cannot express is served by the **aggregator pattern**: the author writes their
own transform — an ordinary handler under the full handler contract, **written
for the specific candidate types** — that reduces the candidates to the combined
value, and that reduction is always author code, never a shipped member:

(the-aggregator-pattern-division-of-labor)=

The [read model](#read-map) wires each input port to exactly one
[channel](#channel) of one declared [channel-field type](#channel-field-type),
and that type vocabulary admits no type-variable — so no single generic member can
be shipped for a type-parametric operation: the genericity lives in author code
parameterized by a binding, not in a shipped member or an engine affordance.

The division of labor is fixed — the **closed registry collects** what it can, the
**author transform custom-reduces** where the registry cannot.

The pattern has two shapes, selected by whether the candidates share a type:

- **Homogeneous candidates (one type).** The contributing nodes write **one
  shared, list-typed channel** under `merge.<channel> = "append_list"`; the
  closed registry concatenates the contributions in graph order into the
  collected list (a node offering a single candidate contributes a one-element
  list; a `list[T | None]` element type carries the presence-or-absence case the
  reducer then interprets). An author transform reads **that one collected
  channel** — one input port wired to one channel, the ordinary
  [read model](#read-map) — and reduces the list to the combined value, written
  to a fresh channel.
- **Heterogeneous candidates (distinct types).** The candidates cannot share a
  channel (a channel carries one declared type), so each occupies its **own
  channel** and there is no `merge` declaration. The author transform reads the
  candidate channels through **N explicit typed input ports, each wired to
  exactly one channel**, and combines them, writing the result to a fresh
  channel.

In both shapes every input port reads exactly one channel: the transform is a
normal handler the type-checker validates like any other, and its reduction is
author code parameterized by `bindings.<name>` declarations, never by code in the
pipeline declaration. Because it is a real handler node with a function body, it
is captured like any node — unlike a closed-registry strategy, which is the
runner's own inline fold.

**Worked example — homogeneous (collect with `append_list`, then reduce).** Two
retrieval nodes each contribute candidate documents to one shared channel; the
registry concatenates them; the author's transform reranks the collected list to
one chosen document:

```toml
# Two nodes contribute candidates to the same channel (each output port → candidate_docs).
[[nodes]]
kind = "handler"
name = "my_lib.handlers.retrieve_by_recency"
writes_map = { docs = "candidate_docs" }       # output port docs: list[Document] → candidate_docs

[[nodes]]
kind = "handler"
name = "my_lib.handlers.retrieve_by_relevance"
writes_map = { docs = "candidate_docs" }       # output port docs: list[Document] → candidate_docs

# The closed registry COLLECTS: append_list concatenates the contributions in graph order.
[merge]
candidate_docs = "append_list"                 # candidate_docs: list[Document]

# The author's type-concrete transform REDUCES the one collected channel to one value.
[[nodes]]
kind = "handler"
name = "my_lib.handlers.rerank_documents"      # reads candidate_docs: list[Document] → best: Document
reads_map  = { candidates = "candidate_docs" } # one input port ← one channel
writes_map = { best = "chosen_document" }
```

For heterogeneous candidates the transform instead declares one typed input port
per candidate channel (e.g. `reads_map = { mood = "npc_mood", stance = "npc_stance" }`)
and combines them in its body — the same ordinary handler, one port per channel.

{#inputs-outputs-optional-api-boundary-declarations}
#### `inputs` / `outputs` — API boundary declarations

`inputs` declares the pipeline's **externally-seedable channels** — the
channels an invocation may seed with a value: the pipeline's API. It is
required whenever the graph reads a channel before any write (the normal
case), so the API-input set is closed and enumerable. `outputs` is optional; its
presence and absence carry categorically distinct semantics. Each `inputs` /
`outputs` field declares a [channel-field type](#channel-field-type) — the same
token grammar handler `reads` / `output_schema` use. Boundary fields admit **no validation
keywords** (neither bare standard constraints nor namespaced third-party validators): the boundary's own validation
is presence-only (below), so a value constraint declared here would have no
enforcement point of its own — the silent-no-op class the engine forecloses
(the same fail-loud-inapplicability posture the handler reference's
§ Validators applies to inapplicable keywords). Declaring one raises
[ContractViolation](#contractviolation) at load. Value constraints live on the
port declarations that enforce them: the reading node's `reads` for an input
channel; the writing node's `output_schema` for an output channel.

```toml
[inputs]
player_input = { type = "str" }
session_id   = { type = "str" }

[outputs]
dialogue = { type = "str" }
emotion  = { type = "Literal['warm', 'wary', 'curious', 'distant']" }
```

The closure check: **a channel read before any write MUST be a declared
input.** An unmapped read-port whose same-named
channel is neither written by an upstream node's resolved write-map nor
declared in `inputs` is a dangling input port: a loud compose-time
ContractViolation at the normalization step, even when no `inputs` block is
present. This closes the API-input set so it is enumerable, and a typo'd
read-port fails loud rather than binding silently to a value the caller
never sends. **Presence of `inputs`** means the engine additionally
validates the presence of every declared input field in the incoming
request before dispatching the first handler; a missing field raises
ContractViolation at the API boundary (no handlers dispatch; the run never
starts, so no `pipeline_error` event fires). The pre-validation is
key-set-only — presence, never values: a declared input field supplied
with a type- or constraint-violating value passes the API boundary and
surfaces as [SchemaValidationError](#schemavalidationerror) at the seeded
channel's **first consumer** — its reads-projection or its merge fold,
whichever the runner dispatches first
([R-error-channel-001's key-set routing](#R-error-channel-001-key-set-routing)
owns the boundary routing; the [R-pipeline-001](#pipeline-derived-rules)
field-resolution clause guarantees every declared input field has at least
one reading node, so a consumer always exists).

**Absence of `outputs`** means the pipeline makes no explicit API
commitment about its success-path surface. **Presence of `outputs`**
declares the fields the consumer relies on receiving on happy-path
completion; compose-time validation cross-checks that every declared output
field is written by at least one handler in the composition.

For `outputs`, absence is categorically distinct from an empty-but-present
declaration. Absence opts out of the output API commitment. An empty
closed-shape key (body omitted) is an
[exhaustive-declaration](#architecture-exhaustive-declaration)
violation; `outputs` is presence-is-the-signal — omit it to opt out, present
it to opt in with the declared fields (body-required when present). `inputs`,
by contrast, is conditionally required — required wherever
the graph reads a channel before any write: every otherwise-unwritten read-port
channel must be covered by an `inputs` declaration or a dangling-input-port
ContractViolation fires at normalization. The training contract is derived
from the composition graph regardless of `inputs` / `outputs` presence.

**Carrying a value across runs.**

(inputs-outputs-carrying-a-value-across-runs)=

Channels are
[single-assignment](#kernel-semantics) — a run never mutates a channel value in place — so
a value that must persist from one invocation to the next (an NPC's evolving mood; a running
summary) is carried across the `inputs` / `outputs` boundary: the graph writes the
carried-forward value to a declared `outputs` channel (e.g. `mood_next`), the consumer reads
it off the [RunResult](#pipeline-result-runresult), and seeds it back as an `inputs` channel
on the next invocation. The engine holds nothing between runs
([§ Kernel semantics](#kernel-semantics)); the feedback path is the consumer's, threaded
through this API boundary — which is why a within-run loop that would rewrite one channel is
instead a next-channel the consumer feeds forward.

{#deployment-declaration-sections-relevant-to-the-pipeline}
### Deployment declaration sections relevant to the pipeline

{#trainingcontract-integrity-enforcement-opt-in}
#### `training_contract` — integrity enforcement opt-in

Declared in the **deployment declaration**, not the pipeline declaration.
The declaration is required, body-required per
[exhaustive-declaration](#architecture-exhaustive-declaration); the grammar
rule is owned at the deployment reference:

The closed-shape key MUST appear AND `integrity_enforcement` MUST carry an explicit boolean; a
missing `[training_contract]` block, an empty
body, or a missing field is [ContractViolation](#contractviolation) at deployment load.

```toml
[training_contract]
integrity_enforcement = true   # or false — explicit; no default
```

With `integrity_enforcement = true` the engine halts on
[training-bundle-hash](#training-bundle-hash)
mismatch at a trainable composition node unless the deployment's
`acknowledged_drift` entries explicitly cover that node. With
`integrity_enforcement = false` mismatches fire
`training_bundle_hash_changed` / `pipeline_hash_changed` canonical events
without halting. Either way, hashes are computed at compose time and events
fire on drift.

The opt-in separates the integrity *property* (always available — hashes
computed, events fire) from the integrity *enforcement* (halt on mismatch).
See [glossary § integrity enforcement](#integrity-enforcement)
for the property this declaration guards.

---

{#pipeline-load-lifecycle}
## Pipeline load lifecycle

Loading a pipeline declaration proceeds through four stages before the
first handler dispatches:

**1. Declaration parse.** The engine reads the pipeline declaration and
constructs a parsed struct — `nodes` entries in declared order,
`service_bindings` blocks, `inputs` / `outputs` if present. Unknown
declarations raise ContractViolation immediately.

**2. Compose-time validation ([R-pipeline-001](#pipeline-derived-rules)).** The
engine runs the full compose-time type-check. This pass covers: resolving
every handler qualified name via [handler resolution](#architecture-handler-resolution)
(dotted-path or entry-points); resolving every service-binding type
against the registered service-type registry; resolving every embedded
composition declaration by path (a pure-substitution bundle's nodes are
textually substituted into the node sequence FIRST — before anything
scopes, validates, or hashes — and validate as if directly declared, per
[glossary § Bundle TOML](#bundle-toml); engine-owned-dispatch kinds
flatten their `inputs` / `outputs` boundary into the outer pipeline's
channel graph);
normalizing each node's `reads_map` / `writes_map` to the always-explicit
wiring IR — the single per-port desugar step that resolves every unmapped
port to a same-named channel, fails loud on a dangling input port (no
upstream writer and no `inputs` declaration), and runs before channel-type
matching and before any hash so identity-sugar is hash-neutral;
verifying channel-type agreement between upstream writes and downstream
reads across the node sequence; verifying any channel with two or more
contributors has an explicit `merge.<channel>` declaration per
[R-pipeline-002](#pipeline-derived-rules); verifying that every service-typed
binding declared in a handler's `service_bindings` has a corresponding
`service_bindings.<name>` identity supply in the pipeline declaration;
verifying kind disciplines (the trainable-composition binding cardinality per
[R-handler-008](#R-handler-008));
verifying hook transport coverage; and cross-checking `inputs` / `outputs`
declarations against the node sequence. A graph that fails any check
raises ContractViolation here — before any handler dispatches.

**3. Hash computation.** After successful compose-time validation, the
engine computes the pipeline-hash and
per-trainable-composition
[training-bundle-hashes](#training-bundle-hash).
The engine compares computed hashes against the
trained-artifact manifest (if any), fires canonical events on drift
(`training_bundle_hash_changed` / `pipeline_hash_changed`), and — when
`integrity_enforcement = true` — halts on training-bundle-hash
mismatch unless acknowledged. See [§ Hash model](#hash-model-at-pipeline-grain) for what
each hash absorbs.

**4. Engine-side dispatch construction.** Each `nodes` entry is
routed through the engine's compose-time path per R-handler-001
(engine-constructed dispatch wrapper).
For bare-function kinds (transform / service / hook), the engine resolves
the bare author function via handler resolution, performs the
R-handler-pure-module source-AST audit + R-handler-bare-function
function-shape check, generates Pydantic models from the handler's
declared `reads` and `output_schema`, and constructs the dispatch
wrapper — a callable that, at each dispatch, supplies the handler a fresh
per-dispatch copy of each resolved `bindings.<name>` value alongside the
projected `reads`. For the
trainable composition kind, the engine resolves the bound trainable
backend's service-type adapter (running the vector-7 AST audit per
R-handler-pure-module's scope extension), resolves `trainable.config`
+ `trainable.service_bindings`, and constructs the dispatch wrapper
against the bound backend — no author body
involved per [R-handler-010](#R-handler-010-no-author-body), which owns
the engine-generated construction. The construction binds only the compose-fixed
config; the runner supplies the closed dispatch-kwargs (the closed set is
owned at the service-type reference's § Closed dispatch-kwargs) at each
dispatch, exactly as it does for a service handler — `input_payload` here
being the `trainable.reads` projection. (A trainable dispatch is a service-type adapter dispatch
with no author body wrapping it — the same boundary, stripped.) The
assembled ordered list of dispatch callables (handlers + trainable
composition node dispatches) is the typed dataflow graph ready to
dispatch; channel-write merges are applied **inline by the runner** (per
R-pipeline-002), not as dispatch callables.

---

{#hot-reload-boundary}
## Hot-reload boundary

The engine distinguishes two effective-on times: **next invocation** (the
change takes effect the next time the runner dispatches this pipeline) and
**process restart** (requires restarting the engine process). The
distinction is mechanical, not a judgment call — it follows from where the
edit is resolved.

The runner reads pipeline declarations and handler declarations per
invocation; edits to those files are hot-reloadable. Entry-point enumeration
runs once at engine startup; installing or removing packages providing entry
points requires restart.

| Edit | Effective when | Why |
|---|---|---|
| Reorder, add, or remove `nodes` entries in a pipeline declaration | Next invocation | Runner reads pipeline declaration per invocation |
| Change a `nodes` entry's `bindings` value block in a pipeline declaration | Next invocation | Runner reads pipeline declaration per invocation |
| Change a `nodes` entry's `reads_map` / `writes_map` in a pipeline declaration | Next invocation | Runner reads pipeline declaration per invocation; the wiring is graph composition (pipeline-hash shifts), same boundary as editing a `nodes` entry's `name` (its qualified-handler reference) |
| Change a `service_bindings.<name>` identity value in a pipeline declaration | Next invocation | Runner reads pipeline declaration per invocation |
| Add, remove, or reorder `merge.<channel>` declarations in a pipeline declaration | Next invocation | Runner reads pipeline declaration per invocation; the runner folds contributors under the declared merge strategy inline per R-pipeline-002 (a runner operation) |
| Add or remove hook entries from a pipeline declaration | Next invocation | Hooks are `nodes` entries with `kind = "handler"`; runner dispatches from current declaration |
| Edit an embedded composition declaration (a trainable composition declaration or a bundle composition declaration) referenced by a `nodes` entry with `kind = "composition"` | Next invocation | Composition declarations resolve at compose; for the trainable composition kind, the embedded declaration's own hash shifts the outer pipeline-hash + that node's training-bundle-hash; for the bundle composition kind, the substituted content shifts the outer pipeline-hash directly (no separate hash domain) |
| Edit a handler declaration — bindings schemas, validator configurations, `output_schema` field modifications | Next invocation | Runner reads handler declaration per invocation |
| Edit a deployment declaration `transport.<binding>` block | Next invocation | Unhashed; runner reads deployment declaration per invocation |
| Edit a deployment declaration `training_export` block | Next invocation | Unhashed; routes new events to the new destination |
| Install or remove a package providing entry points (handler, service-type, validator, adapter) | Process restart | Entry-point enumeration runs once at engine startup |

Studio and third-party authoring tools surface this boundary in their UX —
exposing whether a pending edit takes effect on the next turn or requires a
restart. The specific UX representation is consumer territory per the
engine/consumer partition in
[principles.md § engine / consumer / review partition](#engine-consumer-review-partition).

**Editing a `nodes` entry's `name` to reference a different qualified
handler or composition declaration** is a composition change (pipeline-hash
shifts) that is hot-reloadable at next invocation — even though it swaps
implementations — because entry-point enumeration already saw both
registered names. Only *installing or removing* the package providing
those names requires restart. Editing a node's `reads_map` / `writes_map`
sits at the same boundary: it re-wires the graph (pipeline-hash shifts) and
is hot-reloadable at next invocation, since the runner re-reads the pipeline
declaration. The boundary is decidable at the engine-surface layer (which
declaration the edit touches); no interpretation is needed.

---

{#orchestration-scope}
## Orchestration scope

The engine API accepts one `(pipeline, inputs)` pair per invocation. One
typed dataflow graph; one dispatch; one output (or one error). No **runtime**
multi-pipeline orchestration primitive exists in the engine surface.

**The line it draws — conditionality, not nesting.** The patterns the engine
declines all share one property: they need a **runtime decision** — the engine
would have to inspect a runtime value and *choose* what runs next (which
pipeline fires, whether to retry, how many branches to spawn). A
runtime-selected graph cannot be verified before it runs, so it cannot satisfy
[I2](#invariants-and-derived-rules) (determinism under composition — *can the
engine verify this composition before it runs?*). Hidden runtime conditionals
are precisely what the engine refuses: they put the executed graph beyond
compose-time verification.

**The engine-side guarantees that fix this boundary.** Three affirmative
commitments hold *within a single invocation* — each the engine-side floor
beneath a consumer-territory capability below, and each a face of
[I2](#invariants-and-derived-rules) (only a statically verifiable graph runs)
and [I3](#invariants-and-derived-rules) (cross-invocation coordination is the
consumer's):

- **Sequential dispatch in declared order.** The engine dispatches every node —
  and every statically nested embed a composition contains — one at a time, in
  declared order, never concurrently; the sequencing rule is owned at
  [§ Kernel semantics](#kernel-semantics), and the nested
  [`pipeline`](#nested-pipeline-kind) kind embeds under it. The engine exposes no
  parallel-dispatch or node-scheduling primitive, so concurrent scheduling and
  fan-out are consumer-territory (the consumer spawns concurrent invocations —
  see the list below).
- **No scheduling primitive.** The engine exposes no delay, sleep, timer, or
  scheduling primitive within a run: a node dispatches the moment its
  predecessor's writes are threaded, never on a clock or a future tick. The
  optional [`timeout_ms`](#consumer-pipeline-level-timeout-request-param) is a
  whole-run halt budget, not a scheduler — it never delays a dispatch. Time-based
  and event-driven invocation are consumer-territory.
- **No mid-invocation partial values.**

(orchestration-scope-no-partial-values)=

No engine surface exposes a partial,
incremental, or streamed channel value mid-invocation: a channel carries its
complete validated value when the runner writes it
([§ Kernel semantics](#kernel-semantics)), and the captured training record is
that same value
([channel–record correspondence](#channel-record-correspondence)) — never a
fragment. Token-level streaming delivery ships as the run-scoped
[`stream_sink`](#pipeline-invocation) — a provisional, consumer-facing transport
affordance (latency/UX) that does not expose a partial channel value: fragments
reach only the attached sink, never a channel or a captured record.

  Streaming *accumulation* — looping calls and threading state across
  invocations — is consumer-territory.

This is a positive constraint — a guarantee the consumer relies on, not a
restriction on what is achievable overall. The engine's per-invocation
purity is what gives the consumer the substrate to build any orchestration
shape. Patterns the engine forbids *within a single pipeline run* — each
because it needs a runtime decision — are fully available at the consumer
layer via multi-pipeline composition:

- **Conditional / branching workflows** — consumer evaluates pipeline A's
  output, chooses pipeline B or C. *(A runtime decision tree — the executed
  path is a runtime value, not a compose-time fact.)*
- **Parallel / fan-out execution** — consumer spawns concurrent
  invocations, collects results.
- **Retry / tool-transform loops** — consumer calls a pipeline, inspects
  output, calls again with adjusted inputs.
- **Streaming accumulation** — consumer loops pipeline calls, threading
  state as initial channel inputs.
- **User interaction mid-flow** — consumer pauses between invocations for
  input.
- **Time-based or event-driven invocation** — consumer schedules calls,
  fires on events.
- **Dynamic sub-pipeline composition** — consumer builds *runtime*
  pipeline-of-pipelines orchestration (which sub-pipeline fires, how many, or how
  deep, decided at runtime) at its own layer — distinct from the engine's
  compose-time-static [nested `pipeline` kind](#nested-pipeline-kind).
- **Complex error-recovery flows** — consumer catches pipeline failures,
  invokes repair pipelines.

The engine does not take these on: cross-invocation coordination belongs
where cross-invocation state already lives — the consumer layer (per the
duplication-collapse test in
[principles.md § engine / consumer / review partition](#engine-consumer-review-partition)).

**Static nesting is not orchestration.** Hierarchically composing pipelines whose
structure **and depth are known at compose time** is not a runtime decision,
so it is not consumer territory — it is the engine's
[nested `pipeline` composition kind](#nested-pipeline-kind), a
compose-time-static embed that type-checks whole at load (I2 holds). It runs
inside the one `(pipeline, inputs)` invocation, exactly as an embedded trainable
composition does; the nesting is **fixed-depth — the depth is a compose-time fact**,
so a non-terminating embedding (a cycle) is rejected at load, never run. The "no
runtime multi-pipeline orchestration primitive" above governs *runtime
coordination* — choosing among pipelines on a runtime value, or looping until a
runtime condition — never static composition.

**Cross-invocation observability** is consumer-threaded. Consumers
composing multiple invocations MAY supply a shared `pipeline_run_id`
across related invocations (the engine accepts consumer-supplied
identifiers) to enable cross-invocation trace reconstruction via log
aggregators. No engine-side cross-invocation grouping exists.

---

{#nested-pipeline-kind}
## The nested `pipeline` composition kind

The nested `pipeline` composition kind is **engine-invoking-engine**: a
pipeline statically embeds another pipeline as a node, so a composition nests
sub-pipelines to a depth **fixed at compose time**. It is an own-hash-domain
composition embed — the same mirror mechanism a nested trainable uses.

**It is static, so I2 holds.** The set of embedded pipelines, their wiring, AND the
nesting depth are all declared, not chosen at runtime: the whole nested structure
type-checks at load, so the engine can verify the composition before it runs
([I2](#invariants-and-derived-rules)). This is the boundary
[§ Orchestration scope](#orchestration-scope) draws — static nesting is engine
territory; *runtime-conditional* pipeline selection is the consumer's. The embedding
is **fixed-depth**: nothing about what runs or how deep is decided at runtime. It does
not iterate until a runtime condition, and it does not select among alternative
sub-pipelines on a runtime value — both are runtime decision trees the engine cannot
verify before it runs. Iterate-to-convergence and runtime-depth traversal are the
consumer's, threaded across invocations exactly like every other dynamic pattern (see
[§ Orchestration scope](#orchestration-scope)).

**Embed grammar — the mirror.** The embed is a `kind = "composition"` node whose
embedded `meta.kind = "pipeline"`; outer channels wire to the inner `[inputs]` **by
name** (the flatten-by-name boundary contact — a composition entry carries no
`reads_map`/`writes_map`, per § nodes) and the inner `[outputs]` wire back out the
same way, exactly as for any composition
embed (the [mirror-pipeline principle](#the-mirror-pipeline-principle-kernel); the hash
treatment single-sourced at
[hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs)). It
follows the pipeline's presence-opts-in `[outputs]` arm, not the trainable's
body-required arm. One invocation contains the whole nested structure — it is not a
runtime multi-pipeline orchestration primitive.

**Hash, depth, halt, capture:**

- **Hash** — own-hash-domain; folds its own canonicalized hash by reference into the
  enclosing unit's hash (opaque inner scope), per
  [hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs).
- **Termination — compose-time.** Because the nesting is fully declared, the engine
  resolves the embed graph at load. A cycle — a pipeline that transitively embeds
  itself — is the only non-terminating case under static nesting, and the engine
  rejects it as a [ContractViolation](#contractviolation) at compose, before any node
  dispatches. This is the same load-time rejection every other compose-knowable fault
  takes (structural, not a runtime guard): a cyclic composition never loads, so it can
  never run. A finite acyclic nesting always terminates and type-checks whole at load,
  so its depth is whatever the author declares — there is no depth ceiling. There is no
  runtime depth guard and no `max_depth` invocation parameter.
- **Halt propagation** — a nested `pipeline` embed node is a channel-writing position
  (its `[outputs]` wire back into the enclosing graph), so a halt inside the inner run
  halts the embedding node exactly as any channel-writing dispatch does
  ([R-error-channel-003](#R-error-channel-003)). The inner
  error surfaces as the embedding node's failure with the **attribution chain
  intact**: the inner halt's locus (its `pipeline_run_id`, `composition_ref`, and
  failed-handler position) is preserved through the boundary, correlated to the outer
  run by `parent_run_id`. No inner failure is swallowed; fail-loud propagates outward.
- **Capture nesting** — an inner run emits its own canonical-event stream under its
  own `pipeline_run_id`, correlated to the outer run by `parent_run_id`; the inner
  training corpus is reconstructed by correlation, not duplicated into the outer
  stream, per
  [hash-model § canonical event types](#canonical-event-types).

---

{#in-process-compose-api}
## In-process compose API

[§ Pipeline load lifecycle](#pipeline-load-lifecycle) describes what the engine does as a
declaration loads; this section names the public functions an **integrator** calls to drive
that lifecycle in-process — from declaration text to a dispatch-ready
[`Runnable`](#pipeline-invocation). It is the same path the engine's own tests compose
through. The API has two halves: the compose front-half (parse → register → compile →
assemble, below) and the run entry ([§ Pipeline invocation](#pipeline-invocation)).

The call shape, end to end — illustrative of the sequence; the exact signatures are those of
the public exports named at the end of this section:

```python
from conjured.validator import DeclarationRegistry, loads, compile_pipeline
from conjured.runner import assemble, run

registry = DeclarationRegistry()
registry.add_handler("greet.greet", loads(handler_toml, "handler", file_path="greet.toml"),
                     toml_path="greet.toml")

pipeline = loads(pipeline_toml, "pipeline", file_path="pipeline.toml")
graph = compile_pipeline(pipeline, registry, pipeline_name="demo.hello", file_path="pipeline.toml")
runnable = assemble(graph, registry)
result = run(runnable, {"name": "world"})
```

**1 — Parse each declaration.** `loads(toml_text, kind)` parses one declaration TOML
*string* of the given `kind` — `handler`, `service_type`, `pipeline`, `composition`, or
`deployment` — into its typed declaration record (`parse(data, kind)` does the same from an
already-parsed mapping). A TOML syntax error surfaces as a
[ContractViolation](#contractviolation) — the single error class the whole path raises.

(in-process-compose-api-registry)=

**2 — Register the parsed declarations.** A `DeclarationRegistry` is the in-memory set of
parsed declarations compose-time resolution reads names against: handler declarations by
qualified name, service-type declarations by qualified name, composition declarations by
declaration path. Its registration methods — `add_handler`, `add_service_type`,
`add_composition` — each admit one parsed declaration; the optional `toml_path` records the
declaration's on-disk location (the path a diagnostic cites, and the directory a
`{ file = "…" }` binding resolves against). Registration registers **declarations**, not
functions: the handler *callable* is not registered here — it is resolved from the
declaration's name at step 3 by [handler resolution](#architecture-handler-resolution)
(dotted-path or entry-points).

**3 — Compile the pipeline to a graph.** `compile_pipeline(pipeline, registry, *, pipeline_name=…)`
runs the full [stage-2 compose-time validation](#pipeline-load-lifecycle) over the parsed
pipeline against the registry and returns the compiled typed dataflow graph. Every contract
failure raises a [ContractViolation](#contractviolation) here — before any handler dispatches.
`pipeline_name` is the qualified name this compilation runs under. The pipeline's identity is
its required [`[meta].name`](#meta-pipeline-self-name); the engine's own drivers pass that as
`pipeline_name`, and a caller supplies an explicit value only to compile the same declaration
under a chosen name (the compose-your-own-path / test flow) — never a second identity the
declaration lacks.

**4 — Assemble the graph into a Runnable.** `assemble(graph, registry, deployment=…)`
completes [stage 4](#pipeline-load-lifecycle) — resolving each handler and adapter, generating
the validation models, and computing the [pipeline-hash](#pipeline-hash) — into the frozen,
dispatch-ready [`Runnable`](#pipeline-invocation). `deployment` defaults to the deployment
registered on the registry.

**5 — Run.** `run(runnable, inputs, …)` dispatches the assembled pipeline and returns a
[`RunResult`](#pipeline-result-runresult); its parameters and its raise-on-halt success
contract are owned by [§ Pipeline invocation](#pipeline-invocation) and
[§ Pipeline result](#pipeline-result-runresult) below.

**Native service-types register their declaration too.** A native `conjured.lib.*`
service-type's *implementation* resolves through the engine's own native adapter table (never
your registry or entry-points), but its *declaration* is still registered like any other, so
compose-time validation can resolve the binding's type: hand-load the engine-shipped sibling
TOML — `conjured/lib/<name>.toml` — and register it with `add_service_type`. Registering the
genuine shipped declaration under its native name is legal; registering a *modified*
declaration under a `conjured.lib.*` name fails loud
([R-service-type-004](#R-service-type-004)). The engine does not auto-register the shipped
declarations.

These functions are the public exports of `conjured.validator` (`loads`, `parse`,
`DeclarationRegistry`, `compile_pipeline`) and `conjured.runner` (`assemble`, `run`).

The values these signatures pass between the steps are **opaque engine-constructed
handles**, and they are the public exports of `conjured.ir` — importable for type
annotation: the parsed declaration records `loads`/`parse` return (`HandlerDeclaration` —
the closed kind-discriminated union a handler declaration parses to —
`ServiceTypeDeclaration`, `PipelineDeclaration`, `TrainableComposition` /
`PipelineComposition` / `BundleComposition`, `DeploymentDeclaration`) and the compiled
graph `compile_pipeline` returns (`CompiledGraph`). A consumer passes a handle from the
step that produced it to the step that consumes it and never constructs or introspects
one: the records' fields realize the declaration grammars each component reference owns
and are engine-internal, not consumer surface. (`Runnable` and `RunResult` are owned
below — [§ Pipeline invocation](#pipeline-invocation) and
[§ Pipeline result](#pipeline-result-runresult).)

---

{#pipeline-invocation}
## Pipeline invocation

A loaded pipeline is dispatched through the engine's in-process, per-invocation
entry, `conjured.runner.run`:

```
conjured.runner.run(
    runnable: Runnable,
    inputs: Mapping[str, object],
    *,
    pipeline_run_id: str | None = None,
    timeout_ms: int | None = None,
    stream_sink: Callable[[str], None] | None = None,
) -> RunResult
```

- **`runnable`** — a `Runnable`: the frozen, dispatch-ready record
  [pipeline load lifecycle](#pipeline-load-lifecycle) stage 4 produces
  (`conjured.runner.assemble`). It carries the assembled nodes, the declared API
  boundary, and the [pipeline-hash](#pipeline-hash) the run's canonical events name —
  immutable, and holding nothing across invocations.
- **`inputs`** — the run's **initial channel values**: a mapping keyed by declared
  [`inputs`](#inputs-outputs-optional-api-boundary-declarations) channel name, seeded
  and presence-checked at the API boundary per
  [R-pipeline-001 § API-inputs enforcement](#R-pipeline-001-api-inputs-enforcement).
  Passing a non-mapping here is engine-surface misuse, not an author-facing contract
  case: it raises a plain `TypeError` outside the
  [error channel](#glossary-error-channel) (the signature types the contract; the
  closed error classes govern declared-interface failures, not gross signature misuse).
- **`pipeline_run_id`** (optional) — a consumer-supplied run identifier the engine
  threads through the run's canonical events and echoes back on the
  [`RunResult`](#pipeline-result-runresult-shape); when omitted, the engine mints the
  structured, sortable identifier owned at
  [hash-model § canonical event types](#canonical-event-types).
- **`timeout_ms`** (optional) — the whole-run budget, the
  [consumer pipeline-level timeout](#consumer-pipeline-level-timeout-request-param);
  when omitted, the engine imposes no run-level bound.
- **`stream_sink`** (optional) — the run-scoped token-delivery callback. When the
  pipeline's terminal node is a trainable declaring `streamable = true` — the
  [streamable terminal-node placement rule](#R-pipeline-001-streamable-terminal-node)
  owns which node qualifies (trailing hooks and a terminal nested `pipeline` embed
  included) — the engine calls `stream_sink(fragment)` with each raw text fragment
  the backend emits, while that terminal dispatch is in flight. Fragments are **provisional transport** — the
  [no-mid-invocation-partial-values seal](#orchestration-scope) holds: the channel
  receives only the complete validated value, and the captured record is that same
  value; a run whose assembled value fails validation halts per fail-loud even
  though fragments were already delivered (acting on provisional fragments is
  consumer territory). Attaching a sink to a runnable with no streamable terminal
  raises `ContractViolation` — a sink that would silently never fire is refused at
  the boundary. A sink that itself **raises** during fragment delivery is an
  observation-plane failure, not a dispatch failure: the engine absorbs the raise,
  surfaces it on the runner's operational `conjured.runner` logger, and **detaches
  the sink** for the remainder of the dispatch — the dispatch completes (the
  assembled emission still validates, the channel is written, the captured record
  is intact, and the `RunResult` returns). Halting would spend a completed
  generation's clean captured record to deliver a message the operational log and
  the ended stream already deliver; fragments gate no value path, so the sink takes
  the same absorb-and-surface posture as the canonical event log's
  [producer/consumer wall](#canonical-event-log-consumer-isolation-wall) — the
  engine's one discipline for a raising observation-plane consumer — never the
  value path's halt. A detached sink's fragment stream
  simply ends, with no terminal signal — the authoritative value is the run's
  result, never the fragment stream (the seal above). Omitted, the buffered
  dispatch runs — behavior identical to a sink-less engine.

The call returns the run's [`RunResult`](#pipeline-result-runresult) — see
[§ Pipeline result](#pipeline-result-runresult) for its shape and the raise-on-halt
success contract. Each call
dispatches under a fresh per-run channel state scoped to that invocation's closure
([§ Kernel semantics](#kernel-semantics)); the engine accepts one
`(pipeline, inputs)` pair per invocation ([§ Orchestration scope](#orchestration-scope)).

---

{#pipeline-result-runresult}
## Pipeline result (RunResult)

A pipeline invocation returns one `RunResult` — the run's typed output. It carries
exactly two fields:

(pipeline-result-runresult-shape)=

| Field | Type | Carries |
|---|---|---|
| `state` | `Mapping[str, object]` | the run's final channel values — every **outer-pipeline** channel the graph wrote. A composition's *internal* [scoped channels](#scoped-channel) are not exposed here (encapsulation): a composition's contribution reaches `state` only through its declared outputs, flattened to outer channels. |
| `run_id` | `str` | the invocation identifier: the consumer's `pipeline_run_id` verbatim when one was supplied at invocation, else an engine-generated identifier in the structured, sortable form owned at [hash-model § canonical event types](#canonical-event-types) |

When the pipeline declares [`outputs`](#inputs-outputs-optional-api-boundary-declarations),
those fields are the consumer's committed happy-path surface (cross-checked at compose);
`state` returns them alongside every other channel the graph wrote.

`state` is the **written channels** — the run's final outer-channel values, a plain
mapping the consumer owns. The engine's contract is kept at return: the values it hands
back are what the graph produced; what the consumer does with the mapping afterward is the
consumer's own, not the engine's to police.

`run_id` makes the result self-identifying for cross-invocation observability: a consumer
threading a shared `pipeline_run_id` across related invocations (per
[orchestration scope](#orchestration-scope)) reads it back here to correlate the run.

**What RunResult deliberately does NOT carry.** It is not a status envelope. A run that
returns a `RunResult` succeeded — failure raises and the error channel halts the run, so
there is no result to return; hence no `success` / `ok` / `status` field, no error
context, and no partial `state`. It carries no `pipeline_hash` (that is a property of the
composition, read from the pipeline — not its output), and no timing / dispatch-trace /
snapshot / caller-echo / per-service-hash (run observability lives in the event stream,
not the result). There is no engine-defined `PipelineContext` or `State` type and no
context object: `state` is a plain `Mapping`, nothing a handler or consumer reaches into.

---

{#composition-validation}
## Composition validation

Compose time is the [load lifecycle](#pipeline-load-lifecycle) — the
moment the engine receives a pipeline declaration, before any run is in
flight. At this moment the engine reads the pipeline declaration, resolves
every `nodes`
entry (bare-function handler reference or composition declaration embed),
resolves every service-typed binding, and constructs the typed dataflow
graph. Every structural check the engine can perform statically fires here
and nowhere else. A pipeline that loads successfully is a pipeline whose
graph is internally consistent: every
channel type-checks across every node boundary, every binding resolves,
every channel with two or more contributors has a `merge.<channel>` declaration,
every deployment-declaration supply block is present and validated.

The engine raises `ContractViolation` — halting load entirely — when any
check fails. No node dispatches before load completes. No
`pipeline_error` event fires (the pipeline never started). No partial
state accumulates. For pipeline authors, a `ContractViolation` at load
is unambiguous: the error payload names the failing check via
`audit_code`, the offending artifact via `file_path` or
`composition_ref`, and the mismatched expectation via `expected` +
`actual`. There is no "load-with-warnings" mode; a pipeline that does
not type-check does not run.

The checks in R-pipeline-001 fall into three groups by what they need
to resolve:

**Registry resolution** — qualified names against
[handler resolution](#architecture-handler-resolution)
(dotted-path or `conjured.handlers` entry-points) for
`kind = "handler"` entries; composition declaration path resolution for
`kind = "composition"` entries (dispatching to the
appropriate per-kind specialization path based on the embedded
declaration's `meta.kind` discriminator); service-type qualified-name
resolution. These checks run first; shape-matching requires resolved
nodes and service types before cross-node comparison can proceed.

**Graph topology** — internal consistency of the typed dataflow graph
after names resolve. Channel types agree across node boundaries; all
bindings are fully supplied; channels with two or more contributors have explicit
`merge.<channel>` declarations per [R-pipeline-002](#pipeline-derived-rules);
the trainable-composition binding cardinality holds per
[R-handler-008](#R-handler-008); pipeline-level `inputs` and
`outputs` declarations are reachable through the node graph; the API
invocation path is set up to pre-validate incoming requests before first
node dispatch.

**Deployment coverage** — the engine reads the deployment declaration at
pipeline-declaration load and checks that every service-typed binding has
a `transport.<name>` block and every hook has a
`hook_transport."<as_written_node_name>"` block — the service block key-checked
against its `transport_schema` (presence + no unknown fields; values pass
through opaque save the reserved explicit-null form, per the deployment
reference's `transport.<name>`
contract), the hook block strict-validated against the hook's
`transport_schema` (hook transport IS engine-read). These checks close the silent-misconfiguration
category: hook operational errors are absorbed by the runner's hook
wrapper (per R-error-channel-003), making misconfigured hook transport
undetectable at dispatch time if compose-time validation did not fire.

Some sub-clauses have analogous engine-side structural counterparts
that fire inside the per-kind compose-time path (e.g., binding
cardinality per R-handler-008 expansion fires inside service-kind /
trainable-composition-kind construction; hook binding cardinality per
R-handler-009 fires inside hook construction). These sub-clauses appear
in R-pipeline-001 as part of the umbrella claim — the graph type-checks
at load — even where the implementing check runs inside a per-kind
engine-side path. See the full rule statement in
[§ Derived rules](#pipeline-derived-rules).

(composition-validation-error-reporting)=

**Error reporting — aggregate within a group, fail-fast across groups.** Within a
single check group the engine reports every independently-detectable failure, not
only the first — an author with three channel-type mismatches learns all three from
one load attempt, not one reload per error (the same multi-error posture
[`SchemaValidationError`](#schemavalidationerror)'s `field_validations` array takes
for runtime field failures). Across groups the order is fail-fast: a group that
wholly fails short-circuits the groups that depend on its results, because their
preconditions no longer hold — graph-topology checks cannot run against unresolved
nodes, so a registry-resolution failure halts before topology. The load thus surfaces
the complete set of errors it can detect without running a check a prior failure
invalidated.

---

{#hash-model-at-pipeline-grain}
## Hash model

The engine computes two sibling hashes over every composed pipeline. Each
answers a different structural question about the graph. The full
construction algorithm — how each hash is computed over the engine's
canonical intermediate representation — and the per-event canonical
payload spec are owned by [hash-model](#architecture-hash-model);
this section covers the pipeline-side mechanical justifications for what
each hash absorbs at the pipeline-component grain.

- **[Pipeline-hash](#pipeline-hash)** —
  identity of the full composition. Composes from the outer pipeline
  declaration's normalized hash plus per-composition-kind contribution
  for each embedded composition node: engine-owned-dispatch kinds like
  the trainable composition kind contribute their own normalized hash by
  reference; pure-substitution kinds like the bundle composition kind are
  substituted into the outer declaration before hashing, so their content
  folds into the outer pipeline-hash directly without a separate hash
  domain. Any composition change shifts it. Purpose: composition
  fingerprint, lineage identification, change detection.
- **[Training-bundle-hash](#training-bundle-hash)**
  (per trainable composition node) — identity of one trainable
  composition's declaration — the training-record-shape identity for
  the channels it emits. The construction formula and the per-member strip
  rules (the `annotations` and hook-`[[preprocessors]]` exclusions) are owned
  by [hash-model § Training-bundle-hash](#training-bundle-hash-construction).
  Purpose: LoRA format compatibility, training-record bucketing identity
  at the trainable-composition grain.

The pipeline-hash moves aggressively — any composition edit shifts it
(including any edit to an embedded composition declaration, via the
engine-owned-dispatch by-reference inclusion or the pure-substitution
inlined content). The training-bundle-hash moves at the
trainable-composition grain — it shifts when the trainable composition's
own declaration changes (its `trainable.config` /
`trainable.service_bindings` / `trainable.reads` /
`trainable.output_schema` / internal preprocessor handlers / scoped
channels). A fine-tuned artifact remains format-compatible with the
current pipeline as long as its training-bundle-hash matches at the
trainable composition node it serves.

{#pipeline-hash-at-pipeline-grain}
### Pipeline-hash

[Invariant I4](#invariants-and-derived-rules)
makes a mechanical promise: the training corpus is a derived view of the
graph — a corpus generated against a composition is valid for that
composition. Without a stable composition fingerprint, a deployed pipeline
has no way to detect that it is running a composition different from what
its trained artifacts were produced against when that drift does not change
any trainable composition node's training-bundle-hash. The pipeline-hash is
the fingerprint that makes such drift detectable at artifact-load time.

What the hash absorbs and excludes is not policy. Each inclusion follows
from "this input affects the graph at compose time"; each exclusion
follows from "this input does not affect the graph, or is per-environment
rather than per-composition." The absorb/exclude authority is
[hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs); this
section states the pipeline-grain projection and cites that list rather than
re-deriving the hash model.

**What the pipeline-hash absorbs at the pipeline grain:**

- **Outer pipeline declaration inputs** — `nodes` declaration order;
  per-node `bindings` values (inline or by external file path);
  per-node `reads_map` / `writes_map` wiring (the graph edges), absorbed as
  the normalized always-explicit IR so identity-sugar is hash-neutral — an
  omitted map and a written-out identity map produce the same pipeline-hash;
  pipeline-level `service_bindings.<name>` identity values;
  pipeline-level `merge.<channel>` declarations per
  [R-pipeline-002](#pipeline-derived-rules); qualified-name references to
  handlers / service-types / composition declarations.
- **Handler-declaration content** (for `kind = "handler"` entries;
  resolved via qualified-name) — each handler's declared `output_schema`,
  `bindings.<name>` schemas (including each binding's declared ship-time
  default, where one is declared), `service_bindings` declarations, validator
  configurations.
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
- **Embedded composition declarations** (for `kind = "composition"`
  entries) — contribution shape depends on composition kind:
  engine-owned-dispatch kinds (the trainable composition kind)
  contribute their own normalized hash by reference; pure-substitution
  kinds (the bundle composition kind) are substituted into the outer
  declaration before hashing, so their normalized content folds into the
  outer pipeline-hash directly. For engine-owned-dispatch kinds, the
  embedded declaration's internal scope is opaque to the outer hash; only
  its overall identity hash flows up.

**What is excluded.** The complete excluded-field list is owned by
[hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs); this
section states the rule and cites it rather than re-deriving the hash model. Two
consequences a pipeline author relies on: adding, removing, or reordering **hooks**
does NOT shift the pipeline-hash (hooks write to no channels — composition-visible
but not training-contract participants, so an observation-surface change is not a
contractual one), and moving a composition between environments shifts neither hash
(deployment-supplied `transport.*` / `training_export` / `training_contract` /
`acknowledged_drift` and metadata-class `annotations` are per-environment or
metadata, never per-composition).

{#training-bundle-hash-at-pipeline-grain}
### Training-bundle-hash

**What the [training-bundle-hash](#training-bundle-hash) absorbs at the pipeline grain.** For each trainable
composition node in the pipeline (an embedded composition declaration with
`meta.kind = "trainable"`), the engine computes the training-bundle-hash over the
trainable composition declaration's normalized structural content per
[hash-model § Training-bundle-hash](#training-bundle-hash-construction), which owns the
absorbed-member list and the per-member strip rules (the identity/metadata-class
exclusions, the hook-stripped `[[preprocessors]]` order). The pipeline-grain
distinction: a preprocessor *inside* the trainable composition's scope contributes to
the training-bundle-hash, while a postprocessor *outside* it (in the outer pipeline)
contributes only to the pipeline-hash.

The two hashes stand in a **one-way relationship** — same pipeline-hash →
same training-bundle-hash for every trainable composition node, never the
converse — because the outer hash absorbs the embedded trainable composition
declarations by reference. The full bucketing treatment — the direction and
which edits move which hash — is owned by
[hash-model § Bucketing semantics](#bucketing-semantics-pipeline-hash-vs-training-bundle-hash).

---

{#pipeline-derivables-bundle}
## Pipeline derivables

The [pipeline derivables](#pipeline-derivables) bundle — the compose-time, pure-read
extract that feeds training-data generation — contains exactly the components below,
wrapped by the format and provenance members of the
[bundle serialized form](#derivables-bundle-serialized-form).

**Schema definitions + training-bundle-hashes.** For each
[trainable](#trainable) composition node in
the pipeline: the trainable composition's
[training-bundle-hash](#training-bundle-hash)
and the full shape definition of its declared
[trainable channels](#trainable-channel) — the
input-payload shape the external generator must produce (the trainable
composition's `trainable.reads`), the output-payload shape the generator
LLM must emit (the trainable composition's `trainable.output_schema`),
and the service-type metadata identifying the backend. The schema
definition is the complete specification a generator needs to emit
conformant training pairs; the training-bundle-hash is the binding
identifier that ties generated pairs to the trainable composition
declaration they were produced for.

**Pipeline-fixed binding snapshot.** Resolved `bindings.<name>` values
(with pipeline-level overrides applied), all referenced external
binding-value declarations, and service-binding identity values from
`service_bindings` entries. These tell the external generator *what to
generate about* — which characters, which scenes, which prompt
conventions are in scope. Binding context enters the generator prompt as
scoping input, constraining the generator's output to the specific
composition the author built.

**Service metadata.** Per trainable composition node: the bound
trainable backend's adapter contract — input type, output type — and
the service type's `description` string. The description reaches the
external generator as instruction context for each training pair: what
the backend is for, what characterizes a useful input-output pair from
the backend's perspective. The description folds into neither
structural hash ([hash-model § What the pipeline-hash
absorbs](#what-the-pipeline-hash-absorbs) owns the exclusion); its
integrity pin is the provenance layer — the manifest's
[`derivables_bundle_hash`](#generatorinfo) records the exact bundle the
generator consumed.

**Pipeline composition snapshot.** `nodes` list, node order, and
inter-node relationships — the same composition the
[pipeline-hash](#pipeline-hash) covers.
Included for reproducibility: the `pipeline_hash` recorded in a trained
artifact's manifest corresponds to the composition captured here. A
consumer extracting derivables from the same pipeline declaration before
and after a composition edit receives different composition snapshots
and, when the edit affected any trainable composition declaration,
different training-bundle-hashes.

{#derivables-bundle-serialized-form}
### Bundle serialized form

The bundle serializes as **one JSON object** (UTF-8). Serialization is
**deterministic** — object keys sort lexicographically, and the same
declaration set extracted by the same engine version produces a
byte-identical artifact. That determinism is what makes "the same
derivables bundle" in the manifest's
[`generator_prompt_hash`](#generatorinfo) reproducibility anchor a
well-defined comparison rather than a judgment call.

Top-level members (all always present):

| Key | Content |
|---|---|
| `bundle_format` | Integer version of this envelope; this section specifies format `1`. A consumer MUST reject an unrecognized value rather than guess at the shape. |
| `pipeline_hash` | The [pipeline-hash](#pipeline-hash) of the composition the bundle derives from. |
| `conjured_version` | The engine version that performed the extraction — provenance for the artifact. |
| `trainables` | One entry per trainable composition node in the pipeline, keyed by the trainable composition's declared `meta` name — the same key the trained-artifact manifest's `training_bundle_hashes` field uses (below), so a bundle entry and its manifest hash correlate without translation. Each entry carries exactly `training_bundle_hash`, `reads`, `output_schema`, and `service_metadata` (`service_type` + `description`) — the two per-trainable components above. The adapter contract's input and output types ARE the `reads` / `output_schema` shapes, carried once at the entry level and never restated inside `service_metadata`. A nested `pipeline` embed's inner trainables are the inner pipeline's own derivables concern and do not appear in the outer bundle. |
| `binding_snapshot` | The pipeline-fixed binding snapshot component above. |
| `composition_snapshot` | The pipeline composition snapshot component above. |

The member keys map onto the bundle's components; the components'
*content* is owned by the component paragraphs above, which this table
does not restate. Within `binding_snapshot` and `composition_snapshot`
the internal layout is engine-defined under the `bundle_format` version —
canon pins their *content* (the component paragraphs above) and their
scope (the pipeline-hash's non-hook domain); a layout change is a
`bundle_format` bump, never a silent reshape.

{#extraction-surface}
### Extraction surface

The engine exposes a derivables-extraction surface — the
`conjured derivables` CLI subcommand and the `conjured.derivables`
library entry point — that accepts a pipeline declaration and the
paths of its referenced handler declarations, embedded composition
declarations, external binding-value declarations, and service-type
declarations. The tool reads the declared composition and emits the
bundle as a single serialized artifact in the
[bundle serialized form](#derivables-bundle-serialized-form). No
service invocations occur; no handlers dispatch. The extraction call
is compose-time: it reads declared structure only.

External generator tooling consumes the bundle directly. The consumer
does not hand-assemble the bundle's components.

{#training-data-generation-arc}
### Training-data generation arc

The standard arc once a pipeline is composed:

1. Author extracts the pipeline derivables bundle via the extraction
   tool.
2. Bundle is supplied to an external generator LLM (e.g., Claude, GPT)
   with instructions to emit N training pairs conforming to the derived
   schema definitions, scoped to the derived binding snapshot.
3. Generator emits pairs; consumer accumulates the training corpus.
4. Consumer fine-tunes against the corpus with external tooling
   (axolotl, unsloth, trl, or backend-native training pipelines).
5. Trained artifact ships with a sidecar manifest recording the
   training-bundle-hashes (per trainable composition node) and
   pipeline-hash at training time.

**Pipeline-run capture** is the alternative corpus-source path —
training pairs assembled from live pipeline-run canonical events rather
than from external-generator emission. Which event carries the training
record is **keyed by node kind**, owned by hash-model:

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

For a trainable composition node the captured `handler_enter` +
`handler_exit` pair IS the training record (its input/output snapshot
sides and pairing are owned at
[hash-model § Paired-event structure (trainable composition kind)](#paired-event-structure-trainable-composition-kind)).
Captured events are recorded in the manifest under
`training_data_source = "captured"` (see
[§ training_data_source enum](#trainingdatasource-enum) below). Captured
corpora are rarely suitable as the primary signal; signal-to-noise is
typically low and filtering work often exceeds the cost of fresh
generation.

---

{#trained-artifact-manifest-sidecar}
## Trained-artifact manifest

Every trained artifact ships with its
[trained-artifact manifest](#trained-artifact-manifest) — a **sidecar manifest TOML**
adjacent to the artifact file. Convention: an artifact at
`loras/my_npc.safetensors` ships alongside
`loras/my_npc.safetensors.conjured.toml`. The consumer writes the sidecar
post-training with `conjured artifact-tag` and renames the pair with
`conjured artifact-mv` (the [manifest CLI pair](#manifest-cli-pair) below owns both
tools' grammar); the engine only ever READS the sidecar — at deployment load, for
each artifact the deployment's `[artifacts]` table registers (the deployment
reference's § `artifacts` owns that surface). A registered artifact whose sidecar is
missing halts with [ContractViolation](#contractviolation) under
`integrity_enforcement = true`; with enforcement off it is the no-baseline case (no
comparison, no event) — hash-model's enforcement modes own the split.

The hash-bearing fields (`pipeline_hash_set` and the
`training_bundle_hashes` table) are specified at
[hash-model § Trained-artifact manifest](#trained-artifact-manifest-as-view).
This section is the authoritative full-field specification.

{#full-manifest-field-set}
### Full manifest field set

| Field | TOML location | Required | Notes |
|---|---|---|---|
| `artifact` | `manifest` | Required | Relative path to the artifact file this manifest accompanies. |
| `pipeline_hash_set` | `manifest` | Required | List of [pipeline-hashes](#pipeline-hash) the training corpus came from. Single-element for single-pipeline corpora; multi-element when the corpus spans multiple compositions sharing the same training-bundle-hashes. Load-time check is set-membership, not equality. |
| `base_model` | `manifest` | Required | Base model the artifact targets (e.g., `"qwen3.5-4b"`). **Provenance record only** (informational, like `trained_at`) — not used in any check. A deployed-model change is already a hashed-identity change the pipeline-hash / training-bundle-hash machinery surfaces, so there is no separate `base_model` mismatch check. |
| `artifact_format` | `manifest` | Required | Serialization format — an **open** provenance string (the engine reads it, does not validate against a closed set); examples: `"safetensors"`, `"backend_native"`. |
| `trained_at` | `manifest` | Required | ISO 8601 timestamp of when the training run completed. Traceability and audit-log field; not used in hash or drift machinery. |
| `training_data_source` | `manifest` | Required | Closed enum. See [§ training_data_source enum](#trainingdatasource-enum). |
| `generator_info` | `manifest` | Conditional | Present when `training_data_source` includes `"generated"`. See [§ generator_info](#generatorinfo). |
| `training_bundle_hashes` | Top-level table | Required | Per-trainable-composition-node [training-bundle-hashes](#training-bundle-hash), keyed by the trainable composition's declared `meta` name (`<trainable_composition_name>`, unique within the embedding pipeline). Full specification at [hash-model § manifest-key shape](#manifest-key-shape). |

{#pipeline-hash-set-width}
#### `pipeline_hash_set` width — authoring guidance

A multi-element `pipeline_hash_set` (a corpus spanning several compositions) is ambiguous
between two cases the manifest cannot tell apart structurally: **(a)** a genuinely
composition-agnostic artifact with *intentional* wide validity, and **(b)** a schema too
permissive — silently spanning variants the consumer treats as distinct. The disambiguation
is a question only the author can answer: **does the consumer treat the spanned compositions
as semantically equivalent?** Yes → case (a), the wide scope is intended. No → case (b): split
into separate service schemas with separate qualified names, each carrying its own narrower
`pipeline_hash_set`. A wide set is a deliberate authoring decision, never a default.

{#trainingdatasource-enum}
#### `training_data_source` enum

Closed enum recording how the training corpus was produced:

| Value | Meaning |
|---|---|
| `"generated"` | Training pairs generated by an external generator LLM consuming the [pipeline derivables](#pipeline-derivables-bundle) bundle. The standard path. |
| `"captured"` | Training pairs from pipeline-run capture — `handler_enter` + `handler_exit` event pairs for trainable-composition dispatches (the pair IS the captured training record — see [hash-model § Paired-event structure (trainable composition kind)](#paired-event-structure-trainable-composition-kind)), routed to file via the `training_export` block in the deployment declaration. Supplementary signal; rarely the primary corpus. |
| `"external"` | Training pairs supplied directly by the producer — hand-authored, or imported from an external dataset — not produced by the engine's generation or capture paths. |
| `"mixed"` | Training corpus is a union of two or more of the above sources. |

The value is producer-declared at training time; the engine reads it as
provenance and does not independently verify corpus origin.

{#generatorinfo}
#### `generator_info`

Present when the corpus includes a generated source (`training_data_source` is
`"generated"`, or `"mixed"` spanning generated pairs). Inline
object with four fields:

| Sub-field | Description |
|---|---|
| `generator_id` | External-generator identifier (e.g., `"claude-opus-4-7"`, `"gpt-4o"`). |
| `generator_prompt_hash` | SHA-256 hash of the generator prompt used to produce the pairs. Reproducibility anchor: re-running with the same prompt and same derivables bundle should yield an equivalent corpus. |
| `derivables_bundle_hash` | SHA-256 (`sha256:<hex>`) over the serialized [pipeline derivables](#pipeline-derivables-bundle) bundle artifact the generator consumed — byte-exact over the deterministic [bundle serialized form](#derivables-bundle-serialized-form) (the `conjured derivables` CLI reports it at extraction; the library entry point exposes the hash helper). Makes `generator_prompt_hash`'s "same derivables bundle" anchor a checkable hash equality; the provenance pin for generation-time conditioning inputs the structural hashes exclude ([hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs) owns the exclusion list). |
| `generation_params` | Free-form object recording generation parameters that materially affect corpus distribution (temperature, top-p, N pairs requested, sampling constraints). |

`generator_info` is consumer-authored via `conjured artifact-tag`; the
engine reads it as a provenance record and does not validate its internal
field shape beyond TOML parsing.

{#manifest-cli-pair}
#### The manifest CLI pair — `conjured artifact-tag` / `conjured artifact-mv`

The consumer-side authoring tools for the sidecar (the engine only reads it). Both are
subcommands of the umbrella `conjured` console script, beside `conjured derivables`.

```
conjured artifact-tag <artifact>
    --pipeline <path>
    [--handler NAME=PATH]... [--composition NODEPATH=PATH]... [--service-type PATH]...
    --base-model <s> --artifact-format <s>
    --training-data-source generated|captured|external|mixed
    [--trained-at <ISO-8601>]
    [--generator-id <s> --generator-prompt-hash <hex>
     --derivables-bundle-hash <sha256:hex> --generation-params <json>]
    [--force]
```

`artifact-tag` assembles the declaration registry exactly as `conjured derivables` does
(the same `NAME=PATH` flag conventions — [§ Extraction surface](#extraction-surface)),
computes the composition's current [pipeline-hash](#pipeline-hash) and per-trainable
[training-bundle-hashes](#training-bundle-hash), and writes `<artifact>.conjured.toml`
carrying the [full manifest field set](#full-manifest-field-set): `pipeline_hash_set` as
the one-element set of the computed hash, `training_bundle_hashes` as the per-trainable
table, `trained_at` defaulting to the invocation time (UTC). The blessed flow is
tag-immediately-after-training against the declarations the corpus was trained on — the
computed hashes then ARE the training-time hashes; a multi-composition
`pipeline_hash_set` is authored by editing the sidecar
([§ `pipeline_hash_set` width](#pipeline-hash-set-width) owns the guidance). The
`generator_info` flag group is required iff `--training-data-source` is `generated` or
`mixed` and rejected otherwise ([§ generator_info](#generatorinfo)). An existing sidecar
is never silently overwritten — re-tagging requires `--force`.

```
conjured artifact-mv <src> <dst>
```

`artifact-mv` renames the artifact file AND its sidecar as one pair, rewriting the
manifest's `artifact` field to the destination path — the file pair never desyncs. A
`<src>` with no sidecar fails loud (nothing to keep in sync); an existing destination
file or sidecar is never silently overwritten.

{#load-behavior}
### Load behavior

At deployment, the engine loads the sidecar manifest of each artifact the
deployment's `[artifacts]` table registers (the deployment reference's
§ `artifacts` owns the registration surface) and compares its
hash fields against the pipeline's current pipeline-hash and
per-trainable-composition training-bundle-hashes. Whether a mismatch
halts load or
only fires canonical events depends on the deployment's
[integrity enforcement](#integrity-enforcement)
opt-in. See
[hash-model § integrity-enforcement opt-in](#integrity-enforcement-opt-in)
for the graduated-force logic — per-class mismatch behavior,
acknowledgment mechanics, and the silent-load condition. That page is
the authoritative treatment; this section does not restate it.

---

{#pipeline-derived-rules}
## Derived rules

Every derived rule that governs this component lives here. The rules cite the invariant(s) or
tenet(s) they protect from [principles](#invariants-and-derived-rules) via
`derived_from`; they declare an `enforcement` mode per
[enforcement-modes](#architecture-enforcement-modes).

```yaml
rules:
  - rule_id: R-pipeline-001
    name: compose-time composition validation
    derived_from: [I2]
    enforcement: mechanical
    statement: |
      The typed dataflow graph type-checks at pipeline-declaration load —
      the moment the engine receives the pipeline declaration. Every structural
      check the engine can perform statically fires here; none fires
      after the first node dispatches. A pipeline that loads is a
      pipeline whose graph is internally consistent. All checks below
      raise ContractViolation on failure before any node dispatches.

      **Node-name resolution.** Every `nodes` entry's `name` must
      resolve at pipeline-declaration load:
      - For `kind = "handler"`: via [handler resolution](#architecture-handler-resolution)
        (dotted-path or the `conjured.handlers` entry-points group) +
        R-handler-pure-module source-AST audit + R-handler-bare-function
        function-shape check (vector-2 seal).
      - For `kind = "composition"`: the path resolves to a readable
        composition declaration with `meta.kind` in the closed-enum
        composition-kind set (the handler reference's composition-kind grammar
        owns the members); the
        embedded declaration's grammar is validated and dispatched through
        the per-kind specialization path identified by the discriminator.

      Unresolvable references raise ContractViolation.

      **Service-type resolution.** Every `service_bindings.<name>.type`
      identifier in the pipeline declaration must resolve to a registered
      service type. Resolution is strict qualified-name equality — no
      subtyping, inheritance, or capability-matching.

      **Read/write shape matching.** The check runs in two stages over
      the normalized wiring IR. STAGE 1 — resolve every port to its
      channel through the read-map / write-map: after the compose-time
      desugar, every input and output port (including identity-sugared
      ones) carries an explicit `(port, channel, declared-type)` triple.
      STAGE 2 — exact-equality on channels (mirroring service-type
      resolution's strict qualified-name equality):
      For each channel, collect every port (read or write, across all nodes) wired to it; every such
      port MUST declare the **same declared type** — exact equality, no subtype widening, inheritance,
      or capability-matching. A mismatch raises ContractViolation with a shape-diff report naming the
      resolved channel, the ports wired to it, and the divergent types.
      The join key is the same resolved channel, not the field name;
      "upstream" is the final post-compose dispatch order. (For embedded
      composition declarations, the embedded declaration's `inputs` /
      `outputs` participate after flatten.)

      **Channel-write disjointness.** Per
      [R-pipeline-002](#pipeline-derived-rules), a channel with two or more
      contributors — its seed (if a declared input) plus its node writes,
      in graph order — MUST have an explicit `merge.<channel>`
      declaration naming a strategy from the closed registry; two or more
      contributors without a declaration raise ContractViolation. The runner folds
      contributors under the declared strategy **inline**, in graph order (a
      runner operation). Merge
      declarations are scoped to their composition declaration
      (cross-scope merges are structurally impossible under scoped
      channels).

      **Single-assignment (read/write disjointness).** No node wires a
      read-port and an output-port to the same channel. A node's read-map
      (input-port → channel) and write-map (output-port → channel) target
      disjoint channel sets; overlap raises ContractViolation at the single
      normalization step. A channel is produced by its writer(s) and
      consumed by its readers, never read-then-rewritten by one node.
      Because a handler is a channel-agnostic pure function — the runner
      constructs its input from the read-map before the call and routes its
      return via the write-map after — the body cannot reference a channel,
      so read-then-rewrite is impossible by construction; the residual
      compose-time check is the set-disjointness test over the two
      normalized maps. To transform a value, write a new channel; to combine
      independent contributors, declare a fan-in `merge.<channel>`. This is the
      structural form of the kernel single-assignment property — it keeps a
      channel's value free of in-place mutation across the run, so each
      merge strategy is a pure reducer over independently-produced contributors.

      **Binding supply matching.**
      For each `nodes` entry: **service-typed bindings** — the pipeline declaration must supply a
      matching `service_bindings.<name>` block whose `type` equals the handler's declared type (strict
      qualified-name equality) and whose body includes every field declared in the resolved service
      type's `identity_schema`. **Compose-time bindings** — every handler `bindings.<name>` entry
      that declares a value schema and no ship-time default must be supplied by the pipeline entry's
      `bindings` value block. A binding that declares a ship-time default MAY be omitted (the engine
      supplies the declared default) or overridden; a default-less binding left unsupplied raises
      ContractViolation. A compile-directive binding (`compile = "..."`) is engine-owned: the engine
      produces its value by running the named compiler at binding resolution, so it carries no node
      supply — it is neither subject to the must-supply rule nor omittable, the node contributes nothing
      to it. Orphaned supply entries — `service_bindings.<name>` blocks or `bindings` keys the pipeline
      supplies but no node in the composition declares — also raise ContractViolation. A `bindings.<name>`
      value supplied for a compile-directive binding likewise raises ContractViolation: the engine
      produces that binding's value, so a node supply for it is meaningless and is rejected at compose
      rather than silently absorbed.
      A supplied compose-time binding value takes any form the handler
      reference's § Binding value-supply grammar admits, matching the
      declared binding schema; the trainable composition kind tightens the
      binding cardinality further per [R-handler-008](#R-handler-008).

      **Identity/transport placement.**
      Every field in a pipeline-level `service_bindings.<name>` block beyond `type` and the reserved
      `config` sub-block must be declared in the resolved service type's `identity_schema`. The
      `config` block is the binding's generation-parameter supply — its keys resolve against the
      service type's `[config_schema]`, not `identity_schema` (the service-type reference's § The
      `[config_schema]` contract owns that check). Cross-block misplacement raises ContractViolation
      naming the offending field and its correct location.
      Every field in a deployment `transport.<name>` block must be declared in the resolved service
      type's `transport_schema`. Cross-block misplacement raises ContractViolation naming the offending
      field and its correct location.

      **Transport coverage.**
      Every service-typed binding the engine composes must have a corresponding `transport.<name>`
      block in the deployment declaration — a missing block raises ContractViolation. That covers
      the pipeline's own `service_bindings.<name>` blocks AND
      every `[service_bindings.<name>]` a trainable composition declaration supplies (its terminal
      `trainable.service_bindings` backend and any preprocessor service binding), joined on the
      binding handle under the deployment reference's shared-by-binding-name resolution. The join
      is type-coherent: every binding sharing one handle within a composing pipeline's scope MUST
      resolve the same service-type — differing service-types under one shared handle raise
      ContractViolation at compose (one covering block cannot satisfy two `transport_schema`s).
      Coverage
      follows what the engine composes, never the consumer: service use outside the composed
      pipeline is consumer territory and needs no block. The covering block must carry **every
      declared `transport_schema` field and no undeclared field**. Presence-coverage is **uniform**
      over the declared field set: a nullable-declared field is satisfied by a supplied value OR by
      the [explicit null](#binding-value-supply-grammar-explicit-null) `{ null = true }`; **absence
      of any declared field raises ContractViolation** — nullable grants no presence exemption,
      because a considered-and-null value is spelled while an absent field is indistinguishable from
      forgot ([exhaustive declaration](#architecture-exhaustive-declaration)'s empty-but-present
      principle, held at the field level). An unknown field raises ContractViolation. The check is
      key-set plus reserved-form recognition only: the declared fields' VALUES pass through opaque
      (the `**transport_extra` passthrough the deployment reference's `transport.<name>` contract
      owns), the engine reading exactly two reserved value shapes — the explicit-null form, which
      normalizes to null before delivery, and, on a `secret_ref`-declared field, the
      secret-reference shape whose grammar/scheme/resolver check
      [R-deployment-003](#R-deployment-003) owns (cited, not restated). Every other value passes
      through unread.

      **`inputs` / `outputs` field resolution.**
      `inputs` / `outputs` field names are *channel* names (the pipeline's API surface). For each field
      declared in the pipeline's `inputs` block: at least one node's read-map must route a port to that
      channel. For each field declared in `outputs`: at least one node's write-map must route a port to
      that channel. A declared input field no node reads is a dead declaration; a declared output field
      no node writes cannot surface. Both raise ContractViolation.

      **API-invocation `inputs` enforcement.**
      When a pipeline declares `inputs`, the API invocation path validates the incoming request's
      key-set against the declared input fields before dispatching the first node — presence of
      every declared field, never values.
      Missing field: ContractViolation at the API boundary — no node dispatches; no `pipeline_error`
      event fires because the pipeline never started. An incoming key that is not a declared input
      field is **not admitted but not an error**: the runner seeds only the declared input channels,
      so an extra never becomes a channel and never reaches any handler. The missing-field
      ContractViolation's message names any unrecognized keys present in the request — so a typo'd
      key surfaces in the same error as the declared field it failed to supply. A declared input
      field supplied with a type- or constraint-violating value passes the API boundary and surfaces
      as SchemaValidationError at the seeded channel's first consumer — its reads-projection or its
      merge fold, whichever the runner dispatches first (per R-error-channel-001's key-set routing;
      the `inputs` / `outputs` field-resolution clause guarantees at least one reading node exists,
      so a consumer always exists).
      Every otherwise-unwritten read-port channel must be covered by an `inputs` declaration: an
      unmapped read-port whose same-named channel is neither written by an upstream node's resolved
      write-map nor declared in `inputs` is a dangling input port and raises ContractViolation at the
      single compose-time normalization step — even when the pipeline declares no `inputs` block. This
      closes the API-input set so a typo'd read-port fails loud rather than binding silently to a value
      the caller never sends.

      **Hook transport coverage.**
      Every hook in the pipeline's `nodes` list must have a corresponding
      `hook_transport."<as_written_node_name>"` block in the deployment declaration. The block is
      strict-validated against the hook's `transport_schema`: every declared field must be present, no
      unknown fields are accepted, declared types must match. Presence-coverage is uniform here
      exactly as for service-binding transport: a nullable-declared hook transport field is satisfied
      by a supplied value or by the
      [explicit null](#binding-value-supply-grammar-explicit-null) `{ null = true }` (delivered to
      the hook body as Python `None`); absence of any declared field raises ContractViolation. A hook
      whose `transport_schema` declares
      zero fields requires an empty-but-present block — absent raises ContractViolation; empty-but-present
      does not.

      **Streamable terminal-node placement.**
      A trainable composition whose `[trainable]` node declares `streamable = true` MUST be the
      pipeline's terminal node — only hooks (which write no channels) may follow it. Any non-hook node
      downstream of a streamable trainable raises ContractViolation. Terminal position is evaluated
      **transitively through a terminal nested `pipeline` embed**: a streamable trainable that is
      terminal inside a nested `pipeline` which is itself the enclosing pipeline's terminal node
      satisfies the rule, and any non-hook node downstream of it — at any nesting layer — raises
      ContractViolation.

      Other sub-clauses have analogous engine-side structural
      counterparts that fire inside the per-kind compose-time path:
      binding cardinality per R-handler-008 expansion fires inside
      service-kind / trainable-composition-kind construction; hook
      binding cardinality per R-handler-009 fires inside hook
      construction. These sub-clauses appear in R-pipeline-001 as part
      of the umbrella claim — the graph type-checks at load — even
      where the implementing check runs inside a per-kind engine-side
      path.

  - rule_id: R-pipeline-002
    name: channel-write disjointness with merge opt-in
    derived_from: [I1, I2]
    enforcement: mechanical
    statement: |
      A channel's **contributors** are its seed (if the channel is a
      declared input) plus its node writes, in graph order. A channel
      MAY have two or more contributors **iff** the pipeline
      declaration's `merge` block declares a merge strategy for that
      channel from the engine's closed registry of merge operations.
      Without an explicit `merge.<channel>` declaration, a channel with
      two or more contributors is rejected at compose time with
      [ContractViolation](#contractviolation).

      **Closed registry of merge strategies.** The
      [§ `merge.<channel>` grammar](#mergechannel-channel-write-disjointness-opt-in)
      table is the owning catalog. Each strategy carries a type constraint the engine validates
      against the merged channel's declared type at compose time. The
      registry is closed-enum; new strategies land by an engine change.
      Fan-in the closed registry cannot express is served by
      [the aggregator pattern](#the-aggregator-pattern) — an
      author-written transform, not a shipped handler.

      **Compose-time engine validation.** The per-entry compose-time
      checks are owned by the
      [§ `merge.<channel>` grammar](#mergechannel-channel-write-disjointness-opt-in)
      section — its *Compose-time engine validation* list (contributor-count
      coverage, wired-channel existence, registry membership) together with
      the non-optional-type rule above it — cited here rather than restated,
      as the registry table is.

      **Runtime — an inline runner operation.** A merged channel's value
      is built incrementally: as the runner (the sole channel writer)
      walks graph order, it folds each contributor into the channel's
      current value under the named strategy — a left-fold over the
      contributors in graph order, the seed the fold's first element. A
      reader's projection is the strategy's left-fold over the
      contributors upstream of its position; the final value is the fold
      over all contributors. Each writing node's own `handler_exit`
      records its contribution, and a seed is the invocation's supplied
      input value, so the channel's state at any position is
      reconstructable as the declared (hashed) strategy's fold over the
      contributors upstream of it.

      **Scoping.** `merge.<channel>` declarations are
      scoped to their composition declaration. An outer pipeline's
      `merge` block cannot reach into an embedded composition
      declaration's internal [scoped channels](#scoped-channel)
      and vice versa — cross-scope merges are structurally impossible.
      A trainable composition declaration that internally has a
      fan-in channel (two or more contributors) declares its
      own `merge` within the trainable composition declaration; the
      outer pipeline's `merge` covers only the outer pipeline's
      channels.

      **Load-bearing for I1 (no implicit contracts).** Every contributor
      is explicitly declared — a node writes a channel only if its
      write-map explicitly routes an output port to it, and a seed
      exists only for a declared input — so a deliberate fan-in (two or
      more contributors to one channel) requires an explicit strategy
      declaration; absent one, the engine cannot know which reducer the
      author intends and raises ContractViolation. Closed-registry merge
      declarations make multi-contributor channels a structurally explicit
      case with mechanical type-checked resolution — exactly the kind of
      implicit contract I1 forbids made explicit.

  - rule_id: R-pipeline-003
    name: trained-artifact integrity enforcement
    derived_from: [I4]
    enforcement: mechanical
    statement: |
      For each artifact the deployment's `[artifacts]` table registers
      (the deployment reference's § `artifacts` owns the registration
      surface), the engine loads the sidecar trained-artifact manifest at
      deployment load, recomputes the current pipeline-hash and
      per-trainable training-bundle-hashes, and compares them against the
      manifest's recorded values. The drift events
      (`training_bundle_hash_changed` / `pipeline_hash_changed`) fire on
      every mismatch under either enforcement mode — the integrity
      property is always available; halts are gated on the deployment's
      `training_contract` `integrity_enforcement` opt-in and graduated
      per hash class. The graduated-force logic, the acknowledgment
      mechanics, and the missing-manifest enforcement split are owned by
      hash-model's § Integrity-enforcement opt-in, cited here rather
      than restated.

      Two shapes fail loud under either enforcement mode: a **malformed
      or unreadable** sidecar raises ContractViolation (a corrupt
      engine-read artifact is never coerced to absent — the same posture
      as the audit-stamp artifact), and a registered trainable name that
      matches **no trainable composition node** in the deployed pipeline
      raises ContractViolation (a registration that can never be
      compared is a wiring mistake, not a no-op).
```

{#pipeline-rule-fragments}
## Rule fragments

Single-source definitions of the R-pipeline-001 / R-pipeline-002 sub-clauses other docs depend on.
The convention is the shared rule-fragments kernel (owned at the handler reference):

Each fragment's canonical
text lives here once; the docs that depend on a fragment render it inline by transclusion, so a
dependent doc can never drift from the rule it relies on. Where a derived-rule statement above
carries an owner fragment's exact text, it transcludes that fragment rather than restating it, so
the render mechanism — not hand-maintenance — keeps the statement and the fragment identical. Where
a statement instead states the rule in its own words — a deliberate compression, or a restatement
that carries more than the fragment — the fragment remains the owner wherever the two differ, an
agreement verified by review.

(R-pipeline-001-readwrite-shape-matching)=

For each channel, collect every port (read or write, across all nodes) wired to it; every such
port MUST declare the **same declared type** — exact equality, no subtype widening, inheritance,
or capability-matching. A mismatch raises ContractViolation with a shape-diff report naming the
resolved channel, the ports wired to it, and the divergent types.

(R-pipeline-001-input-closure)=

Every otherwise-unwritten read-port channel must be covered by an `inputs` declaration: an
unmapped read-port whose same-named channel is neither written by an upstream node's resolved
write-map nor declared in `inputs` is a dangling input port and raises ContractViolation at the
single compose-time normalization step — even when the pipeline declares no `inputs` block. This
closes the API-input set so a typo'd read-port fails loud rather than binding silently to a value
the caller never sends.

(R-pipeline-001-binding-supply-matching)=

For each `nodes` entry: **service-typed bindings** — the pipeline declaration must supply a
matching `service_bindings.<name>` block whose `type` equals the handler's declared type (strict
qualified-name equality) and whose body includes every field declared in the resolved service
type's `identity_schema`. **Compose-time bindings** — every handler `bindings.<name>` entry
that declares a value schema and no ship-time default must be supplied by the pipeline entry's
`bindings` value block. A binding that declares a ship-time default MAY be omitted (the engine
supplies the declared default) or overridden; a default-less binding left unsupplied raises
ContractViolation. A compile-directive binding (`compile = "..."`) is engine-owned: the engine
produces its value by running the named compiler at binding resolution, so it carries no node
supply — it is neither subject to the must-supply rule nor omittable, the node contributes nothing
to it. Orphaned supply entries — `service_bindings.<name>` blocks or `bindings` keys the pipeline
supplies but no node in the composition declares — also raise ContractViolation. A `bindings.<name>`
value supplied for a compile-directive binding likewise raises ContractViolation: the engine
produces that binding's value, so a node supply for it is meaningless and is rejected at compose
rather than silently absorbed.

(R-pipeline-001-identity-placement)=

Every field in a pipeline-level `service_bindings.<name>` block beyond `type` and the reserved
`config` sub-block must be declared in the resolved service type's `identity_schema`. The
`config` block is the binding's generation-parameter supply — its keys resolve against the
service type's `[config_schema]`, not `identity_schema` (the service-type reference's § The
`[config_schema]` contract owns that check). Cross-block misplacement raises ContractViolation
naming the offending field and its correct location.

(R-pipeline-001-transport-placement)=

Every field in a deployment `transport.<name>` block must be declared in the resolved service
type's `transport_schema`. Cross-block misplacement raises ContractViolation naming the offending
field and its correct location.

(R-pipeline-001-inputs-outputs-resolution)=

`inputs` / `outputs` field names are *channel* names (the pipeline's API surface). For each field
declared in the pipeline's `inputs` block: at least one node's read-map must route a port to that
channel. For each field declared in `outputs`: at least one node's write-map must route a port to
that channel. A declared input field no node reads is a dead declaration; a declared output field
no node writes cannot surface. Both raise ContractViolation.

(R-pipeline-001-api-inputs-enforcement)=

When a pipeline declares `inputs`, the API invocation path validates the incoming request's
key-set against the declared input fields before dispatching the first node — presence of
every declared field, never values.
Missing field: ContractViolation at the API boundary — no node dispatches; no `pipeline_error`
event fires because the pipeline never started. An incoming key that is not a declared input
field is **not admitted but not an error**: the runner seeds only the declared input channels,
so an extra never becomes a channel and never reaches any handler. The missing-field
ContractViolation's message names any unrecognized keys present in the request — so a typo'd
key surfaces in the same error as the declared field it failed to supply. A declared input
field supplied with a type- or constraint-violating value passes the API boundary and surfaces
as SchemaValidationError at the seeded channel's first consumer — its reads-projection or its
merge fold, whichever the runner dispatches first (per R-error-channel-001's key-set routing;
the `inputs` / `outputs` field-resolution clause guarantees at least one reading node exists,
so a consumer always exists).

(R-pipeline-001-transport-coverage)=

Every service-typed binding the engine composes must have a corresponding `transport.<name>`
block in the deployment declaration — a missing block raises ContractViolation. That covers
the pipeline's own `service_bindings.<name>` blocks AND
every `[service_bindings.<name>]` a trainable composition declaration supplies (its terminal
`trainable.service_bindings` backend and any preprocessor service binding), joined on the
binding handle under the deployment reference's shared-by-binding-name resolution. The join
is type-coherent: every binding sharing one handle within a composing pipeline's scope MUST
resolve the same service-type — differing service-types under one shared handle raise
ContractViolation at compose (one covering block cannot satisfy two `transport_schema`s).
Coverage
follows what the engine composes, never the consumer: service use outside the composed
pipeline is consumer territory and needs no block. The covering block must carry **every
declared `transport_schema` field and no undeclared field**. Presence-coverage is **uniform**
over the declared field set: a nullable-declared field is satisfied by a supplied value OR by
the [explicit null](#binding-value-supply-grammar-explicit-null) `{ null = true }`; **absence
of any declared field raises ContractViolation** — nullable grants no presence exemption,
because a considered-and-null value is spelled while an absent field is indistinguishable from
forgot ([exhaustive declaration](#architecture-exhaustive-declaration)'s empty-but-present
principle, held at the field level). An unknown field raises ContractViolation. The check is
key-set plus reserved-form recognition only: the declared fields' VALUES pass through opaque
(the `**transport_extra` passthrough the deployment reference's `transport.<name>` contract
owns), the engine reading exactly two reserved value shapes — the explicit-null form, which
normalizes to null before delivery, and, on a `secret_ref`-declared field, the
secret-reference shape whose grammar/scheme/resolver check
[R-deployment-003](#R-deployment-003) owns (cited, not restated). Every other value passes
through unread.

(R-pipeline-001-hook-transport-coverage)=

Every hook in the pipeline's `nodes` list must have a corresponding
`hook_transport."<as_written_node_name>"` block in the deployment declaration. The block is
strict-validated against the hook's `transport_schema`: every declared field must be present, no
unknown fields are accepted, declared types must match. Presence-coverage is uniform here
exactly as for service-binding transport: a nullable-declared hook transport field is satisfied
by a supplied value or by the
[explicit null](#binding-value-supply-grammar-explicit-null) `{ null = true }` (delivered to
the hook body as Python `None`); absence of any declared field raises ContractViolation. A hook
whose `transport_schema` declares
zero fields requires an empty-but-present block — absent raises ContractViolation; empty-but-present
does not.

(R-pipeline-001-streamable-terminal-node)=

A trainable composition whose `[trainable]` node declares `streamable = true` MUST be the
pipeline's terminal node — only hooks (which write no channels) may follow it. Any non-hook node
downstream of a streamable trainable raises ContractViolation. Terminal position is evaluated
**transitively through a terminal nested `pipeline` embed**: a streamable trainable that is
terminal inside a nested `pipeline` which is itself the enclosing pipeline's terminal node
satisfies the rule, and any non-hook node downstream of it — at any nesting layer — raises
ContractViolation.

(R-pipeline-002-merge-kernel)=

A channel's contributors are its seed (if the channel is a declared input) plus its node writes,
in graph order. A channel MAY have two or more contributors **iff** the pipeline declaration's
`merge` block declares a merge strategy for that channel from the engine's closed registry of
merge operations. Without an explicit `merge.<channel>` declaration, a channel with two or more
contributors is rejected at compose time with [ContractViolation](#contractviolation). The runner
folds the contributors into the channel's value inline, in graph order under the declared
strategy — the runner's own work.

**Pipeline-hash and training-bundle-hash composition — no dedicated
R-rule in this doc.** Both hash composition formulas are owned by
[hash-model](#architecture-hash-model); this doc
covers the pipeline-side mechanical justifications in
[§ Hash model](#hash-model-at-pipeline-grain) without a separately-named R-rule. The
R-pipeline-002 slot is occupied by the merge-declaration rule above
under ID-immutability discipline.
