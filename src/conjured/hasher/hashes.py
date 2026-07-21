"""The two sibling hashes — pure functions over the normalized declaration IR.

``pipeline_hash`` and ``training_bundle_hash`` (``architecture/hash-model.md``): SHA-256 over
a canonical serialization of the relevant declaration subgraph. **Pure functions** — no I/O,
no events, no manifest compare, no integrity logic, no dispatch (that orchestration is the
deployment-load path + Phase 4, and it *calls* these). Deterministic + stable.

**Input is the declaration IR, not the ``CompiledGraph`` (resolution 3a).** The compiled
graph is dispatch-flattened — it drops ``trainable.config`` + the backend binding
(``compile.py`` sets ``bindings=()`` on the trainable node), scopes channels, and dissolves
the composition boundary — so it is the wrong hash input. These run over the raw declaration
IR, reusing only the shared :func:`~conjured.validator.normalize.desugar_map` step so the
normalized wiring they hash is byte-identical to what the compiler validated.

**What each absorbs / excludes** (the closed exclusion set — ``hash-model.md`` § What is
explicitly NOT in the pipeline-hash + § Training-bundle-hash):

- **Excluded everywhere:** ``annotations`` blocks; a composable unit's ``meta.name`` (the family
  rule — renaming is hash-neutral; a ``[meta]`` carries no ``description``); **hook
  nodes/preprocessors** ("hooks … contribute to neither hash"). No outer wiring exists at an
  embed position to exclude: a composition node entry's key set is closed to ``{kind, name}``,
  so per-node maps are a handler-entry surface — folding into the pipeline-hash only, never a
  TBH (the load-bearing placement condition; the TBH covers the trainable's port shapes plus
  its own internal preprocessor wiring). A trainable ``output_schema`` field's ``description`` is model-facing contract
  content and IS hashed (folded by ``canon_field``; hash-model.md § What the pipeline-hash
  absorbs) — the one prose surface that reaches a hash, because it conditions generation.
- **Pipeline-hash absorbs:** non-hook node order; each handler node's qualified-name reference,
  normalized ``reads_map`` / ``writes_map``, inline binding values, and resolved handler
  declaration content; each embedded own-hash-domain composition's **own hash by reference**
  (its internal scope is opaque — only the hash flows up): a trainable's
  training-bundle-hash, a nested ``pipeline`` composition's own pipeline-hash (recursive);
  pipeline-level ``service_bindings`` identity values; ``merge``; ``[inputs]`` / ``[outputs]``.
- **Training-bundle-hash absorbs** (the trainable composition in isolation, **unscoped** — its
  channels are the author's internal names, so renaming the composition is hash-neutral):
  ``meta.kind``; boundary ``inputs`` / ``outputs``; the **ordered** ``[[preprocessors]]``
  sequence (each non-hook preprocessor's name/type, port schemas, declared service-bindings,
  inline binding values, and **desugared internal wiring maps**); the terminal ``[trainable]``
  node's ``config`` / ``service_bindings`` / ``reads`` / ``output_schema`` **port shapes**;
  internal ``merge``.

**Effective binding values (supplied-or-default).** A node's per-binding contribution is the
*effective* value: an inline supplied value folds its content; an external-file
(``FilePathBindingValue``) binding folds its **stamped canonicalized content** (read +
canonicalized by the stage-1 resolution pass, ``validator.resolve`` — so "inline X" and "a file
containing X" fold identically; the ``content_hash`` is the manifest/event-layer convenience,
never the fold, and the hasher stays pure and never reads a file); an omitted
default-bearing binding folds its declared default. An *unresolved* external-file binding (no
stamped ``content_hash``) **raises** ``EXTERNAL_BINDING_UNSUPPORTED`` — a structural backstop, the
hasher never hashes a path. The declared ship-time default ALSO folds on the handler-declaration
side (a second, distinct contribution).
"""

from __future__ import annotations

from typing import Any, Mapping

from conjured.errors import Check, ContractViolation
from conjured.ir.common import (
    FilePathBindingValue,
    InlineBindingValue,
    NodeBindingValue,
    SchemaBinding,
)
from conjured.ir.composition import (
    CompositionKind,
    PipelineComposition,
    PreprocessorEntry,
    TrainableComposition,
)
from conjured.ir.handler import (
    HandlerDeclaration,
    HookDeclaration,
    ServiceDeclaration,
    TransformDeclaration,
)
from conjured.ir.pipeline import CompositionNode, HandlerNode, PipelineDeclaration
from conjured.ir.substitute import substitute_bundle_nodes
from conjured.canonical import (
    canon_schema,
    canon_schema_ordered,
    canon_service_binding_decl,
    canon_service_supply,
    canon_type,
    canon_value,
    sha256_of,
)
from conjured.ir.common import ServiceBindingSupply
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.validator.compile import effective_config
from conjured.validator.normalize import desugar_map, normalize_binding_value
from conjured.validator.registry import DeclarationRegistry


# ---------------------------------------------------------------------------
# Shared: the EFFECTIVE binding values at a supply site (supplied-or-default)
# ---------------------------------------------------------------------------


