# Trạng thái triển khai theo pipeline

**Ngày ghi nhận:** 28/10/2026. **Phiên bản:** local v2, vận hành thử bằng CLI và app HTML.
Đã triển khai các phần kỹ thuật chính và chạy kiểm tra trên máy hiện tại. Chưa đủ
bằng chứng để tuyên bố hoàn thành benchmark nội dung hoặc sẵn sàng nộp đồ án cuối.

**Cập nhật 29/10/2026 — chuẩn bị triển khai VPS:** Đã thêm HTTPS origin, tài khoản
dùng chung, systemd, Caddyfile, script cài và gói nguồn theo allowlist; xem
[deployment.md](deployment.md). 85/85 tests xác định đạt; kiểm tra HTTP loopback
thật bằng model fixture xác nhận đăng nhập, chặn sai Origin, nhập file và tải báo
cáo (`outputs/validation/deployment-smoke.json`). Chưa triển khai lên VPS đích,
chưa xác nhận DNS/chứng chỉ hay hiệu năng Ollama trên máy chủ.

## 1. Phần đã triển khai

| Mục trong pipeline | Kết quả hiện có | Chỗ kiểm tra |
|---|---|---|
| Frontend HTML | Giao diện y khoa, tra cứu thật, tiến độ, thư viện, lịch sử, làm rõ, dừng/resume và tải báo cáo | `medical_agent/frontend/`, `medical_agent/web.py`, [frontend.md](frontend.md) |
| Hiểu yêu cầu | Question spec giữ câu hỏi, trả lời làm rõ, phạm vi năm và thời điểm; không nhận quần thể do model tự thêm nếu không có trong yêu cầu | `medical_agent/engine.py` |
| Báo cáo | Nhật ký query, query translation, bảng nghiên cứu, liên kết claim–evidence–nguồn, trạng thái review riêng | `medical_agent/report.py` |
| Trích xuất | Model chọn span ID; code lấy nguyên văn cho từng trường; thiếu thì ghi chưa có | `medical_agent/prompts.py`, `report.py` |
| Citation | Kiểm tra evidence ID, PMID của nguồn thực sự đính kèm, từ chối URL/DOI model tự điền | `report.py:validate_claims` |
| Nguồn trùng | Gom tài liệu có chung PMID/DOI/document ID, không đếm chunk thành nghiên cứu riêng | `report.py:original_source` |
| Baseline | Chế độ fixed: một query từ plan, đọc top-k, cùng assessment/report/validators với agent | `engine.py`, `core.py`, CLI `--strategy fixed` |
| Corpus | Collect PubMed thật, manifest SHA-256, thống kê thiếu nội dung/năm; từ chối snapshot thay đổi | `medical_agent/corpus.py` |
| A/B và metrics | Runtime riêng theo variant/câu/lần lặp, results JSONL, summary CSV, usage, hash code/corpus/cases | `medical_agent/benchmark.py`, `metrics.py` |
| Review nội dung | Template claim/criteria, rubric, kiểm tra nguyên vẹn text; chưa chấm giữ null | `benchmark.py`, [evaluation.md](evaluation.md) |
| Độ bền | Backup SQLite API, restore có hash/integrity, không ghi đè runtime, rebuild không gọi model | `medical_agent/operations.py`, CLI |
| Bộ nộp | ZIP allowlist nguồn/tests/docs/evals, manifest hash từng file; loại runtime/secrets | `medical_agent/submission.py` |
| Tài liệu | Runbook, quy trình đánh giá, demo, dàn slide, bản thảo báo cáo | Các liên kết ở cuối trang |

## 2. Kiểm tra đã thực hiện

Môi trường kiểm thử: Python 3.14.6, SQLite 3.53.2 có FTS5; Ollama local có
`qwen3:1.7b`. Model/API không bị gọi trong tests xác định.

| Loại kiểm tra | Kết quả | Giới hạn |
|---|---|---|
| Bộ unittest mới nhất | 77/77 tests đạt, gồm 10 ca cho web và hồi quy trước đó; log `outputs/validation/unittest-web.log` | Chỉ fixtures/mock, không clinical eval |
| Catalog lỗi/phạm vi | 12/12 ca đạt, kết quả tại `outputs/validation/safety-v2.json` | Không thay cho red-team model thật |
| Ollama | `doctor` xác nhận model có trên máy | Không suy ra độ đúng của tổng hợp |
| PubMed live | Một ESearch trả dữ liệu, query translation và timestamp | Bắt đầu collect 60 bản ghi thì nhận trang chặn NCBI, đã dừng |
| Snapshot thật | Xuất bản sao một bài đang có, giữ ngày lấy 27/10/2026 và tạo manifest | Không phải corpus đủ cho ba chủ đề |
| A/B câu hỏi một bài | Fixed và agent đều `completed`, invariant cấu trúc đạt | Chưa có review nội dung độc lập |
| Ba câu brief | Sáu lượt A/B đã hoàn tất; kết quả partial/chờ người dùng | Dữ liệu chỉ có một bài Alzheimer |
| Backup/restore thật | Checkpoint và events của run khôi phục khớp nguồn | Một runtime mẫu, chưa kiểm tra mất điện/hỏng ổ đĩa |
| Frontend với model thật | Run `04464c2d4dac` đạt completed, 149.8 giây xử lý, 6 model calls, 1 nguồn; click claim mở đúng chứng cứ | Offline trên một bài; nội dung vẫn chờ review |

Log trước frontend: `outputs/validation/unittest-v2-final.log`; log hiện tại:
`outputs/validation/unittest-web.log`. Số tests mới nhất
phải lấy từ dòng tổng kết của log này, không gộp thêm 12 ca catalog vốn là các test
đã nằm trong bộ unittest.

