import json
from pathlib import Path

import pytest

from app.services.extraction_batches import MAX_BATCH_CHARS, ResumeSourceError, plan_batches, recovered_rows, resume_batches
from app.services.llm_extractor import ExtractionResult, ExtractorConfig
from app.services.parser import parse_file_bytes, parse_file_path
from app.services.semantic_execution import execute, unprocessed
from domain_semantics.catalog import check_ontology, normalize_term, query_ontology
from domain_semantics.pipeline import project
from domain_semantics.schema import RequirementSemanticModel
from domain_semantics.sources import file_document, message_document
from experiments.run import score

ROOT = Path(__file__).resolve().parents[2]


def test_owl_is_queried_by_version_and_matches_schema():
    check_ontology()
    result = query_ontology("hasInputState")
    assert result["matches"][0]["domain"] == ["Operation"]
    assert result["matches"][0]["range"] == ["MaterialState"]
    assert not query_ontology("hasInputState", "1.0.0")["matches"]
    assert query_ontology("生产工序")["matches"][0]["id"] == "Operation"
    assert normalize_term("operations", "堆叠作业")["term"] == "堆叠"
    assert normalize_term("capabilities", "不在词表")["term"] is None


def test_ontology_keeps_order_dependency_and_direct_flow_separate():
    source = message_document("source", "清洗先于检测。检测依赖清洗。")
    payload = {
        "operations": [
            {"id": "wash", "name": "清洗", "evidence": [{"quote": "清洗先于检测"}]},
            {"id": "inspect", "name": "检测", "evidence": [{"quote": "检测依赖清洗"}]},
        ],
        "relations": [
            {"type": "precedes", "source_id": "wash", "target_id": "inspect", "evidence": [{"quote": "清洗先于检测"}]},
            {"type": "dependsOn", "source_id": "inspect", "target_id": "wash", "evidence": [{"quote": "检测依赖清洗"}]},
            {"type": "hasPredecessor", "source_id": "inspect", "target_id": "wash", "evidence": [{"quote": "清洗先于检测"}]},
        ],
    }
    model = project([source], "ontology-separation", payload)
    assert sorted((edge.kind, edge.direct) for edge in model.process_graph.edges) == [
        ("dependency", False), ("precedence", False),
    ]
    assert all(not op.predecessor_ids for op in model.operations)


def test_explicit_condition_routes_and_material_states_from_columns():
    values = ("工序编号,工序名称,输入物料,输出物料,下一工序,转序条件,工序类型\n"
              "01,清洗,来料部件,清洗后部件,02,外观合格,主线工序\n"
              "02,检测,清洗后部件,合格部件,,,可选工序\n").encode()
    data = parse_file_bytes(values, "route.csv")
    doc = file_document("csv", "route.csv", data.parsed_text, data.parsed_preview)
    model = project([doc], "route")
    assert [item.name for item in model.materials] == ["部件"]
    assert len(model.material_states) == 3
    assert model.operations[0].output_state_ids == model.operations[1].input_state_ids
    assert model.process_graph.nodes[1].category == "optional"
    edge = model.process_graph.edges[0]
    assert (edge.kind, edge.condition, edge.direct, edge.status) == ("conditional", "外观合格", True, "verified")
    assert [item.location.cell for item in edge.evidence] == ["E2", "F2"]


def test_batch_limits_do_not_drop_long_source_and_complete_fragments_are_salvaged():
    long_text = "工艺参数" * 950
    source = message_document("test", long_text)
    batches = plan_batches([source], set())
    assert len(batches) > 1 and all(batch.size <= MAX_BATCH_CHARS for batch in batches)
    assert all(batch.fragments[0].key == "message" for batch in batches)
    assert recovered_rows('{"operations":[{"name":"清洗"},{"name":"') == {"operations": [{"name": "清洗"}]}
    assert not recovered_rows('{"operations":[{"name":"清')


def test_resume_recovers_exact_subrange_of_a_single_long_fragment():
    source = message_document("test", "检查装配参数" * 110 + "确认设备校准状态" * 40)
    original, = plan_batches([source], set())
    _, pending = original.split()
    report = unprocessed(pending, "request_budget_exhausted")
    resumed, = resume_batches([source], [report])
    assert resumed.fragments == pending.fragments
    assert resumed.size < original.size
    with pytest.raises(ResumeSourceError):
        resume_batches([message_document("test", "其他内容")], [report])


