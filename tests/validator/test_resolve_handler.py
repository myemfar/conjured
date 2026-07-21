"""Handler resolution steps 3–7 (``validator.resolve_handler``) — real ``tmp_path``
handler modules on ``sys.path`` (the step-3 source-AST audit genuinely reads the file
``find_spec`` reports; mocking it would not exercise the read-before-import
invariant), and a real ``.dist-info`` fixture for the entry-points path (the stdlib
genuinely discovers it — no engine seam is patched)."""

from __future__ import annotations

import pathlib
import sys
import textwrap
import uuid

import pytest

from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.common import Binding, SchemaBinding, ServiceBindingDecl
from conjured.ir.handler import (
    HookDeclaration,
    ServiceDeclaration,
    TransformDeclaration,
)
from conjured.validator.resolve_handler import HandlerEntry, resolve_handler

TOML = "handlers/fixture.toml"


def _transform_decl(reads=("x",), bindings=()):
    return TransformDeclaration(
        reads=tuple(FieldDecl(name=n, type=primitive("str")) for n in reads),
        output_schema=(FieldDecl(name="out", type=primitive("str")),),
        bindings=tuple(
            Binding(
                name=n,
                body=SchemaBinding(fields=(FieldDecl(name="k", type=primitive("str")),)),
            )
            for n in bindings
        ),
    )


def _service_decl():
    return ServiceDeclaration(
        reads=(FieldDecl(name="q", type=primitive("str")),),
        output_schema=(FieldDecl(name="out", type=primitive("str")),),
        service_bindings=(
            ServiceBindingDecl(name="llm", type="conjured_llm.structured_output"),
        ),
    )


@pytest.fixture()
def module_dir(tmp_path, monkeypatch):
    """A real on-disk module home, prepended to sys.path; modules written here resolve
    through the genuine import machinery (find_spec -> source read -> import)."""
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, source: str) -> str:
    """Write a uniquely named module file; returns the module name (unique per test so
    sys.modules never carries state across tests)."""
    name = f"hmod_{uuid.uuid4().hex[:10]}"
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    import importlib

    importlib.invalidate_caches()
    return name


# --- the happy dotted path -------------------------------------------------------------


def test_dotted_resolution_happy(module_dir):
    mod = _write_module(module_dir, "def fn(*, x):\n    return {'out': x}\n")
    entry = resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert isinstance(entry, HandlerEntry)
    assert entry.qualified_name == f"{mod}.fn"
    assert entry.kind == "transform"
    assert entry.package == mod  # no distribution maps the tmp module; top-level name
    assert entry.toml_path == pathlib.Path(TOML)
    assert entry.callable(x="hi") == {"out": "hi"}


def test_lambda_and_wraps_admitted(module_dir):
    mod = _write_module(
        module_dir,
        """
        import functools

        fn = lambda *, x: {'out': x}

        def _inner(*, x):
            return {'out': x}

        @functools.wraps(_inner)
        def wrapped(*, x):
            return _inner(x=x)
        """,
    )
    assert resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert resolve_handler(f"{mod}.wrapped", _transform_decl(), toml_path=TOML)


def test_io_inside_function_body_is_admissible(module_dir):
    mod = _write_module(
        module_dir,
        """
        def fn(*, x):
            with open(x) as fh:  # call-time I/O is the body's own (reviewed) business
                return {'out': fh.read()}
        """,
    )
    assert resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)


def test_signature_union_includes_bindings_and_services(module_dir):
    mod = _write_module(
        module_dir, "def fn(*, q, services):\n    return {'out': 'x'}\n"
    )
    entry = resolve_handler(f"{mod}.fn", _service_decl(), toml_path=TOML)
    assert entry.kind == "service"


def _stdlib_hook_decl():
    """A stdlib-emission hook: declared transport_schema fields join the R-handler-001
    signature union (delivered to the emitting body as kwargs, like bindings —
    handler/reference.md § transport_schema, the amended union)."""
    from conjured.ir.handler import HookDeclaration

    return HookDeclaration(
        reads=(FieldDecl(name="dialogue", type=primitive("str")),),
        transport_schema=(
            FieldDecl(name="log_path", type=primitive("str")),
            FieldDecl(name="format", type=primitive("str")),
        ),
    )


