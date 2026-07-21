"""Build a testable composition — compile + assemble a pipeline into a ``Runnable``.

The contract: ``conjured/docs/components/testing/reference.md`` names ``load_test_pipeline`` /
``load_test_deployment`` among the seam helpers; their shape is authored here with the code.

The engine has no disk/directory pipeline loader — a registry is assembled by hand
(``DeclarationRegistry`` + ``add_*``), then ``compile_pipeline`` → ``assemble`` produces the
``Runnable`` the engine runner dispatches. These helpers wrap that path so a test goes from a
pipeline declaration (in-memory IR or TOML) plus a populated registry to a ``Runnable`` in one call,
without re-deriving the compile→assemble sequence. They invent no directory layout: the caller owns
how its handler/service-type declarations get into the registry.
"""

from __future__ import annotations

import os
from pathlib import Path

from conjured.ir.deployment import DeploymentDeclaration
from conjured.ir.pipeline import PipelineDeclaration
from conjured.runner.assemble import Runnable, assemble
from conjured.validator import compile_pipeline, loads
from conjured.validator.registry import DeclarationRegistry


def load_test_pipeline(
    pipeline: PipelineDeclaration | str | os.PathLike[str],
    registry: DeclarationRegistry,
    *,
    name: str | None = None,
    deployment: DeploymentDeclaration | None = None,
    file_path: str = "<test-pipeline>",
) -> Runnable:
    """Compile and assemble ``pipeline`` against ``registry`` into a ``Runnable`` ready for the
    engine runner.

    ``pipeline`` is a ``PipelineDeclaration`` (in-memory IR), a TOML **string**, or a
    ``Path``/PathLike to a TOML file. ``name`` defaults to the declaration's ``meta.name``
    (always present — the IR requires it); an explicit ``name=`` overrides it. ``deployment`` (e.g. from
    :func:`load_test_deployment`) supplies transport / hook-transport / training-contract config; it
    is threaded into both ``compile_pipeline`` and ``assemble``. Compose-time failures raise
    ``ContractViolation`` before any dispatch, exactly as in production — the helper adds no
    swallowing.
    """
    if isinstance(pipeline, PipelineDeclaration):
        declaration = pipeline
    else:
        if isinstance(pipeline, os.PathLike):
            file_path = os.fspath(pipeline)
            text = Path(pipeline).read_text(encoding="utf-8")
        else:
            text = pipeline
        declaration = loads(text, "pipeline", file_path=file_path)

    # PipelineDeclaration.meta.name is a required field (and parse enforces it for the TOML path), so
    # meta.name is always present; an explicit name= overrides it.
    resolved_name = name or declaration.meta.name
    graph = compile_pipeline(
        declaration,
        registry,
        pipeline_name=resolved_name,
        deployment=deployment,
        file_path=file_path,
    )
    return assemble(graph, registry, deployment)


def load_test_deployment(
    deployment: DeploymentDeclaration | str | os.PathLike[str],
    *,
    file_path: str = "<test-deployment>",
) -> DeploymentDeclaration:
    """Parse a deployment declaration for use as the ``deployment=`` argument of
    :func:`load_test_pipeline`.

    A deployment has no compile step of its own — it is parsed and handed to ``compile_pipeline`` /
    ``assemble`` — so this is a thin parse wrapper accepting a ``DeploymentDeclaration`` (returned
    unchanged), a TOML string, or a TOML file path. A malformed declaration raises
    ``ContractViolation``.
    """
    if isinstance(deployment, DeploymentDeclaration):
        return deployment
    if isinstance(deployment, os.PathLike):
        file_path = os.fspath(deployment)
        text = Path(deployment).read_text(encoding="utf-8")
    else:
        text = deployment
    return loads(text, "deployment", file_path=file_path)
