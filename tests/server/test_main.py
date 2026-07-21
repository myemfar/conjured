"""The ``python -m conjured.server`` launch surface (enforcement-coverage E13): the bundled
Client's happy path always passes a zero-arg factory as ``--app`` and never sets
``--stream-timeout``, so ``_resolve_app``'s other arms and the parser→``create_app`` plumbing
were reachable by no test. Patching at the ``conjured.server`` seam is sanctioned (the server
is deliberately not an engine-internal module in the mock policy); ``_resolve_app`` itself
runs unpatched.
"""

from __future__ import annotations

import sys
import types

import pytest

from conjured.server.__main__ import _resolve_app, _write_port_file, main

_MODULE_NAME = "_conjured_test_served_pipelines"


@pytest.fixture
def served_module(monkeypatch):
    mod = types.ModuleType(_MODULE_NAME)
    mod.MAPPING = {}
    mod.factory = lambda: mod.MAPPING
    mod.not_a_mapping = ["nope"]
    mod.factory_wrong = lambda: ["nope"]
    monkeypatch.setitem(sys.modules, _MODULE_NAME, mod)
    return mod


def test_resolve_app_returns_a_mapping_attr_verbatim(served_module):
    assert _resolve_app(f"{_MODULE_NAME}:MAPPING") is served_module.MAPPING


def test_resolve_app_calls_a_zero_arg_factory(served_module):
    assert _resolve_app(f"{_MODULE_NAME}:factory") is served_module.MAPPING


def test_resolve_app_rejects_a_spec_without_a_colon():
    with pytest.raises(ValueError, match="module:attr"):
        _resolve_app("not_an_import_spec")


@pytest.mark.parametrize("attr", ["not_a_mapping", "factory_wrong"])
def test_resolve_app_rejects_a_non_mapping_loud(served_module, attr):
    with pytest.raises(TypeError, match="not a"):
        _resolve_app(f"{_MODULE_NAME}:{attr}")


def test_resolve_app_fails_loud_on_a_missing_attribute(served_module):
    with pytest.raises(AttributeError):
        _resolve_app(f"{_MODULE_NAME}:no_such_attr")


def test_write_port_file_leaves_only_the_final_value(tmp_path):
    target = tmp_path / "port"
    _write_port_file(str(target), 43210)
    assert target.read_text(encoding="utf-8") == "43210"
    assert not (tmp_path / "port.tmp").exists()  # the temp name is replaced, never left


def test_main_plumbs_stream_timeout_and_the_resolved_app_into_create_app(
    monkeypatch, served_module, tmp_path
):
    import uvicorn

    captured = {}

    def fake_create_app(pipelines, stream_timeout_s=None):
        captured["pipelines"] = pipelines
        captured["stream_timeout_s"] = stream_timeout_s
        return object()

    class _NoServeServer:
        def __init__(self, config):
            pass

        def run(self, sockets=None):
            for sock in sockets or []:
                sock.close()

    monkeypatch.setattr("conjured.server.__main__.create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "Server", _NoServeServer)

    port_file = tmp_path / "port"
    main([
        "--app", f"{_MODULE_NAME}:factory",
        "--port-file", str(port_file),
        "--stream-timeout", "12.5",
    ])

    assert captured["pipelines"] is served_module.MAPPING
    assert captured["stream_timeout_s"] == 12.5
    # the bound ephemeral port was written (atomically) before serving
    assert port_file.read_text(encoding="utf-8").isdigit()
