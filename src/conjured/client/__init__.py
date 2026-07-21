"""``conjured.client`` — the first-party ``conjured`` Python client.

C4 responsibility (``conjured/docs/architecture/components.md`` § ``conjured`` Python
client): "A first-party thin client. Wraps a bundled localhost subprocess running the
server, exposing import-and-use ergonomics for Python consumers (``import conjured``) so
Python tooling — tests, notebooks, scripts — does not need to spin up a separate process.
The client speaks the same wire API as any other consumer; there is no separate Python API
contract to maintain."

The **bundled-localhost-subprocess** integration mode (reference § Inbound-binding
configuration — the Python default): the client launches and owns a server subprocess
bound to **loopback only** (no network exposure), and exposes a **blocking** call →
:class:`~conjured.runner.run.RunResult`, mirroring the in-process runner's ergonomics (a
returned value IS success; a halt raises). It speaks the same wire API as any other
consumer — it POSTs ``/runs`` and reads the synchronous response — so there is no separate
Python API contract.

Because the server is constructed with a ``Mapping[str, Runnable]`` (the engine has no
disk/directory loader), the client is told **which module builds the served pipelines** via
an ``app`` import string ``module:attr`` (a mapping or a zero-arg factory), passed through
to ``python -m conjured.server`` — the same app-import convention uvicorn uses.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from types import TracebackType
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from conjured.runner.run import RunResult

_LOOPBACK = "127.0.0.1"


class ServerError(Exception):
    """A halted run surfaced over the wire (a non-2xx ``application/problem+json``
    response). Carries the HTTP ``status`` and the parsed RFC 9457 ``problem`` body — the
    wire boundary's structured error, the same surface any consumer dispatches on. The
    client does not reconstruct the engine's in-process exception types across the process
    boundary; the wire form IS the contract."""

    def __init__(self, status: int, problem: Mapping[str, object]) -> None:
        self.status = status
        self.problem = dict(problem)
        title = problem.get("title", "server error")
        detail = problem.get("detail", "")
        super().__init__(f"[{status}] {title}: {detail}")


class Client:
    """A bundled-localhost server subprocess + a blocking ``run`` call.

    ``app`` is the ``module:attr`` import string for the served pipelines (a
    ``Mapping[str, Runnable]`` or a zero-arg factory). Use as a context manager — the
    subprocess starts on ``__enter__`` and is torn down on ``__exit__`` — or call
    :meth:`start` / :meth:`stop` explicitly.
    """

    def __init__(
        self,
        app: str,
        *,
        env: Mapping[str, str] | None = None,
        startup_timeout_s: float = 10.0,
        stream_timeout_s: float | None = None,
    ) -> None:
        self._app = app
        self._env = dict(env) if env is not None else None
        self._startup_timeout_s = startup_timeout_s
        self._stream_timeout_s = stream_timeout_s
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None

    # -- lifecycle -----------------------------------------------------------------
    def start(self) -> "Client":
        """Launch the server subprocess on an OS-assigned loopback port and block until it
        is accepting connections (or fail loud past ``startup_timeout_s``)."""
        if self._proc is not None:
            raise RuntimeError("client already started")
        fd, port_file = tempfile.mkstemp(prefix="conjured-server-", suffix=".port")
        os.close(fd)
        os.unlink(port_file)  # the subprocess writes it atomically once bound
        argv = [
            sys.executable, "-m", "conjured.server",
            "--app", self._app,
            "--host", _LOOPBACK,
            "--port", "0",
            "--port-file", port_file,
        ]
        if self._stream_timeout_s is not None:
            argv += ["--stream-timeout", str(self._stream_timeout_s)]
        proc = subprocess.Popen(argv, env=self._env)
        self._proc = proc
        try:
            self._port = self._await_port(proc, port_file)
            self._await_ready(proc, self._port)
        except Exception:
            self.stop()
            raise
        finally:
            if os.path.exists(port_file):
                os.unlink(port_file)
        return self

    def _await_port(self, proc: subprocess.Popen, port_file: str) -> int:
        deadline = time.monotonic() + self._startup_timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"conjured server subprocess exited (code {proc.returncode}) before "
                    "binding a port — check the --app import string and that "
                    "conjured[server] (uvicorn) is installed"
                )
            try:
                text = open(port_file, encoding="utf-8").read().strip()
            except OSError:
                text = ""
            if text:
                return int(text)
            time.sleep(0.02)
        raise TimeoutError(
            f"conjured server did not report its port within {self._startup_timeout_s}s"
        )

    def _await_ready(self, proc: subprocess.Popen, port: int) -> None:
        deadline = time.monotonic() + self._startup_timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"conjured server subprocess exited (code {proc.returncode}) during startup"
                )
            try:
                with socket.create_connection((_LOOPBACK, port), timeout=0.2):
                    return  # uvicorn is listening
            except OSError:
                time.sleep(0.02)
        raise TimeoutError(
            f"conjured server did not accept connections within {self._startup_timeout_s}s"
        )

    def stop(self) -> None:
        """Terminate the server subprocess (idempotent)."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self._proc = None
        self._port = None

    def __enter__(self) -> "Client":
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    # -- the blocking run call -----------------------------------------------------
    @property
    def base_url(self) -> str:
        if self._port is None:
            raise RuntimeError("client not started — call start() (or use it as a context manager)")
        return f"http://{_LOOPBACK}:{self._port}"

    def run(
        self,
        pipeline: str,
        inputs: Mapping[str, object] | None = None,
        *,
        pipeline_run_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> RunResult:
        """Run ``pipeline`` over ``inputs`` on the bundled server and block for the result
        — the wire projection of one engine invocation. Returns a
        :class:`~conjured.runner.run.RunResult` on success (2xx); raises
        :class:`ServerError` carrying the RFC 9457 problem body on a halt (non-2xx),
        mirroring the in-process "a value returned IS success; a halt raises" discipline."""
        body: dict[str, object] = {"pipeline": pipeline, "inputs": dict(inputs or {})}
        if pipeline_run_id is not None:
            body["pipeline_run_id"] = pipeline_run_id
        if timeout_ms is not None:
            body["timeout_ms"] = timeout_ms
        request = Request(
            f"{self.base_url}/runs",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request) as response:  # noqa: S310 — loopback only, our own server
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as http_error:
            problem = _read_problem(http_error)
            raise ServerError(http_error.code, problem) from None
        except URLError as url_error:  # pragma: no cover - connection lost mid-run
            raise RuntimeError(
                f"conjured client could not reach the bundled server: {url_error.reason}"
            ) from url_error
        return RunResult(state=dict(payload["state"]), run_id=payload["run_id"])


def _read_problem(http_error: HTTPError) -> dict[str, object]:
    """Parse the RFC 9457 ``application/problem+json`` body off an error response; degrade
    to a minimal envelope when the body is not a JSON **object** (never mask the status).

    A problem document is an object. A body that is non-JSON *or* valid-but-non-object JSON (a
    scalar or array — ``json.loads`` succeeds and returns an ``int``/``str``/``list``) is not one,
    and returning it as the ``problem`` would both break the ``dict[str, object]`` contract and hide
    the HTTP status behind a shape the caller can't read; both degrade to the status envelope."""
    try:
        parsed = json.loads(http_error.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - a non-JSON error body still carries a status
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return {"status": http_error.code, "title": http_error.reason or "server error", "detail": ""}
