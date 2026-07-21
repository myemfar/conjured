#!/usr/bin/env python3
"""gen_error_index.py — the R4 codegen step: generate the error → rule cross-reference
artifacts from the engine's **registration API** (the STUB-R4 ruling, 2026-06-10: the
build imports the engine and reads the registered error set — never an AST walk).

Inputs (the registered error set + the canon rule corpus):
  - ``conjured.errors.CHECK_REGISTRY`` — every ``Check`` member → enforced rule_ids +
    raising error class (constructor-sealed: a raise site cannot emit an unregistered
    pair, so the generated index is complete by construction).
  - ``conjured.errors.AUDIT_CODE_REGISTRY`` — the decided catalog codes (three today: the
    two SVE boundary codes + the PipelineFailure-wrap audit; grows as the audit
    catalog assigns codes incrementally).
  - The canonical docs' ``rules:`` blocks (via the canon corpus loader) — the
    rule_id → name/statement/owning-doc join.

A rule ``statement`` ships verbatim into ``error-classes.toml``, so a statement that
single-sources a definition by ``:::{transclude} <id>`` must ship the RESOLVED owner
text, never the literal directive. Before emitting, every ``:::{transclude}`` in a
statement is expanded to its owner's body — a ``:::{region} <id>`` span or a referenced
rule's statement — using the MyST colon-fence directive grammar the documentation build
resolves. **Corpus reading is shared with the sibling docs-site build**
(``docs_site.preprocess``: frontmatter split, region collection, rules-block reading —
the same one-reader posture ``build_agent_surface.py`` takes), so this generator can
never read the corpus differently than the other two projections; only the transclude
*resolution policy* stays local (fail-loud on an unresolved id or a cycle — the shipped
artifact admits no placeholder degrade).

Outputs (each carrying a generated-content marker on line 1 whose hash covers the
LF-normalized body — the marker convention the attestation corpus hash recognizes, so
it skips both):
  - ``docs/reference/error-index.md`` — the consumer-facing rendered cross-reference
    (error-channel/reference.md § Error-index codegen).
  - ``src/conjured/agent/error-classes.toml`` — the machine-readable agent-surface
    companion, shipped in the wheel via ``importlib.resources``.

Modes: default regenerates both files; ``--check`` re-derives both in memory and
exits non-zero if either on-disk artifact is stale (the freshness gap F-PB-3 names,
closed for these two artifacts).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.colon_fence import colon_fence_plugin

TOOLS_DIR = Path(__file__).resolve().parent
PKG_DIR = TOOLS_DIR.parent  # conjured/
sys.path.insert(0, str(PKG_DIR / "src"))
sys.path.insert(0, str(TOOLS_DIR))  # sibling package: docs_site (the shared corpus readers)

from conjured.errors import AUDIT_CODE_REGISTRY, CHECK_REGISTRY, Check  # noqa: E402
from docs_site.preprocess import (  # noqa: E402 — one corpus reader across the shipped tools
    region_directives,
    rule_entries,
    split_frontmatter,
)

DOCS_ROOT = PKG_DIR / "docs"
INDEX_PATH = DOCS_ROOT / "reference" / "error-index.md"
TOML_PATH = PKG_DIR / "src" / "conjured" / "agent" / "error-classes.toml"


# ---------------------------------------------------------------------------
# The rule join — rule_id → (name, statement, owning doc) from the canon corpus.
# Read through the SHARED corpus readers (``docs_site.preprocess.rule_entries`` — every
# ```yaml fence of every doc, the same semantics the docs-site and agent-surface builds
# apply), skipping generated artifacts (this generator's own outputs contribute nothing).
# ---------------------------------------------------------------------------

_GENERATED_RE = re.compile(r"^[#<!\-\s]*GENERATED\s*[—-]+\s*DO NOT EDIT", re.IGNORECASE)


def _is_generated(raw: str) -> bool:
    """A generated build artifact — the line-1 generated-content marker, or (for the
    generated .md, whose frontmatter must sit at byte 0) the marker on the first line
    after the closing ``---``. Local policy: generated outputs define no rules/regions."""
    first = raw.splitlines()[0] if raw else ""
    _, body = split_frontmatter(raw)
    body_first = body.splitlines()[0] if body else ""
    return bool(_GENERATED_RE.match(first) or _GENERATED_RE.match(body_first))


def load_rule_corpus() -> dict[str, dict]:
    table: dict[str, dict] = {}
    for path in sorted(DOCS_ROOT.rglob("*.md")):
        raw = path.read_text(encoding="utf-8")
        if _is_generated(raw):
            continue
        _, body = split_frontmatter(raw)
        for rule in rule_entries(body):
            rule_id = rule.get("rule_id")
            if rule_id:
                table[rule_id] = {
                    "name": rule.get("name", ""),
                    "statement": rule.get("statement", ""),
                    "doc": path.relative_to(DOCS_ROOT).as_posix(),
                }
    return table


def _resolve(rule_id: str, rules: dict[str, dict]) -> dict:
    """Fail loud on a registered rule_id canon does not carry — a silent skip would
    quietly hollow the index's by-construction completeness claim."""
    if rule_id not in rules:
        raise SystemExit(
            f"gen_error_index: registered rule_id {rule_id!r} not found in any canon "
            f"rules: block under {DOCS_ROOT} — the registry and canon have diverged"
        )
    return rules[rule_id]


