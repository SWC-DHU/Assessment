"""Comprehensive tests for multi-agent role isolation, SQLite migration, LLM-enhanced matching & fallback policies."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from agents.llm_client import ExtractorConfig, LLMCallResult
from app.database import Base, ensure_schema_migrations, get_db
from app.main import app
from app.models.db_models import AgentProfile
from app.services.parser import parse_file_bytes
from app.services.seed import seed_initial_data
from domain_semantics.handoff import build_resource_matching_input
from domain_semantics.pipeline import project
from domain_semantics.sources import file_document, message_document
from model_assets.catalog import AssetCatalog, get_default_catalog
from resource_matching import (
    MatchingExecutionError,
    ResourceMatchingConfig,
    run_resource_matching,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/multi_agent.db", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    ensure_schema_migrations(engine)
    with TestingSession() as db:
        seed_initial_data(db)

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

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
            yield tc, TestingSession
    finally:
        app.dependency_overrides.clear()


def _sample_rmi(extra_row: str | None = None):
    body = (
        "工序编号,工序名称,制造能力,输入物料,输出物料,工序类型,前置工序,关键工艺约束\n"
        + (
            extra_row
            or "01,电芯上料,电芯供料、抓取与定位,来料电芯,已定位电芯,主线工序,无,适配指定电芯规格，抓取与定位过程不得损伤电芯\n"
        )
    )
    parsed = parse_file_bytes(body.encode("utf-8"), "case.csv")
    doc = file_document("doc1", "case.csv", parsed.parsed_text, parsed.parsed_preview)
    msg = message_document("msg1", "构建电池模组装配产线数字孪生模型。要求整线节拍不超过120 s/模组。")
    model = project([doc, msg], "multi-agent-test-v1")
    return build_resource_matching_input(model)


def test_sqlite_legacy_migration_preserves_history_and_seeds_matching_role(tmp_path):
    legacy_db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{legacy_db}", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        # Simulate pre-refactor agent_profiles WITHOUT agent_type column
        conn.execute(
            text(
                """
                CREATE TABLE agent_profiles (
                    id VARCHAR(36) PRIMARY KEY,
                    name VARCHAR(120) NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    base_url VARCHAR(500) NOT NULL DEFAULT '',
                    api_key VARCHAR(500) NOT NULL DEFAULT '',
                    model VARCHAR(150) NOT NULL DEFAULT '',
                    temperature FLOAT NOT NULL DEFAULT 0.1,
                    max_tokens INTEGER NOT NULL DEFAULT 4096,
                    json_mode BOOLEAN NOT NULL DEFAULT 1,
                    require_source_quote BOOLEAN NOT NULL DEFAULT 1,
                    system_prompt TEXT NOT NULL DEFAULT '',
                    business_rules TEXT NOT NULL DEFAULT '',
                    extraction_schema JSON NOT NULL DEFAULT '{}',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO agent_profiles (id, name, base_url, model) "
                "VALUES ('legacy-1', '历史需求解析专家', 'https://api.example.com/v1', 'gpt-4o-mini')"
            )
        )

    Base.metadata.create_all(bind=engine)
    ensure_schema_migrations(engine)

    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as db:
        seed_initial_data(db)
        all_agents = db.query(AgentProfile).all()
        by_id = {a.id: a for a in all_agents}
        assert "legacy-1" in by_id
        assert by_id["legacy-1"].agent_type == "requirement_parsing"
        matching_agents = [a for a in all_agents if a.agent_type == "resource_matching"]
        assert len(matching_agents) == 1
        assert "资源匹配" in matching_agents[0].name


def test_independent_agent_configs_and_no_secret_leakage(isolated_client):
    client, _ = isolated_client

    roles = client.get("/api/agents/roles").json()
    role_types = {r["agent_type"] for r in roles}
    assert role_types == {"requirement_parsing", "resource_matching"}
    assert all(len(r["tools"]) >= 6 for r in roles)

    req_secret = "sk-req-only-secret-111111"
    match_secret = "sk-match-only-secret-999999"

    req_created = client.post(
        "/api/agents",
        json={
            "agent_type": "requirement_parsing",
            "name": "需求解析实验A",
            "base_url": "https://req.example.com/v1",
            "api_key": req_secret,
            "model": "req-model-v1",
        },
    )
    assert req_created.status_code == 201
    match_created = client.post(
        "/api/agents",
        json={
            "agent_type": "resource_matching",
            "name": "资源匹配实验B",
            "base_url": "https://match.example.com/v1",
            "api_key": match_secret,
            "model": "match-model-v2",
            "execution": {"fallback_policy": "degrade_deterministic", "request_budget": 4, "max_strategy_retries": 2},
        },
    )
    assert match_created.status_code == 201

    req_list = client.get("/api/agents?agent_type=requirement_parsing").json()
    match_list = client.get("/api/agents?agent_type=resource_matching").json()
    assert all(a["agent_type"] == "requirement_parsing" for a in req_list)
    assert all(a["agent_type"] == "resource_matching" for a in match_list)
    # Secrets must never appear in plaintext in API responses
    raw_combined = str(req_list) + str(match_list)
    assert req_secret not in raw_combined
    assert match_secret not in raw_combined

    # Verify direct matching run records secret-free snapshot with matching agent's own model
    rmi = _sample_rmi()
    run_resp = client.post(
        "/api/resource-matching/run-direct",
        json={
            "handoff_input": rmi.model_dump(mode="json"),
            "config": {"top_k": 3, "method": "C", "llm_mode": "disabled"},
            "agent_id": match_created.json()["id"],
            "save": True,
        },
    )
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["run_metadata"]["agent_type"] == "resource_matching"
    assert body["run_metadata"]["agent_id"] == match_created.json()["id"]
    assert body["run_metadata"]["config_snapshot"]["effective_model"] == "match-model-v2"
    assert match_secret not in run_resp.text


