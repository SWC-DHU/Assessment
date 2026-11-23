"""Bound model requests, keep source coordinates and recover complete items after truncation."""
from collections import defaultdict
from dataclasses import dataclass
import json
import re

from domain_semantics.candidates import ExtractionCandidates
from domain_semantics.sources import Document, Fragment
from domain_semantics.structured_text import clauses

from .llm_extractor import FileInputContext

GROUPS = tuple(ExtractionCandidates.model_fields)
MAX_BATCH_CHARS = 1100
MAX_BATCH_FRAGMENTS = 8


@dataclass(frozen=True)
class ExtractionBatch:
    id: str
    scope: str
    fragments: tuple[Fragment, ...]
    attempt: int = 0

    @property
    def size(self) -> int:
        return sum(len(fragment.text) for fragment in self.fragments)

    @property
    def source_ids(self) -> list[str]:
        return list(dict.fromkeys(fragment.document_id for fragment in self.fragments))

    def context(self) -> list[FileInputContext]:
        return [FileInputContext(filename=self.scope, parsed_text=json.dumps([
            {"document_id": part.document_id, "fragment_id": part.key, "text": part.text}
            for part in self.fragments
        ], ensure_ascii=False))]

    def split(self) -> list["ExtractionBatch"]:
        if len(self.fragments) > 1:
            pivot = len(self.fragments) // 2
            return [ExtractionBatch(f"{self.id}.{suffix}", self.scope, fragments, self.attempt + 1)
                    for suffix, fragments in (("a", self.fragments[:pivot]), ("b", self.fragments[pivot:]))]
        part = self.fragments[0]
        # A long sentence or single spreadsheet cell may require subranges with the same source key.
        if len(part.text) < 380:
            return []
        pivot = len(part.text) // 2
        return [ExtractionBatch(f"{self.id}.{suffix}", self.scope,
                               (Fragment(part.document_id, part.key, content, part.location),), self.attempt + 1)
                for suffix, content in (("a", part.text[:pivot + 35]), ("b", part.text[pivot - 35:]))]


def split_text(fragment: Fragment) -> list[Fragment]:
    if len(fragment.text) <= MAX_BATCH_CHARS and fragment.location.message_id is None:
        return [fragment]
    parts = []
    for clause in clauses(fragment.text):
        for start in range(0, len(clause), MAX_BATCH_CHARS - 40):
            if start and len(clause) - start <= 40:
                break
            parts.append(Fragment(fragment.document_id, fragment.key,
                                  clause[start:start + MAX_BATCH_CHARS], fragment.location))
    return parts


def plan_batches(documents: list[Document], covered: set[tuple[str, str]],
                 covered_text: set[tuple[str, str, str]] | None = None) -> list[ExtractionBatch]:
    result = []
    parts_by_scope = defaultdict(list)
    for doc in documents:
        for fragment in doc.fragments:
            if not fragment.text.strip() or (fragment.document_id, fragment.key) in covered:
                continue
            position = fragment.location
            section = position.sheet or (f"第 {position.page} 页" if position.page else "正文")
            parts_by_scope[(doc.ref.document_id, f"{doc.ref.name} · {section}")].extend(
                (part for part in split_text(fragment) if
                 (part.document_id, part.key, part.text.strip().rstrip("。！？；")) not in (covered_text or set())),
            )
    for (_, scope), fragments in parts_by_scope.items():
        current, size = [], 0
        for part in fragments:
            if current and (size + len(part.text) > MAX_BATCH_CHARS or len(current) >= MAX_BATCH_FRAGMENTS):
                result.append(ExtractionBatch(f"batch-{len(result) + 1}", scope, tuple(current)))
                current, size = [], 0
            current.append(part)
            size += len(part.text)
        if current:
            result.append(ExtractionBatch(f"batch-{len(result) + 1}", scope, tuple(current)))
    return result


def recovered_rows(raw: str) -> dict:
    """Read complete items inside recognized top-level arrays of a cut-off JSON object."""
    text = re.sub(r"^\s*(?:```(?:json)?\s*)?", "", raw, flags=re.I)
    if not text.startswith("{"):
        return {}
    decoder = json.JSONDecoder()

    def skip(index):
        while index < len(text) and text[index].isspace():
            index += 1
        return index

    recovered = {}
    index = skip(1)
    while index < len(text):
        try:
            key, end = decoder.raw_decode(text, index)
        except ValueError:
            break
        index = skip(end)
        if key not in GROUPS or index >= len(text) or text[index] != ":":
            break
        index = skip(index + 1)
        if index >= len(text) or text[index] != "[":
            break
        items = recovered.setdefault(key, [])
        index = skip(index + 1)
        while index < len(text) and text[index] != "]":
            try:
                item, end = decoder.raw_decode(text, index)
            except ValueError:
                return {name: values for name, values in recovered.items() if values}
            if isinstance(item, dict):
                items.append(item)
            index = skip(end)
            if index >= len(text) or text[index] != ",":
                break
            index = skip(index + 1)
        if index >= len(text) or text[index] != "]":
            break
        index = skip(index + 1)
        if index >= len(text) or text[index] != ",":
            break
        index = skip(index + 1)
    return {key: items for key, items in recovered.items() if items}


def scoped_candidates(results: list[tuple[ExtractionBatch, dict]]) -> tuple[dict, dict]:
    """Namespace model-proposed IDs by request and retain references to known canonical IDs."""
    output = {group: [] for group in GROUPS}
    scopes = {}
    for batch, payload in results:
        local = {
            str(item["id"]): f"{batch.id}::{item['id']}"
            for group in GROUPS if group != "relations" and isinstance(payload.get(group), list)
            for item in payload[group] if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        for group in GROUPS:
            rows = payload.get(group, [])
            if not isinstance(rows, list):
                continue
            for item in rows:
                if not isinstance(item, dict):
                    continue
                if any(isinstance(item.get(key), (dict, list)) for key in ("id", "source_id", "target_id", "material_id")):
                    continue
                clean = dict(item)
                for key in ("id", "source_id", "target_id", "material_id"):
                    if isinstance(clean.get(key), str) and clean[key] in local:
                        clean[key] = local[clean[key]]
                scopes[(group, len(output[group]))] = batch.id
                output[group].append(clean)
    return output, scopes
