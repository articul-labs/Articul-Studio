"""Behavioral tests for the dataset store + /api/datasets router.

Audio fixtures are generated in-test: tiny wavs via soundfile, a webm via an
ffmpeg subprocess (skipped when ffmpeg is absent — plan rule: no downloads,
no heavy deps).
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.datasets_api as datasets_api
from server.dataset_store import DatasetStore

SR = 24000


def make_client(tmp_path: Path) -> TestClient:
    # Fresh FastAPI app per test with the store rebound to an isolated dir;
    # main.py mounts this same router during integration.
    datasets_api.store = DatasetStore(tmp_path / "datasets")
    app = FastAPI()
    app.include_router(datasets_api.router)
    return TestClient(app)


def wav_bytes(duration_s: float = 0.5, sample_rate: int = SR, channels: int = 1) -> bytes:
    t = np.linspace(0.0, duration_s, int(sample_rate * duration_s), endpoint=False)
    audio = (0.2 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    if channels > 1:
        audio = np.stack([audio] * channels, axis=1)
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV")
    return buf.getvalue()


def webm_bytes(tmp_path: Path) -> bytes:
    wav = tmp_path / "fixture.wav"
    wav.write_bytes(wav_bytes())
    out = tmp_path / "fixture.webm"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", str(out)],
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"ffmpeg could not build a webm fixture: {proc.stderr.decode()[:200]}")
    return out.read_bytes()


def make_dataset(client: TestClient, name: str = "ds") -> dict:
    r = client.post("/api/datasets", json={"name": name})
    assert r.status_code == 201
    return r.json()


def upload(client: TestClient, dataset_id: str, audio: bytes, filename: str = "clip.wav",
           transcript: str = "Xin chào", speaker: str = "nampq"):
    return client.post(
        f"/api/datasets/{dataset_id}/utterances",
        files={"file": (filename, audio, "audio/wav")},
        data={"transcript": transcript, "speaker": speaker},
    )


def test_dataset_crud_roundtrip(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client, "Studs 01")
    assert ds["name"] == "Studs 01"
    assert ds["speakers"] == []

    listed = client.get("/api/datasets").json()
    assert [d["id"] for d in listed] == [ds["id"]]
    assert listed[0]["counts"] == {"recorded": 0, "accepted": 0, "rejected": 0}

    got = client.get(f"/api/datasets/{ds['id']}").json()
    assert got["name"] == "Studs 01" and got["utterances"] == []

    assert client.delete(f"/api/datasets/{ds['id']}").json() == {"deleted": ds["id"]}
    assert client.get(f"/api/datasets/{ds['id']}").status_code == 404
    assert client.delete(f"/api/datasets/{ds['id']}").status_code == 404


def test_invalid_dataset_id_rejected(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/datasets/../etc").status_code in (400, 404, 422)


def test_transcript_stored_verbatim_never_normalized(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client)
    # Digits/currency/punctuation that normalize_vi would rewrite must land byte-identical.
    transcript = "Năm 2025: gọi 0901234567, phí 1.000đ/km."
    r = upload(client, ds["id"], wav_bytes(), transcript=transcript)
    assert r.status_code == 201
    assert r.json()["transcript"] == transcript

    got = client.get(f"/api/datasets/{ds['id']}").json()
    assert got["utterances"][0]["transcript"] == transcript


def test_transcript_rejects_empty_and_oversized(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client)
    assert upload(client, ds["id"], wav_bytes(), transcript="   ").status_code == 400
    assert upload(client, ds["id"], wav_bytes(), transcript="x" * 2001).status_code == 400
    assert upload(client, ds["id"], wav_bytes(), transcript="x" * 2000).status_code == 201


def test_ingest_resamples_riff_wav_to_24k_mono(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client)
    r = upload(client, ds["id"], wav_bytes(sample_rate=48000, channels=2), "stereo48k.wav")
    assert r.status_code == 201
    utt = r.json()
    assert utt["status"] == "recorded"
    assert utt["sample_rate"] == 24000
    assert utt["duration_s"] == pytest.approx(0.5, abs=0.01)

    on_disk = sf.info(datasets_api.store.root / ds["id"] / utt["wav"])
    assert on_disk.samplerate == 24000 and on_disk.channels == 1
    assert (datasets_api.store.root / ds["id"] / utt["wav"]).read_bytes()[:4] == b"RIFF"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ingest_webm_converted_to_24k_mono_wav(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client)
    r = upload(client, ds["id"], webm_bytes(tmp_path), "clip.webm")
    assert r.status_code == 201
    utt = r.json()
    assert utt["sample_rate"] == 24000

    on_disk = datasets_api.store.root / ds["id"] / utt["wav"]
    assert on_disk.read_bytes()[:4] == b"RIFF"
    info = sf.info(on_disk)
    assert info.samplerate == 24000 and info.channels == 1


def test_ingest_rejects_undecodable_audio(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client)
    r = upload(client, ds["id"], b"definitely not audio", "junk.wav")
    assert r.status_code == 400
    assert client.get(f"/api/datasets/{ds['id']}").json()["utterances"] == []


def test_patch_updates_and_review_queue(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client)
    url = f"/api/datasets/{ds['id']}/utterances"
    ids = [upload(client, ds["id"], wav_bytes(), transcript=f"Câu {i}").json()["utt_id"]
           for i in range(3)]

    assert client.patch(f"{url}/{ids[0]}", json={"status": "accepted"}).json()["status"] == "accepted"
    assert client.patch(f"{url}/{ids[1]}", json={"status": "rejected"}).status_code == 200
    fixed = client.patch(f"{url}/{ids[2]}", json={"transcript": "Sửa: 24.000đ", "speaker": "minh"})
    assert fixed.status_code == 200
    assert fixed.json()["transcript"] == "Sửa: 24.000đ"  # patched transcript stays verbatim

    got = client.get(f"/api/datasets/{ds['id']}").json()
    assert "minh" in got["speakers"]  # dataset-level speaker list stays in sync

    counts = client.get("/api/datasets").json()[0]["counts"]
    assert counts == {"recorded": 1, "accepted": 1, "rejected": 1}

    recorded = client.get(f"/api/datasets/{ds['id']}/review").json()  # default: recorded
    assert [u["utt_id"] for u in recorded] == [ids[2]]
    accepted = client.get(f"/api/datasets/{ds['id']}/review?status=accepted").json()
    assert [u["utt_id"] for u in accepted] == [ids[0]]

    assert client.get(f"/api/datasets/{ds['id']}/review?status=nope").status_code == 400
    assert client.patch(f"{url}/{ids[0]}", json={"status": "nope"}).status_code == 400
    assert client.patch(f"{url}/missing", json={"status": "accepted"}).status_code == 404


def test_import_zip_skips_bad_entries(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client)
    lines = [
        {"file": "audio/a.wav", "transcript": "Câu một.", "speaker": "an"},
        {"file": "audio/b.wav", "transcript": "", "speaker": "an"},  # empty transcript
        {"file": "audio/missing.wav", "transcript": "Câu lost.", "speaker": "an"},
        {"file": "audio/c.wav", "speaker": "an"},  # no transcript key
        "not json",
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("audio/a.wav", wav_bytes())
        zf.writestr("audio/b.wav", wav_bytes(sample_rate=16000))  # converted on import too
        zf.writestr(
            "transcripts.jsonl",
            "\n".join(json.dumps(l, ensure_ascii=False) if isinstance(l, dict) else l
                      for l in lines))

    r = client.post(f"/api/datasets/{ds['id']}/import",
                    files={"file": ("batch.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200
    body = r.json()
    assert len(body["imported"]) == 1
    assert body["imported"][0]["transcript"] == "Câu một."
    assert body["imported"][0]["sample_rate"] == 24000
    assert {s["file"] for s in body["skipped"]} == {
        "audio/b.wav", "audio/missing.wav", "audio/c.wav", "not json"}
    assert "an" in client.get(f"/api/datasets/{ds['id']}").json()["speakers"]


def test_import_rejects_zip_slip_paths(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.wav", wav_bytes())
        zf.writestr("transcripts.jsonl",
                    json.dumps({"file": "../escape.wav", "transcript": "x", "speaker": "a"}))
    r = client.post(f"/api/datasets/{ds['id']}/import",
                    files={"file": ("evil.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 400
    assert client.get(f"/api/datasets/{ds['id']}").json()["utterances"] == []


def test_export_zip_contains_only_accepted(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client, "export set")
    url = f"/api/datasets/{ds['id']}/utterances"
    utts = [upload(client, ds["id"], wav_bytes(), transcript=t, speaker=s).json()
            for t, s in [("Câu một", "an"), ("Câu hai", "binh"), ("Câu ba", "an")]]
    client.patch(f"{url}/{utts[0]['utt_id']}", json={"status": "accepted"})
    client.patch(f"{url}/{utts[2]['utt_id']}", json={"status": "accepted"})

    r = client.get(f"/api/datasets/{ds['id']}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    disposition = r.headers["content-disposition"]
    assert "attachment" in disposition and disposition.endswith('.zip"')

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        lines = [json.loads(l) for l in zf.read("transcripts.jsonl").decode("utf-8").splitlines()]
    assert len(lines) == 2  # rejected/recorded clips are not exported
    assert {l["transcript"] for l in lines} == {"Câu một", "Câu ba"}
    utt_ids = [l["utt_id"] for l in lines]
    assert len(set(utt_ids)) == 2  # unique
    assert all(uid.startswith(ds["id"][:8] + "_") for uid in utt_ids)  # dataset-prefixed
    for line in lines:
        assert line["file"] in names  # every manifest line has its audio in the zip


def test_export_empty_dataset_rejected(tmp_path):
    client = make_client(tmp_path)
    ds = make_dataset(client)
    assert client.get(f"/api/datasets/{ds['id']}/export").status_code == 400


# -- enhance -> adopt_enhanced round trip (review finding: adopt_enhanced
#    used to copy onto its own destination, then unlink it) -------------------


def _uploaded_utterance(client: TestClient, dataset_id: str) -> str:
    resp = client.post(
        f"/api/datasets/{dataset_id}/utterances",
        files={"file": ("clip.wav", wav_bytes(), "audio/wav")},
        data={"transcript": "Ghi âm thử.", "speaker": "nampq"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["utt_id"]


def test_enhance_route_swaps_audio_and_keeps_original(tmp_path, monkeypatch):
    client = make_client(tmp_path)
    ds_id = client.post("/api/datasets", json={"name": "Enh"}).json()["id"]
    utt_id = _uploaded_utterance(client, ds_id)
    wav_path = datasets_api.store.wav_path(ds_id, utt_id)
    original_bytes = wav_path.read_bytes()

    # Fake the heavy wrapper with the real one's contract: writes
    # <stem>.enhanced.wav beside the input and returns that path.
    def fake_enhance(path, model="FRCRN_SE_16K"):
        dest = Path(path).with_name(f"{Path(path).stem}.enhanced.wav")
        dest.write_bytes(wav_bytes(duration_s=0.6))
        return dest

    monkeypatch.setattr(datasets_api, "enhance", fake_enhance)

    resp = client.post(f"/api/datasets/{ds_id}/utterances/{utt_id}/enhance")
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert doc["wav"] == f"audio/{utt_id}.enhanced.wav"
    assert doc["enhanced_by"] == "FRCRN_SE_16K"
    assert doc["enhanced_from"] == f"audio/{utt_id}.wav"
    # Both files survive: current audio + original for provenance.
    assert (tmp_path / "datasets" / ds_id / doc["wav"]).exists()
    assert (tmp_path / "datasets" / ds_id / doc["enhanced_from"]).read_bytes() == original_bytes


def test_enhance_route_rejects_second_enhance(tmp_path, monkeypatch):
    client = make_client(tmp_path)
    ds_id = client.post("/api/datasets", json={"name": "Enh2"}).json()["id"]
    utt_id = _uploaded_utterance(client, ds_id)

    def fake_enhance(path, model="FRCRN_SE_16K"):
        dest = Path(path).with_name(f"{Path(path).stem}.enhanced.wav")
        dest.write_bytes(wav_bytes(duration_s=0.6))
        return dest

    monkeypatch.setattr(datasets_api, "enhance", fake_enhance)
    assert client.post(f"/api/datasets/{ds_id}/utterances/{utt_id}/enhance").status_code == 200
    resp = client.post(f"/api/datasets/{ds_id}/utterances/{utt_id}/enhance")
    assert resp.status_code == 400
    assert "already enhanced" in resp.json()["detail"]


def test_load_utterances_fails_loudly_on_corrupt_line(tmp_path):
    store = DatasetStore(tmp_path / "ds")
    dataset = store.create("Corrupt")
    store.add_utterance(
        dataset.id, wav_bytes(), "a.wav", transcript="Câu một.", speaker="nampq"
    )
    utterances_path = tmp_path / "ds" / dataset.id / "utterances.jsonl"
    lines = utterances_path.read_text(encoding="utf-8").splitlines()
    lines.insert(0, '{"utt_id": "broken"')  # truncated JSON
    utterances_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Silent skipping would let the next save erase the corrupt line's
    # neighbours' audit trail; the store refuses instead.
    with pytest.raises(ValueError, match="corrupt utterance JSON"):
        store.list()
