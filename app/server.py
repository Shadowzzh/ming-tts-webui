"""Same-origin HTTP API for the Ming voice studio."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from app import config
from app.jobs import TERMINAL, JobQueue, WorkerLease, now
from app.storage import Store
from app.validation import (
    ALLOWED_AUDIO,
    MAX_QUEUE,
    MAX_REFERENCE_SECONDS,
    MAX_TEXT,
    MAX_UPLOAD_MB,
    APIError,
    boolean,
    params,
    safe_file,
    string,
    voice,
)

VERSION = "2.0.1"


def create_app(settings=None, *, engine=None, start_worker=True):
    base = Path(__file__).resolve().parent.parent
    settings = settings or {}
    app = Flask(__name__, static_folder=None)
    app.config.update(
        UPLOAD_DIR=os.environ.get("MING_UPLOAD_DIR", str(base / "uploads")),
        OUTPUT_DIR=os.environ.get("MING_OUTPUT_DIR", str(base / "outputs")),
        STATE_DIR=os.environ.get("MING_STATE_DIR", str(base / "state")),
        STATIC_DIR=os.environ.get("MING_STATIC_DIR", str(base / "static")),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
        JSON_AS_ASCII=False,
    )
    app.config.update(settings)
    provider = DefaultJSONProvider(app)
    provider.ensure_ascii = False
    app.json = provider
    uploads, outputs, state, static = [Path(app.config[key]).resolve() for key in (
        "UPLOAD_DIR", "OUTPUT_DIR", "STATE_DIR", "STATIC_DIR")]
    for directory in (uploads, outputs, state):
        directory.mkdir(parents=True, exist_ok=True)
    lease = WorkerLease(state) if start_worker else None
    try:
        store = Store(state / "studio.sqlite3")
        if engine is None:
            from app.runtime import MingEngine
            engine = MingEngine(os.environ.get("MING_MODEL_PATH", "/models/Ming-3.0-tts-0.5B"),
                                os.environ.get("MING_DEVICE", "cuda:0"))
        queue = JobQueue(store, engine, uploads, outputs, start=start_worker, lease=lease)
    except Exception:
        if lease:
            lease.close()
        raise
    app.extensions.update(store=store, queue=queue, engine=engine)

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise APIError("请求需要有效的 JSON 对象")
        return data

    def get_item(table, item_id):
        item = store.get(table, item_id)
        if item is None:
            raise APIError("记录不存在", 404)
        return item

    def public_voice(item):
        item = dict(item)
        item["audio_url"] = f"/api/uploads/{item['upload_id']}" if item.get("upload_id") else None
        return item

    def public_job(item):
        item = dict(item)
        filename = item.get("filename")
        item.update(audio_url=f"/api/audio/{filename}" if filename else None,
                    download_url=f"/api/download/{filename}" if filename else None)
        return item

    @app.before_request
    def same_origin():
        if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                raise APIError("仅接受来自本工作台的操作", 403)

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if request.path.startswith("/api/") or request.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(APIError)
    def validation_error(error):
        return jsonify(error=error.message), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        if error.code == 413:
            return jsonify(error=f"文件不能超过 {MAX_UPLOAD_MB} MB"), 413
        return jsonify(error={404: "页面或文件不存在", 405: "请求方法不支持"}.get(error.code, "请求格式不正确")), error.code

    @app.errorhandler(Exception)
    def internal_error(error):
        app.logger.exception("Unhandled request error")
        return jsonify(error="服务暂时无法完成请求，请查看服务日志"), 500

    @app.get("/api/options")
    def options():
        return jsonify(tasks=config.TASKS, languages=config.LANGUAGES, emotions=config.EMOTIONS,
                       speed=config.SPEED_VALUES, pitch=config.PITCH_VALUES, volume=config.VOLUME_VALUES,
                       dialects=config.DIALECT_VALUES, builtin_voices=config.BUILTIN_VOICES,
                       defaults=config.DEFAULTS, paralinguistic_tags=config.PARALINGUISTIC_TAGS,
                       limits={"max_text_length": MAX_TEXT, "max_upload_mb": MAX_UPLOAD_MB,
                               "max_reference_seconds": MAX_REFERENCE_SECONDS, "max_queue_size": MAX_QUEUE})

    @app.get("/api/status")
    def status():
        counts, storage = store.counts()
        return jsonify(**engine.status(), queue=counts, storage=storage, version=VERSION)

    @app.post("/api/upload_prompt")
    def upload():
        file = request.files.get("file")
        if not file or not file.filename:
            raise APIError("请选择参考音频")
        extension = Path(file.filename).suffix.lower()
        if extension not in ALLOWED_AUDIO:
            raise APIError("支持 WAV、MP3、FLAC、OGG、M4A 格式")
        # Only the generated identifier is used on disk; original names are display metadata.
        upload_id = uuid.uuid4().hex + extension
        path = uploads / upload_id
        file.save(path)
        try:
            info = engine.probe(path)
            if info["duration_seconds"] > MAX_REFERENCE_SECONDS:
                raise APIError(f"参考音不能超过 {MAX_REFERENCE_SECONDS} 秒，建议使用清晰的 5–30 秒录音")
        except Exception as error:
            path.unlink(missing_ok=True)
            if isinstance(error, APIError):
                raise
            raise APIError("无法解码该音频，请确认文件有效或转为 WAV 后上传") from error
        return jsonify(upload_id=upload_id, filename=file.filename, audio_url=f"/api/uploads/{upload_id}", **info)

    @app.get("/api/uploads/<filename>")
    def reference_audio(filename):
        path = safe_file(uploads, filename)
        if path.suffix.lower() not in ALLOWED_AUDIO:
            raise APIError("音频不存在", 404)
        return send_file(path, conditional=True, download_name=path.name)

    @app.get("/api/voices")
    def voices():
        items = sorted(store.voices(), key=lambda x: (x["favorite"], x["updated_at"]), reverse=True)
        return jsonify(items=[public_voice(x) for x in items])

    @app.post("/api/voices")
    def add_voice():
        with store.lock:
            item = {**voice(body(), uploads), "id": uuid.uuid4().hex, "created_at": now(), "updated_at": now()}
            store.put("voices", item)
        return jsonify(public_voice(item)), 201

    @app.patch("/api/voices/<item_id>")
    def update_voice(item_id):
        with store.lock:
            old = get_item("voices", item_id)
            item = {**old, **voice(body(), uploads, old), "updated_at": now()}
            store.put("voices", item)
        return jsonify(public_voice(item))

    @app.delete("/api/voices/<item_id>")
    def delete_voice(item_id):
        with store.lock:
            get_item("voices", item_id)
            store.delete("voices", item_id)
        # Keep the source clip: historical job snapshots may still refer to it.
        return jsonify(ok=True)

    @app.get("/api/jobs")
    def jobs():
        try:
            limit = max(1, min(200, int(request.args.get("limit", 100))))
        except (TypeError, ValueError) as error:
            raise APIError("limit 需要是整数") from error
        items, total = store.jobs(query=request.args.get("q", "")[:200],
                                  favorite=request.args.get("favorite") == "1", limit=limit)
        return jsonify(items=[public_job(x) for x in items], total=total)

    @app.post("/api/jobs")
    def submit():
        data = body()
        with store.lock:
            voice_id = data.get("voice_id")
            if voice_id is not None:
                voice_id = string(voice_id, "声音标识", 64)
            selected = get_item("voices", voice_id) if voice_id else None
            merged = {**(selected["parameters"] if selected else {}), **data}
            validated = params(merged, uploads)
            item = queue.submit(validated, selected)
        return jsonify(public_job(item)), 202

    @app.get("/api/jobs/<item_id>")
    def get_job(item_id):
        return jsonify(public_job(get_item("jobs", item_id)))

    @app.post("/api/jobs/<item_id>/cancel")
    def cancel_job(item_id):
        return jsonify(public_job(queue.cancel(item_id)))

    @app.patch("/api/jobs/<item_id>")
    def edit_job(item_id):
        data = body()
        with store.lock:
            job = get_item("jobs", item_id)
            if "favorite" in data:
                job["favorite"] = boolean(data["favorite"], "收藏")
            if "title" in data:
                job["title"] = string(data["title"], "标题", 120, required=True)
            store.put("jobs", job)
        return jsonify(public_job(job))

    @app.delete("/api/jobs/<item_id>")
    def delete_job(item_id):
        with store.lock:
            job = get_item("jobs", item_id)
            if job["status"] not in TERMINAL:
                raise APIError("请等待任务结束或先取消排队", 409)
            if job["filename"] and (outputs / job["filename"]).exists():
                safe_file(outputs, job["filename"]).unlink()
            store.delete("jobs", item_id)
        return jsonify(ok=True)

    @app.get("/api/audio/<filename>")
    def audio(filename):
        return send_file(safe_file(outputs, filename), mimetype="audio/wav", conditional=True)

    @app.get("/api/download/<filename>")
    def download(filename):
        return send_file(safe_file(outputs, filename), mimetype="audio/wav", conditional=True,
                         as_attachment=True, download_name=secure_filename(filename))

    @app.get("/")
    def index():
        return send_from_directory(static, "index.html")

    @app.get("/<path:filename>")
    def assets(filename):
        return send_from_directory(static, filename)

    return app


def main():
    from waitress import serve
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app()
    serve(app, host=os.environ.get("MING_BIND", "127.0.0.1"),
          port=int(os.environ.get("MING_PORT", "17863")), threads=8,
          max_request_body_size=MAX_UPLOAD_MB * 1024 * 1024, channel_timeout=900)


if __name__ == "__main__":
    main()
