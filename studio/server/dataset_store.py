"""Dataset persistence for speech-training data: one directory per dataset.

Layout (plans/2025-10-24-data-studio.md):

    {root}/{dataset_id}/dataset.json        # dataset doc
    {root}/{dataset_id}/utterances.jsonl    # one utterance doc per line
    {root}/{dataset_id}/audio/{utt_id}.wav  # always 24 kHz mono RIFF WAV

Follows the ProjectStore conventions (pydantic docs, JSON on disk, one lock,
atomic tmp+replace writes). Two rules matter more than convenience here:

- Transcripts are stored VERBATIM. Normalization is the training frontend's
  job (articul rule I005); the studio only validates emptiness and length.
- Audio is always 24 kHz mono WAV on disk, whatever the client uploaded.
  Anything not already in that shape goes through an ffmpeg subprocess.

Missing datasets/utterances raise KeyError; invalid input (empty transcript,
bad status, undecodable audio, unsafe zip paths) raises ValueError — the API
layer maps those to 404/400.
"""

from __future__ import annotations

import io
import json
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import soundfile as sf
from typing import Literal

from pydantic import BaseModel, Field

TARGET_SAMPLE_RATE = 24000
MAX_TRANSCRIPT_CHARS = 2000
UTTERANCE_STATUSES = ("recorded", "accepted", "rejected")
FFMPEG_TIMEOUT_S = 120.0  # plan rule: synchronous subprocess calls with timeouts

Status = Literal["recorded", "accepted", "rejected"]


class Dataset(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    speakers: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class Utterance(BaseModel):
    utt_id: str
    wav: str  # path relative to the dataset dir: "audio/{utt_id}.wav"
    transcript: str  # verbatim — never normalized
    speaker: str
    status: Status = "recorded"
    duration_s: float = 0.0
    sample_rate: int = TARGET_SAMPLE_RATE
    scores: dict[str, float] | None = None  # filled by the QC wrapper (S2), preview only
    enhanced_by: str | None = None  # set by the enhance wrapper (S2)
    enhanced_from: str | None = None  # previous wav when enhanced_by is set
    created_at: float = Field(default_factory=time.time)


def _validated_transcript(transcript: str) -> str:
    """Reject empty/oversized transcripts; otherwise return the text untouched."""
    if not transcript.strip():
        raise ValueError("transcript is empty")
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        raise ValueError(f"transcript exceeds {MAX_TRANSCRIPT_CHARS} characters")
    return transcript


def _validated_speaker(speaker: str) -> str:
    if not speaker.strip():
        raise ValueError("speaker is empty")
    return speaker


def _validated_status(status: str) -> Status:
    if status not in UTTERANCE_STATUSES:
        raise ValueError(f"status must be one of {UTTERANCE_STATUSES}")
    return status  # type: ignore[return-value]


def _is_riff_wav(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _is_24k_mono(data: bytes) -> bool:
    try:
        info = sf.info(io.BytesIO(data))
    except RuntimeError:  # soundfile raises RuntimeError on unparseable wavs
        return False
    return info.samplerate == TARGET_SAMPLE_RATE and info.channels == 1


def to_24k_mono_wav(audio_bytes: bytes, suffix: str) -> bytes:
    """Return the upload as 24 kHz mono RIFF WAV bytes.

    Conforming uploads pass through untouched; everything else (browser
    webm/ogg/mp3 recordings, wavs at other rates/channel counts) is converted
    with `ffmpeg -ac 1 -ar 24000`. No shell, arg-list only; the input suffix
    only helps ffmpeg sniff the container.
    """
    if _is_riff_wav(audio_bytes) and _is_24k_mono(audio_bytes):
        return audio_bytes
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix or '.bin'}"
        dst = Path(tmp) / "out.wav"
        src.write_bytes(audio_bytes)
        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(src), "-vn", "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE), str(dst)],
                capture_output=True,
                timeout=FFMPEG_TIMEOUT_S,
            )
        except FileNotFoundError as e:
            raise ValueError("ffmpeg is not installed") from e
        if proc.returncode != 0:
            detail = proc.stderr.decode(errors="replace").strip()[:300]
            raise ValueError(f"ffmpeg could not decode the upload: {detail}")
        return dst.read_bytes()


