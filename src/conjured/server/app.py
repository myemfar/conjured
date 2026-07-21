"""The Starlette application — the engine's HTTP+SSE wire surface.

Realizes the Server-component reference (``conjured/docs/components/server/reference.md``)
over the reference stack (Starlette + uvicorn + sse-starlette). The server is a
**downstream consumer** of the runner and the canonical event stream; it adds no engine
vocabulary — it projects one engine invocation onto HTTP and one run's canonical event
stream onto SSE, and nothing more.

Three endpoints (reference § The run trigger / § The event stream / § The token stream):

- ``POST /runs`` — the **synchronous** run trigger (§ The run trigger). Resolves the named
  pipeline among the served runnables, runs it to completion **in a threadpool**
  (``run_in_threadpool`` — ``conjured.runner.run`` is synchronous; the threadpool keeps the
  event loop free to stream SSE concurrently), and returns the ``RunResult`` as JSON on the
  happy path or the RFC 9457 error body (non-2xx) on a halt. No ``success`` / ``ok`` /
  ``status`` envelope field — the HTTP status class is the wire discriminator between the
  output channel and the error channel (R-server-001, R-error-channel-004). For a served
  runnable that can stream (a ``streamable`` terminal — ``stream_route_position``), the
  trigger attaches a ``stream_sink`` publishing each fragment to the token hub.
- ``GET /runs/{pipeline_run_id}/events`` — the SSE projection of that run's
  ``conjured.events.runner`` stream (§ The event stream). Filtered to the one
  ``pipeline_run_id``; each run-scoped canonical event becomes one SSE frame; the stream
  terminates on ``pipeline_complete`` / ``pipeline_error`` (R-server-002).
- ``GET /runs/{pipeline_run_id}/stream`` — the run's **provisional token stream**
  (§ The token stream): one ``token`` frame per raw fragment the run's streamable terminal
  delivers, closed by the terminal ``end`` frame when the run completes. Fed by a second,
  non-canonical hub — token deltas NEVER ride the canonical event enum (R-server-003); the
  authoritative value stays the trigger response / ``pipeline_complete``.

**Which pipelines the server serves** is a server-construction input — a mapping
``{qualified_name: Runnable}`` of already-assembled runnables. The engine has no
disk/directory pipeline loader; producing the runnables (hand-built registry →
``compile_pipeline`` → ``assemble``, with the one deployment folded in at assemble time) is
the integration layer's concern (deployment/reference.md § One deployment per engine: the
deployment is "supplied at startup" and "how the engine receives it is an integration
concern"). The **inbound binding** (host / port / TLS) is server-startup config supplied at
launch (``conjured.server.__main__``; reference § Inbound-binding configuration), not a
deployment-grammar section.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Mapping

from sse_starlette import EventSourceResponse
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from conjured.errors import (
    Check,
    ContractViolation,
    PipelineFailure,
    SchemaValidationError,
)
from conjured.events import (
    HandlerEnter,
    HandlerExit,
    PipelineComplete,
    PipelineError,
    ServiceInvocation,
)
from conjured.runner.assemble import Runnable
from conjured.runner.dispatch import new_pipeline_run_id
from conjured.runner.run import run, stream_route_position
from conjured.server.hub import EventHub
from conjured.server.problem_details import to_problem_details

PROBLEM_JSON = "application/problem+json"

#: The terminal marker the run trigger publishes onto the token hub when a run completes
#: (returns OR raises) — the token stream yields its ``end`` frame on it and closes. A
#: module-level sentinel (identity-compared), so no fragment string can collide with it.
_TOKEN_STREAM_END = object()


def create_app(
    pipelines: Mapping[str, Runnable], *, stream_timeout_s: float | None = None
) -> Starlette:
    """Build the Starlette app serving ``pipelines`` (qualified name → assembled
    :class:`~conjured.runner.assemble.Runnable`).

    ``stream_timeout_s`` bounds an idle SSE stream — either endpoint's — that receives
    nothing (e.g. one opened for a run that is never triggered): ``None`` (default) keeps
    the stream open until its terminal frame or the client disconnects — sse-starlette
    cancels the generator on disconnect, which unsubscribes it — matching the reference's
    "stays open for the run."
    """
    served: dict[str, Runnable] = dict(pipelines)
    for name, runnable in served.items():
        if not isinstance(runnable, Runnable):
            raise TypeError(
                f"create_app: served pipeline {name!r} is "
                f"{type(runnable).__name__}, not a Runnable — the server is constructed "
                "with already-assembled runnables (compile_pipeline -> assemble)"
            )
    hub = EventHub()
    # The token hub: a SECOND EventHub instance, NEVER attached to logging — its
    # payload-agnostic subscribe/publish registry carries a streamed run's provisional
    # token fragments, keeping them entirely off the canonical event channel
    # (R-server-003; the closed enum is the training-log substrate).
    token_hub = EventHub()

    async def runs_endpoint(request: Request) -> Response:
        return await _run_trigger(request, served, token_hub)

    async def events_endpoint(request: Request) -> Response:
        return _event_stream(request, hub, stream_timeout_s)

    async def tokens_endpoint(request: Request) -> Response:
        return _token_stream(request, token_hub, stream_timeout_s)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        # One persistent consumer handler for the app's lifetime (hub.py explains why a
        # per-request add/remove would race logging's global handler list).
        hub.attach()
        try:
            yield
        finally:
            hub.detach()

    app = Starlette(
        routes=[
            Route("/runs", runs_endpoint, methods=["POST"]),
            Route(
                "/runs/{pipeline_run_id}/events",
                events_endpoint,
                methods=["GET"],
            ),
            Route(
                "/runs/{pipeline_run_id}/stream",
                tokens_endpoint,
                methods=["GET"],
            ),
        ],
        exception_handlers={
            StarletteHTTPException: _http_exception_handler,
            # R-error-channel-005 is categorical — EVERY HTTP error response carries
            # application/problem+json, the defect-path 500 included (without this
            # handler Starlette's ServerErrorMiddleware serves text/plain).
            Exception: _unhandled_exception_handler,
        },
        lifespan=lifespan,
    )
    return app


# ---------------------------------------------------------------------------
# POST /runs — the synchronous run trigger
# ---------------------------------------------------------------------------


async def _run_trigger(
    request: Request, served: Mapping[str, Runnable], token_hub: EventHub
) -> Response:
    # --- Wire-level validation: rejections that arise BEFORE any engine error class ---
    # (reference § Wire error surface: a transport-level rejection; no pipeline is invoked,
    # no engine error class is raised, no event fires.)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — any JSON decode failure is a malformed body
        return _wire_problem(400, "Malformed request body", "the request body is not valid JSON")
    if not isinstance(body, dict):
        return _wire_problem(
            400, "Malformed request body", "the request body must be a JSON object"
        )
    pipeline_name = body.get("pipeline")
    if pipeline_name is None:
        return _wire_problem(
            400, "Missing required field", "the request body omits the required 'pipeline' field"
        )
    if not isinstance(pipeline_name, str):
        return _wire_problem(
            400, "Malformed 'pipeline'", "the 'pipeline' field must be a string (a qualified pipeline name)"
        )
    runnable = served.get(pipeline_name)
    if runnable is None:
        # A routing miss, not a run failure — the named pipeline is not loaded.
        return _wire_problem(
            404, "Unknown pipeline", f"no served pipeline named {pipeline_name!r}"
        )
    inputs = body.get("inputs", {})
    if not isinstance(inputs, dict):
        return _wire_problem(
            400, "Malformed 'inputs'", "the 'inputs' field must be a JSON object of channel values"
        )
    pipeline_run_id = body.get("pipeline_run_id")
    if pipeline_run_id is not None and not isinstance(pipeline_run_id, str):
        return _wire_problem(
            400, "Malformed 'pipeline_run_id'", "the 'pipeline_run_id' field must be a string"
        )
    timeout_ms = body.get("timeout_ms")
    if timeout_ms is not None and (isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int)):
        return _wire_problem(
            400, "Malformed 'timeout_ms'", "the 'timeout_ms' field must be an integer (milliseconds)"
        )

    # --- The token-stream sink: attached iff the served runnable can stream ----------
    # (§ The token stream / R-server-003.) The streamability derivation is the runner's
    # own (stream_route_position — the same derivation run()'s sink-boundary check uses),
    # so a sink is never attached to a runnable that would reject it. A streamed run needs
    # a concrete id BEFORE run() mints one (the sink publishes under it), so the trigger
    # mints the engine-form id up front when the consumer supplied none; run() echoes it.
    stream_sink = None
    if stream_route_position(runnable) is not None:
        if pipeline_run_id is None:
            pipeline_run_id = new_pipeline_run_id()
        token_run_id = pipeline_run_id

        def stream_sink(fragment: str) -> None:
            # Runs on the threadpool runner thread per fragment; publish is the same
            # thread-safe handoff the canonical hub uses and never raises into the run.
            token_hub.publish(token_run_id, fragment)

    # --- Run the pipeline to completion in a threadpool (sync runner, async server) ---
    # The runner emits the run's canonical events through process-global logging on the
    # threadpool thread; a concurrent SSE subscriber (filtered to this pipeline_run_id) sees
    # them live via the EventHub. On halt, run() raises one of the three closed error
    # classes — never both a result and an error (R-server-001).
    try:
        try:
            result = await run_in_threadpool(
                run,
                runnable,
                inputs,
                pipeline_run_id=pipeline_run_id,
                timeout_ms=timeout_ms,
                stream_sink=stream_sink,
            )
        finally:
            # The token stream's terminal marker — published when the run completes,
            # RETURNS OR RAISES, for every run with a knowable id (a consumer-supplied id
            # on a non-streaming run closes a waiting /stream subscriber promptly instead
            # of leaving it to the idle timeout). With no id there was never an id to
            # subscribe under, so there is no stream to close.
            if pipeline_run_id is not None:
                token_hub.publish(pipeline_run_id, _TOKEN_STREAM_END)
    except (ContractViolation, SchemaValidationError, PipelineFailure) as exc:
        status = _status_for(exc)
        return _problem_response(to_problem_details(exc, status), status)

    # Happy path: 200 + the RunResult (state object + run_id string). No envelope field.
    return JSONResponse({"run_id": result.run_id, "state": dict(result.state)})


def _status_for(exc: Exception) -> int:
    """Select the HTTP status for a halted run (reference § Wire error surface). The SVE /
    PipelineFailure status pins are owned by R-error-channel-005; the API-boundary
    missing-input ContractViolation's 400 is the status that rule "leaves caller-supplied,"
    settled by the reference's wire table. Any other runtime ContractViolation (a
    handler-produced structural fault surfacing mid-dispatch) is HTTP-transport territory →
    502, the same "handler body is upstream" rationale R-error-channel-005 gives SVE."""
    if isinstance(exc, SchemaValidationError):
        return 502  # § SchemaValidationError → RFC 9457 status pin
    if isinstance(exc, PipelineFailure):
        if exc.cause_class == "TimeoutError":
            return 504  # § PipelineFailure → RFC 9457 status
        if exc.failure_category == "service":
            return 502
        return 500
    if isinstance(exc, ContractViolation):
        if exc.check is Check.API_INPUTS_ENFORCEMENT:
            return 400  # reference wire table: missing declared input
        return 502  # other runtime CV: handler-produced structural fault
    return 500  # pragma: no cover - the three classes are exhaustive


# ---------------------------------------------------------------------------
# GET /runs/{pipeline_run_id}/events — the SSE projection
# ---------------------------------------------------------------------------


def _event_stream(
    request: Request, hub: EventHub, stream_timeout_s: float | None
) -> Response:
    # Starlette URL-decodes the path param, so the pipeline_run_id arrives exactly as minted.
    # Engine-minted ids are colon-free basic ISO-8601 (no encoding needed); a consumer-supplied
    # id carrying reserved characters still round-trips through the decode.
    pipeline_run_id = request.path_params["pipeline_run_id"]
    # Subscribe BEFORE the streaming response starts, so a stream opened before its run is
    # triggered receives the run's events (the open-stream-then-POST correlation flow).
    queue = hub.subscribe(pipeline_run_id)

    async def frames():
        try:
            while True:
                if stream_timeout_s is None:
                    event = await queue.get()
                else:
                    try:
                        event = await asyncio.wait_for(queue.get(), stream_timeout_s)
                    except asyncio.TimeoutError:
                        return  # lifecycle bound: no event within the window
                yield _to_frame(event)
                if isinstance(event, (PipelineComplete, PipelineError)):
                    return  # terminal frame: close the stream (R-server-002)
        finally:
            hub.unsubscribe(pipeline_run_id, queue)

    return EventSourceResponse(frames())


def _to_frame(event: object) -> dict[str, Any]:
    """Project one canonical event onto one SSE frame (reference § Event-to-frame mapping):
    ``event:`` = the type name; ``data:`` = the canonical in-process payload as a JSON
    object; ``id:`` = the dispatch's ``(pipeline_run_id, handler_position)`` rendered as a
    string for a per-dispatch event (carrying a ``handler_position``), omitted for a
    run-level frame. The payload includes every declared field (nulls included — the
    canonical in-process serialization, NOT the HTTP error surface's null-omission)."""
    name = event.EVENT_TYPE.value  # type: ignore[attr-defined]
    data = json.dumps(dataclasses.asdict(event), default=_json_default)  # type: ignore[call-overload]
    frame: dict[str, Any] = {"event": name, "data": data}
    if isinstance(event, (HandlerEnter, HandlerExit, ServiceInvocation)):
        # The per-dispatch (handler_position-bearing) kinds. Render the id with a ':'
        # separator (``run_...:0``): byte-identical to the dispatch's correlation_id — the
        # same (pipeline_run_id, handler_position) composite (reference § Event-to-frame
        # mapping); the colon-free engine-minted run-id keeps the join legible.
        frame["id"] = f"{event.pipeline_run_id}:{event.handler_position}"
    return frame


def _json_default(value: object) -> object:
    """A canonical-event payload is closed-type plain data; the one non-JSON-native
    container the snapshots could carry is a set (a ``union_set`` merge yields a deduped
    list, so even that is unusual) → list. Anything else is a projection failure — fail
    loud rather than silently emit a malformed frame (R-server-002)."""
    if isinstance(value, (set, frozenset)):
        return list(value)
    raise TypeError(
        f"canonical event payload carries a non-serializable {type(value).__name__} — "
        "the SSE projection fails loud rather than skip or reshape a frame (R-server-002)"
    )


# ---------------------------------------------------------------------------
# GET /runs/{pipeline_run_id}/stream — the provisional token stream
# ---------------------------------------------------------------------------


def _token_stream(
    request: Request, token_hub: EventHub, stream_timeout_s: float | None
) -> Response:
    """The run's provisional token stream (reference § The token stream, R-server-003):
    one ``token`` frame per raw fragment (``data:`` = ``{"text": <fragment>}``), closed
    by the terminal ``end`` frame (``data:`` = ``{}`` — a close signal, never a value:
    the authoritative validated result rides the trigger response / the event stream's
    ``pipeline_complete``). No ``id:`` field — fragments are provisional transport with
    no resume/replay semantics. Same correlation flow as the event stream: subscribe
    before the streaming response starts, so a stream opened before its run is triggered
    receives the run's fragments; the server keeps no fragment history."""
    pipeline_run_id = request.path_params["pipeline_run_id"]
    queue = token_hub.subscribe(pipeline_run_id)

    async def frames():
        try:
            while True:
                if stream_timeout_s is None:
                    item = await queue.get()
                else:
                    try:
                        item = await asyncio.wait_for(queue.get(), stream_timeout_s)
                    except asyncio.TimeoutError:
                        return  # lifecycle bound: no fragment within the window
                if item is _TOKEN_STREAM_END:
                    yield {"event": "end", "data": "{}"}
                    return  # terminal frame: the run completed (returned or raised)
                yield {"event": "token", "data": json.dumps({"text": item})}
        finally:
            token_hub.unsubscribe(pipeline_run_id, queue)

    return EventSourceResponse(frames())


# ---------------------------------------------------------------------------
# Problem+JSON helpers
# ---------------------------------------------------------------------------


def _problem_response(body: dict[str, Any], status: int) -> Response:
    """An ``application/problem+json`` response (R-error-channel-005: the Content-Type of
    every HTTP error response)."""
    return Response(json.dumps(body), status_code=status, media_type=PROBLEM_JSON)


def _wire_problem(status: int, title: str, detail: str) -> Response:
    """A transport-level rejection that arises before any engine error class is raised
    (reference § Wire error surface) — no engine error instance exists to project, so the
    server constructs a minimal RFC 9457 envelope (``type`` = ``about:blank``, RFC 9457
    §4.2)."""
    return _problem_response(
        {"type": "about:blank", "title": title, "status": status, "detail": detail}, status
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """The defect-path 500: an exception no engine error class covers escaping an
    endpoint (an engine bug, never a run halt — the three-class catch owns those). The
    response STILL carries ``application/problem+json`` (R-error-channel-005 states no
    carve-out), with a minimal ``about:blank`` envelope that names no internals; the
    traceback goes to the server log, fail-loud for the operator, opaque on the wire."""
    logging.getLogger("conjured.server").exception(
        "unhandled exception on %s %s", request.method, request.url.path
    )
    return _problem_response(
        {
            "type": "about:blank",
            "title": "Internal Server Error",
            "status": 500,
            "detail": "an unhandled server error occurred (see the server log)",
        },
        500,
    )


async def _http_exception_handler(request: Request, exc: Exception) -> Response:
    """Render Starlette's transport-level HTTP errors (wrong method → 405, unknown path →
    404) as ``application/problem+json``, so every HTTP error response carries the
    problem+json Content-Type (R-error-channel-005). Typed ``Exception`` to match
    Starlette's handler signature; registered for exactly ``StarletteHTTPException``,
    so the assert is the registration invariant, not a runtime branch."""
    assert isinstance(exc, StarletteHTTPException)
    return _problem_response(
        {
            "type": "about:blank",
            "title": exc.detail,
            "status": exc.status_code,
            "detail": exc.detail,
        },
        exc.status_code,
    )
