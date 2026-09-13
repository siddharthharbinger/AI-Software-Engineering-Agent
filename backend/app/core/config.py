from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "insecure-dev-secret-key-change-in-production"

    # LLM Router Settings
    LLM_PROVIDER_PRIORITY: str = "ollama,groq,openrouter,gemini"

    # Provider: Ollama (Local)
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "qwen2.5-coder:14b"
    OLLAMA_API_KEY: str = "ollama"  # Ollama doesn't require a key, dummy key works for compat

    # Provider: Groq
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Provider: OpenRouter
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "openrouter/free"

    # Provider: Gemini
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # Circuit Breakers
    CIRCUIT_DEFAULT_COOLDOWN_SECONDS: float = 30.0
    CIRCUIT_RATE_LIMIT_COOLDOWN_SECONDS: float = 60.0
    LLM_TIMEOUT_SECONDS: float = 45.0

    # Storage & Queues
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_platform"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Integrations
    GITHUB_TOKEN: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""
    GITEA_URL: str = "http://localhost:3000"
    GITEA_TOKEN: str = ""

    JIRA_BASE_URL: str = ""
    JIRA_USER_EMAIL: str = ""
    JIRA_API_TOKEN: str = ""
    JIRA_PROJECT_KEY: str = ""
    JIRA_POLL_INTERVAL_SECONDS: int = 30

    # Sandbox
    SANDBOX_RUNTIME: str = "runc"
    SANDBOX_CPU_LIMIT: float = 2.0
    SANDBOX_MEMORY_LIMIT: str = "2g"
    SANDBOX_PIDS_LIMIT: int = 100
    SANDBOX_TIMEOUT_SECONDS: int = 300
    SANDBOX_MAX_FILES_TOUCHED: int = 50

    @property
    def provider_priority_list(self) -> List[str]:
        return [p.strip().lower() for p in self.LLM_PROVIDER_PRIORITY.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
