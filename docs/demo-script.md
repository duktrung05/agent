# Kịch bản demo Biomedical Research Agent

Đây là kịch bản cho bản CLI local. Chuẩn bị snapshot hợp lệ trước buổi bảo vệ,
Ollama đang phục vụ model và một thư mục runtime dành riêng cho demo. Không phụ
thuộc NCBI trực tiếp. Nêu rõ ngày lấy dữ liệu và phần đã đọc là abstract.

## 1. Mở đầu trong một phút

“Hệ thống nhận câu hỏi nghiên cứu bằng tiếng Việt, tìm và đọc tài liệu, chọn đoạn
chứng cứ rồi xuất báo cáo có thể đối chiếu. Python kiểm tra mọi hành động và giới
hạn tài nguyên. Phần tổng hợp vẫn cần người đọc review.”

Mở sơ đồ trong [system.md](system.md). Chỉ ra engine, Ollama, tools, SQLite và report.
Không trình bày dense RAG, nhiều agent hoặc web UI như tính năng đang hoạt động.

## 2. Chạy một yêu cầu ngắn trong ba phút

```bash
python3 -m medical_agent --home outputs/demo doctor
python3 -m medical_agent --home outputs/demo ingest /duong/dan/pubmed_snapshot.json
python3 -m medical_agent --home outputs/demo --offline \
  --question "Tóm tắt 1 bài báo về giấc ngủ và Alzheimer; chỉ cần nêu mối liên hệ được tác giả mô tả."
```

Theo dõi plan, action tìm nguồn, đọc nguồn, assessment và trạng thái cuối.
Mở report: giải thích cột thiếu thông tin, nguồn của một ô bảng, liên kết claim–evidence,
timestamp và giới hạn. Không đọc bản dịch thành kết luận y khoa đã được xác nhận.

## 3. Hành vi agent và hỏi lại trong ba phút

Mở trace đã chuẩn bị có ca loại tài liệu rồi đổi query. Nếu dùng fixture hoặc
phát lại, nói rõ đó là kiểm thử có kiểm soát/phát lại, không gọi là online mới.

Với corpus COPD đủ nguồn, dùng câu “nghiên cứu gần đây” trong brief, cho thấy
`waiting_for_user`, bổ sung khoảng năm rồi resume cùng ID. Nếu corpus không có
COPD, chỉ demo hỏi lại và thiếu dữ liệu, không giả lập report đầy đủ.

## 4. Phục hồi và kết quả thực nghiệm trong ba phút

```bash
python3 -m medical_agent --home outputs/demo --resume RUN_ID
python3 -m medical_agent --home outputs/demo eval RUN_ID
python3 -m medical_agent --home outputs/demo rebuild RUN_ID
```

Dùng một run thật đã ngắt hoặc fixture ngắt tool để cho thấy ngân sách được giữ.
Mở bảng A/B, công bố số câu, số lần lặp và lượng dữ liệu. Nêu cả một lỗi có nguồn
đúng nhưng diễn giải sai, và quyết định review xử lý lỗi đó.

## 5. Kết thúc và dự phòng

Đối chiếu mục tiêu với trạng thái đã đạt, các nhãn còn thiếu và hạn chế model nhỏ.
Nếu model chậm, chạy trực tiếp một lượt ngắn rồi phát video cho luồng dài; phân
biệt phần trực tiếp và phần phát lại. Tệp video cần người thực hiện ghi từ buổi
demo thật, không dùng ảnh giả thay cho chứng cứ chạy.

Đồ án cần hoàn thiện đánh giá nội dung và quy định khoa trước khi dùng bộ tài
liệu này như hồ sơ nộp cuối. Gợi ý slide nằm trong [defense-outline.md](defense-outline.md).
