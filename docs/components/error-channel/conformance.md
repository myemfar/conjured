---
kind: conformance
audience: [authors, integrators, agents]
slug: error-channel-conformance
component: error-channel
---

{#error-channel-conformance}
# Error-channel conformance checks

The mechanical conformance checks the engine fires for the [error-channel](#glossary-error-channel) component, plus diagnostic framing for the [review-enforced](#review-enforced) rules whose violations live in handler bodies the runner cannot see.

Each entry below is structured for diagnosing a thrown error or auditing a handler against the engine's contract. The format:

- **Check name** — the mechanical check or review target; lowercase noun phrase.
- **Rule anchor** — the derived rule the check enforces, cited by file + prose anchor.
- **Trigger** — when the check fires (handler-declaration load, compose time, dispatch time, build time).
- **Mechanism** — what the engine does to detect the violation.
- **Violation example** — a concrete handler declaration or Python snippet that fires the check.
- **Error class** — which of the [closed-enum classes](#error-class) the engine raises.
- **Diagnosis** — what to look for and how to fix.

---

{#error-channel-mechanically-enforced-checks}
## Mechanically-enforced checks

{#error-class-closed-enum-guarantee-at-dispatch-boundary}
### Error-class closed-enum guarantee at dispatch boundary

- **Rule anchor.** [Derived rule R-error-channel-001 (closed-enum error classes)](#error-channel-derived-rules). Check `pipeline-failure-wrap` (audit code `C1.PIPELINE_FAILURE_WRAP.001`).
- **Trigger.** Dispatch time — any exception escaping a handler body through the runner's dispatch boundary.
- **Mechanism.** The runner's dispatch boundary intercepts exceptions at handler-body exit. `ContractViolation` and `SchemaValidationError` propagate as-is — they are produced by the runner's own validation machinery and are already engine-class exceptions. Every other exception is caught and wrapped into `PipelineFailure` with `cause_class` set to the Python exception type of the underlying exception. No other class escapes the boundary; the closed-enum guarantee is enforced structurally, not by convention.
- **Violation example.**

  ```python
  # Handler body that raises an uncaught arbitrary exception
  def my_handler(*, player_input: str) -> dict:
      raise ConnectionError("backend unreachable")
      return {"response": "..."}
  ```

  The error channel surfaces:

  ```
  PipelineFailure(
      failure_category="handler",
      cause_class="ConnectionError",
      cause_message="backend unreachable",
      failed_handler_qualified_name="...",
      ...
  )
  # ConnectionError does NOT surface as a fourth error class
  ```

- **Error class.** [PipelineFailure](#pipelinefailure).
- **Diagnosis.** Consumer error-handling code dispatching on error class covers the closed-enum branches: `ContractViolation`, `SchemaValidationError`, `PipelineFailure`. A catch-all after them is dead code under the closed-enum guarantee. For runtime failure, dispatch on the fields of `PipelineFailure` — the closed `failure_category` for the locus (`service` / `handler` / `engine`) and the open `cause_class` for the specific exception — rather than trying to catch sub-classes of `PipelineFailure`; no engine-vended sub-class of `PipelineFailure` exists.

---

{#schema-validation-failure-halts}
### Schema-validation failure halts, never falls back

- **Rule anchor.** [Derived rule R-error-channel-003 (halt semantics)](#error-channel-derived-rules). Checks `halt-on-input-validation-error`, `halt-on-schema-validation-error` (audit codes `C1.HALT_ON_INPUT_VALIDATION_ERROR.001`, `C1.HALT_ON_SCHEMA_VALIDATION_ERROR.001`).
- **Trigger.** Dispatch time — at the engine-constructed dispatch wrapper's two Pydantic-validation boundaries: input-projection validation (values projected into a channel-writing node's dispatch) and output validation (the node's returned dict).
- **Mechanism.** When a value fails its declared schema at either boundary, the runner raises `SchemaValidationError` and the run **halts** — a channel-writing node (transform, service, trainable composition) never proceeds past a schema-invalid value, and the runner never substitutes a default, a coerced value, or a partial result for it. The two checks distinguish the locus by `audit_code` — input-projection validation vs output validation — the distinction owned by [§ SchemaValidationError payload](#schemavalidationerror-payload). Halting on a schema-invalid value is what keeps the captured training record faithful: a coerced-or-defaulted value would record that the node produced a valid X for input Y when it actually produced an invalid value.
- **Violation example.** No author-side TOML triggers this — the violation lives in a non-conformant runner that coerces or defaults instead of halting:

  ```python
  # Non-conformant runner: coerces an out-of-schema output instead of raising
  value = handler(**kwargs)
  try:
      validated = OutputModel(**value)
  except ValidationError:
      validated = OutputModel.construct(**value)  # substitutes an unvalidated
                                                  # value — halt-semantics violation
  ```

- **Error class.** [SchemaValidationError](#schemavalidationerror) — raised at the dispatch boundary; the run halts and the output channel returns nothing (per [no partial output on halt](#no-partial-output-on-halt)).
- **Diagnosis.** A schema-invalid value at either boundary MUST raise `SchemaValidationError` and halt — never a coerced, defaulted, or partial substitute. A fallback is consumer-territory: dispatch on the raised `SchemaValidationError` *above* the engine and decide there. Inside the engine, every dispatch boundary either passes a schema-valid value through or halts.

---

{#hook-returns-non-none}
### Hook returns non-`None`

- **Rule anchor.** [Derived rule R-error-channel-003 (halt semantics)](#error-channel-derived-rules); [hook in handler-kinds](#the-hook-kind). Cross-reference: the hook-returns-`None` check in the handler conformance checks.
- **Trigger.** Dispatch (per hook invocation), at the engine-constructed dispatch wrapper's return-time check.
- **Mechanism.** The engine-constructed dispatch wrapper invokes the inner function and asserts the return value is `None` before the dispatch callable returns. Hooks occupy the observer node role: they write no channels and the runner has no merge path for a hook return. A hook returning non-`None` is claiming a channel write that no graph position admits — from the error-channel perspective, this is a halt-semantics violation because honoring the return would require the runner to route an undeclared channel value, which `ContractViolation` is the correct response to.
- **Violation example.**

  ```python
  # Hook returning a dict — ContractViolation at dispatch
  def log_dialogue(*, pipeline_run_id, dialogue):
      _emit(pipeline_run_id, dialogue)
      return {"emitted_at": _now()}     # hooks return None by contract
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Confirm the handler kind. If the handler genuinely needs to write a channel — e.g., a timestamp or a structured record the next node reads — it is a transform or a service, not a hook. Change the top-level kind header from `hook` to the appropriate kind, remove `transport_schema`, add `output_schema` declaring the [output port](#output-port) the handler writes (routed onto a channel by the node's [write-map](#write-map)), and update the pipeline entry. If the return value was a debug artifact, route it through `logging` or `print` rather than the return dict.

---

{#runner-carries-no-retry-surface-absence-of-api}
### Runner carries no retry surface (absence-of-API)

- **Rule anchor.** [Derived rule R-error-channel-002 (no engine retry API)](#error-channel-derived-rules).
- **Trigger.** Two surfaces: (1) handler-declaration load, when a declaration attempts to declare retry configuration via an unknown block; (2) engine implementation — the runner's dispatch path carries no retry wrapper.
- **Mechanism.**
  - **Declaration surface.** The closed handler-declaration shape grammar ([R-handler-006 in handler/reference.md](#handler-derived-rules)) rejects any block outside the per-kind declared set at handler-declaration load. A `retry_policy` block or any analogous retry-configuration block raises `ContractViolation` immediately — no engine-declared block for retry configuration exists.
  - **Implementation surface.** The runner's dispatch path carries no `max_retries` parameter, no retry-count variable, and no retry wrapper in the engine's own code. Verification: `grep -r "max_retries\|retry_wrapper\|retry_count" src/conjured/` must return no hits in runner or dispatch-path modules. Service-type implementations (in libraries like `acme_llm`) MAY carry transport-retry logic internally — that is impl-internal and sanctioned; this check applies to engine runner code only.
- **Violation example.**

  ```toml
  [transform]

  [reads]
  in = { type = "str" }

  [output_schema]
  out = { type = "str" }

  [retry_policy]         # unknown block — ContractViolation at handler-declaration load
  max_retries = 3
  ```

- **Error class.** [ContractViolation](#contractviolation) for the declaration surface. No runtime error class for the implementation surface — the property is enforced by structural absence, not by runtime detection.
- **Diagnosis.** Remove retry-configuration blocks from handler declarations; no engine-declared home for them exists. For transport recovery (triggered by a transport fault before a usable response exists — a transient connection reset, 5xx, or timeout), implement inside the service-type adapter — impl-internal retry does not flow through any engine retry surface because no engine retry surface exists. For semantic retry (triggered by a verdict on the response — including a verdict-driven resend of identical bytes), route at the consumer multi-pipeline orchestration layer; each re-invocation is a separate pipeline run with its own [channel-record correspondence](#channel-record-correspondence). See [R-handler-002 (no silent fallbacks)](#handler-derived-rules) for the review-enforced prohibition on semantic retry inside handler bodies.

---

{#no-partial-output-on-halt}
### No partial output on halt

- **Rule anchor.** [Derived rule R-error-channel-004 (channel separation)](#error-channel-derived-rules).
- **Trigger.** Per pipeline run, after any error class is raised from a transform or service dispatch.
- **Mechanism.** The runner does not construct any output return value on the halt path — it raises the error class directly. Any runner implementation that catches the raised error class and returns a partial-state value, a discriminated-union result, or an error-annotated wrapper is non-conformant.
- **Violation example.** No author-side TOML triggers this; the violation lives in runner implementation.

  ```python
  # Non-conformant: catches halt and returns instead of raising
  try:
      result = dispatch_all_handlers(pipeline, state)
      return RunResult(state=result)
  except ContractViolation as e:
      return RunResult(state={}, error=e)  # violates channel separation

  # Conformant: raises on halt; returns RunResult only on success
  result = dispatch_all_handlers(pipeline, state)   # raises on halt
  return RunResult(state=result)
  ```

- **Error class.** Runner implementation property. On halt, the runner raises [ContractViolation](#contractviolation), [SchemaValidationError](#schemavalidationerror), or [PipelineFailure](#pipelinefailure). The output channel returns nothing and raises nothing.
- **Diagnosis.** If a caller receives a return value after a handler raised, the runner is non-conformant. Every halt path in the runner must propagate the raised error class rather than converting it to a return value. The existence of a RunResult IS the success signal; its absence (exception) IS the halt signal.

---

{#http-error-response-content-type-is-applicationproblemjson}
### HTTP error response `Content-Type` is `application/problem+json`

- **Rule anchor.** [Derived rule R-error-channel-005 (RFC 9457 HTTP wire projection)](#error-channel-derived-rules).
- **Trigger.** HTTP error-response emission at runtime — any error class halts a pipeline and the engine's HTTP error-response handler emits a response body.
- **Mechanism.** The engine's HTTP error-response handler MUST set `Content-Type: application/problem+json` on every error response body shaped per RFC 9457. A violation means the RFC 9457 envelope body is present but the Content-Type header is absent or carries the wrong MIME type.
- **Violation example.**

  ```
  HTTP/1.1 400 Bad Request
  Content-Type: application/json          ← violation: must be application/problem+json

  {"type": "about:blank", "title": "Field discipline violation", ...}
  ```

- **Error class.** Not a pipeline error class — this is a server-configuration conformance failure. Detection belongs in the engine's server-layer integration tests, not in the runner's error-class machinery.
- **Diagnosis.** Verify the HTTP error-response handler (or its middleware wrapper) sets `Content-Type: application/problem+json` for all 4xx–5xx responses whose body is RFC 9457-shaped. The correct value is exactly `application/problem+json` — not `application/json`, not `application/json; charset=utf-8`. The MIME type is RFC 9457's registered content type.

---

{#payload-completeness-every-raised-error-carries-its-declared-field-set}
### Payload completeness — every raised error carries its declared field set

- **Rule anchor.** [§ Error payload field set](#error-payload-field-set).
- **Trigger.** Test time — the engine's own test suite asserts payload completeness over every raise site. The property is a static fact about the engine's error-construction code (which fields each raise site populates), not a per-dispatch or per-startup runtime check.
- **Mechanism.** The engine's payload-completeness test asserts that every code path emitting a `ContractViolation`, `SchemaValidationError`, or `PipelineFailure` instance populates all required fields for that class. For `ContractViolation`, the test additionally asserts that at least one of `file_path` or `composition_ref` is non-null on every raise site. Missing required fields, or `ContractViolation` raise sites with both location-bearing fields null, fail the test.
- **Violation example.**

  ```python
  # ContractViolation raised without file_path AND without composition_ref
  # — location-bearing field requirement violated; runner-construction halt
  raise ContractViolation(
      audit_code="C2.FIELD_DISCIPLINE.001",
      rule_id="R-handler-006",
      expected="declared key per the handler's declared grammar",
      actual="unknown key 'mood_modifier'",
      message="...",
      file_path=None,
      composition_ref=None,   # both absent — defective raise site
  )
  ```

- **Error class.** Check failure (the payload-completeness test fails; non-zero exit). No runtime error class — the engine is the error producer, and no in-flight consumer intercepts a defective payload, so the assurance lives in the engine's test layer rather than in a runtime guard.
- **Diagnosis.** The required-field set per class is owned by [error-channel § Error payload field set](#error-payload-field-set) — confirm, against that section's per-class field specifications, that every required field for the raised class is non-null at every raise site. (For `ContractViolation`, `audit_code` is non-null only once the audit catalog assigns the code — see [error-channel § ContractViolation with no assigned audit_code](#contractviolation-audit-code-absent).)

---

{#error-channel-review-enforced-checks}
## Review-enforced checks

{#in-body-except-in-any-handler-body}
### In-body `except` in any handler body

- **Rule anchor.** [Derived rule R-error-channel-003 (halt semantics)](#error-channel-derived-rules); [derived rule R-handler-002 (no silent fallbacks)](#handler-derived-rules).
- **Trigger.** Review pass over each handler module's source.
- **Mechanism.** Pattern-match for any `except` clause in a handler body — including `except Exception: pass`, `except Exception: return default_value`, and `except SpecificError:` that catches one of the three engine error classes or their operational subtypes. The runner delivers halt semantics via the dispatch boundary and, for hooks, via the engine-owned hook wrapper. A handler body that interposes its own `except` bypasses both mechanisms: it absorbs a failure that the halt rule requires to surface, and returns a schema-valid value the runner cannot distinguish from a runtime-derived result.
- **Violation example.**

  ```python
  # Transform body catching internal failure — named halt-semantics violation
  def normalize_charset(*, player_input, config):
      try:
          return {"normalized_input": _strip(player_input, config["marker_set"])}
      except Exception:
          return {"normalized_input": player_input}    # silent fallback; violation
  ```

  ```python
  # Hook body catching operational failure — named halt-semantics violation.
  # The hook wrapper already handles this; in-body except bypasses it.
  def log_dialogue(*, pipeline_run_id, dialogue):
      try:
          _emit(pipeline_run_id, dialogue)
      except Exception:
          pass                # in-body except in a hook body; violation
      return None
  ```

- **Diagnosis.** The test: does this `except` clause return a schema-valid value that masks internal failure, or does it perform genuinely safe resource cleanup before re-raising? Safe cleanup that re-raises (e.g., `finally:` releasing a file handle) is not a violation. An `except` that returns, passes, or substitutes a default without re-raising is a named violation regardless of handler kind. For hooks: the engine-owned wrapper already handles operational `PipelineFailure`; in-body `except` in a hook body is redundant and a violation. Remove the `except` clause and let the dispatch boundary and (for hooks) the engine wrapper deliver halt semantics.

---

{#generated-artifact-freshness-checks}
## Generated-artifact freshness checks

{#generated-artifact-freshness-error-index}
### Generated-artifact freshness (`gen_error_index.py --check`)

- **Rule anchor.** The error-index codegen contract
  ([error-channel reference § Error-index codegen](#error-index-codegen)). An
  infrastructure check, not a derived rule from `docs/reference/principles.md`;
  no `rule_id` citation. It enforces that the two generated artifacts —
  `docs/reference/error-index.md` and `src/conjured/agent/error-classes.toml` —
  match a fresh derivation from the engine's error registration API.
- **Trigger.** Test time — the engine's test suite runs
  `tools/gen_error_index.py --check`; the generator is also runnable directly.
- **Mechanism.** `--check` re-derives both artifacts in memory from the current
  registration API (`conjured.errors`' registries), normalizes line endings, and
  compares each derivation against the committed file. The comparison covers the
  first-line generated-content marker (which embeds a hash of the file body), so
  a hand-edit, a stale commit after a registry change, and a corrupted marker
  all surface as the same divergence. Any mismatch reports the stale path with a
  regenerate instruction and exits non-zero.
- **Violation example.** A developer hand-adds a row to
  `docs/reference/error-index.md`, or registers a new check in `conjured.errors`
  without re-running the generator — either way the fresh derivation differs
  from the committed file and the check fails:
  `STALE: <path> — regenerate via tools/gen_error_index.py`.
- **Error class.** Check failure (non-zero exit; the pinning test fails). No
  runtime [ContractViolation](#contractviolation) or
  [SchemaValidationError](#schemavalidationerror) is raised — this is
  infrastructure-only.
- **Diagnosis.** Run `python tools/gen_error_index.py` to regenerate both
  artifacts from the current engine code and commit the result. If the
  divergence was an intentional new error, register it at the registration API
  in `conjured.errors` first (the constructors reject unregistered entries),
  then regenerate.
