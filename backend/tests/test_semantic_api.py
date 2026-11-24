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
    assert catalog["ontology_version"] == "3.0.0"
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
    assert all("thinking" not in call for call in request_log)
    assert any(stage["stage"] == "llm_repair" for stage in debug["pipeline_stages"])

    for mode, code in (("model", "model_access_denied"), ("policy", "provider_forbidden")):
        response_mode = mode
        failed = api.post(f"{url}/run", json={"message": "补充测试"}).json()
        assert failed["assistant_message"]["debug_info"]["error_code"] == code
        assert secret not in json.dumps(failed, ensure_ascii=False)
        assert secret not in api.get(url).text


def test_official_deepseek_uses_effective_batch_limit_and_split_budget(api, monkeypatch):
    requests = []

    def handler(request):
        call = json.loads(request.content)
        requests.append(call)
        cut = len(requests) == 1
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "model": "deepseek-flash", "created": 1,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None if cut else "{}"},
                         "finish_reason": "length" if cut else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": call["max_tokens"] if cut else 2,
                      "completion_tokens_details": {"reasoning_tokens": 0},
                      "total_tokens": 10 + (call["max_tokens"] if cut else 2)},
        })

    monkeypatch.setattr(llm_extractor, "AsyncOpenAI", lambda **kwargs: AsyncOpenAI(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ))
    agent = api.post("/api/agents", json={
        "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key": "fixture-key",
        "model": "deepseek-flash", "max_tokens": 16383,
    }).json()
    session_id = api.post("/api/sessions", json={"agent_id": agent["id"]}).json()["id"]
    session = f"/api/sessions/{session_id}"
    text = "整线生产节拍≤60秒/件。工序01清洗。工序02检测。"
    response = api.post(f"{session}/run", json={"message": text})
    assert response.status_code == 200
    debug = response.json()["assistant_message"]["debug_info"]
    assert (debug["max_tokens"], debug["batch_max_tokens"], debug["request_count"], debug["request_budget"]) == (16383, 8192, 3, 4)
    assert [call["max_tokens"] for call in requests] == [8192, 8192, 8192]
    assert all(call["thinking"] == {"type": "disabled"} and call["response_format"] == {"type": "json_object"}
               for call in requests)
    assert [(entry["batch_id"], entry["finish_reason"]) for entry in debug["provider_attempts"]] == [
        ("batch-1", "length"), ("batch-1.a", "stop"), ("batch-1.b", "stop"),
    ]
    first = debug["provider_attempts"][0]
    assert (first["max_tokens"], first["usage"]["completion_tokens"], first["usage"]["reasoning_tokens"]) == (8192, 8192, 0)
    assert all(batch["status"] == "complete" for batch in debug["batches"])

    agent_url = f"/api/agents/{agent['id']}"
    assert api.patch(agent_url, json={"execution": {"batch_max_tokens": 6144}}).status_code == 200
    changed = api.patch(agent_url, json={"execution": {"request_budget": 3}}).json()
    assert changed["execution"]["batch_max_tokens"] == 6144
    rerun = api.post(f"{session}/rerun/{response.json()['assistant_message']['id']}").json()
    assert rerun["assistant_message"]["debug_info"]["batch_max_tokens"] == requests[-1]["max_tokens"] == 6144
    assert api.patch(agent_url, json={"max_tokens": 4096, "execution": {"batch_max_tokens": None}}).status_code == 200
    rerun = api.post(f"{session}/rerun/{response.json()['assistant_message']['id']}").json()
    assert rerun["assistant_message"]["debug_info"]["batch_max_tokens"] == requests[-1]["max_tokens"] == 4096


