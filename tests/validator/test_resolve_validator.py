"""The field-validator machinery (N1) — the third sibling resolution path
(``validator.resolve_validator``), the R-handler-012 compose-time binding contract
(signature check / data-only params / engine-owned partial application), the closed
verdict protocol through the generated models (``validator.model_gen``), the built-in
attachable constraints (``validator.constraints``), and the ``validators`` entry
grammar at parse (``validator.tokens``).

Same fixture posture as ``test_resolve_handler.py``: real ``tmp_path`` validator
modules on ``sys.path`` (the step-3 source-AST audit genuinely reads the file) and a
real ``.dist-info`` fixture for the ``conjured.validators`` entry-points path — no
engine seam is patched.
"""

from __future__ import annotations

import textwrap
import uuid

import pytest
from pydantic import ValidationError

from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import (
    FieldDecl,
    ValidatorSpec,
    dict_of,
    list_of,
    literal,
    nested,
    optional,
    primitive,
    tuple_of,
)
from conjured.validator.constraints import BUILTIN_VALIDATOR_NAMES
from conjured.validator.model_gen import build_model
from conjured.validator.resolve_validator import (
    FIELD_VALIDATOR_ERROR_TYPE,
    BoundValidator,
    FieldValidatorFailure,
    resolve_builtin_constraint,
    resolve_field_validator,
)
from conjured.validator.tokens import parse_field

TOML = "handlers/fixture.toml"


@pytest.fixture()
def module_dir(tmp_path, monkeypatch):
    """A real on-disk module home, prepended to sys.path; modules written here resolve
    through the genuine import machinery (find_spec -> source read -> import)."""
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, source: str) -> str:
    """Write a uniquely named module file; returns the module name (unique per test so
    sys.modules never carries state across tests)."""
    name = f"vmod_{uuid.uuid4().hex[:10]}"
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    import importlib

    importlib.invalidate_caches()
    return name


def _write_dist_info(module_dir, dist_name: str, ep_line: str) -> None:
    info = module_dir / f"{dist_name}-0.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist_name}\nVersion: 0.1\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        f"[conjured.validators]\n{ep_line}\n", encoding="utf-8"
    )


IN_RANGE = """
def in_range(*, value, min, max):
    if value < min:
        return f"value {value} below {min}"
    if value > max:
        return f"value {value} above {max}"
    return None
"""


# ===========================================================================
# The one validation grammar at parse (tokens.parse_field) — D8: bare standard
# keywords + namespaced (dotted) third-party validators; no `validators` list
# ===========================================================================


def test_parse_dotted_key_is_parameterless_spec():
    """A namespaced (dotted) key with an empty table is a parameterless validator."""
    decl = parse_field(
        "release_date",
        {"type": "str", "mypkg.is_iso_date": {}},
        file_path="h.toml", section_path="output_schema",
    )
    assert decl.validators == (ValidatorSpec(name="mypkg.is_iso_date", params={}),)
    assert dict(decl.validators[0].params) == {}


def test_parse_dotted_key_value_is_the_params_table():
    decl = parse_field(
        "year",
        {"type": "int", "mypkg.in_range": {"min": 1900, "max": 2100}},
        file_path="h.toml", section_path="output_schema",
    )
    (spec,) = decl.validators
    assert spec.name == "mypkg.in_range"
    assert dict(spec.params) == {"min": 1900, "max": 2100}


def test_parse_interleaves_builtin_and_third_party_in_authored_order():
    """Bare standard keywords and namespaced validators are ONE ordered tuple, in
    authored key order across both classes (D8)."""
    decl = parse_field(
        "x",
        {"type": "str", "mypkg.check": {}, "minLength": 4, "mypkg.other": {"n": 1}},
        file_path="h.toml", section_path="reads",
    )
    assert [(s.name, dict(s.params)) for s in decl.validators] == [
        ("mypkg.check", {}),
        ("minLength", {"limit": 4}),
        ("mypkg.other", {"n": 1}),
    ]


def test_parse_new_draft2020_keywords_as_direct_field_keys():
    """Fix 1 — the new bare keywords parse as DIRECT field keys (tokens.py), normalized to
    the documented internal param name via DIRECT_KEY_PARAM: `multipleOf` → `multiple`,
    `minItems`/`maxItems` → `limit`, `uniqueItems` → `unique` (the boolean carried directly),
    `minProperties`/`maxProperties` → `limit`. No `validators` list, no entry-points lookup."""
    decl = parse_field(
        "x",
        {"type": "list[int]", "minItems": 1, "maxItems": 5, "uniqueItems": True},
        file_path="h.toml", section_path="reads",
    )
    assert [(s.name, dict(s.params)) for s in decl.validators] == [
        ("minItems", {"limit": 1}),
        ("maxItems", {"limit": 5}),
        ("uniqueItems", {"unique": True}),
    ]
    decl_num = parse_field(
        "y", {"type": "int", "multipleOf": 4},
        file_path="h.toml", section_path="reads",
    )
    assert [(s.name, dict(s.params)) for s in decl_num.validators] == [("multipleOf", {"multiple": 4})]


def test_parse_dotted_key_non_mapping_value_rejects():
    """A namespaced validator key carries its params TABLE as its value ({} when
    parameterless); a non-mapping value is malformed."""
    with pytest.raises(ContractViolation) as exc:
        parse_field(
            "x", {"type": "str", "mypkg.check": "not-a-table"},
            file_path="h.toml", section_path="reads",
        )
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-handler-006"


