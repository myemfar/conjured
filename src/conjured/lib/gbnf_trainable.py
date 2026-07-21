"""``conjured.lib.gbnf_trainable`` — the llama.cpp / GBNF grammar trainable backend
(the wire-form name says what the adapter *speaks*, never a model type — slice ruling
D3).

The native trainable-backend adapter for the GGUF family: a llama.cpp ``llama-server``
(or any runtime speaking its ``/completion`` wire form with GBNF ``grammar``
enforcement) — "a direct llama.cpp / GBNF grammar path (the GGUF family)" per
``conjured/docs/components/handler/reference.md`` § Trainable backends. The sibling
``gbnf_trainable.toml`` is the service-type declaration this class implements (paired
by qualified name; one implementation per name, R-service-type-004).

**The four-property trainable contract, by construction:**

1. **Server-side decode-time seal.** The declared ``trainable.output_schema``
   (engine-supplied at construction) renders once into the canonical strict constraint
   (:func:`conjured.adapters.wire.render_output_constraint`), projects once into a GBNF
   grammar (:func:`conjured.adapters.gbnf.grammar_from_constraint`), and every
   ``invoke()`` submits that grammar — the serving runtime enforces it token-by-token
   at decode. No client-side parse-and-retry path exists; a non-conforming emission
   halts in the engine's output validation (R-handler-005). Field ``validators``
   (value predicates a token-level grammar cannot enforce) reject at construction
   (= compose) — the compose-time caveat's honest failure, as do ``bytes`` and
   fixed-arity ``tuple`` channels (JSON-wire-wide, the shared renderer: a JSON wire
   delivers arrays, which strict validation rejects against a declared tuple). Unlike
   the strict OpenAI-compatible wire form, the grammar **does** express open-keyed
   ``dict[str, T]`` shapes. One capability boundary of this wire form: field
   ``description`` strings cannot ride a grammar — and the adapter MUST NOT compensate
   by injecting them into the prompt (property 4), so a ``trainable.output_schema`` field
   carrying a ``description`` is **rejected at construction (= compose)** with the same
   constraint-unsupported ContractViolation as ``bytes`` / ``tuple`` — never a silent drop
   (the engine never hashes a model-conditioning input a wire drops), and never reaches this
   backend.
   A second wire boundary of the grammar form: rule names are ASCII-only
   (``[a-zA-Z0-9-]``), so a declared field name carrying **any non-ASCII character**
   cannot name its grammar rules and is rejected at construction (= compose) with the
   same constraint-unsupported ContractViolation — never a per-dispatch grammar
   rejection.
2. **Fine-tunable open weights the consumer owns.** No hosted endpoint, no endpoint
   default — the serving runtime is named exclusively by the deployment's ``endpoint``
   transport value; the composed ``model`` identity is submitted with every call.
3. **A standard training-artifact contract.** ``training_artifact_contract = "gguf"``
   — the artifact family llama.cpp serves.
4. **A clean read/write seal.** ``invoke()`` submits exactly the rendered
   ``input_payload`` as the ``prompt`` (no template, no system scaffold, no injected
   content — prompt shaping is an upstream preprocessor's job per R-handler-011) and
   returns exactly the parsed constrained emission. Dispatch metadata never reaches
   the wire.

**Lifecycle (B2)** and the **wire-failure posture** are identical to the
OpenAI-compatible sibling: one instance per composition, identity-only-plus-declared-
shape construction, the shared module-level wire client
(:func:`conjured.adapters.wire.urllib_transport`) memoized into the instance
attribute on the first ``invoke()`` (the memoized attribute is the injection seam;
per-call transport values ride ``**transport_extra``), instance-state caching only
(the vector-7 seal), generation dials always on the wire with a concrete effective
value (composition-supplied or the declared ship-time default in the sibling TOML —
no unpinned-omit path; the serving runtime's own defaults never apply), and every
protocol failure raised raw as
:class:`conjured.adapters.wire.TrainableWireError` — never retried, never substituted.

**Wire protocol.** ``POST {endpoint}/completion`` with a JSON body carrying ``prompt``,
``grammar``, the pinned dials (``temperature``; ``max_tokens`` maps to llama.cpp's
``n_predict``); transport fields: ``endpoint`` (required), ``api_key_ref`` (optional
``Authorization: Bearer`` value — llama-server's ``--api-key`` — a ``secret_ref``-declared
``[scheme]payload`` secret reference the adapter resolves at dispatch via
:func:`conjured.adapters.secret_refs.resolve_secret_ref`, never a raw token in the TOML),
``timeout_ms`` (an author-named per-call transport passthrough this adapter applies).
The open ``extras``
table (the ``[config_schema]`` ``table`` field, default ``{}``) is the llama.cpp sampling
tail this dial core does not enumerate (``top_p`` / ``top_k`` / ``repeat_penalty`` /
``mirostat`` / ``seed`` / …): merged **verbatim** into the wire body, hash-covered as
canonical data, its keys disjoint-by-compose from the reserved wire keys (the engine's
owned keys are written AFTER the merge — an extras key can never override the grammar /
prompt / dials). The response's ``content`` field carries the constrained emission text,
parsed as JSON and returned verbatim.
"""

