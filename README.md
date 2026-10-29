# Medical Research Agent — local v2

Ứng dụng học xây agent: nhận mục tiêu → chọn hành động → đọc và đánh giá nguồn
→ tìm bổ sung khi thiếu bằng chứng → xuất báo cáo. Python + Ollama local,
không cần thư viện Python ngoài hoặc API trả phí.

## Mở app HTML

```bash
python3 -m medical_agent.web --port 8001 --home outputs
```

Mở **http://127.0.0.1:8001** để tra cứu, xem thư viện/lịch sử, theo dõi tiến độ,
trả lời câu hỏi làm rõ và tải báo cáo. HTML/CSS/JavaScript thuần, không cần npm hoặc
bước build. Ollama cần chạy để tạo nghiên cứu mới; xem thư viện và lịch sử không
cần gọi model. Xem [hướng dẫn frontend và smoke test](docs/frontend.md).

Để đưa lên VPS dùng riêng qua tên miền HTTPS, xem [hướng dẫn triển khai](docs/deployment.md).
Bộ cấu hình tại `deploy/` gồm đóng gói nguồn, tài khoản, systemd và Caddy; chế độ
local vẫn là mặc định. Chế độ tên miền cần `--public-origin` và `--auth-file`.

## Chạy CLI

Chạy từ thư mục gốc repo, sau khi Ollama đang phục vụ model `qwen3:1.7b`:

```bash
python3 -m medical_agent doctor
python3 -m medical_agent
```

Nhập câu hỏi tại `Mục tiêu>`. Agent có thể hỏi tại `Bổ sung>`;
`/runs` xem lịch sử, `/resume ID` chạy tiếp, `/bye` thoát.

Ví dụ mục tiêu nhỏ để quan sát một lượt chạy:

> Tóm tắt 1 bài báo về giấc ngủ và Alzheimer; chỉ cần nêu mối liên hệ được tác giả mô tả.

PubMed đôi lúc trả trang chặn NCBI. Agent ghi lỗi và dùng corpus local nếu có;
không coi lỗi là không có nghiên cứu. Để dùng dữ liệu đã lưu:

```bash
python3 -m medical_agent ingest /duong/dan/pubmed.json
python3 -m medical_agent --offline
```

Ingest nhận txt/md/json/jsonl, gồm JSON output của bài 4. Offline cần dữ liệu nạp
trước, giữ ngày lấy nguồn ban đầu. Workspace hiện tại đã nạp một snapshot PubMed.

## Thành phần đã có

| Thành phần | File chính | Vai trò |
|---|---|---|
| CLI hội thoại | `medical_agent/cli.py` | Nhập mục tiêu, hỏi lại, resume, review |
| App HTML local | `medical_agent/frontend/`, `medical_agent/web.py` | Tra cứu, tiến độ, thư viện, lịch sử, chứng cứ và tải báo cáo |
| Vòng lặp agent | `medical_agent/engine.py` | Plan → quyết định → tool → đánh giá → quyết định tiếp |
| Model local | `medical_agent/model.py` | Ollama, JSON schema, timeout, usage |
| Tools + ingestion | `medical_agent/sources.py` | PubMed, tìm local, đọc nguồn, chunk dữ liệu |
| State và memory | `medical_agent/store.py` | SQLite checkpoint, FTS5/BM25, cache, lịch sử |
| Chứng cứ và report | `medical_agent/report.py` | Đoạn nguyên văn, tham chiếu nguồn, Markdown |
| Hướng dẫn từng vai trò | `medical_agent/prompts.py` | Plan, chọn action, đánh giá, tổng hợp |
| Corpus có phiên bản | `medical_agent/corpus.py` | Thu thập snapshot thật, manifest và kiểm tra SHA-256 |
| Đánh giá A/B | `medical_agent/benchmark.py`, `metrics.py` | Baseline cố định, runner offline, metrics, mẫu review thủ công |
| Phục hồi và đóng gói | `medical_agent/operations.py`, `submission.py` | Backup SQLite nhất quán, restore, gói nguồn có manifest |

Model chọn action bằng JSON; Python kiểm tra quyền và thực thi. Có thể loại nguồn,
đổi truy vấn, hỏi lại hoặc dừng với kết quả một phần. Các bài `lessons/` được giữ
để học từng phần, gồm native tool calling.

## Xem và kiểm tra một run

