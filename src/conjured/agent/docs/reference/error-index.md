---
kind: reference
audience: [authors, integrators, agents]
slug: error-index
---
<!-- GENERATED from conjured/docs by tools/build_agent_surface.py — DO NOT EDIT -->
<!-- GENERATED — DO NOT EDIT (hash: 86aa7f4bb2ba3b92) -->
# Error index

The cross-reference from the engine's **registered error set** to the derived
rules it enforces — generated from the registration API (`conjured.errors`:
`CHECK_REGISTRY` + `AUDIT_CODE_REGISTRY`) by `tools/gen_error_index.py`. The
constructors reject an unregistered `audit_code` / `(check, rule_id)` pair, so
this index is complete by construction.

Audit `<CX>.<TOPIC>.<NNN>` codes are assigned incrementally as the catalog
grows; an unassigned violation dispatches on its symbolic `check` discriminator
— the consumer / test dispatch key present on every `ContractViolation` — and
registered audit codes appear below as the catalog assigns them. The remediation
path for any row is the owning reference named in the rule legend.

## Registered audit codes

| audit_code | error class | check | enforces |
|---|---|---|---|
| `C1.HALT_ON_INPUT_VALIDATION_ERROR.001` | SchemaValidationError | `halt-on-input-validation-error` | R-error-channel-003 (halt semantics) |
| `C1.HALT_ON_SCHEMA_VALIDATION_ERROR.001` | SchemaValidationError | `halt-on-schema-validation-error` | R-error-channel-003 (halt semantics) |
| `C1.PIPELINE_FAILURE_WRAP.001` | PipelineFailure | `pipeline-failure-wrap` | R-error-channel-001 (closed-enum error classes) |

## Check discriminators (the symbolic dispatch keys)

One row per `Check` member, in the enum's stage order. `enforces` lists every
rule_id a raise site may cite with that check (the registered set).

| check | error class | enforces |
|---|---|---|
| `handler-kind-header` | ContractViolation | `R-handler-003`, `R-handler-006` |
| `closed-grammar` | ContractViolation | `R-deployment-001`, `R-deployment-002`, `R-handler-004`, `R-handler-006`, `R-handler-010`, `R-pipeline-001`, `R-pipeline-002`, `R-service-type-001` |
| `section-presence` | ContractViolation | `R-deployment-001`, `R-handler-006`, `R-service-type-001` |
| `body-required` | ContractViolation | `R-deployment-001`, `R-handler-006`, `R-pipeline-001`, `R-service-type-001` |
| `channel-type-token` | ContractViolation | `R-handler-006`, `R-pipeline-001`, `R-service-type-001` |
| `nullable-placement` | ContractViolation | `R-service-type-001` |
| `unknown-composition-kind` | ContractViolation | `R-handler-006` |
| `malformed-declaration` | ContractViolation | `R-deployment-001`, `R-deployment-002`, `R-handler-006`, `R-handler-010`, `R-handler-011`, `R-pipeline-001`, `R-pipeline-002`, `R-service-type-001` |
| `handler-name-resolution` | ContractViolation | `R-pipeline-001` |
| `service-type-resolution` | ContractViolation | `R-pipeline-001`, `R-service-type-004` |
| `bundle-reaches-byref-fold` | ContractViolation | `R-pipeline-001` |
| `composition-embed-cycle` | ContractViolation | `R-pipeline-001` |
| `read-write-shape-mismatch` | ContractViolation | `R-pipeline-001` |
| `wiring-map-port` | ContractViolation | `R-pipeline-001` |
| `dangling-identity-port` | ContractViolation | `R-pipeline-001` |
| `read-port-unclosed` | ContractViolation | `R-pipeline-001` |
| `single-assignment` | ContractViolation | `R-pipeline-001` |
| `channel-write-overlap` | ContractViolation | `R-pipeline-002` |
| `merge-strategy-type` | ContractViolation | `R-pipeline-002` |
| `binding-supply-incomplete` | ContractViolation | `R-handler-006`, `R-pipeline-001` |
| `service-binding-cardinality` | ContractViolation | `R-handler-008`, `R-handler-009` |
| `identity-transport-placement` | ContractViolation | `R-pipeline-001` |
| `transport-coverage-gap` | ContractViolation | `R-pipeline-001` |
| `hook-transport-coverage-gap` | ContractViolation | `R-pipeline-001` |
| `inputs-outputs-dead-declaration` | ContractViolation | `R-pipeline-001` |
| `config-schema-supply` | ContractViolation | `R-service-type-002` |
| `streamable-terminal-node` | ContractViolation | `R-pipeline-001` |
| `streamable-backend-support` | ContractViolation | `R-handler-008` |
| `streamable-sink-target` | ContractViolation | `R-pipeline-001` |
| `deployment-override-target` | ContractViolation | `R-deployment-002` |
| `name-uniqueness` | ContractViolation | `R-handler-006`, `R-pipeline-001`, `R-service-type-001` |
| `handler-module-import` | ContractViolation | `R-handler-012`, `R-pipeline-001`, `R-service-type-003` |
| `handler-namespace-package` | ContractViolation | `R-handler-012`, `R-pipeline-001`, `R-service-type-003` |
| `module-origin-divergence` | ContractViolation | `R-handler-012`, `R-pipeline-001`, `R-service-type-003` |
| `handler-pure-module` | ContractViolation | `R-handler-pure-module` |
| `handler-function-shape` | ContractViolation | `R-handler-bare-function` |
| `handler-signature-mismatch` | ContractViolation | `R-handler-001` |
| `entry-point-collision` | ContractViolation | `R-handler-012`, `R-pipeline-001`, `R-service-type-004` |
| `adapter-pure-module` | ContractViolation | `R-handler-pure-module`, `R-service-type-003` |
| `adapter-signature-mismatch` | ContractViolation | `R-service-type-002`, `R-service-type-003` |
| `audit-stamp-not-fresh` | ContractViolation | `R-handler-pure-module` |
| `audit-stamp-malformed` | ContractViolation | `R-handler-pure-module` |
| `adapter-construction` | ContractViolation | `R-service-type-003` |
| `engine-owned-identity` | ContractViolation | `R-service-type-004` |
| `trainable-backend-certification` | ContractViolation | `R-handler-008` |
| `trainable-constraint-unsupported` | ContractViolation | `R-handler-005` |
| `validator-signature-mismatch` | ContractViolation | `R-handler-012` |
| `validator-parameter-binding` | ContractViolation | `R-handler-012` |
| `undeclared-output-key` | ContractViolation | `R-handler-001` |
| `missing-declared-write` | ContractViolation | `R-handler-001` |
| `return-shape` | ContractViolation | `R-handler-001` |
| `hook-return-not-none` | ContractViolation | `R-error-channel-003`, `R-handler-001` |
| `halt-on-input-validation-error` | SchemaValidationError | `R-error-channel-003` |
| `halt-on-schema-validation-error` | SchemaValidationError | `R-error-channel-003` |
| `external-binding-content-unsupported` | ContractViolation | `R-pipeline-001` |
| `api-invocation-declared-inputs-enforcement` | ContractViolation | `R-pipeline-001` |
| `pipeline-failure-wrap` | PipelineFailure | `R-error-channel-001` |
| `compile-signature` | ContractViolation | `R-pipeline-001` |
| `compile-artifact` | ContractViolation | `R-pipeline-001` |
| `binding-value-shape` | ContractViolation | `R-pipeline-001` |
| `explicit-null-target` | ContractViolation | `R-pipeline-001` |
| `transport-handle-coherence` | ContractViolation | `R-pipeline-001` |
| `trained-artifact-manifest-missing` | ContractViolation | `R-pipeline-003` |
| `trained-artifact-manifest-malformed` | ContractViolation | `R-pipeline-003` |
| `training-bundle-hash-mismatch` | ContractViolation | `R-pipeline-003` |
| `artifact-trainable-unknown` | ContractViolation | `R-pipeline-003` |
| `secret-ref-malformed` | ContractViolation | `R-deployment-003` |
| `secret-ref-scheme-unknown` | ContractViolation | `R-deployment-003` |
| `secret-resolver-invalid` | ContractViolation | `R-deployment-003` |

