"""Configuration: config.yaml (behaviour) + .env (secrets/connections)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class ExtractorConfig(BaseModel):
    enabled: bool = True
    method: str = "text"  # vision | text
    provider: str = "default"
    model: str = "default"
    prompt: str = "extractor_text.v1"


class ConsensusConfig(BaseModel):
    fuzzy_threshold: int = 90
    critical_fields: list[str] = Field(default_factory=lambda: [
        "total_amount", "currency", "vendor_name", "invoice_number",
        "matter_number", "bank_account", "swift_bic"])
    line_items_critical: bool = True


class ValidationConfig(BaseModel):
    duplicate_window_days: int = 30
    max_invoice_age_days: int = 365
    amount_tolerance_minor_units: int = 1


class AnomalyConfig(BaseModel):
    percentile: float = 95
    z_score: float = 3.0
    cold_start_min: int = 5
    global_max_total_usd: float = 50000
    rate_spike_pct: float = 30
    frequency_window_days: int = 30
    frequency_multiplier: float = 2.0
    frequency_min_count: int = 3
    similar_invoices_k: int = 5


class EmbeddingConfig(BaseModel):
    provider: str = "local"
    dim: int = 384


class SummaryConfig(BaseModel):
    use_llm: bool = True


class PdfConfig(BaseModel):
    render_dpi: int = 150
    max_pages: int = 5


class Settings(BaseModel):
    base_currency: str = "USD"
    max_concurrent_invoices: int = 4
    extractors: dict[str, ExtractorConfig] = Field(default_factory=dict)
    consensus: ConsensusConfig = ConsensusConfig()
    validation: ValidationConfig = ValidationConfig()
    anomaly: AnomalyConfig = AnomalyConfig()
    embeddings: EmbeddingConfig = EmbeddingConfig()
    summary: SummaryConfig = SummaryConfig()
    pdf: PdfConfig = PdfConfig()

    # --- from environment ---
    @property
    def db_backend(self) -> str:
        return os.getenv("DB_BACKEND", "oracle").lower()

    @property
    def llm_provider(self) -> str:
        return os.getenv("LLM_PROVIDER", "mock").lower()

    @property
    def upload_dir(self) -> Path:
        p = Path(os.getenv("UPLOAD_DIR", "uploads"))
        return p if p.is_absolute() else ROOT / p

    def enabled_extractors(self) -> dict[str, ExtractorConfig]:
        return {k: v for k, v in self.extractors.items() if v.enabled}


def _config_path() -> Path:
    return Path(os.getenv("CORI_CONFIG", ROOT / "config.yaml"))


@lru_cache
def get_settings() -> Settings:
    path = _config_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return Settings.model_validate(data or {})


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
