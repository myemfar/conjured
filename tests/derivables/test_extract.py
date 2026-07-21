"""Library-level tests for ``conjured.derivables.extract`` / ``serialize``.

Covers the derivables extraction contract's acceptance criteria: the canonized envelope
shape, byte-identical determinism, hashes equal to the real hashers, the pure-read
(zero-events) seal, and the compile-invalid error path. Each grounds in
``pipeline/reference.md`` § Pipeline derivables + § Bundle serialized form.
"""

from __future__ import annotations

import hashlib

import pytest

from conjured.derivables import BUNDLE_FORMAT, bundle_hash, extract, serialize
from conjured.errors import ContractViolation
from conjured.hasher import pipeline_hash, training_bundle_hash
from conjured.testing.events import capture_events
from conjured.validator import DeclarationRegistry, loads

from . import _fixtures as F

_VERSION = "9.9.9-test"

# The exact top-level member set canon fixes (pipeline/reference.md § Bundle serialized form:
# "Top-level members (all always present)").
_ENVELOPE_KEYS = {
    "bundle_format",
    "pipeline_hash",
    "conjured_version",
    "trainables",
    "binding_snapshot",
    "composition_snapshot",
}


def _extract_trainable():
    reg, pipeline = F.build_trainable()
    return extract(pipeline, reg, conjured_version=_VERSION), reg, pipeline


# --- Acceptance 1: the canonized envelope (keys, format integer, per-trainable content) -----


def test_envelope_top_level_keys_are_exactly_the_canon_set():
    bundle, _reg, _pipeline = _extract_trainable()
    assert set(bundle) == _ENVELOPE_KEYS


def test_bundle_format_is_the_integer_version():
    bundle, _reg, _pipeline = _extract_trainable()
    assert bundle["bundle_format"] == 1
    assert bundle["bundle_format"] == BUNDLE_FORMAT
    assert isinstance(bundle["bundle_format"], int)


def test_conjured_version_is_carried_verbatim():
    bundle, _reg, _pipeline = _extract_trainable()
    assert bundle["conjured_version"] == _VERSION


def test_trainable_entry_carries_the_per_trainable_components():
    bundle, _reg, _pipeline = _extract_trainable()
    # Keyed by the composition's declared meta name (the manifest key).
    entry = bundle["trainables"]["dialogue_training"]
    assert set(entry) == {"training_bundle_hash", "reads", "output_schema", "service_metadata"}
    # reads is a name-keyed shape (field order non-semantic); output_schema is an ordered list.
    assert entry["reads"]["formatted_prompt"]["type"] == {"kind": "primitive", "primitive": "str"}
    assert [f["name"] for f in entry["output_schema"]] == ["dialogue_response"]


def test_service_metadata_includes_the_service_type_description():
    # The defended failure mode (acceptance 1): the description is a bundle member now — a
    # trainable's backend service-type description reaches the generator as instruction context.
    bundle, _reg, _pipeline = _extract_trainable()
    meta = bundle["trainables"]["dialogue_training"]["service_metadata"]
    assert meta["service_type"] == "conjured_llm.dialogue"
    assert meta["description"] == (
        "A dialogue backend: given assembled context, emit an in-character reply."
    )


def test_service_metadata_description_is_null_when_undeclared():
    # description is Optional on the service-type; a backend without one folds JSON null, the
    # member still present (declared-structure-only).
    reg, pipeline = F.build_trainable()
    st = loads(
        'name="conjured_llm.dialogue"\n[identity_schema]\nmodel={type="str"}\n'
        '[transport_schema]\nendpoint={type="str"}\n[config_schema]\n'
        'temperature={type="float"}\nmax_tokens={type="int"}\n',
        "service_type", file_path="st.toml",
    )
    reg.service_types["conjured_llm.dialogue"] = st  # replace with a description-less twin
    bundle = extract(pipeline, reg, conjured_version=_VERSION)
    assert bundle["trainables"]["dialogue_training"]["service_metadata"]["description"] is None


