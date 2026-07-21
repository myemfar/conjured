# Trainable-backend audit — adapter modules

> **Who you are.** A fresh review session — an agent, or a human maintainer following this
> as a checklist. You are **not** the session that authored the adapter under review; its
> code is the *subject* of this review, not your work to defend. Your charge is bounded and
> stated below; nothing outside it is yours to fix.
>
> **What this is.** The engine-shipped review instrument for a **consumer-supplied
> trainable-backend adapter** — the certification path the handler reference's *Trainable
> backends* section names for the narrow tail the engine's native adapters do not cover (an
> Apple-MLX runtime, a direct constrained-decoding-library binding). The compose-time gate
> admits a `trainable.service_bindings` binding when its resolved adapter is
> **native-by-construction** (an engine `conjured.lib.*` adapter, resolved through the
> native adapter table) or a **consumer adapter carrying a fresh pass-grade audit stamp** —
> this review, passed, minted as the adapter module's sibling stamp. You are that stamp's
> only issuer.

## Scope — one adapter per audit

Review **one** candidate adapter: its Python module and its service-type declaration TOML,
and nothing else. **Certify or refuse — never partially.** A property you cannot verify
from the source in front of you is a **fail**, not a benefit of the doubt: the stamp
asserts the corpus this backend captures is trustworthy training data, and doubt is
corruption.

This audit is the **review half** of the trainable-backend contract. The engine already
enforces its mechanical half at resolution and compose — do not re-audit these, but know
that a candidate failing any of them never reaches your property review:

- **Class shape + vector-7 purity** — the adapter is a class; no above-instance-scope
  mutable state, no caching decorators, no import-time I/O (`R-handler-pure-module`,
  adapter scope). Instance state on `self` is the only admissible mutable state.
- **The closed `invoke()` signature** — keyword-only, exactly the engine dispatch-kwargs
  plus one kwarg per declared `[config_schema]` field plus a `**transport_extra`
  collector (`R-service-type-002` / `R-service-type-003`).
- **The two immutable certification attributes** — a non-empty `training_artifact_contract`
  string and a `reserved_wire_keys` `frozenset[str]`; the gate rejects the binding if
  either is absent or malformed (the handler reference's *Trainable backends* section,
  `R-handler-008` expansion).
- **Constraint expressibility** — a `trainable.output_schema` keyword the bound wire
  family cannot enforce is rejected at compose (`R-handler-005`, the
  trainable-constraint-unsupported check).

## What to check — cite the rule, attack the source

