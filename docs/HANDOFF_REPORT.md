# Assessment 形式化需求与资源匹配交接开发报告

验证日期为 2026-11-23（Asia/Shanghai）。开发前基线为 `main` 的 `1f28d22c`，现状与实施决策见 [开发前审查](HANDOFF_AUDIT.md)。

## 1. 原始架构与改动范围

原系统已有带坐标的文件解析、确定性表格规则、有界 LLM 抽取和结构恢复、逐字来源核验、RDF 关系端点检查、工序引用、NetworkX 拓扑、版本记录、人工审核和模板填充。开发前的正式 Schema 为 2.1.0，本体与词表均为 3.0.0。既有 `Objective` 保存建模意图，工序及能力由 ID 相连。任务范围、预期产物和目标产品缺少统一表达，且没有面向资源匹配的契约和独立交接判定。

本次保留六个核心本体概念、OWL 关系的业务含义、词表和模板工作流。继续使用 `Capability` 作为 JSON 判别值，后端映射至 OWL `CapabilityRequirement`。已有版本仍按原 ID 读取，目标通过原有 `modeling_intents` 引用。原始工艺表、自然语言、Gold 和历史数据库均通过只读方式核验。

## 2. 调用关系

| 阶段 | 实现 | 输出 |
| --- | --- | --- |
| 文件与用户消息 | `app/api/sessions.py` → `parser.py`、`source_inputs.py`、`sources.py` | 工作表、单元格、文本片段、来源摘要及稳定定位 |
| 候选事实 | `semantic_execution.py` → `pipeline.py`、`extraction_batches.py`、`llm_extractor.py`、`candidates.py` | 已识别表格和明确语句由规则提取，剩余片段分批生成候选。保留截断恢复、局部修复、已完成批次复用和覆盖状态 |
| 语义实例化 | `pipeline.Builder` → `catalog.py`、RDF 本体和 YAML 词表 | 按引文、身份及端点生成正式事实和关系。工序引用、能力编码和量化边界由后端确定 |
| 模型与语义校验 | `modeling_task.py`、`validation.py`、`process_graph.py` | 生成任务、回填有效引用、投影工艺图，计算问题与内容摘要，再通过 Pydantic JSON 重新载入 |
| 保存与局部修正 | `sessions.py`、`semantics.py`、`review.py`、`projection.py` | 运行、审核及显式投影分别生成版本。人工审核和定向修订继承已存事实；原版本可独立读取 |
| 交接 | `semantics.py` → `handoff.build_resource_matching_input` → `validation.check_handoff` | 根据已存版本计算 `ResourceMatchingInput`，按版本预览或下载 |

在 `Builder.finish` 中先校验事实引用和关系，再生成工艺图并汇总图诊断。这样无效关系保留在正式模型用于诊断，工序的已核验引用和图边状态仍来源一致。浏览器直接展示后端结果。历史版本未存工艺图时也由后端即时投影。

## 3. 正式 Schema 2.2.0

| 类型 | 关键字段与对应概念 | 生成和校验 |
| --- | --- | --- |
| `ExtractionCandidates` | 逐条带 `Citation` 的产品、工序、物料、状态、能力、约束、目标和关系候选 | 严格校验数字、布尔值、返回字段与必需引文。批次允许保留可核验的有效条目 |
| `ModelingTask` 与 `Objective` | `task_type`、`target_scope`、`target_product_id`、`required_outputs`、`objective_ids`、`constraint_ids` | 任务组织已有目标和整线约束。范围由明确构建请求或人工确认取得。产品须有明确名称关联或人工确认，冲突有独立诊断 |
| `Product`、`Material`、`MaterialState` | `component_ids`、`hasComponent.quantity`、`material_id` | 组成数量和基准由关系证据支持。物料状态指向基础物料 |
| `Operation`、`Capability` | 编号、标准词、工序类型、能力、约束、输入／输出物料和状态、前驱、信息流、设备条件 | 能力来自 `requiresCapability` 与词表。设备条件有独立引文。工序前驱记录明确声明，其他拓扑关系保存在关系表和工艺图 |
| `Constraint` | `target_id`、`parameter_code`、比较符、数值或范围、单位、计量基准、强度、定性说明 | 核对引文、单位和量纲，校验范围及相容区间。负工时标记为无效。冲突偏好保留诊断，兼容的强制约束仍可用 |
| `ProcessRelation`、`ProcessGraph` | 稳定关系 ID、端点、条件和返工上限，拓扑边记录方向、直接性、原关系 ID 和证据 | 区分前驱、先后、依赖与直接转序。保留主线、分支、汇合、可选和条件关系；仅凭明确来源或审核创建返工 |
| `RequirementSemanticModel` | 上述事实加 `source_refs`、`evidences`、`unresolved_items`、`validation` 和 `metadata` | 检查重复／保留 ID、版本一致性、证据和关系。可导出、重新载入；接受缺少新任务字段的 1.0.0、2.0.0、2.1.0 历史模型 |