def test_parse_unknown_bare_key_rejects_naming_the_vocabulary():
    """A bare key that is neither structural nor a standard validation keyword is a
    closed-grammar CV naming the vocabulary (a third-party key must be dotted)."""
    with pytest.raises(ContractViolation) as exc:
        parse_field(
            "x", {"type": "str", "is_iso_date": {}},  # bare, unknown — must be namespaced
            file_path="h.toml", section_path="reads",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert exc.value.rule_id == "R-handler-006"
    assert "namespaced" in exc.value.actual or "dotted" in exc.value.actual


def test_parse_retired_validators_list_key_is_an_unknown_bare_key():
    """The `validators` list GONE (D8): a `validators` key is now exactly an unknown
    bare key — a loud closed-grammar CV, not deprecated, absent."""
    with pytest.raises(ContractViolation) as exc:
        parse_field(
            "x", {"type": "str", "validators": ["mypkg.check"]},
            file_path="h.toml", section_path="reads",
        )
    assert exc.value.check is Check.CLOSED_GRAMMAR
    assert "validators" in str(exc.value.actual)


# ===========================================================================
# The third sibling resolution path — dotted / entry-points (third-party)
# ===========================================================================


def test_dotted_resolution_binds_and_verdicts(module_dir):
    mod = _write_module(module_dir, IN_RANGE)
    bound = resolve_field_validator(
        ValidatorSpec(name=f"{mod}.in_range", params={"min": 1900, "max": 2100}),
        toml_path=TOML,
    )
    assert isinstance(bound, BoundValidator)
    assert bound.qualified_name == f"{mod}.in_range"
    # Engine-owned partial application: the bound callable takes only `value`.
    assert bound.bound(value=2000) is None
    assert bound.bound(value=1492) == "value 1492 below 1900"


def test_entry_point_resolution_happy(module_dir):
    """The EP group is consulted FIRST keyed by the full DOTTED name (D8 — validator EP
    names MUST be namespaced; the sibling selector the adapter resolution uses)."""
    mod = _write_module(module_dir, "def is_caps(*, value):\n    return None if value == value.upper() else 'not caps'\n")
    ep_name = f"epns_{uuid.uuid4().hex[:8]}.is_caps"  # namespaced (dotted) EP name
    _write_dist_info(module_dir, "vepdista", f"{ep_name} = {mod}:is_caps")
    bound = resolve_field_validator(ValidatorSpec(name=ep_name), toml_path=TOML)
    # The resolved dotted form is the qualified name (the constraint_violated value),
    # not the alias.
    assert bound.qualified_name == f"{mod}.is_caps"
    assert bound.bound(value="HI") is None
    assert bound.bound(value="hi") == "not caps"


def test_entry_point_collision_fails_loud(module_dir):
    """Two distributions registering one dotted EP name fail loud (D8: true
    two-distribution EP collisions still fail loud)."""
    mod = _write_module(module_dir, "def v(*, value):\n    return None\n")
    ep_name = f"epns_{uuid.uuid4().hex[:8]}.v"
    _write_dist_info(module_dir, "vepdistb", f"{ep_name} = {mod}:v")
    _write_dist_info(module_dir, "vepdistc", f"{ep_name} = {mod}:v")
    import importlib

    importlib.invalidate_caches()
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(ValidatorSpec(name=ep_name), toml_path=TOML)
    assert exc.value.check is Check.ENTRY_POINT_COLLISION
    assert exc.value.rule_id == "R-handler-012"


def test_unknown_dotless_name_rejects(module_dir):
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(
            ValidatorSpec(name=f"nope_{uuid.uuid4().hex[:8]}"), toml_path=TOML
        )
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert exc.value.rule_id == "R-handler-012"


def test_unimportable_dotted_module_rejects():
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(
            ValidatorSpec(name=f"no_such_mod_{uuid.uuid4().hex[:8]}.fn"), toml_path=TOML
        )
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert exc.value.rule_id == "R-handler-012"


def test_module_missing_attribute_rejects(module_dir):
    mod = _write_module(module_dir, "def other(*, value):\n    return None\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(ValidatorSpec(name=f"{mod}.absent"), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert exc.value.rule_id == "R-handler-012"


def test_entry_point_stale_attr_rejects(module_dir):
    """A stale entry point: the dist-info names an importable module but a missing
    attribute — ``ep.load()`` fails, surfacing as the structured entry-point load
    failure (R-handler-012), never a raw AttributeError."""
    mod = _write_module(module_dir, "def v(*, value):\n    return None\n")
    ep_name = f"epns_{uuid.uuid4().hex[:8]}.v"
    _write_dist_info(module_dir, "vepdiste", f"{ep_name} = {mod}:absent_attr")
    import importlib

    importlib.invalidate_caches()
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(ValidatorSpec(name=ep_name), toml_path=TOML)
    cv = exc.value
    assert cv.check is Check.HANDLER_MODULE_IMPORT
    assert cv.rule_id == "R-handler-012"
    assert f"entry point '{ep_name}' loads" in cv.expected
    assert f"module '{mod}' exports 'absent_attr'" in cv.expected
    assert "entry-point load failed" in cv.actual
    assert "AttributeError" in cv.actual


def test_undecodable_validator_module_cites_R_handler_012(module_dir):
    """The decode failure branch through the VALIDATOR path cites R-handler-012 (the
    SELF-REVIEW-named rule_id pass-through deviation, pinned)."""
    name = f"vmod_{uuid.uuid4().hex[:10]}"
    # Invalid UTF-8 with no PEP-263 coding declaration — decode_source raises.
    (module_dir / f"{name}.py").write_bytes(
        b"def v(*, value):\n    return None  # caf\xe9\n"
    )
    import importlib

    importlib.invalidate_caches()
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(ValidatorSpec(name=f"{name}.v"), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert exc.value.rule_id == "R-handler-012"


def test_syntax_error_validator_module_cites_R_handler_012(module_dir):
    """The SyntaxError branch of the step-3 audit through the VALIDATOR path cites
    R-handler-012 (the rule_id pass-through deviation, pinned)."""
    mod = _write_module(module_dir, "def v(*, value:\n    return None\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(ValidatorSpec(name=f"{mod}.v"), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert exc.value.rule_id == "R-handler-012"


def test_namespace_package_rejected(module_dir):
    pkg = f"vns_{uuid.uuid4().hex[:8]}"
    (module_dir / pkg).mkdir()  # no __init__.py — a PEP 420 namespace package
    (module_dir / pkg / "val.py").write_text("def v(*, value):\n    return None\n", encoding="utf-8")
    import importlib

    importlib.invalidate_caches()
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(ValidatorSpec(name=f"{pkg}.v"), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_NAMESPACE_PACKAGE
    assert exc.value.rule_id == "R-handler-012"


def test_module_purity_audit_applies_unchanged(module_dir):
    """The R-handler-pure-module source-AST audit applies to validator modules
    unchanged (R-handler-012) — module-level mutable state rejects before import."""
    mod = _write_module(
        module_dir,
        "CACHE = {}\n\ndef v(*, value):\n    return None\n",
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(ValidatorSpec(name=f"{mod}.v"), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_PURE_MODULE
    assert exc.value.rule_id == "R-handler-pure-module"


def test_class_shape_rejected(module_dir):
    """The vector-2 function-shape seal applies unchanged (R-handler-012)."""
    mod = _write_module(
        module_dir,
        """
        class Validator:
            def __call__(self, *, value):
                return None
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(ValidatorSpec(name=f"{mod}.Validator"), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_FUNCTION_SHAPE
    assert exc.value.rule_id == "R-handler-bare-function"


def test_partial_result_shape_rejected(module_dir):
    """An author-side functools.partial is a rejected shape — pre-bound args would
    bypass the declared-parameter / hash surface; partial application is the ENGINE's
    move, after the seal, from declared data."""
    mod = _write_module(
        module_dir,
        """
        import functools

        def _base(*, value, min):
            return None

        v = functools.partial(_base, min=3)
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(ValidatorSpec(name=f"{mod}.v"), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_FUNCTION_SHAPE


# ===========================================================================
# Step 6 — the {value} ∪ declared-params signature check (R-handler-012)
# ===========================================================================


def _signature_cv(module_dir, source: str, params: dict | None = None):
    mod = _write_module(module_dir, source)
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(
            ValidatorSpec(name=f"{mod}.v", params=params or {}), toml_path=TOML
        )
    return exc.value


def test_signature_missing_declared_param(module_dir):
    cv = _signature_cv(module_dir, "def v(*, value):\n    return None\n", {"min": 1})
    assert cv.check is Check.VALIDATOR_SIGNATURE
    assert cv.rule_id == "R-handler-012"
    assert "min" in cv.remediation_hint


def test_signature_extra_undeclared_param(module_dir):
    cv = _signature_cv(module_dir, "def v(*, value, min):\n    return None\n")
    assert cv.check is Check.VALIDATOR_SIGNATURE
    assert "min" in cv.remediation_hint


def test_signature_positional_parameter(module_dir):
    cv = _signature_cv(module_dir, "def v(value):\n    return None\n")
    assert cv.check is Check.VALIDATOR_SIGNATURE


def test_signature_args_collector(module_dir):
    cv = _signature_cv(module_dir, "def v(*args, value):\n    return None\n")
    assert cv.check is Check.VALIDATOR_SIGNATURE


def test_signature_kwargs_collector(module_dir):
    cv = _signature_cv(module_dir, "def v(*, value, **kwargs):\n    return None\n")
    assert cv.check is Check.VALIDATOR_SIGNATURE


# ===========================================================================
# The parameter gate — data only; the reserved `value` kwarg
# ===========================================================================


def test_reserved_value_param_rejects():
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(
            ValidatorSpec(name="minimum", params={"value": 1}), toml_path=TOML
        )
    assert exc.value.check is Check.VALIDATOR_PARAMS
    assert exc.value.rule_id == "R-handler-012"


def test_non_data_param_rejects():
    """Parameters are data only — a callable smuggled through direct IR construction
    (TOML cannot express one) rejects at compose."""
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(
            ValidatorSpec(name="minimum", params={"limit": lambda: 1}), toml_path=TOML
        )
    assert exc.value.check is Check.VALIDATOR_PARAMS


# ===========================================================================
# The verdict shim through the generated models (model_gen wiring)
# ===========================================================================


def _model_for(field: FieldDecl):
    return build_model("M", (field,), schema_source=TOML)


def test_pass_verdict_admits_value(module_dir):
    mod = _write_module(module_dir, IN_RANGE)
    model = _model_for(
        FieldDecl(
            name="year", type=primitive("int"),
            validators=(ValidatorSpec(name=f"{mod}.in_range", params={"min": 1900, "max": 2100}),),
        )
    )
    assert model.model_validate({"year": 2000}).year == 2000


def test_failure_verdict_carries_qualified_name_and_reason(module_dir):
    mod = _write_module(module_dir, IN_RANGE)
    model = _model_for(
        FieldDecl(
            name="year", type=primitive("int"),
            validators=(ValidatorSpec(name=f"{mod}.in_range", params={"min": 1900, "max": 2100}),),
        )
    )
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"year": 1492})
    (error,) = exc.value.errors()
    assert error["type"] == FIELD_VALIDATOR_ERROR_TYPE
    assert error["ctx"]["constraint"] == f"{mod}.in_range"
    assert error["msg"] == "value 1492 below 1900"
    assert error["loc"] == ("year",)


def test_raise_is_the_validators_own_failure_never_a_verdict(module_dir):
    mod = _write_module(
        module_dir, "def v(*, value):\n    return 1 / 0\n"
    )
    model = _model_for(
        FieldDecl(name="x", type=primitive("int"), validators=(ValidatorSpec(name=f"{mod}.v"),))
    )
    with pytest.raises(FieldValidatorFailure) as exc:
        model.model_validate({"x": 1})
    # The underlying exception rides as __cause__ — the Phase-3 PipelineFailure wrap's
    # cause_class source (R-handler-012).
    assert isinstance(exc.value.__cause__, ZeroDivisionError)


def test_value_error_raise_is_not_masked_into_a_verdict(module_dir):
    """The masking regression R-handler-012 forbids: Pydantic converts a ValueError
    raised inside a model validator into a ValidationError — the shim must surface it
    as the validator's own failure instead."""
    mod = _write_module(
        module_dir, "def v(*, value):\n    raise ValueError('broken validator')\n"
    )
    model = _model_for(
        FieldDecl(name="x", type=primitive("int"), validators=(ValidatorSpec(name=f"{mod}.v"),))
    )
    with pytest.raises(FieldValidatorFailure) as exc:
        model.model_validate({"x": 1})
    assert isinstance(exc.value.__cause__, ValueError)


def test_non_none_non_str_verdict_breaks_the_closed_protocol(module_dir):
    mod = _write_module(module_dir, "def v(*, value):\n    return True\n")
    model = _model_for(
        FieldDecl(name="x", type=primitive("int"), validators=(ValidatorSpec(name=f"{mod}.v"),))
    )
    with pytest.raises(FieldValidatorFailure, match="verdict protocol is closed"):
        model.model_validate({"x": 1})


def test_validators_run_in_declaration_order(module_dir):
    """The declared validator sequence is the execution order (the hash preserves
    entry order for the same reason) — the FIRST declared failure reports."""
    mod = _write_module(
        module_dir,
        """
        def first(*, value):
            return "first failed"

        def second(*, value):
            return "second failed"
        """,
    )
    model = _model_for(
        FieldDecl(
            name="x", type=primitive("int"),
            validators=(
                ValidatorSpec(name=f"{mod}.first"),
                ValidatorSpec(name=f"{mod}.second"),
            ),
        )
    )
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"x": 1})
    (error,) = exc.value.errors()
    assert error["msg"] == "first failed"
    assert error["ctx"]["constraint"] == f"{mod}.first"


def test_nested_field_validator_fires_with_nested_loc(module_dir):
    mod = _write_module(module_dir, "def caps(*, value):\n    return None if value == value.upper() else 'not caps'\n")
    field = FieldDecl(
        name="mood",
        type=nested(
            FieldDecl(
                name="label", type=primitive("str"),
                validators=(ValidatorSpec(name=f"{mod}.caps"),),
            )
        ),
    )
    model = _model_for(field)
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"mood": {"label": "calm"}})
    (error,) = exc.value.errors()
    assert error["loc"] == ("mood", "label")
    assert error["ctx"]["constraint"] == f"{mod}.caps"


def test_build_model_with_validators_requires_schema_source(module_dir):
    mod = _write_module(module_dir, "def v(*, value):\n    return None\n")
    field = FieldDecl(
        name="x", type=primitive("int"), validators=(ValidatorSpec(name=f"{mod}.v"),)
    )
    with pytest.raises(ValueError, match="schema_source"):
        build_model("M", (field,))


def test_resolution_failure_surfaces_at_model_build(module_dir):
    """Model construction is compose time — an unresolvable validator name fails the
    build (the pipeline does not load), never deferring to dispatch."""
    field = FieldDecl(
        name="x", type=primitive("int"),
        validators=(ValidatorSpec(name=f"absent_{uuid.uuid4().hex[:8]}.v"),),
    )
    with pytest.raises(ContractViolation) as exc:
        build_model("M", (field,), schema_source=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT


# ===========================================================================
# Plain-data delivery — a validator's `value` is plain data, never a model
# instance (canon's delivery posture); the shim returns the ORIGINAL value
# ===========================================================================

SUBSCRIPTS_LABEL = """
def label_is_caps(*, value):
    if value["label"] == value["label"].upper():
        return None
    return "label not caps"
"""


def test_nested_field_validator_receives_a_plain_dict(module_dir):
    """A third-party validator on a nested-object field subscripts its value as a
    plain dict — never the generated model instance (which would TypeError and be
    misreported as the validator's own failure)."""
    mod = _write_module(module_dir, SUBSCRIPTS_LABEL)
    field = FieldDecl(
        name="mood",
        type=nested(FieldDecl(name="label", type=primitive("str"))),
        validators=(ValidatorSpec(name=f"{mod}.label_is_caps"),),
    )
    model = _model_for(field)
    # The pass path: subscripting works; the field keeps its validated (model) type.
    assert model.model_validate({"mood": {"label": "HI"}}).mood.label == "HI"
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"mood": {"label": "calm"}})
    (error,) = exc.value.errors()
    assert error["type"] == FIELD_VALIDATOR_ERROR_TYPE  # a verdict, never FieldValidatorFailure
    assert error["msg"] == "label not caps"
    assert error["ctx"]["constraint"] == f"{mod}.label_is_caps"


def test_builtin_enum_with_table_member_on_nested_field():
    """`enum` with a table member compares plain dict to plain dict (exact-type
    membership) — model-instance delivery would make the membership structurally
    unsatisfiable (dict vs model never type-match). Attached as a direct-key
    constraint (the built-in layer's home)."""
    field = FieldDecl(
        name="mood",
        type=nested(FieldDecl(name="label", type=primitive("str"))),
        validators=(ValidatorSpec(name="enum", params={"values": [{"label": "happy"}]}),),
    )
    model = _model_for(field)
    assert model.model_validate({"mood": {"label": "happy"}}).mood.label == "happy"
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"mood": {"label": "sad"}})
    (error,) = exc.value.errors()
    assert error["ctx"]["constraint"] == "enum"
    assert error["msg"] == "expected one of [{'label': 'happy'}], got {'label': 'sad'}"


