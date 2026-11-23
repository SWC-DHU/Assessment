"""Orchestrate one requirement agent with bounded extraction and deterministic tools."""
from collections import deque
from dataclasses import replace
import json
from pathlib import Path

from pydantic import ValidationError

from domain_semantics.catalog import query_ontology
from domain_semantics.candidates import ExtractionCandidates
from domain_semantics.pipeline import Builder, CANDIDATES, candidate_errors, revision_request, table_rows
from domain_semantics.schema import BatchReport, SemanticModel
from domain_semantics.sources import Document

from .extraction_batches import ExtractionBatch, plan_batches, recovered_rows, scoped_candidates
from .llm_extractor import ExtractionResult, ExtractorConfig, FileInputContext, run_extraction
from .redaction import safe_endpoint
from .semantic_tools import ToolTrace

PROMPTS = Path(__file__).resolve().parents[2] / "domain_semantics" / "prompts"
PROMPT_VERSION = "2.0.0"
MAX_BATCH_OUTPUT = 4096
MAX_BATCH_ROWS = 24


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8").strip()


def candidate_context(message: Document, files: list[Document]) -> list[FileInputContext]:
    """Whole-input control context used only by the explicit A/B experiment methods."""
    return [FileInputContext(filename=doc.ref.name, parsed_text=json.dumps(
        [{"document_id": frag.document_id, "fragment_id": frag.key, "text": frag.text}
         for frag in doc.fragments if frag.text.strip()], ensure_ascii=False))
        for doc in [*files, message] if any(part.text.strip() for part in doc.fragments)]


def schema_instruction(config: ExtractorConfig) -> str:
    def compact(value):
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items() if key not in {"title", "default"}}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value

    schema = json.dumps(compact(ExtractionCandidates.model_json_schema()), ensure_ascii=False, separators=(",", ":"))
    return "\n".join((prompt("extraction"), "候选 JSON Schema：" + schema, "任务配置背景：",
                      config.system_prompt[:1200], config.business_rules[:1200]))


def unprocessed(batch: ExtractionBatch, reason: str, status="unprocessed", rows=0) -> BatchReport:
    return BatchReport(id=batch.id, source_ids=batch.source_ids, scope=batch.scope, status=status,
                       input_characters=batch.size, accepted_rows=rows, reason=reason)


def related_reference(batch: ExtractionBatch, builder: Builder) -> dict | None:
    text = "\n".join(fragment.text for fragment in batch.fragments)
    rows = {(part.document_id, part.location.sheet, part.location.row) for part in batch.fragments if part.location.sheet}
    entities = [{"id": item.id, "name": item.name, "number": getattr(item, "number", None)}
                for group in ("operations", "products", "materials", "capabilities")
                for item in getattr(builder.model, group)
                if item.name in text or any((cite.document_id, cite.location.sheet, cite.location.row) in rows
                                            for cite in item.evidence)]
    return {"known_entities": entities[:12]} if entities else None


def constrain_citations(payload: dict, batch: ExtractionBatch) -> dict:
    """Only shown fragments can support model-proposed facts."""
    result = {}
    for key, rows in payload.items():
        if not isinstance(rows, list):
            result[key] = rows
            continue
        clean = []
        for row in rows:
            if not isinstance(row, dict):
                clean.append(row)
                continue
            value = dict(row)
            for slot in ("evidence", "equipment_evidence"):
                if not isinstance(value.get(slot), list):
                    continue
                citations = []
                for citation in value[slot]:
                    if not isinstance(citation, dict) or not isinstance(citation.get("quote"), str):
                        continue
                    quote = citation["quote"]
                    matches = {
                        (part.document_id, part.key) for part in batch.fragments if quote.strip() and quote in part.text
                        and citation.get("document_id") in {None, part.document_id}
                        and citation.get("fragment_id") in {None, part.key}
                    } if all(isinstance(citation.get(field), (str, type(None))) for field in ("document_id", "fragment_id")) else set()
                    if len(matches) == 1:
                        document_id, fragment_id = next(iter(matches))
                        citations.append({"quote": quote, "document_id": document_id, "fragment_id": fragment_id})
                value[slot] = citations
            clean.append(value)
        result[key] = clean
    return result


