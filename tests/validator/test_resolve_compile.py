"""The compile-affordance resolution machinery (``validator.resolve_compile``) — the
``compile = "<compiler>"`` directive's compose-time binding: blessed bare-name resolution
(``BUILTIN_COMPILERS``), the open dotted-path third-party path (the shared
dotted-path + R-handler-pure-module audit + vector-2 shape seal), the signature/param
introspection (``COMPILE_SIGNATURE``), and the run → artifact step (``COMPILE_ARTIFACT``).
Ground: ``conjured/docs/components/handler/reference.md`` § The ``compile = "..."`` directive
sub-form (+ the per-compiler contracts).

Same fixture posture as ``test_resolve_validator.py``: real ``tmp_path`` compiler modules on
``sys.path`` (the step-3 source-AST audit genuinely reads the file) — no engine seam is patched.
The ``jinja`` / ``json_schema`` happy paths need the optional ``conjured[compilers]`` backends
(present under ``conjured[dev]``); ``regex`` uses the stdlib and always runs.
"""

from __future__ import annotations

import re
import textwrap
import uuid

import pytest

from conjured.errors import Check, ContractViolation
from conjured.validator.resolve_compile import BUILTIN_COMPILERS, resolve_and_compile

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
    name = f"cmod_{uuid.uuid4().hex[:10]}"
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    import importlib

    importlib.invalidate_caches()
    return name


# ===========================================================================
# Happy paths — each blessed compiler produces its per-contract artifact
# ===========================================================================


def test_regex_produces_compiled_pattern():
    """`regex` — parameters `pattern` + `flags`; artifact a compiled `re.Pattern`."""
    artifact = resolve_and_compile(
        "regex", {"pattern": r"\[[^\]]+\]", "flags": "IGNORECASE"}, toml_path=TOML
    )
    assert isinstance(artifact, re.Pattern)
    assert artifact.search("[Tag]") is not None
    assert artifact.flags & re.IGNORECASE


def test_regex_flags_optional():
    """`flags` is optional (a declared ship-time-style default on the compiler) — a `pattern`-only
    directive binds and compiles."""
    artifact = resolve_and_compile("regex", {"pattern": "ab+c"}, toml_path=TOML)
    assert isinstance(artifact, re.Pattern)
    assert artifact.match("abbc") is not None


def test_regex_multiple_flags():
    artifact = resolve_and_compile(
        "regex", {"pattern": "^x", "flags": "IGNORECASE|MULTILINE"}, toml_path=TOML
    )
    assert artifact.flags & re.IGNORECASE and artifact.flags & re.MULTILINE


def test_jinja_produces_template():
    """`jinja` — parameter `source`; artifact a compiled `jinja2.Template`."""
    import jinja2  # the [compilers] extra is required (dev profile) — fail loud if absent

    artifact = resolve_and_compile("jinja", {"source": "Hello, {{ name }}!"}, toml_path=TOML)
    assert isinstance(artifact, jinja2.Template)
    assert artifact.render(name="Blackwell") == "Hello, Blackwell!"


def test_json_schema_produces_validator():
    """`json_schema` — parameter `schema`; artifact a compiled `jsonschema` validator."""
    import jsonschema  # the [compilers] extra is required (dev profile) — fail loud if absent

    assert jsonschema  # exercised via resolve_and_compile below
    artifact = resolve_and_compile(
        "json_schema", {"schema": {"type": "object", "required": ["name"]}}, toml_path=TOML
    )
    assert artifact.is_valid({"name": "Ada"})
    assert not artifact.is_valid({})


def test_dotted_third_party_compiler_resolves_and_runs(module_dir):
    """A namespaced (dotted) name resolves a third-party compiler through the dotted-path
    machinery, runs it, and delivers its artifact."""
    mod = _write_module(
        module_dir,
        "def to_upper(*, text):\n    return text.upper()\n",
    )
    artifact = resolve_and_compile(f"{mod}.to_upper", {"text": "hi"}, toml_path=TOML)
    assert artifact == "HI"


# ===========================================================================
# COMPILE_ARTIFACT — the compiler ran and rejected its bound parameters
# ===========================================================================


def test_malformed_regex_raises_compile_artifact_cv():
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile("regex", {"pattern": "[unterminated"}, toml_path=TOML)
    assert exc.value.check is Check.COMPILE_ARTIFACT
    assert exc.value.rule_id == "R-pipeline-001"
    assert exc.value.file_path == TOML


