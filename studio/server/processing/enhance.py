"""Per-clip speech enhancement via the vendored ClearVoice (FRCRN_SE_16K).

Enhance is never a pipeline default (plan decision #4): it runs on demand
from the review UI and the result is recorded with enhanced_by=<model>.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Union

import numpy as np
import soundfile as sf

from ._compat import (
    CWD_LOCK,
    ProcessingUnavailable,
    clearvoice_available,
    ensure_repo_paths,
    ffmpeg_available,
    reason,
)

DEFAULT_MODEL = "FRCRN_SE_16K"
ENHANCE_SAMPLE_RATE = 16000
FFMPEG_TIMEOUT_S = 120.0

PathLike = Union[str, os.PathLike]


def enhance(wav_path: PathLike, model: str = DEFAULT_MODEL) -> Path:
    """Denoise one clip with a ClearVoice speech-enhancement model.

    Writes ``<stem>.enhanced.wav`` (16 kHz mono) beside the input and returns
    its path. Raises ProcessingUnavailable when torch/clearvoice are missing
    (or ffmpeg, when the input needs resampling to the model's 16 kHz).

    First use of a model downloads its weights from HuggingFace into the
    CWD-relative ``checkpoints/<model>/`` directory (clearvoice default).
    """
    if not clearvoice_available():
        raise ProcessingUnavailable(reason())
    ensure_repo_paths()
    import clearvoice

    src = Path(wav_path)
    audio, sr = sf.read(src, dtype="float32", always_2d=True)
    mono = audio[:, 0]  # clearvoice models are mono; keep the first channel

    if sr != ENHANCE_SAMPLE_RATE:
        mono = _resampled_16k(src)

    # ClearVoice tensor-to-tensor mode: float32 [batch, length] in,
    # [batch, length] out (per demo_Numpy2Numpy.py).
    batch = np.reshape(mono, [1, mono.shape[0]]).astype(np.float32)
    # ClearVoice resolves checkpoint paths relative to the CWD; take the
    # shared lock so a concurrent QC chdir can't move the ground under it.
    with CWD_LOCK:
        voice = clearvoice.ClearVoice(task="speech_enhancement", model_names=[model])
        enhanced = voice(batch, False)

    dest = src.with_name(f"{src.stem}.enhanced.wav")
    sf.write(dest, enhanced[0, :], ENHANCE_SAMPLE_RATE)
    return dest


def _resampled_16k(src: Path) -> np.ndarray:
    """Resample a wav file to 16 kHz mono via ffmpeg; return the samples."""
    if not ffmpeg_available():
        raise ProcessingUnavailable(reason() + " (ffmpeg needed to resample to 16 kHz)")
    with tempfile.TemporaryDirectory(prefix="studio-resample-") as tmp:
        tmp_wav = Path(tmp) / "input_16k.wav"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src),
            "-ar", str(ENHANCE_SAMPLE_RATE), "-ac", "1",
            str(tmp_wav),
        ]
        try:
            subprocess.run(
                cmd, check=True, timeout=FFMPEG_TIMEOUT_S, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr_tail = (exc.stderr or "").strip()[-500:]
            raise RuntimeError(f"ffmpeg resample failed: {stderr_tail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ffmpeg resample timed out after {FFMPEG_TIMEOUT_S}s") from exc
        data, _ = sf.read(tmp_wav, dtype="float32", always_2d=True)
        return data[:, 0]
