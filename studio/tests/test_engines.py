import pytest

from server.engines.base import ParamSpec
from server.engines.registry import get_engine, get_engines
from server.engines.stub import PlaceholderEngine, count_syllables


def test_placeholder_engine_is_registered():
    ids = [e.id for e in get_engines()]
    assert "placeholder" in ids


def test_articul_hidden_without_model_dir(monkeypatch):
    monkeypatch.delenv("ARTICUL_MODEL_DIR", raising=False)
    assert get_engine("articul") is None


def test_articul_appears_with_model_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTICUL_MODEL_DIR", str(tmp_path))
    engine = get_engine("articul")
    assert engine is not None
    assert engine.language == "vi"


def test_settings_normalized_and_unknowns_dropped():
    engine = PlaceholderEngine()
    cleaned = engine.normalize_settings({"speed": 5.0, "stability": 0.3, "evil": 1})
    assert cleaned["speed"] == 1.2  # clamped to max
    assert cleaned["stability"] == 0.3
    assert "evil" not in cleaned
    assert cleaned["similarity_boost"] == engine.default_settings()["similarity_boost"]


def test_speed_changes_duration_and_text_length_scales():
    engine = PlaceholderEngine()
    short = engine.synthesize("một", "nam-mien-bac", {"speed": 1.0})
    slow = engine.synthesize("một", "nam-mien-bac", {"speed": 0.7})
    long = engine.synthesize("một hai ba bốn năm sáu bảy tám", "nam-mien-bac", {"speed": 1.0})
    assert slow.duration_s > short.duration_s
    assert long.duration_s > short.duration_s
    assert short.wav_bytes[:4] == b"RIFF"


def test_unknown_voice_falls_back_to_first():
    engine = PlaceholderEngine()
    result = engine.synthesize("xin chào", "nope", {})
    assert result.duration_s > 0


def test_param_spec_clamps():
    spec = ParamSpec("x", "X", 0.0, 1.0, 0.5)
    assert spec.clamp(1.4) == 1.0
    assert spec.clamp(-1) == 0.0


def test_syllable_counting_handles_diacritics():
    assert count_syllables("xin chào") == 2
    assert count_syllables("Việt Nam") == 2
    assert count_syllables("quả") == 1

def test_engines_own_their_text_frontend():
    # Placeholder uses the studio normalizer; engines with their own frontend
    # (Articul) override normalize_text.
    engine = PlaceholderEngine()
    assert engine.normalize_text("7:30") == "bảy giờ ba mươi"


def test_articul_normalizer_falls_back_to_identity_without_import(monkeypatch):
    import sys

    from server.engines import articul as articul_module

    monkeypatch.setenv("ARTICUL_MODEL_DIR", "/tmp")
    saved = sys.modules.pop("articul.frontend", None)
    monkeypatch.setitem(sys.modules, "articul", None)  # forces ImportError inside
    engine = articul_module.ArticulEngine()
    assert engine.normalize_text("50%") == "50%"
    if saved is not None:
        sys.modules["articul.frontend"] = saved


def test_vieneu_hidden_without_deps(monkeypatch):
    import shutil
    import sys

    monkeypatch.setitem(sys.modules, "vieneu", None)  # forces ImportError inside
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert get_engine("vieneu") is None


def test_vieneu_unavailable_reason_mentions_espeak_ng(monkeypatch):
    import shutil

    from server.engines.vieneu import unavailable_reason

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert "espeak-ng" in unavailable_reason()


def test_vieneu_registered_and_synthesizes_through_fake_sdk(monkeypatch):
    import shutil
    import sys
    import types

    import numpy as np

    captured = {}

    class FakeVieneu:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs

        def infer(self, text, **kwargs):
            captured["infer_call"] = (text, kwargs)
            return 48000, np.linspace(-0.1, 0.1, 4800, dtype=np.float32)

    fake_module = types.ModuleType("vieneu")
    fake_module.Vieneu = FakeVieneu
    monkeypatch.setitem(sys.modules, "vieneu", fake_module)
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None
    )

    engine = get_engine("vieneu")
    assert engine is not None

    result = engine.synthesize("xin chào", "Mai Anh", {})
    assert result.wav_bytes[:4] == b"RIFF"
    assert result.sample_rate == 48000
    assert result.duration_s == 0.1

    text, kwargs = captured["infer_call"]
    assert text == "xin chào"
    assert kwargs["voice"] == "Mai Anh"
    assert kwargs["denoise"] is True  # engine default


def test_vieneu_ref_voice_uses_resolver(monkeypatch):
    import shutil
    import sys
    import types

    import numpy as np

    fake_module = types.ModuleType("vieneu")

    captured = {}

    class FakeVieneu:
        def infer(self, text, **kwargs):
            captured["infer_call"] = (text, kwargs)
            return 48000, np.zeros(4800, dtype=np.float32)

    fake_module.Vieneu = FakeVieneu
    monkeypatch.setitem(sys.modules, "vieneu", fake_module)
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None
    )

    from server.engines.vieneu import VieneuEngine

    engine = VieneuEngine(ref_audio_resolver=lambda utt_id: f"/data/{utt_id}.wav")
    engine.synthesize("xin chào", "ref:studs01_ab12cd34", {})
    _, kwargs = captured["infer_call"]
    assert kwargs["ref_audio"] == "/data/studs01_ab12cd34.wav"
    assert "voice" not in kwargs


def test_vieneu_ref_voice_without_resolver_raises_value_error():
    from server.engines.vieneu import VieneuEngine

    engine = VieneuEngine()
    with pytest.raises(ValueError, match="no resolver configured"):
        engine.synthesize("xin chào", "ref:studs01_ab12cd34", {})


def test_ref_voice_with_unknown_utterance_fails_fast(monkeypatch):
    """Resolver returning None (unknown/rejected utt) must raise naming the
    voice id, never hand the SDK the string "None" as a path (review P2)."""
    from server.engines.vieneu import VieneuEngine

    engine = VieneuEngine(ref_audio_resolver=lambda utt_id: None)
    with pytest.raises(ValueError, match="ref:nope_40404040"):
        engine.synthesize("xin chào", "ref:nope_40404040", {})
