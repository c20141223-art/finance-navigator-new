"""Loads config/sources.yaml. No hardcoded endpoints or tuning numbers here —
if you're about to add a magic number to a fetcher, it belongs in the YAML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"


@dataclass(frozen=True)
class HttpConfig:
    timeout_seconds: float
    min_interval_seconds: float
    max_retries: int
    retry_backoff_seconds: float


@dataclass(frozen=True)
class DataConfig:
    min_backfill_trading_days: int
    inactive_after_missing_days: int


@dataclass(frozen=True)
class SourcesConfig:
    http: HttpConfig
    data: DataConfig
    urls: dict[str, str]

    def url(self, name: str) -> str:
        try:
            return self.urls[name]
        except KeyError:
            raise KeyError(
                f"config/sources.yaml 未定義來源 '{name}'。"
                f" 可用來源: {sorted(self.urls)}"
            ) from None


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> SourcesConfig:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    http = HttpConfig(**raw["http"])
    data = DataConfig(**raw["data"])
    urls = dict(raw["sources"])
    return SourcesConfig(http=http, data=data, urls=urls)