def test_backend_strictly_rejects_mismatched_agent_roles(isolated_client):
    client, SessionMaker = isolated_client

    req_agent = client.get("/api/agents?agent_type=requirement_parsing").json()[0]
    match_agent = client.get("/api/agents?agent_type=resource_matching").json()[0]

    # 1. Cannot bind resource_matching agent to a requirement session
    bad_sess = client.post("/api/sessions", json={"title": "错配角色测试", "agent_id": match_agent["id"]})
    assert bad_sess.status_code == 400
    assert "角色不匹配" in bad_sess.json()["detail"]

    good_sess = client.post("/api/sessions", json={"title": "正常会话", "agent_id": req_agent["id"]}).json()
    bad_patch = client.patch(f"/api/sessions/{good_sess['id']}", json={"agent_id": match_agent["id"]})
    assert bad_patch.status_code == 400

    bad_run = client.post(
        f"/api/sessions/{good_sess['id']}/run",
        json={"message": "构建装配产线数字孪生模型", "file_ids": [], "agent_id": match_agent["id"]},
    )
    assert bad_run.status_code == 400
    assert "角色不匹配" in bad_run.json()["detail"]

    # 2. Cannot pass requirement_parsing agent to resource matching endpoint
    rmi = _sample_rmi()
    bad_match = client.post(
        "/api/resource-matching/run-direct",
        json={
            "handoff_input": rmi.model_dump(mode="json"),
            "config": {"top_k": 3, "method": "C"},
            "agent_id": req_agent["id"],
        },
    )
    assert bad_match.status_code == 400
    assert "角色不匹配" in bad_match.json()["detail"]

    # 3. Deleting the last agent of resource_matching is rejected
    del_last = client.delete(f"/api/agents/{match_agent['id']}")
    assert del_last.status_code == 400

    # 4. If no resource_matching agent exists in DB, backend refuses rather than falling back to requirement_parsing
    with SessionMaker() as db:
        db.query(AgentProfile).filter(AgentProfile.agent_type == "resource_matching").delete()
        db.commit()
    no_role_match = client.post(
        "/api/resource-matching/run-direct",
        json={"handoff_input": rmi.model_dump(mode="json"), "config": {"top_k": 3, "method": "C"}},
    )
    assert no_role_match.status_code == 400
    assert "resource_matching" in no_role_match.json()["detail"]


