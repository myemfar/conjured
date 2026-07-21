"""The file-supplied compile-parameter form — ``<param> = { file = "<path>" }``.

Ground: ``conjured/docs/components/handler/reference.md`` § The ``compile = "..."`` directive
sub-form ("A compile parameter is supplied inline OR from a file") + ``architecture/hash-model.md``
§ What the pipeline-hash absorbs (the compile-directive bullet). The engine reads the named file as
**raw text** at binding resolution, folds that text into the pipeline-hash (distinct from inline —
the engine never parses compiler content), and passes the text to the compiler as ``<param>`` (the
compiler parses it: ``json_schema`` reads it as JSON; ``jinja`` / ``regex`` use it directly).

This is the SAME ``{ file }`` external-file form a binding value uses, REUSED at the compile-param
level (the §9 reuse): the ``_as_file_ref`` parse classifier + ``FilePathBindingValue`` IR shape +
the ``_read_external_file`` read/fail-loud seam are shared; the one branch is raw-text-vs-canonicalize.

Same fixture posture as ``test_resolve_compile.py``: real on-disk files (the resolution pass genuinely
reads them). ``jinja`` / ``json_schema`` need the optional ``conjured[compilers]`` backends (dev profile).
"""

from __future__ import annotations

import re

import pytest

from conjured.errors import Check, ContractViolation
from conjured.hasher.hashes import pipeline_hash
from conjured.ir.common import CompileBinding, FilePathBindingValue
from conjured.validator import loads
from conjured.validator.registry import DeclarationRegistry
from conjured.validator.resolve import resolve_compile_param_files
from conjured.validator.resolve_compile import resolve_and_compile


def _handler_toml(binding_block: str) -> str:
    return (
        "[transform]\n[reads]\ni={type=\"str\"}\n[output_schema]\no={type=\"str\"}\n"
        + binding_block
    )


def _registry_with_handler(tmp_path, binding_block: str) -> DeclarationRegistry:
    """A registry holding one transform `acme.h` (declared in `tmp_path/h.toml`), so compile-param
    file paths resolve relative to `tmp_path` (the handler's own directory)."""
    reg = DeclarationRegistry()
    decl = loads(_handler_toml(binding_block), "handler", file_path=str(tmp_path / "h.toml"))
    reg.add_handler("acme.h", decl, toml_path=str(tmp_path / "h.toml"))
    return reg


def _compile_params(reg: DeclarationRegistry):
    """The resolved `(compiler, params)` of `acme.h`'s single compile binding."""
    body = reg.get_handler("acme.h").bindings[0].body
    assert isinstance(body, CompileBinding)
    return body.compiler, body.params


def _pipeline_referencing_h():
    return loads(
        '[meta]\nname="acme.p"\n[[nodes]]\nkind="handler"\nname="acme.h"\n[inputs]\ni={type="str"}\n',
        "pipeline", file_path="p.toml",
    )


# ===========================================================================
# Parse — the file-supplied param is the REUSED `{ file }` IR shape (criterion 4)
# ===========================================================================


def test_compile_param_file_parses_to_the_shared_filepath_binding_value():
    """A `<param> = { file = "<path>" }` compile param parses to the SAME `FilePathBindingValue`
    IR a binding value uses — the §9 reuse (not a `<param>_file` key, not a parallel model).
    RED-on-removal: drop `_as_file_ref` from `_parse_binding` and the param would parse as a raw
    `{"file": ...}` dict (no FilePathBindingValue)."""
    decl = loads(
        _handler_toml('[bindings.greeting]\ncompile="jinja"\nsource={file="t.jinja"}'),
        "handler", file_path="h.toml",
    )
    body = decl.bindings[0].body
    assert isinstance(body, CompileBinding)
    src = body.params["source"]
    assert isinstance(src, FilePathBindingValue)
    assert src.path == "t.jinja"
    assert src.content_hash is None  # unresolved until the resolution pass runs


