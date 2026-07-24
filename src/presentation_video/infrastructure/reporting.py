from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable

from presentation_video.domain.models import JobStatus
from presentation_video.domain.ports import JobReporter

logger = logging.getLogger(__name__)


class LoggingJobReporter(JobReporter):
    async def update(self, job_id: str, status: JobStatus, detail: str = "") -> None:
        logger.info("job=%s status=%s detail=%s", job_id, status.value, detail)


class CallbackJobReporter(JobReporter):
    """Adapter that forwards job updates to an async callback."""

    def __init__(
        self,
        callback: Callable[[str, JobStatus, str], Awaitable[None]],
    ) -> None:
        self._callback = callback

    async def update(self, job_id: str, status: JobStatus, detail: str = "") -> None:
        await self._callback(job_id, status, detail)


class CompositeJobReporter(JobReporter):
    """Composite pattern: publish the same update to multiple reporters."""

    def __init__(self, reporters: Iterable[JobReporter]) -> None:
        self._reporters = tuple(reporters)

    async def update(self, job_id: str, status: JobStatus, detail: str = "") -> None:
        for reporter in self._reporters:
            await reporter.update(job_id, status, detail)
