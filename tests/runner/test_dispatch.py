"""The dispatch kernel (``runner.dispatch``) — every happy and error path through the
**constructed wrapper** (validate → call → validate), per the Phase-2 test surface:
binding delivery (deep copy / shared frozen / compile passthrough), both SVE
boundaries, the R-handler-001/output-validation three-way routing, the hook ``None``
contract, the ``ServicesProxy`` through a **test double at the adapter boundary** (no
function patch, no network), and the engine-constructed trainable dispatch.

Handlers here are plain module-level functions handed straight to ``construct`` —
resolution's seals are ``test_resolve_handler``'s territory; the kernel's contract is
everything after a ``HandlerEntry`` exists.
"""

from __future__ import annotations

import pathlib
import re
from types import MappingProxyType

import pytest

from conjured.errors import (
    INPUT_VALIDATION_AUDIT_CODE,
    OUTPUT_VALIDATION_AUDIT_CODE,
    Check,
    ContractViolation,
    SchemaValidationError,
)
from conjured.ir.channel_types import (
    FieldDecl,
    ValidatorSpec,
    dict_of,
    list_of,
    nested,
    primitive,
)
from conjured.ir.common import (
    Binding,
    CompileBinding,
    Delivery,
    SchemaBinding,
)
from conjured.ir.graph import GraphNode, Port
from conjured.runner.dispatch import (
    DispatchContext,
    ResolvedBinding,
    ServiceBindingRuntime,
    _BindingDeliveryError,
    construct,
    construct_trainable,
    new_pipeline_run_id,
)
from conjured.validator.model_gen import build_model
from conjured.validator.resolve_handler import HandlerEntry
from conjured.validator.resolve_validator import FieldValidatorFailure

CTX = DispatchContext(pipeline_run_id="run_2026-06-09T00:00:00Z_t3st", handler_position=0)
TOML = "handlers/fixture.toml"


def _entry(fn, kind="transform", qualified_name="acme.fixture"):
    return HandlerEntry(
        qualified_name=qualified_name,
        callable=fn,
        kind=kind,
        package="acme",
        toml_path=pathlib.Path(TOML),
    )


def _node(input_fields, output_fields, *, kind="transform", qualified_name="acme.fixture"):
    input_ports = tuple(Port(name=f.name, type=f.type) for f in input_fields)
    output_ports = tuple(Port(name=f.name, type=f.type) for f in output_fields)
    return GraphNode(
        position=0,
        node_kind=kind,
        qualified_name=qualified_name,
        input_ports=input_ports,
        output_ports=output_ports,
        read_map={p.name: p.name for p in input_ports},
        write_map={p.name: p.name for p in output_ports},
    )


def _models(input_fields, output_fields):
    return (
        build_model("Reads", tuple(input_fields)),
        build_model("Output", tuple(output_fields)) if output_fields else None,
    )


def _schema_binding(name, value, *, delivery=Delivery.COPY, field_type=None):
    body = SchemaBinding(
        fields=(FieldDecl(name="k", type=field_type or primitive("str")),),
        delivery=delivery,
    )
    return ResolvedBinding(name=name, body=body, value=value)


# ---------------------------------------------------------------------------
# Transform — the happy path through the wrapper
# ---------------------------------------------------------------------------

IN_TEXT = (FieldDecl(name="text", type=primitive("str")),)
OUT_TEXT = (FieldDecl(name="out", type=primitive("str")),)


def _upper(*, text, cfg):
    return {"out": f"{text.upper()}|{cfg['marker']}"}


def test_transform_dispatch_happy():
    reads_model, output_model = _models(IN_TEXT, OUT_TEXT)
    dispatch = construct(
        _entry(_upper),
        _node(IN_TEXT, OUT_TEXT),
        reads_model,
        output_model,
        (_schema_binding("cfg", {"marker": "brackets"}),),
    )
    result = dispatch(reads={"text": "hi"}, ctx=CTX)
    assert result == {"out": "HI|brackets"}
    # invoke-many: the constructed wrapper redispatches cleanly
    assert dispatch(reads={"text": "yo"}, ctx=CTX) == {"out": "YO|brackets"}


# ---------------------------------------------------------------------------
# Binding delivery — the three branches
# ---------------------------------------------------------------------------


def _mutate_copy(*, cfg):
    cfg["tags"].append("mutated")
    return {"out": str(len(cfg["tags"]))}


