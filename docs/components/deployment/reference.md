---
kind: reference
audience: [integrators, agents]
slug: deployment-reference
component: deployment
---

{#deployment-reference}
# Deployment-declaration reference

The per-component reference for the **deployment declaration** — the integrator-authored TOML that
wires a composed [pipeline](#pipeline) to a concrete environment. A
[service binding](#identity-service-binding) names an *intent* ("this node calls an LLM"); the
deployment declaration wires that intent to a concrete backend through the binding's
[transport](#transport) — the endpoint, credential reference, timeout, and headers that reach the
backend, plus the per-hook sinks and the deployment's integrity-enforcement opt-in. The split between
*what the pipeline is* and *where it runs* — identity on the hashed side, transport on the
never-hashed side — is owned by the glossary entries for [transport](#transport) and
[identity service binding](#identity-service-binding); this doc is where that split's transport half
is *written*.

The deployment declaration is a first-class engine-read declaration class: the
[declaration validator](#architecture-components) loads it alongside handler, service-type, pipeline,
and composition declarations.

What lives here:

- The **deployment-TOML grammar** — the closed set of sections an integrator writes to wire a
  pipeline to an environment.
- The **one-deployment-per-engine wiring model** — how a single deployment declaration serves every
  pipeline an engine process runs, by **sharing** transport per binding name, with an optional
  **per-pipeline override** where one pipeline diverges.
- The component's **derived rules** (the R-deployment-* set).

What is **owned elsewhere and cross-referenced, never restated here**:

- The **`[transport_schema]`** each `transport.<name>` block validates against, and the
  binding-handle → backend **wiring model** the deployment realizes, are owned by the service-type
  reference's § `[transport_schema]` and § Binding-handle → backend wiring.
- The **hook `transport_schema`** each `hook_transport` block validates against is owned by the
  handler reference's § `transport_schema` — applicable to hooks only.
- The **coverage cross-checks** — every service-typed binding the engine composes needs a
  `transport.<name>` block, every hook
  needs a `hook_transport."<as_written_node_name>"` block — are owned by the pipeline reference's R-pipeline-001
  (**Transport coverage** + **Hook transport coverage**) and run at pipeline-declaration load.
- The **section-discipline modes** are owned by
  [exhaustive declaration](#architecture-exhaustive-declaration).
- The **integrity-enforcement opt-in** semantics, the **never-hashed** exclusion of every deployment
  section, and the **`acknowledged_drift`** value-space are owned by
  [hash-model](#architecture-hash-model).
- The **audit-stamp mechanism** the `audit_enforcement` opt-in gates is owned by the handler
  reference's [audit-stamps section](#audit-stamps/kernel).
- The **timeout-surface rule** is owned by the error-channel reference's § Timeouts.

---

{#deployment-toml-grammar}
## Deployment-TOML grammar

A deployment declaration is a TOML document with a small closed set of top-level sections. A section
outside this set raises [ContractViolation](#contractviolation) at deployment load.

```toml
# A deployment declaration (illustrative). One per engine process; shared by every
# pipeline the engine runs, resolved against each pipeline by binding name.

[transport.llm]                                  # one per service-typed binding; never hashed
endpoint    = "https://llm.prod.internal/v1"
api_key_ref = "[env]LLM_PROD_KEY"
timeout_ms  = 30000

[hook_transport."mypkg.audit_log"]               # one per hook in the pipeline's nodes; never hashed
path = "/var/log/conjured/audit.jsonl"

[training_contract]                              # required, body-required; the integrity opt-in
integrity_enforcement = true

[training_export]                                # presence-is-the-signal: PRESENCE routes capture; OMITTING
                                                 #   the section routes none (no error). Sink is consumer-attached.

[artifacts]                                      # truly optional; per-trainable artifact registration
"mypkg.dialogue_trainable" = "loras/alice_dialogue.safetensors"

[acknowledged_drift]                             # truly optional; per-artifact, per-trainable
"loras/alice_dialogue.safetensors" = ["mypkg.dialogue_trainable"]

[annotations]                                    # truly optional; engine-opaque consumer surface
notes = "Production wiring for the dialogue engine."

# per-pipeline override — only where one pipeline diverges from the shared wiring
[pipelines."mypkg.experimental_npc".transport.llm]
endpoint = "https://llm.canary.internal/v1"
```

The sections divide into **wiring** (`transport.<name>`, `hook_transport."<as_written_node_name>"`, and the
`pipelines.<name>` override that re-wires them per pipeline) and **environment posture**
(`training_contract`, `training_export`, `artifacts`, `acknowledged_drift`, `annotations` —
posture sections take no per-pipeline override). Every section is
**never hashed** — see § Hash placement.

{#transport-section}
### `transport.<name>`

**One block per service-typed binding the engine composes.** Supplies the per-deployment
[transport](#transport) for the
binding handle `<name>` — endpoint URL, credential reference, timeout, headers. The block is
**key-checked against the bound service-type's `[transport_schema]`** — presence coverage plus
no-unknown-fields, never value/type validation beyond the two reserved shapes the engine itself
interprets: a field not declared
there raises
[ContractViolation](#contractviolation), while the declared fields' VALUES pass through opaque
(the service-transport values are the `**transport_extra` passthrough the engine forwards but
does not read; the pipeline reference's R-pipeline-001 **Transport coverage** owns the
cross-check, and the service-type reference owns the schema and the identity/transport/config
split). The two value shapes the engine DOES read: the reserved
[explicit-null form](#binding-value-supply-grammar/explicit-null) `{ null = true }` — its
recognition and normalization are that law's; the uniform presence-coverage a nullable field
satisfies with it is R-pipeline-001 **Transport coverage**'s — and the
[secret-reference grammar](#secret-references/grammar) of a `secret_ref`-declared field's value
(shape-checked at load, forwarded unresolved — [§ Secret references](#secret-references),
R-deployment-003). The block reaches the service implementation as
`**transport_extra` on the dispatch and is **never** contributed to any hash. (Contrast the
[hook arm](#hook-transport-section): hook transport fields ARE engine-read — delivered as
kwargs into the hook body — so they type-match on delivery.)

The binding name `<name>` is the **as-written binding handle** — the handle a handler declares
in `service_bindings` and a pipeline `service_bindings.<name>` block or a trainable
composition's `[service_bindings.<name>]` block supplies identity for. Across the pipelines one
engine serves, a `transport.<name>`
block is **shared by that binding name** — see § One deployment per engine. That every supplied field
must be a declared `transport_schema` field, and that every service-typed binding the engine
composes must have a covering
block, is the pipeline reference's R-pipeline-001 **Transport coverage** + **Identity/transport
placement**; this doc does not restate the cross-check.

{#hook-transport-section}
### `hook_transport."<as_written_node_name>"`

**The per-deployment transport for each hook node** — a stdlib-emission hook's log-file path or
formatter selector, for instance. Its fields are the hook's `transport_schema` fields (defined in the
handler reference's § `transport_schema` — applicable to hooks only):

:::{transclude} R-pipeline-001/hook-transport-coverage
:::

Hook transport is **never hashed**.

Delivery follows the emission boundary: this block's values reach the hook **body** as
kwargs, exactly like bindings (the handler reference's § `transport_schema` owns the
delivery rule). A backend-SDK-emission hook's bound service-type transport never rides
this block — it rides the binding's `transport.<name>` block to the adapter, exactly as
for a service handler's binding.

The block key is the hook's **as-written pipeline node name** quoted as a TOML key
(`hook_transport."mypkg.audit_log"`) — the same as-written label the pipeline node carries.
It MAY carry dots — a dotted-path
reference and a short entry-points name are each taken verbatim, as-written — which a bare TOML
key would parse as nested tables, hence the quoting.

{#training-contract-section}
### `training_contract`

**Required, body-required.** The deployment's enforcement-posture section: the opt-in to
[pipeline-as-training-contract](#glossary-pipeline-as-training-contract) enforcement
(`integrity_enforcement`, required) and the opt-in to audit-stamp enforcement
(`audit_enforcement`, optional):

```toml
[training_contract]
integrity_enforcement = true   # required — an explicit boolean
audit_enforcement     = true   # optional boolean; defaults to false
```

:::{region} training-contract-section/required-body-required
The closed-shape key MUST appear AND `integrity_enforcement` MUST carry an explicit boolean; a
missing `[training_contract]` block, an empty
body, or a missing field is [ContractViolation](#contractviolation) at deployment load.
:::

Whether a
deployment enforces integrity is a load-bearing affirmative-or-negative choice with no meaningful
"declared nothing" state — the
[integrity-enforcement opt-in](#integrity-enforcement-opt-in) in hash-model owns the full semantics
(the separation of the always-available integrity *property* from the opt-in *enforcement*, and the
graduated halt/warn behavior on hash mismatch). This doc does not restate that behavior. The
declaration is **never hashed** — the enforcement choice is an environment property, not a composition
property.

:::{region} training-contract-section/audit-enforcement
`audit_enforcement` is an **optional boolean defaulting to `false`** — the deployment's opt-in to
audit-stamp enforcement. Its semantics — the stamp mechanism, the freshness states, what
enforcement refuses — are owned by the handler reference's
[audit-stamp mechanism](#audit-stamps/kernel); this doc does not restate that behavior. Like every
deployment section, the declaration is never hashed.
:::

{#training-export-section}
### `training_export`

**Presence-is-the-signal.** Its presence toggles training-capture routing for the environment; its
absence means no first-party capture route is configured (empty-allowed when present — presence
alone is the opt-in). It is **never hashed**. Capture is **opt-in by presence**: a deployment
that wants the training corpus MUST declare the section. Because `training_export` is
presence-is-the-signal — not a required section, so its omission is the one
[R-deployment-001](#R-deployment-001) does **not** raise on — omitting it is accepted, not an
error; it simply routes no capture, so capture never flows "automatically" from a deployment
that never declared it. The capture *transport*
itself — the sink format and destination machinery — is consumer territory: the engine publishes
[canonical events](#canonical-event) and consumers attach their own routing (see the
[components](#architecture-components) view). This doc documents only that the section exists, is
presence-is-the-signal, and toggles routing; the routing semantics are owned by
[exhaustive declaration](#architecture-exhaustive-declaration) and
[hash-model](#architecture-hash-model).

{#artifacts-section}
### `artifacts`

**Truly optional.** The trained-artifact registration surface — the declaration the
phrase "the deployment declared an artifact load" resolves to. An entry maps a
[trainable](#trainable) composition's declared name (the trained-artifact-manifest key —
[hash-model § manifest-key shape](#manifest-key-shape)) to the artifact file this
environment serves it from:

```toml
[artifacts]
"mypkg.dialogue_trainable" = "loras/alice_dialogue.safetensors"
```

The engine never reads the artifact file itself — the serving runtime hosts the weights,
and the bound service-type's identity names what it serves. What the engine reads is the
artifact's **sidecar [trained-artifact manifest](#trained-artifact-manifest)**, resolved
by the pipeline reference's naming convention (`<artifact>.conjured.toml` beside the
artifact, the path resolved relative to the deployment declaration's own directory), and
compares its recorded hashes against the deployed composition at load — the integrity
surface [hash-model § integrity-enforcement opt-in](#integrity-enforcement-opt-in) owns:
drift events fire on shift in both enforcement modes; halts are enforcement-gated and
graduated there. A declared entry whose sidecar is missing halts under
`integrity_enforcement = true` and is the no-baseline case (no comparison, no event)
with enforcement off — hash-model's enforcement modes own that split. A declared
trainable name that matches no trainable composition node in the deployed pipeline
raises [ContractViolation](#contractviolation) at load (a registration that can never be
compared is a wiring mistake, not a no-op). Absence of the whole section means the
environment declares no artifact loads — nothing to compare (the stock-model case).
Artifact paths are per-environment; the section is **never hashed** and, as an
environment-posture section, takes no `pipelines.<name>` override.

{#acknowledged-drift-section}
### `acknowledged_drift`

**Truly optional.** The mechanism for explicitly accepting a known hash mismatch under integrity
enforcement; absence means no acknowledgments. An entry maps an **artifact** to the
[trainable](#trainable)s whose drift it accepts — the author-facing rendering is a table keyed by
artifact path, valued by the list of trainable qualified names:

```toml
[acknowledged_drift]
"loras/alice_dialogue.safetensors" = ["mypkg.dialogue_trainable"]
```

The value-space and the per-trainable discipline are owned by hash-model's
[integrity-enforcement opt-in](#integrity-enforcement-opt-in); under
`integrity_enforcement = false` these entries are ignored (no enforcement to acknowledge against).
The section is **never hashed**.

{#deployment-annotations-section}
### `annotations`

**Truly optional.** Free-form author notes for the deployment, like the
[annotations](#annotations) block on every declaration class: **engine-opaque** — graph-inert,
excluded from every hash, never read by the engine — purely a consumer surface for human- and
tooling-facing metadata. Omit it entirely or include it; either is valid.

{#pipelines-override-section}
### `pipelines.<name>` — per-pipeline override

**Truly optional.** Re-wires a shared `transport.<name>` or `hook_transport."<as_written_node_name>"` block for a single
named pipeline, where that pipeline diverges from the environment's shared wiring — a staging variant
hitting a canary endpoint, a pipeline whose binding points at a different backend than its siblings.
The `<name>` is the qualified name of the pipeline the override applies to:

```toml
[pipelines."mypkg.experimental_npc".transport.llm]
endpoint = "https://llm.canary.internal/v1"

[pipelines."mypkg.experimental_npc".hook_transport."mypkg.audit_log"]
path = "/var/log/conjured/experimental.jsonl"
```

An override block re-declares only the binding(s) or hook(s) that diverge; an overridden block is
validated exactly as the shared block it replaces — the service arm key-checked, the hook arm
strict-validated per its `transport_schema` (the [`transport.<name>` contract](#transport-section)
owns the split). An override
naming a binding or hook the named pipeline does not declare is a misconfiguration — see
R-deployment-002. Only `transport` / `hook_transport` accept per-pipeline override; the
environment-posture sections (`training_contract`, `training_export`, `acknowledged_drift`) are
deployment-wide.

---

{#shared-by-binding-wiring}
## One deployment per engine — shared-by-binding wiring

A deployment declaration is the **environment configuration of one engine process** — the
[deployment story](#architecture-components) is to run one engine, and that engine runs under one
deployment declaration, supplied to it at startup. The pipelines an engine serves — including those a
consumer live-compiles and submits at invocation — all resolve their wiring against this single
deployment.

Transport is keyed by the as-written **binding handle**, not by pipeline. A pipeline's service
binding `llm` resolves
to the deployment's `transport.llm` block; a second pipeline that also names a binding `llm` resolves
to the **same** block; an embedded trainable composition's `[service_bindings.llm]` handle resolves
within its composing pipeline's scope the same way. This is the wiring model the service-type
reference's § Binding-handle →
backend wiring describes — two pipelines naming `llm` are independent handles, each local to its
composing pipeline's scope, and the
deployment wires each, to the same backend (the shared block) or, where one diverges, to its own (a
`pipelines.<name>` override). Because transport is keyed by handle and a handle is local to its
composing pipeline's scope, the count of `transport.<name>` blocks tracks the count of **distinct
backends**, not the
count of pipelines: an engine serving a hundred compositions that all bind `llm` to one backend wires
that backend once.

Resolution for each service binding and hook in a composing pipeline is deterministic: a
`pipelines."<name>"` override for that pipeline if one is declared, otherwise the shared block. The
resolved transport is then subject to the pipeline reference's R-pipeline-001 coverage — every binding
and hook must resolve to a covering block, or the pipeline raises
[ContractViolation](#contractviolation) at load. A different environment (staging vs production) is a
different deployment declaration run by a different engine process; nothing in a deployment declaration
selects between environments.

How the engine *receives* its deployment declaration at startup — a path argument, an environment
variable, a launch convention — is an integration concern, not part of the declaration grammar.

---

{#deployment-hash-placement}
## Hash placement

**Every section of a deployment declaration is excluded from both hashes.** `transport.*`,
`hook_transport.*`, `training_contract`, `training_export`, and `acknowledged_drift` are
environment-specific — they may change from staging to production, or toggle enforcement, without
changing what any pipeline *is* — and `annotations` is engine-opaque metadata. Moving a deployment
from one environment to another, flipping `integrity_enforcement`, or editing an endpoint shifts
neither the [pipeline-hash](#pipeline-hash) nor any
[training-bundle-hash](#training-bundle-hash). The absorb/exclude authority is hash-model's
[§ What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs); this doc states the rule and cites
it rather than re-deriving the hash model.

The integrity opt-in lives in the deployment declaration for this same reason — enforcement is an
environment decision, not a composition one — and hash-model's
[integrity-enforcement opt-in](#integrity-enforcement-opt-in) owns why that separation is load-bearing.

---

{#deployment-coverage-checks}
## Pipeline-side coverage checks

A deployment declaration is validated in two stages. Its **own shape** — the closed section set and
`training_contract`'s required boolean — is checked at deployment load, standalone (R-deployment-001).
Its **coverage** against a pipeline — that every service-typed binding has a resolving
`transport.<name>` block and every hook has a resolving `hook_transport."<as_written_node_name>"` block, each
validated per its arm's contract (service transport key-checked, hook transport
strict-validated — the [`transport.<name>` contract](#transport-section) owns the
split) — is checked at **pipeline-declaration load**, when the engine
pairs the deployment to the composing pipeline. The coverage cross-check is owned by the pipeline
reference's R-pipeline-001 (**Transport coverage** + **Hook transport coverage**); a binding or hook
with no resolving block raises [ContractViolation](#contractviolation) before any node dispatches.
This doc does not restate the coverage rule — it documents only the deployment side of the pair (the
blocks that satisfy it).

---

{#deployment-timeout-surfaces}
## Timeout surfaces

A `transport.<binding>` block is one of the engine's declared timeout surfaces: a per-call
service-binding timeout is declared as a field in the bound service-type's `[transport_schema]` and
supplied here per deployment, where it rides to the service-type adapter as `**transport_extra` and is
applied to the outbound call. The timeout-surface rule — which declaration locations may carry a
timeout, and why a timeout must be transport (never hashed) rather than identity (hashed) — is owned by
the error-channel reference's § Timeouts; this doc cites it rather than enumerating timeout fields. A
timeout that varies per environment belongs here; the whole-run budget is a separate surface (the
`timeout_ms` parameter on the API call to the engine), not a deployment field.

---

{#secret-references}
## Secret references

**A raw secret value never appears in any declaration file.** A transport field that carries a
credential is declared with the `secret_ref` type (the channel-field token set's transport-only
member — handler reference § Types allowed in `reads` and `output_schema` owns the token grammar;
the service-type reference's § `[transport_schema]` states the admission), and its deployment value
is a **secret reference**: an instruction for *where* the consuming implementation fetches the
secret at dispatch, never the secret itself. The engine validates the reference's shape at
pipeline-declaration load and forwards it opaque — **the engine never fetches**. Resolution happens
in the consuming service implementation or hook body at the I/O boundary, via the blessed resolver
(`conjured.adapters.secret_refs.resolve_secret_ref`), so a resolved secret value exists only inside
the consuming call frame — never in engine state, on a channel, or in capture, events, or error
text.

:::{region} secret-references/grammar
A secret reference is the **whole value** of a `secret_ref`-declared field: `[scheme]payload`,
split at the first `]` — `scheme` names the store, `payload` is **verbatim** (no trimming, no case
normalization; any characters, so paths, ARNs, and URLs pass untouched). A value that does not
match the form, or that names an unknown scheme, raises ContractViolation
(`secret-ref-malformed` / `secret-ref-scheme-unknown`) at pipeline-declaration load — a malformed
reference (a pasted raw credential included) never reaches a dispatch. A nullable-declared field
(`secret_ref | None`) accepts the [explicit null](#binding-value-supply-grammar/explicit-null)
`{ null = true }` instead — the considered no-credential state (an unauthenticated local endpoint).
:::

**The scheme set.** A **bare** scheme is engine-owned — the closed zero-dependency built-ins:

- `[env]NAME` — environment variable `NAME`, name verbatim (no case-folding). Unset or empty fails
  loud at dispatch.
- `[file]PATH` — the file's UTF-8 text with exactly one trailing newline stripped (mounted secret
  files are conventionally newline-terminated; a trailing newline inside a credential is never
  intended). Missing, unreadable, or empty-after-strip fails loud at dispatch. Covers
  `/run/secrets` mounts and every platform that materializes secrets as files.

A **namespaced (dotted) scheme IS the qualified name of a consumer resolver** — a callable
`(payload: str) -> str` the consumer ships for any other store (a vault, a cloud secret manager, an
internal config service): `api_key_ref = "[acme_secrets.vault_resolver]prod/llm"`. The same
bare-is-engine-owned / dotted-is-third-party split the validation-keyword grammar uses
(handler reference § Validators), so there is no resolver registry to configure and a built-in can
never be shadowed. The qualified name must import to a callable at pipeline-declaration load
(`secret-resolver-invalid`); the callable returns the secret string or raises — never a default.
Platforms that already land secrets in the environment or in mounted files (compose, Kubernetes,
ECS) need no consumer resolver at all.

**The failure split — shape early, availability late.** Shape problems (the grammar, an unknown
scheme, an unimportable consumer resolver) are compose-time ContractViolations
(R-deployment-003); store problems (an unset variable, a missing file, a resolver raising) fail at
dispatch as `SecretResolutionError`, riding raw to the runner's PipelineFailure wrap exactly as the
wire errors do. No failure maps to a default, and no error message embeds a resolved value.
Resolution is **per-dispatch** — rotation needs no engine support.

**Declaring and supplying** — `secret_ref` is a field *type*, not a field name; a schema declares
any number of secret-reference fields under author-chosen names (the blessed naming convention is
`<credential>_ref`):

```toml
# the bound service-type's schema — each secret_ref field states what the store must hold
[transport_schema]
endpoint    = { type = "str" }
api_key_ref = { type = "secret_ref | None", nullable = true }   # the BARE token; the adapter renders `Authorization: Bearer`
webhook_ref = { type = "secret_ref" }                           # the COMPLETE signing header value; the adapter sends it as-is
timeout_ms  = { type = "int" }
```

```toml
# deployment.prod.toml
[transport.llm]
endpoint    = "https://llm.prod.internal/v1"
api_key_ref = "[env]LLM_PROD_KEY"
webhook_ref = "[file]/run/secrets/webhook_token"
timeout_ms  = 30000

# an unauthenticated local deployment spells the no-credential state, never omits the field
[pipelines."acme.dev".transport.llm]
endpoint    = "http://localhost:8080/v1"
api_key_ref = { null = true }
webhook_ref = "[env]WEBHOOK_DEV_TOKEN"
timeout_ms  = 30000
```

:::{region} secret-references/collection-rule
**Authoring discipline.** A credential never rides inside a collection value (a headers dict, a
list) — it gets its own declared `secret_ref` line. This is grammar, not guidance: the `secret_ref`
token is top-level-only, so `dict[str, secret_ref]` does not parse, and the blessed members give
credentials dedicated fields (a raw-credential paste is unrepresentable rather than detected).
The type is admitted **only** in `[transport_schema]` sections (services and hooks alike) — a
secret has no place in hashed identity/config or in channel dataflow.
:::

{#what-the-store-holds}
### What the store holds

:::{region} what-the-store-holds/kernel
**What the store holds is the declaring field's contract — and the field declares it.** *What the
stored secret must contain* — a bare credential the consuming implementation renders into a wire
format, or a complete pre-rendered value it passes through untouched — is decided by the field that
declares it, and **every `secret_ref` field's declaration states which**.
:::

Both shapes are legitimate,
and which one a field takes follows from its job: a field bound to **one known wire form** renders it
(`api_key_ref` on an OpenAI-compatible member — the
[family rendering convention](#http-member-conventions/kernel): the store holds the **bare token**, and
the adapter emits `Authorization: Bearer <token>`), while a field that must serve **any** authentication scheme
cannot (`auth_value_ref` on a generic HTTP member — the store holds the **complete header value**,
prefix included, because the adapter cannot know whether the target wants `Bearer …`, a bare key, or
something else). A `secret_ref` field whose declaration leaves this unstated is **under-declared**:
the consumer filling the store has to guess, and a wrong guess authenticates nothing — failing at the
remote end, not at the gate.

---

{#deployment-worked-example}
## Worked example

A deployment wiring a small dialogue pipeline's `llm` service binding and `audit_log` hook, consistent
with the service-type reference's worked example (the same `acme_llm.structured_output` shape).

```toml
# pipeline declaration — supplies identity for the binding-handle "llm" (hashed)
[service_bindings.llm]
type            = "acme_llm.structured_output"
model           = "qwen3.5-4b-gguf"
prompt_template = "dialogue_v3"

# nodes include a hook "mypkg.audit_log" (a stdlib-emission hook with a log-path transport_schema)
```

```toml
# deployment.prod.toml — the environment one engine process runs under.
# Shared by every pipeline this engine serves; resolved by binding name at pipeline load.

[transport.llm]                                  # wires the "llm" handle to a concrete backend
endpoint    = "https://llm.prod.internal/v1"     # validated against acme_llm.structured_output's
api_key_ref = "[env]LLM_PROD_KEY"                #   [transport_schema]; never hashed — a secret REFERENCE (§ Secret references)
timeout_ms  = 30000

[hook_transport."mypkg.audit_log"]               # wires the audit hook's stdlib sink
path = "/var/log/conjured/audit.jsonl"

[training_contract]                              # this environment enforces the training contract
integrity_enforcement = true

[training_export]                                # presence routes capture — omit it and NO capture is
                                                 #   routed (the engine rejects nothing). Sink is consumer-attached.

[acknowledged_drift]                             # accept one known training-bundle-hash drift
"loras/alice_dialogue.safetensors" = ["mypkg.dialogue_trainable"]

[annotations]
notes = "Production dialogue engine."

# one experimental pipeline points its "llm" at a canary endpoint; everything else inherits the shared block
[pipelines."mypkg.experimental_npc".transport.llm]
endpoint = "https://llm.canary.internal/v1"
```

A second dialogue pipeline that also binds `llm` to `acme_llm.structured_output` needs **no new
deployment content** — it inherits the shared `[transport.llm]`, `[training_contract]`, and
`[training_export]`. Only divergence costs a block.

---

{#deployment-derived-rules}
## Derived rules

:::{transclude} derived-rules-convention/kernel
:::

```yaml
rules:
  - rule_id: R-deployment-001
    name: closed deployment-declaration grammar
    derived_from: [I1]
    enforcement: mechanical
    statement: |
      A deployment declaration's shape is a closed set of top-level
      sections: the wiring sections `transport.<name>` (one per
      service-typed binding) and `hook_transport."<as_written_node_name>"` (one
      per hook), the per-pipeline override section `pipelines.<name>`, and
      the environment-posture sections `training_contract` (required,
      body-required — `integrity_enforcement` MUST carry an explicit
      boolean; `audit_enforcement` is its optional boolean sibling,
      defaulting to `false`), `training_export` (presence-is-the-signal), `acknowledged_drift`
      (truly optional), and `annotations` (truly optional, engine-opaque).
      A declaration carrying an unknown top-level section, or omitting the
      required `training_contract` declaration or its `integrity_enforcement`
      boolean, raises ContractViolation at deployment load. Section-discipline
      modes are owned by exhaustive-declaration; per-block coverage (every
      binding and hook present, each block validated per its arm's
      contract — service transport key-checked plus the secret-reference
      shape check on secret_ref-declared fields (R-deployment-003), hook
      transport strict-validated) is owned
      by the pipeline reference's R-pipeline-001 and fires at pipeline-declaration
      load. Every section is excluded from both hashes per the hash-model
      exclusion (deployment values are environment properties, never
      composition properties). Load-bearing for I1: a deployment carrying an
      undeclared section, or an empty integrity choice, is asking the engine
      to honor wiring it never declared; closed-shape grammar makes that a
      rejected case, not a silently-ignored one.

  - rule_id: R-deployment-002
    name: shared-by-binding transport resolution with per-pipeline override
    derived_from: [I1, I2]
    enforcement: mechanical
    statement: |
      An engine process runs under exactly one deployment declaration; every
      pipeline it serves resolves each service-typed binding and each hook
      against that one declaration by name. Resolution is deterministic:
      a `pipelines."<name>".transport.<binding>` (or `.hook_transport."<as_written_node_name>"`)
      override applies for the named pipeline, otherwise the shared
      `transport.<binding>` (or `hook_transport."<as_written_node_name>"`) block applies;
      override takes precedence over shared. A transport block is keyed by
      the as-written binding handle. A handle is local to the composing
      pipeline's scope — declared by that pipeline's own
      `service_bindings.<name>` blocks or supplied by an embedded trainable
      composition's `[service_bindings.<name>]` — so two pipelines naming the
      same handle share one block unless one declares an override; the block
      count tracks distinct backends, not pipelines. An override MAY name any
      service-typed binding handle or hook within the named pipeline's
      composed scope, composition-supplied handles included; an override
      naming a handle or hook outside that scope raises ContractViolation when
      that pipeline composes. The resolved transport is subject to the pipeline
      reference's R-pipeline-001 coverage check; an unresolved binding or hook
      raises ContractViolation at pipeline-declaration load. Load-bearing for
      I1/I2: transport resolution is fixed by the declaration — override-over-
      shared, by name — not by load order, ambient state, or install accident,
      so the backend a binding reaches is determined at compose, deterministically.

  - rule_id: R-deployment-003
    name: secret references — validated shape, engine-never-fetches resolution
    derived_from: [I1]
    enforcement: mechanical
    statement: |
      A transport field declared `secret_ref` (the transport-schema-only
      channel-field type; optionally `secret_ref | None`) is supplied as a
      whole-value `[scheme]payload` secret reference, or as the explicit
      null on a nullable field — never a raw credential. At
      pipeline-declaration load the engine validates the SHAPE of every
      such value in the resolved transport and hook-transport blocks: a
      non-reference value raises ContractViolation
      (`secret-ref-malformed`); a bare scheme outside the closed built-in
      set {env, file} raises `secret-ref-scheme-unknown`; a namespaced
      (dotted) scheme — the qualified name of a consumer resolver
      callable (payload: str) -> str — must import to a callable, else
      `secret-resolver-invalid`. The engine NEVER fetches: the validated
      reference is forwarded opaque, and the consuming implementation or
      hook body resolves it at dispatch via the blessed resolver, where a
      store-side failure (an unset variable, a missing/empty file, a
      resolver raising) raises SecretResolutionError raw — an exception
      never maps to a default, and no error message embeds a resolved
      value. Load-bearing for I1: the shape check makes "a raw secret in
      a declaration file" a rejected case at load rather than a silently
      forwarded credential, while the never-fetch split keeps resolved
      secrets out of engine state, channels, capture, and events by
      construction.
```
