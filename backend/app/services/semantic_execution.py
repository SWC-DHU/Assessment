"""Application adapter for the shared LLM service and portable domain pipeline."""
from dataclasses import replace
import json
from pathlib import Path

from domain_semantics.candidates import ExtractionCandidates
from domain_semantics.pipeline import Builder, candidate_errors, revision_request, table_rows, timestamp, trace
from domain_semantics.schema import SemanticModel
from domain_semantics.sources import Document

from .llm_extractor import ExtractionResult, ExtractorConfig, FileInputContext, run_extraction
from .redaction import redact, safe_endpoint

PROMPTS = Path(__file__).resolve().parents[2] / "domain_semantics" / "prompts"
PROMPT_VERSION = "1.0.0"


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8").strip()


def ai_stage(name, mark, result):
    debug = result.debug_info
    return trace(name, *mark, "success" if result.status == "success" else (
        "skipped" if debug.get("error_code") == "unconfigured" else "error"),
        result.error_message or f"{debug.get('request_count', 0)} 次请求 · {debug.get('model_used') or '模型未返回名称'}",
        debug.get("error_code"))


def candidate_context(message: Document, files: list[Document]) -> list[FileInputContext]:
    text = message.fragments[0].text
    input_ctx = [
        FileInputContext(filename=doc.ref.name, parsed_text=json.dumps(
            {"document_id": doc.ref.document_id, "fragments": [
                {"fragment_id": fragment.key, "text": fragment.text} for fragment in doc.fragments
            ]}, ensure_ascii=False)) for doc in files
    ]
    if text.strip():
        input_ctx.append(FileInputContext(filename="本次用户消息的证据定位", parsed_text=json.dumps(
            {"document_id": message.ref.document_id, "fragment_id": "message", "text": text}, ensure_ascii=False)))
    return input_ctx


