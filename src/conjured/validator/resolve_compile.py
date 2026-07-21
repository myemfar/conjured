"""Compile-affordance resolution — the ``compile = "<compiler>"`` directive's compose-time
binding (``conjured/docs/components/handler/reference.md`` § The ``compile = "..."`` directive
sub-form). A fourth sibling beside handler / adapter / validator resolution, sharing the same
dotted-path + source-AST-audit machinery (``resolve_handler``).

A :class:`~conjured.ir.common.CompileBinding` resolves at compose to its **artifact**: the engine
resolves the named compiler, introspects its signature against the directive's declared
parameters, binds those parameters (engine-owned — authors write no factory or closure), and runs
it **once** to produce the artifact. The artifact is delivered as the binding's engine-owned kwarg
value, forwarded as-is (vector-4-copy-exempt). Every failure is a compose-time
``ContractViolation`` raised at binding resolution, **never** at dispatch.

The bare-vs-namespaced split (the same selector field validators use):

- **Bare** names (no dot) are the **blessed first-party** compilers — the engine's reserved
  ``compile`` vocabulary, resolved mechanically from the static :data:`BUILTIN_COMPILERS` table
  (the compile analogue of ``BUILTIN_VALIDATORS``). No entry-points lookup, no source audit: they
  are engine-shipped (``conjured.lib.compilers``), trusted exactly as the built-in validation
  keywords are. The parser has already rejected a bare name no blessed compiler carries
  (``Check.CLOSED_GRAMMAR`` at parse), so an unblessed bare name cannot reach here.
- **Namespaced** (dotted) names are **third-party** compilers, resolved through the same
  ``dotted-path resolution`` + the R-handler-pure-module **source-AST audit** + the vector-2
  function-shape seal as any foreign handler — ``resolve_handler``'s shared steps, unchanged. The
  two name-spaces are disjoint by construction, so a dotted compiler can never shadow a blessed one.

**The signature/param check (both paths)** — the directive's declared parameters (the binding's
sibling keys) MUST be a subset of the compiler's keyword-only parameters, and the compiler's
**required** keyword-only parameters MUST all be declared; collectors and positionals reject. Read
from the real ``__code__`` (the un-fakeable surface handler step 6 uses) → ``Check.COMPILE_SIGNATURE``.
Then the compiler runs against the bound parameters: a raise (a malformed ``regex``, an unparseable
``jinja`` template, an invalid ``json_schema``, an unknown parameter value) is the compiler's own
failure → ``Check.COMPILE_ARTIFACT``. A missing optional backing library (``jinja2`` / ``jsonschema``)
surfaces as the compiler's own raw ``ImportError`` — an environment problem, propagated unchanged
(the same posture as ``import conjured.server``), never a ``ContractViolation``.
"""

from __future__ import annotations

import os
from types import MappingProxyType
from typing import Callable, Mapping

from conjured.errors import Check, ContractViolation
from conjured.ir.common import FilePathBindingValue
from conjured.lib import compilers as _compilers
from conjured.validator.resolve_handler import (
    check_function_shape,
    code_signature,
    resolve_dotted_attribute,
)

#: The blessed first-party compilers — the static ``bare name → callable`` table (the compile
#: analogue of ``BUILTIN_VALIDATORS``; the names are ``CompilePrimitive``). Resolved without
#: entry-points or source audit (engine-shipped, trusted). Immutable by construction (a
#: ``MappingProxyType`` — the codebase idiom for a module-level read-only table; structural, not a
#: convention).
BUILTIN_COMPILERS: Mapping[str, Callable[..., object]] = MappingProxyType(
    {
        "regex": _compilers.regex,
        "jinja": _compilers.jinja,
        "json_schema": _compilers.json_schema,
    }
)


def _resolve_dotted(
    compiler: str, *, toml_path: str, audit_enforcement: bool = False
) -> Callable[..., object]:
    """Resolve a namespaced (dotted) third-party compiler to its callable through the shared
    dotted-path leg: spec-locate (namespace-package rejection), the step-3 pre-import
    source-AST audit (R-handler-pure-module, unchanged), import, attribute read. Import-class
    failures cite R-pipeline-001 (the compose binding-resolution rule the compile directive
    grounds in). ``audit_enforcement`` threads the deployment's audit-stamp opt-in into the
    shared leg — a third-party compiler module receives the pre-import source read, so it is
    an in-scope module under the stamp's definitional clause (handler/reference.md § Audit
    stamps) exactly as a validator module is."""
    # guarantees: compile-third-party-purity
    # The R-handler-pure-module source-AST audit runs on a third-party compiler module
    # unchanged, BEFORE import (a post-import audit cannot prevent import-time I/O) —
    # inside the shared leg.
    return resolve_dotted_attribute(
        compiler, toml_path=toml_path, what="compiler", attr_hint="compiler",
        audit_enforcement=audit_enforcement,
    )


