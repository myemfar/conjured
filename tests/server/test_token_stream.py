"""GET /runs/{pipeline_run_id}/stream — the provisional token stream.

Asserts R-server-003: a streamed run's raw fragments are delivered as ``token`` frames on
their OWN endpoint, closed by the terminal ``end`` frame when the run completes (returns
or raises), fully separate from the canonical event stream — no token frame ever rides
``/events``, no canonical event ever rides ``/stream``, and the authoritative value stays
the trigger response / ``pipeline_complete``.

Wire tests are driven against a **real** server subprocess (real uvicorn, real network)
so the open-stream-then-POST correlation flow and the threadpool-runner → event-loop
handoff run the production path (the same rationale as ``test_sse.py``). The generator's
internals (subscriber cleanup, frame shapes, the idle-timeout bound) are driven in-process
against the real ``_token_stream`` + a real ``EventHub``, where the assertion needs a
server-internal the subprocess boundary hides.
"""

from __future__ import annotations

import asyncio
import json
import os
import textwrap

import httpx
import pytest

from conjured.client import Client
from conjured.errors import OUTPUT_VALIDATION_AUDIT_CODE
from conjured.server.app import _TOKEN_STREAM_END, _token_stream
from conjured.server.hub import EventHub

# --- The served app: a streamable pipeline, its non-streamable twin, and a streamable
# --- pipeline whose ASSEMBLED value fails the output boundary (the halt-mid-stream case).

_BACKEND_SRC = textwrap.dedent(
    """
    import json


    class StubStreamingBackend:
        training_artifact_contract = "gguf"
        reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

        def __init__(self, model, *, output_schema, schema_source):
            self.model = model

        def invoke(self, *, input_payload, service_name, caller_qualified_name,
                   caller_position, temperature, max_tokens, **transport_extra):
            return {"dialogue_response": "buffered:" + input_payload["formatted_prompt"]}

        def invoke_streaming(self, *, input_payload, service_name, caller_qualified_name,
                             caller_position, temperature, max_tokens, **transport_extra):
            emission = {"dialogue_response": "streamed:" + input_payload["formatted_prompt"]}
            text = json.dumps(emission)
            third = max(1, len(text) // 3)
            for start in range(0, len(text), third):
                yield text[start:start + third]
            return emission


    class StubInvalidAssembledBackend:
        training_artifact_contract = "gguf"
        reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

        def __init__(self, model, *, output_schema, schema_source):
            self.model = model

        def invoke(self, *, input_payload, service_name, caller_qualified_name,
                   caller_position, temperature, max_tokens, **transport_extra):
            return {"dialogue_response": "buffered"}

        def invoke_streaming(self, *, input_payload, service_name, caller_qualified_name,
                             caller_position, temperature, max_tokens, **transport_extra):
            yield '{"dialogue_response": '
            yield "42}"
            return {"dialogue_response": 42}
    """
)

