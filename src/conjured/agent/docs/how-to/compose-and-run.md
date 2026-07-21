---
kind: how-to
audience: [integrators, agents]
slug: howto-compose-and-run
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#howto-compose-and-run}
# How to compose and run a pipeline in-process

**Goal.** Take declaration TOML on disk to a running pipeline inside one Python process:
parse → register → compile → assemble → run. At the end you have a dispatch-ready
`Runnable` and a `RunResult` whose `state` carries your pipeline's output — plus the two
variations real integrations need first: carrying state across runs, and running a
service-backed pipeline against a verified fake.

Every code block on this page runs verbatim and prints the output shown. The API this
page drives is owned at [In-process compose API](#in-process-compose-api) — that section
holds the contracts (what each call accepts, returns, and raises); this page shows the
task.

**Prerequisites.** Python ≥ 3.11 and the engine installed:

```sh
pip install conjured
```

{#howto-project-files}
## The project files

Three files in one directory. The handler is an ordinary function in an importable
module — kwargs in, dict out:

`greet.py`

```python
def greet(*, name):
    return {"greeting": f"Hello, {name}!"}
```

Its declaration names what it reads and what it writes:

`greet.toml`

```toml
[transform]

[reads]
name = { type = "str" }

[output_schema]
greeting = { type = "str" }
```

The pipeline composes it — one node, wired to the pipeline's declared API boundary:

`pipeline.toml`

```toml
[meta]
name = "demo.hello"

[[nodes]]
kind = "handler"
name = "greet.greet"

[inputs]
name = { type = "str" }

[outputs]
greeting = { type = "str" }
```

{#howto-compose-and-run-walkthrough}
## Compose and run

One script drives the whole lifecycle:

`compose.py`

```python
from pathlib import Path

from conjured.runner import assemble, run
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

registry = DeclarationRegistry()
registry.add_handler(
    "greet.greet",
    loads(Path("greet.toml").read_text(encoding="utf-8"), "handler", file_path="greet.toml"),
    toml_path="greet.toml",
)

pipeline = loads(Path("pipeline.toml").read_text(encoding="utf-8"), "pipeline", file_path="pipeline.toml")
graph = compile_pipeline(pipeline, registry, pipeline_name="demo.hello", file_path="pipeline.toml")
runnable = assemble(graph, registry)

result = run(runnable, {"name": "world"})
print(result.state["greeting"])
```

```sh
$ python compose.py
Hello, world!
```

Step by step, against [In-process compose API](#in-process-compose-api):

1. **Parse** — `loads(toml_text, kind, file_path=…)` turns each declaration string into
   its typed record. You parse the *text* you read from disk; `file_path` is what a
   diagnostic cites.
2. **Register** — the `DeclarationRegistry` holds the parsed **declarations** compose-time
   resolution reads names against: `add_handler` keys the handler declaration by the
   qualified name your pipeline's node references. The handler *callable* is not
   registered — it resolves from that name at compile time by
   [handler resolution](#architecture-handler-resolution) (which is why `greet.py` must be
   importable from where you run the script: `greet.greet` imports module `greet` and
   takes its `greet`).
3. **Compile** — `compile_pipeline(pipeline, registry, pipeline_name=…)` runs the full
   compose-time type-check and returns the compiled graph. Every contract failure —
   a mistyped channel, an unresolvable name, a read with no upstream write — raises
   [`ContractViolation`](#contractviolation) here, before any handler dispatches.
4. **Assemble** — `assemble(graph, registry)` resolves the handlers, generates the
   validation models, computes the pipeline-hash, and returns the frozen `Runnable`.
5. **Run** — `run(runnable, inputs)` dispatches and returns a
   [`RunResult`](#pipeline-result-runresult): `state` carries the written channels; a
   returned result *is* success (failure raises — there is no status field to check).
   The call's optional parameters are owned at [Pipeline invocation](#pipeline-invocation).

**Registering a native service-type.** When a pipeline binds one of the engine's own
`conjured.lib.*` service-types, its *implementation* resolves through the engine's native
adapter table — but its *declaration* is registered like any other, from the engine-shipped
sibling TOML ([In-process compose API](#in-process-compose-api) owns the rule):

```python
from importlib.resources import files
from conjured.validator import DeclarationRegistry, loads

registry = DeclarationRegistry()
native_toml = files("conjured.lib").joinpath("openai_compatible_trainable.toml").read_text(encoding="utf-8")
registry.add_service_type(loads(native_toml, "service_type",
                                file_path="conjured/lib/openai_compatible_trainable.toml"))
```

{#howto-carrying-state}
## Carrying state across runs

Each invocation is one `(pipeline, inputs)` pair dispatched under a fresh per-run channel
state ([Pipeline invocation](#pipeline-invocation)) — so state that outlives a run is a
channel your pipeline **declares in `outputs`**, and your loop feeds back into the next
run's `inputs`. Inside a run a channel is single-assignment, so the carried value comes
back out under a **new** name: the handler reads `count` and writes `next_count`; the loop
renames at the boundary.

`counter.py`

```python
def step(*, count):
    new_count = count + 1
    return {"next_count": new_count, "message": f"run number {new_count}"}
```

`counter.toml`

```toml
[transform]

[reads]
count = { type = "int" }

[output_schema]
next_count = { type = "int" }
message = { type = "str" }
```

`counter_pipeline.toml`

```toml
[meta]
name = "demo.counter"

[[nodes]]
kind = "handler"
name = "counter.step"

[inputs]
count = { type = "int" }

[outputs]
next_count = { type = "int" }
message = { type = "str" }
```

`carry_state.py`

```python
from pathlib import Path

from conjured.runner import assemble, run
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

registry = DeclarationRegistry()
registry.add_handler(
    "counter.step",
    loads(Path("counter.toml").read_text(encoding="utf-8"), "handler", file_path="counter.toml"),
    toml_path="counter.toml",
)
pipeline = loads(Path("counter_pipeline.toml").read_text(encoding="utf-8"), "pipeline",
                 file_path="counter_pipeline.toml")
graph = compile_pipeline(pipeline, registry, pipeline_name="demo.counter",
                         file_path="counter_pipeline.toml")
runnable = assemble(graph, registry)

count = 0
for _ in range(3):
    result = run(runnable, {"count": count})
    count = result.state["next_count"]
    print(result.state["message"])
```

```sh
$ python carry_state.py
run number 1
run number 2
run number 3
```

The loop — which values carry, and when to stop — is yours: the engine's contract is one
complete, validated run per call.

{#howto-calling-a-service}
## Calling a service from a handler

A handler that makes an external call is a **service**-kind handler: its declaration names
the binding, and its body receives a `services` argument. The call passes your **domain
fields directly as keyword arguments** — `services.llm.invoke(prompt=question)` — and the
engine assembles the request envelope from them and routes it through the bound
adapter (there is no envelope to build in the body; wrapping your kwargs in an
`input_payload=` argument of your own double-wraps the request). The body makes exactly
one external call per dispatch.

`ask.py`

```python
def ask(*, question, services):
    answer = services.llm.invoke(prompt=question)
    return {"answer": answer["reply"]}
```

`ask.toml`

```toml
[service]

[reads]
question = { type = "str" }

[output_schema]
answer = { type = "str" }

[service_bindings]
llm = { type = "fake_llm.FakeChat" }
```

The binding's `type` names a service-type declaration
([service-type reference](#service-type-reference)); which concrete backend serves it —
and its endpoint, credentials, and other transport — is supplied per environment by the
deployment declaration ([`[transport]`](#transport-section); real credentials travel as
[secret references](#secret-references), never as values in TOML).

{#howto-testing-with-a-verified-fake}
## Testing a service pipeline with a verified fake

To run that pipeline without a live backend, substitute a **verified fake** at the adapter
seam ([Verified fakes](#verified-fakes)): a test double that fails wherever the real
backend would. The swap is the service-type **`type` value, in both places that name it**
— the handler declaration's `[service_bindings]` *and* the pipeline's supply — because the
engine matches those two exactly at compose time. Here both already say
`fake_llm.FakeChat`, the fake's own qualified name, which is exactly what a test build of
your declarations does to a production `type` value.

`fake_llm.py` — the fake adapter, resolvable at its qualified name:

```python
from conjured.testing import VerifiedFake


class FakeChat(VerifiedFake):
    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, **transport_extra):
        return self._invoke(
            input_payload=input_payload, service_name=service_name,
            caller_qualified_name=caller_qualified_name,
            caller_position=caller_position, **transport_extra,
        )

    def validate_input(self, input_payload):
        if "prompt" not in input_payload:
            raise ValueError("the real backend rejects a request with no prompt")

    def respond(self, input_payload):
        return {"reply": f"You said: {input_payload['prompt']}"}
```

`fake_llm.toml` — its service-type declaration:

```toml
name = "fake_llm.FakeChat"

[identity_schema]
model = { type = "str" }

[transport_schema]
endpoint = { type = "str" }

[config_schema]
```

`ask_pipeline.toml` — the pipeline supplies the binding's identity:

```toml
[meta]
name = "demo.ask"

[[nodes]]
kind = "handler"
name = "ask.ask"

[service_bindings.llm]
type = "fake_llm.FakeChat"
model = "fake-1"

[inputs]
question = { type = "str" }

[outputs]
answer = { type = "str" }
```

`dev.deployment.toml` — the deployment supplies the transport:

```toml
[transport.llm]
endpoint = "https://fake.test/v1"

[training_contract]
integrity_enforcement = false
```

`test_ask.py` — the same five steps, now with the service-type and deployment registered:

```python
from pathlib import Path

from conjured.runner import assemble, run
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

registry = DeclarationRegistry()
registry.add_service_type(
    loads(Path("fake_llm.toml").read_text(encoding="utf-8"), "service_type",
          file_path="fake_llm.toml"),
    toml_path="fake_llm.toml",
)
registry.add_handler(
    "ask.ask",
    loads(Path("ask.toml").read_text(encoding="utf-8"), "handler", file_path="ask.toml"),
    toml_path="ask.toml",
)
registry.deployment = loads(Path("dev.deployment.toml").read_text(encoding="utf-8"),
                            "deployment", file_path="dev.deployment.toml")

pipeline = loads(Path("ask_pipeline.toml").read_text(encoding="utf-8"), "pipeline",
                 file_path="ask_pipeline.toml")
graph = compile_pipeline(pipeline, registry, pipeline_name="demo.ask",
                         file_path="ask_pipeline.toml")
runnable = assemble(graph, registry)

result = run(runnable, {"question": "is this thing on?"})
print(result.state["answer"])
```

```sh
$ python test_ask.py
You said: is this thing on?
```

The fake's `validate_input` keeps the double honest: a body that submits a request the
real backend would reject fails here too, on the real dispatch path. The consumer testing
library ([testing API](#testing-api)) builds on this seam — `run_and_capture` reads the
run's event stream, and `harvest` turns captured service payloads into hash-gated
fixtures.

{#howto-where-next}
## Where next

- Serving the same pipeline over HTTP + SSE: the [server reference](#server-reference)
  (`create_app` over a mapping of assembled runnables).
- Everything the deployment declaration supplies per environment — transport, artifacts,
  enforcement postures: the [deployment reference](#deployment-reference).
- The full compose-API contracts this page walked:
  [In-process compose API](#in-process-compose-api).
