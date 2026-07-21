---
kind: reference
audience: [integrators, agents]
slug: server-reference
component: server
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#server-reference}
# Server-component reference

The per-component reference for the **server** — the engine's public wire surface. The
[server](#server) is the deployable process that exposes the engine's
[API contract](#api-contract) ([API](#glossary-api)) — the wire side of the consumer boundary,
whose two-sided split is owned at the components view's
[§ Component responsibilities](#component-responsibilities), *Server* entry. The
[components view](#architecture-components) names the server as the C4 component
that *"accepts wire requests on the default HTTP+SSE transport, translates them into kernel
invocations, and projects the kernel's canonical event log onto the wire"*; this doc is where
that wire surface is **specified** — the protocol the server realizes.

The server ships **one first-party reference protocol** over **three endpoints**: a **run
trigger** (HTTP — run a pipeline), an **event stream** ([SSE](#canonical-event) — project a
run's [canonical events](#canonical-event) onto the wire), and a **token stream** (SSE —
deliver a streamed run's provisional token fragments). A consumer needing a different
protocol writes a gateway in front of this one (consumer-territory, one-way conformance per
[I3](#invariants-and-derived-rules)); the engine never grows a protocol-per-consumer.

The reference stack the server is built on is **Starlette + uvicorn + sse-starlette** (an ASGI
core, an ASGI server, and a maintained W3C-SSE layer). The framework is named where the
implementation is described; the **serving seam** — how a deployment becomes a running service
— stays swappable, the same way every engine seam ships one blessed default.

What lives here:

- The **run-trigger contract** — the HTTP endpoint that runs a pipeline: method, path, the
  request shape (which pipeline, its inputs, and the optional run-scoped parameters), and the
  success-response shape.
- The **event-stream contract** — the [SSE](#canonical-event) endpoint that projects a run's
  [`conjured.events.runner`](#event-log-specification) stream: method, path, the
  event-to-frame mapping, and run↔stream correlation.
- The **token-stream contract** — the SSE endpoint that delivers a streamed run's provisional
  token fragments: method, path, the frame vocabulary (`token` / terminal `end`), and its
  separation from the canonical event stream.
- The **wire error surface** — how an engine error class reaches an HTTP caller, and the
  HTTP-transport status selection the [error-channel reference](#R-error-channel-005) defers.
- The **inbound-binding configuration** — where the consumer→engine endpoint itself (bind
  address, port, TLS) is configured.

What is **owned elsewhere and cross-referenced, never restated here**:

- The **canonical event model** — the closed set of event types, each event's payload shape,
  the keying, and the paired-event semantics — is owned by hash-model's
  [§ Event-log specification](#event-log-specification); the SSE contract maps those events to
  wire frames and cites the table rather than re-enumerating it.
- The **structured error payload, the closed error-class enum, and the RFC 9457 HTTP wire
  projection** (the `application/problem+json` envelope, the per-class status pins, the
  `to_problem_details` helper) are owned by the error-channel reference
  ([R-error-channel-001](#R-error-channel-001), [R-error-channel-004](#R-error-channel-004),
  [R-error-channel-005](#R-error-channel-005)); this doc settles only the status selection that
  rule explicitly hands to HTTP-transport territory.
- The **`RunResult` shape** the success response carries is owned by the pipeline reference's
  [§ Pipeline result](#pipeline-result-runresult-shape); the **API-input boundary** the request
  body seeds is owned by [R-pipeline-001](#R-pipeline-001-api-inputs-enforcement).
- The **whole-run-budget timeout** is owned by the error-channel reference's
  [§ Consumer pipeline-level timeout](#consumer-pipeline-level-timeout-request-param); the
  **never-hashed** exclusion of inbound-binding (and every transport) config is owned by
  hash-model's [§ What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs).
- The **`streamable` declaration and its placement rule** — which pipelines can stream — are
  owned by the trainable kind-schema and
  [R-pipeline-001's streamable terminal-node clause](#R-pipeline-001-streamable-terminal-node);
  the **provisional-fragments posture** is owned by the pipeline reference's
  Orchestration-scope seal, transcluded in [§ The token stream](#token-stream).

---

{#run-trigger}
## The run trigger

```
POST /runs
```

Runs one pipeline and returns its result. This is the wire projection of the engine's
invocation unit — one pipeline plus its inputs, yielding one result or one error.

{#run-trigger-request}
### Request

The request body is a JSON object:

| Field | Required | Carries |
|---|---|---|
| `pipeline` | yes | the qualified name of the pipeline to run — resolved among the pipelines the engine serves under its one [deployment](#R-deployment-002). |
| `inputs` | yes when the pipeline declares inputs | a JSON object seeding the pipeline's declared input channels — the values for the pipeline's API-boundary [`inputs`](#R-pipeline-001-api-inputs-enforcement). |
| `pipeline_run_id` | no | a consumer-supplied run identifier; the engine accepts it verbatim and threads it through the run's events and result (the generated form, when absent, is owned at [hash-model § canonical event types](#canonical-event-types)). Supplying it lets a consumer open the event stream for the run *before* triggering it. |
| `timeout_ms` | no | the whole-run budget — the [consumer pipeline-level timeout](#consumer-pipeline-level-timeout-request-param). |

How the request body seeds the run is owned by the pipeline reference:

When a pipeline declares `inputs`, the API invocation path validates the incoming request's
key-set against the declared input fields before dispatching the first node — presence of
every declared field, never values.
Missing field: ContractViolation at the API boundary — no node dispatches; no `pipeline_error`
event fires because the pipeline never started. An incoming key that is not a declared input
field is **not admitted but not an error**: the runner seeds only the declared input channels,
so an extra never becomes a channel and never reaches any handler. The missing-field
ContractViolation's message names any unrecognized keys present in the request — so a typo'd
key surfaces in the same error as the declared field it failed to supply. A declared input
field supplied with a type- or constraint-violating value passes the API boundary and surfaces
as SchemaValidationError at the seeded channel's first consumer — its reads-projection or its
merge fold, whichever the runner dispatches first (per R-error-channel-001's key-set routing;
the `inputs` / `outputs` field-resolution clause guarantees at least one reading node exists,
so a consumer always exists).

A `pipeline` naming no served pipeline, a body that is not valid JSON, or a body missing the
required `pipeline` field is a **wire-level** rejection that arises before any engine error
class — see [§ Wire error surface](#trigger-error-responses).

{#run-trigger-response}
### Response — synchronous

**The trigger blocks for the duration of the run** and returns the run's outcome directly.
On success the response is `200 OK` carrying the run's [`RunResult`](#pipeline-result-runresult-shape):

| Field | Type | Carries |
|---|---|---|
| `state` | `Mapping[str, object]` | the run's final channel values — every **outer-pipeline** channel the graph wrote. A composition's *internal* [scoped channels](#scoped-channel) are not exposed here (encapsulation): a composition's contribution reaches `state` only through its declared outputs, flattened to outer channels. |
| `run_id` | `str` | the invocation identifier: the consumer's `pipeline_run_id` verbatim when one was supplied at invocation, else an engine-generated identifier in the structured, sortable form owned at [hash-model § canonical event types](#canonical-event-types) |

On the wire that `RunResult` is a JSON object — the `state` mapping serialized as a JSON
object of the run's JSON-serializable outer channels, `run_id` as a JSON string. There is **no
`success` / `ok` / `status` envelope field**: the engine's output channel and error channel are
distinct surfaces
([R-error-channel-004](#R-error-channel-004)), so a returned value *is* the success signal. On
the wire that discipline is expressed as the **HTTP status class**: a `2xx` body is a
`RunResult`; a non-`2xx` body is the structured error ([§ Wire error surface](#trigger-error-responses)).
The caller dispatches on which it received, exactly as an in-process consumer dispatches on
"a value returned vs an exception raised."

**Why synchronous.** The trigger is the faithful wire projection of the engine's invocation:
one pipeline plus its inputs in, one `RunResult` or one raised error out. Three properties follow
and motivate the blocking default:

1. **The two-channel discipline maps directly onto HTTP status.** An async `202 + run_id`
   trigger would re-introduce at the wire exactly the status-envelope the engine forecloses
   in-process ([R-error-channel-004](#R-error-channel-004)) — the caller would poll or stream
   for a terminal-state signal. A blocking trigger keeps the wire discriminator structural (the
   status class), not a field.
2. **The server stays stateless per run**
   ([§ Correlating the run with its event stream](#run-trigger-correlation-history-less-hub)
   owns the history-less-hub property). The result returns on the call, not later. (Async
   delivery over SSE would require retaining each in-flight run's eventual `RunResult` — which
   carries more than the [`pipeline_complete`](#canonical-event-types) event's
   `outputs_snapshot`, so SSE frames alone cannot reconstruct it.)
3. **The blessed default integration is the bundled-localhost Python client**, whose ergonomics
   are a blocking call → `RunResult`.

The trade-off: a long-running pipeline holds the HTTP connection for the run's
duration (the [`timeout_ms`](#consumer-pipeline-level-timeout-request-param) budget bounds it);
the SSE stream carries live progress meanwhile. A consumer that needs to *decouple* submission
from completion (a queue, a webhook on completion) builds that as a gateway in front of the
blessed protocol — a consequence of the extensible-first posture, not a gap in it.

{#run-trigger-correlation}
### Correlating the run with its event stream

`pipeline_run_id` is the join between a triggered run and its [event stream](#event-stream). A
consumer that wants live progress **mints a `pipeline_run_id`, opens
`GET /runs/{pipeline_run_id}/events` first, then `POST /runs` with the same id** — the engine
accepts the consumer-supplied identifier, so the stream and the run share it. A consumer that
supplies none receives the engine-generated `run_id` on the `RunResult` and may reconcile the
run against an out-of-process event sink after the fact. The identifier itself, its generated
form, and its role as the cross-event correlation key are owned by
[hash-model § canonical event types](#canonical-event-types).

(run-trigger-correlation-history-less-hub)=

The server is **stateless per run** and history-less: it holds no run registry, keeps no
event or fragment history, and replays nothing. The stream endpoint subscribes to the run's
frames before its streaming response starts, so no frame is lost between subscription and the
response. A stream opened mid-run therefore receives frames from its subscription onward only — so a run's projection is complete exactly for a
stream whose subscription **precedes the run's trigger** (the open-stream-then-POST flow
above).

---

{#trigger-error-responses}
## Wire error surface

When a run halts, the wire response is the engine's **RFC 9457 HTTP wire projection**, owned in
full by the error-channel reference ([R-error-channel-005](#R-error-channel-005)): the response
`Content-Type` is `application/problem+json`, the body is the Problem Details envelope over the
structured error payload, and the closed [error-class](#error-class) enum
([R-error-channel-001](#R-error-channel-001)) is projected per class by the per-class tables
that rule owns. This reference does **not** restate the envelope, the payload fields, or the
status values that projection already pins for a value-level
[`SchemaValidationError`](#schemavalidationerror) and a runtime
[`PipelineFailure`](#pipelinefailure) — follow [R-error-channel-005](#R-error-channel-005) for
those.

What this reference settles is the **status selection the projection explicitly hands to
"HTTP-transport territory"** — the cases the error-channel rule does not pin because they are
wire concerns, plus the rejections that arise *before* any engine error class is raised:

| Wire condition | HTTP status | Notes |
|---|---|---|
| Malformed request — not JSON, the required `pipeline` field missing, or **any request field structurally malformed** (`inputs` not an object, `pipeline_run_id` not a string, `timeout_ms` not an integer) | `400 Bad Request` | A transport-level request-shape rejection covering every structurally-malformed request field; no pipeline is invoked, no engine error class is raised, no event fires. |
| `pipeline` names no served pipeline | `404 Not Found` | The named pipeline is not loaded by the engine; a routing miss, not a run failure. |
| Missing a declared pipeline **input** | `400 Bad Request` | A [`ContractViolation`](#contractviolation) at the API boundary — no node dispatches and no run starts; the boundary routing is owned by [R-error-channel-001 § key-set routing](#R-error-channel-001-key-set-routing). This is the `ContractViolation` status the RFC 9457 projection leaves caller-supplied. |
| A runtime [`ContractViolation`](#contractviolation) raised mid-dispatch — **not** the API-boundary missing-input case above (e.g. a handler returns an undeclared `output_schema` key) | `502 Bad Gateway` | A handler-produced structural fault surfacing after the run started: the handler body is upstream from the engine, the same rationale [R-error-channel-005](#R-error-channel-005) gives a value-level [`SchemaValidationError`](#schemavalidationerror). |
| Wrong method or unknown path | `405` / `404` | Standard HTTP transport behavior. |

Two scoping facts hold this surface together:

- **The trigger maps run-time failures only.** A compose-time [`ContractViolation`](#contractviolation)
  — a pipeline that does not type-check — halts at load ([R-pipeline-001](#R-pipeline-001)),
  before any request reaches a served pipeline; it is a deployment/load concern, never a
  per-request status.
- **A deployment MAY remap status codes.** The per-deployment status-code-override the RFC 9457
  projection defers is part of this HTTP-transport territory; where it lives is the
  inbound-binding configuration ([§ Inbound-binding configuration](#inbound-binding)).

---

{#event-stream}
## The event stream

```
GET /runs/{pipeline_run_id}/events
```

Projects a single run's [canonical event](#canonical-event) stream onto the wire as
Server-Sent Events. The response `Content-Type` is `text/event-stream`; the connection stays
open for the run and closes when the run reaches a terminal event — a **live** stream (one
that has delivered any frame) takes no idle bound; only a stream still waiting for its first
frame is bounded by the construction surface's pre-first-frame idle bound
([§ The construction surface](#construction-surface)). The `{pipeline_run_id}` path
segment is the run to subscribe to (carried in the path under standard URI encoding); the
server filters [`conjured.events.runner`](#event-log-specification) to that run.

{#event-stream-frames}
### Event-to-frame mapping

The canonical event model — the **closed set** of event types, each event's payload fields, the
keying, and the paired-event semantics — is owned authoritatively by hash-model's
[§ Event-log specification](#event-log-specification); that page states other docs cross-reference
it rather than re-enumerate the events. Each canonical event projects to one SSE frame:

- `event:` — the canonical event's type name, verbatim from the
  [canonical event types](#canonical-event-types) table.
- `data:` — the event's payload as a JSON object, **the canonical in-process payload** for that
  event from the same table. The RFC 9457 wire form is the HTTP **error**-surface projection
  only ([R-error-channel-005](#R-error-channel-005)); the event stream carries the canonical
  event payloads, not the Problem Details envelope.

(event-stream-frames-data-null-serialization)=

The `data:` payload serializes optional fields as **explicit `null`**, never omitted — the
canonical in-process serialization, NOT the HTTP error surface's RFC 9457 null-omission.
(The same include-nulls posture the error-channel reference's § Optional field serialization
fixes for error payloads, applied here to every event frame.)
- `id:` — for a frame whose event carries a `handler_position` (a per-dispatch event), the
  dispatch's `(pipeline_run_id, handler_position)` composite rendered exactly as hash-model's
  [composite rendering](#correlation-id-derivation-composite-rendering) fixes it (`run_…:0`) — so a wire
  consumer can de-duplicate frames and verify per-dispatch ordering; a run-level frame, which carries no `handler_position`,
  omits `id:`. This is byte-identical to the string the dispatch's [`correlation_id`](#correlation-id)
  carries *for a service dispatch* — the same composite, the service-pair single-field join, not a
  universal per-frame handle (see [§ Pairing and terminal frames](#event-stream-correlation-and-errors)).

(event-stream-frames-run-scoped-completeness)=

The frames a run's stream carries are the **run-scoped** events — every event the
[canonical event types](#canonical-event-types) table keys by `pipeline_run_id`. The
compose-time events that table marks (the ones carrying no `pipeline_run_id`) fire at load and
are therefore not part of a run-scoped stream. The completeness of the
projection is load-bearing: a dropped event is a hole in the [training projection](#invariants-and-derived-rules)
the event log exists to make reconstructable ([replayability](#replayability)), so the server
projects every run-scoped event — the run's `pipeline_start` first, each dispatch's events in
`handler_position` order, the terminal frame last — and fails loud on a projection failure
rather than silently skipping a frame. Completeness therefore holds only **per stream whose
subscription precedes the run's trigger** — the
[history-less hub](#run-trigger-correlation-history-less-hub) property (a stream opened
mid-run receives events from its subscription onward only).

{#event-stream-correlation-and-errors}
### Pairing and terminal frames

Service and trainable dispatches surface as **paired** events on the stream; a wire consumer
joins a pair by the dispatch composite `(pipeline_run_id, handler_position)` (the same `id:`
handle). Which events pair for each dispatch kind is owned by hash-model's
[§ Paired-event structure (service)](#paired-event-structure-service-kind) and
[§ (trainable composition)](#paired-event-structure-trainable-composition-kind); this reference
adds no pairing semantics — it carries the events on which the owned semantics operate.

A run terminates the stream with either a `pipeline_complete` frame (happy path) or a
`pipeline_error` frame. The `pipeline_error` payload carries the closed [`error_class`](#error-class)
and, for a [`PipelineFailure`](#pipelinefailure), its structural-locus `failure_category` —

**`failure_category`** — the **closed** enum naming the engine's structural locus for the failure
(where it occurred), set by the runner from **which internal scope raised** it, never inferred from
the exception name. Exactly one of:
- **`"service"`** — the failure escaped a service backend call: the `adapter.invoke` of a service
  handler's bound `services.<name>.invoke(...)`, or a [trainable](#trainable) composition node's
  engine-constructed `adapter.invoke`. Includes a service-binding timeout (the outbound call
  exceeding its transport timeout). `service_binding_name` is present.
- **`"handler"`** — the failure escaped **consumer-authored code**: an author handler body (a
  transform, a hook, or a service handler's own body code, including code around its `invoke`
  call). `service_binding_name` is absent.
- **`"engine"`** — the failure escaped the engine's own runner machinery: a run-guard (the consumer
  pipeline-level timeout) or an internal runner operation (binding delivery, channel routing, merge).
  Not attributable to a service backend or an author body. `service_binding_name` is absent.

— exactly as the [canonical event types](#canonical-event-types) table defines the payload. The
`pipeline_error` **frame** and the HTTP **error response** are distinct surfaces of the same
halt: the frame is the canonical event projected onto the stream; the response is the RFC 9457
projection ([R-error-channel-005](#R-error-channel-005)) on the blocking trigger.

---

{#token-stream}
## The token stream

```
GET /runs/{pipeline_run_id}/stream
```

Delivers a streamed run's raw token fragments as Server-Sent Events — the wire realization of
the engine's run-scoped token delivery for a pipeline whose terminal node is a
[`streamable`](#R-pipeline-001-streamable-terminal-node) trainable. The response
`Content-Type` is `text/event-stream`; the `{pipeline_run_id}` path segment is the run to
subscribe to, under the **same [correlation flow](#run-trigger-correlation) as the event
stream**: mint the id, open the stream, then trigger the run — the
[history-less hub](#run-trigger-correlation-history-less-hub) property applies to fragments
exactly as to events (a stream opened mid-run receives fragments from its subscription onward
only), and the pre-first-frame idle bound applies identically
([§ The construction surface](#construction-surface): a live fragment stream takes no idle
bound).

Fragments are **provisional transport, never a value**. The posture is owned by the pipeline
reference:

No engine surface exposes a partial,
incremental, or streamed channel value mid-invocation: a channel carries its
complete validated value when the runner writes it
([§ Kernel semantics](#kernel-semantics)), and the captured training record is
that same value
([channel–record correspondence](#channel-record-correspondence)) — never a
fragment. Token-level streaming delivery ships as the run-scoped
[`stream_sink`](#pipeline-invocation) — a provisional, consumer-facing transport
affordance (latency/UX) that does not expose a partial channel value: fragments
reach only the attached sink, never a channel or a captured record.

{#token-stream-frames}
### Token-stream frames

- `event: token` — one frame per raw fragment, in delivery order. `data:` is a JSON object
  with the single member `text`, carrying the fragment string verbatim.
- `event: end` — the terminal frame, published when the run **completes — returns or halts**.
  `data:` is the empty JSON object `{}`: a close signal, never a value carrier — the
  authoritative validated result rides the trigger response (a `2xx`
  [`RunResult`](#pipeline-result-runresult-shape) / a non-`2xx` RFC 9457 body), and the
  canonical record rides the [event stream](#event-stream)'s terminal frame.

Token frames carry no `id:` — provisional transport has no resume or replay semantics (the
per-dispatch `id:` handle belongs to the [event stream](#event-stream-frames), where frame
de-duplication protects record fidelity; the token stream is not a record surface).

Two scoping facts:

- **Which runs produce token frames.** Tokens flow iff the run's pipeline can stream — its
  terminal node (transitively, through a terminal nested `pipeline` embed) is a trainable
  declaring [`streamable = true`](#R-pipeline-001-streamable-terminal-node); a served pipeline
  that declares it has already passed the compose-time capability gate, so streaming never
  silently degrades. A run with **no** streamable terminal produces no token frames; its
  stream carries only the terminal `end` frame at run completion, so a mistakenly opened
  stream closes promptly rather than idling.
- **The token stream is not the event stream.** Token fragments ride their own endpoint, fed
  by their own delivery path — never the canonical event channel. The closed event enum
  ([§ Event-log specification](#event-log-specification)) is the training-log substrate, where
  every payload is a complete validated snapshot; a fragment fits neither its enum nor its
  posture ([R-server-003](#server-derived-rules)).

---

{#construction-surface}
## The construction surface — `create_app`

The in-process construction half of the wire surface (the
[two-sided consumer boundary](#consumer-boundary-two-sided)'s serving seam): an
integrator builds the ASGI application over **already-assembled runnables** —

```
conjured.server.create_app(
    pipelines: Mapping[str, Runnable],
    *,
    stream_timeout_s: float | None = None,
) -> Starlette
```

- **`pipelines`** — the served set: qualified pipeline name → the frozen `Runnable`
  the in-process compose API produces (hand-built
  registry → `compile_pipeline` → `assemble`, the one deployment folded in at
  assemble — the [two-sided boundary](#consumer-boundary-two-sided)'s composing side,
  owned at the pipeline component's reference). The engine has no disk/directory pipeline loader; producing the
  runnables is the integration layer's. A mapping value that is not a `Runnable`
  fails loud at construction (engine-surface misuse — a plain `TypeError`, the same
  posture as the runner's non-mapping `inputs`).
- **`stream_timeout_s`** — the **pre-first-frame idle bound** for both SSE
  endpoints: it bounds ONLY a stream that has not yet received its first frame (the
  stream opened for a run that is never triggered — the correlation flow invites
  opening the stream first, so an abandoned open must not hold a connection
  forever). Once any frame has been delivered the stream is **live** and the bound
  no longer applies — a live stream stays open to its terminal frame regardless of
  inter-frame gaps (a slow service call between events is a normal run, and closing
  mid-run would silently truncate the projection R-server-002 forbids). `None`
  (default) applies no bound. Launch-surface flag: `--stream-timeout`
  (§ Inbound-binding configuration); the bundled client's `stream_timeout_s`
  constructor kwarg threads the same value.

{#python-client}
## The bundled Python client — `conjured.client`

The first-party Python consumer of this wire protocol (the C4 component the
[components view](#architecture-components) names): `conjured.client.Client` launches
and owns a **loopback-bound server subprocess** (`python -m conjured.server`, port
OS-assigned and read back race-free) and exposes the blocking call —

```
Client(app: str, *, env: Mapping[str, str] | None = None,
       startup_timeout_s: float = 10.0, stream_timeout_s: float | None = None)
Client.run(pipeline: str, inputs: Mapping[str, object] | None = None, *,
           pipeline_run_id: str | None = None, timeout_ms: int | None = None)
    -> RunResult
```

- **`app`** — the `module:attr` import string resolving to the served-pipelines
  mapping (or a zero-arg factory returning one), passed through to the launch
  surface — the same convention § Inbound-binding names.
- **Lifecycle** — a context manager (`__enter__` starts, `__exit__` terminates the
  subprocess) or explicit `start()` / `stop()`. Startup fails loud: a subprocess
  that exits before binding raises a diagnostic `RuntimeError`; a bind or accept
  not reached within `startup_timeout_s` raises `TimeoutError`; a failed start
  tears the subprocess down.
- **The run contract mirrors the in-process runner's**: a returned `RunResult` IS
  success; a halt raises **`ServerError`**, carrying the HTTP `status` and the
  parsed RFC 9457 `problem` body — the wire form IS the contract. The client
  **never reconstructs the engine's in-process exception classes** across the
  process boundary: there is no separate Python API contract to maintain because
  the client speaks only this wire protocol; the "no separate contract" property is
  a property of the wire side — composing still crosses the in-process compose API
  like any embedded consumer.

{#inbound-binding}
## Inbound-binding configuration

The server's **inbound binding** — the bind address (localhost vs a network interface), the
port, and TLS termination — is the consumer→engine endpoint's own configuration. It is
**deployment-class** config: environment-dependent, varying from a developer's localhost to a
networked sidecar without changing what any pipeline *is*, and therefore the **same class as
the deployment model's outbound [`transport.<name>`](#transport)**. Like every transport value,
it is **excluded from both hashes** ([§ What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs)):
moving the server from localhost to a network address, changing its port, or terminating TLS
shifts neither the [pipeline-hash](#pipeline-hash) nor any training-bundle-hash.

The two integration modes the binding selects between match the engine's two consumer paths:

- **Bundled localhost subprocess** — the binding the first-party [Python client](#architecture-components)
  wraps: the server bound to localhost, spoken to over the loopback interface, with no network
  exposure. The Python default.
- **Networked sidecar** — the binding a non-Python consumer reaches over the wire: the server
  bound to a network interface (with TLS and a port), driven directly via HTTP+SSE in any
  language.

The per-deployment HTTP **status-code override** the [wire error surface](#trigger-error-responses)
defers is part of this same inbound configuration — a deployment that fronts the engine behind a
gateway expecting a different status ladder remaps here, in transport config, never by changing
what error class the engine raises.

**Where this config lives.** The inbound binding is a **server-startup / integration concern** —
supplied to the server process when it is launched — **not** a section of the closed
deployment-declaration grammar ([R-deployment-001](#R-deployment-001)). This mirrors the
deployment reference's stance that *how the engine receives its deployment declaration at
startup is an integration concern, not part of the declaration grammar*: both the inbound
binding and the deployment-receipt are how the process is launched and exposed, distinct from
what a pipeline is and what backends it wires to. The deployment declaration governs a pipeline's
outbound wiring; the socket the server itself listens on is launch configuration, so the closed
deployment-section set stays intact.

**The launch surface — `python -m conjured.server`.** The engine ships one launcher for this
binding; both integration modes above run it. Its flag set is closed:

| Flag | Default | Meaning |
|---|---|---|
| `--app` | required | Import string `module:attr` resolving to the served-pipelines mapping [`create_app`](#construction-surface) receives — or a zero-arg factory the launcher calls to produce one. |
| `--host` | `127.0.0.1` | Bind address — loopback is the bundled-subprocess mode, a network interface the sidecar mode (the two modes above). |
| `--port` | `0` | Bind port; `0` requests an OS-assigned ephemeral port. The launcher binds the socket itself before serving starts, so the reported port is exactly the served one. |
| `--port-file` | none | A path the launcher writes the bound port to (atomically) once the socket is bound — with `--port 0`, the race-free handshake a wrapping process reads the actual port from. The [bundled Python client](#python-client)'s subprocess port discovery is exactly this read. |
| `--stream-timeout` | none | Seconds; threads to `create_app`'s `stream_timeout_s` — the pre-first-frame idle bound [§ The construction surface](#construction-surface) owns. |

---

{#server-worked-example}
## Worked example

Running a small dialogue pipeline, with a live progress stream. The consumer mints the run id,
opens the stream, then triggers the run.

```http
POST /runs
Content-Type: application/json

{
  "pipeline": "mypkg.dialogue_npc",
  "inputs": { "player_input": "Where's the bridgekeeper?", "session_id": "s-42" },
  "pipeline_run_id": "run_20260506T142311Z_a3f9",
  "timeout_ms": 30000
}
```

```http
200 OK
Content-Type: application/json

{
  "run_id": "run_20260506T142311Z_a3f9",
  "state": { "dialogue": "He keeps the old stone span past the mill.", "emotion": "warm" }
}
```

The concurrent stream (`GET /runs/run_20260506T142311Z_a3f9/events`) carries the
run-scoped frames in position order, terminating on `pipeline_complete`:

```text
event: pipeline_start
data: {"pipeline_run_id": "run_20260506T142311Z_a3f9", "pipeline_hash": "sha256:…", …}

event: handler_enter
id: run_20260506T142311Z_a3f9:0
data: {"handler_qualified_name": "mypkg.assemble_prompt", "handler_position": 0, …}

event: pipeline_complete
data: {"pipeline_run_id": "run_20260506T142311Z_a3f9", "outputs_snapshot": {…}, …}
```

Had the run halted, the trigger response would instead be an
`application/problem+json` body ([R-error-channel-005](#R-error-channel-005)) and the stream's
terminal frame a `pipeline_error`.

Had the pipeline's terminal trainable declared
[`streamable = true`](#R-pipeline-001-streamable-terminal-node), the same flow with a third
connection (`GET /runs/run_20260506T142311Z_a3f9/stream`) would deliver the emission's
raw text as it arrives — the constrained-JSON emission streaming fragment by fragment, a
latency consumer scooping leading fields (the dialogue) off the accumulating text before the
run completes — closing on `end` ([§ The token stream](#token-stream)):

```text
event: token
data: {"text": "{\"dialogue\": \"He keeps the old"}

event: token
data: {"text": " stone span past the mill.\", \"emo"}

event: token
data: {"text": "tion\": \"warm\"}"}

event: end
data: {}
```

The trigger response and the event stream above are **unchanged** by streaming — the
authoritative result and the canonical record are the same with or without the third
connection.

---

{#server-derived-rules}
## Derived rules

Every derived rule that governs this component lives here. The rules cite the invariant(s) or
tenet(s) they protect from [principles](#invariants-and-derived-rules) via
`derived_from`; they declare an `enforcement` mode per
[enforcement-modes](#architecture-enforcement-modes).

```yaml
rules:
  - rule_id: R-server-001
    name: run-trigger is a faithful invocation projection
    derived_from: [I1, I3]
    enforcement: mechanical
    statement: |
      The run trigger is the wire projection of one engine invocation: one
      request runs one named pipeline with its declared inputs and yields
      either one RunResult or one structured error, never both and never a
      partial result. The HTTP status class is the wire discriminator between
      the output channel and the error channel — a 2xx body is the run's
      RunResult (the shape owned by the pipeline reference's § Pipeline
      result), a non-2xx body is the RFC 9457 error projection owned by
      R-error-channel-005 — so no success/ok/status envelope field appears on
      the response, mirroring the in-process channel separation
      (R-error-channel-004). The request seeds only the pipeline's declared
      input channels under the API-boundary rule (R-pipeline-001): a missing
      declared input is a ContractViolation surfaced as a 4xx before any node
      dispatches; an undeclared key is dropped, not an error. Load-bearing for
      I1/I3: the wire surface declares exactly what it accepts and returns
      (no implicit request field becomes a channel, no implicit response field
      joins the result), and the engine/consumer boundary stays one-way — the
      transport is a projection the server enforces, never a place a consumer
      shape leaks into the engine.

  - rule_id: R-server-002
    name: event stream is a complete, faithful canonical-event projection
    derived_from: [I1, I4]
    enforcement: mechanical
    statement: |
      The event-stream endpoint projects a run's conjured.events.runner stream
      onto Server-Sent Events without adding, dropping, reordering, or
      reshaping events. Each frame carries one canonical event: the event:
      name and the data: payload are the type and the canonical in-process
      payload owned by hash-model's § Event-log specification (the closed
      event enum and per-event shapes are not re-declared at the wire — adding
      or changing an event is a contract amendment there, not a wire
      extension). The stream is filtered to one pipeline_run_id and carries the
      run-scoped events in event order (the run's pipeline_start first, each
      dispatch's events by handler_position, the terminal frame last); the
      compose-time events, which carry no pipeline_run_id, are out of a
      run-scoped stream's scope. A
      projection failure fails loud rather than silently skipping a frame.
      Load-bearing for I1/I4: the event log is the substrate the training
      projection is reconstructed from, so a wire projection that dropped or
      reshaped a frame would corrupt the derived corpus's reconstructability —
      completeness and fidelity are the contract, not best-effort delivery.

  - rule_id: R-server-003
    name: token stream is provisional transport, never a record surface
    derived_from: [I1, I4]
    enforcement: mechanical
    statement: |
      The token-stream endpoint delivers a streamed run's raw fragments as
      provisional transport only: one token frame per fragment in delivery
      order, closed by the terminal end frame when the run completes —
      returns or halts. No token frame is a channel value, a training record,
      or any part of the authoritative result; the validated RunResult rides
      the trigger response and the canonical record rides the event stream's
      terminal frame, exactly as for an unstreamed run (the pipeline
      reference's no-mid-invocation-partial-values seal, transcluded at § The
      token stream, is the owning contract). Token fragments ride their own
      endpoint, fed by their own delivery path — never the canonical event
      channel: the closed event enum is the training-log substrate whose
      every payload is a complete validated snapshot, and a fragment is
      neither. Which runs produce token frames is fixed at compose time (the
      streamable terminal-node placement clause of R-pipeline-001); a run
      with no streamable terminal produces no token frames, and its stream
      closes with the terminal end frame at run completion. Load-bearing for
      I1/I4: the wire declares exactly what it delivers — a fragment is
      labeled provisional by its own endpoint and frame vocabulary, never
      mistakable for the result — and the training projection's substrate
      stays exactly the closed canonical event log, so streaming delivery
      cannot dilute or corrupt it.
```