def test_hook_signature_union_includes_transport_schema_fields(module_dir):
    """The amended R-handler-001 union: a stdlib hook's body declares its
    transport_schema field names as kwargs — exact-match, like every union member."""
    mod = _write_module(
        module_dir,
        "def watch(*, dialogue, log_path, format):\n    return None\n",
    )
    entry = resolve_handler(f"{mod}.watch", _stdlib_hook_decl(), toml_path=TOML)
    assert entry.kind == "hook"


def test_hook_missing_transport_kwarg_is_a_signature_mismatch(module_dir):
    """A hook body omitting a declared transport_schema field name fails the union
    check at compose — the exact CV, before the first pipeline runs."""
    mod = _write_module(
        module_dir, "def watch(*, dialogue, log_path):\n    return None\n"  # no `format`
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.watch", _stdlib_hook_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_SIGNATURE
    assert exc.value.rule_id == "R-handler-001"
    assert "format" in exc.value.remediation_hint


# --- the entry-points path (a real .dist-info on sys.path) -----------------------------


def _write_dist_info(module_dir, dist_name: str, ep_line: str) -> None:
    info = module_dir / f"{dist_name}-0.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist_name}\nVersion: 0.1\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        f"[conjured.handlers]\n{ep_line}\n", encoding="utf-8"
    )


def test_entry_point_resolution_happy(module_dir):
    mod = _write_module(module_dir, "def fn(*, x):\n    return {'out': x}\n")
    short = f"short_{uuid.uuid4().hex[:8]}"
    _write_dist_info(module_dir, "epdista", f"{short} = {mod}:fn")
    entry = resolve_handler(short, _transform_decl(), toml_path=TOML)
    assert entry.qualified_name == f"{mod}.fn"  # the resolved dotted form, not the alias
    assert entry.package == "epdista"


