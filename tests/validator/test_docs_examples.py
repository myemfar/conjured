"""Canon's own declaration artifacts parse under the shipped parsers (enforcement-coverage
E10). Two arms:

1. **The kind-schema templates** — each ``components/handler/kind-schemas/*.schema.toml``,
   after the README's own documented authoring transform (delete the schema-only ``[meta]``;
   drop the schema-only ``example`` keys; composition kinds author their own ``[meta]``),
   must load under its kind's parser. ``schema-example-parity`` derives its section rosters
   FROM the schemas, so schema-vs-parser drift inverts it into policing examples against the
   wrong roster — this arm is the only instrument comparing schema to parser.
2. **The complete worked examples** — every ```` ```toml ```` block in ``conjured/docs`` that
   classifies as a complete declaration must load under its kind's parser. A drifted worked
   example sits in no mechanical instrument's view: a consumer copying canon's own blessed
   example would be rejected at load while the floor stays green.

Self-contained (ships in the sdist; reads the docs tree the wheel force-includes) — the
counter-example rejection-marker convention is stated locally, since shipping tests import
no build-side tooling.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from conjured.validator import parse

_DOCS = Path(__file__).resolve().parents[2] / "docs"
_SCHEMA_DIR = _DOCS / "components" / "handler" / "kind-schemas"

_HANDLER_KINDS = {"transform", "service", "hook"}
_COMPOSITION_KINDS = {"trainable", "pipeline", "bundle"}

# The corpus's counter-example convention: a block annotated with rejection vocabulary
# deliberately shows a forbidden shape — sanctioned teaching content, not drift.
_REJECTION_RE = re.compile(
    r"contractviolation|unknown block|\brejected\b|\bforbidden\b|must not|not allowed",
    re.IGNORECASE,
)

# A shape-summary fragment, not a declaration: an `# (or)` alternation marker (several
# mutually-exclusive headers shown at once) or an `# ...` elision marker (content omitted).
_INCOMPLETE_RE = re.compile(r"^\s*#\s*\(or\)|^\s*#\s*\.\.\.", re.MULTILINE)

_TOML_BLOCK_RE = re.compile(r"^```toml[^\S\n]*\n(.*?)^```[^\S\n]*$", re.MULTILINE | re.DOTALL)


# ---------------------------------------------------------------------------
# Arm 1 — the six kind-schema templates
# ---------------------------------------------------------------------------


def _strip_schema_only_keys(node):
    """The README transform's 'drop the ``example`` key from each field' step, recursively."""
    if isinstance(node, dict):
        return {k: _strip_schema_only_keys(v) for k, v in node.items() if k != "example"}
    if isinstance(node, list):
        return [_strip_schema_only_keys(v) for v in node]
    return node


def _comment_example_lines(text: str):
    """The schemas' 'Example authored block:' comment convention — sections whose authored
    form is not a live table (a composition's own ``[meta]``; the bundle's ``[[nodes]]``)
    carry it as ``#   ``-indented example TOML. Marker prose may continue before the example
    starts; once collecting, a bare ``#`` is a blank line and any other prose ENDS the block
    (a schema may follow one example with an illustration of a *different* file — e.g. the
    referenced handler's own declaration — which must not merge into this one)."""
    state = "idle"  # idle → seeking (marker seen) → collecting (inside the example TOML)
    for ln in text.splitlines():
        if "Example authored block" in ln:
            state = "seeking"
            continue
        if state == "idle":
            continue
        is_example = ln.startswith("#   ")
        if state == "seeking":
            if is_example:
                state = "collecting"
                yield ln[4:]
            elif not ln.startswith("#"):
                state = "idle"  # ran off the comment block without an example
            continue
        if is_example:
            yield ln[4:]
        elif ln.strip() == "#":
            yield ""
        else:
            state = "idle"


