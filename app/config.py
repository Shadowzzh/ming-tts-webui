"""Static option lists for the Ming-3.0-tts web UI.

These mirror the exact values accepted by ``MingTTS.synthesize`` /
``tts_tasks`` so the front-end stays data-driven and the back-end can
validate requests against the same vocabulary.
"""

# Generation tasks exposed in the UI. Each maps to a ``task`` value accepted
# by ``MingTTS.synthesize``. ``STYLE`` is the runtime name for voice design.
TASKS = [
    {"value": "TTS", "label": "TTS (zero-shot / clone)", "needs_prompt": True,
     "language_optional": True, "controls": []},
    {"value": "MULTILINGUAL", "label": "Multilingual", "needs_prompt": True,
     "language_optional": False, "controls": []},
    {"value": "BASIC", "label": "Basic (speed/pitch/volume)", "needs_prompt": True,
     "language_optional": True, "controls": ["speed", "pitch", "volume"]},
    {"value": "EMOTION", "label": "Emotion", "needs_prompt": True,
     "language_optional": True, "controls": ["emotion"]},
    {"value": "DIALECT", "label": "Dialect (Cantonese / Sichuan)", "needs_prompt": True,
     "language_optional": True, "controls": ["dialect"]},
    {"value": "FREEFORM", "label": "Free-form instruction", "needs_prompt": False,
     "language_optional": True, "controls": ["instruction"]},
    {"value": "VOICE_DESIGN", "label": "Voice design (describe a voice)", "needs_prompt": False,
     "language_optional": True, "controls": ["style"]},
    {"value": "IP", "label": "Built-in voice", "needs_prompt": False,
     "language_optional": True, "controls": ["ip"]},
]

# Target languages. Chinese/English work without ``language_id``; the other
# five are required for MULTILINGUAL.
# Labels are English; the ``value`` is what the model expects.
LANGUAGES = [
    {"value": "zh", "label": "Chinese"},
    {"value": "en", "label": "English"},
    {"value": "ja", "label": "Japanese"},
    {"value": "ko", "label": "Korean"},
    {"value": "de", "label": "German"},
    {"value": "fr", "label": "French"},
    {"value": "es", "label": "Spanish"},
]

# ``value`` is the emotion token the model expects; ``label`` is English.
EMOTIONS = [
    {"value": "高兴", "label": "Happy"},
    {"value": "悲伤", "label": "Sad"},
    {"value": "愤怒", "label": "Angry"},
    {"value": "恐惧", "label": "Fearful"},
    {"value": "厌恶", "label": "Disgusted"},
    {"value": "惊讶", "label": "Surprised"},
    {"value": "中性", "label": "Neutral"},
]

# ``value`` is the speed/pitch/volume token the model expects; ``label`` is English.
SPEED_VALUES = [
    {"value": "慢速", "label": "Slow"},
    {"value": "中速", "label": "Medium"},
    {"value": "快速", "label": "Fast"},
]
PITCH_VALUES = [
    {"value": "低", "label": "Low"},
    {"value": "中", "label": "Medium"},
    {"value": "高", "label": "High"},
]
VOLUME_VALUES = [
    {"value": "低", "label": "Low"},
    {"value": "中", "label": "Medium"},
    {"value": "高", "label": "High"},
]
DIALECT_VALUES = ["cantonese", "chuanyu dialect"]

# Inline paralinguistic tags (case-sensitive, include the brackets). They are
# typed directly into the text for any task.
PARALINGUISTIC_TAGS = [
    "[Breathing]", "[Cough]", "[Sigh]", "[Laughter]", "[Uhm]",
    "[Surprise-ah]", "[Surprise-oh]", "[Dissatisfaction-hnn]",
    "[Confirmation-en]", "[Question-ei]", "[Question-ah]",
    "[Question-en]", "[Question-oh]", "[Question-huh]",
]

# Built-in premium voices. English voices plus a curated set of the 100+
# Chinese voices. The ``ip`` field also accepts any other voice name from the
# catalog (e.g. ``灵信远_高兴``).
BUILTIN_VOICES = [
    # English
    {"value": "US_James", "label": "US_James (American male, narration)"},
    {"value": "US_Stella", "label": "US_Stella (American female, warm)"},
    {"value": "US_Emma", "label": "US_Emma (American female, assistant)"},
    {"value": "UK_George", "label": "UK_George (British male, audiobook)"},
    {"value": "UK_Charlotte", "label": "UK_Charlotte (British female, measured)"},
    # Recommended Chinese (value is the voice id; label is English)
    {"value": "灵小柔", "label": "Ling Xiaorou (Chinese female, sweet/clear)"},
    {"value": "灵楷", "label": "Ling Kai (Chinese male, warm/resonant)"},
    {"value": "灵小安", "label": "Ling Xiaoan (Chinese female, teen)"},
    {"value": "灵语汐", "label": "Ling Yuxi (Chinese female, bright)"},
    {"value": "灵朗", "label": "Ling Lang (Chinese male, boyfriend)"},
    {"value": "灵叙柔", "label": "Ling Xurou (Chinese female, news anchor)"},
    {"value": "灵卓然", "label": "Ling Zhuoran (Chinese male, documentary)"},
]

# Default generation parameters (see infer.py).
DEFAULTS = {
    "cfg": 2.0,
    "sigma": 0.25,
    "temperature": 0.0,
    "max_length": 24,
    "max_decode_steps": 1000,
    "seed": 1895,
}

# Which control keys belong to which structured task (must match
# ``tts_tasks._TASK_ALIASES`` / ``build_task_instruction``).
TASK_CONTROL_FIELDS = {
    "BASIC": ["speed", "pitch", "volume"],
    "EMOTION": ["emotion"],
    "DIALECT": ["dialect"],
    "VOICE_DESIGN": ["style"],
    "STYLE": ["style"],
    "IP": ["ip"],
}
