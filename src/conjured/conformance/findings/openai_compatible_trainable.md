# Findings — `conjured.lib.openai_compatible_trainable`

- **Module:** the `conjured.lib.openai_compatible_trainable` module
- **Service-type declaration:** its sibling declaration `openai_compatible_trainable.toml`
- **Instrument:** `trainable-backend-audit.md` (trainable-backend adapter)
- **Verdict:** `pass-with-notes`
- **Supersedes:** the 2026-07-09 audit (`source_hash`
  `602c248d5d7f3de061dee3b58c8f67cfc7185f02f252404d20816d89bc4da0f0`) — the module was
  edited since (the `api_key_ref` transport value became a `[scheme]payload` secret
  reference resolved at dispatch via `conjured.adapters.secret_refs.resolve_secret_ref`,
  and the endpoint/bearer/timeout preparation plus the fail-loud response floor moved to
  shared helpers in `conjured.adapters.wire` — `prepare_json_transport`,
  `parse_json_object_response`), which changes the module's bytes and stales the prior
  stamp. This review covers the module **as it now stands**, including the shared wire
  helpers the graded properties flow through, and every check below was run against both
  dispatch surfaces (`invoke()` and `invoke_streaming()`).

The OpenAI-compatible structured-output trainable-backend adapter. Audited against the
four-property trainable-backend contract, construction & identity, reserved-key coverage,
and failure honesty. Every check holds on both dispatch surfaces; the minor observations
recorded below are real, non-blocking.

## The four properties

1. **Server-side decode-time seal (property 1, R-handler-005).** The declared
   `output_schema` renders once to the strict JSON-Schema constraint in `__init__`
   (`self._constraint`, lines 174–178, via the shared
   `conjured.adapters.wire.render_output_constraint`, wire.py lines 475–500 — a direct
   rendering of the declared `FieldDecl` tuple, never a hand-authored constraint); both
   dispatch surfaces submit it via the shared `_prepare_request` helper (lines 187–230),
   which writes `response_format = {"type": "json_schema", "json_schema": {"name":
   "output_schema", "strict": true, "schema": self._constraint}}` (lines 222–229) — a
   fixed deterministic constraint name, no branch, config, or transport value that omits
   the constraint on either surface. Exactly one wire call per invocation: `invoke()`
   calls `self._transport` once (lines 251–253); `invoke_streaming()` calls
   `self._streaming_transport` once (lines 350–352). No retry loop, fallback endpoint, or
   best-of-N on either surface — `invoke()` parses the emission once (line 304) and
   returns it verbatim (line 312); `invoke_streaming()` assembles the yielded fragments
   once (line 424), parses once (line 426), and returns verbatim (line 434). Neither
   surface validates, coerces, defaults, or repairs the emission — validation against the
   declared shape is the engine's output boundary, downstream (the single verdict layer
   R-handler-005 names). **Pass.**
2. **Fine-tunable open weights the consumer owns (property 2).** No baked-in endpoint,
   vendor SDK, or hosted-API fallback anywhere on either surface: both route through
   `_prepare_request` → the shared `prepare_json_transport` (wire.py lines 177–195),
   which raises `TrainableWireError` when `transport_extra` carries no `endpoint`
   ("no default serving runtime — the consumer owns the backend", module lines 199–204);
   the adapter appends only its route (`/chat/completions`, line 205). The serving
   runtime is named exclusively by the deployment transport; `model` is the composed
   identity, submitted on every call (line 216). The covered family (vLLM,
   llama-server's OpenAI surface, TGI, SGLang) is self-hostable, fine-tunable
   open-weights serving. **Pass.**
3. **A standard training-artifact contract (property 3).**
   `training_artifact_contract = "safetensors+peft"` (line 146) — an immutable non-empty
   class attribute naming a portable, self-servable artifact family (merged safetensors
   plus a PEFT/LoRA adapter), exactly what canon requires (a provenance label the engine
   records opaquely; § Trainable backends, property 3). **Pass.**
4. **Clean read/write seal (property 4, R-handler-011).** `_prepare_request` builds the
   wire `messages` as a single `user` message whose content is exactly
   `render_input_payload(input_payload)` (lines 217–219): the shared renderer (wire.py
   lines 503–512) passes a single str-valued port verbatim and renders anything else
   through the hasher's canonical serializer (`canonical_json` — key-sorted, compact,
   `ensure_ascii=False`; canonical.py lines 68–78), so the reads→wire serialization is
   deterministic and content-neutral — no template, no system prompt, no few-shot
   examples, no dropped or reordered fields, shared by both surfaces. `invoke()` returns
   the parsed emission verbatim (line 312); `invoke_streaming()` yields each
   `delta.content` string exactly as received (lines 401–410) and returns the
   reassembled parsed emission verbatim (lines 424–434) — no added metadata, renamed
   keys, post-processing, or filtering. Dispatch identity is structurally excluded from
   the wire: `_prepare_request`'s parameter list (lines 187–189) does not accept
   `service_name` / `caller_qualified_name` / `caller_position` at all, so neither
   surface can leak them into the body even by mistake — both reference `service_name`
   only inside error-message strings. The resolved `api_key_ref` credential reaches only
   the `Authorization` header (wire.py `bearer_headers`, lines 158–167), never the
   payload. **Pass.**

