"""The trained-artifact manifest surface (R-pipeline-003) — sidecar load, the
integrity comparison's graduated force, and the manifest CLI pair.

Grounding: ``pipeline/reference.md`` § Trained-artifact manifest + § The manifest CLI
pair + the four conformance rows; ``hash-model.md`` § Integrity-enforcement opt-in
(the graduated logic verbatim: events under either mode; halts enforcement-gated;
TBH mismatch acknowledgeable per artifact + trainable; pipeline-hash-only drift never
halts); ``deployment/reference.md`` § artifacts. The comparison mechanism is
``conjured.manifest.verify_artifacts`` (the enforcement locus these tests drive
directly — the assemble seam forwards to it verbatim and stays covered by the
whole-suite assemble tests for the no-registration path)."""

from __future__ import annotations

import logging

import pytest

from conjured import events
from conjured.cli import main as cli_main
from conjured.errors import Check, ContractViolation
from conjured.hasher.hashes import pipeline_hash, training_bundle_hash
from conjured.manifest import (
    collect_trainables,
    load_manifest,
    sidecar_path,
    verify_artifacts,
)
from conjured.validator import loads

from tests.derivables._fixtures import (
    PIPELINE_WITH_COMPOSITION,
    SERVICE_TYPE_DIALOGUE,
    TRAINABLE_COMPOSITION,
    TRANSFORM_CTX,
    TRANSFORM_FORMATTER,
    build_trainable,
)

TRAINABLE_NAME = "dialogue_training"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[object] = []

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        self.events.append(record.msg)


def _deployment(text: str):
    return loads(text, "deployment", file_path="deploy.toml")


def _enforced(artifact: str, *, acknowledged: str | None = None) -> str:
    ack = (
        f'[acknowledged_drift]\n"{artifact}" = ["{acknowledged}"]\n'
        if acknowledged
        else ""
    )
    return (
        "[training_contract]\nintegrity_enforcement = true\n"
        f'[artifacts]\n"{TRAINABLE_NAME}" = "{artifact}"\n' + ack
    )


def _unenforced(artifact: str) -> str:
    return (
        "[training_contract]\nintegrity_enforcement = false\n"
        f'[artifacts]\n"{TRAINABLE_NAME}" = "{artifact}"\n'
    )


def _sidecar_text(*, artifact: str, ph: str, tbh: str) -> str:
    return (
        "[manifest]\n"
        f'artifact = "{artifact}"\n'
        f'pipeline_hash_set = ["{ph}"]\n'
        'base_model = "qwen3.5-4b"\n'
        'artifact_format = "safetensors"\n'
        'trained_at = "2026-07-19T00:00:00+00:00"\n'
        'training_data_source = "external"\n'
        "[training_bundle_hashes]\n"
        f'"{TRAINABLE_NAME}" = "{tbh}"\n'
    )


@pytest.fixture()
def ground(tmp_path):
    """The trainable fixture pipeline + its computed hashes + a tmp artifact file."""
    registry, pipeline = build_trainable()
    trainables, duplicates = collect_trainables(pipeline, registry)
    assert set(trainables) == {TRAINABLE_NAME} and not duplicates
    ph = pipeline_hash(pipeline, registry)
    tbh = training_bundle_hash(trainables[TRAINABLE_NAME], registry)
    artifact = tmp_path / "loras" / "dialogue.safetensors"
    artifact.parent.mkdir()
    artifact.write_bytes(b"weights")
    return registry, pipeline, trainables, ph, tbh, tmp_path, artifact


def _verify(ground, deployment_text: str, *, sidecar: str | None):
    registry, _pipeline, trainables, ph, tbh, tmp_path, artifact = ground
    if sidecar is not None:
        sidecar_path(artifact).write_text(sidecar, encoding="utf-8")
    capture = _Capture()
    with events.subscribe(capture):
        verify_artifacts(
            deployment=_deployment(deployment_text),
            deployment_dir=tmp_path,
            pipeline_name="acme.dialogue",
            pipeline_hash=ph,
            trainables=trainables,
            registry=registry,
        )
    return capture.events


# ── load_manifest: the malformed arm (either enforcement mode) ────────────────────────


