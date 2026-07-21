"""Within-group aggregation + fail-fast across groups — the ContractViolationGroup arc.

Canon: pipeline/reference.md § Composition validation (the aggregate-within-a-group,
fail-fast-across-groups policy) + error-channel/reference.md § ContractViolationGroup.

These are the RED-on-removal tests: each goes RED if compile reverts to raise-first
(within-group) or stops short-circuiting later groups (across-group).
"""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation, ContractViolationGroup
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

from . import fixtures as F


def _two_mismatch_registry() -> DeclarationRegistry:
    reg = DeclarationRegistry()
    reg.add_handler("acme.w", loads(
        '[transform]\n[reads]\ni={type="str"}\n[output_schema]\nout={type="str"}',
        "handler", file_path="w.toml"))
    reg.add_handler("acme.r", loads(
        '[transform]\n[reads]\nin={type="int"}\n[output_schema]\no={type="int"}',
        "handler", file_path="r.toml"))
    return reg


def test_two_same_group_channel_mismatches_both_surface():
    """Two INDEPENDENT channel-type mismatches (the canonical "three channel-type
    mismatches → all reported" aggregation, Group B) surface together from ONE compile
    call as a ContractViolationGroup. RED on revert to raise-first (only the first
    mismatch would surface, as a bare ContractViolation)."""
    reg = _two_mismatch_registry()
    # ch1: written str (acme.w), read int (acme.r). ch2: same, distinct channel.
    pipeline = loads(
        '[meta]\nname="acme.p"\n'
        '[[nodes]]\nkind="handler"\nname="acme.w"\nreads_map={i="seed"}\nwrites_map={out="ch1"}\n'
        '[[nodes]]\nkind="handler"\nname="acme.r"\nreads_map={in="ch1"}\nwrites_map={o="o1"}\n'
        '[[nodes]]\nkind="handler"\nname="acme.w"\nreads_map={i="seed"}\nwrites_map={out="ch2"}\n'
        '[[nodes]]\nkind="handler"\nname="acme.r"\nreads_map={in="ch2"}\nwrites_map={o="o2"}\n'
        '[inputs]\nseed={type="str"}\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolationGroup) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    group = exc.value
    assert len(group.violations) == 2, [v.check.value for v in group.violations]
    assert all(v.check is Check.READ_WRITE_SHAPE for v in group.violations)
    channels = {v.section_path for v in group.violations}
    assert channels == {"channel.ch1", "channel.ch2"}, channels
    # Each member is a full ContractViolation carrying its own complete payload.
    assert all(isinstance(v, ContractViolation) and v.rule_id == "R-pipeline-001"
               for v in group.violations)


def test_two_dangling_identity_ports_both_surface():
    """The acceptance example: two dangling identity ports both surface from one compile
    call (Group B). RED on revert to raise-first."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.add", loads(
        '[transform]\n[reads]\nleft={type="int"}\nright={type="int"}\n[output_schema]\no={type="int"}',
        "handler", file_path="add.toml"))
    # Both ports unmapped → identity channels `left` / `right`, neither written nor in [inputs].
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.add"\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolationGroup) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    group = exc.value
    assert len(group.violations) == 2, [v.check.value for v in group.violations]
    assert all(v.check is Check.DANGLING_IDENTITY_PORT for v in group.violations)
    assert {v.section_path for v in group.violations} == {"channel.left", "channel.right"}


def test_group_a_failure_short_circuits_group_b():
    """Fail-fast across groups: a Group-A (registry-resolution) failure short-circuits
    Group-B (graph topology) — only the Group-A violations surface, even though the graph
    also carries a Group-B fault (a dangling read on `acme.good`). RED if the across-group
    fail-fast is removed (Group B would then run and the group would also carry the
    dangling-port violation)."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.good", loads(
        '[transform]\n[reads]\nin={type="str"}\n[output_schema]\no={type="str"}',
        "handler", file_path="good.toml"))
    pipeline = loads(
        '[meta]\nname="acme.p"\n'
        '[[nodes]]\nkind="handler"\nname="acme.missing1"\n'    # Group A: unresolved
        '[[nodes]]\nkind="handler"\nname="acme.missing2"\n'    # Group A: unresolved
        '[[nodes]]\nkind="handler"\nname="acme.good"\nreads_map={in="ghost"}\n',  # Group B: dangling
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolationGroup) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    group = exc.value
    checks = {v.check for v in group.violations}
    # Both Group-A resolution failures aggregated; the Group-B dangling-port check never ran.
    assert checks == {Check.HANDLER_NAME_RESOLUTION}, [v.check.value for v in group.violations]
    assert len(group.violations) == 2
    assert Check.DANGLING_IDENTITY_PORT not in checks


