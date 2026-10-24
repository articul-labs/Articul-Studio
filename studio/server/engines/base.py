"""TTS engine seam for Articul Studio.

Each engine declares its own adjustable parameter schema (`params`). The web UI
renders sliders/toggles dynamically from that schema, so plugging in the
Articul Vietnamese TTS model requires no frontend changes: implement
`TtsEngine`, declare `params`, and register the engine in `registry.py`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParamSpec:
    """One adjustable synthesis parameter, rendered as a UI control.

    kind is "slider" for ranged numbers or "toggle" for booleans. `step`
    controls slider granularity (ElevenLabs-style engines use 0.01).
    """

    key: str
    label: str
    min: float
    max: float
    default: float
    step: float = 0.01
    kind: str = "slider"

    def clamp(self, value: float) -> float:
        if self.kind == "toggle":
            return 1.0 if value >= 0.5 else 0.0
        return max(self.min, min(self.max, value))

    def accepts(self, value: float) -> bool:
        return self.min <= value <= self.max


@dataclass(frozen=True)
class Voice:
    """A speakable voice identity exposed by an engine."""

    id: str
    name: str
    description: str = ""


@dataclass
class SynthesisResult:
    """In-memory WAV audio plus metadata for the API layer."""

    wav_bytes: bytes
    sample_rate: int
    duration_s: float
    settings_used: dict[str, float] = field(default_factory=dict)


class TtsEngine(abc.ABC):
    """A Vietnamese-capable speech synthesis backend."""

    id: str
    name: str
    description: str
    language: str = "vi"
    voices: list[Voice]
    params: list[ParamSpec]

    def default_settings(self) -> dict[str, float]:
        return {p.key: p.default for p in self.params}

    def normalize_text(self, text: str) -> str:
        """Rewrite written text into speakable words (numbers, currency, times).

        Engines that ship their own text frontend override this — e.g. the
        Articul engine delegates to articul.frontend.normalize so the studio
        and the model agree on exactly what gets spoken.
        """
        from ..normalize_vi import normalize

        return normalize(text)

    def normalize_settings(self, settings: dict[str, float] | None) -> dict[str, float]:
        """Fill defaults, clamp unknown/out-of-range values to the schema.

        Unknown keys are dropped rather than passed through, so a stale client
        cannot smuggle parameters into the engine.
        """
        merged = self.default_settings()
        for key, value in (settings or {}).items():
            spec = self._by_key.get(key)
            if spec is not None:
                try:
                    merged[key] = spec.clamp(float(value))
                except (TypeError, ValueError):
                    continue
        return merged

    @property
    def _by_key(self) -> dict[str, ParamSpec]:
        return {p.key: p for p in self.params}

    @abc.abstractmethod
    def synthesize(self, text: str, voice_id: str, settings: dict[str, float]) -> SynthesisResult:
        """Synthesize one section of text under the given settings."""
