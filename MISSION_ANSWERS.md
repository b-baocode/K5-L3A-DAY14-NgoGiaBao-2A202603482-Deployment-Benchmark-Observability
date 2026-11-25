# Day 14 Lab - Mission Answers

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng | Vì sao nguy hiểm |
|---|---|---|---|
| 1 | API key hardcode: `OPENAI_API_KEY = "sk-hardcoded-..."` | 17 | Push lên GitHub là lộ key. Bot quét GitHub tìm `sk-...` trong vài phút. Muốn đổi key phải sửa code và deploy lại. |
| 2 | Mật khẩu database hardcode trong `DATABASE_URL` (`admin:password123`) | 18 | Giống #1. Ngoài ra dev, staging và prod bị buộc dùng chung một database. |
| 3 | In secret ra log: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Log thường được gửi sang hệ thống khác (Loki, Datadog...) và nhiều người đọc được, nên secret bị lộ qua log dù code không bị lộ. |
| 4 | Dùng `print()` thay cho `logging` | 33, 34, 38 | Không có level, không có thời gian, không parse được, không lọc theo mức. **Đã gặp lỗi thật:** chạy trên Windows và ghi stdout ra file, `print()` câu trả lời tiếng Việt bị `UnicodeEncodeError: 'charmap' codec can't encode character 'Đ'`, nên `/ask` trả **500**. |
| 5 | Config cố định trong code: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Đổi cấu hình phải sửa code. Debug luôn bật ở mọi môi trường. |
| 6 | Không có health check | 42–43 | Đã gọi `GET /health` và nhận **404**. Platform không biết app còn sống không nên không tự restart khi app treo. |
| 7 | `host="localhost"` | 51 | Trong container, `localhost` là chính container đó, nên traffic từ bên ngoài (Docker port mapping, load balancer) không vào được. Phải bind `0.0.0.0`. |
| 8 | `port=8000` cố định | 52 | Railway, Render và Cloud Run cấp cổng qua biến `PORT`. App nghe sai cổng thì health check của platform fail và deploy thất bại. |
| 9 | `reload=True` | 53 | Chế độ auto-reload theo dõi file liên tục, tốn tài nguyên và có thể tự restart giữa chừng. Chỉ dùng khi dev. |
| 10 | Không xử lý SIGTERM và không có lifespan shutdown | cả file | Khi platform tắt hoặc redeploy container, request đang xử lý có thể bị cắt ngang và kết nối không được đóng sạch. |
| 11 | `question` nhận qua query string, không validate | 31 | Câu hỏi của user nằm trên URL nên bị ghi vào access log của proxy và load balancer. Câu hỏi rỗng vẫn được chấp nhận. Gửi JSON body thì bị **422**. |

### Exercise 1.2: Chạy bản develop

```bash
cd 01-localhost-vs-production/develop
pip install -r requirements.txt
python app.py
curl -i -X POST "http://localhost:8000/ask?question=Hello"
```

Lần chạy đầu (stdout ghi ra file, Windows dùng cp1252):

```
HTTP/1.1 500 Internal Server Error
...
UnicodeEncodeError: 'charmap' codec can't encode character 'Đ' in position 18
```

Chạy lại với `PYTHONUTF8=1`:

```
HTTP/1.1 200 OK
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Log server in ra secret:

```
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
```

Các kiểm tra khác:
- `GET /health` trả **404**: app không có health check.
- Gửi JSON `{"question": "Hello"}` trả **422**: app chỉ nhận query string.

**Nhận xét:** app "chạy được" nhưng chưa sẵn sàng cho production. Lỗi 500 ở lần chạy đầu cho thấy đúng vấn đề "works on my machine": cùng một code, chỉ khác encoding của môi trường là đã crash.

### Exercise 1.3: So sánh develop và production

Kết quả chạy bản production (`cp .env.example .env && python app.py`):

```
GET  /health → 200 {"status":"ok","uptime_seconds":0.9,"version":"1.0.0","environment":"development",...}
GET  /ready  → 200 {"ready":true}
POST /ask {"question":"Hello"} → 200 {"question":"Hello","answer":"...","model":"gpt-4o-mini"}
POST /ask {}                   → 422 {"detail":"question field is required"}
Uvicorn running on http://0.0.0.0:8000
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode (`DEBUG = True`, `MAX_TOKENS = 500`) | `config.py` đọc env bằng `os.getenv(...)`, có giá trị mặc định an toàn | Cùng một image chạy được ở dev, staging và prod chỉ bằng cách đổi biến môi trường, không phải sửa code và build lại (12-Factor: Config). |
| Secrets | `sk-...` và mật khẩu DB nằm trong code, còn bị in ra log | `OPENAI_API_KEY`, `AGENT_API_KEY` lấy từ env. `.env` nằm trong `.gitignore`, chỉ commit `.env.example`. `validate()` báo lỗi ngay khi thiếu `AGENT_API_KEY` ở production | Secret không vào Git và không vào log, đổi key không cần deploy lại. Fail fast giúp phát hiện thiếu cấu hình ngay lúc khởi động chứ không phải lúc user gọi. |
| Host / Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Container cần bind `0.0.0.0` thì bên ngoài mới vào được. Platform cloud cấp `PORT` động. |
| Health check | Không có (404) | `GET /health` trả `status`, `uptime`, `version` | Liveness probe: platform gọi định kỳ, không nhận 200 thì restart container. |
| Readiness | Không có | `GET /ready` trả 503 khi `is_ready = False` (đang khởi động hoặc đang tắt) | Load balancer chỉ gửi traffic vào instance đã sẵn sàng. Khi đang tắt thì ngừng nhận request mới. |
| Logging | `print()`, in cả secret, crash vì encoding | `logging` có level, ghi sự kiện dạng JSON, chỉ log độ dài câu hỏi, không log secret | Log có cấu trúc thì công cụ gom log parse, lọc và đếm được. Không log secret và dữ liệu cá nhân. |
| Shutdown | Đột ngột | `lifespan`: lúc tắt đặt `is_ready = False` rồi chờ request đang chạy xong; có handler SIGTERM | Khi redeploy hoặc scale down, request đang chạy không bị cắt và kết nối được đóng sạch. |
| Debug / reload | `reload=True` luôn bật | `reload` chỉ bật khi `DEBUG=true` | Production không chạy file watcher và không tự restart giữa chừng. |
| Input | Query string, không validate | JSON body, thiếu `question` thì trả 422 với thông báo rõ ràng | Lỗi đầu vào trả mã 4xx rõ ràng thay vì 500. Dữ liệu user không nằm trên URL. |

