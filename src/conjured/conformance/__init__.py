"""``conjured.conformance`` — the in-package conformance kit.

The engine-shipped conformance audit prompts plus the native members' audit
**findings reports**, reached at runtime via
``importlib.resources.files("conjured.conformance")``.

**What ships here.**

- ``module-conformance-audit.md`` — the review instrument for **handler** modules
  (transform / service / hook bodies) and **validator** modules: the review-enforced
  conformance the engine's mechanical AST audit cannot reach (body semantics,
  judgment-call import discipline).
- ``trainable-backend-audit.md`` — the review instrument for **adapter** modules: an
  adapter's conduct plus the trainable-backend property contract, the certification
  path a consumer-supplied trainable backend is admitted through.
- ``findings/<module>.md`` — the deposited findings report for each audited native
  member, the worked example an author reads to see how a native module was audited
  and stamped.

**The stamps themselves are elsewhere — deliberately.** An audit stamp is the sibling
``<module>.audit.toml`` beside the audited source file (same stem), because the engine
verifies stamp freshness against the module it sits next to. The native members' stamps
therefore live beside their modules under ``conjured.lib``; the audit-stamp mechanism
(the handler reference's *Audit stamps* section) owns the stamp shape and the
resolution-time freshness check. This package holds the **prompts** an auditor runs and
the **findings** the run deposits — not the stamps.

**The kit is a product feature.** An author writing a custom handler, validator, or
trainable-backend adapter feeds the matching prompt to their own review agent (or works
it as a human checklist) to check the conformance a test suite cannot, then records the
result as their module's sibling stamp — the same protocol the native members were
stamped under, each prompt carrying its own minting instructions.
"""
