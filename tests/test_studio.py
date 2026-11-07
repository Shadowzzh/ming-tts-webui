"""HTTP/storage/queue integration tests without Torch or GPU dependencies."""
import io
import math
import struct
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest  # pyright: ignore[reportMissingImports] -- installed in the isolated test environment
from app.server import create_app


def wav_bytes(seconds=0.2):
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        samples = [int(5000 * math.sin(i * 0.1)) for i in range(int(16000 * seconds))]
        handle.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return output.getvalue()


class FakeEngine:
    cancellable = False
    supports_progress = False
    resumable = False

    def __init__(self):
        self.active = 0
        self.peak = 0
        self.calls = []

    def status(self):
        return {"loaded": False, "device": "test", "sample_rate": None, "gpu": {"cuda_available": False}}

    def probe(self, path):
        with wave.open(str(path)) as handle:
            return {"duration_seconds": handle.getnframes() / handle.getframerate(), "sample_rate": handle.getframerate()}

    def generate(self, parameters, path):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            self.calls.append(parameters)
            time.sleep(0.02)
            if parameters["text"] == "FAIL":
                raise RuntimeError("sensitive/internal/path")
            path.write_bytes(wav_bytes())
            return {"duration_seconds": 0.2, "sample_rate": 16000}
        finally:
            self.active -= 1


@pytest.fixture
def studio(tmp_path):
    engine = FakeEngine()
    settings = {"TESTING": True, **{key: str(tmp_path / key.lower()) for key in (
        "UPLOAD_DIR", "OUTPUT_DIR", "STATE_DIR", "STATIC_DIR")}}
    app = create_app(settings, engine=engine, start_worker=False)
    yield app, app.test_client(), engine, settings
    app.extensions["queue"].stop()


def upload(client):
    response = client.post("/api/upload_prompt", data={"file": (io.BytesIO(wav_bytes()), "我的声音.wav")})
    assert response.status_code == 200
    return response.get_json()["upload_id"]


def test_voice_snapshot_persistence_and_range(studio):
    app, client, engine, settings = studio
    upload_id = upload(client)
    voice = client.post("/api/voices", json={
        "name": "我的旁白", "kind": "clone", "upload_id": upload_id, "prompt_text": "参考原文",
        "parameters": {"task": "TTS"}, "tags": ["中文"], "favorite": True,
    }).get_json()
    assert voice["parameters"]["use_spk_emb"] is True
    job = client.post("/api/jobs", json={"voice_id": voice["id"], "text": "你好"}).get_json()
    client.patch(f"/api/voices/{voice['id']}", json={"name": "改名", "prompt_text": "新参考"})
    client.delete(f"/api/voices/{voice['id']}")
    assert app.extensions["queue"].run_one()
    saved = client.get(f"/api/jobs/{job['id']}").get_json()
    assert saved["status"] == "completed"
    assert saved["voice_name"] == "我的旁白"
    assert engine.calls[0]["prompt_text"] == "参考原文"
    assert "prompt_wav_path" not in saved["parameters"]
    assert "voice_id" not in saved["parameters"]
    retry = client.post("/api/jobs", json=saved["parameters"])
    assert retry.status_code == 202
    assert client.get(saved["audio_url"], headers={"Range": "bytes=0-15"}).status_code == 206
    client.patch(f"/api/jobs/{job['id']}", json={"favorite": True})
    restored = create_app(settings, engine=FakeEngine(), start_worker=False)
    assert restored.test_client().get("/api/jobs?favorite=1&q=你好").get_json()["total"] == 1
    assert restored.test_client().get(voice["audio_url"]).status_code == 200
    restored.extensions["queue"].stop()
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert client.get(saved["audio_url"]).status_code == 404


@pytest.mark.parametrize("parameters", [
    {"text": ""}, {"text": "a" * 2001}, {"text": "ok", "task": "wrong"},
    {"text": "ok", "cfg": "NaN"}, {"text": "ok", "temperature": float("nan")},
    {"text": "ok", "max_decode_steps": 100.2}, {"text": "ok", "use_spk_emb": "true"},
    {"text": "ok", "prompt_wav_path": "/not-allowed.wav"},
    {"text": "ok", "upload_id": "../private.wav"},
    {"text": "ok", "task": "IP"}, {"text": "ok", "task": "MULTILINGUAL"},
    {"text": "ok", "task": []}, {"text": "ok", "voice_id": ["invalid"]},
    {"text": "ok", "cfg": 10 ** 400},
])
def test_bad_parameters_never_reach_engine(studio, parameters):
    _, client, engine, _ = studio
    assert client.post("/api/jobs", json=parameters).status_code in {400, 404}
    assert not engine.calls


def test_upload_validation_symlinks_and_request_size(studio):
    app, client, _, settings = studio
    assert client.post("/api/upload_prompt", data={"file": (io.BytesIO(b"broken"), "a.wav")}).status_code == 400
    assert client.post("/api/upload_prompt", data={"file": (io.BytesIO(b"x"), "a.txt")}).status_code == 400
    from pathlib import Path
    outside = Path(settings["STATE_DIR"]) / "private.wav"
    outside.write_bytes(wav_bytes())
    (Path(settings["UPLOAD_DIR"]) / "link.wav").symlink_to(outside)
    assert client.post("/api/jobs", json={"text": "ok", "upload_id": "link.wav"}).status_code == 404
    app.config["MAX_CONTENT_LENGTH"] = 100
    assert client.post("/api/upload_prompt", data={"file": (io.BytesIO(wav_bytes()), "a.wav")}).status_code == 413


