# Assessment API

本地默认地址为 `http://127.0.0.1:7878`，交互接口文档在 `/docs`。会话中未选择文件 ID 时，接口继续使用当前会话文件作为上下文。

## 语义接口

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/semantics/catalog` | 本体、关系、词表和版本 |
| GET | `/api/semantics/schema` | 规范语义 JSON Schema |
| GET | `/api/semantics/sessions/{session_id}/versions` | 版本索引、创建时间、父版本、摘要和验证状态 |
| GET | `/api/semantics/sessions/{session_id}/versions/{version_id}` | 规范语义模型 |
| GET | `…/versions/{version_id}/context` | 用于展示和模板兼容的投影 |
| GET | `…/versions/{version_id}/issues` | 已存快照的校验问题 |
| POST | `…/versions/{version_id}/validate` | 用仍可取得的原始来源重新核验 |
| GET | `…/versions/{version_id}/export` | 下载规范 JSON |
| POST | `/api/semantics/sessions/{session_id}/project` | 从指定文件及可选的历史消息或候选结构创建独立快照 |

`project` 接受 `file_ids`、`message_id` 和 `candidates`。创建版本后可按 ID 查询或用作修订基础。原会话当前指针由正常运行改变。历史结果只有可重建或可定位的字段进入语义快照。

## 会话、配置及模板

| 方法 | 路径 | 调整 |
| --- | --- | --- |
| POST | `/api/sessions/{id}/run` | `strategy` 默认为 `semantic`，可选 `legacy`。可提供 `run_token` 进行服务端取消。用 `base_version_id` 指定历史修订基础 |
| POST | `/api/sessions/{id}/runs/{run_token}/cancel` | 请求停止在途运行。传入启动或重跑时使用的 UUID |
| POST | `/api/sessions/{id}/rerun/{message_id}` | 保存新运行。语义重跑读取原基础版本、来源及指令 |
| POST | `/api/sessions/{id}/files/{file_id}/reparse` | 重新解析存储中的文件 |
| PATCH | `/api/sessions/{id}` | 旧版 JSON 可编辑。直接改写规范语义返回 409 |
| POST | `/api/agents/test-connection` | 返回 `error_code`、HTTP 状态、有效模型及诊断 |
| POST / PATCH | `/api/agents[/{agent_id}]` | 可保存 `execution.timeout_seconds`、`request_budget` 和 `llm_disambiguation` |
| GET | `/api/templates/{template_id}/versions` | 查询自定义模板定义快照 |
| POST | `/api/templates/{template_id}/preview` | 继续支持 Jinja2 和列映射，表格增加 `cell_states`、`row_ids` 和 `mapping_issues` |
| POST | `/api/templates/{template_id}/export` | 生成 XLSX、DOCX、Markdown 或 JSON。规范 JSON 建议使用版本下载接口 |

版本和文件请求均按会话限定。Provider 错误会保存在执行记录中，其中 401、模型授权拒绝、其他 403、服务不可用和输出达到长度限制分别诊断。

## v2 领域接口

| 路径 | 方法与行为 |
| --- | --- |
| `/api/semantics/catalog?version=3.0.0&term=工序` | GET。查询 RDF 类、属性与关系端点。支持 `version=1.0.0`、`version=2.0.0` 查询历史定义 |
| `/api/semantics/terms?group=operations&text=堆叠作业` | GET。查询受控术语与映射依据 |
| `/api/semantics/tools` | GET。读取需求智能体的实际工具目录 |
| `/api/sessions/{id}/runs/{run_token}/progress` | GET。读取单进程在途运行的阶段、批次和实际状态 |
| `/api/semantics/sessions/{id}/versions/{version}/graph` | GET。读取可导出的工艺节点、边和图诊断 |
| `/api/semantics/sessions/{id}/versions/{version}/review` | POST。提交人工审核动作并生成可追溯的子版本 |

审核动作包括 `rename_operation`、`set_component_quantity`、`confirm_capability`、`add_transition` 和 `confirm_fact`。每项都携带 `note`，并按动作接受实体标识、数值或流转条件。模型结构、基础快照和操作来源随版本保存。详情接口的 `metadata.extraction_status` 和会话消息的 `partial` 状态可以分别反映覆盖与验证情况。

Schema 2.1 的版本 JSON 增加工序信息流、定性约束的关联参数及拓扑层级。已有版本接口和模板字段继续可用。新目录提供 `core_concepts`、`core_relations` 和 `instance_concepts`，由前端用于解释端点与筛选核心关系。
