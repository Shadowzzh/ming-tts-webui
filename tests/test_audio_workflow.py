"""The stored postprocess choice must reach the output, never the model."""
import pytest  # pyright: ignore[reportMissingImports] -- available in the isolated test environment
from test_studio import FakeEngine, upload, wav_bytes

from app import audio_processing
from app.server import create_app


@pytest.fixture
def studio(tmp_path):
    engine = FakeEngine()
    engine.cancellable = True
    settings = {"TESTING": True, **{key: str(tmp_path / key.lower()) for key in (
        "UPLOAD_DIR", "OUTPUT_DIR", "STATE_DIR", "STATIC_DIR")}}
    app = create_app(settings, engine=engine, start_worker=False)
    yield app, app.test_client(), engine
    app.extensions["queue"].stop()


def test_saved_voice_applies_processing_and_retains_snapshot(studio, monkeypatch):
    app, client, engine = studio
    identifier = upload(client)
    voice = client.post("/api/voices", json={
        "name": "降噪测试", "upload_id": identifier, "prompt_text": "参考文字",
        "parameters": {"audio_postprocess": "light_denoise_v1", "seed": 42},
    }).get_json()
    updated = client.patch(f"/api/voices/{voice['id']}", json={"parameters": {"cfg": 3}}).get_json()
    assert updated["parameters"]["audio_postprocess"] == "light_denoise_v1"
    job = client.post("/api/jobs", json={"voice_id": voice["id"], "text": "你好"}).get_json()
    client.patch(f"/api/voices/{voice['id']}", json={"parameters": {"audio_postprocess": "none"}})
    processed = wav_bytes(seconds=0.3)
    calls = []

    def process(path, profile):
        calls.append((path, profile))
        assert path.read_bytes() == wav_bytes()
        current = client.get(f"/api/jobs/{job['id']}").get_json()
        assert current["phase"] == "postprocessing" and not current["can_cancel"]
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
        path.write_bytes(processed)
        return {"profile": profile, "applied": True}

    monkeypatch.setattr(audio_processing, "apply_audio_postprocess", process)
    assert app.extensions["queue"].run_one()
    result = client.get(f"/api/jobs/{job['id']}").get_json()
    assert result["status"] == "completed" and len(calls) == 1
    assert result["parameters"]["audio_postprocess"] == "light_denoise_v1"
    assert result["audio_processing"] == {"profile": "light_denoise_v1", "applied": True}
    assert client.get(result["audio_url"]).data == processed
    assert "audio_postprocess" not in engine.calls[0]
    retried = client.post("/api/jobs", json=result["parameters"]).get_json()
    assert retried["parameters"]["audio_postprocess"] == "light_denoise_v1"


def test_processing_failure_does_not_publish_raw_audio(studio, monkeypatch):
    app, client, _ = studio
    def fail(path, profile):
        raise RuntimeError("private filter error")
    monkeypatch.setattr(audio_processing, "apply_audio_postprocess", fail)
    job = client.post("/api/jobs", json={"text": "你好", "audio_postprocess": "light_denoise_v1"}).get_json()
    app.extensions["queue"].run_one()
    result = client.get(f"/api/jobs/{job['id']}").get_json()
    assert result["status"] == "failed" and result["audio_url"] is None
    assert not list(app.extensions["queue"].outputs.iterdir())
    assert "private" not in result["error"]


@pytest.mark.parametrize("value", [True, [], {}, "custom filter", "light_denoise_v2"])
def test_invalid_processing_rejected_before_queue(studio, value):
    _, client, engine = studio
    assert client.post("/api/jobs", json={"text": "你好", "audio_postprocess": value}).status_code == 400
    assert not engine.calls


@pytest.mark.parametrize("extra", [{}, {"audio_postprocess": "none"}])
def test_default_and_explicit_none_skip_processing(studio, monkeypatch, extra):
    app, client, engine = studio
    def forbidden(*args):
        raise AssertionError("Processing must not run")
    monkeypatch.setattr(audio_processing, "apply_audio_postprocess", forbidden)
    job = client.post("/api/jobs", json={"text": "你好", **extra}).get_json()
    app.extensions["queue"].run_one()
    result = client.get(f"/api/jobs/{job['id']}").get_json()
    assert result["status"] == "completed"
    assert client.get(result["audio_url"]).data == wav_bytes()
    assert "audio_postprocess" not in engine.calls[0]
