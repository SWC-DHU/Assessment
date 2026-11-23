"""Initial seed data and built-in sample datasets for Process Agent Playground."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.db_models import AgentProfile, OutputTemplate, TestSession


PROCESS_EXTRACTION_SCHEMA = {
    "mode": "visual",
    "fields": [
        {"name": "product_name", "type": "string", "description": "产品或零件名称", "required": True, "children": []},
        {"name": "part_no", "type": "string", "description": "零件图号或产品编码（如未提及填 null）", "required": False, "children": []},
        {"name": "target_takt_sec", "type": "number", "description": "整线目标节拍（秒/件）", "required": False, "children": []},
        {
            "name": "operations",
            "type": "array",
            "description": "按工艺顺序排列的工序列表",
            "required": True,
            "children": [
                {"name": "seq", "type": "string", "description": "工序号，如 OP10", "required": True, "children": []},
                {"name": "name", "type": "string", "description": "工序名称", "required": True, "children": []},
                {"name": "equipment", "type": "string", "description": "加工设备或工作站", "required": False, "children": []},
                {"name": "cycle_time_sec", "type": "number", "description": "单件工时/节拍（秒）", "required": False, "children": []},
                {"name": "input_materials", "type": "array", "description": "输入物料/毛坯/辅料清单", "required": False, "children": []},
                {"name": "output_materials", "type": "array", "description": "输出半成品或成品", "required": False, "children": []},
                {"name": "predecessors", "type": "array", "description": "前置依赖工序号列表", "required": False, "children": []},
                {"name": "quality_requirements", "type": "string", "description": "关键工艺参数或质量检测要求", "required": False, "children": []},
                {"name": "source_quote", "type": "string", "description": "原文出处或单元格坐标，如 Sheet1!B3", "required": True, "children": []},
            ],
        },
        {
            "name": "constraints",
            "type": "array",
            "description": "节拍、清洁度、安全、环境或交付约束清单",
            "required": False,
            "children": [
                {"name": "type", "type": "string", "description": "约束类别", "required": True, "children": []},
                {"name": "description", "type": "string", "description": "约束具体内容", "required": True, "children": []},
                {"name": "source_quote", "type": "string", "description": "原文依据", "required": True, "children": []},
            ],
        },
        {
            "name": "unknown_items",
            "type": "array",
            "description": "原文信息缺失、存在歧义或待客户确认的未知项",
            "required": False,
            "children": [
                {"name": "item", "type": "string", "description": "未知或待确认事项", "required": True, "children": []},
                {"name": "reason", "type": "string", "description": "缺失或歧义原因", "required": False, "children": []},
                {"name": "source_quote", "type": "string", "description": "关联上下文或标注未提及", "required": False, "children": []},
            ],
        },
    ],
    "json_schema": {
        "type": "object",
        "required": ["product_name", "operations"],
        "properties": {
            "product_name": {"type": "string"},
            "part_no": {"type": ["string", "null"]},
            "target_takt_sec": {"type": ["number", "null"]},
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["seq", "name", "source_quote"],
                    "properties": {
                        "seq": {"type": "string"},
                        "name": {"type": "string"},
                        "equipment": {"type": ["string", "null"]},
                        "cycle_time_sec": {"type": ["number", "null"]},
                        "input_materials": {"type": "array", "items": {"type": "string"}},
                        "output_materials": {"type": "array", "items": {"type": "string"}},
                        "predecessors": {"type": "array", "items": {"type": "string"}},
                        "quality_requirements": {"type": ["string", "null"]},
                        "source_quote": {"type": "string"},
                    },
                },
            },
            "constraints": {"type": "array"},
            "unknown_items": {"type": "array"},
        },
    },
}

GENERAL_EXTRACTION_SCHEMA = {
    "mode": "visual",
    "fields": [
        {"name": "project_name", "type": "string", "description": "项目或产品主题名称", "required": True, "children": []},
        {"name": "summary", "type": "string", "description": "一句话需求背景摘要", "required": True, "children": []},
        {
            "name": "requirements",
            "type": "array",
            "description": "拆解后的核心需求要点",
            "required": True,
            "children": [
                {"name": "req_id", "type": "string", "description": "编号如 REQ-01", "required": True, "children": []},
                {"name": "category", "type": "string", "description": "需求分类（工艺/设备/质量/物流/合规）", "required": False, "children": []},
                {"name": "title", "type": "string", "description": "需求标题", "required": True, "children": []},
                {"name": "detail", "type": "string", "description": "详细规格描述", "required": True, "children": []},
                {"name": "priority", "type": "string", "description": "优先级（高/中/低）", "required": False, "children": []},
                {"name": "source_quote", "type": "string", "description": "原文出处定位", "required": True, "children": []},
            ],
        },
        {
            "name": "constraints",
            "type": "array",
            "description": "关键约束与边界条件",
            "required": False,
            "children": [
                {"name": "type", "type": "string", "description": "约束类型", "required": True, "children": []},
                {"name": "description", "type": "string", "description": "约束内容", "required": True, "children": []},
                {"name": "source_quote", "type": "string", "description": "原文依据", "required": True, "children": []},
            ],
        },
        {
            "name": "unknown_items",
            "type": "array",
            "description": "信息缺失或需进一步澄清的事项",
            "required": False,
            "children": [
                {"name": "item", "type": "string", "description": "待澄清事项", "required": True, "children": []},
                {"name": "reason", "type": "string", "description": "原因说明", "required": False, "children": []},
            ],
        },
    ],
    "json_schema": {},
}

TEMPLATE_A_JINJA = """# {{ product_name or project_name or "未命名产品" }} — 标准工艺流程卡

