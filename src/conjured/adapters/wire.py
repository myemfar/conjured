"""Wire-form rendering for trainable-backend adapters — the engine-owned half of the
literal-equal seal (R-handler-005; ``conjured/docs/components/handler/reference.md``
§ The service-type adapter / § Trainable backends).

Two render surfaces, single-homed here so both native wire forms (and any
consumer-supplied tail adapter) consume one canonicalization rather than re-deriving it
per adapter:

- :func:`render_output_constraint` — the declared ``trainable.output_schema``
  (``tuple[FieldDecl, ...]``, the literal compose-fixed artifact) → the **canonical
  strict JSON Schema** constraint: an object root, every property required, every
  object level closed with ``additionalProperties: false``, field ``description``
  strings carried (they are load-bearing for trainables — R-handler-005's metadata
  note: descriptions feed the backend's structured-output prompt). The rendering is
  deterministic (declaration order preserved; no environment-dependent content), so the
  constraint submitted at inference is byte-stable against the constraint a training
  pass derives from the same declaration.

  **The compose-time caveat fires here — the accepted matrix** (§ Trainable backends):
  the renderer takes the bound wire family's certified ``accepted_keywords`` set. A
  field's bare standard keyword **in** the set RENDERS into the property's node (the seal
  stays literal-equal *including* the keyword — the engine model enforces the same
  predicate); a bare keyword **out of** the set, or any **namespaced (dotted)** validator
  key (opaque third-party code, never render-eligible), is REJECTED at compose naming the
  keyword + the wire. A ``bytes`` channel (no JSON wire rendering) and a fixed-arity
  ``tuple`` channel (a JSON wire delivers arrays, which the engine's strict generated
  models reject against a declared tuple — the seal cannot close end-to-end) reject on
  every JSON wire regardless of the accepted set. All of these raise
  :class:`~conjured.errors.ContractViolation` (``TRAINABLE_CONSTRAINT_UNSUPPORTED``,
  R-handler-005) at render, which the native adapters run at **construction = compose
  time**: an honest failure, not a silent best-effort. (A ``LiteralType`` closed enum is
  NOT a constraint keyword — it is a type token an enum decode grammar enforces natively;
  the accepted-matrix `enum` *keyword* is the separate value-membership constraint.)

- :func:`render_input_payload` — the per-dispatch ``input_payload`` (the
  ``trainable.reads`` projection) → the wire text. The rule is an explicit two-case
  split, both branches content-neutral (the property-4 clean seal: the adapter submits
  the reads and does nothing else):

  - exactly one declared port AND its value is a ``str`` → the **bare string,
    verbatim** (the dominant assembled-prompt case — the upstream preprocessor owns
    prompt shaping per R-handler-011; wrapping the assembled prompt in JSON would be
    the adapter adding shape);
  - anything else → the **canonical JSON rendering** of the full reads dict — the
    hasher's shared canonical serializer (:func:`conjured.canonical.canonical_json`:
    key-sorted, compact separators, ``ensure_ascii=False``; one recipe, never
    re-derived) — deterministic, so a training corpus serialized under this rule
    matches what the backend sees at inference byte-for-byte.

  A reads value outside JSON's value space (e.g. ``bytes``) raises the underlying
  ``TypeError`` raw and loud — binary content in training-aware pipelines rides
  path/hash references by the canonical authoring default (handler/reference.md
  § Channel-type discipline), never inline bytes into a prompt.

The wire floor also single-homes the pieces both native adapters share:
:class:`TrainableWireError` (the structured backend-protocol failure),
:func:`urllib_transport` (the default stdlib wire client both adapters memoize on
first ``invoke()``), and the streaming siblings — :func:`urllib_streaming_transport`
(the chunked-read client a streaming-capable adapter memoizes on first
``invoke_streaming()``) plus :func:`iter_sse_data` (SSE ``data:``-line payload
extraction, the framing every covered streaming wire speaks).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import MappingProxyType
from typing import Iterator, Mapping, cast

from conjured.errors import Check, ContractViolation
from conjured.canonical import canonical_json
from conjured.ir.channel_types import (
    ChannelFieldType,
    DictType,
    FieldDecl,
    ListType,
    LiteralType,
    NestedType,
    OptionalType,
    Primitive,
    PrimitiveType,
    TupleType,
)


class TrainableWireError(RuntimeError):
    """A backend-protocol failure on the trainable wire — an HTTP error status, a
    refusal payload, a truncated or missing emission, an unparseable or shape-alien
    response body.

    This is a **runtime failure**, not a validation verdict: the structural seal for a
    parseable-but-nonconforming emission is the engine's output validation
    (R-handler-005 — ``SchemaValidationError`` / ``ContractViolation`` per the
    three-way routing), and the adapter never masks a wire failure with a schema-valid
    value (R-handler-002's fallback catalog; in Conjured graceful degrade is
    training-data corruption). The exception rides **raw** through the Phase-2 dispatch
    surface; the Phase-3 runner's boundary wraps it as ``PipelineFailure`` with
    ``cause_class`` (the same contract as ``FieldValidatorFailure`` — errors.py module
    docstring: ``PipelineFailure`` is constructed only by the runner's dispatch-boundary
    wrap)."""


def urllib_transport(
    url: str, body: bytes, headers: dict, timeout_s: "float | None"
) -> tuple[int, bytes]:
    """The default wire client both native trainable adapters memoize — stdlib
    ``urllib`` POST returning ``(status, body)``. An HTTP error status returns **as
    data** (the adapter raises the structured wire error on a non-200 status, never a
    raw ``HTTPError``); a transport-level failure (connection refused, DNS) rides raw.
    Single-homed here (the shared wire floor) so the two adapters import one client
    rather than re-deriving it per module."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def urllib_streaming_transport(
    url: str, body: bytes, headers: dict, timeout_s: "float | None"
) -> tuple[int, Iterator[bytes]]:
    """The streaming sibling of :func:`urllib_transport` — stdlib ``urllib`` POST
    returning ``(status, line_iterator)``: raw response lines yielded as they arrive
    (the response stays open across the iteration and closes on exhaustion or
    generator close). An HTTP error status returns **as data** — ``(code, an
    iterator over the buffered error body)`` — so the adapter raises the structured
    wire error exactly as on the buffered client; a transport-level failure
    (connection refused, DNS) rides raw. Memoized into the streaming instance
    attribute on the first ``invoke_streaming()`` — that attribute is the injection
    seam (tests occupy it with a fake yielding scripted SSE lines)."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        response = urllib.request.urlopen(request, timeout=timeout_s)
    except urllib.error.HTTPError as exc:
        return exc.code, iter((exc.read(),))

    def _lines() -> Iterator[bytes]:
        with response:
            yield from response

    return response.status, _lines()


# ---------------------------------------------------------------------------
# The shared HTTP protocol floor — one recipe for every HTTP-speaking blessed
# member (the canon owner of the conventions realized here is native-library
# reference § HTTP-speaking member conventions): endpoint/bearer/timeout
# preparation and the fail-loud response guards. Engine natives raise
# TrainableWireError; companion-package service members pass their own wire-error
# class — the LOGIC is single-homed, the error class and the per-member message
# prose ride as parameters.
# ---------------------------------------------------------------------------


def bearer_headers(api_key: "str | None", base: Mapping[str, str]) -> dict:
    """The shared request headers — ``base`` plus an ``Authorization: Bearer`` line when
    ``api_key`` is supplied. ``api_key`` is the RESOLVED bare credential (the caller resolves
    its ``api_key_ref`` secret reference via
    :func:`conjured.adapters.secret_refs.resolve_secret_ref` first; ``None`` is the
    unauthenticated no-credential state — no header emitted)."""
    headers = dict(base)
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def timeout_seconds(timeout_ms: "int | None") -> "float | None":
    """Convert the ``timeout_ms`` transport value (the per-call timeout the member applies;
    an engineering-hygiene mandatory timeout) to ``urlopen``'s seconds; ``None`` waits on
    the serving runtime."""
    return timeout_ms / 1000 if timeout_ms is not None else None


def prepare_json_transport(
    transport_extra: Mapping[str, object], *,
    error: type[Exception], missing_endpoint: str,
    remaining_budget_ms: "int | None" = None,
) -> "tuple[str, dict, float | None]":
    """The shared transport preparation for a JSON-speaking member: the endpoint guard
    (no hosted default exists to fall back to — ``missing_endpoint`` is the member's
    message), the resolved-``api_key_ref`` bearer headers, and the ``timeout_ms``
    conversion. Returns ``(endpoint, headers, timeout_s)``; the member appends only its
    wire's route to the endpoint.

    ``remaining_budget_ms`` is the engine-supplied deadline-propagation budget
    (service-type/reference.md § Deadline propagation) — the ONE fold point for the
    participating natives: the effective per-call timeout is ``min(the transport
    ``timeout_ms``, the remaining budget)``; an exhausted budget (zero) yields a zero
    timeout the wire client fails immediately — zero is a floor, not an unbounded
    sentinel."""
    from conjured.adapters.secret_refs import resolve_secret_ref

    endpoint = transport_extra.get("endpoint")
    if not endpoint:
        raise error(missing_endpoint)
    # cast: TYPING-ONLY, not a verified engine guarantee. The service transport block is
    # key-checked at compose (presence + no-unknown-fields) and its VALUES pass through
    # opaque — the engine reads only the two reserved shapes (explicit-null; the
    # secret-ref grammar on api_key_ref, which IS shape-guaranteed) — so a mistyped
    # value (timeout_ms = "30000") reaches here as supplied and fails loud at use
    # (deployment reference § transport.<name>; the type-match arm exists for hook
    # transport only).
    headers = bearer_headers(
        resolve_secret_ref(cast("str | None", transport_extra.get("api_key_ref"))),
        {"Content-Type": "application/json"},
    )
    timeout_ms = cast("int | None", transport_extra.get("timeout_ms"))
    timeout_s = timeout_seconds(timeout_ms)
    if remaining_budget_ms is not None:
        budget_s = remaining_budget_ms / 1000.0
        timeout_s = budget_s if timeout_s is None else min(timeout_s, budget_s)
    return str(endpoint), headers, timeout_s


def expect_success(
    status: int, payload: bytes, *, service_name: str,
    error: type[Exception], expected_status: int = 200,
) -> bytes:
    """The shared wire-success guard: the raw body for **exactly** the wire's documented
    success status (native-library reference § HTTP-speaking member conventions — any
    other status, other ``2xx`` included, is a wire failure raised raw, never retried,
    never substituted)."""
    if status != expected_status:
        raise error(
            f"'{service_name}' backend returned HTTP {status}: "
            f"{payload[:512]!r} (no retry — the wire failure surfaces raw)"
        )
    return payload


def parse_json_object_response(
    status: int, payload: bytes, *, service_name: str,
    error: type[Exception], expected_status: int = 200,
) -> dict:
    """The shared fail-loud response floor: :func:`expect_success`, then the JSON parse
    and object-shape guards — a parseable body outside the wire's response shape raises
    the member's structured wire error naming the offending shape, never a raw
    ``AttributeError``/``TypeError`` from the access path. Per-wire field extraction
    (what the object must CONTAIN) stays with the member."""
    expect_success(
        status, payload, service_name=service_name, error=error,
        expected_status=expected_status,
    )
    try:
        response = json.loads(payload)
    except ValueError as exc:
        raise error(
            f"'{service_name}' backend response body is not JSON: {payload[:512]!r}"
        ) from exc
    if not isinstance(response, dict):
        raise error(
            f"'{service_name}' backend response body is not a JSON object: "
            f"{type(response).__name__} {payload[:512]!r}"
        )
    return response


def iter_sse_data(lines: Iterator[bytes]) -> Iterator[str]:
    """SSE ``data:`` payload extraction over raw wire lines — the framing the
    OpenAI-compatible streaming wire speaks (one ``data: {json}`` line per chunk,
    blank-line separated, ``data: [DONE]`` terminal). Yields each payload string;
    returns on the ``[DONE]`` sentinel or line exhaustion. Comment lines (``:``)
    and blank lines are SSE keep-alive framing, skipped; a non-UTF-8 line raises
    the underlying ``UnicodeDecodeError`` raw and loud (the wire promised text).
    Multi-line ``data:`` continuation is not consumed by the covered wires' chunk
    framing (one JSON object per line) and is not joined here."""
    for raw in lines:
        line = raw.decode("utf-8").strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            payload = line[len("data:"):].lstrip()
            if payload == "[DONE]":
                return
            yield payload


#: JSON Schema type names for the JSON-expressible primitives. ``bytes`` is absent by
#: design — it has no JSON wire rendering (rejected at render, the compose-time caveat).
#: Immutable by construction (no module-level mutable state — R-handler-pure-module).
_PRIMITIVE_JSON_TYPES: Mapping[Primitive, str] = MappingProxyType(
    {
        Primitive.STR: "string",
        Primitive.INT: "integer",
        Primitive.FLOAT: "number",
        Primitive.BOOL: "boolean",
    }
)


def _unsupported(
    *, what: str, field_path: str, schema_source: str, section_path: str
) -> ContractViolation:
    return ContractViolation(
        check=Check.TRAINABLE_CONSTRAINT_UNSUPPORTED,
        rule_id="R-handler-005",
        expected=(
            "every declared field of a trainable output schema is expressible as a "
            "server-enforced decode constraint (the literal-equal seal)"
        ),
        actual=f"{what} at '{field_path}'",
        remediation_hint=(
            "move the value constraint to a downstream transform (the trainable emits "
            "the channel literally), or re-shape the channel within the "
            "grammar-convertible subset — the engine refuses a best-effort seal"
        ),
        file_path=schema_source,
        section_path=section_path,
    )


#: The render arm for each renderable bare standard keyword: the JSON Schema keyword its
#: ``ValidatorSpec`` becomes + the param name carrying the value (``constraints.DIRECT_KEY_PARAM``).
#: ``enum`` renders on every wire whose accepted set admits it (JSON-Schema ``enum`` /
#: GBNF alternation); ``minLength`` / ``maxLength`` render on the GBNF wire (string
#: repetition). A keyword with no arm here cannot be rendered (the accepted set MUST stay a
#: subset of these keys — the certification gate is the author-facing guard; this assert is
#: the engine-internal drift backstop).
_RENDER_ARMS: Mapping[str, str] = MappingProxyType(
    {"enum": "enum", "minLength": "minLength", "maxLength": "maxLength"}
)


def _merge_keyword(node: dict, spec) -> None:
    """Merge one accepted bare standard constraint keyword into its field's JSON Schema
    node — the rendered keyword the submitted constraint carries, so the seal stays
    literal-equal (the engine-side model enforces the same predicate). A constraint
    applies to the present, non-null value, so for an ``OptionalType`` (rendered as
    ``anyOf [<T>, null]``) the keyword merges into the non-null branch."""
    target = node["anyOf"][0] if "anyOf" in node else node
    json_key = _RENDER_ARMS.get(spec.name)
    if json_key is None:  # pragma: no cover - accepted set is a subset of _RENDER_ARMS
        raise AssertionError(
            f"accepted keyword '{spec.name}' has no render arm — the wire family's "
            "accepted set must stay a subset of the renderable keywords"
        )
    if spec.name == "enum":
        target[json_key] = list(spec.params["values"])
    else:  # minLength / maxLength — the direct-key param is `limit`
        target[json_key] = spec.params["limit"]


def _type_schema(
    t: ChannelFieldType,
    *,
    field_path: str,
    schema_source: str,
    section_path: str,
    accepted_keywords: frozenset[str],
    wire: str,
) -> dict:
    """One declared type → its canonical JSON Schema node (deterministic; declaration
    order preserved for nested members). ``accepted_keywords`` / ``wire`` thread to
    nested object levels, whose member fields carry their own constraint keywords."""
    if isinstance(t, PrimitiveType):
        json_type = _PRIMITIVE_JSON_TYPES.get(t.primitive)
        if json_type is None:
            raise _unsupported(
                what=f"a '{t.primitive.value}' channel (no JSON wire rendering)",
                field_path=field_path,
                schema_source=schema_source,
                section_path=section_path,
            )
        return {"type": json_type}
    if isinstance(t, LiteralType):
        return {"enum": list(t.values)}
    if isinstance(t, OptionalType):
        inner = _type_schema(
            t.inner,
            field_path=field_path,
            schema_source=schema_source,
            section_path=section_path,
            accepted_keywords=accepted_keywords,
            wire=wire,
        )
        return {"anyOf": [inner, {"type": "null"}]}
    if isinstance(t, ListType):
        return {
            "type": "array",
            "items": _type_schema(
                t.item,
                field_path=f"{field_path}[]",
                schema_source=schema_source,
                section_path=section_path,
                accepted_keywords=accepted_keywords,
                wire=wire,
            ),
        }
    if isinstance(t, TupleType):
        # A JSON wire has no tuple value: the emission arrives as a JSON array
        # (a Python list), which the engine's strict generated model rejects against
        # a declared tuple port ("value-type fidelity" — model_gen). The seal cannot
        # close end-to-end, so the declaration is rejected at compose rather than
        # failing every dispatch at validation.
        raise _unsupported(
            what="a fixed-arity tuple channel (a JSON wire delivers arrays, which "
                 "strict validation rejects against a declared tuple — the seal "
                 "cannot close; re-shape as a nested object or a list)",
            field_path=field_path,
            schema_source=schema_source,
            section_path=section_path,
        )
    if isinstance(t, DictType):
        return {
            "type": "object",
            "additionalProperties": _type_schema(
                t.value,
                field_path=f"{field_path}[*]",
                schema_source=schema_source,
                section_path=section_path,
                accepted_keywords=accepted_keywords,
                wire=wire,
            ),
        }
    if isinstance(t, NestedType):
        return _object_schema(
            t.fields,
            field_path=field_path,
            schema_source=schema_source,
            section_path=section_path,
            accepted_keywords=accepted_keywords,
            wire=wire,
        )
    raise TypeError(  # pragma: no cover - ChannelFieldType is a closed union
        f"unknown channel-field type at '{field_path}'"
    )


def _object_schema(
    fields: tuple[FieldDecl, ...],
    *,
    field_path: str,
    schema_source: str,
    section_path: str,
    accepted_keywords: frozenset[str],
    wire: str,
) -> dict:
    """A closed object level: every declared member a property, every property
    required, ``additionalProperties: false`` (the strict shape — no implicit
    contracts at any nesting level, mirroring I1).

    The accepted-matrix render (§ Trainable backends — the accepted matrix): each member
    field's validation keywords route by class — a **bare** standard keyword **in** the
    bound wire family's ``accepted_keywords`` set RENDERS into the property's node (the
    seal stays literal-equal including the keyword); a bare keyword **out of** the set, or
    any **namespaced (dotted)** validator key (opaque third-party code, never
    render-eligible), is REJECTED at compose naming the keyword + the wire."""
    properties: dict[str, dict] = {}
    for field in fields:
        member_path = f"{field_path}.{field.name}" if field_path else field.name
        node = _type_schema(
            field.type,
            field_path=member_path,
            schema_source=schema_source,
            section_path=section_path,
            accepted_keywords=accepted_keywords,
            wire=wire,
        )
        for spec in field.validators:
            if "." in spec.name:
                raise _unsupported(
                    what=(
                        f"the namespaced (dotted) validator key '{spec.name}' — opaque "
                        f"third-party code, never render-eligible on the {wire} wire"
                    ),
                    field_path=member_path,
                    schema_source=schema_source,
                    section_path=section_path,
                )
            if spec.name not in accepted_keywords:
                raise _unsupported(
                    what=(
                        f"the validation keyword '{spec.name}' (outside the {wire} wire's "
                        f"accepted set {sorted(accepted_keywords)})"
                    ),
                    field_path=member_path,
                    schema_source=schema_source,
                    section_path=section_path,
                )
            _merge_keyword(node, spec)
        if field.description is not None:
            node = {"description": field.description, **node}
        properties[field.name] = node
    return {
        "type": "object",
        "properties": properties,
        "required": [field.name for field in fields],
        "additionalProperties": False,
    }


def render_output_constraint(
    fields: tuple[FieldDecl, ...],
    *,
    schema_source: str,
    section_path: str = "trainable.output_schema",
    accepted_keywords: frozenset[str] = frozenset(),
    wire: str = "this trainable",
) -> dict:
    """The declared output-port shape → the canonical strict JSON Schema constraint
    (the literal-equal artifact's wire rendering). ``accepted_keywords`` is the bound
    wire family's certified **accepted-keyword set** (the accepted matrix): a bare
    standard keyword in the set RENDERS into the submitted constraint; a keyword outside
    it — and every namespaced (dotted) validator key — raises ``ContractViolation``
    (``TRAINABLE_CONSTRAINT_UNSUPPORTED``, R-handler-005) naming the keyword + ``wire``.
    The default empty set rejects every constraint (the honest-failure floor for a caller
    that certifies none). Run by the native adapters at construction, so a rejection is
    compose-time. ``schema_source`` / ``section_path`` locate the contract document the
    diagnostic points at (the trainable composition declaration)."""
    return _object_schema(
        fields,
        field_path="",
        schema_source=schema_source,
        section_path=section_path,
        accepted_keywords=accepted_keywords,
        wire=wire,
    )


def render_input_payload(input_payload: Mapping[str, object]) -> str:
    """The per-dispatch reads projection → the wire text (the two-case split in the
    module docstring): a single str-valued port passes verbatim; everything else is the
    hasher's canonical JSON rendering of the full dict (a non-JSON value raises the
    underlying ``TypeError`` raw and loud)."""
    if len(input_payload) == 1:
        (value,) = input_payload.values()
        if isinstance(value, str):
            return value
    return canonical_json(dict(input_payload))
