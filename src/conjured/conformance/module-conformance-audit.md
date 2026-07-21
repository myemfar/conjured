# Module conformance audit — handler & validator modules

> **Who you are.** A fresh review session — an agent, or a human maintainer following
> this as a checklist. You are **not** the session that authored the module under review,
> and you carry no obligation to defend it: its code is the *subject* of this review, not
> your work. Your charge is bounded and stated below; nothing outside it is yours to fix.
>
> **What this is.** The engine-shipped review instrument for a **handler** module (a
> `transform`, `service`, or `hook` bare-function handler) or a **validator** module
> (a bare kwarg-only validator function). It audits the review-enforced conformance the
> engine's mechanical checks **cannot** reach: the **body semantics** and **judgment-call
> import discipline** that live inside the function, past what a source-AST walk can see.

## Scope — one module per audit

Review **one** module: its Python source and its declaration TOML, and nothing else. A
module mixing handler and validator functions is audited against whichever contract each
function falls under. **Grade the module as a whole** — one verdict, one stamp.

This audit is the **review half** of a contract whose mechanical half the engine already
enforces at resolution. It is not your job to re-check the mechanical half, and a module
that fails it never reaches you — but knowing where the line falls keeps your findings on
the review side:

- **Already enforced mechanically** (do not re-audit): the source-AST audit that forbids
  module-level mutable state, caching decorators at any scope, and import-time I/O
  (`R-handler-pure-module`); the bare-kwarg-only function shape (the vector-2 seal); the
  compose-time signature-union check. The engine raises on these before dispatch.
- **Your charge** (review-enforced, un-mechanizable): everything below.

## What to check — cite the rule, probe the body

For each item, the **rule** is canon (named by id — read its statement in the shipped
canonical docs; do not take this prompt's word for what it requires). What this prompt
supplies is the **audit process**: where to look and how to attack. Grade each against the
rule as canon states it, not against a paraphrase.

### Handler bodies (`transform` / `service` / `hook`)

1. **Body purity & determinism** — `R-handler-004` (transform purity); for a `service`
   body the non-trainable external-call carve-out its own kind allows. *Probe:* does the
   body compute its return **solely** from its declared input-port kwargs and
   `bindings.<name>` values? Hunt for a read of state that does **not** flow from a
   declared input — a module or class global, an environment variable, a clock, a
   random source, a filesystem or network reach in a `transform`. Ask the determinism
   question canon poses: identical reads and bindings on every call → identical return?
2. **No silent fallbacks** — `R-handler-002`. *Probe:* trace every failure path from an
   external call, a parse, or a lookup. Does any of them swallow the failure and return a
   schema-valid stand-in (a default, an empty value, a cached prior, a "best effort")?
   A body that can fail but returns a plausible value instead of raising is the target —
   in this engine a graceful degrade is training-data corruption, not resilience.
3. **Body import discipline** — `R-handler-007`. *Probe:* the judgment call the AST walk
   cannot make — are the module's imports honest and side-effect-free in what they pull
   in transitively, and does nothing import-inside-the-body smuggle in per-call state or
   defer a forbidden reach past the module-scope audit?
4. **The return contract** — `R-handler-001` (a bare-function handler returns its declared
   `output_schema` as a fresh dict; a `hook` returns `None`). *Probe:* every return path.
   Does a `hook` ever return a non-`None` value? Does a `transform`/`service` ever return
   a key it did not declare, or mutate and return an input rather than a fresh dict?

### Validator bodies

5. **Validator purity, determinism & verdict shape** — `R-handler-013` (validator bodies
   are pure and deterministic) and the validator contract `R-handler-012` owns. *Probe:*
   the body returns `None` (pass) or a **one-line failure string** (fail), and does
   nothing else — no external reach, no non-determinism, no mutation of `value`. Confirm
   it does not **raise** to signal an ordinary failed check: under the contract a raise is
   the validator reporting its *own* failure, surfaced as a pipeline failure, never a
   validation verdict — so a body that raises on a value it means to *reject* is a defect.

### Cross-cutting

6. **Fail loud, no hidden state** — the engine is a developer tool; loud failures and
   visible state are correct. *Probe:* any `except: pass`, any log-instead-of-raise on a
   path that should fail, any state hidden behind a name that a later reader would not
   expect to carry meaning across calls.

## Grading

Assign exactly one verdict:

- **`pass`** — every check above holds, with cited line-level evidence.
- **`pass-with-notes`** — every check holds, but the audit records real, minor
  observations worth a reader's attention (a fragile-but-correct construction, a naming
  smell that is not a violation). Notes never paper over a violation.
- **`fail`** — any check is violated, or any property cannot be verified from the source
  in front of you. Absence of evidence is a fail, not a benefit of the doubt: the stamp
  asserts this module's conformance, and doubt is not conformance.

## Deposit the findings, then mint the stamp

**1. Write the findings report.** A markdown report — per-check verdict with line-level
evidence, every note, and for a `fail` each violated check and why. Deposit it in this
package's `findings/` directory as `findings/<module-stem>.md` (for a module
`my_handlers.py`, `findings/my_handlers.md`). The report is shipped conformance content:
self-contained, no session narrative.

**2. Mint the sibling stamp.** Write `<module-stem>.audit.toml` **beside the audited
module** (same directory, same stem: `my_handlers.py` → `my_handlers.audit.toml`). This is
what the engine reads. It is a flat TOML file with exactly these five fields:

```toml
source_hash = "<64-hex>"        # bare lowercase sha256 hex of the audited module's raw file bytes
audit_prompt_hash = "<64-hex>"  # bare lowercase sha256 hex of THIS prompt file's raw bytes
verdict = "pass"                # one of: pass, pass-with-notes, fail
date = "2026-01-01"             # the audit date, ISO 8601 (YYYY-MM-DD)
findings = "conjured.conformance/findings/my_handlers.md"   # pointer to the report from step 1
```

Compute each hash over the **raw file bytes** (not a normalized or re-encoded form):

```bash
python -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <file>
```

— once for the module (`source_hash`), once for this prompt file (`audit_prompt_hash`).

**Only pass-grades count.** The engine treats `pass` and `pass-with-notes` as fresh; a
`fail` stamp refuses compose under enforcement exactly as an absent stamp does. Any later
edit to the module changes its bytes and **stales the stamp** — the source hash no longer
matches, and the module must be re-audited and re-stamped.

## Auditor ≠ fixer

If your verdict is `fail`, **REFUSE** — record the findings and the `fail` stamp, and
**stop**. Do not fix the module in this session. The reviewer and the fixer stay separate:
a fix lands in its own session, and the fixed module is re-audited from a clean review
before it earns a pass-grade stamp. Stamping a module you also fixed forges the audit.
