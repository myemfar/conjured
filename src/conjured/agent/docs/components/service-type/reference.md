---
kind: reference
audience: [authors, integrators, agents]
slug: service-type-reference
component: service-type
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#service-type-reference}
# Service-type reference

The per-component reference for the **service-type** — a declared contract for an external
dependency the engine calls. A [service](#service) handler and a
[trainable](#trainable) composition node each bind exactly one service-type; the bound
service-type names *what the engine expects to call* (its identity schema, transport schema,
and generation-parameter config schema) and resolves by qualified name at
pipeline-declaration load. The term, and the service-type's place in the dataflow, are owned
by the glossary entries for [service type](#service-type) and
[service binding](#identity-service-binding).

What lives here:

- The **service-type TOML grammar** — the top-level fields and the closed set of schema
  sections an author writes to declare a service-type.
- The **identity / transport / config split** — which fields shape the pipeline (and fold
  into the [pipeline-hash](#pipeline-hash)), which are per-deployment connection config
  (never hashed), and which are the generation-parameter kwargs the backend call accepts.
- The **`[config_schema]` contract** — the engine-validated declaration of the typed
  generation-parameter kwargs a service-type's implementation accepts, and the mechanical gate it
  adds that makes the prompt-shaping violation R-handler-011 guards structurally impossible.
- The **service-impl dispatch contract** — how a concrete implementation is discovered, the
  closed `invoke()` dispatch-kwargs every implementation accepts, the resolution-time validation
  that binds the two, and the one-implementation-per-service-type rule.
- The **binding-handle → backend wiring model** — how a pipeline's named service binding is
  wired to a concrete backend by the deployment declaration.
- The component's **derived rules** (the R-service-type-* set).

What is **owned elsewhere and cross-referenced, never restated here**: the
**service-type adapter** — the engine-internal wrapper around the backend call, the structural
seam between author code and the backend SDK, and the locus of adapter-boundary
[canonical-event](#canonical-event) capture — is owned by the handler reference's
§ The service-type adapter; this doc cites it. Adapter **resolution** (the two-path
mechanism, the source-AST audit, namespace-package rejection) is owned by
[handler resolution](#architecture-handler-resolution). The **hash construction** algorithm
and the per-event payload spec are owned by [hash-model](#architecture-hash-model). The
pipeline-side **identity supply** and **transport coverage** validation is owned by the
pipeline reference's § `service_bindings.<name>` and its R-pipeline-001.

TOML is the current service-type-authoring dialect; the engine's canonical form is the
Pydantic intermediate representation, and the schema sections below declare
[channel-field types](#channel-field-type) the engine generates Pydantic models from.

---

{#service-type-toml-grammar}
## Service-type TOML grammar

A service-type declaration is a TOML document with a small closed set of top-level fields and
closed-shape schema sections. Sections outside this set raise
[ContractViolation](#contractviolation) at service-type load (engine startup).

```toml
# An LLM structured-output service-type (illustrative).
name = "acme_llm.structured_output"
description = "An LLM backend that emits a constrained, schema-validated structured response."

[identity_schema]                                   # pipeline-level; folds into the pipeline-hash
model           = { type = "str" }                  # model identifier the pipeline is composed against
prompt_template = { type = "str" }                  # named prompt-template selector fixed by the composition

[transport_schema]                                  # deployment-level; never hashed
endpoint    = { type = "str" }                              # base URL of the serving runtime (per-deployment)
api_key_ref = { type = "secret_ref | None", nullable = true }  # secret reference (deployment reference § Secret references); the store holds the BARE token, the adapter renders `Authorization: Bearer`; { null = true } for an unauthenticated local endpoint
timeout_ms  = { type = "int" }                              # per-call timeout in milliseconds (per-deployment)

[config_schema]                                     # the typed generation-parameter kwargs invoke() accepts; folds into the pipeline-hash
temperature = { type = "float" }                    # sampling temperature the implementation's invoke() accepts
max_tokens  = { type = "int" }                      # maximum tokens to generate

[annotations]                                       # optional; engine-opaque consumer surface
notes = "Free-form author notes; never read by the engine, never hashed."
```

{#top-level-fields}
### Top-level fields

- **`name`** — **required.** The service-type's qualified identifier — the string a pipeline
  declaration writes in a binding's `type` (e.g. `service_bindings.llm.type =
  "acme_llm.structured_output"`) and the engine resolves by qualified name at
  pipeline-declaration load. Package-prefixed, immutable once published: a published
  service-type name is a contract its consumers compose against.
- **`description`** — **optional.** A one-sentence statement of what the backend is for. It is
  load-bearing for [trainable](#trainable) composition nodes: it reaches an external generator
  as instruction context for each training pair (the pipeline derivables bundle carries it). It
  folds into neither structural hash; its integrity pin is the trained-artifact manifest's
  `generator_info.derivables_bundle_hash` ([hash-model § What the pipeline-hash
  absorbs](#what-the-pipeline-hash-absorbs) owns the exclusion).
  Prose beyond one sentence belongs in `[annotations]`.

There is no `trainable` field on a service-type. Whether a bound implementation can serve a
trainable composition node is the **integration property of that implementation's
adapter** — certified native-by-construction or by the trainable-backend audit-stamp, the
property gate the handler reference's § Trainable backends and R-handler-008 own — and is
checked against the resolved adapter at compose time, not declared on the service-type.

{#identity-schema-section}
### `[identity_schema]`

**Required, body-required.** Declares the fields that constitute the service-type's
**semantic contract** — the values a pipeline fixes when it composes against this
service-type, the values that *shape the pipeline*.

Every field in a pipeline-level `service_bindings.<name>` block beyond `type` and the reserved
`config` sub-block must be declared in the resolved service type's `identity_schema`. The
`config` block is the binding's generation-parameter supply — its keys resolve against the
service type's `[config_schema]`, not `identity_schema` (the service-type reference's § The
`[config_schema]` contract owns that check). Cross-block misplacement raises ContractViolation
naming the offending field and its correct location.

Identity fields fold into the [pipeline-hash](#pipeline-hash) — see
§ Identity / transport / config split. Examples: model identifier, prompt-template selector,
version selectors.

{#transport-schema-section}
### `[transport_schema]`

**Required, body-required.** Declares the **per-deployment connection config** — the values
that may change from staging to production without changing what the pipeline *is*: endpoint
URL, credential references, timeouts, headers.

Every field in a deployment `transport.<name>` block must be declared in the resolved service
type's `transport_schema`. Cross-block misplacement raises ContractViolation naming the offending
field and its correct location.

[Transport](#transport) values are **never** contributed to any hash. They reach
the implementation as `**transport_extra` on the dispatch — see § Closed dispatch-kwargs.

`nullable = true` (equivalently the `"<T> | None"` type union) is permitted **only** on
transport fields, where a null value is a meaningful per-deployment state (an
unauthenticated local endpoint has no credential). Nullability never exempts a field from
presence-coverage: how a null is supplied — the reserved
[explicit null](#binding-value-supply-grammar-explicit-null) `{ null = true }` — is that
form's law, and the uniform presence a covering block owes every declared field is the
pipeline reference's R-pipeline-001 **Transport coverage**. Identity and config fields are
contract-shaping and admit no nullable declaration: a null identity or config value is not
a meaningful composition state.

{#config-schema-section}
### `[config_schema]`

**Required, empty-allowed.** Declares the typed **generation-parameter kwargs** the
service-type's implementation accepts on its `invoke()` call beyond the closed dispatch-kwargs
— `temperature`, `max_tokens`, `top_p`, a sampling-strategy enum, and similar backend-side
dials. The body MAY be empty (the closed-shape key MUST still appear) when the implementation
accepts no generation parameters beyond the closed dispatch-kwargs. The section is **declared,
not Python-introspected** — see § The `[config_schema]` contract for why, and for the
bidirectional validation it drives. Config fields fold into the
[pipeline-hash](#pipeline-hash) with the identity surface.
A config field MAY declare a per-field ship-time **`default`** — the handler
reference's § Ship-time defaults mechanism on its second declaration
surface; § The `[config_schema]` contract below owns the supply rule and the hash
treatment.

{#annotations-section}
### `[annotations]`

**Optional.** Free-form author notes, usage caveats, examples. Like the
[annotations](#annotations) block on a handler declaration it is **engine-opaque**: graph-inert,
excluded from every hash, never read by the engine — purely a consumer surface for author- and
tooling-facing metadata. Omit it entirely or include it; either is valid.

{#schema-field-vocabulary}
### Schema-field vocabulary

Fields inside `[identity_schema]`, `[transport_schema]`, and `[config_schema]` are declared
with the engine's [channel-field type](#channel-field-type) token set — the same Pydantic-aligned
vocabulary `reads` / `output_schema` use, whose full token grammar is owned by the handler reference's
§ TOML field type discipline. Per-field metadata keys:

- **`type`** — **required.** A channel-field type token. `[config_schema]` additionally admits
  the **`table`** token (below) for an open generation-parameter table; `[transport_schema]`
  additionally admits the **`secret_ref`** token (top-level only, optionally `secret_ref | None`)
  for a credential field — a secret reference the
  deployment supplies as `"[scheme]payload"` and the implementation resolves at dispatch (the
  deployment reference's § Secret references owns the grammar, the scheme set, and the
  never-fetches split — [R-deployment-003](#R-deployment-003)); neither token is admitted in any
  other section, and neither nests inside a collection.
- **validation keywords — none admitted.** Service-type schema fields admit **no** validation
  keywords — neither bare standard constraints nor namespaced third-party validators. Identity,
  transport, and config values have no value-enforcement point (identity reaches the adapter
  raw; config is key/coverage-checked only, per the ruled posture; transport projects raw — in
  every case beyond the one reserved value shape, the
  [explicit-null form](#binding-value-supply-grammar-explicit-null), which the engine
  recognizes at these positions), so a
  value constraint declared here would be a silent no-op — the class the engine forecloses (the
  same fail-loud-inapplicability posture the handler reference's § Validators applies). Declaring
  one raises [ContractViolation](#contractviolation) at service-type load. A value rule that must
  enforce lives on the downstream reader's `reads` schema, where a model is built.
- **`table`** — an open, string-keyed table of JSON-expressible values (strings,
  integers, floats, booleans, and arrays/tables of these, recursively; a
  non-JSON-expressible TOML value such as a datetime raises ContractViolation at
  declaration load). A `table` field is engine-opaque data: the engine validates
  shape only (a mapping with string keys), admits no constraint or extension
  keywords on it, and folds its supplied value into the hash as canonical data.
  Admissible only in `[config_schema]`. Its JSON Schema image is the unconstrained
  open object. The shipped use is the trainable members' `extras` table — declared
  with `default = {}` so a composition that supplies nothing gets the empty table.
- **`nullable`** — **optional, transport fields only** (per § `[transport_schema]`).

A field whose declared `type` is outside the engine's Pydantic IR raises
[ContractViolation](#contractviolation) at service-type load.

---

{#identity-transport-config-split}
## Identity / transport / config split

The three schema sections partition the service-type's fields along one axis: **does this
value shape the pipeline, or is it per-environment?**

- **`[identity_schema]` and `[config_schema]` shape the pipeline.** A model selector, a prompt
  template, a sampling temperature are composition decisions: they define *what this pipeline
  does*, are fixed across the composed pipeline's life, and are baked into a trained artifact's
  reproducibility manifest. They are on the [pipeline-hash](#pipeline-hash)'s hashed side (see
  § Hash placement for exactly what folds in).
- **`[transport_schema]` is per-environment.** An endpoint URL, a credential reference, a
  timeout is a *where/how-to-reach-it* decision that may differ between staging and production
  without changing the graph. It is **never** hashed.

The split is decided by **role, never by enumeration**. An open, author-named config-side
table (a service-type MAY declare one — e.g. an `extras` table of server-specific
generation parameters the engine passes through opaquely) is still on the **hashed side**:
its supplied content shapes generation, so it folds into the hashes **as data** with the
rest of the config surface. The transport side stays never-hashed whether its fields are
enumerated or ride the `**transport_extra` collector. That a value is engine-opaque says
nothing about which side it lives on — opacity is about what the engine *reads*; the
split is about what the value *shapes*.

**Worked example — an observability sink is transport, not identity.** A service-type that
ships its emissions to an observability or logging destination (a metrics endpoint, a
log-aggregation URL, a trace collector) declares that destination in `[transport_schema]`:
it is a *where-to-reach-it* value that moves from staging to production without changing what
the pipeline does, so it is never hashed. Pinning the destination in the composition —
putting it on `[identity_schema]` — would fold a per-environment address into the
[pipeline-hash](#pipeline-hash), so one composition would hash differently per environment:
the hash-invisible divergence the split exists to prevent. The placement is a **design call
the author owns** and a reviewer checks — the engine reserves no observability vocabulary and
runs no destination-placement check (it forwards a transport value it does not interpret), so
the discipline here is review territory, not a mechanical gate.

This is the same identity-vs-transport split the glossary draws for a service binding
([identity service binding](#identity-service-binding) vs [transport](#transport)); the
service-type's schema sections are where that split is *declared* — identity and config on the
hashed side, transport on the never-hashed side.

{#hash-placement}
### Hash placement

What folds into which hash follows the identity / transport / config split; the architecture
hash-model's § What the pipeline-hash absorbs owns the authoritative absorb/exclude list. For a
service-type:

- **The qualified `name` and the supplied identity values fold into the
  [pipeline-hash](#pipeline-hash).** The service-type qualified name a binding references, and the
  identity *values* a pipeline supplies in `service_bindings.<name>`, both contribute; a
  `service_bindings.<name>` `config` block's **effective** values (supplied-or-default, per
  § The `[config_schema]` contract) fold in the same way, with the identity surface. Because
  `[identity_schema]` and `[config_schema]` constrain what a binding must supply (the pipeline
  reference's R-pipeline-001 binding-supply matching), editing either schema changes the supplied
  values and so shifts the hash — editing a service-type's identity or config schema is a
  composition change.
- **Config values fold into the trainable's hash.** The **effective** config *values* —
  supplied in `[trainable.config]`, or the declared ship-time default where the
  composition omits a default-bearing field (§ The `[config_schema]` contract) — fold into
  that trainable's [training-bundle-hash](#training-bundle-hash), and so into the
  pipeline-hash by reference.
- **`[transport_schema]` and its values are excluded** — editing the transport schema, or moving
  from staging to production, shifts no hash, consistent with transport being never-hashed
  everywhere in the engine.

---

{#config-schema-contract}
## The `[config_schema]` contract

`[config_schema]` declares the generation-parameter kwargs the implementation's `invoke()`
accepts, and the engine validates against that declaration from both directions. Beyond typing
those kwargs, it is the structural backing for the prompt-shaping discipline R-handler-011 guards
(see § The R-handler-011 consequence below).

**Declared, not introspected.** The config kwargs are written in the service-type TOML, not
read off the Python `invoke()` signature by reflection. This mirrors the handler reference's
R-handler-001 rationale: an introspected signature can lie (a `**kwargs` collector, a
signature-hiding decorator, a `functools.partial`), and a reflected signature is neither
author-visible nor hashable. A declared `[config_schema]` is both — it is the author-facing,
hash-folded contract, and the Python signature is checked *against* it.

**Bidirectional validation:**

1. **Implementation side — at resolution.** When the engine resolves a concrete service
   implementation, it validates that the implementation's `invoke()` signature accepts exactly
   the closed dispatch-kwargs plus a keyword-only parameter for every field declared in
   `[config_schema]`, plus the `**transport_extra` collector — no more, no less. A mismatch
   raises [ContractViolation](#contractviolation) (this is the dispatch contract's signature
   validation; see § Signature validation).
2. **Composition side — at compose.** When a [trainable](#trainable) composition node binds a
   service-type, its `[trainable.config]` entries are validated against the bound service-type's
   `[config_schema]`: every key MUST be a declared config field. An undeclared key raises
   [ContractViolation](#contractviolation) at compose. Config fields admit no nullable
   declaration, so the reserved
   [explicit-null form](#binding-value-supply-grammar-explicit-null) rejects at compose in a
   config position. Supply is also **complete** by
   construction: a config field that declares a ship-time
   `default` (the handler reference's § Ship-time defaults owns the mechanism) MAY be
   omitted in `[trainable.config]` — the
   engine supplies the declared default (and the composition MAY override it); a config
   field with no declared default MUST be supplied, and an uncovered field raises
   [ContractViolation](#contractviolation) at compose. Every config kwarg therefore reaches
   `invoke()` with a concrete, composition-visible value — a generation parameter is never
   an undeclared server-side unknown. **Hash treatment is supply-site:** the **effective**
   value (supplied-or-default) per config field is what folds into the trainable's
   [training-bundle-hash](#training-bundle-hash) (§ Hash placement). The declared default
   itself lives in the service-type declaration, which folds into neither hash (the
   hash-model's explicit exclusion of service-type schema declarations) — so, unlike a
   `bindings.<name>` ship-time default's two-fold treatment, editing a shipped config
   default shifts exactly the compositions that relied on it (their effective value
   changes) and leaves every overriding composition's hashes untouched. The divergence is
   derived, not incidental: a binding default lives in a handler declaration, which IS
   hashed by qualified-name resolution; a service-type declaration is excluded from both
   hashes by design.

The composition-side check applies at **every config supply site**. A service-typed
binding outside the trainable composition kind supplies the same `[config_schema]`
values in its pipeline `service_bindings.<name>` entry's **`config` block** (the
pipeline reference's § `service_bindings.<name>` owns the block's grammar); the supply
rule above — every supplied key declared, every declared field covered by supply or by
its declared default, both directions checked at compose — is identical at both sites.
Hash treatment stays supply-site and follows the node kind: a `config` block's
effective values fold in with the binding's identity values, riding the binding node's
existing hash treatment exactly as the identity surface does (§ Hash placement).

**The R-handler-011 consequence.** The handler reference's R-handler-011 — review-enforced —
requires:

Prompt-shaping content reaching a trainable composition node MUST be produced by an upstream
[preprocessor](#preprocessor) and arrive via `trainable.reads`; it MUST NOT appear in
`trainable.config`. Templates, system prompts, prompt scaffolds, content-injection strings — none
of these are valid `trainable.config` entries, which carry compose-time generation parameters
(temperature, top-p, max-tokens, sampling-strategy enum, similar backend-side dials) ONLY.

`[config_schema]` adds a **mechanical gate** that makes the violation structurally impossible: a
prompt-shaping `template = "..."` slipped into `[trainable.config]` fails compose, because
`template` is not a declared `[config_schema]` field — there is no generation-parameter kwarg by
that name to carry it. The training-input record (`reads_snapshot`) therefore cannot silently
omit prompt content that shaped the emission, which is what R-handler-011 protects for I4.
(R-handler-011 is owned by the handler reference, which records this gate as its
structural-gate component.) Where a service-type declares an **open** config table (an
`extras` table of passthrough generation parameters), the gate's mechanical reach ends at
that field's boundary: the engine cannot know which server-specific key shapes content, so
prompt-shaping content smuggled *inside* an open table is excluded by **review**
(R-handler-011's own enforcement mode), not by the compose gate — and it is never
*hidden*: an open config table is identity-class, hash-covered, and visible in the
composition declaration exactly where a reviewer looks.

---

{#service-impl-dispatch-contract}
## Service-impl dispatch contract

A **service implementation** (the adapter that backs a service-type) is the concrete code the
engine calls to reach a backend. The contract below governs how an implementation is
discovered, what its `invoke()` must accept, and how the engine binds the two at resolution.

{#entry-point-groups}
### Entry-point groups and resolution

The Python [entry-points groups](#entry-points-group) carry additive third-party discovery:

(entry-point-groups-roster)=

- **`conjured.handlers`** — bare-function handler discovery (additive alongside
  [dotted-path resolution](#dotted-path-resolution)).
- **`conjured.service_implementations`** — concrete service-implementation (adapter) discovery.
- **`conjured.validators`** — third-party field-[validator](#validator) discovery (the handler
  reference's § Validators owns the contract; named here for the group roster only).

A service-type's concrete implementation resolves through the **adapter sibling mechanism**
owned by [handler resolution](#architecture-handler-resolution), using the
[inverted selector](#adapter-selector-inverted-priority) under the
`conjured.service_implementations` entry-points group; the same source-AST audit applies, with
the vector-7 above-instance-scope-state AST audit in place of the bare-function
function-shape check (an adapter is a class by construction). A service-type qualified name
is a **type identity**, never coupled to the implementer's module layout. The service-type
**declaration** (this TOML) is resolved by its qualified `name`, and the engine pairs
implementation to service-type by qualified name. Entry-point name collisions fail loud at
startup (the resolution doc's § Entry-points collision owns that).

{#adapter-construction}
### Construction

An adapter is a **class**; the values supplied for this service-type's `identity_schema`
(e.g. `model`, `prompt_template`) fix the instance at construction, while its `transport_schema`
and `config_schema` values arrive per dispatch at `invoke()` (below). Its construction lifecycle:

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

The **trainable composition node dispatches through this same adapter** — it is a service dispatch
with no author body wrapping it (R-handler-010): the engine constructs the bound trainable backend's
adapter identically and calls `invoke()` with the identical closed dispatch-kwargs. Forcing the
trainable through the same adapter boundary as a service is deliberate — it makes the trainable's
training-capture (`handler_enter` / `handler_exit`) match service capture by construction.

{#adapter-instance-state-caching}
### Adapter-internal caching — instance state, composition-bounded

An adapter MAY cache runtime-derived artifacts (an authenticated client, a
compiled request template, a tokenizer handle, an LRU over expensive
derivations) — and the cache MUST live in **instance state** on the adapter
object (`self.…`, initialized in `__init__` or on first use), where the
engine-managed one-instance-per-composition lifetime bounds it. This is the
canonical authoring pattern the construction lifecycle's lazy memoized client
already exemplifies. Class-level caches (`@lru_cache` on a method, a class
variable) and module-level cache dicts persist *beyond* the composition
lifetime and are rejected at adapter resolution — the
[trust-model](#trust-model-vector) vector-7 seal's AST audit. An author who
wants an LRU writes it as an instance attribute (e.g. a bounded dict on
`self`), never as a decorator at namespace scope.

{#deadline-propagation}
### Deadline propagation

A pipeline invocation may carry a whole-run budget — the consumer pipeline-level
timeout ([its request parameter](#consumer-pipeline-level-timeout-request-param)). The
runner propagates that budget to the adapters inside the run: a **participating**
dispatch surface receives the engine-supplied keyword-only kwarg

- **`remaining_budget_ms`** — the whole-run budget **minus the run's elapsed time at
  the moment of this call**, clamped at zero; `None` when the invocation carries no
  budget (an unbounded run).

Participation is declared by the signature, per surface: an `invoke` (and,
independently, an `invoke_streaming`) that declares the kwarg receives it on every
call; a surface that does not declare it is dispatched without it, unchanged — the
kwarg is the one **optional** member of the engine-supplied dispatch-kwargs
([§ Signature validation](#signature-validation) admits both forms). A participating
adapter applies the budget as a per-call ceiling: its effective per-call timeout is
`min(its own per-call transport timeout, the remaining budget)` — so a call issued
late in a budgeted run never outlives the run that issued it. A remaining budget of
zero is an exhausted run: the participating adapter fails the call as a timeout
immediately — zero is a floor, not an unbounded sentinel. The native trainable
backends participate on both surfaces.

The kwarg name is **engine-reserved** — the runner is its only supplier, so a
`[config_schema]` or `[transport_schema]` field named `remaining_budget_ms` is
rejected at declaration load (one kwarg, one source). Like every dispatch-kwarg it
contributes to no hash: the budget is an invocation-time value, outside every
declaration the hash machinery reads.

{#closed-dispatch-kwargs}
### Closed dispatch-kwargs

Every service implementation's `invoke()` accepts a **closed set** of engine-supplied
keyword-only dispatch-kwargs, plus the config kwargs its service-type declares, plus a
transport collector:

```python
class StructuredOutputImpl:
    def invoke(self, *,
               input_payload,            # the call payload (handler-body-assembled, or the
                                         #   trainable's reads projection) submitted to the backend
               service_name,             # the pipeline-local binding handle this call is for
               caller_qualified_name,    # the dispatching node's qualified name (descriptive)
               caller_position,          # the dispatching node's dispatch position (the identity)
               temperature, max_tokens,  # one kwarg per [config_schema] field
               **transport_extra):       # the deployment's transport.<binding> block
        ...
```

- **`input_payload`** — the payload submitted to the backend. For a [service](#service)-kind
  handler it is what the handler body assembled and passed to
  [`services.<name>.invoke(...)`](#services-kwarg) (the domain-kwarg surface, mediated by the
  [ServicesProxy](#servicesproxy) and owned by the handler reference); for a trainable
  composition node it is the `trainable.reads` projection. It is the same `input_payload`
  captured on the `service_invocation` [canonical event](#canonical-event) at the adapter
  boundary (the hash-model's § Adapter-boundary capture owns that capture).
- **`service_name`** — the pipeline-local binding handle (e.g. `llm`) this call serves; see
  § Binding-handle → backend wiring.
- **`caller_qualified_name`** — the dispatching node's qualified name. It is a **descriptive**
  field, not an identity: a handler reused at several node positions shares one qualified name.
- **`caller_position`** — the dispatching node's **dispatch position** — the same
  `handler_position` the [canonical events](#canonical-event-types) carry (0-indexed in the
  engine's final compose-time dispatch order). Position, not
  the qualified name, is the dispatch identity — the
  [dispatch-identity key](#canonical-event-types-dispatch-identity) hash-model
  owns. Carrying it on the dispatch lets an
  implementation attribute a call to its precise dispatch for provenance.
- **config kwargs** — one keyword-only parameter per `[config_schema]` field, carrying the
  composition-fixed generation parameters: the effective values the composition supplies (for
  a trainable, partial-applied from `[trainable.config]`; for any other service-typed binding,
  from the pipeline's `service_bindings.<name>` `config` block — § The `[config_schema]`
  contract owns the supply rule).
- **`**transport_extra`** — the deployment-supplied [transport](#transport) block (the fields
  declared in `[transport_schema]`). Transport rides the variadic collector — not declared named
  parameters — because it is per-deployment and never hashed; the signature contract binds only
  the closed kwargs and the hashed config kwargs.

One engine-supplied kwarg is **optional**: a surface that also declares
`remaining_budget_ms` receives the run's remaining whole-run budget —
[§ Deadline propagation](#deadline-propagation) owns its semantics, the per-surface
participation rule, and the reserved name.

{#streaming-adapter-surface}
### The streaming adapter surface (`invoke_streaming`)

A trainable backend MAY additionally expose **`invoke_streaming`** — a **generator
function** with the same closed dispatch-kwargs as `invoke` — as its token-level
delivery surface: it `yield`s each raw text fragment as the backend emits it and
`return`s the same assembled parsed emission `invoke` returns, which the engine
validates through the identical output boundary. The ENGINE drives the generator and
owns fragment delivery to the consumer's run-attached sink (the pipeline reference's
§ Pipeline invocation `stream_sink`) — no consumer callback ever enters adapter
frames, so adapter code stays pure wire code. The surface is **required exactly when
a composition binding the backend declares `streamable = true`**: compose verifies
the resolved class exposes a generator-function `invoke_streaming` and rejects the
binding otherwise — a delivery promise the binding cannot honor never reaches
dispatch, and there is no silent buffered fallback. A backend never bound streamable
needs no streaming surface. The native `openai_compatible_trainable` implements it
(the OpenAI-compatible SSE chat-completions stream).

{#signature-validation}
### Signature validation

When the engine resolves a service implementation it introspects the implementation's `invoke()`
signature and verifies it is keyword-only and matches the contract: the closed dispatch-kwargs,
exactly the config kwargs declared in the bound service-type's `[config_schema]`, and a
`**transport_extra` collector. The one declared-optional member is the deadline-propagation
kwarg `remaining_budget_ms` ([§ Deadline propagation](#deadline-propagation)) — the check
admits the signature with or without it, per surface. Any other mismatch — a missing closed
kwarg, an undeclared extra parameter, a config kwarg with no `[config_schema]` field, a
positional parameter — raises [ContractViolation](#contractviolation). The check parallels the kwarg-only signature
introspection [handler resolution](#architecture-handler-resolution) performs at its step 6 —
adapter resolution runs the same sequence, save the adapter-specific audits at steps 3 and 5 —
and, like all resolution, it is a compose-time check that never fails at runtime. This is the
implementation-side half of the § The `[config_schema]` contract bidirectional check.

{#namespace-package-restriction}
### Namespace-package restriction

A service-implementation module MUST live in a regular package — one with an explicit
`__init__.py` — never a namespace package (PEP 420). The engine detects a namespace package at
resolution by `find_spec().origin is None` and rejects it with a remediation hint, exactly as
for handler modules; the [handler resolution](#architecture-handler-resolution) doc's
§ Namespace packages owns the mechanism, the hint, and the why.

{#one-implementation-per-service-type}
### One implementation per service-type

Each service-type **qualified name** resolves to **exactly one** registered implementation. A
qualified name resolving to **zero** implementations is an unsatisfiable binding — a compose-time
[ContractViolation](#contractviolation) when a pipeline binds it. **Two or more** implementations
registered for one qualified name (two packages registering the same
`conjured.service_implementations` entry-point name) is an entry-point collision the engine fails
loud on at startup (the [handler resolution](#architecture-handler-resolution) doc's § Entry-points
collision owns the mechanism, the hint, and the why).

This does not narrow the glossary's "multiple implementations may satisfy one service type." That
statement is about contract *shapes*: a fake implementation and a production
implementation satisfy the *same contract shape* by declaring **distinct qualified names** (e.g.
`acme_dialog.structured_output` and `acme_dialog_fake.structured_output`), and a pipeline swaps
between them by editing the binding's `type` — the qualified-name boundary the handler reference's
§ Test substitution and the [fake service](#fake-service) glossary entry describe. Per qualified
name there is exactly one implementation; across qualified names a contract shape may have many.

---

{#binding-handle-to-backend-wiring}
## Binding-handle → backend wiring

A **service binding** is a **named handle** to a service-type, local to its composing pipeline's
scope — the name (e.g.
`llm`) a handler declares in its `service_bindings`, with identity supplied at the owning
declaration layer: the pipeline's `service_bindings.<name>` block, or an embedded trainable
composition's `[service_bindings.<name>]` block (the same supply grammar at both layers — the
mirror-pipeline principle). The **deployment
declaration is the wiring layer**: it wires each binding-handle to a concrete backend
through that binding's [transport](#transport) — structurally parallel to how the port model wires
a handler's read-ports and output-ports to channels via its [read-map](#read-map) and
[write-map](#write-map). A handle names an intent ("this node calls an LLM"); the deployment wires
that intent to a backend.

It follows directly that there is **no binding-name collision** to resolve:

- **Two pipelines both naming `llm`** are two **independent handles**, not a collision — each is
  local to its own composing pipeline's scope, and the deployment wires each to a backend. There
  is nothing for the
  engine to disambiguate.
- **Sharing one backend** across pipelines is wiring both handles to the **same**
  backend/transport — explicit and supported; calling the same backend from two pipelines is
  ordinary.
- **Different backends of the same service-type** is wiring each handle to its **own** backend —
  two deployments of `acme_llm.structured_output` at different endpoints, one per handle.
- **A pipeline and an embedded trainable composition both naming `llm`** share one composed
  scope, so they share the covering `transport.llm` block by the as-written-handle join — which
  is coherent only when both handles resolve the same service-type; the pipeline reference's
  R-pipeline-001 **Transport coverage** owns that join and its type-coherence rule (differing
  service-types under one shared handle reject at compose).

The wiring is what carries a handle to a concrete backend; the binding name is not a
global key, so two pipelines never contend for it. The full deployment-TOML grammar — the exact
shape of the `transport.<binding>` wiring blocks — is owned by the deployment-declaration
reference (how an engine process discovers its deployment declaration at startup is an integration
concern, per that reference); this section documents the binding-wiring *model* it realizes.

---

{#service-type-worked-example}
## Worked example

A structured-output LLM service-type, a conformant implementation, and the pipeline + deployment
that wire a binding-handle to it.

```toml
# acme_llm/service_types/structured_output.toml — the service-type contract
name        = "acme_llm.structured_output"
description = "An LLM backend that emits a constrained, schema-validated structured response."

[identity_schema]
model           = { type = "str" }                  # model identifier the pipeline is composed against
prompt_template = { type = "str" }                  # named prompt-template selector fixed by the composition

[transport_schema]
endpoint    = { type = "str" }                              # base URL of the serving runtime (per-deployment)
api_key_ref = { type = "secret_ref | None", nullable = true }  # secret reference (deployment reference § Secret references); the store holds the BARE token, the adapter renders `Authorization: Bearer`; { null = true } for a local endpoint
timeout_ms  = { type = "int" }                              # per-call timeout in milliseconds (per-deployment)

[config_schema]
temperature = { type = "float" }                    # sampling temperature the implementation's invoke() accepts
max_tokens  = { type = "int" }                      # maximum tokens to generate
```

```python
# acme_llm/impls/structured_output.py — the concrete implementation (adapter)
class StructuredOutputImpl:
    def invoke(self, *, input_payload, service_name, caller_qualified_name, caller_position,
               temperature, max_tokens, **transport_extra):
        # temperature / max_tokens are the [config_schema] kwargs (startup-validated);
        # transport_extra carries endpoint / api_key_ref / timeout_ms from [transport_schema].
        ...  # submit input_payload to the backend under the declared constraint; return the typed result
```

```toml
# pipeline declaration — supplies identity for the binding-handle "llm"
[service_bindings.llm]
type            = "acme_llm.structured_output"   # service-type qualified name
model           = "qwen3.5-4b-gguf"                  # identity (hashed)
prompt_template = "dialogue_v3"                       # identity (hashed)

# deployment declaration — wires the handle "llm" to a concrete backend (transport; never hashed)
[transport.llm]
endpoint    = "https://llm.prod.internal/v1"
api_key_ref = "[env]LLM_PROD_KEY"
timeout_ms  = 30000
```

A second pipeline in the same deployment root MAY also name a binding `llm`; it is an independent
handle, wired by its own `transport.llm` block — to the same backend (sharing) or a different one.

---

{#service-type-derived-rules}
## Derived rules

Every derived rule that governs this component lives here. The rules cite the invariant(s) or
tenet(s) they protect from [principles](#invariants-and-derived-rules) via
`derived_from`; they declare an `enforcement` mode per
[enforcement-modes](#architecture-enforcement-modes).

```yaml
rules:
  - rule_id: R-service-type-001
    name: closed service-type declaration grammar
    derived_from: [I1]
    enforcement: mechanical
    statement: |
      A service-type declaration's shape is a closed set: the top-level
      `name` (required) and `description` (optional) fields, and the
      schema sections `[identity_schema]` (required, body-required),
      `[transport_schema]` (required, body-required), `[config_schema]`
      (required, empty-allowed), and `[annotations]` (optional,
      engine-opaque). A declaration carrying an unknown top-level field
      or section, or omitting a required section, raises ContractViolation
      at service-type load (engine startup). Schema-section fields are
      declared with the engine's channel-field type vocabulary (the
      handler reference's TOML field type discipline owns the token set);
      `nullable` is admitted only on `[transport_schema]` fields. A field
      typed outside the engine's Pydantic IR raises ContractViolation at
      load. Load-bearing for I1: a service-type whose declaration carries
      an undeclared element is asking the engine to honor a contract it
      never declared; closed-shape grammar makes that a rejected case, not
      a silently-ignored one.

  - rule_id: R-service-type-002
    name: config-schema contract
    derived_from: [I1, I4]
    enforcement: mechanical
    statement: |
      A service-type's `[config_schema]` declares the typed
      generation-parameter kwargs its implementation's `invoke()` accepts
      beyond the closed dispatch-kwargs. The engine validates it from both
      directions: (1) at resolution, the registered implementation's
      `invoke()` signature MUST accept exactly the closed dispatch-kwargs
      plus one keyword-only parameter per declared `[config_schema]` field
      plus a `**transport_extra` collector — a mismatch raises
      ContractViolation; (2) at compose, a trainable composition node's
      `[trainable.config]` entries MUST each be a declared `[config_schema]`
      field — an undeclared key raises ContractViolation. The contract is
      declared, not Python-introspected (an introspected signature can lie
      and is neither author-visible nor hashable; mirrors R-handler-001).
      `[config_schema]` is on the hashed identity surface (editing it shifts
      the pipeline-hash via the config values it constrains). Load-bearing
      for I4: this adds a mechanical gate enforcing the R-handler-011
      discipline (prompt-shaping content arrives via `trainable.reads`,
      never `[trainable.config]`) — a prompt-shaping value in
      `[trainable.config]` fails compose because no `[config_schema]` field
      carries it, so the training-input record cannot silently omit prompt
      content that shaped the emission.

  - rule_id: R-service-type-003
    name: service-impl dispatch contract
    derived_from: [I1, I3]
    enforcement: mechanical
    statement: |
      A concrete service implementation is discovered through the adapter
      sibling mechanism (handler resolution's two-path resolution against
      the `conjured.service_implementations` entry-points group, with the
      vector-7 AST audit in place of the function-shape check) and bound to
      its service-type by qualified name. Every implementation's `invoke()`
      accepts a closed set of engine-supplied keyword-only dispatch-kwargs —
      `input_payload`, `service_name`, `caller_qualified_name`,
      `caller_position` — plus the `[config_schema]` config kwargs and a
      `**transport_extra` collector; the signature validation in
      R-service-type-002 binds the signature to the declaration at
      resolution. `caller_position` (the dispatching node's compose-time
      dispatch position) is the dispatch identity, since
      `caller_qualified_name` is not unique under multi-dispatch. An
      implementation module MUST live in a regular package; a namespace
      package (PEP 420, `find_spec().origin is None`) is rejected at
      resolution with a remediation hint. Resolution and signature failures
      are compose-time ContractViolation (entry-point short-name collisions
      fail loud at startup); none can fail at runtime. Load-bearing for I3:
      the dispatch boundary is a closed, engine-owned contract the
      implementation conforms to, keeping the engine agnostic of backend
      shape.

  - rule_id: R-service-type-004
    name: one implementation per service-type qualified name
    derived_from: [I2]
    enforcement: mechanical
    statement: |
      Each service-type qualified name resolves to exactly one registered
      implementation. A qualified name resolving to zero implementations is
      an unsatisfiable binding — a compose-time ContractViolation when a
      pipeline binds it; two or more implementations registered for one
      qualified name is an entry-point collision the engine fails loud on at
      startup (silent disambiguation would let an unrelated package install
      change which backend a pipeline calls). This does not narrow "multiple
      implementations may satisfy one service type": distinct
      implementations satisfy the same contract shape under distinct
      qualified names (the production / fake pair), swapped by
      editing a binding's `type`. Per qualified name, one implementation;
      across qualified names, a shape may have many. Load-bearing for I2:
      service-type resolution is strict qualified-name equality, so a
      composition's backend binding is determined at compose, not by
      install-order accident.

      **Engine-owned identities.** A `conjured.lib.*` qualified name is an
      engine-owned identity: it resolves through the engine's shipped native
      table, which takes precedence over consumer resolution, so a native
      qualified name's implementation is necessarily the engine's shipped one —
      never a value a later-installed package can supply or shadow. Authoring or
      registering a *modified* declaration under a `conjured.lib.*` name (there is
      nothing for an author to re-declare — the identity already resolves to the
      engine's shipped declaration and its one registered implementation) fails
      loud. Enforced by the `engine-owned-identity` check.
```