def test_validator_carrying_nested_member_inside_list_of(module_dir):
    """Two pinned properties in one declaration: ``schema_source`` threads through the
    ListType recursion (the validator-carrying member composes — no engine-internal
    ValueError), and the validator receives plain data through the container too."""
    mod = _write_module(module_dir, SUBSCRIPTS_LABEL)
    field = FieldDecl(
        name="items",
        type=list_of(
            nested(
                FieldDecl(
                    name="mood",
                    type=nested(FieldDecl(name="label", type=primitive("str"))),
                    validators=(ValidatorSpec(name=f"{mod}.label_is_caps"),),
                )
            )
        ),
    )
    model = _model_for(field)  # composes — the kwarg survived the list recursion
    got = model.model_validate({"items": [{"mood": {"label": "HI"}}]})
    assert got.items[0].mood.label == "HI"
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"items": [{"mood": {"label": "calm"}}]})
    (error,) = exc.value.errors()
    assert error["type"] == FIELD_VALIDATOR_ERROR_TYPE
    assert error["msg"] == "label not caps"
    assert error["loc"] == ("items", 0, "mood")


def test_validator_carrying_nested_member_inside_optional(module_dir):
    """``schema_source`` threads through the OptionalType recursion — a
    validator-carrying nested member wrapped in ``<T> | None`` composes and fires."""
    mod = _write_module(module_dir, SUBSCRIPTS_LABEL)
    field = FieldDecl(
        name="maybe",
        type=optional(
            nested(
                FieldDecl(
                    name="mood",
                    type=nested(FieldDecl(name="label", type=primitive("str"))),
                    validators=(ValidatorSpec(name=f"{mod}.label_is_caps"),),
                )
            )
        ),
    )
    model = _model_for(field)  # composes — the kwarg survived the optional recursion
    assert model.model_validate({"maybe": None}).maybe is None
    assert model.model_validate(
        {"maybe": {"mood": {"label": "HI"}}}
    ).maybe.mood.label == "HI"
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"maybe": {"mood": {"label": "calm"}}})
    assert any(
        e["type"] == FIELD_VALIDATOR_ERROR_TYPE and e["msg"] == "label not caps"
        for e in exc.value.errors()
    )