def test_copy_delivery_is_deep_and_isolated_per_dispatch():
    reads_model, output_model = _models((), OUT_TEXT)
    value = {"tags": ["a"]}
    dispatch = construct(
        _entry(_mutate_copy),
        _node((), OUT_TEXT),
        reads_model,
        output_model,
        (_schema_binding("cfg", value),),
    )
    # A shallow copy would leak the nested-list append into the next dispatch.
    assert dispatch(reads={}, ctx=CTX) == {"out": "2"}
    assert dispatch(reads={}, ctx=CTX) == {"out": "2"}
    assert value == {"tags": ["a"]}  # the resolved source value is untouched


class _Uncopyable:
    """A value whose deep copy raises — the non-deep-copyable COPY-binding adversary
    (Fix 2). ``copy.deepcopy`` calls ``__deepcopy__``, which raises."""

    def __deepcopy__(self, memo):
        raise TypeError("this value is not deep-copyable")


def _never_runs(*, cfg):
    return {"out": "unreachable — binding delivery raises before the body runs"}


def test_copy_delivery_of_a_non_copyable_value_raises_binding_delivery_error():
    """Fix 2 (`binding-delivery-engine-locus`): a COPY-mode binding whose value is not
    deep-copyable raises ``_BindingDeliveryError`` at the binding-delivery step — BEFORE the
    author body runs (the body is never reached). The carrier is what the runner reads as
    the ``engine`` failure_category locus (binding delivery is engine machinery, not an
    author-body fault); it names the failing binding and chains the raw deepcopy error. RED
    if ``_deliver`` stops attributing the deepcopy failure to the carrier (the raw TypeError
    would then escape and be mis-attributed by node_kind at the dispatch boundary)."""
    reads_model, output_model = _models((), OUT_TEXT)
    dispatch = construct(
        _entry(_never_runs),
        _node((), OUT_TEXT),
        reads_model,
        output_model,
        (_schema_binding("cfg", _Uncopyable()),),
    )
    with pytest.raises(_BindingDeliveryError) as exc:
        dispatch(reads={}, ctx=CTX)
    assert exc.value.binding_name == "cfg"
    assert isinstance(exc.value.__cause__, TypeError)  # the raw deepcopy failure is chained


def _read_frozen(*, lookup):
    return {
        "kind": type(lookup).__name__,
        "inner_kind": type(lookup["table"]).__name__,
        "value": lookup["table"]["alias"],
        "obj_id": str(id(lookup)),
    }


def test_reference_delivery_is_deep_frozen_and_shared():
    out_fields = (
        FieldDecl(name="kind", type=primitive("str")),
        FieldDecl(name="inner_kind", type=primitive("str")),
        FieldDecl(name="value", type=primitive("str")),
        FieldDecl(name="obj_id", type=primitive("str")),
    )
    reads_model, output_model = _models((), out_fields)
    dispatch = construct(
        _entry(_read_frozen),
        _node((), out_fields),
        reads_model,
        output_model,
        (
            _schema_binding(
                "lookup",
                {"table": {"alias": "Captain Blackwell"}},
                delivery=Delivery.REFERENCE,
                field_type=dict_of(primitive("str")),
            ),
        ),
    )
    result = dispatch(reads={}, ctx=CTX)
    assert result["kind"] == "mappingproxy"   # dict -> MappingProxyType
    assert result["inner_kind"] == "mappingproxy"  # ...recursively (deep, not shallow)
    assert result["value"] == "Captain Blackwell"
    # ...and SHARED: frozen once at construct — the handler sees the SAME instance
    # on every dispatch (no per-dispatch copy or re-freeze).
    again = dispatch(reads={}, ctx=CTX)
    assert again["obj_id"] == result["obj_id"]


def _write_frozen(*, lookup):
    lookup["table"]["alias"] = "overwritten"  # a write to a reference binding
    return {"out": "unreachable"}


# verifies: reference-binding-write-raises
def test_reference_write_raises_fail_loud():
    reads_model, output_model = _models((), OUT_TEXT)
    dispatch = construct(
        _entry(_write_frozen),
        _node((), OUT_TEXT),
        reads_model,
        output_model,
        (
            _schema_binding(
                "lookup",
                {"table": {"alias": "x"}},
                delivery=Delivery.REFERENCE,
            ),
        ),
    )
    with pytest.raises(TypeError):  # frozen: the write raises, never silently discards
        dispatch(reads={}, ctx=CTX)


def _read_frozen_collections(*, payload):
    items = payload["items"]
    tags = payload["tags"]
    return {
        "items_kind": type(items).__name__,
        "tags_kind": type(tags).__name__,
        "has_append": str(hasattr(items, "append")),
    }


