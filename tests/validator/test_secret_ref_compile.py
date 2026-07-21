"""The secret-reference compose-time seals — R-deployment-003's shape-early half
(deployment/reference.md § Secret references) plus the ``secret_ref`` token's placement
law. Each test is the exact adversary the guarantee defends against — RED if the
mechanism is removed (guarantees-need-a-failing-case-test):

- a **raw credential pasted** where a reference belongs rejects at compose
  (``secret-ref-malformed``) — never forwarded to a dispatch;
- an **unknown scheme** rejects (``secret-ref-scheme-unknown``) — no fallback store;
- a **dotted consumer scheme that does not import** rejects (``secret-resolver-invalid``);
- both transport arms run the same shape check (service blocks AND hook blocks);
- the ``secret_ref`` token is admissible ONLY in ``[transport_schema]`` — identity /
  config / reads positions reject at declaration load, and a collection-nested
  ``secret_ref`` is unrepresentable by grammar (the no-secrets-in-collections law);
- the spelled ``{ null = true }`` on a nullable ``secret_ref | None`` field is the
  admitted no-credential state (composes green, no shape check to fail)."""

from __future__ import annotations

import pytest

from conjured.errors import Check, ContractViolation, ContractViolationGroup
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

from . import fixtures as F

# The dialogue service-type with a secret_ref-declared credential alongside the required
# endpoint — the canon § Secret references worked-example shape.
SERVICE_TYPE_WITH_SECRET = F.SERVICE_TYPE_DIALOGUE.replace(
    'endpoint = { type = "str" }',
    'endpoint = { type = "str" }\napi_key_ref = { type = "secret_ref | None", nullable = true }',
)

DEPLOYMENT_HEAD = '[training_contract]\nintegrity_enforcement=true\n'


def _setup(service_type_toml: str = SERVICE_TYPE_WITH_SECRET):
    reg = DeclarationRegistry()
    reg.add_service_type(loads(service_type_toml, "service_type", file_path="st.toml"))
    reg.add_handler("acme.ctx", loads(F.TRANSFORM_CTX, "handler", file_path="ctx.toml"))
    reg.add_handler("transform.formatter", loads(F.TRANSFORM_FORMATTER, "handler", file_path="fmt.toml"))
    reg.add_composition("trainables/dialogue.toml", loads(F.TRAINABLE_COMPOSITION, "composition", file_path="c.toml"))
    pipeline = loads(F.PIPELINE_WITH_COMPOSITION, "pipeline", file_path="p.toml")
    return reg, pipeline


def _violations(reg, pipeline, deployment_toml: str) -> list[ContractViolation]:
    deployment = loads(deployment_toml, "deployment", file_path="d.toml")
    try:
        compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")
    except ContractViolationGroup as group:
        return list(group.violations)
    except ContractViolation as cv:
        return [cv]
    return []


def _transport(api_key_value: str) -> str:
    return f'[transport.llm]\nendpoint="https://x"\napi_key_ref={api_key_value}\n' + DEPLOYMENT_HEAD


# ---------------------------------------------------------------------------
# The service-transport arm — shape early, per value
# ---------------------------------------------------------------------------


# verifies: secret-ref-malformed
def test_raw_credential_pasted_where_a_reference_belongs_rejects():
    """The mistake the mechanism exists to catch: a raw bearer value in the deployment
    TOML rejects at pipeline-declaration load — it never reaches a dispatch, and the
    diagnostic routes to the reference forms."""
    reg, pipeline = _setup()
    found = _violations(reg, pipeline, _transport('"sk-raw-bearer-token"'))
    assert any(v.check is Check.SECRET_REF_MALFORMED for v in found), found


# verifies: secret-ref-scheme-unknown
def test_unknown_bare_scheme_rejects():
    """A well-formed reference naming a bare scheme outside the closed built-in set —
    no fallback store, no guess (the Airflow-style store-to-store fallback chain is the
    named anti-pattern this forecloses)."""
    reg, pipeline = _setup()
    found = _violations(reg, pipeline, _transport('"[vault]prod/llm"'))
    assert any(v.check is Check.SECRET_REF_SCHEME_UNKNOWN for v in found), found


# verifies: secret-resolver-invalid
def test_unimportable_consumer_resolver_rejects():
    """A dotted (consumer) scheme must import to a callable AT LOAD — a deployment naming
    a store integration that is not installed fails before any traffic."""
    reg, pipeline = _setup()
    found = _violations(reg, pipeline, _transport('"[no_such_pkg.resolver]prod/llm"'))
    assert any(v.check is Check.SECRET_RESOLVER_INVALID for v in found), found


