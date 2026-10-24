"""Articul Studio API: engines, projects, section synthesis, export.

The UI is a static page served from ../web; all state flows through /api.
Synthesis normalizes Vietnamese text before handing it to the engine, so
engines never see raw digits or currency symbols.
"""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engines.registry import get_engine, get_engines
from .engines.vieneu import VieneuEngine
from .datasets_api import router as datasets_router
from .datasets_api import store as dataset_store
from .project_store import Project, ProjectStore, Section
from .util import ascii_filename

app = FastAPI(title="Articul Studio", version="0.1.0")
store = ProjectStore(Path(__file__).resolve().parent.parent / "data" / "projects")
app.include_router(datasets_router)

# Serve dataset audio for review playback. Local tool: no auth by design.
app.mount("/data", StaticFiles(directory=Path(__file__).resolve().parent.parent / "data"), name="data")


def _resolve_ref_audio(utt_id: str) -> str | None:
    """VieNeu zero-shot cloning: dataset recordings double as voice references.
    Only accepted utterances qualify — a rejected clip is a bad voice print."""
    found = dataset_store.find_utterance(utt_id.removeprefix("ref:"))
    if found is None or found[1].status != "accepted":
        return None
    return str(found[2])


for _engine in get_engines():
    if isinstance(_engine, VieneuEngine):
        _engine.ref_audio_resolver = _resolve_ref_audio

# Gap inserted between sections when playing/exporting a whole project.
SECTION_GAP_S = 0.35


class ProjectUpdate(BaseModel):
    title: str | None = None
    engine_id: str | None = None
    voice_id: str | None = None
    settings: dict[str, float] | None = None
    sections: list[Section] | None = None  # replaced wholesale


@app.get("/api/engines")
def list_engines() -> list[dict]:
    return [
        {
            "id": e.id,
            "name": e.name,
            "description": e.description,
            "language": e.language,
            "voices": [dataclasses.asdict(v) for v in e.voices],
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "min": p.min,
                    "max": p.max,
                    "default": p.default,
                    "step": p.step,
                    "kind": p.kind,
                }
                for p in e.params
            ],
        }
        for e in get_engines()
    ]


@app.get("/api/normalize")
def preview_normalize(text: str, engine_id: str = "placeholder") -> dict:
    """Show what the engine will actually receive — useful while tuning Vietnamese text."""
    engine = get_engine(engine_id)
    if engine is None:
        raise HTTPException(404, "unknown engine")
    return {"normalized": engine.normalize_text(text)}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [
        {"id": p.id, "title": p.title, "updated_at": p.updated_at} for p in store.list()
    ]


@app.post("/api/projects", status_code=201)
def create_project(body: dict) -> Project:
    engine = get_engine(str(body.get("engine_id", "placeholder")))
    if engine is None:
        raise HTTPException(404, "unknown engine")
    settings = engine.normalize_settings(body.get("settings"))
    default_voice = engine.voices[0].id if engine.voices else None
    if not body.get("voice_id") and default_voice is None:
        # Voiceless engines (vieneu registers voices = [] until presets are
        # known) need an explicit voice_id; voices[0] would IndexError.
        raise HTTPException(
            400,
            f"engine '{engine.id}' exposes no default voice — pass voice_id",
        )
    return store.create(
        title=str(body.get("title", "Dự án mới"))[:200],
        engine_id=engine.id,
        voice_id=str(body.get("voice_id", default_voice)),
        settings=settings,
    )


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> Project:
    project = store.get(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate) -> Project:
    project = store.get(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    if body.title is not None:
        project.title = body.title[:200]
    if body.engine_id is not None:
        engine = get_engine(body.engine_id)
        if engine is None:
            raise HTTPException(404, "unknown engine")
        project.engine_id = engine.id
        project.settings = engine.normalize_settings(project.settings)
    if body.voice_id is not None:
        project.voice_id = body.voice_id
    if body.settings is not None:
        engine = get_engine(project.engine_id)
        project.settings = engine.normalize_settings(body.settings) if engine else body.settings
    if body.sections is not None:
        project.sections = body.sections
    return store.save(project)


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict:
    if not store.delete(project_id):
        raise HTTPException(404, "project not found")
    return {"deleted": project_id}


def _resolve_settings(project: Project, section) -> tuple:
    """Per-section voice/settings override, else the project default."""
    engine = get_engine(project.engine_id)
    if engine is None:
        raise HTTPException(409, "project engine is not available")
    voice_id = section.voice_id or project.voice_id
    settings = engine.normalize_settings(section.settings or project.settings)
    return engine, voice_id, settings


def _wav_response(result) -> Response:
    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers={"X-Duration-Seconds": f"{result.duration_s:.3f}"},
    )


@app.post("/api/projects/{project_id}/sections/{section_id}/synthesize")
def synthesize_section(project_id: str, section_id: str) -> Response:
    project = store.get(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    section = next((s for s in project.sections if s.id == section_id), None)
    if section is None:
        raise HTTPException(404, "section not found")
    if not section.text.strip():
        raise HTTPException(400, "section text is empty")

    engine, voice_id, settings = _resolve_settings(project, section)
    result = engine.synthesize(engine.normalize_text(section.text), voice_id, settings)
    return _wav_response(result)


@app.get("/api/projects/{project_id}/export")
def export_project(project_id: str) -> Response:
    """Concatenate every non-empty section into one WAV, with short gaps."""
    project = store.get(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    spoken = [s for s in project.sections if s.text.strip()]
    if not spoken:
        raise HTTPException(400, "nothing to export")

    pieces: list[np.ndarray] = []
    sample_rate = None
    for section in spoken:
        engine, voice_id, settings = _resolve_settings(project, section)
        result = engine.synthesize(engine.normalize_text(section.text), voice_id, settings)
        audio, sr = sf.read(io.BytesIO(result.wav_bytes), dtype="float32")
        sample_rate = sample_rate or sr
        pieces.append(audio)
        pieces.append(np.zeros(int(sample_rate * SECTION_GAP_S)))

    buffer = io.BytesIO()
    sf.write(buffer, np.concatenate(pieces), sample_rate, format="WAV")
    return Response(
        content=buffer.getvalue(),
        media_type="audio/wav",
        headers={
            "Content-Disposition": f'attachment; filename="'
            f'{ascii_filename(project.title, ".wav", "project")}"'
        },
    )


# Static UI last so /api routes win.
web_dir = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
