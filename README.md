# Assessment · 工艺需求智能体工作台

Assessment 用于研究制造需求抽取、来源核验、工艺拓扑和模板填充。v2 提供可查询 RDF 本体、分批抽取、部分结果恢复和人工审核版本。它包含 FastAPI、Pydantic 2、SQLAlchemy、SQLite 后端，以及 Vite、React 18、TypeScript、Ant Design 6 前端。

## 启动

```sh
cd backend
uv venv --python 3.12
uv pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 7878 --reload
```

已有虚拟环境可跳过 `uv venv`。可选择复制 `backend/.env.example` 为 `backend/.env` 配置默认 Provider，或在智能体面板填写 API 地址、模型和密钥。后端默认使用本地 `playground.db` 和 `uploads/`，启动时补建兼容表；接口文档在 `/docs`。

```sh
cd frontend
npm install
npm run dev
```

浏览器打开 [Assessment](http://localhost:5173)。上传 XLSX、CSV、DOCX、PDF、TXT 或 Markdown，发送一次解析请求。可识别的表格列和明确的数量、节拍及编号表达由后端提取。其余自然语言由配置的 Provider 处理。工作台可检索语义实例、聚焦单道工序、查看校验问题和证据，并导出规范 JSON。切换到旧版自定义 Schema 和本地 Mock 可运行原合成样例。

## 验证与实验

```sh
# 从项目根目录执行
(cd backend && .venv/bin/pytest -q)
(cd frontend && npm run build)
```

```sh
cd backend
.venv/bin/python -m experiments.run --gold experiments/battery_module_gold.json \
  --input "uploads/<会话 ID>/<文件名>.xlsx" --methods c --offline
.venv/bin/python -m experiments.run --gold experiments/synthetic_gold.json \
  --saved-agent first --methods a,b,c --max-tokens 4096
```

在线实验会向所选服务发送指定输入。输出记录输入、人工标注和 Prompt 的摘要、模型设置、实际请求及核心评价指标。缺少的度量保留空值。

- [架构](docs/ARCHITECTURE.md)
- [领域语义](docs/DOMAIN_SEMANTICS.md)
- [接口](docs/API.md)
- [测试报告](docs/TEST_REPORT.md)
- [迁移指南](docs/MIGRATION_GUIDE.md)
- [资源匹配开发与实验报告](docs/RESOURCE_MATCHING_REPORT.md)


### v2 混合来源回归

```sh
cd backend
.venv/bin/python -m experiments.run --methods c --offline --repeat 3   --gold experiments/battery_module_gold.json   --expected-overlay experiments/battery_mixed_expectations.json   --input '../test case/工艺需求文件.xlsx'   --text-file '../test case/自然语言建模需求.md'
```

### 资源匹配对比实验（方法 A / B / C / C+LLM）

```sh
cd backend
.venv/bin/python -m experiments.run_matching \
  --gold experiments/battery_matching_gold.json \
  --input '../test case/工艺需求文件.xlsx' \
  --text-file '../test case/自然语言建模需求.md' \
  --methods a,c,c_llm --top-k 3 --repeat 3 --offline
```

- [v2 现状审查](docs/REFACTOR_AUDIT.md)
- [v2 语义与调用链](docs/REFACTOR_V2.md)
- [v2 测试与实验报告](docs/REFACTOR_REPORT.md)

平台采用**多智能体角色隔离架构**：`requirement_parsing`（需求解析智能体）与 `resource_matching`（资源匹配智能体）各自独立管理 LLM 连接、API Key、Prompt、调用预算、专属工具与运行历史；共享 `domain_semantics` 领域本体与 `model_assets` 模型资产库（59 个 synthetic 虚拟资产），通过标准化契约 `ResourceMatchingInput` 1.0.0 与 `ResourceMatchingOutput` 1.0.0 完成交接，并为后续关联建模智能体预留标准下游接口。详述见 [交接开发报告](docs/HANDOFF_REPORT.md) 与 [资源匹配开发报告](docs/RESOURCE_MATCHING_REPORT.md)。
