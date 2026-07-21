"""Golden + property tests for the Phase-1b hash machinery (prompt deliverable 3).

The hashes are the integrity floor — invariant I4 rests on them — so this suite is the
**behavioral** half of the verification floor: pinned **golden** values (drift detector),
**lexical-neutrality** (two spellings of one graph → one hash), absorbed-field **sensitivity**
(any structural edit moves the hash), excluded-field **neutrality** (identity/metadata/wiring
that canon excludes does NOT move it — including the **`meta.name` rename**, the family rule),
and **determinism** (stable across repeats + a re-imported module).

Fixtures reuse the canon-example declarations in ``tests/validator/fixtures.py`` (the same
worked examples the validator suite grounds in). A binding value is **inline content** by
default (a bare scalar is the value itself, an inline table an inline object); the explicit
``{ file = "<path>" }`` form is the external-declaration-file reference (handler/reference.md
§ Binding value-supply grammar), exercised by ``test_unresolved_external_file_binding_raises``.
"""

from __future__ import annotations

import importlib

import pytest

from conjured.errors import Check, ContractViolation
from conjured.hasher import pipeline_hash, training_bundle_hash
from conjured.validator import DeclarationRegistry, loads

from tests.validator import fixtures as F


# ---------------------------------------------------------------------------
# Golden values — canon-example pipelines/compositions → pinned hashes
# ---------------------------------------------------------------------------
# Drift detectors: a change to the canonicalization algorithm, the absorbed/excluded sets,
# or a canon fixture moves these. Cross-engine-version stability is explicitly NOT promised
# (hash-model.md § What the hash model does NOT promise) — these pin the *current* algorithm.

# NOT re-pinned by the description-hash arc (2026-07-04 code pass) — verified UNCHANGED.
#   That arc REFRAMED a schema field's `description` from metadata-class-excluded to
#   model-facing contract content HASHED where admitted (a trainable's `trainable.output_schema`
#   field), superseding the "Fix 3 hash-description-exclusion" framing below. The goldens did NOT
#   move because the shared canon-example fixtures carry NO `description` at the one folding
#   position (their trainable `output_schema` fields are bare); the fold is a no-op over them, so
#   the canonical bytes are byte-identical. The sensitivity of the new fold is proved by
#   `test_sensitivity_field_description_shifts_both_hashes` (RED-on-removal), not by a golden.
#   Superseded (2026-06-22 Fix 3, `hash-description-exclusion`): under that pass a service-binding
#   DECLARATION's prose `description` was excluded from the fold; the reframe removes the key from
#   the declaration grammar entirely (a service-binding decl is closed to `{type}`), so there is
#   no longer a description there to fold or exclude — the fold sites are unchanged and the values
#   below still hold.
# Re-pinned (2026-07-04, the binding-delivery-normalization arc): a SINGLE-FIELD binding's
#   effective value now folds in its NORMALIZED (bare) form regardless of supply spelling
#   (validator.normalize.normalize_binding_value — the compose-join normalization). The shared
#   canon fixtures supply single-field bindings as one-field TABLES — the base pipeline's
#   `acme.normalize` `config` binding (`{ marker_set = "brackets" }` → `"brackets"`) and the
#   trainable preprocessor's `transform.formatter` `config` binding
#   (`{ template = "..." }` → `"..."`) — so their fold changes from the field-keyed dict to the
#   bare value. ALL THREE goldens move (the base one embeds the single-field `config` supply); an
#   EXPECTED drift-detector re-pin (the documented normalization re-pin class). File ≡ inline ≡
#   bare hash-equivalence is proved by `test_single_field_binding_hash_equivalence_across_routes`;
#   the field name still reaches the hash via the declaration-side schema fold
#   (`test_single_field_binding_field_rename_still_shifts_hash`).
# Prior re-pins folded into the values below (2026-06-11 pending-batch): the P9
# order-semantic `trainable.output_schema` fold, the effective config-supply fold, and the
# `[[preprocessors]]` entry re-key.
# Re-pinned (2026-06-30, the preprocessor-handler-parity mirror-fix): a `[[preprocessors]]`
#   entry is now a NAME-REFERENCE — the TBH folds the REFERENCED handler's resolved declaration
#   (ports + binding declarations + effective binding values, via the shared
#   `_canon_resolved_handler_node`) instead of the entry's deleted inline ports + synthesized
#   COPY-only bindings. The fold now includes real declaration content (the `transform.formatter`
#   handler the fixture registers), so the TRAINABLE pipeline-hash + the training-bundle-hash both
#   shift — an EXPECTED drift-detector re-pin, the structural-guard payoff (delivery/default/
#   validation now fold for a preprocessor exactly as for an outer node). GOLDEN_BASE is unchanged
#   (the base pipeline embeds no composition).
GOLDEN_BASE_PIPELINE_HASH = "sha256:2c12cd8563343ead3cec4aed91cae039490748d700f9f1342f247cc7838ec4dc"
GOLDEN_TRAINABLE_PIPELINE_HASH = "sha256:40ba26f290be32a5f5e138f03a528c78d043dbe923ec0ecc6505658a06f1f479"
GOLDEN_TRAINING_BUNDLE_HASH = "sha256:cd7afff0288d6c2e88dfb73c417291ccfc71a722a64411911b8ad1ce857756c0"


def test_golden_base_pipeline_hash():
    reg, pipeline, _ = F.build_base()
    assert pipeline_hash(pipeline, reg) == GOLDEN_BASE_PIPELINE_HASH


def test_golden_trainable_pipeline_and_bundle_hashes():
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    assert pipeline_hash(pipeline, reg) == GOLDEN_TRAINABLE_PIPELINE_HASH
    assert training_bundle_hash(comp, reg) == GOLDEN_TRAINING_BUNDLE_HASH


def test_hash_shape():
    """Every hash is the canonical ``sha256:<64-hex>`` wire form."""
    reg, pipeline, _ = F.build_base()
    h = pipeline_hash(pipeline, reg)
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64
    int(h.split(":", 1)[1], 16)  # the hex body parses


# ---------------------------------------------------------------------------
# Determinism — same input → identical output (repeat + re-imported module)
# ---------------------------------------------------------------------------


def test_determinism_repeat():
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    assert pipeline_hash(pipeline, reg) == pipeline_hash(pipeline, reg)
    assert training_bundle_hash(comp, reg) == training_bundle_hash(comp, reg)


def test_determinism_across_reimport():
    """A re-imported hasher module produces identical hashes (no module-load-order or
    process-state dependence)."""
    reg, pipeline, _ = F.build_base()
    before = pipeline_hash(pipeline, reg)
    import conjured.hasher.hashes as h
    importlib.reload(h)
    assert h.pipeline_hash(pipeline, reg) == before


# ---------------------------------------------------------------------------
# Lexical-neutrality — two spellings of one declared graph → the same hash
# ---------------------------------------------------------------------------


def _two_field_handler(order: str) -> str:
    # A transform `reads` field carries no `description` (admitted only on a trainable's
    # `output_schema` — the family rule); the two fields differ by name/type/order alone,
    # which is all the field-order-neutrality + validator-sensitivity tests below need.
    a = 'a = { type = "str" }'
    b = 'b = { type = "int" }'
    fields = f"{a}\n{b}" if order == "ab" else f"{b}\n{a}"
    return f'[transform]\n[reads]\n{fields}\n[output_schema]\no = {{ type = "str" }}'


def _pipeline_with(handler_toml: str, *, reads_map: str = "") -> tuple[DeclarationRegistry, object]:
    reg = DeclarationRegistry()
    reg.add_handler("acme.h", loads(handler_toml, "handler", file_path="h.toml"))
    node = '[[nodes]]\nkind = "handler"\nname = "acme.h"\n' + reads_map
    pipe = loads(
        f'[meta]\nname = "acme.p"\n{node}[inputs]\na = {{ type = "str" }}\nb = {{ type = "int" }}\n',
        "pipeline", file_path="p.toml")
    return reg, pipe


def test_lexical_neutrality_schema_field_order():
    """Reordering a schema's fields (a TOML table — key order non-semantic) is hash-neutral."""
    reg_ab, pipe_ab = _pipeline_with(_two_field_handler("ab"))
    reg_ba, pipe_ba = _pipeline_with(_two_field_handler("ba"))
    assert pipeline_hash(pipe_ab, reg_ab) == pipeline_hash(pipe_ba, reg_ba)


def test_lexical_neutrality_identity_map_sugar():
    """An omitted reads_map (all-identity) and a written-out identity reads_map desugar to the
    same normalized wiring → the same hash (the desugar runs before canonicalization)."""
    reg_sugar, pipe_sugar = _pipeline_with(_two_field_handler("ab"))
    reg_explicit, pipe_explicit = _pipeline_with(
        _two_field_handler("ab"), reads_map='reads_map = { a = "a", b = "b" }\n')
    assert pipeline_hash(pipe_sugar, reg_sugar) == pipeline_hash(pipe_explicit, reg_explicit)


