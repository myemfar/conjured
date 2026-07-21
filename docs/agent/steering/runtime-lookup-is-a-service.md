---
kind: steering
audience: [agents]
slug: steering-runtime-lookup-is-a-service
renders_from: when-not-to-use-content-bindings
---

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
