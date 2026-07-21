---
kind: conformance
audience: [authors, integrators, agents]
slug: pipeline-conformance
component: pipeline
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#pipeline-conformance}
# Pipeline conformance checks

The mechanical conformance checks the engine fires for the
[pipeline](#pipeline) component. The checks
cover composition validation (R-pipeline-001) and channel-write
disjointness (R-pipeline-002). Each entry is structured for an
integrator or agent diagnosing a thrown error or auditing a pipeline
against the engine's contract.

The format for each entry:

- **Check name** — the mechanical check; lowercase noun phrase.
- **Rule anchor** — the derived rule the check enforces.
- **Trigger** — when the check fires (pipeline-declaration load, API
  invocation, LoRA artifact load).
- **Mechanism** — what the engine does to detect the violation.
- **Violation example** — a minimal TOML or prose snippet that fires the
  check.
- **Error class** — which of the
  [closed-enum classes](#error-class) the
  engine raises, or the structured-warning level for hash-consistency.
- **Diagnosis** — what to look for and how to fix.

The bulk of composition checks fire at pipeline-declaration load, where
the engine validates the full composition graph in a single pass before any
handler dispatches. A pipeline that loads is a pipeline whose graph is
internally consistent (I2). The hash-consistency checks fire at
deployment time when a trained artifact is mounted against the current
pipeline; they implement the graduated-force distinction between a
training-bundle-hash mismatch and a pipeline-hash mismatch without a
training-bundle-hash mismatch — the two-hash distinction owned by
[hash-model § bucketing semantics](#bucketing-semantics-pipeline-hash-vs-training-bundle-hash), and the
graduated force levels by
[hash-model § enforcement on (`integrity_enforcement = true`)](#enforcement-on-integrityenforcement-true).

---

{#pipeline-mechanically-enforced-checks}
## Mechanically-enforced checks

{#handler-name-resolution-failure}
### Handler-name resolution failure

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `handler-name-resolution` (the name-to-declaration resolution) plus the
  Phase-2 import-resolution seals `handler-module-import`,
  `handler-namespace-package`, `module-origin-divergence`, and
  `entry-point-collision`.
- **Trigger.** Pipeline-declaration load (compose time).
- **Mechanism.** For each `nodes` entry with `kind = "handler"`,
  the engine resolves the `name` field per
  [handler-resolution](#architecture-handler-resolution) —
  dotted-path module resolution (primary) or the `conjured.handlers`
  entry-points group (additive). Resolution fires the
  R-handler-pure-module source-AST audit (before import) and the
  R-handler-bare-function function-shape check (vector-2 seal) at the
  same boundary. An unresolvable name halts load immediately with a
  remediation hint per the
  [handler-resolution error table](#error-semantics). The failure carries
  the discriminator naming which stage missed: the initial
  name-has-no-registered-declaration miss is check
  `handler-name-resolution`; the import-resolution seals each name their
  own — a module that will not import or a function the module does not
  export is check `handler-module-import`, a PEP-420 namespace-package
  module (`find_spec` origin `None`) is check `handler-namespace-package`,
  a cached `sys.modules` entry loaded from a **different** file than the
  audited source is check `module-origin-divergence`, and two installed
  distributions registering one `conjured.handlers` short name is check
  `entry-point-collision` (the engine fails loud, never picks a winner).
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_library.handlers.npc_respond"   # package not installed
  bindings = { system_prompt = "You are an NPC." }
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** For a dotted-path name (the primary form), confirm the
  module is importable in the current environment and exports the named
  function — `python -c "import <module>; print(<module>.<func>)"`. For
  a short name resolved via entry-points, confirm the providing package
  is installed (`pip show <package>`) and its `pyproject.toml` declares
  the name under `[project.entry-points."conjured.handlers"]`; an
  editable install (`pip install -e .`) is sufficient for development.
  If resolution raises R-handler-pure-module or R-handler-bare-function
  ContractViolations rather than a not-found error, the module / function
  fails the source-AST audit or function-shape check — see
  [handler-resolution](#architecture-handler-resolution) for
  the full error-class table and remediation hints.

---

{#service-type-resolution-failure}
### Service-type resolution failure

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules).
- **Trigger.** Pipeline-declaration load (compose time).
- **Mechanism.** For each `service_bindings.<name>` block in the
  pipeline declaration, the engine resolves the `type` field through the
  adapter sibling path in
  [handler-resolution](#architecture-handler-resolution). A native
  service-type qualified name resolves through the engine's
  [native adapter table](#resolution-mechanism) **first** — the native
  consult precedes the entry-points leg — and only a name the table does
  not hold falls through to the adapter's own selector, which is the
  **inverse** of the handler priority:

Adapter resolution consults the entry-points group first, keyed by the **full
service-type qualified name** (dotted or not — an entry-point name may contain
dots), and falls back to dotted-path module resolution when no entry point carries
the name — the **inverse** of the dotted-path-primary, entry-points-additive
handler priority.

  Resolution fires the R-handler-pure-module audit at adapter scope
  (vector-7 seal):

Adapter modules MUST NOT contain class-level mutable state (class variables, `@lru_cache` on
methods) or module-level mutable state. Instance state (initialized in `__init__` or assigned on
`self` elsewhere) IS admissible — adapter instances are engine-managed compose-time state bounded
by composition lifetime.

  An unresolvable service-type
  qualified name halts load (check `service-type-resolution`). Resolution
  is strict qualified-name equality — no subtyping, no capability-matching,
  no wildcard resolution.
- **Violation example.**

  ```toml
  [service_bindings.llm]
  type  = "acme_llm.structured_output"   # package not installed
  model = "gpt-4o"
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Confirm the library providing the service type is
  installed. Confirm the `type` value exactly matches the service type's
  entry-point declaration — a trailing namespace segment mismatch
  (`acme_llm.structured_output` vs `acme_llm.StructuredOutput`)
  is not resolved.

---

{#composition-embed-cycle}
### Composition embed cycle

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules), node-name
  resolution (composition kind); check `composition-embed-cycle`.
  Termination is owned by
  [§ The nested `pipeline` composition kind](#nested-pipeline-kind).
- **Trigger.** Pipeline-declaration load (compose time), while resolving
  the embed graph.
- **Mechanism.** A `kind = "composition"` node embeds another
  composition declaration; because the nesting is fully declared, the
  engine resolves the whole embed graph at load. A **cycle** — a
  composition that transitively embeds itself — is the only
  non-terminating case under static nesting, and the engine rejects it as
  ContractViolation at compose, before any node dispatches (the same
  load-time rejection every compose-knowable fault takes). A cyclic
  composition never loads, so it can never run; a finite acyclic nesting
  always terminates and type-checks whole, so its depth is whatever the
  author declares (no depth ceiling, no runtime depth guard).
- **Violation example.**

  ```toml
  # my_pkg/outer.pipeline.toml embeds my_pkg.inner …
  [[nodes]]
  kind = "composition"
  name = "my_pkg.inner"

  # … and my_pkg/inner.pipeline.toml embeds my_pkg.outer — a cycle
  [[nodes]]
  kind = "composition"
  name = "my_pkg.outer"
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Break the cycle: a composition must not transitively
  embed itself. Iterate-to-convergence or runtime-depth traversal is not
  static nesting — it is consumer-territory multi-pipeline orchestration
  threaded across invocations ([§ Orchestration scope](#orchestration-scope)),
  not a `kind = "composition"` embed.

---

{#readwrite-shape-mismatch}
### Read/write shape mismatch

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); [I2
  (determinism under composition)](#principles); check
  `read-write-shape-mismatch`.
- **Trigger.** Pipeline-declaration load (compose time).
- **Mechanism.** After the single compose-time normalization step has
  resolved every node's read-map (input-port → channel) and write-map
  (output-port → channel) to its always-explicit form — every port,
  including identity-sugared ports the author left unmapped, now carries
  an explicit `(port, channel, declared-type)` triple — the engine
  type-checks per CHANNEL.

For each channel, collect every port (read or write, across all nodes) wired to it; every such
port MUST declare the **same declared type** — exact equality, no subtype widening, inheritance,
or capability-matching. A mismatch raises ContractViolation with a shape-diff report naming the
resolved channel, the ports wired to it, and the divergent types.
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.normalize"
  # normalize declares [output_schema]: normalized_input (str)
  # no writes_map → output-port `normalized_input` is identity-wired
  # to channel `normalized_input`

  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.respond"
  # respond declares [reads]: normalized_text (str)
  # no reads_map → input-port `normalized_text` is identity-wired to
  # channel `normalized_text`; no upstream write-port produces that
  # channel and it is not a declared [inputs] field
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Resolve each port to its channel: an input-port is
  wired to a channel by the node's `reads_map`, or — when the port is
  left unmapped — by identity-sugar to a same-named channel; an
  output-port is wired by the node's `writes_map` or, unmapped, by
  identity. Then, for the offending channel, compare the read-port's
  declared type against the producing write-port's. Type agreement is
  exact-equality; the engine performs no alias resolution between
  differently-named ports sharing a channel. Above, the writer's
  output-port `normalized_input` identity-wires to channel
  `normalized_input` while the reader's input-port `normalized_text`
  identity-wires to channel `normalized_text` — two distinct channels,
  so the reader's channel has no producer. Fix by aligning the wiring:
  give one node a map that routes both ports to a shared channel (e.g.
  `reads_map = { normalized_text = "normalized_input" }` on the reader),
  rename a port so the identity-channels coincide, or — if the reader's
  channel is meant to come from the API — declare it in `[inputs]`.

---

{#undeclared-or-doubly-mapped-port-in-a-node-wiring-map}
### Undeclared or doubly-mapped port in a node wiring map

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules).
- **Trigger.** Pipeline-declaration load (compose time), at the single
  normalization step.
- **Mechanism.** A node's `reads_map` keys range over exactly the
  handler's declared input-port names (its `reads`); its `writes_map`
  keys range over exactly the declared output-port names (its
  `output_schema`). The keys cannot introduce a port — the port set is
  closed at the handler declaration — and a port cannot appear twice. A
  `reads_map` / `writes_map` key naming a port the handler does not
  declare, or a port mapped more than once, raises ContractViolation
  (check `wiring-map-port`) at
  the normalization step naming the node, the map, and the offending
  key. The map value is a plain channel-name string (data); the engine
  admits no callable, expression, or external-declaration file-path
  value in a wiring map.
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.add"
  # add declares [reads]: left (int), right (int)
  reads_map = { left = "base_score", rihgt = "bonus_score" }
  # `rihgt` is not a declared input-port of my_lib.handlers.add
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Open the handler declaration and read its `reads`
  (input ports) and `output_schema` (output ports). Every `reads_map`
  key must be one of the `reads` port names and every `writes_map` key
  one of the `output_schema` port names — no extra key, no port mapped
  twice. Above, `rihgt` is a typo for the input-port `right`; correct
  the key. A map value is always a bare channel-name string: routing
  through a callable, expression, or external file path is not a wiring
  map and is rejected.

---

{#dangling-identity-port}
### Dangling identity port

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules).
- **Trigger.** Pipeline-declaration load (compose time), at the single
  normalization step.
- **Mechanism.** The normalization step desugars every unmapped port to
  a same-named channel — but this is not a silent default. For each
  input-port a node leaves out of its `reads_map`, the same-named
  channel it desugars to must be in scope: either some node earlier in
  the final dispatch order routes a write-port to it, OR it is a
  declared `[inputs]` field. An unmapped input-port whose same-named
  channel is neither written upstream nor declared in `[inputs]` is a
  dangling identity port and raises ContractViolation (check
  `dangling-identity-port`) at the
  normalization step, naming the node, the port, and the absent channel.
  (The symmetric output direction — an unmapped output-port desugaring
  to a channel no downstream node reads and not declared in `[outputs]`
  — is the
  [`inputs` / `outputs` dead declaration](#inputs-outputs-dead-declaration)
  check below.)
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.add"
  # add declares [reads]: left (int), right (int)
  reads_map = { left = "base_score" }
  # `right` is unmapped → identity-desugars to channel `right`,
  # but no upstream node writes `right` and it is not in [inputs]
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** An unmapped port resolves to a same-named channel only
  if that channel exists in scope. For the offending input-port, supply
  it explicitly via `reads_map = { <port> = "<channel>" }`, produce the
  expected channel by routing an upstream node's write-port to it, or —
  if the value comes from the API caller — declare the channel in the
  pipeline's `[inputs]` block. Above, port `right` has no channel
  binding and no channel named `right` is in scope; route it to an
  existing channel, write a `right` channel upstream, or add `right` to
  `[inputs]`.

---

{#read-port-channel-not-closed-by-an-upstream-write-or-inputs}
### Read-port channel not closed by an upstream write or `[inputs]`

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `read-port-unclosed`.
- **Trigger.** Pipeline-declaration load (compose time).
- **Mechanism.**

Every otherwise-unwritten read-port channel must be covered by an `inputs` declaration: an
unmapped read-port whose same-named channel is neither written by an upstream node's resolved
write-map nor declared in `inputs` is a dangling input port and raises ContractViolation at the
single compose-time normalization step — even when the pipeline declares no `inputs` block. This
closes the API-input set so a typo'd read-port fails loud rather than binding silently to a value
the caller never sends.
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.greet"
  # greet declares [reads]: enemy_health (int)
  reads_map = { enemy_health = "nemy_health" }
  # `nemy_health` is a typo: no node writes it and it is not in [inputs]
  # (no [inputs] block does NOT make it an implicit API input)
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Trace the read-port's resolved channel to a producer.
  If an upstream node should write it, confirm that node's `writes_map`
  (or identity-sugared output-port) routes to the same channel name. If
  the value enters from the API, add the channel to the pipeline's
  `[inputs]` block — every API-supplied channel a node reads must be
  declared there. Above, `nemy_health` is a misspelling of the intended
  `enemy_health` channel; fix the `reads_map` value, or declare
  `nemy_health` in `[inputs]` only if it is genuinely a distinct caller
  input. Omitting `[inputs]` never closes a read-port channel; the only
  closures are an upstream write or an explicit `[inputs]` declaration.

---

{#channel-write-overlap-without-merge-declaration}
### Channel-write overlap without `merge` declaration

- **Rule anchor.** [Derived rule R-pipeline-002 (channel-write
  disjointness with `merge` opt-in)](#pipeline-derived-rules); check
  `channel-write-overlap`.
- **Trigger.** Pipeline-declaration load (compose time).
- **Mechanism.** Over the normalized write-maps and the pipeline's
  `inputs` declaration, the engine derives each channel's contributors —
  its seed (if a declared input) plus its node writes, in graph order —
  and collects every channel with two or more. A multi-contributor
  channel is a deliberate fan-in — under per-port wiring a node writes a
  channel ONLY because its `writes_map` (or identity-sugar) explicitly
  routes an output-port there, and a seed exists only for a declared
  input, so the fan-in is always intentional, never an accidental name
  collision.

A channel's contributors are its seed (if the channel is a declared input) plus its node writes,
in graph order. A channel MAY have two or more contributors **iff** the pipeline declaration's
`merge` block declares a merge strategy for that channel from the engine's closed registry of
merge operations. Without an explicit `merge.<channel>` declaration, a channel with two or more
contributors is rejected at compose time with [ContractViolation](#contractviolation). The runner
folds the contributors into the channel's value inline, in graph order under the declared
strategy — the runner's own work.
- **Violation example.**

  ```toml
  # pipeline.toml — two write-maps route to `npc_state`, no [merge] declaration

  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.set_npc_mood"
  writes_map = { state = "npc_state" }       # routes output-port `state` → npc_state

  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.set_npc_inventory"
  writes_map = { state = "npc_state" }       # routes output-port `state` → npc_state

  # both write-maps deliberately target npc_state;
  # [merge.npc_state] absent → ContractViolation at pipeline-declaration load
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Either (a) eliminate the overlap by routing one
  write-map's output-port to a distinct channel, or (b) add a
  `merge.<channel>` entry declaring a strategy from the closed registry — see
  [pipeline reference § `merge.<channel>`](#mergechannel-channel-write-disjointness-opt-in)
  for the registry. The strategy's type constraint must match the merged
  channel's induced type. If no closed-registry strategy expresses
  the intended merge, use [the aggregator pattern](#the-aggregator-pattern) —
  an author-written transform, not a shipped handler.

---

{#single-assignment}
### Single-assignment — no node reads and writes the same channel

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules), single-assignment
  (read/write disjointness) clause; check `single-assignment`.
- **Trigger.** Pipeline-declaration load (compose time), at the single
  normalization step.
- **Mechanism.** No node wires a read-port and an output-port to the
  **same** channel: a node's resolved read-map (input-port → channel)
  and write-map (output-port → channel) MUST target disjoint channel
  sets, and an overlap raises ContractViolation at the normalization
  step naming the node and the shared channel. Because a handler is a
  channel-agnostic pure function — the runner builds its input from the
  read-map before the call and routes its return via the write-map after
  — the body cannot reference a channel, so read-then-rewrite is
  impossible by construction; the residual compose-time check is this
  set-disjointness test over the two normalized maps. It keeps each
  channel's value free of in-place mutation across the run (the kernel
  single-assignment property), so every merge strategy stays a pure
  reducer over independently-produced contributors.
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.accumulate"
  # accumulate declares [reads]: running (list) and
  # [output_schema]: running (list)
  reads_map  = { running = "tally" }
  writes_map = { running = "tally" }
  # both maps resolve to channel `tally` — the node would read then
  # rewrite it in place
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** A node that transforms a value writes a **new** channel
  rather than rewriting the one it read: route the output-port to a
  distinct channel (`writes_map = { running = "tally_next" }`). To
  combine independent contributors into one channel, declare a fan-in
  [`merge.<channel>`](#mergechannel-channel-write-disjointness-opt-in) —
  never a single node reading and rewriting. Above, the read-port and
  write-port both resolve to `tally`; give the write-port its own
  channel.

---

{#merge-strategy-type}
### Merge strategy type constraint mismatch

- **Rule anchor.** [Derived rule R-pipeline-002 (channel-write
  disjointness with `merge` opt-in)](#pipeline-derived-rules); check
  `merge-strategy-type`.
- **Trigger.** Pipeline-declaration load (compose time), during merge
  validation.
- **Mechanism.** Distinct from the missing-`merge` check above (which
  fires when a multi-contributor channel carries *no* declaration): once
  a `merge.<channel>` entry is present, the engine validates the named
  strategy against the
  [§ `merge.<channel>` grammar](#mergechannel-channel-write-disjointness-opt-in)'s
  *Compose-time engine validation* list — the strategy MUST be in the
  closed registry, MUST name a channel some port actually wires, and its
  **type constraint MUST match the merged channel's declared type, which
  MUST be non-optional** (the engine does not see through an
  `Optional[...]` wrapper). A strategy applied to an incompatible channel
  type, or an `Optional[<T>]` merged channel, raises ContractViolation.
- **Violation example.**

  ```toml
  # `tally` is a declared int channel; append_list requires a
  # list-typed channel
  [merge]
  tally = "append_list"
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Pick a strategy whose type constraint matches the merged
  channel's induced type — the
  [closed registry](#mergechannel-channel-write-disjointness-opt-in)
  states each strategy's constraint — and declare the channel as the
  **non-optional** base type the strategy reduces over (an
  `Optional[<T>]` merged channel is rejected; declare `<T>`). If no
  closed-registry strategy expresses the intended fan-in, use
  [the aggregator pattern](#the-aggregator-pattern).

---

{#binding-supply-incomplete}
### Binding supply incomplete

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `binding-supply-incomplete`.
- **Trigger.** Pipeline-declaration load (compose time).
- **Mechanism.**

For each `nodes` entry: **service-typed bindings** — the pipeline declaration must supply a
matching `service_bindings.<name>` block whose `type` equals the handler's declared type (strict
qualified-name equality) and whose body includes every field declared in the resolved service
type's `identity_schema`. **Compose-time bindings** — every handler `bindings.<name>` entry
that declares a value schema and no ship-time default must be supplied by the pipeline entry's
`bindings` value block. A binding that declares a ship-time default MAY be omitted (the engine
supplies the declared default) or overridden; a default-less binding left unsupplied raises
ContractViolation. A compile-directive binding (`compile = "..."`) is engine-owned: the engine
produces its value by running the named compiler at binding resolution, so it carries no node
supply — it is neither subject to the must-supply rule nor omittable, the node contributes nothing
to it. Orphaned supply entries — `service_bindings.<name>` blocks or `bindings` keys the pipeline
supplies but no node in the composition declares — also raise ContractViolation. A `bindings.<name>`
value supplied for a compile-directive binding likewise raises ContractViolation: the engine
produces that binding's value, so a node supply for it is meaningless and is rejected at compose
rather than silently absorbed.

- **Violation example (service-typed, missing identity field).**

  ```toml
  # Resolved service type declares [identity_schema]: model (str), prompt_template (str)
  [service_bindings.llm]
  type  = "acme_llm.structured_output"
  model = "gpt-4o"
  # prompt_template absent — identity_schema field not supplied
  ```

- **Violation example (compose-time binding, unsupplied value).**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.normalize"
  bindings = {}    # handler declares [bindings.system_prompt] (str) — value absent
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Open the handler declaration and read its
  `service_bindings` entries (for service-typed bindings) or
  `bindings.<name>` blocks (for compose-time bindings). For
  service-typed bindings, also open the resolved service type's
  declaration and read its `identity_schema`. Supply all declared
  identity fields in the pipeline's `service_bindings.<name>` block;
  supply each declared compose-time binding in the node entry's
  `bindings = { ... }` table — inline value or external binding-value
  declaration path.

---

{#binding-value-shape}
### Binding value shape

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `binding-value-shape`.
- **Trigger.** Assemble time (stage 4 of compose) — when each
  compose-resolved `bindings.<name>` value is validated against its
  declared binding schema, before any dispatch.
- **Mechanism.** A `bindings.<name>` value that was *supplied* (the
  `binding-supply-incomplete` check above already confirmed presence) is
  validated once at assemble against a model generated over the binding's
  declared fields — the same Pydantic validator the reads / output-schema
  boundaries use (handler reference § Binding value-supply grammar owns
  the "both go through the same Pydantic validator" contract). Two
  failures raise here: a resolved value that violates its declared
  field type or constraint, and a **bare scalar supplied for a
  multi-field binding** (a multi-field binding must be an object keyed by
  its declared field names; the bare-value route is the single-field
  affordance only). Because the value is **compose-fixed** — resolved and
  frozen once, identical across every dispatch — the failure is a
  compose-time [ContractViolation](#contractviolation), never the
  dispatch-only [SchemaValidationError](#schemavalidationerror) the
  per-request reads boundary raises.
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.generate"
  # generate declares [bindings.temperature]: float (constraint: 0.0–2.0)
  bindings = { temperature = "hot" }
  # "hot" is not a float → ContractViolation at assemble
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Align the supplied binding value with the handler's
  declared `bindings.<name>` field types and constraints. For a
  multi-field binding, supply an inline table (or external
  `{ file = "..." }`) keyed by the binding's field names — a bare scalar
  is admitted only for a single-field binding. The value is validated at
  assemble, so a green load already means every binding value conforms;
  this never surfaces mid-dispatch.

---

{#external-binding-content-unsupported}
### External binding-file content unsupported

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `external-binding-content-unsupported`.
- **Trigger.** Compose time — at the resolution pass that reads each
  `{ file = "<path>" }` external-file supply (a binding value or a
  file-supplied compile parameter), and as a backstop at the
  hasher / compiler.
- **Mechanism.** An external-file supply — the reserved
  `{ file = "<path>" }` binding-value form and the same form for a
  compile parameter — is read and canonicalized at compose so the
  [pipeline-hash](#pipeline-hash) folds the file's **content, not its
  path** ([hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs)).
  This check is the structural backstop guarding that fold: it fires when
  a referenced file is **unreadable / un-decodable** at the resolution
  pass (an `OSError`, an invalid-TOML parse error, or content that does
  not canonicalize to a JSON-native value), OR when an external-file
  declaration reaches the hasher / compiler **still unresolved** (the
  resolution pass was not run, or a supplying declaration whose on-disk
  directory the engine does not know could not anchor its relative path).
  It exists so the engine **never silently hashes a path or feeds a path
  to a compiler** — the same fail-loud posture the hasher's
  `bundle-reaches-byref-fold` backstop takes.
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.normalize"
  bindings = { config = { file = "configs/missing.toml" } }
  # configs/missing.toml does not exist (or is not valid TOML) →
  # ContractViolation at the compose resolution pass
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Confirm the `{ file = "<path>" }` target exists, is
  valid TOML (for a binding-value file) or the text the compiler expects
  (for a compile-parameter file), and canonicalizes to a JSON-native
  value. The path resolves against the directory of the **declaration
  TOML that supplied the value** (a pipeline binding against the pipeline
  TOML's directory; a preprocessor binding against the composition TOML's
  own directory; a compile parameter against the handler TOML's
  directory). If the diagnostic reports an *unresolved* external file at
  the hasher, the supplying declaration was registered without its on-disk
  location — register it with its `toml_path` so relative `{ file }`
  paths can anchor.

---

{#compile-signature}
### Compile directive signature mismatch

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `compile-signature`.
- **Trigger.** Compose time — the stage-4 binding-resolution pass, when a
  `bindings.<name>` entry declares the `compile = "<compiler>"` directive
  sub-form and the engine resolves the named compiler.
- **Mechanism.** A compile-directive binding's declared parameters (the
  sibling keys of `compile`) MUST bind the resolved compiler's signature,
  introspected from the compiler's real `__code__` (the same un-fakeable
  surface handler signature resolution reads): the compiler MUST be
  **kwarg-only** — no positional parameter, no `*args` / `**kwargs`
  collector; every declared parameter MUST be one of the compiler's
  keyword-only parameters; and every **required** keyword-only parameter
  of the compiler MUST be declared. A mismatch — an undeclared-by-the-
  compiler parameter, an undeclared-but-required compiler parameter, or a
  non-kwarg-only compiler — raises [ContractViolation](#contractviolation)
  at binding resolution, never deferred to dispatch.
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.validate_name"
  # the blessed `regex` compiler accepts params `pattern`, `flags`
  bindings = { name_check = { compile = "regex", patern = "^[A-Z]" } }
  # `patern` is a typo — not a parameter `regex` accepts → ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Open the compiler's contract and match the directive's
  parameter keys to its keyword-only parameters exactly — every declared
  key must be a parameter the compiler accepts, and every required
  compiler parameter must be declared. A blessed first-party compiler
  (`regex`, `jinja`, `json_schema`) carries a fixed parameter set; a
  third-party (namespaced) compiler's signature is read from its
  function. The check fires at compose, so a green load means every
  compile directive binds its compiler.

---

{#compile-artifact}
### Compile directive artifact failure

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `compile-artifact`.
- **Trigger.** Compose time — the stage-4 binding-resolution pass, when a
  resolved compiler runs once against its bound parameters to produce the
  binding's artifact.
- **Mechanism.** After the `compile-signature` check passes, the engine
  **runs** the resolved compiler once against its bound parameters to
  produce the binding value (a compiled `re.Pattern`, a Jinja `Template`,
  a `jsonschema` validator — the engine records the opaque artifact, never
  interprets it). If the compiler **raises** — a malformed `regex`
  pattern, an unparseable `jinja` template, an invalid `json_schema`, an
  unknown parameter value — that is the compiler's own failure producing
  the artifact and raises [ContractViolation](#contractviolation) at
  binding resolution. Distinct from a **blessed** compiler's missing
  optional backing library (`jinja2` / `jsonschema`), which surfaces as
  that library's own raw `ImportError` — an environment problem propagated
  unchanged, never a ContractViolation.
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.validate_name"
  bindings = { name_check = { compile = "regex", pattern = "^[A-Z" } }
  # "^[A-Z" is an unbalanced character class — `re.compile` raises →
  # ContractViolation (compile-artifact) at binding resolution
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Fix the compile-parameter value(s) the compiler
  rejected — the diagnostic names the compiler's own error (the malformed
  pattern, the template parse error). This is a compose-time failure, so
  the artifact is proven buildable before any dispatch; a compiler that
  needs an optional backing library reports the missing library as its own
  `ImportError` rather than through this check.

---

{#explicit-null-target}
### Explicit-null form on a non-nullable target

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `explicit-null-target`. The admission / positions / spelling law is
  owned by the handler reference's
  [explicit-null form](#binding-value-supply-grammar-explicit-null)
  (the reserved `{ null = true }` region), cited here rather than
  restated.
- **Trigger.** Compose time — at every engine-read TOML value position
  that feeds a declared field (a binding / identity / config / transport /
  hook-transport value, or a compile parameter).
- **Mechanism.** The reserved explicit-null value form `{ null = true }`
  is admitted **only where the target field is nullable-declared** (the
  `"<T> | None"` union or the `nullable` shorthand). Supplied where no
  nullable axis exists — an identity, config, or compile-parameter
  position (none admit a nullable declaration), a whole multi-field
  binding (never a nullable target), or a non-nullable binding /
  transport / hook-transport field — it is recognized and **rejected**
  with [ContractViolation](#contractviolation), never silently absorbed
  as data. (A *malformed* spelling — `{ null = false }`, a non-boolean
  value, or an extra key — is `malformed-declaration` at parse instead,
  the same split the `{ file }` sibling form uses.)
- **Violation example.**

  ```toml
  # Service type: transport_schema = { endpoint (str) }  — not nullable
  # deployment.toml
  [transport.llm]
  endpoint = { null = true }
  # endpoint is not nullable-declared → ContractViolation at compose
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Use `{ null = true }` only for a field the schema
  declares nullable (`endpoint: "str | None"` or the `nullable`
  shorthand). If the field genuinely may be absent-as-null, make its
  declaration nullable at the owning schema; otherwise supply a real
  value. Omission is never a null — a nullable field still must be
  *present* as `{ null = true }` to satisfy presence-coverage; this check
  is its mirror, rejecting the form where nullability was never declared.

---

{#identitytransport-field-misplacement}
### Identity/transport field misplacement

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules), Identity/transport
  placement sub-clause; check `identity-transport-placement`.
  Pipeline-hash construction is owned by
  [hash-model](#architecture-hash-model).
- **Trigger.** Pipeline-declaration load (compose time).
- **Mechanism.**

Every field in a pipeline-level `service_bindings.<name>` block beyond `type` and the reserved
`config` sub-block must be declared in the resolved service type's `identity_schema`. The
`config` block is the binding's generation-parameter supply — its keys resolve against the
service type's `[config_schema]`, not `identity_schema` (the service-type reference's § The
`[config_schema]` contract owns that check). Cross-block misplacement raises ContractViolation
naming the offending field and its correct location.

Every field in a deployment `transport.<name>` block must be declared in the resolved service
type's `transport_schema`. Cross-block misplacement raises ContractViolation naming the offending
field and its correct location.
- **Violation example.**

  ```toml
  # Service type: identity_schema = {model}, transport_schema = {endpoint}
  [service_bindings.llm]
  type     = "acme_llm.structured_output"
  model    = "gpt-4o"
  endpoint = "https://api.example.com"     # transport field in identity section
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Identity fields (which contribute to the
  [pipeline-hash](#pipeline-hash)) belong
  in the pipeline's `service_bindings.<name>` block. Transport
  fields (deployment-local, excluded from the pipeline-hash — see
  [hash-model](#architecture-hash-model)) belong in the
  deployment declaration's `transport.<name>` block. The service type's
  `identity_schema` and `transport_schema` are the authoritative
  declarations of which field belongs where. Moving a field between
  blocks shifts or restores the pipeline-hash.

---

{#service-binding-transport-coverage-gap}
### Service-binding transport coverage gap

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `transport-coverage-gap` (the per-binding coverage check) and check
  `transport-handle-coherence` (the type-coherence clause of the same
  region).
- **Trigger.** Pipeline-declaration load (compose time), during
  deployment-declaration validation.
- **Mechanism.** During deployment-declaration validation, the per-binding
  transport-coverage check applies:

Every service-typed binding the engine composes must have a corresponding `transport.<name>`
block in the deployment declaration — a missing block raises ContractViolation. That covers
the pipeline's own `service_bindings.<name>` blocks AND
every `[service_bindings.<name>]` a trainable composition declaration supplies (its terminal
`trainable.service_bindings` backend and any preprocessor service binding), joined on the
binding handle under the deployment reference's shared-by-binding-name resolution. The join
is type-coherent: every binding sharing one handle within a composing pipeline's scope MUST
resolve the same service-type — differing service-types under one shared handle raise
ContractViolation at compose (one covering block cannot satisfy two `transport_schema`s).
Coverage
follows what the engine composes, never the consumer: service use outside the composed
pipeline is consumer territory and needs no block. The covering block must carry **every
declared `transport_schema` field and no undeclared field**. Presence-coverage is **uniform**
over the declared field set: a nullable-declared field is satisfied by a supplied value OR by
the [explicit null](#binding-value-supply-grammar-explicit-null) `{ null = true }`; **absence
of any declared field raises ContractViolation** — nullable grants no presence exemption,
because a considered-and-null value is spelled while an absent field is indistinguishable from
forgot ([exhaustive declaration](#architecture-exhaustive-declaration)'s empty-but-present
principle, held at the field level). An unknown field raises ContractViolation. The check is
key-set plus reserved-form recognition only: the declared fields' VALUES pass through opaque
(the `**transport_extra` passthrough the deployment reference's `transport.<name>` contract
owns), the engine reading exactly two reserved value shapes — the explicit-null form, which
normalizes to null before delivery, and, on a `secret_ref`-declared field, the
secret-reference shape whose grammar/scheme/resolver check
[R-deployment-003](#R-deployment-003) owns (cited, not restated). Every other value passes
through unread.

  Two discriminators fire out of this one region. A service-typed binding
  with **no** covering `transport.<name>` block, or a block that omits a
  declared `transport_schema` field, is check `transport-coverage-gap`.
  The region's type-coherence clause — two service-typed bindings sharing
  one as-written handle within the composing pipeline's scope (the
  pipeline's own `service_bindings.<name>` and an embedded trainable
  composition's `[service_bindings.<name>]`) that resolve **different**
  service-types, so one covering block cannot satisfy two
  `transport_schema`s — is check `transport-handle-coherence`.
- **Violation example.**

  ```toml
  # pipeline.toml
  [service_bindings.llm]
  type  = "acme_llm.structured_output"
  model = "gpt-4o"

  # deployment.toml — [transport.llm] absent
  # ContractViolation at pipeline-declaration load
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** For every service-typed binding the engine composes — a
  pipeline `service_bindings.<name>` entry or a trainable composition's
  `[service_bindings.<name>]` — add a `transport.<name>` block to the deployment
  declaration supplying every one of the service type's `transport_schema`
  fields (a nullable-declared field as a value or as the explicit
  `{ null = true }`; omission is never a null).
  Transport values are per-environment — the production endpoint differs
  from staging — which is why they are excluded from the pipeline-hash
  and supplied at deployment rather than in the pipeline.

---

{#inputs-outputs-dead-declaration}
### `inputs` / `outputs` dead declaration

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `inputs-outputs-dead-declaration`.
- **Trigger.** Pipeline-declaration load (compose time).
- **Mechanism.**

`inputs` / `outputs` field names are *channel* names (the pipeline's API surface). For each field
declared in the pipeline's `inputs` block: at least one node's read-map must route a port to that
channel. For each field declared in `outputs`: at least one node's write-map must route a port to
that channel. A declared input field no node reads is a dead declaration; a declared output field
no node writes cannot surface. Both raise ContractViolation.
- **Violation example.**

  ```toml
  [inputs]
  player_input = { type = "str" }
  session_id   = { type = "str" }   # no handler reads session_id — dead

  [outputs]
  dialogue     = { type = "str" }
  debug_trace  = { type = "dict" }  # no handler writes debug_trace — dead
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Either (a) remove the dead declaration from `inputs`
  or `outputs`, or (b) add a node that reads the declared input field
  or writes the declared output field. A field left in the declarations
  after the node that used it was removed from the pipeline is a common
  trigger.

---

{#api-invocation-declared-inputs-enforcement}
### API-invocation declared-inputs enforcement

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `api-invocation-declared-inputs-enforcement`.
- **Trigger.** API invocation — pre-dispatch validation of the incoming
  request's initial channel state against the pipeline's `inputs`
  declaration. Fires before any handler dispatches; distinct trigger
  from the compose-time dead-declaration check above.
- **Mechanism.**

When a pipeline declares `inputs`, the API invocation path validates the incoming request's
key-set against the declared input fields before dispatching the first node — presence of
every declared field, never values.
Missing field: ContractViolation at the API boundary — no node dispatches; no `pipeline_error`
event fires because the pipeline never started. An incoming key that is not a declared input
field is **not admitted but not an error**: the runner seeds only the declared input channels,
so an extra never becomes a channel and never reaches any handler. The missing-field
ContractViolation's message names any unrecognized keys present in the request — so a typo'd
key surfaces in the same error as the declared field it failed to supply. A declared input
field supplied with a type- or constraint-violating value passes the API boundary and surfaces
as SchemaValidationError at the seeded channel's first consumer — its reads-projection or its
merge fold, whichever the runner dispatches first (per R-error-channel-001's key-set routing;
the `inputs` / `outputs` field-resolution clause guarantees at least one reading node exists,
so a consumer always exists).

  The boundary failure is surfaced on the wire as the status the server
  reference's [§ Wire error surface](#trigger-error-responses) assigns a
  caller-supplied `ContractViolation` (the `400`-class row). A
  pipeline with no
  `inputs` block declares **no** API inputs: under input closure every
  node's read-port channel must be produced by an upstream node, so the
  graph is self-contained at the boundary. An unwritten read-port channel
  that is neither produced upstream nor declared in `inputs` is a
  compose-time ContractViolation — never an accepted free input.
- **Violation example.**

  ```
  # Pipeline declares [inputs]: player_input (str), session_id (str)
  # Incoming request initial channel state:
  #   {"player_input": "Hello!", "session_idd": "s-1"}
  # session_id absent → ContractViolation at API boundary; HTTP 400;
  # no handlers dispatch; the message names the unrecognized key
  # "session_idd". An extra key alone is not an error — it is never
  # seeded, so it never becomes a channel.
  ```

- **Error class.** [ContractViolation](#contractviolation) at the API
  boundary, projected to the wire status the server reference's
  [§ Wire error surface](#trigger-error-responses) assigns it (the
  caller-supplied `400`-class row).
- **Diagnosis.** Include all fields declared in the pipeline's `inputs`
  block in the initial channel state of every API invocation. Fields
  declared in `inputs` are pre-conditions for any node to dispatch —
  the pipeline has no path to supply a missing field from within the
  composition. This check fires at dispatch time, not at compose time;
  the pipeline loads successfully even if no client has yet sent a
  correctly-formed request. An unrecognized key named in the error
  message is usually the missing field's typo'd spelling; the extra
  itself is not admitted and not an error (it is never seeded, so it
  cannot reach any handler).

---

{#hook-transport-coverage-gap}
### Hook transport coverage gap

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules).
- **Trigger.** Pipeline-declaration load (compose time), during
  deployment-declaration validation.
- **Mechanism.** During deployment-declaration validation, the per-hook transport-coverage check applies (check `hook-transport-coverage-gap`):

Every hook in the pipeline's `nodes` list must have a corresponding
`hook_transport."<as_written_node_name>"` block in the deployment declaration. The block is
strict-validated against the hook's `transport_schema`: every declared field must be present, no
unknown fields are accepted, declared types must match. Presence-coverage is uniform here
exactly as for service-binding transport: a nullable-declared hook transport field is satisfied
by a supplied value or by the
[explicit null](#binding-value-supply-grammar-explicit-null) `{ null = true }` (delivered to
the hook body as Python `None`); absence of any declared field raises ContractViolation. A hook
whose `transport_schema` declares
zero fields requires an empty-but-present block — absent raises ContractViolation; empty-but-present
does not.
- **Violation example.**

  ```toml
  # pipeline.toml — uses hook my_lib.hooks.log_dialogue
  [[nodes]]
  kind = "handler"
  name = "my_lib.hooks.log_dialogue"

  # deployment.toml — [hook_transport."my_lib.hooks.log_dialogue"] absent
  # ContractViolation at pipeline-declaration load
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Add a `hook_transport."<as_written_node_name>"` block
  to the deployment declaration for every hook in the pipeline. If the
  hook's `transport_schema` declares zero fields, add an empty block. The
  block header is quoted with the hook's **as-written pipeline node name**
  (deployment/reference.md § `hook_transport` owns the key-form — a short
  entry-points name carries no separator period). The strict validation exists
  because the runner wrapper swallows hook operational errors (per
  R-error-channel-003), making misconfigured hook transport a
  silent-failure category otherwise; compose-time strictness closes the
  category by construction.

---

{#streamable-terminal-node}
### Streamable terminal-node placement

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules), streamable
  terminal-node placement; check `streamable-terminal-node`.
- **Trigger.** Pipeline-declaration load (compose time).
- **Mechanism.**

A trainable composition whose `[trainable]` node declares `streamable = true` MUST be the
pipeline's terminal node — only hooks (which write no channels) may follow it. Any non-hook node
downstream of a streamable trainable raises ContractViolation. Terminal position is evaluated
**transitively through a terminal nested `pipeline` embed**: a streamable trainable that is
terminal inside a nested `pipeline` which is itself the enclosing pipeline's terminal node
satisfies the rule, and any non-hook node downstream of it — at any nesting layer — raises
ContractViolation.
- **Violation example.**

  ```toml
  [[nodes]]
  kind = "composition"
  name = "my_pkg.dialogue_trainable"   # its [trainable] declares streamable = true

  [[nodes]]
  kind = "handler"
  name = "my_lib.handlers.postprocess"  # a non-hook node downstream
  # of a streamable trainable — ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** A `streamable = true` trainable MUST be the pipeline's
  terminal node — only hooks (which write no channels) may follow it.
  Move any non-hook downstream work upstream of the streamable trainable,
  or drop `streamable = true` if a downstream transform is genuinely
  required. Terminal position is evaluated transitively through a
  terminal nested `pipeline` embed.

---

{#streamable-sink-target}
### Stream sink attached to a non-streamable runnable

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `streamable-sink-target`. The `stream_sink` parameter is owned by
  [§ Pipeline invocation](#pipeline-invocation).
- **Trigger.** Assemble/invocation time — when a `stream_sink` callback
  is attached to a runnable.
- **Mechanism.** The run-scoped `stream_sink` token-delivery callback
  fires only for a run whose pipeline has a streamable terminal (per the
  placement rule above). Attaching a sink to a runnable with **no**
  streamable terminal raises ContractViolation — a sink that would
  silently never fire is refused at the boundary rather than accepted as
  dead configuration. (A sink that raises at runtime is absorbed, surfaced
  on the operational logger, and detached — the run completes; the posture
  is owned at [§ Pipeline invocation](#pipeline-invocation). A sink is
  never a channel or a captured record — fragments reach only the sink.)
- **Violation example.**

  ```python
  # runnable assembled from a pipeline whose terminal node is a plain
  # transform, not a streamable trainable
  run(runnable, {"player_input": "hi"},
      stream_sink=lambda fragment: print(fragment))
  # ContractViolation — the pipeline produces no fragments, so the sink
  # could never fire
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Attach a `stream_sink` only to a runnable whose pipeline
  has a streamable terminal node (a terminal `streamable = true`
  trainable, transitively). If token streaming is wanted, declare
  `streamable = true` on the terminal trainable; otherwise omit the sink
  — a sink-less run behaves identically to a non-streamable one.

---

{#bundle-reaches-byref-fold}
### Bundle reaches the hasher by-reference fold (engine-drift backstop)

- **Rule anchor.** [Derived rule R-pipeline-001 (compose-time
  composition validation)](#pipeline-derived-rules); check
  `bundle-reaches-byref-fold`. Hash-domain ownership is
  [hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs).
- **Trigger.** Compose/assemble time — inside the training-bundle-hash
  by-reference fold, as the hasher walks the composition graph.
- **Mechanism.** This is **not an author-facing check** — no author
  declaration trips it. It is the hasher's own-hash-domain structural
  backstop, a sibling of the cycle and unresolved-file guards. The
  by-reference training-bundle-hash fold is an **own-hash-domain
  allowlist**: only a composition kind that carries its own hash domain (a
  trainable composition) may fold in by reference. A
  **pure-substitution bundle** has no own hash domain — it is textually
  substituted out at every walker's entry (`conjured.ir.substitute`),
  before scoping and hashing — so a bundle (or any future
  non-own-hash-domain kind) *reaching* the by-reference fold means a walk
  **forgot to substitute**: engine drift, never author error. The engine
  fails loud with [ContractViolation](#contractviolation) rather than
  silently folding it by reference (the same fail-loud posture the
  `external-binding-content-unsupported` backstop takes on an unresolved
  `{ file }`).
- **Violation example.** No author declaration produces this — it is an
  engine-internal invariant. The adversary is a code regression: a hasher
  or graph walker that stops calling `conjured.ir.substitute` on a
  `kind = "composition"` node whose `meta.kind` is the pure-substitution
  bundle kind, letting that node reach the training-bundle-hash
  by-reference fold. The backstop raises there, naming the node and the
  reached kind, instead of hashing a substitution-kind node by reference.
- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** An author seeing this has hit an engine bug — report it.
  The fix is in the engine: a bundle must be substituted out before any
  hashing walk reaches it; restore the missing `substitute` call at the
  walker entry.

---

{#training-bundle-hash-mismatch-at-deployment-load}
### Training-bundle-hash mismatch at deployment load

- **Rule anchor.** [Derived rule R-pipeline-003 (trained-artifact integrity
  enforcement)](#pipeline-derived-rules). Hash-consistency framing owned by
  [hash-model.md § Training-bundle-hash](#training-bundle-hash-construction).
  The trained-artifact manifest carrying the recorded
  `training_bundle_hashes` table is specified in
  [hash-model.md § Trained-artifact manifest](#trained-artifact-manifest-as-view).
- **Trigger.** Trained-artifact load at deployment time — when a
  trained artifact (LoRA, adapter, model variant) is registered against
  the current pipeline by the deployment's `[artifacts]` table.
- **Mechanism.** The engine computes the current pipeline's
  [training-bundle-hash](#training-bundle-hash) (check
  `training-bundle-hash-mismatch`)
  per embedded trainable composition declaration and compares each
  against the matching trainable-composition-qualified entry in the
  artifact manifest's `training_bundle_hashes` table (key shape
  `<trainable_composition_name>` per
  [hash-model.md § Manifest-key shape](#manifest-key-shape)).
  A mismatch on any trainable composition node means that trainable
  composition's declaration changed — its preprocessor handlers,
  `trainable.config`, `trainable.service_bindings`, `trainable.reads`,
  or `trainable.output_schema` — so the training-content semantics
  reaching the trained backend differ from what the artifact was
  trained on. The behavior is gated on the deployment's
  [integrity enforcement](#integrity-enforcement) opt-in: under
  `integrity_enforcement = true`, the mismatch (the HIGH-force class)
  **halts the deployment with
  [ContractViolation](#contractviolation)** — naming the trainable
  composition node and both hash values — unless an
  `acknowledged_drift` entry covers the artifact and the
  `training_bundle_hash` drift class at that trainable; under
  `integrity_enforcement = false`, the `training_bundle_hash_changed`
  canonical event fires and load proceeds. Acknowledgment is
  per-trainable-composition; no `"any"` sentinel exists. Hash-model's
  § Integrity-enforcement opt-in owns the graduated-force logic.
- **Violation example.**

  ```toml
  # loras/dialogue_lora.safetensors.conjured.toml — the artifact's sidecar manifest
  # (adjacent-file convention, § Trained-artifact manifest)
  [manifest]
  artifact = "loras/dialogue_lora.safetensors"
  pipeline_hash_set = ["sha256:..."]

  [training_bundle_hashes]
  "my_pkg.dialogue_trainable" = "sha256:abc123..."

  # Current pipeline training-bundle-hash for my_pkg.dialogue_trainable:
  #   "sha256:def456..."
  # Mismatch — under integrity_enforcement = true: ContractViolation
  # halt, pending an acknowledged_drift entry
  ```

- **Error class.** Under `integrity_enforcement = true`:
  [ContractViolation](#contractviolation) at deployment load, unless a
  matching `acknowledged_drift` entry covers the drift. Under
  `integrity_enforcement = false`: no exception — the
  `training_bundle_hash_changed` canonical event fires and load
  proceeds.
- **Diagnosis.** A training-bundle-hash mismatch means the trainable
  composition's declaration changed since the artifact was trained —
  its preprocessor handlers, configuration, service bindings, declared
  reads, or declared output schema. Options: (a) retrain against the
  current pipeline — the new training corpus will reflect the updated
  trainable composition's contract; (b) revert the trainable
  composition declaration changes that shifted the hash if the change
  was unintentional; (c) acknowledge the drift explicitly via
  `acknowledged_drift` in the deployment declaration, accepting that
  the artifact was trained on a different trainable composition
  contract. Acknowledging training-bundle-hash drift on one trainable
  composition node does not silence warnings on other trainable
  composition nodes in the pipeline — acknowledgment is
  per-trainable-composition and per-drift-class.

---

{#pipeline-hash-mismatch-with-training-bundle-hash-match}
### Pipeline-hash mismatch with training-bundle-hash match

- **Rule anchor.** [Derived rule R-pipeline-003 (trained-artifact integrity
  enforcement)](#pipeline-derived-rules) — the no-halt arm. Hash-consistency framing owned by
  [hash-model.md § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs)
  and
  [hash-model.md § Training-bundle-hash](#training-bundle-hash-construction).
  The two-hash distinction and the per-kind hash treatment of embedded
  compositions there determine when this audit fires versus the
  training-bundle-hash mismatch audit above.
- **Trigger.** Trained-artifact load at deployment time.
- **Mechanism.** The engine checks whether the current pipeline's
  [pipeline-hash](#pipeline-hash) appears
  in the artifact manifest's `pipeline_hash_set`. If every entry in
  the manifest's `training_bundle_hashes` table matches the current
  pipeline's per-trainable-composition training-bundle-hashes (no
  training-bundle-hash mismatch) but the current pipeline-hash is
  absent from `pipeline_hash_set`, the outer pipeline composition
  changed — a merge-strategy update, a preprocessor handler edited
  outside any trainable composition declaration, a postprocessor
  change, an outer-pipeline binding shift, or bundle-substituted
  content that flowed into the outer pipeline-hash — without
  invalidating any embedded trainable composition declaration's
  training-bundle-hash. The diagnostic delta at this check: the engine
  emits the `pipeline_hash_changed` canonical event and **load
  proceeds — no halt**, under either enforcement setting, and there is
  **no `pipeline_hash` `acknowledged_drift` class** (nothing halts to
  acknowledge). Its force level relative to a training-bundle-hash
  mismatch, and why, is the graduated-force logic owned by
  [hash-model § enforcement on (`integrity_enforcement = true`)](#enforcement-on-integrityenforcement-true).
- **Violation example.**

  ```toml
  # loras/dialogue_lora.safetensors.conjured.toml — the artifact's sidecar manifest
  # (adjacent-file convention, § Trained-artifact manifest)
  [manifest]
  artifact = "loras/dialogue_lora.safetensors"
  pipeline_hash_set = ["sha256:abc123...", "sha256:def456..."]

  [training_bundle_hashes]
  "my_pkg.dialogue_trainable" = "sha256:aaa111..."

  # Current pipeline-hash: "sha256:ghi789..." (absent from pipeline_hash_set)
  # Current training-bundle-hash for my_pkg.dialogue_trainable:
  #   "sha256:aaa111..." (matches)
  # → MEDIUM-force warning; outer pipeline changed without trainable-contract drift
  ```

- **Error class.** No runtime exception class — structured MEDIUM-force
  warning; **load proceeds, no halt** (logged for operator awareness,
  not gated).
- **Diagnosis.** A pipeline-hash-only mismatch (all
  training-bundle-hashes match) means the outer pipeline composition
  changed — a node was added, removed, or reordered; a merge-strategy
  declaration changed; a binding value outside any trainable
  composition declaration shifted — but every embedded trainable
  composition declaration's training-bundle-hash is unchanged. The
  `pipeline_hash_changed` event surfaces this for an operator's awareness: the
  artifact was trained against a different outer composition, but the
  trainable composition contracts the artifact depends on are intact.
  No action is required to proceed. Why this drift is warn-only while a
  training-bundle-hash mismatch can halt — the graduated-force logic and
  its retraining-implication rationale — is owned by
  [hash-model § enforcement on (`integrity_enforcement = true`)](#enforcement-on-integrityenforcement-true),
  cited here rather than restated.

---

{#missing-manifest-sidecar-under-integrity-enforcement}
### Missing manifest sidecar under integrity enforcement

- **Rule anchor.** [Derived rule R-pipeline-003 (trained-artifact integrity
  enforcement)](#pipeline-derived-rules). Integrity-enforcement framing owned by
  [hash-model.md § Integrity-enforcement opt-in](#integrity-enforcement-opt-in);
  [acknowledged drift](#acknowledged-drift).
- **Trigger.** Deployment time — when the deployment declaration
  declares `integrity_enforcement = true` in its `training_contract`
  block.
- **Mechanism.** When integrity enforcement is enabled, every trained
  artifact (LoRA, adapter, model variant) the deployment's `[artifacts]`
  table registers
  must carry its [sidecar manifest](#trained-artifact-manifest-sidecar) —
  the `<artifact-filename>.conjured.toml` adjacent-file convention that
  section owns. An absent manifest
  raises ContractViolation immediately (check
  `trained-artifact-manifest-missing`) — no hash comparison is
  possible, and an enforcement-on deployment cannot proceed without
  the manifest. A deployment with `integrity_enforcement = false` does
  not check for a sidecar; the check is gated on the explicit
  enforcement opt-in. (There is no no-`training_contract` state: the block is
  [required, body-required at deployment load](#training-contract-section-required-body-required)
  — a deployment without it never loads.)
- **Violation example.**

  ```toml
  # deployment.toml
  [training_contract]
  integrity_enforcement = true

  # LoRA artifact: my_model/adapter.safetensors
  # my_model/adapter.safetensors.conjured.toml — absent → ContractViolation at deployment
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Three paths: (a) write the sidecar post-training with the
  `conjured artifact-tag` helper (the consumer-written generation path
  [§ Trained-artifact manifest](#trained-artifact-manifest-sidecar) owns);
  (b) if the
  artifact was trained outside that path, generate the
  manifest from the pipeline's hash-model output and place it alongside
  the artifact; (c) set `integrity_enforcement = false` in
  `training_contract` to disable the enforcement gate. Options (a) and
  (b) are the conformant paths; (c) disables hash-consistency checks
  entirely for the deployment.

---

{#malformed-trained-artifact-manifest}
### Malformed trained-artifact manifest sidecar

- **Rule anchor.** [Derived rule R-pipeline-003 (trained-artifact integrity
  enforcement)](#pipeline-derived-rules) — the corrupt-artifact arm.
- **Trigger.** Deployment load, whenever a registered artifact's sidecar is read
  (both enforcement modes — the read itself is not enforcement-gated, because the
  always-available drift events need the recorded values).
- **Mechanism.** A sidecar that exists but is unreadable, is not valid UTF-8 TOML,
  omits a required [manifest field](#trained-artifact-manifest-sidecar), or carries a
  mistyped / out-of-enum field is **malformed** (check
  `trained-artifact-manifest-malformed`) — a structured ContractViolation naming the
  offending field, **never coerced to absent** and never a raw parse exception (the
  same fail-loud posture the audit-stamp artifact takes; absent is the distinct,
  enforcement-gated state above).
- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Regenerate the sidecar with `conjured artifact-tag --force`, or fix
  the named field; a hand-edited manifest must keep the full closed field set.

---

{#artifact-registration-unknown-trainable}
### Artifact registration names an unknown trainable

- **Rule anchor.** [Derived rule R-pipeline-003 (trained-artifact integrity
  enforcement)](#pipeline-derived-rules) — the dead-registration arm; the
  `[artifacts]` grammar is the deployment reference's § `artifacts`.
- **Trigger.** Deployment load, when the deployment's `[artifacts]` table is
  reconciled against the deployed pipeline's trainable composition nodes.
- **Mechanism.** A registered key (a trainable composition's declared name — the
  trained-artifact-manifest key shape) that matches no trainable composition node in
  the deployed pipeline raises ContractViolation (check
  `artifact-trainable-unknown`): a registration that can never be compared is a
  wiring mistake — a renamed composition, a typo, a stale entry — not a no-op, and
  silently skipping it would let the integrity opt-in vouch for an artifact it never
  checked.
- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Align the `[artifacts]` key with the deployed trainable
  composition's `meta.name` (rename or remove the stale entry).

---

{#pipeline-review-enforced-checks}
## Review-enforced checks

The pipeline component owns no review-enforced rules distinct from those
the handler component already governs. The review-enforced rules that
apply within a pipeline's composed handlers are handler-body rules the
runner cannot inspect mechanically regardless of pipeline composition;
they are **owned and enumerated** by the handler conformance checks and
the error-channel conformance checks, which carry each rule's
enforcement-mode metadata. Cross-reference those files for review audit
guidance on the handler bodies that compose the pipeline (do not re-list
the set here — it drifts when a rule's enforcement mode changes).

The pipeline component's review surface is intentionally thin: the
engine handles composition validation mechanically at pipeline-declaration
load, leaving no gap that a pipeline-level review rule could close
without duplicating handler-level coverage. Content-translation boundary
discipline and pipeline-composition ordering preferences are authoring
guidance, not engine rules — they carry no `rule_id` and belong in the
authoring guide rather than in conformance checks.

---

{#pipeline-cross-references}
## Cross-references

- [pipeline reference](#pipeline-reference) — R-pipeline-001 (compose-time
  composition validation) and R-pipeline-002 (channel-write
  disjointness with `merge` opt-in) derived rules.
- [hash-model](#architecture-hash-model) — pipeline-hash and
  training-bundle-hash composition (owners of the two-hash spec);
  integrity-enforcement opt-in (gates the deployment-time hash audits).
- [principles](#principles) — invariants I1, I2, I4.
- **handler conformance checks** (`components/handler/conformance.md`) —
  the review-enforced handler-body rules (owned + enumerated there).
- **error-channel conformance checks** (`components/error-channel/conformance.md`) —
  R-error-channel-002 (no engine retry API); R-error-channel-003 (halt
  semantics, hook wrapper sanction).
- [glossary](#glossary) — pipeline-hash,
  training-bundle-hash, acknowledged drift, integrity enforcement,
  training contract, trainable channel, ContractViolation,
  SchemaValidationError, PipelineFailure.
