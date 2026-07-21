---
kind: reference
audience: [authors, integrators, agents]
slug: testing-api
component: testing
---

{#testing-api}
# Testing API reference

The `conjured.testing` package — the harness that realises the runtime-testing
discipline. It drives the **real engine runner** and reads the **canonical event
stream**; it never bare-calls a dispatch-bearing handler and never mocks engine
internals. This reference documents the public API surface; the discipline each
piece enforces is owned by the testing reference and is cited by rule below, never
restated here.

:::{region} testing-api/owned-surfaces
What lives here: the seam helpers, the verified-fake base, the contract-fixture
utilities, the pytest plugin, and the `<lib>_test` twin-package shape.
:::

What does NOT
live here: the discipline itself (the boundary-exercise predicate, compositional
verification, the substitution mechanism, the test-shape invariants `R-testing-NNN`)
— owned by the testing reference; and the engine's own runtime contracts (the runner,
the event types, the adapter seam) — owned by their component references.

**Harness errors are not runtime error-channel classes.** The signals this API raises —
`BoundaryViolation`, `StaleFixtureError`, `AmbiguousServiceCapture` (each a subclass of the
exported common base `TestingError`, the one class a consumer `except` clause catches to mean
"any harness signal"), and a plain `LookupError`
for a missing dispatch — are the harness's own. They are **not** members of the runtime error
channel's closed set of error classes, whose exhaustiveness claim scopes to the **engine's
runtime error channel**, never to this test harness.