def schema_instruction(config: ExtractorConfig) -> str:
    schema = json.dumps(ExtractionCandidates.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    return "\n".join((
        prompt("extraction"),
        "系统候选 JSON Schema：" + schema,
        "以下为智能体维护者提供的领域背景。候选 Schema 与证据规则优先。",
        config.system_prompt, config.business_rules,
    ))


async def execute(config: ExtractorConfig, message: Document, files: list[Document], run_id: str,
                  base: SemanticModel | None, offline: bool = False,
                  failed: list[str] | None = None) -> tuple[SemanticModel | None, ExtractionResult]:
    text = message.fragments[0].text
    docs = [*files, *([message] if text.strip() else [])]
    builder = Builder(docs, run_id, base)
    mark = timestamp()
    known = {item.document_id: item.content_sha256 for item in base.source_refs} if base else {}
    is_revision = bool(base and revision_request(text))
    rows = [] if is_revision else list(table_rows([doc for doc in files
                                                   if known.get(doc.ref.document_id) != doc.ref.content_sha256]))
    stages = [trace("source_preprocessing", *mark, "partial" if failed else "success",
                    f"{len(files)} 份可读文件 · {sum(len(doc.fragments) for doc in docs)} 个来源片段 · {len(rows)} 行明确工序")]
    for error in failed or []:
        builder.issue("file_parse_error", error, "source_refs")
    model_result = None
    attempts = []
    budget = config.request_budget
    mark = timestamp()
    builder.table(rows)
    revised = bool(base and revision_request(text) and builder.revise(message))
    explicit = builder.explicit_line_constraints(message) if text.strip() and not revised and not (
        base and reformat_revision(text)) else 0
    stages.append(trace("deterministic_extraction", *mark, "success" if (rows or revised or explicit) else "skipped",
                        "定向版本修订" if revised else f"{len(rows)} 行表格事实 · {explicit} 项明确整线约束"))

    do_llm = not revised and not offline and (bool(text.strip()) or not rows or len(files) > 1)
    if do_llm:
        input_ctx = candidate_context(message, files)
        instruction = schema_instruction(config)
        configured = replace(config, domain_instruction=instruction, extraction_schema={}, require_source_quote=False)
        state = ({
            group: [{"id": item.id, "name": item.name, **({"number": item.number} if group == "operations" else {})}
                    for item in getattr(base, group)]
            for group in ("operations", "products", "materials", "capabilities")
        } if base else None)
        mark = timestamp()
        model_result = await run_extraction(configured, text, input_ctx, current_state=state)
        attempts.append(model_result.debug_info)
        budget -= model_result.debug_info.get("request_count", 0)
        stages.append(ai_stage("llm_extraction", mark, model_result))
        mark = timestamp()
        errors = (candidate_errors(model_result.extracted_json) if model_result.extracted_json is not None else
                  model_result.debug_info.get("validation_errors", []))
        stages.append(trace("schema_validation", *mark,
                            ("invalid" if errors else "success") if model_result.extracted_json is not None or errors else "skipped",
                            "\n".join(errors[:10]) if errors else "候选字段和类型检查"))
        if errors and budget > 0:
            mark = timestamp()
            repair = replace(configured, domain_instruction=instruction + "\n" + prompt("repair"), request_budget=budget)
            repair_state = {"candidate_errors": errors[:20], "previous_response": model_result.debug_info["raw_response"],
                            "base_entities": state}
            corrected = await run_extraction(repair, text, input_ctx, current_state=repair_state)
            attempts.append(corrected.debug_info)
            budget -= corrected.debug_info.get("request_count", 0)
            stages.append(ai_stage("llm_repair", mark, corrected))
            if corrected.extracted_json is not None:
                fixed_errors = candidate_errors(corrected.extracted_json)
                if len(fixed_errors) < len(errors) or model_result.extracted_json is None:
                    model_result = corrected
                    errors = fixed_errors
        if model_result.extracted_json is None:
            builder.issue(model_result.debug_info.get("error_code") or "extraction_failed",
                          model_result.error_message or "候选生成失败", "extraction", "unprocessed")
        elif base and reformat_revision(text):
            builder.issue("revision_pending", "该修订无法安全定位。请指定工序编号、时间类型和新值。", "operations")
        else:
            for error in errors:
                builder.issue("candidate_schema", error, "extraction", "invalid")
            builder.candidates(redact(model_result.extracted_json, config.api_key))
    else:
        for phase in ("llm_extraction", "schema_validation"):
            stages.append({"stage": phase, "status": "skipped", "summary": "本次使用确定性处理"})

    mark = timestamp()
    queue = [] if revised else builder.mapping_queue()
    if queue and budget > 0 and not offline and config.api_key and config.llm_disambiguation:
        mapping = replace(config, domain_instruction=prompt("mapping"), request_budget=budget, extraction_schema={},
                          require_source_quote=False)
        mapped = await run_extraction(mapping, json.dumps(queue, ensure_ascii=False), [])
        attempts.append(mapped.debug_info)
        choices = (mapped.extracted_json or {}).get("choices", {})
        if isinstance(choices, dict):
            builder.accept_mapping(choices)
        stages.append(ai_stage("llm_disambiguation", mark, mapped))
    stages.append(trace("ontology_mapping", *mark, "skipped" if revised else "success",
                        "继承基础版本映射" if revised else
                        f"{sum(cap.code is not None for cap in builder.model.capabilities)} 个能力已映射 · {len(queue)} 个歧义项"))
    if not builder.by_id and model_result and model_result.status == "success":
        builder.issue("empty_extraction", "本次未生成可核验事实", "extraction")
    model = builder.finish()
    debug = dict(model_result.debug_info if model_result else {})
    usages = [entry["usage"] for entry in attempts if entry.get("usage")]
    debug.update(
        provider_attempts=attempts, request_count=sum(entry.get("request_count", 0) for entry in attempts),
        request_budget=config.request_budget, timeout_seconds=config.timeout_seconds, max_tokens=config.max_tokens,
        temperature=config.temperature, json_mode=config.json_mode,
        duration_ms=round(sum(stage.get("duration_ms", 0) for stage in [*stages, *builder.stages]), 2),
        usage={key: sum(entry[key] for entry in usages if isinstance(entry.get(key), int))
               for key in {key for entry in usages for key in entry} if any(isinstance(entry.get(key), int) for entry in usages)},
        requested_model=config.model, base_url=safe_endpoint(config.base_url), prompt_version=PROMPT_VERSION,
        pipeline_stages=[*stages, *builder.stages],
        validation_errors=[f"{i.code} · {i.path} · {i.message}" for i in model.validation.issues],
        source_ids=[ref.document_id for ref in model.source_refs],
        input_digest=model.input_digest, ontology_version=model.ontology_version,
        schema_version=model.schema_version, vocabulary_version=model.vocabulary_version,
    )
    have_facts = bool(builder.by_id)
    if (not have_facts and model_result is None) or (model_result and model_result.status == "error" and not rows and not explicit):
        message_text = (model_result.error_message if model_result else
                        "离线运行需要包含明确工序列的表格。自然语言抽取请配置模型。")
        return None, ExtractionResult(status="error", error_message=message_text, debug_info=debug)
    summary = (f"已生成语义版本。{len(model.operations)} 道工序、{len(model.relations)} 条关系、"
               f"{len(model.unresolved_items)} 项诊断。")
    if model_result and model_result.error_message:
        summary += " 本次保留了已建立的事实，请在 Debug 查看模型错误。"
    return model, ExtractionResult(status="success", summary_text=summary, debug_info=debug)


def reformat_revision(text: str) -> bool:
    return any(term in text for term in ("改为", "修改", "修订", "调整", "更正", "替换"))
