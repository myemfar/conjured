"""Canon-grounded declaration fixtures for the derivables-extraction tests.

Declaration TOMLs only (no real callables — extraction is compose-time / pure-read), modeled on
the validator suite's worked composition (``tests/validator/fixtures.py``). Two builders: a
binding-rich handler-only pipeline (exercises the ``binding_snapshot``) and a trainable
composition pipeline whose backend service-type carries a ``description`` (exercises the
``trainables`` member incl. the service metadata description). Each is lifted from a
compile-valid composition so extraction's ``compile_pipeline`` gate passes.
"""

from __future__ import annotations

from conjured.validator import DeclarationRegistry, loads

# --- Backend service-types (WITH description — the generator-instruction context) ------------
SERVICE_TYPE_LLM = """
name = "conjured_llm.structured_output"
description = "An LLM backend that emits a constrained structured response."
[identity_schema]
model = { type = "str" }
[transport_schema]
endpoint = { type = "str" }
[config_schema]
temperature = { type = "float" }
"""

SERVICE_TYPE_DIALOGUE = """
name = "conjured_llm.dialogue"
description = "A dialogue backend: given assembled context, emit an in-character reply."
[identity_schema]
model = { type = "str" }
[transport_schema]
endpoint = { type = "str" }
[config_schema]
temperature = { type = "float" }
max_tokens = { type = "int" }
"""

# --- Handlers -------------------------------------------------------------------------------
TRANSFORM_NORMALIZE = """
[transform]
[reads]
player_input = { type = "str" }
[output_schema]
normalized_input = { type = "str" }
[bindings.config]
marker_set = { type = "str" }
"""

SERVICE_RESPOND = """
[service]
[reads]
normalized_input = { type = "str" }
[output_schema]
dialogue = { type = "str" }
[service_bindings]
llm = { type = "conjured_llm.structured_output" }
"""

HOOK_LOG = """
[hook]
[reads]
dialogue = { type = "str" }
[service_bindings]
[transport_schema]
path = { type = "str" }
"""

TRANSFORM_CTX = """
[transform]
[reads]
raw = { type = "str" }
[output_schema]
npc_state = { type = "str" }
user_message = { type = "str" }
"""

TRANSFORM_FORMATTER = """
[transform]
[reads]
context = { type = "str" }
utterance = { type = "str" }
[output_schema]
prompt = { type = "str" }
[bindings.config]
template = { type = "str" }
"""

# --- Pipelines ------------------------------------------------------------------------------
# Binding-rich handler-only pipeline: node 0 supplies a `config` binding; the pipeline declares
# a service_bindings.llm identity supply. Exercises binding_snapshot's node_bindings +
# service_bindings. No trainable (trainables is legitimately empty here).
PIPELINE_BINDINGS = """
[meta]
name = "acme.dialogue"
[[nodes]]
kind = "handler"
name = "acme.normalize"
bindings = { config = { marker_set = "brackets" } }
[[nodes]]
kind = "handler"
name = "acme.respond"
[[nodes]]
kind = "handler"
name = "acme.log"
[service_bindings.llm]
type = "conjured_llm.structured_output"
model = "qwen3.5-4b-gguf"
[service_bindings.llm.config]
temperature = 0.7
[inputs]
player_input = { type = "str" }
[outputs]
dialogue = { type = "str" }
"""

TRAINABLE_COMPOSITION = """
[meta]
kind = "trainable"
name = "dialogue_training"
[inputs]
npc_state = { type = "str" }
user_message = { type = "str" }
[outputs]
dialogue_response = { type = "str" }
[[preprocessors]]
kind = "handler"
name = "transform.formatter"
id   = "assemble_prompt"
reads_map = { context = "npc_state", utterance = "user_message" }
writes_map = { prompt = "formatted_prompt" }
[preprocessors.bindings]
config = { template = "{context}\\n{utterance}" }
[service_bindings.llm]
type = "conjured_llm.dialogue"
model = "qwen3.5-4b-gguf"
[trainable]
[trainable.config]
temperature = 0.7
max_tokens = 512
[trainable.service_bindings]
llm = { type = "conjured_llm.dialogue" }
[trainable.reads]
formatted_prompt = { type = "str" }
[trainable.output_schema]
dialogue_response = { type = "str" }
"""

PIPELINE_WITH_COMPOSITION = """
[meta]
name = "acme.dialogue"
[[nodes]]
kind = "handler"
name = "acme.ctx"
[[nodes]]
kind = "composition"
name = "trainables/dialogue.toml"
[inputs]
raw = { type = "str" }
[outputs]
dialogue_response = { type = "str" }
"""


def build_bindings():
    """A compile-valid handler-only pipeline with node bindings + a pipeline service_bindings
    supply: returns ``(registry, pipeline)``. No trainable node."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(SERVICE_TYPE_LLM, "service_type", file_path="st.toml"))
    reg.add_handler("acme.normalize", loads(TRANSFORM_NORMALIZE, "handler", file_path="norm.toml"))
    reg.add_handler("acme.respond", loads(SERVICE_RESPOND, "handler", file_path="respond.toml"))
    reg.add_handler("acme.log", loads(HOOK_LOG, "handler", file_path="log.toml"))
    pipeline = loads(PIPELINE_BINDINGS, "pipeline", file_path="p.toml")
    return reg, pipeline


def build_trainable():
    """A compile-valid pipeline embedding a trainable composition whose backend service-type
    carries a ``description``: returns ``(registry, pipeline)``."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("acme.ctx", loads(TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg.add_handler("transform.formatter", loads(TRANSFORM_FORMATTER, "handler", file_path="fmt.toml"))
    reg.add_composition(
        "trainables/dialogue.toml", loads(TRAINABLE_COMPOSITION, "composition", file_path="c.toml")
    )
    pipeline = loads(PIPELINE_WITH_COMPOSITION, "pipeline", file_path="p.toml")
    return reg, pipeline
