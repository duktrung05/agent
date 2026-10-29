# Project Brief — Biomedical Research Agent

**Tiến độ thực hiện mới:** đã phát triển bản local v2 theo [pipeline](workflow.md).
Đối chiếu phần đã đạt, kết quả chạy thật và phần còn thiếu tại
[implementation-status.md](implementation-status.md). Nội dung định hướng học bên
dưới được giữ; các mục tiêu chất lượng chưa tự trở thành kết quả đã kiểm chứng.

**Dự án:** Biomedical Research Agent — Agent tra cứu và tổng hợp thông tin y khoa có nguồn  
**Phiên bản:** Local agent v1 · **Ngày:** 27/10/2026 · **Trạng thái:** Đã tích hợp CLI, plan/decision loop, tools, đánh giá chứng cứ, SQLite/resume và report; đã chạy model local với snapshot PubMed. Tra cứu NCBI online còn gặp trang chặn.

**Điểm chạy:** `python3 -m medical_agent`. Xem [hệ thống thực tế](system.md).
Các yêu cầu tổng quan nhiều nghiên cứu bên dưới vẫn là mục tiêu để đánh giá chất lượng,
chưa phải khả năng đã được kiểm chứng y khoa.

## 0. Mục tiêu học và cách triển khai

- **Mục tiêu chính:** Học cách xây agent từng bước, hiểu lý do của mỗi thành phần và cách điều tra lỗi. Không đặt mục tiêu bán sản phẩm ở giai đoạn này.
- **Nền tảng người học:** Intern, dự án agent đầu tiên, đã có chút kinh nghiệm LLM và RAG. Hướng dẫn cần giải thích rõ cách gọi API và cách đọc request/response.
- **Người dùng đầu tiên:** Chính người xây dựng dự án. Các nhóm người dùng nghiên cứu bên dưới là bối cảnh thực hành.
- **Môi trường đã chốt:** Ứng dụng chạy trên máy cá nhân. Local không đồng nghĩa offline; việc truy cập nguồn dữ liệu online sẽ được xác định cho từng bài thực hành.
- **Nơi chạy model:** Ưu tiên model local qua Ollama. API trả phí là bài so sánh tùy chọn với tổng ngân sách dưới 5 USD; chưa bật hoặc chọn nhà cung cấp trả phí.
- **Nguồn dữ liệu đầu tiên:** PubMed theo lựa chọn của người học. VIBioMIR có thể bổ sung sau; hiện chưa xác định được file corpus có sẵn trên máy từ các đường dẫn cấu hình đã kiểm tra.
- **Phạm vi dữ liệu:** Có thể mở rộng ngoài PubMed. ICD-10 chỉ là một ví dụ về nguồn đối chiếu mã và phân loại bệnh, không phải bộ xác minh chung cho mọi kết luận y khoa.
- **Cách mở rộng:** Mỗi phiên bản chỉ thêm một năng lực hoặc một nguồn dữ liệu có mục đích rõ; bắt đầu bằng một tập dữ liệu nhỏ và một công việc cụ thể.
- **Kiến trúc dùng chung:** Một workflow phân tích câu hỏi, lập kế hoạch truy vấn, gọi tool và tổng hợp bằng chứng cho nhiều chủ đề. Không tạo agent hoặc nhánh Python riêng cho từng bệnh. Xem [workflow dự kiến](workflow.md).
- **Thành công về học tập:** Người học giải thích được input/context, lời gọi model, tool call, kết quả, điều kiện dừng; tái hiện và sửa được ít nhất một lỗi có chủ đích của mỗi bài thực hành.
- **Production:** Các chủ đề về triển khai và vận hành sẽ được học bằng bài thực hành nâng cao, không phải điều kiện để bắt đầu.

## 1. Bài toán và người dùng

**Bối cảnh người dùng mô phỏng:** Sinh viên y khoa, học viên cao học và người nghiên cứu y sinh cần tìm hiểu một câu hỏi từ tài liệu có nguồn. Mỗi bài thực hành sẽ chọn một vai trò cụ thể; **không hướng đến bệnh nhân tự điều trị**.

**Công việc cần hỗ trợ:** Người dùng tìm tài liệu, lọc nội dung phù hợp, đọc phần truy cập được, đối chiếu thông tin và viết tóm tắt kèm nguồn. Với bài báo, nguồn có thể là metadata/abstract; với tài liệu khác, cần xác định cấu trúc và phần thực sự đã đọc.

**Điểm nghẽn:** Tốn thời gian sàng lọc, dễ bỏ sót tài liệu, khó phân biệt kết luận có bằng chứng với suy đoán; các nghiên cứu đôi khi cho kết quả khác nhau.

**Hậu quả:** Bản tổng quan chậm hoàn thành hoặc chứa kết luận khó kiểm chứng.

