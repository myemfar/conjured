"""Deployment declaration IR.

Owned by ``conjured/docs/components/deployment/reference.md`` (§ Deployment-TOML
grammar; R-deployment-001 / R-deployment-002). The integrator-authored declaration that
wires a composed pipeline to a concrete environment — one per engine process, shared by
every pipeline the engine runs, resolved by binding name. **Every section is excluded
from both hashes** (environment properties, never composition properties); the exclusion
is hashing logic (a later phase), not modeled here.

Section disciplines (carried as content shape; presence discipline is Phase 1a):
``transport.<name>`` and ``hook_transport."<qn>"`` are wiring; ``training_contract`` is
required, body-required (an explicit ``integrity_enforcement`` boolean, plus the optional
``audit_enforcement`` boolean defaulting to false);
``training_export`` is truly optional and *presence-toggling* (modeled as ``None`` =
absent vs a present mapping); ``acknowledged_drift`` and ``annotations`` are truly
optional; ``pipelines.<name>`` re-wires transport / hook_transport for one named pipeline.
"""

from __future__ import annotations

from typing import Mapping

from conjured.ir.base import IRModel


class TransportBlock(IRModel):
    """A ``transport.<name>`` block — per-deployment transport for binding handle
    ``name``; strict-validated against the bound service-type's ``transport_schema``
    (Phase 1a). Reaches the implementation as ``**transport_extra``; never hashed.
    """

    name: str  # pipeline-local binding name
    values: Mapping[str, object] = {}  # transport field values (endpoint, credential ref, timeout, …)


class HookTransportBlock(IRModel):
    """A ``hook_transport."<qualified_name>"`` block — per-deployment transport for one
    hook; strict-validated against the hook's own ``transport_schema`` (Phase 1a). A hook
    declaring zero transport fields still requires its block empty-but-present.
    """

    hook_qualified_name: str
    values: Mapping[str, object] = {}


class TrainingContract(IRModel):
    """The ``[training_contract]`` block — the enforcement-posture opt-ins.
    ``integrity_enforcement`` is an explicit boolean (no default); an empty body or
    missing field is a ContractViolation at deployment load (a Phase-1a check).
    ``audit_enforcement`` is its **optional** boolean sibling defaulting to ``false`` —
    the opt-in to audit-stamp enforcement (deployment/reference.md § training_contract,
    region ``training-contract-section/audit-enforcement``; the stamp mechanism +
    freshness states are owned by handler/reference.md § Audit stamps). Declaring it true
    makes compose refuse any not-fresh in-scope module (handler / adapter / validator); its
    semantics are enforced at resolution (``validator.audit_stamp``). Never hashed —
    an environment property, not a composition property.
    """

    integrity_enforcement: bool
    audit_enforcement: bool = False


class PipelineOverride(IRModel):
    """A ``pipelines.<name>`` per-pipeline override — re-wires ``transport`` /
    ``hook_transport`` for one named pipeline that diverges from the shared wiring. Only
    transport / hook_transport accept override (the environment-posture sections are
    deployment-wide).
    """

    pipeline_qualified_name: str
    transport: tuple[TransportBlock, ...] = ()
    hook_transport: tuple[HookTransportBlock, ...] = ()


class DeploymentDeclaration(IRModel):
    """A deployment declaration — the environment configuration of one engine process."""

    transport: tuple[TransportBlock, ...] = ()  # one per service-typed binding
    hook_transport: tuple[HookTransportBlock, ...] = ()  # one per hook
    training_contract: TrainingContract  # required, body-required
    #: ``[training_export]`` — truly optional; its *presence* toggles capture routing.
    #: ``None`` = absent (no routing); a present mapping (possibly empty) = routing on.
    training_export: Mapping[str, object] | None = None
    #: ``[artifacts]`` — trainable composition name (the trained-artifact-manifest key) ->
    #: artifact file path this environment serves it from. Truly optional (empty = no
    #: artifact loads declared — nothing to compare). The engine reads only the artifact's
    #: sidecar manifest, never the artifact itself (deployment/reference.md § artifacts;
    #: the comparison is R-pipeline-003's, run at deployment load).
    artifacts: Mapping[str, str] = {}
    #: ``[acknowledged_drift]`` — artifact path -> list of trainable qualified names whose
    #: drift it accepts. Truly optional (empty = no acknowledgments).
    acknowledged_drift: Mapping[str, tuple[str, ...]] = {}
    annotations: Mapping[str, object] | None = None  # engine-opaque
    pipelines: tuple[PipelineOverride, ...] = ()  # per-pipeline overrides
