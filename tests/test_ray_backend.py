"""Ray dispatch and Ray backend tests.

A tiny in-process fake of the Ray Jobs REST API keeps these tests free of Ray,
Docker and GPU dependencies. The fake also emulates the generic executor: it
writes ``executor.json`` (and optionally the worker's result files) when the
container "exits".
"""
from __future__ import annotations

import io
import json
import math
import struct
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.gpu_dispatch import Dispatcher, DispatcherError

JOB = "0" * 31 + "1"
OTHER = "0" * 31 + "2"


def wav_bytes(seconds=0.2):
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        samples = [int(5000 * math.sin(i * 0.1)) for i in range(int(16000 * seconds))]
        handle.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return output.getvalue()


class FakeRay:
    """Minimal Ray Jobs API + executor simulation."""

    def __init__(self, root, *, status_sequence=("PENDING", "RUNNING", "RUNNING", "SUCCEEDED"),
                 fail_status=None, exit_immediately=False, drop_after=None,
                 submit_timeout=False, write_result=True, executor="auto", dispatch=None,
                 executor_stamps=None, worker_timings=None, ignore_stop=False,
                 skip_starting=False, wav_only=False, result_only=False,
                 result_payload=None):
        self.root = root
        self.dispatch = dispatch or root
        self.status_sequence = list(status_sequence)
        self.fail_status = fail_status
        self.drop_after = drop_after  # number of GETs before jobs vanish
        self.submit_timeout = submit_timeout
        self.write_result = write_result
        self.executor = executor  # auto | missing | failed | discard-wav
        self.executor_stamps = executor_stamps or ("2026-11-07T00:00:00Z",
                                                   "2026-11-07T00:00:00Z",
                                                   "2026-11-07T00:00:02Z")
        self.worker_timings = dict(worker_timings or {})
        self.ignore_stop = ignore_stop
        self.skip_starting = skip_starting
        self.wav_only = wav_only
        self.result_only = result_only
        self.result_payload = result_payload
        self.jobs: dict[str, dict] = {}
        self.submissions: dict[str, str] = {}
        self.posts: list[dict] = []
        self.polls = 0
        self.stops: list[str] = []
        self.lock = threading.Lock()
        self.drop_requests = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # -- lifecycle -------------------------------------------------------
    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    # -- state transitions ----------------------------------------------
    def _advance(self, job):
        if job["status"] in {"SUCCEEDED", "FAILED", "STOPPED"}:
            return job
        job["gets"] += 1
        if self.drop_after is not None and job["gets"] > self.drop_after:
            self.jobs.pop(job["job_id"], None)
            return None
        if self.fail_status and job["gets"] > len(self.status_sequence):
            job["status"] = self.fail_status
            return job
        index = min(job["gets"], len(self.status_sequence)) - 1
        job["status"] = self.status_sequence[index]
        if job["status"] == "RUNNING":
            self._progress(job)
        if job["status"] == "SUCCEEDED":
            self._exit(job, exit_code=0)
        elif job["status"] in {"FAILED", "STOPPED"}:
            self._exit(job, exit_code=1)
        return job

    def _progress(self, job):
        """Emulate the executor: starting on container launch, running when the worker begins."""
        if self.executor == "missing":
            return
        directory = self.dispatch / job["business_id"]
        directory.mkdir(parents=True, exist_ok=True)
        job["runs"] = job.get("runs", 0) + 1
        state = "starting" if job["runs"] == 1 and not self.skip_starting else "running"
        created_at, started_at, _ = self.executor_stamps
        (directory / "executor.json").write_text(json.dumps(
            {"state": state, "created_at": created_at, "started_at": started_at,
             "exit_code": None, "container_name": "gpu-ming-" + job["business_id"]}),
            encoding="utf-8")

    def _exit(self, job, exit_code):
        directory = self.dispatch / job["business_id"]
        directory.mkdir(parents=True, exist_ok=True)
        if self.executor == "missing":
            return
        if "running" not in job:
            job["running"] = True
        if exit_code == 0 and self.write_result and self.executor != "failed":
            if not self.wav_only:
                payload = ({"sample_rate": 16000, "duration_seconds": 0.2}
                           if self.result_payload is None else self.result_payload)
                (directory / "result.json").write_text(json.dumps(
                    {**payload, **self.worker_timings}), encoding="utf-8")
            if not self.result_only:
                (directory / "result.wav").write_bytes(wav_bytes())
        state = "succeeded" if exit_code == 0 else "failed"
        if self.executor == "failed":
            state = "failed"
        created_at, started_at, finished_at = self.executor_stamps
        (directory / "executor.json").write_text(json.dumps(
            {"state": state, "exit_code": exit_code, "created_at": created_at,
             "started_at": started_at, "finished_at": finished_at,
             "container_name": "gpu-ming-" + job["job_id"]}), encoding="utf-8")

    # -- HTTP ------------------------------------------------------------
    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # silence test output
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                parsed = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                if parsed.path == "/api/jobs/":
                    fake.posts.append(payload)
                    submission_id = payload["submission_id"]
                    if fake.submit_timeout and len(fake.posts) == 1:
                        # Emulate a request that reached Ray but whose reply was lost.
                        self._send(504, {"detail": "timeout"})
                        return
                    if submission_id in fake.submissions:
                        job_id = fake.submissions[submission_id]
                    else:
                        job_id = f"raysubmit_{len(fake.submissions) + 1:04d}"
                        fake.submissions[submission_id] = job_id
                        fake.jobs[job_id] = {"job_id": job_id, "submission_id": submission_id,
                                             "business_id": submission_id.split("-", 1)[-1],
                                             "status": "PENDING", "gets": 0}
                    self._send(200, {"job_id": job_id, "submission_id": submission_id})
                    return
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 4 and parts[0] == "api" and parts[1] == "jobs" and parts[3] == "stop":
                    fake.stops.append(parts[2])
                    job = fake.jobs.get(parts[2])
                    if job:
                        if fake.ignore_stop:
                            # A container that ignores SIGTERM keeps running.
                            self._send(200, {"stopped": True})
                            return
                        job["status"] = "STOPPED"
                        fake._exit(job, exit_code=1)
                        self._send(200, {"stopped": True})
                    else:
                        self._send(404, {"detail": "job not found"})
                    return
                self._send(404, {"detail": "not found"})

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                if fake.drop_requests:
                    self.close_connection = True
                    self.connection.close()
                    return
                if parsed.path == "/api/version":
                    self._send(200, {"version": "2.44.0", "ray_version": "2.44.0"})
                    return
                if parsed.path == "/api/jobs/":
                    wanted = (query.get("job_or_submission_id") or [None])[0]
                    items = [job for job in fake.jobs.values()
                             if wanted is None or job["submission_id"] == wanted]
                    self._send(200, items)
                    return
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 3 and parts[0] == "api" and parts[1] == "jobs":
                    job = fake.jobs.get(parts[2])
                    if job is None:
                        self._send(404, {"detail": "job not found"})
                        return
                    with fake.lock:
                        advanced = fake._advance(job)
                    if advanced is None:
                        self._send(404, {"detail": "job not found"})
                        return
                    self._send(200, {"job_id": job["job_id"], "submission_id": job["submission_id"],
                                     "status": job["status"], "start_time": 1,
                                     "message": "ok"})
                    return
                self._send(404, {"detail": "not found"})

        return Handler


