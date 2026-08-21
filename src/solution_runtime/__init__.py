"""Generic runtime for "solution" modules — the Python twin of the Go runtime.

Owns everything every solution needs identically: env/config, self-registration
with the host and the gateway (with a heartbeat), CORS, static Module Federation
asset serving, the capability handshake, and the solution manifest. A solution
author supplies a manifest and handlers; each handler receives a Gateway that
forwards the caller's bearer. Standard library only; knows nothing about any
specific host or solution.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

Handler = Callable[["Gateway"], Any]


def _env(key: str, fallback: str) -> str:
    return os.environ.get(key) or fallback


@dataclass
class Gateway:
    """Bound to the caller's bearer; calls the platform on the user's behalf.

    The gateway URL, the bearer, and the wire protocol are all hidden here — a
    solution handler only names a procedure and passes typed protobuf messages.
    """

    base_url: str
    bearer: str

    def unary(self, procedure: str, request, response_type):
        """Typed Connect unary call through the gateway. `request` and the
        returned value are generated protobuf messages; auth and transport are
        handled here so the caller never touches either."""
        http_request = urllib.request.Request(
            f"{self.base_url}{procedure}",
            data=request.SerializeToString(),
            headers={
                "content-type": "application/proto",
                "connect-protocol-version": "1",
                "authorization": self.bearer,
            },
            method="POST",
        )
        with urllib.request.urlopen(http_request, timeout=10) as http_response:
            body = http_response.read()
        message = response_type()
        message.ParseFromString(body)
        return message

    def get_json(self, path: str) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"accept": "application/json", "authorization": self.bearer},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))


@dataclass
class Solution:
    id: str
    title: str
    order: int = 0
    exposed_module: str = "./Page"
    contract: str = "lastlogin"
    capabilities: list[str] = field(default_factory=list)
    _handlers: dict[str, Handler] = field(default_factory=dict)

    def handle(self, path: str, handler: Handler) -> "Solution":
        self._handlers[path] = handler
        return self

    # --- config -----------------------------------------------------------
    @property
    def _port(self) -> str:
        return _env("PORT", "8091")

    @property
    def _public_url(self) -> str:
        return _env("PUBLIC_URL", f"http://localhost:{self._port}")

    @property
    def _gateway_url(self) -> str:
        return _env("GATEWAY_URL", "http://localhost:42152").rstrip("/")

    @property
    def _assets_dir(self) -> Path:
        return Path(_env("ASSETS_DIR", "../fe-remote/dist"))

    def manifest(self) -> dict:
        return {
            "id": self.id,
            "nav": {"title": self.title, "path": f"/s/{self.id}", "order": self.order},
            "frontend": {
                "type": "module-federation",
                "manifestUrl": f"{self._public_url}/assets/mf-manifest.json",
                "exposedModule": self.exposed_module,
                "reactRange": "^19",
            },
            "backend": {
                "serviceAlias": self.id,
                "capabilityPath": "/.well-known/capabilities",
            },
        }

    def capabilities_response(self) -> dict:
        return {
            "schemaVersion": 1,
            "contract": self.contract,
            "contractMajor": 1,
            "capabilities": self.capabilities,
        }

    # --- serve ------------------------------------------------------------
    def serve(self) -> None:
        host_register = _env(
            "HOST_REGISTER_URL", "http://localhost:21931/api/solutions/register"
        )
        gateway_register = _env(
            "GATEWAY_REGISTER_URL", "http://localhost:42152/solutions/_register"
        )
        self_upstream = _env("SELF_UPSTREAM", self._public_url)
        _spawn(_heartbeat, host_register, self.manifest(), f"host as {self.id}")
        _spawn(
            _heartbeat,
            gateway_register,
            {"id": self.id, "upstream": self_upstream},
            f"gateway as {self.id}",
        )
        print(f"solution {self.id!r} listening on :{self._port} (gateway={self._gateway_url})", flush=True)
        handler = partial(_RequestHandler, self)
        ThreadingHTTPServer(("", int(self._port)), handler).serve_forever()


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


def _heartbeat(url: str, body: dict, label: str) -> None:
    logged = False
    while True:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers={"content-type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status < 300 and not logged:
                    print(f"registered with {label}", flush=True)
                    logged = True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(15)


class _RequestHandler(BaseHTTPRequestHandler):
    def __init__(self, solution: Solution, *args, **kwargs):
        self._solution = solution
        super().__init__(*args, **kwargs)

    def _cors(self) -> None:
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "authorization, content-type")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self._cors()
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/.well-known/solution.json":
            self._json(200, self._solution.manifest())
        elif path == "/.well-known/capabilities":
            self._json(200, self._solution.capabilities_response())
        elif path == "/health":
            self._json(200, {"ok": True})
        elif path.startswith("/assets/"):
            self._serve_asset(path[len("/assets/") :])
        elif path in self._solution._handlers:
            self._run_handler(path)
        else:
            self._json(404, {"error": "not found"})

    def _run_handler(self, path: str) -> None:
        bearer = self.headers.get("authorization", "")
        if not bearer:
            self._json(401, {"error": "missing bearer"})
            return
        gateway = Gateway(self._solution._gateway_url, bearer)
        try:
            result = self._solution._handlers[path](gateway)
        except urllib.error.HTTPError as error:
            self._json(502, {"error": f"gateway {error.code}"})
            return
        except Exception as error:  # noqa: BLE001
            self._json(502, {"error": str(error)})
            return
        self._json(200, result)

    def _serve_asset(self, relative: str) -> None:
        assets_dir = self._solution._assets_dir.resolve()
        target = (assets_dir / relative).resolve()
        if assets_dir not in target.parents and target != assets_dir:
            self._json(403, {"error": "forbidden"})
            return
        if not target.is_file():
            self._json(404, {"error": "not found"})
            return
        data = target.read_bytes()
        content_type = (
            "application/javascript"
            if target.suffix == ".js"
            else "application/json"
            if target.suffix == ".json"
            else "application/octet-stream"
        )
        self.send_response(200)
        self.send_header("content-type", content_type)
        self._cors()
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # silence default logging
        pass
