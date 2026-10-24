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
