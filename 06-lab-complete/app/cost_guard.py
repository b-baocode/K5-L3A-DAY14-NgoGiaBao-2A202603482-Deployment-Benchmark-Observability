"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, theo ngày (UTC) và theo tháng
  • Tổng chi phí trong ngày ≥ DAILY_BUDGET_USD (hoặc trong tháng ≥ MONTHLY_BUDGET_USD) → HTTP 402

Key Redis: `cost:day:2026-11-25`, `cost:month:2026-11` — sang ngày/tháng mới là key mới,
nên ngân sách tự reset mà không cần cron job; key cũ tự hết hạn (EXPIRE).
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini) — có thể ghi đè bằng env PRICE_*_PER_1K
PRICE_INPUT_PER_1K = settings.price_input_per_1k
PRICE_OUTPUT_PER_1K = settings.price_output_per_1k


def _keys() -> tuple:
    now = datetime.now(timezone.utc)
    return f"cost:day:{now:%Y-%m-%d}", f"cost:month:{now:%Y-%m}"


def estimate_tokens(text: str) -> int:
    """Mock LLM không trả số token → ước lượng ~4 ký tự / token (xấp xỉ tokenizer của OpenAI)."""
    return max(1, len(text) // 4)


def current_spend() -> dict:
    day_key, month_key = _keys()
    day, month = r.mget(day_key, month_key)
    return {"day_usd": float(day or 0), "month_usd": float(month or 0)}


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    spend = current_spend()
    over_day = spend["day_usd"] >= settings.daily_budget_usd
    over_month = settings.monthly_budget_usd > 0 and spend["month_usd"] >= settings.monthly_budget_usd
    if over_day or over_month:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Budget exceeded",
                "period": "day" if over_day else "month",
                "spent_usd": round(spend["day_usd"] if over_day else spend["month_usd"], 6),
                "budget_usd": settings.daily_budget_usd if over_day else settings.monthly_budget_usd,
            },
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K
    day_key, month_key = _keys()
    pipe = r.pipeline(transaction=True)
    pipe.incrbyfloat(day_key, cost)        # cộng nguyên tử phía Redis: nhiều worker cùng ghi không mất số
    pipe.expire(day_key, 2 * 24 * 3600)
    pipe.incrbyfloat(month_key, cost)
    pipe.expire(month_key, 32 * 24 * 3600)
    pipe.execute()
    return cost
