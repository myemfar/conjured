"""``conjured.hasher`` — the Hash machinery component.

C4 responsibility (``conjured/docs/architecture/components.md`` § Hash machinery):
"Computes the engine's hashes — the pipeline-hash and the
per-trainable-composition training-bundle-hashes."

Both hashes are SHA-256 over a canonicalized serialization of the engine's
**Pydantic IR** of the relevant subgraph — not over the TOML lexical form — so two
authoring conventions producing the same declared graph produce the same hash
(``conjured/docs/architecture/hash-model.md`` § How the hashes are constructed).
The hash machinery is therefore a pure function over the IR (``conjured.ir``).

**Input is the normalized declaration IR, not the dispatch-flattened ``CompiledGraph``**:
the compiled graph drops
``trainable.config`` + the backend binding, scopes channels, and dissolves the composition
boundary, so it is the wrong hash input. The hasher runs over the raw declaration IR + the
:class:`~conjured.validator.registry.DeclarationRegistry` it resolves references against,
reusing only the shared :func:`~conjured.validator.normalize.desugar_map` step so the
normalized wiring it hashes equals what the compiler validated.

**Build state — Phase 1b (the second half of the verification floor).** Pure functions,
compose-time, **no dispatch**: :func:`pipeline_hash` (the full composition's identity) and
:func:`training_bundle_hash` (one per engine-owned-dispatch composition node). External-file
*content* resolution (the ``{ file = "..." }`` form — binding values AND compile-directive params)
is the I/O job of ``validator.resolve``, run **before** the hash; these stay pure and fold the
**stamped** content (and fail loud on an unresolved external-file declaration — never read a file).
The manifest *compare* / drift *events* / integrity *halt* (deployment-load orchestration + Phase 4)
are out of scope — they *call* these.
"""

from conjured.hasher.hashes import pipeline_hash, training_bundle_hash

__all__ = ["pipeline_hash", "training_bundle_hash"]
