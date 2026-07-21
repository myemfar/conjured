> **Read-only mirror.** This repository is the published snapshot of the `conjured`
> package, regenerated wholesale from its privately-developed source tree at each
> release — one sync commit per version. Nothing is developed here and pull requests
> cannot be accepted (the next sync would overwrite them); please **file issues** —
> they are read and triaged upstream. Releases on PyPI are published from this
> repository via Trusted Publishing, so each release carries provenance attestations
> tying the package to this repo and tag.

# Conjured

**A Python engine for handler composition with pipeline-as-training-contract derivation.**

**Documentation: <https://rp-chat.com/conjured/>** — the full canonical corpus: architecture,
references, explanations, and the machine-readable authoring schemas.

Conjured composes handlers into a **typed dataflow graph** — handlers are nodes, and the
state each reads and writes are typed channels between them. The engine type-checks the
whole graph at compose time and dispatches the handlers in declared order at runtime.

Its one load-bearing idea: a composed pipeline is **simultaneously the runtime contract
and the training-data shape**. The schemas that validate a channel at runtime are the same
types that define the training-record shape — not two contracts kept in sync, but one graph
queried two ways. Edit the composition and the training contract re-derives. Everything else
maps to familiar, field-named patterns; this collapse-by-construction is the novel part.

Handlers are ordinary kwargs-in / dict-out functions — they never see a shared mutable
context, and the runner is the sole writer of channel state. That purity is what makes a run
replayable, and replayability is what lets the training corpus be a faithful derived view of
the graph.

## Who it's for

Conjured is for teams shipping **fine-tuned local models inside real products** — where
production must run exactly the pipeline the model was trained on. A captured run is
simultaneously a training record, a replay record, and a test fixture; the engine's
guarantees exist to keep that contract trustworthy. Three readers shape every surface:

- **Authors.** A pipeline is declared in TOML, and the declaration reads as a document — a
  domain author can compose and review one without writing Python. Handler bodies stay
  small, ordinary functions.
- **Coding agents.** The documentation is written to be machine-legible — stable anchors,
  closed vocabularies, per-kind authoring schemas — because an agent helping an author is a
  first-class reader, not an afterthought.
- **Integrators.** The pip-installable engine embeds in your product; the server component
  and bundled Python client put the same pipeline behind HTTP + SSE, and the canonical
  event log feeds your observability stack.

## Where Conjured sits