def _check_signature(
    fn: Callable[..., object], params: Mapping[str, object], *, compiler: str, toml_path: str
) -> None:
    """Introspect the compiler's signature against the directive's declared parameters
    (``params`` — the binding's sibling keys), read from the real ``__code__``: kwarg-only (no
    positional, no ``*args`` / ``**kwargs`` collector); every declared parameter is one of the
    compiler's keyword-only parameters; every **required** keyword-only parameter (no default) is
    declared. A mismatch is ``Check.COMPILE_SIGNATURE`` — compose-time, never deferred to the run.
    """
    sig = code_signature(fn.__code__)
    if sig.has_varargs or sig.has_varkwargs or sig.positional:
        raise ContractViolation(
            check=Check.COMPILE_SIGNATURE, rule_id="R-pipeline-001",
            expected="a kwarg-only compiler signature (no positional parameters, no *args / "
                     "**kwargs collector)",
            actual=f"compiler '{compiler}' declares "
                   + (
                       "a *args collector" if sig.has_varargs
                       else "a **kwargs collector" if sig.has_varkwargs
                       else f"positional parameter(s) {list(sig.positional)}"
                   )
                   + " (real __code__)",
            remediation_hint="a compiler is a deterministic params -> artifact bare kwarg-only "
                             "function: def compiler(*, ...)",
            file_path=toml_path,
        )
    declared = set(params)
    accepted = sig.kwonly
    required = accepted - set(getattr(fn, "__kwdefaults__", None) or {})
    unknown = sorted(declared - accepted)
    missing = sorted(required - declared)
    if unknown or missing:
        raise ContractViolation(
            check=Check.COMPILE_SIGNATURE, rule_id="R-pipeline-001",
            expected=f"the compile directive's declared parameters {sorted(declared)} bind "
                     f"compiler '{compiler}' (parameters {sorted(accepted)}; required "
                     f"{sorted(required)})",
            actual=(
                (f"declared parameter(s) {unknown} the compiler does not accept" if unknown else "")
                + ("; " if unknown and missing else "")
                + (f"required compiler parameter(s) {missing} not declared" if missing else "")
            ),
            remediation_hint=(
                (f"remove {unknown} from the directive; " if unknown else "")
                + (f"declare {missing} as sibling key(s) of compile" if missing else "")
            ).strip("; "),
            file_path=toml_path,
        )


def _materialize_params(
    params: Mapping[str, object], *, compiler: str, toml_path: str, binding_name: str | None
) -> dict[str, object]:
    """Resolve each declared parameter to the value the compiler receives. An inline param passes
    through unchanged; a **file-supplied** param (a ``FilePathBindingValue``, the SAME `{ file }`
    form a binding value uses) is materialized to its stamped **raw text** — the compiler parses it
    (``json_schema`` reads the text as JSON; ``jinja`` / ``regex`` use it directly), and never sees
    the path or the ``file`` key (handler/reference.md § The ``compile = "..."`` directive sub-form).

    The file was read + stamped by the binding-resolution pass
    (``validator.resolve.resolve_compile_param_files``) — a single read shared with the hasher. An
    **unresolved** file param (``content_hash is None``) means that pass did not run: fail loud
    (never read a file here at stage-4, never feed a path to a compiler), the same backstop the
    hasher carries. ``binding_name`` (the enclosing ``bindings.<name>``, supplied by the assemble
    caller) gives the diagnostic the ``bindings.<binding>.<param>`` locus the parse / hasher guards
    use; absent it (a direct compiler call), the param is named without a fabricated table prefix."""
    out: dict[str, object] = {}
    for name, value in params.items():
        if isinstance(value, FilePathBindingValue):
            if value.content_hash is None:
                section_path = f"bindings.{binding_name}.{name}" if binding_name else None
                raise ContractViolation(
                    check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
                    expected=f"the file-supplied compile parameter '{name}' is resolved "
                             "(validator.resolve.resolve_compile_param_files stamps its text)",
                    actual=f"unresolved external file '{value.path}' for compile parameter '{name}'",
                    remediation_hint="run the compile-parameter resolution pass before assemble; "
                                     "the compiler is fed the file's text, never its path",
                    section_path=section_path, file_path=toml_path,
                )
            out[name] = value.resolved  # the raw text the compiler parses
        else:
            out[name] = value
    return out


