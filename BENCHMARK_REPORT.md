# Benchmark & Observability Report — Day 14

**Họ tên:** Ngô Gia Bảo — 2A202603482
**Ngày chạy:** 25/11/2026
**Môi trường:**
- Bước 1 (develop): Windows 11 + **WSL2 Ubuntu 26.04**, Python 3.14 (venv), agent chạy trực tiếp bằng `python app.py`.
- Bước 2 (production): Docker Desktop 27.4.0 (WSL2 backend, 16 CPU, 7.6 GiB RAM cấp cho Docker). Mỗi container agent bị giới hạn `0.5 CPU / 256 MB` (theo `docker-compose.yml`).

File bằng chứng: `reports/*.json` (load test + eval), `benchmark-results/scale_{1,3}_stats.csv` (Locust), `screenshots/` (Grafana, Jaeger, Prometheus).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500** ms | Mock LLM mất ~150–250 ms mỗi call bình thường, nên 500 ms còn gấp đôi dư địa cho mạng và retrieval, nhưng vẫn bắt được trường hợp >5% request rơi vào nhóm chậm (~1.7 s). Đặt theo p95 thay cho average vì average che mất tail latency (xem 2.2). Ngưỡng này trùng mặc định của `load_test.py` (`--slo-p95-ms 500`). |
| Error rate `/ask` | < **2** % | Upstream LLM giả lập lỗi ~1% (`MOCK_ERROR_RATE=0.01`), nên 2% là gấp đôi mức lỗi "bình thường". Ngưỡng 2% vẫn cho phép biến động ngẫu nhiên nhưng phát hiện ngay khi tỉ lệ lỗi tăng thật. Alert production (`AgentHighErrorRate`) dùng ngưỡng 5% kèm `for: 1m` để tránh báo động giả. |
| Accuracy trên golden dataset | ≥ **85** % | Đây là ngưỡng của job CI (`.github/workflows/eval.yml`). Bản hiện tại đạt 100%; 85% cho phép thay đổi prompt/KB làm sai tối đa 3/23 câu. Ngoài ngưỡng này, gate còn **chặn mọi regression** so với baseline đã duyệt, nên một câu đang đúng mà trở thành sai vẫn bị chặn. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json` (mặc định `MOCK_SLOW_RATE=0.03`, `MOCK_ERROR_RATE=0.01`).

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.75 | 188.9 | 1659.2 | 1709.4 | 1.00% (3/300) | ❌ FAIL (p95) |
| 10 | 41.15 | 193.2 | 220.0 | 1691.5 | 0.33% (1/300) | ✅ PASS |
| 50 | 106.81 | 192.2 | 243.9 | 1711.9 | 1.67% (5/300) | ✅ PASS |
| 100 | 138.31 | 199.1 | 270.8 | 1709.2 | 1.00% (3/300) | ✅ PASS |

**Nhận xét:** RPS có tăng tuyến tính theo concurrency không? Vì sao?

- **Gần tuyến tính ở mức thấp, rồi chững lại.** Từ c=1 lên c=10, RPS tăng ~11 lần (3.75 → 41.15), đúng với công thức `RPS ≈ concurrency / latency trung bình` (10 / 0.24 s ≈ 42). Từ c=10 lên c=100 (gấp 10 lần), RPS chỉ tăng ~3.4 lần (41 → 138).
- **Vì sao:** mock LLM là `await asyncio.sleep(...)`, tức I/O-bound: một process uvicorn chờ hàng trăm request cùng lúc mà không tốn CPU. Nên latency từng request gần như không đổi (p50 189 → 199 ms). Khi concurrency cao, phần chiếm thời gian chuyển sang overhead cố định: chạy đơn một process, mỗi request vẫn phải parse JSON, chạy middleware, ghi log, cập nhật metric; và chính client `load_test.py` khởi tạo kết nối. Với 300 request mà c=100, cả bài test chỉ có 3 "đợt" (2.17 s), nên thời gian khởi động và đợt cuối chiếm tỉ trọng lớn. p50 và p95 tăng nhẹ (199/271 ms ở c=100) cho thấy bắt đầu có hàng đợi.
- **c=1 trượt SLO** dù tải thấp nhất: p95 = 1659 ms nghĩa là **≥ 5% request** (≥ 15/300) rơi vào nhóm chậm trong lần chạy này, nhiều hơn kỳ vọng 3%. Đây là **dao động mẫu**: số request chậm theo phân phối nhị thức, trung bình 9 và độ lệch ~3, nên 15 là hiếm nhưng vẫn xảy ra. Với p95 nằm sát ranh giới 3% so với 5%, 300 mẫu là chưa đủ để kết luận chắc chắn; production cần cửa sổ đo dài hơn.

### 2.2 Tail latency

Lệnh: khởi động lại agent v2 với từng giá trị `MOCK_SLOW_RATE`, rồi `--requests 300 --concurrency 20 --out reports/load_v2_slow_<rate>.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 187.4 | 214.5 | 220.0 |
| 0.03 (mặc định) | 223.4 | 217.7 | 1706.0 |
| 0.1 | 351.2 | 1694.2 | 1710.4 |

