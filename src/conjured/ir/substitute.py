"""Bundle substitution — the one pre-walk step every pipeline-node walker applies.

A ``bundle`` composition is **pure substitution** (glossary § Bundle TOML;
handler/reference.md § A composition mirrors the pipeline): its ``nodes`` are textually
substituted into the enclosing node sequence at the embed point at compose, BEFORE the
enclosing unit is scoped or hashed. Every downstream concern — validation,
type-checking, merge resolution, hash computation, dispatch-graph construction, the
derivables extraction — operates on the post-substitute inlined form as if the nodes
had been declared directly in the enclosing unit.

:func:`substitute_bundle_nodes` is the single substitution mechanism (one grammar, one
fold path — never re-derived per walker). Each consuming walker calls it at its entry
chokepoint over the node sequence it is about to walk — ``compile_pipeline``,
``pipeline_hash``, and the derivables extraction. (The resolution pass does NOT
substitute: it stamps the registry's bundle entry with a resolved twin instead, so
each bundle's ``{ file }`` bindings anchor to the BUNDLE's own declaration directory
and every later substitution splices stamped nodes.) The hasher's own-hash-domain
allowlist (``tbh-fold-own-hash-domain-only``) stays in place as the structural
backstop for a walker that forgot to — a bundle reaching a by-reference fold fails
loud rather than being silently mis-hashed.

Non-bundle nodes pass through untouched: handler nodes, own-hash-domain composition
embeds (trainable / nested ``pipeline``), and unresolved composition names alike —
resolution failures stay the calling pass's concern (compile's resolution group owns
that diagnostic; this step substitutes only what RESOLVES to a bundle).
"""

from __future__ import annotations

from typing import Callable

from conjured.errors import Check, ContractViolation
from conjured.ir.composition import BundleComposition
from conjured.ir.pipeline import CompositionNode, PipelineNode

__all__ = ["substitute_bundle_nodes"]


# guarantees: bundle-substitutes-before-scope-and-hash
def substitute_bundle_nodes(
    nodes: tuple[PipelineNode, ...],
    get_composition: Callable[[str], object | None],
    *,
    where: str,
    _embed_stack: tuple[str, ...] = (),
) -> tuple[PipelineNode, ...]:
    """Return ``nodes`` with every bundle embed textually substituted (recursively — a
    bundle may embed bundles), or ``nodes`` unchanged (identity) when none resolves to a
    bundle. ``get_composition`` is the registry lookup (``DeclarationRegistry
    .get_composition``); ``where`` is the enclosing unit's name for the diagnostic locus.

    A bundle transitively embedding itself is the only non-terminating case and is
    rejected as a structured :class:`ContractViolation` (``COMPOSITION_CYCLE``) — the
    same compose-time contract the nested-``pipeline`` embed enforces; a finite acyclic
    nesting has no depth ceiling."""
    out: list[PipelineNode] = []
    changed = False
    for node in nodes:
        if isinstance(node, CompositionNode):
            comp = get_composition(node.name)
            if isinstance(comp, BundleComposition):
                if node.name in _embed_stack:
                    raise ContractViolation(
                        check=Check.COMPOSITION_CYCLE, rule_id="R-pipeline-001",
                        expected="the bundle embed graph is acyclic — a bundle never "
                                 "transitively embeds itself",
                        actual="embed cycle: " + " -> ".join((*_embed_stack, node.name)),
                        composition_ref=where,
                        remediation_hint="break the cycle — a cyclic composition never loads",
                    )
                out.extend(substitute_bundle_nodes(
                    comp.nodes, get_composition,
                    where=where, _embed_stack=(*_embed_stack, node.name),
                ))
                changed = True
                continue
        out.append(node)
    return tuple(out) if changed else nodes
