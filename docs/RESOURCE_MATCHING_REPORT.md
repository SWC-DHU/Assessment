# Assessment 资源匹配智能体开发与验证报告

日期：2026-11-24（Asia/Shanghai）。本报告记录在现有需求解析智能体与 `ResourceMatchingInput` 1.0.0 交接契约基础上，完成模型资产库、共享本体扩展、资源匹配智能体、前后端联调及对比实验的完整实现与验证结果。

## 1. 模块架构与 Metatwin 迁移设计

后端新增两个低耦合、高内聚的独立领域包，不依赖 FastAPI/SQLAlchemy，可直接迁移至 Metatwin 平台：

```text
backend/
├── domain_semantics/           # 共享领域本体与受控词表（新增 resources.ttl、units.py）
├── model_assets/               # 统一模型资产库（Schema / 校验 / 目录 / 59 个 synthetic 资产）
│   ├── schema.py
│   ├── validation.py
│   ├── catalog.py
│   ├── data/                   # 59 份独立 JSON 资产文件
│   └── geometry/               # 预留三维模型绑定目录（与语义解耦）
├── resource_matching/          # 资源匹配智能体核心引擎
│   ├── schema.py               # ResourceMatchingOutput 1.0.0 下游交接契约
│   ├── retrieval.py            # 需求解引用 + 共享本体能力检索 + 关键词基线
│   ├── constraints.py          # PASS / FAIL / UNKNOWN 三值多约束校验引擎
│   ├── ranking.py              # 四维可配置综合评分与分层排序
│   ├── tools.py                # 确定性工具目录与执行追踪
│   └── agent.py                # LLM 协同与 A/B/C 三方法调度
├── app/api/
│   ├── model_assets.py         # 模型资产库 REST API
│   └── resource_matching.py    # 资源匹配 REST API 与运行历史持久化
└── experiments/
    ├── battery_matching_gold.json
    └── run_matching.py         # A / B / C 对比评测脚本
```

## 2. 统一模型资产 Schema（`ModelAsset` 1.0.0）与虚拟资产库

- **八大语义模块**：`identity`、`capabilities`、`material_compatibility`、`parameters`、`interfaces`、`geometry`（允许为空/部分占位）、`behavior`（允许为空）、`model_binding`（与三维文件解耦，通过稳定 `asset_id` 预留 GLB/STEP/USD 绑定入口）。
- **资产定义与实例分离**：通过 `instantiation_policy`（`reusable`、`max_parallel_instances`、`instance_id_pattern="{asset_id}#inst-{seq}"`）显式区分可复用资产定义与产线中的多次实例化。
- **Synthetic 虚拟模型资产库**：共构造 **59 个** 通过 Pydantic 与共享词表校验的 synthetic 资产，覆盖全部 **10 大类别**（`feeding: 9`、`cleaning: 8`、`adhesive: 7`、`stacking: 4`、`assembly: 10`、`welding: 5`、`inspection: 4`、`transport: 3`、`robot: 3`、`auxiliary: 6`）。所有资产均显式声明 `data_source="synthetic"` 及免责声明，不包含任何工序 ID 硬编码或预设答案。
- **对照干扰覆盖**：内置物料不匹配干扰（软包电芯、圆柱电芯、金属壳体）、状态转换不匹配干扰（来料预贴膜、NG 不合格品分流）、能力不足单功能模块（用于多模型协同）、低性能/长工时资产及信息缺失原型资产（`MA-FEED-05`、`MA-ADH-06`、`MA-WLD-05`、`MA-INS-04`）。

## 3. 资源匹配智能体运行机制与下游契约

1. **需求解引用与交接门禁**：直接消费 `ResourceMatchingInput` 1.0.0，解引用工序能力、物料、状态、信息流以及**工序级 / 能力级 / 整线级**三层约束。对 `blocked` 输入默认阻断并返回完整诊断，对 `ready` / `ready_with_warnings` 正常推进并透传警告。
2. **共享本体语义检索**：依据 `required_capability_codes` 检索完整覆盖单资产池与按能力划分的部分资产池。
3. **三值硬约束校验（`PASS` / `FAIL` / `UNKNOWN`）**：
   - `FAIL`：明确违反能力覆盖、物料适配、状态转换、强制数值参数（经 `units.py` 统一量纲换算）或必需信息流接口 → **强制淘汰**，严禁进入合格候选，且不允许综合评分抵消。
   - `UNKNOWN`：资产缺失必要参数、状态或接口定义 → 强制进入 **待确认集合（`pending_candidates`）**。
   - `PASS`：全部硬约束通过 → 进入合格候选集参与排序。
   - **整线节拍隔离**：整线 `takt_time`（如 `<= 120 s/模组`）绝不作为单台设备硬淘汰条件，而是输出至 `line_constraints` 引用，并在候选条目上提供单机/并联实例参考提示（交由后续关联建模与产线平衡智能体决策）。
