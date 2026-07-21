"""The blessed secret-reference resolver — the ONE home for the ``[scheme]payload``
grammar and its resolution (``conjured/docs/components/deployment/reference.md``
§ Secret references, R-deployment-003).

A ``secret_ref``-declared transport field's deployment value is a **secret reference** —
an instruction for *where* the consuming implementation fetches a credential at dispatch,
never the credential itself. The engine validates the reference's **shape** at
pipeline-declaration load (``validator/compile.py`` calls the pure :func:`parse_secret_ref`
/ :func:`classify_scheme` / :func:`load_consumer_resolver` halves below) and forwards the
string opaque; **the engine never fetches**. Resolution happens HERE, called by the
consuming service implementation or hook body inside its own dispatch frame — so a resolved
secret value exists only in that frame, never in engine state, channels, capture, events,
or error text (the containment property; no message this module raises embeds a fetched
value).

The failure split (§ Secret references — shape early, availability late):

- **shape** problems — a malformed reference, an unknown scheme, an unimportable consumer
  resolver — are compose-time ``ContractViolation``\\ s (``secret-ref-malformed`` /
  ``secret-ref-scheme-unknown`` / ``secret-resolver-invalid``), raised by the compile-time
  caller wrapping this module's pure parse errors;
- **store** problems — an unset/empty environment variable, a missing/unreadable/empty
  file, a consumer resolver raising — are dispatch-time :class:`SecretResolutionError`,
  riding raw through the dispatch surface to the runner's ``PipelineFailure`` wrap exactly
  as the wire errors do (R-handler-002: no failure maps to a default).

Schemes (§ Secret references — the scheme set): the **bare built-ins** ``env`` / ``file``
(the closed zero-dependency set), and a **namespaced (dotted) scheme**, which IS the
qualified name of a consumer resolver callable ``(payload: str) -> str`` — the same
bare-is-engine-owned / dotted-is-third-party split the validator-keyword grammar uses
(R-handler-012), so there is no registry section to configure and nothing for a built-in
to be shadowed by. Resolution is **per-dispatch** (env/file re-read every call — rotation
needs no engine support; a slow consumer resolver may memoize internally, its business).
"""

from __future__ import annotations

import importlib
import os
import re
from typing import Callable

__all__ = [
    "SecretResolutionError",
    "parse_secret_ref",
    "classify_scheme",
    "load_consumer_resolver",
    "resolve_secret_ref",
    "BUILTIN_SCHEMES",
]


class SecretResolutionError(RuntimeError):
    """A dispatch-time secret-store failure — an unset or empty environment variable, a
    missing/unreadable/empty secret file, a consumer resolver raising or returning a
    non-string. Rides raw through the dispatch surface (the runner wraps it as
    ``PipelineFailure``, the same contract as the wire errors). Never carries a fetched
    secret value in its message — only the reference and the store-side reason."""


#: The whole-value reference form: ``[scheme]payload``, split at the FIRST ``]``; payload
#: verbatim (no trimming, any characters — absolute paths, ARNs, URLs pass untouched).
_REF_RE = re.compile(r"^\[([^\]]+)\](.+)$", re.DOTALL)
#: A bare (engine-owned) scheme: lowercase identifier. The closed built-in set below is the
#: only bare vocabulary; a bare scheme outside it is `secret-ref-scheme-unknown`.
_BARE_SCHEME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
#: A namespaced (dotted) scheme: a consumer resolver's qualified name — dotted Python
#: identifiers, at least one dot (the bare/dotted split mirrors the validator-keyword
#: grammar's third-party arm).
_QUALIFIED_SCHEME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")


def parse_secret_ref(value: object) -> tuple[str, str]:
    """The pure grammar half: a supplied value → ``(scheme, payload)``, or ``ValueError``
    with the precise malformation (the compile-time caller wraps it as
    ``secret-ref-malformed``; the dispatch-time caller as :class:`SecretResolutionError` —
    unreachable there when compose validated, kept for direct-API consumers). The payload
    is verbatim — significant whitespace included."""
    if not isinstance(value, str):
        raise ValueError(
            f"a secret reference is a '[scheme]payload' string; got {type(value).__name__}"
        )
    match = _REF_RE.match(value)
    if match is None:
        raise ValueError(
            f"{value!r} is not a '[scheme]payload' secret reference — the whole value must "
            "be the reference: a leading [scheme] tag, then the non-empty verbatim payload "
            "(e.g. \"[env]LLM_PROD_KEY\", \"[file]/run/secrets/llm\")"
        )
    return match.group(1), match.group(2)


#: The closed built-in scheme set — zero-dependency fetchers (§ Secret references).
#: ``env``: the payload names an environment variable, verbatim (no case-folding).
#: ``file``: the payload is a path; the file's UTF-8 text with exactly one trailing
#: newline stripped (mounted secret files are conventionally newline-terminated; a
#: trailing newline inside a credential is never intended).
BUILTIN_SCHEMES: tuple[str, ...] = ("env", "file")