Conjured is a typed dataflow engine whose composed pipeline doubles as the training contract
for the model inside it — not a prompt optimizer, not a structured-output parser, not an
agent orchestrator; those occupy different layers. Per-node structured output is table
stakes (the adapter submits the declared output schema as the backend's decode constraint);
the contribution is graph-wide: compose-time type-checking across every channel, canonical
capture events from every run at the declared seams, and a per-load hash check tying the
pipeline you serve to the corpus you trained on. If you never fine-tune, you are paying for
guarantees you don't need; if you do, holding that contract is the engine's entire job.

### The adjacent layers

- **DSPy** programs and optimizes LM pipelines — its optimizers tune prompts and demos (and
  its bootstrap-finetune path distills a program into weights) against a metric; its training
  data is compile-time teacher traces, instrumental to the optimizer. Conjured's corpus is the
  product: captured from real runs, contract-identified to the pipeline that will serve it.
- **BAML** types the call — a DSL for type-safe LLM functions with robust parsing, and no
  training story. Conjured types the graph: every channel between handlers,
  compose-time-checked, with the training view derived from the same types.
- **instructor / outlines** shape one call's output (validation-retry / constrained decoding).
  Inside Conjured that job belongs to the trainable adapter; neither holds a pipeline contract
  or a corpus.
- **LangGraph** orchestrates agent control flow over shared state. Conjured deliberately
  refuses shared mutable state inside a pipeline; driving Conjured pipelines *from* an
  orchestrator is consumer territory, one layer up.
- **distilabel** synthesizes datasets offline from teacher models — complementary, not
  competing: Conjured's pipeline-derivables bundle is exactly the machine-checkable target
  shape an external generator can fill.

## Status

**Alpha.** The engine core is complete and verified: the compose-time validator, the
two-hash integrity scheme, the dispatch runner, the canonical event log, the native trainable
backends, and the server/client surfaces are in place, covered by the test floor that gates
every merge. The canonical reference corpus is complete and hosted at
<https://rp-chat.com/conjured/>, and the first how-to guide (compose-and-run) ships with it;
the public API may change before 1.0.

## Install

```sh
pip install conjured
```

The engine core depends only on `pydantic`. Two optional extras pull in backend stacks that
are lazily imported and raise a clear `ImportError` naming the extra when absent:

```sh
pip install "conjured[server]"     # the HTTP + SSE wire surface (Starlette / Uvicorn)
pip install "conjured[compilers]"  # the jinja / json_schema compile affordances
```

## A first pipeline

A pipeline is composed from handlers you declare in TOML. Here is a one-node pipeline whose
single transform handler reads a `name` channel and writes a `greeting`.

The handler is an ordinary function in an importable module, `greet.py`:

```python
def greet(*, name):
    return {"greeting": f"Hello, {name}!"}
```

Compose it into a pipeline, type-check it, and run it:

```python
from conjured.runner import assemble, run
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

# A transform handler's declaration: it reads a `name` channel and writes a `greeting`.
HANDLER_TOML = """
[transform]
[reads]
name = { type = "str" }
[output_schema]
greeting = { type = "str" }
"""

# A pipeline: one node, wired to the pipeline's `name` input and `greeting` output.
PIPELINE_TOML = """
[meta]
name = "demo.hello"
[[nodes]]
kind = "handler"
name = "greet.greet"
[inputs]
name = { type = "str" }
[outputs]
greeting = { type = "str" }
"""

registry = DeclarationRegistry()
registry.add_handler("greet.greet", loads(HANDLER_TOML, "handler", file_path="greet.toml"),
                     toml_path="greet.toml")

pipeline = loads(PIPELINE_TOML, "pipeline", file_path="pipeline.toml")
graph = compile_pipeline(pipeline, registry, pipeline_name="demo.hello", file_path="pipeline.toml")
runnable = assemble(graph, registry)

result = run(runnable, {"name": "world"})
print(result.state["greeting"])  # -> Hello, world!
```

`compile_pipeline` runs the full compose-time type-check — a mismatched channel type or an
unresolvable handler raises `ContractViolation` before any handler dispatches. `run` walks the
graph in declared order and returns a `RunResult` whose `state` carries the written channels.

## How the package is organized

In the order a pipeline lives its life:

- **`conjured.validator`** — the front door: parses declaration TOML into typed records and
  compiles a pipeline into its typed dataflow graph, refusing anything malformed before a
  single handler runs. Also owns handler/adapter resolution and the generated validation
  models that guard every dispatch boundary.
- **`conjured.ir`** — the canonical internal representation: pure Pydantic models for the
  declaration classes, the channel-type vocabulary, and the compiled graph. Everything else
  operates over these models, never over raw TOML.
- **`conjured.hasher`** — the two fingerprints as pure functions: the pipeline-hash (the
  composed pipeline's identity) and the training-bundle-hash (the training contract's
  identity). Reformatting and renaming never move a hash; a contract-visible change always
  does.
- **`conjured.runner`** — the kernel: `assemble` freezes a compiled graph into a `Runnable`;
  `run` walks it, projecting declared reads into fresh kwargs, validating every return, and
  writing channels as the sole writer.
- **`conjured.events`** — the closed set of canonical events on a standard logging channel.
  The engine only emits; the enter/exit pair per dispatch is the training record.
- **`conjured.manifest`** — trained-artifact integrity at load: recompute the hashes against
  each artifact's sidecar manifest, fire drift events on mismatch, and — when the deployment
  opts in — refuse to serve a pipeline its artifacts were not trained under.
- **`conjured.errors`** — the closed three-class failure surface: `ContractViolation`,
  `SchemaValidationError`, `PipelineFailure`. No other error shape crosses the engine
  boundary.
- **`conjured.server` / `conjured.client`** — the HTTP + SSE wire surface (the `[server]`
  extra) and the thin Python client that wraps a loopback server subprocess behind the same
  wire API.
- **`conjured.adapters` / `conjured.lib`** — the wire-constraint rendering floor (strict
  JSON Schema; GBNF grammars) and the native trainable backends that consume it.
- **`conjured.testing`** — the consumer testing library (a pytest plugin): drive the real
  runner, assert through the event stream, substitute verified fakes at the adapter seam.
- **`conjured.agent` / `conjured.conformance`** — the machine-readable companion surfaces
  for coding agents: the docs projection, steering content, error index, and conformance
  prompts, all shipped inside the wheel.
- **the `conjured` CLI** — `conjured derivables` extracts the pipeline-derivables bundle
  (the machine-checkable training contract); `artifact-tag` / `artifact-mv` author the
  integrity sidecars.

The component responsibilities behind this map are specified in the architecture section of
the hosted docs (<https://rp-chat.com/conjured/>).

## Documentation

The full canonical specification — the architecture, the handler and pipeline references, the
hash and trust models, and the error channel — is hosted at
**<https://rp-chat.com/conjured/>**. The architecture overview is the recommended entry
point; the same corpus ships verbatim in the source distribution under `docs/`, so an
offline install (or a coding agent working from the sdist) reads identical content.

## License

Apache-2.0. See the `LICENSE` and `NOTICE` files in the distribution.
