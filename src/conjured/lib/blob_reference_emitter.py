"""``conjured.lib.blob_reference_emitter`` — the native blob-reference rendering hook.

A **stdlib-emission hook** (the observer kind): it reads a binary blob's path / hash
*reference* and emits it via Python's standard ``logging`` so a downstream consumer
(Studio) can render the blob. It writes no channels and returns ``None`` (the hook
contract — the runner has no merge path for a hook return); operational emission failure
is tolerated by the runner's hook wrapper.

The member realizes the path/hash-reference convention the handler reference's
§ Channel-type discipline owns: training-aware pipelines carry binary content as a
``str`` reference (a filesystem path, a content-addressed hash, an S3 key) rather than
inline ``bytes``, and this hook is the blessed emitter of that reference. How the
consumer renders the emitted reference is consumer-side — the hook emits the value and
reserves no rendering vocabulary for it.

Resolution: ordinary dotted path ``conjured.lib.blob_reference_emitter.emit`` (no
entry-point short name, per § Naming). The same-named sibling
``blob_reference_emitter.toml`` is the ``[hook]`` declaration this function satisfies.
"""

from __future__ import annotations

import json
import logging

#: The documented engine logger the hook emits to. A deployment configures this
#: logger's handlers through standard logging configuration to route the emitted
#: reference to wherever the consumer reads it — the member binds no log-file path of
#: its own.
LOGGER_NAME = "conjured.lib.blob_reference_emitter"

_logger = logging.getLogger(LOGGER_NAME)


def emit(*, reference: str, format: str) -> None:
    """Emit the blob ``reference`` via stdlib ``logging``; return ``None``.

    ``reference`` is the path / hash reference projected from the wired channel (the
    single declared input port). ``format`` is the per-deployment record-format selector
    delivered from the deployment's ``hook_transport`` block (the hook's one
    ``transport_schema`` field): ``'json'`` emits a one-key JSON object, ``'plain'`` a
    ``key=value`` line. The reference rides the log record both in the formatted message
    and as a structured ``blob_reference`` attribute, so a consumer's log handler can
    read it without parsing the message text.

    Returns ``None`` — a hook writes no channels and the runner has no merge path for a
    hook return (R-handler-001); emitting *and* returning the reference would be a
    ``HOOK_RETURN_NOT_NONE`` ContractViolation at dispatch.
    """
    if format == "json":
        message = json.dumps({"blob_reference": reference})
    else:
        message = f"blob_reference={reference}"
    _logger.info(message, extra={"blob_reference": reference})
    return None
