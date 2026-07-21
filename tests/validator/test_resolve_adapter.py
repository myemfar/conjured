"""Adapter resolution — the sibling mechanism (``validator.resolve_adapter``): the
vector-7 adapter-scope AST audit in place of the function-shape check, the
R-service-type-002/003 ``invoke()`` signature contract from the real ``__code__``, and
the B2 identity-only construction. Real ``tmp_path`` modules throughout."""

from __future__ import annotations

import sys
import textwrap
import tomllib
import uuid
from pathlib import Path

import pytest

import conjured.lib
from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.service_type import ServiceTypeDeclaration
from conjured.validator.parse import parse_service_type
from conjured.validator.resolve_adapter import construct_adapter, resolve_adapter

TOML = "service-types/llm.toml"


def _native_toml(qualified_name: str) -> Path:
    """The engine-shipped sibling TOML for a native qualified name (its same-named module
    sibling under ``conjured/lib``)."""
    submodule = qualified_name[len("conjured.lib."):]
    return Path(conjured.lib.__file__).parent / f"{submodule}.toml"


def _shipped_native_service_type(qualified_name: str) -> ServiceTypeDeclaration:
    """Parse a native's engine-shipped service-type declaration from its sibling TOML — the
    same binary ``tomllib.load`` path a consumer hand-loads it through (TOML is UTF-8)."""
    with open(_native_toml(qualified_name), "rb") as fh:
        data = tomllib.load(fh)
    return parse_service_type(data, file_path=str(_native_toml(qualified_name)))

SERVICE_TYPE = ServiceTypeDeclaration(
    name="conjured_llm.structured_output",
    identity_schema=(FieldDecl(name="model", type=primitive("str")),),
    transport_schema=(FieldDecl(name="endpoint", type=primitive("str")),),
    config_schema=(FieldDecl(name="temperature", type=primitive("float")),),
)

GOOD_ADAPTER = """
class GoodAdapter:
    def __init__(self, *, model):
        self.model = model           # compose-fixed identity only
        self._client = None          # lazy: built on first invoke from transport

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, temperature, **transport_extra):
        if self._client is None:
            self._client = {"endpoint": transport_extra.get("endpoint")}
        return {"echo": input_payload, "temperature": temperature}
"""


@pytest.fixture()
def module_dir(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def _write_module(module_dir, source: str) -> str:
    name = f"admod_{uuid.uuid4().hex[:10]}"
    (module_dir / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    import importlib

    importlib.invalidate_caches()
    return name


def test_adapter_resolution_and_b2_construction(module_dir):
    mod = _write_module(module_dir, GOOD_ADAPTER)
    cls = resolve_adapter(f"{mod}.GoodAdapter", SERVICE_TYPE, toml_path=TOML)
    adapter = construct_adapter(
        cls, {"model": "qwen3.5-4b"},
        qualified_name=f"{mod}.GoodAdapter", toml_path=TOML,
    )
    assert adapter.model == "qwen3.5-4b"
    assert adapter._client is None  # no client at compose — first-invoke lazy (B2)
    result = adapter.invoke(
        input_payload={"q": "hi"},
        service_name="llm",
        caller_qualified_name="acme.respond",
        caller_position=0,
        temperature=0.7,
        endpoint="https://llm.test/v1",
    )
    assert adapter._client == {"endpoint": "https://llm.test/v1"}  # built + memoized
    assert result["echo"] == {"q": "hi"}


def test_entry_point_resolution(module_dir):
    mod = _write_module(module_dir, GOOD_ADAPTER)
    short = f"impl_{uuid.uuid4().hex[:8]}"
    info = module_dir / f"adist-0.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: adist\nVersion: 0.1\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        f"[conjured.service_implementations]\n{short} = {mod}:GoodAdapter\n",
        encoding="utf-8",
    )
    import importlib

    importlib.invalidate_caches()
    cls = resolve_adapter(short, SERVICE_TYPE, toml_path=TOML)
    assert cls.__name__ == "GoodAdapter"


def test_dotted_qualified_name_resolves_through_the_entry_points_group(module_dir):
    """The adapter's OWN selector (handler-resolution.md § Adapters): the entry-points
    group is consulted FIRST, keyed by the FULL service-type qualified name — dots and
    all (a qualified name is a type identity, never a module path; the handler
    dot-presence selector could never reach the group for a dotted name)."""
    mod = _write_module(module_dir, GOOD_ADAPTER)
    dotted = f"acme_llm.structured_output_{uuid.uuid4().hex[:8]}"
    assert "." in dotted  # the point: a DOTTED entry-point name
    info = module_dir / "adotted-0.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: adotted\nVersion: 0.1\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        f"[conjured.service_implementations]\n{dotted} = {mod}:GoodAdapter\n",
        encoding="utf-8",
    )
    import importlib

    importlib.invalidate_caches()
    # There is no module 'acme_llm' on the path — only the EP carries the name.
    cls = resolve_adapter(dotted, SERVICE_TYPE, toml_path=TOML)
    assert cls.__name__ == "GoodAdapter"


