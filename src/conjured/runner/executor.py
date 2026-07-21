"""The executor seam — the runner's ordered task-execution boundary.

The kernel walk submits its ordered per-node steps through a **small interface** so a
later parallelism story is a wrapper around the kernel, never a kernel rewrite. Exactly
one implementation ships: :class:`SequentialTaskRunner`, which consumes the ordered
thunks one at a time. The engine exposes no executor configuration surface (the run
entry's signature is pinned — ``run(runnable, inputs, *, pipeline_run_id=None,
timeout_ms=None)``); the seam exists in the code structure, not in the API.

A *task* is a zero-argument callable performing one node step (projection → dispatch →
write-fold) against state the kernel closure owns. Tasks are order-dependent by
construction (declared order is the only sequencing mechanism — pipeline/reference.md
§ Kernel semantics), so the interface contract is strictly ordered consumption; an
implementation that reordered or overlapped dependent tasks would be a different engine
change, arriving with the dependency information it needs.
"""

from __future__ import annotations

from typing import Callable, Iterable, Protocol


class TaskRunner(Protocol):
    """The small interface the kernel walk submits ordered node-thunks through."""

    def run_ordered(self, tasks: Iterable[Callable[[], None]]) -> None:
        """Execute ``tasks`` strictly in iteration order, each to completion before the
        next begins. Exceptions propagate — the kernel's dispatch boundary owns failure
        semantics; the executor never absorbs or retries (R-error-channel-002)."""
        ...  # pragma: no cover - protocol


class SequentialTaskRunner:
    """The only shipped implementation: in-order, in-process, one task at a time."""

    def run_ordered(self, tasks: Iterable[Callable[[], None]]) -> None:
        # guarantees: sequential-dispatch-order
        # guarantees: no-engine-retry
        for task in tasks:
            task()  # exactly once, no retry loop (R-error-channel-002)