def test_binding_snapshot_carries_effective_node_bindings_and_service_identity():
    """The snapshot folds the EFFECTIVE binding values through the hasher's own
    derivation (hash-model: the per-node contribution is the effective value,
    supplied-or-default, a single-field binding in its normalized BARE form) — so the
    snapshot and the pipeline-hash share one domain and cannot diverge."""
    reg, pipeline = F.build_bindings()
    bundle = extract(pipeline, reg, conjured_version=_VERSION)
    snap = bundle["binding_snapshot"]
    # The single-field `config` binding folds bare ("brackets", not {"marker_set": ...}) —
    # the same normalization the pipeline-hash folds (file ≡ inline ≡ bare).
    assert snap["node_bindings"] == [
        {"position": 0, "node": "acme.normalize", "bindings": {"config": "brackets"}}
    ]
    # The pipeline service_bindings identity supply (transport excluded — never in the bundle).
    assert snap["service_bindings"] == {
        "llm": {"type": "conjured_llm.structured_output", "identity": {"model": "qwen3.5-4b-gguf"}}
    }


def test_binding_snapshot_folds_an_omitted_ship_time_default():
    """RED-on-removal for the effective-value fold: a node OMITTING a default-bearing
    binding contributes the declared default — two pipeline-hash-identical compositions
    (explicit X vs defaulted X) produce one bundle (the snapshot-to-hash
    correspondence pipeline/reference.md § Pipeline-fixed binding snapshot pins)."""
    reg, pipeline = F.build_bindings()
    defaulted = loads(
        "[transform]\n[reads]\ndialogue = { type = \"str\" }\n"
        "[output_schema]\nstyled = { type = \"str\" }\n"
        "[bindings.style]\ndefault = \"neutral\"\ntone = { type = \"str\" }\n",
        "handler", file_path="styled.toml",
    )
    reg.add_handler("acme.style", defaulted, toml_path="styled.toml")
    pipeline = loads(
        F.PIPELINE_BINDINGS.replace(
            "[service_bindings.llm]",
            '[[nodes]]\nkind = "handler"\nname = "acme.style"\n[service_bindings.llm]',
        ),
        "pipeline", file_path="p.toml",
    )
    bundle = extract(pipeline, reg, conjured_version=_VERSION)
    entries = {e["node"]: e["bindings"] for e in bundle["binding_snapshot"]["node_bindings"]}
    # acme.style supplies nothing — its effective `style` value is the declared default,
    # in single-field bare form.
    assert entries["acme.style"] == {"style": "neutral"}


def test_non_finite_floats_refuse_serialization():
    """RED-on-removal for allow_nan=False: TOML 1.0 admits nan/inf, but NaN/Infinity
    are not RFC 8259 JSON — the one-JSON-object guarantee (§ Bundle serialized form)
    fails loud rather than emitting an artifact a strict parser rejects."""
    from conjured.canonical import canonical_json
    from conjured.derivables import serialize

    with pytest.raises(ValueError):
        canonical_json({"a": float("nan")})
    with pytest.raises(ValueError):
        serialize({"a": float("inf")})


def test_binding_snapshot_excludes_a_hook_only_supply_entry():
    """RED-on-removal for the non-hook supply domain: a service_bindings entry only a
    HOOK references is invisible to the pipeline-hash (hash-model: the supply table
    folds affirmatively over the non-hook graph), so the bundle excludes it — editing
    it must never change a bundle the hash calls identical."""
    reg, pipeline = F.build_bindings()
    hook_bound = loads(
        "[hook]\n[reads]\ndialogue = { type = \"str\" }\n"
        "[service_bindings]\nsink = { type = \"conjured_llm.structured_output\" }\n"
        "[transport_schema]\n",
        "handler", file_path="hooksink.toml",
    )
    reg.add_handler("acme.hooksink", hook_bound, toml_path="hooksink.toml")
    pipeline = loads(
        F.PIPELINE_BINDINGS.replace(
            "[service_bindings.llm]",
            '[[nodes]]\nkind = "handler"\nname = "acme.hooksink"\n'
            "[service_bindings.sink]\ntype = \"conjured_llm.structured_output\"\n"
            'model = "hook-only-model"\n'
            "[service_bindings.sink.config]\ntemperature = 0.1\n"
            "[service_bindings.llm]",
        ),
        "pipeline", file_path="p.toml",
    )
    bundle = extract(pipeline, reg, conjured_version=_VERSION)
    assert "sink" not in bundle["binding_snapshot"]["service_bindings"]
    assert "llm" in bundle["binding_snapshot"]["service_bindings"]


