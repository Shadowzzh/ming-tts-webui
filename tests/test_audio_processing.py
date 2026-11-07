"""Synthetic audio only: no recordings, model packages or GPU are required."""
from __future__ import annotations

import builtins
import importlib.util
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from app import audio_processing as audio


@pytest.fixture
def np():
    return pytest.importorskip("numpy")


@pytest.fixture
def ffmpeg():
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is unavailable")


def write_wav(path, samples, sample_rate, np):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        wav.writeframes(np.rint(np.clip(samples, -1, 32767 / 32768) * 32768)
                        .astype("<i2").tobytes())


def read_wav(path, np):
    with wave.open(str(path), "rb") as wav:
        params = wav.getparams()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    return params, samples.astype(np.float64) / 32768


def voice_with_pauses(np, sample_rate, seconds=4.0):
    """Male-range changing pitch, harmonics, syllables, breath and floor noise."""
    rng = np.random.default_rng(1707)
    t = np.arange(round(seconds * sample_rate)) / sample_rate
    f0 = 112 + 17 * np.sin(2 * np.pi * 0.73 * t) + 7 * np.sin(2 * np.pi * 2.1 * t)
    phase = 2 * np.pi * np.cumsum(f0) / sample_rate
    harmonics = sum(np.sin(h * phase + h * 0.17) / h ** 1.3 for h in range(1, 17))
    envelope = np.zeros_like(t)
    for start, end in [(0.2, 1.15), (1.8, 2.55), (3.1, 3.8)]:
        position = np.clip((t - start) / (end - start), 0, 1)
        envelope += np.sin(np.pi * position) ** 0.6 * ((t > start) & (t < end))
    syllables = 0.65 + 0.35 * np.sin(2 * np.pi * 3.7 * t) ** 2
    voice = 0.20 * envelope * syllables * harmonics
    breath = rng.normal(0, 0.004, len(t)) * envelope
    noise = rng.normal(0, 0.007, len(t)) + 0.004 * np.sin(2 * np.pi * 35 * t)
    return voice + breath + noise


