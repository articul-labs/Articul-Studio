"""Engine discovery: the placeholder always runs; Articul joins when configured."""

from __future__ import annotations

from .articul import ArticulEngine
from .base import TtsEngine
from .stub import PlaceholderEngine

_ENGINES: list[TtsEngine] = []


def get_engines() -> list[TtsEngine]:
    """Instantiated engine list, rebuilt when availability may have changed."""
    available: list[TtsEngine] = [PlaceholderEngine()]
    if ArticulEngine.available():
        available.append(ArticulEngine())
    if [e.id for e in available] != [e.id for e in _ENGINES]:
        _ENGINES.clear()
        _ENGINES.extend(available)
    return _ENGINES


def get_engine(engine_id: str) -> TtsEngine | None:
    return next((e for e in get_engines() if e.id == engine_id), None)
