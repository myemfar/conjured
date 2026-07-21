---
kind: reference
audience: [authors, integrators, agents]
slug: architecture-context
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#architecture-context}
# System context

The C4 system-context view: the engine and the actors it interacts with.
The engine ships as a [server](#server) process;
its public surface is the [server's API](#api-contract),
not a Python import. Consumers reach the engine over the wire (default
HTTP+SSE) in any language. The first-party `conjured` Python package
wraps a bundled localhost subprocess so Python consumers get an
import-and-use experience without a separate Python API contract.

```{mermaid}
flowchart TB
    integrator(["<b>Integrator</b><br/>Deploys the engine, composes pipelines,<br/>drives it via the wire."])
    non_coder(["<b>Non-coder author</b><br/>Composes pipelines and content via<br/>authoring tools, mediated by agents."])
    agent(["<b>Coding agent</b><br/>Reads the engine's agent surface; assists<br/>integrators and non-coder authors."])

    conjured["<b>Conjured engine</b><br/>Server process exposing a wire API; ships<br/>handler composition into typed dataflow graphs<br/>+ pipeline-as-training-contract derivation."]

    consumer["<b>Consumer codebase</b> (external)<br/>Drives the engine via its wire API (in any<br/>language); owns persistence and deployment."]
    services["<b>External services</b><br/>LLM endpoints, vector stores, classifiers,<br/>databases — invoked by service-typed bindings<br/>through their service-type adapters."]
    training["<b>Training pipeline</b> (external)<br/>Consumes the captured training-projection<br/>corpus to fine-tune model artifacts."]

    integrator -->|"Authors pipelines, services,<br/>deployment config"| consumer
    non_coder -->|"Composes via authoring tools (TOML)"| consumer
    agent -->|"Reads agent surface (in-package<br/>machine-readable companions)"| conjured
    consumer -->|"Drives — HTTP+SSE (or via Python<br/>client wrapping localhost subprocess)"| conjured
    conjured -->|"Service-type adapters invoke<br/>(external calls)"| services
    conjured -->|"Emits canonical events<br/>(training-projection capture)"| training

    classDef external stroke-dasharray: 5 5
    class consumer,services,training external
```

{#framing}
## Framing

The engine is a **typed dataflow language** hosted in Python — a
compose-time type-checker plus a runtime dispatcher for graphs of typed
nodes (handlers) and typed channels (the declared reads and writes
between them). It ships as an importable package with a bundled server
process, behind a
[two-sided consumer boundary](#consumer-boundary-two-sided) (owned at the
components view's *Server* entry); on the wire side
([API contract](#api-contract)) the engine's Python-ness is
implementation detail invisible to the consumer.

The audiences interact with it (defined in
[principles § Audiences](#audiences)):

- **Integrators** read the reference docs and deploy
  the engine — typically as a sidecar server or as the bundled localhost
  subprocess wrapped by the `conjured` Python client. They author
  pipelines, supply service implementations, and own the runtime.
- **Non-coder authors** compose pipelines through authoring tools — TOML
  templates, agent-mediated flows, Studio's graph-rendering UI per
  [Tenet 1](#principles). The engine surfaces its contract
  in forms an authoring tool can render; non-coder authors do not read
  engine source.
- **Coding agents** read the engine's in-package agent surface — the
  machine-readable companions, the [llms.txt](#llmstxt) index, the
  agent-audience markdown bundle, and the [steering](#steering)
  content — and assist both other audiences.
  Steering exists so an agent
  trained on mainstream paradigms (procedural orchestration, ad-hoc
  handler composition, schemas-as-validation rather than
  schemas-as-types-in-a-graph) primes correctly on the engine's
  divergences per [Tenet 2](#principles).

External services are reached only through declared
[service-typed bindings](#service-type) in
`service_bindings`, routed through their service-type adapters. The
training pipeline is downstream of the engine — the runner emits
canonical events that a consumer-side log handler routes to a training
corpus.
