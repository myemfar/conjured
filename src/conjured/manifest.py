"""``conjured.manifest`` — the trained-artifact manifest: sidecar load + the
integrity comparison (R-pipeline-003).

The pipeline component owns the manifest comparison logic (``hash-model.md`` § Where
this lives in the engine); this module is that logic's home. Two halves:

- :func:`load_manifest` — read + validate one sidecar
  ``<artifact>.conjured.toml`` into a typed :class:`TrainedArtifactManifest`
  (closed required-field set per ``pipeline/reference.md`` § Full manifest field
  set; fail loud on a malformed artifact — the audit-stamp posture: malformed is
  never coerced to absent).
- :func:`verify_artifacts` — the deployment-load comparison over the deployment's
  ``[artifacts]`` registrations (``deployment/reference.md`` § artifacts):
  recomputed pipeline-hash + per-trainable training-bundle-hashes vs the recorded
  values, per ``hash-model.md`` § Integrity-enforcement opt-in. The **property**
  half always runs where a manifest exists — the drift events
  (``training_bundle_hash_changed`` / ``pipeline_hash_changed``) fire on every
  mismatch under either enforcement mode; the **enforcement** half (halts,
  graduated per hash class, ``acknowledged_drift``-acknowledgeable at the
  trainable grain) is gated on ``training_contract.integrity_enforcement``.

Called from stage-4 assembly (``runner.assemble``) right after the pipeline-hash
is computed — the load-lifecycle stage-3 comparison seam as this codebase realizes
it (assemble is where the hash exists and the registry + deployment are in hand).

``pipeline_hash_changed.old_pipeline_hash`` for a multi-element
``pipeline_hash_set`` is the manifest's FIRST recorded hash (declaration order) —
the single-element set is the common case and exact; for a variant-spanning set
the first element is the stable representative of the recorded baseline (the full
set rides the manifest itself, which the event consumer can read).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from conjured import events
from conjured.errors import Check, ContractViolation
from conjured.hasher.hashes import training_bundle_hash
from conjured.ir.composition import PipelineComposition, TrainableComposition
from conjured.ir.deployment import DeploymentDeclaration
from conjured.ir.pipeline import CompositionNode, PipelineDeclaration
from conjured.ir.substitute import substitute_bundle_nodes

#: The sidecar naming convention (pipeline/reference.md § Trained-artifact manifest):
#: an artifact at ``loras/x.safetensors`` pairs with ``loras/x.safetensors.conjured.toml``.
SIDECAR_SUFFIX = ".conjured.toml"

#: The required ``[manifest]`` field set (pipeline/reference.md § Full manifest field
#: set). ``generator_info`` is conditional (present iff the source includes generated
#: pairs) and validated for presence only — its internal shape is provenance the engine
#: does not validate beyond TOML parsing.
_MANIFEST_REQUIRED = (
    "artifact", "pipeline_hash_set", "base_model", "artifact_format",
    "trained_at", "training_data_source",
)

#: The closed ``training_data_source`` enum (pipeline/reference.md § training_data_source).
_TRAINING_DATA_SOURCES = frozenset({"generated", "captured", "external", "mixed"})

#: The sources whose corpus includes generated pairs — ``generator_info`` required.
_GENERATED_SOURCES = frozenset({"generated", "mixed"})


@dataclass(frozen=True, slots=True)
class TrainedArtifactManifest:
    """One loaded sidecar manifest — the typed record of the wire form hash-model's
    § Trained-artifact manifest specifies, plus its source path for diagnostics."""

    artifact: str
    pipeline_hash_set: tuple[str, ...]
    base_model: str
    artifact_format: str
    trained_at: str
    training_data_source: str
    training_bundle_hashes: Mapping[str, str]
    generator_info: Mapping[str, object] | None
    source_path: str


def sidecar_path(artifact_path: str | Path) -> Path:
    """The sidecar manifest path for an artifact — the adjacent-file convention
    (``<artifact>.conjured.toml``, same directory)."""
    return Path(str(artifact_path) + SIDECAR_SUFFIX)


def _malformed(*, source: str, actual: str, hint: str) -> ContractViolation:
    # guarantees: manifest-malformed-fails-loud
    return ContractViolation(
        check=Check.TRAINED_ARTIFACT_MANIFEST_MALFORMED,
        rule_id="R-pipeline-003",
        expected=(
            "a well-formed trained-artifact manifest sidecar: a [manifest] table carrying "
            f"the required fields {list(_MANIFEST_REQUIRED)} (each a well-typed value; "
            "training_data_source in the closed enum; generator_info present iff the "
            "source includes generated pairs) plus a [training_bundle_hashes] table of "
            "string hashes (pipeline/reference.md § Full manifest field set)"
        ),
        actual=actual,
        remediation_hint=hint,
        file_path=source,
    )


def load_manifest(path: str | Path) -> TrainedArtifactManifest:
    """Read + validate one sidecar manifest. A file that is unreadable, is not valid
    UTF-8 TOML, omits a required field, or carries a mistyped / out-of-enum field is
    **malformed** — a structured ``ContractViolation``, never coerced to absent and
    never a raw parse exception. Existence is the CALLER's split (absent is the
    enforcement-gated state, distinct by design)."""
    source = Path(path).as_posix()
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise _malformed(
            source=source,
            actual=f"the sidecar is unreadable ({type(exc).__name__}: {exc})",
            hint="make the sidecar readable (permissions?) and re-load",
        ) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise _malformed(
            source=source,
            actual=f"the sidecar is not valid UTF-8 TOML ({type(exc).__name__}: {exc})",
            hint="regenerate the sidecar with conjured artifact-tag --force",
        ) from exc

    manifest_raw = data.get("manifest")
    if not isinstance(manifest_raw, Mapping):
        raise _malformed(
            source=source,
            actual="no [manifest] table",
            hint="the sidecar's hash-bearing envelope is the [manifest] table; regenerate "
                 "with conjured artifact-tag --force",
        )
    missing = [f for f in _MANIFEST_REQUIRED if f not in manifest_raw]
    if missing:
        raise _malformed(
            source=source,
            actual=f"[manifest] omits required field(s) {missing}",
            hint="regenerate with conjured artifact-tag --force, or restore the field(s)",
        )
    for field in ("artifact", "base_model", "artifact_format", "trained_at", "training_data_source"):
        if not isinstance(manifest_raw[field], str) or not manifest_raw[field]:
            raise _malformed(
                source=source,
                actual=f"[manifest].{field} is {manifest_raw[field]!r}, not a non-empty string",
                hint=f"'{field}' MUST be a non-empty string",
            )
    source_kind = manifest_raw["training_data_source"]
    if source_kind not in _TRAINING_DATA_SOURCES:
        raise _malformed(
            source=source,
            actual=f"[manifest].training_data_source is {source_kind!r}, outside the closed "
                   f"enum {sorted(_TRAINING_DATA_SOURCES)}",
            hint="training_data_source is the closed provenance enum "
                 "(pipeline/reference.md § training_data_source enum)",
        )
    hash_set_raw = manifest_raw["pipeline_hash_set"]
    if (
        not isinstance(hash_set_raw, list)
        or not hash_set_raw
        or not all(isinstance(h, str) and h for h in hash_set_raw)
    ):
        raise _malformed(
            source=source,
            actual=f"[manifest].pipeline_hash_set is {hash_set_raw!r}, not a non-empty "
                   "list of hash strings",
            hint="pipeline_hash_set is the non-empty list of pipeline-hashes the corpus "
                 "came from",
        )
    generator_info = manifest_raw.get("generator_info")
    if source_kind in _GENERATED_SOURCES:
        if not isinstance(generator_info, Mapping):
            raise _malformed(
                source=source,
                actual=f"training_data_source is '{source_kind}' but generator_info is "
                       f"{generator_info!r} (required for a generated corpus)",
                hint="a generated/mixed corpus records its generator provenance "
                     "(pipeline/reference.md § generator_info)",
            )
    elif generator_info is not None and not isinstance(generator_info, Mapping):
        raise _malformed(
            source=source,
            actual=f"generator_info is {generator_info!r}, not a table",
            hint="generator_info, when present, is an inline table",
        )
    tbh_raw = data.get("training_bundle_hashes")
    if not isinstance(tbh_raw, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) and v for k, v in tbh_raw.items()
    ):
        raise _malformed(
            source=source,
            actual=f"[training_bundle_hashes] is {tbh_raw!r}, not a table of "
                   "trainable-name -> hash strings",
            hint="one entry per trainable composition node, keyed by its declared meta "
                 "name (hash-model § Manifest-key shape)",
        )
    return TrainedArtifactManifest(
        artifact=manifest_raw["artifact"],
        pipeline_hash_set=tuple(hash_set_raw),
        base_model=manifest_raw["base_model"],
        artifact_format=manifest_raw["artifact_format"],
        trained_at=manifest_raw["trained_at"],
        training_data_source=source_kind,
        training_bundle_hashes=dict(tbh_raw),
        generator_info=dict(generator_info) if isinstance(generator_info, Mapping) else None,
        source_path=source,
    )


def collect_trainables(
    pipeline: PipelineDeclaration, registry
) -> "tuple[dict[str, TrainableComposition], frozenset[str]]":
    """The deployed pipeline tree's trainable composition nodes, keyed by declared
    ``meta.name`` (the trained-artifact-manifest key) — collected RECURSIVELY through
    nested ``pipeline`` embeds (with bundle nodes substituted first, exactly as every
    hashing walk does), so the deployment's flat ``[artifacts]`` table reaches every
    deployed trainable. Returns ``(name -> composition, duplicate-names)``: a name two
    DISTINCT compositions carry across nesting levels is ambiguous as a flat
    registration key (per-pipeline uniqueness is R-pipeline-001's; cross-level
    uniqueness is not enforced), so the caller refuses a registration against it
    rather than comparing an arbitrary one. Runs over a compile-validated (acyclic)
    tree; unresolvable references were already refused at compose."""
    found: dict[str, TrainableComposition] = {}
    duplicates: set[str] = set()

    def walk(p: PipelineDeclaration) -> None:
        nodes = substitute_bundle_nodes(p.nodes, registry.get_composition, where=p.meta.name)
        for node in nodes:
            if not isinstance(node, CompositionNode):
                continue
            comp = registry.get_composition(node.name)
            if isinstance(comp, TrainableComposition):
                name = comp.meta.name
                if name in found and found[name] is not comp:
                    duplicates.add(name)
                found[name] = comp
            elif isinstance(comp, PipelineComposition):
                walk(comp.pipeline)

    walk(pipeline)
    return found, frozenset(duplicates)


def verify_artifacts(
    *,
    deployment: DeploymentDeclaration,
    deployment_dir: Path | None,
    pipeline_name: str,
    pipeline_hash: str,
    trainables: Mapping[str, TrainableComposition],
    registry,
    duplicate_names: frozenset[str] = frozenset(),
) -> None:
    """The R-pipeline-003 comparison over every ``[artifacts]`` registration.

    ``trainables`` maps the deployed pipeline's trainable composition nodes by their
    declared name (the manifest key); ``deployment_dir`` anchors relative artifact
    paths (``None`` falls back to the process CWD — the deployment's own directory is
    the canon anchor and assemble threads it when the declaration's path is known).

    Per registration, in order: unknown trainable → ContractViolation (either mode);
    absent sidecar → ContractViolation iff ``integrity_enforcement`` (else the
    no-baseline case: no comparison, no event); malformed sidecar → ContractViolation
    (either mode, via :func:`load_manifest`); then the comparison — per-trainable TBH
    equality and pipeline-hash set-membership — firing the drift events on every
    mismatch (either mode) and halting per the graduated force only under
    enforcement: TBH mismatch halts unless ``acknowledged_drift`` covers the artifact
    + trainable; a pipeline-hash-only mismatch never halts (the MEDIUM-force event is
    the whole consequence). ``hash-model.md`` § Integrity-enforcement opt-in owns the
    graduated logic; this function realizes it."""
    if not deployment.artifacts:
        return
    enforce = deployment.training_contract.integrity_enforcement
    base = deployment_dir if deployment_dir is not None else Path(".")
    for trainable_name, artifact_rel in deployment.artifacts.items():
        if trainable_name in duplicate_names:
            # A flat registration key matching two DISTINCT trainable compositions
            # across nesting levels is ambiguous — comparing an arbitrary one would
            # let the opt-in vouch for a trainable it never checked. Fail loud.
            raise ContractViolation(
                check=Check.NAME_UNIQUENESS,
                rule_id="R-pipeline-001",
                expected=(
                    f"the [artifacts] key '{trainable_name}' names exactly one trainable "
                    f"composition in the deployed pipeline tree of '{pipeline_name}'"
                ),
                actual=(
                    f"two or more distinct trainable compositions across nesting levels "
                    f"share the declared name '{trainable_name}' — the flat registration "
                    "is ambiguous"
                ),
                remediation_hint=(
                    "give the colliding trainable compositions distinct meta.name values "
                    "(the trained-artifact-manifest key must identify one trainable)"
                ),
                composition_ref=pipeline_name,
                section_path=f"artifacts.{trainable_name}",
            )
        composition = trainables.get(trainable_name)
        if composition is None:
            # guarantees: artifact-unknown-trainable-refused
            raise ContractViolation(
                check=Check.ARTIFACT_TRAINABLE_UNKNOWN,
                rule_id="R-pipeline-003",
                expected=(
                    f"every [artifacts] key names a trainable composition node deployed in "
                    f"pipeline '{pipeline_name}' (known: {sorted(trainables)})"
                ),
                actual=f"artifacts.'{trainable_name}' matches no trainable composition node",
                remediation_hint=(
                    "align the [artifacts] key with the trainable composition's meta.name, "
                    "or remove the stale entry — a registration that can never be compared "
                    "is a wiring mistake, not a no-op"
                ),
                composition_ref=pipeline_name,
                section_path=f"artifacts.{trainable_name}",
            )
        artifact_path = base / artifact_rel
        sidecar = sidecar_path(artifact_path)
        if not sidecar.is_file():
            if not enforce:
                continue  # no baseline to differ from — no comparison, no event
            # guarantees: manifest-missing-halts-under-enforcement
            raise ContractViolation(
                check=Check.TRAINED_ARTIFACT_MANIFEST_MISSING,
                rule_id="R-pipeline-003",
                expected=(
                    f"the registered artifact '{artifact_rel}' carries its sidecar "
                    f"manifest '{sidecar.name}' (the adjacent-file convention) — "
                    "integrity_enforcement = true admits no unverified artifact"
                ),
                actual="no sidecar manifest exists beside the artifact",
                remediation_hint=(
                    "write the sidecar with conjured artifact-tag, or set "
                    "integrity_enforcement = false (disabling the guarantee)"
                ),
                composition_ref=pipeline_name,
                section_path=f"artifacts.{trainable_name}",
            )
        manifest = load_manifest(sidecar)
        current_tbh = training_bundle_hash(composition, registry)
        recorded_tbh = manifest.training_bundle_hashes.get(trainable_name)
        tbh_matches = recorded_tbh == current_tbh
        if not tbh_matches:
            # The drift EVENT fires under either mode (the always-available property).
            # guarantees: tbh-drift-event-fires
            events.emit(
                events.TrainingBundleHashChanged(
                    trainable_qualified_name=trainable_name,
                    old_training_bundle_hash=recorded_tbh,  # None on first observation
                    new_training_bundle_hash=current_tbh,
                    pipeline_hash=pipeline_hash,
                    timestamp=events.now_iso(),
                )
            )
            acknowledged = trainable_name in deployment.acknowledged_drift.get(
                artifact_rel, ()
            )
            if enforce and not acknowledged:
                # guarantees: tbh-mismatch-halts-under-enforcement
                raise ContractViolation(
                    check=Check.TRAINING_BUNDLE_HASH_MISMATCH,
                    rule_id="R-pipeline-003",
                    expected=(
                        f"the trained shape at '{trainable_name}' matches the runtime "
                        f"shape: manifest training_bundle_hash "
                        f"{recorded_tbh or '(absent)'} == current {current_tbh} "
                        "(or an acknowledged_drift entry covers the artifact + trainable)"
                    ),
                    actual=(
                        f"training-bundle-hash mismatch at '{trainable_name}' for "
                        f"artifact '{artifact_rel}' (recorded "
                        f"{recorded_tbh or '(absent)'}, current {current_tbh})"
                    ),
                    remediation_hint=(
                        "retrain against the current composition, revert the trainable "
                        "declaration change, or acknowledge via acknowledged_drift "
                        "(per-artifact, per-trainable)"
                    ),
                    composition_ref=pipeline_name,
                    section_path=f"artifacts.{trainable_name}",
                )
        if pipeline_hash not in manifest.pipeline_hash_set:
            # MEDIUM force under either mode: the event fires, load proceeds — no halt
            # and no acknowledged_drift class exists for pipeline-hash-only drift.
            # guarantees: ph-drift-event-only
            events.emit(
                events.PipelineHashChanged(
                    old_pipeline_hash=manifest.pipeline_hash_set[0],
                    new_pipeline_hash=pipeline_hash,
                    timestamp=events.now_iso(),
                )
            )
