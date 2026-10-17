"""Job events and the in-process fan-out used by the SSE endpoints (SPEC 9.5, 10)."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal["progress", "done", "error"]
TERMINAL: frozenset[str] = frozenset({"done", "error"})


@dataclass(frozen=True, slots=True)
class JobEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.type in TERMINAL

    def encode(self) -> bytes:
        """The event in ``text/event-stream`` framing."""
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        return f"event: {self.type}\ndata: {payload}\n\n".encode()


KEEPALIVE = b": ping\n\n"


def progress_event(
    stage: str, pct: float, message_key: str | None = None, params: dict[str, Any] | None = None
) -> JobEvent:
    return JobEvent(
        "progress",
        {"stage": stage, "pct": pct, "message_key": message_key, "params": params or {}},
    )


def done_event(status: str = "succeeded") -> JobEvent:
    return JobEvent("done", {"status": status})


def error_event(code: str, detail: str, params: dict[str, Any] | None = None) -> JobEvent:
    return JobEvent("error", {"code": code, "detail": detail, "params": params or {}})


class EventHub:
    """Per-job subscribers (asyncio queues) plus the last progress event for late joiners.

    Everything runs on the event loop of the app: publish from other threads is not supported.
    """

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[JobEvent]]] = {}
        self._last: dict[str, JobEvent] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue[JobEvent]:
        queue: asyncio.Queue[JobEvent] = asyncio.Queue()
        self._subs.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[JobEvent]) -> None:
        subs = self._subs.get(job_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                del self._subs[job_id]

    def last(self, job_id: str) -> JobEvent | None:
        return self._last.get(job_id)

    def publish(self, job_id: str, event: JobEvent) -> None:
        if event.terminal:
            self._last.pop(job_id, None)
        else:
            self._last[job_id] = event
        for queue in list(self._subs.get(job_id, ())):
            queue.put_nowait(event)