**Kết quả mong muốn:** Một trợ lý có thể tìm trong dữ liệu được phép sử dụng, tổng hợp thông tin **có thể truy vết đến từng nguồn**, chỉ rõ thông tin mâu thuẫn hoặc còn thiếu. Đây là **dự án học xây công cụ hỗ trợ tra cứu và nghiên cứu**, không phải bác sĩ AI.

## 2. Tình huống sử dụng cụ thể

### Ví dụ 1 — Bệnh Alzheimer

**Input:**

> Các nghiên cứu từ 2020–2026 nói gì về mối liên hệ giữa chất lượng giấc ngủ và nguy cơ mắc Alzheimer?

**Output mong muốn:**

- Danh sách các nghiên cứu phù hợp.
- Bảng đối chiếu quần thể, phương pháp và kết quả.
- Tổng hợp những phát hiện nhất quán và mâu thuẫn.
- Phân biệt mối liên hệ quan sát với bằng chứng nhân quả.
- PMID/DOI và trích dẫn có thể kiểm chứng.

### Ví dụ 2 — Ung thư phổi

**Input:**

> So sánh bằng chứng về hiệu quả sàng lọc ung thư phổi bằng CT liều thấp ở nhóm có nguy cơ cao.

**Output mong muốn:**

- Các nghiên cứu và tổng quan hệ thống liên quan.
- So sánh kết cục được nghiên cứu.
- Tổng hợp lợi ích và hạn chế được tài liệu báo cáo.
- Những khác biệt giữa các nhóm đối tượng nghiên cứu.
- Kết luận có dẫn nguồn, không đưa chỉ định cho bệnh nhân cụ thể.

### Ví dụ 3 — Bệnh COPD

**Input:**

> Những nghiên cứu gần đây cho thấy chương trình phục hồi chức năng hô hấp ảnh hưởng như thế nào đến chất lượng cuộc sống của bệnh nhân COPD?

**Output mong muốn:**

- Các nghiên cứu liên quan đến phục hồi chức năng hô hấp.
- Các thước đo chất lượng cuộc sống được sử dụng.
- Bảng tổng hợp kết quả từng nghiên cứu.
- Đánh giá sự nhất quán của bằng chứng.
- Các hạn chế và trích dẫn.

Các đầu ra trên là **đặc tả kết quả agent cần tạo ra**, chưa phải kết luận đã được xác minh từ tài liệu thực tế. Bản máy đọc của ba tình huống nằm tại [research_cases.json](../evals/research_cases.json); chưa có đáp án y khoa chuẩn hoặc kết quả chạy eval.

### Hợp đồng đầu ra dùng chung

- **Đầu vào:** Câu hỏi nghiên cứu bằng tiếng Việt; có thể kèm thời gian, quần thể, can thiệp/phơi nhiễm, kết cục hoặc loại nghiên cứu. Chỉ sử dụng trường phù hợp với câu hỏi, không ép mọi câu hỏi vào cùng một mẫu cứng.
- **Hệ thống cần làm:** Hiểu câu hỏi → làm rõ phần mơ hồ có ảnh hưởng đến phạm vi → lập kế hoạch → tìm/lấy nguồn → phân tích bằng chứng → kiểm tra trích dẫn → tìm bổ sung có giới hạn hoặc xuất báo cáo.
- **Đầu ra:** Phạm vi đã dùng, thời điểm tìm kiếm, bảng nghiên cứu, phát hiện có nguồn, điểm nhất quán/mâu thuẫn, thông tin còn thiếu và giới hạn tra cứu.
- **Giới hạn nội dung:** Chỉ ghi quần thể, phương pháp, kết cục, thước đo và số liệu được phần tài liệu đã đọc hỗ trợ. Nếu abstract không có thông tin, đánh dấu chưa có trong nguồn đã đọc. DOI không có trong dữ liệu thì để thiếu; không tạo mã giả để hoàn thiện bảng.
- **Thời gian:** Lưu nguyên khoảng năm người dùng yêu cầu và thời điểm tra cứu thực tế. Không tuyên bố bao phủ toàn bộ năm 2026 khi năm chưa kết thúc. Với từ “gần đây”, hỏi rõ khoảng năm hoặc công bố một mặc định cấu hình trước khi tìm.
- **Nguồn:** PubMed và corpus txt/md/json/jsonl đã nạp, tìm bằng FTS5/BM25. Dense Medical RAG với embedding/reranker là nhánh chưa triển khai.
- **Thành công:** Người dùng mở được nguồn thật, kiểm tra được các phát hiện quan trọng và biết Agent chưa xác minh được điều gì.
- **Thất bại:** Bịa bài báo/PMID, dẫn một nguồn không hỗ trợ kết luận, khẳng định chắc chắn khi bằng chứng thiếu, hoặc tự đưa lời khuyên điều trị cá nhân hóa.