**Hai lỗi phát hiện thêm khi chạy thử bản production:**

1. **Log JSON không hiện ra.** Khi chạy chỉ thấy `WARNING:root:OPENAI_API_KEY not set` theo định dạng mặc định, không có dòng `{"event": "startup", ...}` nào.
   - Nguyên nhân: `config.py` gọi `logging.warning(...)` ngay lúc import (`Settings().validate()`), nên Python tự cấu hình root logger với level WARNING. Đến khi `app.py` gọi `logging.basicConfig(...)` thì root logger đã có handler, nên lời gọi này bị bỏ qua: mọi log INFO bị nuốt và format JSON không được áp dụng.
   - Đã kiểm chứng: sau khi `import config`, root logger đã có `StreamHandler` và level `WARNING`.
   - Thêm nữa, kể cả khi format được áp dụng, `"msg":"%(message)s"` bọc một chuỗi `json.dumps(...)` có sẵn dấu `"` mà không escape, nên kết quả không phải JSON hợp lệ. Cách đúng là tự viết `JsonFormatter` gọi `json.dumps` cho cả bản ghi log (sẽ làm ở Part 6).
2. **File `.env` không được nạp.** `requirements.txt` có `python-dotenv` nhưng không file `.py` nào gọi `load_dotenv()`. Vì vậy `cp .env.example .env` không có tác dụng: app chỉ chạy được vì giá trị mặc định trong `config.py` trùng với `.env.example`. Muốn dùng `.env` thì phải gọi `load_dotenv()` trước khi tạo `Settings`, hoặc export biến môi trường, hoặc dùng `env_file` của Docker Compose.

**Graceful shutdown:** chưa thử được bằng `kill -TERM` vì máy đang chạy Python trên Windows, mà Windows không gửi được SIGTERM. Sẽ kiểm tra lại trong WSL hoặc Docker (`docker stop` gửi SIGTERM).

### Checkpoint 1
- [x] Hiểu tại sao hardcode secrets là nguy hiểm
- [x] Biết cách dùng environment variables
- [x] Hiểu vai trò của health check endpoint
- [x] Biết graceful shutdown là gì

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11`. Đây là bản Python đầy đủ chạy trên Debian, có sẵn gcc, git, header để build... Đo được base image này nặng **1.11 GB** (1,105,831,236 bytes).
2. **Working directory:** `/app`. `WORKDIR /app` tạo thư mục này nếu chưa có, và mọi lệnh `COPY`, `RUN`, `CMD` sau đó đều chạy trong `/app`.
3. **Vì sao `COPY requirements.txt` đứng trước `COPY app.py`:** Docker build theo từng layer và cache từng layer. Một layer chỉ phải build lại khi nội dung nó dùng thay đổi, và khi đó **mọi layer phía sau cũng build lại**.
   - `requirements.txt` hiếm khi đổi, còn code đổi liên tục. Đặt `COPY requirements.txt` và `RUN pip install` (layer nặng 48.5 MB) lên trước thì khi chỉ sửa `app.py`, Docker dùng lại cache của bước pip install và chỉ copy lại file code.
   - Nếu `COPY . .` đứng trước, mỗi lần sửa code đều phải cài lại toàn bộ thư viện.
4. **CMD khác ENTRYPOINT thế nào:**
   - `CMD ["python", "app.py"]` là lệnh **mặc định**, bị thay thế hoàn toàn nếu truyền lệnh khi chạy, ví dụ `docker run my-agent:develop python -c "print(1)"`.
   - `ENTRYPOINT` là phần **cố định** của lệnh. Tham số truyền vào `docker run` (hoặc `CMD`) được **nối thêm** vào sau ENTRYPOINT. Muốn đổi ENTRYPOINT phải dùng cờ `--entrypoint`.
   - Thường dùng kết hợp: `ENTRYPOINT ["uvicorn"]` cố định chương trình, còn `CMD ["main:app", "--port", "8000"]` là tham số mặc định có thể đổi.

### Exercise 2.2: Build và chạy image develop

```bash
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .   # build từ gốc repo
docker run -d --name p2-develop -p 8000:8000 my-agent:develop
curl http://localhost:8000/health
# {"status":"ok","uptime_seconds":0.5,"container":true}  [HTTP 200]
curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
# {"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}  [HTTP 200]
docker exec p2-develop whoami
# root
```

**Nhận xét:**
- Container trả lời tiếng Việt bình thường. Ở Part 1, cũng loại code này chạy trên Windows bị `UnicodeEncodeError`. Trong container, môi trường là Linux với UTF-8 giống nhau ở mọi máy. Đây chính là vấn đề Docker giải quyết.
- Container develop chạy bằng **root**: nếu app bị khai thác, kẻ tấn công có quyền root trong container.

### Exercise 2.3: Multi-stage build

- **Stage 1 (`builder`):** dùng `python:3.11-slim`, cài thêm `gcc` và `libpq-dev` (công cụ để biên dịch thư viện có phần C), rồi `pip install --user` để mọi package nằm gọn trong `/root/.local`. Stage này chỉ dùng để build, **không được deploy**.
- **Stage 2 (`runtime`):** bắt đầu lại từ `python:3.11-slim` sạch, tạo user `appuser`, rồi **chỉ copy** `/root/.local` từ builder (37.8 MB) và code của app. Container chạy bằng `appuser` (non-root), có `HEALTHCHECK`, chạy `uvicorn --workers 2`.
- **Vì sao nhỏ hơn:**
  1. Base `python:3.11-slim` chỉ nặng **125 MB**, so với **1.11 GB** của `python:3.11`, vì bỏ compiler, header, tài liệu và các gói hệ thống không cần khi chạy.
  2. `gcc`, `libpq-dev` và cache của apt/pip chỉ nằm trong stage builder. Image cuối cùng chỉ chứa các layer của stage cuối, nên những thứ đó bị bỏ lại.

Kích thước đo bằng `docker images` và `docker image inspect`:

| Image | Base | Kích thước |
|---|---|---|
| `my-agent:develop` | `python:3.11` | **1.15 GB** (1,154,311,927 bytes) |
| `my-agent:advanced` | `python:3.11-slim` + multi-stage | **163 MB** (162,882,434 bytes) |

- Image advanced chỉ bằng **14.1%** image develop, tức **nhỏ hơn 85.9%** (khoảng 7 lần).
- Kiểm tra image advanced: `docker exec ... whoami` in ra `appuser`, `/ask` trả 200, Docker báo `health=healthy`.
- Gần như toàn bộ chênh lệch nằm ở base image: 1.11 GB so với 125 MB. Phần thư viện của app chỉ khoảng 38–49 MB.

### Exercise 2.4: Docker Compose stack

```bash
cd 02-docker/production
touch .env.local
docker compose -p k5-day14-docker up -d --build
```

Sơ đồ:

```
                    máy host
                        │  cổng 80 / 443 (chỉ Nginx publish ra ngoài)
                        ▼
              ┌───────────────────┐
  Client ───▶ │ nginx (nginx:alpine) │  reverse proxy + rate limit 10 r/s/IP + security headers
              └─────────┬─────────┘
                        │ http://agent:8000   (network nội bộ "internal", 172.20.0.0/16)
                        ▼
              ┌───────────────────┐
              │ agent (FastAPI)    │  uvicorn 2 workers, user appuser, không publish cổng
              └───┬───────────┬───┘
     redis://redis:6379  http://qdrant:6333
                  ▼           ▼
           ┌──────────┐  ┌──────────┐
           │ redis    │  │ qdrant   │   mỗi service có volume riêng
           │ 7-alpine │  │ v1.9.0   │   (redis_data, qdrant_data)
           └──────────┘  └──────────┘