def classify_scheme(scheme: str) -> "str | None":
    """``"builtin"`` for a member of the closed built-in set, ``"consumer"`` for a
    namespaced (dotted) qualified name, ``None`` for anything else (the compile-time
    caller's ``secret-ref-scheme-unknown``)."""
    if scheme in BUILTIN_SCHEMES:
        return "builtin"
    if _QUALIFIED_SCHEME_RE.match(scheme):
        return "consumer"
    return None


def load_consumer_resolver(qualified: str) -> Callable[[str], str]:
    """Import a dotted scheme's consumer resolver — ``pkg.module.attr`` → the callable.
    Raises ``ImportError`` / ``AttributeError`` / ``TypeError`` raw with the qualified name
    (the compile-time caller wraps as ``secret-resolver-invalid``). Splits at the LAST dot:
    the module path imports, the final segment is the callable attribute."""
    module_path, _, attr = qualified.rpartition(".")
    module = importlib.import_module(module_path)
    resolver = getattr(module, attr)
    if not callable(resolver):
        raise TypeError(
            f"secret-reference scheme '{qualified}' names a non-callable "
            f"({type(resolver).__name__}) — a consumer resolver is a callable "
            "(payload: str) -> str"
        )
    return resolver


def _fetch_env(payload: str, ref: str) -> str:
    token = os.environ.get(payload)
    if not token:
        state = "unset" if payload not in os.environ else "empty"
        raise SecretResolutionError(
            f"secret reference {ref!r} resolves to environment variable ${payload}, which is "
            f"{state} — provision the secret in the deployment environment (the reference "
            "indirection keeps the raw value out of the declaration files)"
        )
    return token


def _fetch_file(payload: str, ref: str) -> str:
    try:
        with open(payload, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise SecretResolutionError(
            f"secret reference {ref!r} names file {payload!r}, which cannot be read "
            f"({exc.__class__.__name__}: {exc}) — mount the secret in the deployment "
            "environment"
        ) from exc
    except UnicodeDecodeError as exc:
        # An undecodable file is the same store-side "unreadable" class as an OSError —
        # wrapped into the structured error, never a raw UnicodeDecodeError (whose
        # .object attribute would carry the file's raw bytes through engine frames,
        # against the containment posture: the message names the reference and the
        # failure CLASS, never content).
        raise SecretResolutionError(
            f"secret reference {ref!r} names file {payload!r}, which is not valid "
            "UTF-8 — a [file] secret is the file's UTF-8 text (deployment reference "
            "§ Secret references); re-mount the secret as UTF-8 text"
        ) from None  # deliberately unchained: the cause carries raw file bytes
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if not text:
        raise SecretResolutionError(
            f"secret reference {ref!r} names file {payload!r}, which is empty — an empty "
            "credential is never valid"
        )
    return text


def resolve_secret_ref(ref: "str | None") -> "str | None":
    """The dispatch-time resolution the consuming implementation calls with its
    ``secret_ref``-declared transport value: ``None`` (the delivered ``{ null = true }``
    no-credential state) → ``None``; otherwise parse → route by scheme → fetch, failing
    loud as :class:`SecretResolutionError` on every store-side problem. Pure function of
    the reference plus the named store — nothing here reads ambient configuration, and
    nothing resolves at import or compose time."""
    if ref is None:
        return None
    try:
        scheme, payload = parse_secret_ref(ref)
    except ValueError as exc:
        # Compose validates shape for engine-composed transport, so this arm serves
        # direct-API consumers calling the resolver on their own values.
        raise SecretResolutionError(str(exc)) from exc
    kind = classify_scheme(scheme)
    if kind == "builtin":
        return _fetch_env(payload, ref) if scheme == "env" else _fetch_file(payload, ref)
    if kind == "consumer":
        try:
            resolver = load_consumer_resolver(scheme)
        except (ImportError, AttributeError, TypeError) as exc:
            raise SecretResolutionError(
                f"secret reference {ref!r} names consumer resolver '{scheme}', which does "
                f"not load ({exc})"
            ) from exc
        try:
            token = resolver(payload)
        except Exception as exc:
            # The consumer store failed — surface it raw-in-cause, never substitute a
            # default (R-handler-002). The message carries the reference, never a value.
            raise SecretResolutionError(
                f"secret reference {ref!r}: consumer resolver '{scheme}' raised "
                f"{exc.__class__.__name__} — the store-side failure surfaces loud"
            ) from exc
        if not isinstance(token, str) or not token:
            raise SecretResolutionError(
                f"secret reference {ref!r}: consumer resolver '{scheme}' returned "
                f"{'an empty string' if token == '' else 'a non-string'} — a resolver "
                "returns the non-empty secret string or raises"
            )
        return token
    raise SecretResolutionError(
        f"secret reference {ref!r} names unknown scheme '{scheme}' — built-ins are "
        f"{list(BUILTIN_SCHEMES)}; a consumer store is a namespaced (dotted) resolver "
        "qualified name"
    )
