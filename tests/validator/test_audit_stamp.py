"""Audit-stamp freshness — the resolution-time check over the sibling ``<module>.audit.toml``
stamp (``validator.audit_stamp``; handler/reference.md § Audit stamps), its 4-state model
(fresh / stale / absent / failed), the fail-loud on a malformed stamp, and the
``audit_enforcement`` gating threaded through all three resolution loci (handler / adapter /
validator). Real ``tmp_path`` modules + real sibling stamps throughout.

Every guarantee below is a RED-on-removal test: with the stamp check removed the enforcement
tests would compose clean (no raise) and fail; with the enforcement gate removed the
off-by-default tests would raise and fail.
"""

from __future__ import annotations

import textwrap
import uuid
from pathlib import Path

import pytest

from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import FieldDecl, ValidatorSpec, primitive
from conjured.ir.handler import TransformDeclaration
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.validator.audit_stamp import (
    compute_source_hash,
    require_fresh_stamp,
    sibling_stamp_path,
)
from conjured.validator.resolve_adapter import resolve_adapter
from conjured.validator.resolve_handler import resolve_handler
from conjured.validator.resolve_validator import resolve_field_validator

TOML = "handlers/x.toml"

PURE_TRANSFORM = "def fn(*, x):\n    return {'y': x}\n"
GOOD_ADAPTER = """
class GoodAdapter:
    def __init__(self, *, model):
        self.model = model

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, temperature, **transport_extra):
        return {"echo": input_payload, "temperature": temperature}
"""
PURE_VALIDATOR = "def v(*, value):\n    return None\n"

SERVICE_TYPE = ServiceTypeDeclaration(
    name="conjured_llm.structured_output",
    identity_schema=(FieldDecl(name="model", type=primitive("str")),),
    transport_schema=(FieldDecl(name="endpoint", type=primitive("str")),),
    config_schema=(FieldDecl(name="temperature", type=primitive("float")),),
)


def _transform_decl(reads=("x",)):
    return TransformDeclaration(
        reads=tuple(FieldDecl(name=n, type=primitive("str")) for n in reads),
        output_schema=(FieldDecl(name="y", type=primitive("str")),),
    )


@pytest.fixture()
def module_dir(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, source: str) -> tuple[str, Path]:
    import importlib

    name = f"stampmod_{uuid.uuid4().hex[:10]}"
    path = module_dir / f"{name}.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    importlib.invalidate_caches()
    return name, path


def _write_stamp(module_path: Path, *, verdict="pass", source_hash=None, omit=None,
                 raw_toml=None, **overrides) -> Path:
    """Write a sibling ``<module>.audit.toml``. Defaults to a fresh pass-grade stamp over
    the module's current bytes; ``source_hash`` / ``verdict`` / ``omit`` / ``raw_toml`` /
    field overrides shape the not-fresh and malformed cases."""
    stamp_path = sibling_stamp_path(str(module_path))
    if raw_toml is not None:
        stamp_path.write_text(raw_toml, encoding="utf-8")
        return stamp_path
    fields = {
        "source_hash": source_hash or compute_source_hash(module_path.read_bytes()),
        "audit_prompt_hash": "a" * 64,
        "verdict": verdict,
        "date": "2026-07-07",
        "findings": "conformance/findings/module.md",
    }
    fields.update(overrides)
    for k in (omit or ()):
        fields.pop(k, None)
    body = "\n".join(f'{k} = {v!r}'.replace("'", '"') for k, v in fields.items())
    stamp_path.write_text(body + "\n", encoding="utf-8")
    return stamp_path


# ===========================================================================
# The mechanism, in isolation (require_fresh_stamp) — the 4-state model + fail-loud
# ===========================================================================


def test_fresh_pass_grade_stamp_returns_clean(module_dir):
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, verdict="pass")
    # No raise: hash matches + verdict is a pass-grade.
    require_fresh_stamp(
        origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
    )


def test_pass_with_notes_is_a_pass_grade(module_dir):
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, verdict="pass-with-notes")
    require_fresh_stamp(
        origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
    )


