"""Comprehensive unit and end-to-end tests for Resource Matching Agent."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.parser import parse_file_bytes, parse_file_path
from domain_semantics.handoff import build_resource_matching_input
from domain_semantics.pipeline import project
from domain_semantics.sources import file_document, message_document
from model_assets import AssetCatalog, get_default_catalog
from resource_matching import (
    ResourceMatchingConfig,
    ResourceMatchingOutput,
    ScoringWeights,
    run_resource_matching,
)

ROOT = Path(__file__).resolve().parents[2]


def _csv_doc(body: str):
    parsed = parse_file_bytes(body.encode("utf-8"), "case.csv")
    return file_document("case-csv", "case.csv", parsed.parsed_text, parsed.parsed_preview)


def _task_msg(extra: str = ""):
    return message_document("task-msg", "构建电池模组装配产线数字孪生模型。" + extra)


def _battery_rmi():
    wb = ROOT / "test case/工艺需求文件.xlsx"
    parsed = parse_file_path(wb)
    doc = file_document("workbook", wb.name, parsed.parsed_text, parsed.parsed_preview)
    narrative = (ROOT / "test case/自然语言建模需求.md").read_text(encoding="utf-8")
    model = project([doc, message_document("narrative", narrative)], "battery-e2e")
    return build_resource_matching_input(model)


def test_battery_module_e2e_matching_and_gold_alignment():
    rmi = _battery_rmi()
    out = asyncio.run(run_resource_matching(rmi, config=ResourceMatchingConfig(top_k=3)))

    # Contract round-trip & metadata preservation
    assert out == ResourceMatchingOutput.model_validate_json(out.model_dump_json())
    assert out.source_requirement_version == rmi.source_model.run_id
    assert out.source_handoff_status == "ready_with_warnings"
    assert len(out.operation_matches) == 16
    assert len(out.process_graph.edges) == 15
    assert len(out.line_constraints) == 1
    assert out.line_constraints[0].parameter_code == "takt_time"
    assert out.summary.matched_single_count == 16
    assert out.summary.average_top1_capability_coverage == 1.0

    # Check against independent gold annotations
    gold = json.loads((ROOT / "backend/experiments/battery_matching_gold.json").read_text(encoding="utf-8"))
    by_num = {op.operation_number: op for op in out.operation_matches}
    for row in gold["operations"]:
        res = by_num[row["operation_number"]]
        top_ids = {c.asset_id for c in res.top_k_candidates}
        assert top_ids <= set(row["qualified_single_assets"]), f"OP {row['operation_number']} unexpected: {top_ids}"
        assert res.top_k_candidates[0].hard_constraint_verdict == "PASS"
        # Every expected pending asset lands in pending_candidates
        pend_ids = {c.asset_id for c in res.pending_candidates}
        for exp_pend in row["expected_pending_assets"]:
            assert exp_pend in pend_ids, f"OP {row['operation_number']} missing pending {exp_pend}"
        # No distractor appears in qualified Top-k
        for dist in row["expected_rejected_distractors"]:
            assert dist not in top_ids, f"OP {row['operation_number']} admitted distractor {dist}"


def test_hard_numeric_constraint_and_unit_conversion_never_overridden_by_score():
    # Explicit operation-level mandatory process_time <= 0.7 min (= 42 s) on 钢带捆扎
    # MA-ASM-07 (36s) PASS, MA-ASM-08 (46s) FAIL, MA-ASM-09 (150s) FAIL!
    table = _csv_doc(
        "工序编号,工序名称,制造能力,输入物料,输出物料,工序类型,前置工序,关键工艺约束\n"
        "10,钢带捆扎,模组压紧与钢带固定,装有跨接片的模组；钢带,捆扎后模组,主线工序,无,必须保证单件工时不超过0.7分钟\n"
    )
    model = project([table, _task_msg()], "numeric-hard")
    rmi = build_resource_matching_input(model)
    # Even if we weight modeling_completeness heavily, FAIL assets must stay rejected
    cfg = ResourceMatchingConfig(
        top_k=5,
        weights=ScoringWeights(functional_fit=0.1, material_fit=0.1, technical_performance=0.1, modeling_completeness=0.7),
    )
    out = asyncio.run(run_resource_matching(rmi, config=cfg))
    op = out.operation_matches[0]
    assert [c.asset_id for c in op.top_k_candidates] == ["MA-ASM-07"]
    rejected_ids = {c.asset_id for c in op.rejected_candidates}
    assert {"MA-ASM-08", "MA-ASM-09"} <= rejected_ids
    for rej in op.rejected_candidates:
        if rej.asset_id in {"MA-ASM-08", "MA-ASM-09"}:
            assert rej.hard_constraint_verdict == "FAIL"


def test_line_takt_does_not_hard_reject_single_machine_cycle_time():
    # Line takt <= 120s/模组 must NOT hard-reject MA-ASM-09 (150s/模组); it stays qualified with parallel advisory!
    table = _csv_doc(
        "工序编号,工序名称,制造能力,输入物料,输出物料,工序类型,前置工序,关键工艺约束\n"
        "10,钢带捆扎,模组压紧与钢带固定,装有跨接片的模组；钢带,捆扎后模组,主线工序,无,按规定位置固定\n"
    )
    model = project([table, _task_msg("要求整线节拍不超过120 s/模组。")], "line-takt")
    rmi = build_resource_matching_input(model)
    out = asyncio.run(run_resource_matching(rmi, config=ResourceMatchingConfig(top_k=5)))
    op = out.operation_matches[0]
    top_ids = [c.asset_id for c in op.top_k_candidates]
    assert "MA-ASM-09" in top_ids
    slow = next(c for c in op.top_k_candidates if c.asset_id == "MA-ASM-09")
    assert slow.hard_constraint_verdict == "PASS"
    assert slow.line_takt_reference and "2 台并行实例" in slow.line_takt_reference


def test_missing_parameter_routes_to_pending_confirmation_not_qualified_or_rejected():
    # Require explicit tolerance <= 0.1 mm on 激光清洗:
    # MA-CLEAN-01 has positioning_accuracy, none have tolerance -> all should go to pending_candidates (UNKNOWN), not FAIL!
    table = _csv_doc(
        "工序编号,工序名称,制造能力,输入物料,输出物料,工序类型,前置工序,关键工艺约束\n"
        "02,激光清洗,激光清洗,已定位电芯,清洗后电芯,主线工序,无,必须满足位置公差不超过0.1mm\n"
    )
    model = project([table, _task_msg()], "unknown-param")
    rmi = build_resource_matching_input(model)
    out = asyncio.run(run_resource_matching(rmi))
    op = out.operation_matches[0]
    assert op.match_status == "pending_only"
    assert op.top_k_candidates == []
    pend_ids = {c.asset_id for c in op.pending_candidates}
    assert {"MA-CLEAN-01", "MA-CLEAN-02", "MA-CLEAN-03"} <= pend_ids
    assert all(c.hard_constraint_verdict == "UNKNOWN" for c in op.pending_candidates)


def test_multi_asset_collaborative_decomposition_when_no_single_asset_covers_all(tmp_path):
    # Copy only partial assets (MA-FEED-03 [FEED] + MA-ROB-01 [GRIP, POSITION]) into isolated catalog
    default = get_default_catalog()
    iso = AssetCatalog(data_dir=tmp_path)
    for aid in ("MA-FEED-03", "MA-ROB-01"):
        iso.upsert(default.get(aid), persist=True)

    table = _csv_doc(
        "工序编号,工序名称,制造能力,输入物料,输出物料,工序类型,前置工序,关键工艺约束\n"
        "01,电芯上料,电芯供料、抓取与定位,来料电芯,已定位电芯,主线工序,无,不得损伤电芯\n"
    )
    model = project([table, _task_msg()], "multi-asset")
    rmi = build_resource_matching_input(model)
    out = asyncio.run(run_resource_matching(rmi, catalog=iso))
    op = out.operation_matches[0]
    assert op.match_status == "matched_partial_composable"
    assert op.multi_asset_recommended is True
    assert op.top_k_candidates == []
    by_cap = {g.capability_code: [c.asset_id for c in g.candidates] for g in op.partial_candidates_by_capability}
    assert by_cap["FEED"] == ["MA-FEED-03"]
    assert by_cap["GRIP"] == ["MA-ROB-01"]
    assert by_cap["POSITION"] == ["MA-ROB-01"]


def test_no_qualified_resource_and_blocked_handoff_diagnostics():
    # 1. No qualified resource: PACKAGE on prismatic_cell (MA-AUX-06 only supports battery_module)
    table = _csv_doc(
        "工序编号,工序名称,制造能力,输入物料,输出物料,工序类型,前置工序,关键工艺约束\n"
        "99,电芯包装,包装,来料电芯,已定位电芯,主线工序,无,\n"
    )
    model = project([table, _task_msg()], "unmatched")
    rmi = build_resource_matching_input(model)
    out = asyncio.run(run_resource_matching(rmi))
    op = out.operation_matches[0]
    assert op.match_status == "unmatched"
    assert op.top_k_candidates == []
    assert any(u.code == "no_qualified_resource" for u in op.unmet_requirements)

    # 2. Blocked handoff input (no modeling task)
    blocked_model = project([table], "blocked")
    blocked_rmi = build_resource_matching_input(blocked_model)
    assert blocked_rmi.handoff_validation.status == "blocked"
    blocked_out = asyncio.run(run_resource_matching(blocked_rmi))
    assert blocked_out.matching_status == "blocked"
    assert any(d.severity == "blocker" for d in blocked_out.diagnostics)


def test_method_c_outperforms_method_a_keyword_baseline():
    from experiments.run_matching import _score_predictions
    from resource_matching.retrieval import dereference_input, retrieve_keyword_candidates

    rmi = _battery_rmi()
    catalog = get_default_catalog()
    gold = json.loads((ROOT / "backend/experiments/battery_matching_gold.json").read_text(encoding="utf-8"))
    ctx = dereference_input(rmi)

    out_c = asyncio.run(run_resource_matching(rmi, catalog=catalog, config=ResourceMatchingConfig(top_k=3, method="C")))
    picks_c = {
        op.operation_id: [
            {"asset_id": c.asset_id, "reasons": c.match_reasons, "checks": [chk.model_dump(mode="json") for chk in c.constraint_checks]}
            for c in op.top_k_candidates
        ]
        for op in out_c.operation_matches
    }
    score_c = _score_predictions(picks_c, out_c, gold, rmi, catalog, 3)

    out_a = asyncio.run(run_resource_matching(rmi, catalog=catalog, config=ResourceMatchingConfig(top_k=3, method="A")))
    picks_a = {
        op.operation_id: [
            {"asset_id": asset.asset_id, "reasons": [f"关键词命中: {', '.join(toks[:5])}"], "checks": []}
            for asset, _, toks in retrieve_keyword_candidates(op, catalog)[:3]
        ]
        for op in ctx.operations
    }
    score_a = _score_predictions(picks_a, out_a, gold, rmi, catalog, 3)

    assert score_c["hard_constraint_violation_rate"] == 0.0
    assert score_c["precision_at_3"] == 1.0
    assert score_c["recall_at_3"] == 1.0
    assert score_c["capability_coverage"] == 1.0
    assert score_c["precision_at_3"] > score_a["precision_at_3"]
    assert score_a["hard_constraint_violation_rate"] > 0.0