def test_load_manifest_well_formed_roundtrips(ground, tmp_path):
    *_, ph, tbh, _tmp, artifact = ground[2:]
    path = sidecar_path(artifact)
    path.write_text(
        _sidecar_text(artifact="loras/dialogue.safetensors", ph=ph, tbh=tbh),
        encoding="utf-8",
    )
    manifest = load_manifest(path)
    assert manifest.pipeline_hash_set == (ph,)
    assert manifest.training_bundle_hashes == {TRAINABLE_NAME: tbh}
    assert manifest.training_data_source == "external"
    assert manifest.generator_info is None


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("not toml [", "not valid UTF-8 TOML"),
        ("[manifest]\n", "omits required field"),
        # mistyped pipeline_hash_set (string, not list)
        (
            '[manifest]\nartifact = "a"\npipeline_hash_set = "x"\nbase_model = "b"\n'
            'artifact_format = "f"\ntrained_at = "t"\ntraining_data_source = "external"\n'
            "[training_bundle_hashes]\n",
            "pipeline_hash_set",
        ),
        # out-of-enum training_data_source
        (
            '[manifest]\nartifact = "a"\npipeline_hash_set = ["x"]\nbase_model = "b"\n'
            'artifact_format = "f"\ntrained_at = "t"\ntraining_data_source = "scraped"\n'
            "[training_bundle_hashes]\n",
            "closed",
        ),
        # generated corpus without generator_info
        (
            '[manifest]\nartifact = "a"\npipeline_hash_set = ["x"]\nbase_model = "b"\n'
            'artifact_format = "f"\ntrained_at = "t"\ntraining_data_source = "generated"\n'
            "[training_bundle_hashes]\n",
            "generator_info",
        ),
    ],
)
# verifies: manifest-malformed-fails-loud
def test_load_manifest_malformed_fails_loud(tmp_path, text, fragment):
    """The corrupt-artifact arm: malformed is a structured ContractViolation, never
    coerced to absent and never a raw parse exception (check
    trained-artifact-manifest-malformed)."""
    path = tmp_path / "a.safetensors.conjured.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ContractViolation) as exc:
        load_manifest(path)
    assert exc.value.check is Check.TRAINED_ARTIFACT_MANIFEST_MALFORMED
    assert exc.value.rule_id == "R-pipeline-003"
    assert fragment in exc.value.actual or fragment in exc.value.expected


# ── verify_artifacts: the graduated force ─────────────────────────────────────────────


# verifies: artifact-unknown-trainable-refused
def test_unknown_trainable_registration_refused_even_unenforced(ground):
    """The dead-registration arm fires under EITHER mode — a registration that can
    never be compared is a wiring mistake, not a no-op (check
    artifact-trainable-unknown)."""
    with pytest.raises(ContractViolation) as exc:
        _verify(
            ground,
            "[training_contract]\nintegrity_enforcement = false\n"
            '[artifacts]\n"acme.renamed_trainable" = "loras/dialogue.safetensors"\n',
            sidecar=None,
        )
    assert exc.value.check is Check.ARTIFACT_TRAINABLE_UNKNOWN
    assert exc.value.rule_id == "R-pipeline-003"


# verifies: manifest-missing-halts-under-enforcement
def test_missing_sidecar_halts_under_enforcement(ground):
    """Missing manifest + integrity_enforcement = true → halt (check
    trained-artifact-manifest-missing): no manifest = no integrity guarantee, and the
    deployment opted into the guarantee."""
    with pytest.raises(ContractViolation) as exc:
        _verify(ground, _enforced("loras/dialogue.safetensors"), sidecar=None)
    assert exc.value.check is Check.TRAINED_ARTIFACT_MANIFEST_MISSING
    assert exc.value.rule_id == "R-pipeline-003"


def test_missing_sidecar_is_the_silent_no_baseline_case_unenforced(ground):
    """Enforcement off + no sidecar: no comparison, no event, no error (hash-model
    § Enforcement off — nothing to differ from)."""
    captured = _verify(ground, _unenforced("loras/dialogue.safetensors"), sidecar=None)
    assert captured == []


