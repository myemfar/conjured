---
kind: conformance
audience: [integrators, agents]
slug: deployment-conformance
component: deployment
---

{#deployment-conformance}
# Deployment conformance checks

The mechanical conformance checks the engine fires for the [deployment](#deployment-reference) component. Both deployment-component derived rules are mechanically enforced; there is no review-enforced arm here (a deployment declaration is integrator-authored TOML the engine reads whole, not a handler body the runner cannot see).

Each entry below is structured for diagnosing a thrown error or auditing a deployment declaration against the engine's contract. The format:

- **Check name** — the mechanical check; lowercase noun phrase.
- **Rule anchor** — the derived rule the check enforces, cited by file + prose anchor.
- **Trigger** — when the check fires (deployment-declaration load, pipeline-declaration load / compose time).
- **Mechanism** — what the engine does to detect the violation.
- **Violation example** — a concrete deployment (or paired pipeline) declaration that fires the check.
- **Error class** — which of the [closed-enum classes](#error-class) the engine raises.
- **Diagnosis** — what to look for and how to fix.

---

{#deployment-mechanically-enforced-checks}
## Mechanically-enforced checks

{#closed-deployment-declaration-grammar}
### Closed deployment-declaration grammar

- **Rule anchor.** [Derived rule R-deployment-001 (closed deployment-declaration grammar)](#R-deployment-001).
- **Trigger.** Deployment-declaration load — the standalone **own-shape** stage of the two-stage validation the reference describes at [§ Pipeline-side coverage checks](#deployment-coverage-checks) (the coverage stage is R-pipeline-001's, at pipeline-declaration load).
- **Mechanism.** The [declaration validator](#architecture-components) loads the deployment declaration and enforces R-deployment-001's closed grammar: the top-level section set is closed to the wiring sections `transport.<name>` / `hook_transport."<as_written_node_name>"` / the `pipelines.<name>` override, plus the environment-posture sections `training_contract` (required, body-required — `integrity_enforcement` MUST carry an explicit boolean) / `training_export` (presence-is-the-signal) / `acknowledged_drift` / `annotations`. An unknown top-level section, a missing `training_contract` block or its `integrity_enforcement` boolean, or an empty `training_contract` body raises ContractViolation at deployment load; `training_export`'s omission is the one omission R-deployment-001 does not raise on (presence-is-the-signal). The shared grammar-family enforcement primitives (`closed-grammar`, `section-presence`, `body-required`, `malformed-declaration`) are the cross-component declaration-grammar mechanism worked in full at the handler conformance catalog's *Closed handler-declaration grammar* entry (`components/handler/conformance.md`) — the deployment declaration is another declaration class the same validator loads (per the reference's [§ Deployment-TOML grammar](#deployment-toml-grammar)); this catalog does not restate that family, only the deployment-specific closed section set R-deployment-001 owns.
- **Violation example.**

  ```toml
  # deployment.prod.toml
  [transport.llm]
  endpoint = "https://llm.prod.internal/v1"

  [retry_policy]                    # unknown top-level section — ContractViolation
  max_attempts = 3

  # (also a violation on its own: no [training_contract] block at all — the required,
  #  body-required integrity opt-in is absent)
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Reach for a declared section: per-binding wiring goes in `transport.<name>`, per-hook wiring in `hook_transport."<as_written_node_name>"`, per-pipeline divergence in a `pipelines.<name>` override, and the enforcement posture in `training_contract` / `training_export` / `acknowledged_drift` / `annotations`. Engine retry surface does not exist ([R-error-channel-002](#R-error-channel-002)); there is no `retry_policy` section. Every deployment declaration MUST carry a `training_contract` block with an explicit `integrity_enforcement` boolean — the affirmative-or-negative enforcement choice has no "declared nothing" state ([§ `training_contract`](#training-contract-section)). The complete registered check roster this entry sits in is the error-index (`reference/error-index.md`); this catalog names the deployment-specific discriminators rather than re-counting them.

{#deployment-override-target}
### Per-pipeline override targets a served binding or hook

- **Rule anchor.** [Derived rule R-deployment-002 (shared-by-binding transport resolution with per-pipeline override)](#R-deployment-002); the override-scope clause the reference writes at [§ `pipelines.<name>` — per-pipeline override](#pipelines-override-section).
- **Trigger.** Pipeline-declaration load (compose time) — when the engine pairs the process's one deployment to a composing pipeline and resolves that pipeline's overrides, alongside R-pipeline-001's transport-coverage cross-check ([§ Pipeline-side coverage checks](#deployment-coverage-checks)).
- **Mechanism.** A `pipelines."<name>".transport.<binding>` (or `.hook_transport."<as_written_node_name>"`) override block MAY name only a binding handle or hook **within the named pipeline's composed scope** — the pipeline's own `service_bindings.<name>` handles and each embedded trainable composition's `[service_bindings.<name>]` supplies (composition-supplied handles included), and the pipeline's hook nodes plus composition hook preprocessors. The engine collects the named pipeline's composed handle set and hook set, then walks each override block; an override `transport.<binding>` whose handle is not in that set, or an override `hook_transport."<as_written_node_name>"` whose hook is not among the pipeline's hooks, raises ContractViolation (check `deployment-override-target`) when that pipeline composes. Resolution is otherwise deterministic override-over-shared by name (R-deployment-002); this check guards the override *target*, not the coverage of the resolved block (that is R-pipeline-001's).
- **Violation example.**

  ```toml
  # pipeline mypkg.experimental_npc composes exactly one service binding — handle "llm"
  [service_bindings.llm]
  type = "acme_llm.structured_output"
  ```

  ```toml
  # deployment override re-wires a handle the pipeline never composes — ContractViolation
  [pipelines."mypkg.experimental_npc".transport.embedder]
  endpoint = "https://embed.canary.internal/v1"
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** An override re-wires only a binding or hook the named pipeline actually composes; its block key must match an as-written handle the pipeline declares in `service_bindings.<name>` (or one an embedded trainable composition supplies via `[service_bindings.<name>]`), or the as-written node name of a hook the pipeline runs. The fault shapes are a typo'd handle, a binding removed from the pipeline but left behind in the override, and an override block attached to the wrong pipeline's qualified name. Remove the stray override block, or correct the handle/hook to one the named pipeline serves. A binding the pipeline *does* compose but that has **no** resolving transport block at all is the distinct R-pipeline-001 transport-coverage fault (`transport-coverage-gap`), not this check.

{#deployment-secret-references-shape}
### Secret references — validated shape, engine-never-fetches resolution

- **Rule anchor.** [Derived rule R-deployment-003 (secret references — validated shape, engine-never-fetches resolution)](#R-deployment-003); the grammar and scheme set are the reference's [§ Secret references](#secret-references).
- **Trigger.** Pipeline-declaration load (compose time) — when the resolved `transport.<name>` and `hook_transport."<as_written_node_name>"` blocks are validated against their schemas, every `secret_ref`-declared field's supplied value gets the reference **shape** check (both transport arms, one shared check). The store-side FETCH is never checked here — availability is dispatch-time (`SecretResolutionError`, raw through the runner's PipelineFailure wrap).
- **Mechanism.** Three deterministic checks, one fix-shape each: a value that is not a whole-value `[scheme]payload` reference — a pasted raw credential included — raises check `secret-ref-malformed` (fix: reference the store, e.g. `"[env]LLM_PROD_KEY"`, or supply `{ null = true }` on a nullable field); a well-formed reference whose bare scheme is outside the closed built-in set `env` / `file` raises check `secret-ref-scheme-unknown` (fix: use a built-in scheme or a namespaced consumer resolver); a namespaced (dotted) scheme — the qualified name of a consumer resolver callable — that does not import to a callable raises check `secret-resolver-invalid` (fix: install/correct the resolver module path). The `secret_ref` token's *placement* law (transport-schema-only, top-level-only) is not these checks: an out-of-place token is the token grammar's own `channel-type-token` rejection at declaration load, exactly as for `table`.
- **Violation example.**

  ```toml
  # deployment.prod.toml — the bound service-type declares api_key_ref = { type = "secret_ref | None" }
  [transport.llm]
  endpoint    = "https://llm.prod.internal/v1"
  api_key_ref = "sk-live-abc123"     # a RAW credential where a reference belongs — ContractViolation
                                     # (secret-ref-malformed; never forwarded to a dispatch)
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** The value of a `secret_ref`-declared field is always an instruction for *where* the secret lives, never the secret: `"[env]NAME"` (environment variable, verbatim name), `"[file]PATH"` (mounted secret file), a dotted consumer-resolver reference (`"[acme_secrets.vault_resolver]prod/llm"`), or the explicit `{ null = true }` on a nullable field (an unauthenticated endpoint). A compose-green reference can still fail at dispatch — an unset variable or missing file is a *deployment provisioning* fault surfacing as `SecretResolutionError`, not a declaration fault; provision the store, never inline the value.

---

{#deployment-cross-references}
## Cross-references

- [reference](#deployment-reference) — the deployment-TOML grammar, the shared-by-binding wiring model, and the R-deployment-* derived rules cited throughout.
- [hash-model](#architecture-hash-model) — the never-hashed exclusion of every deployment section and the integrity-enforcement opt-in semantics `training_contract` gates.
- [exhaustive declaration](#architecture-exhaustive-declaration) — the section-discipline modes the closed-grammar check enforces.
- [R-pipeline-001](#R-pipeline-001) — the pipeline reference's transport / hook-transport coverage cross-check, the second stage paired with this component's own-shape check.
- [glossary](#glossary) — the engine vocabulary ([transport](#transport), [identity service binding](#identity-service-binding)) cited throughout.
- **handler conformance** (`components/handler/conformance.md`) — the cross-component grammar-family exemplar (the *Closed handler-declaration grammar* entry) the closed-deployment-grammar entry cites rather than restating.
- **error-index** (`reference/error-index.md`) — the codegen-built error → rule map; the complete registered check roster.
