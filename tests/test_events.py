"""``conjured.events`` (C4) unit seals — the closed-enum half of the event contract.

The runner-driven emit behaviour (the handler_enter/exit pair, the run-lifecycle events,
service_invocation, and the producer/consumer isolation wall) is exercised end-to-end in
``tests/runner/test_run.py``. This module holds the pure ``emit()`` unit property the audit
(code-vs-docs 2026-06-15, F2) found unverified-by-luck: the closed-enum REJECT. The set of
canonical events is closed (hash-model.md § Canonical event types) — adding a member is an
engine change, never a runtime extension — so ``emit`` raises ``TypeError`` on any non-member
rather than silently publishing an unshaped record (I4: a malformed record is training-data
corruption)."""

from __future__ import annotations

import pytest

from conjured import events


# verifies: emit-closed-enum-reject
def test_emit_rejects_a_non_canonical_event():
    """The closed-enum REJECT seal: a bare non-event object raises TypeError, never
    publishes. RED if the isinstance guard in emit() is removed (logging accepts any msg,
    so the object would publish silently)."""
    with pytest.raises(TypeError, match="not a canonical event"):
        events.emit(object())


def test_emit_rejects_a_dict_shaped_imposter():
    """The realistic 'unshaped record' adversary: a dict that LOOKS like an event payload
    is still not a canonical-event instance — membership is by type, not by duck shape."""
    with pytest.raises(TypeError, match="not a canonical event"):
        events.emit({"node_kind": "transform", "pipeline_run_id": "run_x"})


def test_emit_admits_a_canonical_event_member():
    """The seal's positive control: a real canonical event passes the guard (with no
    consumer handler attached and the channel at its default level, the publish is a
    silent no-op — the engine ships no handlers)."""
    events.emit(
        events.PipelineStart(
            pipeline_run_id="run_2026-06-15T00:00:00Z_unit",
            pipeline_hash="sha256:" + "0" * 64,
            timestamp=events.now_iso(),
            inputs_snapshot={},
        )
    )  # no raise


def test_emit_rejects_a_subclass_of_a_canonical_event():
    """Exact-type membership, not isinstance (surprise-fixes 3-code): a SUBCLASS of a canonical
    event carries a *changed* shape riding under the parent's identity — the unshaped record the
    closed enum exists to reject (I4). ``isinstance`` admitted it; ``type(event) not in
    CANONICAL_EVENT_CLASSES`` rejects it. RED if the guard reverts to ``isinstance`` (the
    subclass then passes as its parent). Nothing in the engine subclasses an event, so the
    tightening breaks nothing."""
    class SneakyPipelineStart(events.PipelineStart):
        pass

    imposter = SneakyPipelineStart(
        pipeline_run_id="run_2026-06-15T00:00:00Z_unit",
        pipeline_hash="sha256:" + "0" * 64,
        timestamp=events.now_iso(),
        inputs_snapshot={},
    )
    with pytest.raises(TypeError, match="not a canonical event"):
        events.emit(imposter)


def test_pipeline_hash_changed_requires_old_hash_nonnullable():
    """Fix 4 (`event-old-hash-nonnull`): ``PipelineHashChanged.old_pipeline_hash`` is REQUIRED
    and non-nullable. Canon (hash-model.md § Canonical event types) lists it with no nullable
    annotation, in deliberate contrast to the adjacent ``training_bundle_hash_changed`` row whose
    ``old_training_bundle_hash`` IS ``(nullable — absent on first observation)``: a missing
    manifest fires no event at all (§ Enforcement off), so a recorded prior value is always
    present when ``pipeline_hash_changed`` fires. RED if the ``= None`` default is re-added (the
    field would then construct without an old value)."""
    # Required: constructing without `old_pipeline_hash` raises (the field has no default).
    with pytest.raises(TypeError):
        events.PipelineHashChanged(
            new_pipeline_hash="sha256:" + "1" * 64,
            timestamp=events.now_iso(),
        )
    # Supplied: the well-formed event constructs and carries the prior value.
    evt = events.PipelineHashChanged(
        new_pipeline_hash="sha256:" + "1" * 64,
        timestamp=events.now_iso(),
        old_pipeline_hash="sha256:" + "0" * 64,
    )
    assert evt.old_pipeline_hash == "sha256:" + "0" * 64

    # The deliberate contrast: the sibling TBH event IS nullable (first observation) — it
    # constructs WITHOUT an old hash, defaulting `old_training_bundle_hash` to None.
    tbh_evt = events.TrainingBundleHashChanged(
        trainable_qualified_name="my_pkg.dialogue_trainable",
        new_training_bundle_hash="sha256:" + "2" * 64,
        pipeline_hash="sha256:" + "1" * 64,
        timestamp=events.now_iso(),
    )
    assert tbh_evt.old_training_bundle_hash is None