def test_composition_snapshot_records_node_list_order_and_wiring():
    bundle, _reg, _pipeline = _extract_trainable()
    snap = bundle["composition_snapshot"]
    assert snap["pipeline_name"] == "acme.dialogue"
    assert snap["nodes"] == [
        {"order": 0, "kind": "handler", "name": "acme.ctx", "reads_map": {}, "writes_map": {}},
        {
            "order": 1, "kind": "composition", "name": "trainables/dialogue.toml",
            "composition_kind": "trainable", "meta_name": "dialogue_training",
        },
    ]


def test_bundle_with_no_trainable_has_an_empty_trainables_member():
    reg, pipeline = F.build_bindings()
    bundle = extract(pipeline, reg, conjured_version=_VERSION)
    assert bundle["trainables"] == {}


def test_hooks_are_excluded_from_both_snapshots():
    # The build_bindings pipeline is [normalize(0), respond(1), log(2)] where `acme.log` is a
    # hook. Hooks contribute to neither hash, so both snapshots scope to the non-hook domain the
    # pipeline-hash covers — the hook must not appear, and the non-hook order is contiguous.
    reg, pipeline = F.build_bindings()
    bundle = extract(pipeline, reg, conjured_version=_VERSION)
    comp_nodes = bundle["composition_snapshot"]["nodes"]
    assert [n["name"] for n in comp_nodes] == ["acme.normalize", "acme.respond"]  # no acme.log
    assert [n["order"] for n in comp_nodes] == [0, 1]  # contiguous, gap-free
    # And no hook slips into the binding snapshot either.
    assert all(nb["node"] != "acme.log" for nb in bundle["binding_snapshot"]["node_bindings"])


def test_hook_edit_alone_leaves_pipeline_hash_and_composition_snapshot_stable():
    # The 1:1 snapshot↔pipeline-hash correspondence canon commits to: editing ONLY a hook must
    # not change the pipeline-hash — and, now that hooks are excluded, must not change the
    # composition snapshot either (the defect was a hook-only edit shifting the snapshot while
    # the hash stayed byte-identical).
    reg_a, pipe_a = F.build_bindings()
    reg_b, pipe_b = F.build_bindings()
    # Swap the hook's transport-schema field name on B (a hook-only edit — hash-excluded).
    reg_b.add_handler("acme.log", loads(
        '[hook]\n[reads]\ndialogue={type="str"}\n[service_bindings]\n'
        '[transport_schema]\nsink_path={type="str"}\n', "handler", file_path="log.toml",
    ))
    bundle_a = extract(pipe_a, reg_a, conjured_version=_VERSION)
    bundle_b = extract(pipe_b, reg_b, conjured_version=_VERSION)
    assert bundle_a["pipeline_hash"] == bundle_b["pipeline_hash"]
    assert bundle_a["composition_snapshot"] == bundle_b["composition_snapshot"]


def test_unresolved_hook_binding_does_not_slip_in_as_a_silent_null():
    # RED-on-removal for the hook-exclusion fix (refuter finding 2): a hook carrying an
    # unresolved external-file binding must NOT fold into binding_snapshot as a null. Because
    # hooks are excluded, extract succeeds (pipeline_hash skips the hook too) and no null appears
    # — if the hook exclusion were removed, _binding_value would emit `null` here.
    reg = DeclarationRegistry()
    reg.add_handler("acme.emit", loads(
        '[transform]\n[reads]\nplayer_input={type="str"}\n[output_schema]\ndialogue={type="str"}\n',
        "handler", file_path="emit.toml",
    ))
    reg.add_handler("acme.audit", loads(
        '[hook]\n[reads]\ndialogue={type="str"}\n[service_bindings]\n'
        '[transport_schema]\npath={type="str"}\n[bindings.opts]\nlabel={type="str"}\n',
        "handler", file_path="audit.toml",
    ))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.emit"\n'
        '[[nodes]]\nkind="handler"\nname="acme.audit"\n'
        'bindings = { opts = { file = "nope_unresolved.toml" } }\n'
        '[inputs]\nplayer_input={type="str"}\n',
        "pipeline", file_path="p.toml",
    )
    bundle = extract(pipeline, reg, conjured_version=_VERSION)  # no exception, no resolution run
    node_bindings = bundle["binding_snapshot"]["node_bindings"]
    assert all(nb["node"] != "acme.audit" for nb in node_bindings)  # hook not present
    # No null value anywhere in any node's bindings.
    assert all(v is not None for nb in node_bindings for v in nb["bindings"].values())