def _canon_or_malformed(
    value: Any, *, where: str, section_path: str, what: str
) -> Any:
    """``canon_value`` with the structured fail-loud wrap the sibling folds carry
    (fail-loud parity): a non-canonicalizable or non-finite SUPPLIED value — reachable
    only via the direct-Pydantic dialect — raises ``MALFORMED_DECLARATION``, never a
    bare ``TypeError``/``ValueError`` escaping the closed compose-time channel."""
    try:
        return canon_value(value)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
            expected=f"{what} is a canonicalizable JSON-native value",
            actual=f"non-canonicalizable value at {where} ({exc})",
            remediation_hint="supply a JSON-native scalar/object the hasher can "
                             "serialize (finite numbers only)",
            composition_ref=where, section_path=section_path,
        ) from exc


def canon_supplied_bindings(
    bindings: tuple[NodeBindingValue, ...],
    *,
    where: str,
    declared: tuple = (),
) -> dict[str, Any]:
    """Canonicalize the **effective** binding values at a supply site as a name-keyed map
    (hash-model.md § What the pipeline-hash absorbs — the per-node binding contribution is the
    *effective value*, supplied-or-default):

    - An **inline** supplied value folds its canonicalized content.
    - An **external-file** supplied value (the ``{ file = "..." }`` form) folds its stamped
      **canonicalized content** (the ``content_hash`` is the manifest/event-layer convenience,
      never the fold) — read + canonicalized at the stage-1 resolution pass so "inline X" and "a
      file containing X" fold identically (resolution 3c, now realized). An
      *unresolved* file binding (``content_hash is None``) **raises** — the hasher requires a
      resolved IR, never hashes a path (fail loud).
    - A declared **default-bearing** binding the node **omits** folds its declared default as the
      effective value (the supply-site contribution; the declared default ALSO folds on the
      handler-declaration side — two distinct contributions, handler/reference.md § Ship-time
      defaults). ``declared`` is the **referenced handler's** declared ``Binding`` tuple — for both
      an outer node and a composition preprocessor (the mirror-pipeline principle; one resolved
      fold path).

    A **single-field** binding's effective value folds in its **normalized (bare) form
    regardless of supply spelling** (:func:`~conjured.validator.normalize.normalize_binding_value`
    — the compose-join normalization; ``hash-model.md`` § What the pipeline-hash absorbs), so
    inline-bare / one-field-table / external-file / one-field-default all fold to one value.
    ``declared`` supplies the per-binding ``fields`` the normalization keys on; a supplied
    binding with no declared schema (defensive — a validated pipeline never reaches here)
    folds un-normalized.

    **Public shared surface — two consumers:** the hash folds in this module
    (:func:`_canon_resolved_handler_node`), and the derivables bundle's binding snapshot
    (``conjured.derivables._binding_snapshot``), which MUST fold the identical effective
    values so the snapshot and the pipeline-hash cannot diverge. A signature or semantics
    change here is a change to both."""
    decl_bodies = {b.name: b.body for b in declared}
    out: dict[str, Any] = {}
    for b in bindings:
        body = decl_bodies.get(b.name)
        fields = body.fields if isinstance(body, SchemaBinding) else None
        if isinstance(b, FilePathBindingValue):
            if b.content_hash is None:
                raise ContractViolation(
                    check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
                    expected="a resolved external-file binding (stage-1 resolution stamps content_hash)",
                    actual=f"unresolved external declaration-file binding '{b.name}' = {b.path!r} at {where}",
                    remediation_hint="run the stage-1 binding resolution pass before hashing; the hasher never reads files or hashes a path",
                    composition_ref=where, section_path=f"bindings.{b.name}",
                )
            # The stamped CANONICALIZED CONTENT is the value contribution — the SAME structure an
            # inline supply of the same value folds. This is the path-neutrality property: "inline
            # X" and "a file containing X" canonicalize identically → same hash (the `path` is NOT
            # in the fold; the `content_hash` is the stamped convenience for the manifest/event
            # layer, but the FOLD is the content itself so it matches inline byte-for-byte). A
            # single-field binding normalizes the (already-canonical) resolved table to its bare
            # value — the same bare form an inline supply folds, so file ≡ inline ≡ bare hold.
            out[b.name] = (
                normalize_binding_value(
                    fields, b.resolved, owner=f"bindings.{b.name}", composition_ref=where,
                    section_path=f"bindings.{b.name}",
                ) if fields is not None else b.resolved
            )
        elif isinstance(b, InlineBindingValue):
            # Normalize the raw supply to its bare form (single-field) BEFORE canonicalizing —
            # the supply-side counterpart of the wiring-sugar desugar (hash-neutral).
            value = normalize_binding_value(
                fields, b.value, owner=f"bindings.{b.name}", composition_ref=where,
                section_path=f"bindings.{b.name}",
            ) if fields is not None else b.value
            out[b.name] = _canon_or_malformed(
                value, where=where, section_path=f"bindings.{b.name}",
                what=f"bindings.{b.name}'s supplied value",
            )
        else:  # pragma: no cover - NodeBindingValue is a closed union
            raise TypeError(f"unhandled node binding value {type(b).__name__!r}")
    # Fold the declared default as the effective value for each default-bearing binding the
    # node OMITTED (a supplied value already covers the supplied ones above).
    supplied_names = {b.name for b in bindings}
    for decl_binding in declared:
        body = decl_binding.body
        if (
            isinstance(body, SchemaBinding)
            and body.has_default
            and decl_binding.name not in supplied_names
        ):
            # Mirror the handler-declaration side's fail-loud wrap (_canon_binding_decl_body):
            # a non-canonicalizable omitted default raises the structured MALFORMED_DECLARATION
            # rather than letting canon_value's bare TypeError escape (fail-loud parity). The
            # single-field default normalizes to its bare form, in step with the supplied fold.
            try:
                out[decl_binding.name] = canon_value(
                    normalize_binding_value(
                        body.fields, body.default,
                        owner=f"bindings.{decl_binding.name}", composition_ref=where,
                        section_path=f"bindings.{decl_binding.name}.default",
                    )
                )
            except TypeError as exc:
                raise ContractViolation(
                    check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
                    expected=f"bindings.{decl_binding.name}.default is a canonicalizable value",
                    actual=f"non-canonicalizable default value at {where} ({exc})",
                    remediation_hint="a ship-time default must be a JSON-native scalar/object the hasher can serialize",
                    composition_ref=where, section_path=f"bindings.{decl_binding.name}",
                ) from exc
    return out


