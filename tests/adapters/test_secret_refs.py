"""``conjured.adapters.secret_refs`` — the blessed secret-reference resolver
(deployment/reference.md § Secret references, R-deployment-003): the ``[scheme]payload``
grammar, both built-in stores, the dotted consumer-resolver arm, and every fail-loud
store path. Env/file fetching uses the REAL stores (monkeypatched env, ``tmp_path``
files) — cheap real I/O, never an engine mock; the consumer arm imports a REAL module
written to ``tmp_path`` through the real ``importlib`` path."""

from __future__ import annotations

import pytest

from conjured.adapters.secret_refs import (
    SecretResolutionError,
    classify_scheme,
    parse_secret_ref,
    resolve_secret_ref,
)

# ---------------------------------------------------------------------------
# The grammar — parse_secret_ref (pure)
# ---------------------------------------------------------------------------


def test_parse_splits_scheme_and_verbatim_payload():
    assert parse_secret_ref("[env]LLM_PROD_KEY") == ("env", "LLM_PROD_KEY")
    # Payload is verbatim — separators, dots, colons, even `]` pass untouched
    # (split at the FIRST `]`), so paths/ARNs/URLs need no escaping.
    assert parse_secret_ref("[file]/run/secrets/llm") == ("file", "/run/secrets/llm")
    assert parse_secret_ref("[aws.rsl]arn:aws:x:y[0]") == ("aws.rsl", "arn:aws:x:y[0]")


@pytest.mark.parametrize("bad", [
    "sk-raw-bearer-token",   # a pasted raw credential — the mistake the sigil catches
    "env:LLM_PROD",          # no bracket tag
    "[env]",                 # empty payload
    "[]X",                   # empty scheme
    "",                      # empty value
])
def test_parse_rejects_non_references(bad):
    with pytest.raises(ValueError, match=r"\[scheme\]payload|secret reference"):
        parse_secret_ref(bad)


def test_parse_rejects_non_strings():
    with pytest.raises(ValueError, match="got int"):
        parse_secret_ref(5)


def test_classify_scheme_three_ways():
    assert classify_scheme("env") == "builtin"
    assert classify_scheme("file") == "builtin"
    assert classify_scheme("acme_secrets.vault_resolver") == "consumer"  # dotted = qualified
    assert classify_scheme("vault") is None       # bare non-built-in — unknown
    assert classify_scheme("ENV") is None         # built-ins are exact lowercase


# ---------------------------------------------------------------------------
# The env store
# ---------------------------------------------------------------------------


def test_none_is_the_no_credential_state():
    # The delivered `{ null = true }` -> None -> None (the adapter sends no header).
    assert resolve_secret_ref(None) is None


def test_env_resolves_verbatim_name(monkeypatch):
    # The payload IS the variable name — no case-folding, no normalization magic.
    monkeypatch.setenv("Llm_Prod.key", "sk-secret")
    assert resolve_secret_ref("[env]Llm_Prod.key") == "sk-secret"


@pytest.mark.parametrize("state", ["unset", "empty"])
def test_env_unset_or_empty_fails_loud(monkeypatch, state):
    # A referenced-but-unprovisioned secret is a deployment error, never a silent
    # unauthenticated request — and the error names the variable and the state.
    if state == "unset":
        monkeypatch.delenv("LLM_PROD", raising=False)
    else:
        monkeypatch.setenv("LLM_PROD", "")
    with pytest.raises(SecretResolutionError, match=f"LLM_PROD.*{state}"):
        resolve_secret_ref("[env]LLM_PROD")


# ---------------------------------------------------------------------------
# The file store
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("sk-from-file", "sk-from-file"),
    ("sk-from-file\n", "sk-from-file"),      # exactly one trailing newline stripped
    ("sk-from-file\r\n", "sk-from-file"),    # CRLF sibling
    ("sk-from-file\n\n", "sk-from-file\n"),  # ONE newline — a second is content
])
def test_file_resolves_with_one_trailing_newline_stripped(tmp_path, raw, expected):
    secret = tmp_path / "llm_token"
    secret.write_text(raw, encoding="utf-8", newline="")
    assert resolve_secret_ref(f"[file]{secret}") == expected


def test_file_missing_fails_loud(tmp_path):
    with pytest.raises(SecretResolutionError, match="cannot be read"):
        resolve_secret_ref(f"[file]{tmp_path / 'absent'}")


def test_file_empty_fails_loud(tmp_path):
    secret = tmp_path / "empty"
    secret.write_text("\n", encoding="utf-8", newline="")
    with pytest.raises(SecretResolutionError, match="empty"):
        resolve_secret_ref(f"[file]{secret}")


# ---------------------------------------------------------------------------
# The consumer-resolver arm (dotted scheme = qualified callable) + unknown schemes
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_store(tmp_path, monkeypatch):
    """A REAL importable consumer-store module — resolution goes through the same
    importlib path a deployed resolver would."""
    (tmp_path / "fake_secret_store.py").write_text(
        "def resolve(payload):\n    return 'tok-' + payload\n"
        "def boom(payload):\n    raise RuntimeError('store down: ' + payload)\n"
        "def bad(payload):\n    return 7\n"
        "NOT_CALLABLE = 'x'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return "fake_secret_store"


def test_consumer_resolver_resolves(fake_store):
    assert resolve_secret_ref(f"[{fake_store}.resolve]prod/llm") == "tok-prod/llm"


def test_consumer_resolver_raise_surfaces_loud_without_echoing_values(fake_store):
    # The store-side failure surfaces raw-in-cause; the message names the reference and
    # the exception class, never a fetched value (the containment property).
    with pytest.raises(SecretResolutionError, match="RuntimeError") as exc_info:
        resolve_secret_ref(f"[{fake_store}.boom]prod/llm")
    assert "tok-" not in str(exc_info.value)


def test_consumer_resolver_non_string_return_fails_loud(fake_store):
    with pytest.raises(SecretResolutionError, match="non-string"):
        resolve_secret_ref(f"[{fake_store}.bad]prod/llm")


def test_consumer_resolver_non_callable_fails_loud(fake_store):
    with pytest.raises(SecretResolutionError, match="does not load"):
        resolve_secret_ref(f"[{fake_store}.NOT_CALLABLE]prod/llm")


def test_unknown_scheme_fails_loud():
    # A bare scheme outside the closed built-in set: no fallback store, no guess.
    with pytest.raises(SecretResolutionError, match="unknown scheme 'vault'"):
        resolve_secret_ref("[vault]prod/llm")


def test_malformed_reference_fails_loud_at_resolve_for_direct_api_use():
    # Engine-composed transport is shape-checked at compose; the resolver still fails a
    # malformed reference loud for direct-API consumers calling it on their own values.
    with pytest.raises(SecretResolutionError, match=r"\[scheme\]payload"):
        resolve_secret_ref("sk-raw-bearer-token")
