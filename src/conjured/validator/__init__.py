"""``conjured.validator`` — the Declaration validator component.

C4 responsibility (``conjured/docs/architecture/components.md`` § Declaration
validator): "Loads and validates every engine-read declaration class — handler,
service-type, pipeline, composition, deployment. Compiles pipeline declarations
into typed dataflow graphs at compose time; enforces exhaustive declaration,
key-discipline, field-discipline, channel-type agreement between writes and
downstream reads, and cross-declaration composition checks at load.
Handler-name resolution also runs at compose. Failures raise ContractViolation
before any handler dispatches."

Owns R-pipeline-001 (compose-time composition validation,
``conjured/docs/components/pipeline/reference.md`` § Derived rules) and the
pipeline load lifecycle (parse → compose-time validation → hash → dispatch
construction).

**Build state — Phase 1a floor + the Phase-2 resolution/codegen half.** Stage 1 —
``parse`` (TOML → the Phase-0 IR, with section-presence discipline + closed-grammar
diagnostics). Stage 2 — ``compile`` (the full R-pipeline-001 compose-time type-check +
the ``PipelineDeclaration`` → ``CompiledGraph`` transform), over a handed-in
:class:`DeclarationRegistry`. Stage 3 (hashing) is Phase 1b (``conjured.hasher``).
Phase 2 adds stage 4's compose-time half here: ``model_gen`` (the ``FieldDecl`` →
Pydantic model generator the validation boundaries run against), ``resolve_handler``
(resolution-sequence steps 3–7 → :class:`HandlerEntry`), ``resolve_adapter`` (the
vector-7 sibling mechanism), and ``ast_audit`` (the shared pre-import source-AST
audit). The field-validator machinery (N1) adds ``resolve_validator`` (the third
sibling resolution path + the R-handler-012 compose-time binding and verdict shim)
and ``constraints`` (the built-in attachable constraint set), wired into the
generated models by ``model_gen``. The dispatch *kernel* those feed lives in
``conjured.runner.dispatch``.
"""

# The package top IS the declared consumer surface — exactly the compose-API front door
# (pipeline/reference.md § In-process compose API names these as the public exports).
# Everything else (per-kind parse wrappers, the resolution stages, model generation) is
# engine-internal machinery, imported from its owning submodule.
from conjured.validator.compile import compile_pipeline
from conjured.validator.parse import loads, parse
from conjured.validator.registry import DeclarationRegistry

__all__ = [
    "DeclarationRegistry",
    "compile_pipeline",
    "loads",
    "parse",
]
