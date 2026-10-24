# ElevenLabs Studio — feature findings (REA evidence)

Findings from a REA static analysis of the shipped ElevenLabs web app bundles
(85 JS chunks from elevenlabs.io sign-in + studio marketing pages, fetched
2025-10-24). Analysis: `analyze_javascript_application`, 973 graph nodes /
1,044 edges, 870 findings, coverage **partial** (one chunk exceeded the
analyzer worker heap; dynamic storage names unresolved). Evidence record:
`jag_1900f9c0d7485c165c4a5f78693f35379c41880d915a58c32b4f5eea05fe3c7b`
(raw analysis kept under /home/nampq/rea-work/elevenlabs/analysis.json).

Authority: shipped-artifact (static). Nothing below was observed executing.

## What Studio is (marketing page, corroborated by bundle strings)

Timeline-based audio/video editor: script → voiceover tracks → music/SFX →
captions → export. Feature list from the shipped copy: TTS voiceovers
(10,000+ voices), Eleven Music, AI Sound Effects, Captions, Voice Changer,
Transcription, Voice Isolator, Speech Correction ("edit the script, the same
voice regenerates"), Studio Agent (AI co-editor), video support (MP4/MOV),
public share links with timestamped comments, 32+ languages.

## Studio API surface recovered from the bundle

- `/v1/studio/projects/{projectId}` — project CRUD
- `/v1/studio/projects/{projectId}/chapters/{chapterId}/extended` — chapters
- `/v1/studio/projects/{projectId}/snapshots` and `/content-snapshots` —
  snapshot/restore (matches telemetry event `studio_restore_project`)
- `/v1/studio/projects/{projectId}/muted-tracks` — per-track mute
- `/v1/studio/projects/{projectId}/access` — sharing/collaboration
- `/v1/studio/projects/{projectId}/unconvert` — recorded-audio → editable
  conversion (speech correction path)
- `/v2/studio/internal/projects/{projectId}/exports` — export
- `/v1/wave-studio/projects/by-song/{songId}` — music integration

Telemetry event names confirm editor mechanics: `studio_history_action`
(undo/redo), `studio_voice_settings_changed`, `studio_track_duplicate_order_repaired`.

## Adjustable voice parameters (the part this studio clones)

From chunk `1qh-_amfypgaf.js` — voice settings are quantized to 0–1 in 0.01
steps and encoded into cache keys:

- `stability` → `_s{100*v}`
- `similarity_boost` → `_sb{100*v}`
- `style` → `_se{100*v}` (gated by `can_use_style`)
- `quality` → `_q{v}`
- `use_speaker_boost` → `_b` (boolean, gated by `can_use_speaker_boost`)
- `speed` → `_…{100*v}`

Models referenced in the bundles: `eleven_v3`, `eleven_v4`,
`eleven_v3_conversational`, `eleven_flash_v2(5)`, `eleven_turbo_v2_5`,
`eleven_lite`, `eleven_monolingual_v1`.

Languages: two lists in the bundle — a core ~32-language list and an extended
list (`{key:"vi",name:"Vietnamese",localizedName:…}`, plus 70+ more).
Vietnamese is a first-class `languageCode:"vi"`.

## How Articul Studio maps to this

| ElevenLabs Studio | Articul Studio (this repo, `studio/`) |
| --- | --- |
| Projects/chapters/sections | `ProjectStore` JSON files with ordered `Section`s |
| Voice settings sliders | engine-declared `ParamSpec` schema → dynamic UI |
| `stability/similarity_boost/style/speed/speaker_boost` | placeholder engine mirrors these; Articul will declare its own |
| Speech correction (edit → regenerate) | per-section regenerate |
| Snapshots/restore | not built (YAGNI) |
| Export | `/api/projects/{id}/export` WAV concat |
| 32+ languages | Vietnamese-first (`normalize_vi`, `language: "vi"`) |
| Eleven TTS models | `placeholder` now, `ArticulEngine` seam when the model lands |

Unknowns: dynamic localStorage keys, runtime behavior (static analysis only),
and the contents of the heap-exhausted chunk (`1q7h37uog-929.js`, 234KB).
