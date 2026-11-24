# Assessment API

本地默认地址为 `http://127.0.0.1:7878`，交互接口文档在 `/docs`。会话中未选择文件 ID 时，接口继续使用当前会话文件作为上下文。

## 语义接口

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/semantics/catalog` | 本体、关系、词表和版本 |
| GET | `/api/semantics/schema` | 规范语义 JSON Schema |
| GET | `/api/semantics/candidates/schema` | LLM 候选事实 JSON Schema |
| GET | `/api/semantics/handoff/schema` | 资源匹配输入 JSON Schema |
| GET | `/api/semantics/sessions/{session_id}/versions` | 版本索引、创建时间、父版本、摘要和验证状态 |
| GET | `/api/semantics/sessions/{session_id}/versions/{version_id}` | 规范语义模型 |
| GET | `…/versions/{version_id}/context` | 用于展示和模板兼容的投影 |
| GET | `…/versions/{version_id}/issues` | 已存快照的校验问题 |
| POST | `…/versions/{version_id}/validate` | 用仍可取得的原始来源重新核验 |
| GET | `…/versions/{version_id}/export` | 下载规范 JSON |
| GET | `…/versions/{version_id}/handoff` | 计算指定版本的资源匹配输入及交接诊断 |
| GET | `…/versions/{version_id}/handoff/export` | 下载与预览一致的资源匹配输入 JSON |
| POST | `/api/semantics/sessions/{session_id}/project` | 从指定文件及可选的历史消息或候选结构创建独立快照 |

`project` 接受 `file_ids`、`message_id` 和 `candidates`。创建版本后可按 ID 查询或用作修订基础。原会话当前指针由正常运行改变。历史结果只有可重建或可定位的字段进入语义快照。

## 会话、配置及模板

| 方法 | 路径 | 调整 |
| --- | --- | --- |
| POST | `/api/sessions/{id}/run` | `strategy` 默认为 `semantic`，可选 `legacy`。可提供 `run_token` 进行服务端取消。用 `base_version_id` 指定历史修订基础 |
| POST | `/api/sessions/{id}/runs/{run_token}/cancel` | 请求停止在途运行。传入启动、重跑或续跑时使用的 UUID |
| POST | `/api/sessions/{id}/rerun/{message_id}` | 保存新运行。语义重跑读取原基础版本、来源及指令 |
| POST | `/api/sessions/{id}/resume/{message_id}` | 使用当前会话的在线模型，仅处理指定语义结果中未完成的原始批次。复用已核验事实，保存带父版本链接的新结果。可传 `run_token` |
| POST | `/api/sessions/{id}/files/{file_id}/reparse` | 重新解析存储中的文件 |
| PATCH | `/api/sessions/{id}` | 旧版 JSON 可编辑。直接改写规范语义返回 409 |
| POST | `/api/agents/test-connection` | 返回 `error_code`、HTTP 状态、有效模型及诊断 |
| POST | `/api/agents/probe-json` | 使用草稿配置发送一条合成节拍样例，返回完整 JSON 验证、实际发送上限、结束原因、推理用量与思考开关结果。不会提交会话材料或保存配置 |
| POST / PATCH | `/api/agents[/{agent_id}]` | 可保存 `execution.timeout_seconds`、`request_budget`、`batch_max_tokens`、`semantic_thinking`、`thinking_parameter` 和 `llm_disambiguation` |
| GET | `/api/templates/{template_id}/versions` | 查询自定义模板定义快照 |
| POST | `/api/templates/{template_id}/preview` | 继续支持 Jinja2 和列映射，表格增加 `cell_states`、`row_ids` 和 `mapping_issues` |
| POST | `/api/templates/{template_id}/export` | 生成 XLSX、DOCX、Markdown 或 JSON。规范 JSON 建议使用版本下载接口 |

版本和文件请求均按会话限定。Provider 错误会保存在执行记录中，其中 401、模型授权拒绝、其他 403、服务不可用和输出达到长度限制分别诊断。

语义抽取每批发送的 `max_tokens` 取智能体 `max_tokens` 与 `execution.batch_max_tokens` 的较小值。未填写单批上限时，DeepSeek 官方接口默认 8192，其他接口默认 4096。官方 DeepSeek 的语义事实抽取默认关闭思考，单轮请求预算默认 4。`semantic_thinking` 可设置为 `service_default` 或 `disabled`。关闭时，可通过 `thinking_parameter` 选择 `thinking` 或 `reasoning_effort`，应先用该接口的 JSON 探测验证实际生效情况。运行诊断包含实际发送参数、结束原因及 API 返回的用量。输出为空且推理用量耗尽输出额度时，批次原因是 `reasoning_budget_exhausted`。

续跑使用原始消息和附件中的片段，不会新增用户消息。原始片段无法定位时返回 409。若续跑基于历史结果，新版本依然保存到历史，当前版本指针保留在更新的结果上。

## v2 领域接口

| 路径 | 方法与行为 |
| --- | --- |
| `/api/semantics/catalog?version=3.0.0&term=工序` | GET。查询 RDF 类、属性与关系端点。支持 `version=1.0.0`、`version=2.0.0` 查询历史定义 |
| `/api/semantics/terms?group=operations&text=堆叠作业` | GET。查询受控术语与映射依据 |
| `/api/semantics/tools` | GET。读取需求智能体的实际工具目录 |
| `/api/sessions/{id}/runs/{run_token}/progress` | GET。读取单进程在途运行的阶段、批次和实际状态 |
| `/api/semantics/sessions/{id}/versions/{version}/graph` | GET。读取可导出的工艺节点、边和图诊断 |
| `/api/semantics/sessions/{id}/versions/{version}/review` | POST。提交人工审核动作并生成可追溯的子版本 |

审核动作包括 `rename_operation`、`set_component_quantity`、`confirm_capability`、`add_transition`、`confirm_fact` 和 `set_modeling_task`。每项都携带 `note`，并按动作接受实体标识、数值或流转条件。确认建模任务可提供 `target_scope`（`production_line`、`workstation`、`operation` 或 `product`）及已核验的 `target_product_id`。模型结构、基础快照和操作来源随版本保存。

Schema 2.1 的版本 JSON 增加工序信息流、定性约束的关联参数及拓扑层级。已有版本接口和模板字段继续可用。新目录提供 `core_concepts`、`core_relations` 和 `instance_concepts`，由前端用于解释端点与筛选核心关系。

## 2.2 建模任务与交接

`RequirementSemanticModel` 2.2 增加可为空的 `modeling_task`。建模任务引用 `modeling_intents`、可确认的目标产品和整线约束。旧版本历史快照可按原 ID 读取，新增字段缺失时会读取为空值。

`ResourceMatchingInput` 1.0.0 的两条版本接口使用同一 Pydantic 契约。版本不存在或归属其他会话时返回 HTTP 404。状态为 `blocked` 时返回 HTTP 200 及诊断预览。下载响应使用 `application/json`，文件名为 `resource-matching-{version_id}.json`。请求从已存语义快照生成结果。

| 字段 | 消费方式 |
| --- | --- |
| `source_model` | 用运行 ID、来源 Schema、本体、词表版本和内容摘要标识输入，其中还带有独立的抽取覆盖状态和语义校验状态 |
| `handoff_validation` | 先判断 `ready`、`ready_with_warnings` 或 `blocked`，再读取问题、严重程度和关联工序 |
| `modeling_task`、`target_product_ids`、`line_constraint_ids` | 读取任务、任务引用和整线范围要求 |
| `operation_requirements` | 按 `operation_id` 读取已核验能力编码、直接工序约束、物料、状态、直接前驱和显式设备条件 |
| `capabilities`、`constraints`、`products`、`materials`、`material_states`、`objectives` | 用原始稳定 ID 查询本载荷内的实例。能力级约束通过 `parameter_constraint_ids` 查找 |
| `process_graph`、`relations`、`source_refs` | 读取转序、条件和返工，并用关系与引文回溯依据 |

## 模型资产库接口（`/api/model-assets`）

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/model-assets/schema` | 返回统一 `ModelAsset` 1.0.0 JSON Schema |
| GET | `/api/model-assets/summary` | 返回资产总数、有效数、分类/能力/三维绑定统计及资产库版本摘要 |
| GET | `/api/model-assets` | 按 `category`、`capability`、`material_type`、`binding_status`、`data_source`、`search` 筛选资产 |
| GET | `/api/model-assets/{asset_id}` | 读取单个模型资产完整八模块定义及词表/完整度校验报告 |
| PUT | `/api/model-assets/{asset_id}` | 创建或更新模型资产（经 Pydantic 与共享词表校验后持久化至 JSON） |
| PATCH | `/api/model-assets/{asset_id}/binding` | 预留三维模型绑定入口，更新 `binding_status`、`model_uri`、`file_format` |
| POST | `/api/model-assets/validate` | 仅校验单个或批量 JSON 资产定义，不写入资产库 |
| POST | `/api/model-assets/import` | 批量校验并导入模型资产 JSON（支持 `overwrite` 开关） |
| GET | `/api/model-assets/export` | 导出全量或指定 `asset_ids` 的模型资产 JSON |
| DELETE | `/api/model-assets/{asset_id}` | 删除指定模型资产 |

## 资源匹配智能体接口（`/api/resource-matching`）

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/resource-matching/schema` | 返回下游输出契约 `ResourceMatchingOutput` 1.0.0 JSON Schema |
| GET | `/api/resource-matching/tools` | 读取资源匹配智能体确定性工具目录 |
| GET | `/api/resource-matching/mappings` | 读取共享本体中的六项需求↔资源语义对应规则 |
| POST | `/api/resource-matching/sessions/{session_id}/versions/{version_id}/run` | 直接消费指定语义版本生成的 `ResourceMatchingInput`，执行匹配并可选持久化至 `resource_matching_runs` |
| POST | `/api/resource-matching/run-direct` | 直接提交 `ResourceMatchingInput` JSON 执行无状态或持久化匹配（便于 Metatwin 独立集成） |
| GET | `/api/resource-matching/runs` | 按 `session_id` / `semantic_version_id` 查询历史匹配记录 |
| GET | `/api/resource-matching/runs/{run_id}` | 读取指定匹配运行的完整 `ResourceMatchingOutput` |
| GET | `/api/resource-matching/runs/{run_id}/export` | 下载 `resource-matching-output-{run_id}.json` |
| DELETE | `/api/resource-matching/runs/{run_id}` | 删除指定历史匹配记录 |