def test_reference_delivery_freezes_lists_and_sets():
    # The canon-named non-mapping freeze branches: list -> tuple, set -> frozenset
    # (recursively, inside the frozen mapping).
    out_fields = (
        FieldDecl(name="items_kind", type=primitive("str")),
        FieldDecl(name="tags_kind", type=primitive("str")),
        FieldDecl(name="has_append", type=primitive("str")),
    )
    reads_model, output_model = _models((), out_fields)
    dispatch = construct(
        _entry(_read_frozen_collections),
        _node((), out_fields),
        reads_model,
        output_model,
        (
            _schema_binding(
                "payload",
                {"items": ["a", "b"], "tags": {"x", "y"}},
                delivery=Delivery.REFERENCE,
            ),
        ),
    )
    result = dispatch(reads={}, ctx=CTX)
    assert result["items_kind"] == "tuple"        # list -> tuple
    assert result["tags_kind"] == "frozenset"     # set -> frozenset
    assert result["has_append"] == "False"        # a list write has no surface at all


def _append_frozen(*, payload):
    payload["items"][0] = "overwritten"  # item assignment on the frozen (tuple) value
    return {"out": "unreachable"}


def test_reference_list_write_raises_fail_loud():
    reads_model, output_model = _models((), OUT_TEXT)
    dispatch = construct(
        _entry(_append_frozen),
        _node((), OUT_TEXT),
        reads_model,
        output_model,
        (_schema_binding("payload", {"items": ["a"]}, delivery=Delivery.REFERENCE),),
    )
    with pytest.raises(TypeError):
        dispatch(reads={}, ctx=CTX)


COMPILED = re.compile(r"\[[^\]]+\]")


def _use_compiled(*, normalizer):
    return {"out": "same" if normalizer is COMPILED else "different"}


def test_compile_binding_passes_through_unchanged():
    reads_model, output_model = _models((), OUT_TEXT)
    binding = ResolvedBinding(
        name="normalizer",
        body=CompileBinding(compiler="regex", params={"pattern": r"\[[^\]]+\]"}),
        value=COMPILED,  # the compiled artifact the binding-resolution pass produced
    )
    dispatch = construct(
        _entry(_use_compiled), _node((), OUT_TEXT), reads_model, output_model, (binding,)
    )
    # Engine-owned, vector-4-copy-exempt: delivered as-is — the identical object.
    assert dispatch(reads={}, ctx=CTX) == {"out": "same"}


# ---------------------------------------------------------------------------
# The input SVE boundary (G11)
# ---------------------------------------------------------------------------


def test_input_validation_error_is_reads_side_sve():
    reads_model, output_model = _models(IN_TEXT, OUT_TEXT)
    dispatch = construct(
        _entry(_upper), _node(IN_TEXT, OUT_TEXT), reads_model, output_model,
        (_schema_binding("cfg", {"marker": "m"}),),
    )
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={"text": 123}, ctx=CTX)
    sve = exc.value
    assert sve.audit_code == INPUT_VALIDATION_AUDIT_CODE
    assert sve.handler_qualified_name == "acme.fixture"
    assert sve.handler_position == 0
    assert sve.pipeline_run_id == CTX.pipeline_run_id
    assert sve.schema_source == TOML
    detail = sve.field_validations[0]
    assert detail.field_path == "reads.text"
    assert detail.expected_type == "str"
    assert detail.actual_type == "int"
    assert detail.constraint_violated == "type"


def test_input_missing_required_read_is_sve_not_contract_violation():
    """The canon-distinctive half of the input boundary: a KEY-SET fault in the reads
    projection (a required kwarg absent) is SchemaValidationError — the kwargs are the
    engine's own assembly, so the key-set→ContractViolation routing applies at the
    OUTPUT boundary only (error-channel/reference.md § the closed enum; gap closed by
    the 2026-06-10 review-on-return verification pass)."""
    reads_model, output_model = _models(IN_TEXT, OUT_TEXT)
    dispatch = construct(
        _entry(_upper), _node(IN_TEXT, OUT_TEXT), reads_model, output_model,
        (_schema_binding("cfg", {"marker": "m"}),),
    )
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={}, ctx=CTX)
    sve = exc.value
    assert sve.audit_code == INPUT_VALIDATION_AUDIT_CODE
    detail = sve.field_validations[0]
    assert detail.field_path == "reads.text"
    assert detail.constraint_violated == "required"
    assert detail.actual_type == "absent"


