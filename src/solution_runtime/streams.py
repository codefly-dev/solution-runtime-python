"""Bounded SSE transport. Applications own authorization and event semantics."""
from dataclasses import dataclass, field
import json
import re
from typing import Any, Iterator

MAX_EVENT_BYTES = 512 * 1024
MAX_STREAM_SECONDS = 60.0
MAX_STREAM_EVENTS = 256


@dataclass(frozen=True)
class Event:
    data: Any = field(repr=False)
    event: str = "message"
    id: str = ""

    def encode(self) -> bytes:
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", self.event):
            raise ValueError("Invalid event name")
        if not isinstance(self.id, str) or len(self.id) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in self.id):
            raise ValueError("Invalid event identity")
        chunks = [f"event: {self.event}\n".encode()]
        if self.id:
            chunks.append(f"id: {self.id}\n".encode())
        chunks.append(b"data: ")
        size = sum(map(len, chunks)) + 2
        for chunk in json.JSONEncoder(allow_nan=False, separators=(",", ":")).iterencode(self.data):
            raw = chunk.encode("utf-8")
            size += len(raw)
            if size > MAX_EVENT_BYTES:
                raise ValueError("Event exceeds transport bound")
            chunks.append(raw)
        return b"".join(chunks) + b"\n\n"


@dataclass
class EventStream:
    """Single-use iterator; close must release upstream calls and resources.

    The application must bound each next() call and recheck current authority
    before returning data. EOF means observation closed, never task completion.
    No replay, reconnect, cancellation or memory behavior is supplied here.
    """
    events: Iterator[Event] = field(repr=False)

    def close(self):
        close = getattr(self.events, "close", None)
        if close is not None:
            close()
