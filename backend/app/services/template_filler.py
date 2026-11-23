"""Standalone, framework-agnostic Template Filling & Export Engine.

Supports:
1. Jinja2 text / Markdown / HTML rendering
2. Structured Table Mapping rendering & Excel (.xlsx) / Word (.docx) / Markdown / JSON export
"""

from __future__ import annotations

import io
import json
import re
from typing import Any

from docx import Document as DocxDocument
from docx.shared import Pt, RGBColor
from jinja2 import ChainableUndefined, Environment, TemplateError
from jinja2.sandbox import SandboxedEnvironment
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.models.schemas import TemplatePreviewResponse


def _format_value(val: Any, fallback: str = "-") -> str:
    if val is None or val == "":
        return fallback
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    if isinstance(val, bool):
        return "是" if val else "否"
    if isinstance(val, list):
        if not val:
            return fallback
        parts: list[str] = []
        for item in val:
            if isinstance(item, dict):
                # Prefer common human-readable keys if present
                label = (
                    item.get("name")
                    or item.get("item")
                    or item.get("description")
                    or item.get("seq")
                    or json.dumps(item, ensure_ascii=False)
                )
                parts.append(str(label))
            else:
                parts.append(str(item))
        return "、".join(parts)
    if isinstance(val, dict):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def _join_items_filter(val: Any, sep: str = "、", fallback: str = "-") -> str:
    if not val:
        return fallback
    if isinstance(val, list):
        return sep.join(_format_value(v, "") for v in val if v is not None and v != "") or fallback
    return _format_value(val, fallback)


def _tojson_filter(val: Any, indent: int = 2) -> str:
    return json.dumps(val, ensure_ascii=False, indent=indent)


