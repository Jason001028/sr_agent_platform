"""Runtime configuration for the agent platform (env-driven, no framework deps).

Every value is read from an environment variable so the same code speaks the
OpenAI-compatible wire protocol against any endpoint — cloud API, Ollama,
vLLM — with zero code changes (LLM backend intentionally unpinned, see
docs/knowledge/agent-orchestration-research.md §1).

    SR_LLM_BASE_URL    base URL, default https://api.openai.com/v1
    SR_LLM_API_KEY     API key, default "" (local endpoints accept empty)
    SR_LLM_MODEL       model id, default gpt-4o-mini
    SR_LLM_MAX_TOKENS  per-turn completion cap, default 1024
    SR_LLM_TEMPERATURE sampling temperature, default 0.2
    SR_LLM_TIMEOUT     HTTP timeout seconds, default 60
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_max_tokens: int
    llm_temperature: float
    llm_timeout: float


def load_config() -> Config:
    """Build Config from environment variables (unknown vars keep defaults)."""

    def env_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        return int(raw) if raw is not None else default

    def env_float(name: str, default: float) -> float:
        raw = os.environ.get(name)
        return float(raw) if raw is not None else default

    return Config(
        llm_base_url=os.environ.get("SR_LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_api_key=os.environ.get("SR_LLM_API_KEY", ""),
        llm_model=os.environ.get("SR_LLM_MODEL", "gpt-4o-mini"),
        llm_max_tokens=env_int("SR_LLM_MAX_TOKENS", 1024),
        llm_temperature=env_float("SR_LLM_TEMPERATURE", 0.2),
        llm_timeout=env_float("SR_LLM_TIMEOUT", 60),
    )
