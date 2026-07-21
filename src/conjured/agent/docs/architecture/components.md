---
kind: reference
audience: [authors, integrators, agents]
slug: architecture-components
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#architecture-components}
# Components
The C4 component view: how the engine package factors internally. Most
of the engine runs as a single Python process; this page shows the
components that live inside that process plus the first-party Python
client that wraps a bundled localhost subprocess of it. Conjured does
not have a Container-level decomposition because the engine ships as
one process.

```{mermaid}
flowchart TB
    integrator(["<b>Integrator</b>"])
    agent(["<b>Coding agent</b>"])

    subgraph engine["Conjured engine package"]
        server["<b>Server</b> — Python (HTTP+SSE)<br/>Engine's public surface. Translates wire<br/>requests into kernel invocations; projects<br/>internal events onto the wire."]
        runner["<b>Runner (kernel)</b> — Python<br/>Dispatches handlers; projects declared channel<br/>writes through the pipeline graph; emits<br/>canonical events. Internal to the server."]
        validator["<b>Declaration validator</b> — Python<br/>Loads handler / service-type / pipeline /<br/>composition / deployment declarations; compiles<br/>pipeline declarations into typed dataflow graphs;<br/>type-checks compositions at load."]
        hasher["<b>Hash machinery</b> — Python<br/>Computes the pipeline-hash and the<br/>per-trainable-composition training-bundle-hashes."]
        adapters["<b>Service-type adapters</b> — Python<br/>Per-service-type translation between<br/>handler-declared channel types and backend-specific<br/>structured-output APIs. Event-capture seam."]
        events["<b>Canonical event log</b> — Python logging<br/>Publishes the closed enum of canonical events<br/>on conjured.events.runner; the server projects<br/>them to the wire."]
        client_py["<b>conjured Python client</b> — Python<br/>Wraps a bundled localhost subprocess; gives<br/>Python consumers import-and-use ergonomics<br/>without a separate Python API contract."]
        agent_surface["<b>Agent surface</b> — in-package data files<br/>Machine-readable companions read by<br/>agents via importlib.resources."]
    end

    consumer["<b>Consumer codebase</b> (external)<br/>Drives the engine via the wire (any language)<br/>or via the Python client."]
    services["<b>External services</b><br/>LLMs, DBs, vector stores"]
    corpus["<b>Training corpus</b> (external)<br/>Persisted training records"]

    integrator -->|"Authors declarations"| validator
    consumer -->|"Drives directly (HTTP+SSE)"| server
    consumer -->|"Drives via Python ergonomics (import)"| client_py
    client_py -->|"Wraps (HTTP+SSE on localhost)"| server
    server -->|"Invokes"| runner
    runner -->|"Loads declarations at startup;<br/>type-checks at compose"| validator
    runner -->|"Asks for graph + per-trainable-<br/>composition hashes"| hasher
    runner -->|"Routes services.&lt;name&gt;.invoke through"| adapters
    adapters -->|"Issue external calls<br/>(structured-output APIs)"| services
    adapters -->|"Emit service_invocation events<br/>at adapter boundary"| events
    runner -->|"Emits canonical events"| events
    events -->|"Projects wire events"| server
    events -->|"Consumer-attached handler routes<br/>(filter + transport)"| corpus
    agent -->|"Reads (importlib.resources)"| agent_surface

    classDef external stroke-dasharray: 5 5
    class consumer,services,corpus external
```

