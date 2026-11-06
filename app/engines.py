"""Choose the inference backend at startup.

``MING_BACKEND=local`` (default) keeps the original in-container GPU model.
``MING_BACKEND=ray`` dispatches each task to the shared Ray GPU executor, so the
web container needs no ``devices`` entry and never imports torch at runtime.
"""
from __future__ import annotations

import os


def build_engine(config: dict):
    backend = str(config.get("BACKEND") or os.environ.get("MING_BACKEND", "local")).strip().lower()
    if backend == "local":
        from app.runtime import MingEngine
        return MingEngine(
            config.get("MODEL_PATH") or os.environ.get("MING_MODEL_PATH", "/models/Ming-3.0-tts-0.5B"),
            config.get("DEVICE") or os.environ.get("MING_DEVICE", "cuda:0"),
        )
    if backend == "ray":
        from app.ray_engine import RayEngine
        return RayEngine(
            config.get("RAY_JOBS_URL") or os.environ.get("RAY_JOBS_URL"),
            profile=config.get("RAY_PROFILE") or os.environ.get("MING_RAY_PROFILE", "ming"),
            dispatch_dir=config.get("DISPATCH_DIR") or os.environ.get("GPU_DISPATCH_DIR", "/app/dispatch"),
            inputs_dir=config.get("INPUTS_DIR") or os.environ.get("MING_INPUTS_DIR", "/inputs"),
            timeout_seconds=float(config.get("RAY_TIMEOUT_SECONDS")
                                  or os.environ.get("MING_RAY_TIMEOUT_SECONDS", "3600")),
            poll_seconds=float(config.get("RAY_POLL_SECONDS")
                               or os.environ.get("MING_RAY_POLL_SECONDS", "1.0")),
        )
    raise RuntimeError(f"不支持的推理后端：{backend}（可选 local / ray）")