def _create_jinja_env() -> Environment:
    env = SandboxedEnvironment(undefined=ChainableUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters["join_items"] = _join_items_filter
    env.filters["tojson"] = _tojson_filter
    env.filters["fmt"] = _format_value
    return env


def _resolve_dot_path(obj: Any, path: str) -> Any:
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def _resolve_cell_value(row: Any, root: dict[str, Any], path: str, fallback: str = "-") -> str:
    path = (path or "").strip()
    if not path:
        return fallback
    if path.startswith("$root."):
        val = _resolve_dot_path(root, path[6:])
    else:
        val = _resolve_dot_path(row, path)
        if val is None and isinstance(row, dict) and path in root:
            val = _resolve_dot_path(root, path)
    return _format_value(val, fallback=fallback)


def render_template(
    template_id: str,
    template_type: str,
    template_format: str,
    jinja_content: str,
    table_config: dict[str, Any],
    data: dict[str, Any],
) -> TemplatePreviewResponse:
    """Render either a Jinja2 template or a Table Mapping template with extracted JSON data."""
    safe_data = data if isinstance(data, dict) else {}

    if template_type == "table_mapping":
        row_source = (table_config.get("row_source_path") or "").strip()
        columns = table_config.get("columns") or []
        headers = [str(col.get("header") or col.get("path") or "") for col in columns]

        if row_source:
            raw_rows = _resolve_dot_path(safe_data, row_source)
            if not isinstance(raw_rows, list):
                raw_rows = []
        else:
            raw_rows = [safe_data] if safe_data else []

        table_rows: list[list[str]] = []
        cell_states: list[list[str]] = []
        table_values: list[list[str | float | int]] = []
        row_ids: list[str | None] = []
        semantic = isinstance(safe_data.get("semantic"), dict)
        mapping_issues = []
        if row_source and not isinstance(_resolve_dot_path(safe_data, row_source), list):
            mapping_issues.append(f"行来源字段不存在或非数组：{row_source}")
        labels = {"source_missing": "原文未提供", "not_extracted": "尚未抽取",
                  "bad_path": "映射字段不存在", "unverified": "数据未通过核验"}
        for row_item in raw_rows:
            row_ids.append(row_item.get("id") if isinstance(row_item, dict) and isinstance(row_item.get("id"), str) else None)
            row_vals, states, values = [], [], []
            for col in columns:
                path = str(col.get("path") or "")
                header = str(col.get("header") or "")
                if semantic and path == "cycle_time_sec" and "工时" in header:
                    path = "process_time_sec"
                fallback = str(col.get("fallback") or "-")
                if not semantic:
                    row_vals.append(_resolve_cell_value(row_item, safe_data, path, fallback))
                    states.append("value")
                    values.append(row_vals[-1])
                    continue
                root = safe_data if path.startswith("$root.") else row_item
                if isinstance(row_item, dict) and path not in row_item and path in safe_data:
                    root = safe_data
                local_path = path[6:] if path.startswith("$root.") else path
                obj = root
                state = "bad_path"
                for component in local_path.split("."):
                    if isinstance(obj, dict) and component in obj:
                        obj = obj[component]
                        state = "value"
                    elif isinstance(obj, list) and component.isdigit() and int(component) < len(obj):
                        obj = obj[int(component)]
                        state = "value"
                    else:
                        state = "bad_path"
                        break
                explicit_empty = (local_path == "predecessors" and obj == [] and isinstance(root, dict)
                                  and root.get("_explicit_no_predecessor"))
                if state == "value" and not explicit_empty and (obj is None or obj == "" or obj == []):
                    state = (root.get("_cell_states", {}).get(local_path, "source_missing")
                             if isinstance(root, dict) else "source_missing")
                row_vals.append(("无" if explicit_empty else _format_value(obj, fallback)) if state == "value" else labels[state])
                values.append(obj if state == "value" and type(obj) in {float, int} else row_vals[-1])
                if state == "bad_path" and path not in mapping_issues:
                    mapping_issues.append(path)
                states.append(state)
            table_rows.append(row_vals)
            cell_states.append(states)
            table_values.append(values)

        md_lines: list[str] = []
        if headers:
            md_lines.append("| " + " | ".join(headers) + " |")
            md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for r in table_rows:
                escaped = [c.replace("\n", " ").replace("|", "\\|") for c in r]
                md_lines.append("| " + " | ".join(escaped) + " |")
        else:
            md_lines.append("（尚未配置表格映射列）")

        return TemplatePreviewResponse(
            template_id=template_id,
            template_type=template_type,
            format=template_format,
            rendered_text="\n".join(md_lines),
            table_headers=headers,
            table_rows=table_rows,
            cell_states=cell_states,
            table_values=table_values,
            row_ids=row_ids,
            mapping_issues=mapping_issues,
        )

    # Default: Jinja2 rendering
    env = _create_jinja_env()
    try:
        tmpl = env.from_string(jinja_content or "")
        context = safe_data
        if safe_data.get("semantic") and "单件工时" in jinja_content:
            context = {**safe_data, "operations": [
                {**op, "cycle_time_sec": op["process_time_sec"] if op.get("process_time_sec") is not None
                 else "原文未提供", "equipment": op.get("equipment") or "原文未提供"}
                for op in safe_data.get("operations", [])
            ]}
        rendered = tmpl.render(**context, _data=context)
        return TemplatePreviewResponse(
            template_id=template_id,
            template_type=template_type,
            format=template_format,
            rendered_text=rendered,
        )
    except TemplateError as exc:
        return TemplatePreviewResponse(
            template_id=template_id,
            template_type=template_type,
            format=template_format,
            rendered_text="",
            error=f"Jinja2 模板语法或渲染错误 (line {getattr(exc, 'lineno', '?')}): {exc}",
        )
    except Exception as exc:
        return TemplatePreviewResponse(
            template_id=template_id,
            template_type=template_type,
            format=template_format,
            rendered_text="",
            error=f"模板渲染失败: {type(exc).__name__}: {exc}",
        )


def _style_header_row(ws: Any, row_idx: int, col_count: int) -> None:
    fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    thin = Side(border_style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c_idx in range(1, col_count + 1):
        cell = ws.cell(row=row_idx, column=c_idx)
        cell.fill = fill
        cell.font = font
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _auto_fit_sheet(ws: Any) -> None:
    thin = Side(border_style="thin", color="E2E8F0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for col in ws.columns:
        max_len = 10
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if cell.row > 1:
                cell.border = border
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            for line in val_str.splitlines() or [""]:
                # Approximate wide CJK characters as width 2
                w = sum(2 if ord(ch) > 127 else 1 for ch in line)
                if w > max_len:
                    max_len = w
        ws.column_dimensions[col_letter].width = min(max_len + 4, 52)


def export_to_xlsx(
    template_name: str,
    template_type: str,
    jinja_content: str,
    table_config: dict[str, Any],
    data: dict[str, Any],
) -> bytes:
    """Export rendered template or structured JSON into a styled .xlsx workbook."""
    wb = openpyxl.Workbook()
    ws_main = wb.active
    safe_data = data if isinstance(data, dict) else {}

    preview = render_template(
        template_id="",
        template_type=template_type,
        template_format="table" if template_type == "table_mapping" else "markdown",
        jinja_content=jinja_content,
        table_config=table_config,
        data=safe_data,
    )

    if template_type == "table_mapping" and preview.table_headers:
        ws_main.title = (template_name[:28] or "明细汇总").replace("/", "-")
        ws_main.append(preview.table_headers)
        _style_header_row(ws_main, 1, len(preview.table_headers))
        for row in preview.table_values or preview.table_rows:
            ws_main.append(row)
        ws_main.freeze_panes = "A2"
        _auto_fit_sheet(ws_main)

        if table_config.get("include_summary_sheet", True):
            ws_sum = wb.create_sheet(title="基础信息与约束")
            ws_sum.append(["字段", "内容"])
            _style_header_row(ws_sum, 1, 2)
            for k, v in safe_data.items():
                if k != "semantic" and not isinstance(v, list):
                    ws_sum.append([str(k), _format_value(v)])
            for list_key in ("constraints", "unknown_items"):
                items = safe_data.get(list_key)
                if isinstance(items, list):
                    for idx, item in enumerate(items, start=1):
                        ws_sum.append([f"{list_key}[{idx}]", _format_value(item)])
            _auto_fit_sheet(ws_sum)
    else:
        # Export structured sheets from extracted JSON + rendered text sheet
        ws_main.title = "概览信息"
        ws_main.append(["字段", "抽取内容"])
        _style_header_row(ws_main, 1, 2)
        list_keys: list[tuple[str, list[Any]]] = []
        for k, v in safe_data.items():
            if isinstance(v, list):
                list_keys.append((k, v))
            else:
                ws_main.append([str(k), _format_value(v)])
        _auto_fit_sheet(ws_main)

        for key, items in list_keys:
            sheet_title = key[:28].replace("/", "-") or "列表"
            ws_list = wb.create_sheet(title=sheet_title)
            if items and all(isinstance(i, dict) for i in items):
                cols: list[str] = []
                for item in items:
                    for ck in item.keys():
                        if ck not in cols:
                            cols.append(ck)
                ws_list.append(cols)
                _style_header_row(ws_list, 1, len(cols))
                for item in items:
                    ws_list.append([_format_value(item.get(c)) for c in cols])
                ws_list.freeze_panes = "A2"
            else:
                ws_list.append([key])
                _style_header_row(ws_list, 1, 1)
                for item in items:
                    ws_list.append([_format_value(item)])
            _auto_fit_sheet(ws_list)

        if preview.rendered_text:
            ws_doc = wb.create_sheet(title="模板渲染文本")
            ws_doc.append(["行号", "内容"])
            _style_header_row(ws_doc, 1, 2)
            for idx, line in enumerate(preview.rendered_text.splitlines(), start=1):
                ws_doc.append([idx, line])
            _auto_fit_sheet(ws_doc)

    for sheet in wb:
        for cell in (cell for row in sheet for cell in row if cell.data_type == "f"):
            cell.data_type = "s"
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _add_word_table(doc: DocxDocument, headers: list[str], rows: list[list[str]]) -> None:
    if not headers:
        return
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    for idx, h in enumerate(headers):
        hdr_cells[idx].text = h
        for p in hdr_cells[idx].paragraphs:
            for run in p.runs:
                run.bold = True
    for r in rows:
        row_cells = table.add_row().cells
        for idx in range(len(headers)):
            row_cells[idx].text = r[idx] if idx < len(r) else ""


def export_to_docx(
    template_name: str,
    template_type: str,
    jinja_content: str,
    table_config: dict[str, Any],
    data: dict[str, Any],
) -> bytes:
    """Export rendered template into a cleanly formatted Word (.docx) document."""
    doc = DocxDocument()
    safe_data = data if isinstance(data, dict) else {}

    title_para = doc.add_heading(template_name or "工艺需求解析输出文档", level=0)
    for run in title_para.runs:
        run.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)

    preview = render_template(
        template_id="",
        template_type=template_type,
        template_format="table" if template_type == "table_mapping" else "markdown",
        jinja_content=jinja_content,
        table_config=table_config,
        data=safe_data,
    )

    if template_type == "table_mapping":
        # Add summary metadata first
        scalar_rows = [[str(k), _format_value(v)] for k, v in safe_data.items() if not isinstance(v, list)]
        if scalar_rows:
            doc.add_heading("基础信息", level=1)
            _add_word_table(doc, ["字段", "内容"], scalar_rows)

        doc.add_heading("结构化明细表", level=1)
        _add_word_table(doc, preview.table_headers, preview.table_rows)
    else:
        # Parse simple Markdown structure into Word headings, tables, lists, and paragraphs
        lines = (preview.rendered_text or "").splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            stripped = line.strip()
            if not stripped:
                i += 1
                continue

            # Markdown table block
            if stripped.startswith("|") and stripped.endswith("|"):
                table_lines: list[str] = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1
                parsed_rows = [
                    [c.strip() for c in tl.strip("|").split("|")]
                    for tl in table_lines
                    if not re.match(r"^\|?[\s\-:|]+\|?$", tl)
                ]
                if parsed_rows:
                    _add_word_table(doc, parsed_rows[0], parsed_rows[1:])
                continue

            if stripped.startswith("### "):
                doc.add_heading(stripped[4:], level=3)
            elif stripped.startswith("## "):
                doc.add_heading(stripped[3:], level=2)
            elif stripped.startswith("# "):
                doc.add_heading(stripped[2:], level=1)
            elif stripped.startswith("- ") or stripped.startswith("* "):
                p = doc.add_paragraph(stripped[2:], style="List Bullet")
                p.paragraph_format.space_after = Pt(3)
            else:
                p = doc.add_paragraph(stripped)
                p.paragraph_format.space_after = Pt(4)
            i += 1

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
