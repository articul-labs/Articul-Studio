"""Engine discovery: the placeholder always runs; the rest join when their
dependencies appear (Articul: configured model dir; Vieneu: SDK + espeak-ng)."""

from __future__ import annotations

from .articul import ArticulEngine
from .base import TtsEngine
from .stub import PlaceholderEngine
from .vieneu import VieneuEngine

_ENGINES: list[TtsEngine] = []


def get_engines() -> list[TtsEngine]:
    """Instantiated engine list, rebuilt when availability may have changed."""
    available: list[TtsEngine] = [PlaceholderEngine()]
    if ArticulEngine.available():
        available.append(ArticulEngine())
    if VieneuEngine.available():
        available.append(VieneuEngine())
    if [e.id for e in available] != [e.id for e in _ENGINES]:
        _ENGINES.clear()
        _ENGINES.extend(available)
    return _ENGINES


def get_engine(engine_id: str) -> TtsEngine | None:
    return next((e for e in get_engines() if e.id == engine_id), None)
