---
kind: reference
audience: [authors, integrators, agents]
slug: architecture-handler-kinds
---

{#architecture-handler-kinds}
# Handler kinds

The engine's dispatchable unit is the
[handler](#handler) — the unit a
[pipeline](#pipeline) composes into a typed dataflow
[graph](#graph). Every handler is exactly one of the
[closed-enum](#closed-enum)
[kinds](#handler-kind) — **transform**, **service**,
**hook** — distinguished by the [node role](#node-role) it
plays in the graph: what [channels](#channel) it may read, what channels it may write, and
whether its body reaches an external resource. All three ship as
bare kwarg-only functions; the
engine constructs the dispatch wrapper (see
[Binding discipline](#binding-discipline)). The runner dispatches each handler to
the code path for its kind; the kind is named at the top of the handler
declaration.

**A handler is one way to realize a graph [node](#node);
the other is a composition.** A node's `kind` discriminator is `handler` (a
bare-function handler, covered here) or `composition` (an embedded composition
declaration — a taxonomy this doc does not own; its
[membership and realization status](#handler-toml-grammar/composition-kind-roster)
are the handler reference's grammar's). This doc
owns the **handler-kind** layer — the node-role taxonomy. The **composition**
layer — engine-constructed dispatch, training capture, the trainable composition
specialization — is owned by the
[handler component reference](#handler) and the pipeline
reference; this page names that boundary once and does not re-fence it per section.
Which layer a concern lives in is answered by which doc you are in, not by repeated
prose.

The taxonomy is a closed enum. Future extensions go through an engine change with
an architecture decision; the engine has no runtime extension hook for new kinds
(R-handler-003, closed-enum handler kinds). See
[Adding a new kind](#adding-a-new-kind).

---

{#comparison}
## Comparison

| | Declared reads | Declared writes | External call | Halts on failure |
|---|---|---|---|---|
| **Transform** | optional | required | none | yes |
| **Service** | optional | required | exactly one | yes |
| **Hook** | optional | none | zero or more | no (operational only; halts on contract / schema errors) |

- **Declared reads.** Whether the handler may declare `reads` [input ports](#input-port), which
  the runner projects — each from its [read-map](#read-map)-wired channel — into the handler's
  kwargs at dispatch. All kinds may; "optional" means the handler MAY declare empty
  reads (behavior fully determined by compose-time bindings).
- **Declared writes.** Whether the handler's node emits channels — a return dict
  keyed by [output-port](#output-port) name whose values the runner routes onto the graph's
  channels via the node's [write-map](#write-map) after dispatch. Transforms and services emit;
  hooks return `None` and write to no channels.
- **External call.** Whether the handler's dispatch reaches an external resource.
  Transforms never; services exactly once per dispatch (the
  [atomicity rule](#service)); hooks emit zero or more
  times via stdlib or backend-SDK channels.
- **Halts on failure.** Whether a runtime failure halts the pipeline. Transforms
  and services halt on every error class — their nodes write channels downstream
  readers will consume, and a swallowed failure would produce a channel value the
  runner cannot distinguish from a successful one. Hooks write no channels, so a
  swallowed operational failure loses a side-effect record but cannot corrupt any
  downstream read; their disposition is the bounded two-case rule
  [R-error-channel-003](#R-error-channel-003) owns (halt semantics; the runner's
  hook wrapper).

The halt-on-failure column is a *consequence* of the writes column, not an
independent axis: a node writing to channels must halt on failure to preserve
channel-value integrity; a node writing to no channels has no such constraint.
This is the central topological property motivating the taxonomy.

---

{#the-transform-kind}
## Transform

A [transform](#transform) is the **pure-internal-node** kind — deterministic, with no
external runtime resource and no service invocation.

A transform is a bare kwarg-only
function. Every parameter mirrors a declared `reads` field or a declared
`bindings.<name>` entry.

:::{transclude} R-handler-004/mechanical-half
:::

Consequently a transform's node has no external-call edge.
[Compose-time bindings](#compose-time-binding) declared via
`bindings.<name>` reach the handler as kwargs whose values are fixed across every
dispatch of this composed pipeline — the runner supplies each as a fresh
per-dispatch copy of the compose-resolved value. See [Binding discipline](#binding-discipline).

Canonical examples (illustrative):

- **Charset-filter normalizer.** Strips emote markers from player text.
  `bindings.config` declares a `marker_set` enum (brackets / asterisks / parens)
  bound at compose time; `reads` declares one input text port; the node writes
  one normalized-text port onto a channel via its write-map.
- **NPC import.** Materializes an NPC declaration's character data onto downstream
  channels. `bindings.npc` declares the NPC fields; the pipeline-entry supplies
  the values inline or by external declaration file path at compose time. `reads`
  may be empty when behavior is fully determined by bound values.
- **Response packaging.** Assembles a typed envelope from upstream channels
  declared in `reads`; declared bindings may select between envelope variants.
- **Structured-data shaping.** Projects, filters, or rearranges values from
  upstream channels per declared binding shape parameters.

A transform that needs to invoke an LLM, query a database, or otherwise reach
external state is a misuse — the handler is a service for non-trainable external
calls; for training-capture backends, the right structural shape is a trainable
composition node (the composition layer, owned by the handler component
reference).

---

{#the-service-kind}
## Service

A [service](#service) is the **external-edge-node** kind: it makes **exactly one
external call per dispatch** and returns the result as its declared `output_schema`
output ports, which the runner routes onto channels via the node's write-map. That atomicity — semantic retry (the "call → critique → call again"
pattern) is forbidden because it buries multiple distinct external interactions under
one captured invocation — preserves the wire-visible record of what the adapter
submitted vs what the backend returned, the seam the consumer-side
no-silent-fallbacks divergence check (R-handler-002) relies on.

Service-kind handlers do not host training capture; that is the trainable
composition kind's role at the composition layer (a service calling the same
backend a trainable composition node calls produces non-trainable channel writes).
Whether training records fire is determined by composition kind, not by any
property of the service-type declaration.

Services bind to a [service type](#service-type) supplied
at pipeline level. The binding's
[identity](#identity-service-binding) values (model name,
prompt template, version selector) live in the pipeline declaration and contribute
to the [pipeline-hash](#pipeline-hash). The binding's
[transport](#transport) values (endpoint, credentials,
timeouts) live in the deployment declaration and are NOT hashed — moving from
staging to deployment does not change the graph.

{#channels-a-service-writes-the-outputschema-discipline}
### Channels a service writes — the `output_schema` discipline

A service's `output_schema` declares **its named, typed output ports**, no more
and no less; the runner routes each validated output-port value onto a channel via
the node's write-map, and validates the handler's return against the schema. (The
stronger literal-equal identity — the declared schema submitted verbatim as the
backend's structured-output / constrained-decoding constraint — is confined to the
trainable composition node, per R-handler-005; a plain service's `output_schema`
constrains the handler's return, not the backend's wire.) There is no
service-appended metadata — no latency field tucked into the
return dict by the service body, no cost field, no in-band telemetry. A service
that wants to enrich its output with non-emission metadata (token counts,
structural verdicts, runner-measured latency / cost) MUST split into two nodes: the
service writing its declared `output_schema` output ports, and a downstream
transform reading the service's channel and writing the metadata channel.

{#service-type-adapter-the-dispatch-path-role}
### Service-type adapter — the dispatch-path role

The `services.<name>.invoke(...)` call reaches a
[service-type adapter](#service-type-adapter) — the
engine's wrapper around the backend call for the service-type the binding resolves
to. In the dispatch path, the adapter is the structural seam between the handler
body and the backend: it serializes the invocation arguments, issues the call,
deserializes the response into the typed result — translation, never a verdict —
and returns it to the handler body as the value of `services.<name>.invoke(...)`.

The seam is structurally outside the handler body's reach, which is what lets the
engine capture canonical-event payloads at the adapter boundary that the handler
body cannot influence (service-kind dispatches emit `service_invocation` there).
The adapter's full ownership boundaries, its load-bearing properties, and its
[trust-model](#trust-model-vector) vector-7 audit are owned
by the handler component reference; this page covers only its role in the dispatch
path.

---

{#the-hook-kind}
## Hook

:::{region} the-hook-kind/observer-write-profile
A [hook](#hook) is the **observer-node** kind: it writes no channels — it returns
`None` by contract, and the runner has no merge path for a hook's return value. No
downstream node reads channels from a hook position.
:::

A hook resembles a service at the external edge — it too may reach an external
resource — but it is a distinct kind, not a service subtype: it carries **no
identity** (nothing about it is hashed), hands the runner **nothing to write**,
makes **zero or more** external calls rather than a service's exactly one, and
tolerates **operational** failure — contract violations still fail loud.

It has no `output_schema` to declare against. Hooks may carry `bindings.<name>`
compose-time bindings via the same bare-function mechanism as transforms and
services.

Hooks support a bounded two-case emission pattern:

- **Stdlib emission.** File writes, stdout / stderr, in-process tracers. The
  hook's own `transport_schema` declares per-deployment config (paths, formatter
  selectors), delivered to the body as kwargs like bindings (the handler
  reference's § `transport_schema` owns the delivery rule); the hook body emits
  via direct stdlib calls. No service-typed binding required.

  *Wiring `logging` in the body?* Emit to a documented logger — `getLogger(<name>)`
  then `logger.info(...)` — and let the deployment configure the sink through standard
  logging configuration; for a logging hook the `transport_schema` selects the record
  **format**, not a sink for the body to attach. The body attaches **no** handler:
  `getLogger` returns a process-global logger, so a per-dispatch
  `addHandler(FileHandler(...))` accumulates a handler — and leaks its file descriptor —
  on every dispatch. The engine's native stdlib-emission hooks follow exactly this shape:
  emit to the documented logger, bind no sink path of its own (which members exist
  is the [native-library reference](#native-library-reference/kernel)'s to enumerate). (A direct,
  context-managed file write — `open(path) as f: f.write(...)` — does not leak and may
  carry its path in `transport_schema`; the hazard is specifically the persistent handler
  left on the global logger.)
- **Backend-SDK emission.** Any transport requiring an SDK forbidden in
  handler-internal imports (HTTP, queue clients, DB drivers, gRPC, LLM SDKs). The
  hook MUST declare exactly one entry in `service_bindings` and route emission
  through `services.<name>.invoke(...)` — the same channel a service handler uses.
  The hook's own `transport_schema` is empty-but-present in this pattern.

The two emission cases are channel-level distinctions, not handler-level
exclusions: a hook that emits via both stdlib AND backend-SDK declares the
service-typed binding (because the backend-SDK case requires it) and may also emit
via stdlib alongside. In the mixed case the hook's `transport_schema` carries the
stdlib-side config — non-empty, per the stdlib-emission rule — and the
`service_bindings` entry's bound service-type carries the backend-SDK transport.
The "empty-but-present `transport_schema`" rule applies only to the **pure**
backend-SDK case where the hook does no stdlib emission.

Hooks do not host training capture. In mainstream ML frameworks the
callback/hook IS the training-capture point (a trainer callback that logs
samples, a framework hook that collects pairs), so an agent priming on those
paradigms will reach for a hook here — that pattern lands wrong in Conjured:
training capture is the [trainable](#trainable) composition kind's exclusive
role, and a hook's `service_bindings` entry binds for emission only.

Hook error handling is a subcategory of the no-out-channel role: no downstream
channel depends on a hook's emission, so a swallowed operational failure cannot
corrupt a downstream read — structural type-check failures, by contrast, are
graph-shape errors, not in-flight operational noise. The bounded two-case
disposition itself (which classes the runner's hook wrapper tolerates and which
still halt) is owned at [R-error-channel-003](#R-error-channel-003).

Conditional emission is sanctioned: a hook MAY decide per-dispatch, based on its
declared reads, whether to emit. The whether-to-emit boolean is the only
observation-driven in-body conditional the engine sanctions; it is safe because no
write follows.

---

{#binding-discipline}
## Binding discipline

Handler inputs flow through two declared axes, separated by **when** they bind to
the node:

- **[Compose-time bindings](#compose-time-binding)** are
  declared via `bindings.<name>` and resolved once at pipeline-composition time.
  The pipeline-entry supplies each binding's value inline or via an external
  declaration file path; the engine resolves and validates each value at compose
  and supplies the handler a fresh per-dispatch copy of it at every dispatch (large
  static read-only data opts out of copying via the
  [reference binding](#reference-binding) subtype). Same
  composition produces the same bindings, fixed across every dispatch of this
  composed pipeline.
- **[Dispatch-time bindings](#dispatch-time-binding)** are
  supplied as kwargs each time the runner invokes the handler during a pipeline
  run. They carry the input-port values the runner projects from each port's
  read-map-wired channel at this node's position (`reads`), plus the `services`
  proxy where the handler declares a `service_bindings` entry.

{#bare-kwarg-only-functions}
### Bare kwarg-only functions

Handlers in the transform, service, and hook kinds are
**bare kwarg-only functions**: the
author writes one function with kwargs covering the declared `reads` and
`bindings.<name>` (plus `services`, and a hook's declared `transport_schema`
fields, where applicable); the engine constructs the
dispatch wrapper at compose time and, at each dispatch, supplies the handler a
fresh per-dispatch copy of each resolved `bindings.<name>` value alongside the
projected `reads`. The function-shape check at handler resolution rejects all
non-bare-function shapes (R-handler-bare-function, the
[trust-model](#trust-model-vector) vector-2 seal); the exhaustive per-shape
[admit/reject conformance set](#function-shape-predicate/conformance-set) for the
predicate is fixed at handler resolution.

The author-side signature differs by kind:

```python
# Transform — kwargs cover [reads] and [bindings.<name>]; no services
# kwarg ([service_bindings] is kind-disciplined out on transforms).
def my_transform(*, X, Y, Z, config):
    # X, Y, Z populated from [reads] at dispatch (per-dispatch copies)
    # config is a per-dispatch copy of the compose-resolved binding
...
    return {"out_field":...}


# Service — kwargs cover [reads] + [bindings.<name>] + services (the
# [service_bindings] entry is the handler's external-call edge; exactly
# one service binding required).
def my_service(*, X, Y, Z, config, services):
    result = services.my_backend.invoke(...)
    return {"out_field": result}


# Hook (stdlib emission) — kwargs cover [reads] + [bindings.<name>] + the
# hook's [transport_schema] fields (deployment-supplied, delivered like
# bindings); no services kwarg because [service_bindings] is empty.
def my_hook_stdlib(*, X, Y, Z, config, log_path):
    # emits via stdlib calls (file write, logger, stdout)
...
    return None


# Hook (backend-SDK emission) — kwargs cover [reads] + [bindings.<name>]
# + services because the hook declares an entry in [service_bindings]
# to route emission through a backend SDK.
def my_hook_webhook(*, X, Y, Z, config, services):
    services.my_webhook.invoke(...)
    return None
```

{#engine-side-dispatch-construction}
### Engine-side dispatch construction

At pipeline compose time, the engine performs dispatch construction uniformly
across the three bare-function kinds:

```python
# Bare-function (transform / service / hook) — pseudocode:
# 1. Resolve the author function via handler resolution (dotted-path
#    or entry-points). The function-shape check (R-handler-bare-function)
#    rejects every non-bare-function shape per the conformance set fixed
#    at handler resolution.
# 2. Run the R-handler-pure-module AST audit on the module source
#    BEFORE import.
# 3. Resolve each [bindings.<name>] value (inline or by external
#    declaration file path) and validate it against the binding's
#    declared schema; store the resolved value on the composed node.
# 4. Construct the engine dispatch wrapper around the bare handler —
#    a callable that, on every invocation, assembles the handler's
#    kwargs and runs input-validation -> handler-call -> output-validation.
# 5. At each dispatch the runner assembles the kwargs: a fresh
#    per-dispatch copy of each resolved [bindings.<name>] value, the
#    [reads] projection (each input port copied out of its read-map-wired
#    channel in the graph at this node's position), and `services` for
#    service-kind / SDK-emission hooks. The input validator (Pydantic
#    model from declared reads) validates the projection; the output
#    validator (Pydantic model from declared [output_schema]) validates
#    the returned dict — for transforms and services; hooks return None
#    and skip the output validator. A handler mutating any kwarg mutates
#    only its private copy, discarded when the dispatch returns
#    (trust-model vector 4).
```

Authors do not construct dispatch callables themselves. The engine's compose-time
path is the only route to admit a handler into the graph; node-level type-check
happens exactly once at this single auditable boundary.

This compose-time path is the graph's **type-check seam**: it is where each node's
declared interface is realized as the input / output validators the runner invokes
at dispatch. R-handler-001 (engine-constructed dispatch wrapper) anchors here, and
the wrappers' input / output validation is what makes the dispatch-boundary
read/write surface for [I1 (no implicit contracts)](#invariants-and-derived-rules)
and [I3 (engine purity)](#invariants-and-derived-rules)
**mechanically enforced rather than disciplinary** — a handler author cannot ship a
dispatch callable that bypasses validation because they do not author the dispatch
callable. The rest of I1's claim — external side effects, hidden writes,
non-determinism inside handler bodies — remains review-enforced; the runner has no
body-level visibility, and adversarial review catches body-level violations
(R-handler-002 no silent fallbacks, R-handler-004 transform purity). For hooks,
the runner's hook wrapper (R-error-channel-003, halt semantics) surrounds the
engine-constructed dispatch to translate operational PipelineFailure into "log and
continue."

{#compose-time-axis}
### Compose-time axis

Resolved at pipeline-composition time and supplied to each dispatch as a fresh
per-dispatch copy:

- **`bindings.<name>`** — handler-author-declared compose-time bindings. Each
  declared binding section is a schema; the pipeline-entry supplies the binding's
  value inline or by external declaration file path; the engine resolves and
  validates both forms at compose and hands the handler a fresh copy of the value
  at each dispatch. Authors name bindings by domain meaning. The `bindings.<name> compile = "..."` directive is a
  sub-form: the engine resolves a named compiler at binding
  resolution and delivers the produced artifact as an engine-owned kwarg (the handler
  reference's compile-directive sub-form owns the model).
- **`service_bindings`** — service-typed bindings declaring the service-types the
  handler reaches via the `services` proxy at dispatch. The binding resolves at
  compose time (the bound service-type is captured in the dispatch wrapper); the
  per-dispatch invocation goes through the proxy.

Compose-time bindings on channel-writing kinds contribute to the
[pipeline-hash](#pipeline-hash) — with the hook exclusion the
[hash-model](#what-the-pipeline-hash-absorbs/family-rule) owns (a hook carries no identity, so
nothing it declares is hashed); a re-composition with different binding values,
service-binding identity values, or `compile`-directive declarations produces a
different pipeline (the hash covers the declaration, never the compiled artifact
derived from it).

{#dispatch-time-axis}
### Dispatch-time axis

Supplied as kwargs to the handler at each invocation:

- **`reads`** — every input-port name appears as a kwarg-only parameter on the
  handler's signature. The runner builds the kwarg dict by projecting each input
  port's read-map-wired channel value from the graph at this node's position, each
  as a fresh per-dispatch copy — so a handler mutating a read value cannot affect
  the channel or another reader of it; the handler accesses each read by parameter
  name.
- **`services` kwarg** — service-kind handlers and hooks routing through
  service-typed bindings declare it; transforms MUST NOT. The runner constructs a
  [ServicesProxy](#servicesproxy) at dispatch and supplies
  it as `services=<proxy>`; backend SDK access lives behind
  `services.<name>.invoke(...)`.

{#exhaustive-dispatch-surface}
### Exhaustive dispatch surface

A handler's dispatch surface is exhaustively its kwarg signature plus its return —
the only edges the node has into and out of the graph. The runner projects only
declared `reads` input ports into the kwarg dict (each from its read-map-wired
channel) and routes only declared `output_schema` output ports back onto the graph
(each onto its write-map-wired channel); undeclared fields never reach the handler
body, and undeclared returns are rejected at the type-check seam. Transforms and
services emit channels via the return dict (keys match the declared `output_schema`
output ports); hooks return `None` and write no channels.

This dispatch-boundary kwarg assembly is what makes I1 (no implicit contracts)
and I3 (engine purity) **mechanically enforced rather than disciplinary**: an
author reaching for an undeclared field gets a Python `NameError` before the
handler body executes any logic, and an undeclared write fails the output
validator at dispatch return.

---

{#adding-a-new-kind}
## Adding a new kind

The engine provides no extension hook for new kinds. Adding a kind beyond the
current closed-enum membership is an engine change accompanied by an architecture
decision that documents the load-bearing node role it introduces, why the existing
kinds cannot satisfy the use case (with worked examples), and the dispatch /
failure / projection semantics the new kind would carry. A candidate's first
question is *which layer it belongs to* — a new node role at the handler-kind
layer, or composition-layer machinery (engine-owned dispatch, scoped channels) at
the composition layer.

Cases that have surfaced and resolved without adding a new kind:

- **Validation handlers.** A "validation handler" fits the existing transform
  kind — node role (writes channels via return dict, no external call) matches;
  no composition-layer machinery needed. Concrete shape: a transform writing a
  structured-verdict channel (`{valid: true | false, reasons: [...]}`). (Distinct
  from a [validator](#validator) — the field-level value constraint attached to a
  declared field.)
- **Cache layers.** A "cache handler" fits the existing service kind — node role
  (writes channels, exactly one external call to the cache backend) matches; the
  impl resolves hit vs miss internally and writes a signal+data pair via its
  declared [output ports](#output-port).
- **Write-and-observe handlers.**

  :::{region} adding-a-new-kind/write-and-observe-bundle
  A node that both writes channels AND emits to an
  observability destination composes as a **pair of existing kinds** — the
  channel-writing handler (transform or service) followed by a companion
  [hook](#hook) reading the written channel — never as one dual-role handler:
  a hook is the observer node role (writes no channels), and a channel-writing
  kind's emissions are exactly its declared writes, so the two roles stay
  structurally separate and the training capture stays clean. The
  [bundle TOML](#bundle-toml) names the reusable unit: one composition
  declaring the [handler + companion hook] pair, embedded wherever the pair is
  wanted.
  :::
- **Retry / branching / fan-out.** Consumer multi-pipeline orchestration, not an
  engine concern at either layer — composes at a scale outside the engine's
  contract. See the
  [engine / consumer / review partition](#engine-consumer-review-partition)
  meta-rule for the duplication-collapse test that explains why this lands on the
  consumer side, and the [corpus scope](#corpus-scope)
  for what the rule corpus binds.

Machine-readable schemas live alongside the handler component reference, so an
authoring tool or LLM can author conformant
declarations from the schema rather than from prose; they ship in the
in-package agent surface alongside the filtered docs bundle.
