---
kind: reference
audience: [authors, integrators, agents]
slug: kind-schemas
component: handler
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#kind-schemas}
# Kind-schema TOML convention

Machine-readable per-kind schemas live alongside this README — one per bare-function handler kind, plus the composition-kind schemas:

**Handler-kind schemas** (per [handler-kinds](#architecture-handler-kinds)):

- [`transform.schema.toml`](transform.schema.toml)
- [`service.schema.toml`](service.schema.toml)
- [`hook.schema.toml`](hook.schema.toml)

**Composition-kind schemas** (per the [composition TOML primitive](#composition-toml)):

- [`trainable.schema.toml`](trainable.schema.toml) — engine-owned-dispatch family
- [`pipeline.schema.toml`](pipeline.schema.toml) — engine-invoking-engine; the nested `pipeline` kind
- [`bundle.schema.toml`](bundle.schema.toml) — pure-substitution; a reusable `nodes` fragment

Each is a TOML document an [integrator](#audiences) or coding agent reads to author a conformant declaration for the corresponding kind. The schemas pair with the prose reference and the worked-example sections the handler component reference owns; the schemas are the structured-data form an agent parses without having to read prose first (Tenet 2 from [principles](#principles)).

The composition-kind family's [membership and realization status](#handler-toml-grammar-composition-kind-roster) are owned by the handler reference's grammar; this folder holds the machine-readable form, extending as schemas land. The per-kind specialization rules are single-sourced in the handler component reference (§ Derived rules).

---

{#authoring-dialect}
## Authoring dialect

TOML is the **current authoring dialect** for declarations; it is not the engine's internal representation. The engine's canonical form is **Pydantic** — the compose-time construction generates Pydantic models from declared `reads` and `output_schema` blocks, and type-checking, hash construction, and dispatch-boundary validation all operate over the Pydantic IR, not over TOML lexical form.

The schemas in this folder describe the TOML dialect specifically. Future authoring dialects (JSON Schema sidecars, direct Pydantic declarations) will convert into the same canonical IR via 1×N converters — one converter per dialect into the IR, not N×N between every pair of dialects. The author surface this folder documents is current-state; the IR-canonical discipline means agents will be able to author against alternate dialects without changing the engine's type-check, hash, or dispatch behavior.

---

{#kind-schemas-convention}
## Convention

Each schema is a **structurally conformant** declaration for its kind. An agent can copy the file, replace the example field declarations with the actual fields, and write a TOML the engine will load — preserving the closed-shape grammar by construction.

The schema carries a small set of metadata above and beyond a vanilla declaration:

{#top-of-file-meta-table}
### Top-of-file `[meta]` table

Carries schema-level metadata:

| Key | Type | Purpose |
|---|---|---|
| `schema_for` | string | The kind this schema is for — a handler kind (`"transform"`, `"service"`, `"hook"`) or a composition kind (e.g. `"trainable"`). The handler reference's composition-kind grammar owns the full composition-kind member set and each member's realization status. |
| `top_level_header` | string | **Handler-kind schemas only.** The literal top-level section header an authored handler declaration carries (`"[transform]"`, `"[service]"`, `"[hook]"`). Composition-kind schemas omit this key — composition declarations identify their kind via `meta.kind = "<kind>"` inside the embedded composition's own `[meta]` block (see the trainable schema's `[meta]` example for the authored shape). |
| `summary` | string | One-line summary of what the kind does. |

The `[meta]` table is **schema-only** — agents authoring an actual declaration do NOT include the schema's `[meta]` table in the authored file. Note that composition-kind declarations carry their OWN `[meta]` block (with `kind` and `name` fields) inside the authored composition declaration; that is the composition declaration's own metadata surface, distinct from the schema's `[meta]`.

{#per-block-leading-comments}
### Per-block leading comments

Every block in a schema is preceded by a comment block declaring:

- `# discipline: <discipline>` — for a declaration **section**, one of the section-discipline modes owned at [exhaustive-declaration](#the-section-discipline-modes) (the owner's mode set is authoritative — the schemas restate no mode list), optionally followed by a parenthetical qualifier (e.g. `required, body-required (exactly one entry)`). `forbidden` is the kind-discipline marker for blocks that MUST NOT appear on this kind (e.g., `output_schema` on a hook). For a **node-sequence or value-supply block** — the second grammatical category exhaustive-declaration names, which a section mode does not describe (a composition's `[[preprocessors]]` array and terminal `[trainable]` node; the `[service_bindings.<name>]` identity-supply, `[trainable.config]` value-supply, and `[merge]` value blocks) — the comment reads `structural (node-sequence)` or `structural (value-supply)` followed by the block's cardinality/grammar discipline.
- `# applicability: <description>` — when the discipline is conditional on the kind's emission channel or other per-kind state, the comment names the condition.
- `# notes:` — short prose framing what the block is for.

{#per-field-declaration-shape}
### Per-field declaration shape

Each example field declared inside a schema block follows the canonical declaration field shape:

```
field_name = { type = "<type-vocabulary-entry>", example = <example-value> }
```

The `example` key is **schema-only** — agents authoring an actual declaration do NOT include `example` in field declarations. The `description` key is admitted **only** on a trainable composition node's `trainable.output_schema` fields, where it is model-facing contract content (the handler component reference's § TOML field type discipline owns the positional admission rule and its `[annotations]` prose home); at every other field position it raises `ContractViolation` at load. The trainable schema's `[trainable.output_schema]` block is the one place these schema examples carry a `description`. The schema's `example` is illustrative; field metadata in a real declaration follows the closed set the handler component reference declares (§ TOML field type discipline).

{#type-vocabulary}
### Type vocabulary

Type strings (`"str"`, `"int"`, `"list[str]"`, `"Literal['a', 'b']"`, etc.) follow the engine's closed vocabulary the handler component reference documents (§ TOML field type discipline). Structured shapes are sub-table-marked rather than token-spelled: a `.fields` sub-table declares a nested object, and the composite slots `.item.fields` / `.value.fields` declare a list / dict of nested records. The schemas use representative type entries; the full vocabulary is the catalog there.

---

{#authoring-workflow}
## Authoring workflow

To author a new declaration using a schema as the starting template:

1. **Pick the schema** matching the kind. If the right kind is not obvious, read [handler-kinds § Comparison](#comparison) — kind discipline is structural and cannot be retrofitted. Trainable training-capture work belongs in the composition-kind path, not the handler-kind path.
2. **Copy the schema** to the declaration's intended path.
3. **Delete the schema's `[meta]` table** — that is schema-only. (Composition-kind authored declarations carry their own `[meta]` block separately; see the trainable schema's example.)
4. **Keep the kind discriminator** verbatim — for bare-function handler kinds, the top-level section header (`[transform]`, `[service]`, or `[hook]`); for composition kinds, the authored `meta.kind = "<kind>"` field.
5. **Replace each block's example fields** with the actual fields. Preserve each block's discipline per its `# discipline:` comment — the behavioral semantics of each section mode are owned by [exhaustive-declaration § The section-discipline modes](#the-section-discipline-modes). In template terms: required, empty-allowed blocks MUST stay (their body MAY become empty); required, body-required blocks MUST keep at least one field; truly optional blocks MAY be removed; `structural (...)` blocks follow the cardinality their comment states (e.g. exactly one `[trainable]`; one `[merge]` entry per fan-in channel).
6. **Drop the `example` key from each field** — that is schema-only.
7. **For bare-function handler kinds**, write the handler as a bare kwarg-only function per R-handler-001 — the engine then constructs the dispatch:

The engine constructs the dispatch wrapper at compose time, supplying the handler a fresh
per-dispatch copy of each resolved `bindings.<name>` value at each dispatch rather than
partial-applying a shared object (the vector-4 copy seal).

   **For composition kinds**, there is no author Python body:

Trainable composition nodes MUST have no author body. The trainable composition declaration
carries only metadata + composition structure (no Python handler file); the engine validates that
no Python handler file is loaded for a trainable composition entry. The dispatch wrapper is fully
engine-generated as `functools.partial(adapter.invoke, **config)` against the bound trainable
backend. Attempting to register a Python handler under the trainable composition kind raises
ContractViolation at engine startup.

The handler component's conformance checks fire against the result; passing them is the test that the schema was followed.

---

{#what-the-schemas-do-not-cover}
## What the schemas do NOT cover

- **Pipeline-declaration shape.** A handler or composition declaration declares its single kind; the pipeline declaration composes them into a typed dataflow graph (with `nodes` entries discriminated by `kind = "handler" | "composition"`, `bindings = {...}` overrides, service-typed binding supply, and composition embeds). That shape is the pipeline component's territory.
- **Service-type declaration shape.** Entries in `service_bindings` reference qualified service-types resolved at pipeline-declaration load. The service-type TOML grammar and the service-impl dispatch contract are owned by the service-type component reference; [handler-resolution](#architecture-handler-resolution) owns the resolution mechanism that consumes them.
- **Backend adapter authoring.** A trainable backend's adapter (the code that submits the composition's `trainable.output_schema` as the backend's structured-output constraint per R-handler-005, the literal-equal rule) is engine-implementation territory; the schemas describe the author surface.
