"""Native HTTP streaming: authorization boundaries, disconnects and limits."""
import http.client
import socket
import struct
import threading
import pytest
from solution_runtime import Event, EventStream, RequestError, Response
from solution_runtime.streams import MAX_EVENT_BYTES
from test_requests import server, raw, call


def stream_call(server, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", server[1], timeout=3)
    client.request("GET", "/events", headers=headers or {"Authorization": "Bearer one"})
    response = client.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    client.close()
    return result


def test_stream_is_incremental_and_closes_with_original_caller_and_resume(server):
    observed = []
    closed = []
    def handler(gateway, request):
        observed.append((gateway.bearer, request.last_event_id))
        def events():
            try:
                yield Event({"value": "one\ntwo"}, "reset", "original.1")
                yield Event({"value": "three"}, "snapshot", "original.2")
            finally:
                closed.append(True)
        return EventStream(events())
    server[0].route("/events", handler)
    status, headers, body = stream_call(server, {"Authorization": "Bearer two", "Last-Event-ID": "original.0"})
    assert status == 200 and headers["x-accel-buffering"] == "no"
    assert headers["cache-control"] == "no-store, no-transform"
    assert b'data: {"value":"one\\ntwo"}\n\n' in body
    assert body.count(b"data:") == 2
    assert observed == [("Bearer two", "original.0")] and closed == [True]


def test_current_authority_failure_before_first_event_is_http_denial(server):
    closed = []
    def events():
        try:
            raise RequestError(403, "Current authority unavailable")
            yield Event({})
        finally:
            closed.append(True)
    server[0].route("/events", lambda g, r: EventStream(events()))
    assert stream_call(server)[0] == 403
    assert closed == [True]


def test_midstream_revocation_discloses_no_more_data_or_upstream_error(server):
    def events():
        yield Event({"version": 1}, "reset")
        raise RuntimeError("private upstream token")
    server[0].route("/events", lambda g, r: EventStream(events()))
    status, _, body = stream_call(server)
    assert status == 200 and body == b'event: reset\ndata: {"version":1}\n\n'


def test_stream_capacity_closes_rejected_iterator(server):
    class Source:
        closed = False
        def __next__(self):
            raise AssertionError("must not read")
        def __iter__(self): return self
        def close(self): self.closed = True
    source = Source()
    server[0]._stream_slots = threading.BoundedSemaphore(0)
    server[0].route("/events", lambda g, r: EventStream(source))
    assert stream_call(server)[0] == 503 and source.closed


def test_disconnect_releases_upstream_and_slot(server):
    closed = threading.Event()
    advance = threading.Event()
    def events():
        try:
            yield Event({"version": 1})
            advance.wait(2)
            for _ in range(100): yield Event({"data": "x" * 100000})
        finally:
            closed.set()
    server[0].route("/events", lambda g, r: EventStream(events()))
    client = socket.create_connection(("127.0.0.1", server[1]), timeout=3)
    client.sendall(b"GET /events HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\n\r\n")
    assert b"200" in client.recv(4096)
    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    client.close()
    advance.set()
    assert closed.wait(3)
    assert server[0]._stream_slots.acquire(blocking=False)
    server[0]._stream_slots.release()


def test_bad_resume_is_rejected_before_application(server):
    calls = []
    server[0].route("/events", lambda g, r: calls.append(r))
    head=b"GET /events HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\n"
    assert raw(server,head+b"Last-Event-ID: a\r\nLast-Event-ID: b\r\n\r\n")[0] == 400
    assert stream_call(server,{"Authorization":"Bearer one","Last-Event-ID":"x"*257})[0] == 400
    assert calls == []


@pytest.mark.parametrize("event", [Event({}, "bad\ndata: injected"), Event({}, id="bad\nevent"), Event({"x": float("nan")}), Event({"x": "a" * MAX_EVENT_BYTES})])
def test_frame_injection_and_size_denied(event):
    with pytest.raises(ValueError): event.encode()


def test_event_count_bound_closes_generator(server, monkeypatch):
    closed = []
    monkeypatch.setattr("solution_runtime.MAX_STREAM_EVENTS", 2)
    def events():
        try:
            for _ in range(5): yield Event({})
        finally: closed.append(True)
    server[0].route("/events",lambda g,r:EventStream(events()))
    assert stream_call(server)[2].count(b"data:") == 2
    assert closed == [True]
