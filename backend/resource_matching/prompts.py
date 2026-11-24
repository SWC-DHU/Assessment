"""Independent prompt templates and versioning for the Resource Matching Agent."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

MATCHING_PROMPT_VERSION = "2.0.0"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache
def load_matching_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8").strip()


def compose_matching_system_prompt(
    template_name: str,
    *,
    custom_system_prompt: str = "",
    custom_business_rules: str = "",
) -> str:
    sections = [load_matching_prompt(template_name)]
    if custom_system_prompt.strip():
        sections.append(f"## 智能体角色补充指令\n{custom_system_prompt.strip()[:1200]}")
    if custom_business_rules.strip():
        sections.append(f"## 业务规则约束\n{custom_business_rules.strip()[:1200]}")
    return "\n\n".join(sections)


def matching_prompt_manifest() -> dict:
    return {
        "agent_type": "resource_matching",
        "prompt_version": MATCHING_PROMPT_VERSION,
        "templates": sorted(path.stem for path in PROMPT_DIR.glob("*.txt")),
    }
