"""Contract fixtures — harvested from a real or fake-backed run, hash-gated against drift.

The contract: ``conjured/docs/components/testing/reference.md`` § The channel is the seam /
§ Fixtures are harvested … and hash-gated. A captured run's channel records serve directly as
contract fixtures (the pipeline-as-training-contract collapse), so a fixture is **harvested** from a
run, never handwritten. Each fixture records the ``pipeline_hash`` it was captured under; the loader
flags a fixture whose recorded hash no longer matches the current composition ("predates the
composition — re-harvest") — the same composition-identity drift gate the doc attestation and the
training-bundle-hash use, turned on fixtures.

A fixture is per dispatched node (its ``reads`` input seam + ``writes`` output seam, plus the
service payloads for a service dispatch), keyed by ``handler_position``. Storage is plain JSON, which
normalises a tuple-typed value to a list on round-trip — compare a loaded fixture's snapshots after
the same normalisation (the by-value contract assertion is over JSON-shaped values).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from conjured.events import HandlerEnter, HandlerExit
from conjured.testing.errors import StaleFixtureError
from conjured.testing.events import _service_at, run_and_capture


@dataclass(frozen=True, slots=True)
class SeamFixture:
    """One dispatched node's harvested contract record, stamped with the composition's
    ``pipeline_hash``. ``reads`` / ``writes`` are the ``handler_enter`` / ``handler_exit`` snapshots
    (``writes`` is ``None`` for a hook). ``service_input`` / ``service_output`` are the
    ``service_invocation`` payloads, present only for a service dispatch."""

    pipeline_hash: str
    position: int
    qualified_name: str
    node_kind: str
    reads: Mapping[str, object]
    writes: Mapping[str, object] | None
    service_input: Mapping[str, object] | None = None
    service_output: Mapping[str, object] | None = None


def harvest(runnable, inputs: Mapping[str, object], *, pipeline_run_id: str | None = None) -> list[SeamFixture]:
    """Run ``runnable`` once through the real engine runner and harvest a per-node
    :class:`SeamFixture` for every dispatch, each stamped with ``runnable.pipeline_hash``.

    The run is real (the dispatch path, not a bare call), so the fixtures are exactly the channel
    records the composition produces; bind a fake at the adapter seam first (twin substitution) for a
    fake-backed harvest. ``pipeline_run_id`` is echoed to the run as its id (else the engine mints
    one). A run that **halts** propagates its engine error class — there is no partial harvest. A
    **non-halting absorbed hook failure** (the engine logs a warning and continues, emitting a
    ``handler_enter`` but no ``handler_exit`` for that node) yields a fixture with ``writes=None`` for
    it — a hook writes no channels regardless.
    """
    run_kwargs = {} if pipeline_run_id is None else {"pipeline_run_id": pipeline_run_id}
    # guarantees: harvest-halt-propagates
    _result, events = run_and_capture(runnable, inputs, **run_kwargs)
    enters = {e.handler_position: e for e in events if isinstance(e, HandlerEnter)}
    exits = {e.handler_position: e for e in events if isinstance(e, HandlerExit)}
    fixtures: list[SeamFixture] = []
    for position in sorted(enters):
        enter = enters[position]
        exit_ = exits.get(position)  # absent for an absorbed-hook node (enter emitted, body raised)
        service = _service_at(events, position)  # fails loud on >1 (never silently harvests one)
        writes = None if exit_ is None else exit_.writes_snapshot
        fixtures.append(
            SeamFixture(
                pipeline_hash=runnable.pipeline_hash,
                position=position,
                qualified_name=enter.handler_qualified_name,
                node_kind=enter.node_kind,
                reads=dict(enter.reads_snapshot),
                writes=None if writes is None else dict(writes),
                service_input=None if service is None else dict(service.input_payload),
                service_output=None if service is None else dict(service.output_payload),
            )
        )
    return fixtures


def write_fixtures(fixtures: list[SeamFixture], directory: str | Path) -> list[Path]:
    """Write each fixture as one JSON file under ``directory`` (created if absent), named
    ``<position>_<qualified_name>.json``. Returns the written paths."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fixture in fixtures:
        path = target / _fixture_filename(fixture)
        path.write_text(json.dumps(_to_json(fixture), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def load_fixture(path: str | Path, runnable) -> SeamFixture:
    """Load a harvested fixture and gate it against ``runnable.pipeline_hash`` — the drift
    check is intrinsic to the default load, so a fixture that predates a composition edit can
    never silently assert a stale contract.

    Raises :class:`~conjured.testing.errors.StaleFixtureError` when the fixture's recorded
    ``pipeline_hash`` differs from the current composition's. For the rare raw read — fixture
    inspection or re-harvest tooling, where no composition is in hand to gate against — use
    :func:`load_fixture_unchecked`.
    """
    fixture = load_fixture_unchecked(path)
    if fixture.pipeline_hash != runnable.pipeline_hash:
        raise StaleFixtureError(
            f"fixture {Path(path).name} predates the composition — re-harvest. recorded "
            f"pipeline_hash {fixture.pipeline_hash} != current composition {runnable.pipeline_hash}."
        )
    return fixture


def load_fixture_unchecked(path: str | Path) -> SeamFixture:
    """Load a harvested fixture from JSON with NO hash check — the raw read primitive.
    Sanctioned use: raw fixture inspection or re-harvest tooling, where no composition is in
    hand to gate against. Prefer the safe-by-default :func:`load_fixture` whenever a runnable
    is available."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SeamFixture(
        pipeline_hash=data["pipeline_hash"],
        position=data["position"],
        qualified_name=data["qualified_name"],
        node_kind=data["node_kind"],
        reads=data["reads"],
        writes=data.get("writes"),
        service_input=data.get("service_input"),
        service_output=data.get("service_output"),
    )


def _fixture_filename(fixture: SeamFixture) -> str:
    safe = fixture.qualified_name.replace("/", "_").replace("\\", "_")
    return f"{fixture.position:03d}_{safe}.json"


def _to_json(fixture: SeamFixture) -> dict[str, object]:
    return {
        "pipeline_hash": fixture.pipeline_hash,
        "position": fixture.position,
        "qualified_name": fixture.qualified_name,
        "node_kind": fixture.node_kind,
        "reads": dict(fixture.reads),
        "writes": None if fixture.writes is None else dict(fixture.writes),
        "service_input": None if fixture.service_input is None else dict(fixture.service_input),
        "service_output": None if fixture.service_output is None else dict(fixture.service_output),
    }
