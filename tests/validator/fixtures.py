"""Shared canon-grounded declaration fixtures for the Phase-1a validator tests.

Declaration TOMLs **only** — no real handler modules / callables (stages 1–2 need none).
Each fixture is lifted from or modeled on a canon worked example
(``components/handler/reference.md`` § Worked examples; ``components/service-type`` +
``components/deployment`` § Worked example). ``build_base()`` is the valid composition the
negative tests perturb one piece at a time.
"""

from __future__ import annotations

from conjured.validator import DeclarationRegistry, loads

# --- Service-type (service-type/reference.md § Worked example, trimmed) -------------------
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

# --- Handlers (handler/reference.md § Worked examples) -----------------------------------
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

# A hook handler referenced by a hook PREPROCESSOR (name-reference model). Reads `observed`,
# emits via a `log_path` transport_schema (divergence C — the hook preprocessor's transport
# resolves from THIS referenced declaration, never inlined on the [[preprocessors]] entry).
HOOK_AUDIT = """
[hook]
[reads]
observed = { type = "str" }
[service_bindings]
[transport_schema]
log_path = { type = "str" }
"""

PIPELINE = """
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
# The binding's config block — the [config_schema] value supply for a service-typed
# binding outside the trainable kind (pipeline/reference.md § service_bindings.<name>);
# the bound service-type declares `temperature` with no ship-time default, so coverage
# requires the supply.
[service_bindings.llm.config]
temperature = 0.7
[inputs]
player_input = { type = "str" }
[outputs]
dialogue = { type = "str" }
"""

DEPLOYMENT = """
[transport.llm]
endpoint = "https://llm.prod.internal/v1"
[hook_transport."acme.log"]
path = "/var/log/conjured/audit.jsonl"
[training_contract]
integrity_enforcement = true
"""

# --- Trainable composition (kind-schemas/trainable.schema.toml example shape) ------------
SERVICE_TYPE_DIALOGUE = """
name = "conjured_llm.dialogue"
[identity_schema]
model = { type = "str" }
[transport_schema]
endpoint = { type = "str" }
[config_schema]
temperature = { type = "float" }
max_tokens = { type = "int" }
"""

TRANSFORM_CTX = """
[transform]
[reads]
raw = { type = "str" }
[output_schema]
npc_state = { type = "str" }
user_message = { type = "str" }
"""

# The preprocessor's REFERENCED handler (name-reference model — the [[preprocessors]] entry
# resolves its ports + binding declarations from this registered handler, not inline). Declares
# a `config` schema-binding (object with a `template` field) the composition supplies a value for.
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
# Object binding supplied inline as a table (grammar-valid) — a VALUE for the `config` binding
# the referenced handler `transform.formatter` declares. Under the inline-scalar grammar a bare
# string is INLINE content and the explicit `{ file = "..." }` form is the external file; this
# inline-object form remains valid (an inline table = an inline object value).
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

NAME = "acme.dialogue"


def build_base():
    """A valid handler-only composition: returns (registry, pipeline, deployment)."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(SERVICE_TYPE_LLM, "service_type", file_path="st.toml"))
    reg.add_handler("acme.normalize", loads(TRANSFORM_NORMALIZE, "handler", file_path="h.norm.toml"))
    reg.add_handler("acme.respond", loads(SERVICE_RESPOND, "handler", file_path="h.respond.toml"))
    reg.add_handler("acme.log", loads(HOOK_LOG, "handler", file_path="h.log.toml"))
    pipeline = loads(PIPELINE, "pipeline", file_path="p.toml")
    deployment = loads(DEPLOYMENT, "deployment", file_path="d.toml")
    return reg, pipeline, deployment


def build_trainable():
    """A valid composition with an embedded trainable: returns (registry, pipeline)."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("acme.ctx", loads(TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg.add_handler("transform.formatter", loads(TRANSFORM_FORMATTER, "handler", file_path="fmt.toml"))
    reg.add_composition("trainables/dialogue.toml", loads(TRAINABLE_COMPOSITION, "composition", file_path="c.toml"))
    pipeline = loads(PIPELINE_WITH_COMPOSITION, "pipeline", file_path="p.toml")
    return reg, pipeline
