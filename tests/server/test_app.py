"""POST /runs — the synchronous run trigger + the wire error surface.

Drives a real ``Runnable`` over the HTTP surface with Starlette's ``TestClient`` (the app
runs in-process; the runner dispatches for real). Covers the happy path and ≥1 case per
error path: the API-boundary ``ContractViolation`` (400), a value-level
``SchemaValidationError`` (502), a runtime ``PipelineFailure`` (500), plus the wire-level
rejections (malformed body, unknown pipeline, wrong method) — each asserting the structured
RFC 9457 body + status (R-server-001; R-error-channel-005; R-testing baseline coverage).
"""

from __future__ import annotations

from starlette.testclient import TestClient

from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.common import ServiceBindingDecl, ServiceBindingSupply
from conjured.ir.handler import ServiceDeclaration
from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.server import create_app
from conjured.testing import load_test_pipeline

PROBLEM_JSON = "application/problem+json"

# A fake service adapter whose invoke() RAISES — the failure escapes the adapter boundary as a
# service-locus PipelineFailure (failure_category="service"), which the wire surface pins to 502.
_BOOM_ADAPTER = """
from conjured.testing import VerifiedFake

class BoomAdapter(VerifiedFake):
    def invoke(self, *, input_payload, service_name, caller_qualified_name, caller_position, **x):
        return self._invoke(
            input_payload=input_payload, service_name=service_name,
            caller_qualified_name=caller_qualified_name, caller_position=caller_position, **x,
        )
    def validate_input(self, input_payload):
        return None
    def respond(self, input_payload):
        raise RuntimeError("backend unreachable")
"""


# --- Happy path -------------------------------------------------------------------


def test_post_runs_returns_runresult(echo_app):
    client = TestClient(echo_app)
    resp = client.post(
        "/runs",
        json={"pipeline": "srv.echo", "inputs": {"text": "hi"}, "pipeline_run_id": "run_x_1"},
    )
    assert resp.status_code == 200
    # No success/ok/status envelope field — just run_id + state (R-server-001).
    assert resp.json() == {"run_id": "run_x_1", "state": {"result": "HI"}}


