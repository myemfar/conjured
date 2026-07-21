"""Canonical constraint → GBNF grammar — the llama.cpp wire-form projection of the
literal-equal artifact (R-handler-005: "each service-type adapter maps the declared
channel shape to the backend's structured-output API (… llama-server grammar …)").

:func:`grammar_from_constraint` consumes the canonical strict JSON Schema dict
:func:`conjured.adapters.wire.render_output_constraint` renders (never a hand-authored
schema — one constraint artifact, two wire projections) and emits a GBNF grammar the
serving runtime enforces **token-by-token at decode** (§ Trainable backends property 1:
the seal lives at the server). The grammar admits exactly the JSON value space of the
declared shape:

- a fixed-key object per closed object level, **keys in declaration order** (a grammar
  is sequential; the canonical rendering fixes the order deterministically, so a
  training corpus and an inference call see one key order);
- ``string`` / ``integer`` / ``number`` / ``boolean`` / ``null`` JSON productions;
- a ``minLength`` / ``maxLength``-bounded string as a counted ``string-char`` repetition
  (``"\"" string-char{m,n} "\""`` — the D2 accepted matrix's length keywords; the
  engine-side model enforces the same bound, so the seal stays literal-equal);
- enum members as literal alternation; optionals as a ``<T> | null`` union;
- ``dict[str, <T>]`` as an open-keyed string-keyed object — expressible under GBNF (a
  wire-form coverage difference from the OpenAI strict form, which rejects it at
  construction);
- bounded structural whitespace via a shared ``ws`` rule — at most 20 whitespace
  characters per structural gap (a fixed ``{0,20}`` repetition, the llama.cpp
  ``json.gbnf`` convention), so an emission cannot stall the decode in an unbounded
  whitespace loop.

The converter is deterministic: the same constraint dict yields a byte-identical
grammar. Anything outside the canonical renderer's output subset raises ``ValueError``
(engine-internal misuse — the renderer is the only sanctioned producer; author-facing
rejections fired there, at compose).
"""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import Mapping

#: Shared terminal productions, emitted only when referenced (deterministic order).
_TERMINALS: tuple[tuple[str, str], ...] = (
    ("string", '"\\"" string-char* "\\""'),
    (
        "string-char",
        '[^"\\\\\\x7F\\x00-\\x1F] | "\\\\" (["\\\\/bfnrt] | "u" hex hex hex hex)',
    ),
    # The DECODED-character counting unit for length-bounded strings: one raw character,
    # one short escape, one NON-SURROGATE \uXXXX escape — or one surrogate PAIR of
    # escapes (high then low), which decodes to a single character. `string-char` counts
    # a pair as two units, silently diverging from the engine-side minLength/maxLength
    # (which count decoded Python characters) exactly at the astral boundary; this unit
    # keeps the grammar-side count literal-equal to the model-side count.
    (
        "bounded-string-char",
        '[^"\\\\\\x7F\\x00-\\x1F] | "\\\\" (["\\\\/bfnrt] '
        '| "u" [0-9a-cA-Ce-fE-F] hex hex hex | "u" [dD] [0-7] hex hex) '
        '| "\\\\" "u" [dD] [89abAB] hex hex "\\\\" "u" [dD] [c-fC-F] hex hex',
    ),
    ("hex", "[0-9a-fA-F]"),
    ("integer", '"-"? ("0" | [1-9] [0-9]*)'),
    (
        "number",
        '"-"? ("0" | [1-9] [0-9]*) ("." [0-9]+)? ([eE] [-+]? [0-9]+)?',
    ),
    ("boolean", '"true" | "false"'),
    ("null", '"null"'),
    ("ws", "[ \\t\\n\\r]{0,20}"),
)

#: ``string-char`` and ``hex`` ride along whenever ``string`` is used. Immutable by
#: construction (no module-level mutable state — R-handler-pure-module).
_TERMINAL_DEPS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {"string": ("string-char", "hex")}
)

_PRIMITIVE_TERMINALS: Mapping[str, str] = MappingProxyType(
    {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }
)


def _gbnf_literal(text: str) -> str:
    """A GBNF string literal matching ``text`` exactly."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _json_literal(value: object) -> str:
    """The JSON text of one enum member (bool before int — bool is an int subclass)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


