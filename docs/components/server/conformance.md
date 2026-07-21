---
kind: conformance
audience: [authors, integrators, agents]
slug: server-conformance
component: server
---

{#server-conformance}
# Server conformance checks

The conformance checks the engine fires for the [server](#server) component — the
checks that hold the server's wire-projection rules, the [derived rules](#server-derived-rules)
the server reference owns (the complete roster; this catalog cites it rather than re-counting or
re-listing the rules). Unlike the handler and pipeline checks — which catch faults in *consumer*
declarations and bodies — the server checks verify the **engine's own HTTP+SSE boundary**: a
failure means the *server* mis-projected an invocation, an event stream, or a token stream, not
that a consumer violated a contract. The one exception is the API-boundary input check, which
surfaces a consumer [ContractViolation](#contractviolation) the server projects to the wire.

Each entry below is structured for diagnosing a wire-level fault or auditing a server build against
the engine's contract. The format:

- **Check name** — the conformance check; lowercase noun phrase.
- **Rule anchor** — the derived rule the check enforces, cited by file + prose anchor.
- **Trigger** — when the check fires (per request, per SSE frame, per stream).
- **Mechanism** — what the server does, and what a violation looks like.
- **Violation example** — a concrete non-conformant wire response or frame.
- **Error class** — the [closed-enum class](#error-class) the engine raises, or the
  server-conformance level for a wire-projection fault the runner's error machinery never sees.
- **Diagnosis** — what to look for and how to fix.

The wire statuses these checks reference are settled by the server reference's
[§ Wire error surface](#trigger-error-responses); the RFC 9457 projection they ride is owned by the
[error-channel reference](#R-error-channel-005); the canonical event model the SSE checks project is
owned by hash-model's [§ Event-log specification](#event-log-specification). This catalog cites those
owners rather than restating them.

---

{#server-mechanically-enforced-checks}
## Mechanically-enforced checks

{#synchronous-single-outcome-response}
### Synchronous single-outcome trigger response

- **Rule anchor.** [Derived rule R-server-001 (run-trigger is a faithful invocation projection)](#server-derived-rules).
- **Trigger.** HTTP trigger-response emission — the server returns from `POST /runs`.
- **Mechanism.** The trigger blocks for the run and returns exactly one outcome: a `2xx` body
  carrying the run's [`RunResult`](#pipeline-result-runresult/shape) on success, or a non-`2xx`
  [`application/problem+json`](#rfc-9457-http-wire-projection) body on halt — never both, never a
  partial result, and never an asynchronous `202 + run_id` acknowledgement the consumer must poll
  or stream to resolve. A response that acknowledges before the run completes, or that returns a
  partial channel set as if the run had finished, breaks the one-invocation→one-outcome projection.
- **Violation example.**

  ```http
  202 Accepted
  Content-Type: application/json

  {"run_id": "run_20260506T142311Z_a3f9", "status": "running"}
  ```

  An async acknowledgement re-introduces at the wire the terminal-state polling the blocking trigger
  exists to foreclose ([§ Response — synchronous](#run-trigger-response)).
- **Error class.** Not a pipeline error class — a server-conformance property. Detection belongs in
  the engine's server-layer integration tests (a real trigger returns a terminal `RunResult` or a
  terminal error on the call), not in the runner's error machinery.
- **Diagnosis.** Confirm the trigger handler runs the pipeline to completion and returns the
  `RunResult` (or projects the halt) on the same request — the no-run-registry and
  gateway-decoupling rationale is owned at [§ Why synchronous](#run-trigger-response); the engine's
  trigger stays blocking.

{#status-class-is-the-wire-discriminator}
### Status class is the wire discriminator — no `success`/`ok`/`status` envelope field

- **Rule anchor.** [Derived rule R-server-001 (run-trigger is a faithful invocation projection)](#server-derived-rules); [derived rule R-error-channel-004 (channel separation)](#error-channel-derived-rules).
- **Trigger.** HTTP trigger-response emission.
- **Mechanism.** The HTTP status class is the sole discriminator between the output channel and the
  error channel: a `2xx` body is the run's `RunResult`, a
  non-`2xx` body is the RFC 9457 error projection. The success body carries **no**
  `success` / `ok` / `status` envelope field — the returned-value-IS-the-success-signal rule is
  [R-error-channel-004](#R-error-channel-004)'s. A discriminated-union envelope field on the success
  body would let consumer code bypass the error channel the status class already carries.
- **Violation example.**

  ```http
  200 OK
  Content-Type: application/json

  {"success": true, "state": {"dialogue": "…"}, "run_id": "run_…"}
  ```

  The `success` field duplicates the `2xx` status class as a body field — the in-process status
  envelope the engine forecloses, re-introduced at the wire.
- **Error class.** Not a pipeline error class — a server-conformance property; the engine's
  server-layer integration tests assert the success body is exactly the `RunResult` shape.
- **Diagnosis.** Remove any `success` / `ok` / `status` field from the `2xx` body; the body is the
  serialized `RunResult` and nothing more. Consumers dispatch on the status class (`2xx` vs
  non-`2xx`), then parse the `RunResult` or the Problem Details envelope accordingly.

{#api-boundary-declared-input-enforcement}
### API-boundary declared-input enforcement

- **Rule anchor.** [Derived rule R-server-001 (run-trigger is a faithful invocation projection)](#server-derived-rules); the API-boundary routing is owned by [R-error-channel-001 § key-set routing](#R-error-channel-001/key-set-routing) and [R-pipeline-001](#R-pipeline-001).
- **Trigger.** Per request, at the API boundary — before any node dispatches and before any run
  starts.
- **Mechanism.** The request body seeds **only** the pipeline's declared input channels. The
  admission rule is owned by the pipeline reference:

  :::{transclude} R-pipeline-001/api-inputs-enforcement
  :::

  The server-specific carry: the missing-field ContractViolation projects to
  **`400 Bad Request`** ([§ Wire error surface](#trigger-error-responses)), and the boundary is
  symmetric on the way out — the server never lets a response field
  the run did not produce join the result. The engine/consumer boundary stays one-way.
- **Violation example.**

  ```http
  POST /runs
  Content-Type: application/json

  {"pipeline": "mypkg.dialogue_npc", "inputs": {"player_input": "hi", "sesion_id": "s-1"}}
  ```

  ```http
  400 Bad Request
  Content-Type: application/problem+json

  {"type": "about:blank", "title": "Contract violation", "status": 400,
   "detail": "expected: declared input 'session_id'; actual: 'session_id' absent", …}
  ```

  The pipeline declares `session_id`; the request misspells it `sesion_id`. The declared input is
  absent → `400` before any dispatch; the extra `sesion_id` is never seeded.
- **Error class.** [ContractViolation](#contractviolation), surfaced as `400 Bad Request` — the
  `ContractViolation` status the RFC 9457 projection [leaves caller-supplied](#contractviolation-rfc-9457).
- **Diagnosis.** Include every field the pipeline's `[inputs]` block declares in the request's
  `inputs` object. A `400` whose `detail` names a missing declared input is usually a typo in the
  request key (the violation message also NAMES any unrecognized keys present, so the typo'd
  extra is visible alongside the absent declared field). A runtime `ContractViolation` raised mid-dispatch (not this API-boundary case) is
  a distinct wire status — see the [§ Wire error surface](#trigger-error-responses) table.

{#complete-ordered-run-scoped-event-projection}
### Complete, ordered, run-scoped event projection

- **Rule anchor.** [Derived rule R-server-002 (event stream is a complete, faithful canonical-event projection)](#server-derived-rules).
- **Trigger.** Per stream — the lifetime of one `GET /runs/{pipeline_run_id}/events` connection.
- **Mechanism.** The stream is filtered to a single `pipeline_run_id`; the
  completeness / order / fail-loud contract is the reference's owned prose:

  :::{transclude} event-stream-frames/run-scoped-completeness
  :::
- **Violation example.**

  ```text
  event: pipeline_start
  data: {"pipeline_run_id": "run_…", …}

  event: pipeline_complete
  data: {"pipeline_run_id": "run_…", …}
  ```

  A run that dispatched a handler emits `handler_enter` / `handler_exit` between `pipeline_start` and
  `pipeline_complete`; a stream that jumps straight to the terminal frame dropped them — a silent
  hole the projection forbids.
- **Error class.** Not a pipeline error class — a server-conformance property; the engine's
  server-layer integration tests assert a run's stream carries its complete run-scoped event set in
  order, and that a projection failure raises rather than truncating the stream.
- **Diagnosis.** Confirm the SSE endpoint subscribes to the run's events *before* returning the
  streaming response (so a stream opened before the run is triggered receives the run's events) and
  buffers any event arriving before the generator awaits — nothing dropped. Confirm the stream
  filters to the path's `pipeline_run_id` and yields frames until the terminal event. A stream that
  silently omits a frame on back-pressure or a serialization error is non-conformant; the projection
  must fail loud.

{#faithful-frame-mapping}
### Faithful frame mapping — canonical event name and payload, not reshaped at the wire

- **Rule anchor.** [Derived rule R-server-002 (event stream is a complete, faithful canonical-event projection)](#server-derived-rules).
- **Trigger.** Per SSE frame.
- **Mechanism.** Each canonical event projects to exactly one frame, carrying the event verbatim per
  the server reference's [§ Event-to-frame mapping](#event-stream-frames): `event:` is the canonical
  type name from the [canonical event types](#canonical-event-types) table; `data:` is that event's
  **canonical in-process payload** as a JSON object, serialized per the owner's
  [null-inclusion rule](#event-stream-frames/data-null-serialization); `id:` is the
  dispatch composite for a per-dispatch frame (a run-level frame omits it), rendered as the server
  reference's § Event-to-frame mapping specifies. The closed event enum and the per-event payload
  shapes are **not re-declared at the wire** — adding or changing an event is a contract amendment at
  hash-model's [§ Event-log specification](#event-log-specification), never a wire extension. A frame
  that renames an event, reshapes a payload, drops a payload field, or substitutes the RFC 9457
  error envelope for a canonical event payload corrupts the projection.
- **Violation example.**

  ```text
  event: handler_complete
  data: {"type": "about:blank", "title": "…", "status": 502}
  ```

  Two faults: `handler_complete` is not a [canonical event type](#canonical-event-types) (the
  terminal-per-dispatch event is `handler_exit`), and the payload is the RFC 9457 error envelope, not
  the canonical `handler_exit` payload — the event-stream carries canonical event payloads, never the
  Problem Details envelope.
- **Error class.** Not a pipeline error class — a server-conformance property; the engine's
  server-layer integration tests assert each frame's `event:` name and `data:` payload match the
  canonical event the runner emitted, field for field.
- **Diagnosis.** Map each frame straight from the canonical event: the `event:` name verbatim from
  the [canonical event types](#canonical-event-types) table, the `data:` payload the canonical
  in-process object (do not project it through the RFC 9457 error helper — that is the HTTP error
  body's surface only). To add or change an event, amend the closed event model at hash-model's
  [§ Event-log specification](#event-log-specification); the wire projection follows, never leads.

{#token-stream-frame-vocabulary}
### Token-stream frame vocabulary — `token` fragments and the terminal `end` frame

- **Rule anchor.** [Derived rule R-server-003 (token stream is provisional transport, never a record surface)](#server-derived-rules).
- **Trigger.** Per token-stream frame, over the lifetime of one `GET /runs/{pipeline_run_id}/stream` connection.
- **Mechanism.** The token stream carries exactly the two frame types the server reference's
  [§ Token-stream frames](#token-stream-frames) fixes: `event: token` — one frame per raw fragment
  in delivery order, whose `data:` is a JSON object with the single member `text` carrying the
  fragment string verbatim; and `event: end` — the terminal frame, published when the run
  **completes (returns or halts)**, whose `data:` is the empty JSON object `{}`, a close signal
  that carries no value. No token frame carries an `id:` (provisional transport has no
  resume/replay semantics). A frame that renames the event, wraps the fragment in extra members,
  reshapes `text`, carries the run's `RunResult` on the `end` frame, or omits the `end` frame at
  run completion breaks the vocabulary.
- **Violation example.**

  ```text
  event: token
  data: {"text": "He keeps the old", "index": 3, "final": false}

  event: end
  data: {"state": {"dialogue": "He keeps the old stone span past the mill."}}
  ```

  Two faults: the `token` frame adds `index` / `final` members the fragment shape forbids (a
  fragment is `{text}` and nothing more), and the `end` frame carries the run's result — the
  authoritative validated result rides the trigger response, never the close signal.
- **Error class.** Not a pipeline error class — a server-conformance property; the engine's
  server-layer integration tests assert every `token` frame is `{text: <fragment>}` verbatim in
  delivery order and that a completed run (returned OR halted) closes the stream with exactly one
  `end` frame carrying `{}`.
- **Diagnosis.** Emit one `token` frame per raw fragment with `data:` exactly `{"text": "<fragment>"}`;
  publish exactly one `event: end` with `data: {}` when the run returns or halts. Do not put the
  result on the `end` frame or add fields to a `token` frame — the [trigger response](#run-trigger-response)
  and the [event stream](#event-stream)'s terminal frame carry the authoritative result and record
  respectively.

{#token-fragments-never-on-event-channel}
### Token fragments never cross onto the event channel

- **Rule anchor.** [Derived rule R-server-003 (token stream is provisional transport, never a record surface)](#server-derived-rules).
- **Trigger.** Per frame on either stream — the token stream (`/stream`) and the event stream (`/events`).
- **Mechanism.** Token fragments ride their own endpoint, fed by their own delivery path — **never**
  the canonical event channel. The closed event enum ([§ Event-log specification](#event-log-specification))
  is the training-log substrate whose every payload is a complete validated snapshot; a fragment fits
  neither its enum nor its posture. A `token` frame appearing on the `/events` stream, or a raw
  fragment injected as (or into) a canonical event payload, crosses the surfaces the rule holds apart.
- **Violation example.**

  ```text
  # on GET /runs/{id}/events — the event stream
  event: token
  data: {"text": "He keeps the old"}
  ```

  A `token` frame on the event stream: the fragment has leaked onto the record substrate the training
  projection is reconstructed from — corrupting the derived corpus, which the enum admits only
  complete validated snapshots into.
- **Error class.** Not a pipeline error class — a server-conformance property; the engine's
  server-layer integration tests assert the `/events` stream carries only [canonical event
  types](#canonical-event-types) and the `/stream` endpoint carries only `token` / `end` frames,
  with no fragment ever appearing as or within a canonical event payload.
- **Diagnosis.** Keep the two delivery paths disjoint: fragments flow only through the token-stream
  endpoint; the event stream carries only canonical events. If a fragment reaches the event channel,
  the token-delivery path is wired into the event emitter — separate them.

{#token-frames-gated-on-streamable-terminal}
### Token frames flow only for a streamable-terminal run

- **Rule anchor.** [Derived rule R-server-003 (token stream is provisional transport, never a record surface)](#server-derived-rules); the placement gate is [R-pipeline-001's streamable terminal-node clause](#R-pipeline-001/streamable-terminal-node).
- **Trigger.** Per stream — the lifetime of one `/stream` connection, decided by the served pipeline's compose-time shape.
- **Mechanism.** Which runs produce token frames is **fixed at compose time**: tokens flow iff the
  run's pipeline can stream — its terminal node (transitively, through a terminal nested `pipeline`
  embed) is a trainable declaring [`streamable = true`](#R-pipeline-001/streamable-terminal-node). A
  served pipeline that declares it has already passed the compose-time capability gate, so streaming
  never silently degrades. A run with **no** streamable terminal produces **no** token frames; its
  `/stream` carries only the terminal `end` frame at run completion, so a mistakenly opened stream
  closes promptly rather than idling. Emitting `token` frames for a non-streamable run, or producing
  none for a streamable run, breaks the gate.
- **Violation example.**

  ```text
  # run whose pipeline has NO streamable terminal node, yet /stream emits:
  event: token
  data: {"text": "He keeps the old"}
  ```

  Token frames from a non-streamable run: the server is emitting provisional transport the pipeline's
  compose-time shape says cannot exist — the gate that ties fragment production to the declared
  streamable terminal has been bypassed.
- **Error class.** Not a pipeline error class — a server-conformance property; the engine's
  server-layer integration tests assert a streamable-terminal run's `/stream` carries `token` frames
  then `end`, and a non-streamable run's `/stream` carries only `end` at completion.
- **Diagnosis.** Derive token production from the served pipeline's compose-time streamable-terminal
  status, not from a runtime flag: a run streams iff its terminal node (transitively) is a
  `streamable = true` trainable. A non-streamable run's `/stream` must close on `end` with no `token`
  frame; a streamable run must deliver its fragments.

---

{#server-review-enforced-checks}
## Review-enforced checks

The server component owns no review-enforced rules. Every server rule (the server reference's
[derived rules](#server-derived-rules)) declares
[`enforcement: mechanical`](#mechanically-enforced-mode): the wire surface is engine-implemented, so
every server-conformance property is exercised by the engine's own server-layer integration tests —
there is no consumer-authored body the runner cannot inspect, hence no review-enforced gap to close.
The review-enforced rules that govern the handler bodies a served pipeline composes are owned and
enumerated by the **handler conformance checks** (`components/handler/conformance.md`) and the
**error-channel conformance checks** (`components/error-channel/conformance.md`); they apply
unchanged to a pipeline reached over the wire.

---

{#server-cross-references}
## Cross-references

- [server reference](#server-reference) — R-server-001 (faithful invocation projection) and
  R-server-002 (complete, faithful event projection); the wire error surface (the status selection
  the checks above reference) and the event-to-frame mapping.
- [error-channel reference](#error-channel-reference) — R-error-channel-004 (channel separation);
  R-error-channel-005 (the RFC 9457 HTTP wire projection); R-error-channel-001 (the closed error-class
  enum and the API-boundary key-set routing).
- [pipeline reference](#pipeline-reference) — R-pipeline-001 (the API-boundary declared-inputs
  enforcement the trigger projects).
- [hash-model](#architecture-hash-model) — the Event-log specification and canonical event types (the
  closed event model the SSE projection carries).
- [principles](#principles) — invariants I1, I3, I4 cited above.
- [glossary](#glossary) — the engine vocabulary cited throughout (server, RunResult, canonical event,
  ContractViolation).
- **error-index** (`reference/error-index.md`) — the codegen-built error → rule map.
