"""The nested ``pipeline`` composition kind — engine-invoking-engine, end-to-end
(``pipeline/reference.md`` § The nested ``pipeline`` composition kind).

Acceptance over real compiled + assembled pipelines dispatched through
``conjured.runner.run``, asserted via the canonical event stream (the runtime-testing
paradigm): the embed grammar (the ``kind = "composition"`` / ``meta.kind = "pipeline"``
mirror, presence-opts-in ``[outputs]`` arm), compose-time cycle rejection (the seal —
the exact self-embedding adversary, structured ``ContractViolation``, no depth cap),
``parent_run_id`` correlation (the inner run's own stream; ``null`` for a top-level
run; no handler-bearing events for the embed dispatch), and inner-halt propagation
(the inner error object crosses the boundary UNCHANGED — attribution chain intact,
no inner failure swallowed; R-error-channel-003).

Real modules on ``sys.path`` via ``tmp_path``; no engine internals mocked.
"""

from __future__ import annotations

import importlib
import logging
import textwrap

import pytest

from conjured.errors import (
    Check,
    ContractViolation,
    PipelineFailure,
    SchemaValidationError,
)
from conjured.validator import DeclarationRegistry, compile_pipeline, loads
from conjured.runner.assemble import assemble
from conjured.runner.run import run


@pytest.fixture
def module_dir(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, name: str, source: str) -> None:
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    importlib.invalidate_caches()


def _capture_events():
    """Attach a consumer handler to ``conjured.events.runner`` (the engine ships none).
    Returns ``(captured_list, detach)``."""
    from conjured import events as E

    captured: list = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.msg)

    handler = _Capture()
    lg = E.event_logger()
    lg.addHandler(handler)
    prev_level = lg.level
    lg.setLevel(logging.INFO)

    def detach():
        lg.removeHandler(handler)
        lg.setLevel(prev_level)

    return captured, detach


_TRANSFORM_TOML = (
    '[transform]\n[reads]\n{read} = {{ type = "str" }}\n'
    '[output_schema]\n{write} = {{ type = "str" }}\n'
)