```

**Các service được start:** `redis`, `qdrant`, `agent`, `nginx`. Thứ tự start theo `depends_on`: redis và qdrant phải **healthy** trước rồi agent mới start, sau đó mới tới nginx.

```
SERVICE   STATUS                       PORTS
agent     Up (health: starting)        8000/tcp
nginx     Up                           0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    Up (healthy)                 6333-6334/tcp
redis     Up (healthy)                 6379/tcp
```

**Các service liên lạc với nhau thế nào:**
- Cả 4 container nằm chung network bridge `internal`. Docker có DNS nội bộ nên các container gọi nhau **bằng tên service**. Kiểm tra từ trong container agent: `redis -> 172.20.0.2`, `qdrant -> 172.20.0.3`, `nginx -> 172.20.0.5`.
- Agent đọc địa chỉ từ biến môi trường `REDIS_URL=redis://redis:6379/0` và `QDRANT_URL=http://qdrant:6333`, không hardcode IP.
- Chỉ Nginx publish cổng ra máy host. Gọi thẳng `localhost:8000` thì **không kết nối được**. Mọi traffic phải đi qua Nginx, nơi có rate limit và security header.

Test qua Nginx:

```
GET  http://localhost/health → HTTP/1.1 200 OK, Server: nginx, X-Frame-Options: DENY
     {"status":"ok","uptime_seconds":8.8,"version":"2.0.0",...}
POST http://localhost/ask {"question":"Explain microservices"} → 200
     {"answer":"Đây là câu trả lời từ AI agent (mock)..."}
```

**Hai điểm quan sát thêm:**
- Log của agent có dòng `{"time":"...","level":"INFO","msg":"{"event": "request", "q_len": 21}"}`. Dòng này **không phải JSON hợp lệ**, vì chuỗi JSON lồng trong `msg` không được escape dấu `"`. Đây là cùng lỗi đã thấy ở Part 1.
- Stage builder cài `gcc` và `libpq-dev` nhưng `fastapi` và `uvicorn` đã có sẵn bản build sẵn (wheel) nên không cần biên dịch gì. Bước apt-get chỉ làm build chậm hơn; nó không làm image cuối to lên, vì nằm ở stage bị bỏ.

**Ghi chú môi trường:** lúc build, Docker Hub trả `429 Too Many Requests` (giới hạn lượt pull ẩn danh theo IP). Đã pull các image `python:3.11-slim`, `redis:7-alpine`, `qdrant/qdrant:v1.9.0`, `nginx:alpine` qua mirror `mirror.gcr.io` rồi `docker tag` lại đúng tên. Dockerfile và Compose giữ nguyên.

### Checkpoint 2
- [x] Hiểu cấu trúc Dockerfile
- [x] Biết lợi ích của multi-stage builds
- [x] Hiểu Docker Compose orchestration
- [x] Biết cách debug container (`docker logs`, `docker exec`)

---

## Part 4: API Security

### Exercise 4.1: API key authentication (`04-api-gateway/develop/app.py`)

