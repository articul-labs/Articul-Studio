"""HTTP API for datasets: every route from the plan's contract (see
plans/2025-10-24-data-studio.md). Thin layer over DatasetStore: parse the
multipart requests, map store errors to status codes, return pydantic docs.

Mounted by main.py during integration; tests build a fresh FastAPI app and
include the router directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from .dataset_store import Dataset, DatasetStore, Utterance
from .processing.enhance import enhance
from .processing.qc import quick_score
from .processing._compat import ProcessingUnavailable

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

# Module-level store, same pattern as server.main's project store: the
# integrating app inherits this default root, tests rebind it per test.
store = DatasetStore(Path(__file__).resolve().parent.parent / "data" / "datasets")


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    speakers: list[str] = Field(default_factory=list)


class UtteranceUpdate(BaseModel):
    transcript: str | None = None
    speaker: str | None = None
    status: str | None = None


def store_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """DatasetStore signals missing resources with KeyError and bad input with
    ValueError; map those to 404/400 here so routes stay one-liners."""
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(404, f"not found: {e.args[0]}") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("", status_code=201)
def create_dataset(body: DatasetCreate) -> Dataset:
    return store.create(name=body.name, speakers=body.speakers)


@router.get("")
def list_datasets() -> list[dict]:
    return store.list()


@router.get("/{dataset_id}")
def get_dataset(dataset_id: str) -> dict:
    return store_call(store.get, dataset_id)


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str) -> dict:
    if not store_call(store.delete, dataset_id):
        raise HTTPException(404, "dataset not found")
    return {"deleted": dataset_id}


@router.post("/{dataset_id}/utterances", status_code=201)
def add_utterance(dataset_id: str, file: UploadFile = File(...),
                  transcript: str = Form(...), speaker: str = Form(...)) -> Utterance:
    """Ingest one clip (browser recording or file); stored as 24 kHz mono wav,
    status=recorded until a human reviews it."""
    return store_call(store.add_utterance, dataset_id, file.file.read(),
                      file.filename or "", transcript, speaker)


@router.post("/{dataset_id}/import")
def import_dataset_zip(dataset_id: str, file: UploadFile = File(...)) -> dict:
    """Bulk import a zip of audio/*.wav + transcripts.jsonl; skipped entries
    are reported in the response, not fatal."""
    return store_call(store.import_zip, dataset_id, file.file.read())


@router.patch("/{dataset_id}/utterances/{utt_id}")
def update_utterance(dataset_id: str, utt_id: str, body: UtteranceUpdate) -> Utterance:
    return store_call(store.update_utterance, dataset_id, utt_id,
                      transcript=body.transcript, speaker=body.speaker, status=body.status)


@router.get("/{dataset_id}/review")
def review_queue(dataset_id: str, status: str = "recorded") -> list[Utterance]:
    return store_call(store.review_queue, dataset_id, status)


@router.get("/{dataset_id}/export")
def export_dataset(dataset_id: str) -> Response:
    """Accepted utterances only, in the raw layout articul prepare consumes."""
    zip_bytes, filename = store_call(store.export_zip, dataset_id)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{dataset_id}/utterances/{utt_id}/qc")
def qc_utterance(dataset_id: str, utt_id: str) -> Utterance:
    """Preview-only DNSMOS scores (prepare.py remains the official gate)."""
    wav = store_call(store.wav_path, dataset_id, utt_id)
    try:
        scores = quick_score(wav)
    except ProcessingUnavailable as e:
        raise HTTPException(503, str(e)) from None
    return store_call(store.record_scores, dataset_id, utt_id, scores)


@router.post("/{dataset_id}/utterances/{utt_id}/enhance")
def enhance_utterance(dataset_id: str, utt_id: str) -> Utterance:
    """Per-clip human decision: clean one clip up with FRCRN, keep the
    original traceable. Not a pipeline default — enhanced audio bakes model
    artifacts into training data and inflates DNSMOS."""
    wav = store_call(store.wav_path, dataset_id, utt_id)
    try:
        enhanced = enhance(wav)
    except ProcessingUnavailable as e:
        raise HTTPException(503, str(e)) from None
    return store_call(store.adopt_enhanced, dataset_id, utt_id, enhanced, "FRCRN_SE_16K")