**Nhận xét:** Chỉ số nào nhạy nhất với request chậm? Vì sao SLO không nên đặt theo average?

- **p99 nhạy nhất.** Chỉ cần 3% request chậm, p99 nhảy từ 220 ms lên **1706 ms (×7.8)**, trong khi avg chỉ tăng 187 → 223 ms (+19%) và p95 gần như không đổi (214 → 218 ms), vì 3% < 5% nên p95 vẫn rơi vào nhóm nhanh.
- Khi tỉ lệ chậm vượt 5% (0.1 = 10%), **p95 cũng nhảy lên 1694 ms (×7.9)**, còn avg mới lên 351 ms (+87%). Theo avg thì hệ thống có vẻ "vẫn dưới 500 ms, ổn", nhưng thực tế cứ 10 người dùng thì 1 người phải chờ gần 1.7 giây.
- **Average che giấu nhóm người dùng bị chậm**: nó trộn 90% request nhanh với 10% request rất chậm thành một con số "trung bình" mà không ai thật sự trải nghiệm. SLO phải đặt theo percentile (p95 hoặc p99) để mô tả đúng những gì nhóm người dùng tệ nhất gặp phải.
- Lưu ý: lần chạy `MOCK_SLOW_RATE=0` trượt SLO **error rate** (2.67%, 8/300) dù latency tốt nhất. Đây cũng là dao động ngẫu nhiên của `MOCK_ERROR_RATE=0.01`, không liên quan tới slow rate.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | **100.0% (23/23)** |
| Avg tokens / câu | 58.7 | 109.0 | 114.6 |
| Tổng cost (USD) | 0.000426 | 0.000760 | 0.000798 |
| Cost / câu đúng (USD) | 0.00042555 | 0.00003619 | 0.00003472 |
| Latency p50 / p95 (ms) | 168.1 / 189.1 | 178.0 / 211.3 | 188.8 / 214.2 |
| Quality gate | ❌ FAIL (accuracy < 80%) | ✅ PASS (≥ 80%, 0 regression so với v1) | ✅ PASS (≥ 100%, 0 regression so với v2) |

File: `reports/eval_v1.json`, `reports/eval_v2.json` (`--baseline reports/eval_v1.json`), `reports/eval_v2_fixed.json` (`--min-accuracy 1.0 --baseline reports/eval_v2.json`).

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Knowledge base không có tài liệu về HPA, nên retrieval không tìm được gì và agent trả câu chung chung. Đã thêm entry với từ khoá `["hpa", "kubernetes", "autoscal"]` và nội dung nêu rõ HPA scale theo *CPU, memory hoặc custom metrics*. |
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Không có tài liệu về JWT. Đã thêm entry với từ khoá `["jwt"]`: API key cố định, không tự hết hạn; JWT là *token* có *chữ ký* số, chứa thời điểm *hết hạn*. |

