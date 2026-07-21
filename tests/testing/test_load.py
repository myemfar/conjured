"""load_test_pipeline / load_test_deployment — compile+assemble a composition for testing."""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.deployment import DeploymentDeclaration
from conjured.ir.handler import TransformDeclaration
from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
from conjured.runner.run import run
from conjured.testing import load_test_deployment, load_test_pipeline


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


def test_load_test_pipeline_from_declaration_runs(chain):
    # The chain fixture is built via load_test_pipeline(PipelineDeclaration, ...); assert it dispatches.
    assert dict(run(chain.runnable, {"text": "hi"}).state) == {"mid": "HI", "out": "HI!"}


def test_load_test_pipeline_from_toml_string(conjured_registry, module_writer):
    module = module_writer(
        "tl_toml_mod",
        "def first(*, text):\n    return {'mid': text.upper()}\n"
        "def second(*, mid):\n    return {'out': mid + '!'}\n",
    )
    conjured_registry.add_handler(
        f"{module}.first", TransformDeclaration(reads=(_fd("text"),), output_schema=(_fd("mid"),)),
        toml_path="h1.toml",
    )
    conjured_registry.add_handler(
        f"{module}.second", TransformDeclaration(reads=(_fd("mid"),), output_schema=(_fd("out"),)),
        toml_path="h2.toml",
    )
    toml = """
[meta]
name = "testlib.toml_chain"
[[nodes]]
kind = "handler"
name = "MOD.first"
[[nodes]]
kind = "handler"
name = "MOD.second"
[inputs]
text = { type = "str" }
[outputs]
out = { type = "str" }
""".replace("MOD", module)
    runnable = load_test_pipeline(toml, conjured_registry)
    assert dict(run(runnable, {"text": "hey"}).state) == {"mid": "HEY", "out": "HEY!"}


def test_load_test_deployment_from_toml():
    deployment = load_test_deployment(
        '[transport.llm]\nendpoint = "https://llm/v1"\n[training_contract]\nintegrity_enforcement = false\n'
    )
    assert isinstance(deployment, DeploymentDeclaration)
    assert deployment.training_contract.integrity_enforcement is False  # the value actually parsed


def test_load_test_deployment_passthrough():
    deployment = load_test_deployment(
        '[transport.llm]\nendpoint = "https://llm/v1"\n[training_contract]\nintegrity_enforcement = false\n'
    )
    assert load_test_deployment(deployment) is deployment


def test_load_test_pipeline_malformed_toml_raises(conjured_registry):
    with pytest.raises(ContractViolation):
        load_test_pipeline("this is not = valid [[ pipeline toml", conjured_registry)


def test_load_test_deployment_malformed_toml_raises():
    with pytest.raises(ContractViolation):
        load_test_deployment("this is not = valid [[ deployment toml")


def test_load_test_pipeline_surfaces_a_compile_stage_contract_violation(conjured_registry):
    # A well-formed pipeline (parses clean) that references an UNREGISTERED handler fails at the
    # COMPILE stage — load_test_pipeline runs compile+assemble, so the compose-time ContractViolation
    # surfaces exactly as in production (no swallow), distinct from the parse-stage malformed-TOML
    # raise above (33#2 — the parse arm was covered, the compile arm was not).
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="tl.compile_fail"),
        nodes=(HandlerNode(name="nope.unregistered"),),
        inputs=(_fd("text"),), outputs=(_fd("out"),),
    )
    with pytest.raises(ContractViolation) as exc:
        load_test_pipeline(pipeline, conjured_registry)
    assert exc.value.check is Check.HANDLER_NAME_RESOLUTION