def test_unknown_regex_flag_raises_compile_artifact_cv():
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile("regex", {"pattern": "x", "flags": "NO_SUCH_FLAG"}, toml_path=TOML)
    assert exc.value.check is Check.COMPILE_ARTIFACT


@pytest.mark.parametrize("flags", ["IGNORECASE|", "|IGNORECASE", "IGNORECASE||MULTILINE", "|", ""])
def test_empty_regex_flag_segment_raises_compile_artifact_cv(flags):
    """An empty flag segment — a trailing / leading / doubled ``|`` or a blank ``flags`` — raises
    the same COMPILE_ARTIFACT CV as an unknown flag name (surprise-fixes 3-code), instead of being
    silently skipped: a compose-read parameter never silently no-ops (constraints.py's
    compose-or-never posture). RED if the ``if not name: continue`` skip is restored (the empty
    segment then compiles to no-flags silently)."""
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile("regex", {"pattern": "x", "flags": flags}, toml_path=TOML)
    assert exc.value.check is Check.COMPILE_ARTIFACT


def test_unparseable_jinja_raises_compile_artifact_cv():
    import jinja2  # required by the [compilers] extra — fail loud if absent

    assert jinja2
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile("jinja", {"source": "{{ unterminated "}, toml_path=TOML)
    assert exc.value.check is Check.COMPILE_ARTIFACT


def test_invalid_json_schema_raises_compile_artifact_cv():
    import jsonschema  # required by the [compilers] extra — fail loud if absent

    assert jsonschema
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(
            "json_schema", {"schema": {"type": "not-a-real-type"}}, toml_path=TOML
        )
    assert exc.value.check is Check.COMPILE_ARTIFACT


# ===========================================================================
# COMPILE_SIGNATURE — the declared params don't bind the compiler's signature
# ===========================================================================


def test_unknown_declared_param_raises_compile_signature_cv():
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile("regex", {"pattern": "x", "flagz": "IGNORECASE"}, toml_path=TOML)
    assert exc.value.check is Check.COMPILE_SIGNATURE
    assert exc.value.rule_id == "R-pipeline-001"
    assert "flagz" in exc.value.actual


def test_missing_required_param_raises_compile_signature_cv():
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile("regex", {}, toml_path=TOML)  # no `pattern`
    assert exc.value.check is Check.COMPILE_SIGNATURE
    assert "pattern" in exc.value.actual


