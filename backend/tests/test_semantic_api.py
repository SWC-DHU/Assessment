import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import openpyxl
from openai import AsyncOpenAI
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database, main
from app.config import settings
from app.services import llm_extractor


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "default_llm_api_key", "")
    with TestClient(main.app) as client:
        yield client
    engine.dispose()


def test_workbook_revision_template_export_and_restore(api):
    folder = Path(__file__).resolve().parents[1]
    gold = json.loads((folder / "experiments/battery_module_gold.json").read_text())
    paths = list((folder / "uploads").glob("*/*_工艺需求文件.xlsx"))
    if not paths:
        pytest.skip("本地人工标注工作簿未包含在工作区")
    session = api.post("/api/sessions", json={}).json()
    url = f"/api/sessions/{session['id']}"
    versions = f"/api/semantics/sessions/{session['id']}/versions"
    catalog = api.get("/api/semantics/catalog").json()
    assert catalog["ontology_version"] == "1.0.0"
    good, bad = api.post(f"{url}/files", files=[
        ("files", ("工艺需求文件.xlsx", paths[0].read_bytes())), ("files", ("损坏.xlsx", b"not an xlsx")),
    ]).json()
    assert (good["parse_status"], bad["parse_status"]) == ("success", "error")
    assert api.post(f"{url}/files/{bad['id']}/reparse").json()["parse_status"] == "error"
    first = api.post(f"{url}/run", json={"file_ids": [good["id"]]}).json()
    current = first["current_extracted_json"]["semantic"]
    assert [(item["number"], item["name"]) for item in current["operations"]] == [tuple(row[:2]) for row in gold["rows"]]
    assert all(item["equipment_requirement"] is None for item in current["operations"])
    assert first["assistant_message"]["debug_info"]["request_count"] == 0
    assert first["assistant_message"]["debug_info"]["usage"] == {}
    import_version = api.post(
        f"/api/semantics/sessions/{session['id']}/project",
        json={"file_ids": [good["id"]]},
    ).json()
    assert import_version["content_digest"] == current["content_digest"]
    assert api.get(f"{versions}/{current['run_id']}/export").json() == current

    rev = api.post(f"{url}/run", json={
        "message": " 把第三道工序的工时修改为45秒。 ", "base_version_id": current["run_id"],
    }).json()
    child = rev["current_extracted_json"]["semantic"]
    assert child["parent_version_id"] == current["run_id"]
    assert child["operations"][:2] == current["operations"][:2]
    assert child["materials"] == current["materials"]
    assert child["operations"][2]["number"] == "03"
    constraint = next(item for item in child["constraints"] if item["parameter_code"] == "process_time")
    assert constraint["value"] == 45
    assert constraint["evidence"][0]["document_id"] == rev["user_message"]["id"]
    assert rev["current_extracted_json"]["operations"][2]["process_time_sec"] == 45
    assert api.post(f"{versions}/{child['run_id']}/validate").json()["status"] == child["validation"]["status"]
    assert api.get(url).json()["current_extracted_json"]["semantic"] == child

    table = next(template for template in api.get("/api/templates").json() if template["template_type"] == "table_mapping")
    template_url = f"/api/templates/{table['id']}"
    body = {"data": rev["current_extracted_json"]}
    preview = api.post(f"{template_url}/preview", json=body).json()
    hour = next(index for index, header in enumerate(preview["table_headers"]) if "工时" in header)
    assert preview["table_rows"][2][hour] == "45"
    assert preview["cell_states"][0][hour] == "source_missing"
    assert "原文未提供" in preview["table_rows"][0]
    export = api.post(f"{template_url}/export", json={**body, "export_format": "xlsx"})
    book = openpyxl.load_workbook(io.BytesIO(export.content))
    assert book.active.cell(4, hour + 1).value == 45
    assert api.post(f"{template_url}/export", json={**body, "export_format": "md"}).status_code == 200
    assert api.patch(template_url, json={"name": "修订后模板"}).json()["version"] == 2
    revision_history = api.get(f"{template_url}/versions").json()
    assert [(row["version"], row["definition"]["name"]) for row in revision_history] == [
        (2, "修订后模板"), (1, table["name"]),
    ]
    assert api.patch(url, json={"current_extracted_json": {"unsafe": True}}).status_code == 409
    rerun = api.post(f"{url}/rerun/{rev['assistant_message']['id']}").json()
    assert rerun["current_extracted_json"]["semantic"]["content_digest"] == child["content_digest"]
    assert len(api.get(versions).json()) == 4


def test_provider_contract_budget_repair_and_redacted_403(api, monkeypatch):
    request_log = []
    secret = "opaque-local-test-value-189356"
    response_mode = "repair"

    def handler(request):
        call = json.loads(request.content)
        request_log.append(call)
        assert request.headers["authorization"] == f"Bearer {secret}"
        if response_mode != "repair":
            error = {"message": f"No access to model {call['model']} {secret}", "code": "model_access"}
            if response_mode == "policy":
                error = {"message": f"Regional policy blocked the request {secret}", "code": "region_policy"}
            return httpx.Response(403, json={"error": error})
        content = "not JSON"
        if len(request_log) == 2:
            content = json.dumps({
                "operations": [{"id": "clean", "name": "清洗", "number": "01",
                                "evidence": [{"quote": "工序01清洗"}]}],
            }, ensure_ascii=False)
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "model": "provider-reported", "created": 1,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
        })

    monkeypatch.setattr(llm_extractor, "AsyncOpenAI", lambda **kwargs: AsyncOpenAI(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ))
    agent = api.post("/api/agents", json={
        "name": "Local protocol fixture", "base_url": "https://test-provider.example/v1",
        "api_key": secret, "model": "selected-model", "execution": {"request_budget": 2, "timeout_seconds": 5},
    }).json()
    assert agent["execution"]["request_budget"] == 2
    session = api.post("/api/sessions", json={"agent_id": agent["id"]}).json()
    url = f"/api/sessions/{session['id']}"
    result = api.post(f"{url}/run", json={"message": "工序01清洗"}).json()
    assert result["current_extracted_json"]["semantic"]["operations"][0]["number"] == "01"
    debug = result["assistant_message"]["debug_info"]
    assert debug["request_count"] == 2 and debug["usage"]["total_tokens"] == 40
    assert debug["model_used"] == "provider-reported"
    assert all(call["model"] == "selected-model" for call in request_log)
    assert any(stage["stage"] == "llm_repair" for stage in debug["pipeline_stages"])

    for mode, code in (("model", "model_access_denied"), ("policy", "provider_forbidden")):
        response_mode = mode
        failed = api.post(f"{url}/run", json={"message": "补充测试"}).json()
        assert failed["assistant_message"]["debug_info"]["error_code"] == code
        assert secret not in json.dumps(failed, ensure_ascii=False)
        assert secret not in api.get(url).text


def test_cancel_before_run_does_not_leave_messages(api):
    from uuid import uuid4

    session = api.post("/api/sessions", json={}).json()
    url = f"/api/sessions/{session['id']}"
    token = str(uuid4())
    assert api.post(f"{url}/runs/{token}/cancel").status_code == 202
    result = api.post(f"{url}/run", json={"run_token": token, "message": "执行前取消"})
    assert result.status_code == 499
    assert api.get(url).json()["messages"] == []