def test_lexical_neutrality_tbh_preprocessor_map_sugar():
    """At the composition level: a preprocessor's omitted reads_map vs a written-out identity
    reads_map → the same training-bundle-hash (the TBH desugars preprocessor maps)."""
    reg, _ = F.build_trainable()  # supplies the bound service-type the TBH resolves
    base = F.TRAINABLE_COMPOSITION
    # The fixture's preprocessor maps context/utterance non-identically; build an identity-wired
    # variant two ways (omitted vs explicit) and confirm equal TBH.
    omitted = base.replace(
        'reads_map = { context = "npc_state", utterance = "user_message" }\n', ""
    ).replace('writes_map = { prompt = "formatted_prompt" }\n', "").replace(
        "context = { type", "npc_state = { type").replace(
        "utterance = { type", "user_message = { type").replace(
        "prompt = { type", "formatted_prompt = { type").replace(
        "formatted_prompt = { type = \"str\" }\n[trainable]", "formatted_prompt = { type = \"str\" }\n[trainable]")
    explicit = omitted  # build the explicit-identity twin from the omitted text
    # Add written-out identity maps over the now same-named ports.
    explicit = explicit.replace(
        '[[preprocessors]]\nkind = "handler"\nname = "transform.formatter"\nid   = "assemble_prompt"\n',
        '[[preprocessors]]\nkind = "handler"\nname = "transform.formatter"\nid   = "assemble_prompt"\n'
        'reads_map = { npc_state = "npc_state", user_message = "user_message" }\n'
        'writes_map = { formatted_prompt = "formatted_prompt" }\n')
    assert explicit != omitted  # the twin really carries the written-out maps
    c_omitted = loads(omitted, "composition", file_path="c.toml")
    c_explicit = loads(explicit, "composition", file_path="c.toml")
    assert training_bundle_hash(c_omitted, reg) == training_bundle_hash(c_explicit, reg)


# ---------------------------------------------------------------------------
# Sensitivity — a change to any ABSORBED field → a different hash
# ---------------------------------------------------------------------------


def test_sensitivity_binding_value():
    """A pipeline-entry binding value is absorbed — changing it moves the pipeline-hash."""
    reg, pipeline, _ = F.build_base()
    base = pipeline_hash(pipeline, reg)
    n0 = pipeline.nodes[0]
    from conjured.ir.common import InlineBindingValue
    changed = pipeline.model_copy(update={"nodes": (
        n0.model_copy(update={"bindings": (InlineBindingValue(name="config", value={"marker_set": "curly"}),)}),
    ) + pipeline.nodes[1:]})
    assert pipeline_hash(changed, reg) != base


def test_sensitivity_binding_delivery_selector():
    """A handler binding's `delivery` selector (COPY vs REFERENCE) folds into the
    handler-declaration content hash (`_canon_binding_decl_body`:
    `"delivery": body.delivery.value`). COPY hands each dispatch a fresh deep copy (the
    vector-4 isolation seal); REFERENCE deep-freezes the value once and shares it — a
    semantically distinct delivery contract that MUST shift the pipeline-hash of every
    composition resolving the handler. RED if the `"delivery"` fold key is dropped from
    `_canon_binding_decl_body` (both deliveries would then fold identically → equal hashes).
    Sibling to the `streamable`-exclusion tests (delivery is folded; streamable is excluded)."""
    from conjured.ir.channel_types import FieldDecl, primitive
    from conjured.ir.common import Binding, Delivery, SchemaBinding
    from conjured.ir.handler import TransformDeclaration

    def reg_with(delivery):
        r = DeclarationRegistry()
        r.add_handler("acme.h", TransformDeclaration(
            reads=(),
            output_schema=(FieldDecl(name="o", type=primitive("str")),),
            bindings=(Binding(name="cfg", body=SchemaBinding(
                fields=(FieldDecl(name="marker", type=primitive("str")),),
                delivery=delivery,
            )),),
        ))
        return r

    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "handler"\nname = "acme.h"\n'
        'bindings = { cfg = { marker = "x" } }\n[inputs]\nx = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    copy_hash = pipeline_hash(pipe, reg_with(Delivery.COPY))
    ref_hash = pipeline_hash(pipe, reg_with(Delivery.REFERENCE))
    assert copy_hash != ref_hash


def test_sensitivity_node_order():
    """Handler order is absorbed — swapping two non-hook nodes moves the pipeline-hash."""
    reg, pipeline, _ = F.build_base()
    base = pipeline_hash(pipeline, reg)
    swapped = pipeline.model_copy(update={"nodes": (pipeline.nodes[1], pipeline.nodes[0]) + pipeline.nodes[2:]})
    assert pipeline_hash(swapped, reg) != base


def test_sensitivity_outer_rewiring_moves_pipeline_hash():
    """A reads_map re-wiring on an outer node is absorbed by the pipeline-hash."""
    reg, pipeline = F.build_trainable()
    base = pipeline_hash(pipeline, reg)
    n0 = pipeline.nodes[0]  # acme.ctx
    rewired = n0.model_copy(update={"reads_map": {"raw": "raw_renamed"}})
    # also declare the new input so it still compiles-conceptually; hash doesn't validate, but
    # keep the graph coherent for the test's intent.
    changed = pipeline.model_copy(update={"nodes": (rewired,) + pipeline.nodes[1:]})
    assert pipeline_hash(changed, reg) != base


def test_sensitivity_pipeline_inputs_shifts_the_pipeline_hash():
    """The pipeline-level `[inputs]` API boundary is absorbed (hash-model.md § Pipeline-level
    `[inputs]` / `[outputs]` API boundary; the fold at `hashes.py` `"inputs":
    canon_schema(pipeline.inputs)`). GAP-FILL (18#2): this fold had only GOLDEN coverage — this is
    the direct sensitivity adversary. Editing an input field's type moves the pipeline-hash. RED
    against a stubbed constant `inputs` fold (both variants would then collapse to one hash)."""
    from conjured.ir.channel_types import FieldDecl, primitive
    reg, pipeline, _ = F.build_base()
    base = pipeline_hash(pipeline, reg)
    # The base pipeline's [inputs] is `player_input = { type = "str" }`; retype str -> int.
    changed = pipeline.model_copy(
        update={"inputs": (FieldDecl(name="player_input", type=primitive("int")),)})
    assert pipeline_hash(changed, reg) != base


def test_sensitivity_pipeline_outputs_shifts_the_pipeline_hash():
    """The pipeline-level `[outputs]` API commitment is absorbed — its shape is composition
    structure (its presence/absence changes the pipeline's external contract + replay identity;
    hash-model.md). GAP-FILL (18#2) for the `"outputs"` fold (`hashes.py` line ~668), goldens-only
    before. Editing an output field's type moves the pipeline-hash. RED against a stubbed constant
    `outputs` fold."""
    from conjured.ir.channel_types import FieldDecl, primitive
    reg, pipeline, _ = F.build_base()
    base = pipeline_hash(pipeline, reg)
    # The base pipeline's [outputs] is `dialogue = { type = "str" }`; retype str -> int.
    changed = pipeline.model_copy(
        update={"outputs": (FieldDecl(name="dialogue", type=primitive("int")),)})
    assert pipeline_hash(changed, reg) != base


def test_sensitivity_tbh_preprocessor_rewiring():
    """A preprocessor's INTERNAL reads_map is content-determining (it routes which boundary
    input feeds which port) — it folds into the TBH, so re-wiring it moves the TBH
    (hash-model.md § Training-bundle-hash; must-confirm 2)."""
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    base = training_bundle_hash(comp, reg)
    pp = comp.preprocessors[0]
    rewired = pp.model_copy(update={"reads_map": {"context": "user_message", "utterance": "npc_state"}})
    changed = comp.model_copy(update={"preprocessors": (rewired,) + comp.preprocessors[1:]})
    assert training_bundle_hash(changed, reg) != base


def test_sensitivity_tbh_config_value():
    """A trainable.config generation-parameter value folds into the TBH."""
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    base = training_bundle_hash(comp, reg)
    cfg = dict(comp.trainable.config); cfg["temperature"] = 0.9
    changed = comp.model_copy(update={"trainable": comp.trainable.model_copy(update={"config": cfg})})
    assert training_bundle_hash(changed, reg) != base


def test_sensitivity_tbh_output_schema_type():
    """A trainable PORT shape (output_schema type) IS the training-record shape — it folds."""
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    base = training_bundle_hash(comp, reg)
    from conjured.ir.channel_types import FieldDecl, primitive
    new_out = (FieldDecl(name="dialogue_response", type=primitive("int")),)  # str -> int
    changed = comp.model_copy(update={"trainable": comp.trainable.model_copy(update={"output_schema": new_out})})
    assert training_bundle_hash(changed, reg) != base


def test_sensitivity_trainable_output_schema_entry_order_is_semantic():
    """The P9 order-semantic ruling (hash-model.md § Training-bundle-hash): the fold
    preserves entry order for a trainable's `trainable.output_schema` — the declared
    field order IS the enforced emission order (autoregressive conditioning), so a
    reorder is honestly a new training-bundle-hash. Non-trainable schemas stay
    name-keyed (test_lexical_neutrality_schema_field_order pins that side)."""
    from conjured.ir.channel_types import FieldDecl, primitive
    reg, _ = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    two = (
        FieldDecl(name="mood", type=primitive("str")),
        FieldDecl(name="dialogue_response", type=primitive("str")),
    )
    mood_first = comp.model_copy(
        update={"trainable": comp.trainable.model_copy(update={"output_schema": two})}
    )
    dialogue_first = comp.model_copy(
        update={"trainable": comp.trainable.model_copy(update={"output_schema": two[::-1]})}
    )
    assert training_bundle_hash(mood_first, reg) != training_bundle_hash(dialogue_first, reg)


