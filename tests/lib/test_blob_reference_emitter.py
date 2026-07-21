"""``conjured.lib.blob_reference_emitter`` — the native blob-reference rendering hook,
tested at the Phase-2 seams:

- the shipped ``[hook]`` TOML declares the stdlib-emission shape (one required
  ``reference: str`` port, a non-empty ``transport_schema`` carrying ``format``, zero
  ``service_bindings``);
- the shipped ``emit`` function, dispatched through the real runner ``construct()`` path
  as a hook, reads the wired reference, emits it via stdlib ``logging`` (asserted against
  the captured log record), and returns ``None`` — for both the ``plain`` and ``json``
  record formats;
- the hook **kind contract** rejects a non-``None`` return as a
  ``HOOK_RETURN_NOT_NONE`` ContractViolation (the runner's path, exercised against this
  member's wiring — RED if the dispatch hook None-check is removed).

No mocking of engine internals: the dispatch wrapper is the real ``construct()`` and the
emission is real stdlib logging captured by ``caplog``.
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path

import pytest

import conjured.lib
from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import primitive
from conjured.ir.graph import GraphNode, Port
from conjured.ir.handler import HookDeclaration
from conjured.lib.blob_reference_emitter import LOGGER_NAME, emit
from conjured.runner.dispatch import DispatchContext, construct
from conjured.validator.model_gen import build_model
from conjured.validator.parse import parse_handler
from conjured.validator.resolve_handler import HandlerEntry

QUALIFIED_NAME = "conjured.lib.blob_reference_emitter.emit"
TOML_PATH = Path(conjured.lib.__file__).parent / "blob_reference_emitter.toml"
CTX = DispatchContext(pipeline_run_id="run_2026-06-28T00:00:00Z_blob", handler_position=3)
REFERENCE = "blobs/3f2a/portrait.png"


def _declaration() -> HookDeclaration:
    data = tomllib.loads(TOML_PATH.read_text(encoding="utf-8"))
    return parse_handler(data, file_path="handlers/blob_reference_emitter.toml")


def _dispatch(fn, decl, *, transport_format="plain"):
    """The real runner dispatch wrapper for ``fn`` as a hook, driven by the shipped
    declaration's ``reads`` ports + its single ``format`` transport field."""
    reads_model = build_model("Reads", decl.reads)
    input_ports = tuple(Port(name=f.name, type=f.type) for f in decl.reads)
    node = GraphNode(
        position=CTX.handler_position,
        node_kind="hook",
        qualified_name=QUALIFIED_NAME,
        input_ports=input_ports,
        output_ports=(),
        read_map={p.name: p.name for p in input_ports},
        write_map={},
    )
    entry = HandlerEntry(
        qualified_name=QUALIFIED_NAME,
        callable=fn,
        kind="hook",
        package="conjured",
        toml_path=TOML_PATH,
    )
    return construct(
        entry, node, reads_model, None, (), hook_transport={"format": transport_format}
    )


def test_shipped_toml_declares_a_stdlib_emission_hook():
    """The shipped declaration is a hook with exactly one required ``reference: str``
    port, a non-empty ``transport_schema`` (the stdlib-emission rule), and zero
    ``service_bindings`` (R-handler-007 stdlib clause / R-handler-009 zero-entry case)."""
    decl = _declaration()
    assert isinstance(decl, HookDeclaration)
    assert [f.name for f in decl.reads] == ["reference"]
    assert decl.reads[0].type == primitive("str")
    assert [f.name for f in decl.transport_schema] == ["format"]  # non-empty per stdlib
    assert decl.service_bindings == ()  # stdlib-emission: no service-typed binding


def test_emit_logs_the_reference_plain_and_returns_none(caplog):
    """The real ``emit``, dispatched through the runner ``construct()`` path as a hook,
    reads the wired reference, emits it via stdlib logging (``plain`` format), and
    returns ``None``. RED if ``emit`` stops emitting or stops returning ``None``."""
    dispatch = _dispatch(emit, _declaration(), transport_format="plain")
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = dispatch(reads={"reference": REFERENCE}, ctx=CTX)
    assert result is None
    record = caplog.records[-1]
    assert record.blob_reference == REFERENCE          # emitted as a structured field
    assert record.getMessage() == f"blob_reference={REFERENCE}"


def test_emit_json_format_emits_parseable_record(caplog):
    """The ``json`` record format emits a one-key JSON object carrying the reference —
    the other happy branch of the ``format`` transport selector."""
    dispatch = _dispatch(emit, _declaration(), transport_format="json")
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = dispatch(reads={"reference": REFERENCE}, ctx=CTX)
    assert result is None
    record = caplog.records[-1]
    assert json.loads(record.getMessage()) == {"blob_reference": REFERENCE}


def _emit_returning_reference(*, reference, format):
    """The plausible member regression the hook kind contract defends against: emit the
    reference AND return it (so a downstream node could read it) — which a hook must not
    do, because it writes no channels and the runner has no merge path for a return."""
    logging.getLogger(LOGGER_NAME).info("blob_reference=%s", reference)
    return reference


def test_hook_kind_contract_rejects_a_non_none_return():
    """A hook returning non-``None`` is a ``HOOK_RETURN_NOT_NONE`` ContractViolation at
    dispatch (the runner's path). Exercises the kind contract against this member's
    wiring — RED if the dispatch hook None-check is removed."""
    dispatch = _dispatch(_emit_returning_reference, _declaration())
    with pytest.raises(ContractViolation) as exc:
        dispatch(reads={"reference": REFERENCE}, ctx=CTX)
    assert exc.value.check is Check.HOOK_RETURN_NOT_NONE
    assert exc.value.pipeline_run_id == CTX.pipeline_run_id
