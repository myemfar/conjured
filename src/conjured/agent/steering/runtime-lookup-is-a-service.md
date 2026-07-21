---
kind: steering
audience: [agents]
slug: steering-runtime-lookup-is-a-service
renders_from: when-not-to-use-content-bindings
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#steering-runtime-lookup-is-a-service}
# Steering — runtime ID-lookup is a service, not a binding

**When this fires:** a handler needs a value looked up at dispatch time from a runtime
key (an ID arriving on a `reads` channel), and you are about to model it as a
`bindings.<name>` entry.

**Do this:** model it as a **service handler** — the lookup is the service's one
external call, mediated by its bound service-type adapter; the response returns through
the service's declared output ports. A compose-time binding is for values fixed at
composition and reused across every dispatch; if the choice happens per dispatch based
on graph state, the handler is a service.

The owning canonical statement:

{#when-not-to-use-content-bindings}
## When NOT to use content bindings

Compose-time bindings (`bindings.<name>`) supplied by external declaration file path are
for **fixed configuration** — values that do not vary across dispatches of a composed
pipeline. The canonical use cases are per-game-mode bindings (a "tavern_dialogue" mode
binding a specific NPC + scene + tone set; a "combat" mode binding a different set),
per-scene bindings (a scene's narrator instructions, NPC roster, environment
description), per-deployment-cohort bindings (an A/B variant of prompt scaffolds). These
are values fixed at composition time and reused across every dispatch. The same external
declaration file MAY be referenced by more than one handler's `bindings` — the engine
resolves it once and shares the resolved configuration across them (a `battle.toml` bound
by several combat handlers resolves once).

**Runtime ID-lookup is NOT a content-binding use case.** If the handler needs to look up
a value at dispatch based on a runtime ID (e.g., "load the NPC declaration named in
`npc_id`, where `npc_id` is a `reads` channel value from upstream"), that work belongs
in a **service handler** — not a binding. The service handler's external call is the ID
lookup; the bound service-type's adapter mediates the lookup against a database,
filesystem, or REST endpoint; the response is returned as the service's declared
[output ports](#output-port) and routed onto channels by the node's [write-map](#write-map).

The two use cases split cleanly:

- **Per-game-mode fixed binding**: a transform with `bindings.npc` whose value is
  supplied by external declaration file path at composition time. The same NPC is the
  handler's reference data across every dispatch of the composed pipeline. Different
  game modes are different compositions; each composition's pipeline-hash reflects the
  bound NPC.
- **Runtime NPC lookup by ID**: a service handler with `reads.npc_id` and
  `service_bindings.npc_store`; the body calls `services.npc_store.invoke(npc_id=npc_id)`;
  the response is the NPC data. The service-type adapter mediates the lookup. Every
  dispatch resolves the ID at runtime.

The two use cases differ in *when* the NPC is chosen: at compose time (binding) vs at
dispatch time (service lookup). If the choice happens per dispatch based on graph state,
the handler is a service.