def _authored_from_template(schema_path: Path) -> tuple[str, dict]:
    """Apply the README's authoring transform to a template: delete the schema-only
    ``[meta]``, drop ``example`` keys from the live sections, and take each authored-form
    comment example as its section's content. Returns ``(parse_kind, declaration)``."""
    text = schema_path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    schema_for = data["meta"]["schema_for"]
    authored = _strip_schema_only_keys({k: v for k, v in data.items() if k != "meta"})
    example_lines = list(_comment_example_lines(text))
    examples = tomllib.loads("\n".join(example_lines)) if example_lines else {}
    for key, value in examples.items():
        if key in authored:
            continue  # the live table is the template's actual section; the comment duplicate is illustrative
        authored[key] = value
    if schema_for in _COMPOSITION_KINDS:
        if "meta" not in authored:  # no authored-meta example → the minimal {kind, name}
            authored = {"meta": {"kind": schema_for, "name": "acme.schema_template_check"}, **authored}
        return ("composition", authored)
    assert schema_for in _HANDLER_KINDS, f"unknown schema_for {schema_for!r} in {schema_path.name}"
    return ("handler", authored)


def _schema_paths() -> list[Path]:
    return sorted(_SCHEMA_DIR.glob("*.schema.toml"))


def test_the_schema_roster_is_present():
    # anti-vacuity floor for the parametrized arm below (an empty glob must not pass silently)
    assert _schema_paths(), f"no kind-schema templates found under {_SCHEMA_DIR}"


@pytest.mark.parametrize("schema_path", _schema_paths(), ids=lambda p: p.name)
def test_every_kind_schema_template_parses_after_the_authoring_transform(schema_path):
    kind, authored = _authored_from_template(schema_path)
    parse(authored, kind, file_path=str(schema_path))  # loud on any schema-vs-parser drift


# ---------------------------------------------------------------------------
# Arm 2 — the complete worked examples across the corpus
# ---------------------------------------------------------------------------


def _classify(parsed: dict):
    """``(parse_kind, data)`` for a COMPLETE declaration block, else ``None``. Conservative:
    a partial illustrative snippet must not classify (reference docs legitimately show
    fragments — a fragment simply never reaches the parser here)."""
    tables = {k for k, v in parsed.items() if isinstance(v, dict)}
    meta = parsed.get("meta")
    if isinstance(meta, dict):
        if "schema_for" in meta:
            return None  # a schema template — arm 1's territory
        if meta.get("kind") in _COMPOSITION_KINDS:
            return ("composition", parsed)
        if "name" in meta and "nodes" in parsed:
            return ("pipeline", parsed)
        return None
    handler_headers = tables & _HANDLER_KINDS
    if len(handler_headers) == 1:
        return ("handler", parsed)
    if "name" in parsed and ({"identity_schema", "transport_schema", "config_schema"} & tables):
        return ("service_type", parsed)
    if "training_contract" in parsed and ({"transport", "hook_transport"} & tables):
        return ("deployment", parsed)
    return None


def _iter_candidate_blocks():
    for md in sorted(_DOCS.rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        for match in _TOML_BLOCK_RE.finditer(text):
            content = match.group(1)
            if _REJECTION_RE.search(content):
                continue  # an annotated counter-example
            if _INCOMPLETE_RE.search(content):
                continue  # an alternation/elision shape summary, not one declaration
            try:
                parsed = tomllib.loads(content)
            except tomllib.TOMLDecodeError:
                continue  # a partial snippet, not standalone TOML
            classified = _classify(parsed)
            if classified is not None:
                yield md.relative_to(_DOCS).as_posix(), classified


def test_every_complete_worked_declaration_in_docs_parses():
    failures: list[str] = []
    checked = 0
    for where, (kind, data) in _iter_candidate_blocks():
        checked += 1
        try:
            parse(data, kind, file_path=f"docs/{where}")
        except Exception as exc:  # noqa: BLE001 — every failure is reported, none swallowed
            failures.append(f"{where} [{kind}]: {type(exc).__name__}: {exc}")
    assert checked > 0, "the classifier found no complete worked declarations — vacuous walk"
    assert not failures, (
        f"{len(failures)} worked example(s) rejected by the shipped parsers "
        f"(canon's own copy-paste surface is broken):\n" + "\n".join(failures)
    )
