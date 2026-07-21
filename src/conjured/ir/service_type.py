"""Service-type declaration IR.

Owned by ``conjured/docs/components/service-type/reference.md`` (§ Service-type TOML
grammar; R-service-type-001 closed grammar). A service-type is a declared contract for
an external dependency the engine calls; a service handler and a trainable composition
node each bind exactly one.

The three schema sections partition along one axis — *does this value shape the
pipeline, or is it per-environment?*: ``identity_schema`` + ``config_schema`` shape the
pipeline (fold into the pipeline-hash); ``transport_schema`` is per-environment (never
hashed). That hash placement is validation/hashing (later phases); the IR carries the
declared sections.

``nullable`` is admitted only on ``transport_schema`` fields (an absent value is a
meaningful per-deployment state); that restriction is a Phase-1a check — the IR carries
an ``OptionalType`` on whichever field declared one. The ``invoke()`` signature contract
(closed dispatch-kwargs + config kwargs + ``**transport_extra``) is *adapter code*, not
a declaration, and is out of Phase 0.
"""

from __future__ import annotations

from typing import Mapping

from conjured.ir.base import IRModel
from conjured.ir.channel_types import FieldDecl


class ServiceTypeDeclaration(IRModel):
    """A service-type declaration — its qualified name and its three schema sections."""

    name: str  # qualified identifier, e.g. "conjured_llm.structured_output" (required)
    description: str | None = None  # one-sentence; load-bearing for trainable derivables
    identity_schema: tuple[FieldDecl, ...]  # required, body-required; folds into pipeline-hash
    transport_schema: tuple[FieldDecl, ...]  # required, body-required; never hashed (nullable allowed here)
    config_schema: tuple[FieldDecl, ...] = ()  # required, empty-allowed; generation-param kwargs; hashed
    annotations: Mapping[str, object] | None = None  # engine-opaque
