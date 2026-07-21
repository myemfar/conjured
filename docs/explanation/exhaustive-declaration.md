---
kind: explanation
audience: [authors, integrators]
slug: exhaustive-declaration-explanation
explains: ../architecture/exhaustive-declaration.md
---

{#exhaustive-declaration-explanation}
# Why exhaustive declaration

The [exhaustive-declaration reference](#architecture-exhaustive-declaration)
states the discipline — every applicable section header must appear, even when
empty — and classifies every engine-declared section header into one of the section-discipline modes. This
doc carries the *why*: why the engine inverts the mainstream omit-when-empty
convention, why the inversion is the right tool for a TOML-authored contract
system, and what failure mode it is designed to prevent.

{#the-section-headers-are-the-linter}
## The section headers ARE the linter

Mainstream config formats — `Cargo.toml`, `pyproject.toml`, Kubernetes manifests,
most framework configs — optimize for author ergonomics inside tooling-rich
ecosystems. An omitted key is fine because something *else* in the ecosystem
catches the omission when it matters: IDEs highlight missing keys, CI lints the
config, startup errors fire with specific remediation. The empty-equals-absent
convention is safe precisely because a surrounding safety net exists.

TOML has none of that structurally. It is text with brackets — no type system over
the file, no IDE that knows the engine's schema, no linter ecosystem that
understands what a handler declaration is supposed to contain. For a
strict-contract system authored in that medium, the question is where the safety
net comes from, and the engine's answer is to put it *in the declaration's own
structure*: **the section headers themselves ARE the linter.**

That is the whole reason the convention is inverted rather than borrowed. The
engine cannot rely on an external tool to notice a missing `reads` section, so it
makes the section's *presence* mandatory and its *absence* a load-time
[ContractViolation](#contractviolation). The check that
mainstream ecosystems push out to IDEs and CI, Conjured pulls into the contract
itself — where it cannot be skipped, because the runner refuses to load a
declaration that omits an applicable header.

{#empty-but-present-vs-forgotten-the-distinction-the-modes-protect}
## Empty-but-present vs forgotten — the distinction the modes protect

The inversion buys one specific thing: it makes "the author considered this axis
and declared nothing" **structurally distinct** from "the author forgot this
axis." Under omit-when-empty those two states are identical — an absent `reads`
section could mean *deliberately no reads* or *oversight*, and nothing in the file
can tell them apart. Under exhaustive declaration they are different artifacts: a
present-but-empty `[reads]` is the explicit "considered, declared nothing" signal;
an absent one is a forgotten axis, and the engine halts at load.

This is what the [section-discipline modes](#the-section-discipline-modes) are *for*
(the architecture reference owns the modes — names and definitions alike, at
[the section-discipline modes](#the-section-discipline-modes)). They are not
bureaucratic
classification — each mode is a precise answer to "what does emptiness mean for
this section?". Two illustrations carry the idea: where "considered and declared
nothing" is a meaningful, legitimate state, emptiness must be expressible but
presence must be mandatory (*required, empty-allowed*); where the declared
choice is itself load-bearing — the `training_contract` opt-in — emptiness is
itself the error (*required, body-required*).

The modes fall out of asking, section by section, what emptiness *means* there.
That is why the classification is mechanical rather than a matter of taste: the
meaning of an empty body is a property of the section, not a style choice.

{#the-legal-compliance-form-analog}
## The legal-compliance-form analog

The paradigm this borrows from is not software config at all — it is the **legal
compliance form**: a tax return, an audit checklist, a SOC 2 controls matrix. On
those forms every field is addressed, and where something does not apply you write
an explicit "N/A" rather than leaving the field blank. The reason is identical to
the engine's: the failure mode these forms are built to prevent is
**missing-by-oversight** — the blank that is supposed to mean "not applicable" but
actually means "nobody checked." Making the responder write "N/A" converts a silent
omission into an affirmative, auditable statement.

Exhaustive declaration targets exactly that failure mode in handler declarations.
An empty-but-present section is the engine's "N/A": an affirmative record that the
author considered the axis and chose nothing, which a missing section can never be.
The analog is worth stating because it reframes what can feel like ceremony — "why
must I write a section I'm not using?" — as the same discipline a compliance form
imposes for the same reason: so that *not using something* is a decision on the
record, not an absence that might be an accident.
