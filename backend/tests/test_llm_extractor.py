import pytest

from app.services.llm_extractor import (
    ExtractorConfig,
    FileInputContext,
    build_user_content,
    extract_json_from_text,
    run_extraction,
    validate_extracted_data,
)
from app.services.seed import PROCESS_EXTRACTION_SCHEMA


def test_extract_json_fault_tolerant():
    raw_fenced = """<think>Analyzing process...</think>
Here is the structured output:
```json
{
  "product_name": "电机壳体",
  "operations": [
    {"seq": "OP10", "name": "压铸", "source_quote": "L1",}
  ],
}
```
Hope this helps!"""
    data, err = extract_json_from_text(raw_fenced)
    assert err is None
    assert data is not None
    assert data["product_name"] == "电机壳体"
    assert data["operations"][0]["seq"] == "OP10"


def test_extract_json_invalid_returns_error():
    data, err = extract_json_from_text("This response has no JSON at all.")
    assert data is None
    assert err is not None


def test_validate_extracted_data_catches_missing_required_and_quote():
    sample = {
        "operations": [
            {"seq": "OP10", "name": "压铸"}  # missing product_name and source_quote
        ]
    }
    errors = validate_extracted_data(sample, PROCESS_EXTRACTION_SCHEMA, require_source_quote=True)
    assert any("product_name" in e for e in errors)
    assert any("source_quote" in e for e in errors)


def test_build_user_content_includes_state_and_files():
    content = build_user_content(
        user_message="把第三道工序工时改为45秒",
        files=[FileInputContext(filename="a.txt", parsed_text="[L1] 测试")],
        current_state={"product_name": "电机壳体", "operations": []},
    )
    assert "=== 参考文件 1: a.txt ===" in content
    assert "当前已抽取的结构化 JSON 状态" in content
    assert "把第三道工序工时改为45秒" in content


@pytest.mark.asyncio
async def test_run_extraction_missing_api_key_returns_clear_error():
    cfg = ExtractorConfig(
        base_url="https://api.openai.com/v1",
        api_key="",
        model="gpt-4o-mini",
        extraction_schema=PROCESS_EXTRACTION_SCHEMA,
    )
    res = await run_extraction(
        config=cfg,
        user_message="解析工艺",
        files=[],
        use_demo_fallback=False,
    )
    assert res.status == "error"
    assert "API Key 未配置" in (res.error_message or "")


@pytest.mark.asyncio
async def test_run_extraction_demo_and_refinement():
    cfg = ExtractorConfig(
        base_url="mock://demo",
        api_key="",
        model="mock",
        extraction_schema=PROCESS_EXTRACTION_SCHEMA,
    )
    first = await run_extraction(
        config=cfg,
        user_message="解析电机壳体工艺",
        files=[],
    )
    assert first.status == "success"
    assert first.extracted_json is not None
    assert len(first.extracted_json["operations"]) == 4

    second = await run_extraction(
        config=cfg,
        user_message="把第三道工序的工时改成 45 秒",
        files=[],
        current_state=first.extracted_json,
    )
    assert second.status == "success"
    assert second.extracted_json is not None
    assert second.extracted_json["operations"][2]["cycle_time_sec"] == 45