# --- Acceptance 2: byte-identical determinism (the reproducibility anchor) -------------------


def test_serialization_is_byte_identical_across_independent_extractions():
    # Two independent builds of the same declarations (distinct object instances) must serialize
    # byte-for-byte identically — the reproducibility anchor the generator_prompt_hash pairing
    # rests on. Defends against nondeterminism (dict ordering, unsorted keys).
    reg_a, pipe_a = F.build_trainable()
    reg_b, pipe_b = F.build_trainable()
    text_a = serialize(extract(pipe_a, reg_a, conjured_version=_VERSION))
    text_b = serialize(extract(pipe_b, reg_b, conjured_version=_VERSION))
    assert text_a == text_b
    assert text_a.encode("utf-8") == text_b.encode("utf-8")
    # The provenance pin inherits the determinism: same declarations → same bundle hash.
    assert bundle_hash(text_a) == bundle_hash(text_b)


def test_serialized_object_keys_are_sorted():
    bundle, _reg, _pipeline = _extract_trainable()
    text = serialize(bundle)
    # Top-level keys appear in sorted order in the serialized text.
    import json
    reparsed = json.loads(text)
    assert list(reparsed) == sorted(reparsed)


# --- Acceptance 3: hashes equal the real hasher's output ------------------------------------


def test_pipeline_hash_equals_the_real_hasher():
    bundle, reg, pipeline = _extract_trainable()
    assert bundle["pipeline_hash"] == pipeline_hash(pipeline, reg)


def test_training_bundle_hash_equals_the_real_hasher():
    bundle, reg, _pipeline = _extract_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    assert (
        bundle["trainables"]["dialogue_training"]["training_bundle_hash"]
        == training_bundle_hash(comp, reg)
    )


# --- Acceptance 4: extraction provably dispatches nothing (the pure-read seal) --------------


def test_extraction_emits_zero_canonical_events():
    # RED-on-removal: extraction is compose-time / pure-read (no runner.run). If extract ever
    # grew a dispatch, the canonical event stream would be non-empty and this test would fail —
    # the structural guard on the "No service invocations occur; no handlers dispatch" seal.
    reg, pipeline = F.build_trainable()
    with capture_events() as events:
        extract(pipeline, reg, conjured_version=_VERSION)
    assert events == []


# --- Acceptance 5 (library slice): a compile-invalid pipeline fails loud, unchanged ----------


def test_compile_invalid_pipeline_raises_contract_violation():
    # The verification-path seal: extract runs the REAL compile first, so a pipeline whose node
    # references an unregistered handler surfaces the engine's structured ContractViolation
    # before any bundle is built — never a partial or best-effort bundle.
    reg = DeclarationRegistry()
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.missing"\n'
        '[inputs]\nx={type="str"}\n',
        "pipeline", file_path="p.toml",
    )
    with pytest.raises(ContractViolation) as exc:
        extract(pipeline, reg, conjured_version=_VERSION)
    assert exc.value.check.value == "handler-name-resolution"


def test_unresolved_non_hook_binding_fails_loud_before_the_snapshot():
    # The ordering seal (refuter finding 2): pipeline_hash is folded BEFORE the snapshots, so a
    # non-hook node's unresolved external-file binding fails loud (the hasher's external-binding
    # backstop) rather than reaching _binding_snapshot — never a silent path in the bundle.
    reg = DeclarationRegistry()
    reg.add_handler("acme.norm", loads(
        '[transform]\n[reads]\nplayer_input={type="str"}\n[output_schema]\nout={type="str"}\n'
        '[bindings.cfg]\nmarker={type="str"}\n', "handler", file_path="n.toml",
    ))
    pipeline = loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.norm"\n'
        'bindings = { cfg = { file = "nope.toml" } }\n'
        '[inputs]\nplayer_input={type="str"}\n[outputs]\nout={type="str"}\n',
        "pipeline", file_path="p.toml",
    )
    with pytest.raises(ContractViolation) as exc:
        extract(pipeline, reg, conjured_version=_VERSION)  # no resolution run → hasher fails loud
    assert exc.value.check.value == "external-binding-content-unsupported"


