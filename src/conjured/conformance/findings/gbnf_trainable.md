# Findings — `conjured.lib.gbnf_trainable`

- **Module:** the `conjured.lib.gbnf_trainable` module
- **Declaration:** its sibling declaration `gbnf_trainable.toml`
- **Instrument:** `trainable-backend-audit.md` (trainable-backend adapter)
- **Verdict:** `pass-with-notes`

The llama.cpp / GBNF grammar trainable-backend adapter. Audited against the four-property
trainable-backend contract (handler reference § Trainable backends), construction &
identity, reserved-key coverage, and failure honesty. The graded properties flow through
two shared helpers, read as part of this review: `conjured.adapters.wire`
(transport floor, constraint/payload rendering, response guards) and
`conjured.adapters.gbnf` (constraint → GBNF grammar projection).

## The four properties

1. **Server-side decode-time seal (property 1, R-handler-005).** The declared
   `output_schema` renders once to the canonical strict constraint in `__init__`
   (lines 237–241, via `wire.render_output_constraint`, wire.py lines 475–500) and
   projects once to a GBNF grammar (line 251, via `gbnf.grammar_from_constraint`); every
   `invoke()` submits `self._grammar` as the wire `grammar` unconditionally (line 291) —
   no branch, config, or transport value sends a call without it, and it is written AFTER
   the `extras` merge (line 288) so no author key can displace it. Exactly one wire call
   per `invoke()` (lines 295–297); no retry loop, fallback endpoint, or best-of-N. The
   emission is parsed once (`json.loads`, line 320) and returned verbatim (line 328) —
   no validation, coercion, defaulting, or repair; a non-conforming/truncated/unparseable
   emission raises `TrainableWireError` (lines 303–325), never a fix-up. Validation
   against the declared shape is the engine's output boundary, downstream (per
   R-handler-005's single-verdict-layer statement). **Pass.**
2. **Fine-tunable open weights the consumer owns (property 2).** No baked-in endpoint,
   vendor SDK, or hosted fallback anywhere in the module: the serving runtime is named
   only by the deployment's `endpoint` transport value, and a missing/empty `endpoint`
   raises through the shared guard (lines 270–275; wire.py lines 188–190, message at
   lines 272–274: "no default serving runtime"). The composed `model` identity is
   submitted every call (line 289). The targeted family — GGUF served by llama.cpp — is
   fine-tunable and self-hostable. **Pass.**
3. **A standard training-artifact contract (property 3).** `training_artifact_contract =
   "gguf"` (line 206) — an immutable, non-empty label naming a portable, self-servable
   artifact family (the family llama.cpp serves; matches the native-library reference's
   per-member entry: "Artifact contract: GGUF"). **Pass.**
