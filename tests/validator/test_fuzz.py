"""Fuzz — the validator is a pure function over a declaration set, so **every** input must
either compile / parse or raise ``ContractViolation``; **no other exception class may escape
uncaught**.

A property-based harness without a third-party dependency (``hypothesis`` is not a declared
extra; pydantic + pytest are): a seeded PRNG mutates valid canon declarations (drop / swap /
inject lines + tokens + random keys) and feeds random byte-strings, asserting the
compile-or-``ContractViolation`` invariant on every one. The seed is fixed so failures are
reproducible (the harness emits the offending input on a breach).
"""

from __future__ import annotations

import random

import pytest

from conjured.errors import ContractViolation, ContractViolationGroup
from conjured.validator import DeclarationRegistry, compile_pipeline, loads

from . import fixtures as F

_SEED = 0xC0FFEE
_DECL_KINDS = ("handler", "service_type", "pipeline", "composition", "deployment")

_SEED_CORPUS = (
    F.TRANSFORM_NORMALIZE, F.SERVICE_RESPOND, F.HOOK_LOG, F.SERVICE_TYPE_LLM,
    F.PIPELINE, F.DEPLOYMENT, F.TRAINABLE_COMPOSITION, F.PIPELINE_WITH_COMPOSITION,
    F.TRANSFORM_CTX, F.SERVICE_TYPE_DIALOGUE,
)

_TOKENS = (
    "str", "int", "float", "bool", "bytes", "list[str]", "dict[str, int]", "tuple[int, str]",
    "str | None", "Literal['a', 'b']", "frobnicate", "list[", "Literal[]", "", "list[list[str]]",
    "Literal['a' 'b']",
)
_KEYS = ("type", "kind", "name", "reads", "output_schema", "service_bindings", "merge", "wat", "{}", "[[")

#: NON-STRING TOML values injected against engine-read keys — the value alphabet the
#: compile-or-ContractViolation guarantee must hold adversarially over (a non-string
#: `type` / `delivery` / `default` / `compile` must raise structured, never leak a raw
#: pydantic ValidationError out of loads()).
_NON_STRING_VALUES = ("3", "true", "false", "3.5", "[1, 2]", "{ x = 1 }", '["a", 3]', "{}")
_VALUE_KEYS = ("type", "delivery", "default", "compile", "name", "integrity_enforcement", "file")


def _mutate(rng: random.Random, text: str) -> str:
    lines = text.splitlines()
    for _ in range(rng.randint(1, 5)):
        op = rng.randint(0, 6)
        if not lines:
            lines = ["[transform]"]
        if op == 0:  # drop a line
            del lines[rng.randrange(len(lines))]
        elif op == 1:  # duplicate a line
            i = rng.randrange(len(lines))
            lines.insert(i, lines[i])
        elif op == 2:  # swap a token in
            i = rng.randrange(len(lines))
            lines[i] = lines[i] + f'  # {rng.choice(_TOKENS)}'
        elif op == 3:  # inject a random key
            lines.insert(rng.randrange(len(lines) + 1), f'{rng.choice(_KEYS)} = "{rng.choice(_TOKENS)}"')
        elif op == 4:  # replace a type token
            i = rng.randrange(len(lines))
            lines[i] = lines[i].replace('"str"', f'"{rng.choice(_TOKENS)}"')
        elif op == 5:  # inject an engine-read key with a NON-STRING value
            lines.insert(
                rng.randrange(len(lines) + 1),
                f"{rng.choice(_VALUE_KEYS)} = {rng.choice(_NON_STRING_VALUES)}",
            )
        else:  # truncate
            lines = lines[: rng.randrange(len(lines) + 1)]
    return "\n".join(lines)


def _random_garbage(rng: random.Random) -> str:
    alphabet = "[]{}\"'=.,|\n abcStrfloatLiteralkindname0123456789_"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))


def test_parse_never_raises_uncaught():
    """Stage 1: mutated + garbage inputs parse or raise ContractViolation — never another
    exception class."""
    rng = random.Random(_SEED)
    for _ in range(4000):
        kind = rng.choice(_DECL_KINDS)
        text = _mutate(rng, rng.choice(_SEED_CORPUS)) if rng.random() < 0.8 else _random_garbage(rng)
        try:
            loads(text, kind, file_path="fuzz.toml")
        except ContractViolation:
            pass
        except Exception as exc:  # noqa: BLE001 — the property under test
            pytest.fail(f"stage-1 fuzz leaked {type(exc).__name__}: {exc}\n--- input ({kind}) ---\n{text}")


