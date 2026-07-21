"""``conjured.adapters.gbnf`` — the canonical-constraint → GBNF projection: per-node
productions, structural well-formedness (every referenced rule defined, ``root``
present), determinism, the engine-internal misuse guards, and the grammar's
**language** (a golden literal grammar + a test-side recursive-descent acceptance
walk — so a mutation that re-shapes the emitted language, not just the rule
structure, fails a test)."""

from __future__ import annotations

import json

import pytest

from conjured.adapters.gbnf import grammar_from_constraint
from conjured.adapters.wire import render_output_constraint
from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import (
    FieldDecl,
    ValidatorSpec,
    dict_of,
    list_of,
    literal,
    nested,
    optional,
    primitive,
)
from tests.lib.fakes import check_gbnf

SOURCE = "compositions/fixture.toml"

#: The GBNF wire's accepted-keyword set (the D2 accepted matrix) — enum + the two length
#: bounds render; pattern stays reject-only.
GBNF_ACCEPTED = frozenset({"enum", "minLength", "maxLength"})


def grammar(*fields):
    return grammar_from_constraint(
        render_output_constraint(tuple(fields), schema_source=SOURCE)
    )


def grammar_with(*fields, accepted=GBNF_ACCEPTED):
    return grammar_from_constraint(
        render_output_constraint(
            tuple(fields), schema_source=SOURCE, accepted_keywords=accepted, wire="gbnf"
        )
    )