def _nested_output(order: str):
    """A trainable `output_schema` = one field whose type is a NESTED object with two members,
    in `order` (`ab` or `ba`). The wire compiles a nested object's members in declared order too,
    so member order is emission order (recursively)."""
    from conjured.ir.channel_types import FieldDecl, nested, primitive
    members = (
        FieldDecl(name="text", type=primitive("str")),
        FieldDecl(name="tone", type=primitive("str")),
    )
    members = members if order == "ab" else members[::-1]
    return (FieldDecl(name="dialogue_response", type=nested(*members)),)


# verifies: nested-output-schema-order-is-semantic
def test_sensitivity_nested_output_schema_member_order_shifts_both_hashes():
    """18#1 — the arc's Fix-B seal: a NESTED object's member order inside a trainable's
    `trainable.output_schema` is contract, not authoring convention, EXACTLY as the top-level field
    order is (`test_sensitivity_trainable_output_schema_entry_order_is_semantic` pins the top
    level). The bound wire form compiles a nested object's members, in declared order, into the
    backend's decode constraint (adapters/wire.py preserves member order; adapters/gbnf.py renders
    the object rule's keys in that order — a sequential grammar), so a nested reorder is honestly a
    new training-bundle-hash AND a new pipeline-hash (the pipeline folds the TBH by reference). The
    adversary: two trainable compositions differing ONLY in the order of two nested members.
    RED-on-removal — with `canon_type`'s NestedType arm reverted to the name-keyed `canon_schema`,
    both fold to the same order-neutral map and the asserts collapse to equality. Defends against a
    hash-invisible emission-contract change."""
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    comp_ab = comp.model_copy(
        update={"trainable": comp.trainable.model_copy(update={"output_schema": _nested_output("ab")})})
    comp_ba = comp.model_copy(
        update={"trainable": comp.trainable.model_copy(update={"output_schema": _nested_output("ba")})})

    # TBH leg — the nested member order folds into the training-bundle-hash.
    assert training_bundle_hash(comp_ab, reg) != training_bundle_hash(comp_ba, reg)

    # Pipeline-hash leg — the pipeline folds the trainable's TBH by reference, so the nested
    # reorder propagates to the outer pipeline-hash. Swap the composition in the registry and
    # recompute (the same registry-swap the description-seal + rename tests use).
    reg.compositions["trainables/dialogue.toml"] = comp_ab
    ph_ab = pipeline_hash(pipeline, reg)
    reg.compositions["trainables/dialogue.toml"] = comp_ba
    ph_ba = pipeline_hash(pipeline, reg)
    assert ph_ab != ph_ba


def test_nested_members_in_a_non_trainable_schema_stay_name_keyed():
    """The other side of the ordered fold (`ordered=False`, the default): a nested object inside a
    NON-trainable schema — a transform's `output_schema`, folded name-keyed via `canon_schema` —
    keeps its members order-NEUTRAL. Reordering nested members there is hash-neutral (nothing
    consumes their order: a bare-function handler returns a dict). Guards against the nested-order
    fold LEAKING into name-keyed positions — the counterpart of
    `test_sensitivity_nested_output_schema_member_order_shifts_both_hashes` and the nested-member
    analogue of `test_lexical_neutrality_schema_field_order`."""
    from conjured.ir.channel_types import FieldDecl, nested, primitive
    from conjured.ir.handler import TransformDeclaration

    def reg_with(order):
        members = (
            FieldDecl(name="text", type=primitive("str")),
            FieldDecl(name="tone", type=primitive("str")),
        )
        members = members if order == "ab" else members[::-1]
        r = DeclarationRegistry()
        r.add_handler("acme.t", TransformDeclaration(
            reads=(),
            output_schema=(FieldDecl(name="out", type=nested(*members)),),
            bindings=(),
        ))
        return r

    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "handler"\nname = "acme.t"\n'
        '[inputs]\nx = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    assert pipeline_hash(pipe, reg_with("ab")) == pipeline_hash(pipe, reg_with("ba"))


def test_sensitivity_trainable_composition_inputs_shifts_the_tbh():
    """18#3 — the trainable composition's boundary `[inputs]` is absorbed into the
    training-bundle-hash (`hashes.py` line ~481 `"inputs": canon_schema(comp.inputs)`; hash-model.md
    § Training-bundle-hash lists boundary `inputs` / `outputs`). GAP-FILL: this fold had only GOLDEN
    coverage. Retyping a boundary input field moves the TBH. RED against a stubbed constant `inputs`
    fold."""
    from conjured.ir.channel_types import FieldDecl, primitive
    reg, _ = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    base = training_bundle_hash(comp, reg)
    # The composition's [inputs] is (npc_state:str, user_message:str); retype the first str -> int.
    changed = comp.model_copy(update={"inputs": (
        FieldDecl(name="npc_state", type=primitive("int")),
        FieldDecl(name="user_message", type=primitive("str")),
    )})
    assert training_bundle_hash(changed, reg) != base


def test_sensitivity_trainable_composition_outputs_shifts_the_tbh():
    """18#3 — the trainable composition's boundary `[outputs]` IS the training-record shape its
    training-bundle-hash covers (`hashes.py` line ~482 `"outputs": canon_schema(comp.outputs)`;
    hash-model.md — a trainable's `[outputs]` is body-required BECAUSE it is the training-record
    shape). Distinct from the terminal node's `trainable.output_schema` fold
    (`test_sensitivity_tbh_output_schema_type`). GAP-FILL (goldens only before): editing a boundary
    output field's type moves the TBH. RED against a stubbed constant `outputs` fold."""
    from conjured.ir.channel_types import FieldDecl, primitive
    reg, _ = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    base = training_bundle_hash(comp, reg)
    changed = comp.model_copy(
        update={"outputs": (FieldDecl(name="dialogue_response", type=primitive("int")),)})
    assert training_bundle_hash(changed, reg) != base


# ---------------------------------------------------------------------------
# Config supply — the effective-value fold (supplied-or-default; § Hash placement)
# ---------------------------------------------------------------------------


def test_sensitivity_supply_config_value_moves_the_pipeline_hash():
    """A service binding's config-block value folds with the identity surface —
    changing it moves the pipeline-hash (the same treatment identity values get)."""
    reg, pipeline, _ = F.build_base()
    base = pipeline_hash(pipeline, reg)
    s0 = pipeline.service_bindings[0]
    changed = pipeline.model_copy(update={
        "service_bindings": (s0.model_copy(update={"config": {"temperature": 0.9}}),),
    })
    assert pipeline_hash(changed, reg) != base


def test_config_default_edit_shifts_exactly_the_relying_composition():
    """The canon-derived property (service-type/reference.md § The [config_schema]
    contract): editing a declared ship-time config default shifts exactly the
    compositions that RELIED on it (their effective value changes) and leaves an
    OVERRIDING composition's hashes untouched — the effective value is what folds,
    never the declaration."""
    from conjured.ir.channel_types import FieldDecl, primitive

    def build(default_temp, supplied_config):
        reg = DeclarationRegistry()
        st_toml = (
            'name="st.x"\n[identity_schema]\nm={type="str"}\n[transport_schema]\ne={type="str"}\n'
            f'[config_schema]\ntemperature={{type="float", default={default_temp}}}\n'
        )
        reg.add_service_type(loads(st_toml, "service_type", file_path="st.toml"))
        reg.add_handler("acme.s", loads(
            '[service]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n[service_bindings]\nllm={type="st.x"}',
            "handler", file_path="s.toml"))
        cfg = "" if supplied_config is None else f'[service_bindings.llm.config]\ntemperature={supplied_config}\n'
        pipe = loads(
            '[meta]\nname="p"\n[[nodes]]\nkind="handler"\nname="acme.s"\n'
            f'[service_bindings.llm]\ntype="st.x"\nm="1"\n{cfg}[inputs]\ni={{type="str"}}\n',
            "pipeline", file_path="p.toml")
        return pipeline_hash(pipe, reg)

    # A RELYING pipeline (no supply — the default IS the effective value): the default
    # edit shifts its hash.
    assert build(0.5, None) != build(0.9, None)
    # An OVERRIDING pipeline: the default edit is invisible (the supplied value is the
    # effective value either way).
    assert build(0.5, 0.7) == build(0.9, 0.7)
    # And inline-vs-default equivalence: relying on default 0.7 ≡ supplying 0.7 (the
    # EFFECTIVE value is the fold, wherever it came from).
    assert build(0.7, None) == build(0.5, 0.7)


# ---------------------------------------------------------------------------
# D6 — the affirmative non-hook hash domain: a supply referenced ONLY by a hook is
# invisible; a binding shared with a non-hook consumer folds.
# ---------------------------------------------------------------------------