# ---------------------------------------------------------------------------
# Transclude resolution — expand `:::{transclude}` in a shipped statement to its owner
# body, so the directive never reaches error-classes.toml. Region COLLECTION goes through
# the shared reader (`docs_site.preprocess.region_directives`); the resolution POLICY here
# stays local and fail-loud (unresolved id / cycle aborts — no placeholder degrade in a
# shipped artifact).
# ---------------------------------------------------------------------------

# commonmark + the colon-fence plugin, for the statement-side transclude token walk only
# (`:::{transclude} <id>` spans inside a rule statement being expanded).
_MD = MarkdownIt("commonmark").use(colon_fence_plugin)


def _collect_regions() -> dict[str, str]:
    """Scan the whole canon corpus for ``:::{region} <id>`` spans → ``{id: body}`` through the
    shared reader (``docs_site.preprocess.region_directives`` — the same collection the
    docs-site and agent-surface builds run). Generated artifacts are skipped (this generator's
    own outputs define no regions)."""
    regions: dict[str, str] = {}
    for path in sorted(DOCS_ROOT.rglob("*.md")):
        raw = path.read_text(encoding="utf-8")
        if _is_generated(raw):
            continue
        _, body = split_frontmatter(raw)
        for rid, content in region_directives(body):
            regions[rid] = content
    return regions


def _resolve_transcludes(text: str, index: dict[str, str], _stack: tuple[str, ...] = ()) -> str:
    """Expand every ``:::{transclude} <id>`` in ``text`` to its owner body from ``index``
    (recursively — an owner body may itself transclude). Fail loud on an unresolved id or a
    transclusion cycle: a leaked directive in the shipped artifact is exactly the regression this
    prevents, and a silent skip would hollow the single-sourcing it enables."""
    repls: list[tuple[int, int, str]] = []
    for token in _MD.parse(text):
        if token.type == "colon_fence" and token.info.strip().startswith("{transclude}"):
            rid = token.info.strip()[len("{transclude}"):].strip()
            if rid and token.map:
                repls.append((token.map[0], token.map[1], rid))
    if not repls:
        return text
    lines = text.split("\n")
    for start, end, rid in sorted(repls, reverse=True):  # reverse so earlier spans keep their indices
        if rid in _stack:
            raise SystemExit(
                f"gen_error_index: transclusion cycle through {rid!r} "
                f"({' -> '.join((*_stack, rid))}) — the expansion graph has no fixpoint"
            )
        if rid not in index:
            raise SystemExit(
                f"gen_error_index: unresolved transclude {rid!r} in a shipped statement — no "
                f"`:::{{region}}` or rule defines it; the directive would leak into the artifact"
            )
        body = _resolve_transcludes(index[rid], index, (*_stack, rid)).rstrip("\n")
        lines[start:end] = body.split("\n")
    return "\n".join(lines)


