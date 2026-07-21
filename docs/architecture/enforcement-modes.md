---
kind: reference
audience: [authors, integrators, agents]
slug: architecture-enforcement-modes
---

{#architecture-enforcement-modes}
# Enforcement modes

A typed dataflow [graph](#graph) holds its contract at
channel boundaries — the `reads` and `output_schema` interfaces every node
publicly declares. Every [derived rule](#derived-rule)
the engine ships governs one of those boundaries or the handler body connecting
them. How a rule is held is its **enforcement mode**, a closed two-value enum:

- **`mechanical`** — the runner mechanically rejects a violation at a boundary it
  can see (declaration load, compose time, dispatch).
- **`review`** — the rule holds at the handler-body layer where the runner has no
  visibility; adversarial review catches handler-body instances.

The mode is a frontmatter field on every derived rule (`enforcement: mechanical |
review`). The validator enforces the value enum;
[invariants](#invariant) themselves do not carry the
field — invariants are axiomatic, not mechanically held by the engine or by
review.

---

{#mechanically-enforced-mode}
## Mechanically-enforced

A [mechanically-enforced](#mechanically-enforced) violation is one the runner rejects
at a boundary it can see — one of three:

- **Handler-declaration load (engine startup).** Wrong key, missing required
  field, type mismatch within a handler's declared schemas, malformed
  field-metadata.
- **Compose time (pipeline-declaration load + engine-constructed dispatch).**
  Illustrative rejections — the [error-index](#error-index-codegen/kernel) registers the
  complete check roster, not the list below:
  Handler signature does not match the declared `reads` (the bare-function
  signature introspection at [handler resolution](#glossary-handler-resolution), per
  R-handler-001); the
  source-AST audit flags the forbidden impurity patterns in a handler module
  ([R-handler-pure-module](#R-handler-pure-module) — the
  [trust-model](#trust-model-vector) vector-3 and vector-5 seal); the
  function-shape check rejects non-bare-function shapes at handler resolution
  ([R-handler-bare-function](#R-handler-bare-function) — the
  [trust-model](#trust-model-vector) vector-2 seal); schema-shape diff between two
  nodes' channel types;
  qualified-name resolution failure; declared `service_bindings` not supplied in
  the pipeline declaration; a channel with two or more contributors and no explicit
  `merge.<channel>` declaration (R-pipeline-002); the
  [trainable](#trainable) composition kind's one service
  binding not bound to a trainable backend (R-handler-008 expansion); hook
  transport coverage missing.
- **Dispatch (per-invocation).** Input shape mismatch when the engine projects
  declared `reads` from upstream channels into kwargs; output shape mismatch when
  the handler's return dict (or, for the trainable composition kind, the
  engine-routed adapter response) is validated against `output_schema` (key not
  declared, value type wrong, required field missing).

Mechanically-enforced rules carry `enforcement: mechanical`. The validator and
runner together compose the mechanically-enforced layer. Failures raise one of
the [error classes](#error-class) and halt the
pipeline (or, for hooks, halt only on
[ContractViolation](#contractviolation) /
[SchemaValidationError](#schemavalidationerror)).

---

{#review-enforced-mode}
## Review-enforced

A [review-enforced](#review-enforced) rule lives in the handler body's runtime
behavior — what the impl does between reading its declared inputs and writing its
declared outputs — which is **structurally invisible** to the runner (for bare-function
kinds; the trainable composition kind has no author body and is structurally immune to
these failure modes). The runner cannot mechanically detect:

- **Silent fallback.** A handler body wrapping an external call in `try / except:
  return default` and emitting a schema-valid value the runner cannot distinguish
  from a runtime-derived result.
- **Semantic retry.** A service body looping on `external call → critique →
  external call again` and returning the final attempt as the captured
  invocation, burying earlier attempts under one record.
- **Hidden writes.** A handler body performing side effects beyond its declared
  output channels — DB INSERT, file write, mutated module state at runtime.

These rules carry `enforcement: review`. The engine ships
[steering content](#steering) and adversarial-review
materials — one-question falsification checklists usable by human PR reviewers or
by agents — that catch handler-body instances during library publishing.
Adversarial review is the paired methodology for this mode: it catches at the
handler-body layer exactly what the mechanically-enforced layer cannot see, so the
two modes are complementary coverage of one rule surface rather than alternatives.

**Module-level state** is held by two mechanical layers with a bounded review
residue. At *import time* it is mechanically-enforced (the R-handler-pure-module
AST audit at compose rejects module-level mutable state, persistent caching
decorators, and import-time I/O; the scope extension covers adapter modules too —
class-level and module-level mutable state in adapter modules are rejected, with
instance state initialized in `__init__` or assigned on `self` admitted, per the
[trust-model](#trust-model-vector) vector-7 seal). At *runtime* the same rule
carries a second mechanical layer — the module-dict snapshot-and-restore around
each dispatch, with its fail-loud restore, fixed at the
[R-handler-pure-module enforcement kernel](#R-handler-pure-module/enforcement) —
whose scope is bounded to the module *namespace*: the restore reverts rebinding
of module-level names; an in-place mutation of container state behind an
unchanged binding is invisible to the snapshot. What review still catches at the
handler-body layer is exactly that residue — *runtime* mutation that imitates
module-level persistence by writing into mutable container state the AST audit
cannot trace dynamically and the restore cannot revert.

The asymmetry between mechanically-enforced and review-enforced rules is not
weakness in the type system; it is the type system's honest scope.

---

{#how-the-two-modes-compose}
## How the two modes compose

Most rules carry exactly one mode. A rule MAY carry both when its structural
enforcement catches one category of violation and its review-enforced companion
catches another category of the same rule. This is typically the case where:

- A discipline is mechanically enforceable at declaration-visible boundaries (TOML
  load, compose-time check).
- The same discipline has an impl-body shape the runner cannot see.

Example — R-handler-004 (transform purity):

:::{transclude} R-handler-004
:::

Both modes are first-class. Neither is a fallback for the other; rules carrying
both modes are neither miscategorized nor exceptions — they are the explicit
dual-seam case.

---

{#layered-defense}
## Layered defense

The two enforcement modes map to concrete failure classes:

| Failure class | Failure mode | Enforcement layer |
|---|---|---|
| shape-backpressure | Engine deformed to absorb consumer-shape during integration with a downstream system | Structural (mechanically-enforced): I3 + corpus-scope preamble prevent the engine from accepting consumer-shaped TOML |
| dispatch-bypass | Test harness bypassed handler dispatch → training target learned the wrong contract | Structural (mechanically-enforced): R-handler-005 literal-equal rule; same artifact drives runtime contract and training projection |
| mock-isolation | Mocks made tests independent of the engine; refactor passed silently | Both: R-pipeline-001 + R-error-channel-002 (mechanical: no retry API surface); R-handler-002 (review: no in-body silent fallback / semantic retry) |
| silent-fallback | Universal silent fallback masked total-context-load failure | Review at handler-body (R-handler-002) + structural second-layer: [adapter-boundary capture](#adapter-boundary-capture) of the `service_invocation` event |

**Silent-fallback — the second layer is detection, not a third mode.**
The structural second layer is service-kind-specific — silent fallback itself can
occur in any author-bodied kind (R-handler-002's scope), but only a service
dispatch has an adapter boundary capturing the backend's actual response. The
runner cannot inspect what a service handler body does between reading its channel inputs and returning its
output dict, so structural *prevention* at the handler-body layer is unavailable —
review-enforced R-handler-002 is the first-line defense. The structural *second
layer* is the `service_invocation` event captured at the service-type adapter
boundary before the handler body can act: the captured payload carries the
backend's actual response (and the payload the adapter submitted) regardless of
what the body later returns, so consumer-side analysis comparing
`service_invocation` against the paired `handler_exit` can flag the masking
signature.

:::{transclude} R-handler-002/evidentiary-backing-classification
:::

The trainable composition kind has no author body, is structurally immune to
silent-fallback, and emits no `service_invocation`. This second layer is
silent-fallback-specific and lives in the event log — it is **not** a third
enforcement mode. The two modes are exhaustive at the engine layer.

---

{#frontmatter-shape}
## Frontmatter shape

A derived rule's metadata block (in a multi-rule file's `rules:` list, or in a
per-rule frontmatter block on a single-rule file) carries:

```yaml
- rule_id: R-handler-002
  name: no silent fallbacks
  derived_from: [I1, I4]
  enforcement: review
  statement: |
    No silent fallbacks....
```

[Invariants](#invariant) (`I<N>`) and tenets (`T<N>`) carry no `enforcement`
field — they are axiomatic claims, not mechanically held by the engine or by
review. Derived rules cite the invariant(s) or tenet(s) they protect via `derived_from` — at
least one — and MAY additionally cite a derived rule they specialize. The validator rejects
any derived rule missing the `enforcement` field, and rejects any rule whose
`enforcement` value is not one of `mechanical | review`.

A rule that carries mechanical evidentiary backing does **not** declare a third
enforcement value. The rule's enforcement remains `review` (or whichever mode
names where the rule is held) and the evidentiary backing is described in the
rule's statement body via cross-reference to the engine-enabled capture surface.
Adding a third value would conflate *where the rule is held* with *what evidence
the engine emits* — two structurally distinct things, which is why the enum stays
two-valued even for a rule whose review judgment is grounded in a wire-visible
signal.

---

{#what-this-is-not}
## What this is NOT

- **Not a severity scale.** `mechanical` and `review` are not "high priority" and
  "low priority"; they are categorical placements describing where the rule is
  held. A review-enforced rule is no less load-bearing than a mechanically-enforced
  one.
- **Not a CI scheme.** The validator enforces frontmatter shape; which adversarial
  prompts run when, and against which artifacts, is a methodology concern, not an
  enforcement-mode property.
- **Not extensible at runtime.** The two modes are a [closed
  enum](#closed-enum). Adding a third (e.g.,
  "consumer-enforced") would be an engine change, not a runtime extension.
- **Not equivalent to the engine / consumer / review partition.** The
  enforcement-mode taxonomy here covers the two locations where engine-defined
  rules are *held*. Consumer territory (multi-pipeline orchestration, persistence,
  deployment, behavioral evaluation) is a separate category in the partition
  meta-rule owned by [principles](#engine-consumer-review-partition) —
  operations the engine deliberately does not own. Consumer territory has no
  derived rules in this corpus because the engine has no contract there to enforce.

---

{#what-enforcement-does-not-cover}
## What enforcement does not cover

The engine enforces the handler-declared channel contract at its declared
boundaries (TOML load, compose time, dispatch). It does not enforce or promise
backend determinism. For [trainable](#trainable)
composition kind nodes, two pipeline runs with identical channel inputs may
produce different outputs even under identical `trainable.output_schema`
constraints — temperature sampling, backend version drift, quantization changes.
This is intentional: the training-data corpus captures empirical outputs, not
deterministic replays. The
[training-bundle-hash](#training-bundle-hash) per trainable
composition node guarantees training records are bucketed by the trainable
composition declaration's structural identity, not by output value, so backend
non-determinism does not corrupt the corpus's structural integrity. Backend
behavior under a declared constraint is deployment discipline — pinning backends
and treating backend upgrades as composition changes is the consumer's
responsibility.