def test_inline_compile_param_stays_a_plain_value():
    """An inline param (no `{ file }`) is untouched — only the external-file form is recognized."""
    decl = loads(
        _handler_toml('[bindings.greeting]\ncompile="jinja"\nsource="Hi {{ name }}"'),
        "handler", file_path="h.toml",
    )
    body = decl.bindings[0].body
    assert body.params["source"] == "Hi {{ name }}"
    assert not isinstance(body.params["source"], FilePathBindingValue)


def test_malformed_file_mix_on_compile_param_fails_loud():
    """`{ file = "x", other = 1 }` is an ambiguous mix — fail loud at parse (never guess), the same
    strict `{ file }` rule binding values take (rule_id is the handler grammar's R-handler-006)."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            _handler_toml('[bindings.g]\ncompile="jinja"\nsource={file="t.jinja", extra=1}'),
            "handler", file_path="h.toml",
        )
    assert exc.value.check is Check.MALFORMED_DECLARATION


def test_empty_file_path_on_compile_param_rejected_at_parse():
    """`{ file = "" }` names no file — the classifier rejects an empty path string LOUD at parse
    (MALFORMED_DECLARATION), co-located with the other malformed-`{ file }` cases, rather than
    letting it fail late at the read (`IsADirectoryError`/`OSError`) downstream. RED-on-removal:
    drop the `value["file"] == ""` guard in `_as_file_ref` and the empty path parses as a
    FilePathBindingValue that only fails at resolution."""
    with pytest.raises(ContractViolation) as exc:
        loads(
            _handler_toml('[bindings.g]\ncompile="jinja"\nsource={file=""}'),
            "handler", file_path="h.toml",
        )
    assert exc.value.check is Check.MALFORMED_DECLARATION


# ===========================================================================
# Criterion 1 — the file form produces the artifact the inline form would
# ===========================================================================


def test_json_schema_file_param_produces_the_inline_artifact(tmp_path):
    """`json_schema` `schema = { file = ... }`: the engine reads the file as text, the compiler
    parses it as JSON, and the artifact validates exactly as the inline object would."""
    import jsonschema  # the [compilers] extra (dev profile) — fail loud if absent

    assert jsonschema
    (tmp_path / "profile.json").write_text(
        '{"type": "object", "required": ["name"]}', encoding="utf-8"
    )
    reg = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="profile.json"}'
    )
    resolve_compile_param_files(reg)
    compiler, params = _compile_params(reg)
    from_file = resolve_and_compile(compiler, params, toml_path="h.toml")
    inline = resolve_and_compile(
        "json_schema", {"schema": {"type": "object", "required": ["name"]}}, toml_path="h.toml"
    )
    for art in (from_file, inline):
        assert art.is_valid({"name": "Ada"})
        assert not art.is_valid({})


def test_jinja_file_param_produces_the_inline_artifact(tmp_path):
    """`jinja` `source = { file = ... }`: the file's text IS the template; the artifact renders
    exactly as the inline template would."""
    import jinja2  # the [compilers] extra — fail loud if absent

    (tmp_path / "greeting.jinja").write_text("Hello, {{ name }}!", encoding="utf-8")
    reg = _registry_with_handler(
        tmp_path, '[bindings.greeting]\ncompile="jinja"\nsource={file="greeting.jinja"}'
    )
    resolve_compile_param_files(reg)
    compiler, params = _compile_params(reg)
    from_file = resolve_and_compile(compiler, params, toml_path="h.toml")
    assert isinstance(from_file, jinja2.Template)
    assert from_file.render(name="Blackwell") == "Hello, Blackwell!"


def test_regex_file_param_uses_text_directly(tmp_path):
    """`regex` uses the file text directly as the pattern (no parse) — small params usually stay
    inline, but the form is uniform across every compiler/param."""
    (tmp_path / "pattern.txt").write_text(r"\[[^\]]+\]", encoding="utf-8")
    reg = _registry_with_handler(
        tmp_path, '[bindings.tags]\ncompile="regex"\npattern={file="pattern.txt"}'
    )
    resolve_compile_param_files(reg)
    compiler, params = _compile_params(reg)
    artifact = resolve_and_compile(compiler, params, toml_path="h.toml")
    assert isinstance(artifact, re.Pattern)
    assert artifact.search("[Tag]") is not None


# ===========================================================================
# Criterion 2 — the text folds; hash-DISTINCT from inline; content edit shifts
# ===========================================================================


def test_file_text_folds_distinct_from_inline(tmp_path):
    """A param's inline value and its file-supplied text are DISTINCT declarations → DIFFERENT
    pipeline-hashes (the engine never parses compiler content, so it cannot canonicalize them to a
    common form — the deliberate divergence from the binding-value `{ file }`). RED-on-removal: fold
    the file text the same way inline folds and these two hashes collide."""
    (tmp_path / "profile.json").write_text(
        '{"type": "object", "required": ["name"]}', encoding="utf-8"
    )
    reg_file = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="profile.json"}'
    )
    resolve_compile_param_files(reg_file)
    file_hash = pipeline_hash(_pipeline_referencing_h(), reg_file)

    reg_inline = _registry_with_handler(
        tmp_path,
        '[bindings.checker]\ncompile="json_schema"\nschema={type="object", required=["name"]}',
    )
    inline_hash = pipeline_hash(_pipeline_referencing_h(), reg_inline)

    assert file_hash != inline_hash  # distinct declarations — NOT the binding-value path-neutrality


def test_jinja_inline_and_file_with_identical_text_are_distinct(tmp_path):
    """The marker's load-bearing case: a `jinja` `source` whose INLINE value is the exact same
    string as the file's content must STILL hash distinctly (inline content vs file-supplied text
    are different declarations). Here inline and file are both `"Hello, {{ name }}!"` — only the
    distinct fold separates them. RED-on-removal: fold the file text bare (as `value.resolved`)
    and these collide, since the inline param is the same string."""
    template = "Hello, {{ name }}!"
    (tmp_path / "greeting.jinja").write_text(template, encoding="utf-8")
    reg_file = _registry_with_handler(
        tmp_path, '[bindings.greeting]\ncompile="jinja"\nsource={file="greeting.jinja"}'
    )
    resolve_compile_param_files(reg_file)
    file_hash = pipeline_hash(_pipeline_referencing_h(), reg_file)

    reg_inline = _registry_with_handler(
        tmp_path, f'[bindings.greeting]\ncompile="jinja"\nsource="{template}"'
    )
    inline_hash = pipeline_hash(_pipeline_referencing_h(), reg_inline)

    assert file_hash != inline_hash


def test_file_supplied_param_cannot_collide_with_a_colliding_inline_wrapper(tmp_path):
    """The disjoint-keyspace seal (`Guarantees need a failing-case test`): a file-supplied param
    folds into a `file_supplied` sub-map structurally disjoint from the inline `params` map, so NO
    inline declaration can reproduce a file-supplied param's fold — not even the contrived inline
    wrapper that mimics the pre-fix marker.

    The adversary: (a) a file-supplied `source` whose file contains exactly `<text>`, and (b) an
    inline `source = { file_supplied_text = "<text>" }` — the exact single-key table the pre-fix fold
    (`params[name] = {"file_supplied_text": value.resolved}`) emitted for the file branch, which an
    inline `canon_value({"file_supplied_text": "<text>"})` reproduced byte-for-byte. Their
    pipeline-hashes MUST differ. RED on the pre-fix code (the two collided into ONE hash, silently —
    two distinct declarations, one pipeline-hash, on the training-contract seal); GREEN once the file
    branch folds in the disjoint `file_supplied` keyspace `canon_value` can never emit. This is the
    inline-WRAPPER arm of test_jinja_inline_and_file_with_identical_text_are_distinct (which covers
    only the inline-bare-string arm)."""
    text = "Hello, {{ name }}!"
    (tmp_path / "greeting.jinja").write_text(text, encoding="utf-8")
    reg_file = _registry_with_handler(
        tmp_path, '[bindings.greeting]\ncompile="jinja"\nsource={file="greeting.jinja"}'
    )
    resolve_compile_param_files(reg_file)
    file_hash = pipeline_hash(_pipeline_referencing_h(), reg_file)

    # The colliding-wrapper inline param: a single-key `{ file_supplied_text = "<text>" }` table —
    # an ORDINARY inline object, NOT the external-file `{ file = ... }` form (so `_as_file_ref`
    # returns None, it flows to the inline branch, and `canon_value` folds it as a plain dict).
    reg_wrapper = _registry_with_handler(
        tmp_path,
        '[bindings.greeting]\ncompile="jinja"\nsource={file_supplied_text="Hello, {{ name }}!"}',
    )
    wrapper_hash = pipeline_hash(_pipeline_referencing_h(), reg_wrapper)

    assert file_hash != wrapper_hash  # disjoint keyspace — no inline wrapper can collide


def test_file_content_edit_shifts_the_pipeline_hash(tmp_path):
    """A content edit to the referenced file shifts the pipeline-hash (the file's text folds, not
    its path). RED-on-removal: fold the path instead of the text and an edit is invisible."""
    schema = tmp_path / "profile.json"
    schema.write_text('{"type": "object", "required": ["name"]}', encoding="utf-8")
    reg_a = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="profile.json"}'
    )
    resolve_compile_param_files(reg_a)
    hash_a = pipeline_hash(_pipeline_referencing_h(), reg_a)

    schema.write_text('{"type": "object", "required": ["name", "age"]}', encoding="utf-8")
    reg_b = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="profile.json"}'
    )
    resolve_compile_param_files(reg_b)
    hash_b = pipeline_hash(_pipeline_referencing_h(), reg_b)

    assert hash_a != hash_b


def test_same_file_content_is_hash_stable(tmp_path):
    """Determinism: the same file content → the same pipeline-hash across independent resolutions."""
    (tmp_path / "profile.json").write_text('{"type": "string"}', encoding="utf-8")
    hashes = []
    for _ in range(2):
        reg = _registry_with_handler(
            tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="profile.json"}'
        )
        resolve_compile_param_files(reg)
        hashes.append(pipeline_hash(_pipeline_referencing_h(), reg))
    assert hashes[0] == hashes[1]


# ===========================================================================
# Criterion 3 — fail loud (unreadable file; unresolved guards; compiler rejects)
# ===========================================================================


def test_missing_compile_param_file_raises_at_resolution(tmp_path):
    """A missing/unreadable file raises `ContractViolation` at the resolution pass (I/O at compose,
    fail loud) — never a path silently hashed, never a dispatch-time surprise."""
    reg = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="does_not_exist.json"}'
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_compile_param_files(reg)
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED
    assert exc.value.rule_id == "R-pipeline-001"


def test_non_utf8_compile_param_file_raises_at_resolution(tmp_path):
    """A compile-param file that is not valid UTF-8 text fails loud at the resolution pass — the
    file is read as TEXT (the compiler parses it), so a non-decodable file is a compose-time
    `ContractViolation`, never a downstream surprise. RED-on-removal: drop the UnicodeDecodeError
    guard in `_resolve_compile_param` and the decode error escapes raw."""
    (tmp_path / "bad.bin").write_bytes(b"\xff\xfe\x00\x80")
    reg = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="bad.bin"}'
    )
    with pytest.raises(ContractViolation) as exc:
        resolve_compile_param_files(reg)
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED


def test_handler_with_file_param_but_no_registered_path_fails_loud(tmp_path):
    """A file-supplied compile param needs the handler's declaration directory to resolve its
    relative path; a handler registered without a path fails loud (never resolve against cwd)."""
    reg = DeclarationRegistry()
    decl = loads(
        _handler_toml('[bindings.checker]\ncompile="json_schema"\nschema={file="profile.json"}'),
        "handler", file_path="h.toml",
    )
    reg.add_handler("acme.h", decl)  # NO toml_path
    with pytest.raises(ContractViolation) as exc:
        resolve_compile_param_files(reg)
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED


def test_unresolved_file_param_raises_at_hash(tmp_path):
    """The hasher's structural backstop: a file-supplied compile param that reaches the hasher
    UNRESOLVED (the resolution pass was not run) raises — the hasher never reads a file or hashes a
    path. RED-on-removal: drop the `content_hash is None` guard in `_canon_binding_decl_body` and a
    `FilePathBindingValue` would reach `canon_value` (or fold a path/None)."""
    (tmp_path / "profile.json").write_text('{"type": "string"}', encoding="utf-8")
    reg = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="profile.json"}'
    )
    # NOTE: resolve_compile_param_files deliberately NOT run.
    with pytest.raises(ContractViolation) as exc:
        pipeline_hash(_pipeline_referencing_h(), reg)
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED


def test_unresolved_file_param_raises_at_resolve_and_compile(tmp_path):
    """The stage-4 backstop: an unresolved file param reaching `resolve_and_compile` fails loud —
    the compiler is fed the file's TEXT, never its path. RED-on-removal: drop the guard in
    `_materialize_params` and a `FilePathBindingValue` object would be passed to the compiler."""
    unresolved = FilePathBindingValue(name="schema", path="profile.json")
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile("json_schema", {"schema": unresolved}, toml_path="h.toml")
    assert exc.value.check is Check.EXTERNAL_BINDING_UNSUPPORTED


def test_file_text_the_compiler_rejects_raises_compile_artifact(tmp_path):
    """Text the compiler then rejects (a file whose JSON is not a valid JSON Schema) raises
    `COMPILE_ARTIFACT` — the same rule the directive's inline failures take. The engine read the
    text and the compiler rejected it; the failure is the compiler's, at compose."""
    import jsonschema

    assert jsonschema
    (tmp_path / "bad.json").write_text('{"type": "not-a-real-type"}', encoding="utf-8")
    reg = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="bad.json"}'
    )
    resolve_compile_param_files(reg)
    compiler, params = _compile_params(reg)
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(compiler, params, toml_path="h.toml")
    assert exc.value.check is Check.COMPILE_ARTIFACT


