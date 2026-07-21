"""Conjured — a Python engine for handler composition with
pipeline-as-training-contract derivation.

A composed pipeline is a typed dataflow graph (handlers as nodes, declared
reads/writes as typed channels). The engine type-checks the graph at compose
time, dispatches handlers in declared order at runtime, and derives the training
corpus as a view of that same graph (invariant I4). See
``conjured/docs/explanation/overview.md`` for the architecture landing page.

**Build state.** The engine is built through the runtime: ``conjured.ir`` (the
Pydantic IR), ``conjured.validator`` (parse + compose-time type-check + the
resolution seals + model generation), ``conjured.hasher`` (the pipeline- and
training-bundle-hashes), ``conjured.runner`` (the single-dispatch kernel, the
assembled ``Runnable``, and the kernel walk), ``conjured.events`` (the canonical
event log — the walk emits the ``handler_enter``/``handler_exit`` training-record
pair, the run-lifecycle events, and ``service_invocation``), the service-type
adapters (``conjured.adapters``), and the native trainable backends
(``conjured.lib``), the **Server** (the HTTP+SSE wire surface, ``conjured.server``),
and the bundled **``conjured`` Python client** (``conjured/docs/architecture/components.md``
§ Server, § ``conjured`` Python client).

The canonical internal representation is **Pydantic**: type-checking, hash
construction, and dispatch-boundary validation all operate over the IR, not over
the TOML authoring dialect (overview.md § Pydantic as the canonical
representation). ``conjured.ir`` is that representation's home.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
