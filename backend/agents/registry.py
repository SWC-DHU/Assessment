"""Central role registry declaring each agent's responsibilities, tools, prompts, and I/O contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .runtime import AgentType


@dataclass(frozen=True)
class AgentRoleSpec:
    agent_type: AgentType
    display_name: str
    description: str
    input_contract: str
    output_contract: str
    prompt_version: str
    default_system_prompt: str
    default_business_rules: str
    tools_provider: Callable[[], list[dict[str, str]]]

    def to_dict(self) -> dict:
        return {
            "agent_type": self.agent_type,
            "display_name": self.display_name,
            "description": self.description,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "prompt_version": self.prompt_version,
            "tools": self.tools_provider(),
        }


DEFAULT_REQUIREMENT_PROMPT = (
    "你是一名资深的工业制造与工艺工程（IE/PE）需求解析专家。\n"
    "你的任务是从用户提供的非结构化文本、Excel 表格、Word 文档或 PDF 规格书中，准确抽取工艺需求结构化信息。\n"
    "核心原则：\n"
    "1. 严格忠实于原文与表格坐标，严禁凭空捏造工时、物料规格或设备参数。\n"
    "2. 每道工序、每条约束都必须提供可追溯的原文依据，严格复用共享制造本体与受控词表。\n"
    "3. 遇到信息缺失、前后矛盾或口径不清的地方，不要自行猜测，请统一记录为待确认项。"
)

DEFAULT_REQUIREMENT_RULES = (
    "- 工时/节拍尽量统一换算为秒，若原文单位为分钟请换算为秒并保留原计量基准。\n"
    "- 工序编号按加工顺序标准化（若原文已有编号则保留原文编号）。"
)

DEFAULT_MATCHING_PROMPT = (
    "你是一名面向数字孪生制造产线的资源匹配与多约束推理智能体。\n"
    "你的职责是基于标准化 ResourceMatchingInput，结合共享领域本体与模型资产库，对存在语义歧义、设备条件描述复杂、"
    "能力覆盖不足或需多模型协同的工序进行语义消歧、检索策略调整与工程匹配说明。\n"
    "强制原则：\n"
    "1. 严禁虚构不存在的模型资产 ID、设备参数或制造能力。\n"
    "2. 严禁绕过或推翻确定性工具的三值硬约束校验（PASS/FAIL/UNKNOWN）。\n"
    "3. 严禁擅自篡改上游需求事实；若发现上游需求缺失或冲突，仅输出结构化反馈建议。\n"
    "4. 缺失参数或无法核实的信息必须保持 UNKNOWN 状态进入待确认集合。"
)

DEFAULT_MATCHING_RULES = (
    "- 整线级节拍约束（如 <= 120 s/模组）不得作为单台设备的硬淘汰条件，仅做并联实例数量参考提示。\n"
    "- 多能力工序在单资产覆盖不足时，优先按共享标准能力编码拆分为可协同的多模型候选集合。\n"
    "- 所有 LLM 提出的候选资产与检索策略调整均须交由确定性约束引擎复验后方可生效。"
)


def _requirement_tools() -> list[dict[str, str]]:
    from requirement_parsing.tools import list_requirement_tools
    return list_requirement_tools()


def _matching_tools() -> list[dict[str, str]]:
    from resource_matching.tools import list_matching_tools
    return list_matching_tools()


AGENT_ROLES: dict[AgentType, AgentRoleSpec] = {
    "requirement_parsing": AgentRoleSpec(
        agent_type="requirement_parsing",
        display_name="需求解析智能体",
        description="解析自然语言与工艺表格文档，执行确定性抽取、LLM 分批补充、本体映射、工艺拓扑构建与交接校验。",
        input_contract="UploadedDocuments + UserNarrative",
        output_contract="RequirementSemanticModel 2.2.0 / ResourceMatchingInput 1.0.0",
        prompt_version="2.2.1",
        default_system_prompt=DEFAULT_REQUIREMENT_PROMPT,
        default_business_rules=DEFAULT_REQUIREMENT_RULES,
        tools_provider=_requirement_tools,
    ),
    "resource_matching": AgentRoleSpec(
        agent_type="resource_matching",
        display_name="资源匹配智能体",
        description="消费 ResourceMatchingInput，协同共享本体、模型资产库、LLM 消歧策略与三值硬约束校验，输出各工序 Top-k 候选模型集合。",
        input_contract="ResourceMatchingInput 1.0.0",
        output_contract="ResourceMatchingOutput 1.0.0",
        prompt_version="2.0.0",
        default_system_prompt=DEFAULT_MATCHING_PROMPT,
        default_business_rules=DEFAULT_MATCHING_RULES,
        tools_provider=_matching_tools,
    ),
}


def get_role_spec(agent_type: AgentType) -> AgentRoleSpec:
    return AGENT_ROLES[agent_type]


def list_role_specs() -> list[dict]:
    return [spec.to_dict() for spec in AGENT_ROLES.values()]