# verifies: node-kind-single-source
def test_node_kind_is_single_sourced_in_ir_graph_source():
    """Single-source guard (the node-kind dedup): ``conjured.events.NodeKind`` is THE sole
    declaration of the closed handler-bearing-event ``node_kind`` literal
    ``Literal["transform","service","hook","trainable"]``, and ``conjured.ir.graph`` must
    COMPOSE over the imported name (``GraphNodeKind = Union[NodeKind, Literal["pipeline"]]``
    — the graph layer adds exactly the graph-only nested-``pipeline``-embed member, which
    emits no handler-bearing events so it is deliberately NOT an event ``node_kind``),
    never re-declare the four.

    The load-bearing check is **source-level**, NOT runtime identity, because ``typing``
    INTERNS ``Literal``: two textually-identical ``Literal[...]`` declarations return the
    *same cached object*, so a runtime identity check stays green EVEN IF someone re-forks
    the literal (pastes the four-member declaration back — the single most likely
    regression). A runtime ``is`` therefore cannot bite the drift this dedup forbids — a
    green that cannot go red is a non-test. So
    we assert the IR source structurally: it imports ``NodeKind``, references that bare name
    inside the ``GraphNodeKind`` binding, and none of the four event kinds appears as a
    string literal in the binding (a re-fork of any event member → RED; verified
    empirically against both the full re-fork and a five-member re-fork)."""
    import ast
    import inspect

    from conjured.ir import graph as ir_graph

    tree = ast.parse(inspect.getsource(ir_graph))

    # (a) The IR imports the single source from conjured.events.
    imports_nodekind = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "conjured.events"
        and any(alias.name == "NodeKind" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imports_nodekind, "ir.graph must import NodeKind from conjured.events (the one source)"

    # (b) GraphNodeKind is bound EXACTLY ONCE. An annotated re-fork
    # (`GraphNodeKind: X = …`) is an AnnAssign, not an Assign → fails the count check.
    bindings = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "GraphNodeKind" for t in node.targets)
    ]
    assert len(bindings) == 1, f"expected exactly one `GraphNodeKind = …` binding, found {len(bindings)}"
    rhs = bindings[0].value

    # (c) The binding REFERENCES the imported NodeKind name — the four event kinds flow in
    # through the one source, never a pasted copy.
    references_nodekind = any(
        isinstance(node, ast.Name) and node.id == "NodeKind" for node in ast.walk(rhs)
    )
    assert references_nodekind, (
        "GraphNodeKind must compose over the events-owned NodeKind name "
        "(`Union[NodeKind, Literal[\"pipeline\"]]`), never re-declare the four — "
        "a re-fork re-creates the drift the dedup removes"
    )

    # (d) No event node_kind member appears as a string literal in the binding: a re-fork
    # (`Literal["transform", …]`, with or without the graph-only member) → RED. The only
    # admissible extra literal member is the graph-only "pipeline".
    literal_members = {
        node.value
        for node in ast.walk(rhs)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    event_kinds = {"transform", "service", "hook", "trainable"}
    assert not (literal_members & event_kinds), (
        f"GraphNodeKind re-declares event node_kind member(s) {sorted(literal_members & event_kinds)} "
        "— the four are owned by conjured.events.NodeKind and must flow in by name"
    )
    assert literal_members == {"pipeline"}, (
        f"GraphNodeKind's graph-only members must be exactly {{'pipeline'}} (the nested "
        f"`pipeline` embed — it emits no handler-bearing events), got {sorted(literal_members)}"
    )

    # Cheap runtime sanity (NOT the RED-on-removal guard — Literal interning makes identity
    # checks unfalsifiable; the source checks above are the load-bearing guard).
    from typing import Literal, Union, get_args

    from conjured.events import NodeKind
    from conjured.ir import GraphNodeKind as GraphNodeKind_pkg
    from conjured.ir.graph import GraphNodeKind

    assert GraphNodeKind == Union[NodeKind, Literal["pipeline"]]
    assert GraphNodeKind_pkg == GraphNodeKind
    assert get_args(NodeKind) == ("transform", "service", "hook", "trainable")


# ---------------------------------------------------------------------------
# Event-record immutability — a retained event is its own deep copy (Ruling 1)
# ---------------------------------------------------------------------------
# Each carries a nested-mutable payload, mutates the SOURCE after construction, and asserts the
# retained event is unchanged. RED if the `__post_init__` deepcopy is removed (the event would
# alias the source and the mutation would rewrite the retained training record — I4).

_HASH = "sha256:" + "0" * 64


# verifies: event-payload-deepcopy
def test_pipeline_start_deep_copies_inputs_snapshot():
    source = {"seed": {"tags": ["a"]}}
    evt = events.PipelineStart(
        pipeline_run_id="run_x", pipeline_hash=_HASH, timestamp=events.now_iso(),
        inputs_snapshot=source,
    )
    source["seed"]["tags"].append("MUTATED")
    assert evt.inputs_snapshot == {"seed": {"tags": ["a"]}}


# verifies: event-payload-deepcopy
def test_handler_enter_deep_copies_reads_snapshot():
    # The runner SHARES the per-dispatch reads projection with the handler body, so a legal
    # in-place mutation of the reads dict by the body must not rewrite this retained record.
    source = {"tags": ["a", "b"]}
    evt = events.HandlerEnter(
        handler_qualified_name="m.h", handler_position=0, node_kind="transform",
        pipeline_run_id="run_x", timestamp=events.now_iso(), reads_snapshot=source,
    )
    source["tags"].append("MUTATED")
    assert evt.reads_snapshot == {"tags": ["a", "b"]}
    assert evt.reads_snapshot["tags"] is not source["tags"]


# verifies: event-payload-deepcopy
def test_handler_exit_deep_copies_writes_snapshot():
    # The routed result dict evolves channel state downstream; the retained exit is decoupled.
    source = {"out": {"scores": [1, 2]}}
    evt = events.HandlerExit(
        handler_qualified_name="m.h", handler_position=0, node_kind="transform",
        elapsed_ms=1, pipeline_run_id="run_x", timestamp=events.now_iso(),
        writes_snapshot=source, correlation_id=None,
    )
    source["out"]["scores"].append(99)
    assert evt.writes_snapshot == {"out": {"scores": [1, 2]}}


# verifies: event-payload-deepcopy
def test_pipeline_complete_deep_copies_outputs_snapshot():
    source = {"result": {"items": [1]}}
    evt = events.PipelineComplete(
        pipeline_hash=_HASH, pipeline_run_id="run_x", elapsed_ms=1,
        timestamp=events.now_iso(), outputs_snapshot=source,
    )
    source["result"]["items"].append(2)
    assert evt.outputs_snapshot == {"result": {"items": [1]}}


# ---------------------------------------------------------------------------
# Structural presence-iff seals on the event payloads (Ruling 4)
# ---------------------------------------------------------------------------
# Canon (hash-model.md § Canonical event types) pins these presence rules; the dataclasses now
# enforce them as constructor seals (mirroring PipelineFailure's pf-service-binding-iff-service).
# Each negative case is RED-on-removal: drop the guard and the malformed record constructs.


def _exit(node_kind, writes_snapshot, correlation_id):
    return events.HandlerExit(
        handler_qualified_name="m.h", handler_position=0, node_kind=node_kind,
        elapsed_ms=1, pipeline_run_id="run_x", timestamp=events.now_iso(),
        writes_snapshot=writes_snapshot, correlation_id=correlation_id,
    )


# verifies: event-exit-writes-iff-hook
def test_handler_exit_writes_snapshot_present_iff_not_hook():
    # A hook carrying a writes payload, and a non-hook carrying None, are both runner-construction
    # bugs — fail loud both directions.
    with pytest.raises(ValueError, match="writes_snapshot"):
        _exit("hook", {"x": 1}, None)          # a hook writes no channels
    with pytest.raises(ValueError, match="writes_snapshot"):
        _exit("transform", None, None)         # a transform must carry its writes
    # Valid shapes construct cleanly.
    assert _exit("hook", None, None).writes_snapshot is None
    assert _exit("transform", {"x": 1}, None).writes_snapshot == {"x": 1}
    assert _exit("trainable", {"y": 2}, None).writes_snapshot == {"y": 2}


# verifies: event-exit-correlation-iff-service
def test_handler_exit_correlation_id_present_iff_service():
    with pytest.raises(ValueError, match="correlation_id"):
        _exit("service", {"x": 1}, None)       # a service must name its correlation_id
    with pytest.raises(ValueError, match="correlation_id"):
        _exit("transform", {"x": 1}, "run_x:0")  # a non-service must not carry one
    # Valid service shape.
    assert _exit("service", {"x": 1}, "run_x:0").correlation_id == "run_x:0"


def _err(error_class, cause_class=None, failure_category=None):
    return events.PipelineError(
        pipeline_hash=_HASH, pipeline_run_id="run_x", elapsed_ms=1, timestamp=events.now_iso(),
        error_class=error_class, failed_handler_qualified_name="m.h", failed_handler_position=0,
        error_message="boom", cause_class=cause_class, failure_category=failure_category,
    )


# verifies: event-error-cause-fields-iff-pf
def test_pipeline_error_cause_fields_present_iff_pipeline_failure():
    # A PipelineFailure must carry BOTH cause_class and failure_category; a CV/SVE must carry
    # NEITHER (they are their own structural cause). Every mismatch fails loud.
    with pytest.raises(ValueError, match="cause_class"):
        _err("PipelineFailure", cause_class=None, failure_category="engine")
    with pytest.raises(ValueError, match="failure_category"):
        _err("PipelineFailure", cause_class="ValueError", failure_category=None)
    with pytest.raises(ValueError, match="cause_class"):
        _err("ContractViolation", cause_class="ValueError")
    with pytest.raises(ValueError, match="failure_category"):
        _err("SchemaValidationError", failure_category="handler")
    # Valid shapes.
    pf = _err("PipelineFailure", cause_class="ValueError", failure_category="handler")
    assert pf.cause_class == "ValueError" and pf.failure_category == "handler"
    cv = _err("ContractViolation")
    assert cv.cause_class is None and cv.failure_category is None
