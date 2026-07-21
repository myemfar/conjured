---
kind: steering
audience: [agents]
slug: steering-service-invoke-call-convention
renders_from: the-service-type-adapter/body-side-call-convention
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#steering-service-invoke-call-convention}
# Steering — the `services.<name>.invoke(...)` call convention

**When this fires:** you are writing a service-kind handler body and about to call the
bound service.

**Do this:** pass the backend call's domain kwargs directly —
`services.<name>.invoke(text=query_text)` — one keyword per value, nothing else. The
engine packs them into the adapter's `input_payload` itself; passing `input_payload=`
yourself nests the domain kwargs one level too deep and is never correct.

The owning canonical statement:

The handler body passes the backend call's **domain
kwargs directly** — `services.<name>.invoke(text=query_text, model=config["model_name"])`
— one keyword per value the call carries, and nothing else. The engine packs those domain
kwargs into the adapter's `input_payload` and supplies the rest of the adapter's closed
dispatch-kwargs (`service_name`, the `caller_*` fields, the config kwargs, and
`**transport_extra`) itself — the service-type reference's § Closed dispatch-kwargs owns
that adapter-side surface. So the body **never** passes `input_payload=` (that would nest
the domain kwargs one level too deep) and never passes the engine-supplied kwargs: the
body-side surface is only the domain kwargs.
