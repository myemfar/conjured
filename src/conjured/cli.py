"""``conjured`` — the umbrella command-line interface (console entry point).

The umbrella ``conjured`` console script (``[project.scripts]`` in ``pyproject.toml``) dispatches
the three canon-named subcommands: ``conjured derivables`` — the CLI half of the
derivables-extraction surface (``conjured/docs/components/pipeline/reference.md`` § Extraction
surface) — and the manifest CLI pair ``conjured artifact-tag`` / ``conjured artifact-mv``
(§ The manifest CLI pair): the consumer-side sidecar authoring tools (the engine only ever
READS a sidecar, at deployment load, per R-pipeline-003).

**What the CLI owns: path→registry assembly.** The engine deliberately has NO disk/directory
pipeline loader (``server/app.py`` module docstring) — a declaration registry is assembled by
hand. That assembly is the CLI's job and lives here only: read the explicitly-typed declaration
files, load each through the engine's ``validator.loads`` (so a mis-typed file fails loud with
its real ``ContractViolation``, never a silent guess), register them under their resolution keys,
run the engine's external-binding resolution pass, then delegate the bundle construction to the
pure library entry point ``conjured.derivables.extract``. No pipeline shape or graph semantics
live here — only argument handling, file reading, and serialization.

**Registry keys.** A handler resolves by its dotted **qualified name** and a composition by the
exact **path string its pipeline node names** — neither is derivable from a file's location — so
those flags carry the key explicitly: ``--handler <qualified.name>=<path>`` /
``--composition <node/path/string>=<path>``. A service-type declaration self-identifies (its
own ``name`` field), so ``--service-type`` takes a bare path.

**Errors fail loud, unchanged.** A missing file, a kind-mismatched file, or a compile-invalid
pipeline surfaces its REAL error — the engine's structured ``ContractViolation`` propagates
untouched from ``loads`` / ``compile``, an ``OSError`` from a missing file — and :func:`main`
maps it to a non-zero process exit (success is ``0``). Nothing is swallowed or degraded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from conjured import __version__
from conjured.derivables import bundle_hash, extract, serialize
from conjured.errors import ConjuredError
from conjured.validator import DeclarationRegistry, loads
from conjured.validator.resolve import (
    resolve_compile_param_files,
    resolve_pipeline_bindings,
)


def _assemble_registry(
    pipeline_path: str,
    *,
    handlers: Sequence[tuple[str, str]] = (),
    compositions: Sequence[tuple[str, str]] = (),
    service_types: Sequence[str] = (),
):
    """The shared registry-assembly front half (`derivables` and `artifact-tag` take the
    same declaration flags): load + register every declaration through the engine's
    ``validator.loads`` (fail loud on a mis-typed file), parse the pipeline, run the
    external-binding + compile-param resolution passes. Returns
    ``(pipeline_declaration, registry)``."""
    registry = DeclarationRegistry()
    for path in service_types:
        abspath = os.path.abspath(path)
        registry.add_service_type(
            loads(_read(abspath), "service_type", file_path=abspath), toml_path=abspath
        )
    for name, path in handlers:
        abspath = os.path.abspath(path)
        registry.add_handler(
            name, loads(_read(abspath), "handler", file_path=abspath), toml_path=abspath
        )
    for node_path, path in compositions:
        abspath = os.path.abspath(path)
        registry.add_composition(
            node_path, loads(_read(abspath), "composition", file_path=abspath), toml_path=abspath
        )
    pipeline_abspath = os.path.abspath(pipeline_path)
    pipeline = loads(_read(pipeline_abspath), "pipeline", file_path=pipeline_abspath)
    pipeline = resolve_pipeline_bindings(
        pipeline, registry, base_dir=os.path.dirname(pipeline_abspath)
    )
    resolve_compile_param_files(registry)
    return pipeline, registry


def build_bundle(
    pipeline_path: str,
    *,
    handlers: Sequence[tuple[str, str]] = (),
    compositions: Sequence[tuple[str, str]] = (),
    service_types: Sequence[str] = (),
) -> str:
    """Assemble the declaration registry from the given file paths, resolve external bindings,
    extract the derivables bundle, and return the serialized JSON artifact.

    ``handlers`` / ``compositions`` are ``(registry_key, path)`` pairs — the handler's qualified
    name / the composition node's path string, and the file to load. ``service_types`` are bare
    paths (each declaration self-identifies via its ``name``). Every declaration is loaded through
    the engine's ``validator.loads``, so a syntactically or structurally wrong file (including a
    kind-mismatched one) fails loud with its real ``ContractViolation`` here. A missing file
    raises ``OSError`` from the read. The pipeline's external ``{ file = "..." }`` bindings resolve
    relative to the pipeline declaration's own directory (composition bindings relative to each
    composition's own directory — the engine's resolution pass), so the assembly is
    location-independent. Compose-time invalidity surfaces from :func:`extract`'s
    ``compile_pipeline`` call. This function raises the engine's real errors unchanged; the caller
    maps them to an exit code."""
    # Absolute paths so binding resolution anchors to each declaration's actual directory
    # (never the process CWD) and diagnostics carry an unambiguous locus; the shared
    # front half also runs the external-binding + compile-param resolution passes (the
    # hashers inside extract() fail loud on any unresolved file binding).
    pipeline, registry = _assemble_registry(
        pipeline_path,
        handlers=handlers, compositions=compositions, service_types=service_types,
    )
    bundle = extract(pipeline, registry, conjured_version=__version__)
    return serialize(bundle)


def _read(path: str) -> str:
    """Read a declaration file as UTF-8 text (TOML mandates UTF-8). A missing/unreadable file
    raises ``OSError`` — the real error, surfaced loud (the CLI never guesses a default)."""
    return Path(path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Subcommand: derivables
# ---------------------------------------------------------------------------


def _split_key_path(spec: str, flag: str, parser: argparse.ArgumentParser) -> tuple[str, str]:
    """Split a ``KEY=PATH`` flag value into its registry key and file path. A malformed value
    (no ``=``, empty key, or empty path) is a usage error — ``parser.error`` exits with the
    argparse usage code, so a typo fails loud rather than registering under an empty key."""
    key, sep, path = spec.partition("=")
    if not sep or not key or not path:
        parser.error(f"{flag} expects KEY=PATH (got {spec!r})")
    return key, path


def _derivables_main(argv: Sequence[str]) -> int:
    """The ``conjured derivables`` subcommand: assemble the registry from typed flags, extract
    the bundle, and write it to ``-o``/``--output`` (default stdout)."""
    parser = argparse.ArgumentParser(
        prog="conjured derivables",
        description="Extract a pipeline's derivables bundle (schema definitions, "
        "training-bundle-hashes, binding + composition snapshots) as one deterministic JSON "
        "artifact. Pure read: no service invocations, no handler dispatch.",
    )
    parser.add_argument("pipeline", help="path to the pipeline declaration TOML")
    parser.add_argument(
        "--handler", action="append", default=[], metavar="NAME=PATH",
        help="a referenced handler declaration: its qualified name and file path "
        "(repeatable)",
    )
    parser.add_argument(
        "--composition", action="append", default=[], metavar="NODEPATH=PATH",
        help="a referenced composition declaration: the path string its pipeline node names "
        "and its file path (repeatable)",
    )
    parser.add_argument(
        "--service-type", action="append", default=[], metavar="PATH", dest="service_type",
        help="a referenced service-type declaration file (self-identifying; repeatable)",
    )
    parser.add_argument(
        "-o", "--output", default=None, metavar="PATH",
        help="write the bundle to this file (default: stdout)",
    )
    ns = parser.parse_args(list(argv))

    handlers = [_split_key_path(s, "--handler", parser) for s in ns.handler]
    compositions = [_split_key_path(s, "--composition", parser) for s in ns.composition]

    text = build_bundle(
        ns.pipeline,
        handlers=handlers,
        compositions=compositions,
        service_types=ns.service_type,
    )

    # Write the exact UTF-8 bytes serialize() produced — NOT text mode, whose universal-newline
    # translation would rewrite every '\n' to os.linesep (CRLF on Windows), making the emitted
    # bundle non-byte-identical across platforms and breaking the § Bundle serialized form
    # determinism guarantee. The bytes are the same on every OS.
    data = text.encode("utf-8")
    if ns.output is None:
        sys.stdout.buffer.write(data)
    else:
        Path(ns.output).write_bytes(data)
    # The provenance pin (pipeline/reference.md § generator_info): report the
    # derivables_bundle_hash of the exact artifact just written — to stderr, never stdout
    # (stdout carries the artifact's deterministic bytes when -o is absent).
    print(f"derivables_bundle_hash: {bundle_hash(text)}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Subcommands: artifact-tag / artifact-mv — the manifest CLI pair
# (pipeline/reference.md § The manifest CLI pair; the engine only READS sidecars)
# ---------------------------------------------------------------------------


def _toml_value(value: object, *, where: str) -> str:
    """Render one JSON-native value as a TOML value (basic strings with escapes; dicts as
    inline tables; lists as arrays). The sidecar is written, never round-tripped by the
    engine — TOML has no null, so a JSON ``null`` fails loud rather than being dropped."""
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        )
        return f'"{escaped}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v, where=where) for v in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_toml_value(str(k), where=where)} = {_toml_value(v, where=where)}"
            for k, v in value.items()
        )
        return "{" + items + "}"
    raise ValueError(
        f"{where}: {type(value).__name__} is not TOML-representable (JSON null has no "
        "TOML value; supply concrete values only)"
    )


def _render_manifest_toml(
    *,
    artifact: str,
    pipeline_hash: str,
    base_model: str,
    artifact_format: str,
    trained_at: str,
    training_data_source: str,
    generator_info: "dict | None",
    training_bundle_hashes: "dict[str, str]",
) -> str:
    """Render the sidecar TOML per the full manifest field set
    (pipeline/reference.md § Full manifest field set)."""
    lines = [
        "# Trained-artifact manifest — written by `conjured artifact-tag`",
        "# (pipeline/reference.md § Trained-artifact manifest; the engine reads this at",
        "#  deployment load per R-pipeline-003 and never writes it).",
        "[manifest]",
        f"artifact = {_toml_value(artifact, where='artifact')}",
        f"pipeline_hash_set = [{_toml_value(pipeline_hash, where='pipeline_hash_set')}]",
        f"base_model = {_toml_value(base_model, where='base_model')}",
        f"artifact_format = {_toml_value(artifact_format, where='artifact_format')}",
        f"trained_at = {_toml_value(trained_at, where='trained_at')}",
        f"training_data_source = {_toml_value(training_data_source, where='training_data_source')}",
    ]
    if generator_info is not None:
        lines.append("")
        lines.append("[manifest.generator_info]")
        for key, value in generator_info.items():
            lines.append(f"{key} = {_toml_value(value, where=f'generator_info.{key}')}")
    lines.append("")
    lines.append("[training_bundle_hashes]")
    for name in sorted(training_bundle_hashes):
        lines.append(
            f"{_toml_value(name, where='training_bundle_hashes')} = "
            f"{_toml_value(training_bundle_hashes[name], where='training_bundle_hashes')}"
        )
    return "\n".join(lines) + "\n"


def _artifact_tag_main(argv: Sequence[str]) -> int:
    """The ``conjured artifact-tag`` subcommand: assemble the registry from the same typed
    flags as ``derivables``, compute the current pipeline-hash + per-trainable
    training-bundle-hashes, and write ``<artifact>.conjured.toml``."""
    from datetime import datetime, timezone

    from conjured.hasher.hashes import pipeline_hash as compute_pipeline_hash
    from conjured.hasher.hashes import training_bundle_hash
    from conjured.manifest import collect_trainables, sidecar_path

    parser = argparse.ArgumentParser(
        prog="conjured artifact-tag",
        description="Write a trained artifact's sidecar manifest "
        "(<artifact>.conjured.toml): the current composition's pipeline-hash and "
        "per-trainable training-bundle-hashes plus the supplied provenance fields. "
        "The blessed flow is tag-immediately-after-training against the declarations "
        "the corpus was trained on.",
    )
    parser.add_argument("artifact", help="path to the trained artifact file the sidecar accompanies")
    parser.add_argument("--pipeline", required=True, metavar="PATH", help="the pipeline declaration TOML")
    parser.add_argument("--handler", action="append", default=[], metavar="NAME=PATH",
                        help="a referenced handler declaration (repeatable)")
    parser.add_argument("--composition", action="append", default=[], metavar="NODEPATH=PATH",
                        help="a referenced composition declaration (repeatable)")
    parser.add_argument("--service-type", action="append", default=[], metavar="PATH",
                        dest="service_type", help="a referenced service-type declaration file (repeatable)")
    parser.add_argument("--base-model", required=True, metavar="S",
                        help="base model the artifact targets (provenance)")
    parser.add_argument("--artifact-format", required=True, metavar="S",
                        help="serialization format (open provenance string, e.g. safetensors)")
    parser.add_argument("--training-data-source", required=True,
                        choices=["generated", "captured", "external", "mixed"],
                        help="how the training corpus was produced (closed enum)")
    parser.add_argument("--trained-at", default=None, metavar="ISO-8601",
                        help="training completion timestamp (default: now, UTC)")
    parser.add_argument("--generator-id", default=None, metavar="S")
    parser.add_argument("--generator-prompt-hash", default=None, metavar="HEX")
    parser.add_argument("--derivables-bundle-hash", default=None, metavar="SHA256:HEX")
    parser.add_argument("--generation-params", default=None, metavar="JSON",
                        help="free-form generation parameters as a JSON object")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing sidecar (never silent)")
    ns = parser.parse_args(list(argv))

    generator_flags = {
        "--generator-id": ns.generator_id,
        "--generator-prompt-hash": ns.generator_prompt_hash,
        "--derivables-bundle-hash": ns.derivables_bundle_hash,
        "--generation-params": ns.generation_params,
    }
    generated = ns.training_data_source in ("generated", "mixed")
    missing = [flag for flag, value in generator_flags.items() if value is None]
    present = [flag for flag, value in generator_flags.items() if value is not None]
    if generated and missing:
        parser.error(
            f"--training-data-source {ns.training_data_source} requires the generator_info "
            f"flag group; missing: {', '.join(missing)}"
        )
    if not generated and present:
        parser.error(
            f"generator_info flags are admitted only for a generated/mixed corpus "
            f"(got {', '.join(present)} with --training-data-source {ns.training_data_source})"
        )
    generator_info = None
    if generated:
        try:
            generation_params = json.loads(ns.generation_params)
        except json.JSONDecodeError as exc:
            parser.error(f"--generation-params is not valid JSON: {exc}")
        if not isinstance(generation_params, dict):
            parser.error("--generation-params must be a JSON object")
        generator_info = {
            "generator_id": ns.generator_id,
            "generator_prompt_hash": ns.generator_prompt_hash,
            "derivables_bundle_hash": ns.derivables_bundle_hash,
            "generation_params": generation_params,
        }

    sidecar = sidecar_path(ns.artifact)
    if sidecar.exists() and not ns.force:
        print(
            f"conjured artifact-tag: sidecar {sidecar} already exists — re-tagging "
            "requires --force (an existing manifest is never silently overwritten)",
            file=sys.stderr,
        )
        return 1

    handlers = [_split_key_path(s, "--handler", parser) for s in ns.handler]
    compositions = [_split_key_path(s, "--composition", parser) for s in ns.composition]
    pipeline, registry = _assemble_registry(
        ns.pipeline, handlers=handlers, compositions=compositions, service_types=ns.service_type
    )
    ph = compute_pipeline_hash(pipeline, registry)
    trainables, duplicates = collect_trainables(pipeline, registry)
    if duplicates:
        print(
            "conjured artifact-tag: ambiguous trainable names across nesting levels "
            f"({sorted(duplicates)}) — the manifest key must identify one trainable; "
            "give the colliding compositions distinct meta.name values",
            file=sys.stderr,
        )
        return 1
    if not trainables:
        print(
            "conjured artifact-tag: the composition declares no trainable composition "
            "nodes — there is no training-record shape to pin a manifest to",
            file=sys.stderr,
        )
        return 1
    tbh = {name: training_bundle_hash(comp, registry) for name, comp in trainables.items()}

    text = _render_manifest_toml(
        artifact=ns.artifact,
        pipeline_hash=ph,
        base_model=ns.base_model,
        artifact_format=ns.artifact_format,
        trained_at=(
            ns.trained_at
            if ns.trained_at is not None
            else datetime.now(timezone.utc).isoformat()
        ),
        training_data_source=ns.training_data_source,
        generator_info=generator_info,
        training_bundle_hashes=tbh,
    )
    sidecar.write_bytes(text.encode("utf-8"))
    print(f"wrote {sidecar}", file=sys.stderr)
    return 0


def _artifact_mv_main(argv: Sequence[str]) -> int:
    """The ``conjured artifact-mv`` subcommand: rename the artifact file AND its sidecar
    as one pair, rewriting the manifest's ``artifact`` field — the file pair never
    desyncs. Comments in a hand-edited sidecar do not survive (the sidecar is
    re-rendered from its parsed content; every key — known and unknown — is preserved)."""
    import tomllib

    from conjured.manifest import load_manifest, sidecar_path

    parser = argparse.ArgumentParser(
        prog="conjured artifact-mv",
        description="Rename a trained artifact and its sidecar manifest as one pair, "
        "rewriting the manifest's artifact field to the destination path.",
    )
    parser.add_argument("src", help="current artifact path")
    parser.add_argument("dst", help="destination artifact path")
    ns = parser.parse_args(list(argv))

    src, dst = Path(ns.src), Path(ns.dst)
    src_sidecar, dst_sidecar = sidecar_path(src), sidecar_path(dst)
    if not src.exists():
        print(f"conjured artifact-mv: artifact {src} does not exist", file=sys.stderr)
        return 1
    if not src_sidecar.exists():
        print(
            f"conjured artifact-mv: {src} carries no sidecar ({src_sidecar.name}) — "
            "nothing to keep in sync; write one with conjured artifact-tag first",
            file=sys.stderr,
        )
        return 1
    if dst.exists() or dst_sidecar.exists():
        print(
            f"conjured artifact-mv: destination {dst if dst.exists() else dst_sidecar} "
            "already exists — an existing file is never silently overwritten",
            file=sys.stderr,
        )
        return 1
    load_manifest(src_sidecar)  # validate BEFORE touching files — a malformed sidecar fails loud
    data = tomllib.loads(src_sidecar.read_text(encoding="utf-8"))
    data["manifest"]["artifact"] = ns.dst

    def _render_table(name: str, table: dict) -> list[str]:
        lines = [f"[{name}]"]
        nested: list[tuple[str, dict]] = []
        for key, value in table.items():
            rendered_key = (
                key if key.replace("_", "").isalnum() and not key[0].isdigit()
                else _toml_value(key, where=name)
            )
            if isinstance(value, dict):
                nested.append((f"{name}.{rendered_key}", value))
            else:
                lines.append(f"{rendered_key} = {_toml_value(value, where=f'{name}.{key}')}")
        for nested_name, nested_table in nested:
            lines.append("")
            lines.extend(_render_table(nested_name, nested_table))
        return lines

    out: list[str] = [
        "# Trained-artifact manifest — pair-renamed by `conjured artifact-mv`",
    ]
    for section, table in data.items():
        out.append("")
        out.extend(_render_table(section, table))
    src.rename(dst)
    src_sidecar.write_text("\n".join(out) + "\n", encoding="utf-8")
    src_sidecar.rename(dst_sidecar)
    print(f"moved {src} -> {dst} (sidecar kept in sync)", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Umbrella dispatch
# ---------------------------------------------------------------------------

#: The realized subcommands — the three canon-named tools (pipeline/reference.md
#: § Extraction surface + § The manifest CLI pair). The closed set is enumerated in the
#: unknown-subcommand diagnostic so a typo fails loud listing what exists.
_SUBCOMMANDS = {
    "derivables": _derivables_main,
    "artifact-tag": _artifact_tag_main,
    "artifact-mv": _artifact_mv_main,
}


def main(argv: Sequence[str] | None = None) -> int:
    """The umbrella ``conjured`` entry point. Dispatches the first argument to a subcommand;
    an unknown (or missing) subcommand fails loud listing the known set. Maps the engine's real
    errors (``ConjuredError`` — a ``ContractViolation`` from a mis-typed / invalid declaration —
    and ``OSError`` from a missing file) to a non-zero process exit; success is ``0``. The errors
    propagate unchanged up to here — the CLI boundary is where a structured error becomes an exit
    code, never where it is swallowed or degraded."""
    args = list(sys.argv[1:] if argv is None else argv)
    known = ", ".join(sorted(_SUBCOMMANDS))
    if not args:
        print(f"conjured: a subcommand is required (known: {known})", file=sys.stderr)
        return 2
    name, rest = args[0], args[1:]
    handler = _SUBCOMMANDS.get(name)
    if handler is None:
        print(
            f"conjured: unknown subcommand {name!r} (known: {known})", file=sys.stderr
        )
        return 2
    try:
        return handler(rest)
    except (ConjuredError, OSError) as exc:
        print(f"conjured {name}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - module execution entry
    sys.exit(main())
