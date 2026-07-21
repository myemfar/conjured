"""Verified fakes — the test-double base for compose-time twin substitution.

The contract: ``conjured/docs/components/testing/reference.md`` § Verified fakes / § Test-double
substitution. Substitution happens at the **adapter seam** via compose-time twin substitution (swap
the binding ``type`` to the fake service-type's qualified name) — never runtime patching. A shipped
fake **MUST fail wherever the runtime would**: it validates its ``invoke(...)`` input against the
service-type's declared input shape and rejects what the real backend would reject, so a green test
against it carries weight. Output validation is **not symmetric**: when the fake stands in for a
**trainable** node's backend (``conjured.ir.composition``) the engine validates its return against
``trainable.output_schema`` (the literal-equal rule, R-handler-005), so a wrong-shaped response is
caught by dispatch. But a fake at a **plain service binding** has its return consumed by the calling
handler and is NEVER schema-validated by the engine (``_BoundService`` returns the adapter response
raw) — there, :meth:`respond` is the sole guarantor of the output shape.

This base supplies the recording, the request-rejection hook, and the canned-response hook. It does
**not** define ``invoke``: a fake reached through real twin-substitution resolution is signature-
checked (keyword-only, exactly the four closed dispatch kwargs plus one kwarg per the fake service-
type's ``[config_schema]`` field, plus a ``**`` collector), so the exact ``invoke`` signature is
service-type-specific and the concrete fake declares it, delegating its body to :meth:`_invoke`. A
trainable fake additionally declares the property-contract attributes the trainable-backend gate
verifies against the resolved class (``training_artifact_contract`` / ``reserved_wire_keys``) —
certification is structural: native-by-construction via the native table, or a fresh audit
stamp under ``audit_enforcement`` (handler/reference.md § Trainable backends). Binding a trainable fake at a trainable node preserves the ``handler_enter``
/ ``handler_exit`` capture path unchanged (capture follows composition kind, not the double).
"""

from __future__ import annotations

from typing import Mapping

from conjured.errors import snapshot_copy


class VerifiedFake:
    """Base for a verified fake adapter. Concrete fakes subclass it, override
    :meth:`validate_input` and :meth:`respond`, and declare an ``invoke`` whose keyword-only
    parameters match the fake service-type's declared shape, delegating to :meth:`_invoke`::

        class FakeStructuredOutput(VerifiedFake):
            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, max_tokens, **transport_extra):
                return self._invoke(
                    input_payload=input_payload, service_name=service_name,
                    caller_qualified_name=caller_qualified_name, caller_position=caller_position,
                    temperature=temperature, max_tokens=max_tokens, **transport_extra,
                )
            def validate_input(self, input_payload):
                if "messages" not in input_payload:
                    raise ValueError("real backend rejects a request with no messages")
            def respond(self, input_payload):
                return {"reply": "ahoy"}
    """

    def __init__(self, **identity: object) -> None:
        # Compose-fixed identity values (the B2 lifecycle: one instance per composition). Recorded
        # so a test can assert what the fake was constructed with.
        self.identity: dict[str, object] = dict(identity)
        # Every invoke's closed dispatch kwargs + config/transport extras, in call order — the
        # "was-called-with" record a consumer-side test asserts against (state, not a mock verdict).
        self.calls: list[dict[str, object]] = []

    def validate_input(self, input_payload: Mapping[str, object]) -> None:
        """Reject what the real backend would reject. **Override this** — a fake that validates
        nothing is not *verified* (a green test against it proves nothing), so there is no silent
        accept-everything default: like :meth:`respond`, the base raises until overridden, and the
        override IS the opt-in. Raise on a payload the real backend's request validation would
        refuse. A backend that genuinely rejects no request shape is expressed by an EXPLICIT
        override whose body is ``return None`` — a deliberate assertion, not an omission."""
        raise NotImplementedError(
            f"{type(self).__name__}.validate_input must reject what the real backend rejects; "
            "override it (use an explicit `return None` body only if the backend rejects nothing)."
        )

    def respond(self, input_payload: Mapping[str, object]) -> dict[str, object]:
        """Return the canned, shape-matching output for ``input_payload``. Override this. At a
        trainable node the engine then validates the return against ``trainable.output_schema``
        (R-handler-005), so a wrong-shaped response still fails the run; at a plain service binding
        nothing downstream re-checks the shape, so a service fake's ``respond`` must itself return
        exactly what the real backend would."""
        raise NotImplementedError(
            f"{type(self).__name__}.respond must return a shape-matching canned output; override it."
        )

    def _invoke(
        self,
        *,
        input_payload: Mapping[str, object],
        service_name: str,
        caller_qualified_name: str,
        caller_position: int,
        **extra: object,
    ) -> dict[str, object]:
        """The shared ``invoke`` body: record the call, fail where the runtime would
        (:meth:`validate_input`), then return the canned output (:meth:`respond`). A concrete fake's
        exact-signature ``invoke`` delegates here."""
        # The was-called-with record is asserted by value, so deep-snapshot it (the engine's tolerant
        # snapshot_copy, mirroring its own posture) — a shallow dict() would share nested mutables with
        # the live payload and let the record change after the fact. The hooks below see the LIVE
        # payload; only the recorded snapshot is copied.
        self.calls.append(
            {
                "input_payload": snapshot_copy(input_payload),
                "service_name": service_name,
                "caller_qualified_name": caller_qualified_name,
                "caller_position": caller_position,
                "extra": snapshot_copy(extra),
            }
        )
        self.validate_input(input_payload)
        return self.respond(input_payload)
