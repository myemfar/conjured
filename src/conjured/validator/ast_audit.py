"""The min-viable source-AST audit — resolution step 3, run on module source BEFORE import.

One walker, two scopes (the same mechanism must not be solved twice):

- **Handler-module scope** (R-handler-pure-module, ``handler/reference.md``
  ``R-handler-pure-module/forbidden-patterns``): namespace-scope mutable state, persistent
  caching decorators (``@lru_cache`` / ``@cache`` / ``@cached_property``), and I/O at
  import time (the vector-5 import-time-I/O scan) — at module level AND inside class
  bodies (a class body executes at import, and a class-level mutable literal is
  cross-dispatch state the module-dict snapshot-and-restore cannot recover: restoring
  the module dict restores the class *reference*, not the class's own ``__dict__``).
- **Adapter-module scope** (vector 7; ``R-handler-pure-module/adapter-scope``): the same
  module-level + class-body walk (class-level mutable state is the vector's namesake).
  Instance state (``self.x`` in ``__init__`` or assigned on
  ``self`` elsewhere) is admissible — adapter instances are engine-managed compose-time
  state bounded by composition lifetime, so method *bodies* are not walked.

Function **default-argument expressions** evaluate AT IMPORT (they are part of the
``def`` / ``lambda`` statement's own execution and persist on the function object —
surviving the vector-3 snapshot-restore, which restores the module dict, not a
function's mutated ``__defaults__``), so both scopes audit them: I/O in a default and a
mutable-literal default are violations; function *bodies* stay call-time-pruned.

The audit runs on **source text, before ``import_module``** — a post-import audit cannot
prevent import-time I/O (``architecture/handler-resolution.md`` step 3). A violation
raises ``ContractViolation(rule_id="R-handler-pure-module")`` with the module file as
the offending artifact. Fail-fast: the first violation raises the bare ContractViolation
(within-group aggregation — ``ContractViolationGroup`` — belongs to the stage-2
composition-validation groups, per error-channel/reference.md § ContractViolationGroup).

**Min-viable scope, by ratified decision**: the three decided pattern classes plus the
I/O scan, recognized structurally. The coverage-tightening tail — singleton mutation,
aliased I/O, mutable class attrs via metaclasses, non-cache decorator side effects — is a
**reactive catcher**: it hardens as real modules slip patterns through, not a pre-emptive
sweep. Pure library imports (``import re``, ``import numpy``) are admissible — the seal
targets I/O and instantiation at import, not imports.
"""

from __future__ import annotations

import ast

from conjured.errors import Check, ContractViolation

#: Mutable-literal AST value nodes — the module/class-level assignment forms the
#: min-viable walk flags (a mutable binding at namespace scope persists across
#: dispatches through that namespace).
_MUTABLE_LITERAL_NODES = (
    ast.List,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.DictComp,
    ast.SetComp,
)

#: Persistent caching decorators forbidden at namespace scope. Matched by **final name**
#: under any dotted qualification (bare ``@lru_cache``, ``@functools.lru_cache``,
#: ``@ft.lru_cache``, ``@x.y.cached_property`` all match), called or not.
_CACHE_DECORATOR_NAMES = frozenset({"lru_cache", "cache", "cached_property"})

#: Dotted roots whose top-level calls are recognizable import-time I/O / client
#: instantiation (the vector-5 scan's structural pattern set; the long tail is the
#: reactive coverage-tightening tail — min-viable by ratified decision).
_IO_CALL_ROOTS = frozenset(
    {
        "os",
        "io",
        "shutil",
        "pathlib",
        "socket",
        "urllib",
        "requests",
        "httpx",
        "subprocess",
        "sqlite3",
        "http",
        "ftplib",
        "smtplib",
    }
)

#: Bare callables that are I/O regardless of qualification.
_IO_CALL_NAMES = frozenset({"open"})

#: Attribute method names whose call at module level is I/O on any receiver
#: (``Path(...).read_text()``, ``conn.connect()``, …).
_IO_ATTR_NAMES = frozenset(
    {"open", "connect", "urlopen", "read_text", "read_bytes", "write_text", "write_bytes"}
)