For each item, the **rule** is canon (named by id + section — read its statement in the
shipped canonical docs; do not take this prompt's word for what it requires). What this
prompt supplies is the **adversarial process**: where to look and how to attack. Grade
each against the rule as canon states it.

### The four properties (handler reference, *Trainable backends*)

1. **Server-side decode-time seal** — property 1, `R-handler-005` (the literal-equal rule).
   *Attack:* where **exactly** is the output constrained? If the adapter parses the
   response and retries / repairs / re-prompts on mismatch, **fail** — a client-side
   parse-and-retry wrapper can emit a non-conforming value and throw, breaking the
   schema-IS-the-constraint identity. Trace the constraint artifact from the declared
   `output_schema` to the wire: is there any branch, config, or transport value that
   sends a call **without** the constraint attached? Does the adapter validate, coerce,
   default, or "fix up" the emission before returning it? Count the wire calls per
   `invoke()` — more than one (retry loop, fallback endpoint, best-of-N) is a fail.
2. **Fine-tunable open weights the consumer owns** — property 2. *Attack:* any baked-in
   endpoint, vendor SDK default, or hosted-API fallback → fail (the serving runtime is
   named **only** by the deployment's transport). Can the targeted backend family actually
   be fine-tuned and self-hosted? A frozen hosted endpoint is a service-kind backend, not
   a trainable one → fail.
3. **A standard training-artifact contract** — property 3. *Attack:* the adapter names its
   artifact family in the immutable `training_artifact_contract` label. The engine records
   it opaquely (it reads the trained artifact by path, never interpreting the value), so
   your bar is **not** membership in a fixed set — it is the property canon states:
   *portable and self-servable*. A bespoke non-empty label naming a portable, self-servable
   family is admitted; an absent, empty, or non-portable one is a fail.
4. **A clean read/write seal — no service-side pre/post-processing** — property 4,
   `R-handler-011` (prompt-shaping routes through an upstream preprocessor, never the
   backend). *Attack:* diff what `invoke()` sends against `input_payload` — any template
   applied, system prompt injected, few-shot examples added, fields dropped or reordered,
   content rewritten → fail (a shaped submission makes the captured training-pair input a
   lie). Diff what `invoke()` returns against the backend's emission — any added metadata,
   renamed keys, post-processing, filtering → fail. Is the reads→wire serialization
   deterministic (same `input_payload` → byte-identical submission)? Does any dispatch
   identity kwarg (`service_name`, `caller_qualified_name`, `caller_position`) leak into
   the wire payload? Either → fail.

### Construction & identity

5. **Compose-time constraint derivation** — the construction contract the *Trainable
   backends* compose-time caveat states. *Attack:* the decode-constraint derivation runs
   **in the constructor** (= compose time); a schema the backend's constraint mechanism
   cannot enforce raises there, never a silent best-effort and never a dispatch-time
   surprise. Confirm the derivation is genuinely server-enforceable, not a hand-authored
   constraint separate from the declared `output_schema`.
6. **No author-shaping identity** — `R-handler-011`. *Attack:* the service-type's
   `[identity_schema]` carries no prompt template, system prompt, or content-shaping
   selector — shaping content reaching a trainable service-side smuggles past the
   preprocessor boundary.
7. **Reserved-wire-key coverage** — the judgment the mechanical check cannot make.
   The engine verifies `reserved_wire_keys` is a `frozenset[str]`; it **cannot** verify
   the set actually **covers every key `invoke()` writes to the wire**. *Attack:* enumerate
   the keys `invoke()` constructs (the dial core plus every structural key) and confirm
   each is in `reserved_wire_keys`. An under-declared set lets an author's `extras`
   silently override an engine-written wire key — a real defect the disjointness check
   would then miss.

### Failure honesty — `R-handler-002`

*Attack:* wire/protocol failures (HTTP error, truncation, refusal, unparseable body) must
raise — raw or as a dedicated runtime error — and must **never** be masked with a
schema-valid value. No swallowed exceptions, no `except: return default`, no
logging-instead-of-raising on any path between the wire and the return. In this engine a
graceful degrade is training-data corruption.

## Grading

- **`pass`** — every precondition and all checks above hold, with cited line-level evidence.
- **`pass-with-notes`** — all hold, with real minor observations recorded (never papering
  over a violation).
- **`fail`** — any check is violated, or any property cannot be verified from the source in
  front of you. Do not certify a "mostly passing" adapter.

## Deposit the findings, then mint the stamp

**1. Write the findings report.** Per-property verdict with line-level evidence, every
note, and for a `fail` each failed check with its evidence. Deposit it in this package's
`findings/` directory as `findings/<module-stem>.md` (for `my_backend.py`,
`findings/my_backend.md`). Shipped conformance content: self-contained, no session
narrative.

**2. Mint the sibling stamp.** Write `<module-stem>.audit.toml` **beside the audited
adapter module** (same directory, same stem: `my_backend.py` → `my_backend.audit.toml`) —
this is what the engine reads at resolution. A flat TOML with exactly these five fields:

```toml
source_hash = "<64-hex>"        # bare lowercase sha256 hex of the adapter module's raw file bytes
audit_prompt_hash = "<64-hex>"  # bare lowercase sha256 hex of THIS prompt file's raw bytes
verdict = "pass"                # one of: pass, pass-with-notes, fail
date = "2026-01-01"             # the audit date, ISO 8601 (YYYY-MM-DD)
findings = "conjured.conformance/findings/my_backend.md"   # pointer to the report from step 1
```

Compute each hash over the **raw file bytes**:

```bash
python -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <file>
```

— once for the adapter module (`source_hash`), once for this prompt file
(`audit_prompt_hash`).

**Only pass-grades count.** The engine treats `pass` and `pass-with-notes` as fresh; a
`fail` stamp refuses compose under `audit_enforcement` exactly as an absent stamp does.
Any edit to the adapter module changes its bytes and **stales the stamp** — the adapter
must be re-audited and re-stamped. Stamping an edited adapter without re-review forges the
certification.

## Auditor ≠ fixer

If your verdict is `fail`, **REFUSE** — record the findings and the `fail` stamp, and
**stop**. Do not fix the adapter in this session. The reviewer and the fixer stay
separate: a fix lands in its own session, and the fixed adapter is re-audited from a clean
review before it earns a pass-grade stamp.
