"""Native HTTP tests for bounded, caller-bound application routes."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import http.client
from http.server import ThreadingHTTPServer
import json
import socket
import threading
import time

import pytest

from solution_runtime import RequestError, Response, Solution, _RequestHandler
from solution_runtime.requests import MAX_BODY_BYTES, MAX_RESPONSE_BYTES


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "http://gateway.invalid")
    solution = Solution(id="example", title="Example")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_RequestHandler, solution))
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01))
    thread.start()
    yield solution, httpd.server_port
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def call(server, method="GET", path="/thing", body=None, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", server[1], timeout=3)
    try:
        client.request(method, path, body, headers if headers is not None else {"Authorization": "Bearer fixture-one"})
        response = client.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read())
    finally:
        client.close()


def raw(server, data):
    with socket.create_connection(("127.0.0.1", server[1]), timeout=3) as client:
        client.sendall(data)
        client.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(client)
        response.begin()
        return response.status, response.read()


def post_headers(**extra):
    return {"Authorization": "Bearer fixture-one", "Content-Type": "application/json", **extra}


def test_post_round_trip_with_query_status_and_original_caller(server):
    received = []
    def handler(gateway, request):
        received.append((gateway, request))
        with pytest.raises(TypeError):
            request.query["selector"] = "changed"
        return Response({"selector": request.query["selector"], "input": request.json()}, 201)
    server[0].route("/thing", handler, method="POST")
    status, headers, body = call(server, "POST", "/thing?selector=a%2Bb", '{"text":"hello"}', post_headers())
    assert status == 201 and body == {"selector": "a+b", "input": {"text": "hello"}}
    assert headers["cache-control"] == "no-store"
    assert headers["connection"] == "close"
    gateway, request = received[0]
    assert gateway.base_url == "http://gateway.invalid" and gateway.bearer == "Bearer fixture-one"
    assert request.method == "POST" and request.path == "/thing"
    assert "hello" not in repr(request)


def test_legacy_handler_manifest_and_get_post_coexist(server):
    server[0].handle("/thing", lambda gw: {"legacy": True})
    server[0].route("/thing", lambda gw, req: Response({"new": True}), method="POST")
    assert call(server)[2] == {"legacy": True}
    assert call(server, "POST", body="{}", headers=post_headers())[2] == {"new": True}
    assert call(server, path="/.well-known/solution.json", headers={})[2]["id"] == "example"
    assert call(server, "POST", path="/missing", body="{}", headers=post_headers())[0] == 404


@pytest.mark.parametrize("headers", [{}, {"Cookie": "session=fixture"}, {"Authorization": "Basic fixture"}, {"Authorization": "Bearer "}])
def test_no_handler_invocation_without_single_bearer(server, headers):
    calls = []
    server[0].route("/thing", lambda gw, req: calls.append(req))
    assert call(server, headers=headers)[0] == 401
    assert calls == []


@pytest.mark.parametrize("target", ["/thing?a=1&a=2", "/thing?a=%ff", "/thing?a=%zz", "/thing?=empty", "/thing?a=%00", "/thing?bare", "/thing?" + "&".join(f"a{i}=1" for i in range(33))])
def test_invalid_query_denies_before_invocation(server, target):
    calls = []
    server[0].route("/thing", lambda gw, req: calls.append(req))
    assert call(server, path=target)[0] == 400
    assert calls == []


def test_target_limit(server):
    server[0].route("/thing", lambda gw, req: Response({}))
    assert call(server, path="/thing?a=" + "a" * 4096)[0] == 414


def test_exact_body_limit_and_original_credential_spelling(server):
    observed = []
    def handler(gateway, request):
        observed.append(gateway.bearer)
        return Response({"length": len(request.body), "text": request.json()})
    server[0].route("/thing", handler, method="POST")
    body = '"' + "a" * (MAX_BODY_BYTES - 2) + '"'
    status, _, reply = call(server, "POST", body=body,
                            headers=post_headers(Authorization="bearer fixture-lowercase"))
    assert status == 200 and reply["length"] == MAX_BODY_BYTES
    assert observed == ["bearer fixture-lowercase"]


@pytest.mark.parametrize("fields,status", [
    ("Content-Length: 1\r\nContent-Length: 1\r\n", 400),
    ("Transfer-Encoding: chunked\r\n", 400),
    ("Content-Length: -1\r\n", 400),
    (f"Content-Length: {MAX_BODY_BYTES + 1}\r\n", 413),
    ("", 411),
    ("Content-Length: 2\r\nContent-Type: text/plain\r\n", 415),
    ("Content-Length: 5\r\nContent-Type: application/json\r\n", 400),
])
def test_bad_framing_and_truncation_before_handler(server, fields, status):
    calls = []
    server[0].route("/thing", lambda gw, req: calls.append(req), method="POST")
    response = raw(server, ("POST /thing HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer fixture-one\r\n" + fields + "\r\n{}").encode())
    assert response[0] == status
    assert calls == []


def test_duplicate_authorization_and_get_body_rejected(server):
    calls = []
    server[0].route("/thing", lambda gw, req: calls.append(req))
    prefix = b"GET /thing HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\n"
    assert raw(server, prefix + b"Authorization: Bearer two\r\n\r\n")[0] == 401
    assert raw(server, prefix + b"Content-Length: 2\r\n\r\n{}")[0] == 400
    assert calls == []


@pytest.mark.parametrize("body", [b"{", b'{"x":1,"x":2}', b'{"x":NaN}', b'"\xff"'])
def test_strict_json_parse_never_exposes_input(server, body):
    server[0].route("/thing", lambda gw, req: Response(req.json()), method="POST")
    assert call(server, "POST", body=body, headers=post_headers())[::2] == (400, {"error": "Invalid JSON body"})


def test_slow_body_total_deadline_stops_before_handler(server, monkeypatch):
    monkeypatch.setattr("solution_runtime.requests.BODY_TIMEOUT_SECONDS", 0.15)
    calls = []
    server[0].route("/thing", lambda gw, req: calls.append(req), method="POST")
    with socket.create_connection(("127.0.0.1", server[1]), timeout=3) as client:
        client.sendall(b"POST /thing HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer fixture\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
        for _ in range(3):
            time.sleep(0.04)
            client.sendall(b" ")
        response = http.client.HTTPResponse(client)
        response.begin()
        assert response.status == 408
    assert calls == []


def test_independent_requests_keep_original_gateway(server):
    barrier = threading.Barrier(2)
    gateways = []
    def handler(gw, request):
        gateways.append(gw)
        barrier.wait(timeout=3)
        return Response({"caller": gw.bearer})
    server[0].route("/thing", handler)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call, server, headers={"Authorization": f"Bearer fixture-{n}"}) for n in (1, 2)]
        assert [future.result()[2]["caller"] for future in futures] == ["Bearer fixture-1", "Bearer fixture-2"]
    assert gateways[0] is not gateways[1]


@pytest.mark.parametrize("status", [401, 403, 404, 409, 429, 503])
def test_application_status_is_preserved(server, status):
    server[0].route("/thing", lambda gw, req: Response({"error": "public"}, status))
    assert call(server)[::2] == (status, {"error": "public"})


def test_safe_application_error_and_hidden_unexpected_exception(server):
    def deliberate(gw, req): raise RequestError(403, "denied")
    def unexpected(gw, req): raise RuntimeError("private-upstream-detail")
    server[0].route("/thing", deliberate)
    server[0].route("/other", unexpected)
    assert call(server)[::2] == (403, {"error": "denied"})
    assert call(server, path="/other")[::2] == (500, {"error": "request failed"})


@pytest.mark.parametrize("response", [Response("x" * MAX_RESPONSE_BYTES), Response({}, 302), Response({}, 204), Response(float("nan")), {"missing": "Response"}])
def test_invalid_or_oversized_output_is_generic_error(server, response):
    server[0].route("/thing", lambda gw, req: response)
    assert call(server)[::2] == (500, {"error": "request failed"})


def test_lost_ack_does_not_retry_handler(server):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def handler(gw, req):
        calls.append(req.json())
        entered.set()
        assert release.wait(timeout=3)
        finished.set()
        return Response({"saved": True})
    server[0].route("/thing", handler, method="POST")
    client = socket.create_connection(("127.0.0.1", server[1]), timeout=3)
    try:
        client.sendall(b"POST /thing HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer fixture\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}")
        assert entered.wait(timeout=3)
        client.close()
        release.set()
        assert finished.wait(timeout=3)
        assert calls == [{}]
    finally:
        client.close()
        release.set()


@pytest.mark.parametrize("path,method", [("relative", "GET"), ("//host", "GET"), ("/x?query", "GET"), ("/health", "POST"), ("/assets/x", "GET"), ("/thing", "DELETE")])
def test_bad_route_registration(path, method):
    with pytest.raises(ValueError):
        Solution(id="example", title="Example").route(path, lambda g, r: Response({}), method=method)


def test_duplicate_routes_and_legacy_collisions():
    solution = Solution(id="example", title="Example")
    solution.route("/thing", lambda g, r: Response({}))
    with pytest.raises(ValueError): solution.route("/thing", lambda g, r: Response({}))
    with pytest.raises(ValueError): solution.handle("/thing", lambda g: {})
    solution.handle("/legacy", lambda g: {})
    with pytest.raises(ValueError): solution.route("/legacy", lambda g, r: Response({}))
