---
kind: explanation
audience: [authors, integrators]
slug: enforcement-modes-explanation
explains: ../architecture/enforcement-modes.md
---

{#enforcement-modes-explanation}
# Why two enforcement modes

The [enforcement-modes reference](#architecture-enforcement-modes) defines a
closed two-value enum — `mechanical` and `review` — and the rules for each. This
doc carries the *why*: why exactly two modes, why one specific rule
(R-handler-002) is review-enforced rather than a fallback for missing structural
enforcement, why a rule with wire-visible evidence still does not earn a third
value, and why the partition is drawn where it is.

{#why-two-modes-not-one-the-layered-defense-framing}
## Why two modes, not one — the layered-defense framing

The two modes are not an abstract taxonomy; each maps to a concrete failure class
observed in pre-engine work, and the pairing is what gives the engine
*layered defense* rather than a single brittle line.

A typed dataflow graph can mechanically reject a large class of violations: a
wrong key, a type mismatch, an undeclared channel fan-in (two or more
contributors with no `merge`), a handler whose
signature does not match its declared `reads`. These are visible at boundaries the
runner controls — declaration load, compose time, dispatch — so the engine simply
refuses to proceed. That is the mechanical mode, and where it reaches, it is
absolute: the violation cannot occur in a composed pipeline because the pipeline
will not compose.

But a second class of failure lives *inside the handler body*, in the space
between reading declared inputs and writing declared outputs — and that space is
structurally opaque to the runner. A body that catches an exception and returns a
plausible default, retries an external call and reports only the last attempt, or
writes to a database it never declared, produces output the runner cannot
distinguish from honest execution. No amount of type-checking reaches behavior the
type system cannot see. So the engine does not pretend to; it names this the
review mode and pairs it with adversarial review — falsification checklists that a
human reviewer or agent runs against handler bodies at library-publishing time.

Two modes, then, because there are exactly two *places* an engine-defined rule can
be held: at a boundary the runner sees (mechanical), or in body behavior it cannot
(review). The modes are complementary coverage of one contract surface — the
mechanical layer makes the violation structurally impossible where it can, and the
review layer catches, at the body, what the runner is blind to. Calling the second
layer "review" rather than leaving it unnamed is what makes the engine's scope
*honest*: the boundary between what is enforced and what is reviewed is stated, not
papered over.

{#adversarial-review-is-the-paired-methodology-not-a-weaker-tier}
### Adversarial review is the paired methodology, not a weaker tier

It is tempting to read "review-enforced" as "the rule we couldn't actually
enforce." That reading inverts the relationship. Adversarial review is the
*specifically correct* instrument for body-opaque rules, the way a type-checker is
the correct instrument for boundary-visible ones. A reviewer running a
falsification prompt — "show me this handler body cannot be silently swallowing a
failure" — is doing work the runner is constitutionally unable to do, not
substituting for it. The two modes are coordinate, not ranked: a review-enforced
rule is exactly as load-bearing as a mechanically-enforced one; it is simply held
where the evidence lives.

{#why-r-handler-002-is-review-enforced-handler-body-opacity-not-a-fallback}
## Why R-handler-002 is review-enforced — handler-body opacity, not a fallback

R-handler-002 (no silent fallbacks) is the sharpest case, because it is the one
people most expect to be mechanically enforced and are surprised to find under
review. The reason is structural, not a concession.

The silent-fallback failure mode *is* a handler-body choice: wrap an external call
in `try / except`, and on failure return a schema-valid default instead of
raising. At the dispatch-return boundary — the only place the runner observes — a
fallback default and a genuine result are indistinguishable: both are well-typed
values matching `output_schema`. The runner has no signal to fire on. Structural
*prevention* of a silent fallback at the handler-body layer is therefore
unavailable in principle, not merely unimplemented. R-handler-002 is review-
enforced because review is the only first-line defense that can reach the body
decision that produces the fallback.

What makes this more than "trust the reviewer" is the structural *second layer*:
the service-type adapter captures the `service_invocation` event from the
backend's actual response *before* control returns to the handler body. The body
cannot reach or alter that captured payload. So a consumer-side analyzer can
compare the captured backend response against what the handler returned and flag
the masking signature after the fact.

:::{transclude} R-handler-002/evidentiary-backing-classification
:::

The first line is review at the body; the backstop is
captured evidence — together a layered defense for a failure that admits no
single structural seal.

{#why-mechanical-evidentiary-backing-is-not-a-third-mode}
## Why mechanical evidentiary backing is not a third mode

Given that R-handler-002 has wire-visible evidence behind it, why not mint a third
enforcement value — something like "evidence-backed" — to capture that richer
status? Because doing so would conflate two structurally distinct questions:

- *Where is the rule held?* — at a runner-visible boundary, or in body behavior.
  That is what `enforcement` names, and it has exactly two answers.
- *What evidence does the engine emit?* — possibly none, possibly a captured
  canonical event a consumer-side analyzer can use.

These are independent axes. The enforcement value answers the first; the evidence
is described in the rule's statement body via cross-reference to the capture
surface. Folding the second axis into the first would make the enum's meaning
ambiguous — a reader could no longer tell from `enforcement: evidence-backed`
whether the rule is held mechanically, by review, or some unstated blend. Keeping
the enum two-valued, and letting the statement carry the evidentiary detail,
keeps each axis legible on its own terms. The closed two-value enum is the
structural expression of "there are two places a rule can be held," and that fact
does not change just because some review-held rules happen to have a wire signal
backing them up.

{#what-the-partition-is-and-is-not}
## What the partition is, and is not

The enforcement-mode taxonomy is narrow on purpose: it names the two places
engine-defined rules are *held*. It is deliberately **not** several adjacent things
it could be mistaken for:

- **Not a severity scale.** Mechanical and review are placements, not priorities.
  Reading "review" as "lower priority" would invite quietly downgrading
  review-enforced rules — exactly the rules whose violations are hardest to detect
  and most corrosive to the training corpus.
- **Not a CI scheme.** Which adversarial prompts run, when, and against what is a
  methodology question. Binding it into the enforcement mode would couple a stable
  contract property to an evolving operational practice.
- **Not the engine / consumer / review partition.** That broader meta-rule (owned
  by [principles](#engine-consumer-review-partition))
  decides which concerns the engine takes on at all — engine, review, or consumer
  territory. The enforcement-mode enum covers only the *engine* and *review*
  locations where engine-defined rules are held. Consumer territory has no derived
  rules here because the engine has no contract there to enforce; it is outside
  this taxonomy entirely, not a third enforcement value.

Each of these boundaries exists because the cheap misreading in each case —
severity, CI, the partition — would quietly expand or distort a contract surface
that the engine needs to keep small and exact.