## Construction, identity, coverage, honesty

5. **Compose-time constraint derivation.** Derivation runs in `__init__` (= compose;
   lines 174–181): `render_output_constraint` rejects — with `ContractViolation`
   (`TRAINABLE_CONSTRAINT_UNSUPPORTED`, R-handler-005) — any field `validators` keyword
   outside the wire's accepted set, every namespaced (dotted) validator key, a `bytes`
   channel, and a fixed-arity `tuple` channel (wire.py lines 339–347, 372–385, 442–462);
   the strict-wire-specific `_reject_strict_inexpressible` walk (lines 101–134) then
   rejects an open-keyed `dict[str, T]` at any nesting level (properties / items / anyOf
   all traversed; a `DictType` node renders without a `properties` key and is caught at
   its own level). All raise at construction, never at dispatch, and both surfaces read
   the same `self._constraint`. The derivation is genuinely server-enforceable: the
   accepted set `accepted_wire_keywords = frozenset({"enum"})` (line 152) matches the
   accepted-matrix value canon assigns this wire (native-library reference
   § `conjured.lib.openai_compatible_trainable`: "only `enum` renders into the submitted
   schema"), and `enum` renders as JSON-Schema `enum` inside the strict `json_schema`
   the serving runtime enforces token-by-token. **Pass.**
6. **No author-shaping identity (R-handler-011).** The sibling TOML's `[identity_schema]`
   is `{ model }` only (`openai_compatible_trainable.toml` lines 16–17) — no prompt
   template, system prompt, or content-shaping selector. `[config_schema]` carries only
   generation dials (`temperature`, `max_tokens`, the open `extras` sampling-tail table,
   lines 24–38), consistent with R-handler-011's config-vs-reads split (generation
   parameters only). **Pass.**
7. **Reserved-wire-key coverage.** `reserved_wire_keys` (lines 159–161) is `{model,
   messages, temperature, max_tokens, response_format, stream}` — six keys. Enumerating
   every key either surface actually writes: `_prepare_request` writes `model`,
   `messages`, `temperature`, `max_tokens`, `response_format` (lines 216–229, shared by
   both surfaces); `invoke_streaming()` additionally writes `stream` after
   `_prepare_request` returns (line 346). That is exactly six keys, all declared —
   coverage is exact, neither under- nor over-declared. The owned keys are written AFTER
   the `body: dict = dict(extras)` merge (line 215), so an extras key cannot override
   them (with one traced non-uniformity for `stream` on the buffered path — see Notes).
   **Pass.**

**Failure honesty (R-handler-002).** No swallowed exceptions, no `except: return
default`, no logging-instead-of-raising on any path between the wire and the return, on
either surface. `invoke()`: HTTP status ≠ 200 raises via the shared `expect_success` /
`parse_json_object_response` floor (wire.py lines 198–238 — exact-status success, any
other status raised raw, "no retry"); non-JSON or non-object body raises there too;
shape-alien `choices` / `choice` / `message` raise (lines 259–280); an explicit
`refusal` raises (lines 281–285); a truncating `finish_reason` raises (lines 286–292);
missing or non-str content raises (lines 293–302); an unparseable emission raises
(lines 303–309). `invoke_streaming()` covers the full stream-protocol failure list:
HTTP error status (lines 353–358, over the buffered error body the streaming transport
returns as data — wire.py lines 136–138); non-JSON chunk (lines 362–368); shape-alien
chunk at every level (chunk-not-dict 369–373, `choices`-not-list 374–379,
choice-not-dict 382–387, delta-not-dict 388–393, content-not-str 404–408); a refusal
delta (lines 394–398); a truncated stream — post-loop, `finish_reason != "stop"` raises
with an explicit two-case message distinguishing a truncating reason from a stream that
ended with no `finish_reason` at all (lines 411–419); an empty stream — zero content
deltas with a nominal `"stop"` raises on `not fragments` (lines 420–423); an
unparseable assembled emission raises (lines 424–431). A chunk with an empty `choices`
list is skipped as benign non-delta framing (e.g. a usage chunk, lines 380–381) — not a
masked failure: the post-loop `finish_reason` / `fragments` guards are the
stream-integrity backstop. Underneath, `urllib_transport` /
`urllib_streaming_transport` return HTTP error statuses **as data** for the adapter's
structured raise and let transport-level failures (connection refused, DNS) ride raw
(wire.py lines 105–144); `iter_sse_data` raises a non-UTF-8 line's
`UnicodeDecodeError` raw and loud (wire.py lines 241–258); the secret resolver
(`secret_refs.resolve_secret_ref`, reached from `prepare_json_transport`) fails loud as
`SecretResolutionError` on every store-side problem and returns `None` only for the
declared no-credential state — no path substitutes a default credential or value.
Nothing on any path masks a wire failure with a schema-valid value. **Pass.**

