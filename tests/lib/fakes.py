"""Recording fakes AT the trainable adapters' wire seam — contract-satisfying
deterministic stand-ins for the serving runtimes (never a function patch, never a
network call).

Each fake implements the adapters' transport-callable protocol
(``(url, body_bytes, headers, timeout_s) -> (status, body_bytes)``) and occupies the
B2 lazy-client seam (a test pre-memoizes it as the adapter's instance-state client —
the same slot the real ``urllib`` client fills on first ``invoke()``).

**The standing double rule: a fake must fail where the runtime would.** Both fakes
validate every request the way the real server does — wrong path → 404; non-JSON
body, a malformed strict ``response_format`` envelope (OpenAI-compatible), a missing
or ill-formed GBNF ``grammar`` (llama-server) → HTTP 400 with an error body — so a
malformed constraint payload can never pass silently through a green test.
"""

from __future__ import annotations

import json
import re

_RULE_NAME_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9-]*\Z")


# ---------------------------------------------------------------------------
# GBNF structural well-formedness — the check llama-server runs at request time
# ---------------------------------------------------------------------------


def _idents_outside_literals(production: str) -> list[str]:
    """Identifier tokens of a GBNF production outside string literals and char
    classes — the rule references the grammar must define."""
    tokens: list[str] = []
    current = ""
    in_literal = False
    in_class = False
    escaped = False
    for ch in production:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if in_literal:
            if ch == '"':
                in_literal = False
            continue
        if in_class:
            if ch == "]":
                in_class = False
            continue
        if ch == '"':
            in_literal = True
        elif ch == "[":
            in_class = True
        elif ch.isalnum() or ch == "-":
            current += ch
            continue
        if current:
            tokens.append(current)
            current = ""
    if current:
        tokens.append(current)
    return [t for t in tokens if t and t[0].isalpha()]


def check_gbnf(grammar: str) -> list[str]:
    """Structural well-formedness problems of a GBNF grammar text (empty = sound):
    every line a ``name ::= production`` rule, names well-formed and unique, ``root``
    present, every referenced rule defined."""
    problems: list[str] = []
    rules: dict[str, str] = {}
    for line in grammar.splitlines():
        line = line.strip()
        if not line:
            continue
        if "::=" not in line:
            problems.append(f"not a rule line: {line!r}")
            continue
        name, production = line.split("::=", 1)
        name = name.strip()
        if not _RULE_NAME_RE.match(name):
            problems.append(f"ill-formed rule name: {name!r}")
            continue
        if name in rules:
            problems.append(f"duplicate rule: {name!r}")
            continue
        rules[name] = production
    if "root" not in rules:
        problems.append("no 'root' rule")
    for name, production in rules.items():
        for ident in _idents_outside_literals(production):
            if ident not in rules:
                problems.append(f"rule '{name}' references undefined '{ident}'")
    return problems


# ---------------------------------------------------------------------------
# The OpenAI-compatible strict server fake
# ---------------------------------------------------------------------------


def _strict_schema_problems(node: object, path: str = "<root>") -> list[str]:
    """Strict-mode schema validation the way a real strict structured-output server
    rejects: every object level closed (``additionalProperties: false``) with every
    property required; no open-keyed objects; no ``prefixItems``."""
    if not isinstance(node, dict):
        return [f"{path}: schema node is not an object"]
    problems: list[str] = []
    if "enum" in node:
        return problems
    if "anyOf" in node:
        for i, member in enumerate(node["anyOf"]):
            problems += _strict_schema_problems(member, f"{path}.anyOf[{i}]")
        return problems
    node_type = node.get("type")
    if node_type == "object":
        if "properties" not in node:
            problems.append(f"{path}: open-keyed object (no properties)")
            return problems
        if node.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties must be false")
        if sorted(node.get("required", [])) != sorted(node["properties"]):
            problems.append(f"{path}: required must list every property")
        for key, member in node["properties"].items():
            problems += _strict_schema_problems(member, f"{path}.{key}")
        return problems
    if node_type == "array":
        if "prefixItems" in node:
            problems.append(f"{path}: prefixItems is not supported in strict mode")
            return problems
        problems += _strict_schema_problems(node.get("items"), f"{path}[]")
        return problems
    if node_type in ("string", "integer", "number", "boolean", "null"):
        return problems
    problems.append(f"{path}: unsupported schema node {node!r}")
    return problems