def test_resume_only_pending_source_ranges_and_keep_history(api, monkeypatch):
    sent = []

    def handler(request):
        messages = json.loads(request.content)["messages"]
        content = messages[-1]["content"]
        sent.append(content)
        cut = len(sent) == 1
        operation = {"id": "proposed", "number": "01", "name": "清洗", "evidence": [{"quote": "工序01清洗"}]}
        if "工序02检测" in content:
            operation = {"id": "proposed", "number": "02", "name": "检测", "evidence": [{"quote": "工序02检测"}]}
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "model": "deepseek-flash", "created": 1,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None if cut else json.dumps(
                {"operations": [operation]}, ensure_ascii=False)}, "finish_reason": "length" if cut else "stop"}],
        })

    monkeypatch.setattr(llm_extractor, "AsyncOpenAI", lambda **kwargs: AsyncOpenAI(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ))
    agent = api.post("/api/agents", json={
        "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key": "fixture-key",
        "model": "deepseek-flash", "execution": {"request_budget": 2},
    }).json()
    session_id = api.post("/api/sessions", json={"agent_id": agent["id"]}).json()["id"]
    session = f"/api/sessions/{session_id}"
    assert api.post(f"{session}/run", json={"message": "整线生产节拍≤60秒/件。工序01清洗。工序02检测。"}).status_code == 200
    detail = api.get(session).json()
    original_user, partial = detail["messages"]
    original = detail["current_extracted_json"]["semantic"]
    assert partial["status"] == "partial" and len(sent) == 2
    assert [(batch["status"], batch["reason"]) for batch in original["metadata"]["batches"]] == [
        ("complete", None), ("unprocessed", "request_budget_exhausted"),
    ]

    url = f"{session}/resume/{partial['id']}"
    response = api.post(url)
    assert response.status_code == 200, response.text
    resumed = response.json()
    child = resumed["current_extracted_json"]["semantic"]
    assert len(sent) == 3 and "工序02检测" in sent[-1] and "工序01清洗" not in sent[-1]
    assert resumed["user_message"]["id"] == original_user["id"]
    assert [msg["role"] for msg in api.get(session).json()["messages"]] == ["user", "assistant", "assistant"]
    assert child["parent_version_id"] == original["run_id"]
    assert child["metadata"]["extraction_status"] == "complete"
    assert child["input_digest"] == original["input_digest"]
    assert child["constraints"] == original["constraints"]
    assert child["operations"][0]["evidence"] == original["operations"][0]["evidence"]
    assert all(item["evidence"][0]["document_id"] == original_user["id"] for item in child["operations"])
    assert len(child["metadata"]["batches"]) == 2
    assert api.post(f"{session}/resume/{child['run_id']}").status_code == 409

    historical = api.post(url).json()
    assert historical["current_extracted_json"]["semantic"]["run_id"] == child["run_id"]
    assert historical["assistant_message"]["extracted_json"]["semantic"]["parent_version_id"] == original["run_id"]
    assert historical["assistant_message"]["debug_info"]["activation_note"]


def test_cancel_before_run_does_not_leave_messages(api):
    from uuid import uuid4

    session = api.post("/api/sessions", json={}).json()
    url = f"/api/sessions/{session['id']}"
    token = str(uuid4())
    assert api.post(f"{url}/runs/{token}/cancel").status_code == 202
    result = api.post(f"{url}/run", json={"run_token": token, "message": "执行前取消"})
    assert result.status_code == 499
    assert api.get(url).json()["messages"] == []


@pytest.mark.parametrize(("reported", "expected"), [(0, "zero_reported"), (5, "reasoning_returned"),
                                                      (None, "unverified"), ("reject", "rejected")])
def test_gateway_json_probe_reports_effect_of_selected_thinking_parameter(api, monkeypatch, reported, expected):
    calls = []

    def handler(request):
        call = json.loads(request.content)
        calls.append(call)
        if reported == "reject":
            return httpx.Response(422, json={"error": {"message": "reasoning_effort is not supported"}})
        usage = {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22}
        if reported is not None:
            usage["completion_tokens_details"] = {"reasoning_tokens": reported}
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "model": "gateway-model", "created": 1,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"value":120,"unit":"s"}'},
                         "finish_reason": "stop"}], "usage": usage,
        })

    monkeypatch.setattr(llm_extractor, "AsyncOpenAI", lambda **kwargs: AsyncOpenAI(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ))
    result = api.post("/api/agents/probe-json", json={
        "base_url": "https://gateway.example/v1", "model": "gateway-model", "api_key": "fixture-key",
        "execution": {"semantic_thinking": "disabled", "thinking_parameter": "reasoning_effort"},
    }).json()
    call, = calls
    assert call["reasoning_effort"] == "none" and "thinking" not in call
    assert call["max_tokens"] == result["max_tokens"] == 512
    assert call["response_format"] == {"type": "json_object"}
    assert result["ok"] == (reported != "reject")
    assert result["thinking_result"] == expected
    if reported == "reject":
        assert result["error_code"] == "thinking_parameter_rejected"
    else:
        assert result["finish_reason"] == "stop" and result["usage"].get("reasoning_tokens") == reported


