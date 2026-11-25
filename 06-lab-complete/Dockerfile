# ============================================================
# Production Dockerfile — multi-stage, slim, non-root
# Build:  docker build -t day14-agent .
# Run:    docker run -p 8000:8000 -e AGENT_API_KEY=... -e REDIS_URL=... day14-agent
# ============================================================

# ── Stage 1: builder — cài dependencies vào một virtualenv riêng ──
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
# Copy requirements TRƯỚC code → sửa code không làm mất cache của bước pip install
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r requirements.txt


# ── Stage 2: runtime — chỉ chứa Python + venv + code ──
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# User không có quyền root, không có home, không login được
RUN groupadd --system app && useradd --system --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app
EXPOSE 8000

# Docker tự gọi /health; fail 3 lần liên tiếp → container bị đánh dấu unhealthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT', '8000'), timeout=3)" || exit 1

# `exec` thay process sh bằng uvicorn → uvicorn là PID 1, nhận SIGTERM trực tiếp khi `docker stop`.
# PORT do platform cấp (Railway/Render), mặc định 8000.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
