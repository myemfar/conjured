"""Corpus preprocessing — expand the two-graph directives for the Sphinx build.

Pass 1 collects every ``:::{region}`` body and every ``rules:`` entry corpus-wide;
pass 2 rewrites each doc: a ``:::{transclude} <id>`` becomes the owner's current
text, a region wrapper becomes an explicit MyST target plus its content, and
slashed anchor hrefs are rewritten to the dash form the targets use. The parse is
self-contained (``markdown-it-py`` colon-fence tokens + ``PyYAML``), so fence-internal
text is never misread and every dependency ships with the package.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.attrs import attrs_block_plugin
from mdit_py_plugins.colon_fence import colon_fence_plugin

# The same parser configuration the corpus is authored against (attrs_block for the
# leading `{#id}` heading ids; colon_fence for the region/transclude directives).
_MD = MarkdownIt("commonmark").use(attrs_block_plugin).use(colon_fence_plugin)

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_YAML_FENCE_RE = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL)

# A bare two-line transclude fence (the corpus form), optionally indented and/or
# carried inside a blockquote render (`> `-prefixed rule statements).
_NESTED_TRANSCLUDE_RE = re.compile(
    r"^(?P<pre>(?:[ \t]|> )*):::\{transclude\} (?P<rid>\S+)[ \t]*\n"
    r"(?:[ \t]|> )*:::[ \t]*$",
    re.MULTILINE,
)


def _slug(region_id: str) -> str:
    """A region id as an HTML-addressable target (ids carry ``/``; hrefs are
    rewritten with the same substitution so the pair stays consistent)."""
    return region_id.replace("/", "-")


def _slugify(text: str) -> str:
    """Heading text → auto-anchor slug (MyST's common case)."""
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-")


def split_frontmatter(raw: str) -> tuple[str, str]:
    """``(header, body)`` — the raw frontmatter block (possibly empty) and the rest."""
    m = _FRONTMATTER_RE.match(raw)
    return (m.group(0), raw[m.end():]) if m else ("", raw)


_STEERING_KIND_RE = re.compile(r"^kind:\s*steering\s*$", re.MULTILINE)


def _is_steering(raw: str) -> bool:
    """``kind: steering`` docs are agent-surface-only — NEVER the integrator HTML
    site (glossary § Steering pins this) — so the site build excludes them whole:
    not copied, not region-collected, invisible to the human surface. (The agent
    surface renders them via ``tools/build_agent_surface.py`` instead.)"""
    header, _ = split_frontmatter(raw)
    return bool(_STEERING_KIND_RE.search(header))


def _heading_anchors(body: str) -> set[str]:
    """Every heading's anchor: the explicit leading ``{#id}`` when present, else the
    auto-slug — used to avoid emitting a duplicate target for a slug a heading owns."""
    anchors: set[str] = set()
    tokens = _MD.parse(body)
    for i, token in enumerate(tokens):
        if token.type == "heading_open" and i + 1 < len(tokens):
            inline = tokens[i + 1]
            if inline.type == "inline":
                explicit = token.attrs.get("id")
                anchors.add(str(explicit) if explicit else _slugify(inline.content))
    return anchors


def region_directives(body: str):
    """Yield ``(region_id, content)`` for each ``:::{region} <id>`` colon fence."""
    for token in _MD.parse(body):
        if token.type != "colon_fence":
            continue
        info = token.info.strip()
        if info.startswith("{region}"):
            rid = info[len("{region}"):].strip()
            if rid:
                yield rid, token.content


def rule_entries(body: str):
    """Yield every rule mapping from every ``rules:`` yaml fence in the doc."""
    for m in _YAML_FENCE_RE.finditer(body):
        try:
            parsed = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("rules"), list):
            for rule in parsed["rules"]:
                if isinstance(rule, dict):
                    yield rule


def _line_spans(body: str, directives: list[tuple[int, int, str]]) -> str:
    """Replace 0-based line spans [start, end) of ``body`` bottom-up."""
    lines = body.splitlines()
    for start, end, replacement in sorted(directives, key=lambda d: d[0], reverse=True):
        lines[start:end] = replacement.splitlines()
    return "\n".join(lines) + "\n"