def test_nullable_field_none_skips_the_constraint_layer(module_dir):
    """Null-skip (handler/reference.md § Validators, Nullable fields — the ruled N2
    follow-up): an admitted ``None`` on a nullable (``<T> | None``) field passes the
    constraint layer untouched — the validator is skipped; a present value still
    reaches it. The always-failing verdict proves both directions."""
    mod = _write_module(
        module_dir, 'def records(*, value):\n    return f"received {value!r}"\n'
    )
    field = FieldDecl(
        name="hint", type=optional(primitive("str")),
        validators=(ValidatorSpec(name=f"{mod}.records"),),
    )
    model = _model_for(field)
    # None passes untouched — nullability is the type token's axis, never a constraint's.
    assert model.model_validate({"hint": None}).hint is None
    # A present value still reaches the validator (the skip is None-only).
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"hint": "x"})
    (error,) = exc.value.errors()
    assert error["type"] == FIELD_VALIDATOR_ERROR_TYPE
    assert error["msg"] == "received 'x'"


def test_nullable_field_none_skips_a_builtin_constraint():
    """The null-skip covers the built-in layer through the same shared shim: a
    ``minLength`` on a nullable string admits None and still rejects a present
    too-short value."""
    field = FieldDecl(
        name="hint", type=optional(primitive("str")),
        validators=(ValidatorSpec(name="minLength", params={"limit": 2}),),
    )
    model = _model_for(field)
    assert model.model_validate({"hint": None}).hint is None
    assert model.model_validate({"hint": "ab"}).hint == "ab"
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"hint": "a"})
    (error,) = exc.value.errors()
    assert error["ctx"]["constraint"] == "minLength"


# ===========================================================================
# The built-in attachable constraints (validator.constraints)
# ===========================================================================


def test_builtin_attachable_name_set_is_the_full_applicable_draft2020_vocab():
    """Fix 1 (`validator-draft2020`): the bare attachable names ARE the JSON Schema
    draft-2020-12 validation keywords applicable to the engine's channel types — "not a
    hand-rolled engine matrix" (handler/reference.md § Validators) — by family: numeric
    (incl. `multipleOf`), string, array cardinality (`minItems` / `maxItems`) + array
    distinctness (`uniqueItems`), object (`minProperties` / `maxProperties`), and `enum`.
    Deliberately NOT attachable:
    `keys_subset_of` (the closed shape produces it structurally), and the draft keywords
    that are field axes or have no channel-type surface (`type` / `required` / `nullable`,
    `const`, `maxContains` / `minContains`, `dependentRequired` — see the constraints module
    docstring)."""
    assert BUILTIN_VALIDATOR_NAMES == {
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "minLength", "maxLength", "pattern",
        "minItems", "maxItems", "uniqueItems",
        "minProperties", "maxProperties",
        "enum",
    }
    for absent in ("keys_subset_of", "const", "required", "type", "dependentRequired"):
        assert absent not in BUILTIN_VALIDATOR_NAMES


#: A type each built-in keyword is applicable to (the JSON-Schema applicability
#: families) — the direct-key resolver checks applicability against the field's type.
_APPLICABLE_TYPE = {
    "minimum": primitive("int"),
    "maximum": primitive("int"),
    "exclusiveMinimum": primitive("int"),
    "exclusiveMaximum": primitive("int"),
    "multipleOf": primitive("int"),
    "minLength": primitive("str"),
    "maxLength": primitive("str"),
    "pattern": primitive("str"),
    "minItems": list_of(primitive("int")),
    "maxItems": list_of(primitive("int")),
    "uniqueItems": list_of(primitive("int")),
    "minProperties": dict_of(primitive("int")),
    "maxProperties": dict_of(primitive("int")),
    "enum": primitive("str"),
}


def _bound_builtin(name: str, **params):
    """Resolve a built-in the way the loader's direct-key normalization reaches it —
    through ``resolve_builtin_constraint`` with an applicable field type."""
    return resolve_builtin_constraint(
        ValidatorSpec(name=name, params=params),
        field_type=_APPLICABLE_TYPE[name], toml_path=TOML,
    )


@pytest.mark.parametrize(
    ("name", "params", "passing", "failing", "message"),
    [
        ("minimum", {"limit": 10}, 10, 9, "value 9 below minimum 10"),
        # The canon example message, verbatim (error-channel/reference.md § payload).
        ("maximum", {"limit": 10}, 10, 11, "value 11 above maximum 10"),
        ("exclusiveMinimum", {"limit": 10}, 11, 10, "value 10 not above exclusiveMinimum 10"),
        ("exclusiveMaximum", {"limit": 10}, 9, 10, "value 10 not below exclusiveMaximum 10"),
        ("minLength", {"limit": 2}, "ab", "a", "length 1 below minLength 2"),
        ("maxLength", {"limit": 2}, "ab", "abc", "length 3 above maxLength 2"),
        ("pattern", {"pattern": "^[a-z]+$"}, "abc", "ABC", "value 'ABC' does not match pattern '^[a-z]+$'"),
        # Fix 1 — the full applicable draft-2020-12 vocab:
        ("multipleOf", {"multiple": 3}, 9, 10, "value 10 is not a multiple of 3"),
        ("minItems", {"limit": 2}, [1, 2], [1], "item count 1 below minItems 2"),
        ("maxItems", {"limit": 2}, [1, 2], [1, 2, 3], "item count 3 above maxItems 2"),
        ("uniqueItems", {"unique": True}, [1, 2, 3], [1, 1], "items are not unique (duplicate 1)"),
        ("minProperties", {"limit": 2}, {"a": 1, "b": 2}, {"a": 1}, "property count 1 below minProperties 2"),
        ("maxProperties", {"limit": 1}, {"a": 1}, {"a": 1, "b": 2}, "property count 2 above maxProperties 1"),
        # The canon example message, verbatim.
        (
            "enum", {"values": ["happy", "sad", "angry"]}, "happy", "confused",
            "expected one of [happy, sad, angry], got 'confused'",
        ),
    ],
)
def test_builtin_verdicts(name, params, passing, failing, message):
    bound = _bound_builtin(name, **params)
    assert bound.qualified_name == name  # built-ins carry the bare constraint name
    assert bound.bound(value=passing) is None
    assert bound.bound(value=failing) == message