def make_dispatcher(tmp_path, fake, **kwargs):
    options = {"poll_seconds": 0.01, "timeout_seconds": 5, "terminal_grace_seconds": 1}
    options.update(kwargs)
    return Dispatcher(fake.url, "ming", tmp_path, **options)


def request_for(text="你好"):
    return {"job_id": JOB, "profile": "ming", "parameters": {"text": text, "seed": 1895}}


def test_run_submits_polls_and_returns_result(tmp_path):
    with FakeRay(tmp_path) as fake:
        dispatcher = make_dispatcher(tmp_path, fake)
        phases = []
        result = dispatcher.run(JOB, request_for(), on_state=lambda phase, detail: phases.append(phase))
        assert result == {"sample_rate": 16000, "duration_seconds": 0.2}
        assert (tmp_path / JOB / "result.wav").stat().st_size > 44
        assert phases[0] == "waiting_gpu"
        assert "starting" in phases and "running" in phases
        assert fake.posts[0]["submission_id"] == f"ming-{JOB}"
        assert fake.posts[0]["entrypoint"] == f"python -m gpu_runner --profile ming --job-id {JOB}"
        assert fake.posts[0]["entrypoint_num_gpus"] == 1
        assert fake.posts[0]["entrypoint_num_cpus"] == 0.1
        # Request content is persisted for the executor and reused verbatim.
        written = json.loads((tmp_path / JOB / "request.json").read_text())
        assert written == request_for()
        state = json.loads((tmp_path / JOB / "dispatcher.json").read_text())
        assert state["state"] == "succeeded"
        assert dispatcher.root == tmp_path


def test_same_id_replays_without_new_submission(tmp_path):
    with FakeRay(tmp_path) as fake:
        dispatcher = make_dispatcher(tmp_path, fake)
        dispatcher.run(JOB, request_for())
        assert len(fake.posts) == 1
        # A restarted business process re-attaches by id and reuses the artifacts.
        again = make_dispatcher(tmp_path, fake).run(JOB, request_for())
        assert again == {"sample_rate": 16000, "duration_seconds": 0.2}
        assert len(fake.posts) == 1