def _build_fuzz_registry():
    """A fixed, valid registry of handlers with known ports — the stable substrate the
    compile-fuzz throws random *pipelines* at, so the run actually reaches the stage-2
    topology checks instead of dying at parse."""
    reg = DeclarationRegistry()
    reg.add_service_type(loads(F.SERVICE_TYPE_LLM, "service_type", file_path="st.toml"))
    reg.add_handler("acme.src", loads('[transform]\n[reads]\nseed={type="str"}\n[output_schema]\na={type="str"}\nb={type="int"}', "handler", file_path="src.toml"))
    reg.add_handler("acme.mid", loads('[transform]\n[reads]\nin={type="str"}\n[output_schema]\nout={type="str"}', "handler", file_path="mid.toml"))
    reg.add_handler("acme.svc", loads('[service]\n[reads]\nin={type="str"}\n[output_schema]\nout={type="str"}\n[service_bindings]\nllm={type="conjured_llm.structured_output"}', "handler", file_path="svc.toml"))
    reg.add_handler("acme.hook", loads('[hook]\n[reads]\nin={type="str"}\n[service_bindings]\n[transport_schema]\np={type="str"}', "handler", file_path="hook.toml"))
    return reg


_HANDLER_POOL = ("acme.src", "acme.mid", "acme.svc", "acme.hook", "acme.unregistered")
_CHANNELS = ("a", "b", "out", "in", "seed", "ghost")
_PORTS = ("seed", "a", "b", "in", "out", "wat")
_STRATEGIES = ("last_wins", "append_list", "deep_merge_dict", "concat_str", "bogus")


def _random_pipeline_toml(rng: random.Random) -> str:
    """A structurally-random pipeline over the fuzz registry's known handlers, with random
    wiring maps / merges / inputs drawn from small alphabets — directly exercises the
    closure / overlap / shape / cardinality / binding-supply paths."""
    # Self-name (required since the Phase-1b floor amendment) so the generated pipeline parses
    # and the run reaches the stage-2 compile step (the `reached > 2000` guard below).
    lines: list[str] = ['[meta]', 'name = "fuzz.pipeline"']
    for _ in range(rng.randint(0, 4)):
        lines.append("[[nodes]]")
        lines.append('kind = "handler"')
        lines.append(f'name = "{rng.choice(_HANDLER_POOL)}"')
        if rng.random() < 0.5:
            k, v = rng.choice(_PORTS), rng.choice(_CHANNELS)
            lines.append(f'reads_map = {{ {k} = "{v}" }}')
        if rng.random() < 0.5:
            k, v = rng.choice(_PORTS), rng.choice(_CHANNELS)
            lines.append(f'writes_map = {{ {k} = "{v}" }}')
    if rng.random() < 0.4:
        lines.append('[service_bindings.llm]')
        lines.append('type = "conjured_llm.structured_output"')
        if rng.random() < 0.7:
            lines.append('model = "qwen"')
        if rng.random() < 0.3:
            lines.append('endpoint = "https://x"')  # mis-placed transport field
    if rng.random() < 0.4:
        lines.append("[merge]")
        lines.append(f'{rng.choice(_CHANNELS)} = "{rng.choice(_STRATEGIES)}"')
    if rng.random() < 0.6:
        lines.append("[inputs]")
        for _ in range(rng.randint(0, 3)):
            lines.append(f'{rng.choice(_CHANNELS)} = {{ type = "{rng.choice(("str", "int", "list[str]"))}" }}')
    if rng.random() < 0.4:
        lines.append("[outputs]")
        lines.append(f'{rng.choice(_CHANNELS)} = {{ type = "str" }}')
    return "\n".join(lines)


def test_compile_never_raises_uncaught():
    """Stage 2: a structurally-random pipeline over a fixed valid registry compiles or raises
    ContractViolation — or, when one compose group detects ≥2 independent faults, the
    ContractViolationGroup wrapping them (the within-group aggregation; error-channel
    § ContractViolationGroup) — never another exception class. Also feeds text-mutated
    pipelines."""
    rng = random.Random(_SEED ^ 0x1234)
    reg = _build_fuzz_registry()
    reached = 0
    for _ in range(6000):
        if rng.random() < 0.75:
            text = _random_pipeline_toml(rng)
        else:
            text = _mutate(rng, F.PIPELINE)
        try:
            pipeline = loads(text, "pipeline", file_path="p.toml")
        except ContractViolation:
            continue
        deployment = None
        if rng.random() < 0.4:
            try:
                deployment = loads(_mutate(rng, F.DEPLOYMENT), "deployment", file_path="d.toml")
            except ContractViolation:
                deployment = None
        reached += 1
        try:
            compile_pipeline(pipeline, reg, pipeline_name=F.NAME, deployment=deployment, file_path="p.toml")
        except (ContractViolation, ContractViolationGroup):
            pass
        except Exception as exc:  # noqa: BLE001 — the property under test
            pytest.fail(f"stage-2 fuzz leaked {type(exc).__name__}: {exc}\n--- pipeline ---\n{text}")
    assert reached > 2000, f"compile-fuzz only reached the compile step {reached} times — not meaningfully exercised"
