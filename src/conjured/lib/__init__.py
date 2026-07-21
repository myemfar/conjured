"""``conjured.lib`` — the native library: the engine-shipped handler catalog
(``conjured/docs/components/native-library/reference.md``).

Every member is referenced by its **qualified dotted path** under this namespace and
resolves through the same dotted-path resolution as third-party code — the engine
registers **no entry-point short names** for its own members (§ Naming: a bare short
name would squat the global short-name space, and the natives must be resolved,
audited, and shape-checked exactly as foreign code is, keeping the resolution
machinery's verification surface single).

Members ship Pattern B: one TOML declaration + one Python module + one audit entry per
member. For a **trainable-backend adapter** member the TOML is its *service-type
declaration* (the contract a ``trainable.service_bindings`` entry's ``type`` names),
shipped as a same-named sibling of the adapter module; the implementation class
resolves by dotted path (``conjured.lib.<module>.<Class>``).

**Realized members (added as they ship — the catalog list is not enumerated ahead of
the code):**

- ``conjured.lib.openai_compatible_trainable`` — the OpenAI-compatible
  structured-output trainable backend (most self-hosted serving runtimes speaking that
  wire form).
- ``conjured.lib.gbnf_trainable`` — the llama.cpp / GBNF grammar trainable backend
  (the GGUF family).
- ``conjured.lib.blob_reference_emitter`` — the blob-reference rendering hook (a
  stdlib-emission hook emitting a binary blob's path/hash reference for a downstream
  consumer to render); resolves as ``conjured.lib.blob_reference_emitter.emit``.
"""

from types import MappingProxyType

#: Service-type qualified name → dotted implementation path, for the engine's own
#: trainable-backend adapters (one implementation per service-type qualified name,
#: R-service-type-004; resolved through the ordinary dotted-path sibling mechanism —
#: no entry points). **Adapter resolution** consults this table
#: (``validator.resolve_adapter``): a binding naming a native qualified name resolves the
#: mapped class path through the audited dotted-path leg, ahead of the entry-points leg, so
#: the native identity is unshadowable (``handler-resolution.md`` § Resolution mechanism —
#: Native adapters). Immutable by construction (module-level mutable state is sealed out —
#: R-handler-pure-module); this module imports only stdlib, so the consult's import is
#: cycle-safe.
NATIVE_TRAINABLE_ADAPTERS = MappingProxyType(
    {
        "conjured.lib.openai_compatible_trainable": (
            "conjured.lib.openai_compatible_trainable.OpenAICompatibleTrainable"
        ),
        "conjured.lib.gbnf_trainable": "conjured.lib.gbnf_trainable.GBNFTrainable",
    }
)