def test_different_request_for_same_id_is_rejected(tmp_path):
    with FakeRay(tmp_path) as fake:
        dispatcher = make_dispatcher(tmp_path, fake)
        dispatcher.run(JOB, request_for("你好"))
        with pytest.raises(DispatcherError) as error:
            dispatcher.run(JOB, request_for("改过的文本"))
        assert error.value.code == "request-mismatch"
        assert len(fake.posts) == 1


def test_cancel_stops_ray_job_and_reports_cancelled(tmp_path):
    with FakeRay(tmp_path, status_sequence=("PENDING", "RUNNING") * 200) as fake:
        dispatcher = make_dispatcher(tmp_path, fake, timeout_seconds=10)
        errors = []

        def run():
            try:
                dispatcher.run(JOB, request_for())
            except DispatcherError as error:
                errors.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 5
        while not fake.stops and time.monotonic() < deadline:
            time.sleep(0.02)
        assert dispatcher.cancel(JOB) is True
        worker.join(timeout=5)
        assert fake.stops, "cancel must call the Ray stop endpoint"
        assert errors and errors[0].code == "cancelled"
        state = json.loads((tmp_path / JOB / "dispatcher.json").read_text())
        assert state["state"] == "cancelled"
        # No audio is published for a cancelled job.
        assert not (tmp_path / JOB / "result.wav").exists()


def test_timeout_stops_ray_and_blocks_implicit_retry(tmp_path):
    with FakeRay(tmp_path, status_sequence=("PENDING",) * 500) as fake:
        dispatcher = make_dispatcher(tmp_path, fake, timeout_seconds=0.3)
        with pytest.raises(DispatcherError) as error:
            dispatcher.run(JOB, request_for())
        assert error.value.code == "timeout"
        assert fake.stops
        assert len(fake.posts) == 1
        # Second attempt without an explicit retry must not generate again.
        with pytest.raises(DispatcherError) as second:
            dispatcher.run(JOB, request_for())
        assert second.value.code == "timeout"
        assert len(fake.posts) == 1
        # While Ray still remembers the stopped submission, an explicit retry is
        # refused instead of silently reusing or duplicating the old job.
        with pytest.raises(DispatcherError) as conflict:
            dispatcher.run(JOB, request_for(), retry=True)
        assert conflict.value.code == "submission-exists"
        # After a Ray restart the old submission is gone, so the same id may run again.
        fake.jobs.clear()
        fake.submissions.clear()
        fake.status_sequence = ["SUCCEEDED"]
        result = dispatcher.run(JOB, request_for(), retry=True)
        assert result["sample_rate"] == 16000
        assert [post["submission_id"] for post in fake.posts] == [f"ming-{JOB}"] * 2
        # Previous attempt evidence is archived, not overwritten.
        assert (tmp_path / JOB / "dispatcher.attempt1.json").exists()


def test_lost_submission_after_ray_restart_is_interrupted(tmp_path):
    with FakeRay(tmp_path, status_sequence=("PENDING",) * 500, drop_after=2) as fake:
        dispatcher = make_dispatcher(tmp_path, fake, timeout_seconds=5)
        with pytest.raises(DispatcherError) as error:
            dispatcher.run(JOB, request_for())
        assert error.value.code == "interrupted"
        assert len(fake.posts) == 1
        state = json.loads((tmp_path / JOB / "dispatcher.json").read_text())
        assert state["state"] == "interrupted"
        # Nothing is regenerated implicitly, even by a fresh process.
        with pytest.raises(DispatcherError) as second:
            make_dispatcher(tmp_path, fake).run(JOB, request_for())
        assert second.value.code == "interrupted"
        assert len(fake.posts) == 1


def test_submit_timeout_adopts_job_by_submission_id(tmp_path):
    with FakeRay(tmp_path, submit_timeout=True, status_sequence=["SUCCEEDED"]) as fake:
        result = make_dispatcher(tmp_path, fake).run(JOB, request_for())
        assert result["duration_seconds"] == 0.2
        # First POST timed out, lookup found the existing submission: no duplicate.
        assert len({post["submission_id"] for post in fake.posts}) == 1
        assert len(fake.jobs) == 1


def test_transient_poll_failures_are_tolerated(tmp_path):
    with FakeRay(tmp_path, status_sequence=("RUNNING", "RUNNING", "SUCCEEDED")) as fake:
        dispatcher = make_dispatcher(tmp_path, fake, timeout_seconds=5)
        fake.drop_requests = True
        timer = threading.Timer(0.2, lambda: setattr(fake, "drop_requests", False))
        timer.start()
        result = dispatcher.run(JOB, request_for())
        timer.cancel()
        assert result["sample_rate"] == 16000


