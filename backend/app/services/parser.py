"""Standalone, framework-agnostic industrial file parser.

Supports: .txt, .md, .csv, .xlsx, .docx, .pdf
Outputs LLM-ready coordinate-annotated text and structured preview metadata.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import io
import re
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter
import pdfplumber
from pypdf import PdfReader
from docx import Document as DocxDocument


SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".xlsx", ".docx", ".pdf"}


@dataclass
class ParseResult:
    status: str  # "success" | "error"
    filename: str
    file_ext: str
    parsed_text: str = ""
    parsed_preview: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_text_or_markdown(raw: bytes, filename: str, ext: str) -> ParseResult:
    text = _decode_bytes(raw)
    lines = text.splitlines()
    numbered_lines = [f"[L{idx}] {line}" for idx, line in enumerate(lines, start=1)]
    parsed_text = "\n".join(numbered_lines) if numbered_lines else "(空文件)"
    preview = {
        "kind": "text",
        "lines": [{"line_no": idx, "text": line} for idx, line in enumerate(lines[:1000], start=1)],
        "stats": {
            "char_count": len(text),
            "line_count": len(lines),
            "truncated_preview": len(lines) > 1000,
        },
    }
    return ParseResult(
        status="success",
        filename=filename,
        file_ext=ext,
        parsed_text=parsed_text,
        parsed_preview=preview,
    )


def _parse_csv(raw: bytes, filename: str, row_limit: int | None = 500) -> ParseResult:
    text = _decode_bytes(raw)
    sample = text[:4096]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)

    formatted_lines: list[str] = []
    preview_rows: list[list[dict[str, str]]] = []
    for r_idx, row in enumerate(rows, start=1):
        cell_parts = []
        row_cells = []
        for c_idx, val in enumerate(row, start=1):
            col_letter = get_column_letter(c_idx)
            coord = f"{col_letter}{r_idx}"
            val_str = str(val)
            row_cells.append({"coord": coord, "value": val_str})
            if val_str.strip():
                cell_parts.append(f"{coord}={val_str}")
        preview_rows.append(row_cells)
        if cell_parts:
            formatted_lines.append(f"[Row {r_idx}] " + " | ".join(cell_parts))

    parsed_text = "\n".join(formatted_lines) if formatted_lines else "(空 CSV 文件)"
    preview = {
        "kind": "sheets",
        "sheets": [
            {
                "name": "CSV",
                "total_rows": len(rows),
                "rows": preview_rows[:row_limit],
                "truncated_preview": row_limit is not None and len(rows) > row_limit,
            }
        ],
        "stats": {
            "char_count": len(parsed_text),
            "row_count": len(rows),
            "sheet_count": 1,
        },
    }
    return ParseResult(
        status="success",
        filename=filename,
        file_ext=".csv",
        parsed_text=parsed_text,
        parsed_preview=preview,
    )


def _parse_xlsx(raw: bytes, filename: str, row_limit: int | None = 500) -> ParseResult:
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    try:
        all_text_sections: list[str] = []
        sheets_preview: list[dict[str, Any]] = []
        total_rows = 0

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            sheet_lines: list[str] = [f"### Sheet: {sheet_name}"]
            preview_rows: list[list[dict[str, str]]] = []
            sheet_row_count = 0

            for r_idx, row in enumerate(ws.iter_rows(), start=1):
                sheet_row_count += 1
                total_rows += 1
                cell_parts: list[str] = []
                row_cells: list[dict[str, str]] = []
                has_non_empty = False
                for c_idx, cell in enumerate(row, start=1):
                    val = cell.value
                    col_letter = get_column_letter(c_idx)
                    coord = f"{sheet_name}!{col_letter}{r_idx}"
                    val_str = "" if val is None else str(val)
                    number_format = getattr(cell, "number_format", None)
                    if isinstance(val, int) and not isinstance(val, bool) and number_format and re.fullmatch(r"0{2,}", number_format):
                        val_str = f"{val:0{len(number_format)}d}"
                    if val_str.strip():
                        has_non_empty = True
                        cell_parts.append(f"{coord}={val_str}")
                    row_cells.append({"coord": f"{col_letter}{r_idx}", "value": val_str})

                if has_non_empty:
                    sheet_lines.append(" | ".join(cell_parts))
                    if row_limit is None or len(preview_rows) < row_limit:
                        preview_rows.append(row_cells)

            if len(sheet_lines) == 1:
                sheet_lines.append("(空工作表)")
            all_text_sections.append("\n".join(sheet_lines))
            sheets_preview.append(
                {
                    "name": sheet_name,
                    "total_rows": sheet_row_count,
                    "rows": preview_rows,
                    "truncated_preview": row_limit is not None and sheet_row_count > row_limit,
                }
            )

        parsed_text = "\n\n".join(all_text_sections)
        preview = {
            "kind": "sheets",
            "sheets": sheets_preview,
            "stats": {
                "char_count": len(parsed_text),
                "sheet_count": len(wb.sheetnames),
                "row_count": total_rows,
            },
        }
        return ParseResult(
            status="success",
            filename=filename,
            file_ext=".xlsx",
            parsed_text=parsed_text,
            parsed_preview=preview,
        )
    finally:
        wb.close()


def _parse_docx(raw: bytes, filename: str) -> ParseResult:
    doc = DocxDocument(io.BytesIO(raw))
    lines: list[str] = []
    sections: list[dict[str, Any]] = []

    para_items: list[dict[str, Any]] = []
    for idx, para in enumerate(doc.paragraphs, start=1):
        text = para.text.strip()
        if text:
            style_name = para.style.name if para.style else ""
            lines.append(f"[P{idx}] {text}")
            para_items.append({"coord": f"P{idx}", "style": style_name, "text": text})

    if para_items:
        sections.append({"label": "正文段落", "type": "paragraphs", "items": para_items})

    tables_preview: list[dict[str, Any]] = []
    for t_idx, table in enumerate(doc.tables, start=1):
        lines.append(f"\n### Table {t_idx}")
        t_rows: list[list[dict[str, str]]] = []
        for r_idx, row in enumerate(table.rows, start=1):
            row_cells: list[dict[str, str]] = []
            parts: list[str] = []
            for c_idx, cell in enumerate(row.cells, start=1):
                cell_text = " ".join(cell.text.strip().splitlines())
                coord = f"T{t_idx}!R{r_idx}C{c_idx}"
                row_cells.append({"coord": f"R{r_idx}C{c_idx}", "value": cell_text})
                if cell_text:
                    parts.append(f"{coord}={cell_text}")
            t_rows.append(row_cells)
            if parts:
                lines.append(" | ".join(parts))
        tables_preview.append({"name": f"Table {t_idx}", "rows": t_rows})

    if tables_preview:
        sections.append({"label": "文档表格", "type": "tables", "tables": tables_preview})

    parsed_text = "\n".join(lines) if lines else "(空 Word 文档)"
    preview = {
        "kind": "document",
        "sections": sections,
        "stats": {
            "char_count": len(parsed_text),
            "paragraph_count": len(para_items),
            "table_count": len(tables_preview),
        },
    }
    return ParseResult(
        status="success",
        filename=filename,
        file_ext=".docx",
        parsed_text=parsed_text,
        parsed_preview=preview,
    )


def _parse_pdf(raw: bytes, filename: str) -> ParseResult:
    pages_data: list[dict[str, Any]] = []
    formatted_pages: list[str] = []

    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for p_idx, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                if not page_text.strip():
                    # Fallback to pypdf for single page if pdfplumber returned empty
                    try:
                        reader = PdfReader(io.BytesIO(raw))
                        if p_idx - 1 < len(reader.pages):
                            page_text = reader.pages[p_idx - 1].extract_text() or ""
                    except Exception:
                        pass

                page_lines = [line for line in page_text.splitlines() if line.strip()]
                numbered = [f"[Page {p_idx}:L{l_idx}] {line}" for l_idx, line in enumerate(page_lines, start=1)]
                formatted_pages.append(f"### Page {p_idx}\n" + ("\n".join(numbered) if numbered else "(该页无文本)"))
                pages_data.append(
                    {
                        "page_no": p_idx,
                        "lines": [
                            {"coord": f"P{p_idx}:L{l_idx}", "text": line}
                            for l_idx, line in enumerate(page_lines, start=1)
                        ],
                    }
                )
    except Exception:
        pages_data.clear()
        formatted_pages.clear()
        # Full fallback to pypdf
        reader = PdfReader(io.BytesIO(raw))
        for p_idx, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            page_lines = [line for line in page_text.splitlines() if line.strip()]
            numbered = [f"[Page {p_idx}:L{l_idx}] {line}" for l_idx, line in enumerate(page_lines, start=1)]
            formatted_pages.append(f"### Page {p_idx}\n" + ("\n".join(numbered) if numbered else "(该页无文本)"))
            pages_data.append(
                {
                    "page_no": p_idx,
                    "lines": [
                        {"coord": f"P{p_idx}:L{l_idx}", "text": line}
                        for l_idx, line in enumerate(page_lines, start=1)
                    ],
                }
            )

    parsed_text = "\n\n".join(formatted_pages) if formatted_pages else "(空 PDF 文档)"
    preview = {
        "kind": "pdf",
        "pages": pages_data,
        "stats": {
            "char_count": len(parsed_text),
            "page_count": len(pages_data),
        },
    }
    return ParseResult(
        status="success",
        filename=filename,
        file_ext=".pdf",
        parsed_text=parsed_text,
        parsed_preview=preview,
    )


def parse_file_bytes(raw: bytes, filename: str, *, row_limit: int | None = 500) -> ParseResult:
    """Parse raw file bytes based on file extension."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return ParseResult(
            status="error",
            filename=filename,
            file_ext=ext,
            error=f"不支持的文件格式 '{ext or '无后缀'}'，支持格式：{', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    try:
        if ext in {".txt", ".md"}:
            return _parse_text_or_markdown(raw, filename, ext)
        if ext == ".csv":
            return _parse_csv(raw, filename, row_limit)
        if ext == ".xlsx":
            return _parse_xlsx(raw, filename, row_limit)
        if ext == ".docx":
            return _parse_docx(raw, filename)
        if ext == ".pdf":
            return _parse_pdf(raw, filename)
    except Exception as exc:
        return ParseResult(
            status="error",
            filename=filename,
            file_ext=ext,
            error=f"文件解析失败 ({filename}): {type(exc).__name__}: {exc}",
        )

    return ParseResult(
        status="error",
        filename=filename,
        file_ext=ext,
        error=f"未知文件格式: {ext}",
    )


def parse_file_path(file_path: str | Path, *, row_limit: int | None = 500) -> ParseResult:
    """Parse a file from disk."""
    path = Path(file_path)
    if not path.exists():
        return ParseResult(
            status="error",
            filename=path.name,
            file_ext=path.suffix.lower(),
            error=f"文件不存在: {path}",
        )
    return parse_file_bytes(path.read_bytes(), path.name, row_limit=row_limit)
