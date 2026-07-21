---
kind: reference
audience: [authors, integrators, agents]
slug: architecture-handler-resolution
---

{#architecture-handler-resolution}
# Handler resolution
The engine resolves a handler **name** — the string a pipeline
declaration writes in a handler entry — to a bare-function callable it can
introspect and wrap for dispatch. This doc is the reference for that
mechanism: how a name becomes a callable, what the engine checks along
the way, and how each failure surfaces. Adapter resolution —
service-type adapters resolved from their own entry-points group — is
the sibling mechanism, covered alongside.

Resolution runs at **compose time**. Every failure mode below is a
compose-time [ContractViolation](#contractviolation);
nothing here can fail at runtime.

**Scope.** Handler resolution applies to
bare-function handlers — the
[handler kinds](#handler-kind). The [trainable](#trainable)
composition kind has no author Python file to resolve; trainable
composition declarations load via a separate path, not via this
mechanism.

---

{#resolution-mechanism}
## Resolution mechanism

Two discovery paths feed one resolution sequence.

**Primary — dotted-path module resolution.** A pipeline declaration names
a handler as a dotted path: `name = "mymodule.normalize_charset"`. The
engine splits the name at the final dot, imports the module prefix, and
reads the function off it — `importlib.import_module("mymodule")` then
`getattr(module, "normalize_charset")`.

The same resolved name may appear at more than one node in a pipeline: a
handler is channel-agnostic and each node carries its own wiring, so one
qualified name can resolve and dispatch at several node positions. Node
identity is the dispatch position, not the name — resolution itself is
unaffected, keying on `name` regardless of how many nodes share it.

**Additive — entry-points discovery.** A package ships handlers under
the `conjured.handlers` Python entry-points group, declared in its
`pyproject.toml`. A pipeline declaration then names the handler by the
short name the package registered. Entry-points discovery is additive to
dotted-path resolution, not a replacement — it exists so a published
package can expose handlers without the integrator knowing the
package's internal module layout.

**Adapters — the sibling mechanism.** Service-type adapters resolve
through the same resolution sequence, against the
`conjured.service_implementations` entry-points group — with the
adapter's **own selector**:

:::{region} adapter-selector/inverted-priority
Adapter resolution consults the entry-points group first, keyed by the **full
service-type qualified name** (dotted or not — an entry-point name may contain
dots), and falls back to dotted-path module resolution when no entry point carries
the name — the **inverse** of the dotted-path-primary, entry-points-additive
handler priority.
:::

A service-type qualified name is a **type
identity**, never coupled to the implementer's module layout — the
dot-presence selector that routes handler names would read every
dotted service-type name as a module path and could never reach the
group. The sequence below otherwise applies to adapter resolution
unchanged except at three points: **step 3** runs the
[trust-model](#trust-model-vector) vector-7 AST-walk audit (the
R-handler-pure-module adapter-scope extension — a source audit must
precede import, per step 3's own rationale); **step 5** checks the
class shape (an adapter is a class by construction, which the vector-2
function-shape check would reject); **step 6** checks the adapter's
`invoke()` signature against the closed dispatch-kwargs contract
(R-service-type-002/003).

**Native adapters — resolved ahead of the legs.** Before either leg of
the inverted selector runs, adapter resolution first consults the
engine's **native adapter table**: the engine-shipped map from a native
service-type qualified name to the engine's own shipped declaration and
its one registered implementation. A qualified name the table holds
MUST resolve through the table — the native consult precedes the
entry-points leg — and then continues through the
[resolution sequence](#resolution-sequence-compose-time) below: the
native passes the same source-AST audit, class-shape audit, and
signature check every adapter passes (the table supplies only
*discovery*, never a shortcut past the checks). Only a qualified name
the table does not hold falls through to the two legs above.

Because the native consult precedes the entry-points leg, a native
qualified name **cannot be shadowed** by a third-party
`conjured.service_implementations` registration under that name: the
engine's shipped implementation is reached first, and the group is
never consulted for that name. This is the resolution-layer face of the
engine-owned-identity guarantee ([R-service-type-004](#R-service-type-004)):
a native qualified name's implementation is necessarily the engine's
shipped one, never a value a later-installed package can supply.

**Validators — the third sibling.** Third-party field validators resolve through the
[adapter-style selector](#adapter-selector/inverted-priority) (D8), under the
`conjured.validators` entry-points group — a validator name MUST be namespaced (a bare name
is the closed standard-keyword space, never a third-party registration; the namespace rule
fails loud at first resolution). The step-3 source-AST audit, the step-5 function-shape check (a
validator is a bare kwarg-only function), and the step-6 signature check against `{value}` ∪ the
key's declared parameter names apply unchanged; a two-distribution collision on one qualified
name fails loud. R-handler-012 owns the contract; the handler reference's § Validators owns the
declaration grammar.

---

{#resolution-sequence-compose-time}
## Resolution sequence (compose-time)

The engine resolves each handler name through a fixed
sequence:

1. **Read the name.** The pipeline declaration names the handler in its
   handler entry: `name = "..."`.
2. **Locate the module spec without executing it.** For a dotted name,
   `importlib.util.find_spec(prefix)`; for an entry-points short name,
   the module the entry-point declares. `find_spec` also drives
   namespace-package rejection — a namespace package resolves with
   `origin is None`, and is rejected at this step, before step 3 reads the module
   source from that origin (a `None` origin would make the source read impossible).
3. **Source-AST audit before import.** The engine reads the module
   source from the spec's origin and runs the R-handler-pure-module AST
   walk plus the import-time-I/O scan against that source — before
   `importlib.import_module` executes the module's top-level code. A
   post-import audit cannot prevent import-time I/O: by the time a
   post-import check runs, the filesystem read or network call at
   module top level has already happened. Running the audit on source
   closes that gap. A violation raises `ContractViolation` here, before
   the module loads.
4. **Import and read the attribute.** `importlib.import_module(prefix)`
   then `getattr(module, suffix)` for a dotted name; `.load()` on the
   entry-point for a short name. A name that resolves to no module, or
   a module that does not export the named attribute, raises
   `ContractViolation` with a remediation hint.
5. **Function-shape check.** The vector-2 seal: the resolved object
   MUST satisfy `inspect.isfunction(x)`.

   :::{transclude} R-handler-bare-function/predicate-admit-reject
   :::

   The conformance set is fixed below.
6. **Bare-function signature introspection.** The engine introspects the
   resolved function's signature against the R-handler-001 signature
   union — the owned contract:

   :::{transclude} R-handler-001/signature-union
   :::
7. **Populate the resolution record.** The engine populates `HandlerEntry`,
   the immutable post-resolution record the runner dispatches from — its
   field set is fixed in [The HandlerEntry record](#the-handlerentry-record)
   below. **Deriving `package`** depends on the resolution path: entry-points →
   the resolved entry point's distribution name; dotted-path → the installed
   distribution providing the resolved module's top-level package
   (`importlib.metadata.packages_distributions()`, keyed on the top-level name;
   on a multi-distribution collision, the lexically-first). A module importable
   outside any installed distribution — a plain `sys.path` module, e.g. a test
   fixture — has no distribution; `package` is then its **top-level module
   name**, the best-available attribution. This never raises: `package` is
   attribution metadata, not hashed.

For adapter resolution the sequence is identical, with the discovery
at step 2 driven by the native adapter table for a native qualified name
and otherwise by the [inverted selector](#adapter-selector/inverted-priority)
above, step 3
applying the adapter-module scope extension of R-handler-pure-module
(the vector-7 AST-walk audit, run pre-import exactly as step 3's
rationale requires), step 5 checking the class shape (an adapter is a
class by construction — the function-shape check's mirror), and step 6
checking the `invoke()` signature against the closed dispatch-kwargs
contract (R-service-type-002/003).

{#function-shape-predicate-conformance-set}
### Function-shape predicate — conformance set

The vector-2 seal is `inspect.isfunction(x)`. The predicate's
conformance set — the shapes it MUST admit and reject — is fixed here:

:::{region} function-shape-predicate/conformance-set
| Shape | Verdict | Reason |
|---|---|---|
| `def` function | admitted | the canonical bare-function handler shape |
| `lambda` | admitted | a function under `inspect.isfunction`; admitted on the same basis as `def` |
| `@functools.wraps`-decorated function | admitted | `functools.wraps` copies metadata onto a wrapping `def`; the result is still a function |
| class | rejected | `__init__` / `__call__` on a class carry instance state — vector 2 |
| callable instance | rejected | `__call__` on an instance carries `self` state — vector 2 |
| bound method | rejected | carries the bound instance's state — vector 2 |
| builtin function | rejected | not introspectable for the bare-function signature check at step 6 |
| `functools.partial` result | rejected | pre-bound arguments enter the handler without passing through the declaration / `bindings` / hash surface |
:::

---

{#the-handlerentry-record}
## The HandlerEntry record

Step 7 populates `HandlerEntry`: the engine's post-resolution record for one
resolved handler, and the runtime-facing result of resolution — the runner
dispatches from it. A handler becomes known by being **resolved** from a pipeline
declaration's name (dotted-path primary, entry-points additive, per the sequence
above). Registration in Conjured registers **declarations**, not callables: an
integrator composing in-process registers each parsed handler *declaration* in a
[`DeclarationRegistry`](#in-process-compose-api/registry), and the engine resolves
the handler *callable* from that declaration's name by the sequence above — so there
is no `register_handler(...)` call or registration decorator for the function itself.
The record carries exactly the fields below:

| Field | Type | Carries |
|---|---|---|
| `qualified_name` | `str` | the resolved handler name — the dotted path (or the resolved dotted form of an entry-points short name) |
| `callable` | `Callable[..., dict[str, object] \| None]` | the resolved bare kwarg-only handler function itself — the function the step-5 check admitted, never an author-supplied factory, callable class, or `functools.partial`. Returns the handler's output dict, or `None` for a hook |
| `kind` | `Literal["transform", "service", "hook"]` | the handler kind — which bare-function kind this is |
| `package` | `str` | the distribution the handler resolved from — or, for a module outside any installed distribution, its top-level module name (per step 7); the source of its package attribution downstream |
| `toml_path` | `pathlib.Path` | the handler's declaration TOML on disk |

`HandlerEntry` is **immutable** once constructed — the runner reads it, never writes
it. `module` is deliberately not a field: it derives from `qualified_name` at emission
time, and a stored copy would be a second, drift-prone source for a derived fact.

All of its fields are **stable across edits to a handler's TOML body**: they record the
handler's resolved identity — name, kind, package, file, function — not its declared
schema. Editing a handler's `reads` / `output_schema` re-runs the full sequence (per
[hot-reload semantics](#hot-reload-semantics)) and regenerates the Pydantic models the
dispatch wrapper validates against, but changes no HandlerEntry field. The record is
post-resolution runtime identity, not a cached schema.

---

{#error-semantics}
## Error semantics

All resolution failures are compose-time `ContractViolation`. Each failure carries a
remediation hint whose **content class** is fixed by the failure; the exact wording is the
engine's, and the strings below are illustrative of each hint's content, not a verbatim
contract:

| Failure | Remediation hint (illustrative) |
|---|---|
| Module not found | "module `<name>` not importable; check that the providing package is installed and importable" |
| Function not in module | "module `<name>` does not export `<func>`; check spelling or that the function is defined at module top level" |
| Non-function shape | "`<name>.<func>` is not a bare function; handlers MUST be bare kwarg-only functions per R-handler-bare-function" |
| Signature mismatch | "`<name>.<func>` signature `(...)` does not match the TOML declaration `(...)`; missing/extra kwargs: `[...]`" |
| Entry-point collision | "entry-point `<name>` registered by multiple packages: `[...]`; resolve by uninstalling one or using explicit dotted-path references" |
| Namespace package | "module `<name>` is a namespace package (PEP 420); add an empty `__init__.py` to `<path>` to make it a regular package" |

A source-AST audit violation (step 3) raises `ContractViolation` under
R-handler-pure-module with the audit's own remediation hint; that
hint's shape is the handler-component audit's territory, not
enumerated here.

---

{#resolution-priority}
## Resolution priority

:::{region} resolution-priority/dot-presence-selector
Explicit dotted-path resolution wins over entry-points short-name
resolution. The rule is mechanical: if the handler name contains a dot,
the engine treats it as a dotted path and does no entry-points lookup;
a name with no dot is an entry-points short name. The presence or
absence of a dot fully determines which path runs — there is no
ambiguous case.
:::

Entry-points short names are therefore aliases, available to a
package's consumers but never shadowing an explicit dotted path.

The dot-presence selector routes **handler** names. Adapter **and validator** resolution run
their own [inverted selector](#adapter-selector/inverted-priority) — per § Resolution mechanism:
a service-type qualified name is a type identity, not a
module path, and a validator name is namespaced for the same reason (D8).

---

{#entry-points-collision}
## Entry-points collision

Two packages registering the same short name under `conjured.handlers`
is a collision. The engine fails loud at startup — it does not pick a
winner, order by install time, or otherwise disambiguate. Silent
disambiguation would let an unrelated package install change which
handler a pipeline resolves. The collision surfaces as the
entry-point-collision `ContractViolation` above; the integrator
resolves it by uninstalling one package or switching the pipeline
declaration to explicit dotted-path references.

---

{#namespace-packages-pep-420}
## Namespace packages (PEP 420)

Namespace packages are rejected. A handler or adapter module MUST live
in a regular package — one with an explicit `__init__.py`. The
rejection is detected at step 2: `find_spec` reports a namespace
package with `origin is None`, and the engine raises the
namespace-package `ContractViolation`. A namespace package has no
single source origin for the step-3 source-AST audit to read;
requiring `__init__.py` keeps every audited module backed by one
concrete file.

---

{#hot-reload-semantics}
## Hot-reload semantics

Each compose performs a fresh resolution — the sequence runs
again from the top, with no caching of a prior compose's resolved
callables. The mechanism is the **fresh-resolution eviction** at step 4:
when the module about to be imported already sits in `sys.modules` AND
that cached entry's origin IS the file step 3 just read and audited, the
engine evicts the entry and re-imports — the audited source IS the
source that executes, and a re-compose after an on-disk edit dispatches
the new module code.

The eviction's scope is exact. It covers **declaration-resolved modules
only** — the handler / validator / adapter / compiler modules a
declaration names; engine modules (`conjured.*` — a native adapter's
class path resolves there) and stdlib modules are never evicted. A
cached entry whose origin DIFFERS from the just-audited file is a
**detected audited-vs-executed divergence**: two files claim one module
name in this process (a shadowed package, a stale install beside local
source — the `sys.path`-mutation case), so the module that would execute
is not the file the audit and source hash just covered. The compose
REJECTS it with [ContractViolation](#contractviolation)
(`module-origin-divergence`), naming both files and the environment fix —
an ambiguous module resolution is an environment defect, never a
workflow, and executing unaudited code silently is the exact bypass the
audit step exists to foreclose.

The consumer-visible consequence: declaration-resolved module objects —
and the classes defined in them — are superseded per compose. A module
reference held from an earlier import, or a monkeypatched
module attribute, does not reach the next compose's run.