- Sửa trong `develop/utils/mock_llm.py` (chỉ thêm vào `KNOWLEDGE_BASE`, không sửa `golden_dataset.jsonl`). Đã chép cùng phần sửa sang `production/utils/mock_llm.py` để stack Docker cũng đạt 100%.
- **Kiểm tra không có regression:** trước khi thêm, đã grep `golden_dataset.jsonl`: các từ khoá `jwt`, `hpa`, `kubernetes`, `autoscal` chỉ xuất hiện trong đúng 2 câu đang sai, nên entry mới không thể "cướp" retrieval của câu khác. Eval cuối báo `Câu được cải thiện : 2`, `Câu bị regression : 0`.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 có retrieval (RAG) nên accuracy tăng từ 4.3% lên 91.3% (+87 điểm %), và lên 100% sau khi bổ sung tài liệu.
- **Chi phí:** prompt của v2 chứa thêm tài liệu nên tốn **1.86× token mỗi câu** (109.0 so với 58.7) và tổng cost gấp 1.78×. Nhưng tính theo **cost trên mỗi câu trả lời đúng** thì v2 **rẻ hơn 11.8 lần** ($0.0000362 so với $0.000426), vì v1 trả sai gần hết, tiền chi cho câu sai là tiền mất trắng. Đây là lý do cost / câu đúng là chỉ số so sánh tốt hơn cost / câu.
- **Latency:** v2 chậm hơn một chút (p95 189 → 211 ms, +22 ms) do thêm bước retrieval (~20 ms) và prompt dài hơn (latency mock tăng theo số token input).
- **Kết luận:** đổi ~20 ms latency và gấp đôi token để có độ chính xác cao hơn 20 lần là hoàn toàn đáng.

**Quality gate trong CI (Exercise 7.3):**

