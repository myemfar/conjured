"""The shipped conformance kit — the native members' sibling audit stamps, the shipped
audit prompts, their provenance link, and their packaging.

The kit (``conjured.conformance`` prompts + findings; the native members'
``<module>.audit.toml`` siblings under ``conjured.lib``) is the user-facing half of the
audit-stamp mechanism (handler/reference.md § Audit stamps). These tests defend it against
the four ship-time regressions:

1. **silent native-stamp staleness** — a native module edited without re-auditing
   (``test_shipped_stamp_is_fresh_and_pass_grade``: recompute the source hash, compare);
2. **"the native library passes it" breaking** — the native members no longer composing
   clean under ``audit_enforcement`` (``test_native_members_compose_clean_under_enforcement``
   — this RESOLVES each native member under enforcement, the exact per-module read a full
   compose runs; it reds the moment any *read* stamp is deleted or staled, which is what
   pins the operative stamp set);
3. **prompt↔stamp provenance drift** — a stamp minted under a prompt that no longer ships,
   or a prompt edited without re-stamping (``test_stamp_audit_prompt_hash_matches_shipped_prompt``);
4. **ship-time loss** — the prompts / findings / stamps not resolving from the installed
   package (``test_*_resolve_via_importlib_resources``).

The **operative stamp set** is exactly the ``conjured.lib`` modules the enforcement check
reads at resolution when the native members compose: the two trainable adapters and the
emission hook. ``compilers.py`` is NOT in it (the blessed bare-name compilers resolve
without a source audit — resolve_compile.py), nor is ``__init__.py`` (a package
side-effect import, never a resolved handler/adapter/validator module).
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

import pytest

import conjured.lib
from conjured.errors import Check, ContractViolation
from conjured.validator.audit_stamp import (
    PASS_GRADES,
    STAMP_FIELDS,
    compute_source_hash,
)
from conjured.validator.parse import parse_handler, parse_service_type
from conjured.validator.resolve_adapter import resolve_adapter
from conjured.validator.resolve_handler import resolve_handler

LIB_DIR = Path(conjured.lib.__file__).parent

#: Native members resolved as trainable-backend **adapters** (native adapter table).
ADAPTER_MEMBERS = ("gbnf_trainable", "openai_compatible_trainable")
#: Native members resolved as bare-function **handlers** (dotted path).
HANDLER_MEMBERS = ("blob_reference_emitter",)
ALL_MEMBERS = ADAPTER_MEMBERS + HANDLER_MEMBERS

#: stamp stem → the shipped prompt it was minted under (the provenance the stamp's
#: ``audit_prompt_hash`` records).
MINTED_UNDER = {
    "gbnf_trainable": "trainable-backend-audit.md",
    "openai_compatible_trainable": "trainable-backend-audit.md",
    "blob_reference_emitter": "module-conformance-audit.md",
}

PROMPTS = ("module-conformance-audit.md", "trainable-backend-audit.md")


def _stamp(stem: str) -> dict:
    return tomllib.loads((LIB_DIR / f"{stem}.audit.toml").read_text(encoding="utf-8"))


def _resolve_member_under_enforcement(stem: str) -> None:
    """Resolve one native member with ``audit_enforcement=True`` — the exact per-module
    audit-stamp read a full compose performs. Raises ``ContractViolation`` if the member's
    sibling stamp is not fresh (absent / stale / failed) or malformed."""
    toml_path = LIB_DIR / f"{stem}.toml"
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    if stem in ADAPTER_MEMBERS:
        service_type = parse_service_type(data, file_path=str(toml_path))
        resolve_adapter(
            f"conjured.lib.{stem}", service_type,
            toml_path=str(toml_path), audit_enforcement=True,
        )
    else:
        decl = parse_handler(data, file_path=str(toml_path))
        resolve_handler(
            f"conjured.lib.{stem}.emit", decl,
            toml_path=str(toml_path), audit_enforcement=True,
        )


# ---------------------------------------------------------------------------
# 1. Shipped-stamps freshness (defends silent native-stamp staleness)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", ALL_MEMBERS)
def test_shipped_stamp_is_fresh_and_pass_grade(stem):
    """Every native sibling stamp carries exactly the closed field set, a ``source_hash``
    matching its module's current bytes, and a pass-grade verdict. RED the moment a native
    module is edited without re-auditing (the hash no longer matches)."""
    module = LIB_DIR / f"{stem}.py"
    stamp = _stamp(stem)
    assert set(stamp) == set(STAMP_FIELDS)
    assert stamp["source_hash"] == compute_source_hash(module.read_bytes())
    assert stamp["verdict"] in PASS_GRADES


# ---------------------------------------------------------------------------
# 2. Enforcement-on golden (defends "the native library passes it" + pins the set)
# ---------------------------------------------------------------------------


def test_native_members_compose_clean_under_enforcement():
    """The native library passes ``audit_enforcement`` end-to-end: every native member
    resolves clean under enforcement because its shipped sibling stamp is fresh and
    pass-grade. This is the operative-stamp-set oracle — it reds if ANY read stamp is
    deleted or staled (an absent/stale stamp raises AUDIT_STAMP_NOT_FRESH at that member's
    resolution)."""
    for stem in ALL_MEMBERS:
        _resolve_member_under_enforcement(stem)  # no raise == fresh shipped stamp read


@pytest.mark.parametrize("stem", ALL_MEMBERS)
def test_removing_a_shipped_stamp_refuses_under_enforcement(stem):
    """The read is real: hide a member's shipped stamp and its resolution refuses under
    enforcement (absent). Restores the stamp unconditionally. RED if a member were silently
    outside the enforced-read set (it would resolve clean with no stamp)."""
    stamp_path = LIB_DIR / f"{stem}.audit.toml"
    backup = stamp_path.read_bytes()
    stamp_path.unlink()
    try:
        with pytest.raises(ContractViolation) as exc:
            _resolve_member_under_enforcement(stem)
        assert exc.value.check is Check.AUDIT_STAMP_NOT_FRESH
        assert "absent" in exc.value.actual
    finally:
        stamp_path.write_bytes(backup)


# ---------------------------------------------------------------------------
# 3. Prompt resolution + prompt↔stamp provenance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PROMPTS)
def test_prompt_resolves_via_importlib_resources(name):
    """Both shipped prompts resolve via ``importlib.resources.files('conjured.conformance')``
    — the surface the engine's violation messages point an author at."""
    assert resources.files("conjured.conformance").joinpath(name).is_file()


