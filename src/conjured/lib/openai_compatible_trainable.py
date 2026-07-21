"""``conjured.lib.openai_compatible_trainable`` — the OpenAI-compatible
structured-output trainable backend (the wire-form name says what the adapter
*speaks*, never a model type — slice ruling D3).

The native trainable-backend adapter for self-hosted serving runtimes speaking the
OpenAI-compatible chat-completions wire form (vLLM, llama-server's OpenAI surface,
TGI, SGLang, and kin) — "an OpenAI-compatible structured-output endpoint (covering
most self-hosted servers at once)" per
``conjured/docs/components/handler/reference.md`` § Trainable backends. The sibling
``openai_compatible_trainable.toml`` is the service-type declaration this class
implements (paired by qualified name; one implementation per name,
R-service-type-004).

**The four-property trainable contract, by construction:**

1. **Server-side decode-time seal.** The declared ``trainable.output_schema``
   (engine-supplied at construction as the literal compose-fixed artifact) renders
   once into the canonical strict JSON Schema constraint
   (:func:`conjured.adapters.wire.render_output_constraint`) and every ``invoke()``
   submits it as ``response_format = {"type": "json_schema", …, "strict": true}`` —
   the serving runtime enforces it token-by-token. There is **no client-side
   parse-and-retry path**: a response is parsed once and returned verbatim; a
   non-conforming emission halts in the engine's output validation (R-handler-005).
   Schemas outside the seal's expressible subset — any field ``validators``, a
   ``bytes`` or fixed-arity ``tuple`` channel (JSON-wire-wide, the shared renderer),
   and an open-keyed ``dict[str, T]`` (strict-wire-specific) — are rejected at
   construction (= compose) with ``ContractViolation``: the compose-time caveat's
   honest failure.
2. **Fine-tunable open weights the consumer owns.** The adapter bakes in **no hosted
   endpoint and no endpoint default** — the serving runtime is named exclusively by
   the deployment's ``endpoint`` transport value, and the submitted ``model`` is
   exactly the composed identity. A frozen vendor endpoint never enters this wire
   path by construction.
3. **A standard training-artifact contract.** ``training_artifact_contract =
   "safetensors+peft"`` — the artifact family the covered serving runtimes load
   (merged safetensors plus a PEFT/LoRA adapter).
4. **A clean read/write seal.** ``invoke()`` submits exactly the rendered
   ``input_payload`` (the ``trainable.reads`` projection — one ``user`` message, no
   system prompt, no template, no injected content; prompt shaping is an upstream
   preprocessor's job per R-handler-011) and returns exactly the parsed constrained
   emission. Dispatch metadata (``service_name``, ``caller_*``) never reaches the
   wire.

**Lifecycle (B2 — the construction-lifecycle region).** One instance per composition,
constructed at compose with the compose-fixed identity (``model``) plus the
engine-supplied declared output shape (``output_schema`` — ``tuple[FieldDecl, ...]``)
and its declaration path (``schema_source``, the diagnostics locus). Everything
dynamic arrives per dispatch through ``invoke()``: per-call transport values
(``endpoint`` / ``api_key_ref`` / ``timeout_ms``) ride ``**transport_extra``; the wire
client itself is the shared module-level transport callable
(:func:`conjured.adapters.wire.urllib_transport`), memoized into the instance
attribute on the first ``invoke()`` — that memoized attribute is the injection seam
(tests occupy it with a recording fake). Instance-state caching only — no module- or
class-level mutable state (the vector-7 seal; this module passes its own resolution
audit).

**Wire protocol.** ``POST {endpoint}/chat/completions`` with a JSON body; transport
fields (per-deployment, never hashed): ``endpoint`` (required), ``api_key_ref``
(optional ``Authorization: Bearer`` value — a ``secret_ref``-declared ``[scheme]payload``
secret reference the adapter resolves at dispatch via
:func:`conjured.adapters.secret_refs.resolve_secret_ref`, never a raw
token in the TOML), ``timeout_ms`` (an author-named per-call
transport passthrough this adapter applies — the engine reserves no per-binding
timeout vocabulary). Generation dials (``temperature`` / ``max_tokens``) are the
declared ``[config_schema]`` kwargs, always arriving with a concrete **effective**
value — composition-supplied or the declared ship-time default in the sibling TOML
(the values' only home) — and **always written to the wire body**: there is no
unpinned-omit path; the serving runtime's own defaults never apply. The open
``extras`` table (the ``[config_schema]`` ``table`` field, default ``{}``) is the
sampling tail this dial core does not enumerate (``top_p`` / ``top_k`` /
``repeat_penalty`` / ``seed`` / ``logit_bias`` / …): merged **verbatim** into the wire
body, hash-covered as canonical data, its keys disjoint-by-compose from the reserved
wire keys (the engine's owned keys are written AFTER the merge — an extras key can
never override the checkpoint / seal / dials). Protocol
failures — an HTTP error
status, a refusal payload, a truncated (``finish_reason != "stop"``), missing,
shape-alien, or unparseable emission — raise
:class:`conjured.adapters.wire.TrainableWireError` raw
(the Phase-3 runner wraps it as ``PipelineFailure``); the adapter never retries and
never substitutes a value.
"""

