"""``gen_error_index`` — the R4 codegen step. Happy paths (both artifacts cover the
whole registered error set; deterministic; marker hash self-consistent; the TOML
parses) and the error/staleness paths (``--check`` flags a tampered artifact; a
registered rule_id missing from canon fails loud).

Run with ``PYTHONPATH=tools`` (from the package root) (the same harness-test convention) — the
module itself bootstraps the engine's ``src`` path.
"""

from __future__ import annotations

import tomllib

import pytest

import gen_error_index as gei
from conjured.errors import AUDIT_CODE_REGISTRY, CHECK_REGISTRY, Check


@pytest.fixture(scope="module")
def artifacts() -> dict:
    return gei.build()


def test_artifacts_cover_the_whole_registered_error_set(artifacts):
    index = artifacts[gei.INDEX_PATH]
    toml_text = artifacts[gei.TOML_PATH]
    for check in Check:
        assert f"`{check.value}`" in index, f"index missing check {check.value}"
        assert f'check = "{check.value}"' in toml_text
    for code in AUDIT_CODE_REGISTRY:
        assert f"`{code}`" in index
        assert f'audit_code = "{code}"' in toml_text


def test_build_is_deterministic(artifacts):
    assert gei.build() == artifacts


def test_marker_hash_is_self_consistent(artifacts):
    for path, content in artifacts.items():
        # The .md artifact carries frontmatter FIRST (MyST requires it at byte 0);
        # the marker then sits on the first line after the closing `---`. The digest
        # covers the rendered body only — frontmatter and marker are envelope.
        rest = content
        if rest.startswith("---\n"):
            rest = rest.split("---\n", 2)[2]
        marker, body = rest.split("\n", 1)
        digest = gei._body_hash(body)
        assert digest in marker, f"{path.name}: marker hash does not cover the body"


def test_error_classes_toml_parses_with_required_keys(artifacts):
    data = tomllib.loads(artifacts[gei.TOML_PATH])
    assert len(data["audit_codes"]) == len(AUDIT_CODE_REGISTRY)
    for record in data["audit_codes"]:
        for key in ("audit_code", "check", "error_class", "rule_id", "rule_name", "reference", "statement"):
            assert record.get(key), f"audit-code record missing {key}: {record}"
    assert len(data["checks"]) == len(CHECK_REGISTRY)
    for record in data["checks"]:
        for key in ("check", "error_class", "rule_ids", "rule_names", "references"):
            assert record.get(key), f"check record missing {key}: {record}"


def test_on_disk_artifacts_are_fresh():
    """The committed artifacts must match a fresh derivation — the F-PB-3 freshness
    property for these two generated files."""
    assert gei.check() == 0


def test_check_mode_flags_a_tampered_artifact(monkeypatch, tmp_path, capsys):
    fresh = gei.build()
    index_copy = tmp_path / "error-index.md"
    toml_copy = tmp_path / "error-classes.toml"
    index_copy.write_text(fresh[gei.INDEX_PATH] + "\nhand-edited row\n", encoding="utf-8")
    toml_copy.write_text(fresh[gei.TOML_PATH], encoding="utf-8")
    monkeypatch.setattr(gei, "build", lambda: {index_copy: fresh[gei.INDEX_PATH], toml_copy: fresh[gei.TOML_PATH]})
    monkeypatch.setattr(gei, "PKG_DIR", tmp_path)
    assert gei.check() == 1
    assert "STALE" in capsys.readouterr().out


def test_unresolvable_registered_rule_fails_loud():
    with pytest.raises(SystemExit, match="not found in any canon"):
        gei._resolve("R-handler-999", {})
