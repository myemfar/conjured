"""Deadline propagation through REAL dispatch (service-type/reference.md § Deadline
propagation): a participating adapter surface receives ``remaining_budget_ms`` computed at
the CALL moment; a non-participating surface is dispatched without it, unchanged; an
unbounded run propagates ``None``; and the budget reaches adapters inside a nested
``pipeline`` embed (the whole-run budget is the one engine timeout — a call issued late in
a budgeted run must see how little is left, wherever it sits in the graph)."""

from __future__ import annotations

from conjured.runner import assemble, run
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

TIMEOUT_MS = 60_000

# The participating service adapter: declares the optional kwarg and echoes what it
# received (-1 encodes None over the int channel; no module/class mutable state — the
# adapter purity posture holds for stubs too).
_ECHO_ADAPTER = """
class BudgetEcho:
    def __init__(self, **identity):
        self.identity = dict(identity)

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, remaining_budget_ms=None, **transport_extra):
        return {"seen": -1 if remaining_budget_ms is None else remaining_budget_ms}
"""

# The non-participating adapter: the pre-deadline signature, byte-for-byte the old
# contract — dispatch must never pass it the kwarg.
_BLIND_ADAPTER = """
class BudgetBlind:
    def __init__(self, **identity):
        self.identity = dict(identity)

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, **transport_extra):
        return {"seen": -2}
"""

_CALL_BODY = (
    "def call(*, q, services):\n"
    '    return {"seen": services.llm.invoke(prompt=q)["seen"]}\n'
)

# The burner body sleeps BEFORE invoking, so the budget visible to the adapter must be
# smaller than the whole-run budget by at least the burn.
_BURN_BODY = (
    "import time\n"
    "def call(*, q, services):\n"
    "    time.sleep(0.1)\n"
    '    return {"seen": services.llm.invoke(prompt=q)["seen"]}\n'
)

_SERVICE_TYPE = """
name = "{adapter}"
[identity_schema]
model = {{ type = "str" }}
[transport_schema]
endpoint = {{ type = "str" }}
[config_schema]
"""

_HANDLER = """
[service]
[reads]
q = {{ type = "str" }}
[output_schema]
seen = {{ type = "int" }}
[service_bindings]
llm = {{ type = "{adapter}" }}
"""

_PIPELINE = """
[meta]
name = "dp.svc"
[[nodes]]
kind = "handler"
name = "{handler}"
[service_bindings.llm]
type = "{adapter}"
model = "m"
[inputs]
q = {{ type = "str" }}
[outputs]
seen = {{ type = "int" }}
"""

_DEPLOYMENT = """
[transport.llm]
endpoint = "https://budget.test/v1"
[training_contract]
integrity_enforcement = false
"""


def _service_runnable(module_writer, *, adapter_source, body):
    adapters = module_writer("dp_adapters", adapter_source)
    handlers = module_writer("dp_handlers", body)
    adapter = f"{adapters}.{'BudgetEcho' if 'BudgetEcho' in adapter_source else 'BudgetBlind'}"
    handler = f"{handlers}.call"
    registry = DeclarationRegistry()
    registry.add_service_type(
        loads(_SERVICE_TYPE.format(adapter=adapter), "service_type", file_path="st.toml"),
        toml_path="st.toml",
    )
    registry.add_handler(
        handler, loads(_HANDLER.format(adapter=adapter), "handler", file_path="h.toml"),
        toml_path="h.toml",
    )
    pipeline = loads(
        _PIPELINE.format(handler=handler, adapter=adapter), "pipeline", file_path="p.toml"
    )
    graph = compile_pipeline(pipeline, registry, pipeline_name="dp.svc", file_path="p.toml")
    deployment = loads(_DEPLOYMENT, "deployment", file_path="d.toml")
    return assemble(graph, registry, deployment=deployment)


def test_participating_adapter_receives_the_remaining_budget(module_writer):
    runnable = _service_runnable(
        module_writer, adapter_source=_ECHO_ADAPTER, body=_CALL_BODY
    )
    seen = run(runnable, {"q": "x"}, timeout_ms=TIMEOUT_MS).state["seen"]
    assert 0 < seen <= TIMEOUT_MS


