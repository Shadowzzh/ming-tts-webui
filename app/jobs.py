"""A single inference worker: GPU generation never overlaps within this service."""
from __future__ import annotations

import fcntl
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.validation import MAX_QUEUE, APIError, safe_file

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled"}


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkerLease:
    """One process may own a state directory, including restart recovery."""

    def __init__(self, state: Path):
        self.handle = (state / ".worker.lock").open("a")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            raise RuntimeError("已有实例占用此工作台数据目录，请使用单进程启动") from error

    def close(self):
        self.handle.close()


class JobQueue:
    def __init__(self, store, engine, uploads: Path, outputs: Path, *, start=True, lease=None):
        self.store, self.engine, self.uploads, self.outputs = store, engine, uploads, outputs
        self.condition = threading.Condition(store.lock)
        self.stopping = False
        self.thread = None
        self.lease = lease
        self.backend = getattr(engine, "backend", "local")
        # Engines that dispatch to Ray can re-attach to the same job id after a
        # business restart; a local in-process engine cannot resume a waveform.
        self.resumable = bool(getattr(engine, "resumable", False))
        with self.store.lock:
            for job in self.store.jobs(status="running", limit=100000)[0]:
                if self.resumable:
                    job.setdefault("backend", self.backend)
                    job.setdefault("phase", "waiting_gpu")
                    self.store.put("jobs", job)
                else:
                    job.update(status="failed", error="服务重启中断了生成，可使用原参数重试", finished_at=now())
                    self.store.put("jobs", job)
        if start:
            self.start()

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="ming-inference", daemon=True)
            self.thread.start()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=5)
        if self.lease and (not self.thread or not self.thread.is_alive()):
            self.lease.close()
            self.lease = None

    def submit(self, parameters, voice=None):
        with self.condition:
            queued, _ = self.store.counts()
            if queued["queued"] >= MAX_QUEUE:
                raise APIError("等待队列已满，请在已有任务完成后再提交", 429)
            snapshot = dict(parameters)
            job = {
                "id": uuid.uuid4().hex, "status": "queued", "backend": self.backend,
                "phase": None, "text": parameters["text"],
                "title": parameters["text"][:60], "voice_id": voice["id"] if voice else None,
                "voice_name": voice["name"] if voice else "模型默认 / 临时声音",
                "parameters": snapshot, "created_at": now(), "started_at": None,
                "finished_at": None, "error": None, "filename": None,
                "duration_seconds": None, "sample_rate": None, "elapsed_seconds": None,
                "favorite": False,
            }
            self.store.put("jobs", job)
            self.condition.notify_all()
            return job

    def cancel(self, job_id):
        """Cancel a queued task, or really stop a running Ray job."""
        with self.condition:
            job = self.store.get("jobs", job_id)
            if not job:
                raise APIError("任务不存在", 404)
            if job["status"] == "queued":
                job.update(status="cancelled", phase=None, finished_at=now())
                self.store.put("jobs", job)
                return job
            if job["status"] != "running" or not getattr(self.engine, "cancellable", False):
                raise APIError("仅等待中的任务可取消；正在生成的任务会继续完成", 409)
            job.update(cancel_requested=True, phase="cancelling")
            self.store.put("jobs", job)
        # Stopping Ray is a network round trip; never hold the queue lock for it.
        try:
            stopped = self.engine.cancel(job_id)
        except Exception as error:
            if getattr(error, "code", "") == "cancel-pending":
                # Ray accepted the stop but has not terminated the job yet; the UI
                # keeps showing "cancelling" and the polling thread finalises later.
                logger.info("Cancel for job %s is pending: %s", job_id, error)
                return self.store.get("jobs", job_id) or job
            logger.warning("Cancelling job %s failed: %s", job_id, error)
            raise APIError("无法停止 GPU 任务，请稍后重试或等待其完成", 503) from error
        current = self.store.get("jobs", job_id) or job
        if not stopped and current.get("status") == "running":
            logger.info("Cancel for job %s found no live Ray job", job_id)
        return current

    def _claim(self):
        # A Ray task has not started when the local queue claims it; its real start
        # time arrives from the executor (``started_at`` on a phase update or in the
        # final metadata). The local backend keeps the claim timestamp.
        started_at = None if getattr(self.engine, "reports_started_at", False) else now()
        with self.condition:
            return self.store.claim_next(started_at)

    def run_one(self):
        job = self._claim()
        if job is None:
            return False
        self._execute(job)
        return True

    def resume_running(self):
        """Re-attach to tasks that were running when the process restarted (Ray only)."""
        if not self.resumable:
            return 0
        with self.store.lock:
            running = self.store.jobs(status="running", limit=100000)[0]
        count = 0
        for job in reversed(running):
            if self.stopping:
                break
            if self._execute(job):
                count += 1
        return count

    def _phase_reporter(self, job_id, backend):
        """Persist the GPU phase so the UI can show queueing/preparing/synthesis."""
        def report(phase, detail=None):
            with self.store.lock:
                current = self.store.get("jobs", job_id)
                if not current:
                    return
                current.update(phase=phase, backend=backend)
                if detail:
                    current["phase_detail"] = {key: detail[key] for key in
                                               ("ray_status", "executor_state") if key in detail}
                    if detail.get("executor_started_at") and not current.get("started_at"):
                        current["started_at"] = detail["executor_started_at"]
                if current.get("cancel_requested") and phase != "cancelling":
                    return
                self.store.put("jobs", current)
        return report

    def _execute(self, job):
        """Run one claimed or resumed job to a terminal state."""
        start = time.monotonic()
        filename = f"{job['id']}.wav"
        target = self.outputs / filename
        partial = self.outputs / f"{job['id']}.partial.wav"
        backend = getattr(self.engine, "backend", "local")
        report = self._phase_reporter(job["id"], backend) if getattr(
            self.engine, "supports_progress", False) else None
        try:
            parameters = dict(job["parameters"])
            parameters.pop("voice_id", None)
            if parameters.get("upload_id"):
                parameters["prompt_wav_path"] = str(safe_file(self.uploads, parameters.pop("upload_id")))
            if report is None:
                metadata = self.engine.generate(parameters, partial)
            else:
                metadata = self.engine.generate(parameters, partial, job_id=job["id"],
                                                on_state=report)
            partial.replace(target)
            outcome = {"status": "completed", "filename": filename, "phase": None, **metadata}
        except Exception as error:
            logger.exception("Synthesis failed for job %s", job["id"])
            current = self.store.get("jobs", job["id"]) or {}
            if current.get("cancel_requested") or getattr(error, "code", "") == "cancelled":
                target.unlink(missing_ok=True)
                outcome = {"status": "cancelled", "phase": None, "error": None}
            else:
                # Full diagnostic details stay in service logs, not public filesystem paths.
                outcome = {"status": "failed", "phase": None,
                           "error": "生成失败，请检查参考音、文本和显存余量后重试；详情见服务日志"}
        finally:
            partial.unlink(missing_ok=True)
        with self.store.lock:
            # Preserve edits made while inference was running (favorite/title).
            current = self.store.get("jobs", job["id"])
            if not current:
                return False
            current.pop("cancel_requested", None)
            current.pop("phase_detail", None)
            current.update(outcome, elapsed_seconds=round(time.monotonic() - start, 3), finished_at=now())
            self.store.put("jobs", current)
        return True

    def _loop(self):
        try:
            self.resume_running()
        except Exception:
            logger.exception("Re-attaching interrupted jobs failed")
        while True:
            with self.condition:
                if self.stopping:
                    return
                if self.store.counts()[0]["queued"] == 0:
                    self.condition.wait(timeout=10)
                    if self.stopping:
                        return
            try:
                self.run_one()
            except Exception:
                logger.exception("Queue iteration failed")
                # Back off on persistent disk/metadata errors without a hot loop.
                with self.condition:
                    self.condition.wait(timeout=5)
