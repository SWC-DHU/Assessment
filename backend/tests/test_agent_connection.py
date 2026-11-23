from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import httpx
from openai import APIStatusError, AuthenticationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import db_models
from app.services import llm_extractor
from app.services.seed import PROCESS_EXTRACTION_SCHEMA


def test_normalize_base_url():
    assert llm_extractor.normalize_base_url("") == ""
    assert llm_extractor.normalize_base_url("  mock://demo ") == "mock://demo"
    assert llm_extractor.normalize_base_url("https://api.3stooges.chat") == "https://api.3stooges.chat/v1"
    assert llm_extractor.normalize_base_url("https://api.3stooges.chat/v1/") == "https://api.3stooges.chat/v1"
    assert (
        llm_extractor.normalize_base_url("https://api.openai.com/v1/chat/completions")
        == "https://api.openai.com/v1"
    )
    assert (
        llm_extractor.normalize_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1/models")
        == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )


def test_validate_json_schema_mode_nested():
    schema = {
        "mode": "json_schema",
        "json_schema": PROCESS_EXTRACTION_SCHEMA["json_schema"],
    }
    data = {
        "product_name": "电机壳体",
        "operations": [{"seq": "OP10"}],  # missing name & source_quote
    }
    errors = llm_extractor.validate_extracted_data(data, schema, require_source_quote=True)
    assert any("operations[1].name" in e for e in errors)
    assert any("operations[1]" in e and "source_quote" in e for e in errors)


@pytest.mark.asyncio
async def test_probe_llm_connection_mock_and_missing_key():
    mock_res = await llm_extractor.test_llm_connection("mock://demo", "", "mock-demo")
    assert mock_res.ok is True
    assert mock_res.available_models == ["mock-demo"]

    no_key_res = await llm_extractor.test_llm_connection("https://api.openai.com/v1", "", "gpt-4o-mini")
    assert no_key_res.ok is False
    assert "API Key 未配置" in no_key_res.message


@pytest.mark.asyncio
async def test_probe_llm_connection_json_mode_fallback_and_403_hint():
    fake_models = SimpleNamespace(data=[SimpleNamespace(id="qwen3.5-27b"), SimpleNamespace(id="gpt-4o-mini")])
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    resp_400 = httpx.Response(400, request=req)
    err_json_mode = APIStatusError(
        "json_object is not supported by this model", response=resp_400, body={"error": "json_object"}
    )

    mock_client = MagicMock()
    mock_client.models.list = AsyncMock(return_value=fake_models)
    mock_client.chat.completions.create = AsyncMock(side_effect=[err_json_mode, SimpleNamespace(choices=[])])
    mock_client.close = AsyncMock()

    with patch("app.services.llm_extractor.AsyncOpenAI", return_value=mock_client):
        res = await llm_extractor.test_llm_connection(
            "https://api.example.com", "sk-test-1234", "qwen3.5-27b", json_mode=True
        )

    assert res.ok is True
    assert res.json_mode_supported is False
    assert "qwen3.5-27b" in res.available_models
    assert "自动降级" in res.message

    # Test 403 model permission error includes available model hint
    resp_403 = httpx.Response(403, request=req)
    err_403 = APIStatusError(
        "This token has no access to model qwen3.8 27B", response=resp_403, body={}
    )
    mock_client_403 = MagicMock()
    mock_client_403.models.list = AsyncMock(return_value=fake_models)
    mock_client_403.chat.completions.create = AsyncMock(side_effect=err_403)
    mock_client_403.close = AsyncMock()

    with patch("app.services.llm_extractor.AsyncOpenAI", return_value=mock_client_403):
        res_403 = await llm_extractor.test_llm_connection("https://api.example.com", "sk-test-1234", "qwen3.8 27B")

    assert res_403.ok is False
    assert res_403.status_code == 403
    assert "qwen3.5-27b" in res_403.message

    # Test 401 auth error
    resp_401 = httpx.Response(401, request=req)
    err_401 = AuthenticationError("Invalid API key", response=resp_401, body={})
    mock_client_401 = MagicMock()
    mock_client_401.models.list = AsyncMock(return_value=fake_models)
    mock_client_401.chat.completions.create = AsyncMock(side_effect=err_401)
    mock_client_401.close = AsyncMock()

    with patch("app.services.llm_extractor.AsyncOpenAI", return_value=mock_client_401):
        res_401 = await llm_extractor.test_llm_connection("https://api.example.com", "sk-bad", "gpt-4o-mini")

    assert res_401.ok is False
    assert res_401.status_code == 401
    assert "鉴权失败" in res_401.message


def _make_test_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSessionLocal


def test_agent_test_connection_endpoint_and_error_history_filter():
    client, SessionMaker = _make_test_client()
    try:
        db = SessionMaker()
        agent = db_models.AgentProfile(
            name="测试智能体",
            base_url="https://api.example.com",
            api_key="sk-saved-secret-key-9999",
            model="qwen3.5-27b",
        )
        db.add(agent)
        db.flush()
        sess = db_models.TestSession(title="测试", agent_id=agent.id)
        db.add(sess)
        db.flush()
        db.add(db_models.ChatMessage(session_id=sess.id, role="user", content="解析需求", status="success"))
        db.add(
            db_models.ChatMessage(
                session_id=sess.id,
                role="assistant",
                content="HTTP 403 error that should not leak into history",
                status="error",
            )
        )
        db.commit()
        agent_id = agent.id
        sess_id = sess.id
        db.close()

        # Verify saved DB key is used when api_key is omitted in draft test request
        captured = {}

        async def fake_test_conn(**kwargs):
            captured.update(kwargs)
            from app.services.llm_extractor import ConnectionTestResult

            return ConnectionTestResult(
                ok=True,
                message="ok",
                latency_ms=12,
                status_code=200,
                effective_base_url=kwargs["base_url"],
                effective_model=kwargs["model"],
                json_mode_supported=True,
                available_models=["qwen3.5-27b"],
            )

        with patch("app.api.agents.test_llm_connection", side_effect=fake_test_conn):
            resp = client.post(
                "/api/agents/test-connection",
                json={
                    "agent_id": agent_id,
                    "base_url": "https://api.example.com/",
                    "model": " qwen3.5-27b ",
                    "clear_api_key": False,
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert captured["api_key"] == "sk-saved-secret-key-9999"
        assert captured["base_url"] == "https://api.example.com/v1"
        assert captured["model"] == "qwen3.5-27b"

        # Verify failed assistant messages are excluded from conversation_history on next run
        captured_history = []

        async def fake_run_extraction(**kwargs):
            captured_history.extend(kwargs.get("conversation_history") or [])
            from app.services.llm_extractor import ExtractionResult

            return ExtractionResult(status="success", extracted_json={"product_name": "P1"}, summary_text="ok")

        with patch("app.api.sessions.run_extraction", side_effect=fake_run_extraction):
            run_resp = client.post(
                f"/api/sessions/{sess_id}/run",
                json={"message": "继续解析", "file_ids": [], "agent_id": agent_id, "strategy": "legacy"},
            )
        assert run_resp.status_code == 200
        assert captured_history == [{"role": "user", "content": "解析需求"}]
    finally:
        app.dependency_overrides.clear()