- `run_eval.py` trả **exit code 1** khi accuracy < `--min-accuracy` hoặc có câu bị regression so với `--baseline`.
- Đã thử giả lập deploy nhầm v1 với `--baseline reports/eval_v2.json`: `Câu bị regression : 20`, `Quality gate (accuracy ≥ 80%, không regression): ❌ FAIL`, exit code 1.
- Job CI `.github/workflows/eval.yml` chạy mỗi pull request vào `main`: cài dependencies, bật agent v2 (`MOCK_ERROR_RATE=0` để kết quả không flaky), rồi chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`. Exit code khác 0 thì step fail, job đỏ. Bật branch protection *"Require status checks to pass before merging"* cho check `quality-gate` thì GitHub không cho merge PR làm giảm chất lượng.

---

## 4. Observability stack (production)

Stack: `docker compose -p k5-day14-observability up -d --build`. Tải nền: Locust headless 30 users, ramp 5/s (`docker compose run --no-deps ... -u 30 -r 5`).

> **Sửa lỗi Dockerfile của stack:** lần đầu bật, container agent crash liên tục (`Restarting (1)`) với `ModuleNotFoundError: No module named 'uvicorn'`. User `agent` được tạo với `HOME=/app` (`useradd -d /app`), nên Python tìm user site-packages ở `/app/.local`, trong khi package được copy vào `/home/agent/.local`. Đã thêm `ENV PYTHONUSERBASE=/home/agent/.local` vào `production/Dockerfile` (đúng cảnh báo trong CODE_LAB Part 6, bước 8). Benchmark của lần chạy lỗi đó (100% 502) đã bị bỏ và chạy lại.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png), thêm hàng LLM ở [`screenshots/grafana-dashboard-llm.png`](screenshots/grafana-dashboard-llm.png)
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png). Thấy 2 alert cùng FIRING: `AgentHighErrorRate` và `AgentHighLatencyP95`.

**Trả lời Exercise 7.4.** Số liệu đọc từ Prometheus (cửa sổ 1 phút) khi Locust 30 users đang chạy, 2 instance:

1. **p95 và avg đang là bao nhiêu? Khoảng cách giữa chúng nói lên điều gì?**
   - Prometheus cho p95 = **291.7 ms**, avg = **228.1 ms**, p50 = 171.2 ms, p99 = **1818 ms**. Grafana trong ảnh chụp ít giây sau hiện p95 = 295.02 ms.
   - p95 chỉ cao hơn avg ~64 ms, nhưng p99 cao gấp 8 lần avg. Phân phối latency có **đuôi dài**: phần lớn request nhanh (~170–290 ms), còn ~3% request (`MOCK_SLOW_RATE=0.03`) chậm ~1.7 s. Đuôi này làm avg tăng nhẹ nhưng không hiện ra ở p95, chỉ p99 mới thấy. Trên panel *Latency /ask*, đường p99 nằm phía trên ngưỡng 1 s suốt thời gian chạy.
2. **Panel "Requests/s theo instance": Nginx có chia đều traffic cho 2 instance không?**
   - **Có, gần như đều**: `172.20.0.4:8000` nhận 9.15 req/s, `172.20.0.8:8000` nhận 9.38 req/s (tổng 18.5 req/s).
   - Trong benchmark 3 instance (mục 5), số request /ask của 3 instance là 2424 / 2394 / 2437, lệch < 2%. Nginx round-robin qua Docker DNS (`server agent:8000 resolve`).
3. **Vì sao input tokens/s lớn hơn output tokens/s?**
   - Input = **1436.8 tokens/s**, output = **755.6 tokens/s** (≈1.9×). Prompt gửi cho LLM gồm system prompt, **tài liệu retrieval (context RAG)** và câu hỏi, trong khi output chỉ là một câu trả lời ngắn.
   - Agent v2 càng đưa nhiều context vào prompt thì input càng lớn. Đây cũng là lý do v2 tốn token hơn v1 (mục 3).
4. **`curl http://localhost:8080/metrics` trả về gì? Vì sao?**
   - Trả **`HTTP/1.1 404 Not Found`**. Nginx (cổng vào duy nhất từ bên ngoài) cố ý chặn `/metrics`, vì metrics lộ thông tin nội bộ (route, số request, chi phí, version) và không phải API cho người dùng.
   - Prometheus vẫn scrape được vì nó gọi thẳng `agent:8000/metrics` trong network nội bộ `obs_net`, không đi qua Nginx.

### 4.2 Thí nghiệm "phá hệ thống"

Cách làm:
- `cp .env.example .env`, sửa biến, rồi `docker compose -p k5-day14-observability up -d agent` (tạo lại container agent). Thời điểm tạo lại container là **t0**.
- Locust 30 users chạy suốt thí nghiệm.
- Thời gian lấy bằng cách poll `GET /api/v1/alerts` của Prometheus mỗi 5 giây.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | **86.9 s** (pending sau 26.8 s, cộng `for: 1m`) | *Error rate* (Golden Signals) chuyển đỏ; *Requests/s theo status code* xuất hiện nhiều 503; *LLM calls theo kết quả* tăng nhánh `error` |
| B | `MOCK_SLOW_RATE=0.3` (đổi **cùng lúc** với A) | `AgentHighLatencyP95` | **142.1 s** (pending sau 21.8 s, cộng `for: 2m`) | *Latency p95* (Golden Signals) và *Latency /ask p50/p95/p99*: p95 vượt đường 1 s; *LLM latency p95 theo model* |
| C | `LLM_MODEL=mock-llm-large` | Không làm riêng. Ghi nhận: **`LLMCostBurnRateHigh` đã firing ngay với `mock-llm-small`** khi có tải (xem ghi chú) | không đo | *Cost burn rate (USD/giờ)* nằm trong vùng đỏ (> $2/giờ) |
| D | `docker pause` 1 instance | Không làm | – | – |
| E | `AGENT_VERSION=v1` (lỗi và độ chậm về mặc định) | **Không có alert nào** (theo dõi 3 phút có tải) | – (không bao giờ firing) | *Retrieval hit ratio* mất dữ liệu (v1 không chạy retrieval); *Tokens/s* giảm ~53%; *Cost burn rate* giảm một nửa; bảng version đổi sang `v1`. Panel latency lại **đẹp hơn** (p95 294 → 198 ms) |

