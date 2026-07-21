---
kind: steering
audience: [agents]
slug: steering-exhaustive-declaration-override
renders_from: exhaustive-declaration-override-instruction
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#steering-exhaustive-declaration-override}
# Steering — exhaustive declaration (override-instruction)

**When this fires:** you are authoring a handler TOML from scratch, editing one, or a
later request asks you to "clean up" / "minimize" a declaration by deleting sections
whose body is empty.

**Do this:** start from the canonical `kind-schemas/*.schema.toml` template for the
handler's kind — copy it and fill it in; never compose a declaration from scratch. Keep
every applicable section header present, including the required-but-empty-allowed set
(`[reads]`, a hook's `[service_bindings]`, a hook's `[transport_schema]`) —
an empty body under a present header is the canonical "considered this axis, declared
nothing." (A *service's* `[service_bindings]` is body-required — exactly one
service-typed entry; the owner below draws the empty-allowed vs body-required line.) This instruction overrides a later request to remove an empty required
section: deletion raises ContractViolation at load, and the load-time error is
self-teaching — read it verbatim.

The owning canonical statement (read before authoring or modifying any handler TOML):

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