def count_rows(payload) -> int:
    return sum(len(value) for value in payload.values() if isinstance(value, list))


def locally_valid(payload: dict) -> dict:
    valid = {}
    for key, schema in CANDIDATES.items():
        rows = payload.get(key, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            try:
                schema.model_validate(row)
            except ValidationError:
                continue
            valid.setdefault(key, []).append(row)
    return valid


def merge_repair(original: dict, corrected: dict) -> dict:
    output = locally_valid(original)
    for key, rows in locally_valid(corrected).items():
        merged = output.setdefault(key, [])
        for row in rows:
            if row not in merged:
                merged.append(row)
    return output


async def extract_facts(config, batch, builder, trail, budget, repair=None):
    instruction = schema_instruction(config)
    if repair is not None:
        instruction += "\n" + prompt("repair")
    configured = replace(config, domain_instruction=instruction, extraction_schema={}, require_source_quote=False,
                         request_budget=min(budget, 2), max_tokens=min(config.max_tokens, MAX_BATCH_OUTPUT))
    reference = related_reference(batch, builder) or {}
    if repair is not None:
        reference.update(repair)
    step_name = "llm_repair" if repair is not None else "extract_facts"
    entry = trail.start(step_name, f"{batch.scope} · {batch.size} 字符 · {len(batch.fragments)} 个片段", batch.id)
    result = await run_extraction(configured, "仅提取本批来源支持的事实。", batch.context(), current_state=reference or None)
    debug = result.debug_info
    trail.end(entry, result.status, result.error_message or f"{debug.get('request_count', 0)} 次请求 · {debug.get('finish_reason') or '响应结束'}",
              debug.get("error_code"))
    return result


async def execute(config: ExtractorConfig, message: Document, files: list[Document], run_id: str,
                  base: SemanticModel | None, offline: bool = False, failed: list[str] | None = None,
                  session_id: str | None = None, publish=None) -> tuple[SemanticModel | None, ExtractionResult]:
    text = message.fragments[0].text
    docs = [*files, *([message] if text.strip() else [])]
    trail = ToolTrace(publish)
    catalog = trail.call("query_ontology", "读取 RDF 版本及关系端点", query_ontology)
    builder = Builder(docs, run_id, base)
    builder.model.metadata.session_id = session_id
    known = {item.document_id: item.content_sha256 for item in base.source_refs} if base else {}
    revised = bool(base and revision_request(text))
    covered = set()
    covered_text = set()
    rows = trail.call("parse_document", f"{len(files)} 份文件的来源定位", lambda: list(table_rows(files, covered)))
    if not revised:
        rows = [row for row in rows if row["number"].document_id not in known or any(
            doc.ref.document_id == row["number"].document_id and known.get(doc.ref.document_id) != doc.ref.content_sha256
            for doc in files)]
    else:
        rows = []

    for error in failed or []:
        builder.issue("file_parse_error", error, "source_refs")
    initial = trail.start("deterministic_extraction", f"{len(rows)} 行已识别表格")
    builder.table(rows)
    if revised:
        builder.revise(message)
    elif text.strip() and not (base and reformat_revision(text)):
        builder.explicit_requirements(message, covered_text)
    trail.end(initial, "success" if builder.by_id else "skipped",
              f"{len(rows)} 行表格 · {len(builder.by_id)} 项已建立或继承事实")

    batch_docs = [doc for doc in docs if known.get(doc.ref.document_id) != doc.ref.content_sha256]
    batches = [] if revised else plan_batches(batch_docs, covered, covered_text)
    attempts, reports, payloads = [], [], []
    budget = config.request_budget
    request_queue = deque(batches)
    last_error = None
    skip_reason = ("offline" if offline else "unconfigured" if not config.api_key else None)
    if not request_queue:
        trail.end(trail.start("extract_facts", "已识别表格或定向修订"), "skipped", "本次无须提交候选批次")

    while request_queue:
        batch = request_queue.popleft()
        if skip_reason or budget <= 0:
            reports.append(unprocessed(batch, skip_reason or "request_budget_exhausted"))
            continue
        result = await extract_facts(config, batch, builder, trail, budget)
        attempts.append(result.debug_info)
        budget -= result.debug_info.get("request_count", 0)
        code = result.debug_info.get("error_code")
        payload = constrain_citations(result.extracted_json or {}, batch)
        errors = candidate_errors(payload) if result.extracted_json is not None else result.debug_info.get("validation_errors", [])
        if result.status != "success":
            last_error = result
            if code in {"authentication_failed", "model_access_denied", "provider_forbidden", "unconfigured"}:
                skip_reason = code
        cut = result.debug_info.get("finish_reason") == "length" or code == "output_truncated"
        full = count_rows(locally_valid(payload)) >= MAX_BATCH_ROWS
        if cut or full:
            overflow = "output_truncated" if cut else "batch_record_limit"
            if not payload:
                payload = constrain_citations(recovered_rows(result.debug_info.get("raw_response", "")), batch)
            if payload:
                payloads.append((batch, payload))
            children = batch.split() if batch.attempt == 0 else []
            if children and budget > 0:
                # Replacements are considered alongside untouched batches. Always preserve recovered records.
                request_queue.extend(children)
                trail.end(trail.start("batch_split", batch.scope, batch.id), "partial",
                          f"达到批次输出上限，拆成 {len(children)} 个子批次并保留 {count_rows(locally_valid(payload))} 条候选", overflow)
                continue
            reports.append(unprocessed(batch, overflow, "partial" if locally_valid(payload) else "failed", count_rows(locally_valid(payload))))
            continue
        if errors and budget > 0 and batch.attempt == 0:
            repaired = await extract_facts(config, batch, builder, trail, budget, repair={
                "fields_to_repair": errors[:8],
                "previous_output_excerpt": result.debug_info.get("raw_response", "")[:850],
            })
            attempts.append(repaired.debug_info)
            budget -= repaired.debug_info.get("request_count", 0)
            if repaired.extracted_json is not None and repaired.debug_info.get("finish_reason") != "length":
                repaired_payload = constrain_citations(repaired.extracted_json, batch)
                fixed = candidate_errors(repaired_payload)
                if len(fixed) <= len(errors):
                    payload, errors = merge_repair(payload, repaired_payload), fixed
                    result = repaired
            elif not payload:
                payload = constrain_citations(recovered_rows(repaired.debug_info.get("raw_response", "")), batch)
        if payload:
            payloads.append((batch, payload))
        if result.status == "success" and not errors:
            reports.append(unprocessed(batch, None, "complete", count_rows(locally_valid(payload))))
        else:
            reports.append(unprocessed(batch, code or "schema_invalid", "partial" if locally_valid(payload) else "failed", count_rows(locally_valid(payload))))

    published_payload, batch_scopes = scoped_candidates(payloads)
    if payloads and base and reformat_revision(text):
        builder.issue("revision_pending", "请通过人工审核明确选择修订对象及字段", "operations")
    elif payloads:
        trail.call("map_semantic_entities", f"{count_rows(published_payload)} 条候选", builder.candidates,
                   published_payload, batch_scopes)
    if any(report.status != "complete" for report in reports):
        for report in reports:
            if report.status != "complete":
                builder.issue("batch_" + report.status, f"{report.scope} · {report.reason}", report.id, "unprocessed")
    if batches and not builder.by_id:
        builder.issue("empty_extraction", "本次未生成可核验事实", "extraction")
    mapping = trail.call("normalize_term", "使用受控工序、能力及参数词表", builder.mapping_queue) if not revised else []
    if mapping and config.llm_disambiguation and not skip_reason and budget > 0:
        entry = trail.start("llm_disambiguation", "词表歧义候选")
        configured = replace(config, domain_instruction=prompt("mapping"), extraction_schema={},
                             request_budget=budget, max_tokens=min(config.max_tokens, MAX_BATCH_OUTPUT))
        judged = await run_extraction(configured, json.dumps(mapping[:10], ensure_ascii=False), [])
        attempts.append(judged.debug_info)
        choices = (judged.extracted_json or {}).get("choices", {})
        if isinstance(choices, dict):
            builder.accept_mapping(choices)
        trail.end(entry, judged.status, judged.error_message)

    prior = [item.model_copy(update={"id": item.id if ":" in item.id else f"{base.run_id[:8]}:{item.id}"})
             for item in base.metadata.batches if item.status != "complete"] if base and base.metadata else []
    builder.model.metadata.batches = [*prior, *reports]
    incomplete = bool(failed) or any(report.status != "complete" for report in builder.model.metadata.batches)
    incomplete |= any(issue.code in {"candidate_shape", "evidence_not_located", "revision_pending", "empty_extraction"}
                      for issue in builder.issues)
    have_facts = bool(builder.by_id)
    status = "failed" if not have_facts else "partial" if incomplete else "complete"
    builder.model.metadata.extraction_status = status
    model = builder.finish(trail.call)
    representative = attempts[-1] if attempts else {}
    debug = dict(representative)
    usages = [entry["usage"] for entry in attempts if entry.get("usage")]
    debug.update(
        provider_attempts=attempts, request_count=sum(entry.get("request_count", 0) for entry in attempts),
        request_budget=config.request_budget, timeout_seconds=config.timeout_seconds,
        max_tokens=config.max_tokens, batch_max_tokens=min(config.max_tokens, MAX_BATCH_OUTPUT),
        temperature=config.temperature, json_mode=config.json_mode,
        duration_ms=round(sum(stage.get("duration_ms", 0) for stage in trail.steps), 2),
        usage={key: sum(entry[key] for entry in usages if isinstance(entry.get(key), int))
               for key in {key for entry in usages for key in entry} if any(isinstance(entry.get(key), int) for entry in usages)},
        requested_model=config.model, base_url=safe_endpoint(config.base_url), prompt_version=PROMPT_VERSION,
        pipeline_stages=[*trail.steps, *builder.stages], tool_trace=trail.steps,
        batches=[report.model_dump(mode="json") for report in reports], extraction_status=status,
        validation_errors=[f"{i.code} · {i.path} · {i.message}" for i in model.validation.issues],
        source_ids=[ref.document_id for ref in model.source_refs], input_digest=model.input_digest,
        ontology_version=catalog["ontology_version"], schema_version=model.schema_version,
        vocabulary_version=model.vocabulary_version,
    )
    if last_error and status == "failed":
        debug.update(error_code=last_error.debug_info.get("error_code"), error_message=last_error.error_message)
    if not have_facts:
        message_text = (last_error.error_message if last_error else
                        "本次没有可核验事实。请补充明确的工艺描述或配置模型。")
        if skip_reason == "unconfigured" and batches:
            debug.update(error_code=skip_reason, error_message="当前模型缺少可用的 API Key")
            message_text = debug["error_message"]
        return None, ExtractionResult(status="error", error_message=message_text, debug_info=debug)
    summary = (f"{'部分完成，已生成语义版本' if incomplete else '已生成语义版本'}。{len(model.operations)} 道工序、{len(model.relations)} 条语义关系，"
               f"{len(model.unresolved_items)} 项诊断。")
    if incomplete:
        summary += " 已保留核对过的事实，可在 Debug 查看批次状态。"
    return model, ExtractionResult(status="partial" if incomplete else "success", summary_text=summary, debug_info=debug)


def reformat_revision(text: str) -> bool:
    return any(term in text for term in ("改为", "修改", "修订", "调整为", "更正", "替换"))