@pytest.mark.asyncio
async def test_truncation_splits_recovers_and_marks_unprocessed_work(monkeypatch):
    from app.services import semantic_execution

    calls = []
    operation = {"id": "x", "name": "清洗", "number": "01", "evidence": [{"quote": "工序01清洗"}]}

    async def provider(config, user_message, files, current_state=None):
        calls.append((config.max_tokens, files[0].parsed_text, current_state))
        if len(calls) == 1:
            raw = '{"operations":[' + json.dumps(operation, ensure_ascii=False) + ',{"name":"'
            return ExtractionResult(status="error", error_message="输出截断", debug_info={
                "request_count": 1, "error_code": "output_truncated", "finish_reason": "length", "raw_response": raw,
            })
        return ExtractionResult(status="success", extracted_json={"operations": [operation]},
                                debug_info={"request_count": 1, "finish_reason": "stop"})

    monkeypatch.setattr(semantic_execution, "run_extraction", provider)
    config = ExtractorConfig(base_url="http://fixture", api_key="placeholder", model="test", max_tokens=16383, request_budget=2)
    doc = message_document("user", "工序01清洗。工序02检测。")
    model, result = await execute(config, doc, [], "batch-run", None, session_id="session")
    assert result.status == model.metadata.extraction_status == "partial"
    assert len(calls) == 2 and all(call[0] == 4096 for call in calls)
    assert len(calls[1][1]) < len(calls[0][1])
    assert model.operations[0].evidence[0].method == "llm"
    assert model.operations[0].evidence[0].extraction_batch.startswith("batch-run/batch-")
    assert any(batch.status == "unprocessed" for batch in model.metadata.batches)
    assert any(step["stage"] == "batch_split" for step in result.debug_info["tool_trace"])
    assert result.debug_info["request_count"] == 2


@pytest.mark.asyncio
async def test_real_workbook_and_narrative_have_distinct_gold_and_provenance():
    workbook = ROOT / "test case/工艺需求文件.xlsx"
    narrative = ROOT / "test case/自然语言建模需求.md"
    if not workbook.is_file() or not narrative.is_file():
        pytest.skip("工作区缺少原始案例")
    data = parse_file_path(workbook, row_limit=None)
    source = file_document("xlsx", workbook.name, data.parsed_text, data.parsed_preview)
    message = message_document("text", narrative.read_text(encoding="utf-8"))
    gold = json.loads((ROOT / "backend/experiments/battery_module_gold.json").read_text(encoding="utf-8"))
    gold |= json.loads((ROOT / "backend/experiments/battery_mixed_expectations.json").read_text(encoding="utf-8"))
    config = ExtractorConfig(base_url="mock://demo", api_key="", model="fixture")
    model, result = await execute(config, message, [source], "gold-mixed", None, offline=True)
    metrics = score(model.model_dump(mode="json"), gold, [source, message], schema_valid=True)
    for metric in ("operation_entity", "material_entity", "process_relations", "component_quantities", "numeric_constraints"):
        assert metrics[metric]["f1"] == 1
    assert metrics["mapping_accuracy"] == metrics["evidence_accuracy"] == 1
    assert result.debug_info["request_count"] == 0
    assert len(model.modeling_intents) == 4
    feed, = [item for item in model.capabilities if item.code == "FEED"]
    assert len(feed.operation_ids) == 4
    assert set(feed.raw_names) == {"电芯供料", "导热壳上料", "中板结构件供料与输送", "端板结构件供料与输送"}
    assert not ({"识别信息", "标识信息", "合格模组", "不合格模组（如有）"} & {item.name for item in model.materials})
    assert {(op.number, flow.name, flow.evidence[0].location.cell) for op in model.operations
            for flow in op.information_flows} == {("04", "识别信息", "E5"), ("Z1", "标识信息", "E6")}
    assert not any(edge.type == "mergesInto" for edge in model.relations)
    assert {node.number for node in model.process_graph.nodes if node.category == "bypass"} == {"Z1", "Z2", "Z3"}
    assert {(edge.source.removeprefix("operation:"), edge.target.removeprefix("operation:"))
            for edge in model.process_graph.edges if edge.kind == "merge"} == {
        ("04", "05"), ("Z1", "05"), ("07", "08"), ("Z2", "08"), ("Z3", "08"),
    }
    assert not any(edge.kind == "rework" for edge in model.process_graph.edges)
    assert any(issue.code == "conditional_route_missing" and issue.path == "operation:12" for issue in model.validation.issues)
    assert all(item.target_id == "line" for item in model.constraints if item.value == 120)
    assert RequirementSemanticModel.model_validate_json(model.model_dump_json()) == model


@pytest.mark.asyncio
async def test_numbered_statements_are_processed_without_requiring_a_provider():
    doc = message_document("prose", "工序为抛光，编号P7，消耗外壳，产出精饰外壳。第二道工序是检验，编号X9，前置工序为抛光，需要表面检测。")
    config = ExtractorConfig(base_url="mock://demo", api_key="", model="fixture")
    model, result = await execute(config, doc, [], "numbered", None, offline=True)
    assert result.status == "success"
    assert model.metadata.extraction_status == "complete"
    assert result.debug_info["request_count"] == 0
    assert [item.number for item in model.operations] == ["P7", "X9"]
    assert model.process_graph.edges[0].kind == "declared_predecessor"
    assert model.process_graph.edges[0].evidence[0].method == "rule"


