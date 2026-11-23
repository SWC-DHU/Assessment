from pathlib import Path

from domain_semantics.sources import Document, file_document

from .parser import parse_file_bytes


def uploaded_document(file) -> Document:
    preview = file.parsed_preview
    if any(sheet.get("truncated_preview") for sheet in preview.get("sheets", [])):
        try:
            raw = Path(file.stored_path).read_bytes()
            expanded = parse_file_bytes(raw, file.filename, row_limit=None)
            if expanded.status == "success" and expanded.parsed_text == file.parsed_text:
                preview = expanded.parsed_preview
        except OSError:
            pass
    return file_document(file.id, file.filename, file.parsed_text, preview)