def test_unbounded_run_propagates_none(module_writer):
    runnable = _service_runnable(
        module_writer, adapter_source=_ECHO_ADAPTER, body=_CALL_BODY
    )
    assert run(runnable, {"q": "x"}).state["seen"] == -1


def test_non_participating_adapter_is_dispatched_without_the_kwarg(module_writer):
    # RED-on-removal for the participation gate: pass the kwarg unconditionally and this
    # adapter's strict keyword-only signature raises TypeError at dispatch.
    runnable = _service_runnable(
        module_writer, adapter_source=_BLIND_ADAPTER, body=_CALL_BODY
    )
    assert run(runnable, {"q": "x"}, timeout_ms=TIMEOUT_MS).state["seen"] == -2


# verifies: deadline-budget-at-call-moment
def test_budget_is_computed_at_the_call_moment_not_dispatch_start(module_writer):
    # The body burns ~100ms before invoking; a budget computed at dispatch start would
    # still read ~TIMEOUT_MS, so the margin below is the discriminating assertion.
    runnable = _service_runnable(
        module_writer, adapter_source=_ECHO_ADAPTER, body=_BURN_BODY
    )
    seen = run(runnable, {"q": "x"}, timeout_ms=TIMEOUT_MS).state["seen"]
    assert 0 < seen <= TIMEOUT_MS - 50


# verifies: deadline-propagates-into-embed
def test_budget_reaches_adapters_inside_a_nested_pipeline_embed(module_writer):
    adapters = module_writer("dp_embed_adapters", _ECHO_ADAPTER)
    handlers = module_writer("dp_embed_handlers", _CALL_BODY)
    adapter = f"{adapters}.BudgetEcho"
    handler = f"{handlers}.call"
    registry = DeclarationRegistry()
    registry.add_service_type(
        loads(_SERVICE_TYPE.format(adapter=adapter), "service_type", file_path="st.toml"),
        toml_path="st.toml",
    )
    registry.add_handler(
        handler, loads(_HANDLER.format(adapter=adapter), "handler", file_path="h.toml"),
        toml_path="h.toml",
    )
    inner = (
        "[meta]\n"
        'kind = "pipeline"\n'
        'name = "dp_inner"\n'
        "[[nodes]]\n"
        'kind = "handler"\n'
        f'name = "{handler}"\n'
        "[service_bindings.llm]\n"
        f'type = "{adapter}"\n'
        'model = "m"\n'
        "[inputs]\n"
        'q = { type = "str" }\n'
        "[outputs]\n"
        'seen = { type = "int" }\n'
    )
    registry.add_composition(
        "pipelines/inner.toml", loads(inner, "composition", file_path="pipelines/inner.toml")
    )
    outer = (
        "[meta]\n"
        'name = "dp.outer"\n'
        "[[nodes]]\n"
        'kind = "composition"\n'
        'name = "pipelines/inner.toml"\n'
        "[inputs]\n"
        'q = { type = "str" }\n'
        "[outputs]\n"
        'seen = { type = "int" }\n'
    )
    pipeline = loads(outer, "pipeline", file_path="outer.toml")
    graph = compile_pipeline(pipeline, registry, pipeline_name="dp.outer", file_path="outer.toml")
    deployment = loads(_DEPLOYMENT, "deployment", file_path="d.toml")
    runnable = assemble(graph, registry, deployment=deployment)
    # The inner run has no timeout of its own — the value the embedded adapter sees can
    # only come from the OUTER run's threaded deadline.
    seen = run(runnable, {"q": "x"}, timeout_ms=TIMEOUT_MS).state["seen"]
    assert 0 < seen <= TIMEOUT_MS


# ---------------------------------------------------------------------------
# The trainable surfaces — buffered invoke and the streaming generator
# ---------------------------------------------------------------------------