def test_non_json_text_for_json_schema_raises_compile_artifact(tmp_path):
    """A `json_schema` file whose text is not valid JSON: the compiler's `json.loads` raises → the
    engine maps it to `COMPILE_ARTIFACT` (the compiler's own failure parsing the text)."""
    import jsonschema

    assert jsonschema
    (tmp_path / "bad.json").write_text("this is not json {", encoding="utf-8")
    reg = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="bad.json"}'
    )
    resolve_compile_param_files(reg)
    compiler, params = _compile_params(reg)
    with pytest.raises(ContractViolation) as exc:
        resolve_and_compile(compiler, params, toml_path="h.toml")
    assert exc.value.check is Check.COMPILE_ARTIFACT


# ===========================================================================
# Idempotence + the shared read seam
# ===========================================================================


def test_resolution_is_idempotent(tmp_path):
    """Running the resolution pass twice is a no-op on the second pass (an already-stamped param is
    left as is) — assemble may run over a registry the caller already resolved."""
    (tmp_path / "profile.json").write_text('{"type": "string"}', encoding="utf-8")
    reg = _registry_with_handler(
        tmp_path, '[bindings.checker]\ncompile="json_schema"\nschema={file="profile.json"}'
    )
    resolve_compile_param_files(reg)
    first = reg.get_handler("acme.h").bindings[0].body.params["schema"]
    resolve_compile_param_files(reg)
    second = reg.get_handler("acme.h").bindings[0].body.params["schema"]
    assert first.content_hash is not None and second.content_hash == first.content_hash
    assert second.resolved == '{"type": "string"}'  # the RAW text (not parsed/canonicalized)
