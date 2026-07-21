"""``conjured.testing`` — the consumer testing library.

The harness that realises the runtime-testing discipline (``conjured/docs/components/testing/``): it
drives the **real** engine runner and reads the canonical event stream — never a bare call on a
dispatch-bearing handler, never a mock of engine internals. The public surface:

- :func:`get_handler_fn` — a bare transform function for a direct call, gated on the
  boundary-exercise predicate.
- :func:`capture_events` / :func:`run_and_capture` / :func:`inspect_state` (+ :class:`NodeState`) —
  compositional verification through the event stream.
- :func:`load_test_pipeline` / :func:`load_test_deployment` — compile + assemble a composition into a
  ``Runnable``.
- :class:`VerifiedFake` — the test-double base for compose-time twin substitution at the adapter seam.
- The harness error classes — :class:`TestingError` (the common base a consumer ``except`` clause
  catches to mean "any harness signal") and its concrete signals :class:`BoundaryViolation` /
  :class:`StaleFixtureError` / :class:`AmbiguousServiceCapture` — the harness's own, never members
  of the runtime error channel's closed set (``components/testing/api.md``).
- :func:`harvest` / :func:`write_fixtures` / :func:`load_fixture` / :func:`load_fixture_unchecked`
  (+ :class:`SeamFixture`) — harvested, hash-gated contract fixtures.
- The ``pytest11`` plugin (``conjured.testing.plugin``) — registry + import-isolation fixtures.
"""

from __future__ import annotations

from conjured.testing.errors import (
    AmbiguousServiceCapture,
    BoundaryViolation,
    StaleFixtureError,
    TestingError,
)
from conjured.testing.events import (
    NodeState,
    capture_events,
    inspect_state,
    run_and_capture,
)
from conjured.testing.fakes import VerifiedFake
from conjured.testing.fixtures import (
    SeamFixture,
    harvest,
    load_fixture,
    load_fixture_unchecked,
    write_fixtures,
)
from conjured.testing.handlers import get_handler_fn
from conjured.testing.load import load_test_deployment, load_test_pipeline

__all__ = [
    "AmbiguousServiceCapture",
    "BoundaryViolation",
    "NodeState",
    "SeamFixture",
    "StaleFixtureError",
    "TestingError",
    "VerifiedFake",
    "capture_events",
    "get_handler_fn",
    "harvest",
    "inspect_state",
    "load_fixture",
    "load_fixture_unchecked",
    "load_test_deployment",
    "load_test_pipeline",
    "run_and_capture",
    "write_fixtures",
]
