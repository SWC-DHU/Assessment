"""Run matched LLM baselines and ontology-enhanced extraction with reproducible metadata."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from app.config import settings
from app.database import SessionLocal
from app.models.db_models import AgentProfile
from app.services.llm_extractor import ExtractorConfig, run_extraction
from app.services.parser import parse_file_path
from app.services.redaction import redact, safe_endpoint
from app.services.semantic_execution import PROMPT_VERSION, candidate_context, execute, schema_instruction
from domain_semantics import ontology_metadata
from domain_semantics.catalog import candidates
from domain_semantics.pipeline import candidate_errors
from domain_semantics.schema import SCHEMA_VERSION
from domain_semantics.sources import digest, file_document, locate, message_document
from domain_semantics.validation import canonical_unit


TASK = "请抽取可追溯的制造需求事实。"
PLAIN = """阅读制造需求，输出 JSON。工序、产品、物料、能力、约束、关系分别放入 operations、products、
materials、capabilities、constraints、relations。实体写 id、name；工序编号写 number，关系写 type、source_id、
target_id。直接工序前驱使用 hasPredecessor（工序指向前驱），输入输出用 consumes、produces。
数值约束写 target_id、comparator、value、lower、upper、unit、basis。
每项用 evidence 数组记录 quote、document_id、fragment_id。"""


def prf(expected, observed):
    match = len(expected & observed)
    precision = match / len(observed) if observed else None
    recall = match / len(expected) if expected else None
    f1 = 2 * match / (len(expected) + len(observed)) if expected or observed else None
    return {"precision": precision, "recall": recall, "f1": f1,
            "true_positive": match, "expected": len(expected), "observed": len(observed)}


def facts(gold):
    if "rows" not in gold:
        return {tuple(op) for op in gold["operations"]}, {tuple(edge) for edge in gold["relations"]}
    ops = set()
    relations = set()
    for number, name, _, inputs, outputs, _, predecessors, _ in gold["rows"]:
        ops.add((number, name))
        for kind, field in (("hasPredecessor", predecessors), ("consumes", inputs), ("produces", outputs)):
            relations.update((kind, number, term) for term in field.split("；") if term and term != "—")
    return ops, relations


def rows(data, group):
    return [item for item in data.get(group, []) if isinstance(item, dict)] if isinstance(data.get(group), list) else []


def score(data, gold, docs, *, schema_valid):
    gold_ops, gold_relations = facts(gold)
    scope = {"hasPredecessor", "consumes", "produces"}
    gold_materials = {target for kind, _, target in gold_relations if kind in {"consumes", "produces"}}
    observed_materials = {item["name"] for item in rows(data, "materials") if isinstance(item.get("name"), str)}
    gold_products = set(gold.get("products", []))
    observed_products = {item["name"] for item in rows(data, "products") if isinstance(item.get("name"), str)}
    ops = rows(data, "operations")
    lookups = {item.get("id"): (item.get("number") or item.get("name"))
               for group in ("operations", "products", "materials", "capabilities")
               for item in rows(data, group) if isinstance(item.get("id"), str)}
    predicted_ops = {(op.get("number"), op.get("name")) for op in ops
                     if isinstance(op.get("name"), str) and (op.get("number") is None or isinstance(op["number"], str))}
    predicted_edges = set()
    is_v2_model = data.get("schema_version") == "2.0.0"
    for edge in rows(data, "relations"):
        if (not all(isinstance(edge.get(field), str) for field in ("type", "source_id", "target_id"))
                or edge["type"] not in scope or edge.get("status") not in {None, "verified"}):
            continue
        kind = edge["type"]
        target = lookups.get(edge["target_id"], edge["target_id"])
        labels = [target]
        if is_v2_model and kind in {"consumes", "produces"}:
            labels = [cite["quote"] for cite in rows(edge, "evidence") if cite.get("method") == "structured"
                      and isinstance(cite.get("quote"), str) and (cite.get("location") or {}).get("cell")] or labels
        for label in labels:
            predicted_edges.add((kind, lookups.get(edge["source_id"], edge["source_id"]), label))
    canonical_material_count = len(observed_materials)
    if is_v2_model and "rows" in gold:
        # Gold annotates workbook labels, including explicitly named process states.
        observed_materials = {target for kind, _, target in predicted_edges if kind in {"consumes", "produces"}}
    expected_quantities = {tuple(item) for item in gold["numeric_constraints"]}
    predicted_quantities = set()
    for item in rows(data, "constraints"):
        if item.get("status") not in {None, "verified"} or not any(isinstance(item.get(key), (float, int)) for key in ("value", "lower")):
            continue
        if not isinstance(item.get("target_id"), str) or not isinstance(item.get("name"), str):
            continue
        fields = [item.get(field) for field in ("comparator", "value", "lower", "upper", "unit", "basis")]
        if any(isinstance(value, (dict, list)) for value in fields):
            continue
        mappings = candidates("parameters", item["name"])
        code = item.get("parameter_code") or (mappings[0] if len(mappings) == 1 else None)
        if code is not None and not isinstance(code, str):
            continue
        fields[4] = canonical_unit(fields[4])
        predicted_quantities.add((lookups.get(item["target_id"], item["target_id"]), code, *fields))
    expected_components = {tuple(item) for item in gold.get("product_components", []) if len(item) == 4}
    actual_components = {
        (lookups.get(edge.get("source_id")), lookups.get(edge.get("target_id")),
         edge["quantity"].get("value"), edge["quantity"].get("unit"))
        for edge in rows(data, "relations") if edge.get("type") == "hasComponent"
        and edge.get("status") in {None, "verified"} and isinstance(edge.get("quantity"), dict)
        and set(edge["quantity"]) >= {"value", "unit"} and isinstance(edge["quantity"]["value"], (float, int))
        and isinstance(edge["quantity"]["unit"], str)
        and all(isinstance(edge.get(field), str) for field in ("source_id", "target_id"))
    }
    gold_mapping = gold["mapping_sample"]
    mapped = {value["name"]: value.get("code") for value in rows(data, "capabilities")
              if isinstance(value.get("name"), str)}
    mapped_correct = sum(mapped.get(name) == code for name, code in gold_mapping.items())
    mapped_count = sum(isinstance(mapped.get(name), str) for name in gold_mapping)
    matched_quotes = total_quotes = uncited = fact_count = 0
    for group in ("products", "operations", "materials", "material_states", "capabilities", "constraints", "relations"):
        for item in rows(data, group):
            cites = rows(item, "evidence")
            if not cites and isinstance(item.get("source_quote"), str):
                cites = [{"quote": item["source_quote"]}]
            matches = sum(bool(locate(docs, cite.get("quote"), "evaluate",
                                      document_id=cite.get("document_id"), fragment_id=cite.get("fragment_id")))
                          for cite in cites)
            total_quotes += len(cites)
            matched_quotes += matches
            uncited += matches == 0
            fact_count += 1
    topology = prf({edge for edge in gold_relations if edge[0] == "hasPredecessor"},
                   {edge for edge in predicted_edges if edge[0] == "hasPredecessor"})
    return {
        "operation_entity": prf(gold_ops, predicted_ops),
        "material_entity": prf(gold_materials, observed_materials),
        "product_entity": prf(gold_products, observed_products),
        "component_quantities": prf(expected_components, actual_components),
        "canonical_material_count": canonical_material_count,
        "material_state_count": len(rows(data, "material_states")),
        "process_relations": prf(gold_relations, predicted_edges),
        "numeric_constraints": prf(expected_quantities, predicted_quantities),
        "mapping_accuracy": mapped_correct / mapped_count if mapped_count else None,
        "mapping_coverage": mapped_count / len(gold_mapping) if gold_mapping else None,
        "evidence_accuracy": matched_quotes / total_quotes if total_quotes else None,
        "unsupported_fact_fraction": uncited / fact_count if fact_count else None,
        "schema_valid": schema_valid,
        "topology_completeness": topology["recall"],
    }


def config_for(args):
    if args.saved_agent:
        with SessionLocal() as db:
            agent = db.get(AgentProfile, args.saved_agent) if args.saved_agent != "first" else db.query(AgentProfile).first()
            if not agent:
                raise SystemExit("找不到指定的智能体")
            return ExtractorConfig(
                base_url=agent.base_url or settings.default_llm_base_url,
                api_key=agent.api_key or settings.default_llm_api_key, model=agent.model or settings.default_llm_model,
                temperature=args.temperature, max_tokens=args.max_tokens, request_budget=args.budget,
                timeout_seconds=args.timeout, json_mode=True,
            )
    return ExtractorConfig(base_url=settings.default_llm_base_url, api_key=settings.default_llm_api_key,
                           model=settings.default_llm_model, temperature=args.temperature, max_tokens=args.max_tokens,
                           request_budget=args.budget, timeout_seconds=args.timeout)


async def run(args):
    gold_text = args.gold.read_text(encoding="utf-8")
    overlay_text = args.expected_overlay.read_text(encoding="utf-8") if args.expected_overlay else "{}"
    gold = json.loads(gold_text) | json.loads(overlay_text)
    methods = args.methods.lower().split(",")
    if set(methods) - {"a", "b", "c"}:
        raise SystemExit("方法应为 a,b,c 的子集")
    if args.offline and set(methods) != {"c"}:
        raise SystemExit("离线模式只运行 C 的确定性路径")
    if args.input:
        parsed = parse_file_path(args.input, row_limit=None)
        if parsed.status != "success":
            raise SystemExit(parsed.error)
        files = [file_document("experiment-input", args.input.name, parsed.parsed_text, parsed.parsed_preview)]
    else:
        files = []
    text = args.text_file.read_text(encoding="utf-8") if args.text_file else gold.get("input") or ("" if args.offline else TASK)
    message = message_document("experiment-message", text)
    config = config_for(args)
    if not args.offline and (not config.api_key or config.base_url.startswith("mock://")):
        raise SystemExit("A/B/C 的模型对照需要真实 Provider 或本地 OpenAI 兼容服务")
    docs = [*files, *([message] if text else [])]
    metadata = ontology_metadata()
    output = []
    for trial in range(args.repeat):
        for method in methods:
            start = perf_counter()
            if method == "c":
                model, result = await execute(config, message, files, f"experiment-c-{trial + 1}", None, offline=args.offline)
                data = model.model_dump(mode="json") if model else {}
                schema_valid = model is not None
            else:
                instruction = PLAIN if method == "a" else schema_instruction(config)
                probe = ExtractorConfig(**{**config.__dict__, "domain_instruction": instruction})
                result = await run_extraction(probe, text, candidate_context(message, files))
                data = redact(result.extracted_json or {}, config.api_key)
                schema_valid = result.extracted_json is not None and not candidate_errors(data)
            output.append({
                "method": method.upper(), "implementation": f"{method.upper()}-2.0",
                "trial": trial + 1, "status": result.status,
                "coverage_status": result.debug_info.get("extraction_status"),
                "finish_reason": result.debug_info.get("finish_reason"),
                "elapsed_ms": round((perf_counter() - start) * 1000, 2),
                "calls": result.debug_info.get("request_count"), "usage": result.debug_info.get("usage") or None,
                "response_model": result.debug_info.get("model_used"), "error_code": result.debug_info.get("error_code"),
                "prompt_digest": digest(result.debug_info.get("system_prompt") or ""),
                "output_digest": data.get("content_digest") or digest(json.dumps(data, ensure_ascii=False, sort_keys=True)),
                "metrics": score(data, gold, docs, schema_valid=schema_valid) |
                           {"output_complete": (result.debug_info.get("extraction_status") == "complete") if method == "c"
                            else schema_valid and result.debug_info.get("finish_reason") != "length"},
            })
    return {
        "created_at": datetime.now(timezone.utc).isoformat(), "input": gold["name"],
        "input_digest": digest(json.dumps([doc.ref.content_sha256 for doc in docs])),
        "gold_digest": digest(gold_text + "\n" + overlay_text),
        "configuration": {
            "provider": safe_endpoint(config.base_url), "model": config.model, "temperature": config.temperature,
            "max_tokens": config.max_tokens, "timeout_s": config.timeout_seconds, "request_budget": config.request_budget,
            "json_mode": config.json_mode, "offline": args.offline, "prompt_version": PROMPT_VERSION,
            "evaluation_version": "2.0.0",
            "schema_version": SCHEMA_VERSION, "ontology_version": metadata["ontology_version"],
            "vocabulary_version": metadata["vocabulary"]["version"],
        },
        "runs": output,
        "mean_elapsed_ms": {
            method: round(sum(row["elapsed_ms"] for row in output if row["method"] == method) /
                          sum(row["method"] == method for row in output), 2)
            for method in {row["method"] for row in output}
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--text-file", type=Path, help="补充用户需求；只发送该文件与 --input 的原始输入")
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--expected-overlay", type=Path, help="仅供评分的额外标注文件")
    parser.add_argument("--methods", default="a,b,c")
    parser.add_argument("--saved-agent", help="智能体 ID 或 first，密钥仅读入内存")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 21))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--budget", type=int, choices=range(1, 33), default=1)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 0 <= args.temperature <= 2 or args.max_tokens < 64 or args.timeout < 1:
        parser.error("temperature 必须在 0–2 间，max-tokens 至少 64，timeout 至少 1")
    result = json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(result + "\n")
    else:
        print(result)


if __name__ == "__main__":
    main()