def test_input_undeclared_extra_read_key_is_sve_not_contract_violation():
    """The other key-set direction at the input boundary: an undeclared extra key in
    the reads dict is also SchemaValidationError (not the output boundary's
    undeclared-key ContractViolation)."""
    reads_model, output_model = _models(IN_TEXT, OUT_TEXT)
    dispatch = construct(
        _entry(_upper), _node(IN_TEXT, OUT_TEXT), reads_model, output_model,
        (_schema_binding("cfg", {"marker": "m"}),),
    )
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={"text": "hi", "bogus": 1}, ctx=CTX)
    sve = exc.value
    assert sve.audit_code == INPUT_VALIDATION_AUDIT_CODE
    detail = sve.field_validations[0]
    assert detail.field_path == "reads.bogus"
    assert detail.constraint_violated == "keys_subset_of"
    assert detail.expected_type == "(undeclared)"


# ---------------------------------------------------------------------------
# The output boundary — the three-way routing (DF-2)
# ---------------------------------------------------------------------------

NESTED_OUT = (
    FieldDecl(name="status", type=primitive("str")),
    FieldDecl(
        name="mood",
        type=nested(
            FieldDecl(name="intensity", type=primitive("int")),
            FieldDecl(name="label", type=primitive("str")),
        ),
    ),
)


def _make_returner(payload):
    def _handler(**_kwargs):
        return payload

    return _handler


def _construct_returning(payload, out_fields=OUT_TEXT):
    reads_model, output_model = _models((), out_fields)
    return construct(
        _entry(_make_returner(payload)), _node((), out_fields), reads_model, output_model, ()
    )


def test_undeclared_output_key_is_contract_violation():
    dispatch = _construct_returning({"out": "x", "smuggled": 1})
    with pytest.raises(ContractViolation) as exc:
        dispatch(reads={}, ctx=CTX)
    cv = exc.value
    assert cv.check is Check.UNDECLARED_OUTPUT_KEY
    assert cv.rule_id == "R-handler-001"
    assert cv.pipeline_run_id == CTX.pipeline_run_id
    assert "smuggled" in cv.actual


def test_undeclared_keys_of_unorderable_types_still_route_to_cv():
    # A return dict with undeclared keys of mixed types (int + str) must surface the
    # structured CV — never a bare TypeError out of the diagnostic's own sort.
    dispatch = _construct_returning({"out": "x", 1: "a", "z": "b"})
    with pytest.raises(ContractViolation) as exc:
        dispatch(reads={}, ctx=CTX)
    assert exc.value.check is Check.UNDECLARED_OUTPUT_KEY


def test_missing_declared_write_is_contract_violation():
    # The DF-2 ruling: a declared output port omitted from the return dict is a
    # top-level key-set fact -> ContractViolation, same class as the undeclared key.
    dispatch = _construct_returning({})
    with pytest.raises(ContractViolation) as exc:
        dispatch(reads={}, ctx=CTX)
    assert exc.value.check is Check.MISSING_DECLARED_WRITE
    assert exc.value.rule_id == "R-handler-001"


def test_non_dict_return_is_contract_violation():
    dispatch = _construct_returning("not a dict")
    with pytest.raises(ContractViolation) as exc:
        dispatch(reads={}, ctx=CTX)
    assert exc.value.check is Check.RETURN_SHAPE


def test_output_type_mismatch_is_output_sve():
    dispatch = _construct_returning({"out": 123})
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={}, ctx=CTX)
    sve = exc.value
    assert sve.audit_code == OUTPUT_VALIDATION_AUDIT_CODE
    detail = sve.field_validations[0]
    assert detail.field_path == "output_schema.out"
    assert detail.constraint_violated == "type"
    assert detail.actual_value == "123"


def test_nested_required_absence_is_sve_not_cv():
    # The other half of the DF-2 ruling: a required field absent WITHIN a declared
    # port's nested value is value-level -> SVE constraint "required" (the top-level
    # key-set case above is the CV).
    dispatch = _construct_returning(
        {"status": "ok", "mood": {"label": "happy"}}, out_fields=NESTED_OUT
    )
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={}, ctx=CTX)
    detail = exc.value.field_validations[0]
    assert detail.field_path == "output_schema.mood.intensity"
    assert detail.constraint_violated == "required"
    assert detail.actual_type == "absent"
    assert detail.actual_value is None


