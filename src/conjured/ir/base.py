"""The shared base for every IR model.

The IR is the engine's **canonical internal representation** — the privileged form
every authoring dialect resolves into (``conjured/docs/explanation/overview.md``
§ Pydantic as the canonical representation). Two policies are declared here, once,
for the whole IR rather than restated per model (structural over disciplinary —
``conjured/docs/architecture/trust-model.md``):

- ``frozen=True`` — an IR instance is immutable once constructed. A parsed/compiled
  declaration is a record of what was declared; nothing downstream mutates it. (The
  runtime-record immutability canon names elsewhere — ``HandlerEntry``, ``RunResult`` —
  is the same posture; those records belong to later phases.)
- ``extra="forbid"`` — an unknown field is unrepresentable. This is the IR-boundary
  floor under the closed-shape-grammar rules (R-handler-006, R-service-type-001,
  R-deployment-001): a declaration carrying an element the grammar never declared
  cannot even be constructed. The *diagnostic* (a ``ContractViolation`` with a
  remediation hint) is the Phase-1 validator's job; the structural impossibility is
  the IR's.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class IRModel(BaseModel):
    """Frozen, closed-shape base for every Conjured IR model."""

    model_config = ConfigDict(frozen=True, extra="forbid")


#: Sentinel distinguishing "no ship-time default declared" from "a default of ``None``".
#: A declaration MAY carry ``default = <value>`` (a value, possibly ``None``); the *absence*
#: of a default is what makes the declaration supply-required. ``None`` is a legitimate
#: declared default value, so it cannot double as "no default" — hence the sentinel. Single
#: home here (the import-graph root) because two declaration surfaces carry ship-time
#: defaults: ``bindings.<name>`` (``ir/common.py`` ``SchemaBinding``) and a service-type's
#: ``[config_schema]`` fields (``ir/channel_types.py`` ``FieldDecl``).
class _NoDefault:
    """Singleton type for the no-default sentinel (frozen-friendly, identity-comparable)."""

    _instance: "_NoDefault | None" = None

    def __new__(cls) -> "_NoDefault":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "NO_DEFAULT"


NO_DEFAULT = _NoDefault()