def _resolve_nested_transcludes(regions: dict[str, str], rules: dict[str, str],
                                max_passes: int = 10) -> None:
    """Fixpoint-expand transclude fences INSIDE stored region/rule content.

    A transcluded rule's statement (or a region's body) may itself contain a
    ``:::{transclude}`` fence; the per-doc substitution pass is single-shot, so an
    unresolved inner fence would leak into the render as a literal unknown
    directive. Each substituted line re-carries the site prefix (indent and/or the
    ``> `` of a blockquote-rendered rule). A reference cycle can never stabilise —
    fail loud after ``max_passes`` rather than loop or leak.
    """
    def _expand(text: str) -> tuple[str, bool]:
        changed = False

        def repl(m: re.Match[str]) -> str:
            nonlocal changed
            pre, rid = m.group("pre"), m.group("rid")
            content = regions.get(rid) or rules.get(rid) or (
                f"*(transcluded: `{rid}` — see its owner doc)*"
            )
            changed = True
            return "\n".join((pre + ln).rstrip() for ln in content.splitlines())

        return _NESTED_TRANSCLUDE_RE.sub(repl, text), changed

    for _ in range(max_passes):
        any_changed = False
        for store in (regions, rules):
            for key, text in list(store.items()):
                new, changed = _expand(text)
                if changed:
                    store[key] = new
                    any_changed = True
        if not any_changed:
            return
    leftovers = sorted(key for store in (regions, rules)
                       for key, text in store.items()
                       if _NESTED_TRANSCLUDE_RE.search(text))
    raise RuntimeError(
        "nested-transclude expansion did not converge after "
        f"{max_passes} passes (reference cycle?) in: {', '.join(leftovers)}"
    )


def preprocess(src_root: Path, build_src: Path) -> None:
    """Copy the corpus into ``build_src`` with directives expanded.

    Each region OWNER is prefixed with an explicit MyST target ``(slug)=`` so a
    cross-page ``[..](#slug)`` resolves in the multi-page build; the target is
    skipped when its slug already names a heading (avoids a docutils dupname).
    """
    md_files = sorted(
        p for p in src_root.rglob("*.md")
        if not _is_steering(p.read_text(encoding="utf-8"))
    )
    regions: dict[str, str] = {}
    rules: dict[str, str] = {}
    heading_slugs: set[str] = set()
    for path in md_files:
        _, body = split_frontmatter(path.read_text(encoding="utf-8"))
        heading_slugs |= _heading_anchors(body)
        for rid, content in region_directives(body):
            regions[rid] = content.rstrip("\n")
        for rule in rule_entries(body):
            rule_id = str(rule.get("rule_id", "")).strip()
            if rule_id:  # a rule transclude renders the rule's name + statement
                statement = str(rule.get("statement", "")).rstrip()
                rules[rule_id] = (
                    f"> **{rule_id} — {rule.get('name', '')}.**\n>\n"
                    + "\n".join("> " + ln for ln in statement.splitlines())
                )

    # Stored content can itself carry transclude fences (a rule statement
    # transcluding its own region) — expand to fixpoint BEFORE substitution.
    _resolve_nested_transcludes(regions, rules)

    for path in md_files:
        raw = path.read_text(encoding="utf-8")
        header, body = split_frontmatter(raw)

        spans: list[tuple[int, int, str]] = []
        for token in _MD.parse(body):
            if token.type != "colon_fence" or not token.map:
                continue
            info = token.info.strip()
            if info.startswith("{region}"):
                rid = info[len("{region}"):].strip()
                if rid:
                    content = regions[rid]
                    if _slug(rid) not in heading_slugs:
                        content = f"({_slug(rid)})=\n\n{content}"
                    spans.append((token.map[0], token.map[1], content))
            elif info.startswith("{transclude}"):
                rid = info[len("{transclude}"):].strip()
                if rid:
                    body_text = regions.get(rid) or rules.get(rid) or (
                        f"*(transcluded: `{rid}` — see its owner doc)*"
                    )
                    spans.append((token.map[0], token.map[1], body_text))

        out = _line_spans(body, spans) if spans else body

        # textual fallback for transcludes INSIDE code fences (e.g. a rule
        # statement in a ```yaml block) — opaque to the token parser above;
        # expanding them in place makes the displayed rule text complete.
        def _fence_transclude(m: re.Match[str]) -> str:
            indent, rid = m.group(1), m.group(2)
            content = regions.get(rid) or rules.get(rid)
            if content is None:
                return m.group(0)
            return "\n".join(indent + ln for ln in content.splitlines())

        out = re.sub(r"^([ \t]*):::\{transclude\} (\S+)\n[ \t]*:::[ \t]*$",
                     _fence_transclude, out, flags=re.MULTILINE)
        # hrefs to slashed ids (region anchors) → the dash form emitted above.
        out = re.sub(r"\(#([^\s)/]+(?:/[^\s)]+)+)\)",
                     lambda m: "(#" + _slug(m.group(1)) + ")", out)

        rel = path.relative_to(src_root)
        dest = build_src / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(header + out, encoding="utf-8")

    for path in sorted(src_root.rglob("*.toml")):  # kind-schemas, linked from the README
        rel = path.relative_to(src_root)
        dest = build_src / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
