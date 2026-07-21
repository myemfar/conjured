"""Errors the testing library raises.

These are the library's own — distinct from the engine's closed error classes
(``ContractViolation`` / ``SchemaValidationError`` / ``PipelineFailure``), which the harness
re-raises unchanged from a dispatched run. A testing error means the *test* asked for something the
testing discipline forbids (a bare call on a dispatch-bearing handler) or that a fixture is stale —
not that a composition failed.
"""

from __future__ import annotations


class TestingError(Exception):
    """Base for ``conjured.testing`` errors."""


class BoundaryViolation(TestingError):
    """A direct call was requested on a handler the boundary-exercise predicate forbids calling
    bare (``conjured/docs/components/testing/reference.md`` § The boundary-exercise predicate /
    R-testing-001): anything that is not a bindings-free transform must be dispatched through the
    engine runner. Raised structurally so the wrong contract is never trained by a bare call."""


class StaleFixtureError(TestingError):
    """A harvested fixture's recorded ``pipeline_hash`` no longer matches the current composition
    (``testing/reference.md`` § Fixtures are harvested … and hash-gated): the fixture predates the
    composition and must be re-harvested."""


class AmbiguousServiceCapture(TestingError):
    """More than one ``service_invocation`` event was captured at a single ``handler_position``. A
    service node makes **exactly one external call per dispatch** (``handler-kinds.md`` § Service —
    semantic retry is forbidden precisely so one captured invocation stands for the dispatch), so
    two at one position is never steady state: it signals a buried multi-call (the consumer-side
    no-silent-fallbacks class, R-handler-002) or an engine capture bug. Raised so a verification or
    harvest helper can never silently collapse the ambiguity to one arbitrary event."""