表格能力列中，若“汇合”一类词直接修饰同一行声明的物料标签，规则将它识别为物料流语境。其他未知能力术语继续保留为待确认。歧义映射的候选保持独立，人工确认后才参与已核验编码的归并。人工确认的约束在随后的自动处理和文本定向修订中保留，需要人显式作出修订决定。

## 4. 交接契约 1.0.0

| 结构 | 业务含义 |
| --- | --- |
| `source_model` | 来源模型的运行 ID、Schema／本体／词表版本、内容摘要、抽取覆盖和语义校验状态 |
| `modeling_task`、`target_product_ids`、`line_constraint_ids` | 已有建模任务及关联项 |
| `operation_requirements` | 每道工序的已核验能力编码、能力实例、工序约束、输入输出物料和状态、已声明前驱、信息流、显式设备条件与来源 |
| `capabilities`、`constraints`、`products`、`materials`、`material_states`、`objectives` | 以同一份交接 JSON 内的稳定 ID 索引完整记录。能力级约束从能力记录的 `parameter_constraint_ids` 解析 |
| `relations`、`process_graph`、`source_refs` | 组件数量、有类型的工艺关系、条件、拓扑和来源 |
| `handoff_validation` | 独立的就绪状态及问题清单。每项包含严重程度、业务路径与受影响工序 |

`ready` 没有交接问题。`ready_with_warnings` 的来源覆盖、可选约束或其他事项可待办，确定的工序匹配需求仍可消费。`blocked` 保留诊断数据。缺少建模任务、可核验工序或必要能力，关键引用或转序失效，明确返工缺少条件或上限，强制约束无法解释及非偏好区间冲突均可阻塞。设备型号和未声明的单件工时可留空。抽取覆盖、语义校验、交接属于三个独立字段。

查询与导出按会话和版本读取已存快照，复用同一转换函数。缺失图的老版本仍能构造投影，缺少任务依据时显示阻塞。交接结果有足够的语义数据供下游解析引用和检查来源。接口见 [API 文档](API.md)。

## 5. 工作台

- 保留素材与会话、需求对话、解析结果三栏及窄屏导航。结果概览增加任务范围、目标、产物和任务证据，历史版本仍由结果栏选择。
- 增加只读数据契约浏览器。候选、正式语义、资源匹配分别读取对应 API，展示结构、字段类型、必填规则和本体映射。旧版自定义 Schema 仍在原智能体配置入口。
- 工序概况按引用展示能力、约束、物料、状态、依赖和诊断。图节点打开对应工序，来源按钮定位 Excel 单元格或高亮原始消息中的引文。建模任务可审定并保存为子版本。
- “下游交接”显示所选版本的交接状态、任务、能力概要、整线约束、工序资源需求、工艺图入口、阻塞和警告。JSON 预览来自版本 API，导出链接使用对应版本的专用接口。

## 6. 测试与实验

### 命令

