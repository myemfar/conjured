"""Tests for the ``conjured`` umbrella CLI + its ``derivables`` subcommand (``conjured.cli``).

Exercises the path→registry assembly the CLI owns and the error-path contract
(``3-code.PROMPT.md`` acceptance 5): a missing file, a kind-mismatched file, and a
compile-invalid pipeline each surface their REAL structured error, and exit codes distinguish
success from failure. The CLI's bundle must equal the library's for the same declarations.
"""

from __future__ import annotations

import json

import pytest

from conjured.cli import build_bundle, main
from conjured.derivables import extract, serialize
from conjured.errors import ContractViolation
from conjured.validator import DeclarationRegistry, loads

from . import _fixtures as F


def _write(tmp_path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _trainable_files(tmp_path) -> dict[str, str]:
    """Write the trainable-composition fixture set to disk; return the flag paths."""
    return {
        "pipeline": _write(tmp_path, "p.toml", F.PIPELINE_WITH_COMPOSITION),
        "service_type": _write(tmp_path, "st.toml", F.SERVICE_TYPE_DIALOGUE),
        "ctx": _write(tmp_path, "ctx.toml", F.TRANSFORM_CTX),
        "formatter": _write(tmp_path, "fmt.toml", F.TRANSFORM_FORMATTER),
        "composition": _write(tmp_path, "c.toml", F.TRAINABLE_COMPOSITION),
    }


def _derivables_argv(files: dict[str, str], *extra: str) -> list[str]:
    return [
        "derivables", files["pipeline"],
        "--service-type", files["service_type"],
        "--handler", f"acme.ctx={files['ctx']}",
        "--handler", f"transform.formatter={files['formatter']}",
        "--composition", f"trainables/dialogue.toml={files['composition']}",
        *extra,
    ]


# --- Happy path: assembly + stdout / file output --------------------------------------------


def test_cli_emits_bundle_to_stdout(tmp_path, capsys):
    files = _trainable_files(tmp_path)
    rc = main(_derivables_argv(files))
    assert rc == 0
    out = capsys.readouterr().out
    bundle = json.loads(out)
    assert bundle["bundle_format"] == 1
    assert "dialogue_training" in bundle["trainables"]


def test_cli_writes_bundle_to_output_file(tmp_path, capsys):
    files = _trainable_files(tmp_path)
    out_path = tmp_path / "bundle.json"
    rc = main(_derivables_argv(files, "-o", str(out_path)))
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing to stdout when -o is given
    # The provenance-pin report (pipeline/reference.md § generator_info: the CLI reports it
    # at extraction): the stderr line carries the bundle_hash of the EXACT artifact written.
    # RED if the report is dropped or hashes anything other than the written bytes.
    from conjured.derivables import bundle_hash

    expected = bundle_hash(out_path.read_text(encoding="utf-8"))
    assert f"derivables_bundle_hash: {expected}" in captured.err
    bundle = json.loads(out_path.read_text(encoding="utf-8"))
    assert bundle["trainables"]["dialogue_training"]["service_metadata"]["description"]


def test_cli_output_file_has_lf_line_endings_only(tmp_path, capsys):
    # Byte-level determinism through the shipped CLI (refuter finding 1): the emitted artifact
    # must be the exact UTF-8 bytes serialize() produced — LF only — so the same declarations +
    # engine version produce a byte-identical bundle on every platform. Text-mode writing would
    # translate '\n' to os.linesep (CRLF on Windows), diverging from a Linux extraction.
    files = _trainable_files(tmp_path)
    out_path = tmp_path / "bundle.json"
    main(_derivables_argv(files, "-o", str(out_path)))
    raw = out_path.read_bytes()
    assert b"\r" not in raw  # no CR — pure LF regardless of host OS
    assert raw.endswith(b"\n")


def test_cli_bundle_matches_the_library_path(tmp_path):
    # The CLI's assembled-from-disk bundle equals the library's in-memory bundle for the same
    # declarations (the CLI is a thin wrapper; assembly must not perturb the result). Compare
    # everything except the engine-version provenance (the CLI stamps the live version).
    files = _trainable_files(tmp_path)
    cli_text = build_bundle(
        files["pipeline"],
        handlers=[("acme.ctx", files["ctx"]), ("transform.formatter", files["formatter"])],
        compositions=[("trainables/dialogue.toml", files["composition"])],
        service_types=[files["service_type"]],
    )
    reg, pipeline = F.build_trainable()
    lib_bundle = extract(pipeline, reg, conjured_version="ignored")
    # Round-trip both through serialize→parse so they are JSON-normalized before comparison,
    # then drop the version provenance (the CLI stamps the live engine version).
    cli_bundle = json.loads(cli_text)
    lib_norm = json.loads(serialize(lib_bundle))
    cli_bundle.pop("conjured_version")
    lib_norm.pop("conjured_version")
    assert cli_bundle == lib_norm


# --- Error paths (acceptance 5): real errors, distinguishing exit codes ----------------------


def test_missing_file_raises_oserror_and_exits_nonzero(tmp_path, capsys):
    files = _trainable_files(tmp_path)
    # Point the pipeline arg at a nonexistent file.
    argv = _derivables_argv({**files, "pipeline": str(tmp_path / "nope.toml")})
    # build_bundle raises the real OSError unchanged...
    with pytest.raises(OSError):
        build_bundle(
            str(tmp_path / "nope.toml"),
            service_types=[files["service_type"]],
        )
    # ...and main maps it to a non-zero exit (never a traceback dump / swallow).
    rc = main(argv)
    assert rc == 1
    assert "conjured derivables:" in capsys.readouterr().err


def test_kind_mismatched_file_raises_contract_violation(tmp_path, capsys):
    files = _trainable_files(tmp_path)
    # Pass a HANDLER declaration where a service-type is expected — loads(..., "service_type")
    # fails loud with its real ContractViolation (no file-kind sniffing rescues it).
    with pytest.raises(ContractViolation):
        build_bundle(files["pipeline"], service_types=[files["ctx"]])
    rc = main([
        "derivables", files["pipeline"],
        "--service-type", files["ctx"],  # a transform handler, mis-typed
    ])
    assert rc == 1
    assert "conjured derivables:" in capsys.readouterr().err


def test_compile_invalid_pipeline_exits_nonzero(tmp_path, capsys):
    files = _trainable_files(tmp_path)
    # Omit the required composition/handlers → the pipeline references unresolved nodes →
    # compile raises ContractViolation inside extract.
    rc = main(["derivables", files["pipeline"], "--service-type", files["service_type"]])
    assert rc == 1
    assert "conjured derivables:" in capsys.readouterr().err


def test_missing_external_binding_file_raises_contract_violation(tmp_path):
    # A pipeline node binding names an external file that does not exist → the resolution pass
    # fails loud with a ContractViolation (never silently hashes a path).
    st = _write(tmp_path, "st.toml", F.SERVICE_TYPE_LLM)
    handler = _write(tmp_path, "norm.toml", F.TRANSFORM_NORMALIZE)
    pipeline = _write(tmp_path, "p.toml",
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.normalize"\n'
        'bindings = { config = { file = "does_not_exist.toml" } }\n'
        '[inputs]\nplayer_input={type="str"}\n[outputs]\nnormalized_input={type="str"}\n',
    )
    with pytest.raises(ContractViolation) as exc:
        build_bundle(pipeline, handlers=[("acme.normalize", handler)], service_types=[st])
    assert exc.value.check.value == "external-binding-content-unsupported"


# --- Umbrella dispatch --------------------------------------------------------------------


def test_unknown_subcommand_exits_two_and_lists_known(capsys):
    rc = main(["not-a-subcommand"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown subcommand" in err
    assert "derivables" in err  # the known set is listed


def test_no_subcommand_exits_two(capsys):
    rc = main([])
    assert rc == 2
    assert "a subcommand is required" in capsys.readouterr().err


def test_malformed_named_flag_is_a_usage_error(tmp_path):
    files = _trainable_files(tmp_path)
    # A --handler value with no '=' is a usage error → argparse parser.error → SystemExit(2).
    with pytest.raises(SystemExit) as exc:
        main(["derivables", files["pipeline"], "--handler", "no_equals_here"])
    assert exc.value.code == 2
