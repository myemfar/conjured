"""GET /runs/{pipeline_run_id}/events — the SSE event-stream projection.

Asserts R-server-002: a run's ``conjured.events.runner`` stream is projected onto SSE
**filtered to one ``pipeline_run_id``**, **in event order** (pipeline_start first, each
dispatch's events by handler_position, terminal frame last), per-dispatch frames carrying
``id:`` and run-level frames omitting it, terminating on
``pipeline_complete`` / ``pipeline_error``.

Driven against a **real** server subprocess (real uvicorn, real network) so the
open-stream-then-POST correlation flow runs the production path: the SSE GET subscribes the
run before the POST triggers it, and the run's events — emitted from the server's threadpool
runner — stream onto the open connection concurrently. (The in-process ``httpx.ASGITransport``
serializes the single event loop and cannot model the concurrent stream-open + POST.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import textwrap

import httpx
import pytest

from conjured.client import Client
from conjured.events import PipelineStart, now_iso
from conjured.server.app import _event_stream, _to_frame
from conjured.server.hub import EventHub

_HANDLERS_SRC = textwrap.dedent(
    """
    def echo(*, text):
        return {"result": text.upper()}


    def boom(*, text):
        raise ValueError("kaboom")
    """
)

_APP_SRC = textwrap.dedent(
    """
    from conjured.ir.channel_types import FieldDecl, primitive
    from conjured.ir.handler import TransformDeclaration
    from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
    from conjured.runner.assemble import assemble
    from conjured.validator import compile_pipeline
    from conjured.validator.registry import DeclarationRegistry


    def _fd(name, token="str"):
        return FieldDecl(name=name, type=primitive(token))


    def _build(reg, qn, out_field, pipeline_name):
        decl = PipelineDeclaration(
            meta=PipelineMeta(name=pipeline_name),
            nodes=(HandlerNode(name=qn),),
            inputs=(_fd("text"),), outputs=(_fd(out_field),),
        )
        graph = compile_pipeline(decl, reg, pipeline_name=pipeline_name, file_path="<sse-test>")
        return assemble(graph, reg)


    def make_pipelines():
        reg = DeclarationRegistry()
        reg.add_handler("sse_handlers.echo",
            TransformDeclaration(reads=(_fd("text"),), output_schema=(_fd("result"),)),
            toml_path="h.toml")
        reg.add_handler("sse_handlers.boom",
            TransformDeclaration(reads=(_fd("text"),), output_schema=(_fd("out"),)),
            toml_path="h.toml")
        return {
            "srv.echo": _build(reg, "sse_handlers.echo", "result", "srv.echo"),
            "srv.boom": _build(reg, "sse_handlers.boom", "out", "srv.boom"),
        }
    """
)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    app_dir = tmp_path_factory.mktemp("sse_app_dir")
    (app_dir / "sse_handlers.py").write_text(_HANDLERS_SRC, encoding="utf-8")
    (app_dir / "sse_app.py").write_text(_APP_SRC, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(app_dir) + os.pathsep + env.get("PYTHONPATH", "")
    client = Client(app="sse_app:make_pipelines", env=env, startup_timeout_s=30.0)
    client.start()
    try:
        yield client
    finally:
        client.stop()


async def _collect(base_url, run_id, post_body, terminal):
    """Open the SSE stream for ``run_id``, trigger the run, and collect SSE frames up to
    (and including) the ``terminal`` event. Returns ``(post_response, frames)``."""
    frames: list[dict[str, str]] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=20.0) as client:
        async with client.stream("GET", f"/runs/{run_id}/events") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            post_response = await client.post("/runs", json=post_body)
            cur: dict[str, str] = {}
            async for raw in stream.aiter_lines():
                line = raw.rstrip("\r")
                if line == "":  # frame boundary
                    if cur:
                        frames.append(cur)
                        if cur.get("event") == terminal:
                            break
                        cur = {}
                    continue
                if line.startswith(":"):  # comment / keep-alive ping
                    continue
                field, _, value = line.partition(":")
                cur[field] = value[1:] if value.startswith(" ") else value
    return post_response, frames


def _events(frames):
    return [f["event"] for f in frames]


def test_sse_happy_path_orders_and_terminates_on_complete(server):
    run_id = "run_sse_ok_1"
    post, frames = asyncio.run(
        _collect(
            server.base_url, run_id,
            {"pipeline": "srv.echo", "inputs": {"text": "hi"}, "pipeline_run_id": run_id},
            terminal="pipeline_complete",
        )
    )
    assert post.status_code == 200
    assert _events(frames) == [
        "pipeline_start", "handler_enter", "handler_exit", "pipeline_complete"
    ]
    by_event = {f["event"]: f for f in frames}
    # Per-dispatch frames carry an id: field = (pipeline_run_id, handler_position) joined
    # by ':'; run-level frames omit it.
    assert by_event["handler_enter"]["id"] == f"{run_id}:0"
    assert by_event["handler_exit"]["id"] == f"{run_id}:0"
    assert "id" not in by_event["pipeline_start"]
    assert "id" not in by_event["pipeline_complete"]
    # Byte-identity guard (RED-on-removal): the SSE id: MUST be byte-equal to the in-process
    # correlation_id for the same dispatch. runner/dispatch.py builds that correlation_id as
    # f"{pipeline_run_id}:{handler_position}"; the sole handler here is position 0. Construct the
    # expected value the SAME way so reverting app.py's separator (e.g. back to '#') makes this go
    # RED — full byte-equality, NOT an endswith/substring check.
    sole_handler_position = 0
    expected_correlation_id = f"{run_id}:{sole_handler_position}"  # mirrors dispatch.py correlation_id
    assert by_event["handler_enter"]["id"] == expected_correlation_id
    assert by_event["handler_exit"]["id"] == expected_correlation_id
    # data: is the canonical in-process payload as JSON.
    start_payload = json.loads(by_event["pipeline_start"]["data"])
    assert start_payload["pipeline_run_id"] == run_id
    assert start_payload["inputs_snapshot"] == {"text": "hi"}
    complete_payload = json.loads(by_event["pipeline_complete"]["data"])
    assert complete_payload["outputs_snapshot"] == {"result": "HI"}


def test_sse_terminates_on_error_frame(server):
    run_id = "run_sse_err_1"
    post, frames = asyncio.run(
        _collect(
            server.base_url, run_id,
            {"pipeline": "srv.boom", "inputs": {"text": "hi"}, "pipeline_run_id": run_id},
            terminal="pipeline_error",
        )
    )
    assert post.status_code == 500  # PipelineFailure, handler locus
    # No handler_exit (the body raised); the terminal frame is pipeline_error.
    assert _events(frames) == ["pipeline_start", "handler_enter", "pipeline_error"]
    error_payload = json.loads(frames[-1]["data"])
    assert error_payload["error_class"] == "PipelineFailure"
    assert error_payload["failure_category"] == "handler"
    assert error_payload["cause_class"] == "ValueError"


def test_sse_filters_to_one_run(server):
    """A stream for run A receives only run A's events — a concurrently-triggered run B
    (different pipeline_run_id) never crosses into it (R-server-002 filtering)."""
    run_a = "run_sse_filter_a"

    async def drive():
        async with httpx.AsyncClient(base_url=server.base_url, timeout=20.0) as client:
            async with client.stream("GET", f"/runs/{run_a}/events") as stream:
                assert stream.status_code == 200
                # Trigger a DIFFERENT run first, then run A.
                await client.post("/runs", json={
                    "pipeline": "srv.echo", "inputs": {"text": "b"}, "pipeline_run_id": "run_sse_filter_b"})
                await client.post("/runs", json={
                    "pipeline": "srv.echo", "inputs": {"text": "a"}, "pipeline_run_id": run_a})
                seen_run_ids = set()
                cur: dict[str, str] = {}
                async for raw in stream.aiter_lines():
                    line = raw.rstrip("\r")
                    if line == "":
                        if cur:
                            seen_run_ids.add(json.loads(cur["data"])["pipeline_run_id"])
                            if cur.get("event") == "pipeline_complete":
                                break
                            cur = {}
                        continue
                    if line.startswith(":"):
                        continue
                    field, _, value = line.partition(":")
                    cur[field] = value[1:] if value.startswith(" ") else value
                return seen_run_ids

    assert asyncio.run(drive()) == {run_a}  # run B's events never entered run A's stream


# ---------------------------------------------------------------------------
# Subscriber-leak cleanup — the SSE frames() generator's `finally: hub.unsubscribe`
#   removes the run's subscription on client disconnect. Driven IN-PROCESS against the
#   real `_event_stream` + a real `EventHub` (the production frames() closure), because
#   the assertion inspects `hub._subscribers` — a server-internal the subprocess server
#   in the tests above does not expose. No engine internal is mocked: a real hub, the
#   real generator, the real delivery path (`hub.emit`).
# ---------------------------------------------------------------------------


class _FakeRequest:
    """The minimal Request surface `_event_stream` reads — the URL-decoded path param."""

    def __init__(self, run_id: str) -> None:
        self.path_params = {"pipeline_run_id": run_id}


def _event_record(event: object) -> logging.LogRecord:
    """Wrap a canonical event as the `conjured.events.runner` LogRecord the hub routes
    (the event rides as `record.msg`; the engine never string-formats it)."""
    return logging.LogRecord(
        "conjured.events.runner", logging.INFO, "", 0, event, None, None
    )


def test_sse_subscriber_cleanup_on_client_disconnect():
    """RED-on-removal seal for the subscriber-leak cleanup: when a client opens a stream,
    receives `pipeline_start`, then ABRUPTLY DISCONNECTS before the terminal frame, the
    `frames()` generator's `finally: hub.unsubscribe` (app.py) must remove the run's
    subscription so `hub._subscribers` holds no entry for that run id.

    Open the stream (subscribes), deliver a non-terminal `pipeline_start`, consume that one
    frame, then `aclose()` the response generator — the GeneratorExit a client disconnect
    raises at the suspended `yield`. RED if the `finally: hub.unsubscribe` is dropped: the
    subscription would leak (the entry would survive the disconnect)."""
    run_id = "run_sse_leak_1"

    async def drive():
        hub = EventHub()
        resp = _event_stream(_FakeRequest(run_id), hub, stream_timeout_s=None)
        gen = resp.body_iterator  # the production frames() async generator
        assert run_id in hub._subscribers  # subscribed before the stream body starts
        # Deliver a pipeline_start (the run began) — live, but NOT a terminal frame.
        hub.emit(_event_record(PipelineStart(
            pipeline_run_id=run_id, pipeline_hash="h0",
            timestamp=now_iso(), inputs_snapshot={"text": "hi"},
        )))
        frame = await gen.__anext__()  # consume the pipeline_start frame
        assert frame["event"] == "pipeline_start"
        assert run_id in hub._subscribers  # still subscribed mid-stream
        # The client abruptly disconnects before the terminal frame: GeneratorExit at the yield.
        await gen.aclose()
        return dict(hub._subscribers)

    subscribers = asyncio.run(drive())
    assert run_id not in subscribers  # the finally cleaned up the subscription — no leak


# ---------------------------------------------------------------------------
# Event-to-frame projection (`_to_frame`) — faithful frames + fail-loud (R-server-002)
# ---------------------------------------------------------------------------


def test_sse_frame_includes_null_fields():
    """The SSE `data:` payload is the canonical IN-PROCESS serialization — it carries EVERY
    declared field, NULLS INCLUDED (reference § Event-to-frame mapping), in deliberate contrast
    to the HTTP error surface's null-omission (where e.g. `service_binding_name` is dropped when
    null). A `PipelineStart` with no `parent_run_id` (defaults None) frames a `data:` JSON object
    that INCLUDES `parent_run_id: null`. RED if the projection switches to a null-omitting
    serializer for the event stream."""
    event = PipelineStart(
        pipeline_run_id="run_frame_1", pipeline_hash="h0",
        timestamp=now_iso(), inputs_snapshot={"text": "hi"},  # parent_run_id defaults None
    )
    frame = _to_frame(event)
    assert frame["event"] == "pipeline_start"
    data = json.loads(frame["data"])
    assert "parent_run_id" in data and data["parent_run_id"] is None  # null INCLUDED, not omitted


def test_sse_projection_fails_loud_on_non_serializable_payload():
    """R-server-002 — the frame projection FAILS LOUD on a non-serializable payload value rather
    than skip or reshape a frame (a malformed/missing frame would corrupt the very event
    provenance the stream exists to provide). `_json_default` converts the one expected non-JSON
    container (a set → list) but RAISES `TypeError` on anything else. A canonical event whose
    snapshot carries an opaque object therefore makes `_to_frame` raise. RED if `_json_default`
    stops raising (e.g. coerces to str / None) on an unexpected type."""
    class _Opaque:
        pass

    event = PipelineStart(
        pipeline_run_id="run_frame_2", pipeline_hash="h0",
        timestamp=now_iso(), inputs_snapshot={"bad": _Opaque()},
    )
    with pytest.raises(TypeError, match="non-serializable"):
        _to_frame(event)
