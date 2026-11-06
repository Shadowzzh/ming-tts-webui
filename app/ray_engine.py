"""Ray-backed inference engine for the Ming voice studio.

The CPU web container never imports torch and never holds the model: each job is
submitted to the shared Ray GPU executor (see ``app/gpu_dispatch.py``), which
starts one one-shot model container per task. Audio is exchanged only through
the shared dispatch directory, never through Ray object storage.

Responsibility split:

* this module maps a validated web request to ``request.json`` (rewriting the
  uploaded reference clip to the read-only ``/inputs/<file>`` mount), then copies
  the produced ``result.wav`` to the partial path the job queue expects;
* ``app/infer_job.py`` runs inside the GPU container and does the synthesis.
"""
from __future__ import annotations

import importlib
import json
import math
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from app.gpu_dispatch import JOB_ID_RE, Dispatcher, DispatcherError

INPUTS_PREFIX = "/inputs"


class RayEngine:
    """Engine adapter with the same surface as ``app.runtime.MingEngine``."""

    backend = "ray"
    supports_progress = True   # accepts ``on_state`` / ``job_id`` keyword arguments
    cancellable = True         # ``cancel()`` stops the Ray job
    reports_started_at = True  # real start time comes from the GPU container
    resumable = True           # running jobs are re-attached after a process restart

    def __init__(self, url: str | None = None, *, profile: str = "ming",
                 dispatch_dir: str | Path | None = None, inputs_dir: str = INPUTS_PREFIX,
                 timeout_seconds: float | None = None, poll_seconds: float = 1.0,
                 uploads: str | Path | None = None):
        self.url = url or os.environ.get("RAY_JOBS_URL") or "http://ray-gpu-control:8265"
        self.profile = profile
        self.dispatch_dir = Path(dispatch_dir or os.environ.get("GPU_DISPATCH_DIR", "/app/dispatch"))
        self.inputs_dir = inputs_dir.rstrip("/") or INPUTS_PREFIX
        self.uploads = Path(uploads) if uploads else None
        self.timeout_seconds = float(timeout_seconds if timeout_seconds is not None
                                     else os.environ.get("MING_RAY_TIMEOUT_SECONDS", "3600"))
        self.dispatcher = Dispatcher(self.url, self.profile, self.dispatch_dir,
                                     timeout_seconds=self.timeout_seconds,
                                     poll_seconds=poll_seconds)
        self._timings: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        """Cheap CPU-only status: model loading is per task on the GPU node."""
        ping = self.dispatcher.ping()
        return {
            "backend": "ray",
            "loaded": False,
            "model_loading": "per-job",
            "device": f"ray/{self.profile}",
            "sample_rate": None,
            "gpu": {"cuda_available": None, "name": "Ray GPU 节点（按任务加载）",
                    "remote": True},
            "ray": {"url": self.url, "profile": self.profile,
                    "dispatch_dir": str(self.dispatch_dir),
                    "reachable": ping["reachable"], "version": ping["version"],
                    "error": ping["error"]},
        }

    # ------------------------------------------------------------------- probe
    def probe(self, path: Path) -> dict:
        """Decode duration/sample rate on the CPU (the web container has no devices)."""
        suffix = path.suffix.lower()
        if suffix == ".wav":
            try:
                import wave
                with wave.open(str(path)) as handle:
                    rate, frames = handle.getframerate(), handle.getnframes()
                if rate and frames:
                    duration = frames / rate
                    if math.isfinite(duration) and duration > 0:
                        return {"duration_seconds": round(duration, 3), "sample_rate": rate}
            except (wave.Error, EOFError, OSError):
                pass  # float/extensible WAV: fall through to a decoder
        for module_name in ("soundfile", "torchaudio"):
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue
            try:
                info = module.info(str(path))
            except Exception:  # noqa: BLE001,S112 - try the next decoder
                continue
            # soundfile exposes samplerate/frames; torchaudio exposes
            # sample_rate/num_frames. Accept either (and the legacy tuple form).
            rate = int(getattr(info, "samplerate", 0)
                       or getattr(info, "sample_rate", 0) or 0)
            frames = int(getattr(info, "frames", 0)
                         or getattr(info, "num_frames", 0) or 0)
            if (not rate or not frames) and isinstance(info, tuple) and len(info) >= 2:
                rate, frames = int(info[0] or 0), int(info[1] or 0)
            duration = frames / rate if rate else 0
            if rate and math.isfinite(duration) and duration > 0:
                return {"duration_seconds": round(duration, 3), "sample_rate": rate}
        raise ValueError("无法读取音频信息")

    # ---------------------------------------------------------------- generate
    def _job_id(self, path: Path, job_id: str | None) -> str:
        candidate = job_id
        if not candidate:
            name = path.name
            if name.endswith(".partial.wav"):
                candidate = name[: -len(".partial.wav")]
            else:
                candidate = path.stem
        if not isinstance(candidate, str) or not JOB_ID_RE.match(candidate):
            raise DispatcherError("任务标识必须是 32 位小写十六进制", code="bad-job-id")
        return candidate

    def request_for(self, job_id: str, parameters: dict) -> dict:
        """Deterministic business request (identical bytes on every re-attach)."""
        payload = dict(parameters)
        payload.pop("upload_id", None)
        prompt = payload.get("prompt_wav_path")
        if prompt:
            # The GPU container only ever sees the read-only ``/inputs`` mount.
            payload["prompt_wav_path"] = f"{self.inputs_dir}/{Path(str(prompt)).name}"
        return {"job_id": job_id, "profile": self.profile, "parameters": payload}

    def generate(self, parameters: dict, path: Path, *, job_id: str | None = None,
                 on_state=None) -> dict:
        """Submit the task, wait for the executor, publish the WAV at ``path``."""
        job_id = self._job_id(Path(path), job_id)
        request = self.request_for(job_id, parameters)
        started = time.monotonic()

        def report(phase, detail):
            # Timings come from the executor's UTC stamps and the worker counters
            # below; polls can miss ``starting``, so phases are not timed.
            if on_state is not None:
                on_state(phase, detail)

        result = self.dispatcher.run(job_id, request, on_state=report)
        source = self.dispatcher.job_dir(job_id) / "result.wav"
        if not source.is_file() or source.stat().st_size <= 44:
            raise DispatcherError("执行器报告成功但共享目录缺少 result.wav",
                                  code="invalid-result", detail={"job_id": job_id})
        shutil.copyfile(source, path)
        timings, started_at = self._timings_for(job_id, result,
                                                fallback_total=time.monotonic() - started)
        metadata = {
            "backend": "ray",
            "sample_rate": int(result["sample_rate"]) if result.get("sample_rate") else None,
            "duration_seconds": result.get("duration_seconds"),
            "timings": timings,
            "ray": {"job_id": job_id, "profile": self.profile,
                    "submission_id": self.dispatcher.submission_id(job_id)},
        }
        if started_at:
            # The task really started when the GPU container reported it, never at
            # the local queue-claim time.
            metadata["started_at"] = started_at
        with self._lock:
            self._timings[job_id] = timings
        return metadata

    # ---------------------------------------------------------------- timings
    @staticmethod
    def _epoch(value):
        """Parse an executor UTC ISO timestamp (``Z`` or ``+00:00``) to epoch seconds."""
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _json(path: Path):
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _timings_for(self, job_id: str, result: dict, *, fallback_total: float):
        """Build timings only from evidence: executor UTC stamps and worker counters.

        ``starting`` is often shorter than one poll interval, so phase observation
        is not used. The container's own ``started_at`` covers model loading, so
        the worker's explicit ``model_load_seconds`` / ``inference_seconds`` keep
        cold start out of the inference number.
        """
        executor = self._json(self.dispatcher.job_dir(job_id) / "executor.json") or {}
        state = self.dispatcher.state(job_id)
        submitted = self._epoch(state.get("submitted_at"))
        created = self._epoch(executor.get("created_at"))
        started = self._epoch(executor.get("started_at"))
        finished = self._epoch(executor.get("finished_at"))
        timings: dict[str, float] = {}
        if submitted is not None and created is not None and created >= submitted:
            timings["queue_seconds"] = round(created - submitted, 3)
        if created is not None and started is not None and started >= created:
            timings["container_seconds"] = round(started - created, 3)
        for key in ("model_load_seconds", "inference_seconds"):
            value = result.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if math.isfinite(value) and value >= 0:
                timings[key] = round(float(value), 3)
        total = finished - submitted if finished is not None and submitted is not None else None
        if total is None or total <= 0:
            total = fallback_total
        timings["total_seconds"] = round(total, 3)
        started_at = executor.get("started_at")
        return timings, started_at if self._epoch(started_at) is not None else None

    # ------------------------------------------------------------------- cancel
    def cancel(self, job_id: str) -> bool:
        """Ask Ray to stop the job; the queue marks the task cancelled on failure."""
        return self.dispatcher.cancel(job_id)