def ascii_filename(name: str, suffix: str) -> str:
    # Header values must be ASCII; non-ASCII dataset names get an ASCII fallback.
    ascii_name = "".join(c for c in name if c.isascii() and (c.isalnum() or c in " -_")).strip()
    return (ascii_name or "dataset") + suffix


def _assert_safe_zip(zf: zipfile.ZipFile) -> None:
    """Zip-slip guard: reject archives whose entry names could escape the dataset dir."""
    for name in zf.namelist():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError(f"unsafe path in zip: {name}")


class DatasetStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- paths ---------------------------------------------------------------

    def _dir(self, dataset_id: str) -> Path:
        # ids are uuid hex; anything else is a probe (or traversal attempt)
        if not dataset_id.isalnum():
            raise ValueError("invalid dataset id")
        return self.root / dataset_id

    def _dataset_path(self, dataset_id: str) -> Path:
        return self._dir(dataset_id) / "dataset.json"

    def _utterances_path(self, dataset_id: str) -> Path:
        return self._dir(dataset_id) / "utterances.jsonl"

    @staticmethod
    def _utt_prefix(dataset_id: str) -> str:
        # Source-prefixed utt_ids keep them unique when datasets are merged
        # downstream (RawUtterance).
        slug = "".join(c.lower() for c in dataset_id if c.isalnum())
        return slug[:8]

    # -- low-level persistence ------------------------------------------------

    def _read_dataset(self, dataset_id: str) -> Dataset:
        try:
            return Dataset.model_validate_json(
                self._dataset_path(dataset_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(dataset_id) from None

    def _load_utterances(self, dataset_id: str) -> list[Utterance]:
        path = self._utterances_path(dataset_id)
        if not path.exists():
            return []
        utterances = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                utterances.append(Utterance.model_validate_json(line))
            except ValueError:
                continue  # skip corrupt/partial lines rather than breaking the listing
        return utterances

    def _save_dataset(self, dataset: Dataset) -> None:
        path = self._dataset_path(dataset.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(dataset.model_dump_json(), encoding="utf-8")
        tmp.replace(path)

    def _save_utterances(self, dataset_id: str, utterances: list[Utterance]) -> None:
        path = self._utterances_path(dataset_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            "".join(u.model_dump_json() + "\n" for u in utterances), encoding="utf-8")
        tmp.replace(path)

    def _remember_speaker(self, dataset: Dataset, speaker: str) -> None:
        if speaker not in dataset.speakers:
            dataset.speakers.append(speaker)
            self._save_dataset(dataset)

    def _new_utt_id(self, dataset_id: str, taken: set[str]) -> str:
        prefix = self._utt_prefix(dataset_id)
        while True:
            utt_id = f"{prefix}_{secrets.token_hex(4)}"
            if utt_id not in taken:
                return utt_id

    # -- datasets --------------------------------------------------------------

    def create(self, name: str, speakers: list[str] | None = None) -> Dataset:
        dataset = Dataset(name=name, speakers=speakers or [])
        self._dir(dataset.id).mkdir(parents=True)
        (self._dir(dataset.id) / "audio").mkdir()
        self._save_dataset(dataset)
        return dataset

    def list(self) -> list[dict]:
        """All datasets with per-status utterance counts, newest first."""
        datasets = []
        for path in sorted(self.root.glob("*/dataset.json")):
            try:
                dataset = Dataset.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # skip corrupt/partial files rather than breaking the listing
            counts = dict.fromkeys(UTTERANCE_STATUSES, 0)
            for utterance in self._load_utterances(dataset.id):
                counts[utterance.status] += 1
            datasets.append({**dataset.model_dump(), "counts": counts})
        return sorted(datasets, key=lambda d: -d["created_at"])

    def get(self, dataset_id: str) -> dict:
        dataset = self._read_dataset(dataset_id)
        return {
            **dataset.model_dump(),
            "utterances": [u.model_dump() for u in self._load_utterances(dataset_id)],
        }

    def delete(self, dataset_id: str) -> bool:
        with self._lock:
            directory = self._dir(dataset_id)
            if directory.is_dir():
                shutil.rmtree(directory)
                return True
            return False

    # -- utterances ------------------------------------------------------------

    def add_utterance(self, dataset_id: str, audio_bytes: bytes, filename: str,
                      transcript: str, speaker: str) -> Utterance:
        self._read_dataset(dataset_id)
        transcript = _validated_transcript(transcript)  # verbatim beyond this point
        speaker = _validated_speaker(speaker)
        wav_bytes = to_24k_mono_wav(audio_bytes, Path(filename).suffix)
        info = sf.info(io.BytesIO(wav_bytes))
        with self._lock:
            utterances = self._load_utterances(dataset_id)
            utt_id = self._new_utt_id(dataset_id, {u.utt_id for u in utterances})
            utterance = Utterance(
                utt_id=utt_id,
                wav=f"audio/{utt_id}.wav",
                transcript=transcript,
                speaker=speaker,
                duration_s=round(info.frames / info.samplerate, 3),
                sample_rate=info.samplerate,
            )
            (self._dir(dataset_id) / utterance.wav).write_bytes(wav_bytes)
            self._save_utterances(dataset_id, [*utterances, utterance])
        self._remember_speaker(self._read_dataset(dataset_id), speaker)
        return utterance

    def update_utterance(self, dataset_id: str, utt_id: str, *,
                         transcript: str | None = None,
                         speaker: str | None = None,
                         status: str | None = None) -> Utterance:
        dataset = self._read_dataset(dataset_id)
        if transcript is not None:
            transcript = _validated_transcript(transcript)  # verbatim beyond this point
        if speaker is not None:
            speaker = _validated_speaker(speaker)
        if status is not None:
            status = _validated_status(status)
        with self._lock:
            utterances = self._load_utterances(dataset_id)
            for utterance in utterances:
                if utterance.utt_id != utt_id:
                    continue
                if transcript is not None:
                    utterance.transcript = transcript
                if speaker is not None:
                    utterance.speaker = speaker
                if status is not None:
                    utterance.status = status
                self._save_utterances(dataset_id, utterances)
                self._remember_speaker(dataset, utterance.speaker)
                return utterance
            raise KeyError(utt_id)

    def review_queue(self, dataset_id: str, status: str = "recorded") -> list[Utterance]:
        self._read_dataset(dataset_id)
        wanted = _validated_status(status)
        return [u for u in self._load_utterances(dataset_id) if u.status == wanted]

    def wav_path(self, dataset_id: str, utt_id: str) -> Path:
        """Absolute path of an utterance's wav; KeyError if either is unknown."""
        self._read_dataset(dataset_id)
        for utterance in self._load_utterances(dataset_id):
            if utterance.utt_id == utt_id:
                return self._dir(dataset_id) / utterance.wav
        raise KeyError(utt_id)

    def record_scores(self, dataset_id: str, utt_id: str, scores: dict[str, float]) -> Utterance:
        """Persist preview QC scores (never gates — prepare.py owns gating)."""
        with self._lock:
            utterances = self._load_utterances(dataset_id)
            for utterance in utterances:
                if utterance.utt_id == utt_id:
                    utterance.scores = scores
                    self._save_utterances(dataset_id, utterances)
                    return utterance
            raise KeyError(utt_id)

    def adopt_enhanced(self, dataset_id: str, utt_id: str, enhanced_path: Path,
                       model_name: str) -> Utterance:
        """Swap the utterance's audio to an enhanced version, keeping the
        original on disk and traceable via enhanced_from/enhanced_by."""
        with self._lock:
            utterances = self._load_utterances(dataset_id)
            for utterance in utterances:
                if utterance.utt_id != utt_id:
                    continue
                new_rel = f"audio/{utt_id}.enhanced.wav"
                (self._dir(dataset_id) / new_rel).write_bytes(enhanced_path.read_bytes())
                enhanced_path.unlink(missing_ok=True)
                utterance.enhanced_from = utterance.wav
                utterance.enhanced_by = model_name
                utterance.wav = new_rel
                info = sf.info(self._dir(dataset_id) / new_rel)
                utterance.duration_s = round(info.frames / info.samplerate, 3)
                utterance.sample_rate = info.samplerate
                self._save_utterances(dataset_id, utterances)
                return utterance
            raise KeyError(utt_id)

    def find_utterance(self, utt_id: str) -> tuple[str, Utterance, Path] | None:
        """Locate an utterance across all datasets (voice-clone ref lookup)."""
        for dataset in self.list():
            dataset_id = dataset["id"]
            for utterance in self._load_utterances(dataset_id):
                if utterance.utt_id == utt_id:
                    return dataset_id, utterance, self._dir(dataset_id) / utterance.wav
        return None
    # -- bulk in/out -------------------------------------------------------------

    def import_zip(self, dataset_id: str, zip_bytes: bytes) -> dict:
        """Import a zip of `audio/*.wav` + `transcripts.jsonl`.

        Each jsonl line is {file, transcript, speaker}. Archive entries not
        listed in the manifest are ignored; entries with a bad path, missing
        audio, or missing/empty transcript are skipped and reported instead of
        failing the whole import.
        """
        self._read_dataset(dataset_id)
        imported: list[Utterance] = []
        skipped: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            _assert_safe_zip(zf)
            if "transcripts.jsonl" not in zf.namelist():
                raise ValueError("zip has no transcripts.jsonl")
            for raw in zf.read("transcripts.jsonl").decode("utf-8").splitlines():
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    skipped.append({"file": raw.strip()[:80], "reason": "invalid JSON line"})
                    continue
                if not isinstance(entry, dict):
                    skipped.append({"file": raw.strip()[:80], "reason": "entry must be an object"})
                    continue
                file_name = entry.get("file")
                try:
                    if not (isinstance(file_name, str)
                            and isinstance(entry.get("transcript"), str)
                            and isinstance(entry.get("speaker"), str)):
                        raise ValueError("entry needs string file, transcript and speaker")
                    imported.append(self.add_utterance(
                        dataset_id, zf.read(file_name), file_name,
                        entry["transcript"], entry["speaker"]))
                except (KeyError, ValueError, OSError) as e:
                    skipped.append({"file": str(file_name), "reason": str(e) or type(e).__name__})
        return {"imported": [u.model_dump() for u in imported], "skipped": skipped}

    def export_zip(self, dataset_id: str) -> tuple[bytes, str]:
        """Zip of accepted utterances only: audio/*.wav + transcripts.jsonl.

        Mirrors the import manifest (plus utt_id for traceability) so `articul
        prepare` can consume the unpacked layout directly. Returns
        (zip_bytes, attachment filename).
        """
        dataset = self._read_dataset(dataset_id)
        accepted = [u for u in self._load_utterances(dataset_id) if u.status == "accepted"]
        if not accepted:
            raise ValueError("no accepted utterances to export")
        buffer = io.BytesIO()
        lines: list[dict] = []
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
            for utterance in accepted:
                wav_path = self._dir(dataset_id) / utterance.wav
                if not wav_path.exists():
                    continue  # dropping the line keeps the zip self-consistent
                zf.write(wav_path, utterance.wav)
                lines.append({
                    "utt_id": utterance.utt_id,
                    "file": utterance.wav,
                    "transcript": utterance.transcript,
                    "speaker": utterance.speaker,
                })
            if not lines:
                raise ValueError("accepted utterances have no audio on disk")
            zf.writestr(
                "transcripts.jsonl",
                "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
        return buffer.getvalue(), ascii_filename(dataset.name, "_accepted.zip")