def test_unique_items_distinguishes_bool_from_number():
    """uniqueItems carries JSON-Schema draft-2020-12 value-equality: a boolean is a distinct
    JSON type from a number, so ``[1, True]`` is UNIQUE (passes) where bare Python ``==`` calls
    it a duplicate (``True == 1``); the distinction recurses through nested arrays/objects.
    ``1 == 1.0`` stays a duplicate (both are JSON numbers — the documented numeric-equality
    choice). RED if ``_unique_items`` reverts to ``item in seen`` membership (then ``[1, True]``
    fails as a false duplicate). Canon: handler/reference.md § validation keywords — bare keywords
    carry the standard's semantics; the one named deviation is fail-loud inapplicability, not
    equality."""
    bound = _bound_builtin("uniqueItems", unique=True)
    # bool ≠ number — distinct, so these pass (bare `==` would flag them as duplicates):
    assert bound.bound(value=[1, True]) is None
    assert bound.bound(value=[0, False]) is None
    assert bound.bound(value=[{"a": 1}, {"a": True}]) is None      # recurses through objects
    assert bound.bound(value=[[1], [True]]) is None                # recurses through arrays
    # genuine duplicates still fail:
    assert bound.bound(value=[1, 1]) == "items are not unique (duplicate 1)"
    assert bound.bound(value=[True, True]) == "items are not unique (duplicate True)"
    # numeric 1 == 1.0 stays a duplicate (documented numeric equality, not a bool-style split):
    assert bound.bound(value=[1, 1.0]) is not None


def test_multiple_of_tolerates_float_representation_drift():
    """Fix 1 — the float-drift tolerance of ``multipleOf``: validity is ``value / multiple`` being an
    integer, checked RELATIVE to the quotient's magnitude (``math.isclose(quotient, round(quotient),
    rel_tol=…)``), so a value that IS a float multiple but whose quotient drifts (``0.3 / 0.1 ==
    2.9999…``) still PASSES — matching the standard's native implementations. RED if the relative
    tolerance is dropped to an exact ``quotient == round(quotient)`` (0.3 would then reject)."""
    bound = resolve_builtin_constraint(
        ValidatorSpec(name="multipleOf", params={"multiple": 0.1}),
        field_type=primitive("float"), toml_path=TOML,
    )
    assert bound.bound(value=0.3) is None       # a true float multiple — passes via the relative check
    assert bound.bound(value=0.30000000000001) is None  # within tolerance — passes
    assert bound.bound(value=0.35) == "value 0.35 is not a multiple of 0.1"  # genuinely not a multiple


def test_multiple_of_rejects_a_value_orders_of_magnitude_below_the_multiple():
    """Fix (35#13) — the drift tolerance is RELATIVE to the quotient, never an absolute window on the
    remainder. A value orders of magnitude smaller than ``multiple`` (``1e-13`` against ``1``) is NOT
    a multiple: its quotient ``1e-13`` rounds to ``0`` and is nowhere near it relatively. RED-on-removal:
    the reverted absolute-window check (``isclose(mod, 0, abs_tol≈1e-9)``) wrongly accepted it because
    the remainder ``1e-13`` sits inside the absolute tolerance — a false multiple, exactly the
    training-corrupting silent-accept this fix closes."""
    bound = resolve_builtin_constraint(
        ValidatorSpec(name="multipleOf", params={"multiple": 1}),
        field_type=primitive("float"), toml_path=TOML,
    )
    assert bound.bound(value=1e-13) == "value 1e-13 is not a multiple of 1"  # rejected (the fix)
    assert bound.bound(value=1e-9) == "value 1e-09 is not a multiple of 1"   # still far from a multiple
    assert bound.bound(value=3.0) is None   # a genuine multiple of 1 still passes
    assert bound.bound(value=1000000.0) is None  # magnitude does not break a true multiple


def test_pattern_is_unanchored_search():
    """JSON-Schema `pattern` semantics: the regex matches anywhere; authors anchor
    explicitly."""
    bound = _bound_builtin("pattern", pattern="b")
    assert bound.bound(value="abc") is None


def test_enum_membership_is_json_schema_value_equality():
    """The D6 ruling — `enum` membership carries the standard's value-equality
    (draft 2020-12, the same `_json_equal` uniqueItems uses): numeric 1 == 1.0 (one JSON
    number type), while a boolean is its own JSON type — True never matches member 1
    (bool strictness is standard-CORRECT, not an engine deviation)."""
    bound = _bound_builtin("enum", values=[1, 2])
    assert bound.bound(value=1) is None
    assert bound.bound(value=1.0) is None  # numeric family: 1.0 matches member 1
    assert bound.bound(value=True) == "expected one of [1, 2], got True"
    assert bound.bound(value=3.0) == "expected one of [1, 2], got 3.0"


def test_enum_on_literal_rejects_a_non_subset_at_compose():
    """Enum-on-``Literal`` coherence (handler/reference.md § Validators): an ``enum`` whose value
    set is NOT a subset of the field's ``Literal`` members is a compose-knowable contradiction —
    the engine-side model enforces type∩enum, so a foreclosed value can never pass and would fail
    EVERY dispatch. The adversary from the verification: ``Literal['happy','sad','angry']`` with
    ``enum = ['happy','sad','calm']`` — 'calm' is foreclosed. It MUST raise a compose-time
    ContractViolation (never defer to a per-dispatch SchemaValidationError). RED if the
    ``_check_enum_literal_subset`` guard is removed (the overwrite would silently admit 'calm')."""
    with pytest.raises(ContractViolation) as exc:
        resolve_builtin_constraint(
            ValidatorSpec(name="enum", params={"values": ["happy", "sad", "calm"]}),
            field_type=literal("happy", "sad", "angry"), toml_path=TOML,
        )
    assert exc.value.check is Check.VALIDATOR_PARAMS
    assert exc.value.rule_id == "R-handler-012"
    assert "calm" in str(exc.value)  # the foreclosed value is named


def test_enum_on_literal_membership_is_exact_type_at_compose():
    """The subset test is EXACT-type (the same anti-coercion posture as the ``_enum`` verdict and
    model_gen's Literal realization): ``True`` is not a member of ``Literal[1, 2]``. So an
    ``enum = [True]`` on a ``Literal[1, 2]`` field is foreclosed and rejects at compose, even
    though ``True == 1`` under bare equality."""
    with pytest.raises(ContractViolation) as exc:
        resolve_builtin_constraint(
            ValidatorSpec(name="enum", params={"values": [True]}),
            field_type=literal(1, 2), toml_path=TOML,
        )
    assert exc.value.check is Check.VALIDATOR_PARAMS


def test_enum_on_literal_subset_composes():
    """The coherent case: an ``enum`` that IS a subset of the Literal members composes cleanly
    (a proper subset, and the full-set case). The bound verdict enforces the enum values."""
    for values in (["happy", "sad"], ["happy", "sad", "angry"]):
        bound = resolve_builtin_constraint(
            ValidatorSpec(name="enum", params={"values": values}),
            field_type=literal("happy", "sad", "angry"), toml_path=TOML,
        )
        assert bound.bound(value="happy") is None
    # a non-Literal field is unaffected — enum applies to any type, subset check is a no-op there
    bound = resolve_builtin_constraint(
        ValidatorSpec(name="enum", params={"values": ["x", "y"]}),
        field_type=primitive("str"), toml_path=TOML,
    )
    assert bound.bound(value="x") is None


def test_enum_on_optional_literal_unwraps_before_the_subset_check():
    """The subset check unwraps ``Optional`` first (a constraint applies to the present, non-null
    value — the same ``_applicable_base`` posture the applicability check uses), so a foreclosed
    value on an ``Optional[Literal[...]]`` field still rejects at compose."""
    with pytest.raises(ContractViolation) as exc:
        resolve_builtin_constraint(
            ValidatorSpec(name="enum", params={"values": ["calm"]}),
            field_type=optional(literal("happy", "sad")), toml_path=TOML,
        )
    assert exc.value.check is Check.VALIDATOR_PARAMS


# ---------------------------------------------------------------------------
# Enum-vs-length-bound coherence (check_enum_bound_coherence) — the seal that
# restores R-handler-005 on the GBNF wire (finding 32): a co-declared enum member
# that violates a co-declared minLength/maxLength can never pass the engine-side
# model, yet the GBNF enum-only decode path would drop the length and admit it.
# ---------------------------------------------------------------------------


