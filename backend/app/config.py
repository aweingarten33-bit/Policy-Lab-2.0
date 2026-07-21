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
    llm_max_tokens: int = 24000       # gap analysis
    llm_max_tokens_long: int = 24000  # rewrite, action plan, board summary, etc.

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

    def _build_cascade(self, preference_order: List[tuple]) -> List[str]:
        """preference_order: list of (api_key_attr_name, model_string), most preferred first.
        Entries whose key isn't set are skipped automatically."""
        cascade = [model for key_attr, model in preference_order if getattr(self, key_attr)]
        if not cascade:
            cascade.append("gemini/gemini-2.0-flash")
        return cascade

    @property
    def llm_cascade_models(self) -> List[str]:
        """
        Cascade for gap analysis / chat / everything except drafting. Claude
        primary — citation accuracy and audit-grade reasoning matter most here,
        and Analyze already streams progress so its wait is less painful.
        """
        return self._build_cascade([
            ("anthropic_api_key", "anthropic/claude-haiku-4-5-20251001"),
            ("openai_api_key", "gpt-4o-mini"),
            ("groq_api_key", "groq/llama-3.3-70b-versatile"),
            ("gemini_api_key", "gemini/gemini-2.0-flash"),
            ("mistral_api_key", "mistral/mistral-small-latest"),
            ("openrouter_api_key", "openrouter/meta-llama/llama-3.3-70b-instruct:free"),
        ])

    @property
    def llm_cascade_models_draft(self) -> List[str]:
        """
        Cascade for policy drafting specifically. Gemini primary — fastest of
        the three paid providers and the one Harvey (comparable legal-AI
        product) reports as their strongest model for drafting specifically.
        Retrieval/live-research context is identical regardless of which
        model ends up writing; only the writing step itself changes.
        """
        return self._build_cascade([
            ("gemini_api_key", "gemini/gemini-2.0-flash"),
            ("anthropic_api_key", "anthropic/claude-haiku-4-5-20251001"),
            ("openai_api_key", "gpt-4o-mini"),
            ("groq_api_key", "groq/llama-3.3-70b-versatile"),
            ("mistral_api_key", "mistral/mistral-small-latest"),
            ("openrouter_api_key", "openrouter/meta-llama/llama-3.3-70b-instruct:free"),
        ])

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