_APP_SRC = textwrap.dedent(
    """
    from conjured.runner.assemble import assemble
    from conjured.validator import DeclarationRegistry, loads
    from conjured.validator.compile import compile_pipeline

    _SERVICE_TYPE = '''
    name = "{backend}"
    [identity_schema]
    model = { type = "str" }
    [transport_schema]
    endpoint = { type = "str" }
    [config_schema]
    temperature = { type = "float" }
    max_tokens = { type = "int" }
    '''

    _PREP = '''
    [transform]
    [reads]
    raw = { type = "str" }
    [output_schema]
    npc_state = { type = "str" }
    user_message = { type = "str" }
    '''

    _PREPROC = '''
    [transform]
    [reads]
    context = { type = "str" }
    utterance = { type = "str" }
    [output_schema]
    prompt = { type = "str" }
    [bindings.config]
    template = { type = "str" }
    '''

    _COMPOSITION = '''
    [meta]
    kind = "trainable"
    name = "dialogue_training"
    [inputs]
    npc_state = { type = "str" }
    user_message = { type = "str" }
    [outputs]
    dialogue_response = { type = "str" }
    [[preprocessors]]
    kind = "handler"
    name = "tok_handlers.assemble_prompt"
    id   = "assemble_prompt"
    reads_map = { context = "npc_state", utterance = "user_message" }
    writes_map = { prompt = "formatted_prompt" }
    [preprocessors.bindings]
    config = { template = "T" }
    [service_bindings.llm]
    type = "{backend}"
    model = "test-model"
    [trainable]
    streamable = {streamable}
    [trainable.config]
    temperature = 0.7
    max_tokens = 64
    [trainable.service_bindings]
    llm = { type = "{backend}" }
    [trainable.reads]
    formatted_prompt = { type = "str" }
    [trainable.output_schema]
    dialogue_response = { type = "str" }
    '''

    _PIPELINE = '''
    [meta]
    name = "{name}"
    [[nodes]]
    kind = "handler"
    name = "tok_handlers.prep"
    [[nodes]]
    kind = "composition"
    name = "{composition}"
    [inputs]
    raw = { type = "str" }
    [outputs]
    dialogue_response = { type = "str" }
    '''

    _DEPLOYMENT = '''
    [transport.llm]
    endpoint = "https://llm.test/v1"
    [training_contract]
    integrity_enforcement = false
    '''


    def _build(reg, deployment, name, composition):
        pipeline = loads(
            _PIPELINE.replace("{name}", name).replace("{composition}", composition),
            "pipeline", file_path=name + ".toml",
        )
        graph = compile_pipeline(
            pipeline, reg, pipeline_name=name, deployment=deployment,
            file_path=name + ".toml",
        )
        return assemble(graph, reg, deployment)


    def make_pipelines():
        reg = DeclarationRegistry()
        for backend in ("tok_backend_mod.StubStreamingBackend",
                        "tok_backend_mod.StubInvalidAssembledBackend"):
            reg.add_service_type(
                loads(_SERVICE_TYPE.replace("{backend}", backend), "service_type",
                      file_path="st.toml"),
                toml_path="st.toml",
            )
        reg.add_handler(
            "tok_handlers.prep", loads(_PREP, "handler", file_path="prep.toml"),
            toml_path="handlers/prep.toml",
        )
        reg.add_handler(
            "tok_handlers.assemble_prompt",
            loads(_PREPROC, "handler", file_path="pp.toml"),
            toml_path="handlers/pp.toml",
        )
        for path, streamable, backend in (
            ("trainables/stream.toml", "true", "tok_backend_mod.StubStreamingBackend"),
            ("trainables/plain.toml", "false", "tok_backend_mod.StubStreamingBackend"),
            ("trainables/bad.toml", "true", "tok_backend_mod.StubInvalidAssembledBackend"),
        ):
            reg.add_composition(
                path,
                loads(
                    _COMPOSITION.replace("{streamable}", streamable)
                                .replace("{backend}", backend),
                    "composition", file_path=path,
                ),
            )
        deployment = loads(_DEPLOYMENT, "deployment", file_path="d.toml")
        return {
            "srv.stream": _build(reg, deployment, "srv.stream", "trainables/stream.toml"),
            "srv.plain": _build(reg, deployment, "srv.plain", "trainables/plain.toml"),
            "srv.badstream": _build(reg, deployment, "srv.badstream", "trainables/bad.toml"),
        }
    """
)

_HANDLERS_SRC = textwrap.dedent(
    """
    def prep(*, raw):
        return {"npc_state": "calm", "user_message": raw}


    def assemble_prompt(*, context, utterance, config):
        return {"prompt": context + "|" + utterance + "|" + config}
    """
)