def test_field_validations_follow_declaration_order_no_collapse():
    # Both declared fields fail -> two entries (single-field collapse forbidden),
    # ordered by the violated schema's declaration order (status first, mood second).
    dispatch = _construct_returning(
        {"status": 7, "mood": {"intensity": "high", "label": "happy"}},
        out_fields=NESTED_OUT,
    )
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={}, ctx=CTX)
    paths = [d.field_path for d in exc.value.field_validations]
    assert paths == ["output_schema.status", "output_schema.mood.intensity"]


def test_nested_undeclared_key_is_sve_keys_subset_of():
    # An undeclared key INSIDE a declared port's nested value is within-port value
    # validation (SVE), not the top-level key-set CV.
    dispatch = _construct_returning(
        {"status": "ok", "mood": {"intensity": 1, "label": "x", "extra": True}},
        out_fields=NESTED_OUT,
    )
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={}, ctx=CTX)
    detail = exc.value.field_validations[0]
    assert detail.field_path == "output_schema.mood.extra"
    assert detail.constraint_violated == "keys_subset_of"


def test_none_into_non_nullable_is_constraint_nullable():
    dispatch = _construct_returning({"out": None})
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={}, ctx=CTX)
    detail = exc.value.field_validations[0]
    assert detail.constraint_violated == "nullable"
    assert detail.actual_value is None  # null, distinguishable from the string "None"
    assert detail.actual_type == "NoneType"


def test_actual_value_truncated_with_elision_marker():
    dispatch = _construct_returning({"out": ["x" * 600]})
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={}, ctx=CTX)
    value = exc.value.field_validations[0].actual_value
    assert value is not None and len(value) < 600
    assert "…(+" in value and "chars)" in value


# ---------------------------------------------------------------------------
# The "TOML lied" signal — the call is built from the declaration
# ---------------------------------------------------------------------------


def _narrow(*, text):  # honest def whose params don't match the node's TOML union
    return {"out": text}


def test_honest_signature_mismatch_blows_up_at_dispatch():
    in_fields = (
        FieldDecl(name="text", type=primitive("str")),
        FieldDecl(name="extra_port", type=primitive("str")),
    )
    reads_model, output_model = _models(in_fields, OUT_TEXT)
    dispatch = construct(
        _entry(_narrow), _node(in_fields, OUT_TEXT), reads_model, output_model, ()
    )
    # The engine calls with the full TOML-built union; the function cannot accept it.
    with pytest.raises(TypeError):
        dispatch(reads={"text": "a", "extra_port": "b"}, ctx=CTX)


# ---------------------------------------------------------------------------
# Hooks (G14a)
# ---------------------------------------------------------------------------

HOOK_READS = (FieldDecl(name="dialogue", type=primitive("str")),)

_hook_log: list[str] = []


def _stdlib_hook(*, dialogue):
    _hook_log.append(dialogue)  # stdlib-emission stand-in
    return None


def _bad_hook(*, dialogue):
    return {"oops": dialogue}


def _hook_node():
    return _node(HOOK_READS, (), kind="hook")


def test_stdlib_hook_returns_none():
    reads_model, _ = _models(HOOK_READS, ())
    dispatch = construct(
        _entry(_stdlib_hook, kind="hook"), _hook_node(), reads_model, None, ()
    )
    assert dispatch(reads={"dialogue": "hi"}, ctx=CTX) is None
    assert _hook_log[-1] == "hi"


def test_hook_non_none_return_is_contract_violation():
    reads_model, _ = _models(HOOK_READS, ())
    dispatch = construct(
        _entry(_bad_hook, kind="hook"), _hook_node(), reads_model, None, ()
    )
    with pytest.raises(ContractViolation) as exc:
        dispatch(reads={"dialogue": "hi"}, ctx=CTX)
    assert exc.value.check is Check.HOOK_RETURN_NOT_NONE
    assert exc.value.pipeline_run_id == CTX.pipeline_run_id


def _hook_with_transport(*, dialogue, sink):
    _hook_log.append(f"{dialogue}->{sink}")  # unreachable — delivery raises first
    return None


# verifies: failure-category-engine-is-binding-delivery
def test_hook_transport_non_deep_copyable_value_raises_engine_locus_carrier():
    """SEAL-03 — the hook-transport arm of the binding-delivery engine locus (the binding arm
    is `test_copy_delivery_of_a_non_copyable_value_raises_binding_delivery_error` above; this
    path had no own adversary). A HOOK's deployment-supplied `hook_transport."<qn>"` value is
    delivered to the body as a kwarg through the SAME per-dispatch deep copy as a COPY binding
    (dispatch.py: `_deepcopy_for_delivery` per transport field). A non-deep-copyable transport
    value therefore raises `_BindingDeliveryError` — the `engine` failure_category carrier —
    BEFORE the hook body runs, never a raw deepcopy `TypeError` the dispatch boundary would
    mis-attribute by node_kind to the author hook body. RED if the transport-delivery loop
    stops routing through `_deepcopy_for_delivery` (a plain copy/passthrough would drop the
    engine-locus carrier, and a raw deepcopy failure would escape uncarried)."""
    reads_model, _ = _models(HOOK_READS, ())
    dispatch = construct(
        _entry(_hook_with_transport, kind="hook"),
        _hook_node(),
        reads_model,
        None,
        (),
        hook_transport={"sink": _Uncopyable()},
    )
    with pytest.raises(_BindingDeliveryError) as exc:
        dispatch(reads={"dialogue": "hi"}, ctx=CTX)
    assert exc.value.binding_name == "sink"  # the failing transport field, named in the carrier
    assert isinstance(exc.value.__cause__, TypeError)  # the raw deepcopy failure is chained


# ---------------------------------------------------------------------------
# Service dispatch — through a test double at the adapter boundary
# ---------------------------------------------------------------------------


class SpyAdapter:
    """A recording spy AT the service-typed boundary (not a function patch): records the
    closed dispatch-kwargs the proxy supplies and returns a typed result; builds its
    'client' lazily from transport on first invoke, per the B2 lifecycle."""

    def __init__(self, **identity):
        self.identity = identity
        self.calls: list[dict[str, object]] = []
        self._client = None

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, **extra):
        if self._client is None:
            self._client = {"endpoint": extra.get("endpoint")}
        self.calls.append(
            {
                "input_payload": input_payload,
                "service_name": service_name,
                "caller_qualified_name": caller_qualified_name,
                "caller_position": caller_position,
                "extra": dict(extra),
            }
        )
        return {"embedding": [1.0, 2.0]}


def _embed(*, query_text, services):
    result = services.embedder.invoke(text=query_text)
    return {"embedding": result["embedding"]}


def test_service_dispatch_through_test_double_adapter():
    in_fields = (FieldDecl(name="query_text", type=primitive("str")),)
    out_fields = (FieldDecl(name="embedding", type=list_of(primitive("float"))),)
    reads_model, output_model = _models(in_fields, out_fields)
    double = SpyAdapter(model="bge-small")
    ctx = DispatchContext(pipeline_run_id="run_2026-06-09T00:00:00Z_svc1", handler_position=3)
    dispatch = construct(
        _entry(_embed, kind="service", qualified_name="acme.embed_query"),
        _node(in_fields, out_fields, kind="service", qualified_name="acme.embed_query"),
        reads_model,
        output_model,
        (),
        services=(
            ServiceBindingRuntime(
                name="embedder",
                adapter=double,
                config={"temperature": 0.0},
                transport_extra={"endpoint": "https://emb.test/v1"},
            ),
        ),
    )
    result = dispatch(reads={"query_text": "hello"}, ctx=ctx)
    # The typed result routed through the body into the declared output port:
    assert result == {"embedding": [1.0, 2.0]}
    # The body reached services.<name>.invoke and the proxy supplied the closed
    # dispatch-kwargs — caller_position IS the dispatch position (one value, threaded):
    call = double.calls[0]
    assert call["input_payload"] == {"text": "hello"}
    assert call["service_name"] == "embedder"
    assert call["caller_qualified_name"] == "acme.embed_query"
    assert call["caller_position"] == 3
    assert call["extra"]["temperature"] == 0.0      # config kwargs
    assert call["extra"]["endpoint"] == "https://emb.test/v1"  # transport_extra
    assert double._client == {"endpoint": "https://emb.test/v1"}  # lazy, memoized


# ---------------------------------------------------------------------------
# Trainable dispatch — engine-constructed partial, no author body
# ---------------------------------------------------------------------------

TRAINABLE_IN = (FieldDecl(name="assembled_prompt", type=primitive("str")),)
TRAINABLE_OUT = (FieldDecl(name="dialogue", type=primitive("str")),)


class TrainableSpy:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, **extra):
        self.calls.append(
            {
                "input_payload": input_payload,
                "service_name": service_name,
                "caller_position": caller_position,
                "extra": dict(extra),
            }
        )
        return {"dialogue": "Arr, welcome aboard."}


def _construct_trainable(double):
    reads_model, output_model = _models(TRAINABLE_IN, TRAINABLE_OUT)
    return construct_trainable(
        _node(TRAINABLE_IN, TRAINABLE_OUT, kind="trainable", qualified_name="npc_dialogue"),
        adapter=double,
        binding_name="llm",
        config={"temperature": 0.7, "max_tokens": 256},
        transport_extra={"endpoint": "https://llm.test/v1"},
        reads_model=reads_model,
        output_model=output_model,
        schema_source="compositions/npc_dialogue.toml",
    )


def test_trainable_dispatch_engine_partial_no_body():
    double = TrainableSpy()
    dispatch = _construct_trainable(double)
    ctx = DispatchContext(pipeline_run_id="run_2026-06-09T00:00:00Z_trn1", handler_position=2)
    result = dispatch(reads={"assembled_prompt": "Greet the player."}, ctx=ctx)
    assert result == {"dialogue": "Arr, welcome aboard."}
    call = double.calls[0]
    # input_payload IS the trainable.reads projection; config partial-applied; the
    # closed dispatch-kwargs runner-supplied per dispatch.
    assert call["input_payload"] == {"assembled_prompt": "Greet the player."}
    assert call["service_name"] == "llm"
    assert call["caller_position"] == 2
    assert call["extra"]["temperature"] == 0.7
    assert call["extra"]["endpoint"] == "https://llm.test/v1"


class LyingTrainableSpy(TrainableSpy):
    def invoke(self, **kwargs):
        super().invoke(**kwargs)
        return {"dialogue": 42}  # backend ignored the decode constraint


class SmugglingTrainableSpy(TrainableSpy):
    def invoke(self, **kwargs):
        super().invoke(**kwargs)
        return {"dialogue": "ok", "smuggled": 1}


def test_trainable_cv_locates_trainable_output_schema():
    # The composition TOML has no top-level [output_schema]; a trainable-path CV must
    # point at the section that exists in the artifact: trainable.output_schema.
    dispatch = _construct_trainable(SmugglingTrainableSpy())
    with pytest.raises(ContractViolation) as exc:
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert exc.value.check is Check.UNDECLARED_OUTPUT_KEY
    assert exc.value.section_path == "trainable.output_schema"


def test_trainable_backend_constraint_violation_is_sve():
    # R-handler-005 (literal-equal): a backend response that fails the declared shape
    # raises SchemaValidationError and halts.
    dispatch = _construct_trainable(LyingTrainableSpy())
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={"assembled_prompt": "x"}, ctx=CTX)
    assert exc.value.audit_code == OUTPUT_VALIDATION_AUDIT_CODE
    assert exc.value.schema_source == "compositions/npc_dialogue.toml"


# ---------------------------------------------------------------------------
# Construct-time guards + the run-id mint
# ---------------------------------------------------------------------------


def test_construct_guards():
    reads_model, output_model = _models(IN_TEXT, OUT_TEXT)
    with pytest.raises(ValueError):  # a hook has no output model
        construct(_entry(_stdlib_hook, kind="hook"), _hook_node(), reads_model, output_model, ())
    with pytest.raises(ValueError):  # a channel-writing kind requires one
        construct(_entry(_upper), _node(IN_TEXT, OUT_TEXT), reads_model, None, ())
    with pytest.raises(ValueError):  # a transform has no external-call edge
        construct(
            _entry(_upper), _node(IN_TEXT, OUT_TEXT), reads_model, output_model, (),
            services=(
                ServiceBindingRuntime(name="x", adapter=object(), config={}, transport_extra={}),
            ),
        )


def test_new_pipeline_run_id_form():
    # run_<ISO-8601 basic UTC>_<short-random> (hash-model § canonical event types):
    # colon-free, so it rides a URI verbatim; still lexicographically sortable.
    run_id = new_pipeline_run_id()
    assert re.fullmatch(
        r"run_\d{8}T\d{6}Z_[0-9a-f]{4}", run_id
    ), run_id


# ---------------------------------------------------------------------------
# Field validators at the dispatch boundaries (N1 — the R-handler-012 verdict shim
# through both SVE boundaries; built-ins used so no module fixture is needed)
# ---------------------------------------------------------------------------

CAPPED_OUT = (
    FieldDecl(
        name="intensity", type=primitive("int"),
        validators=(ValidatorSpec(name="maximum", params={"limit": 10}),),
    ),
)


def _construct_with_validators(payload, *, in_fields=(), out_fields=CAPPED_OUT):
    # Validator-carrying models build with the declaring artifact's path — the
    # compose-time diagnostics locus build_model requires.
    reads_model = build_model("Reads", tuple(in_fields), schema_source=TOML)
    output_model = build_model("Output", tuple(out_fields), schema_source=TOML)
    return construct(
        _entry(_make_returner(payload)), _node(in_fields, out_fields),
        reads_model, output_model, (),
    )


def test_validator_pass_dispatches_clean():
    dispatch = _construct_with_validators({"intensity": 10})
    assert dispatch(reads={}, ctx=CTX) == {"intensity": 10}


def test_output_validator_failure_is_output_sve_with_qualified_constraint():
    """A non-None verdict surfaces as the structured SVE field-validation:
    constraint_violated = the validator's qualified name, message = the returned
    reason (handler/reference.md § Validators — the verdict; R-handler-012)."""
    dispatch = _construct_with_validators({"intensity": 11})
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={}, ctx=CTX)
    sve = exc.value
    assert sve.audit_code == OUTPUT_VALIDATION_AUDIT_CODE
    assert sve.handler_qualified_name == "acme.fixture"
    assert sve.pipeline_run_id == CTX.pipeline_run_id
    assert sve.schema_source == TOML
    detail = sve.field_validations[0]
    assert detail.field_path == "output_schema.intensity"
    assert detail.expected_type == "int"
    assert detail.actual_type == "int"
    assert detail.actual_value == "11"
    assert detail.constraint_violated == "maximum"
    assert detail.message == "value 11 above maximum 10"


def test_reads_validator_failure_is_input_sve():
    """The same wrapped constraint fires at the reads boundary — one mechanism, two
    boundaries (the model carries the validator; the boundaries differ in schema and
    audit code only)."""
    in_fields = (
        FieldDecl(
            name="text", type=primitive("str"),
            validators=(ValidatorSpec(name="minLength", params={"limit": 2}),),
        ),
    )
    dispatch = _construct_with_validators(
        {"intensity": 1}, in_fields=in_fields
    )
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={"text": "a"}, ctx=CTX)
    sve = exc.value
    assert sve.audit_code == INPUT_VALIDATION_AUDIT_CODE
    detail = sve.field_validations[0]
    assert detail.field_path == "reads.text"
    assert detail.constraint_violated == "minLength"
    assert detail.message == "length 1 below minLength 2"


def test_third_party_validator_constraint_is_its_qualified_name(tmp_path, monkeypatch):
    """A registered third-party validator contributes its qualified-name constraint —
    why constraint_violated is an open vocabulary (error-channel/reference.md
    § SchemaValidationError payload)."""
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "vdisp_mod.py").write_text(
        "def no_brackets(*, value):\n"
        "    return 'brackets forbidden' if '[' in value else None\n",
        encoding="utf-8",
    )
    import importlib

    importlib.invalidate_caches()
    out_fields = (
        FieldDecl(
            name="line", type=primitive("str"),
            validators=(ValidatorSpec(name="vdisp_mod.no_brackets"),),
        ),
    )
    dispatch = _construct_with_validators({"line": "[smile]"}, out_fields=out_fields)
    with pytest.raises(SchemaValidationError) as exc:
        dispatch(reads={}, ctx=CTX)
    detail = exc.value.field_validations[0]
    assert detail.constraint_violated == "vdisp_mod.no_brackets"
    assert detail.message == "brackets forbidden"


def test_validator_raise_at_dispatch_is_its_own_failure_not_a_verdict(tmp_path, monkeypatch):
    """A raising validator surfaces raw and loud at the dispatch boundary as
    FieldValidatorFailure (with the underlying as __cause__) — never as an SVE
    verdict; the PipelineFailure wrap is the Phase-3 runner's boundary
    (R-handler-012; dispatch.py 'not here, by decision'). (A third-party validator
    carries the raise — the built-in layer can no longer host one: the applicability
    check rejects a mistyped built-in at compose.)"""
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "vdisp_raiser.py").write_text(
        "def explode(*, value):\n    raise TypeError('validator exploded')\n",
        encoding="utf-8",
    )
    import importlib

    importlib.invalidate_caches()
    out_fields = (
        FieldDecl(
            name="label", type=primitive("str"),
            validators=(ValidatorSpec(name="vdisp_raiser.explode"),),
        ),
    )
    dispatch = _construct_with_validators({"label": "calm"}, out_fields=out_fields)
    with pytest.raises(FieldValidatorFailure) as exc:
        dispatch(reads={}, ctx=CTX)
    assert isinstance(exc.value.__cause__, TypeError)
