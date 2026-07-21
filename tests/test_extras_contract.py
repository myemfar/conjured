"""The optional-extras import contract (README § Install): a missing backend stack raises a
clear ``ImportError`` naming the extra — never a raw traceback into the stack's own module
(enforcement-coverage E12: the floor venv always has the extras installed, so an in-process
test can only ever see the happy path; every guard was ``pragma: no cover`` with nothing
adversarial anywhere).

Each test runs a FRESH interpreter with the backing library blocked by a meta-path finder
installed before any ``conjured`` import — the only honest adversary for an import-time
guard. The child asserts and exits non-zero on a broken guard; the parent surfaces the
child's output on failure.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

_BLOCKER_TEMPLATE = """\
import sys

class _BlockedStack:
    def __init__(self, names):
        self._names = names
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] in self._names:
            raise ModuleNotFoundError(
                "No module named " + repr(fullname), name=fullname)
        return None

sys.meta_path.insert(0, _BlockedStack({names!r}))
"""


def _run_with_blocked_stack(names: set[str], body: str) -> None:
    script = _BLOCKER_TEMPLATE.format(names=names) + textwrap.dedent(body)
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, (
        f"blocked-stack child failed (stack {sorted(names)} blocked):\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


# verifies: compilers-extra-importerror
def test_jinja_compiler_names_the_compilers_extra_when_jinja2_is_absent():
    _run_with_blocked_stack({"jinja2"}, """
        from conjured.lib.compilers import jinja
        try:
            jinja(source="hello {{ name }}")
        except ImportError as exc:
            assert "conjured[compilers]" in str(exc), f"extra not named: {exc}"
        else:
            raise SystemExit("jinja compiled without jinja2 - the lazy guard is gone")
    """)


# verifies: compilers-extra-importerror
def test_json_schema_compiler_names_the_compilers_extra_when_jsonschema_is_absent():
    _run_with_blocked_stack({"jsonschema"}, """
        from conjured.lib.compilers import json_schema
        try:
            json_schema(schema={"type": "object"})
        except ImportError as exc:
            assert "conjured[compilers]" in str(exc), f"extra not named: {exc}"
        else:
            raise SystemExit("json_schema compiled without jsonschema - the lazy guard is gone")
    """)


# verifies: server-extra-importerror
def test_importing_the_server_names_the_server_extra_when_the_stack_is_absent():
    _run_with_blocked_stack({"starlette", "sse_starlette", "uvicorn"}, """
        try:
            import conjured.server  # noqa: F401
        except ModuleNotFoundError as exc:
            assert "conjured[server]" in str(exc), f"extra not named: {exc}"
        else:
            raise SystemExit("import conjured.server succeeded with the ASGI stack blocked")
    """)


def test_a_non_stack_import_failure_is_not_relabeled_as_the_server_extra():
    # The guard converts ONLY a miss of the extra's own stack; a defect inside the server
    # package (here: pydantic blocked, which conjured itself needs) must propagate untouched
    # - relabeling a real break as "install the extra" would mask it (fail loud).
    _run_with_blocked_stack({"pydantic"}, """
        try:
            import conjured.server  # noqa: F401
        except ModuleNotFoundError as exc:
            assert "conjured[server]" not in str(exc), (
                f"a pydantic miss was relabeled as the server extra: {exc}")
        else:
            raise SystemExit("import conjured.server succeeded with pydantic blocked")
    """)