def test_entry_point_beats_module_path_for_a_dotted_name(module_dir):
    """EP-first priority: a dotted qualified name registered in the group resolves
    through the GROUP even when the same dotted path would also resolve as a module
    attribute (the fallback runs only when no entry point carries the name)."""
    ep_mod = _write_module(module_dir, GOOD_ADAPTER)
    # A real module whose attribute the dotted name would reach via the fallback.
    shadow_mod = _write_module(
        module_dir,
        GOOD_ADAPTER.replace("GoodAdapter", "ShadowAdapter"),
    )
    dotted = f"{shadow_mod}.ShadowAdapter"  # ALSO a valid module path
    info = module_dir / "aprio-0.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: aprio\nVersion: 0.1\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        f"[conjured.service_implementations]\n{dotted} = {ep_mod}:GoodAdapter\n",
        encoding="utf-8",
    )
    import importlib

    importlib.invalidate_caches()
    cls = resolve_adapter(dotted, SERVICE_TYPE, toml_path=TOML)
    assert cls.__name__ == "GoodAdapter"  # the EP registration, not the module path


def test_unregistered_dotted_name_falls_back_to_module_path(module_dir):
    """The dotted-path fallback: no entry point carries the qualified name → module
    resolution runs (the existing mechanics unchanged)."""
    mod = _write_module(module_dir, GOOD_ADAPTER)
    cls = resolve_adapter(f"{mod}.GoodAdapter", SERVICE_TYPE, toml_path=TOML)
    assert cls.__name__ == "GoodAdapter"