def test_none_does_not_import_numpy_or_touch_file(tmp_path, monkeypatch):
    path = tmp_path / "untouched.wav"
    path.write_bytes(b"none does not even decode its input")
    before = path.stat()
    real_import = builtins.__import__

    def block_numpy(name, *args, **kwargs):
        if name == "numpy" or name.startswith("numpy."):
            raise AssertionError("none imported numpy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_numpy)
    # Reload from a file to cover module import as well as the no-op call.
    spec = importlib.util.spec_from_file_location("isolated_audio_processing", audio.__file__)
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    assert isolated.apply_audio_postprocess(path, "none") == {"profile": "none", "applied": False}
    assert path.read_bytes() == b"none does not even decode its input"
    after = path.stat()
    assert (before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_ino, after.st_size, after.st_mtime_ns)
    assert isolated.apply_audio_postprocess(tmp_path / "missing.wav", "none")["applied"] is False


@pytest.mark.parametrize("profile", ["light", "", "LIGHT_DENOISE_V1", None])
def test_invalid_profile_rejected_before_file_access(tmp_path, profile):
    with pytest.raises(ValueError, match="Unsupported"):
        audio.apply_audio_postprocess(tmp_path / "missing.wav", profile)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("sample_rate", [24000, 44100, 48000])
def test_real_ffmpeg_denoise_alignment_rms_and_quiet_window(tmp_path, np, ffmpeg, sample_rate):
    path = tmp_path / "synthetic voice ; $literal.wav"
    write_wav(path, voice_with_pauses(np, sample_rate), sample_rate, np)
    path.chmod(0o640)
    before_params, original = read_wav(path, np)
    old_inode = path.stat().st_ino
    metadata = audio.apply_audio_postprocess(path, "light_denoise_v1")
    params, processed = read_wav(path, np)
    assert params == before_params
    assert path.stat().st_ino != old_inode
    assert path.stat().st_mode & 0o777 == 0o640
    assert len(processed) == len(original)
    assert metadata["applied"] is True
    assert metadata["profile"] == "light_denoise_v1"
    assert metadata["filter"] == "highpass=f=60,afftdn=nr=8:nf=-35:tn=0:gs=8"
    assert metadata["sample_rate"] == sample_rate
    assert metadata["sample_count"] == len(original)
    assert metadata["duration"] == len(original) / sample_rate
    # The high-pass phase response shifts the correlation peak slightly with
    # the waveform; measure latency rather than hard-coding the 44.1 kHz value.
    assert abs(metadata["latency_samples"] - sample_rate * 0.025) <= sample_rate * 0.002
    assert metadata["alignment_correlation"] > 0.8
    residual, correlation = audio._estimate_latency(original, processed, sample_rate)
    assert residual <= 2
    assert correlation > 0.8
    original_rms = np.sqrt(np.mean(original ** 2))
    output_rms = np.sqrt(np.mean(processed ** 2))
    assert not metadata["peak_limited"]
    assert abs(output_rms / original_rms - 1) < 0.001
    assert metadata["output_rms"] == pytest.approx(output_rms)
    quiet = slice(int(1.35 * sample_rate), int(1.65 * sample_rate))
    energy_ratio = np.mean(processed[quiet] ** 2) / np.mean(original[quiet] ** 2)
    assert energy_ratio < 0.65
    assert np.max(np.abs(processed)) <= 0.98
    assert np.all(processed[-metadata["latency_samples"]:] == 0)
    json.dumps(metadata, allow_nan=False)
    assert list(tmp_path.iterdir()) == [path]
    print(f"rate={sample_rate} latency={metadata['latency_samples']} "
          f"corr={metadata['alignment_correlation']:.6f} "
          f"rms_ratio={output_rms / original_rms:.6f} quiet_energy_ratio={energy_ratio:.6f}")


@pytest.fixture
def synthetic_path(tmp_path, np):
    path = tmp_path / "source.wav"
    write_wav(path, voice_with_pauses(np, 44100), 44100, np)
    return path


def fake_ffmpeg(monkeypatch, np, path, transform):
    """Inject known delays/bad output while checking the subprocess contract."""
    _, original = read_wav(path, np)

    def run(command, **kwargs):
        assert isinstance(command, list)
        assert command[0] == "ffmpeg"
        assert kwargs["shell"] is False
        assert 0 < kwargs["timeout"] <= 180
        assert kwargs["check"] is True
        assert command[command.index("-af") + 1] == audio._LIGHT_FILTER
        output = transform(original)
        Path(command[-1]).write_bytes(np.asarray(output, dtype="<f4").tobytes())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(audio.subprocess, "run", run)


@pytest.mark.parametrize("delay", [0, 1102, 4410])
def test_known_delay_compensated_and_rms_matched(synthetic_path, monkeypatch, np, delay):
    _, original = read_wav(synthetic_path, np)

    def transform(samples):
        result = np.zeros_like(samples)
        result[delay:] = samples[:len(samples) - delay] * 0.4
        return result

    fake_ffmpeg(monkeypatch, np, synthetic_path, transform)
    metadata = audio.apply_audio_postprocess(synthetic_path, "light_denoise_v1")
    _, processed = read_wav(synthetic_path, np)
    assert metadata["latency_samples"] == delay
    assert metadata["gain_db"] == pytest.approx(metadata["rms_gain_db"])
    assert np.sqrt(np.mean(processed ** 2)) == pytest.approx(
        np.sqrt(np.mean(original ** 2)), rel=0.001)
    assert np.corrcoef(original[:len(original) - delay], processed[:len(original) - delay])[0, 1] > 0.9999
    if delay:
        assert np.all(processed[-delay:] == 0)


def test_peak_ceiling_records_reduced_gain(synthetic_path, monkeypatch, np):
    _, samples = read_wav(synthetic_path, np)
    samples[50000] = 0.999
    write_wav(synthetic_path, samples, 44100, np)
    fake_ffmpeg(monkeypatch, np, synthetic_path, lambda x: x * 0.5)
    metadata = audio.apply_audio_postprocess(synthetic_path, "light_denoise_v1")
    _, output = read_wav(synthetic_path, np)
    assert metadata["input_peak"] > 0.98
    assert metadata["peak_limited"]
    assert metadata["gain_db"] < metadata["rms_gain_db"]
    assert metadata["output_rms"] < metadata["input_rms"]
    assert np.max(np.abs(output)) <= 0.98
    assert metadata["output_peak"] == pytest.approx(math.floor(0.98 * 32768) / 32768)


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing", "replace", "write"])
def test_atomic_failures_keep_original_and_remove_scratch(synthetic_path, monkeypatch, np, failure):
    before = synthetic_path.read_bytes()
    inode = synthetic_path.stat().st_ino
    fake_ffmpeg(monkeypatch, np, synthetic_path, lambda x: x * 0.5)
    if failure in {"exit", "timeout", "missing"}:
        def fail_run(command, **kwargs):
            Path(command[-1]).write_bytes(b"partial f32")
            if failure == "exit":
                raise subprocess.CalledProcessError(1, command, stderr=b"synthetic failure")
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            raise FileNotFoundError("ffmpeg unavailable")
        monkeypatch.setattr(audio.subprocess, "run", fail_run)
    elif failure == "replace":
        def fail_replace(src, dst):
            with wave.open(str(src), "rb") as wav:
                assert wav.getnframes() == 4 * 44100
            assert Path(dst).read_bytes() == before
            assert Path(src).parent.parent == synthetic_path.parent
            raise OSError("synthetic replace failure")
        monkeypatch.setattr(audio.os, "replace", fail_replace)
    else:
        def fail_write(*args, **kwargs):
            raise OSError("synthetic output write failure")
        monkeypatch.setattr(audio.os, "fsync", fail_write)
    with pytest.raises((OSError, subprocess.SubprocessError)):
        audio.apply_audio_postprocess(synthetic_path, "light_denoise_v1")
    assert synthetic_path.read_bytes() == before
    assert synthetic_path.stat().st_ino == inode
    assert list(synthetic_path.parent.iterdir()) == [synthetic_path]


@pytest.mark.parametrize("kind", ["silent", "unrelated", "nonfinite", "wrong_length", "overlong_delay"])
def test_invalid_filtered_audio_preserves_source(synthetic_path, monkeypatch, np, kind):
    before = synthetic_path.read_bytes()

    def transform(samples):
        if kind == "silent":
            return np.zeros_like(samples)
        if kind == "unrelated":
            return np.random.default_rng(7).normal(0, 0.1, len(samples))
        if kind == "nonfinite":
            samples[0] = np.nan
            return samples
        if kind == "wrong_length":
            return samples[:-1]
        return np.concatenate((np.zeros(6000), samples[:-6000]))

    fake_ffmpeg(monkeypatch, np, synthetic_path, transform)
    with pytest.raises(ValueError):
        audio.apply_audio_postprocess(synthetic_path, "light_denoise_v1")
    assert synthetic_path.read_bytes() == before
    assert list(synthetic_path.parent.iterdir()) == [synthetic_path]


@pytest.mark.parametrize("kind", ["silence", "constant", "tone", "short", "empty", "truncated", "stereo", "pcm8"])
def test_unalignable_or_invalid_input_is_never_applied(tmp_path, monkeypatch, np, kind):
    path = tmp_path / "invalid.wav"
    samples = np.zeros(44100)
    if kind == "constant":
        samples[:] = 0.1
    elif kind == "tone":
        samples = 0.2 * np.sin(2 * np.pi * 100 * np.arange(44100) / 44100)
    elif kind == "short":
        samples = np.random.default_rng(42).normal(0, 0.1, 300)
    elif kind == "empty":
        samples = samples[:0]
    write_wav(path, samples, 44100, np)
    if kind == "truncated":
        path.write_bytes(path.read_bytes()[:-2])
    if kind in {"stereo", "pcm8"}:
        with wave.open(str(path), "wb") as wav:
            wav.setparams((2 if kind == "stereo" else 1, 1 if kind == "pcm8" else 2,
                           44100, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x00" * 400)
    before = path.read_bytes()
    fake_ffmpeg(monkeypatch, np, path, lambda x: x * 0.5)
    with pytest.raises(ValueError):
        audio.apply_audio_postprocess(path, "light_denoise_v1")
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_fft_input_is_bounded_to_five_seconds(np, monkeypatch):
    sample_rate = 24000
    samples = np.random.default_rng(5).normal(0, 0.1, 10 * sample_rate)
    processed = np.concatenate((np.zeros(600), samples[:-600]))
    real_rfft = np.fft.rfft
    lengths = []

    def bounded_rfft(values, n):
        lengths.append(len(values))
        assert len(values) <= 5 * sample_rate
        assert n <= 1 << (10 * sample_rate - 1).bit_length()
        return real_rfft(values, n)

    monkeypatch.setattr(np.fft, "rfft", bounded_rfft)
    latency, confidence = audio._estimate_latency(samples, processed, sample_rate)
    assert latency == 600
    assert confidence > 0.999
    assert len(lengths) == 2
