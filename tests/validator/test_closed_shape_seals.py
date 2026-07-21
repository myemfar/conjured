"""Closed-shape / closed-channel seal fixes — one RED-on-removal adversary per fix.

Each fix closes a site where the compose-time floor either LEAKED a raw exception past the
closed ContractViolation channel, silently ABSORBED an undeclared element, silently CROSSED
a scope boundary canon declares structurally impossible, or resolved an external file
against the WRONG directory. Each test constructs the exact adversary and asserts the
structured raise (the ``Check`` discriminator + ``rule_id``); each goes RED if its fix is
reverted.

Canon grounding per fix:
- Non-string service-binding ``type`` (declaration + supply sides) — the module's
  no-raw-pydantic-leak guarantee (parse.py deliverable 1); handler/reference.md
  R-handler-006 / pipeline supply grammar. Constructions route through ``_construct``.
- Closed ``[pipelines.<name>]`` override grammar — deployment/reference.md
  § pipelines.<name> ("Only transport / hook_transport accept per-pipeline override";
  R-deployment-002). A typo'd or canon-forbidden override key raised, never silently
  no-opped (the I4 masking class).
- Closed kind-header body — R-handler-006: the top-level kind discriminator is a bare
  header; sub-declarations are top-level sections, so a key inside ``[transform]`` /
  ``[service]`` / ``[hook]`` is an undeclared element.
- Closed ``[training_contract]`` body — R-deployment-001 (the block declares exactly
  ``integrity_enforcement``); an unknown key raises instead of vanishing.
- Literal member grammar — handler/reference.md § Types allowed: a quoted member is one
  quoted string, the grammar defines no escaping, so ``Literal['a' 'b']`` is
  CHANNEL_TYPE_TOKEN, never the single string ``"a' 'b"``.
- Reserved ``delivery`` / ``default`` on a compile-directive binding —
  handler/reference.md § The compile directive sub-form ("the directive and its parameter
  keys ARE the complete binding declaration"; the artifact is delivered as-is, not
  copied/frozen; the node supplies nothing) — one key, one meaning.
- The AST-audit seal, both directions — trust-model vectors 3/5/7: class bodies and
  default-argument expressions execute at import (caught); pure ``pathlib.Path`` /
  ``os.path.join`` constructions are neither I/O nor instantiation (admitted).
- Merge scoping — pipeline/reference.md § merge Scope ("cross-scope merges are
  structurally impossible"; the outer merge covers only the outer pipeline's channels) +
  R-handler-006 (composition merge = internal channel conflicts only); R-pipeline-002
  owns the merge-entry checks.
- The composition binding-path anchor — validator/resolve.py: a ``{ file = "..." }``
  binding resolves relative to the directory of the declaration TOML that supplied it
  (the composition's own registered path, never the outer pipeline's directory), so the
  wrong file is never read and hashed as binding content (I2 / hash integrity).
"""

from __future__ import annotations

import textwrap

import pytest

from conjured.errors import Check, ContractViolation, ContractViolationGroup
from conjured.validator import DeclarationRegistry, compile_pipeline, loads
from conjured.validator.ast_audit import (
    audit_adapter_module_source,
    audit_handler_module_source,
)
from conjured.validator.resolve import resolve_pipeline_bindings

from . import fixtures as F


def _violations_from(fn) -> list[ContractViolation]:
    """Run ``fn`` and unwrap its raise to the flat violation list (bare CV or group)."""
    try:
        fn()
    except ContractViolationGroup as group:
        return list(group.violations)
    except ContractViolation as cv:
        return [cv]
    raise AssertionError("expected a ContractViolation; nothing raised")


# ===========================================================================
# A1 — non-string service-binding `type`: structured, never a raw pydantic leak
# ===========================================================================


