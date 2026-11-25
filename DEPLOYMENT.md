# Deployment Information

## Public URL
https://k5-l3a-day14-ngogiabao-2a202603482.onrender.com

## Platform
**Render** — Web Service (runtime Docker, gói Free) + Key Value (Redis, gói Free).

- Source: GitHub repo này, branch `main`, Root Directory `06-lab-complete` (build bằng `06-lab-complete/Dockerfile`)
- Health Check Path: `/health`
- Auto-Deploy: mỗi lần push lên `main`
- Web service và Key Value cùng region → agent kết nối Redis qua Internal URL (không public ra Internet)

> Tài khoản Railway đã hết hạn dùng thử (Trial expired, mọi deployment bị tạm dừng) nên dùng Render làm nền tảng chính — RUBRIC cho phép Railway **hoặc** Render.
> Gói Free của Render tự ngủ sau ~15 phút không có request: request đầu tiên sau đó có thể mất 30–60 giây, các request tiếp theo bình thường.

## Test Commands

Đặt biến trước khi chạy (key lấy từ Render → Environment, không ghi vào repo):

```bash
BASE=https://k5-l3a-day14-ngogiabao-2a202603482.onrender.com
AGENT_API_KEY=<key đặt trên Render>
```

### 4 lệnh tự kiểm (CP6) — chỉ in mã HTTP, không lộ key
```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả (25/11/2026):
```
200
200
401
200
```

### Health check
```bash
curl "$BASE/health"
# {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":197.8}
```

### API test (with authentication)
```bash
curl -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.",
#  "usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05,"requests_remaining":8}}
```

### Rate limit (10 request / phút)
```bash
for i in $(seq 1 12); do
  curl -sS -o /dev/null -w '%{http_code} ' -X POST "$BASE/ask" \
    -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"hi"}'
done
# 200 200 200 200 200 200 200 200 429 429 429 429     (2 request trước đó đã dùng quota)
# HTTP/1.1 429 Too Many Requests — retry-after: 55, x-ratelimit-limit: 10, x-ratelimit-remaining: 0
```

### Script chấm với URL public
```bash
cd 06-lab-complete
python check_production_ready.py --public-url https://k5-l3a-day14-ngogiabao-2a202603482.onrender.com
#  ✅ Public URL: GET https://k5-l3a-day14-ngogiabao-2a202603482.onrender.com/health → 200
```

## Environment Variables Set
Đặt trong Render Dashboard → service → **Environment** (giá trị không nằm trong code hay Git):

| Biến | Giá trị |
|---|---|
| `AGENT_API_KEY` | secret ngẫu nhiên (không ghi ở đây) |
| `REDIS_URL` | Internal URL của Key Value `day14-redis` |
| `ENVIRONMENT` | `production` (tắt `/docs`, bắt buộc có `AGENT_API_KEY`) |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | không đặt → dùng mặc định trong code `10.0` ($10/tháng, ngân sách chung của service) |
| `LOG_LEVEL` | không đặt → mặc định `INFO` |
| `PORT` | do Render tự cấp — `CMD` trong Dockerfile lắng nghe theo `${PORT:-8000}` |

## Screenshots
- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
