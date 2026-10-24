"""Placeholder engine used until the Articul Vietnamese model is integrated.

It renders audible, duration-accurate tone-burst "speech" so the adjustable
pipeline (per-section voices + parameter sliders) is demonstrable end to end.
Every declared parameter has an audible effect, and `speed` scales duration the
way a real TTS engine would. Audio is clearly synthetic — do not ship it.
"""

from __future__ import annotations

import hashlib

import numpy as np
import soundfile as sf

from .base import ParamSpec, SynthesisResult, TtsEngine, Voice

SAMPLE_RATE = 24_000

# Rough per-syllable spoken duration at speed=1.0, matching natural Vietnamese pace.
SYLLABLE_S = 0.22
PAUSE_S = {",": 0.15, ";": 0.15, ":": 0.15, ".": 0.35, "!": 0.35, "?": 0.35, "\n": 0.30}
VOWELS = "aeiouyàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"


def count_syllables(text: str) -> int:
    """Vietnamese syllable proxy: one vowel cluster per word part.

    Vietnamese is monosyllabic per written token, so vowel-cluster counting
    tracks spoken syllables closely enough for duration estimation.
    """
    total = 0
    for word in text.lower().split():
        clusters = 0
        prev_vowel = False
        for ch in word:
            is_vowel = ch in VOWELS
            if is_vowel and not prev_vowel:
                clusters += 1
            prev_vowel = is_vowel
        total += max(clusters, 1 if any(c.isalnum() for c in word) else 0)
    return total


class PlaceholderEngine(TtsEngine):
    id = "placeholder"
    name = "Placeholder (Articul chưa nối)"
    description = (
        "Tone-burst stand-in so the studio pipeline works before the Articul "
        "model is wired in. Params are audible but speech is synthetic."
    )
    voices = [
        Voice("nam-mien-bac", "Nam (Bắc)", "Northern male placeholder"),
        Voice("nu-mien-bac", "Nữ (Bắc)", "Northern female placeholder"),
        Voice("nu-mien-nam", "Nữ (Nam Bộ)", "Southern female placeholder"),
    ]
    # ElevenLabs-style schema, matching the parameter set observed in the
    # Studio web app, until Articul declares its own.
    params = [
        ParamSpec("stability", "Stability", 0.0, 1.0, 0.5),
        ParamSpec("similarity_boost", "Similarity boost", 0.0, 1.0, 0.75),
        ParamSpec("style", "Style exaggeration", 0.0, 1.0, 0.0),
        ParamSpec("speed", "Speed", 0.7, 1.2, 1.0),
        ParamSpec("speaker_boost", "Speaker boost", 0.0, 1.0, 1.0, kind="toggle"),
    ]

    def synthesize(self, text: str, voice_id: str, settings: dict[str, float]) -> SynthesisResult:
        settings = self.normalize_settings(settings)
        voice = next((v for v in self.voices if v.id == voice_id), self.voices[0])

        rng = np.random.default_rng(int(hashlib.sha1(text.encode()).hexdigest()[:12], 16))
        base_hz = 118.0 if voice.id.startswith("nam") else 205.0
        if voice.id == "nu-mien-nam":
            base_hz *= 0.94

        syllables = max(count_syllables(text), 1)
        chunk_s = SYLLABLE_S / settings["speed"]
        jitter = 0.25 * (1.0 - settings["stability"])  # low stability -> unsteady pitch
        vibrato = 6.0 * settings["style"]  # style -> noticeable vibrato
        brightness = settings["similarity_boost"]

        pieces: list[np.ndarray] = []
        for i in range(syllables):
            f0 = base_hz * (1.0 + rng.uniform(-jitter, jitter)) * rng.choice([0.94, 1.0, 1.06])
            t = np.arange(int(SAMPLE_RATE * chunk_s)) / SAMPLE_RATE
            phase = 2 * np.pi * f0 * t + vibrato * np.sin(2 * np.pi * 5.5 * t)
            wave = np.sin(phase)
            wave += brightness * 0.45 * np.sin(2 * phase)  # similarity -> brighter timbre
            env = np.minimum(1.0, np.minimum(t, chunk_s - t) * 60)  # click-free edges
            pieces.append(0.35 * wave * env)
            pieces.append(np.zeros(int(SAMPLE_RATE * 0.03)))
            for ch in text:
                if ch in PAUSE_S and rng.random() < 0.02:
                    pieces.append(np.zeros(int(SAMPLE_RATE * PAUSE_S[ch])))

        if settings["speaker_boost"] >= 0.5:
            gain = 1.5
        else:
            gain = 1.0
        audio = gain * np.concatenate(pieces) if pieces else np.zeros(SAMPLE_RATE // 2)
        peak = float(np.max(np.abs(audio))) or 1.0
        audio = 0.9 * audio / peak

        import io

        buffer = io.BytesIO()
        sf.write(buffer, audio.astype(np.float32), SAMPLE_RATE, format="WAV")
        wav_bytes = buffer.getvalue()
        return SynthesisResult(
            wav_bytes=wav_bytes,
            sample_rate=SAMPLE_RATE,
            duration_s=len(audio) / SAMPLE_RATE,
            settings_used=settings,
        )
