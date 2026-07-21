"""Stage-1 external binding-value resolution — the I/O pass that resolves ``{ file = "..." }``
bindings before any hash or dispatch.

The hashes are **pure functions** over the declaration IR (no I/O). An external-file binding
(``FilePathBindingValue``) names a declaration file whose *canonicalized content* folds into the
pipeline-hash / training-bundle-hash (``architecture/hash-model.md`` § External binding-value
declaration content). The reconciliation is **here**: a dedicated resolution pass reads
each referenced file at compose, parses it to the binding's value shape, canonicalizes it to the
same canonical IR an inline value normalizes to, hashes that canonicalized content, and **stamps**
``content_hash`` + ``resolved`` onto a fresh (frozen) IR instance. The hasher then folds the
stamped canonicalized content (``resolved``) — the ``content_hash`` is a manifest/event-layer
convenience, not the fold; the hasher never touches the filesystem.

**All I/O lives here, at compose, never at dispatch.** A missing file, an unreadable file, or a
file that does not parse raises ``ContractViolation`` at this pass — fail loud, never a path
silently hashed or a dispatch-time surprise. Paths are resolved relative to the **directory of
the declaration TOML that supplied the binding**: the caller-passed ``base_dir`` anchors the
pipeline's own handler-node bindings (the pipeline TOML's directory), and a composition's
bindings anchor to the composition's OWN registered declaration path
(``DeclarationRegistry.composition_paths`` — never the outer pipeline's directory), so a relative
``{ file = "npcs/captain.toml" }`` resolves the same way the author wrote it next to the
declaration, a same-relative-named file under the outer directory is never silently read and
hashed in its place, and a composition shared by pipelines in different directories resolves
identically regardless of which composes first (compose-time determinism, I2). The no-anchor
contract is universal: a composition with an unresolved file binding but no registered
declaration path fails loud, a pipeline with an unresolved file binding but no ``base_dir``
fails loud, and ``resolve_compile_param_files`` enforces the same per handler — never a
silent resolve against the process CWD.

The pass is **idempotent over already-resolved instances** (a stamped ``content_hash`` is left as
is) and returns a NEW IR tree (the IR is frozen — resolution produces fresh instances). It is the
caller's job to feed the *resolved* IR to the hasher; the hasher's external-file guard raises on
any unresolved instance that slips through (a structural backstop).
"""

from __future__ import annotations

import os
import tomllib

from conjured.errors import Check, ContractViolation
from conjured.canonical import canon_value, sha256_of
from conjured.ir.common import (
    Binding,
    CompileBinding,
    FilePathBindingValue,
    InlineBindingValue,
    NodeBindingValue,
)
from conjured.ir.composition import (
    BundleComposition,
    PipelineComposition,
    PreprocessorEntry,
    TrainableComposition,
)
from conjured.ir.handler import HandlerDeclaration
from conjured.ir.pipeline import (
    CompositionNode,
    HandlerNode,
    PipelineDeclaration,
    PipelineNode,
)
from conjured.validator.registry import DeclarationRegistry


