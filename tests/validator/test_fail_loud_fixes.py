"""Fail-loud bugfix arc (C1–C6) + the parse-strictness arc (PARSE-F1/F2/F3) — one
RED-on-removal adversary per fix.

Each fix closes a site where the engine silently ACCEPTED a malformed input (the
universal-silent-fallback failure class — a masked malformed declaration is training-data
corruption), or cited the wrong rule for a correct rejection. Each test constructs the exact
adversary the fix defends against and asserts the **structured** raise (the ``Check``
discriminator + ``rule_id``, never a bare trace); the test goes RED if the fix is reverted (the
docstring names how).

Canon grounding per fix:
- C1/C2 — deployment/reference.md § acknowledged_drift / § transport / § pipelines.<name>
  override; R-deployment-001 / R-deployment-002. Fail-loud is reference/principles.md I1
  (no silent fallbacks).
- C3 — handler/reference.md § forbidden sections; architecture/exhaustive-declaration.md
  § Forbidden. The kind-specific forbidden-section diagnostic (R-handler-004 for a transform's
  service_bindings) must claim the section before the generic closed-grammar check.
- C4 — canonical.py module docstring ("no silent coercion"); the key side of the same
  promise the value side (the ``:215`` TypeError) already holds.
- C5 — architecture/exhaustive-declaration.md § the value-supply carve-out (a required
  value-supply block requires its header present); trainable.schema.toml § [trainable.config].
- C6 — pipeline/reference.md § Node-name resolution (an unresolvable composition reference
  raises ContractViolation; R-pipeline-001). The stage-1 binding-resolution pass dereferences
  each composition node, so a registry-absent reference fails loud HERE — the same structured
  violation the compile + hasher passes raise — instead of being silently skipped and deferred.
- PARSE-F1 — deployment/reference.md § training_contract + architecture/exhaustive-declaration.md
  § Required, body-required; R-deployment-001 ("integrity_enforcement MUST carry an explicit
  boolean"). The lenient pydantic bool validator silently coerced "yes"/1/"0".
- PARSE-F2 — kind-schemas/trainable.schema.toml § [trainable] (streamable = true|false);
  R-handler-010. The ``bool(...)`` wrapper truthiness-coerced "false"/0 (a forward-stub field).
- PARSE-F3 — a malformed FIELD in a non-handler schema section cites the OWNING rule
  (R-service-type-001 / R-pipeline-001), not the generic R-handler-006 fallback; the structural
  rejection was always correct. The (MALFORMED_DECLARATION, R-service-type-001) pair was
  registered in errors.CHECK_REGISTRY to admit it.
"""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation
from conjured.canonical import canon_value
from conjured.validator import DeclarationRegistry, loads
from conjured.validator.parse import parse_deployment, parse_pipeline, parse_service_type
from conjured.validator.resolve import resolve_pipeline_bindings


# ===========================================================================
# C1 — acknowledged_drift: each entry value MUST be a list of strings
#      (a bare string was silently tuple()-shredded into a char-tuple)
# ===========================================================================


def _deployment(ack_body: str) -> str:
    return (
        "[training_contract]\n"
        "integrity_enforcement = true\n"
        "[acknowledged_drift]\n" + ack_body
    )


