"""Standalone, framework-agnostic LLM extraction & validation service.

Compatible with any OpenAI Chat Completions endpoint (OpenAI, DeepSeek, Qwen,
Gemini proxies, Ollama, vLLM, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .redaction import redact, safe_endpoint

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, AuthenticationError


def normalize_base_url(raw_url: str) -> str:
    """Normalize user-entered OpenAI-compatible Base URL safely."""
    url = (raw_url or "").strip()
    if not url:
        return ""
    if url.startswith("mock://"):
        return url

    url = url.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")

    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and parts.netloc and parts.path in ("", "/"):
        url = urlunsplit((parts.scheme, parts.netloc, "/v1", parts.query, parts.fragment))
    return url


def _is_response_format_error(exc: APIStatusError) -> bool:
    if exc.status_code not in (400, 422):
        return False
    msg = f"{exc.message} {getattr(exc, 'body', '')}".lower()
    return any(tok in msg for tok in ("response_format", "json_object", "json mode", "structured"))


@dataclass
class ExtractorConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.1
    max_tokens: int = 4096
    json_mode: bool = True
    require_source_quote: bool = True
    system_prompt: str = ""
    business_rules: str = ""
    extraction_schema: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 90
    request_budget: int = 2
    llm_disambiguation: bool = True
    domain_instruction: str = ""
    plain_instruction: str = ""


@dataclass
class FileInputContext:
    filename: str
    parsed_text: str


@dataclass
class ExtractionResult:
    status: str  # "success" | "error"
    extracted_json: dict[str, Any] | None = None
    summary_text: str = ""
    error_message: str | None = None
    debug_info: dict[str, Any] = field(default_factory=dict)


def _describe_fields(fields: list[dict[str, Any]], indent: int = 0) -> list[str]:
    lines: list[str] = []
    prefix = "  " * indent
    for f in fields:
        name = f.get("name", "").strip()
        if not name:
            continue
        ftype = f.get("type", "string")
        req = "必填" if f.get("required") else "可选"
        desc = f.get("description", "").strip()
        line = f"{prefix}- `{name}` ({ftype}, {req})"
        if desc:
            line += f": {desc}"
        lines.append(line)
        children = f.get("children") or []
        if children:
            lines.extend(_describe_fields(children, indent + 1))
    return lines


def build_schema_instruction(extraction_schema: dict[str, Any], require_source_quote: bool) -> str:
    """Build clear JSON schema instructions from either visual fields or raw JSON schema."""
    mode = extraction_schema.get("mode", "visual")
    parts: list[str] = ["## 输出数据结构要求（必须返回合法 JSON 对象）"]

    if mode == "json_schema" and extraction_schema.get("json_schema"):
        schema_str = json.dumps(extraction_schema["json_schema"], ensure_ascii=False, indent=2)
        parts.append("请严格遵循以下 JSON Schema 输出一个 JSON 对象：")
        parts.append(f"```json\n{schema_str}\n```")
    else:
        fields = extraction_schema.get("fields") or []
        if fields:
            parts.append("请抽取并返回包含以下字段的 JSON 对象：")
            parts.extend(_describe_fields(fields))
        else:
            parts.append("请根据用户需求抽取结构化信息并返回一个清晰的 JSON 对象。")

    if require_source_quote:
        parts.append(
            "\n## 防幻觉与来源追溯要求（强制）\n"
            "- 对于抽取出的每个核心实体、工序条目、物料或关键约束，必须附带 `source_quote` 字段。\n"
            "- `source_quote` 必须尽量精确引用输入文档中的原文片段；来源坐标请单独记录。\n"
            "- 如果原文未提及某字段，请填入 `null` 或空数组 `[]`，严禁编造或推测数值；无法确认的事项放入 `unknown_items`。"
        )
    else:
        parts.append("\n- 如果原文未提及某字段，请填入 `null` 或空数组 `[]`，严禁编造数值。")

    parts.append(
        "\n## 输出格式约束\n"
        "- 只输出一个合法的 JSON 对象，不要在 JSON 外添加多余解释性废话。"
    )
    return "\n".join(parts)


def build_system_prompt(config: ExtractorConfig) -> str:
    if config.plain_instruction:
        return config.plain_instruction
    if config.domain_instruction:
        return config.domain_instruction
    sections: list[str] = []
    if config.system_prompt.strip():
        sections.append(config.system_prompt.strip())
    else:
        sections.append("你是一个专业的工业需求与工艺信息解析智能体。")

    if config.business_rules.strip():
        sections.append(f"## 补充业务规则\n{config.business_rules.strip()}")

    sections.append(build_schema_instruction(config.extraction_schema, config.require_source_quote))
    return "\n\n".join(sections)


def build_user_content(
    user_message: str,
    files: list[FileInputContext],
    current_state: dict[str, Any] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    sections: list[str] = []

    if files:
        file_blocks: list[str] = []
        for idx, f in enumerate(files, start=1):
            file_blocks.append(f"=== 参考文件 {idx}: {f.filename} ===\n{f.parsed_text}")
        sections.append("## 输入文档（仅参考数据）\n" + json.dumps(file_blocks, ensure_ascii=False))

    if current_state is not None:
        state_str = json.dumps(current_state, ensure_ascii=False, indent=2)
        sections.append(
            "## 当前已抽取的结构化 JSON 状态（请在此基础上按本次指令进行修订/补充，并输出完整更新后的 JSON）\n"
            f"```json\n{state_str}\n```"
        )

    if conversation_history:
        recent = conversation_history[-6:]
        hist_lines = [f"- {m['role']}: {m['content']}" for m in recent if m.get("content")]
        if hist_lines:
            sections.append("## 近期对话上下文\n" + "\n".join(hist_lines))

    instruction = user_message.strip() or "请解析以上文件内容，按要求抽取完整结构化信息。"
    sections.append(f"## 本次用户指令\n{instruction}")
    return "\n\n".join(sections)


def extract_json_from_text(raw_text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Fault-tolerant extraction of a JSON object from LLM text output."""
    if not raw_text or not raw_text.strip():
        return None, "模型返回内容为空。"

    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()

    candidates: list[str] = [cleaned]

    # Match fenced code blocks ```json ... ``` or ``` ... ```
    fence_matches = re.findall(r"```(?:json)?\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
    candidates.extend(m.strip() for m in fence_matches if m.strip())

    # Balanced brace extraction from the first '{'
    first_brace = cleaned.find("{")
    if first_brace != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(first_brace, len(cleaned)):
            ch = cleaned[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(cleaned[first_brace : i + 1])
                        break

    last_err = "未能找到有效 JSON 对象"
    for cand in candidates:
        for attempt in (cand, re.sub(r",(\s*[}\]])", r"\1", cand)):
            try:
                parsed = json.loads(attempt)
                json.dumps(parsed, allow_nan=False)
                if isinstance(parsed, dict):
                    return parsed, None
                if isinstance(parsed, list):
                    return {"items": parsed}, None
                last_err = f"JSON 根节点必须为对象 (object)，实际为 {type(parsed).__name__}"
            except json.JSONDecodeError as exc:
                last_err = f"JSON 语法解析失败 (line {exc.lineno}, col {exc.colno}): {exc.msg}"
            except ValueError:
                last_err = "JSON 包含非有限数值"

    return None, last_err


def _validate_json_schema_node(
    node: Any,
    schema_obj: dict[str, Any],
    path: str,
    errors: list[str],
    require_source_quote: bool,
) -> None:
    if not isinstance(node, dict) or not isinstance(schema_obj, dict):
        return
    for req_key in schema_obj.get("required") or []:
        if req_key not in node or node[req_key] is None or node[req_key] == "":
            label = f"`{path}.{req_key}`" if path else f"`{req_key}`"
            errors.append(f"缺少必填字段: {label}")

    props = schema_obj.get("properties") or {}
    for prop_name, prop_schema in props.items():
        if not isinstance(prop_schema, dict) or prop_name not in node:
            continue
        val = node[prop_name]
        if val is None:
            continue
        sub_path = f"{path}.{prop_name}" if path else prop_name
        ptype = prop_schema.get("type")
        if isinstance(val, dict) and (ptype == "object" or (isinstance(ptype, list) and "object" in ptype)):
            _validate_json_schema_node(val, prop_schema, sub_path, errors, require_source_quote)
        elif isinstance(val, list) and (ptype == "array" or (isinstance(ptype, list) and "array" in ptype)):
            items_schema = prop_schema.get("items")
            if isinstance(items_schema, dict) and items_schema.get("type") == "object":
                for idx, item in enumerate(val, start=1):
                    if isinstance(item, dict):
                        _validate_json_schema_node(
                            item, items_schema, f"{sub_path}[{idx}]", errors, require_source_quote
                        )
                        if require_source_quote and not item.get("source_quote"):
                            errors.append(f"`{sub_path}[{idx}]` 未附带 `source_quote` 原文依据")


def _validate_visual_fields(
    node: dict[str, Any],
    fields: list[dict[str, Any]],
    path: str,
    errors: list[str],
    require_source_quote: bool,
) -> None:
    for f in fields:
        name = (f.get("name") or "").strip()
        if not name:
            continue
        full_name = f"{path}.{name}" if path else name
        val = node.get(name)
        if f.get("required") and (name not in node or val is None or val == ""):
            errors.append(f"缺少必填字段: `{full_name}`")

        children = f.get("children") or []
        if not children:
            continue

        if f.get("type") == "object" and isinstance(val, dict):
            _validate_visual_fields(val, children, full_name, errors, require_source_quote)
        elif f.get("type") == "array" and isinstance(val, list):
            for idx, item in enumerate(val, start=1):
                if not isinstance(item, dict):
                    continue
                _validate_visual_fields(item, children, f"{full_name}[{idx}]", errors, require_source_quote)
                if require_source_quote and not item.get("source_quote"):
                    errors.append(f"`{full_name}[{idx}]` 未附带 `source_quote` 原文依据")


def validate_extracted_data(
    data: dict[str, Any],
    extraction_schema: dict[str, Any],
    require_source_quote: bool,
) -> list[str]:
    """Validate required fields and source_quote presence, returning diagnostic warnings."""
    errors: list[str] = []
    mode = extraction_schema.get("mode", "visual")

    if mode == "json_schema":
        schema_obj = extraction_schema.get("json_schema") or {}
        _validate_json_schema_node(data, schema_obj, "", errors, require_source_quote)
    else:
        fields = extraction_schema.get("fields") or []
        _validate_visual_fields(data, fields, "", errors, require_source_quote)

    if require_source_quote:
        has_any_quote = _contains_source_quote(data)
        if not has_any_quote:
            errors.append("已开启防幻觉追溯 (`require_source_quote`)，但抽取结果中未检测到任何 `source_quote` 字段。")

    return errors


def _contains_source_quote(node: Any) -> bool:
    if isinstance(node, dict):
        if node.get("source_quote"):
            return True
        return any(_contains_source_quote(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_source_quote(v) for v in node)
    return False


def _run_demo_extraction(
    user_message: str,
    files: list[FileInputContext],
    current_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Deterministic offline demo extraction when base_url is mock://demo or demo fallback is used."""
    if current_state is not None:
        updated = json.loads(json.dumps(current_state))
        # Handle simple demo refinement like changing cycle time to 45
        match_sec = re.search(r"(\d+)\s*秒", user_message)
        if match_sec and isinstance(updated.get("operations"), list) and len(updated["operations"]) >= 1:
            new_sec = int(match_sec.group(1))
            target_idx = 2 if ("第三" in user_message or "3" in user_message) and len(updated["operations"]) >= 3 else 0
            updated["operations"][target_idx]["cycle_time_sec"] = new_sec
            updated["operations"][target_idx]["notes"] = (
                f"已按用户修订指令调整为 {new_sec} 秒"
            )
        return updated

    combined_text = "\n".join(f.parsed_text for f in files) + "\n" + user_message
    product_name = "新能源汽车电机壳体（压铸+CNC加工）"
    if "减速器" in combined_text:
        product_name = "精密行星减速器壳体"
    elif "电池托盘" in combined_text:
        product_name = "动力电池下箱体托盘"

    return {
        "product_name": product_name,
        "part_no": "MTR-HSG-2026-A03",
        "target_takt_sec": 60,
        "operations": [
            {
                "seq": "OP10",
                "name": "高压压铸成型",
                "equipment": "2500T冷室压铸机",
                "cycle_time_sec": 42,
                "input_materials": ["ADC12铝合金锭 (3.8kg/件)", "脱模剂"],
                "output_materials": ["壳体压铸毛坯"],
                "predecessors": [],
                "quality_requirements": "X光探伤气孔率 <= 2%，无冷隔裂纹",
                "source_quote": "[演示样本] OP10 高压压铸成型，节拍42s，使用ADC12铝合金",
            },
            {
                "seq": "OP20",
                "name": "去毛刺与抛丸清理",
                "equipment": "自动去毛刺工作站 + 履带式抛丸机",
                "cycle_time_sec": 28,
                "input_materials": ["壳体压铸毛坯", "不锈钢丸"],
                "output_materials": ["清理后壳体半成品"],
                "predecessors": ["OP10"],
                "quality_requirements": "分型面残余毛刺 <= 0.2mm",
                "source_quote": "[演示样本] OP20 去毛刺清理，前置工序OP10，工时28s",
            },
            {
                "seq": "OP30",
                "name": "CNC精密加工（端面及轴承孔）",
                "equipment": "五轴卧式加工中心",
                "cycle_time_sec": 55,
                "input_materials": ["清理后壳体半成品", "水溶性切削液"],
                "output_materials": ["精加工电机壳体"],
                "predecessors": ["OP20"],
                "quality_requirements": "轴承孔同轴度 <= 0.015mm，Ra1.6",
                "source_quote": "[演示样本] OP30 CNC精加工，轴承孔同轴度0.015mm，节拍55s",
            },
            {
                "seq": "OP40",
                "name": "气密性检测与清洗烘干",
                "equipment": "差压气密检漏仪 + 超声波清洗线",
                "cycle_time_sec": 35,
                "input_materials": ["精加工电机壳体", "中性清洗剂"],
                "output_materials": ["合格成品壳体"],
                "predecessors": ["OP30"],
                "quality_requirements": "测试压力 0.2MPa，保压30s泄漏量 <= 1.5mL/min",
                "source_quote": "[演示样本] OP40 气密检测 0.2MPa 保压30s，泄漏量<=1.5mL/min",
            },
        ],
        "constraints": [
            {
                "type": "节拍约束",
                "description": "产线综合设计节拍 <= 60s/件，OP30 为当前瓶颈工序（55s）",
                "source_quote": "[演示样本] 产线节拍目标 60s/件",
            },
            {
                "type": "环境与清洁度",
                "description": "成品内腔残留颗粒物直径 <= 0.5mm，总杂质 <= 3mg",
                "source_quote": "[演示样本] 清洁度及气密性要求",
            },
        ],
        "unknown_items": [
            {
                "item": "OP30 CNC加工中心刀库切换时间是否计入单件节拍",
                "reason": "输入资料未明确说明换刀摊销方式",
                "source_quote": "原文未明确说明",
            }
        ],
    }


async def run_extraction(
    config: ExtractorConfig,
    user_message: str,
    files: list[FileInputContext],
    current_state: dict[str, Any] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    use_demo_fallback: bool = False,
    *,
    on_debug=None,
) -> ExtractionResult:
    """Execute extraction or multi-turn refinement against an OpenAI-compatible endpoint."""
    result = await _run_extraction(config, user_message, files, current_state, conversation_history, use_demo_fallback)
    result.debug_info = redact(result.debug_info, config.api_key)
    result.extracted_json = redact(result.extracted_json, config.api_key)
    result.summary_text = redact(result.summary_text, config.api_key)
    result.error_message = redact(result.error_message, config.api_key)
    if on_debug:
        on_debug(result.debug_info)
    return result


async def _run_extraction(config, user_message, files, current_state, conversation_history, use_demo_fallback):
    system_prompt = build_system_prompt(config)
    user_content = build_user_content(
        user_message=user_message,
        files=files,
        current_state=current_state,
        conversation_history=conversation_history,
    )

    started = time.perf_counter()
    debug_info: dict[str, Any] = {
        "system_prompt": system_prompt,
        "user_content": user_content,
        "raw_response": "",
        "validation_errors": [],
        "duration_ms": 0,
        "usage": {},
        "model_used": None,
        "requested_model": config.model,
        "request_count": 0,
        "base_url": safe_endpoint(config.base_url),
        "error_message": None,
    }

    # Explicit offline mock mode
    if config.base_url.strip().startswith("mock://") or use_demo_fallback:
        demo_data = _run_demo_extraction(user_message, files, current_state)
        raw_str = json.dumps(demo_data, ensure_ascii=False, indent=2)
        duration_ms = int((time.perf_counter() - started) * 1000)
        val_errors = validate_extracted_data(
            demo_data, config.extraction_schema, config.require_source_quote
        )
        debug_info.update(
            {
                "raw_response": raw_str,
                "validation_errors": val_errors,
                "duration_ms": duration_ms,
                "usage": {},
                "model_used": "mock-demo-extractor",
            }
        )
        return ExtractionResult(
            status="success",
            extracted_json=demo_data,
            summary_text=_build_summary(demo_data, val_errors, is_demo=True),
            debug_info=debug_info,
        )

    if not config.api_key.strip():
        err = (
            "API Key 未配置：请在顶部「智能体配置」中为当前智能体填写 API Key，"
            "或在 backend/.env 中配置 DEFAULT_LLM_API_KEY。"
            "（如需离线体验抽-填流程，也可点击输入框上方的「离线演示模式」或将 Base URL 设为 mock://demo）"
        )
        debug_info["error_message"] = err
        debug_info["error_code"] = "unconfigured"
        debug_info["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return ExtractionResult(
            status="error",
            error_message=err,
            debug_info=debug_info,
        )

    effective_base_url = normalize_base_url(config.base_url) or "https://api.openai.com/v1"
    effective_model = config.model.strip() or "gpt-4o-mini"
    debug_info["base_url"] = safe_endpoint(effective_base_url)
    debug_info["requested_model"] = effective_model

    client = AsyncOpenAI(
        api_key=config.api_key.strip(),
        base_url=effective_base_url,
        timeout=float(config.timeout_seconds),
        max_retries=0,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    kwargs: dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    if config.json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    raw_response = ""
    usage_dict: dict[str, Any] = {}

    try:
        try:
            debug_info["request_count"] += 1
            resp = await client.chat.completions.create(**kwargs)
        except APIStatusError as exc:
            # Graceful fallback if provider does not support response_format=json_object
            if config.json_mode and config.request_budget > 1 and _is_response_format_error(exc):
                kwargs.pop("response_format", None)
                debug_info["json_mode_fallback"] = True
                debug_info["request_count"] += 1
                resp = await client.chat.completions.create(**kwargs)
            else:
                raise

        debug_info["model_used"] = getattr(resp, "model", None)
        choice = resp.choices[0] if resp.choices else None
        debug_info["finish_reason"] = choice.finish_reason if choice else None
        raw_response = (choice.message.content or "") if choice and choice.message else ""
        if resp.usage:
            usage_dict = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
    except AuthenticationError as exc:
        debug_info.update(error_code="authentication_failed", status_code=401)
        err = f"大模型鉴权失败 (HTTP 401)：请检查 API Key 是否正确。详情：{exc}"
        debug_info.update(
            {
                "raw_response": str(exc),
                "error_message": err,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        )
        return ExtractionResult(status="error", error_message=err, debug_info=debug_info)
    except APITimeoutError as exc:
        debug_info["error_code"] = "service_unavailable"
        err = f"大模型请求超时（超过 {config.timeout_seconds}s）：请检查网络或适当增大超时时间。详情：{exc}"
        debug_info.update(
            {
                "raw_response": str(exc),
                "error_message": err,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        )
        return ExtractionResult(status="error", error_message=err, debug_info=debug_info)
    except APIConnectionError as exc:
        debug_info["error_code"] = "service_unavailable"
        err = f"无法连接到大模型服务 ({config.base_url})：请检查 Base URL 与网络连通性。详情：{exc}"
        debug_info.update(
            {
                "raw_response": str(exc),
                "error_message": err,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        )
        return ExtractionResult(status="error", error_message=err, debug_info=debug_info)
    except APIStatusError as exc:
        code, err = provider_status(exc, effective_model)
        debug_info.update(error_code=code, status_code=exc.status_code)
        debug_info.update(
            {
                "raw_response": str(getattr(exc, "body", "") or exc),
                "error_message": err,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        )
        return ExtractionResult(status="error", error_message=err, debug_info=debug_info)
    except Exception as exc:
        err = f"调用大模型发生未预期错误 ({type(exc).__name__})：{exc}"
        debug_info.update(
            {
                "raw_response": str(exc),
                "error_message": err,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        )
        return ExtractionResult(status="error", error_message=err, debug_info=debug_info)
    finally:
        await client.close()

    duration_ms = int((time.perf_counter() - started) * 1000)
    debug_info["raw_response"] = raw_response
    debug_info["usage"] = usage_dict
    debug_info["duration_ms"] = duration_ms

    parsed_json, json_err = extract_json_from_text(raw_response)
    if parsed_json is None:
        err = f"JSON 解析失败：{json_err}。请在右侧「调试透视 (Debug)」查看模型 Raw 输出。"
        debug_info["error_message"] = err
        debug_info["validation_errors"] = [json_err or "JSON 解析失败"]
        debug_info["error_code"] = "invalid_json"
        if debug_info.get("finish_reason") == "length":
            err = "模型输出达到 Token 限制，未形成完整 JSON。请提高 Max Tokens 或缩小单次解析范围。"
            debug_info.update(error_message=err, error_code="output_truncated")
        return ExtractionResult(status="error", error_message=err, debug_info=debug_info)

    val_errors = validate_extracted_data(
        parsed_json, config.extraction_schema, config.require_source_quote
    )
    debug_info["validation_errors"] = val_errors

    return ExtractionResult(
        status="success",
        extracted_json=parsed_json,
        summary_text=_build_summary(parsed_json, val_errors, is_demo=False),
        debug_info=debug_info,
    )


def _build_summary(data: dict[str, Any], val_errors: list[str], is_demo: bool = False) -> str:
    prefix = "【离线演示模式】已完成模拟抽取与模板填充" if is_demo else "已完成结构化信息抽取并联动填充模板"
    details: list[str] = []

    product = data.get("product_name") or data.get("project_name") or data.get("title")
    if product:
        details.append(f"对象：{product}")

    ops = data.get("operations")
    if isinstance(ops, list):
        details.append(f"共提取 {len(ops)} 道工序")
    elif isinstance(data.get("requirements"), list):
        details.append(f"共提取 {len(data['requirements'])} 条需求要点")

    constraints = data.get("constraints")
    if isinstance(constraints, list) and constraints:
        details.append(f"{len(constraints)} 项约束")

    unknowns = data.get("unknown_items")
    if isinstance(unknowns, list) and unknowns:
        details.append(f"{len(unknowns)} 项待确认未知项")

    summary = f"{prefix}（{'，'.join(details)}）。" if details else f"{prefix}。"
    if val_errors:
        summary += f" 校验提示 {len(val_errors)} 条，详见 Debug 面板。"
    return summary


@dataclass
class ConnectionTestResult:
    ok: bool
    message: str
    latency_ms: int = 0
    status_code: int | None = None
    effective_base_url: str = ""
    effective_model: str = ""
    json_mode_supported: bool | None = None
    available_models: list[str] = field(default_factory=list)
    error_code: str | None = None


def provider_status(exc: APIStatusError, model: str) -> tuple[str, str]:
    status = exc.status_code
    details = f"{exc.message} {getattr(exc, 'body', '')}".casefold()
    if status in {403, 404} and re.search(
        r"model_not_found|model_access|model.{0,70}(?:access|permission|not found|not authorized|not available)|"
        r"(?:access|permission).{0,70}model|模型.{0,30}(?:权限|授权|不存在)", details,
    ):
        return "model_access_denied", f"模型 {model} 无访问权限或不可用 (HTTP {status})。请核对模型名称和项目授权。{exc.message}"
    if status == 403:
        return "provider_forbidden", f"服务拒绝请求 (HTTP 403)。请检查项目、组织、区域或代理权限策略。{exc.message}"
    if status >= 500 or status == 429:
        return "service_unavailable", f"模型服务暂不可用 (HTTP {status})。请检查服务状态或配额。{exc.message}"
    return "provider_error", f"接口返回异常 (HTTP {status})。{exc.message}"


async def list_available_models(
    base_url: str,
    api_key: str,
    timeout_seconds: int = 15,
) -> tuple[list[str], str | None]:
    """Fetch available model IDs from an OpenAI-compatible /models endpoint."""
    eff_url = normalize_base_url(base_url) or "https://api.openai.com/v1"
    if eff_url.startswith("mock://"):
        return ["mock-demo"], None
    if not api_key.strip():
        return [], "API Key 未配置：无法拉取模型列表，请先填写 API Key 或配置 backend/.env。"

    client = AsyncOpenAI(
        api_key=api_key.strip(),
        base_url=eff_url,
        timeout=float(timeout_seconds),
    )
    try:
        resp = await client.models.list()
        ids = sorted({m.id for m in getattr(resp, "data", []) if getattr(m, "id", None)})
        return ids[:200], None
    except AuthenticationError as exc:
        return [], redact(f"鉴权失败 (HTTP 401)：请检查 API Key 是否正确。详情：{exc}", api_key)
    except APITimeoutError as exc:
        return [], redact(f"请求超时（>{timeout_seconds}s）：请检查网络连通性。详情：{exc}", api_key)
    except APIConnectionError as exc:
        return [], redact(f"无法连接到服务 ({safe_endpoint(eff_url)})：请检查 Base URL 是否正确。详情：{exc}", api_key)
    except APIStatusError as exc:
        return [], redact(provider_status(exc, "")[1], api_key)
    except Exception as exc:
        return [], redact(f"拉取模型列表失败 ({type(exc).__name__})：{exc}", api_key)
    finally:
        await client.close()


async def test_llm_connection(
    base_url: str,
    api_key: str,
    model: str,
    json_mode: bool = True,
    timeout_seconds: int = 20,
) -> ConnectionTestResult:
    """Probe OpenAI-compatible endpoint connectivity, auth, model access, and JSON mode."""
    eff_url = normalize_base_url(base_url) or "https://api.openai.com/v1"
    eff_model = (model or "").strip() or "gpt-4o-mini"

    if eff_url.startswith("mock://"):
        return ConnectionTestResult(
            ok=True,
            message="离线 Mock 模式已就绪（不会发起外部网络请求）。",
            latency_ms=0,
            effective_base_url=eff_url,
            effective_model=eff_model,
            json_mode_supported=True,
            available_models=["mock-demo"],
        )

    if not api_key.strip():
        return ConnectionTestResult(
            ok=False,
            error_code="unconfigured",
            message="API Key 未配置：请填写自定义 API Key，或在 backend/.env 中配置 DEFAULT_LLM_API_KEY。",
            effective_base_url=eff_url,
            effective_model=eff_model,
        )

    started = time.perf_counter()
    available_models, _ = await list_available_models(
        base_url=eff_url,
        api_key=api_key,
        timeout_seconds=min(timeout_seconds, 10),
    )

    client = AsyncOpenAI(
        api_key=api_key.strip(),
        base_url=eff_url,
        timeout=float(timeout_seconds),
    )

    test_prompt = (
        'Reply with a valid json object: {"status": "ok"}'
        if json_mode
        else "Reply with OK"
    )
    kwargs: dict[str, Any] = {
        "model": eff_model,
        "messages": [{"role": "user", "content": test_prompt}],
        "max_tokens": 16,
        "temperature": 0,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    json_supported: bool | None = True if json_mode else None
    try:
        try:
            await client.chat.completions.create(**kwargs)
        except APIStatusError as exc:
            if json_mode and _is_response_format_error(exc):
                kwargs.pop("response_format", None)
                json_supported = False
                await client.chat.completions.create(**kwargs)
            else:
                raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        msg = f"连接成功！模型 `{eff_model}` 响应正常（耗时 {latency_ms} ms）。"
        if json_mode and json_supported is False:
            msg += " 注意：该模型/代理不支持 response_format=json_object，运行时将自动降级为提示词约束。"
        return ConnectionTestResult(
            ok=True,
            message=msg,
            latency_ms=latency_ms,
            status_code=200,
            effective_base_url=eff_url,
            effective_model=eff_model,
            json_mode_supported=json_supported,
            available_models=available_models,
        )
    except AuthenticationError as exc:
        return ConnectionTestResult(
            ok=False,
            error_code="authentication_failed",
            message=redact(f"鉴权失败 (HTTP 401)：请检查 API Key 是否有效或与该 Base URL 匹配。详情：{exc}", api_key),
            latency_ms=int((time.perf_counter() - started) * 1000),
            status_code=401,
            effective_base_url=eff_url,
            effective_model=eff_model,
            available_models=available_models,
        )
    except APITimeoutError as exc:
        return ConnectionTestResult(
            ok=False,
            error_code="service_unavailable",
            message=redact(f"连接超时（超过 {timeout_seconds}s）：请检查网络代理或 Base URL 是否可达。详情：{exc}", api_key),
            latency_ms=int((time.perf_counter() - started) * 1000),
            effective_base_url=eff_url,
            effective_model=eff_model,
            available_models=available_models,
        )
    except APIConnectionError as exc:
        return ConnectionTestResult(
            ok=False,
            error_code="service_unavailable",
            message=redact(f"无法连接到服务 ({safe_endpoint(eff_url)})：请检查 Base URL 格式与网络连通性。详情：{exc}", api_key),
            latency_ms=int((time.perf_counter() - started) * 1000),
            effective_base_url=eff_url,
            effective_model=eff_model,
            available_models=available_models,
        )
    except APIStatusError as exc:
        hint = ""
        if available_models and eff_model not in available_models:
            sample = ", ".join(available_models[:5])
            hint = f" 当前 Key 可用模型示例：{sample}"
        code, message = provider_status(exc, eff_model)
        return ConnectionTestResult(
            ok=False,
            error_code=code,
            message=redact(message + hint, api_key),
            latency_ms=int((time.perf_counter() - started) * 1000),
            status_code=exc.status_code,
            effective_base_url=eff_url,
            effective_model=eff_model,
            available_models=available_models,
        )
    except Exception as exc:
        return ConnectionTestResult(
            ok=False,
            error_code="provider_error",
            message=redact(f"连接测试发生未预期错误 ({type(exc).__name__})：{exc}", api_key),
            latency_ms=int((time.perf_counter() - started) * 1000),
            effective_base_url=eff_url,
            effective_model=eff_model,
            available_models=available_models,
        )
    finally:
        await client.close()