def test_wav_without_executor_success_is_not_success(tmp_path):
    with FakeRay(tmp_path, status_sequence=["RUNNING"] * 2 + ["SUCCEEDED"],
                 executor="missing") as fake:
        dispatcher = make_dispatcher(tmp_path, fake, timeout_seconds=3, terminal_grace_seconds=0.1)
        with pytest.raises(DispatcherError) as error:
            dispatcher.run(JOB, request_for())
        assert error.value.code == "invalid-result"
        assert not (tmp_path / JOB / "executor.json").exists()


def test_stray_wav_without_result_json_is_not_success(tmp_path):
    with FakeRay(tmp_path, status_sequence=["SUCCEEDED"], wav_only=True) as fake:
        dispatcher = make_dispatcher(tmp_path, fake, timeout_seconds=3, terminal_grace_seconds=0.1)
        with pytest.raises(DispatcherError) as error:
            dispatcher.run(JOB, request_for())
        assert error.value.code == "invalid-result"
        assert (tmp_path / JOB / "result.wav").exists()  # the stray WAV is ignored


def test_executor_failed_reports_failure_without_audio(tmp_path):
    with FakeRay(tmp_path, status_sequence=["RUNNING", "FAILED"],
                 executor="failed") as fake:
        dispatcher = make_dispatcher(tmp_path, fake, timeout_seconds=3)
        with pytest.raises(DispatcherError) as error:
            dispatcher.run(JOB, request_for())
        assert error.value.code == "failed"
        assert (tmp_path / JOB / "request.json").exists()


def test_bad_job_id_and_profile_are_rejected(tmp_path):
    with FakeRay(tmp_path) as fake:
        dispatcher = make_dispatcher(tmp_path, fake)
        for bad in ["../escape", "A" * 32, "a" * 31, "/tmp/x"]:
            with pytest.raises(DispatcherError):
                dispatcher.run(bad, request_for())
        with pytest.raises(DispatcherError):
            Dispatcher(fake.url, "Ming; rm -rf /", tmp_path)
        # A different valid id creates a separate directory.
        dispatcher.run(OTHER, request_for())
        assert (tmp_path / OTHER / "request.json").exists()
        assert not (tmp_path.parent / "escape").exists()


# ---------------------------------------------------------------- Ray backend
# The web/CPU side: request mapping, CPU probe, queue integration, restart.

def make_engine(tmp_path, fake, **kwargs):
    from app.ray_engine import RayEngine
    options = {"timeout_seconds": 5, "poll_seconds": 0.01}
    options.update(kwargs)
    return RayEngine(fake.url, dispatch_dir=tmp_path / "dispatch", **options)


def dispatch_fake(tmp_path, **kwargs):
    return FakeRay(tmp_path, dispatch=tmp_path / "dispatch", **kwargs)


def test_ray_engine_maps_reference_to_inputs_mount(tmp_path):
    with dispatch_fake(tmp_path) as fake:
        engine = make_engine(tmp_path, fake)
        upload = tmp_path / "uploads" / "abc123.wav"
        upload.parent.mkdir()
        upload.write_bytes(wav_bytes())
        partial = tmp_path / "outputs" / f"{JOB}.partial.wav"
        partial.parent.mkdir()
        metadata = engine.generate({"text": "你好", "seed": 1,
                                    "prompt_wav_path": str(upload)}, partial)
        assert metadata["backend"] == "ray"
        assert metadata["sample_rate"] == 16000
        assert metadata["duration_seconds"] == 0.2
        # Only phases backed by evidence are reported: this fake has no worker
        # counters, so it must not invent a model-load or inference duration.
        assert "model_load_seconds" not in metadata["timings"]
        assert "inference_seconds" not in metadata["timings"]
        assert {"container_seconds", "total_seconds"} <= set(metadata["timings"])
        assert metadata["timings"]["total_seconds"] > 0
        assert metadata["started_at"] == "2026-11-07T00:00:00Z"
        request = json.loads((tmp_path / "dispatch" / JOB / "request.json").read_text())
        assert request["parameters"]["prompt_wav_path"] == "/inputs/abc123.wav"
        assert "upload_id" not in request["parameters"]
        assert partial.read_bytes() == (tmp_path / "dispatch" / JOB / "result.wav").read_bytes()
        # CPU-side probe never touches the model.
        info = engine.probe(upload)
        assert info["sample_rate"] == 16000 and 0.1 < info["duration_seconds"] < 0.3


def test_ray_status_is_cpu_only_and_reports_reachability(tmp_path):
    with dispatch_fake(tmp_path) as fake:
        status = make_engine(tmp_path, fake).status()
        assert status["backend"] == "ray"
        assert status["loaded"] is False and status["model_loading"] == "per-job"
        assert status["ray"]["reachable"] is True
        assert status["gpu"]["cuda_available"] is None  # nothing is loaded on the web node
    offline = make_engine(tmp_path, type("Offline", (), {"url": "http://127.0.0.1:9"})()).status()
    assert offline["backend"] == "ray" and offline["ray"]["reachable"] is False


