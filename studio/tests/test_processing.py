"""Processing wrapper tests (T2).

The unavailable paths run everywhere. The real-inference paths are guarded by
the same availability probes the wrappers use, so they skip cleanly on
machines without torch/librosa/onnxruntime. The enhance real path additionally
refuses to run unless the FRCRN checkpoint is already on disk — tests never
download models (plan hard rule).
"""
import sys

import numpy as np
import pytest
import soundfile as sf

from server.processing import _compat, enhance, qc

HEAVY_MODULES = ("torch", "speechscore", "clearvoice")


def test_wrappers_import_without_heavy_deps():
    # Hard rule: importing the wrapper modules must never pull in
    # torch/speechscore/clearvoice. Runs first in this file so no earlier
    # test here can have polluted sys.modules.
    import server.processing.enhance  # noqa: F401
    import server.processing.qc  # noqa: F401

    for name in HEAVY_MODULES:
        assert name not in sys.modules, f"{name} got imported at module import time"


def test_processing_unavailable_is_runtime_error():
    assert issubclass(_compat.ProcessingUnavailable, RuntimeError)


def test_reason_reports_machine_state():
    text = _compat.reason()
    assert isinstance(text, str) and text
    assert isinstance(_compat.ffmpeg_available(), bool)
    if _compat.speechscore_available():
        assert "speechscore" not in text
    else:
        assert "speechscore" in text
    if _compat.clearvoice_available():
        assert "clearvoice" not in text
    else:
        assert "clearvoice" in text


def _sine_wav(path, seconds=1.0, rate=24000):
    t = np.linspace(0.0, seconds, int(rate * seconds), endpoint=False)
    sf.write(path, 0.1 * np.sin(2 * np.pi * 220.0 * t).astype(np.float32), rate)
    return path


@pytest.mark.skipif(
    _compat.speechscore_available(),
    reason="speechscore installed — unavailable path unreachable here",
)
def test_quick_score_raises_processing_unavailable(tmp_path):
    wav = _sine_wav(tmp_path / "utt.wav")
    with pytest.raises(_compat.ProcessingUnavailable) as excinfo:
        qc.quick_score(wav)
    assert str(excinfo.value)  # reason must be non-empty


@pytest.mark.skipif(
    _compat.clearvoice_available(),
    reason="clearvoice installed — unavailable path unreachable here",
)
def test_enhance_raises_processing_unavailable(tmp_path):
    wav = _sine_wav(tmp_path / "utt.wav")
    with pytest.raises(_compat.ProcessingUnavailable) as excinfo:
        enhance.enhance(wav)
    assert str(excinfo.value)  # reason must be non-empty


@pytest.mark.skipif(
    not _compat.speechscore_available(),
    reason="speechscore deps (librosa/onnxruntime) not installed",
)
def test_quick_score_real(tmp_path):
    wav = _sine_wav(tmp_path / "utt.wav")
    scores = qc.quick_score(wav)
    assert set(scores) == {"dnsmos_sig", "dnsmos_bak", "dnsmos_ovr", "duration_s"}
    assert all(isinstance(v, float) for v in scores.values())
    assert 1.0 <= scores["dnsmos_ovr"] <= 5.0
    assert abs(scores["duration_s"] - 1.0) < 0.05


def _frcrn_checkpoint_present() -> bool:
    # clearvoice resolves its checkpoint_dir relative to the CWD; production
    # runs from the repo root, so that is where the test looks for weights.
    return (
        _compat.repo_root() / "checkpoints" / "FRCRN_SE_16K" / "last_best_checkpoint"
    ).is_file()


@pytest.mark.skipif(
    not _compat.clearvoice_available(),
    reason="clearvoice/torch not installed",
)
@pytest.mark.skipif(
    not _frcrn_checkpoint_present(),
    reason="FRCRN checkpoint not on disk — tests never download models",
)
def test_enhance_real(tmp_path, monkeypatch):
    monkeypatch.chdir(_compat.repo_root())  # checkpoint lookup is CWD-relative
    wav = _sine_wav(tmp_path / "utt.wav")  # 24 kHz input exercises the resample path
    out = enhance.enhance(wav)
    assert out == tmp_path / "utt.enhanced.wav"
    assert out.is_file()
    info = sf.info(out)
    assert info.samplerate == 16000
