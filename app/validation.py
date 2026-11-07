"""Validate all user supplied parameters before they reach the model or filesystem."""
from __future__ import annotations

import math
from pathlib import Path

from app import config

MAX_TEXT = 2000
MAX_QUEUE = 12
MAX_REFERENCE_SECONDS = 120
MAX_UPLOAD_MB = 30
ALLOWED_AUDIO = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
REFERENCE_TASKS = {"TTS", "MULTILINGUAL", "BASIC", "EMOTION", "DIALECT"}


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def string(value, label, limit, *, required=False):
    if not isinstance(value, str):
        raise APIError(f"{label}必须是文字")
    value = value.strip()
    if required and not value:
        raise APIError(f"请填写{label}")
    if len(value) > limit:
        raise APIError(f"{label}最多 {limit} 字")
    return value


def safe_file(directory: Path, name):
    if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise APIError("无效的音频文件标识")
    path = directory / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
        raise APIError("音频不存在，请重新上传", 404)
    return path


def boolean(value, label):
    if not isinstance(value, bool):
        raise APIError(f"{label}必须是布尔值")
    return value


def params(data, uploads: Path, *, require_text=True):
    if not isinstance(data, dict):
        raise APIError("请求参数必须是 JSON 对象")
    if "prompt_wav_path" in data:
        raise APIError("不接受服务器文件路径，请使用上传的参考音")
    result = {}
    if require_text:
        result["text"] = string(data.get("text", ""), "合成文本", MAX_TEXT, required=True)
    task = data.get("task", "TTS")
    if not isinstance(task, str) or task not in {t["value"] for t in config.TASKS}:
        raise APIError("不支持的合成模式")
    result["task"] = task
    enums = {
        "language_id": config.LANGUAGES, "emotion": config.EMOTIONS,
        "speed": config.SPEED_VALUES, "pitch": config.PITCH_VALUES,
        "volume": config.VOLUME_VALUES, "dialect": config.DIALECT_VALUES,
    }
    for key, items in enums.items():
        value = data.get(key)
        if value in (None, ""):
            continue
        if value not in [x["value"] if isinstance(x, dict) else x for x in items]:
            raise APIError(f"{key} 的取值无效")
        result[key] = value
    for key, max_chars in {"instruction": 1000, "style": 1000, "ip": 100, "prompt_text": 3000}.items():
        if data.get(key) is not None:
            result[key] = string(data[key], key, max_chars)
    for key, low, high, integer in [
        ("cfg", 0, 10, False), ("sigma", 0, 1, False), ("temperature", 0, 2, False),
        ("max_length", 1, 60, True), ("max_decode_steps", 50, 2000, True),
        ("seed", 0, 2147483647, True),
    ]:
        value = data.get(key, config.DEFAULTS[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise APIError(f"{key} 必须是有效数字")
        if not low <= value <= high or (isinstance(value, float) and not math.isfinite(value)) or (integer and value != int(value)):
            raise APIError(f"{key} 需要在 {low}–{high} 之间" + ("，且为整数" if integer else ""))
        result[key] = int(value) if integer else float(value)
    for key in ["use_spk_emb", "use_zero_spk_emb", "no_trim_silence", "raw_text"]:
        result[key] = boolean(data.get(key, False), key)
    if "audio_postprocess" in data:
        profile = data["audio_postprocess"]
        if not isinstance(profile, str) or profile not in {"none", "light_denoise_v1"}:
            raise APIError("不支持的音频后处理选项")
        result["audio_postprocess"] = profile
    upload_id = data.get("upload_id")
    if upload_id:
        path = safe_file(uploads, upload_id)
        if path.suffix.lower() not in ALLOWED_AUDIO:
            raise APIError("不支持的参考音格式")
        result["upload_id"] = upload_id
    if task in {"VOICE_DESIGN", "FREEFORM", "IP"}:
        result.pop("upload_id", None)
        result.pop("prompt_text", None)
        result["use_spk_emb"], result["use_zero_spk_emb"] = False, True
        required_key = {"VOICE_DESIGN": "style", "FREEFORM": "instruction", "IP": "ip"}[task]
        if not result.get(required_key):
            raise APIError({"style": "请描述想要的声音", "instruction": "请填写演绎指令", "ip": "请选择内置音色"}[required_key])
    elif result["use_spk_emb"] and not upload_id:
        raise APIError("声音克隆需要先上传参考音或选择声音预设")
    if task == "MULTILINGUAL" and not result.get("language_id"):
        raise APIError("跨语言模式需要选择目标语言")
    return result


def voice(data, uploads, existing=None):
    if not isinstance(data, dict):
        raise APIError("声音参数必须是 JSON 对象")
    merged = {**(existing or {}), **data}
    name = string(merged.get("name", ""), "声音名称", 80, required=True)
    kind = merged.get("kind", "clone")
    if not isinstance(kind, str) or kind not in {"clone", "builtin", "design"}:
        raise APIError("不支持的声音类型")
    description = string(merged.get("description", ""), "声音备注", 1000)
    tags = merged.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 12:
        raise APIError("声音标签最多 12 个")
    tags = list(dict.fromkeys(string(x, "标签", 30, required=True) for x in tags))
    upload_id = merged.get("upload_id")
    prompt_text = string(merged.get("prompt_text", ""), "参考文字", 3000)
    incoming_params = data.get("parameters", {})
    if not isinstance(incoming_params, dict):
        raise APIError("预设参数必须是 JSON 对象")
    previous_params = existing.get("parameters", {}) if existing and existing.get("kind") == kind else {}
    raw_params = {**previous_params, **incoming_params}
    if kind == "clone":
        if not upload_id:
            raise APIError("请先上传参考声音")
        raw_params.update(upload_id=upload_id, prompt_text=prompt_text)
        raw_params.setdefault("use_spk_emb", True)
        if not isinstance(raw_params.get("task", "TTS"), str) or raw_params.get("task", "TTS") not in REFERENCE_TASKS:
            raise APIError("参考音预设请选择声音克隆、情绪、方言、语速或跨语言模式")
    else:
        raw_params.pop("upload_id", None)
        raw_params.pop("prompt_text", None)
        raw_params["task"] = "IP" if kind == "builtin" else "VOICE_DESIGN"
        upload_id, prompt_text = None, ""
    validated = params(raw_params, uploads, require_text=False)
    return {"name": name, "kind": kind, "description": description, "tags": tags,
            "favorite": boolean(merged.get("favorite", False), "收藏"), "upload_id": upload_id,
            "prompt_text": prompt_text, "parameters": validated}
