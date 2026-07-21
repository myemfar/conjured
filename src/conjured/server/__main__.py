"""``python -m conjured.server`` — launch the server process under uvicorn.

This is the **inbound-binding / server-startup surface** (reference § Inbound-binding
configuration): the bind address, port, and the served-pipelines source are supplied here,
when the process is launched — they are not part of any declaration grammar. The bundled
:class:`conjured.client.Client` drives this entry point to own a localhost subprocess;
a networked-sidecar deployment runs the same entry point bound to a network interface.

``--app`` is an import string ``module:attr`` resolving to the served pipelines — either a
``Mapping[str, Runnable]`` (qualified name → assembled runnable) or a zero-arg factory
returning one. The engine has no disk/directory pipeline loader, so the served set is
constructed by the integrator's module (hand-built registry → ``compile_pipeline`` →
``assemble``) and named here, mirroring uvicorn's own app-import convention.

When ``--port 0`` (the default the client uses), the OS picks an ephemeral port; the actual
bound port is written to ``--port-file`` (atomically) once the socket is bound, so the
launching parent reads the port without a guess-and-race. The socket is bound here and
handed to uvicorn pre-bound, so the reported port is exactly the served one.
"""

from __future__ import annotations

import argparse
import importlib
import os
import socket
from typing import Mapping

from conjured.runner.assemble import Runnable
from conjured.server.app import create_app


def _resolve_app(spec: str) -> Mapping[str, Runnable]:
    """Resolve ``module:attr`` to the served-pipelines mapping. ``attr`` may be the mapping
    itself or a zero-arg factory returning one. Fails loud on a malformed spec or a missing
    attribute (the launch surface is a developer tool — loud failures, visible state)."""
    if ":" not in spec:
        raise ValueError(
            f"--app {spec!r} must be 'module:attr' (an import path to a "
            "Mapping[str, Runnable] or a zero-arg factory returning one)"
        )
    module_name, _, attr_name = spec.partition(":")
    module = importlib.import_module(module_name)
    target = getattr(module, attr_name)
    pipelines = target() if callable(target) else target
    if not isinstance(pipelines, Mapping):
        raise TypeError(
            f"--app {spec!r} resolved to {type(pipelines).__name__}, not a "
            "Mapping[str, Runnable] (or a factory returning one)"
        )
    return pipelines


def _write_port_file(path: str, port: int) -> None:
    """Write the bound port atomically (temp + ``os.replace``), so the parent never reads a
    partial value."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(str(port))
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m conjured.server")
    parser.add_argument("--app", required=True, help="import string 'module:attr' for the served pipelines")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=0, help="bind port (0 = OS-assigned ephemeral)")
    parser.add_argument("--port-file", default=None, help="write the bound port here once bound")
    parser.add_argument("--stream-timeout", type=float, default=None, help="SSE idle-stream bound (seconds)")
    args = parser.parse_args(argv)

    import uvicorn

    app = create_app(_resolve_app(args.app), stream_timeout_s=args.stream_timeout)

    # Bind the socket here so the actual port is known before serving (race-free port
    # reporting), then hand the pre-bound socket to uvicorn.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    bound_port = sock.getsockname()[1]
    if args.port_file:
        _write_port_file(args.port_file, bound_port)

    config = uvicorn.Config(app, log_level="warning")
    server = uvicorn.Server(config)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