def app_for(tmp_path, engine):
    from app.server import create_app
    settings = {"TESTING": True, **{key: str(tmp_path / key.lower()) for key in (
        "UPLOAD_DIR", "OUTPUT_DIR", "STATE_DIR", "STATIC_DIR")}}
    app = create_app(settings, engine=engine, start_worker=False)
    return app, settings


def upload_reference(client, tmp_path):
    import io
    response = client.post("/api/upload_prompt",
                           data={"file": (io.BytesIO(wav_bytes()), "我的声音.wav")})
    assert response.status_code == 200
    return response.get_json()["upload_id"]


def test_ray_backend_end_to_end_through_http(tmp_path):
    with dispatch_fake(tmp_path) as fake:
        app, _ = app_for(tmp_path, make_engine(tmp_path, fake))
        client = app.test_client()
        status = client.get("/api/status").get_json()
        assert status["backend"] == "ray" and status["ray"]["reachable"] is True
        upload_id = upload_reference(client, tmp_path)
        job = client.post("/api/jobs", json={"text": "你好", "upload_id": upload_id}).get_json()
        assert job["backend"] == "ray"
        assert app.extensions["queue"].run_one()
        saved = client.get(f"/api/jobs/{job['id']}").get_json()
        assert saved["status"] == "completed"
        assert saved["backend"] == "ray"
        assert saved["phase"] is None
        assert saved["timings"]["total_seconds"] > 0
        assert saved["parameters"]["upload_id"] == upload_id  # history keeps the upload id
        request = json.loads((tmp_path / "dispatch" / job["id"] / "request.json").read_text())
        assert request["parameters"]["prompt_wav_path"] == f"/inputs/{upload_id}"
        assert client.get(saved["audio_url"]).status_code == 200
        app.extensions["queue"].stop()


def test_ray_job_survives_business_restart_with_same_id(tmp_path):
    with dispatch_fake(tmp_path) as fake:
        app, _ = app_for(tmp_path, make_engine(tmp_path, fake))
        client = app.test_client()
        job = client.post("/api/jobs", json={"text": "重启后继续"}).get_json()
        app.extensions["queue"]._claim()  # process dies while the task is "running"
        restarted = app_for(tmp_path, make_engine(tmp_path, fake))[0]
        other = restarted.test_client()
        # A Ray task is re-attachable: it is not marked failed as a local run would be.
        assert other.get(f"/api/jobs/{job['id']}").get_json()["status"] == "running"
        assert restarted.extensions["queue"].resume_running() == 1
        saved = other.get(f"/api/jobs/{job['id']}").get_json()
        assert saved["status"] == "completed"
        assert client.get(saved["audio_url"]).status_code == 200
        restarted.extensions["queue"].stop()
        app.extensions["queue"].stop()


def test_running_job_cancel_really_stops_ray(tmp_path):
    with dispatch_fake(tmp_path, status_sequence=("PENDING", "RUNNING") * 200) as fake:
        app, _ = app_for(tmp_path, make_engine(tmp_path, fake))
        queue = app.extensions["queue"]
        client = app.test_client()
        job = client.post("/api/jobs", json={"text": "取消我"}).get_json()
        worker = threading.Thread(target=queue.run_one)
        worker.start()
        deadline = time.monotonic() + 5
        while not fake.submissions and time.monotonic() < deadline:
            time.sleep(0.02)
        response = client.post(f"/api/jobs/{job['id']}/cancel")
        assert response.status_code == 200
        worker.join(timeout=10)
        assert fake.stops, "cancelling a running task must stop the Ray job"
        saved = client.get(f"/api/jobs/{job['id']}").get_json()
        assert saved["status"] == "cancelled"
        assert saved["audio_url"] is None
        app.extensions["queue"].stop()


def test_ray_unreachable_fails_cleanly(tmp_path):
    from app.ray_engine import RayEngine
    engine = RayEngine("http://127.0.0.1:9", dispatch_dir=tmp_path / "dispatch",
                       timeout_seconds=0.5, poll_seconds=0.05)
    app, _ = app_for(tmp_path, engine)
    client = app.test_client()
    job = client.post("/api/jobs", json={"text": "Ray 掉线"}).get_json()
    assert app.extensions["queue"].run_one()
    saved = client.get(f"/api/jobs/{job['id']}").get_json()
    assert saved["status"] == "failed"
    assert saved["audio_url"] is None
    assert "Traceback" not in (saved["error"] or "")
    app.extensions["queue"].stop()


def test_worker_cli_rejects_missing_or_bad_request(tmp_path, capsys):
    from app.infer_job import main
    assert main([str(tmp_path / "empty")]) == 2
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "request.json").write_text(json.dumps({"job_id": JOB}))
    assert main([str(tmp_path / "bad")]) == 2
    assert not (tmp_path / "bad" / "result.wav").exists()
    assert "parameters" in capsys.readouterr().err


