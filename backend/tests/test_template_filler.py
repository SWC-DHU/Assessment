import io
import openpyxl
from docx import Document as DocxDocument

from app.services.seed import TEMPLATE_A_JINJA, TEMPLATE_B_TABLE_CONFIG
from app.services.template_filler import export_to_docx, export_to_xlsx, render_template


SAMPLE_DATA = {
    "product_name": "电机壳体",
    "part_no": "MTR-01",
    "target_takt_sec": 60,
    "operations": [
        {
            "seq": "OP10",
            "name": "压铸成型",
            "equipment": "2500T压铸机",
            "cycle_time_sec": 42,
            "input_materials": ["ADC12铝锭", "脱模剂"],
            "output_materials": ["毛坯"],
            "predecessors": [],
            "quality_requirements": "气孔率<=2%",
            "source_quote": "Sheet1!A2",
        }
    ],
    "constraints": [{"type": "节拍", "description": "<=60s", "source_quote": "L1"}],
    "unknown_items": [{"item": "换刀时间口径", "reason": "未提及", "source_quote": "未提及"}],
}


def test_render_jinja2_markdown_template():
    res = render_template(
        template_id="t1",
        template_type="jinja2",
        template_format="markdown",
        jinja_content=TEMPLATE_A_JINJA,
        table_config={},
        data=SAMPLE_DATA,
    )
    assert res.error is None
    assert "电机壳体" in res.rendered_text
    assert "| OP10 | 压铸成型 | 2500T压铸机 | 42 | ADC12铝锭、脱模剂 |" in res.rendered_text
    assert "换刀时间口径" in res.rendered_text


def test_render_jinja2_syntax_error_handled_gracefully():
    res = render_template(
        template_id="t_bad",
        template_type="jinja2",
        template_format="markdown",
        jinja_content="{{ unclosed ",
        table_config={},
        data=SAMPLE_DATA,
    )
    assert res.error is not None
    assert "Jinja2" in res.error


def test_render_table_mapping_and_export_xlsx_docx():
    res = render_template(
        template_id="t2",
        template_type="table_mapping",
        template_format="table",
        jinja_content="",
        table_config=TEMPLATE_B_TABLE_CONFIG,
        data=SAMPLE_DATA,
    )
    assert res.error is None
    assert res.table_headers[0] == "产品名称"
    assert len(res.table_rows) == 1
    assert res.table_rows[0][0] == "电机壳体"
    assert res.table_rows[0][2] == "OP10"
    assert res.table_rows[0][5] == "42"

    xlsx_bytes = export_to_xlsx(
        template_name="产线节拍汇总",
        template_type="table_mapping",
        jinja_content="",
        table_config=TEMPLATE_B_TABLE_CONFIG,
        data=SAMPLE_DATA,
    )
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    assert "产线节拍汇总" in wb.sheetnames
    assert "基础信息与约束" in wb.sheetnames
    assert wb["产线节拍汇总"]["A2"].value == "电机壳体"
    assert wb["产线节拍汇总"]["C2"].value == "OP10"
    wb.close()

    docx_bytes = export_to_docx(
        template_name="标准工艺流程卡",
        template_type="jinja2",
        jinja_content=TEMPLATE_A_JINJA,
        table_config={},
        data=SAMPLE_DATA,
    )
    doc = DocxDocument(io.BytesIO(docx_bytes))
    assert len(doc.paragraphs) > 0
    assert len(doc.tables) == 1