def test_unsatisfiable_binding(module_dir):
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter("no_such_impl", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.SERVICE_TYPE_RESOLUTION


def test_missing_adapter_module_cites_service_type_rule_and_adapter_noun(module_dir):
    """The locate_spec rule_id/noun overrides (mechanical set): a missing adapter MODULE
    (dotted name, no EP, no module to fall back to) cites R-service-type-003 and the
    'adapter' noun — not the default R-pipeline-001 / 'handler'."""
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter("zzz_no_such_adapter_module.Adapter", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.rule_id == "R-service-type-003"
    blob = " ".join(
        str(x) for x in (exc.value.expected, exc.value.actual, exc.value.remediation_hint)
    ).lower()
    assert "adapter" in blob


def test_undecodable_adapter_module_cites_R_service_type_003(module_dir):
    """Fix 5 (`adapter-undecodable-ruleid`): the decode-failure branch through the ADAPTER
    path cites R-service-type-003 (the adapter sibling-resolution rule — service-type/reference.md
    § Service-impl dispatch contract: "Resolution and signature failures are compose-time
    ContractViolation"), NOT the `decode_module_source` default R-pipeline-001 (a pipeline
    rule). The adapter analogue of test_undecodable_validator_module_cites_R_handler_012; it
    matches the R-service-type-003 the SyntaxError branch one line below already cites. RED
    before Fix 5 threaded rule_id into the adapter's `decode_module_source` call."""
    name = f"admod_{uuid.uuid4().hex[:10]}"
    # Invalid UTF-8 with no PEP-263 coding declaration — decode_source raises before the AST audit.
    (module_dir / f"{name}.py").write_bytes(
        b"class Adapter:\n    pass  # caf\xe9\n"
    )
    import importlib

    importlib.invalidate_caches()
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(f"{name}.Adapter", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.HANDLER_MODULE_IMPORT
    assert exc.value.rule_id == "R-service-type-003"


def test_namespace_package_adapter_rejected_with_service_type_rule(module_dir):
    """F1 regression: a service-type ADAPTER whose module is a PEP-420 namespace package
    (a directory with no __init__.py) is rejected as a compose-time ContractViolation
    citing R-service-type-003 — it must NOT escape as a raw ValueError from the
    constructor's registration seal (a fourth class out of the closed error channel,
    R-error-channel-001). The namespace-package case was tested for handlers/validators
    (whose rule_ids were registered) but never adapters, so the adapter path — the only
    one passing R-service-type-003 to HANDLER_NAMESPACE_PACKAGE — went through an
    unregistered pair. RED before the CHECK_REGISTRY pairing was added."""
    ns = f"nspkg_adapter_{uuid.uuid4().hex[:8]}"
    (module_dir / ns).mkdir()  # a directory with no __init__.py — PEP 420
    import importlib

    importlib.invalidate_caches()
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(f"{ns}.GoodAdapter", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.HANDLER_NAMESPACE_PACKAGE
    assert exc.value.rule_id == "R-service-type-003"  # the registry pair F1 added
    hint = exc.value.remediation_hint or ""
    assert "__init__.py" in hint
    assert ns in hint  # the hint names the namespace-package directory needing __init__.py


def test_non_class_rejected(module_dir):
    mod = _write_module(module_dir, "def adapter(*, input_payload):\n    return {}\n")
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(f"{mod}.adapter", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.ADAPTER_PURE_MODULE
    assert "class" in exc.value.expected


# verifies: resolve-non-file-origin-fails-loud
def test_non_file_origin_adapter_fails_loud():
    """The adapter sibling of the handler non-file-origin seal (trust-model.md Vector 5
    extends to adapter modules; handler-resolution.md § Resolution mechanism — Adapters,
    step 3): a sourceless module — a builtin / extension with no readable source — MUST
    fail loud rather than silently skip the vector-7 source-AST audit.

    The adversary is a built-in module via the dotted-path fallback: no entry point carries
    ``sys.exit``, so resolution falls back to dotted-path module resolution;
    ``find_spec('sys').origin == 'built-in'`` and ``os.path.isfile('built-in')`` is False,
    so ``_read_and_audit_adapter_source`` reaches its ``not os.path.isfile(origin)`` raise.
    The namespace-package adapter test stops at step 2 (``origin is None``); no test
    exercises this step-3 branch. RED if it is deleted: the function would fall through to
    ``open('built-in', 'rb')`` and escape as a raw ``OSError`` — a fourth class out of the
    closed compose-time channel (R-error-channel-001)."""
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter("sys.exit", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.ADAPTER_PURE_MODULE
    assert exc.value.rule_id == "R-handler-pure-module"
    assert "built-in" in exc.value.actual  # the origin-based rejection, not a syntax/decode one


# --- the vector-7 audit (above-instance-scope mutable state; pre-import) ---------------


def test_class_level_mutable_state_rejected(module_dir):
    mod = _write_module(
        module_dir,
        """
        class BadAdapter:
            cache = {}    # class-level mutable state escapes the composition lifetime

            def __init__(self, *, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, **transport_extra):
                return {}
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(f"{mod}.BadAdapter", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.ADAPTER_PURE_MODULE
    assert mod not in sys.modules  # audited on source, before import


def test_cache_decorator_on_method_rejected(module_dir):
    mod = _write_module(
        module_dir,
        """
        import functools

        class BadAdapter:
            def __init__(self, *, model):
                self.model = model

            @functools.lru_cache
            def lookup(self, key):
                return key

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, **transport_extra):
                return {}
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(f"{mod}.BadAdapter", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.ADAPTER_PURE_MODULE


def test_module_level_io_rejected_in_adapter_module(module_dir):
    mod = _write_module(
        module_dir,
        """
        CLIENT = open('creds.txt')

        class BadAdapter:
            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, **transport_extra):
                return {}
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(f"{mod}.BadAdapter", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.ADAPTER_PURE_MODULE
    assert mod not in sys.modules


def test_nested_class_state_rejected(module_dir):
    # The vector-7 audit recurses into nested classes — class-level mutable state
    # cannot hide one nesting level down (a nested class body executes at import).
    mod = _write_module(
        module_dir,
        """
        class Adapter:
            class _Cache:
                store = {}

            def __init__(self, *, model):
                self.model = model

            def invoke(self, *, input_payload, service_name, caller_qualified_name,
                       caller_position, temperature, **transport_extra):
                return {}
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(f"{mod}.Adapter", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.ADAPTER_PURE_MODULE
    assert mod not in sys.modules


def test_unsatisfiable_and_collision_cite_rule_004(module_dir):
    # R-service-type-004 owns the one-implementation-per-qualified-name dispositions.
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter("no_such_impl_004", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.rule_id == "R-service-type-004"


def test_entry_point_collision_fails_loud(module_dir):
    """Collision semantics preserved under the EP-first selector: two distributions
    registering one qualified name fail loud — no winner, no install-order tiebreak."""
    mod = _write_module(module_dir, GOOD_ADAPTER)
    name = f"acme.collide_{uuid.uuid4().hex[:8]}"
    for dist in ("colla", "collb"):
        info = module_dir / f"{dist}-0.1.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {dist}\nVersion: 0.1\n", encoding="utf-8"
        )
        (info / "entry_points.txt").write_text(
            f"[conjured.service_implementations]\n{name} = {mod}:GoodAdapter\n",
            encoding="utf-8",
        )
    import importlib

    importlib.invalidate_caches()
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(name, SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check is Check.ENTRY_POINT_COLLISION
    assert exc.value.rule_id == "R-service-type-004"


def test_instance_state_is_admissible(module_dir):
    # The vector-2 / vector-7 distinction is exact: instance state in __init__ /
    # on self is engine-managed compose-time state, bounded by composition lifetime.
    mod = _write_module(module_dir, GOOD_ADAPTER)
    assert resolve_adapter(f"{mod}.GoodAdapter", SERVICE_TYPE, toml_path=TOML)


# --- the R-service-type-002/003 signature contract --------------------------------------


@pytest.mark.parametrize(
    ("invoke_sig", "why"),
    [
        # missing a closed dispatch-kwarg (caller_position)
        ("self, *, input_payload, service_name, caller_qualified_name, temperature, **t", "closed"),
        # config kwarg with no [config_schema] field
        ("self, *, input_payload, service_name, caller_qualified_name, caller_position, temperature, top_p, **t", "undeclared"),
        # missing the declared config kwarg
        ("self, *, input_payload, service_name, caller_qualified_name, caller_position, **t", "config"),
        # no **transport_extra collector
        ("self, *, input_payload, service_name, caller_qualified_name, caller_position, temperature", "collector"),
        # positional parameters beyond self
        ("self, input_payload, *, service_name, caller_qualified_name, caller_position, temperature, **t", "positional"),
    ],
)
def test_invoke_signature_mismatches_rejected(module_dir, invoke_sig, why):
    mod = _write_module(
        module_dir,
        f"""
        class BadAdapter:
            def invoke({invoke_sig}):
                return {{}}
        """,
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(f"{mod}.BadAdapter", SERVICE_TYPE, toml_path=TOML)
    assert exc.value.check in (Check.ADAPTER_SIGNATURE,)


# --- the trainable-backend compose-time gate (R-handler-008 expansion) ------------------


class _StampedConsumerAdapter:
    """A consumer-tail adapter certified **structurally** (the UNIFY ruling): NO
    ``trainable_backend_certification`` class attribute — certification is native-by-table
    or a fresh audit stamp under ``audit_enforcement``, never a self-declared marker. The
    gate here verifies only the property contract (a non-empty ``training_artifact_contract``
    + a ``reserved_wire_keys`` frozenset)."""

    training_artifact_contract = "gguf"
    reserved_wire_keys = frozenset({"model", "prompt", "temperature", "n_predict", "grammar"})

    def __init__(self, *, model, output_schema, schema_source):
        self.model = model

    def invoke(self, *, input_payload, service_name, caller_qualified_name,
               caller_position, temperature, **transport_extra):
        return {}


def test_trainable_gate_admits_a_property_complete_adapter_without_a_cert_attribute():
    """The gate rewrite (UNIFY / criterion 5): a consumer adapter with NO
    ``trainable_backend_certification`` attribute but the property contract intact is
    admitted — the gate no longer reads the retired marker. RED if a leftover
    class-attribute dependency were reintroduced (this adapter carries none)."""
    from conjured.validator.resolve_adapter import check_trainable_backend

    assert not hasattr(_StampedConsumerAdapter, "trainable_backend_certification")
    check_trainable_backend(
        _StampedConsumerAdapter, qualified_name="acme_mlx.trainable", toml_path=TOML
    )  # no raise


def test_trainable_gate_ignores_a_stray_certification_marker():
    """The gate rewrite (UNIFY / criterion 5): the retired ``trainable_backend_certification``
    attribute is no longer READ — a property-complete adapter carrying a stray, bogus marker
    is admitted on the strength of its property contract alone (the marker is inert). RED if
    the gate reintroduced a dependency on the marker's value."""
    from conjured.validator.resolve_adapter import check_trainable_backend

    class StrayMarker:
        trainable_backend_certification = "trust-me"  # bogus; must be ignored, not read
        training_artifact_contract = "gguf"
        reserved_wire_keys = frozenset({"model"})

    check_trainable_backend(
        StrayMarker, qualified_name="acme.stray", toml_path=TOML
    )  # no raise — the marker is inert; the property contract admits it


def test_trainable_gate_rejects_a_missing_artifact_contract():
    from conjured.validator.resolve_adapter import check_trainable_backend

    class NoContract:
        reserved_wire_keys = frozenset({"model"})  # property present, but no artifact contract

    with pytest.raises(ContractViolation) as exc:
        check_trainable_backend(
            NoContract, qualified_name="acme.nocontract", toml_path=TOML
        )
    assert exc.value.check is Check.TRAINABLE_BACKEND_CERTIFICATION
    assert exc.value.rule_id == "R-handler-008"
    assert "no training_artifact_contract attribute" in exc.value.actual


def test_trainable_gate_admits_a_nonstandard_provenance_contract():
    # The roster is OPEN: training_artifact_contract is a provenance label the engine records
    # but never interprets (it reads the trained artifact by path), so a non-standard but
    # non-empty string resolves cleanly — this case previously RAISED on closed-set membership.
    # RED if someone re-closes the set: the membership check would reject "my-runtime-checkpoint".
    from conjured.validator.resolve_adapter import check_trainable_backend

    class BespokeArtifact:
        training_artifact_contract = "my-runtime-checkpoint"
        reserved_wire_keys = frozenset({"model", "prompt"})

    check_trainable_backend(
        BespokeArtifact, qualified_name="acme.bespoke", toml_path=TOML
    )  # no raise — any non-empty provenance string is admitted


def test_trainable_gate_rejects_an_empty_artifact_contract():
    # Fail-loud preserved (the surviving seal): an EMPTY provenance string names no artifact
    # family — a real declaration defect — so the gate still raises. Opening the set relaxed
    # membership, NEVER the present-and-non-empty requirement. RED if that guard is removed.
    from conjured.validator.resolve_adapter import check_trainable_backend

    class EmptyContract:
        training_artifact_contract = ""
        reserved_wire_keys = frozenset({"model"})

    with pytest.raises(ContractViolation) as exc:
        check_trainable_backend(
            EmptyContract, qualified_name="acme.empty", toml_path=TOML
        )
    assert exc.value.check is Check.TRAINABLE_BACKEND_CERTIFICATION
    assert exc.value.rule_id == "R-handler-008"
    assert "empty string" in exc.value.actual


def test_trainable_gate_rejects_a_nonstring_artifact_contract():
    # Fail-loud preserved (the surviving seal): a NON-STRING training_artifact_contract is
    # malformed — a provenance label is a string. RED if the string requirement is removed.
    from conjured.validator.resolve_adapter import check_trainable_backend

    class NonStringContract:
        training_artifact_contract = 123
        reserved_wire_keys = frozenset({"model"})

    with pytest.raises(ContractViolation) as exc:
        check_trainable_backend(
            NonStringContract, qualified_name="acme.nonstring", toml_path=TOML
        )
    assert exc.value.check is Check.TRAINABLE_BACKEND_CERTIFICATION
    assert "not a string" in exc.value.actual


def test_trainable_gate_rejects_a_missing_reserved_wire_keys(tmp_path):
    # D3: a certified trainable backend MUST declare reserved_wire_keys (the extras
    # rider's disjointness source); a backend with valid certification + artifact but no
    # reserved_wire_keys fails the gate's third arm.
    from conjured.validator.resolve_adapter import check_trainable_backend

    class NoReservedKeys:
        training_artifact_contract = "gguf"

    with pytest.raises(ContractViolation) as exc:
        check_trainable_backend(
            NoReservedKeys, qualified_name="acme.noreserved", toml_path=TOML
        )
    assert exc.value.check is Check.TRAINABLE_BACKEND_CERTIFICATION
    assert exc.value.rule_id == "R-handler-008"
    assert "no reserved_wire_keys attribute" in exc.value.actual


def test_trainable_gate_rejects_a_malformed_reserved_wire_keys():
    # Shape check: reserved_wire_keys must be a frozenset[str] — a plain set (mutable) or
    # a non-string member is malformed (the immutable-certification posture).
    from conjured.validator.resolve_adapter import check_trainable_backend

    class MutableReserved:
        training_artifact_contract = "gguf"
        reserved_wire_keys = {"model"}  # a mutable set, not a frozenset

    with pytest.raises(ContractViolation) as exc:
        check_trainable_backend(
            MutableReserved, qualified_name="acme.mutablereserved", toml_path=TOML
        )
    assert exc.value.check is Check.TRAINABLE_BACKEND_CERTIFICATION
    assert "not a frozenset" in exc.value.actual


# --- the extras-disjointness check (D3 extras rider) -----------------------------------


class _WireAdapter:
    reserved_wire_keys = frozenset(
        {"model", "messages", "temperature", "max_tokens", "response_format"}
    )


def test_extras_disjoint_passes_when_extras_holds_only_sampling_tail():
    from conjured.validator.resolve_adapter import check_extras_disjoint

    check_extras_disjoint(
        _WireAdapter, {"temperature": 0.7, "extras": {"top_p": 0.9, "top_k": 40}},
        qualified_name="acme.wire", toml_path=TOML,
    )  # no raise — the sampling tail is disjoint from the reserved wire keys


def test_extras_naming_a_reserved_key_rejects_with_the_home_message():
    from conjured.validator.resolve_adapter import check_extras_disjoint

    with pytest.raises(ContractViolation) as exc:
        check_extras_disjoint(
            _WireAdapter, {"extras": {"temperature": 0.9}},  # temperature is a dial, not extras
            qualified_name="acme.wire", toml_path=TOML,
        )
    cv = exc.value
    assert cv.check is Check.CONFIG_SCHEMA_SUPPLY
    assert cv.rule_id == "R-service-type-002"
    assert "temperature" in cv.actual
    assert "[config_schema]" in cv.remediation_hint  # names the dial's real home


def test_extras_naming_a_structural_wire_key_rejects():
    from conjured.validator.resolve_adapter import check_extras_disjoint

    for key in ("model", "messages", "response_format"):
        with pytest.raises(ContractViolation) as exc:
            check_extras_disjoint(
                _WireAdapter, {"extras": {key: "x"}},
                qualified_name="acme.wire", toml_path=TOML,
            )
        assert key in exc.value.actual


def test_extras_disjoint_skips_an_adapter_without_reserved_wire_keys():
    from conjured.validator.resolve_adapter import check_extras_disjoint

    class GenericService:  # a non-trainable service adapter — no reserved_wire_keys
        pass

    # An extras key that would collide IF reserved_wire_keys existed — but a generic
    # service adapter has no wire keys to collide with, so the check is a no-op.
    check_extras_disjoint(
        GenericService, {"extras": {"model": "x"}},
        qualified_name="acme.generic", toml_path=TOML,
    )


def test_extras_disjoint_is_a_no_op_when_no_extras_in_config():
    from conjured.validator.resolve_adapter import check_extras_disjoint

    check_extras_disjoint(
        _WireAdapter, {"temperature": 0.7, "max_tokens": 64},
        qualified_name="acme.wire", toml_path=TOML,
    )  # no extras table → nothing to check


def test_extras_disjoint_rejects_a_present_but_malformed_reserved_wire_keys():
    from conjured.validator.resolve_adapter import check_extras_disjoint

    # Present-but-malformed reserved_wire_keys is NOT absence: a mutable set (wrong type) would
    # silently equal "no reserved keys" and let a colliding extras key through unchecked (the
    # silent-degrade class). It fails loud, mirroring the trainable gate's frozenset[str] validation.
    # `top_p` does NOT name a reserved key, so on REVERT (no guard) the disjointness check finds no
    # overlap and returns clean — RED-on-removal: the raise disappears and nothing is raised.
    class _MutableReserved:
        reserved_wire_keys = {"model"}  # a mutable set, not a frozenset — malformed

    with pytest.raises(ContractViolation) as exc:
        check_extras_disjoint(
            _MutableReserved, {"extras": {"top_p": 0.9}},
            qualified_name="acme.malformed", toml_path=TOML,
        )
    assert exc.value.check is Check.CONFIG_SCHEMA_SUPPLY
    assert exc.value.rule_id == "R-service-type-002"
    assert "not a frozenset of strings" in exc.value.actual
    assert exc.value.section_path == "config.extras"


def test_extras_disjoint_rejects_reserved_wire_keys_with_a_non_string_member():
    from conjured.validator.resolve_adapter import check_extras_disjoint

    # A frozenset carrying a non-string member is malformed the same way. `123` cannot collide with
    # a string extras key, so REVERT (no guard) finds no overlap and returns clean — RED-on-removal.
    class _NonStringMember:
        reserved_wire_keys = frozenset({123})

    with pytest.raises(ContractViolation) as exc:
        check_extras_disjoint(
            _NonStringMember, {"extras": {"top_p": 0.9}},
            qualified_name="acme.malformed", toml_path=TOML,
        )
    assert exc.value.check is Check.CONFIG_SCHEMA_SUPPLY
    assert exc.value.rule_id == "R-service-type-002"
    assert "not a frozenset of strings" in exc.value.actual


# --- the native adapter table consult + the engine-owned-identity guards ---------------
# (native-table-resolver arc; handler-resolution.md § Native adapters; native-library/
# reference.md § the engine-owned-identity clause; R-service-type-004.)


def test_native_qualified_name_resolves_through_the_full_verification_surface():
    """Piece 1 — a native service-type qualified name resolves through the engine's native
    adapter table AHEAD of the entry-points leg, routed through the SAME audited dotted-path
    leg every adapter passes (source-AST audit + class shape + invoke-signature check). The
    reproduced baseline: before the consult, this name split as module `conjured.lib` + attr
    and raised HANDLER_MODULE_IMPORT; now it resolves the shipped implementation class.

    RED-on-removal: delete the native consult in resolve_adapter and this name falls through
    to the dotted-path fallback (`conjured.lib`.`gbnf_trainable` has no such attribute at
    import) — a ContractViolation, not the resolved class."""
    from conjured.lib import NATIVE_TRAINABLE_ADAPTERS
    from conjured.lib.gbnf_trainable import GBNFTrainable

    name = "conjured.lib.gbnf_trainable"
    st = _shipped_native_service_type(name)
    cls = resolve_adapter(name, st, toml_path=str(_native_toml(name)))
    assert cls is GBNFTrainable
    # discovery only — the mapped class path is what routed through the dotted leg.
    assert NATIVE_TRAINABLE_ADAPTERS[name].endswith(".GBNFTrainable")


def test_native_consult_precedes_and_survives_an_entry_point_under_the_native_name(module_dir):
    """Piece 1, the unshadowable property (handler-resolution.md § Native adapters): a
    third-party `conjured.service_implementations` entry point registered under a native
    qualified name does NOT shadow the engine's shipped implementation — the native consult
    runs first and the group is never reached for that name.

    RED-on-removal: if the native consult moved AFTER the entry-points leg, this would resolve
    the shadowing `ShadowAdapter`, not the engine's `GBNFTrainable`."""
    from conjured.lib.gbnf_trainable import GBNFTrainable

    name = "conjured.lib.gbnf_trainable"
    shadow = _write_module(module_dir, GOOD_ADAPTER.replace("GoodAdapter", "ShadowAdapter"))
    info = module_dir / "shadowdist-0.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: shadowdist\nVersion: 0.1\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        f"[conjured.service_implementations]\n{name} = {shadow}:ShadowAdapter\n",
        encoding="utf-8",
    )
    import importlib

    importlib.invalidate_caches()
    cls = resolve_adapter(
        name, _shipped_native_service_type(name), toml_path=str(_native_toml(name))
    )
    assert cls is GBNFTrainable  # the engine's shipped impl, not the shadowing entry point


def test_class_path_binding_to_a_native_is_rejected():
    """Piece 2 — a binding whose requested name is a native adapter CLASS PATH (a native
    table VALUE) is rejected loud: binding a native by its class path would fold a second,
    non-canonical hash identity for one backend (the dual-identity hazard). The remediation
    names the native qualified name.

    RED-on-removal: delete the `_NATIVE_ADAPTER_CLASS_PATHS` guard and the class path resolves
    cleanly to `GBNFTrainable` (via the dotted-path fallback) — the exact laundered identity
    the seal forbids."""
    from conjured.lib import NATIVE_TRAINABLE_ADAPTERS

    name = "conjured.lib.gbnf_trainable"
    class_path = NATIVE_TRAINABLE_ADAPTERS[name]
    with pytest.raises(ContractViolation) as exc:
        resolve_adapter(class_path, _shipped_native_service_type(name), toml_path=TOML)
    assert exc.value.check is Check.ENGINE_OWNED_IDENTITY
    assert exc.value.rule_id == "R-service-type-004"
    assert name in exc.value.remediation_hint  # steers to the native qualified name


def test_registry_rejects_redefining_an_engine_owned_native_identity():
    """Piece 3 — `DeclarationRegistry.add_service_type` rejects a `conjured.lib.*`
    registration that is not the engine-shipped declaration for that native qualified name
    (redefining an engine-owned identity), while hand-loading the genuine shipped declaration
    stays legal. Both faces of the engine-owned-identity guarantee (R-service-type-004).

    RED-on-removal: delete the guard and the tampered declaration overwrites the engine-owned
    identity silently (the dict-overwrite this replaces)."""
    from conjured.validator.registry import DeclarationRegistry

    name = "conjured.lib.gbnf_trainable"
    shipped = _shipped_native_service_type(name)
    toml = str(_native_toml(name))

    # Legal: the genuine shipped declaration, hand-loaded.
    reg = DeclarationRegistry()
    reg.add_service_type(shipped, toml_path=toml)
    assert reg.get_service_type(name) is shipped

    # Illegal: a modified declaration under the same native name.
    tampered = shipped.model_copy(update={"description": "a redefinition"})
    with pytest.raises(ContractViolation) as exc:
        DeclarationRegistry().add_service_type(tampered, toml_path=toml)
    assert exc.value.check is Check.ENGINE_OWNED_IDENTITY
    assert exc.value.rule_id == "R-service-type-004"

    # Illegal: a `conjured.lib.*` name the engine ships nothing under (namespace squatting).
    squat = ServiceTypeDeclaration(
        name="conjured.lib.not_a_native",
        identity_schema=(FieldDecl(name="model", type=primitive("str")),),
        transport_schema=(FieldDecl(name="endpoint", type=primitive("str")),),
    )
    with pytest.raises(ContractViolation) as exc2:
        DeclarationRegistry().add_service_type(squat, toml_path="x.toml")
    assert exc2.value.check is Check.ENGINE_OWNED_IDENTITY


def test_construct_trainable_adapter_supplies_the_compose_fixed_kwargs():
    from conjured.ir.channel_types import FieldDecl, primitive
    from conjured.validator.resolve_adapter import construct_trainable_adapter

    captured = {}

    class Capturing:
        def __init__(self, *, model, output_schema, schema_source):
            captured.update(
                model=model, output_schema=output_schema, schema_source=schema_source
            )

    fields = (FieldDecl(name="dialogue", type=primitive("str")),)
    construct_trainable_adapter(
        Capturing, {"model": "m"}, output_schema=fields,
        schema_source="compositions/x.toml",
        qualified_name="tests.Capturing", toml_path="service-types/x.toml",
    )
    assert captured == {
        "model": "m",
        "output_schema": fields,
        "schema_source": "compositions/x.toml",
    }


# ---------------------------------------------------------------------------
# The construction half of the closed channel (ADAPTER_CONSTRUCTION)
# ---------------------------------------------------------------------------


# verifies: adapter-construction-fails-structured
def test_identity_kwargs_mismatch_at_construction_is_structured(module_dir):
    """An adapter __init__ that rejects the compose-supplied identity kwargs (a
    TypeError from the call binding) surfaces as the compose-time ContractViolation the
    module seal promises — RED with the construction wrap removed (a raw TypeError
    would escape stage-4 assembly)."""
    mod = _write_module(module_dir, GOOD_ADAPTER)
    cls = resolve_adapter(f"{mod}.GoodAdapter", SERVICE_TYPE, toml_path=TOML)
    with pytest.raises(ContractViolation) as exc:
        construct_adapter(
            cls, {"model": "qwen3.5-4b", "unexpected_identity": "x"},
            qualified_name=f"{mod}.GoodAdapter", toml_path=TOML,
        )
    assert exc.value.check is Check.ADAPTER_CONSTRUCTION
    assert exc.value.rule_id == "R-service-type-003"


# verifies: adapter-construction-fails-structured
def test_constructor_body_raise_is_structured():
    """A consumer-adapter constructor that raises from its own body (any exception
    class) stays inside the closed compose-time channel."""

    class Exploding:
        def __init__(self, *, model):
            raise ValueError("no such checkpoint: " + model)

    with pytest.raises(ContractViolation) as exc:
        construct_adapter(
            Exploding, {"model": "missing"},
            qualified_name="tests.Exploding", toml_path=TOML,
        )
    assert exc.value.check is Check.ADAPTER_CONSTRUCTION
    assert "no such checkpoint" in str(exc.value)


def test_constructor_raised_contractviolation_passes_through_unwrapped():
    """A ContractViolation the constructor itself raises (the trainable
    constraint-derivation rejection is the shipped case) is ALREADY the closed channel
    — it must pass through unwrapped, keeping its own check discriminator."""
    from conjured.validator.resolve_adapter import construct_trainable_adapter

    original = ContractViolation(
        check=Check.TRAINABLE_CONSTRAINT_UNSUPPORTED, rule_id="R-handler-005",
        expected="a grammar-expressible schema", actual="a bytes channel",
        file_path="compositions/x.toml",
    )

    class Rejecting:
        def __init__(self, *, model, output_schema, schema_source):
            raise original

    with pytest.raises(ContractViolation) as exc:
        construct_trainable_adapter(
            Rejecting, {"model": "m"}, output_schema=(),
            schema_source="compositions/x.toml",
            qualified_name="tests.Rejecting", toml_path=TOML,
        )
    assert exc.value is original  # unwrapped — not re-labeled as ADAPTER_CONSTRUCTION
