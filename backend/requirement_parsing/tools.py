"""Exclusive tool registry for the Requirement Parsing Agent."""
from __future__ import annotations

from app.services.semantic_tools import TOOL_CATALOG, ToolTrace

REQUIREMENT_PARSING_TOOLS = dict(TOOL_CATALOG)


def list_requirement_tools() -> list[dict[str, str]]:
    return [
        {"id": key, "input": args, "output": output}
        for key, (args, output) in REQUIREMENT_PARSING_TOOLS.items()
    ]


__all__ = ["REQUIREMENT_PARSING_TOOLS", "ToolTrace", "list_requirement_tools"]
