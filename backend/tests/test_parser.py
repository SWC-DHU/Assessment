import io
import openpyxl
from docx import Document as DocxDocument

from app.services.parser import parse_file_bytes


def test_parse_txt_and_markdown():
    res = parse_file_bytes("产品：电机壳体\n节拍：60秒".encode("utf-8"), "spec.txt")
    assert res.status == "success"
    assert "[L1] 产品：电机壳体" in res.parsed_text
    assert "[L2] 节拍：60秒" in res.parsed_text
    assert res.parsed_preview["kind"] == "text"
    assert res.parsed_preview["stats"]["line_count"] == 2


def test_parse_csv_with_coordinates():
    csv_bytes = "工序号,工序名称,工时\nOP10,压铸,42\n".encode("utf-8")
    res = parse_file_bytes(csv_bytes, "route.csv")
    assert res.status == "success"
    assert "[Row 2] A2=OP10 | B2=压铸 | C2=42" in res.parsed_text
    assert res.parsed_preview["kind"] == "sheets"


def test_parse_xlsx_extracts_sheet_and_cell_coords():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "工艺路线"
    ws.append(["工序号", "工序名称", "工时(秒)"])
    ws.append(["OP10", "高压压铸", 42])
    ws2 = wb.create_sheet("物料清单")
    ws2.append(["物料", "规格"])
    ws2.append(["ADC12", "3.8kg"])

    buf = io.BytesIO()
    wb.save(buf)
    wb.close()

    res = parse_file_bytes(buf.getvalue(), "process.xlsx")
    assert res.status == "success"
    assert "### Sheet: 工艺路线" in res.parsed_text
    assert "工艺路线!A2=OP10" in res.parsed_text
    assert "工艺路线!B2=高压压铸" in res.parsed_text
    assert "物料清单!A2=ADC12" in res.parsed_text
    assert res.parsed_preview["stats"]["sheet_count"] == 2


def test_parse_docx_paragraphs_and_tables():
    doc = DocxDocument()
    doc.add_paragraph("电机壳体工艺需求说明书")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "工序号"
    table.cell(0, 1).text = "设备"
    table.cell(1, 0).text = "OP30"
    table.cell(1, 1).text = "五轴CNC"

    buf = io.BytesIO()
    doc.save(buf)

    res = parse_file_bytes(buf.getvalue(), "spec.docx")
    assert res.status == "success"
    assert "[P1] 电机壳体工艺需求说明书" in res.parsed_text
    assert "T1!R2C1=OP30" in res.parsed_text
    assert "T1!R2C2=五轴CNC" in res.parsed_text


def test_parse_unsupported_and_corrupted_files():
    bad_ext = parse_file_bytes(b"hello", "archive.zip")
    assert bad_ext.status == "error"
    assert "不支持的文件格式" in (bad_ext.error or "")

    corrupt_xlsx = parse_file_bytes(b"not-an-xlsx-binary", "broken.xlsx")
    assert corrupt_xlsx.status == "error"
    assert "文件解析失败" in (corrupt_xlsx.error or "")


def test_parse_pdf_preserves_page_and_line():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }),
        }),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 170 Td (Operation 01) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    target = io.BytesIO()
    writer.write(target)
    result = parse_file_bytes(target.getvalue(), "fixture.pdf")
    assert result.status == "success"
    assert result.parsed_preview["pages"][0]["lines"] == [{"coord": "P1:L1", "text": "Operation 01"}]
