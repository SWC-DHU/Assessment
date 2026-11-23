# 领域语义契约

| 层 | 实现 | 内容 |
| --- | --- | --- |
| 本体 | `domain_semantics/ontology/*.ttl`，RDFLib | RDF/OWL 类、关系端点、不同时间参数对应的约束类型 |
| 词表 | `domain_semantics/vocabulary/manufacturing.yaml`，PyYAML | 概念别名、受控制造能力、参数词和单位 |
| Schema | `schema.py`，Pydantic 2 | 五类实体、可重用的关系实例、证据、来源、版本和验证问题 |
| 候选 | `candidates.py` | 模型提出的事实与引文，核验结果由后端生成 |
| 校验 | `validation.py` | 引用类型、直接依赖环、条件与返工限制、数值及单位、来源冲突 |
| 投影 | `projection.py` | 语义字段与旧模板字段的只读输出上下文 |

Schema、本体、词表和 Prompt 当前均为 `1.0.0`。本体命名空间为 `https://assessment.example.org/ontology/manufacturing#`。业务检查由 Python 执行。本体保留 OWL 的关系语义供迁移或图查询使用。

## 关系与方向

- `hasPredecessor` 从当前工序指向直接前驱。工艺拓扑将其箭头转换成执行顺序。
- `branchesTo`、`mergesInto`、`conditionallyPrecedes` 和 `reworksTo` 使用工艺流向。明确的汇合类型加直接前驱可生成汇合关系。并行控制需要原文直接说明。
- `consumes` 和 `produces` 从工序指向物料，`requiresCapability` 从工序指向能力，`hasComponent` 从产品指向产品或物料。
- `appliesTo` 从约束指向产品、工序、能力或 `line`。返工边独立于直接依赖环检查。

## 证据、映射与未知值

证据包含文档 ID、片段 ID、逐字引文、位置和批次。工作表引用带 Sheet、行及单元格，文本文档带行或段落，PDF 带页与行，用户输入带消息 ID。起止偏移相对于片段。语义摘要基于内容，排除时间、运行和来源的随机标识，同时保留可比的输入 SHA-256。

决定性的表格证据来自单元格及其表头。词表使用标准名、编码、同义词和能力列中的明确别名片段。多个候选时可以调用现有 Provider 作有限选项判断。未知能力保留原词及来源。当候选的具体设备无法逐字核验时，要求保留空值。当前实体只表达需求阶段的设备描述。

工时、等待、缓存和节拍独立编码。示例 `整线生产节拍≤120秒/模组` 表达为 `target_id="line"`、`parameter_code="takt_time"`、`comparator="<="`、`value=120`、`unit="s"`、`basis="模组"`。约束数值仅在受支持的范围和量纲内核验，显式空值和零值分开保存。

状态包括已核验、待确认、来源冲突、校验不通过及未处理。逐字匹配验证引用的存在。语义检查进一步核对对象、比较方向和关系端点。自然语言识别采用保守规则，定向工时修订、明确表格列和所支持的运算符拥有较强的确定性。跨段推理、OCR 和更复杂的工程单位留作后续扩展。
