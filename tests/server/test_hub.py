"""EventHub identity semantics — the RED-on-removal seal for a hashable handler.

``EventHub`` is a ``logging.Handler`` subclass, and logging treats every handler as an
**identity object**: handler lists, ``addHandler`` de-duplication, and — on POSIX only —
the at-fork WeakSet that ``logging.Handler.__init__`` registers each handler into (via
``os.register_at_fork``) all assume identity ``__eq__`` / ``__hash__``. A default
``@dataclass`` would generate a field-based ``__eq__`` and thereby set ``__hash__ = None``
(an unhashable instance); ``hub.py`` pins ``@dataclass(eq=False)`` to keep the base's
object-identity semantics.

This is the **platform-independent** adversary for that seal. ``hash(EventHub())`` raises
``TypeError`` on *every* platform when the instance is unhashable — the POSIX-only crash
(``Handler.__init__`` → ``createLock`` → ``_register_at_fork_reinit_lock`` adding to a
WeakSet) is just where the unhashability *bites*. Asserting hashability directly is
therefore RED on the pre-fix code everywhere, so the seal never needs a Linux runner to
catch a regression that only fails CI on POSIX.
"""

from __future__ import annotations

import asyncio

from conjured.server.hub import EventHub, _Subscription


def test_event_hub_is_hashable_with_identity_semantics():
    """RED-on-removal seal: an ``EventHub`` instance must be hashable (so
    ``logging.Handler.__init__``'s POSIX at-fork WeakSet registration cannot crash) and
    carry object-*identity* equality/hash (so logging's handler-list and addHandler
    de-duplication behave). RED if the decorator reverts to a default ``@dataclass``:
    the generated field-based ``__eq__`` sets ``__hash__ = None`` and ``hash(h1)`` raises
    ``TypeError`` — on every platform, which is exactly why this bites without a Linux CI leg."""
    h1 = EventHub()
    h2 = EventHub()

    # Hashable — the property whose absence crashes Handler.__init__ on POSIX (WeakSet insert).
    assert isinstance(hash(h1), int)
    assert isinstance(hash(h2), int)

    # Identity equality, NOT dataclass value-equality: two fresh hubs are distinct objects,
    # and each equals only itself. A default @dataclass would make h1 == h2 (both have the
    # same empty-dict + fresh-lock fields under value-equality) — this asserts it does not.
    assert h1 == h1
    assert h2 == h2
    assert h1 != h2

    # The identity contract: equal-by-identity implies equal hash; distinct objects usable
    # as distinct dict/set keys (the handler-collection semantics logging relies on).
    assert hash(h1) == hash(h1)
    assert len({h1, h2}) == 2


# verifies: hub-delivery-isolated
def test_publish_isolates_a_torn_down_subscriber_loop():
    """A subscriber whose event loop is CLOSED (its SSE stream is tearing down) must NOT
    propagate into the emitting run, and a second LIVE subscriber still receives the item —
    the producer/consumer wall at the hub layer (the ``events.emit`` isolation seal one layer
    down). This is the SOLE wall on the token-stream path, which publishes fragments directly
    via ``publish`` and bypasses ``events.emit``'s outer guard. RED-on-removal: dropping
    ``except RuntimeError`` in ``EventHub._deliver`` makes ``publish`` raise
    ``RuntimeError('Event loop is closed')`` from the dead subscriber's handoff."""
    hub = EventHub()

    dead_loop = asyncio.new_event_loop()
    dead_loop.close()  # call_soon_threadsafe on a closed loop raises RuntimeError
    dead = _Subscription(loop=dead_loop, queue=asyncio.Queue())

    live_loop = asyncio.new_event_loop()
    try:
        live = _Subscription(loop=live_loop, queue=asyncio.Queue())
        run_id = "run_x"
        hub._subscribers[run_id] = [dead, live]

        # The dead subscriber must be swallowed, not propagated — publish returns cleanly.
        hub.publish(run_id, "fragment")

        # The live subscriber's deferred put_nowait runs when its loop is pumped once.
        live_loop.call_soon(live_loop.stop)
        live_loop.run_forever()
        assert live.queue.get_nowait() == "fragment"  # the live wall stayed open
    finally:
        live_loop.close()