# ---------------------------------------------------------------------------
# Handler declaration content (folded into the pipeline-hash by qualified-name)
# ---------------------------------------------------------------------------


def _canon_binding_decl_body(body: Any, *, where: str, binding_name: str) -> dict[str, Any]:
    """A handler-declared ``bindings.<name>`` body — a declared schema (fields + delivery
    selector) or a ``compile`` directive (a named compiler + its params).

    A compile directive folds its declared content (the compiler name + its params) — a composition
    change (hash-model.md § What the pipeline-hash absorbs, the compile-directive bullet). A param
    supplied **inline** folds its canonicalized value in the ``params`` sub-map; a param supplied
    **from a file** (``<param> = { file = "..." }``) folds the file's **raw text** (read + stamped by
    ``resolve_compile_param_files``) in the **disjoint** ``file_supplied`` sub-map — so a param's
    inline value and its file-supplied text are **distinct declarations** (different hashes), and a
    content edit to the file shifts the hash. (This is the deliberate divergence from the
    binding-value ``{ file }``, which canonicalizes → hash-neutral: the engine parses a binding value
    but never compiler content — §8 ownership.)

    **The disjoint-keyspace invariant (the seal).** ``canon_value`` only ever canonicalizes an author
    param *value*, and every such value lands **inside** ``params`` (one level down, keyed by param
    name). The ``file_supplied`` map is a **sibling key of the binding-body dict the hasher itself
    emits** — no ``canon_value`` output can reach the binding-body level to populate it. Therefore no
    inline declaration can ever produce a binding body with a non-empty ``file_supplied`` map: a
    file-supplied param occupies a keyspace ``canon_value``'s output domain cannot emit, *by
    construction*, not by an author avoiding a reserved key (a ``Structural over disciplinary`` /
    make-the-bad-state-unrepresentable seal). The earlier fold placed the text at
    ``params[name] = {"file_supplied_text": text}`` — an ordinary string-keyed dict in
    author-canonicalizable space that an inline ``<param> = { file_supplied_text = "<text>" }`` table
    reproduced byte-for-byte (a silent pipeline-hash collision); the sub-map split removes the shared
    keyspace. (Adversary: ``test_file_supplied_param_cannot_collide_with_a_colliding_inline_wrapper``.)

    An un-canonicalizable inline param **raises ``ContractViolation``** rather than letting
    ``canon_value``'s bare ``TypeError`` escape; an *unresolved* file param raises the external-file
    backstop (the hasher never reads files or hashes a path)."""
    if isinstance(body, SchemaBinding):
        out: dict[str, Any] = {
            "form": "schema", "fields": canon_schema(body.fields), "delivery": body.delivery.value,
        }
        # The declared ship-time default folds into the handler-declaration content hash (a
        # second, distinct contribution from the effective value at the supply site) — changing
        # a shipped default is a handler-declaration change that shifts the pipeline-hash of
        # every composition resolving the handler (hash-model.md § What the pipeline-hash
        # absorbs, Handler-content bullet). Absent default → the key is omitted (NO_DEFAULT is
        # not a value); present-but-None is a real declared default and folds as `null`.
        if body.has_default:
            # The declared default folds in its normalized (bare) form for a single-field
            # binding, in step with the effective-value fold above — one canonical
            # representation across all three fold sites (the differing spellings of one
            # logical default fold to one hash).
            try:
                out["default"] = canon_value(normalize_binding_value(
                    body.fields, body.default, owner=f"bindings.{binding_name}",
                    composition_ref=where, section_path=f"bindings.{binding_name}.default",
                ))
            except TypeError as exc:
                raise ContractViolation(
                    check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
                    expected=f"bindings.{binding_name}.default is a canonicalizable value",
                    actual=f"non-canonicalizable default value at {where} ({exc})",
                    remediation_hint="a ship-time default must be a JSON-native scalar/object the hasher can serialize",
                    composition_ref=where, section_path=f"bindings.{binding_name}",
                ) from exc
        return out
    # Inline params and file-supplied params fold into DISJOINT sub-maps (the disjoint-keyspace
    # seal — see the docstring's invariant): `canon_value` writes only into `params`; the file
    # branch writes only into `file_supplied`, a binding-body-level sibling no inline value can reach.
    params: dict[str, Any] = {}
    file_supplied: dict[str, Any] = {}
    for name, value in body.params.items():
        if isinstance(value, FilePathBindingValue):
            if value.content_hash is None:
                raise ContractViolation(
                    check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
                    expected="a resolved file-supplied compile parameter "
                             "(resolve_compile_param_files stamps its text)",
                    actual=f"unresolved external file '{value.path}' for compile parameter "
                           f"'{name}' at {where}",
                    remediation_hint="run the compile-parameter resolution pass before hashing; the "
                                     "hasher never reads files or hashes a path",
                    composition_ref=where, section_path=f"bindings.{binding_name}.{name}",
                )
            # The file's RAW TEXT folds in the disjoint `file_supplied` sub-map — hash-distinct from
            # inline by KEYSPACE (unreachable by `canon_value`), not by a reserved key.
            file_supplied[name] = value.resolved
        else:
            try:
                params[name] = canon_value(value)
            except TypeError as exc:
                raise ContractViolation(
                    check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
                    expected=f"compile-directive bindings.{binding_name}.params is a canonicalizable value",
                    actual=f"non-canonicalizable params value at {where} ({exc})",
                    remediation_hint="a compile-directive param must be a JSON-native scalar/object the hasher can serialize",
                    composition_ref=where, section_path=f"bindings.{binding_name}",
                ) from exc
    out = {"form": "compile", "compiler": body.compiler, "params": params}
    # Emit `file_supplied` ONLY when non-empty: a directive with no file-supplied param folds
    # byte-identically to before this surface existed (no golden re-baseline), and a non-empty
    # `file_supplied` map is itself the structural marker no inline declaration can produce.
    if file_supplied:
        out["file_supplied"] = file_supplied
    return out


