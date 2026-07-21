---
kind: reference
audience: [authors, integrators, agents]
slug: architecture-exhaustive-declaration
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#architecture-exhaustive-declaration}
# Exhaustive declaration
In the typed dataflow [graph](#graph), every node publicly
declares its channel interfaces via declaration sections — `reads` for the
[input ports](#input-port) a node reads, `output_schema` for the [output ports](#output-port) it writes (each port wired to a channel by the node's [read-map](#read-map) / [write-map](#write-map)).
Exhaustive declaration is the discipline that makes those interface declarations
complete and unambiguous: **every section header applicable to the artifact MUST
appear, even when the body is empty.** Empty-but-present is the canonical
"considered this axis, declared nothing" signal; omission is a load-time
[ContractViolation](#contractviolation).

The discipline is a deliberate deviation from mainstream omit-when-empty config
conventions. The deviation is load-bearing for
[invariant I1](#invariants-and-derived-rules); the
[override-instruction](#exhaustive-declaration-override-instruction) below is the agent-facing form.

---

{#the-section-discipline-modes}
## The section-discipline modes

Every engine-declared section header is classified by exactly one of the section-discipline modes below.
The modes classify **declaration sections** — headers whose body is a set of declared fields.
Node-sequence and value-supply blocks — a pipeline's `nodes` array and a composition's
`[[preprocessors]]` array and terminal `trainable` node, `merge` declarations, and the
`service_bindings.<name>` / `trainable.config` / deployment `transport.<name>` /
deployment `hook_transport."<node>"` value-supply blocks — are a different grammatical
category: each is governed by the cardinality and grammar
its owning rule states (the composition grammar at R-handler-006; the pipeline grammar at
R-pipeline-001 / R-pipeline-002; deployment `transport.<name>` and `hook_transport."<node>"`
cardinality and field-validation at R-pipeline-001's transport coverage, owned by the
pipeline reference), not by a section mode. The presence discipline still binds them: a value-supply block whose
owning rule marks it **required** also requires its header to be **present** — omission is a
[ContractViolation](#contractviolation) at load, like any other applicable header (the
carve-out governs each block's *body* cardinality and grammar, not whether its header must
appear). A trainable node's `[trainable.config]`, for instance — marked required by the
composition grammar — MUST appear even when its body is empty.

- **Required, empty-allowed.** The section header MUST appear; an empty body is
  canonical "considered this axis and declared nothing." Omission is
  [ContractViolation](#contractviolation) at load. Most
  engine-declared declaration sections fall here — a handler declaration's `reads`,
  for instance; each kind's applicable-sections set is the
  [handler reference](#handler)'s to enumerate, and the service-type reference owns
  its own. Note: `bindings.<name>` declarations are individually-named
  sections — author-chosen by domain meaning, N ≥ 0 per handler. There is no
  umbrella `bindings` header that must appear; a handler with zero declared
  bindings simply has no `bindings.<name>` sections.
- **Required, body-required.** The section header MUST appear AND its declared
  fields MUST carry explicit values; an empty body is
  [ContractViolation](#contractviolation) at load. Applies
  to sections whose declared content is itself load-bearing and has no meaningful
  "declared nothing" state — a channel-writing kind's `output_schema`, for
  instance: the header MUST appear AND the field declarations MUST be non-empty,
  because a kind whose [comparison table](#handler-kind) "Declared writes" cell
  reads `required` that writes nothing contradicts the structural claim. (Hooks
  have no `output_schema` at all — the `forbidden` mode applies.) Which other
  sections are body-required — the deployment's `training_contract`
  (the [grammar rule](#training-contract-section-required-body-required) is the
  deployment reference's), a service-type's `identity_schema` and
  `transport_schema` (owned at R-service-type-001) — is each owning reference's to
  state.
- **Truly optional.** Omission and empty body are equivalent; both accepted, and
  both mean the same declared-nothing state. Applies to sections whose presence
  carries no opt-in signal of its own — `annotations`, for instance, on the
  declaration classes whose grammar declares one (free-form prose; the
  pipeline-family `[inputs]` / `[outputs]` grammars declare none).
- **Presence-is-the-signal.** The section's presence-or-absence is itself the
  declared signal: presence opts in to the surface or behavior the section
  governs; absence opts out. Empty-but-present is therefore NOT equivalent to
  omission — they are categorically distinct declarations. Whether the present
  body must carry fields is the section's own sub-rule, split two ways:
  deployment-declaration `training_export` is empty-allowed when present
  (presence alone toggles capture routing); pipeline `outputs` is body-required
  when present (presence declares the output API commitment, and an opt-in
  declaring no fields is itself a violation — the pipeline reference's
  § `inputs` / `outputs` owns the boundary semantics).
- **Conditionally required.** The section is required exactly when a named
  structural condition holds, and the condition is checked mechanically:
  pipeline `inputs` is required whenever the graph reads a channel before any
  write (an otherwise-unwritten read-port channel covered by no `inputs` declaration
  is a dangling input port —
  [ContractViolation](#contractviolation) at compose-time
  normalization, per R-pipeline-001's input closure).
- **Forbidden.** The section MUST NOT appear on this handler kind; its presence
  raises [ContractViolation](#contractviolation) at
  handler-declaration load. This is a kind-discipline property enforced by the
  closed per-kind section grammar (R-handler-006, closed handler-declaration shape
  grammar); the section is structurally absent, not just empty. Examples:
  `output_schema` on a hook declaration; `service_bindings` and `transport_schema`
  on a transform declaration.

The classification rule is mechanical:

- Any section for which "considered and declared nothing" is meaningfully distinct
  from "forgot" is **required, empty-allowed**.
- Any section whose declared choice is itself load-bearing (no meaningful
  "declared nothing" state) is **required, body-required**.
- Any section for which omission and an empty body genuinely mean the same
  declared-nothing state is **truly optional**.
- Any section whose presence-or-absence is itself an opt-in signal is
  **presence-is-the-signal**.
- Any section required exactly when a named structural condition holds is
  **conditionally required**.
- Any section whose presence is a structural error for this handler kind is
  **forbidden**.

---

{#why-this-works-mechanically}
## Why this works mechanically

Three properties combine to close the missing-by-oversight failure mode by
construction:

1. **Every section is enumerated by [kind](#handler-kind).**
   The handler-declaration vocabulary defines exactly which sections apply to each
   kind in the closed-enum taxonomy. The trainable composition kind uses a
   different declaration grammar — the
   [composition TOML primitive](#composition-toml) — whose
   section enumeration is owned by the handler component reference and the
   `trainable.schema.toml` machine-readable schema. Sections outside the kind's
   applicable set are rejected at load (presence of an inapplicable section is
   itself a [ContractViolation](#contractviolation)).
2. **The runner refuses unknown keys**
   ([invariant I1](#invariants-and-derived-rules)). A
   typo in a section name is caught — there is no "fall through to prose" path the
   engine accepts.
3. **The empty body is parseable.** TOML accepts `[reads]` with nothing below it;
   the runner reads the empty section as the explicit "no declared reads" signal.

---

{#exhaustive-declaration-override-instruction}
## Override-instruction

This section is the canonical render-base for the agent surface's
[override-instruction](#override-instruction) steering
note. An agent priming on the engine should read this verbatim before authoring or
modifying any handler TOML.

> **Override-instruction — exhaustive declaration.**
>
> When you author or edit a handler declaration for the Conjured engine, do NOT
> omit section headers because their body is empty. Mainstream Python config
> conventions (pyproject, setup.cfg, Cargo) treat empty-equals-absent as
> ergonomic; for engine-conformant handlers this rule is inverted.
>
> Every section header applicable to the handler's
> [kind](#handler-kind) MUST appear in the file (the
> [handler reference](#handler) enumerates each kind's applicable sections). Where
> the section's mode is empty-allowed, an empty body is canonical "considered
> this axis, declared nothing"; where it is body-required, the body carries its
> required content. Omission of the header raises ContractViolation at load. `bindings.<name>` declarations are individually-named
> author sections — N ≥ 0 per handler; no umbrella header required. The trainable
> composition kind uses a different declaration grammar (the composition TOML
> primitive); cross-reference the handler component reference for its
> applicable-sections set.
>
> Start from a canonical template per kind rather than composing declarations from
> scratch. If you are about to delete an "empty section," stop — the section
> header is load-bearing.
>
> The reason: empty-but-present is structurally distinct from forgot. The engine
> has no surrounding IDE / linter ecosystem to catch missing-by-oversight; the
> section headers ARE the linter.

---

{#what-this-discipline-does-not-cover}
## What this discipline does NOT cover

Exhaustive declaration applies to **engine-declared sections** of engine-read
declarations. It does not apply to:

- **Field-name choices inside schema sections.** Field names within
  `output_schema`, `reads`, etc. are author-chosen; only the field-metadata keys —
  the closed set owned at the handler reference's § TOML field type discipline —
  are engine-declared.
- **Prose under `annotations`.** The `annotations` section is truly optional
  and admits free-form author keys. It is exempt from field-discipline; the
  consequence is the documented footgun in which a typed field misplaced under
  `annotations` is silently accepted as prose. The mistake fails loudly
  downstream when the missing declared field is read; it is not a runtime silent
  fallback.
- **Consumer-side configuration.** Consumer code driving the engine (deployment
  scripts, integration helpers, in any language) has no declaration obligations.
  Per the [corpus scope](#corpus-scope) of the principles
  file, the discipline binds engine-conformant handler declarations only.