# verifies: tbh-mismatch-halts-under-enforcement
def test_tbh_mismatch_halts_and_fires_the_drift_event(ground):
    """The HIGH-force arm (check training-bundle-hash-mismatch): the drift event fires
    (the always-available property) AND the halt lands (enforcement, unacknowledged)."""
    _reg, _p, _t, ph, _tbh, _tmp, _artifact = ground
    with pytest.raises(ContractViolation) as exc:
        _verify(
            ground,
            _enforced("loras/dialogue.safetensors"),
            sidecar=_sidecar_text(
                artifact="loras/dialogue.safetensors", ph=ph, tbh="sha256:" + "0" * 64
            ),
        )
    assert exc.value.check is Check.TRAINING_BUNDLE_HASH_MISMATCH
    assert exc.value.rule_id == "R-pipeline-003"


def test_tbh_mismatch_acknowledged_proceeds_with_event(ground):
    """acknowledged_drift covering the artifact + trainable converts the halt into
    the recorded drift event (per-artifact, per-trainable acknowledgment)."""
    _reg, _p, _t, ph, _tbh, _tmp, _artifact = ground
    captured = _verify(
        ground,
        _enforced("loras/dialogue.safetensors", acknowledged=TRAINABLE_NAME),
        sidecar=_sidecar_text(
            artifact="loras/dialogue.safetensors", ph=ph, tbh="sha256:" + "0" * 64
        ),
    )
    kinds = [type(e).__name__ for e in captured]
    assert kinds == ["TrainingBundleHashChanged"]
    assert captured[0].new_training_bundle_hash != captured[0].old_training_bundle_hash


# verifies: tbh-drift-event-fires
def test_tbh_mismatch_unenforced_fires_event_and_proceeds(ground):
    """Enforcement off: the mismatch fires training_bundle_hash_changed and load
    proceeds — the integrity property stays available without the enforcement."""
    _reg, _p, _t, ph, _tbh, _tmp, _artifact = ground
    captured = _verify(
        ground,
        _unenforced("loras/dialogue.safetensors"),
        sidecar=_sidecar_text(
            artifact="loras/dialogue.safetensors", ph=ph, tbh="sha256:" + "1" * 64
        ),
    )
    assert [type(e).__name__ for e in captured] == ["TrainingBundleHashChanged"]


@pytest.mark.parametrize("enforce", [True, False])
# verifies: ph-drift-event-only
def test_pipeline_hash_only_drift_is_event_only_under_both_modes(ground, enforce):
    """The MEDIUM-force arm: TBHs match, pipeline-hash absent from the recorded set →
    pipeline_hash_changed fires and load proceeds under EITHER mode (no halt, no
    acknowledged_drift class exists for it)."""
    _reg, _p, _t, _ph, tbh, _tmp, _artifact = ground
    text = (
        _enforced("loras/dialogue.safetensors")
        if enforce
        else _unenforced("loras/dialogue.safetensors")
    )
    recorded = "sha256:" + "a" * 64
    captured = _verify(
        ground,
        text,
        sidecar=_sidecar_text(
            artifact="loras/dialogue.safetensors", ph=recorded, tbh=tbh
        ),
    )
    assert [type(e).__name__ for e in captured] == ["PipelineHashChanged"]
    assert captured[0].old_pipeline_hash == recorded


def test_both_match_is_silent(ground):
    """Full match: no events, no halt — the silent-load condition."""
    _reg, _p, _t, ph, tbh, _tmp, _artifact = ground
    captured = _verify(
        ground,
        _enforced("loras/dialogue.safetensors"),
        sidecar=_sidecar_text(artifact="loras/dialogue.safetensors", ph=ph, tbh=tbh),
    )
    assert captured == []


# ── deployment [artifacts] parse ──────────────────────────────────────────────────────


def test_deployment_artifacts_table_parses():
    dep = _deployment(_unenforced("loras/dialogue.safetensors"))
    assert dep.artifacts == {TRAINABLE_NAME: "loras/dialogue.safetensors"}


def test_deployment_artifacts_non_string_value_is_malformed():
    with pytest.raises(ContractViolation) as exc:
        _deployment(
            "[training_contract]\nintegrity_enforcement = false\n"
            '[artifacts]\n"x" = 3\n'
        )
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-deployment-001"


# ── the manifest CLI pair ─────────────────────────────────────────────────────────────


