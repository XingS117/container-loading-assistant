"""Short-lived jobs for the single API worker; no credentials persisted."""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass

from fastapi.responses import JSONResponse

from .models import PackResponse
from .packing import PackingFailure


@dataclass
class PackJob:
    id: str
    started: float
    phase: str = 'queued'
    finished: float | None = None
    result: PackResponse | None = None
    error: dict | None = None
    http_status: int = 200

    def snapshot(self):
        return {'job_id': self.id, 'phase': self.phase,
                'elapsed_ms': round(((self.finished or time.monotonic()) - self.started) * 1000),
                'result': self.result.model_dump(mode='json') if self.result else None,
                'error': self.error, 'http_status': self.http_status}


class PackJobs:
    def __init__(self, limit=16, ttl_seconds=900):
        self.limit = limit
        self.ttl_seconds = ttl_seconds
        self.jobs = {}
        self.tasks = set()

    def cleanup(self):
        now = time.monotonic()
        for key, job in list(self.jobs.items()):
            if job.finished is not None and now - job.finished > self.ttl_seconds:
                self.jobs.pop(key)

    def start(self, run, release):
        self.cleanup()
        if len(self.jobs) >= self.limit:
            finished = [j for j in self.jobs.values() if j.finished is not None]
            if not finished:
                raise RuntimeError('job capacity reached')
            self.jobs.pop(min(finished, key=lambda j: j.finished).id)
        job = PackJob(uuid.uuid4().hex, time.monotonic())
        self.jobs[job.id] = job

        async def execute():
            try:
                result = await asyncio.wait_for(run(lambda phase: setattr(job, 'phase', phase)), 65)
                if isinstance(result, JSONResponse):
                    job.error = json.loads(result.body)['error']
                    job.http_status = result.status_code
                    job.phase = 'failed'
                else:
                    job.result = result
                    job.phase = 'complete'
            except PackingFailure as exc:
                job.error = {'code': exc.code, 'message': exc.message, 'hint': exc.hint}
                job.http_status, job.phase = 422, 'failed'
            except asyncio.TimeoutError:
                job.error = {'code': 'CALCULATION_TIMEOUT', 'message': '本次计算达到时间上限，原清单和方案未改变，请稍后重试'}
                job.http_status, job.phase = 504, 'failed'
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.getLogger(__name__).error('pack job failed id=%s type=%s', job.id, type(exc).__name__)
                job.error = {'code': 'INTERNAL_ERROR', 'message': '计算任务暂时无法完成，原清单和方案未改变'}
                job.http_status, job.phase = 500, 'failed'

        def done(task):
            if task.cancelled():
                job.error = {'code': 'JOB_EXPIRED', 'message': '服务重启，计算任务已结束，请重新提交；原订单仍保留'}
                job.http_status, job.phase = 410, 'failed'
            job.finished = time.monotonic()
            self.tasks.discard(task)
            release()

        task = asyncio.create_task(execute())
        self.tasks.add(task)
        # Also releases slots if shutdown cancels a task before its first await.
        task.add_done_callback(done)
        return job

    def get(self, job_id):
        self.cleanup()
        return self.jobs.get(job_id)

    async def close(self):
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
