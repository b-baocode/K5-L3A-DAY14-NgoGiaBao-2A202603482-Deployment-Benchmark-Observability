"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

• `redis.from_url` chỉ tạo connection pool, KHÔNG kết nối ngay → app vẫn khởi động được
  khi Redis chưa sẵn sàng (container chạy một mình, Redis khởi động chậm hơn...).
• Timeout ngắn để /ready và /ask trả lỗi nhanh khi Redis chết, thay vì treo request.
"""
import redis

from app.config import settings

r = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=2,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: ping được Redis → True, lỗi kết nối → False."""
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
