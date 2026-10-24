"""Seam for the Articul Vietnamese TTS model (in development).

The engine is registered only when `ARTICUL_MODEL_DIR` points at an exported
model, so the studio runs standalone on the placeholder until then.

Integration contract: implement `load_model` and `_synthesize_with_model`.
Declared params below are placeholders — replace them with Articul's real
adjustable parameters (e.g. speed/pitch/energy) and the UI picks them up
automatically from GET /api/engines.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import ParamSpec, SynthesisResult, TtsEngine

ARTICUL_MODEL_DIR_ENV = "ARTICUL_MODEL_DIR"


def _articul_normalize(text: str) -> str:
    """Delegate to articul's own text frontend when importable."""
    try:
        from articul.frontend import normalize as articul_normalize
    except ImportError:
        return text
    return articul_normalize(text)


def model_dir() -> Path | None:
    raw = os.environ.get(ARTICUL_MODEL_DIR_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


class ArticulEngine(TtsEngine):
    id = "articul"
    name = "Articul (Vietnamese TTS)"
    description = "In-house Vietnamese TTS model."
    voices: list = []  # populate from the model's speaker table in load_model
    params = [
        # TODO(articul): replace with the model's real parameter schema.
        ParamSpec("speed", "Speed", 0.7, 1.2, 1.0),
    ]

    def __init__(self) -> None:
        self._model = None

    @classmethod
    def available(cls) -> bool:
        return model_dir() is not None

    def load_model(self):
        """Load exported Articul weights from model_dir()."""
        raise NotImplementedError("Implement when the Articul export format is decided")

    def normalize_text(self, text: str) -> str:
        return _articul_normalize(text)

    def _synthesize_with_model(self, text: str, voice_id: str, settings: dict[str, float]):
        """Run the model; return (samples: np.ndarray float32, sample_rate: int).

        Expected integration path (see articul src/articul/frontend and
        src/articul/eval/synthesis.py Synthesizer protocol):
        normalize -> phonemize(dialect, lexicon) -> to_input_ids -> model.
        """
        raise NotImplementedError("Implement when the Articul inference API is decided")

    def ensure_loaded(self) -> None:
        if self._model is None:
            self._model = self.load_model()

    def synthesize(self, text: str, voice_id: str, settings: dict[str, float]) -> SynthesisResult:
        self.ensure_loaded()
        settings = self.normalize_settings(settings)
        samples, sample_rate = self._synthesize_with_model(text, voice_id, settings)

        import io

        import soundfile as sf

        buffer = io.BytesIO()
        sf.write(buffer, samples, sample_rate, format="WAV")
        return SynthesisResult(
            wav_bytes=buffer.getvalue(),
            sample_rate=sample_rate,
            duration_s=len(samples) / sample_rate,
            settings_used=settings,
        )
