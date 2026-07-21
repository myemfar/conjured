"""Audit-stamp freshness check — resolution-time verification of the sibling
``<module>.audit.toml`` stamp (``conjured/docs/components/handler/reference.md``
§ Audit stamps, region ``audit-stamps/kernel``).

The review-enforced rule family over resolved modules — the conduct
R-handler-pure-module's mechanical AST walk cannot check (body semantics, judgment-call
import discipline, adapter conduct, the trainable-backend property contract) — is verified
by a **dated audit**: an LLM or human auditor runs the shipped audit prompt over a module
and records the result as a **sibling stamp**, ``<module>.audit.toml`` beside the audited
source file (same stem). The engine never re-runs an audit; it verifies **freshness** at
resolution, hashing the module source bytes it **already read for the pre-import AST walk**
(near-free — no second source read; the same bytes flow in here) and comparing them to the
sibling stamp.

**The 4-state model** (§ audit-stamps kernel). A module's stamp state is:

- **fresh** — the recorded ``source_hash`` matches the current source AND the recorded
  ``verdict`` is a pass-grade (``pass`` / ``pass-with-notes``);
- **stale** — the source changed since the stamp (hash mismatch);
- **absent** — no stamp file exists (the normal not-yet-audited state);
- **failed** — the hashes match but the recorded verdict is not a pass-grade.

**Enforcement-gated** (decided design; the ``audit_stamp_changed`` event is deferred, so
refusal under ``audit_enforcement`` is the stamp's ONLY compose-time surface). The engine
reads ``.audit.toml`` **only under** the deployment's ``audit_enforcement`` opt-in
(``ir.deployment.TrainingContract``) — with the opt-in absent/false there is
no consumer, no read, no consequence (stamps are then tool-facing artifacts). Under
enforcement, any not-fresh state refuses compose with a structured ``ContractViolation``
(``AUDIT_STAMP_NOT_FRESH``). The read itself is therefore behind the caller's
``audit_enforcement`` gate — this module is invoked only when the caller has decided to
enforce.

**Fail loud on a corrupt artifact** (decided design). A stamp file that exists but is
unreadable, is not valid TOML, is missing a closed field, or carries a mistyped/out-of-enum
field is **malformed** — a structured ``ContractViolation`` (``AUDIT_STAMP_MALFORMED``),
never coerced to ``absent`` and never a raw exception. This mirrors
``resolve_handler._read_and_audit_adapter_source``'s posture (a structured CV on an
unreadable source, never a raw ``OSError``): a corrupt engine-read artifact fails loud.

**Scope of comparison.** Freshness compares ``source_hash`` (the module source) and
``verdict``. ``audit_prompt_hash`` is a closed field the engine parses and type-checks but
does **not** compare at compose this pass — canon's concrete fresh condition is
source-match + pass-verdict; the module source is in memory at resolution, the shipped
prompt is not read during a compose. (Compose-time prompt-revision staleness — the engine
hashing the shipped prompt to auto-stale stamps minted under a revised prompt — is a
deferred refinement that needs a prompt-lookup mechanism not yet defined.)
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from pathlib import Path

from conjured.errors import Check, ContractViolation

#: Rule the stamp check enforces: the audit stamp is the dated human/LLM-audit complement of
#: R-handler-pure-module — the review-enforced family the mechanical AST walk cannot reach
#: (§ audit-stamps kernel: "the conduct R-handler-pure-module's mechanical AST walk cannot
#: check … is verified by a dated audit").
STAMP_RULE_ID = "R-handler-pure-module"

#: The sibling stamp's file suffix — ``<module>.audit.toml`` beside the module source, same
#: stem (``my_handlers.py`` → ``my_handlers.audit.toml``).
STAMP_SUFFIX = ".audit.toml"

#: The stamp's closed field set (§ audit-stamps — "The stamp's closed field set"). Every
#: field MUST be present and well-typed or the stamp is malformed.
STAMP_FIELDS = ("source_hash", "audit_prompt_hash", "verdict", "date", "findings")

#: The verdict closed enum (§ audit-stamps). Only pass-grades count toward freshness.
PASS_GRADES = frozenset({"pass", "pass-with-notes"})
_VERDICTS = frozenset({"pass", "pass-with-notes", "fail"})


def sibling_stamp_path(origin: str | os.PathLike[str]) -> Path:
    """The sibling stamp path for a module source at ``origin`` — same directory, same
    stem, ``.audit.toml`` suffix (``…/my_handlers.py`` → ``…/my_handlers.audit.toml``)."""
    return Path(origin).with_suffix(STAMP_SUFFIX)


def compute_source_hash(source_bytes: bytes) -> str:
    """The stamp's ``source_hash`` derivation — bare lowercase SHA-256 hex over the module
    source bytes (the exact bytes the pre-import AST walk read). A stamp records this value;
    the audit prompt (shipped by the conformance kit) mints it the same way."""
    return hashlib.sha256(source_bytes).hexdigest()


def _malformed(
    *, origin: str, toml_path: str, actual: str, hint: str
) -> ContractViolation:
    # guarantees: audit-stamp-malformed-fails-loud
    return ContractViolation(
        check=Check.AUDIT_STAMP_MALFORMED,
        rule_id=STAMP_RULE_ID,
        expected=(
            f"the sibling audit stamp '{sibling_stamp_path(origin).name}' is a readable "
            f"TOML file carrying the closed field set {list(STAMP_FIELDS)}, each "
            "well-typed (verdict ∈ pass / pass-with-notes / fail)"
        ),
        actual=actual,
        remediation_hint=hint,
        file_path=toml_path,
    )


def _read_stamp(stamp_path: Path, *, origin: str, toml_path: str) -> dict:
    """Read + parse the sibling stamp into a validated closed-field mapping. A file that is
    unreadable, is not valid TOML, is missing a closed field, or carries a mistyped /
    out-of-enum field is **malformed** — a structured ``ContractViolation``, never coerced
    to absent and never a raw exception (fail loud on a corrupt engine-read artifact)."""
    try:
        raw = stamp_path.read_bytes()
    except OSError as exc:
        # The exists() guard the caller ran cannot cover a permission-denied or
        # vanished-after-check stamp — the read stays inside the closed compose-time channel.
        raise _malformed(
            origin=origin, toml_path=toml_path,
            actual=f"the stamp file is unreadable ({type(exc).__name__}: {exc})",
            hint="make the .audit.toml readable (permissions?) and re-compose",
        ) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise _malformed(
            origin=origin, toml_path=toml_path,
            actual=f"the stamp file is not valid UTF-8 TOML ({type(exc).__name__}: {exc})",
            hint="re-run the shipped audit prompt to regenerate a well-formed stamp",
        ) from exc
    missing = [f for f in STAMP_FIELDS if f not in data]
    if missing:
        raise _malformed(
            origin=origin, toml_path=toml_path,
            actual=f"the stamp omits the closed field(s) {missing}",
            hint="a stamp carries exactly source_hash / audit_prompt_hash / verdict / date "
                 "/ findings; re-run the shipped audit prompt to regenerate it",
        )
    # Type-check EVERY closed field as a non-empty string before any value test. verdict is
    # included here (not just enum-tested below) so an array/table verdict — tomllib parses
    # `verdict = ["pass"]` into an unhashable list — fails loud as a structured
    # AUDIT_STAMP_MALFORMED rather than raising a raw `TypeError: unhashable type` from the
    # `in _VERDICTS` frozenset membership. All five stamp fields are strings, so STAMP_FIELDS
    # is the exact set (and single-sources the roster with the closed-field definition).
    for field in STAMP_FIELDS:
        if not isinstance(data[field], str) or not data[field]:
            raise _malformed(
                origin=origin, toml_path=toml_path,
                actual=f"the stamp field '{field}' is {data[field]!r}, not a non-empty string",
                hint=f"'{field}' MUST be a non-empty string; regenerate the stamp",
            )
    if data["verdict"] not in _VERDICTS:
        raise _malformed(
            origin=origin, toml_path=toml_path,
            actual=f"the stamp verdict is {data['verdict']!r}, outside the closed enum "
                   f"{sorted(_VERDICTS)}",
            hint="verdict MUST be one of pass / pass-with-notes / fail; regenerate the stamp",
        )
    return data


def require_fresh_stamp(
    *,
    origin: str,
    source_bytes: bytes,
    toml_path: str,
    what: str = "module",
) -> None:
    """Refuse compose unless the module at ``origin`` carries a **fresh** sibling stamp.

    Called at resolution **only when the caller has opted into enforcement**
    (``audit_enforcement``) — this module never decides the gate, it only computes the state
    and refuses on not-fresh. ``source_bytes`` are the exact bytes the pre-import AST walk
    already read (no second source read). ``what`` names the in-scope module class
    (handler / adapter / validator) for the diagnostic.

    Raises ``ContractViolation`` (``AUDIT_STAMP_NOT_FRESH``) on **absent** / **stale** /
    **failed**; ``AUDIT_STAMP_MALFORMED`` on a corrupt stamp artifact (via
    :func:`_read_stamp`). Returns ``None`` on a fresh pass-grade stamp."""
    stamp_path = sibling_stamp_path(origin)
    if not stamp_path.is_file():
        # ABSENT — the normal not-yet-audited state; refused under enforcement.
        # guarantees: audit-stamp-absent-refused
        raise _not_fresh(
            origin=origin, toml_path=toml_path, what=what, state="absent",
            detail=f"no sibling audit stamp '{stamp_path.name}' exists beside the {what} module",
            hint=f"run the engine-shipped conformance audit prompt against this {what} and "
                 "record the result as its sibling .audit.toml (the audit-stamp kit)",
        )
    stamp = _read_stamp(stamp_path, origin=origin, toml_path=toml_path)
    current_hash = compute_source_hash(source_bytes)
    if stamp["source_hash"] != current_hash:
        # STALE — the source changed since the stamp was minted.
        # guarantees: audit-stamp-stale-refused
        raise _not_fresh(
            origin=origin, toml_path=toml_path, what=what, state="stale",
            detail=f"the {what} module source changed since the stamp was minted "
                   f"(recorded source_hash != current source hash)",
            hint=f"re-run the audit prompt against the edited {what} and re-stamp — any "
                 "edit to a stamped module stales its stamp structurally",
        )
    # A hash match with a non-pass verdict is FAILED, never fresh — the fail-verdict hole
    # (Engine historical incidents): "matching hash ⇒ fresh" would admit a module the audit
    # explicitly failed. The verdict gate closes it.
    if stamp["verdict"] not in PASS_GRADES:
        # FAILED — hashes match but the recorded verdict is not a pass-grade.
        # guarantees: audit-stamp-fail-verdict-refused
        raise _not_fresh(
            origin=origin, toml_path=toml_path, what=what, state="failed",
            detail=f"the stamp's recorded verdict is '{stamp['verdict']}', not a pass-grade "
                   f"(pass / pass-with-notes) — the audit failed this {what}",
            hint=f"address the audit findings ({stamp['findings']}) and re-audit to a "
                 "pass-grade verdict before composing under audit_enforcement",
        )
    # FRESH — hash match + pass-grade verdict.


def _not_fresh(
    *, origin: str, toml_path: str, what: str, state: str, detail: str, hint: str
) -> ContractViolation:
    return ContractViolation(
        check=Check.AUDIT_STAMP_NOT_FRESH,
        rule_id=STAMP_RULE_ID,
        expected=(
            f"under audit_enforcement, the resolved {what} module carries a **fresh** "
            "audit stamp (sibling .audit.toml: recorded source_hash matches the current "
            "source AND verdict is a pass-grade)"
        ),
        actual=f"the stamp is {state} — {detail}",
        remediation_hint=hint,
        file_path=toml_path,
    )
