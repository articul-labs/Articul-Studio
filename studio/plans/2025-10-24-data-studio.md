# Data Studio — plan (2025-10-24)

Turn the generation studio into a **data studio for training**: record/import
utterances, review them, export in the raw layout `articul/data/prepare.py`
consumes. Builds on the `studio/` FastAPI app (branch
`feature/vietnamese-studio-mini`, stacked on PR #1).

## Decisions (defaults chosen by Bot, confirmed by Jesse going ahead)

1. **Both ingest paths in V1**: browser recorder (MediaRecorder) AND zip import.
2. **Speaker is a first-class field from day one** (RawUtterance requires it);
   V1 keeps a simple string speaker per utterance with a dataset-level list.
3. **Human review gates acceptance**: every clip must be listened to and
   accepted/rejected in the review UI. `prepare.py` gates remain the official
   quality bar — studio review is the human bar.
4. **Per-clip enhance is V1** (clearvoice FRCRN_SE_16K): a button in review,
   never a default pipeline step. Result stored with `enhanced_by` for
   traceability. Denoising everything would bake artifacts into training data
   and inflate DNSMOS.
5. **Deferred**: separation (MossFormer2_SS_16K), augmentation
   (train/data_generation), UTMOS (lives in articul, not this repo's
   speechscore).

## Hard rules (apply to every todo)

- **Transcripts are stored VERBATIM** — exactly as received, never normalized
  (articul rule: normalization is the model frontend's job, I005). The
  existing `/api/normalize` stays a preview-only helper.
- **Do not reimplement scoring/gating** that `articul prepare` owns. Studio
  scoring via `speechscore/` is *preview only*.
- **Do not download models or heavy deps in tests.** Root disk is 100% full;
  /home has ~7GB. Heavy imports (speechscore, clearvoice, torch) are guarded
  and tests skip when absent (`pytest.importorskip`).
- Studio stays a **thin API**: heavy audio work runs in
  `server/processing/` modules behind small routes; no queue system in V1 —
  synchronous subprocess/np calls with timeouts.
- `TMPDIR=/home/nampq/rea-work/tmp` when running uv/pytest (root fs full).
- Tests: `cd studio && uv run pytest -q` must stay green.

## Contracts

### Dataset storage layout

```text
studio/data/datasets/{dataset_id}/
├── dataset.json     # {id, name, speakers: [..], created_at}
├── utterances.jsonl # one utterance doc per line
└── audio/{utt_id}.wav   # always 24 kHz mono wav on disk after ingest
```

Utterance doc:

```json
{"utt_id": "studs01_ab12cd34", "wav": "audio/studs01_ab12cd34.wav",
 "transcript": "verbatim text", "speaker": "nampq",
 "status": "recorded|accepted|rejected", "duration_s": 3.42,
 "sample_rate": 24000, "scores": null,
 "enhanced_by": null, "created_at": 1730000000.0}
```

### HTTP API (S1 implements; S3 builds UI against this)

```text
POST   /api/datasets                                  {name, speakers?} -> 201 dataset doc
GET    /api/datasets                                  -> list + per-status counts
GET    /api/datasets/{id}                             -> dataset doc + utterances
DELETE /api/datasets/{id}
POST   /api/datasets/{id}/utterances                  multipart: file(audio), transcript, speaker
                                                      (webm/ogg/mp3 -> ffmpeg -> 24k mono wav; status=recorded)
POST   /api/datasets/{id}/import                      multipart zip: audio/*.wav + transcripts.jsonl
                                                      (lines: {file, transcript, speaker})
PATCH  /api/datasets/{id}/utterances/{utt_id}         {transcript?, speaker?, status?}
GET    /api/datasets/{id}/review?status=recorded      -> review queue
GET    /api/datasets/{id}/export                      -> zip: audio/*.wav + transcripts.jsonl
                                                      (accepted only; utt_id prefixed per dataset,
                                                       source-prefixed unique for RawUtterance)
```

Processing wrappers (S2; wired to routes by integration):

```python
# server/processing/qc.py
quick_score(wav_path) -> {"dnsmos_sig": f, "dnsmos_bak": f, "dnsmos_ovr": f,
                          "duration_s": f}   # raises ProcessingUnavailable if deps missing

# server/processing/enhance.py
enhance(wav_path, model="FRCRN_SE_16K") -> Path  # new 16k/48k wav beside input, enhanced_by=model
```

## Work split

- **T1** (subagent S1): dataset store + router + tests — `server/dataset_store.py`,
  `server/datasets_api.py`, `tests/test_datasets.py`. Router is an `APIRouter`;
  integration mounts it. Does NOT touch `main.py`.
- **T2** (subagent S2): processing wrappers + tests — `server/processing/`.
  Guarded imports; no model downloads; speechscore needs `sys.path` to repo
  root (its modules import `from scores.dnsmos...`).
- **T3** (subagent S3): dataset UI — `web/dataset.html`, `web/dataset.js`,
  `web/dataset.css` against the HTTP contract. Vanilla JS, no frameworks.
- **T4** (Bot, after T1–T3): mount router in `main.py`, wire QC/enhance routes,
  link pages, e2e check (uvicorn + headless chrome), commit.
- **T5** (backlog): separation, augmentation, UTMOS bridge.
