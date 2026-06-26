"""Configuration loader for DSA-HK. Reads .env and validates required keys."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Project root = parent of src/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def _load_env() -> None:
    """Load .env from project root if present."""
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)
    else:
        # Try example file (so import never crashes for newcomers)
        example = PROJECT_ROOT / ".env.example"
        if example.exists():
            load_dotenv(example, override=False)


@dataclass
class Config:
    """Validated runtime configuration."""

    # LLM
    minimax_api_key: Optional[str] = None
    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M3"
    gemini_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: Optional[str] = None
    litellm_model: str = "openai/MiniMax-M3"
    report_language: str = "zh-Hant"

    # HK data
    futu_host: str = "127.0.0.1"
    futu_port: int = 11111

    # News
    tavily_api_key: Optional[str] = None
    bocha_api_key: Optional[str] = None
    brave_api_key: Optional[str] = None

    # Ticker universe
    radar_path: str = "/Users/kenken/Documents/Gstack/trading-platform/docs/curated-radar.json"
    hk_tickers_override: Optional[str] = None
    us_tickers_override: Optional[str] = None
    max_tickers: int = 0

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_message_thread_id: Optional[int] = None

    # Storage
    database_path: str = "./data/dsa_hk.db"
    reports_dir: str = "./reports"
    log_dir: str = "./logs"

    # Scheduler
    schedule_time: str = "18:00"
    schedule_enabled: bool = True
    skip_non_trading_days: bool = True

    # Server
    webui_port: int = 8200
    log_level: str = "INFO"

    # Computed paths (absolute)
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)

    def has_llm_key(self) -> bool:
        return any([
            self.minimax_api_key,
            self.gemini_api_key,
            self.deepseek_api_key,
            self.openai_api_key,
        ])

    def has_news_key(self) -> bool:
        # DuckDuckGo is the always-on no-key fallback, so news is always available
        return True

    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def available_llm_providers(self) -> list[str]:
        """List LLM providers with keys configured."""
        providers = []
        if self.minimax_api_key:
            providers.append("minimax")
        if self.gemini_api_key:
            providers.append("gemini")
        if self.deepseek_api_key:
            providers.append("deepseek")
        if self.openai_api_key:
            providers.append("openai")
        return providers

    def resolve_litellm_model(self) -> str:
        """Resolve the litellm model string. If user only set a bare model name,
        and we have MiniMax key but no openai key, route via MiniMax's OpenAI-compatible API.
        """
        model = self.litellm_model
        # If model has a provider prefix, use as-is
        if "/" in model:
            return model
        # Bare model name — route based on available keys
        if self.minimax_api_key:
            # Route via MiniMax OpenAI-compatible protocol
            return f"openai/{model}"
        if self.openai_api_key:
            return f"openai/{model}"
        return model

    def resolve_llm_call_kwargs(self) -> dict:
        """Return kwargs to merge into litellm.completion() for the active provider.
        Note: `model` is NOT included — caller passes it explicitly to avoid double-kwarg errors.
        """
        model = self.resolve_litellm_model()
        kwargs = {}
        if model.startswith("openai/MiniMax-M") or model.startswith("openai/minimax"):
            # MiniMax via OpenAI-compatible protocol
            kwargs["api_key"] = self.minimax_api_key
            kwargs["api_base"] = self.minimax_base_url
        elif model.startswith("openai/") and self.minimax_api_key and not self.openai_api_key:
            # Bare openai/<model> with MiniMax key only — route to MiniMax
            kwargs["api_key"] = self.minimax_api_key
            kwargs["api_base"] = self.minimax_base_url
        return kwargs

    def available_news_sources(self) -> list[str]:
        """List news sources with keys configured."""
        sources = []
        # Key-based sources (preferred)
        if self.bocha_api_key:
            sources.append("bocha")
        if self.tavily_api_key:
            sources.append("tavily")
        if self.brave_api_key:
            sources.append("brave")
        # DuckDuckGo is always available (no key)
        sources.append("duckduckgo")
        return sources

    def warnings(self) -> list[str]:
        """Return non-fatal config warnings."""
        warnings = []
        if not self.has_llm_key():
            warnings.append(
                "No LLM API key configured — analysis will fail. "
                "Set MINIMAX_API_KEY, GEMINI_API_KEY, DEEPSEEK_API_KEY, or OPENAI_API_KEY in .env"
            )
        # News is always available via DuckDuckGo (no key needed).
        # Optional premium sources for better quality: TAVILY_API_KEY / BOCHA_API_KEY.
        if not Path(self.radar_path).exists() and not self.hk_tickers_override:
            warnings.append(
                f"Radar file not found: {self.radar_path}. "
                "Set RADAR_PATH or HK_TICKERS_OVERRIDE in .env"
            )
        return warnings


def load_config() -> Config:
    """Build validated Config from environment."""
    _load_env()

    cfg = Config(
        minimax_api_key=os.getenv("MINIMAX_API_KEY") or None,
        minimax_base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
        minimax_model=os.getenv("MINIMAX_MODEL", "MiniMax-M3"),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
        openai_model=os.getenv("OPENAI_MODEL") or None,
        litellm_model=os.getenv("LITELLM_MODEL", "openai/MiniMax-M3"),
        report_language=os.getenv("REPORT_LANGUAGE", "zh-Hant"),
        futu_host=os.getenv("FUTU_HOST", "127.0.0.1"),
        futu_port=int(os.getenv("FUTU_PORT", "11111")),
        tavily_api_key=os.getenv("TAVILY_API_KEY") or None,
        bocha_api_key=os.getenv("BOCHA_API_KEY") or None,
        brave_api_key=os.getenv("BRAVE_API_KEY") or None,
        radar_path=os.getenv(
            "RADAR_PATH",
            "/Users/kenken/Documents/Gstack/trading-platform/docs/curated-radar.json",
        ),
        hk_tickers_override=os.getenv("HK_TICKERS_OVERRIDE") or None,
        us_tickers_override=os.getenv("US_TICKERS_OVERRIDE") or None,
        max_tickers=int(os.getenv("MAX_TICKERS", "0")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        telegram_message_thread_id=int(os.getenv("TELEGRAM_MESSAGE_THREAD_ID", "0")) or None,
        database_path=os.getenv("DATABASE_PATH", "./data/dsa_hk.db"),
        reports_dir=os.getenv("REPORTS_DIR", "./reports"),
        log_dir=os.getenv("LOG_DIR", "./logs"),
        schedule_time=os.getenv("SCHEDULE_TIME", "18:00"),
        schedule_enabled=os.getenv("SCHEDULE_ENABLED", "true").lower() == "true",
        skip_non_trading_days=os.getenv("SKIP_NON_TRADING_DAYS", "true").lower() == "true",
        webui_port=int(os.getenv("WEBUI_PORT", "8200")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )

    # Resolve relative paths to absolute (relative to project root)
    for attr in ("database_path", "reports_dir", "log_dir"):
        p = Path(getattr(cfg, attr))
        if not p.is_absolute():
            setattr(cfg, attr, str((PROJECT_ROOT / p).resolve()))

    # Create directories
    Path(cfg.database_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.reports_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.log_dir).mkdir(parents=True, exist_ok=True)

    return cfg


# Singleton
_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


if __name__ == "__main__":
    cfg = get_config()
    print(f"LITELLM_MODEL: {cfg.litellm_model}")
    print(f"Resolved model: {cfg.resolve_litellm_model()}")
    print(f"REPORT_LANGUAGE: {cfg.report_language}")
    print(f"LLM providers: {cfg.available_llm_providers() or 'NONE'}")
    print(f"News sources: {cfg.available_news_sources() or 'NONE'}")
    print(f"Telegram: {'configured' if cfg.has_telegram() else 'not configured'}")
    print(f"Database: {cfg.database_path}")
    print(f"Reports dir: {cfg.reports_dir}")
    print()
    if cfg.warnings():
        print("Warnings:")
        for w in cfg.warnings():
            print(f"  - {w}")
        sys.exit(1)
    else:
        print("OK: all required config present")
