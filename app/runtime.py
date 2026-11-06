"""Production adapter. Imports GPU libraries only when the runtime is constructed."""
from __future__ import annotations

import importlib
import math
from pathlib import Path


class MingEngine:
    def __init__(self, model_path, device):
        from app.tts_wrapper import TTSWrapper
        self.wrapper = TTSWrapper(model_path, device=device, text_normalization=False)
        self.device = device

    def status(self):
        torch = importlib.import_module("torch")
        gpu = {"cuda_available": torch.cuda.is_available()}
        if gpu["cuda_available"]:
            gpu.update(name=torch.cuda.get_device_name(self.device),
                       memory_allocated_mb=round(torch.cuda.memory_allocated(self.device) / 1e6, 1),
                       memory_reserved_mb=round(torch.cuda.memory_reserved(self.device) / 1e6, 1))
        return {"loaded": self.wrapper.is_loaded, "device": self.device,
                "sample_rate": self.wrapper.sample_rate if self.wrapper.is_loaded else None, "gpu": gpu}

    def probe(self, path: Path):
        torchaudio = importlib.import_module("torchaudio")
        info = torchaudio.info(str(path))
        duration = info.num_frames / info.sample_rate if info.sample_rate > 0 else 0
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("empty or invalid audio")
        return {"duration_seconds": round(duration, 3), "sample_rate": info.sample_rate}

    def generate(self, params, path: Path):
        torch = importlib.import_module("torch")
        torchaudio = importlib.import_module("torchaudio")
        # A single queue worker owns all random state and all model calls.
        with torch.inference_mode():
            torch.manual_seed(params["seed"])
            waveform = self.wrapper.synthesize(params)
            rate = self.wrapper.sample_rate
            if waveform.numel() == 0 or not torch.isfinite(waveform).all() or waveform.abs().max().item() < 1e-7:
                raise ValueError("model returned empty, non-finite or silent audio")
            torchaudio.save(str(path), waveform.detach().cpu(), sample_rate=rate)
        return {"sample_rate": rate, "duration_seconds": round(waveform.shape[-1] / rate, 3)}
