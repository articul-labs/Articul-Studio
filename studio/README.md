# Articul Studio

Small, Vietnamese-first studio in the shape of ElevenLabs Studio: write a
script, split it into sections, assign voices, tune adjustable parameters per
project or per section, generate, play, and export a single WAV.

Built to host the **Articul** Vietnamese TTS model (`~/projects/articul-labs/articul`)
— until the model's inference lands, a clearly-synthetic placeholder engine
keeps the whole pipeline demonstrable.

## Run

```bash
cd studio
uv sync
uv run uvicorn server.main:app --port 8322
# open http://localhost:8322
```

Tests:

```bash
uv run pytest -q
```

## How it fits together

```
browser (web/, vanilla JS)
   │  GET /api/engines          → engine list + ParamSpec schema (drives the sliders)
   │  GET/PATCH /api/projects   → JSON-file project store
   │  POST …/sections/{id}/synthesize
   ▼
engine.normalize_text(text)     ← engine owns its text frontend
   ▼
TtsEngine.synthesize(text, voice_id, settings) → WAV bytes
```

- **Engines declare their own parameters** (`server/engines/base.py`,
  `ParamSpec`). The UI renders sliders/toggles from that schema, so swapping
  engines — or changing Articul's parameter set — needs no frontend change.
- **Text normalization is engine-owned too.** The default is
  `server/normalize_vi.py` (numbers → words with the lẻ/mốt/lăm/tư rules,
  currency, %, clock times, km/kg). The Articul engine overrides it to call
  `articul.frontend.normalize` so the studio and the model always agree on
  what gets spoken. `/api/normalize?text=…&engine_id=…` previews this.
- **Projects** are JSON files under `studio/data/projects/` (gitignored):
  ordered sections, per-section voice/settings overrides, project defaults.

## Plugging in Articul

1. Export/checkpoint the model and set `ARTICUL_MODEL_DIR=/path/to/dir`.
   The engine registers itself automatically (`server/engines/registry.py`).
2. Implement the two hooks in `server/engines/articul.py`:
   - `load_model()` — load weights from the model dir.
   - `_synthesize_with_model(text, voice_id, settings)` — run inference,
     return `(samples_f32, sample_rate)`. Expected path mirrors articul's own
     frontend: `normalize → phonemize(dialect, lexicon) → to_input_ids → model`
     (see `articul/src/articul/frontend/` and the `Synthesizer` protocol in
     `articul/src/articul/eval/synthesis.py`).
3. Replace the placeholder `params` list with Articul's real adjustable
   parameters and set `voices` from the model's speaker table. Done — the UI
   picks everything up from `GET /api/engines`.

## Feature provenance

`docs/ELEVENLABS-STUDIO-FEATURES.md` documents the ElevenLabs Studio feature
set this app mirrors, with the REA evidence (shipped-bundle static analysis)
that backs each mapping.
