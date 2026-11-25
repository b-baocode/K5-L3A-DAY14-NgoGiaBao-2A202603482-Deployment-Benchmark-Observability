"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

Thứ tự ưu tiên: biến môi trường thật  >  file .env (chỉ để dev local)  >  giá trị mặc định.
Không có secret nào được hard-code: AGENT_API_KEY mặc định rỗng → mọi request /ask bị 401,
và khi ENVIRONMENT=production mà thiếu key thì app dừng ngay lúc khởi động (fail fast).
"""
import os
from dataclasses import dataclass, field

try:  # .env chỉ dùng khi chạy local; không ghi đè biến môi trường đã có (Docker, Railway...)
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # python-dotenv là tuỳ chọn
    pass


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # Server
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    app_version: str = field(default_factory=lambda: os.getenv("APP_VERSION", "1.0.0"))

    # Security
    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))

    # Redis — state dùng chung (rate limit, cost)
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    # Rate limit: số request tối đa / 60 giây cho mỗi API key
    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))

    # Cost guard (USD). MONTHLY_BUDGET_USD = 0 nghĩa là không giới hạn theo tháng
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))
    monthly_budget_usd: float = field(default_factory=lambda: _env_float("MONTHLY_BUDGET_USD", 0.0))
    price_input_per_1k: float = field(default_factory=lambda: _env_float("PRICE_INPUT_PER_1K", 0.00015))
    price_output_per_1k: float = field(default_factory=lambda: _env_float("PRICE_OUTPUT_PER_1K", 0.0006))

    def validate(self) -> "Settings":
        if self.environment == "production" and not self.agent_api_key:
            raise RuntimeError("AGENT_API_KEY must be set when ENVIRONMENT=production")
        if self.rate_limit_per_minute < 1:
            raise RuntimeError("RATE_LIMIT_PER_MINUTE must be >= 1")
        return self


settings = Settings().validate()
