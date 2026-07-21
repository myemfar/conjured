"""VerifiedFake — the test-double base must fail wherever the runtime would (reference.md
§ Verified fakes; R-testing-008 is the failing-case-test teeth).

The validate_input rejection test is RED-on-removal: delete the override (or the base's call to it)
and the bad-payload case passes, proving nothing — which is exactly what a non-verified fake does.
"""

from __future__ import annotations

import pytest

from conjured.testing import VerifiedFake


class _FakeLLM(VerifiedFake):
    """A verified fake whose invoke matches a structured-output service-type's declared shape
    (config field ``temperature``); rejects a request the real backend would reject."""

    def invoke(self, *, input_payload, service_name, caller_qualified_name, caller_position,
               temperature, **transport_extra):
        return self._invoke(
            input_payload=input_payload, service_name=service_name,
            caller_qualified_name=caller_qualified_name, caller_position=caller_position,
            temperature=temperature, **transport_extra,
        )

    def validate_input(self, input_payload):
        if "messages" not in input_payload:
            raise ValueError("the real backend rejects a request with no messages")

    def respond(self, input_payload):
        return {"reply": "ahoy"}


def test_records_and_responds():
    fake = _FakeLLM(model="qwen")
    out = fake.invoke(
        input_payload={"messages": [{"role": "user"}]}, service_name="llm",
        caller_qualified_name="acme.respond", caller_position=2, temperature=0.7,
        endpoint="https://llm/v1",
    )
    assert out == {"reply": "ahoy"}
    assert fake.identity == {"model": "qwen"}
    call = fake.calls[0]
    assert call["input_payload"] == {"messages": [{"role": "user"}]}
    assert call["service_name"] == "llm"
    assert call["caller_position"] == 2
    assert call["extra"]["temperature"] == 0.7
    assert call["extra"]["endpoint"] == "https://llm/v1"


def test_fails_where_the_runtime_would():
    fake = _FakeLLM(model="qwen")
    with pytest.raises(ValueError):
        fake.invoke(
            input_payload={"no_messages": True}, service_name="llm",
            caller_qualified_name="acme.respond", caller_position=2, temperature=0.7,
        )
    # The rejected call is still recorded (the rejection happens after _record).
    assert fake.calls[-1]["input_payload"] == {"no_messages": True}


def test_recorded_call_is_deep_snapshotted():
    # RED-on-removal: with a shallow dict() snapshot the nested list/dict is shared with the live
    # payload, so this mutation would corrupt the was-called-with record the library advertises as
    # "asserted by value". The deep snapshot keeps it stable.
    fake = _FakeLLM(model="qwen")
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    fake.invoke(
        input_payload=payload, service_name="llm", caller_qualified_name="acme.respond",
        caller_position=0, temperature=0.7,
    )
    payload["messages"][0]["content"] = "TAMPERED"
    payload["messages"].append({"role": "system"})
    assert fake.calls[0]["input_payload"] == {"messages": [{"role": "user", "content": "hi"}]}


def test_respond_must_be_overridden():
    # Overrides validate_input (the opt-in that says "this backend rejects nothing") but NOT respond,
    # so _invoke runs past validation and hits respond's NotImplementedError — the thing under test.
    class _NoRespond(VerifiedFake):
        def invoke(self, *, input_payload, service_name, caller_qualified_name, caller_position, **x):
            return self._invoke(
                input_payload=input_payload, service_name=service_name,
                caller_qualified_name=caller_qualified_name, caller_position=caller_position, **x,
            )

        def validate_input(self, input_payload):
            return None  # explicit "rejects nothing" opt-in

    with pytest.raises(NotImplementedError):
        _NoRespond().invoke(input_payload={}, service_name="s", caller_qualified_name="a.b", caller_position=0)


def test_validate_input_must_be_overridden():
    # A fake that overrides respond but NOT validate_input raises NotImplementedError from
    # validate_input FIRST — there is no silent accept-everything default (a fake that validates
    # nothing is not verified). RED-on-removal: restore the base's `return None` default and the
    # bad-payload call proceeds to respond and succeeds, proving nothing.
    class _NoValidate(VerifiedFake):
        def invoke(self, *, input_payload, service_name, caller_qualified_name, caller_position, **x):
            return self._invoke(
                input_payload=input_payload, service_name=service_name,
                caller_qualified_name=caller_qualified_name, caller_position=caller_position, **x,
            )

        def respond(self, input_payload):
            return {"reply": "ahoy"}

    with pytest.raises(NotImplementedError):
        _NoValidate().invoke(input_payload={}, service_name="s", caller_qualified_name="a.b", caller_position=0)
