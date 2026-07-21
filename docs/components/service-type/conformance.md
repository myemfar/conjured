---
kind: conformance
audience: [authors, integrators, agents]
slug: service-type-conformance
component: service-type
---

{#service-type-conformance}
# Service-type conformance checks

The mechanical conformance checks the engine fires for the [service-type](#service-type) component — the declared contract for an external dependency the engine calls, and the concrete implementation ([adapter](#the-service-type-adapter)) that backs it. Every service-type check is engine-enforced (mechanical); the service-type surface carries no review-enforced arm, so a thrown error here always names a declaration or adapter that violates the contract, never a body the runner cannot see.

The complete registered-check roster is the **error index** (`reference/error-index.md`), generated from the engine's `CHECK_REGISTRY` and complete by construction; each entry below **names its check discriminator inline** so that roster and this catalog bind. The `adapter-pure-module` check — the vector-7 adapter-module purity AST audit that fires at the same adapter resolution — is named on the handler catalog's Adapter-module purity entry (`components/handler/conformance.md`), its single home (the mechanism is the handler module-purity AST walk with a broader scope), and is not restated here.

Each entry below is structured for diagnosing a thrown error or auditing a service-type declaration (and its adapter) against the engine's contract. The format:

- **Check name** — the mechanical check; lowercase noun phrase.
- **Rule anchor** — the derived rule the check enforces, cited by prose anchor.
- **Trigger** — when the check fires (service-type-declaration load, compose time / resolution, registration).
- **Mechanism** — what the engine does to detect the violation.
- **Violation example** — a concrete declaration or Python snippet that fires the check.
- **Error class** — which of the [closed-enum classes](#error-class) the engine raises.
- **Diagnosis** — what to look for and how to fix.

---

{#service-type-mechanically-enforced-checks}
## Mechanically-enforced checks

{#nullable-only-on-transport-fields}
### Nullable declared only on transport fields

- **Rule anchor.** [Derived rule R-service-type-001 (closed service-type declaration grammar)](#service-type-derived-rules); [§ `[transport_schema]`](#transport-schema-section) (nullable is transport-only).
- **Trigger.** Service-type-declaration load (engine startup), at parse.
- **Mechanism.** As [§ `[transport_schema]`](#transport-schema-section) states, `nullable = true` (equivalently the `"<T> | None"` type union) is admitted **only** on transport fields, where a null value is a meaningful per-deployment state (an unauthenticated local endpoint has no credential); identity and config values are contract-shaping and admit no nullable declaration, because a null identity or config value is not a meaningful composition state. The parser walks every `[identity_schema]` and `[config_schema]` field's declared type and rejects a **reachable** Optional — the ban reaches a nullable nested inside an object or collection, not just the field's top level. Check discriminator `nullable-placement`.
- **Violation example.**

  ```toml
  [identity_schema]
  model = { type = "str | None", nullable = true }   # nullable on an identity field — ContractViolation
  ```

  ```toml
  [config_schema]
  overrides = { type = "dict[str, str | None]" }      # nullable reachable inside a config field's collection — ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Drop the nullable: a missing identity/config value is not a meaningful composition state. Nullability is meaningful only per-deployment — declare a genuinely optional value in `[transport_schema]`, where the reserved [explicit null](#binding-value-supply-grammar/explicit-null) `{ null = true }` supplies the unauthenticated-local state at a covering deployment block. A value that must vary but shapes the pipeline is a required identity or config field, not a nullable one.

{#config-supply-covers-config-schema-both-directions}
### Config supply covers `[config_schema]` in both directions

- **Rule anchor.** [Derived rule R-service-type-002 (config-schema contract)](#service-type-derived-rules); [§ The `[config_schema]` contract](#config-schema-contract) (the compose-side supply rule).
- **Trigger.** Compose time, at **every** config supply site — a [trainable](#trainable) composition's `[trainable.config]`, and any other service-typed binding's pipeline/composition `service_bindings.<name>` `config` block (one derivation, identical at both sites, per [§ The `[config_schema]` contract](#config-schema-contract)).
- **Mechanism.** As [§ The `[config_schema]` contract](#config-schema-contract) states, the engine validates config supply against the bound service-type's `[config_schema]` in **both directions**: every supplied config key MUST be a declared `[config_schema]` field of the bound service-type (an undeclared key raises), and every declared field MUST be **covered** — supplied, or carrying a declared ship-time `default` (an uncovered field raises). Supply is complete by construction, so every config kwarg reaches `invoke()` with a concrete, composition-visible value. The same discriminator carries two riders on the effective value: a `table` config field's effective value MUST be JSON-expressible, and a supplied `extras` table's keys MUST be disjoint from the bound trainable adapter's `reserved_wire_keys`. Check discriminator `config-schema-supply`.
- **Violation example (undeclared key).**

  ```toml
  # bound service-type declares [config_schema] temperature, max_tokens only
  [trainable.config]
  template = "dialogue_v3"     # not a declared config field — ContractViolation
  ```

- **Violation example (uncovered field).**

  ```toml
  # bound service-type declares [config_schema] temperature (no default) and max_tokens
  [trainable.config]
  max_tokens = 512             # temperature neither supplied nor default-bearing — ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** For an **undeclared key**: prompt-shaping content arrives via `trainable.reads`, never config ([R-handler-011](#handler-derived-rules) — this check is that rule's structural gate); a genuine generation parameter needs a declared `[config_schema]` field on the service-type. For an **uncovered field**: supply the value, or declare a ship-time `default` on the service-type's `[config_schema]` field. This is the compose-side half of R-service-type-002; the implementation-side half is the adapter-signature check below.

{#adapter-invoke-signature-matches-the-config-schema}
### Adapter `invoke()` signature matches the closed dispatch-kwargs and `[config_schema]`

- **Rule anchor.** [Derived rule R-service-type-003 (service-impl dispatch contract)](#service-type-derived-rules) and [derived rule R-service-type-002 (config-schema contract)](#service-type-derived-rules); [§ Signature validation](#signature-validation), [§ Closed dispatch-kwargs](#closed-dispatch-kwargs).
- **Trigger.** Compose time, at service-implementation resolution (the adapter sibling mechanism) — a compose-time check that never fails at runtime.
- **Mechanism.** As [§ Signature validation](#signature-validation) states, when the engine resolves a service implementation it introspects the adapter's `invoke()` from the real `__code__` and verifies it is **keyword-only** and matches the contract exactly: the closed dispatch-kwargs (`input_payload`, `service_name`, `caller_qualified_name`, `caller_position` — [§ Closed dispatch-kwargs](#closed-dispatch-kwargs)), **exactly** one keyword-only parameter per `[config_schema]` field of the bound service-type, and a `**transport_extra` collector — no more, no less, with one declared-optional exception: the deadline-propagation kwarg `remaining_budget_ms` ([§ Deadline propagation](#deadline-propagation)), legal to declare or omit per surface. Any other mismatch — a missing closed or config kwarg, an undeclared extra parameter, a config kwarg with no `[config_schema]` field, a `*args` collector, a missing `**` collector, or any positional parameter beyond `self` — raises. This is the implementation-side half of the bidirectional `[config_schema]` check ([§ The `[config_schema]` contract](#config-schema-contract)). Check discriminator `adapter-signature-mismatch`.
- **Violation example.**

  ```python
  # bound service-type declares [config_schema] temperature, max_tokens
  class StructuredOutputImpl:
      def invoke(self, *, input_payload, service_name, caller_qualified_name,
                 caller_position, temperature, **transport_extra):
          ...          # the max_tokens config kwarg is missing — ContractViolation
  ```

  ```python
  # an extra parameter with no [config_schema] field backing it
  class StructuredOutputImpl:
      def invoke(self, *, input_payload, service_name, caller_qualified_name,
                 caller_position, temperature, max_tokens, top_p, **transport_extra):
          ...          # top_p is not a declared config field — ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Bring the signature into exact correspondence with the bound service-type's `[config_schema]`: one keyword-only parameter per declared config field, the four closed dispatch-kwargs, `**transport_extra`, and `self` as the only positional parameter. A generation parameter the adapter accepts but the service-type does not declare is the mirror fault — declare it in `[config_schema]` (which folds into the [pipeline-hash](#pipeline-hash)); the contract is declared, not introspected. Transport fields ride the `**transport_extra` collector, never named parameters.

{#adapter-construction-from-compose-fixed-identity}
### Adapter construction from compose-fixed identity

- **Rule anchor.** [Derived rule R-service-type-003 (service-impl dispatch contract)](#service-type-derived-rules); [§ Construction](#adapter-construction).
- **Trigger.** Compose time, at the one-instance-per-composition adapter construction (after resolution and signature validation).
- **Mechanism.** As [§ Construction](#adapter-construction) states, the engine constructs **exactly one** adapter instance per composition, at compose time, passing the constructor **only the compose-fixed identity** values the pipeline supplied for the binding (e.g. `model`, `prompt_template`); a trainable backend additionally receives the two engine-supplied `output_schema` / `schema_source` kwargs. Everything dynamic — transport, config, the call payload — arrives per dispatch through `invoke()`. A construction failure (a `TypeError` from binding the identity kwargs, or any raise from the `__init__` body) wraps into a compose-time ContractViolation — the closed compose-time channel covers construction, so nothing there fails at runtime. A `ContractViolation` the constructor itself raises (e.g. the trainable constraint-derivation rejection) passes through unwrapped, already the closed channel. Check discriminator `adapter-construction`.
- **Violation example.**

  ```python
  # bound service-type supplies identity model / prompt_template
  class StructuredOutputImpl:
      def __init__(self, *, endpoint):              # expects transport, not the identity kwargs
          self.client = HttpClient(endpoint)        # + reaches for transport / I/O at construct time
      def invoke(self, *, input_payload, service_name, caller_qualified_name,
                 caller_position, temperature, max_tokens, **transport_extra):
          ...
  # compose supplies model= / prompt_template= → __init__ rejects the kwargs (TypeError) — ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** The adapter `__init__` takes exactly the bound service-type's `[identity_schema]` fields as keyword arguments and must not raise at construction. Anything dynamic — transport, config, the call payload — arrives per dispatch through `invoke()`; the authenticated client is the adapter's own lazy first-`invoke()` memoization on [instance state](#adapter-instance-state-caching) (the constructor has no transport, so it cannot build one there).

{#engine-owned-identity-not-redefined-or-class-path-bound}
### Engine-owned `conjured.lib.*` identity is neither redefined nor bound by class path

- **Rule anchor.** [Derived rule R-service-type-004 (one implementation per service-type qualified name) — § Engine-owned identities](#service-type-derived-rules).
- **Trigger.** Compose time (binding resolution) and service-type registration.
- **Mechanism.** A `conjured.lib.*` qualified name is an **engine-owned identity**: it resolves through the engine's shipped native adapter table, which takes precedence over consumer resolution, so a native qualified name's implementation is necessarily the engine's shipped one. Two illegitimate ways of representing that identity fail loud. **(1) Class-path binding, at adapter resolution:** a binding whose `type` is a native adapter **class path** (the value the native table maps the qualified name to) rather than the native qualified name is rejected — routing an engine-owned native by a non-canonical identity would let one backend carry two hash identities. **(2) Redefinition, at registration:** registering a service-type under a `conjured.lib.*` name that is **not** the engine-shipped declaration for that native (a modified declaration, or a name the engine ships nothing under) is rejected as redefining an engine-owned identity. Hand-loading the genuine shipped declaration stays legal (there is nothing for an author to re-declare — the identity already resolves to the shipped declaration and its one registered implementation). Check discriminator `engine-owned-identity`.
- **Violation example (class-path binding).**

  ```toml
  # binding the native trainable by its adapter class path instead of its qualified name
  [service_bindings.llm]
  type = "conjured.lib.openai_compatible_trainable.OpenAICompatibleTrainable"   # the native adapter CLASS PATH — ContractViolation
  # canonical: type = "conjured.lib.openai_compatible_trainable"
  ```

- **Violation example (redefinition).** A hand-written `conjured.lib.openai_compatible_trainable` service-type TOML — differing in any field from the engine-shipped sibling — registered via `add_service_type`: it redefines an engine-owned identity and raises.
- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Bind the native qualified name directly (`type = "conjured.lib.<name>"`); the engine resolves its shipped declaration and one registered implementation through the native adapter table, so one backend keeps exactly one hash identity. A backend the native catalog does not cover gets its **own** package-prefixed qualified name and a certified (audit-stamped) adapter — never a redefinition of, or a class-path alias for, a `conjured.lib.*` name.

---

{#service-type-conformance-cross-references}
## Cross-references

- [reference](#service-type-reference) — the per-component grammar and the service-type derived rules (R-service-type-001..004) cited throughout.
- **handler conformance** (`components/handler/conformance.md`) — the sibling catalog; its Adapter-module purity entry is the single home of the `adapter-pure-module` check that fires at the same adapter resolution.
- [handler resolution](#architecture-handler-resolution) — the adapter sibling resolution mechanism (the source-AST audit, namespace-package rejection, entry-points collision) the resolution-time checks run within.
- [handler-kinds](#architecture-handler-kinds) — the service handler and trainable composition node that each bind one service-type.
- [trust-model](#architecture-trust-model) — the vector-7 seal the co-fired `adapter-pure-module` audit closes.
- [glossary](#glossary) — the engine vocabulary cited throughout.
- **error-index** (`reference/error-index.md`) — the codegen-built error → rule map; the complete check roster this catalog binds against.
