"""Preview-only DNSMOS QC via the vendored speechscore package.

The official quality bar lives in ``articul prepare``; this wrapper only
feeds the studio review UI (plan hard rule: studio scoring is preview only).
"""
from __future__ import annotations

import os
from typing import Union

import soundfile as sf

from ._compat import (
    CWD_LOCK,
    ProcessingUnavailable,
    ensure_repo_paths,
    reason,
    repo_root,
    speechscore_available,
)

PathLike = Union[str, os.PathLike]


def quick_score(wav_path: PathLike) -> dict:
    """Score a wav with DNSMOS (non-intrusive, no reference needed).

    Returns ``{"dnsmos_sig": float, "dnsmos_bak": float, "dnsmos_ovr": float,
    "duration_s": float}``. Raises ProcessingUnavailable when the vendored
    speechscore stack (librosa/onnxruntime/resampy/...) is missing.
    """
    if not speechscore_available():
        raise ProcessingUnavailable(reason())
    ensure_repo_paths()
    import speechscore  # vendored module exposing SpeechScore()

    info = sf.info(os.fspath(wav_path))

    # DNSMOS opens its ONNX models via CWD-relative paths
    # ('scores/dnsmos/DNSMOS/*.onnx'), so scoring runs with the CWD pinned to
    # the vendored speechscore dir. This mutates process-global state but is
    # restored after; acceptable for the studio's synchronous V1 routes.
    prev_cwd = os.getcwd()
    with CWD_LOCK:
        os.chdir(repo_root() / "speechscore")
        try:
            # SpeechScore(["dnsmos"]) -> ScoresList; calling it returns
            # {"DNSMOS": {"OVRL": ..., "SIG": ..., "BAK": ..., "P808_MOS": ...}}.
            scorer = speechscore.SpeechScore(["dnsmos"])
            results = scorer(os.fspath(wav_path), None)
        except ImportError as exc:
            # basis.scoring imports museval/resampy at call time, after the
            # availability probe has already passed.
            raise ProcessingUnavailable(f"DNSMOS scoring deps missing ({exc})") from exc
        finally:
            os.chdir(prev_cwd)

    dnsmos = results["DNSMOS"]
    return {
        "dnsmos_sig": float(dnsmos["SIG"]),
        "dnsmos_bak": float(dnsmos["BAK"]),
        "dnsmos_ovr": float(dnsmos["OVRL"]),
        "duration_s": float(info.duration),
    }