def _read_external_file(
    full_path: str, *, descriptor: str, where: str, section_path: str
) -> bytes:
    """THE shared external-file read seam — the open + read-bytes step both external-file branches
    (binding values AND compile-directive params) share, with its fail-loud ``OSError ->
    ContractViolation`` translation. The CALLER owns the one thing that differs after the read: a
    binding value parses + canonicalizes the bytes (hash-neutral); a compile param keeps them as raw
    text (hash-distinct). I/O at compose, never at dispatch — a missing/unreadable file fails loud
    here, never a path silently hashed."""
    try:
        with open(full_path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        raise ContractViolation(
            check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
            expected=f"the external file for {descriptor} is readable at '{full_path}'",
            actual=f"could not read the file ({type(exc).__name__}: {exc})",
            remediation_hint="fix the file path (resolved relative to the declaration's directory), "
                             "or supply the value inline",
            composition_ref=where, section_path=section_path,
        ) from exc


def _resolve_one(value: NodeBindingValue, base_dir: str, where: str) -> NodeBindingValue:
    """Resolve a single binding value. Inline values pass through; an external-file value is
    read + parsed + canonicalized + hashed and returned as a stamped instance."""
    if not isinstance(value, FilePathBindingValue):
        return value
    if value.content_hash is not None:
        return value  # already resolved — idempotent
    full_path = os.path.join(base_dir, value.path) if base_dir else value.path
    raw = _read_external_file(
        full_path, descriptor=f"binding '{value.name}'", where=where,
        section_path=f"bindings.{value.name}",
    )
    # The binding-value branch of the one external-file divergence: parse the bytes to the binding's
    # value shape and canonicalize, so "inline X" and "a file containing X" produce the SAME content
    # hash (path-neutral). (The compile-param branch keeps raw text instead — see
    # ``_resolve_compile_param`` below; that decode-vs-parse line is the only difference.)
    try:
        content = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ContractViolation(
            check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
            expected=f"the external declaration file for binding '{value.name}' is a valid TOML declaration",
            actual=f"parse error at '{full_path}' ({type(exc).__name__}: {exc})",
            composition_ref=where, section_path=f"bindings.{value.name}",
        ) from exc
    try:
        canonical = canon_value(content)
    except TypeError as exc:
        raise ContractViolation(
            check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
            expected=f"the external declaration file for binding '{value.name}' canonicalizes to a JSON-native value",
            actual=f"non-canonicalizable content at '{full_path}' ({exc})",
            composition_ref=where, section_path=f"bindings.{value.name}",
        ) from exc
    return value.model_copy(update={"content_hash": sha256_of(canonical), "resolved": canonical})


def _resolve_values(
    values: tuple[NodeBindingValue, ...], base_dir: str, where: str
) -> tuple[NodeBindingValue, ...]:
    return tuple(_resolve_one(v, base_dir, where) for v in values)


def _has_unresolved_file_binding(values: tuple[NodeBindingValue, ...]) -> bool:
    return any(
        isinstance(v, FilePathBindingValue) and v.content_hash is None for v in values
    )


def _composition_anchor_dir(
    registry: DeclarationRegistry, comp_key: str, *, needs_anchor: bool, pipeline_name: str
) -> str | None:
    """The directory a composition's own ``{ file = "..." }`` bindings anchor to — the
    composition's registered declaration path's directory (the binding path is written
    next to the composition TOML, so it resolves from there, never from the outer
    pipeline's directory). A composition that carries an unresolved file binding but no
    registered path fails loud (mirrors ``resolve_compile_param_files``' per-handler
    no-anchor contract); with no file binding to resolve, no anchor is needed."""
    toml_path = registry.get_composition_path(comp_key)
    if toml_path is not None:
        return os.path.dirname(toml_path)
    if needs_anchor:
        raise ContractViolation(
            check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
            expected=f"composition '{comp_key}' has a registered declaration path (its "
                     "directory anchors the composition's relative { file } binding "
                     "paths)",
            actual="no declaration path registered for the composition "
                   "(DeclarationRegistry.add_composition toml_path=)",
            remediation_hint="register the composition with toml_path=, or supply the "
                             "binding value inline",
            composition_ref=pipeline_name,
        )
    return None


def resolve_composition_bindings(comp: TrainableComposition, *, base_dir: str) -> TrainableComposition:
    """Return ``comp`` with every external-file preprocessor binding resolved + stamped.
    ``base_dir`` is the COMPOSITION's own declaration directory (the caller anchors it
    via the registry's composition-path map), never the embedding pipeline's."""
    new_preprocessors = []
    changed = False
    for pp in comp.preprocessors:
        resolved = _resolve_values(pp.bindings, base_dir, where=f"{comp.meta.name}.{pp.name}")
        if resolved != pp.bindings:
            changed = True
            new_preprocessors.append(pp.model_copy(update={"bindings": resolved}))
        else:
            new_preprocessors.append(pp)
    if not changed:
        return comp
    return comp.model_copy(update={"preprocessors": tuple(new_preprocessors)})


def resolve_pipeline_bindings(
    pipeline: PipelineDeclaration, registry: DeclarationRegistry, *, base_dir: str,
    embed_stack: tuple[str, ...] = (),
) -> PipelineDeclaration:
    """Return ``pipeline`` with every external-file handler-node binding resolved + stamped, and
    every embedded composition's file bindings resolved in the ``registry`` (the registry's
    composition entries are replaced with their resolved twins, since the hasher reads
    compositions from the registry). A nested ``pipeline`` composition resolves recursively
    through this same pass (one external-file mechanism across both layers).

    ``base_dir`` is the directory THIS pipeline's own handler-node binding file paths resolve
    relative to (the pipeline declaration's directory). An embedded composition's bindings
    anchor to the composition's OWN registered declaration directory
    (``registry.composition_paths``) — never to ``base_dir`` — so a same-relative-named file
    under the outer directory is never read in the composition file's place, and a composition
    shared by two pipelines in different directories resolves order-independently. Idempotent;
    returns fresh frozen IR (the IR is immutable). ``embed_stack``
    is the engine's recursive nested-embed context (the same convention ``compile_pipeline``
    threads): this pass has no precondition that compile ran first, so it carries its own
    cycle rejection — a self-embedding chain fails loud here rather than recursing forever."""
    new_nodes: list[PipelineNode] = []
    changed = False
    for node in pipeline.nodes:
        if isinstance(node, HandlerNode):
            # The pipeline arm of the no-anchor contract (the same rule
            # _composition_anchor_dir enforces for compositions): a pipeline-level
            # { file } binding with no known declaration directory MUST fail loud —
            # resolving against the process CWD would read (and hash) whatever
            # same-named file happens to sit there, the wrong-file-hashed outcome the
            # anchor rule forbids (handler/reference.md § Binding value-supply grammar:
            # "a supplying declaration whose on-disk location the engine does not know
            # MUST fail loud at resolution").
            # guarantees: pipeline-file-anchor-fails-loud
            if not base_dir and _has_unresolved_file_binding(node.bindings):
                raise ContractViolation(
                    check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
                    expected=f"pipeline '{pipeline.meta.name}' has a known declaration "
                             "directory (the anchor its relative { file } binding paths "
                             "resolve against)",
                    actual="no base_dir supplied for a pipeline carrying an unresolved "
                           "{ file } binding",
                    remediation_hint="resolve with base_dir=<the pipeline declaration's "
                                     "directory>, or supply the binding value inline",
                    composition_ref=pipeline.meta.name,
                )
            resolved = _resolve_values(node.bindings, base_dir, where=pipeline.meta.name)
            if resolved != node.bindings:
                changed = True
                new_nodes.append(node.model_copy(update={"bindings": resolved}))
            else:
                new_nodes.append(node)
        elif isinstance(node, CompositionNode):
            _resolve_composition_ref(
                node, registry, embed_stack=embed_stack, owner_name=pipeline.meta.name,
            )
            new_nodes.append(node)
        else:  # pragma: no cover - PipelineNode is a closed union
            new_nodes.append(node)
    if not changed:
        return pipeline
    return pipeline.model_copy(update={"nodes": tuple(new_nodes)})


def _resolve_composition_ref(
    node: CompositionNode, registry: DeclarationRegistry, *,
    embed_stack: tuple[str, ...], owner_name: str,
) -> None:
    """Resolve one composition reference's external-file bindings, re-registering the
    **resolved twin** (the hasher and the bundle-substitution step read compositions
    from the registry). One dispatch across the three realized composition kinds — the
    shared arm of the pipeline walk above and the bundle walk below (one external-file
    mechanism across every layer, never re-derived per kind)."""
    comp = registry.get_composition(node.name)
    if comp is None:
        # Fail loud — a CompositionNode whose path is registry-absent has no
        # declaration to resolve (its file-bindings have nowhere to land). This is a
        # standalone compose-time I/O pass with no precondition that compile ran first,
        # so a silent skip would defer the failure to a downstream pass that may never
        # run; the fail-loud home of this pass dereferences its own references. Same
        # structured violation the compile + hasher passes raise for this condition
        # (R-pipeline-001 node-name resolution).
        raise ContractViolation(
            check=Check.HANDLER_NAME_RESOLUTION, rule_id="R-pipeline-001",
            expected=f"composition path '{node.name}' resolves to a composition declaration",
            actual="no such composition declaration",
            remediation_hint="register the composition declaration, or fix the path",
            composition_ref=owner_name,
        )
    if isinstance(comp, PipelineComposition):
        # A nested `pipeline` composition's body IS the pipeline grammar — resolve it
        # through THIS same pass, recursively (the mirror-pipeline principle: one
        # external-file mechanism across both layers). A re-registered resolved twin
        # keeps the hasher reading the stamped IR from the registry. The embed_stack
        # guard makes a cyclic chain fail loud (the same COMPOSITION_CYCLE contract
        # compose enforces) instead of recursing forever — this pass may run first.
        if node.name in embed_stack:
            raise ContractViolation(
                check=Check.COMPOSITION_CYCLE, rule_id="R-pipeline-001",
                expected="the nested-pipeline embed graph is acyclic — a pipeline "
                         "never transitively embeds itself",
                actual="embed cycle: " + " -> ".join((*embed_stack, node.name)),
                composition_ref=owner_name,
                remediation_hint="break the cycle — a cyclic composition never loads",
            )
        # The inner pipeline's own handler-node bindings anchor to the
        # COMPOSITION's declaration directory (the author wrote the paths next
        # to the composition TOML) — never the outer pipeline's base_dir.
        # Nested compositions inside it anchor to their own registered paths
        # through the recursion, so the anchor needed HERE covers exactly the
        # inner pipeline's direct handler nodes.
        inner_anchor = _composition_anchor_dir(
            registry, node.name,
            needs_anchor=any(
                _has_unresolved_file_binding(n.bindings)
                for n in comp.pipeline.nodes if isinstance(n, HandlerNode)
            ),
            pipeline_name=owner_name,
        )
        resolved_inner = resolve_pipeline_bindings(
            comp.pipeline, registry, base_dir=inner_anchor or "",
            embed_stack=(*embed_stack, node.name),
        )
        if resolved_inner is not comp.pipeline:
            registry.add_composition(
                node.name, comp.model_copy(update={"pipeline": resolved_inner})
            )
    elif isinstance(comp, BundleComposition):
        # A bundle's nodes are the exact pipeline node-entry grammar — its handler-node
        # bindings anchor to the BUNDLE's own declaration directory (the author wrote
        # the { file } paths next to the bundle TOML), and its nested composition refs
        # recurse through this same dispatch. The resolved twin re-registers so the
        # substitution step (conjured.ir.substitute) splices STAMPED nodes wherever the
        # bundle embeds.
        if node.name in embed_stack:
            raise ContractViolation(
                check=Check.COMPOSITION_CYCLE, rule_id="R-pipeline-001",
                expected="the bundle embed graph is acyclic — a bundle never "
                         "transitively embeds itself",
                actual="embed cycle: " + " -> ".join((*embed_stack, node.name)),
                composition_ref=owner_name,
                remediation_hint="break the cycle — a cyclic composition never loads",
            )
        bundle_anchor = _composition_anchor_dir(
            registry, node.name,
            needs_anchor=any(
                _has_unresolved_file_binding(n.bindings)
                for n in comp.nodes if isinstance(n, HandlerNode)
            ),
            pipeline_name=owner_name,
        )
        bundle_nodes: list = []
        bundle_changed = False
        for n in comp.nodes:
            if isinstance(n, HandlerNode):
                resolved = _resolve_values(n.bindings, bundle_anchor or "", where=comp.meta.name)
                if resolved != n.bindings:
                    bundle_changed = True
                    bundle_nodes.append(n.model_copy(update={"bindings": resolved}))
                else:
                    bundle_nodes.append(n)
            elif isinstance(n, CompositionNode):
                _resolve_composition_ref(
                    n, registry,
                    embed_stack=(*embed_stack, node.name), owner_name=comp.meta.name,
                )
                bundle_nodes.append(n)
            else:  # pragma: no cover - PipelineNode is a closed union
                bundle_nodes.append(n)
        if bundle_changed:
            registry.add_composition(
                node.name, comp.model_copy(update={"nodes": tuple(bundle_nodes)})
            )
    else:
        # A trainable composition's preprocessor bindings anchor to ITS OWN
        # declaration directory — same rule, same fail-loud no-anchor contract.
        comp_anchor = _composition_anchor_dir(
            registry, node.name,
            needs_anchor=any(
                _has_unresolved_file_binding(pp.bindings)
                for pp in comp.preprocessors
            ),
            pipeline_name=owner_name,
        )
        resolved_comp = resolve_composition_bindings(
            comp, base_dir=comp_anchor or ""
        )
        if resolved_comp is not comp:
            registry.add_composition(node.name, resolved_comp)


# ---------------------------------------------------------------------------
# Compile-directive parameter files — the SAME `{ file }` external-file form, kept as RAW TEXT
# ---------------------------------------------------------------------------


def _resolve_compile_param(
    value: FilePathBindingValue, base_dir: str, *, where: str, binding_name: str
) -> FilePathBindingValue:
    """Resolve one file-supplied compile parameter — the compile-param branch of the single
    external-file divergence. Reuses :func:`_read_external_file` (the shared read + fail-loud seam),
    then keeps the bytes as **raw UTF-8 text** (it does NOT parse / canonicalize, the one line that
    differs from a binding value — the engine never parses compiler content, §8 ownership). Stamps
    ``resolved`` = the text and ``content_hash`` = its ``sha256`` so the hasher folds the text and a
    content edit shifts the pipeline-hash (handler/reference.md § The ``compile = "..."`` directive
    sub-form; hash-model.md § What the pipeline-hash absorbs). The diagnostic locus is
    ``bindings.<binding>.<param>`` — the param is a sibling key of ``compile`` under ``[bindings.
    <name>]`` (the same dotted locus the parse + hasher guards use; there is no ``[compile]`` table)."""
    if value.content_hash is not None:
        return value  # already resolved — idempotent
    section_path = f"bindings.{binding_name}.{value.name}"
    full_path = os.path.join(base_dir, value.path) if base_dir else value.path
    raw = _read_external_file(
        full_path, descriptor=f"compile parameter '{value.name}'", where=where,
        section_path=section_path,
    )
    try:
        text = raw.decode("utf-8")  # <-- THE branch point: raw text, never parsed/canonicalized
    except UnicodeDecodeError as exc:
        raise ContractViolation(
            check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
            expected=f"the external file for compile parameter '{value.name}' is valid UTF-8 text",
            actual=f"decode error at '{full_path}' ({type(exc).__name__}: {exc})",
            remediation_hint="a compile-parameter file is read as text (the compiler parses it); "
                             "save it as UTF-8",
            composition_ref=where, section_path=section_path,
        ) from exc
    return value.model_copy(update={"content_hash": sha256_of(text), "resolved": text})


def _resolve_compile_binding(
    body: CompileBinding, base_dir: str, *, where: str, binding_name: str
) -> CompileBinding:
    """Return ``body`` with every file-supplied parameter resolved + stamped (raw text); inline
    params pass through untouched. Returns the same instance when nothing changed."""
    new_params: dict[str, object] = {}
    changed = False
    for name, value in body.params.items():
        if isinstance(value, FilePathBindingValue):
            resolved = _resolve_compile_param(value, base_dir, where=where, binding_name=binding_name)
            if resolved is not value:
                changed = True
            new_params[name] = resolved
        else:
            new_params[name] = value
    if not changed:
        return body
    return body.model_copy(update={"params": new_params})


def _resolve_handler_compile_params(decl: HandlerDeclaration, base_dir: str, where: str) -> HandlerDeclaration:
    """Return ``decl`` with every ``compile`` binding's file-supplied params resolved; the same
    instance when the handler declares no file-supplied compile param. ``where`` is the handler's
    qualified name (the diagnostic ``composition_ref``)."""
    new_bindings = []
    changed = False
    for binding in decl.bindings:
        if isinstance(binding.body, CompileBinding):
            new_body = _resolve_compile_binding(
                binding.body, base_dir, where=where, binding_name=binding.name
            )
            if new_body is not binding.body:
                changed = True
                new_bindings.append(binding.model_copy(update={"body": new_body}))
                continue
        new_bindings.append(binding)
    if not changed:
        return decl
    return decl.model_copy(update={"bindings": tuple(new_bindings)})


def resolve_compile_param_files(registry) -> None:
    """Resolve every registered handler's file-supplied ``compile`` parameters in place — the
    compile-directive analogue of :func:`resolve_pipeline_bindings`, run by the caller before the
    hash / assemble. Each ``<param> = { file = "<path>" }`` file is read as **raw text** and stamped
    onto the handler declaration (which is re-registered as a resolved twin; the IR is frozen).

    Paths resolve relative to **each handler's own declaration directory** (the compile directive
    lives in the handler TOML, so the author wrote the path next to it) — taken from
    ``registry.handler_paths``; a handler with a file-supplied compile param but no registered path
    fails loud. Idempotent (an already-stamped param is left as is); the hasher + ``resolve_compile``
    guards make an *unresolved* file param fail loud, so forgetting this pass can never silently
    hash a path or feed a path to a compiler."""
    updates: list[tuple[str, HandlerDeclaration, str | None]] = []
    for qn, decl in registry.handlers.items():
        has_file_param = any(
            isinstance(b.body, CompileBinding)
            and any(isinstance(v, FilePathBindingValue) for v in b.body.params.values())
            for b in decl.bindings
        )
        if not has_file_param:
            continue
        toml_path = registry.get_handler_path(qn)
        if toml_path is None:
            raise ContractViolation(
                check=Check.EXTERNAL_BINDING_UNSUPPORTED, rule_id="R-pipeline-001",
                expected=f"handler '{qn}' has a registered declaration path (its directory anchors "
                         "the relative compile-parameter file path)",
                actual="no declaration path registered for the handler (DeclarationRegistry."
                       "add_handler toml_path=)",
                remediation_hint="register the handler with toml_path=, or supply the compile "
                                 "parameter inline",
                composition_ref=qn,
            )
        base_dir = os.path.dirname(toml_path)
        resolved = _resolve_handler_compile_params(decl, base_dir, where=qn)
        if resolved is not decl:
            updates.append((qn, resolved, toml_path))
    for qn, resolved, toml_path in updates:  # apply after iterating (no mutation mid-walk)
        registry.add_handler(qn, resolved, toml_path=toml_path)