def test_queue_bound_cancel_and_terminal_deletion(studio):
    app, client, _, _ = studio
    ids = [client.post("/api/jobs", json={"text": f"排队 {i}"}).get_json()["id"] for i in range(12)]
    assert client.post("/api/jobs", json={"text": "满了"}).status_code == 429
    assert client.delete(f"/api/jobs/{ids[0]}").status_code == 409
    assert client.post(f"/api/jobs/{ids[0]}/cancel").get_json()["status"] == "cancelled"
    app.extensions["queue"].run_one()
    assert client.post(f"/api/jobs/{ids[1]}/cancel").status_code == 409
    assert client.get("/api/status").get_json()["queue"] == {"queued": 10, "running": 0}


def test_restart_recovers_running_and_preserves_queued(studio):
    app, client, _, settings = studio
    interrupted = client.post("/api/jobs", json={"text": "被中断"}).get_json()["id"]
    queued = client.post("/api/jobs", json={"text": "等待中"}).get_json()["id"]
    app.extensions["queue"]._claim()
    restored = create_app(settings, engine=FakeEngine(), start_worker=False)
    other = restored.test_client()
    assert other.get(f"/api/jobs/{interrupted}").get_json()["status"] == "failed"
    assert other.get(f"/api/jobs/{queued}").get_json()["status"] == "queued"
    restored.extensions["queue"].run_one()
    assert other.get(f"/api/jobs/{queued}").get_json()["status"] == "completed"
    restored.extensions["queue"].stop()


def test_failure_has_no_audio_or_private_error(studio):
    app, client, _, _ = studio
    job = client.post("/api/jobs", json={"text": "FAIL"}).get_json()
    app.extensions["queue"].run_one()
    result = client.get(f"/api/jobs/{job['id']}").get_json()
    assert result["status"] == "failed"
    assert result["audio_url"] is None
    assert "sensitive" not in result["error"]


def test_worker_serializes_parallel_http_requests(studio):
    app, _, engine, _ = studio
    queue = app.extensions["queue"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda i: app.test_client().post("/api/jobs", json={"text": str(i)}).status_code, range(8)))
    assert responses == [202] * 8
    queue.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if sum(app.extensions["store"].counts()[0].values()) == 0:
            break
        time.sleep(0.03)
    assert engine.peak == 1
    assert len(engine.calls) == 8
    assert all(job["status"] == "completed" for job in app.extensions["store"].jobs()[0])


def test_design_builtin_and_cross_origin_rejection(studio):
    _, client, _, _ = studio
    for kind, parameters in [("builtin", {"ip": "灵小柔"}), ("design", {"style": "温暖的旁白"})]:
        response = client.post("/api/voices", json={"name": kind, "kind": kind, "parameters": parameters})
        assert response.status_code == 201
        assert response.get_json()["parameters"]["use_zero_spk_emb"] is True
    assert client.post("/api/jobs", json={"text": "x"}, headers={"Origin": "https://elsewhere.invalid"}).status_code == 403
    assert client.post("/api/jobs", json=[]).status_code == 400
    assert client.get("/api/jobs?limit=bad").status_code == 400
    assert client.post("/api/voices", json={"name": "invalid", "kind": []}).status_code == 400


def test_partial_voice_parameter_update_keeps_other_settings(studio):
    _, client, _, _ = studio
    item = client.post("/api/voices", json={
        "name": "设计声音", "kind": "design", "parameters": {"style": "温暖的旁白", "seed": 42},
    }).get_json()
    response = client.patch(f"/api/voices/{item['id']}", json={"parameters": {"cfg": 3}})
    assert response.status_code == 200
    assert response.get_json()["parameters"]["seed"] == 42
    assert response.get_json()["parameters"]["style"] == "温暖的旁白"
    assert response.get_json()["parameters"]["cfg"] == 3


def test_missing_audio_does_not_block_history_deletion(studio):
    app, client, _, _ = studio
    job = client.post("/api/jobs", json={"text": "生成后文件被清理"}).get_json()
    app.extensions["queue"].run_one()
    filename = app.extensions["store"].get("jobs", job["id"])["filename"]
    (app.extensions["queue"].outputs / filename).unlink()
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200


def test_worker_lease_blocks_second_app_until_shutdown(studio):
    _, _, _, settings = studio
    first = create_app(settings, engine=FakeEngine(), start_worker=True)
    try:
        with pytest.raises(RuntimeError, match="已有实例"):
            create_app(settings, engine=FakeEngine(), start_worker=True)
    finally:
        first.extensions["queue"].stop()
    next_app = create_app(settings, engine=FakeEngine(), start_worker=True)
    next_app.extensions["queue"].stop()


def test_relative_audio_paths_resolve_consistently(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app({"UPLOAD_DIR": "uploads", "OUTPUT_DIR": "outputs", "STATE_DIR": "state"},
                     engine=FakeEngine(), start_worker=False)
    client = app.test_client()
    identifier = upload(client)
    assert client.get(f"/api/uploads/{identifier}").status_code == 200
    app.extensions["queue"].stop()
