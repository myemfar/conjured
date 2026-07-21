"""``conjured.adapters`` — the Service-type adapters component.

The C4 responsibility is owned at ``conjured/docs/architecture/components.md``
§ Service-type adapters (cited, not quoted — a verbatim quote goes stale silently):
the per-service-type translation layer between handler-declared channel types and
backend-specific structured-output APIs; every ``services.<name>.invoke(...)`` call
routes through the bound service-type's adapter, and the adapter is the
**event-capture seam** where ``service_invocation`` events originate. The SEAM is
engine-internal; concrete vendor implementations are packages resolved through it.

An adapter is a **class** by construction; the engine constructs one instance per
composition at compose time. Its ``invoke()`` accepts the closed dispatch-kwargs +
the service-type's ``[config_schema]`` kwargs + a ``**transport_extra`` collector
(``conjured/docs/components/service-type/reference.md`` § Closed dispatch-kwargs).
Adapter modules are AST-audited for the vector-7 seal (no above-instance-scope
mutable state — ``conjured/docs/architecture/trust-model.md``).

**Build state — the wire-form rendering floor.** :mod:`conjured.adapters.wire` renders
the declared output-port shape into the canonical strict JSON Schema constraint (the
literal-equal artifact's wire rendering; the compose-time caveat fires there) and the
per-dispatch reads projection into wire text; :mod:`conjured.adapters.gbnf` projects the
canonical constraint into a GBNF grammar (the llama.cpp wire form). The two native
trainable-backend adapters consuming this floor live in the native library
(``conjured.lib.openai_compatible_trainable`` / ``conjured.lib.gbnf_trainable``).
Service-type adapter authoring beyond the trainable natives is Phase F.
"""

from conjured.adapters.gbnf import grammar_from_constraint
from conjured.adapters.wire import (
    TrainableWireError,
    render_input_payload,
    render_output_constraint,
)

__all__ = [
    "TrainableWireError",
    "grammar_from_constraint",
    "render_input_payload",
    "render_output_constraint",
]
