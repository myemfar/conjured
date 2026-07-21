"""The blessed first-party **compile-affordance compilers** — the engine-shipped
``compile = "<name>"`` directive vocabulary (``conjured/docs/components/handler/reference.md``
§ The ``compile = "..."`` directive sub-form; the per-compiler contracts there).

Each is a **deterministic ``params → artifact`` bare kwarg-only function**: the engine
introspects its signature against the directive's declared parameters, binds those parameters
at compose (engine-owned — authors write no factory or closure), and runs it once at binding
resolution to produce the artifact the binding delivers as its kwarg value. The engine does not
interpret the artifact beyond this contract — it is forwarded as-is (vector-4-copy-exempt).

These three are the engine's reserved **bare-name** compile vocabulary (``CompilePrimitive``),
the compile-directive analogue of the built-in field-validator keywords (``constraints.py``):
co-housed engine functions resolved through a static table (``validator.resolve_compile`` owns
``BUILTIN_COMPILERS``), **not** dotted-path handler resolution and **not** carrying a Pattern-B
TOML declaration (they are not handlers — they take no channels and emit no record). A bare
``compile`` value resolves here; a dotted value is an open third-party compiler.

``jinja`` and ``json_schema`` import their backing library (``jinja2`` / ``jsonschema``) **lazily
inside the function**: those libraries ship in the optional ``conjured[compilers]`` extra so the
engine core stays pydantic-only (the same posture as the ``conjured[server]`` extra). A missing
backing library raises a clear ``ImportError`` naming the extra — propagated raw (an environment
problem, not an authoring ``ContractViolation``), exactly as ``import conjured.server`` does. The
``regex`` compiler uses the standard-library ``re`` and needs no extra.
"""

from __future__ import annotations

import json
import re

#: The pip extra carrying the optional compiler backends (jinja2 / jsonschema).
_COMPILERS_EXTRA_HINT = (
    "install the optional compiler backends: pip install 'conjured[compilers]'"
)


def regex(*, pattern: str, flags: str | None = None) -> re.Pattern:
    """Compile ``pattern`` into a :class:`re.Pattern` (standard-library ``re``). ``flags`` is an
    optional string naming one or more :class:`re.RegexFlag` members, ``|``-separated
    (``"IGNORECASE"`` or ``"IGNORECASE|MULTILINE"``) — the Python source idiom; absent → no
    flags. A pattern that does not compile, or an unknown or empty flag segment (a stray or
    doubled ``|``, or a blank ``flags``), raises (the compiler's own failure — the engine maps
    it to a compose-time ``ContractViolation`` at binding resolution; a compose-read parameter
    never silently no-ops).
    """
    compiled_flags = re.RegexFlag(0)
    if flags is not None:
        for token in flags.split("|"):
            name = token.strip()
            try:
                compiled_flags |= re.RegexFlag[name]
            except KeyError as exc:
                raise ValueError(
                    f"unknown regex flag {name!r} — name one or more re.RegexFlag members "
                    f"(e.g. IGNORECASE, MULTILINE, DOTALL), '|'-separated"
                ) from exc
    return re.compile(pattern, compiled_flags)


def jinja(*, source: str):
    """Compile the inline template ``source`` into a ``jinja2.Template`` (the artifact named by
    the per-compiler contract). Uses a default ``jinja2.Environment`` (jinja2's documented
    ``Environment().from_string`` entry point) — no environment knobs are exposed. An unparseable
    template raises ``jinja2.TemplateSyntaxError`` (mapped to a compose-time ``ContractViolation``).
    """
    try:
        import jinja2
    except ImportError as exc:  # pragma: no cover - environment-dependent
        # guarantees: compilers-extra-importerror
        raise ImportError(
            f"the 'jinja' compiler needs the jinja2 library; {_COMPILERS_EXTRA_HINT}"
        ) from exc
    return jinja2.Environment().from_string(source)


def json_schema(*, schema: dict | str):
    """Compile the JSON Schema ``schema`` into a ``jsonschema`` validator (the artifact named by the
    per-compiler contract). ``schema`` is the inline TOML object (a ``dict``) OR, when the parameter
    is supplied from a file (``schema = { file = "..." }``), the file's **raw text** — the engine
    reads the file as text and the compiler **parses it as JSON** (handler/reference.md § The
    ``compile = "..."`` directive sub-form: "``json_schema`` reads the text as JSON"). A ``str`` that
    is not valid JSON raises ``json.JSONDecodeError`` (mapped to a compose-time ``ContractViolation``,
    the same ``COMPILE_ARTIFACT`` class an invalid schema takes).

    ``validator_for`` selects the draft from the schema's ``$schema`` (draft-agnostic — pinning one
    draft would bake an incidental); ``check_schema`` rejects an invalid schema with
    ``jsonschema.exceptions.SchemaError``. Returns a validator instance bound to the schema.
    """
    parsed = json.loads(schema) if isinstance(schema, str) else schema
    # file-supplied text → the compiler parses it as JSON
    try:
        from jsonschema import validators
    except ImportError as exc:  # pragma: no cover - environment-dependent
        # guarantees: compilers-extra-importerror
        raise ImportError(
            f"the 'json_schema' compiler needs the jsonschema library; {_COMPILERS_EXTRA_HINT}"
        ) from exc
    validator_cls = validators.validator_for(parsed)
    validator_cls.check_schema(parsed)
    return validator_cls(parsed)
