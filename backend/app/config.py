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
    # Separate credential for destructive knowledge-base operations (ingest,
    # seed, delete). Deliberately has no default: if it isn't set, those
    # endpoints are disabled entirely rather than falling back to api_key,
    # so read access can never imply the ability to rewrite what every
    # future generation is grounded in.
    admin_api_key: str = ""

    # Maximum characters accepted on any field that flows into a paid LLM
    # call. Without a cap, a single request can run up an unbounded bill.
    max_input_chars: int = 100_000

    # Refuse to generate when NEITHER the knowledge base nor live research
    # returned a single source. The product's claim is that output is checked
    # against authoritative regulatory text; producing a normal-looking,
    # citation-formatted analysis with nothing behind it is the most damaging
    # failure mode available to it.
    #
    # DEFAULT IS OFF, deliberately, and this should be turned on:
    #   Set REQUIRE_GROUNDING=true once GET /api/health reports
    #   "kb_grounded": true on the live deployment.
    #
    # Why not on by default: grounding needs either a populated knowledge base
    # or working live research (which needs TAVILY_API_KEY). If both are
    # unavailable in a given deployment, enabling this turns every request into
    # a 503 and the app stops working entirely. Shipping that as the default,
    # while the production knowledge base is known to be empty, would trade a
    # silent-correctness problem for a total outage. Fix the grounding first,
    # confirm it, then enforce it.
    require_grounding: bool = False

    # ── Provider API Keys (set whichever you have — cascade uses all available) ──
    groq_api_key: str = ""           # console.groq.com — free, fast, high limits
    gemini_api_key: str = ""         # aistudio.google.com — free tier
    mistral_api_key: str = ""        # console.mistral.ai — free tier
    openrouter_api_key: str = ""     # openrouter.ai — free models available
    anthropic_api_key: str = ""      # console.anthropic.com — paid, best quality
    openai_api_key: str = ""         # platform.openai.com — paid

    # ── Model Configuration ──
    llm_max_tokens: int = 12000        # gap analysis
    llm_max_tokens_long: int = 12000   # rewrite, action plan, board summary, etc.

    # ── App Settings ──
    # Locked to the production domain + local dev by default. Frontend and
    # backend are served from the same origin in production (single Docker
    # image), so this only matters for cross-origin callers, not the app
    # itself. Override via the CORS_ORIGINS env var (comma-separated) if
    # the Render domain changes or another origin needs access.
    cors_origins: str = "https://policy-lab-2-0.onrender.com,http://localhost:5173,http://localhost:8080"
    host: str = "0.0.0.0"
    port: int = 8000
    environment: str = "development"

    # ── Knowledge Base Settings ──
    # NOTE ON PERSISTENCE: this default writes inside the container, and Render's
    # filesystem is ephemeral — anything here is lost on every deploy and
    # restart, so the knowledge base has to re-seed from eCFR each time and is
    # empty until that succeeds. To make it survive, attach a Render persistent
    # disk (mount path e.g. /var/data) and set:
    #     KB_PERSIST_DIR=/var/data/policy-lab-kb
    # Only paths under the mounted disk persist. A local disk also pins the
    # service to a single instance, which is fine at this stage; a hosted vector
    # store is the answer if this ever needs to scale horizontally.
    kb_persist_dir: str = "./knowledge_base"
    kb_auto_seed: bool = True
    # Total wall-clock budget for seeding every CFR target. Generous on
    # purpose: exceeding it leaves the knowledge base empty, which is the
    # single worst outcome for this product.
    kb_seed_timeout_seconds: int = 600
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
        Cascade for gap analysis / chat / everything except drafting. Sonnet 5
        primary -- Haiku was faster but proved unreliable at actually obeying
        the prompt's length constraints, causing repeated max_tokens failures
        even after tightening them twice. Sonnet follows instructions more
        precisely, so far fewer wasted 1-3 minute waits that end in failure.
        Citation accuracy and audit-grade reasoning matter most here anyway,
        and Analyze already streams progress so its wait is less painful.
        """
        return self._build_cascade([
            ("anthropic_api_key", "anthropic/claude-sonnet-5"),
            ("openai_api_key", "gpt-4o-mini"),
            ("groq_api_key", "groq/llama-3.3-70b-versatile"),
            ("gemini_api_key", "gemini/gemini-2.0-flash"),
            ("mistral_api_key", "mistral/mistral-small-latest"),
            ("openrouter_api_key", "openrouter/meta-llama/llama-3.3-70b-instruct:free"),
        ])

    @property
    def llm_cascade_models_draft(self) -> List[str]:
        """
        Cascade for policy drafting. Sonnet 5 primary — same tier as gap
        analysis. Used to prefer Gemini/Haiku for speed, but with no Gemini
        key configured that fallback logic never actually ran; it was Haiku
        4.5 the whole time. A drafted policy is meant to hold up when run
        back through gap analysis, so it gets the same model tier doing the
        writing as the one doing the reviewing, not a lighter one.
        """
        return self._build_cascade([
            ("anthropic_api_key", "anthropic/claude-sonnet-5"),
            ("gemini_api_key", "gemini/gemini-2.0-flash"),
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


settings = Settings()