#: Known-pure constructions carved OUT of the blanket root match: the vector-5 scan
#: targets I/O and client instantiation at import (trust-model § vector 5), and these
#: perform neither — constructing a path object / doing string path-algebra touches no
#: file, socket, or client. The carve-out is an exact-surface allowlist under two roots
#: only; every other call under a matched root still flags (the conservative default —
#: e.g. ``pathlib.Path.cwd()`` / ``os.getcwd()`` / ``os.path.exists()`` stay flagged),
#: and the ``_IO_ATTR_NAMES`` final-attr match runs FIRST, so
#: ``pathlib.Path('f').read_text()`` keeps its RED case.
_PATHLIB_PURE_CONSTRUCTORS = frozenset(
    {"Path", "PurePath", "PurePosixPath", "PureWindowsPath", "PosixPath", "WindowsPath"}
)
_OS_PATH_PURE_ATTRS = frozenset(
    {
        "join", "dirname", "basename", "split", "splitext", "splitdrive",
        "normpath", "normcase", "isabs", "commonprefix", "commonpath",
    }
)


def _is_pure_construction(func: ast.Attribute) -> bool:
    """True for the named pure surfaces: ``pathlib.<PurePath-family constructor>(...)``
    and ``os.path.<string-algebra fn>(...)`` — exact chains only (a deeper or different
    chain falls back to the conservative root match)."""
    value = func.value
    if (
        isinstance(value, ast.Name)
        and value.id == "pathlib"
        and func.attr in _PATHLIB_PURE_CONSTRUCTORS
    ):
        return True
    if (
        isinstance(value, ast.Attribute)
        and value.attr == "path"
        and isinstance(value.value, ast.Name)
        and value.value.id == "os"
        and func.attr in _OS_PATH_PURE_ATTRS
    ):
        return True
    return False


def _dotted_root(node: ast.expr) -> str | None:
    """The leftmost name of an attribute/name chain (``urllib.request.urlopen`` →
    ``urllib``), or ``None`` when the chain bottoms out in a non-name (a call, a
    subscript)."""
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