**API key được check ở đâu?**
- Hàm `verify_api_key` ([app.py:39-54](04-api-gateway/develop/app.py#L39-L54)) là một **dependency** của FastAPI. `APIKeyHeader(name="X-API-Key", auto_error=False)` đọc header `X-API-Key`. Nhờ `auto_error=False`, khi thiếu header FastAPI không tự trả lỗi mà để hàm tự quyết định mã lỗi.
- Endpoint `/ask` khai báo `_key: str = Depends(verify_api_key)` (dòng 70), nên FastAPI chạy hàm kiểm tra **trước** khi vào thân hàm. Hai endpoint `/` và `/health` không khai báo dependency này nên vẫn public, để platform gọi health check được.

**Sai key thì sao?**
- Thiếu key → **401** `Missing API key`.
- Sai key → **403** `Invalid API key`.
- Lưu ý cho Part 6: hợp đồng chấm yêu cầu **cả thiếu lẫn sai key đều trả 401**, nên không được copy nguyên logic 403 này.

**Rotate key thế nào?**
- Key đọc từ biến `AGENT_API_KEY`, nên đổi giá trị trên platform rồi restart hoặc redeploy là xong, không cần sửa code.
- Để không làm gián đoạn client đang dùng key cũ, nên cho phép **nhiều key cùng lúc** trong giai đoạn chuyển tiếp (ví dụ `AGENT_API_KEYS=new,old`): phát key mới cho client, chờ client chuyển hết, rồi mới gỡ key cũ.

Test (server chạy bằng `AGENT_API_KEY=secret-key-123 python app.py`):

```
$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -i localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok"}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production/`)

**Luồng JWT** ([auth.py](04-api-gateway/production/auth.py)):
1. `POST /auth/token` nhận username và password. `authenticate_user` kiểm tra thông tin đăng nhập, rồi `create_token` tạo token gồm payload `{sub, role, iat, exp}`, **ký bằng HMAC-SHA256** với `JWT_SECRET`, hết hạn sau 60 phút.
2. Client gửi `Authorization: Bearer <token>` ở mỗi request.
3. `verify_token` gọi `jwt.decode(...)` để kiểm tra chữ ký và thời hạn, rồi lấy `username` và `role` từ payload. Server **không cần tra database** ở mỗi request, nên auth là stateless.

Token gồm 3 phần `header.payload.signature`. Payload chỉ được **mã hoá base64, không bị mã hoá bí mật**: ai có token cũng đọc được nội dung, nhưng không sửa được, vì sửa thì chữ ký sai.

```
$ curl -i -X POST localhost:8000/auth/token -d '{"username":"student","password":"wrong"}' ...
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i -X POST localhost:8000/auth/token -d '{"username":"student","password":"demo123"}' ...
HTTP/1.1 200 OK
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"..."}

# Giải mã base64 phần payload, KHÔNG cần secret:
{'sub': 'student', 'role': 'user', 'iat': 1795596294, 'exp': 1795599894}

$ curl -i -X POST localhost:8000/ask -d '{"question":"Explain JWT"}' ...            # không token
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST localhost:8000/ask -H "Authorization: Bearer <token sửa 1 ký tự>" ...
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i -X POST localhost:8000/ask -H "Authorization: Bearer $TOKEN" -d '{"question":"Explain JWT"}' ...
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"question":"Explain JWT","answer":"...","usage":{"requests_remaining":9,"budget_remaining_usd":1.6e-05}}
```

### Exercise 4.3: Rate limiting (`rate_limiter.py`)

- **Thuật toán:** **sliding window log**. Mỗi user có một `deque` lưu thời điểm của từng request. Mỗi lần kiểm tra, xoá các mốc cũ hơn 60 giây, rồi đếm số mốc còn lại.
  - Docstring gọi là "Sliding Window Counter", nhưng code thực tế lưu từng timestamp, tức là **log** chứ không phải counter.
  - Ưu điểm so với fixed window: không bị dồn gấp đôi số request ở ranh giới giữa hai phút.
- **Giới hạn:** user thường (`student`) **10 request/phút**; admin (`teacher`) **100 request/phút**.
- **Admin có được bỏ qua không:** `app.py:141` chọn `rate_limiter_admin` khi `role == "admin"`. Đây không phải bỏ qua hoàn toàn mà là giới hạn cao hơn (100/phút).
  - Muốn bỏ qua hẳn thì thêm `if role != "admin": limiter.check(username)`.
  - Tuy vậy, nên giữ một giới hạn cao cho admin để phòng trường hợp token admin bị lộ.

Gọi liên tục 20 lần bằng token của `student` (trước đó đã dùng 1 lượt ở bài 4.2):

```
Request  1: 200      Request  6: 200      Request 11: 429      Request 16: 429
Request  2: 200      Request  7: 200      Request 12: 429      Request 17: 429
Request  3: 200      Request  8: 200      Request 13: 429      Request 18: 429
Request  4: 200      Request  9: 200      Request 14: 429      Request 19: 429
Request  5: 200      Request 10: 429      Request 15: 429      Request 20: 429
```

Tổng cộng đúng 10 request thành công trong 60 giây. Response khi bị chặn:

```
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
retry-after: 54
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":54}}
```

**Thí nghiệm restart:**
- Tắt rồi bật lại server, dùng **đúng token cũ**: request trả **200** và `/me/usage` báo `"requests":1`.
- **JWT vẫn hợp lệ** sau restart, vì token tự chứa thông tin và chỉ cần secret để kiểm tra.
- Nhưng **bộ đếm rate limit và chi phí bị reset về 0**, vì chúng nằm trong RAM của process.
- Hệ quả: chạy nhiều instance hoặc nhiều worker thì mỗi process có bộ đếm riêng, nên giới hạn thực tế bị nhân lên. Vì vậy Part 6 bắt buộc lưu state trong Redis.

### Exercise 4.4: Cost guard

**Cách làm (`cost_guard.py`):**
1. Trước khi gọi LLM, `check_budget(user)` kiểm tra hai mức:
   - Nếu tổng chi phí toàn hệ thống trong ngày ≥ $10 thì trả **503**.
   - Nếu chi phí của user trong ngày ≥ $1 thì trả **402 Payment Required**.
   - Khi user đã dùng ≥ 80% ngân sách thì ghi log cảnh báo.
2. Sau khi gọi LLM, `record_usage(user, input_tokens, output_tokens)` tính chi phí theo giá mỗi 1K token (input $0.00015, output $0.0006, theo giá GPT-4o-mini) rồi cộng dồn.
3. Ngân sách tự reset mỗi ngày: mỗi bản ghi có trường `day`, và khi sang ngày mới thì tạo bản ghi mới.

**Bản dùng Redis** (theo yêu cầu: $10/tháng, theo dõi trong Redis, reset đầu tháng):

```python
def check_budget(user_id: str, estimated_cost: float) -> bool:
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month_key}"          # mỗi tháng một key → tự "reset" khi sang tháng mới

    current = float(r.get(key) or 0)
    if current + estimated_cost > MONTHLY_BUDGET_USD:
        return False                               # endpoint trả 402

    r.incrbyfloat(key, estimated_cost)
    r.expire(key, 32 * 24 * 3600)                  # key cũ tự xoá sau 32 ngày, không rác Redis
    return True
```

Chạy thử với Redis thật (container `redis:7-alpine`), budget $10, mỗi lần tốn $3:

```
Lần 1: +$3.00 → cho phép | đã dùng $3.00 / $10.00
Lần 2: +$3.00 → cho phép | đã dùng $6.00 / $10.00
Lần 3: +$3.00 → cho phép | đã dùng $9.00 / $10.00
Lần 4: +$3.00 → TỪ CHỐI (402) | đã dùng $9.00 / $10.00
Lần 5: +$3.00 → TỪ CHỐI (402) | đã dùng $9.00 / $10.00
Key trong Redis: budget:student:2026-11 | TTL còn 2764800 giây (~ 32 ngày)
Client mới (giả lập restart) đọc lại: 9
```

Mở một kết nối mới (giả lập restart app) vẫn đọc lại được `9`, nên state nằm trong Redis chứ không trong RAM.

**Hạn chế còn lại:** `GET` rồi mới `INCRBYFLOAT` là hai lệnh riêng nên không nguyên tử. Hai request đến cùng lúc có thể cùng đọc `9` và cùng được cho qua. Cách sửa: gọi `INCRBYFLOAT` trước, nếu kết quả vượt ngân sách thì trừ lại và từ chối, hoặc gộp cả hai vào một Lua script.

**Vì sao cần cả rate limit lẫn cost guard:**
- **Rate limit bảo vệ tốc độ**, tức số request mỗi phút: chống spam và chống làm quá tải server.
- **Cost guard bảo vệ tiền**, tức tổng chi phí theo ngày hoặc tháng.
- Một user gọi đều 10 request/phút với prompt rất dài thì không bao giờ bị rate limit, nhưng sau một tháng vẫn có thể đốt hàng trăm đô. Ngược lại, cost guard không ngăn được một đợt spam làm nghẽn server trong vài giây. Hai lớp bổ sung cho nhau.

**Các điểm chưa tốt phát hiện trong code mẫu:**
- `app.py:160` trả `"budget_remaining_usd": usage.total_cost_usd`, tức là **số tiền đã dùng** chứ không phải số còn lại. Endpoint `/me/usage` mới tính đúng (`0.999821`).
- Token sai bị trả 403, còn token hết hạn trả 401. Theo chuẩn HTTP, cả hai đều nên là 401, vì client chưa xác thực được; 403 dành cho trường hợp đã xác thực nhưng không đủ quyền, như `/admin/stats` với role user.
- `JWT_SECRET` có giá trị mặc định nằm trong code (`super-secret-change-in-production-please`). Quên đặt biến này thì ai đọc được code cũng tự ký được token admin. Production nên báo lỗi và dừng ngay khi thiếu secret.
- Mật khẩu demo lưu dạng plaintext. Thực tế phải lưu hash (bcrypt hoặc argon2).
- Bản develop in API key ra console khi khởi động (`print(f"API Key: {API_KEY}")`, dòng 91).

### Checkpoint 4
- [x] Implement API key authentication
- [x] Hiểu JWT flow
- [x] Implement rate limiting
- [x] Implement cost guard với Redis

---

## Part 5: Scaling & Reliability

> Exercise 5.1–5.2 chạy trong **WSL2 Ubuntu** (`os.name = posix`) để gửi được SIGTERM thật. Python 3.14 trong venv `~/venvs/day14`. Riêng `psutil` cài bản mới nhất (7.2.2) vì bản 6.0.0 trong requirements không có sẵn bản build cho Python 3.14.

### Exercise 5.1: Health check và readiness (`05-scaling-reliability/develop/app.py`)

```
GET /health → 200
{"status":"ok","uptime_seconds":0.3,"version":"1.0.0","environment":"development",
 "timestamp":"...","checks":{"memory":{"status":"ok","used_percent":13.6}}}

GET /ready → 200
{"ready":true,"in_flight_requests":1}
```

| | `/health` (liveness) | `/ready` (readiness) |
|---|---|---|
| Câu hỏi | Process còn sống không? | Có nhận traffic được không? |
| Ai gọi | Platform (Docker `HEALTHCHECK`, K8s livenessProbe, Railway) | Load balancer, K8s readinessProbe |
| Khi fail | **Restart** container | **Ngừng gửi traffic** vào instance, nhưng không giết nó |
| Nên kiểm tra | Chỉ những gì process tự kiểm soát (event loop phản hồi, bộ nhớ) | Các thành phần phụ thuộc (Redis, DB, model đã load xong), cờ đang khởi động hoặc đang tắt |

- Vì sao `/health` **không nên** phụ thuộc Redis: nếu Redis chết mà `/health` báo fail, platform sẽ restart **tất cả** instance liên tục, trong khi restart cũng không làm Redis sống lại. Lỗi do thành phần phụ thuộc phải báo qua `/ready` (503), để load balancer tạm ngừng gửi traffic chờ Redis hồi phục.
- `in_flight_requests: 1`: middleware đếm luôn chính request `/ready` đang được xử lý.

### Exercise 5.2: Graceful shutdown

Cơ chế trong code:
1. uvicorn nhận SIGTERM và **ngừng nhận kết nối mới** (đóng socket listen).
2. Chờ các request **đang xử lý** xong (`timeout_graceful_shutdown=30`).
3. Chạy phần shutdown của `lifespan`: đặt `_is_ready = False`, chờ `_in_flight_requests == 0`, rồi đóng tài nguyên.
4. Process thoát với exit code 0.

Handler `handle_sigterm` tự viết chỉ để ghi log. uvicorn tạm giữ tín hiệu trong lúc chạy và gọi lại handler gốc **sau khi** đã tắt xong, nên dòng `Received signal 15` xuất hiện **cuối cùng** trong log.

**Test 1: code gốc.** Gửi 2 request `/ask` (mỗi request khoảng 3s), gửi `kill -TERM` ở giây 0.5:

```
  >> đã gửi SIGTERM lúc t=.5s
  >> request MỚI gửi sau SIGTERM: HTTP 000 (bị từ chối kết nối)        ← đúng: không nhận request mới
  req1: {"answer":"Tôi là AI agent..."}  [HTTP 200]  → xong lúc t=3.0s   ← request đang chạy được hoàn thành
  req2:   [HTTP 000]                       → xong lúc t=3.0s             ← request thứ 2 BỊ MẤT
  >> server thoát lúc t=3.1s, exit code 0

INFO:     Shutting down
INFO:     ... "POST /ask?question=Long%20task%201 HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
INFO 🔄 Graceful shutdown initiated...
INFO ✅ Shutdown complete
INFO:     Finished server process [1016]
INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Phân tích: vì sao request 2 bị mất.** Endpoint khai báo `async def ask_agent`, nhưng bên trong `mock_llm.ask()` gọi `time.sleep(3)`, một lệnh **chặn** (blocking).
- Hàm `async` chạy thẳng trên **event loop**, nên `time.sleep` làm cả server đứng im 3 giây: không đọc được request khác, cũng không xử lý được tín hiệu.
- Request 2 đã kết nối TCP nhưng chưa từng được đọc, nên không được tính là "đang xử lý". Khi uvicorn tắt, kết nối này bị đóng.

Kiểm chứng bằng 3 thí nghiệm. Bản "sửa" là một bản sao trong `/tmp`, chỉ đổi `async def` thành `def`; khi đó FastAPI chạy hàm trong threadpool:

```
A) Bản gốc (async def + time.sleep), KHÔNG SIGTERM
  req1 HTTP 200, xong lúc t=6.0s
  req2 HTTP 200, xong lúc t=6.0s          ← 2 request bị xử lý tuần tự: 3s + 3s
