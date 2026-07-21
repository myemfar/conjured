"""Shared builders for the server suite — real compiled+assembled pipelines.

Every server test drives a **real** ``Runnable`` through the real engine runner over the
HTTP surface (never a mock — the boundary-exercise discipline). These helpers wrap the
``conjured.testing`` load path (registry → ``compile_pipeline`` → ``assemble``) so a test
goes from a one-line handler body to a served ``Runnable``. ``conjured_registry`` and
``module_writer`` come from the shipped ``conjured.testing`` pytest plugin. The builders are
exposed as **fixtures** (``fd`` / ``make_runnable``) so tests need no cross-module import.
"""

from __future__ import annotations

import textwrap

import pytest

from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.handler import TransformDeclaration
from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
from conjured.server import create_app
from conjured.testing import load_test_pipeline


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


def _build_runnable(
    registry,
    module_writer,
    *,
    module_name: str,
    fn_name: str,
    src: str,
    pipeline_name: str,
    reads: tuple[FieldDecl, ...],
    outputs: tuple[FieldDecl, ...],
    inputs: tuple[FieldDecl, ...],
    pipeline_outputs: tuple[FieldDecl, ...] | None = None,
):
    """Write a real handler module, register a one-node transform pipeline, compile +
    assemble into a ``Runnable``."""
    mod = module_writer(module_name, textwrap.dedent(src))
    qn = f"{mod}.{fn_name}"
    registry.add_handler(
        qn,
        TransformDeclaration(reads=reads, output_schema=outputs),
        toml_path=f"handlers/{fn_name}.toml",
    )
    declaration = PipelineDeclaration(
        meta=PipelineMeta(name=pipeline_name),
        nodes=(HandlerNode(name=qn),),
        inputs=inputs,
        outputs=pipeline_outputs if pipeline_outputs is not None else outputs,
    )
    return load_test_pipeline(declaration, registry)


@pytest.fixture
def fd():
    """The ``FieldDecl`` builder, ``fd(name, token="str")``."""
    return _fd


@pytest.fixture
def make_runnable(conjured_registry, module_writer):
    """A builder bound to this test's fresh registry + module writer; takes the same
    keyword args as :func:`_build_runnable` minus the first two."""
    def _make(**kwargs):
        return _build_runnable(conjured_registry, module_writer, **kwargs)
    return _make


@pytest.fixture
def echo_runnable(conjured_registry, module_writer):
    """``srv.echo`` — one ``str`` input ``text`` → one ``str`` output ``result`` (upper)."""
    return _build_runnable(
        conjured_registry, module_writer,
        module_name="srv_echo_mod", fn_name="echo",
        src="def echo(*, text):\n    return {'result': text.upper()}\n",
        pipeline_name="srv.echo",
        reads=(_fd("text"),), outputs=(_fd("result"),), inputs=(_fd("text"),),
    )


@pytest.fixture
def echo_app(echo_runnable):
    return create_app({echo_runnable.pipeline_name: echo_runnable})
