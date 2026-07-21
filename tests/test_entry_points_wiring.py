"""``[project.scripts]`` + ``[project.entry-points]`` wiring parity (enforcement-coverage E9,
floor-grain half): every declared entry point resolves to a real module/callable in the
CURRENT source tree. Without this, a module move or a pyproject typo ships a wheel whose
installed ``conjured`` command (or pytest11 plugin) dies at bootstrap while every test stays
green — the suites import the targets directly, and the editable venv's stale entry-point
metadata keeps local invocation working. The wheel-grain half (the installed script shim the
index actually serves) is the release smoke test's shipped-surface block
(release-policy § The pre-release gate).
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _project_table() -> dict:
    with open(_PYPROJECT, "rb") as fh:
        return tomllib.load(fh)["project"]


def _resolve(target: str):
    """Resolve an entry-point value — ``module`` or ``module:attr`` — loud on any miss."""
    module_name, sep, attr = target.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr) if sep else module


def test_the_console_script_is_declared_and_resolves_to_the_cli_main():
    import conjured.cli

    scripts = _project_table().get("scripts", {})
    assert "conjured" in scripts, "the 'conjured' console command is no longer declared"
    assert _resolve(scripts["conjured"]) is conjured.cli.main


def test_every_declared_script_resolves_to_a_callable():
    for name, target in _project_table().get("scripts", {}).items():
        assert ":" in target, f"[project.scripts] {name}: {target!r} is not 'module:attr'"
        assert callable(_resolve(target)), f"[project.scripts] {name}: {target!r} is not callable"


def test_every_entry_point_group_value_resolves():
    groups = _project_table().get("entry-points", {})
    assert "pytest11" in groups, "the pytest11 plugin registration is no longer declared"
    for group, entries in groups.items():
        for name, target in entries.items():
            _resolve(target)  # raises loud (ImportError / AttributeError) on a broken value