{#direct-handler-access}
## Direct handler access — `get_handler_fn`

`get_handler_fn(registry, qualified_name) -> Callable` returns a registered handler's
bare kwarg-only function for a **direct call**, enforcing the boundary-exercise predicate
([R-testing-001](#R-testing-001)) structurally. It returns the function only for a
bindings-free transform, and raises `BoundaryViolation` for anything else — a service, a
hook, a transform that declares a `bindings.<name>` table, or any name not registered as a
handler (which includes a trainable composition, addressed by path rather than as a
handler). The returned function is the real resolved callable (the engine's resolution
runs — the pre-import source audit and the signature check); a direct call is legal only
because [R-testing-001](#R-testing-001) holds it so for a pure transform. Anything the
predicate refuses must be dispatched through the engine runner instead (see
[§ Building a composition](#building-a-composition)).

{#compositional-verification-api}
## Compositional verification — `capture_events`, `run_and_capture`, `inspect_state`

A test confirms a dispatched handler ran correctly by observing the canonical event
stream, not by inspecting engine internals.

- `capture_events()` is a context manager that attaches a consumer handler to the
  canonical event channel for the duration of the block and yields the list the events
  accumulate into; it detaches on exit. Delivery is synchronous, so on the dispatched
  run's return the list already holds the complete position-ordered stream. Use it
  directly around an error-path run (under `pytest.raises`) so the partial stream,
  including the run's terminal error event, is still observable.
- `run_and_capture(runnable, inputs, **run_kwargs) -> (RunResult, list)` is the
  happy-path convenience: it dispatches `runnable` through the **engine runner — the
  real dispatch path** — and returns the result with the captured stream.
- `inspect_state(events, position) -> NodeState` reads one dispatched node out of a
  captured stream, selected by `handler_position` (the
  [dispatch-identity key](#canonical-event-types/dispatch-identity) hash-model
  owns). `NodeState` carries the node's `reads` and `writes` snapshots (the
  training pair; `writes` is `None` for a hook, which writes no channels) plus, for a
  service dispatch, the wire-visible `service_input` / `service_output` payloads. A
  missing dispatch at `position` raises `LookupError` — a hole the consumer should see,
  never a silent empty result; a service position with **more than one** captured
  `service_invocation` (a buried multi-call — the engine holds a service to exactly one
  external call per dispatch, review-enforced) raises `AmbiguousServiceCapture` rather than
  laundering the ambiguity into one arbitrary invocation. This is the read that proves the
  composition: feed a canned response and check that a node's `reads` plus that response
  yield the expected `writes`.

{#building-a-composition}
## Building a composition — `load_test_pipeline`, `load_test_deployment`

- `load_test_pipeline(pipeline, registry, *, name=None, deployment=None, file_path=...)`
  compiles and assembles a composition into the engine's assembled, runnable form ready for
  the engine runner.
  `pipeline` is an in-memory pipeline declaration, a TOML string, or a path to a TOML
  file; `name` defaults to the declaration's `meta.name`. The caller populates
  `registry` (the handler / service-type declarations the pipeline references) — the
  helper invents no directory layout. Compose-time failures raise `ContractViolation`
  before any dispatch, exactly as in production; the helper adds no swallowing.
- `load_test_deployment(deployment, *, file_path=...) -> DeploymentDeclaration` parses a
  deployment declaration (a `DeploymentDeclaration`, a TOML string, or a TOML file path)
  for use as the `deployment=` argument above. A deployment has no compile step of its
  own, so this is a thin parse wrapper; a malformed declaration raises `ContractViolation`.

{#verified-fakes-api}
## Verified fakes — `VerifiedFake`

Substitution happens at the **adapter seam** via compose-time twin substitution (swap
the test composition's binding `type` to the fake service-type's qualified name) — never
runtime patching. `VerifiedFake` is the base a fake adapter subclasses. It records each
call's closed dispatch arguments on `calls` (the "was-called-with" record a consumer
asserts by value), exposes `validate_input(input_payload)` — the hook for the
fail-where-the-runtime-would discipline the testing reference owns (§ Verified fakes;
[R-testing-008](#R-testing-008) is its failing-case-test teeth): override it to reject a
payload the real backend would refuse — and `respond(input_payload)`, which returns the
canned shape-matching output. What checks that output downstream is asymmetric by node
kind:

:::{transclude} verified-fakes/output-asymmetry
:::

The base does not define `invoke`: a fake reached through real twin-substitution
resolution is signature-checked against the service-type's declared invoke shape (the
closed engine-supplied dispatch arguments plus the service-type's own config and transport
surface — the service-type reference owns that contract), so the
exact signature is service-type-specific. The concrete fake declares its `invoke` matching
that shape and delegates the body to `_invoke(...)` (record → `validate_input` →
`respond`). A trainable fake additionally sets the certification attributes the
trainable-backend gate requires; binding it at a trainable node preserves the capture path
unchanged (capture follows composition kind, not the double).

{#contract-fixtures-api}
## Contract fixtures — `harvest`, `write_fixtures`, `load_fixture`, `load_fixture_unchecked`

A captured run's channel records are the contract fixtures (harvested from a run, never
handwritten), each stamped with the composition's `pipeline_hash` and gated against drift.

- `harvest(runnable, inputs, *, pipeline_run_id=None) -> list[SeamFixture]` runs the
  composition once through the engine runner and returns a per-node `SeamFixture` for
  every dispatch, each stamped with the composition's pipeline-hash; `pipeline_run_id` is
  echoed to the run as its id. Bind a fake at the adapter seam first for a fake-backed
  harvest. `SeamFixture` carries the node's `reads` / `writes` snapshots (`writes` is
  `None` for a hook) and, for a service dispatch, its `service_input` / `service_output`; a
  service position with more than one captured invocation raises `AmbiguousServiceCapture`
  (the same buried-multi-call guard as `inspect_state`), never a silently-harvested arbitrary one.
- `write_fixtures(fixtures, directory) -> list[Path]` writes each fixture as one JSON
  file under `directory`.
- `load_fixture(path, runnable) -> SeamFixture` is the **safe-by-default load** — the hash
  check is intrinsic: it reads a fixture back and gates it against `runnable.pipeline_hash`,
  raising `StaleFixtureError` when the recorded pipeline-hash differs from the current
  composition's, so a fixture that predates a composition edit can never silently assert a
  stale contract. The hash-gating discipline is owned by the testing reference (§ Fixtures
  are harvested … and hash-gated).
- `load_fixture_unchecked(path) -> SeamFixture` reads one back with **no** hash check — the
  raw-read primitive `load_fixture` composes. Its sanctioned use is raw fixture inspection or
  re-harvest tooling, where no composition is in hand to gate against; prefer `load_fixture`
  whenever a runnable is available.

{#the-pytest-plugin}
## The pytest plugin — registry + import isolation

Installing the engine registers the `conjured.testing` pytest plugin (the `pytest11`
entry-point), which provides two fixtures:

- `conjured_registry` — a fresh, empty declaration registry per test. The registry is
  instance-scoped, so this is the whole of registry isolation (no global state to
  restore).
- `module_writer` — `write(name, source) -> name` writes an importable handler/adapter
  module under a per-test directory on the import path and, on teardown, evicts exactly
  the modules loaded from that directory and restores the path. This isolates the
  process-global import system (the real cross-test contamination surface, since the
  engine resolves a handler by importing its module), so a module name is safe to reuse
  across tests. The isolation rides this fixture rather than being automatic, so a suite
  that writes no modules is unaffected.

For the engine's own logged warnings (an absorbed hook failure, a namespace revert),
pytest's built-in `caplog` scoped to the engine's logger is the companion surface.

{#the-twin-test-package}
## The `<lib>_test` twin package

A test twin ships beside a handler library as a sibling `<lib>_test` package — a
development dependency, excluded from production deployments ([R-testing-006](#R-testing-006)).
For a service-kind handler the twin module is a one-line re-export of the production
handler function (one source of truth for the body); the test composition's service-type
binding `type` points at the fake's qualified name. Twin-declaration integrity — what the
twin may differ in, and the by-construction propagation of the load-bearing field
descriptions — is [R-testing-007](#R-testing-007).