def test_well_formed_env_reference_composes_green(monkeypatch):
    """The happy path: a built-in-scheme reference composes with NO violations — and
    without fetching (the referenced variable being UNSET at compose proves the engine
    never fetches; availability is dispatch-time)."""
    monkeypatch.delenv("LLM_PROD_KEY", raising=False)
    reg, pipeline = _setup()
    assert _violations(reg, pipeline, _transport('"[env]LLM_PROD_KEY"')) == []


def test_spelled_null_is_the_admitted_no_credential_state():
    reg, pipeline = _setup()
    assert _violations(reg, pipeline, _transport("{null=true}")) == []


# ---------------------------------------------------------------------------
# The hook-transport arm — the same shape check (never the model generator)
# ---------------------------------------------------------------------------

HOOK_WITH_SECRET = F.HOOK_LOG.replace(
    'path = { type = "str" }',
    'path = { type = "str" }\ntoken_ref = { type = "secret_ref" }',
)


def _hook_setup():
    reg, pipeline, _ = F.build_base()
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_LLM, "service_type", file_path="st.toml"))
    reg.add_handler("acme.normalize", loads(F.TRANSFORM_NORMALIZE, "handler", file_path="h.norm.toml"))
    reg.add_handler("acme.respond", loads(F.SERVICE_RESPOND, "handler", file_path="h.respond.toml"))
    reg.add_handler("acme.log", loads(HOOK_WITH_SECRET, "handler", file_path="h.log.toml"))
    pipeline = loads(F.PIPELINE, "pipeline", file_path="p.toml")
    return reg, pipeline


def _hook_deployment(token_value: str) -> str:
    return (
        '[transport.llm]\nendpoint="https://llm/v1"\n'
        f'[hook_transport."acme.log"]\npath="/var/log/x.jsonl"\ntoken_ref={token_value}\n'
        + DEPLOYMENT_HEAD
    )


# verifies: secret-ref-malformed
def test_hook_transport_secret_ref_gets_the_same_shape_check():
    reg, pipeline = _hook_setup()
    found = _violations(reg, pipeline, _hook_deployment('"sk-raw-token"'))
    assert any(
        v.check is Check.SECRET_REF_MALFORMED and "acme.log" in (v.section_path or "")
        for v in found
    ), found


def test_hook_transport_secret_ref_composes_green(monkeypatch):
    monkeypatch.delenv("WEBHOOK_TOKEN", raising=False)  # compose never fetches
    reg, pipeline = _hook_setup()
    assert _violations(reg, pipeline, _hook_deployment('"[env]WEBHOOK_TOKEN"')) == []


# ---------------------------------------------------------------------------
# The token's placement law — transport_schema only, never in a collection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("section", ["identity_schema", "config_schema"])
def test_secret_ref_outside_transport_schema_rejects(section):
    """A secret has no business in hashed identity/config — the token is inadmissible
    there at declaration load (the same closed-grammar rejection the `table` token gets
    outside config)."""
    bad = F.SERVICE_TYPE_DIALOGUE.replace(
        f"[{section}]", f"[{section}]\nleak_ref = {{ type = \"secret_ref\" }}"
    )
    with pytest.raises((ContractViolation, ContractViolationGroup)) as exc_info:
        loads(bad, "service_type", file_path="st.toml")
    assert "secret_ref" in str(exc_info.value)


def test_secret_ref_in_handler_reads_rejects():
    bad = F.TRANSFORM_CTX.replace("[reads]", "[reads]\nleak_ref = { type = \"secret_ref\" }")
    with pytest.raises((ContractViolation, ContractViolationGroup)) as exc_info:
        loads(bad, "handler", file_path="ctx.toml")
    assert "secret_ref" in str(exc_info.value)


def test_secret_ref_nested_in_a_collection_is_unrepresentable():
    """The no-secrets-in-collections law is grammar, not detection: even in
    [transport_schema], `dict[str, secret_ref]` does not parse — a credential gets its
    own declared line."""
    bad = F.SERVICE_TYPE_DIALOGUE.replace(
        'endpoint = { type = "str" }',
        'endpoint = { type = "str" }\nheaders = { type = "dict[str, secret_ref]" }',
    )
    with pytest.raises((ContractViolation, ContractViolationGroup)) as exc_info:
        loads(bad, "service_type", file_path="st.toml")
    assert "secret_ref" in str(exc_info.value)
