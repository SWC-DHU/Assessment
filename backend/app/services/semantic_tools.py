"""The agent owns the allowlisted tool execution and trace. Provider text is data."""
from time import perf_counter
from typing import Callable

from domain_semantics.pipeline import timestamp, trace

TOOL_CATALOG = {
    "parse_document": ("选中的已上传文档或消息", "工作表、原文来源区段和待抽取批次"),
    "extract_facts": ("单个有界来源批次与候选约束", "候选 JSON、结束原因和实际用量"),
    "query_ontology": ("本体版本及可选术语", "RDF 类、属性、端点和词表"),
    "normalize_term": ("词表类别与原始术语", "候选、标准术语及映射依据"),
    "map_semantic_entities": ("候选事实和原始证据", "版本内实体、关系及映射诊断"),
    "build_process_graph": ("统一语义模型中的工序和工艺关系", "有类型的节点、边及图诊断"),
    "validate_semantics": ("已实例化的领域实体及证据", "关系、引用、来源与属性问题"),
    "validate_constraints": ("实体引用、量纲和约束实例", "量化区间冲突与约束诊断"),
    "export_requirement_model": ("统一需求语义模型", "规范 JSON 或模板兼容投影"),
}


class ToolTrace:
    def __init__(self, publish: Callable[[dict], None] | None = None):
        self.steps: list[dict] = []
        self.publish = publish or (lambda _: None)

    def start(self, stage, summary, batch=None):
        mark = timestamp()
        item = {"stage": stage, "status": "running", "summary": summary, "batch_id": batch,
                "started_at": mark[0].isoformat(), "input_summary": summary}
        self.publish(item)
        return mark, item

    def end(self, entry, status="success", summary=None, code=None):
        mark, item = entry
        update = trace(item["stage"], *mark, status, summary or item["summary"], code)
        update.update(batch_id=item["batch_id"], input_summary=item["input_summary"])
        self.steps.append(update)
        self.publish(update)

    def call(self, stage, summary, function, *args, **kwargs):
        entry = self.start(stage, summary)
        try:
            output = function(*args, **kwargs)
        except Exception as exc:
            self.end(entry, "error", code=type(exc).__name__)
            raise
        self.end(entry)
        return output