# verifies: audit-stamp-absent-refused
def test_absent_stamp_refused(module_dir):
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    # No stamp written — the normal not-yet-audited state.
    with pytest.raises(ContractViolation) as exc:
        require_fresh_stamp(
            origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
        )
    assert exc.value.check is Check.AUDIT_STAMP_NOT_FRESH
    assert exc.value.rule_id == "R-handler-pure-module"
    assert "absent" in exc.value.actual


# verifies: audit-stamp-stale-refused
def test_stale_stamp_refused(module_dir):
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, source_hash="0" * 64)  # a hash that cannot match the source
    with pytest.raises(ContractViolation) as exc:
        require_fresh_stamp(
            origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
        )
    assert exc.value.check is Check.AUDIT_STAMP_NOT_FRESH
    assert "stale" in exc.value.actual


# verifies: audit-stamp-fail-verdict-refused
def test_fail_verdict_with_matching_hash_refused(module_dir):
    """The fail-verdict hole (Engine historical incidents): a stamp whose source_hash
    MATCHES but whose verdict is `fail` MUST still refuse — a `matching hash ⇒ fresh`
    shortcut would re-open it. RED if the verdict gate is dropped."""
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, verdict="fail")  # hash matches the real source; verdict fails
    with pytest.raises(ContractViolation) as exc:
        require_fresh_stamp(
            origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
        )
    assert exc.value.check is Check.AUDIT_STAMP_NOT_FRESH
    assert "failed" in exc.value.actual


# verifies: audit-stamp-malformed-fails-loud
def test_corrupt_toml_is_malformed(module_dir):
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, raw_toml="this is = = not valid toml [[[")
    with pytest.raises(ContractViolation) as exc:
        require_fresh_stamp(
            origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
        )
    assert exc.value.check is Check.AUDIT_STAMP_MALFORMED


# verifies: audit-stamp-malformed-fails-loud
def test_missing_field_is_malformed(module_dir):
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, omit=("verdict",))  # a closed field absent
    with pytest.raises(ContractViolation) as exc:
        require_fresh_stamp(
            origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
        )
    assert exc.value.check is Check.AUDIT_STAMP_MALFORMED
    assert "verdict" in exc.value.actual


# verifies: audit-stamp-malformed-fails-loud
def test_out_of_enum_verdict_is_malformed(module_dir):
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, verdict="probably-fine")  # not in the closed verdict enum
    with pytest.raises(ContractViolation) as exc:
        require_fresh_stamp(
            origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
        )
    assert exc.value.check is Check.AUDIT_STAMP_MALFORMED


# verifies: audit-stamp-malformed-fails-loud
def test_array_verdict_is_malformed_not_a_raw_typeerror(module_dir):
    """An array/table verdict (`verdict = ["pass"]`) parses to an unhashable list, so the
    closed-enum `in _VERDICTS` membership test would raise a raw `TypeError: unhashable type`
    and ESCAPE the compose-time ContractViolation channel. It MUST fail loud as
    AUDIT_STAMP_MALFORMED. RED-on-removal: dropping `verdict` from the string-field type-check
    (so only the enum test guards it) makes this raise TypeError instead of a ContractViolation.
    A `raw_toml` fixture is required — the %r-based stamp writer cannot emit a TOML array."""
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, raw_toml=(
        'source_hash = "0000000000000000000000000000000000000000000000000000000000000000"\n'
        'audit_prompt_hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
        'verdict = ["pass"]\n'
        'date = "2026-07-07"\n'
        'findings = "conformance/findings/module.md"\n'
    ))
    with pytest.raises(ContractViolation) as exc:
        require_fresh_stamp(
            origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
        )
    assert exc.value.check is Check.AUDIT_STAMP_MALFORMED
    assert "verdict" in exc.value.actual