def test_networkx_detects_cycle_across_dependency_and_direct_workflow():
    text = "检测直接转入清洗。检测依赖清洗。"
    model = project([message_document("cycle", text)], "cycle-run", {
        "operations": [
            {"id": "inspect", "name": "检测", "evidence": [{"quote": "检测直接转入清洗"}]},
            {"id": "clean", "name": "清洗", "evidence": [{"quote": "检测依赖清洗"}]},
        ],
        "relations": [
            {"type": "directlyPrecedes", "source_id": "inspect", "target_id": "clean",
             "evidence": [{"quote": "检测直接转入清洗"}]},
            {"type": "dependsOn", "source_id": "inspect", "target_id": "clean",
             "evidence": [{"quote": "检测依赖清洗"}]},
        ],
    })
    assert {issue.code for issue in model.validation.issues} >= {"precedence_cycle", "dependency_cycle"}
    assert all(edge.status == "invalid" for edge in model.process_graph.edges)


def test_numeric_column_and_quality_clause_keep_parameter_target_and_evidence():
    text = ("工序编号,工序名称,压力(kPa),节拍(s/件),关键工艺约束\n"
            "01,检测,50,≤120,温度不超过40 ℃；完成电压及绝缘检测\n")
    parsed = parse_file_bytes(text.encode(), "measurements.csv")
    model = project([file_document("file", "measurements.csv", parsed.parsed_text, parsed.parsed_preview)], "pressure")
    by_code = {item.parameter_code: item for item in model.constraints}
    assert (by_code["pressure"].comparator, by_code["pressure"].value, by_code["pressure"].unit) == ("=", 50, "kPa")
    assert [cite.location.cell for cite in by_code["pressure"].evidence] == ["C2", "C1"]
    assert (by_code["temperature"].comparator, by_code["temperature"].unit) == ("<=", "°C")
    assert (by_code["takt_time"].comparator, by_code["takt_time"].value, by_code["takt_time"].basis) == ("<=", 120, "件")
    assert all(item.status == "verified" and item.target_id == "operation:01" for item in by_code.values())
    assert by_code["electrical_test"].comparator is None


@pytest.mark.asyncio
async def test_partial_source_retries_only_uncached_batches(monkeypatch):
    from app.services import semantic_execution

    text = "\n".join(f"其他自然语言条件第{i}项。" for i in range(11))
    parsed = parse_file_bytes(text.encode(), "supplement.md")
    source = file_document("notes", "supplement.md", parsed.parsed_text, parsed.parsed_preview)
    calls = []

    async def provider(config, user_message, files, current_state=None):
        calls.append(files[0].parsed_text)
        return ExtractionResult(status="success", extracted_json={},
                                debug_info={"request_count": 1, "finish_reason": "stop"})

    monkeypatch.setattr(semantic_execution, "run_extraction", provider)
    config = ExtractorConfig(base_url="http://fixture", api_key="placeholder", model="test", request_budget=1)
    message = message_document("input-1", "工序为检测，编号X9。")
    first, result = await execute(config, message, [source], "first", None)
    assert result.status == "partial"
    assert [batch.status for batch in first.metadata.batches] == ["complete", "unprocessed"]
    second, result = await execute(config, message_document("input-2", ""), [source], "second", first)
    assert result.status == "success" and second.metadata.extraction_status == "complete"
    assert len(calls) == 2 and "第0项" in calls[0] and "第0项" not in calls[1]
    assert "notes" in second.metadata.processed_source_ids
    third, result = await execute(config, message_document("input-3", ""), [source], "third", second)
    assert len(calls) == 2 and third.content_digest == second.content_digest


@pytest.mark.asyncio
async def test_ambiguous_revision_preserves_base_and_does_not_call_provider(monkeypatch):
    from app.services import semantic_execution

    async def provider(*args, **kwargs):
        pytest.fail("Ambiguous revision must go through review before model calls")

    monkeypatch.setattr(semantic_execution, "run_extraction", provider)
    original = project([message_document("old", "工序为检测，编号X9。")], "base")
    config = ExtractorConfig(base_url="http://fixture", api_key="placeholder", model="test")
    revised, result = await execute(config, message_document("new", "请修改工时。"), [], "ambiguous", original)
    assert result.status == "partial"
    assert revised.operations == original.operations and not revised.constraints
    assert any(issue.code == "revision_pending" for issue in revised.validation.issues)
    assert result.debug_info["request_count"] == 0


def test_structured_product_component_has_quantity_and_cell_evidence():
    text = "工序编号,工序名称,产品名称,产品组成\n01,装配,标准模组,8个方形电芯；结构连接件\n"
    parsed = parse_file_bytes(text.encode(), "components.csv")
    doc = file_document("file", "components.csv", parsed.parsed_text, parsed.parsed_preview)
    model = project([doc], "components")
    cell, = [item for item in model.materials if item.name == "方形电芯"]
    relation, = [edge for edge in model.relations if edge.type == "hasComponent" and edge.target_id == cell.id]
    assert (relation.quantity.value, relation.quantity.unit, relation.quantity.basis) == (8, "个", "每标准模组")
    assert relation.evidence[0].location.cell == "D2"
    assert relation.evidence[0].quote == "8个方形电芯"
