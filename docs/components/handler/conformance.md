---
kind: conformance
audience: [authors, integrators, agents]
slug: handler-conformance
component: handler
---

{#handler-conformance}
# Handler conformance checks

The mechanical conformance checks the engine fires for the [handler](#handler) component, plus diagnostic framing for the [review-enforced](#review-enforced) rules whose violations live in handler bodies the runner cannot see.

Each entry below is structured for diagnosing a thrown error or auditing a handler against the engine's contract. The format:

- **Check name** — the mechanical check; lowercase noun phrase.
- **Rule anchor** — the derived rule the check enforces, cited by file + prose anchor.
- **Trigger** — when the check fires (handler-declaration load, compose time, dispatch time).
- **Mechanism** — what the engine does to detect the violation.
- **Violation example** — a concrete handler declaration or Python snippet that fires the check.
- **Error class** — which of the [closed-enum classes](#error-class) the engine raises.
- **Diagnosis** — what to look for and how to fix.

---

{#handler-mechanically-enforced-checks}
## Mechanically-enforced checks

{#top-level-kind-header-is-exactly-one-of-the-closed-enum}
### Top-level kind header is exactly one of the closed enum

- **Rule anchor.** [Derived rule R-handler-003 (closed-enum handler kinds)](#handler-derived-rules); [derived rule R-handler-006 (closed handler-declaration shape grammar)](#handler-derived-rules).
- **Trigger.** Handler-declaration load (engine startup).
- **Mechanism.** The handler-declaration loader scans the file's top-level section headers; exactly one of `transform`, `service`, `hook` MUST appear. Zero or more than one halts startup (check `handler-kind-header`).
- **Violation example.**

  ```toml
  # Two top-level kind headers — ContractViolation
  [transform]

  [service]

  [reads]
  player_input = { type = "str" }
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Confirm exactly one `transform` / `service` / `hook` header at file top level. If a handler legitimately needs both transform-like (declared writes) and service-like (external call) behavior, it is a service — services declare `output_schema` and make external calls. The trainable composition kind does NOT use a handler-declaration top-level header:

  :::{transclude} R-handler-003/discriminator
  :::

  so this audit's `transform` / `service` / `hook` enumeration is the complete handler-declaration set.

{#handler-module-purity-no-module-level-mutable-state-no-import-time-io}
### Handler-module purity (no namespace-scope mutable state, no import-time I/O)

- **Rule anchor.** [Derived rule R-handler-pure-module (handler module purity; vector-3 seal + vector-5 import-I/O seal)](#handler-derived-rules); [trust-model vectors 3 + 5](#architecture-trust-model). Complementary to the review-enforced [R-handler-007](#handler-derived-rules) handler-body import discipline below.
- **Trigger.** Compose time, at handler resolution (before module import per [handler-resolution.md § Resolution sequence](#resolution-sequence-compose-time)) — the AST walk runs on module source, not on the imported module.
- **Mechanism.** The engine reads the handler module's source from the spec's origin and runs an AST walk against the source BEFORE `importlib.import_module` executes the module's top-level code. The walk rejects:

  :::{transclude} R-handler-pure-module/forbidden-patterns
  :::

  Violations raise ContractViolation (check `handler-pure-module`) BEFORE the module loads — a post-import audit cannot prevent import-time I/O. Runtime defense-in-depth: module-dict snapshot+restore around each dispatch catches per-dispatch mutations.
- **Violation example.**

  ```python
  # Handler module — three violations
  import requests                                          # OK (library import)
  client = requests.Session()                              # vector 5 — client instantiation at import
  _CACHE: dict = {}                                        # vector 3 — module-level mutable state

  from functools import lru_cache

  @lru_cache(maxsize=1024)                                 # vector 3 — persistent caching decorator
  def _expensive(x): ...

  def my_handler(*, player_input):
      ...
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Namespace-scope mutable state (module level, a class body, a mutable-literal default argument), caching decorators, and import-time I/O all leak compose-time author state across dispatches outside the declared `bindings.<name>` surface. The three sanctioned architectural homes: compose-time configuration goes in `bindings.<name>` (the engine delivers a fresh per-dispatch copy of the resolved value); stateful artifacts with lifecycle (DB connections, model weights, SDK clients with auth context) go in a service-type adapter (instance-state cache permitted per the adapter-module purity scope extension below); cheap per-dispatch compute goes in the handler body. Caching at module scope is forbidden categorically — the engine reconstructs the dispatch wrapper per compose, so the cache's correctness boundary doesn't match the engine's lifecycle.

{#function-shape-check-at-handler-resolution}
### Function-shape check at handler resolution

- **Rule anchor.** [Derived rule R-handler-bare-function (function-shape check; vector-2 seal)](#handler-derived-rules); [trust-model vector 2](#architecture-trust-model).
- **Trigger.** Compose time, at handler resolution (per [handler-resolution.md § Resolution sequence](#resolution-sequence-compose-time)) — after the module-source AST walk above and after import, before the bare-function signature introspection below.
- **Mechanism.** After the engine resolves a handler name to a callable (dotted-path import or entry-points `.load()`), it runs a function-shape check:

  :::{transclude} R-handler-bare-function/predicate-admit-reject
  :::

  Resolution to any rejected shape raises ContractViolation (check `handler-function-shape`) before the signature check and the Pydantic-model wrapping run. The check applies to transform / service / hook handlers (bare kwarg-only functions); the trainable composition kind has no author callable to check per [R-handler-010](#handler-derived-rules).
- **Violation example.**

  ```python
  # Handler module exposes a callable class instead of a bare function
  class MyHandler:
      def __call__(self, *, player_input):
          return {"out": player_input.upper()}

  my_handler = MyHandler()    # resolution finds a callable instance — ContractViolation
  ```

  ```python
  # Handler exposes a pre-bound functools.partial — pre-bound args bypass
  # the bindings.<name> / hash surface — ContractViolation
  from functools import partial

  def _inner(*, player_input, system_prompt):
      return {"out": ...}

  my_handler = partial(_inner, system_prompt="You are an NPC.")
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** The handler must be a bare kwarg-only Python function — `def handler(*, ...): ...` (or a `@functools.wraps`-decorated function) at module top level. Replace callable classes with bare functions; replace `functools.partial` results with bare functions whose binding values are supplied via `bindings.<name>` declarations (the engine supplies them as a fresh per-dispatch copy per [R-handler-001](#handler-derived-rules)). The vector-2 seal closes "instance state on a callable class as a hidden stash for compose-time author state" per [trust-model](#architecture-trust-model); the binding home (`bindings.<name>`) is the sanctioned compose-time state surface.

{#adapter-module-purity-no-above-instance-scope-mutable-state}
### Adapter-module purity (no above-instance-scope mutable state)

- **Rule anchor.** [Derived rule R-handler-pure-module (handler module purity)](#handler-derived-rules) — adapter-scope extension; [derived rule R-service-type-003 (service-impl dispatch contract)](#service-type-derived-rules) — the adapter class-shape requirement; [trust-model vector 7](#architecture-trust-model).
- **Trigger.** Compose time, at service-type adapter resolution (sibling path to handler resolution per [handler-resolution.md § Resolution mechanism](#resolution-mechanism)).
- **Mechanism.** The engine runs the same AST walk used by handler-module purity (above) against the adapter module's source, with broader scope:

  :::{transclude} R-handler-pure-module/adapter-scope
  :::

  The distinction from handler modules: handler modules forbid the class shape entirely (bare kwarg-only functions); adapter modules REQUIRE the class shape (the adapter pattern), but constrain mutable state to instance scope. Violations raise ContractViolation at adapter resolution (check `adapter-pure-module` — the discriminator the vector-7 AST audit fires under; the same discriminator also fires when the resolved adapter object is not a class, since an adapter is a class by construction).
- **Violation example.**

  ```python
  # Adapter module — two violations
  from functools import lru_cache

  _RESPONSES: dict = {}                                    # vector 7 — module-level mutable state

  class MyAdapter:
      _CONNECTION_POOL: dict = {}                          # vector 7 — class-level mutable state

      @lru_cache(maxsize=1024)                             # vector 7 — class-method caching
      def lookup(self, key): ...

      def __init__(self, **config):
          self.client = SDKClient(**config)                # OK — instance state
          self.cache: dict = {}                            # OK — instance state cache

      def invoke(self, **kwargs):
          ...
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Move mutable state from class-level / module-level into instance scope: declare caches and state in `__init__` (`self.cache = {}`); attach cache methods on instances rather than via `@lru_cache` at class scope. The vector-7 seal closes "above-instance-scope state in adapter modules as a hidden cross-dispatch leak" per [trust-model](#architecture-trust-model); adapter instances are engine-managed compose-time state with composition-scoped lifetime, and their instance attributes are the sanctioned home for stateful artifacts (DB connections, loaded model weights, SDK clients with auth context).

{#handler-signature-matches-declared-reads-bindingsname-and-servicebindings-shape}
### Handler signature matches declared `reads`, `bindings.<name>`, and `service_bindings` shape

- **Rule anchor.** [Derived rule R-handler-001 (engine-constructed dispatch wrapper)](#handler-derived-rules).
- **Trigger.** Compose time — pipeline-declaration load triggers engine-constructed dispatch-wrapper construction per kind (see [handler-resolution](#architecture-handler-resolution); signature check at the post-resolution introspection step).
- **Mechanism.** At compose-time dispatch-wrapper construction (bare-function), the engine introspects the bare kwarg-only handler function's real `__code__` — the same un-fakeable surface the sibling resolution checks read; a planted `__signature__` can neither widen nor narrow it — and verifies:

  :::{transclude} R-handler-001/signature-union
  :::

  The check runs once per handler at compose (check `handler-signature-mismatch`); the trainable composition kind has no author signature to introspect per [R-handler-010](#handler-derived-rules).
- **Violation example.**

  ```python
  # Handler declaration declares reads: player_input, dialogue_history
  # Handler signature is missing dialogue_history — ContractViolation
  def my_handler(*, player_input):
      return {"out": ...}
  ```

  ```python
  # Handler declaration declares empty service_bindings but signature carries services
  def my_transform(*, player_input, services):       # transforms forbid services kwarg
      return {"out": ...}
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Compare the handler's Python signature parameter list against the union (`reads` input-port names) ∪ `bindings.<name>` ∪ {`services`-iff-service-typed-binding-declared}. The signature check fires **once at compose time** — no per-dispatch re-check — so the diagnostic appears the first time a pipeline using the handler loads. Non-bare-function shapes (`functools.partial`, callable instances, classes, bound methods) are rejected at handler resolution before the signature check per [R-handler-bare-function](#handler-derived-rules) (vector-2 seal in [trust-model](#architecture-trust-model)); canonical authoring uses plain `def handler(*, ...): ...`.

{#handler-return-dict-carries-only-declared-outputschema-keys}
### Handler return dict carries only declared `output_schema` keys

- **Rule anchor.** [Derived rule R-handler-001 (engine-constructed dispatch wrapper)](#handler-derived-rules); [derived rule R-error-channel-001 (closed-enum error classes)](#error-channel-derived-rules).
- **Trigger.** Dispatch (per invocation), at the engine-constructed dispatch wrapper's output-validation step.
- **Mechanism.**

  :::{transclude} R-handler-001/output-validation
  :::
- **Violation example.**

  ```python
  # Handler declaration declares output_schema: dialogue (str)
  # Handler returns an undeclared key — ContractViolation
  def my_service(*, npc_state, services):
      return {"dialogue": "...", "scratch_debug_info": {...}}    # scratch_debug_info undeclared
  ```

- **Error class.** [ContractViolation](#contractviolation) for a top-level key-set fault against the declared output-port set — an undeclared key in the return dict is the `undeclared-output-key` check; a declared output port omitted from the return dict is the `missing-declared-write` check; a non-hook return that is not a dict keyed by output-port name at all (the key-set cannot even be read) is the `return-shape` check — the [R-handler-001](#handler-derived-rules) sole-admission-gate seal that the return dict IS the sole write surface. [SchemaValidationError](#schemavalidationerror) for a value that fails its declared shape *within* a declared port (wrong type, regex/validator failure, out-of-set enum value, a required field absent inside a nested object) — the routing the Mechanism's transcluded kernel states.
- **Diagnosis.** Compare the handler's return dict keys against `output_schema`'s declared output-port set. Output-port validation followed by write-map routing is the **only** path for admitting values onto channels — undeclared keys cannot reach state, and the handler cannot name a channel directly. To add an output port, declare it in `output_schema` (which shifts the [pipeline-hash](#pipeline-hash) and, if the handler is a preprocessor inside a trainable composition declaration, the [training-bundle-hash](#training-bundle-hash) for that trainable composition node per [hash-model.md § Training-bundle-hash](#training-bundle-hash-construction)). To debug without polluting state, log via Python's `logging` module rather than the return dict.

{#hook-returns-none}
### Hook returns `None`

- **Rule anchor.** [Derived rule R-handler-001 (engine-constructed dispatch wrapper)](#handler-derived-rules); [Hook in handler-kinds](#the-hook-kind).
- **Trigger.** Dispatch (per hook invocation), at the engine-constructed dispatch wrapper's return-time check.
- **Mechanism.** The engine-constructed dispatch wrapper for the hook invokes the bare kwarg-only handler function and asserts the return value is `None` before returning (check `hook-return-not-none`). Hooks have no `output_schema` and no merge path; a hook returning a non-`None` value is a contract claim the runner cannot honor.
- **Violation example.**

  ```python
  # Hook returning a dict — ContractViolation
  def my_hook(*, dialogue, pipeline_run_id):
      return {"emitted_at": "..."}          # hooks return None by contract
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Confirm the handler kind. If the handler legitimately writes state, it is a transform or a service, not a hook — change the top-level kind header and add an `output_schema` per the kind's discipline. If the return value was a debug artifact, route it through `logging` instead of the return.

{#transform-forbids-the-services-kwarg}
### Transform forbids the `services` kwarg

- **Rule anchor.** Mechanical half of [derived rule R-handler-004 (transform purity)](#handler-derived-rules); [derived rule R-handler-001 (engine-constructed dispatch wrapper)](#handler-derived-rules).
- **Trigger.** Compose time, at the engine's signature-check step during transform dispatch-wrapper construction (per [handler-resolution](#architecture-handler-resolution)).
- **Mechanism.** A transform declares no `service_bindings`, so the engine's compose-time signature check admits no `services` kwarg in its parameter union:

  :::{transclude} R-handler-001/signature-union
  :::

  The runner has no path to supply `services` to a transform's dispatch wrapper — the kind's discipline forbids `service_bindings` per [R-handler-006](#handler-derived-rules) and the `services` kwarg per the comparison table in [handler-kinds](#architecture-handler-kinds).
- **Violation example.**

  ```toml
  # Transform declaration declaring a service_bindings block (forbidden by kind discipline)
  [transform]

  [reads]
  player_input = { type = "str" }

  [output_schema]
  out = { type = "str" }

  [service_bindings]
  llm = { type = "acme_llm.structured_output" }     # service_bindings on a transform — ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** A transform that needs an external call is misclassified — the right kind is a service. Change the top-level header from `transform` to `service`, retain the `service_bindings` entry, add the `services` kwarg to the handler signature, and replace the body's pure computation with `services.<name>.invoke(...)`. The kind change is structurally a new identity per [the closed-enum kinds rule R-handler-003](#handler-derived-rules); update qualified-name references accordingly.

{#trainable-composition-kind-has-no-author-body}
### Trainable composition kind has no author body

- **Rule anchor.** [Derived rule R-handler-010 (trainable composition has no author body)](#handler-derived-rules).
- **Trigger.** Compose time, at trainable-composition-kind compose-time construction.
- **Mechanism.**

  :::{transclude} R-handler-010/no-author-body
  :::
- **Violation example.**

  ```toml
  # composition declaration attempting to register a Python handler under trainable composition kind
  [meta]
  kind = "trainable"
  name = "my_pkg.dialogue_trainable"

  [trainable]
  name = "my_pkg.handlers.dialogue"    # trainable composition kind admits no author callable — ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** The trainable composition kind's dispatch wrapper is engine-constructed against the bound trainable backend adapter; there is no author body to register. To customize behavior around a trainable composition node, the architectural homes are: (a) `trainable.config` for compose-time backend configuration (sampling parameters, decoding strategy — NEVER prompt content per [R-handler-011](#handler-derived-rules)); (b) preprocessor handlers declared inside the trainable composition declaration as `[[preprocessors]]` entries (these shape the inputs reaching the trainable's `trainable.reads` [input ports](#input-port)); (c) postprocessor handlers declared in the outer pipeline downstream of the trainable composition node (these consume the channels the trainable's `trainable.output_schema` [output ports](#output-port) are routed onto by the node's [write-map](#write-map)). The trainable composition's own dispatch wrapper is engine territory; no author code interposes between the channel boundary and the adapter boundary.

{#service-kind-and-trainable-composition-kind-declare-exactly-one-service-typed-binding}
### Service kind and trainable composition kind declare exactly one service-typed binding

- **Rule anchor.** [Derived rule R-handler-008 (exactly one service-typed binding (service handler and trainable composition node))](#handler-derived-rules); [Service in handler-kinds](#the-service-kind).
- **Trigger.** Compose time, at the engine's compose-time construction gate for service-kind and trainable-composition-kind dispatches (alongside R-handler-001's signature check for service kind; the trainable composition kind has no author signature to check per [R-handler-010](#handler-derived-rules)).
- **Mechanism.** The engine rejects compose-time construction when the handler-kind or composition-kind service-typed binding declaration does not match the required cardinality (exactly one).

  - **Service kind (handler-kind).**

    :::{transclude} R-handler-008/service-binding-cardinality
    :::

  - **Trainable composition kind (composition-kind).**

    :::{transclude} R-handler-008/trainable-binding-cardinality
    :::

  Both misclassifications raise ContractViolation at the compose-time construction gate (check `service-binding-cardinality` — the one discriminator carrying both the service/trainable exactly-one arm here and the hook at-most-one arm below).
- **Violation example (zero entries).**

  ```toml
  [service]

  [reads]
  in = { type = "str" }

  [output_schema]
  out = { type = "str" }

  [service_bindings]
  # empty — service with no entry has no external call to make
  ```

- **Violation example (multiple entries).**

  ```toml
  [service]

  [reads]
  in = { type = "str" }

  [output_schema]
  out = { type = "str" }

  [service_bindings]
  llm = { type = "acme_llm.structured_output" }
  embedder = { type = "acme_embeddings.dense" }     # second entry — ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** **Zero entries (service kind):** either (a) declare exactly one `service_bindings` entry whose `type` resolves to a registered service-type, OR (b) the handler is a transform and should change kinds. A handler doing pure computation that never calls an external service is a transform (and may carry `bindings.<name>` entries for parameterization). **Zero entries (trainable composition kind):** `trainable.service_bindings` must name a trainable backend; without it the engine has no backend to construct the dispatch against. **Multiple entries (either kind):** split into separate nodes composed sequentially in the pipeline. A "dialogue step" needing both embedding lookup and LLM dispatch decomposes into (1) an embedding service handler writing a `retrieved_context` channel and (2) a trainable composition node (or service handler) reading `retrieved_context` and writing `dialogue` + `emotion` channels. Each emits its kind's captured event (service-kind: `service_invocation` paired with `handler_exit`; trainable composition kind: `handler_enter` paired with `handler_exit` — no `service_invocation`); the training corpus comprises the trainable composition nodes' captured records, keyed by composition-node identity.

{#hook-servicebindings-contains-at-most-one-entry}
### Hook `service_bindings` contains at most one entry

- **Rule anchor.** [Derived rule R-handler-009 (hook binding cardinality)](#handler-derived-rules).
- **Trigger.** Compose time, at the engine's compose-time construction gate for hook dispatches.
- **Mechanism.**

  :::{transclude} R-handler-009
  :::

  This is the **hook at-most-one arm of the same `service-binding-cardinality` check** whose service/trainable exactly-one arm the [entry above](#service-kind-and-trainable-composition-kind-declare-exactly-one-service-typed-binding) carries — one discriminator, two per-kind cardinalities (service/trainable = exactly one; hook = at most one).
- **Violation example.**

  ```toml
  [hook]

  [reads]
  dialogue = { type = "str" }

  [service_bindings]
  webhook_a = { type = "acme_webhook.poster" }
  webhook_b = { type = "acme_webhook.poster" }    # second entry — ContractViolation

  [transport_schema]
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** A hook emitting to multiple backends must be split into separate hook handlers — one per emission target. Each hook observes the same upstream channel reads and routes emission through its own distinct service-typed binding.

{#trainable-composition-kind-trainableoutputschema-is-the-literal-equal-backend-constraint}
### Trainable composition kind `trainable.output_schema` is the literal-equal backend constraint

- **Rule anchor.** [Derived rule R-handler-005 (literal-equal rule)](#handler-derived-rules).
- **Trigger.** Engine implementation (per service-type adapter); validation surface fires at dispatch.
- **Mechanism.** For each LLM-emission [output port](#output-port) of a [trainable](#trainable) composition node — declared in `trainable.output_schema`, its values routed onto a [trainable channel](#trainable-channel) by the node's [write-map](#write-map):

  :::{transclude} R-handler-005/literal-equal-kernel
  :::

  An adapter-returned value the schema rejects raises [SchemaValidationError](#schemavalidationerror) at the engine's output boundary — the single verdict layer; the adapter itself never validates the emission (pure translation) — typically because the adapter's submitted constraint diverged from the runtime output-port schema, or the server did not actually honor it. The validated response appears in the trainable composition node's `handler_exit` event (carrying `writes_snapshot`, the output-port projection taken upstream of the write-map), not in a `service_invocation` event — trainable composition node dispatches emit no `service_invocation`.
- **Violation example.** No author-side declaration triggers this; the violation lives in adapter implementation. A canonical detection signal: a trainable composition node whose `trainable.output_schema` declares an `emotion: Literal['warm', 'wary']` field begins receiving SchemaValidationError on dispatch with an out-of-set value (`"neutral"`) — the adapter's submitted constraint is not the runtime schema.
- **Error class.** [SchemaValidationError](#schemavalidationerror).
- **Diagnosis.** Inspect the service-type adapter (the code in the library that provides the bound trainable backend). The adapter MUST construct its backend-call from the runtime `trainable.output_schema` directly, not from a parallel schema authored alongside. If the adapter is engine-shipped (first-party), file an issue; if third-party, the library is non-conformant. Workaround until fixed: tighten or relax the runtime `trainable.output_schema` to match the adapter's actual constraint — but this is masking, not fixing.

{#closed-handler-declaration-grammar-no-unknown-blocks}
### Closed handler-declaration grammar — no unknown blocks

- **Rule anchor.** [Derived rule R-handler-006 (closed handler-declaration shape grammar)](#handler-derived-rules).
- **Trigger.** Handler-declaration load (engine startup).
- **Mechanism.** The closed per-kind block set is the one declared in [Handler-TOML grammar](#handler-toml-grammar) (the artifact-format anchor); the handler-declaration loader enforces it (check `closed-grammar` — the diagnostic translation of the IR's `extra="forbid"`):

  :::{transclude} R-handler-006/reject-unknown-blocks
  :::
- **Violation example.**

  ```toml
  [transform]

  [reads]
  in = { type = "str" }

  [output_schema]
  out = { type = "str" }

  [retry_policy]                          # unknown block — ContractViolation
  max_retries = 3
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Reach for the right declared block: compose-time values go in `bindings.<name>` (compile-affordance directives also live here); service-typed bindings go in `service_bindings`; deployment-local config on hooks goes in `transport_schema`; free-form prose goes in `annotations`. Engine retry surface does not exist (per [derived rule R-error-channel-002](#error-channel-derived-rules)); transport retry is impl-internal to service implementations; semantic retry is forbidden by [derived rule R-handler-002 (no silent fallbacks)](#handler-derived-rules). Trainable composition declarations carry a symmetric closed-shape grammar check per [R-handler-006](#handler-derived-rules) (its closed member set is enumerated at the owning rule, in the handler reference).

{#the-declaration-grammar-check-family}
### The declaration-grammar check family — one grammar mechanism across every declaration

The closed handler-declaration grammar above (check `closed-grammar`) is the **handler exemplar of a check family the engine fires identically across every declaration grammar** — handler, pipeline, service-type, and deployment. Documented once here (the handler arm); the sibling conformance catalogs do not restate the mechanism. The **error-index** (`reference/error-index.md`, the complete check roster) registers each family member's full cross-declaration rule set: the same `closed-grammar` discriminator enforces `R-handler-004` / `R-handler-006` / `R-handler-010` here, `R-pipeline-001` / `R-pipeline-002` on pipeline declarations, `R-service-type-001` on service-type declarations, and `R-deployment-001` / `R-deployment-002` on deployment declarations, and its siblings below carry the analogous cross-declaration reach. The four remaining family members:

- **`section-presence`** — a **required, empty-allowed** section header is absent from the declaration text. The presence discipline is text-level, not IR-level: a defaulted empty body cannot distinguish *present-but-empty* from *absent*, so the loader checks the header's literal presence per [exhaustive-declaration § The section-discipline modes](#the-section-discipline-modes) (reach: `R-handler-006`, `R-service-type-001`, `R-deployment-001`). *Adversary:* a transform carrying `[transform]` and `[output_schema]` but **no `[reads]` header at all** — `reads` is required-empty-allowed, so its textual absence raises ContractViolation at handler-declaration load, distinct from a present-but-empty `[reads]` (which is conformant).
- **`body-required`** — a **required, body-required** section is present but declares fewer than one field (reach: `R-handler-006`, `R-pipeline-001`, `R-service-type-001`, `R-deployment-001`). *Adversary:* a transform whose `[output_schema]` header appears with **zero fields under it** — `output_schema` is body-required for transforms/services (the [comparison-table `Declared writes` = required](#comparison)), so an empty body raises ContractViolation ("declared nothing" is not a meaningful state), the same shape the service-type `identity_schema` / `transport_schema` and trainable `trainable.output_schema` arms take.
- **`channel-type-token`** — a declared channel-field type token is outside the engine's Pydantic IR token grammar ([§ Types allowed in `reads` and `output_schema`](#types-allowed-in-reads-and-outputschema) owns the vocabulary; reach: `R-handler-006`, `R-pipeline-001`, `R-service-type-001`). *Adversary:* `out = { type = "complex" }` on an `[output_schema]` field — `complex` is no IR token, so it raises ContractViolation at load (the same rejection a `bytes` token takes, which no TOML primitive expresses).
- **`malformed-declaration`** — the **residual** family member: a declaration structurally malformed in a way no more-specific member names, the diagnostic translation of a raw Pydantic `ValidationError` into a ContractViolation (reach: every declaration grammar — `R-handler-006` / `R-handler-010` / `R-handler-011`, `R-pipeline-001` / `R-pipeline-002`, `R-service-type-001`, `R-deployment-001` / `R-deployment-002`). It is what keeps the fuzz-harness guarantee — *every declaration input either compiles or raises ContractViolation, never another exception*. *Adversary:* a top-level `reads = "player_input"` (a bare string where the grammar requires a `[reads]` table) — the shape is wrong in a way the per-section checks do not name, so the loader translates the Pydantic reject into a `malformed-declaration` ContractViolation rather than letting the raw error escape.

{#composition-metakind-is-one-of-the-closed-composition-kind-enum}
### Composition `meta.kind` is one of the closed composition-kind enum

- **Rule anchor.** [Derived rule R-handler-006 (closed handler-declaration shape grammar)](#handler-derived-rules); the composition-kind roster the [Handler-TOML grammar](#handler-toml-grammar) owns:

  :::{transclude} handler-toml-grammar/composition-kind-roster
  :::
- **Trigger.** Handler-declaration load (engine startup), for a composition declaration.
- **Mechanism.** A composition declaration's `meta.kind` value MUST be one of the closed composition-kind enum; a value outside it raises ContractViolation (check `unknown-composition-kind`). This is the composition-declaration counterpart of the bare-function `handler-kind-header` check, and is **distinct from `closed-grammar`** (which fires on an unknown *block*, not an unknown *kind value*).
- **Violation example.**

  ```toml
  [meta]
  kind = "ensemble"        # not one of the closed composition-kind values — ContractViolation
  name = "my_pkg.dialogue"
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Use a realized composition kind (the closed-enum members the roster above names); a novel kind is an engine change (a new enum member plus its per-kind fold and hash treatment), never an author-declared value.

{#names-are-unique-within-their-namespace}
### Names are unique within their namespace

- **Rule anchor.** [Derived rule R-handler-006 (closed handler-declaration shape grammar)](#handler-derived-rules) — the `[[preprocessors]]` id-label grammar the [mirror-pipeline principle](#composition-mirrors-the-pipeline) makes load-bearing; the pipeline-node arm is `R-pipeline-001`'s (owned in the pipeline conformance catalog — the error-index registers this check's full rule set, `R-handler-006` + `R-pipeline-001`).
- **Trigger.** Declaration load / compose time — the transport-collision arm fires at handler-declaration load (its inputs are declaration-local); the preprocessor and pipeline-node arms at composition / pipeline load.
- **Mechanism.** A name the engine requires unique within a namespace is duplicated (check `name-uniqueness`). The **handler arm** (`R-handler-006`): two `[[preprocessors]]` entries inside one trainable composition declaration share a local `id` label — their post-flatten qualified names `<meta.name>.<id>` would collide, so the id-label address the composition grammar makes load-bearing (a hook preprocessor's deployment-transport key; the local label channel scoping resolves against) is no longer an address. The **pipeline-node arm** (`R-pipeline-001`): two composition nodes resolve to the same `meta.name` within one pipeline — colliding in the trained-artifact-manifest key and in `<meta.name>.<channel>` channel scoping (hash-model owns the manifest-key uniqueness requirement). The **transport-collision arm** (`R-handler-006`): a hook's declared `transport_schema` field name duplicates another member of its own R-handler-001 signature union — a declared input-port name, a `bindings.<name>` name, or the reserved `services` kwarg — so one kwarg name would carry two sources at dispatch (the handler reference's § `transport_schema` owns the MUST-NOT). The engine refuses every collision rather than silently last-wins.
- **Violation example.**

  ```toml
  # inside one trainable composition declaration
  [[preprocessors]]
  kind = "handler"
  name = "acme_dialog.normalize_markers"
  id = "normalize"

  [[preprocessors]]
  kind = "handler"
  name = "acme_dialog.strip_whitespace"
  id = "normalize"          # duplicate id in one composition — <meta.name>.normalize collides — ContractViolation
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Give each preprocessor entry a distinct `id` (and each composition node a distinct `meta.name`); the qualified name is a load-bearing address, so a duplicate is a collision the engine refuses at compose, never a silent overwrite.

{#audit-stamp-freshness-under-audit-enforcement}
### Audit-stamp freshness under `audit_enforcement`

- **Rule anchor.** [Derived rule R-handler-pure-module (handler module purity)](#handler-derived-rules) — the dated-audit complement of the mechanical AST walk (the review-enforced conduct the walk cannot check); the [audit-stamp mechanism](#audit-stamps):

  :::{transclude} audit-stamps/kernel
  :::
- **Trigger.** Compose time, at module resolution (handler / adapter / validator) — **only when the deployment opts into `audit_enforcement`**. Without the opt-in the stamp is never read and carries no compose-time consequence (a tool-facing artifact only).
- **Mechanism.** The engine hashes the module source bytes it already read for the pre-import AST walk and compares them to the sibling `<module>.audit.toml` stamp. Any **not-fresh** state — **absent** (no stamp), **stale** (recorded `source_hash` ≠ current source), or **failed** (hashes match but `verdict` is not a pass-grade) — refuses compose with a ContractViolation (check `audit-stamp-not-fresh`). A stamp file that exists but is **corrupt** — unreadable, not valid TOML, missing a closed field, or carrying a mistyped / out-of-enum field — is a **distinct** ContractViolation (check `audit-stamp-malformed`), never coerced to `absent`: a corrupt engine-read artifact fails loud. The closed field set and the 4-state model are the [audit-stamp mechanism](#audit-stamps)'s.
- **Violation example.**

  ```toml
  # my_handlers.audit.toml beside my_handlers.py, under a deployment declaring audit_enforcement:
  source_hash       = "0000000000000000000000000000000000000000000000000000000000000000"  # ≠ current my_handlers.py source
  audit_prompt_hash = "abcd..."
  verdict           = "pass"
  date              = "2026-07-10"
  findings          = "findings/my_handlers.md"
  # → STALE (recorded source_hash ≠ current) — audit-stamp-not-fresh
  ```

  In the same file, a `verdict = "reviewed"` (outside the closed `pass` / `pass-with-notes` / `fail` enum) is instead `audit-stamp-malformed`; deleting the file entirely is the **absent** arm of `audit-stamp-not-fresh`.
- **Error class.** [ContractViolation](#contractviolation) — `audit-stamp-not-fresh` (absent / stale / failed) or `audit-stamp-malformed` (corrupt artifact).
- **Diagnosis.** Run the engine-shipped conformance audit prompt (shipped at `conjured.conformance`) against the in-scope module and record the result as its sibling `.audit.toml`; any edit to a stamped module stales its stamp structurally (re-audit to re-stamp). A `failed` verdict means the audit found a real violation — address the recorded findings, never re-stamp over them. Regenerate a malformed stamp from the shipped prompt rather than hand-patching it.

{#a-streamable-trainable-nodes-backend-supports-token-streaming}
### A `streamable` trainable node's backend supports token streaming

- **Rule anchor.** [Derived rule R-handler-008 (exactly one service-typed binding — trainable expansion)](#handler-derived-rules); [§ Trainable backends — the I4 seal and the compose-time gate](#trainable-backends) (the streaming-capability half of the trainable-backend gate).
- **Trigger.** Compose time, at trainable-composition-node construction, when the terminal `trainable` node carries the `streamable` delivery selector.
- **Mechanism.** A `streamable = true` trainable composition node promises token-level delivery. The engine verifies the bound trainable backend's adapter exposes an `invoke_streaming` generator; an adapter that exposes none cannot honor the promise, and a silent buffered fallback would be exactly the graceful-degrade the engine forbids — so compose refuses with a ContractViolation (check `streamable-backend-support`). This is the **backend-capability** half, distinct from the `streamable-terminal-node` placement check (`R-pipeline-001`, which governs *where* a streamable node may sit) and the `streamable-sink-target` run-boundary check (`R-pipeline-001`, the `run(..., stream_sink=…)` half).
- **Violation example.**

  ```toml
  # inside a trainable composition declaration
  [trainable]
  streamable = true
  # trainable.service_bindings binds an adapter exposing invoke(...) but no invoke_streaming(...) generator
  ```

  Bound to a non-streaming adapter, this raises at compose: the declaration promises token delivery the binding cannot produce.
- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** Bind a trainable backend whose adapter implements the `invoke_streaming` generator (the native streaming-capable backends do), or drop the `streamable` selector and consume the emission as a single buffered result. The engine never degrades a `streamable` promise to buffered delivery silently.

---

{#validator-resolution-and-binding}
### Validator resolution and parameter binding

- **Rule anchor.** [Derived rule R-handler-012 (validator registration and binding contract)](#handler-derived-rules).
- **Trigger.** Compose time, at field-validator resolution during model generation (wrapper construction).
- **Mechanism.** Every field key is a validation keyword in **one grammar**. A **bare** key is a standard constraint, checked at compose — keyword applicability to the field's declared type (the standard's own mapping; an inapplicable keyword raises, the named fail-loud deviation from JSON Schema's silent ignore) and keyword-value well-formedness (a non-numeric or non-finite bound, a non-compiling `pattern`, an empty `enum` reject) — and is realized in the field's generated Pydantic model; an unrecognized bare key raises ContractViolation naming the closed validation vocabulary. A **namespaced (dotted)** key resolves via the sibling mechanism against `conjured.validators` (source-AST audit + function-shape check unchanged; the key's value IS the parameter table); the engine verifies the kwarg-only signature equals `{value}` ∪ the key's declared parameter names, binds the parameters (engine-owned partial application), and wraps the bound validator into the field's generated Pydantic model. Validator names MUST be namespaced — a bare registered name fails loud at first resolution — so the bare and dotted key-spaces are disjoint and no shadowing case exists. Keywords enforce in **authored key order** across both classes. An unrecognized name, a resolution failure, a signature mismatch, a non-data parameter value, an inapplicable keyword, or a malformed keyword value raises ContractViolation before any dispatch. Two of these faults are this entry's owned discriminators: a resolved validator whose kwarg-only signature is not `{value}` ∪ its declared parameter names (extra / missing / positional / collector parameter) is the `validator-signature-mismatch` check; the binding-contract faults — a parameter named `value` (the reserved kwarg), a non-data parameter value, a built-in keyword declared on a type outside its JSON-Schema applicability family, or a malformed built-in keyword value (a non-numeric/non-finite bound, a non-compiling `pattern`, an empty `enum`) — are the `validator-parameter-binding` check.
- **Violation example.**

  ```toml
  [output_schema]
  release_date = { type = "str", "mypkg.in_range" = { minimum = 1900 } }   # the validator's params are min/max — signature mismatch
  ```

- **Error class.** [ContractViolation](#contractviolation).
- **Diagnosis.** The key's parameter table names must equal the validator function's kwargs beyond the reserved `value`. A parameterless validator carries an empty table (`{}`); parameters are data only — a callable, expression, or file reference in a parameter is rejected. The grammar and contract are owned at the handler reference's § Validators.

---

{#review-enforced-rules-diagnostic-framing}
## Review-enforced rules — diagnostic framing

The review-enforced rules below carry [`enforcement: review`](#review-enforced-mode) in their metadata. The runner cannot inspect handler bodies for these patterns; adversarial review at library publishing catches handler-body instances. The framing below is for an integrator or agent investigating *whether* a given handler body conforms, not for the engine's mechanical detection (there is none).

{#no-silent-fallbacks-r-handler-002referencemdderived-rules}
### No silent fallbacks ([R-handler-002](#handler-derived-rules))

- **Trigger** — review pass over each handler module's source.
- **Mechanism** — pattern-match against the [silent fallback](#silent-fallback) catalog:

  :::{transclude} R-handler-002/fallback-pattern-catalog
  :::

  **Per-kind scope** (per R-handler-002): the review applies to handler kinds that carry an author body — transform, service, hook. The trainable composition kind has no author body per [R-handler-010](#handler-derived-rules); the silent-fallback failure class is structurally impossible for trainable composition node dispatches. **Mechanical evidentiary backing (service kind):** the service-type adapter's `service_invocation` event captures the backend response before the handler body runs, and the handler's `handler_exit` event captures the response the body actually returned — the masking signature (a captured backend response indicating failure or absence paired with a schema-valid return; mere reshaping of a successful response is not the signal) is the wire-visible signal a consumer-side analyzer can use to flag candidate silent-fallback instances. The engine itself raises nothing (review enforcement, not engine enforcement).
- **Diagnosis** — the test from [the silent-fallback glossary entry](#silent-fallback): "is this value the outcome of the handler's actual work, or a sentinel emitted to mask internal failure?" A derived value that happens to match a sentinel (a validator returning `passes: true` because checks ran and all passed) is fine; the same return value as a swallowed-exception default is a violation. Production-resilience patterns are training-data contraindications in Conjured because the captured record claims the handler produced X for input Y when it actually failed.

{#transform-purity-r-handler-004referencemdderived-rules}
### Transform purity ([R-handler-004](#handler-derived-rules))

- **Trigger** — review pass over each transform handler's body.
- **Mechanism** — pattern-match against the forbidden-pattern set:

  :::{transclude} R-handler-004/forbidden-patterns
  :::

  The mechanical half — "transforms forbid service-typed bindings" — is mechanically enforced (above); the review half is the body's purity.
- **Diagnosis** — apply the [replay-determinism test](#handler-derived-rules) stated in the Mechanism above. If the answer is no, the handler should be a service — services carry the external-call profile honestly, their `service_invocation` events captured as provenance at the adapter boundary (for training capture, the right structural shape is a [trainable](#trainable) composition node). Refactoring is structural: change the kind, add the `service_bindings` entry, route the external reach through `services.<name>.invoke(...)`.

{#handler-import-discipline-r-handler-007referencemdderived-rules}
### Handler import discipline ([R-handler-007](#handler-derived-rules))

- **Trigger** — first-party CI AST walk; third-party publishing convention.
- **Mechanism** — AST-walk each handler module's import closure (transitive within the library's namespace) against the namespace lists:

  :::{transclude} R-handler-007/import-namespace-lists
  :::
- **Diagnosis** — every flagged import has a sanctioned replacement: backend SDKs route through `services.<name>.invoke(...)` against a `service_bindings` entry; global state lives in service-internal infrastructure that the bound service-type's adapter encapsulates; cross-library coordination is consumer-side multi-pipeline orchestration. The laundering pattern (handler imports a "utility" module that imports the backend SDK) is flagged at the handler's import closure; refactoring routes the backend reach through a service implementation.

{#no-semantic-retry-inside-handler-bodies-r-handler-002}
### No semantic retry inside handler bodies ([R-handler-002](#handler-derived-rules))

- **Trigger** — review pass over each service handler's body. (The engine-side half —
  no retry API exists — is [R-error-channel-002](#R-error-channel-002)'s
  absence-of-API mechanical enforcement, whose owner routes this handler-body
  prohibition here, to R-handler-002's review.)
- **Mechanism** — pattern-match for: explicit retry loops around `services.<name>.invoke(...)`; "validate-and-retry" patterns where the handler re-calls after judging the reply; "critique-and-revise" patterns; **any re-call triggered by a verdict on the reply — empty, malformed, or refused — even when it resends identical bytes**. Transport-level retry inside the service implementation (triggered by a transport fault before a usable response exists — connection reset, 5xx, timeout) is sanctioned; semantic retry inside the handler body (triggered by a verdict on the response) corrupts the [training contract](#training-contract) by burying multiple distinct external interactions under one captured invocation. The deciding axis is the trigger, not whether the payload changed — the error-channel reference's [no-engine-retry predicate](#no-engine-retry/payload-predicate) owns it.
- **Diagnosis** — semantic retry is consumer-territory multi-pipeline orchestration, not handler-body logic. Re-invocation — whether with modified inputs or a verdict-driven resend of identical bytes — lives at the consumer layer above the engine; handler bodies make exactly one external call per invocation per [the comparison table](#comparison). The engine raises [PipelineFailure](#pipelinefailure) on transport failure; consumers dispatch on the error and decide whether to re-invoke at their layer.

{#prompt-shaping-content-via-trainablereads-r-handler-011referencemdderived-rules}
### Prompt-shaping content via `trainable.reads` ([R-handler-011](#handler-derived-rules))

- **Trigger** — review pass over each trainable composition declaration and its preprocessor handlers.
- **Mechanism** — pattern-match for prompt-shaping content in the wrong surface:

  :::{transclude} R-handler-011/config-vs-reads-split
  :::

  Prompt-shaping content MUST instead be produced by an upstream preprocessor handler declared inside the trainable composition declaration as a `[[preprocessors]]` sequence entry. **Mechanical evidentiary backing.** Prompt content arriving via `trainable.reads` appears in the trainable composition node's `handler_enter.reads_snapshot` event payload (the per-dispatch training-input record). Prompt content slipped into `trainable.config` is partial-applied into the dispatch wrapper at compose-time and is absent from `reads_snapshot` — a captured `reads_snapshot` that lacks the prompt content the trainable composition node evidently used is a wire-visible structural signal a consumer-side analyzer can use to flag candidate R-handler-011 violations.
- **Diagnosis** — separate the two surfaces by purpose. Backend-side dials (sampling, decoding, generation parameters) → `trainable.config`. Prompt content (system prompts, instruction templates, in-context exemplars, content-injection strings, anything shaping what the model SEES) → produce in a preprocessor handler declared inside the trainable composition declaration as a `[[preprocessors]]` sequence entry. The preprocessor is a regular channel-agnostic handler: it writes an [output port](#output-port) that its entry [write-map](#write-map) routes onto one of the composition's internal scoped channels, and the trainable declares a matching [input port](#input-port) in `trainable.reads` that the trainable node's [read-map](#read-map) wires back from that scoped channel. The wire evidence (the trainable composition node's `handler_enter.reads_snapshot`) is what review grounds its judgment in; the discipline survives review's interpretive layer because the captured-record signal is uniform across runs.

{#validator-purity-r-handler-013}
### Validator purity ([R-handler-013](#handler-derived-rules))

- **Trigger** — review pass over each registered validator's body.
- **Mechanism** — pattern-match the same forbidden set as transform purity: external runtime resource access, clock reads, random-number generation, observation of external state. Test: same value + same bound parameters → the same verdict, every invocation.
- **Diagnosis** — a check that needs external state is not a validator: route it through a service handler writing a verdict channel (or a downstream verdict-transform). The mechanical halves — resolution seals, signature discipline, data-only parameters — are R-handler-012's; review covers only the body.

---

{#handler-cross-references}
## Cross-references

- [reference](#handler-reference) — per-component grammar and the handler-component derived rules cited throughout.
- [handler-kinds](#architecture-handler-kinds) — the cross-component shared shape; the comparison table cited throughout.
- [enforcement-modes](#architecture-enforcement-modes) — the engine-vs-review split this file's two sections divide along.
- [principles](#principles) — invariants I1-I4 cited above.
- [error-channel reference](#error-channel-reference) — R-error-channel-001 and R-error-channel-002 cited above.
- [glossary](#glossary) — the engine vocabulary cited throughout.
- **error-index** (`reference/error-index.md`) — the codegen-built error → rule map.