def test_ray_backend_selection_never_imports_torch():
    """The CPU web process must not import torch/model libraries for the Ray backend."""
    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    script = ("import sys; from app.engines import build_engine; "
              "engine = build_engine({'BACKEND': 'ray', 'RAY_JOBS_URL': 'http://127.0.0.1:9', "
              "'DISPATCH_DIR': '/tmp/none', 'RAY_TIMEOUT_SECONDS': 1}); "
              "print('torch' in sys.modules, 'torchaudio' in sys.modules, engine.status()['backend'])")
    completed = subprocess.run([sys.executable, "-c", script], cwd=root,
                               env={**os.environ, "PYTHONPATH": "."}, check=False,
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["False", "False", "ray"]


# ------------------------------------------------------- hardening (review)
# 1) One Dispatcher is written by the poll thread and an HTTP cancel thread.

def test_state_writes_are_unique_per_call_and_never_corrupt(tmp_path):
    with dispatch_fake(tmp_path) as fake:
        dispatcher = make_dispatcher(tmp_path, fake)
        dispatcher._save_state(JOB, request_sha256="a" * 64, attempts=1)
        payload_a = {"attempts": 1, "note": "A" * 200_000}
        payload_b = {"attempts": 2, "note": "B" * 200_000}
        errors = []

        def writer(payload):
            try:
                for _ in range(40):
                    dispatcher._write_json(tmp_path / JOB / "dispatcher.json", payload)
            except Exception as error:  # noqa: BLE001 - collect, then assert empty
                errors.append(error)

        threads = [threading.Thread(target=writer, args=(p,)) for p in (payload_a, payload_b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not errors
        final = json.loads((tmp_path / JOB / "dispatcher.json").read_text())
        assert final in (payload_a, payload_b)
        assert [p.name for p in (tmp_path / JOB).iterdir() if p.name.endswith(".tmp")] == []


# 2) probe fallback must understand torchaudio's metadata field names.

def test_probe_reads_torchaudio_sample_rate_and_num_frames(tmp_path, monkeypatch):
    import sys
    import types

    class Metadata:
        sample_rate = 44100
        num_frames = 22050
        channels = 1

    torchaudio = types.ModuleType("torchaudio")
    monkeypatch.setattr(torchaudio, "info", lambda path: Metadata(), raising=False)
    monkeypatch.setitem(sys.modules, "soundfile", None)  # force ImportError
    monkeypatch.setitem(sys.modules, "torchaudio", torchaudio)
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"not-a-real-mp3")
    from app.ray_engine import RayEngine
    info = RayEngine("http://127.0.0.1:9", dispatch_dir=tmp_path / "dispatch").probe(clip)
    assert info == {"duration_seconds": 0.5, "sample_rate": 44100}
    # Legacy tuple form (torchaudio < 2.1) is understood too.
    monkeypatch.setattr(torchaudio, "info", lambda path: (48000, 96000), raising=False)
    assert RayEngine("http://127.0.0.1:9", dispatch_dir=tmp_path / "dispatch").probe(clip) == {
        "duration_seconds": 2.0, "sample_rate": 48000}


# 3) cancel() must not claim a stop that Ray has not confirmed.

@pytest.mark.parametrize("wait_seconds", [0, 0.2])
def test_cancel_pending_keeps_cancelling_state_instead_of_faking_stop(tmp_path, wait_seconds):
    with dispatch_fake(tmp_path, status_sequence=("RUNNING",) * 500, ignore_stop=True) as fake:
        dispatcher = make_dispatcher(tmp_path, fake, timeout_seconds=20)
        errors = []

        def run():
            try:
                dispatcher.run(JOB, request_for())
            except DispatcherError as error:
                errors.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 5
        while not fake.jobs and time.monotonic() < deadline:
            time.sleep(0.02)
        with pytest.raises(DispatcherError) as pending:
            dispatcher.cancel(JOB, wait_seconds=wait_seconds)
        assert pending.value.code == "cancel-pending"
        state = json.loads((tmp_path / JOB / "dispatcher.json").read_text())
        assert state["state"] == "cancelling"
        assert state["cancel_pending"] is True
        assert not (tmp_path / JOB / "result.wav").exists()
        # The polling thread still owns the job and finalises when Ray really stops.
        for job in fake.jobs.values():
            job["status"] = "STOPPED"
        worker.join(timeout=5)
        assert errors and errors[0].code == "cancelled"
        assert json.loads((tmp_path / JOB / "dispatcher.json").read_text())["state"] == "cancelled"


def test_http_cancel_pending_keeps_task_in_cancelling(tmp_path):
    with dispatch_fake(tmp_path, status_sequence=("RUNNING",) * 500, ignore_stop=True) as fake:
        engine = make_engine(tmp_path, fake)
        app, _ = app_for(tmp_path, engine)
        client = app.test_client()
        job = client.post("/api/jobs", json={"text": "很慢的任务"}).get_json()
        app.extensions["queue"]._claim()
        response = client.post(f"/api/jobs/{job['id']}/cancel")
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "running" and body["phase"] == "cancelling"
        app.extensions["queue"].stop()


# 4) Timings come from executor UTC stamps plus the worker's own counters.

def _epoch(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def test_timings_come_from_executor_stamps_and_worker_counters(tmp_path):
    from datetime import datetime, timedelta, timezone
    base = datetime.now(timezone.utc)
    stamps = tuple((base + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")
                   for offset in (10, 12, 30))
    with dispatch_fake(tmp_path, status_sequence=["PENDING", "RUNNING", "SUCCEEDED"],
                       executor_stamps=stamps, skip_starting=True,
                       worker_timings={"model_load_seconds": 5.0,
                                       "inference_seconds": 8.0}) as fake:
        engine = make_engine(tmp_path, fake)
        partial = tmp_path / "out" / f"{JOB}.partial.wav"
        partial.parent.mkdir()
        phases = []
        metadata = engine.generate({"text": "计时"}, partial,
                                   on_state=lambda phase, detail: phases.append(phase))
        timings = metadata["timings"]
        # A real starting window can shorter than the poll interval: the phase list
        # legitimately skips it, and the timings must stay exact anyway.
        assert "starting" not in phases
        assert timings["container_seconds"] == 2.0
        assert timings["model_load_seconds"] == 5.0
        assert timings["inference_seconds"] == 8.0
        submitted = engine.dispatcher.state(JOB)["submitted_at"]
        # No double counting: total equals executor stamps, not sum of guesses.
        assert timings["total_seconds"] == round(_epoch(stamps[2]) - _epoch(submitted), 3)
        assert timings["queue_seconds"] == round(_epoch(stamps[0]) - _epoch(submitted), 3)
        assert 29.5 <= timings["total_seconds"] <= 31.0
        assert 9.5 <= timings["queue_seconds"] <= 11.0
        assert (timings["queue_seconds"] + timings["container_seconds"]
                + timings["model_load_seconds"] + timings["inference_seconds"]) <= timings["total_seconds"]
        # The real start is the GPU container start, not the local submit time.
        assert metadata["started_at"] == stamps[1] != submitted


def test_ray_job_started_at_comes_from_the_executor(tmp_path):
    with dispatch_fake(tmp_path) as fake:
        app, _ = app_for(tmp_path, make_engine(tmp_path, fake))
        client = app.test_client()
        job = client.post("/api/jobs", json={"text": "开始时间"}).get_json()
        claimed = app.extensions["queue"]._claim()
        assert claimed["started_at"] is None  # no local claim timestamp for Ray
        app.extensions["queue"]._execute(claimed)
        saved = client.get(f"/api/jobs/{job['id']}").get_json()
        assert saved["started_at"] == "2026-11-07T00:00:00Z"
        assert saved["status"] == "completed"
        app.extensions["queue"].stop()


def test_local_backend_keeps_claim_started_at(tmp_path):
    from test_studio import FakeEngine
    app, _ = app_for(tmp_path, FakeEngine())
    client = app.test_client()
    job = client.post("/api/jobs", json={"text": "本地开始时间"}).get_json()
    claimed = app.extensions["queue"]._claim()
    assert claimed["started_at"]  # local keeps the claim timestamp (unchanged)
    app.extensions["queue"]._execute(claimed)
    saved = client.get(f"/api/jobs/{job['id']}").get_json()
    assert saved["started_at"] == claimed["started_at"]
    app.extensions["queue"].stop()


def test_worker_records_model_load_and_inference_separately(tmp_path, monkeypatch):
    """Cold model load must not be counted as inference in result.json."""
    from app import infer_job, runtime

    class Wrapper:
        load_seconds = 4.0

        def load(self):
            time.sleep(0.05)

    class StubEngine:
        def __init__(self, path, device):
            self.wrapper = Wrapper()

        def generate(self, parameters, path):
            time.sleep(0.05)
            path.write_bytes(wav_bytes())
            return {"sample_rate": 16000, "duration_seconds": 0.2}

    monkeypatch.setattr(runtime, "MingEngine", StubEngine)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "request.json").write_text(json.dumps(
        {"job_id": JOB, "profile": "ming", "parameters": {"text": "你好", "seed": 1}}))
    assert infer_job.main([str(job_dir)]) == 0
    payload = json.loads((job_dir / "result.json").read_text())
    assert 0.03 <= payload["model_load_seconds"] <= 5
    assert 0.03 <= payload["inference_seconds"] <= 5
    assert payload["model_load_seconds"] >= 0.05
    assert payload["checkpoint_load_seconds"] == 4.0
    assert payload["sample_rate"] == 16000
    assert (job_dir / "result.wav").stat().st_size > 44
    assert not (job_dir / "result.partial.wav").exists()


def test_generic_dispatcher_accepts_artifact_result_without_audio(tmp_path):
    """Qwen-style voice profiles produce no WAV; the shared client must accept it."""
    payload = {"artifact": "prompt.pt", "voice_id": "qwen-voice-1", "bytes": 1024}
    with FakeRay(tmp_path, status_sequence=["PENDING", "RUNNING", "SUCCEEDED"],
                 result_only=True, result_payload=payload) as fake:
        result = make_dispatcher(tmp_path, fake).run(JOB, {"job_id": JOB, "profile": "qwen17b",
                                                           "parameters": {"text": "创建音色"}})
        assert result == payload
        assert not (tmp_path / JOB / "result.wav").exists()
        assert json.loads((tmp_path / JOB / "dispatcher.json").read_text())["state"] == "succeeded"
        # Recovery from the shared directory is just as generic and audio-free.
        again = make_dispatcher(tmp_path, fake).run(JOB, {"job_id": JOB, "profile": "qwen17b",
                                                          "parameters": {"text": "创建音色"}})
        assert again == payload
        assert len(fake.posts) == 1


def test_empty_result_object_is_not_success(tmp_path):
    with FakeRay(tmp_path, status_sequence=["SUCCEEDED"], result_only=True,
                 result_payload={}) as fake:
        with pytest.raises(DispatcherError) as error:
            make_dispatcher(tmp_path, fake, terminal_grace_seconds=0.1).run(
                JOB, {"job_id": JOB, "profile": "qwen17b", "parameters": {"text": "x"}})
        assert error.value.code == "invalid-result"


def test_ming_adapter_keeps_its_own_audio_check(tmp_path):
    """Audio validation is a business rule: Ming still refuses a json-only result."""
    with dispatch_fake(tmp_path, status_sequence=["PENDING", "RUNNING", "SUCCEEDED"],
                       result_only=True,
                       result_payload={"sample_rate": 16000, "duration_seconds": 0.2}) as fake:
        engine = make_engine(tmp_path, fake)
        partial = tmp_path / "out" / f"{JOB}.partial.wav"
        partial.parent.mkdir()
        with pytest.raises(DispatcherError) as error:
            engine.generate({"text": "没有音频"}, partial)
        assert error.value.code == "invalid-result"
        assert not partial.exists()


def test_worker_confines_prompt_path_to_inputs_mount(tmp_path, monkeypatch):
    from app.infer_job import main
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "ok.wav").write_bytes(wav_bytes())
    outside = tmp_path / "secret.wav"
    outside.write_bytes(wav_bytes())
    (inputs / "escape.wav").symlink_to(outside)
    nested = inputs / "nested"
    nested.mkdir()
    (nested / "ok.wav").write_bytes(wav_bytes())
    monkeypatch.setenv("MING_INPUTS_DIR", str(inputs))
    bad_paths = ["/etc/passwd", str(outside), str(inputs / "escape.wav"),
                 "relative.wav", str(inputs / "missing.wav"), str(nested / ".." / ".." / "secret.wav")]
    for index, bad in enumerate(bad_paths):
        job_dir = tmp_path / f"bad{index}"
        job_dir.mkdir()
        (job_dir / "request.json").write_text(json.dumps(
            {"job_id": JOB, "profile": "ming",
             "parameters": {"text": "校验", "prompt_wav_path": bad}}))
        assert main([str(job_dir)]) == 2, bad
        assert not (job_dir / "result.json").exists()


def test_worker_rewrites_verified_prompt_path(tmp_path, monkeypatch):
    from app import infer_job, runtime
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    clip = inputs / "ok.wav"
    clip.write_bytes(wav_bytes())
    monkeypatch.setenv("MING_INPUTS_DIR", str(inputs))
    seen = {}

    class Wrapper:
        load_seconds = 0.0

        def load(self):
            pass

    class StubEngine:
        def __init__(self, path, device):
            self.wrapper = Wrapper()

        def generate(self, parameters, path):
            seen["prompt"] = parameters.get("prompt_wav_path")
            path.write_bytes(wav_bytes())
            return {"sample_rate": 16000, "duration_seconds": 0.2}

    monkeypatch.setattr(runtime, "MingEngine", StubEngine)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "request.json").write_text(json.dumps(
        {"job_id": JOB, "profile": "ming",
         "parameters": {"text": "校验", "prompt_wav_path": str(clip)}}))
    assert infer_job.main([str(job_dir)]) == 0
    assert seen["prompt"] == str(clip.resolve())
    assert (job_dir / "result.json").is_file()