def _write_cli_fixtures(tmp_path):
    (tmp_path / "st.toml").write_text(SERVICE_TYPE_DIALOGUE, encoding="utf-8")
    (tmp_path / "ctx.toml").write_text(TRANSFORM_CTX, encoding="utf-8")
    (tmp_path / "fmt.toml").write_text(TRANSFORM_FORMATTER, encoding="utf-8")
    (tmp_path / "c.toml").write_text(TRAINABLE_COMPOSITION, encoding="utf-8")
    (tmp_path / "p.toml").write_text(PIPELINE_WITH_COMPOSITION, encoding="utf-8")
    artifact = tmp_path / "dialogue.safetensors"
    artifact.write_bytes(b"weights")
    return artifact


def _tag_argv(tmp_path, artifact, *extra):
    return [
        "artifact-tag", str(artifact),
        "--pipeline", str(tmp_path / "p.toml"),
        "--handler", f"acme.ctx={tmp_path / 'ctx.toml'}",
        "--handler", f"transform.formatter={tmp_path / 'fmt.toml'}",
        "--composition", f"trainables/dialogue.toml={tmp_path / 'c.toml'}",
        "--service-type", str(tmp_path / "st.toml"),
        "--base-model", "qwen3.5-4b",
        "--artifact-format", "safetensors",
        "--training-data-source", "external",
        *extra,
    ]


def test_artifact_tag_writes_a_sidecar_the_engine_loads(tmp_path, capsys):
    """Round trip: artifact-tag writes the sidecar; load_manifest validates it; the
    recorded hashes equal the independently-computed ones (the tag-after-training
    flow — the computed hashes ARE the training-time hashes)."""
    artifact = _write_cli_fixtures(tmp_path)
    assert cli_main(_tag_argv(tmp_path, artifact)) == 0
    manifest = load_manifest(sidecar_path(artifact))
    registry, pipeline = build_trainable()
    trainables, _ = collect_trainables(pipeline, registry)
    assert manifest.pipeline_hash_set == (pipeline_hash(pipeline, registry),)
    assert manifest.training_bundle_hashes == {
        TRAINABLE_NAME: training_bundle_hash(trainables[TRAINABLE_NAME], registry)
    }


def test_artifact_tag_refuses_an_existing_sidecar_without_force(tmp_path, capsys):
    artifact = _write_cli_fixtures(tmp_path)
    assert cli_main(_tag_argv(tmp_path, artifact)) == 0
    assert cli_main(_tag_argv(tmp_path, artifact)) == 1
    assert "--force" in capsys.readouterr().err
    assert cli_main(_tag_argv(tmp_path, artifact, "--force")) == 0


def test_artifact_tag_generator_flags_required_iff_generated(tmp_path, capsys):
    artifact = _write_cli_fixtures(tmp_path)
    argv = _tag_argv(tmp_path, artifact)
    argv[argv.index("external")] = "generated"
    with pytest.raises(SystemExit):  # argparse .error on the missing flag group
        cli_main(argv)
    with pytest.raises(SystemExit):  # generator flags on a non-generated corpus reject
        cli_main(_tag_argv(tmp_path, artifact, "--generator-id", "gpt-x"))


def test_artifact_mv_moves_the_pair_and_rewrites_the_artifact_field(tmp_path, capsys):
    artifact = _write_cli_fixtures(tmp_path)
    assert cli_main(_tag_argv(tmp_path, artifact)) == 0
    dst = tmp_path / "renamed.safetensors"
    assert cli_main(["artifact-mv", str(artifact), str(dst)]) == 0
    assert not artifact.exists() and not sidecar_path(artifact).exists()
    assert dst.exists()
    manifest = load_manifest(sidecar_path(dst))
    assert manifest.artifact == str(dst)


def test_artifact_mv_refuses_a_pair_without_a_sidecar(tmp_path, capsys):
    artifact = _write_cli_fixtures(tmp_path)
    dst = tmp_path / "renamed.safetensors"
    assert cli_main(["artifact-mv", str(artifact), str(dst)]) == 1
    assert "no sidecar" in capsys.readouterr().err
    assert artifact.exists()  # nothing moved


def test_artifact_mv_never_overwrites_an_existing_destination(tmp_path, capsys):
    artifact = _write_cli_fixtures(tmp_path)
    assert cli_main(_tag_argv(tmp_path, artifact)) == 0
    dst = tmp_path / "renamed.safetensors"
    dst.write_bytes(b"other")
    assert cli_main(["artifact-mv", str(artifact), str(dst)]) == 1
    assert artifact.exists() and sidecar_path(artifact).exists()
