"""Seam for ViNeu-TTS, registered as the studio's experimental engine.

ViNeu-TTS (PyPI ``vieneu``, repo pnnbao97/VieNeu-TTS, weights
pnnbao-ump/VieNeu-TTS on Hugging Face) is a Vietnamese voice-cloning TTS;
v3-Turbo (0.1B) is the recommended experimental model. The engine registers
only when the ``vieneu`` package is importable AND ``espeak-ng`` is on PATH
(the SDK phonemizes through it), so the studio keeps running — without this
engine listed — on machines that have neither.

Assumed SDK shape, from the PyPI/GitHub README quickstarts (exact signatures
beyond these kwargs are unverified offline — reconcile against the installed
SDK during integration):

    model = Vieneu()  # SDK defaults select v3-Turbo
    rate, audio = model.infer(text, voice="Mai Anh",   # built-in preset, or
                              ref_audio="ref.wav",     # clone from a clip
                              denoise=True)
    # v3-Turbo emits 48 kHz audio.

Built-in preset voices (~23-25 names across Northern/Central/Southern
accents, e.g. "Mai Anh") are deliberately NOT enumerated here: the exact
voice ids could not be confirmed offline, so `voices` stays empty until
integration populates it from the installed SDK. Meanwhile only
"ref:<utterance_id>" voices are usable, resolved through the injectable
`ref_audio_resolver`.
"""

from __future__ import annotations

import shutil

from .base import ParamSpec, SynthesisResult, TtsEngine

REF_VOICE_PREFIX = "ref:"


def _import_error() -> str | None:
    """Try importing vieneu; return a readable error or None."""
    try:
        import vieneu  # noqa: F401
    except Exception as exc:  # any failure counts as unavailable, not just ImportError
        return f"{type(exc).__name__}: {exc}"
    return None


def unavailable_reason() -> str:
    """Human-readable summary of exactly what is missing for the engine."""
    problems = []
    error = _import_error()
    if error is not None:
        problems.append(f"vieneu not importable ({error})")
    if shutil.which("espeak-ng") is None:
        problems.append("espeak-ng not on PATH")
    if not problems:
        return "all vieneu dependencies available"
    return "vieneu engine unavailable: " + "; ".join(problems)


class VieneuEngine(TtsEngine):
    id = "vieneu"
    name = "ViNeu-TTS (experimental)"
    description = (
        "Vietnamese voice-cloning TTS (v3-Turbo 0.1B); clones a voice from a "
        "reference clip."
    )
    # Intentionally empty: built-in preset voice ids are unverified offline.
    # Populate from the installed SDK during integration; "ref:" voices need
    # no entry here.
    voices: list = []
    # denoise is the only adjustable kwarg the README quickstart documents for
    # infer(); extend this schema only against the installed SDK, never by
    # guess.
    params = [
        ParamSpec("denoise", "Denoise audio", 0.0, 1.0, 1.0, kind="toggle"),
    ]

    def __init__(self, ref_audio_resolver=None) -> None:
        self.ref_audio_resolver = ref_audio_resolver
        self._model = None

    @classmethod
    def available(cls) -> bool:
        return _import_error() is None and shutil.which("espeak-ng") is not None

    def _load_model(self):
        """Import + construct the SDK model. Heavy: called once, lazily."""
        from vieneu import Vieneu

        return Vieneu()  # SDK defaults select v3-Turbo

    def ensure_loaded(self):
        if self._model is None:
            self._model = self._load_model()
        return self._model

    def _infer_kwargs(self, voice_id: str) -> dict:
        """Map a studio voice_id onto infer() kwargs.

        "ref:<utterance_id>" clones from reference audio resolved by the
        injectable resolver; anything else is a built-in preset name.
        Resolution happens before model load so bad voice config fails fast
        without pulling in the heavy SDK import.
        """
        if voice_id.startswith(REF_VOICE_PREFIX):
            if self.ref_audio_resolver is None:
                raise ValueError("no resolver configured for ref voices")
            ref_path = self.ref_audio_resolver(voice_id[len(REF_VOICE_PREFIX):])
            return {"ref_audio": str(ref_path)}
        return {"voice": voice_id}

    def synthesize(self, text: str, voice_id: str, settings: dict[str, float]) -> SynthesisResult:
        settings = self.normalize_settings(settings)
        infer_kwargs = self._infer_kwargs(voice_id)

        model = self.ensure_loaded()
        sample_rate, samples = model.infer(
            text, denoise=bool(settings["denoise"]), **infer_kwargs
        )

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
