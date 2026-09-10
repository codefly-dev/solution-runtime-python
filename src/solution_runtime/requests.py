"""Bounded JSON request/response values for opt-in application routes."""

from dataclasses import dataclass, field
import json
import re
import time
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import parse_qsl

MAX_TARGET_BYTES = 4096
MAX_QUERY_FIELDS = 32
MAX_BODY_BYTES = 65536
MAX_RESPONSE_BYTES = 1048576
BODY_TIMEOUT_SECONDS = 5.0


class RequestError(Exception):
    """An intentional, public HTTP error; never include upstream secrets."""

    def __init__(self, status: int, message: str):
        if type(status) is not int or not 400 <= status <= 599:
            raise ValueError("Request errors require a 4xx or 5xx status")
        self.status = status
        self.message = message
        super().__init__(message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite JSON value")


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)

    def json(self) -> Any:
        """Parse strict UTF-8 JSON. The application still validates its schema."""
        try:
            return json.loads(self.body.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_invalid_constant)
        except (ValueError, RecursionError):
            raise RequestError(400, "Invalid JSON body") from None


@dataclass(frozen=True)
class Response:
    body: Any
    status: int = 200


def encode_response(response: Response) -> bytes:
    if not isinstance(response, Response) or type(response.status) is not int or not (
        200 <= response.status <= 599 and not 300 <= response.status <= 399
    ) or response.status in (204, 205, 304):
        raise ValueError("A JSON response requires a body-bearing non-redirect status")
    payload = bytearray()
    for chunk in json.JSONEncoder(allow_nan=False, separators=(",", ":")).iterencode(response.body):
        encoded = chunk.encode("utf-8")
        if len(payload) + len(encoded) > MAX_RESPONSE_BYTES:
            raise ValueError("Response exceeds the JSON route limit")
        payload.extend(encoded)
    return bytes(payload)


def read_request(handler) -> Request:
    """Read one bounded request from the runtime's HTTP handler."""
    target = handler.path
    if len(target.encode("utf-8")) > MAX_TARGET_BYTES:
        raise RequestError(414, "Request target too long")
    if not target.startswith("/") or target.startswith("//") or "#" in target:
        raise RequestError(400, "Invalid request target")
    path, _, raw_query = target.partition("?")
    try:
        if re.search(r"%(?![0-9a-fA-F]{2})", raw_query):
            raise ValueError("Invalid escape")
        pairs = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=True,
                          encoding="utf-8", errors="strict", max_num_fields=MAX_QUERY_FIELDS)
        query = _unique_object(pairs)
        if any(not key or any(ord(c) < 32 or ord(c) == 127 for c in key + value)
               for key, value in pairs):
            raise ValueError("Invalid query field")
    except ValueError:
        raise RequestError(400, "Invalid query parameters") from None

    lengths = handler.headers.get_all("content-length", [])
    if handler.headers.get_all("transfer-encoding") or len(lengths) > 1:
        raise RequestError(400, "Unsupported request framing")
    if lengths and not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
        raise RequestError(400, "Invalid content length")
    length = int(lengths[0]) if lengths else 0
    if handler.command == "GET" and length:
        raise RequestError(400, "GET requests cannot carry a body")
    if length > MAX_BODY_BYTES:
        raise RequestError(413, "Request body too large")
    if handler.command == "POST":
        if not lengths:
            raise RequestError(411, "Content length required")
        types = handler.headers.get_all("content-type", [])
        if len(types) != 1 or types[0].lower().replace(" ", "") not in (
            "application/json", "application/json;charset=utf-8"
        ):
            raise RequestError(415, "UTF-8 application/json required")

    body = bytearray()
    previous_timeout = handler.connection.gettimeout()
    deadline = time.monotonic() + BODY_TIMEOUT_SECONDS
    try:
        while len(body) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            handler.connection.settimeout(remaining)
            chunk = handler.rfile.read1(min(8192, length - len(body)))
            if not chunk:
                raise RequestError(400, "Incomplete request body")
            body.extend(chunk)
    except TimeoutError:
        raise RequestError(408, "Request body timed out") from None
    finally:
        handler.connection.settimeout(previous_timeout)
    return Request(handler.command, path, MappingProxyType(query), bytes(body))