## Rule legend

| rule | name | owning reference (the remediation path) |
|---|---|---|
| `R-deployment-001` | closed deployment-declaration grammar | `components/deployment/reference.md` |
| `R-deployment-002` | shared-by-binding transport resolution with per-pipeline override | `components/deployment/reference.md` |
| `R-deployment-003` | secret references — validated shape, engine-never-fetches resolution | `components/deployment/reference.md` |
| `R-error-channel-001` | closed-enum error classes | `components/error-channel/reference.md` |
| `R-error-channel-003` | halt semantics | `components/error-channel/reference.md` |
| `R-handler-001` | engine-constructed dispatch wrapper | `components/handler/reference.md` |
| `R-handler-003` | closed-enum handler kinds | `components/handler/reference.md` |
| `R-handler-004` | transform purity | `components/handler/reference.md` |
| `R-handler-005` | literal-equal rule | `components/handler/reference.md` |
| `R-handler-006` | closed handler-declaration shape grammar | `components/handler/reference.md` |
| `R-handler-008` | exactly one service-typed binding (service handler and trainable composition node) | `components/handler/reference.md` |
| `R-handler-009` | hook binding cardinality | `components/handler/reference.md` |
| `R-handler-010` | trainable composition has no author body | `components/handler/reference.md` |
| `R-handler-011` | prompt-shaping content via trainable.reads | `components/handler/reference.md` |
| `R-handler-012` | validator registration and binding contract | `components/handler/reference.md` |
| `R-handler-bare-function` | handler function-shape check (vector-2 seal) | `components/handler/reference.md` |
| `R-handler-pure-module` | handler module purity | `components/handler/reference.md` |
| `R-pipeline-001` | compose-time composition validation | `components/pipeline/reference.md` |
| `R-pipeline-002` | channel-write disjointness with merge opt-in | `components/pipeline/reference.md` |
| `R-pipeline-003` | trained-artifact integrity enforcement | `components/pipeline/reference.md` |
| `R-service-type-001` | closed service-type declaration grammar | `components/service-type/reference.md` |
| `R-service-type-002` | config-schema contract | `components/service-type/reference.md` |
| `R-service-type-003` | service-impl dispatch contract | `components/service-type/reference.md` |
| `R-service-type-004` | one implementation per service-type qualified name | `components/service-type/reference.md` |
