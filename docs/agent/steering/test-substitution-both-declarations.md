---
kind: steering
audience: [agents]
slug: steering-test-substitution-both-declarations
renders_from: test-substitution-twin-handlers-twin-declarations-compose-time-binding-swap
---

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