@pytest.mark.parametrize("stem", ALL_MEMBERS)
def test_stamp_audit_prompt_hash_matches_shipped_prompt(stem):
    """Each stamp's recorded ``audit_prompt_hash`` equals the sha256 of the shipped prompt
    it was minted under. RED if a prompt is edited without re-stamping, or a stamp names a
    prompt that no longer ships — the provenance the ``audit_prompt_hash`` field exists for."""
    prompt_bytes = (
        resources.files("conjured.conformance")
        .joinpath(MINTED_UNDER[stem])
        .read_bytes()
    )
    assert _stamp(stem)["audit_prompt_hash"] == compute_source_hash(prompt_bytes)


# ---------------------------------------------------------------------------
# 4. Ship-time presence (defends package-data loss)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", ALL_MEMBERS)
def test_findings_report_ships_and_resolves(stem):
    """Each native member's findings report resolves from the shipped conformance package
    (the worked example an author reads)."""
    assert (
        resources.files("conjured.conformance")
        .joinpath("findings", f"{stem}.md")
        .is_file()
    )


@pytest.mark.parametrize("stem", ALL_MEMBERS)
def test_sibling_stamp_ships_beside_its_module(stem):
    """Each native sibling stamp resolves from the shipped ``conjured.lib`` package (the
    ``*.toml`` package-data glob carries the ``.audit.toml`` siblings into the wheel)."""
    assert resources.files("conjured.lib").joinpath(f"{stem}.audit.toml").is_file()