def test_malformed_is_distinct_from_absent(module_dir):
    """A file that exists but is corrupt is MALFORMED, never coerced to ABSENT (decided
    design #3 — fail loud on a corrupt engine-read artifact)."""
    _, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, source_hash=123)  # a non-string hash — a mistyped field
    with pytest.raises(ContractViolation) as exc:
        require_fresh_stamp(
            origin=str(path), source_bytes=path.read_bytes(), toml_path=TOML, what="handler"
        )
    assert exc.value.check is Check.AUDIT_STAMP_MALFORMED
    assert exc.value.check is not Check.AUDIT_STAMP_NOT_FRESH


# ===========================================================================
# The three resolution loci are wired (criterion 3 — validator locus is not silently skipped)
# ===========================================================================


def test_handler_locus_refuses_absent_stamp_under_enforcement(module_dir):
    mod, _ = _write_module(module_dir, PURE_TRANSFORM)
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(
            f"{mod}.fn", _transform_decl(), toml_path=TOML, audit_enforcement=True
        )
    assert exc.value.check is Check.AUDIT_STAMP_NOT_FRESH


def test_handler_locus_admits_fresh_stamp_under_enforcement(module_dir):
    mod, path = _write_module(module_dir, PURE_TRANSFORM)
    _write_stamp(path, verdict="pass")
    entry = resolve_handler(
        f"{mod}.fn", _transform_decl(), toml_path=TOML, audit_enforcement=True
    )
    assert entry.qualified_name == f"{mod}.fn"


def test_adapter_locus_refuses_stale_stamp_under_enforcement(module_dir):
    mod, path = _write_module(module_dir, GOOD_ADAPTER)
    _write_stamp(path, source_hash="0" * 64)  # stale
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(
            f"{mod}.GoodAdapter", SERVICE_TYPE, toml_path=TOML, audit_enforcement=True
        )
    assert exc.value.check is Check.AUDIT_STAMP_NOT_FRESH


def test_validator_locus_refuses_stale_stamp_under_enforcement(module_dir):
    """The validator module is an in-scope module — the stamp check runs for it too, not
    only handler/adapter (defends the silent scope hole, REPORT-v2 FIX 2 / criterion 3)."""
    mod, path = _write_module(module_dir, PURE_VALIDATOR)
    _write_stamp(path, source_hash="0" * 64)  # stale
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(
            ValidatorSpec(name=f"{mod}.v"), toml_path=TOML, audit_enforcement=True
        )
    assert exc.value.check is Check.AUDIT_STAMP_NOT_FRESH


def test_locus_malformed_stamp_under_enforcement_fails_loud(module_dir):
    mod, path = _write_module(module_dir, GOOD_ADAPTER)
    _write_stamp(path, raw_toml="broken = = toml [[[")
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(
            f"{mod}.GoodAdapter", SERVICE_TYPE, toml_path=TOML, audit_enforcement=True
        )
    assert exc.value.check is Check.AUDIT_STAMP_MALFORMED


# ===========================================================================
# Enforcement OFF → no stamp read, no consequence (criterion 4)
# ===========================================================================


def test_enforcement_off_admits_a_module_with_no_stamp(module_dir):
    """The default (audit_enforcement off): an absent stamp composes clean. RED if the
    stamp check were wrongly always-on."""
    mod, _ = _write_module(module_dir, PURE_TRANSFORM)
    entry = resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)  # default False
    assert entry.qualified_name == f"{mod}.fn"


def test_enforcement_off_does_not_read_even_a_corrupt_stamp(module_dir):
    """The stamp read itself is enforcement-gated (decided design #2 — no opt-in, no read,
    no consequence): a CORRUPT .audit.toml beside the module composes clean when enforcement
    is off, proving the file is never opened. RED if the read were not gated."""
    mod, path = _write_module(module_dir, GOOD_ADAPTER)
    _write_stamp(path, raw_toml="this would fail loud IF it were read [[[")
    cls = resolve_adapter(f"{mod}.GoodAdapter", SERVICE_TYPE, toml_path=TOML)  # off
    assert cls.__name__ == "GoodAdapter"