def _canon_handler_decl(decl: HandlerDeclaration, *, where: str) -> dict[str, Any]:
    """Resolved handler declaration content folded into the pipeline-hash (``hash-model.md``:
    each referenced handler's ``output_schema`` / ``bindings`` schemas / ``service_bindings``
    declarations / validator configs fold in via qualified-name). ``annotations`` excluded
    (metadata-class). Hooks never reach here — they are skipped at the node level."""
    bindings = {b.name: _canon_binding_decl_body(b.body, where=where, binding_name=b.name) for b in decl.bindings}
    if isinstance(decl, TransformDeclaration):
        return {
            "kind": "transform", "reads": canon_schema(decl.reads),
            "output_schema": canon_schema(decl.output_schema), "bindings": bindings,
        }
    if isinstance(decl, ServiceDeclaration):
        return {
            "kind": "service", "reads": canon_schema(decl.reads),
            "output_schema": canon_schema(decl.output_schema),
            "service_bindings": {sb.name: canon_service_binding_decl(sb) for sb in decl.service_bindings},
            "bindings": bindings,
        }
    raise TypeError(  # fail loud — a hook must be filtered before this, any other kind is unknown
        f"_canon_handler_decl: unhashable handler kind {type(decl).__name__!r} "
        "(hooks are excluded upstream; only transform/service fold into the pipeline-hash)"
    )


# ---------------------------------------------------------------------------
# Training-bundle-hash — the trainable composition in isolation (unscoped)
# ---------------------------------------------------------------------------


# guarantees: preprocessor-mirrors-outer-node
def _canon_resolved_handler_node(
    name: str, bindings: tuple[NodeBindingValue, ...], reads_map: Mapping[str, str],
    writes_map: Mapping[str, str], registry: DeclarationRegistry, *, where: str,
) -> dict[str, Any] | None:
    """The single canonical fold for a name-referenced handler node — shared by an outer
    pipeline ``HandlerNode`` and a composition ``[[preprocessors]]`` entry (the mirror-pipeline
    principle: one grammar, one hash treatment across both layers; the structural guard against
    a divergent preprocessor fold). Resolves the referenced declaration (fail-loud — the hasher
    runs over a compile-validated set), returns ``None`` for a hook (hooks contribute to neither
    hash), and folds the normalized wiring maps over the **declaration's** ports, the EFFECTIVE
    supplied binding values (supplied-or-default over the declared bindings — so a preprocessor's
    ``delivery`` / ``default`` / validation fold exactly as an outer node's), and the resolved
    declaration content.
    """
    decl = registry.get_handler(name)
    if decl is None:
        raise ContractViolation(  # fail loud — the hasher runs over a validated pipeline
            check=Check.HANDLER_NAME_RESOLUTION, rule_id="R-pipeline-001",
            expected=f"node name '{name}' resolves to a handler declaration",
            actual="no such handler declaration (hasher requires a compile-validated pipeline)",
            composition_ref=where,
        )
    if isinstance(decl, HookDeclaration):
        return None  # hooks contribute to neither hash
    output_ports = [f.name for f in decl.output_schema]
    return {
        "name": name,  # qualified-name reference (absorbed)
        "reads_map": desugar_map(reads_map, [f.name for f in decl.reads]),
        "writes_map": desugar_map(writes_map, output_ports),
        # Effective values (supplied-or-default) — pass the handler's declared bindings so an
        # omitted default-bearing binding folds its declared default at the supply site.
        "bindings": canon_supplied_bindings(bindings, where=where, declared=decl.bindings),
        "handler": _canon_handler_decl(decl, where=where),
    }