from __future__ import annotations

import json

from conjured.adapters.wire import (
    TrainableWireError,
    iter_sse_data,
    parse_json_object_response,
    prepare_json_transport,
    render_input_payload,
    render_output_constraint,
    urllib_streaming_transport,
    urllib_transport,
)
from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import FieldDecl


def _reject_strict_inexpressible(
    node: dict, *, field_path: str, schema_source: str
) -> None:
    """The strict wire form's expressibility gate (the compose-time caveat,
    R-handler-005): OpenAI-style strict structured outputs close every object level
    (``additionalProperties: false``, all properties required) — an open-keyed
    ``dict[str, T]`` cannot form the seal on this wire and is rejected at construction
    (tuples and ``bytes`` already rejected in the shared renderer for every JSON
    wire)."""
    if node.get("type") == "object" and "properties" not in node:
        raise ContractViolation(
            check=Check.TRAINABLE_CONSTRAINT_UNSUPPORTED, rule_id="R-handler-005",
            expected="every object level of the constraint is closed-keyed (the "
                     "strict structured-output wire form admits no open-keyed "
                     "dict[str, <T>])",
            actual=f"an open-keyed dict at '{field_path or '<root>'}'",
            remediation_hint="re-shape the channel as a closed nested object, or bind "
                             "the gbnf_trainable wire form (its grammar expresses "
                             "open-keyed objects)",
            file_path=schema_source, section_path="trainable.output_schema",
        )
    for key, member in (node.get("properties") or {}).items():
        member_path = f"{field_path}.{key}" if field_path else key
        _reject_strict_inexpressible(
            member, field_path=member_path, schema_source=schema_source
        )
    if isinstance(node.get("items"), dict):
        _reject_strict_inexpressible(
            node["items"], field_path=f"{field_path}[]", schema_source=schema_source
        )
    for member in node.get("anyOf") or ():
        _reject_strict_inexpressible(
            member, field_path=field_path, schema_source=schema_source
        )


