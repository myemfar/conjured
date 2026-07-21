"""Direct handler access — the boundary-exercise predicate, enforced structurally.

The contract: ``conjured/docs/components/testing/reference.md`` § The boundary-exercise predicate
(R-testing-001). Direct invocation is permitted **if and only if** the handler is a transform that
declares no ``bindings.<name>`` table — such a body is pure computation over its declared ports
(R-handler-004) and a bare call faithfully tests that content computation. Every other handler
reaches its inputs through the engine-constructed dispatch wrapper and MUST be exercised through the
engine runner; a bare call would assert behaviour the composed pipeline never produces.

``get_handler_fn`` realises the predicate as a structural gate: it returns the bare function only for
a bindings-free transform and raises :class:`~conjured.testing.errors.BoundaryViolation` otherwise,
so a test cannot accidentally train the wrong contract by calling a dispatch-bearing handler bare.
"""

from __future__ import annotations

from typing import Callable, cast

from conjured.testing.errors import BoundaryViolation
from conjured.validator.registry import DeclarationRegistry
from conjured.validator.resolve_handler import resolve_handler


def get_handler_fn(
    registry: DeclarationRegistry, qualified_name: str
) -> Callable[..., dict[str, object]]:
    """Return the registered handler's bare kwarg-only function for a **direct call**, gated on the
    boundary-exercise predicate.

    Permitted only when ``qualified_name`` resolves to a transform with no ``bindings.<name>``
    entries; raises :class:`BoundaryViolation` for a service, a hook, a transform that declares
    bindings (its values are engine-delivered per dispatch, so a bare call sees none of them), or any
    name not registered as a handler — which includes a trainable composition, registered by path
    rather than as a handler. The returned function is the real resolved callable (resolution runs: the
    pre-import source audit + the signature check) — calling it bare deliberately skips
    ``output_schema`` validation and event emission, which the predicate treats as not under test for
    a pure transform. Anything the predicate refuses must instead be dispatched (e.g. via
    :func:`conjured.testing.load_test_pipeline` + the engine runner).
    """
    declaration = registry.get_handler(qualified_name)
    if declaration is None:
        if qualified_name in registry.compositions:
            raise BoundaryViolation(
                f"'{qualified_name}' is a composition registered by path, not a handler — the "
                f"boundary-exercise predicate (R-testing-001) has no bare-call form for it. Compile "
                f"it (load_test_pipeline) and dispatch through the engine runner; do NOT add_handler "
                f"it (a composition registers by path, via add_composition)."
            )
        raise BoundaryViolation(
            f"no handler '{qualified_name}' is registered; cannot resolve a function to call. If this "
            f"names a trainable composition it registers by PATH (add_composition) and must be "
            f"dispatched through the engine runner, not called bare; otherwise register the handler "
            f"with registry.add_handler(...) first."
        )
    if declaration.kind != "transform":
        raise BoundaryViolation(
            f"'{qualified_name}' is a {declaration.kind}, not a transform — the boundary-exercise "
            f"predicate (R-testing-001) forbids a direct call. A {declaration.kind} reaches its "
            f"inputs through the engine-constructed dispatch wrapper; dispatch it through the engine "
            f"runner instead."
        )
    if declaration.bindings:
        raise BoundaryViolation(
            f"'{qualified_name}' is a transform but declares {len(declaration.bindings)} "
            f"bindings.<name> entr{'y' if len(declaration.bindings) == 1 else 'ies'}; their values "
            f"are delivered per dispatch by the engine, so a bare call would see none of them. "
            f"Dispatch it through the engine runner instead of calling it directly."
        )
    toml_path = registry.get_handler_path(qualified_name) or "<test>"
    entry = resolve_handler(qualified_name, declaration, toml_path=toml_path)
    # cast: the predicate above admitted only a transform, whose bare function returns the
    # kwarg dict; entry.callable's static type carries the hook kinds' None return.
    return cast("Callable[..., dict[str, object]]", entry.callable)
