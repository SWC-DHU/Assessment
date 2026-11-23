# Assessment v2 语义重构

## 调用链与数据契约

1. 会话接口接收自然语言和工艺文件。上传复用原解析器并保存源文件。结构化表格可按工作表、行与列定位，PDF、DOCX 和普通文本保留可用的页、段、行或字符位置。
2. 智能体加载实际 RDF 本体目录，识别表格列并直接建立明确事实。随后按来源和段落批量提交其余片段。模型返回有逐字引文的候选 JSON。服务端逐条校验候选，附上批次，合并同一工序和相关字段。
3. 受控 YAML 词表映射工序标准词、能力编码和参数。多义能力仅在请求预算内交给模型选择。未知词保持原始表达。正式本体可用原名、标签和词表别名查询。
4. Python 校验调用 RDF 声明的关系端点。数值规则另行检查单位、量纲、引用、同一参数区间和组件数量冲突，生成规范语义模型及工艺图。应用保存会话、版本和模板投影。浏览器读取同一快照。
5. 人工审核可修正工序名称、调整组件数量、确认能力映射、补充工艺转序或确认事实。每次提交保存新的子版本、用户审核依据和重新校验结果。历史结果可继续读取和导出。

`RequirementSemanticModel` 保留现有的 `products` 和 `relations` 列表，新增 `metadata`、`material_states`、`modeling_intents`、`process_graph`、`evidences`。原有 `validation` 和 `unresolved_items` 继续承载报告与待确认项。正则规则只处理可识别且可定位的表达。材料收敛到基础物料时，模板继续显示用户原始输入输出标签。线上的资源匹配、模型关联与仿真验证在结果中保存为后续建模意图。

每条事实保留自动语义状态和人工复核状态。引文另有来源定位状态和产生方式，包括表格、规则、模型、人工或历史来源。人工确认状态可与校验警告并存。模型仅提出可追溯的候选推断，推断边默认待确认。

## 本体、标准化与工艺图

- `ontology/manufacturing.ttl` 定义产品、物料、工序、制造能力、约束和辅助类型。物料状态通过 `stateOf` 指向基础物料，工序通过 `hasInputState` 或 `hasOutputState` 关联状态。
- `ontology/relations.ttl` 给出对象关系、方向、定义域和值域。已有 `hasComponent`、`consumes`、`produces`、`requiresCapability`、`appliesTo` 保持语义。组件关系支持经来源核对的每件数量。
- `precedes` 表示普通时序，`dependsOn` 表示业务依赖，两者的工艺图边标为间接。`hasPredecessor` 保存明确前驱声明，`directlyPrecedes` 专指直接转序。图层将明确汇合与同一对节点的前驱声明合并展示，并保存两条关系引用。
- 条件、可选和返工需原文或人工声明。前后顺序循环和业务依赖循环分开检查。明确的返工边可形成受控回路。检测结果写了不合格产物但去向缺失时，系统保留问题并等待确认。
- 本体初始化检查 OWL 版本、端点和 Pydantic 枚举是否一致。版本 v1 的 Turtle 与词表作为可查询快照保留。SQLite 沿用现有表结构，旧 JSON 通过兼容模型加载。

## 批次及工具

程序通过 `ToolTrace` 编排 `parse_document`、`extract_facts`、`query_ontology`、`normalize_term`、`map_semantic_entities`、`build_process_graph`、`validate_semantics`、`validate_constraints`、`export_requirement_model`。每个阶段实际调用相应的 Python 函数。模型调用位于抽取、局部修复和受控消歧阶段。工具目录通过 API 可查询，前端从实时进度接口和持久化 Debug 读取执行结果。

每批最多 8 个来源片段、1,100 个原文字符，模型输出上限为配置值与 4,096 Token 中的较小值。不完整 JSON 只恢复结构闭合的候选项，且逐条通过局部 Schema 和来源校验。收到 `finish_reason=length` 或满记录批次时优先拆分来源，模型修复使用有限的局部上下文。共享预算耗尽时记录未处理的范围，已成功处理的事实保持可用。

| 状态 | 含义 |
| --- | --- |
| `complete` | 本轮计划的可处理范围均已执行 |
| `partial` | 保留已有可追溯事实，并明确记录遗漏或失败范围 |
| `failed` | 当前运行没有构建可用结果，诊断保留在会话 |