# verifies: enum-bound-coherence
@pytest.mark.parametrize(
    ("field_type", "validators", "foreclosed"),
    [
        # minLength: 'x' (length 1) is below the floor.
        (primitive("str"),
         (ValidatorSpec(name="enum", params={"values": ["ok", "x"]}),
          ValidatorSpec(name="minLength", params={"limit": 2})),
         "x"),
        # maxLength: 'toolong' (length 7) is above the ceiling.
        (primitive("str"),
         (ValidatorSpec(name="enum", params={"values": ["ok", "toolong"]}),
          ValidatorSpec(name="maxLength", params={"limit": 4})),
         "toolong"),
        # Order-independent: the length bound declared BEFORE the enum keyword.
        (primitive("str"),
         (ValidatorSpec(name="minLength", params={"limit": 2}),
          ValidatorSpec(name="enum", params={"values": ["ok", "x"]})),
         "x"),
        # Optional[str]: a length bound applies to the present non-null value, so the
        # enum-member coherence still fires (the members are the same strings).
        (optional(primitive("str")),
         (ValidatorSpec(name="enum", params={"values": ["ok", "x"]}),
          ValidatorSpec(name="minLength", params={"limit": 2})),
         "x"),
    ],
)
def test_enum_member_violating_length_bound_rejects_at_compose(field_type, validators, foreclosed):
    """Enum-vs-length-bound coherence (the seal ``check_enum_bound_coherence`` restores — finding
    32): where a field co-declares an ``enum`` and a length bound, EVERY enum member MUST satisfy
    the bound. The engine-side model enforces ``enum ∩ bound``, so a foreclosed member can never
    pass — and on the GBNF wire (accepted matrix ``{enum, minLength, maxLength}``) the enum-only
    decode path drops the length repetition, so the submitted grammar would admit the member the
    model rejects (the R-handler-005 literal-equal breach). It MUST raise a compose-time
    ContractViolation at model build, never defer to a per-dispatch SchemaValidationError. RED if
    ``check_enum_bound_coherence`` is removed (the seal-breaching declaration would compose and the
    GBNF grammar would silently admit the foreclosed member)."""
    field = FieldDecl(name="code", type=field_type, validators=validators)
    with pytest.raises(ContractViolation) as exc:
        _model_for(field)
    assert exc.value.check is Check.VALIDATOR_PARAMS
    assert exc.value.rule_id == "R-handler-012"
    assert repr(foreclosed) in str(exc.value)  # the foreclosed member is named in the diagnostic


def test_enum_within_length_bounds_composes():
    """The coherent case — an ``enum`` whose every member satisfies the co-declared length bounds
    composes cleanly (no false positive from ``check_enum_bound_coherence``), and the built model
    still enforces every keyword. Guards against over-rejection: this is exactly the case that
    reaches the GBNF wire, where the enum-only rendering is extensionally literal-equal because
    every member satisfies the bound by construction."""
    field = FieldDecl(
        name="code", type=primitive("str"),
        validators=(
            ValidatorSpec(name="enum", params={"values": ["ok", "fine", "good"]}),
            ValidatorSpec(name="minLength", params={"limit": 2}),
            ValidatorSpec(name="maxLength", params={"limit": 4}),
        ),
    )
    model = _model_for(field)  # composes — every member is 2..4 chars
    assert model.model_validate({"code": "ok"}).code == "ok"
    # A non-member of the right length still fails the enum keyword (both keywords stay live).
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"code": "nope"})
    assert any(e["ctx"]["constraint"] == "enum" for e in exc.value.errors())


def test_non_string_member_on_length_bounded_field_rejects_via_the_type_arm():
    """A non-string enum member on a length-bounded ``str`` field is foreclosed by the
    string TYPE layer — ``check_enum_bound_coherence`` still skips it (never a bare
    ``len()`` TypeError), and the sibling ``check_enum_type_coherence`` now adjudicates
    the type incoherence at compose: the declaration REJECTS with the type-arm
    diagnostic naming the foreclosed member, never a per-dispatch storm."""
    field = FieldDecl(
        name="code", type=primitive("str"),
        validators=(
            ValidatorSpec(name="enum", params={"values": ["ok", 7]}),
            ValidatorSpec(name="minLength", params={"limit": 2}),
        ),
    )
    with pytest.raises(ContractViolation) as exc:
        _model_for(field)
    assert exc.value.check is Check.VALIDATOR_PARAMS
    assert exc.value.rule_id == "R-handler-012"
    assert "7" in exc.value.actual and "foreclosed" in exc.value.actual


# verifies: enum-type-coherence
@pytest.mark.parametrize(
    ("field_type", "members", "foreclosed"),
    [
        # The REAUDIT #17 adversary: an int member on a `str` field composed clean and
        # split the literal-equal seal (the grammar rendered `7`, the model rejected it).
        (primitive("str"), ["ok", 7], 7),
        # bool is its own JSON type: member 1 can never match a bool value.
        (primitive("bool"), [True, 1], 1),
        # A non-integral float member on an `int` field matches no int under numeric equality.
        (primitive("int"), [1, 1.5], 1.5),
        # A string member on a numeric field.
        (primitive("float"), [1.0, "1"], "1"),
        # A bool member on a numeric field (bool ∉ the JSON number family).
        (primitive("float"), [1.0, True], True),
        # None is never satisfiable — the shared shim's null-skip means the constraint
        # layer never sees an admitted None, even on an Optional field.
        (optional(primitive("str")), ["ok", None], None),
        # Composites recurse: a list member with a foreclosed item.
        (list_of(primitive("str")), [["a"], [7]], [7]),
    ],
    ids=["str-int", "bool-int", "int-fractional", "float-str", "float-bool", "optional-none", "list-item"],
)
def test_enum_member_foreclosed_by_field_type_rejects_at_compose(field_type, members, foreclosed):
    """Member-vs-field-type coherence (``check_enum_type_coherence`` — the REAUDIT #17
    type arm beside the wave-0 length arm): every enum member must be admissible under
    the field's declared type per the post-D6 value-space semantics. A foreclosed member
    can never pass the engine-side model (type ∩ enum), and on a wire rendering the enum
    alternation the submitted grammar would admit it — the literal-equal seal split
    (R-handler-005). MUST raise a compose-time ContractViolation at model build. RED if
    ``check_enum_type_coherence`` is removed (the seal-breaching declaration composes)."""
    field = FieldDecl(name="code", type=field_type, validators=(
        ValidatorSpec(name="enum", params={"values": members}),
    ))
    with pytest.raises(ContractViolation) as exc:
        _model_for(field)
    assert exc.value.check is Check.VALIDATOR_PARAMS
    assert exc.value.rule_id == "R-handler-012"
    assert repr(foreclosed) in exc.value.actual  # the foreclosed member is named


# verifies: enum-type-coherence
def test_numeric_family_enum_composes_and_validates():
    """The D6 positive controls — the numeric family is ONE JSON type for both the
    compose-time coherence check and the runtime membership verdict: `[1]` on a float
    field composes (an int member is satisfiable through its float twin) and `1.0`
    validates against it; `[2.0]` on an int field composes and `2` validates. Guards
    against over-rejection in the exact direction the D6 ruling opened."""
    float_field = FieldDecl(name="temperature", type=primitive("float"), validators=(
        ValidatorSpec(name="enum", params={"values": [1]}),
    ))
    model = _model_for(float_field)
    assert model.model_validate({"temperature": 1.0}).temperature == 1.0
    with pytest.raises(ValidationError):
        model.model_validate({"temperature": 2.0})  # a non-member still fails the enum

    int_field = FieldDecl(name="count", type=primitive("int"), validators=(
        ValidatorSpec(name="enum", params={"values": [2.0]}),
    ))
    model = _model_for(int_field)
    assert model.model_validate({"count": 2}).count == 2