class FakeOpenAICompatibleServer:
    """A strict OpenAI-compatible ``/chat/completions`` server at the wire seam.

    ``emission`` is the conforming object the constrained decode would produce
    (JSON-encoded into ``message.content``). ``mode`` selects a scripted runtime
    behavior: ``"ok"`` (default), ``"http_500"``, ``"non_json_body"``,
    ``"non_object_body"``, ``"no_choices"``, ``"choices_not_array"``,
    ``"choice_not_object"``, ``"message_not_object"``, ``"no_content"``,
    ``"content_not_text"``, ``"refusal"``, ``"length"`` (truncation),
    ``"non_json_content"``.
    """

    def __init__(self, emission: object, *, mode: str = "ok") -> None:
        self.emission = emission
        self.mode = mode
        self.requests: list[dict] = []

    def __call__(self, url, body, headers, timeout_s):
        try:
            parsed = json.loads(body)
        except ValueError:
            return 400, b'{"error": "request body is not JSON"}'
        self.requests.append(
            {
                "url": url,
                "body": parsed,
                "headers": dict(headers),
                "timeout_s": timeout_s,
            }
        )
        if not url.endswith("/chat/completions"):
            # The route check a real server runs before any body validation.
            return 404, json.dumps({"error": f"unknown path: {url}"}).encode("utf-8")
        problems = self._request_problems(parsed)
        if problems:
            return 400, json.dumps({"error": problems}).encode("utf-8")
        if self.mode == "http_500":
            return 500, b'{"error": "internal server error"}'
        if self.mode == "http_201":
            # A success-FAMILY status that is not the wire's documented 200, carrying a
            # fully well-formed body — the exact-status discriminator (ADAPTERS-5): the
            # floor's expect_success must reject it; a status >= 400 (or 2xx-range)
            # weakening stays green on every other mode but goes RED on this one.
            return 201, json.dumps(
                {"choices": [{"message": {"role": "assistant",
                                          "content": json.dumps(self.emission)},
                              "finish_reason": "stop"}]}
            ).encode("utf-8")
        if self.mode == "non_json_body":
            return 200, b"<html>gateway said what</html>"
        if self.mode == "non_object_body":
            return 200, b'[1, 2, 3]'
        if self.mode == "no_choices":
            return 200, b'{"choices": []}'
        if self.mode == "choices_not_array":
            return 200, b'{"choices": "x"}'
        if self.mode == "choice_not_object":
            return 200, b'{"choices": [42]}'
        if self.mode == "message_not_object":
            return 200, b'{"choices": [{"message": "hi", "finish_reason": "stop"}]}'
        if self.mode == "content_not_text":
            return 200, json.dumps(
                {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": 42},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ).encode("utf-8")
        body_out: dict
        if self.mode == "no_content":
            body_out = {
                "choices": [{"message": {"role": "assistant"}, "finish_reason": "stop"}]
            }
        elif self.mode == "refusal":
            body_out = {
                "choices": [
                    {
                        "message": {"role": "assistant", "refusal": "I can't do that."},
                        "finish_reason": "stop",
                    }
                ]
            }
        elif self.mode == "length":
            body_out = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(self.emission)[:5],
                        },
                        "finish_reason": "length",
                    }
                ]
            }
        elif self.mode == "non_json_content":
            body_out = {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "not json at all"},
                        "finish_reason": "stop",
                    }
                ]
            }
        elif self.mode == "no_finish_buffered":
            # A syntactically-complete body whose choice carries NO finish_reason — the
            # buffered analogue of the streaming "no_finish" mode: the wire never said
            # the emission completed, so the adapter must reject (LIB-2).
            body_out = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(self.emission),
                        },
                    }
                ]
            }
        else:
            body_out = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(self.emission),
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        return 200, json.dumps(body_out).encode("utf-8")

    @staticmethod
    def _request_problems(body: dict) -> list[str]:
        problems: list[str] = []
        if not isinstance(body.get("model"), str) or not body.get("model"):
            problems.append("missing 'model'")
        messages = body.get("messages")
        if (
            not isinstance(messages, list)
            or not messages
            or not all(
                isinstance(m, dict) and m.get("role") and isinstance(m.get("content"), str)
                for m in messages
            )
        ):
            problems.append("malformed 'messages'")
        response_format = body.get("response_format")
        if response_format is not None:
            if response_format.get("type") != "json_schema":
                problems.append("response_format.type must be 'json_schema'")
            else:
                envelope = response_format.get("json_schema")
                if not isinstance(envelope, dict):
                    problems.append("response_format.json_schema missing")
                else:
                    if not isinstance(envelope.get("name"), str):
                        problems.append("json_schema.name missing")
                    if envelope.get("strict") is not True:
                        problems.append("json_schema.strict must be true")
                    problems += _strict_schema_problems(envelope.get("schema"))
        return problems


# ---------------------------------------------------------------------------
# The llama-server fake
# ---------------------------------------------------------------------------


