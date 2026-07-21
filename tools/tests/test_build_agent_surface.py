"""``build_agent_surface`` — the agent-surface codegen step. The F-PA-8 fixture pair
(a valid ``renders_from`` render; a missing-anchor abort) plus the surface's happy and
error paths: audience filtering, the llms.txt index, determinism, the ``--check``
staleness gate, and the fail-loud arms (ambiguous anchor, transclude leak, a steering
doc with no renders_from).

Run with ``PYTHONPATH=tools`` (from the package root) (the same harness-test convention as the
``gen_error_index`` suite). Fixture corpora are real tmp_path doc trees driven through
the real ``build()`` entry point — no parsing internals are mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import build_agent_surface as bas


# ---------------------------------------------------------------------------
# Fixture corpus (F-PA-8's `valid_steering_renders_from` shape)
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


_ARCH_PAGE = """---
kind: reference
audience: [authors, integrators, agents]
slug: fixture-arch
---

{#fixture-arch-page}
# Fixture architecture page

{#the-owned-instruction}
## The owned instruction

The canonical instruction body an agent must read verbatim.

{#a-sibling-section}
## A sibling section

Content the extraction must NOT include.
"""

_HUMAN_ONLY_PAGE = """---
kind: explanation
audience: [authors, integrators]
slug: fixture-essay
---

{#fixture-essay}
# A reasoning essay

Human-surface prose the agent bundle must not carry.
"""

_STEERING_DOC = """---
kind: steering
audience: [agents]
slug: fixture-steering
renders_from: the-owned-instruction
---

{#fixture-steering}
# Steering — fixture

**When this fires:** fixture trigger.

The owning canonical statement:
"""


def _corpus(root: Path, *, steering: str = _STEERING_DOC) -> Path:
    docs = root / "docs"
    _write(docs, "architecture/fixture-arch.md", _ARCH_PAGE)
    _write(docs, "explanation/fixture-essay.md", _HUMAN_ONLY_PAGE)
    if steering:
        _write(docs, "agent/steering/fixture.md", steering)
    return docs


@pytest.fixture()
def artifacts(tmp_path) -> dict:
    return bas.build(_corpus(tmp_path))


# ---------------------------------------------------------------------------
# F-PA-8 — the render chain's fixture pair
# ---------------------------------------------------------------------------


def test_valid_steering_renders_from_extracts_the_owner_section(artifacts):
    rendered = artifacts[Path("steering") / "fixture.md"]
    # the steering doc's own framing survives, then the extracted owner section
    assert "**When this fires:** fixture trigger." in rendered
    assert "The canonical instruction body an agent must read verbatim." in rendered
    # extraction is bounded: the sibling section never leaks in
    assert "A sibling section" not in rendered
    assert "must NOT include" not in rendered


def test_missing_anchor_aborts_cleanly(tmp_path):
    docs = _corpus(
        tmp_path,
        steering=_STEERING_DOC.replace("the-owned-instruction", "no-such-anchor"),
    )
    with pytest.raises(SystemExit, match="no-such-anchor"):
        bas.build(docs)


# ---------------------------------------------------------------------------
# The projection + index
# ---------------------------------------------------------------------------


def test_bundle_filters_by_agents_audience(artifacts):
    assert Path("docs/architecture/fixture-arch.md") in artifacts
    assert not any("fixture-essay" in p.as_posix() for p in artifacts)


def test_llms_txt_lists_pages_by_section(artifacts):
    index = artifacts[Path("llms.txt")]
    assert index.splitlines()[0].startswith("<!-- GENERATED")
    assert "## architecture" in index
    assert "- docs/architecture/fixture-arch.md" in index
    assert "## steering" in index
    assert "- steering/fixture.md" in index
    assert "fixture-essay" not in index


def test_build_is_deterministic(tmp_path):
    docs = _corpus(tmp_path)
    assert bas.build(docs) == bas.build(docs)


def test_every_generated_file_carries_a_do_not_edit_marker(artifacts):
    for rel, content in artifacts.items():
        assert "GENERATED" in content and "DO NOT EDIT" in content, rel.as_posix()


# ---------------------------------------------------------------------------
# The --check staleness gate (the F-PB-3 posture)
# ---------------------------------------------------------------------------


def test_check_passes_fresh_and_flags_tampered_stale_and_extra(tmp_path, artifacts):
    agent_pkg = tmp_path / "agent"
    agent_pkg.mkdir()
    bas.write(artifacts, agent_pkg)
    assert bas.check(artifacts, agent_pkg) == []
    # tampered content
    target = agent_pkg / "steering" / "fixture.md"
    target.write_text(target.read_text(encoding="utf-8") + "hand edit\n", encoding="utf-8")
    problems = bas.check(artifacts, agent_pkg)
    assert any("stale content" in p and "fixture.md" in p for p in problems)
    # an extra file no derivation produces
    (agent_pkg / "docs" / "orphan.md").write_text("orphan", encoding="utf-8")
    problems = bas.check(artifacts, agent_pkg)
    assert any("stale extra file" in p and "orphan.md" in p for p in problems)
    # a missing file
    target.unlink()
    problems = bas.check(artifacts, agent_pkg)
    assert any("missing from the committed surface" in p for p in problems)


# ---------------------------------------------------------------------------
# Fail-loud arms
# ---------------------------------------------------------------------------


def test_ambiguous_anchor_aborts(tmp_path):
    docs = _corpus(tmp_path)
    _write(docs, "architecture/second-owner.md", _ARCH_PAGE.replace(
        "fixture-arch", "fixture-arch-two").replace("fixture-arch-page", "fixture-arch-page-two"))
    with pytest.raises(SystemExit, match="ambiguous"):
        bas.build(docs)


def test_extraction_with_unexpanded_transclude_aborts(tmp_path):
    docs = _corpus(tmp_path)
    _write(docs, "architecture/fixture-arch.md", _ARCH_PAGE.replace(
        "The canonical instruction body an agent must read verbatim.",
        ":::{transclude} some/region\n:::",
    ))
    with pytest.raises(SystemExit, match="transclude"):
        bas.build(docs)


def test_steering_doc_without_renders_from_aborts(tmp_path):
    docs = _corpus(tmp_path, steering=_STEERING_DOC.replace(
        "renders_from: the-owned-instruction\n", ""))
    with pytest.raises(SystemExit, match="renders_from"):
        bas.build(docs)
