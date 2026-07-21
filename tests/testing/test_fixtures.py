"""Contract fixtures — harvested from a real run, hash-gated against composition drift.

The hash-gate test is RED-on-removal: remove the pipeline_hash comparison in load_fixture and
the tampered-hash fixture loads silently, asserting a stale contract (R-testing-008).
"""

from __future__ import annotations

import json

import pytest

from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.handler import HookDeclaration, TransformDeclaration
from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
from conjured.testing import (
    StaleFixtureError,
    harvest,
    load_fixture,
    load_fixture_unchecked,
    load_test_pipeline,
    write_fixtures,
)


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


def test_harvest_stamps_every_fixture_with_the_pipeline_hash(chain):
    fixtures = harvest(chain.runnable, {"text": "hi"})
    assert len(fixtures) == 2  # one per dispatched node
    assert all(f.pipeline_hash == chain.runnable.pipeline_hash for f in fixtures)
    first = next(f for f in fixtures if f.position == 0)
    assert first.reads == {"text": "hi"}
    assert first.writes == {"mid": "HI"}
    assert first.node_kind == "transform"


def test_write_and_load_roundtrip(chain, tmp_path):
    paths = write_fixtures(harvest(chain.runnable, {"text": "hi"}), tmp_path / "fx")
    assert len(paths) == 2
    loaded = load_fixture_unchecked(paths[0])
    assert loaded.pipeline_hash == chain.runnable.pipeline_hash
    assert loaded.reads == {"text": "hi"}


def test_load_fixture_passes_on_matching_hash(chain, tmp_path):
    paths = write_fixtures(harvest(chain.runnable, {"text": "hi"}), tmp_path / "fx")
    fixture = load_fixture(paths[0], chain.runnable)
    assert fixture.pipeline_hash == chain.runnable.pipeline_hash


def test_load_fixture_rejects_stale_hash(chain, tmp_path):
    paths = write_fixtures(harvest(chain.runnable, {"text": "hi"}), tmp_path / "fx")
    path = paths[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["pipeline_hash"] = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StaleFixtureError):
        load_fixture(path, chain.runnable)


def test_harvest_tolerates_an_absorbed_hook(conjured_registry, module_writer):
    # A hook whose body raises is absorbed by the engine (warning, run continues) — it emits a
    # handler_enter but no handler_exit. harvest must NOT crash on that enter-only node; it yields a
    # fixture with writes=None (a hook writes no channels). RED-on-removal: revert harvest's tolerant
    # pairing and this raises LookupError.
    module = module_writer(
        "fx_hook_mod",
        """
        def first(*, text):
            return {"mid": text.upper()}

        def boom(*, mid):
            raise RuntimeError("hook failure absorbed by the engine")
        """,
    )
    conjured_registry.add_handler(
        f"{module}.first", TransformDeclaration(reads=(_fd("text"),), output_schema=(_fd("mid"),)),
        toml_path="h1.toml",
    )
    conjured_registry.add_handler(
        f"{module}.boom", HookDeclaration(reads=(_fd("mid"),)), toml_path="h2.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="fx.hook"),
        nodes=(HandlerNode(name=f"{module}.first"), HandlerNode(name=f"{module}.boom")),
        inputs=(_fd("text"),), outputs=(_fd("mid"),),
    )
    runnable = load_test_pipeline(pipeline, conjured_registry)
    fixtures = harvest(runnable, {"text": "hi"})  # must not raise
    by_position = {f.position: f for f in fixtures}
    assert by_position[0].node_kind == "transform" and by_position[0].writes == {"mid": "HI"}
    assert by_position[1].node_kind == "hook" and by_position[1].writes is None


def test_completed_hook_reads_writes_none_through_both_readers(conjured_registry, module_writer):
    # TFD-6: the COMPLETED-hook branch (exit present, writes_snapshot=None per the
    # HandlerExit constructor seal) — the documented happy-path arm the absorbed-hook
    # test above cannot reach (it exercises the exit-ABSENT arm). Both readers must
    # report writes is None via the exit_.writes_snapshot path.
    from conjured.testing import inspect_state, run_and_capture

    module = module_writer(
        "fx_ok_hook_mod",
        """
        def first(*, text):
            return {"mid": text.upper()}

        def observe(*, mid):
            return None
        """,
    )
    conjured_registry.add_handler(
        f"{module}.first", TransformDeclaration(reads=(_fd("text"),), output_schema=(_fd("mid"),)),
        toml_path="h1.toml",
    )
    conjured_registry.add_handler(
        f"{module}.observe", HookDeclaration(reads=(_fd("mid"),)), toml_path="h2.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="fx.okhook"),
        nodes=(HandlerNode(name=f"{module}.first"), HandlerNode(name=f"{module}.observe")),
        inputs=(_fd("text"),), outputs=(_fd("mid"),),
    )
    runnable = load_test_pipeline(pipeline, conjured_registry)
    _result, events = run_and_capture(runnable, {"text": "hi"})
    state = inspect_state(events, 1)
    assert state.node_kind == "hook" and state.writes is None  # reader 1: inspect_state
    fixture = {f.position: f for f in harvest(runnable, {"text": "hi"})}[1]
    assert fixture.node_kind == "hook" and fixture.writes is None  # reader 2: harvest