class FakeLlamaServer:
    """A llama.cpp ``llama-server`` ``/completion`` endpoint at the wire seam.

    ``emission`` is the conforming object the grammar-constrained decode would
    produce (JSON-encoded into ``content``). ``mode``: ``"ok"`` (default),
    ``"http_500"``, ``"non_json_body"``, ``"non_object_body"``, ``"no_content"``,
    ``"content_not_text"``, ``"truncated"``, ``"non_json_content"``.
    """

    def __init__(self, emission: object, *, mode: str = "ok") -> None:
        self.emission = emission
        self.mode = mode
        self.requests: list[dict] = []

    def __call__(self, url, body, headers, timeout_s):
        try:
            parsed = json.loads(body)
        except ValueError:
            return 400, b'{"error": "request body is not JSON"}'
        self.requests.append(
            {
                "url": url,
                "body": parsed,
                "headers": dict(headers),
                "timeout_s": timeout_s,
            }
        )
        if not url.endswith("/completion"):
            # The route check a real server runs before any body validation.
            return 404, json.dumps({"error": f"unknown path: {url}"}).encode("utf-8")
        problems: list[str] = []
        if not isinstance(parsed.get("prompt"), str):
            problems.append("missing 'prompt'")
        grammar = parsed.get("grammar")
        if not isinstance(grammar, str) or not grammar.strip():
            problems.append("missing 'grammar'")
        else:
            problems += check_gbnf(grammar)  # llama-server rejects a bad grammar
        if problems:
            return 400, json.dumps({"error": problems}).encode("utf-8")
        if self.mode == "http_500":
            return 500, b'{"error": "internal server error"}'
        if self.mode == "non_json_body":
            return 200, b"<html>gateway said what</html>"
        if self.mode == "non_object_body":
            return 200, b'[1, 2, 3]'
        if self.mode == "no_content":
            return 200, b'{"tokens_predicted": 0}'
        if self.mode == "content_not_text":
            return 200, b'{"content": 42}'
        if self.mode == "truncated":
            return 200, json.dumps(
                {"content": json.dumps(self.emission)[:5], "truncated": True}
            ).encode("utf-8")
        if self.mode == "non_json_content":
            return 200, b'{"content": "not json at all"}'
        return 200, json.dumps({"content": json.dumps(self.emission)}).encode("utf-8")


class FakeOpenAICompatibleStreamingServer:
    """The streaming sibling of :class:`FakeOpenAICompatibleServer` — a strict
    OpenAI-compatible ``/chat/completions`` SSE stream at the STREAMING wire seam
    (the ``_streaming_transport`` injection point: ``(url, body, headers, timeout_s)
    -> (status, line_iterator)``).

    ``emission`` is the conforming object the constrained decode would produce; its
    JSON text is split into ``fragment_count`` content-delta chunks. ``mode`` selects
    a scripted runtime behavior: ``"ok"`` (default), ``"http_500"``, ``"refusal"``,
    ``"length"`` (truncation finish_reason), ``"no_finish"`` (stream ends without a
    finish_reason), ``"non_json_chunk"``, ``"empty"`` (a finish-only stream carrying
    no content deltas), ``"content_not_text"``, ``"non_json_assembled"`` (fragments
    that do not assemble to JSON). The fake fails where the runtime would.
    """

    def __init__(self, emission: object, *, mode: str = "ok",
                 fragment_count: int = 3) -> None:
        self.emission = emission
        self.mode = mode
        self.fragment_count = fragment_count
        self.requests: list[dict] = []

    @staticmethod
    def _chunk(delta: dict, finish_reason=None) -> bytes:
        return b"data: " + json.dumps(
            {"choices": [{"delta": delta, "finish_reason": finish_reason}]}
        ).encode("utf-8") + b"\n"

    def sse_lines(self) -> list[bytes]:
        if self.mode == "refusal":
            return [self._chunk({"refusal": "cannot comply"}), b"\n",
                    b"data: [DONE]\n", b"\n"]
        if self.mode == "non_json_chunk":
            return [b"data: {not json\n", b"\n", b"data: [DONE]\n", b"\n"]
        if self.mode == "empty":
            return [self._chunk({}, "stop"), b"\n", b"data: [DONE]\n", b"\n"]
        if self.mode == "content_not_text":
            return [self._chunk({"content": 42}), b"\n", b"data: [DONE]\n", b"\n"]
        text = ("not json at all" if self.mode == "non_json_assembled"
                else json.dumps(self.emission))
        size = max(1, len(text) // self.fragment_count)
        parts = [text[i:i + size] for i in range(0, len(text), size)]
        lines: list[bytes] = []
        for part in parts[:-1]:
            lines += [self._chunk({"content": part}), b"\n"]
        final_finish = {"length": "length", "no_finish": None}.get(self.mode, "stop")
        lines += [self._chunk({"content": parts[-1]}, final_finish), b"\n"]
        lines += [b"data: [DONE]\n", b"\n"]
        return lines

    def __call__(self, url, body, headers, timeout_s):
        self.requests.append(
            {"url": url, "body": json.loads(body), "headers": dict(headers),
             "timeout_s": timeout_s}
        )
        if self.mode == "http_500":
            return 500, iter([b'{"error": "internal server error"}'])
        return 200, iter(self.sse_lines())
