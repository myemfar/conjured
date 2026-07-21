"""The in-memory declaration registry the compose-time validator resolves against.

Phase 1a's validator is a **pure function over an in-memory declaration set** — declarations
are *handed in*, never discovered via entry-points (entry-points discovery is a later thin
layer — ``handler-resolution.md`` § Resolution sequence). This registry is that set:
handler declarations keyed by qualified name, service-type declarations keyed by qualified
name, and composition declarations (trainable / nested-pipeline) keyed by their declaration
path (the string a ``kind = "composition"`` node names).

One registration path is not read-free: ``add_service_type`` of a ``conjured.lib.*`` name
performs a single deterministic read of the engine's OWN shipped sibling TOML to enforce the
native-identity guard (a registration under that namespace is legal only when it equals the
shipped declaration — R-service-type-004). That is the engine reading its own ship-time
artifact, not consumer discovery and not ``importlib`` — name *resolution* below stays pure
membership; only this one guard touches the filesystem, and only the engine's own files.

Name *resolution* in Phase 1a is **registry membership** — "resolve a name to its
*declaration* (for its ports)", never ``importlib`` (``handler-resolution.md``
§ Resolution sequence steps 1–2 vs 3–7). The module import, the
source-AST seals, the function-shape / signature checks, and ``HandlerEntry`` are Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from conjured.ir.composition import BundleComposition, PipelineComposition, TrainableComposition
from conjured.ir.deployment import DeploymentDeclaration
from conjured.ir.handler import HandlerDeclaration
from conjured.ir.service_type import ServiceTypeDeclaration

#: A registrable composition declaration — the realized composition kinds a
#: ``kind = "composition"`` pipeline node can resolve to. A bundle registers like any
#: other composition; it is textually substituted out at every walker's entry (the
#: pure-substitution kind — ``conjured.ir.substitute``).
CompositionDeclaration = TrainableComposition | PipelineComposition | BundleComposition

#: The engine-owned service-type namespace. A ``conjured.lib.*`` qualified name resolves to
#: the engine's shipped declaration and its one registered implementation
#: (R-service-type-004); registering under it is legal only when the declaration IS that
#: shipped sibling (native-library/reference.md § the engine-owned-identity clause).
_NATIVE_NAMESPACE = "conjured.lib."


def _shipped_native_service_type(qualified_name: str) -> ServiceTypeDeclaration | None:
    """The engine-shipped service-type declaration for a native qualified name, parsed from
    its sibling TOML — or ``None`` when the engine ships no service-type under that
    ``conjured.lib.*`` name. The engine-shipped ``conjured.lib.*`` service-types are exactly
    the native adapter table's keys (each a same-named sibling of its adapter module); any
    other ``conjured.lib.*`` name has no shipped declaration for a registration to equal.

    Imports are local: ``conjured.lib`` imports only stdlib (cycle-safe), and
    ``validator.parse`` is resolved at call time (never at registry import) so the module
    stays a leaf in the import graph."""
    from conjured.lib import NATIVE_TRAINABLE_ADAPTERS

    if qualified_name not in NATIVE_TRAINABLE_ADAPTERS:
        return None
    import tomllib
    from pathlib import Path

    import conjured.lib
    from conjured.validator.parse import parse_service_type

    submodule = qualified_name[len(_NATIVE_NAMESPACE):]
    toml_path = Path(conjured.lib.__file__).parent / f"{submodule}.toml"
    # Binary tomllib.load — TOML mandates UTF-8 and this is the canonical shipped-declaration
    # load path (matching how a consumer hand-loads the sibling TOML), so the parsed result is
    # byte-for-byte the declaration a legal registration hands in. A text read under the
    # platform default encoding would mis-decode non-ASCII annotation content and break the
    # equality check.
    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)
    return parse_service_type(data, file_path=str(toml_path))


def _guard_engine_owned_service_type(
    decl: ServiceTypeDeclaration, toml_path: str | None
) -> None:
    """Reject a registration that redefines an engine-owned native identity. A service-type
    whose name is under ``conjured.lib.*`` is admitted only when the declaration equals the
    engine-shipped sibling for that native qualified name (hand-loading the genuine shipped
    TOML stays legal); any other ``conjured.lib.*`` registration — a modified declaration, or
    a name the engine ships nothing under — fails loud (native-library/reference.md § the
    engine-owned-identity clause; R-service-type-004). Auto-registration of the shipped
    declarations is out of scope; this guard only polices what an author hands in."""
    if not decl.name.startswith(_NATIVE_NAMESPACE):
        return
    shipped = _shipped_native_service_type(decl.name)
    if shipped is not None and decl == shipped:
        return  # the genuine shipped declaration, hand-loaded — legal
    # Local import: errors is a leaf, but keep the registry's top imports to the IR it maps.
    from conjured.errors import Check, ContractViolation

    raise ContractViolation(
        check=Check.ENGINE_OWNED_IDENTITY, rule_id="R-service-type-004",
        expected="a service-type registered under the engine-owned 'conjured.lib.*' "
                 "namespace IS the engine's shipped declaration for that native qualified "
                 "name (hand-loading the genuine shipped TOML is legal)",
        actual=(
            f"'{decl.name}' names no engine-shipped native service-type — the "
            "'conjured.lib.*' namespace is engine-owned, and the engine ships no "
            "declaration under this name to register"
            if shipped is None
            else f"the declaration registered under '{decl.name}' differs from the "
                 "engine's shipped declaration for that native qualified name "
                 "(redefining an engine-owned identity)"
        ),
        remediation_hint="do not redefine an engine-owned native identity: bind the native "
                         "qualified name directly (its shipped declaration and one "
                         "registered implementation resolve through the native adapter "
                         "table), or give a non-catalog backend its own package-prefixed "
                         "qualified name and a certified adapter",
        # The declaration path is the natural locus; the name is the fallback when a caller
        # registers without one (add_service_type's toml_path is optional). At least one
        # location-bearing field is required (error-channel § Location-bearing field).
        file_path=toml_path or decl.name,
    )


@dataclass
class DeclarationRegistry:
    """A handed-in set of parsed declarations the validator resolves names against.

    Not entry-points discovery (a later layer); a plain in-memory map so the validator
    stays a pure, fuzzable function. ``add_*`` registers a parsed IR model under its
    resolution key; ``get_*`` returns it or ``None`` (the caller raises the
    resolution ``ContractViolation`` with the right diagnostic).
    """

    handlers: dict[str, HandlerDeclaration] = field(default_factory=dict)
    service_types: dict[str, ServiceTypeDeclaration] = field(default_factory=dict)
    compositions: dict[str, CompositionDeclaration] = field(default_factory=dict)
    #: One deployment per engine process (deployment/reference.md § One deployment per
    #: engine). Optional — the graph compiles without it; the transport/hook coverage
    #: checks run only when a deployment is paired.
    deployment: DeploymentDeclaration | None = None
    #: The deployment declaration's on-disk TOML path (optional, like the other path
    #: fields). Its DIRECTORY anchors the deployment's relative ``[artifacts]`` paths
    #: (deployment/reference.md § artifacts — resolved relative to the deployment
    #: declaration's own directory); the R-pipeline-003 comparison falls back to the
    #: process CWD when no path was registered.
    deployment_path: str | None = None
    #: Declaration TOML paths, keyed like their declaration maps. The path is part of a
    #: declaration's diagnostics identity: it feeds ``SchemaValidationError.schema_source``
    #: / ``ContractViolation.file_path`` (the contract-document path a consumer opens —
    #: error-channel/reference.md § per-class payloads) and the resolution seals' loci.
    #: Optional at registration (the Phase-1a validator never needs them); stage-4
    #: assembly requires them where it constructs diagnostics-bearing wrappers.
    handler_paths: dict[str, str] = field(default_factory=dict)
    service_type_paths: dict[str, str] = field(default_factory=dict)
    #: Composition declaration TOML paths, keyed like ``compositions``. The path's
    #: directory is the ANCHOR a composition's own ``{ file = "..." }`` bindings resolve
    #: against (``validator.resolve`` — a binding file path resolves relative to the
    #: directory of the declaration TOML that supplied it, so a composition shared by
    #: two pipelines in different directories resolves identically regardless of which
    #: composes first). Optional at registration; the binding-resolution pass fails loud
    #: when a composition carries a file binding but no registered path.
    composition_paths: dict[str, str] = field(default_factory=dict)

    def add_handler(
        self, qualified_name: str, decl: HandlerDeclaration, *, toml_path: str | None = None
    ) -> None:
        """Register a parsed handler declaration under its qualified name — the compose-time
        set the validator resolves handler names against. ``toml_path`` records the
        declaration's on-disk location (optional here — the Phase-1a validator never needs
        it; stage-4 assembly requires it where it constructs diagnostics-bearing wrappers, per
        the ``handler_paths`` field). This registers the *declaration*, never the handler
        *callable* (which resolves from the declaration's name — ``handler-resolution.md``
        § The HandlerEntry record). The in-process compose sequence this feeds — parse,
        register, ``compile_pipeline``, ``assemble``, ``run`` — is documented at
        ``conjured/docs/components/pipeline/reference.md`` § In-process compose API."""
        self.handlers[qualified_name] = decl
        if toml_path is not None:
            self.handler_paths[qualified_name] = toml_path

    def add_service_type(
        self, decl: ServiceTypeDeclaration, *, toml_path: str | None = None
    ) -> None:
        """Register a parsed service-type declaration under its declared qualified name.

        A ``conjured.lib.*`` name is an engine-owned identity: registering under it is legal
        only for the genuine engine-shipped declaration (hand-loaded); a modified redefinition
        fails loud (R-service-type-004 — :func:`_guard_engine_owned_service_type`). A native
        service-type's *adapter* resolves through the engine's native table, but its
        *declaration* is still registered here so compose-time validation can resolve the
        binding's type — the engine does not auto-register the shipped declarations.
        ``toml_path`` records the declaration's on-disk location (optional; see
        ``service_type_paths``). The in-process compose sequence is documented at
        ``conjured/docs/components/pipeline/reference.md`` § In-process compose API."""
        _guard_engine_owned_service_type(decl, toml_path)  # R-service-type-004
        self.service_types[decl.name] = decl
        if toml_path is not None:
            self.service_type_paths[decl.name] = toml_path

    def add_composition(
        self, path: str, decl: CompositionDeclaration, *, toml_path: str | None = None
    ) -> None:
        self.compositions[path] = decl
        if toml_path is not None:
            self.composition_paths[path] = toml_path

    def get_handler(self, qualified_name: str) -> HandlerDeclaration | None:
        return self.handlers.get(qualified_name)

    def get_handler_path(self, qualified_name: str) -> str | None:
        return self.handler_paths.get(qualified_name)

    def get_service_type(self, qualified_name: str) -> ServiceTypeDeclaration | None:
        return self.service_types.get(qualified_name)

    def get_service_type_path(self, qualified_name: str) -> str | None:
        return self.service_type_paths.get(qualified_name)

    def get_composition(self, path: str) -> CompositionDeclaration | None:
        return self.compositions.get(path)

    def get_composition_path(self, path: str) -> str | None:
        return self.composition_paths.get(path)