def test_entry_point_collision_fails_loud(module_dir):
    mod = _write_module(module_dir, "def fn(*, x):\n    return {'out': x}\n")
    short = f"short_{uuid.uuid4().hex[:8]}"
    _write_dist_info(module_dir, "epdistb", f"{short} = {mod}:fn")
    _write_dist_info(module_dir, "epdistc", f"{short} = {mod}:fn")
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(short, _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.ENTRY_POINT_COLLISION
    assert "epdistb" in exc.value.actual and "epdistc" in exc.value.actual


def test_unregistered_short_name(module_dir):
    with pytest.raises(ContractViolation) as exc:
        resolve_handler("nosuchshortname", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT


def test_dotted_wins_no_entry_point_lookup(module_dir):
    # A dotted name never consults entry-points: dot-presence is the whole selector.
    mod = _write_module(module_dir, "def fn(*, x):\n    return {'out': x}\n")
    _write_dist_info(module_dir, "epdistd", f"{mod} = {mod}:fn")  # alias matching the module name
    entry = resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert entry.qualified_name == f"{mod}.fn"


# --- resolution failures (each the exact structured class) -----------------------------


def test_module_not_found(module_dir):
    with pytest.raises(ContractViolation) as exc:
        resolve_handler("no_such_module_xyz.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT


def test_function_not_in_module(module_dir):
    mod = _write_module(module_dir, "def fn(*, x):\n    return {'out': x}\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.nope", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert "does not export" in (exc.value.remediation_hint or "")


def test_namespace_package_rejected(module_dir):
    ns = f"nspkg_{uuid.uuid4().hex[:8]}"
    (module_dir / ns).mkdir()  # a directory with no __init__.py — PEP 420
    (module_dir / ns / "inner.py").write_text("def fn(*, x):\n    return {}\n")
    import importlib

    importlib.invalidate_caches()
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{ns}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_NAMESPACE_PACKAGE
    hint = exc.value.remediation_hint or ""
    assert "__init__.py" in hint
    assert ns in hint  # the decided hint names the path needing the __init__.py


def test_syntax_error_module_is_structured_cv(module_dir):
    # Every resolution failure is a compose-time ContractViolation — a module with a
    # syntax error must not escape as a raw SyntaxError from the step-3 parse.
    mod = _write_module(module_dir, "def fn(*, x:\n    return {\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert mod not in sys.modules


def test_pep263_encoded_module_is_auditable(module_dir):
    # A legal non-UTF-8 module (PEP-263 coding declaration) decodes the way import
    # would and resolves normally — never a raw UnicodeDecodeError.
    name = f"hmod_{uuid.uuid4().hex[:10]}"
    source = "# -*- coding: latin-1 -*-\n# caf\xe9\ndef fn(*, x):\n    return {'out': x}\n"
    (module_dir / f"{name}.py").write_bytes(source.encode("latin-1"))
    import importlib

    importlib.invalidate_caches()
    entry = resolve_handler(f"{name}.fn", _transform_decl(), toml_path=TOML)
    assert entry.callable(x="ok") == {"out": "ok"}


def test_match_arm_mutable_state_rejected(module_dir):
    # The walker recurses into match arms — module-level mutable state cannot hide
    # behind a `case` any more than behind an `if`.
    mod = _write_module(
        module_dir,
        """
        import sys

        match sys.platform:
            case _:
                CACHE = {}

        def fn(*, x):
            return {'out': x}
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_PURE_MODULE


# --- the step-3 audit runs BEFORE import ------------------------------------------------


def test_purity_violation_raises_before_import(module_dir):
    sentinel = module_dir / "sentinel_must_never_exist.txt"
    mod = _write_module(
        module_dir,
        f"""
        CACHE = {{}}
        open(r'{sentinel}', 'w').write('imported')
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_PURE_MODULE
    assert exc.value.rule_id == "R-handler-pure-module"
    # The audit fired on source, pre-import: the module never executed.
    assert mod not in sys.modules
    assert not sentinel.exists()


def test_module_level_cache_decorator_rejected(module_dir):
    mod = _write_module(
        module_dir,
        """
        import functools

        @functools.lru_cache(maxsize=8)
        def fn(*, x):
            return {'out': x}
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_PURE_MODULE


def test_module_level_io_rejected(module_dir):
    mod = _write_module(
        module_dir,
        """
        import urllib.request

        DATA = urllib.request.urlopen('http://example.invalid')

        def fn(*, x):
            return {'out': x}
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_PURE_MODULE
    assert mod not in sys.modules


# verifies: resolve-non-file-origin-fails-loud
def test_non_file_origin_module_fails_loud():
    """trust-model.md Vector 5 + handler-resolution.md § Resolution sequence step 3: the
    step-3 source-AST audit reads module source from ``spec.origin`` BEFORE import (a
    post-import audit cannot prevent import-time I/O), so a sourceless module — a builtin /
    extension with no readable source file — MUST fail loud rather than silently skip the
    purity + import-I/O seal (a skipped scan is a broken seal — an I4 break).

    The adversary is a built-in module: ``find_spec('sys').origin == 'built-in'`` and
    ``os.path.isfile('built-in')`` is False, so ``read_and_audit_source`` reaches its
    ``not os.path.isfile(origin)`` raise. No other resolution test exercises this branch —
    the namespace-package test stops at step 2 (``origin is None``), the purity tests use
    real ``.py`` modules. RED if the non-file-origin branch is deleted: the function would
    fall through to ``open('built-in', 'rb')`` and escape as a raw ``OSError`` — a fourth
    class out of the closed compose-time channel, not the structured ContractViolation."""
    with pytest.raises(ContractViolation) as exc:
        resolve_handler("sys.exit", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_PURE_MODULE
    assert exc.value.rule_id == "R-handler-pure-module"
    assert "built-in" in exc.value.actual  # the origin-based rejection, not a syntax/decode one


# --- the vector-2 shape seal (step 5) ---------------------------------------------------


@pytest.mark.parametrize(
    "attr", ["KLASS", "INSTANCE", "PARTIAL", "BUILTIN", "BOUND"]
)
def test_non_function_shapes_rejected(module_dir, attr):
    mod = _write_module(
        module_dir,
        """
        import functools

        def _fn(*, x):
            return {'out': x}

        class KLASS:
            def method(self, *, x):
                return {'out': x}
            def __call__(self, *, x):
                return {'out': x}

        INSTANCE = KLASS()
        PARTIAL = functools.partial(_fn)
        BUILTIN = len
        BOUND = INSTANCE.method
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.{attr}", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_FUNCTION_SHAPE
    assert exc.value.rule_id == "R-handler-bare-function"


# --- the signature seal (step 6, read from the real __code__) ---------------------------


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("def fn(*, x, extra):\n    return {}\n", "extra"),
        ("def fn(*, wrong):\n    return {}\n", "missing"),
        ("def fn(x):\n    return {}\n", "positional"),
        ("def fn(*args, x):\n    return {}\n", "*args"),
        ("def fn(*, x, **kw):\n    return {}\n", "**kwargs"),
    ],
)
def test_signature_mismatches_rejected(module_dir, source, fragment):
    mod = _write_module(module_dir, source)
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_SIGNATURE
    assert exc.value.rule_id == "R-handler-001"


def test_faked_signature_cannot_hide_collectors(module_dir):
    # A genuine def whose __signature__ lies (hides its **kwargs): the compose check
    # reads the real __code__, so the lie does not survive resolution.
    mod = _write_module(
        module_dir,
        """
        import inspect

        def fn(*, x, **smuggled):
            return {'out': x}

        fn.__signature__ = inspect.Signature(
            [inspect.Parameter('x', inspect.Parameter.KEYWORD_ONLY)]
        )
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_SIGNATURE
    assert "**kwargs" in exc.value.expected


def test_transform_signature_must_not_carry_services(module_dir):
    # R-handler-004's mechanical companion: a transform declaration can never put
    # 'services' in the union, so a transform fn declaring it fails the union check.
    mod = _write_module(module_dir, "def fn(*, x, services):\n    return {}\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_SIGNATURE


def test_hook_with_binding_requires_services_kwarg(module_dir):
    decl = HookDeclaration(
        reads=(FieldDecl(name="evt", type=primitive("str")),),
        service_bindings=(
            ServiceBindingDecl(name="webhook", type="acme.webhook"),
        ),
    )
    mod = _write_module(
        module_dir, "def fn(*, evt, services):\n    return None\n"
    )
    entry = resolve_handler(f"{mod}.fn", decl, toml_path=TOML)
    assert entry.kind == "hook"
    bad = _write_module(module_dir, "def fn(*, evt):\n    return None\n")
    with pytest.raises(ContractViolation):
        resolve_handler(f"{bad}.fn", decl, toml_path=TOML)


# ---------------------------------------------------------------------------
# The closed step-4 import channel + the fresh-resolution eviction
# ---------------------------------------------------------------------------


# verifies: resolve-import-fails-structured
def test_step4_missing_dependency_import_is_structured(module_dir):
    """A handler module whose top-level `import missing_dep` fails — a statement the
    step-3 source-AST audit legally ADMITS (the seal targets I/O and instantiation,
    never imports) — must surface as a compose-time ContractViolation, not a raw
    ModuleNotFoundError escaping the closed channel."""
    mod = _write_module(
        module_dir,
        "import missing_dep_zq18\n\ndef fn(*, x):\n    return {'out': x}\n",
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert "missing_dep_zq18" in str(exc.value)


# verifies: resolve-import-fails-structured
def test_step4_toplevel_raise_is_structured(module_dir):
    """ANY exception the module's top-level code raises at import (here a RuntimeError
    the audit cannot see — it is not I/O) stays inside the closed channel."""
    mod = _write_module(
        module_dir,
        "raise RuntimeError('boom at import')\n",
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert "boom at import" in str(exc.value)


# verifies: resolve-source-read-fails-structured
def test_step3_source_read_failure_is_structured(module_dir, monkeypatch):
    """A vanished-after-isfile / permission-denied origin: the step-3 source read's
    OSError must translate to the structured compose-time ContractViolation, never
    escape raw. The OS boundary is faked (builtins.open denying exactly this origin —
    the double fails precisely the way the runtime would); no engine seam is patched."""
    import builtins
    import os as _os

    mod = _write_module(module_dir, "def fn(*, x):\n    return {'out': x}\n")
    origin = _os.path.normcase(str(module_dir / f"{mod}.py"))
    real_open = builtins.open

    def deny(file, *args, **kwargs):
        if isinstance(file, (str, _os.PathLike)) and _os.path.normcase(
            _os.path.abspath(_os.fspath(file))
        ) == _os.path.normcase(_os.path.abspath(origin)):
            raise PermissionError(13, "Permission denied", _os.fspath(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", deny)
    with pytest.raises(ContractViolation) as exc:
        resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert "Permission denied" in str(exc.value)


# verifies: resolve-fresh-eviction
def test_recompose_after_rewrite_runs_the_new_module_code(module_dir):
    """The fresh-resolution contract (handler-resolution.md § Hot-reload semantics: 'a
    fresh compose sees freshly resolved handlers') + audit/execution coherence (the
    source step 3 audits IS the source that executes): write, resolve, rewrite the
    SAME file, re-resolve -> the NEW behavior runs. RED with the sys.modules eviction
    removed (the cached first-import module would keep serving 'v1')."""
    import importlib

    mod = _write_module(module_dir, "def fn(*, x):\n    return {'out': 'v1'}\n")
    first = resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert first.callable(x="ignored") == {"out": "v1"}

    # The rewrite deliberately differs in LENGTH: the engine's eviction refreshes
    # sys.modules; the import system's own bytecode-cache freshness heuristic is
    # (source size + mtime), so a same-length rewrite within the same mtime second
    # would exercise CPython's pyc staleness, not the seal under test.
    (module_dir / f"{mod}.py").write_text(
        "def fn(*, x):\n    return {'out': 'v2-rewritten'}\n", encoding="utf-8"
    )
    importlib.invalidate_caches()
    second = resolve_handler(f"{mod}.fn", _transform_decl(), toml_path=TOML)
    assert second.callable(x="ignored") == {"out": "v2-rewritten"}


# verifies: resolve-fresh-eviction
def test_eviction_never_touches_engine_or_stdlib_modules():
    """The eviction's scope guard: engine (conjured.*) and stdlib modules are never
    evicted, even when name + origin would match — their source is not authoring
    surface, and evicting a live engine/stdlib module forks class identities."""
    import json as _json
    import sys as _sys

    from conjured.validator.resolve_handler import _evict_stale_module

    import conjured.errors as _errors

    _evict_stale_module("json", getattr(_json, "__file__", None))
    assert _sys.modules.get("json") is _json
    _evict_stale_module("conjured.errors", _errors.__file__)
    assert _sys.modules.get("conjured.errors") is _errors


# verifies: resolve-fresh-eviction
def test_different_origin_module_is_rejected_loud(module_dir):
    """A cached sys.modules entry whose __file__ DIFFERS from the audited origin is a
    detected audited-vs-executed divergence — the module that would execute is not the
    file the audit just read (two files claiming one name: a shadowed package, a stale
    install beside local source). The compose REJECTS it with the structured
    ContractViolation rather than silently running unaudited code (user ruling
    2026-07-10; the verification-path-bypass class). RED if the raise reverts to the
    former silent leave-alone."""
    import sys as _sys

    from conjured.validator.resolve_handler import _evict_stale_module

    mod = _write_module(module_dir, "def fn(*, x):\n    return {'out': x}\n")
    import importlib

    module = importlib.import_module(mod)
    try:
        divergent_origin = str(module_dir / "elsewhere" / f"{mod}.py")
        with pytest.raises(ContractViolation) as exc:
            _evict_stale_module(mod, divergent_origin)
        assert exc.value.check is Check.MODULE_ORIGIN_DIVERGENCE
        assert exc.value.rule_id == "R-pipeline-001"
        assert mod in str(exc.value)  # names the ambiguous module
        assert _sys.modules.get(mod) is module  # nothing was evicted on the way out
    finally:
        del _sys.modules[mod]  # leave no cross-test residue


# verifies: resolve-fresh-eviction
@pytest.mark.parametrize(
    "rule_id, what",
    [("R-service-type-003", "adapter"), ("R-handler-012", "validator")],
)
def test_divergence_cites_the_callers_rule_and_noun(module_dir, rule_id, what):
    """A module-origin divergence carries the CALLER's rule_id + noun, not the handler
    defaults: an adapter cites R-service-type-003/'adapter', a validator R-handler-012/
    'validator' (the shared trio the dotted legs already thread — this is the mechanism the
    entry-point legs of resolve_adapter/resolve_validator now pass through, mirroring their
    sibling import_audited_module calls). RED if _evict_stale_module stops threading rule_id/
    what into the divergence ContractViolation."""
    import sys as _sys
    import importlib

    from conjured.validator.resolve_handler import _evict_stale_module

    mod = _write_module(module_dir, "def fn(*, x):\n    return {'out': x}\n")
    module = importlib.import_module(mod)
    try:
        divergent_origin = str(module_dir / "elsewhere" / f"{mod}.py")
        with pytest.raises(ContractViolation) as exc:
            _evict_stale_module(mod, divergent_origin, rule_id=rule_id, what=what)
        assert exc.value.check is Check.MODULE_ORIGIN_DIVERGENCE
        assert exc.value.rule_id == rule_id
        assert what in str(exc.value)  # the caller-specific diagnostic noun
    finally:
        del _sys.modules[mod]