def test_non_kwarg_only_third_party_compiler_raises_compile_signature_cv(module_dir):
    """A third-party compiler that is not kwarg-only (a positional parameter) fails the
    signature introspection — compose-time, read from the real __code__."""
    mod = _write_module(module_dir, "def c(pattern):\n    return pattern\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(f"{mod}.c", {"pattern": "x"}, toml_path=TOML)
    assert exc.value.check is Check.COMPILE_SIGNATURE


def test_varkwargs_third_party_compiler_raises_compile_signature_cv(module_dir):
    mod = _write_module(module_dir, "def c(*, pattern, **rest):\n    return pattern\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(f"{mod}.c", {"pattern": "x"}, toml_path=TOML)
    assert exc.value.check is Check.COMPILE_SIGNATURE


def test_varargs_third_party_compiler_raises_compile_signature_cv(module_dir):
    """The third signature sub-arm — a `*args` collector — rejects too (its own `actual` branch;
    sibling of the positional + `**kwargs` cases above)."""
    mod = _write_module(module_dir, "def c(*args, pattern):\n    return pattern\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(f"{mod}.c", {"pattern": "x"}, toml_path=TOML)
    assert exc.value.check is Check.COMPILE_SIGNATURE
    assert "*args" in exc.value.actual


# ===========================================================================
# Dotted-path resolution seals (reused from the handler sibling, citing the
# compile directive's R-pipeline-001 for the import-class failures)
# ===========================================================================


def test_unresolvable_dotted_module_raises_module_import_cv():
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile("no_such_pkg_xyzzy.compile_it", {}, toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert exc.value.rule_id == "R-pipeline-001"


def test_dotted_missing_attribute_raises_module_import_cv(module_dir):
    mod = _write_module(module_dir, "def other(*, x):\n    return x\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(f"{mod}.nope", {}, toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT


# verifies: compile-third-party-purity
def test_third_party_compiler_purity_audit_runs_unchanged(module_dir):
    """The R-handler-pure-module source-AST audit runs on a third-party compiler module
    BEFORE import — module-level mutable state rejects (the seal RED-on-removal: drop the
    pre-import audit and an impure module would import unaudited)."""
    mod = _write_module(
        module_dir,
        "CACHE = {}\n\ndef c(*, pattern):\n    return pattern\n",
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(f"{mod}.c", {"pattern": "x"}, toml_path=TOML)
    assert exc.value.check is Check.HANDLER_PURE_MODULE
    assert exc.value.rule_id == "R-handler-pure-module"


def test_class_shaped_third_party_compiler_rejected(module_dir):
    """The vector-2 function-shape seal applies unchanged — a compiler MUST be a bare function."""
    mod = _write_module(
        module_dir,
        """
        class Compiler:
            def __call__(self, *, pattern):
                return pattern
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(f"{mod}.Compiler", {"pattern": "x"}, toml_path=TOML)
    assert exc.value.check is Check.HANDLER_FUNCTION_SHAPE
    assert exc.value.rule_id == "R-handler-bare-function"


# verifies: compile-engine-binds-params-no-closure
def test_partial_pre_bound_compiler_rejected(module_dir):
    """The actual no-closure / no-factory adversary: an author-side ``functools.partial`` (a
    pre-bound callable smuggling state past the declared-parameter / hash surface) is rejected by
    the vector-2 shape seal — the engine binds DATA params and supplies the callable itself; there
    is no author factory/closure seam. RED-on-removal: drop ``_check_function_shape`` and a
    pre-bound partial would resolve, threading author state the compose-time contract forbids
    (mirrors the validator sibling ``test_partial_result_shape_rejected``)."""
    mod = _write_module(
        module_dir,
        """
        import functools

        def _base(*, pattern, fixed):
            return (pattern, fixed)

        compile_it = functools.partial(_base, fixed="smuggled-state")
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(f"{mod}.compile_it", {"pattern": "x"}, toml_path=TOML)
    assert exc.value.check is Check.HANDLER_FUNCTION_SHAPE
    assert exc.value.rule_id == "R-handler-bare-function"


# ===========================================================================
# The disjoint no-shadowing guarantee — bare and dotted spaces never cross
# ===========================================================================


# verifies: compile-disjoint-no-shadowing
def test_dotted_name_cannot_shadow_a_blessed_compiler(module_dir):
    """A third-party module exporting a function named `regex` resolves to the MODULE'S
    function (the dotted space), never the blessed `regex` table entry — and the blessed bare
    `regex` is unaffected. RED-on-removal: route a dotted name through BUILTIN_COMPILERS (or a
    bare name through the module path) and these two assertions cross."""
    mod = _write_module(
        module_dir,
        "def regex(*, pattern):\n    return ('third-party', pattern)\n",
    )
    shadow = resolve_and_compile(f"{mod}.regex", {"pattern": "x"}, toml_path=TOML)
    assert shadow == ("third-party", "x")  # the module's, not the blessed compiler's
    blessed = resolve_and_compile("regex", {"pattern": "x"}, toml_path=TOML)
    assert isinstance(blessed, re.Pattern)  # the blessed bare name is untouched


def test_blessed_table_holds_exactly_the_three_blessed_compilers():
    assert set(BUILTIN_COMPILERS) == {"regex", "jinja", "json_schema"}


# ===========================================================================
# Determinism / engine-binds-no-closure
# ===========================================================================


def test_same_compiler_and_params_is_deterministic():
    """A determinism sanity check (the no-closure half of the seal is structural — CompileBinding
    carries a compiler NAME + a data params Mapping, verified RED-on-removal by
    ``test_partial_pre_bound_compiler_rejected`` above — so this is illustrative, not a RED-test):
    same (compiler, params) yields an equivalent artifact each compose, and a dict param reaches
    the compiler as plain data (not invoked)."""
    a = resolve_and_compile("regex", {"pattern": "a.c", "flags": "DOTALL"}, toml_path=TOML)
    b = resolve_and_compile("regex", {"pattern": "a.c", "flags": "DOTALL"}, toml_path=TOML)
    assert a.pattern == b.pattern and a.flags == b.flags
    import jsonschema  # the [compilers] extra is required (dev profile) — fail loud if absent

    assert jsonschema  # used via resolve_and_compile below
    v = resolve_and_compile(
        "json_schema", {"schema": {"type": "string", "minLength": 2}}, toml_path=TOML
    )
    assert v.is_valid("ok") and not v.is_valid("x")
