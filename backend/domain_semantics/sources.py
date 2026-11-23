"""Immutable source fragments and exact-match evidence lookup."""
from dataclasses import dataclass, field
from hashlib import sha256
import re

from .schema import Evidence, EvidenceMethod, Location, SourceRef


def digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Fragment:
    document_id: str
    key: str
    text: str
    location: Location

    def evidence(self, batch: str, quote: str | None = None, method: EvidenceMethod = "structured") -> Evidence:
        quote = self.text if quote is None else quote
        start = self.text.index(quote)
        return Evidence(
            document_id=self.document_id, fragment_id=self.key, quote=quote,
            location=self.location.model_copy(update={"start": start, "end": start + len(quote)}),
            extraction_batch=batch, method=method, status="verified",
        )


@dataclass
class Document:
    ref: SourceRef
    fragments: list[Fragment] = field(default_factory=list)
    sheets: dict[str, list[list[Fragment]]] = field(default_factory=dict)


def message_document(message_id: str, text: str, kind: str = "message") -> Document:
    ref = SourceRef(document_id=message_id, name="用户消息" if kind == "message" else "离线合成样例",
                    kind=kind, content_sha256=digest(text))
    return Document(ref, [Fragment(message_id, "message", text, Location(message_id=message_id))])


def file_document(document_id: str, name: str, text: str, preview: dict) -> Document:
    ref = SourceRef(document_id=document_id, name=name, kind="file", content_sha256=digest(text))
    doc = Document(ref)

    def add(key: str, quote: str, **position) -> Fragment:
        fragment = Fragment(document_id, key, quote, Location(**position))
        if quote:
            doc.fragments.append(fragment)
        return fragment

    def table(sheet: dict) -> None:
        label = sheet["name"]
        rows = []
        for index, row in enumerate(sheet["rows"], 1):
            fragments = []
            for cell in row:
                coord = cell["coord"]
                row_no = re.search(r"(?:^R|^[A-Z]+)(\d+)", coord)
                fragments.append(add(f"{label}!{coord}", cell["value"], sheet=label, cell=coord,
                                     row=int(row_no[1]) if row_no else index))
            rows.append(fragments)
        doc.sheets[label] = rows

    kind = preview.get("kind")
    if kind == "sheets":
        for sheet in preview["sheets"]:
            table(sheet)
    elif kind == "pdf":
        for page in preview["pages"]:
            for index, line in enumerate(page["lines"], 1):
                add(line["coord"], line["text"], page=page["page_no"], line=index)
    elif kind == "document":
        for section in preview["sections"]:
            for item in section.get("items", []):
                add(item["coord"], item["text"], paragraph=int(item["coord"][1:]))
            for sheet in section.get("tables", []):
                table(sheet)
    elif kind == "text":
        for line in text.splitlines():
            match = re.fullmatch(r"\[L(\d+)\] (.*)", line)
            if match:
                add(f"L{match[1]}", match[2], line=int(match[1]))
    return doc


def locate(documents: list[Document], quote: object, batch: str, *,
           document_id: str | None = None, fragment_id: str | None = None, method: EvidenceMethod = "llm") -> list[Evidence]:
    if not isinstance(quote, str) or not quote.strip():
        return []
    hits = [
        fragment for document in documents if not document_id or document.ref.document_id == document_id
        for fragment in document.fragments if (not fragment_id or fragment.key == fragment_id)
        and quote in fragment.text
    ]
    return [hits[0].evidence(batch, quote, method)] if len(hits) == 1 else []