```sh
assessment_qa=$(mktemp -d /tmp/assessment-handoff-qa.XXXXXX)
(cd backend && .venv/bin/python -B -m pytest -q -p no:cacheprovider)
(cd frontend && npm run build -- --outDir "$assessment_qa/dist-final")
(cd backend && .venv/bin/python -B -m experiments.run --methods c --offline --repeat 3 \
  --gold experiments/battery_module_gold.json --expected-overlay experiments/battery_mixed_expectations.json \
  --input '../test case/工艺需求文件.xlsx' --text-file '../test case/自然语言建模需求.md' \
  --output "$assessment_qa/offline-final.json")
backend/.venv/bin/python -B frontend/tests/workbench_smoke.py \
  --base-url http://127.0.0.1:15174 --workbook 'test case/工艺需求文件.xlsx' \
  --chrome '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' --screenshots "$assessment_qa/screenshots-final"
```

浏览器运行使用独立的 SQLite 与上传目录。FastAPI 在 17878 端口启动，Vite 以 `VITE_API_TARGET=http://127.0.0.1:17878` 转发。测试阶段产生的实验 JSON、截图和构建产物写入临时目录。

| 验证 | 结果 |
| --- | --- |
| Python 基线 | 48 通过、0 失败 |
| Python 全量 | 72 通过、0 失败。覆盖 Schema 往返、历史兼容、严格候选、拓扑方向、合法返工、非法环路、约束冲突及交接阻塞 |
| 版本 API | 用原始 XLSX 和自然语言实际上传并执行，比较预览、图、导出和正式版本。跨会话及无效版本返回 404。审核子版本的状态与原版本独立 |
| 历史数据 | 只读验证现有 SQLite 的 5 个语义版本（1 个 1.0.0、4 个 2.0.0），均可加载并生成带诊断的交接预览，每份含 16 道工序和 15 条图边 |
| TypeScript 与 Vite | 构建通过，Vite 保留主包超过 500 kB 的提示 |
| 原始案例离线 | 重复运行 3 次，模型请求均为 0，抽取覆盖均为 `partial`。工序 16/16、原始物料标签 27/27、标注范围内工艺与物料关系 58/58、信息流 2/2、能力映射抽样 8/8，均与 Gold 一致 |
| 工序和约束 | 3 道旁路，2 处汇合共 5 条进入汇合的边。8 个方形电芯及 `≤ 120 s/模组` 各命中 1/1。工作簿无逐工序工时或设备型号，交接中相应字段留空 |
| 交接结果 | 混合案例离线 `ready_with_warnings`，抽取为 `partial`、语义校验为 `pending`。16 道工序都有核验后的能力编码，共 14 个不同编码。目标产品待确认、EOL 不合格去向和未处理批次均有诊断。仅工作簿运行因建模任务缺失而阻塞，仍可导出诊断 |
| Chrome 回归 | 1512、1280、900、390、320 px 通过。覆盖上传、自然语言输入、拓扑和语义模型、文件单元格定位、消息高亮、人工审核、历史切换、三层 Schema、模板、交接状态、导出及取消 |
| 真实 Provider | 用已配置的 Provider 运行 `experiments.run.run`，参数为 `synthetic_smoke_gold.json`、方法 C、1 次重复、预算 1、最多 2400 Token、超时 25 秒、temperature 0。实际模型 `deepseek-v4.1-flash` 返回 `stop`，1 次请求，1792 Token，抽取覆盖为 `complete`。工序与节拍各命中 1/1，通过 Pydantic，评分范围内引文定位率 1.00 |

真实 Provider 验证范围为单次合成用例。原始工作簿与自然语言的混合案例已验证离线路径，剩余自由文本的线上覆盖、多模型稳定性和更多工业样本尚未验证。

## 7. 下游集成

资源匹配智能体可读取 `ResourceMatchingInput` 1.0.0。建议先验证契约版本和 `handoff_validation.status`，对阻塞载荷返回具体诊断。对就绪载荷可按 `operation_id` 消费 `required_capability_codes`，按 ID 解引用工序及能力级约束、物料状态和产品组成，使用状态为 `verified` 的约束和图边参与计算。工艺图表达逻辑转序，物理连接需要后续独立建模。

当前契约和回归结果具备开展资源匹配智能体开发的技术基础。原始案例可用已核验工序需求启动下游工作，待确认事项仍要随版本传递。
