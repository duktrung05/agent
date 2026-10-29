# Hướng dẫn vận hành Biomedical Research Agent

Ứng dụng hỗ trợ tra cứu tài liệu y sinh bằng CLI hoặc app HTML trên một máy cá nhân. Model chạy
qua Ollama loopback; PubMed chỉ dùng khi online. Mỗi thư mục runtime dùng một tiến
trình agent tại một thời điểm. Báo cáo cần người đọc kiểm tra nội dung.

Để mở giao diện: `python3 -m medical_agent.web --port 8001 --home outputs`, rồi vào
http://127.0.0.1:8001. Các màn hình và smoke test được mô tả ở [frontend.md](frontend.md).
Không chạy thêm CLI nghiên cứu cùng thư mục runtime khi app đang xử lý một phiên.

## 1. Chuẩn bị môi trường

Không có thư viện Python bên thứ ba hoặc bước build. Cần Python có SQLite FTS5,
Ollama đã cài và model `qwen3:1.7b` đã tải. Mở Ollama theo cách cài đặt trên máy;
chỉ chạy `ollama serve` nếu chưa có dịch vụ phục vụ cổng mặc định.

```bash
python3 --version
ollama --version
ollama list
python3 -m medical_agent doctor
python3 -m unittest discover -s tests -v
```

Nếu chưa có model, chạy `ollama pull qwen3:1.7b` rồi kiểm tra lại. Lệnh này tải
model từ Internet; chế độ offline của agent không tự cài hoặc tải model.
`doctor` đọc danh sách model và số nguồn đã lập chỉ mục, không xác minh PubMed live.

## 2. Chuẩn bị và đóng băng corpus

Đường dẫn bên dưới là ví dụ; chọn tên snapshot mới cho mỗi phiên bản. Nguồn có thể
trả metadata nhưng thiếu abstract; manifest ghi rõ số tài liệu thiếu nội dung/năm.

```bash
python3 -m medical_agent --home outputs/collection-demo collect \
  --query "Alzheimer sleep" \
  --query "lung cancer low dose CT screening high risk" \
  --query "COPD pulmonary rehabilitation quality of life" \
  --limit 20 --start-year 2020 --end-year 2026 \
  --output outputs/corpus/pubmed-demo.json
```

`collect` giữ truy vấn, query translation, ngày lấy, lỗi và bản ghi thực tế. Trang
chặn NCBI làm dừng thu thập; không coi là corpus rỗng hợp lệ. File đã xuất không
được ghi đè. Chưa xác định quyền tái phân phối abstract thì chưa đưa corpus vào gói chia sẻ.

Nếu đã có file từ nguồn hợp lệ:

```bash
python3 -m medical_agent manifest /duong/dan/pubmed.json
python3 -m medical_agent --home outputs/demo ingest /duong/dan/pubmed.json
```

`manifest` tạo file `.manifest.json` bên cạnh corpus bằng SHA-256, thông tin nguồn
và thống kê nhập. Đổi nội dung corpus làm kiểm tra hash thất bại. Có manifest
không xác nhận tính đúng đắn khoa học hoặc quyền sử dụng dữ liệu.

## 3. Chạy nghiên cứu và kiểm tra kết quả

```bash
python3 -m medical_agent --home outputs/demo --offline
python3 -m medical_agent --home outputs/demo --offline \
  --question "Tóm tắt 1 bài báo về giấc ngủ và Alzheimer; chỉ cần nêu mối liên hệ được tác giả mô tả."
python3 -m medical_agent --home outputs/demo runs
```

Thay `RUN_ID` bằng ID trong kết quả. Cùng một ID được dùng cho mọi thao tác dưới đây:

```bash
python3 -m medical_agent --home outputs/demo show RUN_ID
python3 -m medical_agent --home outputs/demo trace RUN_ID
python3 -m medical_agent --home outputs/demo eval RUN_ID
python3 -m medical_agent --home outputs/demo --resume RUN_ID --answer "2020–2026"
python3 -m medical_agent --home outputs/demo review RUN_ID rejected \
  --reviewer "nguoi_doc" --note "Nhận định cần sửa sau khi đối chiếu đoạn nguồn."
```