def test_service_binding_decl_non_string_type_is_structured():
    """Declaration side (parse.py ServiceBindingDecl construction): a non-string
    ``type`` surfaces as MALFORMED_DECLARATION — RED if the construction bypasses
    ``_construct`` (a raw pydantic ValidationError escapes loads())."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[service]\n[reads]\nq={type="str"}\n[output_schema]\no={type="str"}\n'
            "[service_bindings]\nllm = { type = 3 }",
            "handler", file_path="x.toml",
        )
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-handler-006"


def test_trainable_service_binding_decl_non_string_type_is_structured():
    """The same declaration-side seal on the trainable's [trainable.service_bindings]."""
    bad = F.TRAINABLE_COMPOSITION.replace(
        'llm = { type = "conjured_llm.dialogue" }', "llm = { type = 3 }"
    )
    assert 'llm = { type = 3 }' in bad  # the perturbation landed
    with pytest.raises(ContractViolation) as exc:
        loads(bad, "composition", file_path="c.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION


def test_service_binding_supply_non_string_type_is_structured():
    """Supply side (parse.py ServiceBindingSupply construction) — one fix-shape, two
    sites: a non-string ``type`` in a pipeline [service_bindings.<name>] block."""
    bad = F.PIPELINE.replace(
        '[service_bindings.llm]\ntype = "conjured_llm.structured_output"',
        "[service_bindings.llm]\ntype = 7",
    )
    assert "type = 7" in bad
    with pytest.raises(ContractViolation) as exc:
        loads(bad, "pipeline", file_path="p.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-pipeline-001"


# ===========================================================================
# A2 — closed [pipelines.<name>] override grammar (R-deployment-002)
# ===========================================================================


def test_pipeline_override_typoed_section_raises():
    """A typo'd override section (``transprot``) must raise, never parse clean while the
    override silently fails to apply (falling back to the shared transport block)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            F.DEPLOYMENT + '\n[pipelines."acme.dialogue".transprot.llm]\n'
                           'endpoint = "https://other/v1"\n',
            "deployment", file_path="d.toml",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-deployment-002"


def test_pipeline_override_of_environment_posture_section_raises():
    """A canon-forbidden per-pipeline override of an environment-posture section
    (training_contract) must raise — the author would otherwise believe integrity
    enforcement changed for one pipeline while the deployment-wide value silently
    applies (the I4 masking class)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            F.DEPLOYMENT + '\n[pipelines."acme.dialogue".training_contract]\n'
                           "integrity_enforcement = false\n",
            "deployment", file_path="d.toml",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-deployment-002"


def test_pipeline_override_of_transport_still_parses():
    """Positive control: the two sanctioned override sections stay admitted."""
    decl = loads(
        F.DEPLOYMENT + '\n[pipelines."acme.dialogue".transport.llm]\n'
                       'endpoint = "https://experimental/v1"\n',
        "deployment", file_path="d.toml",
    )
    assert decl.pipelines[0].transport[0].values["endpoint"] == "https://experimental/v1"


# ===========================================================================
# A3 — closed kind-header body
# ===========================================================================


def test_kind_header_body_key_raises():
    """A key authored inside the bare-function kind-header table ([transform] body) is an
    undeclared element — silently absorbing it is the closed-shape breach."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[transform]\nname = "acme.normalize"\n[reads]\nx={type="str"}\n'
            '[output_schema]\ny={type="str"}',
            "handler", file_path="x.toml",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.section_path == "transform"


def test_kind_header_nested_section_raises():
    """A section nested under the kind header ([transform.reads] instead of [reads]) is
    the same undeclared-element class — and previously ALSO silently dropped the
    author's schema (reads defaulted empty)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[transform.reads]\nx={type="str"}\n[reads]\nx={type="str"}\n'
            '[output_schema]\ny={type="str"}',
            "handler", file_path="x.toml",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR


def test_kind_header_non_table_raises():
    """A non-table kind header (``transform = "yes"``) is malformed, not a discriminator."""
    with pytest.raises(ContractViolation) as exc:
        loads('transform = "yes"', "handler", file_path="x.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION


# ===========================================================================
# A4 — closed [training_contract] body
# ===========================================================================


def test_training_contract_unknown_key_raises():
    """An unknown key inside [training_contract] must raise — the whole table is read,
    not just ``integrity_enforcement`` (RED if parse hands _construct one kwarg and the
    stray key never reaches the extra='forbid' floor)."""
    bad = F.DEPLOYMENT.replace(
        "integrity_enforcement = true",
        'integrity_enforcement = true\nexport_mode = "jsonl"',
    )
    assert "export_mode" in bad
    with pytest.raises(ContractViolation) as exc:
        loads(bad, "deployment", file_path="d.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-deployment-001"


# ===========================================================================
# A4b — [training_contract].audit_enforcement (optional boolean, defaults false)
# (deployment/reference.md § training_contract; self-conformance-kit 3-code criterion 6)
# ===========================================================================


def test_audit_enforcement_absent_defaults_false():
    """The optional opt-in defaults to false when omitted — RED if the default flips or the
    key becomes required."""
    dep = loads(F.DEPLOYMENT, "deployment", file_path="d.toml")
    assert dep.training_contract.audit_enforcement is False


def test_audit_enforcement_explicit_boolean_admitted():
    """A present explicit boolean is admitted into the closed [training_contract] body."""
    good = F.DEPLOYMENT.replace(
        "integrity_enforcement = true",
        "integrity_enforcement = true\naudit_enforcement = true",
    )
    dep = loads(good, "deployment", file_path="d.toml")
    assert dep.training_contract.audit_enforcement is True


def test_audit_enforcement_non_boolean_rejected():
    """A non-boolean value is NOT coerced — the same fail-loud guard integrity_enforcement
    gets (a misread enforcement opt-in is training-contract corruption). RED if the
    isinstance(., bool) guard is dropped and pydantic coerces ``"yes"``."""
    bad = F.DEPLOYMENT.replace(
        "integrity_enforcement = true",
        'integrity_enforcement = true\naudit_enforcement = "yes"',
    )
    with pytest.raises(ContractViolation) as exc:
        loads(bad, "deployment", file_path="d.toml")
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-deployment-001"
    assert exc.value.section_path == "training_contract.audit_enforcement"


# ===========================================================================
# A5 — Literal member grammar: malformed quoted member raises
# ===========================================================================


def test_literal_malformed_quoted_member_raises():
    """``Literal['a' 'b']`` (no comma — one malformed member) must raise
    CHANNEL_TYPE_TOKEN, not parse as the single string member ``"a' 'b"``."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[transform]\n[reads]\nx={type="str"}\n'
            "[output_schema]\ny={type=\"Literal['a' 'b']\"}",
            "handler", file_path="x.toml",
        )
    assert exc.value.check is Check.CHANNEL_TYPE_TOKEN


def test_literal_well_formed_members_still_parse():
    """Positive control: the closed grammar's real shapes stay admitted — including a
    member carrying the OTHER quote kind inside (no escaping needed or defined)."""
    decl = loads(
        '[transform]\n[reads]\nx={type="str"}\n'
        "[output_schema]\ny={type=\"Literal['a', 'b']\"}\n"
        'z={type="Literal[\'say \\"hi\\"\']"}',
        "handler", file_path="x.toml",
    )
    (y,) = [f for f in decl.output_schema if f.name == "y"]
    assert y.type.values == ("a", "b")
    (z,) = [f for f in decl.output_schema if f.name == "z"]
    assert z.type.values == ('say "hi"',)


# ===========================================================================
# A6 (D7) — reserved `delivery` / `default` on a compile-directive binding
# ===========================================================================


@pytest.mark.parametrize(
    "reserved_line",
    ['delivery = "reference"', "default = 3", 'file = "schemas/x.json"'],
    ids=["delivery", "default", "file"],
)
def test_compile_binding_with_reserved_key_raises(reserved_line):
    """A compile-directive binding carrying an engine-read binding key (``delivery`` /
    ``default`` / ``file``) as a PARAMETER KEY raises at parse — previously the key was
    silently packed into the opaque compiler params, stripping its engine meaning (one
    key, one meaning; ``file`` is the engine's reserved external-file form)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            '[transform]\n[reads]\nx={type="str"}\n[output_schema]\ny={type="str"}\n'
            "[bindings.pattern]\ncompile = \"regex\"\npattern = \"a+\"\n" + reserved_line,
            "handler", file_path="x.toml",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-006"
    assert "compile" in exc.value.expected


def test_compile_param_file_value_form_stays_legal():
    """The other direction of the ``file`` reservation: a compile parameter supplied
    FROM a file — ``<param> = { file = "<path>" }`` — is the engine's own external-file
    form and MUST keep parsing (only the top-level param KEY ``file`` is reserved)."""
    from conjured.ir.common import CompileBinding, FilePathBindingValue

    decl = loads(
        '[transform]\n[reads]\nx={type="str"}\n[output_schema]\ny={type="str"}\n'
        '[bindings.profile_check]\ncompile = "json_schema"\nschema = { file = "schemas/profile.json" }\n',
        "handler", file_path="x.toml",
    )
    (binding,) = decl.bindings
    assert isinstance(binding.body, CompileBinding)
    param = binding.body.params["schema"]
    assert isinstance(param, FilePathBindingValue) and param.path == "schemas/profile.json"


# ===========================================================================
# B — the AST-audit seal, both directions (vectors 3/5/7)
# ===========================================================================


def test_handler_class_body_io_is_rejected():
    """Import-time I/O inside a class body of a HANDLER module — class bodies execute at
    import; RED if the handler-scope walk skips class bodies."""
    src = textwrap.dedent(
        """
        class Config:
            DATA = open("cfg.txt").read()

        def fn(*, x):
            return {"out": x}
        """
    )
    with pytest.raises(ContractViolation) as exc:
        audit_handler_module_source(src, origin="m.py")
    assert exc.value.check is Check.HANDLER_PURE_MODULE


def test_handler_class_body_mutable_literal_is_rejected():
    """Literal-form class-level mutable state in a handler module — cross-dispatch state
    the module-dict snapshot-restore cannot recover (it restores the class reference,
    not the class's own __dict__)."""
    src = "class C:\n    CACHE = {}\n\ndef fn(*, x):\n    return {'out': x}\n"
    with pytest.raises(ContractViolation) as exc:
        audit_handler_module_source(src, origin="m.py")
    assert exc.value.check is Check.HANDLER_PURE_MODULE


def test_handler_class_body_tuple_unpacked_mutable_literals_are_rejected():
    """A tuple-target unpack — ``CACHE, STORE = {}, []`` — binds each name to a mutable literal
    at class scope, exactly as the single-target form does. The RHS is an ``ast.Tuple`` (not
    itself a mutable-literal node), so the naive ``isinstance(value, _MUTABLE_LITERAL_NODES)``
    check misses it, and the class-body form is NOT recovered by the vector-3 module-dict
    snapshot. RED if the tuple-unpack recursion is dropped from the purity walk."""
    src = "class C:\n    CACHE, STORE = {}, []\n\ndef fn(*, x):\n    return {'out': x}\n"
    with pytest.raises(ContractViolation) as exc:
        audit_handler_module_source(src, origin="m.py")
    assert exc.value.check is Check.HANDLER_PURE_MODULE


def test_handler_default_argument_io_is_rejected():
    """A default-argument expression evaluates AT IMPORT — I/O in a default is
    import-time I/O (previously pruned as call-time)."""
    src = "def fn(*, x=open('cfg.txt').read()):\n    return {'out': x}\n"
    with pytest.raises(ContractViolation) as exc:
        audit_handler_module_source(src, origin="m.py")
    assert exc.value.check is Check.HANDLER_PURE_MODULE


def test_handler_mutable_default_argument_is_rejected():
    """A mutable-literal default on a module helper is cross-dispatch state that
    survives the vector-3 snapshot (the restored function object keeps its mutated
    __defaults__)."""
    src = "def _helper(k, acc=[]):\n    acc.append(k)\n    return acc\n"
    with pytest.raises(ContractViolation) as exc:
        audit_handler_module_source(src, origin="m.py")
    assert exc.value.check is Check.HANDLER_PURE_MODULE


def test_module_level_lambda_default_io_is_rejected():
    """A lambda's defaults also evaluate at import — the walker yields a pruned
    function-like node's default expressions."""
    src = "g = lambda x=open('cfg.txt'): x\n"
    with pytest.raises(ContractViolation) as exc:
        audit_handler_module_source(src, origin="m.py")
    assert exc.value.check is Check.HANDLER_PURE_MODULE


def test_module_level_lambda_mutable_default_is_rejected():
    """The lambda arm of the mutable-literal-default seal (trust-model qualified-seals
    region: function OR lambda defaults are inside the scanned surface): `g = lambda
    x=[]: x` at module scope is the def form's exact sibling — the mutated __defaults__
    persist on the function object across dispatches, invisible to the vector-3
    snapshot-restore."""
    src = "g = lambda x=[]: x\n"
    with pytest.raises(ContractViolation) as exc:
        audit_handler_module_source(src, origin="m.py")
    assert exc.value.check is Check.HANDLER_PURE_MODULE


def test_class_body_lambda_mutable_default_is_rejected_in_both_scopes():
    """The class-body pass shares the lambda arm — handler and adapter scope alike (a
    class body executes at import)."""
    src = "class C:\n    g = lambda self, x={}: x\n"
    with pytest.raises(ContractViolation) as handler_exc:
        audit_handler_module_source(src, origin="m.py")
    assert handler_exc.value.check is Check.HANDLER_PURE_MODULE
    with pytest.raises(ContractViolation) as adapter_exc:
        audit_adapter_module_source(src, origin="a.py")
    assert adapter_exc.value.check is Check.ADAPTER_PURE_MODULE


def test_nested_lambda_mutable_default_inside_def_default_is_rejected():
    """A lambda constructed inside a def's default evaluates ITS defaults at the same
    import moment — the nested form of the same seal."""
    src = "def fn(*, cb=lambda x={}: x):\n    return {'out': cb()}\n"
    with pytest.raises(ContractViolation) as exc:
        audit_handler_module_source(src, origin="m.py")
    assert exc.value.check is Check.HANDLER_PURE_MODULE


def test_lambda_inside_function_body_stays_call_time():
    """A lambda in a def BODY evaluates at call time — its mutable default is not
    import-time state; the walk must stay pruned (no over-enforcement)."""
    src = "def fn(*, x):\n    g = lambda y=[]: y\n    return {'out': g()}\n"
    audit_handler_module_source(src, origin="m.py")  # clean — no raise


def test_adapter_method_default_argument_io_is_rejected():
    """The adapter scope shares the default-argument seal: a method's default evaluates
    when the class body executes at import."""
    src = textwrap.dedent(
        """
        class Adapter:
            def __init__(self, *, model, cfg=open("c.txt").read()):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {}
        """
    )
    with pytest.raises(ContractViolation) as exc:
        audit_adapter_module_source(src, origin="a.py")
    assert exc.value.check is Check.ADAPTER_PURE_MODULE


def test_adapter_class_body_tuple_unpacked_mutable_literals_are_rejected():
    """Vector-7 namesake: a class-level mutable class variable via tuple unpack in an ADAPTER
    module — ``CACHE, STORE = {}, []`` — is cross-dispatch state, and vector 7 runs the AST scan
    ALONE (no dispatch-time snapshot backstop), so it must be caught at the walk. RED if the
    tuple-unpack recursion is dropped."""
    src = textwrap.dedent(
        """
        class Adapter:
            CACHE, STORE = {}, []

            def __init__(self, *, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {}
        """
    )
    with pytest.raises(ContractViolation) as exc:
        audit_adapter_module_source(src, origin="a.py")
    assert exc.value.check is Check.ADAPTER_PURE_MODULE


def test_pure_path_constructions_are_admitted():
    """The over-match direction (the seal must not flag what canon does not forbid):
    ``pathlib.Path(__file__)`` and ``os.path.join`` are pure constructions — neither
    I/O nor client instantiation."""
    src = textwrap.dedent(
        """
        import os
        import pathlib

        BASE = pathlib.Path(__file__)
        KEY = os.path.join("a", "b")

        def fn(*, x):
            return {"out": x}
        """
    )
    audit_handler_module_source(src, origin="m.py")  # no raise
    audit_adapter_module_source(src, origin="m.py")  # same carve-out, adapter scope


@pytest.mark.parametrize(
    "stmt",
    [
        "DATA = open('f.txt').read()",
        "TEXT = pathlib.Path('f.txt').read_text()",
        "RESP = urllib.request.urlopen('https://x')",
        "CLIENT = httpx.Client()",
        "EXISTS = os.path.exists('f.txt')",
        "CWD = os.getcwd()",
    ],
    ids=["open", "path-read_text", "urlopen", "client-instantiation", "os-path-exists", "os-getcwd"],
)
def test_io_construct_classes_keep_their_red_case(stmt):
    """Tightening the match must not weaken the seal: every construct class canon names
    (filesystem read / network call / client instantiation) still rejects."""
    src = f"import os\nimport pathlib\nimport urllib.request\nimport httpx\n{stmt}\n"
    with pytest.raises(ContractViolation):
        audit_handler_module_source(src, origin="m.py")


# ===========================================================================
# D — merge scoping: cross-scope merges fail loud (37#2)
# ===========================================================================


def _compile_trainable(reg, pipeline):
    compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")


def test_composition_merge_on_its_own_boundary_channel_raises():
    """(a) A composition merge entry naming one of its own BOUNDARY channels would be
    silently promoted into the outer pipeline's merge table (scope() leaves boundary
    names unscoped) — composition merge governs internal conflicts only."""
    reg, pipeline = F.build_trainable()
    bad_comp = F.TRAINABLE_COMPOSITION + '\n[merge]\nnpc_state = "last_wins"\n'
    reg.add_composition(
        "trainables/dialogue.toml", loads(bad_comp, "composition", file_path="c.toml")
    )
    violations = _violations_from(lambda: _compile_trainable(reg, pipeline))
    matching = [
        v for v in violations
        if v.check is Check.CHANNEL_WRITE_OVERLAP and "boundary" in v.actual
    ]
    assert matching, [v.actual for v in violations]
    assert matching[0].rule_id == "R-pipeline-002"


def test_outer_merge_key_reaching_a_scoped_channel_raises():
    """(b) An outer-pipeline merge key literally spelled ``<meta.name>.<channel>``
    would reach and govern the composition's internal scoped channel — cross-scope,
    structurally impossible per canon."""
    reg, _ = F.build_trainable()
    bad_pipeline = loads(
        F.PIPELINE_WITH_COMPOSITION
        + '\n[merge]\n"dialogue_training.formatted_prompt" = "concat_str"\n',
        "pipeline", file_path="p.toml",
    )
    violations = _violations_from(lambda: _compile_trainable(reg, bad_pipeline))
    matching = [
        v for v in violations
        if v.check is Check.CHANNEL_WRITE_OVERLAP and "scoped" in v.actual
    ]
    assert matching, [v.actual for v in violations]


def test_composition_and_outer_merge_key_collision_fails_loud():
    """(c) A composition-merge / outer-merge key collision must fail loud — the
    ``merges[scope(ch)] = st`` assignment previously overwrote silently
    (composition-last-wins, no ContractViolation)."""
    reg, _ = F.build_trainable()
    comp_with_merge = F.TRAINABLE_COMPOSITION + '\n[merge]\nformatted_prompt = "concat_str"\n'
    reg.add_composition(
        "trainables/dialogue.toml",
        loads(comp_with_merge, "composition", file_path="c.toml"),
    )
    bad_pipeline = loads(
        F.PIPELINE_WITH_COMPOSITION
        + '\n[merge]\n"dialogue_training.formatted_prompt" = "concat_str"\n',
        "pipeline", file_path="p.toml",
    )
    violations = _violations_from(lambda: _compile_trainable(reg, bad_pipeline))
    matching = [
        v for v in violations
        if v.check is Check.CHANNEL_WRITE_OVERLAP and "collision" in v.actual
    ]
    assert matching, [v.actual for v in violations]


def test_composition_internal_merge_still_scopes_cleanly():
    """Positive control: a composition merge on an INTERNAL channel lands scoped in the
    graph's merge table (the legal single-scope path is untouched)."""
    reg, pipeline = F.build_trainable()
    comp_with_merge = F.TRAINABLE_COMPOSITION + '\n[merge]\nformatted_prompt = "concat_str"\n'
    reg.add_composition(
        "trainables/dialogue.toml",
        loads(comp_with_merge, "composition", file_path="c.toml"),
    )
    graph = compile_pipeline(pipeline, reg, pipeline_name=F.NAME, file_path="p.toml")
    assert any(
        m.channel == "dialogue_training.formatted_prompt" for m in graph.merges
    )


# ===========================================================================
# E — the composition binding-path anchor (36#2)
# ===========================================================================

# The composition variant whose preprocessor `config` binding is supplied by external
# file — the anchor adversary's subject.
_FILE_BINDING_COMPOSITION = F.TRAINABLE_COMPOSITION.replace(
    'config = { template = "{context}\\n{utterance}" }',
    'config = { file = "x.toml" }',
)
assert 'config = { file = "x.toml" }' in _FILE_BINDING_COMPOSITION


def _registry_with_composition(comp_toml_path=None):
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("acme.ctx", loads(F.TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg.add_handler("transform.formatter", loads(F.TRANSFORM_FORMATTER, "handler", file_path="fmt.toml"))
    kwargs = {} if comp_toml_path is None else {"toml_path": str(comp_toml_path)}
    reg.add_composition(
        "trainables/dialogue.toml",
        loads(_FILE_BINDING_COMPOSITION, "composition",
              file_path=str(comp_toml_path or "c.toml")),
        **kwargs,
    )
    return reg


def _stamped_binding(reg):
    comp = reg.get_composition("trainables/dialogue.toml")
    (binding,) = comp.preprocessors[0].bindings
    return binding


def test_composition_file_binding_resolves_against_its_own_directory(tmp_path):
    """The two-directory adversary: the composition (dir B) carries
    ``{ file = "x.toml" }`` and a same-named file exists in the outer pipeline's dir A —
    resolution MUST read B's file. RED with the anchor map removed (the outer base_dir
    would silently read, canonicalize, and hash A's file as the binding content)."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir(); dir_b.mkdir()
    (dir_a / "x.toml").write_text('template = "from A"', encoding="utf-8")
    (dir_b / "x.toml").write_text('template = "from B"', encoding="utf-8")
    comp_path = dir_b / "dialogue.toml"

    reg = _registry_with_composition(comp_toml_path=comp_path)
    pipeline = loads(F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path=str(dir_a / "p.toml"))
    resolve_pipeline_bindings(pipeline, reg, base_dir=str(dir_a))

    binding = _stamped_binding(reg)
    assert binding.content_hash is not None
    assert binding.resolved == {"template": "from B"}


def test_shared_composition_resolution_is_order_independent(tmp_path):
    """A composition shared by two pipelines in different directories resolves against
    its OWN directory regardless of which composes first (compose-time determinism, I2)
    — RED under the old idempotent-stamp-plus-outer-base_dir behavior (first composer's
    directory would win)."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_c = tmp_path / "c"
    for d, marker in ((dir_a, "from A"), (dir_b, "from B"), (dir_c, "from C")):
        d.mkdir()
        (d / "x.toml").write_text(f'template = "{marker}"', encoding="utf-8")
    comp_path = dir_b / "dialogue.toml"

    def resolve_both(first_dir, second_dir):
        reg = _registry_with_composition(comp_toml_path=comp_path)
        for d in (first_dir, second_dir):
            pipeline = loads(
                F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path=str(d / "p.toml")
            )
            resolve_pipeline_bindings(pipeline, reg, base_dir=str(d))
        return _stamped_binding(reg)

    a_first = resolve_both(dir_a, dir_c)
    c_first = resolve_both(dir_c, dir_a)
    assert a_first.resolved == {"template": "from B"}
    assert c_first.resolved == {"template": "from B"}
    assert a_first.content_hash == c_first.content_hash


def test_composition_file_binding_without_registered_anchor_fails_loud(tmp_path):
    """A composition carrying an unresolved file binding but no registered declaration
    path has no anchor — fail loud (mirrors resolve_compile_param_files' per-handler
    contract), never resolve against the outer pipeline's directory."""
    (tmp_path / "x.toml").write_text('template = "outer"', encoding="utf-8")
    reg = _registry_with_composition(comp_toml_path=None)  # no toml_path registered
    pipeline = loads(F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path=str(tmp_path / "p.toml"))
    with pytest.raises(ContractViolation) as exc:
        resolve_pipeline_bindings(pipeline, reg, base_dir=str(tmp_path))
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED
    assert "declaration path" in exc.value.expected


# verifies: pipeline-file-anchor-fails-loud
def test_pipeline_file_binding_without_anchor_fails_loud(tmp_path):
    """The pipeline arm of the no-anchor contract (REAUDIT #16): a pipeline-level
    handler-node ``{ file }`` binding with an empty ``base_dir`` MUST fail loud — the
    old behavior silently resolved the path against the process CWD, reading (and
    hashing) whatever same-named file sat there: the exact wrong-file-hashed outcome
    the anchor rule forbids. The same declaration with a real anchor resolves against
    it (the positive control). RED with the pipeline-arm raise removed."""
    reg = DeclarationRegistry()
    reg.add_handler(
        "acme.norm",
        loads(F.TRANSFORM_NORMALIZE, "handler", file_path="norm.toml"),
    )
    pipeline_toml = (
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "handler"\nname = "acme.norm"\n'
        'bindings = { config = { file = "cfg.toml" } }\n[inputs]\nplayer_input = { type = "str" }\n'
    )
    pipeline = loads(pipeline_toml, "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        resolve_pipeline_bindings(pipeline, reg, base_dir="")
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED
    assert exc.value.rule_id == "R-pipeline-001"

    # The positive control: the SAME declaration with a real anchor resolves against it.
    (tmp_path / "cfg.toml").write_text('marker_set = "brackets"', encoding="utf-8")
    resolved = resolve_pipeline_bindings(
        loads(pipeline_toml, "pipeline", file_path=str(tmp_path / "p.toml")),
        reg, base_dir=str(tmp_path),
    )
    (binding,) = resolved.nodes[0].bindings
    assert binding.content_hash is not None
    assert binding.resolved == {"marker_set": "brackets"}  # the file's canonicalized content


def test_composition_without_file_bindings_needs_no_anchor():
    """A composition with only inline bindings resolves with no registered path — the
    anchor is required exactly where a file must be read, nowhere else."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("acme.ctx", loads(F.TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg.add_handler("transform.formatter", loads(F.TRANSFORM_FORMATTER, "handler", file_path="fmt.toml"))
    reg.add_composition(
        "trainables/dialogue.toml",
        loads(F.TRAINABLE_COMPOSITION, "composition", file_path="c.toml"),
    )
    pipeline = loads(F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path="p.toml")
    resolve_pipeline_bindings(pipeline, reg, base_dir="")  # no raise
