---
kind: steering
audience: [agents]
slug: steering-exhaustive-declaration-override
renders_from: exhaustive-declaration-override-instruction
---

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
