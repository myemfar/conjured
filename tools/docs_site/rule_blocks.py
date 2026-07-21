"""The rule-anchor render — each ``rules:`` yaml block becomes anchored rule sections.

The decision-of-record (2026-06-30, option 4): a GENERATOR directive, not a source
reshape — canon keeps the one machine-readable YAML rule registry; the human surface
renders, per rule: ``rule_id`` → an explicit MyST target (so a Graph-A citation
``[R-handler-001](#R-handler-001)`` lands ON the rule) · ``name`` → a bold heading ·
``statement`` → prose · ``enforcement`` → a small badge. ``derived_from`` stays OFF
the human surface (machine-read metadata — the agent surface carries it; the rendered
site does not show it). Grounding:
``rule_id`` + ``statement`` are product-facing — the shipped engine raises
``rule_id="…"`` in ContractViolations, and integrators look them up here.
"""

from __future__ import annotations

import re

import yaml

_YAML_FENCE_RE = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL)


def _render_rule(rule: dict) -> str:
    rid = str(rule.get("rule_id", "")).strip()
    name = str(rule.get("name", "")).strip()
    statement = str(rule.get("statement", "")).rstrip()
    enforcement = str(rule.get("enforcement", "")).strip()
    head = f"**{rid} — {name}**" if name else f"**{rid}**"
    if enforcement:
        head += f" &nbsp;·&nbsp; `{enforcement}`"
    # Two anchors per rule: the MyST target (docutils lowercases it — the form every
    # in-site citation resolves to) AND a raw case-exact HTML anchor, so a fragment
    # hand-typed from an error payload's verbatim `rule_id` ("#R-handler-001") also
    # lands (browser fragment lookup is case-sensitive).
    target = f'({rid})=\n<span id="{rid}"></span>\n' if rid else ""
    return f"{target}{head}\n\n{statement}"


def render_rule_blocks(md: str) -> str:
    """Replace each ```yaml fence that is a ``rules:`` list with rendered, anchored
    rule sections. Non-rule yaml fences are left untouched."""
    def repl(m: re.Match[str]) -> str:
        try:
            data = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            return m.group(0)
        if not (isinstance(data, dict) and isinstance(data.get("rules"), list)):
            return m.group(0)
        rendered = [_render_rule(r) for r in data["rules"] if isinstance(r, dict)]
        return "\n\n".join(rendered) + "\n" if rendered else m.group(0)

    return _YAML_FENCE_RE.sub(repl, md)
