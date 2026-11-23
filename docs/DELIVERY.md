# Assessment 升级文件清单

本目录未包含 Git 元数据。清单按本次实际新增和修改的源文件整理，省略构建缓存与虚拟环境产物。

| 范围 | 新增 | 修改 |
| --- | --- | --- |
| 领域后端 | `backend/domain_semantics/__init__.py`、`schema.py`、`candidates.py`、`catalog.py`、`sources.py`、`pipeline.py`、`validation.py`、`projection.py` |  |
| 领域资料 | `backend/domain_semantics/ontology/{manufacturing,relations,constraints}.ttl`、`vocabulary/manufacturing.yaml`、`prompts/{extraction,repair,mapping}.txt` |  |
| 后端 API 与存储 | `backend/app/api/semantics.py` | `backend/app/api/{agents,samples,sessions,templates}.py`、`backend/app/models/{db_models,schemas}.py`、`backend/app/{config,main}.py` |
| 后端适配与依赖 | `backend/app/services/{semantic_execution,source_inputs,redaction}.py` | `backend/app/services/{llm_extractor,parser,template_filler}.py`、`backend/.env.example`、`backend/{requirements.txt,pyproject.toml}` |
| 工作台前端 | `frontend/src/components/{SemanticGraph,HtmlPreview}.tsx` | `frontend/src/{App.tsx,main.tsx,index.css}`、`frontend/src/api/client.ts`、`frontend/src/types/index.ts`、`frontend/src/components/{TopBar,LeftSidebar,ChatWorkbench,RightPanel,AgentManagerModal,TemplateStudioModal}.tsx`、`frontend/tailwind.config.js` |
| 回归与实验 | `backend/tests/{test_domain_foundation,test_semantic_pipeline,test_semantic_api}.py`、`frontend/tests/workbench_smoke.py`、`backend/experiments/run.py`、`backend/experiments/{battery_module_gold,synthetic_gold,synthetic_smoke_gold}.json`、`backend/experiments/results/{battery_offline,synthetic_abc,synthetic_smoke}_2026-11-23.json` | `backend/tests/{test_agent_connection,test_parser}.py` |
| 说明 | `docs/{PROJECT_AUDIT,ARCHITECTURE,DOMAIN_SEMANTICS,API,TEST_REPORT,MIGRATION_GUIDE,DELIVERY}.md` | `README.md` |