def test_gateway_run_and_resume_distinguish_reasoning_exhaustion_and_unstarted_batches(api, monkeypatch):
    calls = []

    def handler(request):
        call = json.loads(request.content)
        calls.append(call)
        no_thinking = call.get("thinking") == {"type": "disabled"}
        count = 2 if no_thinking else call["max_tokens"]
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "model": "gateway-model", "created": 1,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}" if no_thinking else None},
                         "finish_reason": "stop" if no_thinking else "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": count, "total_tokens": 10 + count,
                      "completion_tokens_details": {"reasoning_tokens": 0 if no_thinking else count}},
        })

    monkeypatch.setattr(llm_extractor, "AsyncOpenAI", lambda **kwargs: AsyncOpenAI(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ))
    agent = api.post("/api/agents", json={
        "name": "网关测试", "base_url": "https://gateway.example/v1", "api_key": "fixture-key",
        "model": "gateway-model", "max_tokens": 26383,
    }).json()
    session_id = api.post("/api/sessions", json={"agent_id": agent["id"]}).json()["id"]
    session = f"/api/sessions/{session_id}"
    partial = api.post(f"{session}/run", json={"message": "整线生产节拍≤60秒/件。工序01清洗。工序02检测。"}).json()
    assistant = partial["assistant_message"]
    assert assistant["status"] == "partial"
    assert [batch["reason"] for batch in assistant["debug_info"]["batches"]] == [
        "reasoning_budget_exhausted", "request_budget_exhausted",
    ]
    assert all("thinking" not in call and call["max_tokens"] == 4096 for call in calls)
    updated = api.patch(f"/api/agents/{agent['id']}", json={"execution": {
        "batch_max_tokens": 8192, "request_budget": 4, "semantic_thinking": "disabled",
    }})
    assert updated.status_code == 200
    resumed = api.post(f"{session}/resume/{assistant['id']}").json()["assistant_message"]
    assert resumed["status"] == "success"
    assert len(calls) == 4 and [call["max_tokens"] for call in calls[-2:]] == [8192, 8192]
    assert all(attempt["thinking_result"] == "zero_reported" for attempt in resumed["debug_info"]["provider_attempts"])


def test_human_review_creates_traceable_version_and_preserves_original(api):
    session = api.post("/api/sessions", json={}).json()
    sess = f"/api/sessions/{session['id']}"
    versions = f"/api/semantics/sessions/{session['id']}/versions"
    text = "工序编号,工序名称,工序类型\n01,清洗,主线\n02,检测,主线\n".encode()
    f = api.post(f"{sess}/files", files={"files": ("route.csv", text)}).json()[0]
    result = api.post(f"{sess}/run", json={"file_ids": [f["id"]], "use_demo_fallback": True}).json()
    base = result["current_extracted_json"]["semantic"]
    assert base["metadata"]["session_id"] == session["id"]
    response = api.post(f"{versions}/{base['run_id']}/review", json={
        "action": "add_transition", "source_id": "operation:01", "target_id": "operation:02",
        "type": "conditionallyPrecedes", "condition": "工件干燥", "note": "工程师确认流转条件",
    })
    assert response.status_code == 201, response.text
    child = response.json()
    assert child["parent_version_id"] == base["run_id"]
    assert child["process_graph"]["edges"][0]["kind"] == "conditional"
    assert child["process_graph"]["edges"][0]["evidence"][0]["method"] == "manual"
    persisted = api.get(f"{versions}/{child['run_id']}/graph").json()
    assert persisted == child["process_graph"]
    assert not api.get(f"{versions}/{base['run_id']}").json()["process_graph"]["edges"]
    assert api.get(sess).json()["current_extracted_json"]["semantic"]["run_id"] == child["run_id"]
    assert api.get("/api/semantics/catalog?version=1.0.0").json()["ontology_version"] == "1.0.0"
    assert {"parse_document", "deterministic_extraction", "build_process_graph", "validate_constraints"} <= {
        item["id"] for item in api.get("/api/semantics/tools").json()
    }


def test_partial_result_is_saved_with_unprocessed_scope(api):
    session = api.post("/api/sessions", json={}).json()
    url = f"/api/sessions/{session['id']}"
    response = api.post(f"{url}/run", json={
        "message": "整线生产节拍≤60秒/件。另需分析说明文档中的夹具兼容问题。", "use_demo_fallback": True,
    })
    assert response.status_code == 200
    result = response.json()
    assert result["assistant_message"]["status"] == "partial"
    semantic = result["current_extracted_json"]["semantic"]
    assert semantic["metadata"]["extraction_status"] == "partial"
    reports = semantic["metadata"]["batches"]
    assert len(reports) == 1 and reports[0]["reason"] == "offline"
    assert semantic["constraints"][0]["value"] == 60
    steps = {step["stage"] for step in result["assistant_message"]["debug_info"]["tool_trace"]}
    assert {"query_ontology", "build_process_graph", "validate_constraints", "export_requirement_model"} <= steps
    assert api.get(url).json()["current_extracted_json"]["semantic"]["run_id"] == semantic["run_id"]


