"""GPU worker entrypoint for the shared Ray executor.

Run as ``python -m app.infer_job /job`` from the one-shot model container. The
generic executor mounts the shared dispatch directory at ``/job`` and passes
only the fixed profile; this module performs the Ming-3.0-tts synthesis and is
otherwise responsible for nothing (locking and container recycling belong to the
executor/guard).

Contract:

* read ``/job/request.json`` (business JSON, never printed to the log);
* write ``/job/result.wav`` and ``/job/result.json`` atomically;
* exit ``0`` only when both files are complete, non-zero otherwise, leaving the
  partial output in place for diagnosis.

The request shape is ``{"job_id", "profile", "parameters"}`` where
``parameters`` is the validated web request. ``prompt_wav_path`` points at the
read-only ``/inputs/<file>`` mount managed by the RayEngine client.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

EXIT_USAGE = 2
EXIT_FAILED = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _confine_prompt(value, inputs_dir: str) -> str:
    """Return the resolved reference clip, confined to the read-only input mount.

    The web layer has already mapped ``prompt_wav_path`` to ``/inputs/<name>``, but
    the worker is the last line of defence: it must reject absolute paths elsewhere,
    ``..`` traversal and symlinks that escape the mount.
    """
    if not isinstance(value, str) or not value.strip():
        raise TypeError("prompt_wav_path 必须是非空字符串")
    root = Path(inputs_dir).resolve()
    raw = Path(value)
    if not raw.is_absolute():
        raise ValueError(f"prompt_wav_path 必须是绝对路径：{value!r}")
    if raw.is_symlink():
        raise ValueError("prompt_wav_path 不能是符号链接")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise ValueError("参考音不存在或无法访问") from error
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"prompt_wav_path 必须位于 {root} 内")
    if not resolved.is_file():
        raise ValueError("prompt_wav_path 不是普通文件")
    if resolved.is_symlink():  # resolve(strict=True) already followed it; be explicit
        raise ValueError("prompt_wav_path 不能是符号链接")
    return str(resolved)


def load_request(job_dir: Path) -> dict:
    with (job_dir / "request.json").open("r", encoding="utf-8") as handle:
        request = json.load(handle)
    if not isinstance(request, dict):
        raise TypeError("request.json 必须是 JSON 对象")
    parameters = request.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("request.json 缺少 parameters")
    if not isinstance(parameters.get("text"), str) or not parameters["text"].strip():
        raise ValueError("parameters.text 为空")
    prompt = parameters.get("prompt_wav_path")
    if prompt is not None:
        inputs_dir = os.environ.get("MING_INPUTS_DIR", "/inputs")
        resolved = _confine_prompt(prompt, inputs_dir)
        # Re-point the model at the verified path rather than trusting the request.
        parameters["prompt_wav_path"] = resolved
    return request


def synthesize(request: dict, job_dir: Path) -> dict:
    """Run the model and publish result.wav/result.json atomically."""
    from app.runtime import MingEngine

    parameters = dict(request["parameters"])
    model_path = os.environ.get("MING_MODEL_PATH", "/models/Ming-3.0-tts-0.5B")
    device = os.environ.get("MING_DEVICE", "cuda:0")
    # The model is loaded per task in the one-shot container; nothing is cached.
    engine = MingEngine(model_path, device)
    partial = job_dir / "result.partial.wav"
    result_path = job_dir / "result.json"
    try:
        # The container is "running" while the checkpoint loads, so measure the two
        # phases explicitly instead of calling the whole container run inference.
        load_start = time.monotonic()
        engine.wrapper.load()
        model_load_seconds = round(time.monotonic() - load_start, 3)
        checkpoint_load_seconds = engine.wrapper.load_seconds
        inference_start = time.monotonic()
        metadata = engine.generate(parameters, partial)
        inference_seconds = round(time.monotonic() - inference_start, 3)
        if not partial.is_file() or partial.stat().st_size <= 44:
            raise RuntimeError("模型未写出有效 WAV")
        os.replace(partial, job_dir / "result.wav")
        payload = {
            "job_id": request.get("job_id"),
            "profile": request.get("profile"),
            "sample_rate": metadata.get("sample_rate"),
            "duration_seconds": metadata.get("duration_seconds"),
            "model_load_seconds": model_load_seconds,
            "checkpoint_load_seconds": (round(float(checkpoint_load_seconds), 3)
                                       if isinstance(checkpoint_load_seconds, (int, float)) else None),
            "inference_seconds": inference_seconds,
            "finished_at": _utc_now(),
        }
        _write_json(result_path, payload)
        return payload
    finally:
        partial.unlink(missing_ok=True)


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    job_dir = Path(arguments[0]) if arguments else Path(os.environ.get("GPU_JOB_DIR", "/job"))
    try:
        request = load_request(job_dir)
    except Exception:  # noqa: BLE001 - any read/parse problem is a usage error
        traceback.print_exc()
        print(f"[infer_job] 无法读取任务请求：{job_dir}/request.json", file=sys.stderr)
        return EXIT_USAGE
    print(f"[infer_job] job={request.get('job_id')} profile={request.get('profile')} "
          f"chars={len(request['parameters'].get('text', ''))}", flush=True)
    try:
        payload = synthesize(request, job_dir)
    except MemoryError:
        traceback.print_exc()
        print("[infer_job] 显存不足", file=sys.stderr)
        return EXIT_FAILED
    except Exception:  # noqa: BLE001 - the worker must fail loudly on any model error
        traceback.print_exc()
        print("[infer_job] 合成失败，保留 partial 输出", file=sys.stderr)
        return EXIT_FAILED
    print(f"[infer_job] 完成 {payload['duration_seconds']}s @ {payload['sample_rate']}Hz", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