def _resolve_rule_statements(rules: dict[str, dict]) -> None:
    """Rewrite each rule's ``statement`` in place with its ``:::{transclude}`` directives
    expanded. The transcludable namespace is the corpus's ``:::{region}`` spans plus the rule
    statements themselves (a transclude may target either — the documentation build resolves
    both)."""
    index = _collect_regions()
    for rid, info in rules.items():
        index.setdefault(rid, info["statement"])  # raw statement captured before the rewrite below
    for info in rules.values():
        info["statement"] = _resolve_transcludes(info["statement"], index)


# ---------------------------------------------------------------------------
# Marker + hash (the V5 convention: sha256 of the LF-normalized body after line 1)
# ---------------------------------------------------------------------------


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def _with_marker(body: str, template: str) -> str:
    return template.format(digest=_body_hash(body)) + "\n" + body


MD_MARKER = "<!-- GENERATED — DO NOT EDIT (hash: {digest}) -->"
TOML_MARKER = "# GENERATED — DO NOT EDIT (hash: {digest})"

# The generated .md carries frontmatter FIRST (MyST requires it at byte 0, and the
# two-surface `audience:` filter needs it parseable); the generated-content marker
# then sits on the first line AFTER the closing `---` — any consumer that skips
# generated artifacts (e.g. `_rule_blocks` above) accepts either placement. Field
# set mirrors the reference-plane siblings (glossary.md).
_MD_FRONTMATTER = (
    "---\n"
    "kind: reference\n"
    "audience: [authors, integrators, agents]\n"
    "slug: error-index\n"
    "---\n"
)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_error_index(rules: dict[str, dict]) -> str:
    lines: list[str] = [
        "# Error index",
        "",
        "The cross-reference from the engine's **registered error set** to the derived",
        "rules it enforces — generated from the registration API (`conjured.errors`:",
        "`CHECK_REGISTRY` + `AUDIT_CODE_REGISTRY`) by `tools/gen_error_index.py`. The",
        "constructors reject an unregistered `audit_code` / `(check, rule_id)` pair, so",
        "this index is complete by construction.",
        "",
        "Audit `<CX>.<TOPIC>.<NNN>` codes are assigned incrementally as the catalog",
        "grows; an unassigned violation dispatches on its symbolic `check` discriminator",
        "— the consumer / test dispatch key present on every `ContractViolation` — and",
        "registered audit codes appear below as the catalog assigns them. The remediation",
        "path for any row is the owning reference named in the rule legend.",
        "",
        "## Registered audit codes",
        "",
        "| audit_code | error class | check | enforces |",
        "|---|---|---|---|",
    ]
    for code in sorted(AUDIT_CODE_REGISTRY):
        check = AUDIT_CODE_REGISTRY[code]
        record = CHECK_REGISTRY[check]
        rule_refs = ", ".join(
            f"{rid} ({_resolve(rid, rules)['name']})" for rid in record.rule_ids
        )
        lines.append(f"| `{code}` | {record.error_class} | `{check.value}` | {rule_refs} |")

    lines += [
        "",
        "## Check discriminators (the symbolic dispatch keys)",
        "",
        "One row per `Check` member, in the enum's stage order. `enforces` lists every",
        "rule_id a raise site may cite with that check (the registered set).",
        "",
        "| check | error class | enforces |",
        "|---|---|---|",
    ]
    rule_ids_seen: list[str] = []
    for check in Check:
        record = CHECK_REGISTRY[check]
        for rid in record.rule_ids:
            if rid not in rule_ids_seen:
                rule_ids_seen.append(rid)
        enforced = ", ".join(f"`{rid}`" for rid in record.rule_ids)
        lines.append(f"| `{check.value}` | {record.error_class} | {enforced} |")

    lines += [
        "",
        "## Rule legend",
        "",
        "| rule | name | owning reference (the remediation path) |",
        "|---|---|---|",
    ]
    for rid in sorted(rule_ids_seen):
        info = _resolve(rid, rules)
        lines.append(f"| `{rid}` | {info['name']} | `{info['doc']}` |")
    lines.append("")
    return "\n".join(lines)