## Preconditions (engine-enforced; confirmed consistent)

Class shape and vector-7 purity: instance-state-only mutable state (`self._transport`,
`self._streaming_transport`, memoized injection seams, lines 184–185, 248–249, 347–348);
the two class attributes plus `accepted_wire_keywords` are immutable (`str` /
`frozenset`); no caching decorators, no import-time I/O. `invoke()`'s closed
dispatch-kwargs signature is engine-validated at resolution (R-service-type-002/003).
`invoke_streaming()` is the canon-owned optional streaming surface (service-type
reference § The streaming adapter surface): a generator function with the same closed
dispatch-kwargs, verified here directly — its signature (lines 314–325) is keyword-only
and lists exactly `input_payload, service_name, caller_qualified_name, caller_position,
temperature, max_tokens, extras, **transport_extra` — byte-for-byte the same closed
shape as `invoke()` (lines 232–243), and it yields raw text fragments and returns the
assembled parsed emission, exactly the canon-stated generator contract. **Confirmed
matching.**

## Notes

- **`finish_reason=None` is accepted as non-truncated on `invoke()`** (line 287:
  `if finish_reason not in (None, "stop")`), carried over from the prior audits — a
  defended leniency, not a violation (an incomplete emission still fails `json.loads`
  at line 304 or the engine's output-boundary validation downstream). It is asymmetric
  with the streaming surface, which requires an explicit `"stop"` and raises on a
  stream that ends with none (lines 411–419) — defensible, since a buffered body that
  arrived at all is complete in a way an interruptible stream is not. No fix required.
- **The `"stream"` reserved key has no code-level reassertion on the `invoke()` path**,
  unlike the other five reserved keys. `_prepare_request` deliberately never writes
  `stream` (its absence is the wire's non-streaming default, per its own docstring,
  lines 191–195), and `invoke()` never writes or clears it. The module docstring's
  defense-in-depth claim (owned keys "written AFTER the merge … even if the compose-time
  disjointness check had a gap", lines 72–74) holds uniformly for the other five keys
  and for `stream` on `invoke_streaming()` (line 346, unconditionally set), but not for
  `stream` on `invoke()`: an extras table carrying `"stream"` — reachable only if the
  compose-time disjointness check had a gap, since `stream` IS declared in
  `reserved_wire_keys` — would ride into the buffered request body. Traced consequence
  is still fail-loud, not corruption: `invoke()` always uses the buffered transport and
  `parse_json_object_response` on the full body; an SSE-framed streaming response is
  not a JSON object, so this raises `TrainableWireError` ("response body is not JSON")
  rather than returning a masked value. The primary loud guarantee (compose-time
  disjointness) fully covers the key. Recorded because the docstring's uniformity claim
  is not literal for all six keys; no fix required.
- **Module docstring's "Lifecycle" paragraph names one injection seam, not both.** Lines
  49–55 describe the buffered wire client (`urllib_transport`, "memoized into the
  instance attribute on the first `invoke()` — that memoized attribute is the injection
  seam") — singular, without the second memoized seam the streaming surface added
  (`self._streaming_transport` / `urllib_streaming_transport`, lines 184–185, 347–348).
  The "Wire protocol" paragraph (lines 57–81) is likewise buffered-only vocabulary; the
  SSE streaming protocol is documented correctly and in full in `invoke_streaming`'s
  own docstring (lines 326–341). Documentation completeness gap only; no fix required.