def _decorator_name(node: ast.expr) -> str | None:
    """The decorator's final name — unwraps a call (``@lru_cache(maxsize=8)``) and returns
    the final attribute under any dotted qualification (``@functools.lru_cache``,
    ``@ft.lru_cache``, ``@x.y.cache`` all yield their final name)."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_io_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in _IO_CALL_NAMES
    if isinstance(func, ast.Attribute):
        if func.attr in _IO_ATTR_NAMES:
            return True
        if _is_pure_construction(func):
            return False  # a pure construction is not import-time I/O — never flagged
        root = _dotted_root(func)
        return root in _IO_CALL_ROOTS
    return False


def _iter_defaults(args: ast.arguments):
    """A function's default-argument expressions — positional (incl. positional-only)
    plus keyword-only (whose slots are ``None`` when a kw-only arg has no default)."""
    yield from args.defaults
    yield from (d for d in args.kw_defaults if d is not None)


def _walk_import_time(node: ast.AST):
    """Yield the AST nodes that execute at import time under ``node``, pruning
    function / class / lambda *bodies* (they run at call time, not import — flagging a
    call inside ``def f(): open(...)`` would be a false positive) while still walking a
    pruned function's / lambda's default-argument expressions, which DO evaluate at
    import as part of the definition statement itself."""
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if isinstance(current, ast.Lambda):
                # The lambda EXPRESSION executes at import wherever this walk reaches it
                # (that is the walk's invariant), constructing the function object and
                # evaluating its defaults — so the lambda node itself is yielded for the
                # caller's mutable-literal default check, then pruned into its defaults
                # exactly as a def's are (the body stays call-time-pruned).
                yield current
            stack.extend(_iter_defaults(current.args))
            continue
        if isinstance(current, ast.ClassDef):
            continue  # class bodies get their own namespace pass (_iter_class_defs)
        yield current
        stack.extend(ast.iter_child_nodes(current))


def _iter_namespace_statements(body: list[ast.stmt]) -> "list[ast.stmt]":
    """Statements executed in a namespace at import time — recurses into compound
    statements (``if`` / ``try`` / ``with`` / ``for`` / ``while``) but never into
    function or class bodies (function bodies run at call time; class bodies get their
    own namespace pass via ``_audit_class_bodies``, in both scopes)."""
    out: list[ast.stmt] = []
    for stmt in body:
        out.append(stmt)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child_body in (
            getattr(stmt, "body", None),
            getattr(stmt, "orelse", None),
            getattr(stmt, "finalbody", None),
        ):
            if child_body:
                out.extend(_iter_namespace_statements(child_body))
        for handler in getattr(stmt, "handlers", ()) or ():
            out.extend(_iter_namespace_statements(handler.body))
        for case in getattr(stmt, "cases", ()) or ():  # ast.Match arms
            out.extend(_iter_namespace_statements(case.body))
    return out


def _iter_class_defs(statements: list[ast.stmt]):
    """Every class definition reachable at import time — module-namespace classes AND
    classes nested inside them (a nested class body also executes at import; skipping
    it would blind the vector-7 seal to ``class Adapter: class _Cache: store = {}``).
    Classes inside function bodies execute at call time and are not yielded."""
    for stmt in statements:
        if isinstance(stmt, ast.ClassDef):
            yield stmt
            yield from _iter_class_defs(_iter_namespace_statements(stmt.body))


def _check_lambda_defaults(
    node: ast.AST, *, check: Check, origin: str, where: str
) -> None:
    """The lambda arm of the mutable-literal-default seal: an ``ast.Lambda`` yielded by
    the import-time walk constructs its function object AT IMPORT, evaluating its
    default-argument expressions — a mutable-literal default there is the same
    cross-dispatch state a ``def``'s is (the object persists, its mutated ``__defaults__``
    survive the vector-3 snapshot-restore). Raises on the first violating default."""
    if not isinstance(node, ast.Lambda):
        return
    for default in _iter_defaults(node.args):
        if isinstance(default, _MUTABLE_LITERAL_NODES):
            raise _violation(
                check=check,
                expected=f"no {where} mutable state (R-handler-pure-module)",
                actual=(
                    f"a mutable-literal default argument on a lambda at line "
                    f"{default.lineno}"
                ),
                origin=origin,
                line=default.lineno,
                hint=(
                    "a default evaluates once at import and persists on the function "
                    "object across dispatches; default to None and derive within the "
                    "body, or hold the value as a compose-time binding"
                ),
            )


def _violation(
    *, check: Check, expected: str, actual: str, origin: str, line: int, hint: str
) -> ContractViolation:
    return ContractViolation(
        check=check,
        rule_id="R-handler-pure-module",
        expected=expected,
        actual=actual,
        remediation_hint=hint,
        file_path=origin,
        line_number=line,
    )


def _binds_mutable_literal(node: ast.expr) -> bool:
    """Does assigning ``node`` at namespace scope bind a mutable literal? True for a direct
    list/dict/set/comprehension node, AND for a tuple-unpack whose elements (recursively) bind
    one — so ``CACHE, STORE = {}, []`` (RHS ``ast.Tuple``, not itself a mutable-literal node)
    is caught, not only the single-target ``CACHE = {}``. An ``ast.List`` RHS is itself a
    mutable-literal node, so the direct check already covers ``[A, B] = [{}, []]``."""
    if isinstance(node, _MUTABLE_LITERAL_NODES):
        return True
    if isinstance(node, ast.Tuple):
        return any(_binds_mutable_literal(elt) for elt in node.elts)
    return False


def _audit_namespace(
    statements: list[ast.stmt],
    *,
    check: Check,
    origin: str,
    where: str,
) -> None:
    """The shared namespace walk — flags mutable-literal assignment, cache decorators
    on functions, and I/O calls among ``statements`` (already namespace-flattened)."""
    for stmt in statements:
        # (1) mutable-literal assignment at namespace scope
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            value = stmt.value
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
            value = stmt.value
        if value is not None and _binds_mutable_literal(value):
            raise _violation(
                check=check,
                expected=f"no {where} mutable state (R-handler-pure-module)",
                actual=f"a {where} assignment binds a mutable literal at line {stmt.lineno}",
                origin=origin,
                line=stmt.lineno,
                hint=(
                    "move per-dispatch state into the handler body, compose-time values "
                    "into a bindings.<name> declaration"
                ),
            )
        # (2) persistent caching decorators on namespace-scope functions — plus the
        # function's default-argument expressions, which evaluate AT IMPORT (part of the
        # def statement's own execution) and persist on the function object across
        # dispatches: a mutable-literal default is cross-dispatch state that survives
        # the vector-3 module-dict snapshot-and-restore (the restored function object
        # keeps its mutated __defaults__), and I/O in a default runs at import.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in stmt.decorator_list:
                name = _decorator_name(dec)
                if name in _CACHE_DECORATOR_NAMES:
                    raise _violation(
                        check=check,
                        expected=f"no persistent caching decorator at {where} scope",
                        actual=f"@{name} on '{stmt.name}' at line {dec.lineno}",
                        origin=origin,
                        line=dec.lineno,
                        hint=(
                            "a cache at namespace scope persists across dispatches; "
                            "remove the decorator (derive within the body, or hold the "
                            "value as a compose-time binding)"
                        ),
                    )
            for default in _iter_defaults(stmt.args):
                if isinstance(default, _MUTABLE_LITERAL_NODES):
                    raise _violation(
                        check=check,
                        expected=f"no {where} mutable state (R-handler-pure-module)",
                        actual=f"a mutable-literal default argument on '{stmt.name}' "
                               f"at line {default.lineno}",
                        origin=origin,
                        line=default.lineno,
                        hint=(
                            "a default evaluates once at import and persists on the "
                            "function object across dispatches; default to None and "
                            "derive within the body, or hold the value as a "
                            "compose-time binding"
                        ),
                    )
                for node in _walk_import_time(default):
                    # A lambda constructed inside the def's default evaluates ITS
                    # defaults at the same import moment — same seal, nested form.
                    _check_lambda_defaults(node, check=check, origin=origin, where=where)
                    if isinstance(node, ast.Call) and _is_io_call(node):
                        raise _violation(
                            check=check,
                            expected=f"no {where} I/O at import time (the vector-5 scan)",
                            actual=f"an import-time I/O call in a default argument of "
                                   f"'{stmt.name}' at line {node.lineno}",
                            origin=origin,
                            line=node.lineno,
                            hint=(
                                "a default-argument expression evaluates at import; "
                                "I/O belongs behind the adapter boundary or in an "
                                "external binding-value declaration resolved at compose"
                            ),
                        )
            continue  # do not scan the function body's calls — they run at call time
        if isinstance(stmt, ast.ClassDef):
            continue  # class bodies execute at import too — audited by the class-body pass BOTH scopes run (_audit_class_bodies)
        # (3) import-time I/O — any recognizable I/O call reachable at import — plus the
        # lambda arm of the mutable-literal-default seal (a lambda expression at
        # import-executing scope constructs its function object, evaluating its defaults,
        # at import — `g = lambda x=[]: x` is the def form's exact sibling).
        for node in _walk_import_time(stmt):
            _check_lambda_defaults(node, check=check, origin=origin, where=where)
            if isinstance(node, ast.Call) and _is_io_call(node):
                raise _violation(
                    check=check,
                    expected=f"no {where} I/O at import time (the vector-5 scan)",
                    actual=f"an import-time I/O call at line {node.lineno}",
                    origin=origin,
                    line=node.lineno,
                    hint=(
                        "I/O belongs behind the adapter boundary "
                        "(services.<name>.invoke) or in an external binding-value "
                        "declaration resolved at compose"
                    ),
                )


def _audit_class_bodies(statements: list, *, check: Check, origin: str) -> None:
    """The class-body pass BOTH scopes run: a class body executes at import, so
    class-level mutable literals, cache decorators on methods, and class-body I/O are
    import-time violations in a handler module exactly as in an adapter module (a
    class-level mutable is above dispatch scope either way, and the vector-3
    snapshot-restore cannot recover it — the module dict holds the class *reference*,
    not the class's own ``__dict__``). Method bodies stay unwalked (call-time; for
    adapters, instance state is admissible by design)."""
    for class_def in _iter_class_defs(statements):
        class_statements = _iter_namespace_statements(class_def.body)
        _audit_namespace(
            class_statements, check=check, origin=origin, where="class-level"
        )


def audit_handler_module_source(source: str, *, origin: str) -> None:
    """The handler-module audit (R-handler-pure-module + the import-time-I/O scan) —
    module-level AND class-body scope (class bodies execute at import).
    Raises ``ContractViolation`` on the first violation; returns ``None`` on a clean
    module. Run BEFORE ``import_module`` — on source read from ``spec.origin``."""
    tree = ast.parse(source, filename=origin)
    statements = _iter_namespace_statements(tree.body)
    _audit_namespace(
        statements, check=Check.HANDLER_PURE_MODULE, origin=origin, where="module-level"
    )
    _audit_class_bodies(statements, check=Check.HANDLER_PURE_MODULE, origin=origin)


def audit_adapter_module_source(source: str, *, origin: str) -> None:
    """The adapter-module audit (vector 7 — the R-handler-pure-module adapter-scope
    extension): the same module-level + class-body walk under the adapter check class.
    Instance state is admissible (method bodies are not walked)."""
    tree = ast.parse(source, filename=origin)
    statements = _iter_namespace_statements(tree.body)
    _audit_namespace(
        statements, check=Check.ADAPTER_PURE_MODULE, origin=origin, where="module-level"
    )
    _audit_class_bodies(statements, check=Check.ADAPTER_PURE_MODULE, origin=origin)
