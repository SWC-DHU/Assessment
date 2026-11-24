"""Prompt management for the Requirement Parsing Agent (reusing verified domain_semantics prompts)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.services.semantic_execution import PROMPT_VERSION

PROMPT_DIR = Path(__file__).resolve().parents[1] / "domain_semantics" / "prompts"


@lru_cache
def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8").strip()


def prompt_manifest() -> dict:
    return {
        "agent_type": "requirement_parsing",
        "prompt_version": PROMPT_VERSION,
        "templates": sorted(path.stem for path in PROMPT_DIR.glob("*.txt")),
    }


__all__ = ["PROMPT_DIR", "PROMPT_VERSION", "load_prompt", "prompt_manifest"]
