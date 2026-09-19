"""Application configuration.

Values come from environment variables / `.env` (prefix ``TA_``). A subset is also
editable at runtime from the Settings page; those overrides live in the ``app_settings``
table and are merged on top of these defaults by ``app.services.settings``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProviderName = Literal["ollama", "openai_compat", "fake"]

# Settings a user is allowed to change from the UI at runtime. Anything outside this set
# requires an .env edit and a restart, so that e.g. the database URL cannot be swapped
# out from under a running process.
RUNTIME_OVERRIDABLE: frozenset[str] = frozenset(
    {
        "llm_provider",
        "llm_base_url",
        "llm_model",
        "llm_api_key",
        "llm_temperature",
        "llm_max_tokens",
        "llm_num_ctx",
        "llm_auto_context",
        "llm_thinking",
        "llm_verify_model",
        "llm_timeout",
        "llm_max_repair_attempts",
        "embed_model",
        "embed_device",
        "chunk_tokens",
        "chunk_overlap",
        "retrieve_top_k",
        "ocr_enabled",
        "ocr_languages",
        "default_institution",
        "default_subject",
        "default_grade_level",
    }
)

# Settings whose values must never be echoed back to a client in full.
SECRET_FIELDS: frozenset[str] = frozenset({"llm_api_key"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TA_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- general ----------
    app_name: str = "Teacher Assistant"
    debug: bool = False
    data_dir: Path = Path("./data")
    database_url: str = "sqlite+aiosqlite:///./data/teacher_assistant.db"
    # NoDecode is load-bearing. Without it pydantic-settings tries to JSON-parse any
    # complex type coming from a .env file *before* validators run, so the documented
    # comma-separated form raises a SettingsError and the app will not start at all.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # ---------- language model ----------
    llm_provider: LLMProviderName = "ollama"
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:8b"
    llm_api_key: str = ""
    llm_temperature: float = 0.4
    llm_max_tokens: int = 8192
    #: Ceiling for the context window. The effective value is reduced automatically to
    #: whatever keeps the whole model on the GPU, unless llm_auto_context is off.
    llm_num_ctx: int = 16384
    #: Size the context to the GPU rather than trusting the number above. Off means
    #: "use exactly what I asked for", which is the right choice on a very large card.
    llm_auto_context: bool = True
    #: Let a reasoning model (Qwen3, DeepSeek-R1) write its chain of thought first.
    #:
    #: Off by default, and measured rather than assumed: on an RTX 4060 the same
    #: question-generation call took 47.5s with thinking and 19.9s without, because 550 of
    #: its 666 output tokens were scratchpad that gets stripped before parsing anyway.
    #: This pipeline already externalises the reasoning — it plans an outline, then writes
    #: content against it, then audits the result — so the model's internal deliberation
    #: largely duplicates work already being done. Turn it on to trade time for depth.
    llm_thinking: bool = False
    #: A *different* model to check generated answer keys with. Blank means reuse the
    #: generation model.
    #:
    #: Asking the same weights to re-answer their own question is a weaker check than it
    #: looks: a model that believes the light-dependent reactions happen in the stroma
    #: believes it twice. A second model has different blind spots, which is the entire
    #: point of a second opinion. The cost is that Ollama must swap models in and out of
    #: VRAM, so on a card that cannot hold both this trades roughly twenty seconds per
    #: paper for a materially better check.
    llm_verify_model: str = ""
    llm_timeout: int = 600
    llm_max_repair_attempts: int = 3

    # ---------- embeddings / retrieval ----------
    embed_model: str = "BAAI/bge-small-en-v1.5"
    #: "auto" resolves to cuda when PyTorch sees a GPU with room to spare, else cpu.
    embed_device: str = "auto"
    embed_batch_size: int = 32
    chunk_tokens: int = 512
    chunk_overlap: int = 64
    retrieve_top_k: int = 12

    # ---------- ingestion ----------
    ocr_enabled: bool = True
    ocr_languages: str = "eng"
    max_upload_mb: int = 100

    # ---------- jobs ----------
    worker_concurrency: int = 2

    # ---------- document defaults (used to prefill generation forms) ----------
    default_institution: str = ""
    default_subject: str = ""
    default_grade_level: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("data_dir", mode="after")
    @classmethod
    def _absolute_data_dir(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    # ---------- derived paths ----------
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def parsed_dir(self) -> Path:
        """Docling output (markdown + structured json) for each ingested document."""
        return self.data_dir / "parsed"

    @property
    def vector_dir(self) -> Path:
        return self.data_dir / "vectors"

    @property
    def exports_dir(self) -> Path:
        """Rendered artifacts the user downloads."""
        return self.data_dir / "exports"

    @property
    def media_dir(self) -> Path:
        """Generated diagrams, charts and narration audio."""
        return self.data_dir / "media"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.uploads_dir,
            self.parsed_dir,
            self.vector_dir,
            self.exports_dir,
            self.media_dir,
            self.cache_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Repository root (the directory holding backend/, frontend/, templates/).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "templates"