def resolve_and_compile(
    compiler: str, params: Mapping[str, object], *, toml_path: str | os.PathLike[str],
    binding_name: str | None = None,
    audit_enforcement: bool = False,
) -> object:
    """Resolve the named ``compiler``, bind ``params``, and run it once to produce the artifact —
    compose-time only; every failure a ``ContractViolation`` at binding resolution.

    Bare → a blessed first-party compiler (``BUILTIN_COMPILERS``, no audit / shape seal — engine
    trusted). Dotted → a third-party compiler through the shared dotted-path resolution + R-handler-
    pure-module audit + the vector-2 function-shape seal. Both paths then introspect the signature
    against ``params`` (``COMPILE_SIGNATURE``) and run the compiler (``COMPILE_ARTIFACT`` on a raise).
    """
    toml_str = str(toml_path)
    # guarantees: compile-disjoint-no-shadowing
    # The bare-vs-namespaced split is the ONLY router: a dotted name resolves a third-party
    # module, a bare name the blessed engine table. The spaces are disjoint by construction —
    # a dotted compiler can never reach BUILTIN_COMPILERS, so it cannot shadow a blessed one.
    fn: Callable[..., object] | None
    if "." in compiler:
        fn = _resolve_dotted(
            compiler, toml_path=toml_str, audit_enforcement=audit_enforcement
        )
        check_function_shape(  # the shared vector-2 seal, compiler hint
            fn, toml_path=toml_str,
            hint=f"the compiler '{compiler}' is not a bare function; a compiler "
                 "MUST be a bare kwarg-only function per R-handler-bare-function",
        )
    else:
        fn = BUILTIN_COMPILERS.get(compiler)
        if fn is None:  # pragma: no cover - parse rejects an unblessed bare name first
            raise ContractViolation(
                check=Check.CLOSED_GRAMMAR, rule_id="R-handler-006",
                expected=f"a blessed first-party compiler {sorted(BUILTIN_COMPILERS)}",
                actual=f"no blessed compiler named '{compiler}'",
                remediation_hint="use a blessed bare name, or namespace a third-party compiler",
                file_path=toml_str,
            )
    _check_signature(fn, params, compiler=compiler, toml_path=toml_str)
    # Materialize file-supplied params to their stamped raw text (inline params pass through);
    # the signature check above used the declared param NAMES, identical either way.
    bound = _materialize_params(params, compiler=compiler, toml_path=toml_str, binding_name=binding_name)
    # guarantees: compile-engine-binds-params-no-closure
    # The engine binds the directive's declared params (data only — CompileBinding carries a
    # compiler NAME + a params Mapping, never a callable) and runs the engine-resolved compiler
    # here. There is no author factory/closure seam: the author supplies data, the engine the
    # callable. Same (compiler, params) → an equivalent artifact each compose (determinism).
    # guarantees: compile-failure-at-compose-not-dispatch
    # Resolution + the run happen at the stage-4 binding-resolution pass (compose); a failure is
    # a ContractViolation HERE, never deferred to dispatch.
    try:
        return fn(**bound)
    except ImportError as exc:
        if compiler in BUILTIN_COMPILERS:
            # A BLESSED compiler's missing optional backing library (conjured[compilers])
            # is an environment problem, not an authoring ContractViolation — propagate
            # the compiler's own clear ImportError raw (the same posture as
            # `import conjured.server`). Canon scopes this exemption to the blessed
            # names only (handler/reference.md § The compile directive sub-form).
            raise
        # A THIRD-PARTY compiler's run-time ImportError is that compiler's own failure —
        # it stays inside the closed compose-time channel exactly like any other raise
        # (the environment exemption never extends to foreign code).
        raise ContractViolation(
            check=Check.COMPILE_ARTIFACT, rule_id="R-pipeline-001",
            expected=f"compiler '{compiler}' accepts its bound parameters and produces an artifact",
            actual=f"the compiler raised ImportError: {exc}",
            remediation_hint="the third-party compiler failed importing its own "
                             "dependency at run time — fix that package's environment; "
                             "only a blessed compiler's missing optional extra "
                             "propagates raw",
            file_path=toml_str,
        ) from exc
    except Exception as exc:  # noqa: BLE001 — any other raise is the compiler's own failure
        raise ContractViolation(
            check=Check.COMPILE_ARTIFACT, rule_id="R-pipeline-001",
            expected=f"compiler '{compiler}' accepts its bound parameters and produces an artifact",
            actual=f"the compiler raised {type(exc).__name__}: {exc}",
            remediation_hint="fix the compile parameter value(s) the compiler rejected (e.g. a "
                             "malformed regex pattern, an unparseable jinja template, an invalid "
                             "json_schema)",
            file_path=toml_str,
        ) from exc