def _d6_hash(hook_m: str, *, shared: bool) -> str:
    st = ('name="st.x"\n[identity_schema]\nm={type="str"}\n'
          '[transport_schema]\ne={type="str"}\n[config_schema]\n')
    reg = DeclarationRegistry()
    reg.add_service_type(loads(st, "service_type", file_path="st.toml"))
    # A backend-SDK hook declaring a service binding `sink`.
    reg.add_handler("acme.h", loads(
        '[hook]\n[reads]\ni={type="str"}\n[service_bindings]\nsink={type="st.x"}\n[transport_schema]',
        "handler", file_path="h.toml"))
    # The service references `sink` (shared) or its own `other` binding (pure-hook `sink`).
    svc_binding = "sink" if shared else "other"
    reg.add_handler("acme.s", loads(
        '[service]\n[reads]\ni={type="str"}\n[output_schema]\no={type="str"}\n'
        f'[service_bindings]\n{svc_binding}={{type="st.x"}}',
        "handler", file_path="s.toml"))
    supplies = f'[service_bindings.sink]\ntype="st.x"\nm="{hook_m}"\n'
    if not shared:
        supplies += '[service_bindings.other]\ntype="st.x"\nm="x"\n'
    pipe = loads(
        '[meta]\nname="p"\n'
        '[[nodes]]\nkind="handler"\nname="acme.h"\n'
        '[[nodes]]\nkind="handler"\nname="acme.s"\n'
        + supplies + '[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml")
    return pipeline_hash(pipe, reg)


def test_d6_pure_hook_binding_is_hash_invisible():
    """Editing a service binding referenced ONLY by a hook is hash-neutral — the hasher never
    reads a hook's declaration; the supply is outside the non-hook hash domain (D6)."""
    assert _d6_hash("1", shared=False) == _d6_hash("2", shared=False)


def test_d6_shared_binding_folds_as_ordinary_supply():
    """A binding shared between a hook and a non-hook consumer folds — the non-hook reference
    puts it in the domain, so editing its identity shifts the pipeline-hash (D6)."""
    assert _d6_hash("1", shared=True) != _d6_hash("2", shared=True)


def _tbh_with_sink(sink_m: str, *, hook_references_sink: bool) -> str:
    """A trainable composition whose `[service_bindings.sink]` is referenced by a HOOK
    preprocessor (a hook handler) or a non-hook (service) preprocessor — the TBH mirror of the D6
    non-hook domain (training_bundle_hash). The preprocessor is a name-reference; the referenced
    handler declares the `sink` service binding (so the hook/non-hook classification + the
    referenced bindings resolve from the registered declaration)."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads('name="st.x"\n[identity_schema]\nm={type="str"}\n[transport_schema]\ne={type="str"}\n[config_schema]\n', "service_type", file_path="st.toml"))
    reg.add_service_type(loads('name="bk.x"\n[identity_schema]\nmm={type="str"}\n[transport_schema]\ne={type="str"}\n[config_schema]\n', "service_type", file_path="bk.toml"))
    if hook_references_sink:
        # A HOOK handler binding `sink` — referenced only by a hook → sink is excluded from the TBH.
        reg.add_handler("acme.auditor", loads('[hook]\n[reads]\nobserved={type="str"}\n[service_bindings]\nsink={type="st.x"}\n[transport_schema]\n', "handler", file_path="aud.toml"))
        pre = ('[[preprocessors]]\nkind="handler"\nname="acme.auditor"\nid="aud"\n'
               'reads_map={observed="npc_state"}\n')
    else:
        # A SERVICE handler binding `sink` — referenced by a non-hook → sink folds into the TBH.
        reg.add_handler("acme.pre", loads('[service]\n[reads]\nobserved={type="str"}\n[output_schema]\nout2={type="str"}\n[service_bindings]\nsink={type="st.x"}\n', "handler", file_path="pre.toml"))
        pre = ('[[preprocessors]]\nkind="handler"\nname="acme.pre"\nid="pre"\n'
               'reads_map={observed="npc_state"}\nwrites_map={out2="out2"}\n')
    comp = loads(
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        + pre
        + f'[service_bindings.sink]\ntype="st.x"\nm="{sink_m}"\n'
        '[service_bindings.bk]\ntype="bk.x"\nmm="q"\n'
        '[trainable]\n[trainable.config]\n[trainable.service_bindings]\nbk={type="bk.x"}\n'
        '[trainable.reads]\nnpc_state={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n',
        "composition", file_path="dt.toml")
    return training_bundle_hash(comp, reg)


def test_d6_tbh_pure_hook_binding_is_invisible():
    """The TBH mirror: a composition supply referenced ONLY by a hook preprocessor is excluded
    from the training-bundle-hash — editing it is TBH-neutral (D6; the ruling's service-bound-
    hook test, TBH arm)."""
    assert _tbh_with_sink("1", hook_references_sink=True) == _tbh_with_sink("2", hook_references_sink=True)


def test_d6_tbh_non_hook_binding_folds():
    """The TBH mirror: a composition supply referenced by a NON-HOOK preprocessor folds —
    editing it shifts the training-bundle-hash (D6)."""
    assert _tbh_with_sink("1", hook_references_sink=False) != _tbh_with_sink("2", hook_references_sink=False)


def _tbh_with_preproc_handler(decl) -> str:
    """A trainable composition whose lone preprocessor name-references a registered handler
    declaration ``decl`` (binding name `cfg`, supplied `cfg = { marker = "x" }`). Returns the TBH."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
    reg.add_handler("transform.fmt", decl)
    comp = loads(
        '[meta]\nkind="trainable"\nname="dt"\n'
        '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
        '[[preprocessors]]\nkind="handler"\nname="transform.fmt"\nid="fmt"\n'
        'reads_map={context="npc_state"}\nwrites_map={prompt="formatted_prompt"}\n'
        '[preprocessors.bindings]\ncfg={marker="x"}\n'
        '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="m"\n'
        '[trainable]\n[trainable.config]\ntemperature=0.7\nmax_tokens=64\n'
        '[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
        '[trainable.reads]\nformatted_prompt={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n',
        "composition", file_path="c.toml")
    return training_bundle_hash(comp, reg)


# verifies: preprocessor-mirrors-outer-node
def test_sensitivity_tbh_preprocessor_delivery_selector():
    """A preprocessor binding's `delivery` selector (COPY vs REFERENCE) folds into the
    training-bundle-hash EXACTLY as an outer node's folds into the pipeline-hash — the composition
    twin of `test_sensitivity_binding_delivery_selector`. The referenced handler's `cfg` binding
    registered delivery=COPY vs REFERENCE in two registries → different TBH. RED on removal of the
    mirror-fix: the old inline model synthesized `delivery=COPY` for every preprocessor binding, so
    both folds coincided (the regression this seal guards)."""
    from conjured.ir.channel_types import FieldDecl, primitive
    from conjured.ir.common import Binding, Delivery, SchemaBinding
    from conjured.ir.handler import TransformDeclaration

    def decl(delivery):
        return TransformDeclaration(
            reads=(FieldDecl(name="context", type=primitive("str")),),
            output_schema=(FieldDecl(name="prompt", type=primitive("str")),),
            bindings=(Binding(name="cfg", body=SchemaBinding(
                fields=(FieldDecl(name="marker", type=primitive("str")),), delivery=delivery,
            )),),
        )
    assert _tbh_with_preproc_handler(decl(Delivery.COPY)) != _tbh_with_preproc_handler(decl(Delivery.REFERENCE))


# verifies: preprocessor-mirrors-outer-node
def test_sensitivity_tbh_preprocessor_ship_time_default():
    """A preprocessor's referenced handler's declared ship-time `default` folds into the
    training-bundle-hash — changing the declared default (the preprocessor omits the binding, so
    the default is the effective value) moves the TBH. RED on removal: the old inline model folded
    no declared default (`declared=()`), so changing it was hash-neutral."""
    def reg_and_comp(default_marker):
        reg = DeclarationRegistry()
        reg.add_service_type(loads(F.SERVICE_TYPE_DIALOGUE, "service_type", file_path="st.toml"))
        reg.add_handler("transform.fmt", loads(
            '[transform]\n[reads]\ncontext={type="str"}\n[output_schema]\nprompt={type="str"}\n'
            f'[bindings.cfg]\ndefault={{marker="{default_marker}"}}\nmarker={{type="str"}}\n',
            "handler", file_path="fmt.toml"))
        comp = loads(
            '[meta]\nkind="trainable"\nname="dt"\n'
            '[inputs]\nnpc_state={type="str"}\n[outputs]\ndialogue_response={type="str"}\n'
            '[[preprocessors]]\nkind="handler"\nname="transform.fmt"\nid="fmt"\n'
            'reads_map={context="npc_state"}\nwrites_map={prompt="formatted_prompt"}\n'  # cfg omitted → declared default folds
            '[service_bindings.llm]\ntype="conjured_llm.dialogue"\nmodel="m"\n'
            '[trainable]\n[trainable.config]\ntemperature=0.7\nmax_tokens=64\n'
            '[trainable.service_bindings]\nllm={type="conjured_llm.dialogue"}\n'
            '[trainable.reads]\nformatted_prompt={type="str"}\n[trainable.output_schema]\ndialogue_response={type="str"}\n',
            "composition", file_path="c.toml")
        return training_bundle_hash(comp, reg)
    assert reg_and_comp("a") != reg_and_comp("b")


# ---------------------------------------------------------------------------
# Exclusion — a change to an EXCLUDED field → the SAME hash (incl. the rename test)
# ---------------------------------------------------------------------------


def test_exclusion_composition_meta_name_rename_is_hash_neutral():
    """THE family-rule rename test: renaming a trainable composition's meta.name is hash-neutral
    — same TBH, and same pipeline-hash (folded by reference)."""
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    tbh_before, ph_before = training_bundle_hash(comp, reg), pipeline_hash(pipeline, reg)
    renamed = comp.model_copy(update={"meta": comp.meta.model_copy(update={"name": "totally_different_name"})})
    reg.compositions["trainables/dialogue.toml"] = renamed
    assert training_bundle_hash(renamed, reg) == tbh_before
    assert pipeline_hash(pipeline, reg) == ph_before


def test_exclusion_pipeline_meta_name_rename_is_hash_neutral():
    """Renaming the top-level pipeline (its [meta].name) is hash-neutral (the family rule for
    the outer pipeline; the Phase-1b floor amendment's payoff)."""
    reg, pipeline, _ = F.build_base()
    before = pipeline_hash(pipeline, reg)
    renamed = pipeline.model_copy(update={"meta": pipeline.meta.model_copy(update={"name": "renamed.pipeline"})})
    assert pipeline_hash(renamed, reg) == before


def test_meta_description_is_a_grammar_rejection():
    """CONVERTED from the old meta.description exclusion test: a composition `[meta]` no longer
    admits a `description` key at all (the family rule closes it to `{kind, name}` —
    handler/reference.md § A composition mirrors the pipeline). A `[meta].description` is now a
    loud CLOSED_GRAMMAR ContractViolation at load, not silently-excluded prose. Author prose
    about a composition lives in its `[annotations]` block."""
    described_toml = F.TRAINABLE_COMPOSITION.replace(
        '[meta]\nkind = "trainable"\nname = "dialogue_training"',
        '[meta]\nkind = "trainable"\nname = "dialogue_training"\ndescription = "prose that no longer belongs here"')
    assert described_toml != F.TRAINABLE_COMPOSITION  # the .replace actually fired
    with pytest.raises(ContractViolation) as exc:
        loads(described_toml, "composition", file_path="c.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR


def test_exclusion_annotations():
    """annotations blocks are excluded from the TBH (and the outer pipeline-hash by extension)."""
    reg, pipeline = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    tbh_before, ph_before = training_bundle_hash(comp, reg), pipeline_hash(pipeline, reg)
    annotated = comp.model_copy(update={"annotations": {"description": "x", "postprocessors": ["a", "b"]}})
    reg.compositions["trainables/dialogue.toml"] = annotated
    assert training_bundle_hash(annotated, reg) == tbh_before
    assert pipeline_hash(pipeline, reg) == ph_before


# verifies: description-shifts-both-hashes
def test_sensitivity_field_description_shifts_both_hashes():
    """AC1 — the arc's core seal (INVERTED from the old exclusion test). A `description` on a
    trainable's `output_schema` field is model-facing contract content that conditions the
    backend's constrained generation (hash-model.md § What the pipeline-hash absorbs + the
    family rule), so editing it is a composition change that honestly shifts BOTH the
    training-bundle-hash AND the pipeline-hash (the pipeline folds the TBH by reference). The
    exact adversary: two trainable compositions differing ONLY in one output-field description.
    RED-on-removal — if `canon_field` stops folding `description`, both asserts collapse to
    equality. Defends against a generation-behavior change hiding under an identical hash."""
    reg_plain, pipe_plain = F.build_trainable()
    comp_plain = reg_plain.get_composition("trainables/dialogue.toml")
    tbh_plain = training_bundle_hash(comp_plain, reg_plain)
    ph_plain = pipeline_hash(pipe_plain, reg_plain)

    # The ONE admitted description position — the trainable's output_schema field.
    described_toml = F.TRAINABLE_COMPOSITION.replace(
        '[trainable.output_schema]\ndialogue_response = { type = "str" }',
        '[trainable.output_schema]\ndialogue_response = { type = "str", description = "The NPC line." }')
    assert described_toml != F.TRAINABLE_COMPOSITION  # the .replace actually fired (no vacuous pass)
    reg_desc, pipe_desc = F.build_trainable()
    reg_desc.compositions["trainables/dialogue.toml"] = loads(described_toml, "composition", file_path="c.toml")
    comp_desc = reg_desc.get_composition("trainables/dialogue.toml")

    assert training_bundle_hash(comp_desc, reg_desc) != tbh_plain  # the seal, TBH leg
    assert pipeline_hash(pipe_desc, reg_desc) != ph_plain          # the seal, pipeline leg (by-reference)


def test_service_binding_decl_description_is_a_grammar_rejection():
    """CONVERTED from the old service-binding-decl description exclusion test: a
    `service_bindings.<name>` DECLARATION entry is closed to `{type}` — it carries no prose
    `description` (the family rule admits `description` only on a trainable's `output_schema`
    fields; binding prose lives in `[annotations]`). A `description` on a binding declaration is
    now a loud CLOSED_GRAMMAR ContractViolation at load, at both a service handler's
    `[service_bindings]` and a trainable backend's `[trainable.service_bindings]`. RED if the
    parser re-admits the key."""
    # --- a service handler's service-binding declaration ---
    described_respond_toml = F.SERVICE_RESPOND.replace(
        'llm = { type = "conjured_llm.structured_output" }',
        'llm = { type = "conjured_llm.structured_output", description = "prose that no longer belongs here" }')
    assert described_respond_toml != F.SERVICE_RESPOND  # the .replace actually fired
    with pytest.raises(ContractViolation) as exc:
        loads(described_respond_toml, "handler", file_path="h.respond.toml")
    assert exc.value.check is Check.CLOSED_GRAMMAR

    # --- a trainable backend's service-binding declaration ---
    described_comp_toml = F.TRAINABLE_COMPOSITION.replace(
        'llm = { type = "conjured_llm.dialogue" }',
        'llm = { type = "conjured_llm.dialogue", description = "backend prose" }')
    assert described_comp_toml != F.TRAINABLE_COMPOSITION  # the .replace actually fired
    with pytest.raises(ContractViolation) as exc2:
        loads(described_comp_toml, "composition", file_path="c.toml")
    assert exc2.value.check is Check.CLOSED_GRAMMAR


# ---------------------------------------------------------------------------
# Field validators — value-constraining, so absorbed (N1; handler/reference.md
# § Validators: parameters "fold into the pipeline-hash as the field's validator
# configuration — a validator-parameter change is a composition change")
# ---------------------------------------------------------------------------


def _validated_handler(keywords_toml: str) -> str:
    """A handler whose `reads.a` carries the given validation keywords as inline field
    keys (D8 — one grammar: bare standard keywords + namespaced dotted validators)."""
    a = f'a = {{ type = "str", {keywords_toml} }}'
    return f'[transform]\n[reads]\n{a}\nb = {{ type = "int" }}\n[output_schema]\no = {{ type = "str" }}'


def test_sensitivity_validator_addition_moves_the_hash():
    reg_plain, pipe_plain = _pipeline_with(_two_field_handler("ab"))
    reg_val, pipe_val = _pipeline_with(_validated_handler('"mypkg.is_clean" = {}'))
    assert pipeline_hash(pipe_plain, reg_plain) != pipeline_hash(pipe_val, reg_val)


def test_sensitivity_validator_parameter_change_moves_the_hash():
    reg_2, pipe_2 = _pipeline_with(_validated_handler("minLength = 2"))
    reg_3, pipe_3 = _pipeline_with(_validated_handler("minLength = 3"))
    assert pipeline_hash(pipe_2, reg_2) != pipeline_hash(pipe_3, reg_3)


def test_sensitivity_validator_order_moves_the_hash():
    """Validation-keyword order is semantic — the declared sequence is the execution
    order (authored key order across both classes, D8), and canon_field preserves it —
    so reversing two keywords on one field moves the hash. The order-sensitivity
    counterpart of test_lexical_neutrality_schema_field_order (a sorted() inside the
    emission would pass every other validator hash test but fail this one)."""
    ab = "minLength = 2, maxLength = 5"
    ba = "maxLength = 5, minLength = 2"
    reg_ab, pipe_ab = _pipeline_with(_validated_handler(ab))
    reg_ba, pipe_ba = _pipeline_with(_validated_handler(ba))
    assert pipeline_hash(pipe_ab, reg_ab) != pipeline_hash(pipe_ba, reg_ba)


def test_interleaved_builtin_and_third_party_fold_in_authored_order():
    """D8 — bare standard keywords and namespaced validators interleave in ONE authored
    tuple: reversing a builtin and a third-party key across the boundary moves the hash
    (no class-precedence reordering inside canon_field)."""
    bt = 'minLength = 2, "mypkg.is_clean" = {}'
    tb = '"mypkg.is_clean" = {}, minLength = 2'
    reg_bt, pipe_bt = _pipeline_with(_validated_handler(bt))
    reg_tb, pipe_tb = _pipeline_with(_validated_handler(tb))
    assert pipeline_hash(pipe_bt, reg_bt) != pipeline_hash(pipe_tb, reg_tb)


def test_exclusion_hook_node_is_hash_neutral():
    """Hook nodes contribute to neither hash — adding/removing a hook is pipeline-hash-neutral
    (hash-model.md exclusion set)."""
    reg, pipeline, _ = F.build_base()  # nodes: normalize, respond, log(hook)
    with_hook = pipeline_hash(pipeline, reg)
    without_hook = pipeline.model_copy(update={"nodes": pipeline.nodes[:2]})  # drop the hook
    assert pipeline_hash(without_hook, reg) == with_hook


# ---------------------------------------------------------------------------
# External-file binding guard (resolution 3c) — raise, never silently hash the path
# ---------------------------------------------------------------------------


def test_unresolved_external_file_binding_raises():
    """The structural backstop: an UNRESOLVED external-file binding (the resolution pass never
    ran, so content_hash is None) raises EXTERNAL_BINDING_UNSUPPORTED — the hasher never reads a
    file or hashes a path. (Resolution is the stage-1 pass's job; see
    test_external_file_binding_folds_canonicalized_content.)"""
    reg, _, _ = F.build_base()
    pipe = loads(
        F.PIPELINE.replace(
            'bindings = { config = { marker_set = "brackets" } }',
            'bindings = { config = { file = "configs/markers.toml" } }'),
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipe, reg)
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED


def test_external_file_binding_folds_canonicalized_content(tmp_path):
    """Step 5 (external-file content): after the stage-1 resolution pass reads + canonicalizes +
    stamps the file, the hasher folds the canonicalized CONTENT (not the content hash) — and
    "inline X" and "a file containing X" produce the SAME pipeline-hash (path-neutrality;
    hash-model.md § External binding-value declaration content). `marker_set` is the declared
    field; the file/inline both supply the same object."""
    from conjured.validator.resolve import resolve_pipeline_bindings

    # The external file's content (a TOML table → the same object an inline supply gives).
    (tmp_path / "markers.toml").write_text('marker_set = "brackets"\n', encoding="utf-8")

    reg_file, _, _ = F.build_base()
    pipe_file = loads(
        F.PIPELINE.replace(
            'bindings = { config = { marker_set = "brackets" } }',
            'bindings = { config = { file = "markers.toml" } }'),
        "pipeline", file_path="p.toml")
    pipe_file = resolve_pipeline_bindings(pipe_file, reg_file, base_dir=str(tmp_path))
    file_hash = pipeline_hash(pipe_file, reg_file)

    # The inline-equivalent: the same object supplied inline as the one-field table.
    reg_inline, pipe_inline, _ = F.build_base()  # PIPELINE already supplies { marker_set = "brackets" } inline
    inline_hash = pipeline_hash(pipe_inline, reg_inline)

    # The BARE-equivalent: the same single logical value supplied as a bare scalar. Under the
    # single-field normalization this folds to the SAME bare value the file and one-field table
    # reduce to — so all three routes agree (binding-delivery-normalization arc).
    reg_bare, _, _ = F.build_base()
    pipe_bare = loads(
        F.PIPELINE.replace(
            'bindings = { config = { marker_set = "brackets" } }',
            'bindings = { config = "brackets" }'),
        "pipeline", file_path="p.toml")
    bare_hash = pipeline_hash(pipe_bare, reg_bare)

    assert file_hash == inline_hash == bare_hash  # path- AND spelling-neutral for one value
    # The external-file golden IS the base golden — the inline-equivalent pins it (path-neutral).
    assert file_hash == GOLDEN_BASE_PIPELINE_HASH


def test_single_field_binding_hash_equivalence_across_routes(tmp_path):
    """The binding-delivery-normalization seal (hash side): a single-field binding supplied
    as a bare scalar, its one-field inline table, or an external file folds to ONE
    pipeline-hash — the differing spellings of one logical value are hash-equivalent. RED on
    removal of the normalization: without it the table/file routes fold
    ``{"marker_set": "brackets"}`` while the bare route folds ``"brackets"`` — two hashes for
    one value (hash-model.md § What the pipeline-hash absorbs, the single-field-binding rule)."""
    from conjured.validator.resolve import resolve_pipeline_bindings

    def _supply(spelling):
        reg, _, _ = F.build_base()
        pipe = loads(
            F.PIPELINE.replace('bindings = { config = { marker_set = "brackets" } }', spelling),
            "pipeline", file_path="p.toml")
        return reg, pipe

    reg_b, pipe_b = _supply('bindings = { config = "brackets" }')                       # bare
    reg_t, pipe_t = _supply('bindings = { config = { marker_set = "brackets" } }')       # one-field table
    (tmp_path / "m.toml").write_text('marker_set = "brackets"\n', encoding="utf-8")
    reg_f, pipe_f = _supply('bindings = { config = { file = "m.toml" } }')               # external file
    pipe_f = resolve_pipeline_bindings(pipe_f, reg_f, base_dir=str(tmp_path))

    hashes = {
        pipeline_hash(pipe_b, reg_b),
        pipeline_hash(pipe_t, reg_t),
        pipeline_hash(pipe_f, reg_f),
    }
    assert hashes == {GOLDEN_BASE_PIPELINE_HASH}  # one logical value → exactly one hash


def test_single_field_binding_field_rename_still_shifts_hash():
    """Criterion 4: renaming a single-field binding's FIELD still shifts the pipeline-hash —
    via the handler-DECLARATION-side schema fold — even though the supply-site fold now
    carries only the bare value (the field name dropped there by normalization). The supply
    is bare (field-name-agnostic), so the ONLY difference between the two hashes is the
    declared field name; if the declaration-side schema fold stopped carrying it, a rename
    would go hash-invisible. RED on removal of the declaration-side field-name fold."""
    bare_supply = 'bindings = { config = "brackets" }'
    reg, _, _ = F.build_base()
    pipe = loads(F.PIPELINE.replace(
        'bindings = { config = { marker_set = "brackets" } }', bare_supply),
        "pipeline", file_path="p.toml")
    base = pipeline_hash(pipe, reg)

    reg_renamed, _, _ = F.build_base()
    reg_renamed.add_handler("acme.normalize", loads(
        F.TRANSFORM_NORMALIZE.replace("marker_set", "marker_style"),
        "handler", file_path="h.norm.toml"))
    pipe_renamed = loads(F.PIPELINE.replace(
        'bindings = { config = { marker_set = "brackets" } }', bare_supply),
        "pipeline", file_path="p.toml")
    assert pipeline_hash(pipe_renamed, reg_renamed) != base


def test_external_file_missing_raises_at_resolution(tmp_path):
    """A missing external declaration file raises at the resolution pass (I/O at compose, fail
    loud) — never a path silently hashed, never a dispatch-time surprise."""
    from conjured.validator.resolve import resolve_pipeline_bindings
    reg, _, _ = F.build_base()
    pipe = loads(
        F.PIPELINE.replace(
            'bindings = { config = { marker_set = "brackets" } }',
            'bindings = { config = { file = "does_not_exist.toml" } }'),
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        resolve_pipeline_bindings(pipe, reg, base_dir=str(tmp_path))
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED


def test_trainable_composition_with_no_backend_binding_fails_loud():
    """The hasher's raw-config graceful-degrade arm was deleted (mechanical set): a trainable
    composition with no service-typed backend binding cannot fold an effective config (the
    bound [config_schema] is the validator), so the TBH raises the sibling fail-loud
    SERVICE_BINDING_CARDINALITY rather than silently folding unvalidated raw config
    (graceful-degrade = training-data corruption)."""
    reg, _ = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    no_backend = comp.model_copy(
        update={"trainable": comp.trainable.model_copy(update={"service_bindings": ()})}
    )
    with pytest.raises(ContractViolation) as exc:
        training_bundle_hash(no_backend, reg)
    assert exc.value.check is Check.SERVICE_BINDING_CARDINALITY
    assert exc.value.rule_id == "R-handler-008"


# ---------------------------------------------------------------------------
# Fail-loud resolution arms — the hasher runs over a COMPILE-VALIDATED declaration
# set (hash-model.md), so an unresolvable name/type or a non-canonicalizable default
# is a structured ContractViolation, NEVER a silent fold. The Check enum members are
# exercised through the validator/compile seam in tests/validator/test_negative.py
# (test_every_check_has_a_negative); these tests are the missing HASHER-SPECIFIC
# adversaries that bite the hasher's own raise sites.
# Each goes RED if its raise folds silently — a seal is verified only by its failing case.
# ---------------------------------------------------------------------------


def test_unresolvable_handler_node_name_fails_loud():
    """S3 arm 1 (handler node): a `pipeline_hash` over a pipeline whose handler node
    references a name absent from the registry raises the structured
    HANDLER_NAME_RESOLUTION — never a silent fold over an unvalidated pipeline (a None
    declaration would otherwise AttributeError on `.output_schema`). RED if the
    `_canon_pipeline_node` handler-node raise stops firing."""
    reg = DeclarationRegistry()  # empty — `acme.missing` resolves to nothing
    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "handler"\nname = "acme.missing"\n'
        '[inputs]\nx = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipe, reg)
    assert exc.value.check is Check.HANDLER_NAME_RESOLUTION
    assert exc.value.rule_id == "R-pipeline-001"


def test_unresolvable_composition_node_name_fails_loud():
    """S3 arm 2 (composition node): a `pipeline_hash` over a pipeline whose composition
    node references a path absent from the registry raises HANDLER_NAME_RESOLUTION — the
    embedded composition's identity (its TBH) cannot be folded by reference if the
    composition does not resolve. RED if the `_canon_pipeline_node` composition-node raise
    stops firing (a None composition would AttributeError inside `training_bundle_hash`)."""
    reg = DeclarationRegistry()  # empty — the composition path resolves to nothing
    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "composition"\nname = "trainables/missing.toml"\n'
        '[inputs]\nx = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipe, reg)
    assert exc.value.check is Check.HANDLER_NAME_RESOLUTION
    assert exc.value.rule_id == "R-pipeline-001"


def test_supply_reference_scan_fails_loud_on_an_unresolvable_handler():
    """S3 arm 3 (the supply-reference scan): the third HANDLER_NAME_RESOLUTION arm lives in
    `non_hook_referenced_supplies` (the non-hook supply-domain pass). On the public
    `pipeline_hash` path it is SHADOWED — the node-canonicalization pass runs first and
    raises on the same unresolvable name against the same registry, so this arm is never the
    first raise end-to-end (surfaced to the principal: not independently reachable from a
    public entrypoint today). It is exercised here by a direct call so the defensive guard
    stays honest — a refactor reordering the two passes would make it the reachable arm. RED
    if the raise folds (it would return an empty referenced-set via `getattr(None, ...)`)."""
    from conjured.hasher.hashes import non_hook_referenced_supplies

    reg = DeclarationRegistry()  # empty — `acme.missing` resolves to nothing
    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "handler"\nname = "acme.missing"\n'
        '[inputs]\nx = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        non_hook_referenced_supplies(pipe, reg)
    assert exc.value.check is Check.HANDLER_NAME_RESOLUTION
    assert exc.value.rule_id == "R-pipeline-001"


def test_unresolvable_supply_service_type_fails_loud():
    """S4 arm 1 (pipeline supply): a `pipeline_hash` whose pipeline-level
    `service_bindings.<name>` supply (referenced by a non-hook service handler) names a
    service-type absent from the registry raises SERVICE_TYPE_RESOLUTION — the effective
    config fold needs the bound `[config_schema]`, so an unresolvable type fails loud rather
    than folding to a default. RED if the `_resolve_supply_service_type` `if st is None` arm
    is dropped."""
    reg = DeclarationRegistry()
    # A service handler that references a service-type NOT registered here.
    reg.add_handler("acme.s", loads(
        '[service]\n[reads]\ni = { type = "str" }\n[output_schema]\no = { type = "str" }\n'
        '[service_bindings]\nllm = { type = "st.missing" }',
        "handler", file_path="s.toml"))
    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "handler"\nname = "acme.s"\n'
        '[service_bindings.llm]\ntype = "st.missing"\nm = "1"\n[inputs]\ni = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipe, reg)
    assert exc.value.check is Check.SERVICE_TYPE_RESOLUTION
    assert exc.value.rule_id == "R-service-type-004"


def test_unresolvable_trainable_backend_service_type_fails_loud():
    """S4 arm 2 (trainable backend): a `training_bundle_hash` whose trainable backend binding
    names a service-type absent from the registry raises SERVICE_TYPE_RESOLUTION — distinct
    from the no-backend SERVICE_BINDING_CARDINALITY arm (a backend binding IS present here,
    its TYPE just does not resolve). RED if the `_canon_trainable_composition`
    `if backend_type is None` arm is dropped (the effective-config fold would AttributeError
    on the None service-type)."""
    reg, _ = F.build_trainable()
    comp = reg.get_composition("trainables/dialogue.toml")
    # Point the (present) backend binding at a service-type that is NOT registered.
    bad_backend = comp.trainable.service_bindings[0].model_copy(update={"type": "no.such.backend"})
    bad = comp.model_copy(update={
        "trainable": comp.trainable.model_copy(update={"service_bindings": (bad_backend,)})
    })
    with pytest.raises(ContractViolation) as exc:
        training_bundle_hash(bad, reg)
    assert exc.value.check is Check.SERVICE_TYPE_RESOLUTION
    assert exc.value.rule_id == "R-service-type-004"


def test_non_canonicalizable_ship_time_default_fails_loud():
    """S5 (the ship-time-default wrap): a handler binding declaring a ship-time `default` the
    canonicalizer cannot serialize (a `set` is non-JSON-native — TOML cannot express one, so
    the adversary is built in the real IR) raises MALFORMED_DECLARATION through `pipeline_hash`
    rather than letting `canon_value`'s bare `TypeError` escape. This is the UNTESTED
    ship-time-default wrap (`out["default"] = canon_value(body.default)`), distinct from the
    already-covered compile-params wrap. The node SUPPLIES the binding (a canonicalizable inline
    value) so the path reaches the handler-declaration fold rather than the supply-site
    omitted-default fold. RED if the ship-time-default `try/except TypeError` is removed (a raw
    TypeError would surface instead of the structured ContractViolation)."""
    from conjured.ir.channel_types import FieldDecl, primitive
    from conjured.ir.common import Binding, SchemaBinding
    from conjured.ir.handler import TransformDeclaration

    bad_binding = Binding(name="cfg", body=SchemaBinding(
        fields=(FieldDecl(name="marker", type=primitive("str")),),
        default={"a", "b"},  # a set — non-canonicalizable (canon_value raises TypeError)
    ))
    reg = DeclarationRegistry()
    reg.add_handler("acme.bad", TransformDeclaration(
        reads=(),
        output_schema=(FieldDecl(name="o", type=primitive("str")),),
        bindings=(bad_binding,),
    ))
    # The node SUPPLIES `cfg` inline (canonicalizable) → the omitted-default supply-site fold is
    # skipped and the path reaches the handler-declaration ship-time-default wrap.
    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "handler"\nname = "acme.bad"\n'
        'bindings = { cfg = { marker = "x" } }\n[inputs]\nx = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipe, reg)
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-pipeline-001"


def test_non_canonicalizable_omitted_default_fails_loud():
    """The SUPPLY-SITE omitted-default fold (canon_supplied_bindings): a node that OMITS a
    default-bearing binding folds the declared default as the effective value at the supply
    site. A non-canonicalizable such default (a `set` — non-JSON-native) must raise the
    structured MALFORMED_DECLARATION, mirroring the handler-declaration ship-time-default wrap
    (its sibling, covered by `test_non_canonicalizable_ship_time_default_fails_loud`), rather
    than letting `canon_value`'s bare `TypeError` escape — the fail-loud parity the small-fixes
    item closed.

    The node OMITS `cfg`, so the supply-site omitted-default fold is the path that hits the bad
    default. It runs BEFORE the handler-declaration fold (`_canon_pipeline_node` evaluates the
    `bindings` key before the `handler` key), so removing the supply-site `try/except TypeError`
    surfaces a raw `TypeError` here — the adversary is genuine: RED on removal of that wrap, not
    masked by the sibling handler-declaration wrap."""
    from conjured.ir.channel_types import FieldDecl, primitive
    from conjured.ir.common import Binding, SchemaBinding
    from conjured.ir.handler import TransformDeclaration

    bad_binding = Binding(name="cfg", body=SchemaBinding(
        fields=(FieldDecl(name="marker", type=primitive("str")),),
        default={"a", "b"},  # a set — non-canonicalizable (canon_value raises TypeError)
    ))
    reg = DeclarationRegistry()
    reg.add_handler("acme.bad", TransformDeclaration(
        reads=(),
        output_schema=(FieldDecl(name="o", type=primitive("str")),),
        bindings=(bad_binding,),
    ))
    # The node OMITS `cfg` → the supply-site omitted-default fold folds the (bad) declared
    # default as the effective value, and is the first fold to touch it.
    pipe = loads(
        '[meta]\nname = "acme.p"\n[[nodes]]\nkind = "handler"\nname = "acme.bad"\n'
        '[inputs]\nx = { type = "str" }\n',
        "pipeline", file_path="p.toml")
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipe, reg)
    assert exc.value.check is Check.MALFORMED_DECLARATION
    assert exc.value.rule_id == "R-pipeline-001"


# ---------------------------------------------------------------------------
# The nested `pipeline` composition kind — own-hash-domain, by-reference fold
# (pipeline/reference.md § The nested `pipeline` composition kind; hash-model.md
# § What the pipeline-hash absorbs — the recursive mirror rule)
# ---------------------------------------------------------------------------


def _nested_setup(inner_body: str = 'loud = { type = "str" }'):
    """An outer pipeline embedding one nested `pipeline` composition whose single node is
    a registered transform. ``inner_body`` parameterizes the inner [outputs] block so a
    test can edit inner content."""
    reg = DeclarationRegistry()
    reg.add_handler("acme.up", loads(
        '[transform]\n[reads]\ntext = { type = "str" }\n'
        f'[output_schema]\n{inner_body}\n',
        "handler", file_path="up.toml"))
    reg.add_composition("pipelines/inner.toml", loads(
        '[meta]\nkind = "pipeline"\nname = "acme.inner"\n'
        '[[nodes]]\nkind = "handler"\nname = "acme.up"\n'
        f'[inputs]\ntext = {{ type = "str" }}\n[outputs]\n{inner_body}\n',
        "composition", file_path="inner.toml"))
    outer = loads(
        '[meta]\nname = "acme.outer"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/inner.toml"\n'
        f'[inputs]\ntext = {{ type = "str" }}\n[outputs]\n{inner_body}\n',
        "pipeline", file_path="outer.toml")
    return reg, outer


# Drift detector for the nested-pipeline by-reference fold (the same golden discipline the
# base/trainable pins carry): a change to the fold shape — the `pipeline_hash` reference key,
# the inner pipeline-hash construction, or the canonicalization — moves this.
GOLDEN_NESTED_PIPELINE_HASH = "sha256:aedaafe6c13ad53e860469244608d04c8a6e6b7caac0b9b4a52be2bc3d8aae53"


def test_golden_nested_pipeline_hash():
    reg, outer = _nested_setup()
    assert pipeline_hash(outer, reg) == GOLDEN_NESTED_PIPELINE_HASH


def test_nested_pipeline_fold_reflects_inner_edits_by_reference():
    """An inner-pipeline content edit shifts the enclosing pipeline-hash — through the
    by-reference fold, not inlining: the fold entry is the inner pipeline's OWN hash, so
    any absorbed inner edit moves the outer value."""
    reg_a, outer_a = _nested_setup()
    reg_b, outer_b = _nested_setup(inner_body='loud = { type = "int" }')
    assert pipeline_hash(outer_a, reg_a) != pipeline_hash(outer_b, reg_b)


def test_nested_pipeline_rename_is_hash_neutral():
    """Renaming the nested composition (meta.name — identity under the family rule, which a
    nested-pipeline composition shares with a top-level pipeline) is hash-neutral for the
    enclosing pipeline-hash."""
    reg, outer = _nested_setup()
    base = pipeline_hash(outer, reg)
    renamed = loads(
        '[meta]\nkind = "pipeline"\nname = "acme.renamed_inner"\n'
        '[[nodes]]\nkind = "handler"\nname = "acme.up"\n'
        '[inputs]\ntext = { type = "str" }\n[outputs]\nloud = { type = "str" }\n',
        "composition", file_path="inner.toml")
    reg.add_composition("pipelines/inner.toml", renamed)
    assert pipeline_hash(outer, reg) == base


def test_nested_pipeline_fold_is_opaque_not_inlined():
    """By reference, never by textual inlining: the outer-embedding hash differs from the
    hash of the structurally-equivalent pipeline declaring the inner node DIRECTLY — the
    embed boundary is part of composition identity (opaque inner scope), unlike a
    pure-substitution bundle which would fold textually."""
    reg, outer = _nested_setup()
    inlined = loads(
        '[meta]\nname = "acme.outer"\n'
        '[[nodes]]\nkind = "handler"\nname = "acme.up"\n'
        '[inputs]\ntext = { type = "str" }\n[outputs]\nloud = { type = "str" }\n',
        "pipeline", file_path="outer.toml")
    assert pipeline_hash(outer, reg) != pipeline_hash(inlined, reg)


def test_nested_pipeline_fold_is_recursive():
    """The mirror rule applies at whichever layer the embed sits: with two-deep nesting
    (outer -> mid -> inner), an innermost edit propagates to the outermost pipeline-hash
    through the chained by-reference folds."""
    def build(inner_type: str):
        reg = DeclarationRegistry()
        reg.add_handler("acme.up", loads(
            '[transform]\n[reads]\ntext = { type = "str" }\n'
            f'[output_schema]\nloud = {{ type = "{inner_type}" }}\n',
            "handler", file_path="up.toml"))
        reg.add_composition("pipelines/inner.toml", loads(
            '[meta]\nkind = "pipeline"\nname = "acme.inner"\n'
            '[[nodes]]\nkind = "handler"\nname = "acme.up"\n'
            f'[inputs]\ntext = {{ type = "str" }}\n[outputs]\nloud = {{ type = "{inner_type}" }}\n',
            "composition", file_path="inner.toml"))
        reg.add_composition("pipelines/mid.toml", loads(
            '[meta]\nkind = "pipeline"\nname = "acme.mid"\n'
            '[[nodes]]\nkind = "composition"\nname = "pipelines/inner.toml"\n'
            f'[inputs]\ntext = {{ type = "str" }}\n[outputs]\nloud = {{ type = "{inner_type}" }}\n',
            "composition", file_path="mid.toml"))
        outer = loads(
            '[meta]\nname = "acme.outer"\n'
            '[[nodes]]\nkind = "composition"\nname = "pipelines/mid.toml"\n'
            f'[inputs]\ntext = {{ type = "str" }}\n[outputs]\nloud = {{ type = "{inner_type}" }}\n',
            "pipeline", file_path="outer.toml")
        return reg, outer

    reg_str, outer_str = build("str")
    reg_int, outer_int = build("int")
    assert pipeline_hash(outer_str, reg_str) != pipeline_hash(outer_int, reg_int)


def test_nested_pipeline_hasher_cycle_backstop_fails_loud():
    """The hasher's structural cycle backstop (sibling of the unresolved-external-file
    guard): the hasher runs over a compile-validated (acyclic) pipeline, so a cyclic embed
    graph reaching it is registry drift — it must raise the structured COMPOSITION_CYCLE
    ContractViolation, never recurse unboundedly. RED if the backstop is removed (the
    recursion would blow the stack as a raw RecursionError — a fourth class)."""
    reg = DeclarationRegistry()
    reg.add_composition("pipelines/a.toml", loads(
        '[meta]\nkind = "pipeline"\nname = "acme.a"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/a.toml"\n'
        '[inputs]\ntext = { type = "str" }\n',
        "composition", file_path="a.toml"))
    outer = loads(
        '[meta]\nname = "acme.outer"\n'
        '[[nodes]]\nkind = "composition"\nname = "pipelines/a.toml"\n'
        '[inputs]\ntext = { type = "str" }\n',
        "pipeline", file_path="outer.toml")
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(outer, reg)
    assert exc.value.check is Check.COMPOSITION_CYCLE
    assert exc.value.rule_id == "R-pipeline-001"


# ---------------------------------------------------------------------------
# The by-reference fold kind-guard — own-hash-domain allowlist
# (bundle-embed-form arc, 1-kindguard; hash-model.md § What the pipeline-hash absorbs —
# a pure-substitution bundle has no own hash domain and folds textually, never by reference)
# ---------------------------------------------------------------------------


# verifies: tbh-fold-own-hash-domain-only
def test_by_reference_fold_refuses_a_non_own_hash_domain_composition():
    """The hasher's by-reference training-bundle-hash fold is an own-hash-domain ALLOWLIST:
    only a trainable composition folds by reference here (the nested ``pipeline`` kind folds in
    its own arm above). A pure-substitution bundle has NO own hash domain — it folds textually
    into the outer pipeline BEFORE hashing — so it must never reach the by-reference fold; doing
    so would silently mis-hash it (a training-contract break). A real ``BundleComposition`` is
    substituted out at every walker's entry (``conjured.ir.substitute`` — ``pipeline_hash``
    substitutes at its own head), so this is the fail-loud structural backstop against a walk
    that FORGOT to substitute, the sibling of the cycle / unresolved-file backstops.

    **Adversary construction (surfaced tradeoff).** A real ``BundleComposition`` can no longer
    reach the fold through the normal path (the entry substitution rewrites it first), so the
    exact adversary is a non-own-hash-domain value the substitution does NOT rewrite. The test
    injects a **minimal stand-in** composition that is neither a ``TrainableComposition`` nor a
    ``PipelineComposition`` (carrying a real ``BUNDLE``-kind ``[meta]`` so the diagnostic names the
    kind honestly). This is NOT a mock of engine behavior: the guard REJECTS the value before
    ``training_bundle_hash`` is ever called, so the stand-in needs zero engine-type fidelity — it
    IS the adversarial input, not a double that stands in for a real type's behavior (the
    no-engine-mock rule polices doubles that hide behavior; here nothing is hidden). The fuller
    alternative — a duck-typed stand-in complete enough to actually hash, so removal produces a
    silent WRONG hash rather than an error — was rejected as closer to faking a `TrainableComposition`.

    **RED-on-removal:** delete the ``if not isinstance(comp, TrainableComposition):`` guard and the
    stand-in falls through to ``training_bundle_hash(comp, ...)``, which dereferences ``comp.trainable``
    and raises a bare ``AttributeError`` — a fourth, unstructured class the ``pytest.raises(ContractViolation)``
    below does NOT catch, so the test goes RED. The structured ``BUNDLE_REACHES_BYREF_FOLD`` violation is
    produced ONLY by the guard."""
    from conjured.ir.composition import CompositionKind, CompositionMeta

    reg, pipeline = F.build_trainable()

    class _BundleStandIn:
        # A non-own-hash-domain composition the entry substitution does NOT rewrite: a real
        # BUNDLE-kind [meta] (so the message names the kind), deliberately NOT a
        # BundleComposition/Trainable/Pipeline IR model — the guard defends against a walk
        # reaching the fold with anything outside the own-hash-domain allowlist.
        meta = CompositionMeta(kind=CompositionKind.BUNDLE, name="acme.bundle")

    # Register under the SAME path the pipeline's composition node already references, so the node
    # resolves to the stand-in and the by-reference fold is reached.
    reg.add_composition("trainables/dialogue.toml", _BundleStandIn())

    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(pipeline, reg)
    assert exc.value.check is Check.BUNDLE_REACHES_BYREF_FOLD
    assert exc.value.rule_id == "R-pipeline-001"
    # Fail loud + log deep (acceptance 3): the message names what happened and what to check.
    msg = str(exc.value)
    assert "bundle" in msg
    assert "by-reference" in msg
    assert "textual" in msg