from __future__ import annotations

import json

from conjured.adapters.gbnf import grammar_from_constraint
from conjured.adapters.wire import (
    TrainableWireError,
    parse_json_object_response,
    prepare_json_transport,
    render_input_payload,
    render_output_constraint,
    urllib_transport,
)
from conjured.errors import Check, ContractViolation
from conjured.ir.channel_types import FieldDecl


def _reject_gbnf_unrenderable_names(
    node: dict, *, field_path: str, schema_source: str
) -> None:
    """The GBNF wire form's rule-name boundary (the compose-time caveat,
    R-handler-005): grammar rule names are drawn from the ASCII-only charset
    ``[a-zA-Z0-9-]``, so a declared field name carrying **any non-ASCII character**
    cannot name its grammar rules — the declaration is rejected at construction (=
    compose), the same honest-failure class as ``bytes``/``tuple``, never a per-dispatch
    grammar rejection from the serving runtime. The reject is **unconditional on
    non-ASCII** (``native-library/reference.md`` § ``conjured.lib.gbnf_trainable``: "a
    declared output-field name carrying a non-ASCII character is … rejected at compose"):
    not just non-ASCII *alphanumerics* (``é``), but non-ASCII *non*-alphanumerics
    (``€``, ``°``) too — the latter would otherwise survive ``_claim``'s sanitizer (which
    maps a non-ASCII non-alphanumeric to ``-``, masking it from the engine-internal
    backstop) and let the grammar carry a ``\\uXXXX``-escaped key literal while the output
    model validates the raw key, weakening the literal-equal seal at the wire boundary."""
    for key, member in (node.get("properties") or {}).items():
        member_path = f"{field_path}.{key}" if field_path else key
        if not key.isascii():
            raise ContractViolation(
                check=Check.TRAINABLE_CONSTRAINT_UNSUPPORTED, rule_id="R-handler-005",
                expected="every declared field name on the GBNF wire renders to the "
                         "ASCII rule-name charset ([a-zA-Z0-9-]) grammar rule names "
                         "are drawn from",
                actual=f"a non-ASCII field name at '{member_path}'",
                remediation_hint="rename the field within ASCII (letters, digits, "
                                 "'-', '_'), or bind a wire form whose constraint "
                                 "carries no grammar rule names",
                file_path=schema_source, section_path="trainable.output_schema",
            )
        _reject_gbnf_unrenderable_names(
            member, field_path=member_path, schema_source=schema_source
        )
    if isinstance(node.get("items"), dict):
        _reject_gbnf_unrenderable_names(
            node["items"], field_path=f"{field_path}[]", schema_source=schema_source
        )
    if isinstance(node.get("additionalProperties"), dict):
        _reject_gbnf_unrenderable_names(
            node["additionalProperties"],
            field_path=f"{field_path}[*]",
            schema_source=schema_source,
        )
    for member in node.get("anyOf") or ():
        _reject_gbnf_unrenderable_names(
            member, field_path=field_path, schema_source=schema_source
        )


