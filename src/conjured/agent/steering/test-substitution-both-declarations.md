---
kind: steering
audience: [agents]
slug: steering-test-substitution-both-declarations
renders_from: test-substitution-twin-handlers-twin-declarations-compose-time-binding-swap
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#steering-test-substitution-both-declarations}
# Steering — test substitution swaps the binding in every declaration that names it

**When this fires:** you are writing a test that substitutes a real backend with a fake,
and are about to swap the service-type name in only one place (the pipeline's
`service_bindings`), or to reach for `unittest.mock.patch` / monkey-patching instead.

**Do this:** substitution happens at **compose time**, in the declarations — swap the
service-type `type` name everywhere it is declared (the pipeline's `service_bindings`
AND each handler or trainable composition declaration's `service_bindings.<name>.type` —
they are matched exactly), so the test composition is a real composition with its own
pipeline-hash. Runtime patching modifies the running program without changing the
composition, which corrupts the training-capture claim the engine exists to protect.

The owning canonical statement:

{#test-substitution-twin-handlers-twin-declarations-compose-time-binding-swap}
### Test substitution — twin handlers / twin declarations, compose-time binding swap

Substituting a real backend with a fake for testing happens at **compose time** via the
pipeline declaration, not at runtime via function patching. The pattern differs slightly
by kind:

- **Service kind — twin handlers, one Python function.** Twin handlers preserve one
  source of truth for handler code; the twin's Python module is a one-line re-export
  shim:
  ```python
  # acme_dialog_test/handlers/detect_intent.py
  from acme_dialog.handlers.detect_intent import detect_intent
  ```
  Two registration handles point at the same bare function via separate
  `conjured.handlers` entry-points: `acme_dialog.detect_intent` (production) and
  `acme_dialog_test.detect_intent` (twin). The handler code lives once. What differs
  between production and twin is the **service-type binding** in the pipeline
  declaration, not the handler implementation:
  - Production pipeline: `service_bindings.llm` `type = "acme_dialog.structured_output"`.
  - Test pipeline: `service_bindings.llm` `type = "acme_dialog_fake.structured_output"`.
- **Trainable composition kind — twin composition declarations, no Python function.**
  The trainable composition kind has no author body per [R-handler-010](#handler-derived-rules);
  the trainable composition declaration IS the handler. Production and test ship as two
  trainable composition declarations differing in `trainable.service_bindings`:
  - Production trainable composition declaration: `trainable.service_bindings.llm`
    `type = "acme_dialog.qwen_trainable"`.
  - Test trainable composition declaration: `trainable.service_bindings.llm`
    `type = "acme_dialog_fake.qwen_trainable"`.
  Both bind trainable backends (per R-handler-008 expansion); the fake trainable
  backend's adapter returns canned responses while preserving the engine-constructed
  dispatch and the `handler_enter` / `handler_exit` training-capture path.

The composition validator sees both bindings at compose time. The pipeline-hash differs
between production and test composition by construction; for the trainable composition
kind, the [training-bundle-hash](#training-bundle-hash) on the
test trainable composition declaration also differs from production's (the
`trainable.service_bindings` qualified name is part of the composition declaration's
normalized hash). The fake backend's adapter validates `invoke(...)` arguments against
the service-type's declared input shape (catching handler-body assembly errors
structurally for service kind; catching engine-routed argument errors for the trainable
composition kind) and returns shape-matching output per the canned-response declaration.

**Why this pattern, not runtime patching.**

:::{region} test-substitution/runtime-patching-attests
`unittest.mock.patch`, monkey-patching,
dependency-injection swaps, or service-locator substitution all modify the running
program at dispatch time without changing the composition. The pipeline-hash sees the
production composition; the dispatch wrapper validates against the production schema; the
engine emits canonical events as if production code ran (per dispatch kind, the owned
event pairs — hash-model's
[§ Paired-event structure (service)](#paired-event-structure-service-kind) and
[§ (trainable composition)](#paired-event-structure-trainable-composition-kind)).
The training-corpus claim
("this composition produces this
training-record stream") becomes false in the test environment — the patched-in fake's
invocations would emit events into the training corpus under the production
pipeline-hash, violating
[I4 (pipeline-as-training-contract)](#invariants-and-derived-rules).
Twin handlers / twin composition declarations preserve I4 by moving substitution to
compose time where the composition validator and the pipeline-hash see it.
:::

:::{region} test-substitution/sanctioned-site
**The adapter seam is the only sanctioned substitution site.** Compose-time twin
substitution at a declared service-type binding is the engine's one substitution
mechanism, and the adapter seam is its one site: a test composition swaps which
backend a binding resolves to, and changes nothing else.
:::

A transform has no
substitution surface at all — it is called real (its body is pure computation
over its declared ports per R-handler-004, so there is nothing external behind
it to stand in for) — and no handler's internals are ever patched. Agents
trained on mainstream Python testing reach for `unittest.mock.patch` /
monkeypatching here by reflex; that pattern lands wrong in Conjured for the
reason the fragment above derives. Substitute by editing the test
composition's binding `type`; never by
patching.

The test-double library that builds on this mechanism — its verified fakes and twin
packages, the exclusion of fake packages from production deployments, the
propagation of load-bearing field descriptions into twins, and the verification
discipline that observes dispatch through the canonical event stream — is the
testing reference's territory.
Whether training records fire is determined by kind-based separation: the taxonomy
enforces it through the trainable composition kind, not through any property of the
service-type declaration.