_BUDGET_BACKEND = """
import json

class BudgetBackend:
    training_artifact_contract = "gguf"
    reserved_wire_keys = frozenset({"model", "temperature", "max_tokens"})

    def __init__(self, model, *, output_schema, schema_source):
        self.model = model

    def _encode(self, remaining):
        return "none" if remaining is None else str(remaining)

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, temperature, max_tokens, remaining_budget_ms=None,
               **transport_extra):
        return {"dialogue_response": "b:" + self._encode(remaining_budget_ms)}

    def invoke_streaming(self, *, input_payload, service_name, caller_qualified_name,
                         caller_position, temperature, max_tokens,
                         remaining_budget_ms=None, **transport_extra):
        emission = {"dialogue_response": "s:" + self._encode(remaining_budget_ms)}
        yield json.dumps(emission)
        return emission
"""

_BACKEND_SERVICE_TYPE = """
name = "{backend}"
[identity_schema]
model = {{ type = "str" }}
[transport_schema]
endpoint = {{ type = "str" }}
[config_schema]
temperature = {{ type = "float" }}
max_tokens = {{ type = "int" }}
"""

_TRAINABLE_COMPOSITION = """
[meta]
kind = "trainable"
name = "dp_train"
[inputs]
prompt = {{ type = "str" }}
[outputs]
dialogue_response = {{ type = "str" }}
[service_bindings.llm]
type = "{backend}"
model = "m"
[trainable]
streamable = {streamable}
[trainable.config]
temperature = 0.1
max_tokens = 8
[trainable.service_bindings]
llm = {{ type = "{backend}" }}
[trainable.reads]
prompt = {{ type = "str" }}
[trainable.output_schema]
dialogue_response = {{ type = "str" }}
"""

_TRAINABLE_PIPELINE = """
[meta]
name = "dp.train"
[[nodes]]
kind = "composition"
name = "trainables/dp.toml"
[inputs]
prompt = { type = "str" }
[outputs]
dialogue_response = { type = "str" }
"""


def _trainable_runnable(module_writer, *, streamable: str):
    backends = module_writer("dp_backend_mod", _BUDGET_BACKEND)
    backend = f"{backends}.BudgetBackend"
    registry = DeclarationRegistry()
    registry.add_service_type(
        loads(_BACKEND_SERVICE_TYPE.format(backend=backend), "service_type",
              file_path="st.toml"),
        toml_path="st.toml",
    )
    registry.add_composition(
        "trainables/dp.toml",
        loads(_TRAINABLE_COMPOSITION.format(backend=backend, streamable=streamable),
              "composition", file_path="trainables/dp.toml"),
    )
    pipeline = loads(_TRAINABLE_PIPELINE, "pipeline", file_path="p.toml")
    graph = compile_pipeline(pipeline, registry, pipeline_name="dp.train", file_path="p.toml")
    deployment = loads(_DEPLOYMENT, "deployment", file_path="d.toml")
    return assemble(graph, registry, deployment=deployment)


def test_trainable_buffered_surface_receives_the_budget(module_writer):
    runnable = _trainable_runnable(module_writer, streamable="false")
    value = run(runnable, {"prompt": "hi"}, timeout_ms=TIMEOUT_MS).state["dialogue_response"]
    assert value.startswith("b:")
    assert 0 < int(value[2:]) <= TIMEOUT_MS


def test_trainable_buffered_surface_unbounded_is_none(module_writer):
    runnable = _trainable_runnable(module_writer, streamable="false")
    value = run(runnable, {"prompt": "hi"}).state["dialogue_response"]
    assert value == "b:none"


def test_trainable_streaming_surface_receives_the_budget(module_writer):
    runnable = _trainable_runnable(module_writer, streamable="true")
    fragments: list[str] = []
    value = run(
        runnable, {"prompt": "hi"}, timeout_ms=TIMEOUT_MS, stream_sink=fragments.append,
    ).state["dialogue_response"]
    assert value.startswith("s:")
    assert 0 < int(value[2:]) <= TIMEOUT_MS
    assert fragments  # the generator surface actually ran
