from fastapi.testclient import TestClient

from server.main import app
from server.project_store import ProjectStore


def client_with_store(tmp_path):
    # Rebind the store to an isolated directory per test.
    app.state.store = None  # ensure no stale state
    import server.main as main

    main.store = ProjectStore(tmp_path / "projects")
    return TestClient(app)


def test_engine_schema_shape(tmp_path):
    client = client_with_store(tmp_path)
    engines = client.get("/api/engines").json()
    placeholder = next(e for e in engines if e["id"] == "placeholder")
    assert placeholder["language"] == "vi"
    keys = [p["key"] for p in placeholder["params"]]
    assert {"stability", "similarity_boost", "style", "speed", "speaker_boost"} <= set(keys)
    speed = placeholder["params"][[p["key"] for p in placeholder["params"]].index("speed")]
    assert speed["kind"] == "slider" and speed["min"] == 0.7 and speed["max"] == 1.2


def test_project_crud_and_synthesize(tmp_path):
    client = client_with_store(tmp_path)
    created = client.post(
        "/api/projects",
        json={"title": "Thử", "engine_id": "placeholder", "voice_id": "nu-mien-bac"},
    )
    assert created.status_code == 201
    project = created.json()
    section_id = project["sections"][0]["id"]

    updated = client.patch(
        f"/api/projects/{project['id']}",
        json={"sections": [{**project["sections"][0], "text": "Xin chào Việt Nam."}]},
    )
    assert updated.status_code == 200

    audio = client.post(
        f"/api/projects/{project['id']}/sections/{section_id}/synthesize"
    )
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content[:4] == b"RIFF"
    assert float(audio.headers["x-duration-seconds"]) > 0


def test_synthesize_rejects_empty_section(tmp_path):
    client = client_with_store(tmp_path)
    project = client.post(
        "/api/projects", json={"engine_id": "placeholder", "voice_id": "nu-mien-bac"}
    ).json()
    r = client.post(f"/api/projects/{project['id']}/sections/{project['sections'][0]['id']}/synthesize")
    assert r.status_code == 400


def test_export_concatenates_sections(tmp_path):
    client = client_with_store(tmp_path)
    project = client.post(
        "/api/projects", json={"engine_id": "placeholder", "voice_id": "nam-mien-bac"}
    ).json()
    sections = [
        {**project["sections"][0], "id": "s1", "text": "Câu thứ nhất."},
        {**project["sections"][0], "id": "s2", "text": "Câu thứ hai."},
    ]
    client.patch(f"/api/projects/{project['id']}", json={"sections": sections})
    exported = client.get(f"/api/projects/{project['id']}/export")
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "audio/wav"
    assert "attachment" in exported.headers["content-disposition"]


def test_bad_project_id_rejected(tmp_path):
    client = client_with_store(tmp_path)
    assert client.get("/api/projects/../etc").status_code in (400, 404, 422)
