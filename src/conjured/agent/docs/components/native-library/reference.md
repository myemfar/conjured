---
kind: reference
audience: [authors, integrators, agents]
slug: native-library
component: native-library
---

<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
{#native-library-reference}
# Native library

(native-library-reference-kernel)=

The **first-party blessed handler catalog** (the `conjured*` namespace — see
§ Naming) — the **primary authoring surface**: most pipelines are *composed* from
native-library handlers declaratively; custom handlers are the exception. (The
overview develops this claim and the three-tier authoring model.) This reference
owns the **naming convention** and the contracts of the engine portion's
**normative members** — the handlers other canon depends on.

{#native-library-naming}
## Naming — `conjured.lib.<name>`, ordinary dotted-path resolution

Every native-library handler is referenced by its **qualified dotted path** under
the `conjured.lib` namespace — `name = "conjured.lib.blob_reference_emitter.emit"` — and resolves
through the same [dotted-path resolution](#dotted-path-resolution) as any
third-party or consumer handler. The engine registers **no entry-point short
names** for its own handlers: a bare `blob_reference_emitter` would squat a generic name in
the global short-name space and turn every third-party package shipping a
same-named handler into a startup collision. The native library gets no special
resolution path — it is resolved, audited, and shape-checked exactly as foreign
code is, which keeps the resolution machinery's verification surface single.

**Native is the `conjured*` first-party namespace, not one package.** The engine
ships its portion under `conjured.lib.*`; first-party companion packages ship their
blessed members under their own `conjured*` namespace, resolved identically — all
first-party, blessed, and canon-covered. Third-party handlers are distinguished by
*not* carrying a first-party `conjured*` name. Because the `conjured*` convention
itself generates the blessed set, no enumerated list of blessed first-party namespaces
need exist anywhere — membership is decided by the naming convention, not by a roster.

{#native-library-http-member-conventions}
## HTTP-speaking member conventions

Every blessed member that speaks HTTP to a backend — the engine's native trainable backends
and a first-party companion package's service members alike (§ Naming: the `conjured*`
convention generates the blessed set) — answers the wire questions below the same way.
This section is the **one owner** of those answers: member entries here, companion-package
member docs, and the members' own module docstrings cite it, each stating per member only
what genuinely varies (the route and the wire form).

(http-member-conventions-kernel)=

- **Endpoint.** The transport `endpoint` field's value is the serving runtime's **base URL —
  the URL the member's wire form appends its route to, including the wire's API-version
  segment where the wire has one** — stated per member entry (the OpenAI-compatible wire's
  base carries `/v1`, e.g. `https://api.example.com/v1`; the llama.cpp wire's base is the
  server root); the member appends **only its wire's route** and never synthesizes a version
  segment. The one exception class: a member whose contract IS calling an
  arbitrary consumer-named endpoint carries the **full URL in a single field named `url`**
  and appends nothing.
- **Wire success.** A backend response is a success **iff its HTTP status equals the wire's
  documented success status** — `200` unless the member's entry states another single value.
  Any other status — **other `2xx` values included** — is a wire failure raised raw, never
  retried, never substituted (an unexpected-but-tolerated status is a shape surprise, and in
  this engine a tolerated surprise is training-data risk). The same exception class as the
  endpoint rule: the arbitrary-endpoint member's wire has no documented status to pin, so
  its entry documents its own success predicate (the `2xx` range) — the range is that
  member's stated contract, never a family default.
- **Credential rendering.** For a member bound to **one known wire form**, the credential
  transport field is named **`api_key_ref`**, a nullable `secret_ref` —
  [R-deployment-003](#R-deployment-003) owns the reference grammar and the
  engine-never-fetches resolution. The store holds the **bare token**; the member resolves
  the reference at dispatch and renders `Authorization: Bearer <token>`; a null is the
  unauthenticated no-credential state (no header emitted). The same exception class as the
  endpoint rule: a member serving **any** authentication scheme cannot render, so its field
  carries a complete pre-rendered value under a name that says so — the store-contract
  split ([what a field's store holds is the field's declared
  contract](#what-the-store-holds-kernel)) is owned at the deployment reference.
- **Timeout.** The per-call timeout transport field is named **`timeout_ms`** (nullable,
  integer milliseconds) — the canonical instance of the author-named service-binding timeout
  ([its declaration and placement](#service-binding-timeout-kernel) are owned at the
  error-channel reference). The member converts it to seconds at its wire-client seam; a
  null/absent value means the call waits on the serving runtime.
- **Headers.** A member whose wire admits per-deployment request headers carries them in a
  transport field named **`headers`** — **non-secret** headers only. A credential never rides
  in the dict: per the secret-reference authoring discipline
  ([a credential never rides inside a collection value](#secret-references-collection-rule)),
  the credential gets its own dedicated `secret_ref` field —
  [R-deployment-003](#R-deployment-003) owns the reference grammar.

{#native-library-shared-wire-floor}
### The shared wire floor — `conjured.adapters.wire`

The conventions above are realized once, in one engine module: `conjured.adapters.wire`, the
**shared wire floor** every HTTP-speaking blessed member builds on — the engine's native
trainable backends and a first-party companion package's service members alike import it
directly (one recipe, never re-derived per package). Its protocol surface:

- **`urllib_transport`** — the one blessed wire client: a stdlib `urllib` **POST** returning
  `(status, body)`. An HTTP error status returns **as data** — the member raises its structured
  wire error per the *Wire success* rule above, never a raw `HTTPError`; a transport-level
  failure (connection refused, DNS) rides raw. `urllib_streaming_transport` is its chunked-read
  streaming sibling, and `iter_sse_data` extracts SSE `data:` payloads (the framing the covered
  streaming wires speak).
- **`prepare_json_transport`** — transport preparation for a JSON-speaking member: the
  fail-loud missing-`endpoint` guard (no hosted default exists to fall back to), bearer-header
  rendering from the resolved `api_key_ref`, and the `timeout_ms` conversion — the *Endpoint* /
  *Credential rendering* / *Timeout* rules above, applied in one step.
- **`timeout_seconds`** — the standalone `timeout_ms` → seconds conversion (the *Timeout*
  rule applied alone), for a member whose transport preparation does not fit the bearer-JSON
  shape above (the companion package's arbitrary-method member is the realized consumer).
- **`expect_success`** — the strict success-status guard: returns the raw body iff the status
  equals the wire's documented success status, else raises the member's wire error (the *Wire
  success* rule's enforcement point).
- **`parse_json_object_response`** — `expect_success` plus the fail-loud JSON parse and
  JSON-object shape guards: a body that is not JSON, or not a JSON object, raises the member's
  structured wire error naming the offending shape — never a raw access-path error. Per-wire
  field extraction (what the object must contain) stays with the member.

**The logic is single-homed; the wire-error class rides as a parameter.** The engine's natives
raise `TrainableWireError` — the structured backend-protocol failure (an error status, an
unparseable or shape-alien response body); a **runtime** failure, not a validation verdict, it
rides raw through dispatch and the runner wraps it as [PipelineFailure](#pipelinefailure) at
the boundary. A companion package's members pass their own wire-error class with the same
posture. The floor's consumers are blessed members' **adapters** — engine-side machinery —
never handler bodies, whose import surface [R-handler-007](#R-handler-007) closes. The
module's other half, the trainable constraint/payload rendering, is not this floor: the
literal-equal seal ([R-handler-005](#R-handler-005)) and § Trainable backends own that
contract.

{#native-library-normative-members}
## Normative members

The members below are load-bearing for other canon today; each ships as a regular
bare kwarg-only handler (or hook) under the full handler contract.

{#native-library-blob-reference-emitter}
### `conjured.lib.blob_reference_emitter.emit` — blob-reference rendering (stdlib-emission hook)

The native [stdlib-emission hook](#the-hook-kind) that emits a binary blob's path /
hash **reference** so a downstream consumer (Studio) can render it. It realizes the
[path/hash-reference convention](#channel-type-discipline-reference-convention) the
handler reference's § Channel-type discipline owns — training-aware pipelines carry
binary content as a `str` reference rather than inline `bytes`, and this is the blessed
emitter of that reference. An [observer node](#the-hook-kind) like any hook:

A [hook](#hook) is the **observer-node** kind: it writes no channels — it returns
`None` by contract, and the runner has no merge path for a hook's return value. No
downstream node reads channels from a hook position.

**Reads — exactly one required port.** `reference` (`str`) — the blob's path / hash
reference (a filesystem path, a content-addressed hash, an S3 key), which the author
wires to this port through the node's read-map. The optional `<name>_hash`
content-addressing sibling the convention documents stays the author's separate concern
(a rendering hook needs only the reference it renders); the closed-set reads discipline
admits no optional second port — one required `reference`, never an "if present" hash
read.

**Emission — stdlib `logging`, zero service-typed bindings.** The hook emits the
reference value via Python's standard `logging` to a documented engine logger; its
`service_bindings` is empty — the stdlib-emission case
([R-handler-007](#R-handler-007)'s stdlib clause; [R-handler-009](#R-handler-009)'s
zero-entry case), so the dispatch signature carries no `services` kwarg. How the
consumer renders the emitted reference is consumer-side and outside this hook's
contract: the engine emits the value and reserves no rendering vocabulary for it (it
forwards a value it does not interpret).

**Transport — the stdlib-side deployment config.** Per the
[stdlib-emission rule](#transport_schema-stdlib-non-empty) (the handler reference requires
a non-empty `transport_schema` for stdlib emission) the hook declares `format`
(`Literal['plain', 'json']`), the per-deployment
record-format selector, delivered to the body as a kwarg like a binding. The emission sink itself is
the documented engine logger, configured deployment-side through standard logging
configuration — the blessed member binds no log-file path of its own.

**Return — `None`.** The hook writes no channels and returns `None`; a non-`None`
return is rejected as a [ContractViolation](#contractviolation) at dispatch
([R-handler-001](#R-handler-001) — the runner has no merge path for a hook return).
Operational emission failure is tolerated by the runner's hook wrapper.

{#native-library-trainable-backends}
## Trainable backends — the wire-form natives

The engine's native trainable-backend adapters (the handler reference's
§ Trainable backends — the trainable-backend gate — owns the trainable-backend
property contract, the certification, and the audit-stamp path; this section owns
the realized members). **Names are wire forms** — the name says what the adapter *speaks*,
never a model type; there are no by-model-type aliases. For an adapter member,
the shipped TOML is its **service-type declaration**, shipped as a same-named
sibling of the adapter module; the implementation class resolves by ordinary
dotted path (`conjured.lib.<module>.<Class>` — no entry-point short names, per
§ Naming), paired to its service-type by qualified name.

**Binding a native backend supplies identity only — you do not re-author its declaration.**
Each native backend's service-type declaration ships with the engine (the same-named sibling
above); to bind one, a composition writes a single `[service_bindings.<name>]` entry naming
the native qualified name and supplying its identity values (`model`) — exactly as it binds
any service-type. Authoring a service-type TOML under a native qualified name (a hand-written
`conjured.lib.openai_compatible_trainable` declaration, say) tries to **redefine an
engine-owned identity**: that qualified name already resolves to the engine's shipped
declaration and its **one registered implementation** ([R-service-type-004](#R-service-type-004)),
so there is nothing for an author to re-declare. A backend the native catalog does **not**
cover is the other case: it gets its **own package-prefixed qualified name** and a certified
(audit-stamped) trainable-backend adapter — never a redefinition of a `conjured.lib.*` name.

(native-library-trainable-backends-description-delivery)=

**Description delivery is a per-wire capability.** A `trainable.output_schema` field's
`description` is model-facing contract content that folds into the hashes
([hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs) owns the
treatment). Whether a member's wire can carry it is stated in that member's entry below — the
`json_schema` wire carries descriptions in the submitted schema; a GBNF grammar cannot. A described
field bound to a non-delivering wire is a compose-time [ContractViolation](#contractviolation), not
a silent drop.

The members share one contract surface:

- **Identity:** `model` (the model identifier the consumer's serving runtime
  hosts) — and nothing else. A trainable backend admits no prompt-template
  identity: prompt-shaping content arrives via `trainable.reads` only
  (R-handler-011), and the clean read/write seal (property 4) forbids
  service-side shaping.
- **Transport (never hashed):** the three
  [HTTP-speaking member convention](#http-member-conventions-kernel) fields —
  `endpoint`, `api_key_ref`, `timeout_ms` — with one trainable-specific sharpening:
  `endpoint` is **required with no declared default** (no hosted default exists,
  property 2 — the consumer's serving runtime is named by the deployment or the
  call fails loud).
- **Config:** `temperature`, `max_tokens` — each carrying a **declared
  ship-time default** in the member's service-type declaration (the
  config-side realization of ship-time defaults; the service-type reference's
  § The `[config_schema]` contract owns the supply rule). A composition that
  states a dial overrides the default; a composition that doesn't gets the
  declared default — visible in the shipped declaration, hash-covered through
  the effective value, never a server-side unknown. **Every dial always
  reaches the wire with a concrete value**; there is no unpinned-omit path
  (the serving runtime's own defaults never apply). The default values
  themselves have exactly one home — each member's shipped service-type
  declaration, the same-named sibling of the adapter module; canon never
  restates them.
- **Config extras (one open table — hashed, engine-opaque):** each member's
  `[config_schema]` declares, beside the enumerated dial core, exactly one
  open **`extras`** table for server-specific generation parameters the
  cross-server core does not enumerate (mirostat settings, guided-decoding
  options, logit biases, …). Its keys are author-named and the engine never
  reads them: the adapter passes the table through to the wire's
  generation-parameter surface verbatim. It is **hash-covered as data** —
  extras shape generation, so they are identity-class and fold into the
  hashes with the config surface, unlike the never-hashed transport side
  (`**transport_extra`). The long tail of server dials is never enumerated:
  a coverage gap in the *wire form* surfaces as a new member; a coverage gap
  in the *dial core* rides `extras`.

(config-extras-reserved-keys-disjoint)=

An `extras` key MUST NOT name one of the adapter family's reserved wire keys — its
declared dial-core fields plus the structural keys it constructs (`model`,
`messages`/`prompt`, `response_format`/`grammar`). Each reserved name has its own
home: the checkpoint identity in `[identity_schema]`, the dials as declared
`[config_schema]` fields, the prompt and seal derived from `reads`/`output_schema`.
An overlap is rejected at declaration load (compose) with a message naming the key's
real home; past compose the extras table provably cannot override an engine-written
wire key (the two key-sets are disjoint by construction).

  The reserved set is the adapter family's certified knowledge — a frozen class
  attribute (`reserved_wire_keys`), one of the adapter's certification attributes
  (the closed set is owned by § Trainable backends in the handler reference); its values live
  with the member adapters (the keys named above — `model`, `messages`/`prompt`,
  `response_format`/`grammar` — are illustrative, not the full set). The certification
  gate validates the attribute's presence/shape; compose reads it for the disjointness
  check. (`extras` carries the sampling tail — `top_p`,
  `top_k`, `repeat_penalty`, `seed`, `logit_bias`, etc. — engine-opaque,
  reserve-only-what-the-engine-uses; never the checkpoint, the prompt, or the seal.)
- **The reads→wire serialization rule (deterministic, content-neutral):**
  exactly one declared input port whose value is a `str` → the bare string,
  verbatim (the assembled-prompt case); anything else → the canonical JSON
  rendering of the full reads dict (key-sorted, compact). Training-time and
  inference-time serializations are byte-identical by this rule.
- **The compose-time caveat fires at construction** (= compose) — the
  seal-expressibility rejected class (owned by the handler reference's § Trainable
  backends — the accepted matrix; wire-form-specific boundaries in the per-member
  entries below):

For the JSON wire forms the native adapters speak, the seal-expressibility rejected class
concretely includes: a **constraint keyword outside the bound wire family's accepted set** — a
value predicate the grammar cannot enforce (a PCRE shorthand like `\d` / `\w` / `\s`, a
deep cross-field or numeric-range predicate), moved to a downstream transform reading the
literally-emitted channel; any **namespaced (dotted) validator key** — opaque third-party code,
never render-eligible, same downstream-transform remedy; a `bytes` channel — no JSON wire
rendering, binary rides path/hash references per the handler reference's § Channel-type
discipline; and a fixed-arity `tuple` channel — a JSON wire delivers arrays, which strict output
validation rejects against a declared tuple, so the seal cannot close end-to-end. The strict
structured-output wire form additionally rejects an open-keyed `dict[str, <T>]` level (the GBNF
wire expresses them).
- **Wire failures fail loud:** an HTTP error, refusal, truncation, or
  unparseable emission raises raw (`PipelineFailure` territory once the runner
  lands); the adapters never retry and never substitute a value.

{#native-library-openai-compatible-trainable}
### `conjured.lib.openai_compatible_trainable` — the OpenAI-compatible structured-output wire

Covers any self-hosted serving runtime exposing an OpenAI-compatible
chat-completions surface (e.g. vLLM): `POST {endpoint}/chat/completions`, one `user`
message carrying the rendered reads, the declared `trainable.output_schema`
submitted as `response_format = {type: "json_schema", …, strict: true}` — the
server-side decode-time seal. Artifact contract: **merged safetensors + a
PEFT/LoRA adapter**. Wire-form boundary: the strict form cannot express an
open-keyed `dict[str, <T>]` — rejected at compose (bind the GBNF wire instead).
This wire **delivers descriptions** ([§ description delivery is a per-wire
capability](#native-library-trainable-backends-description-delivery)): a
`trainable.output_schema` field's `description` rides in the submitted `json_schema`.
**Accepted constraint keywords** (the accepted
matrix is owned by the handler reference's § Trainable backends — these are this
wire's values): only `enum` renders into the submitted schema; `pattern`,
`minLength`/`maxLength`, and numeric-range keywords are rejected at compose, named
with the wire.

{#native-library-gbnf-trainable}
### `conjured.lib.gbnf_trainable` — the llama.cpp / GBNF grammar wire

The GGUF family's direct grammar path: `POST {endpoint}/completion` with the
rendered reads as `prompt` and the declared shape projected into a GBNF
`grammar` the runtime enforces token-by-token (`max_tokens` maps to
`n_predict`). Artifact contract: **GGUF**. The grammar expresses open-keyed
`dict[str, <T>]` shapes the strict wire cannot. Wire-form boundary: this wire
**does not deliver descriptions** ([§ description delivery is a per-wire
capability](#native-library-trainable-backends-description-delivery)) — a GBNF grammar
carries no field-description channel, and the adapter never compensates by shaping the
prompt (property 4). A `trainable.output_schema` field carrying a `description` on this
wire is therefore a compose-time [ContractViolation](#contractviolation)
([hash-model § What the pipeline-hash absorbs](#what-the-pipeline-hash-absorbs) owns
why): route the field to the `openai_compatible` wire, or move the guidance to
`[annotations]`. A declared output-field
**name** carrying a non-ASCII character is likewise rejected at compose
(rename the field within ASCII). The `[a-zA-Z0-9-]` set is the **internal
GBNF rule-name** charset, distinct from the field-name charset: an ASCII `_`
in a field name is admitted — the sanitizer maps it (`_`→`-`) for the
internal rule label while the emitted, literal-equal-validated key preserves
the `_` (the seal holds) — so only a non-ASCII character, which has no ASCII
rule-name mapping, rejects. **Accepted constraint
keywords** (per the same § Trainable backends matrix): `enum`, `minLength`, and
`maxLength` render into the grammar; `pattern` is rejected at compose — a sound
regex→GBNF equivalence is undecidable for full Python `re`, so the wire rejects
rather than submit a seal it cannot hold literal-equal.

{#native-library-value-shaping-operations}
## Value-shaping operations — author transforms over shipped affordances

Value-shaping leaf operations — render a value to a string, map a value through a
lookup table, select among candidates, load a declaration file, validate a value —
are realized by **author-written [transforms](#transform) over the engine's shipped
compile and binding affordances**, and by **declarative engine features**, not by
generic catalog members:

The [read model](#read-map) wires each input port to exactly one
[channel](#channel) of one declared [channel-field type](#channel-field-type),
and that type vocabulary admits no type-variable — so no single generic member can
be shipped for a type-parametric operation: the genericity lives in author code
parameterized by a binding, not in a shipped member or an engine affordance.

So a single "shape any value" member cannot exist — the same division of labor the
[aggregator pattern](#the-aggregator-pattern-division-of-labor) draws for fan-in. The
common-case members the catalog carries have concrete port types, so they ship
cleanly — the [trainable backends](#native-library-trainable-backends), the
[blob-reference emitter](#native-library-blob-reference-emitter).

The route per operation:

- **Render a value to a string** — an author transform binding a compiled template or
  pattern via the [compile directive](#compile-directive) (`jinja` / `regex`), reading
  the template's variables through its concrete input ports and writing the `str`
  result.
- **Map a value through a lookup table** — an author transform binding the table as a
  [compose-time binding](#compose-time-binding) (inline or by `{ file }`), reading the
  concrete key port and writing the concrete value port.
- **Select among candidate values** — the [aggregator pattern](#the-aggregator-pattern-division-of-labor): an
  author-written transform reducing the candidate channels; picking one is its
  degenerate single-output case.
- **Load a declaration file into a typed value** — the `{ file = "..." }`
  [compose-time binding](#compose-time-binding) resolves and validates the file at
  compose; no handler is involved. (Resolving a path chosen per dispatch is file
  I/O — outside the pure engine's transforms.)
- **Validate a value** — [field validators](#field-validators-kernel) are named
  constraints attached to a field only where declared, not a value-shaping member;
  what applies on every channel and port is the engine-generated Pydantic **type**
  validation, and a bound external schema rides the `json_schema`
  [compile directive](#compile-directive). Validation is structural and pervasive,
  never a node an author inserts.

{#native-library-catalog}
## The catalog

The **engine** catalog ships one TOML + one module + one audit entry per
[handler](#handler); members are added to this reference as they ship — the
catalog list is never enumerated ahead of the code. Type-generic value-shaping
operations are realized without catalog members (above).
