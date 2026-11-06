"""Thin wrapper around Ming-3.0-tts ``MingTTS`` for the web service.

Loads the model once (on the GPU) and exposes a single ``synthesize`` method
that maps the web request parameters onto ``MingTTS.synthesize``. The model is
loaded lazily on first use so the server can start without the weights.
"""

from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from typing import Any

from app import config

# The Ming-3.0-tts source tree is not pip-installable, so make sure it is on
# the import path. MING_REPO_DIR overrides the default (repo cloned next to
# this project as ``Ming-3.0-tts``).
_MING_REPO = os.environ.get(
    "MING_REPO_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "Ming-3.0-tts"),
)
if os.path.isdir(_MING_REPO) and _MING_REPO not in sys.path:
    sys.path.insert(0, _MING_REPO)


class TTSWrapper:
    def __init__(self, model_path: str, device: str = "cuda:0",
                 text_normalization: bool = False):
        self.model_path = model_path
        self.device = device
        # pynini (FST text normalization) has no Windows wheel and fails to
        # build, so it is disabled by default. Numbers/dates are still handled
        # by the lightweight ``normalize_numbers`` path inside MingTTS.
        self.text_normalization = text_normalization
        self._model: Any = None
        self._lock = threading.Lock()
        self._loaded_at = None
        self._sample_rate: int | None = None

    # -- loading ---------------------------------------------------------
    @property
    def model(self) -> Any:
        if self._model is None:
            self.load()
        return self._model

    def load(self):
        """Load the checkpoint onto the GPU. Thread-safe, runs once."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            MingTTS = importlib.import_module("ming_tts").MingTTS

            if not os.path.isdir(self.model_path) and not os.path.exists(
                os.path.join(self.model_path, "config.json")
            ):
                # Allow a Hub repo id; MingTTS downloads it.
                pass
            print(f"[tts] loading model from {self.model_path} onto {self.device} ...")
            t0 = time.time()
            self._model = MingTTS(
                self.model_path,
                device=self.device,
                text_normalization=self.text_normalization,
            )
            self._sample_rate = self._model.sample_rate
            self._loaded_at = time.time() - t0
            print(f"[tts] model ready in {self._loaded_at:.1f}s, "
                  f"sample_rate={self._sample_rate}")

    def unload(self):
        with self._lock:
            if self._model is not None:
                del self._model
                self._model = None
                torch = importlib.import_module("torch")
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                print("[tts] model unloaded")

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def load_seconds(self):
        """Seconds spent loading the checkpoint (``None`` until loaded)."""
        return self._loaded_at

    @property
    def sample_rate(self) -> int:
        if self._sample_rate is None:
            self.load()
        if self._sample_rate is None:
            raise RuntimeError("模型未返回有效采样率")
        return self._sample_rate

    # -- synthesis -------------------------------------------------------
    def synthesize(self, params: dict):
        """Run one synthesis job.

        ``params`` is the validated request dict from the API. Returns the
        path to a written WAV file plus metadata.
        """
        task = params["task"]
        text = params["text"]
        language_id = params.get("language_id") or None
        instruction = params.get("instruction") or None
        prompt_wav_path = params.get("prompt_wav_path") or None
        prompt_text = params.get("prompt_text") or None
        use_spk_emb = bool(params.get("use_spk_emb", False))
        use_zero_spk_emb = bool(params.get("use_zero_spk_emb", False))

        # Build the ``controls`` dict for structured tasks.
        controls = {}
        for key in config.TASK_CONTROL_FIELDS.get(task, []):
            val = params.get(key)
            if val:
                controls[key] = val
        if task == "FREEFORM":
            # Freeform uses ``instruction`` only; never ``controls``.
            controls = None
        elif task == "IP":
            controls = {"ip": params.get("ip")} if params.get("ip") else None

        # Voice-design / freeform / built-in voices do not use a reference
        # prompt by default; they rely on ``use_zero_spk_emb``.
        if task in ("VOICE_DESIGN", "FREEFORM", "IP"):
            prompt_wav_path = None
            prompt_text = None
            use_spk_emb = False
            use_zero_spk_emb = True

        # MULTILINGUAL requires a language id.
        if task == "MULTILINGUAL" and not language_id:
            language_id = "en"

        kwargs = {
            "task": task,
            "text": text,
            "language_id": language_id,
            "instruction": instruction,
            "controls": controls,
            "prompt_wav_path": prompt_wav_path,
            "prompt_text": prompt_text,
            "use_spk_emb": use_spk_emb,
            "use_zero_spk_emb": use_zero_spk_emb,
            "cfg": float(params.get("cfg", config.DEFAULTS["cfg"])),
            "sigma": float(params.get("sigma", config.DEFAULTS["sigma"])),
            "temperature": float(params.get("temperature", config.DEFAULTS["temperature"])),
            "max_length": int(params.get("max_length", config.DEFAULTS["max_length"])),
            "max_decode_steps": int(params.get("max_decode_steps", config.DEFAULTS["max_decode_steps"])),
            "trim_silence": not bool(params.get("no_trim_silence", False)),
            "raw_text": bool(params.get("raw_text", False)),
        }

        waveform = self.model.synthesize(**kwargs)
        return waveform
