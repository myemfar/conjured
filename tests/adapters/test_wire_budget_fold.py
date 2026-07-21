"""The deadline-propagation min-fold at the shared transport floor
(``prepare_json_transport`` — the ONE fold point the participating natives route
through): the effective per-call timeout is ``min(transport timeout_ms, remaining
budget)``; a budget with no transport timeout bounds the call by itself; no budget
leaves the transport timeout untouched; an exhausted budget yields a ZERO timeout —
a floor, never an unbounded sentinel."""

from __future__ import annotations

from conjured.adapters.wire import prepare_json_transport


class _Err(Exception):
    pass


def _fold(transport_timeout_ms, remaining_budget_ms):
    transport = {"endpoint": "https://b.test/v1"}
    if transport_timeout_ms is not None:
        transport["timeout_ms"] = transport_timeout_ms
    _, _, timeout_s = prepare_json_transport(
        transport, error=_Err, missing_endpoint="no endpoint",
        remaining_budget_ms=remaining_budget_ms,
    )
    return timeout_s


def test_budget_tighter_than_transport_wins():
    assert _fold(30_000, 5_000) == 5.0


def test_transport_tighter_than_budget_wins():
    assert _fold(1_000, 5_000) == 1.0


def test_budget_bounds_a_call_with_no_transport_timeout():
    assert _fold(None, 5_000) == 5.0


def test_no_budget_leaves_the_transport_timeout_untouched():
    assert _fold(30_000, None) == 30.0
    assert _fold(None, None) is None


def test_exhausted_budget_is_a_zero_timeout_not_unbounded():
    assert _fold(30_000, 0) == 0.0
    assert _fold(None, 0) == 0.0
