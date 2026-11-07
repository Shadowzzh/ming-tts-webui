"""Optional, atomic postprocessing for generated mono PCM16 WAV files.

Only ``light_denoise_v1`` needs NumPy and ffmpeg. No model/GPU imports occur
here. Alignment uses at most the first five seconds; silence, weak correlation
and ambiguous periodic matches fail without replacing the source file.
"""
from __future__ import annotations

import math
import os
import stat
import subprocess
import tempfile
import wave
from pathlib import Path

_LIGHT_FILTER = "highpass=f=60,afftdn=nr=8:nf=-35:tn=0:gs=8"
_PEAK_LIMIT = 0.98
_TIMEOUT_SECONDS = 180


def _estimate_latency(original, processed, sample_rate: int) -> tuple[int, float]:
    """Find positive processing delay with normalized FFT cross-correlation.

    Every candidate uses the same reference length and its own centered output
    energy. A distant second peak within 0.02 of the winner is ambiguous (e.g.
    an unmodulated tone), even if the winning correlation is otherwise high.
    """
    import numpy as np

    count = min(len(original), len(processed), 5 * sample_rate)
    max_lag = min(sample_rate // 10, count // 4)
    window = count - max_lag
    if window < max(32, sample_rate // 20):
        raise ValueError("Audio is too short for confident latency alignment")
    reference = original[:window].astype(np.float64)
    reference -= reference.mean()
    candidate = processed[:count].astype(np.float64)
    reference_energy = float(np.dot(reference, reference))
    if reference_energy / window <= 1e-14:
        raise ValueError("Silent alignment reference")

    fft_size = 1 << (window + count - 2).bit_length()
    correlation = np.fft.irfft(
        np.fft.rfft(candidate, fft_size)
        * np.fft.rfft(reference[::-1], fft_size), fft_size,
    )[window - 1:window + max_lag]
    sums = np.concatenate(([0.0], np.cumsum(candidate)))
    squares = np.concatenate(([0.0], np.cumsum(candidate * candidate)))
    energies = (squares[window:window + max_lag + 1] - squares[:max_lag + 1]
                - (sums[window:window + max_lag + 1] - sums[:max_lag + 1]) ** 2 / window)
    scores = correlation / np.sqrt(reference_energy * np.maximum(energies, 1e-30))
    lag = int(np.argmax(scores))
    confidence = float(scores[lag])
    # Allow a broad local peak (filter phase response), but reject competing
    # pitch periods or unrelated material anywhere else in the search range.
    competitors = scores.copy()
    radius = max(1, sample_rate // 500)
    competitors[max(0, lag - radius):lag + radius + 1] = -np.inf
    if (not math.isfinite(confidence) or confidence < 0.5
            or confidence - float(np.max(competitors)) < 0.02):
        raise ValueError("Cannot confidently align filtered audio")
    return lag, confidence


def apply_audio_postprocess(path: Path, profile: str) -> dict:
    """Apply a versioned filter in place, returning JSON-safe provenance.

    ``none`` returns immediately without reading the file or importing NumPy.
    The light profile requires uncompressed mono PCM16 WAV and ffmpeg on PATH.
    Failures (including subprocess timeout and alignment rejection) propagate;
    the original is replaced only after a complete output has been written.
    Callers should ensure exclusive access to the source while processing.

    Delay compensation removes the measured prefix and pads the tail with
    zeros. RMS is matched over the entire original duration, subject to the
    0.98 peak ceiling; metadata records both requested and actual gain.
    """
    if profile == "none":
        return {"profile": profile, "applied": False}
    if profile != "light_denoise_v1":
        raise ValueError(f"Unsupported audio postprocess profile: {profile!r}")

    import numpy as np

    path = Path(path).absolute()
    source_mode = stat.S_IMODE(path.stat().st_mode)
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels() != 1 or source.getsampwidth() != 2
                or source.getcomptype() != "NONE"):
            raise ValueError("Expected uncompressed mono PCM16 WAV")
        sample_rate = source.getframerate()
        frame_count = source.getnframes()
        pcm = source.readframes(frame_count)
    if not frame_count or len(pcm) != frame_count * 2:
        raise ValueError("Empty or truncated PCM16 WAV")
    original = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    input_rms = float(np.sqrt(np.mean(original * original)))
    if input_rms <= 1e-7:
        raise ValueError("Silent original audio")

    # Both scratch data and the final WAV live on the source filesystem, so
    # os.replace is atomic. TemporaryDirectory also cleans up on exceptions.
    with tempfile.TemporaryDirectory(prefix=".audio-postprocess-", dir=path.parent) as scratch:
        scratch = Path(scratch)
        filtered_path = scratch / "filtered.f32"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(path), "-map", "0:a:0", "-af", _LIGHT_FILTER,
             "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_f32le",
             "-f", "f32le", str(filtered_path)],
            shell=False, check=True, timeout=_TIMEOUT_SECONDS,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if filtered_path.stat().st_size != frame_count * 4:
            raise ValueError("Filtered audio has an unexpected sample count")
        processed = np.fromfile(filtered_path, dtype="<f4").astype(np.float64)
        if not np.all(np.isfinite(processed)):
            raise ValueError("Filtered audio contains non-finite samples")
        if float(np.sqrt(np.mean(processed * processed))) <= 1e-7:
            raise ValueError("Silent filtered audio")
        latency, confidence = _estimate_latency(original, processed, sample_rate)
        aligned = np.zeros(frame_count, dtype=np.float64)
        aligned[:frame_count - latency] = processed[latency:]
        processed_rms = float(np.sqrt(np.mean(aligned * aligned)))
        peak = float(np.max(np.abs(aligned)))
        if processed_rms <= 1e-7 or peak <= 1e-7:
            raise ValueError("Silent aligned audio")
        rms_gain = input_rms / processed_rms
        gain = min(rms_gain, _PEAK_LIMIT / peak)
        aligned *= gain
        # Quantization must never round a peak above the stated ceiling.
        pcm_limit = math.floor(_PEAK_LIMIT * 32768)
        output_pcm = np.clip(np.rint(aligned * 32768), -pcm_limit, pcm_limit).astype("<i2")
        output_float = output_pcm.astype(np.float64) / 32768.0
        metadata = {
            "profile": profile,
            "applied": True,
            "sample_rate": sample_rate,
            "sample_count": frame_count,
            "duration": frame_count / sample_rate,
            "filter": _LIGHT_FILTER,
            "latency_samples": latency,
            "latency_ms": latency * 1000 / sample_rate,
            "alignment_correlation": confidence,
            "gain_db": 20 * math.log10(gain),
            "rms_gain_db": 20 * math.log10(rms_gain),
            "peak_limited": gain < rms_gain,
            "peak_limit": _PEAK_LIMIT,
            "input_rms": input_rms,
            "output_rms": float(np.sqrt(np.mean(output_float * output_float))),
            "input_peak": float(np.max(np.abs(original))),
            "output_peak": float(np.max(np.abs(output_float))),
        }
        output_path = scratch / "processed.wav"
        with output_path.open("wb") as output:
            with wave.open(output, "wb") as wav:
                wav.setparams((1, 2, sample_rate, frame_count, "NONE", "not compressed"))
                wav.writeframes(output_pcm.tobytes())
            output.flush()
            os.fsync(output.fileno())
        output_path.chmod(source_mode)
        os.replace(output_path, path)
    return metadata