4. **可配置四维评分与 Top-k 输出**：综合 `functional_fit`（0.35）、`material_fit`（0.30）、`technical_performance`（0.20）与 `modeling_completeness`（0.15），默认输出 Top-3 单资产候选、按能力拆分的部分候选（支持多模型协同）、待确认集合、淘汰原因、未满足需求、原始 `process_graph` 与 `line_constraints`。
5. **LLM 与确定性工具协同**：标准编码匹配、单位换算、三值校验与排序完全由确定性工具执行；可选开启 LLM 辅助定性工艺解释与消歧（且不得推翻硬约束结论）。

## 4. 前端功能实现

在保留原有需求解析三栏工作台的基础上，顶部新增模块切换导航（`需求解析` / `模型资产库` / `资源匹配`），并在「下游交接」视图提供一键「进入资源匹配」入口：

- **模型资产库（`ModelAssetLibrary.tsx`）**：分类/能力/绑定状态筛选、关键词搜索、八大模块结构化详情、Schema 查看（五层数据契约浏览器）、JSON 校验与批量导入/导出、三维模型绑定状态及预留绑定表单。
- **资源匹配工作台（`ResourceMatchingWorkbench.tsx`）**：会话与历史语义版本选择、Top-k 与匹配方法（A/B/C）配置、LLM 解释开关、工序列表与可点击工艺拓扑条、整线约束与执行追踪、Top-k 合格候选评分拆解、按能力拆分多模型协同视图、待确认集合、淘汰原因明细、点击资产 ID 跨模块跳转以及结果保存与 JSON 导出。

## 5. 测试与 A/C 对比实验结果

### 自动化验证

| 验证项 | 命令与覆盖范围 | 结果 |
| --- | --- | --- |
| 后端单元测试与集成测试 | `(cd backend && .venv/bin/pytest -q)`（含 80 项原有测试 + 13 项模型资产与资源匹配新测试） | **93 passed, 0 failed** |
| 前端 TypeScript 与构建 | `(cd frontend && npm run build)` | **通过（0 错误）** |
| 端到端 Chrome UI 验证 | 上传电池模组工艺表+自然语言 → 需求解析 → 下游交接直达资源匹配 → 16/16 工序匹配、拆分/待确认/淘汰视图、导出 JSON、跳转模型资产库及三维绑定视图 | **通过（0 页面错误）** |
| 原工作台全量回归 | `frontend/tests/workbench_smoke.py`（多视口拓扑、证据、审核、交接、取消） | **通过** |

### 电池模组案例 A vs C 对比实验（3 次重复，Top-3，离线确定性路径）

实验命令：
```sh
cd backend
.venv/bin/python -m experiments.run_matching \
  --gold experiments/battery_matching_gold.json \
  --input "../test case/工艺需求文件.xlsx" \
  --text-file "../test case/自然语言建模需求.md" \
  --methods a,c --top-k 3 --repeat 3 --offline \
  --output experiments/results/battery_matching_ac_2026-11-24.json
```

| 指标 | 方法 A（关键词匹配基线） | 方法 C（本体语义 + 多约束推理） |
| --- | ---: | ---: |
| Precision@3 | 0.7500 | **1.0000** |
| Recall@3 | 0.9271 | **1.0000** |
| 能力覆盖率 | 0.9288 | **1.0000** |
| 硬约束违反率 | 0.1875（9/48 违规） | **0.0000（0 违规）** |
| 匹配依据正确率 | 0.7500 | **1.0000** |
| 待确认资产识别召回率 | 0.7500 | **1.0000** |
| 干扰资产拦截率 | 0.7941 | **1.0000** |
| 平均运行耗时 | 57.65 ms | **18.25 ms** |

方法 B（直接 LLM 匹配基线）已完整接入 `experiments/run_matching.py` 与前端/API，配置在线 Provider 后可直接运行 `--methods a,b,c --saved-agent first` 获取三方法完整对照数据。
