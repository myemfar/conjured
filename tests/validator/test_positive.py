"""Positive acceptance — canon-example declaration sets compile cleanly to the expected
``CompiledGraph``."""

from __future__ import annotations

import pytest

from conjured.errors import ContractViolation
from conjured.ir.graph import CompiledGraph
from conjured.validator import compile_pipeline
from conjured.validator.tokens import parse_type_token

from .fixtures import NAME, build_base, build_trainable


def test_handler_pipeline_compiles_to_expected_graph():
    reg, pipeline, deployment = build_base()
    graph = compile_pipeline(pipeline, reg, pipeline_name=NAME, deployment=deployment, file_path="p.toml")

    assert isinstance(graph, CompiledGraph)
    # Nodes in declared order; position is identity; node_kind from the resolved declaration.
    assert [(n.position, n.node_kind, n.qualified_name) for n in graph.nodes] == [
        (0, "transform", "acme.normalize"),
        (1, "service", "acme.respond"),
        (2, "hook", "acme.log"),
    ]
    # The hook writes no channels (empty output ports).
    assert graph.nodes[2].output_ports == ()
    # Maps are total + normalized (every port mapped, identity-desugared).
    assert graph.nodes[0].write_map == {"normalized_input": "normalized_input"}
    assert graph.nodes[1].read_map == {"normalized_input": "normalized_input"}
    # Channels are typed by the agreed port type.
    assert {c.name for c in graph.channels} == {"player_input", "normalized_input", "dialogue"}
    assert [f.name for f in graph.inputs] == ["player_input"]
    assert [f.name for f in (graph.outputs or ())] == ["dialogue"]
    assert graph.merges == ()  # no multi-writer channel


def test_compiles_without_a_deployment():
    """The graph compiles without a paired deployment; coverage checks are gated on one."""
    reg, pipeline, _ = build_base()
    graph = compile_pipeline(pipeline, reg, pipeline_name=NAME, file_path="p.toml")
    assert len(graph.nodes) == 3


def test_trainable_composition_flattens_with_scoped_channels():
    reg, pipeline = build_trainable()
    graph = compile_pipeline(pipeline, reg, pipeline_name=NAME, file_path="p.toml")

    # Preprocessors + the terminal trainable become nodes; the embed itself is not a node.
    assert [(n.position, n.node_kind, n.qualified_name) for n in graph.nodes] == [
        (0, "transform", "acme.ctx"),
        (1, "transform", "dialogue_training.assemble_prompt"),
        (2, "trainable", "dialogue_training"),
    ]
    # Internal channel is scoped; boundary inputs/outputs stay outer.
    channel_names = {c.name for c in graph.channels}
    assert "dialogue_training.formatted_prompt" in channel_names
    assert {"npc_state", "user_message", "dialogue_response", "raw"} <= channel_names
    # The trainable writes the boundary output unscoped; reads the scoped internal channel.
    trainable = graph.nodes[2]
    assert trainable.write_map == {"dialogue_response": "dialogue_response"}
    assert trainable.read_map == {"formatted_prompt": "dialogue_training.formatted_prompt"}


def test_reference_binding_and_merge_compile():
    """A two-writer channel with a declared merge of the matching type compiles; the merge is
    a MergeOp, not a node (R-pipeline-002; runner-operation-not-a-node)."""
    from conjured.validator import DeclarationRegistry, loads

    reg = DeclarationRegistry()
    reg.add_handler("acme.a", loads(
        '[transform]\n[reads]\nx={type="str"}\n[output_schema]\nlog={type="list[str]"}',
        "handler", file_path="a.toml"))
    reg.add_handler("acme.b", loads(
        '[transform]\n[reads]\nx={type="str"}\n[output_schema]\nlog={type="list[str]"}',
        "handler", file_path="b.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n'
        '[[nodes]]\nkind="handler"\nname="acme.a"\n'
        '[[nodes]]\nkind="handler"\nname="acme.b"\n'
        '[merge]\nlog="append_list"\n'
        '[inputs]\nx={type="str"}\n',
        "pipeline", file_path="p.toml")
    graph = compile_pipeline(pipeline, reg, pipeline_name=NAME, file_path="p.toml")
    assert len(graph.merges) == 1
    assert graph.merges[0].channel == "log"
    assert graph.merges[0].strategy.value == "append_list"


@pytest.mark.parametrize("token,kind", [
    ("str", "primitive"), ("int", "primitive"), ("float", "primitive"), ("bool", "primitive"),
    ("list[str]", "list"), ("dict[str, int]", "dict"), ("tuple[int, str]", "tuple"),
    ("str | None", "optional"), ("Literal['a', 'b']", "literal"),
    ("Literal[1, 2]", "literal"), ("Literal[true, false]", "literal"),
    ("list[list[str]]", "list"), ("dict[str, list[int]]", "dict"),
])
def test_parse_type_token_valid_forms(token, kind):
    """Positive-coverage twin of the negative meta-tests: every VALID channel-type token form
    parses (no raise) to the right normalized descriptor kind. `parse_type_token` is a
    multi-branch leaf (primitive / list / dict / tuple / optional / literal + recursion) and each
    happy branch gets a case — the systematic happy-path counterpart to the per-check negatives
    (handler/reference.md § Types allowed; `bytes` is exercised by the fuzz suite and the empty
    forms by the negative suite)."""
    desc = parse_type_token(token, file_path="t.toml")
    assert desc.kind == kind


def test_literal_token_member_value_space():
    """A `Literal` member is a string, int, or bool — the scalar types Python's `Literal` admits and
    the engine's IR carries; `float` is NOT a valid member (handler/reference.md § Enums). Each member
    parses to its native Python type (int/bool are native TOML primitives, so all three are reachable
    from a TOML-authored token), and a `float` member is a token error."""
    desc = parse_type_token("Literal['a', 1, true, false]", file_path="t.toml")
    assert desc.values == ("a", 1, True, False)
    # bool before int: a bool member must stay bool, not collapse to 1/0.
    assert [type(v) for v in desc.values] == [str, int, bool, bool]

    with pytest.raises(ContractViolation):
        parse_type_token("Literal[1.5]", file_path="t.toml")
