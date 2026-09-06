"""Pins the `Gateway.unary(procedure, request, response_type)` seam.

The companion's Python facade emits calls against exactly this signature and
wire shape, so a change here silently breaks every generated SDK. The test
drives a real recording HTTP server (not a mock) with real protobuf messages
and asserts both the call signature and the bytes on the wire. Once the
companion publishes a generated-facade fixture, it can be added here to exercise
the same seam through generated code.
"""

from __future__ import annotations

import inspect
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from google.protobuf.wrappers_pb2 import StringValue

from solution_runtime import Gateway


class _Recorder(BaseHTTPRequestHandler):
    requests: list[dict] = []
    reply: bytes = b""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        _Recorder.requests.append(
            {
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": self.rfile.read(length),
            }
        )
        self.send_response(200)
        self.send_header("content-type", "application/proto")
        self.send_header("content-length", str(len(_Recorder.reply)))
        self.end_headers()
        self.wfile.write(_Recorder.reply)

    def log_message(self, *args) -> None:
        pass


def test_unary_signature_is_stable():
    parameters = list(inspect.signature(Gateway.unary).parameters)
    assert parameters == ["self", "procedure", "request", "response_type"]


def test_unary_round_trips_through_a_real_gateway():
    _Recorder.requests = []
    _Recorder.reply = StringValue(value="pong").SerializeToString()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host, port = server.server_address
        gateway = Gateway(f"http://{host}:{port}", "Bearer token-123")

        response = gateway.unary(
            "/pkg.Service/Method", StringValue(value="ping"), StringValue
        )

        assert response.value == "pong"  # response parsed into response_type
    finally:
        server.shutdown()

    (recorded,) = _Recorder.requests
    assert recorded["path"] == "/pkg.Service/Method"
    assert recorded["body"] == StringValue(value="ping").SerializeToString()
    assert recorded["headers"]["content-type"] == "application/proto"
    assert recorded["headers"]["connect-protocol-version"] == "1"
    assert recorded["headers"]["authorization"] == "Bearer token-123"
