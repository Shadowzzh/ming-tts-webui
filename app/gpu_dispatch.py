"""Ray Jobs HTTP client for the shared single-GPU executor (protocol v1).

This module is intentionally dependency free (standard library only) so the very
same file can be copied verbatim into ``hosts/workhorse/ray-gpu/runtime/`` and
imported by other GPU-backed services (IndexTTS, Qwen3-TTS). Keep
``app/gpu_dispatch.py`` and ``hosts/workhorse/ray-gpu/runtime/gpu_dispatch.py``
byte identical.

Protocol summary (see the family contract):

* a job is a shared directory ``<root>/<job_id>/`` holding ``request.json``
  (business JSON written by the client), ``result.wav`` / ``result.json``
  (written by the worker) and ``executor.json`` (written by the generic
  executor after the model container exits);
* clients never import Ray: they POST to the Ray Jobs REST API with
  ``submission_id = "<profile>-<job_id>"`` and poll until the executor reports
  ``succeeded``;
* audio never travels through Ray object storage; only this directory is shared.

A successful ``run`` only returns once the GPU container has exited
(``executor.json`` -> ``succeeded``) *and* ``result.json`` holds a non-empty JSON
object. No audio file or business schema is required here: services that produce
audio (Ming, IndexTTS) or artifacts (Qwen ``prompt.pt``) validate their own
outputs. A stray ``result.wav`` alone is never treated as success.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import re
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["JOB_ID_RE", "PHASES", "Dispatcher", "DispatcherError"]

#: Job identifiers are 32 lowercase hex characters (one per business task).
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
#: Profiles are fixed lowercase slugs; they are interpolated into the entrypoint.
PROFILE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
#: Progress phases reported through ``on_state`` (``waiting_gpu`` is always first).
PHASES = ("waiting_gpu", "starting", "running")

TERMINAL_RAY = frozenset({"SUCCEEDED", "FAILED", "STOPPED"})
#: Dispatcher states persisted in ``dispatcher.json``.
STATE_SUBMITTING = "submitting"
STATE_SUBMITTED = "submitted"
STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_INTERRUPTED = "interrupted"
STATE_TIMEOUT = "timeout"
STATE_CANCELLING = "cancelling"
#: Terminal states that block an implicit re-submission of the same job id.
_BLOCKING = frozenset({STATE_FAILED, STATE_CANCELLED, STATE_INTERRUPTED,
                       STATE_TIMEOUT, STATE_SUCCEEDED})
_HTTP_TIMEOUT = 5.0  # every poll request is capped at 5 s (contract)
_MAX_RESPONSE = 8 * 1024 * 1024
_ARTIFACTS = ("result.wav", "result.json", "executor.json")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


class DispatcherError(RuntimeError):
    """A dispatch failure with a machine readable ``code`` and public ``detail``."""

    def __init__(self, message: str, *, code: str = "error", detail=None):
        super().__init__(message)
        self.code = code
        self.detail = dict(detail or {})


class _NetworkError(Exception):
    """Internal: transport failure (DNS, refused, timeout); safe to retry."""


class Dispatcher:
    """Submit one GPU job to Ray and wait for the executor to finish it.

    ``root`` is the shared dispatch directory (``GPU_DISPATCH_DIR`` inside the
    service container). ``url`` is the Ray Jobs endpoint; it is reachable only
    from the private service network.
    """

    def __init__(self, url: str, profile: str, root: Path, timeout_seconds: float = 3600,
                 poll_seconds: float = 1.0, *, http_timeout: float = _HTTP_TIMEOUT,
                 terminal_grace_seconds: float = 30.0):
        if not isinstance(url, str) or not url.strip():
            raise DispatcherError("Ray Jobs 地址为空", code="config")
        parsed = urllib.parse.urlsplit(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DispatcherError(f"Ray Jobs 地址无效：{url!r}", code="config")
        if not isinstance(profile, str) or not PROFILE_RE.match(profile):
            raise DispatcherError(f"profile 无效：{profile!r}", code="config")
        self.url = url.strip().rstrip("/")
        self._scheme = parsed.scheme
        self._netloc = parsed.netloc
        self._base_path = parsed.path.rstrip("/")
        self.profile = profile
        self.root = Path(root)
        self.timeout_seconds = float(timeout_seconds)
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.http_timeout = min(float(http_timeout), _HTTP_TIMEOUT)
        self.terminal_grace_seconds = max(0.0, float(terminal_grace_seconds))
        if self.timeout_seconds <= 0:
            raise DispatcherError("timeout_seconds 必须为正数", code="config")
        self.root.mkdir(parents=True, exist_ok=True)
        self._cancel_requested: set[str] = set()
        self._cancel_lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    @property
    def submission_id_base(self) -> str:
        return self.profile

    def submission_id(self, job_id: str) -> str:
        return f"{self.profile}-{job_id}"

    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _validate(self, job_id: str) -> str:
        if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
            raise DispatcherError("job_id 必须是 32 位小写十六进制", code="bad-job-id",
                                  detail={"job_id": job_id})
        return job_id

    def _http(self, method: str, path: str, payload=None):
        """Return ``(status, parsed_body)``; raises ``_NetworkError`` on transport errors.

        ``http.client`` is used directly: the scheme is fixed at construction time
        (http/https only, validated in ``__init__``), so no URL opener can be
        tricked into ``file:`` or another handler by the request path.
        """
        target = f"{self._base_path}{path}"
        parsed_target = urllib.parse.urlsplit(target)
        request_target = parsed_target.path or "/"
        if parsed_target.query:
            request_target += "?" + parsed_target.query
        data = None
        headers = {"Accept": "application/json", "User-Agent": "gpu-dispatch/v1"}
        if payload is not None:
            data = _canonical(payload)
            headers["Content-Type"] = "application/json"
        connection_class = (http.client.HTTPSConnection if self._scheme == "https"
                            else http.client.HTTPConnection)
        connection = connection_class(self._netloc, timeout=self.http_timeout)
        try:
            connection.request(method, request_target, body=data, headers=headers)
            response = connection.getresponse()
            status = response.status
            raw = response.read(_MAX_RESPONSE)
        except (http.client.HTTPException, OSError) as error:
            raise _NetworkError(f"{method} {path} 失败：{error}") from error
        finally:
            connection.close()
        body = None
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = raw.decode("utf-8", "replace")
        return status, body

    def _write_json(self, path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The polling thread and an HTTP cancel thread may write the same file in
        # one process, so the temporary name must be unique per call (not per pid).
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp")
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          indent=2, allow_nan=False) + "\n"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def _read_json(self, path: Path):
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def _state_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "dispatcher.json"

    def _load_state(self, job_id: str) -> dict:
        state = self._read_json(self._state_path(job_id))
        return state if isinstance(state, dict) else {}

    def _save_state(self, job_id: str, **changes) -> dict:
        state = self._load_state(job_id)
        state.update(changes)
        state["profile"] = self.profile
        state["updated_at"] = _utc_now()
        self._write_json(self._state_path(job_id), state)
        return state

    def _mark(self, job_id: str, state: str, **extra) -> None:
        # Never lose the raised error because metadata could not be written.
        with contextlib.suppress(OSError):
            self._save_state(job_id, state=state, **extra)

    # ----------------------------------------------------------------- request
    def _write_request(self, job_id: str, request: dict) -> str:
        """Persist ``request.json`` (never overwriting different content)."""
        digest = hashlib.sha256(_canonical(request)).hexdigest()
        path = self.job_dir(job_id) / "request.json"
        existing = self._read_json(path)
        if existing is not None:
            if not isinstance(existing, dict) or hashlib.sha256(_canonical(existing)).hexdigest() != digest:
                raise DispatcherError("同一 job_id 的 request.json 内容不一致，拒绝覆盖",
                                      code="request-mismatch", detail={"job_id": job_id})
            return digest
        if path.exists():  # unreadable/corrupt leftovers must not be silently replaced
            raise DispatcherError("request.json 无法解析，请人工检查共享目录",
                                  code="request-corrupt", detail={"job_id": job_id})
        self._write_json(path, request)
        return digest

    def _archive_attempt(self, job_id: str, index: int) -> None:
        """Move previous artifacts aside before an explicit retry."""
        directory = self.job_dir(job_id)
        for name in _ARTIFACTS:
            path = directory / name
            if path.exists():
                os.replace(path, directory / f"{name}.attempt{index}")
        state = self._state_path(job_id)
        if state.exists():
            os.replace(state, directory / f"dispatcher.attempt{index}.json")

    # -------------------------------------------------------------- ray queries
    @staticmethod
    def _job_list(body):
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        if isinstance(body, dict):
            for key in ("jobs", "items", "data"):
                value = body.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _find_by_submission(self, submission_id: str):
        """Look up a Ray job by submission id (used after ambiguous POSTs)."""
        quoted = urllib.parse.quote(submission_id, safe="")
        try:
            _, body = self._http("GET", f"/api/jobs/?job_or_submission_id={quoted}")
            matches = [job for job in self._job_list(body)
                       if job.get("submission_id") == submission_id]
            if not matches:
                _, body = self._http("GET", "/api/jobs/")
                matches = [job for job in self._job_list(body)
                           if job.get("submission_id") == submission_id]
        except _NetworkError:
            return None
        if not matches:
            return None
        matches.sort(key=lambda job: str(job.get("start_time") or ""), reverse=True)
        return matches[0]

    def _get_job(self, ray_job_id: str):
        """Return the Ray job document, ``None`` when Ray no longer knows it."""
        try:
            status, body = self._http("GET", f"/api/jobs/{urllib.parse.quote(ray_job_id, safe='')}")
        except _NetworkError as error:
            raise DispatcherError(f"无法读取 Ray 任务状态：{error}", code="ray-unreachable",
                                  detail={"ray_job_id": ray_job_id}) from error
        if status == 404:
            return None
        if status >= 500:
            raise DispatcherError(f"Ray 返回 HTTP {status}（读取任务）", code="ray-unreachable",
                                  detail={"ray_job_id": ray_job_id, "status": status})
        if status != 200:
            raise DispatcherError(f"Ray 返回 HTTP {status}（读取任务）", code="ray-error",
                                  detail={"ray_job_id": ray_job_id, "status": status})
        return body if isinstance(body, dict) else None

    def state(self, job_id: str) -> dict:
        """Read-only view of ``dispatcher.json`` (submission id/time, last state)."""
        return dict(self._load_state(self._validate(job_id)))

    def ping(self) -> dict:
        """CPU-side reachability check for the Ray Jobs endpoint (no model, no GPU)."""
        try:
            status, body = self._http("GET", "/api/version")
        except _NetworkError as error:
            return {"reachable": False, "version": None, "error": str(error)}
        if status != 200:
            return {"reachable": False, "version": None, "error": f"HTTP {status}"}
        version = body.get("version") if isinstance(body, dict) else None
        return {"reachable": True, "version": version, "error": None}

    def ray_status(self, ray_job_id: str) -> str:
        job = self._get_job(ray_job_id)
        return str((job or {}).get("status") or "UNKNOWN")

    # ---------------------------------------------------------------- executor
    def _executor(self, job_id: str):
        executor = self._read_json(self.job_dir(job_id) / "executor.json")
        return executor if isinstance(executor, dict) else None

    def _result(self, job_id: str):
        """Return ``result.json`` when the business result is a non-empty object.

        The executor is business agnostic and so is this client: audio, model
        artifacts (e.g. a Qwen ``prompt.pt`` voice profile) or pure metadata are
        all valid. Only the *business* adapter may require files or schema.
        """
        result = self._read_json(self.job_dir(job_id) / "result.json")
        if not isinstance(result, dict) or not result:
            return None
        return result

    def _succeeded(self, job_id: str):
        executor = self._executor(job_id)
        if not executor or executor.get("state") != STATE_SUCCEEDED:
            return None
        return self._result(job_id)

    # --------------------------------------------------------------- submitting
    def _submit(self, job_id: str, submission_id: str, deadline: float, *, retry: bool = False):
        """POST the job; safe to repeat because ``submission_id`` is idempotent.

        Ray keeps a submission id alive after a terminal outcome. A manual retry
        therefore only produces a new job when Ray no longer remembers the old
        submission (e.g. after a Ray restart); otherwise it is reported instead of
        silently re-running or silently reusing a stopped job.
        """
        payload = {
            "entrypoint": f"python -m gpu_runner --profile {self.profile} --job-id {job_id}",
            "submission_id": submission_id,
            "entrypoint_num_gpus": 1,
            "entrypoint_num_cpus": 0.1,
            "metadata": {"profile": self.profile, "job_id": job_id},
        }
        if retry:
            # Ray keeps a submission id after a terminal outcome; only resubmit when
            # Ray no longer knows it (e.g. after a restart).
            found = self._find_by_submission(submission_id)
            if found and found.get("job_id"):
                existing = self._get_job(str(found["job_id"]))
                status = str((existing or {}).get("status") or "UNKNOWN").upper()
                if existing is None or status not in TERMINAL_RAY:
                    return str(found["job_id"])
                self._refuse_stale(job_id, submission_id, str(found["job_id"]))
        state = self._load_state(job_id)
        attempts = int(state.get("attempts") or 0) + 1
        # Persist the submission marker *before* the network call.
        self._save_state(job_id, state=STATE_SUBMITTING, submission_id=submission_id,
                         ray_job_id=None, attempts=attempts, submitted_at=_utc_now())
        last = ""
        for attempt in (1, 2):
            if time.monotonic() >= deadline:
                break
            try:
                status, body = self._http("POST", "/api/jobs/", payload)
            except _NetworkError as error:
                last = str(error)
                status, body = None, None
            if isinstance(body, dict) and body.get("job_id"):
                return str(body["job_id"])
            found = self._find_by_submission(submission_id)
            if found and found.get("job_id"):
                return str(found["job_id"])
            if status is not None and status not in (200, 201, 202):
                last = f"HTTP {status}: {body}"
            # Only a transport timeout (or a body without job_id) is retried, and
            # the retry reuses the same submission id so Ray cannot duplicate it.
            if attempt == 1:
                time.sleep(min(self.poll_seconds, 1.0))
        self._mark(job_id, STATE_INTERRUPTED, submission_id=submission_id,
                   error=last or "提交未确认")
        raise DispatcherError(
            "提交到 Ray 未确认（可能超时或 Ray 重启）；已标记 interrupted，"
            "确认没有旧任务后请人工重试",
            code="interrupted", detail={"job_id": job_id, "submission_id": submission_id,
                                        "reason": last})

    def _refuse_stale(self, job_id: str, submission_id: str, ray_job_id: str) -> None:
        """Reject a retry that would reuse a terminal Ray job under the same submission id."""
        existing = self._get_job(ray_job_id)
        status = str((existing or {}).get("status") or "UNKNOWN").upper()
        if existing is not None and status not in TERMINAL_RAY:
            return
        if existing is None:
            return
        self._mark(job_id, STATE_FAILED, ray_job_id=ray_job_id, ray_status=status,
                   error="Ray 仍保留旧 submission")
        raise DispatcherError(
            f"Ray 仍保留该 submission 的旧任务（{status}），不能重复生成；请确认后新建任务再提交",
            code="submission-exists",
            detail={"job_id": job_id, "submission_id": submission_id,
                    "ray_job_id": ray_job_id, "ray_status": status})

    # ------------------------------------------------------------------- cancel
    def cancel(self, job_id: str, *, wait_seconds: float | None = None) -> bool:
        """Ask Ray to stop ``job_id`` and wait (bounded) for a terminal state.

        Returns ``True`` when a live Ray job was found and the stop request was
        accepted. Never waits longer than ``wait_seconds`` (default 60 s).
        """
        job_id = self._validate(job_id)
        with self._cancel_lock:
            self._cancel_requested.add(job_id)
        state = self._load_state(job_id)
        submission_id = state.get("submission_id") or self.submission_id(job_id)
        ray_job_id = state.get("ray_job_id")
        if not ray_job_id:
            found = self._find_by_submission(submission_id)
            ray_job_id = (found or {}).get("job_id")
        if not ray_job_id:
            self._mark(job_id, STATE_CANCELLED, submission_id=submission_id,
                       error="取消时未找到 Ray 任务")
            return False
        self._save_state(job_id, state=STATE_CANCELLING, ray_job_id=str(ray_job_id),
                         submission_id=submission_id, cancel_requested_at=_utc_now())
        current = self._get_job(str(ray_job_id))
        current_status = str((current or {}).get("status") or "UNKNOWN").upper()
        if current is None or current_status in TERMINAL_RAY:
            if current_status == "STOPPED":
                self._mark(job_id, STATE_CANCELLED, ray_job_id=str(ray_job_id),
                           ray_status=current_status)
            return False
        try:
            status, body = self._http("POST", f"/api/jobs/{urllib.parse.quote(str(ray_job_id), safe='')}/stop")
        except _NetworkError as error:
            raise DispatcherError(f"停止 Ray 任务失败：{error}", code="ray-unreachable",
                                  detail={"ray_job_id": ray_job_id}) from error
        if status not in (200, 202, 204):
            raise DispatcherError(f"Ray 拒绝停止任务（HTTP {status}）", code="ray-error",
                                  detail={"ray_job_id": ray_job_id, "status": status,
                                          "body": body})
        limit = time.monotonic() + (60.0 if wait_seconds is None else max(0.0, wait_seconds))
        final = current_status
        job = current  # A zero wait budget preserves the last observed live state.
        while time.monotonic() < limit:
            job = self._get_job(str(ray_job_id))
            final = str((job or {}).get("status") or "UNKNOWN").upper()
            if job is None or final in TERMINAL_RAY:
                break
            time.sleep(self.poll_seconds)
        if job is not None and final not in TERMINAL_RAY:
            # The stop request was accepted but Ray still runs the job (e.g. the
            # container ignores SIGTERM while loading a model). Keep ``cancelling``
            # and say so; the polling thread will finalise when it really stops.
            self._save_state(job_id, state=STATE_CANCELLING, ray_job_id=str(ray_job_id),
                             ray_status=final, cancel_pending=True, cancel_requested_at=_utc_now())
            raise DispatcherError(
                f"停止请求已提交，但 Ray 任务仍在运行（{final}），尚未确认终止",
                code="cancel-pending",
                detail={"job_id": job_id, "ray_job_id": str(ray_job_id),
                        "ray_status": final})
        self._save_state(job_id, state=STATE_CANCELLED, ray_job_id=str(ray_job_id),
                         ray_status=final, cancelled_at=_utc_now())
        return True

    # ---------------------------------------------------------------------- run
    def run(self, job_id: str, request: dict, *, on_state=None, retry: bool = False) -> dict:
        """Run (or re-attach to) one job; returns the validated ``result.json``.

        ``request`` is the business JSON written to ``<root>/<job_id>/request.json``;
        the same id always implies the same request content. ``retry=True`` is the
        only way to start a second Ray submission after a failure, timeout, cancel
        or interrupted submission.
        """
        job_id = self._validate(job_id)
        if not isinstance(request, dict) or not request:
            raise DispatcherError("request 必须是非空 JSON 对象", code="bad-request",
                                  detail={"job_id": job_id})
        try:
            digest = self._write_request(job_id, request)
        except OSError as error:
            raise DispatcherError(f"无法写入共享任务目录：{error}", code="dispatch-io",
                                  detail={"job_id": job_id}) from error
        except (TypeError, ValueError) as error:
            raise DispatcherError(f"request 无法序列化为 JSON：{error}", code="bad-request",
                                  detail={"job_id": job_id}) from error
        state = self._load_state(job_id)
        known = state.get("request_sha256")
        if known and known != digest:
            raise DispatcherError("同一 job_id 对应不同 request，拒绝执行",
                                  code="request-mismatch", detail={"job_id": job_id})
        if not known:
            state = self._save_state(job_id, request_sha256=digest,
                                     submission_id=self.submission_id(job_id))
            state.setdefault("request_sha256", digest)
        if retry and state:
            with self._cancel_lock:
                self._cancel_requested.discard(job_id)
            self._archive_attempt(job_id, int(state.get("attempts") or 1))
            state = self._save_state(job_id, state=STATE_SUBMITTING, ray_job_id=None,
                                     cancel_requested_at=None, finished_at=None)
            started_new = True
        else:
            started_new = False

        # A completed, validated result is authoritative: recover instead of regenerating.
        recovered = self._succeeded(job_id)
        if recovered is not None:
            self._mark(job_id, STATE_SUCCEEDED, ray_status="RECOVERED",
                       finished_at=_utc_now())
            return recovered

        executor = self._executor(job_id)
        if not started_new and state.get("state") in _BLOCKING:
            previous = str(state.get("state"))
            raise DispatcherError(
                f"该 job_id 的上一次执行已结束于 {previous}，不会自动重复生成；"
                "请人工 retry",
                code=previous, detail={"job_id": job_id, "state": state})
        if not started_new and executor and executor.get("state") == STATE_FAILED:
            self._mark(job_id, STATE_FAILED, ray_status="FAILED",
                       exit_code=executor.get("exit_code"))
            raise DispatcherError(
                "执行器报告该任务失败；请确认后人工重试（retry）",
                code="failed", detail={"job_id": job_id, "executor": executor})

        deadline = time.monotonic() + self.timeout_seconds
        ray_job_id = state.get("ray_job_id") or None
        if ray_job_id:
            existing = self._get_job(str(ray_job_id))
            if existing is None:
                self._mark(job_id, STATE_INTERRUPTED, ray_job_id=str(ray_job_id),
                           error="Ray 已丢失该提交")
                raise DispatcherError(
                    "Ray 上找不到旧提交（可能 Ray 重启），已标记 interrupted；"
                    "请确认没有残留容器后人工重试",
                    code="interrupted",
                    detail={"job_id": job_id, "ray_job_id": ray_job_id})
        else:
            ray_job_id = self._submit(job_id, self.submission_id(job_id), deadline,
                                     retry=retry)
            self._save_state(job_id, ray_job_id=str(ray_job_id), state=STATE_SUBMITTED,
                             submission_id=self.submission_id(job_id))

        phase = None
        unreachable_since = None
        terminal_at = None
        stop_sent = False
        while True:
            with self._cancel_lock:
                cancelling = job_id in self._cancel_requested
            if cancelling and not stop_sent:
                stop_sent = True
                self._stop_quietly(str(ray_job_id))
            try:
                job = self._get_job(str(ray_job_id))
                unreachable_since = None
            except DispatcherError as error:
                if error.code != "ray-unreachable":
                    raise
                # Transient poll failures are tolerated until the deadline.
                job = None
                unreachable_since = unreachable_since or time.monotonic()
                if time.monotonic() >= deadline:
                    self._stop_quietly(str(ray_job_id))
                    self._mark(job_id, STATE_TIMEOUT, error=str(error))
                    raise DispatcherError(
                        f"等待 Ray 超过 {self.timeout_seconds:.0f} 秒（最后一次连接失败）",
                        code="timeout", detail={"job_id": job_id}) from error
            ray_status = "UNKNOWN" if job is None else str(job.get("status") or "UNKNOWN").upper()
            # Read the executor record after the Ray status so phase/state agree.
            executor = self._executor(job_id)

            if job is None and unreachable_since is None:
                self._mark(job_id, STATE_INTERRUPTED, ray_job_id=str(ray_job_id),
                           error="Ray 已丢失该提交")
                raise DispatcherError(
                    "Ray 上找不到该提交（可能 Ray 重启），已标记 interrupted；"
                    "不会自动重复生成，请人工重试",
                    code="interrupted",
                    detail={"job_id": job_id, "ray_job_id": str(ray_job_id)})

            cancelled = cancelling or ray_status == "STOPPED"
            if ray_status in {"FAILED", "STOPPED"}:
                code = "cancelled" if cancelled else "failed"
                state_name = STATE_CANCELLED if cancelled else STATE_FAILED
                self._mark(job_id, state_name, ray_status=ray_status,
                           finished_at=_utc_now(), exit_code=(executor or {}).get("exit_code"))
                raise DispatcherError(
                    f"Ray 任务已结束：{ray_status}" + ("（已取消）" if cancelled else ""),
                    code=code, detail={"job_id": job_id, "ray_status": ray_status,
                                       "executor": executor})

            if executor and executor.get("state") == STATE_FAILED:
                state_name = STATE_CANCELLED if cancelling else STATE_FAILED
                self._mark(job_id, state_name, ray_status=ray_status,
                           exit_code=executor.get("exit_code"), finished_at=_utc_now())
                raise DispatcherError(
                    "GPU 任务已取消" if cancelling else
                    "GPU 任务执行失败，已保留 partial 输出；请人工重试",
                    code="cancelled" if cancelling else "failed",
                    detail={"job_id": job_id, "ray_status": ray_status,
                            "executor": executor})

            if ray_status == "SUCCEEDED":
                terminal_at = terminal_at or time.monotonic()
                if executor is None or executor.get("state") != STATE_SUCCEEDED:
                    # The executor writes executor.json after the container exits;
                    # allow a short grace period instead of declaring failure.
                    if time.monotonic() - terminal_at >= self.terminal_grace_seconds:
                        self._mark(job_id, STATE_FAILED, ray_status=ray_status,
                                   error="executor.json 未记录成功")
                        raise DispatcherError(
                            "Ray 任务成功退出但执行器未记录 succeeded（结果不可信）",
                            code="invalid-result", detail={"job_id": job_id})
                else:
                    result = self._result(job_id)
                    if result is not None:
                        with self._cancel_lock:
                            self._cancel_requested.discard(job_id)
                        self._mark(job_id, STATE_SUCCEEDED, ray_status=ray_status,
                                   finished_at=_utc_now())
                        return result
                    if time.monotonic() - terminal_at >= self.terminal_grace_seconds:
                        self._mark(job_id, STATE_FAILED, ray_status=ray_status,
                                   error="result.json 无效")
                        raise DispatcherError(
                            "执行器报告成功但 result.json 缺失或不是非空 JSON 对象",
                            code="invalid-result", detail={"job_id": job_id})
            else:
                terminal_at = None

            if ray_status == "PENDING":
                next_phase = "waiting_gpu"
            elif ray_status == "RUNNING":
                next_phase = "running" if (executor or {}).get("state") == "running" else "starting"
            else:
                next_phase = "starting"
            # ``on_state`` only emits the three contract phases; cancellation is
            # reported through ``detail["cancel_requested"]`` and the raised error.
            if next_phase != phase:
                phase = next_phase
                detail = {
                    "job_id": job_id, "ray_job_id": str(ray_job_id),
                    "ray_status": ray_status,
                    "executor_state": (executor or {}).get("state"),
                    # UTC timestamps written by the executor (container start/exit).
                    "executor_created_at": (executor or {}).get("created_at"),
                    "executor_started_at": (executor or {}).get("started_at"),
                    "submission_id": self.submission_id(job_id),
                    "elapsed_seconds": round(self.timeout_seconds - max(0.0, deadline - time.monotonic()), 1),
                    "cancel_requested": cancelling,
                }
                if on_state is not None:
                    # A UI/DB callback must never kill a running GPU job.
                    with contextlib.suppress(Exception):
                        on_state(phase, detail)

            if time.monotonic() >= deadline:
                self._stop_quietly(str(ray_job_id))
                self._mark(job_id, STATE_TIMEOUT, ray_status=ray_status,
                           finished_at=_utc_now())
                raise DispatcherError(
                    f"等待 GPU 任务超过 {self.timeout_seconds:.0f} 秒，已请求停止 Ray 任务",
                    code="timeout", detail={"job_id": job_id, "ray_status": ray_status})
            time.sleep(min(self.poll_seconds, max(0.0, deadline - time.monotonic())))

    def _stop_quietly(self, ray_job_id: str) -> None:
        with contextlib.suppress(_NetworkError, DispatcherError):
            self._http("POST", f"/api/jobs/{urllib.parse.quote(ray_job_id, safe='')}/stop")
