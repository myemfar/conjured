---
kind: conformance
audience: [authors, integrators, agents]
slug: native-library-conformance
component: native-library
---

{#native-library-conformance}
# Native-library conformance checks

The mechanical conformance checks the engine fires for native-library members,
plus the review instrument for the trainable-backend contract. Per-member audit
entries land here as members ship (the member shape: one TOML + one module + one
audit entry per member). The entry format follows the handler conformance reference.

{#trainable-backend-certification-check}
### Trainable-backend certification carried on the resolved adapter

- **Rule anchor.** [Derived rule R-handler-008 (exactly one service-typed
  binding — trainable expansion)](#handler-derived-rules);
  [§ Trainable backends — the compose-time gate](#trainable-backends).
- **Trigger.** Compose time, after adapter resolution for a
  `trainable.service_bindings` binding.
- **Mechanism.** Certification is **structural**, verified against the resolved adapter
  class at compose — never a self-declared marker. A binding is admitted when its adapter
  is native-by-construction (resolved through the engine's native adapter table) or a
  consumer adapter carrying a fresh pass-grade sibling audit stamp — the two admission arms
  [§ Trainable backends](#trainable-backends) owns, the stamp arm gated by the deployment's
  `audit_enforcement` per the [audit-stamp mechanism](#audit-stamps/kernel). Separately, the
  gate verifies the two immutable property attributes the contract requires (the closed set
  owned by [§ Trainable backends](#trainable-backends), R-handler-008), rejecting the
  binding if either is absent or malformed.
- **Violation example.**

  ```python
  class MyRuntimeAdapter:            # no property attributes, no audit stamp
      def invoke(self, *, input_payload, service_name, caller_qualified_name,
                 caller_position, temperature, **transport_extra): ...
  ```

  Bound from `trainable.service_bindings`, this raises at compose: the adapter
  declares neither property attribute, and its module carries no fresh audit stamp.
- **Error class.** [ContractViolation](#contractviolation)
  (check `trainable-backend-certification`).
- **Diagnosis.** Bind a native trainable backend (`conjured.lib.*`), or run the
  engine-shipped audit-stamp prompt (shipped at `conjured.conformance`) against a
  consumer-supplied adapter — the review process that certifies the
  [§ Trainable backends](#trainable-backends) property contract — and record the
  result as the adapter module's sibling stamp (the
  [audit-stamp mechanism](#audit-stamps/kernel) owns the stamp shape and the
  freshness check; any edit to the stamped module stales the stamp structurally —
  re-audit to re-stamp). An absent, empty, or
  non-string `training_artifact_contract` is a property-3 failure — declare a
  non-empty provenance label (the engine records it but does not interpret the
  value, so a bespoke-but-portable artifact format is admitted, not a failure).

{#trainable-constraint-unsupported-check}
### Trainable output schema outside the wire form's seal-expressible subset

- **Rule anchor.** [Derived rule R-handler-005 (literal-equal
  rule)](#handler-derived-rules); [§ Trainable backends — the compose-time
  caveat](#trainable-backends).
- **Trigger.** Compose time, at trainable-backend adapter construction (the
  constraint derivation).
- **Mechanism.** The adapter derives the backend decode constraint from the
  declared `trainable.output_schema` at construction. An in-set constraint keyword
  renders into the submitted constraint (the seal stays literal-equal); a keyword
  out of the bound wire family's accepted set, and the structural cases below,
  reject — the seal-expressibility rejected class (owned by the handler reference's
  § Trainable backends — the accepted matrix):

  :::{transclude} trainable-wire/rejected-class
  :::
- **Violation example.**

  ```toml
  [trainable.output_schema]
  count = { type = "int", minimum = 1 }   # numeric-range predicate, out of every JSON wire's accepted set
  ```

  A token-level grammar cannot enforce the value predicate; compose rejects, naming
  the keyword (`minimum`) and the wire.
- **Error class.** [ContractViolation](#contractviolation)
  (check `trainable-constraint-unsupported`).
- **Diagnosis.** Move value constraints to a downstream transform (the
  trainable emits the channel literally — the split-with-downstream-transform
  pattern); route binary as path/hash references; re-shape tuples as nested
  objects or lists; on the strict wire, close open-keyed dicts into declared
  nested objects or bind `conjured.lib.gbnf_trainable`.

{#extras-disjoint-check}
### Config `extras` key collides with a reserved wire key

- **Rule anchor.** [Derived rule R-service-type-002 (config-schema
  contract)](#service-type-derived-rules); the reserved-set owner is
  [§ Trainable backends](#trainable-backends), R-handler-008 (`reserved_wire_keys`).
- **Trigger.** Compose time, when a trainable composition node's `[trainable.config]`
  (or a service binding's config block) supplies values for the bound adapter's open
  `extras` table.
- **Mechanism.** The engine intersects the keys supplied in the open `extras` table with
  the resolved adapter's `reserved_wire_keys` (the frozen class attribute owned by
  [§ Trainable backends](#trainable-backends), R-handler-008). A non-empty intersection
  rejects the declaration — the rule, its rationale, and the own-home mapping are the
  extras rider's:

  :::{transclude} config-extras/reserved-keys-disjoint
  :::
- **Violation example.**

  ```toml
  [trainable.config]
  extras = { model = "qwen3.5-4b-gguf" }   # `model` is a reserved wire key, not an extra
  ```

  The supplied `extras` names `model`, a reserved wire key; compose rejects, naming the
  key and its real home (the checkpoint identity belongs in the binding's
  `[identity_schema]` supply).
- **Error class.** [ContractViolation](#contractviolation) — the `config-schema-supply`
  check's extras-disjointness rider (the check's canonical conformance entry is in
  `components/service-type/conformance.md` § Config supply covers `config_schema`).
- **Diagnosis.** Move the colliding key to the declared home the
  [rider's own-home mapping](#config-extras/reserved-keys-disjoint) names; `extras`
  carries only the engine-opaque sampling tail.

{#per-member-audit-entries}
## Per-member audit entries

One entry per shipped native member — the per-member audit the intro promises. Each
**cites** its reference entry (which owns the member's contract — wire form, accepted
matrix, reserved keys, emission shape) and the generic checks it rides above (which own
the mechanisms); it restates none of that. What each entry adds is member-specific: which
checks govern the member, and a concrete declaration it rejects at compose that
**distinguishes** it from its siblings.

{#audit-openai-compatible-trainable}
### `conjured.lib.openai_compatible_trainable`

- **Rule anchor.** The member's reference entry
  [§ `openai_compatible_trainable`](#native-library-openai-compatible-trainable); the checks it
  rides — [trainable-backend certification](#trainable-backend-certification-check), the
  [constraint-expressibility check](#trainable-constraint-unsupported-check)
  ([derived rule R-handler-005](#handler-derived-rules)), and the
  [extras-disjointness check](#extras-disjoint-check)
  ([derived rule R-service-type-002](#service-type-derived-rules), reserved-set owner
  R-handler-008).
- **Trigger.** Compose time, at this backend's adapter construction (the constraint
  derivation) and at the `[trainable.config]` extras-disjointness check.
- **Mechanism.** A certified `native-by-construction` trainable backend riding the generic
  trainable-gate checks above; its accepted-keyword matrix, its `reserved_wire_keys`, and its
  wire boundaries are the reference entry's, cited not restated. The distinguishing
  rejection this entry exercises is the strict wire's structural boundary — an open-keyed
  `dict[str, <T>]`, which the submitted strict `json_schema` cannot express (a shape
  `gbnf_trainable`'s grammar *does* express) — and an output keyword outside this wire's
  narrower accepted set.
- **Violation example.**

  ```toml
  [trainable.output_schema]
  attrs = { type = "dict[str, str]" }   # open-keyed dict — the strict json_schema form cannot express it
  ```

  Compose rejects (check `trainable-constraint-unsupported`, R-handler-005), naming the
  open-keyed dict and the wire.
- **Error class.** [ContractViolation](#contractviolation) — under the check it rides.
- **Diagnosis.** Close the open-keyed dict into a declared nested object, or bind
  [`conjured.lib.gbnf_trainable`](#native-library-gbnf-trainable) (whose grammar expresses
  it). A rejected value keyword moves to a downstream reader's schema (the trainable emits
  the channel literally); an extras collision moves to the key's real home per the
  [own-home mapping](#config-extras/reserved-keys-disjoint).

{#audit-gbnf-trainable}
### `conjured.lib.gbnf_trainable`

- **Rule anchor.** The member's reference entry
  [§ `gbnf_trainable`](#native-library-gbnf-trainable); the checks it rides —
  [trainable-backend certification](#trainable-backend-certification-check), the
  [constraint-expressibility check](#trainable-constraint-unsupported-check)
  ([derived rule R-handler-005](#handler-derived-rules)), and the
  [extras-disjointness check](#extras-disjoint-check)
  ([derived rule R-service-type-002](#service-type-derived-rules), reserved-set owner
  R-handler-008).
- **Trigger.** Compose time, at this backend's adapter construction (the grammar
  derivation) and at the `[trainable.config]` extras-disjointness check.
- **Mechanism.** A certified `native-by-construction` trainable backend riding the generic
  trainable-gate checks above; its accepted matrix (wider than the strict wire's), its
  `reserved_wire_keys` (the llama.cpp structural
  keys, so its extras collisions differ from the strict wire's), and its wire boundaries
  are the reference entry's, cited not restated. The distinguishing rejections this entry
  exercises are the two the grammar wire imposes that the strict wire does not: a
  `trainable.output_schema` field carrying a `description` (a GBNF grammar has no
  description channel, and property 4 forbids the adapter from prompt-shaping to
  compensate), and a declared output-field **name** carrying a non-ASCII character (grammar
  rule names are ASCII-only).
- **Violation example.**

  ```toml
  [trainable.output_schema]
  tone = { type = "str", description = "the emotional register the line should carry" }
  # rejected at compose — this wire delivers no descriptions (the openai_compatible wire accepts it)
  ```

  Compose rejects (check `trainable-constraint-unsupported`, R-handler-005), naming the
  wire; the same declaration is accepted by
  [`conjured.lib.openai_compatible_trainable`](#native-library-openai-compatible-trainable),
  whose submitted `json_schema` carries descriptions.
- **Error class.** [ContractViolation](#contractviolation) — under the check it rides.
- **Diagnosis.** Route a described field to
  [`conjured.lib.openai_compatible_trainable`](#native-library-openai-compatible-trainable),
  or move the guidance to the composition's `[annotations]`; rename a non-ASCII field name
  within ASCII; a `pattern` keyword (rejected on this wire) moves to a downstream reader's
  schema; an extras collision moves to the key's real home per the
  [own-home mapping](#config-extras/reserved-keys-disjoint).

{#audit-blob-reference-emitter}
### `conjured.lib.blob_reference_emitter`

- **Rule anchor.** The member's reference entry
  [§ `blob_reference_emitter`](#native-library-blob-reference-emitter). This member is a
  stdlib-emission **hook**, not a trainable backend — it rides **none** of the
  trainable-gate checks above; its conformance surface is the ordinary hook contract
  ([derived rules R-handler-001, R-handler-007, R-handler-009](#handler-derived-rules); the
  [observer node profile](#the-hook-kind)).
- **Trigger.** Compose time, when a node binds `conjured.lib.blob_reference_emitter.emit`
  (its closed `[hook]` shape — one required `reference` read, empty `service_bindings`, a
  non-empty `transport_schema` — is grammar-checked); and dispatch time, where the
  return-`None` seal fires.
- **Mechanism.** An observer hook that writes no channels, declares exactly one required
  `reference` read port (no optional second hash port — the closed-set reads discipline the
  reference entry closes), reserves no rendering vocabulary, and returns `None`; there is
  deliberately no wire form, accepted matrix, or reserved-key surface for a trainable check
  to touch. The emission contract and the read/transport shape are the reference entry's,
  cited not restated.
- **Violation example.**

  ```python
  def emit(*, reference, format):
      logging.getLogger("conjured.lib.blob_reference_emitter").info(...)
      return reference   # a hook returns None — the runner has no merge path for a hook return
  ```

  Rejected at dispatch (R-handler-001): a non-`None` hook return.
- **Error class.** [ContractViolation](#contractviolation) — the hook return-`None` seal.
- **Diagnosis.** Emit the reference; never return it (the runner threads no hook return). A
  wanted content-hash is the author's separate concern via the `<name>_hash` convention on
  their own channel, never a second read port on this hook; route the emitted reference to
  the consumer by configuring the engine logger `conjured.lib.blob_reference_emitter`
  deployment-side (the hook binds no log path of its own).

{#native-library-property-review}
## Review-enforced: the trainable-backend property contract

The properties themselves are
[§ Trainable backends](#trainable-backends)' contract; for a consumer-supplied
adapter their verification is **review-enforced** — the engine-shipped
audit-stamp prompt is the review instrument, run per adapter version. The
mechanical checks above are the compose-time teeth: the certification gate
excludes unreviewed adapters; the constraint check excludes seal-breaking
schemas regardless of certification.
