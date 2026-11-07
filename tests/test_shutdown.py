"""Real Waitress entrypoint shutdown, using CPU fixtures and isolated SQLite."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

from app.server import create_app
from app.storage import Store
from app.validation import APIError
from test_ray_backend import FakeRay
from test_studio import FakeEngine

ROOT = Path(__file__).resolve().parents[1]


def wait_for(check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("shutdown fixture did not reach expected state")


@contextmanager
def web_process(root, *, ray_url=None, local_block=False):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "MING_BIND": "127.0.0.1", "MING_PORT": str(port),
           "MING_BACKEND": "ray", "RAY_JOBS_URL": ray_url or "http://127.0.0.1:1",
           "GPU_DISPATCH_DIR": str(root / "dispatch"), "MING_RAY_POLL_SECONDS": "0.03",
           **{f"MING_{key}_DIR": str(root / key.lower()) for key in
              ("UPLOAD", "OUTPUT", "STATE", "STATIC")}}
    if local_block:
        # Exercise main() and real Waitress, replacing only model inference.
        command = [sys.executable, "-u", "-c", """
import time
from app import engines, server
class BlockedEngine:
    def status(self):
        return {'loaded': False, 'device': 'cpu-fixture'}
    def generate(self, parameters, path):
        time.sleep(60)
        raise AssertionError('fixture must be stopped before finishing')
engines.build_engine = lambda config: BlockedEngine()
server.main()
"""]
    else:
        command = [sys.executable, "-u", "-m", "app.server"]
    with (root / "web.log").open("w") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
        def request(path, payload=None):
            body = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as response:
                return json.load(response)
        def ready():
            assert process.poll() is None, (root / "web.log").read_text()
            try:
                request("/api/jobs")
                return True
            except (OSError, urllib.error.URLError):
                return False
        try:
            wait_for(ready)
            yield process, request
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


def stop(process, sig=signal.SIGTERM, *, repeat=False):
    started = time.monotonic()
    process.send_signal(sig)
    if repeat:
        time.sleep(0.15)
        if process.poll() is None:
            process.send_signal(sig)
    process.wait(timeout=8)
    elapsed = time.monotonic() - started
    assert process.returncode == 0, f"shutdown exit={process.returncode}, elapsed={elapsed:.3f}s"
    assert elapsed < 7, f"shutdown took {elapsed:.3f}s"


def integrity(root):
    with sqlite3.connect(root / "state" / "studio.sqlite3") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    return Store(root / "state" / "studio.sqlite3")


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_idle_entrypoint_exits_cleanly(tmp_path, sig):
    with web_process(tmp_path) as (process, request):
        stop(process, sig)
    assert integrity(tmp_path).counts()[0] == {"queued": 0, "running": 0}


def test_running_local_work_is_interrupted_and_queued_work_survives(tmp_path):
    with web_process(tmp_path, local_block=True) as (process, request):
        running = request("/api/jobs", {"text": "shutdown fixture"})["id"]
        wait_for(lambda: request(f"/api/jobs/{running}")["status"] == "running")
        queued = request("/api/jobs", {"text": "queued fixture"})["id"]
        stop(process, repeat=True)
    store = integrity(tmp_path)
    assert store.get("jobs", running)["status"] == "running"
    assert store.get("jobs", queued)["status"] == "queued"
    settings = {key: str(tmp_path / value) for key, value in (
        ("UPLOAD_DIR", "upload"), ("OUTPUT_DIR", "output"), ("STATE_DIR", "state"))}
    restored = create_app(settings, engine=FakeEngine(), start_worker=False)
    try:
        saved = restored.extensions["store"].get("jobs", running)
        assert saved["status"] == "failed"
        assert saved["filename"] is None
        assert restored.extensions["store"].get("jobs", queued)["status"] == "queued"
    finally:
        restored.extensions["queue"].stop()


@pytest.mark.parametrize("ray_status", ["PENDING", "RUNNING"])
def test_ray_work_survives_web_shutdown_and_reconnects_once(tmp_path, ray_status):
    with FakeRay(tmp_path, dispatch=tmp_path / "dispatch", status_sequence=[ray_status]) as fake:
        with web_process(tmp_path, ray_url=fake.url) as (process, request):
            running = request("/api/jobs", {"text": "shutdown fixture"})["id"]
            wait_for(lambda: bool(fake.posts) and
                     request(f"/api/jobs/{running}").get("phase") is not None)
            queued = request("/api/jobs", {"text": "queued fixture"})["id"]
            stop(process, repeat=True)
        store = integrity(tmp_path)
        saved = store.get("jobs", running)
        assert saved["status"] == "running"
        assert saved["filename"] is None and saved["finished_at"] is None
        assert store.get("jobs", queued)["status"] == "queued"
        assert fake.stops == []
        assert len(fake.posts) == 1
        submission = json.loads((tmp_path / "dispatch" / running / "dispatcher.json").read_text())
        with web_process(tmp_path, ray_url=fake.url) as (process, request):
            wait_for(lambda: request(f"/api/jobs/{running}").get("phase") is not None)
            # Let only this original submission finish. The queued task stays pending.
            with fake.lock:
                fake._exit(next(iter(fake.jobs.values())), exit_code=0)
                next(iter(fake.jobs.values()))["status"] = "SUCCEEDED"
            wait_for(lambda: request(f"/api/jobs/{running}")["status"] == "completed")
            assert len([p for p in fake.posts if p["submission_id"] == submission["submission_id"]]) == 1
            stop(process)
        assert fake.stops == []
        assert integrity(tmp_path).get("jobs", running)["status"] == "completed"


def test_stopped_queue_rejects_submission_and_does_not_claim(tmp_path):
    settings = {key: str(tmp_path / key.lower()) for key in ("UPLOAD_DIR", "OUTPUT_DIR", "STATE_DIR")}
    app = create_app(settings, engine=FakeEngine(), start_worker=False)
    queue = app.extensions["queue"]
    queued = queue.submit({"text": "queued fixture"})
    queue.stop()
    with pytest.raises(APIError) as error:
        queue.submit({"text": "shutdown fixture"})
    assert error.value.status == 503
    assert queue.run_one() is False
    assert app.extensions["store"].get("jobs", queued["id"])["status"] == "queued"