B) Bản sửa (def), KHÔNG SIGTERM
  req2 HTTP 200, xong lúc t=3.0s
  req1 HTTP 200, xong lúc t=3.0s          ← chạy song song trong threadpool
C) Bản sửa (def), SIGTERM sau 0.5s
  >> SIGTERM lúc t=.5s
  req1 HTTP 200, xong lúc t=3.0s
  req2 HTTP 200, xong lúc t=3.0s          ← cả 2 request đều hoàn thành
  >> server exit code 0
```

**Kết luận:** graceful shutdown chỉ bảo vệ những request mà server **đã bắt đầu xử lý**. Nếu code chặn event loop thì request có thể bị mất dù đã có SIGTERM handler. Quy tắc: trong `async def` chỉ dùng thư viện async (`await asyncio.sleep`, `httpx.AsyncClient`, `redis.asyncio`). Gọi thư viện đồng bộ thì khai báo `def` thường. Bài học này áp dụng trực tiếp cho tiêu chí Graceful shutdown của Part 6.

### Exercise 5.3: Stateless design

**Vì sao state trong RAM hỏng khi scale:** mỗi instance là một process riêng, có bộ nhớ riêng.
- Load balancer chia request theo vòng (round-robin): request 1 của user vào instance A, request 2 vào instance B. B không thấy history mà A lưu trong RAM.
- Restart hoặc redeploy instance nào thì mất toàn bộ dữ liệu của instance đó.

**Cách `production/app.py` làm stateless:**
- Toàn bộ session lưu trong Redis tại key `session:<uuid>`, dạng JSON, ghi bằng `SETEX` với TTL 3600s (tự hết hạn sau 1 giờ không hoạt động).
- Mỗi request đều `load_session`, thêm tin nhắn, rồi `save_session`. Instance không giữ gì giữa các request.
- Client giữ `session_id` và gửi lại ở mỗi lượt, nên **instance nào cũng phục vụ được bất kỳ user nào**.
- Xác nhận trong Redis: `KEYS session:*` cho ra `session:af626cbb-...`, `TTL` còn 3586 giây.

Lưu ý: code có nhánh dự phòng `_memory_store` khi không kết nối được Redis. Nhánh này tiện khi dev nhưng nguy hiểm khi chạy production: nếu Redis chết lúc khởi động, app vẫn chạy **ở chế độ stateful** mà không báo lỗi, nên mỗi instance có state riêng. Production nên báo lỗi ngay, hoặc ít nhất `/ready` phải trả 503.

### Exercise 5.4: Load balancing

```bash
cd 05-scaling-reliability/production && touch .env.local
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```

```
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 5 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-2   Up 5 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-3   Up 5 seconds (healthy)    8000/tcp
k5-day14-scaling-nginx-1   Up 5 seconds              0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 16 seconds (healthy)   6379/tcp
```

```
Client ──▶ Nginx :8080 ──round-robin──▶ agent-1 (instance-5996d9) ──┐
                           ├──────────▶ agent-2 (instance-698205) ──┼──▶ Redis (session:*)
                           └──────────▶ agent-3 (instance-9777bf) ──┘
