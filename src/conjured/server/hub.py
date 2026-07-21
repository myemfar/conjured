"""The event hub — bridge the runner's synchronous ``logging`` event emits onto the
async SSE streams.

The engine emits :data:`~conjured.events.CanonicalEvent` objects on the process-global
``logging`` channel ``conjured.events.runner`` and ships **no** ``logging.Handler``
(producer/consumer; ``conjured.events`` module docstring). The server attaches **one**
persistent consumer handler — this :class:`EventHub` — for the app's lifetime, rather
than adding/removing a handler per SSE request: a per-request add/remove mutates the
global logger's handler list concurrently with the emits happening on a threadpool
thread (``POST /runs`` runs the synchronous runner under ``run_in_threadpool``), which
races ``logging``'s unguarded ``callHandlers`` iteration. One stable handler + an
internal per-run subscriber registry sidesteps that entirely.

Bridge mechanics. The hub's :meth:`emit` runs on whichever thread emitted — the
threadpool thread for a dispatched run. It looks up the run-scoped subscribers for the
event's ``pipeline_run_id`` and hands each the event via
``loop.call_soon_threadsafe(queue.put_nowait, event)`` — the thread-safe handoff onto the
event loop the SSE generator awaits on. The per-run filter is ``pipeline_run_id``, so
concurrent runs/streams never cross (R-server-002: the stream is filtered to one
``pipeline_run_id``). **Compose-time events carry no ``pipeline_run_id`` and are out of a
run-scoped stream's scope** (R-server-002) — the hub skips them.

Isolation. A consumer-side delivery failure to one subscriber never propagates into the
run and never blocks the other subscribers (the producer/consumer wall — the same
discipline ``conjured.events.emit`` enforces). Each handoff is individually guarded.

The registry + handoff mechanics (``subscribe`` / ``unsubscribe`` / ``publish``) are
run-scoped and **payload-agnostic** — nothing in them reads the canonical event classes.
The server reuses them for the **token stream** (server reference § The token stream): a
second ``EventHub`` instance, **never attached to logging**, carries a streamed run's
provisional token fragments — the run trigger publishes each fragment (and the terminal
end marker) directly via :meth:`publish`; token deltas never ride the canonical event
channel (the closed enum is the training-log substrate).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field

from conjured.events import EVENT_LOGGER_NAME, attach_consumer


@dataclass(frozen=True, slots=True)
class _Subscription:
    """One open SSE stream's handoff target: the loop it runs on and the queue its
    generator awaits. Identity is the object itself (a fresh queue per subscription), so a
    run with several concurrent subscribers holds several distinct entries."""

    loop: asyncio.AbstractEventLoop
    queue: "asyncio.Queue[object]"


@dataclass(eq=False)
class EventHub(logging.Handler):
    """A ``logging.Handler`` fan-out: route each run-scoped canonical event to the SSE
    streams subscribed to its ``pipeline_run_id``.

    Attached once (``attach()``) at app startup and detached (``detach()``) at shutdown.
    The subscriber registry is guarded by its own lock — ``logging`` serializes
    :meth:`emit` against itself per handler, but ``subscribe`` / ``unsubscribe`` run on
    the event-loop thread, so the registry needs its own mutual exclusion."""

    _subscribers: dict[str, list[_Subscription]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        # logging.Handler.__init__ sets up the handler's own lock + level; a dataclass
        # subclass must call it explicitly (the generated __init__ does not).
        #
        # The `eq=False` on the decorator is load-bearing, not cosmetic. A logging.Handler
        # is an identity object — logging's own machinery (handler lists, addHandler
        # de-duplication, and on POSIX the at-fork WeakSet that Handler.__init__ registers
        # every handler into via os.register_at_fork) assumes identity `__eq__`/`__hash__`.
        # A default `@dataclass` generates a field-based `__eq__`, which sets
        # `__hash__ = None` → an unhashable instance. That is invisible on Windows (no
        # os.register_at_fork, so the WeakSet path never runs) but crashes Handler.__init__
        # on Linux/macOS the moment a handler is constructed. `eq=False` keeps object
        # identity `__eq__`/`__hash__` from the base; value-equality over the hub's fields
        # (a subscriber dict + a lock) was never a meaningful operation.
        logging.Handler.__init__(self)

    # -- lifecycle -----------------------------------------------------------------
    def attach(self) -> None:
        """Attach to the canonical event channel through the channel owner's long-lived
        attachment surface (``conjured.events.attach_consumer``): handler on, channel
        delivering INFO, ``propagate`` untouched — the hub is a leaf consumer; the
        engine's own warnings on the parent ``conjured.events`` logger are a separate
        surface."""
        attach_consumer(self)

    def detach(self) -> None:
        logging.getLogger(EVENT_LOGGER_NAME).removeHandler(self)

    # -- subscription (event-loop thread) ------------------------------------------
    def subscribe(self, pipeline_run_id: str) -> "asyncio.Queue[object]":
        """Register a stream for ``pipeline_run_id`` and return the queue its generator
        awaits. Called from the SSE endpoint **before** the streaming response starts, so
        a stream opened before its run is triggered still receives the run's events
        (the reference's open-stream-then-POST correlation flow). The queue is unbounded,
        so an event that arrives before the generator awaits is buffered, never dropped."""
        queue: "asyncio.Queue[object]" = asyncio.Queue()
        sub = _Subscription(loop=asyncio.get_running_loop(), queue=queue)
        with self._lock:
            self._subscribers.setdefault(pipeline_run_id, []).append(sub)
        return queue

    def unsubscribe(self, pipeline_run_id: str, queue: "asyncio.Queue[object]") -> None:
        """Remove the stream's subscription (on terminal frame, client disconnect, or
        stream timeout). Idempotent and never raises for an already-removed entry."""
        with self._lock:
            subs = self._subscribers.get(pipeline_run_id)
            if not subs:
                return
            remaining = [s for s in subs if s.queue is not queue]
            if remaining:
                self._subscribers[pipeline_run_id] = remaining
            else:
                del self._subscribers[pipeline_run_id]

    # -- delivery (emitting thread, e.g. the threadpool runner thread) -------------
    def emit(self, record: logging.LogRecord) -> None:
        """Route one canonical event to its run's subscribers. The event rides as
        ``record.msg`` (the engine never string-formats it). A non-event record, or a
        compose-time event (no ``pipeline_run_id``), is ignored — only run-scoped events
        enter a run-scoped stream."""
        event = record.msg
        run_id = getattr(event, "pipeline_run_id", None)
        if not isinstance(run_id, str):
            return  # not a run-scoped canonical event
        self.publish(run_id, event)

    def publish(self, run_id: str, item: object) -> None:
        """Route one item to ``run_id``'s subscribers — the payload-agnostic delivery
        core :meth:`emit` adapts the logging channel onto. Thread-safe: callable from
        any thread (the threadpool runner thread for both the canonical events and a
        streamed run's token fragments); each subscriber handoff is the same
        ``call_soon_threadsafe`` bridge, individually guarded."""
        with self._lock:
            subs = list(self._subscribers.get(run_id, ()))
        for sub in subs:
            self._deliver(sub, item)

    @staticmethod
    def _deliver(sub: _Subscription, event: object) -> None:
        """Hand one event to one subscriber's loop, guarded: a torn-down loop (the stream
        is closing) must never propagate into the emitting run (the producer/consumer
        wall). The caught ``RuntimeError`` is the closed event loop rejecting
        ``call_soon_threadsafe`` — ``asyncio.Queue`` has no closed state, and
        ``put_nowait`` runs as a deferred callback outside this ``try``."""
        # guarantees: hub-delivery-isolated
        try:
            sub.loop.call_soon_threadsafe(sub.queue.put_nowait, event)
        except RuntimeError:
            # The loop is closed/closing — the subscription is being torn down; drop.
            pass
