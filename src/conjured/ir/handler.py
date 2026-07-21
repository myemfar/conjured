"""Handler declaration IR — the bare-function kinds (transform / service / hook).

Owned by ``conjured/docs/components/handler/reference.md`` (§ Handler-TOML grammar,
§ Derived rules R-handler-006 closed grammar) and the three
``kind-schemas/{transform,service,hook}.schema.toml``.

**Modeled as three per-kind models behind a discriminated union, not one model with
optional sections.** This makes R-handler-006's *forbidden* sections structurally
unrepresentable rather than rule-checked (structural over disciplinary): a
``HookDeclaration`` has no ``output_schema`` attribute at all (a hook returns ``None``);
a ``TransformDeclaration`` has no ``service_bindings`` or ``transport_schema`` (a
transform has no external-call edge; ``transport_schema`` is hook-only); a
``ServiceDeclaration`` has no ``transport_schema``. The remaining per-kind rules are
*cardinality / body-required counts* (service: exactly one binding; hook: 0 or 1;
transform/service ``output_schema`` ≥ 1 field) — those are validation (Phase 1a), not
representable structure, and are deliberately not enforced here.

Section *presence* discipline (the required-empty-allowed vs required-body-required
distinction — whether a section header must textually appear) is a TOML-parse-time
Phase-1a concern; the IR models the declaration's resolved *content*. A defaulted
empty ``reads = ()`` means "no input ports," not "section absent."
"""

from __future__ import annotations

from typing import Annotated, Literal, Mapping, Union

from pydantic import Field

from conjured.ir.base import IRModel
from conjured.ir.channel_types import FieldDecl
from conjured.ir.common import Binding, ServiceBindingDecl


class TransformDeclaration(IRModel):
    """A pure transform — deterministic, no external call. ``service_bindings`` and
    ``transport_schema`` are absent by construction (R-handler-004 transform purity;
    R-handler-006 closed grammar).
    """

    kind: Literal["transform"] = "transform"
    reads: tuple[FieldDecl, ...] = ()  # input ports (required, empty-allowed)
    output_schema: tuple[FieldDecl, ...]  # output ports (required, body-required ≥ 1 — count is Phase 1a)
    bindings: tuple[Binding, ...] = ()  # N ≥ 0 compose-time bindings
    annotations: Mapping[str, object] | None = None  # engine-opaque


class ServiceDeclaration(IRModel):
    """A service handler — exactly one external call per dispatch. Carries its one
    ``service_bindings`` entry (cardinality is Phase 1a). ``transport_schema`` is absent
    by construction (hook-only).
    """

    kind: Literal["service"] = "service"
    reads: tuple[FieldDecl, ...] = ()
    output_schema: tuple[FieldDecl, ...]  # output ports (required, body-required)
    service_bindings: tuple[ServiceBindingDecl, ...]  # exactly one (cardinality Phase 1a)
    bindings: tuple[Binding, ...] = ()
    annotations: Mapping[str, object] | None = None


class HookDeclaration(IRModel):
    """A hook — observes channels, emits externally, returns ``None``. ``output_schema``
    is absent by construction (R-handler-006: a hook has no ``output_schema``; the runner
    has no merge path for a hook return). ``service_bindings`` is 0 entries
    (stdlib-emission) or 1 (backend-SDK-emission) — cardinality is Phase 1a.
    """

    kind: Literal["hook"] = "hook"
    reads: tuple[FieldDecl, ...] = ()
    service_bindings: tuple[ServiceBindingDecl, ...] = ()  # 0 or 1 (cardinality Phase 1a)
    transport_schema: tuple[FieldDecl, ...] = ()  # hook-only (required, empty-allowed)
    bindings: tuple[Binding, ...] = ()
    annotations: Mapping[str, object] | None = None


#: The handler declaration class — a closed discriminated union over the kind tag.
HandlerDeclaration = Annotated[
    Union[TransformDeclaration, ServiceDeclaration, HookDeclaration],
    Field(discriminator="kind"),
]
