---
kind: reference
audience: [authors, integrators, agents]
slug: architecture-trust-model
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#architecture-trust-model}
# Trust model

This is the canonical inventory of the ways author-supplied code can break the
[channel-record correspondence](#channel-record-correspondence)
that invariant I4 (pipeline-as-training-contract) depends on, and the structural
seal the engine commits against each. I4 — owned by
[principles](#invariants-and-derived-rules) — makes the
derivation load-bearing: the channel records the engine captures at runtime ARE
the training data. For that projection to be trustworthy, the values flowing
through channels must be the complete determinant of each handler's behavior, so
every mechanism by which author code could carry hidden state past the channel
records is enumerated here with its seal.

Each entry is a **vector** — a stable identifier (vector 1 … vector 7) that other
docs cite by number. The inventory is the single source of truth for what the
engine commits to seal: a vector not registered here is not sealed; a seal not
recorded here is not auditable.

---

{#the-vector-inventory}
## The vector inventory

| Vector | Mutation mechanism | Scope | Seal |
|---|---|---|---|
| 1 | Closure scope (the factory pattern) | Handler modules | Bare kwarg-only functions; no author-controlled outer scope |
| 2 | Instance state (`__init__` / `__call__` on a callable class) | Handler modules | Function-shape check at handler resolution |
| 3 | Module-level mutable state in handler modules | Handler modules | AST-walk audit at compose + module-dict snapshot-and-restore at dispatch |
| 4 | Mutable kwargs | Every kwarg the engine delivers | Per-dispatch copy of every delivered kwarg |
| 5 | External I/O at handler-module import | Handler modules; extends to adapter modules | R-handler-pure-module compose-time source-AST audit |
| 6 | Engine-blessed compose-time author state | Any author-controlled compose-time mechanism | Policy — the engine exposes no such surface |
| 7 | Above-instance-scope mutable state in adapter modules | Adapter modules | AST walk at adapter resolution |

(the-vector-inventory-qualified-seals)=

The compose-time source-AST scan shared by vectors 3, 5, and 7 is **min-viable by
ratified decision** — it recognizes the structural patterns at every scope that
executes at import: literal-form mutable assignments and import-time I/O at module
scope AND inside class bodies (class bodies execute at import, in handler and
adapter modules alike, nested classes included), function default-argument
expressions (which evaluate at import and persist on the function object), and
named caching decorators, against the recognized import-time-I/O roots (with the
pure path-construction surfaces — `pathlib`'s path constructors, `os.path`'s
string algebra — carved out: they perform no I/O and no instantiation). Call-form
and aliased patterns are a reactive tightening
boundary, hardened as real library modules surface them — as are the named
unscanned residuals: decorator-argument expressions, and class bases and keywords
(each executes at import; each is tail, not sealed). A function OR lambda
default-argument expression is inside the scanned surface for both pattern
classes — a mutable literal and import-time I/O alike. One further named boundary rides the fresh-resolution eviction
(handler-resolution § Hot-reload semantics): a `sys.modules` entry whose origin
differs from the just-audited file is outside the eviction's scope — there the
audited-vs-executed coherence that section names is not this seal's to deliver. Three seals carry a
qualification stated with their vectors below: vector 5 pairs that scan with a
documented admissible-imports boundary; vector 6's seal is a standing policy
commitment rather than a check against a present mechanism; and vector 7 runs that
scan alone — only vector 3 pairs it with a second, dispatch-time layer (the
module-dict snapshot-and-restore at
[R-handler-pure-module enforcement](#R-handler-pure-module-enforcement)), so
vector 3's seal is structural across both layers while vector 7's is the single
compose-time scan.

{#vector-1-closure-scope-the-factory-pattern}
### Vector 1 — Closure scope (the factory pattern)

**Mechanism.** An author-controlled compose-time closure — the
closure-factory pattern, where an outer function receives compose-time bindings
and returns the dispatch callable — gives the handler a private scope to stash
mutable state in. State written to that scope persists across every dispatch of
the composed handler.

**Scope.** Handler modules.

**Seal.** Bare-function handler shape. Handlers are bare
kwarg-only functions; the engine constructs the dispatch wrapper. There is no
author-controlled outer function, so there is no
closure scope to stash state in. The seal is structural — the affordance does not
exist.

{#vector-2-instance-state-init-call-on-a-callable-class}
### Vector 2 — Instance state (`__init__` / `__call__` on a callable class)

**Mechanism.** A callable class — `__init__` capturing compose-time bindings,
`__call__` serving dispatch — carries mutable state on `self` across dispatches,
the same escape as a closure under a different shape.

**Scope.** Handler modules. Adapter modules require the class shape; their
constraint is vector 7.

**Seal.** A function-shape check at handler resolution.

The predicate is `inspect.isfunction(x)` — admits `def` / `lambda` /
`@functools.wraps`-decorated functions; rejects classes, callable instances, bound methods,
builtins, and `functools.partial` results (a partial's pre-bound args would bypass the
declaration / `bindings.<name>` / hash surface). Resolution to any rejected shape raises
ContractViolation at compose time.

This seal is **necessary, not sufficient**: a bare
function can still carry cross-dispatch state through a closure (vector 1) or its
module namespace (vector 3), each sealed by its own vector. The seal is
structural; the exhaustive per-shape [admit/reject conformance
set](#function-shape-predicate-conformance-set) for the predicate is fixed at
handler resolution.

{#vector-3-module-level-mutable-state-in-handler-modules}
### Vector 3 — Module-level mutable state in handler modules

**Mechanism.** A module-level mutable binding (`_cache = {}`, a module dict, a
list), the same literal-form mutable binding inside a class body (a class body
executes at import, and the module-dict snapshot restores the class *reference*,
not the class's own `__dict__`), a mutable-literal default argument (a default
evaluates at import and the restored function object keeps its mutated
`__defaults__`), or a persistent caching decorator (`@lru_cache`, `@cache`,
`@cached_property` at namespace scope) persists across dispatches through the module
namespace.

**Scope.** Handler modules.

**Seal.** Two layers, enforced under [R-handler-pure-module](#R-handler-pure-module):
An AST-walk audit enforces at compose, run on the module source *before* import (per
[handler-resolution](#architecture-handler-resolution)) — a post-import audit cannot prevent
import-time I/O. A module-dict snapshot-and-restore around each dispatch enforces at runtime as a
defense-in-depth check, reverting any mutation the AST walk does not catch. A mutation the restore
cannot undo raises (fail-loud); the engine never continues past a partial restore.
Both layers are structural — prevention at compose, recovery at dispatch.

{#vector-4-mutable-kwargs}
### Vector 4 — Mutable kwargs

**Mechanism.** A handler mutates a binding or read value in place. If that value
is shared — the same object handed to another reader of the channel within a run,
or reused across dispatches of the composed pipeline — the mutation leaks: a later
reader or a later dispatch sees state the channel records never captured.

**Scope.** Every kwarg the engine delivers to a handler — bindings, reads, and a
hook's `transport_schema` fields alike.

**Seal.** Per-dispatch copy. The runner hands each dispatch its own fresh copy of
every kwarg — input ports projected-and-copied from their read-map-wired channels
at this node position, bindings copied per dispatch rather than shared as one
partial-applied object. The
handler receives ordinary mutable values (a normal `dict` / `list`, no
type-identity change); an in-place mutation touches only that dispatch's private
copy and is discarded when the dispatch returns. Nothing the handler can reach is
shared, so a mutation cannot leak — not across readers within a run, not across
dispatches. The seal is structural and fail-soft: an accidental mutation is a
harmless no-op against shared state, correct because mutating your own copy is not
a contract violation.

Copy is the seal rather than a frozen kwarg: a freeze only ever closes a
*shared* object, and copying every delivered kwarg sidesteps the freeze entirely —
nothing the handler can reach is shared, so nothing needs freezing. The
[full copy-vs-freeze derivation](#copy-vs-freeze-derivation) — why a shallow seal
and a recursive freeze each fail — is developed in the explanation plane. This pairs with
vector 3 under one
organizing principle: **copy what the engine hands in (kwargs); snapshot-and-restore
what it cannot replace (the module namespace the handler's code lives in).**

Two delivered kwargs are exempt from the copy. A binding resolved through the
`compile` directive is delivered as an already-resolved engine-owned kwarg —
neither copied nor frozen, covered by usage discipline; the
[handler reference](#handler)'s compile-directive sub-form owns that exemption
and its rationale. And
large static read-only data opts out via the
[reference binding](#reference-binding) subtype:
deep-frozen once at compose and shared across every dispatch, trading the
per-dispatch copy for an O(size)-once freeze — safe because the one-time deep
freeze leaves no mutable interior to leak, and fail-**loud** there is correct
(mutating reference data is always a bug). The reference-binding subtype's
definitive treatment — marker, deep-freeze mechanism, decision rule, caveats — is
owned by the handler component reference.

{#vector-5-external-io-at-handler-module-import}
### Vector 5 — External I/O at handler-module import

**Mechanism.** A filesystem read, a network call, or a client instantiation at
import-time-executing scope in a handler module — module top level, a class body,
or a function default-argument expression — executes at import time, inside the
engine load path,
carrying side effects and external state into composition.

**Scope.** Handler modules; the same constraint extends to adapter modules.

**Seal.** The R-handler-pure-module audit — a compose-time source-AST scan, run at
handler/adapter resolution before the module is imported, for top-level
filesystem / network / client-instantiation patterns — and a handler-component
audit entry. Pure library imports (`import re`, `import numpy`) remain
admissible — the seal targets I/O and instantiation at import, not imports
themselves. The compose-time scan covers the recognizable import-time-I/O patterns structurally;
the admissible-imports boundary is documented.

{#vector-6-engine-blessed-compose-time-author-state}
### Vector 6 — Engine-blessed compose-time author state

**Mechanism.** Any author-controlled compose-time mechanism — a closure-factory, a
compose-hook, an "engine-blessed place to put compose-time state" —
reintroduces an intra-pipeline mutable-state escape hatch. No such mechanism
exists in the engine today; the vector is the standing pressure to add one.

**Scope.** Any author-controlled compose-time mechanism.

**Seal.** A policy commitment, not a check against a present mechanism — the
engine exposes no compose-hook or closure-factory surface, and adds none.
Compose-time work routes to one of the
[three architectural homes](#compose-time-work-homes-homes) the handler
reference's § Compose-time work homes owns. The one
further engine-managed surface admitting author-supplied compose-time content — a
node's wiring maps (the read-map and write-map) — is a **data-only surface, not a
work home**: it can host no author code. The wiring-map surface is bounded by
construction: its admitted content is exactly a port-name → channel-name inline
table — a string-literal value per entry, data only. It admits no callable, no
expression or lambda, and — unlike a binding — no external-declaration file path;
the runner reads the wired channel name and nothing else. That data-and-inline-only
bound is stated explicitly precisely because the sibling binding home does admit
external file paths, so the wiring maps are affirmatively excluded from that
affordance rather than inheriting it: the surface adds no escape hatch. The
commitment is firm — no such surface is added without a vector revision here.

{#vector-7-above-instance-scope-mutable-state-in-adapter-modules}
### Vector 7 — Above-instance-scope mutable state in adapter modules

**Mechanism.** A service-type adapter is a class by construction. Class-level
mutable state — a class variable, `@lru_cache` on a method — or module-level cache
state in the adapter module persists beyond a single adapter instance, escaping
the composition lifetime that bounds the instance.

**Scope.** Adapter modules.

**Seal.** An AST walk at compose time, run at adapter resolution, enforces the
R-handler-pure-module adapter-module scope extension:

Adapter modules MUST NOT contain class-level mutable state (class variables, `@lru_cache` on
methods) or module-level mutable state. Instance state (initialized in `__init__` or assigned on
`self` elsewhere) IS admissible — adapter instances are engine-managed compose-time state bounded
by composition lifetime.

Same compose-time AST-walk mechanism as vector 3 (the min-viable scan qualified
above), broader scope — but without vector 3's dispatch-time second layer: an
adapter instance is engine-managed compose-time state, with no per-dispatch module
namespace to restore, so for adapters the compose-time scan is the whole seal.

The distinction from vector 2 is exact: handler modules forbid the class shape
entirely (handlers are bare functions); adapter modules require the class shape
(the adapter pattern) but constrain mutable state to instance scope.

---

{#auditing-a-new-feature}
## Auditing a new feature

Every new engine feature that touches author-supplied code is checked against this
inventory before it is authored:

1. Does the feature introduce any new author-controlled state?
2. If yes, what is its scope — closure, instance, class, or module?
3. If the scope is above instance, does it fall under existing vector 3 or vector
   7? If so, the existing seal covers it.
4. If it is a genuinely new vector, register it here, name its seal, and add the
   R-rule plus the audit.
5. Does the feature change which threat model Conjured covers? If yes, that is a
   separate architecture decision, not a vector addition.
6. Does the feature alter the structural-vs-documented classification of an
   existing concern? If yes, the reclassification is an architecture-review
   matter — resolve it before authoring, not inline.

---

{#forward-vector-protocol}
## Forward-vector protocol

When a new mutation vector is identified, registering it here means naming four
things:

a. **the mutation mechanism** — how author code would carry state the channel
   records do not capture;
b. **the scope** where it applies — which author-code category is exposed;
c. **the seal** — the enforcement mechanism, its R-rule, and its audit;
d. **the originating decision** — the design decision that introduced the surface.

---

{#library-publisher-contract}
## Library-publisher contract

The engine admits third-party handler, adapter, and validator packages — via
dotted-path resolution and the entry-point groups, whose roster the service-type
reference owns:

- **`conjured.handlers`** — bare-function handler discovery (additive alongside
  [dotted-path resolution](#dotted-path-resolution)).
- **`conjured.service_implementations`** — concrete service-implementation (adapter) discovery.
- **`conjured.validators`** — third-party field-[validator](#validator) discovery (the handler
  reference's § Validators owns the contract; named here for the group roster only).

A library publisher's package composes cleanly into a Conjured pipeline
only if its handler, adapter, and validator modules clear every vector in this
inventory.

The inventory is the contract surface. It states what a published package's code
must look like: bare-function handlers and validators, class-shaped adapters with
instance-scoped state only, no module-level mutable state, no import-time I/O. A
publisher audits a package against every vector before publishing; the audits this
doc names are the same audits the engine runs at resolution, so a package that
passes a pre-publish self-audit passes engine resolution by construction.