def test_post_runs_generates_run_id_when_absent(echo_app):
    client = TestClient(echo_app)
    resp = client.post("/runs", json={"pipeline": "srv.echo", "inputs": {"text": "yo"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == {"result": "YO"}
    assert isinstance(body["run_id"], str) and body["run_id"]  # engine-minted


def test_pipeline_with_no_declared_inputs_runs_without_inputs(make_runnable, fd):
    runnable = make_runnable(
        module_name="srv_const_mod", fn_name="const",
        src="def const():\n    return {'out': 'k'}\n",
        pipeline_name="srv.const", reads=(), outputs=(fd("out"),), inputs=(),
    )
    client = TestClient(create_app({runnable.pipeline_name: runnable}))
    resp = client.post("/runs", json={"pipeline": "srv.const"})
    assert resp.status_code == 200
    assert resp.json()["state"] == {"out": "k"}


# --- Wire-level rejections (before any engine error class) ------------------------


def test_malformed_json_body_400(echo_app):
    client = TestClient(echo_app)
    resp = client.post("/runs", content=b"not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.headers["content-type"] == PROBLEM_JSON
    assert resp.json()["status"] == 400


def test_missing_pipeline_field_400(echo_app):
    client = TestClient(echo_app)
    resp = client.post("/runs", json={"inputs": {"text": "hi"}})
    assert resp.status_code == 400
    assert resp.headers["content-type"] == PROBLEM_JSON


def test_non_object_inputs_400(echo_app):
    client = TestClient(echo_app)
    resp = client.post("/runs", json={"pipeline": "srv.echo", "inputs": [1, 2]})
    assert resp.status_code == 400


def test_non_string_pipeline_field_400(echo_app):
    """The 'pipeline' field must be a string (a qualified name) — a non-string is a wire-level
    malformed field, 400, before any routing/run (only inputs-not-an-object was covered)."""
    client = TestClient(echo_app)
    resp = client.post("/runs", json={"pipeline": 123, "inputs": {"text": "hi"}})
    assert resp.status_code == 400
    assert resp.headers["content-type"] == PROBLEM_JSON
    assert resp.json()["title"] == "Malformed 'pipeline'"


def test_non_string_pipeline_run_id_400(echo_app):
    """An explicit non-string 'pipeline_run_id' is a wire-level malformed field → 400 (a string
    is the only admitted form; absent is fine — the engine mints one)."""
    client = TestClient(echo_app)
    resp = client.post(
        "/runs",
        json={"pipeline": "srv.echo", "inputs": {"text": "hi"}, "pipeline_run_id": 123},
    )
    assert resp.status_code == 400
    assert resp.headers["content-type"] == PROBLEM_JSON
    assert resp.json()["title"] == "Malformed 'pipeline_run_id'"


def test_non_integer_timeout_ms_400(echo_app):
    """A non-integer 'timeout_ms' is a wire-level malformed field → 400 (milliseconds is an
    integer; absent is fine — no budget)."""
    client = TestClient(echo_app)
    resp = client.post(
        "/runs",
        json={"pipeline": "srv.echo", "inputs": {"text": "hi"}, "timeout_ms": "soon"},
    )
    assert resp.status_code == 400
    assert resp.headers["content-type"] == PROBLEM_JSON
    assert resp.json()["title"] == "Malformed 'timeout_ms'"


def test_boolean_timeout_ms_400(echo_app):
    """A bool 'timeout_ms' is rejected → 400: `bool` is an `int` subclass in Python, so the
    check excludes it explicitly (a `True` budget is malformed, not `timeout_ms=1`)."""
    client = TestClient(echo_app)
    resp = client.post(
        "/runs",
        json={"pipeline": "srv.echo", "inputs": {"text": "hi"}, "timeout_ms": True},
    )
    assert resp.status_code == 400
    assert resp.json()["title"] == "Malformed 'timeout_ms'"


def test_unknown_pipeline_404(echo_app):
    client = TestClient(echo_app)
    resp = client.post("/runs", json={"pipeline": "srv.nope", "inputs": {}})
    assert resp.status_code == 404
    assert resp.headers["content-type"] == PROBLEM_JSON
    assert "srv.nope" in resp.json()["detail"]


def test_wrong_method_405_problem_json(echo_app):
    client = TestClient(echo_app)
    resp = client.get("/runs")
    assert resp.status_code == 405
    assert resp.headers["content-type"] == PROBLEM_JSON


def test_unknown_path_404_problem_json(echo_app):
    client = TestClient(echo_app)
    resp = client.post("/nope", json={})
    assert resp.status_code == 404
    assert resp.headers["content-type"] == PROBLEM_JSON


# --- The three closed error classes, projected to RFC 9457 ------------------------


def test_missing_declared_input_contract_violation_400(echo_app):
    """API-boundary missing declared input → ContractViolation → 400 (the CV status the
    RFC 9457 projection leaves caller-supplied; reference § Wire error surface)."""
    client = TestClient(echo_app)
    resp = client.post("/runs", json={"pipeline": "srv.echo", "inputs": {}})
    assert resp.status_code == 400
    assert resp.headers["content-type"] == PROBLEM_JSON
    body = resp.json()
    assert body["status"] == 400
    assert body["rule_id"] == "R-pipeline-001"
    assert body["instance"] == "srv.echo"  # composition_ref (file_path null)
    # audit_code is deferred (None) → omitted; type falls back to about:blank.
    assert "audit_code" not in body
    assert body["type"] == "about:blank"


def test_input_value_type_violation_schema_validation_error_502(make_runnable, fd):
    """A value-level type violation at the reads boundary → SchemaValidationError → 502
    (§ SchemaValidationError → RFC 9457 status pin)."""
    runnable = make_runnable(
        module_name="srv_int_mod", fn_name="add_one",
        src="def add_one(*, n):\n    return {'out': n + 1}\n",
        pipeline_name="srv.adder",
        reads=(fd("n", "int"),), outputs=(fd("out", "int"),), inputs=(fd("n", "int"),),
    )
    client = TestClient(create_app({runnable.pipeline_name: runnable}))
    resp = client.post("/runs", json={"pipeline": "srv.adder", "inputs": {"n": "not-an-int"}})
    assert resp.status_code == 502
    assert resp.headers["content-type"] == PROBLEM_JSON
    body = resp.json()
    assert body["status"] == 502
    assert body["audit_code"] == "C1.HALT_ON_INPUT_VALIDATION_ERROR.001"
    assert body["rule_id"] == "R-error-channel-003"
    assert body["field_validations"]  # non-empty array, verbatim
    # No per-error web URI — dispatch is on `audit_code`, asserted above.
    assert body["type"] == "about:blank"


def test_handler_raises_pipeline_failure_500(make_runnable, fd):
    """A handler body raising → PipelineFailure (handler locus) → 500 (§ PipelineFailure →
    RFC 9457 status: not a timeout, not a service locus → 500)."""
    runnable = make_runnable(
        module_name="srv_boom_mod", fn_name="boom",
        src="def boom(*, text):\n    raise ValueError('kaboom')\n",
        pipeline_name="srv.boom",
        reads=(fd("text"),), outputs=(fd("out"),), inputs=(fd("text"),),
    )
    client = TestClient(create_app({runnable.pipeline_name: runnable}))
    resp = client.post("/runs", json={"pipeline": "srv.boom", "inputs": {"text": "hi"}})
    assert resp.status_code == 500
    assert resp.headers["content-type"] == PROBLEM_JSON
    body = resp.json()
    assert body["status"] == 500
    assert body["failure_category"] == "handler"
    assert body["cause_class"] == "ValueError"
    assert body["cause_message"] == "kaboom"
    # No per-error web URI — dispatch is on `cause_class`, asserted above.
    assert body["type"] == "about:blank"


def test_runtime_contract_violation_502(make_runnable, fd):
    """A NON-API runtime ContractViolation — a handler body returning an UNDECLARED output key
    (R-handler-001 return-contract, UNDECLARED_OUTPUT_KEY, surfacing mid-dispatch) → 502. This
    is the `_status_for` else-branch: any runtime ContractViolation that is NOT the API-boundary
    API_INPUTS_ENFORCEMENT is HTTP-transport territory (the "handler body is upstream" rationale,
    the same R-error-channel-005 gives SVE). The API-boundary CV→400 arm is tested by
    test_missing_declared_input_contract_violation_400; this is the previously-untested 502 arm.
    RED if `_status_for` stops mapping a non-API ContractViolation to 502."""
    runnable = make_runnable(
        module_name="srv_extra_mod", fn_name="extra",
        src="def extra(*, text):\n    return {'out': text, 'surprise': 'x'}\n",
        pipeline_name="srv.extra",
        reads=(fd("text"),), outputs=(fd("out"),), inputs=(fd("text"),),
    )
    client = TestClient(create_app({runnable.pipeline_name: runnable}))
    resp = client.post("/runs", json={"pipeline": "srv.extra", "inputs": {"text": "hi"}})
    assert resp.status_code == 502  # the non-API runtime-CV branch (not 400)
    assert resp.headers["content-type"] == PROBLEM_JSON
    body = resp.json()
    assert body["status"] == 502
    assert body["rule_id"] == "R-handler-001"  # a handler-produced structural fault


def test_service_locus_failure_pipeline_failure_502(conjured_registry, module_writer, fd):
    """A SERVICE-locus failure — a bound adapter's invoke() raising → the failure escapes the adapter
    boundary as a PipelineFailure with failure_category="service", which the wire surface pins to 502
    (§ PipelineFailure → RFC 9457 status: not a TimeoutError → not 504; a service locus → not the 500
    handler arm). This was the only PipelineFailure status arm untested (500 handler + 504 timeout were
    covered). RED-on-removal: drop `_status_for`'s `if exc.failure_category == "service": return 502`
    and a service-locus failure falls to the 500 handler arm."""
    adapters = module_writer("srv_svc_adapters", _BOOM_ADAPTER)
    handler_mod = module_writer(
        "srv_svc_mod",
        'def call(*, text, services):\n    return {"out": services.llm.invoke(q=text)["r"]}\n',
    )
    type_name = f"{adapters}.BoomAdapter"
    conjured_registry.add_service_type(
        ServiceTypeDeclaration(name=type_name, identity_schema=(fd("model"),), transport_schema=()),
        toml_path="st.toml",
    )
    conjured_registry.add_handler(
        f"{handler_mod}.call",
        ServiceDeclaration(
            reads=(fd("text"),), output_schema=(fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="srv.svc_boom"),
        nodes=(HandlerNode(name=f"{handler_mod}.call"),),
        service_bindings=(ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),),
        inputs=(fd("text"),), outputs=(fd("out"),),
    )
    runnable = load_test_pipeline(pipeline, conjured_registry)
    client = TestClient(create_app({runnable.pipeline_name: runnable}))
    resp = client.post("/runs", json={"pipeline": "srv.svc_boom", "inputs": {"text": "hi"}})
    assert resp.status_code == 502
    assert resp.headers["content-type"] == PROBLEM_JSON
    body = resp.json()
    assert body["status"] == 502
    assert body["failure_category"] == "service"
    assert body["cause_class"] == "RuntimeError"
    assert body["service_binding_name"] == "llm"  # the failing binding, attributed structurally


def test_timeout_pipeline_failure_504(make_runnable, fd):
    """The whole-run budget exceeded → PipelineFailure (engine locus, TimeoutError) → 504
    (§ PipelineFailure → RFC 9457 status: cause_class TimeoutError → 504)."""
    # timeout_ms=0 trips the budget at the first dispatch boundary, before the body runs.
    runnable = make_runnable(
        module_name="srv_slow_mod", fn_name="slow",
        src="def slow(*, text):\n    return {'out': text}\n",
        pipeline_name="srv.slow",
        reads=(fd("text"),), outputs=(fd("out"),), inputs=(fd("text"),),
    )
    client = TestClient(create_app({runnable.pipeline_name: runnable}))
    resp = client.post(
        "/runs", json={"pipeline": "srv.slow", "inputs": {"text": "hi"}, "timeout_ms": 0}
    )
    assert resp.status_code == 504
    body = resp.json()
    assert body["failure_category"] == "engine"
    assert body["cause_class"] == "TimeoutError"
    assert "service_binding_name" not in body  # null for the engine locus → omitted


def test_unhandled_exception_500_is_problem_json(echo_app, echo_runnable, monkeypatch):
    """RED-on-removal for the catch-all handler (SERVER-3): R-error-channel-005 is
    categorical — EVERY HTTP error response carries application/problem+json, the
    defect-path 500 included (without the handler Starlette's ServerErrorMiddleware
    serves text/plain). The defect is forced at the one seam a non-engine-class
    exception can escape through."""
    import conjured.server.app as app_mod

    async def boom(*args, **kwargs):
        raise RuntimeError("engine defect (not an error-channel class)")

    monkeypatch.setattr(app_mod, "run_in_threadpool", boom)
    client = TestClient(echo_app, raise_server_exceptions=False)
    response = client.post(
        "/runs", json={"pipeline": echo_runnable.pipeline_name, "inputs": {"text": "x"}}
    )
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "about:blank" and body["status"] == 500
    # Opaque on the wire: the defect's message stays in the server log, not the body.
    assert "engine defect" not in body["detail"]


def test_group_instance_carries_no_per_member_line_fragment():
    """RED-on-removal for SERVER-4: the CVGroup envelope's `instance` is the SHARED
    compose locus only (file path / composition ref); a member's #L<line> fragment is a
    per-member locus riding inside its own violations entry — never on the group."""
    from conjured.errors import Check, ContractViolation, ContractViolationGroup
    from conjured.server.problem_details import to_problem_details

    first = ContractViolation(
        check=Check.CLOSED_GRAMMAR, rule_id="R-handler-006",
        expected="a", actual="b", file_path="handlers/x.toml", line_number=42,
    )
    second = ContractViolation(
        check=Check.SECTION_PRESENCE, rule_id="R-handler-006",
        expected="c", actual="d", file_path="handlers/x.toml",
    )
    envelope = to_problem_details(ContractViolationGroup((first, second)), 400)
    assert envelope["instance"] == "handlers/x.toml"          # no #L42 on the group
    assert envelope["violations"][0]["instance"] == "handlers/x.toml#L42"  # member keeps it
