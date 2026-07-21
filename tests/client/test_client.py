"""The bundled-localhost-subprocess Python client — a blocking call → RunResult.

Launches a **real** server subprocess (``python -m conjured.server``) serving a **real**
compiled+assembled pipeline over loopback, then drives the blocking ``Client.run`` and
asserts the happy-path ``RunResult`` and the halt surface (a ``ServerError`` carrying the
RFC 9457 body). No engine internals are mocked — only the process/transport edge is real
(the boundary-exercise discipline). The served pipelines are built by an importable factory
module the subprocess loads via ``--app`` (the engine has no disk/directory loader).
"""

from __future__ import annotations

import io
import os
import textwrap
from urllib.error import HTTPError

import pytest

from conjured.client import Client, ServerError, _read_problem

# --- The importable app + handler modules the subprocess loads --------------------

_HANDLERS_SRC = textwrap.dedent(
    """
    def echo(*, text):
        return {"result": text.upper()}
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


    def make_pipelines():
        reg = DeclarationRegistry()
        qn = "client_handlers.echo"
        reg.add_handler(
            qn,
            TransformDeclaration(
                reads=(FieldDecl(name="text", type=primitive("str")),),
                output_schema=(FieldDecl(name="result", type=primitive("str")),),
            ),
            toml_path="handlers/echo.toml",
        )
        decl = PipelineDeclaration(
            meta=PipelineMeta(name="client.echo"),
            nodes=(HandlerNode(name=qn),),
            inputs=(FieldDecl(name="text", type=primitive("str")),),
            outputs=(FieldDecl(name="result", type=primitive("str")),),
        )
        graph = compile_pipeline(decl, reg, pipeline_name="client.echo", file_path="<client-test>")
        return {"client.echo": assemble(graph, reg)}
    """
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app_dir = tmp_path_factory.mktemp("client_app_dir")
    (app_dir / "client_handlers.py").write_text(_HANDLERS_SRC, encoding="utf-8")
    (app_dir / "client_app.py").write_text(_APP_SRC, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(app_dir) + os.pathsep + env.get("PYTHONPATH", "")
    started = Client(app="client_app:make_pipelines", env=env, startup_timeout_s=30.0)
    started.start()
    try:
        yield started
    finally:
        started.stop()


def test_blocking_run_returns_runresult(client):
    result = client.run("client.echo", {"text": "hi"})
    assert result.run_id  # engine-minted
    assert dict(result.state) == {"result": "HI"}


def test_consumer_supplied_run_id_echoed(client):
    result = client.run("client.echo", {"text": "yo"}, pipeline_run_id="run_client_1")
    assert result.run_id == "run_client_1"
    assert dict(result.state) == {"result": "YO"}


def test_halt_raises_server_error_with_problem_body(client):
    with pytest.raises(ServerError) as excinfo:
        client.run("client.echo", {})  # missing the declared input → 400 ContractViolation
    assert excinfo.value.status == 400
    assert excinfo.value.problem["rule_id"] == "R-pipeline-001"


def test_unknown_pipeline_raises_server_error_404(client):
    with pytest.raises(ServerError) as excinfo:
        client.run("client.nope", {"text": "x"})
    assert excinfo.value.status == 404


# --- _read_problem: an error body that is not a JSON OBJECT never masks the status ------
# A real HTTPError over a BytesIO body — the transport edge is real, only the body varies.


def _http_error(body: bytes, code: int = 500) -> HTTPError:
    return HTTPError("http://x/runs", code, "Internal Server Error", {}, io.BytesIO(body))


def test_read_problem_degrades_a_json_scalar_body_to_the_status_envelope():
    # A valid-but-non-object JSON body (a bare scalar) is NOT a problem document. _read_problem must
    # degrade to the status envelope, never return the scalar (which breaks the dict[str, object]
    # contract and hides the HTTP status behind a shape the caller cannot read). RED-on-removal: drop
    # the `isinstance(parsed, dict)` guard and `_read_problem` returns `5`, so `problem["status"]`
    # raises TypeError downstream.
    problem = _read_problem(_http_error(b"5", code=500))
    assert problem == {"status": 500, "title": "Internal Server Error", "detail": ""}


def test_read_problem_degrades_a_json_array_body_to_the_status_envelope():
    # A JSON array likewise parses successfully but is not an object — same degrade.
    problem = _read_problem(_http_error(b'["nope"]', code=502))
    assert problem["status"] == 502


def test_read_problem_degrades_a_non_json_body_to_the_status_envelope():
    # The pre-existing behaviour is preserved: a body that is not JSON at all still degrades cleanly.
    problem = _read_problem(_http_error(b"not json at all", code=503))
    assert problem["status"] == 503


def test_read_problem_passes_through_a_json_object_body():
    # The valid path is unbroken: a genuine problem+json object is returned verbatim.
    problem = _read_problem(_http_error(b'{"status": 400, "rule_id": "R-x"}', code=400))
    assert problem == {"status": 400, "rule_id": "R-x"}


# --- CLIENT-CLI-3: the startup fail-loud guarantees, each with its failing case -------


def test_start_fails_loud_when_the_subprocess_exits_before_binding():
    """A bad --app import string kills the subprocess before it binds — start() raises
    the diagnostic RuntimeError (not a bare TimeoutError) and tears the process down
    (the except-branch stop()); RED if the exit-detection poll or the cleanup goes."""
    bad = Client(app="no.such.module:attr", startup_timeout_s=30.0)
    with pytest.raises(RuntimeError, match="exited"):
        bad.start()
    assert bad._proc is None  # torn down, not leaked


def test_start_times_out_against_a_subprocess_that_never_binds(tmp_path):
    """A subprocess that hangs before binding trips startup_timeout_s — TimeoutError,
    with the subprocess terminated (RED if the deadline or teardown is removed)."""
    (tmp_path / "hang_app.py").write_text(
        "import time\ntime.sleep(120)\nMAPPING = {}\n", encoding="utf-8"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    hung = Client(app="hang_app:MAPPING", env=env, startup_timeout_s=3.0)
    with pytest.raises(TimeoutError, match="did not"):
        hung.start()
    assert hung._proc is None  # terminated, not orphaned
