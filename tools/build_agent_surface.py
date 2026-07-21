"""Build the in-package agent surface from the canonical docs corpus.

Renders the three artifacts the components view names for
``importlib.resources.files("conjured.agent")`` (architecture/components.md § Agent
surface): the **audience-filtered docs bundle** (every ``audience: [..., agents]`` doc,
directives expanded so each page is self-contained, plus the machine-readable ``.toml``
schema companions at their corpus paths), the **steering content** (each
``kind: steering`` doc under ``docs/agent/steering/`` with its ``renders_from`` owner
content extracted and appended — the steering render chain), and the **``llms.txt``
index** (agent-audience pages by section, from frontmatter — glossary § llms.txt).

Codegen-script pattern (the ``gen_error_index.py`` sibling): output is committed and
shipped as ``conjured.agent`` package data; ``--check`` re-derives everything in memory
and fails on any mismatch, so a canon edit without a regenerate cannot ship a stale
surface (the F-PB-3 freshness posture). Corpus parsing is shared with the sibling
docs-site build (``docs_site.preprocess``) — one reader, two projections.

The render FAILS LOUD: a steering doc with a missing/unresolvable ``renders_from``, an
ambiguous anchor, or an extraction that would leak an unexpanded transclude aborts the
build with a named error — never a silently thinner surface.

Usage (from the repo root or the package directory)::

    python tools/build_agent_surface.py           # regenerate into src/conjured/agent/
    python tools/build_agent_surface.py --check   # verify committed output is fresh
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):  # invoked as a script: make sibling imports resolve
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from docs_site.preprocess import (  # noqa: E402
    preprocess,
    region_directives,
    rule_entries,
    split_frontmatter,
)

PKG_DIR = Path(__file__).resolve().parents[1]  # conjured/ (the extraction root)
DOCS_ROOT = PKG_DIR / "docs"
AGENT_PKG = PKG_DIR / "src" / "conjured" / "agent"

#: The generator-owned output set inside the agent package. `error-classes.toml` and
#: `__init__.py` are NOT ours (gen_error_index.py / source) and are never touched.
_OWNED_DIRS = ("docs", "steering")
_INDEX_NAME = "llms.txt"

_MD_MARKER = "<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->\n"
_TOML_MARKER = "# GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT\n"

_AUDIENCE_RE = re.compile(r"^audience:\s*\[(?P<vals>[^\]]*)\]\s*$", re.MULTILINE)
_RENDERS_FROM_RE = re.compile(r"^renders_from:\s*(?P<id>\S+)\s*$", re.MULTILINE)
_ATTR_LINE_RE = re.compile(r"^\{#(?P<id>[^}\s]+)\}\s*$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+\S")


def _audience(header: str) -> list[str]:
    m = _AUDIENCE_RE.search(header)
    if not m:
        return []
    return [v.strip() for v in m.group("vals").split(",") if v.strip()]


# ---------------------------------------------------------------------------
# The steering render chain — resolve a renders_from anchor, extract its content
# ---------------------------------------------------------------------------


def _heading_section(body: str, anchor: str) -> "str | None":
    """The section owned by explicit heading anchor ``{#anchor}``: from the attrs
    line through the line before the next heading of the same or higher level
    (attrs line included), trailing hrules/blank lines trimmed."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        m = _ATTR_LINE_RE.match(line)
        if not (m and m.group("id") == anchor and i + 1 < len(lines)):
            continue
        h = _HEADING_RE.match(lines[i + 1])
        if not h:
            continue
        level = len(h.group("hashes"))
        end = len(lines)
        for j in range(i + 2, len(lines)):
            nxt = _HEADING_RE.match(lines[j])
            if nxt and len(nxt.group("hashes")) <= level:
                # cut before the heading's own attrs line when present
                end = j - 1 if j > 0 and _ATTR_LINE_RE.match(lines[j - 1]) else j
                break
        section = lines[i:end]
        while section and section[-1].strip() in ("", "---"):
            section.pop()
        return "\n".join(section)
    return None


def _extract(anchor: str, source_docs: "list[tuple[Path, str, str]]") -> str:
    """Resolve ``anchor`` across the source corpus (heading, region, or rule — the
    same three anchor classes the doc harness's renders_from edge admits) and return
    its extractable content. Aborts loud on missing/ambiguous/unexpandable targets."""
    hits: list[str] = []
    for rel, _header, body in source_docs:
        section = _heading_section(body, anchor)
        if section is not None:
            hits.append(section)
        for rid, content in region_directives(body):
            if rid == anchor:
                hits.append(content.rstrip("\n"))
        for rule in rule_entries(body):
            if str(rule.get("rule_id", "")).strip() == anchor:
                statement = str(rule.get("statement", "")).rstrip()
                hits.append(
                    f"> **{anchor} — {rule.get('name', '')}.**\n>\n"
                    + "\n".join("> " + ln for ln in statement.splitlines())
                )
    if not hits:
        raise SystemExit(
            f"build_agent_surface: renders_from anchor '{anchor}' resolves to no "
            f"heading, region, or rule in the corpus — the steering render aborts "
            f"(fix the steering doc or restore the owner anchor)."
        )
    if len(hits) > 1:
        raise SystemExit(
            f"build_agent_surface: renders_from anchor '{anchor}' is ambiguous "
            f"({len(hits)} owners) — a dependency id must resolve to exactly one owner."
        )
    if ":::{transclude}" in hits[0]:
        raise SystemExit(
            f"build_agent_surface: the content behind '{anchor}' carries an unexpanded "
            f"transclude fence — a rendered steering page must be self-contained; "
            f"narrow the anchor or expand the owner."
        )
    return hits[0]


# ---------------------------------------------------------------------------
# build / write / check
# ---------------------------------------------------------------------------


def build(docs_root: Path = DOCS_ROOT) -> "dict[Path, str]":
    """Derive the full agent surface: ``{path relative to the agent package: content}``."""
    out: dict[Path, str] = {}

    # -- the docs bundle: expanded corpus, filtered to the agents audience ---------
    with tempfile.TemporaryDirectory() as td:
        build_src = Path(td) / "expanded"
        build_src.mkdir()
        preprocess(docs_root, build_src)  # steering excluded there; directives expanded
        for md in sorted(build_src.rglob("*.md")):
            raw = md.read_text(encoding="utf-8")
            header, body = split_frontmatter(raw)
            if "agents" not in _audience(header):
                continue
            rel = md.relative_to(build_src)
            out[Path("docs") / rel] = header + _MD_MARKER + body
        for toml in sorted(build_src.rglob("*.toml")):
            rel = toml.relative_to(build_src)
            out[Path("docs") / rel] = _TOML_MARKER + toml.read_text(encoding="utf-8")

    # -- the steering render chain (from the SOURCE corpus) ------------------------
    source_docs = [
        (p.relative_to(docs_root), *split_frontmatter(p.read_text(encoding="utf-8")))
        for p in sorted(docs_root.rglob("*.md"))
    ]
    steering_dir = docs_root / "agent" / "steering"
    for sd in sorted(steering_dir.glob("*.md")) if steering_dir.is_dir() else []:
        raw = sd.read_text(encoding="utf-8")
        header, body = split_frontmatter(raw)
        m = _RENDERS_FROM_RE.search(header)
        if not m:
            raise SystemExit(
                f"build_agent_surface: steering doc '{sd.name}' declares no "
                f"renders_from — every steering doc renders its owner's content."
            )
        extracted = _extract(m.group("id"), source_docs)
        out[Path("steering") / sd.name] = (
            header + _MD_MARKER + body.rstrip("\n") + "\n\n" + extracted + "\n"
        )

    # -- llms.txt: agent-audience pages by section (frontmatter-driven) ------------
    sections: dict[str, list[str]] = {}
    for rel in sorted(out):
        if rel.suffix != ".md":
            continue  # the index lists pages; the .toml companions ride beside them
        parts = rel.parts
        if parts[0] == "steering":
            section = "steering"
        elif parts[1] == "components" and len(parts) > 3:
            section = f"components/{parts[2]}"
        else:
            section = parts[1] if len(parts) > 2 else "(root)"
        sections.setdefault(section, []).append(rel.as_posix())
    digest = hashlib.sha256(
        "".join(f"{p.as_posix()}\0{c}\0" for p, c in sorted(out.items())).encode("utf-8")
    ).hexdigest()[:16]
    index_lines = [
        f"<!-- GENERATED — DO NOT EDIT (hash: {digest}) — tools/build_agent_surface.py -->",
        "# Conjured — agent surface index",
        "",
    ]
    for section in sorted(sections):
        index_lines.append(f"## {section}")
        index_lines.extend(f"- {page}" for page in sections[section])
        index_lines.append("")
    out[Path(_INDEX_NAME)] = "\n".join(index_lines)
    return out


def write(artifacts: "dict[Path, str]", agent_pkg: Path = AGENT_PKG) -> None:
    """Write the surface, clearing ONLY the generator-owned outputs first."""
    for d in _OWNED_DIRS:
        target = agent_pkg / d
        if target.exists():
            shutil.rmtree(target)
    (agent_pkg / _INDEX_NAME).unlink(missing_ok=True)
    for rel, content in sorted(artifacts.items()):
        dest = agent_pkg / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8", newline="\n")


def check(artifacts: "dict[Path, str]", agent_pkg: Path = AGENT_PKG) -> "list[str]":
    """Freshness: the committed surface must equal the in-memory derivation exactly —
    same file set (no extras, no missing), same bytes."""
    problems: list[str] = []
    expected = {rel.as_posix(): content for rel, content in artifacts.items()}
    on_disk: dict[str, Path] = {}
    for d in _OWNED_DIRS:
        root = agent_pkg / d
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file():
                    on_disk[p.relative_to(agent_pkg).as_posix()] = p
    if (agent_pkg / _INDEX_NAME).is_file():
        on_disk[_INDEX_NAME] = agent_pkg / _INDEX_NAME
    for rel in sorted(set(expected) - set(on_disk)):
        problems.append(f"missing from the committed surface: {rel}")
    for rel in sorted(set(on_disk) - set(expected)):
        problems.append(f"stale extra file in the committed surface: {rel}")
    for rel in sorted(set(expected) & set(on_disk)):
        if on_disk[rel].read_text(encoding="utf-8") != expected[rel]:
            problems.append(f"stale content (differs from current canon): {rel}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify the committed surface is fresh against current canon")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252
    except Exception:
        pass
    artifacts = build()
    if args.check:
        problems = check(artifacts)
        if problems:
            print("agent surface STALE against current canon:")
            for p in problems:
                print(f"  {p}")
            print("run: python tools/build_agent_surface.py")
            return 1
        print(f"agent surface fresh ({len(artifacts)} files).")
        return 0
    write(artifacts)
    print(f"agent surface -> {AGENT_PKG} ({len(artifacts)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