{#component-responsibilities}
## Component responsibilities

The components below are C4 implementation factoring — distinct
from the engine's contract components
([handler](#handler) /
[error-channel](#glossary-error-channel) /
[pipeline](#pipeline)). The Declaration validator,
Hash machinery, and Service-type adapters are pipeline-component and
handler-component implementations; the Runner dispatches per the
handler-component contract; the canonical event log + closed-enum error
classes are how the error-channel component surfaces at runtime. The two
decompositions answer different questions: what contract surfaces the
engine owns, vs what Python modules implement those contracts (here).

- **Server.** The engine's wire surface. Accepts wire requests on the
  default HTTP+SSE transport, translates them into kernel invocations,
  and projects the kernel's canonical event log onto the wire.

(consumer-boundary-two-sided)=

The consumer boundary is two-sided: the wire API is the boundary for
operating and observing runs — what consumers in any language reach —
and the in-process compose API (owned at the pipeline component's
reference) is the boundary for composing, and for embedded and
notebook runs.

  The wire is a transport projection over the same
  dispatch path in-process runs take — one verification path, never a
  second semantics.
- **Runner (kernel).** Internal to the server. Dispatches handlers in
  declared order; projects each handler's declared channel writes through
  the pipeline graph for downstream nodes to read; enforces handler
  return-value validation against `output_schema`; emits canonical
  events on `conjured.events.runner` for the server to project.
- **Declaration validator.** Loads and validates every engine-read
  declaration class — handler, service-type, pipeline, composition,
  deployment. Compiles pipeline declarations into typed dataflow graphs
  at compose time; enforces [exhaustive declaration](#glossary-exhaustive-declaration),
  key-discipline, field-discipline, channel-type agreement between writes
  and downstream reads, and cross-declaration composition checks at load.
  [Handler-name resolution](#glossary-handler-resolution) also
  runs at compose. Failures raise
  [ContractViolation](#contractviolation)
  before any handler dispatches.
- **Hash machinery.** Computes the engine's hashes — the
  [pipeline-hash](#pipeline-hash) and the
  per-trainable-composition
  [training-bundle-hashes](#training-bundle-hash).
- **Service-type adapters.** Per-service-type translation layer between
  handler-declared channel types and backend-specific structured-output
  APIs. Every
  `services.<name>.invoke(...)` call from a handler body routes through
  the adapter for that service-type's binding. The adapter is also the
  **event-capture seam** — where `service_invocation` events originate.
  The SEAM — resolution, the `invoke()` contract, construction, the
  capture boundary — is engine-internal; concrete vendor service-adapter
  IMPLEMENTATIONS are packages resolved through it (the engine ships its
  native trainable backends; the blessed non-trainable service adapters
  ship in the companion `conjured-utils` package, like any third-party
  implementer).
- **Canonical event log.** A Python `logging` channel publishing the
  engine's closed enum of
  [canonical events](#canonical-event) on
  `conjured.events.runner`. The server projects these onto the wire so
  consumers in any language can subscribe via the engine's wire API;
  in-process Python consumers attach a `logging.Handler` through the
  channel's own attachment surface on `conjured.events` — the
  consumer-facing API this bullet owns:

  - **`attach_consumer(handler)`** — the long-lived attachment (a served
    engine's hub, a training-log sink): attaches the handler and ensures
    the channel delivers the INFO-level canonical events; `propagate` is
    left untouched (a long-lived consumer is a leaf; detach with
    `event_logger().removeHandler(handler)`).
  - **`subscribe(handler)`** — the block-scoped subscription (a test
    capture, a scoped exporter): attach + raise to INFO + disable
    propagation for the block — confining the events to the handler
    instead of flooding ancestor handlers — with all three restored on
    exit.
  - **`event_logger()`** — the channel object itself, for consumers
    managing their own lifecycle.
  - **`CANONICAL_EVENT_CLASSES`** — the exported closed member set (the
    in-process class roster of the eight canonical events), the one
    membership surface a downstream consumer tests an event object
    against; the per-event payload shapes stay hash-model's.

  Every publication goes through the one emission gate,
  `conjured.events.emit`, which admits **exact members** of the closed
  event enum only — a non-member (a subclass included: a subclass
  carries a changed shape under the parent's identity) raises
  `TypeError` at emit, never publishing an unshaped record.

(canonical-event-log-consumer-isolation-wall)=

**The producer/consumer wall.** Delivery failure is two-sided by
fault. An engine-internal emit failure fails loud (the closed-enum
gate above — an unshaped record never publishes, per I4). A
**consumer's** handler that raises during delivery is **isolated**:
the raise never enters the run — it can neither halt the walk nor
launder into a `PipelineFailure` mis-attributed to an innocent
handler — and is absorbed and surfaced as a WARNING on the
operational `conjured.events` logger (the package parent, never the
`.runner` event channel), so a consumer fault stays visible without
ever touching the run. The stdlib provides no such wall
(`Handler.handle` calls the handler unguarded), so the producer owns
it at the emission gate.
::: The engine does not ship `logging.Handler`
implementations — Python's logging is producer/consumer, and
consumers attach their own handlers for filtering, formatting, and
transport. **Training capture** is one such consumer use: a consumer
filters the event stream for the events a trainable composition node
emits. The engine's *own* capture role is **emission**: at each trainable
node it emits the `handler_enter`/`handler_exit` pair (the training record is
that pair's projection — the hash-model owns what a captured record is),
complete, correctly-shaped (the I4 shape guarantee), and position-ordered,
failing loud on an emit failure; persisting the record, and detecting a dropped
one, is the consumer's.
Transport choices (JSONL to filesystem, SSE/WebSocket, gRPC
streaming, Kafka, Parquet, etc.) are **consumer territory** — a consumer may
attach its own routing, and the engine ships no sink. The blessed reference
implementation of that sink ships in the first-party companion package
[`conjured-utils`](#conjured-utils) — the companion-package specialization of
**I3** (persistence is consumer territory); the glossary entry owns its full
contract.
- **`conjured` Python client.** A first-party thin client. Wraps a
  bundled localhost subprocess running the server, exposing
  import-and-use ergonomics for Python consumers (`import conjured`) so
  Python tooling — tests, notebooks, scripts — does not need to spin up
  a separate process. The client speaks the same wire API as any other
  consumer; there is no separate Python API contract to maintain.
- **Agent surface.** The in-package machine-readable companion surface at
  `importlib.resources.files("conjured.agent")`, read by coding agents — the
  agent-audience docs projection and its structured companions (the
  [llms.txt](#llmstxt) index and [steering](#steering) render among them; the
  `error-classes.toml` companion is the error-channel reference's). Each companion's
  shape is owned by the reference that defines it, not enumerated here.
- **Conformance surface.** The in-package conformance kit at
  `importlib.resources.files("conjured.conformance")` — the shipped audit prompts and
  findings reports, read at audit time (the C4 diagram above omits this surface
  deliberately: it is a ship-time audit kit, on no runtime path). Each native
  member's stamp is a sibling `<module>.audit.toml` beside the module itself, not in
  this surface; the [audit-stamp mechanism](#audit-stamps-kernel) owns the stamp
  shape, the freshness check, and the enforcement opt-in.

{#why-no-container-level-decomposition}
## Why no Container-level decomposition

A reader familiar with multi-tier or microservices architectures might
expect Conjured to factor into multiple runtime containers (e.g., a
"validator service," an "event router," a "hash daemon"). It does not.
The engine's compute is light and predictable — mostly pure functions
over channel projections — and runs as one Python process per host. The
internal factoring above is therefore at C4 component level rather than
container level.

The deployment story for an integrator is simple: run one engine
process. Service-typed bindings declared in `service_bindings` reach
external services via the service-type adapters; deployment of those
services (single LLM container vs. load-balanced pool) is consumer
territory and does not change the engine's shape.