def _nested_runnable(module_dir, mod_name="nested_mod", *, inner_raises=False,
                     inner_bad_output=False):
    """Outer pipeline: transform `shout` -> nested `pipeline` embed -> transform `bang`.
    The inner pipeline holds one transform `mark` (text_upper -> marked). Fault injection:
    ``inner_raises`` makes the inner body raise; ``inner_bad_output`` makes it return a
    wrong-typed output (the inner output-validation SVE)."""
    body = 'raise RuntimeError("inner backend down")' if inner_raises else (
        'return {"marked": 42}' if inner_bad_output else
        'return {"marked": "<" + text_upper + ">"}'
    )
    _write_module(
        module_dir, mod_name,
        f"""
        def shout(*, text):
            return {{"text_upper": text.upper()}}

        def mark(*, text_upper):
            {body}

        def bang(*, marked):
            return {{"final": marked + "!"}}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        f"{mod_name}.shout",
        loads(_TRANSFORM_TOML.format(read="text", write="text_upper"), "handler", file_path="shout.toml"),
        toml_path="handlers/shout.toml",
    )
    reg.add_handler(
        f"{mod_name}.mark",
        loads(_TRANSFORM_TOML.format(read="text_upper", write="marked"), "handler", file_path="mark.toml"),
        toml_path="handlers/mark.toml",
    )
    reg.add_handler(
        f"{mod_name}.bang",
        loads(_TRANSFORM_TOML.format(read="marked", write="final"), "handler", file_path="bang.toml"),
        toml_path="handlers/bang.toml",
    )
    reg.add_composition("pipelines/inner.toml", loads(
        '[meta]\nkind = "pipeline"\nname = "acme.inner"\n'
        f'[[nodes]]\nkind = "handler"\nname = "{mod_name}.mark"\n'
        '[inputs]\ntext_upper = { type = "str" }\n'
        '[outputs]\nmarked = { type = "str" }\n',
        "composition", file_path="pipelines/inner.toml"))
    outer = loads(
        '[meta]\nname = "acme.outer"\n'
        f'[[nodes]]\nkind = "handler"\nname = "{mod_name}.shout"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/inner.toml"\n'
        f'[[nodes]]\nkind = "handler"\nname = "{mod_name}.bang"\n'
        '[inputs]\ntext = { type = "str" }\n[outputs]\nfinal = { type = "str" }\n',
        "pipeline", file_path="outer.toml")
    graph = compile_pipeline(outer, reg, pipeline_name="acme.outer", file_path="outer.toml")
    return assemble(graph, reg)


# ---------------------------------------------------------------------------
# 1. Happy path — a finite nested pipeline runs end-to-end through the one invocation
# ---------------------------------------------------------------------------


def test_nested_pipeline_runs_end_to_end(module_dir):
    """One `(pipeline, inputs)` invocation contains the whole nested structure: outer
    channels wire to the inner [inputs] by name, the inner [outputs] wire back out, and
    downstream outer nodes consume the embed's writes."""
    runnable = _nested_runnable(module_dir)
    result = run(runnable, {"text": "hi"})
    assert dict(result.state) == {
        "text_upper": "HI", "marked": "<HI>", "final": "<HI>!",
    }


def test_nested_pipeline_encapsulates_inner_channels(module_dir):
    """The inner run's channel state never reaches the outer RunResult — only the declared
    inner [outputs] cross the boundary (opaque inner scope, mirroring the hash treatment)."""
    runnable = _nested_runnable(module_dir, mod_name="nested_encap_mod")
    result = run(runnable, {"text": "hi"})
    # `marked` IS an inner output (flattened by name); the inner pipeline has no other
    # internal channel here, so assert the closed key set — nothing extra leaked.
    assert set(result.state) == {"text_upper", "marked", "final"}


# ---------------------------------------------------------------------------
# 2. parent_run_id — the inner run's own stream, correlated, never duplicated
# ---------------------------------------------------------------------------


def test_inner_run_emits_own_stream_with_parent_run_id(module_dir):
    """The inner run emits its own canonical-event stream under its own engine-generated
    pipeline_run_id; its pipeline_start carries parent_run_id = the enclosing run's id;
    a top-level run carries null (hash-model.md § canonical event types)."""
    from conjured import events as E

    runnable = _nested_runnable(module_dir, mod_name="nested_events_mod")
    captured, detach = _capture_events()
    try:
        result = run(runnable, {"text": "hi"})
    finally:
        detach()

    starts = [e for e in captured if isinstance(e, E.PipelineStart)]
    assert len(starts) == 2  # the outer run + the inner run — each its own stream
    outer_start = next(s for s in starts if s.pipeline_run_id == result.run_id)
    inner_start = next(s for s in starts if s.pipeline_run_id != result.run_id)
    # Top-level run: no parent, structurally null.
    assert outer_start.parent_run_id is None
    # Inner run: parent_run_id = the enclosing run's pipeline_run_id — the single linkage.
    assert inner_start.parent_run_id == result.run_id
    # The inner id is engine-minted (never the consumer's), in the structured sortable form.
    assert inner_start.pipeline_run_id.startswith("run_")

    # Own-hash-domain: the inner stream names the INNER pipeline's own pipeline-hash.
    assert inner_start.pipeline_hash != outer_start.pipeline_hash

    completes = [e for e in captured if isinstance(e, E.PipelineComplete)]
    assert {c.pipeline_run_id for c in completes} == {
        outer_start.pipeline_run_id, inner_start.pipeline_run_id,
    }
    # The embed's channel-writes correspond to the INNER run's pipeline_complete
    # outputs_snapshot (the channel-record correspondence at the embed boundary).
    inner_complete = next(c for c in completes if c.pipeline_run_id == inner_start.pipeline_run_id)
    assert inner_complete.outputs_snapshot == {"marked": "<HI>"}


def test_embed_dispatch_emits_no_handler_events_in_outer_stream(module_dir):
    """The embed node's dispatch fires NO handler_enter / handler_exit in the outer
    stream — the closed event node_kind enum has no `pipeline` member, and the inner
    corpus is reconstructed by correlation, never duplicated into the outer stream. The
    inner run's own handler pair carries the inner run id."""
    from conjured import events as E

    runnable = _nested_runnable(module_dir, mod_name="nested_nodup_mod")
    captured, detach = _capture_events()
    try:
        result = run(runnable, {"text": "hi"})
    finally:
        detach()

    outer_enters = [
        e for e in captured
        if isinstance(e, E.HandlerEnter) and e.pipeline_run_id == result.run_id
    ]
    # Exactly the two OUTER transforms — positions 0 and 2; the embed (position 1) emits
    # no handler-bearing event in the outer stream.
    assert [(e.handler_position, e.handler_qualified_name) for e in outer_enters] == [
        (0, "nested_nodup_mod.shout"), (2, "nested_nodup_mod.bang"),
    ]
    inner_enters = [
        e for e in captured
        if isinstance(e, E.HandlerEnter) and e.pipeline_run_id != result.run_id
    ]
    # The inner handler pair rides the INNER run's own stream (fresh 0-indexed order).
    assert [(e.handler_position, e.handler_qualified_name) for e in inner_enters] == [
        (0, "nested_nodup_mod.mark"),
    ]
    assert all(
        e.node_kind in ("transform", "service", "hook", "trainable")
        for e in captured if isinstance(e, (E.HandlerEnter, E.HandlerExit))
    )


# ---------------------------------------------------------------------------
# 3. Inner-halt propagation — attribution chain intact, no inner failure swallowed
# ---------------------------------------------------------------------------


def test_inner_halt_propagates_with_attribution_chain(module_dir):
    """A handler failure inside the inner run surfaces as the embedding node's failure
    with the attribution chain INTACT: the propagated PipelineFailure is the inner run's
    own — inner pipeline_run_id, inner composition_ref, inner failed-handler position, and
    the INNER run's failure_category — correlated to the outer run by parent_run_id; the
    outer pipeline_error names the embed node (R-error-channel-003; § The nested
    `pipeline` composition kind, Halt propagation)."""
    from conjured import events as E

    runnable = _nested_runnable(module_dir, mod_name="nested_halt_mod", inner_raises=True)
    captured, detach = _capture_events()
    try:
        with pytest.raises(PipelineFailure) as exc:
            run(runnable, {"text": "hi"}, pipeline_run_id="outer-run-7")
    finally:
        detach()

    failure = exc.value
    # The inner locus, preserved through the boundary (structured, not a bare trace):
    assert failure.failure_category == "handler"  # the INNER run's category
    assert failure.cause_class == "RuntimeError"
    assert failure.cause_message == "inner backend down"
    assert failure.composition_ref == "acme.inner[0]"  # the INNER pipeline + entry ordinal
    assert failure.failed_handler_qualified_name == "nested_halt_mod.mark"
    assert failure.failed_handler_position == 0  # the inner run's own dispatch order
    # The failure is attributed under the INNER run's engine-minted id — correlated
    # outward via the inner pipeline_start's parent_run_id, never re-stamped.
    inner_start = next(
        e for e in captured
        if isinstance(e, E.PipelineStart) and e.pipeline_run_id != "outer-run-7"
    )
    assert failure.pipeline_run_id == inner_start.pipeline_run_id
    assert inner_start.parent_run_id == "outer-run-7"

    # BOTH runs emit pipeline_error (no inner failure swallowed): the inner names its
    # failing handler; the outer names the embed node position, carrying the inner
    # failure_category (the error object is the same one).
    errors = [e for e in captured if isinstance(e, E.PipelineError)]
    inner_error = next(e for e in errors if e.pipeline_run_id == inner_start.pipeline_run_id)
    outer_error = next(e for e in errors if e.pipeline_run_id == "outer-run-7")
    assert inner_error.failed_handler_qualified_name == "nested_halt_mod.mark"
    assert outer_error.failed_handler_qualified_name == "acme.inner"  # the embed node
    assert outer_error.failed_handler_position == 1  # the embed's outer dispatch position
    assert outer_error.failure_category == "handler"  # carries the INNER run's category
    assert outer_error.error_class == "PipelineFailure"


def test_inner_schema_violation_propagates_unchanged(module_dir):
    """An inner output-validation failure (the structured SchemaValidationError) crosses
    the embed boundary unchanged — the inner handler attribution rides the exception."""
    runnable = _nested_runnable(
        module_dir, mod_name="nested_sve_mod", inner_bad_output=True
    )
    with pytest.raises(SchemaValidationError) as exc:
        run(runnable, {"text": "hi"})
    assert exc.value.handler_qualified_name == "nested_sve_mod.mark"
    assert exc.value.field_validations  # structured field attribution, not a bare trace


# ---------------------------------------------------------------------------
# 4. The cycle seal — compose-time, structural, no depth cap
# ---------------------------------------------------------------------------


def _register_chain(reg, depth: int, *, close_cycle: bool) -> None:
    """Register `pipelines/p0.toml` .. `pipelines/p<depth-1>.toml`, each embedding the
    next; the last embeds p0 again when ``close_cycle`` (the transitive adversary) else
    holds a plain terminal transform declaration reference-free body."""
    for i in range(depth):
        if i < depth - 1:
            body = f'[[nodes]]\nkind = "composition"\nname = "pipelines/p{i + 1}.toml"\n'
        elif close_cycle:
            body = '[[nodes]]\nkind = "composition"\nname = "pipelines/p0.toml"\n'
        else:
            body = '[[nodes]]\nkind = "handler"\nname = "chain_mod.deep_mark"\n'
        reg.add_composition(f"pipelines/p{i}.toml", loads(
            f'[meta]\nkind = "pipeline"\nname = "acme.p{i}"\n{body}'
            '[inputs]\ntext_upper = { type = "str" }\n'
            + ("" if close_cycle else '[outputs]\nmarked = { type = "str" }\n'),
            "composition", file_path=f"pipelines/p{i}.toml"))


# verifies: nested-embed-cycle-rejected
def test_cycle_is_rejected_at_compose_before_any_dispatch():
    """The seal's exact adversary — a pipeline that TRANSITIVELY embeds itself — is
    rejected as a structured ContractViolation at compose, before any node dispatches
    (a cyclic composition never loads, so it can never run). RED if the cycle detection
    is removed: the compose recursion would recurse unboundedly (RecursionError, a
    fourth class) instead of the structured contract."""
    reg = DeclarationRegistry()
    _register_chain(reg, 3, close_cycle=True)
    outer = loads(
        '[meta]\nname = "acme.outer"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/p0.toml"\n'
        '[inputs]\ntext_upper = { type = "str" }\n',
        "pipeline", file_path="outer.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(outer, reg, pipeline_name="acme.outer", file_path="outer.toml")
    assert exc.value.check is Check.COMPOSITION_CYCLE
    assert exc.value.rule_id == "R-pipeline-001"
    # The diagnostic names the embed chain (the cycle path, not just "a cycle exists").
    assert "pipelines/p0.toml" in exc.value.actual


# verifies: nested-embed-cycle-rejected
def test_direct_self_embed_is_rejected():
    """The degenerate adversary: a pipeline composition embedding ITSELF."""
    reg = DeclarationRegistry()
    reg.add_composition("pipelines/self.toml", loads(
        '[meta]\nkind = "pipeline"\nname = "acme.selfish"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/self.toml"\n'
        '[inputs]\ntext_upper = { type = "str" }\n',
        "composition", file_path="pipelines/self.toml"))
    outer = loads(
        '[meta]\nname = "acme.outer"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/self.toml"\n'
        '[inputs]\ntext_upper = { type = "str" }\n',
        "pipeline", file_path="outer.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(outer, reg, pipeline_name="acme.outer", file_path="outer.toml")
    assert exc.value.check is Check.COMPOSITION_CYCLE


def test_finite_acyclic_nesting_has_no_depth_cap(module_dir):
    """A finite acyclic nesting always terminates and type-checks whole at load; its depth
    is whatever the author declares — no depth ceiling, no max_depth, no runtime depth
    guard. Depth 30 (comfortably past any historical cap value) compiles AND runs."""
    depth = 30
    _write_module(
        module_dir, "chain_mod",
        """
        def shout(*, text):
            return {"text_upper": text.upper()}

        def deep_mark(*, text_upper):
            return {"marked": "<" + text_upper + ">"}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "chain_mod.shout",
        loads(_TRANSFORM_TOML.format(read="text", write="text_upper"), "handler", file_path="shout.toml"),
        toml_path="handlers/shout.toml",
    )
    reg.add_handler(
        "chain_mod.deep_mark",
        loads(_TRANSFORM_TOML.format(read="text_upper", write="marked"), "handler", file_path="deep_mark.toml"),
        toml_path="handlers/deep_mark.toml",
    )
    # Each pi embeds p(i+1) and passes the boundary channels through; p(depth-1) holds the
    # one real transform. Every layer declares the same boundary, wired by name.
    for i in range(depth):
        if i < depth - 1:
            body = f'[[nodes]]\nkind = "composition"\nname = "pipelines/p{i + 1}.toml"\n'
        else:
            body = '[[nodes]]\nkind = "handler"\nname = "chain_mod.deep_mark"\n'
        reg.add_composition(f"pipelines/p{i}.toml", loads(
            f'[meta]\nkind = "pipeline"\nname = "acme.p{i}"\n{body}'
            '[inputs]\ntext_upper = { type = "str" }\n'
            '[outputs]\nmarked = { type = "str" }\n',
            "composition", file_path=f"pipelines/p{i}.toml"))
    outer = loads(
        '[meta]\nname = "acme.deep_outer"\n'
        '[[nodes]]\nkind = "handler"\nname = "chain_mod.shout"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/p0.toml"\n'
        '[inputs]\ntext = { type = "str" }\n[outputs]\nmarked = { type = "str" }\n',
        "pipeline", file_path="outer.toml")
    graph = compile_pipeline(outer, reg, pipeline_name="acme.deep_outer", file_path="outer.toml")
    runnable = assemble(graph, reg)

    from conjured import events as E

    captured, detach = _capture_events()
    try:
        result = run(runnable, {"text": "deep"})
    finally:
        detach()
    assert result.state["marked"] == "<DEEP>"
    # One run per nesting layer plus the outer — each its own stream, chained by
    # parent_run_id all the way down.
    starts = [e for e in captured if isinstance(e, E.PipelineStart)]
    assert len(starts) == depth + 1
    parents = {s.pipeline_run_id: s.parent_run_id for s in starts}
    roots = [rid for rid, parent in parents.items() if parent is None]
    assert roots == [result.run_id]  # exactly one top-level run
    # Every non-root chains to another run in the set (the correlation is complete).
    assert all(parent in parents for rid, parent in parents.items() if parent is not None)


# ---------------------------------------------------------------------------
# 5. The presence-opts-in [outputs] arm
# ---------------------------------------------------------------------------


def test_inner_pipeline_without_outputs_writes_nothing_back(module_dir):
    """The nested kind follows the pipeline's presence-opts-in [outputs] arm (not the
    trainable's body-required arm): an inner pipeline declaring no [outputs] block is a
    valid embed that writes no outer channels."""
    _write_module(
        module_dir, "noout_mod",
        """
        def shout(*, text):
            return {"text_upper": text.upper()}

        def observe(*, text_upper):
            return {"seen": text_upper}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "noout_mod.shout",
        loads(_TRANSFORM_TOML.format(read="text", write="text_upper"), "handler", file_path="shout.toml"),
        toml_path="handlers/shout.toml",
    )
    reg.add_handler(
        "noout_mod.observe",
        loads(_TRANSFORM_TOML.format(read="text_upper", write="seen"), "handler", file_path="observe.toml"),
        toml_path="handlers/observe.toml",
    )
    reg.add_composition("pipelines/sink.toml", loads(
        '[meta]\nkind = "pipeline"\nname = "acme.sink"\n'
        '[[nodes]]\nkind = "handler"\nname = "noout_mod.observe"\n'
        '[inputs]\ntext_upper = { type = "str" }\n',  # NO [outputs] — opts out
        "composition", file_path="pipelines/sink.toml"))
    outer = loads(
        '[meta]\nname = "acme.outer"\n'
        '[[nodes]]\nkind = "handler"\nname = "noout_mod.shout"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/sink.toml"\n'
        '[inputs]\ntext = { type = "str" }\n',
        "pipeline", file_path="outer.toml")
    graph = compile_pipeline(outer, reg, pipeline_name="acme.outer", file_path="outer.toml")
    runnable = assemble(graph, reg)
    result = run(runnable, {"text": "hi"})
    # The embed wrote nothing back: only the outer transform's write is outer state.
    assert dict(result.state) == {"text_upper": "HI"}
