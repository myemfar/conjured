---
kind: reference
audience: [integrators]
slug: index
---

{#index}
# Conjured

Train the model your pipeline actually runs. Conjured composes handlers into
a typed dataflow graph; the runtime contract and the training-data shape are
two queries against that same graph — capture real runs as training records,
fine-tune your local model, and ship with the served pipeline hash-checked at
every load against the one it learned.

**Use Conjured when** you are fine-tuning the model your product will
actually serve. Single-call structured output, agent orchestration, and
prompt optimization are different layers — reach for those tools when you
don't need the pipeline-level training contract; reach for Conjured when
you do.

This site is the integrator surface. The **overview** (under Explanation
below) is the recommended entry point.

{#explanation}
## Explanation

```{toctree}
:maxdepth: 1

explanation/overview
explanation/pipeline-as-training-contract
explanation/handler-kinds
explanation/exhaustive-declaration
explanation/enforcement-modes
explanation/trust-model
explanation/hash-model
explanation/handler-reference-explanation
explanation/pipeline-reference-explanation
explanation/error-channel
```

{#index-how-to}
## How-to guides

```{toctree}
:maxdepth: 1

how-to/compose-and-run
```

{#architecture}
## Architecture

```{toctree}
:maxdepth: 1

architecture/handler-kinds
architecture/handler-resolution
architecture/exhaustive-declaration
architecture/enforcement-modes
architecture/trust-model
architecture/hash-model
architecture/context
architecture/components
```

{#index-components}
## Components

```{toctree}
:maxdepth: 2

components/handler/reference
components/handler/conformance
components/handler/kind-schemas/README
components/native-library/reference
components/native-library/conformance
components/pipeline/reference
components/pipeline/conformance
components/error-channel/reference
components/error-channel/conformance
components/service-type/reference
components/service-type/conformance
components/deployment/reference
components/deployment/conformance
components/server/reference
components/server/conformance
components/testing/reference
components/testing/api
```

{#index-reference}
## Reference

```{toctree}
:maxdepth: 1

reference/glossary
reference/principles
reference/error-index
```