# --- The provenance pin: the description rides the bundle, outside both structural hashes ----


# verifies: derivables-bundle-hash-provenance-pin
def test_description_edit_shifts_the_bundle_hash_but_neither_structural_hash():
    """The decision-of-record (2026-07-09, the derivables-bundle-hash arc): the bound
    service-type's top-level ``description`` is generation-time conditioning — it rides the
    bundle, so the ``derivables_bundle_hash`` provenance pin detects an edit — and folds into
    NEITHER structural hash (a description edit is not a contract change; hash-model § What
    the pipeline-hash absorbs owns the exclusion). RED if the description stops riding the
    bundle, if it is ever folded into a structural hash without a conscious decision, or if
    ``bundle_hash`` stops covering the serialized bytes."""
    bundle_a, _reg, _pipeline = _extract_trainable()

    reg_b = DeclarationRegistry()
    reg_b.add_service_type(loads(
        F.SERVICE_TYPE_DIALOGUE.replace(
            "A dialogue backend: given assembled context, emit an in-character reply.",
            "A REWORDED dialogue backend description.",
        ),
        "service_type", file_path="st.toml",
    ))
    reg_b.add_handler("acme.ctx", loads(F.TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg_b.add_handler(
        "transform.formatter", loads(F.TRANSFORM_FORMATTER, "handler", file_path="fmt.toml")
    )
    reg_b.add_composition(
        "trainables/dialogue.toml",
        loads(F.TRAINABLE_COMPOSITION, "composition", file_path="c.toml"),
    )
    pipeline_b = loads(F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path="p.toml")
    bundle_b = extract(pipeline_b, reg_b, conjured_version=_VERSION)

    # The edit reaches the generator: the bundle differs, so the provenance pin moves.
    assert bundle_b["trainables"]["dialogue_training"]["service_metadata"]["description"] == (
        "A REWORDED dialogue backend description."
    )
    assert bundle_hash(serialize(bundle_a)) != bundle_hash(serialize(bundle_b))
    # ...while NEITHER structural hash moves: a description edit is not a contract change.
    assert bundle_a["pipeline_hash"] == bundle_b["pipeline_hash"]
    assert (
        bundle_a["trainables"]["dialogue_training"]["training_bundle_hash"]
        == bundle_b["trainables"]["dialogue_training"]["training_bundle_hash"]
    )
    # The pin is byte-exact sha256 over the serialized artifact, in the engine's prefixed form.
    s = serialize(bundle_a)
    assert bundle_hash(s) == "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


# --- Pure-substitution embeds: extraction sees the post-substitute composition ---------------


def test_extract_enumerates_a_bundle_embedded_trainable():
    """A trainable reached THROUGH a pure-substitution bundle embed IS a trainable of
    this pipeline (glossary § Bundle TOML: extraction operates on the post-substitute
    inlined form) — enumerated in `trainables`, with the pipeline_hash identical to the
    direct embed (the bundle is invisible to hashing)."""
    reg, direct_pipeline = F.build_trainable()
    reg.add_composition(
        "bundles/tp.toml",
        loads(
            '[meta]\nkind = "bundle"\nname = "trainable_prep"\n'
            '[[nodes]]\nkind = "composition"\nname = "trainables/dialogue.toml"\n',
            "composition", file_path="tp.toml",
        ),
    )
    via_bundle = loads(
        F.PIPELINE_WITH_COMPOSITION.replace(
            'name = "trainables/dialogue.toml"', 'name = "bundles/tp.toml"'
        ),
        "pipeline", file_path="p2.toml",
    )
    bundled = extract(via_bundle, reg, conjured_version=_VERSION)
    direct = extract(direct_pipeline, reg, conjured_version=_VERSION)
    assert "dialogue_training" in bundled["trainables"]
    assert bundled["pipeline_hash"] == direct["pipeline_hash"]