Report nằm ở `outputs/demo/runs/RUN_ID/report.md`. Các cột nghiên cứu là đoạn nguồn
nguyên văn theo span ID. Nếu model bỏ trống cột kết quả, code dùng các đoạn chứng
cứ đã được chọn; các trường khác thiếu thì ghi rõ chưa có trong phần đã đọc.
Đọc cả phần tổng hợp và đoạn nguồn, đặc biệt thuật ngữ dịch, mức độ chắc chắn,
đơn vị, quần thể và chiều liên hệ. `completed` chỉ là trạng thái cấu trúc.

Review bằng công cụ hỗ trợ dùng `--kind assisted --reviewer TEN_CONG_CU`, tránh
gắn nhãn như người đã kiểm tra. CLI lưu nhãn reviewer nhưng không xác thực danh tính.
Review của CLI tách khỏi bảng chấm benchmark; không tự tạo nhãn chuyên gia.

## 4. Ngắt và phục hồi

Ctrl+C lưu checkpoint. Với run `interrupted`, dùng:

```bash
python3 -m medical_agent --home outputs/demo --resume RUN_ID
python3 -m medical_agent --home outputs/demo rebuild RUN_ID
```

`rebuild` xuất lại report/state/trace từ SQLite, không gọi model và không tăng
ngân sách. Run `partial/completed/failed` đã kết thúc không được tự cấp thêm budget
bằng resume. Khi muốn nghiên cứu rộng hơn, tạo run mới và công bố cấu hình mới.

```bash
python3 -m medical_agent --home outputs/demo backup outputs/backups/demo-v1
python3 -m medical_agent --home outputs/restored-demo restore outputs/backups/demo-v1
python3 -m medical_agent --home outputs/restored-demo runs
python3 -m medical_agent --home outputs/restored-demo eval RUN_ID
```

Backup dùng SQLite backup API, giữ một snapshot nhất quán rồi dựng lại exports.
Restore kiểm tra hash và integrity, chỉ ghi vào thư mục chưa tồn tại. Không ghi đè
runtime đang dùng. DB có thể chứa truy vấn người dùng, nên chỉ chia sẻ khi đã rà dữ liệu.

## 5. Xử lý sự cố

| Hiện tượng | Cách kiểm tra và xử lý |
|---|---|
| Không gọi được Ollama | Kiểm tra dịch vụ, loopback, tên model qua `doctor/ollama list` |
| Corpus trống | Ingest file thật; xem số tài liệu có nội dung trong manifest |
| PubMed trả trang chặn | Dừng online; dùng snapshot và công bố ngày dữ liệu |
| `waiting_for_user` | Đọc câu hỏi rồi resume kèm `--answer`; chưa phải lỗi runtime |
| `partial` | Xem tiêu chí thiếu, nguồn bị loại và budget; không tự đổi nhãn thành hoàn thành |
| Context quá lớn | Giảm packet/chia nội dung có kiểm thử; không âm thầm cắt chứng cứ |
| Report không còn | `rebuild RUN_ID`; DB là nguồn checkpoint gốc |
| Corpus khác manifest | Tạo snapshot và manifest phiên bản mới |
| Restore báo hash sai | Giữ bản bị lỗi để điều tra; dùng backup hợp lệ khác |

Mã thoát cho lượt nghiên cứu: 0 = `completed`, 2 = chưa hoàn tất/ngoài phạm vi,
1 = lỗi CLI, 130 = ngắt. Lệnh quản lý thành công trả 0. Benchmark trả 0 khi runner
chạy hết danh sách, kể cả khi có run `partial/failed`; xem `results.jsonl` để đánh giá.

## 6. Đóng gói và demo

```bash
python3 evals/run_safety.py --output outputs/validation/safety-demo.json
python3 -m medical_agent.submission --output outputs/submission/source-demo.zip
```

Gói ZIP chỉ có mã nguồn, tests, lessons, docs và evals theo allowlist cùng manifest
hash từng file. Không đưa `outputs/`, DB, `.git`, `.env` hoặc cache vào gói nguồn.
Report mẫu và video cần được rà rồi đính kèm riêng khi nộp.
Kịch bản và lời dẫn demo nằm trong [demo-script.md](demo-script.md).
