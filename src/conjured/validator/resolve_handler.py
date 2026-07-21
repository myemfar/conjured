"""Handler-name → callable resolution — the runtime half of the resolution sequence
(``conjured/docs/architecture/handler-resolution.md`` § Resolution sequence steps 3–7).

The Phase-1a half (steps 1–2's *declaration* lookup) is registry membership in
``validator.compile``; this module is the **callable** half, run at compose time, every
failure a compose-time ``ContractViolation`` (nothing here can fail at runtime):

3. **Source-AST audit before import** — read the module source from ``spec.origin`` and
   run the R-handler-pure-module walk + import-time-I/O scan BEFORE ``import_module``
   (a post-import audit cannot prevent import-time I/O). ``validator.ast_audit`` owns
   the walker.
4. **Import and read the attribute** — ``import_module`` + ``getattr`` (dotted), or the
   entry-point ``.load()`` (short name).
5. **Function-shape check** — the vector-2 seal, ``inspect.isfunction`` (the
   admit/reject conformance set is fixed at ``function-shape-predicate/conformance-set``:
   admits ``def`` / ``lambda`` / ``@functools.wraps``-decorated; rejects classes,
   callable instances, bound methods, builtins, ``functools.partial`` results).
6. **Signature introspection from the real ``__code__``** — kwarg-only, parameter set
   equal to the declared union (reads ports ∪ ``bindings.<name>`` ∪ ``services`` iff a
   service-typed binding is declared), ``*args`` / ``**kwargs`` collectors rejected
   (R-handler-001 signature-union). The check reads ``__code__`` (``co_flags`` /
   ``co_varnames``), never ``inspect.signature`` alone — a faked ``__signature__``
   cannot hide a collector or widen the set, and the dispatch call is built from the
   declaration anyway (the TOML drives the call; see ``runner.dispatch``), so the
   honest compose-time check + the un-fakeable code object close the signature-check
   edge cases: a faked ``__signature__``, a hidden ``*args``/``**kwargs`` collector,
   and a parameter set widened past the declared union.
7. **Populate** :class:`HandlerEntry` — the immutable five-field post-resolution record
   the runner dispatches from.

**Two discovery paths, one sequence** (§ Resolution mechanism / priority): dot-presence
is the mechanical selector — a name containing a dot is a dotted path (module prefix +
function name, no entry-points lookup); a dot-less name is a ``conjured.handlers``
entry-points short name. A short name registered by more than one installed distribution
is a collision the engine fails loud on (§ Entry-points collision) — never silently
disambiguated. Namespace packages (``find_spec().origin is None``) are rejected at
step 2, before the step-3 source read they would make impossible.

(Locating a dotted module's spec necessarily imports its *parent* packages — a Python
import-system fact; the audit covers the named handler module itself. Auditing parent
``__init__`` chains is part of the AST audit's reactive coverage-tightening tail
(min-viable by ratified decision), not the current walk's scope.)
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import os
import pathlib
import sys
from dataclasses import dataclass
from typing import Callable, Literal

from conjured.errors import Check, ContractViolation
from conjured.ir.handler import HandlerDeclaration
from conjured.validator.ast_audit import audit_handler_module_source
from conjured.validator.audit_stamp import require_fresh_stamp

#: The handler entry-points group (service-type/reference.md § Entry-point groups).
HANDLER_ENTRY_POINT_GROUP = "conjured.handlers"


@dataclass(frozen=True, slots=True)
class HandlerEntry:
    """The engine's post-resolution record for one resolved handler — the runtime-facing
    result of resolution; the runner dispatches from it
    (``handler-resolution.md`` § The HandlerEntry record). Exactly five fields, immutable
    once constructed; all five are stable across edits to the handler's TOML body (they
    record resolved identity, not declared schema). ``module`` is deliberately not a
    field — it derives from ``qualified_name``."""

    qualified_name: str
    callable: Callable[..., dict[str, object] | None]
    kind: Literal["transform", "service", "hook"]
    package: str
    toml_path: pathlib.Path


# ---------------------------------------------------------------------------
# Steps 2-4 — locate, audit, import
# ---------------------------------------------------------------------------


def locate_spec(
    module_name: str, *, name: str, toml_path: str,
    rule_id: str = "R-pipeline-001", what: str = "handler",
):
    """Step 2 — locate the module spec without executing the module. Rejects
    namespace packages here, before step 3 reads source from the origin.

    Shared across the sibling resolution paths (handler / adapter / validator), which
    differ only in the citing ``rule_id`` (R-pipeline-001 for a pipeline-named handler;
    R-handler-012 for a field validator) and the diagnostic noun ``what``."""
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError) as exc:
        raise ContractViolation(
            check=Check.HANDLER_MODULE_IMPORT, rule_id=rule_id,
            expected=f"{what} name '{name}' resolves: module '{module_name}' is importable",
            actual=f"module spec lookup failed ({type(exc).__name__}: {exc})",
            remediation_hint=(
                f"module '{module_name}' not importable; check that the providing "
                "package is installed and importable"
            ),
            file_path=toml_path,
        ) from exc
    if spec is None:
        raise ContractViolation(
            check=Check.HANDLER_MODULE_IMPORT, rule_id=rule_id,
            expected=f"{what} name '{name}' resolves: module '{module_name}' is importable",
            actual="no module spec found",
            remediation_hint=(
                f"module '{module_name}' not importable; check that the providing "
                "package is installed and importable"
            ),
            file_path=toml_path,
        )
    if spec.origin is None:
        locations = list(spec.submodule_search_locations or ())
        where = locations[0] if locations else f"the '{module_name}' package directory"
        raise ContractViolation(
            check=Check.HANDLER_NAMESPACE_PACKAGE, rule_id=rule_id,
            expected=f"module '{module_name}' lives in a regular package (one source origin)",
            actual="a namespace package (PEP 420) — find_spec reports origin is None",
            remediation_hint=(
                f"module '{module_name}' is a namespace package (PEP 420); add an empty "
                f"__init__.py to {where} to make it a regular package"
            ),
            file_path=toml_path,
        )
    return spec


def decode_module_source(
    raw: bytes, *, origin: str, toml_path: str, check: Check,
    rule_id: str = "R-pipeline-001",
) -> str:
    """Decode module source bytes the way the import system would (PEP 263 encoding
    declarations + BOM via ``importlib.util.decode_source``) — a legal non-UTF-8
    module stays auditable. Undecodable bytes raise the structured compose-time
    ``ContractViolation`` (every resolution failure is one; a raw ``UnicodeDecodeError``
    escaping this boundary would be an untyped failure)."""
    try:
        return importlib.util.decode_source(raw)
    except (UnicodeDecodeError, SyntaxError, LookupError) as exc:
        raise ContractViolation(
            check=check, rule_id=rule_id,
            expected="the module source decodes as Python source (PEP 263)",
            actual=f"source at '{origin}' is undecodable ({type(exc).__name__}: {exc})",
            remediation_hint="fix the module's text encoding (or its coding declaration)",
            file_path=toml_path,
        ) from exc


#: The default audit-name phrasing pair for the handler-family diagnostics: the long form
#: (the non-file-origin message) and the short form (the read/parse messages). The adapter
#: sibling passes its own pair ("the vector-7 AST audit" / "the vector-7 audit").
HANDLER_AUDIT_LABEL: tuple[str, str] = ("the step-3 source-AST audit", "the step-3 audit")


def read_and_audit_source(
    spec, *, toml_path: str, rule_id: str = "R-pipeline-001", what: str = "handler",
    audit_enforcement: bool = False,
    auditor: "Callable[..., None] | None" = None,  # default: audit_handler_module_source (below)
    pure_check: Check = Check.HANDLER_PURE_MODULE,
    audit_label: tuple[str, str] = HANDLER_AUDIT_LABEL,
    pure_hint: "str | None" = None,
) -> None:
    """Step 3 — read the module source from the spec's origin and audit it BEFORE
    import. A non-file origin (builtin / frozen / extension module) has no auditable
    source — fail loud rather than skip the seal. Undecodable or unparseable source is
    likewise a structured compose-time failure (the module could never import).

    The ONE source-audit boundary for every sibling resolution path: the validator and
    compiler paths share the handler defaults (the R-handler-pure-module audit applies
    unchanged — ``rule_id`` / ``what`` vary only the import-class diagnostics, never the
    audit's own rule); the ADAPTER path passes its scope extension — ``auditor`` (the
    vector-7 walker), ``pure_check`` (:data:`Check.ADAPTER_PURE_MODULE`), its
    ``audit_label`` pair, and its shorter ``pure_hint``.

    **Audit-stamp freshness** (handler/reference.md § Audit stamps). When
    ``audit_enforcement`` is set (the deployment opt-in), the sibling ``<module>.audit.toml``
    stamp is verified fresh here — reusing the exact source bytes the AST walk read (no
    second source read), refusing any not-fresh / malformed stamp
    (``validator.audit_stamp.require_fresh_stamp``). With the opt-in absent the stamp is
    never read (no consumer, no read, no consequence)."""
    audit_source = auditor if auditor is not None else audit_handler_module_source
    origin = spec.origin
    # guarantees: resolve-non-file-origin-fails-loud
    if not os.path.isfile(origin):
        raise ContractViolation(
            check=pure_check, rule_id="R-handler-pure-module",
            expected=f"the {what} module is backed by a readable Python source file "
                     f"({audit_label[0]} reads it before import)",
            actual=f"module origin '{origin}' is not a readable source file",
            remediation_hint=pure_hint if pure_hint is not None else (
                f"{what}s live in plain .py modules; builtin/extension "
                f"modules cannot host {what}s"
            ),
            file_path=toml_path,
        )
    # guarantees: resolve-source-read-fails-structured
    try:
        with open(origin, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        # The isfile guard above cannot cover a permission-denied or
        # vanished-after-isfile origin — the read itself must stay inside the closed
        # compose-time channel, never a raw OSError.
        raise ContractViolation(
            check=Check.HANDLER_MODULE_IMPORT, rule_id=rule_id,
            expected=f"the {what} module source at '{origin}' is readable ({audit_label[1]} "
                     "reads the exact source the import will execute)",
            actual=f"source read failed ({type(exc).__name__}: {exc})",
            remediation_hint="the module file is unreadable (permissions?) or vanished "
                             "after discovery; make the source file readable and re-compose",
            file_path=toml_path,
        ) from exc
    source = decode_module_source(
        raw, origin=origin, toml_path=toml_path, check=Check.HANDLER_MODULE_IMPORT,
        rule_id=rule_id,
    )
    try:
        audit_source(source, origin=origin)
    except SyntaxError as exc:
        raise ContractViolation(
            check=Check.HANDLER_MODULE_IMPORT, rule_id=rule_id,
            expected=f"the {what} module parses as Python ({audit_label[1]} walks its AST)",
            actual=f"syntax error in '{origin}' ({exc.msg} at line {exc.lineno})",
            remediation_hint="fix the module's syntax — it could not be imported either",
            file_path=toml_path,
        ) from exc
    if audit_enforcement:
        # The audit-stamp freshness gate — reuse the `raw` bytes just read (no second
        # source read); only under the deployment's audit_enforcement opt-in.
        require_fresh_stamp(
            origin=origin, source_bytes=raw, toml_path=toml_path, what=what,
        )


def _evict_stale_module(
    module_name: str, origin: str | None, *,
    rule_id: str = "R-pipeline-001", what: str = "handler",
) -> None:
    """The fresh-resolution eviction — the compose-time half of hot-reload semantics
    (``handler-resolution.md`` § Hot-reload semantics: "a fresh compose sees freshly
    resolved handlers"): when the module step 4 is about to import already sits in
    ``sys.modules`` AND that cached entry was loaded from the SAME file step 3 just read
    and audited, evict it so the import executes the audited on-disk source. Two
    contracts ride this one move: audited source IS executed code (the step-3 audit
    vouches for what actually runs), and a re-compose after an on-disk edit dispatches
    the NEW module code.

    Scoped to declaration-resolved modules only: engine modules (``conjured.*`` — e.g. a
    native adapter's class path) and stdlib modules are never evicted (evicting a live
    engine/stdlib module would fork class identities process-wide for zero freshness
    gain — their source is not authoring surface). A cached entry whose ``__file__``
    DIFFERS from the audited origin is a **detected audited-vs-executed divergence**:
    two files claim one module name in this process (a shadowed package, a stale
    install beside local source — the ``sys.path``-mutation case), so the module that
    would execute is not the file the step-3 audit and source hash just covered. The
    compose REJECTS it loud (ContractViolation) rather than proceed — the
    verification-path-bypass class (user ruling 2026-07-10, replacing the former
    silent-proceed boundary)."""
    # guarantees: resolve-fresh-eviction
    top = module_name.partition(".")[0]
    if top == "conjured" or top in sys.stdlib_module_names:
        return
    cached = sys.modules.get(module_name)
    if cached is None or origin is None:
        return
    cached_file = getattr(cached, "__file__", None)
    if cached_file is None:
        # A declaration-resolved module cached with NO __file__ is always anomalous —
        # a normal disk import sets it, and the namespace/builtin origins were rejected
        # or exempted upstream — so this entry was planted in memory: import_module
        # would serve it and silently SKIP executing the file step 3 just audited (the
        # audited-vs-executed bypass class this seal exists to foreclose). Detected
        # divergence, not a silent proceed.
        raise ContractViolation(
            check=Check.MODULE_ORIGIN_DIVERGENCE,
            rule_id=rule_id,
            expected=f"the {what} module '{module_name}' about to execute is the file "
                     f"the source audit just read ('{origin}')",
            actual=f"sys.modules already holds '{module_name}' with no __file__ — an "
                   "in-memory module that would execute INSTEAD of the just-audited "
                   "file (a planted or synthetic module entry)",
            remediation_hint="remove the in-memory sys.modules entry (or the tooling "
                             f"that plants it) so '{module_name}' resolves to exactly "
                             "the audited file",
            file_path=origin,
            section_path=module_name,
        )
    if os.path.normcase(os.path.abspath(cached_file)) == os.path.normcase(
        os.path.abspath(origin)
    ):
        del sys.modules[module_name]
        return
    raise ContractViolation(
        check=Check.MODULE_ORIGIN_DIVERGENCE,
        rule_id=rule_id,
        expected=f"the {what} module '{module_name}' about to execute is the file the "
                 f"source audit just read ('{origin}')",
        actual=f"sys.modules already holds '{module_name}' loaded from a DIFFERENT "
               f"file ('{cached_file}') — two files claim one module name in this "
               f"process, so the audited source is not the source that would execute",
        remediation_hint="fix the environment's module resolution: remove the shadowed "
                         "or stale copy (a leftover install beside local source, a "
                         "sys.path entry pointing at another tree) so "
                         f"'{module_name}' resolves to exactly one file",
        file_path=origin,
        section_path=module_name,
    )


def import_audited_module(
    module_name: str, *, origin: str | None, name: str, toml_path: str,
    rule_id: str = "R-pipeline-001", what: str = "handler",
):
    """Step 4 — import the module step 3 just audited, behind the fresh-resolution
    eviction, inside the closed compose-time error channel: a failing top-level import
    (e.g. a missing dependency — an ``import`` statement the source-AST audit legally
    admits, since the seal targets I/O and instantiation, not imports) or ANY exception
    the module's top-level code raises surfaces as a compose-time ``ContractViolation``,
    never a raw ``ModuleNotFoundError`` / arbitrary exception.

    Shared by the sibling resolution paths (handler / adapter / validator / compiler),
    which differ only in the citing ``rule_id`` and the diagnostic noun ``what``."""
    _evict_stale_module(module_name, origin, rule_id=rule_id, what=what)
    # guarantees: resolve-import-fails-structured
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        raise ContractViolation(
            check=Check.HANDLER_MODULE_IMPORT, rule_id=rule_id,
            expected=f"{what} name '{name}': module '{module_name}' imports cleanly "
                     "at compose (its top-level code runs without raising)",
            actual=f"module import failed ({type(exc).__name__}: {exc})",
            remediation_hint=f"importing '{module_name}' raised — fix the module's "
                             "top-level imports/statements (a missing dependency, a "
                             "top-level raise); resolution imports the audited module "
                             "at compose, so it must import cleanly",
            file_path=toml_path,
        ) from exc


# ---------------------------------------------------------------------------
# The two shared resolution legs — every sibling resolver composes THESE
# (handler / adapter / validator / compiler differ only in selector + contract
# checks; the legs are one sequence with per-kind diagnostic parameters)
# ---------------------------------------------------------------------------


def resolve_dotted_attribute(
    name: str, *, toml_path: str,
    rule_id: str = "R-pipeline-001", what: str = "handler", attr_hint: str = "function",
    audit_enforcement: bool = False,
    auditor: "Callable[..., None] | None" = None,
    pure_check: Check = Check.HANDLER_PURE_MODULE,
    audit_label: tuple[str, str] = HANDLER_AUDIT_LABEL,
    pure_hint: "str | None" = None,
):
    """The shared **dotted-path leg** (steps 2–4): locate the spec (namespace-package
    rejection) → source-audit BEFORE import → import behind the fresh-resolution
    eviction → read the attribute. Returns the resolved object; every failure is the
    structured compose-time ``ContractViolation`` citing the caller's ``rule_id`` /
    ``what`` (``attr_hint`` names what the attribute should be — "function" / "class" /
    "compiler" — in the missing-attribute remediation)."""
    module_name, _, attr = name.rpartition(".")
    spec = locate_spec(module_name, name=name, toml_path=toml_path, rule_id=rule_id, what=what)
    read_and_audit_source(  # step 3 BEFORE import
        spec, toml_path=toml_path, rule_id=rule_id, what=what,
        audit_enforcement=audit_enforcement, auditor=auditor, pure_check=pure_check,
        audit_label=audit_label, pure_hint=pure_hint,
    )
    module = import_audited_module(  # step 4 — fresh-resolution eviction + closed channel
        module_name, origin=spec.origin, name=name, toml_path=toml_path,
        rule_id=rule_id, what=what,
    )
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ContractViolation(
            check=Check.HANDLER_MODULE_IMPORT, rule_id=rule_id,
            expected=f"module '{module_name}' exports '{attr}'",
            actual=f"no attribute '{attr}' on the imported module",
            remediation_hint=f"module '{module_name}' does not export '{attr}'; "
                             f"check spelling or that the {attr_hint} is defined at "
                             "module top level",
            file_path=toml_path,
        ) from exc


def select_entry_point(
    group: str, name: str, *, toml_path: str, rule_id: str,
    on_missing: Literal["raise", "none"] = "none",
):
    """The shared **entry-point selector**: exactly one registration under ``group``
    for ``name``, or the fail-loud collision (no winner is picked, no install-order
    tiebreak — § Entry-points collision). Zero registrations follow the caller's
    selector contract: ``on_missing="none"`` returns ``None`` (the caller falls
    through to its other leg); ``on_missing="raise"`` is the handler short-name case
    (a dot-less name has no dotted-path fallback)."""
    eps = importlib.metadata.entry_points(group=group)
    matches = [ep for ep in eps if ep.name == name]
    if not matches:
        if on_missing == "none":
            return None
        raise ContractViolation(
            check=Check.HANDLER_MODULE_IMPORT, rule_id=rule_id,
            expected=f"short name '{name}' is registered under the "
                     f"'{group}' entry-points group",
            actual="no installed distribution registers it",
            remediation_hint="install the providing package, or use an explicit "
                             "dotted-path reference",
            file_path=toml_path,
        )
    if len(matches) > 1:
        dists = sorted(
            getattr(ep.dist, "name", "<unknown distribution>") for ep in matches
        )
        raise ContractViolation(
            check=Check.ENTRY_POINT_COLLISION, rule_id=rule_id,
            expected=f"exactly one distribution registers entry point '{name}' "
                     f"under '{group}'",
            actual=f"registered by multiple packages: {dists}",
            remediation_hint=f"entry-point '{name}' registered by multiple "
                             f"packages: {dists}; resolve by uninstalling one or using "
                             "explicit dotted-path references",
            file_path=toml_path,
        )
    return matches[0]


def load_entry_point(
    ep, name: str, *, toml_path: str,
    rule_id: str = "R-pipeline-001", what: str = "handler",
    audit_enforcement: bool = False,
    auditor: "Callable[..., None] | None" = None,
    pure_check: Check = Check.HANDLER_PURE_MODULE,
    audit_label: tuple[str, str] = HANDLER_AUDIT_LABEL,
    pure_hint: "str | None" = None,
):
    """The shared **entry-point leg** (steps 2–4 for a selected entry point): locate the
    EP's module spec → source-audit BEFORE import → fresh-resolution eviction →
    ``ep.load()`` inside the closed compose-time channel. The load ``except`` is broad
    on purpose: besides a stale entry-point declaration (ImportError/AttributeError),
    the module's top-level code can raise anything — every shape stays structured."""
    spec = locate_spec(ep.module, name=name, toml_path=toml_path, rule_id=rule_id, what=what)
    read_and_audit_source(  # step 3 BEFORE import
        spec, toml_path=toml_path, rule_id=rule_id, what=what,
        audit_enforcement=audit_enforcement, auditor=auditor, pure_check=pure_check,
        audit_label=audit_label, pure_hint=pure_hint,
    )
    _evict_stale_module(ep.module, spec.origin, rule_id=rule_id, what=what)
    # guarantees: resolve-import-fails-structured
    try:
        return ep.load()  # step 4 — import + attribute read
    except Exception as exc:
        raise ContractViolation(
            check=Check.HANDLER_MODULE_IMPORT, rule_id=rule_id,
            expected=f"entry point '{name}' loads: module '{ep.module}' exports "
                     f"'{ep.attr}'",
            actual=f"entry-point load failed ({type(exc).__name__}: {exc})",
            remediation_hint=f"module '{ep.module}' does not export '{ep.attr}'; "
                             "the providing package's entry-point declaration is "
                             "stale or broken",
            file_path=toml_path,
        ) from exc


def _distribution_for(module_name: str) -> str:
    """The distribution a dotted-path module resolved from — the ``package`` field's
    attribution source. A module importable outside any installed distribution (a
    plain ``sys.path`` module — the test-fixture case) attributes to its top-level
    module name, the best-available package identity."""
    top = module_name.partition(".")[0]
    dists = importlib.metadata.packages_distributions().get(top)
    if dists:
        return sorted(set(dists))[0]
    return top


# ---------------------------------------------------------------------------
# Steps 5-6 — shape + signature seals
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CodeSignature:
    """The signature surface of one function, read from its real ``__code__`` (the
    un-fakeable surface — a planted ``__signature__`` cannot hide a collector or widen
    the set): the collector flags, the positional parameter names, and the keyword-only
    parameter-name set. The ONE shared walk behind the three resolvers' signature
    checks (handler step 6 / validator step 6 / the adapter ``invoke()`` contract),
    which differ only in which facts they reject on and the ``ContractViolation`` they
    raise — never in how the facts are read."""

    has_varargs: bool
    has_varkwargs: bool
    positional: tuple[str, ...]
    kwonly: frozenset[str]


def code_signature(code) -> CodeSignature:
    """Walk one real code object into its :class:`CodeSignature` facts, using the
    public ``inspect.CO_VARARGS`` / ``inspect.CO_VARKEYWORDS`` flag constants."""
    return CodeSignature(
        has_varargs=bool(code.co_flags & inspect.CO_VARARGS),
        has_varkwargs=bool(code.co_flags & inspect.CO_VARKEYWORDS),
        positional=tuple(code.co_varnames[: code.co_argcount]),
        kwonly=frozenset(
            code.co_varnames[code.co_argcount : code.co_argcount + code.co_kwonlyargcount]
        ),
    )


def check_function_shape(obj: object, *, toml_path: str, hint: str) -> None:
    """The shared vector-2 seal (step 5): ``inspect.isfunction``. Rejects classes,
    callable instances, bound methods, builtins, and ``functools.partial`` results (a
    partial's pre-bound args would bypass the declaration / bindings / hash surface).
    One check for the three bare-function resolution paths (handler / validator /
    compiler — the adapter path requires the OPPOSITE shape, a class); each caller
    supplies its own remediation ``hint`` naming its subject and rule."""
    if not inspect.isfunction(obj):
        raise ContractViolation(
            check=Check.HANDLER_FUNCTION_SHAPE, rule_id="R-handler-bare-function",
            expected="a bare function (inspect.isfunction) — def / lambda / "
                     "functools.wraps-decorated",
            actual=f"a {type(obj).__name__}",
            remediation_hint=hint,
            file_path=toml_path,
        )


def signature_union(declaration: HandlerDeclaration) -> frozenset[str]:
    """The engine-owned kwarg union R-handler-001 fixes: declared input-port names ∪
    ``bindings.<name>`` names ∪ the reserved ``services`` kwarg iff the kind declares a
    service-typed binding ∪ (for a hook) the hook's declared ``transport_schema`` field
    names — deployment-supplied transport delivered to the emitting body as kwargs, per
    handler/reference.md § ``transport_schema`` (delivery follows the emission
    boundary). This is the single source both the step-6 check and the dispatch call
    are built from (the TOML drives the call — one problem, one solution). A transform
    structurally has no ``service_bindings`` / ``transport_schema`` attribute, so it
    can never gain ``services`` or transport kwargs (R-handler-004's mechanical
    half / the hook-only kind discipline)."""
    union = {field.name for field in declaration.reads}
    union |= {binding.name for binding in declaration.bindings}
    if getattr(declaration, "service_bindings", ()):
        union.add("services")
    union |= {field.name for field in getattr(declaration, "transport_schema", ())}
    return frozenset(union)


def _check_signature(
    fn, declaration: HandlerDeclaration, *, qualified_name: str, toml_path: str
) -> None:
    """Step 6 — the R-handler-001 signature-union check, read from the function's real
    ``__code__`` (un-fakeable; ``inspect.signature`` honors a ``__signature__``
    override, which a lying module could plant)."""
    sig = code_signature(fn.__code__)
    declared = signature_union(declaration)
    if sig.has_varargs:
        raise ContractViolation(
            check=Check.HANDLER_SIGNATURE, rule_id="R-handler-001",
            expected="a kwarg-only signature with no *args collector",
            actual=f"'{qualified_name}' declares a *args collector (real __code__)",
            remediation_hint="remove the *args collector; declare exactly the union of "
                             "reads ports, bindings names, and services where declared",
            file_path=toml_path,
        )
    if sig.has_varkwargs:
        raise ContractViolation(
            check=Check.HANDLER_SIGNATURE, rule_id="R-handler-001",
            expected="a kwarg-only signature with no **kwargs collector",
            actual=f"'{qualified_name}' declares a **kwargs collector (real __code__)",
            remediation_hint="remove the **kwargs collector; declare exactly the union "
                             "of reads ports, bindings names, and services where declared",
            file_path=toml_path,
        )
    if sig.positional:
        raise ContractViolation(
            check=Check.HANDLER_SIGNATURE, rule_id="R-handler-001",
            expected="a kwarg-only signature (every parameter keyword-only)",
            actual=f"'{qualified_name}' declares positional parameter(s) {list(sig.positional)}",
            remediation_hint="make every parameter keyword-only: def handler(*, ...)",
            file_path=toml_path,
        )
    actual = sig.kwonly
    if actual != declared:
        missing = sorted(declared - actual)
        extra = sorted(actual - declared)
        raise ContractViolation(
            check=Check.HANDLER_SIGNATURE, rule_id="R-handler-001",
            expected=f"keyword-only parameters equal to the declared union {sorted(declared)}",
            actual=f"signature parameters {sorted(actual)}",
            remediation_hint=f"'{qualified_name}' signature does not match the TOML "
                             f"declaration; missing kwargs: {missing}; extra kwargs: {extra}",
            file_path=toml_path,
        )


# ---------------------------------------------------------------------------
# The resolution entry — steps 2-7 in sequence
# ---------------------------------------------------------------------------


def resolve_handler(
    name: str,
    declaration: HandlerDeclaration,
    *,
    toml_path: str | os.PathLike[str],
    audit_enforcement: bool = False,
) -> HandlerEntry:
    """Resolve a pipeline-declared handler name to its :class:`HandlerEntry`.

    ``name`` is the string the pipeline declaration wrote (step 1); ``declaration`` is
    the registry-resolved handler declaration (the Phase-1a half) supplying the
    signature union and the ``kind``; ``toml_path`` is the handler's declaration TOML
    (the record's fifth field and the diagnostics' declaration-site locus). Compose-time
    only; every failure is a ``ContractViolation``.

    ``audit_enforcement`` (the deployment opt-in, threaded from stage-4 assembly) gates the
    step-3 audit-stamp freshness check (handler/reference.md § Audit stamps): under it, the
    handler module's sibling ``.audit.toml`` must be fresh or compose refuses; without it the
    stamp is never read.
    """
    toml_str = str(toml_path)
    if "." in name:
        # Primary — dotted-path module resolution (dot-presence is the selector;
        # no entry-points lookup runs). Steps 2-4 are the shared dotted leg.
        resolved = resolve_dotted_attribute(
            name, toml_path=toml_str, audit_enforcement=audit_enforcement,
        )
        qualified_name = name
        package = _distribution_for(name.rpartition(".")[0])
    else:
        # Additive — entry-points short-name resolution (a dot-less name has no
        # dotted-path fallback, so a missing registration raises). Steps 2-4 are the
        # shared selector + entry-point leg.
        ep = select_entry_point(
            HANDLER_ENTRY_POINT_GROUP, name, toml_path=toml_str,
            rule_id="R-pipeline-001", on_missing="raise",
        )
        resolved = load_entry_point(
            ep, name, toml_path=toml_str, audit_enforcement=audit_enforcement,
        )
        # The resolved dotted form of the short name (the record stores the dotted
        # identity, not the alias).
        qualified_name = f"{ep.module}.{ep.attr}"
        package = ep.dist.name if ep.dist is not None else ep.module.partition(".")[0]

    check_function_shape(  # step 5
        resolved, toml_path=toml_str,
        hint=f"'{qualified_name}' is not a bare function; handlers MUST "
             "be bare kwarg-only functions per R-handler-bare-function",
    )
    _check_signature(resolved, declaration, qualified_name=qualified_name, toml_path=toml_str)  # step 6
    return HandlerEntry(  # step 7
        qualified_name=qualified_name,
        callable=resolved,
        kind=declaration.kind,
        package=package,
        toml_path=pathlib.Path(toml_path),
    )