# guarantees: gbnf-rejects-described-field
def _reject_gbnf_descriptions(
    node: dict, *, field_path: str, schema_source: str
) -> None:
    """The GBNF wire form's description-delivery boundary (the compose-time caveat,
    R-handler-005): a GBNF grammar carries **no field-description channel**, and the adapter
    MUST NOT compensate by shaping the prompt (property 4 — the clean read/write seal). A
    ``trainable.output_schema`` field carrying a ``description`` therefore cannot ride this wire
    and is rejected at construction (= compose) — the same honest-failure class as ``bytes`` /
    ``tuple`` / a non-ASCII field name, **never a silent drop** (``native-library/reference.md``
    § ``conjured.lib.gbnf_trainable``: "a described field on this wire is … a compose-time
    ContractViolation"; ``hash-model.md`` § What the pipeline-hash absorbs owns why — the engine
    never hashes a model-conditioning input a wire silently drops).

    The shared renderer (``wire.render_output_constraint``) carries a field's ``description`` into
    its JSON Schema node (``node['description']``), so this walker rejects on any node bearing one,
    at the root object and every nested level the name walker covers (properties, list ``items``,
    ``dict`` ``additionalProperties``, optional ``anyOf`` branches). Kept a **separate,
    single-concern walk** from ``_reject_gbnf_unrenderable_names`` (one deterministic check, one
    fix-shape) and run BEFORE ``grammar_from_constraint`` so the rejection is compose-time."""
    if "description" in node:
        raise ContractViolation(
            check=Check.TRAINABLE_CONSTRAINT_UNSUPPORTED, rule_id="R-handler-005",
            expected="every declared field on the GBNF wire is expressible as a grammar "
                     "decode constraint; a field `description` is model-facing contract "
                     "content the GBNF grammar has no channel to carry",
            actual=f"a `description` on the output-schema field at '{field_path or '<root>'}'",
            remediation_hint="route the field to the conjured.lib.openai_compatible_trainable "
                             "wire (its submitted json_schema carries descriptions), or move the "
                             "guidance to the composition's [annotations] block",
            file_path=schema_source, section_path="trainable.output_schema",
        )
    for key, member in (node.get("properties") or {}).items():
        member_path = f"{field_path}.{key}" if field_path else key
        _reject_gbnf_descriptions(
            member, field_path=member_path, schema_source=schema_source
        )
    if isinstance(node.get("items"), dict):
        _reject_gbnf_descriptions(
            node["items"], field_path=f"{field_path}[]", schema_source=schema_source
        )
    if isinstance(node.get("additionalProperties"), dict):
        _reject_gbnf_descriptions(
            node["additionalProperties"],
            field_path=f"{field_path}[*]",
            schema_source=schema_source,
        )
    for member in node.get("anyOf") or ():
        _reject_gbnf_descriptions(
            member, field_path=field_path, schema_source=schema_source
        )