class _GrammarBuilder:
    def __init__(self) -> None:
        self._rules: list[tuple[str, str]] = []  # (name, production), emission order
        self._names: set[str] = set()
        self._terminals_used: set[str] = set()

    def _claim(self, base: str) -> str:
        """A unique rule name from a sanitized base (field names may carry
        underscores; GBNF rule names are ``[a-zA-Z0-9-]``). A base whose sanitized
        form escapes that ASCII charset (a non-ASCII alphanumeric survives the
        sanitizer) is engine-internal misuse — the GBNF adapter rejects non-ASCII
        field names at compose, so nothing sanctioned reaches here with one."""
        sanitized = "".join(c if c.isalnum() else "-" for c in base).strip("-") or "r"
        if not sanitized.isascii():
            raise ValueError(
                f"rule-name base {base!r} does not render to the GBNF rule-name "
                "charset [a-zA-Z0-9-] (non-ASCII field names are rejected at compose "
                "by the GBNF adapter — reaching here is engine-internal misuse)"
            )
        name = sanitized
        suffix = 2
        while name in self._names or name in _PRIMITIVE_TERMINALS or name in (
            "string-char",
            "hex",
            "ws",
        ):
            name = f"{sanitized}-{suffix}"
            suffix += 1
        self._names.add(name)
        return name

    def _terminal(self, name: str) -> str:
        self._terminals_used.add(name)
        for dep in _TERMINAL_DEPS.get(name, ()):
            self._terminals_used.add(dep)
        return name

    def _ws(self) -> str:
        return self._terminal("ws")

    def ref(self, node: Mapping, base: str) -> str:
        """The reference expression for one canonical-schema node — a terminal name or
        a freshly-emitted named rule."""
        if "enum" in node:
            # Enum members render as a literal alternation — the grammar's accepted value space
            # is exactly the listed members. A co-declared `minLength`/`maxLength` on the SAME
            # node (both are in this wire's accepted matrix) is NOT re-rendered here as a
            # repetition and needs none: the compose-time coherence checks
            # (`validator/resolve_validator.py`, run at model build) guarantee BOTH arms —
            # `check_enum_type_coherence` that every member is admissible under the field's
            # declared type, and `check_enum_bound_coherence` that every member satisfies a
            # co-declared length bound — so the alternation already equals the engine-side
            # model's `type ∩ enum ∩ bound` accepted space and
            # the seal stays literal-equal (R-handler-005). This is the "author-facing rejections
            # fired at compose" contract this converter relies on (module docstring).
            name = self._claim(base)
            production = " | ".join(
                _gbnf_literal(_json_literal(v)) for v in node["enum"]
            )
            self._rules.append((name, production))
            return name
        if "anyOf" in node:
            # The canonical renderer emits anyOf only for optionals: [<T>, null].
            members = node["anyOf"]
            if len(members) != 2 or members[1] != {"type": "null"}:
                raise ValueError(
                    "unsupported anyOf shape (the canonical renderer emits only "
                    "[<T>, {'type': 'null'}])"
                )
            inner = self.ref(members[0], base)
            name = self._claim(f"{base}-opt")
            self._rules.append((name, f"{inner} | {self._terminal('null')}"))
            return name
        node_type = node.get("type")
        if node_type == "string" and ("minLength" in node or "maxLength" in node):
            # A length-bounded string (the D2 accepted matrix: minLength/maxLength render
            # on the GBNF wire as a counted repetition of `bounded-string-char` — the
            # DECODED-character unit: a surrogate-pair escape counts as ONE, matching the
            # engine-side model's character counting, so the seal stays literal-equal at
            # the astral boundary too). `{m,}` (min only), `{0,n}` (max only), `{m,n}`
            # (both) are llama.cpp GBNF repetition forms.
            self._terminal("bounded-string-char")
            self._terminal("hex")  # the escape alternatives reference hex
            lo = node.get("minLength", 0)
            hi = node.get("maxLength")
            rep = f"{{{lo},{hi}}}" if hi is not None else f"{{{lo},}}"
            name = self._claim(base)
            self._rules.append((name, f'"\\"" bounded-string-char{rep} "\\""'))
            return name
        if node_type in ("string", "integer", "number", "boolean", "null"):
            return self._terminal(_PRIMITIVE_TERMINALS[node_type])
        if node_type == "array":
            ws = self._ws()
            item = self.ref(node["items"], f"{base}-item")
            name = self._claim(base)
            self._rules.append(
                (name, f'"[" {ws} ({item} ({ws} "," {ws} {item})*)? {ws} "]"')
            )
            return name
        if node_type == "object":
            ws = self._ws()
            if "properties" in node:  # a closed fixed-key object level
                parts: list[str] = ['"{"', ws]
                for i, (key, member) in enumerate(node["properties"].items()):
                    member_ref = self.ref(member, f"{base}-{key}")
                    if i:
                        parts.extend([ws, '","', ws])
                    parts.extend(
                        [_gbnf_literal(json.dumps(key)), ws, '":"', ws, member_ref]
                    )
                parts.extend([ws, '"}"'])
                name = self._claim(base)
                self._rules.append((name, " ".join(parts)))
                return name
            # An open-keyed dict[str, <T>] level (additionalProperties carries the
            # value schema).
            value_ref = self.ref(node["additionalProperties"], f"{base}-value")
            kv_name = self._claim(f"{base}-kv")
            self._rules.append(
                (kv_name, f'{self._terminal("string")} {ws} ":" {ws} {value_ref}')
            )
            name = self._claim(base)
            self._rules.append(
                (name, f'"{{" {ws} ({kv_name} ({ws} "," {ws} {kv_name})*)? {ws} "}}"')
            )
            return name
        raise ValueError(f"unsupported canonical-schema node at '{base}': {node!r}")

    def render(self) -> str:
        lines = [f"{name} ::= {production}" for name, production in self._rules]
        for name, production in _TERMINALS:
            if name in self._terminals_used:
                lines.append(f"{name} ::= {production}")
        return "\n".join(lines) + "\n"


def grammar_from_constraint(schema: Mapping) -> str:
    """The canonical strict-constraint dict → a GBNF grammar whose language is exactly
    the JSON value space of the declared shape, rooted at ``root``."""
    builder = _GrammarBuilder()
    builder._names.add("root")
    body_ref = builder.ref(schema, "root-value")
    builder._rules.insert(0, ("root", body_ref))
    return builder.render()