Ghi chú:
- **A và B làm đồng thời** để có ≥ 2 alert cùng firing trong một ảnh chụp. Thời gian của mỗi alert vẫn đo riêng từ cùng t0. Giá trị lúc firing: error rate ~17–20%, p95 ≈ 1.89–1.90 s.
- **Độ trễ phát hiện = thời gian để `rate(...[1m])` phản ánh dữ liệu mới + thời gian `for`.** Phần đầu khoảng 20–30 s (scrape mỗi 5 s, rule đánh giá theo chu kỳ, cửa sổ 1 phút cần đủ mẫu mới). Phần sau là 1 phút cho A, 2 phút cho B. `for` dài hơn thì ít báo động giả hơn nhưng phát hiện chậm hơn.
- Ngay sau khi tạo lại container, `AgentInstanceDown` vào **pending** (+6.7 s) cho 2 IP cũ đã biến mất, rồi tự hết, nhờ `for: 30s` mà không firing. Cũng trong lúc chuyển đổi, giá trị error rate có lúc > 100% (`value=6.28`), vì series của instance cũ và mới chồng nhau trong cửa sổ `rate[1m]`.
- **Thí nghiệm C (quan sát):** với tải 30 users và model nhỏ, chi phí đã là **$2.43/giờ**, vượt ngưỡng `$2/giờ` nên `LLMCostBurnRateHigh` **firing từ 17:28:12 mà không cần "phá" gì**. Ngưỡng $2/giờ được đặt cho tải thấp hơn; với lưu lượng này, cần nâng ngưỡng hoặc tính ngân sách theo ngày. Khi bật A và B, alert này về *inactive* vì 20% request lỗi và 30% request chậm làm số call thành công/giây giảm, tiền đốt giảm theo.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Số liệu (Prometheus, cửa sổ 1 phút, Locust 30 users, 2 instance). "Trước" đo lúc 17:57:45 với agent v2 và cấu hình mặc định. "Sau" đo lúc 19:49:29, 3 phút sau khi bật lại tải với agent v1. Lần đo "sau" đầu tiên bị bỏ: máy bị treo hoặc ngủ giữa chừng, Locust đã hết hạn nên không còn tải và mọi metric đều trống.

| Metric | Agent v2 (trước) | Agent v1 (sau) |
|---|---|---|
| Retrieval hit ratio | 1.0 | không còn series (v1 không gọi retrieval) |
| Tokens/s input / output | 1449.7 / 764.5 | 678.1 / 372.3 |
| Cost USD/giờ | 2.43 | 1.17 |
| req/s `/ask` | 18.67 | 18.75 |
| Error rate `/ask` | 0.88% | 0.78% |
| p95 `/ask` | 294 ms | 198 ms |
| Alert | không có | **không có** |
| Accuracy (eval, mục 3) | 91.3–100% | **4.3%** |

Mọi metric hạ tầng (traffic, error, latency, saturation) đều xanh, thậm chí **đẹp hơn**: rẻ hơn một nửa, nhanh hơn 100 ms. Lý do là v1 bỏ bước retrieval và prompt ngắn hơn. Nhưng agent trả lời sai gần hết. Agent trả lời sai vẫn trả **HTTP 200**, nên Golden Signals không có cách nào phát hiện. Cách phát hiện:

1. **Chặn trước khi deploy:** quality gate trong CI (mục 3). Đã thử `run_eval.py` với v1 và `--baseline` là v2: 20 câu regression, exit code 1, PR không merge được. Lỗi kiểu E không bao giờ lên được production nếu đi qua pipeline.
2. **Phát hiện trên production:**
   - Alert trên **metric "proxy" của chất lượng**: retrieval hit ratio giảm hoặc mất series (`absent(agent_retrieval_total)`); tokens/câu hoặc độ dài câu trả lời giảm đột ngột; `agent_info{version}` khác version đã duyệt.
   - **Eval định kỳ trên production**: chạy golden dataset mỗi N phút vào endpoint thật (synthetic), hoặc LLM-as-judge chấm một mẫu traffic thật, rồi đẩy accuracy thành metric để có alert.
   - **Tín hiệu từ người dùng**: tỉ lệ 👎, tỉ lệ hỏi lại, tỉ lệ chuyển sang người hỗ trợ.