def _canon_preprocessor(p: PreprocessorEntry, registry: DeclarationRegistry) -> dict[str, Any]:
    """One non-hook ``[[preprocessors]]`` entry for the TBH: the composition-layer entry head
    (``kind`` + the composition-local ``id``) over the shared resolved-handler-node fold
    (``name`` + desugared maps + effective binding values + the resolved declaration content).
    The referenced handler owns the ports and the binding declarations — the entry inlines none
    of it (the structural guard). The maps are **unscoped** (the author's internal channel
    names; the TBH hashes the composition in isolation). Hooks are filtered by the caller, so
    the core is never ``None`` here. Order in the sequence is preserved by the caller (semantic).
    """
    core = _canon_resolved_handler_node(
        p.name, p.bindings, p.reads_map, p.writes_map, registry, where=f"preprocessors.{p.id}",
    )
    assert core is not None  # the caller filters hooks (_is_hook_preprocessor) before this fold
    return {"kind": p.kind, "id": p.id, **core}


def _comp_non_hook_referenced(
    comp, backend_names: set[str], registry: DeclarationRegistry
) -> set[str]:
    """The composition's supply names referenced by the **non-hook** graph (D6): the backend
    (the trainable node's binding) plus every non-hook preprocessor's service bindings, resolved
    from the **referenced handler declaration** (the name-reference model). A supply referenced
    only by a hook preprocessor is invisible to the TBH."""
    referenced = set(backend_names)
    for p in comp.preprocessors:
        if _is_hook_preprocessor(p, registry):
            continue
        decl = registry.get_handler(p.name)
        if decl is None:
            # Mirror-fix (the mirror-pipeline principle): the pipeline-layer twin of this
            # scan raises the structured resolution violation; a silent () here would
            # narrow the TBH's folded supply domain with no error if a fold reorder ever
            # exposed this arm — the identity-laundering class the twin's guard prevents.
            raise ContractViolation(
                check=Check.HANDLER_NAME_RESOLUTION, rule_id="R-pipeline-001",
                expected=f"preprocessor name '{p.name}' resolves to a handler declaration",
                actual="no such handler declaration (hasher requires a compile-validated "
                       "declaration set)",
                composition_ref=comp.meta.name,
                section_path=f"preprocessors.{p.id}",
            )
        for sb in getattr(decl, "service_bindings", ()):
            referenced.add(sb.name)
    return referenced


def _is_hook_preprocessor(p: PreprocessorEntry, registry: DeclarationRegistry) -> bool:
    """A preprocessor that references a hook handler is a hook (it writes no channels) — resolved
    from the referenced declaration, exactly as the compiler classifies it. Hooks contribute to
    neither hash, so they are dropped from the TBH."""
    return isinstance(registry.get_handler(p.name), HookDeclaration)


def _resolve_supply_service_type(
    supply: ServiceBindingSupply, registry: DeclarationRegistry, where: str
) -> ServiceTypeDeclaration:
    """Resolve a supply entry's bound service-type for the effective-config fold — the
    hasher runs over a compile-validated declaration set, so an unresolvable type is the
    same fail-loud class as an unresolvable handler reference."""
    st = registry.get_service_type(supply.type)
    if st is None:
        raise ContractViolation(
            check=Check.SERVICE_TYPE_RESOLUTION, rule_id="R-service-type-004",
            expected=f"service-type '{supply.type}' resolves to a declaration in the registry",
            actual="no such service-type declaration (hasher requires a compile-validated declaration set)",
            composition_ref=where, section_path=f"service_bindings.{supply.name}",
        )
    return st


def _canon_supply_with_config(
    supply: ServiceBindingSupply, registry: DeclarationRegistry, where: str
) -> dict[str, Any]:
    """One config-supply-site entry: identity + the config block's **effective** values
    (supplied-or-default, via the shared compose derivation — hash treatment is
    supply-site; ``service-type/reference.md`` § Hash placement)."""
    st = _resolve_supply_service_type(supply, registry, where)
    try:
        return canon_service_supply(
            supply,
            config=effective_config(
                supply.config, st, composition_ref=where,
                section_path=f"service_bindings.{supply.name}.config",
            ),
        )
    except (TypeError, ValueError) as exc:
        # Fail-loud parity with the binding folds: a non-canonicalizable identity or
        # config value (direct-Pydantic dialect) stays inside the closed channel. An
        # effective_config ContractViolation is not TypeError/ValueError and propagates
        # untouched above.
        raise ContractViolation(
            check=Check.MALFORMED_DECLARATION, rule_id="R-pipeline-001",
            expected=f"service_bindings.{supply.name}'s identity/config values are "
                     "canonicalizable JSON-native values",
            actual=f"non-canonicalizable value at {where} ({exc})",
            remediation_hint="supply JSON-native scalars/objects (finite numbers only)",
            composition_ref=where, section_path=f"service_bindings.{supply.name}",
        ) from exc