## 3. Số liệu smoke test thật

Thí nghiệm `outputs/experiments/smoke-v2/`, một câu `alz_sleep_single`, một snapshot
có một tài liệu, mỗi phương án một lượt:

| Phương án | Trạng thái cấu trúc | Giây thực | Model calls | Tool calls | Input tokens | Output tokens |
|---|---|---|---|---|---|---|
| Fixed | completed | 50.120 | 3 | 2 | 1417 | 443 |
| Agent | completed | 95.188 | 6 | 2 | 3379 | 951 |

Đây là quan sát một ca, chưa kiểm soát đầy đủ trạng thái model nóng/lạnh và chưa
có đủ mẫu để kết luận về hiệu quả/latency. Trong bản tổng hợp agent, kiểm tra hỗ
trợ phát hiện dịch thuật ngữ sai so với đoạn nguồn. Run được ghi `review=rejected`,
`kind=assisted`, reviewer `assistant_text_audit`; không giả làm review chuyên gia.
Tỷ lệ claim support và hoàn thành nội dung vẫn null.

Thí nghiệm `outputs/experiments/brief-v2/`, giới hạn 180 giây, 10 model calls,
6 tool calls, 6 bước, 3 nguồn tối đa:

| Câu hỏi | Fixed | Agent |
|---|---|---|
| Alzheimer tổng quan 2020–2026 | partial, 74.815 giây | waiting_for_user, 91.583 giây |
| CT liều thấp sàng lọc ung thư phổi | partial, 14.237 giây | partial, 47.470 giây |
| COPD và chất lượng cuộc sống | waiting_for_user, 35.971 giây | waiting_for_user, 33.681 giây |

Cả sáu giữ invariant cấu trúc; không có ca được coi là hoàn thành nội dung.
Ca COPD cho thấy model dùng chuỗi xác nhận năm như câu hỏi làm rõ. Lỗi này đã có
sửa runtime/prompt và regression; lượt kiểm tra xác nhận được lưu riêng tại
`outputs/experiments/clarification-fix-v2/`, không sửa số liệu thí nghiệm trước.

Lượt xác nhận sau sửa: run `ec90a6726fd0`, 88.510 giây, 5 model calls, 1 lượt tìm,
giữ năm áp dụng 2020–2026 và không dừng chờ lời xác nhận thêm. Trạng thái `partial`
phản ánh thiếu nguồn phù hợp. Code hash ổn định trong lượt này; model digest và
các invariant được lưu trong experiment/results. Đây là xác nhận lỗi điều phối,
không phải kết quả đạt về tổng hợp COPD.

Run xác nhận bảng nghiên cứu và lý do dừng tại `outputs/experiments/final-smoke-v2/`
có trạng thái completed, 125.215 giây, 6 model calls, 2 tool calls. Có thay đổi mã
trên đĩa khi run này đang chạy, được đánh dấu trong experiment; không dùng run này
làm benchmark của một phiên bản code cuối đã đóng băng.

Tổng cộng đã thực hiện 10 lượt nghiên cứu với model thật trong bốn thư mục thí
nghiệm nêu trên. Các lượt thuộc những thời điểm sửa mã khác nhau, nên không gộp
chúng thành benchmark so sánh của một phiên bản duy nhất.

## 4. Phần còn thiếu trước nghiệm thu đồ án

- Corpus 60–100 bài phù hợp và có quyền dùng; hiện NCBI chặn lượt tải, chưa thu thập được bộ mới.
- Nhãn nguồn/đoạn chứng cứ và rà split cho 30 câu; hiện chỉ có yêu cầu và rubric nháp.
- Người chấm độc lập và phần kiểm tra thứ hai trên mẫu 20%; không tự sinh nhãn như đã được con người xác nhận.
- Chạy A/B toàn bộ tập test sau khi đóng băng dữ liệu/cấu hình; đo độ ổn định và phân tích lỗi.
- Sửa và đánh giá chất lượng dịch/diễn giải; citation hợp lệ vẫn có thể đi kèm nhận định sai.
- Thử trên máy bảo vệ; bổ sung thông tin trường, tác giả, giảng viên và mẫu báo cáo của khoa.
- Làm slide PowerPoint và ghi video demo từ phiên chạy thật. Hiện đã có dàn ý/kịch bản, chưa có các tệp này.

Đã có CLI và app HTML local. Dense RAG/rerank, multi-agent, MCP/A2A, nhiều worker
và triển khai cloud chưa nằm trong phần đã triển khai.

## 5. Tài liệu và lệnh tiếp tục

- [Pipeline và các mốc nghiệm thu](workflow.md).
- [Hướng dẫn vận hành](runbook.md).
- [Đánh giá A/B và rubric](evaluation.md).
- [Bản thảo báo cáo kỹ thuật](thesis-report.md).
- [Kịch bản demo](demo-script.md) và [dàn slide bảo vệ](defense-outline.md).

Để chạy corpus sẵn có trong workspace này, chưa phải gói dữ liệu phân phối:

```bash
python3 -m medical_agent --home outputs/demo-v2 ingest outputs/corpus/pubmed-existing-v1.json
python3 -m medical_agent --home outputs/demo-v2 --offline
```

Không đánh dấu toàn bộ checklist đồ án hoàn thành từ các smoke test này. Các phần
kỹ thuật đã được triển khai; nhãn độc lập, độ phủ dữ liệu và yêu cầu nghiệm thu của
trường là những điều kiện còn phải thực hiện với bằng chứng thực tế.