def rules_of(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if line.strip():
            name, production = line.split("::=", 1)
            out[name.strip()] = production.strip()
    return out


def test_object_rule_fixes_keys_in_declaration_order():
    text = grammar(
        FieldDecl(name="dialogue", type=primitive("str")),
        FieldDecl(name="mood", type=literal("happy", "sad")),
    )
    assert check_gbnf(text) == []
    rules = rules_of(text)
    root_value = rules[rules["root"]]  # root aliases the rendered object rule
    # Keys appear as fixed literals, declaration order preserved:
    assert root_value.index('"\\"dialogue\\""') < root_value.index('"\\"mood\\""')


def test_primitive_terminals_are_emitted_only_when_referenced():
    rules = rules_of(grammar(FieldDecl(name="n", type=primitive("int"))))
    assert "integer" in rules
    # Object keys are inline fixed literals, not the string terminal; with no string
    # VALUE anywhere, the string production (and the other unused terminals) never
    # ride along:
    assert "string" not in rules
    assert "boolean" not in rules
    assert "number" not in rules


def test_enum_members_render_as_json_literal_alternation():
    rules = rules_of(grammar(FieldDecl(name="pick", type=literal("a", 2, True))))
    enum_rule = next(p for n, p in rules.items() if n.endswith("pick"))
    assert '"\\"a\\""' in enum_rule
    assert '"2"' in enum_rule
    assert '"true"' in enum_rule


def test_optional_renders_as_null_union():
    text = grammar(FieldDecl(name="note", type=optional(primitive("str"))))
    assert check_gbnf(text) == []
    rules = rules_of(text)
    opt_rule = next(p for n, p in rules.items() if n.endswith("-opt"))
    assert "null" in opt_rule and "|" in opt_rule
    assert "null" in rules


def test_list_dict_nested_all_project_to_sound_grammars():
    text = grammar(
        FieldDecl(name="tags", type=list_of(primitive("str"))),
        FieldDecl(name="aliases", type=dict_of(primitive("int"))),
        FieldDecl(
            name="mood",
            type=nested(FieldDecl(name="intensity", type=primitive("int"))),
        ),
    )
    assert check_gbnf(text) == []
    rules = rules_of(text)
    # dict: an open-keyed object whose kv rule pairs the string terminal with the
    # value rule.
    kv_rule = next(p for n, p in rules.items() if n.endswith("-kv"))
    assert kv_rule.startswith("string")
    # list: a star-repeated homogeneous sequence.
    tags_rule = next(p for n, p in rules.items() if n.endswith("tags"))
    assert "*" in tags_rule


def test_grammar_is_deterministic():
    fields = (
        FieldDecl(name="dialogue", type=primitive("str")),
        FieldDecl(name="mood", type=literal("happy", "sad")),
    )
    assert grammar(*fields) == grammar(*fields)


def test_key_literals_are_json_escaped():
    # A field name carrying a quote is not declarable in TOML, but the converter is
    # defensive: JSON-escaping the key keeps the grammar literal well-formed.
    text = grammar_from_constraint(
        {
            "type": "object",
            "properties": {'we"ird': {"type": "integer"}},
            "required": ['we"ird'],
            "additionalProperties": False,
        }
    )
    assert check_gbnf(text) == []


def test_unsupported_node_is_engine_internal_misuse():
    with pytest.raises(ValueError, match="unsupported"):
        grammar_from_constraint({"type": "frobnicate"})
    with pytest.raises(ValueError, match="anyOf"):
        grammar_from_constraint(
            {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        )


def test_non_ascii_rule_name_base_is_engine_internal_misuse():
    # The GBNF adapter rejects non-ASCII field names at compose (ContractViolation —
    # tested in the adapter suite); the converter's own guard makes an unsanctioned
    # producer fail loud rather than emit an illegal rule name silently.
    with pytest.raises(ValueError, match="rule-name"):
        grammar_from_constraint(
            {
                "type": "object",
                "properties": {"héllo": {"enum": ["a"]}},
                "required": ["héllo"],
                "additionalProperties": False,
            }
        )


def test_colliding_sanitized_rule_names_get_a_deterministic_suffix():
    # Distinct declared names may sanitize to one rule-name base ('a_b' and 'a-b'
    # both render to 'a-b'); the claim loop suffixes the second deterministically
    # and the grammar stays structurally sound.
    text = grammar(
        FieldDecl(name="a_b", type=literal("x")),
        FieldDecl(name="a-b", type=literal("y")),
    )
    assert check_gbnf(text) == []
    rules = rules_of(text)
    assert "root-value-a-b" in rules
    assert "root-value-a-b-2" in rules


# ---------------------------------------------------------------------------
# The accepted matrix on the GBNF wire (D2) — length-bounded strings render as a
# counted string-char repetition; pattern stays reject-only (enum alternation is
# covered by test_enum_members_render_as_json_literal_alternation above)
# ---------------------------------------------------------------------------


def _obj(code: str) -> str:
    return json.dumps({"code": code}, separators=(",", ":"))


def test_length_bounded_string_renders_a_counted_repetition_and_pins_the_language():
    text = grammar_with(
        FieldDecl(
            name="code", type=primitive("str"),
            validators=(
                ValidatorSpec(name="minLength", params={"limit": 2}),
                ValidatorSpec(name="maxLength", params={"limit": 4}),
            ),
        ),
    )
    assert check_gbnf(text) == []
    code_rule = next(p for n, p in rules_of(text).items() if n.endswith("code"))
    assert "string-char{2,4}" in code_rule
    # The decode-time language matches the engine model's minLength/maxLength bound (the
    # seal stays literal-equal): only 2..4-char strings are in the grammar.
    assert not gbnf_accepts(text, _obj("a"))      # 1 char — below minLength
    assert gbnf_accepts(text, _obj("ab"))         # 2 — the floor
    assert gbnf_accepts(text, _obj("abcd"))       # 4 — the ceiling
    assert not gbnf_accepts(text, _obj("abcde"))  # 5 — above maxLength


def test_min_length_only_renders_open_ended_repetition():
    text = grammar_with(
        FieldDecl(
            name="code", type=primitive("str"),
            validators=(ValidatorSpec(name="minLength", params={"limit": 3}),),
        ),
    )
    assert check_gbnf(text) == []
    code_rule = next(p for n, p in rules_of(text).items() if n.endswith("code"))
    assert "string-char{3,}" in code_rule  # open-ended above the floor
    assert not gbnf_accepts(text, _obj("ab"))   # 2 — below the floor
    assert gbnf_accepts(text, _obj("abc"))      # 3 — the floor
    assert gbnf_accepts(text, _obj("abcdef"))   # 6 — open-ended above


def test_max_length_only_renders_zero_bounded_repetition():
    text = grammar_with(
        FieldDecl(
            name="code", type=primitive("str"),
            validators=(ValidatorSpec(name="maxLength", params={"limit": 2}),),
        ),
    )
    assert check_gbnf(text) == []
    code_rule = next(p for n, p in rules_of(text).items() if n.endswith("code"))
    assert "string-char{0,2}" in code_rule
    assert gbnf_accepts(text, _obj(""))         # 0 — admitted (no floor)
    assert gbnf_accepts(text, _obj("ab"))       # 2 — the ceiling
    assert not gbnf_accepts(text, _obj("abc"))  # 3 — above maxLength


def test_pattern_is_reject_only_on_the_gbnf_wire():
    # The ruled scope (2026-06-13): a `pattern` constraint stays a loud compose-time
    # rejection on the GBNF wire — a subtly-wrong regex→GBNF translation would corrupt the
    # literal-equal seal worse than rejecting. `pattern` is NOT in GBNF_ACCEPTED.
    field = FieldDecl(
        name="slug", type=primitive("str"),
        validators=(ValidatorSpec(name="pattern", params={"pattern": "^[a-z]+$"}),),
    )
    with pytest.raises(ContractViolation) as exc:
        grammar_with(field)
    assert exc.value.check is Check.TRAINABLE_CONSTRAINT_UNSUPPORTED
    assert "pattern" in exc.value.actual


def test_enum_and_length_co_declaration_renders_the_enum_alternation():
    """The accepted matrix's enum+length co-declaration (finding 32#2): a field carrying BOTH an
    `enum` and length bounds renders the enum LITERAL ALTERNATION (the enum decode path) — the
    length repetition is not separately emitted, and it need not be. The rendering stays
    literal-equal because the compose-time enum-bound coherence check
    (`validator.resolve_validator.check_enum_bound_coherence`, exercised in
    `tests/validator/test_resolve_validator.py`) guarantees every enum member satisfies the
    co-declared bound, so the alternation already equals the engine-side model's `enum ∩ bound`
    accepted space. Here every member (`ab`, `cd`) satisfies minLength=2/maxLength=2, so the
    grammar's language is exactly those two members. (The renderer itself does not run the
    coherence check — that fires at model build; this test feeds a coherence-passing shape and
    pins the rendering, symmetric with the length-only tests above.)"""
    text = grammar_with(
        FieldDecl(
            name="code", type=primitive("str"),
            validators=(
                ValidatorSpec(name="enum", params={"values": ["ab", "cd"]}),
                ValidatorSpec(name="minLength", params={"limit": 2}),
                ValidatorSpec(name="maxLength", params={"limit": 2}),
            ),
        ),
    )
    assert check_gbnf(text) == []
    code_rule = next(p for n, p in rules_of(text).items() if n.endswith("code"))
    # The enum alternation renders — NOT a string-char repetition (the enum decode path wins).
    assert '"\\"ab\\""' in code_rule and '"\\"cd\\""' in code_rule
    assert "string-char" not in code_rule
    # The language is exactly the two members — a non-member and a shorter string are both out.
    assert gbnf_accepts(text, _obj("ab"))
    assert gbnf_accepts(text, _obj("cd"))
    assert not gbnf_accepts(text, _obj("ef"))  # a 2-char non-member
    assert not gbnf_accepts(text, _obj("a"))   # not in the alternation at all


# ---------------------------------------------------------------------------
# The grammar's LANGUAGE — pinned literally and walked (not re-derived from the
# function under test, which would be circular)
# ---------------------------------------------------------------------------

#: The representative multi-field shape (string + enum + list + nested).
REPRESENTATIVE_FIELDS = (
    FieldDecl(name="dialogue", type=primitive("str")),
    FieldDecl(name="mood", type=literal("happy", "sad")),
    FieldDecl(name="tags", type=list_of(primitive("str"))),
    FieldDecl(name="meta", type=nested(FieldDecl(name="turn", type=primitive("int")))),
)

#: The golden grammar for REPRESENTATIVE_FIELDS — hand-verified once, pinned as a
#: literal so a language-shaping mutation (a separator, a quote, a production body)
#: cannot pass on structural soundness alone.
GOLDEN_GRAMMAR = (
    'root ::= root-value\n'
    'root-value-mood ::= "\\"happy\\"" | "\\"sad\\""\n'
    'root-value-tags ::= "[" ws (string (ws "," ws string)*)? ws "]"\n'
    'root-value-meta ::= "{" ws "\\"turn\\"" ws ":" ws integer ws "}"\n'
    'root-value ::= "{" ws "\\"dialogue\\"" ws ":" ws string ws "," ws "\\"mood\\"" '
    'ws ":" ws root-value-mood ws "," ws "\\"tags\\"" ws ":" ws root-value-tags ws '
    '"," ws "\\"meta\\"" ws ":" ws root-value-meta ws "}"\n'
    'string ::= "\\"" string-char* "\\""\n'
    'string-char ::= [^"\\\\\\x7F\\x00-\\x1F] | "\\\\" (["\\\\/bfnrt] | '
    '"u" hex hex hex hex)\n'
    'hex ::= [0-9a-fA-F]\n'
    'integer ::= "-"? ("0" | [1-9] [0-9]*)\n'
    'ws ::= [ \\t\\n\\r]{0,20}\n'
)


def test_golden_grammar_for_the_representative_shape():
    assert grammar(*REPRESENTATIVE_FIELDS) == GOLDEN_GRAMMAR


# -- A minimal GBNF interpreter over the converter's emitted subset (test-side only):
#    string literals, char classes (negation / ranges / \xNN escapes), rule refs,
#    groups, alternation, and the *, ?, {m,n} postfixes. Matching returns the set of
#    end positions, so alternation and repetition backtrack without recursion blowup.


def _read_class_char(body: str, i: int) -> tuple[str, int]:
    if body[i] == "\\":
        esc = body[i + 1]
        if esc == "x":
            return chr(int(body[i + 2 : i + 4], 16)), i + 4
        return {"n": "\n", "r": "\r", "t": "\t"}.get(esc, esc), i + 2
    return body[i], i + 1


def _parse_class(body: str):
    negated = body.startswith("^")
    if negated:
        body = body[1:]
    ranges: list[tuple[str, str]] = []
    i = 0
    while i < len(body):
        lo, i = _read_class_char(body, i)
        if i < len(body) - 1 and body[i] == "-":
            hi, i = _read_class_char(body, i + 1)
            ranges.append((lo, hi))
        else:
            ranges.append((lo, lo))
    return ("class", negated, tuple(ranges))


def _tokenize(production: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(production):
        ch = production[i]
        if ch.isspace():
            i += 1
        elif ch == '"':
            j, buf = i + 1, []
            while production[j] != '"':
                if production[j] == "\\":
                    esc = production[j + 1]
                    buf.append({"n": "\n", "r": "\r", "t": "\t"}.get(esc, esc))
                    j += 2
                else:
                    buf.append(production[j])
                    j += 1
            tokens.append(("lit", "".join(buf)))
            i = j + 1
        elif ch == "[":
            j = i + 1
            while production[j] != "]":
                j += 2 if production[j] == "\\" else 1
            tokens.append(("classbody", production[i + 1 : j]))
            i = j + 1
        elif ch == "{":
            j = production.index("}", i)
            tokens.append(("bound", production[i + 1 : j]))
            i = j + 1
        elif ch in "()|*?":
            tokens.append((ch, ch))
            i += 1
        else:
            j = i
            while j < len(production) and (
                production[j].isalnum() or production[j] == "-"
            ):
                j += 1
            tokens.append(("ref", production[i:j]))
            i = j
    return tokens


def _parse_expr(tokens, i):
    branches = []
    node, i = _parse_seq(tokens, i)
    branches.append(node)
    while i < len(tokens) and tokens[i][0] == "|":
        node, i = _parse_seq(tokens, i + 1)
        branches.append(node)
    return (("alt", tuple(branches)) if len(branches) > 1 else branches[0]), i


def _parse_seq(tokens, i):
    items = []
    while i < len(tokens) and tokens[i][0] not in ("|", ")"):
        node, i = _parse_atom(tokens, i)
        items.append(node)
    return (("seq", tuple(items)) if len(items) != 1 else items[0]), i


def _parse_atom(tokens, i):
    kind, value = tokens[i]
    if kind == "(":
        node, i = _parse_expr(tokens, i + 1)
        assert tokens[i][0] == ")"
        i += 1
    elif kind == "lit":
        node, i = ("lit", value), i + 1
    elif kind == "classbody":
        node, i = _parse_class(value), i + 1
    elif kind == "ref":
        node, i = ("ref", value), i + 1
    else:
        raise AssertionError(f"unexpected token {kind!r}")
    while i < len(tokens) and tokens[i][0] in ("*", "?", "bound"):
        kind2, value2 = tokens[i]
        if kind2 == "*":
            node = ("rep", node, 0, None)
        elif kind2 == "?":
            node = ("rep", node, 0, 1)
        else:
            lo, sep, hi = value2.partition(",")
            if not sep:  # "{m}" — exactly m
                node = ("rep", node, int(lo), int(lo))
            else:  # "{m,n}" — m..n; "{m,}" — m..unbounded (hi None)
                node = ("rep", node, int(lo), int(hi) if hi else None)
        i += 1
    return node, i


def _match(node, text: str, pos: int, rules) -> set[int]:
    kind = node[0]
    if kind == "lit":
        return {pos + len(node[1])} if text.startswith(node[1], pos) else set()
    if kind == "class":
        if pos >= len(text):
            return set()
        inside = any(lo <= text[pos] <= hi for lo, hi in node[2])
        return {pos + 1} if inside != node[1] else set()
    if kind == "ref":
        return _match(rules[node[1]], text, pos, rules)
    if kind == "seq":
        positions = {pos}
        for item in node[1]:
            positions = set().union(
                *(_match(item, text, p, rules) for p in positions)
            ) if positions else set()
        return positions
    if kind == "alt":
        return set().union(*(_match(branch, text, pos, rules) for branch in node[1]))
    if kind == "rep":
        _, inner, lo, hi = node
        results: set[int] = set()
        seen: set[int] = set()
        frontier = {pos}
        count = 0
        while True:
            if count >= lo:
                results |= frontier
            if hi is not None and count == hi:
                break
            advanced = set().union(
                *(_match(inner, text, p, rules) for p in frontier)
            ) if frontier else set()
            advanced -= seen
            seen |= advanced
            if not advanced:
                break
            frontier = advanced
            count += 1
        return results
    raise AssertionError(f"unknown node kind {kind!r}")


def gbnf_accepts(grammar_text: str, candidate: str) -> bool:
    """True iff ``candidate`` is in the language of ``grammar_text``'s ``root``."""
    rules = {}
    for line in grammar_text.splitlines():
        if line.strip():
            name, production = line.split("::=", 1)
            rules[name.strip()] = _parse_expr(_tokenize(production), 0)[0]
    return len(candidate) in _match(rules["root"], candidate, 0, rules)


CONFORMING_EMISSION = {
    "dialogue": 'Arr, "welcome" aboard.\n',
    "mood": "happy",
    "tags": ["a", "b"],
    "meta": {"turn": -3},
}


def test_conforming_emissions_are_in_the_grammars_language():
    text = grammar(*REPRESENTATIVE_FIELDS)
    compact = json.dumps(CONFORMING_EMISSION, separators=(",", ":"))
    spaced = json.dumps(CONFORMING_EMISSION, separators=(", ", ": "))
    assert gbnf_accepts(text, compact)
    assert gbnf_accepts(text, spaced)  # bounded structural whitespace admitted


def test_nonconforming_emissions_are_outside_the_grammars_language():
    text = grammar(*REPRESENTATIVE_FIELDS)
    compact = json.dumps(CONFORMING_EMISSION, separators=(",", ":"))
    # The demonstrated suite-survivor mutation: ';' as the member separator.
    assert not gbnf_accepts(text, compact.replace(",", ";"))
    # An enum member outside the closed set.
    assert not gbnf_accepts(text, compact.replace('"happy"', '"angry"'))
    # Keys out of declaration order (the grammar fixes one key order).
    reordered = json.dumps(
        {k: CONFORMING_EMISSION[k] for k in ("mood", "dialogue", "tags", "meta")},
        separators=(",", ":"),
    )
    assert not gbnf_accepts(text, reordered)
    # A structural gap wider than the bounded ws rule admits (>20 chars).
    assert not gbnf_accepts(text, compact.replace(":", ":" + " " * 21, 1))
