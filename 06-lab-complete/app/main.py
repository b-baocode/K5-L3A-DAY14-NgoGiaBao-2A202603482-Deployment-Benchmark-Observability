"""
Production AI Agent — Part 6 Final Project.

Luồng của POST /ask:
    X-API-Key (401) → rate limit (429) → budget (402) → LLM → ghi chi phí → 200

Chạy:  uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Kiểm tra:  python check_production_ready.py
"""
import json
import logging
import signal
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log = 1 JSON object
# ─────────────────────────────────────────────────────────
_STD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Ghi mọi LogRecord thành một dòng JSON; field truyền qua `extra=` được giữ nguyên."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)  # traceback chỉ nằm trong log
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    # force=True: ghi đè cấu hình có sẵn (tránh lỗi basicConfig bị bỏ qua đã gặp ở Part 1)
    logging.basicConfig(level=settings.log_level, handlers=[handler], force=True)
    return logging.getLogger("agent")


logger = _setup_logging()

START_TIME = time.time()
_state = {"shutting_down": False}


# ─────────────────────────────────────────────────────────
# Graceful shutdown (tiêu chí 11)
# uvicorn tự xử lý SIGTERM: ngừng nhận kết nối mới → chờ request đang chạy xong →
# chạy phần shutdown của lifespan → thoát với exit code 0.
# Handler dưới đây chỉ ghi log rồi GỌI LẠI handler cũ, để không chặn mất cơ chế của uvicorn.
# ─────────────────────────────────────────────────────────
_previous_sigterm = signal.getsignal(signal.SIGTERM)


def _on_sigterm(signum, frame):
    _state["shutting_down"] = True
    logger.info("sigterm received", extra={"event": "sigterm"})
    if callable(_previous_sigterm):
        _previous_sigterm(signum, frame)


signal.signal(signal.SIGTERM, _on_sigterm)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("agent starting", extra={
        "event": "startup", "env": settings.environment, "version": settings.app_version,
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "daily_budget_usd": settings.daily_budget_usd,
    })
    if not settings.agent_api_key:
        logger.warning("AGENT_API_KEY is empty — every /ask request will get 401",
                       extra={"event": "config_warning"})
    yield
    _state["shutting_down"] = True  # /ready → 503 để load balancer ngừng gửi traffic mới
    r.close()
    logger.info("graceful shutdown complete", extra={"event": "shutdown",
                                                      "uptime_s": round(time.time() - START_TIME, 1)})


app = FastAPI(title="Production AI Agent", version=settings.app_version, lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


# ─────────────────────────────────────────────────────────
# Middleware: request id + access log JSON + chặn lộ stack trace
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def access_log(request: Request, call_next):
    # Giới hạn độ dài: header do client gửi, không tin tưởng (tránh log bị bơm dữ liệu rác)
    request_id = (request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16])[:64]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:  # lỗi không lường trước: ghi traceback vào log, client chỉ nhận JSON gọn
        logger.exception("unhandled error", extra={"event": "error", "request_id": request_id,
                                                    "path": request.url.path})
        response = JSONResponse(status_code=500,
                                content={"detail": "Internal server error", "request_id": request_id})
    response.headers["X-Request-ID"] = request_id
    logger.info("request", extra={
        "event": "request", "request_id": request_id, "method": request.method,
        "path": request.url.path, "status": response.status_code,
        "ms": round((time.perf_counter() - started) * 1000, 1),
    })
    return response


@app.exception_handler(redis.RedisError)
async def redis_unavailable(request: Request, exc: redis.RedisError):
    """Redis chết giữa chừng → 503 rõ ràng thay vì 500 + stack trace."""
    logger.error("redis unavailable", extra={"event": "redis_error", "error": type(exc).__name__,
                                             "path": request.url.path})
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


# ─────────────────────────────────────────────────────────
# Health & readiness (tiêu chí 4, 5)
# ─────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Liveness: process còn sống là 200 — KHÔNG phụ thuộc Redis (Redis chết thì restart agent cũng vô ích).

    `async def` vì không có I/O chặn: chạy thẳng trên event loop, không phải xếp hàng chờ threadpool
    cùng các request /ask chậm → health check vẫn trả lời nhanh khi tải cao.
    """
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
    }


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được và agent không đang tắt."""
    if _state["shutting_down"]:
        raise HTTPException(status_code=503, detail="Shutting down")
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Redis not reachable")
    return {"ready": True}


# ─────────────────────────────────────────────────────────
# Agent endpoint (tiêu chí 6, 7, 8)
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)  # "   " → "" → bị min_length chặn (422)

    question: str = Field(..., min_length=1, max_length=2000)
    user_id: Optional[str] = Field(default=None, max_length=128)


# `def` (không phải `async def`): mock LLM và client Redis đều là code chặn (blocking), FastAPI sẽ
# chạy hàm này trong threadpool → event loop không bị đứng, nhiều request chạy song song,
# và SIGTERM không làm rơi request đang chờ (bài học Part 5).
@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    quota = check_rate_limit(api_key)   # 429
    check_budget()                      # 402

    started = time.perf_counter()
    answer = llm_ask(body.question)
    llm_ms = round((time.perf_counter() - started) * 1000, 1)

    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    logger.info("llm call", extra={"event": "llm_call", "user_id": body.user_id, "llm_ms": llm_ms,
                                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                                    "cost_usd": round(cost, 8)})
    return {
        "question": body.question,
        "answer": answer,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
            "requests_remaining": quota["remaining"],
        },
    }
