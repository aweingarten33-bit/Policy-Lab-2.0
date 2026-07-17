"""
Application configuration — reads from environment variables.
API keys are NEVER hard-coded. All secrets come from .env or the runtime environment.
Supports multiple LLM providers via LiteLLM cascade fallback.
"""

from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    # ── API Security ──
    api_key: str = ""

    # ── Provider API Keys (set whichever you have — cascade uses all available) ──
    groq_api_key: str = ""           # console.groq.com — free, fast, high limits
    gemini_api_key: str = ""         # aistudio.google.com — free tier
    mistral_api_key: str = ""        # console.mistral.ai — free tier
    openrouter_api_key: str = ""     # openrouter.ai — free models available
    anthropic_api_key: str = ""      # console.anthropic.com — paid, best quality
    openai_api_key: str = ""         # platform.openai.com — paid

    # ── Model Configuration ──
    llm_max_tokens: int = 4000        # gap analysis
    llm_max_tokens_long: int = 4000   # rewrite, action plan, board summary, etc.

    # ── Legacy fields (kept for backward compat) ──
    llm_primary_model: str = "gemini/gemini-2.0-flash"
    llm_fallback_model: str = "gemini/gemini-2.0-flash"
    llm_provider_mode: str = "auto"

    # ── App Settings ──
    cors_origins: str = "*"
    host: str = "0.0.0.0"
    port: int = 8000
    environment: str = "development"

    # ── Knowledge Base Settings ──
    kb_persist_dir: str = "./knowledge_base"
    kb_auto_seed: bool = True
    kb_enabled: bool = True

    # ── Live Research Settings ──
    live_research_enabled: bool = True
    live_research_max_results: int = 5

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @property
    def llm_cascade_models(self) -> List[str]:
        """
        Returns the ordered list of models to try, based on which API keys are set.
        Order: Claude Opus 4.8 (primary — best calibration/lowest hallucination rate of
        any flagship model, which matters most for citation-grounded compliance findings)
        → OpenAI → Groq → Gemini Flash → Mistral Small → OpenRouter free tier.
        Models whose keys are missing are skipped automatically.
        """
        cascade = []
        if self.anthropic_api_key:
            cascade.append("anthropic/claude-opus-4-8")              # primary — best-calibrated, lowest hallucination rate
        if self.openai_api_key:
            cascade.append("gpt-4o-mini")                            # fallback — fast, handles everything
        if self.groq_api_key:
            cascade.append("groq/llama-3.3-70b-versatile")           # fallback
        if self.gemini_api_key:
            cascade.append("gemini/gemini-2.0-flash")                # fallback
        if self.mistral_api_key:
            cascade.append("mistral/mistral-small-latest")           # fallback
        if self.openrouter_api_key:
            cascade.append("openrouter/meta-llama/llama-3.3-70b-instruct:free")
        # Always have at least one model to try
        if not cascade:
            cascade.append("gemini/gemini-2.0-flash")
        return cascade

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    # ── Backward compat properties ──
    @property
    def claude_model(self) -> str:
        return self.llm_primary_model

    @property
    def claude_max_tokens(self) -> int:
        return self.llm_max_tokens


settings = Settings()