def _canon_trainable_composition(
    comp: TrainableComposition, registry: DeclarationRegistry
) -> dict[str, Any]:
    """The canonical structure the training-bundle-hash covers: ``meta.kind`` (structural;
    ``name`` excluded by the family rule, and ``[meta]`` carries no ``description``), the
    boundary ``inputs`` / ``outputs`` port
    schemas, the ordered non-hook ``[[preprocessors]]`` sequence, the composition's
    ``service_bindings`` identity supply (folded in — the composition supplies its own
    backend/service identity, mirroring the pipeline; ``hash-model.md`` § the mirror-pipeline
    principle + ``handler/reference.md`` composition ``service_bindings`` supply), the terminal
    trainable node's port shapes + ``config`` + ``service_bindings`` (NO wiring maps — the
    node's own maps are excluded), and the internal ``merge``. ``annotations`` excluded.

    ``streamable`` is EXCLUDED — it is a delivery selector, not training-record shape, the same
    class as the unhashed deployment ``transport.*`` values (``hash-model.md``
    § Training-bundle-hash).

    Config folds are **effective values** (supplied-or-default, the shared compose
    derivation — ``service-type/reference.md`` § Hash placement: "the **effective**
    config *values* … fold into that trainable's training-bundle-hash"), which is what
    needs the ``registry``: a declared ship-time default lives on the bound service-type's
    ``[config_schema]``. The trainable backend's own supply entry folds NO config key (its
    config supply site is ``[trainable.config]``, folded under ``trainable.config`` —
    compose rejects a config block on the backend's supply entry); a preprocessor-declared
    binding's supply entry folds its effective config with its identity."""
    t = comp.trainable
    backend_names = {sb.name for sb in t.service_bindings}
    # The backend's declared service-type (exactly one binding — compose-checked); the
    # effective [trainable.config] fold validates against ITS [config_schema]. Resolved
    # from the declared binding reference, mirroring the compose check.
    # A trainable composition MUST declare exactly one backend binding (R-handler-008);
    # the hasher runs over a compile-validated declaration set, so a missing backend is the
    # sibling guards' fail-loud class — never a silent raw-config fold (graceful-degrade =
    # training-data corruption). No raw-config else-arm: the effective fold below is
    # unconditional, with the bound service-type's [config_schema] in hand.
    if not t.service_bindings:
        raise ContractViolation(
            check=Check.SERVICE_BINDING_CARDINALITY, rule_id="R-handler-008",
            expected="a trainable composition declares exactly one service-typed binding "
                     "(the backend) — its [config_schema] validates the effective config fold",
            actual="no service-typed binding on the trainable node (hasher requires a "
                   "compile-validated declaration set)",
            composition_ref=comp.meta.name, section_path="trainable.service_bindings",
        )
    backend_type = registry.get_service_type(t.service_bindings[0].type)
    if backend_type is None:
        raise ContractViolation(
            check=Check.SERVICE_TYPE_RESOLUTION, rule_id="R-service-type-004",
            expected=f"the trainable backend service-type '{t.service_bindings[0].type}' "
                     "resolves to a declaration in the registry",
            actual="no such service-type declaration (hasher requires a compile-validated declaration set)",
            composition_ref=comp.meta.name, section_path="trainable.service_bindings",
        )
    return {
        "meta_kind": comp.meta.kind.value,  # meta.name excluded (family rule); [meta] has no description
        "inputs": canon_schema(comp.inputs),
        "outputs": canon_schema(comp.outputs),
        "preprocessors": [  # ORDERED — sequence order is semantic (dispatch order)
            _canon_preprocessor(p, registry)
            for p in comp.preprocessors if not _is_hook_preprocessor(p, registry)
        ],
        # The composition's own service-binding identity supply (self-contained, mirroring the
        # pipeline's `service_bindings.<name>`): a composition backend's supplied identity folds
        # into the training-bundle-hash; transport stays deployment-supplied (excluded). D6 —
        # the same affirmative non-hook domain: a supply entry folds iff the backend (the
        # trainable node) or a NON-HOOK preprocessor references it; a binding referenced only by
        # a hook preprocessor is invisible (preprocessor hooks excluded the same affirmative way).
        "service_bindings": {
            s.name: (
                canon_service_supply(s)  # the backend's config site is [trainable.config]
                if s.name in backend_names
                else _canon_supply_with_config(s, registry, comp.meta.name)
            )
            for s in comp.service_bindings
            if s.name in _comp_non_hook_referenced(comp, backend_names, registry)
        },
        "trainable": {
            # `streamable` excluded — delivery selector, not training-record shape.
            # EFFECTIVE generation-parameter values (supplied-or-default), through the
            # structured fail-loud wrap (parity with every sibling fold).
            "config": _canon_or_malformed(
                effective_config(
                    t.config, backend_type,
                    composition_ref=comp.meta.name, section_path="trainable.config",
                ),
                where=comp.meta.name, section_path="trainable.config",
                what="trainable.config's effective values",
            ),
            "service_bindings": {sb.name: canon_service_binding_decl(sb) for sb in t.service_bindings},
            "reads": canon_schema(t.reads),
            # ORDERED — the P9 order-semantic ruling (2026-06-10; hash-model.md
            # § Training-bundle-hash): the bound wire form compiles the declared field
            # order into the enforced emission order, so the fold preserves entry order
            # for a trainable's output schema — a reorder is honestly a new
            # training-bundle-hash. Non-trainable schemas and the read side stay
            # name-keyed.
            "output_schema": canon_schema_ordered(t.output_schema),
        },
        "merge": {channel: strategy.value for channel, strategy in comp.merge.items()},  # unscoped
    }