def test_upload_reuses_identical_name_and_content_but_keeps_distinct_revisions(api):
    session = api.post("/api/sessions", json={}).json()
    session_url = f"/api/sessions/{session['id']}"
    original = ("files", ("parts.csv", b"operation id,operation name\n01,Inspect"))
    changed = ("files", ("parts.csv", b"operation id,operation name\n01,Assemble"))
    first, = api.post(f"{session_url}/files", files=[original, original]).json()
    repeated, revision = api.post(f"{session_url}/files", files=[original, changed]).json()
    assert repeated["id"] == first["id"]
    assert revision["id"] != first["id"]
    assert len(api.get(session_url).json()["files"]) == 2


def test_resource_matching_schema_preview_and_export_on_original_inputs(api):
    from domain_semantics.handoff import ResourceMatchingInput

    root = Path(__file__).resolve().parents[2] / "test case"
    workbook = root / "工艺需求文件.xlsx"
    narrative = (root / "自然语言建模需求.md").read_text(encoding="utf-8")
    base = "/api/semantics"
    assert api.get(f"{base}/schema").json()["title"] == "RequirementSemanticModel"
    assert api.get(f"{base}/candidates/schema").json()["title"] == "ExtractionCandidates"
    assert api.get(f"{base}/handoff/schema").json() == ResourceMatchingInput.model_json_schema()
    session_id = api.post("/api/sessions", json={}).json()["id"]
    session = f"/api/sessions/{session_id}"
    file, = api.post(f"{session}/files", files={"files": (workbook.name, workbook.read_bytes())}).json()
    assert file["parse_status"] == "success"
    run = api.post(f"{session}/run", json={"file_ids": [file["id"]], "message": narrative, "use_demo_fallback": True})
    assert run.status_code == 200
    data = run.json()
    model = data["current_extracted_json"]["semantic"]
    assert data["assistant_message"]["debug_info"]["request_count"] == 0
    assert model["metadata"]["extraction_status"] == "partial"
    version = f"{base}/sessions/{session_id}/versions/{model['run_id']}"
    preview = api.get(f"{version}/handoff")
    assert preview.status_code == 200
    handoff = ResourceMatchingInput.model_validate(preview.json())
    assert handoff.source_model.content_digest == model["content_digest"]
    assert handoff.handoff_validation.status == "ready_with_warnings"
    assert len(handoff.operation_requirements) == 16
    assert {item.code for item in handoff.handoff_validation.issues} >= {
        "handoff_extraction_coverage", "conditional_route_missing",
    }
    assert api.get(f"{version}/graph").json() == preview.json()["process_graph"]
    download = api.get(f"{version}/handoff/export")
    assert download.headers["Content-Disposition"] == f'attachment; filename="resource-matching-{model["run_id"]}.json"'
    assert ResourceMatchingInput.model_validate_json(download.content) == handoff
    assert api.get(version).json() == api.get(f"{version}/export").json() == model
    assert api.get(f"{base}/sessions/{session_id}/versions/missing/handoff").status_code == 404
    other_id = api.post("/api/sessions", json={}).json()["id"]
    assert api.get(f"{base}/sessions/{other_id}/versions/{model['run_id']}/handoff").status_code == 404
    assert api.get(f"{base}/sessions/{other_id}/versions/{model['run_id']}/handoff/export").status_code == 404


def test_blocked_handoff_is_exportable_and_review_is_version_specific(api):
    session_id = api.post("/api/sessions", json={}).json()["id"]
    session = f"/api/sessions/{session_id}"
    versions = f"/api/semantics/sessions/{session_id}/versions"
    raw = b"operation id,operation name,capability\nA1,Clean,cleaning"
    file, = api.post(f"{session}/files", files={"files": ("single.csv", raw)}).json()
    run = api.post(f"{session}/run", json={"file_ids": [file["id"]], "use_demo_fallback": True})
    original = run.json()["current_extracted_json"]["semantic"]
    url = f"{versions}/{original['run_id']}"
    first = api.get(f"{url}/handoff").json()
    assert first["handoff_validation"]["status"] == "blocked"
    assert api.get(f"{url}/handoff/export").json() == first
    review = api.post(f"{url}/review", json={
        "action": "set_modeling_task", "target_scope": "workstation", "note": "已确认构建清洗工位的数字孪生模型。",
    })
    assert review.status_code == 201, review.text
    child = review.json()
    assert child["parent_version_id"] == original["run_id"]
    child_url = f"{versions}/{child['run_id']}"
    assert api.get(f"{child_url}/handoff").json()["handoff_validation"]["status"] == "ready"
    assert api.get(f"{url}/handoff").json() == first
    assert api.post(f"{child_url}/validate").json() == child["validation"]
