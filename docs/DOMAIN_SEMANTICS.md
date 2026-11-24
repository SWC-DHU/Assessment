# 领域语义契约

| 层 | 实现 | 职责 |
| --- | --- | --- |
| 静态本体 | `backend/domain_semantics/ontology` | 六个核心类 Product、Material、Operation、CapabilityRequirement、Constraint、Objective，辅助类 MaterialState、ProductionLine，并定义关系允许的端点 |
| 受控词表 | `backend/domain_semantics/vocabulary/manufacturing.yaml` | 工序和能力的别名、物料状态规则、信息流分类、参数与单位 |
| 动态实例 | `pipeline.py`、`validation.py`、`process_graph.py` | Pydantic 实例化、逐字证据核验、OWL 端点检查、数量和量纲冲突校验、NetworkX 拓扑 |
| 交换契约 | `candidates.py`、`schema.py`、`handoff.py` | 候选事实、正式语义版本及自包含的资源匹配输入 |
| 模板投影 | `projection.py` | 从已保存事实生成兼容的模板上下文 |

本体和词表为 3.0.0，正式交换 Schema 为 2.2.0。既有 `capabilities[].concept` 的 `Capability` 判别值在后端映射到 `CapabilityRequirement`。目标继续通过 `modeling_intents` 发布，`modeling_task` 以 ID 引用已有目标及整线约束。历史 Schema 1.0.0、2.0.0、2.1.0 的 JSON 可读取。基于旧版本生成子版本时，程序归并已核验的能力别名，并迁移仅作为已核验工序输入输出的已知信息项。

## 事实与拓扑

- 核心关系覆盖产品组成、直接前驱、物料输入输出、制造能力和约束对象。OWL 保留明确声明的业务依赖、先后、条件、旁路和返工端点。表达不合格产出时，缺少明确下一工序会产生待确认。
- `hasPredecessor` 在语义模型中从当前工序指向前驱。拓扑按执行方向输出，合并同一直接流转的等价边，保留构成该边的关系 ID 与证据。由前驱和工序类型共同确定的汇合只在拓扑表达。NetworkX 联合检查先后和业务依赖形成的环，返工仍单独检查条件与次数。
- 按标准编码聚合制造能力。例如“供料”“上料”“电芯供料”归入 FEED。实例保留所有原始描述和引文，各工序的能力关系保留自己的来源。
- `material_states[].material_id` 指向基础物料，输入输出关系仍能追溯原始状态词。识别信息、标识信息和检测报告等保存在 `operations[].information_flows`，保留方向与原始证据。
- 数值约束使用 `target_id`、`parameter_code`、`comparator`、`value` 或上下限、`unit` 和可选计量基准 `basis`。整线节拍示例为 `line / takt_time / <= / 120 / s / 模组`。强制与偏好由引文措辞确定。偏好超出可行区间时记录冲突，强制边界仍可用。定性要求保留原文，可带一个或多个相关参数编码。

## 交接状态

- `ready` 表示必需事实和引用已校验。工序至少需要一个已核验且有明确关系支持的制造能力。模型在歧义候选中建议的映射需人工确认。
- `ready_with_warnings` 表示没有交接阻塞，同时存在可保留的诊断。例如来源覆盖部分完成、可选参数待确认，或者原文提供不合格产出但未规定必经去向。
- `blocked` 表示缺少可核验的建模任务、工序或必要能力，或已声明的关键转序、条件、返工或强制约束存在问题。交接 JSON 仍保留工序和问题用于诊断。

## 证据与执行

证据包含文件或消息 ID、原文片段、工作表与单元格或文段位置以及提取方法。确定性规则处理已识别表列和明确文本表达。LLM 按批接收剩余片段，输出带引文的候选。程序复用已处理且内容一致的来源，部分失败时保留已核验事实及已成功的批次。兼容导出和历史版本通过原有接口访问。
