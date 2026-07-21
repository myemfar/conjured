"""``gen_error_index`` transclude resolution — a rule ``statement`` may single-source a
definition by ``:::{transclude} <id>``; the tool MUST ship the RESOLVED owner body into
``error-classes.toml``, never the literal directive. These are the RED-on-removal tests: if
the resolution step is dropped, the directive leaks into the shipped agent surface and the
first/second tests go red.

Lives under the package's main ``tests/`` suite rather than ``tools/tests/``
so the standard project test run collects it; it bootstraps the tool's import the way the tool
itself bootstraps the engine ``src`` path.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

# tools/ onto the path (the engine suite runs without it preconfigured).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import gen_error_index as gei  # noqa: E402


# -- the resolver, in isolation (RED-on-removal) -----------------------------------------

def test_transclude_resolves_to_region_body_and_strips_the_directive():
    """The happy path: a `:::{transclude}` is replaced by the owner body, surrounding prose is
    preserved, and the literal directive is gone. RED if `_resolve_transcludes` is reverted to a
    no-op (the `:::{transclude}` substring would survive)."""
    index = {"error-classes/kernel": "RESOLVED kernel line one\nRESOLVED kernel line two\n"}
    statement = "Rationale before.\n\n:::{transclude} error-classes/kernel\n:::\n\nRationale after."
    out = gei._resolve_transcludes(statement, index)
    assert "RESOLVED kernel line one" in out
    assert "RESOLVED kernel line two" in out
    assert ":::{transclude}" not in out  # the directive must not survive resolution
    assert out.startswith("Rationale before.")
    assert out.rstrip().endswith("Rationale after.")


def test_transclude_resolves_recursively():
    """An owner body may itself transclude — resolution is a fixpoint, not one level."""
    index = {
        "outer/region": "OUTER head\n:::{transclude} inner/region\n:::\nOUTER tail\n",
        "inner/region": "INNER body\n",
    }
    out = gei._resolve_transcludes(":::{transclude} outer/region\n:::\n", index)
    assert "OUTER head" in out and "OUTER tail" in out and "INNER body" in out
    assert ":::{transclude}" not in out


def test_no_transclude_is_an_identity_passthrough():
    text = "A plain statement with no directives.\nSecond line."
    assert gei._resolve_transcludes(text, {}) == text


# -- fail-loud error paths (a leaked/unresolvable directive must never ship) --------------

def test_unresolved_transclude_fails_loud():
    with pytest.raises(SystemExit, match="unresolved transclude"):
        gei._resolve_transcludes(":::{transclude} no-such/region\n:::\n", {})


def test_transclusion_cycle_fails_loud():
    index = {
        "a/x": ":::{transclude} b/y\n:::\n",
        "b/y": ":::{transclude} a/x\n:::\n",
    }
    with pytest.raises(SystemExit, match="cycle"):
        gei._resolve_transcludes(":::{transclude} a/x\n:::\n", index)


# -- end-to-end over the real shipped artifact -------------------------------------------

def test_region_index_is_collected_from_canon():
    """`_collect_regions` finds the corpus's `:::{region}` spans — the resolution substrate."""
    regions = gei._collect_regions()
    # a long-standing region every build resolves; presence proves the corpus scan works.
    assert "R-error-channel-001/key-set-routing" in regions
    assert regions["R-error-channel-001/key-set-routing"].strip()


def test_no_directive_leaks_into_any_shipped_statement():
    """The shipped agent surface must carry resolved text only — no `:::{transclude}` /
    `:::{region}` directive in any emitted statement. RED if resolution is removed once any
    shipped rule statement single-sources by transclude (R-error-channel-001 after the A-merge)."""
    data = tomllib.loads(gei.build()[gei.TOML_PATH])
    for record in data["audit_codes"]:
        statement = record["statement"]
        assert ":::{transclude}" not in statement, f"directive leaked into {record['audit_code']}"
        assert ":::{region}" not in statement, f"directive leaked into {record['audit_code']}"


def test_error_channel_001_statement_carries_resolved_kernel():
    """The A-merge single-sources R-error-channel-001 (C1.PIPELINE_FAILURE_WRAP.001) to two
    transcluded regions; the shipped statement must carry the RESOLVED merged kernel — the three
    class definitions, the fuller SchemaValidationError/PipelineFailure detail, and the full
    key-set-routing nuance — never the directive. RED if resolution is removed (the directive would
    ship and these body fragments would be absent)."""
    data = tomllib.loads(gei.build()[gei.TOML_PATH])
    statement = next(
        r["statement"] for r in data["audit_codes"]
        if r["audit_code"] == "C1.PIPELINE_FAILURE_WRAP.001"
    )
    assert ":::{transclude}" not in statement
    # the three class definitions (the kernel)
    for fragment in ("ContractViolation", "SchemaValidationError", "PipelineFailure"):
        assert fragment in statement
    # prose-unique fuller detail (SVE) and (PF) — would be lost without the merge
    assert "declaration-derived" in statement
    assert "inflated error-class vocabulary" in statement
    # the rule-unique key-set-routing nuance, now via the transcluded region
    assert "routing is scoped by boundary" in statement


# -- multi-rule audit-code guard (surprise-fixes 3-code) ----------------------------------

def test_render_fails_loud_on_multi_rule_audit_coded_record(monkeypatch):
    """A future multi-rule audit-code assignment must FAIL the generator, not silently ship only
    the first rule_id (surprise-fixes 3-code). Canon pins the [[audit_codes]] record shape SINGULAR
    (one audit_code → one rule_id — error-channel/reference.md § error-classes.toml), while the
    [[checks]] table carries the full list; an audit-coded record emitting only ``rule_ids[0]`` would
    break that md-row mirror, against the generator's by-construction-completeness claim. RED if the
    ``len(record.rule_ids) != 1`` guard is removed (the generator then silently truncates). Latent
    today — every registered audit code maps to a single-rule check — so this monkeypatches a
    two-rule audit-coded record to exercise the guard."""
    from conjured.errors import Check, CheckRecord
    fake_check = next(iter(Check))  # any real Check member
    fake_record = CheckRecord(
        rule_ids=("R-error-channel-001", "R-error-channel-003"),
        error_class="SchemaValidationError",
        audit_code="C9.MULTI.001",
    )
    monkeypatch.setattr(gei, "AUDIT_CODE_REGISTRY", {"C9.MULTI.001": fake_check})
    monkeypatch.setattr(gei, "CHECK_REGISTRY", {fake_check: fake_record})
    with pytest.raises(SystemExit, match="record shape is singular"):
        gei.render_error_classes_toml({})