```

- Nginx dùng `server agent:8000`. DNS nội bộ của Docker (`resolver 127.0.0.11`) trả về IP của cả 3 container, và Nginx chia đều request.
- Header `X-Served-By: 172.20.0.5:8000` cho biết container nào đã phục vụ.
- Tổng 23 request `/chat` được chia như sau: agent-1: 2, agent-2: 10, agent-3: 11. agent-1 ít vì nó bị tắt trong thí nghiệm bên dưới.

### Exercise 5.5: Test stateless

```
Session ID: af626cbb-bbe1-4200-9cd7-cdcea44010b0

Request 1: [instance-698205]   Q: What is Docker?
Request 2: [instance-9777bf]   Q: Why do we need containers?
Request 3: [instance-5996d9]   Q: What is Kubernetes?
Request 4: [instance-698205]   Q: How does load balancing work?
Request 5: [instance-9777bf]   Q: What is Redis used for?

Instances used: {'instance-698205', 'instance-9777bf', 'instance-5996d9'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 request đi theo vòng qua cả 3 instance (698205, 9777bf, 5996d9, rồi lặp lại). Mỗi request do một instance khác xử lý, nhưng history vẫn đủ 10 tin nhắn (5 câu hỏi, 5 câu trả lời), vì không instance nào giữ state; tất cả cùng đọc và ghi một key trong Redis.

**Thí nghiệm thêm: tắt hẳn một instance giữa chừng** (kịch bản gốc trong CODE_LAB; `test_stateless.py` không tự làm bước này):

```
$ docker stop k5-day14-scaling-agent-1          # agent-1 = instance-5996d9
k5-day14-scaling-agent-2: Up (healthy)
k5-day14-scaling-agent-3: Up (healthy)

# 12 request tiếp theo vào CÙNG session → tất cả HTTP 200
  turn=11  served_by=instance-698205
  turn=11  served_by=instance-9777bf
  ...
history count = 20

$ docker start k5-day14-scaling-agent-1
  instance-9777bf, instance-698205, instance-9777bf, instance-38a0a8, instance-698205, instance-9777bf
```

- Mất một instance thì Nginx tự chuyển traffic sang 2 instance còn lại (`proxy_next_upstream error timeout http_503`). Session không mất vì nằm trong Redis.
- Khi bật lại, agent-1 có **ID mới** (`instance-38a0a8`), vì `INSTANCE_ID` được sinh ngẫu nhiên lúc khởi động. Nginx tự nhận lại nó sau vài giây nhờ `resolver ... valid=10s`, rồi tiếp tục chia tải.
- Phát hiện thêm: `turn` dừng ở **11**. Code giữ tối đa 20 tin nhắn (10 lượt) và tính `turn` từ history đã bị cắt bớt, nên sau lượt 10 con số này không còn đúng. Muốn đếm đúng thì phải lưu bộ đếm riêng, ví dụ `INCR session:<id>:turns`.

### Checkpoint 5
- [x] Implement health và readiness checks
- [x] Implement graceful shutdown
- [x] Refactor code thành stateless
- [x] Hiểu load balancing với Nginx
- [x] Test stateless design

---

## Part 6: Final Project (`06-lab-complete/`)

### Kết quả `check_production_ready.py`

Chạy **bản đầy đủ** (có Docker, không `--skip-docker`) trong WSL2 Ubuntu (`os.name = posix`, Docker 27.4.0):

```
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết đáng chú ý trong output:
- `Docker: image < 500 MB — 183 MB`
- `429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)`
- `state nằm trong Redis — restart agent vẫn bị 429`
- `402 ở request thứ 1`
- `mọi dòng log của app là JSON hợp lệ — 17 dòng JSON`
- `SIGTERM lúc đang có request → request vẫn 200 — exit code 0`
- `docker stop tắt êm — exit code 0, 0.5s`

Mốc ban đầu (bản khung chưa sửa): `0/12 tiêu chí đạt · 5/24 checks passed`.

### Thiết kế

| File | Làm gì |
|---|---|
| `app/config.py` | Dataclass `Settings`, mọi field đọc từ `os.getenv`. `load_dotenv(override=False)` chỉ để dev local, nên biến môi trường thật luôn được ưu tiên. `validate()` báo lỗi ngay khi `ENVIRONMENT=production` mà thiếu `AGENT_API_KEY`. Không có secret nào nằm trong code. |
| `app/redis_client.py` | `redis.from_url(...)` chỉ tạo connection pool, **không kết nối lúc import**, nên container khởi động được khi chưa có Redis. Timeout kết nối 1s và timeout đọc/ghi 2s. `redis_healthy()` gọi `PING`. |
| `app/auth.py` | `APIKeyHeader("X-API-Key", auto_error=False)` để thiếu header thì hàm tự trả **401** (không để FastAPI trả 403). So sánh bằng `hmac.compare_digest` để tránh timing attack. Key rỗng thì từ chối tất cả. |
| `app/rate_limiter.py` | Sliding window 60s bằng **Redis sorted set** `ratelimit:<sha256(key)>`. Pipeline `MULTI` gồm `ZREMRANGEBYSCORE`, `ZADD`, `ZCARD`, `EXPIRE`. Vượt giới hạn thì `ZREM` request vừa thêm (request bị từ chối không tính vào quota), trả **429** kèm `Retry-After` tính từ request cũ nhất trong cửa sổ. Không lưu API key gốc trong Redis, chỉ lưu hash. |
| `app/cost_guard.py` | Chi phí cộng bằng `INCRBYFLOAT` vào `cost:day:<YYYY-MM-DD>` (TTL 2 ngày) và `cost:month:<YYYY-MM>` (TTL 32 ngày); sang ngày hay tháng mới thì dùng key mới, nên ngân sách tự reset. `check_budget()` trả **402** khi chi phí trong ngày ≥ `DAILY_BUDGET_USD`, hoặc chi phí trong tháng ≥ `MONTHLY_BUDGET_USD` nếu biến này > 0. Số token ước lượng bằng số ký tự chia 4, vì mock LLM không trả số token. |
| `app/main.py` | `JsonFormatter` tự viết, `logging.basicConfig(..., force=True)` để tránh lỗi đã gặp ở Part 1. Middleware gắn `X-Request-ID` và ghi access log dạng JSON. `/health` không phụ thuộc Redis. `/ready` kiểm tra Redis và cờ đang tắt. `/ask` khai báo `def`, không phải `async def` (bài học Part 5), theo thứ tự: auth → rate limit → budget → LLM → ghi chi phí. Handler `RedisError` trả **503**. Lỗi không lường trước trả **500** dạng JSON kèm `request_id`, traceback chỉ ghi vào log. Handler SIGTERM ghi log rồi gọi lại handler cũ. |
| `Dockerfile` | Builder (`python:3.11-slim`) cài thư viện vào `/opt/venv`. Runtime (`python:3.11-slim`) chỉ copy venv và code, chạy bằng user `app` (non-root), có `HEALTHCHECK`. `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]`: `exec` để uvicorn là **PID 1** và nhận SIGTERM. Dùng venv thay vì `pip --user`, nên không cần `PYTHONUSERBASE`. |
| `docker-compose.yml` | `agent` (build `.`, `env_file: .env` với `required: false`, `REDIS_URL=redis://redis:6379/0`, `depends_on: redis: condition: service_healthy`) và `redis:7-alpine` (healthcheck `redis-cli ping`, AOF và volume để restart Redis không mất state). |
| `.dockerignore` | Loại `.env*` (giữ lại `.env.example`), `__pycache__`, `venv`/`.venv`, `.git`, `*.md`, `benchmark-observability/`, script chấm... |
| `railway.toml` | `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`, restart khi lỗi. |
| `render.yaml` | Web service Docker (`rootDir: 06-lab-complete`) cùng Key Value (Redis). `AGENT_API_KEY` dùng `sync: false`, `REDIS_URL` lấy từ `fromService`. |

