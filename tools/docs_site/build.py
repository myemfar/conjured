"""Build the shipping multi-page docs site from the canonical corpus.

Pipeline: preprocess (region/transclude expansion) → rule-anchor render (each
``rules:`` block → anchored sections) → an appended, generated toctree entry for the
autodoc2 API companion → Sphinx (``-b html`` + Furo). Warning lines are categorised
so the QA signal is scannable; ``xref_missing`` (an unresolved rule-id / region-id /
heading citation) is the failure class this build exists to surface.

Usage (from the package directory — the directory the lift extracts)::

    python tools/docs_site/build.py                 # → _build/docs-site
    python tools/docs_site/build.py --out <dir>

Requires the ``conjured[docs]`` extra (Sphinx + MyST + Furo + mermaid + autodoc2).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):  # invoked as a script: make sibling imports resolve
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from preprocess import preprocess  # type: ignore[no-redef]
    from rule_blocks import render_rule_blocks  # type: ignore[no-redef]
else:
    from .preprocess import preprocess
    from .rule_blocks import render_rule_blocks

PKG_DIR = Path(__file__).resolve().parents[2]  # conjured/ (the extraction root)
DOCS_ROOT = PKG_DIR / "docs"
SRC_PKG = PKG_DIR / "src" / "conjured"
CONF_PY = Path(__file__).resolve().parent / "conf.py"

# The API companion rides a generated, hidden toctree appended to the COPIED
# index — canon is untouched; the build owns the wiring of generated surfaces.
_APIDOCS_TOCTREE = """
```{toctree}
:hidden:
:maxdepth: 1

apidocs/index
```
"""

# Categorise sphinx warning lines so the report is scannable.
_WARN_PATTERNS = [
    ("xref_missing (unresolved link)", re.compile(r"xref_missing|cross-reference target|undefined label|reference target not found", re.I)),
    ("xref_ambiguous (REGRESSION — structurally forbidden since 2026-07-09; the harness single-ownership anchor arm should have caught this)", re.compile(r"xref_ambiguous|more than one target found", re.I)),
    ("highlighting_failure (HTTP lexer — known cosmetic)", re.compile(r"highlighting_failure|Lexing literal_block", re.I)),
    ("directive_unknown (a leaked/unexpanded directive)", re.compile(r"directive_unknown|Unknown directive type", re.I)),
    ("duplicate id / label", re.compile(r"duplicate (?:id|label|explicit target|object description)", re.I)),
    ("toctree orphan / not-included", re.compile(r"isn't included in any toctree|not included in any toctree|document isn't included", re.I)),
    ("toctree bad reference", re.compile(r"toctree contains reference to (?:nonexisting|excluded)", re.I)),
    ("mermaid / diagram", re.compile(r"mermaid", re.I)),
    ("autodoc2", re.compile(r"autodoc2|apidocs", re.I)),
]


def _categorise(output: str) -> tuple[Counter, list[str]]:
    cats: Counter = Counter()
    uncategorised: list[str] = []
    for line in output.splitlines():
        if "WARNING" not in line and "ERROR" not in line:
            continue
        for label, pat in _WARN_PATTERNS:
            if pat.search(line):
                cats[label] += 1
                break
        else:
            cats["other"] += 1
            uncategorised.append(line.strip())
    return cats, uncategorised


def build_site(out_dir: Path, keep_src: bool = False) -> int:
    build_src = out_dir.parent / (out_dir.name + "-src")
    for d in (build_src, out_dir):
        if d.exists():
            shutil.rmtree(d)
    build_src.mkdir(parents=True)

    preprocess(DOCS_ROOT, build_src)
    for md in build_src.rglob("*.md"):  # render `rules:` yaml blocks → anchored sections
        md.write_text(render_rule_blocks(md.read_text(encoding="utf-8")), encoding="utf-8")
    index = build_src / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + _APIDOCS_TOCTREE, encoding="utf-8")
    shutil.copy2(CONF_PY, build_src / "conf.py")

    env = dict(os.environ, CONJURED_AUTODOC2_SRC=str(SRC_PKG))
    result = subprocess.run(
        [sys.executable, "-m", "sphinx", "-b", "html", str(build_src), str(out_dir)],
        capture_output=True, text=True, env=env,
    )
    combined = result.stdout + result.stderr
    cats, uncategorised = _categorise(combined)

    print("=" * 68)
    print(f"docs site -> {out_dir}   (exit {result.returncode})")
    print("=" * 68)
    if cats:
        print("warnings by category:")
        for label, n in cats.most_common():
            print(f"  {n:>4}  {label}")
    else:
        print("no warnings.")
    if uncategorised:
        print("\nuncategorised warning/error lines (first 25):")
        for line in uncategorised[:25]:
            print(f"  {line}")
    if result.returncode != 0:
        # A hard failure (e.g. a missing extension) often carries no
        # WARNING/ERROR-tagged line — never let it print as "no warnings."
        print("\nBUILD FAILED — raw sphinx output (last 30 lines):")
        for line in combined.strip().splitlines()[-30:]:
            print(f"  {line}")

    if (out_dir / "index.html").is_file():
        print(f"\nopen: {out_dir / 'index.html'}")
    if not keep_src and build_src.exists() and result.returncode == 0:
        shutil.rmtree(build_src)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(PKG_DIR / "_build" / "docs-site"),
                        help="output directory (default: <package>/_build/docs-site)")
    parser.add_argument("--keep-src", action="store_true",
                        help="leave the preprocessed source tree next to the output")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252
    except Exception:
        pass
    return build_site(Path(args.out).resolve(), keep_src=args.keep_src)


if __name__ == "__main__":
    raise SystemExit(main())
