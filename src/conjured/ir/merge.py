"""The merge-strategy definition table — one total definition per closed-enum member.

``MergeStrategy`` (``conjured.ir.common``) is the closed name set (R-pipeline-002,
``conjured/docs/components/pipeline/reference.md`` § ``merge.<channel>``); THIS table is
where each member's three behavioral facts live **together**: the compose-time type
constraint (``accepts`` — which non-optional base channel types the strategy applies to),
the fold seed (``seed`` — the treatment of the FIRST contributor), and one runtime fold
step (``fold``). The compose-time validator and the runner's kernel walk both read this
one table, so a new enum member missing any fact is **unrepresentable**: the totality
check below raises at import (long before compose), never a mid-run surprise.

The semantics realized here are owned at the pipeline reference's § ``merge.<channel>``
— its registry table plus the pinned micro-semantics paragraph (the
``last_present_wins`` emptiness predicate; ``deep_merge_dict``'s
recurse-where-both-dicts / later-write-wins-on-collision rule; ``union_set``'s
first-occurrence, equality-based dedup) — cited, not restated.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping

from conjured.ir.channel_types import (
    ChannelFieldType,
    DictType,
    ListType,
    Primitive,
    PrimitiveType,
)
from conjured.ir.common import MergeStrategy


def _is_present(value: object) -> bool:
    """The ``last_present_wins`` emptiness predicate (the pinned micro-semantics, pipeline reference): "empty" is a zero-length
    sized value; numerics and bools are always present (``0`` / ``False`` are values,
    not absences). ``None`` is unreachable post-validation — a merged channel is
    non-optional and every write was output-validated — so the strategy's non-None
    clause is vacuously satisfied here."""
    try:
        return len(value) != 0  # type: ignore[arg-type]
    except TypeError:
        return True


def _deep_merge(current, new) -> dict:
    """``deep_merge_dict`` (the pinned micro-semantics, pipeline reference): recurse where both sides' values are dicts; otherwise
    the later write (declared order) wins — lists and scalars are replaced, never
    concatenated. Builds a fresh dict (no in-place mutation of channel state)."""
    result = dict(current)
    for key, value in new.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _dedup_first_occurrence(items: list) -> list:
    """``union_set`` ordering (the pinned micro-semantics, pipeline reference): dedup by equality preserving first-occurrence
    order — equality, not hashing, so unhashable element types (dicts) carry no extra
    constraint."""
    result: list = []
    for item in items:
        if item not in result:  # list membership is equality-based
            result.append(item)
    return result


def _last_present_fold(current: object, new: object) -> object:
    if _is_present(new):
        return new
    # All-empty degenerates to the last write in declared order (deterministic,
    # no invented value) — chain the latest empty forward.
    return current if _is_present(current) else new


def _is_str(base: ChannelFieldType) -> bool:
    return isinstance(base, PrimitiveType) and base.primitive is Primitive.STR


@dataclass(frozen=True, slots=True)
class MergeStrategyDef:
    """One strategy's total behavioral definition: ``accepts`` (the compose-time type
    constraint over the merged channel's non-optional BASE type — the optional-wrapper
    rejection itself is the validator's channel-typing policy, upstream), ``seed`` (the
    fold's initial element from the first contributor), ``fold`` (one graph-order left
    fold step; every branch builds a fresh value — R-pipeline-002 runtime region)."""

    accepts: Callable[[ChannelFieldType], bool]
    seed: Callable[[object], object]
    fold: Callable[[object, object], object]


#: The total table — exactly one definition per MergeStrategy member (totality checked
#: below at import). Immutable by construction (the codebase's module-level table idiom).
MERGE_STRATEGY_DEFS: Mapping[MergeStrategy, MergeStrategyDef] = MappingProxyType({
    MergeStrategy.LAST_WINS: MergeStrategyDef(
        accepts=lambda base: True,
        seed=lambda value: value,
        fold=lambda current, new: new,
    ),
    MergeStrategy.FIRST_WINS: MergeStrategyDef(
        accepts=lambda base: True,
        seed=lambda value: value,
        fold=lambda current, new: current,
    ),
    MergeStrategy.LAST_PRESENT_WINS: MergeStrategyDef(
        accepts=lambda base: True,
        seed=lambda value: value,
        fold=_last_present_fold,
    ),
    MergeStrategy.APPEND_LIST: MergeStrategyDef(
        accepts=lambda base: isinstance(base, ListType),
        seed=lambda value: value,
        fold=lambda current, new: list(current) + list(new),  # type: ignore[call-overload]
    ),
    MergeStrategy.UNION_SET: MergeStrategyDef(
        accepts=lambda base: isinstance(base, ListType),
        # The fold over one contributor is already a union — the seed dedups itself.
        seed=lambda value: _dedup_first_occurrence(list(value)),  # type: ignore[call-overload]
        fold=lambda current, new: _dedup_first_occurrence(list(current) + list(new)),  # type: ignore[call-overload]
    ),
    MergeStrategy.DEEP_MERGE_DICT: MergeStrategyDef(
        accepts=lambda base: isinstance(base, DictType),
        seed=lambda value: value,
        fold=_deep_merge,
    ),
    MergeStrategy.CONCAT_STR: MergeStrategyDef(
        accepts=_is_str,
        seed=lambda value: value,
        fold=lambda current, new: current + new,  # type: ignore[operator]
    ),
})


def assert_defs_total(
    defs: Mapping[MergeStrategy, MergeStrategyDef] = MERGE_STRATEGY_DEFS,
) -> None:
    """The totality seal: every closed-enum member carries a definition. Raises at
    import (below) so an enum expansion missing its definition fails the whole engine
    load — compose-time at the latest, never a mid-run fold surprise."""
    # guarantees: merge-strategy-defs-total
    missing = [m.value for m in MergeStrategy if m not in defs]
    if missing:
        raise AssertionError(
            f"MergeStrategy member(s) {missing} carry no MergeStrategyDef — the closed "
            "registry is total by construction; an enum expansion lands with its "
            "accepts/seed/fold definition in conjured.ir.merge.MERGE_STRATEGY_DEFS"
        )


assert_defs_total()