- **零件图号 / 编码**：{{ part_no | fmt("-") }}
- **产线目标节拍**：{% if target_takt_sec %}{{ target_takt_sec }} 秒/件{% else %}-{% endif %}
- **工序总数**：{{ operations | length if operations else 0 }} 道

## 一、 工序路线与工时明细

| 工序号 | 工序名称 | 核心设备 | 单件工时(s) | 输入物料 | 输出物料 | 前置依赖 | 质量/参数要求 | 原文依据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
{% for op in operations or [] %}| {{ op.seq | fmt("-") }} | {{ op.name | fmt("-") }} | {{ op.equipment | fmt("-") }} | {{ op.cycle_time_sec | fmt("-") }} | {{ op.input_materials | join_items("、", "-") }} | {{ op.output_materials | join_items("、", "-") }} | {{ op.predecessors | join_items(", ", "无") }} | {{ op.quality_requirements | fmt("-") }} | {{ op.source_quote | fmt("-") }} |
{% else %}| - | 暂无工序数据 | - | - | - | - | - | - | - |
{% endfor %}

## 二、 约束条件清单

{% for c in constraints or [] %}- **[{{ c.type | fmt("约束") }}]** {{ c.description | fmt("-") }}  
  _依据：{{ c.source_quote | fmt("未标注") }}_
{% else %}- 暂无明确约束条件记录
{% endfor %}

## 三、 待确认未知项（防遗漏清单）

