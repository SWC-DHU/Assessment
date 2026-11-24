"""API integration tests for /api/model-assets and /api/resource-matching."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.services.seed import seed_initial_data
from model_assets.catalog import AssetCatalog, get_default_catalog

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/test_rm.db", connect_args={"check_same_thread": False})
    SessionTesting = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionTesting() as db:
        seed_initial_data(db)

    def override_db():
        db = SessionTesting()
        try:
            yield db
        finally:
            db.close()

    # Copy default assets to isolated tmp dir so binding/import tests never pollute repo data/
    isolated_data = tmp_path / "assets_data"
    isolated_data.mkdir()
    src = get_default_catalog()
    for asset in src.all_assets():
        (isolated_data / f"{asset.asset_id}.json").write_text(asset.model_dump_json(indent=2), encoding="utf-8")
    iso_catalog = AssetCatalog(data_dir=isolated_data)

    monkeypatch.setattr("app.api.model_assets.get_default_catalog", lambda: iso_catalog)
    monkeypatch.setattr("app.api.resource_matching.get_default_catalog", lambda: iso_catalog)
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as tc:
            yield tc
    finally:
        app.dependency_overrides.clear()


def test_model_assets_api_endpoints(client: TestClient):
    schema = client.get("/api/model-assets/schema")
    assert schema.status_code == 200
    assert "identity" in schema.json()["properties"]

    summary = client.get("/api/model-assets/summary").json()
    assert summary["total_assets"] >= 50
    assert summary["valid_assets"] == summary["total_assets"]

    listed = client.get("/api/model-assets?category=cleaning&capability=LASER_CLEAN").json()
    assert listed["total"] >= 3
    first_id = listed["items"][0]["asset"]["identity"]["asset_id"]

    detail = client.get(f"/api/model-assets/{first_id}")
    assert detail.status_code == 200
    assert detail.json()["asset"]["identity"]["asset_id"] == first_id

    # Update binding
    patched = client.patch(
        f"/api/model-assets/{first_id}/binding",
        json={"binding_status": "bound", "model_uri": f"geometry/{first_id}.glb", "file_format": "glb"},
    )
    assert patched.status_code == 200
    assert patched.json()["asset"]["model_binding"]["binding_status"] == "bound"

    # Validate invalid payload
    val = client.post("/api/model-assets/validate", json={"payload": {"identity": {"asset_id": "BAD"}}})
    assert val.status_code == 200
    assert val.json()["valid"] is False

    # Export JSON
    exp = client.get("/api/model-assets/export")
    assert exp.status_code == 200
    assert exp.json()["count"] >= 50


def test_resource_matching_api_e2e_from_session_version(client: TestClient):
    assert client.get("/api/resource-matching/schema").status_code == 200
    assert len(client.get("/api/resource-matching/tools").json()) >= 5
    assert len(client.get("/api/resource-matching/mappings").json()) == 6

    # Create session, upload battery workbook, run semantic parsing with narrative
    session = client.post("/api/sessions", json={"title": "电池模组匹配端到端"}).json()
    sid = session["id"]
    wb_path = ROOT / "test case/工艺需求文件.xlsx"
    narrative = (ROOT / "test case/自然语言建模需求.md").read_text(encoding="utf-8")

    with wb_path.open("rb") as fh:
        up = client.post(f"/api/sessions/{sid}/files", files={"files": (wb_path.name, fh, "application/octet-stream")})
    assert up.status_code == 201
    fid = up.json()[0]["id"]

    run_parse = client.post(
        f"/api/sessions/{sid}/run",
        json={"message": narrative, "file_ids": [fid], "strategy": "semantic", "use_demo_fallback": False},
    )
    assert run_parse.status_code == 200
    versions = client.get(f"/api/semantics/sessions/{sid}/versions").json()
    assert len(versions) >= 1
    vid = versions[0]["id"]

    # Run resource matching directly against the saved semantic version
    match_resp = client.post(
        f"/api/resource-matching/sessions/{sid}/versions/{vid}/run",
        json={"config": {"top_k": 3, "method": "C"}, "save": True},
    )
    assert match_resp.status_code == 201
    matched = match_resp.json()
    assert matched["summary"]["total_operations"] == 16
    assert matched["summary"]["matched_single_count"] == 16
    run_id = matched["run_id"]

    # List, fetch, export, delete saved matching run
    runs = client.get(f"/api/resource-matching/runs?session_id={sid}").json()
    assert any(r["run_id"] == run_id for r in runs)
    fetched = client.get(f"/api/resource-matching/runs/{run_id}").json()
    assert fetched["run_id"] == run_id
    exported = client.get(f"/api/resource-matching/runs/{run_id}/export")
    assert exported.status_code == 200
    assert exported.json()["run_id"] == run_id

    # Cross-session access returns 404
    other_session = client.post("/api/sessions", json={"title": "other"}).json()
    assert client.post(f"/api/resource-matching/sessions/{other_session['id']}/versions/{vid}/run").status_code == 404

    assert client.delete(f"/api/resource-matching/runs/{run_id}").status_code == 204
    assert client.get(f"/api/resource-matching/runs/{run_id}").status_code == 404