# verifies: enum-type-coherence
def test_type_coherent_enums_compose():
    """No false positives: type-admissible members compose on their fields — bool on
    bool, strings on str, composite members shape-matching their composite type."""
    _model_for(FieldDecl(name="flag", type=primitive("bool"), validators=(
        ValidatorSpec(name="enum", params={"values": [True]}),
    )))
    _model_for(FieldDecl(name="mood", type=primitive("str"), validators=(
        ValidatorSpec(name="enum", params={"values": ["happy", "sad"]}),
    )))
    _model_for(FieldDecl(name="tags", type=list_of(primitive("str")), validators=(
        ValidatorSpec(name="enum", params={"values": [["a"], ["a", "b"]]}),
    )))


def test_builtin_wrong_param_name_is_a_signature_mismatch():
    """The same {value} ∪ declared-params check polices built-in parameter names —
    `min` is not `minimum`'s documented `limit` parameter."""
    with pytest.raises(ContractViolation) as exc:
        resolve_builtin_constraint(
            ValidatorSpec(name="minimum", params={"min": 5}),
            field_type=primitive("int"), toml_path=TOML,
        )
    assert exc.value.check is Check.VALIDATOR_SIGNATURE
    assert exc.value.rule_id == "R-handler-012"


def test_builtin_missing_param_is_a_signature_mismatch():
    """The hint's DIRECTION matters for a built-in: its signature is engine-owned (the
    author cannot edit it), so the actionable advice is to ADD the parameter to the
    bare field key — never to edit the function signature (D8: one grammar, no list)."""
    with pytest.raises(ContractViolation) as exc:
        resolve_builtin_constraint(
            ValidatorSpec(name="minimum"), field_type=primitive("int"), toml_path=TOML
        )
    assert exc.value.check is Check.VALIDATOR_SIGNATURE
    hint = exc.value.remediation_hint
    assert "engine built-in" in hint
    assert "add limit = <value> to the 'minimum' field key" in hint
    # No signature-edit advice for an engine-owned signature.
    assert "missing kwargs" not in hint and "extra kwargs" not in hint


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("minimum", {"limit": "ten"}),       # non-numeric bound
        ("minimum", {"limit": True}),        # bool is not a bound
        # Non-finite bounds (TOML-expressible): every IEEE comparison with nan is
        # False, so a nan limit would pass everything forever — the silent-no-op
        # class the compose checks foreclose.
        ("minimum", {"limit": float("nan")}),
        ("minimum", {"limit": float("inf")}),
        ("minLength", {"limit": -1}),        # negative length
        ("minLength", {"limit": 2.5}),       # non-integer length
        ("pattern", {"pattern": "["}),       # non-compiling regex
        ("pattern", {"pattern": 3}),         # non-string pattern
        ("enum", {"values": []}),            # empty enum admits nothing
        ("enum", {"values": "abc"}),         # not a list of members
        # Fix 1 — the new keywords' param-value checks:
        ("multipleOf", {"multiple": 0}),     # a zero divisor is a div-by-zero / no-op
        ("multipleOf", {"multiple": -2}),    # JSON Schema: multipleOf MUST be > 0
        ("multipleOf", {"multiple": "x"}),   # non-numeric divisor
        ("multipleOf", {"multiple": float("inf")}),  # non-finite divisor
        ("minItems", {"limit": -1}),         # negative array cardinality
        ("maxItems", {"limit": 1.5}),        # non-integer array cardinality
        ("uniqueItems", {"unique": "yes"}),  # uniqueItems is a boolean flag
        ("minProperties", {"limit": -1}),    # negative object cardinality
        ("maxProperties", {"limit": 2.5}),   # non-integer object cardinality
    ],
)
def test_builtin_malformed_param_values_reject_at_compose(name, params):
    with pytest.raises(ContractViolation) as exc:
        resolve_builtin_constraint(
            ValidatorSpec(name=name, params=params),
            field_type=_APPLICABLE_TYPE[name], toml_path=TOML,
        )
    assert exc.value.check is Check.VALIDATOR_PARAMS
    assert exc.value.rule_id == "R-handler-012"


def test_shadowing_is_structurally_impossible_disjoint_key_spaces(module_dir):
    """D8 — the two key-spaces are disjoint by construction: a bare key (`minimum`) is the
    standard vocabulary and resolves the engine table; a third-party validator MUST be
    namespaced (dotted). A `conjured.validators` registration named bare `minimum` is
    simply unreachable as a field key — the parser routes `minimum` to the built-in, never
    to a third-party lookup — so there is no shadowing case to detect (the prior fail-loud
    shadowing check is retired)."""
    mod = _write_module(module_dir, "def fake_minimum(*, value, limit):\n    return 'shadowed!'\n")
    _write_dist_info(module_dir, "vepdistd", f"minimum = {mod}:fake_minimum")
    import importlib

    importlib.invalidate_caches()
    # The built-in resolves the engine table unaffected by the bare registration.
    bound = _bound_builtin("minimum", limit=10)
    assert bound.qualified_name == "minimum"
    assert bound.bound(value=10) is None
    assert bound.bound(value=9) == "value 9 below minimum 10"


def test_validator_name_must_be_namespaced():
    """A bare name reaching the third-party resolver fails loud — the registration-time
    namespace rule surfaced at first resolution (D8). Built-ins attach as bare keys; a
    third-party validator name MUST carry a dot."""
    with pytest.raises(ContractViolation) as exc:
        resolve_field_validator(
            ValidatorSpec(name="is_iso_date", params={}), toml_path=TOML
        )
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert exc.value.rule_id == "R-handler-012"
    assert "namespace" in exc.value.remediation_hint


def test_builtin_through_generated_model():
    """A built-in (a direct-key constraint) wires into the generated model exactly as
    a third-party validator does (one mechanism)."""
    model = _model_for(
        FieldDecl(
            name="intensity", type=primitive("int"),
            validators=(ValidatorSpec(name="maximum", params={"limit": 10}),),
        )
    )
    assert model.model_validate({"intensity": 10}).intensity == 10
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"intensity": 11})
    (error,) = exc.value.errors()
    assert error["ctx"]["constraint"] == "maximum"
    assert error["msg"] == "value 11 above maximum 10"


@pytest.mark.parametrize(
    ("field_name", "field_type", "spec", "passing", "failing", "constraint", "message"),
    [
        ("step", primitive("int"), ValidatorSpec(name="multipleOf", params={"multiple": 5}),
         10, 7, "multipleOf", "value 7 is not a multiple of 5"),
        ("tags", list_of(primitive("str")), ValidatorSpec(name="minItems", params={"limit": 2}),
         ["a", "b"], ["a"], "minItems", "item count 1 below minItems 2"),
        ("tags", list_of(primitive("str")), ValidatorSpec(name="maxItems", params={"limit": 2}),
         ["a", "b"], ["a", "b", "c"], "maxItems", "item count 3 above maxItems 2"),
        ("ids", list_of(primitive("int")), ValidatorSpec(name="uniqueItems", params={"unique": True}),
         [1, 2, 3], [1, 1], "uniqueItems", "items are not unique (duplicate 1)"),
        # uniqueItems on a fixed-arity TUPLE — distinctness applies (the cardinality-vs-
        # distinctness split): a duplicated-element tuple fails with constraint_violated =
        # "uniqueItems", a distinct one passes. Strict generated models take a real tuple
        # input (no list→tuple coercion), so passing/failing are tuples. RED before the
        # split — the model build would raise ContractViolation (uniqueItems inapplicable on
        # a tuple), so model_validate would never run.
        ("pair", tuple_of(primitive("int"), primitive("int")),
         ValidatorSpec(name="uniqueItems", params={"unique": True}),
         (1, 2), (1, 1), "uniqueItems", "items are not unique (duplicate 1)"),
        ("attrs", dict_of(primitive("int")), ValidatorSpec(name="minProperties", params={"limit": 2}),
         {"a": 1, "b": 2}, {"a": 1}, "minProperties", "property count 1 below minProperties 2"),
        ("attrs", dict_of(primitive("int")), ValidatorSpec(name="maxProperties", params={"limit": 1}),
         {"a": 1}, {"a": 1, "b": 2}, "maxProperties", "property count 2 above maxProperties 1"),
    ],
)
def test_new_draft2020_keywords_enforce_through_generated_model(
    field_name, field_type, spec, passing, failing, constraint, message
):
    """Fix 1 — happy + error path through the REAL generated Pydantic model for each new
    keyword family: a passing value validates, a violating value surfaces the structured
    SchemaValidationError shape with `constraint_violated` = the keyword name and the
    verdict string as the message (the same `constraint`/`msg` ctx the existing built-ins
    carry — the error-channel SchemaValidationError payload). Exercises the full path
    (resolve → applicability → bind → AfterValidator shim in the generated model), not just
    the resolver in isolation."""
    model = _model_for(FieldDecl(name=field_name, type=field_type, validators=(spec,)))
    assert getattr(model.model_validate({field_name: passing}), field_name) == passing
    with pytest.raises(ValidationError) as exc:
        model.model_validate({field_name: failing})
    (error,) = exc.value.errors()
    assert error["ctx"]["constraint"] == constraint
    assert error["msg"] == message


