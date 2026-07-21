"""get_handler_fn — the boundary-exercise predicate enforced structurally (R-testing-001/008).

Direct invocation is legal only for a bindings-free transform; the gate refuses everything else, so a
bare call can never train the wrong contract. Its refusals distinguish a genuinely unregistered name
from one registered as a COMPOSITION by path (add_composition, not add_handler): the latter has no
bare-call form and must be dispatched, so it gets its own steering message rather than the wrong
"register with add_handler" remedy. The refusal tests are RED-on-removal: drop the gate (or the
composition-by-path arm) and get_handler_fn hands back a callable (or the wrong steering) instead of
raising, so the `pytest.raises(BoundaryViolation)` assertions FAIL — surfacing the silently-violated
or mis-directed predicate rather than hiding it.
"""

from __future__ import annotations

import pytest

from conjured.ir.channel_types import FieldDecl, primitive
from conjured.ir.common import Binding, SchemaBinding, ServiceBindingDecl
from conjured.ir.handler import HookDeclaration, ServiceDeclaration, TransformDeclaration
from conjured.testing import BoundaryViolation, get_handler_fn


def _fd(name: str, token: str = "str") -> FieldDecl:
    return FieldDecl(name=name, type=primitive(token))


def test_returns_bare_transform_function(chain):
    fn = get_handler_fn(chain.registry, chain.first_qn)
    assert fn(text="hi") == {"mid": "HI"}  # the real resolved bare function, called directly


def test_refuses_service(conjured_registry, module_writer):
    module = module_writer("th_svc_mod", "def embed(*, q, services):\n    return {'v': [1.0]}\n")
    conjured_registry.add_handler(
        f"{module}.embed",
        ServiceDeclaration(
            reads=(_fd("q"),), output_schema=(_fd("v"),),
            service_bindings=(ServiceBindingDecl(name="emb", type="x.y"),),
        ),
        toml_path="h.toml",
    )
    with pytest.raises(BoundaryViolation):
        get_handler_fn(conjured_registry, f"{module}.embed")


def test_refuses_transform_with_bindings(conjured_registry, module_writer):
    module = module_writer("th_xb_mod", "def f(*, text, cfg):\n    return {'out': text}\n")
    binding = Binding(name="cfg", body=SchemaBinding(fields=(_fd("marker"),)))
    conjured_registry.add_handler(
        f"{module}.f",
        TransformDeclaration(reads=(_fd("text"),), output_schema=(_fd("out"),), bindings=(binding,)),
        toml_path="h.toml",
    )
    with pytest.raises(BoundaryViolation):
        get_handler_fn(conjured_registry, f"{module}.f")


def test_refuses_hook(conjured_registry, module_writer):
    module = module_writer("th_hook_mod", "def log(*, dialogue):\n    return None\n")
    conjured_registry.add_handler(
        f"{module}.log", HookDeclaration(reads=(_fd("dialogue"),)), toml_path="h.toml",
    )
    with pytest.raises(BoundaryViolation):
        get_handler_fn(conjured_registry, f"{module}.log")


def test_refuses_unregistered(conjured_registry):
    with pytest.raises(BoundaryViolation) as exc:
        get_handler_fn(conjured_registry, "nope.missing")
    # The truly-unregistered arm keeps the add_handler remedy (and now names the composition
    # possibility without mis-directing) — distinct from the composition-by-path arm below.
    assert "no handler 'nope.missing' is registered" in str(exc.value)


def test_refuses_composition_registered_by_path(conjured_registry):
    # A composition registers by PATH (add_composition), not as a handler — get_handler_fn has no
    # bare-call form for it and refuses with the composition-specific steering, directing to dispatch
    # and explicitly NOT to add_handler (33#1/33#3). get_handler_fn only checks membership in
    # registry.compositions, so a placeholder value suffices to exercise the arm. RED-on-removal: drop
    # the composition-by-path arm and this path falls to the generic "register with add_handler"
    # message — the wrong remedy for a composition.
    conjured_registry.add_composition("trainables/dialogue.toml", object())  # membership is all that is read
    with pytest.raises(BoundaryViolation) as exc:
        get_handler_fn(conjured_registry, "trainables/dialogue.toml")
    message = str(exc.value)
    assert "composition registered by path" in message
    assert "do NOT add_handler" in message