class GBNFTrainable:
    """The llama.cpp / GBNF grammar trainable backend (module docstring)."""

    #: Property 3 — a portable, self-servable artifact contract (this adapter ships GGUF).
    #: The compose-time gate certifies this native **structurally** — by the engine's native
    #: adapter table (native-by-construction), never a self-declared class attribute
    #: (handler/reference.md § Trainable backends). The gate still verifies this property
    #: label (non-empty string) + ``reserved_wire_keys`` against the resolved class.
    training_artifact_contract = "gguf"
    #: The accepted-matrix certification (handler/reference.md § Trainable backends — the
    #: accepted matrix): the bare standard constraint keywords this wire RENDERS into the
    #: GBNF grammar. `enum` → literal alternation; `minLength` / `maxLength` → a counted
    #: `string-char` repetition. `pattern` is NOT in this set — a subtly-wrong regex→GBNF
    #: translation corrupts the literal-equal seal worse than rejecting, so a `pattern`
    #: keyword stays a loud compose-time rejection (the author moves it to a downstream
    #: reader's reads schema); widening to a decidable regex subset is a later
    #: per-family certification edit.
    accepted_wire_keywords = frozenset({"enum", "minLength", "maxLength"})
    #: The reserved wire keys this adapter constructs (native-library/reference.md extras
    #: rider; the certification gate validates this attribute, compose checks an `extras`
    #: table is disjoint from it): the DECLARED dial-core fields — `temperature` AND
    #: `max_tokens` (the [config_schema] name, reserved alongside its wire rendering
    #: `n_predict` so the token-bound dial has exactly one supply route: an
    #: `extras = { max_tokens = N }` smuggle is rejected at compose naming the dial's
    #: real home) — plus the structural keys (llama.cpp's `prompt` / `n_predict` /
    #: `grammar`) and `model`. An `extras` key naming one is rejected at compose; past
    #: compose the key-sets are disjoint by construction, and `invoke()` writes its owned
    #: keys AFTER the `**extras` merge so the grammar/prompt/dials hold even with a gap.
    reserved_wire_keys = frozenset(
        {"model", "prompt", "temperature", "max_tokens", "n_predict", "grammar"}
    )

    def __init__(
        self,
        *,
        model: str,
        output_schema: "tuple[FieldDecl, ...]",
        schema_source: str,
    ) -> None:
        # Compose-fixed identity + the literal-equal artifact, fixed once per
        # composition. The grammar derivation IS the compose-time expressibility gate
        # for this wire form (validators reject inside the renderer).
        self.model = model
        self._constraint = render_output_constraint(
            tuple(output_schema), schema_source=schema_source,
            accepted_keywords=self.accepted_wire_keywords,
            wire="llama.cpp GBNF grammar",
        )
        _reject_gbnf_unrenderable_names(
            self._constraint, field_path="", schema_source=schema_source
        )
        # A GBNF grammar has no field-description channel and the adapter never prompt-injects
        # (property 4) — so a described output-schema field is rejected here at compose, never
        # silently dropped (the engine never hashes a model-conditioning input a wire drops).
        _reject_gbnf_descriptions(
            self._constraint, field_path="", schema_source=schema_source
        )
        self._grammar = grammar_from_constraint(self._constraint)
        # The injection seam: memoizes the shared module-level wire client on the
        # first invoke() (B2 — the constructor has no transport to exercise it with).
        self._transport = None

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
        # The shared transport floor (property 2 rides the endpoint guard: no hosted
        # default exists to fall back to); this wire appends only its route. The
        # remaining_budget_ms kwarg is deadline participation (service-type/
        # reference.md § Deadline propagation): the floor folds min(transport
        # timeout, budget) into the effective per-call timeout.
        endpoint, headers, timeout_s = prepare_json_transport(
            transport_extra, error=TrainableWireError,
            missing_endpoint="transport supplies no 'endpoint' — the GBNF trainable "
                             "wire has no default serving runtime (the consumer owns "
                             "the backend)",
            remaining_budget_ms=remaining_budget_ms,
        )
        if self._transport is None:
            self._transport = urllib_transport
        url = f"{endpoint.rstrip('/')}/completion"

        # The clean seal: exactly the rendered reads as the prompt, the grammar as the
        # decode constraint, plus the effective dials (composition-supplied or the
        # declared ship-time default — every dial always reaches the wire with a
        # concrete value; there is no unpinned-omit path, the serving runtime's own
        # defaults never apply). Dispatch metadata never reaches the wire. The open
        # `extras` sampling tail is merged FIRST; the engine's owned wire keys are written
        # AFTER, so an extras key provably cannot override the grammar/prompt/dials even if
        # the compose disjointness check had a gap (defense in depth).
        body: dict = dict(extras)
        body["model"] = self.model
        body["prompt"] = render_input_payload(input_payload)
        body["grammar"] = self._grammar
        body["temperature"] = temperature
        body["n_predict"] = max_tokens  # llama.cpp's name for the token bound

        status, payload = self._transport(
            url, json.dumps(body).encode("utf-8"), headers, timeout_s
        )
        # The shared fail-loud response floor (exact-status success + JSON/object
        # guards); the /completion field extraction below stays wire-specific.
        response = parse_json_object_response(
            status, payload, service_name=service_name, error=TrainableWireError,
        )
        content = response.get("content")
        if content is None:
            raise TrainableWireError(
                f"'{service_name}' backend response carries no content"
            )
        if not isinstance(content, str):
            raise TrainableWireError(
                f"'{service_name}' backend response content is not text: "
                f"{type(content).__name__}"
            )
        if response.get("truncated"):
            raise TrainableWireError(
                f"'{service_name}' backend emission was truncated (a truncated "
                "emission cannot satisfy the decode constraint; raise max_tokens or "
                "shrink the schema)"
            )
        try:
            emission = json.loads(content)
        except ValueError as exc:
            raise TrainableWireError(
                f"'{service_name}' backend emission is not the constrained JSON the "
                f"submitted grammar requires: {content[:512]!r}"
            ) from exc
        # Returned verbatim — validation against the same declared shape is the
        # engine's output boundary (R-handler-005), strictly downstream of here.
        return emission
