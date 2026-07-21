# Changelog

Notable changes to the `conjured` package, for consumers upgrading between published
releases. The record starts at 0.2.0 — the first release published from this repository.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Pre-1.0, **minor versions
are the breaking lane**: no compatibility is owed until 1.0, and every break is announced
here with its migration.

## [0.2.0] - 2026-07-20

### Breaking

- **The native trainable adapters' credential field was renamed and retyped.**
  `api_key` (a raw bearer value) → `api_key_ref` (a `secret_ref` — a `[scheme]payload`
  reference the engine dereferences at dispatch; see the deployment reference's
  Secret references section). A deployment carrying the old field now fails loudly at
  compose, twice: an unknown transport key, and a missing declared field. Migration:
  `api_key = "sk-…"` → `api_key_ref = "[env]YOUR_VAR"`;
  `api_key = { null = true }` → `api_key_ref = { null = true }`. Affects
  `conjured.lib.openai_compatible_trainable` and `conjured.lib.gbnf_trainable`.
- **The public export sets were narrowed to the canon-declared API.**
  `conjured.validator.__all__` is now exactly `loads` / `parse` / `DeclarationRegistry` /
  `compile_pipeline`; `conjured.runner.__all__` is `assemble` / `run` / `Runnable` /
  `RunResult`; `conjured.ir.__all__` is the eight opaque declaration/graph handles. Code
  importing previously re-exported internals from those package tops must import from the
  owning submodule (or, better, stay on the declared API — the pipeline reference's
  In-process compose API section is the contract).

### Added

- **The server component** (`pip install "conjured[server]"`): the HTTP + SSE wire surface —
  `POST /runs`, the run event stream, and the token stream endpoint — plus the bundled
  Python client (`conjured.client`) wrapping a loopback server subprocess, and the
  `python -m conjured.server` launch surface (`--app`/`--host`/`--port`/`--port-file`/
  `--stream-timeout`).
- **Streaming dispatch**: a trainable composition may declare `streamable = true`; a
  streaming-capable backend exposes `invoke_streaming`, and `run(..., stream_sink=...)`
  delivers raw fragments while the dispatch is in flight (fragments are provisional
  transport; the validated assembled value remains the only channel value).
- **Trained-artifact integrity** (`conjured.manifest`): `<artifact>.conjured.toml` sidecar
  manifests, the deployment `[artifacts]` section, drift events on every hash mismatch, and
  graduated enforcement under `training_contract.integrity_enforcement` — plus the
  `conjured artifact-tag` / `conjured artifact-mv` CLI pair for authoring sidecars.
- **Deadline propagation**: a service/trainable adapter surface that declares the optional
  `remaining_budget_ms` dispatch-kwarg receives the run's remaining `timeout_ms` budget at
  each call and applies `min(its per-call transport timeout, the remaining budget)`; the
  native backends participate on both dispatch surfaces.
- **The agent surface** (`conjured.agent` package data): `llms.txt`, the audience-filtered
  docs bundle, steering content, and the machine-readable `error-classes.toml` — all
  shipped in the wheel and reachable via `importlib.resources`.
- **The canonical docs corpus ships in the wheel** (`conjured/docs/…`), so engine errors
  resolve their `rule_id` documentation locally, offline.
- **The first how-to** (`docs/how-to/compose-and-run.md`): declaration TOML to a running
  pipeline in-process, including carrying state across runs and testing a service pipeline
  with a verified fake.
- **The consumer testing library** (`conjured.testing`, a pytest plugin installed with the
  package): registry/module fixtures, event-stream assertions, `VerifiedFake` doubles, and
  hash-gated seam fixtures.
- Nested-records channel types (list-of-record / dict-of-record composite forms) and the
  `bundle` composition kind.

### Changed

- `conjured.events` consumer surface is now declared public API: `attach_consumer` /
  `subscribe` / `event_logger` and the `CANONICAL_EVENT_CLASSES` roster, with the
  producer/consumer isolation wall documented at its canon owner.
- Hash inputs were extended for the new surfaces (streaming declarations, the manifest
  registrations, nested-record shapes): compositions using new surfaces hash differently
  than any 0.1.1 hash of the same text — recorded here per the release policy's rule that
  a hash-moving change is never a silent patch. Unchanged 0.1.1-era compositions that avoid
  the new surfaces keep their hashes except where a breaking item above applies.

[0.2.0]: https://github.com/myemfar/conjured/releases/tag/v0.2.0
