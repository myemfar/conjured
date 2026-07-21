"""``conjured.server`` — the Server component (the engine's wire surface).

C4 responsibility (``conjured/docs/architecture/components.md`` § Server): "The
engine's wire surface. Accepts wire requests on the default HTTP+SSE transport,
translates them into kernel invocations, and projects the kernel's canonical event
log onto the wire." The wire API is the operating/observing side of the two-sided
consumer boundary — what consumers in any language reach; composing crosses the
in-process compose API (the pipeline component's surface).

The engine ships as **one process** (no Container-level decomposition); the server
wraps the runner internally. The wire protocol it realizes is specified by
``conjured/docs/components/server/reference.md`` — one first-party reference protocol
whose endpoint roster that reference owns (the run trigger, the event stream, and the
token stream; cited, not restated — a restated roster goes stale silently) on the
reference stack **Starlette + uvicorn + sse-starlette**. The serving seam stays
swappable; this is the one blessed default.

Public surface:

- :func:`~conjured.server.app.create_app` — build the Starlette app over a mapping of
  served runnables (qualified name → assembled ``Runnable``).
- :func:`~conjured.server.problem_details.to_problem_details` — the RFC 9457 HTTP wire
  projection (R-error-channel-005), homed here per error-channel § Scope-split ("in the
  engine's HTTP error-response handler").

Launch the process with ``python -m conjured.server`` (see :mod:`conjured.server.__main__`)
— the inbound-binding / server-startup surface (host / port / served-pipelines source).

The ASGI/SSE stack is an **optional extra** (``pip install conjured[server]``); importing
this package without it raises a clear ``ImportError`` naming the extra (the README-taught
contract, same posture as the ``conjured[compilers]`` affordances — a ``ModuleNotFoundError``
chained from the missing stack module; any other import failure propagates untouched).
"""

from __future__ import annotations

#: The ASGI/SSE stack the ``server`` extra installs — only a miss of THESE modules is the
#: missing-extra condition; anything else re-raises untouched (relabeling a real defect as
#: "install the extra" would mask it).
_SERVER_EXTRA_STACK = frozenset({"starlette", "sse_starlette", "uvicorn"})

try:
    from conjured.server.app import create_app
    from conjured.server.problem_details import to_problem_details
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the extra installed
    if (exc.name or "").partition(".")[0] not in _SERVER_EXTRA_STACK:
        raise
    # guarantees: server-extra-importerror
    raise ModuleNotFoundError(
        "conjured.server requires the ASGI/SSE stack: pip install 'conjured[server]'"
    ) from exc

__all__ = ["create_app", "to_problem_details"]