### Kiểm tra thêm bằng `docker compose` (ngoài script chấm)

Lệnh: `docker compose -p k5-day14-final up -d --build`. Image `k5-day14-final-agent:latest` nặng 192 MB.

```
/health                          → 200 {"status":"ok","version":"1.0.0","environment":"staging",...}
/ready                           → 200 {"ready":true}
/ask thiếu key                   → 401 {"detail":"Missing or invalid API key. Send header: X-API-Key: <key>"}
/ask sai key                     → 401 (cùng thông báo — không tiết lộ key sai hay thiếu)
/ask đúng key                    → 200 {"question":"What is deployment?","answer":"Deployment là ...",
                                        "usage":{"input_tokens":4,"output_tokens":19,"cost_usd":1.2e-05,"requests_remaining":9}}
/ask {"q":"x"}                   → 422 {"detail":[{"type":"missing","loc":["body","question"],"msg":"Field required",...}]}
/ask {"question":""}             → 422 {"detail":[{"type":"string_too_short",...,"ctx":{"min_length":1}}]}
/ask JSON hỏng                   → 422 {"detail":[{"type":"json_invalid",...,"msg":"JSON decode error",...}]}
12 request tiếp (limit 10/phút)  → 200 ×9, 429 ×3
429 headers                      → retry-after: 58, x-ratelimit-limit: 10, x-ratelimit-remaining: 0
Redis keys                       → ratelimit:c1feea1c52761a5efa05f3bc, cost:day:2026-11-25, cost:month:2026-11
docker compose stop redis        → /health 200, /ready 503 "Redis not reachable", /ask 503 "Service temporarily unavailable"
docker compose start redis       → /ready 200
API key xuất hiện trong log?     → 0 lần
PID 1 trong container            → /opt/venv/bin/python /opt/venv/bin/uvicorn app.main:app ... --workers 2 (user: app)
docker compose stop agent        → 1.1s, exit code 0
```