4. **Clean read/write seal (property 4, R-handler-011).** The wire `prompt` is exactly
   `render_input_payload(input_payload)` (line 290) — the shared two-case rule (wire.py
   lines 503–512): a single str-valued port passes verbatim; anything else is the
   hasher's canonical JSON (key-sorted, compact) — both branches content-neutral and
   deterministic (same `input_payload` → byte-identical submission; `extras` is
   compose-fixed, so the full body is byte-stable per composition). No template, system
   scaffold, few-shot content, or injected guidance on the submit path; no added
   metadata, renamed keys, filtering, or post-processing on the return path (the
   `content` extraction + JSON parse is the wire form's sanctioned pure translation).
   Dispatch identity: `service_name` appears only in error prose (lines 306, 310, 314,
   323); `caller_qualified_name` / `caller_position` are accepted per the closed
   signature and never used — none reaches the body dict (lines 288–293). **Pass.**

## Construction, identity, coverage, honesty

5. **Compose-time constraint derivation.** The full derivation — strict-constraint
   render, the two single-concern reject walks, the grammar projection — runs in
   `__init__` (lines 237–251), i.e. compose time. Inexpressible declarations raise
   `ContractViolation` (`TRAINABLE_CONSTRAINT_UNSUPPORTED`, R-handler-005) there, never a
   dispatch-time surprise and never a silent best-effort: field `validators` outside the
   accepted set `{enum, minLength, maxLength}` (line 215) and every dotted validator key
   reject inside the shared renderer (wire.py lines 442–462), as do `bytes` (wire.py
   lines 339–347) and fixed-arity `tuple` (wire.py lines 372–385); a non-ASCII declared
   field name rejects in `_reject_gbnf_unrenderable_names` (lines 95–141 — including
   non-ASCII non-alphanumerics that would otherwise survive the grammar builder's
   sanitizer); a field `description` rejects in `_reject_gbnf_descriptions` (lines
   145–194 — this wire has no description channel and MUST NOT compensate via the
   prompt), both walkers covering every nesting level (properties, list `items`, dict
   `additionalProperties`, optional `anyOf` branches). The grammar is derived from the
   rendered constraint of the declared `output_schema` — never a hand-authored sibling —
   and GBNF is genuinely server-enforced (llama.cpp applies `grammar` token-by-token at
   decode). All four rejections match the native-library reference's per-member entry
   for this wire. **Pass.**
6. **No author-shaping identity (R-handler-011).** The sibling TOML's
   `[identity_schema]` is `{ model }` only (TOML lines 16–17) — no prompt template,
   system prompt, or content-shaping selector. `[transport_schema]` carries only
   `endpoint` / `api_key_ref` / `timeout_ms` (routing, credential, timeout — none
   content-bearing); `[config_schema]` carries the dial core plus the `extras` sampling
   tail (generation parameters, not prompt content; the R-service-type-002 gate
   forecloses undeclared config keys). **Pass.**
7. **Reserved-wire-key coverage.** The keys `invoke()` writes to the wire body are
   exactly `model`, `prompt`, `grammar`, `temperature`, `n_predict` (lines 289–293);
   `reserved_wire_keys` (lines 222–224) is exactly that five-key frozenset. Coverage is
   complete — no engine-written wire key is undeclared, so the compose disjointness
   check fully protects against an author `extras` override; and as defense in depth the
   owned keys are written after the `extras` merge, so an override is impossible even
   with a gap. **Pass.**

**Failure honesty (R-handler-002).** Every path between the wire and the return raises
raw: HTTP status ≠ 200 (shared `expect_success`, wire.py lines 198–211 — "no retry — the
wire failure surfaces raw"; the transport returns an `HTTPError` body as data, wire.py
lines 118–119, so the structured error always fires), non-JSON response body and
non-object body (shared `parse_json_object_response`, wire.py lines 214–238), missing
`content` (lines 303–307), non-string `content` (lines 308–312), a truthy `truncated`
flag (lines 313–318), and a `content` that is not parseable JSON (lines 319–325). No
`except` swallows, no default substitution, no log-instead-of-raise; transport-level
failures (connection refused, DNS) ride raw out of `urllib_transport`. **Pass.**

## Preconditions (engine-enforced; confirmed consistent)

Class shape; keyword-only `invoke()` with the closed dispatch kwargs + the declared
`[config_schema]` fields (`temperature`, `max_tokens`, `extras`) + `**transport_extra`;
instance-state-only mutation (`self.model` / `self._constraint` / `self._grammar` /
`self._transport`, the memoized injection seam); class attributes are immutable
(`str`, two `frozenset`s); no import-time I/O; both certification attributes present and
well-formed.

## Notes

Neither note is a violation — no path masks a failure or corrupts a captured record;
both are bounded by the engine's output-boundary backstop R-handler-005 names.

1. **`truncated`-flag semantics vs. the remediation hint.** The adapter raises on a
   truthy response `truncated` flag (lines 313–318) with a hint attributing it to the
   token bound ("raise max_tokens or shrink the schema"). In llama.cpp's `/completion`
   wire form, `truncated` conventionally signals **context-window/prompt truncation**
   (the runtime dropped prompt content), while a generation stopped by the `n_predict`
   bound is signaled by separate stop fields the adapter does not read. Raising on
   `truncated` is correct — stronger than the hint implies, since a truncated prompt
   also breaks the property-4 claim that the backend saw exactly the submitted reads —
   but the hint prose may misattribute the cause, and a limit-cut emission is caught
   only by the JSON-parse guard (lines 319–325) or, for the narrow
   grammar-complete-at-the-bound case, by the engine's output boundary (canon's named
   backstop for token-budget truncation). Honesty holds on every arm; the hint wording
   and the exact llama.cpp field semantics are worth verifying at the module's next
   edit (which stales this stamp anyway).
2. **Length-bound escape-representation margin (shared renderer,
   `adapters/gbnf.py` lines 168–182).** A `minLength`/`maxLength` bound renders as a
   counted `string-char` repetition, where one JSON escape sequence — including one
   `\uXXXX` unit — counts as one repetition; the engine-side model counts decoded code
   points. The two counts diverge only for an emission spelling an astral character as
   a surrogate-pair escape (two grammar units, one decoded character): the grammar can
   admit a representation whose decoded length falls outside the declared bound, which
   the engine's output boundary then rejects loudly. A fail-loud dispatch-time edge in
   R-handler-005's acknowledged divergence class, not a corruption path; recorded as
   the one known margin where the grammar's accepted space is not exactly the model's.