def test_c1_acknowledged_drift_bare_string_raises():
    """A bare-string `acknowledged_drift.<artifact>` value (today silently shredded into a
    char-tuple) raises MALFORMED_DECLARATION. RED if the list-of-strings guard is removed
    (then `tuple("mypkg.t")` would become `('m','y',...)`)."""
    with pytest.raises(ContractViolation) as exc:
        loads(_deployment('"loras/alice.safetensors" = "mypkg.t"\n'), "deployment", file_path="d.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-deployment-001"
    assert exc.value.section_path == "acknowledged_drift.loras/alice.safetensors"


def test_c1_acknowledged_drift_non_string_member_raises():
    """A list containing a non-string member raises MALFORMED_DECLARATION. RED if the
    per-member `isinstance(n, str)` guard is removed."""
    with pytest.raises(ContractViolation) as exc:
        loads(_deployment('"loras/alice.safetensors" = ["ok", 123]\n'), "deployment", file_path="d.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-deployment-001"
    assert exc.value.section_path == "acknowledged_drift.loras/alice.safetensors"


def test_c1_acknowledged_drift_list_of_strings_still_parses():
    """The valid path is unbroken: a list of strings parses to a tuple of those strings."""
    dep = loads(
        _deployment('"loras/alice.safetensors" = ["mypkg.dialogue_trainable", "mypkg.other"]\n'),
        "deployment", file_path="d.toml")
    assert dep.acknowledged_drift == {
        "loras/alice.safetensors": ("mypkg.dialogue_trainable", "mypkg.other")
    }


# ===========================================================================
# C2 — a non-mapping transport / hook_transport / pipeline-override BLOCK body
#      raises (was silently substituted with {} — indistinguishable from empty)
# ===========================================================================

_TC = "[training_contract]\nintegrity_enforcement = true\n"


def test_c2_transport_block_non_mapping_raises():
    """A non-mapping `transport.<name>` block body raises (was a silent `{}`). RED if the
    transport per-block guard is restored to a silent fallback."""
    with pytest.raises(ContractViolation) as exc:
        loads(_TC + '[transport]\nllm = "not-a-table"\n', "deployment", file_path="d.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-deployment-001"
    assert exc.value.section_path == "transport.llm"


def test_c2_hook_transport_block_non_mapping_raises():
    """A non-mapping `hook_transport."<qn>"` block body raises (was a silent `{}`). RED if the
    hook-transport per-block guard is restored to a silent fallback."""
    with pytest.raises(ContractViolation) as exc:
        loads(_TC + '[hook_transport]\n"acme.log" = "not-a-table"\n', "deployment", file_path="d.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-deployment-001"
    assert exc.value.section_path == "hook_transport.acme.log"


def test_c2_pipeline_override_block_non_mapping_raises():
    """A non-mapping `pipelines.<qn>` override block body raises (was a silent `{}`). RED if the
    pipeline-override per-block guard is restored to a silent fallback."""
    with pytest.raises(ContractViolation) as exc:
        loads(_TC + '[pipelines]\n"acme.dialogue" = "not-a-table"\n', "deployment", file_path="d.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-deployment-002"
    assert exc.value.section_path == "pipelines.acme.dialogue"


def test_c2_empty_block_body_still_valid():
    """The valid path is unbroken: a legitimately-empty (present) transport block parses — the
    fix rejects only a NON-mapping body, never an empty table."""
    dep = loads(_TC + '[transport.llm]\n', "deployment", file_path="d.toml")
    assert [b.name for b in dep.transport] == ["llm"]
    assert dep.transport[0].values == {}


# ===========================================================================
# C3 — a transform declaring a service-only section gets the KIND diagnostic
#      (R-handler-004), not the generic closed-grammar message (R-handler-006)
# ===========================================================================


def test_c3_transform_service_bindings_cites_kind_rule_id():
    """A transform declaring `service_bindings` raises the kind-specific forbidden-section
    diagnostic (R-handler-004), NOT the generic closed-grammar `R-handler-006`. RED if the
    check order is reverted (then `_closed_grammar` shadows the kind check and R-handler-006
    returns)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n'
            '[service_bindings]\nllm={type="x.y"}\n',
            "handler", file_path="h.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-004"  # the kind diagnostic, not the generic R-handler-006
    assert exc.value.section_path == "service_bindings"


def test_c3_genuinely_unknown_section_still_closed_grammar():
    """The forbidden-before-closed-grammar swap does not weaken the generic check: a
    genuinely-unknown section (not a per-kind forbidden one) still raises the generic
    closed-grammar R-handler-006."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n'
            '[retry_policy]\nmax=3\n',
            "handler", file_path="h.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-006"


# ===========================================================================
# C4 — the canonicalizer raises on a post-str() key collision rather than
#      silently merging two distinct keys into one (data loss)
# ===========================================================================


def test_c4_stringify_key_collision_raises_typeerror():
    """Two distinct mapping keys that stringify identically (`1` and `"1"`) raise TypeError
    rather than silently collapsing to one entry (the key side of the module's "no silent
    coercion" promise). RED if the collision detection is removed (then the comprehension
    silently merges to one key)."""
    with pytest.raises(TypeError) as exc:
        canon_value({1: "a", "1": "b"})
    assert "collide" in str(exc.value)


def test_c4_distinct_string_keys_still_canonicalize():
    """The valid path is unbroken: distinct keys whose str() forms differ canonicalize without
    a false collision (both string keys and non-colliding int keys)."""
    assert canon_value({"1": "b", "2": "c"}) == {"1": "b", "2": "c"}
    assert canon_value({1: "a", 2: "b"}) == {"1": "a", "2": "b"}


# ===========================================================================
# C5 — a trainable node omitting [trainable.config] raises the missing
#      required-header CV rather than silently defaulting to {}
# ===========================================================================

_TRAINABLE_NO_CONFIG = (
    '[meta]\nkind="trainable"\nname="dt"\n'
    '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
    '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="qwen"\n'
    '[trainable]\n'  # NB: no [trainable.config] header
    '[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
    '[trainable.reads]\nnpc_state={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
)


def test_c5_trainable_omitting_config_raises_section_presence():
    """A trainable node omitting [trainable.config] entirely raises the missing-required-header
    SECTION_PRESENCE CV. RED if the `_require_present(..., "config", ...)` call is removed (then
    config silently defaults to {})."""
    with pytest.raises(ContractViolation) as exc:
        loads(_TRAINABLE_NO_CONFIG, "composition", file_path="c.toml")
    assert exc.value.check is Check.SECTION_PRESENCE
    assert exc.value.rule_id == "R-handler-006"
    assert exc.value.section_path == "config"


def test_c5_trainable_with_empty_config_still_parses():
    """The valid path is unbroken: a PRESENT but empty [trainable.config] parses (required,
    empty-allowed) and yields config == {} — identical IR to the (now-rejected) omitted form."""
    comp = loads(
        _TRAINABLE_NO_CONFIG.replace("[trainable]\n", "[trainable]\n[trainable.config]\n"),
        "composition", file_path="c.toml")
    assert comp.trainable.config == {}


# ===========================================================================
# PARSE-F1 — deployment integrity_enforcement MUST be an explicit boolean
#      (the lenient pydantic bool validator silently coerced "yes"/1/"0")
# ===========================================================================


def _dep_with_integrity(value: str) -> str:
    """A minimal deployment whose [training_contract].integrity_enforcement carries `value`
    verbatim (every other section is optional)."""
    return f"[training_contract]\nintegrity_enforcement = {value}\n"


@pytest.mark.parametrize("value", ['"yes"', "1", '"0"'])
def test_pf1_non_boolean_integrity_enforcement_rejects(value):
    """A non-boolean integrity_enforcement raises MALFORMED_DECLARATION / R-deployment-001 — a
    string, or an int (incl. the sneaky `1`/`"0"` the pydantic bool validator coerces to
    True/False). It is the I4 integrity-enforcement opt-in; a coerced opt-in is training-contract
    corruption (R-deployment-001 § training_contract: "MUST carry an explicit boolean"). RED if the
    `isinstance(integrity_value, bool)` guard is removed — then the lenient bool validator coerces
    the value and the declaration loads clean."""
    with pytest.raises(ContractViolation) as exc:
        loads(_dep_with_integrity(value), "deployment", file_path="d.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-deployment-001"
    assert exc.value.section_path == "training_contract.integrity_enforcement"


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_pf1_real_boolean_integrity_enforcement_still_parses(value, expected):
    """The valid path is unbroken: a real TOML boolean parses to that boolean — the explicit
    affirmative-or-negative opt-in the canon requires."""
    dep = loads(_dep_with_integrity(value), "deployment", file_path="d.toml")
    assert dep.training_contract.integrity_enforcement is expected


# ===========================================================================
# PARSE-F2 — a DECLARED trainable streamable MUST be an explicit boolean
#      (the `bool(...)` wrapper truthiness-coerced "false"/0, masking the parse)
# ===========================================================================

_TRAINABLE = (
    '[meta]\nkind="trainable"\nname="dt"\n'
    '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
    '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="qwen"\n'
    '[trainable]\n[trainable.config]\n'
    '[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
    '[trainable.reads]\nnpc_state={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n'
)


def _trainable_streamable(line: str) -> str:
    """`_TRAINABLE` with a `streamable = …` line inserted into the [trainable] node (none when
    `line` is empty — the omitted-streamable default path)."""
    return _TRAINABLE.replace("[trainable]\n", f"[trainable]\n{line}")


@pytest.mark.parametrize("value", ['"false"', "1"])
def test_pf2_non_boolean_streamable_rejects(value):
    """A DECLARED non-boolean [trainable].streamable raises MALFORMED_DECLARATION / R-handler-010
    (the trainable node's owning rule) — the same explicit-boolean discipline as PARSE-F1
    (trainable.schema.toml § [trainable]: `streamable = true|false`). RED if the
    `isinstance(raw["streamable"], bool)` guard is removed AND the `bool(...)` wrapper restored —
    then `bool("false")` is True (any non-empty string is truthy) and the declaration loads clean."""
    with pytest.raises(ContractViolation) as exc:
        loads(_trainable_streamable(f"streamable = {value}\n"), "composition", file_path="c.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-handler-010"
    assert exc.value.section_path == "trainable.streamable"


def test_pf2_boolean_or_absent_streamable_still_parses():
    """The valid path is unbroken: a real boolean streamable parses to that boolean, and an
    omitted streamable defaults to False (the forward-stub default)."""
    declared = loads(_trainable_streamable("streamable = true\n"), "composition", file_path="c.toml")
    omitted = loads(_trainable_streamable(""), "composition", file_path="c.toml")
    assert declared.trainable.streamable is True
    assert omitted.trainable.streamable is False


# ===========================================================================
# PARSE-F3 — a malformed FIELD in a non-handler schema section cites the
#      OWNING rule, not the generic handler-flavored R-handler-006 fallback
#      (the structural rejection was always right; only the cited rule was wrong)
# ===========================================================================


def test_pf3_service_type_malformed_field_cites_r_service_type_001():
    """A malformed field in a service-type [identity_schema] (a non-boolean `nullable`) raises
    MALFORMED_DECLARATION citing R-service-type-001 — the service-type's OWN rule — NOT the generic
    R-handler-006 (the `_malformed_field` path; PARSE-F3). RED if the owning rule_id threading is
    reverted (then `_malformed_field` falls back to its R-handler-006 default). The
    (MALFORMED_DECLARATION, R-service-type-001) pair was registered in errors.CHECK_REGISTRY to
    admit this raise. (`nullable` — not the retired `description` vehicle, which now rejects earlier
    as an inadmissible field key — is the sibling malformed-field check on the same threading.)"""
    with pytest.raises(ContractViolation) as exc:
        loads('name="s"\n[identity_schema]\nm={type="str", nullable=123}\n'
              '[transport_schema]\ne={type="str"}\n[config_schema]\n',
              "service_type", file_path="x.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-service-type-001"  # the owning rule, not R-handler-006
    assert exc.value.section_path == "identity_schema.m"


def test_pf3_pipeline_malformed_field_cites_r_pipeline_001():
    """A malformed field in a pipeline [inputs] (declares neither `type` nor `fields`) raises
    MALFORMED_DECLARATION citing R-pipeline-001 — the pipeline's OWN rule — NOT R-handler-006 (the
    inline parse_field MALFORMED_DECLARATION site, a distinct code path from `_malformed_field`;
    PARSE-F3). RED if the owning rule_id threading is reverted (the inline site falls back to its
    R-handler-006 default)."""
    with pytest.raises(ContractViolation) as exc:
        loads('[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.x"\n'
              '[inputs]\ni={nullable=true}\n',  # a typeless field — neither `type` nor `fields`
              "pipeline", file_path="p.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-pipeline-001"  # the owning rule, not R-handler-006
    assert exc.value.section_path == "inputs.i"


def test_pf3_handler_malformed_field_still_cites_r_handler_006():
    """The default is unchanged: a malformed field in a HANDLER schema section still cites
    R-handler-006 (the correct owner for handler declarations) — the threading defaults to it, so
    the common path is untouched (a guard against over-rotating the rule_id off handlers)."""
    with pytest.raises(ContractViolation) as exc:
        loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str", nullable=123}\n',
              "handler", file_path="h.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-handler-006"
    assert exc.value.section_path == "output_schema.o"


# ===========================================================================
# PARSE-F3 sibling — the unknown-bare-key CLOSED_GRAMMAR diagnostic (a DIFFERENT
#      check than the MALFORMED_DECLARATION above) likewise cites the OWNING rule,
#      not the hardcoded R-handler-006. The (CLOSED_GRAMMAR, R-service-type-001)
#      pair was already registered in errors.CHECK_REGISTRY.
# ===========================================================================


def test_unknown_bare_field_key_cites_owning_rule_service_type():
    """An unknown bare field key (neither a structural key nor a standard validation keyword)
    in a service-type [config_schema] field raises CLOSED_GRAMMAR citing R-service-type-001 —
    the service-type's OWN rule — NOT the generic R-handler-006. RED if the unknown-bare-key
    diagnostic's hardcoded `rule_id="R-handler-006"` is restored (it now threads the section's
    owning `rule_id`, like the MALFORMED_DECLARATION sites PARSE-F3 fixed)."""
    with pytest.raises(ContractViolation) as exc:
        loads('name="s"\n[identity_schema]\nm={type="str"}\n[transport_schema]\ne={type="str"}\n'
              '[config_schema]\nc={type="str", bogus_key=1}\n', "service_type", file_path="x.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-service-type-001"  # the owning rule, not R-handler-006
    assert exc.value.section_path == "config_schema.c"


def test_unknown_bare_field_key_still_cites_r_handler_006_for_handlers():
    """The default is unchanged: an unknown bare field key in a HANDLER schema section still
    cites R-handler-006 (the correct owner) — a guard against over-rotating the rule_id off
    handlers when threading the owning rule into the unknown-bare-key diagnostic."""
    with pytest.raises(ContractViolation) as exc:
        loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str", bogus_key=1}\n',
              "handler", file_path="h.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-006"
    assert exc.value.section_path == "output_schema.o"


# ===========================================================================
# PARSE-F3 sibling (channel-type-token-rule) — the CHANNEL_TYPE_TOKEN diagnostic
#      (a malformed channel-field TYPE token, distinct from the malformed-FIELD and
#      unknown-bare-KEY siblings above) likewise cites the OWNING rule, not the
#      hardcoded R-handler-006. The (CHANNEL_TYPE_TOKEN, R-pipeline-001) and
#      (CHANNEL_TYPE_TOKEN, R-service-type-001) pairs were registered in
#      errors.CHECK_REGISTRY to admit these raises (the new-registry-pairs promote
#      trigger that split this fix off from the rest of PARSE-F3). The token grammar
#      is owned by handler/reference.md § Types allowed; the diagnostic routes the
#      author to the SECTION's owning rule.
# ===========================================================================


def test_ctt_pipeline_inputs_token_cites_r_pipeline_001():
    """A malformed channel-field type token in a pipeline [inputs] field raises
    CHANNEL_TYPE_TOKEN citing R-pipeline-001 — the pipeline's OWN rule — NOT the generic
    R-handler-006. Exercises the top-level `parse_field → parse_type_token` threading.
    RED if the channel-type-token threading is reverted (then `_violate` falls back to its
    R-handler-006 default). Canon: pipeline/reference.md § `inputs`/`outputs` — the same
    token grammar handler `reads`/`output_schema` use; the pipeline declaration's owner is
    R-pipeline-001."""
    with pytest.raises(ContractViolation) as exc:
        loads('[meta]\nname="acme.p"\n[inputs]\nx={type="frobnicate"}\n',
              "pipeline", file_path="p.toml")
    assert exc.value.check is Check.CHANNEL_TYPE_TOKEN
    assert exc.value.rule_id == "R-pipeline-001"  # the owning rule, not R-handler-006
    assert exc.value.section_path == "inputs.x"


# One malformed token per RECURSIVE shape — each drives a DISTINCT recursive call site in
# `parse_type_token` (list item / dict value / tuple member / optional inner / Literal
# member). Threading the owning `rule_id` through each is a separate line; this set makes
# every one RED-on-removal (reverting any single recursive call site's `rule_id=rule_id`
# leaves its shape's test failing). The recursion is section-independent, so exercising the
# full shape set against ONE owner (pipeline [outputs]) proves every call site threads
# whatever rule_id it is handed; the service-type tests then prove the rule flows from that
# entry call site too.
_NESTED_MALFORMED = [
    "list[frobnicate]",        # list-item recursion
    "dict[str, frobnicate]",   # dict-value recursion
    "tuple[frobnicate, str]",  # tuple-member recursion
    "frobnicate | None",       # optional-inner recursion
    "Literal[1.5]",            # Literal-member path (_parse_literal_value → _violate)
]


@pytest.mark.parametrize("token", _NESTED_MALFORMED)
def test_ctt_pipeline_nested_token_cites_r_pipeline_001(token):
    """A malformed token NESTED at any recursive depth in a pipeline [outputs] field still
    cites R-pipeline-001 — biting the RECURSIVE-call-site threading specifically (the inner
    `parse_type_token` / `_parse_literal_value`, not just the top-level `parse_field` call).
    RED if the corresponding recursive call site drops `rule_id` (the inner `_violate` then
    defaults to R-handler-006). Canon: as above; the grammar recurses for collections /
    optionals / literals."""
    with pytest.raises(ContractViolation) as exc:
        loads(f'[meta]\nname="acme.p"\n[outputs]\ny={{type="{token}"}}\n',
              "pipeline", file_path="p.toml")
    assert exc.value.check is Check.CHANNEL_TYPE_TOKEN
    assert exc.value.rule_id == "R-pipeline-001"  # the owning rule, threaded through recursion
    assert exc.value.section_path == "outputs.y"


# One malformed token per NON-RECURSIVE (top-level) `_violate` flavor inside parse_type_token
# — each drives a DISTINCT direct raise site. The `frobnicate` test above bites only the
# "unrecognized token" site; without these, reverting `, rule_id` on any single other direct
# site (bytes / empty / bad-union / unknown-constructor / non-str-dict-key / Literal-empty /
# table-not-allowed / non-string) would silently fall back to R-handler-006 and stay GREEN
# (the CHECK_REGISTRY pair-guard still accepts R-handler-006 for CHANNEL_TYPE_TOKEN, so it
# does not catch a per-site fallback). The `(check, rule_id)` value is the seal; this set makes
# every reachable direct site RED-on-removal. (The doubly-optional guard `_violate` at the
# `inner is OptionalType` branch is unreachable — the first top-level `|` makes `left` never
# itself optional, so `str | None | None` raises at the union-RHS site instead — so it has no
# driving case.) The token value as it appears in the TOML field body (`123` is a bare TOML
# int, exercising the non-string-token site).
_DIRECT_MALFORMED = [
    "123",                # non-string token (TOML int) — line `type must be a string token`
    '""',                 # empty token
    '"str | Foo"',        # union right-hand side is not None
    '"Literal[]"',        # Literal with no members
    '"dict[int, str]"',   # dict with a non-str key type
    '"set[str]"',         # unknown collection constructor
    '"bytes"',            # bytes has no TOML token
    '"table"',            # `table` outside a service-type [config_schema] (allow_table False)
]


@pytest.mark.parametrize("type_value", _DIRECT_MALFORMED)
def test_ctt_pipeline_direct_token_flavors_cite_r_pipeline_001(type_value):
    """Every NON-RECURSIVE malformed-token flavor in a pipeline [inputs] field cites
    R-pipeline-001 — sealing each direct `_violate` site individually. RED if that site drops
    `rule_id` (it then defaults to R-handler-006). Together with the recursive-shape set and
    the over-rotation guard, this makes the fix's guarantee — a malformed token at ANY shape
    cites its section's OWN rule — RED-on-removal at every reachable raise site."""
    with pytest.raises(ContractViolation) as exc:
        loads(f'[meta]\nname="acme.p"\n[inputs]\nx={{type={type_value}}}\n',
              "pipeline", file_path="p.toml")
    assert exc.value.check is Check.CHANNEL_TYPE_TOKEN
    assert exc.value.rule_id == "R-pipeline-001"  # the owning rule, not R-handler-006
    assert exc.value.section_path == "inputs.x"


def test_ctt_service_type_identity_token_cites_r_service_type_001():
    """A malformed channel-field type token in a service-type [identity_schema] field raises
    CHANNEL_TYPE_TOKEN citing R-service-type-001 — the service-type's OWN rule — NOT the
    generic R-handler-006. Top-level threading. RED if the threading is reverted. Canon:
    service-type/reference.md § Schema-field vocabulary — the channel-field type token set;
    owner R-service-type-001."""
    with pytest.raises(ContractViolation) as exc:
        loads('name="s"\n[identity_schema]\nm={type="frobnicate"}\n'
              '[transport_schema]\ne={type="str"}\n[config_schema]\n',
              "service_type", file_path="x.toml")
    assert exc.value.check is Check.CHANNEL_TYPE_TOKEN
    assert exc.value.rule_id == "R-service-type-001"  # the owning rule, not R-handler-006
    assert exc.value.section_path == "identity_schema.m"


def test_ctt_service_type_config_nested_token_cites_r_service_type_001():
    """A malformed token NESTED in a collection (`dict[str, frobnicate]`) in a service-type
    [config_schema] field still cites R-service-type-001 — exercising the recursive
    threading on the `allow_table=True` call variant. RED if a recursive call site drops
    `rule_id`."""
    with pytest.raises(ContractViolation) as exc:
        loads('name="s"\n[identity_schema]\nm={type="str"}\n'
              '[transport_schema]\ne={type="str"}\n'
              '[config_schema]\nc={type="dict[str, frobnicate]"}\n',
              "service_type", file_path="x.toml")
    assert exc.value.check is Check.CHANNEL_TYPE_TOKEN
    assert exc.value.rule_id == "R-service-type-001"  # the owning rule, threaded through recursion
    assert exc.value.section_path == "config_schema.c"


def test_ctt_handler_token_still_cites_r_handler_006():
    """The default is unchanged: a malformed channel-field type token in a HANDLER schema
    section still cites R-handler-006 (the correct owner) — a guard against over-rotating
    the rule_id off handlers when threading the owning rule into the channel-type-token
    diagnostic (mirrors the malformed-field / unknown-bare-key over-rotation guards above)."""
    with pytest.raises(ContractViolation) as exc:
        loads('[transform]\n[reads]\ni={type="str"}\n[output_schema]\no={type="frobnicate"}\n',
              "handler", file_path="h.toml")
    assert exc.value.check is Check.CHANNEL_TYPE_TOKEN
    assert exc.value.rule_id == "R-handler-006"
    assert exc.value.section_path == "output_schema.o"


# ===========================================================================
# C6 — resolve_pipeline_bindings fails loud on a registry-absent composition
#      node (the CompositionNode arm's `if comp is not None:` had no else, so a
#      None composition was silently skipped and the absent reference deferred to
#      a downstream pass that may never run on this standalone compose-time pass)
# ===========================================================================


def test_c6_resolve_absent_composition_node_fails_loud():
    """A `resolve_pipeline_bindings` pass over a pipeline whose composition node references a
    path absent from the registry raises HANDLER_NAME_RESOLUTION / R-pipeline-001 — the same
    structured violation the compile + hasher passes raise for an unresolvable composition
    reference (pipeline/reference.md § Node-name resolution), brought to the earliest pass that
    dereferences the composition. RED if the `comp is None` raise is removed: the arm silently
    skips (appends the node, returns the pipeline unchanged) and the absent reference slips
    through this pass clean."""
    reg = DeclarationRegistry()  # empty — the composition path resolves to nothing
    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "composition"\nname = "trainables/missing.toml"\n'
        '[inputs]\nx = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        resolve_pipeline_bindings(pipe, reg, base_dir="")
    assert exc.value.check is Check.HANDLER_NAME_RESOLUTION
    assert exc.value.rule_id == "R-pipeline-001"


def test_c6_present_composition_node_still_resolves():
    """The valid path is unbroken: a composition node whose path IS registered resolves without
    raising — the fix rejects only a registry-ABSENT reference, never a present one (a composition
    with no external-file preprocessor bindings is a clean pass-through)."""
    reg = DeclarationRegistry()
    reg.add_composition("trainables/dialogue.toml", loads(_TRAINABLE, "composition", file_path="c.toml"))
    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "composition"\nname = "trainables/dialogue.toml"\n'
        '[inputs]\nnpc_state = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    resolved = resolve_pipeline_bindings(pipe, reg, base_dir="")  # no raise
    assert [n.name for n in resolved.nodes] == ["trainables/dialogue.toml"]


# ===========================================================================
# 3-code item 8 — a MALFORMED (TOML-syntax-error) non-handler declaration cites
#      the kind's OWNING rule (_KIND_OWNING_RULE / the _require_mapping rule_id
#      threading), not the generic R-handler-006 fallback.
# ===========================================================================


@pytest.mark.parametrize("kind,rule_id", [
    ("service_type", "R-service-type-001"),
    ("pipeline", "R-pipeline-001"),
    ("deployment", "R-deployment-001"),
])
def test_malformed_toml_cites_the_kind_owning_rule(kind, rule_id):
    """A TOML syntax error in a NON-handler declaration cites the kind's OWNING rule (via
    `_KIND_OWNING_RULE` in `loads`), not the generic R-handler-006. RED-on-removal: revert the
    `loads` raise to a hardcoded `rule_id="R-handler-006"` and every non-handler kind mis-cites."""
    with pytest.raises(ContractViolation) as exc:
        loads("[unterminated", kind, file_path="x.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == rule_id


def test_malformed_handler_toml_still_cites_r_handler_006():
    """The default is unchanged: a TOML syntax error in a HANDLER declaration still cites
    R-handler-006 (the handler/composition owner) — a guard against over-rotating item 8's threading."""
    with pytest.raises(ContractViolation) as exc:
        loads("[transform", "handler", file_path="h.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-handler-006"


@pytest.mark.parametrize("parse_fn,rule_id", [
    (parse_service_type, "R-service-type-001"),
    (parse_pipeline, "R-pipeline-001"),
    (parse_deployment, "R-deployment-001"),
])
def test_non_mapping_declaration_cites_the_owning_rule(parse_fn, rule_id):
    """A non-mapping top-level value handed to a NON-handler parser fails `_require_mapping` citing
    the OWNING rule (item 8, the `_require_mapping` rule_id half — a distinct site from the
    `_KIND_OWNING_RULE` TOMLDecodeError path above). RED-on-removal: revert the parser's
    `_require_mapping` call to drop `rule_id` and it falls back to R-handler-006."""
    with pytest.raises(ContractViolation) as exc:
        parse_fn([1, 2, 3], file_path="x.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == rule_id


# ===========================================================================
# 3-code item 9 — a service-type MISSING a required section header cites the
#      service-type's OWN rule (the _require_present rule_id threading — the
#      missing-header sibling of the present-but-empty BODY_REQUIRED arm).
# ===========================================================================


def test_service_type_missing_section_header_cites_r_service_type_001():
    """A service-type omitting a required section header (here `[config_schema]`) raises
    SECTION_PRESENCE citing R-service-type-001 — the service-type's OWN rule — not the generic
    R-handler-006. RED-on-removal: revert the `_require_present` `rule_id` kwarg and it defaults to
    R-handler-006."""
    with pytest.raises(ContractViolation) as exc:
        loads('name="s"\n[identity_schema]\nm={type="str"}\n[transport_schema]\ne={type="str"}\n',
              "service_type", file_path="x.toml")  # no [config_schema] header
    assert exc.value.check is Check.SECTION_PRESENCE
    assert exc.value.rule_id == "R-service-type-001"
    assert exc.value.section_path == "config_schema"


# ===========================================================================
# 3-code item 10 — nullable reachability RECURSES: a `nullable` / `<T> | None`
#      nested at any depth in a service-type identity/config field is flagged
#      (field_type_contains_optional), not only a top-level Optional.
# ===========================================================================


def test_nullable_nested_in_a_collection_in_identity_schema_rejects():
    """A `nullable` reachable INSIDE a collection in a service-type [identity_schema] field (here a
    list item) raises NULLABLE_PLACEMENT / R-service-type-001 — nullable is admitted only on
    transport fields, and the reachability check now RECURSES into nested types. RED-on-removal:
    revert `field_type_contains_optional` to the top-level-only `field_type_is_optional` and the
    nested Optional slips through unflagged."""
    with pytest.raises(ContractViolation) as exc:
        loads('name="s"\n[identity_schema]\nm={type="list[str | None]"}\n'
              '[transport_schema]\ne={type="str"}\n[config_schema]\n',
              "service_type", file_path="x.toml")
    assert exc.value.check is Check.NULLABLE_PLACEMENT
    assert exc.value.rule_id == "R-service-type-001"
    assert exc.value.section_path == "identity_schema.m"


def test_nullable_nested_in_a_config_schema_dict_value_rejects():
    """The recursion also bites a [config_schema] field with a nullable nested as a dict VALUE — a
    distinct recursive call site (DictType.value). RED-on-removal as above."""
    with pytest.raises(ContractViolation) as exc:
        loads('name="s"\n[identity_schema]\nm={type="str"}\n'
              '[transport_schema]\ne={type="str"}\n'
              '[config_schema]\nc={type="dict[str, int | None]"}\n',
              "service_type", file_path="x.toml")
    assert exc.value.check is Check.NULLABLE_PLACEMENT
    assert exc.value.rule_id == "R-service-type-001"
    assert exc.value.section_path == "config_schema.c"


def test_nullable_on_a_transport_field_still_allowed():
    """The valid path is unbroken: nullable IS admitted on a [transport_schema] field (even nested),
    so a transport field carrying `str | None` parses clean — a guard against the recursion
    over-reaching into the one section that permits nullable."""
    st = loads('name="s"\n[identity_schema]\nm={type="str"}\n'
               '[transport_schema]\ne={type="str | None"}\n[config_schema]\n',
               "service_type", file_path="x.toml")
    assert [f.name for f in st.transport_schema] == ["e"]


# ===========================================================================
# C3 sibling (surprise-fixes 3-code) — the service + hook forbidden-section arms
#      mirror the transform arm's ordering: _check_forbidden_handler_sections runs
#      BEFORE _closed_grammar so the TAILORED kind-discipline diagnostic
#      (transport_schema-on-a-service; output_schema-on-a-hook) is reachable rather
#      than shadowed. Both the tailored and the generic closed-grammar path carry the
#      SAME check (CLOSED_GRAMMAR) and rule_id (R-handler-006), so these adversaries
#      bite the DISTINCTIVE tailored payload — the "kind discipline" expected phrasing
#      and the section_path the generic _closed_grammar leaves None — which the hoist
#      makes reachable and a revert re-shadows. Canon: handler/reference.md § forbidden
#      sections; architecture/exhaustive-declaration.md § Forbidden.
# ===========================================================================


def test_c3_service_transport_schema_reaches_tailored_kind_diagnostic():
    """A service declaring [transport_schema] (hook-only) raises the TAILORED kind-discipline
    forbidden-section diagnostic, not the generic closed-grammar message. Both share
    (CLOSED_GRAMMAR, R-handler-006), so the seal is the tailored payload: the "kind discipline"
    expected phrasing and the section_path the generic _closed_grammar leaves None. RED if the
    service arm's check order is reverted (then _closed_grammar shadows it: section_path is None
    and 'kind discipline' is absent)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[service]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n'
            '[service_bindings]\nllm={type="x.y"}\n[transport_schema]\ne={type="str"}\n',
            "handler", file_path="h.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-006"
    assert exc.value.section_path == "transport_schema"          # tailored; generic leaves this None
    assert "kind discipline" in exc.value.expected               # tailored expected phrasing
    assert "not part of the service grammar" in exc.value.remediation_hint


def test_c3_hook_output_schema_reaches_tailored_kind_diagnostic():
    """A hook declaring [output_schema] (a hook writes no channels) raises the TAILORED
    kind-discipline forbidden-section diagnostic, not the generic closed-grammar message. The
    seal is the tailored payload (section_path + the hook-specific remediation hint), since the
    check + rule_id match the generic path. RED if the hook arm's check order is reverted (then
    _closed_grammar shadows it: section_path is None and the tailored hint is gone)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[hook]\n[reads]\ni={type="str"}\n[service_bindings]\n[transport_schema]\n'
            '[output_schema]\no={type="str"}\n',
            "handler", file_path="h.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-006"
    assert exc.value.section_path == "output_schema"             # tailored; generic leaves this None
    assert "kind discipline" in exc.value.expected               # tailored expected phrasing
    assert "a hook returns None and writes no channels" in exc.value.remediation_hint
