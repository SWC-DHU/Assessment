"""Recognize numbered operation declarations and explicit fields in production prose."""
from dataclasses import dataclass
import re

DECLARATION = re.compile(
    r"(?:第[一二三四五六七八九十百\d]+道)?工序\s*(?:为|是)\s*"
    r"(?P<name>[^，,。；\n]{1,36})[，,]\s*(?:工序)?编号\s*[:：]?\s*(?P<number>[A-Za-z0-9][A-Za-z0-9_-]{0,15})"
)
FIELDS = {
    "consumes": r"消耗|投入|使用物料|输入(?:物料)?",
    "produces": r"产出|产物为|产生|输出(?:物料)?",
    "requiresCapability": r"需要(?:具备)?|要求具备|需具备",
    "hasPredecessor": r"前置工序(?:为|是)?|直接前驱(?:为|是)?",
}
FIELD_PATTERN = re.compile(r"(?P<label>" + "|".join(f"(?:{label})" for label in FIELDS.values()) + r")\s*[:：]?\s*(?P<value>\S.*)")


@dataclass(frozen=True)
class StatedOperation:
    name: str
    number: str
    quote: str
    fields: tuple[tuple[str, str, tuple[str, ...]], ...]
    fully_read: bool


def clauses(text: str) -> list[str]:
    return re.findall(r"[^。！？；\n]+[。！？；\n]?", text)


def operation_declarations(text: str):
    for line in clauses(text):
        trimmed = line.strip().rstrip("。！？；")
        match = DECLARATION.match(trimmed)
        if not match:
            continue
        fields = []
        fully_read = True
        for segment in re.split(r"[，,]", trimmed[match.end():]):
            segment = segment.strip()
            if not segment:
                continue
            field = FIELD_PATTERN.fullmatch(segment)
            if not field or re.search(r"可能|例如|或者|暂定|若|如果", segment):
                fully_read = False
                continue
            kind = next(name for name, labels in FIELDS.items() if re.fullmatch(labels, field["label"]))
            values = tuple(item.strip() for item in re.split(r"、|与|和|及", field["value"]) if item.strip())
            if values:
                fields.append((kind, segment, values))
        yield StatedOperation(match["name"].strip(), match["number"], trimmed, tuple(fields), fully_read)
