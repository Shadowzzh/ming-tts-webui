"""Public execution capabilities, using only fake engines and temporary storage."""
import pytest  # pyright: ignore[reportMissingImports] -- available in the isolated test environment
from test_studio import FakeEngine

from app.server import create_app


@pytest.fixture(params=[False, True], ids=["local", "capable-engine"])
def studio(request, tmp_path):
    engine = FakeEngine()
    # Capabilities, rather than a backend name, define the public contract.
    if request.param:
        engine.cancellable = True
        engine.supports_progress = True
        engine.resumable = True
    settings = {"TESTING": True, **{key: str(tmp_path / key.lower()) for key in (
        "UPLOAD_DIR", "OUTPUT_DIR", "STATE_DIR", "STATIC_DIR")}}
    app = create_app(settings, engine=engine, start_worker=False)
    yield app, app.test_client(), request.param
    app.extensions["queue"].stop()


def test_status_reports_engine_capabilities(studio):
    _, client, capable = studio
    state = client.get("/api/status").get_json()
    assert state["backend"] == "local"
    assert state["capabilities"] == {
        "cancel_running": capable, "reports_progress": capable, "resume_running": capable,
    }
    assert {"loaded", "device", "gpu", "queue", "storage", "version"} <= state.keys()


def test_all_job_responses_report_current_cancel_capability(studio):
    app, client, capable = studio
    response = client.post("/api/jobs", json={"text": "能力回归"})
    assert response.status_code == 202
    submitted = response.get_json()
    assert submitted["can_cancel"] is True
    job_id = submitted["id"]
    assert client.get("/api/jobs").get_json()["items"][0]["can_cancel"] is True
    assert client.get(f"/api/jobs/{job_id}").get_json()["can_cancel"] is True
    assert client.patch(f"/api/jobs/{job_id}", json={"favorite": True}).get_json()["can_cancel"] is True
    assert "can_cancel" not in app.extensions["store"].get("jobs", job_id)

    app.extensions["queue"]._claim()
    # Even a historical backend name or stale stored flag cannot grant capability.
    stored = app.extensions["store"].get("jobs", job_id)
    stored.update(backend="ray", can_cancel=not capable)
    app.extensions["store"].put("jobs", stored)
    assert client.get(f"/api/jobs/{job_id}").get_json()["can_cancel"] is capable
    assert client.get("/api/jobs").get_json()["items"][0]["can_cancel"] is capable
    assert client.patch(f"/api/jobs/{job_id}", json={"title": "改名"}).get_json()["can_cancel"] is capable


@pytest.mark.parametrize("pending", [{"phase": "cancelling"}, {"phase": "postprocessing"}, {"cancel_requested": True}])
def test_stop_in_progress_cannot_be_requested_again(studio, pending):
    app, client, _ = studio
    job = client.post("/api/jobs", json={"text": "停止中"}).get_json()
    app.extensions["queue"]._claim()
    stored = app.extensions["store"].get("jobs", job["id"])
    stored.update(pending)
    app.extensions["store"].put("jobs", stored)
    assert client.get(f"/api/jobs/{job['id']}").get_json()["can_cancel"] is False


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_jobs_never_offer_cancel(studio, status):
    app, client, _ = studio
    job = client.post("/api/jobs", json={"text": "已结束"}).get_json()
    stored = app.extensions["store"].get("jobs", job["id"])
    stored.update(status=status, can_cancel=True)
    app.extensions["store"].put("jobs", stored)
    assert client.get(f"/api/jobs/{job['id']}").get_json()["can_cancel"] is False


def test_queued_cancel_response_disables_cancel(studio):
    _, client, _ = studio
    job = client.post("/api/jobs", json={"text": "取消排队"}).get_json()
    response = client.post(f"/api/jobs/{job['id']}/cancel")
    assert response.status_code == 200
    assert response.get_json()["status"] == "cancelled"
    assert response.get_json()["can_cancel"] is False


def test_local_running_cancel_still_rejected_then_completes(tmp_path):
    engine = FakeEngine()
    settings = {key: str(tmp_path / key.lower()) for key in (
        "UPLOAD_DIR", "OUTPUT_DIR", "STATE_DIR", "STATIC_DIR")}
    app = create_app(settings, engine=engine, start_worker=False)
    try:
        client = app.test_client()
        job = client.post("/api/jobs", json={"text": "本地继续完成"}).get_json()
        claimed = app.extensions["queue"]._claim()
        assert client.get(f"/api/jobs/{job['id']}").get_json()["can_cancel"] is False
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
        app.extensions["queue"]._execute(claimed)
        result = client.get(f"/api/jobs/{job['id']}").get_json()
        assert result["status"] == "completed"
        assert result["can_cancel"] is False
        assert client.get(result["audio_url"]).status_code == 200
    finally:
        app.extensions["queue"].stop()