Log JSON mẫu:

```json
{"ts": "2026-11-25T09:18:57.466+00:00", "lvl": "ERROR", "logger": "agent", "msg": "redis unavailable", "event": "redis_error", "error": "ConnectionError", "path": "/ask"}
{"ts": "2026-11-25T09:18:57.467+00:00", "lvl": "INFO", "logger": "agent", "msg": "request", "event": "request", "request_id": "f7c7361127f74154", "method": "POST", "path": "/ask", "status": 503, "ms": 7859.5}
{"ts": "2026-11-25T09:19:15.391+00:00", "lvl": "INFO", "logger": "agent", "msg": "sigterm received", "event": "sigterm"}
{"ts": "2026-11-25T09:19:15.568+00:00", "lvl": "INFO", "logger": "agent", "msg": "agent stopped", "event": "shutdown", "uptime_s": 53.2}
```

**Hạn chế đã biết:**
- Khi container Redis bị `stop` trong Compose, `/ready` và `/ask` mất khoảng 7.8s mới trả 503. Thời gian này nằm ở bước **tra DNS** tên `redis` (container đã biến mất khỏi network), không phải ở `socket_connect_timeout=1`. Script chấm dùng `127.0.0.1:<cổng trống>` nên trả 503 ngay. Trên cloud, hostname của Redis vẫn tồn tại khi Redis lỗi, nên timeout kết nối 1s mới là giới hạn thực tế.
- Cost guard kiểm tra **trước** rồi mới cộng chi phí **sau** khi gọi LLM. Nhiều request đồng thời lúc gần hết ngân sách có thể vượt một chút (soft limit). Muốn chặn cứng thì phải "đặt trước" chi phí ước tính bằng `INCRBYFLOAT` trước khi gọi LLM, rồi điều chỉnh lại sau.

---

## AI log

| # | Phần | Hỏi AI gì | Nhận được gì | Tự sửa / tự kiểm tra thế nào |
|---|---|---|---|---|
| 1 | Part 1 | Nhờ Claude Code đọc `develop/app.py` và `production/app.py`, chạy thử hai bản và giải thích các khác biệt | Danh sách anti-pattern kèm số dòng, output curl, bảng so sánh, và giải thích hai lỗi của bản production (log bị nuốt, `.env` không được nạp) | Tự chạy lại curl xem mã 200/404/422/500. Kiểm chứng lỗi logging bằng cách in `logging.getLogger().handlers` sau khi `import config`. Grep `load_dotenv` không thấy trong code. |
| 2 | Part 2 | Nhờ Claude Code build hai image, đo dung lượng, chạy stack Compose và giải thích từng lệnh | Số MB đo được, phân tích layer bằng `docker history`, sơ đồ stack, cách xử lý lỗi 429 của Docker Hub bằng mirror | Tự đối chiếu số MB với `docker images`. Gọi thẳng cổng 8000 để xác nhận agent không bị publish ra ngoài. Kiểm tra DNS nội bộ bằng `docker compose exec`. |
| 3 | Part 4 | Nhờ Claude Code chạy bản develop và production, lấy JWT, test rate limit, viết và test cost guard bằng Redis | Output 401/403/200/429, giải mã payload JWT, thí nghiệm restart, hàm `check_budget` dùng Redis kèm kết quả chạy | Tự đếm số lần 200 trước khi gặp 429. Restart server để xác nhận state nằm trong RAM. Mở client Redis mới để xác nhận state cost guard còn sau restart. Đọc lại code tìm lỗi `budget_remaining_usd`. |
| 4 | Part 5 | Nhờ Claude Code test health check và SIGTERM trong WSL, giải thích vì sao request thứ 2 bị mất khi shutdown, chạy stack 3 instance và test stateless | Phân tích `async def` + `time.sleep` chặn event loop, 3 thí nghiệm A/B/C, output `test_stateless.py`, thí nghiệm tắt một instance | Tự so sánh thời gian 6s (tuần tự) và 3s (song song). Kiểm tra key `session:*` và TTL trực tiếp trong Redis. Đếm request của từng container bằng `docker logs`. |
| 5 | Part 6 | Nhờ Claude Code đọc `check_production_ready.py` (không sửa), viết code cho các TODO trong `06-lab-complete/` và giải thích từng quyết định | Code `config`, `auth`, `redis_client`, `rate_limiter`, `cost_guard`, `main`, Dockerfile, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Chạy script chấm bản đầy đủ trong WSL: `PASS 12/12`. Tự test thêm bằng `docker compose`: 401/422/429/503, tắt Redis, `docker stop` (exit 0), kiểm tra PID 1 và user non-root, grep để chắc API key không xuất hiện trong log. |
