# MediSearch HTML app và smoke test

App dùng HTML, CSS và JavaScript thuần, kết nối trực tiếp với engine hiện có qua
server Python dùng thư viện chuẩn. Không cần npm, framework hoặc bước build.

## Chạy app

Từ root repo:

```bash
python3 -m medical_agent.web --port 8001 --home outputs
```

Mở **http://127.0.0.1:8001**. Tham số `--port` có thể thay đổi khi cổng đang được dùng;
default của module là 8000. Server bind loopback và không publish lên Internet.
Ctrl+C trong terminal chạy lệnh để dừng. Không chạy hai server hoặc CLI nghiên cứu
đồng thời trên cùng thư mục `--home`.

Để triển khai sau Caddy với tên miền HTTPS và tài khoản dùng riêng, xem
[deployment.md](deployment.md). Chế độ này dùng `--public-origin` và `--auth-file`;
mọi màn hình/API cần đăng nhập. Local mặc định không yêu cầu tài khoản.

Ollama cần phục vụ `qwen3:1.7b` để tạo nghiên cứu. Thư viện và lịch sử vẫn xem được
khi model chưa sẵn sàng. Sau khi bật Ollama, tải lại trang để kiểm tra kết nối.
Workspace hiện có một bài PubMed; câu hỏi một bài phù hợp nhất cho smoke test đầu.
Nếu chọn một thư mục runtime mới, hãy nhập tài liệu ở mục Thư viện trước.

## Các màn hình và thao tác

| Màn hình | Thao tác đã nối backend |
|---|---|
| Tra cứu y văn | Nhập câu hỏi, chọn nguồn local/PubMed, giới hạn nguồn và thời gian; chọn câu gợi ý chỉ điền ô nhập |
| Tiến độ | Theo dõi plan, tìm/đọc nguồn và tổng hợp từ events thật; có nút dừng sau bước hiện tại |
| Kết quả | Xem nhận định, bảng nghiên cứu, phạm vi, nguồn và nhật ký; click evidence ID mở đúng đoạn nguồn |
| Làm rõ | Trả lời câu hỏi rồi tiếp tục cùng run ID và ngân sách |
| Thư viện | Tìm từ khóa, đọc nội dung đã lưu, mở nguồn gốc; nhập TXT/MD/JSON/JSONL tối đa 10 MB |
| Lịch sử | Mở tối đa 30 phiên gần nhất, lọc theo trạng thái, tiếp tục phiên chưa kết thúc |
| Review | Ghi người đọc, quyết định và ghi chú sau khi đối chiếu; tách trạng thái review với trạng thái run |
| Báo cáo | Tải Markdown dựng từ checkpoint, không chạy model lần nữa |

App cho chạy một tác vụ nghiên cứu tại một thời điểm. Khi chuyển trang trong app,
tác vụ vẫn chạy trên server. Tải lại trình duyệt sẽ kết nối lại tác vụ hiện hành.
Nếu server bị dừng, metadata tác vụ trong RAM mất nhưng checkpoint SQLite vẫn còn;
mở phiên từ lịch sử rồi chọn tiếp tục. Đây chưa phải hàng đợi tác vụ bền vững.

## Smoke test bằng trình duyệt

1. Mở app, chờ nhãn “Model cục bộ sẵn sàng”.
2. Giữ “Dữ liệu đã lưu”. Chọn gợi ý “Giấc ngủ & Alzheimer”.
3. Kiểm tra câu hỏi đã điền rồi bấm “Bắt đầu tra cứu”.
4. Quan sát nhật ký thay đổi trong lúc model xử lý. Có thể mở thư viện/lịch sử rồi quay lại.
5. Khi phiên kết thúc, kiểm tra bảng nguồn, trạng thái và phần còn thiếu. Với snapshot
   đúng câu hỏi một bài, đã quan sát completed; trạng thái này chưa xác nhận nội dung đúng.
6. Click evidence ID trong nhận định; tab nguồn phải mở đúng đoạn nguyên văn.
7. Mở tab Hoạt động và tải báo cáo. Không ghi review approved nếu chưa thực sự đọc nguồn.

Ca mở rộng: câu hỏi “gần đây” để kiểm tra form làm rõ; dừng trong lúc đang xử lý rồi
tiếp tục; tìm từ khóa không có trong thư viện; nhập JSON không hợp lệ. Không dùng
dữ liệu bệnh nhân thật cho smoke test.

### Kết quả đã ghi nhận ngày 28/10/2026

- Đã mở app trong Chrome và chạy câu hỏi từ form tới báo cáo bằng Ollama thật.
- Run `04464c2d4dac`: completed, 6 model calls, 2 tool calls, 1 nguồn dùng được,
  khoảng 149.8 giây xử lý chủ động; review pending, chưa được kiểm định nội dung.
- Thư viện hiển thị đúng PMID, ngày lấy nguồn và nội dung đã lưu; lịch sử có phiên mới.
- Click claim mở đúng trích đoạn nguồn, không render nội dung nguồn thành HTML.
- Đã xem giao diện tại breakpoint di động; chiều rộng nội dung không vượt viewport.
- 77 tests xác định đạt, trong đó 10 tests web chạy không cần socket, Ollama hoặc PubMed.
  Log: `outputs/validation/unittest-web.log`.

Smoke test này xác nhận tích hợp UI và backend, không thay benchmark chất lượng y khoa.
Thu thập PubMed live và upload qua trình duyệt chưa được kiểm tra trực tiếp trong lượt
này; nhập file được kiểm tra bằng API/fixtures, còn PubMed kế thừa source tools hiện có.

## Cấu trúc và API local

| File | Vai trò |
|---|---|
| `medical_agent/frontend/index.html` | Cấu trúc màn hình, form và dialog |
| `medical_agent/frontend/styles.css` | Giao diện y khoa, desktop/mobile, focus và reduced motion |
| `medical_agent/frontend/app.js` | Fetch API, điều hướng, polling, render nguồn bằng textContent |
| `medical_agent/web.py` | Server, một worker, lock mutation, upload và review |
| `tests/test_web.py` | Async run, cancel/resume, validation, HTTP, CSRF và download |

API gồm bootstrap/health, danh sách tài liệu, tạo run, đọc run/events, đọc job,
cancel, resume, review và tải report. Request ghi dữ liệu cần token phiên và đúng
origin/Host. Static files được phục vụ bằng allowlist; API không nhận lệnh shell,
đường dẫn file tùy ý hoặc endpoint model do trình duyệt cung cấp.

Nguồn và kết quả được dựng qua DOM text thay vì chèn HTML từ tài liệu. App không
có phân quyền nhiều người dùng; chế độ VPS dùng một tài khoản chung và vẫn bind loopback.
Các giới hạn chất lượng, dữ liệu và đồ án xem [implementation-status.md](implementation-status.md).