{% for u in unknown_items or [] %}- **{{ u.item | fmt("-") }}**{% if u.reason %} — {{ u.reason }}{% endif %}{% if u.source_quote %}（{{ u.source_quote }}）{% endif %}
{% else %}- 暂无待确认事项
{% endfor %}
"""

TEMPLATE_B_TABLE_CONFIG = {
    "row_source_path": "operations",
    "include_summary_sheet": True,
    "columns": [
        {"header": "产品名称", "path": "$root.product_name", "fallback": "-"},
        {"header": "零件图号", "path": "$root.part_no", "fallback": "-"},
        {"header": "工序号", "path": "seq", "fallback": "-"},
        {"header": "工序名称", "path": "name", "fallback": "-"},
        {"header": "核心设备", "path": "equipment", "fallback": "-"},
        {"header": "单件工时(秒)", "path": "cycle_time_sec", "fallback": "-"},
        {"header": "输入物料", "path": "input_materials", "fallback": "-"},
        {"header": "输出物料", "path": "output_materials", "fallback": "-"},
        {"header": "前置依赖", "path": "predecessors", "fallback": "无"},
        {"header": "质量要求", "path": "quality_requirements", "fallback": "-"},
        {"header": "原文依据 (source_quote)", "path": "source_quote", "fallback": "-"},
    ],
}

SAMPLE_DATASETS = [
    {
        "id": "sample-motor-housing-text",
        "title": "样例 1：电机壳体工艺需求文本",
        "description": "一段包含产品信息、4道主要工序、物料流转、节拍约束及未明事项的典型工艺需求文本。",
        "kind": "text",
        "filename": None,
        "content": (
            "项目需求：新能源汽车驱动电机壳体（图号 MTR-HSG-2026-A03）产线工艺规划。\n"
            "整线目标设计节拍 <= 60秒/件。\n"
            "工艺路线如下：\n"
            "1. OP10 高压压铸成型：采用 2500T 冷室压铸机，投入 ADC12 铝合金锭（3.8kg/件）与脱模剂，产出壳体压铸毛坯，单件工时 42 秒。质量要求 X 光探伤气孔率 <= 2%，无冷隔裂纹。\n"
            "2. OP20 去毛刺与抛丸清理：在 OP10 完成后进行，使用自动去毛刺工作站及履带式抛丸机，消耗不锈钢丸，产出清理后壳体半成品，工时 28 秒，分型面残余毛刺 <= 0.2mm。\n"
            "3. OP30 CNC 精密加工（端面及轴承孔）：承接 OP20 输出，使用五轴卧式加工中心与水溶性切削液，产出精加工电机壳体，当前预估节拍 55 秒，轴承孔同轴度 <= 0.015mm，表面粗糙度 Ra1.6。\n"
            "4. OP40 气密性检测与清洗烘干：承接 OP30，通过差压气密检漏仪与超声波清洗线（使用中性清洗剂），产出合格成品壳体，工时 35 秒。测试压力 0.2MPa，保压 30s 泄漏量 <= 1.5mL/min。\n"
            "补充约束：成品内腔残留颗粒物直径 <= 0.5mm，总杂质 <= 3mg。注意：OP30 换刀摊销时间暂未明确是否计入单件节拍。"
        ),
    },
    {
        "id": "sample-gearbox-csv",
        "title": "样例 2：精密减速器工艺路线表 (CSV)",
        "description": "自动在当前会话生成一份模拟工业工艺路线 CSV 文件，用于测试表格坐标提取与 source_quote 定位。",
        "kind": "csv",
        "filename": "精密减速器壳体工艺路线_sample.csv",
        "content": (
            "产品名称,图号,工序号,工序名称,设备名称,单件工时(秒),输入物料,输出物料,前置工序,质量与工艺要求\n"
            "精密行星减速器壳体,GBX-PL-09,OP10,锻造毛坯下料,锯床+中频感应炉,30,40Cr圆钢棒料,锻造坯料,,单重误差<=±15g\n"
            "精密行星减速器壳体,GBX-PL-09,OP20,数控粗车端面及内孔,CNC卧式车床,48,锻造坯料;切削液,粗车壳体,OP10,留精加工余量0.5mm\n"
            "精密行星减速器壳体,GBX-PL-09,OP30,渗碳淬火热处理,多用炉热处理线,120,粗车壳体;渗碳剂,热处理壳体,OP20,表面硬度HRC58-62;有效硬化层0.8-1.1mm\n"
            "精密行星减速器壳体,GBX-PL-09,OP40,内齿圈插齿及精密磨削,数控插齿机+内圆磨床,65,热处理壳体;磨削液,成品减速器壳体,OP30,齿形精度GB/T10095 6级;产线要求总节拍<=70秒(OP30需多台并行)\n"
        ),
    },
]


def seed_initial_data(db: Session) -> None:
    """Populate initial agents, templates, and a starter session on an empty database."""
    has_agents = db.query(AgentProfile).first() is not None
    has_templates = db.query(OutputTemplate).first() is not None

    agent1_id: str | None = None
    tmpl1_id: str | None = None

    if not has_agents:
        agent1 = AgentProfile(
            name="工业工艺需求解析专家",
            description="严谨抽取产品、工序序列、输入输出物料、工时节拍、前置依赖与未知项，强制要求附带 source_quote 原文定位防幻觉。",
            base_url="",
            api_key="",
            model="",
            temperature=0.1,
            max_tokens=4096,
            json_mode=True,
            require_source_quote=True,
            system_prompt=(
                "你是一名资深的工业制造与工艺工程（IE/PE）需求解析专家。\n"
                "你的任务是从用户提供的非结构化文本、Excel 表格、Word 文档或 PDF 规格书中，准确抽取工艺需求结构化信息。\n"
                "核心原则：\n"
                "1. 严格忠实于原文与表格坐标，严禁凭空捏造工时、物料规格或设备参数。\n"
                "2. 每道工序、每条约束都必须提供 `source_quote` 字段，尽可能保留文件行号或单元格坐标（如 `Sheet1!B3` 或 `[Row 2]`）。\n"
                "3. 遇到信息缺失、前后矛盾或口径不清的地方，不要自行猜测，请统一记录到 `unknown_items` 列表中。"
            ),
            business_rules=(
                "- 工时/节拍尽量统一换算为秒（cycle_time_sec，数值类型），若原文单位为分钟请换算为秒并在 quality_requirements 或 notes 中注明原文。\n"
                "- 工序编号按加工顺序标准化为 OP10、OP20...（若原文已有编号则保留原文编号）。"
            ),
            extraction_schema=PROCESS_EXTRACTION_SCHEMA,
        )
        agent2 = AgentProfile(
            name="通用需求要点提取器",
            description="面向项目需求说明书与会议纪要，按需求条目拆解分类、优先级与约束，适合与工艺专家对比评测。",
            base_url="",
            api_key="",
            model="",
            temperature=0.2,
            max_tokens=4096,
            json_mode=True,
            require_source_quote=True,
            system_prompt=(
                "你是一名系统分析与需求工程专家，擅长将复杂的项目沟通文本、文档和表格拆解为结构化的需求条目清单。\n"
                "请保持客观、准确，每条核心需求均需标注分类、优先级及原文依据（source_quote）。"
            ),
            business_rules="- 同一段落包含多个独立诉求时，请拆分为多条独立的 requirements 条目。",
            extraction_schema=GENERAL_EXTRACTION_SCHEMA,
        )
        db.add(agent1)
        db.add(agent2)
        db.flush()
        agent1_id = agent1.id
    else:
        first_agent = db.query(AgentProfile).first()
        agent1_id = first_agent.id if first_agent else None

    if not has_templates:
        tmpl_a = OutputTemplate(
            name="标准工艺流程卡（Markdown 表格 + 约束清单）",
            description="基于 Jinja2 渲染的标准工艺卡片视图，含产品基础信息、全工序流转表、约束条件与待确认未知项。",
            template_type="jinja2",
            format="markdown",
            jinja_content=TEMPLATE_A_JINJA,
            table_config={},
        )
        tmpl_b = OutputTemplate(
            name="产线节拍与物料汇总表（支持导出 Excel）",
            description="面向 IE/工艺评审的结构化表格映射模板，支持直接预览表格并一键导出多 Sheet 美化版 Excel (.xlsx)。",
            template_type="table_mapping",
            format="table",
            jinja_content="",
            table_config=TEMPLATE_B_TABLE_CONFIG,
        )
        db.add(tmpl_a)
        db.add(tmpl_b)
        db.flush()
        tmpl1_id = tmpl_a.id
    else:
        first_tmpl = db.query(OutputTemplate).first()
        tmpl1_id = first_tmpl.id if first_tmpl else None

    if db.query(TestSession).first() is None:
        starter = TestSession(
            title="新建工艺解析测试",
            agent_id=agent1_id,
            template_id=tmpl1_id,
            current_extracted_json=None,
        )
        db.add(starter)

    db.commit()
