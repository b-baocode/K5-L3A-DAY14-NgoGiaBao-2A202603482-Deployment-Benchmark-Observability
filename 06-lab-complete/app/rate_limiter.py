"""
Rate limiting (tiêu chí 7) — sliding window 60 giây bằng Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây gần nhất
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis → mọi worker / instance dùng chung, còn nguyên sau khi restart

Sorted set `ratelimit:<hash(key)>`: mỗi request là 1 member, score = thời điểm (giây).
"""
import hashlib
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _bucket(key: str) -> str:
    # Không lưu API key gốc trong Redis — chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:24]


def check_rate_limit(key: str) -> dict:
    """Ghi nhận 1 request của `key`; vượt giới hạn → raise HTTPException(429)."""
    limit = settings.rate_limit_per_minute
    bucket = _bucket(key)
    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"

    # MULTI/EXEC: 4 lệnh chạy liền một khối, request khác không chen vào giữa
    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(bucket, 0, now - WINDOW_SECONDS)   # 1. xoá request cũ hơn 60s
    pipe.zadd(bucket, {member: now})                         # 2. thêm request hiện tại
    pipe.zcard(bucket)                                       # 3. đếm số request trong cửa sổ
    pipe.expire(bucket, WINDOW_SECONDS + 1)                  # 4. key tự xoá khi user ngừng gọi
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(bucket, member)  # request bị từ chối không tính vào quota
        oldest = r.zrange(bucket, 0, 0, withscores=True)
        retry_after = int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded", "limit": limit,
                    "window_seconds": WINDOW_SECONDS, "retry_after_seconds": retry_after},
            headers={"Retry-After": str(max(retry_after, 1)),
                     "X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
        )
    return {"limit": limit, "remaining": limit - count}