def test_llm_enhanced_matching_strategy_verification_and_hallucination_blocking():
    rmi = _sample_rmi()
    before_rmi_json = rmi.model_dump_json()

    fake_llm_response = LLMCallResult(
        ok=True,
        data={
            "strategies": [
                {
                    "operation_id": rmi.operation_requirements[0].operation_id,
                    "additional_asset_ids": ["MA-HALLUCINATED-99", "MA-FEED-04", "MA-FEED-01"],
                    "preferred_categories": ["feeding"],
                    "capability_decomposition_hint": [["FEED"], ["GRIP", "POSITION"]],
                    "qualitative_judgments": [
                        {
                            "asset_id": "MA-FEED-01",
                            "scope": "operation",
                            "satisfied": True,
                            "rationale": "柔性伺服力控夹爪满足无损抓取定位要求",
                        }
                    ],
                    "explanation": "优先推荐 MA-FEED-01，并保留 MA-FEED-03 + MA-ROB-01 协同方案。",
                    "upstream_feedback": "建议上游补充单电芯目标工时边界以便进一步筛选。",
                }
            ]
        },
        request_count=1,
        model_used="mock-llm-enhanced",
    )

    cfg = ResourceMatchingConfig(top_k=3, method="C", llm_mode="enhanced", max_llm_calls=3)
    llm_cfg = ExtractorConfig(
        base_url="https://api.example.com/v1",
        api_key="sk-fake-key-for-mock",
        model="mock-llm-enhanced",
    )

    with patch("resource_matching.agent.UnifiedLLMClient.complete_json", new=AsyncMock(return_value=fake_llm_response)):
        out = asyncio.run(run_resource_matching(rmi, config=cfg, llm_config=llm_cfg))

    # Upstream Requirement input must never be mutated
    assert rmi.model_dump_json() == before_rmi_json

    op = out.operation_matches[0]
    assert op.llm_strategy is not None
    assert op.llm_strategy.triggered is True
    assert "MA-HALLUCINATED-99" in op.llm_strategy.rejected_hallucinated_ids
    assert "MA-FEED-04" in op.llm_strategy.rejected_hard_constraint_ids
    assert op.llm_strategy.preferred_categories == ["feeding"]
    assert op.llm_strategy.capability_decomposition_hint == [["FEED"], ["GRIP", "POSITION"]]
    assert op.llm_explanation and "MA-FEED-01" in op.llm_explanation

    # Neither hallucinated nor hard-failing distractor enters Top-k
    top_ids = [c.asset_id for c in op.top_k_candidates]
    assert "MA-FEED-01" in top_ids
    assert "MA-HALLUCINATED-99" not in top_ids
    assert "MA-FEED-04" not in top_ids
    assert all(c.hard_constraint_verdict == "PASS" for c in op.top_k_candidates)

    # Structured non-mutating upstream feedback emitted
    assert any(
        fb.issue_source == "constraint_ambiguity" and fb.mutates_upstream is False and "工时" in fb.message
        for fb in out.feedback_items
    )
    assert out.run_metadata is not None
    assert out.run_metadata.execution_mode == "llm_enhanced"
    assert out.summary.fallback_triggered is False


def test_llm_unconfigured_and_failure_fallback_policies():
    rmi = _sample_rmi()
    unconfigured_llm = ExtractorConfig(base_url="https://api.example.com/v1", api_key="", model="gpt-4o-mini")

    # 1. degrade_deterministic returns full deterministic results + fallback warning
    cfg_degrade = ResourceMatchingConfig(top_k=3, method="C", llm_mode="enhanced", fallback_policy="degrade_deterministic")
    out_degrade = asyncio.run(run_resource_matching(rmi, config=cfg_degrade, llm_config=unconfigured_llm))
    assert out_degrade.summary.fallback_triggered is True
    assert out_degrade.summary.fallback_reason is not None
    assert out_degrade.operation_matches[0].top_k_candidates[0].asset_id == "MA-FEED-01"
    assert any(fb.issue_source == "llm_runtime" for fb in out_degrade.feedback_items)

    # 2. fail_fast raises clear MatchingExecutionError when unconfigured
    cfg_fail = ResourceMatchingConfig(top_k=3, method="C", llm_mode="enhanced", fallback_policy="fail_fast")
    with pytest.raises(MatchingExecutionError):
        asyncio.run(run_resource_matching(rmi, config=cfg_fail, llm_config=unconfigured_llm))

    # 3. Runtime LLM failure degrades cleanly under degrade_deterministic
    configured_llm = ExtractorConfig(base_url="https://api.example.com/v1", api_key="sk-live", model="gpt-4o-mini")
    failed_call = LLMCallResult(ok=False, error_code="service_unavailable", error_message="模拟网关 503 超时")
    with patch("resource_matching.agent.UnifiedLLMClient.complete_json", new=AsyncMock(return_value=failed_call)):
        out_err = asyncio.run(run_resource_matching(rmi, config=cfg_degrade, llm_config=configured_llm))
    assert out_err.summary.fallback_triggered is True
    assert "503" in (out_err.summary.fallback_reason or "")
    assert out_err.operation_matches[0].top_k_candidates[0].asset_id == "MA-FEED-01"


def test_experiment_runner_supports_a_c_and_c_llm_offline():
    from experiments.run_matching import run_experiment

    args = SimpleNamespace(
        input=ROOT / "test case/工艺需求文件.xlsx",
        text_file=ROOT / "test case/自然语言建模需求.md",
        gold=ROOT / "backend/experiments/battery_matching_gold.json",
        methods="a,c,c_llm",
        top_k=3,
        repeat=1,
        offline=True,
        use_llm=False,
        saved_agent=None,
        temperature=0.0,
        max_tokens=2048,
        timeout=30,
    )
    report = asyncio.run(run_experiment(args))
    by_method = {row["method"]: row for row in report["runs"]}
    assert set(by_method) == {"A", "C", "C_LLM"}
    assert by_method["C"]["metrics"]["precision_at_3"] == 1.0
    assert by_method["C"]["metrics"]["hard_constraint_violation_rate"] == 0.0
    assert by_method["C_LLM"]["fallback_triggered"] is True
    assert by_method["C_LLM"]["metrics"]["hard_constraint_violation_rate"] == 0.0
    assert by_method["C"]["run_metadata"]["agent_type"] == "resource_matching"