def training_bundle_hash(
    composition: TrainableComposition, registry: DeclarationRegistry
) -> str:
    """The training-bundle-hash for one trainable composition: ``sha256`` over the canonical
    serialization of the composition's structural membership, in isolation. Pure + deterministic;
    one per engine-owned-dispatch composition node. ``registry`` resolves the bound
    service-types for the **effective** config folds (supplied-or-default — a declared
    ship-time default lives on the service-type's ``[config_schema]``); the service-type
    declarations themselves stay excluded from the fold. Renaming the composition
    (``meta.name``) or its annotations is hash-neutral; editing any port shape /
    preprocessor / config / internal wiring shifts it. The load-bearing placement
    condition — outer-pipeline wiring never reaches the TBH — is structural: this
    signature takes no pipeline, so no outer edit has a path in (no test needed)."""
    return sha256_of(_canon_trainable_composition(composition, registry))


# ---------------------------------------------------------------------------
# Pipeline-hash — the outer declaration + embedded TBHs by reference
# ---------------------------------------------------------------------------


def _canon_pipeline_node(
    node: Any, registry: DeclarationRegistry, pipeline_name: str,
    embed_stack: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Canonicalize one outer pipeline node, or ``None`` if it is a **hook** (excluded from the
    hash). A handler node folds its qualified-name reference, normalized maps, inline binding
    values, and resolved declaration content; a composition node folds its own identity hash
    **by reference** (the embedded internal scope is opaque) — the training-bundle-hash for a
    trainable, the pipeline-hash for a nested ``pipeline`` composition (one own-hash-domain
    mechanism, applied at whichever layer the embed sits; ``hash-model.md`` § What the
    pipeline-hash absorbs). The two kinds fold under DISTINCT keys, so ``meta.kind`` — a
    structural discriminator — is absorbed by construction: flipping a composition's kind
    changes the fold shape, hence the hash."""
    if isinstance(node, HandlerNode):
        core = _canon_resolved_handler_node(
            node.name, node.bindings, node.reads_map, node.writes_map, registry, where=pipeline_name,
        )
        if core is None:
            return None  # hook — contributes to neither hash
        return {"node": "handler", **core}
    if isinstance(node, CompositionNode):
        comp = registry.get_composition(node.name)
        if comp is None:
            raise ContractViolation(  # fail loud — validated pipeline expected
                check=Check.HANDLER_NAME_RESOLUTION, rule_id="R-pipeline-001",
                expected=f"composition path '{node.name}' resolves to a composition declaration",
                actual="no such composition declaration (hasher requires a compile-validated pipeline)",
                composition_ref=pipeline_name,
            )
        if isinstance(comp, PipelineComposition):
            # Structural backstop, sibling of the unresolved-external-file guard: the hasher
            # runs over a compile-validated (acyclic) pipeline, so a cycle reaching it is
            # registry drift — fail loud with the same structured contract compose enforces,
            # never an unbounded recursion.
            if node.name in embed_stack:
                raise ContractViolation(
                    check=Check.COMPOSITION_CYCLE, rule_id="R-pipeline-001",
                    expected="the nested-pipeline embed graph is acyclic (hasher requires a "
                             "compile-validated pipeline)",
                    actual="embed cycle: " + " -> ".join((*embed_stack, node.name)),
                    composition_ref=pipeline_name,
                )
            # The nested pipeline's OWN canonicalized hash — its pipeline-hash — flows up by
            # reference (recursive by construction for deeper nesting); its internal scope is
            # opaque and its declaration path is a loading detail, not composition identity.
            return {
                "node": "composition",
                "pipeline_hash": pipeline_hash(
                    comp.pipeline, registry, embed_stack=(*embed_stack, node.name)
                ),
            }
        # Own-hash-domain allowlist (structural, fail-closed). Past the nested-`pipeline` arm
        # above, the ONLY composition kind that folds by-reference here is a trainable — its own
        # canonicalized hash IS its training-bundle-hash (hash-model.md § What the pipeline-hash
        # absorbs). A pure-substitution bundle has NO own hash domain: it is textually substituted
        # into the outer pipeline BEFORE hashing, never folded by reference. So a bundle (or any
        # future non-own-hash-domain kind) reaching this fold would be silently mis-hashed — a
        # training-contract break. A bundle is substituted out at every walker's entry
        # (conjured.ir.substitute — pipeline_hash substitutes above), so reaching here means a
        # walk forgot to substitute: fail loud with the structured contract (the sibling of the
        # cycle / unresolved-file backstops above), never a silent by-reference fold (a graceful
        # degrade here is training-data corruption).
        # Fail-closed: a new own-hash-domain kind MUST be added to this allowlist explicitly.
        # guarantees: tbh-fold-own-hash-domain-only
        if not isinstance(comp, TrainableComposition):
            reached = getattr(getattr(comp, "meta", None), "kind", None)
            kind_label = reached.value if isinstance(reached, CompositionKind) else type(comp).__name__
            raise ContractViolation(
                check=Check.BUNDLE_REACHES_BYREF_FOLD, rule_id="R-pipeline-001",
                expected="the composition folding by-reference into the pipeline-hash is an "
                         "own-hash-domain kind (a trainable composition — the nested `pipeline` kind "
                         "folds in its own arm); a pure-substitution bundle has no own hash domain and "
                         "folds by textual substitution BEFORE hashing, never by reference",
                actual=f"a non-own-hash-domain composition (kind {kind_label!r}) reached the hasher's "
                       f"by-reference fold at node '{node.name}' — a bundle is substituted out at every "
                       "walker's entry, so this walk forgot to substitute (engine drift)",
                remediation_hint="a bundle composition is substituted textually into the outer pipeline "
                                 "before hashing (hash-model.md § What the pipeline-hash absorbs); the "
                                 "hasher requires a compile-validated pipeline whose every "
                                 "by-reference-folding composition is own-hash-domain",
                composition_ref=pipeline_name, section_path=f"nodes.{node.name}",
            )
        # Only the embedded trainable composition's identity hash flows up — its internal scope is
        # opaque, and its declaration path is a loading detail, not composition identity (the TBH is).
        return {"node": "composition", "training_bundle_hash": training_bundle_hash(comp, registry)}
    raise TypeError(f"unhandled pipeline node {type(node).__name__!r}")  # pragma: no cover


def non_hook_referenced_supplies(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry
) -> set[str]:
    """The set of pipeline-level ``service_bindings.<name>`` names referenced by a **non-hook**
    node — the affirmative hash domain (D6; hash-model.md § What the pipeline-hash absorbs). A
    node references a supply by declaring a ``service_bindings`` binding of that name; a hook's
    references do not count (the hasher never reads a hook's declaration). A composition node
    supplies its own bindings internally (folded in its TBH), so it references no pipeline-level
    supply. Registry resolution is fail-loud (the hasher runs over a compile-validated
    pipeline).

    **Public shared surface — two consumers:** the pipeline-hash's supply fold
    (:func:`pipeline_hash`), and the derivables bundle's supply snapshot
    (``conjured.derivables._binding_snapshot``), which scopes to the SAME non-hook domain so
    the snapshot and the hash cannot diverge. A semantics change here is a change to both."""
    referenced: set[str] = set()
    for node in pipeline.nodes:
        if not isinstance(node, HandlerNode):
            continue
        decl = registry.get_handler(node.name)
        if decl is None:
            raise ContractViolation(
                check=Check.HANDLER_NAME_RESOLUTION, rule_id="R-pipeline-001",
                expected=f"node name '{node.name}' resolves to a handler declaration",
                actual="no such handler declaration (hasher requires a compile-validated pipeline)",
                composition_ref=pipeline.meta.name,
            )
        if isinstance(decl, HookDeclaration):
            continue  # a hook's binding references are invisible to the hash
        for sb in getattr(decl, "service_bindings", ()):
            referenced.add(sb.name)
    return referenced


def pipeline_hash(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry,
    *, embed_stack: tuple[str, ...] = (),
) -> str:
    """The pipeline-hash: ``sha256`` over the canonical serialization of the whole pipeline
    declaration minus the closed exclusion set, with embedded own-hash-domain compositions
    folded in by reference — a trainable's training-bundle-hash; a nested ``pipeline``
    composition's own pipeline-hash (recursive). Pure function over the declaration IR + the
    registry it resolves handler/composition references against (no ``CompiledGraph``, no
    dispatch). ``embed_stack`` is the engine's recursive nested-embed context (a structural
    cycle backstop — the hasher runs over a compile-validated, acyclic pipeline); callers
    other than the hasher's own recursion leave it defaulted.

    ``meta`` contributes nothing (a pipeline's ``meta`` is just ``name`` — identity, excluded
    by the family rule; the block declares no ``description``), so renaming a pipeline is
    hash-neutral. Hook nodes are skipped (they contribute to neither hash)."""
    # Pure-substitution embeds resolve FIRST (glossary § Bundle TOML): a bundle's nodes
    # fold into this hash INLINE, exactly like directly-declared content — substituted
    # before anything below reads the node sequence (idempotent when the caller already
    # substituted; the by-reference guard below stays the structural backstop).
    substituted = substitute_bundle_nodes(
        pipeline.nodes, registry.get_composition, where=pipeline.meta.name,
    )
    if substituted is not pipeline.nodes:
        pipeline = pipeline.model_copy(update={"nodes": substituted})
    nodes = [
        canon for node in pipeline.nodes
        if (canon := _canon_pipeline_node(node, registry, pipeline.meta.name, embed_stack)) is not None
    ]
    # D6 — the supply table folds **affirmatively over the non-hook graph**: a pipeline-level
    # `service_bindings.<name>` entry folds iff a NON-HOOK node references it (declares a
    # binding by that name). A supply entry referenced only by hooks is invisible (the hasher
    # never reads a hook's declaration — the domain is defined by the non-hook graph, not by
    # subtracting hook entries); a binding shared with a non-hook consumer folds as ordinary
    # supply data (the non-hook reference puts it in the set). hash-model.md § What the
    # pipeline-hash absorbs.
    non_hook_referenced = non_hook_referenced_supplies(pipeline, registry)
    structure: dict[str, Any] = {
        "nodes": nodes,  # ORDERED (non-hook node order is absorbed); hooks already filtered
        "service_bindings": {
            s.name: _canon_supply_with_config(s, registry, pipeline.meta.name)
            for s in pipeline.service_bindings
            if s.name in non_hook_referenced
        },
        "merge": {channel: strategy.value for channel, strategy in pipeline.merge.items()},
        "inputs": canon_schema(pipeline.inputs),
        # outputs: absence (None) is categorically distinct from an empty-but-present block.
        "outputs": None if pipeline.outputs is None else canon_schema(pipeline.outputs),
    }
    return sha256_of(structure)
