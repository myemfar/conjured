"""The merge-strategy definition table's totality seal (conjured/ir/merge.py).

Canon: pipeline/reference.md § ``merge.<channel>`` (R-pipeline-002) — the strategy registry is
closed and each member's compose-time acceptance + runtime fold are ONE definition. The seal under
test: a ``MergeStrategy`` member with no ``MergeStrategyDef`` fails at TABLE IMPORT (compose-time at
the latest), never as a mid-run fold surprise.
"""

from __future__ import annotations

import pytest

from conjured.ir.common import MergeStrategy
from conjured.ir.merge import MERGE_STRATEGY_DEFS, assert_defs_total


# verifies: merge-strategy-defs-total
def test_missing_definition_goes_red() -> None:
    """RED-on-removal: drop one member's definition and the totality seal must raise,
    naming the fold-less member — defending against a new enum member landing
    type-checked at compose but AssertionError-ing mid-run at its first fold."""
    incomplete = {
        member: definition
        for member, definition in MERGE_STRATEGY_DEFS.items()
        if member is not MergeStrategy.UNION_SET
    }
    with pytest.raises(AssertionError, match="union_set"):
        assert_defs_total(incomplete)


def test_real_table_is_total() -> None:
    """The shipped table covers every closed-enum member (the import-time call of the
    seal passed for this test to even import; assert it directly for the RED pairing)."""
    assert_defs_total(MERGE_STRATEGY_DEFS)
    assert set(MERGE_STRATEGY_DEFS) == set(MergeStrategy)


def test_every_definition_is_complete() -> None:
    """Each member's definition carries all three callables — accepts / seed / fold
    (a None-stubbed fact would pass totality but fail at its first use)."""
    for member, definition in MERGE_STRATEGY_DEFS.items():
        assert callable(definition.accepts), member
        assert callable(definition.seed), member
        assert callable(definition.fold), member