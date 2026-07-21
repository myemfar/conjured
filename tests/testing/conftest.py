"""Test fixtures for the conjured.testing dogfood suite.

The library's own pytest plugin (``conjured.testing.plugin``) is the canonical surface a consumer
gets via the ``pytest11`` entry-point. The engine's own suite runs from source (``PYTHONPATH``, not an
install that would refresh entry-point metadata), so the two plugin fixtures are imported here
directly — dogfooding the same fixture functions a consumer would get, scoped to this directory, with
no global effect on the rest of the suite.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.handler import TransformDeclaration
from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
from conjured.testing import load_test_pipeline
from conjured.testing.plugin import conjured_registry, module_writer  # noqa: F401 — re-exported as fixtures


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


@dataclass
class Chain:
    """A built two-transform chain (text -> mid -> out) plus its registry and node names."""

    runnable: object
    registry: object
    module: str
    first_qn: str
    second_qn: str


@pytest.fixture
def chain(conjured_registry, module_writer) -> Chain:  # noqa: F811 — fixture args
    """A real compiled+assembled two-transform chain, built through ``load_test_pipeline`` — the
    standard fixture the verification/harvest tests dispatch."""
    module = module_writer(
        "testlib_chain_mod",
        """
        def first(*, text):
            return {"mid": text.upper()}

        def second(*, mid):
            return {"out": mid + "!"}
        """,
    )
    first_qn, second_qn = f"{module}.first", f"{module}.second"
    conjured_registry.add_handler(
        first_qn, TransformDeclaration(reads=(_fd("text"),), output_schema=(_fd("mid"),)),
        toml_path="handlers/first.toml",
    )
    conjured_registry.add_handler(
        second_qn, TransformDeclaration(reads=(_fd("mid"),), output_schema=(_fd("out"),)),
        toml_path="handlers/second.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="testlib.chain"),
        nodes=(HandlerNode(name=first_qn), HandlerNode(name=second_qn)),
        inputs=(_fd("text"),),
        outputs=(_fd("out"),),
    )
    runnable = load_test_pipeline(pipeline, conjured_registry)
    return Chain(runnable=runnable, registry=conjured_registry, module=module,
                 first_qn=first_qn, second_qn=second_qn)
