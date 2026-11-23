# 后续迁移到 Metatwin

迁移单位是 `backend/domain_semantics`。其运行时依赖为 Pydantic 2、RDFLib 和 PyYAML。Turtle、YAML 和文本 Prompt 随 Python 包分发。Assessment 的 FastAPI、SQLite、OpenAI SDK 配置和 React 工作台属于集成层。

## 接口

```python
from domain_semantics import SemanticModel, ontology_metadata
from domain_semantics.pipeline import project
from domain_semantics.projection import template_context
from domain_semantics.sources import message_document

sources = [message_document("message-1", "整线生产节拍≤120秒/模组。")]
model = project(sources, "run-1", candidate_payload)
model_json = model.model_dump(mode="json")
round_trip = SemanticModel.model_validate(model_json)
```

`candidate_payload` 是调用方抽取得到的候选对象。其交换 Schema 由 `ExtractionCandidates.model_json_schema()` 生成，静态资料可由 `ontology_metadata()` 查询。表格预处理可构造带坐标的 `Document`，或者先复用现有解析器和 `file_document()`。仅需展示时调用 `template_context()`。

## 接入边界

1. 将目标系统的文件、表格单元格、PDF 页、消息 ID 转给 `Document` 和 `Fragment`。
2. 把目标系统已有模型客户端接入候选抽取。参考 `app/services/semantic_execution.py` 的绑定点和请求预算实现。
3. 在目标持久层保存完整语义模型、运行 ID、父版本和摘要，保留原始来源的稳定可读引用。
4. 将查询和展示绑定到语义模型及版本 ID。模板定义作为单独的数据对象保存。
5. 对 Metatwin 的设备或资源库做显式匹配。解析中的能力和设备描述属于需求数据，后续设备选型有独立的来源和授权。

建议先用同一工作簿和合成用例对齐确定性投影与摘要，再接通新的 Provider 和持久层。

## v2 迁移接口

外部服务可从领域包直接导入 `RequirementSemanticModel` 和 `query_ontology`。将 `process_graph` 作为工艺拓扑交付数据，并保留语义关系和来源索引供后续资源匹配查询。消费方可据 `metadata.extraction_status` 暂缓自动建模，针对缺失的流转条件向用户发起澄清。采用 v1 时仍可用 `query_ontology(version="1.0.0")` 读取该版本的契约。