# ===========================================================================
# The applicability check (the JSON-Schema mapping, fail-loud) + direct keys
# ===========================================================================


@pytest.mark.parametrize(
    ("name", "params", "field_type"),
    [
        ("minimum", {"limit": 1}, primitive("str")),       # numeric keyword on a string
        ("maximum", {"limit": 1}, primitive("bool")),      # bool is not a numeric family member
        ("minLength", {"limit": 1}, primitive("int")),     # string keyword on an int
        ("pattern", {"pattern": "x"}, primitive("float")),  # string keyword on a float
        ("pattern", {"pattern": "x"}, list_of(primitive("str"))),  # …or a collection
        # Fix 1 — the new families' inapplicability:
        ("multipleOf", {"multiple": 2}, primitive("str")),   # numeric keyword on a string
        ("multipleOf", {"multiple": 2}, primitive("bool")),  # bool is not numeric
        ("minItems", {"limit": 1}, primitive("int")),        # array keyword on an int
        ("minItems", {"limit": 1}, dict_of(primitive("int"))),  # array keyword on an object
        # A fixed-arity tuple's length is STRUCTURAL — a CARDINALITY keyword can never apply
        # (both minItems and maxItems reject; uniqueItems, a DISTINCTNESS keyword, is exempt
        # and admitted — see test_applicable_keyword_families_admit).
        ("maxItems", {"limit": 1}, tuple_of(primitive("int"), primitive("str"))),
        ("minItems", {"limit": 1}, tuple_of(primitive("int"), primitive("int"))),
        # uniqueItems is DISTINCTNESS — applicable to any ARRAY (list/tuple), so it REJECTS on a
        # NON-array: a scalar or an object. The admit path (list/tuple) is well-tested in
        # test_applicable_keyword_families_admit; this pins its reject tail, symmetric with the
        # cardinality rejects above (the fail-loud `else` already gives RED-on-removal; this pins the grain).
        ("uniqueItems", {"unique": True}, primitive("int")),       # distinctness keyword on a scalar
        ("uniqueItems", {"unique": True}, dict_of(primitive("int"))),  # …or an object
        ("minProperties", {"limit": 1}, primitive("str")),   # object keyword on a string
        ("maxProperties", {"limit": 1}, list_of(primitive("int"))),  # object keyword on a list
        # A fixed-field nested object's property count is STRUCTURAL — rejected loud.
        ("minProperties", {"limit": 1}, nested(FieldDecl(name="a", type=primitive("int")))),
    ],
)
def test_inapplicable_keyword_rejects_at_compose(name, params, field_type):
    """The named fail-loud deviation from JSON Schema's silent ignore: an inapplicable
    keyword is a composition defect, ContractViolation at compose."""
    with pytest.raises(ContractViolation) as exc:
        resolve_builtin_constraint(
            ValidatorSpec(name=name, params=params), field_type=field_type, toml_path=TOML
        )
    assert exc.value.check is Check.VALIDATOR_PARAMS
    assert exc.value.rule_id == "R-handler-012"


@pytest.mark.parametrize(
    ("name", "params", "field_type"),
    [
        ("minimum", {"limit": 1}, primitive("float")),          # numeric → float OK
        ("minimum", {"limit": 1}, optional(primitive("int"))),  # Optional unwraps first
        ("maxLength", {"limit": 3}, optional(primitive("str"))),
        ("enum", {"values": [1, 2]}, primitive("int")),         # enum → any declared type
        ("enum", {"values": [[1], [2]]}, list_of(primitive("int"))),
        # Fix 1 — the new families admit their applicable types:
        ("multipleOf", {"multiple": 2}, primitive("float")),    # numeric → float OK
        ("multipleOf", {"multiple": 2}, optional(primitive("int"))),  # Optional unwraps
        ("minItems", {"limit": 1}, list_of(primitive("str"))),  # array → list OK
        ("uniqueItems", {"unique": True}, optional(list_of(primitive("int")))),  # Optional unwraps
        # uniqueItems is DISTINCTNESS, orthogonal to cardinality — applicable to ANY array,
        # a list OR a fixed-arity tuple (incl. a heterogeneous one). RED before the split.
        ("uniqueItems", {"unique": True}, tuple_of(primitive("int"), primitive("int"))),
        ("uniqueItems", {"unique": True}, tuple_of(primitive("int"), primitive("str"))),  # heterogeneous
        ("uniqueItems", {"unique": True}, optional(tuple_of(primitive("int"), primitive("int")))),  # Optional unwraps to a tuple
        ("minProperties", {"limit": 1}, dict_of(primitive("int"))),  # object → dict OK
        ("maxProperties", {"limit": 1}, optional(dict_of(primitive("str")))),  # Optional unwraps
    ],
)
def test_applicable_keyword_families_admit(name, params, field_type):
    bound = resolve_builtin_constraint(
        ValidatorSpec(name=name, params=params), field_type=field_type, toml_path=TOML
    )
    assert bound.qualified_name == name


def test_direct_key_parses_to_the_internal_constraint_representation():
    """The loader normalizes a direct constraint key into the same internal
    representation a resolved validator binds to (handler/reference.md § Validators —
    'Built-in constraints are direct field keys'), in authored key order."""
    field = parse_field(
        "release_date",
        {"type": "str", "pattern": "^\\d{4}", "minLength": 4},
        file_path=TOML, section_path="output_schema",
    )
    assert [(c.name, dict(c.params)) for c in field.validators] == [
        ("pattern", {"pattern": "^\\d{4}"}),
        ("minLength", {"limit": 4}),
    ]


def test_direct_key_constraint_enforces_through_the_generated_model():
    """Direct-key parse → compose resolution → model enforcement, end to end: a
    violated constraint surfaces with constraint_violated = the keyword name."""
    field = parse_field(
        "release_date",
        {"type": "str", "pattern": "^\\d{4}", "minLength": 4},
        file_path=TOML, section_path="output_schema",
    )
    model = _model_for(field)
    assert model.model_validate({"release_date": "1999"}).release_date == "1999"
    with pytest.raises(ValidationError) as exc:
        model.model_validate({"release_date": "99"})
    constraints = {e["ctx"]["constraint"] for e in exc.value.errors()}
    assert "pattern" in constraints


def test_direct_key_inapplicable_keyword_fails_model_construction():
    """An inapplicable direct key parses (the type is not yet in hand at the key site)
    and rejects at compose — model construction, where the field type meets the
    keyword (handler/conformance.md § Validator resolution and parameter binding)."""
    field = parse_field(
        "count", {"type": "int", "pattern": "^x"},
        file_path=TOML, section_path="output_schema",
    )
    with pytest.raises(ContractViolation) as exc:
        _model_for(field)
    assert exc.value.check is Check.VALIDATOR_PARAMS
