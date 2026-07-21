---
kind: steering
audience: [agents]
slug: steering-service-invoke-call-convention
renders_from: the-service-type-adapter/body-side-call-convention
---

{#steering-service-invoke-call-convention}
# Steering — the `services.<name>.invoke(...)` call convention

**When this fires:** you are writing a service-kind handler body and about to call the
bound service.

**Do this:** pass the backend call's domain kwargs directly —
`services.<name>.invoke(text=query_text)` — one keyword per value, nothing else. The
engine packs them into the adapter's `input_payload` itself; passing `input_payload=`
yourself nests the domain kwargs one level too deep and is never correct.

The owning canonical statement:
