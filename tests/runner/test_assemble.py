"""Stage-4 assembly (``runner.assemble``) — lifecycle stage 4 into the frozen
``Runnable``: the declaration joins, the resolution seals firing through assembly, the
binding-value resolution (inline / ship-time default / the compile-directive deferral),
the service-config supply gate, and the engine-internal-misuse ValueErrors. Real
modules on ``sys.path`` via ``tmp_path`` (the resolution seals read real source);
doubles only at the adapter seam. The kernel walk over an assembled Runnable is
tests/runner/test_run.py territory."""

from __future__ import annotations

import importlib
import re
import textwrap
from types import MappingProxyType

import pytest

from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import FieldDecl, ValidatorSpec, list_of, primitive
from conjured.ir.common import (
    Binding,
    CompileBinding,
    FilePathBindingValue,
    InlineBindingValue,
    SchemaBinding,
    ServiceBindingDecl,
    ServiceBindingSupply,
)
from conjured.ir.deployment import (
    DeploymentDeclaration,
    HookTransportBlock,
    PipelineOverride,
    TrainingContract,
    TransportBlock,
)
from conjured.ir.pipeline import HandlerNode, PipelineDeclaration, PipelineMeta
from conjured.ir.handler import HookDeclaration, ServiceDeclaration, TransformDeclaration
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.runner.assemble import assemble
from conjured.runner.dispatch import DispatchContext
from conjured.validator import loads
from conjured.validator.compile import compile_pipeline
from conjured.validator.registry import DeclarationRegistry

CTX = DispatchContext(pipeline_run_id="run_2026-06-10T00:00:00Z_asm1", handler_position=0)


@pytest.fixture
def module_dir(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, name: str, source: str) -> None:
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    importlib.invalidate_caches()


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


def _transform(reads, outputs, bindings=()):
    return TransformDeclaration(
        reads=tuple(reads), output_schema=tuple(outputs), bindings=tuple(bindings)
    )


def _compile(pipeline, registry, *, name, deployment=None):
    return compile_pipeline(
        pipeline, registry, pipeline_name=name, deployment=deployment, file_path="p.toml"
    )


# ---------------------------------------------------------------------------
# 6. Happy: a two-transform pipeline assembles into the Runnable shape
# ---------------------------------------------------------------------------


def test_two_transform_pipeline_assembles_runnable_shape(module_dir):
    _write_module(
        module_dir, "asm_chain_mod",
        """
        def first(*, text):
            return {"mid": text.upper()}

        def second(*, mid):
            return {"out": mid + "!"}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "asm_chain_mod.first", _transform((_fd("text"),), (_fd("mid"),)),
        toml_path="handlers/first.toml",
    )
    reg.add_handler(
        "asm_chain_mod.second", _transform((_fd("mid"),), (_fd("out"),)),
        toml_path="handlers/second.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.chain"),
        nodes=(HandlerNode(name="asm_chain_mod.first"), HandlerNode(name="asm_chain_mod.second")),
        inputs=(_fd("text"),),
        outputs=(_fd("out"),),
    )
    graph = _compile(pipeline, reg, name="acme.chain")
    runnable = assemble(graph, reg)

    assert runnable.pipeline_name == "acme.chain"
    assert [n.position for n in runnable.nodes] == [0, 1]  # declared order
    assert [n.qualified_name for n in runnable.nodes] == [
        "asm_chain_mod.first", "asm_chain_mod.second",
    ]
    assert [n.entry_ordinal for n in runnable.nodes] == [0, 1]
    assert runnable.outer_written_channels == frozenset({"mid", "out"})  # inputs excluded
    assert dict(runnable.merges) == {}
    assert tuple(f.name for f in runnable.input_fields) == ("text",)
    assert runnable.graph is graph  # the Phase-4 seam stays addressable
    node = runnable.nodes[0]
    assert node.module.__name__ == "asm_chain_mod"  # the vector-3 snapshot scope (D3)
    assert node.schema_source == "handlers/first.toml"
    assert node.service_binding_name is None
    assert dict(node.bindings_values) == {}
    assert isinstance(node.read_map, MappingProxyType)  # frozen record
    # The constructed dispatch is live (the Phase-2 wrapper, reused not rebuilt):
    assert node.dispatch(reads={"text": "hi"}, ctx=CTX) == {"mid": "HI"}


# ---------------------------------------------------------------------------
# 7. A compile-directive binding → the engine produces + delivers the artifact
# ---------------------------------------------------------------------------


def _compile_binding_pipeline(module_dir, *, pattern):
    """A one-transform pipeline whose `rx` binding is a `compile = "regex"` directive — the
    handler receives the compiled artifact as its `rx` kwarg."""
    _write_module(
        module_dir, "asm_compile_mod",
        """
        def strip(*, text, rx):
            return {"out": "matched" if rx.search(text) else "none"}
        """,
    )
    decl = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(
            Binding(name="rx", body=CompileBinding(compiler="regex", params={"pattern": pattern})),
        ),
    )
    reg = DeclarationRegistry()
    reg.add_handler("asm_compile_mod.strip", decl, toml_path="handlers/strip.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.compile"),
        nodes=(HandlerNode(name="asm_compile_mod.strip"),),
        inputs=(_fd("text"),),
    )
    return _compile(pipeline, reg, name="acme.compile"), reg


def test_compile_binding_produces_and_delivers_the_artifact(module_dir):
    """The engine resolves the named compiler, runs it once at binding resolution, and delivers
    the compiled `re.Pattern` as the binding's engine-owned kwarg value — reaching the handler
    body and working end-to-end."""
    graph, reg = _compile_binding_pipeline(module_dir, pattern=r"\[[^\]]+\]")
    runnable = assemble(graph, reg)
    node = runnable.nodes[0]
    assert isinstance(dict(node.bindings_values)["rx"], re.Pattern)  # artifact, not the directive
    assert node.dispatch(reads={"text": "[hi]"}, ctx=CTX) == {"out": "matched"}
    assert node.dispatch(reads={"text": "plain"}, ctx=CTX) == {"out": "none"}


# verifies: compile-failure-at-compose-not-dispatch
def test_compile_failure_raises_at_assembly_never_at_dispatch(module_dir):
    """A malformed `regex` compile directive raises a ContractViolation at the stage-4
    binding-resolution pass (compose) — `assemble` raises and no Runnable is produced, so the
    node never dispatches. RED-on-removal: defer the run to dispatch and assemble would
    succeed, surfacing the failure only at run time."""
    graph, reg = _compile_binding_pipeline(module_dir, pattern="[unterminated")
    with pytest.raises(ContractViolation) as exc:
        assemble(graph, reg)
    cv = exc.value
    assert cv.check is Check.COMPILE_ARTIFACT
    assert cv.rule_id == "R-pipeline-001"
    assert cv.file_path == "handlers/strip.toml"


# ---------------------------------------------------------------------------
# 8. Config supply — the service_bindings.<name> config block (compose checks +
#    delivery; the Phase-3 SERVICE_CONFIG_SUPPLY_UNSUPPORTED gate is RETIRED,
#    superseded by this surface)
# ---------------------------------------------------------------------------


def _cfg_fixture(module_dir, *, supply_config, config_schema=None):
    """A service handler bound to a config-schema'd service-type, with `supply_config`
    on the pipeline's supply entry. Returns (registry, pipeline)."""
    _write_module(
        module_dir, "asm_cfg_mod",
        """
        def call(*, text, services):
            return {"out": services.llm.invoke(q=text)["echo"]}
        """,
    )
    _write_module(
        module_dir, "asm_cfg_adapters",
        """
        class Adapter:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, max_tokens, **transport_extra):
                return {"echo": f"{input_payload['q']}|t={temperature}|n={max_tokens}"}
        """,
    )
    service_type = ServiceTypeDeclaration(
        name="asm_cfg_adapters.Adapter",
        identity_schema=(_fd("model"),),
        transport_schema=(),
        config_schema=config_schema
        if config_schema is not None
        else (
            FieldDecl(name="temperature", type=primitive("float")),
            FieldDecl(
                name="max_tokens", type=primitive("int"), default=64
            ),  # default-bearing — coverable by omission
        ),
    )
    decl = ServiceDeclaration(
        reads=(_fd("text"),),
        output_schema=(_fd("out"),),
        service_bindings=(
            ServiceBindingDecl(name="llm", type="asm_cfg_adapters.Adapter"),
        ),
    )
    reg = DeclarationRegistry()
    reg.add_service_type(service_type, toml_path="st.toml")
    reg.add_handler("asm_cfg_mod.call", decl, toml_path="handlers/call.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.cfg"),
        nodes=(HandlerNode(name="asm_cfg_mod.call"),),
        service_bindings=(
            ServiceBindingSupply(
                name="llm", type="asm_cfg_adapters.Adapter",
                identity={"model": "m"}, config=supply_config,
            ),
        ),
        inputs=(_fd("text"),),
    )
    return reg, pipeline


def test_uncovered_config_field_fails_compose(module_dir):
    """The covered direction (declared ⊆ supplied-or-default): a declared
    [config_schema] field neither supplied in the binding's config block nor carrying
    a declared ship-time default fails COMPOSE with the exact CV."""
    reg, pipeline = _cfg_fixture(module_dir, supply_config={})  # temperature uncovered
    with pytest.raises(ContractViolation) as exc:
        _compile(pipeline, reg, name="acme.cfg")
    cv = exc.value
    assert cv.check is Check.CONFIG_SCHEMA_SUPPLY
    assert cv.rule_id == "R-service-type-002"
    assert "temperature" in cv.actual
    assert cv.section_path == "service_bindings.llm.config"


def test_undeclared_config_key_fails_compose(module_dir):
    """The declared direction (supplied ⊆ declared): a config-block key outside the
    bound service-type's [config_schema] fails COMPOSE with the exact CV."""
    reg, pipeline = _cfg_fixture(
        module_dir, supply_config={"temperature": 0.7, "template": "you are an npc"}
    )
    with pytest.raises(ContractViolation) as exc:
        _compile(pipeline, reg, name="acme.cfg")
    cv = exc.value
    assert cv.check is Check.CONFIG_SCHEMA_SUPPLY
    assert cv.rule_id == "R-service-type-002"
    assert "template" in cv.actual


def test_config_supply_delivers_effective_values_to_invoke(module_dir):
    """The trainable-precedent delivery: the binding's effective config values
    (supplied temperature + the DECLARED ship-time default for the omitted
    max_tokens) reach the adapter's invoke() as its config kwargs at dispatch."""
    reg, pipeline = _cfg_fixture(module_dir, supply_config={"temperature": 0.7})
    deployment = DeploymentDeclaration(
        transport=(TransportBlock(name="llm", values={}),),  # zero-field schema: empty block
        training_contract=TrainingContract(integrity_enforcement=False),
    )
    graph = _compile(pipeline, reg, name="acme.cfg", deployment=deployment)
    runnable = assemble(graph, reg, deployment)
    out = runnable.nodes[0].dispatch(reads={"text": "hi"}, ctx=CTX)
    assert out == {"out": "hi|t=0.7|n=64"}  # supplied override + declared default


# ---------------------------------------------------------------------------
# 9. Ship-time default delivered when the node omits the binding
# ---------------------------------------------------------------------------


def test_ship_time_default_delivered_when_binding_omitted(module_dir):
    _write_module(
        module_dir, "asm_default_mod",
        """
        def mark(*, text, cfg):
            return {"out": text + cfg}
        """,
    )
    decl = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(
            Binding(
                name="cfg",
                body=SchemaBinding(fields=(_fd("marker"),), default={"marker": "D"}),
            ),
        ),
    )
    reg = DeclarationRegistry()
    reg.add_handler("asm_default_mod.mark", decl, toml_path="handlers/mark.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.default"),
        nodes=(HandlerNode(name="asm_default_mod.mark"),),  # binding omitted
        inputs=(_fd("text"),),
    )
    runnable = assemble(_compile(pipeline, reg, name="acme.default"), reg)
    # The single-field ship-time default (a one-field table) normalizes to its bare value.
    assert dict(runnable.nodes[0].bindings_values) == {"cfg": "D"}
    assert runnable.nodes[0].dispatch(reads={"text": "a"}, ctx=CTX) == {"out": "aD"}


def test_supplied_inline_value_wins_over_default(module_dir):
    _write_module(
        module_dir, "asm_supplied_mod",
        """
        def mark(*, text, cfg):
            return {"out": text + cfg}
        """,
    )
    decl = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(
            Binding(
                name="cfg",
                body=SchemaBinding(fields=(_fd("marker"),), default={"marker": "D"}),
            ),
        ),
    )
    reg = DeclarationRegistry()
    reg.add_handler("asm_supplied_mod.mark", decl, toml_path="handlers/mark.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.supplied"),
        nodes=(
            HandlerNode(
                name="asm_supplied_mod.mark",
                bindings=(InlineBindingValue(name="cfg", value={"marker": "S"}),),
            ),
        ),
        inputs=(_fd("text"),),
    )
    runnable = assemble(_compile(pipeline, reg, name="acme.supplied"), reg)
    # Supplied value (one-field table) wins over the default, normalized to its bare value.
    assert dict(runnable.nodes[0].bindings_values) == {"cfg": "S"}


# ---------------------------------------------------------------------------
# 9b. Binding-value validation at assemble (D4) — the missing enforcement point
# ---------------------------------------------------------------------------


def _bind_assemble(module_dir, mod, fields, supply):
    """Compose + assemble a one-node pipeline whose `cfg` binding declares `fields` and the
    node supplies `supply` inline. Raises at assemble on a binding value that violates the
    declared binding schema (D4)."""
    _write_module(module_dir, mod, "def mark(*, text, cfg):\n    return {'out': text}\n")
    decl = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(Binding(name="cfg", body=SchemaBinding(fields=tuple(fields))),),
    )
    reg = DeclarationRegistry()
    reg.add_handler(f"{mod}.mark", decl, toml_path="handlers/mark.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.bind"),
        nodes=(HandlerNode(
            name=f"{mod}.mark",
            bindings=(InlineBindingValue(name="cfg", value=supply),),
        ),),
        inputs=(_fd("text"),),
    )
    return assemble(_compile(pipeline, reg, name="acme.bind"), reg)


def test_binding_value_wrong_type_rejects_at_assemble(module_dir):
    # D4: a binding value violating its declared field type is caught at assemble (the
    # missing enforcement point) — a ContractViolation (BINDING_VALUE_SHAPE), not the
    # dispatch-only SchemaValidationError.
    with pytest.raises(ContractViolation) as exc:
        _bind_assemble(
            module_dir, "asm_bind_wt_mod", (_fd("marker", "int"),), {"marker": "not-an-int"}
        )
    cv = exc.value
    assert cv.check is Check.BINDING_VALUE_SHAPE
    assert cv.rule_id == "R-pipeline-001"
    assert "marker" in cv.section_path


def test_binding_field_constraint_enforces_at_assemble(module_dir):
    # The D4 payoff: a constraint on a binding field now enforces "for free" (the same
    # Pydantic validator the reads/output boundaries use). count >= 1; a supplied 0 rejects.
    count = FieldDecl(
        name="count", type=primitive("int"),
        validators=(ValidatorSpec(name="minimum", params={"limit": 1}),),
    )
    with pytest.raises(ContractViolation) as exc:
        _bind_assemble(module_dir, "asm_bind_con_mod", (count,), {"count": 0})
    assert exc.value.check is Check.BINDING_VALUE_SHAPE
    # A conforming value assembles clean; the single-field value normalizes to bare.
    runnable = _bind_assemble(module_dir, "asm_bind_con_ok_mod", (count,), {"count": 5})
    assert dict(runnable.nodes[0].bindings_values) == {"cfg": 5}


def test_valid_object_binding_assembles_clean(module_dir):
    # A single-field binding supplied as a one-field table normalizes to its bare value.
    runnable = _bind_assemble(
        module_dir, "asm_bind_obj_mod", (_fd("marker"),), {"marker": "ok"}
    )
    assert dict(runnable.nodes[0].bindings_values) == {"cfg": "ok"}


def test_scalar_binding_validates_against_the_single_field(module_dir):
    # canon § Binding value-supply grammar: a bare scalar for a single-field binding is the
    # field's value. A valid scalar assembles; a wrong-typed scalar rejects.
    runnable = _bind_assemble(
        module_dir, "asm_bind_scalar_ok_mod", (_fd("prompt"),), "You are a gruff keeper."
    )
    assert runnable.nodes[0].bindings_values["cfg"] == "You are a gruff keeper."
    with pytest.raises(ContractViolation) as exc:
        _bind_assemble(module_dir, "asm_bind_scalar_bad_mod", (_fd("n", "int"),), "not-int")
    assert exc.value.check is Check.BINDING_VALUE_SHAPE


def test_scalar_for_a_multi_field_binding_rejects(module_dir):
    # A bare scalar for a MULTI-field binding is malformed — the scalar shorthand is
    # single-field only; a multi-field binding needs an object keyed by field name.
    with pytest.raises(ContractViolation) as exc:
        _bind_assemble(
            module_dir, "asm_bind_multi_mod", (_fd("a"), _fd("b")), "scalar"
        )
    cv = exc.value
    assert cv.check is Check.BINDING_VALUE_SHAPE
    assert "multi-field" in cv.actual


# ---------------------------------------------------------------------------
# 9c. Single-field binding delivery normalization (binding-delivery-normalization arc)
#     — one logical value, one delivered (bare) shape across every supply route.
# ---------------------------------------------------------------------------


def test_single_field_binding_delivers_bare_identically_across_supply_routes(module_dir):
    """RED-on-removal against the compose-join normalization: a single-field binding
    supplied as a bare value, as its one-field inline table, OR from an external file
    delivers the IDENTICAL bare value — never the field-keyed dict. The D2 `probe_phrases`
    array case: a single-field ``list[str]`` binding. Without the normalization the
    table/file routes would deliver ``{"probe_phrases": [...]}`` while the bare route
    delivers ``[...]`` — a silent per-route delivery split, exactly the defect this arc
    closes (handler/reference.md § Binding value-supply grammar, the normalization region)."""
    phrases = ["Care for a room?", "The usual?"]

    def build(supply):
        _write_module(module_dir, "asm_probe_mod",
                      "def mark(*, text, probes):\n    return {'out': text}\n")
        decl = _transform(
            (_fd("text"),), (_fd("out"),),
            bindings=(Binding(name="probes", body=SchemaBinding(
                fields=(FieldDecl(name="probe_phrases", type=list_of(primitive("str"))),))),),
        )
        reg = DeclarationRegistry()
        reg.add_handler("asm_probe_mod.mark", decl, toml_path="handlers/mark.toml")
        pipeline = PipelineDeclaration(
            meta=PipelineMeta(name="acme.probe"),
            nodes=(HandlerNode(name="asm_probe_mod.mark", bindings=(supply,)),),
            inputs=(_fd("text"),),
        )
        return assemble(_compile(pipeline, reg, name="acme.probe"), reg)

    bare = build(InlineBindingValue(name="probes", value=phrases))
    table = build(InlineBindingValue(name="probes", value={"probe_phrases": phrases}))
    from_file = build(FilePathBindingValue(
        name="probes", path="probes.toml",
        content_hash="sha256:" + "0" * 64, resolved={"probe_phrases": phrases}))

    for r in (bare, table, from_file):
        # The bare list is the delivered value — never the one-field wrapper dict.
        assert dict(r.nodes[0].bindings_values) == {"probes": phrases}


def test_single_field_ship_time_default_normalizes_to_bare(module_dir):
    """Criterion 2: a single-field binding's ship-time default normalizes to the bare value
    exactly as a supplied value does — defending against the default arm carrying the
    un-normalized dict. A default declared as a one-field table and a bare default of the
    same logical value both deliver the identical bare value when the node omits the
    binding (and the handler reads ``cfg`` directly, never ``cfg["marker"]``)."""
    def build(default_value):
        _write_module(module_dir, "asm_def_norm_mod",
                      "def mark(*, text, cfg):\n    return {'out': text + cfg}\n")
        decl = _transform(
            (_fd("text"),), (_fd("out"),),
            bindings=(Binding(name="cfg", body=SchemaBinding(
                fields=(_fd("marker"),), default=default_value)),),
        )
        reg = DeclarationRegistry()
        reg.add_handler("asm_def_norm_mod.mark", decl, toml_path="handlers/mark.toml")
        pipeline = PipelineDeclaration(
            meta=PipelineMeta(name="acme.defnorm"),
            nodes=(HandlerNode(name="asm_def_norm_mod.mark"),),  # binding omitted
            inputs=(_fd("text"),),
        )
        return assemble(_compile(pipeline, reg, name="acme.defnorm"), reg)

    table_default = build({"marker": "D"})   # one-field table default
    bare_default = build("D")                # bare default of the same logical value
    assert dict(table_default.nodes[0].bindings_values) == {"cfg": "D"}
    assert dict(bare_default.nodes[0].bindings_values) == {"cfg": "D"}
    # Delivered bare — the handler reads `cfg` directly.
    assert table_default.nodes[0].dispatch(reads={"text": "a"}, ctx=CTX) == {"out": "aD"}


# ---------------------------------------------------------------------------
# 10. The item-9 internal assertion: graph ports ≡ declaration (engine-bug attribution)
# ---------------------------------------------------------------------------


def test_registry_drift_between_compile_and_assemble_hits_the_assertion(module_dir):
    _write_module(
        module_dir, "asm_drift_mod",
        """
        def f(*, text):
            return {"out": text}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "asm_drift_mod.f", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/f.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.drift"),
        nodes=(HandlerNode(name="asm_drift_mod.f"),),
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name="acme.drift")
    # The registry drifts AFTER compile: same name, different reads.
    reg.add_handler(
        "asm_drift_mod.f", _transform((_fd("other"),), (_fd("out"),)),
        toml_path="handlers/f.toml",
    )
    with pytest.raises(AssertionError, match="diverge from the joined declaration"):
        assemble(graph, reg)


# ---------------------------------------------------------------------------
# 11. Missing declaration path in the registry → ValueError (engine-internal misuse)
# ---------------------------------------------------------------------------


def test_missing_handler_path_is_engine_internal_misuse(module_dir):
    _write_module(
        module_dir, "asm_nopath_mod",
        """
        def f(*, text):
            return {"out": text}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler("asm_nopath_mod.f", _transform((_fd("text"),), (_fd("out"),)))
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.nopath"),
        nodes=(HandlerNode(name="asm_nopath_mod.f"),),
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name="acme.nopath")
    with pytest.raises(ValueError, match="no declaration path registered"):
        assemble(graph, reg)


# ---------------------------------------------------------------------------
# 12. The Phase-2 resolution seals fire through assemble, before any dispatch
# ---------------------------------------------------------------------------


def test_pure_module_seal_fires_through_assemble(module_dir):
    _write_module(
        module_dir, "asm_impure_mod",
        """
        cache = {}

        def f(*, text):
            return {"out": text}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "asm_impure_mod.f", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/f.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.impure"),
        nodes=(HandlerNode(name="asm_impure_mod.f"),),
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name="acme.impure")
    with pytest.raises(ContractViolation) as exc:
        assemble(graph, reg)
    assert exc.value.check is Check.HANDLER_PURE_MODULE
    assert exc.value.rule_id == "R-handler-pure-module"


# ---------------------------------------------------------------------------
# Backend-SDK hook transport: the binding's transport.<name> block reaches the
# adapter (the seam compose validates is the seam assembly delivers)
# ---------------------------------------------------------------------------


def test_backend_sdk_hook_transport_delivered_from_the_binding_block(module_dir):
    _write_module(
        module_dir, "asm_hooktx_mod",
        """
        def watch(*, out, services):
            response = services.emit.invoke(line=out)
            assert response["endpoint"] == "https://emit.test/v1", "transport never arrived"
        """,
    )
    _write_module(
        module_dir, "asm_hooktx_adapters",
        """
        class EmitAdapter:
            def __init__(self, sink):
                self.sink = sink

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"endpoint": transport_extra.get("endpoint")}
        """,
    )
    type_name = "asm_hooktx_adapters.EmitAdapter"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("sink"),),
            transport_schema=(_fd("endpoint"),),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        "asm_hooktx_mod.watch",
        HookDeclaration(
            reads=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="emit", type=type_name),),
        ),
        toml_path="handlers/watch.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.hooktx"),
        nodes=(HandlerNode(name="asm_hooktx_mod.watch"),),
        service_bindings=(
            ServiceBindingSupply(name="emit", type=type_name, identity={"sink": "s"}),
        ),
        inputs=(_fd("out"),),
    )
    deployment = DeploymentDeclaration(
        transport=(
            TransportBlock(name="emit", values={"endpoint": "https://emit.test/v1"}),
        ),
        hook_transport=(
            HookTransportBlock(hook_qualified_name="asm_hooktx_mod.watch"),
        ),
        training_contract=TrainingContract(integrity_enforcement=False),
    )
    graph = _compile(pipeline, reg, name="acme.hooktx", deployment=deployment)
    runnable = assemble(graph, reg, deployment)
    node = runnable.nodes[0]
    assert node.node_kind == "hook" and node.service_binding_name == "emit"
    # The body ASSERTS the endpoint arrived — a regression surfaces as a raw
    # AssertionError out of this direct dispatch call (no runner absorption here).
    assert node.dispatch(reads={"out": "x"}, ctx=CTX) is None


def test_stdlib_hook_missing_covering_block_is_engine_internal_misuse(module_dir):
    """A stdlib-emission hook with declared transport_schema fields and no covering
    hook_transport."<qn>" block at assembly is registry/deployment drift — the
    author-facing coverage check is compose-time (R-pipeline-001); assembly fails
    loud with the established ValueError posture, never a silent empty delivery."""
    _write_module(
        module_dir, "asm_stdlibtx_mod",
        """
        def watch(*, out, log_path):
            return None
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "asm_stdlibtx_mod.watch",
        HookDeclaration(
            reads=(_fd("out"),),
            transport_schema=(_fd("log_path"),),
        ),
        toml_path="handlers/watch.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.stdlibtx"),
        nodes=(HandlerNode(name="asm_stdlibtx_mod.watch"),),
        inputs=(_fd("out"),),
    )
    deployment = DeploymentDeclaration(
        hook_transport=(
            HookTransportBlock(
                hook_qualified_name="asm_stdlibtx_mod.watch",
                values={"log_path": "/var/log/a.jsonl"},
            ),
        ),
        training_contract=TrainingContract(integrity_enforcement=False),
    )
    # Compose passed WITH the covering deployment; assembling without one fails loud.
    graph = _compile(pipeline, reg, name="acme.stdlibtx", deployment=deployment)
    with pytest.raises(ValueError, match=r'no hook_transport\."asm_stdlibtx_mod\.watch" block'):
        assemble(graph, reg, None)


def test_missing_transport_home_for_declared_fields_is_engine_internal_misuse(module_dir):
    _write_module(
        module_dir, "asm_notx_mod",
        """
        def call(*, text, services):
            return {"out": text}
        """,
    )
    _write_module(
        module_dir, "asm_notx_adapters",
        """
        class Adapter:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"out": "x"}
        """,
    )
    type_name = "asm_notx_adapters.Adapter"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),),
            transport_schema=(_fd("endpoint"),),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        "asm_notx_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.notx"),
        nodes=(HandlerNode(name="asm_notx_mod.call"),),
        service_bindings=(
            ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),
        ),
        inputs=(_fd("text"),),
    )
    deployment = DeploymentDeclaration(
        transport=(
            TransportBlock(name="llm", values={"endpoint": "https://llm.test"}),
        ),
        training_contract=TrainingContract(integrity_enforcement=False),
    )
    # Compose passed WITH the covering deployment; assembling without one is
    # engine-internal misuse (the author-facing gate is the compose-time coverage
    # check) — fail loud, never a silent empty transport.
    graph = _compile(pipeline, reg, name="acme.notx", deployment=deployment)
    with pytest.raises(ValueError, match="no transport.llm block covers"):
        assemble(graph, reg, None)


def test_pipeline_override_transport_beats_the_shared_block(module_dir):
    _write_module(
        module_dir, "asm_ovr_mod",
        """
        def call(*, text, services):
            return {"out": services.llm.invoke(q=text)["endpoint"]}
        """,
    )
    _write_module(
        module_dir, "asm_ovr_adapters",
        """
        class EchoAdapter:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"endpoint": transport_extra.get("endpoint")}
        """,
    )
    type_name = "asm_ovr_adapters.EchoAdapter"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),),
            transport_schema=(_fd("endpoint"),),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        "asm_ovr_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )

    def _pipeline(name):
        return PipelineDeclaration(
            meta=PipelineMeta(name=name),
            nodes=(HandlerNode(name="asm_ovr_mod.call"),),
            service_bindings=(
                ServiceBindingSupply(
                    name="llm", type=type_name, identity={"model": "m"}
                ),
            ),
            inputs=(_fd("text"),),
        )

    deployment = DeploymentDeclaration(
        transport=(
            TransportBlock(name="llm", values={"endpoint": "https://shared.test"}),
        ),
        pipelines=(
            PipelineOverride(
                pipeline_qualified_name="acme.ovr",
                transport=(
                    TransportBlock(
                        name="llm", values={"endpoint": "https://override.test"}
                    ),
                ),
            ),
        ),
        training_contract=TrainingContract(integrity_enforcement=False),
    )
    ovr = assemble(
        _compile(_pipeline("acme.ovr"), reg, name="acme.ovr", deployment=deployment),
        reg, deployment,
    )
    plain = assemble(
        _compile(_pipeline("acme.plain"), reg, name="acme.plain", deployment=deployment),
        reg, deployment,
    )
    # Canon's deterministic resolution order: a pipelines."<name>" override for THAT
    # pipeline wins; any other pipeline resolves the shared block.
    assert ovr.nodes[0].dispatch(reads={"text": "t"}, ctx=CTX) == {
        "out": "https://override.test"
    }
    assert plain.nodes[0].dispatch(reads={"text": "t"}, ctx=CTX) == {
        "out": "https://shared.test"
    }


# ---------------------------------------------------------------------------
# The stamped external-file binding branch (FilePathBindingValue -> .resolved)
# ---------------------------------------------------------------------------


def test_stamped_external_file_binding_value_is_consumed(module_dir):
    _write_module(
        module_dir, "asm_file_mod",
        """
        def mark(*, text, cfg):
            return {"out": text + cfg}
        """,
    )
    decl = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(Binding(name="cfg", body=SchemaBinding(fields=(_fd("marker"),))),),
    )
    reg = DeclarationRegistry()
    reg.add_handler("asm_file_mod.mark", decl, toml_path="handlers/mark.toml")
    stamped = FilePathBindingValue(
        name="cfg", path="npcs/values.toml",
        content_hash="sha256:" + "0" * 64, resolved={"marker": "F"},
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.file"),
        nodes=(HandlerNode(name="asm_file_mod.mark", bindings=(stamped,)),),
        inputs=(_fd("text"),),
    )
    runnable = assemble(_compile(pipeline, reg, name="acme.file"), reg)
    # The stage-1-stamped resolved content IS the delivered value (the path is a locator,
    # never the value); a SINGLE-FIELD binding normalizes its one-field resolved table to
    # the bare value at the compose join, so `cfg` arrives bare — not `{"marker": "F"}`.
    assert dict(runnable.nodes[0].bindings_values) == {"cfg": "F"}
    assert runnable.nodes[0].dispatch(reads={"text": "a"}, ctx=CTX) == {"out": "aF"}


def test_unresolved_external_file_binding_is_engine_internal_misuse(module_dir):
    _write_module(
        module_dir, "asm_fileunres_mod",
        """
        def mark(*, text, cfg):
            return {"out": text + cfg["marker"]}
        """,
    )
    decl = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(Binding(name="cfg", body=SchemaBinding(fields=(_fd("marker"),))),),
    )
    reg = DeclarationRegistry()
    reg.add_handler("asm_fileunres_mod.mark", decl, toml_path="handlers/mark.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.fileunres"),
        nodes=(
            HandlerNode(
                name="asm_fileunres_mod.mark",
                bindings=(
                    FilePathBindingValue(name="cfg", path="npcs/values.toml"),
                ),
            ),
        ),
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name="acme.fileunres")
    with pytest.raises(ValueError, match="is unresolved"):
        assemble(graph, reg)


# ---------------------------------------------------------------------------
# The bindings half of the item-9 cross-check (registry drift on supplied values)
# ---------------------------------------------------------------------------


def test_orphan_supplied_binding_after_drift_hits_the_bindings_assertion(module_dir):
    _write_module(
        module_dir, "asm_bdrift_mod",
        """
        def f(*, text):
            return {"out": text}
        """,
    )
    decl_with_binding = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(Binding(name="knob", body=SchemaBinding(fields=(_fd("level"),))),),
    )
    reg = DeclarationRegistry()
    reg.add_handler("asm_bdrift_mod.f", decl_with_binding, toml_path="handlers/f.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.bdrift"),
        nodes=(
            HandlerNode(
                name="asm_bdrift_mod.f",
                bindings=(InlineBindingValue(name="knob", value={"level": "x"}),),
            ),
        ),
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name="acme.bdrift")
    # The registry drifts: the declaration loses its binding — without the bindings
    # half of the item-9 cross-check the supplied "knob" would silently drop.
    reg.add_handler(
        "asm_bdrift_mod.f", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/f.toml",
    )
    with pytest.raises(AssertionError, match=r"supplies binding value\(s\) \['knob'\]"):
        assemble(graph, reg)


# ---------------------------------------------------------------------------
# The engine-internal-misuse ValueError battery (every reachable guard fires)
# ---------------------------------------------------------------------------


def test_unsupplied_binding_without_default_after_drift_fails_loud(module_dir):
    _write_module(
        module_dir, "asm_nodefault_mod",
        """
        def mark(*, text, cfg):
            return {"out": text}
        """,
    )
    with_default = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(
            Binding(
                name="cfg",
                body=SchemaBinding(fields=(_fd("marker"),), default={"marker": "D"}),
            ),
        ),
    )
    reg = DeclarationRegistry()
    reg.add_handler("asm_nodefault_mod.mark", with_default, toml_path="handlers/mark.toml")
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.nodefault"),
        nodes=(HandlerNode(name="asm_nodefault_mod.mark"),),  # binding omitted
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name="acme.nodefault")
    # Drift: the same binding loses its ship-time default.
    without_default = _transform(
        (_fd("text"),), (_fd("out"),),
        bindings=(Binding(name="cfg", body=SchemaBinding(fields=(_fd("marker"),))),),
    )
    reg.add_handler("asm_nodefault_mod.mark", without_default, toml_path="handlers/mark.toml")
    with pytest.raises(ValueError, match="unsupplied and declares no ship-time default"):
        assemble(graph, reg)


def _service_drift_fixture(module_dir, suffix):
    _write_module(
        module_dir, f"asm_sdrift_{suffix}_mod",
        """
        def call(*, text, services):
            return {"out": text}
        """,
    )
    _write_module(
        module_dir, f"asm_sdrift_{suffix}_adapters",
        """
        class Adapter:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"out": "x"}
        """,
    )
    type_name = f"asm_sdrift_{suffix}_adapters.Adapter"
    handler_name = f"asm_sdrift_{suffix}_mod.call"
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),), transport_schema=(),
        ),
        toml_path="st.toml",
    )
    reg.add_handler(
        handler_name,
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name=f"acme.sdrift{suffix}"),
        nodes=(HandlerNode(name=handler_name),),
        service_bindings=(
            ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),
        ),
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name=f"acme.sdrift{suffix}")
    return graph, reg, type_name, handler_name


def test_missing_service_supply_after_drift_fails_loud(module_dir):
    graph, reg, type_name, handler_name = _service_drift_fixture(module_dir, "supply")
    # Drift: the declaration's binding renames; the graph's supply set no longer
    # carries it.
    reg.add_handler(
        handler_name,
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm2", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    with pytest.raises(ValueError, match="no service_bindings.llm2 identity supply"):
        assemble(graph, reg)


def test_unregistered_service_type_after_drift_fails_loud(module_dir):
    graph, reg, _type_name, handler_name = _service_drift_fixture(module_dir, "sttype")
    # Drift: the binding keeps its name but points at a type the registry lacks.
    reg.add_handler(
        handler_name,
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type="ghost.Type"),),
        ),
        toml_path="handlers/call.toml",
    )
    with pytest.raises(
        ValueError, match="service-type 'ghost.Type' is not in the registry"
    ):
        assemble(graph, reg)


def test_missing_service_type_path_is_engine_internal_misuse(module_dir):
    _write_module(
        module_dir, "asm_stnopath_mod",
        """
        def call(*, text, services):
            return {"out": text}
        """,
    )
    _write_module(
        module_dir, "asm_stnopath_adapters",
        """
        class Adapter:
            def __init__(self, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, **transport_extra):
                return {"out": "x"}
        """,
    )
    type_name = "asm_stnopath_adapters.Adapter"
    reg = DeclarationRegistry()
    reg.add_service_type(  # registered WITHOUT toml_path
        ServiceTypeDeclaration(
            name=type_name, identity_schema=(_fd("model"),), transport_schema=(),
        ),
    )
    reg.add_handler(
        "asm_stnopath_mod.call",
        ServiceDeclaration(
            reads=(_fd("text"),), output_schema=(_fd("out"),),
            service_bindings=(ServiceBindingDecl(name="llm", type=type_name),),
        ),
        toml_path="handlers/call.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.stnopath"),
        nodes=(HandlerNode(name="asm_stnopath_mod.call"),),
        service_bindings=(
            ServiceBindingSupply(name="llm", type=type_name, identity={"model": "m"}),
        ),
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name="acme.stnopath")
    with pytest.raises(
        ValueError, match="no declaration path registered for service-type"
    ):
        assemble(graph, reg)


def test_missing_handler_after_drift_fails_loud(module_dir):
    _write_module(
        module_dir, "asm_hdrift_mod",
        """
        def f(*, text):
            return {"out": text}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "asm_hdrift_mod.f", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/f.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.hdrift"),
        nodes=(HandlerNode(name="asm_hdrift_mod.f"),),
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name="acme.hdrift")
    with pytest.raises(
        ValueError, match="handler 'asm_hdrift_mod.f' is not in the registry"
    ):
        assemble(graph, DeclarationRegistry())  # a registry that lost the handler


def test_defining_module_missing_from_sys_modules_is_engine_internal_misuse(module_dir):
    _write_module(
        module_dir, "asm_ghost_mod",
        """
        def f(*, text):
            return {"out": text}

        f.__module__ = "asm_ghost_never_imported"
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "asm_ghost_mod.f", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/f.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.ghost"),
        nodes=(HandlerNode(name="asm_ghost_mod.f"),),
        inputs=(_fd("text"),),
    )
    graph = _compile(pipeline, reg, name="acme.ghost")
    # The resolved function claims a defining module that was never imported — the
    # vector-3 snapshot layer has no namespace to guard; fail loud.
    with pytest.raises(ValueError, match="is not in sys.modules after resolution"):
        assemble(graph, reg)


# ---------------------------------------------------------------------------
# The composition-join ValueError guards (bare member + trainable mirrors)
# ---------------------------------------------------------------------------

ASM_BACKEND_SRC = """
class CertifiedBackend:
    training_artifact_contract = "gguf"
    reserved_wire_keys = frozenset({"model", "prompt"})

    def __init__(self, model, *, output_schema, schema_source):
        self.model = model

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, **transport_extra):
        return {"resp": "ok:" + input_payload["prompt"]}
"""

ASM_PREP_COMPOSITION = """
[meta]
kind = "trainable"
name = "asm_prep_comp"
[inputs]
seed = { type = "str" }
[outputs]
resp = { type = "str" }
[[preprocessors]]
kind = "handler"
name = "asm_comp_mod.prep"
id   = "prep"
reads_map = { s = "seed" }
writes_map = { p = "prompt" }
[trainable]
[trainable.config]
[trainable.service_bindings]
llm = { type = "asm_backend_mod.CertifiedBackend" }
[trainable.reads]
prompt = { type = "str" }
[trainable.output_schema]
resp = { type = "str" }
[service_bindings.llm]
type = "asm_backend_mod.CertifiedBackend"
model = "m"
"""

ASM_PREP_PIPELINE = """
[meta]
name = "acme.prepcomp"
[[nodes]]
kind = "composition"
name = "compositions/asm_prep.toml"
[inputs]
seed = { type = "str" }
[outputs]
resp = { type = "str" }
"""

ASM_SOLO_COMPOSITION = """
[meta]
kind = "trainable"
name = "asm_solo"
[inputs]
prompt = { type = "str" }
[outputs]
resp = { type = "str" }
[trainable]
[trainable.config]
[trainable.service_bindings]
llm = { type = "asm_backend_mod.CertifiedBackend" }
[trainable.reads]
prompt = { type = "str" }
[trainable.output_schema]
resp = { type = "str" }
[service_bindings.llm]
type = "asm_backend_mod.CertifiedBackend"
model = "m"
"""

ASM_SOLO_PIPELINE = """
[meta]
name = "acme.solocomp"
[[nodes]]
kind = "composition"
name = "compositions/asm_solo.toml"
[inputs]
prompt = { type = "str" }
[outputs]
resp = { type = "str" }
"""


def _prep_composition_fixture(module_dir):
    _write_module(module_dir, "asm_backend_mod", ASM_BACKEND_SRC)
    _write_module(
        module_dir, "asm_comp_mod",
        """
        def prep(*, s):
            return {"p": s}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name="asm_backend_mod.CertifiedBackend",
            identity_schema=(_fd("model"),),
            transport_schema=(),
        ),
        toml_path="backend.toml",
    )
    reg.add_handler(
        "asm_comp_mod.prep",
        loads('[transform]\n[reads]\ns={type="str"}\n[output_schema]\np={type="str"}\n',
              "handler", file_path="prep.toml"),
        toml_path="handlers/prep.toml",
    )
    comp = loads(
        ASM_PREP_COMPOSITION, "composition", file_path="compositions/asm_prep.toml"
    )
    reg.add_composition("compositions/asm_prep.toml", comp)
    pipeline = loads(ASM_PREP_PIPELINE, "pipeline", file_path="p.toml")
    graph = _compile(pipeline, reg, name="acme.prepcomp")
    return graph, reg, comp


def _solo_composition_fixture(module_dir):
    _write_module(module_dir, "asm_backend_mod", ASM_BACKEND_SRC)
    reg = DeclarationRegistry()
    reg.add_service_type(
        ServiceTypeDeclaration(
            name="asm_backend_mod.CertifiedBackend",
            identity_schema=(_fd("model"),),
            transport_schema=(),
        ),
        toml_path="backend.toml",
    )
    comp = loads(
        ASM_SOLO_COMPOSITION, "composition", file_path="compositions/asm_solo.toml"
    )
    reg.add_composition("compositions/asm_solo.toml", comp)
    pipeline = loads(ASM_SOLO_PIPELINE, "pipeline", file_path="p.toml")
    graph = _compile(pipeline, reg, name="acme.solocomp")
    return graph, reg, comp


def test_missing_composition_for_a_bare_member_fails_loud(module_dir):
    graph, _reg, _comp = _prep_composition_fixture(module_dir)
    # A registry that lost the composition: the flattened PREPROCESSOR member is
    # the first join to notice.
    with pytest.raises(
        ValueError,
        match="composition 'compositions/asm_prep.toml' is not in the registry",
    ):
        assemble(graph, DeclarationRegistry())


def test_missing_preprocessor_entry_after_drift_fails_loud(module_dir):
    graph, reg, comp = _prep_composition_fixture(module_dir)
    # Drift: the composition re-registers with its preprocessors gone.
    reg.add_composition(
        "compositions/asm_prep.toml", comp.model_copy(update={"preprocessors": ()})
    )
    with pytest.raises(
        ValueError, match=r"has no \[\[preprocessors\]\] entry named 'prep'"
    ):
        assemble(graph, reg)


def test_missing_composition_for_the_trainable_terminal_fails_loud(module_dir):
    graph, _reg, _comp = _solo_composition_fixture(module_dir)
    # No preprocessors: the TRAINABLE join is the first to notice the lost
    # composition (the _assemble_trainable mirror of the bare-member guard).
    with pytest.raises(
        ValueError,
        match="composition 'compositions/asm_solo.toml' is not in the registry",
    ):
        assemble(graph, DeclarationRegistry())


def test_missing_trainable_supply_after_drift_fails_loud(module_dir):
    graph, reg, comp = _solo_composition_fixture(module_dir)
    drifted_trainable = comp.trainable.model_copy(
        update={
            "service_bindings": (
                ServiceBindingDecl(
                    name="llm2", type="asm_backend_mod.CertifiedBackend"
                ),
            )
        }
    )
    reg.add_composition(
        "compositions/asm_solo.toml",
        comp.model_copy(update={"trainable": drifted_trainable}),
    )
    with pytest.raises(
        ValueError, match=r"no \[service_bindings\.llm2\] supply in composition"
    ):
        assemble(graph, reg)


def test_unregistered_trainable_service_type_after_drift_fails_loud(module_dir):
    graph, reg, comp = _solo_composition_fixture(module_dir)
    drifted_trainable = comp.trainable.model_copy(
        update={
            "service_bindings": (
                ServiceBindingDecl(name="llm", type="ghost.Backend"),
            )
        }
    )
    reg.add_composition(
        "compositions/asm_solo.toml",
        comp.model_copy(update={"trainable": drifted_trainable}),
    )
    with pytest.raises(
        ValueError, match="service-type 'ghost.Backend' is not in the registry"
    ):
        assemble(graph, reg)


def test_missing_trainable_service_type_path_fails_loud(module_dir):
    _write_module(module_dir, "asm_backend_mod", ASM_BACKEND_SRC)
    reg = DeclarationRegistry()
    reg.add_service_type(  # registered WITHOUT toml_path
        ServiceTypeDeclaration(
            name="asm_backend_mod.CertifiedBackend",
            identity_schema=(_fd("model"),),
            transport_schema=(),
        ),
    )
    comp = loads(
        ASM_SOLO_COMPOSITION, "composition", file_path="compositions/asm_solo.toml"
    )
    reg.add_composition("compositions/asm_solo.toml", comp)
    pipeline = loads(ASM_SOLO_PIPELINE, "pipeline", file_path="p.toml")
    graph = _compile(pipeline, reg, name="acme.solocomp2")
    with pytest.raises(
        ValueError, match="no declaration path registered for service-type"
    ):
        assemble(graph, reg)


# ---------------------------------------------------------------------------
# Audit-stamp enforcement flows deployment → assemble → resolution
# (self-conformance-kit 3-code: the audit_enforcement opt-in threaded through stage 4)
# ---------------------------------------------------------------------------


def _one_transform_graph_and_reg(module_dir):
    _write_module(
        module_dir, "asm_stamp_mod",
        """
        def only(*, text):
            return {"out": text.upper()}
        """,
    )
    reg = DeclarationRegistry()
    reg.add_handler(
        "asm_stamp_mod.only", _transform((_fd("text"),), (_fd("out"),)),
        toml_path="handlers/only.toml",
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="acme.stamp"),
        nodes=(HandlerNode(name="asm_stamp_mod.only"),),
        inputs=(_fd("text"),),
        outputs=(_fd("out"),),
    )
    graph = _compile(pipeline, reg, name="acme.stamp")
    return graph, reg


def test_assemble_refuses_unstamped_handler_under_audit_enforcement(module_dir):
    """The deployment opt-in flows all the way through stage-4 assembly: with
    ``audit_enforcement = true`` and no sibling stamp beside the handler module, assemble
    refuses. RED if assemble stops threading the opt-in into resolve_handler."""
    graph, reg = _one_transform_graph_and_reg(module_dir)
    deployment = DeploymentDeclaration(
        training_contract=TrainingContract(
            integrity_enforcement=False, audit_enforcement=True
        )
    )
    with pytest.raises(ContractViolation) as exc:
        assemble(graph, reg, deployment)
    assert exc.value.check is Check.AUDIT_STAMP_NOT_FRESH


def test_assemble_admits_unstamped_handler_without_audit_enforcement(module_dir):
    """The off-by-default path (criterion 4): no opt-in ⇒ no stamp read ⇒ an unstamped
    handler composes clean. RED if the stamp check were wrongly always-on in assembly."""
    graph, reg = _one_transform_graph_and_reg(module_dir)
    deployment = DeploymentDeclaration(
        training_contract=TrainingContract(
            integrity_enforcement=False, audit_enforcement=False
        )
    )
    runnable = assemble(graph, reg, deployment)
    assert [n.qualified_name for n in runnable.nodes] == ["asm_stamp_mod.only"]


def test_hand_built_graph_without_source_declaration_refuses_before_hashing(module_dir):
    """RED-on-removal for the source_declaration guard (IR-5): a graph stripped of its
    compile-stamped declaration refuses assembly with the NAMED fail-loud ValueError —
    never a degraded AttributeError inside the hasher (the hash↔graph correspondence is
    structural because only compile_pipeline stamps the source)."""
    _write_module(module_dir, "asm_ir5_mod", "def f(*, x):\n    return {'y': x}\n")
    reg = DeclarationRegistry()
    reg.add_handler(
        "asm_ir5_mod.f", _transform([_fd("x")], [_fd("y")]), toml_path="f.toml"
    )
    pipeline = PipelineDeclaration(
        meta=PipelineMeta(name="asm.ir5"),
        nodes=(HandlerNode(name="asm_ir5_mod.f"),),
        inputs=(_fd("x"),),
    )
    graph = _compile(pipeline, reg, name="asm.ir5")
    stripped = graph.model_copy(update={"source_declaration": None})
    with pytest.raises(ValueError, match="source_declaration"):
        assemble(stripped, reg)