```bash
python3 -m medical_agent runs
python3 -m medical_agent show RUN_ID
python3 -m medical_agent trace RUN_ID
python3 -m medical_agent eval RUN_ID
python3 -m medical_agent review RUN_ID approved
python3 -m medical_agent --resume RUN_ID --answer "Dùng khoảng năm 2020–2026"
python3 -m unittest discover -s tests -v
```

`eval` chỉ kiểm tra cấu trúc, tham chiếu và budget; không chấm tính đúng đắn y khoa.
Review là quyết định của người dùng local. Resume giữ budget, không tự cấp lại.
Kết quả: `outputs/runs/RUN_ID/` gồm `report.md`, `state.json`, `trace.jsonl`;
SQLite là checkpoint gốc. `outputs/` được bỏ qua bởi Git.
Mặc định: 10 bước, 16 model call, 8 tool call, 6 tài liệu/chunk, 600 giây chạy chủ động.

## Vận hành và đánh giá bản v2

Báo cáo bổ sung nhật ký truy vấn, bảng nghiên cứu từ ID đoạn nguyên văn và liên kết
claim–evidence. Nhiều chunk hoặc bản sao có DOI/PMID chung được gom theo tài liệu gốc.
Nguồn thiếu thông tin để trống có giải thích; kiểm tra cấu trúc không tự chấm đúng ngữ nghĩa.

```bash
python3 -m medical_agent --home outputs/demo backup outputs/backups/demo-v1
python3 -m medical_agent --home outputs/restored-demo restore outputs/backups/demo-v1
python3 -m medical_agent --home outputs/restored-demo rebuild RUN_ID
python3 evals/run_safety.py --output outputs/validation/safety-demo.json
python3 -m medical_agent.submission --output outputs/submission/source-demo.zip
```

Để so sánh fixed/agent, chuẩn bị corpus và manifest theo [runbook](docs/runbook.md):

```bash
python3 -m medical_agent.benchmark run \
  --corpus /duong/dan/pubmed.json --split dev \
  --output outputs/experiments/dev-v1
```

Runner tạo `results.jsonl`, `summary.csv`, `reviews.template.json` và các runtime
tách biệt. [30 câu hỏi](evals/questions.json) có split nháp 10 dev/20 test, chưa có
nhãn nguồn được rà độc lập. Metric nội dung giữ null đến khi chấm theo
[quy trình đánh giá](docs/evaluation.md). Benchmark chạy hết không có nghĩa mọi run thành công.

## Tài liệu

- [Trạng thái triển khai và kết quả thực tế](docs/implementation-status.md).
- [Hướng dẫn vận hành, backup và xử lý lỗi](docs/runbook.md).
- [Rubric và cách chạy đánh giá A/B](docs/evaluation.md).
- [Bản thảo báo cáo kỹ thuật đồ án](docs/thesis-report.md).
- [Kịch bản demo](docs/demo-script.md) và [dàn slide](docs/defense-outline.md).
- [Sơ đồ hệ thống thực tế và cách đọc code](docs/system.md).
- [Kế hoạch học](docs/1.md).
- [Pipeline học bằng sơ đồ theo 15 phần PDF](docs/learning-pipeline.md).
- [Nhận xét brief ban đầu](docs/2.md).
- [Project brief hiện tại](docs/project-brief.md).
- [Pipeline hoàn thiện hệ thống, vận hành và nộp đồ án](docs/workflow.md).
- [Ba tình huống đầu vào/đầu ra](evals/research_cases.json), là đặc tả yêu cầu, chưa có đáp án chuẩn y khoa.
- [Bài thực hành 1](docs/3.md).
- [Bài thực hành 2: tìm PMID qua PubMed](docs/4.md).
- [Bài thực hành 3: model yêu cầu gọi tool](docs/5.md).
- [Bài thực hành 4: lấy metadata và abstract từ PMID](docs/6.md).
- [Bài thực hành 5: nối search và fetch bằng vòng lặp](docs/7.md).

Đây là hệ thống local v2 cho vận hành thử, chưa được thẩm định y khoa.
Model nhỏ có thể đánh giá hoặc dịch sai. Kiểm tra ID và nguyên văn không chứng minh
kết luận đúng; PubMed hiện chỉ đọc metadata/abstract. Chưa có dense RAG/rerank,
MCP/A2A, multi-agent hoặc triển khai production.
