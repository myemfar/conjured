"""``conjured.runner`` — the Runner (kernel) component.

C4 responsibility (``conjured/docs/architecture/components.md`` § Runner (kernel)):
"Internal to the server. Dispatches handlers in declared order; projects each
handler's declared channel writes through the pipeline graph for downstream nodes
to read; enforces handler return-value validation against ``output_schema``; emits
canonical events on ``conjured.events.runner`` for the server to project."

The runner is the **sole channel writer**: handlers declare channel-agnostic ports
and return dicts keyed by output-port name; the runner projects each return via the
node's write-map onto channels (``conjured/docs/components/pipeline/reference.md``
§ Kernel semantics). Merge / projection / identity-desugar are **runner
operations, not nodes** — a merge is applied inline with no synthesized node and no
merge event. ``conjured.runner.run(...)`` is the engine's per-invocation entry.

**Build state — the multi-handler runner (Phase 3).** ``conjured.runner.dispatch``
carries the construct-once / invoke-many dispatch kernel (Phase 2: binding delivery,
the two validation boundaries, the ``ServicesProxy``, the trainable
``functools.partial`` dispatch). ``conjured.runner.assemble`` completes lifecycle
stage 4 into the frozen ``Runnable``; ``conjured.runner.run`` is the kernel walk —
channel state, read-map projection, fold-as-you-walk merges, the dispatch-boundary
``PipelineFailure`` wrap, the hook wrapper's operational sanction, the vector-3
module-namespace snapshot-restore, and the cooperative pipeline-level timeout —
behind the ``SequentialTaskRunner`` executor seam (``conjured.runner.executor``).

**Canonical event emission (Phase 4) fires.** The walk emits the ``handler_enter`` /
``handler_exit`` pair per dispatch (the training record), the run-lifecycle events
(``pipeline_start`` / ``pipeline_complete`` / ``pipeline_error``), and — at the
``dispatch._BoundService`` adapter boundary — ``service_invocation`` for service-kind
dispatches, all on ``conjured.events.runner`` (producer/consumer; the engine ships no
handler). The compose-time ``training_bundle_hash_changed`` / ``pipeline_hash_changed``
events fire from trained-artifact manifest verification at the public ``assemble`` entry
(``conjured.manifest``, R-pipeline-003).
"""

# The package top IS the declared consumer surface — the compose-API run half
# (pipeline/reference.md § In-process compose API: `assemble`, `run`) plus the two owned
# value types (§ Pipeline invocation / § Pipeline result). The dispatch kernel, executor
# seam, and construction internals are engine-internal, imported from their submodules.
from conjured.runner.assemble import Runnable, assemble
from conjured.runner.run import RunResult, run

__all__ = [
    "Runnable",
    "RunResult",
    "assemble",
    "run",
]