#: What the streaming backend emits for inputs {"raw": "hello"} through the prep chain.
_EXPECTED_VALUE = "streamed:calm|hello|T"
_EXPECTED_WIRE_TEXT = '{"dialogue_response": "' + _EXPECTED_VALUE + '"}'


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    app_dir = tmp_path_factory.mktemp("token_app_dir")
    (app_dir / "tok_handlers.py").write_text(_HANDLERS_SRC, encoding="utf-8")
    (app_dir / "tok_backend_mod.py").write_text(_BACKEND_SRC, encoding="utf-8")
    (app_dir / "tok_app.py").write_text(_APP_SRC, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(app_dir) + os.pathsep + env.get("PYTHONPATH", "")
    client = Client(app="tok_app:make_pipelines", env=env, startup_timeout_s=30.0)
    client.start()
    try:
        yield client
    finally:
        client.stop()


async def _read_frames(stream, terminal: str) -> list[dict[str, str]]:
    """Read SSE frames off an already-open ``httpx`` stream response up to (and
    including) the ``terminal`` event name (the same hand-parse ``test_sse.py`` uses —
    no SSE client library dependency)."""
    frames: list[dict[str, str]] = []
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
    return frames


# ---------------------------------------------------------------------------
# 1. The wire contract — fragments as token frames, closed by the end frame
# ---------------------------------------------------------------------------


def test_token_stream_delivers_fragments_then_end(server):
    """The happy path: a streamed run's raw fragments arrive as ``token`` frames in
    delivery order (their concatenated ``text`` payloads reassemble the emission's raw
    wire text), the terminal ``end`` frame closes the stream when the run completes, and
    the trigger response still carries the ONE complete validated value — a frame is
    provisional transport, never the result."""
    run_id = "run_tok_ok_1"

    async def drive():
        async with httpx.AsyncClient(base_url=server.base_url, timeout=20.0) as client:
            async with client.stream("GET", f"/runs/{run_id}/stream") as stream:
                assert stream.status_code == 200
                assert stream.headers["content-type"].startswith("text/event-stream")
                post = await client.post("/runs", json={
                    "pipeline": "srv.stream", "inputs": {"raw": "hello"},
                    "pipeline_run_id": run_id,
                })
                frames = await _read_frames(stream, terminal="end")
        return post, frames

    post, frames = asyncio.run(drive())
    assert post.status_code == 200
    body = post.json()
    assert body["run_id"] == run_id
    assert body["state"]["dialogue_response"] == _EXPECTED_VALUE
    # Token frames in delivery order, then exactly one terminal end frame.
    assert [f["event"] for f in frames[:-1]] == ["token"] * (len(frames) - 1)
    assert len(frames) >= 4  # the stub yields >= 3 fragments + the end frame
    assert frames[-1]["event"] == "end"
    assert json.loads(frames[-1]["data"]) == {}  # a close signal, never a value carrier
    texts = [json.loads(f["data"])["text"] for f in frames[:-1]]
    assert "".join(texts) == _EXPECTED_WIRE_TEXT
    # No id: field — provisional transport carries no resume/replay handle.
    assert all("id" not in f for f in frames)


def test_token_stream_of_a_non_streaming_run_carries_only_end(server):
    """A run with NO streamable terminal produces no token frames — its stream carries
    only the terminal ``end`` frame at run completion, closing a waiting subscriber
    promptly instead of leaving it to the idle timeout."""
    run_id = "run_tok_plain_1"

    async def drive():
        async with httpx.AsyncClient(base_url=server.base_url, timeout=20.0) as client:
            async with client.stream("GET", f"/runs/{run_id}/stream") as stream:
                assert stream.status_code == 200
                post = await client.post("/runs", json={
                    "pipeline": "srv.plain", "inputs": {"raw": "hello"},
                    "pipeline_run_id": run_id,
                })
                frames = await _read_frames(stream, terminal="end")
        return post, frames

    post, frames = asyncio.run(drive())
    assert post.status_code == 200
    assert post.json()["state"]["dialogue_response"] == "buffered:calm|hello|T"
    assert [f["event"] for f in frames] == ["end"]


def test_token_stream_ends_when_the_run_halts(server):
    """The end frame publishes when the run completes RETURNS OR RAISES: a stream whose
    assembled value fails the output boundary halts the run (validate-on-assembly), and
    the token stream still terminates — fragments already delivered stay delivered
    (provisional by contract), the trigger returns the structured error, and the stream
    closes with ``end`` rather than hanging."""
    run_id = "run_tok_halt_1"

    async def drive():
        async with httpx.AsyncClient(base_url=server.base_url, timeout=20.0) as client:
            async with client.stream("GET", f"/runs/{run_id}/stream") as stream:
                assert stream.status_code == 200
                post = await client.post("/runs", json={
                    "pipeline": "srv.badstream", "inputs": {"raw": "hello"},
                    "pipeline_run_id": run_id,
                })
                frames = await _read_frames(stream, terminal="end")
        return post, frames

    post, frames = asyncio.run(drive())
    assert post.status_code == 502  # SchemaValidationError -> RFC 9457 status pin
    assert post.headers["content-type"] == "application/problem+json"
    assert post.json()["audit_code"] == OUTPUT_VALIDATION_AUDIT_CODE  # the SVE envelope
    # The fragments HAD been delivered before the halt, then the terminal end frame.
    assert frames[-1]["event"] == "end"
    token_frames = frames[:-1]
    assert token_frames and all(f["event"] == "token" for f in token_frames)
    assert "".join(json.loads(f["data"])["text"] for f in token_frames) == (
        '{"dialogue_response": 42}'
    )


def test_token_stream_and_event_stream_never_cross(server):
    """R-server-003's separation seal, on the real wire: for ONE streamed run with both
    streams open, the canonical event stream carries only closed-enum event frames (no
    token / end frame ever rides it) and the token stream carries only token frames plus
    the terminal end (no canonical event ever rides it)."""
    run_id = "run_tok_separate_1"

    async def drive():
        async with httpx.AsyncClient(base_url=server.base_url, timeout=20.0) as client:
            async with client.stream("GET", f"/runs/{run_id}/events") as events:
                async with client.stream("GET", f"/runs/{run_id}/stream") as tokens:
                    assert events.status_code == 200
                    assert tokens.status_code == 200
                    post = await client.post("/runs", json={
                        "pipeline": "srv.stream", "inputs": {"raw": "hello"},
                        "pipeline_run_id": run_id,
                    })
                    # Both hub queues buffer unboundedly, so sequential reads see all.
                    token_frames = await _read_frames(tokens, terminal="end")
                    event_frames = await _read_frames(events, terminal="pipeline_complete")
        return post, event_frames, token_frames

    post, event_frames, token_frames = asyncio.run(drive())
    assert post.status_code == 200
    event_names = {f["event"] for f in event_frames}
    assert event_names <= {
        "pipeline_start", "handler_enter", "handler_exit", "pipeline_complete"
    }
    assert "pipeline_complete" in event_names  # the canonical stream still terminates
    assert {f["event"] for f in token_frames} == {"token", "end"}
    # The captured trainable record on the canonical stream is the COMPLETE validated
    # value, never a fragment (the channel-value seal holds across the wire).
    exits = [json.loads(f["data"]) for f in event_frames if f["event"] == "handler_exit"]
    trainable_exits = [e for e in exits if e["node_kind"] == "trainable"]
    assert len(trainable_exits) == 1
    assert trainable_exits[0]["writes_snapshot"] == {"dialogue_response": _EXPECTED_VALUE}


def test_trigger_mints_the_run_id_upfront_for_a_streaming_run(server):
    """A streaming-capable pipeline triggered WITHOUT a pipeline_run_id still runs clean:
    the trigger mints the engine-form id up front (the sink needs a concrete id before
    run() would mint one) and run() echoes it back on the RunResult."""

    async def drive():
        async with httpx.AsyncClient(base_url=server.base_url, timeout=20.0) as client:
            return await client.post("/runs", json={
                "pipeline": "srv.stream", "inputs": {"raw": "hello"},
            })

    post = asyncio.run(drive())
    assert post.status_code == 200
    body = post.json()
    assert body["state"]["dialogue_response"] == _EXPECTED_VALUE
    # The engine-minted structured form (run_<ISO-8601 UTC>_<short-random>) — minted by
    # the trigger via the runner's single minting point, not an ad-hoc server format.
    assert body["run_id"].startswith("run_")


# ---------------------------------------------------------------------------
# 2. Generator internals — in-process against the real _token_stream + EventHub
# ---------------------------------------------------------------------------


class _FakeRequest:
    """The minimal Request surface `_token_stream` reads — the URL-decoded path param."""

    def __init__(self, run_id: str) -> None:
        self.path_params = {"pipeline_run_id": run_id}


def test_token_stream_subscriber_cleanup_on_client_disconnect():
    """RED-on-removal seal for the token generator's `finally: unsubscribe`: a client
    that receives fragments then ABRUPTLY DISCONNECTS before the end frame must not leak
    its subscription (the GeneratorExit path a disconnect raises at the yield)."""
    run_id = "run_tok_leak_1"

    async def drive():
        hub = EventHub()
        resp = _token_stream(_FakeRequest(run_id), hub, stream_timeout_s=None)
        gen = resp.body_iterator  # the production frames() async generator
        assert run_id in hub._subscribers  # subscribed before the stream body starts
        hub.publish(run_id, "frag-1")
        frame = await gen.__anext__()
        assert frame["event"] == "token"
        assert json.loads(frame["data"]) == {"text": "frag-1"}
        assert run_id in hub._subscribers  # still subscribed mid-stream
        await gen.aclose()  # the client disconnects before the terminal frame
        return dict(hub._subscribers)

    assert run_id not in asyncio.run(drive())


def test_token_stream_terminates_and_unsubscribes_on_the_end_marker():
    """The terminal path: the END marker yields exactly one `end` frame (data `{}`),
    the generator then terminates, and the subscription is removed — the marker is
    identity-compared, so a fragment STRING can never impersonate it."""
    run_id = "run_tok_term_1"

    async def drive():
        hub = EventHub()
        resp = _token_stream(_FakeRequest(run_id), hub, stream_timeout_s=None)
        gen = resp.body_iterator
        hub.publish(run_id, _TOKEN_STREAM_END)
        frame = await gen.__anext__()
        assert frame == {"event": "end", "data": "{}"}
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        return dict(hub._subscribers)

    assert run_id not in asyncio.run(drive())


def test_token_stream_idle_timeout_closes_without_frames():
    """The lifecycle bound: a token stream that receives nothing within
    `stream_timeout_s` (e.g. one opened for a run that is never triggered) closes empty
    and unsubscribes, exactly like the event stream's bound."""
    run_id = "run_tok_idle_1"

    async def drive():
        hub = EventHub()
        resp = _token_stream(_FakeRequest(run_id), hub, stream_timeout_s=0.01)
        gen = resp.body_iterator
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        return dict(hub._subscribers)

    assert run_id not in asyncio.run(drive())

