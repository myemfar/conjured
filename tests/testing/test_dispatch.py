"""A VerifiedFake exercised through REAL dispatch — compose-time twin substitution at the adapter
seam, resolved and run by the engine (R-testing-001/002/004). The bare-call test in test_fakes.py
proves the base's plumbing; this proves the fake produces a real, resolvable adapter whose
validate_input fires on the sanctioned verification path, and that the service payloads are captured.
"""

from __future__ import annotations

import pytest

from conjured.errors import PipelineFailure
from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.common import ServiceBindingDecl, ServiceBindingSupply
from conjured.ir.handler import ServiceDeclaration
from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.runner.run import run
from conjured.testing import (
    AmbiguousServiceCapture,
    harvest,
    inspect_state,
    load_test_pipeline,
    run_and_capture,
)

_DOUBLE_INVOKE_BODY = (
    "def call(*, text, services):\n"
    '    a = services.llm.invoke(q=text)["r"]\n'
    "    services.llm.invoke(q=text)  # a SECOND external call at one dispatch — buried multi-call\n"
    '    return {"out": a}\n'
)


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


# A fake adapter that subclasses VerifiedFake at the adapter seam — its qualified name is the
# fake service-type's `type`, so twin substitution routes resolution here.
_FAKE_ADAPTER = """
from conjured.testing import VerifiedFake

class FakeEcho(VerifiedFake):
    def invoke(self, *, input_payload, service_name, caller_qualified_name, caller_position,
               **transport_extra):
        return self._invoke(
            input_payload=input_payload, service_name=service_name,
            caller_qualified_name=caller_qualified_name, caller_position=caller_position,
            **transport_extra,
        )

    def validate_input(self, input_payload):
        if "q" not in input_payload:
            raise ValueError("the real backend rejects a request with no 'q'")

    def respond(self, input_payload):
        return {"r": input_payload["q"].upper()}
"""


def _service_pipeline(conjured_registry, module_writer, *, body):
    module = module_writer("fd_svc_mod", body)
    adapters = module_writer("fd_svc_adapters", _FAKE_ADAPTER)
    type_name = f"{adapters}.FakeEcho"
    conjured_registry.add_service_type(
        ServiceTypeDeclaration(name=type_name, identity_schema=(_fd("model"),), transport_schema=()),
        toml_path="st.toml",
    )
    conjured_registry.add_handler(
        f"{module}.call",
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="fd.svc"),
        nodes=(HandlerNode(name=f"{module}.call"),),
        service_bindings=(ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),),
        inputs=(_fd("text"),), outputs=(_fd("out"),),
    )
    return load_test_pipeline(pipeline, conjured_registry)


def test_verified_fake_dispatches_and_captures_service_payloads(conjured_registry, module_writer):
    runnable = _service_pipeline(
        conjured_registry, module_writer,
        body='def call(*, text, services):\n    return {"out": services.llm.invoke(q=text)["r"]}\n',
    )
    result, events = run_and_capture(runnable, {"text": "hi"})
    assert dict(result.state) == {"out": "HI"}
    state = inspect_state(events, 0)
    assert state.node_kind == "service"
    assert state.service_input == {"q": "hi"}      # captured at the adapter boundary
    assert state.service_output == {"r": "HI"}
    # harvest carries the service payloads onto the SeamFixture too
    fixture = harvest(runnable, {"text": "hi"})[0]
    assert fixture.service_input == {"q": "hi"}
    assert fixture.service_output == {"r": "HI"}


def test_verified_fake_rejection_surfaces_on_the_real_path(conjured_registry, module_writer):
    # The body submits a payload the fake's validate_input rejects; the rejection surfaces as the
    # engine error class through real dispatch — RED-on-removal: drop validate_input and the run
    # succeeds, proving nothing.
    runnable = _service_pipeline(
        conjured_registry, module_writer,
        body='def call(*, text, services):\n    return {"out": services.llm.invoke(wrong=text)["r"]}\n',
    )
    with pytest.raises(PipelineFailure):
        run(runnable, {"text": "hi"})


def test_inspect_state_fails_loud_on_ambiguous_service_capture(conjured_registry, module_writer):
    # A service body that calls invoke() TWICE emits two service_invocation events at one
    # handler_position — a buried multi-call the engine does not structurally forbid (canon:
    # handler-kinds § Service — exactly one external call per dispatch, review-enforced). inspect_state
    # MUST fail loud rather than silently return the first. RED-on-removal: revert _service_at to a
    # first-match (_one) and inspect_state laundered the ambiguity into a clean single-invocation record.
    runnable = _service_pipeline(conjured_registry, module_writer, body=_DOUBLE_INVOKE_BODY)
    _result, events = run_and_capture(runnable, {"text": "hi"})  # the run itself succeeds
    with pytest.raises(AmbiguousServiceCapture):
        inspect_state(events, 0)


def test_harvest_fails_loud_on_ambiguous_service_capture(conjured_registry, module_writer):
    # harvest shares the same guard (via _service_at): a double-invoke position raises rather than
    # silently harvesting one arbitrary service payload onto the SeamFixture. RED-on-removal as above.
    runnable = _service_pipeline(conjured_registry, module_writer, body=_DOUBLE_INVOKE_BODY)
    with pytest.raises(AmbiguousServiceCapture):
        harvest(runnable, {"text": "hi"})


# verifies: harvest-halt-propagates
def test_harvest_propagates_a_halting_run_with_no_partial_fixtures(conjured_registry, module_writer):
    # A run whose body raises HALTS with the engine error class; harvest propagates it and produces NO
    # partial fixture list (harvest builds fixtures only AFTER run_and_capture returns, wrapping it in
    # no try/except — testing/reference.md § harvest: "A run that halts propagates its engine error
    # class — there is no partial harvest"). RED-on-removal: a swallowing try/except around the run
    # would let harvest return a truncated fixture list instead of raising.
    runnable = _service_pipeline(
        conjured_registry, module_writer,
        body='def call(*, text, services):\n    raise RuntimeError("body halts before any service call")\n',
    )
    with pytest.raises(PipelineFailure):
        harvest(runnable, {"text": "hi"})
