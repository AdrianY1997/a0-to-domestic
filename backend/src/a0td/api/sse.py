"""Server-Sent Events helpers for the document and job event endpoints (SPEC 10)."""

from collections.abc import AsyncIterator
from typing import Any, Literal

from starlette.responses import StreamingResponse

from a0td.api.deps import Services
from a0td.jobs.events import KEEPALIVE, JobEvent
from a0td.storage import models as m


def sse_response(stream: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _payload(
    svc: Services, event: JobEvent, job_id: str, subject: Literal["job", "document"]
) -> dict[str, Any]:
    """The terminal event data enriched with the final job or document (SPEC 10)."""
    from a0td.api.schemas import document_out, job_out
    from a0td.storage import repo

    data = dict(event.data)
    with svc.db.session() as s:
        job = s.get(m.Job, job_id)
        if job is None:
            return data
        if subject == "job":
            if event.type == "done":
                data["job"] = job_out(job).model_dump(mode="json")
        else:
            doc = s.get(m.Document, job.document_id)
            if doc is not None:
                latest = repo.latest_ingest_job(s, doc.id)
                data["document"] = document_out(doc, latest.id if latest else None).model_dump(
                    mode="json"
                )
    return data


async def event_stream(
    svc: Services, job_id: str, subject: Literal["job", "document"]
) -> AsyncIterator[bytes]:
    async for event in svc.manager.events(job_id):
        if event is None:
            yield KEEPALIVE
            continue
        if event.terminal:
            event = JobEvent(event.type, _payload(svc, event, job_id, subject))
        yield event.encode()