def test_single_group_violation_raises_bare_contract_violation():
    """The common case: a group with exactly one violation raises the BARE
    ContractViolation (no one-element ContractViolationGroup wrapper) — the existing
    single-violation consumers stay unchanged (error-channel § ContractViolationGroup)."""
    reg = DeclarationRegistry()
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.only_missing"\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert not isinstance(exc.value, ContractViolationGroup)
    assert exc.value.check is Check.HANDLER_NAME_RESOLUTION


def test_composition_supply_fault_coreports_with_a_pipeline_group_b_fault():
    """A composition-level SUPPLY fault (a missing service-binding identity supply) reports in Group
    B — the graph-topology group — ALONGSIDE a pipeline-level Group-B fault, surfacing BOTH from one
    compile as a ContractViolationGroup (37#5). It is detected during flatten (where the resolved
    backend is in scope) but is a supply/topology concern whose preconditions are the graph, not
    Group-A resolution, so it must co-report rather than short-circuit Group B. RED-on-removal: revert
    the composition supply checks to append to Group A (`violations_a`); the Group-A finalize then
    raises the composition fault FIRST as a bare ContractViolation, short-circuiting Group B, and the
    pipeline-level dangling-port fault never surfaces (this `raises(ContractViolationGroup)` fails)."""
    reg, _pipeline = F.build_trainable()
    # Break the composition's service-binding identity supply → a composition BINDING_SUPPLY fault.
    comp = reg.get_composition("trainables/dialogue.toml")
    reg.add_composition("trainables/dialogue.toml", comp.model_copy(update={"service_bindings": ()}))
    # A handler whose unmapped ports create a pipeline-level Group-B dangling-port fault.
    reg.add_handler("acme.add", loads(
        '[transform]\n[reads]\nleft={type="int"}\nright={type="int"}\n[output_schema]\no={type="int"}',
        "handler", file_path="add.toml"))
    pipeline = loads(
        '[meta]\nname="acme.dialogue"\n'
        '[[nodes]]\nkind="handler"\nname="acme.ctx"\n'
        '[[nodes]]\nkind="composition"\nname="trainables/dialogue.toml"\n'
        '[[nodes]]\nkind="handler"\nname="acme.add"\n'   # unmapped left/right → dangling (Group B)
        '[inputs]\nraw={type="str"}\n[outputs]\ndialogue_response={type="str"}\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolationGroup) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    checks = {v.check for v in exc.value.violations}
    assert Check.BINDING_SUPPLY in checks          # the composition supply fault (was Group A)
    assert Check.DANGLING_IDENTITY_PORT in checks  # the pipeline-level Group-B fault — co-reported


def test_multi_fault_config_block_reports_every_fault():
    """RED-on-removal for COMPILE-4 (within-group aggregation over one config block):
    a [trainable.config] carrying an undeclared key AND leaving both declared no-default
    fields uncovered holds three independently-detectable supply faults - all three
    surface from one compile as a ContractViolationGroup, never only the first (the
    composition-validation error-reporting policy, pipeline reference)."""
    reg, pipeline = F.build_trainable()
    comp_text = F.TRAINABLE_COMPOSITION.replace(
        "[trainable.config]\ntemperature = 0.7\nmax_tokens = 512",
        "[trainable.config]\nbogus = 1",
    )
    reg.add_composition(
        "trainables/dialogue.toml", loads(comp_text, "composition", file_path="c.toml")
    )
    with pytest.raises(ContractViolationGroup) as exc:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    supply_faults = [
        v for v in exc.value.violations if v.check is Check.CONFIG_SCHEMA_SUPPLY
    ]
    actuals = " | ".join(v.actual for v in supply_faults)
    assert "bogus" in actuals            # the undeclared-key direction
    assert "temperature" in actuals      # uncovered declared field 1
    assert "max_tokens" in actuals       # uncovered declared field 2
    assert len(supply_faults) >= 3
