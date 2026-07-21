"""``conjured.core`` — engine-declared types and pure utilities exposed to handler
bodies.

This is the **only** engine-internal namespace a handler body may import, per
R-handler-007 (handler import discipline, ``conjured/docs/components/handler/reference.md``
§ Derived rules): "Allowed. ``conjured.core.*`` (engine-declared types and pure
utilities); the Python standard library; library-internal pure technical
utilities." Backend SDKs, service-locator/registry modules, dynamic-import
mechanisms, and engine internals beyond declared interfaces are forbidden to
handler bodies.

**Build state — reserved stub.** No handler-facing types are exposed yet (Phase 0
authors no handlers). The namespace is reserved here so the R-handler-007 import
surface has a concrete home; it is populated as engine-exposed types stabilize.
This package is an addition beyond the literal C4-component list of engine components.
"""