def _toml_string(value: str) -> str:
    """Render a TOML string value. Multi-line content uses a literal multi-line string
    (no escape processing); the fallback escapes for a basic string."""
    if "\n" in value:
        if "'''" in value:  # literal strings cannot contain their own delimiter
            escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
            return f'"""\n{escaped}"""'
        return f"'''\n{value}'''"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_error_classes_toml(rules: dict[str, dict]) -> str:
    lines: list[str] = [
        "# conjured.agent error-classes — the machine-readable audit_code → rule mapping",
        "# (error-channel/reference.md § error-classes.toml). Generated from the engine's",
        "# registration API by tools/gen_error_index.py; the in-process Check discriminator",
        "# table rides along as the pre-catalog dispatch surface.",
        "",
    ]
    for code in sorted(AUDIT_CODE_REGISTRY):
        check = AUDIT_CODE_REGISTRY[code]
        record = CHECK_REGISTRY[check]
        if len(record.rule_ids) != 1:
            # Canon pins the [[audit_codes]] record shape SINGULAR (one audit_code → one
            # rule_id — error-channel/reference.md § error-classes.toml); the [[checks]] table
            # below carries the full rule_ids list, but an audit-coded record silently emitting
            # only rule_ids[0] would break the md-row mirror the same canon section names. Fail
            # loud rather than under-report — the same posture as _resolve / _resolve_transcludes.
            raise SystemExit(
                f"gen_error_index: audit-coded check {check.value!r} (audit_code {code!r}) "
                f"enforces {len(record.rule_ids)} rule_ids {list(record.rule_ids)}, but canon's "
                "§ error-classes.toml record shape is singular (one audit_code keys one rule_id). "
                "A multi-rule audit assignment needs a canon record-shape decision first — the "
                "generator will not silently emit only the first."
            )
        rule_id = record.rule_ids[0]
        info = _resolve(rule_id, rules)
        lines += [
            "[[audit_codes]]",
            f'audit_code = "{code}"',
            f'check = "{check.value}"',
            f'error_class = "{record.error_class}"',
            f'rule_id = "{rule_id}"',
            f"rule_name = {_toml_string(info['name'])}",
            f"reference = {_toml_string(info['doc'])}",
            f"statement = {_toml_string(info['statement'])}",
            "",
        ]
    for check in Check:
        record = CHECK_REGISTRY[check]
        rule_names = [_resolve(rid, rules)["name"] for rid in record.rule_ids]
        refs = sorted({_resolve(rid, rules)["doc"] for rid in record.rule_ids})
        lines += [
            "[[checks]]",
            f'check = "{check.value}"',
            f'error_class = "{record.error_class}"',
            "rule_ids = [" + ", ".join(f'"{rid}"' for rid in record.rule_ids) + "]",
            "rule_names = [" + ", ".join(_toml_string(n) for n in rule_names) + "]",
            "references = [" + ", ".join(_toml_string(r) for r in refs) + "]",
            "",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build / write / check
# ---------------------------------------------------------------------------


def build() -> dict[Path, str]:
    rules = load_rule_corpus()
    _resolve_rule_statements(rules)  # ship resolved transclude bodies, never the directive
    return {
        INDEX_PATH: _MD_FRONTMATTER + _with_marker(render_error_index(rules), MD_MARKER),
        TOML_PATH: _with_marker(render_error_classes_toml(rules), TOML_MARKER),
    }


def write() -> None:
    for path, content in build().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(PKG_DIR)}")


def check() -> int:
    """Freshness: re-derive in memory and compare against disk (LF-normalized)."""
    stale = []
    for path, content in build().items():
        on_disk = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else ""
        if on_disk != content:
            stale.append(path)
    for path in stale:
        print(f"STALE: {path.relative_to(PKG_DIR)} — regenerate via tools/gen_error_index.py")
    return 1 if stale else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true",
        help="verify the on-disk artifacts match a fresh derivation; exit 1 if stale",
    )
    args = parser.parse_args(argv)
    if args.check:
        return check()
    write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