3. **Triển khai canary:** đưa bản mới cho 5% traffic, so sánh các chỉ số chất lượng ở trên với bản cũ trước khi mở rộng.

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được (`GET /api/v3/traces` của Jaeger, 1000 trace trong 15 phút, sắp theo tổng thời gian):

- `trace_id`: `43d9ab6e3e2045f584f6c7aa7161192e`
- Tổng thời gian: **1718.8 ms** (Jaeger UI: Duration 1.7s, 4 spans, depth 3)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (**1689.3 ms, 98.3%**). Các span khác: `agent.retrieve_context` 26.4 ms (1.5%); `agent.run` 1715.9 ms là span cha bao cả hai.
- Kết luận: request chậm vì **LLM upstream** (rơi vào nhóm "slow" của mock), không phải do code agent hay retrieval. Muốn giảm tail latency phải xử lý ở tầng gọi LLM (timeout và retry, hedged request, cache, model nhanh hơn), không phải tối ưu code.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 43d9ab6e3e2045f584f6c7aa7161192e`):

```json
{"ts": "2026-11-25T10:38:55.123346+00:00", "level": "INFO", "service": "observable-agent", "instance": "114c7a4da80f", "event": "llm_call", "trace_id": "43d9ab6e3e2045f584f6c7aa7161192e", "span_id": "610146215b74affd", "request_id": "f66095edf280", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 76, "output_tokens": 39, "cost_usd": 3.5e-05, "llm_ms": 1690.5}
{"ts": "2026-11-25T10:38:55.123875+00:00", "level": "INFO", "service": "observable-agent", "instance": "114c7a4da80f", "event": "http_request", "trace_id": "43d9ab6e3e2045f584f6c7aa7161192e", "span_id": "610146215b74affd", "request_id": "f66095edf280", "method": "POST", "route": "/ask", "status": 200, "ms": 1718.1}
```

**Trace lỗi** (ảnh `jaeger-error-trace.png`): lấy `trace_id` từ log (`docker compose ... logs agent | grep llm_error | head -1`) rồi tra lại trong Jaeger:

```json
{"ts": "2026-11-25T10:34:57.036938+00:00", "level": "INFO", "service": "observable-agent", "instance": "500867eefe24", "event": "llm_error", "trace_id": "aed8463a4711155a037c6ae8c959cb9f", "span_id": "b8a6ff9d7cd9a33d", "request_id": "da8af046c6fe", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

Trace `aed8463a4711155a037c6ae8c959cb9f`: tổng 102.5 ms, `POST /ask` trả 503. Span `chat mock-llm-small` (77.1 ms) có status ERROR, attribute `error.type = upstream_unavailable` và event **`exception`** (`utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`). Lỗi đến nhanh hơn request thành công vì mock trả lỗi sau một nửa latency. Có `trace_id` chung giữa log và trace nên đi được từ log sang trace và ngược lại.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh: `docker compose -p k5-day14-observability up -d --scale agent=<n>`, chờ 25 s, rồi `docker compose -p k5-day14-observability run --no-deps --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Số liệu dòng `POST /ask` trong `benchmark-results/scale_<n>_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.68 | 190 | 220 | 1700 | 87 / 7147 (1.22%), toàn bộ 503 từ mock LLM |
| 3 | 60.05 | 190 | 220 | 1700 | 82 / 7139 (1.15%), toàn bộ 503 từ mock LLM |

Xác nhận số instance thật sự chạy (Prometheus `count(up{job="agent"}==1)` và `increase(http_requests_total[150s])` theo instance):
- Lượt 1: 1 container, 1 target up, toàn bộ request vào `172.20.0.4` (≈7407).
- Lượt 3: 3 container, 3 target up, chia ≈2424 / 2394 / 2437.

Số theo instance là giá trị xấp xỉ của `increase()`, hàm này có ngoại suy; số chính xác lấy theo CSV của Locust.

> **Lỗi đã gặp và sửa:** lệnh trong CHECKPOINTS (`docker compose run --rm locust ...`) **không có `--no-deps`**, nên `compose run` tự khởi động lại các dependency (locust → nginx → agent) theo `deploy.replicas: 2`. Lần chạy đầu, cả "1 instance" lẫn "3 instance" thực chất đều chạy **2 instance** (container `agent-3` được tạo đúng lúc bắt đầu lượt 1; `agent-4` của lượt 3 bị xoá). Kết quả lần đó đã bị bỏ; số trong bảng là lần chạy lại có `--no-deps` và có xác minh số instance.

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

- **Không cải thiện.** RPS, p50, p95, p99 và tỉ lệ lỗi gần như giống hệt nhau giữa 1 và 3 instance.
- **Tải do Locust tạo ra là cố định (closed-loop):** 100 users, mỗi user nghỉ `between(0.5, 2)` s (trung bình 1.25 s) giữa các request. Tải tối đa ≈ 100 / (1.25 + 0.23) ≈ **67 req/s**, khớp với tổng 65.9–66.3 req/s đo được (gồm cả `/health`). Thêm instance không làm Locust gửi nhiều hơn.
- **1 instance chưa bão hoà:** agent là async và mock LLM là `asyncio.sleep` (I/O-bound), nên 1 container (giới hạn 0.5 CPU) xử lý được ~60 req/s với hàng chục request đồng thời mà latency không tăng (p50 vẫn 190 ms, đúng bằng latency của LLM).
- **Nút thắt là LLM upstream:** latency mỗi request ≈ thời gian gọi LLM (~120–180 ms cơ bản; 3% request thêm 1.5 s nên p99 = 1700 ms). Đúng như trace ở 4.3 (`chat mock-llm-small` chiếm 98% thời gian) và panel *LLM latency p95 theo model*. Scale ngang agent không làm LLM trả lời nhanh hơn, nên không giảm được p95/p99.
- **Khi nào scale mới có ích:** khi agent bị nghẽn CPU hoặc bộ nhớ (tải lớn hơn nhiều, hoặc xử lý nặng trong agent), hoặc để **tăng độ sẵn sàng** (mất 1 instance thì vẫn còn instance khác phục vụ, như thí nghiệm Part 5). Muốn giảm tail latency thì phải xử lý ở tầng LLM.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Với 10% request chậm, avg chỉ tăng 187 → 351 ms nhưng p95 tăng 214 → 1694 ms. SLO và alert phải đặt theo p95/p99, và cần đủ mẫu: 300 request ở c=1 đã đủ để dao động ngẫu nhiên làm trượt SLO.
  2. **Metric hạ tầng không bắt được lỗi chất lượng.** Agent trả lời sai (v1) vẫn "khoẻ" trên mọi dashboard. Phải có eval trên golden dataset làm quality gate trong CI, cùng với chỉ số chất lượng chạy trên production. Ngược lại, eval không cho biết hệ thống chậm hay lỗi, nên cần cả hai.
  3. **Ba trụ cột bổ trợ nhau qua `trace_id`.** Metric báo *có vấn đề* (alert p95), trace chỉ ra *ở đâu* (span LLM chiếm 98%), log cho *chi tiết* (model, token, lỗi). Và phải kiểm chứng cả công cụ đo: lệnh benchmark mẫu đã đo sai số instance vì thiếu `--no-deps`, và stack mẫu crash vì thiếu `PYTHONUSERBASE`. Nếu không đọc kỹ số liệu (100% lỗi 502, RPS bằng nhau), đã có thể ghi kết luận sai vào báo cáo.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Chất lượng:** metric `agent_retrieval_total{result="miss"}` theo tỉ lệ kèm alert khi hit ratio giảm đột ngột (bắt được lỗi kiểu E); chạy eval định kỳ (canary/shadow traffic) và đẩy accuracy thành metric; thu thập feedback 👍/👎 của user.
  - **Phía người dùng:** alert từ **Nginx/load balancer** (5xx, 504, latency) và **synthetic check từ bên ngoài**. Lỗi 504 ở Nginx không hiện trên metric của agent, vì agent chưa bao giờ xử lý xong request đó.
  - **Cost:** alert theo **ngân sách ngày/tháng** (forecast "sẽ vượt budget lúc mấy giờ") thay vì chỉ theo burn rate tức thời; theo dõi token theo từng user/API key ở log (không dùng label vì cardinality).
  - **Error budget burn rate** nhiều cửa sổ (1h/6h, theo SRE workbook) thay cho ngưỡng cố định, để vừa nhanh vừa ít báo động giả. Thêm alert hết quota hoặc 429 từ LLM provider, và saturation (in-flight, CPU, memory) để biết khi nào thật sự cần scale.

---

## Phụ lục — Exercise 7.1: metrics của agent (develop)

Sau 2 request `/ask` và 2 request tới URL lạ (`/wp-admin`, `/.env`), `curl -s http://localhost:8002/metrics | grep -v "^#"` cho:

```
http_requests_total{method="POST",route="/ask",status="200"} 2.0
http_requests_total{method="GET",route="unmatched",status="404"} 2.0
agent_in_flight_requests 0.0
llm_request_duration_seconds_bucket{le="0.2",model="mock-llm-small"} 2.0
llm_request_duration_seconds_sum{model="mock-llm-small"} 0.305364324287825
llm_request_duration_seconds_count{model="mock-llm-small"} 2.0
llm_tokens_total{model="mock-llm-small",type="input"} 183.0
llm_cost_usd_total{model="mock-llm-small"} 8.805e-05
agent_info{model="mock-llm-small",version="v2"} 1.0
agent_question_length_chars_bucket{le="50.0"} 1.0
agent_question_length_chars_bucket{le="100.0"} 2.0
agent_question_length_chars_count 2.0
agent_question_length_chars_sum 120.0
```

1. **Mỗi loại metric một ví dụ:**
   - **Counter** (chỉ tăng): `http_requests_total`, `llm_tokens_total`, `llm_cost_usd_total`.
   - **Gauge** (lên xuống): `agent_in_flight_requests` (số request đang xử lý), `agent_info` (luôn = 1, mang thông tin version/model qua label).
   - **Histogram** (bucket, sum, count): `http_request_duration_seconds`, `llm_request_duration_seconds`. `histogram_quantile` dùng các bucket này để tính p95/p99.
2. **Vì sao label `route` dùng `route.path` chứ không dùng `request.url.path`?** Mỗi giá trị label khác nhau tạo một **time series riêng**. Nếu dùng URL thật, bot quét gọi hàng nghìn URL lạ (`/wp-admin`, `/.env`, `/a1b2c3`...) sẽ tạo hàng nghìn series mới. Cardinality bùng nổ làm Prometheus tốn RAM, query chậm, có thể sập. Dùng route template thì mọi URL không khớp route nào gộp về một series `route="unmatched"`: kết quả trên cho thấy `/wp-admin` và `/.env` cùng thành `route="unmatched",status="404"} 2.0`.
3. **Metric mới `agent_question_length_chars`** (đã thêm vào `develop/app.py`): Histogram với bucket `(20, 50, 100, 200, 500, 1000, 2000)`, gọi `QUESTION_LENGTH.observe(len(body.question))` trong `/ask`. Dùng Histogram thay vì label `length=...` vì độ dài có vô số giá trị, nếu làm label thì cardinality bùng nổ. 2 câu hỏi dài 120 ký tự tổng cộng, rơi vào bucket ≤50 và ≤100.
