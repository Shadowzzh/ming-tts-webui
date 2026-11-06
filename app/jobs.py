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
        # A process restart cannot resume a half-generated waveform. Keep its evidence.
        with self.store.lock:
            for job in self.store.jobs(status="running", limit=100000)[0]:
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
                "id": uuid.uuid4().hex, "status": "queued", "text": parameters["text"],
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
        with self.condition:
            job = self.store.get("jobs", job_id)
            if not job:
                raise APIError("任务不存在", 404)
            if job["status"] != "queued":
                raise APIError("仅等待中的任务可取消；正在生成的任务会继续完成", 409)
            job.update(status="cancelled", finished_at=now())
            self.store.put("jobs", job)
            return job

    def _claim(self):
        with self.condition:
            return self.store.claim_next(now())

    def run_one(self):
        job = self._claim()
        if job is None:
            return False
        start = time.monotonic()
        filename = f"{job['id']}.wav"
        target = self.outputs / filename
        partial = self.outputs / f"{job['id']}.partial.wav"
        try:
            parameters = dict(job["parameters"])
            parameters.pop("voice_id", None)
            if parameters.get("upload_id"):
                parameters["prompt_wav_path"] = str(safe_file(self.uploads, parameters.pop("upload_id")))
            metadata = self.engine.generate(parameters, partial)
            partial.replace(target)
            outcome = {"status": "completed", "filename": filename, **metadata}
        except Exception:
            logger.exception("Synthesis failed for job %s", job["id"])
            # Full diagnostic details stay in service logs, not public filesystem paths.
            outcome = {"status": "failed", "error": "生成失败，请检查参考音、文本和显存余量后重试；详情见服务日志"}
        finally:
            partial.unlink(missing_ok=True)
        with self.store.lock:
            # Preserve edits made while inference was running (favorite/title).
            current = self.store.get("jobs", job["id"])
            current.update(outcome, elapsed_seconds=round(time.monotonic() - start, 3), finished_at=now())
            self.store.put("jobs", current)
        return True

    def _loop(self):
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
