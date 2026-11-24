"""Comparative evaluation harness for Resource Matching Methods A (Keyword), B (Direct LLM), and C (Ontology + Multi-Constraint)."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from app.config import settings
from app.database import SessionLocal
from app.models.db_models import AgentProfile
from app.services.llm_extractor import ExtractorConfig
from app.services.parser import parse_file_path
from app.services.redaction import safe_endpoint
from domain_semantics.handoff import build_resource_matching_input
from domain_semantics.pipeline import project
from domain_semantics.sources import digest, file_document, message_document
from model_assets.catalog import AssetCatalog, get_default_catalog
from resource_matching.agent import _run_method_b_llm, run_resource_matching
from resource_matching.constraints import evaluate_asset_constraints
from resource_matching.retrieval import dereference_input, retrieve_keyword_candidates
from resource_matching.schema import ResourceMatchingConfig, ResourceMatchingOutput


def _score_predictions(
    raw_top_k: dict[str, list[dict]],
    output: ResourceMatchingOutput,
    gold: dict,
    rmi,
    catalog: AssetCatalog,
    top_k: int,
) -> dict:
    """Evaluate Precision@k, Recall@k, capability coverage, hard-constraint violation rate, rationale accuracy, and pending recall."""
    context = dereference_input(rmi)
    op_by_number = {op.operation_number or op.operation_id: op for op in context.operations}
    out_by_number = {op.operation_number or op.operation_id: op for op in output.operation_matches}

    precisions: list[float] = []
    recalls: list[float] = []
    cap_coverages: list[float] = []
    total_predicted = 0
    hard_violations = 0
    rationale_ok = 0
    pending_tp = 0
    pending_expected = 0
    distractor_total = 0
    distractor_rejected = 0

    for entry in gold["operations"]:
        op_num = entry["operation_number"]
        op_ctx = op_by_number.get(op_num)
        op_out = out_by_number.get(op_num)
        if op_ctx is None or op_out is None:
            continue
        gold_qualified = set(entry.get("qualified_single_assets", []))
        gold_pending = set(entry.get("expected_pending_assets", []))
        gold_distractors = set(entry.get("expected_rejected_distractors", []))

        picks = raw_top_k.get(op_ctx.operation_id, [])[:top_k]
        picked_ids = [p["asset_id"] for p in picks]

        hits = sum(1 for aid in picked_ids if aid in gold_qualified)
        precisions.append(hits / len(picked_ids) if picked_ids else 0.0)
        denom_r = min(top_k, len(gold_qualified)) if gold_qualified else 0
        if denom_r > 0:
            recalls.append(hits / denom_r)

        for pick in picks:
            total_predicted += 1
            asset = catalog.get(pick["asset_id"])
            if asset is None:
                hard_violations += 1
                cap_coverages.append(0.0)
                continue
            verdict, checks, _, _, _ = evaluate_asset_constraints(op_ctx, asset, context)
            if verdict == "FAIL":
                hard_violations += 1
            req = set(op_ctx.required_capability_codes)
            cov = len(req & set(asset.capability_codes)) / max(1, len(req))
            cap_coverages.append(cov)
            # Rationale accuracy: candidate supplies structured verified constraint checks matching real verdict
            reasons = pick.get("reasons") or []
            structured_checks = pick.get("checks") or []
            if structured_checks:
                check_consistent = all(
                    c.get("verdict") == "PASS" for c in structured_checks if c.get("is_hard")
                ) == (verdict == "PASS")
                if check_consistent and bool(reasons):
                    rationale_ok += 1
            elif verdict == "PASS" and bool(reasons) and any(
                kw in " ".join(reasons) for kw in (asset.identity.name[:4], *op_ctx.required_capability_codes, "物料", "能力", "关键词")
            ):
                rationale_ok += 1

        if gold_pending:
            pending_expected += len(gold_pending)
            obs_pending = {c.asset_id for c in op_out.pending_candidates}
            pending_tp += len(gold_pending & obs_pending)

        if gold_distractors:
            distractor_total += len(gold_distractors)
            distractor_rejected += sum(1 for aid in gold_distractors if aid not in set(picked_ids))

    return {
        f"precision_at_{top_k}": round(sum(precisions) / max(1, len(precisions)), 4),
        f"recall_at_{top_k}": round(sum(recalls) / max(1, len(recalls)), 4),
        "capability_coverage": round(sum(cap_coverages) / max(1, len(cap_coverages)), 4) if cap_coverages else 0.0,
        "hard_constraint_violation_rate": round(hard_violations / max(1, total_predicted), 4) if total_predicted else 0.0,
        "rationale_accuracy": round(rationale_ok / max(1, total_predicted), 4) if total_predicted else 0.0,
        "pending_identification_recall": round(pending_tp / pending_expected, 4) if pending_expected else None,
        "distractor_rejection_rate": round(distractor_rejected / distractor_total, 4) if distractor_total else None,
        "total_predicted_candidates": total_predicted,
        "hard_constraint_violations": hard_violations,
    }


def _resolve_llm(args) -> ExtractorConfig | None:
    if args.offline:
        return None
    if args.saved_agent:
        with SessionLocal() as db:
            agent = db.get(AgentProfile, args.saved_agent) if args.saved_agent != "first" else db.query(AgentProfile).first()
            if agent:
                return ExtractorConfig(
                    base_url=agent.base_url or settings.default_llm_base_url,
                    api_key=agent.api_key or settings.default_llm_api_key,
                    model=agent.model or settings.default_llm_model,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    timeout_seconds=args.timeout,
                    request_budget=1,
                    json_mode=True,
                )
    if settings.default_llm_api_key:
        return ExtractorConfig(
            base_url=settings.default_llm_base_url,
            api_key=settings.default_llm_api_key,
            model=settings.default_llm_model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout,
            request_budget=1,
            json_mode=True,
        )
    return None


async def run_experiment(args) -> dict:
    gold_text = args.gold.read_text(encoding="utf-8")
    gold = json.loads(gold_text)
    methods = [m.strip().upper() for m in args.methods.split(",") if m.strip()]
    if set(methods) - {"A", "B", "C"}:
        raise SystemExit("methods 必须为 A,B,C 的子集")

    parsed = parse_file_path(args.input, row_limit=None)
    if parsed.status != "success":
        raise SystemExit(parsed.error)
    docs = [file_document("exp-workbook", args.input.name, parsed.parsed_text, parsed.parsed_preview)]
    if args.text_file:
        docs.append(message_document("exp-narrative", args.text_file.read_text(encoding="utf-8")))
    semantic = project(docs, "matching-experiment-source")
    rmi = build_resource_matching_input(semantic)
    catalog = get_default_catalog()
    context = dereference_input(rmi)
    llm_cfg = _resolve_llm(args)

    runs = []
    for trial in range(args.repeat):
        for method in methods:
            if method == "B" and llm_cfg is None:
                runs.append({
                    "method": "B",
                    "trial": trial + 1,
                    "status": "skipped",
                    "reason": "离线或未配置在线 LLM Provider，跳过方法B直接LLM匹配基线",
                })
                continue
            started = perf_counter()
            cfg = ResourceMatchingConfig(top_k=args.top_k, method=method, use_llm=(method == "C" and args.use_llm and llm_cfg is not None))
            raw_top_k: dict[str, list[dict]] = {}
            if method == "A":
                output = await run_resource_matching(rmi, catalog=catalog, config=cfg)
                # Capture raw lexical Top-k (before constraint filtering) to measure genuine violation rate!
                for op in context.operations:
                    hits = retrieve_keyword_candidates(op, catalog)[: args.top_k]
                    raw_top_k[op.operation_id] = [
                        {"asset_id": asset.asset_id, "reasons": [f"关键词命中: {', '.join(toks[:5])}"], "checks": []}
                        for asset, _, toks in hits
                    ]
            elif method == "B":
                op_results, b_picks, calls, model_used, diags = await _run_method_b_llm(context, catalog, cfg, llm_cfg)
                output = await run_resource_matching(rmi, catalog=catalog, config=ResourceMatchingConfig(top_k=args.top_k, method="C"))
                output = output.model_copy(update={"config": cfg, "operation_matches": op_results})
                for op_id, picks in b_picks.items():
                    raw_top_k[op_id] = [
                        {"asset_id": str(p.get("asset_id") or ""), "reasons": [str(p.get("reason") or "")], "checks": []}
                        for p in picks
                    ]
            else:
                output = await run_resource_matching(rmi, catalog=catalog, config=cfg, llm_config=llm_cfg)
                for op_res in output.operation_matches:
                    raw_top_k[op_res.operation_id] = [
                        {
                            "asset_id": c.asset_id,
                            "reasons": list(c.match_reasons),
                            "checks": [chk.model_dump(mode="json") for chk in c.constraint_checks],
                        }
                        for c in op_res.top_k_candidates
                    ]
            elapsed_ms = round((perf_counter() - started) * 1000, 2)
            metrics = _score_predictions(raw_top_k, output, gold, rmi, catalog, args.top_k)
            runs.append({
                "method": method,
                "trial": trial + 1,
                "status": output.matching_status,
                "elapsed_ms": elapsed_ms,
                "llm_calls": output.summary.llm_calls,
                "metrics": metrics,
            })

    executed_methods = sorted({r["method"] for r in runs if r.get("status") != "skipped"})
    mean_elapsed = {
        m: round(sum(r["elapsed_ms"] for r in runs if r["method"] == m) / sum(r["method"] == m for r in runs), 2)
        for m in executed_methods
    }
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case": gold["name"],
        "gold_digest": digest(gold_text),
        "catalog_version": catalog.catalog_version,
        "catalog_size": len(catalog.all_assets()),
        "top_k": args.top_k,
        "offline": args.offline,
        "provider": safe_endpoint(llm_cfg.base_url) if llm_cfg else None,
        "runs": runs,
        "mean_elapsed_ms": mean_elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--methods", default="a,c")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--saved-agent")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(asyncio.run(run_experiment(args)), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(result + "\n", encoding="utf-8")
    else:
        print(result)


if __name__ == "__main__":
    main()
