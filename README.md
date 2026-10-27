# Dự án học xây agent y khoa

Người học đã có kinh nghiệm cơ bản về LLM và RAG; tập trung học agent từng bước,
hiểu cách gọi model, tools, state, điều kiện dừng và cách điều tra lỗi.

- Mục tiêu: học và chạy local.
- Model: ưu tiên local qua Ollama; API trả phí chỉ là thực nghiệm bổ sung với tổng ngân sách dưới 5 USD.
- Nguồn dữ liệu ban đầu: PubMed. VIBioMIR và các nguồn khác để mở rộng sau.

## Bắt đầu ở đây

Đọc [bài 1: gọi model API local](docs/3.md).

Chạy từ thư mục gốc repo, không cần cài thư viện Python ngoài:

```bash
python3 lessons/01_call_local_api.py --dry-run
```

Sau khi đã cài Ollama, tải model và khởi động server theo bài 1:

```bash
python3 lessons/01_call_local_api.py
```

Script chỉ gọi `http://127.0.0.1:11434/api/chat`. Nó không tải model,
không gọi PubMed và không gọi API model trả phí.

## Tài liệu

- [Kế hoạch học](docs/1.md).
- [Pipeline học bằng sơ đồ theo 15 phần PDF](docs/learning-pipeline.md).
- [Nhận xét brief ban đầu](docs/2.md).
- [Project brief hiện tại](docs/project-brief.md).
- [Workflow nhiều chủ đề](docs/workflow.md).
- [Ba tình huống đầu vào/đầu ra](evals/research_cases.json), mới là đặc tả, chưa có đáp án chuẩn hoặc kết quả chạy.
- [Bài thực hành 1](docs/3.md).
- [Bài thực hành 2: tìm PMID qua PubMed](docs/4.md).
- [Bài thực hành 3: model yêu cầu gọi tool](docs/5.md).
- [Bài thực hành 4: lấy metadata và abstract từ PMID](docs/6.md).
- [Bài thực hành 5: nối search và fetch bằng vòng lặp](docs/7.md).

Các ví dụ và bài tập chưa phải công cụ y khoa đã được thẩm định.