class OpenAICompatibleTrainable:
    """The OpenAI-compatible structured-output trainable backend (module docstring)."""

    #: Property 3 — a portable, self-servable artifact contract (this adapter ships merged safetensors + PEFT/LoRA).
    #: The compose-time gate certifies this native **structurally** — by the engine's native
    #: adapter table (native-by-construction), never a self-declared class attribute
    #: (handler/reference.md § Trainable backends). The gate still verifies this property
    #: label (non-empty string) + ``reserved_wire_keys`` against the resolved class.
    training_artifact_contract = "safetensors+peft"
    #: The accepted-matrix certification (handler/reference.md § Trainable backends — the
    #: accepted matrix): the bare standard constraint keywords this wire RENDERS into the
    #: submitted strict json_schema. The strict OpenAI form expresses `enum` as JSON-Schema
    #: `enum`; length/pattern/range keywords are not in this wire's set (a value rule a
    #: trainable can't carry server-side belongs on a downstream reader's reads schema).
    accepted_wire_keywords = frozenset({"enum"})
    #: The reserved wire keys this adapter constructs (native-library/reference.md extras
    #: rider; the certification gate validates this attribute, compose checks an `extras`
    #: table is disjoint from it): the dial core + the structural keys. An `extras` key
    #: naming one is rejected at compose with the key's real home; past compose the two
    #: key-sets are disjoint by construction, and `invoke()` writes its owned keys AFTER the
    #: `**extras` merge so the seal/dials hold even if the compose check had a gap.
    reserved_wire_keys = frozenset(
        {"model", "messages", "temperature", "max_tokens", "response_format", "stream"}
    )

    def __init__(
        self,
        *,
        model: str,
        output_schema: "tuple[FieldDecl, ...]",
        schema_source: str,
    ) -> None:
        # Compose-fixed identity + the literal-equal artifact, fixed once per
        # composition. Constraint derivation IS the compose-time expressibility gate:
        # an inexpressible schema raises ContractViolation here, never at dispatch.
        self.model = model
        self._constraint = render_output_constraint(
            tuple(output_schema), schema_source=schema_source,
            accepted_keywords=self.accepted_wire_keywords,
            wire="OpenAI-compatible (strict json_schema)",
        )
        _reject_strict_inexpressible(
            self._constraint, field_path="", schema_source=schema_source
        )
        # The injection seams: each memoizes its shared module-level wire client on
        # first use (B2 — the constructor has no transport to exercise them with).
        self._transport = None
        self._streaming_transport = None

    def _prepare_request(
        self, *, input_payload, temperature, max_tokens, extras, transport_extra,
        remaining_budget_ms=None,
    ):
        """The shared wire-request preparation both dispatch surfaces submit —
        ``(url, headers, timeout_s, body)``. The ``stream`` key is deliberately NOT
        written here: absence is the wire's non-streaming default, so the buffered
        ``invoke()`` body stays byte-identical to the pre-streaming engine;
        ``invoke_streaming()`` writes its owned ``"stream": true`` after this returns
        (an owned key, written post-merge like every other)."""
        # The shared transport floor (property 2 rides the endpoint guard: no hosted
        # default exists to fall back to — the consumer's serving runtime is named by
        # the deployment or the call fails loud); this wire appends only its route.
        endpoint, headers, timeout_s = prepare_json_transport(
            transport_extra, error=TrainableWireError,
            missing_endpoint="transport supplies no 'endpoint' — the OpenAI-compatible "
                             "trainable wire has no default serving runtime (the "
                             "consumer owns the backend)",
            # Deadline participation (service-type/reference.md § Deadline
            # propagation): the shared floor folds min(transport timeout, budget).
            remaining_budget_ms=remaining_budget_ms,
        )
        url = f"{endpoint.rstrip('/')}/chat/completions"

        # The clean seal: exactly the rendered reads, one user message, plus the
        # effective dials (composition-supplied or the declared ship-time default —
        # every dial always reaches the wire with a concrete value; there is no
        # unpinned-omit path, the serving runtime's own defaults never apply).
        # Dispatch metadata never reaches the wire. The open `extras` sampling tail is
        # merged FIRST; the engine's owned wire keys are written AFTER, so an extras key
        # provably cannot override the checkpoint/seal/dials even if the compose-time
        # disjointness check had a gap (defense in depth — compose is the loud guarantee).
        body: dict = dict(extras)
        body["model"] = self.model
        body["messages"] = [
            {"role": "user", "content": render_input_payload(input_payload)}
        ]
        body["temperature"] = temperature
        body["max_tokens"] = max_tokens
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "output_schema",  # a fixed, deterministic constraint name
                "strict": True,
                "schema": self._constraint,
            },
        }
        return url, headers, timeout_s, body

    def invoke(
        self,
        *,
        input_payload,
        service_name,
        caller_qualified_name,
        caller_position,
        temperature,
        max_tokens,
        extras,
        remaining_budget_ms=None,
        **transport_extra,
    ):
        url, headers, timeout_s, body = self._prepare_request(
            input_payload=input_payload, temperature=temperature,
            max_tokens=max_tokens, extras=extras, transport_extra=transport_extra,
            remaining_budget_ms=remaining_budget_ms,
        )
        if self._transport is None:
            self._transport = urllib_transport

        status, payload = self._transport(
            url, json.dumps(body).encode("utf-8"), headers, timeout_s
        )
        # The shared fail-loud response floor (exact-status success + JSON/object
        # guards); the chat-completions field extraction below stays wire-specific.
        response = parse_json_object_response(
            status, payload, service_name=service_name, error=TrainableWireError,
        )
        choices = response.get("choices") or []
        if not isinstance(choices, list):
            raise TrainableWireError(
                f"'{service_name}' backend response 'choices' is not an array: "
                f"{type(choices).__name__}"
            )
        if not choices:
            raise TrainableWireError(
                f"'{service_name}' backend response carries no choices"
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise TrainableWireError(
                f"'{service_name}' backend response choice is not an object: "
                f"{type(choice).__name__}"
            )
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            raise TrainableWireError(
                f"'{service_name}' backend response message is not an object: "
                f"{type(message).__name__}"
            )
        if message.get("refusal"):
            raise TrainableWireError(
                f"'{service_name}' backend refused the constrained emission: "
                f"{message['refusal']!r}"
            )
        finish_reason = choice.get("finish_reason")
        if finish_reason != "stop":
            # Exactly "stop" — the completed-emission signal. An ABSENT finish_reason is
            # rejected too (the streaming surface's exact posture for a stream ending
            # without one): a wire that cannot say the emission completed cannot seal a
            # training record, and admitting None would be the claim-honesty gap the
            # module docstring's truncation guarantee forbids.
            raise TrainableWireError(
                f"'{service_name}' backend emission ended with finish_reason="
                f"{finish_reason!r}, not 'stop' (a truncated or unterminated emission "
                "cannot satisfy the decode constraint; raise max_tokens or shrink the "
                "schema)"
            )
        content = message.get("content")
        if content is None:
            raise TrainableWireError(
                f"'{service_name}' backend response carries no message content"
            )
        if not isinstance(content, str):
            raise TrainableWireError(
                f"'{service_name}' backend message content is not text: "
                f"{type(content).__name__}"
            )
        try:
            emission = json.loads(content)
        except ValueError as exc:
            raise TrainableWireError(
                f"'{service_name}' backend emission is not the constrained JSON the "
                f"submitted response_format requires: {content[:512]!r}"
            ) from exc
        # Returned verbatim — validation against the same declared shape is the
        # engine's output boundary (R-handler-005), strictly downstream of here.
        return emission

    def invoke_streaming(
        self,
        *,
        input_payload,
        service_name,
        caller_qualified_name,
        caller_position,
        temperature,
        max_tokens,
        extras,
        remaining_budget_ms=None,
        **transport_extra,
    ):
        """The streaming dispatch surface — a **generator**: yields each raw text
        fragment (``delta.content``) as the backend emits it, and **returns** the same
        assembled parsed emission ``invoke()`` returns (the engine's output boundary
        validates it identically — validate-on-assembly). Same closed dispatch-kwargs
        as ``invoke``; no consumer callback ever enters this frame — the ENGINE drives
        the generator and owns fragment delivery, so adapter code stays pure wire code.

        Wire form: the same request body plus the owned ``"stream": true`` key →
        SSE-framed chat-completions chunks (``data: {json}`` lines, ``data: [DONE]``
        terminal). Protocol failures raise :class:`TrainableWireError` raw, mirroring
        ``invoke()``: an HTTP error status, a refusal delta, a truncated stream
        (``finish_reason`` other than ``"stop"``, or a stream that ends with none), a
        non-JSON chunk, a shape-alien chunk, an empty stream, or an assembled emission
        that is not the constrained JSON. A chunk carrying no ``choices`` entry is
        benign non-delta framing on covered backends and is skipped — the
        ``finish_reason`` guard is the stream-integrity check."""
        url, headers, timeout_s, body = self._prepare_request(
            input_payload=input_payload, temperature=temperature,
            max_tokens=max_tokens, extras=extras, transport_extra=transport_extra,
            remaining_budget_ms=remaining_budget_ms,
        )
        body["stream"] = True  # engine-owned key, written after the extras merge
        if self._streaming_transport is None:
            self._streaming_transport = urllib_streaming_transport

        status, lines = self._streaming_transport(
            url, json.dumps(body).encode("utf-8"), headers, timeout_s
        )
        if status != 200:
            payload = b"".join(lines)
            raise TrainableWireError(
                f"'{service_name}' backend returned HTTP {status}: "
                f"{payload[:512]!r} (no retry — the wire failure surfaces raw)"
            )
        fragments: list[str] = []
        finish_reason = None
        for payload in iter_sse_data(lines):
            try:
                chunk = json.loads(payload)
            except ValueError as exc:
                raise TrainableWireError(
                    f"'{service_name}' backend stream chunk is not JSON: "
                    f"{payload[:512]!r}"
                ) from exc
            if not isinstance(chunk, dict):
                raise TrainableWireError(
                    f"'{service_name}' backend stream chunk is not a JSON object: "
                    f"{type(chunk).__name__} {payload[:512]!r}"
                )
            choices = chunk.get("choices") or []
            if not isinstance(choices, list):
                raise TrainableWireError(
                    f"'{service_name}' backend stream chunk 'choices' is not an "
                    f"array: {type(choices).__name__}"
                )
            if not choices:
                continue  # benign non-delta framing (e.g. a usage chunk) — skipped
            choice = choices[0]
            if not isinstance(choice, dict):
                raise TrainableWireError(
                    f"'{service_name}' backend stream choice is not an object: "
                    f"{type(choice).__name__}"
                )
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise TrainableWireError(
                    f"'{service_name}' backend stream delta is not an object: "
                    f"{type(delta).__name__}"
                )
            if delta.get("refusal"):
                raise TrainableWireError(
                    f"'{service_name}' backend refused the constrained emission: "
                    f"{delta['refusal']!r}"
                )
            if choice.get("finish_reason") is not None:
                finish_reason = choice["finish_reason"]
            content = delta.get("content")
            if content is None:
                continue  # role/refusal-only or finish-only delta — nothing to deliver
            if not isinstance(content, str):
                raise TrainableWireError(
                    f"'{service_name}' backend stream delta content is not text: "
                    f"{type(content).__name__}"
                )
            fragments.append(content)
            yield content
        if finish_reason != "stop":
            raise TrainableWireError(
                f"'{service_name}' backend stream ended with finish_reason="
                f"{finish_reason!r} (a truncated stream cannot satisfy the decode "
                "constraint; raise max_tokens or shrink the schema)"
                if finish_reason is not None
                else f"'{service_name}' backend stream ended without a finish_reason "
                     "(the stream was cut off before the backend finished emitting)"
            )
        if not fragments:
            raise TrainableWireError(
                f"'{service_name}' backend stream carried no content deltas"
            )
        assembled = "".join(fragments)
        try:
            emission = json.loads(assembled)
        except ValueError as exc:
            raise TrainableWireError(
                f"'{service_name}' backend assembled emission is not the constrained "
                f"JSON the submitted response_format requires: {assembled[:512]!r}"
            ) from exc
        # Returned verbatim (the generator's return value) — validation against the
        # same declared shape is the engine's output boundary, downstream of here.
        return emission