## 3. Phạm vi phiên bản học đầu tiên

**Định hướng:** Câu hỏi kiến thức/nghiên cứu y sinh **không gắn với bệnh nhân cụ thể**; bắt đầu với PubMed, tóm tắt có nguồn và thông báo khi dữ liệu không đủ. Ba tình huống Alzheimer, ung thư phổi và COPD dùng chung cơ chế lập truy vấn và tool calling. Không giới hạn workflow vào một bệnh cụ thể.

**Mở rộng sau:** Thêm nguồn tài liệu/dataset theo từng bài thực hành. Mỗi nguồn phải có vai trò rõ: tra cứu mã/phân loại, truy xuất kiến thức hay tìm bằng chứng nghiên cứu. Việc có nguồn trích dẫn không tự chứng minh một nhận định đúng; cần đối chiếu nội dung và có tiêu chí đánh giá phù hợp.

**Chưa bao gồm:** Chẩn đoán, kê đơn, thay đổi điều trị, đọc hồ sơ bệnh nhân thật, tự động cập nhật hệ thống bệnh viện, đánh giá đầy đủ toàn bộ y văn hoặc giả định đã đọc full-text khi chỉ có abstract. Kho tài liệu nội bộ chỉ bổ sung khi có quyền sử dụng hợp lệ.

## 4. Tiêu chí đánh giá khi mở rộng bài thực hành

Bắt đầu với **ba tình huống đã đặc tả** để kiểm tra một workflow trên nhiều chủ đề. Đây là các ca chức năng; cần bổ sung ca mơ hồ, không có kết quả, API lỗi, nguồn mâu thuẫn và yêu cầu ngoài phạm vi trước khi đánh giá rộng hơn. Sau đó mở rộng dần tới **30 câu hỏi thử nghiệm**. Các mức sau là **mục tiêu thực nghiệm**, không phải thành tích đã đo hay cam kết sử dụng thực tế. Tiêu chí về bài báo áp dụng cho bài thực hành với nguồn bài báo:

| Tiêu chí | Mục tiêu thực nghiệm mở rộng |
|---|---|
| Hoàn thành quy trình tìm kiếm → tổng hợp | ≥ 90% câu hỏi hợp lệ |
| Chọn đúng hành động/tool tại điểm quyết định | ≥ 90% trường hợp có nhãn kiểm thử |
| Nguồn được trích dẫn là có thật và đúng tài liệu đã truy xuất | 100% trích dẫn |
| Nhận định quan trọng được nguồn đính kèm hỗ trợ | ≥ 90% khi rà soát thủ công |
| Nhận diện yêu cầu vượt phạm vi và không tự đưa chỉ định điều trị | 100% ca kiểm thử an toàn |

**Lỗi cần ghi nhận và sửa trước khi coi bài thực hành là đạt:** Có trích dẫn bịa, mô tả dữ liệu không truy xuất được như sự thật, hoặc đưa ra chỉ định điều trị cho một bệnh nhân. Mỗi lỗi tái hiện được cần có trường hợp kiểm thử tương ứng.

## 5. Ràng buộc và giả định

- **Nguồn dữ liệu:** Chỉ tài liệu công khai/được phép dùng; không lưu thông tin nhận dạng bệnh nhân.
- **Minh bạch:** Ghi thời điểm tìm kiếm, giới hạn tra cứu, tài liệu đã sử dụng và lý do không thể kết luận; giữ log tool call phục vụ kiểm thử.
- **Giả định cần xác minh:** Với bài thực hành dùng abstract, bản tổng hợp dựa trên abstract có đáp ứng đầu ra đã định hay không; khi thiếu full-text, phải nêu rõ giới hạn.
- **Bước hiện tại:** Chạy [hệ thống local v1](system.md), đọc trace và đối chiếu report. Có corpus/memory bền vững, checkpoint/resume và giới hạn; chưa kiểm chứng đầy đủ ngữ nghĩa trích dẫn. Run mới không tự chèn toàn bộ hội thoại cũ vào context.
- **Đã chốt:** Ba ví dụ đầu vào/đầu ra và kiến trúc dùng chung nhiều chủ đề. [Workflow](workflow.md) giữ thiết kế mở rộng; [system.md](system.md) chỉ rõ phần đã triển khai.
- **Cần làm khi sang bài dữ liệu:** Lấy các bản ghi PubMed phù hợp, lưu nguồn và thời điểm truy xuất; xây tiêu chí đối chiếu nội dung và xác định người đánh giá diễn giải chuyên môn nếu cần.

**Tiêu chí hoàn thành brief:** Người học kể lại được một tình huống cụ thể, đầu vào/đầu ra và cách phân biệt thành công với thất bại. Đã chạy model local thật trên snapshot PubMed; truy xuất online ổn định và chất lượng tổng quan nhiều nghiên cứu vẫn cần đánh giá tiếp.
