---
kind: reference
audience: [authors, integrators, agents]
slug: testing-reference
component: testing
---

{#testing-reference}
# Testing reference

How a composed pipeline and its handlers are tested. This reference owns the
**runtime-testing discipline** — the testing paradigm, the rule for when a
handler may be called directly versus dispatched, the test-double substitution
discipline for the mock library, and the structural test-shape invariants. It is
the home an author or coding agent reaches for the question "how do I test this
handler / this pipeline?"

What lives here:

- the runtime-testing paradigm (classical/Detroit-school, contract fixtures, one
  sanctioned substitution site);
- the **boundary-exercise predicate** (direct invocation versus runner dispatch);
- **compositional verification** (the canonical event stream as the proof surface);
- the **test-double substitution** discipline the mock library builds on
  (training-capture, transport shape, production exclusion, twin integrity);
- the **anti-pattern catalog** (trained testing reflexes that break the contract);
- the **derived rules** `R-testing-NNN`.

What does NOT live here:

- the *vocabulary* of test doubles — owned by the glossary
  [test double](#test-double) and [fake service](#fake-service);
- the compose-time twin-substitution *mechanism* — owned by the handler reference
  § Test substitution (transcluded below where this discipline depends on it);
- the `conjured.testing` *API surface* (the public seam helpers, the fixture-harvest
  utility, the hash-gated loader, the registry-isolation plugin) and the
  `<lib>_test` twin-package layout — owned by the sibling testing API doc's
  [owned surfaces](#testing-api/owned-surfaces), the designated owner of those
  contracts (the pytest plugin's included);
- the engine's own behaviour under test (what the engine guarantees) — owned by the
  [trust model](#architecture-trust-model) and the per-component conformance docs.

{#the-runtime-testing-paradigm}
## The runtime-testing paradigm

Conjured testing is **classical (Detroit-school)**: call real functions by default
and substitute only at the edges, rather than isolating every unit behind doubles.
On top of that sits a **contract-fixture** model (the Pact family): a single fixture
artifact is asserted from both sides of a seam, so two fast solitary tests jointly
emulate the integration test without standing the integration up.

{#the-channel-is-the-seam}
### The channel is the seam

Handlers never call each other. Every seam between two handlers is a declared,
typed [channel](#channel); a handler is a [node](#node) that reads its declared
input ports and returns a fresh output dict the runner projects onto the channels
its write-map names. Because the seam is the channel, a captured run already
produces the middle-of-pipeline artifacts a test needs — there is no separately
authored fixture corpus.

By the [pipeline-as-training-contract](#glossary-pipeline-as-training-contract) collapse, **a
captured run's channel records serve directly as contract fixtures** — the artifact a
test needs is the artifact the run already produced, so a fixture cannot drift from
the contract it fixes. (How a suite harvests, stores, and loads those records is
downstream test-tooling, not engine canon.)

{#bidirectional-contract-assertion}
### Bidirectional contract assertion

A seam is asserted from both sides against the one fixture, by value — never by
interaction verification. The **producer** handler's test asserts its output port
equals the seam fixture; the **consumer** handler's test feeds that same fixture to
its reads and asserts the output it produces. The "what reached the backend" half is
read the same way: a test compares the captured `service_invocation` input payload
against the fixture — consumer-side evidence drawn from the event stream, not a
verdict a double renders. Two solitary, fast tests sharing one fixture jointly carry
the guarantee an end-to-end test would, without the end-to-end cost.

{#substitution-at-the-adapter-seam}
### Substitution has one sanctioned site

:::{transclude} test-substitution/sanctioned-site
:::

The mechanism — twin handlers and twin composition declarations — is owned by the
handler reference § Test substitution. The discipline this reference adds is the
*library* around that mechanism: § Test-double substitution below.

{#fixtures-harvested-and-hash-gated}
### Fixtures are harvested, not handwritten — and hash-gated

A contract fixture MUST be **harvested** from a real or fake-backed run, never
handwritten: a handwritten fixture asserts what its author believed the seam
carries, not what the composition actually produces. Each harvested fixture records
the [pipeline-hash](#pipeline-hash) it was captured under, and the
loader flags a fixture whose recorded hash no longer matches the current
composition ("predates the composition — re-harvest"). This is the same drift
mechanism the [training-bundle-hash](#training-bundle-hash) uses, turned on
fixtures — the structural answer to snapshot rot, available because
the engine has a composition-identity hash.

{#verified-fakes}
### Verified fakes

Every shipped fake passes the same conformance assertions a real implementation
does: **a fake MUST fail wherever the runtime would.** A fake adapter validates its
`invoke(...)` arguments against the service-type's declared input shape and returns
shape-matching output; a fake that accepts a payload the real backend would reject
is not a test double, because a green test against it proves nothing.

:::{region} verified-fakes/output-asymmetry
The output half of the fail-where-the-runtime-would discipline is **asymmetric by
node kind**: at a [trainable](#trainable)
composition node the engine validates the fake's return against
`trainable.output_schema` ([R-handler-005](#R-handler-005)'s literal-equal verdict),
so a wrong-shaped canned response still fails the run; at a plain service binding
the response reaches the calling handler exactly as returned — the engine's only
downstream verdict is the caller's own return contract (a service handler's
`output_schema` validation; the return-`None` check for an emitting hook), never
the service response itself — so there the fake's `respond` is the sole guarantor
of the response shape.
:::

The same
RED-on-removal logic verifies any guarantee or seal a test claims — the case that
would fail if the mechanism were removed, not coverage alone (`R-testing-008`). Compose
once and dispatch many — a session-scoped compose fixture mirrors the kernel's
construct-once / invoke-many shape — for speed without weakening the assertions.

{#the-boundary-exercise-predicate}
## The boundary-exercise predicate

The predicate decides, mechanically, whether a handler may be tested by a direct
call or must be dispatched:

> **Direct invocation is permitted if and only if the handler is a transform that
> declares no `bindings.<name>` table. Every other handler MUST be exercised through
> the engine runner — the real dispatch path — never by calling its bare function.**

A bindings-free transform is pure computation over its declared ports
([R-handler-004](#R-handler-004)): a bare call faithfully tests its content
computation — the mapping from its input ports to its output ports. (The engine still
validates the return against `output_schema` and emits the canonical events on
dispatch; the predicate intentionally treats that engine-added machinery as not under
test for a pure transform.) Every other handler reaches its inputs
through the engine-constructed dispatch wrapper ([R-handler-001](#R-handler-001)),
which interposes machinery the bare function never sees:

- per-dispatch delivery of resolved `bindings.<name>` values (a fresh per-dispatch
  copy, or the deep-frozen shared value for `delivery = "reference"`);
- service-binding resolution and adapter routing for every `services.<name>.invoke(...)`
  call;
- hook transport delivery from the deployment;
- `output_schema` validation of the return and emission of the canonical events.

A transform that declares a `bindings.<name>` table still has those values delivered
by that machinery, so it too is dispatched, not called bare. Testing a
dispatch-bearing handler by its bare function asserts behaviour the composed
pipeline never produces — the failure the [dispatch-bypass incident](#layered-defense)
records, where a harness that skipped dispatch trained the wrong contract.

The rationale generalises: **content assertions may go bare; anything that touches
the contract goes through the engine.** A pure transform's output is content; a
binding value, a service response, or a hook delivery is contract, and the only
faithful way to put it under test is the real dispatch path.

{#compositional-verification}
## Compositional verification — the event stream is the proof surface

A test does not inspect engine internals to confirm a handler ran correctly under
dispatch; it observes the **canonical event stream**. A consumer attaches a
`logging.Handler` to `conjured.events.runner` (the engine ships none — capture is a
test-side opt-in) and reads the typed event objects. The pair that carries a
dispatch is `handler_enter` (its `reads_snapshot` is the projection over the node's
declared `reads` input ports, populated from their read-map-wired channels) followed
by `handler_exit` (its `writes_snapshot` is the projection over the node's declared
`output_schema` output ports, taken before the write-map routes them onto channels;
`null` for a hook — key present, value `null`, since hooks write nothing). A test asserts the
composition by feeding a canned backend response and checking that
`handler_enter.reads_snapshot` plus that response yield the expected
`handler_exit.writes_snapshot` — the same `handler_enter` / `handler_exit` pair the
engine captures as the training record. The exactly-one verification path is the
real dispatch path; the event stream is how a test reads it. `caplog` scoped to the
engine logger is the companion surface for the engine's own logged warnings (an
absorbed hook failure, a namespace revert).

{#test-double-substitution-discipline}
## Test-double substitution — the mock-library discipline

The mock library is the catalog of verified fakes and twin declarations that realise
§ Substitution at the adapter seam. Its substitution swaps in [fakes](#fake-service),
never interaction-verifying [mocks](#test-double), and never runtime patching. The
discipline below is what the library must hold to; the API that ships it is authored
with the code.

{#fake-trainable-vs-fake-service-type}
### Training capture follows kind, not double

Whether a substituted backend's invocations land in the training corpus is
determined by **composition kind**, not by which kind of double is bound. A fake
bound to a [trainable](#trainable) composition node preserves the
`handler_enter` / `handler_exit` training-capture path — the engine-constructed
dispatch is unchanged, so the pair is captured exactly as in production. A fake bound
at a service-kind node has no training capture, because a service node emits no
trainable channel. The mock/fake vocabulary never carries this distinction; the
taxonomy enforces it through the trainable composition kind, as the handler
reference § Test substitution owns.

{#fake-service-type-transport-shape}
### A fake service-type still declares transport

A fake service-type is a real service-type declaration under a distinct qualified
name, so its `[transport_schema]` is required and body-required ([R-service-type-001](#R-service-type-001)):
it MUST declare at least one transport field even though it reaches no external
dependency. A fixture-path or canned-response selector is the legitimate transport
value — it is the per-deployment connection config a fake still needs to choose what
to return — and the fake deployment supplies it. The fake's transport shape is its
own; contract-shape parity with the production twin is the field set the *handler*
sees, not the transport field set.

{#production-deploy-exclusion}
### Fake packages are excluded from production

A fake backend or a `<lib>_test` twin package is a development dependency only. It
MUST NOT appear in a production pipeline declaration or be installed in a production
deployment. The production pipeline-hash must reflect the production composition; a
fake bound in production would emit records into the training corpus under the
production hash, corrupting it. The separation is structural — fakes live in the
twin package, absent from production deploy specs — not a runtime guard.

{#twin-declaration-integrity}
### Twin-declaration integrity — descriptions propagate

A test twin differs from its production counterpart in exactly one respect — the
service-type binding's `type` — and is otherwise identical, field for field. A
`trainable.output_schema` field `description` is **model-facing contract content** — it
conditions the backend's constrained generation and folds into the training-bundle-hash
([R-handler-005](#R-handler-005); [hash-model § Training-bundle-hash](#training-bundle-hash-construction))
— so a twin that drops or rewords one changes what the fake is asked to produce *and* drifts
the hash. The re-export shim (service kind) and the by-construction copy of the declaration
(trainable kind) propagate descriptions by identity, so the twin carries them without
re-authoring and cannot drift from production.

{#anti-patterns}
## Anti-patterns — trained reflexes that break the contract

Agents and engineers arriving from mainstream Python testing carry reflexes that
land wrong in Conjured. Each entry names the trigger, the Conjured form to use, and
the contract the reflex breaks.

{#anti-pattern-mocked-airgap}
### Mocked airgap

When you would mock the engine so a handler's test passes without a real run:
dispatch through the engine runner — the real dispatch path — instead. A suite that
mocks its way to an airgap from the engine tests nothing about the composition — the
canonical failure
where mocks made tests independent of the engine and a breaking refactor passed
green (the [mock-isolation incident](#layered-defense)). The verification path is the
real dispatch path; there is no second one.

{#anti-pattern-monkeypatch-resurrection}
### Monkeypatch resurrection

When you reach for `unittest.mock.patch`, `monkeypatch.setattr` on a handler or
engine internal, a dependency-injection swap, or a service-locator override:
substitute at the adapter seam by editing the test composition's binding `type`
instead (§ Substitution at the adapter seam) —
[runtime patching attests a composition that did not run](#test-substitution/runtime-patching-attests),
the derivation the handler reference owns. The
trained reflex is strong; the redirect is compose-time twin substitution, every time.

{#anti-pattern-snapshot-assertions}
### Snapshot assertions

When you would assert an entire handler output by equality against a stored blob:
assert against a **typed, seam-scoped, hash-pinned, bidirectional contract fixture**
instead. The anti-pattern is *unstructured whole-output snapshot-equality as the
assertion of record* — it pins incidental shape, rots silently, and says nothing
about the contract. A harvested channel-record fixture is the sanctioned form: it is
the channel's declared type, scoped to one seam, pinned to the pipeline-hash it was
captured under, and asserted from both producer and consumer sides.

{#anti-pattern-async-test-scaffolding}
### Async test scaffolding

When you reach for `pytest-asyncio` or an async test harness: write a plain
synchronous test. The engine runner returns a `RunResult` synchronously and the
canonical events are synchronous logging records; there is no async surface to await,
so async scaffolding adds ceremony and a nondeterministic scheduler for no gain.

{#anti-pattern-class-based-tests}
### Class-based test suites

When you reach for an xUnit-style `TestCase` class hierarchy: write module-level
test functions with fixtures. The class wrapper adds inheritance ceremony without
buying anything the compose-once / dispatch-many shape does not already give; the
suite shape is plain functions over shared fixtures.

{#anti-pattern-hybrid-real-dependency}
### Hybrid real dependency for speed

When you would wire a test to a real database or service "because the fake is slow to
build": stand up a verified fake at the adapter seam instead. A real external
dependency makes the test nondeterministic and couples it to state the engine does
not own (violating no-real-external-services). Speed comes from the session-scoped
compose fixture and a deterministic fake, not from borrowing production
infrastructure.

{#testing-derived-rules}
## Derived rules

:::{transclude} derived-rules-convention/kernel
:::

The invariants these rules cite are
**universal** — every test author holds to them — while the mechanical audit scripts
that enforce them run in first-party CI; a third-party author receives the rules as
discipline (rules for us, tools for everyone).

```yaml
rules:
  - rule_id: R-testing-001
    name: no runner-dispatch bypass
    derived_from: [I1, I4]
    enforcement: review
    statement: |
      A test of any handler that is not a bindings-free transform MUST dispatch it
      through the engine runner — the real dispatch path — never by calling its bare
      function. The boundary-exercise predicate draws the line: direct invocation is
      sanctioned only for a transform that declares no `bindings.<name>` table, because
      such a body is pure computation over its declared ports (`R-handler-004`) and a
      bare call faithfully tests that content computation. Every other handler reaches
      its inputs through the engine-constructed dispatch wrapper (`R-handler-001`) —
      per-dispatch binding delivery, service-binding resolution and adapter routing,
      hook transport delivery, output validation, and event emission — so a bare call
      asserts behaviour the composed pipeline never produces and trains the wrong
      contract (the dispatch-bypass incident).
  - rule_id: R-testing-002
    name: substitution only at the adapter seam
    derived_from: [I3, I4]
    enforcement: review
    scope: |
      First-party CI holds the engine's own suite to the no-engine-mock half
      mechanically over the engine-internal modules; for consumer test code the rule
      is review-enforced.
    statement: |
      A test MUST substitute a real backend only by compose-time twin substitution at
      a declared service-type binding — the one sanctioned site. It MUST NOT patch,
      monkeypatch, mock, dependency-inject, or service-locator-swap a handler's
      internals or any engine module — the derivation (runtime patching attests a
      composition that did not run) is owned at the handler reference's
      [runtime-patching fragment](#test-substitution/runtime-patching-attests).
      A transform has no substitution surface and is called
      real.
  - rule_id: R-testing-003
    name: no skipped tests
    derived_from: [I1]
    enforcement: review
    statement: |
      A test suite MUST NOT contain `pytest.mark.skip`, `skipif`, `xfail`,
      `pytest.skip(...)`, or `importorskip`. A registered case that silently does not
      execute is an undeclared coverage gap — the test-layer face of no implicit
      contracts (`I1`): it claims coverage it does not provide. A case that cannot yet
      pass is removed with its unbuilt feature, not parked green.
  - rule_id: R-testing-004
    name: no real external services in tests
    derived_from: [I2, I3]
    enforcement: review
    statement: |
      A test MUST NOT reach a real LLM, ML model, database, or network service.
      External services are consumer territory (`I3`); their adapters are substituted
      by a verified fake at the seam (R-testing-002), and a fake is deterministic
      (`I2`). The fake fails wherever the real backend would, so a green test against
      it carries weight.
  - rule_id: R-testing-005
    name: at least one test module per registered unit
    derived_from: [I1]
    enforcement: review
    statement: |
      Every registered handler and validator MUST have at least one dedicated test
      module. Exhaustive declaration (`I1`) extends to the test layer: a declared unit
      with no test home is an unverified contract. Both the happy path and each error
      path get at least one case, asserting the correct output or the structured
      `ContractViolation` / `SchemaValidationError`, never a bare traceback.
  - rule_id: R-testing-006
    name: production references only production handlers
    derived_from: [I4]
    enforcement: review
    statement: |
      A production pipeline declaration and a production deployment MUST reference only
      production handlers and bindings. A fake backend or a `<lib>_test` twin package
      MUST NOT appear in a production pipeline or be installed in a production
      deployment — they are development dependencies, structurally absent from
      production deploy specs. The production pipeline-hash must reflect the production
      composition; a fake bound in production emits records into the training corpus
      under the production hash, corrupting it (`I4`).
  - rule_id: R-testing-007
    name: twin-declaration integrity
    derived_from: [I4]
    enforcement: review
    statement: |
      A test twin MUST differ from its production counterpart in exactly one respect —
      the service-type binding's `type` (its qualified name) — and be otherwise
      identical: the same ports, the same `output_schema`, the same
      `trainable.output_schema` field `description`s. A trainable output_schema field
      `description` is model-facing contract content that conditions the backend's
      constrained generation and folds into the training-bundle-hash (`R-handler-005`;
      hash-model § Training-bundle-hash), so a twin that drops or rewords one changes
      what the fake is asked to produce and drifts the hash. For
      the service kind the twin's Python module is a one-line re-export shim; for the
      trainable composition kind the twin is a second composition declaration
      differing only in `trainable.service_bindings`. Byte-identity propagation
      preserves the rest without re-authoring.
  - rule_id: R-testing-008
    name: guarantees need a failing-case test
    derived_from: [I1, I4]
    enforcement: review
    statement: |
      A test that vouches for a behavioral guarantee or seal — "never propagates", "is
      isolated", "cannot mutate", "fails loud", "closed enum", any affirmative "always X /
      never Y" — is not verified until a case would go RED if the mechanism were removed.
      Coverage (`R-testing-005`) is necessary but not sufficient: a green suite proves nothing
      about a seal whose failure case no test exercises — the input merely happened not to trip
      it. The author MUST construct the exact adversary the claim defends against (the consumer
      that does raise, the value that does violate, the body that does mutate) and assert the
      guarantee holds against it. The engine's own seals carry this structurally (the seal->test
      binding); a consumer's guarantee-claiming test carries it by review.
```
